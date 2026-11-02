"""The setup tool's plan, configure, check, and deploy work offline against fakes."""

import ast
import configparser
import contextlib
import importlib.util
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from wtf_adsb_publisher import explain, site_identity, site_origin

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "setup.py"
INTERVIEW = ROOT / "docs" / "setup-interview.md"
SPEC = importlib.util.spec_from_file_location("wtf_publisher_setup_tool", SCRIPT)
setup_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_tool)
RUNNER_SPEC = importlib.util.spec_from_file_location(
    "wtf_publisher_job_runner", ROOT / "tools/run_container_job.py"
)
job_runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(job_runner)
IMAGE = "sha256:" + "ab" * 32
FAKE_DOCKER = """#!/bin/sh
case "$1" in
  ps) exit 0 ;;
  compose) echo 'Docker Compose version v0.0.0-test' ;;
esac
"""


def run(*args, env=None):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    return completed.returncode, json.loads(completed.stdout)


def document_rows():
    """Yield (group, key, type, default) from the interview document's tables."""
    group = None
    for line in INTERVIEW.read_text().splitlines():
        heading = re.match(r"## (.+)", line)
        if heading:
            group = heading[1]
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 5 and re.fullmatch(r"`[^`]+`", cells[0]):
            default = cells[2]
            if default.startswith("none"):
                value = None
            else:
                text = default.strip("`")
                try:
                    value = json.loads(text)
                except ValueError:
                    value = text
            yield group, cells[0].strip("`"), cells[1], value


