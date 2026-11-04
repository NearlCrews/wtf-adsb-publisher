"""Offline approval ledger and public event snapshots, independent of collector retention.

The ledger is private. Export creates a new allowlisted tree; it never copies an
arbitrary evidence object into public JSON. This module does not publish to Ghost.
"""

import fcntl
import hashlib
import ipaddress
import json
import math
import os
import re
import tempfile
import uuid
from contextlib import contextmanager, suppress
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from . import (
    enrichment_rules,
    episode_evidence,
    kinds,
    limits,
    maneuvers,
    private_terms,
    rules,
    site_identity,
    site_origin,
    stories,
    story_urls,
    watchlists,
)
from . import reference as reference_data
from .curation import SUSTAINED_FIELDS, priority_evidence, validate_daily_limit
from .rules import finite

VERSION = 1
MAX_INVENTORY_AGE_SECONDS = 900
MAX_LIMITATIONS = 40
# The most withdrawn stories a published snapshot lists; the most recent withdrawals are kept.
MAX_RETIRED = 300
EVENT_TYPES = (
    ("flight-patterns", "Flight patterns"),
    ("unusual-aircraft", "Unusual aircraft"),
    ("performance", "Performance"),
    ("data-quality", "Data quality"),
    ("transponder-reports", "Transponder reports"),
)
TYPE_LABELS = dict(EVENT_TYPES)
POSITION_SOURCES = {
    "adsb_icao",
    "adsb_icao_nt",
    "adsr_icao",
    "tisb_icao",
    "adsc",
    "mlat",
    "other",
    "mode_s",
    "adsb_other",
    "adsr_other",
    "tisb_trackfile",
    "tisb_other",
    "mode_ac",
    "unknown",
}
EMERGENCY_STATUSES = stories.EMERGENCY_STATUSES
TEXT_REPORTS = {"squawk": "Transponder code", "emergency_status": "Reported emergency status"}
# Archive labels are carried between saved positions; they carry only their own time.
LABEL_REPORTS = {"callsign": "Callsign", "squawk": "Transponder code"}
PUBLIC_EVIDENCE_FIELDS = (
    "observed_start",
    "observed_end",
    "kind",
    "aircraft",
    "measurements",
    "references",
    "source",
    "limitations",
)
# Optional blocks join the evidence hash only when present, so older events keep theirs.
OPTIONAL_EVIDENCE_FIELDS = ("priority_evidence", "watchlist", "participants")
# Each kind's public section (data/topics.json routes every topic to one), in registry order.
KIND_TYPES = kinds.table("section")
READINGS = {
    "gs": ("Ground speed", "kn"),
    "tas": ("True airspeed", "kn"),
    "mach": ("Mach report", "Mach"),
    "alt_baro": ("Pressure altitude", "ft"),
    "alt_geom": ("Geometric altitude", "ft"),
    "archive_alt": ("Archived altitude; reference not retained", "ft"),
    "baro_rate": ("Pressure-altitude vertical rate", "ft/min"),
    "geom_rate": ("Geometric-altitude vertical rate", "ft/min"),
    "nic": ("Navigation integrity category", "category"),
    "nac_p": ("Navigation accuracy category", "category"),
    "nav_altitude_mcp": ("Selected altitude (autopilot setting)", "ft"),
    "nav_qnh": ("Altimeter setting", "hPa"),
    "nic_baro": ("Barometric altitude integrity", "category"),
    "sil": ("Source integrity level", "category"),
    "gva": ("Geometric vertical accuracy", "category"),
    "sda": ("System design assurance", "category"),
    "version": ("ADS-B version", ""),
}
PATTERN_MEASUREMENTS = {
    "duration_seconds": ("Analysis duration", "s"),
    "sample_count": ("Position samples", "samples"),
    "accumulated_turns": ("Accumulated turns", "turns"),
    "closed_loops": ("Closed loops", "loops"),
    "ground_closed_loops": ("Loops closed over one ground position", "loops"),
    "drift_corrected_loops": ("Loops closed after allowing for drift", "loops"),
    "orbit_center_drift_nm": ("Orbit-centre drift per loop", "NM"),
    "complete_passes": ("Complete passes", "passes"),
    "median_position_speed_kn": ("Median position-derived speed", "kn"),
    "median_reported_speed_kn": ("Median reported ground speed", "kn"),
    "largest_gap_seconds": ("Largest sample gap", "s"),
}
EPISODE_MEASUREMENTS = episode_evidence.METRICS
# A winds-aloft day's counts and medians (stories.winds_aloft), over the episode window.
WINDS_MEASUREMENTS = {
    "asymmetry_kn": ("Eastbound minus westbound median ground speed", "kn"),
    "east_median_kn": ("Median eastbound ground speed", "kn"),
    "west_median_kn": ("Median westbound ground speed", "kn"),
    "east_addresses": ("Eastbound aircraft counted", "aircraft"),
    "west_addresses": ("Westbound aircraft counted", "aircraft"),
}
# A peak is one report with its own time; a detection threshold is a rule, not a reading.
PEAK_MEASUREMENTS = episode_evidence.PEAK_METRICS
RULE_MEASUREMENTS = episode_evidence.RULE_METRICS
EPISODE_TEXT = {
    "code": "Transponder code",
    "code_sequence": "Transponder code sequence",
    "advisory": "Resolution advisory text",
    "advisory_complement": "Advisory complement",
}
DISCONTINUITY_MEASUREMENTS = {
    "elapsed_seconds": ("Position interval", "s"),
    "displacement_nm": ("Position displacement", "NM"),
    "implied_speed_kn": ("Position-derived speed", "kn"),
}
# The saved readings around an outlying value, so the card, chart and prose agree.
NEIGHBOR_FIELDS = {
    name: field
    for name, field in kinds.table("neighbor_field").items()
    if kinds.kind(name).family == "outlier"
}
NEIGHBOR_MEASUREMENTS = {
    "previous_report": "Report before the outlying value",
    "next_report": "Report after the outlying value",
    "lowest_surrounding_report": "Lowest surrounding report",
    "highest_surrounding_report": "Highest surrounding report",
    "surrounding_report_count": "Surrounding reports",
}
NEIGHBOR_REPORTS = {"previous_report", "next_report"}
# A rated finding's comparison (stories.rated_limit), as evidence-card rows. The certified
# figures (limits.LIMITS), the modelled wind and the registry that confirmed the type are
# reference data, not readings from the aircraft. The analysis-window rows describe the
# reports that were over the limit: floors over every report counted, and the decisive
# report's own figures (the one closest to the limit, whose ground speed the airspeed was
# worked out from). `field` is where a row's value comes from: the certified figure's
# dataset key, the modelled wind's field, or the sustained comparison's. `kinds` are the
# kinds whose card may carry the row, so a snapshot cannot move one onto another kind, and
# `basis` is the airspeed origin an airspeed row shows (limits.AIRSPEED_BASES).
RATED_KINDS = rules.RATED_KINDS
ALTITUDE_KINDS = kinds.where(rated="altitude")
SPEED_KINDS = kinds.where(rated="speed")


class RatedRow(NamedTuple):
    """One evidence-card row of a rated comparison: its key, source field, label, unit, and kinds."""

    key: str
    field: str
    label: str
    unit: str
    kinds: frozenset
    basis: str | None = None


