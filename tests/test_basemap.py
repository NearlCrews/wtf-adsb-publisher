"""Offline checks for real map alignment, cache integrity and remote boundaries."""

import hashlib
import importlib.util
import json
import math
import struct
import sys
import tempfile
import unittest
import zlib
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools/ghost-theme"
sys.path.insert(0, str(TOOLS))
import basemap
from validate_media import validate

SPEC = importlib.util.spec_from_file_location("basemap_event_media", TOOLS / "event-media.py")
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


def png():
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1020, 440, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\xe0\xe8\xf0" * 1020) * 440))
        + chunk(b"IEND", b"")
    )


def points():
    return [{"lat": 42.7 + index * 0.01, "lon": -83.4 + index * 0.01, "ts": index * 15} for index in range(3)]


def export_response(spec):
    return {
        "width": 1020,
        "height": 440,
        "extent": {
            **dict(zip(("xmin", "ymin", "xmax", "ymax"), spec["bbox"], strict=True)),
            "spatialReference": {"wkid": 102100, "latestWkid": 3857},
        },
        "href": "https://basemap.nationalmap.gov/arcgis/rest/directories/basemap_output/USGSTopo_MapServer/_ags_map_test.png",
    }


def shifted_request(spec, steps):
    changed = deepcopy(spec)
    changed["bbox"] = [
        math.nextafter(value, math.inf if index % 2 else -math.inf, steps=step)
        for index, (value, step) in enumerate(zip(spec["bbox"], steps, strict=True))
    ]
    return changed


def cached_map(directory, spec, *, webp=False):
    """A normalized map and receipts acquired by another machine."""
    key = basemap.BasemapCache.key(spec)
    raw = basemap.normalize_png(png())
    receipt = {
        "request": spec,
        "export": export_response(spec),
        "fetched_at": "2026-10-01T00:00:00+00:00",
        "png_sha256": hashlib.sha256(raw).hexdigest(),
        "credit": "USGS The National Map",
    }
    directory = Path(directory)
    (directory / (key + ".png")).write_bytes(raw)
    (directory / (key + ".json")).write_text(json.dumps(receipt))
    if webp:
        # A small still VP8L frame, sufficient for the offline format validator.
        bits = 1019 | (439 << 14)
        pixels = b"\x2f" + bits.to_bytes(4, "little") + b"\0" * 40
        payload = b"WEBPVP8L" + struct.pack("<I", len(pixels)) + pixels + b"\0"
        encoded = b"RIFF" + struct.pack("<I", len(payload)) + payload
        basemap.validate_webp(encoded)
        (directory / (key + ".webp")).write_bytes(encoded)
        (directory / (key + ".webp.json")).write_text(
            json.dumps(
                {
                    "png_sha256": receipt["png_sha256"],
                    "webp_sha256": hashlib.sha256(encoded).hexdigest(),
                    "recipe": basemap.RECIPE,
                }
            )
        )
    return key, raw


