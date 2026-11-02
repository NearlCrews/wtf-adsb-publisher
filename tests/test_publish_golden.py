"""Publication decisions equal the ones recorded before the day quota moved out of publish.

tools/publish_golden.py runs the scenarios and records every autopilot.publish call; this
test only compares a fresh recording with tests/fixtures/publish-golden.json (re-record it
with `tools/publish_golden.py --write` when a change to publication is intended). The 24
named publication tests it replays run a second time in the suite on purpose: here their
publish calls are compared with the recording.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from golden_support import assert_tables_match

from tools import publish_golden


class PublishDecisionsMatchTheRecording(unittest.TestCase):
    def test_every_scenario_publishes_as_recorded(self):
        recorded = json.loads(publish_golden.FIXTURE.read_text())
        observed = publish_golden.observe()
        for scenario, calls in observed.items():
            with self.subTest(scenario=scenario):
                self.assertTrue(calls, "the scenario no longer publishes")
        assert_tables_match(self, recorded, observed)


if __name__ == "__main__":
    unittest.main()
