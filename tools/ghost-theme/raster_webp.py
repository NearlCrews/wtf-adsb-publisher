"""Bound and encode the WebP subset used for static map backgrounds.

The container follows RFC 9649. Only still images are accepted: one VP8 or
VP8L bitstream, optionally behind a VP8X header with an ALPH chunk. Metadata
(EXIF, XMP, ICC) and animation chunks are refused, so only pixels reach an
SVG. Encoding uses the pinned Chromium through webp.mjs, one browser session
for every image in a run.
"""

import atexit
import json
import os
import select
import struct
import subprocess
import threading
import time
from pathlib import Path

from raster_png import MAP_HEIGHT, MAP_WIDTH, MAX_PNG_BYTES

TOOLS = Path(__file__).resolve().parent
MAX_WEBP_BYTES = MAX_PNG_BYTES
QUALITY = 0.8
# A browser or quality change re-encodes cached backgrounds. A browser override
# (CHROMIUM_PATH) has no pinned version, so its path names it, as for social cards.
RECIPE = {
    "version": 1,
    "quality": QUALITY,
    "browser": (
        "path:" + os.environ["CHROMIUM_PATH"]
        if os.environ.get("CHROMIUM_PATH")
        else json.loads((TOOLS / "browser-version.json").read_text())["version"]
    ),
}
LAYOUTS = ([b"VP8 "], [b"VP8L"], [b"VP8X", b"VP8 "], [b"VP8X", b"ALPH", b"VP8 "], [b"VP8X", b"VP8L"])


def _chunks(raw):
    offset, chunks = 12, []
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("Truncated WebP chunk header")
        kind = raw[offset : offset + 4]
        (size,) = struct.unpack("<I", raw[offset + 4 : offset + 8])
        start, end = offset + 8, offset + 8 + size
        if end > len(raw):
            raise ValueError("Truncated WebP chunk")
        # An odd-sized chunk is followed by one zero pad byte.
        if size & 1 and raw[end : end + 1] != b"\0":
            raise ValueError("Missing or nonzero WebP pad byte")
        chunks.append((kind, raw[start:end]))
        offset = end + (size & 1)
    if offset != len(raw):
        raise ValueError("Trailing bytes after the last WebP chunk")
    return chunks


def _dimensions(kind, data):
    if kind == b"VP8 ":
        if len(data) < 10 or data[3:6] != b"\x9d\x01\x2a":
            raise ValueError("Invalid VP8 frame header")
        # RFC 6386 frame tag: a shown key frame whose first partition fits.
        tag = int.from_bytes(data[:3], "little")
        if tag & 1 or not tag & 0x10 or (tag >> 1) & 7 > 3 or tag >> 5 > len(data) - 10:
            raise ValueError("Invalid VP8 frame tag")
        width, height = struct.unpack("<HH", data[6:10])
        return width & 0x3FFF, height & 0x3FFF
    if len(data) < 5 or data[0] != 0x2F:
        raise ValueError("Invalid VP8L header")
    bits = int.from_bytes(data[1:5], "little")
    # The three version bits must be zero.
    if bits >> 29:
        raise ValueError("Unsupported VP8L version")
    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1


def validate_webp(raw, width=MAP_WIDTH, height=MAP_HEIGHT):
    """Return the bytes of a still, metadata-free WebP of the expected size."""
    if not isinstance(raw, bytes) or not 20 <= len(raw) <= MAX_WEBP_BYTES:
        raise ValueError("WebP size is out of bounds")
    if raw[:4] != b"RIFF" or raw[8:12] != b"WEBP" or struct.unpack("<I", raw[4:8])[0] != len(raw) - 8:
        raise ValueError("Invalid WebP container")
    chunks = _chunks(raw)
    if [kind for kind, _ in chunks] not in LAYOUTS:
        raise ValueError("Unsupported WebP chunks; only still pixels are accepted")
    if chunks[0][0] == b"VP8X":
        header = chunks[0][1]
        flags = header[0] if header else 0xFF
        # ICC, EXIF, XMP and animation flags must be clear.
        if len(header) != 10 or flags & 0b00101110:
            raise ValueError("Extended WebP header declares metadata or animation")
        canvas = (int.from_bytes(header[4:7], "little") + 1, int.from_bytes(header[7:10], "little") + 1)
        if canvas != (width, height):
            raise ValueError("WebP canvas differs from the map frame")
    if _dimensions(*chunks[-1]) != (width, height):
        raise ValueError("WebP dimensions differ from the map frame")
    return raw


