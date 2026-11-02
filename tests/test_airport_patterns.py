"""Airport-pattern hints require observed circuits, not airport proximity alone."""

import importlib.util
import math
import sys
import types
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tests"))
SPEC = importlib.util.spec_from_file_location(
    "airport_patterns", PROJECT / "tools/ghost-theme/airport_patterns.py"
)
patterns = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patterns)

RUNWAY = {
    "airport_ident": "TEST",
    "airport_name": "Example Airport",
    "runway": "09/27",
    "ends": [
        [-83.5 - 0.5 / (60 * math.cos(math.radians(42.5))), 42.5],
        [-83.5 + 0.5 / (60 * math.cos(math.radians(42.5))), 42.5],
    ],
}


def fixture():
    # Two circuits with an observed approach and offset downwind on each one.
    points = []
    route = [
        (-1.5, 0),
        (-1, 0),
        (-0.5, 0),
        (0, 0),
        (0.5, 0),
        (1, 0),
        (1, 0.4),
        (1, 0.8),
        (0.5, 0.8),
        (0, 0.8),
        (-0.5, 0.8),
        (-1, 0.8),
        (-1.5, 0.8),
        (-1.5, 0.4),
    ]
    for x, y in route * 2 + route[:5]:
        points.append(
            {
                "ts": len(points) * 15,
                "lat": 42.5 + y / 60,
                "lon": -83.5 + x / (60 * math.cos(math.radians(42.5))),
                "source": "adsb_icao",
            }
        )
    event = {
        "kind": "circling",
        "aircraft": {"type_code": "C172"},
        "measurements": [
            {
                "key": "closed_loops",
                "scope": "analysis-window",
                "value": 2,
                "start_utc": "1970-01-01T00:00:00Z",
                "end_utc": datetime.fromtimestamp(points[-1]["ts"], timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        ],
    }
    return [points], event


class AirportPatternTests(unittest.TestCase):
    def classify(self, parts, event):
        with patch.object(patterns, "runway_data", return_value=[RUNWAY]):
            return patterns.classify(parts, event, analysis_window=True)

    def test_repeated_runway_passes_and_offset_return_qualify_without_mutating_evidence(self):
        parts, event = fixture()
        before = deepcopy((parts, event))
        result = self.classify(parts, event)
        self.assertEqual(result["aligned_passes"], 3)
        self.assertEqual(result["closed_loops"], 2)
        self.assertEqual(result["airport_ident"], "TEST")
        self.assertNotIn("lat", result)
        self.assertNotIn("altitude", result)
        self.assertEqual((parts, event), before)

    def test_helicopters_unknown_types_other_event_kinds_and_excerpts_remain_visible(self):
        parts, original = fixture()
        for type_code in ("AS50", "R44", "R66", "B06", "UNKNOWN", ""):
            event = deepcopy(original)
            event["aircraft"]["type_code"] = type_code
            self.assertIsNone(self.classify(parts, event))
        for kind in ("loitering", "repeated_passes", "outlier_gs"):
            event = {**original, "kind": kind}
            self.assertIsNone(self.classify(parts, event))
        self.assertIsNone(patterns.classify(parts, original, analysis_window=False))
        self.assertIsNone(self.classify(parts, {**original, "synthetic": True}))

    def test_missing_or_wrong_interval_loop_evidence_cannot_classify(self):
        parts, original = fixture()
        for field, value in (
            ("scope", "observation"),
            ("value", 1),
            ("value", True),
            ("value", 2.5),
            ("start_utc", "1970-01-01T00:00:01Z"),
            ("end_utc", "1970-01-01T01:00:00Z"),
        ):
            event = deepcopy(original)
            event["measurements"][0][field] = value
            self.assertIsNone(self.classify(parts, event), (field, value))
        for measurements in ([], original["measurements"] * 2):
            self.assertIsNone(self.classify(parts, {**original, "measurements": measurements}))

    def test_nearby_orbits_and_parallel_offset_tracks_are_insufficient(self):
        parts, event = fixture()
        for point in parts[0]:
            point["lat"] += 0.6 / 60
        self.assertIsNone(self.classify(parts, event))
        parts, event = fixture()
        for point in parts[0]:
            point["lat"] = 42.5
        self.assertIsNone(self.classify(parts, event))

    def test_gaps_never_supply_missing_runway_crossings_or_return_legs(self):
        parts, event = fixture()
        self.assertIsNone(self.classify([[point] for point in parts[0]], event))
        # A complete-looking circuit assembled across saved discontinuities is
        # still insufficient, even when all timestamps remain in the window.
        self.assertIsNone(
            self.classify(
                [parts[0][:7], parts[0][7:14], parts[0][14:21], parts[0][21:28], parts[0][28:]], event
            )
        )
        # Accepted consecutive points separated by more than 45 s are invalid.
        parts[0][1]["ts"] = 60
        self.assertIsNone(self.classify(parts, event))
        parts, event = fixture()
        # Removing every connected return leg prevents a circuit assertion.
        split = []
        for index, point in enumerate(parts[0]):
            if not split or 6 <= index % 14 <= 13:
                split.append([])
            split[-1].append(point)
        self.assertIsNone(self.classify(split, event))

    def test_uncertain_sources_and_invalid_coordinates_remain_unclassified(self):
        for field, value in (
            ("source", "mlat"),
            ("source", "adsr_icao"),
            ("lat", float("nan")),
            ("lon", True),
            ("lat", 0),
        ):
            parts, event = fixture()
            parts[0][8][field] = value
            self.assertIsNone(self.classify(parts, event), (field, value))

    def test_runway_reversal_does_not_change_pass_count(self):
        parts, event = fixture()
        expected = self.classify(parts, event)
        reverse = {**RUNWAY, "ends": list(reversed(RUNWAY["ends"]))}
        with patch.object(patterns, "runway_data", return_value=[reverse]):
            actual = patterns.classify(parts, event, analysis_window=True)
        self.assertEqual(actual, expected)

    def test_checked_in_runway_bundle_is_pinned_and_covers_public_regional_airports(self):
        data = patterns.runway_data()
        self.assertEqual(len(data), 1837)
        self.assertTrue(any(row["airport_ident"] == "KPTK" and row["runway"] == "09L/27R" for row in data))
        self.assertTrue(any(row["airport_ident"] == "1D2" for row in data))


MEDIA_SPEC = importlib.util.spec_from_file_location(
    "event_media_parity", PROJECT / "tools/ghost-theme/event-media.py"
)
media = importlib.util.module_from_spec(MEDIA_SPEC)
MEDIA_SPEC.loader.exec_module(media)


def circuits(period=10.0, laps=4, gaps=()):
    """Left-hand 80-kn circuits around a synthetic runway at the local origin."""
    from fixture_geometry import Track, runway_at, sample

    track = Track(x=-0.5, altitude=1000, speed=80)
    for _ in range(laps):
        track.straight(45).turn(-90).straight(30).turn(-90).straight(90).turn(-90).straight(30).turn(
            -90
        ).straight(45)
    points = [{k: p[k] for k in ("ts", "lat", "lon", "source")} for p in sample(track, period, gaps=gaps)]
    return points, runway_at(0, 0, 90, 0.8)


class SharedRuleTests(unittest.TestCase):
    def test_saved_tracks_split_exactly_where_the_media_renderer_breaks_lines(self):
        raw = [
            {"ts": 0, "lat": 42.5, "lon": -83.5, "source": "adsb_icao"},
            {"ts": 10, "lat": 42.501, "lon": -83.5, "source": "adsb_icao"},
            {"ts": 10, "lat": 42.502, "lon": -83.5, "source": "adsb_icao"},
            {"ts": 20, "lat": 42.503, "lon": -83.5, "source": "adsb_icao"},
            {"ts": 70, "lat": 42.504, "lon": -83.5, "source": "adsb_icao"},
            {"ts": 80, "lat": 42.505, "lon": -83.5, "source": "mlat"},
            {"ts": 90, "lat": 42.506, "lon": -83.5, "source": "mlat", "alt": "ground"},
            {"ts": 100, "lat": 42.507, "lon": -83.5, "source": "mlat", "new_leg": True},
            {"ts": 110, "lat": 42.508, "lon": -83.5, "source": "mlat"},
            {"ts": 120, "lat": 43.5, "lon": -83.5, "source": "mlat"},
        ]
        expected = [
            [{k: p[k] for k in ("ts", "lat", "lon", "source")} for p in part]
            for part in media.segments(raw, 0, 120)
        ]
        self.assertEqual(patterns.airport_context.parts(raw), expected)

    def test_return_legs_do_not_depend_on_sampling_density(self):
        counts = set()
        for period in (5.0, 10.0, 15.0):
            points, runway = circuits(period)
            match = patterns.airport_context.runway_passes([points], runway)
            self.assertIsNotNone(match, period)
            counts.add(match[0])
        self.assertEqual(len(counts), 1)

    def test_a_dropout_on_final_splits_the_window_without_bridging_it(self):
        points, runway = circuits(gaps=((1060, 1120),))
        pattern = {"started_at": points[0]["ts"], "ended_at": points[-1]["ts"], "closed_loops": 4}
        with patch.object(patterns.airport_context, "nearby", return_value=[runway]):
            result = patterns.airport_context.window_context(pattern, points, {"t": "C172"})
        self.assertIsNotNone(result)
        self.assertEqual(result["airport_ident"], "SYNT")
        split = patterns.airport_context.parts(points)
        self.assertEqual(len(split), 2)
        self.assertLessEqual(
            max(b["ts"] - a["ts"] for part in split for a, b in zip(part, part[1:], strict=False)), 45
        )

    def test_classifier_coverage_comes_from_the_dataset(self):
        self.assertEqual(patterns.airport_context.coverage(), (-93, 38, -74, 50))
        parts, event = fixture()
        data = {**patterns.airport_context.dataset(), "coverage": [-80, 44, -70, 48]}
        with patch.object(patterns.airport_context, "dataset", return_value=data):
            self.assertIsNone(self.classify(parts, event))

    def classify(self, parts, event):
        with patch.object(patterns, "runway_data", return_value=[RUNWAY]):
            return patterns.classify(parts, event, analysis_window=True)

    def test_reference_classes_extend_the_fixed_wing_list(self):
        parts, event = fixture()
        event = deepcopy(event)
        event["aircraft"]["type_code"] = "P28B"
        # Without reference data only the reviewed list counts as fixed-wing.
        unavailable = types.SimpleNamespace(type_class=lambda code: None)
        with patch.object(patterns.airport_context, "reference", unavailable):
            self.assertIsNone(self.classify(parts, event))
        reference = types.SimpleNamespace(type_class={"P28B": "airplane", "C172": "helicopter"}.get)
        with patch.object(patterns.airport_context, "reference", reference):
            event["aircraft"]["type_code"] = "P28B"
            self.assertIsNotNone(self.classify(parts, event))
            event["aircraft"]["type_code"] = "C172"
            self.assertIsNone(self.classify(parts, event))


if __name__ == "__main__":
    unittest.main()
