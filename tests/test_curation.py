"""Daily editorial selection favors supported exceptional events without flooding the feed."""

import sys
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fixtures import aircraft

from wtf_adsb_publisher import curation, selection, typecheck
from wtf_adsb_publisher.rules import candidates

START = datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp()


def finding(identifier, kind="circling", *, day=0, value=3, address=None, flags=(), confirmed=True):
    metrics = {
        "confirmed": confirmed,
        "shape_verified": confirmed,
        "quality_review_required": bool(flags),
        "quality_flags": list(flags),
        "duration_seconds": 720,
        "sample_count": 46,
        "closed_loops": 3,
    }
    evidence = {"aircraft": {"t": "C172", "type": "adsb_icao"}}
    if kind in typecheck.TYPE_DEPENDENT_KINDS:
        # A completed, agreeing second-source type check: publication waits for one
        # (curation.type_hold); the states themselves are covered in test_typecheck.
        evidence["type_check"] = {"state": "agrees", "source": "adsbdb", "checked_at": START}
    if kind in {"rapid_descent", "rapid_climb"}:
        metrics.update(
            peak_rate_fpm=value,
            started_at=START + day * 86400,
            ended_at=START + day * 86400 + 720,
            threshold_fpm=4500,
            qualifying_duration_seconds=30,
            qualifying_sample_count=4,
        )
        if abs(value) >= 7000:
            metrics.update(
                sustained_rate_fpm=value,
                sustained_duration_seconds=30,
                sustained_sample_count=4,
                sustained_altitude_change_ft=value / 2,
                sustained_expected_altitude_change_ft=value / 2,
                sustained_started_at=START + day * 86400,
                sustained_ended_at=START + day * 86400 + 30,
            )
    if kind in {"circling", "repeated_passes", "loitering", "confined_activity"}:
        evidence["pattern"] = metrics
    elif kind not in curation.IDENTITY_KINDS:
        evidence["maneuver"] = metrics
    event = {
        "id": identifier,
        "hex": address or f"{identifier:06x}",
        "kind": kind,
        "first_seen": START + day * 86400,
        "last_seen": START + day * 86400 + 720,
        "value": value,
        "hits": 46,
        "evidence": evidence,
    }
    event["selection"] = selection.assess(event, {})
    return event


def shift(event, seconds):
    """Move a synthetic finding and its episode times together."""
    event["first_seen"] += seconds
    event["last_seen"] += seconds
    metrics = event["evidence"].get("maneuver") or event["evidence"].get("pattern") or {}
    for key in ("started_at", "ended_at", "sustained_started_at", "sustained_ended_at"):
        if key in metrics:
            metrics[key] += seconds
    return event


def group(*events, key=None):
    return {
        "id": key or str(events[0]["id"]),
        "events": list(events),
        "first_seen": min(event["first_seen"] for event in events),
        "last_seen": max(event["last_seen"] for event in events),
    }


