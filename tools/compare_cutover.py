#!/usr/bin/env python3
"""Compare the old publisher with frozen application images, entirely offline.

The project and state copies must be private. This command never contacts Ghost,
AI providers or SSH. Export the Ghost inventory separately through its read-only
inventory command. Run each checkpoint against a SQLite backup of the journal.
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if len(sys.argv) > 1 and sys.argv[1] == "simulate" and "--checkout" in sys.argv:
    candidate = Path(sys.argv[sys.argv.index("--checkout") + 1]) / "src"
    if (candidate / "wtf_adsb_publisher").is_dir():
        sys.path.insert(0, str(candidate))

from wtf_adsb_publisher import cutover
from wtf_adsb_publisher import public_events as public


def copy_media(cache_source, destination):
    """Copy only retained media and caches into a private rehearsal directory."""
    cache_source, destination = Path(cache_source), Path(destination)
    for name in ("basemap-cache", "social-cache"):
        cache = cache_source / "artifacts/ghost-theme" / name
        if cache.exists():
            shutil.copytree(cache, destination / "artifacts/ghost-theme" / name)
    media = cache_source / "artifacts/ghost-theme/autopilot/media"
    if media.exists():
        shutil.copytree(media, destination / "artifacts/ghost-theme/comparison/media")


def checkout_copy(source, destination, *, cache_source=None):
    """Copy code; share only immutable installed tools with the source checkout."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    destination.mkdir(mode=0o700)
    for name in ("src", "tools", "themes", "contracts"):
        if (source / name).exists():
            shutil.copytree(
                source / name,
                destination / name,
                ignore=shutil.ignore_patterns("node_modules", "__pycache__", ".cache", ".venv", "*.pyc"),
            )
    for name in ("package.json", "package-lock.json", "pyproject.toml", "uv.lock"):
        if (source / name).is_file():
            shutil.copyfile(source / name, destination / name)
    for name in ("node_modules", "tools/ghost-theme/node_modules", ".venv"):
        if (source / name).exists():
            (destination / name).symlink_to((source / name).resolve(), target_is_directory=True)
    copy_media(cache_source or source, destination)


def inspect_image(image):
    """Reject mutable tags and unsupported images before capturing production state."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("Comparison requires an immutable local image ID")
    result = subprocess.run(
        ["docker", "image", "inspect", image], check=True, capture_output=True, text=True, timeout=30
    )
    details = json.loads(result.stdout)[0]
    if details["Id"] != image or (details["Os"], details["Architecture"]) != ("linux", "amd64"):
        raise ValueError("Comparison requires the recorded Linux amd64 image")
    return image


def image_python(image, arguments, *, mounts, timeout=1200, environment=(), memory="4g"):
    """Run a disposable offline test process with production filesystem restrictions.

    Only these explicitly offline rehearsal containers may be force-removed on
    interruption. The production job runner retains containers and receipts.
    """
    name = "wtf-adsb-comparison-" + uuid.uuid4().hex
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--label",
        "com.wtf-adsb.purpose=offline-comparison",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--init",
        "--memory",
        memory,
        "--shm-size=256m",
        "--tmpfs",
        "/tmp:size=512m,mode=1777",
        "--entrypoint",
        "python",
    ]
    for source, destination, readonly in mounts:
        command.extend(
            (
                "--mount",
                f"type=bind,src={Path(source).resolve()},dst={destination}"
                + (",readonly" if readonly else ""),
            )
        )
    for value in environment:
        command.extend(("--env", value))
    try:
        return subprocess.run([*command, image, *map(str, arguments)], check=True, timeout=timeout)
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=30)


def image_ready_feed(image, directory, inbox, *, at=None):
    """Import with the installed publisher, mounting only its inbox and the feed."""
    inbox = Path(inbox).resolve()
    if inbox.name != "inbox.sqlite3":
        raise ValueError("Image comparison inbox must be named inbox.sqlite3 in a dedicated directory")
    inbox.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    image_python(
        image,
        [
            "-c",
            "import json,sys; from wtf_adsb_publisher import cutover,public_events as p; "
            "p.write_json_atomic('/inbox/readiness.json', "
            "cutover.ready_feed('/feed','/inbox/inbox.sqlite3',at=json.loads(sys.argv[1])),private=True)",
            json.dumps(at),
        ],
        mounts=[(directory, "/feed", True), (inbox.parent, "/inbox", False)],
    )
    return public.read_json(inbox.parent / "readiness.json")


def simulate(checkout, config_path, inventory, output, at):
    """One process per implementation prevents mixing legacy module imports."""
    checkout = Path(checkout).resolve()
    sys.path.insert(0, str(checkout / "tools"))
    spec = importlib.util.spec_from_file_location("cutover_autopilot", checkout / "tools/run_autopilot.py")
    module = importlib.util.module_from_spec(spec)
    # Freeze imports too: PhotoCache binds its default clock at class definition.
    with patch("time.time", return_value=public._instant(at).timestamp()):
        spec.loader.exec_module(module)
    config = public.load_config(config_path)
    original = module.publication_pass
    run = subprocess.run
    captured = {}

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        client = args[3]()
        captured.update(ledger=args[1], writes=client.writes, posts=client.inventory())
        return result

    def save_process_failure(result):
        error = result.stderr or ""
        if isinstance(error, bytes):
            error = error.decode("utf-8", errors="replace")
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        public.write_json_atomic(
            directory / "subprocess-failure.json",
            {
                "returncode": getattr(result, "returncode", None),
                "timeout": getattr(result, "timeout", None),
                "stderr": error[-65536:],
                "stderr_truncated": len(error) > 65536,
            },
            private=True,
        )

    def capture_process(*args, **kwargs):
        try:
            result = run(*args, **kwargs)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            save_process_failure(error)
            raise
        if result.returncode:
            save_process_failure(result)
        return result

    before = cutover.digest_file(Path(config["state_dir"]) / "ledger.json")
    with (
        patch.object(module, "publication_pass", capture),
        patch("subprocess.run", side_effect=capture_process),
        patch("time.time", return_value=public._instant(at).timestamp()),
        patch(
            "socket.socket.connect",
            side_effect=AssertionError("Offline comparison attempted a network request"),
        ),
    ):
        report = module.dry_run(config, inventory, output, at=at, fetch_basemaps=False)
    if cutover.digest_file(Path(config["state_dir"]) / "ledger.json") != before:
        raise ValueError("Dry run modified its source ledger")
    captured.update(report=report, snapshot=public.read_json(Path(output) / "snapshot.json"))
    captured["existing_post_ids"] = sorted(item["id"] for item in public.read_json(inventory)["posts"])
    captured["media"] = public.read_json(Path(output) / "media/manifest.json")["events"]
    public.write_json_atomic(Path(output) / "comparison.json", captured, private=True)
    return {"articles": len(captured["snapshot"]["events"]), "writes": len(captured["writes"])}


def isolated_command(command, *, work, denied=()):
    """Kernel-enforced offline process tree, writable only in its private work area."""
    wrapper = [
        "bwrap",
        "--die-with-parent",
        "--unshare-net",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev-bind",
        "/dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(work),
        str(work),
    ]
    for path in denied:
        path = Path(path).resolve()
        if path.is_dir():
            wrapper.extend(("--tmpfs", str(path)))
        elif path.exists():
            wrapper.extend(("--ro-bind", "/dev/null", str(path)))
    return [*wrapper, *command]


def normalize_sources(engine_image, baseline, result_paths, *, reference_directory):
    """Use the engine only in the offline harness, never inside the publisher."""
    code = """import json, sys
