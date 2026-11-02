#!/usr/bin/env python3
"""Record every autopilot.publish call of the publication scenarios, so a refactor can prove it changed no decision.

Each scenario runs an existing publication test (or one of two scenarios of its own) while
every call to autopilot.publish is recorded: the result counters, the number of persist()
calls and Ghost writes, each record's release flags, priority, state, and Ghost post status, and
the snapshot's stories per UTC date. Records are keyed by episode key, and record ids are
numbered in creation order so that ties in the publication order break the same way on
every run. tests/test_publish_golden.py compares a fresh recording with the fixture;
--write records the fixture again when a change to publication is intended.
"""

import argparse
import sys
import unittest
import uuid
from collections import Counter
from copy import deepcopy
from itertools import count
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
from test_autopilot import NOW, Ghost, candidate
from test_selection_publication import moved

from tools.kinds_golden import serialise
from wtf_adsb_publisher import autopilot, curation
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher.ghost_client import GhostError

FIXTURE = ROOT / "tests" / "fixtures" / "publish-golden.json"

POLICY = "test_selection_publication.PublicationPolicyTests"
AUTOPILOT = "test_autopilot.AutopilotTests"
STORIES = "test_autopilot.StoryPublicationTests"

# The scenarios the plan names, then others that reach the quota's remaining branches.
TESTS = (
    f"{POLICY}.test_publication_caps_one_aircraft_burst_at_two_exceptional_stories",
    f"{POLICY}.test_selection_demotion_survives_staging",
    f"{POLICY}.test_scarce_slots_follow_rank_not_recency",
    f"{POLICY}.test_a_live_exception_over_the_per_aircraft_cap_gives_way_first",
    f"{POLICY}.test_a_corrected_date_rechecks_the_per_aircraft_exceptional_cap",
    f"{AUTOPILOT}.test_nine_already_live_then_exceptional_afternoon_event_publishes_automatically",
    f"{AUTOPILOT}.test_lost_put_response_never_allows_a_tenth_ordinary_post_between_writes",
    f"{AUTOPILOT}.test_corrected_overflow_is_suspended_before_it_can_exceed_the_ordinary_quota",
    f"{AUTOPILOT}.test_interval_correction_into_a_full_previous_utc_day_suspends_before_writing",
    f"{STORIES}.test_a_live_exception_demoted_with_room_under_the_cap_is_updated_and_the_pass_goes_on",
    f"{STORIES}.test_a_demotion_holds_while_its_ghost_write_fails_and_never_blocks_a_dossier",
    f"{POLICY}.test_a_stored_repeated_descent_is_not_promoted_by_the_publisher",
    f"{POLICY}.test_suspended_story_does_not_hold_its_aircraft_but_an_owner_removal_does",
    f"{POLICY}.test_live_article_that_no_longer_projects_is_suspended_and_recovers",
    f"{POLICY}.test_a_suspension_for_unreadable_evidence_keeps_the_story_claim",
    f"{POLICY}.test_live_article_with_an_unresolved_card_still_holds_its_slot",
    f"{POLICY}.test_owner_removal_does_not_suppress_a_later_exceptional_finding",
    f"{POLICY}.test_a_live_story_keeps_its_slot_against_later_stronger_candidates",
    f"{POLICY}.test_a_published_exception_survives_a_later_selection_demotion",
    f"{AUTOPILOT}.test_lost_post_response_is_reconciled_without_duplicate_or_manual_approval",
    f"{AUTOPILOT}.test_ordinary_quota_includes_approved_legacy_publications",
    f"{AUTOPILOT}.test_retention_of_the_same_encounter_does_not_duplicate_an_exception",
    f"{AUTOPILOT}.test_one_uneditable_owned_article_does_not_starve_other_publications",
    f"{STORIES}.test_a_story_released_as_ordinary_carries_no_proof_and_stays_ordinary",
)


def refused_displaced_exception():
    """Two live exceptions over the per-aircraft cap, and Ghost refuses the first demotion.

    The refused story stays live, so for the rest of the pass it counts as an
    ordinary story and fills the date: the second one over the cap is suspended.
    """
    ledger, ghost = public.new_ledger("2026-10-01"), Ghost()

    def publish():
        return autopilot.publish(ledger, ghost, persist=lambda ledger: None, at=NOW)

    autopilot.stage(ledger, [dict(candidate(i), rank=i) for i in range(1, 9)], at=NOW)
    publish()
    burst = [
        dict(moved(candidate(i, "rapid_descent", -9000, address="abcdef"), (i - 12) * 5 * 3600), rank=i)
        for i in (10, 11, 12, 13)
    ]
    autopilot.stage(ledger, burst, at=NOW)
    with patch.object(curation, "EXCEPTIONAL_PER_AIRCRAFT", 4):
        publish()
    record = next(row for row in ledger["records"] if row["episode_key"] == "13")
    slug = autopilot._slug(record)
    save = ghost.save

    def refusing(post, current=None):
        if current and current["slug"] == slug:
            raise GhostError("Ghost request failed (HTTP 422)", 422)
        return save(post, current)

    with patch.object(ghost, "save", side_effect=refusing):
        publish()
    publish()


