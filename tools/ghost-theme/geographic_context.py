"""Render a small, offline geographic backdrop around aircraft observations.

Only the public data bundle and the caller's aircraft-map projection are used.
Natural Earth's generalized outlines provide context, not navigation detail.
"""

import math
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
# Stories name the same place as the map heading through these shared routines,
# from the one verified, cached copy of the regional bundle (places.dataset).
from wtf_adsb_publisher.places import (  # noqa: E402
    dataset,
    describe,
    distance_km,
    frame_center,
    inside,
    intersects,
    nearest_city,
)
from wtf_adsb_publisher.places import label as place_label  # noqa: E402

MAP_FRAME = (90, 165, 1110, 605)
MAX_LABELS = 5


def clip_segment(start, end, bounds):
    """Liang–Barsky clipping keeps crossing segments even with both ends outside."""
    x, y = start
    dx, dy = end[0] - x, end[1] - y
    low, high = 0.0, 1.0
    for p, q in ((-dx, x - bounds[0]), (dx, bounds[2] - x), (-dy, y - bounds[1]), (dy, bounds[3] - y)):
        if p == 0:
            if q < 0:
                return None
            continue
        ratio = q / p
        if p < 0:
            low = max(low, ratio)
        else:
            high = min(high, ratio)
        if low > high:
            return None
    return [(x + low * dx, y + low * dy), (x + high * dx, y + high * dy)]


def clip_lines(points, bounds):
    """Return separate pieces instead of connecting across an off-map excursion."""
    pieces = []
    for first, last in zip(points, points[1:], strict=False):
        clipped = clip_segment(first, last, bounds)
        if clipped is None or clipped[0] == clipped[1]:
            continue
        if pieces and pieces[-1][-1] == clipped[0]:
            pieces[-1].append(clipped[1])
        else:
            pieces.append(clipped)
    return pieces


def clip_ring(points, bounds):
    """Sutherland–Hodgman clipping against a rectangular map frame."""
    result = list(points)
    if result and result[0] == result[-1]:
        result.pop()
    for axis, boundary, lower in (
        (0, bounds[0], True),
        (0, bounds[2], False),
        (1, bounds[1], True),
        (1, bounds[3], False),
    ):
        source, result = result, []
        if not source:
            break
        previous = source[-1]
        previous_inside = previous[axis] >= boundary if lower else previous[axis] <= boundary
        for current in source:
            current_inside = current[axis] >= boundary if lower else current[axis] <= boundary
            if current_inside != previous_inside:
                ratio = (boundary - previous[axis]) / (current[axis] - previous[axis])
                result.append(tuple(previous[i] + ratio * (current[i] - previous[i]) for i in (0, 1)))
            if current_inside:
                result.append(tuple(current))
            previous, previous_inside = current, current_inside
    return result if len(result) >= 3 else []


def ring_area(points):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1], strict=True)) / 2


def point_in_ring(point, ring):
    x, y = point
    contained = False
    for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            contained = not contained
    return contained


def _path(points, closed=False):
    return " ".join(f"{'M' if index == 0 else 'L'}{x:.2f} {y:.2f}" for index, (x, y) in enumerate(points)) + (
        " Z" if closed else ""
    )


def _label_box(x, y, label, size, anchor):
    # A deliberately generous glyph estimate prevents labels from touching.
    width = len(label) * size * 0.68 + 12
    left = x - width if anchor == "end" else x - width / 2 if anchor == "middle" else x
    return (left - 4, y - size - 5, left + width + 4, y + 7)


def _label(x, y, name, size, occupied, frame):
    for dx, dy, anchor in (
        (12, -10, "start"),
        (-12, -10, "end"),
        (12, size + 10, "start"),
        (-12, size + 10, "end"),
    ):
        tx, ty = x + dx, y + dy
        box = _label_box(tx, ty, name, size, anchor)
        if (
            inside(box[:2], frame)
            and inside(box[2:], frame)
            and not any(intersects(box, old) for old in occupied)
        ):
            occupied.append(box)
            return (
                f'<text x="{tx:.2f}" y="{ty:.2f}" font-family="sans-serif" font-size="{size}" '
                f'text-anchor="{anchor}" fill="#425e56">{escape(name)}</text>'
            )
    return ""


