"""Private inbox administration and migration commands."""

import argparse
import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path

from . import budget_store, plugins, public_events


def state_manifest(directory):
    """Hash every regular file in a quiescent private state tree."""
    directory = Path(directory)
    result = {}
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Private state must be a real directory")
    for path in sorted(directory.rglob("*")):
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError("Private state migration rejects symbolic links and special files")
        entry = {"mode": stat.S_IMODE(info.st_mode), "directory": path.is_dir()}
        if not entry["directory"]:
            with path.open("rb") as stream:
                entry.update(size=info.st_size, sha256=hashlib.file_digest(stream, "sha256").hexdigest())
        result[path.relative_to(directory).as_posix()] = entry
    return result


def copy_state(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or destination.is_symlink():
        raise ValueError("Private state migration does not follow symbolic links")
    source, destination = source.resolve(), destination.resolve()
    if destination.exists():
        raise ValueError("The migration destination must not exist")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("The migration destination cannot be inside its source")
    ledger = public_events.read_json(source / "ledger.json")
    public_events._validate_ledger(ledger)
    original = state_manifest(source)
    shutil.copytree(source, destination, symlinks=True)
    if state_manifest(destination) != original or state_manifest(source) != original:
        raise ValueError(
            "Source state changed during migration; keep writers stopped and use a new destination"
        )
    destination.chmod(0o700)
    for path in destination.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    expected = {
        name: {**entry, "mode": 0o700 if entry["directory"] else 0o600} for name, entry in original.items()
    }
    if state_manifest(destination) != expected:
        raise ValueError("Copied state verification failed; preserve this destination for diagnosis")
    # The receipt is durable only after every copied file and directory is too.
    for path in [
        *sorted(destination.rglob("*"), key=lambda value: len(value.parts), reverse=True),
        destination,
    ]:
        descriptor = os.open(path, os.O_RDONLY | (os.O_DIRECTORY if path.is_dir() else 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    public_events.write_json_atomic(
        destination / "migration-receipts" / (str(uuid.uuid4()) + ".json"),
        {
            "version": 1,
            "captured_at": public_events.stamp(),
            "files": expected,
        },
        private=True,
    )
    return {"records": len(ledger["records"]), "copied": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plugins", help="List installed plugins without importing their code")
    initialize = commands.add_parser("init", help="Create an empty private publisher ledger and AI store")
    initialize.add_argument("--state-dir", type=Path, required=True)
    initialize.add_argument("--available-from", required=True)
    ingest = commands.add_parser("ingest", help="Validate and durably import an engine export")
    ingest.add_argument("--feed", type=Path, required=True)
    ingest.add_argument("--database", type=Path, required=True)
    ingest.add_argument("--allow-synthetic", action="store_true", help="Offline fixture imports only")
    ingest.add_argument(
        "--extension-catalog",
        type=Path,
        help="Reviewed JSON array of namespace, schema_version, absolute path and sha256 pins",
    )
    status = commands.add_parser("status", help="Report inbox sequence and record counts")
    status.add_argument("--database", type=Path, required=True)
    budget = commands.add_parser("migrate-budget", help="Copy reservations after old paid writers stop")
    budget.add_argument("--source", type=Path, required=True)
    budget.add_argument("--destination", type=Path, required=True)
    state = commands.add_parser(
        "migrate-state", help="Copy stopped publisher state to a new private directory"
    )
    state.add_argument("--source", type=Path, required=True)
    state.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.command == "plugins":
        result = {role: sorted(plugins.discover(role)) for role in plugins.DEFAULTS}
    elif args.command == "init":
        args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = args.state_dir / "ledger.json"
        if path.exists():
            raise ValueError("A publisher ledger already exists")
        public_events.write_json_atomic(path, public_events.new_ledger(args.available_from), private=True)
        budget_store.initialize(args.state_dir / "budget.sqlite3")
        result = {"initialized": True}
    elif args.command == "migrate-budget":
        result = budget_store.migrate(args.source, args.destination)
    elif args.command == "migrate-state":
        result = copy_state(args.source, args.destination)
    elif args.command == "ingest":
        from .ingest import ingest_directory

        entries = public_events.read_json(args.extension_catalog) if args.extension_catalog else []
        approved = plugins.approved_extensions({"extension_schemas": entries})
        result = ingest_directory(
            args.feed, args.database, allow_synthetic=args.allow_synthetic, approved_extensions=approved
        )
    else:
        from .ingest import read_state

        current = read_state(args.database)
        result = {key: current[key] for key in ("stream_id", "cursor", "health")} | {
            "observations": len(current["observations"]),
            "retractions": len(current["retractions"]),
        }
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
