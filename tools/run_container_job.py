#!/usr/bin/env python3
"""Run and reconcile the three publisher jobs without depending on its Python environment.

The host keeps the schedule and durable launch intent. Docker owns the detached
process; losing this wrapper never makes an existing job safe to repeat.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

ROLES = {"publish": 20 * 60, "write": 25 * 60, "weekly": 45 * 60}
LABEL = "io.wtf-adsb.publisher."
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
LOG_LIMIT = 65536


class JobError(RuntimeError):
    pass


class Busy(JobError):
    pass


def read_json(path):
    with Path(path).open() as stream:
        return json.load(stream)


def sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".job-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def locked(path, *, create=True, wait=False):
    path = Path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = path.open("a" if create else "r")
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as error:
            raise Busy("A publisher worker still holds a lock") from error
        yield


def held(path):
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def load_config(path):
    config = read_json(path)
    required = {
        "version",
        "project",
        "compose_file",
        "env_file",
        "job_directory",
        "activation_hold",
        "publisher_state_directory",
        "budget_database",
        "configuration_files",
    }
    if set(config) != required or config["version"] != 1:
        raise JobError("Unsupported container job configuration")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", config["project"]):
        raise JobError("Invalid Compose project name")
    paths = [config[key] for key in required - {"version", "project", "configuration_files"}]
    if not isinstance(config["configuration_files"], list) or not config["configuration_files"]:
        raise JobError("Pin every runtime configuration and credential input")
    if any(
        not isinstance(name, str) or not Path(name).is_absolute()
        for name in paths + config["configuration_files"]
    ):
        raise JobError("Container job paths must be absolute")
    if (
        not Path(config["budget_database"])
        .resolve()
        .is_relative_to(Path(config["publisher_state_directory"]).resolve())
    ):
        raise JobError("The shared spending store must be inside publisher state")
    return config


class Docker:
    def __init__(self, config):
        self.config = config
        self.compose = [
            "docker",
            "compose",
            "--project-name",
            config["project"],
            "--env-file",
            config["env_file"],
            "--file",
            config["compose_file"],
        ]

    @staticmethod
    def command(arguments):
        result = subprocess.run(arguments, capture_output=True, timeout=120)
        if result.returncode:
            # Docker diagnostics can repeat environment or credential paths.
            raise JobError("Container command failed; existing launch intent is retained")
        return result.stdout.decode("utf-8")

    def configuration(self):
        return json.loads(self.command([*self.compose, "--profile", "jobs", "config", "--format", "json"]))

    def containers(self):
        identifiers = self.command(
            [
                "docker",
                "ps",
                "--all",
                "--quiet",
                "--no-trunc",
                "--filter",
                "label=com.docker.compose.project=" + self.config["project"],
            ]
        ).split()
        return json.loads(self.command(["docker", "inspect", *identifiers])) if identifiers else []

    def create(self, role, intent):
        labels = [
            part
            for key in ("role", "run_id", "image", "configuration_sha256")
            for part in ("--label", LABEL + key + "=" + intent[key])
        ]
        return self.command(
            [
                *self.compose,
                "run",
                "--detach",
                "--no-deps",
                "--pull",
                "never",
                "--name",
                intent["name"],
                *labels,
                role,
            ]
        ).strip()

    def start(self, identifier):
        self.command(["docker", "start", identifier])

    def remove(self, identifier):
        # No force: Docker itself refuses to remove a running container.
        self.command(["docker", "rm", identifier])

    def logs(self, identifier):
        # Stream into an anonymous private file, retaining only its bounded tail
        # in memory and in the durable receipt. Docker's own log is also capped.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                ["docker", "logs", "--timestamps", "--tail", "200", identifier],
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=120,
            )
            if result.returncode:
                raise JobError("Cannot retain job logs; stopped container preserved")
            size = output.tell()
            output.seek(max(0, size - LOG_LIMIT))
            return {"text": output.read().decode("utf-8", errors="replace"), "truncated": size > LOG_LIMIT}

    def follow_up(self):
        unit = "wtf-adsb-autopilot.service"
        status = self.command(
            ["systemctl", "--user", "show", "--property=ActiveState", "--property=Job", unit]
        )
        properties = dict(line.split("=", 1) for line in status.splitlines() if "=" in line)
        if properties.get("ActiveState") not in {"inactive", "failed"} or properties.get("Job") not in {
            "",
            "0",
        }:
            return False
        self.command(["systemctl", "--user", "start", "--no-block", unit])
        return True


class Runner:
    def __init__(self, config, *, docker=None, clock=time.time, sleep=time.sleep):
        self.config = config
        self.directory = Path(config["job_directory"])
        self.docker = docker or Docker(config)
        self.clock, self.sleep = clock, sleep

    def intent_path(self, role):
        return self.directory / "active" / (role + ".json")

    def receipt_path(self, intent):
        return self.directory / "receipts" / (intent["run_id"] + ".json")

    def follow_up_path(self, intent):
        return self.directory / "pending-followups" / (intent["run_id"] + ".json")

    def name(self, role):
        return self.config["project"] + "-" + role

    def application_locks(self):
        state = Path(self.config["publisher_state_directory"])
        return {
            "writer": state / "writer/status.json.lock",
            "weekly": state / "weekly/status.json.lock",
            "ledger": state / "ledger.json.lock",
            "ai": Path(self.config["budget_database"]).with_suffix(".ai.lock"),
        }

    def signature(self):
        composition = self.docker.configuration()
        images = set()
        for role in ROLES:
            service = composition["services"][role]
            image = service["image"]
            if not IMAGE.fullmatch(image) or service.get("pull_policy") != "never":
                raise JobError("Jobs require a local immutable image ID with pulling disabled")
            if service.get("restart") != "no" or service.get("read_only") is not True:
                raise JobError("Jobs must have a read-only root and no automatic restart")
            mounts = {value["target"]: value for value in service["volumes"]}
            if (
                Path(mounts["/state"]["source"]).resolve()
                != Path(self.config["publisher_state_directory"]).resolve()
                or mounts["/state"].get("read_only", False)
                or Path(mounts["/control"]["source"]).resolve() / "activation.hold"
                != Path(self.config["activation_hold"]).resolve()
                or not mounts["/control"].get("read_only")
                or not mounts["/feed"].get("read_only")
            ):
                raise JobError("Job state, feed or activation hold mounts disagree with the runner")
            images.add(image)
        if len(images) != 1:
            raise JobError("Publisher jobs must use the same image")
        digest = hashlib.sha256(
            json.dumps({"runner": self.config, "compose": composition}, sort_keys=True).encode()
        )
        for name in sorted(
            set([self.config["compose_file"], self.config["env_file"], *self.config["configuration_files"]])
        ):
            with Path(name).open("rb") as stream:
                digest.update(name.encode() + b"\0" + hashlib.file_digest(stream, "sha256").digest())
        return images.pop(), digest.hexdigest()

    def matching(self, role, intent, containers):
        candidates = [
            item
            for item in containers
            if item["Name"].lstrip("/") == self.name(role)
            or item["Config"].get("Labels", {}).get(LABEL + "role") == role
        ]
        if len(candidates) > 1 or (candidates and intent is None):
            raise JobError("Untracked or duplicate job containers require reconciliation")
        if not candidates:
            return None
        container = candidates[0]
        labels = container["Config"].get("Labels", {})
        if (
            container["Name"].lstrip("/") != intent["name"]
            or container["Image"] != intent["image"]
            or any(
                labels.get(LABEL + key) != intent[key]
                for key in ("role", "run_id", "image", "configuration_sha256")
            )
            or intent.get("container_id", container["Id"]) != container["Id"]
        ):
            raise JobError("Container identity does not match durable launch intent")
        return container

    def follow_up(self):
        # A just-queued wrapper may arrive before its sender finishes fsyncing.
        # Wait only for this short bookkeeping section, never for a worker.
        with locked(self.directory / "follow-up.lock", wait=True):
            for marker in sorted((self.directory / "pending-followups").glob("*.json")):
                path = self.receipt_path({"run_id": marker.stem})
                receipt = read_json(path)
                if (
                    receipt.get("run_id") != marker.stem
                    or receipt.get("role") != "weekly"
                    or receipt.get("phase") != "completed"
                    or receipt.get("exit_code") != 0
                ):
                    raise JobError("Pending follow-up does not match a successful weekly receipt")
                if receipt.get("follow_up_pending"):
                    with locked(self.directory / "launch.lock", wait=True):
                        if held(self.config["activation_hold"]) or held(self.intent_path("publish")):
                            return
                        if not self.docker.follow_up():
                            return
                        receipt["follow_up_pending"] = False
                        receipt["follow_up_queued_at"] = self.clock()
                        private_json(path, receipt)
                marker.unlink()
                sync_directory(marker.parent)

    @staticmethod
    def never_started(container):
        state = container["State"]
        return (
            state["Status"] == "created"
            and not state["Running"]
            and not state.get("Pid", 0)
            and state.get("StartedAt") in {None, "", "0001-01-01T00:00:00Z"}
        )

    def finish(self, intent, container, *, cancel=False):
        if container is not None and "container_id" not in intent:
            intent = {**intent, "container_id": container["Id"]}
            private_json(self.intent_path(intent["role"]), intent)
        path = self.receipt_path(intent)
        if path.exists():
            receipt = read_json(path)
            if receipt["run_id"] != intent["run_id"] or receipt["container_id"] != intent.get("container_id"):
                raise JobError("Completion receipt does not match launch intent")
        else:
            state = container["State"]
            if cancel:
                if not self.never_started(container):
                    raise JobError("Only a proven never-started container can be cancelled")
            elif state["Status"] != "exited" or state["Running"] or state.get("Pid", 0):
                raise JobError("Only a confirmed stopped job may be completed")
            receipt = {
                **intent,
                "phase": "cancelled_before_start" if cancel else "completed",
                "completed_at": self.clock(),
                "exit_code": 125 if cancel else state["ExitCode"],
                "oom_killed": state.get("OOMKilled", False),
                "started_at": state.get("StartedAt"),
                "finished_at": state.get("FinishedAt"),
                "logs": {"text": "", "truncated": False} if cancel else self.docker.logs(container["Id"]),
                "follow_up_pending": not cancel and intent["role"] == "weekly" and state["ExitCode"] == 0,
            }
            private_json(path, receipt)
        if container is not None:
            cancelled = receipt["phase"] == "cancelled_before_start" and self.never_started(container)
            if not cancelled and (container["State"]["Status"] != "exited" or container["State"]["Running"]):
                raise JobError("A completed job container unexpectedly became active")
        # Keep the active intent until the small pending index is durable. If
        # this write is interrupted, finish() recreates it from the receipt.
        if receipt.get("follow_up_pending"):
            private_json(self.follow_up_path(receipt), {"run_id": receipt["run_id"]})
        if container is not None:
            self.docker.remove(container["Id"])
        self.intent_path(intent["role"]).unlink()
        sync_directory(self.intent_path(intent["role"]).parent)
        return receipt["exit_code"]

    def run_role(self, role, *, reconcile_only=False):
        with locked(self.directory / (role + ".lock")):
            path = self.intent_path(role)
            intent = read_json(path) if path.exists() else None
            container = self.matching(role, intent, self.docker.containers())
            if intent is None:
                if reconcile_only:
                    return None
                with locked(self.directory / "launch.lock", wait=True):
                    if held(self.config["activation_hold"]):
                        raise Busy("New publisher launches are fenced")
                    image, signature = self.signature()
                    intent = {
                        "version": 1,
                        "phase": "prepared",
                        "role": role,
                        "name": self.name(role),
                        "project": self.config["project"],
                        "run_id": str(uuid.uuid4()),
                        "image": image,
                        "configuration_sha256": signature,
                        "created_at": self.clock(),
                    }
                    if self.signature() != (image, signature):
                        raise JobError("Job configuration changed during launch preparation")
                    if held(self.config["activation_hold"]):
                        raise Busy("New publisher launches are fenced")
                    private_json(path, intent)
                    self.docker.create(role, intent)
                container = self.matching(role, intent, self.docker.containers())
            if intent.get("project") != self.config["project"] or intent.get("role") != role:
                raise JobError("Launch intent belongs to another publisher job")
            if self.receipt_path(intent).exists():
                return self.finish(intent, container)
            if container is None:
                raise JobError("Job container disappeared without a completion receipt; outcome is unknown")
            intent["container_id"] = container["Id"]
            intent["phase"] = "container_created"
            private_json(path, intent)
            while True:
                state = container["State"]
                if state["Status"] == "exited":
                    return self.finish(intent, container)
                if state["Status"] == "created":
                    if not self.never_started(container):
                        raise JobError("Prepared container has inconsistent start history")
                    if reconcile_only:
                        return None
                    if held(self.config["activation_hold"]):
                        raise Busy("Starting the prepared publisher job is fenced")
                    with locked(self.directory / "launch.lock", wait=True):
                        if held(self.config["activation_hold"]):
                            raise Busy("Starting the prepared publisher job is fenced")
                        if self.signature() != (intent["image"], intent["configuration_sha256"]):
                            raise JobError("Prepared job configuration changed; it must not start")
                        self.docker.start(container["Id"])
                elif state["Status"] != "running" or not state["Running"]:
                    raise JobError("Job container is in an unresolved lifecycle state")
                if self.clock() - intent["created_at"] > ROLES[role] and not intent.get("overdue_at"):
                    intent["overdue_at"] = self.clock()
                    private_json(path, intent)
                    print(
                        f"Publisher {role} job {intent['run_id']} is overdue; still waiting",
                        file=sys.stderr,
                        flush=True,
                    )
                if reconcile_only:
                    return None
                self.sleep(5)
                container = self.matching(role, intent, self.docker.containers())
                if container is None:
                    raise JobError("Running job disappeared; its outcome remains unknown")

    def reconcile(self, *, exclude=None):
        """Inspect each existing role once; never start work or wait behind another role."""
        failed = False
        for role in ROLES:
            if role != exclude:
                try:
                    failed |= bool(self.run_role(role, reconcile_only=True))
                except Busy:
                    continue  # The normal service already owns this role.
        self.follow_up()
        return int(failed)

    def status(self, *, check_locks=True):
        containers = self.docker.containers()
        locks = {}
        if check_locks:
            paths = {"host_" + role: self.directory / (role + ".lock") for role in ROLES}
            paths.update(self.application_locks())
            for name, path in paths.items():
                try:
                    with locked(path, create=False):
                        locks[name] = "available"
                except Busy:
                    locks[name] = "busy"
                except FileNotFoundError:
                    locks[name] = "missing"
        return {
            "fenced": held(self.config["activation_hold"]),
            "containers": [
                {
                    "id": row["Id"],
                    "name": row["Name"].lstrip("/"),
                    "role": row["Config"].get("Labels", {}).get(LABEL + "role"),
                    "status": row["State"]["Status"],
                    "running": row["State"]["Running"],
                }
                for row in containers
            ],
            "pending_roles": [role for role in ROLES if self.intent_path(role).exists()],
            "locks": locks,
        }

    def drain(self):
        """Fence first; never stop workers. Success proves current containers and locks are idle."""
        with locked(self.directory / "launch.lock", wait=True):
            hold = Path(self.config["activation_hold"])
            if not held(hold):
                private_json(hold, {"held_at": self.clock(), "reason": "publisher drain"})
        self.signature()  # Verify that the inspected directory is what every container mounts.
        state = Path(self.config["publisher_state_directory"])
        if not isinstance(read_json(state / "ledger.json").get("records"), list):
            raise JobError("Publisher ledger is not initialized")
        with closing(
            sqlite3.connect(Path(self.config["budget_database"]).as_uri() + "?mode=ro", uri=True)
        ) as database:
            tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"meta", "ai_runs"} <= tables:
                raise JobError("Publisher spending store is not initialized")
        # Reconcile only stopped jobs; a drain must not block behind or kill a paid request.
        with ExitStack() as stack:
            for role in ROLES:
                stack.enter_context(locked(self.directory / (role + ".lock")))
            containers = self.docker.containers()
            if any(
                row["State"]["Status"] not in {"exited", "created"} or row["State"]["Running"]
                for row in containers
            ):
                raise Busy("Publisher containers are still active; the launch fence is retained")
            for role in ROLES:
                path = self.intent_path(role)
                intent = read_json(path) if path.exists() else None
                container = self.matching(role, intent, containers)
                if intent:
                    if container is None and not self.receipt_path(intent).exists():
                        raise JobError("A job outcome is unknown; state transfer is blocked")
                    self.finish(
                        intent, container, cancel=container is not None and self.never_started(container)
                    )
            known = {self.name(role) for role in ROLES}
            if any(row["Name"].lstrip("/") not in known for row in self.docker.containers()):
                raise JobError("Untracked publisher containers block state transfer")
            for path in self.application_locks().values():
                if not path.is_file():
                    raise JobError("Expected publisher locks are missing; verify state setup before transfer")
                stack.enter_context(locked(path, create=False))
            return {
                **self.status(check_locks=False),
                "drained": True,
                "locks": dict.fromkeys(
                    [*("host_" + role for role in ROLES), *self.application_locks()], "held_by_drain"
                ),
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run").add_argument("role", choices=ROLES)
    for name in ("reconcile", "status", "drain"):
        commands.add_parser(name)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        runner = Runner(load_config(args.config))
        if args.command == "status":
            print(json.dumps(runner.status()))
            return 0
        if args.command == "drain":
            print(json.dumps(runner.drain()))
            return 0
        if args.command == "run":
            runner.reconcile(exclude=args.role)
            result = runner.run_role(args.role)
            runner.follow_up()
            return result or 0
        return runner.reconcile()
    except Busy as error:
        print(str(error), file=sys.stderr)
        return 75
    except (
        JobError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as error:
        print(f"Container job held ({type(error).__name__}); inspect private job state", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
