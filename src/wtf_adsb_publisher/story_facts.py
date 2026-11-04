"""Deterministic story facts, the only input to the template and AI story writers.

Facts come from allowlisted evidence-packet and projection fields, the pinned
tables in data/ (type names, operator designators, topics, C's reference data),
earlier published stories, and the place and type profiles a caller passes in.
Nothing here reads notes, drafts, private rule prose (why, explanation),
receiver-relative fields, ownOp or a watchlist association label: every value is
taken by an explicit accessor, never by walking an arbitrary structure.

Each fact is {"id", "value", "basis", ...}. Basis is one of telemetry, database,
reference, derived, baseline, map, community-list, or archive-label; the site's own
detection rules are never facts, so no story can quote a threshold. Optional keys
type the value for the prose checks: "unit" for measurements, "field" for the packet
field the value stands for, "at" for its UTC time.
"""

import hashlib
import json
import logging
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from . import (
    airports,
    enrichment_rules,
    entity_rules,
    episode_evidence,
    kinds,
    limits,
    maneuvers,
    places,
    public_events,
    public_prose,
    publishers,
    reference,
    rules,
    story_urls,
    watchlists,
)
from .public_prose import number
from .rules import IDENTIFIERS, identifier

DATA = Path(__file__).resolve().parent / "data"
FACTS_VERSION = 4
# An airframe younger than this has no age fact: its age is not a story.
MIN_AGE_YEARS = 50
# A registration or address block from home says nothing; both writers leave such a fact out.
HOME_COUNTRY = "United States"
LOG = logging.getLogger("discoveries.story_facts")
TABLES = {
    "type_names": ("type_names.json", "types"),
    "airline_designators": ("airline_designators.json", "designators"),
    "topics": ("topics.json", "topics"),
    "display_names": ("display_names.json", "names"),
}
MAX_TABLE_BYTES = 2 * 1024 * 1024
KIND = re.compile(r"[a-z0-9_]{2,60}")
# A broadcast airline callsign: a three-letter designator, then a flight number.
AIRLINE_CALLSIGN = re.compile(r"(?P<designator>[A-Z]{3})(?P<number>[0-9][0-9A-Z]{0,4})")
PLACE_TEXT = re.compile(r"[A-Z][A-Za-z.' -]{0,60}(?:, [A-Z][A-Za-z.' -]{0,40})?")
PLACE_WORDS = re.compile(
    r"\b(?:receiver|antenna|feeder|station|home|coverage|range|bearing|overhead|local|nearby)\b", re.I
)
# Kinds whose story would make a nearby airport read as a landing, take-off, approach, hold, or
# diversion name none: the landing-shaped kinds (climbs and descents, go-arounds and returns,
# holds and reversals, and collision advisories) and the emergency-status kinds (every
# transponder status code, for lost link, hijack, radio failure, or emergency, and every
# emergency or priority status).
AIRPORT_EXCLUDED_KINDS = kinds.where(landing_shaped=True) | kinds.where(origin="special")
# Airport names that say more than a place: a landing, a private field, a military or guard field, a
# naval or air base, or a kind of person. PLACE_WORDS, which airport_name also applies, covers "home".
AIRPORT_WORDS = re.compile(
    r"\b(?:landing|strip|private|executive|captain|pilots?|skydive|skydiving|parachute|naval|air station|base"
    r"|army|national guard|marine corps|farms?|ranch)\b",
    re.I,
)
# A public body's field. Its name may honour a person ("Gerald R. Ford International Airport").
PUBLIC_AIRPORT_WORDS = re.compile(
    r"\b(?:County|Community|Municipal|Regional|International|Metropolitan|City|Township|State|National)\b"
)
# A person's middle initial ("Spencer J. Hardy"; "St." is no initial) or a memorial, which name a
# person when no public body owns the field.
PERSON_NAMED = re.compile(r"\b[A-Z]\.\s|\b[Mm]emorial\b")
PROFILE_ID = re.compile(r"tp\.[A-Z0-9]{2,4}\.[0-9]{1,2}")
# A curated type's encyclopedia article, for the site's JSON-LD sameAs.
SAME_AS = re.compile(r"https://en\.wikipedia\.org/wiki/[A-Za-z0-9_%/().,'-]+")
PROFILE_URL = re.compile(r"https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s<>\"]*)?")
# A profile fact is restated as a whole sentence: one terminal full stop, no exclamation or question.
PROFILE_SENTENCE = re.compile(r"[A-Z0-9][^!?]*[^.!?\s]\.")
MAX_PROFILE_FACTS = 6
MAX_PARTICIPANTS = 12
# Kinds whose story may state the type's cited certified limits, and the limit facts each
# may carry: an altitude reading the ceiling, a ground-speed spike or mismatch the airspeed
# limits (Vmo or Vne, and Mmo), which its prose names as airspeed limits and never compares
# with the reading. A ground-speed record or fast pass carries none: certified limits are
# airspeeds, and one beside a ground speed reads as a comparison the data cannot make.
SPEED_LIMIT_FACTS = frozenset({"lim.speed", "lim.mach"})
ALTITUDE_LIMIT_FACTS = frozenset({"lim.altitude"})
LIMIT_FACTS = {"speed": SPEED_LIMIT_FACTS, "altitude": ALTITUDE_LIMIT_FACTS}
RATED_LIMIT_KINDS = {
    name: LIMIT_FACTS[dimension]
    for name, dimension in kinds.table(
        "limit_dimension",
        order=(
            "type_speed_mismatch",
            "outlier_gs",
            "type_altitude_mismatch",
            "high",
            "outlier_alt_baro",
            "outlier_archive_alt",
        ),
    ).items()
}
# The certified figure each limit (limits.LIMITS) gives: (unit, field) as the prose checks
# type it. Vmo and Vne are speeds in knots there; the altitude's field is its dataset key,
# since the prose checks read a bare "alt" as an altitude reading, not a certified one.
RATED_LIMIT_UNITS = {
    name: ("kn" if limit.unit == "KIAS" else limit.unit, limit.field) for name, limit in limits.LIMITS.items()
}
# Rated-limit dataset keys, with the fact id, unit, and field each becomes; Vmo and Vne
# share lim.speed, since a type states one or the other.
LIMIT_FACT_IDS = {"ft": "lim.altitude", "KIAS": "lim.speed", "Mach": "lim.mach"}
RATED_LIMIT_FIELDS = tuple(
    (name, LIMIT_FACT_IDS[limit.unit], *RATED_LIMIT_UNITS[name]) for name, limit in limits.LIMITS.items()
)
# Kinds whose headline value is one reading with neighbouring reports around it, in their
# recorded order.
FACT_NEIGHBOR_FIELDS = kinds.table(
    "neighbor_field",
    order=(
        "outlier_gs",
        "outlier_alt_baro",
        "outlier_archive_alt",
        "outlier_baro_rate",
        "type_speed_mismatch",
        "type_altitude_mismatch",
        "fast",
        "high",
        "position_jump",
        "stratospheric_balloon",
    ),
)
# Reading fields a participant may carry, with their units from the prose checks' table.
FIELD_UNITS = {
    field: public_prose.FIELD_UNITS[field]
    for field in ("gs", "alt_baro", "archive_alt", "alt_geom", "baro_rate", "geom_rate")
}
# A participant's own measurements: its readings, and a winds-aloft day's median ground speed.
PARTICIPANT_UNITS = {
    **FIELD_UNITS,
    **{key: unit for key, (_label, unit) in episode_evidence.PARTICIPANT_METRICS.items()},
}
# A detection rule's own threshold is a site setting, not a fact; it has no basis here.
SCOPE_BASIS = {
    "saved-report": "telemetry",
    "peak-report": "telemetry",
    "analysis-window": "derived",
    "archived-label": "archive-label",
    "discontinuity-pair-not-physical-travel": "derived",
}
# Why a reading the listed type cannot produce may come from another aircraft's record.
MISMATCH_HYPOTHESES = (
    "a stale registry entry",
    "a re-registration",
    "a transponder set to the wrong address",
    "a database typo",
)
# Possible explanations a story may offer, always hedged in prose. A glitch story offers
# none: it gives the reading, the listed type and the type's cited limit, never a cause.
HYPOTHESES = {
    "position_jump": ("a decoding or data error",),
    "identity_conflict": ("a database error", "a re-registration", "an avionics setting"),
    "emitter_type_mismatch": ("a configuration setting aboard the aircraft", "a database error"),
    "stratospheric_balloon": ("a high-altitude balloon",),
    "survey_grid": ("aerial survey or mapping",),
    "parallel_passes": ("aerial survey or mapping",),
    "thermal_soaring": ("soaring in rising air",),
    "formation_flight": ("formation flight",),
    "type_speed_mismatch": MISMATCH_HYPOTHESES,
    "type_altitude_mismatch": MISMATCH_HYPOTHESES,
    "winds_aloft_day": ("strong winds aloft, such as a jet stream",),
    "aerobatic_practice": ("aerobatic practice",),
    "unregistered_address": (
        "registry lag after a new registration",
        "a registry the site does not consult",
        "an address programmed by hand",
    ),
    "racetrack_orbit": ("a long racetrack hold", "a refueling-style track", "an orbit over one area"),
}
# One general sentence per kind on what such an observation usually is, never what this flight
# was: the writer may restate it once as context (ctx.kind).
KIND_CONTEXT = {
    "descending_spiral": "A descending spiral is a standard maneuver, practiced to lose height over one spot.",
    "stratospheric_balloon": "Callsigns beginning HBAL are typical of high-altitude balloon flights.",
    "rapid_descent": "Descent rates vary with aircraft class, so a rapid descent is best read against the "
    "usual rates for its class.",
    "tcas_ra": "A TCAS resolution advisory tells a crew how to change their vertical motion to keep clear of "
    "another aircraft; Level Off tells a climbing or descending crew to stop.",
    "formation_flight": "Two aircraft holding position within a few hundred feet for minutes is typical of "
    "formation flight, which is routine for military trainers and some civil groups.",
    "winds_aloft_day": "Ground speed is airspeed plus wind, so a jet stream may show up as eastbound aircraft "
    "running far faster over the ground than westbound ones.",
    "foreign_military": "Military aircraft from allied States regularly cross this region; the data show the "
    "airframe, not the reason for the flight.",
    "first_operator_here": "An operator designator is the three-letter code that starts an airline-style callsign.",
    "aerobatic_practice": "Repeated climbs and dives inside a small area are what aerobatic practice looks like on "
    "a track map.",
    "unregistered_address": "Registries lag new registrations by days or weeks, and some States publish no public "
    "registry at all.",
    "racetrack_orbit": "A racetrack of long reciprocal legs is how tankers, airborne radar aircraft, and long holds "
    "use one piece of sky for an hour or more.",
    "identity_conflict": "The three identifiers an aircraft carries usually agree; a disagreement is usually a "
    "database or settings artifact.",
    "type_speed_mismatch": "A reading a type cannot produce usually means the address belongs to a different "
    "aircraft than the database lists.",
    "type_altitude_mismatch": "A reading a type cannot produce usually means the address belongs to a different "
    "aircraft than the database lists.",
    "seasonal_speed_record": "Ground-speed records mostly follow the wind, which is why the margin over the earlier "
    "best matters.",
    "thermal_soaring": "Gliders and motor gliders gain height by circling in rising air.",
    "first_type_here": "A first sighting says only that the type had not appeared in these records before.",
    "first_country_here": "An address block names the State that issued the address, which usually matches the "
    "registry.",
    "vintage": "Airframes past their eightieth year are rare in the air and usually fly to shows or from private "
    "strips.",
    "watch_type": "A watch type is one the site follows because it is large, rare, or historic.",
    "watchlist_match": "Plane Alert DB is a community list of aircraft spotters find interesting; a listing says "
    "why spotters follow an aircraft and nothing about who is aboard.",
    "notable_variant": "A notable variant is a military or government version of a type the database names "
    "precisely.",
    "altitude_gap": "Pressure and GPS altitudes differ with the weather; a gap beyond a few thousand feet is "
    "unusual.",
}
# The usual descent rate for a type class (desc.typical_fpm), beside a rapid descent's reading.
TYPICAL_DESCENT_FPM = {
    "airliner": "2,000 to 3,500 ft/min",
    "business_jet": "2,500 to 4,000 ft/min",
    "turboprop": "1,500 to 2,500 ft/min",
    "light_piston": "500 to 1,500 ft/min",
}
# Each range's slower and faster ends (desc.typical_fpm_low and _high) as quotable rates, signed
# as descents like the observed rate, so "descend at 2,000 to 3,500 feet a minute" is supported.
TYPICAL_DESCENT_RANGES = {
    name: tuple(-int(end.replace(",", "")) for end in text.removesuffix(" ft/min").split(" to "))
    for name, text in TYPICAL_DESCENT_FPM.items()
}
# Curated type categories (type_names.json) that set the descent class: business jets always,
# the rest only for a designator the Doc 8643 table lacks.
BUSINESS_JET_CATEGORIES = frozenset({"business jet", "light jet"})
DESCENT_CATEGORIES = {
    "airliner": "airliner",
    "double-deck airliner": "airliner",
    "regional jet": "airliner",
    "turboprop": "turboprop",
    "light aircraft": "light_piston",
}
# What marked a foreign military aircraft (fm.basis), in the order a story names them.
FOREIGN_BASIS_WORDS = {"dbFlags": "the aircraft database", "military_allocation": "the address block"}
# A shape kind's facts, each read from one episode metric with the metric's own unit.
SHAPE_FACTS = {
    "aerobatic_practice": (
        ("aer.reversals", "reversals"),
        ("aer.excursion", "altitude_excursion_ft"),
        ("aer.span", "span_nm"),
        ("aer.turn_rate", "max_turn_rate_dps"),
    ),
    "racetrack_orbit": (
        ("rt.legs", "racetrack_legs"),
        ("rt.leg_length", "leg_length_nm"),
        ("rt.spacing", "lateral_spacing_nm"),
        ("rt.duration", "duration_seconds"),
        ("rt.axis", "axis_heading_deg"),
    ),
}
# What qualified an aerobatic-style episode (aer.basis), in story words.
AEROBATIC_BASIS_WORDS = {"type": "an aerobatic type", "turn_rate": "a sustained hard turn"}
RECORD_KINDS = kinds.where(record=True)
# Stories about a day rather than an aircraft: the aircraft they name are examples, so they
# take no type profile and route to no type, airframe, or operator page.
SUBJECTLESS_KINDS = kinds.where(subjectless=True)
# A display title for each published kind, for a story that names an earlier one with no title.
KIND_TITLES = {
    "above_rated_altitude": "Above the rated ceiling",
    "above_rated_speed": "Above the rated speed",
    "aerobatic_practice": "Aerobatic practice",
    "altitude_gap": "Altitude gap",
    "altitude_oscillation": "Altitude oscillation",
    "circling": "Circling",
    "confined_activity": "Confined flight",
    "correlated_data_anomaly": "Data anomaly",
    "course_reversal": "Course reversal",
    "database_watch": "Military listing",
    "departure_return": "Departure and return",
    "descending_spiral": "Descending spiral",
    "emitter_type_mismatch": "Emitter mismatch",
    "extreme_turn_rate": "Tight turn",
    "fast": "Ground speed",
    "first_country_here": "First sighting",
    "first_operator_here": "First sighting",
    "first_type_here": "First sighting",
    "foreign_military": "Foreign military aircraft",
    "formation_flight": "Formation flight",
    "gnss_integrity_cluster": "GPS integrity",
    "gnss_integrity_loss": "GPS integrity",
    "high": "High altitude",
    "holding_stack": "Circling group",
    "hovering": "Hovering",
    "identity_conflict": "Identity conflict",
    "loitering": "Loitering",
    "military": "Military aircraft",
    "notable_variant": "Notable variant",
    "outlier_alt_baro": "Altitude spike",
    "outlier_archive_alt": "Altitude spike",
    "outlier_baro_rate": "Vertical rate spike",
    "outlier_gs": "Ground speed spike",
    "parallel_passes": "Parallel passes",
    "position_jump": "Position jump",
    "possible_go_around": "Go-around",
    "prolonged_holding": "Holding",
    "racetrack_orbit": "Racetrack orbit",
    "rapid_climb": "Rapid climb",
    "rapid_descent": "Rapid descent",
    "rare_here": "Type sighting",
    "repeated_passes": "Repeated passes",
    "seasonal_speed_record": "Ground speed record",
    "selected_altitude_overshoot": "Selected altitude overshoot",
    "space_category": "Emitter category",
    "special_squawk_downed": "Transponder status",
    "special_squawk_general": "Squawk 7700",
    "special_squawk_lifeguard": "Transponder status",
    "special_squawk_minfuel": "Transponder status",
    "special_squawk_nordo": "Squawk 7600",
    "special_squawk_unlawful": "Squawk 7500",
    "special_use_code": "Special code",
    "stratospheric_balloon": "High-altitude balloon",
    "survey_grid": "Grid of straight lines",
    "tcas_ra": "TCAS advisory",
    "thermal_soaring": "Soaring",
    "type_altitude_mismatch": "Altitude mismatch",
    "type_speed_mismatch": "Speed mismatch",
    "unregistered_address": "Unlisted address",
    "unusual_emitter": "Emitter category",
    "vertical": "Vertical rate",
    "vintage": "Vintage aircraft",
    "watch_type": "Watched type",
    "watchlist_match": "Watchlist entry",
    "wind_boost": "Speed difference",
    "winds_aloft_day": "Winds aloft",
    **{
        f"{family}_squawk_{code}": f"Squawk {code}"
        for family in ("special", "archive")
        for code in ("7400", "7500", "7600", "7700")
    },
}
# An earlier story of the same registration counts as a repeat (repeat.previous) for this long.
REPEAT_DAYS = 60
ARTICLE_PATH = re.compile(r"/stories/(?P<slug>[a-z0-9-]{1,120})/")
# Transmitted status meanings in story words (FAA JO 7110.66H Appendix A; ADS-B emergency status values).
STATUS_MEANINGS = {
    "7400": "an unmanned aircraft's lost link",
    "7500": "unlawful interference",
    "7600": "radio communication failure",
    "7700": "a general emergency",
    "general": "a general emergency",
    "nordo": "radio communication failure",
    "unlawful": "unlawful interference",
    "lifeguard": "a lifeguard (medical priority) flight",
    "minfuel": "minimum fuel",
    "downed": "a downed aircraft",
}
# The squawk code of each ADS-B status that has one: general 7700, nordo 7600, unlawful 7500.
STATUS_CODES = {name: code for code, name in maneuvers.STATUS_MEANINGS.items()}
FAA_CODES = {
    "id": "src.faa.jo-7110-66h",
    "publisher": "Federal Aviation Administration",
    "title": "JO 7110.66H, National Beacon Code Allocation Plan",
    "url": "https://www.faa.gov/documentLibrary/media/Order/2024-04-04_Order_JO_7110.66H_National_Beacon_Code"
    "_Allocation_Plan_(NBCAP)_FINAL.pdf",
}
MICTRONICS = {
    "id": "src.mictronics",
    "publisher": "Mictronics",
    "title": "Aircraft database (operator designators, ODC Attribution License 1.0)",
    "url": "https://github.com/Mictronics/aircraft-database",
}
# The Doc 8643 designator table behind a type's class phrase, for a designator with no name.
MICTRONICS_TYPES = {
    "id": "src.mictronics-types",
    "publisher": "Mictronics",
    "title": "ICAO aircraft type designators, Mictronics aircraft database (ODC-By)",
    "url": "https://github.com/Mictronics/aircraft-database",
}
# The second registries typecheck.py asks, cited by their API record for the address: neither
# has a public page per address. Both serve their answers to any caller (docs/autopilot.md).
REGISTRY_PAGES = {"adsbdb": enrichment_rules.ADSBDB + "{hex}", "hexdb": enrichment_rules.HEXDB + "{hex}"}
# Words for a Doc 8643 description code: the airframe from its first letter, then the engine
# count and type. Doc 8643 puts seaplanes and amphibians under airplanes, so they have their own
# words; the rotorcraft take reference.DESCRIPTION_CLASSES's.
AIRFRAME_WORDS = {"L": "airplane", "S": "seaplane", "A": "amphibian"}
AIRFRAMES = frozenset({"airplane", "seaplane", "amphibian", "helicopter", "gyroplane", "tiltrotor"})
ROTORCRAFT = frozenset({"helicopter", "gyroplane", "tiltrotor"})
ENGINE_COUNT_WORDS = {1: "single", 2: "twin", 3: "three", 4: "four", 6: "six", 8: "eight"}
ENGINE_WORDS = {"P": "piston", "T": "turboprop", "J": "jet", "E": "electric", "R": "rocket"}
# A rotorcraft's turbine drives a shaft, not a propeller.
ROTOR_ENGINE_WORDS = {"T": "turboshaft"}
# Only a twin joins its engine word ("twin-jet", "twin-turboshaft"); every other count takes
# "-engine" ("three-engine jet airplane", "four-engine piston airplane"), and so do electric and
# rocket engines, a twin's included ("twin-engine electric airplane").
COUNTED_ENGINES = frozenset({"E", "R"})
PLANE_ALERT_ID = "src.plane-alert-db"
# The modelled wind behind an airspeed estimate; its title is the attribution the licence asks for.
OPEN_METEO = {
    "id": "src.open-meteo",
    "publisher": "Open-Meteo",
    "title": "Weather data by Open-Meteo.com (CC BY 4.0)",
    "url": "https://open-meteo.com/",
}
# The collector's emitter labels, plus the categories it never names.
EMITTER_LABELS = {
    **rules.CATEGORY_LABELS,
    "A6": "high-performance",
    "B3": "parachutist",
    "B7": "space or transatmospheric vehicle",
}
# Descriptions that name a whole group rather than a model: "Balloon", "Ultralight".
GROUP_WORDS = {
    "airship",
    "balloon",
    "hot air balloon",
    "gas balloon",
    "blimp",
    "glider",
    "sailplane",
    "motor glider",
    "ultralight",
    "microlight",
    "gyroplane",
    "gyrocopter",
    "autogyro",
    "paraglider",
    "paramotor",
    "hang glider",
    "powered parachute",
    "drone",
    "unmanned aircraft",
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@lru_cache(maxsize=None)
def table(name):
    """A verified pinned table, or None. Callers must treat it as read-only."""
    filename, key = TABLES[name]
    try:
        raw = (DATA / filename).read_bytes()
        if len(raw) > MAX_TABLE_BYTES:
            raise ValueError("table exceeds its size bound")
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != 1
            or not isinstance(data.get(key), dict)
        ):
            raise ValueError("unsupported table schema")
        recorded = data.get("content_sha256")
        content = {item: value for item, value in data.items() if item != "content_sha256"}
        if recorded != hashlib.sha256(_canonical(content).encode()).hexdigest():
            raise ValueError("content hash differs")
        return data
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
        LOG.warning("Story table %s unavailable (%s: %s)", name, type(error).__name__, error)
        return None


