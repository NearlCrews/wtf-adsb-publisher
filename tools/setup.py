"""Configure, check, and install the publisher's scheduled container jobs.

An AI assistant or a person runs these subcommands after the interview in
docs/setup-interview.md. Each one prints exactly one JSON object on stdout and
exits nonzero on failure. The tool uses only the Python standard library and
runs on Python 3.11 or newer, since the host's Python may be older than the
publisher's. It never reads key files; it checks their metadata only.
"""

import argparse
import contextlib
import http.client
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTERVIEW = ROOT / "docs" / "setup-interview.md"
COMPOSE = ROOT / "deploy" / "container" / "compose.yaml"
UNIT_SOURCE = ROOT / "deploy" / "ghost-preview"
RUNNER = ROOT / "tools" / "run_container_job.py"
PROJECT = "wtf-adsb-publisher"
CONFIG_NAME = "publisher.json"
AI_NAME = "ai.env"
ENV_NAME = "compose.env"
JOBS_NAME = "jobs.json"
SETTINGS_NAME = "setup.json"
SSH_NAME = "ssh_config"
EMPTY_KEY_NAME = "no-openai-key"
JOB_DIRECTORY = "jobs"
HOLD_NAME = "activation.hold"
TIMERS = {
    "wtf-adsb-autopilot.timer": "schedule.publish",
    "wtf-adsb-writer.timer": "schedule.write",
    "wtf-adsb-weekly.timer": "schedule.weekly",
}
SERVICES = (
    "wtf-adsb-autopilot.service",
    "wtf-adsb-writer.service",
    "wtf-adsb-weekly.service",
    "wtf-adsb-publisher-reconcile.service",
)
UNITS = (*SERVICES, *TIMERS)
RECONCILE = SERVICES[3]
TEMPLATE_CHECKOUT = "%h/src/wtf-adsb-publisher"
# The README's manual layout, which the unit templates name.
TEMPLATE_HOLD = "/srv/wtf-adsb-publisher/control/activation.hold"
TEMPLATE_JOBS = "/srv/wtf-adsb-publisher/config/jobs.json"
CONTAINER_SSH = "/home/publisher/.ssh"
CONTAINER_TERMS = "/config/private-terms.txt"
CONTAINER_ABOUT = "/config/about.md"
# Largest About file, in bytes, as in site_identity.ABOUT_LIMIT.
ABOUT_LIMIT = 64 * 1024
SAFE_PATH = re.compile(r"/[A-Za-z0-9._/-]+")
MEMORY = re.compile(r"[1-9][0-9]*[bkmg]")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
HOST_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}")
HOST_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}")
SSH_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
CALENDAR = re.compile(r"[A-Za-z0-9*,:./~ -]{1,100}")
EMAIL = re.compile(
    r"[A-Za-z0-9_%+-]+(?:\.[A-Za-z0-9_%+-]+)*@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
)
# Copies of the publisher's rules, since this tool imports nothing from it; a test holds
# them equal. A host label, as in site_origin and site_identity.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# site_origin.FORM: the last label starts with a letter, so IP addresses are refused.
SITE_ORIGIN = re.compile(rf"https://(?:{_LABEL}\.)*(?=[a-z]){_LABEL}")
# site_identity.URL_FORM and URL_LIMIT: an https:// address with a lower-case host name.
SUPPORT_URL = re.compile(
    rf"https://(?:{_LABEL}\.)*(?=[a-z]){_LABEL}(?:[/?#][A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%-]*)?"
)
SUPPORT_URL_LIMIT = 2000
# site_identity._CONTROL: control characters, the line and paragraph separators, and the
# byte order mark.
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\ufeff]")
MODELS = ("gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol", "gpt-6-astra")
EXAMPLE_ORIGIN = "https://example.com"
PROBE_TIMEOUT = 5


class SetupError(Exception):
    """A user-facing failure; the message is safe to print."""


@dataclass(frozen=True)
class Question:
    group: str
    key: str
    type: str
    default: object = None
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    length: int = 0


QUESTIONS = (
    Question("Feed", "feed_dir", "path", "/srv/wtf-adsb/feed"),
    Question("Directories", "state_dir", "path", "/srv/wtf-adsb-publisher/state"),
    Question("Directories", "artifacts_dir", "path", "/srv/wtf-adsb-publisher/artifacts"),
    Question("Directories", "control_dir", "path", "/srv/wtf-adsb-publisher/control"),
    Question("Directories", "reference_dir", "path"),
    Question("Site", "site_origin", "url", EXAMPLE_ORIGIN),
    Question("Site", "site_name", "string", length=60),
    Question("Site", "site_tagline", "string", length=120),
    Question("Site", "site_description", "string", length=160),
    Question("Site", "author_name", "string", length=60),
    Question("Site", "about_file", "path"),
    Question("Site", "support_link.url", "url"),
    Question("Site", "support_link.label", "string", length=40),
    Question("Site", "contact_email", "email"),
    Question("Site", "production", "boolean", False),
    Question("Site", "live_since", "timestamp", required=True),
    Question("Site", "private_terms_file", "path"),
    Question("Site", "private_terms", "list", ()),
    Question("Ghost", "ghost_origin", "url", required=True),
    Question("Ghost", "ghost_key_file", "path", required=True),
    Question("Delivery", "deploy_target", "choice", "ghost-ssh", choices=("ghost-ssh", "ghost")),
    Question("Delivery", "ssh.host", "string"),
    Question("Delivery", "ssh.hostname", "string"),
    Question("Delivery", "ssh.user", "string"),
    Question("Delivery", "ssh.identity_file", "path"),
    Question("Delivery", "ssh.known_hosts_file", "path"),
    Question("Delivery", "ssh.host_key_alias", "string"),
    Question("AI", "ai.enabled", "boolean", False),
    Question("AI", "ai.public_model", "choice", "gpt-6.1-sol", choices=MODELS),
    Question("AI", "ai.reasoning_effort", "choice", "low", choices=("low", "medium", "high")),
    Question("AI", "ai.daily_budget_usd", "number", 1.0, minimum=0, maximum=1000),
    Question("AI", "ai.public_reserve_usd", "number", 0.75, minimum=0, maximum=1000),
    Question("AI", "openai_key_file", "path"),
    Question("Schedule", "schedule.publish", "calendar", "*-*-* *:00,30:00"),
    Question("Schedule", "schedule.write", "calendar", "*-*-* *:10,40:00"),
    Question("Schedule", "schedule.weekly", "calendar", "*-*-* 06:10:00 UTC"),
    Question("Runtime", "image", "string", required=True),
    Question("Runtime", "uid", "integer", 1000, minimum=1, maximum=4294967294),
    Question("Runtime", "gid", "integer", 1000, minimum=1, maximum=4294967294),
    Question("Runtime", "memory_limit", "string", "4g"),
)
GROUPS = ("Feed", "Directories", "Site", "Ghost", "Delivery", "AI", "Schedule", "Runtime")
BY_KEY = {question.key: question for question in QUESTIONS}
KEY_FILES = ("ghost_key_file", "openai_key_file", "ssh.identity_file")


class JsonParser(argparse.ArgumentParser):
    """Report usage errors as one JSON object, like every other result."""

    def error(self, message):
        emit({"ok": False, "error": "usage: " + message})
        raise SystemExit(2)


def emit(result):
    print(json.dumps(result, indent=2, sort_keys=False))


