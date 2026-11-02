#!/usr/bin/env python3
"""Manage a private approval ledger and build public/review snapshots offline."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import public_events
from wtf_adsb_publisher.curation import DailyLimitError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger", type=Path, help="Private ledger (default .private/public-events/ledger.json)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init", help="Create a private, empty ledger; refuses overwrite")
    initialize.add_argument("--available-from", required=True)
    put = commands.add_parser("put", help="Create/revise an episode from an evidence_packet JSON file")
    put.add_argument("--episode-key", required=True, help="Stable private grouping key, never exported")
    put.add_argument("--evidence", type=Path, required=True)
    put.add_argument("--title", required=True)
    put.add_argument("--summary", required=True)
    put.add_argument("--article-path", required=True)
    put.add_argument("--ghost-id", help="Private Ghost post ID; required before a production export")
    put.add_argument("--primary-type", choices=public_events.TYPE_LABELS)
    put.add_argument("--label", action="append", default=[])
    put.add_argument("--media", type=Path, help='Optional JSON object: {"src":"/images/...","alt":"..."}')
    put.add_argument("--synthetic", action="store_true")
    put.add_argument("--at")
    for name in ("approve", "withdraw"):
        review = commands.add_parser(name)
        review.add_argument("event_id")
        review.add_argument("--at")
    commands.add_parser("list", help="List IDs, states, and revision numbers without evidence")
    export = commands.add_parser("export", help="Write one atomic snapshot; never publishes to Ghost")
    export.add_argument("--mode", choices=("published", "review"), required=True)
    export.add_argument("--as-of")
    export.add_argument("--inventory", type=Path, help="Fresh, complete Ghost publication inventory JSON")
    export.add_argument("--output", type=Path, required=True)
    migrate = commands.add_parser(
        "migrate-evidence",
        help="Rebind weekly research, map excerpts, and approvals after a public evidence change. Pause the "
        "publisher, pass the publisher's ledger with --ledger, review the report, then rerun with --apply "
        "before the next publisher run.",
    )
    migrate.add_argument("--apply", action="store_true", help="Write the ledger; otherwise only report")
    validate = commands.add_parser("validate-snapshot", help="Validate a public snapshot before frontend use")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.ledger is None:
        if args.command == "migrate-evidence":
            parser.error("migrate-evidence needs --ledger pointing at the publisher's ledger")
        args.ledger = ROOT / ".private" / "public-events" / "ledger.json"
    try:
        if args.command == "validate-snapshot":
            operate(args)
        else:
            with public_events.ledger_lock(args.ledger):
                operate(args)
    except DailyLimitError:
        parser.exit(
            1,
            "Select at most nine ordinary events per UTC observation date; supported exceptional events may exceed that limit.\n",
        )
    except (ValueError, TypeError, KeyError, OSError) as error:
        # Inputs can contain private evidence: never print payloads or file contents.
        # A saved record from before a change in evidence wording names its repair, and only that.
        hint = (
            f" After a change in public evidence, {public_events.BINDING_REPAIR}."
            if public_events.BINDING_REPAIR in str(error)
            else ""
        )
        parser.exit(
            1,
            f"Public event operation failed ({type(error).__name__}); check input structure and paths.{hint}\n",
        )


def operate(args):
    if args.command == "validate-snapshot":
        if args.output.resolve() == args.ledger.resolve():
            raise ValueError("Snapshot output cannot replace the private ledger")
        snapshot = public_events.validate_snapshot(public_events.read_json(args.input))
        public_events.write_json_atomic(args.output, snapshot, private=snapshot["mode"] == "review")
        print(json.dumps({"snapshot_id": snapshot["snapshot_id"], "events": len(snapshot["events"])}))
        return
    if args.command == "init":
        if args.ledger.exists():
            raise ValueError("Ledger already exists")
        ledger = public_events.new_ledger(args.available_from)
        public_events.write_json_atomic(args.ledger, ledger, private=True)
        print("Created empty private ledger")
        return
    ledger = public_events.read_json(args.ledger)
    if args.command == "put":
        editorial = {
            "title": args.title,
            "summary": args.summary,
            "article_path": args.article_path,
            "ghost_id": args.ghost_id,
            "primary_type": args.primary_type,
            "secondary_labels": args.label,
            "synthetic": args.synthetic,
        }
        if args.media:
            editorial["media"] = public_events.read_json(args.media)
        event_id = public_events.put_event(
            ledger, args.episode_key, public_events.read_json(args.evidence), editorial, at=args.at
        )
        public_events.write_json_atomic(args.ledger, ledger, private=True)
        print(json.dumps({"id": event_id, "state": "pending"}))
    elif args.command in {"approve", "withdraw"}:
        state = "approved" if args.command == "approve" else "withdrawn"
        public_events.set_review_state(ledger, args.event_id, state, at=args.at)
        public_events.write_json_atomic(args.ledger, ledger, private=True)
        print(json.dumps({"id": args.event_id, "state": state}))
    elif args.command == "migrate-evidence":
        report = public_events.migrate_evidence_bindings(ledger)
        if args.apply:
            public_events.write_json_atomic(args.ledger, ledger, private=True)
        print(json.dumps({**report, "applied": args.apply}, indent=2))
    elif args.command == "list":
        print(
            json.dumps(
                [{key: row[key] for key in ("id", "state", "revision")} for row in ledger["records"]],
                indent=2,
            )
        )
    else:
        if args.output.resolve() == args.ledger.resolve():
            raise ValueError("Snapshot output cannot replace the private ledger")
        inventory = public_events.read_json(args.inventory) if args.inventory else None
        snapshot = public_events.export_snapshot(
            ledger, mode=args.mode, as_of=args.as_of, inventory=inventory
        )
        public_events.write_json_atomic(args.output, snapshot, private=args.mode == "review")
        print(
            json.dumps(
                {
                    "mode": snapshot["mode"],
                    "events": len(snapshot["events"]),
                    "snapshot_id": snapshot["snapshot_id"],
                }
            )
        )


if __name__ == "__main__":
    main()
