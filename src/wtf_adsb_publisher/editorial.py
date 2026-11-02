"""Deterministic story text from story facts: angles, rotation, SEO titles and a variety guard.

render() turns story_facts.build() output into a complete, publishable story. The
template text alone must pass public_prose.validate_story_fields, the variety guard
and the indexability gate; AI prose later only improves it. The angle library lives
at the end of this module.

Choice is deterministic. Angles are ordered by recent use, tier and a hash of the
facts digest, so the AI writer sees the same order through angle_options(). Variants
rotate by a hash of the episode key, and each part (headline, dek, opener, body
sentences) takes the first variant that the variety guard accepts against recent
stories; the headline, dek and opener are chosen together so that none repeats the
part before it (_parts). When nothing passes, the least similar option is used and
the result is reported as relaxed; the guard never blocks publication.
"""

import functools
import hashlib
import math
import re
from dataclasses import KW_ONLY, dataclass, field, replace
from datetime import date

from . import kinds, limits, maneuvers, public_events, public_prose, rules, story_facts, story_urls
from .public_prose import MONTH_NAMES, number, series

# The writing policy: a bump lets the writer replace AI prose written under an older one.
PROSE_POLICY = 6
# A story lists at most this many facts; the snapshot allowlist refuses more.
MAX_STORY_FACTS = 30
# Identity and date facts every story keeps, so the variety guard can date it and
# normalize its names.
BASE_FACTS = ("obs.start", "obs.date", "id.type_code", "id.type", "id.registration", "id.callsign", "id.hex")
# Short forms a story carries beside the fact they shorten.
DERIVED_FACTS = {
    "track.place.short": "track.place",
    "track.place.state": "track.place",
    "op.callsign.short": "op.callsign",
}
STORY_KEYS = public_events.STORY_FIELDS
# Variety windows and thresholds (design 4.6).
WINDOW_ANY_DAYS = 7
WINDOW_SAME_DAYS = 30
BODY_HISTORY = 20
HEADLINE_JACCARD = 0.6
DEK_JACCARD = 0.4
BODY_CONTAINMENT = 0.5
BODY_JACCARD = 0.35
OPENER_TOKENS = 6
# Statute miles in one nautical mile; event-media's map captions use the same figure.
MILES_PER_NM = 1 / public_prose.STATUTE_MILE_NM
# Counts and ordinals read as words up to twenty and tenth.
SMALL_NUMBERS = {
    value: word for word, value in public_prose.WORD_VALUES.items() if 2 <= value <= 20 and word != "dozen"
}
ORDINALS = {value: word for word, value in public_prose.ORDINAL_WORDS.items() if 2 <= value <= 10}
# Country names read with "the" in running text.
THE_COUNTRIES = re.compile(
    r"(?:United |Netherlands|Philippines|Bahamas|Czech Republic|Dominican Republic|Central African Republic"
    r"|Gambia|Maldives|Marshall Islands|Solomon Islands|Cayman Islands|Turks and Caicos|Falkland Islands"
    r"|British Virgin Islands|Comoros|Seychelles)"
)


@dataclass(frozen=True)
class Angle:
    """One way into a story. Text uses {slot}, {slot|cap}, {slot|a} and [optional parts].

    An angle applies when every slot it requires has a value and none it excludes has one.
    Only a TCAS advisory's explainer may ask a question in its headline.
    """

    id: str
    focus: str
    headlines: tuple
    deks: tuple
    openers: tuple
    body: tuple
    _: KW_ONLY
    tier: int = 2
    requires: tuple = ()
    excludes: tuple = ()

    def usable(self, slots):
        return all(slots.get(name) for name in self.requires) and not any(
            slots.get(name) for name in self.excludes
        )


@dataclass
class Report:
    """How render() reached its story: whether variety was relaxed or the fallback was used.

    `fallback` marks a story from the generic angles or the plain record. A story is
    `publishable` unless even the plain record failed the story checks; `issues` then
    lists them, and render_with_report()'s caller must not publish it.
    """

    variety_relaxed: bool = False
    issues: list = field(default_factory=list)
    fallback: bool = False
    publishable: bool = True


class StoryUnavailable(ValueError):
    """No story for these facts passes the story checks, so there is nothing to publish."""


def cap_story_facts(values, limit=MAX_STORY_FACTS):
    """At most `limit` story facts: identity and date first, then type background, then the rest.

    A short form stays only beside the fact it shortens. The choice and the order are
    deterministic, so the same story always lists the same facts.
    """
    if len(values) <= limit:
        return values

    def rank(key):
        parent = DERIVED_FACTS.get(key, key)
        tier = 0 if parent in BASE_FACTS else 1 if parent.startswith("tp.") else 2
        return (tier, parent, key)

    kept = set(sorted(values, key=rank)[:limit])
    return {key: values[key] for key in sorted(kept) if DERIVED_FACTS.get(key, key) in kept}


def story_fact_values(facts, fact_ids, *, cap=True):
    """{fact id: value} for the given facts plus identity and date, as a story lists them.

    A place keeps its short form and state, an operator its short name. Template and AI
    stories both list their facts this way; at most MAX_STORY_FACTS when capped.
    """
    values = story_facts.fact_map(facts)
    result = {}
    for fact_id in sorted({*BASE_FACTS, *fact_ids}):
        # Context such as "tags" guides the text but is not a fact a story can list.
        if fact_id not in values or not public_events.FACT_ID.fullmatch(fact_id):
            continue
        result[fact_id] = values[fact_id]["value"]
        if fact_id == "track.place":
            result["track.place.short"] = values[fact_id].get("short")
            result["track.place.state"] = values[fact_id].get("state")
        elif fact_id == "op.callsign":
            result["op.callsign.short"] = values[fact_id].get("short")
    result = {key: value for key, value in result.items() if value is not None}
    return cap_story_facts(result) if cap else result


# ---------------------------------------------------------------- formatting


def _thousands(value, decimals=0):
    if decimals:
        return f"{value:,.{decimals}f}".rstrip("0").rstrip(".")
    return f"{round(value):,}"


def _exact(value):
    """A value as recorded: whole numbers without decimals, others with one."""
    return _thousands(value) if float(value).is_integer() else _thousands(value, 1)


def spelled(count):
    return SMALL_NUMBERS.get(count, f"{count:,}")


# An organization's name takes "the" in running text when it ends in a common noun from this
# list, its head word ("the Federal Aviation Administration", "the Yankee Air Museum"); any
# other name, such as "Acme Air" or "Example Aviation Holdings", stands without one.
ORGANIZATION_HEADS = frozenset(
    {
        "Administration", "Agency", "Association", "Authority", "Board", "Bureau", "Commission",
        "Company", "Corporation", "Council", "Department", "Force", "Foundation", "Group", "Guard",
        "Institute", "Lightning", "Museum", "Service", "Society", "Trust", "University",
    }
)  # fmt: skip


def article(text):
    """a or an for a noun phrase, including designators read letter by letter (an A380, an MD-11)."""
    if not text:
        return text
    first = text.split()[0]
    digits = re.match(r"\d+", first)
    if digits:
        # Read aloud: an 8th, an 11th, an 18th, an 80th; a 12th.
        count = digits.group()
        spoken_an = (
            count.startswith("8")
            or count in {"11", "18"}
            or (len(count) in {4, 5} and count[: len(count) - 3] in {"11", "18"})
        )
        return ("an " if spoken_an else "a ") + text
    letters = re.match(r"[A-Za-z]+", first)
    if letters and letters.group().isupper() and (len(letters.group()) <= 3 or re.search(r"\d", first)):
        use_an = letters.group()[0] in "AEFHILMNORSX"
    else:
        lowered = first.lower()
        # "an unusual", but "a United", "a unique", "a European", "a useful", "a one-off": these
        # vowels are said as "you" or "won".
        use_an = (lowered[0] in "aeio" or lowered.startswith(("ul", "un", "um"))) and not lowered.startswith(
            YOU_SOUNDS
        )
    return ("an " if use_an else "a ") + text


# Word starts whose vowel is said as a consonant, so they take "a" (article).
YOU_SOUNDS = ("unic", "unif", "unio", "uniq", "unit", "univ", "eu", "use", "usu", "uti", "one", "once")


def described(text):
    """A description with "a" or "an" before it, unless it already opens with an article or
    "one of" ("the largest airline", "one of the oldest museums")."""
    return text if re.match(r"(?:the|a|an|one of)\s", text, re.I) else article(text)


def with_the(name):
    return f"the {name}" if isinstance(name, str) and THE_COUNTRIES.match(name) else name


def _moment(value):
    return public_prose.moment(value) if isinstance(value, str) else None


def _day(value):
    moment = _moment(value)
    return moment.date() if moment else None


def _month(value):
    day = _day(value)
    return MONTH_NAMES[day.month - 1] if day else None


def day_month(value):
    day = _day(value)
    return f"{day.day} {_month(value)}" if day else None


def long_date(value):
    day = _day(value)
    return f"{day.day} {_month(value)} {day.year}" if day else None


def short_date(value):
    day = _day(value)
    return f"{day.day} {_month(value)[:3]} {day.year}" if day else None


def clock(value):
    moment = _moment(value)
    return f"{moment:%H:%M} UTC" if moment else None


def duration(seconds):
    """Spoken duration that the prose checks accept for the recorded number of seconds.

    A figure without "about" stays within a twentieth of the recorded value.
    """
    seconds = number(seconds)
    if seconds is None or seconds <= 0:
        return None
    if seconds < 90:
        whole = round(seconds)
        unit = "second" if whole == 1 else "seconds"
        if whole and abs(seconds - whole) <= 0.05 * seconds:
            return f"{whole} {unit}"
        if whole and abs(seconds - whole) <= 0.18 * seconds:
            return f"about {whole} {unit}"
        return f"{seconds:.1f} seconds"
    minutes = seconds / 60
    if minutes < 100:
        rounded = int(minutes + 0.5)
        if abs(minutes - rounded) < 0.05:
            return f"{rounded} minutes"
        if minutes < 10 and abs(minutes * 2 - round(minutes * 2)) < 0.05:
            return f"{round(minutes * 2) / 2:g} minutes"  # 150 seconds read as 2.5 minutes.
        # The checks accept "about" within a fifth of the recorded value.
        if abs(minutes - rounded) <= 0.18 * minutes:
            return f"about {rounded} minutes"
        return f"{round(seconds)} seconds"
    hours = seconds / 3600
    rounded = round(hours)
    if abs(hours - rounded) < 0.02:
        return f"{rounded} hours"
    if abs(hours - rounded) <= 0.1 * hours:
        return f"about {rounded} hours"
    tenths = round(hours, 1)
    return f"{tenths:g} hours" if abs(hours - tenths) < 0.01 else f"about {tenths:g} hours"


def miles(nm):
    """A distance in statute miles from one in NM, from one statute mile up: "about 18 miles"
    from three miles, and below that "about 2 miles" within 0.05 of a whole number, else one
    decimal with no hedge ("1.7 miles")."""
    nm = number(nm)
    if nm is None or nm * MILES_PER_NM < 1:
        return None
    statute = nm * MILES_PER_NM
    whole = round(statute)
    if statute < 3 and abs(statute - whole) > 0.05 and round(statute, 1) != whole:
        return f"{statute:.1f} miles"
    return f"about {whole} mile" if whole == 1 else f"about {whole} miles"


def rate_phrase(value):
    """A vertical rate with its direction: a 250 ft/min descent, a 120 ft/min climb or 0 ft/min."""
    rate = number(value)
    if rate is None:
        return None
    text = unit_value(rate, "ft/min")
    return text if rate == 0 else f"a {text} {'descent' if rate < 0 else 'climb'}"


def unit_value(value, unit, *, rounded=False, adjective=False, signed=False):
    """1,063.9 knots, 39,000 ft, 7,600 ft/min; adjective form 1,064-knot.

    A level reads with its sign (-500 ft) when `signed`; a change or rate gives its
    direction in words, so its magnitude is enough.
    """
    value = number(value)
    if value is None:
        return None
    magnitude = abs(value)
    text = _thousands(magnitude) if rounded else _exact(magnitude)
    if signed and value < 0:
        text = "-" + text
    if unit in {"kn", "kt", "kn difference"}:
        return f"{text}-knot" if adjective else f"{text} knots"
    if unit in {"ft", "ft regained", "ft apart"}:
        return f"{text}-foot" if adjective else f"{text} ft"
    if unit == "ft/min":
        return f"{text} ft/min"
    if unit in {"NM", "nm"}:
        return f"{text} NM"
    if unit == "s":
        return duration(magnitude)
    if unit == "minutes":
        return duration(magnitude * 60)
    return None


# ---------------------------------------------------------------- slots


ALTITUDE_FIELDS = (
    ("alt_baro", "a pressure altitude of {}"),
    ("archive_alt", "an altitude of {}"),
    ("alt_geom", "a geometric altitude of {}"),
)
FIELD_WORDS = {
    "gs": "ground speed",
    "alt_baro": "pressure altitude",
    "archive_alt": "altitude",
    "alt_geom": "geometric altitude",
    "baro_rate": "vertical rate",
    "geom_rate": "vertical rate",
}
# Altitude levels keep their sign; a change or separation gives its direction in words.
LEVEL_FIELDS = frozenset({"alt_baro", "alt_geom", "archive_alt", "track_alt"})
# Kinds whose story is the emitter category, the altitude gap or the position jump: only they name it.
EMITTER_KINDS = kinds.where(emitter=True)
# Episode and pattern metrics: (slot, unit); counts read as words.
METRIC_SLOTS = {
    "duration_seconds": ("duration", "s"),
    "leg_count": ("legs", "count"),
    "leg_spacing_nm": ("leg_spacing", "NM"),
    "leg_length_nm": ("leg_length", "NM"),
    "climb_gain_ft": ("gain", "ft"),
    "max_altitude_ft": ("max_alt", "ft"),
    "median_ground_speed_kt": ("median_gs", "kn"),
    "min_separation_nm": ("sep", "NM"),
    "vertical_separation_ft": ("vsep", "ft"),
    "response_rate_change_fpm": ("rate_change", "ft/min"),
    "peak_rate_fpm": ("peak_rate", "ft/min"),
    "altitude_change_ft": ("change", "ft"),
    "start_altitude_ft": ("start_alt", "ft"),
    "end_altitude_ft": ("end_alt", "ft"),
    "aircraft_count": ("affected", "count"),
}


class Slots(dict):
    """Rendered values for templates, plus the fact ids each slot rests on."""

    def __init__(self):
        super().__init__()
        self.sources = {}

    def put(self, name, value, *fact_ids):
        if value is None or value == "":
            return
        self[name] = value
        self.sources[name] = fact_ids


class _Values:
    """Story facts by id: value(id) reads a fact's value, value.item(id) the whole fact."""

    def __init__(self, facts):
        self.items = story_facts.fact_map(facts)

    def __call__(self, fact_id):
        item = self.items.get(fact_id)
        return item.get("value") if item else None

    def item(self, fact_id):
        return self.items.get(fact_id) or {}


def build_slots(facts):
    value = _Values(facts)
    kind = facts.get("kind", "")
    slots = Slots()
    for build in (
        _identity_slots,
        _time_slots,
        _reading_slots,
        _track_slots,
        _age_slots,
        _value_slots,
        _code_slots,
        _metric_slots,
        _record_slots,
        _norm_slots,
        _shape_slots,
        _comparison_slots,
    ):
        build(slots, value, kind)
    for tag in value("tags") or []:
        slots.put("tag_" + tag, "yes", "tags")
    if value("db.military"):
        slots.put("military_flag", "yes", "db.military")
    if kind in public_prose.RECORD_KINDS and value("obs.value") is not None:
        # The record's one hedge (record_hedge) stands in for the evidence card's caveats.
        slots.put("speed_record", "yes", "obs.value")
    elif kind not in public_prose.RATED_KINDS:
        # A rated story's hedge (rated_hedge_*) stands in for the card's caveats the same way.
        slots.put("limit", _best_limit(kind, facts.get("limits") or []), "limits")
    # A fixed kind's one headline (public_prose.FIXED_HEADLINES), which its angles carry as
    # "{fixed_headline}"; the checks require the same text.
    if fixed := public_prose.fixed_headline(facts):
        slots.put("fixed_headline", fixed[0], *fixed[1])
    return slots


def _identity_slots(slots, value, kind):
    registration, callsign, address = value("id.registration"), value("id.callsign"), value("id.hex")
    slots.put("reg", registration, "id.registration")
    slots.put("call", callsign, "id.callsign")
    slots.put("hex", address, "id.hex")
    if callsign and kind in story_urls.CALLSIGN_FIRST:
        slots.put("ident", callsign, "id.callsign")
        slots.put("ident_named", callsign, "id.callsign")
    elif registration:
        slots.put("ident", registration, "id.registration")
        slots.put("ident_named", registration, "id.registration")
    elif callsign:
        slots.put("ident", callsign, "id.callsign")
        slots.put("ident_named", f"callsign {callsign}", "id.callsign")
    elif address:
        slots.put("ident", address, "id.hex")
        slots.put("ident_named", f"address {address}", "id.hex")
    kind_type = value.item("id.type")
    if kind_type and not kind_type.get("group"):
        short = kind_type.get("short") or kind_type["value"]
        slots.put("type", kind_type["value"], "id.type")
        slots.put("type_a", article(kind_type["value"]), "id.type")
        slots.put("type_short", short, "id.type")
        if kind_type.get("category"):
            slots.put("type_cat", kind_type["category"], "id.type")
            slots.put("type_cat_a", article(kind_type["category"]), "id.type")
    elif kind_type:
        # A group designator's curated name: "airship", "balloon".
        slots.put("group", kind_type["value"].lower(), "id.type")
    klass = value.item("id.type_class")
    if klass and not kind_type:
        # A designator with no name: "type code A129, a twin-turboshaft helicopter". type_short
        # is then the class phrase, so a template that presupposes a named type requires "type".
        slots.put("type_class", klass["value"], "id.type_class")
        slots.put("type_class_a", article(klass["value"]), "id.type_class")
        slots.put("type_short", klass["value"], "id.type_class")
        slots.put("type_code", klass.get("code"), "id.type_class", "id.type_code")
    named = "group" if "group" in slots else "type_short"
    slots.put("group_or_type", slots.get(named), *slots.sources.get(named, ()))
    operator = value.item("op.callsign")
    if operator and callsign:
        slots.put("op", operator["value"], "op.callsign", "id.callsign")
        slots.put("op_short", operator.get("short") or operator["value"], "op.callsign", "id.callsign")
        slots.put("designator", operator.get("designator"), "op.callsign")
    # A registration or address block from home says nothing (story_facts marks it home).
    country = value.item("reg.country")
    if country and not country.get("home"):
        slots.put("country_the", with_the(country["value"]), "reg.country")
        slots.put("prefix", country.get("prefix"), "reg.country", "id.registration")
    allocation = value.item("alloc.country")
    if allocation and not allocation.get("home"):
        slots.put("alloc_country", allocation["value"], "alloc.country")
        slots.put("alloc_country_the", with_the(allocation["value"]), "alloc.country")
    if kind in EMITTER_KINDS and (emitter := value.item("id.emitter")):
        slots.put("emitter", emitter["value"], "id.emitter")
        slots.put("emitter_label", emitter.get("label"), "id.emitter")
    # A rated or mismatch story's second registry (story_facts._registry_check), and, for a
    # mismatch story, the type it lists when that differs.
    registry = value.item("tc.source")
    if registry:
        slots.put("registry", registry["value"], "tc.source")
        if registry.get("state") == "agrees":
            slots.put("tc_agrees", "yes", "tc.source")
    other = value.item("tc.type")
    if other:
        slots.put("other_type_a", article(other["value"]), "tc.type")


def _time_slots(slots, value, kind):
    start, end = value("obs.start"), value("obs.end")
    slots.put("date", long_date(start), "obs.start")
    slots.put("date_short", short_date(start), "obs.start")
    slots.put("day_month", day_month(start), "obs.start")
    moment = public_prose.moment(start)
    if moment:
        slots.put("clock", clock(start), "obs.start")
        slots.put("weekday", f"{moment:%A}", "obs.start")
        if moment.hour == 0 and moment.minute < 30:
            slots.put("midnight", "just after midnight UTC", "obs.start")
    ep_start, ep_end = value("ep.start"), value("ep.end")
    start_id, end_id = ("ep.start", "ep.end") if ep_start else ("obs.start", "obs.end")
    ep_start, ep_end = ep_start or start, ep_end or end
    slots.put("ep_clock", clock(ep_start), start_id)
    slots.put("ep_date", long_date(ep_start), start_id)
    slots.put("ep_day_month", day_month(ep_start), start_id)
    slots.put("ep_end_clock", clock(ep_end), end_id)
    if ep_end and _day(ep_end) != _day(ep_start):
        slots.put("ep_end_day_month", day_month(ep_end), end_id)


def _own_day(slots, name, at, start, fact_id):
    """A time on a later UTC date than the start also gets its own date, which DATED_ENDS requires."""
    if _day(at) and _day(start) and _day(at) != _day(start):
        slots.put(name, day_month(at), fact_id)


def _reading_slots(slots, value, kind):
    """Readings at the saved time, each with its own time."""
    for name, phrase in ALTITUDE_FIELDS:
        reading = value.item(f"m.{name}")
        if (number(reading.get("value")) or 0) > 0:
            text = unit_value(reading["value"], "ft")
            slots.put("alt", text, f"m.{name}")
            slots.put("alt_phrase", phrase.format(text), f"m.{name}")
            measured = (reading.get("at"), f"m.{name}")
            break
    else:
        measured = None
    speed = value.item("m.gs")
    if (number(speed.get("value")) or 0) > 0:
        slots.put("gs", unit_value(speed["value"], "kn", rounded=True), "m.gs")
        slots.put("gs_exact", unit_value(speed["value"], "kn"), "m.gs")
        slots.put("mph", f"about {round(speed['value'] * MILES_PER_NM):,} mph", "m.gs")
        measured = (speed.get("at"), "m.gs")
    if measured:
        # The saved readings' time: the ground speed's when there is one, else the altitude's.
        slots.put("m_clock", clock(measured[0]), measured[1])
        _own_day(slots, "m_day_month", measured[0], value("obs.start"), measured[1])
    rate_id = next(
        (f"m.{name}" for name in ("baro_rate", "geom_rate") if number(value(f"m.{name}")) is not None), None
    )
    if rate_id and value(rate_id) == 0:
        slots.put("level", "level", rate_id)
    if kind == "altitude_gap":
        for name in ("alt_baro", "alt_geom"):
            slots.put(name, unit_value(value(f"m.{name}"), "ft", signed=True), f"m.{name}")
    slots.put("tas", unit_value(value("m.tas"), "kn", rounded=True), "m.tas")
    altitude = next(
        (value(f"m.{name}") for name, _phrase in ALTITUDE_FIELDS if number(value(f"m.{name}")) is not None),
        None,
    )
    speed_value = number(value("m.gs"))
    if altitude is not None and speed_value is not None and altitude < 10000 and speed_value < 200:
        slots.put("slow", "yes", "m.gs")


def _track_slots(slots, value, kind):
    positions = value("track.positions")
    if positions and positions >= 2:
        slots.put("positions", spelled(positions), "track.positions")
        slots.put("positions_few", spelled(positions) if positions <= 4 else None, "track.positions")
    slots.put("track_time", duration(value("track.seconds")), "track.seconds")
    slots.put("miles", miles(value("track.nm")), "track.nm")
    place = value.item("track.place")
    if place:
        slots.put("place", place["value"], "track.place")
        slots.put("place_short", place.get("short"), "track.place")
    airport = value.item("track.airport")
    if airport:
        slots.put("airport", airport["value"], "track.airport")
        nm = number(value("track.airport_nm"))
        if nm is not None:
            slots.put("airport_miles", miles(nm) or "a mile", "track.airport_nm")
    if value("track.mlat"):
        slots.put("mlat", "multilateration", "track.mlat")
        slots.put("estimated", "estimated", "obs.source")
    low, high = number(value("track.alt_min")), number(value("track.alt_max"))
    peak = number(value("m.max_altitude_ft"))
    if low is not None and high is not None and high > low:
        slots.put("alt_min", unit_value(low, "ft", signed=True), "track.alt_min")
        slots.put("alt_max", unit_value(max(high, peak or high), "ft", signed=True), "track.alt_max")
    if low is not None and low >= 1000:
        slots.put("alt_floor", unit_value(math.floor(low / 1000) * 1000, "ft"), "track.alt_min")
    slow, fast = number(value("track.gs_min")), number(value("track.gs_max"))
    if slow is not None and fast is not None and fast > slow:
        slots.put("gs_min", unit_value(slow, "kn", rounded=True), "track.gs_min")
        slots.put("gs_max", unit_value(fast, "kn", rounded=True), "track.gs_max")


def _age_slots(slots, value, kind):
    """The listed build year and age; story_facts gives them only for an old airframe."""
    built, age = value("age.built"), value("age.years")
    if built and age:
        slots.put("built", str(built), "age.built")
        slots.put("age", str(age), "age.years", "age.built")


