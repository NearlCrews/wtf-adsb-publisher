#!/usr/bin/env python3
"""Create self-contained aircraft SVGs from a selected, validated archive review.

The public output contains rendered geometry and allowed event details. Source
paths, hashes and finding IDs stay in a separate private derivation audit.
"""

import base64
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from airport_patterns import classify as airport_pattern
from geographic_context import backdrop

from wtf_adsb_publisher import airport_context, editorial
from wtf_adsb_publisher.airport_context import MAX_GAP, ground_fix
from wtf_adsb_publisher.editorial import MILES_PER_NM
from wtf_adsb_publisher.places import nearest_place, projection
from wtf_adsb_publisher.public_events import _projection, epoch_utc
from wtf_adsb_publisher.rules import distance_nm, number
from wtf_adsb_publisher.stories import evidence_packet, participant_entries

WIDTH, HEIGHT = 1200, 800
MAX_POINTS = 256
# Decoded archive objects kept while one selection renders.
ARCHIVE_CACHE = 8
COLORS = {
    "ink": "#193e39",
    "muted": "#50665d",
    "paper": "#eceee4",
    "line": "#187268",
    # The other aircraft of a formation or an advisory, in maps and profiles.
    "other": "#af4735",
}
# Kinds whose map also draws each participant's saved track.
SECOND_TRACK_KINDS = frozenset({"formation_flight", "tcas_ra"})
# Kinds that add an altitude profile below the map; TCAS plots the other aircraft too.
PROFILE_KINDS = frozenset({"tcas_ra", "stratospheric_balloon", "thermal_soaring", "aerobatic_practice"})
PROFILE_HEIGHT = 400


def instant(value):
    return datetime.fromisoformat(value).timestamp()


def fmt(value):
    return f"{value:,.1f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}"


def text(x, y, value, size=36, *, color=None, anchor="start", weight=400):
    return (
        f'<text x="{x}" y="{y}" font-family="sans-serif" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}" fill="{color or COLORS["ink"]}">'
        + escape(str(value))
        + "</text>"
    )


def svg(title, caption, body, height=HEIGHT):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title description">'
        f'<title id="title">{escape(title)}</title><desc id="description">{escape(caption)}</desc>'
        f'<rect width="{WIDTH}" height="{height}" fill="{COLORS["paper"]}"/>' + body + "</svg>\n"
    )


def legend(x, y, entries, size=24):
    """Line swatches and labels, left to right from (x, y): [(label, color), ...]."""
    body = ""
    for label, color in entries:
        body += f'<path d="M{x} {y - size * 0.35:.1f}H{x + 40}" stroke="{color}" stroke-width="7" stroke-linecap="round"/>'
        body += text(x + 52, y, label, size, color=COLORS["muted"])
        x += 52 + round(len(label) * size * 0.58) + 36
    return body


def primary_label(event):
    """The subject aircraft's short name in a legend: registration, else callsign."""
    aircraft = event["aircraft"]
    return aircraft.get("registration") or aircraft.get("callsign") or "This aircraft"


def type_name(event):
    """The curated type name when the story context has one, else the database string."""
    context = event.get("context") or {}
    aircraft = event["aircraft"]
    curated = (context.get("type") or {}).get("name") if isinstance(context, dict) else None
    return curated or aircraft.get("type_description") or aircraft.get("type_code") or ""


def subject(event):
    """Type and identifier as readers search for them, e.g. "Airbus A380-800 HL7635"."""
    aircraft = event["aircraft"]
    identifier = aircraft.get("registration") or aircraft.get("callsign")
    if not identifier and aircraft.get("icao_address"):
        identifier = "ICAO address " + aircraft["icao_address"]
    return " ".join(part for part in (type_name(event), identifier) if part) or "the aircraft"


def possessive(name):
    return name + ("'" if name.endswith("s") else "'s")


def span(seconds):
    """The story text's wording for a duration (editorial.duration), so a map and its story agree."""
    return editorial.duration(seconds) or f"{round(seconds)} seconds"


def miles_text(nautical_miles):
    """The story text's wording for a distance (editorial.miles); shorter paths keep a tenth of a mile."""
    if text := editorial.miles(nautical_miles):
        return text
    miles = nautical_miles * MILES_PER_NM
    return "under 0.1 miles" if miles < 0.1 else f"about {fmt(round(miles, 1))} miles"


