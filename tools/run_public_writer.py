#!/usr/bin/env python3
"""Replace template story prose with validated AI prose; the normal publisher releases it.

A timer pass upgrades released stories within the shared daily AI budget. The
production redo runs one receipts-only backfill before the first publication,
with a one-time spending limit, so each new post starts with AI prose.
--pause-ai and --resume-ai turn the publisher's AI kill switch on and off: every
AI writer that shares the publisher budget database honours it.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from run_autopilot import privacy_guard

from wtf_adsb_publisher import explain, public_writer, weekly_research
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher.budget_store import connect


def seed(config, old_ledger, *, apply=False):
    """Type profiles from an old ledger's published weekly dossiers; the old ledger is only read."""
    state = Path(config["state_dir"])
    seeded = weekly_research.seed_profiles(public.read_json(old_ledger), public.now_utc())
    current = weekly_research.load_profiles(state)
    added = sorted(key for key in seeded if key not in current)
    if apply and added:
        weekly_research.save_profiles(state, {**current, **{key: seeded[key] for key in added}})
    return {
        "applied": bool(apply),
        "seeded": len(seeded),
        "added": added,
        "kept": sorted(key for key in seeded if key in current),
        "facts": sum(len(seeded[key]["facts"]) for key in added),
    }


def switch(config, on):
    """Turn the publisher's AI kill switch on or off."""
    with connect(config["budget_database"]) as db:
        explain.set_paused(db, on)
        return {"ai_paused": explain.paused(db)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--backfill", type=int, metavar="N", help="Write up to N stories in this run")
    parser.add_argument(
        "--max-spend",
        type=float,
        metavar="USD",
        help=(
            f"One-time budget for this run, at most ${explain.MAX_ONE_TIME_SPEND:g} across all runs (kept in the "
            "publisher budget database); it replaces "
            "the daily cap, and its spending counts toward the UTC day, so other AI writing waits until midnight"
        ),
    )
    parser.add_argument(
        "--receipts-only",
        action="store_true",
        help="Write AI prose into unreleased records before their first publication",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Build and check requests; send and save nothing"
    )
    parser.add_argument("--no-research", action="store_true", help="Do not research missing type profiles")
    parser.add_argument(
        "--seed-profiles",
        type=Path,
        metavar="OLD_LEDGER",
        help="Report type profiles from an old ledger's weekly dossiers; --apply saves new ones",
    )
    parser.add_argument("--apply", action="store_true", help="With --seed-profiles, save the new profiles")
    switches = parser.add_mutually_exclusive_group()
    switches.add_argument(
        "--pause-ai", action="store_true", help="Turn the publisher's AI kill switch on for every writer"
    )
    switches.add_argument("--resume-ai", action="store_true", help="Turn the publisher's AI kill switch off")
    args = parser.parse_args()
    os.umask(0o077)
    if args.backfill is not None and not 1 <= args.backfill <= 1000:
        parser.error("--backfill takes 1 to 1000 stories")
    if args.max_spend is not None:
        try:
            explain.RunBudget(args.max_spend)
        except ValueError as error:
            parser.error(f"--max-spend: {error}")
    if args.apply and not args.seed_profiles:
        parser.error("--apply only saves profiles found with --seed-profiles")
    try:
        config = public.load_config(args.config)
        if args.pause_ai or args.resume_ai:
            print(json.dumps(switch(config, args.pause_ai)))
            return
        if args.seed_profiles:
            print(json.dumps(seed(config, args.seed_profiles, apply=args.apply)))
            return
        result = public_writer.run(
            config,
            receipts_only=args.receipts_only,
            limit=args.backfill or public_writer.MAX_PER_PASS,
            max_spend=args.max_spend,
            dry_run=args.dry_run,
            research=not args.no_research,
            check_private=privacy_guard(config),
            # A timer pass ends before the service's time limit and backs off after failures;
            # an explicit backfill runs to the end.
            deadline=None if args.backfill else time.monotonic() + public_writer.PASS_SECONDS,
            unattended=not args.backfill,
        )
        print(json.dumps(result))
        if not args.dry_run and result["failed"]:
            raise SystemExit(1)
    except explain.BudgetError as error:
        # The settings, not a secret, so the message is safe to show.
        parser.exit(1, f"Story writing stopped: {error}; published stories are unchanged.\n")
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(1, f"Story writing stopped ({type(error).__name__}); published stories are unchanged.\n")


if __name__ == "__main__":
    main()