def _value_slots(slots, value, kind):
    """The headline value, its neighbours, a balloon's spike, the envelope and a position jump."""
    reading = value.item("obs.value")
    if reading:
        unit = reading.get("unit")
        level = reading.get("field") in LEVEL_FIELDS
        text = unit_value(reading["value"], unit, signed=level)
        slots.put("value", text, "obs.value")
        slots.put("value_round", unit_value(reading["value"], unit, rounded=True, signed=level), "obs.value")
        slots.put(
            "value_adj",
            unit_value(reading["value"], unit, rounded=True, adjective=True, signed=level),
            "obs.value",
        )
        field_word = FIELD_WORDS.get(reading.get("field"))
        direction = ("descent" if reading["value"] < 0 else "climb") if unit == "ft/min" else None
        slots.put("rate_direction", direction, "obs.value")
        # The value on its own, with a rate's direction: "a 9,800 ft/min descent" or "1,063.9 knots".
        slots.put("value_text", rate_phrase(reading["value"]) if direction else text, "obs.value")
        adjective = f"{direction}-rate" if direction else FIELD_ADJECTIVES.get(field_word, field_word)
        slots.put("field_adj", adjective, "obs.value")
        slots.put("value_phrase", _value_phrase(kind, number(reading["value"]), unit, text), "obs.value")
    peak_at = value("nb.peak_at") or reading.get("at")
    slots.put("peak_clock", clock(peak_at), "nb.peak_at")
    _own_day(slots, "peak_day_month", peak_at, value("obs.start"), "nb.peak_at")
    for name in ("before", "after", "min", "max"):
        neighbor = value.item(f"nb.{name}")
        if not neighbor:
            continue
        if neighbor.get("unit") == "ft/min":
            # A rate names its own direction; a bare number would read as a climb.
            slots.put(f"nb_{name}", rate_phrase(neighbor["value"]), f"nb.{name}")
            continue
        level = neighbor.get("field") in LEVEL_FIELDS
        slots.put(
            f"nb_{name}", unit_value(neighbor["value"], neighbor.get("unit"), signed=level), f"nb.{name}"
        )
        if name in {"before", "min"}:
            number_text = _exact(abs(neighbor["value"]))
            slots.put(
                f"nb_{name}_num",
                f"-{number_text}" if level and neighbor["value"] < 0 else number_text,
                f"nb.{name}",
            )
    low, high = number(value("nb.min")), number(value("nb.max"))
    observed = number(reading.get("value")) if reading else None
    if observed is not None and low is not None and high is not None:
        # A lone spike sits well outside every neighboring report, not merely above a limit.
        margin = max(high - low, 0.1 * abs(observed))
        if observed > high + margin or observed < low - margin:
            slots.put("lone_spike", "yes", "obs.value", "nb.min", "nb.max")
    count = value("nb.count")
    if count and count >= 2:
        slots.put("nb_count", spelled(count), "nb.count")
    before, after = duration(value("nb.gap_before")), duration(value("nb.gap_after"))
    if before and after:
        gaps = f"{before} before and after" if before == after else f"{before} before and {after} after"
        slots.put("nb_gaps", gaps, "nb.gap_before", "nb.gap_after")
    spike = value.item("spike.gs")
    if spike:
        slots.put("spike", unit_value(spike["value"], "kn"), "spike.gs")
        slots.put("spike_adj", unit_value(spike["value"], "kn", rounded=True, adjective=True), "spike.gs")
        for name in ("before", "after"):
            slots.put(f"spike_{name}", unit_value(value(f"spike.{name}"), "kn"), f"spike.{name}")
    _rated_slots(slots, value, kind, reading.get("field") or "" if reading else "")
    for name, key, unit in (
        ("jump_nm", "m.displacement_nm", "NM"),
        ("jump_time", "m.elapsed_seconds", "s"),
        ("jump_speed", "m.implied_speed_kn", "kn"),
    ):
        if (number(value(key)) or 0) > 0:
            text = unit_value(value(key), unit, rounded=unit == "kn")
            slots.put(name, f"an implied {text}" if name == "jump_speed" else text, key)


def _rated_slots(slots, value, kind, field):
    """The type's cited certified limit for the story's reading: a speed story gets the speed
    limit, an altitude story the ceiling, as a phrase that follows the type's name.

    A speed limit is an airspeed and the reading a ground speed, so the phrase calls it an
    airspeed limit; the checks keep it out of any sentence that holds the reading
    (claim:limit_vs_ground_speed)."""
    speed = field == "gs" or kind in SPEED_KINDS
    if speed and (limit := value.item("lim.speed")) and limit.get("field") in AIRSPEED_LIMIT_NAMES:
        text = unit_value(limit["value"], "kn")
        slots.put("rated_phrase", f"has a {AIRSPEED_LIMIT_NAMES[limit['field']]} of {text}", "lim.speed")
    elif "alt" in field and value("lim.altitude"):
        slots.put(
            "rated_phrase", f"is certified to {unit_value(value('lim.altitude'), 'ft')}", "lim.altitude"
        )


def _code_slots(slots, value, kind):
    """Codes, statuses and advisories. A meaning is used only with the code or status it explains."""
    code_id = "code.code" if value("code.code") else "code.squawk"
    code = value(code_id)
    slots.put("code", code, code_id)
    meaning = value.item("code.meaning")
    status = value("code.status")
    if meaning and code and meaning.get("code") == code:
        slots.put("code_meaning", meaning["value"], "code.meaning", code_id)
    if slots.get("code_meaning") and code in maneuvers.STATUS_CODES:
        slots.put(
            "code_caveat", f"The code alone does not confirm {meaning['value']}.", "code.meaning", code_id
        )
    elif slots.get("code_meaning") and kind == "special_use_code":
        slots.put("code_caveat", "A code alone does not show what this flight was doing.", code_id)
    if status and status != "none":
        slots.put("status", status, "code.status")
        slots.put("status_label", STATUS_LABELS.get(status), "code.status")
        if meaning and meaning.get("code") in {status, story_facts.STATUS_CODES.get(status)}:
            slots.put("status_meaning", meaning["value"], "code.meaning", "code.status")
            caveat = f"The status alone does not confirm {meaning['value']}."
            slots.put("status_caveat", caveat, "code.meaning", "code.status")
    sequence = value("code.sequence")
    if isinstance(sequence, list) and len(sequence) >= 2:
        slots.put("sequence", ", then ".join(sequence), "code.sequence")
        slots.put("sequence_first", duration(value("code.step_1_seconds")), "code.step_1_seconds")
    advisory = value("ra.advisory")
    slots.put("advisory", advisory, "ra.advisory")
    if advisory and re.match(
        r"(?:climb|descend|level|maintain|adjust|monitor|increase|reverse)\b", advisory, re.I
    ):
        slots.put("advisory_lower", advisory.lower(), "ra.advisory")


def _metric_slots(slots, value, kind):
    """Episode and pattern metrics, and the other aircraft in the episode."""
    for key, (name, unit) in METRIC_SLOTS.items():
        metric = number(value(f"m.{key}"))
        if not metric:
            continue
        if unit != "count":
            slots.put(name, unit_value(metric, unit), f"m.{key}")
        elif metric >= 2 and float(metric).is_integer():
            slots.put(name, spelled(int(metric)), f"m.{key}")
    if slots.get("duration") and (value("m.duration_seconds") or 0) >= 5400:
        slots.put("hours", duration(value("m.duration_seconds")), "m.duration_seconds")
    others = [fact_id for fact_id in value.items if re.fullmatch(r"p\.\d+\.role", fact_id)]
    if not others:
        return
    # The packet lists at most twelve others; the episode's own count is the full total.
    total, total_id = number(value("m.participant_count")), "m.participant_count"
    if not total or total < len(others) + 1 or not float(total).is_integer():
        total, total_id = len(others) + 1, "p.count"
    total = int(total)
    slots.put("others", spelled(total - 1) if total > 2 else "one", total_id)
    slots.put("aircraft_total", spelled(total), total_id)
    partner = value.item("p.1.type")
    if partner and not partner.get("group"):
        slots.put("p1_type", partner["value"], "p.1.type")
        slots.put("p1_type_a", article(partner["value"]), "p.1.type")
        slots.put("p1_short", partner.get("short") or partner["value"], "p.1.type")
    partner_ident = value("p.1.registration") or value("p.1.callsign") or value("p.1.hex")
    slots.put("p1_ident", partner_ident, "p.1.registration", "p.1.callsign", "p.1.hex")
    if slots.get("p1_type") and slots.get("type"):
        same = slots["p1_type"] == slots["type"]
        slots.put("same_type" if same else "mixed_types", "yes", "p.1.type", "id.type")
        slots.put("partner", f"another {slots['type_short']}" if same else slots["p1_type_a"], "p.1.type")


def _record_slots(slots, value, kind):
    """Type background, season records, variants, watchlist entries and identity checks."""
    profiles = [fact_id for fact_id in value.items if fact_id.startswith("tp.")]
    for index, fact_id in enumerate(profiles[:3], start=1):
        slots.put(f"tp{index}", value(fact_id), fact_id)
    season = value.item("season.start")
    if season:
        slots.put("season_start", day_month(season["value"]), "season.start")
        slots.put("season_name", season.get("name"), "season.start")
    slots.put("previous_gs", unit_value(value("season.previous"), "kn", rounded=True), "season.previous")
    slots.put("variant", value("variant.label"), "variant.label")
    # The Plane Alert DB entry: who or what it names, and why the list carries the aircraft.
    slots.put("wl_assoc", value("wl.association"), "wl.association")
    category, named = value("wl.category"), value("wl.list_category")
    slots.put("wl_list", named or category, "wl.list_category" if named else "wl.category")
    slots.put("wl_desc", value("wl.description"), "wl.description")
    slots.put("wl_note", value("wl.category_note"), "wl.category_note")
    slots.put("wl_group", value("wl.group"), "wl.group")
    slots.put("wl_tags", series(f'"{tag}"' for tag in value("wl.tags") or []), "wl.tags")
    # What Wikidata calls the organization the entry links to (entity_context), with articles:
    # "the Federal Aviation Administration, a United States government agency". When the entry
    # carries one of the item's aliases instead of its label, the label and the description go
    # to their own slots, so the sentence names both.
    who = value.item("wl.who")
    if who:
        label, text = who.get("label"), who["value"]
        if isinstance(label, str) and text.startswith(label + ", "):
            named = ("the " if label.split()[-1] in ORGANIZATION_HEADS else "") + label
            description = described(text[len(label) + 2 :])
            association = value("wl.association")
            if isinstance(association, str) and association.casefold() != label.casefold():
                slots.put("who_label", named, "wl.who")
                slots.put("who_desc", description, "wl.who")
            else:
                slots.put("who", f"{named}, {description}", "wl.who")
        else:
            slots.put("who", text, "wl.who")
    implied = value("idc.address_registration")
    if implied:
        slot = "address_agrees" if implied == value("id.registration") else "implied"
        slots.put(slot, implied, "idc.address_registration")
    slots.put("callsign_conflict", value("idc.callsign_registration"), "idc.callsign_registration")
    if value("idc.callsign_registration"):
        slots.put(
            "callsign_source",
            "archived callsign label"
            if value.item("idc.callsign_registration").get("basis") == "archive-label"
            else "callsign field",
            "idc.callsign_registration",
        )
    if value("idc.registration_country") and value("idc.allocation_country"):
        slots.put("reg_conflict_the", with_the(value("idc.registration_country")), "idc.registration_country")
        slots.put("alloc_conflict_the", with_the(value("idc.allocation_country")), "idc.allocation_country")
    # The data explainer names only fields that disagree; without one it does not apply.
    conflicts = ("implied", "callsign_conflict", "reg_conflict_the", "emitter", "alt_baro", "jump_nm")
    if any(slots.get(name) for name in conflicts):
        slots.put(
            "data_conflict", "yes", *(fact for name in conflicts for fact in slots.sources.get(name, ()))
        )


def _norm_slots(slots, value, kind):
    """The type's usual ground speed in these records, for a named type only: the norm is one
    type code's, never a class phrase's. A short name ending in a consonant and "y" ("Husky")
    gets none, since its plural ("Huskies") is no spelling the checks read as the type."""
    # story_facts._norm gives the five facts together, with a known phase, or none of them.
    phase = value("norm.phase")
    if phase is None or not slots.get("type") or re.search(r"[^aeiou\W]y$", slots["type_short"], re.I):
        return
    low, high = value("norm.gs_low"), value("norm.gs_high")
    floor, ceiling = value("norm.band_low"), value("norm.band_high")
    slots.put("norm_low", _thousands(low), "norm.gs_low")
    slots.put("norm_high", unit_value(high, "kn"), "norm.gs_high")
    slots.put(
        "norm_band",
        f"between {_thousands(floor)} and {_thousands(ceiling)} ft",
        "norm.band_low",
        "norm.band_high",
    )
    slots.put("norm_phase", story_facts.NORM_PHASE_WORDS[phase], "norm.phase")
    short = slots["type_short"]
    slots.put(
        "type_plural", short + ("es" if short.endswith(("s", "x")) else "s"), *slots.sources["type_short"]
    )


def _shape_slots(slots, value, kind):
    """Shapes from track geometry and the verbs of a climb or descent."""
    if kind in SHAPES:
        present, past, ing = SHAPES[kind]
        loops, passes = number(value("m.closed_loops")), number(value("m.complete_passes"))
        if kind == "circling" and loops and loops >= 2:
            present, past, ing = (
                f"{verb} {spelled(int(loops))} closed loops" for verb in ("flies", "flew", "flying")
            )
        if kind in {"parallel_passes", "repeated_passes"} and passes and passes >= 2:
            noun = "parallel passes" if kind == "parallel_passes" else "passes over one area"
            present, past, ing = (
                f"{verb} {spelled(int(passes))} {noun}" for verb in ("makes", "made", "making")
            )
        slots.put("shape", present, "m.closed_loops", "m.complete_passes")
        if kind in DURATIVE_SHAPES and slots.get("duration"):
            slots.put("shape_duration", slots["duration"], "m.duration_seconds")
        slots.put("shape_past", past, "m.closed_loops", "m.complete_passes")
        slots.put("shape_past_ing", ing, "m.closed_loops", "m.complete_passes")
    rate, change = number(value("m.peak_rate_fpm")), number(value("m.altitude_change_ft"))
    sign = rate or change
    if kind in {"rapid_descent", "rapid_climb", "vertical"} and sign:
        descending = sign < 0
        slots.put("rate_verb", "descends" if descending else "climbs", "m.peak_rate_fpm")
        slots.put("rate_verb_past", "descended" if descending else "climbed", "m.peak_rate_fpm")
        slots.put("rate_noun", "descent" if descending else "climb", "m.peak_rate_fpm")
        if change:
            slots.put("change_adj", unit_value(change, "ft", adjective=True), "m.altitude_change_ft")


def _mach_text(value, *, certified=False):
    """Mach 0.82 or Mach 0.925 for a certified figure, which keeps its digits; Mach 1.03 for an
    estimate, which reads to two places."""
    value = number(value)
    if value is None or value <= 0:
        return None
    return f"Mach {value:g}" if certified else f"Mach {value:.2f}"


# How prose names each certified figure (limits.LIMITS), by the field story facts give it.
LIMIT_NAMES = {story_facts.RATED_LIMIT_UNITS[key][1]: limit.name for key, limit in limits.LIMITS.items()}
# Vmo and Vne named as what they are, indicated airspeeds, beside a story's ground speed.
AIRSPEED_LIMIT_NAMES = {
    field: name.replace(" speed", " airspeed")
    for field, name in LIMIT_NAMES.items()
    if name.endswith(" speed")
}


def _floor_value(value, unit):
    """A floor over several reports, rounded down so "or more" and "at least" stay true."""
    value = number(value)
    return None if value is None else unit_value(math.floor(value), unit)


def _comparison_slots(slots, value, kind):
    """A rated story's comparison: the certified figure, the reports over it, the margin and,
    for a speed, the report closest to the limit, whose ground speed, airspeed, and Mach
    number are one calculation. Floors over the reports read "or more" and "at least";
    nothing says the aircraft held a value continuously. A story states the figure and its
    sheet once (rated_source) and carries one hedge (rated_hedge_*), never a cause or a
    judgement."""
    limit = value.item("lim.limit")
    field = limit.get("field")
    # Without its cited figure a rated story has no comparison, no hedge, and so no publication.
    if kind not in public_prose.RATED_KINDS or field not in LIMIT_NAMES:
        return
    if kind == "above_rated_altitude":
        slots.put("rated_altitude", "yes", "lim.limit")
    slots.put("lim_name", LIMIT_NAMES[field], "lim.limit")
    if field == "max_operating_altitude_ft":
        slots.put("lim_limit", unit_value(limit["value"], "ft"), "lim.limit")
        slots.put("lim_limit_adj", unit_value(limit["value"], "ft", adjective=True), "lim.limit")
    elif field == "mmo":
        slots.put("lim_limit", _mach_text(limit["value"], certified=True), "lim.limit")
    else:
        slots.put("lim_limit", unit_value(limit["value"], "kn"), "lim.limit")
    reading = value.item("lim.reading")
    if reading:
        slots.put("lim_reading", _floor_value(reading["value"], reading.get("unit")), "lim.reading")
    margin = value.item("lim.margin")
    if margin and margin.get("unit") == "ft":
        slots.put("lim_margin", _floor_value(margin["value"], "ft"), "lim.margin")
    elif margin and number(margin["value"]) is not None:
        slots.put("lim_margin", f"{math.floor(margin['value'])} percent", "lim.margin")
    slots.put("lim_time", duration(value("lim.seconds")), "lim.seconds")
    reports = number(value("lim.reports"))
    if reports and reports >= 2 and float(reports).is_integer():
        slots.put("lim_reports", spelled(int(reports)), "lim.reports")
    slots.put("lim_decisive_gs", unit_value(value("lim.decisive_gs"), "kn", rounded=True), "lim.decisive_gs")
    airspeed = value.item("lim.airspeed")
    if airspeed:
        text = unit_value(airspeed["value"], "kn", rounded=True)
        slots.put("lim_airspeed", text, "lim.airspeed")
        estimated = airspeed.get("field") == "estimated_airspeed"
        slots.put("airspeed_estimated" if estimated else "airspeed_reported", "yes", "lim.airspeed")
        # The required hedge names how the airspeed was obtained (public_prose.REQUIRED_SENTENCES).
        slots.put("airspeed_origin", public_prose.AIRSPEED_ORIGINS.get(airspeed.get("basis")), "lim.airspeed")
        slots.put("est_word", "estimated" if estimated else "reported", "lim.airspeed")
        slots.put(
            "airspeed_kind", "estimated airspeed" if estimated else "reported true airspeed", "lim.airspeed"
        )
        slots.put(
            "airspeed_phrase",
            f"an estimated airspeed of {text}" if estimated else f"a reported true airspeed of {text}",
            "lim.airspeed",
        )
        # The figure the binding limit is stated in: Mach under Mmo, or an equivalent
        # airspeed under Vmo or Vne. Sheets state Vmo and Vne as indicated, calibrated or
        # equivalent airspeed, so the prose says "on the same airspeed scale as the limit".
        mach = _mach_text(value("lim.mach"))
        if field == "mmo":
            slots.put("lim_compare", mach, "lim.mach")
            if estimated:
                slots.put("lim_mach_estimate", mach, "lim.mach")
        elif number(value("lim.indicated")) is not None:
            compare = f"about {unit_value(value('lim.indicated'), 'kn', rounded=True)} on the same airspeed scale as the limit"
            slots.put("lim_compare", compare, "lim.indicated")
    slots.put("lim_wind_adj", unit_value(value("lim.wind"), "kn", rounded=True, adjective=True), "lim.wind")


# Status values that read as plain words in a headline; "downed" only appears with its definition.
STATUS_LABELS = {"lifeguard": "lifeguard", "minfuel": "minimum-fuel"}
# Dek templates that say the code or status alone confirms nothing (design 4.4); squawk kinds use only these.
CAVEAT_DEK = re.compile(r"\{(?:code|status)_caveat\}|does not (?:confirm|show)")


SHAPES = {
    "circling": ("circles", "circled", "circling"),
    "prolonged_holding": (
        "flies an oval holding pattern",
        "flew an oval holding pattern",
        "flying an oval holding pattern",
    ),
    "hovering": ("hovers", "hovered", "hovering"),
    "descending_spiral": ("spirals down", "spiraled down", "spiraling down"),
    "parallel_passes": ("makes parallel passes", "made parallel passes", "making parallel passes"),
    "repeated_passes": ("makes repeated passes", "made repeated passes", "making repeated passes"),
    "loitering": ("loiters over a small area", "loitered over a small area", "loitering over a small area"),
    "confined_activity": (
        "stays within a small area",
        "stayed within a small area",
        "staying within a small area",
    ),
    "course_reversal": ("reverses course", "reversed course", "reversing course"),
    "altitude_oscillation": (
        "climbs and descends repeatedly",
        "climbed and descended repeatedly",
        "climbing and descending repeatedly",
    ),
    "departure_return": (
        "turns back toward its start",
        "turned back toward its start",
        "turning back toward its start",
    ),
    "possible_go_around": (
        "descends, then climbs away",
        "descended, then climbed away",
        "descending, then climbing away",
    ),
    "extreme_turn_rate": ("makes a very tight turn", "made a very tight turn", "making a very tight turn"),
}
FIELD_ADJECTIVES = {"ground speed": "ground-speed", "vertical rate": "vertical-rate"}
# Kinds whose prose names a ground speed (public_prose.SPEED_PROSE_KINDS).
SPEED_KINDS = set(kinds.where(speed_prose=True))
# Shapes that last; the others happen at a moment, so a duration would mislead.
DURATIVE_SHAPES = set(kinds.where(durative=True))


def _value_phrase(kind, value, unit, text):
    """The headline value with its field, direction and unit (PUBLIC-5), or None."""
    if text is None:
        return None
    if kind == "wind_boost" and unit in {"kn difference", "kn"}:
        return f"a ground speed {text} above its true airspeed"
    if kind == "selected_altitude_overshoot" and unit == "ft":
        return f"an altitude {text} past its selected altitude"
    if unit in {"kn", "kn difference"}:
        return f"a ground speed of {text}"
    if unit == "ft":
        return f"an altitude of {text}"
    if unit == "ft/min":
        return f"a {'descent' if value < 0 else 'climb'} rate of {text}"
    if unit in {"s", "minutes"}:
        return f"an episode lasting {text}"
    return None


LIMIT_PREFERENCES = (
    "not show who",
    "not an emergency",
    "does not confirm",
    "does not establish",
    "cannot show",
    "consistent with",
    "not a worldwide",
    "is not airspeed",
    "different references",
    "not evidence",
    "not independently verified",
)


# Caveats about the data pipeline that the evidence card states better than prose. The card-only
# caveats (public_events.CARD_ONLY_LIMITATIONS) stay out by name.
TECHNICAL_LIMITS = re.compile(
    r"timestamps do not establish|archived label carried|rates are signed|field ages|independent corroboration"
    r"|independent verification|position source is unspecified|confirmation requirements",
    re.I,
)
KIND_LIMIT_PREFERENCES = {
    "special_use_code": ("special-use code meanings",),
    "notable_variant": ("the data show the airframe", "not who"),
    "watchlist_match": ("plane alert db association",),
    "stratospheric_balloon": ("consistent with a high-altitude balloon",),
    "tcas_ra": ("collision-avoidance",),
    "first_type_here": ("first for these records",),
    "first_country_here": ("first for these records",),
    "rare_here": ("rarely include",),
    "military": ("database labels",),
    "database_watch": ("database labels",),
    "vintage": ("build year",),
}


# Packet caveats written as fragments or disclaimers, and the sentence a story uses instead.
LIMIT_REWRITES = {
    public_events.FIRST_HERE: "This is a first for these records only; it says nothing about how often such "
    "aircraft fly elsewhere.",
    public_events.RARE_HERE: "These records rarely include this type, which says nothing about how common it is "
    "elsewhere.",
    public_events.DATABASE_YEAR: "The build year comes from the aircraft database alone.",
}


def _best_limit(kind, limits):
    """The limitation a reader is most likely to need, restated once in the body."""
    limits = [LIMIT_REWRITES.get(text, text) for text in limits if isinstance(text, str)]
    usable = [
        text
        for text in limits
        if 30 <= len(text) <= 260
        and text.endswith(".")
        and not public_prose.BANNED_PHRASES.search(text)
        and not TECHNICAL_LIMITS.search(text)
        and text not in public_events.CARD_ONLY_LIMITATIONS
    ]
    if not usable:
        return None
    for phrase in (*KIND_LIMIT_PREFERENCES.get(kind, ()), *LIMIT_PREFERENCES):
        for text in usable:
            if phrase in text.lower() and "metadata is a current database match" not in text:
                return text
    return usable[-1]


# ---------------------------------------------------------------- template language

PLACEHOLDER = re.compile(r"\{(\w+)(?:\|(\w+))?\}")
TEMPLATE_PART = re.compile(r"\[(?P<optional>[^\[\]]*)\]|" + PLACEHOLDER.pattern)
# A time on a later UTC date than the start must carry its own date: a variant that states the
# time without it is skipped, so "00:03 UTC on 26 September" never stands for 27 September.
DATED_ENDS = (
    ("ep_end_clock", "ep_end_day_month"),
    ("m_clock", "m_day_month"),
    ("peak_clock", "peak_day_month"),
)