def display_name(text, *, registration=None):
    """The text with the database misspellings in display_names.json replaced, whole names only.

    A plain entry always applies; an entry with when_registration_prefix applies only to an
    airframe whose registration starts with it. Any other text comes back unchanged.
    """
    if not isinstance(text, str):
        return text
    names = (table("display_names") or {}).get("names", {})
    for wrong in sorted(names, key=len, reverse=True):
        right = names[wrong]
        if isinstance(right, dict):
            prefix = right.get("when_registration_prefix")
            if not (
                isinstance(prefix, str) and isinstance(registration, str) and registration.startswith(prefix)
            ):
                continue
            right = right.get("name")
        if isinstance(right, str):
            text = re.sub(
                rf"(?<![A-Za-z0-9]){re.escape(wrong)}(?![A-Za-z0-9])", lambda _match, name=right: name, text
            )
    return text


def _text(value, limit=200):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:limit] if value else None


def _utc(value):
    """A UTC ISO timestamp with Z, from an ISO time or epoch seconds, or None."""
    moment = public_prose.moment(value) if not isinstance(value, str) or "T" in value else None
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z") if moment else None


def _iso_date(value):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def _dict(mapping, key):
    value = mapping.get(key) if isinstance(mapping, dict) else None
    return value if isinstance(value, dict) else {}


