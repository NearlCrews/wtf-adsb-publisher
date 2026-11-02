"""Bound and normalize the PNG subset used by static map backgrounds.

PNG structure follows https://www.w3.org/TR/png-3/. Only noninterlaced, 8-bit
RGB, RGBA and indexed images are accepted. Metadata never reaches the SVG.
"""

import re
import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 700 * 1024
MAX_CHUNKS = 1024
MAP_WIDTH, MAP_HEIGHT = 1020, 440
_BEFORE_PALETTE = {b"cHRM", b"gAMA", b"iCCP", b"sBIT", b"sRGB", b"cICP", b"mDCV", b"cLLI"}
_BEFORE_DATA = _BEFORE_PALETTE | {b"bKGD", b"hIST", b"pHYs", b"sPLT", b"eXIf"}


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _check_palette_indices(scanlines, width, height, entries):
    """Undo indexed filters only when some byte values would lack a palette entry."""
    if entries == 256:
        return
    previous = bytearray(width)
    for row_number in range(height):
        offset = row_number * (width + 1)
        method = scanlines[offset]
        row = bytearray(scanlines[offset + 1 : offset + 1 + width])
        if method:
            for index in range(width):
                left = row[index - 1] if index else 0
                above = previous[index]
                if method == 1:
                    predictor = left
                elif method == 2:
                    predictor = above
                elif method == 3:
                    predictor = (left + above) // 2
                else:
                    upper_left = previous[index - 1] if index else 0
                    estimate = left + above - upper_left
                    distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
                    predictor = (left, above, upper_left)[distances.index(min(distances))]
                row[index] = (row[index] + predictor) & 255
        if max(row) >= entries:
            raise ValueError("PNG pixel has no palette entry")
        previous = row


def normalize_png(raw, width=MAP_WIDTH, height=MAP_HEIGHT):
    """Return one static PNG with only required pixel and palette chunks.

    Compressed input is capped at 700 KiB. Inflation stops after the exact
    expected scanline length plus one byte, so a compressed bomb cannot expand
    beyond the fixed map frame. The compressed pixels themselves are retained.
    """
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
        or not 1 <= width <= MAP_WIDTH
        or not 1 <= height <= MAP_HEIGHT
    ):
        raise ValueError("Unsupported PNG dimensions")
    if not isinstance(raw, bytes) or len(raw) > MAX_PNG_BYTES or not raw.startswith(PNG_SIGNATURE):
        raise ValueError("Invalid PNG signature or size")
    offset, count = len(PNG_SIGNATURE), 0
    header = palette = transparency = None
    compressed = bytearray()
    data_started = data_ended = ended = False
    color = None
    while offset < len(raw):
        count += 1
        if count > MAX_CHUNKS or len(raw) - offset < 12:
            raise ValueError("Invalid PNG chunk count or length")
        length, kind = struct.unpack_from(">I4s", raw, offset)
        end = offset + 12 + length
        if end > len(raw) or not re.fullmatch(rb"[A-Za-z]{2}[A-Z][A-Za-z]", kind):
            raise ValueError("Invalid PNG chunk length or type")
        data = raw[offset + 8 : end - 4]
        checksum = struct.unpack_from(">I", raw, end - 4)[0]
        if zlib.crc32(kind + data) != checksum:
            raise ValueError("PNG chunk checksum mismatch")
        offset = end
        if header is None and kind != b"IHDR":
            raise ValueError("PNG must start with IHDR")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise ValueError("Invalid PNG header")
            actual_width, actual_height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            if (
                (actual_width, actual_height) != (width, height)
                or depth != 8
                or color not in {2, 3, 6}
                or (compression, filtering, interlace) != (0, 0, 0)
            ):
                raise ValueError("Unsupported PNG format or dimensions")
            header = data
        elif kind == b"PLTE":
            if (
                palette is not None
                or data_started
                or transparency is not None
                or not 3 <= length <= 768
                or length % 3
            ):
                raise ValueError("Invalid PNG palette")
            palette = data
        elif kind == b"tRNS":
            if (
                color != 3
                or palette is None
                or transparency is not None
                or data_started
                or not 1 <= length <= len(palette) // 3
            ):
                raise ValueError("Invalid PNG palette transparency")
            transparency = data
        elif kind == b"IDAT":
            if data_ended or (color == 3 and palette is None):
                raise ValueError("Invalid PNG image-data order")
            data_started = True
            compressed.extend(data)
        elif kind == b"IEND":
            if length or not data_started or offset != len(raw):
                raise ValueError("Invalid PNG trailer")
            ended = True
            break
        elif kind in {b"acTL", b"fcTL", b"fdAT"} or not kind[0] & 32:
            raise ValueError("Animated or unsupported critical PNG chunk")
        else:
            if (kind in _BEFORE_DATA and data_started) or (kind in _BEFORE_PALETTE and palette is not None):
                raise ValueError("Invalid PNG metadata order")
            if data_started:
                data_ended = True
    if not ended:
        raise ValueError("PNG is missing its trailer")
    channels = {2: 3, 3: 1, 6: 4}[color]
    stride = width * channels + 1
    expected = stride * height
    inflater = zlib.decompressobj()
    try:
        scanlines = inflater.decompress(compressed, expected + 1)
    except zlib.error as error:
        raise ValueError("Invalid PNG compressed pixels") from error
    if len(scanlines) != expected or not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
        raise ValueError("PNG pixel data has an invalid length or trailing stream")
    if any(method > 4 for method in scanlines[::stride]):
        raise ValueError("Unsupported PNG scanline filter")
    if color == 3:
        _check_palette_indices(scanlines, width, height, len(palette) // 3)
    normalized = PNG_SIGNATURE + _chunk(b"IHDR", header)
    if color == 3:
        normalized += _chunk(b"PLTE", palette)
        if transparency is not None:
            normalized += _chunk(b"tRNS", transparency)
    return normalized + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")


def validate_png(raw, width=MAP_WIDTH, height=MAP_HEIGHT):
    """Require the metadata-free canonical representation used inside SVGs."""
    if normalize_png(raw, width, height) != raw:
        raise ValueError("Embedded PNG must be normalized")
