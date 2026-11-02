"""Exercise the encoder's framed pipe protocol without starting a browser."""

import hashlib
import subprocess
import sys
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import raster_webp


@contextmanager
def encoder_process(script):
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    try:
        with patch.object(raster_webp.subprocess, "Popen", return_value=process):
            encoder = raster_webp._Encoder(raster_webp.QUALITY)
        yield encoder
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


class EncoderPipeTests(unittest.TestCase):
    def test_input_backpressure_obeys_the_encoding_deadline(self):
        # The child exits after two seconds as a backstop for a blocking writer.
        # The real deadline must win while the image is still filling the pipe.
        with (
            encoder_process("import time; time.sleep(2)") as encoder,
            patch.object(raster_webp, "ENCODE_SECONDS", 0.05),
        ):
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                encoder.encode(b"x" * 1_000_000)
            self.assertLess(time.monotonic() - started, 1)

    def test_partial_pipe_io_preserves_frames_across_images(self):
        script = """
import hashlib
import struct
import sys
import time

for _ in range(2):
    (remaining,) = struct.unpack(">I", sys.stdin.buffer.read(4))
    digest = hashlib.sha256()
    while remaining:
        chunk = sys.stdin.buffer.read(min(remaining, 1024))
        if not chunk:
            raise SystemExit(1)
        digest.update(chunk)
        remaining -= len(chunk)
        time.sleep(0.0001)
    reply = struct.pack(">I", digest.digest_size) + digest.digest()
    for offset in range(0, len(reply), 3):
        sys.stdout.buffer.write(reply[offset:offset + 3])
        sys.stdout.buffer.flush()
        time.sleep(0.0001)
"""
        with (
            encoder_process(script) as encoder,
            patch.object(raster_webp, "ENCODE_SECONDS", 5),
        ):
            for payload in [bytes(range(256)) * 4096, b"second\0image" * 1024]:
                with self.subTest(size=len(payload)):
                    self.assertEqual(encoder.encode(payload), hashlib.sha256(payload).digest())

    def test_waiting_for_reply_obeys_the_encoding_deadline(self):
        script = """
import struct
import sys
import time

(size,) = struct.unpack(">I", sys.stdin.buffer.read(4))
sys.stdin.buffer.read(size)
time.sleep(2)
"""
        with (
            encoder_process(script) as encoder,
            patch.object(raster_webp, "ENCODE_SECONDS", 0.05),
        ):
            with self.assertRaises(TimeoutError):
                encoder.encode(b"image")


if __name__ == "__main__":
    unittest.main()
