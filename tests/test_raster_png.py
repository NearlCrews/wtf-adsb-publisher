"""A map image must remain a bounded, passive raster through SVG validation."""

import base64
import struct
import sys
import unittest
import zlib
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools/ghost-theme"
sys.path.insert(0, str(TOOLS))
from raster_png import MAX_PNG_BYTES, PNG_SIGNATURE, normalize_png, validate_png
from validate_media import validate


def chunk(kind, data=b""):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def header(width=2, height=2, *, color=2, depth=8, interlace=0, compression=0, filtering=0):
    return chunk(
        b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, compression, filtering, interlace)
    )


def png(*, width=2, height=2, color=2, scanlines=None, metadata=b"", palette=b"", transparency=b""):
    if scanlines is None:
        scanlines = (b"\x00" + b"\x01" * width * {2: 3, 3: 1, 6: 4}[color]) * height
    return (
        PNG_SIGNATURE
        + header(width, height, color=color)
        + (chunk(b"PLTE", palette) if palette else b"")
        + (chunk(b"tRNS", transparency) if transparency else b"")
        + metadata
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND")
    )


def chunk_types(raw):
    kinds, offset = [], 8
    while offset < len(raw):
        size, kind = struct.unpack_from(">I4s", raw, offset)
        kinds.append(kind)
        offset += size + 12
    return kinds


def svg(body):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800">'
        + body
        + "</svg>"
    )


def embedded(raw, **attributes):
    values = {
        "x": "90",
        "y": "165",
        "width": "1020",
        "height": "440",
        "href": "data:image/png;base64," + base64.b64encode(raw).decode(),
        **attributes,
    }
    return "<image " + " ".join(f'{key}="{value}"' for key, value in values.items()) + "/>"