class BasemapTests(unittest.TestCase):
    def test_projection_matches_export_extent_and_saved_replay_coordinates(self):
        project, bounds = media.projection(points())
        spec = basemap.request_spec(bounds)
        xmin, ymin, xmax, ymax = spec["bbox"]
        self.assertAlmostEqual((xmax - xmin) / (ymax - ymin), 1020 / 440, places=8)
        center_latitude = bounds["scale_latitude"]
        self.assertAlmostEqual(project({"lat": center_latitude, "lon": -83.4})[1], 385)
        for point in points():
            x = 6378137 * math.radians(point["lon"])
            y = 6378137 * math.log(math.tan(math.pi / 4 + math.radians(point["lat"]) / 2))
            expected = (90 + (x - xmin) / (xmax - xmin) * 1020, 605 - (y - ymin) / (ymax - ymin) * 440)
            self.assertAlmostEqual(project(point)[0], expected[0], places=6)
            self.assertAlmostEqual(project(point)[1], expected[1], places=6)
        self.assertGreaterEqual(xmax - xmin, 3000)
        event = {"id": "example", "evidence_version": "a" * 64, "aircraft": {"registration": "N12345"}}
        with tempfile.TemporaryDirectory() as directory:
            cache = basemap.BasemapCache(directory, fetch=True)
            with patch.object(
                basemap, "download", side_effect=[json.dumps(export_response(spec)).encode(), png()]
            ):
                image = media.track_image(event, [points()], analysis_window=True, basemaps=cache)
            validate(image["svg"])
            self.assertIn("USGS The National Map", image["svg"])
            self.assertIn("current reference map", image["caption"])
            self.assertEqual(image["svg"].count("data:image/png;base64,"), 1)
            self.assertNotIn("basemap_output", image["svg"])
            for point, sample in zip(points(), image["replay"]["points"], strict=True):
                self.assertEqual([sample["x"], sample["y"]], [round(value, 2) for value in project(point)])

    def test_cache_reuses_verified_pixels_without_network_and_detects_changes(self):
        _, bounds = media.projection(points())
        spec = basemap.request_spec(bounds)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                basemap, "download", side_effect=[json.dumps(export_response(spec)).encode(), png()]
            ) as fetch:
                original = basemap.BasemapCache(directory, fetch=True).get(bounds)
                self.assertEqual(fetch.call_count, 2)
            with patch.object(basemap, "download", side_effect=AssertionError("Unexpected network access")):
                self.assertEqual(basemap.BasemapCache(directory).get(bounds), original)
                receipt = next(Path(directory).glob("*.json"))
                saved = json.loads(receipt.read_text())
                saved["request"]["bbox"][0] += 10
                receipt.write_text(json.dumps(saved))
                with self.assertRaisesRegex(ValueError, "request or checksum"):
                    basemap.BasemapCache(directory).get(bounds)
                saved["request"] = spec
                receipt.write_text(json.dumps(saved))
                next(Path(directory).glob("*.png")).write_bytes(original + b"extra")
                with self.assertRaisesRegex(ValueError, "request or checksum"):
                    basemap.BasemapCache(directory).get(bounds)

    def test_missing_cache_is_offline_and_unsupported_bounds_do_not_fetch(self):
        _, bounds = media.projection(points())
        with tempfile.TemporaryDirectory() as directory, patch.object(basemap, "download") as fetch:
            cache = basemap.BasemapCache(directory)
            with self.assertRaisesRegex(ValueError, "--fetch-basemaps"):
                cache.get(bounds)
            for changed in ({**bounds, "right": 181}, {**bounds, "top": 89}):
                self.assertIsNone(cache.get(changed))
            fetch.assert_not_called()

    def test_cross_platform_bounds_preserve_original_png_webp_and_receipts(self):
        _, bounds = media.projection(points())
        original = basemap.request_spec(bounds)
        changed = shifted_request(original, (1, 2, 3, 1))
        event = {"id": "example", "evidence_version": "a" * 64, "aircraft": {"registration": "N12345"}}
        with tempfile.TemporaryDirectory() as directory:
            key, raw = cached_map(directory, original, webp=True)
            before = {path.name: path.read_bytes() for path in Path(directory).iterdir()}
            cache = basemap.BasemapCache(directory, webp=True)
            expected = media.track_image(event, [points()], analysis_window=True, basemaps=cache)
            with (
                patch.object(basemap, "request_spec", return_value=changed),
                patch.object(basemap, "download", side_effect=AssertionError("Unexpected network access")),
                patch.object(basemap, "encode_webp", side_effect=AssertionError("Unexpected re-encoding")),
            ):
                self.assertEqual(cache.get(bounds), raw)
                self.assertEqual(
                    media.track_image(event, [points()], analysis_window=True, basemaps=cache), expected
                )
                self.assertEqual(cache.image(bounds), (before[key + ".webp"], "image/webp"))
            self.assertEqual(list(cache.used), [key])
            self.assertEqual(cache.used[key]["request"], original)
            self.assertEqual(cache.used[key]["export"], export_response(original))
            self.assertEqual(before, {path.name: path.read_bytes() for path in Path(directory).iterdir()})

    def test_exact_keys_skip_index_and_fallback_metadata_is_loaded_once(self):
        _, bounds = media.projection(points())
        original = basemap.request_spec(bounds)
        with tempfile.TemporaryDirectory() as directory:
            key, raw = cached_map(directory, original)
            cache = basemap.BasemapCache(directory)
            with patch.object(cache, "_load_receipts", wraps=cache._load_receipts) as load:
                self.assertEqual(cache.get(bounds), raw)
                load.assert_not_called()
                for steps in ((1, 2, 3, 1), (3, 2, 1, 3), (1, 2, 3, 1)):
                    self.assertEqual(cache.cached_key(shifted_request(original, steps)), key)
                for _ in range(2):
                    self.assertIsNone(cache.cached_key(shifted_request(original, (5, 0, 0, 0))))
                load.assert_called_once_with()

    def test_compatibility_requires_all_other_request_fields_to_match(self):
        _, bounds = media.projection(points())
        original = basemap.request_spec(bounds)
        changed = shifted_request(original, (1, 2, 3, 1))
        with tempfile.TemporaryDirectory() as directory:
            cached_map(directory, original)
            cache = basemap.BasemapCache(directory)
            for field, value in (
                ("recipe", 2),
                ("recipe", True),
                ("service", "https://example.invalid/MapServer"),
                ("spatial_reference", 4326),
                ("width", 2040),
                ("width", 1020.0),
                ("height", 880),
                ("format", "jpeg"),
                ("extra", "value"),
            ):
                with self.subTest(field=field, value=value):
                    self.assertIsNone(cache.cached_key({**changed, field: value}))
            for steps in ((5, 0, 0, 0), (0, 5, 0, 0), (0, 0, 5, 0), (0, 0, 0, 5)):
                with self.subTest(steps=steps):
                    self.assertIsNone(cache.cached_key(shifted_request(original, steps)))

    def test_float_tolerance_counts_steps_across_a_power_of_two(self):
        original = {"bbox": [1.0, 2.0, 3.0, 4.0]}
        self.assertTrue(basemap.equivalent_request(original, shifted_request(original, (4, 0, 0, 0))))
        self.assertFalse(basemap.equivalent_request(original, shifted_request(original, (5, 0, 0, 0))))

    def test_ambiguous_compatible_receipts_fail_but_exact_keys_still_work(self):
        _, bounds = media.projection(points())
        original = basemap.request_spec(bounds)
        other = shifted_request(original, (6, 0, 0, 0))
        between = shifted_request(original, (3, 0, 0, 0))
        with tempfile.TemporaryDirectory() as directory:
            key, _ = cached_map(directory, original)
            other_key, _ = cached_map(directory, other)
            cache = basemap.BasemapCache(directory)
            self.assertEqual(cache.cached_key(original), key)
            self.assertEqual(cache.cached_key(other), other_key)
            with patch.object(basemap, "request_spec", return_value=between):
                with self.assertRaisesRegex(ValueError, "Ambiguous"):
                    cache.get(bounds)

    def test_receipt_index_rejects_invalid_bbox_and_filename_binding(self):
        _, bounds = media.projection(points())
        original = basemap.request_spec(bounds)
        query = shifted_request(original, (1, 2, 3, 1))
        bad_boxes = (
            None,
            [],
            original["bbox"][:3],
            [True, *original["bbox"][1:]],
            ["west", *original["bbox"][1:]],
            [math.nan, *original["bbox"][1:]],
            [math.inf, *original["bbox"][1:]],
            list(reversed(original["bbox"])),
        )
        for bbox in bad_boxes:
            with self.subTest(bbox=bbox), tempfile.TemporaryDirectory() as directory:
                spec = {**original, "bbox": bbox}
                key = basemap.BasemapCache.key(spec)
                (Path(directory) / (key + ".json")).write_text(json.dumps({"request": spec}))
                with self.assertRaisesRegex(ValueError, "request or checksum"):
                    basemap.BasemapCache(directory).cached_key(query)
        with tempfile.TemporaryDirectory() as directory:
            key, _ = cached_map(directory, original)
            path = Path(directory) / (key + ".json")
            saved = json.loads(path.read_text())
            saved["request"] = query
            path.write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError, "request or checksum"):
                basemap.BasemapCache(directory).cached_key(shifted_request(original, (2, 2, 2, 2)))

    def test_compatible_cache_still_checks_fresh_receipt_pixels_and_exports(self):
        _, bounds = media.projection(points())
        original = basemap.request_spec(bounds)
        query = shifted_request(original, (1, 2, 3, 1))
        for change, error in (
            ("request", "request or checksum"),
            ("png", "request or checksum"),
            ("metadata", "normalized PNG"),
            ("extent", "adjusted"),
            ("origin", "unexpected image URL"),
            ("webp", "WebP.*checksum"),
            ("webp_dimensions", "WebP dimensions"),
        ):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                key, raw = cached_map(directory, original, webp=True)
                cache = basemap.BasemapCache(directory, webp=True)
                self.assertEqual(cache.cached_key(query), key)
                receipt_path = Path(directory) / (key + ".json")
                receipt = json.loads(receipt_path.read_text())
                if change == "request":
                    receipt["request"] = shifted_request(original, (1, 0, 0, 0))
                elif change == "png":
                    (Path(directory) / (key + ".png")).write_bytes(raw + b"extra")
                elif change == "metadata":
                    text = b"tEXtnote\0private metadata"
                    chunk = struct.pack(">I", len(text) - 4) + text + struct.pack(">I", zlib.crc32(text))
                    tagged = raw[:-12] + chunk + raw[-12:]
                    (Path(directory) / (key + ".png")).write_bytes(tagged)
                    receipt["png_sha256"] = hashlib.sha256(tagged).hexdigest()
                elif change == "extent":
                    receipt["export"]["extent"]["xmin"] += 100
                elif change == "origin":
                    receipt["export"]["href"] = "https://example.invalid/map.png"
                else:
                    webp_path = Path(directory) / (key + ".webp")
                    data = bytearray(webp_path.read_bytes())
                    data[21] ^= 1
                    webp_path.write_bytes(data)
                    if change == "webp_dimensions":
                        path = Path(directory) / (key + ".webp.json")
                        webp_receipt = json.loads(path.read_text())
                        webp_receipt["webp_sha256"] = hashlib.sha256(data).hexdigest()
                        path.write_text(json.dumps(webp_receipt))
                receipt_path.write_text(json.dumps(receipt))
                with patch.object(basemap, "request_spec", return_value=query):
                    with self.assertRaisesRegex(ValueError, error):
                        cache.image(bounds)

    def test_explicit_fetch_refreshes_cached_matches_and_misses_without_rescanning(self):
        _, bounds = media.projection(points())
        original = basemap.request_spec(bounds)
        between = shifted_request(original, (3, 0, 0, 0))
        other = shifted_request(original, (6, 0, 0, 0))
        beyond = shifted_request(original, (10, 0, 0, 0))
        with tempfile.TemporaryDirectory() as directory:
            key, _ = cached_map(directory, original)
            cache = basemap.BasemapCache(directory, fetch=True)
            with patch.object(cache, "_load_receipts", wraps=cache._load_receipts) as load:
                self.assertEqual(cache.cached_key(between), key)
                self.assertIsNone(cache.cached_key(beyond))
                with (
                    patch.object(basemap, "request_spec", return_value=other),
                    patch.object(
                        basemap, "download", side_effect=[json.dumps(export_response(other)).encode(), png()]
                    ) as download,
                ):
                    cache.get(bounds)
                    self.assertEqual(download.call_count, 2)
                self.assertEqual(cache.cached_key(beyond), cache.key(other))
                with self.assertRaisesRegex(ValueError, "Ambiguous"):
                    cache.cached_key(between)
                load.assert_called_once_with()

    def test_dry_run_wrapper_uses_the_same_compatible_cache_lookup(self):
        spec = importlib.util.spec_from_file_location("basemap_autopilot", TOOLS.parent / "run_autopilot.py")
        autopilot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(autopilot)
        _, bounds = media.projection(points())
        original = basemap.request_spec(bounds)
        with tempfile.TemporaryDirectory() as directory:
            key, raw = cached_map(directory, original, webp=True)
            cache = autopilot.CachedBasemaps(directory, webp=True)
            with patch.object(basemap, "request_spec", return_value=shifted_request(original, (1, 2, 3, 1))):
                self.assertEqual(cache.get(bounds), raw)
                self.assertEqual(cache.image(bounds)[1], "image/webp")
            self.assertEqual(cache.missing, 0)
            self.assertEqual(list(cache.used), [key])
            with patch.object(basemap, "request_spec", return_value=shifted_request(original, (5, 0, 0, 0))):
                self.assertIsNone(cache.image(bounds))
            self.assertEqual(cache.missing, 1)
            self.assertIsNone(cache.image({**bounds, "right": 181}))
            self.assertEqual(cache.missing, 1)

    def test_mercator_limit_uses_an_explicit_offline_fallback(self):
        for latitude in (85.001, 85.01, 85.04, 85.052, 89):
            with self.subTest(latitude=latitude):
                fixes = [{**point, "lat": latitude + index * 0.0001} for index, point in enumerate(points())]
                project, bounds = media.projection(fixes)
                # The frame crossing the Mercator limit is intentionally not
                # exported. Frames inside it must pass the exact aspect check.
                spec = basemap.request_spec(bounds)
                if bounds["top"] > basemap.MAX_LATITUDE:
                    self.assertIsNone(spec)
                else:
                    self.assertIsNotNone(spec)
                self.assertTrue(all(math.isfinite(value) for point in fixes for value in project(point)))

    def test_adjusted_extent_and_non_source_image_urls_are_rejected(self):
        _, bounds = media.projection(points())
        spec = basemap.request_spec(bounds)
        result = export_response(spec)
        basemap.verify_export(result, spec)
        changes = [
            ("width", 2040),
            ("height", 880),
            ("href", "https://example.com/map.png"),
            ("href", "https://basemap.nationalmap.gov@evil.example/map.png"),
            ("href", result["href"].replace("https:", "http:")),
            ("href", result["href"] + "?token=example"),
            ("href", result["href"].replace("_ags_map_test.png", "../map.png")),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                basemap.verify_export({**result, key: value}, spec)
        for key, value in (
            ("xmin", spec["bbox"][0] + 100),
            ("ymax", float("nan")),
            ("spatialReference", {"wkid": 4326}),
        ):
            changed = deepcopy(result)
            changed["extent"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                basemap.verify_export(changed, spec)
        with self.assertRaisesRegex(ValueError, "must not redirect"):
            basemap.NoRedirect().redirect_request(None, None, 302, "redirect", {}, result["href"])


if __name__ == "__main__":
    unittest.main()