def _apply(name, modifier, slots):
    text = slots.get(name)
    if not text:
        return None
    if modifier == "cap":
        return text[0].upper() + text[1:]
    if modifier == "a":
        return article(text)
    if modifier == "same":
        return text if slots.get("same_type") else None
    if modifier == "mixed":
        return text if slots.get("mixed_types") else None
    return text


def _pieces(text):
    """Text and ("slot", name, modifier) pieces of a template without optional groups."""
    pieces, position = [], 0
    for match in PLACEHOLDER.finditer(text):
        pieces.extend([text[position : match.start()], ("slot", match[1], match[2])])
        position = match.end()
    return [piece for piece in (*pieces, text[position:]) if piece]


@functools.lru_cache(maxsize=4096)
def _parse(template):
    """(parts, optional group count): pieces, with ("optional", index, pieces) for each [group].

    Groups do not nest; trailing groups drop first when a text must fit its limits.
    """
    parts, position, count = [], 0, 0
    for match in TEMPLATE_PART.finditer(template):
        parts.append(template[position : match.start()])
        if match["optional"] is not None:
            parts.append(("optional", count, _pieces(match["optional"])))
            count += 1
        else:
            parts.append(("slot", match[2], match[3]))
        position = match.end()
    parts.append(template[position:])
    return tuple(part for part in parts if part), count


def _render(pieces, slots, used):
    """Text for pieces, or None when a slot is missing."""
    output = []
    for piece in pieces:
        if isinstance(piece, str):
            output.append(piece)
            continue
        text = _apply(piece[1], piece[2], slots)
        if text is None:
            return None
        used.add(piece[1])
        output.append(text)
    return "".join(output)


def _fill(parts, slots, drop):
    """(text, slots used) with the optional groups in drop left out; (None, set()) when a slot is missing."""
    output, used = [], set()
    for part in parts:
        if isinstance(part, tuple) and part[0] == "optional":
            inner = set()
            text = None if part[1] in drop else _render(part[2], slots, inner)
            if text is not None:
                used |= inner
                output.append(text)
            continue
        text = _render((part,), slots, used)
        if text is None:
            return None, set()
        output.append(text)
    return "".join(output), used


def _tidy(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r",,+", ",", text)
    return text[:1].upper() + text[1:] if text else text


def _template_slots(template):
    """The slot names a template reads."""
    return [match.group(1) for match in PLACEHOLDER.finditer(template)]


def fill(template, slots, *, limits=None):
    """(text, slots used) for a template, dropping trailing optional parts to fit limits.

    A record that ends on a later UTC date than it starts only uses text that names the
    end date, so an end time is never read as falling on the start date.
    """
    parts, count = _parse(template)
    for dropped in range(count + 1):
        text, used = _fill(parts, slots, set(range(count - dropped, count)))
        if text is None:
            return None, set()
        text = _tidy(text)
        if any(slots.get(day) and clock in used and day not in used for clock, day in DATED_ENDS):
            continue
        if limits is None or limits[0] <= len(text) <= limits[1]:
            return text, used
        if len(text) < limits[0]:
            return None, set()
    return None, set()


# ---------------------------------------------------------------- normalization and variety

TOKEN_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s*UTC)?\b", re.I)
MONTH_PATTERN = public_prose.MONTH
TOKEN_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}\s+"
    + MONTH_PATTERN
    + r"\b(?:\s+\d{4})?|\b"
    + MONTH_PATTERN
    + r"\s+\d{1,2}(?:,\s*\d{4})?\b|\b"
    + MONTH_PATTERN
    + r"\b(?:\s+\d{4})?"
)
NUMBER_WORDS = sorted({"one", *SMALL_NUMBERS.values(), *ORDINALS.values()}, key=len, reverse=True)
TOKEN_NUMBER = re.compile(
    r"\b\d[\d,]*(?:\.\d+)?(?:st|nd|rd|th)?\b|\b(?:" + "|".join(NUMBER_WORDS) + r")\b",
    re.I,
)


def _identity_names(source):
    """(text, class token) pairs for one story's own identities, longest first.

    source: a story or recent entry, with "registration" and "callsign" at the top and
    its story facts under "facts".
    """
    pairs = []
    facts = source.get("facts") if isinstance(source.get("facts"), dict) else {}

    def add(text, token):
        if isinstance(text, str) and len(text) >= 2:
            pairs.append((text, token))

    for text in (source.get("registration"), source.get("callsign")):
        add(text, "reg")
    for key in ("id.registration", "id.callsign", "id.hex"):
        add(facts.get(key), "reg")
    code = source.get("type_code") or facts.get("id.type_code")
    entry = story_facts.type_entry(code, facts.get("id.type")) if code else None
    for name in (facts.get("id.type"), facts.get("variant.label"), *(entry or {}).get("aliases", [])):
        add(name, "type")
    if entry:
        add(entry.get("name"), "type")
        add(entry.get("short"), "type")
    operator = story_facts.operator_for(facts.get("id.callsign") or source.get("callsign"))
    if operator:
        for text in (operator["name"], operator["short"], operator["designator"]):
            add(text, "op")
    add(facts.get("op.callsign"), "op")
    for key in ("track.place", "track.place.short", "track.place.state"):
        add(facts.get(key), "place")
    for key in ("reg.country", "alloc.country"):
        add(facts.get(key), "country")
    for key, text in facts.items():
        if not isinstance(key, str):
            continue
        if re.fullmatch(r"p\.\d+\.(?:registration|callsign|hex)", key):
            add(text, "reg")
        elif re.fullmatch(r"p\.\d+\.type", key):
            add(text, "type")
    add(facts.get("wl.category"), "category")
    if any(isinstance(key, str) and key.startswith("wl.") for key in facts):
        # The entry's own names are names, so its sentences never read as repeats of each other.
        add("Plane Alert DB", "category")
        for text in (
            facts.get("wl.association"),
            facts.get("wl.list_category"),
            *(facts.get("wl.tags") or ()),
        ):
            add(text, "category")
    return sorted(set(pairs), key=lambda pair: (-len(pair[0]), pair[0]))


@functools.lru_cache(maxsize=256)
def _name_pattern(names):
    """One pattern for a story's identity names, longest first, and each name's class token."""
    tokens = {}
    for name, token in names:
        tokens.setdefault(name.lower(), token.upper())
    alternatives = "|".join(re.escape(name) for name, _token in names)
    return re.compile(r"(?<![\w-])(?:" + alternatives + r")(?![\w-])", re.I), tokens


@functools.lru_cache(maxsize=16384)
def _normalized(text, names):
    if names:
        pattern, tokens = _name_pattern(names)
        text = pattern.sub(lambda match: f" {tokens[match.group().lower()]} ", text)
    text = TOKEN_TIME.sub(" TIME ", text)
    text = TOKEN_DATE.sub(" DATE ", text)
    text = TOKEN_NUMBER.sub(" NUM ", text)
    text = re.sub(r"\b[\w-]*\d[\w-]*\b", " ID ", text)
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    return tuple(word.lower().strip("'-") for word in words if word.strip("'-"))


def normalize(text, names=()):
    """Lowercase class tokens: identities, times, dates and numbers replaced, punctuation stripped."""
    if not isinstance(text, str):
        return []
    return list(_normalized(text, tuple(names)))


def _skeleton(text, names, limit=None):
    return " ".join(normalize(text, names)[:limit])


def _grams(tokens, size):
    return {tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}


def jaccard(first, second):
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def sentences(text):
    """A text's sentences, split as the story checks split them ("U.S." ends none)."""
    return [text[start:end].strip() for start, end in public_prose.sentence_spans(text)]


def skeletons(story):
    """The opener and headline skeletons a template or AI story stores (STORY_KEYS)."""
    names = _identity_names(story)
    paragraphs = story.get("paragraphs") or []
    first = paragraphs[0] if paragraphs and isinstance(paragraphs[0], str) else ""
    return {
        "opener_skeleton": _skeleton(first, names, OPENER_TOKENS),
        "headline_skeleton": _skeleton(story.get("headline", ""), names),
    }


def _entry_names(source):
    """_identity_names for a story or a recent entry. A recent entry lists its facts as ids and
    carries its story's fact values (fact_values), which name its text as they named the stored
    skeletons."""
    values = source.get("fact_values")
    return _identity_names({**source, "facts": values} if isinstance(values, dict) else source)


def dek_skeleton(story):
    """A story's or recent entry's dek skeleton, which no story stores; the AI writer's avoid list
    computes it when it needs one."""
    return _skeleton(story.get("dek", ""), _entry_names(story))


@dataclass
class _Entry:
    day: object
    kind: str
    type_code: str
    headline: str
    dek: str
    seo_title: str
    opener: str
    headline_grams: set
    dek_grams: set
    body_grams: set
    sentences: frozenset = frozenset()


def _plain(text):
    return " ".join(text.lower().split()) if isinstance(text, str) else ""


def _story_day(source):
    facts = source.get("facts") if isinstance(source.get("facts"), dict) else {}
    return _day(source.get("date")) or _day(facts.get("obs.date")) or _day(facts.get("obs.start"))


def _entry(source):
    """A story or recent entry normalized for the variety checks."""
    names = _entry_names(source)
    paragraphs = source.get("paragraphs")
    paragraphs = (
        [text for text in paragraphs if isinstance(text, str)] if isinstance(paragraphs, list) else []
    )
    opener = source.get("opener_skeleton")
    if not isinstance(opener, str) or not opener:
        opener = _skeleton(paragraphs[0] if paragraphs else "", names, OPENER_TOKENS)
    facts = source.get("facts") if isinstance(source.get("facts"), dict) else {}
    return _Entry(
        day=_story_day(source),
        kind=source.get("kind") or "",
        type_code=source.get("type_code") or facts.get("id.type_code") or "",
        headline=_plain(source.get("headline")),
        dek=_plain(source.get("dek")),
        seo_title=_plain(source.get("seo_title")),
        opener=opener,
        headline_grams=_grams(normalize(source.get("headline", ""), names), 2),
        dek_grams=_grams(normalize(source.get("dek", ""), names), 3),
        body_grams=_grams(normalize(" ".join(paragraphs), names), 3),
        sentences=frozenset(_plain(sentence) for text in paragraphs for sentence in sentences(text)),
    )


class Corpus:
    """Recent stories, normalized once for repeated variety checks.

    Exact repeats of a headline, standfirst or title tag count at any distance, so their
    plain text is kept for every story. The similarity checks look only at the 30 days
    around `day` and the BODY_HISTORY latest bodies, so only those stories are normalized.
    Without a day, every story is.
    """

    def __init__(self, recent, day=None):
        items = (
            [item for item in recent if isinstance(item, dict)] if isinstance(recent, (list, tuple)) else []
        )
        self.headlines = {_plain(item.get("headline")) for item in items} - {""}
        self.deks = {_plain(item.get("dek")) for item in items} - {""}
        self.seo_titles = {_plain(item.get("seo_title")) for item in items} - {""}
        if day is not None:
            dated = [(_story_day(item), item) for item in items]
            earlier = [pair for pair in dated if pair[0] is None or pair[0] <= day]
            latest = sorted(earlier, key=lambda pair: pair[0] or date.min, reverse=True)[:BODY_HISTORY]
            keep = {id(item) for _day, item in latest} | {
                id(item) for when, item in dated if when is None or abs((day - when).days) <= WINDOW_SAME_DAYS
            }
            items = [item for item in items if id(item) in keep]
        self.entries = [_entry(item) for item in items]
        self._unions, self._sentences = {}, {}

    def window(self, day, days, *, kind=None, type_code=None):
        for entry in self.entries:
            if day is not None and entry.day is not None and abs((day - entry.day).days) > days:
                continue
            if kind is not None and entry.kind != kind and not (type_code and entry.type_code == type_code):
                continue
            yield entry

    def bodies(self, day):
        dated = sorted(
            (entry for entry in self.entries if day is None or entry.day is None or entry.day <= day),
            key=lambda entry: entry.day or date.min,
            reverse=True,
        )
        return dated[:BODY_HISTORY]

    def body_union(self, day):
        """Normalized trigrams of the recent bodies, once per day."""
        if day not in self._unions:
            self._unions[day] = set().union(*(entry.body_grams for entry in self.bodies(day)))
        return self._unions[day]

    def sentences(self, day):
        """Plain sentences of the recent bodies, for sentences that should not recur verbatim."""
        if day not in self._sentences:
            self._sentences[day] = frozenset().union(*(entry.sentences for entry in self.bodies(day)))
        return self._sentences[day]


def _part_issues(part, entry, corpus, day, kind, type_code):
    """Variety reasons for one part of a candidate: headline, dek, seo, opener or body."""
    issues = []
    if part == "headline":
        if entry.headline and entry.headline in corpus.headlines:
            issues.append("duplicate_headline")
        # A fixed headline ("{type} spotted for the first time") shares its skeleton by design.
        if kind not in public_prose.FIXED_HEADLINE_KINDS and any(
            jaccard(entry.headline_grams, other.headline_grams) >= HEADLINE_JACCARD
            for other in corpus.window(day, WINDOW_ANY_DAYS)
        ):
            issues.append("headline_similar")
    elif part == "dek":
        if entry.dek and entry.dek in corpus.deks:
            issues.append("duplicate_dek")
        if any(
            jaccard(entry.dek_grams, other.dek_grams) >= DEK_JACCARD
            for other in corpus.window(day, WINDOW_SAME_DAYS, kind=kind, type_code=type_code)
        ):
            issues.append("dek_similar")
    elif part == "seo":
        if entry.seo_title and entry.seo_title in corpus.seo_titles:
            issues.append("duplicate_seo_title")
    elif part == "opener":
        recent = {other.opener for other in corpus.window(day, WINDOW_ANY_DAYS)}
        recent |= {other.opener for other in corpus.window(day, WINDOW_SAME_DAYS, kind=kind)}
        if entry.opener and entry.opener in recent:
            issues.append("opener_repeat")
    elif part == "body":
        union = corpus.body_union(day)
        if entry.body_grams and len(entry.body_grams & union) / len(entry.body_grams) >= BODY_CONTAINMENT:
            issues.append("body_contained")
        nearby = [
            *corpus.window(day, WINDOW_ANY_DAYS),
            *corpus.window(day, WINDOW_SAME_DAYS, kind=kind, type_code=type_code),
        ]
        if any(jaccard(entry.body_grams, other.body_grams) >= BODY_JACCARD for other in nearby):
            issues.append("body_similar")
    return issues


def _issues(entry, corpus, *, kind=None, type_code=None):
    kind, type_code = kind or entry.kind, type_code or entry.type_code
    return [
        issue
        for part in ("headline", "dek", "seo", "opener", "body")
        for issue in _part_issues(part, entry, corpus, entry.day, kind, type_code)
    ]


def variety_issues(story, recent, *, kind=None, type_code=None):
    """Reason codes when a story repeats recent stories; [] when it is varied enough.

    recent: a Corpus, or [{"kind", "type_code", "registration", "date": "YYYY-MM-DD", "headline",
              "dek", "paragraphs", "angle", "opener_skeleton", "headline_skeleton"}], optionally
              with "seo_title", "callsign" and "facts" for better normalization. Windows are
              applied by date; exact duplicates are checked against every entry. Build the
              Corpus once to check many candidates against the same stories.
    kind, type_code: the candidate's own, for stories in the fixed shape, which carries neither;
              without a kind the same-kind windows cannot apply.
    """
    if not isinstance(story, dict):
        return ["not_a_story"]
    entry = _entry(story)
    corpus = recent if isinstance(recent, Corpus) else Corpus(recent, day=entry.day)
    return _issues(entry, corpus, kind=kind, type_code=type_code)


def variety_score(entry, corpus, issues):
    """Lower is less similar: used to pick the least repetitive option when none passes."""
    near = list(corpus.window(entry.day, WINDOW_SAME_DAYS))
    score = sum(
        max((jaccard(getattr(entry, name), getattr(other, name)) for other in near), default=0.0)
        for name in ("headline_grams", "dek_grams", "body_grams")
    )
    return score + 2.0 * sum(1 for issue in issues if issue.startswith(("duplicate", "opener")))


# ---------------------------------------------------------------- choice


def _hash(*parts):
    return hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()


def _recent_list(recent):
    return [item for item in recent or [] if isinstance(item, dict)]


def _ordered(facts, slots, recent):
    kind = facts.get("kind", "")
    angles = [angle for angle in angles_for(kind) if angle.usable(slots)]
    if not angles:
        angles = [angle for angle in GENERIC_ANGLES if angle.usable(slots)]
    type_code = story_facts.value(facts, "id.type_code")
    dated = sorted(
        (item for item in recent if isinstance(item.get("date"), str)), key=lambda item: item["date"]
    )
    used_kind = [item.get("angle") for item in dated if item.get("kind") == kind][-3:]
    used_type = [item.get("angle") for item in dated if type_code and item.get("type_code") == type_code][-2:]
    return sorted(
        angles,
        key=lambda angle: (
            angle.id in used_kind,
            angle.id in used_type,
            angle.tier,
            _hash(facts["digest"], angle.id, PROSE_POLICY),
        ),
    )


def angle_options(facts, *, recent=None):
    """Usable angles in the order both writers try them; the first is the preferred angle."""
    slots = build_slots(facts)
    return [{"id": angle.id, "focus": angle.focus} for angle in _ordered(facts, slots, _recent_list(recent))]


def context(facts):
    """Routing for the build: topic, section, type hub, airframe slug, operator, place and country."""
    return story_facts.context(facts)


def _rotation(items, seed):
    if not items:
        return []
    start = int(_hash(seed), 16) % len(items)
    return [(index, items[index]) for index in [*range(start, len(items)), *range(start)]]


class _Builder:
    """Assembles candidate stories for one render: facts, slots, recent stories and checks."""

    def __init__(self, facts, slots, corpus):
        self.facts, self.slots, self.corpus = facts, slots, corpus
        self.check = public_prose.prose_checker(facts)
        # The kind's required sentence is the checks' own text, which they read the story without.
        self.required = public_prose.required_sentence(facts)
        # Sourced type facts as the checks read them: a restated one is never the story's caveat.
        self.profiles = public_prose.profile_texts(facts)
        self.cache = {}
        self.kind = facts.get("kind", "")
        self.type_code = story_facts.value(facts, "id.type_code") or ""
        self.day = _day(story_facts.value(facts, "obs.date"))
        every = self.story_facts(set(slots.sources), cap=False)
        self.names = _identity_names(
            {"facts": {key: value for key, value in every.items() if isinstance(value, str)}}
        )
        self.union = corpus.body_union(self.day)
        self.recent = corpus.sentences(self.day)

    def story_facts(self, used, *, cap=True):
        """The facts behind the slots used, plus identity and date."""
        return story_fact_values(
            self.facts, {fact_id for name in used for fact_id in self.slots.sources.get(name, ())}, cap=cap
        )

    def clean(self, text):
        if text not in self.cache:
            self.cache[text] = text == self.required or not self.check(text)
        return self.cache[text]

    def _part_entry(self, part, text):
        """The variety entry of one candidate part, normalized with this story's names."""
        tokens = normalize(text, self.names)
        return _Entry(
            day=self.day,
            kind=self.kind,
            type_code=self.type_code,
            headline=_plain(text) if part == "headline" else "",
            dek=_plain(text) if part == "dek" else "",
            seo_title=_plain(text) if part == "seo" else "",
            opener=" ".join(tokens[:OPENER_TOKENS]) if part == "opener" else "",
            headline_grams=_grams(tokens, 2) if part == "headline" else set(),
            dek_grams=_grams(tokens, 3) if part == "dek" else set(),
            body_grams=set(),
        )

    def varied(self, part, text):
        """Whether a headline, dek or opener passes its part's variety check."""
        return not _part_issues(
            part, self._part_entry(part, text), self.corpus, self.day, self.kind, self.type_code
        )

    def pick(self, templates, seed, limits, part, *, skip=(), reject=None, hide=frozenset(), demote=None):
        """A rotated variant that renders and passes the prose checks and the part's variety check.

        Body sentences (part None) take the variant least present in recent bodies; reject()
        drops variants that would repeat a sentence the story already has, and hidden slots
        count as missing. Variants that demote() names are tried only when no other renders.
        """
        options = _rotation(list(templates), seed)
        if demote is not None:
            first = self.pick(
                templates, seed, limits, part, skip=skip, reject=lambda text: demote(text), hide=hide
            )
            return first or self.pick(templates, seed, limits, part, skip=skip, reject=reject, hide=hide)
        best = None
        slots = {name: text for name, text in self.slots.items() if name not in hide} if hide else self.slots
        for index, template in options:
            text, used = fill(template, slots, limits=limits)
            if not text or text in skip or (reject and reject(text)) or not self.clean(text):
                continue
            if part in (None, "opener", "dek") and (
                LEADING_COUNT.match(text) or not text.endswith(".") or public_prose.DASH.search(text)
            ):
                continue  # A sentence ends with a full stop and does not open with a numeral.
            if part is None:
                grams = _grams(normalize(text, self.names), 3)
                seen = len(grams & self.union) / len(grams) if grams else 0.0
                if best is None or seen < best[3]:
                    best = (index, text, used, seen)
                if seen == 0:
                    break
                continue
            if self.varied(part, text):
                return index, text, used
            best = best or (index, text, used)
        if part is None and best is not None:
            return best[:3]
        return best


# A thin story adds these fact sentences after its angle's own body until it holds enough
# prose that belongs to this page alone. Design 1.2 asks for 60 such words; the margin
# covers values that another page may share.
EXTRA_BUILDERS = ("operator", "country", "military_flag", "age")
SPECIFIC_WORDS = 80
SPECIFIC_TOKENS = frozenset({"reg", "id", "time"})
# A story states these once: later sentences drop an optional mention and skip variants that
# need one. Clock slots that render the same time count as one.
SINGLE_USE = frozenset({"date", "place", "airport"})
CLOCK_SLOTS = ("clock", "m_clock", "peak_clock", "ep_clock", "ep_end_clock")
# Builders with a form that names the type and one that leaves it out: the second follows a
# sentence that named the type, so the type is never stated twice running; the first is used
# anywhere else.
TYPE_ECHOES = frozenset({"registry_agrees"})
TYPE_SLOTS = frozenset({"type", "type_a", "variant"})
# Any slot that names the type, its short form and plural too: a sentence after one that used
# any of them does not name the type again (TYPE_ECHOES).
NAMED_TYPE_SLOTS = TYPE_SLOTS | {"type_short", "type_plural"}
# Optional background that a story leaves out when a recent story used the same sentence: type
# profiles.
OPTIONAL_REPEATS = frozenset({"profile", "profile2"})
# Optional background, by builder, that a story leaves out when any recent sentence names the
# slot's value, whatever its form: the airport, which recurs in nearly every story near a busy
# local field, at a distance that changes from story to story.
NAMED_REPEATS = {"airport": "airport"}
# A reading the story already gave, perhaps through another slot or rounded, is not given again.
MEASURES = {"alt": ("alt", "alt_phrase"), "gs": ("gs", "gs_exact", "mph")}
LEADING_COUNT = re.compile(r"\d[\d,.]*\s+[a-z]")
# Sentences a kind's story carries whatever its angle: the speed record's scope and hedge (its
# title tag says "fastest", which a paragraph scopes to these records), a rated story's
# certified figure with its sheet and its one hedge, and why Plane Alert DB lists a watchlist
# entry. Every angle of the kind carries them (a watchlist category angle in its opener); the
# fallback angles and the plain record add them (_fallback_story).
REQUIRED_BUILDERS = {
    "seasonal_speed_record": ("season", "record_hedge"),
    "above_rated_altitude": ("rated_source", "rated_hedge_altitude"),
    "above_rated_speed": ("rated_source", "rated_hedge_speed"),
    "watchlist_match": ("wl_why",),
}
# Fact sentences that should not repeat a recent story word for word when another variant exists.
FRESH_BUILDERS = frozenset(
    {
        "identity",
        "identity_ref",
        "identity_any",
        "identity_group",
        "identity_reg",
        "operator",
        "country",
        "alloc",
        "age",
        "reading",
        "reading_alt",
        "reading_gs",
        "track",
        "military_flag",
        "ceiling_reading",
        "ceiling_margin",
        "speed_estimate",
        "speed_report",
        "speed_margin",
    }
)


def _stated(text, written):
    """Whether the number and unit in text already appear in written, exactly or rounded."""
    match = re.match(r"([\d,]+(?:\.\d+)?)\s+(\S+)", text or "")
    if not match:
        return False
    value = public_prose._number(match[1])
    pattern = r"(?<![\d.,])([\d,]+(?:\.\d+)?)\s+" + re.escape(match[2])
    return any(
        abs(public_prose._number(found[1]) - value) <= max(0.5, 0.001 * value)
        for found in re.finditer(pattern, written)
    )


def specific_words(sentences, names=(), recent=frozenset()):
    """Words that belong to this page: sentences of six or more words that name the aircraft, a
    clock time or two values, and that no recent story carried word for word."""
    total = 0
    for sentence in sentences:
        tokens = normalize(sentence, names)
        if len(sentence.split()) < 6 or _plain(sentence) in recent:
            continue
        if SPECIFIC_TOKENS & set(tokens) or tokens.count("num") >= 2:
            total += len(sentence.split())
    return total


