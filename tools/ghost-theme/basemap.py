"""Acquire and cache public USGS Topo images for the aircraft map frame.

Network access is explicit at build time. Published images embed only normalized
pixels; readers never contact the map service or receive cache metadata.
"""

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from raster_png import MAX_PNG_BYTES, normalize_png
from raster_webp import MAX_WEBP_BYTES, RECIPE, encode_webp, validate_webp

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
# One Web Mercator definition serves the map frame, its place label and this export.
from wtf_adsb_publisher import public_events as public  # noqa: E402
from wtf_adsb_publisher import site_origin  # noqa: E402
from wtf_adsb_publisher.places import MAX_LATITUDE, mercator  # noqa: E402

SERVICE = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer"
ORIGIN = "basemap.nationalmap.gov"
WIDTH, HEIGHT = 1020, 440
RECIPE_VERSION = 1
# Platform math libraries can move the projected bounds by a few float steps.
BBOX_ULPS = 4


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Basemap requests must not redirect")


def download(url, limit):
    request = Request(url, headers={"User-Agent": site_origin.user_agent("WTF-ADSB-map-builder/1.0")})
    with build_opener(NoRedirect).open(request, timeout=45) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Basemap response exceeds its size limit")
    return raw


def request_spec(bounds):
    values = [bounds[key] for key in ("left", "bottom", "right", "top")]
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
        raise ValueError("Invalid basemap bounds")
    left, bottom, right, top = values
    if not (-180 <= left < right <= 180 and -MAX_LATITUDE <= bottom < top <= MAX_LATITUDE):
        return None
    xmin, ymin = mercator(left, bottom)
    xmax, ymax = mercator(right, top)
    # An export may expand one dimension to preserve aspect ratio. Reject a
    # mismatched request rather than silently misaligning the replay.
    if not math.isclose((xmax - xmin) / (ymax - ymin), WIDTH / HEIGHT, rel_tol=1e-8):
        raise ValueError("Basemap extent does not match the map frame")
    return {
        "recipe": RECIPE_VERSION,
        "service": SERVICE,
        "bbox": [xmin, ymin, xmax, ymax],
        "spatial_reference": 3857,
        "width": WIDTH,
        "height": HEIGHT,
        "format": "png",
    }


def verify_export(result, spec):
    extent = result.get("extent", {})
    reference = extent.get("spatialReference", {})
    if (
        result.get("width") != WIDTH
        or result.get("height") != HEIGHT
        or reference.get("latestWkid", reference.get("wkid")) not in (3857, 102100)
    ):
        raise ValueError("Basemap service returned a different frame")
    tolerance = (spec["bbox"][2] - spec["bbox"][0]) / WIDTH * 0.01
    for key, expected in zip(("xmin", "ymin", "xmax", "ymax"), spec["bbox"], strict=True):
        actual = extent.get(key)
        if (
            not isinstance(actual, (float, int))
            or not math.isfinite(actual)
            or abs(actual - expected) > tolerance
        ):
            raise ValueError("Basemap service adjusted the requested extent")
    source = urlsplit(result.get("href", ""))
    if (
        source.scheme != "https"
        or source.netloc != ORIGIN
        or not source.path.startswith("/arcgis/rest/directories/basemap_output/USGSTopo_MapServer/")
        or not source.path.endswith(".png")
        or source.query
        or source.fragment
        or "/../" in source.path
        or "%" in source.path
    ):
        raise ValueError("Basemap service returned an unexpected image URL")


def equivalent_request(cached, requested):
    """Allow only a few floating-point steps of difference in the map extent."""
    if not isinstance(cached, dict) or cached.keys() != requested.keys():
        return False
    for spec in (cached, requested):
        bbox = spec.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(type(value) in (int, float) and math.isfinite(value) for value in bbox)
            or not (bbox[0] < bbox[2] and bbox[1] < bbox[3])
        ):
            return False
    return all(
        type(cached[key]) is type(value) and cached[key] == value
        for key, value in requested.items()
        if key != "bbox"
    ) and all(
        math.nextafter(left, -math.inf, steps=BBOX_ULPS)
        <= right
        <= math.nextafter(left, math.inf, steps=BBOX_ULPS)
        for left, right in zip(cached["bbox"], requested["bbox"], strict=True)
    )