def observed_date(event):
    """The observation date as the stories write it, e.g. "2 Oct 2026"."""
    return editorial.short_date(event.get("observed_start")) or ""


def database_year(event):
    """The build year from the aircraft database, as the story facts carry it (age.built)."""
    facts = (event.get("story") or {}).get("facts") or {}
    value = facts.get("age.built") if isinstance(facts, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and 1900 <= value <= 2100:
        return value
    return None


def telemetry(raw):
    """Retain saved readings without inventing an altitude reference."""
    speed = number(raw.get("gs"))
    altitude, reference = None, None
    for key, label in (
        ("alt_baro", "barometric"),
        ("alt_geom", "geometric"),
        ("archive_alt", "unspecified"),
        ("alt", "unspecified"),
    ):
        altitude = number(raw.get(key))
        if altitude is not None:
            reference = label
            break
    return {
        "speed": speed if speed is not None and speed >= 0 else None,
        "altitude": altitude,
        "altitude_reference": reference,
    }


def segments(raw_points, start, end):
    """The drawn lines of the saved positions between ``start`` and ``end``.

    airport_context.parts splits them exactly as it splits a track for the
    runway classifier: invalid, stale, conflicting or ground fixes, conflicting
    same-time reports, source changes, implied speeds over 1,200 kn and new legs
    end a line. A trace gap flag is not a break by itself: the 45-second rule
    decides, as it does for the archive's movement analysis.
    """
    if len(raw_points) > MAX_POINTS:
        raise ValueError("Too many selected positions for one media asset")
    window = [
        raw for raw in raw_points if (stamp := number(raw.get("ts"))) is not None and start <= stamp <= end
    ]
    return airport_context.parts(window, describe=telemetry)


def participant_tracks(evidence, start, end, primary=None):
    """Each participant's drawn lines, from the saved ``track`` its evidence entry carries.

    The participants are the ones a story lists (stories.participant_entries: a known
    role, one entry per address, never the ``primary`` address). The positions pass
    the same checks as the primary's (segments): a participant without a saved track,
    or with too many positions, is not drawn.
    """
    tracks = []
    for item, _ in participant_entries(evidence, primary) if isinstance(evidence, dict) else ():
        points = item.get("track")
        if not isinstance(points, list) or not points or len(points) > MAX_POINTS:
            continue
        parts = segments([point for point in points if isinstance(point, dict)], start, end)
        if any(len(part) >= 2 for part in parts):
            tracks.append(parts)
    return tracks


def degree(value, latitude):
    value = value if latitude else (value + 180) % 360 - 180
    direction = ("N" if value >= 0 else "S") if latitude else ("E" if value >= 0 else "W")
    return f"{abs(value):.2f}° {direction}"


def replay_track(event, parts, project):
    """Use the image's exact accepted segments for timing and path length."""
    start = parts[0][0]["ts"]
    cumulative, samples = 0.0, []
    for part_index, part in enumerate(parts):
        for index, point in enumerate(part):
            if index:
                previous = part[index - 1]
                cumulative += distance_nm((previous["lat"], previous["lon"]), (point["lat"], point["lon"]))
            x, y = project(point)
            samples.append(
                {
                    "t": point["ts"] - start,
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "distance_nm": round(cumulative, 4),
                    "speed": point.get("speed"),
                    "altitude": point.get("altitude"),
                    "altitude_reference": point.get("altitude_reference"),
                    **({"break": True} if part_index and not index else {}),
                }
            )
    return {
        "version": 1,
        "kind": "geographic",
        "event_id": event["id"],
        "evidence_version": event["evidence_version"],
        "started_at": epoch_utc(start),
        "width": WIDTH,
        "height": HEIGHT,
        "points": samples,
    }


def scale_bar(bounds, max_width=250):
    """Nice statute-mile scale at the map's middle latitude, with exact inversion."""
    latitude = bounds.get("scale_latitude", (bounds["top"] + bounds["bottom"]) / 2)
    longitude = (bounds["left"] + bounds["right"]) / 2

    def miles(width):
        half_span = (bounds["right"] - bounds["left"]) * width / 1020 / 2
        return (
            distance_nm((latitude, longitude - half_span), (latitude, longitude + half_span)) * MILES_PER_NM
        )

    maximum = miles(max_width)
    if maximum < 0.001:
        return None
    magnitude = 10 ** math.floor(math.log10(maximum))
    value = max(step * magnitude for step in (1, 2, 5) if step * magnitude <= maximum)
    low, high = 0, max_width
    for _ in range(32):
        middle = (low + high) / 2
        if miles(middle) < value:
            low = middle
        else:
            high = middle
    return {"miles": value, "width": (low + high) / 2}


def map_background(basemaps, bounds):
    """(pixels, media type) for the frame, or None without a cached background.

    A cache without image(), such as a read-only wrapper, supplies PNG pixels.
    """
    if basemaps is None:
        return None
    image = getattr(basemaps, "image", None)
    if callable(image):
        return image(bounds)
    pixels = basemaps.get(bounds)
    return None if pixels is None else (pixels, "image/png")


def track_image(event, parts, *, analysis_window, basemaps=None, others=()):
    """The map of the recorded track; ``others`` (participant_tracks) are drawn in a second color."""
    points = [point for part in parts for point in part]
    if len(points) < 3 or not any(len(part) >= 2 for part in parts):
        return None
    others = [track for track in others if track] if event.get("kind") in SECOND_TRACK_KINDS else []
    # The frame fits every drawn aircraft; the replay and the path length stay the primary's.
    project, bounds = projection(points + [point for track in others for part in track for point in part])
    context = backdrop(project, bounds)
    background = map_background(basemaps, bounds)
    map_pixels = background[0] if background else None
    if map_pixels is not None:
        if context["label"] == "Outside regional map coverage":
            context["label"] = "Recorded aircraft area"
        context["svg"] = (
            f'<image x="90" y="165" width="1020" height="440" href="data:{background[1]};base64,'
            + base64.b64encode(map_pixels).decode("ascii")
            + '"/>'
        )
        context["caption"] = (
            "USGS topographic background shows roads, water and place names where available. "
            "It is a current reference map, not imagery from the flight date."
        )
        context["credit"] = "Basemap: USGS The National Map"
    replay = replay_track(event, parts, project)
    length = replay["points"][-1]["distance_nm"]
    seconds = points[-1]["ts"] - points[0]["ts"]
    duration = seconds / 60
    scope = "the selected analysis window" if analysis_window else "a short archived excerpt"
    # The same routine names the place in the story, so text and map agree.
    place = nearest_place(parts)
    near = " near " + place["name"] if place else ""
    title = (
        f"Map of {possessive(subject(event))} recorded track{near}: "
        f"{len(points)} positions over {span(seconds)}, {miles_text(length)}."
    )
    caption = (
        f"Saved positions of {subject(event)}{near}, drawn from {scope}. "
        "The line breaks at gaps over 45 seconds, at source changes and at rejected positions. "
        f"The path measures {miles_text(length)} ({fmt(length)} nautical miles) along the plotted segments. "
        "Archive timestamps do not establish exact message freshness. "
        + (
            f"The {'line' if len(others) == 1 else 'lines'} in the second color "
            f"{'shows' if len(others) == 1 else 'show'} the saved positions of the other aircraft "
            "over the same interval, split by the same rules. "
            if others
            else ""
        )
        + context["caption"]
    )
    body = text(70, 76, "Recorded track", 48, weight=600)
    body += text(1130, 76, event["aircraft"].get("registration", ""), 36, anchor="end")
    body += text(70, 118, f"{fmt(duration)} min · {len(points)} positions", 28, color=COLORS["muted"])
    body += text(1130, 118, context["label"], 28, anchor="end", color=COLORS["muted"])
    body += '<rect x="90" y="165" width="1020" height="440" rx="14" fill="#fafbf5"/>'
    body += context["svg"]
    for ratio in (0, 0.5, 1):
        x, y = 90 + ratio * 1020, 165 + ratio * 440
        if map_pixels is None:
            body += f'<path d="M{x} 165V605M90 {y}H1110" stroke="#c5d0c3" stroke-width="2"/>'
        lon = bounds["left"] + ratio * (bounds["right"] - bounds["left"])
        body += text(x, 647, degree(lon, False), 28, anchor="middle", color=COLORS["muted"])
    body += text(94, 143, degree(bounds["top"], True), 28, color=COLORS["muted"])
    body += text(94, 591, degree(bounds["bottom"], True), 28, color=COLORS["muted"])
    body += (
        '<path d="M1060 258V195M1047 217 1060 193 1073 217" fill="none" stroke="#193e39" stroke-width="4"/>'
    )
    body += text(1060, 180, "N", 30, anchor="middle", weight=600)
    # The other aircraft first, so the story's own aircraft is drawn on top.
    drawn = [(part, COLORS["other"]) for track in others for part in track]
    for part, color in [*drawn, *((part, COLORS["line"]) for part in parts)]:
        coordinates = [project(point) for point in part]
        if len(part) >= 2:
            path = " ".join(
                f"{'M' if i == 0 else 'L'}{x:.2f} {y:.2f}" for i, (x, y) in enumerate(coordinates)
            )
            if map_pixels is not None:
                body += f'<path d="{path}" fill="none" stroke="#ffffff" stroke-width="13" stroke-linejoin="round" stroke-linecap="round"/>'
            body += f'<path d="{path}" fill="none" stroke="{color}" stroke-width="7" stroke-linejoin="round" stroke-linecap="round"/>'
        dot = COLORS["ink"] if color == COLORS["line"] else color
        body += "".join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4.5" fill="{dot}"/>' for x, y in coordinates)
    for point, fill in ((points[0], "#fafbf5"), (points[-1], "#d47136")):
        x, y = project(point)
        body += f'<circle cx="{x:.2f}" cy="{y:.2f}" r="12" fill="{fill}" stroke="#193e39" stroke-width="4"/>'
    scale = scale_bar(bounds)
    if scale:
        end = 95 + scale["width"]
        body += f'<path d="M95 670V684M95 678H{end:.2f}M{end:.2f} 670V684" fill="none" stroke="#193e39" stroke-width="4"/>'
        body += text(95, 716, f"{scale['miles']:g} mi · map scale", 28, color=COLORS["muted"])
    body += text(1130, 716, f"Recorded path ≈ {fmt(length * MILES_PER_NM)} mi", 36, anchor="end", weight=600)
    body += text(1130, 766, "First ○    Last ●", 26, anchor="end", color=COLORS["muted"])
    body += text(70, 766, context["credit"], 22, color=COLORS["muted"])
    if others:
        body += legend(
            470, 766, [(primary_label(event), COLORS["line"]), ("Other aircraft", COLORS["other"])]
        )
    pattern = airport_pattern(parts, event, analysis_window=analysis_window)
    return {
        "kind": "track",
        "alt": title,
        "caption": caption,
        "svg": svg(title, caption, body),
        "replay": replay,
        "creditPath": "/about/#map-data",
        **({"airportPattern": pattern} if pattern else {}),
    }


def _barometric(parts):
    """A track's pressure-altitude readings as lines of (timestamp, ft), split where the track splits."""
    lines = []
    for part in parts:
        line = [
            (point["ts"], point["altitude"])
            for point in part
            if point.get("altitude_reference") == "barometric" and number(point.get("altitude")) is not None
        ]
        if line:
            lines.append(line)
    return lines


def _nice_step(span, ticks=4):
    """A 1, 2, or 5 times a power of ten step that splits ``span`` into about ``ticks`` parts,
    at least 100 ft, so a level profile is labeled in whole hundreds."""
    raw = max(span / ticks, 100)
    magnitude = 10 ** math.floor(math.log10(raw))
    return next(step * magnitude for step in (1, 2, 5, 10) if step * magnitude >= raw)


def profile_image(event, parts, others=()):
    """Pressure altitude against time for the episode: the aircraft, and for TCAS the other aircraft.

    Drawn from the map's own accepted segments (``parts``, and participant_tracks for
    ``others``), barometric readings only; 1200 by 400. None without two readings.
    """
    if event.get("kind") not in PROFILE_KINDS:
        return None
    series = [(primary_label(event), COLORS["line"], _barometric(parts))]
    if event["kind"] == "tcas_ra":
        series += [("Other aircraft", COLORS["other"], _barometric(track)) for track in others]
    series = [item for item in series if sum(len(line) for line in item[2]) >= 2]
    if not series or series[0][1] != COLORS["line"]:
        return None
    readings = [reading for _, _, lines in series for line in lines for reading in line]
    first, last = min(stamp for stamp, _ in readings), max(stamp for stamp, _ in readings)
    if first == last:
        return None
    low, high = min(value for _, value in readings), max(value for _, value in readings)
    step = _nice_step(high - low)
    floor, ceiling = math.floor(low / step) * step, math.ceil(high / step) * step
    if floor == ceiling:
        floor, ceiling = floor - step, ceiling + step
    left, right, top, bottom = 150, 1110, 100, 320

    def place(stamp, value):
        return (
            left + (stamp - first) / (last - first) * (right - left),
            bottom - (value - floor) / (ceiling - floor) * (bottom - top),
        )

    minutes = (last - first) / 60
    own = [value for line in series[0][2] for _, value in line]
    others_shown = len(series) > 1
    title = (
        f"Chart of {possessive(subject(event))} pressure altitude over {span(last - first)}, "
        f"from {fmt(min(own))} to {fmt(max(own))} ft"
        + (", beside the other aircraft in the advisory." if others_shown else ".")
    )
    caption = (
        f"Pressure altitude reported by {subject(event)}"
        + (" and the other aircraft in the advisory" if others_shown else "")
        + " against minutes from the first plotted report. Lines stop where the map's track breaks. "
        "The altitudes are reported pressure altitudes, not corrected for local air pressure, and "
        "archive timestamps do not establish exact message freshness."
    )
    body = text(70, 52, "Altitude profile", 34, weight=600)
    # One legend entry per label: every other aircraft shares the second color.
    body += legend(640, 52, list(dict.fromkeys((label, color) for label, color, _ in series)), size=22)
    body += text(70, 84, "Pressure altitude (ft)", 22, color=COLORS["muted"])
    body += (
        f'<rect x="{left}" y="{top}" width="{right - left}" height="{bottom - top}" rx="10" fill="#fafbf5"/>'
    )
    value = floor
    while value <= ceiling + step / 2:
        _, y = place(first, value)
        body += f'<path d="M{left} {y:.2f}H{right}" stroke="#c5d0c3" stroke-width="2"/>'
        body += text(left - 14, y + 8, fmt(value), 22, anchor="end", color=COLORS["muted"])
        value += step
    for ratio in (0, 0.5, 1):
        x = left + ratio * (right - left)
        body += text(x, 352, fmt(round(minutes * ratio, 1)), 22, anchor="middle", color=COLORS["muted"])
    body += text(
        (left + right) / 2, 386, "Minutes from the first report", 22, anchor="middle", color=COLORS["muted"]
    )
    for _, color, lines in series:
        for line in lines:
            coordinates = [place(stamp, value) for stamp, value in line]
            if len(coordinates) >= 2:
                path = " ".join(
                    f"{'M' if i == 0 else 'L'}{x:.2f} {y:.2f}" for i, (x, y) in enumerate(coordinates)
                )
                body += f'<path d="{path}" fill="none" stroke-linejoin="round" stroke="{color}" stroke-width="5"/>'
            body += "".join(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}"/>' for x, y in coordinates
            )
    return {
        "role": "profile",
        "alt": title,
        "caption": caption,
        "svg": svg(title, caption, body, PROFILE_HEIGHT),
        "width": WIDTH,
        "height": PROFILE_HEIGHT,
    }