def _list(mapping, key):
    value = mapping.get(key) if isinstance(mapping, dict) else None
    return value if isinstance(value, list) else []


def _casing(description):
    """Readable casing for an uncurated database description: AIRBUS A-380-800 -> Airbus A-380-800."""
    words = []
    for word in description.split():
        letters = re.sub(r"[^A-Za-z]", "", word)
        # The database joins a double-barrelled maker with an underscore: KREIDER_REISNER.
        if word.isupper() and len(letters) >= 3 and not re.search(r"\d", word) and "-" not in word:
            word = "-".join(part.capitalize() for part in word.split("_"))
        words.append(word)
    return " ".join(words)


def type_entry(code, description=None):
    """Display name, aliases, short name, slugs and category for a type, or None.

    A mixed-case database description that names a specific model (for example
    "Boeing KC-46A Pegasus" under B762) wins over the designator's curated name, and
    a model under a group designator ("Zeppelin LZ N07-101" under SHIP) is a type of
    its own rather than the group.
    """
    code = identifier(code, IDENTIFIERS["type_code"])
    description = _text(description, 160)
    names = (table("type_names") or {}).get("types", {})
    entry = names.get(code) if code else None
    if entry and description:
        known = {entry["name"].lower(), *(alias.lower() for alias in entry["aliases"])}
        first = description.split()[0]
        specific = (
            description.lower() not in known and not first.isupper() and re.search(r"[A-Z]", description)
        )
        if specific and not _same_family(description, entry):
            entry = None
    if entry:
        aliases = list(entry["aliases"])
        if description and description not in aliases and description != entry["name"]:
            aliases.append(description)
        return {
            "code": code,
            "name": entry["name"],
            "short": entry["short"],
            "aliases": aliases,
            "slug": None if entry.get("group") else entry["slug"],
            "short_slug": None if entry.get("group") else entry.get("short_slug"),
            "category": entry["category"],
            "group": bool(entry.get("group")),
            "curated": True,
            "same_as": entry["same_as"] if SAME_AS.fullmatch(entry.get("same_as") or "") else None,
        }
    if not description:
        return None
    name = _casing(description)
    group = bool(code and reference.is_group_designator(code)) and description.lower() in GROUP_WORDS
    short = name.split(" ", 1)[1] if " " in name and re.search(r"\d", name.split(" ", 1)[1]) else name
    return {
        "code": code,
        "name": name,
        "short": short,
        "aliases": [description] if description != name else [],
        "slug": None if group else story_urls.token(name) or None,
        "short_slug": None,
        "category": None,
        "group": group,
        "curated": False,
        "same_as": None,
    }


def _same_family(description, entry):
    """A description naming a model of the curated type ("Cessna 172R Skyhawk" under C172),
    not a different designation ("Boeing KC-46A Pegasus" under B762)."""

    def models(text):
        return {word.lower() for word in re.findall(r"[A-Za-z0-9-]*\d[A-Za-z0-9-]*", text)}

    curated = models(" ".join([entry["name"], *entry["aliases"]]))
    return any(word.startswith(model) for word in models(description) for model in curated if len(model) >= 3)


# Armed-service words a type history names: "delivered to the Navy", "Air National Guard".
SERVICE_WORDS = (
    "Navy",
    "Naval",
    "Army",
    "Air",
    "Force",
    "Forces",
    "Marine",
    "Marines",
    "Corps",
    "Coast",
    "Guard",
)


def type_vocabulary(entry=None):
    """Names a sourced profile of this type may use, as (words, phrases).

    The words are armed-service words and every word of the type's own curated names,
    manufacturer and aliases, and of the entries that share its slug. Every other
    curated type is named only by a whole phrase: its name, its name without the
    manufacturer, its short name and its aliases, each kept only when it has two
    words or a digit. So "Boeing E-3 Sentry" and "E-3 Sentry" pass in an E-6 profile,
    but another type's maker or one-word alias ("Great Lakes" is a maker, "Dakota" an
    alias, "Sierra" a short name) never makes a place name pass.
    """
    entry = entry if isinstance(entry, dict) else {}
    return _type_vocabulary(entry.get("slug"), entry.get("code"))


@lru_cache(maxsize=256)
def _type_vocabulary(slug, code):
    words, phrases = set(SERVICE_WORDS), set()
    for key, entry in (table("type_names") or {}).get("types", {}).items():
        name, manufacturer = entry.get("name"), entry.get("manufacturer")
        texts = [name, entry.get("short"), *entry.get("aliases", ())]
        if key == code or (slug and entry.get("slug") == slug):
            words.update(
                word
                for text in [*texts, manufacturer]
                if isinstance(text, str)
                for word in re.findall(r"[A-Za-z]+", text)
            )
            continue
        if isinstance(name, str) and isinstance(manufacturer, str) and name.startswith(manufacturer + " "):
            texts.append(name[len(manufacturer) + 1 :])
        phrases.update(
            text
            for text in texts
            if isinstance(text, str) and (" " in text.strip() or re.search(r"\d", text))
        )
    return frozenset(words), frozenset(phrases)


def operator_for(callsign):
    """The operator assigned an airline-format callsign's designator, or None."""
    match = AIRLINE_CALLSIGN.fullmatch(callsign) if isinstance(callsign, str) else None
    if not match:
        return None
    entry = (table("airline_designators") or {}).get("designators", {}).get(match["designator"])
    if not entry:
        return None
    return {**entry, "designator": match["designator"]}


def topic_for(kind):
    """(topic slug, section slug) for a kind; an unlisted kind goes to its event type's first topic.

    The site build's topicFor() applies the same fallback, and a test holds them together.
    """
    topics = (table("topics") or {}).get("topics", {})
    for slug, topic in topics.items():
        if kind in topic.get("kinds", []):
            return slug, topic["section"]
    section = public_events.KIND_TYPES.get(kind)
    if section is None and isinstance(kind, str) and kind.startswith(rules.SQUAWK_PREFIXES):
        section = "transponder-reports"
    return next((slug for slug, topic in topics.items() if topic.get("section") == section), None), section


def _fact(facts, identifier_, value, basis, **extra):
    if value is None or value == "" or value == []:
        return
    facts.append(
        {
            "id": identifier_,
            "value": value,
            "basis": basis,
            **{k: v for k, v in extra.items() if v is not None},
        }
    )


def _measurement_items(packet, projection):
    """Allowlisted projected measurements; built from the packet when no projection is given."""
    if isinstance(projection, dict) and isinstance(projection.get("measurements"), list):
        return [item for item in projection["measurements"] if isinstance(item, dict)]
    try:
        return public_events._measurements(packet)
    except (ValueError, TypeError, KeyError):
        return []


def _limits(packet, projection):
    if isinstance(projection, dict) and isinstance(projection.get("limitations"), list):
        return [item for item in projection["limitations"] if isinstance(item, str)]
    try:
        return public_events._context(packet)[1]
    except (ValueError, TypeError, KeyError):
        return []


def _identity(packet, projection):
    source = projection.get("aircraft") if isinstance(projection, dict) else None
    if packet.get("kind") in SUBJECTLESS_KINDS:
        # The public event names no aircraft; the story still names its example by the packet.
        source = None
    source = source if isinstance(source, dict) else _dict(packet, "aircraft")
    registration = identifier(source.get("registration"), IDENTIFIERS["registration"])
    parts = reference.registration_parts(registration) if registration else None
    return {
        # A bare nationality mark such as "F-" names no aircraft.
        "registration": None if parts and not parts[1] else registration,
        "callsign": identifier(source.get("callsign"), IDENTIFIERS["callsign"]),
        "hex": identifier(source.get("icao_address"), IDENTIFIERS["icao_address"]),
        "type_code": identifier(source.get("type_code"), IDENTIFIERS["type_code"]),
        "description": _text(source.get("type_description"), 160),
    }


