"""Offline map tests cover clipping, source integrity and location boundaries."""

import importlib.util
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "geographic_context_tests", PROJECT / "tools/ghost-theme/geographic_context.py"
)
geo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(geo)
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "geographic_validator_tests", PROJECT / "tools/ghost-theme/validate_media.py"
)
validator = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(validator)
PREPARER_SPEC = importlib.util.spec_from_file_location(
    "geographic_preparer_tests", PROJECT / "tools/ghost-theme/prepare_geographic_data.py"
)
preparer = importlib.util.module_from_spec(PREPARER_SPEC)
with patch.dict(sys.modules, {"geographic_context": geo}):
    PREPARER_SPEC.loader.exec_module(preparer)
from wtf_adsb_publisher import places  # noqa: E402  (geographic_context put src/discoveries on the path)


def project(bounds, frame=geo.MAP_FRAME):
    def point(value):
        return (
            frame[0]
            + (value["lon"] - bounds["left"]) / (bounds["right"] - bounds["left"]) * (frame[2] - frame[0]),
            frame[3]
            - (value["lat"] - bounds["bottom"]) / (bounds["top"] - bounds["bottom"]) * (frame[3] - frame[1]),
        )

    return point


def document(body):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800">'
        + body
        + "</svg>"
    )


def fixture():
    return {"coverage": [-93, 38, -74, 50], "lakes": [], "rivers": [], "boundaries": [], "places": []}