def speed_image(event, finding):
    if event["kind"] != "outlier_gs":
        return None
    raw = finding["evidence"].get("context_samples", [])
    if not isinstance(raw, list) or len(raw) > MAX_POINTS:
        return None
    samples = {}
    for item in raw:
        stamp, value = number(item.get("timestamp")), number(item.get("gs"))
        if stamp is None:
            continue
        valid = value is not None and not item.get("conflicting_position") and not ground_fix(item)
        reading = (value, item.get("source", "unknown")) if valid else None
        if stamp in samples and samples[stamp] != reading:
            samples[stamp] = None
        else:
            samples[stamp] = reading
    valid_samples = sorted((stamp, reading[0]) for stamp, reading in samples.items() if reading is not None)
    peak = finding["evidence"].get("observed_at")
    if len(valid_samples) < 3 or not any(
        stamp == peak and value == finding["value"] for stamp, value in valid_samples
    ):
        return None
    first, last = valid_samples[0][0], valid_samples[-1][0]
    if first == last:
        return None
    maximum = max(100, math.ceil(max(value for _, value in valid_samples) / 200) * 200)
    minimum = min(0, math.floor(min(value for _, value in valid_samples) / 200) * 200)
    others = [value for stamp, value in valid_samples if stamp != peak]
    low, high = fmt(min(others)), fmt(max(others))
    rest = f"the rest {low} knots" if low == high else f"the rest {low} to {high} knots"
    title = (
        f"Chart of {len(valid_samples)} archived ground-speed reports for {subject(event)}; "
        f"one reads {fmt(finding['value'])} knots, {rest}."
    )
    caption = (
        f"Archived ground-speed reports for {subject(event)} around the selected value. "
        "Each dot is a saved report; lines stop at gaps over 45 seconds, source changes and rejected readings. "
        "Archive timestamps do not establish exact message freshness. "
        "The peak is an unconfirmed reported value and does not show how fast the aircraft flew."
    )
    body = text(70, 76, "Archived speed reports", 48, weight=600)
    body += text(1130, 76, event["aircraft"].get("registration", ""), 36, anchor="end")
    body += '<rect x="130" y="165" width="980" height="425" rx="14" fill="#fafbf5"/>'
    for value in (minimum, (minimum + maximum) / 2, maximum):
        y = 590 - (value - minimum) / (maximum - minimum) * 425
        body += f'<path d="M130 {y:.2f}H1110" stroke="#c5d0c3" stroke-width="2"/>'
        body += text(111, y + 10, fmt(value), 28, anchor="end", color=COLORS["muted"])
    body += text(70, 141, "kn", 30, color=COLORS["muted"])
    previous = None
    for stamp, reading in sorted(samples.items()):
        if reading is None:
            previous = None
            continue
        value, source = reading
        x, y = (
            130 + (stamp - first) / (last - first) * 980,
            590 - (value - minimum) / (maximum - minimum) * 425,
        )
        if previous is not None and stamp - previous[0] <= MAX_GAP and source == previous[3]:
            body += f'<path d="M{previous[1]:.2f} {previous[2]:.2f}L{x:.2f} {y:.2f}" stroke="#af4735" stroke-width="6" fill="none"/>'
        body += f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{12 if stamp == peak else 6}" fill="#af4735"/>'
        previous = stamp, x, y, source
    for stamp in (first, peak, last):
        x = 130 + (stamp - first) / (last - first) * 980
        label = datetime.fromtimestamp(stamp, timezone.utc).strftime("%H:%M:%S")
        body += text(x, 636, label, 28, anchor="middle", color=COLORS["muted"])
    body += text(70, 728, f"Selected report: {fmt(finding['value'])} kn", 44, weight=600)
    body += text(1130, 728, "Times in UTC", 30, anchor="end", color=COLORS["muted"])
    return {"kind": "measurement-chart", "alt": title, "caption": caption, "svg": svg(title, caption, body)}