def type_class_phrase(code):
    """A Doc 8643 designator's class phrase, such as "twin-turboshaft helicopter", from its
    description code; None for a group designator or an unknown code. A designator the
    Mictronics table lacks has no phrase, and neither has a glider or drone, whose
    description code reads as an airplane's."""
    info = reference.type_info(code)
    if not info or info["group"] or info["class"] not in {"airplane", "helicopter", "gyroplane", "tiltrotor"}:
        return None
    letter = info["description"][:1]
    airframe = AIRFRAME_WORDS.get(letter, reference.DESCRIPTION_CLASSES.get(letter))
    if airframe not in AIRFRAMES:
        return None
    count, engine = info.get("engine_count"), info.get("engine_type")
    words = ROTOR_ENGINE_WORDS if airframe in ROTORCRAFT else {}
    engine_word = words.get(engine, ENGINE_WORDS.get(engine))
    count_word = ENGINE_COUNT_WORDS.get(count)
    if not (engine_word and count_word):
        return airframe
    if count == 2 and engine not in COUNTED_ENGINES:
        return f"{count_word}-{engine_word} {airframe}"
    return f"{count_word}-engine {engine_word} {airframe}"


def _type_class(facts, sources, code):
    """A story's designator with no name: its class phrase (id.type_class), with the table it
    comes from in sources."""
    phrase = type_class_phrase(code)
    if phrase:
        _fact(facts, "id.type_class", phrase, "reference", source=MICTRONICS_TYPES["id"], code=code)
        sources.append(dict(MICTRONICS_TYPES))


def _type_fact(facts, prefix, code, description, *, registration=None):
    """The type's name fact and its entry, or None for a type with no name."""
    entry = type_entry(code, display_name(description, registration=registration))
    if not entry:
        return None
    _fact(
        facts,
        prefix,
        entry["name"],
        "database",
        field="type_description",
        aliases=entry["aliases"] or None,
        short=entry["short"],
        slug=entry["slug"],
        short_slug=entry["short_slug"],
        category=entry["category"],
        group=entry["group"] or None,
        code=entry["code"],
        same_as=entry["same_as"],
    )
    return entry


def _track(facts, packet):
    """Counts, span and path length of the aircraft's own positions; never a coordinate."""
    points = _dict(packet, "replay").get("points")
    if not isinstance(points, list) or not points:
        return
    clean = []
    for point in points[:200]:
        if not isinstance(point, dict):
            return
        stamp, lat, lon = (number(point.get(key)) for key in ("timestamp", "latitude", "longitude"))
        if stamp is None or lat is None or lon is None:
            return
        clean.append((stamp, lat, lon, point))
    clean.sort(key=lambda item: item[0])
    length = sum(
        rules.distance_nm(previous[1:3], current[1:3])
        for previous, current in zip(clean, clean[1:], strict=False)
        if not current[3].get("break_before")
    )
    sources = sorted({str(item[3].get("source")) for item in clean if isinstance(item[3].get("source"), str)})
    _fact(facts, "track.positions", len(clean), "derived", unit="samples")
    _fact(facts, "track.seconds", round(clean[-1][0] - clean[0][0], 1), "derived", unit="s")
    if length >= 0.1:
        _fact(facts, "track.nm", round(length, 2), "derived", unit="NM")
    if sources == ["mlat"]:
        _fact(facts, "track.mlat", True, "telemetry")
    for key, unit, name in (("altitude_ft", "ft", "alt"), ("ground_speed_kn", "kn", "gs")):
        values = [value for item in clean if (value := number(item[3].get(key))) is not None]
        if len(values) >= 2:
            _fact(facts, f"track.{name}_min", min(values), "telemetry", unit=unit, field=f"track_{name}")
            _fact(facts, f"track.{name}_max", max(values), "telemetry", unit=unit, field=f"track_{name}")


def _place(facts, place):
    if not isinstance(place, dict):
        return None
    values = {key: place.get(key) for key in ("name", "short", "state")}
    if not all(isinstance(value, str) and PLACE_TEXT.fullmatch(value) for value in values.values()):
        return None
    if any(PLACE_WORDS.search(value) or re.search(r"\d", value) for value in values.values()):
        return None
    _fact(facts, "track.place", values["name"], "map", short=values["short"], state=values["state"])
    return values


def airport_name(name, kind):
    """Whether an airport name may appear in a story: a plain place name that trips no claim,
    location, or person rule on its own, and a public body's field (PUBLIC_AIRPORT_WORDS) or one
    named for a regional town ("Brighton Airport"). A family's field ("Gross Airport") is neither,
    and a town's field is refused when an initial or a memorial names a person."""
    return isinstance(name, str) and _airport_name(name, kind, places.town_names())


# The same few fields recur in nearly every record, so each name is judged once for each town
# set: a failed read of the places bundle (an empty set) is never the answer for a later one.
@lru_cache(maxsize=1024)
def _airport_name(name, kind, towns):
    if not PLACE_TEXT.fullmatch(name) or re.search(r"\d", name):
        return False
    if PLACE_WORDS.search(name) or AIRPORT_WORDS.search(name) or public_prose.phrase_issues(name, kind):
        return False
    if PUBLIC_AIRPORT_WORDS.search(name):
        return True
    if PERSON_NAMED.search(name):
        return False
    words = name.split(" ")
    return any(" ".join(words[:count]) in towns for count in range(1, len(words) + 1))


def _airport(facts, airport, kind):
    """The nearest public airport (airports.nearest), as a name and a distance from the track."""
    if not isinstance(airport, dict) or kind in AIRPORT_EXCLUDED_KINDS:
        return
    name, ident, nm = airport.get("name"), airport.get("ident"), number(airport.get("nm"))
    if (
        not airport_name(name, kind)
        or not isinstance(ident, str)
        or nm is None
        or not 0 <= nm <= airports.MAX_NM
    ):
        return
    _fact(facts, "track.airport", name, "map", ident=ident)
    _fact(facts, "track.airport_nm", nm, "map", unit="NM", field="airport_distance")


def _readings(samples, field):
    """(time, value) pairs of one field in the saved surrounding reports, in time order."""
    series = []
    for sample in samples[:40] if isinstance(samples, list) else ():
        if isinstance(sample, dict):
            stamp = number(sample.get("ts", sample.get("timestamp")))
            reading = number(sample.get(field))
            if stamp is not None and reading is not None:
                series.append((stamp, reading))
    return sorted(series)


def _neighbors(facts, packet, kind, observed_at):
    field = FACT_NEIGHBOR_FIELDS.get(kind)
    value = number(_dict(packet, "observation").get("metric_value"))
    if not field or value is None:
        return
    series = _readings(packet.get("surrounding_readings"), field)
    if len(series) < 2:
        return
    # The peak is the matching reading nearest the saved readings' time.
    candidates = [index for index, (_stamp, reading) in enumerate(series) if abs(reading - value) < 0.05]
    if not candidates:
        return
    target = observed_at.timestamp() if observed_at else series[candidates[0]][0]
    peak = min(candidates, key=lambda index: abs(series[index][0] - target))
    others = [reading for index, (_stamp, reading) in enumerate(series) if index != peak]
    unit = FIELD_UNITS[field]
    if peak > 0:
        stamp, reading = series[peak - 1]
        _fact(facts, "nb.before", reading, "telemetry", unit=unit, field=field, at=_utc(stamp))
        _fact(facts, "nb.gap_before", round(series[peak][0] - stamp, 1), "derived", unit="s")
    if peak < len(series) - 1:
        stamp, reading = series[peak + 1]
        _fact(facts, "nb.after", reading, "telemetry", unit=unit, field=field, at=_utc(stamp))
        _fact(facts, "nb.gap_after", round(stamp - series[peak][0], 1), "derived", unit="s")
    if others:
        _fact(facts, "nb.min", min(others), "telemetry", unit=unit, field=field)
        _fact(facts, "nb.max", max(others), "telemetry", unit=unit, field=field)
        _fact(facts, "nb.count", len(others), "derived", unit="samples")
    _fact(facts, "nb.peak_at", _utc(series[peak][0]), "telemetry")