def _paragraphs(builder, body, opener, seed, opener_used=frozenset()):
    """Opener plus body sentences for one to three paragraphs within the paragraph limits.

    The angle's body comes first. A thin story then adds fact sentences, least repetitive
    first, until it holds enough prose of its own for the indexability gate. They follow
    the limitation; the caller moves a caveat that still comes last (_caveat_inside).
    """
    sentences, used, choices = [opener], set(), []
    seen = {opener}
    profiles = builder.profiles
    named_type = bool(opener_used & TYPE_SLOTS)
    # A story carries one caveat: the kind's required sentence when it has one, else the first
    # sentence that says what the data cannot show. A restated type fact is never that caveat.
    state = {
        "limited": bool(builder.required) or public_prose.is_caveat(opener, profiles),
        # Whether the sentence before the next one named the type (TYPE_ECHOES).
        "typed": bool(opener_used & NAMED_TYPE_SLOTS),
    }
    marker = None
    groups = {"identity"} if public_prose.DATABASE_ATTRIBUTION.search(opener) else set()
    groups |= {SLOT_GROUPS[slot] for slot in opener_used if slot in SLOT_GROUPS}
    names, recent = builder.names, builder.recent
    if slots_limit_hedged(builder.slots) and "limit" in body:
        # The limitation already offers the hedged explanation; skip the separate one.
        groups.add("hypothesis")

    def repeats(text):
        return any(_overlaps(text, other, names) for other in sentences)

    def named_recently(slot):
        # As whole words: "new brighton airportville" names no Brighton Airport.
        value = _plain(builder.slots.get(slot))
        named = re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)")
        return bool(value) and any(named.search(sentence) for sentence in recent)

    def candidate(name):
        """(picked, group) for the builder's best sentence to add next, or None."""
        group = BUILDER_GROUPS.get(name, name)
        if group in groups or (name in NAMED_REPEATS and named_recently(NAMED_REPEATS[name])):
            return None
        options = (name, *BUILDER_FALLBACKS.get(name, ()))
        if group == "identity" and named_type:
            # The opener already named the type; attribute it without repeating the name.
            options = ("identity_ref", *options)
        options = [
            option
            for option in options
            if all(builder.slots.get(slot) for slot in BUILDER_REQUIRES.get(option, ()))
        ]
        said = used | set(opener_used)
        clocks = {builder.slots.get(slot) for slot in said & set(CLOCK_SLOTS)}
        hide = {slot for slot in SINGLE_USE if slot in said}
        hide |= {slot for slot in CLOCK_SLOTS if builder.slots.get(slot) in clocks}
        written = " ".join(sentences)
        for slot, measure in MEASURES.items():
            if _stated(builder.slots.get(slot), written):
                hide.update(measure)
        echo = name in TYPE_ECHOES and state["typed"]
        if echo:
            hide.update(NAMED_TYPE_SLOTS)
        previous = sentences[-1].split(maxsplit=1)[0].lower()

        def overlap(text):
            # Type background is optional; a sentence recent stories already carried is left out.
            if name in OPTIONAL_REPEATS and _plain(text) in recent:
                return True
            # A limitation is a caveat, not a restatement, so only the one-limitation rule applies.
            return name != "limit" and repeats(text)

        def stale(text):
            return overlap(text) or (name in FRESH_BUILDERS and _plain(text) in recent)

        def same_start(text):
            return stale(text) or text.split(maxsplit=1)[0].lower() == previous

        def templates(option):
            forms = BUILDERS.get(option, ())
            if name in TYPE_ECHOES and not echo:
                # The type was not just named, so the sentence names it.
                return tuple(form for form in forms if TYPE_SLOTS & set(_template_slots(form)))
            return forms

        # Prefer a fresh sentence that does not open with the same word as the one before it.
        for reject in (same_start, stale, overlap):
            for option in options:
                picked = builder.pick(
                    templates(option),
                    _hash(seed, option),
                    None,
                    None,
                    skip=seen,
                    reject=reject,
                    hide=frozenset(hide),
                )
                if picked:
                    return picked, group
        return None

    def commit(name, found):
        (index, text, slots_used), group = found
        # The required sentence is the caveat the story was already counted as carrying.
        limiting = public_prose.is_caveat(text, profiles) and text != builder.required
        if limiting and state["limited"]:
            return
        state["limited"] = state["limited"] or limiting
        groups.add(group)
        groups.update(SLOT_GROUPS[slot] for slot in slots_used if slot in SLOT_GROUPS)
        sentences.append(text)
        seen.add(text)
        used.update(slots_used)
        state["typed"] = bool(set(slots_used) & NAMED_TYPE_SLOTS)
        choices.append(f"{name}{index}")

    for name in body:
        if name == "/":
            marker = len(sentences)
            continue
        found = candidate(name)
        if found:
            commit(name, found)
    remaining = list(EXTRA_BUILDERS)
    while remaining and specific_words(sentences, names, recent) < SPECIFIC_WORDS:
        options = []
        for order, name in enumerate(remaining):
            found = candidate(name)
            if found:
                grams = _grams(normalize(found[0][1], names), 3)
                share = len(grams & builder.union) / len(grams) if grams else 1.0
                options.append((share, order, name, found))
        if not options:
            break
        _share, _order, name, found = min(options, key=lambda option: option[:2])
        remaining.remove(name)
        commit(name, found)
    return sentences, used, choices, marker


# A sentence that leans on the one before it for the type (the short registry form, "lists the
# same type"); a caveat never comes between the two.
LEANS_ON_PREVIOUS = re.compile(r"\bthe same type\b", re.I)


def _caveat_inside(sentences, required=None, profiles=()):
    """sentences with a closing caveat, or the kind's required sentence, moved ahead of the
    sentence before it, so the story never ends on one; a restated type fact (profiles) is no
    caveat. A sentence that leans on the one before it (LEANS_ON_PREVIOUS) moves with it, unless
    the one before it is the opener, which stays first in a longer story."""
    if len(sentences) < 2 or not public_prose.closes_on_caveat(sentences[-1], required, profiles):
        return sentences
    at = len(sentences) - 2
    while at > 1 and LEANS_ON_PREVIOUS.search(sentences[at]):
        at -= 1
    return [*sentences[:at], sentences[-1], *sentences[at:-1]]


CLASS_TOKENS = {"num", "id", "time", "date", "reg", "type", "op", "place", "country", "category"}


def slots_limit_hedged(slots):
    limit = slots.get("limit")
    return bool(limit and public_prose.HEDGE.search(limit))


def _overlaps(first, second, names=()):
    """Two sentences that say the same thing in nearly the same words, names and numbers aside."""
    a = set(normalize(first, names)) - public_prose.STOPWORDS - CLASS_TOKENS
    b = set(normalize(second, names)) - public_prose.STOPWORDS - CLASS_TOKENS
    shared = len(a & b)
    return shared >= 3 and shared / min(len(a), len(b)) >= 0.6


def _echoes(text, other, names=(), share=0.5):
    """Whether share or more of text's word trigrams, names and numbers aside, also appear in other."""
    grams = _grams(normalize(text, names), 3)
    return bool(grams) and len(grams & _grams(normalize(other, names), 3)) / len(grams) >= share


# A thin story of three sentences or fewer stays one paragraph up to this many words.
THIN_STORY_WORDS = 120


def _split(sentences, marker=None):
    """One to three paragraphs within the limits: a thin story of three sentences or fewer stays one
    short paragraph; a longer one splits into what happened, then why it is interesting."""
    low, high = public_prose.FIELD_LIMITS["paragraph"]
    if len(sentences) < 2:
        return None
    whole = " ".join(sentences)
    if len(sentences) <= 3 and low <= len(whole) <= high and len(whole.split()) <= THIN_STORY_WORDS:
        return [whole]
    if marker and 0 < marker < len(sentences):
        first, second = " ".join(sentences[:marker]), " ".join(sentences[marker:])
        if low <= len(first) <= high and low <= len(second) <= high:
            return [first, second]
    best = None
    for cut in range(1, len(sentences)):
        first, second = " ".join(sentences[:cut]), " ".join(sentences[cut:])
        if low <= len(first) <= high and low <= len(second) <= high:
            score = abs(len(first) - len(second) * 1.3)
            if best is None or score < best[0]:
                best = (score, [first, second])
    if best:
        return best[1]
    for cut in range(1, len(sentences) - 1):
        for second_cut in range(cut + 1, len(sentences)):
            parts = [
                " ".join(sentences[:cut]),
                " ".join(sentences[cut:second_cut]),
                " ".join(sentences[second_cut:]),
            ]
            if all(low <= len(part) <= high for part in parts):
                return parts
    # Thin facts make a short story, never a padded one.
    return [whole] if low <= len(whole) <= high else None


def seo_title(facts, slots=None, *, recent=None):
    """Deterministic title tag text within the SEO field limit, unique by a date suffix."""
    slots = slots or build_slots(facts)
    limits = public_prose.FIELD_LIMITS["seo_title"]
    titles = {_plain(item.get("seo_title")) for item in _recent_list(recent)}
    suffix = slots.get("date_short")
    check = public_prose.prose_checker(facts)

    def clean(text):
        return text and not check(text) and not public_prose.DASH.search(text)

    for template in seo_candidates(facts.get("kind", "")):
        text, _used = fill(template, slots, limits=limits)
        if not clean(text):
            continue
        if _plain(text) not in titles:
            return text
        if suffix:
            dated, _ = fill(template + ", {date_short}", slots, limits=limits)
            if clean(dated) and _plain(dated) not in titles:
                return dated
    ident = slots.get("ident") or "Aircraft"
    # A class phrase ("twin-jet airplane") starts in lower case.
    fallback = _tidy(f"{slots['type_short']} {ident}") if slots.get("type_short") else ident
    return (f"{fallback}, {suffix}" if suffix else fallback)[: limits[1]]


def _sources(facts, used_facts):
    cited = {item.get("source") for item in facts.get("facts", []) if item.get("id") in used_facts}
    cited.discard(None)
    return [dict(source) for source in facts.get("sources", []) if source.get("id") in cited]


def _story(builder, angle, variant, headline, seo, dek, paragraphs, used):
    """A story in STORY_KEYS order; every source the text relies on is cited, even past the fact cap."""
    if (
        builder.kind == "identity_conflict"
        and builder.required
        and paragraphs
        and not public_prose.carries(paragraphs, builder.required)
    ):
        paragraphs = public_prose.insert_before_last(paragraphs, builder.required)
    story = {
        "version": PROSE_POLICY,
        "source": "template",
        "angle": angle,
        "variant": variant,
        "headline": headline,
        "seo_title": seo,
        "dek": dek,
        "paragraphs": paragraphs,
        "facts_digest": builder.facts["digest"],
        "facts": builder.story_facts(used | {"date", "ident"}),
        "sources": _sources(builder.facts, builder.story_facts(used, cap=False)),
    }
    story.update(skeletons(story))
    return {key: story[key] for key in STORY_KEYS if key in story}


def fixed_headline(facts):
    """The deterministic headline of a kind whose headline is fixed, else None.

    Both writers use it: the kind's angles carry only this form, and the AI writer's
    candidates take it in place of their own.
    """
    fixed = public_prose.fixed_headline(facts)
    return fixed[0] if fixed else None


def _headlines(builder, angle, seed):
    """Headlines to try in turn: the angle's rotated variants within the headline limits. A fixed
    kind's angles carry its one form, which is not free text, so no length window applies."""
    tried = set()
    limits = (
        None if builder.kind in public_prose.FIXED_HEADLINE_KINDS else public_prose.FIELD_LIMITS["headline"]
    )
    while headline := builder.pick(angle.headlines, _hash(seed, "h"), limits, "headline", skip=tried):
        tried.add(headline[1])
        yield headline


def _parts(builder, angle, seed):
    """(headline, standfirst, opener), each adding to what came before, or None.

    A standfirst may restate the headline's fact, but not in the headline's words,
    and the opener does not repeat the standfirst. When every opener would repeat
    a headline's standfirst, the next headline is tried; with none better, the
    first combination stands.
    """
    limits = public_prose.FIELD_LIMITS
    deks = angle.deks
    if builder.kind.startswith(rules.SQUAWK_PREFIXES):
        deks = tuple(template for template in deks if CAVEAT_DEK.search(template))
    first = None
    for headline in _headlines(builder, angle, seed):
        if first is not None and not builder.varied("headline", headline[1]):
            # pick() offers a headline with variety issues only once every clean one is tried.
            break
        dek = builder.pick(
            deks,
            _hash(seed, "d"),
            limits["dek"],
            "dek",
            demote=lambda text, title=headline[1]: _echoes(title, text, builder.names, 0.75),
        )
        if not dek:
            continue
        opener = builder.pick(
            angle.openers,
            _hash(seed, "o"),
            (20, 400),
            "opener",
            demote=lambda text, standfirst=dek[1]: _echoes(text, standfirst, builder.names),
        )
        if not opener:
            continue
        if not _echoes(opener[1], dek[1], builder.names) and (
            first is None or (builder.varied("dek", dek[1]) and builder.varied("opener", opener[1]))
        ):
            return headline, dek, opener
        first = first or (headline, dek, opener)
    return first


def _candidate(builder, angle, key, seo):
    """(story, entry, variety issues) for one angle, or None when it renders no valid story."""
    seed = _hash(key, angle.id, PROSE_POLICY)
    parts = _parts(builder, angle, seed)
    if parts is None:
        return None
    headline, dek, opener = parts
    sentences, body_used, choices, marker = _paragraphs(builder, angle.body, opener[1], seed, opener[2])
    paragraphs = _split(_caveat_inside(sentences, builder.required, builder.profiles), marker)
    if paragraphs is None:
        return None
    variant = f"h{headline[0]}/d{dek[0]}/o{opener[0]}/" + ".".join(choices)
    used = headline[2] | dek[2] | opener[2] | body_used
    story = _story(builder, angle.id, variant, headline[1], seo, dek[1], paragraphs, used)
    if public_prose.story_issues(story, builder.facts):
        return None
    entry = _entry({**story, "kind": builder.kind})
    return story, entry, _issues(entry, builder.corpus, type_code=builder.type_code)


def render_with_report(facts, key, *, recent=None):
    """(story, Report). Never raises for facts from story_facts.build()."""
    if not isinstance(facts, dict) or not isinstance(facts.get("facts"), list):
        raise ValueError("render() needs story facts")
    recent = _recent_list(recent)
    slots = build_slots(facts)
    builder = _Builder(facts, slots, Corpus(recent, day=_day(story_facts.value(facts, "obs.date"))))
    seo = seo_title(facts, slots, recent=recent)
    report = Report()
    fallback = None
    for angle in _ordered(facts, slots, recent):
        found = _candidate(builder, angle, key, seo)
        if found is None:
            continue
        story, entry, issues = found
        if not issues:
            return story, report
        score = variety_score(entry, builder.corpus, issues)
        if fallback is None or score < fallback[0]:
            fallback = (score, story, issues)
    if fallback is None:
        story, issues = _fallback_story(facts, slots, key, seo)
        report.fallback = True
        if issues:
            # Nothing passes the story checks; the caller must not publish this story.
            report.publishable = False
            report.issues = issues
        return story, report
    report.variety_relaxed = True
    report.issues = fallback[2]
    return fallback[1], report


def render(facts, key, *, recent=None):
    """A deterministic template story for the facts; never raises for valid facts.

    Callers check render_with_report()'s `publishable` flag, or the story itself, before
    publishing: on facts too thin for any valid story the result fails the story checks.
    """
    return render_with_report(facts, key, recent=recent)[0]


# Fact sentences for the plain record that ends the fallback chain.
MINIMAL_BODY = (
    "identity",
    "identity_reg",
    "code_def",
    "status_def",
    "alloc",
    "country",
    "reading",
    "track",
    "limit",
)
# The plain record's own text, filled from the facts and tried in order. Every part is
# checked on its own and the whole story is checked again.
MINIMAL_HEADLINES = (
    "{type} {reg} flies near {place}",
    "[{type} ]{ident} in the air on {day_month}",
    "{ident_named|cap} in the air on {date}",
    "Aircraft reports from {ident_named} on {date}",
)
MINIMAL_DEKS = (
    "It reported {alt}[ and {gs}] on {day_month}[ near {place}].",
    "{ident_named|cap} reported {alt_phrase}[ and a ground speed of {gs}] on {date}.",
    "The evidence card below lists the reports from {ident_named} on {date}.",
    # A short identity still reaches the standfirst's minimum length with facts it has.
    "The evidence card below lists the reports from {type} {reg} on {date}.",
    "The evidence card below lists the reports from {ident_named} on {weekday}, {date}.",
)
MINIMAL_OPENERS = (
    "On {date}, {ident_named} reported {alt_phrase}[ near {place}].",
    "{ident_named|cap} sent reports on {date}.",
)
# A story that names the registration or type says they come from the aircraft database.
MINIMAL_ATTRIBUTION = (
    "The aircraft database lists {reg} as {type_a}.",
    "The aircraft database matches {ident_named} to {type_a}.",
    "The registration {reg} comes from the aircraft database.",
)
# Added until the story reaches the paragraph minimum: a fact from the record, never filler.
MINIMAL_PADDING = ("Its transponder address is {hex}.",)


def _filled(templates, builder, limits=None):
    """(text, slots used) for each template that renders within limits and passes the prose checks."""
    for template in templates:
        text, used = fill(template, builder.slots, limits=limits)
        if text and builder.clean(text) and not public_prose.DASH.search(text):
            yield text, used


def _house_headlines(kind):
    """The headline forms every angle of a kind carries, the generic and plain ones too: a fixed
    kind's one headline, or a watchlist story's lead with the aircraft and its entry; else None."""
    if kind in public_prose.FIXED_HEADLINE_KINDS:
        return FIXED_FORM
    return WATCHLIST_HEADLINES if kind == "watchlist_match" else None


def _plain_record(builder, key, seo):
    """(story, issues): the plain record, with one more fact sentence when thin facts fall short."""
    slots, facts = builder.slots, builder.facts
    sentences, used = [], set()
    opener = next(_filled(MINIMAL_OPENERS, builder, (20, 400)), None)
    if opener:
        sentences, used, _choices, _marker = _paragraphs(
            builder,
            (*MINIMAL_BODY, *REQUIRED_BUILDERS.get(builder.kind, ())),
            opener[0],
            _hash(key, "minimal", PROSE_POLICY),
            frozenset(opener[1]),
        )
        used = used | opener[1]
    if (slots.get("reg") or slots.get("type")) and not public_prose.DATABASE_ATTRIBUTION.search(
        " ".join(sentences)
    ):
        for text, slots_used in _filled(MINIMAL_ATTRIBUTION, builder):
            sentences.insert(1 if sentences else 0, text)
            used |= slots_used
            break
    # The caveat moves in once every sentence but the padding is placed; padding goes last.
    sentences = _caveat_inside(sentences, builder.required, builder.profiles)
    paragraphs = _split(sentences)
    written = " ".join(sentences).lower()
    for text, slots_used in _filled(MINIMAL_PADDING, builder) if not paragraphs else ():
        # A sentence whose every value the story already gives adds nothing.
        if (slots_used and all(str(slots[name]).lower() in written for name in slots_used)) or any(
            text == other or _overlaps(text, other, builder.names) for other in sentences
        ):
            continue
        sentences.append(text)
        used |= slots_used
        paragraphs = _split(sentences)
        break
    if not paragraphs:
        paragraphs = [" ".join(sentences)] if sentences else []
    best = None
    limits = public_prose.FIELD_LIMITS
    # A kind with a house headline form keeps it even in the plain record.
    house = _house_headlines(builder.kind)
    headlines = (
        list(_filled(house, builder, None if house == FIXED_FORM else limits["headline"])) if house else []
    )
    headlines = headlines or _filled(MINIMAL_HEADLINES, builder, limits["headline"])
    for headline, headline_used in headlines:
        for dek, dek_used in _filled(MINIMAL_DEKS, builder, limits["dek"]):
            story = _story(
                builder, "minimal", "minimal", headline, seo, dek, paragraphs, used | headline_used | dek_used
            )
            issues = public_prose.story_issues(story, facts)
            if not issues:
                return story, []
            best = best or (story, issues)
    if best:
        return best
    # Not even a headline renders: an empty record that names its failure.
    story = _story(builder, "minimal", "minimal", "", seo, "", paragraphs, used)
    return story, public_prose.story_issues(story, facts) or ["story:no_headline"]


def _fallback_story(facts, slots, key, seo):
    """(story, issues): the generic angles, then the plain record.

    The plain record pads thin facts until both paragraphs reach their minimum length,
    so it passes the story checks whenever the facts name a time; `issues` lists what
    still fails otherwise, and the caller must not publish that story.
    """
    builder = _Builder(facts, slots, Corpus([]))
    # The generic angles carry what the kind's own angles do: its required sentences, and a
    # fixed kind's one headline.
    required = REQUIRED_BUILDERS.get(builder.kind, ())
    house = _house_headlines(builder.kind)
    for angle in GENERIC_ANGLES:
        angle = replace(angle, body=(*angle.body, *required), headlines=house or angle.headlines)
        if found := _candidate(builder, angle, key, seo):
            return found[0], []
    return _plain_record(builder, key, seo)


# ---------------------------------------------------------------- angle library
# Every string below is public prose, written and then edited with Humanizer in file
# mode: plain verbs, one fact per clause, varied sentence length, no dashes, no staged
# contrasts or closers, and every hypothesis hedged. Slots come from story facts; a
# sentence whose slots are missing is skipped. "/" in a body list marks the preferred
# paragraph break: what was recorded first, then why it is interesting.