def interview_text():
    """Read the Validation and Why columns from the interview document."""
    rows = {}
    if not INTERVIEW.is_file():
        return rows
    for line in INTERVIEW.read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0].startswith("`") and cells[0].endswith("`"):
            rows[cells[0].strip("`")] = {"validation": cells[3], "why": cells[4]}
    return rows


def plan():
    text = interview_text()
    groups = []
    for group in GROUPS:
        questions = []
        for question in QUESTIONS:
            if question.group != group:
                continue
            default = list(question.default) if isinstance(question.default, tuple) else question.default
            questions.append(
                {
                    "key": question.key,
                    "type": question.type,
                    "default": default,
                    "required": question.required,
                    **({"choices": list(question.choices)} if question.choices else {}),
                    **text.get(question.key, {"validation": "", "why": ""}),
                }
            )
        groups.append({"name": group, "questions": questions})
    return {
        "ok": True,
        "document": str(INTERVIEW.relative_to(ROOT)),
        "configuration_directory": (
            "The private directory passed as --output holds the answers file, publisher.json, ai.env, "
            "compose.env, jobs.json, setup.json, ssh_config, the rendered systemd units, and the job "
            "runner's jobs directory. Compose mounts only publisher.json from it."
        ),
        "activation_hold": (
            "configure creates activation.hold in the control directory the first time it runs and "
            "whenever it changes a job input, and deploy creates it before it rewrites compose.env or "
            "jobs.json with new content. The timers skip their runs while it exists. The user "
            "removes it after check passes, then runs deploy --start."
        ),
        "groups": groups,
    }


def overlaps_repository(path):
    """True when the path is inside this repository or contains it, after resolving links."""
    resolved = Path(os.path.abspath(path)).resolve()
    return resolved.is_relative_to(ROOT) or ROOT.is_relative_to(resolved)


def is_number(value):
    return type(value) in (int, float)


def check_path(key, value):
    if not isinstance(value, str) or not SAFE_PATH.fullmatch(value) or "/../" in value + "/":
        raise SetupError(
            f"{key} must be an absolute path of letters, digits, '.', '_', '-', and '/' without '..'"
        )
    normal = os.path.normpath(value)
    if len(Path(normal).parts) < 3 or len(Path(normal).resolve().parts) < 3:
        raise SetupError(
            f"{key} must be at least two directories below /, such as /srv/wtf-adsb-publisher, "
            "including after following symbolic links"
        )
    if overlaps_repository(normal):
        raise SetupError(f"{key} must be outside this repository and must not contain it")
    return normal


def check_url(key, value):
    if not isinstance(value, str) or any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise SetupError(f"{key} must be a URL without spaces or control characters")
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise SetupError(f"{key} must start with https:// and name a host")
    if parts.username is not None or parts.password is not None:
        raise SetupError(f"{key} must not contain a user name or password")
    if key in {"site_origin", "ghost_origin"} and (
        parts.path not in {"", "/"} or parts.query or parts.fragment
    ):
        raise SetupError(f"{key} must be an origin with no path, query, or fragment")
    if key == "site_origin" and (not SITE_ORIGIN.fullmatch(value) or len(parts.hostname) > 253):
        raise SetupError(
            "site_origin must be an https:// origin such as https://example.com, in lower case, "
            "with no port, path, or trailing slash"
        )
    if key == "support_link.url" and (not SUPPORT_URL.fullmatch(value) or len(value) > SUPPORT_URL_LIMIT):
        raise SetupError(
            "support_link.url must be an https:// address such as https://example.com/support, "
            "with a lower-case host name and no port"
        )
    return value.rstrip("/") if key == "ghost_origin" else value