def _balloon_spike(facts, packet):
    """A lone ground-speed spike inside a slow, high track: the glitch a balloon story folds in."""
    readings = _readings(packet.get("surrounding_readings"), "gs")
    if len(readings) < 3:
        return
    index = max(range(len(readings)), key=lambda position: readings[position][1])
    stamp, peak = readings[index]
    others = sorted(speed for moment, speed in readings if moment != stamp)
    middle = others[len(others) // 2]
    if peak >= 300 and middle <= 120:
        _fact(facts, "spike.gs", peak, "telemetry", unit="kn", field="gs", at=_utc(stamp))
        if index > 0:
            _fact(facts, "spike.before", readings[index - 1][1], "telemetry", unit="kn", field="gs")
        if index < len(readings) - 1:
            _fact(facts, "spike.after", readings[index + 1][1], "telemetry", unit="kn", field="gs")


def _profile_shape(text):
    """Issues with a profile sentence's shape: one plain sentence of 20 to 300 characters."""
    if not isinstance(text, str) or not 20 <= len(text) <= 300 or len(text.split()) > 40:
        return ["profile:length"]
    if not PROFILE_SENTENCE.fullmatch(text) or public_prose.DASH.search(text):
        return ["profile:not_one_sentence"]
    return []


def profile_issues(text, code, description=None, source=None):
    """Why a researched sentence cannot be a profile fact for this type; [] when it can.

    The sentence must be one plain, sourced sentence and pass the claim, name and
    location checks story prose passes. Names may come from type_vocabulary (the type's
    own words, other curated types' whole names) and the source's publisher and title.
    weekly_research runs this before saving a profile; build() checks each fact again on
    use, against the story's own facts.
    """
    facts = []
    words, phrases = type_vocabulary(_type_fact(facts, "id.type", code, description))
    packet = {"kind": "profile", "facts": facts, "limits": [], "hypotheses": [], "sources": []}
    return _profile_shape(text) or public_prose.profile_issues(
        text, packet, source=source, vocabulary=words, phrases=phrases
    )


def _profiles(facts, sources, profiles, entry, kind):
    """Sourced type-profile sentences for this type's family; each source is copied with its fact.

    Profiles are keyed by curated type slug, so research on a C-32A never describes a
    757-200, though both share B752. Every sentence passes the story checks again here,
    under the rules in force now.
    """
    profile = profiles.get(entry["slug"]) if isinstance(profiles, dict) and entry else None
    if not isinstance(profile, dict) or not isinstance(profile.get("facts"), list):
        return
    packet = {"kind": kind, "facts": list(facts), "limits": [], "hypotheses": [], "sources": []}
    words, phrases = type_vocabulary(entry)
    check = public_prose.profile_checker(packet, vocabulary=words, phrases=phrases)
    added = 0
    for item in profile["facts"]:
        if added >= MAX_PROFILE_FACTS or not isinstance(item, dict):
            continue
        fact_id = item.get("id")
        text = _text(item.get("text"), 400)
        source = _dict(item, "source")
        url = source.get("url")
        publisher, title = _text(source.get("publisher"), 120), _text(source.get("title"), 200)
        if (
            not isinstance(fact_id, str)
            or not PROFILE_ID.fullmatch(fact_id)
            or not isinstance(url, str)
            or not PROFILE_URL.fullmatch(url)
            or not publisher
            or not title
            or _profile_shape(text)
            or check(text, source)
        ):
            continue
        source_id = "src." + fact_id[3:]
        _fact(facts, fact_id, text, "reference", source=source_id, field="profile")
        sources.append({"id": source_id, "publisher": publisher, "title": title, "url": url})
        added += 1


def _participants(facts, packet, projection, *, start=1):
    """The other aircraft as p.<n> facts, numbered from start (2 when p.1 is the finding's own)."""
    raw = projection.get("participants") if isinstance(projection, dict) else None
    raw = raw if isinstance(raw, list) else packet.get("participants", [])
    if not isinstance(raw, list):
        return []
    allowed = set(PARTICIPANT_UNITS) | set(episode_evidence.METRICS)
    entries = []
    for index, item in enumerate(raw[:MAX_PARTICIPANTS], start=start):
        if not isinstance(item, dict):
            continue
        prefix = f"p.{index}"
        role = item.get("role") if item.get("role") in {"lead", "member", "wingman", "threat"} else "member"
        _fact(facts, f"{prefix}.role", role, "derived")
        registration = identifier(item.get("registration"), IDENTIFIERS["registration"])
        _fact(facts, f"{prefix}.registration", registration, "database", field="registration")
        _fact(
            facts,
            f"{prefix}.callsign",
            identifier(item.get("callsign"), IDENTIFIERS["callsign"]),
            "telemetry",
            field="callsign",
        )
        _fact(
            facts,
            f"{prefix}.hex",
            identifier(item.get("icao_address"), IDENTIFIERS["icao_address"]),
            "telemetry",
            field="icao_address",
        )
        entry = _type_fact(
            facts,
            f"{prefix}.type",
            item.get("type_code"),
            item.get("type_description"),
            registration=registration,
        )
        measurements = item.get("measurements")
        if isinstance(measurements, list):
            pairs = [
                (m.get("key"), m.get("value"), m.get("unit"))
                for m in measurements
                if isinstance(m, dict) and isinstance(m.get("key"), str)
            ]
        elif isinstance(measurements, dict):
            pairs = [(key, value, PARTICIPANT_UNITS.get(key)) for key, value in measurements.items()]
        else:
            pairs = []
        for key, value, unit in pairs:
            if number(value) is not None and key in allowed and isinstance(unit, str):
                _fact(facts, f"{prefix}.m.{key}", value, "telemetry", unit=unit, field=key)
        entries.append(entry)
    if entries:
        _fact(facts, "p.count", len(entries) + 1, "derived", unit="aircraft")
        _fact(facts, "p.others", len(entries), "derived", unit="aircraft")
    return entries


def _codes(facts, sources, packet, kind, items):
    """Transmitted codes, sequences and advisories, with the code meanings they carry."""
    text_items = {item.get("key"): item for item in items if isinstance(item.get("value"), str)}
    if (squawk := text_items.get("squawk")) and episode_evidence.octal_code(squawk["value"]):
        _fact(
            facts, "code.squawk", squawk["value"], "telemetry", field="squawk", at=_utc(squawk.get("at_utc"))
        )
    if (status := text_items.get("emergency_status")) and status["value"] in STATUS_MEANINGS:
        _fact(facts, "code.status", status["value"], "telemetry", field="emergency")
    episode = _dict(packet, "maneuver")
    code = episode_evidence.octal_code(episode.get("code")) if isinstance(episode.get("code"), str) else None
    _fact(facts, "code.code", code, "telemetry", field="code")
    sequence = episode.get("code_sequence")
    if isinstance(sequence, list) and 1 <= len(sequence) <= 16:
        steps = [
            (step.get("code"), number(step.get("seconds"))) if isinstance(step, dict) else (None, None)
            for step in sequence
        ]
        if all(isinstance(value, str) and seconds is not None for value, seconds in steps):
            _fact(
                facts,
                "code.sequence",
                [value for value, _seconds in steps],
                "telemetry",
                field="code_sequence",
            )
            for index, (_value, seconds) in enumerate(steps, start=1):
                _fact(facts, f"code.step_{index}_seconds", seconds, "telemetry", unit="s")
    if (advisory := text_items.get("advisory")) and advisory["value"]:
        _fact(facts, "ra.advisory", advisory["value"], "telemetry", field="advisory")
    if (complement := text_items.get("advisory_complement")) and complement["value"]:
        _fact(facts, "ra.complement", complement["value"], "telemetry", field="advisory")
    meaning_code = None
    if kind.startswith(rules.SQUAWK_PREFIXES):
        meaning_code = kind.rsplit("_", 1)[1]
    elif kind == "special_use_code" and code:
        meaning_code = code
    if meaning_code in STATUS_MEANINGS:
        _fact(
            facts,
            "code.meaning",
            STATUS_MEANINGS[meaning_code],
            "reference",
            code=STATUS_CODES.get(meaning_code, meaning_code),
            source=FAA_CODES["id"],
        )
        sources.append(dict(FAA_CODES))
    elif meaning_code:
        entry = reference.beacon_code(meaning_code)
        if entry and not entry.get("sensitive") and entry.get("category") in {"special_use", "vfr", "status"}:
            label = entry["label"][0].lower() + entry["label"][1:]
            _fact(facts, "code.meaning", label, "reference", code=meaning_code, source=FAA_CODES["id"])
            sources.append(dict(FAA_CODES))


def _operator(facts, sources, identity, checks):
    operator = operator_for(identity["callsign"])
    if operator and checks.get("airline_callsign_on_light_type") is not True:
        _fact(
            facts,
            "op.callsign",
            display_name(operator["name"]),
            "reference",
            field="operator",
            short=display_name(operator["short"]),
            slug=operator["slug"],
            designator=operator["designator"],
            telephony=operator.get("telephony"),
            kind=operator["kind"],
            source=MICTRONICS["id"],
        )
        sources.append(dict(MICTRONICS))


def _nationality(facts, identity):
    """The registration's country and the address block's State; only for an unconflicted identity."""
    registration = identity["registration"]
    parts = reference.registration_parts(registration) if registration else None
    countries = reference.registration_countries(registration) if parts else None
    name = reference.country_name(countries[0]) if countries else None
    _fact(
        facts,
        "reg.country",
        name,
        "reference",
        field="registration_country",
        prefix=parts[0] if parts else None,
        home=True if name == HOME_COUNTRY else None,
    )
    allocation = reference.allocation(identity["hex"]) if identity["hex"] else None
    if allocation and allocation.get("name"):
        home = True if allocation["name"] == HOME_COUNTRY else None
        _fact(facts, "alloc.country", allocation["name"], "reference", field="allocation_country", home=home)


def _identity_conflicts(facts, checks, *, archived=False):
    """Keep agreeing address evidence and the provenance of the differing callsign."""
    implied = identifier(checks.get("us_registration_from_address"), IDENTIFIERS["registration"])
    _fact(facts, "idc.address_registration", implied, "derived", field="registration")
    if checks.get("callsign_matches_registration") is False:
        callsign = identifier(checks.get("callsign_registration"), IDENTIFIERS["registration"])
        _fact(
            facts,
            "idc.callsign_registration",
            callsign,
            "archive-label" if archived else "telemetry",
            field="callsign",
        )
    if checks.get("allocation_matches_registration") is False:
        registered = checks.get("registration_country")
        allocated = checks.get("allocation_country")
        registered = reference.country_name(registered) if isinstance(registered, str) else None
        allocated = reference.country_name(allocated) if isinstance(allocated, str) else None
        if registered and allocated and registered != allocated:
            _fact(facts, "idc.registration_country", registered, "reference", field="registration_country")
            _fact(facts, "idc.allocation_country", allocated, "reference", field="allocation_country")


def _observation(facts, packet, projection, kind):
    """Start, end, source and headline value; returns the start time."""
    observation = _dict(packet, "observation")
    start = _utc((projection or {}).get("observed_start") or observation.get("start_utc"))
    end = _utc((projection or {}).get("observed_end") or observation.get("end_utc"))
    observed = public_prose.moment(start)
    _fact(facts, "obs.start", start, "telemetry", field="observed_start")
    _fact(facts, "obs.end", end, "telemetry", field="observed_end")
    if start:
        _fact(facts, "obs.date", start[:10], "telemetry", field="date")
    if start and end:
        _fact(
            facts, "obs.seconds", (public_prose.moment(end) - observed).total_seconds(), "derived", unit="s"
        )
    source = _dict(packet, "received").get("source")
    if isinstance(source, str) and re.fullmatch(r"[a-z_]{2,20}", source):
        _fact(facts, "obs.source", source, "telemetry")
    metric, unit = number(observation.get("metric_value")), _text(observation.get("metric_unit"), 32)
    if metric is not None and unit:
        _fact(facts, "obs.value", metric, "telemetry", unit=unit, field=FACT_NEIGHBOR_FIELDS.get(kind))
    episode, pattern = _dict(packet, "maneuver"), _dict(packet, "pattern")
    window = episode if episode.get("start_utc") else pattern
    _fact(facts, "ep.start", _utc(window.get("start_utc")), "derived", field="start_utc")
    _fact(facts, "ep.end", _utc(window.get("end_utc")), "derived", field="end_utc")
    return observed


def _measurements(facts, items):
    seen = set()
    for item in items:
        key, value = item.get("key"), item.get("value")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9_]{1,40}", key) or number(value) is None:
            continue
        scope = item.get("scope")
        if scope == "detection-rule":
            continue  # A rule's threshold stays on the evidence card.
        if scope == "reference-data" or key in public_events.RATED_WINDOW:
            continue  # A rated comparison's facts are the lim.* facts, each with its source.
        fact_id = f"m.{key}" if f"m.{key}" not in seen else f"m.{key}.2"
        seen.add(fact_id)
        _fact(
            facts,
            fact_id,
            value,
            SCOPE_BASIS.get(scope, "telemetry"),
            unit=item.get("unit") if isinstance(item.get("unit"), str) else None,
            field=key,
            scope=scope if isinstance(scope, str) else None,
            label=_text(item.get("label"), 120),
            at=_utc(item.get("at_utc")),
        )


