"""Daily selection and unattended publication agree on repeats, caps and ledger history.

Selection reads an imported evidence view (inbox_support.inbox), as the engine exports it.
"""

import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from inbox_support import inbox
from test_autopilot import NOW, Ghost, candidate

from tools import run_autopilot as runner
from wtf_adsb_publisher import autopilot, curation, plugins, watchlists
from wtf_adsb_publisher import public_events as public

DAY = 86400


def moved(item, seconds):
    source = item["source"]
    source["first_seen"] += seconds
    source["last_seen"] += seconds
    metrics = source["evidence"].get("maneuver") or {}
    for key in ("started_at", "ended_at", "sustained_started_at", "sustained_ended_at"):
        if key in metrics:
            metrics[key] += seconds
    item["interval"] = (source["first_seen"], source["last_seen"])
    return item


def typed(item, typecode):
    item["source"]["evidence"]["aircraft"]["t"] = typecode
    return item


class PublicationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.ledger = public.new_ledger("2026-08-01")
        self.ghost = Ghost()
        self.saved = None
        self.now = public._instant(NOW).timestamp()

    def persist(self, ledger):
        self.saved = deepcopy(ledger)

    def publish(self, at=NOW):
        with patch("test_autopilot.NOW", at):
            return autopilot.publish(self.ledger, self.ghost, persist=self.persist, at=at)

    def database(self, *items):
        """The findings the engine would export; collect() builds their observations."""
        return items

    def collect(self, items, now, **options):
        """Selection over the imported view of `items` at `now`, as autopilot.collect read the
        journal: findings first seen after `now` are not there yet."""
        norms = options.pop("norms", None)
        novelty = options.pop("novelty", None)
        extra = {"novelty": novelty} if novelty else {}
        return autopilot.collect_state(inbox(*items, now=now, norms=norms, **extra), now, **options)

    def release(self, item, *, frozen=True):
        autopilot.stage(self.ledger, [item], at=NOW)
        _, result = self.publish()
        self.assertEqual(result["published"], 1)
        record = next(row for row in self.ledger["records"] if row["episode_key"] == item["key"])
        if frozen:
            record["automation"]["source_kind"] = "frozen"
        return record

    def test_collect_and_publish_agree_across_a_frozen_story(self):
        self.release(typed(candidate(1, "watch_type", None, address="aaaaaa", day=-9), "A388"))
        held = typed(candidate(2, "watch_type", None, address="aaaaaa"), "A388")
        other = typed(candidate(3, "watch_type", None, address="bbbbbb"), "C5M")
        database = self.database(held, other)
        selected = self.collect(database, self.now, ledger=self.ledger)
        self.assertEqual([item["source"]["hex"] for item in selected], ["bbbbbb"])
        autopilot.stage(self.ledger, selected, at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(
            result, {"published": 1, "unchanged": 1, "deferred": 0, "suspended": 0, "failed": 0, "invalid": 0}
        )
        self.assertEqual(sorted(e["aircraft"]["type_code"] for e in snapshot["events"]), ["A388", "C5M"])
        # Without the ledger the held repeat takes a slot that publication would defer.
        self.assertEqual(len(self.collect(database, self.now)), 2)

    def test_the_publisher_holds_a_series_repeat_on_another_aircraft(self):
        def story(identifier, address, start):
            return {
                "id": identifier,
                "addresses": [address],
                "kind": "identity_conflict",
                "start": start,
                "day": "2026-06-01",
                "series": "identity_conflict",
            }

        # An identity conflict reads the same on any aircraft for 21 days.
        log = curation.StoryLog([story("a", "aaaaaa", 0)])
        self.assertEqual(autopilot.repeat_reason(log, story("b", "bbbbbb", 20 * DAY)), "identity_series")
        self.assertIsNone(autopilot.repeat_reason(log, story("b", "bbbbbb", 22 * DAY)))

    def test_a_watchlist_record_without_a_saved_source_joins_no_series(self):
        event = {
            "kind": "watchlist_match",
            "aircraft": {"icao_address": "ABCDEF", "type_code": "B25"},
            "watchlist": {"category": "Historic aircraft"},
        }
        # Each watchlist story names its own entry, so none reads like another.
        facts = autopilot._facts({}, event)
        self.assertEqual((facts["type"], facts["series"]), ("B25", None))
        event["watchlist"]["category"] = watchlists.GENERIC_CATEGORY
        self.assertIsNone(autopilot._facts({}, event)["series"])
        del event["watchlist"]
        self.assertIsNone(autopilot._facts({}, event)["series"])

    def test_exact_episode_times_decide_identity_repeats_in_both_places(self):
        # A vintage airframe's identity cooldown is 90 days, at exact episode times.
        story = moved(candidate(1, "vintage", 100, address="abcdef", day=-90), 11 * 3600)
        self.release(story)
        early = moved(candidate(2, "vintage", 100, address="abcdef"), -4 * 3600)
        late = moved(candidate(3, "vintage", 100, address="abcdef", day=1), -4 * 3600)
        database = self.database(early, late)
        selected = self.collect(database, self.now + DAY, ledger=self.ledger)
        self.assertEqual([item["source"]["id"] for item in selected], [3])
        # Publication applies the same exact-time rule to a pick staged directly.
        autopilot.stage(self.ledger, [early], at=NOW)
        _, result = self.publish()
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(result["published"], 0)
        autopilot.stage(self.ledger, selected, at=NOW)
        _, result = self.publish(at="2026-10-02T18:00:00Z")
        self.assertEqual(result["published"], 1)

    # The engine sends type-day counts for the days of its observations only, so a prior
    # story without a saved automation.routine_type (bootstrapped records and those staged
    # from collect_state carry it; this one is staged directly without it) has no counts for
    # its own date and cannot hold the later story; the live journal path counted every date.
    # Kept as a known difference of the split.
    @unittest.expectedFailure
    def test_a_frozen_story_routine_on_its_date_holds_a_later_live_one(self):
        record = self.release(typed(candidate(1, "vintage", 100, address="aaaaaa", day=-20), "DC3"))
        self.assertEqual(record["automation"].get("routine_type"), None)
        later = typed(candidate(2, "vintage", 100, address="bbbbbb"), "DC3")
        database = self.database(later)
        first = datetime.fromtimestamp(record["automation"]["source"]["first_seen"], timezone.utc).date()
        days = [(first + timedelta(days=offset)).isoformat() for offset in range(-60, 21)]
        # Every day is covered; DC3 was routine on the frozen story's date, but not 20 days later.
        presence = {"coverage": days, "types": {"DC3": days[:24]}}

        def novelty(day):
            return curation.type_day_counts(presence, day)

        self.assertEqual(self.collect(database, self.now, ledger=self.ledger, novelty=novelty), [])

    def test_an_in_place_withdrawal_never_holds_the_aircraft(self):
        """B14 (H3): autopilot._story marks withdrawn records, and curation.StoryLog ignores them."""
        record = self.release(typed(candidate(1, "watch_type", None, address="aaaaaa"), "B52"))
        public.set_review_state(self.ledger, record["id"], "withdrawn", at=NOW)
        later = moved(typed(candidate(2, "vintage", 100, address="aaaaaa"), "B52"), 3600)
        selected = self.collect(self.database(later), self.now + 3600, ledger=self.ledger)
        self.assertEqual([item["source"]["id"] for item in selected], [2])

    def test_publication_caps_one_aircraft_burst_at_two_exceptional_stories(self):
        burst = [
            moved(candidate(i, "rapid_descent", -9000, address="abcdef"), (i - 1) * 7 * 3600 - 10 * 3600)
            for i in (1, 2, 3)
        ]
        autopilot.stage(self.ledger, burst, at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 2)
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(len(snapshot["events"]), 2)
        spread = [moved(candidate(i, "rapid_descent", -9000), -600 * i) for i in range(10, 22)]
        autopilot.stage(self.ledger, spread, at=NOW)
        _, result = self.publish()
        self.assertEqual(result["published"], 12)

    def test_selection_demotion_survives_staging(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 10)], at=NOW)
        self.publish()
        demoted = candidate(20, "rapid_descent", -9000)
        demoted["exceptional"] = False
        autopilot.stage(self.ledger, [demoted], at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(len(snapshot["events"]), 9)

    def test_a_stored_repeated_descent_is_not_promoted_by_the_publisher(self):
        first = candidate(20, "rapid_descent", -9000, address="abcdef")
        second = moved(candidate(21, "rapid_descent", -9000, address="abcdef"), 2 * 3600)
        selected = self.collect(self.database(first, second), self.now)
        self.assertEqual(len(selected), 1)
        self.assertIs(selected[0]["exceptional"], False)
        self.assertEqual(
            selected[0]["exception"],
            {
                "grade": True,
                "exceptional": False,
                "reason": "repeated_descent",
                "curation_version": curation.VERSION,
            },
        )
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 10)], at=NOW)
        self.publish()
        autopilot.stage(self.ledger, selected, at=NOW)
        record = next(row for row in self.ledger["records"] if row["automation"].get("exception"))
        self.assertEqual(record["automation"]["exception"]["reason"], "repeated_descent")
        # Neither the publisher's order nor its priority promotes it over the daily target.
        record["editorial"].pop("priority", None)
        event = autopilot._project(record)
        self.assertIn("priority_evidence", event)
        self.assertFalse(autopilot.exception_standing(record, event))
        self.assertTrue(autopilot.exception_standing(record, event, sticky=True))
        snapshot, result = self.publish()
        self.assertEqual((result["published"], result["deferred"]), (0, 1))
        self.assertEqual(len(snapshot["events"]), 9)

    def test_suspended_story_does_not_hold_its_aircraft_but_an_owner_removal_does(self):
        morning = moved(candidate(1, "vintage", 100, address="abcdef"), -5 * 3600)
        autopilot.stage(self.ledger, [morning], at=NOW)
        self.publish()
        rejected = deepcopy(morning)
        rejected["source"]["verdict"] = "bad_data"
        autopilot.stage(self.ledger, [rejected], at=NOW)
        _, result = self.publish()
        self.assertEqual(result["suspended"], 1)
        afternoon = candidate(2, "rapid_descent", -5000, address="abcdef")
        autopilot.stage(self.ledger, [afternoon], at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual([event["kind"] for event in snapshot["events"]], ["rapid_descent"])
        # An owner's unpublication still holds the aircraft for the rest of the date.
        self.ghost.posts[-1]["status"] = "draft"
        self.publish()
        evening = moved(candidate(3, "vintage", 100, address="abcdef"), 4 * 3600)
        autopilot.stage(self.ledger, [evening], at=NOW)
        _, result = self.publish()
        self.assertEqual(result["published"], 0)
        self.assertTrue(
            next(row for row in self.ledger["records"] if row["episode_key"] == "2")["automation"]["removed"]
        )

    def test_one_record_that_no_longer_projects_does_not_stop_publication(self):
        autopilot.stage(self.ledger, [candidate(1), candidate(2)], at=NOW)
        broken = self.ledger["records"][0]
        broken["evidence"]["kind"] = "retired_detector"
        snapshot, result = self.publish()
        self.assertEqual((result["published"], result["invalid"], result["failed"]), (1, 1, 0))
        self.assertEqual(len(snapshot["events"]), 1)
        # Nothing is saved against the record, so it publishes once it projects again.
        self.assertNotIn("blocked", broken["automation"])
        broken["evidence"]["kind"] = "vintage"
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual(len(snapshot["events"]), 2)

    def test_live_article_that_no_longer_projects_is_suspended_and_recovers(self):
        autopilot.stage(self.ledger, [candidate(1), candidate(2)], at=NOW)
        self.publish()
        broken = self.ledger["records"][0]
        slug = public.article_slug(broken["editorial"]["article_path"])
        broken["evidence"]["kind"] = "retired_detector"
        autopilot.stage(self.ledger, [moved(candidate(3), -3600)], at=NOW)
        snapshot, result = self.publish()
        self.assertEqual((result["published"], result["suspended"], result["invalid"]), (1, 1, 1))
        self.assertEqual(len(snapshot["events"]), 2)
        self.assertEqual(next(post for post in self.ghost.posts if post["slug"] == slug)["status"], "draft")
        broken["evidence"]["kind"] = "vintage"
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual(len(snapshot["events"]), 3)
        self.assertEqual(
            next(post for post in self.ghost.posts if post["slug"] == slug)["status"], "published"
        )

    def test_several_records_that_no_longer_project_pause_publication(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 5)], at=NOW)
        self.publish()
        writes = self.ghost.writes
        for record in self.ledger["records"][:3]:
            record["evidence"]["kind"] = "retired_detector"
        with self.assertRaisesRegex(ValueError, "previous release remains"):
            self.publish()
        self.assertEqual(self.ghost.writes, writes)

    def test_records_without_live_articles_never_pause_publication(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 4)], at=NOW)
        for record in self.ledger["records"]:
            record["evidence"]["kind"] = "retired_detector"
        autopilot.stage(self.ledger, [candidate(4)], at=NOW)
        snapshot, result = self.publish()
        self.assertEqual((result["published"], result["invalid"]), (1, 3))
        self.assertEqual(len(snapshot["events"]), 1)

    def test_a_suspension_for_unreadable_evidence_keeps_the_story_claim(self):
        story = typed(candidate(1, "watch_type", None, address="abcdef"), "A388")
        autopilot.stage(self.ledger, [story], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        record["evidence"]["kind"] = "retired_detector"
        _, result = self.publish()
        self.assertEqual(result["suspended"], 1)
        later = typed(moved(candidate(2, "vintage", 90, address="abcdef"), 3 * 86400), "A388")
        autopilot.stage(self.ledger, [later], at=NOW)
        at = "2026-10-04T18:00:00Z"
        _, result = self.publish(at=at)
        self.assertEqual(result["published"], 0)
        # Once the evidence projects again, the original story returns and the repeat stays held.
        record["evidence"]["kind"] = "watch_type"
        snapshot, result = self.publish(at=at)
        self.assertEqual(result["published"], 1)
        self.assertEqual([event["kind"] for event in snapshot["events"]], ["watch_type"])
        self.assertNotIn("unprojectable", record["automation"])

    def test_a_record_that_no_longer_validates_does_not_block_staging(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.ledger["records"][0]["evidence"]["kind"] = "retired_detector"
        self.assertEqual(autopilot.stage(self.ledger, [candidate(2)], at=NOW), {"changed": 1, "rejected": 0})
        self.assertEqual(len(self.ledger["records"]), 2)
        self.assertEqual(self.ledger["records"][0]["evidence"]["kind"], "retired_detector")
        # A revision of the broken record itself waits for a fix without a saved rejection.
        self.assertEqual(autopilot.stage(self.ledger, [candidate(1)], at=NOW), {"changed": 0, "rejected": 1})
        self.assertNotIn("blocked", self.ledger["records"][0]["automation"])

    def test_live_article_with_an_unresolved_card_still_holds_its_slot(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 10)], at=NOW)
        self.publish()
        original = autopilot.post_evidence
        failing = self.ledger["records"][0]["id"]

        def post_evidence(record, post):
            if record["id"] == failing:
                raise ValueError("Owner-edited card with a retired revision")
            return original(record, post)

        autopilot.stage(self.ledger, [candidate(10)], at=NOW)
        with patch.object(autopilot, "post_evidence", side_effect=post_evidence):
            snapshot, result = self.publish()
        self.assertEqual(result["published"], 0)
        self.assertEqual(result["deferred"], 1)
        self.assertEqual(len(snapshot["events"]), 9)

    def test_scarce_slots_follow_rank_not_recency(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 9)], at=NOW)
        self.publish()
        stronger = dict(candidate(10), rank=9)
        weaker = dict(moved(candidate(11), 3600), rank=10)
        autopilot.stage(self.ledger, [stronger, weaker], at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        published = {event["aircraft"]["registration"] for event in snapshot["events"]}
        self.assertIn("N10010", published)
        self.assertNotIn("N10011", published)

    def test_owner_removed_finding_stays_removed_when_it_later_becomes_exceptional(self):
        descent = candidate(1, "rapid_descent", -9000, address="abcdef")
        descent["exceptional"] = False
        autopilot.stage(self.ledger, [descent], at=NOW)
        self.publish()
        self.ghost.posts[0]["status"] = "draft"
        self.publish()
        autopilot.stage(self.ledger, [dict(descent, exceptional=True)], at=NOW)
        self.assertEqual(len(self.ledger["records"]), 1)
        snapshot, result = self.publish()
        self.assertFalse(snapshot["events"])
        self.assertEqual(self.ghost.posts[0]["status"], "draft")

    def test_a_live_exception_over_the_per_aircraft_cap_gives_way_first(self):
        autopilot.stage(self.ledger, [dict(candidate(i), rank=i) for i in range(1, 10)], at=NOW)
        self.publish()
        # Three exceptional stories for one aircraft went live before the cap existed.
        burst = [
            dict(
                moved(candidate(i, "rapid_descent", -9000, address="abcdef"), (i - 11) * 7 * 3600 - 3 * 3600),
                rank=i,
            )
            for i in (10, 11, 12)
        ]
        autopilot.stage(self.ledger, burst, at=NOW)
        with patch.object(curation, "EXCEPTIONAL_PER_AIRCRAFT", 3):
            _, result = self.publish()
        self.assertEqual(result["published"], 3)
        snapshot, result = self.publish()
        self.assertEqual(result["suspended"], 1)
        live = {self.record_key(event["id"]) for event in snapshot["events"]}
        self.assertEqual(live, {str(i) for i in range(1, 12)})

    def record_key(self, identifier):
        return next(row["episode_key"] for row in self.ledger["records"] if row["id"] == identifier)

    def test_a_corrected_date_rechecks_the_per_aircraft_exceptional_cap(self):
        first, second = (
            moved(candidate(i, "rapid_descent", -9000, address="abcdef", day=1), hours * 3600)
            for i, hours in ((1, -10), (2, -2))
        )
        third = moved(candidate(3, "rapid_descent", -9000, address="abcdef", day=2), -10 * 3600)
        autopilot.stage(self.ledger, [first, second, third], at="2026-10-03T18:00:00Z")
        _, result = self.publish(at="2026-10-03T18:00:00Z")
        self.assertEqual(result["published"], 3)
        record = next(row for row in self.ledger["records"] if row["episode_key"] == "3")
        self.assertTrue(record["automation"]["published_exceptional"])
        corrected = deepcopy(third)
        corrected["interval"] = (first["source"]["first_seen"] + 600, third["source"]["last_seen"])
        autopilot.stage(self.ledger, [corrected], at="2026-10-03T18:00:00Z")
        snapshot, _ = self.publish(at="2026-10-03T18:00:00Z")
        record = next(row for row in self.ledger["records"] if row["episode_key"] == "3")
        self.assertTrue(record["evidence"]["observation"]["start_utc"].startswith("2026-10-02"))
        self.assertFalse(record["automation"]["published_exceptional"])
        # Demoted by the full per-aircraft cap, its public record shows no exception proof.
        event = next(event for event in snapshot["events"] if event["id"] == record["id"])
        self.assertNotIn("priority_evidence", event)
        self.assertEqual(sum("priority_evidence" in event for event in snapshot["events"]), 2)
        self.assertEqual(record["editorial"]["priority"], "ordinary")
        # Later passes neither restore the proof nor revise the record again.
        revision = record["revision"]
        autopilot.stage(self.ledger, [corrected], at="2026-10-03T18:00:00Z")
        snapshot, result = self.publish(at="2026-10-03T18:00:00Z")
        self.assertEqual(result["published"], 0)
        record = next(row for row in self.ledger["records"] if row["episode_key"] == "3")
        self.assertEqual(record["revision"], revision)
        self.assertEqual(sum("priority_evidence" in event for event in snapshot["events"]), 2)

    def test_owner_removal_does_not_suppress_a_later_exceptional_finding(self):
        ordinary = candidate(1, "vintage", 100, address="abcdef")
        autopilot.stage(self.ledger, [ordinary], at=NOW)
        self.publish()
        self.ghost.posts[0]["status"] = "draft"
        self.publish()
        removed = self.ledger["records"][0]
        self.assertTrue(removed["automation"]["removed"])
        later = candidate(2, "rapid_descent", -9000, address="abcdef")
        later.update(key=ordinary["key"], exceptional=True)
        autopilot.stage(self.ledger, [later], at=NOW)
        self.assertEqual(len(self.ledger["records"]), 2)
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual([event["kind"] for event in snapshot["events"]], ["rapid_descent"])
        self.assertEqual(self.ghost.posts[0]["status"], "draft")
        # An ordinary update to the removed story stays with the owner's decision.
        autopilot.stage(self.ledger, [dict(ordinary, exceptional=False)], at=NOW)
        self.assertEqual(len(self.ledger["records"]), 2)

    def test_a_live_story_keeps_its_slot_against_later_stronger_candidates(self):
        live = candidate(1, "vintage", 80, address="aaaaaa")
        # Distinct watch types are distinct sub-topics; vintage airframes would share one.
        types = ("A388", "C5M", "B52", "A124", "E6", "V22", "DC3", "P51", "BLCF")
        stronger = [
            typed(moved(candidate(i, "watch_type", None), 3600 + i), types[i - 2]) for i in range(2, 11)
        ]
        database = self.database(live)
        autopilot.stage(self.ledger, self.collect(database, self.now, ledger=self.ledger), at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        database = self.database(live, *stronger)
        selected = self.collect(database, self.now, ledger=self.ledger)
        self.assertEqual(len(selected), 9)
        self.assertIn(record["episode_key"], {item["key"] for item in selected})
        autopilot.stage(self.ledger, selected, at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(result["deferred"], 0)
        self.assertEqual(len(snapshot["events"]), 9)

    def test_live_story_keeps_its_primary_finding_on_later_passes(self):
        self.release(typed(candidate(1, "watch_type", None, address="aaaaaa", day=-5), "DC3"))
        held = typed(candidate(2, "watch_type", None, address="bbbbbb"), "DC3")
        vintage = typed(candidate(3, "vintage", 85, address="bbbbbb"), "DC3")
        alarm = candidate(4, "special_squawk_7700", 7700, address="bbbbbb")
        alarm["source"]["unit"] = ""
        for item in (held, vintage, alarm):
            item["source"]["evidence"]["aircraft"]["r"] = "N52"
        database = self.database(held, vintage)
        selected = self.collect(database, self.now, ledger=self.ledger)
        self.assertEqual([item["source"]["kind"] for item in selected], ["vintage"])
        autopilot.stage(self.ledger, selected, at=NOW)
        self.publish()
        writes = self.ghost.writes
        # The held watch-type reason must not take over the live story on a later pass.
        again = self.collect(database, self.now, ledger=self.ledger)
        self.assertEqual([item["source"]["kind"] for item in again], ["vintage"])
        self.assertEqual(autopilot.stage(self.ledger, again, at=NOW), {"changed": 0, "rejected": 0})
        _, result = self.publish()
        self.assertEqual(result["unchanged"], 2)
        self.assertEqual(self.ghost.writes, writes)
        # An exceptional finding in the same encounter can still replace it.
        database = self.database(held, vintage, alarm)
        later = self.collect(database, self.now, ledger=self.ledger)
        self.assertEqual([item["source"]["kind"] for item in later], ["special_squawk_7700"])
        self.assertTrue(later[0]["exceptional"])
        self.assertEqual(later[0]["key"], selected[0]["key"])

    def test_collect_sees_the_evidence_of_rules_findings(self):
        record = typed(candidate(1, "seasonal_speed_record", 640, address="abcdef"), "B738")
        evidence = record["source"]["evidence"]
        evidence.pop("maneuver")
        evidence["season_record"] = {"sustained_seconds": 75, "samples": 6, "source": "adsb_icao"}
        first = typed(candidate(2, "first_country_here", 120, address="4ca123"), "A320")
        first["source"]["evidence"].pop("maneuver", None)
        first["source"]["evidence"]["first_sighting"] = {"scope": "allocation_country", "country": "IE"}
        selected = self.collect(self.database(record, first), self.now)
        self.assertEqual(
            sorted(item["source"]["kind"] for item in selected),
            ["first_country_here", "seasonal_speed_record"],
        )

    def test_collect_gives_a_speed_story_the_type_norm_the_engine_exported(self):
        record = typed(candidate(1, "seasonal_speed_record", 640, address="abcdef"), "B738")
        evidence = record["source"]["evidence"]
        evidence.pop("maneuver")
        evidence["season_record"] = {"sustained_seconds": 75, "samples": 6, "source": "adsb_icao"}
        evidence["aircraft"].update(alt_baro=35000, baro_rate=0)
        first = typed(candidate(2, "first_country_here", 120, address="4ca123"), "A320")
        first["source"]["evidence"].pop("maneuver", None)
        first["source"]["evidence"]["first_sighting"] = {"scope": "allocation_country", "country": "IE"}
        first["source"]["evidence"]["aircraft"].update(alt_baro=35000, baro_rate=0)
        database = self.database(record, first)
        # The engine's norm for the story (selection.type_norm over other addresses), as the
        # live test computed it from performance samples.
        norm = {
            "low": 425,
            "high": 465,
            "phase": "level",
            "band_floor_ft": 30000,
            "band_ceiling_ft": 40000,
            "addresses": 25,
            "days": 20,
            "window_days": 30,
        }
        selected = {
            item["source"]["kind"]: item for item in self.collect(database, self.now, norms={1: norm})
        }
        self.assertEqual(selected["seasonal_speed_record"]["type_norm"], norm)
        # Other kinds get no norm, even one the feed names.
        self.assertIsNone(selected["first_country_here"]["type_norm"])
        both = {1: norm, 2: norm}
        selected = {item["source"]["kind"]: item for item in self.collect(database, self.now, norms=both)}
        self.assertIsNone(selected["first_country_here"]["type_norm"])
        # A 1.0.0 feed carries no norms: the story has none.
        selected = {item["source"]["kind"]: item for item in self.collect(database, self.now)}
        self.assertIsNone(selected["seasonal_speed_record"]["type_norm"])

    def test_collect_folds_spikes_into_a_balloon_under_review(self):
        balloon = candidate(1, "stratospheric_balloon", 65000, address="a1b2c3", flags=("uncertain_source",))
        spike = candidate(2, "outlier_gs", 1005.6, address="a1b2c3")
        for item in (balloon, spike):
            item["source"]["evidence"]["aircraft"]["r"] = "N930TH"
        self.assertTrue(autopilot.eligible(spike["source"]))
        self.assertFalse(self.collect(self.database(balloon, spike), self.now))
        self.assertEqual(len(self.collect(self.database(spike), self.now)), 1)

    def test_a_published_exception_survives_a_later_selection_demotion(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 10)], at=NOW)
        self.publish()
        descent = candidate(10, "rapid_descent", -9000)
        autopilot.stage(self.ledger, [descent], at=NOW)
        _, result = self.publish()
        self.assertEqual(result["published"], 1)
        # A later descent nearby demotes it at selection time; the live story keeps its priority.
        autopilot.stage(self.ledger, [dict(descent, exceptional=False)], at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(result["suspended"], 0)
        self.assertEqual(len(snapshot["events"]), 10)

    def test_a_removed_story_keeps_the_decision_it_was_published_with(self):
        descent = candidate(1, "rapid_descent", -9000, address="abcdef")
        descent["exceptional"] = False
        autopilot.stage(self.ledger, [descent], at=NOW)
        self.publish()
        self.ghost.posts[0]["status"] = "draft"
        self.publish()
        autopilot.stage(self.ledger, [dict(descent, exceptional=True)], at=NOW)
        self.assertFalse(self.ledger["records"][0]["automation"]["exceptional"])
        alarm = candidate(2, "special_squawk_7700", 7700, address="abcdef")
        alarm["source"]["unit"] = ""
        autopilot.stage(self.ledger, [dict(alarm, key=descent["key"], exceptional=True)], at=NOW)
        self.assertEqual(self.ledger["records"][-1]["episode_key"], descent["key"] + ":exceptional")
        snapshot, _ = self.publish()
        self.assertEqual([event["kind"] for event in snapshot["events"]], ["special_squawk_7700"])

    def test_later_decisions_follow_the_exceptional_replacement(self):
        ordinary = candidate(1, "vintage", 100, address="abcdef")
        autopilot.stage(self.ledger, [ordinary], at=NOW)
        self.publish()
        self.ghost.posts[0]["status"] = "draft"
        self.publish()
        later = candidate(2, "rapid_descent", -9000, address="abcdef")
        autopilot.stage(self.ledger, [dict(later, key=ordinary["key"], exceptional=True)], at=NOW)
        self.publish()
        autopilot.stage(self.ledger, [dict(later, key=ordinary["key"], exceptional=False)], at=NOW)
        removed, replacement = self.ledger["records"]
        self.assertEqual(removed["automation"]["source"]["kind"], "vintage")
        self.assertEqual(replacement["episode_key"], ordinary["key"] + ":exceptional")
        self.assertFalse(replacement["automation"]["exceptional"])

    def test_collect_survives_overlapping_imports_with_one_episode_per_encounter(self):
        first = candidate(1, "rapid_descent", -9000, address="abcdef")
        start = first["source"]["first_seen"]
        first["source"]["evidence"]["encounter_start"] = start
        imported = moved(candidate(2, "vintage", 100, address="abcdef"), 1000)
        third = moved(candidate(3, "vintage", 100, address="abcdef"), 1100)
        third["source"]["evidence"]["encounter_start"] = start
        selected = self.collect(self.database(first, imported, third), self.now + 7200)
        keys = [item["key"] for item in selected]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source"]["kind"], "rapid_descent")


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.ledger = public.new_ledger("2026-10-01")
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.ledger["records"][0]["automation"]["source_kind"] = "frozen"
        self.end = public._instant(self.ledger["records"][0]["evidence"]["observation"]["end_utc"])
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        public.write_json_atomic(Path(self.folder.name) / "ledger.json", self.ledger, private=True)

    def run_with(self, live_since, **config):
        config = {
            "state_dir": self.folder.name,
            "inbox_database": "unused",
            # An empty replayed view, so no freshness warning joins the coverage ones.
            "allow_replay_publication": True,
            "preview_ssh_host": "test-site",
            "live_since": public.utc(live_since.isoformat()),
            **config,
        }
        with (
            patch.object(plugins.JsonInbox, "read", return_value=inbox()),
            patch.object(plugins.SitePolicy, "select", return_value=[]),
            patch.object(runner, "source_digest", return_value="same-renderer"),
            patch.object(runner, "build_release"),
        ):
            result = runner.run(config, client=Ghost())
        self.assertEqual(result["failed"], 0)
        return result, public.read_json(Path(self.folder.name) / "status.json")

    def test_configured_cutoff_warns_about_gaps_and_overlaps_without_stopping(self):
        cutoff = datetime(2026, 10, 7, tzinfo=timezone.utc)
        for offset, expected in (
            (25 * 3600, "coverage_gap"),
            (-2 * 3600, "coverage_overlap"),
            (60, None),
            (-60, None),
        ):
            with self.subTest(offset=offset):
                result, status = self.run_with(
                    cutoff + timedelta(seconds=offset), frozen_through=cutoff.isoformat()
                )
                self.assertEqual(result["warnings"], [expected] if expected else [])
                warnings = [name for name in runner.COVERAGE_WARNINGS if name in status]
                self.assertEqual(warnings, [expected] if expected else [])
                if expected:
                    self.assertEqual(status[expected]["seconds"], abs(offset))
                    self.assertEqual(status[expected]["basis"], "frozen_through")

    def test_missing_cutoff_falls_back_to_the_newest_frozen_record(self):
        for offset, expected in ((25 * 3600, "coverage_gap"), (-3600, "coverage_overlap"), (90, None)):
            with self.subTest(offset=offset):
                result, status = self.run_with(self.end + timedelta(seconds=offset))
                self.assertEqual(result["warnings"], [expected] if expected else [])
                if expected:
                    self.assertEqual(status[expected]["basis"], "newest_frozen_record")
        # The configured cutoff wins over the ledger's newest frozen record.
        result, _ = self.run_with(
            self.end + timedelta(hours=25), frozen_through=(self.end + timedelta(hours=25)).isoformat()
        )
        self.assertEqual(result["warnings"], [])

    def test_malformed_cutoff_is_reported_and_publication_continues(self):
        result, status = self.run_with(self.end, frozen_through="yesterday")
        self.assertEqual(result["warnings"], ["coverage_unknown"])
        self.assertIn("coverage_unknown", status)


class ImportedViewTests(unittest.TestCase):
    """inbox_support groups findings into encounters as the engine's selection.encounters does."""

    @staticmethod
    def airframe(identifier, seconds=0):
        """A finding for one airframe (one address and registration), moved by `seconds`."""
        item = moved(candidate(identifier, address="aaaaaa"), seconds)
        item["source"]["evidence"]["aircraft"]["r"] = "N10001"
        return item

    @staticmethod
    def groups(*items):
        return [
            [event["id"] for event in row["observation"]["group"]["events"]]
            for row in inbox(*items)["observations"]
        ]

    def test_an_encounter_ends_after_ten_quiet_minutes(self):
        first = self.airframe(1)
        length = first["source"]["last_seen"] - first["source"]["first_seen"]
        # Twenty minutes after the first finding ends: past the engine's 600-second gap.
        self.assertEqual(self.groups(first, self.airframe(2, length + 1200)), [[1], [2]])
        # Within the gap the findings share one encounter.
        self.assertEqual(self.groups(first, self.airframe(3, length + 300)), [[1, 3]])

    def test_findings_seen_together_keep_the_journal_id_order(self):
        # Equal first-seen times order by numeric id, as the journal's rows do (2 before 10).
        self.assertEqual(self.groups(self.airframe(10), self.airframe(2)), [[2, 10]])


if __name__ == "__main__":
    unittest.main()