# A type-mismatch story's one line about the disagreement: a statement, not a cause. The AI
# writer may use it too (public_writer.KIND_NOTES).
MISMATCH_LINE = "The reading and the listed type do not agree."
BUILDERS = {
    "identity": (
        "The aircraft database lists {reg} as {type_a}.",
        "By its database record, {reg} is {type_a}.",
        "Database records give {reg}'s type as {type}.",
        "{reg} is registered as {type_a}, according to the aircraft database.",
        "{reg} appears in the aircraft database as {type_a}.",
        "In the aircraft database, {reg} is {type_a}.",
    ),
    "identity_ref": (
        "That type comes from the aircraft database record for {reg}.",
        "The aircraft database is the source for that type.",
        "Its type comes from the aircraft database.",
    ),
    "identity_any": (
        "The aircraft database matches {ident_named} to {type_a}.",
        "Database records list {ident_named} as {type_a}.",
    ),
    "identity_class": (
        "The aircraft database lists {reg} under type code {type_code}, {type_class_a}.",
        "By its database record, {reg} has type code {type_code}, {type_class_a}.",
    ),
    "identity_group": (
        "The aircraft database lists {reg} as {group|a}.",
        "By its database record, {reg} is {group|a}.",
    ),
    "identity_reg": (
        "The aircraft database lists address {hex} under registration {reg}.",
        "Database records give the registration for address {hex} as {reg}.",
    ),
    "operator": (
        "Its callsign, {call}, starts with {designator}, the ICAO designator assigned to {op}.",
        "The {designator} prefix in the callsign {call} is assigned to {op}.",
        "{call} uses the {designator} designator, which ICAO assigns to {op}.",
        "ICAO assigns the {designator} prefix in {call} to {op}.",
    ),
    "country": (
        "The {prefix} prefix on {reg} is the nationality mark of {country_the}.",
        "{reg} carries the {prefix} nationality mark of {country_the}.",
        "{reg} is registered in {country_the}, as its {prefix} prefix shows.",
        "Its {prefix} prefix puts {reg} on the aircraft register of {country_the}.",
        "The registration {reg} begins with {prefix}, the mark {country_the} uses.",
    ),
    "alloc": (
        "Its transponder address, {hex}, sits in the block ICAO allocates to {alloc_country_the}.",
        "The address {hex} falls in the range ICAO allocates to {alloc_country_the}.",
    ),
    "reading": (
        "It reported {alt_phrase} and a ground speed of {gs}[ at {m_clock}].",
        "[At {m_clock}, ]it reported {alt_phrase} and {gs} over the ground.",
        "Its saved report put it at {alt} and {gs}[, {mph}].",
        "It was logged at {alt} with a ground speed of {gs_exact}.",
    ),
    "reading_alt": (
        "It reported {alt_phrase}[ at {m_clock}].",
        "Its saved report gives {alt_phrase}.",
    ),
    "reading_gs": (
        "Its ground speed was {gs}[ at {m_clock}].",
        "It reported a ground speed of {gs_exact}.",
    ),
    "track": (
        "{positions|cap} positions[ near {place}] cover {miles} of its path.",
        "Its track[ near {place}] spans {positions} positions over {track_time}.",
        "The record holds {positions} positions[ near {place}], logged over {track_time}.",
        "Over {track_time}, {positions} positions traced its path[ near {place}].",
    ),
    "airport": (
        "It came within {airport_miles} of {airport}.",
        "Its track came within {airport_miles} of {airport}.",
        "The positions come within {airport_miles} of {airport}.",
    ),
    "age": (
        "The aircraft database lists {reg} as built in {built}, which makes it {age} years old.",
        "Built in {built}, according to the aircraft database, {reg} is {age} years old.",
    ),
    "profile": ("{tp1}",),
    "profile2": ("{tp2}",),
    "limit": ("{limit}",),
    "military_flag": (
        "The aircraft database flags it as military.",
        "Its database record carries the military flag.",
    ),
    "wl_entry": (
        "Plane Alert DB, a community watchlist, associates {ident} with {wl_assoc}.",
        "The community watchlist Plane Alert DB lists {ident} under {wl_assoc}.",
    ),
    "wl_detail": (
        "Its Plane Alert DB entry describes the aircraft as {wl_desc}[ and counts it among the list's "
        "{wl_group} aircraft].",
        "Plane Alert DB describes it as {wl_desc}[, one of the list's {wl_group} entries].",
    ),
    "wl_why": (
        'Plane Alert DB files {ident} under "{wl_list}"[, its category for {wl_note}].[ Its Plane Alert DB tags are {wl_tags}.]',
        'In Plane Alert DB, {ident} sits under "{wl_list}"[, its category for {wl_note}][, with the tags {wl_tags}].',
    ),
    # Said once, attributed to the entry's link or to Wikidata; a plain fact, never a caveat.
    # The last form names the label and the alias the entry carries ("Yankee Air Museum").
    "wl_who": (
        "The entry's link leads to {who}, according to Wikidata.",
        "Its link in Plane Alert DB leads to {who}, as Wikidata describes it.",
        "The entry's link leads to {who_label}, which the entry lists as {wl_assoc}: {who_desc}, "
        "according to Wikidata.",
    ),
    # The type's cited certified limit, stated beside the reading; never why they disagree.
    # "As a type" because the figure is the type's; the phrase itself says "certified".
    "rated": (
        "The {type_short} {rated_phrase}.",
        "As a type, the {type_short} {rated_phrase}.",
    ),
    # The one hedge a speed record carries, and a rated story's, as the checks require them
    # word for word (public_prose.REQUIRED_SENTENCES).
    "record_hedge": (public_prose.REQUIRED_SENTENCES["seasonal_speed_record"],),
    "rated_hedge_altitude": (public_prose.REQUIRED_SENTENCES["above_rated_altitude"],),
    "rated_hedge_speed": (public_prose.REQUIRED_SENTENCES["above_rated_speed"],),
    # A rated story states the certified figure and its sheet once.
    "rated_source": ("The type certificate data sheet lists a {lim_name} of {lim_limit}.",),
    # Reports over the limit are counted, never read as a value held for a span of time.
    "ceiling_reading": (
        "{reg} sent {lim_reports} reports of {lim_reading} or higher over {lim_time}[ on {date}].",
        "Over {lim_time}[ on {date}], {lim_reports} reports from {reg} gave {lim_reading} or more.",
    ),
    "ceiling_margin": (
        "Each of them sat at least {lim_margin} above the certified figure.",
        "Every one of those reports was at least {lim_margin} above it.",
    ),
    # The report closest to the limit: its ground speed, airspeed, and Mach number are one calculation.
    "speed_estimate": (
        "For the report closest to the limit, allowing for a modelled[ {lim_wind_adj}] wind, a ground speed "
        "of {lim_decisive_gs} works out to {airspeed_phrase}, or {lim_compare}.",
        "The report closest to the limit had {lim_decisive_gs} over the ground; with a modelled"
        "[ {lim_wind_adj}] wind taken out, that is {airspeed_phrase}, or {lim_compare}.",
    ),
    "speed_report": (
        "In the report closest to the limit, the aircraft's own true airspeed was {lim_airspeed}, or "
        "{lim_compare}.",
        "The aircraft reported a true airspeed of {lim_airspeed} in the report closest to the limit, or "
        "{lim_compare}.",
    ),
    "speed_margin": (
        "Each of the {lim_reports} reports over the limit ran at least {lim_margin} past it at its altitude.",
        "Every one of those reports ran at least {lim_margin} past the certified limit at its altitude.",
    ),
    "neighbors": (
        "The report before it read {nb_before} and the one after it {nb_after}.",
        "Its neighbors read {nb_before} just before and {nb_after} just after.",
        "Just before it the record shows {nb_before}; just after, {nb_after}.",
    ),
    "neighbor_range": (
        "Every other report in that stretch read between {nb_min} and {nb_max}.",
        "The surrounding reports stayed between {nb_min} and {nb_max}.",
    ),
    "neighbor_gaps": (
        "Those neighbors came {nb_gaps} the outlier.",
        "The reports on either side came {nb_gaps} it.",
    ),
    "mismatch_line": (MISMATCH_LINE,),
    # A mismatch story's second registry: its type stated as a fact beside the database's, never a
    # cause. The registry's lower-case name never opens a sentence.
    "registry_type": (
        "Another registry, {registry}, lists the same address as {other_type_a}.",
        "A second registry, {registry}, lists this address as {other_type_a}.",
    ),
    # A rated or mismatch story's second registry agreed with the listed type (typecheck.py). Right
    # after a sentence that named the type, only the second form is used (TYPE_ECHOES).
    "registry_agrees": (
        "The aircraft database and {registry} both list it as {type_a}.",
        "A second registry, {registry}, lists the same type.",
    ),
    # The type's usual ground speed in these records (story_facts._norm): a population range in
    # one phase and band, stated about the type, never as a limit or beside this aircraft's reading.
    "norm": (
        "In these records, {type_plural} {norm_phase} {norm_band} usually show ground-speed readings of "
        "{norm_low} to {norm_high}.",
        "{type_plural|cap} {norm_phase} {norm_band} usually show ground-speed readings of {norm_low} to "
        "{norm_high} in these records.",
    ),
    "code_def": (
        "Code {code} is reserved for {code_meaning}.",
        "Code {code} is the transponder code for {code_meaning}.",
        "Transponder code {code} stands for {code_meaning}.",
    ),
    "code_duration": (
        "The code stayed on for {duration} in this record.",
        "It kept the code for {duration}.",
    ),
    "code_sequence": (
        "The record shows the codes {sequence} in turn.",
        "Its transponder moved through {sequence}.",
    ),
    "status_def": (
        "Its ADS-B status field read {status}, which stands for {status_meaning}.",
        "The aircraft's own status field showed {status}, which stands for {status_meaning}.",
    ),
    "tcas_threat": (
        "The advisory named {p1_type_a}[, {p1_ident},] as the other aircraft.",
        "The other aircraft in the advisory was {p1_type_a}[, {p1_ident}].",
    ),
    "tcas_spacing": (
        "At the closest recorded point between the aircraft, they were {sep} and {vsep} apart.",
        "The closest recorded spacing between the two aircraft was {sep} horizontally and {vsep} vertically.",
    ),
    "tcas_rate": (
        "After the advisory, its vertical rate changed by {rate_change}.",
        "Its vertical rate changed by {rate_change} after the advisory.",
    ),
    "shape": (
        "Over {shape_duration}, it {shape_past}[ near {place}].",
        "The track shows it {shape_past_ing}[ near {place}] for {shape_duration}.",
    ),
    "pair": (
        "The other aircraft was {p1_type_a}[, {p1_ident}].",
        "Its companion was {p1_type_a}[, {p1_ident}].",
    ),
    "pair_spacing": (
        "At their closest, the two aircraft were {sep} apart horizontally and {vsep} vertically.",
        "The closest recorded spacing between the aircraft was {sep} and {vsep}.",
    ),
    "formation_hyp": (
        "Spacing that close for that long is consistent with formation flight.",
        "Formation flight is one possible explanation for spacing this close.",
    ),
    "survey_hyp": (
        "Evenly spaced legs like these may come from aerial survey or mapping work.",
        "A grid of straight, evenly spaced legs is typical of aerial survey or mapping.",
    ),
    "survey_legs": (
        "The legs averaged {leg_length} long and {leg_spacing} apart.",
        "Each leg averaged {leg_length}, with about {leg_spacing} between them.",
    ),
    "soaring_hyp": (
        "Climbing in circles like this is consistent with soaring in rising air.",
        "Circling climbs like these may come from soaring in rising air.",
    ),
    "balloon_hyp": (
        "A track this slow and this high is consistent with a high-altitude balloon.",
        "Slow movement at that height looks like a high-altitude balloon.",
    ),
    "balloon_spike": (
        "One report read {spike}, between neighbors of {spike_before} and {spike_after}, and looks like a data "
        "error.",
        "A single report of {spike} stands apart from the rest and may be a data error.",
    ),
    "balloon_range": (
        "Its altitude stayed between {alt_min} and {alt_max} while it moved {gs_min} to {gs_max} over the ground.",
        "Ground speed ran from {gs_min} to {gs_max}, at altitudes from {alt_min} to {alt_max}.",
    ),
    "value_line": (
        "One report among them read {value_text}[ at {peak_clock}].",
        "The exception is a single report of {value_text}.",
    ),
    "descent_profile": (
        "It went from {start_alt} to {end_alt}, a change of {change}.",
        "The episode began at {start_alt} and ended at {end_alt}.",
    ),
    "peak_rate": (
        "The rate peaked at {peak_rate}[ at {peak_clock}].",
        "Its steepest reported rate was {peak_rate}.",
    ),
    "stack": (
        "{aircraft_total|cap} aircraft took part[ near {place}].",
        "The circling group counted {aircraft_total} aircraft.",
    ),
    "gnss_count": (
        "{affected|cap} aircraft reported lower navigation integrity within the same window.",
        "The drop showed up on {affected} aircraft[ near {place}].",
    ),
    # The comparison starts where the saved reports for the type start, which can be after the
    # season began; only a baseline that covers the whole season names the season.
    "season": (
        "The comparison covers reports in these records since {season_start}[, when meteorological "
        "{season_name} began].",
        "Since {season_start}, no saved report of the type in these records was faster.",
    ),
    "season_previous": (
        "The previous best for the type in these records since {season_start} was {previous_gs}.",
        "Before this, the type's best in these records since {season_start} was {previous_gs}.",
    ),
    "jump": (
        "Its reported position moved {jump_nm} in {jump_time}.",
        "Its position reports {jump_time} apart were {jump_nm} from each other.",
    ),
    "gap": (
        "Its pressure altitude read {alt_baro} while its geometric altitude read {alt_geom}.",
        "The two altitude fields disagreed: {alt_baro} by pressure, {alt_geom} by satellite.",
    ),
    "emitter": (
        "It broadcast emitter category {emitter}, the {emitter_label} category.",
        "Its transponder reported emitter category {emitter}, the {emitter_label} class.",
    ),
    "implied": (
        "Its transponder address corresponds to registration {implied}, not {reg}.",
        "Under the FAA's N-number allocation, its address maps to {implied} rather than {reg}.",
    ),
    "address_agrees": ("The address maps to {address_agrees}, agreeing with the database registration.",),
    # Identity checks that disagree, each stated only when that check found the conflict.
    "callsign_conflict": (
        "The {callsign_source} reads {callsign_conflict}, while the database lists {reg}.",
        "The database registration is {reg}; the {callsign_source} reads {callsign_conflict}.",
    ),
    "country_conflict": (
        "Its registration points to {reg_conflict_the}, but its address sits in the block ICAO allocates to "
        "{alloc_conflict_the}.",
        "The address {hex} belongs to the block ICAO allocates to {alloc_conflict_the}, not to {reg_conflict_the}.",
    ),
    "airspeed": (
        "It reported a true airspeed of {tas} and a ground speed of {gs}.",
        "Its true airspeed read {tas}, against {gs} over the ground.",
    ),
}
# A group counts as said once a sentence has used one of these slots.
SLOT_GROUPS = {
    "alt": "reading",
    "alt_phrase": "reading",
    "gs": "reading",
    "gs_exact": "reading",
    "positions": "track",
    "track_time": "track",
    "miles": "track",
    "airport": "airport",
    "shape_past": "shape",
    "shape_past_ing": "shape",
    "sep": "spacing",
    "vsep": "spacing",
    "p1_type_a": "pair",
    "p1_ident": "pair",
    "partner": "pair",
    "legs": "survey_legs",
    "leg_spacing": "survey_legs",
    "start_alt": "descent_profile",
    "end_alt": "descent_profile",
    "peak_rate": "peak_rate",
    "nb_before": "neighbors",
    "nb_after": "neighbors",
    "nb_min": "neighbor_range",
    "nb_max": "neighbor_range",
    "aircraft_total": "stack",
    "affected": "gnss_count",
    "jump_nm": "jump",
    "alt_baro": "gap",
    "alt_geom": "gap",
    "emitter": "emitter",
    "code_meaning": "code_def",
    "status_meaning": "code_def",
    "built": "age",
    "age": "age",
    "tp1": "profile",
    "tp2": "profile2",
    "op": "operator",
    "alloc_country_the": "nationality",
    "country_the": "nationality",
    "rated_phrase": "rated",
    "advisory": "advisory",
    "rate_change": "tcas_rate",
    "spike": "balloon_spike",
    "duration": "duration",
    "sequence": "code_sequence",
    "implied": "implied",
    "tas": "airspeed",
    "season_start": "season",
    "previous_gs": "season_previous",
    "norm_low": "norm",
    "alt_min": "balloon_range",
    "gs_min": "balloon_range",
    "lim_margin": "rated_margin",
    "airspeed_phrase": "rated_airspeed",
    "lim_compare": "rated_airspeed",
    "who": "wl_who",
    "who_label": "wl_who",
}
# Builders that may only run when these slots exist: most name no slot in their sentences.
BUILDER_REQUIRES = {
    "military_flag": ("military_flag",),
    "mismatch_line": ("value", "type"),
    "registry_agrees": ("tc_agrees", "type_a"),
    # A certified figure belongs to a named type, never to a class phrase ("twin-jet airplane").
    "rated": ("type",),
    "record_hedge": ("speed_record",),
    "rated_hedge_altitude": ("rated_altitude",),
    "rated_hedge_speed": ("airspeed_origin",),
    "speed_estimate": ("airspeed_estimated",),
    "speed_report": ("airspeed_reported",),
    "formation_hyp": ("sep",),
    "survey_hyp": ("legs",),
    "soaring_hyp": ("gain",),
    "balloon_hyp": ("max_alt",),
}
# A builder that renders nothing hands over to these, in order.
BUILDER_FALLBACKS = {
    "identity": ("identity_class", "identity_any", "identity_group", "identity_reg"),
    "reading": ("reading_alt", "reading_gs"),
}
# Builders that say the same kind of thing; a story uses at most one from each group.
BUILDER_GROUPS = {
    "identity": "identity",
    "identity_ref": "identity",
    "identity_any": "identity",
    "identity_class": "identity",
    "identity_group": "identity",
    "identity_reg": "identity",
    "reading": "reading",
    "reading_alt": "reading",
    "reading_gs": "reading",
    "mismatch_line": "hypothesis",
    "registry_type": "registry",
    "registry_agrees": "registry",
    "formation_hyp": "hypothesis",
    "survey_hyp": "hypothesis",
    "soaring_hyp": "hypothesis",
    "balloon_hyp": "hypothesis",
    "pair": "pair",
    "tcas_threat": "pair",
    "pair_spacing": "spacing",
    "tcas_spacing": "spacing",
    "code_duration": "duration",
    "track": "track",
    "country": "nationality",
    "alloc": "nationality",
    "rated_hedge_altitude": "rated_hedge",
    "rated_hedge_speed": "rated_hedge",
    "ceiling_margin": "rated_margin",
    "speed_margin": "rated_margin",
    "speed_estimate": "rated_airspeed",
    "speed_report": "rated_airspeed",
}


# Identity kinds: the aircraft is the story.
OPERATOR_SNAPSHOT = Angle(
    "operator_snapshot",
    "The airline or unit behind the callsign prefix, then altitude, speed, and place.",
    headlines=(
        "{op_short} {type_short} {reg} at {alt}[ near {place}]",
        "{type} {reg} flies as {op} flight {call}",
        "{op_short} callsign {call} on {type} {reg}[ near {place}]",
        "{op} {type_short} {reg} logged[ near {place}][ at {alt}]",
        "{call} brings {op_short}'s {type_short} {reg} past {place_short}",
    ),
    deks=(
        "Flying as {call}, the {type_short} reported {gs}[ at {alt}]. The {designator} prefix is assigned to {op}.",
        "The {designator} prefix in its callsign is assigned to {op}. It reported {alt}[ and {gs}][ on {day_month}].",
        "As {call}, {reg} reported {alt}[ and {gs}] at {m_clock} on {day_month}[ near {place}].",
        "On {day_month}, {op} flight {call} reported {alt}[ and {gs}][ near {place}].",
        "{reg} carried the {op_short} callsign {call} on {day_month}. The record holds {positions} positions"
        "[ near {place}].",
    ),
    openers=(
        "{reg} broadcast callsign {call} at {clock} on {date}.",
        "Flying as {call}, {reg} reported {alt_phrase} at {m_clock} on {date}.",
        "The {designator} prefix on callsign {call} is assigned to {op}.",
        "{midnight|cap} on {date}, {reg} was flying as {call}.",
        "{op} flight {call} passed[ near {place}] on {date}, flown by {type_a}.",
        "On {date}, the callsign {call} came from {reg}[ near {place}].",
    ),
    body=(
        "identity",
        "operator",
        "country",
        "reading",
        "track",
        "/",
        "age",
        "profile",
        "limit",
    ),
    tier=1,
    requires=("op", "call", "reg", "type"),
)
TYPE_PROFILE = Angle(
    "type_profile",
    "A sourced fact about the type as the hook, tied to this airframe's record.",
    headlines=(
        "{type} {reg}, {type_cat_a}, at {alt}[ near {place}]",
        "[{op_short} ]{type} {reg} logged at {alt}[ near {place}]",
        "{type} {reg}[ near {place_short}] at {alt} and {gs}",
        "{type_cat|cap} {reg}, {type_a}, passes[ near {place}]",
    ),
    deks=(
        "{positions|cap} positions put the {type_short} at {alt}[ near {place}] on {day_month}.",
        "{reg} reported {alt}[ and {gs}] at {m_clock} on {day_month}[, flying as {call}][ near {place}].",
        "On {day_month}, {reg} reported {alt}[ near {place}].[ The record holds {positions} positions.]",
        "The {type_short} in this record is {reg}[, flying as {call}], logged at {alt} on {day_month}.",
        "{reg}, {type_a} by its database record, was logged on {day_month}[ at {alt}][ near {place}].",
    ),
    openers=("{tp1}", "{tp2}", "{tp3}"),
    body=(
        "identity",
        "operator",
        "reading",
        "track",
        "/",
        "profile",
        "profile2",
        "age",
        "limit",
    ),
    tier=1,
    requires=("tp1", "reg", "type"),
)
BRIEF_GLIMPSE = Angle(
    "brief_glimpse",
    "Only a few positions or no readings: say plainly how little the record holds.",
    headlines=(
        "{type} {reg} caught briefly[ near {place}]",
        "A brief pass by {type} {reg}[ near {place}]",
        "{type} {reg} logs {positions} positions in {track_time}[ near {place}]",
    ),
    deks=(
        "{positions|cap} [{estimated} ]positions over {track_time}, logged at {clock} on {day_month}, are all "
        "this record holds.",
        "The record holds only {positions} positions for {reg}, logged at {clock} on {day_month}[ near {place}].",
    ),
    openers=(
        "The aircraft database lists {reg} as {type_a}[, {type_cat_a}].",
        "On {date}, the record for {reg} holds only {positions} positions[ near {place}].",
        "{reg} left a short trail[ near {place}] on {date}: {positions} positions over {track_time}.",
    ),
    body=("identity", "reading", "/", "profile", "age", "limit"),
    tier=2,
    requires=("reg", "type", "positions", "positions_few"),
)
PLACE_FIRST = Angle(
    "place_first",
    "Lead with where the track ran, then what the aircraft reported there.",
    headlines=(
        "{type} {reg} logged near {place}[ at {alt}]",
        "{type} {reg} passes near {place_short}[ at {alt}]",
        "Near {place}, {type} {reg} reports {alt}",
        "{type} {reg} at {alt}, logged near {place_short}",
    ),
    deks=(
        "The {type_short} reported {alt}[ and {gs}] near {place} at {m_clock} on {day_month}.",
        "{positions|cap} positions near {place} trace {miles} of its path on {day_month}.",
        "Near {place}, {reg} reported {alt} at {m_clock} on {day_month}[, flying as {call}].",
        "{reg} passed near {place} on {day_month}[ as {call}], with {positions} positions over {track_time}.",
        "Its path near {place} on {day_month} covers {miles}[, at {alt}]. The database lists it as {type_a}.",
    ),
    openers=(
        "Near {place}, {reg} reported {alt_phrase} at {m_clock} on {date}.",
        "{positions|cap} positions near {place} trace {reg}'s path on {date}.",
        "The track of {reg} on {date} runs near {place}.",
        "On {date}, the track of {reg} passed near {place}.",
    ),
    body=("identity", "operator", "reading", "track", "/", "age", "profile", "limit"),
    tier=2,
    requires=("place", "reg", "type"),
)
SNAPSHOT = Angle(
    "snapshot",
    "What the aircraft is and one detail that pictures the flight; never record timestamps.",
    headlines=(
        "{type} {reg} flies near {place}",
        "{type} {reg} passes near {place}[ on {day_month}]",
        "{type} {reg} at {alt}[ near {place}]",
        "[{type} ]{ident} in the air[ near {place}][ on {day_month}]",
    ),
    deks=(
        "It reported {alt_phrase}[ and a ground speed of {gs}] on {day_month}.",
        "On {day_month}, {ident} reported {alt}[ and {gs}][ near {place}].",
        "{ident} was logged at {alt}[ and {gs}][ near {place}] on {day_month}.",
        "The record puts {ident} at {alt}[ near {place}] on {day_month}[, moving at {gs} over the ground].",
    ),
    openers=(
        "On {date}, {ident} reported {alt_phrase}[ and a ground speed of {gs}].",
        "{ident} reported {alt_phrase} on {date}[ near {place}].",
    ),
    body=(
        "identity",
        "operator",
        "country",
        "reading",
        "track",
        "/",
        "age",
        "profile",
        "limit",
    ),
    tier=3,
    requires=("ident",),
)
VARIANT_PROFILE = Angle(
    "variant_profile",
    "The database names a notable military or government variant; describe the airframe, never its mission.",
    headlines=(
        "{variant} {reg}[ near {place}]",
        "{variant} {reg} logged at {alt}[ near {place}]",
        "[{op_short} ]{variant} {reg} at {alt}[ on {day_month}]",
    ),
    deks=(
        "The aircraft database describes {reg} as {type_a}. The record holds {positions} positions[ near {place}].",
        "{positions|cap} positions put {reg} at {alt} on {day_month}[ near {place}].",
    ),
    openers=(
        "The aircraft database describes {reg} as {type_a}.",
        "{reg} appears in the aircraft database as {type_a}.",
        "On {date}, {reg} reported {alt_phrase}[ near {place}].",
    ),
    body=("track", "reading", "/", "limit", "profile", "profile2"),
    tier=1,
    requires=("variant", "reg"),
)
MILITARY_FLAG = Angle(
    "database_flag",
    "The database marks the aircraft as military; say what that label is and what the record shows.",
    headlines=(
        "{type} {reg}, flagged military in the aircraft database[, near {place}]",
        "Military-flagged {type} {reg} at {alt}[ near {place}]",
    ),
    deks=(
        "The aircraft database flags {reg} as military. It reported {alt}[ and {gs}] on {day_month}.",
        "{reg} carries the database's military flag[ and flew as {call}] on {day_month}[ near {place}].",
    ),
    openers=(
        "The aircraft database flags {reg} as military.",
        "{reg} carries the military flag in the aircraft database.",
    ),
    body=("identity", "operator", "reading", "track", "/", "profile", "limit"),
    tier=2,
    requires=("military_flag", "reg", "type"),
)
IDENTITY_ANGLES = (
    OPERATOR_SNAPSHOT,
    TYPE_PROFILE,
    BRIEF_GLIMPSE,
    PLACE_FIRST,
    SNAPSHOT,
)