def check_timestamp(key, value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise SetupError(f"{key} must be an ISO 8601 time with an offset, such as 2026-11-01T00:00:00Z")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def one_line(key, value, length):
    """`value` when it is 1 to `length` characters of one-line text with no surrounding spaces,
    as site_identity.validate_text requires."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or CONTROL.search(value)
        or len(value) > length
    ):
        raise SetupError(
            f"{key} must be 1 to {length} characters on one line, with no leading or trailing spaces"
        )
    return value


def check_value(question, value):
    key, kind = question.key, question.type
    if kind == "boolean" and type(value) is not bool:
        raise SetupError(f"{key} must be true or false")
    if kind == "integer" and type(value) is not int:
        raise SetupError(f"{key} must be a whole number")
    if kind == "number" and not is_number(value):
        raise SetupError(f"{key} must be a number")
    if question.minimum is not None and not question.minimum <= value <= question.maximum:
        low, high = (
            format(limit, "d" if type(limit) is int else "g")
            for limit in (question.minimum, question.maximum)
        )
        raise SetupError(f"{key} must be between {low} and {high}")
    if kind == "path":
        return check_path(key, value)
    if kind == "url":
        return check_url(key, value)
    if kind == "timestamp":
        return check_timestamp(key, value)
    if kind == "choice" and value not in question.choices:
        raise SetupError(f"{key} must be one of: " + ", ".join(question.choices))
    if kind == "email" and (not isinstance(value, str) or not EMAIL.fullmatch(value) or len(value) > 254):
        raise SetupError(f"{key} must be a plain email address such as contact@example.com")
    if kind == "calendar" and (not isinstance(value, str) or not CALENDAR.fullmatch(value)):
        raise SetupError(f"{key} must be a systemd OnCalendar expression such as *-*-* *:00,30:00")
    if kind == "list":
        if not isinstance(value, (list, tuple)):
            raise SetupError(f"{key} must be a list of strings")
        return [one_line(key, term, 200) for term in value]
    if kind == "string":
        if not isinstance(value, str):
            raise SetupError(f"{key} must be a string")
        if question.length:
            value = one_line(key, value, question.length)
        # site_identity.validate_author: the author page's address needs a letter or digit.
        if key == "author_name" and not re.search(r"[A-Za-z0-9]", value):
            raise SetupError("author_name must contain a letter or digit from A to Z or 0 to 9")
    return value


def normalize(answers):
    """Validate an answers object and fill in the defaults."""
    if not isinstance(answers, dict):
        raise SetupError("The answers file must hold one JSON object")
    unknown = sorted(set(answers) - set(BY_KEY))
    if unknown:
        raise SetupError("Unknown answer keys: " + ", ".join(unknown))
    values = {}
    for question in QUESTIONS:
        value = answers.get(question.key)
        if value is None:
            value = question.default
        if value is None:
            if question.required:
                raise SetupError(f"{question.key} is required")
            values[question.key] = None
            continue
        values[question.key] = check_value(question, value)
    if not IMAGE.fullmatch(values["image"]):
        raise SetupError(
            "image must be a local image ID such as the one "
            "docker image inspect --format '{{.Id}}' wtf-adsb-publisher:local prints"
        )
    if not MEMORY.fullmatch(values["memory_limit"]):
        raise SetupError("memory_limit must be a whole number followed by b, k, m, or g, such as 4g")
    if values["ai.public_reserve_usd"] > values["ai.daily_budget_usd"]:
        raise SetupError("ai.public_reserve_usd must not exceed ai.daily_budget_usd")
    if values["ai.enabled"] and values["openai_key_file"] is None:
        raise SetupError("openai_key_file is required when ai.enabled is true")
    if (values["support_link.url"] is None) != (values["support_link.label"] is None):
        raise SetupError("Set support_link.url and support_link.label together, or neither")
    if values["production"]:
        if values["site_origin"] == EXAMPLE_ORIGIN:
            raise SetupError("Set site_origin to the public site's address before production is true")
        if values["site_name"] is None:
            raise SetupError("site_name is required when production is true")
    for key, pattern in (
        ("ssh.host", HOST_ALIAS),
        ("ssh.hostname", HOST_NAME),
        ("ssh.user", SSH_USER),
        ("ssh.host_key_alias", HOST_ALIAS),
    ):
        if values[key] is not None and not pattern.fullmatch(values[key]):
            raise SetupError(f"{key} has characters SSH configuration cannot hold; see the interview")
    if values["deploy_target"] == "ghost-ssh":
        for key in ("ssh.host", "ssh.user", "ssh.identity_file", "ssh.known_hosts_file"):
            if values[key] is None:
                raise SetupError(f"{key} is required when deploy_target is ghost-ssh")
    return values


def directories(values, output):
    """The directories configure creates, and the two it only reads, after checking they are separate."""
    created = {
        "config": output,
        "state": Path(values["state_dir"]),
        "artifacts": Path(values["artifacts_dir"]),
        "control": Path(values["control_dir"]),
    }
    others = {"feed": Path(values["feed_dir"])}
    if values["reference_dir"] is not None:
        others["reference"] = Path(values["reference_dir"])
    paths = list({**created, **others}.values())
    for index, first in enumerate(paths):
        for second in paths[index + 1 :]:
            if first == second or first.is_relative_to(second) or second.is_relative_to(first):
                raise SetupError(
                    "The configuration, state, artifacts, control, feed, and reference directories "
                    "must be separate"
                )
    for key in (*KEY_FILES, "ssh.known_hosts_file", "private_terms_file", "about_file"):
        if values[key] is None:
            continue
        path = Path(values[key])
        for name in ("state", "artifacts", "control", "feed"):
            place = created.get(name) or others[name]
            if path.is_relative_to(place):
                raise SetupError(f"{key} must not be inside the {name} directory, which a job can see")
    return created, others


def ssh_used(values):
    return values["deploy_target"] == "ghost-ssh"


def openai_key(values, output):
    return values["openai_key_file"] or str(output / EMPTY_KEY_NAME)


def render_config(values):
    config = {"production": values["production"], "site_origin": values["site_origin"]}
    for key in ("site_name", "site_tagline", "site_description", "author_name"):
        if values[key] is not None:
            config[key] = values[key]
    if values["support_link.url"] is not None:
        config["support_link"] = {"url": values["support_link.url"], "label": values["support_link.label"]}
    if values["contact_email"] is not None:
        config["contact_email"] = values["contact_email"]
    config.update(
        {
            "feed_directory": "/feed",
            "inbox_database": "/state/inbox.sqlite3",
            "budget_database": "/state/budget.sqlite3",
            "state_dir": "/state",
            "live_since": values["live_since"],
            "max_feed_age_seconds": 300,
            "allow_replay_publication": False,
            "extension_schemas": [],
            "ghost_origin": values["ghost_origin"],
            "ghost_key_file": "/run/secrets/ghost-admin-key",
            "weekly_key_file": "/run/secrets/openai-api-key",
            # The release fingerprint names the destination even when nothing is sent.
            "preview_ssh_host": values["ssh.host"] if ssh_used(values) else "none",
        }
    )
    if values["private_terms_file"] is not None:
        config["private_terms_file"] = CONTAINER_TERMS
    config["private_terms"] = values["private_terms"]
    if values["about_file"] is not None:
        config["about_file"] = CONTAINER_ABOUT
    if values["reference_dir"] is not None:
        config["reference_dir"] = "/references"
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def render_ai(values):
    settings = {
        "AI_ENABLED": "true" if values["ai.enabled"] else "false",
        "AI_DAILY_BUDGET_USD": f"{values['ai.daily_budget_usd']:.2f}",
        "AI_PUBLIC_RESERVE_USD": f"{values['ai.public_reserve_usd']:.2f}",
        "AI_PUBLIC_MODEL": values["ai.public_model"],
        "AI_PUBLIC_REASONING_EFFORT": values["ai.reasoning_effort"],
    }
    lines = ["# Written by tools/setup.py configure. Rerun configure to change it."]
    lines += [f"{key}={value}" for key, value in settings.items()]
    return "\n".join(lines) + "\n"


def render_env(values, places):
    created, others = places
    output = created["config"]
    ssh = ssh_used(values)
    settings = {
        "PUBLISHER_IMAGE": values["image"],
        "PUBLISHER_STATE_DIR": created["state"],
        "PUBLISHER_ARTIFACTS_DIR": created["artifacts"],
        "PUBLISHER_CONFIG_DIR": output,
        "PUBLISHER_CONTROL_DIR": created["control"],
        "ENGINE_FEED_DIR": others["feed"],
        "PUBLISHER_REFERENCE_DIR": others.get("reference", ""),
        "PUBLISHER_AI_ENV_FILE": output / AI_NAME,
        "PUBLISHER_GHOST_KEY_FILE": values["ghost_key_file"],
        "PUBLISHER_OPENAI_KEY_FILE": openai_key(values, output),
        # Ghost-only delivery never runs SSH, but Compose still names the mounts.
        "PUBLISHER_SSH_CONFIG": output / SSH_NAME if ssh else "/dev/null",
        "PUBLISHER_SSH_KEY_FILE": values["ssh.identity_file"] if ssh else "/dev/null",
        "PUBLISHER_SSH_KNOWN_HOSTS": values["ssh.known_hosts_file"] if ssh else "/dev/null",
        "PUBLISHER_PRIVATE_TERMS_FILE": values["private_terms_file"] or "",
        "PUBLISHER_ABOUT_FILE": values["about_file"] or "",
        "PUBLISHER_PUBLISH_OPTIONS": "" if ssh else "--no-deploy",
        "PUBLISHER_UID": values["uid"],
        "PUBLISHER_GID": values["gid"],
        "PUBLISHER_MEMORY_LIMIT": values["memory_limit"],
    }
    lines = ["# Written by tools/setup.py configure for deploy/container/compose.yaml."]
    lines += [f"{key}={value}" for key, value in settings.items()]
    return "\n".join(lines) + "\n"


def configuration_files(values, output):
    files = [str(output / CONFIG_NAME), str(output / AI_NAME), values["ghost_key_file"]]
    files.append(openai_key(values, output))
    if ssh_used(values):
        files += [str(output / SSH_NAME), values["ssh.identity_file"], values["ssh.known_hosts_file"]]
    for key in ("private_terms_file", "about_file"):
        if values[key] is not None:
            files.append(values[key])
    return files


def render_jobs(values, places):
    created, _ = places
    output = created["config"]
    jobs = {
        "version": 1,
        "project": PROJECT,
        "compose_file": str(COMPOSE),
        "env_file": str(output / ENV_NAME),
        "job_directory": str(output / JOB_DIRECTORY),
        "activation_hold": str(created["control"] / HOLD_NAME),
        "publisher_state_directory": str(created["state"]),
        "budget_database": str(created["state"] / "budget.sqlite3"),
        "configuration_files": configuration_files(values, output),
    }
    return json.dumps(jobs, indent=2) + "\n"


def render_ssh(values):
    lines = [
        "# Written by tools/setup.py configure. Rerun configure to change it.",
        "# Paths are as the publish job sees them.",
        f"Host {values['ssh.host']}",
        f"  HostName {values['ssh.hostname'] or values['ssh.host']}",
        f"  User {values['ssh.user']}",
        f"  IdentityFile {CONTAINER_SSH}/id_ed25519",
        "  IdentitiesOnly yes",
        "  StrictHostKeyChecking yes",
        f"  UserKnownHostsFile {CONTAINER_SSH}/known_hosts",
    ]
    if values["ssh.host_key_alias"] is not None:
        lines.append(f"  HostKeyAlias {values['ssh.host_key_alias']}")
    return "\n".join(lines) + "\n"


def checkout():
    if not SAFE_PATH.fullmatch(str(ROOT)):
        raise SetupError(
            "The checkout's path must use only letters, digits, '.', '_', '-', and '/', "
            "since systemd units name it"
        )
    return str(ROOT)


def render_units(values, places):
    """The user units from deploy/ghost-preview/, with this checkout's paths and the schedules."""
    created, _ = places
    root = checkout()
    hold = str(created["control"] / HOLD_NAME)
    jobs = str(created["config"] / JOBS_NAME)
    units = {}
    for name in UNITS:
        lines = (UNIT_SOURCE / name).read_text().splitlines()
        while lines and lines[0].startswith("#"):
            lines.pop(0)
        text = "\n".join(lines) + "\n"
        text = text.replace(TEMPLATE_HOLD, hold).replace(TEMPLATE_JOBS, jobs).replace(TEMPLATE_CHECKOUT, root)
        if name in TIMERS:
            calendar = "OnCalendar=" + values[TIMERS[name]]
            text, count = re.subn(r"(?m)^OnCalendar=.*$", lambda _, line=calendar: line, text)
            if count != 1:
                raise SetupError(f"deploy/ghost-preview/{name} no longer has exactly one OnCalendar line")
        if "%h/" in text:
            raise SetupError(f"deploy/ghost-preview/{name} names a path configure does not replace")
        header = f"# Written by tools/setup.py configure from deploy/ghost-preview/{name}.\n"
        units[name] = header + text
    return units


FILE_MODES = {
    CONFIG_NAME: 0o640,
    AI_NAME: 0o640,
    ENV_NAME: 0o640,
    JOBS_NAME: 0o600,
    SETTINGS_NAME: 0o600,
    **dict.fromkeys(UNITS, 0o640),
}
SECRET_MODE = 0o600
ANSWERS_MODE = 0o600
READ_LIMIT = 1 << 20


def sudo_uid():
    """The uid of the account that ran sudo, or None when there is none or it was root."""
    value = os.environ.get("SUDO_UID", "")
    uid = int(value) if value.isascii() and value.isdigit() else 0
    return uid or None


def operator_uid():
    """The account that owns the configuration: the sudo user, root without one, or the running account."""
    if os.geteuid() == 0:
        return sudo_uid() or 0
    return os.geteuid()


def owner_for(role, values):
    """The (uid, gid) configure gives a path under sudo, or None when it runs unprivileged.

    The configuration belongs to the operator with the container's group, so the container
    can read it but cannot change anything root reads later. The state, artifacts, and
    control directories and the files the container reads as its own account belong to uid.
    """
    if os.geteuid() != 0:
        return None
    if role == "config":
        return operator_uid(), values["gid"]
    return values["uid"], values["gid"]


def directory_mode(role):
    return 0o750 if role == "config" else 0o700


def secure(descriptor, owner, mode):
    """Set the mode, and under sudo the owner, through an open descriptor, never a path."""
    os.fchmod(descriptor, mode)
    if owner is not None:
        os.fchown(descriptor, *owner)


def already_secured(info, owner, mode):
    """True when an existing file or directory already has the mode and owner configure would give it."""
    if stat.S_IMODE(info.st_mode) != mode:
        return False
    if owner is not None:
        return (info.st_uid, info.st_gid) == owner
    return info.st_uid == os.geteuid()


def trusted_owners():
    """Accounts whose files the tool acts on: root, the running account, and the account that ran sudo."""
    return {0, os.geteuid(), sudo_uid()} - {None}


def require_trusted(label, info):
    """Refuse a file or directory that an account other than a trusted one could have changed."""
    if info.st_uid not in trusted_owners():
        raise SetupError(
            f"{label} belongs to uid {info.st_uid}. The setup tool trusts only files owned by root, the "
            "account running it, or the account that ran sudo, so the container account cannot change what "
            "root reads. Rerun configure with sudo to restore the layout."
        )
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022:
        raise SetupError(
            f"{label} is writable by group or others (mode {mode:o}). Rerun configure to restore its mode."
        )


def check_ancestors(path):
    """Under sudo, refuse a path whose existing parents another account could rename or replace."""
    if os.geteuid() != 0:
        return
    trusted = {0, sudo_uid()} - {None}
    absolute = Path(os.path.abspath(path))
    for parent in sorted(set(absolute.parents) | set(absolute.resolve().parents)):
        try:
            link = parent.lstat()
            info = parent.stat()
        except FileNotFoundError:
            continue
        mode = stat.S_IMODE(info.st_mode)
        owner = link.st_uid if stat.S_ISLNK(link.st_mode) and link.st_uid not in trusted else info.st_uid
        if owner not in trusted:
            raise SetupError(
                f"{parent} belongs to uid {owner}. Under sudo, every parent of {path} must belong to root "
                "or to the account that ran sudo, so no other account can swap the directory. Choose another "
                "location, or give that parent to root or to your account."
            )
        if mode & 0o022 and not (info.st_uid == 0 and mode & stat.S_ISVTX):
            raise SetupError(
                f"{parent} is writable by group or others (mode {mode:o}). Under sudo, every parent of "
                f"{path} must be writable only by its owner; a root-owned sticky directory such as /tmp "
                "is the exception."
            )


def check_existing_owner(path, info, values):
    """Under sudo, refuse an existing directory that belongs to neither uid nor the sudo user."""
    if os.geteuid() == 0 and info.st_uid not in {values["uid"], sudo_uid()}:
        raise SetupError(
            f"{path} already exists and belongs to uid {info.st_uid}, not uid {values['uid']} or the "
            "account that ran sudo, so configure will not change it. Name a new directory, or create "
            "it with your own account as the owner."
        )


def make_directory(path, values, role, known=frozenset()):
    """Create or secure a directory; return an open descriptor and whether it was new.

    Every later write in the directory goes through this descriptor, so swapping the path
    for a symbolic link afterwards changes nothing. An existing directory keeps its mode and
    owner unless it already has the configured ones, is empty, or holds only the files named
    in `known`; anything else is refused rather than changed. Under sudo, an existing
    directory must also belong to uid or to the account that ran sudo, and no parent may be
    open to another account. The caller closes the descriptor.
    """
    mode, owner = directory_mode(role), owner_for(role, values)
    try:
        check_ancestors(path)
        os.makedirs(path.parent, mode=0o755, exist_ok=True)
        try:
            os.mkdir(path, 0o700)
            created = True
        except FileExistsError:
            created = False
        # Check again: another account could have created a missing parent in the meantime.
        check_ancestors(path)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except PermissionError:
            raise
        except OSError:
            raise SetupError(f"{path} must be a real directory, not a file or symbolic link") from None
        try:
            info = os.fstat(descriptor)
            if not created and not already_secured(info, owner, mode):
                check_existing_owner(path, info, values)
                unexpected = sorted(set(os.listdir(descriptor)) - known)
                if unexpected:
                    raise SetupError(
                        f"{path} already exists with mode {stat.S_IMODE(info.st_mode):o}, owner "
                        f"{info.st_uid}:{info.st_gid}, and other files in it, so configure will not change "
                        f"it. Name a new or empty directory, or give it mode {mode:o} and the configured "
                        "owner yourself."
                    )
            secure(descriptor, owner, mode)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor, created
    except PermissionError:
        raise SetupError(
            f"No permission to create or secure {path}. Create its parent with your account as the owner, "
            "or rerun configure with sudo."
        ) from None


def unchanged(directory, name, data, owner, mode, label):
    """True when the file already holds `data` with the mode and owner, and has no other link.

    Nothing is followed: a symbolic link or other non-regular file is refused.
    """
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return False
    except PermissionError:
        raise
    except OSError:
        raise SetupError(f"{label} must be a regular file, not a symbolic link") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SetupError(f"{label} must be a regular file, not a symbolic link")
        if info.st_nlink == 1 and already_secured(info, owner, mode):
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                return stream.read(len(data) + 1) == data
        return False
    finally:
        os.close(descriptor)


def write_private(directory, name, content, owner, mode, label):
    """Write a file atomically inside an open directory; report whether it changed.

    Every operation is relative to the directory descriptor, and modes and owners are set on
    open descriptors before the file takes its final name, so neither a symbolic link nor a
    hard link placed there is followed. An unchanged file is kept only when it already has
    the mode and owner; otherwise it is replaced, so an account that held it open for writing
    before its owner changed cannot write to the new file.
    """
    data = content if isinstance(content, bytes) else content.encode()
    if unchanged(directory, name, data, owner, mode, label):
        return False
    temporary = "." + name + ".tmp"
    try:
        os.unlink(temporary, dir_fd=directory)
    except FileNotFoundError:
        pass
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        secure(stream.fileno(), owner, mode)
    os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    return True


def create_once(directory, name, content, owner, label):
    """Create a 0600 file that does not exist yet; never replace or read an existing one.

    Returns True when it created the file. An existing file must be a regular file; its
    size, not its content, tells an empty placeholder from a file that holds something.
    """
    try:
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, SECRET_MODE, dir_fd=directory
        )
    except FileExistsError:
        descriptor = None
    if descriptor is None:
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except OSError:
            raise SetupError(f"Cannot inspect {label}") from None
        if not stat.S_ISREG(info.st_mode):
            raise SetupError(f"{label} must be a regular file, not a symbolic link")
        return False
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content.encode())
        stream.flush()
        secure(stream.fileno(), owner, SECRET_MODE)
    return True


