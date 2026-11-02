#!/usr/bin/env python3
"""Prepare weekly nominations or attach completed research; never publish or spend AI credits."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher import weekly_spotlight as weekly


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    nominate = commands.add_parser("nominate")
    nominate.add_argument("--curation", type=Path, required=True)
    nominate.add_argument("--as-of")
    nominate.add_argument("--output", type=Path, required=True)
    attach = commands.add_parser("attach")
    attach.add_argument("--ledger", type=Path, required=True)
    attach.add_argument("--event-id", required=True)
    attach.add_argument("--research", type=Path, required=True)
    attach.add_argument("--finding", type=Path, required=True, help="Original private source finding JSON")
    attach.add_argument("--at")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "nominate":
            output = args.output.resolve()
            if not output.is_relative_to(ROOT / ".private") or output.exists():
                raise ValueError("Choose a new result beneath this project's .private directory")
            result = weekly.nominate(public.read_json(args.curation), args.as_of or public.now_utc())
            public.write_json_atomic(output, result, private=True)
            print(json.dumps({"week_start": result["week_start"], "candidates": result["candidate_count"]}))
        else:
            with public.ledger_lock(args.ledger):
                ledger = public.read_json(args.ledger)
                public._validate_ledger(ledger)
                record = next(row for row in ledger["records"] if row["id"] == args.event_id)
                research = public.read_json(args.research)
                event = public._projection(record["evidence"], record["editorial"])
                research = weekly.validate_research(research, event, as_of=args.at or public.now_utc())
                editorial = {
                    **record["editorial"],
                    "weekly_spotlight": research,
                    "weekly_source": public.read_json(args.finding),
                }
                public.put_event(ledger, record["episode_key"], record["evidence"], editorial, at=args.at)
                public.write_json_atomic(args.ledger, ledger, private=True)
            print(json.dumps({"id": args.event_id, "state": "pending"}))
    except (ValueError, TypeError, KeyError, OSError, StopIteration) as error:
        parser.exit(1, f"Weekly preparation failed ({type(error).__name__}); check the private inputs.\n")


if __name__ == "__main__":
    main()