RATED_LIMIT_ROWS = tuple(
    RatedRow(
        limit.card_key,
        name,
        limit.card_label,
        limit.unit,
        ALTITUDE_KINDS if name == "max_operating_altitude_ft" else SPEED_KINDS,
    )
    for name, limit in limits.LIMITS.items()
)
WIND_ROWS = (
    RatedRow("wind_speed_kt", "speed_kt", "Modelled wind speed", "kn", SPEED_KINDS),
    RatedRow("wind_direction_deg", "direction_deg", "Modelled wind direction (from)", "degrees", SPEED_KINDS),
    RatedRow("wind_level_hpa", "level_hpa", "Modelled wind pressure level", "hPa", SPEED_KINDS),
)
TYPE_CHECK_ROW = RatedRow("type_check", "source", "Type confirmed by a second source", "", RATED_KINDS)
RATED_WINDOW_ROWS = (
    RatedRow(
        "rated_lowest_alt_ft", "altitude_ft", "Lowest pressure altitude over the limit", "ft", ALTITUDE_KINDS
    ),
    RatedRow(
        "rated_margin_ft",
        "margin_ft",
        "Smallest height above the certified maximum altitude",
        "ft",
        ALTITUDE_KINDS,
    ),
    RatedRow(
        "rated_lowest_gs_kt", "ground_speed_kt", "Lowest ground speed while over the limit", "kn", SPEED_KINDS
    ),
    RatedRow(
        "rated_decisive_gs_kt",
        "decisive_ground_speed_kt",
        "Ground speed of the report closest to the limit",
        "kn",
        SPEED_KINDS,
    ),
    RatedRow(
        "rated_estimated_tas_kt",
        "airspeed_kt",
        "Estimated true airspeed of the report closest to the limit",
        "kn",
        SPEED_KINDS,
        "modelled_wind",
    ),
    RatedRow(
        "rated_reported_tas_kt",
        "airspeed_kt",
        "Reported true airspeed of the report closest to the limit",
        "kn",
        SPEED_KINDS,
        "reported",
    ),
    # limits.speed_comparison gives an equivalent airspeed only when Vmo or Vne binds: those are
    # stated as indicated airspeeds, which it approximates, and Mmo is compared by Mach.
    RatedRow(
        "rated_equivalent_kt",
        "equivalent_airspeed_kt",
        "Equivalent airspeed of the report closest to the limit",
        "kn",
        SPEED_KINDS,
    ),
    RatedRow("rated_mach", "mach", "Mach number of the report closest to the limit", "Mach", SPEED_KINDS),
    RatedRow(
        "rated_decisive_alt_ft",
        "altitude_ft",
        "Pressure altitude of the report closest to the limit",
        "ft",
        SPEED_KINDS,
    ),
    RatedRow(
        "rated_margin_percent", "margin_percent", "Smallest margin over the certified limit", "%", SPEED_KINDS
    ),
    RatedRow("rated_reports", "reports", "Reports over the limit", "reports", RATED_KINDS),
    RatedRow("rated_seconds", "seconds", "Span of the reports over the limit", "s", RATED_KINDS),
)
RATED_REFERENCE = {row.key: (row.label, row.unit) for row in (*RATED_LIMIT_ROWS, *WIND_ROWS, TYPE_CHECK_ROW)}
RATED_WINDOW = {row.key: (row.label, row.unit) for row in RATED_WINDOW_ROWS}
RATED_KIND_KEYS = {
    kind: frozenset(
        row.key
        for row in (*RATED_LIMIT_ROWS, *WIND_ROWS, TYPE_CHECK_ROW, *RATED_WINDOW_ROWS)
        if kind in row.kinds
    )
    for kind in kinds.table("rated")
}
WIND_KEYS = frozenset(row.key for row in WIND_ROWS)
# The range a rated reference figure must lie in; any other is a certified figure, so positive.
REFERENCE_RANGES = {
    "wind_direction_deg": lambda value: 0 <= value <= 360,
    "wind_speed_kt": lambda value: value >= 0,
}
PARTICIPANT_ROLES = {
    "lead": "Lead aircraft",
    "member": "Other aircraft",
    "wingman": "Wingman",
    "threat": "Aircraft named in the advisory",
}
# Another aircraft's own readings and window metrics; peaks and rules have no scope there.
PARTICIPANT_MEASUREMENTS = {
    **{key: value for key, value in READINGS.items() if value[1] not in {"category", ""}},
    **{
        key: value
        for key, value in EPISODE_MEASUREMENTS.items()
        if key not in PEAK_MEASUREMENTS and key not in RULE_MEASUREMENTS
    },
    **episode_evidence.PARTICIPANT_METRICS,
}
# The wording stories.watchlist_summary() writes; every public watchlist block pins it.
WATCHLIST_SOURCE = stories.WATCHLIST_SOURCE
WATCHLIST_ATTRIBUTION = stories.WATCHLIST_ATTRIBUTION
WATCHLIST_CATEGORIES = watchlists.NEUTRAL_LABELS | {watchlists.GENERIC_CATEGORY}
WATCHLIST_LICENSES = [item["name"] for item in watchlists.LICENSES]
WATCHLIST_CLAIM_SCOPE = stories.CLAIM_SCOPE
MISSION = "Track geometry does not establish a mission, purpose, or activity on the ground."
NOT_EMERGENCY = "These reports are not an emergency or distress determination."
CODE_ALONE = "A transmitted code or status alone does not confirm an emergency or other incident."
GROUND_SPEED = "Ground speed is not airspeed; wind adds to or subtracts from it."
ALTITUDE_REFERENCES = (
    "Pressure and geometric altitudes use different references; neither is height above terrain."
)
TYPE_MISMATCH = (
    "A value outside the listed type's envelope can come from wrong type metadata, an address error, or "
    "corrupted data; it does not establish real performance."
)
GNSS = "Lower navigation-integrity reports do not establish GPS jamming, spoofing, or any other cause."
FIRST_HERE = "First in this collection's retained records; this is not a worldwide rarity claim or a first-ever sighting."
RARE_HERE = "Uncommon in this collection's preceding 30 days; this is not a worldwide rarity claim."
DATABASE_YEAR = (
    "The manufacture year comes from the aircraft database and has not been independently verified."
)
DATABASE_LABEL = (
    "Military and interesting flags are database labels; they do not identify a mission or its crew."
)
EMITTER = "Emitter categories are configured aboard the aircraft; incorrect settings occur."
SPECIAL_CODE = (
    "Special-use code meanings follow United States FAA assignments and can differ in other countries "
    "or at individual facilities."
)
# What a certified-limit comparison is not follows each kind's own caveat (rules.RATED_SCOPE).
RATED_ALTITUDE = (
    "Pressure altitude from ADS-B is not a verified flight level, and a short excursion or an altimeter "
    "setting can read above a certified limit. " + rules.RATED_SCOPE
)
RATED_SPEED = (
    "The airspeed is estimated from ground speed and a modelled wind, or reported by the aircraft, and a "
    "faulty reading or wind estimate can put it above a certified limit. " + rules.RATED_SCOPE
)
# The attribution Open-Meteo's licence asks for, shown wherever its wind is.
WIND_ATTRIBUTION = f"The wind figures are modelled. {enrichment_rules.WIND_ATTRIBUTION}."
# The compared reports can begin after the season does (stories name the day).
SEASON_SCOPE = (
    "The comparison covers only this collection's retained reports of the type since its baseline began, "
    "which can be later than the start of the season; it is not an aircraft performance limit or a global "
    "record."
)
# Caveats only the evidence card states: a glitch story offers no cause, and a season record
# scopes its claim in its own words. The template writer never restates them (editorial).
CARD_ONLY_LIMITATIONS = frozenset({TYPE_MISMATCH, SEASON_SCOPE})
KIND_LIMITATIONS = {
    "rapid_descent": (NOT_EMERGENCY,),
    "rapid_climb": (NOT_EMERGENCY,),
    "vertical": (NOT_EMERGENCY,),
    "fast": (GROUND_SPEED,),
    "above_rated_altitude": (RATED_ALTITUDE,),
    "above_rated_speed": (RATED_SPEED,),
    "seasonal_speed_record": (GROUND_SPEED, SEASON_SCOPE),
    "outlier_gs": (GROUND_SPEED,),
    "type_speed_mismatch": (GROUND_SPEED, TYPE_MISMATCH),
    "wind_boost": (
        "The difference between ground speed and true airspeed is not a measured wind vector; the two "
        "reports can have different ages.",
    ),
    "high": (ALTITUDE_REFERENCES,),
    "outlier_alt_baro": (ALTITUDE_REFERENCES,),
    "type_altitude_mismatch": (ALTITUDE_REFERENCES, TYPE_MISMATCH),
    "altitude_gap": (
        "Pressure and geometric altitudes use different references; weather and stale fields can widen "
        "the gap between them.",
    ),
    "outlier_archive_alt": ("The archive does not retain whether an altitude was pressure or geometric.",),
    "stratospheric_balloon": (
        "The reports are consistent with a high-altitude balloon; the operator and purpose are not established.",
        ALTITUDE_REFERENCES,
    ),
    "tcas_ra": (
        "A TCAS resolution advisory is an automatic collision-avoidance instruction; the broadcast does not "
        "establish a near miss or a loss of separation.",
    ),
    "formation_flight": (
        "Close proximity is consistent with formation flight; the data cannot show coordination or intent.",
    ),
    "holding_stack": (
        "Several aircraft circling near one another does not establish an air traffic control holding "
        "instruction or its cause.",
    ),
    "gnss_integrity_loss": (GNSS,),
    "gnss_integrity_cluster": (GNSS,),
    "survey_grid": (
        "Systematic parallel legs resemble aerial survey or mapping; the operator's purpose is not established.",
    ),
    "thermal_soaring": (
        "Climbing while circling is consistent with soaring in rising air; engine use and the lift source "
        "are not known.",
    ),
    "selected_altitude_overshoot": (episode_evidence.LIMITATIONS["selected_altitude_not_clearance"],),
    "extreme_turn_rate": (
        "A turn rate derived from positions can be inflated by position noise; it is not a measured bank angle.",
    ),
    "identity_conflict": (
        "Identity checks compare database and transmitted identifiers; a mismatch can come from a database "
        "error, a re-registration, or an avionics setting, and its cause is unknown.",
    ),
    "first_type_here": (FIRST_HERE,),
    "first_country_here": (FIRST_HERE,),
    "notable_variant": (
        "The variant comes from the aircraft database description; the data show the airframe, not who was "
        "aboard or its mission.",
    ),
    "emitter_type_mismatch": (
        "The emitter category is set aboard the aircraft and the type comes from a database; either can be wrong.",
    ),
    "military": (DATABASE_LABEL,),
    "database_watch": (DATABASE_LABEL,),
    "vintage": (DATABASE_YEAR,),
    "rare_here": (RARE_HERE,),
    "unusual_emitter": (EMITTER,),
    "space_category": (EMITTER,),
    "special_use_code": (CODE_ALONE, SPECIAL_CODE),
    "special_squawk_7400": (SPECIAL_CODE,),
    "winds_aloft_day": (
        "Ground-speed differences by direction are consistent with winds aloft; the wind itself was not measured.",
        "Open-Meteo values, when shown, are a model forecast for one grid cell, not an observation.",
    ),
    "foreign_military": (
        stories.DATABASE_FLAG_CAVEAT,
        "An address block names the State of registry; a re-registered aircraft can keep an old address.",
    ),
    "first_operator_here": (
        "First in this collection's retained records; an operator designator in a callsign is a label the "
        "crew enters, not a confirmed operator.",
    ),
    "aerobatic_practice": (stories.PATTERN_LIMITATION,),
    "unregistered_address": (
        "No registration was found in the registries the site consults on the dates shown; the aircraft may "
        "be registered elsewhere.",
    ),
    "racetrack_orbit": (stories.PATTERN_LIMITATION,),
}
WATCHLIST_SCOPE = "A Plane Alert DB association is not evidence of the aircraft's operator on this flight or of anyone aboard."
# What each kind's claim rests on; every basis carries its own caveat.
DATABASE_KINDS = set(kinds.where(database_basis=True))
BASIS_LIMITATIONS = {
    "geometry": (MISSION,),
    "status": (CODE_ALONE, NOT_EMERGENCY),
    "community-list": (WATCHLIST_SCOPE,),
    "database": (),
    "telemetry": (),
}
MLAT_ESTIMATE_KINDS = episode_evidence.MLAT_ESTIMATE_KINDS
RATE_KINDS = set(kinds.where(rate_reading=True))
ARCHIVE_FRESHNESS = episode_evidence.LIMITATIONS["archive_field_freshness_unknown"]
SIGN_NOTE = "Vertical rates are signed: negative values are descents and positive values are climbs."
IDENTITY_CONFLICT = (
    "Identity checks found a conflict between the database identity and the transmitted identifiers; the "
    "listed registration or type may not describe this aircraft."
)
# Evidence sentences a saved packet or snapshot may still carry in an earlier wording: each
# current sentence with the exact wordings it replaced (the serial commas of 7 October
# 2026). Every read that compares a saved sentence with its constant goes through
# current_text(), so a wording change never makes a saved record unreadable; projections
# and regenerated packets use the current sentence. Keep every entry: a ledger can hold
# packets from before each change listed. A future wording change adds its old wording here.
LEGACY_TEXT = {
    WATCHLIST_CLAIM_SCOPE: (
        "Source association only; not verified current ownership, operator, historical association or "
        "presence of any person aboard.",
    ),
    stories.DATABASE_FLAG_CAVEAT: (
        "Database flags are labels in the aircraft database. PIA (Privacy ICAO Address) and LADD (Limiting "
        "Aircraft Data Displayed) describe FAA data-distribution programs; they do not identify the "
        "operator, the occupants or the purpose of a flight.",
    ),
    stories.PATTERN_LIMITATION: (
        "Movement geometry does not establish the operator, mission or activity on the ground.",
    ),
    stories.SOURCE_CAVEAT: (
        "The collection can include ADS-B, MLAT and relayed data; a record in it does not show how its "
        "messages were received.",
    ),
    episode_evidence.LIMITATIONS["participant_uncertain_source"]: (
        "At least one other aircraft was tracked by multilateration, TIS-B or ADS-R rather than its own "
        "ADS-B broadcast.",
    ),
    episode_evidence.LIMITATIONS["airport_maneuver_hypothesis"]: (
        "Runway alignment suggests an airport maneuver; landing, clearance and intent are unconfirmed. "
        "Altitudes are not terrain clearance.",
    ),
    MISSION: ("Track geometry does not establish a mission, purpose or activity on the ground.",),
    GNSS: ("Lower navigation-integrity reports do not establish GPS jamming, spoofing or any other cause.",),
    TYPE_MISMATCH: (
        "A value outside the listed type's envelope can come from wrong type metadata, an address error or "
        "corrupted data; it does not establish real performance.",
    ),
    KIND_LIMITATIONS["identity_conflict"][0]: (
        "Identity checks compare database and transmitted identifiers; a mismatch can come from a database "
        "error, a re-registration or an avionics setting, and its cause is unknown.",
    ),
}
_CURRENT_TEXT = {old: new for new, olds in LEGACY_TEXT.items() for old in olds}


def current_text(value):
    """A saved evidence sentence in its current wording; any other value as it is."""
    return _CURRENT_TEXT.get(value, value) if isinstance(value, str) else value


# Malformed private data raises one of these; each means "this record or value is unusable".
DATA_ERRORS = (ValueError, TypeError, KeyError, AttributeError, OverflowError)
# Names refused in any public text: the built-in patterns and the operator's configured
# terms (private_terms), which the release, Ghost, and theme checks apply too, plus
# localhost and a bare /.private path, which the theme's own scripts and styles name
# and so cannot join that list. Entry points that load publisher.json configure it;
# until one does, the built-in patterns and any file PRIVATE_TERMS_FILE names apply.
_private_text = None


def configure_private_text(config=None):
    """Compile the public-text deny list from `config` and use it from now on."""
    global _private_text
    _private_text = re.compile(private_terms.pattern(config) + r"|\blocalhost\b|/\.private\b", re.I)
    return _private_text


def private_text():
    """The compiled public-text deny list."""
    return _private_text or configure_private_text()


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def evidence_version(event):
    fields = {key: event[key] for key in PUBLIC_EVIDENCE_FIELDS}
    fields.update({key: event[key] for key in OPTIONAL_EVIDENCE_FIELDS if key in event})
    return _digest(fields)


def content_version(event):
    """What a reader of the article sees: its evidence, its prose with the type facts and
    sources behind it, and any weekly research. Publication times and the rendering of
    the evidence card are not part of it, so a card refresh never counts as a revision.
    """
    story = event.get("story") or {}
    return _digest(
        {
            "evidence_version": event["evidence_version"],
            "title": event["title"],
            "summary": event["summary"],
            "story": {key: story.get(key) for key in ("headline", "dek", "paragraphs", "sources")},
            "type_facts": {
                key: value for key, value in (story.get("facts") or {}).items() if key.startswith("tp.")
            },
            "weekly_spotlight": event.get("weekly_spotlight"),
        }
    )


def utc(value):
    """Accept explicit-offset ISO timestamps only and normalize them to UTC."""
    if not isinstance(value, str):
        raise ValueError("A UTC timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Invalid timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("Timestamp needs an explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def instant(value):
    """An explicit-offset ISO timestamp as an aware UTC datetime."""
    return datetime.fromisoformat(utc(value).replace("Z", "+00:00"))


# The older private names stay for callers that use them.
_instant = instant


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stamp(at=None):
    """`at` as normalized UTC, or the current time to the second."""
    return utc(at or now_utc())


def epoch_utc(seconds):
    """A POSIX time as normalized UTC."""
    return utc(datetime.fromtimestamp(seconds, timezone.utc).isoformat())


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("A calendar date in YYYY-MM-DD form is required")
    return date.fromisoformat(value).isoformat()


def _text(value, limit=600):
    if (
        not isinstance(value, str)
        or not 1 <= len(value.strip()) <= limit
        or private_text().search(value)
        or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]|<[^>]+>", value)
    ):
        raise ValueError("Invalid public text")
    return value.strip()


def public_path(value):
    """Local paths only, without encoded traversal, queries or private endpoints."""
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"/[A-Za-z0-9][A-Za-z0-9._/-]*/?", value)
        or "//" in value
        or any(part in {".", "..", ".private"} for part in value.split("/"))
        or value.split("/")[1].lower() in {"ghost", "wtf", "api"}
        or private_text().search(value)
    ):
        raise ValueError("Invalid public path")
    return value


STORY_PATH = re.compile(r"/stories/(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)/")
# Ghost stores the bare slug and keeps it unique across every post and page.
GHOST_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,199}")
# A placeholder for projections made before a story has its own text and URL.
PLACEHOLDER_EDITORIAL = {
    "title": "Projection check",
    "summary": "Projection check.",
    "article_path": "/stories/projection-check-1-jan-2026/",
}


def _article_path(value):
    """Only /stories/{slug}/ in the story slug family; older ledgers stay with older code."""
    value = public_path(value)
    match = STORY_PATH.fullmatch(value)
    if not match or not story_urls.NEW_SLUG.fullmatch(match["slug"]):
        raise ValueError("Article path must be /stories/{slug}/ with a story slug")
    return value


