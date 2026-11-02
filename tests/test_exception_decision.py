"""The selection's exceptional decision is made once, with the reason a story is ordinary."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import curation, selection
from wtf_adsb_publisher.curation import exception_decision


# The live project's test_selection_policy helpers, which this repository does not carry.
def status(identifier, kind="special_squawk_7700", *, address=None, **metrics):
    event = finding(identifier, kind, value=None, address=address)
    event["evidence"]["maneuver"].update(metrics)
    event["selection"] = selection.assess(event, {})
    return event


def descent(identifier, address, *, hours=0, value=-8000, typecode="C172", tags=(), **metrics):
    event = shift(finding(identifier, "rapid_descent", value=value, address=address), hours * 3600)
    event["evidence"]["aircraft"]["t"] = typecode
    event["evidence"]["maneuver"].update(metrics, context_tags=list(tags))
    event["selection"] = selection.assess(event, {})
    return event


from test_curation import finding, group, shift


class ExceptionDecision(unittest.TestCase):
    def test_a_graded_exception_is_exceptional_without_a_reason(self):
        event = finding(1, "rapid_descent", value=-8000)
        self.assertEqual(exception_decision(event), {"grade": True, "exceptional": True, "reason": None})
        self.assertTrue(curation.exceptional_event(event))

    def test_a_graded_descent_repeated_within_six_hours_is_ordinary_for_that_reason(self):
        first, second = descent(1, "abcdef"), descent(2, "abcdef", hours=2)
        descents = curation.descent_starts([group(first), group(second)])
        self.assertEqual(
            exception_decision(first, descents),
            {"grade": True, "exceptional": False, "reason": "repeated_descent"},
        )
        self.assertFalse(curation.exceptional_event(first, descents))
        # Without the descent context the repeat rule cannot apply.
        self.assertEqual(exception_decision(first)["reason"], None)
        # The emergency profile from high altitude overrides the repeat rule.
        steep = descent(3, "fedcba", tags=["emergency_descent_profile"], start_altitude_ft=31000)
        again = descent(4, "fedcba", hours=2)
        self.assertIsNone(
            exception_decision(steep, curation.descent_starts([group(steep), group(again)]))["reason"]
        )

    def test_a_crewed_7400_is_ordinary_with_its_reason(self):
        crewed = status(1, "special_squawk_7400")
        self.assertEqual(
            exception_decision(crewed), {"grade": True, "exceptional": False, "reason": "crewed_7400"}
        )
        self.assertFalse(curation.exceptional_event(crewed))
        crewed["evidence"]["aircraft"]["category"] = "B6"
        self.assertEqual(exception_decision(crewed)["reason"], None)
        moved = status(2, "special_squawk_7400", code_sequence=[{"code": "7400"}, {"code": "7700"}])
        self.assertEqual(exception_decision(moved)["reason"], None)

    def test_a_jump_type_near_its_runway_is_ordinary_with_its_reason(self):
        jump = descent(1, "abcdef", typecode="C208", tags=["airport_descent_context"])
        self.assertEqual(
            exception_decision(jump), {"grade": True, "exceptional": False, "reason": "jump_type"}
        )
        # Decided from the finding alone, like no_grade and crewed_7400, so the rerun of a saved
        # finding with this reason is never exceptional and a stored one cannot outlast it.
        self.assertFalse(curation.exceptional_event(jump))

    def test_an_ungraded_event_reports_no_grade(self):
        for event in (finding(1, "rapid_descent", value=-5000), finding(2, "vintage", value=90)):
            with self.subTest(kind=event["kind"]):
                self.assertEqual(
                    exception_decision(event), {"grade": False, "exceptional": False, "reason": "no_grade"}
                )
                self.assertFalse(curation.exceptional_event(event))

    def test_eligibility_and_review_are_reasons_after_the_grade(self):
        event = finding(1, "rapid_descent", value=-8000)
        self.assertEqual(
            exception_decision(event, eligible=False),
            {"grade": True, "exceptional": False, "reason": "ineligible"},
        )
        self.assertEqual(
            exception_decision(event, review_required=True),
            {"grade": True, "exceptional": False, "reason": "review"},
        )

    def test_reasons_follow_the_precedence_order(self):
        weak = finding(1, "rapid_descent", value=-5000)
        self.assertEqual(exception_decision(weak, eligible=False, review_required=True)["reason"], "no_grade")
        crewed = status(2, "special_squawk_7400")
        self.assertEqual(
            exception_decision(crewed, eligible=False, review_required=True)["reason"], "ineligible"
        )
        self.assertEqual(exception_decision(crewed, review_required=True)["reason"], "review")
        self.assertEqual(exception_decision(crewed)["reason"], "crewed_7400")
        loads = [descent(3, "a8b1e4", typecode="BE9L", tags=["airport_descent_context"])]
        loads.append(descent(4, "a8b1e4", hours=1, typecode="BE9L", tags=["airport_descent_context"]))
        descents = curation.descent_starts([group(event) for event in loads])
        self.assertEqual(exception_decision(loads[0], descents)["reason"], "jump_type")
        self.assertEqual(
            curation.EXCEPTION_REASONS,
            ("no_grade", "ineligible", "review", "crewed_7400", "jump_type", "repeated_descent"),
        )

    def test_the_decision_agrees_with_the_rated_row(self):
        events = [
            finding(1, "rapid_descent", value=-8000),
            finding(2, "rapid_descent", value=-5000),
            status(3, "special_squawk_7400"),
            status(4),
            descent(5, "abcdef"),
            descent(6, "abcdef", hours=1),
        ]
        descents = curation.descent_starts([group(event) for event in events])
        for event in events:
            with self.subTest(identifier=event["id"]):
                row = curation.rate(event, descents=descents)
                made = exception_decision(
                    event,
                    descents,
                    eligible=row["eligible"],
                    review_required=event["selection"]["review_required"],
                )
                self.assertEqual(made["exceptional"], row["exceptional"])

    def test_rate_rows_keep_their_shape(self):
        self.assertNotIn("exception", curation.rate(finding(1, "rapid_descent", value=-8000)))


class SelectedException(unittest.TestCase):
    def test_the_block_reflects_the_aircraft_cap_and_a_kept_standing(self):
        event = finding(1, "rapid_descent", value=-8000)
        row = curation.rate(event)
        block = curation.selected_exception(event, row)
        self.assertEqual(
            block,
            {"grade": True, "exceptional": True, "reason": None, "curation_version": curation.VERSION},
        )
        capped = curation.selected_exception(event, {**row, "exceptional": False})
        self.assertEqual((capped["exceptional"], capped["reason"]), (False, "aircraft_cap"))
        # A live story's own finding keeps its standing through a later repeat.
        first, second = descent(2, "abcdef"), descent(3, "abcdef", hours=2)
        descents = curation.descent_starts([group(first), group(second)])
        kept = curation.selected_exception(first, {**curation.rate(first), "exceptional": True}, descents)
        self.assertEqual((kept["exceptional"], kept["reason"]), (True, None))
        repeated = curation.selected_exception(first, curation.rate(first, descents=descents), descents)
        self.assertEqual((repeated["exceptional"], repeated["reason"]), (False, "repeated_descent"))


if __name__ == "__main__":
    unittest.main()