from pathlib import Path
from wtf_adsb_engine.analysis.export import project_source, media_parts
baseline = {r["id"]: r for r in json.loads(Path(sys.argv[1]).read_text())["records"]}
results = [json.loads(Path(name).read_text()) for name in sys.argv[2:]]
changed = {
    record["id"] for result in results for record in result["ledger"]["records"]
    if record["id"] not in baseline
    or record.get("automation", {}).get("source") != baseline[record["id"]].get("automation", {}).get("source")
}
for name, result in zip(sys.argv[2:], results, strict=True):
    path = Path(name)
    sources = {}
    media = {}
    for record in result["ledger"]["records"]:
        source = record.get("automation", {}).get("source")
        if record["id"] in changed and source:
            projected = project_source(source, finding_identifier="comparison")
            if projected is None:
                raise ValueError("Cannot normalize an unsupported retained finding")
            sources[record["id"]] = projected
            media[record["id"]] = media_parts(projected)
    result["normalized_sources"] = sources
    result["normalized_media"] = media
    path.write_text(json.dumps(result, allow_nan=False))
"""
    work = baseline.parent.parent
    image_python(
        engine_image,
        ["-c", code, baseline, *result_paths],
        mounts=[(work, work, False), (reference_directory, "/references", True)],
        environment=["WTF_ADSB_REFERENCE_DIR=/references"],
        timeout=120,
        memory="2g",
    )


def compare_frozen(args, config, baseline, inbox, feed):
    """Compare an already captured projection; each simulation gets fresh copied state."""
    work = args.work.resolve()
    result_paths = []
    timings = {}
    for label in ("legacy", "publisher"):
        started = time.monotonic()
        checkout = work / label
        if label == "legacy":
            checkout_copy(args.legacy, checkout)
        else:
            checkout.mkdir(mode=0o700)
            copy_media(args.legacy, checkout)
        state = checkout / ".private/state"
        state.parent.mkdir(mode=0o700)
        shutil.copytree(baseline, state)
        settings = {**config, "state_dir": str(state), "database": str(args.journal.resolve())}
        for key in ("ghost_key_file", "weekly_key_file"):
            settings.pop(key, None)
        if label == "publisher":
            settings.pop("database", None)
            # Inside the image the reference files are mounted at /references;
            # the live reference_dir names a host path.
            settings.update(
                state_dir="/fixture/state",
                inbox_database="/inbox/inbox.sqlite3",
                feed_directory="/feed",
                budget_database="/fixture/state/budget.sqlite3",
                reference_dir="/references",
            )
        else:
            # The legacy application reads its own bundled references.
            settings.pop("reference_dir", None)
        private_config = checkout / ".private/config.json"
        public.write_json_atomic(private_config, settings, private=True)
        output = checkout / "artifacts/ghost-theme/comparison"
        if label == "publisher":
            shutil.copyfile(args.inventory, checkout / ".private/inventory.json")
            image_python(
                args.publisher_image,
                [
                    "/app/tools/compare_cutover.py",
                    "simulate",
                    "--checkout",
                    "/app",
                    "--config",
                    "/fixture/config.json",
                    "--inventory",
                    "/fixture/inventory.json",
                    "--output",
                    "/app/artifacts/ghost-theme/comparison",
                    "--at",
                    args.at,
                ],
                mounts=[
                    (checkout / ".private", "/fixture", True),
                    (inbox.parent, "/inbox", True),
                    (work / "feed", "/feed", True),
                    (checkout / "artifacts/ghost-theme", "/app/artifacts/ghost-theme", False),
                    # The image lacks the operator-supplied reference files the legacy run reads.
                    (args.legacy / "src/discoveries/data", "/references", True),
                ],
                environment=["WTF_ADSB_REFERENCE_DIR=/references"],
            )
        else:
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "simulate",
                "--checkout",
                str(checkout),
                "--config",
                str(private_config),
                "--inventory",
                str(args.inventory.resolve()),
                "--output",
                str(output),
                "--at",
                args.at,
            ]
            subprocess.run(isolated_command(command, work=checkout), check=True, timeout=1200)
        result_paths.append(output / "comparison.json")
        timings[label] = round(time.monotonic() - started, 3)
    normalize_sources(
        args.engine_image,
        baseline / "ledger.json",
        result_paths,
        reference_directory=args.legacy / "src/discoveries/data",
    )
    result = cutover.compare_runs(
        public.read_json(baseline / "ledger.json"), *(public.read_json(path) for path in result_paths)
    )
    result.update(
        at=public._instant(args.at).timestamp(),
        feed=feed,
        duration_seconds=timings,
        inventory_sha256=cutover.digest_file(args.inventory),
        journal_sha256=cutover.digest_file(args.journal),
        engine_image_id=args.engine_image,
        publisher_image_id=args.publisher_image,
    )
    public.write_json_atomic(work / "result.json", result, private=True)
    return result


def compare(args):
    inspect_image(args.engine_image)
    inspect_image(args.publisher_image)
    work = args.work.resolve()
    work.mkdir(mode=0o700)
    config = public.read_json(args.config)
    baseline = work / "baseline"
    cutover.capture_state(config["state_dir"], baseline)
    projection = work / "engine.toml"
    projection.write_text(f"""version=1
