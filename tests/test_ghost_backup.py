"""Exercise backup failures without Docker or production filesystem access."""

import gzip
import hashlib
import io
import os
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = b"CREATE TABLE sample (id INT);\nINSERT INTO sample VALUES (7);\n-- Dump completed on 2026-10-04\n"


class GhostBackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="wtf-adsb-backup-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.deployment = self.root / "deployment"
        self.backups = self.root / "backups"
        self.content = self.root / "content"
        self.commands = self.root / "commands"
        self.state = self.root / "state"
        self.state.write_text("running")
        self.log = self.root / "calls"
        for directory in (self.deployment, self.content, self.commands):
            directory.mkdir()
        (self.content / "image.txt").write_text("sample content")
        for name in (".env", "compose.yaml", "Caddyfile", "wtf-adsb-backup.service", "wtf-adsb-backup.timer"):
            (self.deployment / name).write_text("fixture\n")
        for name in ("tailscale", "secrets"):
            (self.deployment / name).mkdir()
            (self.deployment / name / "fixture.txt").write_text("not a credential\n")

        source = (ROOT / "deploy/ghost/backup.sh").read_text()
        replacements = {
            "cd /opt/wtf-adsb": f"cd {shlex.quote(str(self.deployment))}",
            "backup_dir=/var/backups/wtf-adsb": f"backup_dir={shlex.quote(str(self.backups))}",
            "exec 9>/run/lock/wtf-adsb-backup.lock": f"exec 9>{shlex.quote(str(self.root / 'lock'))}",
        }
        for original, replacement in replacements.items():
            self.assertEqual(source.count(original), 1, f"Review isolation before changing {original}")
            source = source.replace(original, replacement)
        for production_path in ("/opt/wtf-adsb", "/var/backups/", "/run/lock/"):
            self.assertNotIn(production_path, source)
        self.script = self.deployment / "backup.sh"
        self.script.write_text(source)

        self.env = {
            "PATH": f"{self.commands}:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "BACKUP_TEST_ROOT": str(self.root),
            "BACKUP_TEST_TAR": shutil.which("tar") or "/usr/bin/tar",
        }
        common = """
            import os
            import signal
            import sys
            from pathlib import Path

            root = Path(os.environ["BACKUP_TEST_ROOT"])
            fault = os.environ.get("BACKUP_TEST_FAULT")
            args = sys.argv[1:]

            def record(event):
                with (root / "calls").open("a") as log:
                    log.write(event + "\\n")
        """
        self.executable(
            "docker",
            common,
            """
            if args[:2] == ["compose", "ps"]:
                record("status")
                if fault == "status":
                    sys.exit(42)
                statuses = {args[index + 1] for index, arg in enumerate(args) if arg == "--status"}
                if (root / "state").read_text() in statuses:
                    print("fake-container-id")
            elif args[:2] == ["compose", "stop"]:
                record("stop")
                (root / "state").write_text("stopped")
            elif args[:2] == ["compose", "exec"]:
                record("dump")
                print("CREATE TABLE sample (id INT);\\nINSERT INTO sample VALUES (7);")
                if fault == "truncated":
                    sys.exit(0)
                print("-- Dump completed on 2026-10-04")
                if fault == "dump":
                    sys.exit(43)
                if fault == "signal":
                    os.kill(os.getppid(), signal.SIGTERM)
            elif args[:2] == ["volume", "inspect"]:
                print(root / "content")
            elif args[:2] == ["compose", "up"]:
                record("start")
                if fault == "start":
                    # Ghost is not running, as when an earlier stop is still in flight.
                    sys.exit(46)
                (root / "state").write_text("running")
                if fault == "health":
                    sys.exit(44)
            else:
                raise SystemExit("Unexpected Docker call in isolated backup test: " + repr(args))
            """,
        )
        self.executable(
            "tar",
            common,
            """
            if args[0] == "-cf":
                record("archive")
                if fault == "archive":
                    Path(args[1]).write_bytes(b"incomplete archive")
                    sys.exit(45)
            os.execv(os.environ["BACKUP_TEST_TAR"], ["tar", *args])
            """,
        )

    def executable(self, name, *parts):
        path = self.commands / name
        path.write_text("#!/usr/bin/python3\n" + "\n".join(textwrap.dedent(part) for part in parts))
        path.chmod(0o755)

    def run_backup(self, fault="", *arguments):
        return subprocess.run(
            ["/bin/bash", str(self.script), *arguments],
            env={**self.env, "BACKUP_TEST_FAULT": fault},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def assert_no_artifacts(self):
        self.assertEqual(list(self.backups.iterdir()), [])

    @property
    def marker(self):
        return self.deployment / ".ghost-stopped-by-backup"

    def test_complete_archive_contains_restorable_data_and_valid_checksums(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        archives = list(self.backups.iterdir())
        self.assertEqual(len(archives), 1)
        self.assertEqual(stat.S_IMODE(archives[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.backups.stat().st_mode), 0o700)
        self.assertEqual(self.state.read_text(), "running")
        self.assertFalse(self.marker.exists())
        calls = self.calls()
        self.assertLess(calls.index("stop"), calls.index("dump"))
        self.assertLess(calls.index("start"), calls.index("archive"))
        with tarfile.open(archives[0]) as archive:
            files = {name: archive.extractfile(name).read() for name in archive.getnames()}
        self.assertEqual(gzip.decompress(files["ghost.sql.gz"]), SQL)
        for line in files["SHA256SUMS"].decode().splitlines():
            checksum, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256(files[name]).hexdigest(), checksum)
        with tarfile.open(fileobj=io.BytesIO(files["content.tar.gz"])) as content:
            self.assertEqual(content.extractfile("./image.txt").read(), b"sample content")
        with tarfile.open(fileobj=io.BytesIO(files["configuration.tar.gz"])) as configuration:
            self.assertIn("secrets/fixture.txt", configuration.getnames())
            self.assertIn("wtf-adsb-backup.timer", configuration.getnames())

    def test_initially_stopped_ghost_remains_stopped(self):
        self.state.write_text("stopped")
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(), "stopped")
        self.assertNotIn("stop", self.calls())
        self.assertNotIn("start", self.calls())
        self.assertFalse(self.marker.exists())

    def test_restarting_ghost_is_stopped_before_dump_and_started_afterward(self):
        self.state.write_text("restarting")
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(), "running")
        calls = self.calls()
        self.assertLess(calls.index("stop"), calls.index("dump"))
        self.assertLess(calls.index("dump"), calls.index("start"))

    def test_status_failure_aborts_before_database_or_ghost_changes(self):
        result = self.run_backup("status")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), ["status"])
        self.assertEqual(self.state.read_text(), "running")
        self.assert_no_artifacts()

    def test_failed_dump_restarts_ghost_and_discards_partial_data(self):
        result = self.run_backup("dump")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stop", self.calls())
        self.assertIn("start", self.calls())
        self.assertEqual(self.state.read_text(), "running")
        self.assert_no_artifacts()

    def test_failed_outer_archive_leaves_no_partial_files(self):
        result = self.run_backup("archive")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archive", self.calls())
        self.assertEqual(self.state.read_text(), "running")
        self.assert_no_artifacts()

    def test_failed_restart_health_check_does_not_report_backup_success(self):
        result = self.run_backup("health")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("start", self.calls())
        self.assertNotIn("Backup complete", result.stdout)
        self.assert_no_artifacts()

    def test_termination_during_dump_restarts_ghost_and_cleans_up(self):
        result = self.run_backup("signal")
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn("start", self.calls())
        self.assertEqual(self.state.read_text(), "running")
        self.assertFalse(self.marker.exists())
        self.assert_no_artifacts()

    def test_truncated_dump_is_not_archived_even_when_the_export_exits_zero(self):
        result = self.run_backup("truncated")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dump is incomplete", result.stderr)
        self.assertEqual(self.state.read_text(), "running")
        self.assertFalse(self.marker.exists())
        self.assert_no_artifacts()

    def test_run_after_a_killed_backup_restarts_ghost_and_removes_its_private_work(self):
        # A kill or reboot after the stop leaves Ghost down, with a plaintext dump on disk.
        self.state.write_text("stopped")
        self.marker.write_text("")
        leftover = self.backups / ".work.killed"
        leftover.mkdir(parents=True)
        (leftover / "ghost.sql.gz").write_bytes(b"private dump")
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state.read_text(), "running")
        self.assertNotIn("stop", self.calls())
        self.assertFalse(self.marker.exists())
        self.assertFalse(leftover.exists())
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    def test_unhealthy_restart_fails_once_and_leaves_later_stops_to_the_operator(self):
        self.assertNotEqual(self.run_backup("health").returncode, 0)
        # Ghost was started, so a stop after this point is deliberate.
        self.assertEqual(self.calls().count("start"), 1)
        self.assertFalse(self.marker.exists())
        self.assert_no_artifacts()

    def test_failed_start_keeps_the_marker_and_recovery_starts_ghost_without_a_backup(self):
        self.assertNotEqual(self.run_backup("start").returncode, 0)
        self.assertEqual(self.state.read_text(), "stopped")
        self.assertTrue(self.marker.exists())
        self.log.unlink()
        result = self.run_backup("", "recover")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), ["start"])
        self.assertIn("Starting Ghost", result.stdout)
        self.assertEqual(self.state.read_text(), "running")
        self.assertFalse(self.marker.exists())
        self.assert_no_artifacts()

    def test_unknown_argument_is_refused_before_anything_runs(self):
        result = self.run_backup("", "recovr")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.backups.exists())

    def test_recovery_without_a_marker_changes_nothing(self):
        self.state.write_text("stopped")
        result = self.run_backup("", "recover")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.state.read_text(), "stopped")
        self.assert_no_artifacts()

    def test_success_prunes_old_backups_and_preserves_recent_ones(self):
        self.backups.mkdir()
        old = self.backups / "ghost-old.tar"
        recent = self.backups / "ghost-recent.tar"
        for path, age_days in ((old, 8), (recent, 2)):
            path.write_bytes(b"existing archive")
            timestamp = time.time() - age_days * 86400
            os.utime(path, (timestamp, timestamp))
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old.exists())
        self.assertEqual(recent.read_bytes(), b"existing archive")