def _season(facts, packet, observed):
    """The season record's comparison window: since the first saved report it covers.

    The window opens at the evidence's baseline_start, never at the meteorological
    season start, which may come weeks before the first saved report. The season is
    named only when the baseline covers it from its first day.
    """
    season = _dict(packet, "season_record")
    baseline = _iso_date(season.get("baseline_start")) or (_utc(season.get("baseline_start")) or "")[:10]
    if observed and baseline and baseline <= observed.date().isoformat():
        season_day, season_name = rules.season_start(observed.timestamp())
        whole = baseline <= season_day
        _fact(facts, "season.start", baseline, "derived", field="date", name=season_name if whole else None)
    previous = number(season.get("previous_max_gs"))
    _fact(facts, "season.previous", previous, "derived", unit="kn", field="gs")
    # How far the record passed the previous best: a margin of a few knots is the wind's to give.
    observation = _dict(packet, "observation")
    record = number(observation.get("metric_value"))
    if record is not None and previous and observation.get("metric_unit") == "kn":
        margin = round(record - previous, 1)
        if margin > 0:
            _fact(facts, "rec.margin_kn", margin, "telemetry", unit="kn", field="gs")


# The flight phases a norm is kept for, as the story says each.
NORM_PHASE_WORDS = {"level": "in level flight", "climb": "climbing", "descent": "descending"}


def _norm(facts, packet, kind):
    """The type's usual ground speed in these records (selection.type_norm), as baseline facts.

    A population range for one phase and altitude band, fixed when the record was first
    staged; it never vouches for this aircraft's own reading.
    """
    if kind not in rules.NORM_KINDS:
        return
    block = _dict(packet, "type_norm")
    low, high = number(block.get("low")), number(block.get("high"))
    floor, ceiling = number(block.get("band_floor_ft")), number(block.get("band_ceiling_ft"))
    phase = block.get("phase")
    if (
        None in (low, high, floor, ceiling)
        or not isinstance(phase, str)
        or phase not in NORM_PHASE_WORDS
        or not 0 < low <= high
    ):
        return
    for fact_id, value, unit in (
        ("norm.gs_low", low, "kn"),
        ("norm.gs_high", high, "kn"),
        ("norm.band_low", floor, "ft"),
        ("norm.band_high", ceiling, "ft"),
    ):
        _fact(facts, fact_id, value, "baseline", unit=unit, scope="these records", field="type_norm")
    _fact(facts, "norm.phase", phase, "baseline", scope="these records")


def _winds(facts, sources, packet, identity):
    """A winds-aloft day: its date, both medians and counts, their difference, the modelled wind
    when the engine attached one, and the finding's own aircraft, the fastest eastbound, as p.1."""
    block = _dict(packet, "winds_aloft")
    if not block:
        return
    _fact(facts, "wind.day", _iso_date(block.get("day")), "telemetry", field="date")
    for fact_id, key, unit in (
        ("wind.east_median", "east_median_kn", "kn"),
        ("wind.west_median", "west_median_kn", "kn"),
        ("wind.east_count", "east_addresses", "aircraft"),
        ("wind.west_count", "west_addresses", "aircraft"),
    ):
        _fact(facts, fact_id, number(block.get(key)), "telemetry", unit=unit)
    _fact(facts, "wind.asymmetry", number(block.get("asymmetry_kn")), "derived", unit="kn")
    wind = _dict(block, "wind_250_hpa")
    speed, direction = number(wind.get("speed_kn")), number(wind.get("direction_deg"))
    if speed is not None and direction is not None:
        cited = {"source": OPEN_METEO["id"], "at": wind.get("hour")}
        _fact(facts, "wind.open_meteo_speed", speed, "reference", unit="kn", **cited)
        _fact(facts, "wind.open_meteo_direction", direction, "reference", unit="degrees", **cited)
        _fact(facts, "wind.open_meteo_level", 250, "reference", unit="hPa", **cited)
        sources.append(dict(OPEN_METEO))
    _fact(facts, "p.1.registration", identity["registration"], "database", field="registration")
    _fact(facts, "p.1.callsign", identity["callsign"], "telemetry", field="callsign")
    _fact(facts, "p.1.hex", identity["hex"], "telemetry", field="icao_address")
    _type_fact(
        facts,
        "p.1.type",
        identity["type_code"],
        identity["description"],
        registration=identity["registration"],
    )


def _foreign_military(facts, packet):
    """The allocation State of a foreign military aircraft and what marked it military."""
    block = _dict(packet, "foreign_military")
    name = _text(block.get("country_name"), 60)
    if not name:
        return
    _fact(facts, "fm.country", name, "database", field="allocation_country", code=block.get("country"))
    basis = set(_list(block, "basis"))
    words = [text for key, text in FOREIGN_BASIS_WORDS.items() if key in basis]
    _fact(facts, "fm.basis", " and ".join(words) or None, "database")


def _first_operator(facts, sources, packet):
    """A first operator sighting: the operator, its designator, and the earlier days compared.

    The engine's operator name wins; without one, the pinned designator table names it.
    """
    block = _dict(packet, "first_sighting")
    designator = block.get("designator")
    if block.get("scope") != "operator" or not isinstance(designator, str):
        return
    entry = (table("airline_designators") or {}).get("designators", {}).get(designator) or {}
    name = display_name(_text(block.get("operator"), 120) or entry.get("name"))
    if name:
        _fact(facts, "fs.operator", name, "reference", field="operator", source=MICTRONICS["id"])
        sources.append(dict(MICTRONICS))
    _fact(facts, "fs.designator", designator, "reference", field="callsign")
    _fact(facts, "fs.coverage_days", number(block.get("coverage_days")), "derived", unit="days")
    _fact(facts, "fs.since", _iso_date(block.get("since")), "derived", field="date")


def _shape(facts, packet, kind):
    """An aerobatic or racetrack episode's headline metrics, each with its metric's unit."""
    episode = _dict(packet, "maneuver") or _dict(packet, "pattern")
    for fact_id, key in SHAPE_FACTS.get(kind, ()):
        unit = episode_evidence.METRICS[key][1]
        _fact(facts, fact_id, number(episode.get(key)), "derived", unit=unit, field=key)
    if kind == "aerobatic_practice":
        _fact(facts, "aer.basis", AEROBATIC_BASIS_WORDS.get(episode.get("basis")), "derived")


def _unregistered(facts, packet):
    """An address no registry lists: what it transmitted, the registries asked, and when it appeared."""
    block = _dict(packet, "unregistered")
    if not block:
        return
    callsign = identifier(block.get("callsign"), IDENTIFIERS["callsign"])
    _fact(facts, "unr.callsign", callsign, "telemetry", field="callsign")
    category = identifier(block.get("category"), r"[A-C][0-7]")
    _fact(
        facts,
        "unr.category",
        category,
        "telemetry",
        field="emitter_category",
        label=EMITTER_LABELS.get(category),
    )
    _fact(facts, "unr.registries", " and ".join(sorted(enrichment_rules.REGISTRIES)), "reference")
    first = _utc(_dict(packet, "observation").get("start_utc"))
    _fact(facts, "unr.first_seen", first, "telemetry", field="observed_start")


def _descent_class(code, entry):
    """The TYPICAL_DESCENT_FPM class of a type, or None: business jets by their curated category,
    then the Doc 8643 description (airliners are medium or heavy jets, turboprops turbine, light
    pistons light piston airplanes), then the curated category for a designator Doc 8643 lacks."""
    category = (entry or {}).get("category")
    if category in BUSINESS_JET_CATEGORIES:
        return "business_jet"
    info = reference.type_info(code) if code else None
    if info:
        if info["group"] or info["class"] != "airplane":
            return None
        engine, wake = info["engine_type"], info["wake_category"]
        if engine == "J" and wake in {"M", "H", "J"}:
            return "airliner"
        if engine == "T":
            return "turboprop"
        return "light_piston" if engine == "P" and wake == "L" else None
    return DESCENT_CATEGORIES.get(category)


def _repeat(facts, related, observed):
    """The latest story of this registration from an earlier day inside REPEAT_DAYS
    (repeat.previous): its kind's display title, never its headline, so the fact carries no
    figure to source, and its date; and its path. A story of the same day is a sibling."""
    if not observed:
        return
    latest = None
    for item in related or ():
        if not isinstance(item, dict):
            continue
        day, kind, path = _iso_date(item.get("date")), item.get("kind"), item.get("path")
        if not (day and isinstance(kind, str) and KIND.fullmatch(kind) and isinstance(path, str)):
            continue
        match = ARTICLE_PATH.fullmatch(path)
        if not match or not story_urls.is_new_slug(match["slug"]):
            continue
        if not 1 <= (observed.date() - date.fromisoformat(day)).days <= REPEAT_DAYS:
            continue
        title = KIND_TITLES.get(kind)
        if title and (latest is None or (day, path) > (latest[0], latest[1])):
            latest = (day, path, kind, title)
    if latest:
        day, path, kind, title = latest
        when = date.fromisoformat(day)
        value_ = f"{title} on {when.day} {story_urls.MONTH_NAMES[when.month - 1]} {when.year}"
        _fact(facts, "repeat.previous", value_, "derived", kind=kind, dates=[day])
        _fact(facts, "repeat.path", path, "derived")


def _registry_named(code):
    """(name, entry) for a disagreeing registry's type code: its curated name, else its class
    phrase with the code; None when the story cannot name it."""
    code = identifier(code, IDENTIFIERS["type_code"])
    entry = type_entry(code) if code else None
    if entry:
        return entry["name"], entry
    phrase = type_class_phrase(code) if code else None
    return (f"{phrase} {code}", None) if phrase else None