def _wrapped(value, width):
    """Words of `value` in lines of at most `width` characters (a longer word keeps its own line)."""
    words, lines = value.split(), []
    while words:
        line = words.pop(0)
        while words and len(line) + len(words[0]) + 1 <= width:
            line += " " + words.pop(0)
        lines.append(line)
    return lines


def day_summary_image(event):
    """A subjectless story's card: its headline and date. The aircraft such a story names are
    examples, so the card shows no type, registration, or track."""
    headline = event["title"]
    title = headline if headline.endswith((".", "!", "?")) else headline + "."
    caption = "A summary of one UTC day of readings from many aircraft; no single aircraft is its subject."
    body = text(70, 86, "Day summary", 44, weight=600)
    body += '<path d="M70 138H1130M70 645H1130" stroke="#aebcaf" stroke-width="3"/>'
    for index, line in enumerate(_wrapped(headline, 28)[:4]):
        body += text(70, 260 + index * 84, line, 68, weight=600)
    body += text(70, 728, observed_date(event) or "Date not recorded", 44, weight=600)
    body += text(1130, 728, "Many aircraft", 30, anchor="end", color=COLORS["muted"])
    return {"kind": "observation-summary", "alt": title, "caption": caption, "svg": svg(title, caption, body)}


def summary_image(event):
    if event.get("subjectless"):
        return day_summary_image(event)
    aircraft = event["aircraft"]
    code = aircraft.get("type_code", "Aircraft record")
    description = aircraft.get("type_description", "Aircraft type was not recorded")
    caption = "Aircraft identity from the selected record. Type and registration are database matches; historical assignments may differ."
    year = database_year(event)
    details = [
        f"database year {year}" if year else "",
        "observed " + observed_date(event) if observed_date(event) else "",
    ]
    title = (
        subject(event) + (": " + ", ".join(part for part in details if part) if any(details) else "") + "."
    )
    body = text(70, 86, "Aircraft record", 44, weight=600)
    body += '<path d="M70 138H1130M70 645H1130" stroke="#aebcaf" stroke-width="3"/>'
    body += text(70, 332, code, 120, weight=600)
    for index, line in enumerate(_wrapped(description, 42)[:3]):
        body += text(74, 428 + index * 54, line, 38)
    body += text(70, 728, aircraft.get("registration", "Tail number not recorded"), 44, weight=600)
    body += text(1130, 728, "Metadata match", 30, anchor="end", color=COLORS["muted"])
    return {"kind": "observation-summary", "alt": title, "caption": caption, "svg": svg(title, caption, body)}