class CurationTests(unittest.TestCase):
    def test_legacy_scalar_direction_comes_from_the_report_not_its_absolute_value(self):
        for signed_rate in (8000, -8000):
            with self.subTest(rate=signed_rate):
                report = aircraft(baro_rate=signed_rate)
                scalar = next(f for f in candidates(report, START) if f.kind == "vertical")
                self.assertEqual(scalar.value, 8000)
                source = finding(1, "vertical", value=scalar.value)
                source["evidence"] = {"aircraft": report, "confirmation": {"sustained": True}}
                source["selection"] = selection.assess(source)
                selected = curation.choose([group(source)])["selected"]
                self.assertEqual(bool(selected), signed_rate < 0)

    def test_climbs_need_an_independent_reason_even_with_a_sustained_high_rate(self):
        for kind in ("rapid_climb", "vertical"):
            for value in (4500, 8000, 12000):
                with self.subTest(kind=kind, value=value):
                    climb = finding(1, kind, value=value)
                    result = curation.choose([group(climb)])
                    self.assertFalse(result["selected"])
                    self.assertFalse(result["priority_review"])
                    self.assertEqual(result["decisions"][0]["disposition"], "context_only")
                    other = finding(2, "vintage", value=100, address=climb["hex"])
                    result = curation.choose([group(climb, other)])
                    self.assertEqual([r["finding_id"] for r in result["selected"]], [2])

    def test_exception_proof_requires_the_high_interval_not_whole_episode(self):
        original = finding(1, "rapid_descent", value=-8000)
        for changes in (
            {"sustained_duration_seconds": 10},
            {"sustained_sample_count": 3},
            {"sustained_rate_fpm": -6000},
            {"sustained_altitude_change_ft": 0},
            {"sustained_altitude_change_ft": -250, "sustained_expected_altitude_change_ft": -250},
            {"sustained_duration_seconds": 900},
        ):
            with self.subTest(changes=changes):
                event = deepcopy(original)
                event["evidence"]["maneuver"].update(changes)
                self.assertFalse(curation.rate(event)["exceptional"])

    def test_oscillations_and_other_context_cannot_qualify_each_other(self):
        oscillation = finding(1, "altitude_oscillation", value=20)
        oscillation["verdict"] = "keep"
        oscillation["selection"] = selection.assess(oscillation, {})
        context = [
            finding(2, "rapid_climb", value=8000, address=oscillation["hex"]),
            finding(3, "circling", value=100, address=oscillation["hex"]),
        ]
        result = curation.choose([group(oscillation, *context)])
        self.assertFalse(result["selected"])
        self.assertFalse(result["priority_review"])
        descent = finding(4, "rapid_descent", value=-8000, address=oscillation["hex"])
        result = curation.choose([group(oscillation, *context, descent)])
        self.assertEqual([r["finding_id"] for r in result["selected"]], [4])
        self.assertTrue(result["selected"][0]["exceptional"])

    def test_routine_shapes_need_independent_interest_even_with_keep_or_many_loops(self):
        for kind in curation.CONTEXT_KINDS:
            for verdict in (None, "keep"):
                with self.subTest(kind=kind, verdict=verdict):
                    event = finding(1, kind, value=100)
                    metrics = event["evidence"].get("pattern") or event["evidence"]["maneuver"]
                    metrics.update(closed_loops=100, complete_passes=100)
                    event["verdict"] = verdict
                    event["selection"] = selection.assess(event, {})
                    result = curation.choose([group(event)])
                    self.assertFalse(result["selected"])
                    self.assertFalse(result["priority_review"])
                    self.assertEqual(result["decisions"][0]["disposition"], "context_only")

    def test_multiple_routine_shapes_and_a_weak_flag_do_not_qualify_each_other(self):
        events = [
            finding(i, kind, address="abcdef")
            for i, kind in enumerate(("circling", "repeated_passes", "loitering", "military"), 1)
        ]
        self.assertFalse(curation.choose([group(*events)])["selected"])
        rejected = finding(9, "rapid_descent", value=-9000, address="abcdef")
        rejected["verdict"] = "bad_data"
        rejected["selection"] = selection.assess(rejected, {})
        self.assertFalse(curation.choose([group(*events, rejected)])["selected"])

    def test_a_school_aircraft_can_qualify_for_an_independently_supported_event(self):
        circle = finding(1, address="abcdef")
        circle["evidence"]["pattern"]["closed_loops"] = 100
        descent = finding(2, "rapid_descent", value=-8000, address="abcdef")
        for event in (circle, descent):
            event["evidence"]["aircraft"].update(r="N536DC", t="DV20")
        result = curation.choose([group(circle, descent)])
        self.assertEqual([row["finding_id"] for row in result["selected"]], [2])
        self.assertTrue(result["selected"][0]["exceptional"])
        self.assertEqual(result["decisions"][0]["disposition"], "context_only")

    def test_all_supported_exceptional_episodes_are_selected(self):
        groups = [group(finding(i, "rapid_descent", value=-8000)) for i in range(1, 13)]
        result = curation.choose(groups)
        self.assertEqual(len(result["selected"]), 12)
        self.assertTrue(all(row["exceptional"] for row in result["selected"]))
        self.assertEqual([row["rank"] for row in result["selected"]], list(range(1, 13)))

    def test_late_exception_survives_nine_other_exceptional_episodes(self):
        groups = [group(finding(i, "special_squawk_7700", value=7700)) for i in range(1, 10)]
        later = finding(20, "rapid_descent", value=-9000)
        later["first_seen"] += 3600
        later["last_seen"] += 3600
        groups.append(group(later))
        result = curation.choose(groups)
        self.assertIn(20, [row["finding_id"] for row in result["selected"]])
        self.assertEqual(len(result["selected"]), 10)

    def test_later_stronger_same_kind_event_displaces_earlier_exceptional_candidate(self):
        groups = [group(finding(i, "rapid_descent", value=-7000)) for i in range(1, 10)]
        later = finding(20, "rapid_descent", value=-11000)
        later["first_seen"] += 3600
        later["last_seen"] += 3600
        result = curation.choose([*groups, group(later)])
        self.assertEqual(result["selected"][0]["finding_id"], 20)
        self.assertEqual(len(result["selected"]), 10)

    def test_deterministic_selection_and_no_input_mutation(self):
        groups = [group(finding(i, "rapid_descent", value=-4500)) for i in range(1, 12)]
        before = deepcopy(groups)
        selected = curation.choose(groups)["selected"]
        self.assertEqual(selected, curation.choose(list(reversed(groups)))["selected"])
        self.assertEqual(groups, before)
        self.assertEqual(curation.choose(groups), curation.choose(list(reversed(groups))))
        self.assertEqual(selected, curation.choose(groups, audit=False)["selected"])

    def test_quiet_days_are_not_filled_and_course_reversals_do_not_dominate(self):
        groups = [group(finding(i, "course_reversal", value=170)) for i in range(1, 30)]
        self.assertEqual(curation.choose(groups)["selected"], [])
        groups.append(group(finding(31, "rapid_descent", value=-5000)))
        self.assertEqual(len(curation.choose(groups)["selected"]), 1)

    def test_topic_diversity_penalty_preserves_other_worthy_kinds(self):
        groups = [group(finding(i, "rapid_descent", value=-4500)) for i in range(1, 10)]
        groups.append(group(finding(20, "vintage", value=90)))
        result = curation.choose(groups)
        kinds = [row["kind"] for row in result["selected"]]
        self.assertIn("vintage", kinds)
        self.assertEqual(kinds.count("rapid_descent"), 4)

    def test_archive_exception_is_priority_review_without_bypassing_evidence(self):
        event = finding(1, "rapid_descent", value=-9000, flags=("archive_field_freshness_unknown",))
        result = curation.choose([group(event)])
        self.assertFalse(result["selected"])
        self.assertEqual(len(result["priority_review"]), 1)
        self.assertEqual(result["decisions"][0]["disposition"], "needs_review")
        scheduler = curation.choose([group(event)], audit=False)
        self.assertEqual(scheduler["selected"], result["selected"])
        self.assertFalse(scheduler["priority_review"])
        self.assertFalse(scheduler["decisions"])

    def test_explicitly_rejected_events_do_not_return_to_priority_review(self):
        for verdict in ("routine", "bad_data"):
            with self.subTest(verdict=verdict):
                event = finding(1, "rapid_descent", value=-9000)
                event["verdict"] = verdict
                event["selection"] = selection.assess(event, {})
                result = curation.choose([group(event)])
                self.assertFalse(result["selected"])
                self.assertFalse(result["priority_review"])

    def test_implausible_or_conflicting_magnitude_does_not_buy_exception_status(self):
        event = finding(
            1, "rapid_descent", value=-99000, flags=("conflicting_same_time_reports",), confirmed=False
        )
        result = curation.choose([group(event)])
        self.assertFalse(result["selected"])
        self.assertFalse(result["priority_review"])
        event["verdict"] = "keep"
        event["selection"] = selection.assess(event, {})
        self.assertFalse(curation.rate(event)["exceptional"])

    def test_one_episode_with_multiple_qualifying_reasons_consumes_one_slot(self):
        events = [finding(i, "rapid_descent", value=-8000, address="abcdef") for i in (1, 2, 3)]
        result = curation.choose([group(*events)])
        self.assertEqual(len(result["selected"]), 1)
        self.assertEqual(sum(row["disposition"] == "same_episode" for row in result["decisions"]), 2)

    def test_identity_repeat_does_not_hide_a_new_behavior_in_the_same_episode(self):
        first = finding(1, "vintage", value=100, address="abcdef")
        repeated = finding(2, "vintage", value=100, address="abcdef", day=1)
        behavior = finding(3, "rapid_descent", value=-5000, address="abcdef", day=1)
        result = curation.choose([group(first), group(repeated, behavior)])
        self.assertEqual([row["finding_id"] for row in result["selected"]], [1, 3])
        self.assertEqual(
            next(row for row in result["decisions"] if row["finding_id"] == 2)["disposition"],
            "identity_repeat",
        )

    def test_identity_cooldown_expires_and_rejected_findings_do_not_start_it(self):
        def watched(identifier, day):
            event = finding(identifier, "watch_type", value=None, address="abcdef", day=day)
            event["evidence"]["aircraft"]["t"] = "DC3"
            event["selection"] = selection.assess(event, {})
            return event

        groups = [group(watched(i + 1, day)) for i, day in enumerate((0, 1, 29, 30))]
        self.assertEqual([row["finding_id"] for row in curation.choose(groups)["selected"]], [1, 4])
        # A vintage airframe waits 90 days instead.
        groups = [
            group(finding(i + 1, "vintage", value=100, address="abcdef", day=day))
            for i, day in enumerate((0, 30, 89, 90))
        ]
        self.assertEqual([row["finding_id"] for row in curation.choose(groups)["selected"]], [1, 4])
        weak = finding(10, "vintage", value=55, address="fedcba")
        strong = finding(11, "vintage", value=100, address="fedcba", day=1)
        self.assertEqual(
            [row["finding_id"] for row in curation.choose([group(weak), group(strong)])["selected"]], [11]
        )

    def test_a_kind_repeats_for_one_airframe_only_after_thirty_days(self):
        def spiral(identifier, day, address="abcdef"):
            return finding(identifier, "descending_spiral", value=6, address=address, day=day)

        groups = [group(spiral(i + 1, day)) for i, day in enumerate((0, 1, 29, 30))]
        result = curation.choose(groups)
        self.assertEqual([row["finding_id"] for row in result["selected"]], [1, 4])
        self.assertEqual(
            next(row for row in result["decisions"] if row["finding_id"] == 2)["disposition"], "kind_repeat"
        )
        # Another kind, another airframe, and an exceptional repeat all run.
        other_kind = group(finding(5, "rapid_descent", value=-6000, address="abcdef", day=2))
        other_aircraft = group(spiral(6, 2, address="fedcba"))
        exceptional = group(finding(7, "special_squawk_7700", value=7700, address="abcdef", day=3))
        exceptional_again = group(finding(8, "special_squawk_7700", value=7700, address="abcdef", day=4))
        chosen = curation.choose([groups[0], other_kind, other_aircraft, exceptional, exceptional_again])
        self.assertEqual(sorted(row["finding_id"] for row in chosen["selected"]), [1, 5, 6, 7, 8])
        # Squawk kinds share a family: a special and an archive code repeat each other.
        self.assertEqual(
            curation.kind_family("archive_squawk_7600"), curation.kind_family("special_squawk_7600")
        )
        archive = {
            "addresses": ["abcdef"],
            "kind": "archive_squawk_7600",
            "day": "2026-10-06",
            "start": START,
        }
        special = {**archive, "kind": "special_squawk_7600", "day": "2026-10-07", "start": START + 86400}
        self.assertEqual(curation.StoryLog([archive]).repeat(special), "kind_repeat")

    def test_a_series_repeat_on_one_airframe_takes_the_airframe_rules_reason(self):
        # The airframe rules run before the series rule (docs/daily-curation-2026-10-05.md).
        # A watchlist match is an identity kind, never a series.
        self.assertNotIn("watchlist_match", curation.SERIES_KINDS)
        for kind, reason in (
            ("identity_conflict", "identity_repeat"),
            ("seasonal_speed_record", "kind_repeat"),
            ("above_rated_altitude", "kind_repeat"),
        ):
            earlier = {
                "addresses": ["abcdef"],
                "kind": kind,
                "day": "2026-10-06",
                "start": START,
                "series": kind,
            }
            later = {**earlier, "day": "2026-10-08", "start": START + 2 * 86400}
            with self.subTest(kind=kind):
                self.assertEqual(curation.StoryLog([earlier]).repeat(later), reason)
                other = {**later, "addresses": ["fedcba"]}
                self.assertEqual(curation.StoryLog([earlier]).repeat(other), "series_repeat")

    def test_released_history_holds_a_kind_repeat_both_ways_unless_withdrawn(self):
        prior = curation.choose([group(finding(1, "descending_spiral", value=6, address="abcdef"))])
        before = curation.story_entry(prior["selected"][0])
        after = curation.story_entry(
            curation.choose([group(finding(1, "descending_spiral", value=6, address="abcdef", day=20))])[
                "selected"
            ][0]
        )
        candidate = group(finding(2, "descending_spiral", value=6, address="abcdef", day=10))
        for history, disposition in (
            ([before], "kind_repeat"),
            ([after], "kind_repeat"),
            ([{**before, "withdrawn": True}], "selected"),
        ):
            with self.subTest(history=history[0]["day"], withdrawn=history[0].get("withdrawn", False)):
                row = curation.choose([candidate], history=history)["decisions"][0]
                self.assertEqual(row["disposition"], disposition)
                if disposition == "kind_repeat":
                    self.assertIn(curation.REPEAT_REASONS["kind_repeat"], row["reasons"])

    def test_a_held_kind_repeat_gives_its_slot_to_the_next_candidate(self):
        prior = curation.choose([group(finding(1, "descending_spiral", value=6, address="abcdef"))])
        history = [curation.story_entry(prior["selected"][0])]
        # The repeat would outrank every other candidate on this full day.
        repeat = group(finding(2, "descending_spiral", value=6, address="abcdef", day=10))
        kinds = (
            ("rapid_descent", -6000),
            ("tcas_ra", 3),
            ("departure_return", 3),
            ("formation_flight", 3),
            ("possible_go_around", 3),
            ("holding_stack", 3),
            ("fast", 600),
            ("hovering", 3),
            ("vintage", 100),
        )
        others = [group(finding(20 + i, kind, value=value, day=10)) for i, (kind, value) in enumerate(kinds)]
        result = curation.choose([repeat, *others], history=history)
        self.assertEqual(sorted(row["finding_id"] for row in result["selected"]), list(range(20, 29)))
        last = max(result["selected"], key=lambda row: row["rank"])
        self.assertEqual((last["rank"], last["disposition"]), (9, "selected"))
        self.assertEqual(
            next(row for row in result["decisions"] if row["finding_id"] == 2)["disposition"], "kind_repeat"
        )

    def test_two_exceptional_events_per_aircraft_day_bypass_repeat_and_topic_penalties(self):
        # Seven hours apart, so none belongs to a repeated descent profile.
        day = [
            shift(finding(i + 1, "rapid_descent", value=-8000, address="abcdef"), hours * 3600)
            for i, hours in enumerate((-10, -3, 4))
        ]
        groups = [*map(group, day), group(finding(4, "rapid_descent", value=-8000, address="abcdef", day=1))]
        result = curation.choose(groups)
        self.assertEqual([row["finding_id"] for row in result["selected"]], [1, 2, 4])
        self.assertTrue(
            all(
                row["exceptional"] and row["novelty"] == 15 and row["diversity_penalty"] == 0
                for row in result["selected"]
            )
        )
        third = next(row for row in result["decisions"] if row["finding_id"] == 3)
        self.assertFalse(third["exceptional"])
        self.assertEqual(third["disposition"], "same_aircraft_day")

    def test_budget_uses_episode_start_when_primary_finding_crosses_midnight(self):
        event = finding(1, "rapid_descent", value=-8000)
        episode = group(event)
        episode["first_seen"] = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc).timestamp()
        self.assertEqual(curation.choose([episode])["selected"][0]["day"], "2026-09-30")

    def test_exceptional_episodes_count_once_not_per_duplicate_reason(self):
        groups = [group(finding(i, "rapid_descent", value=-11000)) for i in range(1, 10)]
        groups.append(
            group(
                finding(20, "rapid_climb", value=7000, address="abcdef"),
                finding(21, "rapid_descent", value=-7000, address="abcdef"),
            )
        )
        result = curation.choose(groups)
        self.assertEqual(len(result["selected"]), 10)

    def test_a_disagreeing_registry_no_longer_blocks_a_mismatch_story_but_still_blocks_a_type_story(self):
        def with_check(kind, state, code="B752"):
            event = finding(1, kind, value=394, address="abcdef")
            event["evidence"]["aircraft"]["t"] = "C172"
            event["evidence"]["type_check"] = {
                "state": state,
                "source": "adsbdb",
                "type_code": code,
                "listed_type": "C172",
                "checked_at": 1,
            }
            return event

        for kind in sorted(typecheck.REGISTRY_TYPE_KINDS):
            with self.subTest(kind=kind):
                self.assertEqual([rule for rule, _ in curation.gates(with_check(kind, "disagrees"))], [])
                self.assertEqual(curation.type_hold(with_check(kind, "pending"))[0], "type_check_pending")
                # No registry listing the address still leaves the type uncorroborated.
                self.assertIn(
                    "type_uncorroborated",
                    [rule for rule, _ in curation.gates(with_check(kind, "unavailable", code=None))],
                )
        self.assertEqual(
            [rule for rule, _ in curation.gates(with_check("first_type_here", "disagrees"))],
            ["type_disagrees"],
        )