state_directory={json.dumps(str(work / "engine-state"))}
source_instance="cutover-comparison"
[[pipelines]]
name="journal"
input={{plugin="wtf_adsb.journal", options={{path="/journal/journal.sqlite3",mode="live"}}}}
detectors=[{{plugin="wtf_adsb.collection"}}]
[[outputs]]
name="publisher"
plugin="wtf_adsb.files"
options={{directory={json.dumps(str(work / "feed"))}}}
""")
    project_code = """import sys
from unittest.mock import patch
from wtf_adsb_engine.runtime import Runtime
with patch("time.time", return_value=float(sys.argv[2])):
    runtime=Runtime(sys.argv[1])
    with runtime.locked():
        result=runtime.once()
    if result["errors"] or any(v != "ready" for v in result["outputs"].values()):
        raise SystemExit("Projection failed")
"""
    at = public._instant(args.at).timestamp()
    image_python(
        args.engine_image,
        ["-c", project_code, projection, at],
        mounts=[
            (work, work, False),
            (args.journal, "/journal/journal.sqlite3", True),
            (args.legacy / "src/discoveries/data", "/references", True),
        ],
        environment=["WTF_ADSB_REFERENCE_DIR=/references"],
        memory="2g",
    )
    inbox = work / "inbox/inbox.sqlite3"
    feed = image_ready_feed(args.publisher_image, work / "feed", inbox, at=at)
    return compare_frozen(args, config, baseline, inbox, feed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    worker = commands.add_parser("simulate")
    for name in ("checkout", "config", "inventory", "output"):
        worker.add_argument("--" + name, type=Path, required=True)
    worker.add_argument("--at", required=True)
    comparison = commands.add_parser("compare")
    for name in ("legacy", "config", "journal", "inventory", "work"):
        comparison.add_argument("--" + name, type=Path, required=True)
    for name in ("engine-image", "publisher-image"):
        comparison.add_argument("--" + name, required=True)
    comparison.add_argument("--at", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == "simulate":
        result = simulate(args.checkout, args.config, args.inventory, args.output, args.at)
    else:
        result = compare(args)
    print(json.dumps(result, allow_nan=False))
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
