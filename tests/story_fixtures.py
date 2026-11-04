"""Synthetic story fixtures: collector-shaped events, their packets, projections and facts.

Every identity, value and time here is invented or a public type designation. Track
positions sit in the South Pacific, far from any receiver, and no event carries
receiver-relative fields unless a test adds them on purpose as canaries.
"""

import contextlib
import functools
import math
import sys
import zlib
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from wtf_adsb_publisher import entity_rules, public_events, reference, rules, stories, story_facts, wind

LAT, LON = -45.0, -150.0
EDITORIAL = {
    "title": "Fixture",
    "summary": "Fixture summary.",
    "article_path": "/stories/cessna-172-n12345-fixture-1-jun-2026/",
}
PLACES = {
    "coleman": {"name": "Coleman, Michigan", "short": "Coleman, MI", "state": "Michigan"},
    "battle-creek": {"name": "Battle Creek, Michigan", "short": "Battle Creek, MI", "state": "Michigan"},
    "marion": {"name": "Marion, Indiana", "short": "Marion, IN", "state": "Indiana"},
    "camden": {"name": "Camden, Michigan", "short": "Camden, MI", "state": "Michigan"},
    "lansing": {"name": "Lansing, Michigan", "short": "Lansing, MI", "state": "Michigan"},
    "toledo": {"name": "Toledo, Ohio", "short": "Toledo, OH", "state": "Ohio"},
}