def _chunk(kind, data):
    return kind + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"")


def normalize_webp(raw):
    """Keep only the pixel chunks of an encoder's output.

    Chromium tags canvas exports with an sRGB ICC profile; an untagged image
    is already read as sRGB, so the profile is dropped with any metadata.
    """
    if (
        not isinstance(raw, bytes)
        or len(raw) > 4 * MAX_WEBP_BYTES
        or raw[:4] != b"RIFF"
        or raw[8:12] != b"WEBP"
    ):
        raise ValueError("Invalid WebP container")
    chunks = dict(_chunks(raw))
    stream = next((kind for kind in (b"VP8 ", b"VP8L") if kind in chunks), None)
    if stream is None:
        raise ValueError("WebP has no still image stream")
    body = _chunk(stream, chunks[stream])
    if b"ALPH" in chunks and stream == b"VP8 ":
        width, height = _dimensions(stream, chunks[stream])
        header = (
            bytes([0b00010000, 0, 0, 0])
            + (width - 1).to_bytes(3, "little")
            + (height - 1).to_bytes(3, "little")
        )
        body = _chunk(b"VP8X", header) + _chunk(b"ALPH", chunks[b"ALPH"]) + body
    payload = b"WEBP" + body
    return validate_webp(b"RIFF" + struct.pack("<I", len(payload)) + payload)


# One image may take this long, including the browser's start for the first.
ENCODE_SECONDS = 180


class _Encoder:
    """One webp.mjs process, and so one browser session, for every image in a run.

    Requests and replies are frames: a 4-byte big-endian length and the bytes.
    An empty reply means that image could not be encoded; the session stays.
    """

    def __init__(self, quality):
        self.process = subprocess.Popen(
            ["node", str(TOOLS / "webp.mjs"), "--frames", "--quality", str(quality)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        os.set_blocking(self.process.stdin.fileno(), False)

    def _write(self, frame, deadline):
        descriptor = self.process.stdin.fileno()
        while frame:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [descriptor], [], remaining)[1]:
                raise TimeoutError("WebP encoder did not accept the image")
            try:
                written = os.write(descriptor, frame)
            except BlockingIOError:
                continue
            if not written:
                raise EOFError("WebP encoder stopped")
            frame = frame[written:]

    def _read(self, size, deadline):
        data = bytearray()
        descriptor = self.process.stdout.fileno()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]:
                raise TimeoutError("WebP encoder did not answer")
            chunk = os.read(descriptor, size - len(data))
            if not chunk:
                raise EOFError("WebP encoder stopped")
            data += chunk
        return bytes(data)

    def encode(self, png):
        deadline = time.monotonic() + ENCODE_SECONDS
        self._write(memoryview(struct.pack(">I", len(png)) + png), deadline)
        (size,) = struct.unpack(">I", self._read(4, deadline))
        if size > 4 * MAX_WEBP_BYTES:
            raise ValueError("WebP encoder reply exceeds its size limit")
        return self._read(size, deadline)

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()


_encoders = {}
_lock = threading.Lock()


@atexit.register
def close_encoders():
    """Stop every open encoder session; the next image starts a new one."""
    with _lock:
        while _encoders:
            _encoders.popitem()[1].close()


def encode_webp(png, *, quality=QUALITY):
    """WebP pixels for a validated PNG, from the pinned browser's encoder."""
    with _lock:
        encoder = _encoders.get(quality)
        try:
            if encoder is None:
                encoder = _encoders[quality] = _Encoder(quality)
            raw = encoder.encode(png)
        except (OSError, EOFError, TimeoutError, ValueError) as error:
            # A broken session is replaced on the next call.
            _encoders.pop(quality, None)
            if encoder is not None:
                encoder.close()
            raise ValueError("WebP encoding failed; install the pinned test browser") from error
    if not raw:
        raise ValueError("WebP encoding failed for this image")
    return normalize_webp(raw)