class BasemapCache:
    def __init__(self, directory, *, fetch=False, webp=False):
        self.directory = Path(directory)
        self.fetch = fetch
        # Re-encode backgrounds as WebP when that is smaller (pinned browser).
        self.webp = webp
        self.used = {}
        self._receipt_index = None
        self._resolved_keys = {}

    @staticmethod
    def key(spec):
        return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _read_receipt(path):
        if path.stat().st_size > 32768:
            raise ValueError("Cached basemap exceeds its size limit")
        receipt = json.loads(path.read_text())
        if not isinstance(receipt, dict):
            raise ValueError("Cached basemap does not match its request or checksum")
        return receipt

    def _load_receipts(self):
        index = []
        for path in self.directory.glob("*.json"):
            key = path.stem
            # WebP receipts and unrelated files are not map requests.
            if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
                continue
            receipt = self._read_receipt(path)
            spec = receipt.get("request")
            if not isinstance(spec, dict) or self.key(spec) != key or not equivalent_request(spec, spec):
                raise ValueError("Cached basemap does not match its request or checksum")
            index.append((key, spec))
        self._receipt_index = index

    def cached_key(self, spec):
        """Find an exact or unique equivalent receipt, without reading its pixels."""
        key = self.key(spec)
        if (self.directory / (key + ".json")).exists():
            return key
        if key not in self._resolved_keys:
            if self._receipt_index is None:
                self._load_receipts()
            matches = [
                cached_key
                for cached_key, cached_spec in self._receipt_index
                if equivalent_request(cached_spec, spec)
            ]
            if len(matches) > 1:
                raise ValueError("Ambiguous cached basemap extent")
            self._resolved_keys[key] = matches[0] if matches else None
        return self._resolved_keys[key]

    def image(self, bounds):
        """(pixels, media type) for the frame, or None outside the export's range.

        With WebP enabled, each background is encoded once, recorded beside its
        PNG with the encoding recipe, and used only when it is the smaller file.
        """
        key, raw = self._get(bounds)
        if raw is None or not self.webp:
            return None if raw is None else (raw, "image/png")
        image_path = self.directory / (key + ".webp")
        receipt_path = self.directory / (key + ".webp.json")
        source = hashlib.sha256(raw).hexdigest()
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if receipt.get("png_sha256") == source and receipt.get("recipe") == RECIPE:
            if image_path.stat().st_size > MAX_WEBP_BYTES:
                raise ValueError("Cached WebP basemap exceeds its size limit")
            data = image_path.read_bytes()
            if receipt.get("webp_sha256") != hashlib.sha256(data).hexdigest():
                raise ValueError("Cached WebP basemap does not match its checksum")
            validate_webp(data)
        else:
            data = encode_webp(raw)
            public.write_bytes_atomic(image_path, data, private=True)
            receipt = {
                "png_sha256": source,
                "webp_sha256": hashlib.sha256(data).hexdigest(),
                "recipe": RECIPE,
            }
            public.write_bytes_atomic(
                receipt_path, (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode(), private=True
            )
        chosen = (data, "image/webp") if len(data) < len(raw) else (raw, "image/png")
        # The media audit records which encoding each map embedded.
        self.used[key] = {**self.used[key], "webp": receipt, "embedded": chosen[1]}
        return chosen

    def get(self, bounds):
        _, raw = self._get(bounds)
        return raw

    def _get(self, bounds):
        spec = request_spec(bounds)
        if spec is None:
            return None, None
        cached_key = self.cached_key(spec)
        key = cached_key or self.key(spec)
        receipt_path = self.directory / (key + ".json")
        image_path = self.directory / (key + ".png")
        if cached_key is not None:
            receipt = self._read_receipt(receipt_path)
            if image_path.stat().st_size > MAX_PNG_BYTES:
                raise ValueError("Cached basemap exceeds its size limit")
            raw = image_path.read_bytes()
            cached_spec = receipt.get("request")
            if (
                not equivalent_request(cached_spec, spec)
                or self.key(cached_spec) != key
                or receipt.get("png_sha256") != hashlib.sha256(raw).hexdigest()
            ):
                raise ValueError("Cached basemap does not match its request or checksum")
            if normalize_png(raw) != raw:
                raise ValueError("Cached basemap is not a normalized PNG")
            verify_export(receipt["export"], cached_spec)
        else:
            if not self.fetch:
                raise ValueError("Basemap is not cached; rerun explicitly with --fetch-basemaps")
            parameters = {
                "f": "json",
                "bbox": ",".join(str(value) for value in spec["bbox"]),
                "bboxSR": "3857",
                "imageSR": "3857",
                "size": f"{WIDTH},{HEIGHT}",
                "format": "png",
                "transparent": "false",
            }
            result = json.loads(download(SERVICE + "/export?" + urlencode(parameters), 32768))
            verify_export(result, spec)
            raw = normalize_png(download(result["href"], MAX_PNG_BYTES))
            receipt = {
                "request": spec,
                "export": result,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "png_sha256": hashlib.sha256(raw).hexdigest(),
                "credit": "USGS The National Map",
            }
            self.directory.mkdir(parents=True, exist_ok=True)
            public.write_bytes_atomic(image_path, raw, private=True)
            public.write_bytes_atomic(
                receipt_path, (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode(), private=True
            )
            # Include this explicit fetch in the existing index, and reconsider
            # memoized misses or matches that may now be ambiguous.
            self._receipt_index.append((key, spec))
            self._resolved_keys.clear()
        self.used[key] = receipt
        return key, raw
