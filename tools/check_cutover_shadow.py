#!/usr/bin/env python3
"""Record a private production comparison and native-shadow health checkpoint.

This read-only production observer never activates writers or changes services.
Its only external request is the existing read-only Ghost inventory export.
"""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import compare_cutover as comparison
import export_ghost_inventory

from wtf_adsb_publisher import cutover
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher.feed_adapter import feed_fresh
from wtf_adsb_publisher.ghost_client import GhostClient

# Directories the digest skips: dependencies, caches, private and runtime state, build output,
# and the notes agent sessions and the sync tool write on every run, which change no artifact.
IGNORED_DIRECTORIES = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".venv",
        ".cache",
        ".private",
        "dist",
        "artifacts",
        ".git",
        "state",
        ".ruff_cache",
        ".pytest_cache",
        ".mypy_cache",
        ".hypothesis",
        "htmlcov",
        "build",
        ".claude",
        ".remember",
        ".superpowers",
        ".sync",
    }
)
IGNORED_DIRECTORY_SUFFIXES = (".egg-info",)
# Compiled files, logs, and the merges the sync tool stages beside a module for review.
IGNORED_SUFFIXES = frozenset({".pyc", ".log", ".sync-merge"})


def _ignored_directory(name):
    return name in IGNORED_DIRECTORIES or name.endswith(IGNORED_DIRECTORY_SUFFIXES)


def artifact_digest(roots):
    digest = hashlib.sha256()
    for root in map(Path, roots):
        for directory, folders, names in root.walk():
            folders[:] = sorted(name for name in folders if not _ignored_directory(name))
            for name in sorted(names):
                path = directory / name
                if path.is_symlink() or path.suffix in IGNORED_SUFFIXES:
                    continue
                relative = path.relative_to(root)
                digest.update(root.name.encode() + b"/" + relative.as_posix().encode() + b"\0")
                digest.update(bytes.fromhex(cutover.digest_file(path)))
    return digest.hexdigest()


class CommandFailure(RuntimeError):
    """Bounded command diagnostics stay in the private checkpoint directory."""

    def __init__(self, result):
        super().__init__("A checkpoint command failed")
        self.details = {
            "returncode": getattr(result, "returncode", None),
            "timeout": getattr(result, "timeout", None),
        }
        for key in ("stdout", "stderr"):
            value = getattr(result, key, None) or ""
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            self.details[key] = value[-65536:]
            self.details[key + "_truncated"] = len(value) > 65536


def command_json(command, *, timeout=120):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise CommandFailure(error) from error
    if result.returncode:
        raise CommandFailure(result)
    return json.loads(result.stdout)


def delivered_readiness(container):
    """Allow only demonstrated delivery progress through a bounded commit/delivery gap."""
    command = [
        "docker",
        "exec",
        container,
        "wtf-adsb-engine",
        "health",
        "--config",
        "/config/receiver.toml",
        "--readiness",
    ]
    previous = None
    probes = []
    deadline = time.monotonic() + 30
    for _ in range(16):
        failure = None
        try:
            state = command_json(command, timeout=min(10, max(0.1, deadline - time.monotonic())))
        except CommandFailure as error:
            failure = error
            try:
                state = json.loads(error.details["stdout"])
            except (ValueError, TypeError) as invalid:
                raise error from invalid
        health = state.get("health", {})
        outputs = state.get("outputs", [])
        identity = [state.get("stream_id"), state.get("generation")]
        cursors = {item["name"]: item["cursor"] for item in outputs}
        fresh = all(
            type(health.get(key)) in (int, float) and 0 <= time.time() - health[key] <= 300
            for key in ("last_receiver_poll", "analysis_watermark")
        )
        valid = (
            fresh
            and health.get("ready")
            and health.get("complete")
            and health.get("mode") == "live"
            and not state.get("synthetic")
            and all(identity)
            and bool(outputs)
            and all(
                item["status"] == "ready" and item["cursor"] <= state["head_sequence"] for item in outputs
            )
        )
        progress = previous is None or (
            identity == previous["identity"]
            and cursors.keys() == previous["cursors"].keys()
            and all(cursors[key] >= previous["cursors"][key] for key in cursors)
            and any(cursors[key] > previous["cursors"][key] for key in cursors)
        )
        if valid and progress and state.get("ready") and failure is None and time.monotonic() <= deadline:
            return state, probes
        if failure is None:
            failure = CommandFailure(subprocess.CompletedProcess(command, 1, json.dumps(state), ""))
        probes.append({"at": time.time(), **failure.details})
        failure.details["probes"] = list(probes)
        if not valid or not progress or state.get("ready") or time.monotonic() >= deadline:
            raise failure
        previous = {"identity": identity, "cursors": cursors}
        time.sleep(2)
    raise failure


def configuration_digest(settings):
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True, allow_nan=False).encode())
    for name in sorted(settings["configuration_files"]):
        path = Path(name)
        digest.update(str(path).encode() + b"\0" + bytes.fromhex(cutover.digest_file(path)))
    return digest.hexdigest()