def _registry_check(facts, sources, packet, kind, identity, *, rated=False):
    """The second registry's settled answer, cited: which registry, for a mismatch story or a
    rated story whose comparison stands (rated: an unconflicted identity and a cited sheet), and,
    for a mismatch story only, the type it lists when that differs. A fact, never an
    explanation; a rated story whose registry disagrees is never published (curation.gates)."""
    if kind in enrichment_rules.REGISTRY_TYPE_KINDS:
        allowed = {"agrees", "disagrees"}
    else:
        allowed = {"agrees"} if kind in public_events.RATED_KINDS and rated else None
    check = _dict(packet, "type_check")
    source, state, hexcode = check.get("source"), check.get("state"), identity.get("hex")
    if source not in REGISTRY_PAGES or state not in (allowed or ()) or not hexcode:
        return
    named = _registry_named(check.get("type_code")) if state == "disagrees" else None
    # A differing type the story cannot name leaves the registry out altogether.
    if state == "disagrees" and not named:
        return
    source_id = "src." + source
    _fact(facts, "tc.source", source, "database", source=source_id, state=state)
    if named:
        name, entry = named
        _fact(
            facts,
            "tc.type",
            name,
            "database",
            source=source_id,
            code=identifier(check.get("type_code"), IDENTIFIERS["type_code"]),
            short=entry["short"] if entry else name,
            aliases=(entry["aliases"] or None) if entry else None,
        )
    sources.append(
        {
            "id": source_id,
            "publisher": source,
            # Neither registry has a public page per address: the page is its JSON API record.
            "title": f"{source} JSON API record for this address",
            "url": REGISTRY_PAGES[source].format(hex=hexcode),
        }
    )


def _watchlist(facts, sources, packet, projection, entities=None):
    """The Plane Alert DB entry: who or what it associates with the aircraft and why it lists it.

    The association, the list's own category name and its tags explain the entry; the
    neutral category says what kind of entry it is. Never the source row. `entities` are
    the cached Wikidata answers for entry links (the host's entities.json), keyed by link and
    association (entity_rules.cache_key); an organization the link leads to adds what
    Wikidata calls it.
    """
    # The validated public block: its commit is pinned and its link a public page.
    watchlist = (projection or {}).get("watchlist") or public_events._watchlist(packet, strict=False)
    if not watchlist:
        return
    link = watchlist.get("link")
    association, category = watchlist["association"], watchlist["category"]
    # "Private" or "Unknown" names nobody; the category and tags carry such an entry.
    if association.lower() not in watchlists.VAGUE_ASSOCIATIONS:
        # The entry's own link backs the association it names.
        source = PLANE_ALERT_ID + ".link" if link else PLANE_ALERT_ID
        _fact(
            facts,
            "wl.association",
            display_name(association),
            "community-list",
            field="association_label",
            source=source,
        )
        if link and watchlists.describable(association, watchlist.get("list_category"), category):
            entity = (entities or {}).get(entity_rules.cache_key(link, association))
            _watchlist_who(facts, sources, entity, association)
    if category != watchlists.GENERIC_CATEGORY:
        _fact(
            facts, "wl.category", category, "community-list", field="neutral_category", source=PLANE_ALERT_ID
        )
    named = watchlist.get("list_category")
    if named and named.lower() != category.lower():
        _fact(facts, "wl.list_category", named, "community-list", source=PLANE_ALERT_ID)
    if named in watchlists.CATEGORY_NOTES:
        # What the category collects: the reason the list carries the aircraft.
        _fact(facts, "wl.category_note", watchlists.CATEGORY_NOTES[named], "reference", source=PLANE_ALERT_ID)
    for key in ("tags", "group", "description"):
        if watchlist.get(key):
            _fact(facts, "wl." + key, watchlist[key], "community-list", source=PLANE_ALERT_ID)
    if link:
        sources.append(
            {
                "id": PLANE_ALERT_ID + ".link",
                "publisher": urlsplit(link).hostname,
                "title": "The link in the Plane Alert DB entry",
                "url": link,
            }
        )
    sources.append(
        {
            "id": PLANE_ALERT_ID,
            "publisher": "Plane Alert DB",
            "title": "Plane Alert DB community watchlist",
            "url": "https://github.com/sdr-enthusiasts/plane-alert-db/blob/"
            + watchlist["source_commit"]
            + "/plane-alert-db.csv",
        }
    )


def _watchlist_who(facts, sources, entity, association):
    """What Wikidata calls the organization an entry links to, as wl.who.

    Only an answer looked up for the association the entry still carries counts, and only
    while its description passes entity_rules.description_issue, so an answer cached before
    a rule existed is judged by it. The label is one more name for that association; the
    description stays text the story checks read like any other.
    """
    if (
        not isinstance(entity, dict)
        or entity.get("state") != "available"
        or not isinstance(entity.get("association"), str)
        or entity["association"].casefold() != association.casefold()
    ):
        return
    item, label, description = entity.get("item"), entity.get("label"), entity.get("description")
    if not (
        isinstance(item, str)
        and entity_rules.ITEM.fullmatch(item)
        and isinstance(label, str)
        and label.strip()
        and isinstance(description, str)
        and description.strip()
        and entity_rules.description_issue(description, (label, association, *(entity.get("aliases") or ())))
        is None
    ):
        return
    source_id = f"src.wikidata.{item}"
    revision = entity.get("revision")
    revised = f", revision {revision}" if isinstance(revision, int) and not isinstance(revision, bool) else ""
    _fact(
        facts,
        "wl.who",
        display_name(f"{label}, {description}"),
        "reference",
        source=source_id,
        label=display_name(label),
    )
    sources.append(
        {
            "id": source_id,
            "publisher": "Wikidata",
            "title": f"Wikidata item {item}{revised}, CC0",
            "url": f"https://www.wikidata.org/wiki/{item}",
        }
    )


def _limit_source(sheet, source_id):
    """A cited certificate sheet ({title, url}) as a story source, or None without a public one.

    The dataset names a sheet by its title and URL; its publisher is read from the host
    (publishers.py), or is the host itself for one not listed there.
    """
    sheet = sheet if isinstance(sheet, dict) else {}
    title, url = _text(sheet.get("title"), 200), sheet.get("url")
    if not title or not isinstance(url, str) or not PROFILE_URL.fullmatch(url):
        return None
    host = urlsplit(url).hostname
    return {"id": source_id, "publisher": publishers.publisher(host) or host, "title": title, "url": url}


def _rated_limits(facts, sources, code, allowed):
    """The type's certified limits from the cited rated-limits dataset, those `allowed` only.

    A glitch story states what the type is certified to instead of why a reading
    disagrees with it. Each limit cites the sheet it was read from (limits.source_for):
    the entry's own sheet as src.limits.<code>, a different one under the fact's name.
    """
    entry = reference.limits(code)
    if not isinstance(entry, dict):
        return
    default = _limit_source(entry.get("source"), f"src.limits.{code}")
    added = {item["id"] for item in facts}
    for key, fact_id, unit, field in RATED_LIMIT_FIELDS:
        value = number(entry.get(key))
        # Vmo and Vne both give lim.speed; a type has one or the other.
        if value is None or value <= 0 or fact_id in added or fact_id not in allowed:
            continue
        sheet = limits.source_for(entry, key)
        source = (
            default
            if default and sheet.get("url") == default["url"] and sheet.get("title") == default["title"]
            else _limit_source(sheet, f"src.limits.{code}.{fact_id.removeprefix('lim.')}")
        )
        if source is None:
            continue
        _fact(facts, fact_id, value, "reference", unit=unit, field=field, source=source["id"])
        added.add(fact_id)
        if source["id"] not in {item["id"] for item in sources}:
            sources.append(source)


def _rated_figure(packet, code):
    """(sheet source, limit name, figure) a rated finding was judged against, or None without a
    cited sheet for this type or a figure."""
    block = _dict(packet, "rated_limit")
    # The packet's sheet is already the one its binding limit is read from (limits.source_for).
    source = _limit_source(block.get("source"), f"src.limits.{code}") if block.get("type") == code else None
    if source is None:
        return None
    sustained = _dict(block, "sustained")
    speed = packet.get("kind") == "above_rated_speed"
    name = (sustained.get("binding_limit") or block.get("limit_name")) if speed else block.get("limit_name")
    figure = block.get("mmo") if name == "mmo" else block.get("limit")
    if name not in RATED_LIMIT_UNITS or number(figure) is None:
        return None
    return source, name, figure


def _rated_comparison(facts, sources, packet, code):
    """A rated finding's comparison: the certified figure and its sheet, the reports over it,
    the margin, and, for a speed, the decisive report's airspeed, Mach number, and wind.

    lim.limit is the figure the reading was judged against: for a speed, the limit that
    binds at the decisive report's altitude (Vmo or Vne, or Mmo high up). lim.reading and
    lim.margin are floors over every report counted over the limit; the speed's other
    figures belong to the decisive report, the one closest to the limit, so a story's
    "this ground speed works out to that airspeed" is one report's calculation.
    lim.indicated is that airspeed as an equivalent airspeed, the figure Vmo and Vne are
    stated in (limits.speed_comparison gives none when Mmo binds); _registry_check names the
    registry that confirmed the listed type. Nothing here is a cause or a judgement.
    """
    compared = _rated_figure(packet, code)
    if compared is None:
        return
    source, name, figure = compared
    block = _dict(packet, "rated_limit")
    speed = packet.get("kind") == "above_rated_speed"
    sustained = _dict(block, "sustained")
    unit, field = RATED_LIMIT_UNITS[name]
    _fact(facts, "lim.limit", figure, "reference", unit=unit, field=field, source=source["id"])
    sources.append(source)
    if speed and sustained:
        basis = sustained.get("airspeed_basis")
        modelled = basis == "modelled_wind"
        weather = OPEN_METEO["id"] if modelled else None
        _fact(
            facts, "lim.reading", number(sustained.get("ground_speed_kt")), "telemetry", unit="kn", field="gs"
        )
        _fact(
            facts,
            "lim.decisive_gs",
            number(sustained.get("decisive_ground_speed_kt")),
            "telemetry",
            unit="kn",
            field="gs",
        )
        # A reported true airspeed is the aircraft's own; an estimate rests on the modelled wind.
        _fact(
            facts,
            "lim.airspeed",
            number(sustained.get("airspeed_kt")),
            "derived" if modelled else "telemetry",
            unit="kn",
            field="estimated_airspeed" if modelled else "tas",
            source=weather,
        )
        _fact(
            facts,
            "lim.mach",
            number(sustained.get("mach")),
            "derived",
            unit="Mach",
            field="mach_estimate",
            source=weather,
        )
        _fact(
            facts,
            "lim.indicated",
            number(sustained.get("equivalent_airspeed_kt")),
            "derived",
            unit="kn",
            field="equivalent_airspeed",
            source=weather,
        )
        _fact(facts, "lim.margin", number(sustained.get("margin_percent")), "derived", unit="%")
        wind = _dict(sustained, "wind") if modelled else {}
        _fact(
            facts,
            "lim.wind",
            number(wind.get("speed_kt")),
            "derived",
            unit="kn",
            field="wind",
            direction=number(wind.get("direction_deg")),
            source=weather,
        )
        if weather:
            sources.append(dict(OPEN_METEO))
    elif sustained:
        _fact(
            facts,
            "lim.reading",
            number(sustained.get("altitude_ft")),
            "telemetry",
            unit="ft",
            field="alt_baro",
        )
        _fact(facts, "lim.margin", number(sustained.get("margin_ft")), "derived", unit="ft")
    _fact(facts, "lim.reports", number(sustained.get("reports")), "derived", unit="reports")
    _fact(facts, "lim.seconds", number(sustained.get("seconds")), "derived", unit="s")