def corrected_exception_into_a_full_old_date():
    """A live exception whose date is corrected into a full earlier date, then more stories.

    It gives up the exceptional slot on its old date and takes priority over the nine
    ordinary stories on the corrected one; its old date then has room for eight more.
    """
    ledger, ghost = public.new_ledger("2026-09-30"), Ghost()

    def publish():
        return autopilot.publish(ledger, ghost, persist=lambda ledger: None, at=NOW)

    autopilot.stage(ledger, [dict(candidate(i, day=-1), rank=i) for i in range(1, 10)], at=NOW)
    publish()
    exception = candidate(11, "rapid_descent", -9000, address="abcdef")
    autopilot.stage(ledger, [exception, dict(candidate(12), rank=1)], at=NOW)
    publish()
    corrected = deepcopy(exception)
    corrected["interval"] = (public._instant("2026-09-30T23:55:00Z").timestamp(), exception["interval"][1])
    autopilot.stage(ledger, [corrected], at=NOW)
    publish()
    later = [dict(candidate(i), rank=i) for i in range(13, 22)]
    autopilot.stage(ledger, [*later, dict(candidate(22, day=-1), rank=1)], at=NOW)
    publish()


SCENARIOS = {name.rsplit(".", 1)[1]: name for name in TESTS}
FUNCTIONS = {
    "refused_displaced_exception": refused_displaced_exception,
    "corrected_exception_into_a_full_old_date": corrected_exception_into_a_full_old_date,
}


def _keys(ledger):
    """Episode key per record id, numbered when two records share an episode key."""
    seen, keys = Counter(), {}
    for record in ledger["records"]:
        key = str(record.get("episode_key"))
        seen[key] += 1
        keys[record["id"]] = key if seen[key] == 1 else f"{key}#{seen[key]}"
    return keys


def _records(ledger, client):
    keys = _keys(ledger)
    posts = getattr(client, "posts", None) or []
    by_slug = {post.get("slug"): post for post in posts}
    rows = {}
    for record in ledger["records"]:
        automatic = record.get("automation") or {}
        slug = autopilot._quiet(autopilot._slug, record)
        post = by_slug.get(slug)
        strays = sorted(
            post["status"]
            for post in posts
            if post.get("slug") != slug and autopilot.has_owner_tag(post, record["id"])
        )
        row = {
            "published_exceptional": automatic.get("published_exceptional"),
            "demoted_at_release": automatic.get("demoted_at_release"),
            "suspended": automatic.get("suspended"),
            "removed": automatic.get("removed"),
            "priority": record.get("editorial", {}).get("priority"),
            "state": record.get("state"),
            "revision": record.get("revision"),
            "post": post.get("status") if post else None,
        }
        if strays:
            row["strays"] = strays
        rows[keys[record["id"]]] = row
    return rows


def _days(ledger, snapshot):
    keys = _keys(ledger)
    days = {}
    for event in snapshot.get("events", []):
        days.setdefault(event["observed_start"][:10], []).append(keys.get(event["id"], event["id"]))
    return {day: sorted(stories) for day, stories in sorted(days.items())}


def record(run):
    """Every publish call made while run() executes, in order."""
    calls = []
    original = autopilot.publish

    def recording(ledger, client, *, persist, **kwargs):
        persisted = []
        requests = getattr(client, "requests", None)
        before = len(requests) if requests is not None else 0

        def counting(saved):
            persisted.append(True)
            return persist(saved)

        call = {}
        try:
            snapshot, result = original(ledger, client, persist=counting, **kwargs)
        except Exception as error:
            call["error"] = type(error).__name__
            raise
        else:
            call.update(result=dict(result), days=_days(ledger, snapshot))
            return snapshot, result
        finally:
            call.update(
                persists=len(persisted),
                writes=[post.get("status") for post in (requests or [])[before:]],
                records=_records(ledger, client),
            )
            calls.append(call)

    numbers = count(1)
    with (
        patch.object(autopilot, "publish", recording),
        patch.object(public.uuid, "uuid4", lambda: uuid.UUID(int=next(numbers), version=4)),
    ):
        run()
    return calls


def _run_test(name):
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    outcome = unittest.TestResult()
    suite.run(outcome)
    if not outcome.wasSuccessful():
        problems = outcome.errors + outcome.failures
        raise AssertionError(f"{name} no longer passes: {problems[0][1]}")


def observe():
    observed = {}
    for scenario, name in SCENARIOS.items():
        observed[scenario] = record(lambda name=name: _run_test(name))
    for scenario, function in FUNCTIONS.items():
        observed[scenario] = record(function)
    return observed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write", action="store_true", help=f"record {FIXTURE.relative_to(ROOT)} instead of printing"
    )
    args = parser.parse_args(argv)
    text = serialise(observe())
    if args.write:
        FIXTURE.write_text(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