class Ghost(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/ghost/api/admin/site/":
            self.send_error(404)
            return
        body = json.dumps({"site": {"title": "Test", "version": "6.0"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class StaticTests(unittest.TestCase):
    """Tests that write nothing, so they also run as root."""

    def test_tool_parses_on_the_oldest_supported_host_python(self):
        ast.parse(SCRIPT.read_text(), feature_version=(3, 11))

    def test_plan_lists_every_key_in_the_interview_document(self):
        code, result = run("plan")
        self.assertEqual(code, 0)
        planned = [
            (group["name"], question["key"], question["type"], question["default"])
            for group in result["groups"]
            for question in group["questions"]
        ]
        self.assertEqual(planned, list(document_rows()))
        self.assertTrue(all(q["validation"] and q["why"] for g in result["groups"] for q in g["questions"]))

    def test_plan_and_interview_list_the_same_configuration_files(self):
        files = "answers file, publisher.json, ai.env, compose.env, jobs.json, setup.json, ssh_config"
        self.assertIn(files, setup_tool.plan()["configuration_directory"])
        self.assertIn(files, " ".join(INTERVIEW.read_text().replace("`", "").split()))

    def test_model_choices_are_the_priced_models(self):
        self.assertEqual(set(setup_tool.MODELS), set(explain.PRICES))
        self.assertEqual(setup_tool.BY_KEY["ai.public_model"].default, explain.REQUESTED_PUBLIC_MODEL)

    def test_units_render_from_the_templates_with_real_paths(self):
        output = Path("/srv/wtf-adsb-publisher/config")
        values = {
            "schedule.publish": "*-*-* *:05,35:00",
            "schedule.write": "hourly",
            "schedule.weekly": "daily",
        }
        places = ({"config": output, "control": Path("/srv/wtf-adsb-publisher/control")}, {})
        units = setup_tool.render_units(values, places)
        self.assertEqual(sorted(units), sorted([*setup_tool.SERVICES, *setup_tool.TIMERS]))
        for name, text in units.items():
            parsed = configparser.ConfigParser(strict=False, interpolation=None)
            parsed.read_string(text)
            self.assertNotIn("%h", text, name)
            if name in setup_tool.SERVICES:
                self.assertIn(f"--config {output}/jobs.json ", parsed["Service"]["ExecStart"])
                self.assertEqual(parsed["Service"]["WorkingDirectory"], str(ROOT))
            if name in setup_tool.SERVICES[:3]:
                self.assertEqual(
                    parsed["Unit"]["ConditionPathExists"], "!/srv/wtf-adsb-publisher/control/activation.hold"
                )
        self.assertIn("OnCalendar=*-*-* *:05,35:00\n", units["wtf-adsb-autopilot.timer"])
        self.assertIn("OnCalendar=hourly\n", units["wtf-adsb-writer.timer"])

    def test_usage_errors_print_one_json_object_and_exit_2(self):
        for args in (("deploy",), ("configure", "--output", "/srv/x/y"), ("check", "--output")):
            with self.subTest(args=args):
                code, result = run(*args)
                self.assertEqual(code, 2)
                self.assertFalse(result["ok"])
                self.assertTrue(result["error"].startswith("usage: "), result)

    def test_ghost_probe_reports_a_malformed_http_response(self):
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)

        def answer():
            connection, _ = listener.accept()
            with connection:
                connection.recv(65536)
                connection.sendall(b"not an HTTP status line\r\n\r\n")

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        port = listener.getsockname()[1]
        with mock.patch.object(setup_tool, "admin_site_url", lambda origin: f"http://127.0.0.1:{port}/"):
            result = setup_tool.probe_ghost("https://ghost.example.net")
        thread.join(5)
        self.assertEqual(result, {"ok": False, "error": "BadStatusLine"})

    def test_site_rules_match_the_publisher_at_each_boundary(self):
        """Each value the tool accepts, the publisher accepts, and each it refuses, the publisher refuses."""
        label = "a" * 63

        def text(limit):
            return (
                "x" * limit,
                "x" * (limit + 1),
                "",
                " Fixture",
                "Fixture ",
                "Fix\u00a0",
                "Two\nlines",
                "Tab\there",
                "C1\x85",
                "Line\u2028separator",
                "\ufeffFixture",
                "Caf\u00e9 & <Skies>",
            )

        cases = {
            "site_name": (lambda v: site_identity.validate_text(v, "site_name", 60), text(60)),
            "site_tagline": (lambda v: site_identity.validate_text(v, "site_tagline", 120), text(120)),
            "site_description": (
                lambda v: site_identity.validate_text(v, "site_description", 160),
                text(160),
            ),
            "support_link.label": (lambda v: site_identity.validate_text(v, "label", 40), text(40)),
            "author_name": (site_identity.validate_author, (*text(60), "---", "Jos\u00e9 9")),
            "support_link.url": (
                lambda v: site_identity.validate_url(v, "support_link.url"),
                (
                    "https://example.com",
                    "https://support.example/fixture?ref=site#top",
                    "https://support.example/" + "x" * 1976,
                    "https://support.example/" + "x" * 1977,
                    "https://Support.example",
                    "https://support.example:8443/",
                    "https://user@support.example",
                    "https://192.0.2.1/",
                    "https://example.com-/",
                    "https://-example.com",
                    "http://support.example",
                    'https://support.example/"x"',
                    "https://support.example/a b",
                ),
            ),
            "site_origin": (
                site_origin.validate,
                (
                    "https://example.com",
                    "https://x--y.example",
                    f"https://{label}.example",
                    f"https://a{label}.example",
                    f"https://example.{label}",
                    "https://example.com-",
                    "https://example-.com",
                    "https://-example.com",
                    "https://192.0.2.1",
                    "https://0x7f000001",
                    "https://Example.com",
                    "https://example.com/",
                    "https://example.com:443",
                    "http://example.com",
                    "https://" + ".".join([label] * 3) + ".example",
                    "https://" + ".".join([label] * 4),
                ),
            ),
        }
        for key, (publisher, values) in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    try:
                        publisher(value)
                        expected = True
                    except ValueError:
                        expected = False
                    try:
                        setup_tool.check_value(setup_tool.BY_KEY[key], value)
                        accepted = True
                    except setup_tool.SetupError:
                        accepted = False
                    self.assertEqual(accepted, expected)
        self.assertEqual(setup_tool.ABOUT_LIMIT, site_identity.ABOUT_LIMIT)

    def test_agents_and_claude_instructions_are_identical(self):
        self.assertEqual((ROOT / "AGENTS.md").read_bytes(), (ROOT / "CLAUDE.md").read_bytes())


# The answers use the host's uid and gid, and uid must be at least 1.
@unittest.skipIf(os.geteuid() == 0 or os.getuid() == 0, "configure and check expect an unprivileged account")
class SetupToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wtf-publisher-setup-")
        self.base = Path(self.temporary.name)
        self.output = self.base / "config"
        self.feed = self.base / "feed"
        self.feed.mkdir(mode=0o700)
        (self.feed / "manifest.json").write_text('{"schema_version": 1}\n')
        self.secrets = self.base / "secrets"
        self.secrets.mkdir(mode=0o700)
        for name in ("ghost-admin-key", "openai-api-key", "id_ed25519"):
            self.secret(name, "not a real key\n")
        (self.secrets / "known_hosts").write_text("site ssh-ed25519 AAAA\n")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        for name, text in (
            ("docker", FAKE_DOCKER),
            ("ssh", "#!/bin/sh\nexit 0\n"),
            ("systemctl", "#!/bin/sh\n"),
        ):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o700)
        self.docker = str(self.bin / "docker")

    def tearDown(self):
        self.temporary.cleanup()

    def secret(self, name, content):
        path = self.secrets / name
        path.write_text(content)
        path.chmod(0o600)
        return str(path)

    def answers(self, **extra):
        values = {
            "feed_dir": str(self.feed),
            "state_dir": str(self.base / "state"),
            "artifacts_dir": str(self.base / "artifacts"),
            "control_dir": str(self.base / "control"),
            "live_since": "2026-11-01T00:00:00Z",
            "ghost_origin": "https://ghost.example.net",
            "ghost_key_file": str(self.secrets / "ghost-admin-key"),
            "deploy_target": "ghost",
            "image": IMAGE,
            "uid": os.getuid(),
            "gid": os.getgid(),
            **extra,
        }
        path = self.base / "answers.json"
        path.write_text(json.dumps(values))
        return str(path)

    def ssh_answers(self):
        return {
            "deploy_target": "ghost-ssh",
            "ssh.host": "site",
            "ssh.hostname": "site.example.net",
            "ssh.user": "deploy",
            "ssh.identity_file": str(self.secrets / "id_ed25519"),
            "ssh.known_hosts_file": str(self.secrets / "known_hosts"),
        }

    def configure(self, **extra):
        return run("configure", "--answers", self.answers(**extra), "--output", str(self.output))

    @contextlib.contextmanager
    def fake_ghost(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Ghost)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with mock.patch.object(
                setup_tool, "admin_site_url", lambda origin: f"http://127.0.0.1:{port}/ghost/api/admin/site/"
            ):
                yield
        finally:
            server.shutdown()
            server.server_close()

    def test_configure_writes_files_the_publisher_and_runner_accept(self):
        terms = self.secret("private-terms.txt", "operator-name|private-host\n")
        references = self.base / "references"
        references.mkdir(mode=0o700)
        about = self.base / "about.md"
        about.write_text("## Who runs this\n\nA test receiver.\n")
        code, result = self.configure(
            **self.ssh_answers(),
            private_terms_file=terms,
            private_terms=["Extra Term"],
            reference_dir=str(references),
            about_file=str(about),
            site_name="Test Findings",
            site_description="Test readings from one receiver",
            author_name="Test Author",
            contact_email="contact@example.com",
            **{"support_link.url": "https://example.org/support", "support_link.label": "Support"},
        )
        self.assertEqual(code, 0, result)
        config = json.loads((self.output / "publisher.json").read_text())
        self.assertEqual(
            {
                key: config[key]
                for key in ("feed_directory", "state_dir", "ghost_key_file", "private_terms_file")
            },
            {
                "feed_directory": "/feed",
                "state_dir": "/state",
                "ghost_key_file": "/run/secrets/ghost-admin-key",
                "private_terms_file": "/config/private-terms.txt",
            },
        )
        self.assertEqual(config["preview_ssh_host"], "site")
        self.assertEqual(config["reference_dir"], "/references")
        self.assertEqual(config["support_link"], {"url": "https://example.org/support", "label": "Support"})
        self.assertEqual(config["site_name"], "Test Findings")
        self.assertEqual(config["site_description"], "Test readings from one receiver")
        self.assertEqual(config["author_name"], "Test Author")
        self.assertEqual(config["about_file"], "/config/about.md")
        # The publisher's own loader accepts the container file, apart from the files it cannot see here.
        loadable = {
            key: value for key, value in config.items() if key not in {"private_terms_file", "about_file"}
        }
        container = self.base / "container.json"
        container.write_text(json.dumps(loadable))
        loaded = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from wtf_adsb_publisher import public_events; public_events.load_config(sys.argv[1])",
                str(container),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(loaded.returncode, 0, loaded.stderr)
        env = (self.output / "compose.env").read_text()
        for line in (
            f"PUBLISHER_CONFIG_DIR={self.output}",
            f"ENGINE_FEED_DIR={self.feed}",
            f"PUBLISHER_REFERENCE_DIR={references}",
            f"PUBLISHER_SSH_CONFIG={self.output / 'ssh_config'}",
            f"PUBLISHER_PRIVATE_TERMS_FILE={terms}",
            f"PUBLISHER_ABOUT_FILE={about}",
            f"PUBLISHER_OPENAI_KEY_FILE={self.output / 'no-openai-key'}",
            "PUBLISHER_PUBLISH_OPTIONS=",
            f"PUBLISHER_IMAGE={IMAGE}",
        ):
            self.assertIn(line + "\n", env)
        jobs = job_runner.load_config(self.output / "jobs.json")
        self.assertEqual(jobs["activation_hold"], str(self.base / "control" / "activation.hold"))
        self.assertIn(terms, jobs["configuration_files"])
        self.assertIn(str(about), jobs["configuration_files"])
        self.assertIn(str(self.secrets / "id_ed25519"), jobs["configuration_files"])
        ai = (self.output / "ai.env").read_text()
        self.assertIn("AI_ENABLED=false\n", ai)
        self.assertIn("AI_DAILY_BUDGET_USD=1.00\n", ai)
        ssh = (self.output / "ssh_config").read_text()
        self.assertIn("Host site\n  HostName site.example.net\n  User deploy\n", ssh)
        self.assertIn("IdentityFile /home/publisher/.ssh/id_ed25519\n", ssh)
        modes = {"publisher.json": 0o640, "ai.env": 0o640, "compose.env": 0o640, "jobs.json": 0o600}
        modes.update({"setup.json": 0o600, "ssh_config": 0o600, "no-openai-key": 0o600})
        modes.update(dict.fromkeys([*setup_tool.SERVICES, *setup_tool.TIMERS], 0o640))
        for name, mode in modes.items():
            self.assertEqual(stat.S_IMODE((self.output / name).stat().st_mode), mode, name)
        for name, mode in (("config", 0o750), ("state", 0o700), ("artifacts", 0o700), ("control", 0o700)):
            self.assertEqual(stat.S_IMODE((self.base / name).stat().st_mode), mode, name)
        self.assertEqual((self.output / "no-openai-key").stat().st_size, 0)
        hold = self.base / "control" / "activation.hold"
        self.assertTrue(result["hold_created"])
        self.assertEqual(stat.S_IMODE(hold.stat().st_mode), 0o600)
        # A rerun changes nothing and leaves a removed hold removed.
        hold.unlink()
        code, again = run(
            "configure", "--answers", str(self.base / "answers.json"), "--output", str(self.output)
        )
        self.assertEqual((code, again["changed"], again["hold_created"]), (0, [], False))
        self.assertFalse(hold.exists())
        # A changed job input sets the hold again.
        code, changed = self.configure(**self.ssh_answers(), memory_limit="3g")
        self.assertEqual(code, 0, changed)
        self.assertIn(str(self.output / "compose.env"), changed["changed"])
        self.assertTrue(changed["hold_created"])

    def test_ghost_only_delivery_publishes_without_ssh(self):
        code, result = self.configure()
        self.assertEqual(code, 0, result)
        env = (self.output / "compose.env").read_text()
        self.assertIn("PUBLISHER_PUBLISH_OPTIONS=--no-deploy\n", env)
        self.assertIn("PUBLISHER_SSH_CONFIG=/dev/null\n", env)
        self.assertFalse((self.output / "ssh_config").exists())
        self.assertEqual(json.loads((self.output / "publisher.json").read_text())["preview_ssh_host"], "none")
        files = json.loads((self.output / "jobs.json").read_text())["configuration_files"]
        self.assertNotIn("/dev/null", files)

    def test_configure_rejects_invalid_and_unsafe_answers(self):
        cases = (
            ({"ghost_origin": "http://ghost.example.net"}, "https://"),
            ({"ghost_origin": "https://user:secret@ghost.example.net"}, "password"),
            ({"ghost_origin": "https://ghost.example.net/ghost/"}, "no path"),
            ({"site_origin": "https://Example.com"}, "lower case"),
            ({"live_since": "2026-11-01T00:00:00"}, "offset"),
            ({"image": "wtf-adsb-publisher:local"}, "local image ID"),
            ({"deploy_target": "directory"}, "one of"),
            ({"deploy_target": "ghost-ssh"}, "ssh.host is required"),
            ({"ai.enabled": True}, "openai_key_file is required"),
            ({"ai.public_reserve_usd": 2}, "must not exceed"),
            ({"ai.public_model": "gpt-unknown"}, "one of"),
            ({"production": True}, "site_origin"),
            ({"production": True, "site_origin": "https://findings.example.org"}, "site_name is required"),
            ({"support_link.url": "https://example.org/support"}, "together"),
            ({"schedule.publish": "*:00\nExecStart=/bin/sh"}, "OnCalendar"),
            ({"site_name": "two\nlines"}, "one line"),
            ({"site_name": "Padded "}, "no leading or trailing spaces"),
            ({"author_name": "---"}, "author_name must contain a letter or digit"),
            ({"support_link.url": "https://192.0.2.1/", "support_link.label": "Support"}, "lower-case host"),
            ({"uid": 4294967295}, "between 1 and 4294967294"),
            ({"ghost_key_file": str(self.base / "state" / "key")}, "inside the state directory"),
            ({"feed_dir": str(self.base / "state" / "feed")}, "must be separate"),
            ({"surprise": 1}, "Unknown answer keys"),
            ({"state_dir": str(Path("/tmp") / "x" / ".." / "y")}, "without '..'"),
        )
        for extra, message in cases:
            with self.subTest(extra=extra):
                code, result = self.configure(**extra)
                self.assertEqual(code, 1)
                self.assertIn(message, result["error"])
        self.assertFalse(self.output.exists())

    def test_null_answers_take_their_defaults(self):
        code, result = self.configure(
            uid=None, gid=None, memory_limit=None, site_name=None, private_terms=None
        )
        self.assertEqual(code, 0, result)
        env = (self.output / "compose.env").read_text()
        self.assertIn("PUBLISHER_UID=1000\n", env)
        self.assertIn("PUBLISHER_MEMORY_LIMIT=4g\n", env)
        self.assertNotIn("None", env)
        config = json.loads((self.output / "publisher.json").read_text())
        self.assertNotIn("site_name", config)
        self.assertEqual(config["private_terms"], [])
        self.assertIn("OnCalendar=*-*-* 06:10:00 UTC\n", (self.output / "wtf-adsb-weekly.timer").read_text())
        code, result = self.configure(live_since=None)
        self.assertEqual(code, 1)
        self.assertIn("live_since is required", result["error"])

    def test_configure_refuses_an_output_directory_inside_the_repository(self):
        target = ROOT / "state" / "setup-tool-test"
        code, result = run("configure", "--answers", self.answers(), "--output", str(target))
        self.assertEqual(code, 1)
        self.assertIn("outside this repository", result["error"])
        self.assertFalse(target.exists())
        code, result = run(
            "configure", "--answers", str(ROOT / "answers-test.json"), "--output", str(self.output)
        )
        self.assertIn("answers file outside this repository", result["error"])

    def test_configure_refuses_paths_that_contain_the_repository_or_sit_near_the_root(self):
        if len(ROOT.parent.parts) >= 3:
            code, result = run("configure", "--answers", self.answers(), "--output", str(ROOT.parent))
            self.assertEqual(code, 1)
            self.assertIn("must not contain it", result["error"])
        top = self.base / "top"
        top.symlink_to("/")
        for value in ("/srv", "/srv/", "/opt", str(top / "srv")):
            with self.subTest(value=value):
                code, result = self.configure(state_dir=value)
                self.assertEqual(code, 1)
                self.assertIn("two directories below /", result["error"])
        self.assertFalse(self.output.exists())

    def test_configure_refuses_a_symlinked_output_directory_or_config_file(self):
        real = self.base / "real-config"
        real.mkdir(mode=0o700)
        self.output.symlink_to(real)
        code, result = self.configure()
        self.assertEqual(code, 1)
        self.assertIn("real directory", result["error"])
        self.output.unlink()
        self.assertEqual(self.configure()[0], 0)
        elsewhere = self.base / "elsewhere.json"
        elsewhere.write_text("untouched\n")
        (self.output / "publisher.json").unlink()
        (self.output / "publisher.json").symlink_to(elsewhere)
        code, result = self.configure(production=False, site_tagline="Changed")
        self.assertEqual(code, 1)
        self.assertIn("not a symbolic link", result["error"])
        self.assertEqual(elsewhere.read_text(), "untouched\n")

    def test_configure_will_not_take_over_a_directory_holding_other_files(self):
        busy = self.base / "busy"
        busy.mkdir(mode=0o755)
        busy.chmod(0o755)
        (busy / "notes.txt").write_text("keep me\n")
        code, result = self.configure(artifacts_dir=str(busy))
        self.assertEqual(code, 1)
        self.assertIn("will not change it", result["error"])
        self.assertEqual(stat.S_IMODE(busy.stat().st_mode), 0o755)

    def test_check_passes_against_a_fake_ghost_and_a_feed_with_a_manifest(self):
        terms = self.secret("private-terms.txt", "operator-name\n")
        about = self.base / "about.md"
        about.write_text("A test receiver.\n")
        about.chmod(0o644)
        self.assertEqual(
            self.configure(**self.ssh_answers(), private_terms_file=terms, about_file=str(about))[0], 0
        )
        # Write-only keys: a check that opened one would fail.
        for name in ("ghost-admin-key", "id_ed25519"):
            (self.secrets / name).chmod(0o200)
        with self.fake_ghost():
            result = setup_tool.check(str(self.output), self.docker, str(self.bin / "ssh"))
        self.assertTrue(result["ok"], result)
        checks = result["checks"]
        self.assertEqual(checks["ghost"], {"ok": True, "status": 200, "ghost_site": True, "version": "6.0"})
        self.assertEqual(checks["configuration"]["publisher_validation"], "passed")
        self.assertEqual(checks["configuration"]["budget_issues"], [])
        self.assertEqual(checks["ssh"], {"ok": True, "returncode": 0})
        self.assertTrue(checks["feed"]["ok"])
        self.assertTrue(result["activation_hold"])
        self.assertTrue(any("activation hold" in warning for warning in result["warnings"]))

    def test_check_reports_problems(self):
        about = self.base / "about.md"
        about.write_bytes(b"x" * (setup_tool.ABOUT_LIMIT + 1))
        self.assertEqual(self.configure(about_file=str(about))[0], 0)
        about.chmod(0o200)
        (self.feed / "manifest.json").unlink()
        (self.secrets / "ghost-admin-key").chmod(0o644)
        (self.base / "state").chmod(0o755)
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        with mock.patch.object(setup_tool, "admin_site_url", lambda origin: f"http://127.0.0.1:{port}/"):
            result = setup_tool.check(str(self.output), self.docker)
        problems = " ".join(result["checks"]["directories"]["problems"])
        self.assertFalse(result["ok"])
        self.assertIn("ghost-admin-key: readable by group or others", problems)
        self.assertIn("mode 755", problems)
        self.assertIn(f"{about}: the container account", problems)
        self.assertIn(f"{about}: larger than 65536 bytes", problems)
        self.assertEqual(result["checks"]["configuration"]["publisher_validation"], "failed")
        self.assertEqual(result["checks"]["feed"]["error"], "FileNotFoundError")
        self.assertEqual(result["checks"]["ghost"], {"ok": False, "error": "URLError"})

    def test_check_flags_a_reserve_below_the_largest_public_request(self):
        self.assertEqual(self.configure(**{"ai.public_reserve_usd": 0.1})[0], 0)
        with self.fake_ghost():
            result = setup_tool.check(str(self.output), self.docker)
        configuration = result["checks"]["configuration"]
        self.assertFalse(configuration["ok"])
        self.assertIn("below the largest public request", " ".join(configuration["budget_issues"]))

    def test_check_and_deploy_refuse_files_another_account_could_change(self):
        self.assertEqual(self.configure()[0], 0)
        for path, loose, restore in (
            (self.output, 0o770, 0o750),
            (self.output / "setup.json", 0o620, 0o600),
            (self.output / "compose.env", 0o660, 0o640),
        ):
            path.chmod(loose)
            try:
                for command in ("check", "deploy"):
                    with self.subTest(path=path.name, command=command):
                        code, result = run(command, "--output", str(self.output), "--docker", self.docker)
                        self.assertEqual(code, 1)
                        self.assertIn(f"{path} is writable by group or others", result["error"])
            finally:
                path.chmod(restore)

    def test_deploy_print_only_shows_the_commands(self):
        self.assertEqual(self.configure()[0], 0)
        environment = {**os.environ, "XDG_CONFIG_HOME": str(self.base / "xdg")}
        code, result = run(
            "deploy", "--output", str(self.output), "--print-only", "--docker", self.docker, env=environment
        )
        self.assertEqual(code, 0, result)
        compose = [
            self.docker,
            "compose",
            "--project-name",
            "wtf-adsb-publisher",
            "--env-file",
            str(self.output / "compose.env"),
            "-f",
            str(ROOT / "deploy/container/compose.yaml"),
        ]
        commands = result["commands"]
        self.assertEqual(list(commands), ["initialize", "daemon_reload", "enable_reconcile", "status"])
        self.assertEqual(
            commands["initialize"],
            [
                *compose,
                "--profile",
                "maintenance",
                "run",
                "--rm",
                "admin",
                "admin",
                "init",
                "--state-dir",
                "/state",
            ]
            + ["--available-from", "2026-11-01"],
        )
        self.assertEqual(
            commands["enable_reconcile"], ["systemctl", "--user", "enable", setup_tool.RECONCILE]
        )
        self.assertEqual(
            commands["status"][1:],
            [str(ROOT / "tools/run_container_job.py"), "--config"]
            + [
                str(self.output / "jobs.json"),
                "status",
            ],
        )
        self.assertIn(str(self.base / "xdg/systemd/user/wtf-adsb-autopilot.timer"), result["units"])
        self.assertFalse((self.base / "xdg").exists())
        code, result = run(
            "deploy",
            "--output",
            str(self.output),
            "--print-only",
            "--start",
            "--docker",
            self.docker,
            env=environment,
        )
        self.assertEqual(
            result["commands"]["start_timers"], ["systemctl", "--user", "enable", "--now", *setup_tool.TIMERS]
        )

    def test_deploy_installs_units_and_starts_timers_only_without_the_hold(self):
        self.assertEqual(self.configure()[0], 0)
        xdg = self.base / "xdg"
        environment = {"XDG_CONFIG_HOME": str(xdg), "PATH": f"{self.bin}:{os.environ['PATH']}"}
        hold = self.base / "control" / "activation.hold"
        with mock.patch.dict(os.environ, environment), self.fake_ghost():
            result = setup_tool.deploy(
                str(self.output), self.docker, str(self.bin / "systemctl"), False, True
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["step"], "start")
            self.assertTrue(result["initialized"])
            self.assertIs(result["status"]["fenced"], True)
            hold.unlink()
            # The fake init wrote no ledger, so the next deploy initializes again; check still runs first.
            result = setup_tool.deploy(
                str(self.output), self.docker, str(self.bin / "systemctl"), False, True
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["timers"], list(setup_tool.TIMERS))
        units = xdg / "systemd" / "user"
        self.assertEqual(
            sorted(path.name for path in units.iterdir()), sorted([*setup_tool.SERVICES, *setup_tool.TIMERS])
        )
        self.assertEqual(
            (units / "wtf-adsb-writer.timer").read_text(), (self.output / "wtf-adsb-writer.timer").read_text()
        )
        for lock in setup_tool.LOCKS:
            self.assertEqual((self.base / "state" / lock).stat().st_size, 0, lock)

    def test_deploy_sets_the_hold_before_it_changes_a_job_input(self):
        self.assertEqual(self.configure()[0], 0)
        hold = self.base / "control" / "activation.hold"
        hold.unlink()
        env, jobs = self.output / "compose.env", self.output / "jobs.json"
        rendered = env.read_text()
        env.write_text(rendered + "PUBLISHER_IMAGE=sha256:" + "cd" * 32 + "\n")
        environment = {"XDG_CONFIG_HOME": str(self.base / "xdg"), "PATH": f"{self.bin}:{os.environ['PATH']}"}
        systemctl = str(self.bin / "systemctl")
        with mock.patch.dict(os.environ, environment):
            result = setup_tool.deploy(str(self.output), self.docker, systemctl, False, False)
            self.assertTrue(result["ok"], result)
            self.assertEqual((result["changed"], result["hold_created"]), ([str(env)], True))
            self.assertEqual(env.read_text(), rendered)
            self.assertEqual(json.loads(hold.read_text())["reason"], "setup deploy")
            self.assertEqual(stat.S_IMODE(hold.stat().st_mode), 0o600)
            self.assertTrue(any("activation hold" in warning for warning in result["warnings"]))
            # Unchanged inputs leave a removed hold removed.
            hold.unlink()
            result = setup_tool.deploy(str(self.output), self.docker, systemctl, False, False)
            self.assertTrue(result["ok"], result)
            self.assertEqual((result["changed"], result["hold_created"]), ([], False))
            self.assertFalse(hold.exists())
            # Without a way to set the hold, deploy changes nothing.
            jobs.write_text(jobs.read_text().replace('"version": 1', '"version": 2'))
            changed = jobs.read_text()
            (self.base / "control").chmod(0)
            try:
                with self.assertRaisesRegex(setup_tool.SetupError, "rerun configure"):
                    setup_tool.deploy(str(self.output), self.docker, systemctl, False, False)
            finally:
                (self.base / "control").chmod(0o700)
        self.assertEqual(jobs.read_text(), changed)
        self.assertFalse(hold.exists())

    def test_an_unreadable_control_directory_counts_as_held(self):
        self.assertEqual(self.configure()[0], 0)
        control = self.base / "control"
        (control / "activation.hold").unlink()
        self.assertFalse(setup_tool.held(control / "activation.hold"))
        control.chmod(0)
        try:
            self.assertTrue(setup_tool.held(control / "activation.hold"))
            with self.fake_ghost():
                result = setup_tool.check(str(self.output), self.docker)
        finally:
            control.chmod(0o700)
        self.assertTrue(result["activation_hold"])

    def test_check_fails_when_uid_is_not_the_operator(self):
        self.assertEqual(self.configure(uid=os.getuid() + 1)[0], 0)
        with self.fake_ghost():
            result = setup_tool.check(str(self.output), self.docker)
        account = result["checks"]["account"]
        self.assertFalse(result["ok"])
        self.assertEqual((account["ok"], account["operator"]), (False, os.getuid()))
        self.assertIn(f"uid is {os.getuid() + 1}", account["error"])
        self.assertFalse(any("timers run as" in warning for warning in result["warnings"]))

    @contextlib.contextmanager
    def as_root(self, sudo_uid=None, ancestors=True):
        """Pretend the tool runs as root, with SUDO_UID set or unset; yield the recorded fchown."""
        environment = {key: value for key, value in os.environ.items() if key != "SUDO_UID"}
        if sudo_uid is not None:
            environment["SUDO_UID"] = str(sudo_uid)
        owners = {}

        def record(descriptor, uid, gid):
            path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
            name = path.name[1:-4] if path.name.startswith(".") and path.name.endswith(".tmp") else path.name
            owners[path.with_name(name)] = (uid, gid)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(setup_tool.os, "geteuid", return_value=0))
            stack.enter_context(mock.patch.dict(os.environ, environment, clear=True))
            fchown = stack.enter_context(mock.patch.object(setup_tool.os, "fchown", side_effect=record))
            if not ancestors:
                stack.enter_context(mock.patch.object(setup_tool, "check_ancestors"))
            fchown.owners = owners
            yield fchown

    def test_sudo_configure_keeps_the_configuration_with_the_operator(self):
        owner, group = os.getuid(), os.getgid()
        self.output.mkdir(mode=0o700)
        answers = Path(self.answers(**self.ssh_answers(), uid=owner + 1))
        moved = self.output / "answers.json"
        answers.rename(moved)
        moved.chmod(0o644)
        with self.as_root(owner) as fchown:
            result = setup_tool.configure(str(moved), str(self.output))
        self.assertTrue(result["ok"], result)
        operator, container = (owner, group), (owner + 1, group)
        expected = {self.output: operator}
        names = ["publisher.json", "ai.env", "compose.env", "jobs.json", "setup.json", "answers.json"]
        for name in names + [*setup_tool.SERVICES, *setup_tool.TIMERS]:
            expected[self.output / name] = operator
        for name in ("ssh_config", "no-openai-key"):
            expected[self.output / name] = container
        for name in ("state", "artifacts", "control"):
            expected[self.base / name] = container
        expected[self.base / "control" / "activation.hold"] = container
        self.assertEqual(fchown.owners, expected)
        self.assertEqual(stat.S_IMODE(moved.stat().st_mode), 0o600)

    def test_sudo_refuses_parents_another_account_could_swap(self):
        owner, values = os.getuid(), {"uid": os.getuid() + 1, "gid": os.getgid()}
        for sudo_uid in (None, owner + 2):
            with self.subTest(sudo_uid=sudo_uid), self.as_root(sudo_uid) as fchown:
                with self.assertRaisesRegex(setup_tool.SetupError, f"belongs to uid {owner}\\."):
                    setup_tool.make_directory(self.base / "state", values, "data")
                fchown.assert_not_called()
            self.assertFalse((self.base / "state").exists())

    def test_sudo_refuses_an_existing_directory_owned_by_another_account(self):
        owner = os.getuid()
        target = self.base / "foreign"
        target.mkdir(mode=0o755)
        target.chmod(0o755)
        with self.as_root(owner + 2, ancestors=False) as fchown:
            with self.assertRaisesRegex(
                setup_tool.SetupError, f"belongs to uid {owner}, not uid {owner + 1}"
            ):
                setup_tool.make_directory(target, {"uid": owner + 1, "gid": os.getgid()}, "data")
        fchown.assert_not_called()

    def test_deploy_refuses_to_run_under_sudo(self):
        self.assertEqual(self.configure()[0], 0)
        with self.as_root(os.getuid()), self.assertRaisesRegex(setup_tool.SetupError, "without sudo"):
            setup_tool.deploy(str(self.output), self.docker, "systemctl", False, False)

    def test_writes_stay_in_the_opened_directory_after_a_swap(self):
        values = {"uid": os.getuid(), "gid": os.getgid()}
        target, elsewhere = self.base / "artifacts", self.base / "elsewhere"
        elsewhere.mkdir()
        descriptor, _ = setup_tool.make_directory(target, values, "data")
        try:
            target.rename(self.base / "moved")
            target.symlink_to(elsewhere)
            setup_tool.write_private(descriptor, "file.json", "{}\n", None, 0o600, target / "file.json")
        finally:
            os.close(descriptor)
        self.assertEqual((self.base / "moved" / "file.json").read_text(), "{}\n")
        self.assertEqual(list(elsewhere.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