class RasterPngTests(unittest.TestCase):
    def test_rgb_and_rgba_remove_metadata_and_merge_image_chunks_without_changing_pixels(self):
        for color, channels in ((2, 3), (6, 4)):
            with self.subTest(color=color):
                pixels = (b"\x00" + b"\x11\x22\x33\x44"[:channels] * 2) * 2
                compressed = zlib.compress(pixels)
                raw = (
                    PNG_SIGNATURE
                    + header(color=color)
                    + chunk(b"tEXt", b"Comment\x00private.example.ts.net")
                    + chunk(b"IDAT", compressed[:5])
                    + chunk(b"IDAT", compressed[5:])
                    + chunk(b"tIME", b"\x07\xea\x0a\x04\x00\x00\x00")
                    + chunk(b"IEND")
                )
                normalized = normalize_png(raw, 2, 2)
                self.assertEqual(chunk_types(normalized), [b"IHDR", b"IDAT", b"IEND"])
                self.assertNotIn(b"private.example", normalized)
                self.assertIn(compressed, normalized)
                validate_png(normalized, 2, 2)
                with self.assertRaisesRegex(ValueError, "normalized"):
                    validate_png(raw, 2, 2)

    def test_indexed_palette_and_transparency_survive_without_source_metadata(self):
        palette = bytes([0, 0, 0, 255, 255, 255])
        raw = png(color=3, palette=palette, transparency=b"\x00\xff", metadata=chunk(b"pHYs", b"\x00" * 9))
        normalized = normalize_png(raw, 2, 2)
        self.assertEqual(chunk_types(normalized), [b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"])
        self.assertIn(chunk(b"PLTE", palette), normalized)
        self.assertIn(chunk(b"tRNS", b"\x00\xff"), normalized)
        self.assertEqual(normalize_png(normalized, 2, 2), normalized)
        suggested = png(palette=palette)
        self.assertNotIn(b"PLTE", normalize_png(suggested, 2, 2))

    def test_palette_reconstruction_checks_every_filter_and_rejects_missing_colors(self):
        second_rows = {
            0: bytes([2, 1, 0]),
            1: bytes([2, 255, 255]),
            2: bytes([2, 0, 254]),
            3: bytes([2, 0, 255]),
            4: bytes([2, 255, 254]),
        }
        for method, row in second_rows.items():
            with self.subTest(method=method):
                scans = b"\x00\x00\x01\x02" + bytes([method]) + row
                raw = png(width=3, color=3, palette=bytes(range(9)), scanlines=scans)
                validate_png(raw, 3, 2)
                invalid = scans[:5] + b"\x03" + scans[6:]
                with self.assertRaisesRegex(ValueError, "palette entry"):
                    normalize_png(png(width=3, color=3, palette=bytes(range(9)), scanlines=invalid), 3, 2)

    def test_invalid_or_animated_formats_are_rejected(self):
        body = chunk(b"IDAT", zlib.compress(bytes(14))) + chunk(b"IEND")
        headers = [
            header(width=3),
            header(depth=16),
            header(color=0),
            header(color=4),
            header(interlace=1),
            header(compression=1),
            header(filtering=1),
        ]
        for value in headers:
            with self.subTest(header=value), self.assertRaises(ValueError):
                normalize_png(PNG_SIGNATURE + value + body, 2, 2)
        for kind in (b"acTL", b"fcTL", b"fdAT", b"ABCD", b"abca"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                normalize_png(PNG_SIGNATURE + header() + chunk(kind) + body, 2, 2)
        for dimensions in ((0, 2), (2, 0), (1021, 2), (2, 441), (True, 2)):
            with self.subTest(dimensions=dimensions), self.assertRaises(ValueError):
                normalize_png(png(), *dimensions)

    def test_structure_crc_truncation_order_and_extra_streams_fail_closed(self):
        ihdr = header()
        idat = chunk(b"IDAT", zlib.compress(bytes(14)))
        iend = chunk(b"IEND")
        good = PNG_SIGNATURE + ihdr + idat + iend
        cases = [
            b"not a PNG" + good,
            good[:-1],
            good + b"trailing",
            good + iend,
            good[:-12],
            good[:-1] + bytes([good[-1] ^ 1]),
            PNG_SIGNATURE + idat + ihdr + iend,
            PNG_SIGNATURE + ihdr + ihdr + idat + iend,
            PNG_SIGNATURE + ihdr + iend,
            PNG_SIGNATURE + ihdr + idat + chunk(b"tEXt", b"Comment\x00text") + idat + iend,
            PNG_SIGNATURE + ihdr + idat + chunk(b"pHYs", bytes(9)) + iend,
            PNG_SIGNATURE + ihdr + idat + chunk(b"PLTE", bytes(3)) + iend,
            PNG_SIGNATURE + ihdr + chunk(b"PLTE", bytes(3)) + chunk(b"sRGB", b"\x00") + idat + iend,
            PNG_SIGNATURE + ihdr + chunk(b"tEXt") * 1024 + idat + iend,
            PNG_SIGNATURE + ihdr + struct.pack(">I4s", 0x7FFFFFFF, b"IDAT"),
        ]
        for index, raw in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                normalize_png(raw, 2, 2)

    def test_palette_size_transparency_and_required_order_are_validated(self):
        indexed = header(color=3)
        body = chunk(b"IDAT", zlib.compress(bytes(6))) + chunk(b"IEND")
        for middle in (
            b"",
            chunk(b"PLTE", bytes(2)),
            chunk(b"PLTE", bytes(769)),
            chunk(b"PLTE", bytes(3)) * 2,
            chunk(b"tRNS", b"\x00") + chunk(b"PLTE", bytes(3)),
            chunk(b"PLTE", bytes(3)) + chunk(b"tRNS", b""),
            chunk(b"PLTE", bytes(3)) + chunk(b"tRNS", bytes(2)),
            chunk(b"PLTE", bytes(3)) + chunk(b"tRNS", b"\x00") * 2,
        ):
            with self.subTest(chunks=middle), self.assertRaises(ValueError):
                normalize_png(PNG_SIGNATURE + indexed + middle + body, 2, 2)
        with self.assertRaises(ValueError):
            normalize_png(png(transparency=b"\x00"), 2, 2)

    def test_inflation_is_bounded_and_filter_bytes_and_stream_end_are_checked(self):
        pixels = bytes(14)
        streams = [
            zlib.compress(pixels[:-1]),
            zlib.compress(pixels + b"\x00"),
            zlib.compress(bytes(4 * 1024 * 1024)),
            zlib.compress(pixels)[:-1],
            zlib.compress(pixels) + b"junk",
            zlib.compress(pixels) + zlib.compress(pixels),
            zlib.compress(b"\x05" + pixels[1:]),
        ]
        for index, compressed in enumerate(streams):
            raw = PNG_SIGNATURE + header() + chunk(b"IDAT", compressed) + chunk(b"IEND")
            with self.subTest(case=index), self.assertRaises(ValueError):
                normalize_png(raw, 2, 2)
        with self.assertRaises(ValueError):
            normalize_png(png() + bytes(MAX_PNG_BYTES), 2, 2)


class EmbeddedMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = png(width=1020, height=440)

    def test_passive_vectors_and_one_canonical_map_are_accepted(self):
        validate(svg('<path d="M90 165L100 180" fill="none" stroke="#123456"/>'))
        validate(svg(embedded(self.raw) + '<path d="M90 165L100 180" stroke="#123456"/>'))
        validate(svg(embedded(png(width=1020, height=440, color=3, palette=bytes(6)))))

    def test_only_the_fixed_frame_leaf_image_can_have_a_png_href(self):
        image = embedded(self.raw)
        bad = [
            image + image,
            "<g>" + image + "</g>",
            embedded(self.raw, x="0"),
            embedded(self.raw, width="100%"),
            embedded(self.raw, transform="translate(1 1)"),
            embedded(self.raw, onload="alert(1)"),
            embedded(self.raw, href="https://example.com/map.png"),
            embedded(self.raw, href="data:image/svg+xml;base64,PHN2Zy8+"),
            image.replace("href=", "xlink:href=").replace(
                "<image ", '<image xmlns:xlink="http://www.w3.org/1999/xlink" '
            ),
            image.replace("<image ", "<text "),
            image.replace("/>", "><script>alert(1)</script></image>"),
            '<rect fill="url(https://example.com/map.png)"/>',
            "<text>private.example.t&#115;.net</text>",
        ]
        for index, body in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(ValueError):
                validate(svg(body))
        with self.assertRaises(ValueError):
            validate(svg(image).replace('viewBox="', 'transform="translate(1 1)" viewBox="'))

    def test_embedded_metadata_bad_encoding_wrong_dimensions_and_size_are_rejected(self):
        cases = [
            embedded(png(width=1020, height=440, metadata=chunk(b"tEXt", b"Comment\x00metadata"))),
            embedded(png()),
            embedded(self.raw, href="data:image/png;base64,%%%"),
            embedded(self.raw).replace("base64,", "base64, "),
            embedded(self.raw).replace('"/>', '="/>'),
            "<text>" + "a" * (1024 * 1024) + "</text>",
        ]
        for index, body in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                validate(svg(body))


if __name__ == "__main__":
    unittest.main()