# Vintage: the database year is the story.
VINTAGE_AGE = Angle(
    "age",
    "The airframe's age from its database build year, as a plain compound adjective.",
    headlines=(
        "{age}-year-old {type} {reg} spotted[ near {place}]",
        "{age}-year-old {type} {reg} flies[ near {place}][ on {day_month}]",
        "A {built} {type}, {reg}, flies[ near {place}]",
    ),
    deks=(
        "The aircraft database lists {reg} as built in {built}. It reported {alt}[ and {gs}] on {day_month}.",
        "Built in {built}, according to the aircraft database, the {type_short} logged {positions} positions"
        "[ near {place}] on {day_month}.",
    ),
    openers=(
        "The aircraft database lists {reg}, {type_a}, as built in {built}.",
        "{reg} was built in {built}, according to the aircraft database.",
    ),
    body=("identity", "reading", "track", "/", "profile", "limit"),
    tier=1,
    requires=("age", "built", "reg", "type"),
)
VINTAGE_YEAR = Angle(
    "year_listed",
    "The listed build year as the hook, then the flight.",
    headlines=(
        "{type} {reg}, built in {built}, flies[ near {place}]",
        "Built in {built}: {type} {reg}[ at {alt}][ near {place}]",
    ),
    deks=(
        "The aircraft database lists {reg} as built in {built}. It reported {alt}[ and {gs}].",
        "According to the aircraft database, {reg} was built in {built}; it reported {alt}[ near {place}].",
    ),
    openers=(
        "The aircraft database lists {built} as the year {reg} was built.",
        "{reg} was built in {built}, according to the aircraft database.",
    ),
    body=("identity", "reading", "track", "/", "age", "profile", "limit"),
    tier=2,
    requires=("built", "reg", "type"),
)
LOW_AND_SLOW = Angle(
    "low_and_slow",
    "An old airframe flying low and slow; lead with the speed and altitude.",
    headlines=(
        "{type} {reg} at {gs} and {alt}[ near {place}]",
        "{gs|cap} at {alt}: {type} {reg}[ near {place}]",
    ),
    deks=(
        "The {type_short} reported {gs} at {alt} on {day_month}. The aircraft database lists it as built in "
        "{built}.",
        "At {m_clock} on {day_month}, {reg} logged {gs} and {alt}[ near {place}]. It was built in {built}, "
        "according to the aircraft database.",
    ),
    openers=(
        "{reg} reported {gs} at {alt} at {m_clock} on {date}.",
        "At {gs} and {alt}, {reg} was flying low and slow on {date}.",
    ),
    body=("identity", "track", "/", "age", "profile", "limit"),
    tier=2,
    requires=("gs", "alt", "reg", "type", "built", "slow"),
)
VINTAGE_ANGLES = (VINTAGE_AGE, TYPE_PROFILE, VINTAGE_YEAR, LOW_AND_SLOW, SNAPSHOT)

# Watchlist entries: who or what Plane Alert DB associates with the aircraft and why it
# lists it (its category, group, description and tags). The headline leads with the
# aircraft, then the name the entry carries, then "spotted"; the dek or a paragraph
# names Plane Alert DB and attributes each detail to it.
LIST_ASSOCIATION = Angle(
    "list_association",
    "Lead with the aircraft and the name its Plane Alert DB entry carries, then why the list carries it.",
    headlines=(
        "{type} {reg}, listed under {wl_assoc}, spotted[ near {place}]",
        "{type_short} {reg}, listed under {wl_assoc}, spotted[ near {place}]",
        "[{type} ]{ident}, listed under {wl_assoc}, spotted[ near {place}]",
        "{wl_assoc}-listed {type_short} {ident} spotted[ near {place}]",
    ),
    deks=(
        'Plane Alert DB files it under "{wl_list}"[, its category for {wl_note}].',
        'Plane Alert DB tags it {wl_tags} and files it under "{wl_list}".',
    ),
    openers=BUILDERS["wl_entry"],
    body=("wl_why", "wl_who", "wl_detail", "identity", "reading", "track", "/", "age", "profile", "limit"),
    tier=0,
    requires=("wl_assoc", "ident"),
)
LIST_PROFILE = Angle(
    "category_type_profile",
    "The type's background first, then the attributed Plane Alert DB entry and its reason.",
    headlines=(
        "{type} {reg}, listed under {wl_assoc}, spotted[ at {alt}]",
        "{wl_assoc}-listed {type_short} {reg} spotted[ near {place}]",
    ),
    deks=(
        "Plane Alert DB lists {reg} under {wl_assoc}. It reported {alt}[ and {gs}] on {day_month}.",
        "The community list Plane Alert DB names {wl_assoc} for {reg}, which reported {alt} on {day_month}.",
    ),
    openers=("{tp1}", "{tp2}"),
    body=("wl_entry", "wl_why", "wl_who", "identity", "reading", "track", "/", "limit", "profile2"),
    tier=1,
    requires=("wl_assoc", "tp1", "reg", "type"),
)
LIST_CATEGORY = Angle(
    "list_category",
    "An entry that names nobody: lead with the aircraft and the Plane Alert DB category it is filed under.",
    headlines=(
        '{type} {reg}, filed under "{wl_list}", spotted[ near {place}]',
        '[{type} ]{ident}, filed under "{wl_list}", spotted[ near {place}]',
        "[{type} ]{ident}, tagged {wl_tags}, spotted[ near {place}]",
    ),
    deks=LIST_ASSOCIATION.deks,
    openers=(
        'Plane Alert DB files {ident} under "{wl_list}"[, its category for {wl_note}].',
        '{ident} has an entry in Plane Alert DB under "{wl_list}".',
    ),
    body=("wl_entry", "wl_detail", "identity", "reading", "track", "/", "age", "profile", "limit"),
    tier=1,
    requires=("wl_list", "ident"),
    # An entry that names someone leads with that name (LIST_ASSOCIATION, LIST_PROFILE).
    excludes=("wl_assoc",),
)
WATCHLIST_ANGLES = (LIST_ASSOCIATION, LIST_PROFILE, LIST_CATEGORY)
# Every watchlist headline form, which the checks require (public_prose.WATCHLIST_HEADLINE).
WATCHLIST_HEADLINES = tuple(dict.fromkeys(form for angle in WATCHLIST_ANGLES for form in angle.headlines))
FIXED_FORM = ("{fixed_headline}",)

# First sightings in these records. The headline is fixed (public_prose.FIXED_HEADLINES): the
# first form whose names the facts give, which its angles carry. The dek adds the aircraft and a
# paragraph scopes the first to these records.
FIXED_HEADLINES = public_prose.FIXED_HEADLINES
FIRST_IN_RECORDS = Angle(
    "first_in_records",
    "The first of its type in these records; a claim about this collection only, with no day count.",
    headlines=("{fixed_headline}",),
    deks=(
        "The aircraft database lists {reg} as {type_a}. It reported {alt}[ and {gs}] on {day_month}.",
        "It logged {positions} positions[ near {place}] on {day_month}.[ Its address sits in the block ICAO "
        "allocates to {alloc_country_the}.]",
        # A record with no reading or track still names the aircraft and the day.
        "The aircraft database lists {reg} as {type_a}. It flew[ near {place}] on {day_month}.",
    ),
    openers=(
        "Until {date}, these records held no {type}.",
        "No {type} appeared in these records before {date}, when {reg} flew[ near {place}].",
        "{reg}, {type_a} by its database record, is the first of its type in these records.",
    ),
    body=("identity", "alloc", "reading", "track", "/", "profile", "profile2", "limit"),
    tier=0,
    requires=("reg", "type"),
)
ALLOCATION = Angle(
    "allocation",
    "The first day these records hold an address from a country's ICAO block.",
    headlines=("{fixed_headline}",),
    deks=(
        "Its transponder address sits in the block ICAO allocates to {alloc_country_the}.[ The database lists it "
        "as {type_a}.]",
        "Its address comes from the block ICAO allocates to {alloc_country_the}. It reported {alt}[ and {gs}] on "
        "{day_month}.",
    ),
    openers=(
        "No address in these records before {date} came from the block ICAO allocates to {alloc_country_the}.",
        "Until {date}, these records held no address from the block ICAO allocates to {alloc_country_the}.",
    ),
    body=("identity", "reading", "track", "/", "profile", "limit"),
    tier=1,
    requires=("alloc_country", "ident"),
)
# A first sighting is the story: every angle leads with it.
FIRST_ANGLES = (FIRST_IN_RECORDS,)


# Outlying readings: one value against the reports around it. A glitch story says what was
# reported, what the database lists and what the type is certified to, never why.
def _outlier_angles(word):
    """Angles for one outlying field; word is the reading's plain name.

    A vertical rate reads as "{rate_direction} rate", so every value carries its direction.
    The spike angles need lone_spike: the value sits well outside its neighbors.
    """
    adjective = word.replace(" ", "-")
    lead = (
        "{reg}[, {type_a} by its database record,] was flying at {alt} when the archive saved a "
        + word
        + " of {value} for it at {peak_clock} on {date}."
        if word == "ground speed"
        else "{reg}[, {type_a} by its database record,] reported "
        + article(word)
        + " of {value} at {peak_clock} "
        "on {date}."
    )
    jump = Angle(
        "jump_and_return",
        "One reading between two others: the values before and after it.",
        headlines=(
            "[{type} ]{ident} logs one {value_adj} report between {nb_before_num} and {nb_after}",
            "[{type} ]{ident} logs a lone {value_adj} " + word + " report among its other readings",
            "[{type_short} ]{ident}'s {value_adj} " + word + " reading stands alone among {nb_count} reports",
            # Rates name a direction for each neighbor, which leaves no room for this one.
            *(
                ()
                if "rate_direction" in word
                else (
                    "A {value_adj} "
                    + adjective
                    + " spike for [{type_short} ]{ident}, between {nb_before} and {nb_after}",
                )
            ),
        ),
        deks=(
            "Every other " + adjective + " report around it read between {nb_min} and {nb_max}, so the spike "
            "stands alone.",
            "The report before it read {nb_before} and the one after it {nb_after}.",
            "Its neighbors read {nb_before} and {nb_after}, {nb_gaps} the outlier.",
        ),
        openers=(
            lead,
            "At {peak_clock} on {date}, one report from {ident} put its " + word + " at {value}.",
            "The report saved for {ident} at {peak_clock} on {date} gives " + article(word) + " of {value}.",
        ),
        body=(
            "neighbors",
            "neighbor_range",
            "neighbor_gaps",
            "/",
            "identity",
            "rated",
            "limit",
        ),
        tier=1,
        requires=("nb_before", "nb_after", "value", "lone_spike", "ident"),
    )
    contrast = Angle(
        "range_contrast",
        "The outlier against the range of every other report in the window.",
        headlines=(
            "[{type} ]{ident} reports {value_round} among readings of {nb_min} to {nb_max}",
            "A {value_adj} outlier in a run of {nb_min_num} to {nb_max} for {ident}",
            "[{type_short} ]{ident} logs one " + word + " of {value_round} in a run of other readings",
        ),
        deks=(
            "The other {nb_count} reports in that stretch read {nb_min} to {nb_max}. One report read "
            + article(word)
            + " of {value}.",
            "Around one "
            + word
            + " report of {value}, every other reading stayed between {nb_min} and {nb_max}.",
        ),
        openers=(
            "{nb_count|cap} reports from {ident} on {date} read between {nb_min} and {nb_max}.",
            "Apart from one report, the record for {ident} on {date} reads between {nb_min} and {nb_max}.",
        ),
        body=(
            "value_line",
            "neighbors",
            "/",
            "identity",
            "rated",
            "limit",
        ),
        tier=1,
        requires=("nb_min", "nb_max", "nb_count", "value", "lone_spike", "ident"),
    )
    explainer = Angle(
        "explainer",
        "A statement headline; the dek gives the readings around one impossible-looking value.",
        headlines=(
            "One " + word + " report of {value_round} for [{type_short} ]{ident} stands alone",
            "[{type_short} ]{ident}'s " + word + " of {value_round} stands apart from its other reports",
        ),
        deks=(
            "The readings on either side of it ran {nb_min} to {nb_max}.",
            "The reports just before and after it read {nb_before} and {nb_after}.",
            "The exact " + word + " figure, logged for {ident} on {day_month}, is {value}.",
        ),
        openers=(
            "The record for {ident} on {date} includes one " + word + " of {value}.",
            "One report from {ident} at {peak_clock} on {date} gives " + article(word) + " of {value}.",
        ),
        body=(
            "neighbors",
            "neighbor_range",
            "/",
            "identity",
            "rated",
            "limit",
        ),
        tier=2,
        requires=("value_round", "lone_spike", "ident"),
    )
    mlat = Angle(
        "mlat_solution",
        "The value comes from a multilateration solution, not a field the aircraft sent.",
        headlines=(
            "Multilateration puts [{type} ]{ident} at {value_round}",
            "[{type_short} ]{ident}'s {value_round} comes from multilateration",
        ),
        deks=(
            "The {value_round} figure comes from a multilateration solution for {ident} on {day_month}.",
            "A multilateration estimate gave {ident} " + article(word) + " of {value} on {day_month}.",
        ),
        openers=(
            "Multilateration produced " + article(word) + " of {value} for {ident} on {date}.",
            "The {value} " + word + " for {ident} on {date} is a multilateration estimate.",
        ),
        body=("neighbors", "/", "identity", "limit"),
        tier=2,
        requires=("mlat", "value", "ident"),
    )
    snapshot = Angle(
        "value_snapshot",
        "The reading itself, with its field, sign, and unit, then what surrounds it.",
        headlines=(
            "[{type} ]{ident} logs a {value_adj} " + word + " report",
            "[{type} ]{ident} reports " + article(word) + " of {value}",
        ),
        deks=(
            "One report at {peak_clock} on {day_month} shows " + article(word) + " of {value}.",
            "The record for {ident} on {day_month} includes " + article(word) + " of {value}.",
        ),
        openers=(
            "At {peak_clock} on {date}, {ident} reported " + article(word) + " of {value}.",
            "The record for {ident} on {date} holds one " + word + " report of {value}.",
        ),
        body=("identity", "neighbors", "neighbor_range", "/", "rated", "reading", "limit"),
        tier=3,
        requires=("value", "ident"),
    )
    return {angle.id: angle for angle in (jump, contrast, explainer, mlat, snapshot)}


SPEED_OUTLIER = _outlier_angles("ground speed")
# Multilateration estimates position and speed, never altitude or vertical rate.
ALTITUDE_OUTLIER = {
    key: angle for key, angle in _outlier_angles("altitude").items() if key != "mlat_solution"
}
RATE_OUTLIER = {
    key: angle for key, angle in _outlier_angles("{rate_direction} rate").items() if key != "mlat_solution"
}

MISMATCH = Angle(
    "contrast",
    "A reading the listed type is not certified for, against the type's cited limit.",
    headlines=(
        "{type} {ident} reports {value_round}, a mismatch with its listed type",
        "[{type_short} ]{ident} logs {value_round}, a reading its listed type does not match",
    ),
    deks=(
        "The aircraft database lists {ident} as {type_a}, which {rated_phrase}. One report read {value}.",
        "The aircraft database lists {ident} as {type_a}, but one report read {value}.",
    ),
    openers=(
        "The aircraft database lists {ident} as {type_a}, yet one report on {date} read {value}.",
        "On {date}, {ident} reported {value}, a figure that does not fit {type_a}.",
    ),
    body=(
        "rated",
        "neighbors",
        "/",
        "mismatch_line",
        "registry_type",
        "registry_agrees",
        "norm",
        "identity",
        "limit",
    ),
    tier=1,
    requires=("value", "ident", "type"),
)
MISMATCH_EXPLAINER = Angle(
    "explainer",
    "A statement headline; the dek gives the reading and the listed type's limit.",
    headlines=(
        "{ident}, listed as {type_a}, reports {value_round}",
        "A {value_adj} report for {ident}, {type_a} by its database record",
    ),
    deks=(
        "The aircraft database lists {ident} as {type_a}, which {rated_phrase}, and one report read {value}.",
        "The database lists {ident} as {type_a}, and one report read {value}.",
    ),
    openers=(
        "One report from {ident} on {date} read {value}.",
        "The record for {ident} on {date} holds a report of {value}.",
    ),
    body=(
        "identity",
        "rated",
        "neighbors",
        "/",
        "mismatch_line",
        "registry_type",
        "registry_agrees",
        "norm",
        "limit",
    ),
    tier=2,
    requires=("value_round", "ident", "type"),
)


def _mismatch_angles(outlier):
    """The mismatch angles plus two outlier angles, which state the reading and the type's limit."""
    return (MISMATCH, MISMATCH_EXPLAINER, outlier["jump_and_return"], outlier["value_snapshot"])


MISMATCH_ANGLES = _mismatch_angles(SPEED_OUTLIER)
ALTITUDE_MISMATCH_ANGLES = _mismatch_angles(ALTITUDE_OUTLIER)

# Transponder codes and statuses.
CODE_MEANING = Angle(
    "code_meaning",
    "The transmitted code and its standard meaning, stated as a definition; the code alone confirms nothing.",
    headlines=(
        "[{op_short} ]{type_short} {ident} transmits squawk {code}[ near {place}]",
        "{type} {ident} sets {code}, the code for {code_meaning}",
        "[{type_short} ]{ident} squawks {code}[ for {duration}]",
        "{ident_named|cap} squawks {code}, the code for {code_meaning}",
    ),
    deks=(
        "Code {code} is the transponder code for {code_meaning}. {code_caveat}",
        "The code lasted {duration} in this record. A code alone does not confirm {code_meaning}.",
        "{ident} sent code {code} at {ep_clock} on {ep_day_month}. {code_caveat}",
        "{ident} showed squawk {code} for {duration}[ near {place}]. {code_caveat}",
    ),
    openers=(
        "{ident_named} broadcast transponder code {code} at {ep_clock} on {ep_date}.",
        "At {ep_clock} on {ep_date}, {ident_named} switched its transponder to {code}.",
        "Code {code}, the transponder code for {code_meaning}, appeared on {ident_named} on {ep_date}.",
        "On {ep_date}, the transponder of {ident_named} was sending code {code} at {ep_clock}.",
        "Squawk {code} came from {ident_named} at {ep_clock} on {ep_date}[, near {place}].",
        "For {duration} on {ep_date}, the transponder of {ident_named} showed {code}.",
    ),
    body=(
        "code_def",
        "code_duration",
        "code_sequence",
        "identity",
        "operator",
        "/",
        "reading",
        "track",
        "limit",
    ),
    tier=1,
    requires=("code", "ident"),
)
CODE_SEQUENCE = Angle(
    "duration_and_sequence",
    "How long the code lasted and what came before or after it.",
    headlines=(
        "[{type} ]{ident} moves through codes {sequence}",
        "[{type_short} ]{ident} squawks {sequence}[ near {place}]",
    ),
    deks=(
        "The transponder showed {sequence}, with the first code on for {sequence_first}.",
        "Codes {sequence} appear in turn in the record for {ident} from {ep_day_month}.",
        "The transponder of {ident} showed {sequence} on {ep_day_month}. {code_caveat}",
        "{ident} moved through codes {sequence}[ near {place}]. {code_caveat}",
    ),
    openers=(
        "The transponder of {ident_named} showed codes {sequence} on {ep_date}.",
        "On {ep_date}, {ident_named} moved through transponder codes {sequence}.",
        "Codes {sequence} appear in turn in the reports from {ident_named} on {ep_date}.",
    ),
    body=("code_def", "identity", "operator", "/", "reading", "track", "limit"),
    tier=1,
    requires=("sequence", "ident"),
)
CODE_OPERATOR = Angle(
    "operator_snapshot",
    "The airline behind the callsign, then the code it transmitted.",
    headlines=(
        "{op_short} {type_short} {call} transmits squawk {code}[ near {place}]",
        "{op} flight {call} squawks {code}",
    ),
    deks=(
        "The {designator} prefix in {call} is assigned to {op}. {code_caveat}",
        "{call} sent code {code} on {ep_day_month}. A code alone does not confirm {code_meaning}.",
    ),
    openers=(
        "{op} flight {call} broadcast transponder code {code} on {ep_date}.",
        "Flying as {call}, {reg} set its transponder to {code} at {ep_clock} on {ep_date}.",
        "Code {code} came from {op} flight {call} on {ep_date}[ near {place}].",
    ),
    body=("code_def", "code_duration", "identity", "operator", "/", "reading", "track", "limit"),
    tier=2,
    requires=("op", "call", "code"),
)
CODE_SNAPSHOT = Angle(
    "code_snapshot",
    "A plain record of the code, the aircraft, and the time.",
    headlines=(
        "[{type} ]{ident} logged with code {code} on {day_month}",
        "Code {code} from [{type_short} ]{ident}[ near {place}]",
    ),
    deks=(
        "The record shows code {code} from {ident} at {ep_clock} on {ep_day_month}[ near {place}].",
        "{ident} transmitted code {code} on {ep_day_month}[ at {alt}][ near {place}].",
        "{ident_named|cap} transmitted code {code}, the code for {code_meaning}, on {ep_day_month}.",
        "The record shows code {code} from {ident} on {ep_day_month}. {code_caveat}",
        "{ident} transmitted code {code}[ at {alt}][ near {place}]. {code_caveat}",
    ),
    openers=(
        "The record for {ident_named} on {ep_date} includes transponder code {code}.",
        "{ident_named} transmitted code {code} on {ep_date}.",
        "At {ep_clock} on {ep_date}, the transponder of {ident_named} showed {code}.",
        "The reports from {ident_named} on {ep_date} include code {code}.",
    ),
    body=("code_def", "identity", "/", "reading", "track", "limit"),
    tier=3,
    requires=("code", "ident"),
)
STATUS_GENERIC = Angle(
    "status_record",
    "A non-routine ADS-B status value, defined where it appears; the status alone confirms nothing.",
    headlines=(
        "[{type} ]{ident} broadcasts a non-routine ADS-B status[ near {place}]",
        "[{type_short} ]{ident} sets a non-routine ADS-B status value on {day_month}",
    ),
    deks=(
        "Its ADS-B status field showed a non-routine value on {day_month}. The status alone does not confirm "
        "an emergency.",
        "The status value from {ident} on {day_month} is defined below. The status alone does not confirm "
        "an emergency.",
    ),
    openers=(
        "The ADS-B status field of {ident_named} changed on {ep_date}.",
        "On {ep_date}, {ident_named} broadcast a non-routine ADS-B status value.",
    ),
    body=("status_def", "identity", "operator", "/", "reading", "track", "limit"),
    tier=2,
    requires=("status", "ident"),
)
STATUS_ANGLES = (
    Angle(
        "status_meaning",
        "The aircraft's own emergency-status field and what the value stands for; it confirms nothing.",
        headlines=(
            "[{type_short} ]{ident} reports {status_label} status[ near {place}]",
            "[{type_short} ]{ident} sets its status field to {status}, which stands for {status_meaning}",
            "{type} {ident} sets its ADS-B status to {status_label}[ near {place}]",
        ),
        deks=(
            "Its ADS-B status field read {status}, which stands for {status_meaning}. {status_caveat}",
            "{ident} broadcast the {status_label} status at {ep_clock} on {ep_day_month}. {status_caveat}",
        ),
        openers=(
            "At {ep_clock} on {ep_date}, {ident_named} set its ADS-B status field to {status}, which stands "
            "for {status_meaning}.",
            "On {ep_date}, the ADS-B status field of {ident_named} read {status}, which stands for "
            "{status_meaning}.",
        ),
        body=("status_def", "identity", "operator", "/", "reading", "track", "limit"),
        tier=1,
        requires=("status", "status_meaning", "ident"),
    ),
    Angle(
        "status_snapshot",
        "The aircraft, its status value, and the time.",
        headlines=(
            "[{type} ]{ident} logged with {status_label} status on {day_month}",
            "{status_label|cap} status from [{type_short} ]{ident}[ near {place}]",
            "[{type_short} ]{ident} logs status {status}, which stands for {status_meaning}",
        ),
        deks=(
            "The record shows the status {status}, which stands for {status_meaning}, on {ep_day_month}. "
            "{status_caveat}",
            "{ident} sent the {status_label} status value on {ep_day_month}[ at {alt}]. {status_caveat}",
        ),
        openers=(
            "The record for {ident_named} on {ep_date} includes the status {status}, which stands for "
            "{status_meaning}.",
            "{ident_named} sent the {status_label} status value on {ep_date}.",
        ),
        body=("status_def", "identity", "/", "reading", "track", "limit"),
        tier=3,
        requires=("status", "status_meaning", "ident"),
    ),
    replace(CODE_OPERATOR, requires=("op", "call", "code", "code_meaning")),
)
SQUAWK_ANGLES = (CODE_MEANING, CODE_SEQUENCE, CODE_OPERATOR, CODE_SNAPSHOT)
SPECIAL_CODE = Angle(
    "code_meaning",
    "A special-use code and its FAA assignment; the assignment for this flight is not known.",
    headlines=(
        "{type} {ident} squawks {code}, a special-use code",
        "[{type_short} ]{ident} transmits {code}, the FAA code for {code_meaning}",
        "Special-use code {code} from [{type_short} ]{ident}[ near {place}]",
    ),
    deks=(
        "The FAA assigns {code} to {code_meaning}. A code alone does not show what this flight was doing.",
        "Code {code} lasted {duration} in this record[ near {place}]. The FAA assigns it to {code_meaning}.",
    ),
    openers=(
        "{ident_named} transmitted transponder code {code} at {ep_clock} on {ep_date}.",
        "On {ep_date}, {ident_named} set its transponder to {code}.",
    ),
    body=("code_def", "code_duration", "identity", "/", "reading", "track", "limit"),
    tier=1,
    requires=("code", "ident"),
)
SPECIAL_CODE_ANGLES = (SPECIAL_CODE, CODE_SEQUENCE, CODE_OPERATOR, CODE_SNAPSHOT)

