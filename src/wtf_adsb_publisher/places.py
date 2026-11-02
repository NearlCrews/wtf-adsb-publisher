"""Name the public place nearest an aircraft's saved positions.

Stories and track images share this routine, so a story names the same town as
its map. The map frame is the Web Mercator view the track image draws, and the
place is the nearest pinned city point to that frame's centre, within 50 km.
Only aircraft positions enter the calculation; no receiver location, range or
bearing does. The regional bundle ships in the package's ``data`` directory;
the PLACES_BUNDLE environment variable names another copy. A process whose
bundle is missing or unreadable has no towns and no place names.
"""

import math
import os
from functools import lru_cache
from pathlib import Path

from . import reference, rules


def _bundle_path():
    """PLACES_BUNDLE, else the package's own copy."""
    if configured := os.environ.get("PLACES_BUNDLE"):
        return Path(configured)
    return Path(__file__).resolve().parent / "data" / "great-lakes-v1.json"


DATA_PATH = _bundle_path()
DATA_KEYS = ("coverage", "lakes", "rivers", "boundaries", "places")
RADIUS = 6378137
MAX_LATITUDE = 85.0511287798066
NEAR_KM = 50
# Postal abbreviations for the regions the bundle names.
REGION_CODES = {
    "Delaware": "DE",
    "District of Columbia": "DC",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kentucky": "KY",
    "Maryland": "MD",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Missouri": "MO",
    "New Jersey": "NJ",
    "New York": "NY",
    "Ohio": "OH",
    "Ontario": "ON",
    "Pennsylvania": "PA",
    "Québec": "QC",
    "Virginia": "VA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
}


def content_hash(data):
    return reference.canonical_hash({key: data[key] for key in DATA_KEYS})


def load(path):
    if path is None:
        raise FileNotFoundError("No regional places bundle is configured")
    try:
        return reference.pinned_json(path, keys=DATA_KEYS)
    except ValueError as error:
        raise ValueError("Geographic data version or content hash differs from its manifest") from error


@lru_cache(maxsize=1)
def dataset():
    return load(DATA_PATH)


@lru_cache(maxsize=1)
def _town_names():
    found = dataset()["places"]
    if not isinstance(found, list):
        raise TypeError("The places bundle lists no places")
    return frozenset(
        place["name"]
        for place in found
        if isinstance(place, dict) and place.get("kind") == "city" and isinstance(place.get("name"), str)
    )


def town_names():
    """The regional towns in the pinned bundle, the only place names that hint at the reception
    region. A process without the bundle (the collector container) gets none; a failed read is
    not kept, so the next call tries again."""
    try:
        return _town_names()
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()


def mercator(longitude, latitude):
    return RADIUS * math.radians(longitude), RADIUS * math.asinh(math.tan(math.radians(latitude)))


def latitude_from_y(y):
    return math.degrees(math.atan(math.sinh(y / RADIUS)))


def intersects(a, b):
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def inside(point, bounds):
    return bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]


def distance_km(a, b):
    """Great-circle distance between (longitude, latitude) points."""
    return rules.distance_nm((a[1], a[0]), (b[1], b[0])) * 1.852


def local_projection(points):
    """Local equirectangular view; longitude wrapping avoids a dateline jump."""
    anchor = points[0]["lon"]
    longitudes = [anchor + (p["lon"] - anchor + 180) % 360 - 180 for p in points]
    latitudes = [p["lat"] for p in points]
    center_lat = (min(latitudes) + max(latitudes)) / 2
    center_lon = (min(longitudes) + max(longitudes)) / 2
    cosine = max(0.01, math.cos(math.radians(center_lat)))
    x_span = max((max(longitudes) - min(longitudes)) * cosine, 0.005)
    y_span = max(max(latitudes) - min(latitudes), 0.005)
    x_span = max(x_span, y_span * 1020 / 440) * 1.16
    y_span = x_span * 440 / 1020

    def project(point):
        longitude = anchor + (point["lon"] - anchor + 180) % 360 - 180
        return (
            600 + (longitude - center_lon) * cosine / x_span * 1020,
            385 - (point["lat"] - center_lat) / y_span * 440,
        )

    return project, {
        "left": center_lon - x_span / cosine / 2,
        "right": center_lon + x_span / cosine / 2,
        "bottom": center_lat - y_span / 2,
        "top": center_lat + y_span / 2,
        "scale_latitude": center_lat,
    }


def projection(points):
    """Fit aircraft positions to the track image's Web Mercator frame."""
    if any(abs(point["lat"]) > MAX_LATITUDE for point in points):
        return local_projection(points)
    anchor = points[0]["lon"]

    def position(point):
        longitude = anchor + (point["lon"] - anchor + 180) % 360 - 180
        return mercator(longitude, point["lat"])

    coordinates = [position(point) for point in points]
    xs, ys = zip(*coordinates, strict=True)
    center_x, center_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    # Stay within the source's useful zoom range even for very short excerpts.
    x_span = max(max(xs) - min(xs), (max(ys) - min(ys)) * 1020 / 440, 3000) * 1.16
    y_span = x_span * 440 / 1020

    def project(point):
        x, y = position(point)
        return 600 + (x - center_x) / x_span * 1020, 385 - (y - center_y) / y_span * 440

    return project, {
        "left": math.degrees((center_x - x_span / 2) / RADIUS),
        "right": math.degrees((center_x + x_span / 2) / RADIUS),
        "bottom": latitude_from_y(center_y - y_span / 2),
        "top": latitude_from_y(center_y + y_span / 2),
        "scale_latitude": latitude_from_y(center_y),
    }


def frame_center(bounds):
    """The (longitude, latitude) centre of a map frame's corner coordinates."""
    return ((bounds["left"] + bounds["right"]) / 2, (bounds["bottom"] + bounds["top"]) / 2)


def nearest_city(center, data):
    """The pinned city nearest a frame centre inside coverage, within 50 km."""
    cities = [p for p in data["places"] if p["kind"] == "city"]
    nearest = min(cities, key=lambda p: distance_km(center, p["point"]), default=None)
    if nearest and inside(center, data["coverage"]) and distance_km(center, nearest["point"]) <= NEAR_KM:
        return nearest
    return None


def describe(city):
    """Public place fields: {"name": "Coleman, Michigan", "short": "Coleman, MI", "state": "Michigan"}."""
    region = city.get("region")
    return {
        "name": city["name"] + (", " + region if region else ""),
        "short": city["name"] + (", " + REGION_CODES.get(region, region) if region else ""),
        "state": region,
    }


def label(place):
    """The heading a track image prints for a place, or its regional fallback."""
    return "Near " + place["name"] if place else "Great Lakes region"


def _positions(parts_or_points):
    items = list(parts_or_points or [])
    flat = [point for part in items for point in part] if items and isinstance(items[0], list) else items
    points = []
    for point in flat:
        if not isinstance(point, dict):
            continue
        lat, lon = point.get("lat"), point.get("lon")
        if rules.finite(lat) and rules.finite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            points.append({"lat": lat, "lon": lon})
    return points


def nearest_place(parts_or_points, *, data=None):
    """Return {"name", "short", "state"} for a track, or None.

    Accepts drawn parts (lists of position dicts) or a flat list of positions.
    Invalid positions are skipped. A missing or damaged regional bundle yields
    None, so a caller's publication never depends on this label.
    """
    points = _positions(parts_or_points)
    if not points:
        return None
    if data is None:
        try:
            data = dataset()
        except (OSError, ValueError, KeyError, TypeError):
            return None
    _, bounds = projection(points)
    city = nearest_city(frame_center(bounds), data)
    return describe(city) if city else None
