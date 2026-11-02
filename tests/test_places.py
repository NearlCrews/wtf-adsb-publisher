"""The shared place label: aircraft positions only, and the same town as the track map."""

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from wtf_adsb_publisher import places  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "places_event_media", PROJECT / "tools/ghost-theme/event-media.py"
)
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


def city(name, region="Michigan"):
    return next(
        p
        for p in places.dataset()["places"]
        if p["kind"] == "city" and p["name"] == name and p["region"] == region
    )


def track(lon, lat, count=6, step=0.004, **extra):
    return [
        {
            "ts": index * 15,
            "lat": lat + index * step,
            "lon": lon + index * step,
            "source": "adsb_icao",
            **extra,
        }
        for index in range(count)
    ]


def event():
    return {
        "id": "00000000-0000-4000-8000-000000000001",
        "evidence_version": "b" * 64,
        "kind": "watch_type",
        "observed_start": "2026-09-27T00:05:15Z",
        "aircraft": {"registration": "N12345", "type_code": "A388", "type_description": "AIRBUS A-380-800"},
    }


class PlaceTests(unittest.TestCase):
    def test_nearest_city_names_state_and_postal_short_form(self):
        lon, lat = city("Coleman")["point"]
        place = places.nearest_place(track(lon - 0.01, lat - 0.01, count=6, step=0.004))
        self.assertEqual(place, {"name": "Coleman, Michigan", "short": "Coleman, MI", "state": "Michigan"})
        self.assertEqual(places.label(place), "Near Coleman, Michigan")

    def test_parts_and_flat_points_give_the_same_place(self):
        lon, lat = city("Coleman")["point"]
        points = track(lon - 0.01, lat - 0.01)
        self.assertEqual(places.nearest_place([points[:3], points[3:]]), places.nearest_place(points))

    def test_no_place_outside_coverage_beyond_fifty_km_or_without_valid_positions(self):
        self.assertIsNone(places.nearest_place(track(-120.0, 35.0)))
        self.assertIsNone(places.nearest_place([]))
        self.assertIsNone(places.nearest_place(None))
        self.assertIsNone(places.nearest_place([{"lat": "43", "lon": -84}, {"lat": True, "lon": -84}]))
        self.assertIsNone(places.nearest_place([{"lat": float("nan"), "lon": -84.5}]))
        far = {
            "coverage": [-93, 38, -74, 50],
            "places": [{"kind": "city", "name": "Far", "point": [-80, 40]}],
        }
        self.assertIsNone(places.nearest_place(track(-84.5, 43.5), data=far))

    def test_damaged_or_missing_bundle_never_breaks_a_caller(self):
        for error in (OSError("missing"), ValueError("hash"), KeyError("places")):
            with self.subTest(error=error), patch.object(places, "dataset", side_effect=error):
                self.assertIsNone(places.nearest_place(track(-84.5, 43.5)))

    def test_only_aircraft_coordinates_are_read(self):
        lon, lat = city("Coleman")["point"]
        plain = track(lon - 0.01, lat - 0.01)
        decorated = [{**point, "receiver_lat": 12.345678, "r_dst": 3.2, "r_dir": 270} for point in plain]
        self.assertEqual(places.nearest_place(decorated), places.nearest_place(plain))
        result = json.dumps(places.nearest_place(decorated))
        self.assertNotRegex(result, re.compile(r"12\.345|r_dst|r_dir|receiver"))

    def test_track_heading_alt_text_and_story_place_agree(self):
        for name in ("Coleman", "Adrian", "Akron"):
            lon, lat = city(name)["point"]
            for offset in (-0.03, 0.0, 0.02):
                parts = media.segments(track(lon + offset, lat - offset), 0, 600)
                with self.subTest(name=name, offset=offset):
                    image = media.track_image(event(), parts, analysis_window=False)
                    place = places.nearest_place(parts)
                    self.assertIsNotNone(place)
                    self.assertIn(">" + places.label(place) + "<", image["svg"])
                    self.assertIn(" near " + place["name"] + ":", image["alt"])

    def test_the_media_renderer_uses_the_shared_projection(self):
        self.assertIs(media.projection, places.projection)


class TownNameTests(unittest.TestCase):
    def setUp(self):
        places._town_names.cache_clear()
        self.addCleanup(places._town_names.cache_clear)

    def test_the_regional_towns_are_the_bundles_city_names(self):
        towns = places.town_names()
        self.assertIsInstance(towns, frozenset)
        self.assertTrue({"Brighton", "Ann Arbor", "Coleman"} <= towns)
        self.assertEqual(towns, {p["name"] for p in places.dataset()["places"] if p["kind"] == "city"})

    def test_without_the_bundle_there_are_none_and_a_failed_read_is_not_kept(self):
        for error in (FileNotFoundError("bundle"), ValueError("hash"), KeyError("places")):
            with self.subTest(error=error), patch.object(places, "dataset", side_effect=error):
                self.assertEqual(places.town_names(), frozenset())
        with patch.object(places, "dataset", return_value={"places": None}):
            self.assertEqual(places.town_names(), frozenset())
        self.assertIn("Brighton", places.town_names())

    def test_a_process_with_no_bundle_path_has_no_towns_or_places(self):
        places.dataset.cache_clear()
        self.addCleanup(places.dataset.cache_clear)
        with patch.object(places, "DATA_PATH", None):
            self.assertEqual(places.town_names(), frozenset())
            self.assertIsNone(places.nearest_place(track(-84.5, 43.5)))
            with self.assertRaises(FileNotFoundError):
                places.dataset()


if __name__ == "__main__":
    unittest.main()