TCAS_RESPONSE = Angle(
    "advisory_response",
    "The advisory text, the other aircraft, and how the vertical rate changed; automatic, not a near miss.",
    headlines=(
        "TCAS tells {type_a} to {advisory_lower} near {p1_type_a}",
        "{type} {ident} broadcasts a TCAS '{advisory}' advisory near {p1_type_a}",
        "TCAS '{advisory}' for {type_short} {ident} near {p1_type_a}",
    ),
    deks=(
        "The closest recorded spacing was {sep} and {vsep}.[ Its vertical rate changed by {rate_change} "
        "afterwards.]",
        "{type_short} {ident} reported the advisory at {ep_clock} on {ep_day_month}; the other aircraft was "
        "{p1_type_a}.",
    ),
    openers=(
        '{type_a|cap}, by its database record, broadcast a TCAS resolution advisory, "{advisory}", at '
        "{ep_clock} on {ep_date}.",
        'At {ep_clock} on {ep_date}, {ident_named} reported a TCAS resolution advisory: "{advisory}".',
        'A TCAS resolution advisory, "{advisory}", came from {ident_named} on {ep_date}.',
    ),
    body=("tcas_threat", "tcas_spacing", "tcas_rate", "/", "identity", "limit"),
    tier=1,
    requires=("advisory", "type", "p1_type", "ident"),
)
TCAS_GEOMETRY = Angle(
    "geometry",
    "The spacing between the two aircraft around the advisory.",
    headlines=(
        "TCAS advisory as {type_short} and {p1_short} pass {sep} apart",
        "{type_short} {ident} and {p1_short} come within {sep} as TCAS steps in",
    ),
    deks=(
        "The closest recorded spacing was {sep} horizontally and {vsep} vertically. TCAS advisories are automatic.",
        "With {p1_type_a} {sep} and {vsep} apart, {ident} reported a TCAS advisory.",
    ),
    openers=(
        "The closest recorded spacing between {ident_named} and {p1_type_a} was {sep}.",
        "{ident_named|cap} and {p1_type_a} came within {sep} of each other on {ep_date}.",
    ),
    body=("tcas_threat", "tcas_rate", "/", "identity", "limit"),
    tier=1,
    requires=("sep", "vsep", "p1_type", "ident"),
)
TCAS_EXPLAINER = Angle(
    "explainer",
    "Ask what the advisory told the crew to do; the dek answers with the advisory and the spacing.",
    headlines=(
        "What did the TCAS advisory tell {type_short} {ident}[ near {place}]?",
        "What does a TCAS '{advisory}' advisory mean for {type_a}?",
    ),
    deks=(
        'It broadcast "{advisory}" while {p1_type_a} was {sep} and {vsep} apart.',
        'The advisory read "{advisory}"; the closest recorded spacing was {sep}.',
    ),
    openers=(
        "{ident_named} reported a TCAS resolution advisory on {ep_date}.",
        "The record for {ident_named} on {ep_date} includes a TCAS resolution advisory.",
    ),
    body=("tcas_threat", "tcas_spacing", "tcas_rate", "/", "identity", "limit"),
    tier=2,
    requires=("advisory", "ident"),
)
TCAS_SNAPSHOT = Angle(
    "advisory_snapshot",
    "A plain record of the advisory and the aircraft involved.",
    headlines=(
        "[{type} ]{ident} reports a TCAS resolution advisory on {day_month}",
        "TCAS resolution advisory from [{type_short} ]{ident}[ near {place}]",
    ),
    deks=(
        "The record shows a TCAS resolution advisory from {ident} at {ep_clock} on {ep_day_month}.",
        "{ident} broadcast a resolution advisory on {ep_day_month}[ at {alt}][ near {place}].",
    ),
    openers=(
        "{ident_named} broadcast a TCAS resolution advisory at {ep_clock} on {ep_date}.",
        "The record for {ident_named} on {ep_date} holds a TCAS resolution advisory.",
    ),
    body=("identity", "tcas_threat", "tcas_spacing", "/", "reading", "limit"),
    tier=3,
    requires=("ident",),
)
TCAS_ANGLES = (TCAS_RESPONSE, TCAS_GEOMETRY, TCAS_EXPLAINER, TCAS_SNAPSHOT)

# High-altitude balloons: slow drift at extreme altitude, always hedged.
BALLOON_DRIFT = Angle(
    "drift",
    "Altitude band, speed band, and duration; the balloon is a hedged explanation.",
    headlines=(
        "{ident} drifts above {alt_floor} for {hours}, like a high-altitude balloon",
        "{hours|cap} up to {max_alt} at a median {median_gs}: {ident} fits a balloon",
        "{ident} peaks at {max_alt} over {hours}, consistent with a balloon",
    ),
    deks=(
        "Reports between {alt_min} and {alt_max} at {gs_min} to {gs_max} fit a balloon.[ One {spike_adj} report "
        "stands apart from its neighbors.]",
        "For {hours}, {ident} stayed above {alt_floor} while moving about {median_gs} over the ground.",
    ),
    openers=(
        "From {ep_clock} on {ep_day_month} until about {ep_end_clock}[ on {ep_end_day_month}], {ident_named} "
        "reported altitudes between {alt_min} and {alt_max}.",
        "{ident_named} spent {hours} above {alt_floor}, moving {gs_min} to {gs_max} over the ground.",
        "For {hours} from {ep_date}, {ident_named} moved slowly over the ground while flying above {alt_floor}.",
    ),
    body=("balloon_range", "/", "balloon_hyp", "balloon_spike", "track", "limit"),
    tier=1,
    requires=("ident", "hours", "max_alt"),
)
BALLOON_GLITCH = Angle(
    "glitch_folded",
    "A lone ground-speed spike in an otherwise slow, high track; the spike is a data error, the drift the story.",
    headlines=(
        "One {spike_adj} report hides a slow drift up to {max_alt} for {ident}",
        "Behind a {spike_adj} glitch, {ident} drifts as high as {max_alt} like a balloon",
    ),
    deks=(
        "One report read {spike}, but the rest show about {median_gs} at up to {max_alt} for {hours}.",
        "Apart from one {spike_adj} report, {ident} moved slowly at extreme altitude for {hours}.",
    ),
    openers=(
        "One report from {ident_named} reads {spike}, far from every reading around it.",
        "The record for {ident_named} holds a single report of {spike} amid hours of slow drift.",
    ),
    body=("balloon_range", "/", "balloon_hyp", "track", "limit"),
    tier=1,
    requires=("spike", "max_alt", "hours", "ident"),
)
BALLOON_CALLSIGN = Angle(
    "callsign_lead",
    "The callsign people follow, the altitude, and the hours aloft.",
    headlines=(
        "{call} spends {hours} near {max_alt}[ near {place}]",
        "{call} stays aloft for {hours} at up to {max_alt}",
    ),
    deks=(
        "{call} reported up to {max_alt} at about {median_gs} over the ground, consistent with a balloon.",
        "The record for {call} runs {hours}, with altitudes up to {max_alt}[ near {place}].",
    ),
    openers=(
        "{call} reported altitudes up to {max_alt} on {ep_date}.",
        "Over {hours} from {ep_date}, {call} reported altitudes up to {max_alt}.",
    ),
    body=("balloon_range", "/", "balloon_hyp", "balloon_spike", "track", "limit"),
    tier=2,
    requires=("call", "hours", "max_alt"),
)
BALLOON_ANGLES = (BALLOON_DRIFT, BALLOON_GLITCH, BALLOON_CALLSIGN, SNAPSHOT)

# Two or more aircraft together.
FORMATION_PAIR = Angle(
    "pair_geometry",
    "How close the two aircraft stayed and for how long; formation flight is a hedged explanation.",
    headlines=(
        "A pair of {type_short|same}s fly close together for {duration}[ near {place}]",
        "{type_short} {ident} and {partner} come within {sep} of each other[ near {place}]",
        "{type} {ident} flies close to {partner} for {duration}",
    ),
    deks=(
        "For {duration}, the two aircraft flew close together, at their closest {sep} apart horizontally and "
        "{vsep} vertically.",
        "The closest recorded spacing was {sep} and {vsep}. Formation flight is one possible explanation.",
    ),
    openers=(
        "For {duration} on {date}, {ident_named} and {partner} flew close together, at one point {sep} apart.",
        "{ident_named} and {partner}[, {p1_ident},] held formation-like spacing on {date}.",
    ),
    body=(
        "pair",
        "pair_spacing",
        "operator",
        "/",
        "formation_hyp",
        "identity",
        "track",
        "airport",
        "country",
        "limit",
    ),
    tier=1,
    requires=("sep", "p1_type", "ident", "duration"),
)
FORMATION_TYPES = Angle(
    "types_together",
    "The types flying together, with the spacing as support.",
    headlines=(
        "{type_short} and {p1_short|mixed} fly close together[ near {place}]",
        "{type} {ident} pairs up with {p1_type_a|mixed}[ near {place}]",
        "{ident} and {p1_ident}, a pair of {type_short|same}s, fly close together[ near {place}]",
    ),
    deks=(
        "{ident} and {p1_ident} flew close together for {duration} on {day_month}, coming within {sep}.",
        "{ident} held close spacing with {partner} for {duration}[ near {place}].",
    ),
    openers=(
        "{ident_named} and {p1_ident} flew close together on {date}.",
        "On {date}, {ident_named} flew alongside {partner}.",
    ),
    body=(
        "pair_spacing",
        "pair",
        "operator",
        "/",
        "formation_hyp",
        "identity",
        "track",
        "airport",
        "country",
        "limit",
    ),
    tier=1,
    requires=("p1_type", "type", "ident"),
)
PAIR_SNAPSHOT = Angle(
    "pair_snapshot",
    "A plain record of the aircraft involved and the time.",
    headlines=(
        "{aircraft_total|cap} aircraft fly close together[ near {place}] on {day_month}",
        "[{type} ]{ident} and another aircraft fly close together on {day_month}",
    ),
    deks=(
        "The record from {ep_day_month} shows {ident} and another aircraft as close as {sep} to each other.",
        "{ident} and another aircraft flew close together for {duration}[ near {place}].",
    ),
    openers=(
        "On {ep_date}, {ident_named} and another aircraft flew close together.",
        "The record for {ident_named} on {ep_date} includes a second aircraft close by.",
    ),
    body=("pair", "pair_spacing", "/", "identity", "track", "airport", "limit"),
    tier=3,
    requires=("ident",),
)
FORMATION_ANGLES = (FORMATION_PAIR, FORMATION_TYPES, PAIR_SNAPSHOT)

SURVEY_LINES = Angle(
    "lines_and_spacing",
    "The number of straight legs, their length, and spacing; survey work is a hedged explanation.",
    headlines=(
        "{type} {ident} flies {legs} parallel legs {leg_spacing} apart[ near {place}]",
        "[{type} ]{ident} works a grid of {legs} legs[ near {place}]",
        "{legs|cap} straight legs, {leg_spacing} apart, from {type_short} {ident}",
    ),
    deks=(
        "The legs averaged {leg_length} long and {leg_spacing} apart, a pattern typical of survey flying.",
        "Over {duration}, {ident} flew {legs} straight legs about {leg_spacing} apart[ near {place}].",
    ),
    openers=(
        "Over {duration} on {date}, {ident_named} flew {legs} straight, parallel legs.",
        "{ident_named} spent {duration} on {date} flying back and forth on parallel legs about {leg_spacing} apart.",
    ),
    body=(
        "survey_legs",
        "identity",
        "reading",
        "/",
        "survey_hyp",
        "track",
        "country",
        "age",
        "limit",
    ),
    tier=1,
    requires=("legs", "leg_spacing", "ident"),
)
SURVEY_PLACE = Angle(
    "place",
    "Where the grid lay and how long it took.",
    headlines=(
        "{type} {ident} flies a grid of legs near {place}",
        "Parallel legs near {place} from {type_short} {ident}",
    ),
    deks=(
        "{ident} flew {legs} parallel legs near {place} over {duration}.",
        "Near {place}, {ident} spent {duration} on straight, evenly spaced legs.",
    ),
    openers=(
        "Near {place}, {ident_named} flew {legs} parallel legs on {date}.",
        "The track of {ident_named} near {place} on {date} is a grid of straight legs.",
    ),
    body=("survey_legs", "identity", "reading", "/", "survey_hyp", "track", "country", "limit"),
    tier=2,
    requires=("place", "legs", "ident"),
)
SURVEY_ANGLES = (SURVEY_LINES, SURVEY_PLACE, SNAPSHOT)

HOLDING_STACK = Angle(
    "count_and_duration",
    "How many aircraft circled together and for how long; no holding instruction is implied.",
    headlines=(
        "{aircraft_total|cap} aircraft circle near {place} for up to {duration}",
        "A group of {aircraft_total} aircraft circles near {place}",
        "{aircraft_total|cap} aircraft circle together for {duration}[ near {place}]",
    ),
    deks=(
        "{type_a|cap}, {ident}, was one of {aircraft_total} aircraft circling in the same area on {day_month}.",
        "For up to {duration}, {aircraft_total} aircraft circled near one another[ near {place}].",
    ),
    openers=(
        "On {date}, {aircraft_total} aircraft circled near one another[ near {place}] for up to {duration}.",
        "{ident_named} was one of {aircraft_total} aircraft circling together on {date}.",
    ),
    body=("stack", "identity", "operator", "/", "reading", "track", "country", "limit"),
    tier=1,
    requires=("aircraft_total", "duration", "ident"),
)
HOLDING_PLACE = Angle(
    "place",
    "Where the aircraft circled together.",
    headlines=(
        "Aircraft circle together near {place} on {day_month}",
        "Near {place}, {aircraft_total} aircraft circle at once",
    ),
    deks=(
        "{aircraft_total|cap} aircraft circled near {place} on {day_month}, including {ident}.",
        "Near {place}, {ident} circled with {others} other aircraft for {duration}.",
    ),
    openers=(
        "Near {place} on {date}, {aircraft_total} aircraft circled at the same time.",
        "{ident_named} circled near {place} on {date} alongside {others} other aircraft.",
    ),
    body=("stack", "identity", "/", "reading", "limit"),
    tier=2,
    requires=("place", "aircraft_total", "ident"),
)
STACK_ANGLES = (HOLDING_STACK, HOLDING_PLACE, PAIR_SNAPSHOT)

GNSS_WINDOW = Angle(
    "onset_window",
    "Several aircraft reporting lower navigation integrity close together in time; the cause is unknown.",
    headlines=(
        "{affected|cap} aircraft report navigation-integrity drops within {duration}[ near {place}]",
        "Navigation integrity drops on {affected} aircraft[ near {place}] on {day_month}",
    ),
    deks=(
        "{type_a|cap}, {ident}, was one of {affected} aircraft whose navigation integrity fell on {day_month}.",
        "The drops came within {duration} of each other[ near {place}] on {day_month}.",
    ),
    openers=(
        "Within {duration} on {date}, {affected} aircraft reported lower navigation integrity.",
        "{ident_named} was one of {affected} aircraft whose navigation integrity dropped on {date}.",
    ),
    body=("gnss_count", "identity", "/", "reading", "track", "limit"),
    tier=1,
    requires=("affected", "duration", "ident"),
)
GNSS_COUNT = Angle(
    "count",
    "How many aircraft showed the integrity drop.",
    headlines=(
        "{affected|cap} aircraft lose navigation integrity at once[ near {place}]",
        "Navigation-integrity drop shows up on {affected} aircraft",
    ),
    deks=(
        "{affected|cap} aircraft, including {ident}, reported lower navigation integrity on {day_month}.",
        "The record links {affected} aircraft that reported integrity drops on {day_month}[ near {place}].",
    ),
    openers=(
        "{affected|cap} aircraft reported lower navigation integrity on {date}.",
        "On {date}, the record shows integrity drops from {affected} aircraft.",
    ),
    body=("gnss_count", "identity", "/", "reading", "limit"),
    tier=2,
    requires=("affected", "ident"),
)
GNSS_SINGLE = Angle(
    "integrity_drop",
    "One aircraft's navigation integrity dropped; the cause is unknown.",
    headlines=(
        "[{type} ]{ident} reports a navigation-integrity drop[ near {place}]",
        "Navigation integrity falls for [{type_short} ]{ident} on {day_month}",
    ),
    deks=(
        "{ident} reported lower navigation integrity on {day_month}[ at {alt}][ near {place}].",
        "The integrity figures from {ident} fell during the record on {day_month}[ near {place}].",
    ),
    openers=(
        "The navigation-integrity figures from {ident_named} fell on {date}.",
        "On {date}, {ident_named} reported lower navigation integrity.",
    ),
    body=("identity", "reading", "/", "track", "limit"),
    tier=2,
    requires=("ident",),
)
GNSS_ANGLES = (GNSS_WINDOW, GNSS_COUNT, GNSS_SINGLE)

THERMAL = Angle(
    "climb_in_circles",
    "Altitude gained while circling; soaring is a hedged explanation.",
    headlines=(
        "{group_or_type|cap} {ident} climbs {gain} in circles[ near {place}]",
        "[{type_short} ]{ident} circles up {gain}[ near {place}]",
    ),
    deks=(
        "{ident} gained {gain} while circling on {day_month}, consistent with soaring in rising air.",
        "Over {duration}, {ident} climbed {gain} in circles[ near {place}].",
    ),
    openers=(
        "{ident_named} gained {gain} while circling on {date}.",
        "Over {duration} on {date}, {ident_named} climbed {gain} in a series of circles.",
    ),
    body=("identity", "reading", "track", "/", "soaring_hyp", "country", "age", "limit"),
    tier=1,
    requires=("gain", "ident"),
)
THERMAL_ANGLES = (THERMAL, TYPE_PROFILE, SNAPSHOT)

# Flight shapes from track geometry.
SHAPE_DURATION = Angle(
    "shape_and_duration",
    "The shape the track drew and how long it lasted; geometry says nothing about purpose.",
    headlines=(
        "[{type} ]{ident} {shape}[ for {shape_duration}][ near {place}]",
        "For {shape_duration}, [{type_short} ]{ident} {shape}",
        "[{type} ]{ident} {shape} on {day_month}[ near {place}]",
    ),
    deks=(
        "Over {shape_duration} on {day_month}, {ident} {shape_past}[ near {place}].",
        "The track from {ident} on {day_month} shows it {shape_past_ing}[ near {place}].",
        "On {day_month}, {ident} {shape_past}[ near {place}][ at {alt}].",
        "{positions|cap} positions from {ident} on {day_month} trace the maneuver[ near {place}][, at {alt}].",
    ),
    openers=(
        "Over {shape_duration} on {date}, {ident_named} {shape_past}.",
        "The track of {ident_named} on {date} shows it {shape_past_ing}[ near {place}].",
        "At {ep_clock} on {ep_date}, {ident_named} {shape_past}[ near {place}].",
    ),
    body=("shape", "identity", "reading", "/", "track", "airport", "country", "age", "limit"),
    tier=1,
    requires=("shape", "ident"),
)
SHAPE_PLACE = Angle(
    "place",
    "Where the shape was flown.",
    headlines=(
        "[{type} ]{ident} {shape} near {place}",
        "Near {place}, [{type_short} ]{ident} {shape}",
    ),
    deks=(
        "Near {place}, {ident} {shape_past} on {day_month}[ at {alt}].",
        "The track near {place} shows {ident} {shape_past_ing} on {day_month}.",
        "{positions|cap} positions near {place} trace the maneuver by {ident} on {day_month}.",
    ),
    openers=(
        "Near {place} on {date}, {ident_named} {shape_past}.",
        "{ident_named} {shape_past} near {place} on {date}.",
    ),
    body=("shape", "identity", "reading", "/", "track", "airport", "country", "age", "limit"),
    tier=2,
    requires=("shape", "place", "ident"),
)
SHAPE_PROFILE = Angle(
    "type_profile",
    "A sourced fact about the type, then the shape it flew.",
    headlines=(
        "{type} {ident} {shape}[ near {place}]",
        "{type} {ident} {shape} on {day_month}",
    ),
    deks=(
        "Over {shape_duration} on {day_month}, the {type_short} {shape_past}[ near {place}].",
        "The {type_short} {shape_past} on {day_month}[ near {place}][ at {alt}].",
    ),
    openers=("{tp1}", "{tp2}"),
    body=("shape", "identity", "reading", "/", "track", "airport", "profile2", "limit"),
    tier=2,
    requires=("tp1", "shape", "type", "ident"),
)
SHAPE_ANGLES = (SHAPE_DURATION, SHAPE_PLACE, SHAPE_PROFILE, SNAPSHOT)

# Vertical performance.
RATE_CHANGE = Angle(
    "rate_and_change",
    "The altitude change and the peak rate; never an emergency determination.",
    headlines=(
        "{type} {ident} {rate_verb} {change} at up to {peak_rate}[ near {place}]",
        "[{type_short} ]{ident} {rate_verb} from {start_alt} to {end_alt}[ in {duration}]",
        "A {change_adj} {rate_noun} for [{type_short} ]{ident} at up to {peak_rate}",
    ),
    deks=(
        "The {rate_noun} peaked at {peak_rate}[ at {peak_clock}] on {day_month}. These reports are not an "
        "emergency determination.",
        "Pressure altitude went from {start_alt} to {end_alt} over {duration} on {day_month}.",
    ),
    openers=(
        "{ident_named} {rate_verb_past} from {start_alt} to {end_alt} in {duration} on {date}.",
        "On {date}, {ident_named} {rate_verb_past} {change}, with a peak rate of {peak_rate}.",
    ),
    body=("peak_rate", "descent_profile", "identity", "/", "operator", "track", "country", "age", "limit"),
    tier=1,
    requires=("change", "peak_rate", "rate_verb", "ident"),
)
RATE_PROFILE = Angle(
    "profile",
    "A descent that matches the profile flown after cabin-pressure problems; the cause stays unknown.",
    headlines=(
        "[{type_short} ]{ident} descends from {start_alt} and levels near {end_alt}",
        "{type} {ident} drops {change} and levels off[ near {place}]",
    ),
    deks=(
        "The descent began at {start_alt} and leveled off at {end_alt}, a drop of {change}.",
        "{ident} went from {start_alt} to {end_alt} on {day_month}, then leveled off.",
    ),
    openers=(
        "{ident_named} descended from {start_alt} and leveled off near {end_alt} on {date}.",
        "On {date}, {ident_named} came down {change} before leveling off.",
    ),
    body=("peak_rate", "descent_profile", "identity", "/", "limit", "track"),
    tier=1,
    requires=("tag_emergency_descent_profile", "start_alt", "end_alt", "ident"),
)
RATE_PLACE = Angle(
    "place",
    "Where the climb or descent happened.",
    headlines=(
        "[{type} ]{ident} {rate_verb} {change} near {place}",
        "Near {place}, [{type_short} ]{ident} {rate_verb} at up to {peak_rate}",
    ),
    deks=(
        "Near {place}, {ident} {rate_verb_past} {change} on {day_month}[, at up to {peak_rate}].",
        "The {rate_noun} near {place} peaked at {peak_rate} on {day_month}.",
    ),
    openers=(
        "Near {place} on {date}, {ident_named} {rate_verb_past} {change}.",
        "{ident_named} {rate_verb_past} {change} near {place} on {date}.",
    ),
    body=("peak_rate", "descent_profile", "identity", "/", "track", "limit"),
    tier=2,
    requires=("place", "change", "rate_verb", "ident"),
)
RATE_ANGLES = (RATE_CHANGE, RATE_PROFILE, RATE_PLACE, SNAPSHOT)

# Season records: the fastest ground speed for the type since the saved reports for it began
# this season. Every angle states that record and scopes it to these records in a paragraph,
# never in the headline or dek; none says "this season" unless the reports cover the whole
# season. The one hedge, record_hedge, replaces the evidence card's longer caveats.
SEASON_BEST = Angle(
    "season_best",
    "The fastest ground speed for the type in these records since the comparison began; ground speed includes "
    "wind.",
    headlines=(
        "{value_round|cap} for {type_short} {ident}, the fastest since {season_start}",
        "{type} {ident} tops every {type_short} ground speed since {season_start}",
        "Fastest {type_short} since {season_start}: {ident} at {value_round}",
    ),
    deks=(
        "No {type_short} since {season_start} went faster over the ground.[ The previous best was {previous_gs}.]",
        "{ident} reported {value} over the ground, the fastest for the type since {season_start}.",
    ),
    openers=(
        "{ident_named} reported a ground speed of {value} on {date}, the fastest for the {type_short} in these "
        "records since {season_start}.",
        "Since {season_start}, no {type_short} in these records went faster over the ground than {ident} on "
        "{date}.",
    ),
    body=("season_previous", "season", "record_hedge", "identity", "/", "norm", "operator", "reading"),
    tier=1,
    requires=("value", "type", "ident", "season_start"),
)
SEASON_OPERATOR = Angle(
    "operator_record",
    "The airline behind the callsign, with the type's fastest ground speed in these records since the "
    "comparison began.",
    headlines=(
        "{op_short} {type_short} {ident} sets the fastest {type_short} speed since {season_start}",
        "{op} flight {call} is the fastest {type_short} since {season_start}",
    ),
    deks=(
        "Flying as {call}, {reg} reported {value} over the ground, the fastest for the type since "
        "{season_start}.",
        "The {designator} prefix in {call} is assigned to {op}. No {type_short} since {season_start} was faster.",
    ),
    openers=(
        "{op} flight {call} reported a ground speed of {value} on {date}, the fastest for the {type_short} in "
        "these records since {season_start}.",
        "Flying as {call} on {date}, {reg} went faster over the ground than any {type_short} in these records "
        "since {season_start}.",
    ),
    body=("season_previous", "season", "record_hedge", "identity", "operator", "/", "norm", "reading"),
    tier=1,
    requires=("op", "call", "reg", "value", "type", "season_start"),
)
SEASON_VALUE = Angle(
    "record_value",
    "The reading itself, then the comparison it tops in these records.",
    headlines=(
        "[{type} ]{ident} logs {value_round}, a {type_short} best since {season_start}",
        "[{type_short} ]{ident} reports {value_round}, the fastest {type_short} since {season_start}",
    ),
    deks=(
        "The record for {ident} on {day_month} shows {value} over the ground, faster than any {type_short} since "
        "{season_start}.",
        "No {type_short} since {season_start} matched the {value} over the ground that {ident} reported.",
    ),
    openers=(
        "The record for {ident_named} on {date} holds a ground speed of {value}, a best for the {type_short} in "
        "these records since {season_start}.",
        "On {date}, {ident_named} reported {value} over the ground, more than any {type_short} in these records "
        "since {season_start}.",
    ),
    body=("season_previous", "season", "record_hedge", "identity", "/", "norm", "reading", "track"),
    tier=2,
    requires=("value", "type", "ident", "season_start"),
)
SEASON_ANGLES = (SEASON_BEST, SEASON_OPERATOR, SEASON_VALUE)

