#!/usr/bin/env python3
"""List where reading the stored exception decision changes a reader's answer (read only).

Each live, non-frozen record of the ledger with a saved finding is read the old way, the
exception rules rerun on the saved finding without the descent context and the saved
`automation.exceptional` boolean, and the new way, autopilot.exception_standing(), which
also reads the stored decision's `exceptional` when the boolean is missing. The readers
compared are the publisher's ordering and priority (`exception_standing`) and a prior
story's entry (`_story`, which curation's pinned path also reads). The sticky form cannot
differ (both readers reduce to the rerun), and readers of release facts do not read the
stored decision.

A difference under an expected reason, a stored repeated descent or aircraft cap the rerun
would promote, is the stored decision working as intended; any other difference is a
behaviour change, and the tool then exits with status 1.
"""

import argparse
import json
import sys
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import autopilot
from wtf_adsb_publisher import public_events as public

# Differences the audit expects: a stored demotion the rerun would promote.
EXPECTED_REASONS = frozenset({"repeated_descent", "aircraft_cap"})


def _old_standing(record, event):
    """The reader before the stored decision: the rerun and the saved boolean only."""
    stored = (record.get("automation") or {}).get("exceptional")
    return autopilot._canonical(record, event) and stored is not False


def _old_entry(record, event):
    receipt = (record.get("automation") or {}).get("published_event")
    return autopilot._published_exception(record, receipt) if receipt else _old_standing(record, event)


def _audited(ledger):
    """The live, non-frozen records with a saved finding."""
    return [
        record
        for record in ledger.get("records", [])
        if (record.get("automation") or {}).get("source")
        and record["automation"].get("source_kind") != "frozen"
        and not autopilot._removed(record)
    ]


def exception_audit(ledger):
    """The number of audited records and the reader differences among them."""
    quiet = autopilot._quiet
    records = _audited(ledger)
    differences = []
    for record in records:
        automatic = record["automation"]
        event = autopilot._project(record)
        readers = []
        if event is not None:
            readers.append(
                (
                    "exception_standing",
                    partial(_old_standing, record, event),
                    partial(autopilot.exception_standing, record, event),
                )
            )
        prior = autopilot._prior_event(record, event)
        if prior is not None and autopilot._prior(record):
            readers.append(
                (
                    "_story",
                    partial(_old_entry, record, prior),
                    partial(autopilot._entry_exception, record, prior),
                )
            )
        block = autopilot._exception_block(automatic)
        reason = block["reason"] if block else None
        for name, before, after in readers:
            old, new = bool(quiet(before, default=False)), bool(quiet(after, default=False))
            if old is new:
                continue
            differences.append(
                {
                    "record_id": record.get("id"),
                    "episode_key": record.get("episode_key"),
                    "reader": name,
                    "old": old,
                    "new": new,
                    "reason": reason,
                    "expected": old and not new and reason in EXPECTED_REASONS,
                }
            )
    return len(records), differences


def report(ledger):
    records, differences = exception_audit(ledger)
    expected = sum(item["expected"] for item in differences)
    return {
        "records": records,
        "expected": expected,
        "changes": len(differences) - expected,
        "differences": differences,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ledger", type=Path, required=True, help="Publication ledger (read only)")
    args = parser.parse_args(argv)
    summary = report(public.read_json(args.ledger))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["changes"] else 0


if __name__ == "__main__":
    sys.exit(main())
