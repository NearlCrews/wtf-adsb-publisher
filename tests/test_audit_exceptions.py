"""tools/audit_exceptions.py compares each reader's answer before and after the stored decision."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from test_autopilot import NOW, candidate, exceptional_descent

from tools import audit_exceptions
from wtf_adsb_publisher import autopilot
from wtf_adsb_publisher import public_events as public


class ExceptionAuditTests(unittest.TestCase):
    def ledger(self):
        ledger = public.new_ledger("2026-10-01")
        older = candidate(2, "rapid_descent", -9000)
        older["exceptional"] = True
        autopilot.stage(ledger, [exceptional_descent(1), older, exceptional_descent(3)], at=NOW)
        agreeing, older, repeated = (
            next(row for row in ledger["records"] if row["episode_key"] == str(identifier))
            for identifier in (1, 2, 3)
        )
        self.assertIn("exception", agreeing["automation"])
        self.assertNotIn("exception", older["automation"])
        for record in ledger["records"]:
            # Released stories are prior stories too, so the `_story` entry is compared.
            record["automation"]["released_at"] = NOW
        # A stored repeated descent without the boolean, which the rerun would promote.
        repeated["automation"]["exception"].update(exceptional=False, reason="repeated_descent")
        del repeated["automation"]["exceptional"]
        return ledger

    def test_an_expected_difference_is_reported_under_its_reason_not_as_a_change(self):
        summary = audit_exceptions.report(self.ledger())
        self.assertEqual((summary["records"], summary["expected"], summary["changes"]), (3, 2, 0))
        self.assertEqual(
            sorted((item["episode_key"], item["reader"]) for item in summary["differences"]),
            [("3", "_story"), ("3", "exception_standing")],
        )
        for item in summary["differences"]:
            self.assertEqual(
                (item["old"], item["new"], item["reason"], item["expected"]),
                (True, False, "repeated_descent", True),
            )

    def test_a_story_released_as_exceptional_has_no_sticky_reader(self):
        ledger = self.ledger()
        repeated = next(row for row in ledger["records"] if row["episode_key"] == "3")
        repeated["automation"].update(
            published_event=autopilot._project(repeated), published_exceptional=True
        )
        # Its release receipt decides its entry, and a sticky reader would reduce to the rerun.
        summary = audit_exceptions.report(ledger)
        self.assertEqual(
            [(item["episode_key"], item["reader"]) for item in summary["differences"]],
            [("3", "exception_standing")],
        )

    def test_any_other_difference_is_a_change_and_fails_the_tool(self):
        ledger = self.ledger()
        agreeing = next(row for row in ledger["records"] if row["episode_key"] == "1")
        # A stored review demotion without the boolean: the rerun would promote it, but a
        # review is not a demotion the refactor expects to change.
        agreeing["automation"]["exception"].update(exceptional=False, reason="review")
        del agreeing["automation"]["exceptional"]
        summary = audit_exceptions.report(ledger)
        self.assertEqual(summary["changes"], 2)
        changes = [item for item in summary["differences"] if not item["expected"]]
        self.assertEqual({item["episode_key"] for item in changes}, {"1"})
        self.assertTrue(
            all((item["old"], item["new"], item["reason"]) == (True, False, "review") for item in changes)
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            public.write_json_atomic(path, ledger, private=True)
            output = io.StringIO()
            with redirect_stdout(output):
                status = audit_exceptions.main(["--ledger", str(path)])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue()), summary)

    def test_frozen_records_are_neither_audited_nor_counted(self):
        ledger = self.ledger()
        ledger["records"][0]["automation"]["source_kind"] = "frozen"
        self.assertEqual(audit_exceptions.report(ledger)["records"], 2)
        for record in ledger["records"]:
            record["automation"]["source_kind"] = "frozen"
        self.assertEqual(audit_exceptions.exception_audit(ledger), (0, []))


if __name__ == "__main__":
    unittest.main()