# Readings past a certified limit: the sustained figure against the type's certificate. The
# airframe is the point, so the headline names it. Every story states the figure and its sheet
# once (rated_source) and one hedge (rated_hedge_*), never a cause, a judgement, or the wind
# service, which the evidence card credits.
CEILING_FIGURE = Angle(
    "certificate_figure",
    "The reports above the type's certified ceiling against the figure, cited to its certificate.",
    headlines=(
        "{type} {reg} reported above its certified ceiling",
        "{type} {reg} reported {alt}, above the type's {lim_limit_adj} ceiling",
        "{type} {reg} reported {lim_reading} or more, over its {lim_limit_adj} ceiling",
    ),
    deks=(
        "{lim_reports|cap} reports over {lim_time} put its pressure altitude at {lim_reading} or more, against a "
        "certified maximum of {lim_limit}.",
        "{lim_reports|cap} reports over {lim_time} sat at least {lim_margin} above the {lim_name} on its "
        "certificate.",
    ),
    openers=(
        "On {date}, {lim_reports} reports from {reg} over {lim_time} gave a pressure altitude of {lim_reading} "
        "or more[ near {place}].",
        "{reg} sent {lim_reports} reports of {lim_reading} or higher over {lim_time} on {date}[ near {place}].",
    ),
    body=(
        "rated_source",
        "ceiling_margin",
        "rated_hedge_altitude",
        "/",
        "identity",
        "registry_agrees",
        "operator",
        "track",
    ),
    tier=0,
    requires=("lim_reading", "lim_limit", "lim_reports", "lim_time", "reg", "type"),
)
CEILING_PROFILE = Angle(
    "type_profile",
    "A sourced fact about the type, then the reports above its certified ceiling.",
    headlines=(
        "{type} {reg} reported {alt}, past its certified ceiling",
        "{type} {reg} reports above the type's {lim_limit_adj} ceiling",
    ),
    deks=(
        "{reg} sent {lim_reports} reports of {lim_reading} or more over {lim_time}[ near {place}], above the "
        "type's certified {lim_limit}.",
        "Its type certificate gives a {lim_name} of {lim_limit}. {lim_reports|cap} reports from {reg} sat at "
        "least {lim_margin} above it.",
    ),
    openers=("{tp1}", "{tp2}"),
    body=(
        "ceiling_reading",
        "rated_source",
        "ceiling_margin",
        "rated_hedge_altitude",
        "/",
        "identity",
        "registry_agrees",
        "track",
    ),
    tier=1,
    requires=("tp1", "lim_reading", "lim_limit", "lim_reports", "reg", "type"),
)
CEILING_SNAPSHOT = Angle(
    "snapshot",
    "The aircraft, where it flew, and the reports over the limit, then the certified figure.",
    headlines=(
        "{type} {reg} reported above its certified ceiling[ near {place}]",
        "{type} {reg} reports {alt}[ near {place}], over its certified ceiling",
    ),
    deks=(
        "On {day_month}, {lim_reports} of its reports gave a pressure altitude of {lim_reading} or more"
        "[ near {place}].",
        "{reg} sent {lim_reports} reports of {lim_reading} or higher on {day_month}, over its certified "
        "ceiling[ near {place}].",
    ),
    openers=(
        "{reg} reported a pressure altitude of {lim_reading} or more in {lim_reports} reports on {date}"
        "[ near {place}].",
        "On {date}, {lim_reports} reports from {reg} over {lim_time} put it at {lim_reading} or higher.",
    ),
    body=(
        "identity",
        "rated_source",
        "ceiling_margin",
        "rated_hedge_altitude",
        "/",
        "registry_agrees",
        "operator",
        "track",
    ),
    tier=2,
    requires=("lim_reading", "lim_reports", "reg", "type"),
)
CEILING_ANGLES = (CEILING_FIGURE, CEILING_PROFILE, CEILING_SNAPSHOT)

SPEED_FIGURE = Angle(
    "certificate_figure",
    "The airspeed behind the ground speed against the type's certified limit, cited to its certificate.",
    headlines=(
        "{type} {reg} {est_word} past its {lim_name}",
        "{type} {reg} at an estimated {lim_mach_estimate}, past its certified limit",
        # A long limit name ("maximum operating Mach number") may leave no room for the margin.
        "{type} {reg} {est_word}[ at least {lim_margin}] past its {lim_name}",
    ),
    deks=(
        "{lim_reports|cap} reports over {lim_time} put its {airspeed_kind} at least {lim_margin} past the "
        "certified limit.",
        "In the report closest to the limit, its {airspeed_kind} was {lim_airspeed}, or {lim_compare}.",
    ),
    openers=(
        "On {date}, {lim_reports} reports from {reg} over {lim_time} gave a ground speed of {lim_reading} or "
        "more[ near {place}].",
        "On {date}, {lim_reports} reports from {reg} in {lim_time} put its {airspeed_kind} past its "
        "certified {lim_name}.",
    ),
    body=(
        "rated_source",
        "speed_estimate",
        "speed_report",
        "speed_margin",
        "rated_hedge_speed",
        "/",
        "identity",
        "registry_agrees",
        "track",
    ),
    tier=0,
    requires=("airspeed_kind", "lim_compare", "lim_limit", "lim_margin", "lim_reports", "reg", "type"),
)
SPEED_PROFILE = Angle(
    "type_profile",
    "A sourced fact about the type, then the airspeed behind the ground speed against its certified limit.",
    headlines=(
        "{type} {reg} at an estimated {lim_mach_estimate}, past the type's limit",
        "{type} {reg} {est_word} past its certified {lim_name}",
    ),
    deks=(
        "{lim_reports|cap} reports from {reg} put its {airspeed_kind} at least {lim_margin} past the type's "
        "certified limit.",
        "Its type certificate gives a {lim_name} of {lim_limit}. {lim_reports|cap} reports from {reg} ran at "
        "least {lim_margin} past it.",
    ),
    openers=("{tp1}", "{tp2}"),
    body=(
        "identity",
        "rated_source",
        "speed_estimate",
        "speed_report",
        "speed_margin",
        "rated_hedge_speed",
        "/",
        "registry_agrees",
        "track",
    ),
    tier=1,
    requires=("tp1", "airspeed_kind", "lim_compare", "lim_limit", "lim_margin", "lim_reports", "reg", "type"),
)
SPEED_SNAPSHOT = Angle(
    "snapshot",
    "The aircraft, its ground speed, and where it flew, then the airspeed and the certified figure.",
    headlines=(
        "{type} {reg} {est_word} over its certified {lim_name}[ near {place}]",
        "{type} {reg} {est_word} past its certified limit[ near {place}]",
    ),
    deks=(
        "On {day_month}, {lim_reports} of its reports gave a ground speed of {lim_reading} or more[ near "
        "{place}].",
        "{reg} sent {lim_reports} reports that put its {airspeed_kind} past its certified {lim_name}[ on "
        "{day_month}].",
    ),
    openers=(
        "{reg} reported a ground speed of {lim_reading} or more in {lim_reports} reports on {date}"
        "[ near {place}].",
        "On {date}, {lim_reports} reports from {reg} over {lim_time} gave a ground speed of {lim_reading} or "
        "more.",
    ),
    body=(
        "identity",
        "rated_source",
        "speed_estimate",
        "speed_report",
        "speed_margin",
        "rated_hedge_speed",
        "/",
        "registry_agrees",
        "track",
    ),
    tier=2,
    requires=("lim_reading", "airspeed_kind", "lim_compare", "lim_reports", "reg", "type"),
)
SPEED_LIMIT_ANGLES = (SPEED_FIGURE, SPEED_PROFILE, SPEED_SNAPSHOT)

PERFORMANCE_VALUE = Angle(
    "value_snapshot",
    "The reading itself, its field, and its unit, with the record around it.",
    headlines=(
        "[{type} ]{ident} reports {value_phrase}[ near {place}]",
        "[{type_short} ]{ident} logs {value_phrase} on {day_month}",
        "{ident_named|cap} reports {value_phrase} on {day_month}",
    ),
    deks=(
        "The record shows {value_phrase} from {ident} at {clock} on {day_month}.",
        "On {day_month}, {ident} reported {value_phrase}[ at {alt}][ near {place}].",
        "{ident} reported a true airspeed of {tas} and a ground speed of {gs} on {day_month}[ near {place}].",
        "{ident}, {type_a} by its database record, was logged at {alt} on {day_month}[ near {place}].",
    ),
    openers=(
        "At {clock} on {date}, {ident_named} reported {value_phrase}.",
        "The record for {ident_named} on {date} includes {value_phrase}.",
        "{ident_named|cap} was flying at {alt} when it reported {value_phrase} on {date}.",
        "{ident_named|cap} was flying at {alt}[ near {place}] on {date}.",
    ),
    body=("identity", "reading", "airspeed", "/", "rated", "track", "limit"),
    tier=1,
    requires=("value_phrase", "ident"),
)
PERFORMANCE_ANGLES = (PERFORMANCE_VALUE, OPERATOR_SNAPSHOT, PLACE_FIRST, SNAPSHOT)

# Identity and emitter checks.
IDENTITY_CONTRAST = Angle(
    "contrast",
    "The identifiers that disagree, side by side; the cause is unknown.",
    headlines=(
        "Database registration {reg} conflicts with address {hex}",
        "Address {hex} maps to {implied}, while the database lists {reg}",
    ),
    deks=(
        "The registration {reg} and the transponder address {hex} do not match in the identity checks.",
        "A database error, a re-registration, or an avionics setting could explain the mismatch for {reg}.",
    ),
    openers=(
        "The record from {date} carries address {hex}; the aircraft database labels it {reg}.",
        "Address {hex} was recorded on {date}. The aircraft database associates it with {reg}.",
    ),
    body=("implied", "callsign_conflict", "country_conflict", "identity", "/", "reading", "limit"),
    tier=1,
    requires=("reg", "hex", "implied"),
)
EMITTER_CONTRAST = Angle(
    "contrast",
    "The emitter category the aircraft broadcast against its database type.",
    headlines=(
        "{type} {ident} broadcasts emitter category {emitter}",
        "[{type_short} ]{ident} broadcasts the {emitter_label} emitter category",
    ),
    deks=(
        "{ident} broadcast emitter category {emitter}, the {emitter_label} category, on {day_month}.",
        "The database lists {ident} as {type_a}, yet its emitter category reads {emitter}.",
    ),
    openers=(
        "{ident_named} broadcast emitter category {emitter} on {date}.",
        "On {date}, the emitter category from {ident_named} read {emitter}.",
    ),
    body=("emitter", "identity", "/", "reading", "limit"),
    tier=1,
    requires=("emitter", "ident"),
)
# Only the fields a check flagged are named, so ordinary readings never pass for a contradiction.
DATA_EXPLAINER = Angle(
    "explainer",
    "A statement headline; name only the flagged fields and point to the data.",
    headlines=(
        "The record for {type_short} {ident} disagrees with itself",
        "Conflicting fields in the data for {ident} on {day_month}",
    ),
    deks=(
        "The values reported for {ident} do not agree, which points to a data or database problem.",
        "Fields in the record for {ident} contradict each other on {day_month}[ near {place}].",
    ),
    openers=(
        "The record for {ident_named} on {date} holds fields that do not agree.",
        "The {date} record for {ident_named} contains fields that disagree.",
        "Conflicting fields accompany the {date} record for {ident_named}.",
    ),
    body=(
        "implied",
        "callsign_conflict",
        "address_agrees",
        "country_conflict",
        "emitter",
        "gap",
        "jump",
        "identity",
        "/",
        "reading",
        "limit",
    ),
    tier=2,
    requires=("ident", "data_conflict"),
)
JUMP_CONTRAST = Angle(
    "contrast",
    "A position jump between two reports, measured as distance and time, not as flight.",
    headlines=(
        "[{type_short} ]{ident}'s position jumps {jump_nm} in {jump_time}",
        "[{type} ]{ident} reports a {jump_nm} position jump",
    ),
    deks=(
        "Its reported position moved {jump_nm} between reports {jump_time} apart on {day_month}.",
        "A jump of {jump_nm} in {jump_time} would mean {jump_speed}, so it is a data finding.",
    ),
    openers=(
        "Between reports {jump_time} apart on {date}, the position of {ident_named} moved {jump_nm}.",
        "The position reported by {ident_named} on {date} jumped {jump_nm}.",
    ),
    body=("jump", "identity", "/", "reading", "limit"),
    tier=1,
    requires=("jump_nm", "jump_time", "ident"),
)
GAP_CONTRAST = Angle(
    "contrast",
    "Pressure and geometric altitude far apart in one report.",
    headlines=(
        "[{type} ]{ident} reports altitudes {value_round} apart",
        "Pressure and geometric altitude disagree for [{type_short} ]{ident}",
    ),
    deks=(
        "Pressure altitude read {alt_baro} while geometric altitude read {alt_geom}.",
        "The altitude fields from {ident} differed by {value} on {day_month}.",
    ),
    openers=(
        "The two altitude fields from {ident_named} differed by {value} on {date}.",
        "On {date}, {ident_named} reported {alt_baro} by pressure and {alt_geom} by satellite.",
    ),
    body=("gap", "identity", "/", "reading", "limit"),
    tier=1,
    requires=("alt_baro", "alt_geom", "ident"),
)
DATA_SNAPSHOT = Angle(
    "data_snapshot",
    "A plain record of the data finding and the aircraft.",
    headlines=(
        "A data-quality finding for [{type} ]{ident} on {day_month}",
        "[{type_short} ]{ident} logs a data-quality finding on {day_month}",
    ),
    deks=(
        "The record for {ident} on {day_month} includes a data-quality finding[ near {place}].",
        "The {day_month} record for {ident} includes a data-quality finding[ near {place}].",
    ),
    openers=(
        "The record for {ident_named} on {date} includes a data-quality finding.",
        "On {date}, the record for {ident_named} contains a data-quality finding.",
    ),
    body=("identity", "reading", "/", "track", "limit"),
    tier=3,
    requires=("ident",),
)
IDENTITY_CONFLICT_ANGLES = (IDENTITY_CONTRAST, DATA_EXPLAINER, DATA_SNAPSHOT)
EMITTER_ANGLES = (EMITTER_CONTRAST, DATA_EXPLAINER, DATA_SNAPSHOT)
JUMP_ANGLES = (JUMP_CONTRAST, DATA_EXPLAINER, DATA_SNAPSHOT)
GAP_ANGLES = (GAP_CONTRAST, DATA_EXPLAINER, DATA_SNAPSHOT)

# Fail closed for kinds without their own angles: name the field, direction and unit.
GENERIC_VALUE = replace(
    PERFORMANCE_VALUE,
    id="generic_value",
    focus="The recorded value with its field, direction, and unit.",
    deks=PERFORMANCE_VALUE.deks[:2],
    openers=PERFORMANCE_VALUE.openers[:2],
    body=("identity", "reading", "/", "track", "limit"),
)
GENERIC_PLACE = Angle(
    "generic_place",
    "Where the aircraft was and what it reported.",
    headlines=(
        "[{type} ]{ident} flies near {place} on {day_month}",
        "Near {place}, [{type_short} ]{ident} on {day_month}",
    ),
    deks=(
        "{ident} reported {alt}[ and {gs}] near {place} on {day_month}.",
        "On {day_month}, {ident} reported {alt_phrase} near {place}.",
    ),
    openers=(
        "Near {place} on {date}, {ident_named} reported {alt_phrase}.",
        "{ident_named|cap} flew near {place} on {date}[ at {alt}].",
    ),
    body=("identity", "reading", "/", "track", "airport", "limit"),
    tier=2,
    requires=("place", "ident"),
)
GENERIC_RECORD = Angle(
    "generic_record",
    "A plain account of the aircraft and what it reported.",
    headlines=(
        "[{type} ]{ident} in the air on {day_month}[ at {alt}]",
        "[{type_short} ]{ident} reports {alt} on {day_month}",
        "{ident_named|cap} flies[ near {place}] on {day_month}",
    ),
    deks=(
        "{ident} reported {alt}[ and {gs}] on {day_month}[ near {place}].",
        "On {day_month}, {ident_named} reported {alt_phrase}[ near {place}].",
        "The evidence card below lists the reports from {ident_named} on {date}.",
    ),
    openers=(
        "On {date}, {ident_named} reported {alt_phrase}.",
        "{ident_named|cap} sent reports on {date}[ near {place}].",
    ),
    body=("identity", "reading", "/", "track", "airport", "limit"),
    tier=3,
    requires=("ident",),
)
GENERIC_ANGLES = (GENERIC_VALUE, GENERIC_PLACE, GENERIC_RECORD)

SHAPE_KINDS = tuple(SHAPES)
SQUAWK_CODES = tuple(sorted(kinds.STATUS_CODES))
STATUS_KINDS = tuple(sorted(kinds.STATUS_NAMES))
# The transponder-code kinds, live and archived, and the live ADS-B status kinds: the registry's
# squawk family, generated from its status tables in this order.
SQUAWK_CODE_KINDS = tuple(prefix + code for prefix in kinds.SQUAWK_PREFIXES for code in SQUAWK_CODES)
STATUS_CODE_KINDS = tuple(kinds.LIVE_SQUAWK_PREFIX + name for name in STATUS_KINDS)
# An ADS-B status kind: its status field, then the code its status stands for.
STATUS_KIND_ANGLES = (
    *STATUS_ANGLES,
    STATUS_GENERIC,
    replace(CODE_MEANING, requires=("code", "code_meaning", "ident")),
    replace(CODE_SNAPSHOT, requires=("code", "code_meaning", "ident")),
)
KIND_ANGLES = {
    **dict.fromkeys(("watch_type", "rare_here"), IDENTITY_ANGLES),
    **dict.fromkeys(("military", "database_watch"), (*IDENTITY_ANGLES, MILITARY_FLAG)),
    "notable_variant": (
        VARIANT_PROFILE,
        OPERATOR_SNAPSHOT,
        TYPE_PROFILE,
        BRIEF_GLIMPSE,
        SNAPSHOT,
    ),
    "vintage": VINTAGE_ANGLES,
    "watchlist_match": WATCHLIST_ANGLES,
    "first_type_here": FIRST_ANGLES,
    "first_country_here": (ALLOCATION,),
    "stratospheric_balloon": BALLOON_ANGLES,
    "outlier_gs": tuple(SPEED_OUTLIER.values()),
    **dict.fromkeys(("outlier_alt_baro", "outlier_archive_alt"), tuple(ALTITUDE_OUTLIER.values())),
    "outlier_baro_rate": tuple(RATE_OUTLIER.values()),
    "type_speed_mismatch": MISMATCH_ANGLES,
    "type_altitude_mismatch": ALTITUDE_MISMATCH_ANGLES,
    "fast": (PERFORMANCE_VALUE, SPEED_OUTLIER["jump_and_return"], OPERATOR_SNAPSHOT, PLACE_FIRST, SNAPSHOT),
    "high": (
        PERFORMANCE_VALUE,
        ALTITUDE_OUTLIER["jump_and_return"],
        OPERATOR_SNAPSHOT,
        PLACE_FIRST,
        SNAPSHOT,
    ),
    **dict.fromkeys(("wind_boost", "selected_altitude_overshoot"), PERFORMANCE_ANGLES),
    "seasonal_speed_record": SEASON_ANGLES,
    "above_rated_altitude": CEILING_ANGLES,
    "above_rated_speed": SPEED_LIMIT_ANGLES,
    **dict.fromkeys(("rapid_descent", "rapid_climb", "vertical"), RATE_ANGLES),
    "position_jump": JUMP_ANGLES,
    "altitude_gap": GAP_ANGLES,
    "identity_conflict": IDENTITY_CONFLICT_ANGLES,
    **dict.fromkeys(sorted(EMITTER_KINDS), EMITTER_ANGLES),
    "gnss_integrity_loss": (GNSS_SINGLE, GENERIC_PLACE, DATA_SNAPSHOT),
    "gnss_integrity_cluster": GNSS_ANGLES,
    "correlated_data_anomaly": (DATA_EXPLAINER, GENERIC_PLACE, DATA_SNAPSHOT),
    **dict.fromkeys(SHAPE_KINDS, SHAPE_ANGLES),
    "formation_flight": FORMATION_ANGLES,
    "holding_stack": STACK_ANGLES,
    "survey_grid": SURVEY_ANGLES,
    "thermal_soaring": THERMAL_ANGLES,
    **dict.fromkeys(SQUAWK_CODE_KINDS, SQUAWK_ANGLES),
    **dict.fromkeys(STATUS_CODE_KINDS, STATUS_KIND_ANGLES),
    "special_use_code": SPECIAL_CODE_ANGLES,
    "tcas_ra": TCAS_ANGLES,
}


def angles_for(kind):
    """The angle library for a kind; unknown kinds fail closed to the generic angles."""
    return KIND_ANGLES.get(kind) or GENERIC_ANGLES


IDENTITY_SEO = (
    "[{op_short} ]{type_short} {reg}[ near {place_short}]",
    "{type_short} {ident}",
    "{type} {ident}",
    "{ident}",
)
SEO_FACTS = {
    **dict.fromkeys(
        ("outlier_gs", "outlier_alt_baro", "outlier_archive_alt", "outlier_baro_rate"),
        "one {value_adj} {field_adj} spike",
    ),
    **dict.fromkeys(("type_speed_mismatch", "type_altitude_mismatch"), "{value_adj} {field_adj} report"),
    "position_jump": "{jump_nm} position jump",
    "altitude_gap": "altitude fields {value_round} apart",
    "identity_conflict": "identity conflict",
    **dict.fromkeys(sorted(EMITTER_KINDS), "emitter category {emitter}"),
    **dict.fromkeys(("gnss_integrity_loss", "gnss_integrity_cluster"), "navigation-integrity drop"),
    "correlated_data_anomaly": "related data anomalies",
    "special_use_code": "squawk {code}",
    "stratospheric_balloon": "balloon-like drift up to {max_alt}",
    "survey_grid": "{legs} parallel legs",
    "holding_stack": "{aircraft_total} aircraft circling",
    "thermal_soaring": "{gain} circling climb",
    "seasonal_speed_record": "fastest since {season_start}",
    "above_rated_altitude": "above its certified ceiling",
    "above_rated_speed": "{est_word} above its {lim_name}",
    **dict.fromkeys(("rapid_descent", "rapid_climb", "vertical"), "{change_adj} {rate_noun}"),
    **dict.fromkeys(("fast", "high", "wind_boost", "selected_altitude_overshoot"), "{value_phrase}"),
    **dict.fromkeys(SQUAWK_CODE_KINDS, "squawk {code}"),
    **dict.fromkeys(STATUS_CODE_KINDS, "{status} status"),
    **dict.fromkeys(SHAPE_KINDS, "{shape}"),
}
PAIR_SEO = {
    "tcas_ra": "TCAS {advisory} advisory",
    "formation_flight": "Formation-like pair",
}


def seo_candidates(kind):
    """Title-tag templates in priority order; optional parts drop from the end to fit the title limit.

    A title tag is fixed at a story's first publication, so a change here reaches new stories
    only; like a headline, it carries no scope phrase ("these records").
    """
    if kind == "vintage":
        return ("{type_short} {reg}, listed as built {built}", *IDENTITY_SEO)
    if kind == "first_type_here":
        return ("{type_short} spotted for the first time", *IDENTITY_SEO)
    if kind == "first_country_here":
        return (
            "First {alloc_country} address spotted: {type_short}",
            "First {alloc_country} address spotted: {ident}",
            *IDENTITY_SEO,
        )
    if kind in PAIR_SEO:
        fact = PAIR_SEO[kind]
        lowered = fact[0].lower() + fact[1:]
        return (
            fact + ": two {type_short|same}s[ near {place_short}]",
            fact + ": {type_short} and {p1_short|mixed}[ near {place_short}]",
            "{type_short} {ident}: " + lowered,
            "{ident}: " + lowered,
            *IDENTITY_SEO,
        )
    fact = SEO_FACTS.get(kind)
    if fact is None and kind in KIND_ANGLES:
        return IDENTITY_SEO
    fact = fact or "{value_phrase}"
    joiner = " " if kind in SHAPE_KINDS else ": "
    return (
        "{type_short} {ident}" + joiner + fact,
        "{ident}" + joiner + fact,
        *IDENTITY_SEO,
    )
