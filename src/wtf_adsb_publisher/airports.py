"""The public-use airport nearest an aircraft's saved positions.

A story may say that a track came within a distance of an airport. The airport
comes from the pinned OurAirports runway dataset (airport_context), the
positions from the aircraft alone; no receiver location, range or bearing enters.
Only public-use idents count: four letters, or an FAA location identifier of
three characters, digits included (Brighton, 45G). A private-use field's FAA
identifier is four characters with a digit (MI45), and naming one would point at
a family's field. Runway proximity establishes no landing, take-off or height
above terrain, and the sentence never says so.
"""

import math
import re
from functools import lru_cache

from . import airport_context
from .rules import finite, number

# Four letters (an ICAO ident) or any three characters (an FAA location identifier); four characters
# with a digit ("MI45", "0MI3") is the FAA's private-use pattern.
PUBLIC_IDENT = re.compile(r"(?:[A-Z]{4}|[A-Z0-9]{3})")
# Ten statute miles.
MAX_NM = 8.7
# A box this wide in degrees holds every runway end within MAX_NM of a point, plus the
# longest runway, up to 50 degrees north (the dataset's northern edge).
BOX = 0.3
NM_PER_DEGREE = 60.0


@lru_cache(maxsize=1)
def _airports():
    """{ident: {"name", "segments": [((lat, lon), (lat, lon)), ...]}} for public-use idents."""
    found = {}
    for runway in airport_context.runway_data():
        ident, name, ends = runway.get("airport_ident"), runway.get("airport_name"), runway.get("ends")
        if not isinstance(ident, str) or not PUBLIC_IDENT.fullmatch(ident) or not isinstance(name, str):
            continue
        if not isinstance(ends, list) or len(ends) != 2:
            continue
        (lon1, lat1), (lon2, lat2) = ends
        entry = found.setdefault(ident, {"name": name, "segments": []})
        entry["segments"].append(((lat1, lon1), (lat2, lon2)))
    return found


def _position(raw):
    if not isinstance(raw, dict):
        return None
    lat, lon = number(raw.get("lat")), number(raw.get("lon"))
    return (lat, lon) if finite(lat) and finite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _segment_nm(point, start, end):
    """Distance from a point to a runway segment, in nautical miles, on a local flat projection."""
    scale = math.cos(math.radians(point[0]))

    def xy(place):
        return (place[1] - point[1]) * scale, place[0] - point[0]

    ax, ay = xy(start)
    bx, by = xy(end)
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    share = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
    return math.hypot(ax + share * dx, ay + share * dy) * NM_PER_DEGREE


def distance_nm(ident, point):
    """The distance in NM from a position to the nearest runway of one airport, or None."""
    airport, position = _airports().get(ident), _position(point)
    if airport is None or position is None:
        return None
    return min(_segment_nm(position, start, end) for start, end in airport["segments"])


def ranked(points, *, limit_nm=MAX_NM):
    """Each public airport whose runways come within limit_nm of a position, closest first, as
    (distance in NM, ident, index of its closest position), from one scan. Positions outside
    the dataset's coverage are ignored."""
    positions = [(index, found) for index, raw in enumerate(points or ()) if (found := _position(raw))]
    if not positions or not airport_context.available():
        return []
    west, south, east, north = airport_context.coverage()
    positions = [(index, p) for index, p in positions if south <= p[0] <= north and west <= p[1] <= east]
    if not positions:
        return []
    lats = [p[0] for _, p in positions]
    lons = [p[1] for _, p in positions]
    box = (min(lats) - BOX, max(lats) + BOX, min(lons) - BOX, max(lons) + BOX)
    best = {}
    for ident, airport in _airports().items():
        for start, end in airport["segments"]:
            if not (box[0] <= start[0] <= box[1] and box[2] <= start[1] <= box[3]):
                continue
            for index, point in positions:
                if abs(point[0] - start[0]) > BOX or abs(point[1] - start[1]) > BOX:
                    continue
                distance = _segment_nm(point, start, end)
                if distance <= limit_nm and (ident not in best or distance < best[ident][0]):
                    best[ident] = (distance, index)
    # A stable sort, so equal distances keep the dataset's order, as one scan for the minimum did.
    return sorted(
        ((distance, ident, index) for ident, (distance, index) in best.items()), key=lambda item: item[0]
    )


def entry(found):
    """A ranked() item as {"name", "ident", "nm" (one decimal), "at" (index of the closest position)}."""
    distance, ident, index = found
    return {"name": _airports()[ident]["name"], "ident": ident, "nm": round(distance, 1), "at": index}


def nearest(points, *, exclude=frozenset(), limit_nm=MAX_NM):
    """The public airport whose runways come closest to any position, within limit_nm, or None.

    Returns entry()'s shape. Positions outside the dataset's coverage are ignored.
    """
    return next(
        (entry(found) for found in ranked(points, limit_nm=limit_nm) if found[1] not in exclude), None
    )