def open_trusted(directory, name, label):
    """Open a regular file in a directory without following links and refuse it unless it is
    trusted; return a binary stream, or None when the file does not exist."""
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    except PermissionError:
        raise SetupError(
            f"No permission to read {label}. configure ran with sudo for another uid, so rerun this with sudo"
        ) from None
    except OSError:
        raise SetupError(f"{label} must be a regular file, not a symbolic link") from None
    stream = os.fdopen(descriptor, "rb")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SetupError(f"{label} must be a regular file, not a symbolic link")
        require_trusted(label, info)
    except BaseException:
        stream.close()
        raise
    return stream


def read_trusted(directory, name, label):
    """Return a trusted file's bytes, or None when it does not exist."""
    stream = open_trusted(directory, name, label)
    if stream is None:
        return None
    with stream:
        return stream.read(READ_LIMIT)


def known_entries(name, path, values, answers_path):
    """Names configure may find in a directory it has not secured yet: its own files and the answers' files."""
    known = set()
    if name == "config":
        known |= {*FILE_MODES, SSH_NAME, EMPTY_KEY_NAME, JOB_DIRECTORY}
    if name == "control":
        known.add(HOLD_NAME)
    named = [Path(os.path.abspath(answers_path))]
    named += [
        Path(values[key])
        for key in (*KEY_FILES, "ssh.known_hosts_file", "private_terms_file", "about_file")
        if values[key] is not None
    ]
    known |= {item.name for item in named if item.parent == path}
    return frozenset(known | {"." + item + ".tmp" for item in known})