def bind_packet(finding, record, packet, event):
    """The selected episode and publication evidence must describe this finding."""
    source = {
        **finding,
        "evidence": {**finding["evidence"], "watchlist": finding["evidence"].get("watchlist") or {}},
    }
    interval = tuple(instant(record[key]) for key in ("episode_start", "episode_end"))
    if "qualifying_count_start_utc" in packet.get("observation", {}):
        rebuilt = evidence_packet(source, include_track=False, observation_interval=interval)
    else:
        # Retained previews predate explicit per-finding count intervals.
        selected = {**source, "first_seen": interval[0], "last_seen": interval[1]}
        rebuilt = evidence_packet(selected, include_track=False)
    # The review selection qualifies the hit count for a grouped episode. It does
    # not alter the selected finding's measurements or selected analysis window.
    original_meaning = rebuilt["observation"]["count_meaning"]
    selected_meaning = packet.get("observation", {}).get("count_meaning")
    if (
        selected_meaning
        == original_meaning + "; selected finding only, not every finding in the grouped episode"
    ):
        rebuilt["observation"]["count_meaning"] = selected_meaning
    if rebuilt != packet:
        raise ValueError("Selected packet differs from its source finding or episode interval")
    # The snapshot already validates research. Binding its media needs only the
    # observation projection, not the private weekly source omitted from exports.
    editorial = {key: event[key] for key in ("title", "summary", "article_path")}
    if "priority_evidence" not in event:
        # An event the publisher treats as ordinary carries no exception proof.
        editorial["priority"] = "ordinary"
    if _projection(packet, editorial)["evidence_version"] != event["evidence_version"]:
        raise ValueError("Selected packet differs from the public event evidence")


