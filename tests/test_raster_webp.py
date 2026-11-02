"""WebP map backgrounds: a still, metadata-free image of the frame, cached once."""

import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools/ghost-theme"
sys.path.insert(0, str(TOOLS))
import basemap  # noqa: E402
import raster_webp  # noqa: E402
from test_basemap import export_response, png, points  # noqa: E402
from validate_media import validate  # noqa: E402

SPEC = importlib.util.spec_from_file_location("webp_event_media", TOOLS / "event-media.py")
media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(media)


def chunk(kind, data):
    return kind + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")


def riff(*chunks):
    payload = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def vp8(width=1020, height=440, size=400):
    return chunk(b"VP8 ", b"\x10\x02\x00\x9d\x01\x2a" + struct.pack("<HH", width, height) + b"\x07" * size)


def vp8l(width=1020, height=440):
    bits = (width - 1) | ((height - 1) << 14)
    return chunk(b"VP8L", b"\x2f" + bits.to_bytes(4, "little") + b"\x00" * 40)


def vp8x(flags=0, width=1020, height=440):
    return chunk(
        b"VP8X",
        bytes([flags, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little"),
    )


class WebpTests(unittest.TestCase):
    def test_still_lossy_and_lossless_frames_are_accepted(self):
        for raw in (
            riff(vp8()),
            riff(vp8l()),
            riff(vp8x(), vp8()),
            riff(vp8x(0x10), chunk(b"ALPH", b"\0" * 8), vp8()),
        ):
            with self.subTest(raw=raw[12:16]):
                self.assertEqual(raster_webp.validate_webp(raw), raw)

    def test_metadata_animation_size_and_structure_are_refused(self):
        bad = {
            "frame width": riff(vp8(width=1200)),
            "lossless height": riff(vp8l(height=441)),
            # Metadata chunks fail on the layout, and their flags fail on their own.
            "ICC chunk": riff(vp8x(0x20), chunk(b"ICCP", b"\0" * 12), vp8()),
            "EXIF chunk": riff(vp8x(0x08), vp8(), chunk(b"EXIF", b"\0" * 8)),
            "XMP chunk": riff(vp8x(0x04), vp8(), chunk(b"XMP ", b"\0" * 8)),
            "ICC flag": riff(vp8x(0x20), vp8()),
            "EXIF flag": riff(vp8x(0x08), vp8()),
            "XMP flag": riff(vp8x(0x04), vp8()),
            "animation flag": riff(vp8x(0x02), vp8()),
            "canvas": riff(vp8x(width=1200), vp8()),
            "two frames": riff(vp8(), vp8()),
            "truncated": riff(vp8())[:-5],
            "trailing bytes": riff(vp8()) + b"extra",
            "container": b"RIFX" + riff(vp8())[4:],
            "too large": riff(vp8(size=raster_webp.MAX_WEBP_BYTES)),
            "start code": riff(chunk(b"VP8 ", b"\x00" * 12)),
            "inter frame": riff(
                chunk(b"VP8 ", b"\x11\x02\x00\x9d\x01\x2a" + struct.pack("<HH", 1020, 440) + b"\0" * 40)
            ),
            "hidden frame": riff(
                chunk(b"VP8 ", b"\x00\x02\x00\x9d\x01\x2a" + struct.pack("<HH", 1020, 440) + b"\0" * 40)
            ),
            "lossless version": riff(
                chunk(b"VP8L", b"\x2f" + (1019 | 439 << 14 | 1 << 29).to_bytes(4, "little") + b"\0" * 40)
            ),
            "pad byte": riff(vp8(size=401))[:-1] + b"\1",
        }
        for name, raw in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                raster_webp.validate_webp(raw)

    def test_normalizing_drops_the_encoder_profile_and_metadata(self):
        tagged = riff(vp8x(0x2C), chunk(b"ICCP", b"\1" * 456), vp8(), chunk(b"EXIF", b"\2" * 10))
        clean = raster_webp.normalize_webp(tagged)
        self.assertEqual(clean, riff(vp8()))
        self.assertEqual(raster_webp.normalize_webp(clean), clean)
        with self.assertRaises(ValueError):
            raster_webp.normalize_webp(riff(chunk(b"ICCP", b"\0" * 4)))

    def test_normalizing_keeps_alpha_behind_a_rebuilt_header(self):
        alpha = chunk(b"ALPH", b"\3" * 9)
        tagged = riff(vp8x(0x30), chunk(b"ICCP", b"\1" * 20), alpha, vp8(), chunk(b"XMP ", b"\2" * 6))
        clean = raster_webp.normalize_webp(tagged)
        self.assertEqual(clean, riff(vp8x(0x10), alpha, vp8()))
        self.assertEqual(raster_webp.validate_webp(clean), clean)

    def test_the_cache_encodes_once_and_prefers_the_smaller_file(self):
        _, bounds = media.projection(points())
        spec = basemap.request_spec(bounds)
        small, large = riff(vp8(size=100)), riff(vp8(size=60000))
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                basemap, "download", side_effect=[json.dumps(export_response(spec)).encode(), png()]
            ):
                plain = basemap.BasemapCache(directory, fetch=True)
                self.assertEqual(plain.image(bounds), (plain.get(bounds), "image/png"))
            cache = basemap.BasemapCache(directory, webp=True)
            with patch.object(basemap, "encode_webp", return_value=small) as encode:
                self.assertEqual(cache.image(bounds), (small, "image/webp"))
                self.assertEqual(cache.image(bounds), (small, "image/webp"))
                self.assertEqual(encode.call_count, 1)
            # The media audit names the embedded encoding and its receipt.
            (used,) = cache.used.values()
            self.assertEqual(used["embedded"], "image/webp")
            self.assertEqual(used["webp"]["recipe"], raster_webp.RECIPE)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            receipt = json.loads(next(Path(directory).glob("*.webp.json")).read_text())
            self.assertEqual(receipt["recipe"], raster_webp.RECIPE)
            next(Path(directory).glob("*.webp")).write_bytes(large)
            with self.assertRaisesRegex(ValueError, "checksum"):
                cache.image(bounds)
            with (
                patch.object(basemap, "RECIPE", {**raster_webp.RECIPE, "quality": 0.5}),
                patch.object(basemap, "encode_webp", return_value=large) as encode,
            ):
                pixels, kind = cache.image(bounds)
                self.assertEqual(encode.call_count, 1)
                self.assertEqual(kind, "image/png")
                self.assertEqual(pixels, cache.get(bounds))

    def test_a_browser_override_names_its_own_recipe(self):
        def recipe(environment):
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "import json, raster_webp; print(json.dumps(raster_webp.RECIPE))",
                ],
                cwd=TOOLS,
                env=environment,
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(result.stdout)

        environment = {key: value for key, value in os.environ.items() if key != "CHROMIUM_PATH"}
        pinned = json.loads((TOOLS / "browser-version.json").read_text())["version"]
        self.assertEqual(recipe(environment)["browser"], pinned)
        self.assertEqual(recipe({**environment, "CHROMIUM_PATH": ""})["browser"], pinned)
        override = recipe({**environment, "CHROMIUM_PATH": "/opt/other/chrome"})
        self.assertEqual(override, {**raster_webp.RECIPE, "browser": "path:/opt/other/chrome"})

    def test_a_cache_without_image_supplies_png_pixels(self):
        class ReadOnly:
            def __init__(self, pixels):
                self.pixels = pixels

            def get(self, bounds):
                return self.pixels

        _, bounds = media.projection(points())
        self.assertEqual(media.map_background(ReadOnly(png()), bounds), (png(), "image/png"))
        self.assertIsNone(media.map_background(ReadOnly(None), bounds))
        self.assertIsNone(media.map_background(None, bounds))
        event = {"id": "example", "evidence_version": "a" * 64, "aircraft": {"registration": "N12345"}}
        image = media.track_image(event, [points()], analysis_window=True, basemaps=ReadOnly(png()))
        self.assertEqual(image["svg"].count("data:image/png;base64,"), 1)
        validate(image["svg"])

    def test_track_images_embed_a_webp_background_that_validates(self):
        _, bounds = media.projection(points())
        spec = basemap.request_spec(bounds)
        event = {"id": "example", "evidence_version": "a" * 64, "aircraft": {"registration": "N12345"}}
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    basemap, "download", side_effect=[json.dumps(export_response(spec)).encode(), png()]
                ),
                patch.object(basemap, "encode_webp", return_value=riff(vp8(size=100))),
            ):
                cache = basemap.BasemapCache(directory, fetch=True, webp=True)
                image = media.track_image(event, [points()], analysis_window=True, basemaps=cache)
        self.assertEqual(image["svg"].count("data:image/webp;base64,"), 1)
        self.assertNotIn("data:image/png", image["svg"])
        validate(image["svg"])


if __name__ == "__main__":
    unittest.main()