def build(packet, projection=None, *, place=None, airport=None, profiles=None, entities=None, related=()):
    """Facts for one observation from allowlisted packet and projection fields.

    place:   {"name": "Coleman, Michigan", "short": "Coleman, MI", "state": "Michigan"} or None.
    airport: {"name", "ident", "nm", "at"} as airports.nearest() gives it, or None.
    profiles: {curated type slug: {"facts": [{"id", "text", "source": {...}}], "retrieved_at"}},
             as weekly_research.story_profiles() gives them.
    entities: {entity_rules.cache_key(entry link, association): cached Wikidata answer}, as the
             host's entities.json holds them.
    related: [{"date", "kind", "path", "title"}] for the same registration's earlier stories,
             as the autopilot gives them (autopilot.related_stories).
    """
    if not isinstance(packet, dict):
        raise ValueError("Story facts need an evidence packet")
    kind = packet.get("kind")
    if not isinstance(kind, str) or not KIND.fullmatch(kind):
        raise ValueError("Story facts need an allowlisted kind")
    projection = projection if isinstance(projection, dict) else None
    facts, sources = [], []
    # Identity: who the database and the transponder say this is.
    identity = _identity(packet, projection)
    checks = _dict(packet, "identity_checks")
    conflict = checks.get("conflict") is True
    received = _dict(packet, "received")
    archived = received.get("label_provenance") == "archived_label"
    _fact(facts, "id.registration", identity["registration"], "database", field="registration")
    _fact(
        facts,
        "id.callsign",
        identity["callsign"],
        "archive-label" if archived else "telemetry",
        field="callsign",
    )
    _fact(facts, "id.hex", identity["hex"], "telemetry", field="icao_address")
    _fact(facts, "id.type_code", identity["type_code"], "database", field="type_code")
    entry = _type_fact(
        facts,
        "id.type",
        identity["type_code"],
        identity["description"],
        registration=identity["registration"],
    )
    if not entry:
        _type_class(facts, sources, identity["type_code"])
    emitter = identifier(received.get("emitter_category"), r"[A-C][0-7]")
    if emitter:
        _fact(
            facts,
            "id.emitter",
            emitter,
            "telemetry",
            field="emitter_category",
            label=EMITTER_LABELS.get(emitter),
        )
    _operator(facts, sources, identity, checks)
    if not conflict:
        _nationality(facts, identity)
    # Observation: when, how and the headline value.
    observed = _observation(facts, packet, projection, kind)
    items = _measurement_items(packet, projection)
    _measurements(facts, items)
    _codes(facts, sources, packet, kind, items)
    known_tags = set(episode_evidence.CONTEXT_NOTES)
    tags = [*_list(packet, "context_tags"), *_list(_dict(packet, "maneuver"), "context_tags")]
    _fact(
        facts,
        "tags",
        sorted({tag for tag in tags if isinstance(tag, str) and tag in known_tags}) or None,
        "derived",
    )
    metadata = _dict(packet, "metadata")
    if _dict(metadata, "database_flags").get("military") is True:
        _fact(facts, "db.military", True, "database")
    year = number(metadata.get("database_year"))
    if year is not None and observed and 1900 <= year <= observed.year and year == int(year) and not conflict:
        earliest = reference.earliest_year(identity["type_code"]) if identity["type_code"] else None
        # An airframe's age is a story only from MIN_AGE_YEARS; a younger one's is noise.
        if (earliest is None or year >= earliest) and observed.year - int(year) >= MIN_AGE_YEARS:
            _fact(facts, "age.built", int(year), "database", field="database_year")
            _fact(facts, "age.years", observed.year - int(year), "derived", unit="years")
    # Context: track, place and neighbouring readings.
    _track(facts, packet)
    place_values = _place(facts, place)
    _airport(facts, airport, kind)
    _neighbors(facts, packet, kind, public_prose.moment(received.get("saved_readings_at_utc")))
    # Kind-specific facts. A first sighting is a first in these records; a count of the
    # days without one reads as filler.
    _fact(facts, "ctx.kind", KIND_CONTEXT.get(kind), "reference")
    if kind in RECORD_KINDS:
        _season(facts, packet, observed)
    if kind == "rapid_descent" and not conflict:
        descent_class = _descent_class(identity["type_code"], entry)
        if descent_class:
            # The range in words for display, and its two ends as quotable rates.
            low, high = TYPICAL_DESCENT_RANGES[descent_class]
            _fact(facts, "desc.typical_fpm", TYPICAL_DESCENT_FPM[descent_class], "reference")
            _fact(facts, "desc.typical_fpm_low", low, "reference", unit="ft/min")
            _fact(facts, "desc.typical_fpm_high", high, "reference", unit="ft/min")
    if kind == "winds_aloft_day":
        _winds(facts, sources, packet, identity)
    if kind == "foreign_military":
        _foreign_military(facts, packet)
    if kind == "first_operator_here":
        _first_operator(facts, sources, packet)
    if kind == "unregistered_address":
        _unregistered(facts, packet)
    _shape(facts, packet, kind)
    _repeat(facts, related, observed)
    limits = _limits(packet, projection)
    if kind == "notable_variant" and identity["description"]:
        variant = reference.notable_variant(identity["description"])
        if variant and isinstance(variant.get("label"), str):
            _fact(facts, "variant.label", variant["label"], "reference", field="type_description")
            if isinstance(variant.get("note"), str):
                limits = [variant["note"], *limits]
    _watchlist(facts, sources, packet, projection, entities)
    rated = kind in public_events.RATED_KINDS and identity["type_code"] and not conflict
    _registry_check(
        facts,
        sources,
        packet,
        kind,
        identity,
        rated=bool(rated and _rated_figure(packet, identity["type_code"])),
    )
    _norm(facts, packet, kind)
    if kind in RATED_LIMIT_KINDS and identity["type_code"] and not conflict:
        _rated_limits(facts, sources, identity["type_code"], RATED_LIMIT_KINDS[kind])
    if rated:
        _rated_comparison(facts, sources, packet, identity["type_code"])
    if kind == "identity_conflict":
        _identity_conflicts(facts, checks, archived=archived)
        observation = _dict(packet, "observation")
        start, end = _utc(observation.get("start_utc")), _utc(observation.get("end_utc"))
        if archived and observation.get("qualifying_count") == 1 and start is not None and start == end:
            _fact(facts, "idc.samples", 1, "derived", unit="samples")
    if kind == "stratospheric_balloon":
        _balloon_spike(facts, packet)
    _participants(facts, packet, projection, start=2 if kind == "winds_aloft_day" else 1)
    if kind not in SUBJECTLESS_KINDS:
        _profiles(facts, sources, profiles, entry, kind)
    result = {
        "version": FACTS_VERSION,
        "kind": kind,
        "facts": facts,
        "limits": list(dict.fromkeys(limits)),
        "hypotheses": list(HYPOTHESES.get(kind, ())),
        "sources": list({item["id"]: item for item in sources}.values()),
    }
    result["digest"] = digest(result)
    result["context"] = context(result, place=place_values)
    return result


def profile_facts(kind, listed, description=None):
    """Story facts holding only a published story's type and its listed type-profile sentences,
    from its fact list ({fact id: value}); enough for public_prose.bound_facts to say which of
    those sentences the story's text relies on. The record's database description, when given,
    names the type as build() did, its alias included."""
    facts = []
    code = listed.get("id.type_code")
    if isinstance(code, str):
        _fact(facts, "id.type_code", code, "database", field="type_code")
        if not _type_fact(facts, "id.type", code, description or listed.get("id.type")):
            _type_class(facts, [], code)
    for key, text in sorted(listed.items()):
        if PROFILE_ID.fullmatch(key) and isinstance(text, str):
            _fact(facts, key, text, "reference", source="src." + key[3:], field="profile")
    return {"kind": kind, "facts": facts, "limits": [], "hypotheses": [], "sources": []}


def digest(facts):
    """sha256 of the canonical facts, excluding the digest and the routing context derived from them."""
    content = {key: value for key, value in facts.items() if key not in {"digest", "context"}}
    return hashlib.sha256(_canonical(content).encode()).hexdigest()


def fact_map(facts):
    """{id: fact} for a build() result."""
    return {item["id"]: item for item in facts.get("facts", []) if isinstance(item, dict) and "id" in item}


def value(facts, fact_id, default=None):
    item = fact_map(facts).get(fact_id)
    return item["value"] if item else default


def context(facts, *, place=None):
    """Public routing for the site build: topic, section, type hub, airframe slug, operator, place, country.

    Exactly the snapshot's routing schema; slugs come from the facts themselves.
    """
    values = fact_map(facts)
    topic, section = topic_for(facts.get("kind"))
    if facts.get("kind") in SUBJECTLESS_KINDS:
        # The aircraft a subjectless story names are examples, not its subject.
        values = {key: item for key, item in values.items() if not key.startswith(("id.", "op.", "reg."))}
    kind_type = values.get("id.type")
    type_value = None
    if kind_type and kind_type.get("slug") and not kind_type.get("group"):
        type_value = {"code": kind_type.get("code"), "name": kind_type["value"], "slug": kind_type["slug"]}
        if kind_type.get("same_as") and SAME_AS.fullmatch(kind_type["same_as"]):
            # The type's verified encyclopedia article: the site names it as the subject's sameAs.
            type_value["same_as"] = kind_type["same_as"]
        # The type's profile facts, which the page's type box lists less the ones the text quotes
        # (publication.type_box); every story lists their values.
        profile = [item["id"] for item in facts.get("facts", []) if item.get("id", "").startswith("tp.")]
        if profile:
            type_value["profile"] = profile
    registration = values.get("id.registration", {}).get("value")
    operator = values.get("op.callsign")
    operator_value = None
    if operator:
        operator_value = {
            "name": operator["value"],
            "slug": operator["slug"],
            "basis": "callsign_designator",
            "designator": operator["designator"],
        }
    return {
        "topic": topic,
        "section": section,
        "type": type_value,
        "registration_slug": story_urls.token(registration) if registration else None,
        "operator": operator_value,
        "place": (place or {}).get("name") if place else values.get("track.place", {}).get("value"),
        "country": values.get("reg.country", {}).get("value"),
    }