def read_answers(answers_path):
    """Read the answers through one descriptor; under sudo, refuse a file another account owns."""
    if overlaps_repository(answers_path):
        raise SetupError("Keep the answers file outside this repository")
    try:
        with open(answers_path, "rb") as stream:
            info = os.fstat(stream.fileno())
            if os.geteuid() == 0 and info.st_uid not in trusted_owners():
                raise SetupError(
                    f"The answers file belongs to uid {info.st_uid}. Under sudo it must belong to root or "
                    "to the account that ran sudo, because the answers decide what root creates."
                )
            return json.loads(stream.read(READ_LIMIT))
    except PermissionError:
        raise SetupError("No permission to read the answers file; rerun with sudo") from None
    except (OSError, ValueError) as error:
        raise SetupError(f"Cannot read the answers file ({type(error).__name__})") from None


def secure_answers(answers_path, output, directory, owner):
    """Give an answers file kept in the configuration directory mode 0600 and the operator as owner."""
    answers = Path(os.path.abspath(answers_path))
    if answers.parent != output:
        return
    try:
        descriptor = os.open(answers.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except OSError:
        raise SetupError(f"{answers} must be a regular file, not a symbolic link") from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SetupError(f"{answers} must be a regular file, not a symbolic link")
        secure(descriptor, owner, ANSWERS_MODE)
    finally:
        os.close(descriptor)


def rendered_files(values, places):
    """Every file configure writes in the configuration directory, by name, with its mode."""
    files = {
        CONFIG_NAME: render_config(values),
        AI_NAME: render_ai(values),
        ENV_NAME: render_env(values, places),
        JOBS_NAME: render_jobs(values, places),
    }
    files.update(render_units(values, places))
    return files


def hold_content(reason):
    return json.dumps({"held_at": time.time(), "reason": reason}) + "\n"


def held(path):
    """True unless the hold is known to be absent, as in the job runner.

    The control directory may be 0700 and belong to uid, so any error other than a
    missing file, such as EACCES, counts as held.
    """
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def configure(answers_path, output):
    answers = read_answers(answers_path)
    output = Path(check_path("--output", str(Path(os.path.abspath(output)))))
    values = normalize(answers)
    places = directories(values, output)
    created_places, _ = places
    files = rendered_files(values, places)
    config_owner, data_owner = owner_for("config", values), owner_for("data", values)
    changed = []
    with contextlib.ExitStack() as stack:
        handles, new = {}, {}
        for name, path in created_places.items():
            role = "config" if name == "config" else "data"
            handles[name], new[name] = make_directory(
                path, values, role, known_entries(name, path, values, answers_path)
            )
            stack.callback(os.close, handles[name])
        secure_answers(answers_path, output, handles["config"], config_owner)
        writes = [(name, content, config_owner, FILE_MODES[name]) for name, content in files.items()]
        if ssh_used(values):
            # SSH in the container reads its configuration as uid and refuses one another account owns.
            writes.append((SSH_NAME, render_ssh(values), data_owner, SECRET_MODE))
        pending = [
            item
            for item in writes
            if not unchanged(handles["config"], item[0], item[1].encode(), item[2], item[3], output / item[0])
        ]
        hold = created_places["control"] / HOLD_NAME
        hold_created = False
        # Fence the jobs before any of their inputs change.
        if new["control"] or pending:
            hold_created = create_once(
                handles["control"], HOLD_NAME, hold_content("setup configure"), data_owner, hold
            )
        for name, content, owner, mode in pending:
            write_private(handles["config"], name, content, owner, mode, output / name)
            changed.append(str(output / name))
        if values["openai_key_file"] is None and create_once(
            handles["config"], EMPTY_KEY_NAME, "", data_owner, output / EMPTY_KEY_NAME
        ):
            changed.append(str(output / EMPTY_KEY_NAME))
        settings = {
            "answers": values,
            "directories": {name: str(path) for name, path in created_places.items()},
        }
        write_private(
            handles["config"],
            SETTINGS_NAME,
            json.dumps(settings, indent=2) + "\n",
            config_owner,
            FILE_MODES[SETTINGS_NAME],
            output / SETTINGS_NAME,
        )
    warnings = []
    if values["openai_key_file"] is None:
        size = (output / EMPTY_KEY_NAME).stat().st_size
        if size:
            warnings.append(
                f"{output / EMPTY_KEY_NAME} is not empty. Move any key it holds to its own file and name "
                "that file in openai_key_file."
            )
    if os.geteuid() != 0 and (os.getuid(), os.getgid()) != (values["uid"], values["gid"]):
        warnings.append(
            f"The files belong to your account, not {values['uid']}:{values['gid']}. The job runner runs "
            "as your account, so set uid and gid to your own account's numbers."
        )
    if values["uid"] != 1000 or values["gid"] != 1000:
        warnings.append(
            "The image's account is 1000:1000. SSH in the publish job needs an account with the "
            "container's uid, so another uid needs a derived image that adds one."
        )
    if values["ai.enabled"]:
        warnings.append(
            "Paid AI writing is enabled. It bills the OpenAI account behind the key, up to "
            f"{values['ai.daily_budget_usd']:.2f} USD a day."
        )
    if not values["production"]:
        warnings.append("production is false, so the site builds noindex preview pages.")
    if held(hold):
        warnings.append(
            f"The activation hold {hold} exists, so the timers skip their runs. Remove it after check "
            "passes, then run deploy --start."
        )
    return {
        "ok": True,
        "output": str(output),
        "files": [str(output / name) for name in (*files, SETTINGS_NAME)],
        "directories": {name: str(path) for name, path in created_places.items()},
        "activation_hold": str(hold),
        "hold_created": hold_created,
        "changed": changed,
        "warnings": warnings,
    }


def load_settings(output):
    """Open the configuration directory and read setup.json through it.

    Both must be trusted, because deploy renders compose.env and the units from these
    values. Returns the values, the directories, and the open directory descriptor,
    which the caller closes.
    """
    output = Path(check_path("--output", os.path.abspath(output)))
    path = output / SETTINGS_NAME
    check_ancestors(output)
    try:
        directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except PermissionError:
        raise SetupError(
            f"No permission to read {output}. configure ran with sudo for another uid, so rerun this with sudo"
        ) from None
    except OSError as error:
        raise SetupError(f"Cannot open {output} ({type(error).__name__}); run configure first") from None
    try:
        require_trusted(str(output), os.fstat(directory))
        stream = open_trusted(directory, SETTINGS_NAME, path)
        if stream is None:
            raise SetupError(f"Cannot read {path} (FileNotFoundError); run configure first")
        with stream:
            try:
                settings = json.loads(stream.read(READ_LIMIT))
                values = normalize(settings["answers"])
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise SetupError(
                    f"Cannot read {path} ({type(error).__name__}); run configure first"
                ) from None
        places = directories(values, output)
    except BaseException:
        os.close(directory)
        raise
    return values, places, directory


def publisher_python():
    """An interpreter that can import the publisher, or None."""
    if importlib.util.find_spec("wtf_adsb_publisher") is not None:
        return sys.executable
    candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if os.access(candidate, os.X_OK) else None


VALIDATE = """
import json, sys
from wtf_adsb_publisher import public_events
public_events.load_config(sys.argv[1])
from wtf_adsb_publisher import explain
print(json.dumps({"budget_issues": explain.budget_issues()}))
"""


def host_config(values, places):
    """publisher.json with each container path replaced by its host path, for a check on the host."""
    created, others = places
    config = json.loads(render_config(values))
    config.update(
        feed_directory=str(others["feed"]),
        state_dir=str(created["state"]),
        inbox_database=str(created["state"] / "inbox.sqlite3"),
        budget_database=str(created["state"] / "budget.sqlite3"),
        ghost_key_file=values["ghost_key_file"],
        weekly_key_file=openai_key(values, created["config"]),
    )
    for key in ("private_terms_file", "about_file"):
        if values[key] is not None:
            config[key] = values[key]
    if "reference" in others:
        config["reference_dir"] = str(others["reference"])
    return config


def check_configuration(values, places):
    """Let the publisher's own loader validate publisher.json, and its writer the AI budget."""
    output = places[0]["config"]
    result = {"ok": False, "path": str(output / CONFIG_NAME)}
    try:
        json.loads((output / CONFIG_NAME).read_text())
    except (OSError, ValueError) as error:
        result["error"] = type(error).__name__
        return result
    python = publisher_python()
    if python is None:
        result.update(ok=True, publisher_validation="skipped: wtf_adsb_publisher is not installed here")
        return result
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AI_", "OPENAI_", "SITE_", "PRIVATE_TERMS", "WTF_ADSB_"))
    }
    environment.update(
        line.split("=", 1) for line in render_ai(values).splitlines() if line and not line.startswith("#")
    )
    with tempfile.TemporaryDirectory(prefix="wtf-publisher-setup-") as temporary:
        path = Path(temporary) / CONFIG_NAME
        path.write_text(json.dumps(host_config(values, places)))
        try:
            completed = subprocess.run(
                [python, "-I", "-c", VALIDATE, str(path)],
                capture_output=True,
                text=True,
                timeout=120,
                env=environment,
                cwd=temporary,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            result.update(publisher_validation="failed", error=type(error).__name__)
            return result
    if completed.returncode:
        lines = completed.stderr.strip().splitlines()
        result.update(publisher_validation="failed", error=(lines[-1] if lines else "failed")[-300:])
        return result
    try:
        issues = json.loads(completed.stdout.strip().splitlines()[-1])["budget_issues"]
    except (IndexError, KeyError, ValueError):
        issues = ["the budget check printed no result"]
    result.update(ok=not issues, publisher_validation="passed", budget_issues=issues)
    return result


def check_tree(values, places, env):
    """Compare the directories and files with the layout configure writes."""
    created, others = places
    data_owner = (values["uid"], values["gid"])
    report = {"ok": True, "expected_owner": f"{data_owner[0]}:{data_owner[1]}", "problems": []}

    def require(path, kind, mode=None, owner=None, readable=False, private=False):
        """Report any problem with the path; return True when there was none."""
        before = len(report["problems"])
        try:
            info = path.lstat()
        except OSError as error:
            report["problems"].append(f"{path}: {type(error).__name__}")
            return False
        if (
            kind == "dir"
            and not stat.S_ISDIR(info.st_mode)
            or kind == "file"
            and not stat.S_ISREG(info.st_mode)
        ):
            report["problems"].append(f"{path}: not a regular {'directory' if kind == 'dir' else 'file'}")
            return False
        if mode is not None and stat.S_IMODE(info.st_mode) != mode:
            report["problems"].append(f"{path}: mode {stat.S_IMODE(info.st_mode):o}, expected {mode:o}")
        if owner is not None and (info.st_uid, info.st_gid) != owner:
            report["problems"].append(f"{path}: owner {info.st_uid}:{info.st_gid}")
        if readable and info.st_uid != values["uid"] and info.st_gid != values["gid"]:
            report["problems"].append(
                f"{path}: owner {info.st_uid}:{info.st_gid}, so the container account "
                f"{data_owner[0]}:{data_owner[1]} cannot read it"
            )
        if private:
            if info.st_mode & 0o077:
                report["problems"].append(f"{path}: readable by group or others; use chmod 0600")
            elif info.st_uid != data_owner[0]:
                report["problems"].append(f"{path}: owner {info.st_uid}, expected {data_owner[0]}")
        return len(report["problems"]) == before

    config = created["config"]
    expected = rendered_files(values, places)
    require(config, "dir", directory_mode("config"), readable=True)
    for name, mode in FILE_MODES.items():
        if not require(config / name, "file", mode, readable=name == CONFIG_NAME) or name == SETTINGS_NAME:
            continue
        if name == ENV_NAME:
            content = env
        else:
            try:
                content = (config / name).read_bytes()
            except OSError as error:
                report["problems"].append(f"{config / name}: {type(error).__name__}")
                continue
        if content != expected[name].encode():
            report["problems"].append(f"{config / name}: differs from {SETTINGS_NAME}; rerun configure")
    if ssh_used(values) and require(config / SSH_NAME, "file", SECRET_MODE, data_owner):
        if (config / SSH_NAME).read_bytes() != render_ssh(values).encode():
            report["problems"].append(f"{config / SSH_NAME}: differs from {SETTINGS_NAME}; rerun configure")
    for name in ("state", "artifacts", "control"):
        require(created[name], "dir", directory_mode("data"), data_owner)
    for name in others:
        if require(others[name], "dir") and not os.access(others[name], os.R_OK | os.X_OK):
            report["problems"].append(f"{others[name]}: not readable by this account")
    keys = [values["ghost_key_file"], openai_key(values, config)]
    if ssh_used(values):
        keys.append(values["ssh.identity_file"])
    if values["private_terms_file"] is not None:
        keys.append(values["private_terms_file"])
    for key in keys:
        # Metadata only: neither the tool nor this check opens a key file.
        if not os.path.lexists(key):
            report["problems"].append(f"{key}: FileNotFoundError; create the file first")
        else:
            require(Path(key), "file", private=True)
    if ssh_used(values):
        known = Path(values["ssh.known_hosts_file"])
        if require(known, "file") and known.lstat().st_mode & 0o022:
            report["problems"].append(f"{known}: writable by group or others")
    if values["about_file"] is not None:
        about = Path(values["about_file"])
        if require(about, "file"):
            info = about.lstat()
            if not (
                info.st_mode & 0o004
                or (info.st_gid == values["gid"] and info.st_mode & 0o040)
                or (info.st_uid == values["uid"] and info.st_mode & 0o400)
            ):
                report["problems"].append(
                    f"{about}: the container account {data_owner[0]}:{data_owner[1]} cannot read it"
                )
            if info.st_size > ABOUT_LIMIT:
                report["problems"].append(f"{about}: larger than {ABOUT_LIMIT} bytes")
    report["ok"] = not report["problems"]
    return report


def run_quietly(command, timeout=60):
    """Run a command; return (ok, first line of output or an error summary)."""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, type(error).__name__
    if completed.returncode:
        return False, (completed.stderr.strip() or "exit " + str(completed.returncode))[-300:]
    lines = completed.stdout.strip().splitlines()
    return True, lines[0][:120] if lines else ""


def compose_base(docker, output):
    return [docker, "compose", "--project-name", PROJECT, "--env-file", str(Path(output) / ENV_NAME)]


def check_docker(docker, output):
    """Run `docker compose version`, then let Compose validate the generated compose.env."""
    result = {"ok": False}
    ok, line = run_quietly([docker, "compose", "version"])
    if not ok:
        result["error"] = "version: " + line
        return result
    result["version"] = line
    ok, line = run_quietly(
        [*compose_base(docker, output), "-f", str(COMPOSE), "--profile", "jobs", "config", "--quiet"]
    )
    if not ok:
        result["error"] = "config: " + line
        return result
    result["ok"] = True
    return result


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def admin_site_url(origin):
    return origin + "/ghost/api/admin/site/"


def probe_ghost(origin):
    """GET Ghost's public site endpoint once, without a key, and report only what came back."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())
    request = urllib.request.Request(admin_site_url(origin), headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=PROBE_TIMEOUT) as response:
            status = response.status
            body = response.read(1024 * 1024)
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": error.code}
    except (OSError, ValueError, http.client.HTTPException) as error:
        return {"ok": False, "error": type(error).__name__}
    try:
        document = json.loads(body)
        site = document.get("site") if isinstance(document, dict) else None
    except ValueError:
        site = None
    shaped = isinstance(site, dict)
    result = {"ok": status == 200 and shaped, "status": status, "ghost_site": shaped}
    if shaped and isinstance(site.get("version"), str):
        result["version"] = site["version"][:20]
    return result


def probe_ssh(ssh, values, output):
    """Run `true` on the static host with the job's SSH settings and host paths, in batch mode."""
    command = [
        ssh,
        "-F",
        str(Path(output) / SSH_NAME),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "IdentityFile=" + values["ssh.identity_file"],
        "-o",
        "UserKnownHostsFile=" + values["ssh.known_hosts_file"],
        values["ssh.host"],
        "true",
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "error": type(error).__name__}
    result = {"ok": completed.returncode == 0, "returncode": completed.returncode}
    if completed.returncode:
        lines = completed.stderr.strip().splitlines()
        result["error"] = (lines[-1] if lines else "")[-200:]
    return result


def check_feed(feed):
    manifest = Path(feed) / "manifest.json"
    try:
        with open(manifest, "rb") as stream:
            document = json.loads(stream.read(READ_LIMIT * 16))
    except OSError as error:
        return {"ok": False, "path": str(manifest), "error": type(error).__name__}
    except ValueError:
        return {"ok": False, "path": str(manifest), "error": "not JSON"}
    return {"ok": isinstance(document, dict), "path": str(manifest)}


def check_schedule(values):
    analyze = shutil.which("systemd-analyze")
    if analyze is None:
        return {"ok": True, "validation": "skipped: systemd-analyze is not installed"}
    problems = []
    for key in TIMERS.values():
        ok, line = run_quietly([analyze, "calendar", values[key]], timeout=20)
        if not ok:
            problems.append(f"{key}: {line}")
    return {"ok": not problems, "validation": "passed" if not problems else "failed", "problems": problems}


def check_account(values):
    """uid must be the operator's account, which runs the timers and reads the key files and locks."""
    operator = sudo_uid() if os.geteuid() == 0 else os.geteuid()
    if operator is None:
        return {"ok": True, "uid": values["uid"], "validation": "skipped: run as root without sudo"}
    if operator == values["uid"]:
        return {"ok": True, "uid": values["uid"]}
    return {
        "ok": False,
        "uid": values["uid"],
        "operator": operator,
        "error": (
            f"uid is {values['uid']}, but the timers run as uid {operator}, which must read the key files "
            "and the state directory's locks. Set uid and gid to that account's numbers and rerun configure."
        ),
    }


def check(output, docker, ssh="ssh"):
    values, places, directory = load_settings(output)
    created, others = places
    output = created["config"]
    try:
        env = read_trusted(directory, ENV_NAME, output / ENV_NAME)
    finally:
        os.close(directory)
    checks = {
        "account": check_account(values),
        "configuration": check_configuration(values, places),
        "directories": check_tree(values, places, env),
        "feed": check_feed(others["feed"]),
        "schedule": check_schedule(values),
        "docker_compose": check_docker(docker, output),
        "ghost": probe_ghost(values["ghost_origin"]),
    }
    if ssh_used(values):
        checks["ssh"] = probe_ssh(ssh, values, output)
    warnings = []
    hold = created["control"] / HOLD_NAME
    hold_present = held(hold)
    if hold_present:
        warnings.append(
            f"The activation hold {hold} exists, so the timers skip their runs. Remove it when the user "
            "is ready, then run deploy --start."
        )
    if values["uid"] != 1000 or values["gid"] != 1000:
        warnings.append(
            "The image's account is 1000:1000; another uid needs a derived image with that account."
        )
    if values["production"] and values["site_origin"] == EXAMPLE_ORIGIN:
        warnings.append("production is true but site_origin is the example address.")
    return {
        "ok": all(item["ok"] for item in checks.values()),
        "activation_hold": hold_present,
        "checks": checks,
        "warnings": warnings,
    }


def unit_directory():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def deploy_commands(values, places, docker, systemctl):
    created, _ = places
    output = created["config"]
    compose = [*compose_base(docker, output), "-f", str(COMPOSE)]
    available_from = values["live_since"][:10]
    return {
        "initialize": [
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
            "--available-from",
            available_from,
        ],
        "daemon_reload": [systemctl, "--user", "daemon-reload"],
        "enable_reconcile": [systemctl, "--user", "enable", RECONCILE],
        "status": [sys.executable, str(RUNNER), "--config", str(output / JOBS_NAME), "status"],
        "start_timers": [systemctl, "--user", "enable", "--now", *TIMERS],
    }


LOCKS = ("writer/status.json.lock", "weekly/status.json.lock", "ledger.json.lock", "budget.ai.lock")


def create_locks(state):
    """Create the shared zero-byte lock files of a new state directory; never replace one."""
    for name in LOCKS:
        path = Path(state) / name
        path.parent.mkdir(mode=0o700, exist_ok=True)
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        except FileExistsError:
            pass


def install_units(units, directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    changed = []
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name, text in units.items():
            if write_private(descriptor, name, text, None, 0o644, directory / name):
                changed.append(str(directory / name))
    finally:
        os.close(descriptor)
    return changed


def set_deploy_hold(control):
    """Create the activation hold through the control directory's descriptor; return whether it is new."""
    hold = control / HOLD_NAME
    try:
        descriptor = os.open(control, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise SetupError(
            f"Cannot open {control} to set the activation hold ({type(error).__name__}), so deploy will "
            "not change compose.env or jobs.json; rerun configure"
        ) from None
    try:
        return create_once(descriptor, HOLD_NAME, hold_content("setup deploy"), None, hold)
    except OSError as error:
        raise SetupError(
            f"Cannot create {hold} ({type(error).__name__}), so deploy will not change compose.env or "
            "jobs.json; rerun configure"
        ) from None
    finally:
        os.close(descriptor)


def deploy(output, docker, systemctl, print_only, start, ssh="ssh"):
    """Install the user units, initialize new state, enable the reconciler, and report status.

    deploy never trusts the files on disk: it writes compose.env and jobs.json again from the
    validated setup.json and installs units rendered from it. When either file would change,
    it first creates the activation hold, as configure does, so no job starts with the new
    inputs before the user lifts it. With `start`, it enables the three timers only after the
    user has removed the activation hold and check passes.
    """
    if os.geteuid() == 0 and not print_only:
        raise SetupError("deploy installs user units for your account; run it without sudo")
    values, places, directory = load_settings(output)
    created, _ = places
    output = created["config"]
    units = render_units(values, places)
    changed, hold_created = [], False
    try:
        read_trusted(directory, ENV_NAME, output / ENV_NAME)
        if not print_only:
            pending = [
                (name, content)
                for name, content in (
                    (ENV_NAME, render_env(values, places)),
                    (JOBS_NAME, render_jobs(values, places)),
                )
                if not unchanged(directory, name, content.encode(), None, FILE_MODES[name], output / name)
            ]
            # Fence the jobs before any of their inputs change.
            if pending:
                hold_created = set_deploy_hold(created["control"])
            for name, content in pending:
                write_private(directory, name, content, None, FILE_MODES[name], output / name)
                changed.append(str(output / name))
    finally:
        os.close(directory)
    commands = deploy_commands(values, places, docker, systemctl)
    target = unit_directory()
    hold = created["control"] / HOLD_NAME
    fresh = not (created["state"] / "ledger.json").exists()
    if print_only:
        planned = {key: value for key, value in commands.items() if key != "start_timers" or start}
        if not fresh:
            planned.pop("initialize")
        return {"ok": True, "units": [str(target / name) for name in units], "commands": planned}
    result = {
        "ok": False,
        "changed": changed,
        "hold_created": hold_created,
        "units_changed": install_units(units, target),
    }
    if fresh:
        ok, line = run_quietly(commands["initialize"], timeout=900)
        if not ok:
            return {**result, "step": "initialize", "error": line}
        create_locks(created["state"])
        result["initialized"] = True
    for step in ("daemon_reload", "enable_reconcile"):
        ok, line = run_quietly(commands[step], timeout=120)
        if not ok:
            return {**result, "step": step, "error": line}
    try:
        completed = subprocess.run(commands["status"], capture_output=True, text=True, timeout=180)
        status = json.loads(completed.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, IndexError, ValueError):
        return {**result, "step": "status", "error": "the job runner printed no status"}
    result["status"] = status
    warnings = ['Run sudo loginctl enable-linger "$USER" once, so the timers keep running after you log out.']
    if start:
        if held(hold):
            return {
                **result,
                "step": "start",
                "error": f"Remove {hold} first; the timers stay off until then.",
            }
        checked = check(str(output), docker, ssh)
        if not checked["ok"]:
            return {
                **result,
                "step": "check",
                "error": "check failed; run check for details",
                "check": checked,
            }
        ok, line = run_quietly(commands["start_timers"], timeout=120)
        if not ok:
            return {**result, "step": "start_timers", "error": line}
        result["timers"] = list(TIMERS)
    elif held(hold):
        warnings.append(f"The activation hold {hold} exists. Remove it, then run deploy --start.")
    else:
        warnings.append("The timers are not enabled yet. Run deploy --start when the user confirms.")
    return {**result, "ok": True, "warnings": warnings}


def main(argv=None):
    parser = JsonParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True, parser_class=JsonParser)
    commands.add_parser("plan", help="Print the interview questions")
    configure_parser = commands.add_parser("configure", help="Write the configuration, units, and hold")
    configure_parser.add_argument("--answers", required=True)
    configure_parser.add_argument("--output", required=True)
    check_parser = commands.add_parser("check", help="Validate files, directories, Docker, Ghost, and SSH")
    check_parser.add_argument("--output", required=True)
    deploy_parser = commands.add_parser("deploy", help="Install the units and report the job status")
    deploy_parser.add_argument("--output", required=True)
    deploy_parser.add_argument("--print-only", action="store_true")
    deploy_parser.add_argument("--start", action="store_true", help="Enable the timers once the hold is gone")
    for sub in (check_parser, deploy_parser):
        sub.add_argument("--docker", default="docker", help=argparse.SUPPRESS)
        sub.add_argument("--ssh", default="ssh", help=argparse.SUPPRESS)
    deploy_parser.add_argument("--systemctl", default="systemctl", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan()
        elif args.command == "configure":
            result = configure(args.answers, args.output)
        elif args.command == "check":
            result = check(args.output, args.docker, args.ssh)
        else:
            result = deploy(args.output, args.docker, args.systemctl, args.print_only, args.start, args.ssh)
    except SetupError as error:
        result = {"ok": False, "error": str(error)}
    except Exception as error:
        result = {"ok": False, "error": type(error).__name__}
    emit(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