def article_slug(path):
    """The bare Ghost slug of a story path."""
    return STORY_PATH.fullmatch(_article_path(path))["slug"]


def ghost_post_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,80}", value):
        raise ValueError("A stable Ghost post ID is required")
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Measurement needs a finite number")
    try:
        bounded = math.isfinite(value)
    except OverflowError:
        bounded = False
    if not bounded:
        raise ValueError("Measurement needs a finite number")
    return value


def _identity(raw):
    if not isinstance(raw, dict):
        raise ValueError("Invalid aircraft identity")
    result = {}
    for field in ("icao_address", "registration", "callsign", "type_code"):
        value = raw.get(field)
        if value is not None:
            # An address may arrive in lower case; every other identifier is upper case.
            if not isinstance(value, str) or not re.fullmatch(
                stories.IDENTIFIERS[field], value.upper() if field == "icao_address" else value
            ):
                raise ValueError("Invalid aircraft identifier")
            result[field] = value.upper()
    if raw.get("type_description") is not None:
        result["type_description"] = _text(raw["type_description"], 160)
    return result


# Name suffixes that never reach the public internet, checked on the canonical hostname.
PRIVATE_SUFFIXES = (
    ".local",
    ".internal",
    ".ts.net",
    ".localhost",
    ".lan",
    ".home",
    ".intranet",
    ".arpa",
    ".onion",
)
# Labels as browsers accept them, underscores included ("docs_host.example.com").
HOST_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")


def public_host(host):
    """A public DNS hostname in canonical form, or None.

    The terminal dot is stripped and the name lowercased before any check, so
    "office.local." is as private as "office.local". Every IP-literal spelling a browser
    accepts is refused: dotted, short-form ("127.1"), hex ("0x7f.0.0.1"), octal, a bare
    integer, and bracketed IPv6, which urlsplit hands over without its brackets.
    """
    if not isinstance(host, str):
        return None
    try:
        host = host.strip().lower().rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = host.split(".")
    if len(labels) < 2 or not all(HOST_LABEL.fullmatch(label) for label in labels):
        return None
    if host.endswith(PRIVATE_SUFFIXES) or host == "localhost":
        return None
    # A numeric or hex last label is an IP spelling, never a top-level domain.
    if re.fullmatch(r"0x[0-9a-f]+|\d+", labels[-1]):
        return None
    with suppress(ValueError):
        ipaddress.ip_address(host)
        return None
    return host


def public_url(value):
    """An HTTPS URL on a public hostname, without credentials or private hosts."""
    url = _text(value, 2048)
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or re.search(r"\s", url)
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 443}
    ):
        raise ValueError("Invalid public reference")
    if public_host(parts.hostname) is None:
        raise ValueError("References require public hostnames")
    return url


def _references(raw):
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("Invalid references")
    output, ids = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Invalid reference")
        identifier = _text(item.get("id"), 80)
        url = public_url(item.get("url"))
        if identifier in ids:
            raise ValueError("Invalid public reference")
        ids.add(identifier)
        output.append({"id": identifier, "label": _text(item.get("label"), 180), "url": url})
    return output


# The story text and routing that the static build and Ghost receive. Both stay
# outside evidence_version, so a prose upgrade never looks like an evidence change.
STORY_FIELDS = (
    "version",
    "source",
    "angle",
    "variant",
    "headline",
    "seo_title",
    "dek",
    "paragraphs",
    "opener_skeleton",
    "headline_skeleton",
    "facts_digest",
    "facts",
    "sources",
)
STORY_SOURCES = {"template", "ai"}
# A story lists at most this many facts (editorial.MAX_STORY_FACTS).
MAX_STORY_FACTS = 30
CONTEXT_OPTIONAL = ("type", "registration_slug", "operator", "place", "country")
# The fields of a type hub and an operator hub in the public routing, and their optional ones.
TYPE_ROUTING = ("code", "name", "slug")
OPERATOR_ROUTING = ("name", "slug", "basis", "designator")
ROUTING_OPTIONAL = {"type": ("same_as",)}
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
FACT_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[A-Za-z0-9_-]+)+")
# The publisher treats an event with exception-grade evidence as ordinary.
PRIORITIES = {"ordinary"}


def _line(value, limit):
    """Normalized public text on one line."""
    text = _text(value, limit)
    if re.search(r"[\r\n\t]", text):
        raise ValueError("Story text must be one line")
    return text


def _slug(value, limit):
    if not isinstance(value, str) or len(value) > limit or not SLUG.fullmatch(value):
        raise ValueError("Invalid public slug")
    return value


def _fact_value(value, *, nested=False):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _text(value, 600)
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, list) and not nested and len(value) <= 30:
        return [_fact_value(item, nested=True) for item in value]
    raise ValueError("Invalid story fact")


