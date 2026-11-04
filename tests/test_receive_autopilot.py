"""The preview receiver keeps older generations unless a release corrects a public event."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "receive_autopilot", ROOT / "deploy/ghost-preview/receive-autopilot.py"
)
receive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receive)


def event(**changes):
    return {"id": "a", "title": "A story", "measurements": [{"key": "gs", "value": 480}], **changes}


class Uncorrected(unittest.TestCase):
    def test_fields_an_earlier_release_lacked_may_appear(self):
        added = event(
            headline_measurements=["gs", "alt_baro"],
            related=[{"path": "/stories/x/", "title": "X", "observed_start": "2026-11-01T00:00:00Z"}],
            weekly_spotlight={"note": "research"},
        )
        self.assertTrue(receive.uncorrected({"a": event()}, {"a": added}))

    def test_a_changed_field_is_still_a_correction(self):
        for field, before, after in (
            ("headline_measurements", ["gs"], ["gs", "alt_baro"]),
            ("title", "A story", "Another story"),
        ):
            with self.subTest(field=field):
                previous = {"a": event(**{field: before})}
                self.assertFalse(receive.uncorrected(previous, {"a": event(**{field: after})}))
        # Dropping a field an earlier release had is a correction too.
        self.assertFalse(receive.uncorrected({"a": event(headline_measurements=["gs"])}, {"a": event()}))
        self.assertFalse(receive.uncorrected({"a": event()}, {}))

    def test_a_related_story_list_never_corrects_an_event(self):
        one = [{"path": "/stories/x/", "title": "X", "observed_start": "2026-11-01T00:00:00Z"}]
        two = [*one, {"path": "/stories/y/", "title": "Y", "observed_start": "2026-10-30T00:00:00Z"}]
        for before, after in ((one, two), (two, one), (one, None), (None, one)):
            with self.subTest(before=before, after=after):
                previous = event() if before is None else event(related=before)
                current = event() if after is None else event(related=after)
                self.assertTrue(receive.uncorrected({"a": previous}, {"a": current}))


if __name__ == "__main__":
    unittest.main()