def native_health(settings):
    container = settings["shadow_container"]
    details = command_json(["docker", "inspect", container])[0]
    if details["Image"] != settings["engine_image_id"]:
        raise ValueError("Shadow image changed")
    if details["State"].get("OOMKilled") or details["State"].get("Health", {}).get("Status") != "healthy":
        raise ValueError("Native shadow container is not healthy")
    try:
        state, probes = delivered_readiness(container)
    except CommandFailure as error:
        error.details["runtime"] = {
            "container_id": details["Id"],
            "image_id": details["Image"],
            "started_at": details["State"]["StartedAt"],
            "at": time.time(),
        }
        raise
    feed = comparison.image_ready_feed(
        settings["publisher_image_id"], settings["shadow_feed"], settings["shadow_inbox"]
    )
    if (
        any(feed[key] != state[key] for key in ("stream_id", "generation"))
        or feed["cursor"] < state["head_sequence"]
    ):
        raise ValueError("Shadow feed does not match the ready native engine head")
    if not feed_fresh(feed, time.time(), 300):
        raise ValueError("Shadow feed expired while its inbox caught up")
    usage = command_json(["docker", "stats", "--no-stream", "--format", "{{json .}}", container])
    return {
        "health": state["health"],
        "head_sequence": state["head_sequence"],
        "outputs": state["outputs"],
        "feed": feed,
        "resources": usage,
        "image_id": details["Image"],
        "restarts": details["RestartCount"],
        "runtime_identity": details["Id"] + ":" + details["State"]["StartedAt"],
        "readiness_probes": probes,
    }


def checkpoint(settings):
    directory = Path(settings["evidence_directory"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.time()
    work = directory / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(stamp))
    work.mkdir(mode=0o700)
    roots = [settings[key] for key in ("legacy_root", "engine_root", "publisher_root")]
    artifact_hash = artifact_digest(roots)
    result = {"at": stamp, "artifacts_sha256": artifact_hash, "passed": False}
    try:
        result["configuration_sha256"] = configuration_digest(settings)
        if artifact_hash != settings["approved_artifacts_sha256"]:
            raise ValueError("Artifacts differ from the approved checked build")
        if shutil.disk_usage(directory).free < settings.get("minimum_free_bytes", 20 * 1024**3):
            raise ValueError("Insufficient free space for another retained-history checkpoint")
        result["image_ids"] = {
            role: comparison.inspect_image(settings[role + "_image_id"]) for role in ("engine", "publisher")
        }
        result["native"] = native_health(settings)
        result["runtime_identity"] = result["native"]["runtime_identity"]
        config = public.read_json(settings["production_config"])
        client = GhostClient(config["ghost_origin"], Path(config["ghost_key_file"]).read_text())
        inventory = work / "inventory.json"
        public.write_json_atomic(inventory, export_ghost_inventory.export(client), private=True)
        journal = work / "journal.sqlite3"
        engine = Path(settings["engine_root"])
        backup = command_json(
            [
                str(engine / ".venv/bin/python"),
                "-c",
                "import json,sys,time; from wtf_adsb_engine.maintenance import backup_journal; "
                "r=backup_journal(sys.argv[1],sys.argv[2]); r['at']=time.time(); print(json.dumps(r))",
                settings["production_journal"],
                str(journal),
            ]
        )
        public.write_json_atomic(work / "journal-receipt.json", backup, private=True)
        arguments = SimpleNamespace(
            legacy=Path(settings["legacy_root"]),
            engine_image=settings["engine_image_id"],
            publisher_image=settings["publisher_image_id"],
            config=Path(settings["production_config"]),
            journal=journal,
            inventory=inventory,
            work=work / "comparison",
            at=public.epoch_utc(backup["at"]),
        )
        with (work / "comparison.log").open("w") as log:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("compare_cutover.py")),
                    "compare",
                    *[
                        part
                        for key, value in vars(arguments).items()
                        for part in ("--" + key.replace("_", "-"), str(value))
                    ],
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=1500,
            )
        if completed.returncode:
            raise ValueError("Retained-history comparison failed; inspect its private report")
        result["comparison"] = public.read_json(arguments.work / "result.json")
        result["native_after"] = native_health(settings)
        if result["native_after"]["runtime_identity"] != result["runtime_identity"]:
            raise ValueError("Native shadow restarted during the checkpoint")
        result["feed_identity"] = [result["native"]["feed"][key] for key in ("stream_id", "generation")]
        if result["feed_identity"] != [
            result["native_after"]["feed"][key] for key in ("stream_id", "generation")
        ]:
            raise ValueError("Shadow stream changed during the checkpoint")
        if artifact_digest(roots) != artifact_hash:
            raise ValueError("Artifacts changed during the checkpoint")
        if configuration_digest(settings) != result["configuration_sha256"]:
            raise ValueError("Configuration changed during the checkpoint")
        result["passed"] = True
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        result["error"] = type(error).__name__
        if isinstance(error, CommandFailure):
            public.write_json_atomic(work / "command-failure.json", error.details, private=True)
        # Detailed failure reports stay private; do not forward provider exceptions.
    result["completed_at"] = time.time()
    public.write_json_atomic(work / "checkpoint.json", result, private=True)
    samples = [public.read_json(path) for path in sorted(directory.glob("*/checkpoint.json"))]
    status = {
        "phase": "shadow",
        "production": "legacy",
        "latest": str(work / "checkpoint.json"),
        "latest_passed": result["passed"],
        "checkpoints": len(samples),
        "gate_passed": cutover.shadow_gate(samples),
        "updated_at": time.time(),
    }
    public.write_json_atomic(directory / "status.json", status, private=True)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--print-artifact-hash", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    settings = public.read_json(args.config)
    if args.print_artifact_hash:
        print(artifact_digest([settings[key] for key in ("legacy_root", "engine_root", "publisher_root")]))
        return 0
    directory = Path(settings["evidence_directory"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "observer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = checkpoint(settings)
    print(json.dumps(result))
    return 0 if result["latest_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
