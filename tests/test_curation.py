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

        groups = [group(watched(i + 1, day)) for i, day in enumerate((0, 1, 59, 60))]
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

    def test_a_kind_repeats_for_one_airframe_only_after_sixty_days(self):
        def spiral(identifier, day, address="abcdef"):
            return finding(identifier, "descending_spiral", value=6, address=address, day=day)

        groups = [group(spiral(i + 1, day)) for i, day in enumerate((0, 1, 59, 60))]
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
        for kind, reason, series in (
            ("identity_conflict", "identity_repeat", "identity_series"),
            ("seasonal_speed_record", "kind_repeat", "series_repeat"),
            ("above_rated_altitude", "kind_repeat", "series_repeat"),
            # A winds day has no subject airframe, so only its series rule holds it.
            ("winds_aloft_day", "winds_series", "winds_series"),
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
                self.assertEqual(curation.StoryLog([earlier]).repeat(other), series)

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
                # A pending check holds selection until STALE_CHECK_SECONDS after the first sighting.
                self.assertEqual(
                    curation.type_hold(with_check(kind, "pending"), now=START)[0], "type_check_pending"
                )
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


def _story_event(kind, address, evidence=None):
    """A minimal saved finding for the repeat-policy facts."""
    return {
        "hex": address,
        "kind": kind,
        "evidence": {"aircraft": {"t": "C172"}, **(evidence or {})},
    }


def prior_story(*, kind, address, start, live=True, evidence=None):
    """A StoryLog entry, as story_entry() or the publication ledger writes one."""
    stamp = START + start
    return {
        "id": None,
        "episode_key": f"{address}:{stamp:.6f}",
        "kind": kind,
        "start": stamp,
        "day": curation.utc_day(stamp),
        "live": live,
        **curation.facts(_story_event(kind, address, evidence)),
    }


def candidate_row(*, kind, address, start, evidence=None):
    """The repeat-policy part of a rated row (_rate_group)."""
    stamp = START + start
    return {
        "id": None,
        "kind": kind,
        "start": stamp,
        "day": curation.utc_day(stamp),
        **curation.facts(_story_event(kind, address, evidence)),
    }


def unregistered_event(*, first_seen, checked_at, state, source=None, registration=None):
    return {
        "id": 1,
        "hex": "a1b2c3",
        "kind": "unregistered_address",
        "first_seen": first_seen,
        "last_seen": first_seen + 600,
        "value": None,
        "hits": 40,
        "evidence": {
            "aircraft": {"hex": "a1b2c3", "t": "C172", "type": "adsb_icao"},
            "type_check": {
                "state": state,
                "source": source,
                "type_code": None,
                "registration": registration,
                "listed_type": "C172",
                "checked_at": checked_at,
            },
        },
    }


def pending_event(*, first_seen, kind="first_type_here"):
    return {
        "id": 1,
        "hex": "a1b2c3",
        "kind": kind,
        "first_seen": first_seen,
        "last_seen": first_seen + 600,
        "value": None,
        "hits": 40,
        "evidence": {
            "aircraft": {"hex": "a1b2c3", "t": "C172", "type": "adsb_icao"},
            "type_check": {"state": "pending"},
        },
    }


class NewCooldowns(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(curation.STORY_COOLDOWN, 60 * 86400)
        self.assertEqual(curation.TYPE_COOLDOWN, 30 * 86400)
        self.assertEqual(curation.VARIANT_COOLDOWN, 14 * 86400)
        self.assertEqual(curation.WATCHLIST_COOLDOWN, 90 * 86400)
        self.assertEqual(curation.IDENTITY_SERIES_COOLDOWN, 21 * 86400)
        self.assertEqual(curation.WINDS_COOLDOWN, 3 * 86400)
        self.assertEqual(curation.UNREGISTERED_COOLDOWN, 90 * 86400)
        self.assertEqual(curation.REGISTRY_LAG_SECONDS, 7 * 86400)
        self.assertEqual(curation.STALE_CHECK_SECONDS, 30 * 86400)

    def test_watchlist_repeat_inside_90_days_is_held(self):
        log = curation.StoryLog()
        log.add(prior_story(kind="watchlist_match", address="a1b2c3", start=0, live=True))
        row = candidate_row(kind="watchlist_match", address="a1b2c3", start=80 * 86400)
        self.assertEqual(log.repeat(row), "watchlist_repeat")
        self.assertIsNone(
            log.repeat(candidate_row(kind="watchlist_match", address="a1b2c3", start=91 * 86400))
        )
        self.assertIsNone(
            log.repeat(candidate_row(kind="watchlist_match", address="d4e5f6", start=80 * 86400))
        )

    def test_unregistered_repeat_inside_90_days_is_held(self):
        log = curation.StoryLog()
        log.add(prior_story(kind="unregistered_address", address="a1b2c3", start=0))
        # identity_repeat holds the first 60 days; this rule the rest of the 90.
        row = candidate_row(kind="unregistered_address", address="a1b2c3", start=75 * 86400)
        self.assertEqual(log.repeat(row), "unregistered_repeat")
        self.assertIn("90 days", curation.REPEAT_REASONS["unregistered_repeat"])
        self.assertIsNone(
            log.repeat(candidate_row(kind="unregistered_address", address="a1b2c3", start=91 * 86400))
        )

    def test_identity_conflict_series_is_21_days(self):
        log = curation.StoryLog()
        log.add(prior_story(kind="identity_conflict", address="aaaaaa", start=0, live=True))
        self.assertEqual(
            log.repeat(candidate_row(kind="identity_conflict", address="bbbbbb", start=10 * 86400)),
            "identity_series",
        )
        self.assertIsNone(
            log.repeat(candidate_row(kind="identity_conflict", address="bbbbbb", start=22 * 86400))
        )

    def test_winds_aloft_series_is_3_days(self):
        log = curation.StoryLog()
        log.add(prior_story(kind="winds_aloft_day", address="aaaaaa", start=0))
        self.assertEqual(
            log.repeat(candidate_row(kind="winds_aloft_day", address="bbbbbb", start=2 * 86400)),
            "winds_series",
        )
        self.assertIsNone(
            log.repeat(candidate_row(kind="winds_aloft_day", address="bbbbbb", start=4 * 86400))
        )

    def test_other_series_kinds_keep_seven_days(self):
        log = curation.StoryLog()
        log.add(prior_story(kind="seasonal_speed_record", address="aaaaaa", start=0))
        self.assertEqual(
            log.repeat(candidate_row(kind="seasonal_speed_record", address="bbbbbb", start=6 * 86400)),
            "series_repeat",
        )
        self.assertIsNone(
            log.repeat(candidate_row(kind="seasonal_speed_record", address="bbbbbb", start=8 * 86400))
        )

    def test_a_kind_repeats_on_one_airframe_after_sixty_days(self):
        log = curation.StoryLog()
        log.add(prior_story(kind="descending_spiral", address="aaaaaa", start=0))
        self.assertEqual(
            log.repeat(candidate_row(kind="descending_spiral", address="aaaaaa", start=59 * 86400)),
            "kind_repeat",
        )
        self.assertIsNone(
            log.repeat(candidate_row(kind="descending_spiral", address="aaaaaa", start=61 * 86400))
        )

    def test_foreign_military_deployment_is_one_story_a_fortnight(self):
        def evidence(country, typecode="A400"):
            return {"aircraft": {"t": typecode}, "foreign_military": {"country": country}}

        self.assertEqual(
            curation.facts(_story_event("foreign_military", "3b0001", evidence("FR")))["variant"], "FR/A400"
        )
        log = curation.StoryLog()
        log.add(prior_story(kind="foreign_military", address="3b0001", start=0, evidence=evidence("FR")))
        same = candidate_row(
            kind="foreign_military", address="3b0002", start=10 * 86400, evidence=evidence("FR")
        )
        self.assertEqual(log.repeat(same), "variant_repeat")
        later = candidate_row(
            kind="foreign_military", address="3b0002", start=15 * 86400, evidence=evidence("FR")
        )
        self.assertIsNone(log.repeat(later))
        for other in (evidence("DE"), evidence("FR", "C130")):
            with self.subTest(other=other):
                row = candidate_row(
                    kind="foreign_military", address="3b0002", start=10 * 86400, evidence=other
                )
                self.assertIsNone(log.repeat(row))

    def test_identity_cooldown_is_60_days(self):
        self.assertEqual(curation.IDENTITY_COOLDOWN, 60 * 86400)
        log = curation.StoryLog()
        log.add(prior_story(kind="military", address="a1b2c3", start=0))
        self.assertEqual(
            log.repeat(candidate_row(kind="watch_type", address="a1b2c3", start=59 * 86400)),
            "identity_repeat",
        )
        self.assertIsNone(log.repeat(candidate_row(kind="watch_type", address="a1b2c3", start=61 * 86400)))

    def test_go_around_series_is_7_days(self):
        self.assertEqual(curation.GO_AROUND_COOLDOWN, 7 * 86400)
        self.assertEqual(
            curation.facts(_story_event("possible_go_around", "aaaaaa"))["series"], "possible_go_around"
        )
        log = curation.StoryLog()
        log.add(prior_story(kind="possible_go_around", address="aaaaaa", start=0))
        self.assertEqual(
            log.repeat(candidate_row(kind="possible_go_around", address="bbbbbb", start=6 * 86400)),
            "go_around_series",
        )
        self.assertIsNone(
            log.repeat(candidate_row(kind="possible_go_around", address="bbbbbb", start=8 * 86400))
        )

    def test_a_subjectless_story_takes_no_address_repeat(self):
        self.assertEqual(curation.SUBJECTLESS_KINDS, frozenset({"winds_aloft_day"}))
        log = curation.StoryLog()
        log.add(prior_story(kind="winds_aloft_day", address="aaaaaa", start=0))
        # Only the series rule applies, in either direction.
        self.assertIsNone(
            log.repeat(candidate_row(kind="winds_aloft_day", address="aaaaaa", start=4 * 86400))
        )
        self.assertIsNone(log.repeat(candidate_row(kind="identity_conflict", address="aaaaaa", start=3600)))
        log = curation.StoryLog()
        log.add(prior_story(kind="identity_conflict", address="aaaaaa", start=0))
        self.assertIsNone(log.repeat(candidate_row(kind="winds_aloft_day", address="aaaaaa", start=3600)))

    def test_two_winds_days_four_days_apart_with_one_primary_both_select(self):
        def winds(identifier, day):
            event = finding(identifier, "winds_aloft_day", value=None, address="abcdef", day=day)
            event["evidence"]["participants"] = [{"hex": "a00001"}, {"hex": "a00002"}]
            event["selection"] = selection.assess(event, {})
            return event

        self.assertTrue(winds(1, 0)["selection"]["story_eligible"])
        result = curation.choose([group(winds(1, 0)), group(winds(2, 4))])
        self.assertEqual([row["finding_id"] for row in result["selected"]], [1, 2])
        # A participant's own story on the same day is not held by the winds day.
        other = finding(3, "descending_spiral", value=6, address="a00001")
        result = curation.choose([group(winds(1, 0)), group(other)])
        self.assertEqual(sorted(row["finding_id"] for row in result["selected"]), [1, 3])
        # The series still holds a winds day inside three days.
        result = curation.choose([group(winds(1, 0)), group(winds(2, 2))])
        self.assertEqual([row["finding_id"] for row in result["selected"]], [1])

    def test_operator_firsts_are_keyed_by_designator(self):
        def first(designator):
            return {
                "aircraft": {"t": "A320"},
                "first_sighting": {"scope": "operator", "designator": designator},
            }

        self.assertEqual(
            curation.facts(_story_event("first_operator_here", "a00001", first("ABC")))["subtopic"],
            "operator:ABC",
        )
        log = curation.StoryLog()
        log.add(prior_story(kind="first_operator_here", address="a00001", start=0, evidence=first("ABC")))
        # Another operator's first on the same type is its own story, at any distance.
        other = candidate_row(
            kind="first_operator_here", address="a00002", start=86400, evidence=first("XYZ")
        )
        self.assertIsNone(log.repeat(other))
        # A second first for the same designator would be false.
        again = candidate_row(
            kind="first_operator_here", address="a00003", start=400 * 86400, evidence=first("ABC")
        )
        self.assertEqual(log.repeat(again), "first_repeat")

    def test_a_routine_type_does_not_hold_an_operator_first(self):
        def first(designator):
            return {
                "aircraft": {"t": "C172"},
                "first_sighting": {"scope": "operator", "designator": designator},
            }

        log = curation.StoryLog()
        log.add(prior_story(kind="watch_type", address="a00001", start=0))
        log.add(prior_story(kind="first_operator_here", address="a00002", start=0, evidence=first("ABC")))
        row = candidate_row(kind="first_operator_here", address="a00003", start=86400, evidence=first("XYZ"))
        self.assertIsNone(log.repeat(row, frozenset({"C172"})))
        # Another identity story on the routine type is still held, but not by the operator first.
        log = curation.StoryLog()
        log.add(prior_story(kind="first_operator_here", address="a00002", start=0, evidence=first("ABC")))
        row = candidate_row(kind="military", address="a00004", start=86400)
        self.assertIsNone(log.repeat(row, frozenset({"C172"})))
        log.add(prior_story(kind="watch_type", address="a00001", start=0))
        self.assertEqual(log.repeat(row, frozenset({"C172"})), "routine_type")

    def test_the_curation_version_is_12(self):
        self.assertEqual(curation.VERSION, 12)


class RegistryLagHold(unittest.TestCase):
    def test_unregistered_waits_a_week_then_a_fresh_check(self):
        now = 1_700_000_000.0
        event = unregistered_event(
            first_seen=now - 3 * 86400, checked_at=now - 3 * 86400, state="unavailable"
        )
        self.assertEqual(curation.type_hold(event, now=now)[0], "registry_lag")
        event = unregistered_event(
            first_seen=now - 8 * 86400, checked_at=now - 8 * 86400, state="unavailable"
        )
        self.assertEqual(curation.type_hold(event, now=now)[0], "type_check_stale")
        event = unregistered_event(first_seen=now - 8 * 86400, checked_at=now - 60, state="unavailable")
        self.assertIsNone(curation.type_hold(event, now=now))

    def test_pending_check_older_than_30_days_is_not_a_hold(self):
        now = 1_700_000_000.0
        event = pending_event(first_seen=now - 31 * 86400)
        self.assertIsNone(curation.type_hold(event, now=now))
        self.assertEqual(
            curation.type_hold(pending_event(first_seen=now - 29 * 86400), now=now)[0], "type_check_pending"
        )

    def test_a_stale_check_leaves_a_performance_claim_uncorroborated(self):
        now = 1_700_000_000.0
        stale = pending_event(first_seen=now - 31 * 86400, kind="type_speed_mismatch")
        self.assertTrue(curation.stale_check(stale, now))
        self.assertIn("type_uncorroborated", [rule for rule, _ in curation.gates(stale, now=now)])
        fresh = pending_event(first_seen=now - 3 * 86400, kind="type_speed_mismatch")
        self.assertNotIn("type_uncorroborated", [rule for rule, _ in curation.gates(fresh, now=now)])
        # An identity story stays publishable on the database alone.
        identity = pending_event(first_seen=now - 31 * 86400)
        self.assertNotIn("type_uncorroborated", [rule for rule, _ in curation.gates(identity, now=now)])

    def test_an_unlisted_address_check_never_goes_stale(self):
        now = 1_700_000_000.0
        event = unregistered_event(first_seen=now - 40 * 86400, checked_at=None, state="pending")
        self.assertFalse(curation.stale_check(event, now))
        self.assertIsNotNone(curation.type_hold(event, now=now))

    def test_unlisted_address_gates(self):
        now = 1_700_000_000.0
        settled = unregistered_event(first_seen=now - 8 * 86400, checked_at=now - 60, state="unavailable")
        self.assertEqual(curation.gates(settled, now=now), [])
        early = unregistered_event(
            first_seen=now - 8 * 86400, checked_at=now - 8 * 86400, state="unavailable"
        )
        self.assertEqual([rule for rule, _ in curation.gates(early, now=now)], ["registry_unchecked"])
        listed = unregistered_event(
            first_seen=now - 8 * 86400,
            checked_at=now - 60,
            state="unavailable",
            source="hexdb",
            registration="N123AB",
        )
        rules = curation.gates(listed, now=now)
        self.assertEqual([rule for rule, _ in rules], ["registry_lists_address"])
        self.assertIn("hexdb", rules[0][1])
        self.assertIn("N123AB", rules[0][1])


class ReadingPlausibilityGate(unittest.TestCase):
    @staticmethod
    def sighting(kind="first_type_here", **aircraft_fields):
        return {
            "id": 1,
            "hex": "a1b2c3",
            "kind": kind,
            "first_seen": START,
            "last_seen": START + 600,
            "hits": 40,
            "evidence": {
                "aircraft": {"hex": "a1b2c3", "t": "C172", "type": "adsb_icao", **aircraft_fields},
                "type_check": {
                    "state": "agrees",
                    "source": "adsbdb",
                    "type_code": "C172",
                    "listed_type": "C172",
                },
            },
        }

    def rules(self, event):
        return [rule for rule, _ in curation.gates(event, now=START)]

    def test_a_direct_speed_beyond_the_envelope_is_implausible(self):
        for kind in sorted(curation.PLAUSIBILITY_KINDS):
            with self.subTest(kind=kind):
                self.assertIn("reading_implausible", self.rules(self.sighting(kind, gs=400, alt_baro=5000)))
        self.assertNotIn("reading_implausible", self.rules(self.sighting(gs=110, alt_baro=5000)))
        # Other kinds never take the gate.
        self.assertNotIn("reading_implausible", self.rules(self.sighting("military", gs=400, alt_baro=5000)))

    def test_a_position_derived_speed_is_no_evidence(self):
        self.assertNotIn("reading_implausible", self.rules(self.sighting(gs=400, alt_baro=5000, type="mlat")))
        self.assertNotIn("reading_implausible", self.rules(self.sighting(gs=400, alt_baro=5000, mlat=["gs"])))

    def test_an_altitude_above_the_ceiling_margin_is_implausible(self):
        # The C172 envelope's ceiling is 15,000 ft; 2,000 ft above it is allowed.
        self.assertNotIn("reading_implausible", self.rules(self.sighting(alt_baro=16900)))
        self.assertIn("reading_implausible", self.rules(self.sighting(alt_baro=17100)))
        # Altitude applies whatever the speed's source.
        self.assertIn("reading_implausible", self.rules(self.sighting(alt_baro=17100, type="mlat")))
        decision = selection.assess(self.sighting(alt_baro=17100), {}, now=START)
        self.assertTrue(decision["review_required"])
        self.assertIn("reading_implausible", decision["blocking_rules"])


class ContextRules(unittest.TestCase):
    def test_aerobatic_practice_near_an_airport_is_context(self):
        for tag in ("terminal_area_context", "runway_circuit_context"):
            with self.subTest(tag=tag):
                self.assertTrue(
                    curation.context_only("aerobatic_practice", {"maneuver": {"context_tags": [tag]}})
                )
        self.assertFalse(curation.context_only("aerobatic_practice", {"maneuver": {"context_tags": []}}))
        self.assertFalse(curation.context_only("aerobatic_practice", {}))


class RuleMetricsAndRecords(unittest.TestCase):
    def test_descent_rule_metrics_are_accepted(self):
        from wtf_adsb_publisher import episode_evidence, maneuvers

        for key in ("threshold_fpm", "qualifying_seconds_required", "qualifying_reports_required"):
            with self.subTest(key=key):
                self.assertIn(key, maneuvers.METRICS)
                self.assertIn(key, episode_evidence.METRICS)
                self.assertIn(key, episode_evidence.RULE_METRICS)
        rule = {"qualifying_seconds_required": 30, "qualifying_reports_required": 4}
        summary = episode_evidence.summary(rule)
        self.assertEqual({key: summary.get(key) for key in rule}, rule)

    def test_the_season_record_keeps_its_margin(self):
        from wtf_adsb_publisher import stories

        block = stories.season_record({"previous_max_gs": 230, "margin_kn": 12.5, "baseline_days": 31})
        self.assertEqual(block["margin_kn"], 12.5)
        for bad in (-1, True, "12", float("nan")):
            with self.subTest(bad=bad):
                self.assertNotIn(
                    "margin_kn", stories.season_record({"previous_max_gs": 230, "margin_kn": bad})
                )

    def test_a_record_needs_thirty_baseline_days_and_a_clear_margin(self):
        from wtf_adsb_publisher import rules

        self.assertEqual(rules.SEASON_MIN_BASELINE_DAYS, 30)
        self.assertEqual(rules.RECORD_MARGIN, (10, 0.03))
        aircraft = {"hex": "a0b1c2", "t": "BE58", "type": "adsb_icao", "alt_baro": 5000}
        sustained = {"gs": 245, "duration_seconds": 75, "samples": 6, "source": "adsb_icao"}

        def record(gs=245, **season):
            base = {
                "type": "BE58",
                "season_start": "2026-09-01",
                "max_gs": 230,
                "addresses": 25,
                "baseline_days": 30,
            }
            return rules.seasonal_speed_record(
                aircraft, {**base, **season}, sustained={**sustained, "gs": gs}
            )

        found = record()
        self.assertIsNotNone(found)
        self.assertEqual(found.evidence["season_record"]["margin_kn"], 15.0)
        self.assertIsNone(record(baseline_days=29))
        # Below about 333 kn the fixed 10 kn is the larger margin; above it, 3 percent.
        self.assertIsNone(record(max_gs=236))
        self.assertIsNotNone(record(max_gs=235))
        self.assertIsNone(record(gs=411, max_gs=400))
        self.assertIsNotNone(record(gs=412, max_gs=400))


if __name__ == "__main__":
    unittest.main()