def stamp(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def track(start, count, *, step=15, gs=480.0, alt=39000, source="adsb_icao", heading=90.0, turn=0.0):
    """Positions moving at the given ground speed; an optional steady turn in degrees per step."""
    points, lat, lon = [], LAT, LON
    for index in range(count):
        points.append(
            {"ts": start + index * step, "lat": round(lat, 5), "lon": round(lon, 5), "gs": gs, "alt": alt}
            | {"source": source}
        )
        distance = gs * step / 3600 / 60
        lat += distance * math.cos(math.radians(heading))
        lon += distance * math.sin(math.radians(heading)) / math.cos(math.radians(lat))
        heading = (heading + turn) % 360
    return points


def event(
    kind,
    *,
    hexcode="a1b2c3",
    registration=None,
    callsign=None,
    typecode=None,
    desc=None,
    year=None,
    start="2026-09-27T00:05:15Z",
    end=None,
    value=None,
    unit="",
    source="adsb_icao",
    readings=None,
    samples=None,
    context=None,
    maneuver=None,
    pattern=None,
    hits=3,
    **evidence,
):
    first = stamp(start)
    last = stamp(end) if end else first + 600
    aircraft = {"hex": hexcode, "type": source}
    for key, item in (
        ("r", registration),
        ("flight", callsign),
        ("t", typecode),
        ("desc", desc),
        ("year", year),
    ):
        if item is not None:
            aircraft[key] = item
    aircraft.update(readings or {})
    payload = {"aircraft": aircraft, "observed_at": first, "watchlist": {}}
    if samples is not None:
        payload["pattern_samples"] = samples
    if context is not None:
        payload["context_samples"] = context
    if maneuver is not None:
        payload["maneuver"] = {"confirmed": True, "quality_review_required": False, **maneuver}
    if pattern is not None:
        payload["pattern"] = {"observed_sources": [source], "quality_review_required": False, **pattern}
    payload.update(evidence)
    return {
        "id": 1,
        "hex": hexcode,
        "kind": kind,
        "category": "airframes",
        "title": "PRIVATE TITLE",
        "value": value,
        "unit": unit,
        "first_seen": first,
        "last_seen": last,
        "hits": hits,
        "origin": "Live receiver",
        "evidence": payload,
    }


def packet(raw):
    return stories.evidence_packet(raw)


def projection(evidence):
    return public_events._projection(evidence, EDITORIAL)


def facts(raw, *, place=None, airport=None, profiles=None, entities=None):
    evidence = packet(raw)
    return story_facts.build(
        evidence, projection(evidence), place=place, airport=airport, profiles=profiles, entities=entities
    )


# A type's usual ground speed in these records, as selection.type_norm gives it.
NORM = {
    "low": 380,
    "high": 430,
    "phase": "level",
    "band_floor_ft": 30000,
    "band_ceiling_ft": 40000,
    "addresses": 61,
    "days": 22,
    "window_days": 30,
}
# The nearest public airport to a track, as airports.nearest gives it.
BRIGHTON = {"name": "Brighton Airport", "ident": "BTN", "nm": 1.7, "at": 3}


def norm_facts(kind, block=None, *, place=None):
    """Story facts for a kind's fixture event whose packet carries a type norm (NORM by default)."""
    evidence = packet(kind_event(kind))
    evidence["type_norm"] = NORM if block is None else block
    return story_facts.build(evidence, projection(evidence), place=place)


def with_registry(raw, state, code="B752", source="adsbdb"):
    """A fixture event with a second registry's answer for its listed type, as typecheck writes it."""
    raw["evidence"]["type_check"] = {
        "state": state,
        "source": source,
        "type_code": code,
        "listed_type": raw["evidence"]["aircraft"]["t"],
        "checked_at": 1,
    }
    return raw


def unnamed(kind, code="A129", desc=None):
    """A kind's fixture event whose aircraft has a designator the type table does not name."""
    raw = kind_event(kind)
    raw["evidence"]["aircraft"].update(t=code, desc=desc)
    return raw


def unnamed_jet(kind="type_speed_mismatch"):
    """A mismatch story's facts for a twin jet the type table does not name (CRJX), with certified
    limits."""
    table = {
        "CRJX": {"max_operating_altitude_ft": 41000, "vmo_kias": 335, "mmo": 0.85, "source": RATED_SOURCE}
    }
    with rated_limits(table):
        return facts(unnamed(kind, "CRJX"), place=PLACES["coleman"])


def watch_entity(description="aircraft leasing company in Ohio", **changes):
    """A cached Wikidata answer (entity_context) for the watchlist fixture's entry link."""
    raw = kind_event("watchlist_match")
    entity = {
        "state": "available",
        "label": WATCH_ASSOCIATION,
        "description": description,
        "item": "Q4242",
        "revision": 7,
        "association": WATCH_ASSOCIATION,
        "retrieved_at": 1,
    }
    entity.update(changes)
    association = entity["association"] if isinstance(entity["association"], str) else ""
    return {entity_rules.cache_key(raw["evidence"]["watchlist"]["reference_url"], association): entity}


def profile(code, *texts, publisher="Example Aerospace", title="Type overview", description=None):
    """A type profile as weekly_research.story_profiles() gives it, keyed by curated type slug."""
    return {
        story_facts.type_entry(code, description)["slug"]: {
            "facts": [
                {
                    "id": f"tp.{code}.{index}",
                    "text": text,
                    "source": {
                        "publisher": publisher,
                        "title": title,
                        "url": f"https://www.example.com/types/{code.lower()}",
                    },
                }
                for index, text in enumerate(texts, start=1)
            ],
            "retrieved_at": "2026-10-01T00:00:00Z",
        }
    }


A380_PROFILE = profile(
    "A388",
    "Airbus built the A380 with two full-length passenger decks.",
    "The A380 first flew in April 2005 and entered airline service in 2007.",
    publisher="Airbus",
    title="A380 overview",
)
C5M_PROFILE = profile(
    "C5M",
    "The C-5M cargo hold opens at both the nose and the tail, with drive-on ramps at each end.",
    publisher="US Air Force",
    title="C-5 A/B/C Galaxy and C-5M Super Galaxy fact sheet",
)
E6_PROFILE = profile(
    "E6",
    "Naval Air Systems Command describes the E-6B as a relay for strategic communications and an airborne "
    "command post.",
    "Boeing built the E-6 on the 707-based line it also used for the E-3 Sentry.",
    publisher="Naval Air Systems Command",
    title="E-6B Mercury",
)


def watch_a380(
    registration="HL7635",
    callsign="AAR224",
    hexcode="71be35",
    start="2026-09-27T00:05:15Z",
    alt=39000,
    gs=487.0,
    positions=9,
):
    first = stamp(start)
    return event(
        "watch_type",
        hexcode=hexcode,
        registration=registration,
        callsign=callsign,
        typecode="A388",
        desc="AIRBUS A-380-800",
        start=start,
        end=start[:11] + "00:22:15Z" if start.endswith("00:05:15Z") else None,
        readings={"archive_alt": alt, "gs": gs},
        samples=track(first, positions, gs=gs, alt=alt),
        historical_corpus=True,
    )


def outlier_cessna():
    first = stamp("2026-09-18T16:14:00Z")
    context = [
        {"ts": first - 45, "gs": 95.8, "archive_alt": 4150},
        {"ts": first - 30, "gs": 104.2, "archive_alt": 4175},
        {"ts": first - 15, "gs": 110.8, "archive_alt": 4200},
        {"ts": first, "gs": 1063.9, "archive_alt": 4200},
        {"ts": first + 15, "gs": 118.1, "archive_alt": 4200},
        {"ts": first + 30, "gs": 123.0, "archive_alt": 4225},
    ]
    return event(
        "outlier_gs",
        hexcode="a89034",
        registration="N6508K",
        typecode="C172",
        desc="CESSNA 172 Skyhawk",
        year="1980",
        start="2026-09-18T16:13:15Z",
        end="2026-09-18T16:14:30Z",
        value=1063.9,
        unit="kn",
        readings={"gs": 1063.9, "archive_alt": 4200},
        context=context,
        historical_corpus=True,
    ) | {"first_seen": first - 45, "last_seen": first + 30}


# The invented Plane Alert DB association of the watchlist fixture; stories name it.
WATCH_ASSOCIATION = "Example Aviation Holdings"
# Cited rated limits in the shape reference.limits() returns; the figures are invented.
RATED_SOURCE = {
    "publisher": "Example Aviation Authority",
    "title": "Example type certificate data sheet",
    "url": "https://www.example.com/tcds",
}
RATED_LIMITS = {
    "C172": {"max_operating_altitude_ft": 14000, "vne_kias": 163, "source": RATED_SOURCE},
    "A388": {"max_operating_altitude_ft": 43000, "vmo_kias": 340, "mmo": 0.89, "source": RATED_SOURCE},
    "B77W": {"max_operating_altitude_ft": 43100, "vmo_kias": 330, "mmo": 0.89, "source": RATED_SOURCE},
}


@contextlib.contextmanager
def rated_limits(table=None):
    """reference.limits() answering from a fixture table, whether or not the dataset has landed."""
    table = RATED_LIMITS if table is None else table
    with mock.patch.object(reference, "limits", lambda code: deepcopy(table.get(code)), create=True):
        yield


def all_kind_events():
    """One plausible event for every kind with a public display policy."""
    base = stamp("2026-09-20T14:00:00Z")
    events = {}
    for kind in public_events.KIND_TYPES:
        events[kind] = kind_event(kind, base)
    return events


def kind_event(kind, base=None):
    """A plausible event for the kind; the observation lasts at least as long as its episode."""
    raw = _kind_event(kind, base)
    episode = raw["evidence"].get("maneuver") or raw["evidence"].get("pattern") or {}
    ended = episode.get("ended_at")
    if isinstance(ended, (int, float)) and ended > raw["last_seen"]:
        raw["last_seen"] = ended
    return raw


def _kind_event(kind, base=None):
    serial = zlib.crc32(kind.encode()) % 8900 + 100
    # Each kind gets its own invented aircraft, time and readings, as each real story does.
    base = base if base is not None else stamp("2026-09-20T14:00:00Z") + (serial % 600) * 61
    start = datetime.fromtimestamp(base, timezone.utc).isoformat().replace("+00:00", "Z")
    tail = "TX" if serial % 2 else "QK"
    common = {
        "start": start,
        "hexcode": f"a{serial:04x}0"[:6],
        "registration": f"N{serial}{tail}",
        "callsign": f"N{serial}{tail}",
    }
    jet = {
        "typecode": "C25C",
        "desc": "CESSNA 525C Citation CJ4",
        "year": str(2010 + serial % 15),
        "readings": {"alt_baro": 37000 + serial % 40 * 100, "gs": 400.0 + serial % 90},
    }
    heli = {
        "typecode": "B407",
        "desc": "BELL 407",
        "year": str(1996 + serial % 25),
        "readings": {"alt_baro": 1200 + serial % 30 * 25, "gs": 0.0},
    }
    window = {"started_at": base, "ended_at": base + 720}
    if kind in {"watch_type", "military", "database_watch", "rare_here"}:
        alt, gs = 26000 + serial % 30 * 200, 400.0 + serial % 80
        military = {
            "registration": f"0{serial % 9 + 1}-{serial:04d}",
            "callsign": f"RCH{serial % 900 + 100}",
            "hexcode": f"ae{serial:04x}",
        }
        flags = {"dbFlags": 1} if kind in {"military", "database_watch"} else {}
        return event(
            kind,
            **{**common, **military},
            typecode="C17",
            desc="Boeing C-17A Globemaster III",
            readings={"alt_baro": alt, "gs": gs, **flags},
            samples=track(base, 10 + serial % 6, gs=gs, alt=alt),
        )
    if kind == "notable_variant":
        return event(
            kind,
            **{**common, "registration": "163920", "callsign": None, "hexcode": "ae0412"},
            typecode="E6",
            desc="Boeing E-6B Mercury",
            source="mlat",
            readings={"archive_alt": 11875},
            samples=track(base, 6, gs=320.0, alt=11875, source="mlat"),
            historical_corpus=True,
        )
    if kind == "vintage":
        return event(
            kind,
            **{**common, "registration": "N8704", "callsign": None, "hexcode": "abf93a"},
            typecode="DC3",
            desc="DOUGLAS DC-3 Dakota",
            year="1944",
            readings={"archive_alt": 3975, "gs": 142.9},
            samples=track(base, 5, gs=142.9, alt=3975),
            historical_corpus=True,
        )
    if kind == "watchlist_match":
        commit = "c" * 40
        return event(
            kind,
            **{**common, "registration": "N51EM", "callsign": "N51EM", "hexcode": "a65f86"},
            typecode="C25C",
            desc="CESSNA 525C Citation CJ4",
            readings={"alt_geom": 46050, "gs": 509.0, "geom_rate": 0},
            samples=track(base, 30, gs=509.0, alt=46050),
            watchlist={
                "hex": "a65f86",
                "public_claim_allowed": True,
                "source_registration": "N51EM",
                "association": WATCH_ASSOCIATION,
                "source_category": "Distinctive",
                "neutral_category": "Distinctive aircraft",
                "group": "Civ",
                "tags": ["Business Jet", "Corporate"],
                "aircraft_description": "Citation CJ4",
                "reference_url": "https://www.example.com/aircraft/n51em",
                "source_row": 1234,
                "source": {
                    "url": "https://raw.githubusercontent.com/sdr-enthusiasts/plane-alert-db/"
                    + commit
                    + "/plane-alert-db.csv",
                    "commit": commit,
                    "source_date": "2026-09-01T00:00:00Z",
                    "fetched_at": "2026-09-02T00:00:00Z",
                },
            },
        )
    if kind == "stratospheric_balloon":
        return event(
            kind,
            **{**common, "registration": "N930TH", "callsign": "HBAL837", "hexcode": "ace75a"},
            end=datetime.fromtimestamp(base + 33840, timezone.utc).isoformat().replace("+00:00", "Z"),
            value=66000,
            unit="ft",
            readings={"alt_baro": 66000, "gs": 18.0},
            samples=track(base, 40, step=840, gs=18.0, alt=66000),
            maneuver={
                **window,
                "ended_at": base + 33840,
                "duration_seconds": 33840,
                "max_altitude_ft": 68650,
                "median_ground_speed_kt": 18.5,
                "sample_count": 560,
            },
        )
    if kind in {"first_type_here", "first_country_here"}:
        alt, gs = 22000 + serial % 20 * 200, 380.0 + serial % 60
        portugal = {
            "registration": f"{26900 + serial % 99}",
            "callsign": None,
            "hexcode": f"497{serial:03x}"[:6],
        }
        return event(
            kind,
            **{**common, **portugal},
            typecode="E390",
            desc="EMBRAER EMB-390",
            readings={"alt_baro": alt, "gs": gs},
            samples=track(base, 12 + serial % 7, step=12 + serial % 7, gs=gs, alt=alt),
            first_sighting={"scope": "type", "coverage_days": 91, "since": "2026-06-01"},
        )
    if kind == "tcas_ra":
        return event(
            kind,
            **common,
            **{**jet, "readings": {"alt_baro": 17000, "gs": 300.0 + serial % 30}},
            samples=track(base, 10, gs=300.0 + serial % 30, alt=17000),
            maneuver={
                **window,
                "ended_at": base + 40,
                "duration_seconds": 40,
                "advisory": "Level Off",
                "response_rate_change_fpm": -1300,
                "min_separation_nm": 3.2,
                "vertical_separation_ft": 1100,
                "participant_count": 2,
                "distinct_report_count": 6,
            },
            participants=[
                {
                    "hex": "a0c0d0",
                    "role": "threat",
                    "registration": "N774AX",
                    "type": "B763",
                    "desc": "BOEING 767-300",
                    "callsign": "GTI3311",
                    "source": "adsb_icao",
                    "measurements": {"min_separation_nm": 3.2, "vertical_separation_ft": 1100},
                }
            ],
        )
    if kind == "formation_flight":
        return event(
            kind,
            **{**common, "registration": "05-5140", "callsign": "RCH871", "hexcode": "ae1234"},
            typecode="C17",
            desc="Boeing C-17A Globemaster III",
            readings={"alt_baro": 24000, "gs": 330.0},
            samples=track(base, 16, gs=330.0, alt=24000),
            maneuver={
                **window,
                "ended_at": base + 540,
                "duration_seconds": 540,
                "min_separation_nm": 0.4,
                "vertical_separation_ft": 200,
                "participant_count": 2,
            },
            participants=[
                {
                    "hex": "ae5678",
                    "role": "wingman",
                    "registration": "06-6159",
                    "type": "C17",
                    "desc": "Boeing C-17A Globemaster III",
                    "callsign": "RCH872",
                    "source": "adsb_icao",
                    "measurements": {"min_separation_nm": 0.4, "vertical_separation_ft": 200},
                }
            ],
        )
    if kind in {"holding_stack", "gnss_integrity_cluster"}:
        others = [
            {
                "hex": f"a0{index}a{index}b",
                "role": "member",
                "registration": f"N{index}05QX",
                "type": "B738",
                "desc": "BOEING 737-800",
                "source": "adsb_icao",
                "measurements": {},
            }
            for index in range(1, 4)
        ]
        metrics = {"participant_count": 4}
        if kind == "gnss_integrity_cluster":
            metrics["aircraft_count"] = 4
        return event(
            kind,
            **common,
            typecode="B738",
            desc="BOEING 737-800",
            readings={"alt_baro": 9000, "gs": 230.0},
            samples=track(base, 20, gs=230.0, alt=9000, turn=12),
            maneuver={**window, "duration_seconds": 720, **metrics},
            participants=others,
        )
    if kind.startswith(("special_squawk_", "archive_squawk_")):
        code = kind.rsplit("_", 1)[1]
        code = {"general": "7700", "nordo": "7600", "unlawful": "7500"}.get(code, code)
        squawk = code if code.isdigit() else "1200"
        archived = kind.startswith("archive_")
        status = {} if code.isdigit() else {"emergency": code}
        sequence = (
            {"code_sequence": [{"code": "7700", "seconds": 95}, {"code": "7600", "seconds": 60}]}
            if kind == "special_squawk_7600"
            else {}
        )
        return event(
            kind,
            **{**common, "callsign": f"SKW{serial % 9000 + 100}"},
            typecode="CRJ7",
            desc="BOMBARDIER Regional Jet CRJ-700",
            readings={
                "alt_baro" if not archived else "archive_alt": 9000 + serial % 40 * 100,
                "gs": 250.0 + serial % 60,
                "squawk": squawk,
                **status,
            },
            samples=track(base, 10 + serial % 6, gs=250.0 + serial % 60, alt=9000 + serial % 40 * 100),
            maneuver=None
            if archived
            else {**window, "duration_seconds": 420, "code": squawk, **sequence}
            if code.isdigit()
            else {**window, "duration_seconds": 420},
            **({"historical_corpus": True} if archived else {}),
        )
    if kind == "special_use_code":
        return event(
            kind,
            **common,
            **heli,
            samples=track(base, 12, gs=70.0 + serial % 40, alt=heli["readings"]["alt_baro"]),
            maneuver={**window, "duration_seconds": 720, "code": "1255"},
        )
    if kind == "fast":
        gs = 640.0 + serial % 30
        return event(
            kind,
            **{**common, "registration": f"N7{serial % 90 + 10}AN", "callsign": f"AAL{serial % 900 + 100}"},
            typecode="B77W",
            desc="BOEING 777-300ER",
            year=str(2012 + serial % 10),
            value=gs,
            unit="kn",
            readings={"alt_baro": 37000, "gs": gs},
            samples=track(base, 18, gs=gs, alt=37000),
        )
    if kind in {"outlier_gs", "type_speed_mismatch"}:
        sample = outlier_cessna()
        sample["kind"] = kind
        if kind != "outlier_gs":
            sample["hex"] = common["hexcode"]
            sample["evidence"]["aircraft"].update(hex=common["hexcode"], r=common["registration"])
        return sample
    if kind == "high":
        gs = 450.0 + serial % 40
        return event(
            kind,
            **common,
            typecode="GLF6",
            desc="GULFSTREAM G650",
            year=str(2012 + serial % 13),
            value=51000,
            unit="ft",
            readings={"alt_baro": 51000, "gs": gs},
            samples=track(base, 14, gs=gs, alt=51000),
        )
    if kind in {"outlier_alt_baro", "outlier_archive_alt", "type_altitude_mismatch"}:
        field = "archive_alt" if kind == "outlier_archive_alt" else "alt_baro"
        level, spike, step, gs = (
            3000 + serial % 30 * 100,
            50000 + serial % 10 * 500,
            10 + serial % 6,
            100.0 + serial % 40,
        )
        context = [
            {"ts": base - 2 * step + step * index, field: altitude, "gs": gs}
            for index, altitude in enumerate((level, level + 25, spike, level + 50, level + 50))
        ]
        return event(
            kind,
            **{**common, "callsign": None},
            typecode="C172",
            desc="CESSNA 172 Skyhawk",
            value=spike,
            unit="ft",
            readings={field: spike, "gs": gs},
            context=context,
        ) | {"first_seen": base - 2 * step, "last_seen": base + 2 * step}
    if kind == "outlier_baro_rate":
        altitude = 5000 + serial % 30 * 100
        context = [
            {"ts": base - 30 + 15 * index, "baro_rate": rate, "alt_baro": altitude}
            for index, rate in enumerate((-300, -250, -9800, -200, -150))
        ]
        return event(
            kind,
            **{**common, "callsign": None},
            typecode="C172",
            desc="CESSNA 172 Skyhawk",
            year=str(1965 + serial % 50),
            value=-9800,
            unit="ft/min",
            readings={"baro_rate": -9800, "alt_baro": altitude},
            context=context,
        ) | {"first_seen": base - 30, "last_seen": base + 30}
    if kind in {"rapid_descent", "rapid_climb", "vertical"}:
        sign = 1 if kind == "rapid_climb" else -1
        gs = 360.0 + serial % 50
        return event(
            kind,
            **common,
            **{
                **jet,
                "readings": {"alt_baro": 12000 if sign > 0 else 36000, "gs": gs, "baro_rate": sign * 7600},
            },
            value=sign * 7600,
            unit="ft/min",
            samples=track(base, 10, gs=gs, alt=25000),
            maneuver={
                **window,
                "ended_at": base + 150,
                "peak_at": base + 60,
                "duration_seconds": 150,
                "peak_rate_fpm": sign * 7600,
                "altitude_change_ft": sign * 14200,
                "start_altitude_ft": 36000 if sign < 0 else 12000,
                "end_altitude_ft": 21800 if sign < 0 else 26200,
                "threshold_fpm": 4500,
                "sustained_derived_rate_fpm": sign * 6200,
            },
        )
    if kind in {"above_rated_altitude", "above_rated_speed"}:
        return rated_event(kind, base, common)
    if kind == "seasonal_speed_record":
        return event(
            kind,
            **{**common, "registration": "HL7634", "callsign": "KAL82", "hexcode": "71be34"},
            typecode="A388",
            desc="AIRBUS A-380-800",
            value=651.0,
            unit="kn",
            readings={"alt_baro": 37000, "gs": 651.0},
            samples=track(base, 20, gs=651.0, alt=37000),
            season_record={
                "previous_max_gs": 622.0,
                "season_start": "2026-09-01",
                "baseline_start": "2026-09-01",
            },
        )
    if kind in {"wind_boost", "altitude_gap", "selected_altitude_overshoot"}:
        alt, gs = 33000 + serial % 30 * 100, 500.0 + serial % 40
        readings = {
            "alt_baro": alt,
            "alt_geom": alt + 1800,
            "gs": gs,
            "tas": gs - 80,
            "nav_altitude_mcp": alt - 1000,
        }
        units = {"wind_boost": ("kn difference", 80.0), "altitude_gap": ("ft", 1800.0)}
        unit, value = units.get(kind, ("ft", 1000.0))
        return event(kind, **common, **{**jet, "readings": readings}, value=value, unit=unit)
    if kind == "position_jump":
        moved = {**jet["readings"], "lat": LAT + 0.1, "lon": LON, "seen_pos": 0}
        return event(
            kind,
            **common,
            **{**jet, "readings": moved},
            value=2400.0,
            unit="kn",
            previous_position={"lat": LAT, "lon": LON, "seen_pos": 1, "type": "adsb_icao"},
            previous_snapshot_at=base - 10,
        )
    if kind in {"identity_conflict", "emitter_type_mismatch", "unusual_emitter", "space_category"}:
        # An identity conflict on every check: address, callsign and nationality all disagree.
        conflicted = kind == "identity_conflict"
        return event(
            kind,
            **{**common, "callsign": "N4417QK" if conflicted else common["callsign"]},
            typecode="C172",
            desc="CESSNA 172 Skyhawk",
            year=str(1965 + serial % 50),
            readings={"alt_baro": 4500 + serial % 30 * 100, "gs": 100.0 + serial % 30, "category": "A5"},
            identity_checks={
                "conflict": conflicted,
                "registration_matches_address": False,
                "us_registration_from_address": "N88231",
                **(
                    {
                        "callsign_registration": "N4417QK",
                        "callsign_matches_registration": False,
                        "registration_country": "US",
                        "allocation_country": "CA",
                        "allocation_matches_registration": False,
                    }
                    if conflicted
                    else {}
                ),
            },
        )
    if kind in {"gnss_integrity_loss", "correlated_data_anomaly"}:
        return event(
            kind,
            **common,
            **jet,
            samples=track(base, 16 + serial % 9, gs=jet["readings"]["gs"], alt=jet["readings"]["alt_baro"]),
            maneuver={**window, "duration_seconds": 300},
        )
    if kind in {"hovering", "thermal_soaring"}:
        if kind == "thermal_soaring":
            return event(
                kind,
                **{**common, "callsign": None},
                typecode="GLID",
                desc="STANDARD CIRRUS",
                readings={"alt_baro": 5200, "gs": 52.0},
                samples=track(base, 30, gs=52.0, alt=5200, turn=40),
                maneuver={**window, "duration_seconds": 720, "climb_gain_ft": 1800, "closed_loops": 7},
            )
        return event(
            kind,
            **{**common, "callsign": None},
            **heli,
            samples=track(base, 20, gs=2.0 + serial % 3, alt=heli["readings"]["alt_baro"]),
            maneuver={**window, "duration_seconds": 420},
        )
    if kind == "survey_grid":
        return event(
            kind,
            **{**common, "callsign": None},
            typecode="C208",
            desc="CESSNA 208 Caravan",
            readings={"alt_baro": 8500, "gs": 140.0},
            samples=track(base, 40, gs=140.0, alt=8500),
            maneuver={
                **window,
                "ended_at": base + 5400,
                "duration_seconds": 5400,
                "leg_count": 9,
                "leg_spacing_nm": 0.8,
                "leg_length_nm": 11.5,
            },
        )
    if kind == "winds_aloft_day":
        # The fastest eastbound aircraft is the finding's own; the next ones are participants.
        others = [
            {
                "hex": f"a0{index}c{index}d",
                "role": "member",
                "registration": f"N{index}12QK",
                "type": "B738",
                "desc": "BOEING 737-800",
                "source": "adsb_icao",
                "measurements": {"median_gs_kn": 580.0 - index * 5},
            }
            for index in (1, 2)
        ]
        return event(
            kind,
            **{**common, "callsign": None},
            typecode="A21N",
            desc="AIRBUS A-321neo",
            readings={"alt_baro": 37000, "gs": 596.0},
            samples=track(base, 20, gs=596.0, alt=37000),
            maneuver={**window, "duration_seconds": 720},
            participants=others,
            winds_aloft={
                "day": start[:10],
                "east_addresses": 35,
                "west_addresses": 33,
                "east_median_kn": 560.0,
                "west_median_kn": 380.0,
                "east_quartiles_kn": [540.0, 575.0],
                "west_quartiles_kn": [360.0, 395.0],
                "asymmetry_kn": 180,
                "altitude_floor_ft": 28000,
                "evaluated_at": base + 86400,
            },
        )
    if kind == "foreign_military":
        return event(
            kind,
            **{**common, "registration": "130336", "callsign": None, "hexcode": "c2b0a1"},
            typecode="C130",
            desc="Lockheed C-130 Hercules",
            readings={"alt_baro": 28000, "gs": 420.0, "dbFlags": 1},
            samples=track(base, 14, gs=420.0, alt=28000),
            foreign_military={
                "country": "CA",
                "country_name": "Canada",
                "basis": ["dbFlags", "military_allocation"],
                "registration_country": "CA",
            },
        )
    if kind == "first_operator_here":
        return event(
            kind,
            **{**common, "registration": "VH-ZNJ", "callsign": "QFA12", "hexcode": "7c7a3f"},
            typecode="B789",
            desc="BOEING 787-9 Dreamliner",
            readings={"alt_baro": 38000, "gs": 520.0},
            samples=track(base, 16, gs=520.0, alt=38000),
            first_sighting={
                "scope": "operator",
                "coverage_days": 120,
                "since": "2026-05-23",
                "designator": "QFA",
                "operator": "Qantas",
            },
        )
    if kind == "aerobatic_practice":
        return event(
            kind,
            **{**common, "callsign": None},
            typecode="E300",
            desc="EXTRA EA-300",
            readings={"alt_baro": 4500, "gs": 150.0},
            samples=track(base, 30, gs=150.0, alt=4500, turn=30),
            maneuver={
                **window,
                "duration_seconds": 720,
                "reversals": 6,
                "altitude_excursion_ft": 1800,
                "span_nm": 2.5,
                "max_turn_rate_dps": 24.0,
                "basis": "turn_rate",
            },
        )
    if kind == "racetrack_orbit":
        return event(
            kind,
            **{**common, "registration": "63-8871", "callsign": None, "hexcode": "ae01ce"},
            typecode="K35R",
            desc="Boeing KC-135R Stratotanker",
            readings={"alt_baro": 24000, "gs": 300.0},
            samples=track(base, 40, step=75, gs=300.0, alt=24000),
            maneuver={
                **window,
                "ended_at": base + 3000,
                "duration_seconds": 3000,
                "racetrack_legs": 5,
                "leg_length_nm": 12.5,
                "axis_heading_deg": 300,
                "lateral_spacing_nm": 6.0,
                "line_repeat_nm": 0.8,
            },
        )
    if kind == "unregistered_address":
        return event(
            kind,
            hexcode=common["hexcode"],
            callsign="TEST12",
            start=start,
            readings={"alt_baro": 6500, "gs": 160.0, "category": "A1"},
            samples=track(base, 12, gs=160.0, alt=6500),
            unregistered={"callsign": "TEST12", "category": "A1", "source": "adsb_icao"},
        )
    # Remaining geometry kinds: circles, passes, holds, spirals and reversals.
    metrics = {"duration_seconds": 720.0, "sample_count": 46}
    if kind in {"circling", "prolonged_holding", "descending_spiral"}:
        metrics["closed_loops"] = 4
    if kind in {"parallel_passes", "repeated_passes"}:
        metrics["complete_passes"] = 5
    if kind in maneuvers_kinds():
        return event(
            kind,
            **{**common, "callsign": None},
            **heli,
            samples=track(
                base, 24 + serial % 10, gs=60.0 + serial % 50, alt=heli["readings"]["alt_baro"], turn=25
            ),
            maneuver={**window, **metrics},
        )
    return event(
        kind,
        **{**common, "callsign": None},
        **heli,
        samples=track(
            base, 24 + serial % 10, gs=60.0 + serial % 50, alt=heli["readings"]["alt_baro"], turn=25
        ),
        pattern={"kind": kind, "started_at": base, "ended_at": base + 720, **metrics},
    )


# A second registry agreeing with the listed type, as typecheck.attach writes it.
AGREEING_CHECK = {
    "state": "agrees",
    "source": "adsbdb",
    "type_code": "B738",
    "registration": "N8ROLE",
    "listed_type": "B738",
}


def rated_event(
    kind,
    base,
    common,
    *,
    reported=False,
    altitudes=None,
    speed=530.0,
    speeds=None,
    tracks=None,
    levels=None,
    winds=None,
    wind_from=270.0,
    typecode="B738",
    desc="BOEING 737-800",
):
    """A sustained reading past a type's certified limits (a Boeing 737-800 unless `typecode`
    names another type with limits), as the collector saves it.

    The comparison comes from the detector's own builders (rules._rated_altitude and
    rules._rated_speed) and, for a speed, wind.py's correction of every confirming report.
    By default: six reports over 75 s, eastbound at FL370 with an 8-knot modelled tailwind,
    or the aircraft's own true-airspeed reports when `reported`. A speed window can vary
    per report: `speeds` (ground speeds), `tracks`, `levels` (pressure altitudes), and
    `winds` ((speed, direction it blows from) pairs).
    """
    entry = reference.limits(typecode)
    speed_kind = kind == "above_rated_speed"
    count = len(speeds) if speeds else 6
    altitudes = altitudes or [42400.0 + 20 * index for index in range(6)]
    levels = (levels or [37000.0] * count) if speed_kind else altitudes
    speeds = speeds or [speed] * count
    tracks = tracks or [90.0] * count
    winds = winds or [(8.0, wind_from)] * count
    aircraft = {"hex": common["hexcode"], "t": typecode, "type": "adsb_icao", "track": tracks[-1]}
    samples = [
        {
            "ts": base + 15 * index,
            "position_at": base + 15 * index,
            "value": speeds[index] if speed_kind else level,
            "source": "adsb_icao",
            "inputs": (
                {
                    "gs": speeds[index],
                    "track": tracks[index],
                    "alt_baro": level,
                    "tas": speeds[index] if reported else None,
                }
                if speed_kind
                else {"alt_baro": level}
            ),
            "quality_flags": [],
        }
        for index, level in enumerate(levels)
    ]
    if speed_kind:
        peak = max(range(count), key=lambda index: speeds[index])
        finding = rules._rated_speed(
            aircraft, speeds[peak], levels[peak], entry, speeds[peak] if reported else None
        )
        corrected = [
            wind.correct_sample(
                entry,
                sample,
                None
                if reported
                else {
                    "speed_kt": winds[index][0],
                    "direction_deg": winds[index][1],
                    "level_hpa": wind.level_for(levels[index]),
                    "source": wind.SOURCE,
                },
            )
            for index, sample in enumerate(samples)
        ]
        block = wind.resolve_rated_speed(entry, finding.evidence["rated_limit"], corrected)
    else:
        block = rules._rated_altitude(aircraft, max(levels), entry).evidence["rated_limit"]
    readings = {
        "alt_baro": levels[-1],
        "gs": speeds[-1] if speed_kind else 440.0,
        "track": tracks[-1],
    }
    if reported:
        readings["tas"] = speeds[-1]
    start = datetime.fromtimestamp(base, timezone.utc).isoformat().replace("+00:00", "Z")
    end = base + 15 * (len(levels) - 1)
    raw = event(
        kind,
        **{**common, "callsign": None, "start": start},
        typecode=typecode,
        desc=desc,
        value=max(speeds) if speed_kind else max(levels),
        unit="kn" if speed_kind else "ft",
        readings=readings,
        samples=track(base, 8, gs=speeds[-1] if speed_kind else 440.0, alt=levels[-1]),
        hits=len(levels),
        rated_limit=block,
        confirmation={
            "sustained": True,
            "samples": samples,
            "duration_seconds": end - base,
            "quality_flags": [],
        },
        type_check={
            **AGREEING_CHECK,
            "type_code": typecode,
            "listed_type": typecode,
            "registration": common["registration"],
            "checked_at": base + 600,
        },
    )
    raw["evidence"]["observed_at"] = end
    return raw | {"last_seen": end}


def maneuvers_kinds():
    from wtf_adsb_publisher import maneuvers

    return maneuvers.KINDS


def copy_event(raw, **changes):
    result = deepcopy(raw)
    result.update(changes)
    return result


DC3_PROFILE = profile(
    "DC3", "The DC-3 first flew on 17 December 1935.", publisher="Boeing", title="DC-3 Commercial Transport"
)
E6_FULL_PROFILE = profile(
    "E6",
    "Naval Air Systems Command describes the E-6B as a relay for strategic communications and an airborne "
    "command post.",
    "Boeing built the E-6 on the 707-based line it also used for the E-3 Sentry.",
    "Boeing delivered 16 E-6s to the Navy between 1989 and 1992.",
    publisher="Naval Air Systems Command",
    title="E-6B Mercury",
)
BEFORE_WATCH = (
    "The saved aircraft database identifies this aircraft as {desc}. The type match does not independently "
    "verify its historical assignment or the purpose of this flight."
)
BEFORE_SPEED = (
    "A saved ground-speed report reads {value} knots. The observation preserves that unusual reported value and "
    "the available surrounding readings; it does not establish the aircraft\u2019s actual performance."
)
BEFORE_GENERIC = (
    "The saved reports contain {kind} reports. These observations describe received data; they do not establish "
    "an emergency, a mission or the cause of an unusual reading."
)


def design_cases():
    """The ten before-and-after cases of the editorial design, section 5, with supporting facts.

    The "after" texts are the design's, minus a clause wherever it needs a fact the facts
    layer does not produce (the design says the writer then drops that clause), and
    rewritten where the story policy changed: no story counts, no day counts, a
    watchlist story names its Plane Alert DB entry, a glitch story offers no cause,
    first sightings carry their fixed headline, and every list takes the serial comma.
    """
    cases = []

    def add(name, raw, *, place=None, profiles=None, before, after):
        cases.append(
            {
                "name": name,
                "event": raw,
                "place": place,
                "profiles": profiles,
                "before": before,
                "after": after,
            }
        )

    start = stamp("2026-09-27T00:05:15Z")
    add(
        "1 Airbus A380 HL7635",
        event(
            "watch_type",
            hexcode="71be35",
            registration="HL7635",
            callsign="AAR224",
            typecode="A388",
            desc="AIRBUS A-380-800",
            start="2026-09-27T00:05:15Z",
            end="2026-09-27T00:22:15Z",
            readings={"archive_alt": 39000, "gs": 487.0},
            samples=track(start, 9, gs=472.0, alt=39000),
            historical_corpus=True,
        ),
        place=PLACES["coleman"],
        profiles=A380_PROFILE,
        before={
            "path": "/hl7635-watch-type-2026-09-27/",
            "headline": "HL7635: A388 type match",
            "dek": BEFORE_WATCH.format(desc="AIRBUS A-380-800"),
        },
        after={
            "headline": "Asiana A380 HL7635 passes at 39,000 ft near Coleman, Michigan",
            "dek": "Flying as AAR224 on 27 September, the double-deck Airbus reported a ground speed of 487 "
            "knots.",
            "paragraph": "HL7635 broadcast callsign AAR224 just after midnight UTC on 27 September 2026. The AAR "
            "prefix is assigned to Asiana Airlines, and the aircraft database lists the registration as an "
            "Airbus A380-800. Nine saved positions near Coleman, Michigan, cover about 18 miles of its path, and "
            "the archive recorded 39,000 ft and a ground speed of 487 knots.",
        },
    )
    start = stamp("2026-06-04T04:00:00Z")
    add(
        "2 Airbus A380 G-XLEB",
        event(
            "watch_type",
            hexcode="40688c",
            registration="G-XLEB",
            callsign="BAW8K",
            typecode="A388",
            desc="AIRBUS A-380-800",
            start="2026-06-04T04:00:00Z",
            end="2026-06-04T04:12:00Z",
            readings={"archive_alt": 37000, "gs": 490.9},
            samples=track(start, 8, step=20, gs=447.0, alt=37000),
            historical_corpus=True,
        ),
        place=PLACES["battle-creek"],
        profiles=A380_PROFILE,
        before={
            "path": "/g-xleb-watch-type-2026-06-04/",
            "headline": "G-XLEB: A388 type match",
            "dek": BEFORE_WATCH.format(desc="AIRBUS A-380-800"),
        },
        after={
            "headline": "British Airways A380 G-XLEB passes near Battle Creek, Michigan",
            "dek": "Flying as BAW8K on 4 June, the double-deck Airbus reported 37,000 ft and a ground speed of "
            "491 knots.",
            "paragraph": "Airbus built the A380 with two full-length passenger decks. The aircraft database lists "
            "G-XLEB as one, under a United Kingdom registration, and its callsign prefix, BAW, is assigned to "
            "British Airways. Eight saved positions near Battle Creek, Michigan, trace about 20 miles of its "
            "path at 04:00 UTC, when the archive recorded 37,000 ft and 490.9 knots.",
        },
    )
    start = stamp("2026-10-04T03:49:00Z")
    add(
        "3 Lockheed C-5M 87-0043",
        event(
            "watch_type",
            hexcode="ae0588",
            registration="87-0043",
            typecode="C5M",
            desc="Lockheed C-5M Super Galaxy",
            start="2026-10-04T03:49:00Z",
            end="2026-10-04T03:49:18Z",
            source="mlat",
            samples=track(start, 3, step=9, gs=420.0, alt=30000, source="mlat"),
        ),
        place=PLACES["marion"],
        profiles=C5M_PROFILE,
        before={
            "path": "/87-0043-watch-type-2026-10-04/",
            "headline": "87-0043: C5M type match",
            "dek": BEFORE_WATCH.format(desc="Lockheed C-5M Super Galaxy"),
        },
        after={
            "headline": "Lockheed C-5M Super Galaxy 87-0043 caught briefly near Marion, Indiana",
            "dek": "Three estimated positions over 18 seconds, logged at 03:49 UTC on 4 October, are all this "
            "record holds.",
            "paragraph": "The aircraft database lists serial 87-0043 as a Lockheed C-5M Super Galaxy, the "
            "transport whose cargo hold opens at both the nose and the tail, with drive-on ramps at each end. "
            "Its three positions near Marion, Indiana, came from multilateration, which estimates position from "
            "the timing of an aircraft's signals at several receivers. The record holds no altitude or speed for "
            "this pass.",
        },
    )
    add(
        "4 Douglas DC-3 N8704",
        event(
            "vintage",
            hexcode="abf93a",
            registration="N8704",
            typecode="DC3",
            desc="DOUGLAS DC-3 Dakota",
            year="1944",
            start="2026-10-02T12:51:00Z",
            end="2026-10-02T12:51:00Z",
            readings={"archive_alt": 3975, "gs": 142.9},
            historical_corpus=True,
        ),
        profiles=DC3_PROFILE,
        before={
            "path": "/n8704-vintage-2026-10-02/",
            "headline": "N8704: an aircraft record dated 1944",
            "dek": "The saved aircraft database gives a manufacture year of 1944. This is a metadata match, not "
            "independently verified airframe history.",
        },
        after={
            "headline": "82-year-old Douglas DC-3 N8704 reports 143 knots at 3,975 ft",
            "dek": "The aircraft database lists the Dakota as built in 1944. It reported its position on 2 "
            "October.",
            "paragraph": "On 2 October at 12:51 UTC the archive saved a single report from N8704, with a ground "
            "speed of 142.9 knots at 3,975 ft. The aircraft database lists it as a Douglas DC-3 Dakota built in "
            "1944, which makes it 82 years old. The DC-3 first flew on 17 December 1935.",
        },
    )
    add(
        "5 Cessna 172 N6508K",
        outlier_cessna(),
        before={
            "path": "/n6508k-outlier-gs-2026-09-18/",
            "headline": "N6508K: a ground-speed report of 1,063.9 kn",
            "dek": BEFORE_SPEED.format(value="1,063.9"),
        },
        after={
            "headline": "Cessna 172 N6508K logs one 1,064-knot report between 111 and 118 knots",
            "dek": "Every other saved ground-speed report around it read between 96 and 123 knots, so the spike "
            "stands alone.",
            "paragraph": "N6508K, a Cessna 172 Skyhawk by its database record, was flying at 4,200 ft when the "
            "archive saved a ground speed of 1,063.9 knots for it at 16:14 UTC on 18 September. The report before "
            "it read 110.8 knots and the one after it 118.1. Every other reading in that stretch stayed between "
            "95.8 and 123 knots, and the one report stands alone.",
        },
    )
    watch = kind_event("watchlist_match", stamp("2026-10-05T20:42:00Z"))
    watch["last_seen"] = stamp("2026-10-05T20:57:00Z")
    # The design's text quotes the final report, so the saved readings are the last ones.
    watch["evidence"]["observed_at"] = watch["last_seen"]
    add(
        "6 Cessna Citation CJ4 N51EM",
        watch,
        place=PLACES["toledo"],
        before={
            "path": "/observation-4053cdb19b3ec8b0c54234d0/",
            "headline": "N51EM: watchlist match reports",
            "dek": BEFORE_GENERIC.format(kind="watchlist match"),
        },
        after={
            "headline": "Citation CJ4 N51EM, listed under Example Aviation Holdings, spotted near Toledo, Ohio",
            "dek": "Its last report showed 509 knots at a geometric altitude of 46,050 ft. The list entry does "
            "not show who was aboard.",
            "paragraph": "Plane Alert DB, a community-maintained list of aircraft that spotters follow, files "
            'N51EM under "Distinctive" with the tags "Business Jet" and "Corporate". The aircraft database '
            "lists the registration as a Cessna Citation CJ4. During a 15-minute pass that ended at 20:57 UTC on 5 October, the jet's final report "
            "showed 509 knots, a vertical rate of zero, and a geometric altitude of 46,050 ft. Geometric altitude "
            "comes from satellite positioning and can differ from the pressure altitude pilots fly by.",
        },
    )
    start = stamp("2026-09-29T17:38:00Z")
    add(
        "7 Boeing E-6B Mercury 163920",
        event(
            "watch_type",
            hexcode="ae0412",
            registration="163920",
            typecode="E6",
            desc="Boeing E-6B Mercury",
            start="2026-09-29T17:38:00Z",
            end="2026-09-29T17:40:00Z",
            source="mlat",
            readings={"archive_alt": 11875},
            samples=track(start, 6, step=24, gs=320.0, alt=11875, source="mlat"),
            historical_corpus=True,
        ),
        place=PLACES["camden"],
        profiles=E6_FULL_PROFILE,
        before={
            "path": "/163920-watch-type-2026-09-29/",
            "headline": "163920: an E-6B Mercury database match",
            "dek": BEFORE_WATCH.format(desc="Boeing E-6B Mercury"),
        },
        after={
            "headline": "Navy E-6B Mercury 163920, a 707-based command post, near Camden, Michigan",
            "dek": "Six estimated positions put the Mercury at 11,875 ft on 29 September. Boeing delivered 16 "
            "E-6s to the Navy between 1989 and 1992.",
            "paragraph": "The aircraft database lists 163920 as a Boeing E-6B Mercury, a type Naval Air Systems "
            "Command describes as a relay for strategic communications and an airborne command post. Boeing "
            "built the E-6 on the 707-based line it also used for the E-3 Sentry. This record holds six "
            "multilateration positions near Camden, Michigan, logged over two minutes from 17:38 UTC, with an "
            "archived altitude of 11,875 ft.",
        },
    )
    start = stamp("2026-10-02T22:07:00Z")
    balloon = event(
        "stratospheric_balloon",
        hexcode="ace75a",
        registration="N930TH",
        callsign="HBAL837",
        start="2026-10-02T22:07:00Z",
        end="2026-10-03T07:33:00Z",
        value=66500,
        unit="ft",
        readings={"alt_baro": 66500, "gs": 18.0},
        samples=[
            {
                "ts": start + index * 870,
                "lat": LAT + index * 0.01,
                "lon": LON + index * 0.01,
                "source": "adsb_icao",
            }
            | {"gs": 11.0 + (index * 7) % 20, "alt": 64500 + (index * 937) % 4151}
            for index in range(40)
        ],
        context=[
            {"ts": start + 1800 + offset * 15, "gs": speed, "alt_baro": 66500}
            for offset, speed in enumerate((14.0, 15.2, 1005.6, 15.8, 16.1))
        ],
        maneuver={
            "started_at": start,
            "ended_at": stamp("2026-10-03T07:33:00Z"),
            "duration_seconds": 33960,
            "max_altitude_ft": 68650,
            "median_ground_speed_kt": 18.5,
        },
    )
    add(
        "8 High-altitude balloon HBAL837",
        balloon,
        before={
            "path": "/n930th-outlier-gs-2026-10-02/",
            "headline": "N930TH: a ground-speed report of 1,005.6 kn",
            "dek": BEFORE_SPEED.format(value="1,005.6"),
        },
        after={
            "headline": "HBAL837 drifts above 64,000 ft for nine hours, like a high-altitude balloon",
            "dek": "Reports between 64,500 and 68,650 ft at 11 to 30 knots fit a balloon. One 1,006-knot report "
            "stands apart from its neighbors.",
            "paragraph": "From 22:07 UTC on 2 October until about 07:33 UTC on 3 October, the aircraft "
            "registered as N930TH reported altitudes between 64,500 and 68,650 ft while moving 11 to 30 "
            "knots over the ground. That slow drift at extreme altitude is consistent with a high-altitude "
            "balloon. One archived report read 1,005.6 knots, between neighbors of about 15 knots, and looks like "
            "a data error. Who launched the flight and why are not in the data.",
        },
    )
    tcas = kind_event("tcas_ra", stamp("2026-10-04T11:25:00Z"))
    add(
        "9 TCAS advisory, Citation CJ4 and Boeing 767",
        tcas,
        before={
            "path": "/observation-<hash>/",
            "headline": "N412TX: tcas ra reports",
            "dek": BEFORE_GENERIC.format(kind="tcas ra"),
        },
        after={
            "headline": "TCAS tells a Citation CJ4 to level off near a Boeing 767",
            "dek": "The closest recorded spacing was about 3.2 NM and 1,100 ft. The jet's vertical rate changed by "
            "1,300 ft/min after the advisory.",
            "paragraph": "A Cessna Citation CJ4, by its database record, broadcast a TCAS resolution advisory, "
            '"Level Off", at 11:25 UTC on 4 October. The advisory named a Boeing 767-300 as the other aircraft. '
            "TCAS issues these instructions automatically, and the broadcast does not establish a near miss or a "
            "loss of separation.",
        },
    )
    add(
        "10 First Embraer C-390 26903",
        first_embraer(),
        before={
            "path": "/observation-<hash>/",
            "headline": "26903: first type here reports",
            "dek": BEFORE_GENERIC.format(kind="first type here"),
        },
        after={
            "headline": "Embraer C-390 Millennium spotted for the first time",
            "dek": "The database lists 26903 as an Embraer EMB-390 military transport. Its address sits in the "
            "block ICAO assigns to Portugal.",
            "paragraph": "On 8 September 2026 the address 497CDE appeared, listed with registration 26903 as an "
            "Embraer EMB-390. It is the first of its type in these records; that says nothing about how often the "
            "type flies elsewhere.",
        },
    )
    return cases


def first_embraer():
    """The design's case 10 aircraft: Portuguese Air Force serial 26903 at address 497CDE."""
    raw = kind_event("first_type_here", stamp("2026-09-08T15:10:00Z"))
    raw["hex"] = "497cde"
    raw["evidence"]["aircraft"].update(hex="497cde", r="26903")
    return raw


def case_facts(case):
    return facts(case["event"], place=case["place"], profiles=case["profiles"])


A380_FLEET = (
    ("HL7634", "71be34", "KAL"),
    ("HL7635", "71be35", "AAR"),
    ("A6-EUA", "896121", "UAE"),
    ("G-XLEB", "40688c", "BAW"),
    ("HL7612", "71be12", "KAL"),
    ("A6-EOF", "8961f2", "UAE"),
    ("HL7626", "71be26", "AAR"),
    ("G-XLEF", "406890", "BAW"),
    ("VH-OQA", "7c4ee1", "QFA"),
    ("D-AIMA", "3c6741", "DLH"),
)
PLACE_ROTATION = ("coleman", "battle-creek", "lansing", "toledo", "camden", "marion")


def a380_sequence(count=20, start="2026-09-01"):
    """count A380 sightings on consecutive days, as (event, place, airframe registration)."""
    first = stamp(start + "T00:00:00Z")
    sequence = []
    for index in range(count):
        registration, hexcode, designator = A380_FLEET[(index * 3) % len(A380_FLEET)]
        moment = first + index * 86400 + (index * 3797) % 80000
        positions = 3 if index % 7 == 4 else 6 + index % 7
        callsign = f"{designator}{100 + (index * 37) % 800}" if index % 5 != 3 else None
        raw = event(
            "watch_type",
            hexcode=hexcode,
            registration=registration,
            callsign=callsign,
            typecode="A388",
            desc="AIRBUS A-380-800",
            start=datetime.fromtimestamp(moment, timezone.utc).isoformat().replace("+00:00", "Z"),
            readings={"archive_alt": 35000 + (index % 5) * 1000, "gs": 455.0 + (index * 7) % 60},
            samples=track(moment, positions, gs=470.0, alt=35000 + (index % 5) * 1000),
            historical_corpus=True,
        )
        sequence.append((raw, PLACES[PLACE_ROTATION[index % len(PLACE_ROTATION)]], registration))
    return sequence


def recent_entry(story, facts):
    """A rendered story as the variety guard's recent list carries it."""
    values = story_facts.fact_map(facts)
    return {
        **story,
        "kind": facts["kind"],
        "type_code": (values.get("id.type_code") or {}).get("value"),
        "registration": (values.get("id.registration") or {}).get("value"),
        "date": (values.get("obs.date") or {}).get("value"),
    }


def render_sequence(sequence, profiles=None, editorial_module=None):
    """Render events in order, each against the stories before it; returns (story, facts, report)."""
    from wtf_adsb_publisher import editorial

    module = editorial_module or editorial
    rendered, recent = [], []
    for raw, place, registration in sequence:
        day = datetime.fromtimestamp(raw["first_seen"], timezone.utc).date().isoformat()
        built = facts(raw, place=place, profiles=profiles)
        story, report = module.render_with_report(built, f"episode-{registration}-{day}", recent=recent)
        rendered.append((story, built, report))
        recent.append(recent_entry(story, built))
    return rendered


@functools.lru_cache(maxsize=1)
def a380_run(count=20):
    """The rendered A380 sequence, shared by the tests that read it (they must not change it)."""
    return tuple(render_sequence(a380_sequence(count), profiles=A380_PROFILE))