def backdrop(project, bounds, *, frame=MAP_FRAME):
    """Return passive SVG and qualified context for the supplied geographic frame.

    The projection accepts ``{"lon": longitude, "lat": latitude}``. A map outside
    this regional bundle is left blank rather than assigned invented geography.
    """
    limits = (bounds["left"], bounds["bottom"], bounds["right"], bounds["top"])
    if not all(
        isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n)
        for n in (*limits, *frame)
    ):
        raise ValueError("Map bounds must be finite numbers")
    if limits[0] >= limits[2] or limits[1] >= limits[3] or frame[0] >= frame[2] or frame[1] >= frame[3]:
        raise ValueError("Map bounds must have positive width and height")
    data = dataset()
    coverage = data["coverage"]
    credit = "Natural Earth · Census · OurAirports"
    if not intersects(limits, coverage):
        return {
            "svg": "",
            "label": "Outside regional map coverage",
            "caption": "Geographic context is unavailable for this area.",
            "credit": credit,
        }

    def projected(point):
        x, y = project({"lon": point[0], "lat": point[1]})
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Map projection produced a non-finite coordinate")
        return x, y

    shapes = []
    lake_labels = []
    for feature in data["lakes"]:
        if not intersects(feature["bounds"], limits):
            continue
        rings = [clip_ring([projected(point) for point in ring], frame) for ring in feature["rings"]]
        if not rings or not rings[0] or abs(ring_area(rings[0])) < 2:
            continue
        shapes.append(f'<path d="{" ".join(_path(ring, True) for ring in rings if ring)}" fill="#d5e8e9"/>')
        # Only real shorelines are stroked; clipping edges are not geography.
        for original in feature.get("shorelines", feature["rings"]):
            for piece in clip_lines([projected(point) for point in original], frame):
                shapes.append(f'<path d="{_path(piece)}" fill="none" stroke="#91b3b7" stroke-width="1.5"/>')
        outer = rings[0]
        box = (
            min(p[0] for p in outer),
            min(p[1] for p in outer),
            max(p[0] for p in outer),
            max(p[1] for p in outer),
        )
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        if (
            feature["name"]
            and abs(ring_area(outer)) > 10000
            and point_in_ring(center, outer)
            and not any(point_in_ring(center, hole) for hole in rings[1:] if hole)
        ):
            lake_labels.append((center, feature["name"]))
    for layer, color, width in (("boundaries", "#b5c2b1", 1.5), ("rivers", "#91b3b7", 2)):
        for feature in data[layer]:
            if intersects(feature["bounds"], limits):
                for line in feature["lines"]:
                    for piece in clip_lines([projected(point) for point in line], frame):
                        shapes.append(
                            f'<path d="{_path(piece)}" fill="none" stroke="{color}" stroke-width="{width}"/>'
                        )

    center = frame_center(bounds)
    places = [p for p in data["places"] if inside(p["point"], limits)]
    # Ordered by distance from the map's centre: a vicinity cue, never a receiver range or flight leg.
    places.sort(key=lambda p: (p["kind"] != "city", p["rank"], distance_km(center, p["point"]), p["name"]))
    # Reserve the north-arrow corner and a lower-left latitude annotation.
    occupied = [
        (frame[2] - 110, frame[1], frame[2], frame[1] + 115),
        (frame[0], frame[3] - 40, frame[0] + 150, frame[3]),
    ]
    labels, used = [], set()
    for place in places:
        if len(labels) >= MAX_LABELS:
            break
        name = place["name"] if place["kind"] == "city" else place["code"] + " airport"
        if name in used:
            continue
        x, y = projected(place["point"])
        marker_box = (x - 6, y - 6, x + 6, y + 6)
        if not (inside(marker_box[:2], frame) and inside(marker_box[2:], frame)) or any(
            intersects(marker_box, old) for old in occupied
        ):
            continue
        label = _label(x, y, name, 22, occupied, frame)
        if not label:
            continue
        marker = (
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="#536b61"/>'
            if place["kind"] == "city"
            else f'<rect x="{x - 4:.2f}" y="{y - 4:.2f}" width="8" height="8" fill="#fafbf5" stroke="#866347" stroke-width="2"/>'
        )
        labels.append(marker + label)
        occupied.append(marker_box)
        used.add(name)
    for point, name in lake_labels:
        if len(labels) >= MAX_LABELS:
            break
        if name in used:
            continue
        label = _label(*point, name, 26, occupied, frame)
        if label:
            labels.append(label)
            used.add(name)
    city = nearest_city(center, data)
    location = place_label(describe(city) if city else None)
    caption = "Place labels use public reference points. Natural Earth outlines are generalized; this is not a navigation map."
    if not (inside(limits[:2], coverage) and inside(limits[2:], coverage)):
        caption += " Geographic context is available for only part of this frame."
    return {"svg": "".join(shapes + labels), "label": location, "caption": caption, "credit": credit}
