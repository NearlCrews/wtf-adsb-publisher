"""Type speed norms arrive with the engine's evidence (context.type_norms) and are staged once.

The engine computes each norm (selection.type_norm over its performance samples) and exports it
per norm-kind finding; the publisher stages the first value a record receives, None included,
so a drifting baseline never revises a released story.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
import story_fixtures
from inbox_support import inbox
from test_autopilot import NOW, candidate

from wtf_adsb_publisher import autopilot, curation, feed_adapter
from wtf_adsb_publisher import public_events as public

STAMP = public._instant(NOW).timestamp()


def seasonal(identifier=2, address=None):
    item = candidate(
        identifier, "seasonal_speed_record", value=350, **({"address": address} if address else {})
    )
    item["source"]["unit"] = "kn"
    item["source"]["evidence"]["aircraft"]["gs"] = 350
    item["source"]["evidence"]["season_record"] = {
        "previous_max_gs": 300,
        "baseline_start": "2026-07-01",
        "season_start": "2026-09-01",
        "baseline_days": 40,
    }
    return item


def norm(low):
    return {**story_fixtures.NORM, "low": low, "high": low + 40}


class StagedNorms(unittest.TestCase):
    def setUp(self):
        self.ledger = public.new_ledger("2026-10-01")
        self.enterContext(patch.object(autopilot, "_place", return_value=None))

    def stage(self, *items, norms=None):
        selected = autopilot.collect_state(inbox(*items, now=STAMP, norms=norms), STAMP, ledger=self.ledger)
        return selected, autopilot.stage(self.ledger, selected, at=NOW)

    def test_the_exported_norm_of_a_norm_kind_finding_is_staged_on_its_record(self):
        item = seasonal()
        self.assertIn(item["source"]["kind"], curation.NORM_KINDS)
        selected, result = self.stage(item, norms={item["source"]["id"]: norm(300)})
        self.assertEqual([entry["type_norm"] for entry in selected], [norm(300)])
        self.assertEqual(result["changed"], 1)
        record = self.ledger["records"][0]
        self.assertEqual(record["automation"]["type_norm"], norm(300))
        self.assertEqual(record["evidence"]["type_norm"], norm(300))
        self.assertEqual(record["editorial"]["story"]["facts"]["norm.gs_low"], 300)

    def test_a_later_norm_never_revises_the_record(self):
        item = seasonal()
        self.stage(item, norms={item["source"]["id"]: norm(300)})
        record = self.ledger["records"][0]
        revision = record["revision"]
        _, result = self.stage(item, norms={item["source"]["id"]: norm(320)})
        self.assertEqual(result["changed"], 0)
        self.assertEqual(record["automation"]["type_norm"], norm(300))
        self.assertEqual(record["evidence"]["type_norm"], norm(300))
        self.assertEqual(record["revision"], revision)

    def test_a_view_without_the_norm_stages_none_and_keeps_it(self):
        # A 1.0.0 observation, or a null norm (too few addresses or days), stages no norm.
        item = seasonal()
        for norms in (None, {item["source"]["id"]: None}):
            with self.subTest(norms=norms):
                self.ledger = public.new_ledger("2026-10-01")
                selected, _ = self.stage(item, norms=norms)
                self.assertIsNone(selected[0]["type_norm"])
                record = self.ledger["records"][0]
                self.assertIn("type_norm", record["automation"])
                self.assertIsNone(record["automation"]["type_norm"])
                self.assertNotIn("type_norm", record["evidence"])
                # A norm that arrives later does not revise the record either.
                self.stage(item, norms={item["source"]["id"]: norm(300)})
                self.assertIsNone(record["automation"]["type_norm"])
                self.assertNotIn("type_norm", record["evidence"])

    def test_other_kinds_take_no_norm_even_when_the_view_names_one(self):
        item = candidate(1)
        selected, _ = self.stage(item, norms={item["source"]["id"]: norm(300)})
        self.assertIsNone(selected[0]["type_norm"])
        record = self.ledger["records"][0]
        self.assertNotIn("type_norm", record["automation"])
        self.assertNotIn("type_norm", record["evidence"])

    def test_the_feed_adapter_returns_the_norms_beside_the_groups(self):
        first, second = seasonal(2, "abc123"), seasonal(3, "def456")
        norms = {first["source"]["id"]: norm(300), second["source"]["id"]: None}
        state = inbox(first, second, now=STAMP, norms=norms)
        self.assertEqual(len(state["observations"]), 2)
        groups, _novelty, sources, _bindings, found = feed_adapter.collection(state, self.ledger, at=NOW)
        self.assertEqual(len(groups), 2)
        self.assertEqual(found, norms)
        self.assertLessEqual(set(found), set(sources))
        # The adapter hands over copies: changing one leaves the imported view as it was.
        found[first["source"]["id"]]["low"] = 0
        self.assertEqual(state["observations"][0]["observation"]["context"]["type_norms"], {2: norm(300)})


if __name__ == "__main__":
    unittest.main()
