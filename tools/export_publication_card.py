#!/usr/bin/env python3
"""Write one validated event as a Ghost HTML card; no network or publication."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import public_events, publication


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--format", choices=("html", "ghost-draft", "ghost-research-update"), default="html")
    parser.add_argument(
        "--post", type=Path, help="Current Ghost post JSON, including Lexical content and tags"
    )
    parser.add_argument("--prepared-at", help="UTC preparation time for a ghost-draft audit bundle")
    parser.add_argument("--ledger", type=Path, help="Private event ledger binding an existing Ghost post")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.output.resolve() in {
            path.resolve() for path in (args.snapshot, args.ledger, args.post) if path
        }:
            raise ValueError("Card output cannot replace any input")
        snapshot = public_events.read_json(args.snapshot)
        if args.format == "ghost-research-update":
            if not args.post or not args.ledger or args.prepared_at:
                raise ValueError("Research updates need a current post and no preparation-date override")
            ledger = public_events.read_json(args.ledger)
            public_events._validate_ledger(ledger)
            record = next(row for row in ledger["records"] if row["id"] == args.event_id)
            content = (
                json.dumps(
                    publication.research_update_request(
                        snapshot,
                        args.event_id,
                        public_events.read_json(args.post),
                        expected_ghost_id=record["editorial"].get("ghost_id"),
                    ),
                    indent=2,
                )
                + "\n"
            )
        elif args.format == "ghost-draft":
            content = (
                json.dumps(
                    publication.draft_request(snapshot, args.event_id, prepared_at=args.prepared_at), indent=2
                )
                + "\n"
            )
        else:
            if args.prepared_at:
                raise ValueError("Preparation time belongs to a Ghost draft bundle")
            content = publication.event_card(snapshot, args.event_id)
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        public_events.write_bytes_atomic(
            args.output,
            content.encode("utf-8"),
            private=snapshot["mode"] == "review" or args.format.startswith("ghost-"),
        )
    except (ValueError, TypeError, KeyError, OSError, StopIteration) as error:
        parser.exit(
            1, f"Publication card export failed ({type(error).__name__}); check the snapshot and paths.\n"
        )
    print(
        "Wrote "
        + (
            "a private Ghost request bundle"
            if args.format.startswith("ghost-")
            else "an observation HTML card"
        )
        + "; nothing was published"
    )


if __name__ == "__main__":
    main()