def media_entry(image, event):
    """An image's manifest entry and its content-addressed files.

    The SVG and the replay are named by their SHA-256, and the replay names its
    SVG. Returns (entry, [(file name, bytes), ...]); the image is not changed.
    A figure such as the profile carries its own width, height, and role.
    """
    image = dict(image)
    width, height = image.pop("width", WIDTH), image.pop("height", HEIGHT)
    raw = image.pop("svg").encode()
    name = hashlib.sha256(raw).hexdigest() + ".svg"
    files = [(name, raw)]
    replay = image.pop("replay", None)
    if replay:
        content = (
            json.dumps(
                {**replay, "image": "/event-media/" + name},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        replay_name = hashlib.sha256(content).hexdigest() + ".json"
        files.append((replay_name, content))
        image["replaySrc"] = "/event-media/" + replay_name
    entry = {
        **image,
        "evidence_version": event["evidence_version"],
        "src": "/event-media/" + name,
        "width": width,
        "height": height,
    }
    return entry, files


def write_media(assets, files):
    """Write content-addressed files; a file already present must hold the same bytes."""
    assets.mkdir(parents=True, exist_ok=True)
    for filename, raw in files:
        path = assets / filename
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("Content-addressed media file changed")
        else:
            path.write_bytes(raw)


def store_image(image, assets, event):
    """Write one image's files under ``assets`` and return its manifest entry."""
    entry, files = media_entry(image, event)
    write_media(Path(assets), files)
    return entry
