"""Selection scores for the pattern, episode, rarity, and emitter kinds."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import reference_fixture

from wtf_adsb_publisher import curation, selection, typecheck

START = datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp()
NOW = START + 3600


def setUpModule():
    reference_fixture.install()


def event(kind, *, aircraft=None, **evidence):
    """One saved finding of `kind` with the given evidence blocks and a settled type check."""
    block = {"aircraft": {"hex": "a1b2c3", "t": "C172", "type": "adsb_icao", **(aircraft or {})}, **evidence}
    if kind in typecheck.TYPE_DEPENDENT_KINDS:
        block["type_check"] = {
            "state": "agrees",
            "source": "adsbdb",
            "type_code": "C172",
            "listed_type": "C172",
            "checked_at": START,
        }
    return {
        "id": 1,
        "hex": "a1b2c3",
        "kind": kind,
        "first_seen": START,
        "last_seen": START + 900,
        "value": None,
        "hits": 40,
        "evidence": block,
    }


def assess(item):
    return selection.assess(item, {}, now=NOW)


def episode(**fields):
    return {"confirmed": True, "quality_review_required": False, "quality_flags": [], **fields}


class ReopenedKinds(unittest.TestCase):
    def test_rare_here_with_one_prior_day_is_story_eligible(self):
        result = assess(event("rare_here", rarity={"aircraft_days": 0, "type_days": 1}))
        self.assertTrue(result["story_eligible"])
        self.assertEqual(result["score"], 75)

    def test_rare_here_seen_on_more_days_stays_below_a_story(self):
        for rarity, score in (
            ({"aircraft_days": 2, "type_days": 1}, 70),
            ({"aircraft_days": 0, "type_days": 3}, 70),
            ({"aircraft_days": 3, "type_days": 1}, 35),
            ({"aircraft_days": 0, "type_days": 4}, 35),
        ):
            with self.subTest(rarity=rarity):
                result = assess(event("rare_here", rarity=rarity))
                self.assertEqual(result["score"], score)
                self.assertFalse(result["story_eligible"])

    def test_prolonged_holding_twenty_minutes_is_a_story(self):
        result = assess(event("prolonged_holding", maneuver=episode(duration_seconds=1300)))
        self.assertTrue(result["story_eligible"])
        self.assertEqual(result["score"], 76)
        self.assertFalse(curation.context_only("prolonged_holding", {"maneuver": {"duration_seconds": 1200}}))
        result = assess(event("prolonged_holding", maneuver=episode(duration_seconds=700)))
        self.assertFalse(result["story_eligible"])
        self.assertTrue(curation.context_only("prolonged_holding", {"maneuver": {"duration_seconds": 1199}}))
        self.assertTrue(curation.context_only("prolonged_holding", {}))

    def test_go_around_without_quality_flags_is_a_story(self):
        result = assess(
            event(
                "possible_go_around",
                maneuver=episode(context_tags=["airport_maneuver_hypothesis"]),
            )
        )
        self.assertTrue(result["story_eligible"])
        self.assertEqual(result["score"], 75)

    def test_go_around_with_low_position_quality_is_not_a_story(self):
        flagged = episode(quality_flags=["low_position_quality"], quality_review_required=True)
        result = assess(event("possible_go_around", maneuver=flagged))
        self.assertFalse(result["story_eligible"])
        # The flag alone lowers the score, whatever the review field says.
        result = assess(event("possible_go_around", maneuver={**flagged, "quality_review_required": False}))
        self.assertFalse(result["story_eligible"])

    def test_new_pattern_kinds_score_like_circling(self):
        for kind in ("aerobatic_practice", "racetrack_orbit"):
            with self.subTest(kind=kind):
                result = assess(event(kind, maneuver=episode(shape_verified=True)))
                self.assertTrue(result["story_eligible"], kind)
                self.assertEqual(result["score"], 80)
                # The engine's episode carries no shape_verified; its confirmation window stands for it.
                self.assertTrue(assess(event(kind, maneuver=episode()))["story_eligible"])
                review = episode(quality_review_required=True, quality_flags=["low_position_quality"])
                result = assess(event(kind, maneuver=review))
                self.assertFalse(result["story_eligible"])
                self.assertIn("Source or geometry needs review before promotion", result["reasons"])

    def test_aerobatic_practice_near_an_airport_stays_context(self):
        near = episode(context_tags=["terminal_area_context"])
        result = assess(event("aerobatic_practice", maneuver=near))
        self.assertFalse(result["story_eligible"])
        self.assertTrue(assess(event("racetrack_orbit", maneuver=near))["story_eligible"])

    def test_a_saved_pattern_block_still_needs_its_shape_check(self):
        result = assess(event("circling", pattern={"quality_review_required": False}))
        self.assertIn("Saved pattern predates the closure/corridor checks", result["reasons"])


class EmitterAndFixedScores(unittest.TestCase):
    def test_a_glider_category_on_a_glider_type_is_a_story(self):
        result = assess(event("unusual_emitter", aircraft={"category": "B1", "t": "GLID"}))
        self.assertEqual(result["score"], 75)
        self.assertTrue(result["story_eligible"])

    def test_other_emitter_categories_keep_their_fixed_score(self):
        for category, typecode in (("B1", "C172"), ("B2", "GLID"), ("B4", "GLID"), ("B6", None)):
            with self.subTest(category=category, typecode=typecode):
                result = assess(event("unusual_emitter", aircraft={"category": category, "t": typecode}))
                self.assertEqual(result["score"], 65)
                self.assertFalse(result["story_eligible"])

    def test_fixed_scores_come_from_the_registry(self):
        for kind, score in (
            ("holding_stack", 78),
            ("identity_conflict", 70),
            ("winds_aloft_day", 80),
            ("foreign_military", 80),
            ("unregistered_address", 75),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(selection.FIXED_SCORES[kind][0], score)


if __name__ == "__main__":
    unittest.main()