class GeographicContextTests(unittest.TestCase):
    def test_clipping_retains_crossings_without_bridging_off_map_excursions(self):
        self.assertEqual(geo.clip_segment((-5, 5), (15, 5), (0, 0, 10, 10)), [(0, 5), (10, 5)])
        self.assertIsNone(geo.clip_segment((-5, 12), (15, 12), (0, 0, 10, 10)))
        parts = geo.clip_lines([(-1, 2), (2, 2), (2, 12), (8, 12), (8, 2), (11, 2)], (0, 0, 10, 10))
        self.assertEqual(parts, [[(0, 2), (2, 2), (2, 10)], [(8, 10), (8, 2), (10, 2)]])

    def test_polygon_clipping_keeps_enclosing_water_and_hole_orientation(self):
        rectangle = [(-10, -10), (20, -10), (20, 20), (-10, 20)]
        clipped = geo.clip_ring(rectangle, (0, 0, 10, 10))
        self.assertAlmostEqual(geo.ring_area(clipped), 100)
        self.assertTrue(all(geo.inside(point, (0, 0, 10, 10)) for point in clipped))
        self.assertEqual(geo.clip_ring(rectangle, (30, 30, 40, 40)), [])
        for lake in geo.dataset()["lakes"]:
            self.assertGreater(geo.ring_area(lake["rings"][0]), 0)
            self.assertTrue(all(geo.ring_area(hole) < 0 for hole in lake["rings"][1:]))

    def test_public_airport_is_marked_only_when_inside_actual_frame(self):
        bounds = {"left": -83.45, "right": -83.39, "bottom": 42.64, "top": 42.69}
        original = deepcopy(bounds)
        result = geo.backdrop(project(bounds), bounds)
        self.assertIn("PTK airport", result["svg"])
        self.assertEqual(bounds, original)
        validator.validate(document(result["svg"]))
        elsewhere = {"left": -83.8, "right": -83.7, "bottom": 43.4, "top": 43.5}
        self.assertNotIn("PTK airport", geo.backdrop(project(elsewhere), elsewhere)["svg"])
        self.assertIn("generalized", result["caption"])

    def test_no_geography_is_invented_beyond_dataset_coverage(self):
        bounds = {"left": 140, "right": 141, "bottom": 35, "top": 36}
        result = geo.backdrop(project(bounds), bounds)
        self.assertEqual(result["svg"], "")
        self.assertEqual(result["label"], "Outside regional map coverage")
        self.assertIn("unavailable", result["caption"])

    def test_near_city_label_is_bounded_and_does_not_claim_airport_use(self):
        data = fixture()
        data["places"] = [
            {"kind": "city", "name": "Example city", "point": [-83.5, 42.5], "region": "Michigan", "rank": 1}
        ]
        with patch.object(geo, "dataset", return_value=data):
            near = {"left": -83.7, "right": -83.6, "bottom": 42.4, "top": 42.5}
            result = geo.backdrop(project(near), near)
            self.assertEqual(result["label"], "Near Example city, Michigan")
            far = {"left": -88, "right": -87, "bottom": 42, "top": 43}
            self.assertEqual(geo.backdrop(project(far), far)["label"], "Great Lakes region")
        for unsupported in ("departure", "arrival", "origin"):
            self.assertNotIn(unsupported, result["caption"])

    def test_labels_are_sparse_deduplicated_escaped_and_collision_checked(self):
        data = fixture()
        data["places"] = [
            {"kind": "city", "name": 'Town & "test" <label>', "point": [-83.5, 42.5], "rank": i}
            for i in range(20)
        ]
        bounds = {"left": -84, "right": -83, "bottom": 42, "top": 43}
        with patch.object(geo, "dataset", return_value=data):
            result = geo.backdrop(project(bounds), bounds)
        parsed = ET.fromstring(document(result["svg"]))
        texts = parsed.findall("{http://www.w3.org/2000/svg}text")
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0].text, 'Town & "test" <label>')
        self.assertNotIn("<label>", result["svg"])
        self.assertLessEqual(len(texts), geo.MAX_LABELS)
        occupied = []
        self.assertTrue(geo._label(500, 350, "First", 22, occupied, geo.MAP_FRAME))
        self.assertTrue(geo._label(500, 350, "Second", 22, occupied, geo.MAP_FRAME))
        self.assertFalse(geo.intersects(occupied[0], occupied[1]))

    def test_data_hash_detects_mutations_and_sources_remain_pinned(self):
        data = geo.dataset()
        self.assertEqual(data["content_sha256"], places.content_hash(data))
        self.assertEqual(len(data["sources"]), 6)
        for source in data["sources"]:
            self.assertIn(source["revision"], source["url"])
            self.assertRegex(source["sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(source["license"], "Public domain")
        changed = deepcopy(data)
        changed["places"][0]["point"][0] += 1
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "map.json"
            target.write_text(json.dumps(changed))
            geo.dataset.cache_clear()
            try:
                with (
                    patch.object(places, "DATA_PATH", target),
                    self.assertRaisesRegex(ValueError, "content hash"),
                ):
                    geo.dataset()
            finally:
                geo.dataset.cache_clear()

    def test_names_use_single_spaces(self):
        data = geo.dataset()
        for layer in ("lakes", "rivers", "boundaries", "places"):
            for feature in data[layer]:
                with self.subTest(layer=layer, name=feature["name"]):
                    self.assertEqual(feature["name"], " ".join(feature["name"].split()))
        self.assertEqual(preparer.clean_name(" Washington,  D.C. "), "Washington, D.C.")

    def test_census_places_preserve_source_identity_and_do_not_duplicate_city_layers(self):
        data = fixture()
        data["places"] = [
            {"kind": "city", "name": "Detroit", "region": "Michigan", "point": [-83, 42], "rank": 2},
            {"kind": "city", "name": "Detroit", "region": "Elsewhere", "point": [-84, 43], "rank": 8},
        ]
        raw = (
            "USPS|GEOID|NAME|LSAD|INTPTLAT|INTPTLONG\n"
            "MI|2622000|Detroit city|25|42.383037|-83.102237\n"
            "MI|2671300|Sand Lake village|47|43.293|-85.516\n"
            "MI|2671320|Sand Lake CDP|57|44.319|-83.685\n"
        ).encode()
        preparer.add_michigan_places(data, raw)
        michigan = [p for p in data["places"] if p["region"] == "Michigan"]
        self.assertEqual(len(michigan), 3)
        self.assertEqual(len(data["places"]), 4)
        self.assertEqual(michigan[0]["rank"], 2)
        self.assertEqual(michigan[0]["source_name"], "Detroit city")
        self.assertEqual(michigan[0]["point"], [-83.10224, 42.38304])
        self.assertEqual({p["census_geoid"] for p in michigan}, {"2622000", "2671300", "2671320"})
        self.assertEqual([p["name"] for p in michigan[1:]], ["Sand Lake", "Sand Lake"])

    def test_small_michigan_frame_uses_actual_local_place_reference(self):
        bounds = {"left": -83.70, "right": -83.49, "bottom": 42.40, "top": 42.47}
        result = geo.backdrop(project(bounds), bounds)
        self.assertEqual(result["label"], "Near South Lyon, Michigan")
        self.assertIn(">South Lyon</text>", result["svg"])
        self.assertNotIn("Ann Arbor", result["svg"])
        self.assertNotIn("Novi", result["svg"])
        self.assertNotIn("PTK airport", result["svg"])
        census = [p for p in geo.dataset()["places"] if "census_geoid" in p]
        self.assertEqual(len(census), 745)
        self.assertEqual(len({p["census_geoid"] for p in census}), len(census))
        northville = next(p for p in census if p["name"] == "Northville")
        self.assertEqual(northville["point"], [-83.48828, 42.43621])
        self.assertEqual(northville["source_name"], "Northville city")
        validator.validate(document(result["svg"]))

    def test_invalid_bounds_are_rejected_and_projection_receives_only_public_coordinates(self):
        bounds = {"left": -84, "right": -83, "bottom": 42, "top": 43}
        with self.assertRaises(ValueError):
            geo.backdrop(project(bounds), {**bounds, "right": float("inf")})
        keys = set()
        default = project(bounds)

        def recording(value):
            keys.update(value)
            return default(value)

        result = geo.backdrop(recording, bounds)
        self.assertEqual(keys, {"lat", "lon"})
        for unsupported in ("receiver", "ts.net", "href=", "<image", "<script"):
            self.assertNotIn(unsupported, result["svg"])
        validator.validate(document(result["svg"]))


if __name__ == "__main__":
    unittest.main()