class RecordPlausibilityGateTests(unittest.TestCase):
    """Season records saved under rules-v3 (a flat 150-kn allowance and no rated-limit check)
    are judged again by today's plausibility gate before they can publish."""

    @staticmethod
    def record(typecode, gs, **altitude):
        window = {"sustained_seconds": 75, "samples": 6, "source": "adsb_icao", "baseline_days": 30}
        return {
            "id": 1,
            "hex": "a0b1c2",
            "kind": "seasonal_speed_record",
            "first_seen": START,
            "last_seen": START + 75,
            "value": gs,
            "hits": 6,
            "evidence": {
                "aircraft": {"hex": "a0b1c2", "t": typecode, "type": "adsb_icao", **altitude},
                "season_record": {
                    "type": typecode,
                    "season_start": "2026-09-01",
                    "baseline_start": "2026-09-01",
                    "previous_max_gs": 230,
                    "addresses": 25,
                    **window,
                },
                "type_check": {"state": "agrees", "source": "adsbdb", "type_code": typecode},
            },
        }

    def test_an_implausible_saved_record_is_held_for_review(self):
        for typecode, gs, altitude in (
            ("BE58", 432, {"alt_baro": 5000}),
            # The archive's heatmap altitude picks the same band.
            ("BE58", 432, {"archive_alt": 5000}),
            # A King Air 200 past its 270-KCAS Vmo plus the low band's 60 kn of wind.
            ("BE20", 452, {"alt_baro": 8000}),
        ):
            with self.subTest(typecode=typecode, altitude=altitude):
                event = self.record(typecode, gs, **altitude)
                self.assertIn("speed_implausible", [rule for rule, _reason in curation.gates(event)])
                decision = selection.assess(event, {})
                self.assertTrue(decision["review_required"])
                self.assertIn("speed_implausible", decision["blocking_rules"])
                self.assertFalse(selection.publishable(event))
        plausible = self.record("BE58", 240, alt_baro=5000)
        self.assertEqual(curation.gates(plausible), [])
        self.assertTrue(selection.publishable(plausible))
        # Other kinds never take the record gate.
        mismatch = {**self.record("BE58", 432, alt_baro=5000), "kind": "fast"}
        self.assertEqual(curation.gates(mismatch), [])


if __name__ == "__main__":
    unittest.main()