def _story(raw):
    """Allowlisted story fields; the prose checks themselves run when a story is written."""
    _keys(raw, STORY_FIELDS)
    if (
        type(raw["version"]) is not int
        or raw["version"] < 1
        or raw["source"] not in STORY_SOURCES
        or not isinstance(raw["angle"], str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", raw["angle"])
        or not isinstance(raw["variant"], str)
        # The engine names every phrase builder a story used, so variants run long.
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.:-]{0,199}", raw["variant"])
        or not isinstance(raw["facts_digest"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", raw["facts_digest"])
    ):
        raise ValueError("Invalid story")
    paragraphs, facts, sources = raw["paragraphs"], raw["facts"], raw["sources"]
    if (
        not isinstance(paragraphs, list)
        or not 1 <= len(paragraphs) <= 8
        or not isinstance(facts, dict)
        or len(facts) > MAX_STORY_FACTS
        or any(not isinstance(key, str) or len(key) > 80 or not FACT_ID.fullmatch(key) for key in facts)
        or not isinstance(sources, list)
        or len(sources) > 16
    ):
        raise ValueError("Invalid story")
    normalized, ids = [], set()
    for source in sources:
        _keys(source, ("id", "publisher", "title", "url"))
        if (
            not isinstance(source["id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", source["id"])
            or source["id"] in ids
        ):
            raise ValueError("Invalid story source")
        ids.add(source["id"])
        normalized.append(
            {
                "id": source["id"],
                "publisher": _line(source["publisher"], 180),
                "title": _line(source["title"], 300),
                "url": public_url(source["url"]),
            }
        )
    return {
        "version": raw["version"],
        "source": raw["source"],
        "angle": raw["angle"],
        "variant": raw["variant"],
        "headline": _line(raw["headline"], 160),
        "seo_title": _line(raw["seo_title"], 120),
        "dek": _line(raw["dek"], 300),
        "paragraphs": [_line(paragraph, 1500) for paragraph in paragraphs],
        "opener_skeleton": _line(raw["opener_skeleton"], 300),
        "headline_skeleton": _line(raw["headline_skeleton"], 300),
        "facts_digest": raw["facts_digest"],
        "facts": {key: _fact_value(facts[key]) for key in sorted(facts)},
        "sources": normalized,
    }


def _routing(raw):
    """Topic, section and hub routing; nothing here names a receiver location."""
    _keys(raw, ("topic", "section"), CONTEXT_OPTIONAL)
    result = {"topic": _slug(raw["topic"], 60), "section": _slug(raw["section"], 60)}
    if "type" in raw:
        kind = raw["type"]
        if kind is not None:
            _keys(kind, TYPE_ROUTING, ROUTING_OPTIONAL["type"])
            if kind["code"] is not None and (
                not isinstance(kind["code"], str) or not re.fullmatch(r"[A-Z0-9]{2,4}", kind["code"])
            ):
                raise ValueError("Invalid aircraft type routing")
            routed = {"code": kind["code"], "name": _line(kind["name"], 120), "slug": _slug(kind["slug"], 80)}
            if "same_as" in kind:
                # The type's reference article, which the site names as the story's subject.
                routed["same_as"] = public_url(kind["same_as"])
            kind = routed
        result["type"] = kind
    if "registration_slug" in raw:
        value = raw["registration_slug"]
        result["registration_slug"] = None if value is None else _slug(value, 40)
    if "operator" in raw:
        operator = raw["operator"]
        if operator is not None:
            _keys(operator, OPERATOR_ROUTING)
            if not isinstance(operator["basis"], str) or not re.fullmatch(
                r"[a-z][a-z_]{0,39}", operator["basis"]
            ):
                raise ValueError("Invalid operator basis")
            if operator["designator"] is not None and (
                not isinstance(operator["designator"], str)
                or not re.fullmatch(r"[A-Z0-9]{2,4}", operator["designator"])
            ):
                raise ValueError("Invalid operator designator")
            operator = {
                "name": _line(operator["name"], 120),
                "slug": _slug(operator["slug"], 80),
                "basis": operator["basis"],
                "designator": operator["designator"],
            }
        result["operator"] = operator
    for field, limit in (("place", 120), ("country", 80)):
        if field in raw:
            result[field] = None if raw[field] is None else _line(raw[field], limit)
            # A place names a town near the aircraft; digits could carry coordinates.
            if result[field] is not None and re.search(r"\d", result[field]):
                raise ValueError("A place or country name has no digits")
    return result


def _archived_labels(packet):
    """Whether callsign and code are archive labels carried between saved positions."""
    received = packet.get("received", {})
    provenance = received.get("label_provenance")
    if provenance is not None:
        if not isinstance(provenance, str) or provenance not in {"archived_label", "saved_report"}:
            raise ValueError("Unknown label provenance")
        return provenance == "archived_label"
    # Packets written before provenance was recorded: compact and trace archives
    # always kept archive altitude, which live reports never contain.
    readings = received.get("readings", [])
    samples = packet.get("surrounding_readings", [])
    return any(isinstance(item, dict) and item.get("field") == "archive_alt" for item in readings) or any(
        isinstance(item, dict) and "archive_alt" in item
        for item in (samples if isinstance(samples, list) else [])
    )


def _window(item, start, end, message):
    if start is not None and end is not None:
        item.update(start_utc=utc(start), end_utc=utc(end))
        if _instant(item["end_utc"]) < _instant(item["start_utc"]):
            raise ValueError(message)
    return item


def _seconds(value):
    """Whole seconds, or one decimal place, never exponent notation."""
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def _sequence_text(sequence):
    """One deterministic string for a status sequence, such as 7700 then 7600."""
    if episode_evidence.code_sequence(sequence) != sequence:
        raise ValueError("Invalid transponder code sequence")
    return ", then ".join(f"{step['code']} for {_seconds(step['seconds'])} s" for step in sequence)


def _measurements(packet, *, strict=True):
    measurements = []
    received = packet.get("received", {})
    if not isinstance(received, dict):
        raise ValueError("Invalid received readings")
    readings = received.get("readings", [])
    if not isinstance(readings, list) or len(readings) > 100:
        raise ValueError("Invalid received readings")
    at = received.get("saved_readings_at_utc")
    for reading in readings:
        if not isinstance(reading, dict):
            raise ValueError("Invalid reading")
        field = reading.get("field")
        if not isinstance(field, str) or field not in READINGS:
            continue
        label, unit = READINGS[field]
        value = reading.get("value")
        if value == "ground" and field in {"alt_baro", "alt_geom", "archive_alt"}:
            label, unit = "Air/ground report", ""
        else:
            value = _number(value)
        item = {
            "key": field,
            "label": label,
            "value": value,
            "unit": unit,
            "scope": "saved-report",
        }
        if at is not None:
            item["at_utc"] = utc(at)
        measurements.append(item)
    archived = _archived_labels(packet)
    for field, label in TEXT_REPORTS.items():
        value = received.get(field)
        # 0000 is an absent-code placeholder and "none" is no status; neither is a report.
        if value is None or (field, value) in (("squawk", "0000"), ("emergency_status", "none")):
            continue
        _text_report(field, value)
        if field == "squawk" and archived:
            continue
        item = {"key": field, "label": label, "value": value, "unit": "", "scope": "saved-report"}
        if at is not None:
            item["at_utc"] = utc(at)
        measurements.append(item)
    if archived:
        labels = {"callsign": packet.get("aircraft", {}).get("callsign"), "squawk": received.get("squawk")}
        for field, value in labels.items():
            if value is None or value == "0000":
                continue
            _label_report(field, value)
            item = {
                "key": field,
                "label": LABEL_REPORTS[field],
                "value": value,
                "unit": "",
                "scope": "archived-label",
            }
            # Never the position time: only the label record's own time, when it was kept.
            if received.get("label_at_utc") is not None:
                item["at_utc"] = utc(received["label_at_utc"])
            measurements.append(item)
    pattern = packet.get("pattern", {})
    if not isinstance(pattern, dict):
        raise ValueError("Invalid pattern measurements")
    for field, (label, unit) in PATTERN_MEASUREMENTS.items():
        if field not in pattern:
            continue
        item = {
            "key": field,
            "label": label,
            "value": _number(pattern[field]),
            "unit": unit,
            "scope": "analysis-window",
        }
        measurements.append(
            _window(
                item,
                pattern.get("start_utc"),
                pattern.get("end_utc"),
                "Analysis window ends before it starts",
            )
        )
    discontinuity = packet.get("position_discontinuity")
    episode = packet.get("maneuver", {})
    if not isinstance(episode, dict):
        raise ValueError("Invalid episode measurements")
    for field, (label, unit) in EPISODE_MEASUREMENTS.items():
        if field not in episode:
            continue
        item = {"key": field, "label": label, "value": _number(episode[field]), "unit": unit}
        if field in PEAK_MEASUREMENTS:
            item["scope"] = "peak-report"
            if episode.get(PEAK_MEASUREMENTS[field]) is not None:
                item["at_utc"] = utc(episode[PEAK_MEASUREMENTS[field]])
            measurements.append(item)
            continue
        if field in RULE_MEASUREMENTS:
            measurements.append({**item, "scope": "detection-rule"})
            continue
        prefix = (
            "sustained_"
            if field.startswith("sustained_")
            else "qualifying_"
            if field.startswith("qualifying_")
            else ""
        )
        item["scope"] = "analysis-window"
        start, end = episode.get(prefix + "start_utc"), episode.get(prefix + "end_utc")
        measurements.append(_window(item, start, end, "Episode ends before it starts"))
    winds = packet.get("winds_aloft") if packet.get("kind") == "winds_aloft_day" else None
    if winds is not None:
        if not isinstance(winds, dict):
            raise ValueError("Invalid winds-aloft measurements")
        for field, (label, unit) in WINDS_MEASUREMENTS.items():
            item = {"key": field, "label": label, "value": _number(winds.get(field)), "unit": unit}
            item["scope"] = "analysis-window"
            measurements.append(
                _window(
                    item, episode.get("start_utc"), episode.get("end_utc"), "Episode ends before it starts"
                )
            )
    for field, label in EPISODE_TEXT.items():
        if field not in episode:
            continue
        value = _sequence_text(episode[field]) if field == "code_sequence" else episode[field]
        _episode_text(field, value)
        item = {"key": field, "label": label, "value": value, "unit": "", "scope": "analysis-window"}
        measurements.append(
            _window(item, episode.get("start_utc"), episode.get("end_utc"), "Episode ends before it starts")
        )
    if discontinuity is not None:
        if not isinstance(discontinuity, dict):
            raise ValueError("Invalid position discontinuity")
        for field, (label, unit) in DISCONTINUITY_MEASUREMENTS.items():
            if field in discontinuity:
                measurements.append(
                    {
                        "key": field,
                        "label": label,
                        "value": _number(discontinuity[field]),
                        "unit": unit,
                        "scope": "discontinuity-pair-not-physical-travel",
                    }
                )
    return measurements + _neighbors(packet) + _rated_measurements(packet, strict=strict)


def _rated(packet):
    """A rated finding's comparison block, checked field by field, or None for another kind."""
    kind = packet.get("kind")
    if kind not in RATED_KINDS:
        return None
    block = packet.get("rated_limit")
    if not isinstance(block, dict) or block.get("field") != stories.RATED_FIELDS[kind]:
        raise ValueError("A rated finding needs its certified-limit comparison")
    if block.get("limit_name") not in stories.RATED_LIMIT_NAMES:
        raise ValueError("Unknown certified limit")
    speed = kind == "above_rated_speed"
    limit, mmo = block.get("limit"), block.get("mmo") if speed else None
    if any(value is not None and _number(value) <= 0 for value in (limit, mmo)):
        raise ValueError("A certified limit is positive")
    if limit is None and (mmo is None or not speed):
        raise ValueError("A rated finding needs its certified limit")
    sustained = block.get("sustained")
    if sustained is not None and not isinstance(sustained, dict):
        raise ValueError("Invalid sustained comparison")
    modelled = (sustained or {}).get("wind")
    if modelled is not None and (
        not isinstance(modelled, dict) or modelled.get("source") not in stories.WIND_SOURCES
    ):
        raise ValueError("Invalid modelled wind")
    # A speed's decisive figures are shown only with how its airspeed was obtained.
    if speed and sustained and sustained.get("airspeed_basis") not in limits.AIRSPEED_BASES:
        raise ValueError("Unknown airspeed basis")
    return block


def _rated_measurements(packet, *, strict=True):
    """The certified figures, the sustained comparison, the modelled wind, and the type check.

    Certified figures, the wind, and the registry are reference data with no time; the
    sustained figures share the window of the reports that were over the limit. Writes
    and exports require a readable block; a structural read leaves out one it cannot
    read, as _watchlist() does, so one changed packet cannot invalidate the whole ledger.
    """
    try:
        return _rated_rows(packet)
    except DATA_ERRORS:
        if strict:
            raise
        return []


def _rated_rows(packet):
    block = _rated(packet)
    if block is None:
        return []
    kind = packet["kind"]
    rows = []

    def add(row, value, scope, **window):
        rows.append(
            {"key": row.key, "label": row.label, "value": value, "unit": row.unit, "scope": scope, **window}
        )

    figures = {block["limit_name"]: block["limit"]}
    if kind == "above_rated_speed":
        figures["mmo"] = block.get("mmo")
    for row in RATED_LIMIT_ROWS:
        if figures.get(row.field) is not None:
            add(row, _number(figures[row.field]), "reference-data")
    sustained = block.get("sustained") or {}
    if sustained:
        window = {"start_utc": utc(sustained["start_utc"]), "end_utc": utc(sustained["end_utc"])}
        if _instant(window["end_utc"]) < _instant(window["start_utc"]):
            raise ValueError("The sustained comparison ends before it starts")
        basis = sustained.get("airspeed_basis")
        for row in RATED_WINDOW_ROWS:
            if kind in row.kinds and row.basis in {None, basis} and sustained.get(row.field) is not None:
                add(row, _number(sustained[row.field]), "analysis-window", **window)
    if modelled := sustained.get("wind"):
        for row in WIND_ROWS:
            add(row, _number(modelled[row.field]), "reference-data")
    check = packet.get("type_check")
    # Only an agreeing check is shown; the registry's own registration never is.
    if isinstance(check, dict) and check.get("state") == "agrees":
        if check.get("source") not in enrichment_rules.REGISTRIES:
            raise ValueError("Unknown type-check source")
        add(TYPE_CHECK_ROW, check["source"], "reference-data")
    return rows


def _reading_time(sample):
    seconds = sample.get("timestamp", sample.get("ts"))
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 1e9 <= seconds <= 1e10:
        return None
    return seconds


def _neighbors(packet):
    """The readings before and after an outlying value, and the range of the rest.

    The outlier is the saved reading at the saved time with the reported value.
    Without it, or without other readings, there are no neighbor measurements.
    """
    field = NEIGHBOR_FIELDS.get(packet.get("kind"))
    received = packet.get("received", {})
    value = packet.get("observation", {}).get("metric_value")
    samples = packet.get("surrounding_readings")
    if not field or not isinstance(samples, list) or received.get("saved_readings_at_utc") is None:
        return []
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return []
    peak = _instant(received["saved_readings_at_utc"]).timestamp()
    readings = []
    for sample in samples:
        seconds = _reading_time(sample) if isinstance(sample, dict) else None
        reading = sample.get(field) if seconds is not None else None
        if finite(reading):
            readings.append((seconds, reading))
    readings.sort()
    outlier = next(
        (
            index
            for index, (seconds, reading) in enumerate(readings)
            if abs(seconds - peak) < 0.001 and math.isclose(reading, value, rel_tol=0, abs_tol=1e-6)
        ),
        None,
    )
    if outlier is None:
        return []
    others = readings[:outlier] + readings[outlier + 1 :]
    if not others:
        return []
    unit = READINGS[field][1]

    items = []
    before = [item for item in others if item[0] < peak]
    after = [item for item in others if item[0] > peak]
    for key, chosen in (("previous_report", before[-1:]), ("next_report", after[:1])):
        if chosen:
            items.append(
                {
                    "key": key,
                    "label": NEIGHBOR_MEASUREMENTS[key],
                    "value": chosen[0][1],
                    "unit": unit,
                    "scope": "surrounding-reports",
                    "at_utc": epoch_utc(chosen[0][0]),
                }
            )
    window = {"start_utc": epoch_utc(others[0][0]), "end_utc": epoch_utc(others[-1][0])}
    values = [reading for _, reading in others]
    for key, amount, amount_unit in (
        ("lowest_surrounding_report", min(values), unit),
        ("highest_surrounding_report", max(values), unit),
        ("surrounding_report_count", len(values), "reports"),
    ):
        items.append(
            {
                "key": key,
                "label": NEIGHBOR_MEASUREMENTS[key],
                "value": amount,
                "unit": amount_unit,
                "scope": "surrounding-reports",
                **window,
            }
        )
    return items


def _text_report(field, value):
    if (
        not isinstance(value, str)
        or (field == "squawk" and episode_evidence.octal_code(value) != value)
        or (field == "emergency_status" and value not in EMERGENCY_STATUSES)
    ):
        raise ValueError("Invalid transponder report")


def _label_report(field, value):
    if not isinstance(value, str) or not (
        episode_evidence.octal_code(value) == value
        if field == "squawk"
        else re.fullmatch(r"[A-Z0-9]{2,8}", value)
    ):
        raise ValueError("Invalid archived label")


STEP = re.compile(
    r"(?P<code>[0-7]{4}|" + "|".join(sorted(maneuvers.STATUS_NAMES)) + r") for (?P<seconds>\d+(?:\.\d)?) s"
)


def _sequence_valid(value):
    steps = value.split(", then ")
    matches = [STEP.fullmatch(step) for step in steps]
    return (
        1 <= len(steps) <= 16
        and all(matches)
        and all(match["code"] != "0000" and float(match["seconds"]) <= 86400 for match in matches)
    )


def _episode_text(field, value):
    if not isinstance(value, str) or not (
        (field == "code" and episode_evidence.octal_code(value) == value)
        or (field == "advisory" and episode_evidence.advisory_text(value) == value)
        or (field == "advisory_complement" and episode_evidence.advisory_complement(value) == value)
        or (field == "code_sequence" and _sequence_valid(value))
    ):
        raise ValueError("Invalid episode text report")


def _tags(packet):
    """Allowlisted context tags, including ones the packet's own facts imply."""
    episode = packet.get("maneuver", {})
    tags = set(episode_evidence.tags(packet)) | set(episode_evidence.tags(episode))
    if _archived_labels(packet) and (
        packet.get("aircraft", {}).get("callsign")
        or packet.get("received", {}).get("squawk") not in (None, "0000")
    ):
        tags.add("archive_carried_label")
    if packet.get("received", {}).get("source") == "mlat" and packet.get("kind") in MLAT_ESTIMATE_KINDS:
        tags.add("mlat_solver_estimate")
    return sorted(tags)


def _context(packet):
    source = packet.get("received", {}).get("source", "unknown")
    if not isinstance(source, str) or source not in POSITION_SOURCES:
        raise ValueError("Unknown received source")
    kind = packet.get("kind", "")
    limitations = []
    if any(packet.get("aircraft", {}).get(key) for key in ("registration", "type_code", "type_description")):
        limitations.append(
            "Aircraft metadata is a current database match; historical assignment is not independently verified."
        )
    checks = packet.get("identity_checks")
    if checks is not None and not isinstance(checks, dict):
        raise ValueError("Invalid identity checks")
    if (checks or {}).get("conflict") is True:
        limitations.append(IDENTITY_CONFLICT)
    if source == "mlat":
        limitations.append(
            "MLAT estimates position from signal timing across receivers. Derived position and speed may not provide independent corroboration."
        )
    elif source == "unknown":
        limitations.append(
            "The position source is unspecified; direct ADS-B reception has not been established."
        )
    pattern = packet.get("pattern", {})
    episode = packet.get("maneuver", {})
    for flag in episode_evidence.flags(packet):
        limitations.append(episode_evidence.LIMITATIONS[flag])
    if episode:
        allowed = set(episode_evidence.LIMITATIONS.values()) | {
            episode_evidence.FIELD_AGE_LIMITATION,
            episode_evidence.UNCONFIRMED_LIMITATION,
        }
        limitations.extend(
            current_text(value)
            for value in episode.get("limitations", [])
            if isinstance(value, str) and current_text(value) in allowed
        )
        if episode.get("altitude_reference") in {"alt_baro", "alt_geom"}:
            limitations.append(
                "Altitude differences use "
                + (
                    "pressure altitude"
                    if episode["altitude_reference"] == "alt_baro"
                    else "geometric altitude"
                )
                + "; they do not measure height above terrain."
            )
        elif episode.get("altitude_reference") == "archive_alt":
            limitations.append(
                "Altitude differences use archived altitude whose reference was not retained; they do not "
                "measure height above terrain."
            )
    if pattern.get("quality_review_required") is True:
        limitations.append(
            "Source or geometry quality requires review; the exact path or turn count may be unreliable."
        )
    if pattern.get("shape_verified") is False:
        limitations.append("The reported movement shape has not been verified.")
    if pattern:
        limitations.append(MISSION)
    limitations.extend(BASIS_LIMITATIONS[claim_basis(kind)])
    if kind == "position_jump":
        limitations.append(
            "An implausible position-derived value is a data-quality finding, not a measured aircraft capability."
        )
    elif kind in {"outlier_gs", "type_speed_mismatch"}:
        limitations.append(
            "An outlying report alone does not establish an aircraft's performance or the cause of a data error."
        )
    limitations.extend(KIND_LIMITATIONS.get(kind, ()))
    rated = packet.get("rated_limit") if kind == "above_rated_speed" else None
    sustained = rated.get("sustained") if isinstance(rated, dict) else None
    if isinstance(sustained, dict) and isinstance(sustained.get("wind"), dict):
        # The evidence card shows the modelled wind, so it carries the licence's attribution.
        limitations.append(WIND_ATTRIBUTION)
    winds = packet.get("winds_aloft") if kind == "winds_aloft_day" else None
    if isinstance(winds, dict) and isinstance(winds.get("wind_250_hpa"), dict):
        # The story may state the modelled 250 hPa wind, so it carries the attribution too.
        limitations.append(WIND_ATTRIBUTION)
    tags = _tags(packet)
    limitations.extend(episode_evidence.CONTEXT_NOTES[tag] for tag in tags)
    # An archive-shape note already states the freshness limit; one sentence is enough.
    if _archived_labels(packet) and "archive_field_freshness_unknown" not in tags:
        limitations.append(ARCHIVE_FRESHNESS)
    rates = {
        key
        for key, (_label, unit) in EPISODE_MEASUREMENTS.items()
        if unit == "ft/min" and _window_metric(key)
    }
    rates |= set(PEAK_MEASUREMENTS)
    if kind in RATE_KINDS or rates & set(episode):
        limitations.append(SIGN_NOTE)
    if packet.get("watchlist") is not None:
        limitations.append(WATCHLIST_SCOPE)
    return source, list(dict.fromkeys(limitations))


def claim_basis(kind):
    """Telemetry, track geometry, a transmitted status, database metadata or a community list."""
    if kind.startswith(rules.SQUAWK_PREFIXES) or kind == "special_use_code":
        return "status"
    if kind == "watchlist_match":
        return "community-list"
    if KIND_TYPES.get(kind) == "flight-patterns":
        return "geometry"
    return "database" if kind in DATABASE_KINDS else "telemetry"


def _participants(packet, primary):
    raw = packet.get("participants", [])
    if not isinstance(raw, list) or len(raw) > 12:
        raise ValueError("Invalid participants")
    result, addresses = [], set()
    for item in raw:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("role"), str)
            or item["role"] not in PARTICIPANT_ROLES
        ):
            raise ValueError("Invalid participant")
        identity = _identity(
            {
                key: item[key]
                for key in ("icao_address", "registration", "callsign", "type_code", "type_description")
                if item.get(key) is not None
            }
        )
        address = identity.get("icao_address")
        if primary and address == primary:
            continue
        if address in addresses:
            raise ValueError("Duplicate participant")
        if address:
            addresses.add(address)
        source = item.get("source", "unknown")
        if not isinstance(source, str) or source not in POSITION_SOURCES:
            raise ValueError("Unknown participant source")
        raw_measurements = item.get("measurements", {})
        if not isinstance(raw_measurements, dict):
            raise ValueError("Invalid participant measurements")
        measurements = [
            {"key": key, "label": label, "value": _number(raw_measurements[key]), "unit": unit}
            for key, (label, unit) in PARTICIPANT_MEASUREMENTS.items()
            if key in raw_measurements
        ]
        result.append({"role": item["role"], **identity, "source": source, "measurements": measurements})
    return result


def _watchlist_block(entry):
    """The one public form of a Plane Alert DB entry, or None for another category or commit.

    The entry names who or what the list associates with the aircraft and why the list
    carries it (its category and tags), always beside the attribution and claim scope.
    """
    category, commit, association = (
        entry.get("category"),
        entry.get("source_commit"),
        entry.get("association"),
    )
    tags = entry.get("tags") or []
    list_category = entry.get("list_category")
    try:
        if (
            not isinstance(category, str)
            or category not in WATCHLIST_CATEGORIES
            or not isinstance(commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", commit)
            or not isinstance(tags, list)
            or len(tags) > watchlists.MAX_TAGS
        ):
            return None
        block = {
            "source_name": WATCHLIST_SOURCE,
            "association": _line(association, watchlists.TEXT_LIMITS["association"]),
            "category": category,
            "source_commit": commit,
            "attribution": WATCHLIST_ATTRIBUTION,
            "licenses": list(WATCHLIST_LICENSES),
            "claim_scope": WATCHLIST_CLAIM_SCOPE,
        }
        if list_category is not None:
            block["list_category"] = _line(list_category, watchlists.TEXT_LIMITS["list_category"])
        if tags:
            block["tags"] = [_line(tag, watchlists.TEXT_LIMITS["tag"]) for tag in tags]
        if entry.get("group") is not None:
            if entry["group"] not in watchlists.GROUPS.values():
                return None
            block["group"] = entry["group"]
        if entry.get("description") is not None:
            block["description"] = _line(entry["description"], watchlists.TEXT_LIMITS["description"])
        if entry.get("link") is not None:
            # A link that is no public HTTPS page is left out, as story_facts leaves it out;
            # it never withholds the entry itself.
            with suppress(ValueError):
                block["link"] = public_url(entry["link"])
    except (TypeError, ValueError):
        return None
    if not block["association"]:
        return None
    return block


def _watchlist(packet, *, strict=True):
    """The attributed Plane Alert DB entry: its association, category and tags.

    Writes and exports pin every field. A structural read drops a block saved under
    older wording, so a wording change cannot invalidate the whole ledger.
    """
    match = packet.get("watchlist")
    if match is None:
        return None
    block = (
        _watchlist_block({**match, "association": match.get("association_label")})
        if isinstance(match, dict)
        else None
    )
    licenses = match.get("licenses") if isinstance(match, dict) else None
    if (
        block is None
        or any(
            current_text(match.get(key)) != block[key]
            for key in ("source_name", "attribution", "claim_scope")
        )
        or not isinstance(licenses, list)
        or [item.get("name") if isinstance(item, dict) else None for item in licenses] != WATCHLIST_LICENSES
    ):
        if strict:
            raise ValueError("Invalid watchlist association")
        return None
    return block


def project(packet, editorial, *, strict=True):
    """The public event an evidence packet and its editorial metadata project to.

    Writes, approvals and exports are strict: they require the weekly dossier's
    binding and a readable watchlist and certified-limit block. Structural reads
    (strict=False) leave out a block they cannot read and still allow a stale
    weekly dossier to be withdrawn, so one changed packet cannot invalidate the
    whole ledger.
    """
    if not isinstance(packet, dict) or not isinstance(editorial, dict):
        raise ValueError("Evidence and editorial metadata must be objects")
    if type(packet.get("schema_version")) is not int or packet["schema_version"] != VERSION:
        raise ValueError("Unsupported evidence packet version")
    kind = packet.get("kind")
    if not isinstance(kind, str) or kind not in KIND_TYPES:
        raise ValueError("Event kind has no public display policy")
    primary = editorial.get("primary_type") or KIND_TYPES[kind]
    if not isinstance(primary, str) or primary not in TYPE_LABELS:
        raise ValueError("Unknown primary event type")
    observation = packet.get("observation", {})
    if not isinstance(observation, dict):
        raise ValueError("Invalid observation interval")
    start, end = utc(observation.get("start_utc")), utc(observation.get("end_utc"))
    if _instant(end) < _instant(start):
        raise ValueError("Observation ends before it starts")
    secondary = editorial.get("secondary_labels", [])
    if not isinstance(secondary, list) or len(secondary) > 12:
        raise ValueError("Invalid secondary labels")
    synthetic = editorial.get("synthetic", False)
    if not isinstance(synthetic, bool):
        raise ValueError("Synthetic flag must be boolean")
    result = {
        "observed_start": start,
        "observed_end": end,
        "kind": kind,
        "primary_type": primary,
        "secondary_labels": sorted({_text(label, 60) for label in secondary}),
        "title": _text(editorial.get("title"), 160),
        "summary": _text(editorial.get("summary"), 1000),
        "article_path": _article_path(editorial.get("article_path")),
        "aircraft": _identity(packet.get("aircraft", {})),
        "measurements": _measurements(packet, strict=strict),
        "references": _references(packet.get("links", [])),
        "synthetic": synthetic,
    }
    result["source"], result["limitations"] = _context(packet)
    if len(result["limitations"]) > MAX_LIMITATIONS:
        raise ValueError("Too many public limitations")
    if watchlist := _watchlist(packet, strict=strict):
        result["watchlist"] = watchlist
    if participants := _participants(packet, result["aircraft"].get("icao_address")):
        result["participants"] = participants
    publisher_priority = editorial.get("priority")
    if publisher_priority is not None and publisher_priority not in PRIORITIES:
        raise ValueError("Unknown publication priority")
    priority = priority_evidence(kind, observation.get("metric_value"), packet.get("maneuver", {}))
    # Exception-grade evidence the publisher treated as ordinary is not presented as an exception.
    if priority and publisher_priority != "ordinary":
        result["priority_evidence"] = priority
    if editorial.get("media") is not None:
        media = editorial["media"]
        if not isinstance(media, dict):
            raise ValueError("Invalid public media")
        result["media"] = {"src": public_path(media.get("src")), "alt": _text(media.get("alt"), 300)}
    if editorial.get("ghost_id") is not None:
        ghost_post_id(editorial["ghost_id"])
    result["evidence_version"] = evidence_version(result)
    if editorial.get("story") is not None:
        story = _story(editorial["story"])
        # Search, cards, Ghost and the weekly partials read the title and summary.
        if result["title"] != story["headline"] or result["summary"] != story["dek"]:
            raise ValueError("Title and summary must be the story headline and dek")
        result["story"] = story
    if editorial.get("context") is not None:
        # story_facts.context() returns exactly the public routing; any other key is refused.
        result["context"] = _routing(editorial["context"])
    if editorial.get("weekly_spotlight") is not None:
        from .weekly_spotlight import check_source, validate_research

        check_source(editorial.get("weekly_source"), result, require_interest=strict)
        research = editorial["weekly_spotlight"]
        # Structural reads must allow a stale dossier to be removed or withdrawn.
        # New writes, approvals and exports still require the current binding.
        binding = (
            result
            if strict
            else {
                **result,
                "evidence_version": research.get("evidence_version") if isinstance(research, dict) else None,
            }
        )
        result["weekly_spotlight"] = validate_research(research, binding)
    return result


_projection = project


def new_ledger(available_from):
    """The boundary describes known publication history, never receiver uptime."""
    return {"schema_version": VERSION, "available_from": _date(available_from), "records": []}


def _last_operation(record):
    created = _instant(record.get("created_at"))
    updated = _instant(record.get("updated_at"))
    reviewed = record.get("reviewed_at")
    if (record.get("state") == "pending") != (reviewed is None):
        raise ValueError("Review timestamp does not match the review state")
    if updated < created:
        raise ValueError("A revision cannot precede record creation")
    if reviewed is not None:
        reviewed = _instant(reviewed)
        if reviewed < updated:
            raise ValueError("Review cannot precede the current revision")
        return reviewed
    return updated


def _validate_header(ledger):
    if (
        not isinstance(ledger, dict)
        or type(ledger.get("schema_version")) is not int
        or ledger["schema_version"] != VERSION
    ):
        raise ValueError("Unsupported ledger version")
    _date(ledger.get("available_from"))
    if not isinstance(ledger.get("records"), list):
        raise ValueError("Invalid ledger records")


def validate_record(record):
    """One record's identity, review state, revision and projection; returns its event page.

    The uniqueness of IDs, episode keys and pages is a ledger-wide check. Structural
    validation must still allow old dossiers to be revised or withdrawn after a policy
    change, so the weekly interest rule applies only to new writes, approvals and exports.
    """
    if not isinstance(record, dict):
        raise ValueError("Invalid ledger record")
    identifier = record.get("id")
    try:
        valid_id = str(uuid.UUID(identifier)) == identifier
    except (ValueError, AttributeError, TypeError):
        valid_id = False
    if not valid_id:
        raise ValueError("Invalid or duplicate event ID")
    episode = record.get("episode_key")
    if not isinstance(episode, str) or not 1 <= len(episode) <= 300:
        raise ValueError("Invalid or duplicate episode key")
    if not isinstance(record.get("state"), str) or record["state"] not in {
        "pending",
        "approved",
        "withdrawn",
    }:
        raise ValueError("Invalid review state")
    _last_operation(record)
    if (
        not isinstance(record.get("revision"), int)
        or isinstance(record["revision"], bool)
        or record["revision"] < 1
    ):
        raise ValueError("Invalid revision")
    return _projection(record.get("evidence"), record.get("editorial"), strict=False)["article_path"]


def broken_records(ledger):
    """IDs of the records that do not validate on their own; the others still write and export."""
    broken = set()
    for record in ledger.get("records", []):
        try:
            validate_record(record)
        except DATA_ERRORS:
            broken.add(record.get("id") if isinstance(record, dict) else None)
    return broken


def _validate_ledger(ledger):
    _validate_header(ledger)
    ids, episodes, paths = set(), set(), set()
    for record in ledger["records"]:
        path = validate_record(record)
        if record["id"] in ids:
            raise ValueError("Invalid or duplicate event ID")
        if record["episode_key"] in episodes:
            raise ValueError("Invalid or duplicate episode key")
        if path in paths:
            raise ValueError("Two events cannot share one event page")
        ids.add(record["id"])
        episodes.add(record["episode_key"])
        paths.add(path)
    return ledger


def find_record(ledger, event_id):
    """The ledger record with this public event ID, or None."""
    return next(
        (row for row in ledger["records"] if isinstance(row, dict) and row.get("id") == event_id), None
    )


def put_event(ledger, episode_key, evidence, editorial, *, at=None):
    """Create or revise one episode; revisions require a new explicit approval.

    Only this episode's record is validated, with the ledger-wide uniqueness of IDs,
    episode keys and event pages, so another record that no longer validates never
    blocks the write. A revision updates the record in place and keeps its other
    fields, such as the publisher's automation block, so every holder sees it.
    """
    _validate_header(ledger)
    _projection(evidence, editorial)
    if not isinstance(episode_key, str) or not 1 <= len(episode_key) <= 300:
        raise ValueError("A stable private episode key is required")
    at = stamp(at)
    records = ledger["records"]
    existing = next(
        (row for row in records if isinstance(row, dict) and row.get("episode_key") == episode_key), None
    )
    if existing is not None:
        if _instant(at) < _last_operation(existing):
            raise ValueError("A revision cannot precede the previous operation")
        history = existing.get("history", [])
        if not isinstance(history, list):
            raise ValueError("Invalid public revision history")
        previous = {key: deepcopy(value) for key, value in existing.items() if key != "history"}
        record = {**existing, "history": [*history, previous], "revision": existing["revision"] + 1}
    else:
        record = {
            "id": str(uuid.uuid4()),
            "episode_key": episode_key,
            "created_at": at,
            "revision": 1,
            "history": [],
        }
    record.update(
        evidence=deepcopy(evidence),
        editorial=deepcopy(editorial),
        updated_at=at,
        state="pending",
        reviewed_at=None,
        approved_content_hash=None,
    )
    path = validate_record(record)
    for other in records:
        if other is existing or not isinstance(other, dict):
            continue
        if other.get("id") == record["id"]:
            raise ValueError("Invalid or duplicate event ID")
        if other.get("episode_key") == episode_key:
            raise ValueError("Invalid or duplicate episode key")
        if isinstance(other.get("editorial"), dict) and other["editorial"].get("article_path") == path:
            raise ValueError("Two events cannot share one event page")
    if existing is None:
        records.append(record)
    else:
        existing.clear()
        existing.update(record)
    return record["id"]


def set_review_state(ledger, event_id, state, *, at=None):
    """Approve or withdraw one record's current revision; only that record is validated."""
    _validate_header(ledger)
    if not isinstance(state, str) or state not in {"approved", "withdrawn"}:
        raise ValueError("Use approved or withdrawn")
    record = find_record(ledger, event_id)
    if record is None:
        raise ValueError("Public event not found")
    validate_record(record)
    reviewed = stamp(at)
    if _instant(reviewed) < _last_operation(record):
        raise ValueError("Review cannot precede the previous operation")
    if state == "approved":
        _projection(record["evidence"], record["editorial"])
    content_hash = (
        _digest({"evidence": record["evidence"], "editorial": record["editorial"]})
        if state == "approved"
        else None
    )
    record.update(state=state, reviewed_at=reviewed, approved_content_hash=content_hash)


def _published_inventory(inventory, as_of):
    if (
        not isinstance(inventory, dict)
        or inventory.get("complete") is not True
        or not isinstance(inventory.get("posts"), list)
        or type(inventory.get("total")) is not int
        or inventory["total"] != len(inventory["posts"])
    ):
        raise ValueError("A complete Ghost inventory with a matching total is required")
    age = (_instant(as_of) - _instant(inventory.get("as_of_utc"))).total_seconds()
    if not 0 <= age <= MAX_INVENTORY_AGE_SECONDS:
        raise ValueError("Ghost inventory is stale or from the future")
    entries, identifiers = {}, set()
    for post in inventory["posts"]:
        if not isinstance(post, dict):
            raise ValueError("Invalid Ghost inventory entry")
        slug = post.get("slug")
        if not isinstance(slug, str) or not GHOST_SLUG.fullmatch(slug):
            raise ValueError("Ghost inventory entry needs a slug")
        identifier = ghost_post_id(post.get("id"))
        if slug in entries or identifier in identifiers:
            raise ValueError("Duplicate Ghost inventory slug or ID")
        identifiers.add(identifier)
        if post.get("status") not in {"published", "draft", "scheduled", "sent"} or post.get(
            "visibility"
        ) not in {"public", "members", "paid", "tiers"}:
            raise ValueError("Ghost publication status and visibility are required")
        entries[slug] = None
        if post["status"] != "published" or post["visibility"] != "public":
            continue
        published, revised = utc(post.get("published_at")), utc(post.get("updated_at"))
        if max(_instant(published), _instant(revised)) > _instant(inventory["as_of_utc"]):
            raise ValueError("Ghost publication timestamps exceed inventory time")
        entries[slug] = {
            "ghost_id": identifier,
            "published_at": published,
            "revised_at": revised,
            "html": post.get("html") if isinstance(post.get("html"), str) else "",
            "tags": post.get("tags", []),
        }
    return entries


def _clamped(value, earliest, latest):
    """A saved time held between two bounds, or None when it is missing or unreadable.

    A correction can move a story's observation end past its first release; the
    release then counts from the new end, never from the observation start.
    """
    try:
        value = utc(value)
    except ValueError:
        return None
    return max(earliest, min(value, latest, key=_instant), key=_instant)


def _release_time(record, event, as_of):
    """The first release, between the end of the observation and the snapshot."""
    return _clamped((record.get("automation") or {}).get("released_at"), event["observed_end"], as_of)


def _content_revision_time(record, released, as_of):
    """The last release of new prose or evidence, between the first release and the snapshot."""
    return _clamped((record.get("automation") or {}).get("content_revised_at"), released, as_of)


def export_snapshot(ledger, *, mode, as_of=None, inventory=None):
    """One full-inventory snapshot drives lists, daily counts and search together."""
    _validate_ledger(ledger)
    if not isinstance(mode, str) or mode not in {"published", "review"}:
        raise ValueError("Export mode must be published or review")
    as_of = utc(as_of or now_utc())
    published = _published_inventory(inventory, as_of) if mode == "published" else {}
    events, retired = [], []
    for record in ledger["records"]:
        if record["state"] == "withdrawn":
            if mode == "published" and (entry := _retired_entry(record, as_of)):
                retired.append(entry)
            continue
        current = record
        if mode == "published":
            approved = _approved_revision(record)
            if approved is None:
                continue
            record = approved
            if record["editorial"].get("synthetic", False):
                continue
            # Suspended or replaced posts cannot appear in this export. Check
            # Ghost first so stale weekly research cannot block their removal.
            publication = published.get(article_slug(record["editorial"]["article_path"]))
            if publication is None:
                continue
            expected_id = ghost_post_id(record["editorial"].get("ghost_id"))
            if publication["ghost_id"] != expected_id:
                continue
        try:
            event = _projection(record["evidence"], record["editorial"])
        except ValueError as error:
            if not BINDING_ERRORS.search(str(error)):
                raise
            raise ValueError(f"{error}; after a change in public evidence, {BINDING_REPAIR}") from error
        if mode == "published":
            if "weekly_spotlight" in event:
                from .weekly_spotlight import published_research_matches

                researched = {**event, "id": record["id"], "revision": record["revision"]}
                if not published_research_matches(researched, publication):
                    event.pop("weekly_spotlight")
            publication = {key: publication[key] for key in ("published_at", "revised_at")}
            # The release times live with the current record, not the approved revision.
            if released := _release_time(current, event, as_of):
                publication["released_at"] = released
                if revised := _content_revision_time(current, released, as_of):
                    publication["content_revised_at"] = revised
        else:
            publication = {"published_at": None, "revised_at": utc(record["updated_at"])}
        if _instant(event["observed_end"]) > _instant(as_of):
            raise ValueError("Observation is later than snapshot time")
        events.append(
            {
                **event,
                "id": record["id"],
                "revision": record["revision"],
                **publication,
                "publication_status": "published" if mode == "published" else "unpublished",
                "headline_measurements": list(stories.HEADLINE_MEASUREMENTS[event["kind"]]),
            }
        )
    # Stable UUID tie-breaks do not change when prose or publication times change.
    events.sort(key=lambda item: (-_instant(item["observed_start"]).timestamp(), item["id"]))
    days = _daily_counts(events, ledger["available_from"], as_of)
    snapshot = {
        "schema_version": VERSION,
        "mode": mode,
        "as_of_utc": as_of,
        "publication_status": "published" if mode == "published" else "unpublished",
        "available_from": ledger["available_from"],
        "window": {"start": days[0]["date"], "end": days[-1]["date"], "timezone": "UTC"},
        "types": [{"id": key, "label": label} for key, label in EVENT_TYPES],
        "events": events,
        "days": days,
        "search": _search(events),
        "retired": _retired_list(retired),
    }
    snapshot["snapshot_id"] = _digest(snapshot)
    return validate_snapshot(snapshot)


def _was_released(record):
    """Whether a story went out publicly: the publisher recorded its release on the
    record, or on an approved revision in its history."""

    def released(revision):
        automatic = revision.get("automation") if isinstance(revision, dict) else None
        if not isinstance(automatic, dict):
            return False
        try:
            utc(automatic.get("released_at"))
        except ValueError:
            return False
        return True

    history = record.get("history", [])
    return released(record) or (
        isinstance(history, list)
        and any(
            isinstance(item, dict) and item.get("state") == "approved" and released(item) for item in history
        )
    )


def _retired_entry(record, as_of):
    """A withdrawn story's public path, the slug of its type hub and the withdrawal time.

    Only a story that was released has a public path to retire. Nothing else
    from the record leaves it: no evidence, identity, prose or private ID.
    """
    editorial = record["editorial"]
    if editorial.get("synthetic", False) or not _was_released(record):
        return None
    context = editorial.get("context")
    kind = _routing(context).get("type") if context is not None else None
    return {
        "path": _article_path(editorial.get("article_path")),
        "type": kind["slug"] if kind else None,
        "withdrawn_at": min(utc(record["reviewed_at"]), as_of, key=_instant),
    }


def _retired_list(entries):
    """The MAX_RETIRED most recent withdrawals, sorted by path."""
    latest = sorted(entries, key=lambda entry: (-_instant(entry["withdrawn_at"]).timestamp(), entry["path"]))
    return sorted(latest[:MAX_RETIRED], key=lambda entry: entry["path"])


def _snapshot_retired(retired, mode, as_of):
    """Withdrawn stories' paths, each with a type hub slug or null and a withdrawal time; nothing else.

    A review snapshot has none. A path a live event also holds stays the event's;
    the site build reports it.
    """
    if not isinstance(retired, list) or len(retired) > MAX_RETIRED or (mode == "review" and retired):
        raise ValueError("Invalid list of withdrawn stories")
    for entry in retired:
        _keys(entry, ("path", "type", "withdrawn_at"))
        _article_path(entry["path"])
        if entry["type"] is not None:
            _slug(entry["type"], 80)
        if not _stamped_by(entry["withdrawn_at"], as_of):
            raise ValueError("A withdrawal time falls after the snapshot")
    paths = [entry["path"] for entry in retired]
    if paths != sorted(set(paths)):
        raise ValueError("Withdrawn stories must be unique and sorted by path")


def _approved_revision(record):
    """The latest approved revision, unless a withdrawal came after it; None without one."""
    if record["state"] == "withdrawn":
        return None
    history = record.get("history", [])
    if not isinstance(history, list):
        raise ValueError("Invalid public revision history")
    for candidate in (record, *reversed(history)):
        if not isinstance(candidate, dict):
            raise ValueError("Invalid public revision history")
        # A withdrawn episode stays withdrawn if later put back into pending
        # review. Only a subsequent explicit approval can restore publication.
        if candidate.get("state") == "withdrawn":
            return None
        if candidate.get("state") != "approved":
            continue
        validate_record(candidate)
        if candidate["id"] != record["id"] or candidate["episode_key"] != record["episode_key"]:
            raise ValueError("Revision belongs to another episode")
        if candidate.get("approved_content_hash") != _digest(
            {"evidence": candidate["evidence"], "editorial": candidate["editorial"]}
        ):
            raise ValueError("Approved content changed without review")
        return candidate
    return None


LABEL_FIELDS = ("label_provenance", "label_at_utc", "label_caveat")
# Errors that a change in evidence wording or code causes in a saved record, and the order
# that repairs them: rebind the saved dossiers and maps, then regenerate the packets.
BINDING_ERRORS = re.compile(
    r"prepared for different observation evidence|does not match the reviewed public evidence"
    r"|Invalid watchlist association"
)
BINDING_REPAIR = "run tools/export_public_events.py migrate-evidence --apply, then tools/run_autopilot.py --reproject --apply"


def _binding_identity(event):
    """What a dossier or map excerpt describes; card wording can change around it."""
    aircraft = event.get("aircraft") if isinstance(event.get("aircraft"), dict) else {}
    return (
        event.get("kind"),
        event.get("observed_start"),
        event.get("observed_end"),
        *(aircraft.get(key) for key in ("icao_address", "registration", "type_code")),
    )


MIGRATION_ERRORS = (*DATA_ERRORS, OSError)


def _with_labels(packet, source):
    """A packet saved before label provenance existed, given the provenance its finding implies.

    Returned only when that alone makes the saved packet project like the packet
    publish() regenerates from the same finding; None when nothing should change.
    """
    received = packet.get("received")
    if not isinstance(received, dict) or "label_provenance" in received or not isinstance(source, dict):
        return None
    try:
        evidence = source["evidence"]
        interval = tuple(_instant(packet["observation"][key]).timestamp() for key in ("start_utc", "end_utc"))
        finding = {**source, "evidence": {**evidence, "watchlist": evidence.get("watchlist") or {}}}
        regenerated = stories.evidence_packet(finding, include_track=False, observation_interval=interval)
        target = _projection(regenerated, PLACEHOLDER_EDITORIAL)["evidence_version"]
        if _projection(packet, PLACEHOLDER_EDITORIAL, strict=False)["evidence_version"] == target:
            return None
        upgraded = deepcopy(packet)
        upgraded["received"].update(
            {key: regenerated["received"][key] for key in LABEL_FIELDS if key in regenerated["received"]}
        )
        projected = _projection(upgraded, PLACEHOLDER_EDITORIAL, strict=False)
    except MIGRATION_ERRORS:
        # A finding that no longer regenerates leaves its saved packet as it was.
        return None
    return upgraded if projected["evidence_version"] == target else None


MIGRATION_COUNTS = ("packets", "research", "media", "approvals")


def _migrate_revision(revision, source, receipt):
    """One revision's content with its bindings moved, its projection and the counts changed.

    A weekly revision is checked against its own saved finding, as exports check it.
    Raises when a binding cannot be moved safely; the caller then keeps the record.
    """
    content = {"evidence": deepcopy(revision["evidence"]), "editorial": deepcopy(revision["editorial"])}
    signed = revision.get("approved_content_hash") == _digest(content)
    counts = dict.fromkeys(MIGRATION_COUNTS, 0)
    finding = content["editorial"].get("weekly_source") or source
    if finding and (upgraded := _with_labels(content["evidence"], finding)) is not None:
        content["evidence"] = upgraded
        counts["packets"] += 1
    current = _projection(content["evidence"], content["editorial"], strict=False)
    research = content["editorial"].get("weekly_spotlight")
    old = receipt.get("evidence_version") if receipt else None
    if (
        isinstance(research, dict)
        and old
        and research.get("evidence_version") == old != current["evidence_version"]
    ):
        if _binding_identity(receipt) != _binding_identity(current):
            raise ValueError("observation_changed")
        research["evidence_version"] = current["evidence_version"]
        counts["research"] += 1
    if any(counts.values()) and revision.get("state") == "approved":
        if not signed:
            raise ValueError("approval_hash_mismatch")
        content["approved_content_hash"] = _digest({key: content[key] for key in ("evidence", "editorial")})
        counts["approvals"] += 1
    return content, current, counts


def _unproven_bindings(record):
    """Weekly dossiers that still name another evidence version, by revision number."""
    found = []
    for revision in (record, *record.get("history", [])):
        research = (
            revision.get("editorial", {}).get("weekly_spotlight") if isinstance(revision, dict) else None
        )
        if not isinstance(research, dict):
            continue
        try:
            current = _projection(revision["evidence"], revision["editorial"], strict=False)
        except MIGRATION_ERRORS as error:
            found.append(f"revision {revision.get('revision')}: {_reason(error)}")
            continue
        if research.get("evidence_version") != current["evidence_version"]:
            found.append(f"revision {revision.get('revision')}: weekly_binding_unproven")
    return found


def _reason(error):
    return (str(error) if isinstance(error, ValueError) else type(error).__name__)[:120]


def migrate_evidence_bindings(ledger, *, at=None):
    """Carry saved bindings across a change in public card wording; returns a report.

    publish() compares each saved packet with one regenerated from its finding. Weekly
    dossiers and map excerpts are bound to an evidence version, and approvals to a
    content hash. After a projection-only change, a binding moves to the current
    version only when it was made for the published receipt's version and the receipt
    describes the same kind, interval and identity. A saved packet gains only the label
    provenance its own finding implies. An approval whose hash no longer matched its
    content before migration is never re-signed. A record either moves completely or
    stays as it was. The report holds counts, event IDs and fixed reason codes, and
    lists every weekly dossier still bound to other evidence.
    """
    _validate_ledger(ledger)
    at = stamp(at)
    updated = deepcopy(ledger)
    report = {**dict.fromkeys(MIGRATION_COUNTS, 0), "unresolved": []}
    for record in updated["records"]:
        automatic = record.get("automation") if isinstance(record.get("automation"), dict) else {}
        receipt = automatic.get("published_event")
        receipt = receipt if isinstance(receipt, dict) else None
        source = automatic.get("source")
        migrated = None
        if automatic:
            revisions = [record, *record.get("history", [])]
            migrated = []
            for revision in revisions:
                try:
                    migrated.append(_migrate_revision(revision, source, receipt))
                except MIGRATION_ERRORS as error:
                    number = revision.get("revision") if isinstance(revision, dict) else None
                    report["unresolved"].append(
                        {"id": record["id"], "reason": f"revision {number}: {_reason(error)}"}
                    )
                    migrated = None
                    break
        if migrated:
            counts = dict.fromkeys(MIGRATION_COUNTS, 0)
            for revision, (content, _projected, changes) in zip(revisions, migrated, strict=True):
                revision.update(content)
                for key, value in changes.items():
                    counts[key] += value
            # The map belongs to the published revision, which may be older than a pending one.
            published = next(
                (
                    projected
                    for revision, (_content, projected, _changes) in zip(revisions, migrated, strict=True)
                    if receipt and revision.get("revision") == receipt.get("revision")
                ),
                migrated[0][1],
            )
            media = automatic.get("media_evidence_version")
            if (
                receipt
                and media == receipt.get("evidence_version") != published["evidence_version"]
                and _binding_identity(receipt) == _binding_identity(published)
            ):
                automatic["media_evidence_version"] = published["evidence_version"]
                counts["media"] += 1
            if any(counts.values()):
                automatic.setdefault("evidence_migrations", []).append(
                    {
                        "at": at,
                        "from": receipt.get("evidence_version") if receipt else None,
                        "to": published["evidence_version"],
                        **counts,
                    }
                )
            for key, value in counts.items():
                report[key] += value
        report["unresolved"].extend(
            {"id": record["id"], "reason": reason} for reason in _unproven_bindings(record)
        )
        if record["state"] == "approved" and record["editorial"].get("weekly_spotlight") is not None:
            # Exports need the current binding; report any other reason they would fail.
            try:
                _projection(record["evidence"], record["editorial"])
            except MIGRATION_ERRORS as error:
                report["unresolved"].append({"id": record["id"], "reason": "export_check: " + _reason(error)})
    _validate_ledger(updated)
    ledger.clear()
    ledger.update(updated)
    return report


def _daily_counts(events, available_from, as_of):
    today = _instant(as_of).date()
    first = today - timedelta(days=29)
    days = []
    for offset in range(30):
        day = (first + timedelta(days=offset)).isoformat()
        available = day >= available_from
        counts = dict.fromkeys(TYPE_LABELS, 0) if available else None
        days.append(
            {
                "date": day,
                "status": "available" if available else "unavailable",
                "partial": day == today.isoformat(),
                "total": 0 if available else None,
                "counts": counts,
            }
        )
    by_day = {day["date"]: day for day in days}
    for event in events:
        bucket = by_day.get(event["observed_start"][:10])
        if bucket and bucket["counts"] is not None:
            bucket["total"] += 1
            bucket["counts"][event["primary_type"]] += 1
    return days


def _search(events):
    return [
        {
            "id": event["id"],
            "title": event["title"],
            "excerpt": event["story"]["dek"] if "story" in event else event["summary"],
            "topic": TYPE_LABELS[event["primary_type"]],
            "url": event["article_path"],
        }
        for event in events
    ]


def require_keys(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or set(value) - set(required) - set(optional)
        or set(required) - set(value)
    ):
        raise ValueError("Unexpected or missing public snapshot fields")


_keys = require_keys


def _window_metric(key):
    return key in EPISODE_MEASUREMENTS and key not in PEAK_MEASUREMENTS and key not in RULE_MEASUREMENTS


def _snapshot_measurement(item, as_of, kind=None):
    _keys(item, ("key", "label", "value", "unit", "scope"), ("at_utc", "start_utc", "end_utc"))
    key = item["key"]
    if not isinstance(key, str):
        raise ValueError("Unknown public measurement")
    scope = item["scope"]
    windowed = "start_utc" in item or "end_utc" in item
    if scope == "surrounding-reports" and key in NEIGHBOR_MEASUREMENTS and kind in NEIGHBOR_FIELDS:
        unit = "reports" if key == "surrounding_report_count" else READINGS[NEIGHBOR_FIELDS[kind]][1]
        expected = (NEIGHBOR_MEASUREMENTS[key], unit)
        _number(item["value"])
        single = key in NEIGHBOR_REPORTS
        if single == windowed or single != ("at_utc" in item) or ("start_utc" in item) != ("end_utc" in item):
            raise ValueError("A surrounding report has one time; a summary of them has a window")
    elif scope == "saved-report" and key in READINGS:
        expected = READINGS[key]
        if item["value"] == "ground" and key in {"alt_baro", "alt_geom", "archive_alt"}:
            expected = ("Air/ground report", "")
        else:
            _number(item["value"])
        if windowed:
            raise ValueError("A saved reading has one timestamp, not a window")
    elif scope == "saved-report" and key in TEXT_REPORTS:
        _text_report(key, item["value"])
        expected = (TEXT_REPORTS[key], "")
        if windowed or (key, item["value"]) == ("emergency_status", "none"):
            raise ValueError("A transponder report has one timestamp and a reported value")
    elif scope == "archived-label" and key in LABEL_REPORTS:
        _label_report(key, item["value"])
        expected = (LABEL_REPORTS[key], "")
        if windowed:
            raise ValueError("An archived label has at most its own timestamp")
    elif scope == "peak-report" and key in PEAK_MEASUREMENTS:
        expected = EPISODE_MEASUREMENTS[key]
        _number(item["value"])
        if windowed:
            raise ValueError("A peak report has one timestamp, not a window")
    elif scope == "detection-rule" and key in RULE_MEASUREMENTS:
        expected = EPISODE_MEASUREMENTS[key]
        _number(item["value"])
        if windowed or "at_utc" in item:
            raise ValueError("A detection rule is not a timed measurement")
    elif scope == "analysis-window" and key in EPISODE_TEXT:
        _episode_text(key, item["value"])
        expected = (EPISODE_TEXT[key], "")
        if "at_utc" in item or ("start_utc" in item) != ("end_utc" in item):
            raise ValueError("Incomplete analysis window")
    elif scope == "analysis-window" and key in WINDS_MEASUREMENTS and kind == "winds_aloft_day":
        expected = WINDS_MEASUREMENTS[key]
        _number(item["value"])
        if "at_utc" in item or ("start_utc" in item) != ("end_utc" in item):
            raise ValueError("Incomplete analysis window")
    elif scope == "analysis-window" and (key in PATTERN_MEASUREMENTS or _window_metric(key)):
        expected = PATTERN_MEASUREMENTS.get(key)
        if expected != (item["label"], item["unit"]) and _window_metric(key):
            expected = EPISODE_MEASUREMENTS[key]
        _number(item["value"])
        if "at_utc" in item or ("start_utc" in item) != ("end_utc" in item):
            raise ValueError("Incomplete analysis window")
    elif scope == "reference-data" and key in RATED_REFERENCE and key in RATED_KIND_KEYS.get(kind, ()):
        expected = RATED_REFERENCE[key]
        value = item["value"]
        if key == "type_check":
            if not isinstance(value, str) or value not in enrichment_rules.REGISTRIES:
                raise ValueError("Unknown type-check source")
        elif not REFERENCE_RANGES.get(key, lambda figure: figure > 0)(_number(value)):
            raise ValueError("A certified figure or modelled wind is out of range")
        if windowed or "at_utc" in item:
            raise ValueError("Reference data is not a timed measurement")
    elif scope == "analysis-window" and key in RATED_WINDOW and key in RATED_KIND_KEYS.get(kind, ()):
        expected = RATED_WINDOW[key]
        if _number(item["value"]) < 0:
            raise ValueError("A reading over the limit is never negative")
        if "at_utc" in item or "start_utc" not in item or "end_utc" not in item:
            raise ValueError("A comparison over the limit keeps its window")
    elif scope == "discontinuity-pair-not-physical-travel" and key in DISCONTINUITY_MEASUREMENTS:
        expected = DISCONTINUITY_MEASUREMENTS[key]
        _number(item["value"])
        if "at_utc" in item or ("start_utc" in item) != ("end_utc" in item):
            raise ValueError("Incomplete position interval")
    else:
        raise ValueError("Unknown public measurement or scope")
    if (item["label"], item["unit"]) != expected:
        raise ValueError("Measurement label or unit does not match its field")
    if not all(
        _stamped_by(item[field], as_of) for field in ("at_utc", "start_utc", "end_utc") if field in item
    ):
        raise ValueError("Invalid measurement timestamp")
    if "start_utc" in item and _instant(item["end_utc"]) < _instant(item["start_utc"]):
        raise ValueError("Measurement window ends before it starts")


def _snapshot_rated(event):
    """Each rated row at most once, and the modelled wind only beside its attribution."""
    keys = [item["key"] for item in event["measurements"] if item["key"] in RATED_KIND_KEYS[event["kind"]]]
    if len(keys) != len(set(keys)):
        raise ValueError("A certified-limit row appears twice")
    if WIND_KEYS & set(keys) and WIND_ATTRIBUTION not in event["limitations"]:
        raise ValueError("A modelled wind needs its attribution")


# Non-numeric proof fields an exceptional-event rule may record, with their checks.
PROOF_TEXT = {
    "state": lambda value: isinstance(value, str) and value in episode_evidence.STATE_MEANINGS,
    "context_tags": lambda value: (
        isinstance(value, list) and value == episode_evidence.tags({"context_tags": value})
    ),
}


def _snapshot_watchlist(block):
    # A snapshot exported before a wording change still validates; current_text() reads it.
    if not isinstance(block, dict) or {key: current_text(value) for key, value in block.items()} != (
        _watchlist_block(block)
    ):
        raise ValueError("Invalid watchlist association")


def _snapshot_participants(participants, primary):
    if not isinstance(participants, list) or not 1 <= len(participants) <= 12:
        raise ValueError("Invalid participants")
    addresses = [entry.get("icao_address") for entry in participants if isinstance(entry, dict)]
    if len([address for address in addresses if address]) != len(
        {address for address in addresses if address}
    ):
        raise ValueError("Duplicate participant")
    for entry in participants:
        _keys(
            entry,
            ("role", "source", "measurements"),
            ("icao_address", "registration", "callsign", "type_code", "type_description"),
        )
        identity = {
            key: value for key, value in entry.items() if key not in {"role", "source", "measurements"}
        }
        if (
            not isinstance(entry["role"], str)
            or entry["role"] not in PARTICIPANT_ROLES
            or not isinstance(entry["source"], str)
            or entry["source"] not in POSITION_SOURCES
            or _identity(identity) != identity
            or (primary and identity.get("icao_address") == primary)
            or not isinstance(entry["measurements"], list)
        ):
            raise ValueError("Invalid participant")
        for item in entry["measurements"]:
            _keys(item, ("key", "label", "value", "unit"))
            if not isinstance(item["key"], str) or PARTICIPANT_MEASUREMENTS.get(item["key"]) != (
                item["label"],
                item["unit"],
            ):
                raise ValueError("Participant measurement label or unit does not match its field")
            _number(item["value"])


EVENT_FIELDS = (
    "id",
    "revision",
    "observed_start",
    "observed_end",
    "kind",
    "primary_type",
    "secondary_labels",
    "title",
    "summary",
    "article_path",
    "aircraft",
    "measurements",
    "references",
    "synthetic",
    "published_at",
    "revised_at",
    "publication_status",
    "evidence_version",
    "source",
    "limitations",
)
EVENT_OPTIONAL = (
    "media",
    "weekly_spotlight",
    "priority_evidence",
    "watchlist",
    "participants",
    "story",
    "context",
    "released_at",
    "content_revised_at",
    # The measurement keys a card leads with (stories.HEADLINE_MEASUREMENTS); an export
    # always writes them, and an older snapshot without them still validates.
    "headline_measurements",
)


def _stamped_by(value, latest):
    """Whether a time is normalized UTC no later than `latest`."""
    return utc(value) == value and _instant(value) <= _instant(latest)


def _snapshot_priority(event):
    """Exception proof must be the rule's own output and agree with the recorded measurements."""
    proof = event["priority_evidence"]
    base = {"confirmed", "quality_review_required", "value", "duration_seconds", "sample_count"}
    if not isinstance(proof, dict) or not base <= set(proof):
        raise ValueError("Unexpected or missing public snapshot fields")
    fields = sorted(set(proof) - {"confirmed", "quality_review_required", "value", *PROOF_TEXT})
    for key, value in proof.items():
        if key in PROOF_TEXT and not PROOF_TEXT[key](value):
            raise ValueError("Invalid exceptional-event evidence")
    for key in fields:
        _number(proof[key])
    if proof["value"] is not None:
        _number(proof["value"])
    if priority_evidence(event["kind"], proof["value"], proof) != proof:
        raise ValueError("Invalid exceptional-event evidence")
    measurements = {
        item["key"]: item["value"]
        for item in event["measurements"]
        if item["scope"] in {"analysis-window", "peak-report"}
    }
    if any(key not in measurements or measurements[key] != proof[key] for key in fields):
        raise ValueError("Exceptional evidence does not match the recorded measurements")
    if event["kind"] != "rapid_descent":
        return
    if measurements.get("peak_rate_fpm") != proof["value"]:
        raise ValueError("Exceptional rate does not match the recorded peak")
    rows = {m["key"]: m for m in event["measurements"] if m["scope"] == "analysis-window"}
    intervals = {(rows[key].get("start_utc"), rows[key].get("end_utc")) for key in SUSTAINED_FIELDS}
    if len(intervals) != 1 or None in next(iter(intervals)):
        raise ValueError("Exceptional evidence needs one complete high-rate interval")
    start, end = map(_instant, next(iter(intervals)))
    episode = rows["duration_seconds"]
    episode_start, episode_end = _instant(episode.get("start_utc")), _instant(episode.get("end_utc"))
    contained = (
        _instant(event["observed_start"])
        <= episode_start
        <= start
        < end
        <= episode_end
        <= _instant(event["observed_end"])
    )
    elapsed = (end - start).total_seconds()
    if not contained or not math.isclose(elapsed, proof["sustained_duration_seconds"], abs_tol=0.000001):
        raise ValueError("Exceptional interval does not match its duration or episode")


def _snapshot_event(event, mode, status, as_of):
    """One public event's fields; IDs, pages and order are checked across the snapshot."""
    if type(event["revision"]) is not int or event["revision"] < 1:
        raise ValueError("Invalid public event revision")
    if (
        not isinstance(event["kind"], str)
        or event["kind"] not in KIND_TYPES
        or not isinstance(event["primary_type"], str)
        or event["primary_type"] not in TYPE_LABELS
    ):
        raise ValueError("Unknown public event kind or type")
    for field, limit in (("title", 160), ("summary", 1000)):
        if _text(event[field], limit) != event[field]:
            raise ValueError("Public text must be normalized")
    labels = event["secondary_labels"]
    if (
        not isinstance(labels, list)
        or len(labels) > 12
        or labels != sorted({_text(label, 60) for label in labels})
    ):
        raise ValueError("Invalid secondary labels")
    if type(event["synthetic"]) is not bool or (mode == "published" and event["synthetic"]):
        raise ValueError("Synthetic records cannot be published")
    if event["publication_status"] != status:
        raise ValueError("Mixed publication state")
    if not all(
        _stamped_by(event[field], as_of) for field in ("observed_start", "observed_end", "revised_at")
    ):
        raise ValueError("Invalid public event timestamp")
    if _instant(event["observed_end"]) < _instant(event["observed_start"]):
        raise ValueError("Reversed observation interval")
    if mode == "review":
        if event["published_at"] is not None:
            raise ValueError("Review snapshots must not claim publication")
    elif not _stamped_by(event["published_at"], as_of):
        raise ValueError("Invalid publication timestamp")
    _keys(
        event["aircraft"], (), ("icao_address", "registration", "callsign", "type_code", "type_description")
    )
    if _identity(event["aircraft"]) != event["aircraft"]:
        raise ValueError("Aircraft identity must be normalized")
    if not isinstance(event["source"], str) or event["source"] not in POSITION_SOURCES:
        raise ValueError("Unknown received source")
    if not isinstance(event["limitations"], list) or len(event["limitations"]) > MAX_LIMITATIONS:
        raise ValueError("Invalid public limitations")
    if any(_text(limitation, 600) != limitation for limitation in event["limitations"]):
        raise ValueError("Limitations must be normalized public text")
    if not isinstance(event["measurements"], list) or len(event["measurements"]) > 120:
        raise ValueError("Invalid public measurements")
    for item in event["measurements"]:
        _snapshot_measurement(item, as_of, event["kind"])
    if "story" in event and (
        _story(event["story"]) != event["story"]
        or event["title"] != event["story"]["headline"]
        or event["summary"] != event["story"]["dek"]
    ):
        raise ValueError("The story must be normalized and match the title and summary")
    if "context" in event and _routing(event["context"]) != event["context"]:
        raise ValueError("Story routing must be normalized")
    if "released_at" in event and (
        mode != "published"
        or not _stamped_by(event["released_at"], as_of)
        or _instant(event["released_at"]) < _instant(event["observed_end"])
    ):
        raise ValueError("A release time falls between the observation and the snapshot")
    if "content_revised_at" in event and (
        "released_at" not in event
        or not _stamped_by(event["content_revised_at"], as_of)
        or _instant(event["content_revised_at"]) < _instant(event["released_at"])
    ):
        raise ValueError("A content revision falls between the release and the snapshot")
    if not isinstance(event["references"], list):
        raise ValueError("Invalid public references")
    for reference in event["references"]:
        _keys(reference, ("id", "label", "url"))
    if _references(event["references"]) != event["references"]:
        raise ValueError("Public references must be normalized")
    if "media" in event:
        _keys(event["media"], ("src", "alt"))
        public_path(event["media"]["src"])
        _text(event["media"]["alt"], 300)
    if "watchlist" in event:
        _snapshot_watchlist(event["watchlist"])
    if "participants" in event:
        _snapshot_participants(event["participants"], event["aircraft"].get("icao_address"))
    if "priority_evidence" in event:
        _snapshot_priority(event)
    if event["kind"] in RATED_KINDS:
        _snapshot_rated(event)
    if "headline_measurements" in event and event["headline_measurements"] != list(
        stories.HEADLINE_MEASUREMENTS[event["kind"]]
    ):
        raise ValueError("Headline measurements must be the kind's list")
    if event["evidence_version"] != evidence_version(event):
        raise ValueError("Evidence version does not match its facts")


def validate_snapshot(snapshot):
    """Reject unknown/private fields before a frontend can copy an imported file.

    The content hash binds the versioned file's bytes to its normalized data. It
    is an integrity check, not a signature or proof of editorial approval.
    """
    _keys(
        snapshot,
        (
            "schema_version",
            "mode",
            "publication_status",
            "as_of_utc",
            "available_from",
            "window",
            "types",
            "events",
            "days",
            "search",
            "retired",
            "snapshot_id",
        ),
    )
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != VERSION:
        raise ValueError("Unsupported snapshot version")
    mode = snapshot["mode"]
    if (
        not isinstance(mode, str)
        or mode not in {"review", "published"}
        or snapshot["publication_status"] != ("unpublished" if mode == "review" else "published")
    ):
        raise ValueError("Invalid snapshot publication state")
    as_of = utc(snapshot["as_of_utc"])
    if as_of != snapshot["as_of_utc"]:
        raise ValueError("Snapshot time must be normalized UTC")
    _date(snapshot["available_from"])
    if not isinstance(snapshot["events"], list):
        raise ValueError("Invalid public event list")
    ids, paths = set(), set()
    for event in snapshot["events"]:
        _keys(event, EVENT_FIELDS, EVENT_OPTIONAL)
        try:
            valid_id = str(uuid.UUID(event["id"])) == event["id"]
        except (ValueError, AttributeError, TypeError):
            valid_id = False
        if not valid_id or event["id"] in ids or event["article_path"] in paths:
            raise ValueError("Duplicate or invalid public event")
        ids.add(event["id"])
        paths.add(_article_path(event["article_path"]))
        _snapshot_event(event, mode, snapshot["publication_status"], as_of)
    ordered = sorted(
        snapshot["events"], key=lambda item: (-_instant(item["observed_start"]).timestamp(), item["id"])
    )
    if snapshot["events"] != ordered:
        raise ValueError("Public events must be in observation order")
    _snapshot_retired(snapshot["retired"], mode, as_of)
    if mode == "published":
        validate_daily_limit(ordered)
    from .weekly_spotlight import validate_picks

    validate_picks(ordered, as_of)
    days = _daily_counts(ordered, snapshot["available_from"], as_of)
    expected = {
        "days": days,
        "window": {"start": days[0]["date"], "end": days[-1]["date"], "timezone": "UTC"},
        "types": [{"id": key, "label": label} for key, label in EVENT_TYPES],
        "search": _search(ordered),
    }
    if any(_json(snapshot[key]) != _json(value) for key, value in expected.items()):
        raise ValueError("Snapshot counts, search, or dates disagree with its events")
    if snapshot["snapshot_id"] != _digest(
        {key: value for key, value in snapshot.items() if key != "snapshot_id"}
    ):
        raise ValueError("Snapshot version does not match its content")
    return deepcopy(snapshot)


def _reject_constant(_value):
    raise ValueError("JSON contains a non-finite number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains a duplicate key")
        result[key] = value
    return result


def loads_strict(text):
    """JSON text without duplicate keys or non-finite numbers, as every saved file must be."""
    return json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def read_json(path):
    return loads_strict(Path(path).read_text())


def load_config(path):
    """publisher.json, with its private terms, site origin, contact address, site identity, and
    reference directory applied here and named for the tools this starts."""
    config = read_json(path)
    private_terms.export(config)
    site_origin.export(config)
    site_origin.export_contact(config)
    site_identity.export(config)
    reference_data.export(config)
    configure_private_text(config)
    return config


@contextmanager
def ledger_lock(path):
    """Serialize CLI read-modify-write operations to prevent lost approvals."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def write_json_atomic(path, value, *, private=False):
    """Keep the previous complete file on validation/write failure."""
    write_bytes_atomic(path, (_json(value) + "\n").encode(), private=private)


def write_bytes_atomic(path, data, *, private=False):
    """Replace a file in one step and keep the previous complete file if the write fails."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private else 0o755)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600 if private else 0o644)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
