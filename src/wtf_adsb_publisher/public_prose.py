"""Deterministic checks for AI-written public prose against its evidence packet.

Published text needs no human approval, so these checks are strict. Every numeral,
date, time and identifier must resolve to a typed packet fact, allowing only the
rounding a sentence states and standard unit conversions; a reading stated with a
time must be the reading saved at that time. Comparisons are checked as
inequalities. Names must come from the packet. First-person and reception framing,
which could help locate the receiver, is refused. Claims about owners, occupants,
emergencies, hijacking, missions, weather, air traffic control, landings, rarity,
fleets, safety and visibility need a hedge that governs them. In a public story a
possibility hedge offers only a listed hypothesis or limit, and purposes, near
misses and, in watchlist stories, kinds of people are refused even when hedged.
Passing establishes neither accuracy nor completeness; the checks reject known
failure modes.
"""

import bisect
import functools
import math
import re
import unicodedata
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from . import curation, enrichment_rules, episode_evidence, kinds, places, rules, site_identity

MAX_PROSE = 20000
# Packet keys whose values are opaque, private or not facts about the observation.
SKIPPED_KEYS = {
    "url",
    "source_url",
    "source_commit",
    "replay",
    "id",
    "event_id",
    "evidence_version",
    "snapshot_id",
    "revision",
    "schema_version",
    "article_path",
    "media",
    "ghost_id",
    "source_row",
    "licenses",
    "references",
    "links",
}
# Strings whose digit-bearing tokens are identifiers a writer may repeat exactly.
IDENTITY_KEYS = {
    "year",
    "database_year",
    "registration",
    "callsign",
    "icao_address",
    "type_code",
    "type_description",
    "hex",
    "r",
    "t",
    "desc",
    "flight",
    "squawk",
    "reported_squawk",
    "code",
    "advisory",
    "emitter_category",
    "category",
    "neutral_category",
    "source_name",
    "association_label",
    "label",
    "us_registration_from_address",
    "callsign_registration",
    "allocation_country",
    "registration_country",
    "registry_type",
}
TYPE_PHRASE_KEYS = {"type_description", "desc", "advisory"}
CATEGORY_PHRASE_KEYS = {"category", "neutral_category"}
TIME_KEY = re.compile(r"(?:^|_)(?:ts|timestamp|at|first_seen|last_seen)$")
FIELD_UNITS = {
    "gs": "kn",
    "tas": "kn",
    "ias": "kn",
    "mach": "Mach",
    "alt_baro": "ft",
    "alt_geom": "ft",
    "archive_alt": "ft",
    "alt": "ft",
    "nav_altitude_mcp": "ft",
    "baro_rate": "ft/min",
    "geom_rate": "ft/min",
    "nav_qnh": "hPa",
    "track": "degrees",
    "true_heading": "degrees",
}
SUFFIX_UNITS = (
    ("_fpm", "ft/min"),
    ("_ft", "ft"),
    ("_kn", "kn"),
    ("_kt", "kn"),
    ("_nm", "NM"),
    ("_seconds", "s"),
    ("_minutes", "minutes"),
    ("_degrees", "degrees"),
    ("_observations", "samples"),
    ("_count", "samples"),
    ("closed_loops", "loops"),
    ("accumulated_turns", "loops"),
    ("complete_passes", "passes"),
    ("reversals", "reversals"),
)
# Packet and public-event unit strings: (dimension, factor to the canonical unit).
PACKET_UNITS = {
    "ft": ("altitude", 1),
    "ft regained": ("altitude", 1),
    "ft apart": ("altitude", 1),
    "kn": ("speed", 1),
    "kt": ("speed", 1),
    "kn difference": ("speed", 1),
    "ft/min": ("vrate", 1),
    "s": ("time", 1),
    "minutes": ("time", 60),
    "nm": ("distance", 1),
    "NM": ("distance", 1),
    "degrees": ("angle", 1),
    "deg/s": ("turn_rate", 1),
    "Mach": ("mach", 1),
    "years": ("years", 1),
    "hPa": ("pressure", 1),
    "%": ("percent", 1),
    "samples": ("count:samples", 1),
    "reports": ("count:samples", 1),
    "loops": ("count:loops", 1),
    "turns": ("count:loops", 1),
    "passes": ("count:passes", 1),
    "legs": ("count:passes", 1),
    "reversals": ("count:reversals", 1),
    "addresses": ("count:aircraft", 1),
    "aircraft": ("count:aircraft", 1),
    "IDs / 30 days": ("count:aircraft", 1),
    "tags": ("count:tags", 1),
    "tag": ("count:tags", 1),
    # Story facts: an ordinal such as "fourth", counts of earlier stories and spans of days.
    "ordinal": ("ordinal", 1),
    "stories": ("count:stories", 1),
    "days": ("time", 86400),
}
RULE_KEYS = {"threshold_fpm", *episode_evidence.RULE_METRICS}
LEVEL_KEYS = {
    "alt_baro",
    "alt_geom",
    "archive_alt",
    "alt",
    "nav_altitude_mcp",
    "start_altitude_ft",
    "end_altitude_ft",
    "level_off_altitude_ft",
    "max_altitude_ft",
}
# Nautical miles in one statute mile; knots to mph and NM to miles both use it.
STATUTE_MILE_NM = 0.868976
# Prose units in matching order: (pattern, [(dimension, factor to canonical)]).
PROSE_UNITS = [
    (
        r"feet[ -]per[ -]minute|foot[ -]per[ -]minute|feet[ -]a[ -]minute|ft[ -]per[ -]min(?:ute)?|ft/min|fpm",
        [("vrate", 1)],
    ),
    (r"metres[ -]per[ -]second|meters[ -]per[ -]second|m/s", [("vrate", 196.850394), ("speed", 1.943844)]),
    (r"metres[ -]per[ -]minute|meters[ -]per[ -]minute|m/min", [("vrate", 3.280840)]),
    (r"miles[ -]per[ -]hour|miles[ -]an[ -]hour|mph", [("speed", STATUTE_MILE_NM)]),
    (r"kilometres[ -]per[ -]hour|kilometers[ -]per[ -]hour|km/h|kph|kmh", [("speed", 0.539957)]),
    (r"knots|knot|kts|kt|kn", [("speed", 1)]),
    (r"nautical[ -]miles|nautical[ -]mile|nmi|nm", [("distance", 1)]),
    (r"kilometres|kilometers|kilometre|kilometer|km", [("distance", 0.539957)]),
    (r"statute[ -]miles|statute[ -]mile|miles|mile|mi", [("distance", STATUTE_MILE_NM)]),
    (r"feet|foot|ft", [("altitude", 1)]),
    (r"metres|meters|metre|meter|m", [("altitude", 3.280840)]),
    (r"seconds|second|secs|sec|s", [("time", 1)]),
    (r"minutes|minute|mins|min", [("time", 60)]),
    (r"hours|hour|hrs|hr|h", [("time", 3600)]),
    (r"days|day", [("time", 86400)]),
    (r"weeks|week", [("time", 604800)]),
    (r"years|year|yrs|yr", [("years", 1)]),
    (r"decades|decade", [("years", 10)]),
    (r"centuries|century", [("years", 100)]),
    # A turn rate, before the bare angle so "degrees per second", "degrees a second", and "degrees
    # each second" read as one unit.
    (r"degrees?[ -](?:per|a|each)[ -]second|deg/s|\u00b0/s", [("turn_rate", 1)]),
    (r"degrees|degree|deg|\u00b0", [("angle", 1)]),
    (r"percent|per[ -]cent|%", [("percent", 1)]),
    (r"hectopascals|hectopascal|hpa|millibars|millibar|mbar|mb", [("pressure", 1)]),
    (r"inches[ -]of[ -]mercury|inhg", [("pressure", 33.8639)]),
]
COUNT_NOUNS = {
    "loops": "loops|loop|circles|circle|orbits|orbit|laps|lap|turns|turn|revolutions|revolution",
    "passes": "passes|pass|legs|leg|runs|run",
    "samples": "reports|report|samples|sample|readings|reading|positions|position|messages|message"
    "|windows|window"
    "|fixes|fix|updates|update|points|point|observations|observation|polls|poll|repetitions|repetition"
    "|repeats|repeat|values|value|entries|entry",
    "reversals": "reversals|reversal|changes|change",
    "stories": "stories|story|appearances|appearance|visits|visit",
    "tags": "tags|tag",
    "aircraft": "aircraft|planes|plane|airplanes|airplane|jets|jet|helicopters|helicopter|addresses|address"
    "|airframes|airframe|targets|target|ids|id",
    "times": "times",
}
COUNT_ADJECTIVES = (
    "position|positional|closed|complete|completed|full|consecutive|distinct|separate|qualifying|saved"
    "|retained|straight|parallel|alternating|received|reported|transponder|telemetry|status|individual"
    "|high-rate|above-threshold|more|other|further|additional|ads-b|mlat|different|tracked|accumulated"
    "|extreme|isolated|outlying|unusual|large|high|low|similar|surrounding|neighbou?ring|earlier|later|initial"
    "|final|last|single|brief|short|long|separate|valid|recorded|archived|altitude|speed|rate|analysis"
    "|movement|overlapping|adjacent|observation|evidence|left|right|left-hand|right-hand|clockwise"
    "|counterclockwise|anticlockwise|estimated|multilateration"
)
STOPWORDS = set(
    """a an the and or but nor of to in on at by for with from into onto over under between among across along
    around about above below through throughout past per each every later earlier apart again too also only
    just both either neither is was were are be been being has had have did does do will would could should may
    might can must shall that which who whom whose where when while whereas here there this these those it its
    they their he she his her him them not no so if because though although yet still then than as during before
    after until since utc gmt z reported recorded saved observed transmitted broadcast logged""".split()
)
WORD_VALUES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "dozen": 12,
}
SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000}
_UNIT_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth")
ORDINAL_WORDS = (
    {word: value for value, word in enumerate(_UNIT_ORDINALS, start=1)}
    | {
        "tenth": 10,
        "eleventh": 11,
        "twelfth": 12,
        "thirteenth": 13,
        "fourteenth": 14,
        "fifteenth": 15,
        "sixteenth": 16,
        "seventeenth": 17,
        "eighteenth": 18,
        "nineteenth": 19,
        "twentieth": 20,
        "thirtieth": 30,
    }
    | {
        f"{tens}-{word}": base + value
        for tens, base in (("twenty", 20), ("thirty", 30))
        for value, word in enumerate(_UNIT_ORDINALS, start=1)
        if base + value <= 31
    }
)
# Month names in order, with the abbreviations prose uses; editorial and story_urls read them too.
# A literal table, not calendar.month_name, so public text never depends on the locale.
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTHS = {
    name: index
    for index, full in enumerate(MONTH_NAMES, start=1)
    for name in (full, full[:3], *(("Sept",) if index == 9 else ()))
}
# Number words longest first, for the regex alternations that read spelled counts.
SMALL_WORDS = "|".join(sorted(WORD_VALUES, key=len, reverse=True))
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
FIXED_VOCABULARY = (
    set(
        """utc gmt zulu ads adsb mlat tis mode faa icao plane alert db flightaware flightradar24 jetphotos
        exchange wikipedia nm fl mach tcas acas ra gps gnss nic nac nacp sil gva sda qnh vfr ifr pia ladd uas uav
        wtf united states multilateration doc air traffic control id ids automatic dependent surveillance
        broadcast earth""".split()
    )
    | {name.lower() for name in MONTHS}
    | set(WEEKDAYS)
    | set("abcdefghijklmnopqrstuvwxyz")
)
# Case-sensitive months, so the verb "may" and "march" never parse as dates.
MONTH = r"(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
SPELLED_DAY = r"(?:" + "|".join(sorted(ORDINAL_WORDS, key=len, reverse=True)) + r")"
NUMBER = r"(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
NUMBER_WORDS = "|".join(sorted([*WORD_VALUES, *SCALES], key=len, reverse=True))
UNIT_PATTERN = "|".join(f"(?:{pattern})" for pattern, _ in PROSE_UNITS)
# A speed or an altitude with its unit, in any unit the prose may use: "651 knots", "750 mph",
# "1,200 km/h", "41,000 ft", "12,000 metres".
SPEED_OR_ALTITUDE_UNIT = "|".join(
    f"(?:{pattern})"
    for pattern, dimensions in PROSE_UNITS
    if any(dimension in {"speed", "altitude"} for dimension, _scale in dimensions)
)
SPEED_OR_ALTITUDE = NUMBER + r"\s*-?(?:" + SPEED_OR_ALTITUDE_UNIT + r")\b"
COUNT_PATTERN = "|".join(f"(?:{nouns})" for nouns in COUNT_NOUNS.values())
UNIT_AFTER = re.compile(
    r"(?:\s{0,3}-\s{0,3}|\s{1,4}|(?<=\d))(?:(?:"
    + COUNT_ADJECTIVES
    + r"|(?!(?:"
    + UNIT_PATTERN
    + r")-)[a-z]+-[a-z]+),?[\s-]{1,4}){0,3}"
    r"(?P<unit>" + UNIT_PATTERN + "|" + COUNT_PATTERN + r")(?![a-z0-9])",
    re.I,
)
NEXT_WORD = re.compile(r"(?:\s{0,3}-\s{0,3}|\s{1,4})(?P<word>[A-Za-z][A-Za-z'-]*)")
DIGITS = re.compile(
    r"(?P<sign>(?<![\w.])[-+](?=\.?\d))?(?<![\w.])(?<![A-Za-z]-)(?P<n>"
    + NUMBER
    + r")(?P<ord>st|nd|rd|th)?(?!\w)"
)
SPELLED = re.compile(
    r"\b(?P<w>(?:an?\s+)?(?:" + NUMBER_WORDS + r")(?:(?:[\s-]+(?:and[\s-]+)?)(?:" + NUMBER_WORDS + r"))*)\b",
    re.I,
)
MULTIPLIER = re.compile(r"[\s-]{0,3}(?P<mult>hundred|thousand|million)\b", re.I)
SMALL_COUNT = r"(?:\d+|an?|" + SMALL_WORDS + ")"
COMPOUND_DURATION = re.compile(
    r"\b(?:(?P<h>" + SMALL_COUNT + r")[\s-]{0,3}(?:hours?|hrs?)\b[\s,-]{0,4}(?:and\s{1,3})?)?"
    r"(?:(?P<m>" + SMALL_COUNT + r")[\s-]{0,3}(?:minutes?|mins?)\b[\s,-]{0,4}(?:and\s{1,3})?)?"
    r"(?:(?P<s>\d+(?:\.\d+)?|" + SMALL_COUNT + r")[\s-]{0,3}(?:seconds?|secs?)\b)?",
    re.I,
)
ISO = re.compile(
    r"\b(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})"
    r"(?:[T ](?P<h>\d{2}):(?P<mi>\d{2})(?::(?P<s>\d{2})(?:\.\d+)?)?)?"
    r"(?:\s*(?P<zone>Z|UTC|GMT|[+-]\d{2}:?\d{2}))?(?![\d:])"
)
NUMERIC_DATE = re.compile(r"\b(?P<a>\d{1,4})(?P<sep>[/.])(?P<b>\d{1,2})(?P=sep)(?P<c>\d{2,4})\b")
DATE_RANGE = re.compile(
    r"\b(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s*(?:-|\u2013|to|and)\s*(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?"
    r"(?P<mo>" + MONTH + r")(?:,?\s+(?P<y>\d{4}))?\b"
    r"|\b(?P<mo2>" + MONTH + r")\s+(?P<d3>\d{1,2})(?:st|nd|rd|th)?\s*(?:-|\u2013|to|and)\s*(?P<d4>\d{1,2})"
    r"(?:st|nd|rd|th)?(?:,?\s+(?P<y2>\d{4}))?\b"
)
DAY_MONTH = re.compile(
    r"\b(?:the\s+)?(?P<d>\d{1,2}(?:st|nd|rd|th)?|" + SPELLED_DAY + r")\s+(?:of\s+)?(?P<mo>" + MONTH + r")"
    r"(?:,?\s+(?P<y>\d{4}))?\b",
    re.I,
)
MONTH_DAY = re.compile(
    r"\b(?P<mo>" + MONTH + r")\s+(?:the\s+)?(?P<d>\d{1,2}(?:st|nd|rd|th)?|" + SPELLED_DAY + r")\b"
    r"(?:,?\s+(?P<y>\d{4}))?"
)
MONTH_YEAR = re.compile(r"\b(?P<mo>" + MONTH + r")\s+(?P<y>\d{4})\b")
MONTH_ALONE = re.compile(r"\b(?P<mo>" + MONTH + r")(?![\w])")
CLOCK = re.compile(
    r"(?<![\d:.,])(?P<h>[01]?\d|2[0-3]):(?P<mi>[0-5]\d)(?::(?P<s>[0-5]\d)(?:\.\d+)?)?(?![\d:])"
    r"(?:\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?![a-z]))?(?:\s*(?:UTC|GMT|Z)\b)?",
    re.I,
)
MILITARY_TIME = re.compile(r"\b(?P<h>[01]\d|2[0-3])(?P<mi>[0-5]\d)\s?(?:Z|UTC|GMT)\b")
HOUR_TIME = re.compile(
    r"\b(?P<h>1[0-2]|0?[1-9])\s*(?:(?P<ap>a\.m\.|p\.m\.|am|pm)(?![a-z])|o'clock)(?:\s*(?:UTC|GMT))?", re.I
)
HOUR_WORDS = "|".join(name for name, value in WORD_VALUES.items() if 1 <= value <= 12)
# Spoken clock times: "half past ten", "twenty to two", "a quarter of an hour after midnight".
SPOKEN_TIME = re.compile(
    r"\b(?:(?P<count>an?|\d{1,2}|" + SMALL_WORDS + r")\s+)?(?P<unit>quarters?(?:\s+of\s+an)?\s+hours?"
    r"|half(?:\s+an|-an)?[\s-]+hours?|hours?|minutes?)\s+(?P<rel>after|past|before|to)\s+(?P<base>midnight|noon)\b"
    r"|\b(?P<part>half|quarter|\d{1,2}|" + SMALL_WORDS + r")(?:\s+minutes?)?\s+(?P<rel2>past|to)\s+"
    r"(?P<h>midnight|noon|1[0-2]|0?[1-9]|" + HOUR_WORDS + r")\b",
    re.I,
)
NAMED_TIME = re.compile(r"\b(?P<name>noon|midnight)\b", re.I)
FLIGHT_LEVEL = re.compile(r"\b(?:FL\s?|flight level\s+)(?P<n>\d{2,3})\b", re.I)
MACH = re.compile(r"\bMach\s+(?P<a>" + NUMBER + r")|(?<![\d.])(?P<b>" + NUMBER + r")\s+Mach\b", re.I)
DECADE = re.compile(r"\b(?P<n>1[89]\d0|20\d0)'?s\b")
CURRENCY = re.compile(
    r"[$\u20ac\u00a3\u00a5]\s*" + NUMBER + r"|\b" + NUMBER + r"\s*(?:dollars|euros|pounds|usd|eur|gbp)\b",
    re.I,
)
FRACTION = re.compile(
    r"\b(?:(?P<whole>\d+|" + SMALL_WORDS + r")\s+and\s+)?(?:a|one)[\s-]+half\b"
    r"|\bhalf\b|\b(?:a|one|two)[\s-]+thirds?\b|\b(?:a|one|three)[\s-]+quarters?\b|\bthree-quarters\b",
    re.I,
)
AND_A_HALF = re.compile(
    r"\b(?P<n>an?|\d+|" + SMALL_WORDS + r")\s+(?P<unit>" + UNIT_PATTERN + r")\s+and\s+a\s+half\b",
    re.I,
)
MULTIPLE = re.compile(
    r"\b(?:twice|thrice|double|triple|quadruple|(?P<n>\d+(?:\.\d+)?|" + SMALL_WORDS + r")"
    r"[\s-]{0,3}(?:times|fold|x))\b",
    re.I,
)
COMPARATIVE_AFTER = re.compile(
    r"\s+(?:as\b|the\b|that\b|faster|slower|higher|lower|more|less|greater|larger|bigger|smaller|longer|shorter"
    r"|steeper|over\b|above\b|below\b|than\b)",
    re.I,
)
IDENTIFIER = re.compile(
    r"(?<![\w/-])(?!\d+(?:st|nd|rd|th)(?![\w-]))(?=[\w/-]*\d)(?=[\w/-]*[A-Za-z])"
    r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*(?![\w/-])"
)
DIGIT_RUN = re.compile(r"(?<![\w-])\d+(?:-\d+)+(?![\w-])")
TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-/.][A-Za-z0-9]+)*")
WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
GREATER = (
    r"more than|over|above|exceed(?:ed|ing|s)?|greater than|higher than|faster than|larger than|at least"
    r"|upwards of|beyond|in excess of|topp(?:ed|ing)|surpass(?:ed|ing)?"
)
NEVER = (
    r"never (?:exceeded|went above|rose above|topped|passed)|did not exceed|didn't exceed|no more than"
    r"|not more than|at most|stayed (?:below|under)|remained (?:below|under)|never above"
)
LESS = r"less than|under|below|fewer than|lower than|slower than|smaller than|up to|within"
NEARLY = r"nearly|almost|just under"
ABOUT = r"about|around|roughly|approximately|approx|some|circa|close to|approaching"
RELATION = re.compile(
    r"\b(?:(?P<never>" + NEVER + r")|(?P<greater>" + GREATER + r")|(?P<nearly>" + NEARLY + r")"
    r"|(?P<less>" + LESS + r")|(?P<about>" + ABOUT + r"))(?:\s+[\w'$-]+){0,2}\s*$",
    re.I,
)
RULE_CONTEXT = re.compile(
    r"\b(?:threshold|cutoff|cut-off|limit|rule|criteri\w+|band|profile|setting|filter|defin\w+|between)\b",
    re.I,
)
CLIMB_WORDS = re.compile(r"\b(?:climb\w*|ascen\w+|rose|rising|gain\w*)\b", re.I)
DESCENT_WORDS = re.compile(
    r"\b(?:descen\w+|fell|falling|dropp?\w*|sank|sinking|dive\w*|diving|lost|losing)\b", re.I
)
RATE_CLIMB = re.compile(r"\b(?:climb\w*|ascen\w+)\b", re.I)
RATE_DESCENT = re.compile(r"\b(?:descen\w+|sank|sinking|dive\w*|diving)\b", re.I)
SPACING_CONTEXT = re.compile(
    r"\b(?:later|earlier|before|after|apart|intervals?|spacing|spaced|next|previous|preceding|following|gaps?"
    r"|between|every|each)\b",
    re.I,
)
LEVEL_CONTEXT = re.compile(
    r"\b(?:to|at|from|through|near|above|below|between)\s+(?:about|around|roughly|approximately|nearly|almost"
    r"|some|just over|just under)?\s*$",
    re.I,
)
LOOP_WORDS = re.compile(r"\b(?:circl\w*|loop\w*|orbit\w*|lap\w*|turn\w*)\b", re.I)
PASS_WORDS = re.compile(r"\b(?:pass\w*|leg\w*|crossed)\b", re.I)
SAMPLE_WORDS = re.compile(
    r"\b(?:reports?|readings?|samples?|messages?|positions?|recorded|reported|transmitted|broadcast|logged"
    r"|appear(?:s|ed)?|observed)\b",
    re.I,
)
REVERSAL_WORDS = re.compile(r"\b(?:revers\w*|chang\w* direction|direction chang\w*|switch\w*)\b", re.I)

LOCATION = [
    re.compile(pattern, flags)
    for pattern, flags in (
        (r"\b(?:[Ww]e|[Uu]s|[Oo]urs?|[Oo]urselves|[Mm]e|[Mm]y|[Mm]ine|[Mm]yself)\b", 0),
        # Capitals too, but never "US", which names the country.
        (r"\b(?:WE|OURS?|OURSELVES|ME|MY|MINE|MYSELF)\b|(?i:\blet's\b|\blet us\b)", 0),
        (r"(?<!War )(?<!Mark )(?<!TCAS )(?<!Class )(?<!Phase )(?<!Type )\bI\b(?![-.])", 0),
        (
            r"\b(?:receiver|antennas?|feeders?|dongles?|sdrs?(?!-enthusiasts)|software-defined radio|horizon"
            r"|line[ -]of[ -]sight|reception|receiving|heard|hear|hears|hearing|vicinity|neighbou?rhoods?"
            r"|backyards?|rooftops?"
            r"|nearby(?!\s+(?:[\w/-]+\s+)?(?:readings|reports|samples|values|measurements|positions|data"
            r"|times|timestamps))|locally|local|coverage|listening|within reach|out of reach|out of sight|out of view"
            r"|signal strength|rssi|dbfs"
            r"|message rate|messages per second|range rings?|stations?(?![- ]keeping)|home(?![- ]?built))\b",
            re.I,
        ),
        (r"\bpicked\s+(?:\w+\s+){0,2}up\b", re.I),
        (
            r"\bsignals?\s+(?:(?:was|were|is|are|got|grew|became)\s+)?(?:faded|fades|fading|dropped|weakened|weak"
            r"|strong|faint|good|poor|clear|lost|disappeared|vanished)\b",
            re.I,
        ),
        (
            r"\b(?:above|over|near|close to|beside|next to|around|by)\s+(?:the|our|a)\s+(?:site|station|receiver"
            r"|antenna|feeder|collector)\b",
            re.I,
        ),
        (r"\btoo (?:low|far|distant) to be (?:seen|heard|received|tracked|detected|picked up)\b", re.I),
        (
            r"\b(?:site|station|receiver|antenna|feeder|collector)s?\s+(?:saw|sees|heard|hears|tracked|tracks"
            r"|detected"
            r"|detects|received|receives|picked|caught|logged|logs|spotted)\b",
            re.I,
        ),
        (
            r"\b(?:a|one|two|three|four|five|few|several|many|some)\s+(?:\w+\s+)?(?:miles?|nm|nautical miles?"
            r"|kilomet\w+|km)\s+(?:away|out|off|from|to the (?:north|south|east|west)\w*)\b"
            r"|\b(?:miles?|kilomet\w+|km|nm)\s+(?:away|out)\b",
            re.I,
        ),
        (
            r"\b(?:is|was|were|are|lay|sat)\s+(?:\w+\s+)?due\s+(?:north|south|east|west)\b"
            r"|\b(?:north|south|east|west|north-?east|north-?west|south-?east|south-?west)\s+of\s+(?:here|us"
            r"|the (?:station|site|receiver|antenna|feeder))\b",
            re.I,
        ),
        (r"\b" + NUMBER + r"\s*(?:nm|nautical[ -]miles?|km|kilomet\w+|miles?|mi)\s+(?:away|out|off)\b", re.I),
        (
            r"\bwhere (?:these|the|its|those) (?:reports|data|positions|signals|messages) (?:was|were) "
            r"(?:collected|recorded|received|logged)\b",
            re.I,
        ),
        (
            r"\b(?:in|out of|within|beyond|outside|edge of)\s+(?:receiver'?s?\s+|reception\s+|radio\s+)?range\b"
            r"(?!\s+of\b)",
            re.I,
        ),
        (
            r"\brange\s+(?:of\s+)?(?:about\s+|roughly\s+|some\s+)?"
            + NUMBER
            + r"\s*(?:nm|nautical|km|kilomet|mi)",
            re.I,
        ),
        (r"\bazimuth\b|\bbearing\s+(?:of\s+)?\d", re.I),
        (r"\b(?:directly\s+|right\s+)?overhead\b", re.I),
        (r"\br_(?:dst|dir)\b|\b(?:receiver|site)_?(?:lat|lon)\w*", re.I),
        (r"\.ts\.net\b|\blocalhost\b|\btailscale\b|\btailnet\b", re.I),
        (r"\blocal time\b", re.I),
        (
            r"\b(?:eastern|central|mountain|pacific|atlantic|alaska|hawaii|greenwich)\s+(?:standard\s+|daylight\s+)?"
            r"time\b",
            re.I,
        ),
        (r"\b(?:[ECMP][SD]T|ET|PT|BST|CET|CEST|AEST|AEDT|IST|JST)\b", 0),
        (r"\b(?:UTC|GMT)\s*[+-]\s*\d", re.I),
        (
            r"\b(?:morning|afternoon|evening|night|nights|nighttime|night-time|daytime|daylight|overnight|tonight"
            r"|dawn|dusk|sunrise|sunset|daybreak|lunchtime|teatime|rush hour|after dark|before dark|in the dark"
            r"|darkness)\b",
            re.I,
        ),
        (r"\b(?:midday|nightfall)\b", re.I),
    )
]
CLOSEST_APPROACH = re.compile(r"\bclosest (?:point of )?approach\b", re.I)
PAIR_CONTEXT = re.compile(r"\b(?:between|their|the two|each other|another aircraft|other aircraft)\b", re.I)
RECEIVERS = re.compile(r"\breceivers\b", re.I)
MLAT_CONTEXT = re.compile(r"\b(?:mlat|multilateration|several|multiple|many|network|across)\b", re.I)
RELATIVE_DATE = re.compile(
    r"\b(?:yesterday|today|tomorrow|this (?:week|month|year|weekend)|last (?:week|month|year|weekend"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|next (?:week|month|year)|recently|lately"
    r"|earlier this \w+|ago|the (?:following|next) (?:day|morning|afternoon|evening|night|week|weekend|month"
    r"|year)|the (?:day|week|month|year) after)\b",
    re.I,
)
# A repeat visit dated from another story: "a month earlier", "the previous day". It needs an
# earlier story whose date is that far before the observation.
RELATIVE_REPEAT = re.compile(
    r"\b(?:(?P<n>an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d{1,3})\s+"
    r"(?P<unit>days?|weeks?|months?|years?)\s+(?:earlier|before|prior|previously)"
    r"|the (?:previous|preceding|prior) (?P<unit2>day|week|month|year)|the (?P<unit3>day|week|month|year) before)\b",
    re.I,
)
URLISH = re.compile(
    r"https?://|www\.|<[^>]{1,200}>|\b[\w.+-]{1,64}@[\w-]{1,63}\.[\w.]{1,63}|\[[^\]]{0,200}\]\([^)]{0,200}\)"
    r"|\*\*|__|(?:^|\n)[^\S\n]{0,8}#+\s"
    r"|\b[\w-]{1,63}\.(?:com|org|net|gov|mil|edu|io|co|uk|us|aero|info|app|dev|ly|me|tv)\b",
    re.I,
)
# Hedge vocabularies share these fragments, so a word added to one reaches the others.
NEGATED_AUX = (
    r"cannot|can't|can not|could not|couldn't|does not|doesn't|do not|don't|did not|didn't"
    r"|is not able to|are not able to"
)
ALONE = r"(?:by itself\s+|alone\s+|on its own\s+|on their own\s+)?"
SHOW_VERBS = (
    r"show|establish|confirm|prove|identify|indicate|reveal|tell|determine|verify|demonstrate|mean|imply|say"
)
# "May" with a capital is the month ("on 20 May"), never a hedge; the patterns ignore case otherwise.
MODAL_HEDGES = r"(?-i:may)|might|could|possibly|possible|possibility|perhaps|hypothes\w+|one explanation"
LIKENESS_HEDGES = r"consistent with|resembl\w+|typical of"
EPISTEMIC = re.compile(
    r"\b(?:" + NEGATED_AUX + r")\s+" + ALONE + r"(?:" + SHOW_VERBS + r")"
    # "The value could not be confirmed": a negated modal states a limit, not a possibility.
    r"|\b(?:cannot|can't|can not|could not|couldn't|may not|might not)\s+be\s+(?:independently\s+)?"
    r"(?:confirmed|verified|established|shown|determined|identified|proven|proved|known)\b"
    r"|\b(?:(?:is|are|was|were)\s+not|isn't|aren't|wasn't|weren't)\s+(?:[\w-]+\s+){0,4}?"
    r"(?:evidence|proof|confirmation|indication|determination|established|known|confirmed|verified|identified"
    r"|clear|shown|determined)\b"
    r"|\bno\s+(?:evidence|indication|sign|proof|confirmation|way to (?:know|tell|confirm|establish))"
    r"|\bno\s+(?:[\w-]+\s+){0,4}?(?:established|known|confirmed|verified|identified|determined)\b"
    r"|\bnot\s+(?:evidence|proof|confirmation|verification|an? indication|an? determination)\s+(?:of|about|that"
    r"|for)\b"
    r"|\b(?:neither|nor)\s+(?:[\w-]+\s+)?(?:establish|show|confirm|prove|identif|indicat)\w*"
    r"|\brather than\s+(?:independent\s+|direct\s+)?(?:confirmation|proof|evidence|verification)\b"
    r"|\b(?:says|tells|shows|reveals)\s+nothing\b|\bnothing\s+about\b|\b(?:unresolved|unanswered)\b"
    r"|\b(?:unknown|unverified|unconfirmed|unclear|undetermined|unestablished)\b"
    r"|\bnot\s+(?:established|known|confirmed|verified|identified)\b"
    r"|\bwhether\b|\bnothing\s+(?:in|about)\s+(?:the|these|this)\b",
    re.I,
)
# The occupant disclaimer a watchlist story carries: "not who was aboard", "none of that
# shows who was aboard", "beyond what the data shows". It governs only the occupant claim,
# so "beyond what" never excuses an owner or rarity claim in the same clause.
OCCUPANT_DISCLAIMER = re.compile(
    r"\b(?:not|nor)\s+who\b"
    r"|\bnone of (?:that|this|it|these|those|the \w+)\s+(?:shows?|says?|establish\w*|confirm\w*|prov\w*"
    r"|identif\w*|tells?)\b"
    r"|\bbeyond what\b"
    r"|\b(?:does|do|did|can|could|will)\s+not\s+(?:show|say|tell|reveal|establish|confirm)\b",
    re.I,
)
POSSIBILITY = re.compile(
    r"\b(?:" + MODAL_HEDGES + "|" + LIKENESS_HEDGES + r"|typically"
    r"|can (?:occur|happen|indicate|mean|reflect|result|be)|is sometimes|are sometimes|often|suggests?"
    r"|suggesting|appears?|appeared|seems?|seemed|likely)\b",
    re.I,
)
# Possibility words strong enough to hedge an emergency claim; "suggests" and "likely" are not.
STRONG_POSSIBILITY = re.compile(
    r"\b(?:" + MODAL_HEDGES + r"|cannot be ruled out|can (?:occur|happen|be))\b",
    re.I,
)
# A statement of what the data cannot show; "no sign of an emergency" is a claim, not this.
SHOW_LIMIT = re.compile(
    r"\b(?:" + NEGATED_AUX + r")\s+" + ALONE + r"(?:" + SHOW_VERBS + r")\b|\bwhether\b|\bsays\s+nothing\b"
    r"|\bnothing\s+about\b|\b(?:unknown|unverified|unconfirmed|unclear|undetermined)\b",
    re.I,
)
NEGATION = re.compile(
    r"\b(?:not|no|none|never|nobody|no one|nothing|neither|nor|isn't|aren't|wasn't|weren't|don't|doesn't"
    r"|didn't|cannot|can't|without)\b",
    re.I,
)
# Plane Alert DB by name, which a story that uses its entry carries in the dek or a paragraph;
# a sentence may also attribute a detail to "the watchlist" or "the community watchlist".
PLANE_ALERT = re.compile(r"\bplane alert\b", re.I)
WATCHLIST_SOURCE = re.compile(PLANE_ALERT.pattern + r"|\b(?:the watchlist|community watchlist)\b", re.I)
# In a story that names Plane Alert DB, a sentence may refer back to the entry instead of
# naming the list again: "the entry names", "the list files it under", "its entry carries".
WATCHLIST_REFERENCE = re.compile(
    r"\b(?:(?:the|its|that|this) (?:entry|listing|list(?:'s)?|watchlist entry|community list)"
    r"|(?:a|the) spotters' watchlist|the (?:listed )?association)\b",
    re.I,
)
# A sentence that quotes the entry's category or tags refers back to it by those words too:
# "its tags", "the list's category".
LIST_REFERENCE = re.compile(WATCHLIST_REFERENCE.pattern + r"|\btag(?:s|ged)?\b|\bcategor(?:y|ies)\b", re.I)
# A headline or dek names the entry in passing, "listed under Jane Example" or "Jane
# Example-listed", and leaves the list's name to the paragraphs.
LISTED_FORM = re.compile(r"\b(?:listed|filed) under\b|\btagged\b|(?<=\w)-listed\b", re.I)
# The listed form stands for the list alone in the headline's house form; anywhere else it must
# refer to Plane Alert DB or the entry, and a sentence that names another source is not the list's.
LISTED_HEADING = re.compile(
    r", (?:(?:listed|filed) under|tagged) .+?, spotted\b|(?<=\w)-listed\b.*?\bspotted\b", re.I
)
OTHER_SOURCE = re.compile(
    r"\b(?:regist(?:ry|er|ration records?)|(?<!plane alert )database|faa records?|wikipedia"
    r"|adsbdb|hexdb|opensky|flightaware|flightradar\w*|ads-?b exchange|transport canada)\b",
    re.I,
)
# A verb that puts a watchlist detail in the list's mouth, as in "Plane Alert DB files N51EM under".
ATTRIBUTION = re.compile(
    r"\b(?:lists?|listed|listing|associates?|associated|association|names?|named|records?|recorded|links?"
    r"|linked|includes?|included|categori[sz]\w*|labels?|labell?ed|tags?|tagged|says|claims?|identifies"
    r"|identified|files?|filed|sits|carries|carried)\b",
    re.I,
)
# What a rarity, first or best is scoped to: this site's records, never the world.
SCOPE_WORDS = r"this collection|these (?:flight )?records|(?:the )?retained records"
RARE_SCOPE = re.compile(rf"\b(?:{SCOPE_WORDS})\b", re.I)
# A code's standard meaning may be stated as a definition, never as a confirmed event.
DEFINITION = re.compile(
    r"\b(?:means?|meaning|signals?|signall?ing|signifies|indicates?|indicating|denotes?|stands for|reserved for"
    r"|code for|used (?:for|to)|uses? (?:it )?(?:for|to)|to (?:declare|signal|indicate|report)"
    r"|emergency (?:status|code|squawk))\b",
    re.I,
)
ASSERTION = re.compile(
    r"\b(?:confirm\w*|prov(?:e|es|ed|en|ing)|shows?|showed|shown|had|has(?!\s+(?:long\s+)?been\s+(?:reserved"
    r"|used|assigned|designated|defined))|was in|were in|is in|genuine|real|really|actual(?:ly)?|declared"
    r"|experienc\w+|suffer\w*|faced|facing|occurr\w*|happen\w*|took place|taking place|indeed|in fact|exactly"
    r"|under ?way)\b",
    re.I,
)
_GENERAL_EMERGENCY = r"(?:general )?emergenc(?:y|ies)"
_RADIO_FAILURE = (
    r"radio(?: communications?)? failure|communications? failure|lost (?:radio|communications?)"
    r"|loss of (?:radio|communications?)"
)
# What each transmitted code or status means, by code and by status name.
CODE_MEANINGS = {
    **dict.fromkeys(("7700", "general"), _GENERAL_EMERGENCY),
    **dict.fromkeys(("7600", "nordo"), _RADIO_FAILURE),
    **dict.fromkeys(("7500", "unlawful"), r"unlawful interference"),
    "7400": r"lost link|loss of (?:the )?(?:control )?link",
    "lifeguard": r"lifeguard",
    "minfuel": r"minimum fuel",
    "downed": r"downed",
}
OWNER_CLAIM = re.compile(
    r"\b(?:owned|owns|owners?|operated by|operates|operators?|hers|theirs|sold|bought|purchased|acquired"
    r"|(?:belongs?|belonging) to(?!\s+(?:an?|the)\s+(?:aircraft|airframe|type|category|block|range|series"
    r"|family|registry|country)\b)"
    r"|whose (?:own |private )?(?:jet|plane|aircraft|helicopter|airplane|airliner)"
    r"|registered (?:owner|to)|property of|leased|leases|chartered|charters"
    r"|(?:his|her|their) (?:own |private |personal )?(?:jet|plane|aircraft"
    r"|helicopter|airplane|airliner|gulfstream|bizjet|business jet))\b",
    re.I,
)
POSSESSIVE = re.compile(
    r"\b(?P<name>(?-i:[A-Z][\w&.-]{0,40}(?:\s+[A-Z][\w&.-]{0,40}){0,3}))'s\s+(?:[\w-]+\s+)?"
    r"(?:jet|plane|aircraft|helicopter|airplane"
    r"|airliner|bizjet|chopper|gulfstream|learjet|citation|falcon|challenger|fleet|airframe|craft)\b",
    re.I,
)
# "To" before an altitude, a flight level, or a speed is no destination: "flew to 41,000 ft",
# "flew to FL410", "returned to 250 knots", "climbed to" and "descended to" alike. The figure
# checks still judge the figure.
TO_FIGURE = r"(?!(?<=\bto)\s+(?:" + SPEED_OR_ALTITUDE + r"|(?:FL\s?|flight level\s+)\d{2,3}\b))"
# A story that names an airport (track.airport) says only how close the track came to it: no
# landing, take-off, approach, or destination, hedged or not (story check claim:airport_use).
# Landing gear is part of the aircraft, not a landing.
AIRPORT_USE = re.compile(
    r"\b(?:land(?:s|ed)?|landings?(?![- ]gear)|touch(?:es|ed|ing)? down|took off|takes? off|take-?offs?"
    r"|taking off|departs?|departed|departing|departures?|arriv(?:e|es|ed|ing|als?)|return(?:s|ed|ing)? to"
    r"|bound for|inbound|outbound|approach(?:es|ed|ing)?(?: to)?|on final|en route to|divert(?:s|ed|ing)? to"
    r"|(?:flew|flown) to|head(?:s|ed|ing)? (?:to|for|towards?)|(?:flew|flying|fly|flies) towards?)\b"
    + TO_FIGURE,
    re.I,
)
CLAIMS = (
    (
        "occupant",
        re.compile(
            r"\b(?:aboard|on board|onboard|occupants?|crew members?|patients?|vips?|celebrit(?:y|ies)"
            r"|billionaires?|the president|vice president|air force one|marine one|travell?ers?"
            r"|travell?(?:ed|ing) (?:with|aboard)"
            r"|passengers?(?![ -](?:airliners?|jets?|aircraft|planes?|airplanes?|transports?|versions?|variants?"
            r"|configurations?|models?|types?|service|flights?|carriers?|airlines?|capacity|seats?|cabins?|doors?"
            r"|decks?))"
            r"|flew (?:him|her|them|it|the (?:jet|aircraft|plane|helicopter))|carr(?:ied|ying|ies) (?:the |a "
            r"|an )?(?:president|vip|celebrity|passengers?|troops|patients?|organs?|cargo|freight|people|guests?)"
            r"|(?:was|were|is|are) (?:on|in|inside) (?:it|(?:the|this|that|its|a) (?:jet|aircraft|plane|helicopter"
            r"|airplane|airliner|flight|cockpit|cabin))"
            r"|(?:was|were) flying (?:it|the (?:jet|aircraft|plane|helicopter|airplane))|at the controls|rode|riding"
            r"|governors?|senators?|ministers?|(?:kings?|queens?)(?! air)|princes?|princess\w*|ceos?|mayors?"
            r"|dignitar\w+|royals?|royalty)\b",
            re.I,
        ),
        (EPISTEMIC, OCCUPANT_DISCLAIMER),
    ),
    (
        "destination or landing",
        re.compile(
            r"\b(?:headed (?:to|for|toward|towards)|bound for|destinations?|en route (?:to|from)|on (?:its|their|the)"
            r" way (?:to|from)|flew to|flying to|flown to|arriv\w+ (?:at|in)"
            r"|return(?:ed|ing) to (?:the |its |a )?(?:airport|airfield|field|base|runway|land)"
            r"|depart\w+ (?:from|for)|took off|landed|landing at|touched down|diverted|diversion|divert\w*"
            r"|go-arounds?|missed approach|crash-?landed|on (?:final )?approach|final approach|touch[- ]and[- ]go\w*"
            r"|landings?(?![- ]gear)|(?:came|comes|coming|come) in to land)\b" + TO_FIGURE,
            re.I,
        ),
        (EPISTEMIC, POSSIBILITY, NEGATION),
    ),
    (
        "crew",
        re.compile(r"\b(?:pilots?|crews?|captain|flight crew|first officer|co-?pilots?|aircrew)\b", re.I),
        (EPISTEMIC, POSSIBILITY),
    ),
    (
        "air traffic control",
        re.compile(
            r"\b(?:air traffic control(?:lers?)?|atc|controllers?|cleared (?:to|for)|clearances?|vectored|instructed"
            r"|instructions? to|told to|ordered to|directed to|authori[sz]ed to|permission to)\b",
            re.I,
        ),
        (EPISTEMIC, POSSIBILITY, NEGATION),
    ),
    (
        "hijacking",
        re.compile(
            r"\b(?:hijack\w*|skyjack\w*|air piracy|pirat\w+|seiz(?:ed|ure|ing)|stolen|stole|steal\w*|theft"
            r"|armed (?:men|man|people|group|attackers?)|gunm[ae]n|weapons?|terror\w*|commandeer\w*|kidnap\w*"
            r"|hostages?|bomb threats?|hostile takeover)\b",
            re.I,
        ),
        (EPISTEMIC,),
    ),
    ("unlawful interference", re.compile(r"\bunlawful interference\b", re.I), (EPISTEMIC,)),
    (
        "emergency",
        re.compile(
            r"\b(?:emergenc(?:y|ies)|distress|mayday|pan-pan|in trouble|crash(?:ed|es|ing)?|lost control"
            r"|out of control|loss of control|plummet\w*|plung\w*|nose-?div\w*|stall(?:ed|ing|s)?"
            r"|engine (?:failure|fire|out|problems?|trouble)|on fire|smoke|fire|depressuri[sz]\w*|decompression"
            r"|cabin pressure|lost pressure|radio (?:failure|problems?|trouble)|communications? failure"
            r"|lost (?:radio|communications?|contact)|loss of (?:radio|communications?)|malfunction\w*"
            r"|(?:mechanical|hydraulic|electrical|instrument) (?:problems?|issues?|failures?|faults?)|failures?"
            r"|faults?|forced landing|ditch(?:ed|ing)|incidents?|accidents?|medical (?:emergency|issue|problem)"
            r"|minimum fuel|low (?:on )?fuel|fuel (?:emergency|shortage|exhaustion|starvation)|out of fuel"
            r"|lost link|loss of (?:the )?(?:control )?link|downed|near[- ]miss\w*|near[- ]collisions?|close calls?"
            r"|mid-?air collisions?|collided|flam(?:ed|es|ing) out|flame-?outs?|tanks? ran dry|ran dry"
            r"|(?:engines?|hydraulics?|electrics?|radios?|transponders?|instruments?|gear|systems?)"
            r" (?:quit|stopped|died|cut out|seized|failed))\b",
            re.I,
        ),
        (EPISTEMIC, STRONG_POSSIBILITY, NEGATION),
    ),
    (
        "mission",
        re.compile(
            r"\b(?:missions?|purposes?|surveil\w*|spy\w*|spied|reconnaissance|intelligence|patrol\w*"
            r"|search(?:ed|ing) for|search and rescue|rescu\w+|manhunt|pursuit|chas(?:e|ed|ing)"
            r"|track(?:ed|ing) (?:a|the|an) (?:suspect|person|vehicle|target|car|boat)"
            r"|police (?:operations?|activity|chase|search)|military (?:operations?|exercises?)|exercises?|drills?"
            r"|training (?:flights?|missions?|sorties?)|sorties?|deploy\w*|covert\w*|secret\w*|stealth|smuggl\w*"
            r"|drugs?|refuel(?:l)?(?:ing|ed)|intercept\w*|escort\w*|aerial photograph\w*|photographing"
            r"|photo (?:flights?|missions?)|filming|surveys?|surveying|mapping|crop[- ]dusting|aerial application"
            r"|skydiv\w*|parachut\w*|jump (?:flights?|runs?)|medevac|air ambulance|ufos?|aliens?|uaps?|jamm\w*"
            r"|spoof\w*|air ?shows?|flyovers?|flypasts?|fly-pasts?|sightseeing|charter\w*|protests?|demonstrators?"
            r"|protesters?|rall(?:y|ies)|crowds?|stadiums?|concerts?|funerals?|weddings?|parades?|ceremon(?:y|ies))\b",
            re.I,
        ),
        (EPISTEMIC, POSSIBILITY, NEGATION),
    ),
    (
        "capability",
        re.compile(
            r"\b(?:supersonic|transonic|hypersonic|sound barrier|speed of sound|faster than sound|record[- ]breaking"
            r"|world records?|top speed|maximum speed|redline|never[- ]exceed|tailwinds?|headwinds?|jet stream"
            r"|wind speed|winds? of)\b",
            re.I,
        ),
        (EPISTEMIC, POSSIBILITY, NEGATION),
    ),
    (
        "weather",
        re.compile(
            r"\b(?:weather|storms?|thunderstorms?|turbulence|icing|fog|crosswinds?|wind ?shear|snow\w*|rain\w*"
            r"|hail|lightning|gusts?|gusty|winds?\s+(?:pushed|carried|slowed|helped|blew|pushing|carrying)"
            r"|(?:strong|light|high|gusty) winds?|wind (?:speed|direction)s?)\b",
            re.I,
        ),
        (EPISTEMIC, POSSIBILITY, NEGATION),
    ),
    (
        "terrain clearance",
        re.compile(
            r"\b(?:above ground level|agl|height above (?:the )?(?:ground|terrain)|above the (?:ground|terrain"
            r"|houses|rooftops|roofs|treetops|trees)|terrain clearance|skimm\w+|buzz(?:ed|ing)|low pass)\b",
            re.I,
        ),
        (EPISTEMIC, POSSIBILITY, NEGATION),
    ),
    (
        "superlative",
        re.compile(
            r"\b(?:first[- ]ever|ever recorded|all[- ]time|world'?s (?:fastest|highest|largest|biggest|first)"
            r"|record[- ](?:high|low|breaking|setting)|unprecedented|never before|rarest|one of only|the only"
            r"|set (?:a|the|another|new) (?:\w+ )?records?|new records?|broke (?:a|the|another) (?:\w+ )?records?"
            r"|records? (?:broken|fell))\b",
            re.I,
        ),
        (EPISTEMIC, NEGATION),
    ),
    (
        "fleet status",
        re.compile(
            r"\b(?:fleets?|retir(?:e|es|ed|ing|ement)|grounded|grounding|mothball\w*|scrapp(?:ed|ing)"
            r"|phas(?:ed|ing) out|withdrawn from (?:service|use)|out of service|in storage|decommission\w*)\b",
            re.I,
        ),
        (EPISTEMIC, NEGATION),
    ),
    ("military", re.compile(r"\bmilitary\b", re.I), (EPISTEMIC, NEGATION)),
    (
        "safety",
        re.compile(
            r"\b(?:safe(?:ly|ty)?|unsafe|danger(?:s|ous|ously)?|hazard(?:s|ous)?|risk(?:s|y)?|uneventful\w*"
            r"|without (?:any )?(?:incident|problems?|issues?|trouble|difficulty)|went smoothly|under control"
            r"|(?:no|not any|never any|nothing)\s+(?:\w+\s+)?(?:signs?|indications?|evidence|hint|trace)\s+of"
            r"\s+(?:an?\s+|any\s+)?(?:emergenc\w+|trouble|problems?|distress|danger|difficult\w*|issues?|incidents?)"
            r"|nothing (?:unusual|wrong|amiss|abnormal|out of the ordinary)|all (?:was|is|seemed|appeared) (?:well"
            r"|fine|normal)|(?:no|nothing) (?:cause|reason) for (?:concern|alarm)|in good (?:shape|condition))\b",
            re.I,
        ),
        (SHOW_LIMIT,),
    ),
    (
        "visibility",
        re.compile(
            r"\b(?:contrails?|vapou?r trails?|condensation trails?|chemtrails?|visible|invisible|naked eye"
            r"|(?:could|can|to) be seen|(?:was|were|been) seen|seen from|sighted|eyewitness\w*|witness(?:es|ed)?"
            r"|onlookers?|spectators?|watched|photographed|filmed|in (?:plain |full )?(?:view|sight)"
            r"|binoculars|lit up|lights? (?:on|flashing|blinking))\b",
            re.I,
        ),
        (EPISTEMIC, POSSIBILITY, NEGATION),
    ),
)
# The mission row, which also reads a category word as a purpose (_purpose_word).
MISSION_CLAIM = next(pattern for name, pattern, _ in CLAIMS if name == "mission")
# Words no public story may offer even as a possibility: a purpose or occupant the data cannot
# show, or the near-miss framing a TCAS story's own limitation denies.
SPECULATION = re.compile(
    r"\b(?:missions?|sorties?|charter\w*|ferr(?:y|ies|ied|ying)|training|trainees?|medevac\w*|delegations?"
    r"|spy|spies|spying|spied|surveil\w*|collid\w*|collisions?(?![- ]avoidance)|vector(?:s|ed|ing)?"
    # A separation figure is a measurement; only its loss or breach is a near-miss claim.
    r"|(?:loss|lost|losing|lose|loses|inadequate|insufficient|eroded)\s+(?:of\s+)?"
    r"(?:(?:horizontal|vertical|lateral|safe)\s+)?separation"
    r"|separation\s+(?:[\w-]+\s+){0,4}?(?:lost|loss|eroded|compromised|broke\s+down|breached|violated)"
    r"|separation\s+(?:violations?|infringements?|breach\w*|incidents?))\b",
    re.I,
)
# A watchlist entry never says who is aboard or associated, hedged or not.
PERSON_ASSOCIATION = re.compile(
    r"\b(?:public figures?|well[- ]known (?:people|persons?|figures?|individuals?|names?|personalit\w+)"
    r"|famous (?:people|persons?|figures?|individuals?|names?)|celebrit(?:y|ies)|politicians?"
    r"|political (?:figures?|leaders?|parties|party)|royals?|royalty|royal famil(?:y|ies)"
    r"|executives?(?![ -](?:jets?|aircraft|transports?|aviation|airlines?))|business (?:figures?|leaders?"
    r"|people|persons?|magnates?)|billionaires?|millionaires?|tycoons?|moguls?|vips?|dignitar(?:y|ies)"
    r"|heads? of state|statesm[ae]n|lawmakers?|personalit(?:y|ies)|influencers?|athletes?|musicians?|actors?"
    r"|actress\w*|singers?|(?:co-?)?founders?|entrepreneurs?|investors?|financiers?|industrialists?"
    r"|philanthropists?|business(?:m[ae]n|wom[ae]n|pe(?:rsons?|ople))|chair(?:m[ae]n|wom[ae]n|persons?)"
    r"|chief executives?|heirs?|heiress(?:es)?|socialites?|wealthy\s+[\w-]+)\b",
    re.I,
)
# Text that reads as an instruction to the writer rather than a fact about a type.
INSTRUCTION = re.compile(
    r"^\W*(?:please\s+)?(?:ignore|disregard|forget|say|write|tell|claim|describe|mention|include|add|remove"
    r"|output|print|return|respond|reply|answer|pretend|act|treat|consider|assume|repeat|insert|append|translate"
    r"|summari[sz]e|list|make|do not|don't|never|always|note|remember|imagine)\b"
    r"|\b(?:you|your|yours|yourself|ignor\w*|disregard\w*|instructions?|prompts?|system message|assistant"
    r"|chatbot|language model|ai model|jailbreak\w*)\b"
    r"|\b(?:previous|earlier|prior|above|following|these|those|all) (?:rules|instructions|directions|guidance"
    r"|guidelines)\b",
    re.I,
)
LEAD_HEDGE = re.compile(
    r"\s*(?:hypothes[ie]s|possibility|possible explanation|alternative(?: explanation)?|one explanation"
    r"|another explanation|speculation)\s*(?:\d+\s*)?:",
    re.I,
)
# Rarity and normality: "rare", "few ... ever", "common", "routine", "ordinary". A claim about how
# often a type flies, or that a flight was normal, needs a scope or a statement of what the data cannot show.
RARITY = re.compile(
    r"\b(?:rare(?:ly|r)?|rarit(?:y|ies)|uncommon(?:ly)?|unusual(?:ly)?|seldom|scarce(?:ly)?|infrequent(?:ly)?"
    r"|hardly ever|few\s+(?:[\w-]+\s+){0,3}ever|one of (?:the )?(?:few|only)|common(?:ly|est|er)?"
    r"|ordinar(?:y|ily)|normal(?:ly)?|(?<!non-)routine(?:ly)?|everyday|unremarkabl[ey]"
    # "No A380 has ever gone faster", "Few of these jets still fly", "a handful survive".
    r"|no\s+(?:[\w-]+\s+){1,3}(?:has|have|had)\s+ever|(?:only )?(?:a )?handful"
    r"|few\s+(?:of\s+)?(?:[\w-]+\s+){0,3}(?:still\s+)?(?:fly|flies|flying|remain|remains|survive|survives"
    r"|exist|exists|are left|left))\b",
    re.I,
)
# A subordinate clause asserts on its own: "who was aboard when it crashed".
SUBORDINATE = re.compile(
    r"\b(?:when|whenever|after|before|because|since|once|until|why|during|amid|amidst)\b"
    r"(?=\s+(?!or\b|and\b|not\b)[A-Za-z0-9])",
    re.I,
)
# A later comma clause with its own subject and verb is not covered by an earlier hedge.
INDEPENDENT = re.compile(
    r"\s*(?:(?:and|or|so|then)\s+)?(?:it|this|that|they|he|she|we|there|the\s+[\w-]+|its\s+[\w-]+"
    r"|their\s+[\w-]+|a\s+[\w-]+|an\s+[\w-]+)\s+(?:[\w-]+\s+){0,2}?(?:is|was|were|are|has|had|did|does"
    r"|will|would|can|could|may|might|must|declared|reported|made|became|went|flew|carried|suffered"
    r"|experienced|lost|crashed)\b",
    re.I,
)
DEFINITE = re.compile(r"\b(?:the|its|their|his|her|this|that|these|those|both|two|three)\s+$", re.I)
HABITUAL = re.compile(
    r"\b(?:can|could|(?-i:may)|might|often|typically|usually|commonly|sometimes|generally|routinely|normally"
    r"|tends?|uses?|used to|would)\b",
    re.I,
)
NO_STATUS = re.compile(
    r"emergency status(?: field)?\s+(?:was|is|read|reported|showed)?\s*(?:none|not set|empty|absent|unset)\b",
    re.I,
)
INTENSIFIERS = re.compile(
    r"\b(?:no doubt|no question|without (?:a |any )?doubt|without question|no denying|not surprisingly|no wonder"
    r"|undoubtedly|doubtless)\b",
    re.I,
)
# A relative clause, closed by a comma or by the end of its sentence, hedges only itself:
# "..., which is unknown." does not hedge the claim before it.
RELATIVE = re.compile(r",\s+(?:who|which|whose)\b[^,.;:!?]*(?:,|(?=[.;:!?])|$)")
PERSONAL = re.compile(r"\b(?:she|her|hers|herself|he|him|his|himself)\b", re.I)
GENERIC_ROLES = re.compile(r"(?:pilots|crews|co-?pilots|controllers|air traffic controllers)", re.I)
AIRSPEED = re.compile(
    r"\b(?:true|indicated|calibrated)\s+(?:air ?)?speed\b|\b(?:knots|kts|kt|kn)\s+(?:indicated|true|calibrated)\b"
    r"|\bair ?speed\s+(?:of|was|is|reached|peaked|hit|near|around"
    r"|about|at|stood)\b|\b(?:its|their|the aircraft's|the jet's|the helicopter's)\s+air ?speed\b",
    re.I,
)
# A type's certified limit, stated from a cited rated-limits fact: "never-exceed speed of
# 223 knots" is the type's figure, not this flight's airspeed or a capability claim.
RATED_LIMIT = re.compile(
    r"\b(?:never[- ]exceed|maximum operating|certified|certification|rated|v(?:mo|ne)|mmo)\b", re.I
)
RATED_FIELDS = {"max_operating_altitude_ft", "vmo", "vne", "mmo"}
# Only the certified figure's own words lift the airspeed and capability checks, never any
# clause that happens to say "certified" or "rated".
CERTIFIED_FIGURE = re.compile(
    r"\b(?:maximum operating|never[- ]exceed) (?:air ?)?speed of \d[\d,]*(?:\.\d+)?\s*(?:knots|kts|kt|kn)\b",
    re.I,
)
# A ground speed is no airspeed, so a story whose reading is one never sets the type's
# certified speed beside it: no certified speed figure or name in a sentence with a ground
# speed or a comparison (claim:limit_vs_ground_speed). The figure may stand alone, named as
# the airspeed limit it is: "The Cessna 172 has a never-exceed airspeed of 163 knots."
SPEED_PROSE_KINDS = kinds.where(speed_prose=True)
SPEED_LIMIT_NAMES = re.compile(
    r"\b(?:maximum (?:operating )?(?:air ?)?speed|top speed|v(?:mo|ne)|never[- ]exceed|speed limits?"
    r"|(?:certified|rated) (?:maximum |top )?(?:air ?)?speed|redline)\b",
    re.I,
)
SPEED_COMPARISON = re.compile(
    r"\b(?:past|over|above|beyond|(?<!never[- ])exceed\w*|faster than|quicker than|more than|higher than"
    r"|surpass\w*|outpac\w*|outr[au]n|broke|beat|topped|below|under|less than|lower than|slower than"
    r"|compared?|versus|vs)\b|\bground ?speeds?\b"
    r"|\b(?:the|that|this|its) (?:readings?|reports?|spikes?|values?|figures?|outliers?)\b",
    re.I,
)
SPEED_LIMIT_FIELDS = frozenset({"vmo", "vne"})
NEVER_EXCEED = re.compile(r"never[- ]exceed", re.I)
# Fields that carry an airspeed: the aircraft's own reports, and a rated story's estimate from
# ground speed and a modelled wind (story_facts lim.airspeed). A ground speed is none of them.
AIRSPEED_FIELDS = frozenset({"tas", "ias", "estimated_airspeed", "equivalent_airspeed"})
SEGMENT_BREAK = re.compile(
    r"[;()\[\]\u2014]|\s[\u2013-]\s|:(?=\s)|\b(?:but|although|though|whereas|yet|while|however|except)\b"
    r"|\band\b(?=\s+(?:it|this|that|there|they|he|she|we|i|its|their|his|her)\b)"
    r"|\band\b(?=\s+(?:the|a|an)\s+[\w-]+\s+(?:is|was|are|were|has|had|did|does|do|may|might|can|could|will"
    r"|would|cannot|remains?)\b)"
    r"|,\s+(?:and|so)\b",
    re.I,
)
CLAUSE_COMMA = re.compile(r",(?=\s|$)(?!\s+(?:which|who|whom|whose)\b)")
FORMAT_CHARACTERS = re.compile(
    r"[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff]"
)
PUNCTUATION = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2212": "-",
    "\u2010": "-",
    "\u2011": "-",
    "\u00a0": " ",
    "\u202f": " ",
    "\u2009": " ",
    "\u2007": " ",
    "\u00d7": "x",
    "\u2026": "...",
}
EXTRA_CHARACTERS = {"\u2013", "\u2014", "\u00b0", "\n"}


VULGAR_FRACTIONS = {"\u00bd": ".5", "\u00bc": ".25", "\u00be": ".75"}
CAVEAT_KEY = re.compile(
    r"(?:^|_)(?:caveat|limitations|context_notes|count_meaning|state_meaning|claim_scope|attribution|rarity_scope"
    r"|metrics_scope|meaning)$"
)
# Keys whose text names this record's own aircraft, as written.
AIRFRAME_KEYS = {"registration", "callsign", "icao_address", "hex", "r", "flight"}
# Keys whose text is a proper name a headline keeps as written.
PHRASE_KEYS = {"label", "type_description", "desc", "neutral_category", "category", "registry_type"}
WINDOW_PAIRS = (
    ("start_utc", "end_utc"),
    ("started_at", "ended_at"),
    ("first_seen_utc", "last_seen_utc"),
    ("observed_start", "observed_end"),
)
PEAK_KEY = re.compile(r"(?:^|_)(?:peak|observed_at|saved_readings_at)")
SAMPLE_TIME_KEYS = ("ts", "timestamp", "at_utc", "time")
# Readings whose time a sentence may state: a stated time must be the reading's own.
TIMED_DIMENSIONS = {"speed", "altitude", "vrate"}
RANGE_START = re.compile(
    r"(?<![\w.])(?P<n>" + NUMBER + r")\s*(?:,\s*(?:and\s+|or\s+)?|-|\u2013|to|and|or|through)\s*$", re.I
)
CONTEXT = 240
RANGE_LEVEL = re.compile(
    r"\b(?:between|from)\s+(?:about\s+|around\s+|roughly\s+)?" + NUMBER + r"\s*(?:ft|feet|m|metres|meters)?"
    r"\s*(?:and|to|-|\u2013)\s*$",
    re.I,
)
FIELD_NOUNS = re.compile(
    r"\b(?:(?P<speed>ground ?speeds?|groundspeeds?|speeds?|gs)|(?P<altitude>altitudes?|archive_alt|alt_baro|alt_geom)"
    r"|(?P<vrate>(?:vertical|climb|descent|sink) rates?|baro_rate|geom_rate))\b",
    re.I,
)
# A measurement named by its field: the number must then come from that field. "Ground
# speed" takes any speed but an airspeed; "pressure altitude" any altitude whose reference
# is pressure, never a geometric or archived one.
NAMED_FIELDS = re.compile(
    r"\b(?:(?P<certified>maximum operating (?:altitude|(?:air ?)?speed|mach(?: number)?)|never[- ]exceed (?:air ?)?speed"
    r"|certified (?:ceiling|maximum|limit|figure)|vmo|vne|mmo)"
    r"|(?P<estimated_airspeed>estimated (?:true )?air ?speeds?)"
    r"|(?P<tas>true air ?speeds?|tas)|(?P<ias>indicated air ?speeds?|ias)"
    r"|(?P<gs>ground ?speeds?|groundspeeds?|over the ground)"
    r"|(?P<alt_geom>(?:geometric|gps|gnss|satellite) altitudes?|altitudes? by (?:satellite|gps|gnss))"
    r"|(?P<alt_baro>(?:pressure|barometric|baro) altitudes?|altitudes? by pressure)"
    r"|(?P<airspeed>air ?speeds?))\b",
    re.I,
)
# The same names after the number: "500 ft by pressure", "487 knots over the ground".
FIELD_AFTER = re.compile(
    r"\s+(?:(?P<gs>over the ground)|(?:by|from)\s+(?:(?P<alt_baro>pressure|barometer|barometric)"
    r"|(?P<alt_geom>satellite|gps|gnss)))\b",
    re.I,
)
# The dimensions each name governs: a certified figure is a ceiling, a speed, or a Mach number.
NAMED_DIMENSIONS = {
    "certified": ("altitude", "speed", "mach"),
    "airspeed": ("speed",),
    "estimated_airspeed": ("speed",),
    "tas": ("speed",),
    "ias": ("speed",),
    "gs": ("speed",),
    "alt_geom": ("altitude",),
    "alt_baro": ("altitude",),
}
BUILT = re.compile(
    r"\b(?:built|manufactur\w*|made|produced|delivered|registered|introduced|entered service|first flew"
    r"|rolled out|constructed|completed)\b",
    re.I,
)
VERBS = {
    "appears",
    "seems",
    "flies",
    "climbs",
    "descends",
    "turns",
    "shows",
    "was",
    "is",
    "has",
    "does",
    "remains",
    "reports",
    "transmits",
    "broadcasts",
    "lists",
    "matches",
}
DETERMINER = re.compile(
    r"\b(?:a|an|the|this|that|these|those|its|their|each|every|one|another|first|last|later|earlier|preceding"
    r"|following|previous|next|single|same|final|initial|subsequent|separate|squawk|squawked|code|transponder"
    r"|registration|serial|listed|database)\s+$",
    re.I,
)
YEAR_OR_CODE = re.compile(
    r"\b(?:years?|built|manufactur\w*|database|listed|registered|squawk\w*|codes?|transponder|status)\b", re.I
)
MEASURE_NOUNS = re.compile(
    r"(?:readings?|values?|figures?|numbers?|reports?|samples?|measurements?|fields?|rates?|altitudes?|speeds?"
    r"|timestamps?|codes?|records?|sources?)",
    re.I,
)
# "A second aircraft" claims another aircraft; the packet must list one.
ANOTHER_AIRCRAFT = re.compile(
    r"\b(?:(?:a|another|the)\s+(?P<n>second|third|fourth|fifth)|another|both)\s+(?:aircraft|jets?|planes?"
    r"|helicopters?|airplanes?|targets?|airliners?)\b",
    re.I,
)
OUT_OF = re.compile(r"\s+(?:of|out of)\s+" + NUMBER + r"\b", re.I)
# A reading held across a whole span: "for all 17 minutes". No fact shows a reading that steady.
SPAN_CLAIM = re.compile(
    r"\b(?:all|the\s+(?:whole|entire|full))\s+(?:of\s+(?:the|its|those|these)\s+)?(?:(?:"
    + NUMBER
    + "|"
    + SMALL_WORDS
    + r")[\s-]*)?(?:seconds?|minutes?|hours?|days?)\b"
    r"|\bthroughout\s+(?:the|its|those|these)\s+(?:(?:" + NUMBER + "|" + SMALL_WORDS + r")[\s-]*)?"
    r"(?:seconds?|minutes?|hours?)\b"
    r"|\b(?:" + NUMBER + "|" + SMALL_WORDS + r")[\s-]*(?:seconds?|minutes?|hours?)\s+(?:straight|on end"
    r"|without a break|non-?stop)\b",
    re.I,
)
# A span claim is about a held reading when its sentence keeps a value or states one.
HELD_READING = re.compile(
    r"\b(?:held|holds?|holding|stayed|stays?|kept|keeps?|maintain\w*|remain\w*|cruis\w*|steady|level)\b"
    r"|\d\s*(?:ft|feet|foot|knots?|kn|kts?|mph|ft/min)\b",
    re.I,
)
# A rate per unit of time or distance; only the rates in PROSE_UNITS are facts.
RATE_PER = re.compile(
    r"\b(?=[A-Za-z]{2})(?:"
    + COUNT_PATTERN
    + "|"
    + UNIT_PATTERN
    + r")\s+(?:per|an?|each|every)\s+(?:second|minute|hour|day"
    r"|week|month|year|mile|nautical mile|nm|kilomet(?:re|er)|km)s?\b",
    re.I,
)
ABBREVIATION = re.compile(
    r"(?<![^\s(])(?:[A-Za-z]|U\.S|e\.g|i\.e|a\.m|p\.m|Mr|Mrs|Ms|Dr|St|Jr|Sr|Mt|No|vs|etc|approx|Inc|Ltd|Co"
    r"|Corp)\.$"
)
TYPE_NOUN = re.compile(
    r"aircraft|airplane|aeroplane|helicopter|jet|airliner|tanker|transport|freighter|trainer|glider|bomber|fighter"
    r"|airship|balloon|gyroplane|gyrocopter|seaplane|amphibian|biplane|monoplane|turboprop|airlifter",
    re.I,
)
# A type fact describes the type, never what its flights are for: "Every E-6B flight is a nuclear
# readiness drill" is a mission claim about each airframe a story names. A design role is a
# description ("a maritime patrol aircraft", "designed for combat communications"): a purpose word
# counts only up to two words after a flight word, after "on", or as what the type "is". A design
# role (DESIGN_ROLE) and a type name that carries one of the words ("Combat Sent") are blanked
# first.
# What a flight is for ("flew its first mission", "flies long sorties").
_PURPOSE = r"(?:drills?|missions?|sorties?|patrols?|exercises?|deployments?|surveillance|combat|strikes?)"
# What the type is out on ("on patrol", "on alert"), which is no flight word's object.
_ON_PURPOSE = r"(?:patrols?|missions?|sorties?|deployments?|alert)"
# What the type or its flights are ("is a readiness exercise"); a role noun such as "combat" is
# no such thing.
_IS_PURPOSE = r"(?:drills?|missions?|sorties?|exercises?|deployments?)"
PROFILE_MISSION = re.compile(
    r"\b(?:flights?|flew|fl(?:y|ies|ying)|sorties?)(?:\s+[\w'-]+){0,2}?\s+"
    + _PURPOSE
    + r"\b"
    + r"|\bon\s+(?:an?\s+|the\s+)?(?:[\w-]+\s+)?"
    + _ON_PURPOSE
    + r"\b"
    # Deployed on alert or for a purpose; deployed to a place is a move.
    + r"|\bdeployed\s+(?:on|for|to\s+(?:patrol|drill|exercise|strike|fight))\b"
    + r"|\b(?:is|are|was|were)\s+(?:an?\s+|the\s+)?(?:[\w-]+\s+){0,3}?"
    + _IS_PURPOSE
    + r"\b",
    re.I,
)
# A sentence about this airframe rather than its type: a figure from a type profile cannot support it.
AIRFRAME_REFERENCE = re.compile(
    r"\b(?:this|that|the same)\s+(?:[\w-]+\s+)?(?:jet|aircraft|airframe|plane|airplane|airliner|helicopter"
    r"|example|flight)\b|^\W*(?:it|its|it's|they|their)\b",
    re.I,
)
# A figure in a type profile: a dated month, a number or a spelled number.
FIGURE = re.compile(
    r"(?:\b\d{1,2}(?:st|nd|rd|th)?\s+)?\b" + MONTH + r"(?:\s+\d{1,2}(?:st|nd|rd|th)?,?)?\s+\d{4}\b"
    r"|(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\w|\.\d|,\d)"
    r"|(?i:\b(?:" + NUMBER_WORDS + r")(?:[\s-]+(?:" + NUMBER_WORDS + r"))*\b)"
)
HERE = re.compile(r"\bhere\b", re.I)
# "Here" means this journal only beside a story: "its fourth story about HL7635 here". After a
# preposition or a verb of motion or sight ("passed near here") it is a place, story or not.
SITE_WORDS = r"stor(?:y|ies)|journal"
JOURNAL_SCOPE = re.compile(rf"\b(?:{SITE_WORDS})\b", re.I)
PLACE_HERE = re.compile(
    r"\b(?:near|over|from|around|above|below|by|past|through|to|toward|towards|into|overhead|nearby|close to"
    r"|pass(?:ed|es|ing)?|fl(?:ew|ies|y|ying)|c(?:ame|omes?|oming)|seen|visible|spotted|heard|land(?:ed|s|ing)?"
    r"|arriv(?:ed|es|ing)|went|head(?:ed|ing)|circl(?:ed|es|ing)|cross(?:ed|es|ing))\s+$",
    re.I,
)
# Words a hedged clause may use without a packet fact: they explain or describe, they claim nothing new.
SPECULATION_GENERIC = frozenset(
    """explain explains explained explaining explanation explanations reason reasons cause causes caused
    result results resulted reflect reflects reflected reflecting account accounts mean means meant point
    points pointed come comes came coming make makes made give gives gave happen happens happened occur occurs
    occurred show shows showed shown source sources produce produces produced lead leads been being have
    having value values reading readings report reports reported record records recorded data figure figures
    number numbers entry entries field fields sample samples message messages error errors glitch glitches
    problem problems issue issues mistake mistakes single lone one kind sort something thing things case part
    like look looks looked seem seems seemed instead rather other another either more most less least much
    many some such simple simply also still just only even both each every same different similar flight
    flights flying flew fly flies aircraft airframe plane jet track tracks path position positions altitude
    altitudes speed speeds ground rate rates time times moment minute minutes second seconds hour hours day
    days period window stretch brief briefly short long close closely near nearly far high higher low lower
    fast faster slow slower steep steady level spacing gap gaps mismatch mismatches difference differences
    discrepancy change changes changed jump jumps spike spikes outlier outliers pattern patterns shape shapes
    work working real actual elsewhere there then type types metadata database neighbors neighbours around
    apart away stands stand rest knots knot feet foot mph miles mile kilometres kilometers""".split()
)
# Hedges that only offer a possibility; in a public story they need a listed hypothesis.
LOOSE_HEDGES = (POSSIBILITY, STRONG_POSSIBILITY)
# Words that offer an explanation as a possibility: what follows them must come from the packet.
SPECULATIVE = re.compile(
    r"\b(?:" + MODAL_HEDGES + "|" + LIKENESS_HEDGES + r"|likely|probably|perhaps|suggests?|suggesting"
    r"|(?:appears?|appeared|seems?|seemed) to)\b",
    re.I,
)
# A stated cause of a reading: "was a decoding error", "caused by", "due to". In a public story
# the cause must be a listed hypothesis, hedged or not; a story that lists none states no cause.
# A marker is a linking phrase, or a copula and its qualifiers before the error it names.
CAUSE_LINKS = (
    r"caused by|due to|because of|(?:the|a) result of|results? from|resulted from|stems? from"
    r"|stemmed from|explained by|attributable to|owing to|traced to|blamed on|put down to"
)
CAUSE_COPULA = (
    r"(?:was|were|is|are|be|been)\s+(?:(?:an?|the|its|their|one|another|just|simply|probably|likely"
    r"|most likely|almost certainly|clearly|plainly)\s+)*"
)
CAUSE = re.compile(
    r"\b(?:" + CAUSE_LINKS + r"|" + CAUSE_COPULA + r"(?:[\w-]+\s+){0,2}?"
    r"(?:errors?|glitch(?:es)?|faults?|artifacts?|artefacts?|bugs?|problems?|malfunctions?|mistakes?"
    r"|corruption|misreads?|reflections?|interference|flukes?|(?:corrupted|faulty|bad|erroneous|spurious|false"
    r"|bogus|garbled|wrong)\s+(?:data|readings?|values?|reports?|metadata|figures?)))\b",
    re.I,
)
READING_NOUNS = re.compile(
    r"\b(?:readings?|reports?|values?|figures?|spikes?|jumps?|outliers?|speeds?|altitudes?|rates?"
    r"|mismatch(?:es)?|differences?|gaps?|drops?|glitch(?:es)?|data|fields?|numbers?)\b",
    re.I,
)
# The words of a cause marker itself, which name no cause: every word its patterns spell out.
CAUSE_MARKER_WORDS = frozenset(re.findall(r"[a-z]{2,}", CAUSE_LINKS + CAUSE_COPULA))
# Kinds whose story names no cause at all: an outlying reading, a reading that does not fit the
# listed type, and a reading past the type's certified limit. A cause noun anywhere, or a causal
# verb in a sentence about a reading, is refused, hedged or not. A mismatch story may state a
# second registry's type as a fact, and offer one of its listed hypotheses, hedged, none of which
# names a cause noun.
CAUSELESS_KINDS = kinds.where(causeless=True)
CAUSELESS_PREFIXES = ("outlier_",)
CAUSE_NOUNS = re.compile(
    r"\b(?:decod\w*|corrupt\w*|errors?|erroneous|glitch\w*|fault\w*|multipath|interference|misread\w*"
    r"|bad (?:data|readings?|reports?|values?)|garbled)\b",
    re.I,
)
# Which source is right is a cause too: a causeless story judges neither the database nor a
# registry. The words count only in a sentence that names a source ("It turned right" is no verdict).
SOURCE_VERDICT = re.compile(r"\b(?:wrong|right|correct|incorrect|mistaken|in error)\b", re.I)
NAMED_SOURCE = re.compile(
    r"\b(?:databases?|registr(?:y|ies)|metadata|" + "|".join(sorted(enrichment_rules.REGISTRIES)) + r")\b",
    re.I,
)
CAUSAL_VERBS = re.compile(
    r"\b(?:caus(?:e|es|ed|ing)|produc(?:e|es|ed|ing)|c(?:ame|omes?|oming) from|explain(?:s|ed|ing)?"
    r"|reflect(?:s|ed|ing)?|trigger(?:s|ed|ing)?|due to|because of|results? of|resulted from|results? from)\b",
    re.I,
)
# A sentence about the reading: it names one, or refers back to it.
READING_SUBJECT = re.compile(READING_NOUNS.pattern + r"|\b(?:it|this|that)\b", re.I)
# Where a value came from is a fact of the record, not a cause: "the figure comes from a
# multilateration solution", "its type comes from the aircraft database".
PROVENANCE = re.compile(r"\b(?:multilateration|mlat|database|icao)\b", re.I)
CHECK_ERRORS = (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError)


def _clean(text):
    """Plain fractions, NFKC, no hidden format characters and plain punctuation."""
    text = re.sub(
        "(\\d?)([\u00bd\u00bc\u00be])",
        lambda match: (match[1] or "0") + VULGAR_FRACTIONS[match[2]],
        text,
    )
    text = FORMAT_CHARACTERS.sub("", unicodedata.normalize("NFKC", text))
    return "".join(PUNCTUATION.get(character, character) for character in text)


def _number(text):
    return float(text.replace(",", ""))


def series(items, conjunction="and"):
    """Items joined as prose with the serial comma: "A", "A and B", "A, B, and C".

    Every list the site writes goes through here, so a template and a table never
    disagree about the comma before the conjunction.
    """
    items = [str(item) for item in items if item not in (None, "")]
    if len(items) <= 2:
        return f" {conjunction} ".join(items)
    return ", ".join(items[:-1]) + f", {conjunction} " + items[-1]


def _place(text):
    """The rounding step that a written number states."""
    digits = text.replace(",", "")
    if "." in digits:
        return 10.0 ** -len(digits.split(".", 1)[1])
    stripped = digits.lstrip("0") or "0"
    if stripped == "0":
        return 1.0
    return 10.0 ** (len(stripped) - len(stripped.rstrip("0")))


def finite(value):
    """A real, finite number that is not a bool; editorial and story_facts share this test."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def number(value):
    """The value when finite(), else None."""
    return value if finite(value) else None


def moment(value):
    """A UTC datetime from an ISO date or time (naive means UTC) or epoch seconds, or None.

    The soft parser story_facts and editorial share; it never raises.
    """
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ]\S+)?", value):
            return None
        try:
            parsed = datetime.fromisoformat(value)
            # Converting an offset at the calendar's edge ("9999-12-31T23:59:59-05:00") overflows.
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except (ValueError, OverflowError):
            return None
    if finite(value) and 1e9 <= value <= 1e10:
        return datetime.fromtimestamp(value, timezone.utc)
    return None


def _normal(token):
    return re.sub(r"[-/. ]", "", token).upper()


def _joins(token):
    """A token and every run of its parts, normalized: A-380-800 gives A380 and 747-400LCF gives 747400."""
    parts = [part for part in re.split(r"[-/.]", token) if part]
    # A model suffix after digits, as in 400LCF, also splits into its runs.
    runs = [
        run
        for part in parts
        for run in (re.findall(r"\d+|[A-Za-z]+", part) if re.fullmatch(r"\d+[A-Za-z]+", part) else [part])
    ]
    found = {_normal(token)}
    for sequence in (parts, runs):
        for first in range(len(sequence)):
            for last in range(first + 1, len(sequence) + 1):
                found.add(_normal("".join(sequence[first:last])))
    return found


def _units(unit):
    for pattern, conversions in PROSE_UNITS:
        if re.fullmatch(pattern, unit, re.I):
            return conversions
    for name, nouns in COUNT_NOUNS.items():
        if re.fullmatch(nouns, unit, re.I):
            return [("count:" + name, 1)]
    return []


def _content_words(text):
    return {word for word in re.findall(r"[a-z]{4,}", text.lower()) if word not in STOPWORDS}


def _words(text):
    """Lowercase words and their hyphen or apostrophe parts."""
    return {
        part
        for word in WORD.findall(text)
        for part in (word.lower().strip("'-"), *re.split(r"[-']", word.lower()))
        if part
    }


def _longest_first(values):
    return sorted(values, key=lambda value: (-len(value), value))


def _blank(text, pattern):
    """Each match of a pattern replaced by as many x's, so positions and sentence ends stay put."""
    regex = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, re.I)
    return regex.sub(lambda match: "x" * len(match.group()), text)


def _blank_phrase(text, phrase, *, bounded=False):
    """A phrase blanked wherever it appears; bounded, only as whole words (plurals included)."""
    body = re.escape(phrase)
    return _blank(text, r"(?<![\w-])" + body + r"(?:e?s)?(?![\w-])" if bounded else body)


def _phrase_words(text):
    """A phrase's words as a pattern, any spacing between them; the final full stop left out."""
    return r"\s+".join(re.escape(word) for word in text.rstrip(".").split())


# The site's fixed phrases whose words read as claims one by one: ADS-B's "automatic dependent
# surveillance" is no surveillance. The claim and profile mission checks blank them first.
SITE_PHRASES = ("automatic dependent surveillance",)
# Purposes a type is designed for. A use one airframe is put to ("a charter aircraft", "a medevac
# helicopter") or a wrongdoing ("a smuggling aircraft") is never one.
_DESIGN_PURPOSE = (
    r"surveillance|reconnaissance|intelligence|patrols?|search and rescue|rescue|training|combat|strikes?"
    r"|missions?|purpose|spy|stealth|interceptor|escort|(?:aerial\s+)?refuel(?:l)?ing|jamming"
    r"|aerial application|crop[- ]dusting|drills?|exercises?|deployments?|sorties?"
)
# Nouns a design purpose names a role with ("a surveillance aircraft", "the training version",
# "combat service"), and the type nouns ("a patrol bomber") and class nouns ("the reconnaissance
# family", "type", "series", "fleet") it may name one with only when the type's own facts carry
# the purpose.
_ROLE_NOUN = r"aircraft|airplane|platform|variant|version|role|service"
_SUPPORTED_ROLE_NOUN = TYPE_NOUN.pattern + r"|type|family|series|fleet"
_ANY_ROLE_NOUN = r"(?:" + _ROLE_NOUN + "|" + _SUPPORTED_ROLE_NOUN + r")(?:e?s)?\b"
# The site's phrases; a design purpose after "designed for" ("designed for maritime patrol"); and
# a design purpose before a role noun, with at most one word between ("a signals intelligence
# variant", "a surveillance and attack aircraft"), or before a hyphen compound, with the role noun
# after it ("a surveillance-equipped aircraft") where there is one.
DESIGN_ROLE = re.compile(
    r"(?P<site>" + "|".join(re.escape(phrase) for phrase in SITE_PHRASES) + r")"
    r"|\b(?:designed|built|developed|intended)\s+for\s+(?:[\w-]+\s+){0,2}?(?P<designed>"
    + _DESIGN_PURPOSE
    + r")\b"
    r"|\b(?P<purpose>"
    + _DESIGN_PURPOSE
    + r")(?=(?P<hyphen>-[\w-]+(?:\s+(?P<compound>"
    + _ANY_ROLE_NOUN
    + r"))?)|\s+(?:[\w-]+\s+)?(?P<noun>"
    + _ANY_ROLE_NOUN
    + r"))",
    re.I,
)
_BASE_ROLE_NOUN = re.compile(r"(?:" + _ROLE_NOUN + r")(?:e?s)?", re.I)
# A type phrase that names a design role ("reconnaissance aircraft", "maritime patrol aircraft"):
# claim_issues leaves it to _blank_roles rather than blanking it as a name, so its frames and
# anchors are read too.
_ROLE_PHRASE = re.compile(
    r"\b(?:" + _DESIGN_PURPOSE + r")(?:-[\w-]+)?\s+(?:[\w-]+\s+)?" + _ANY_ROLE_NOUN, re.I
)
# A frame that assigns a role to one flight or airframe, whatever its tense: "working as a",
# "serves as the" (PROFILE_ROLE_ASSIGNMENT). Story prose reads any "as a", "as an", "as the", or
# "like a" so, opening the text or not ("circled the harbor as a", "used as a", "acting like a"),
# except "such as", and "was the" or "were the" (ROLE_ASSIGNMENT); a type description uses those
# of the type ("is used as a reconnaissance platform by the air force").
_ACTIVE_FRAME = r"\b(?:working|acting|served|serves|serving|flew|flying|flown)\s+as\s+(?:a|an|the)"
_FRAME_GAP = r"\s+(?:[\w-]+\s+){0,2}$"
ROLE_ASSIGNMENT = re.compile(
    r"(?:(?<!\bsuch\s)\b(?:as|like)\s+(?:a|an|the)|\b(?:was|were)\s+the)" + _FRAME_GAP, re.I
)
PROFILE_ROLE_ASSIGNMENT = re.compile(r"(?:" + _ACTIVE_FRAME + r")" + _FRAME_GAP, re.I)
# In story prose, a present participle right after the role ("a surveillance aircraft circling the
# city") is one flight's action. A lowercase word only, so a name ("Boeing") is none, and never a
# preposition that ends in "ing".
ROLE_ACTION = re.compile(
    r"\s+(?!(?:during|including|according|regarding|concerning|following|excluding)\b)[a-z]+ing\b"
)
# One flight's time anywhere in the role's sentence, in any prose; in story prose also a task,
# place, or duration ("for the operation", "over the city", "looking for", "while", "at work", "on
# duty", "for hours", "throughout"), words a type description uses of the type ("built for the air
# force", "with over 30 antennas").
_ONE_FLIGHT = r"that\s+day|today|on\s+this\s+flight|this\s+(?:morning|afternoon|evening)"
ONE_FLIGHT = re.compile(r"\b(?:" + _ONE_FLIGHT + r")\b", re.I)
FLIGHT_ANCHOR = re.compile(
    r"\b(?:"
    + _ONE_FLIGHT
    + r"|for\s+the|over|looking\s+for|while|at\s+work|on\s+duty|for\s+hours|throughout)\b",
    re.I,
)
SENTENCE_BREAK = re.compile(r"[.!?;]")
# Surveillance, reconnaissance, and intelligence name one family of roles, so a type described
# by one ("reconnaissance aircraft") supports the others.
ISR_WORDS = frozenset({"surveillance", "reconnaissance", "intelligence"})


def _purpose_word(word):
    """A word the mission claim row or a flight's purpose (_PURPOSE) reads as a purpose."""
    return bool(MISSION_CLAIM.fullmatch(word) or re.fullmatch(_PURPOSE, word, re.I))


def _purpose_modifiers(facts):
    """Type phrases that are a role category's modifier ending in a purpose: "maritime patrol" of
    "maritime patrol aircraft", "reconnaissance" of "reconnaissance aircraft". They name the type
    wherever the type's names count (a figure's sourcing, the name checks), but claim_issues never
    blanks one as a name: alone it reads as an activity ("it flew a maritime patrol")."""
    phrases = facts.type_phrases
    return {
        phrase
        for phrase in phrases
        if _purpose_word(phrase.split()[-1])
        and any(
            other.startswith(phrase + " ")
            and " " not in other[len(phrase) + 1 :]
            and _ROLE_PHRASE.search(other)
            for other in phrases
        )
    }


def _role_words(facts):
    """The words a story's type facts carry (descriptions, categories, profile facts), which a
    design purpose must be among before story prose may name it as the type's role."""
    if facts is None:
        return frozenset()
    words = {word for phrase in facts.type_phrases for word in _words(phrase)} | set(facts.profile_words)
    words = {word.removesuffix("s") for word in words}
    return frozenset(words | (ISR_WORDS if words & ISR_WORDS else set()))


def _anchored(text, match, end, *, profile):
    """A design role at `match`, ending at `end`, tied to one flight: after a frame that assigns
    it, or in a sentence with one flight's time; in story prose also with a participle after it
    or a flight's task or place in its sentence (ROLE_ASSIGNMENT, ROLE_ACTION, FLIGHT_ANCHOR)."""
    start = match.start()
    frame, anchor = (PROFILE_ROLE_ASSIGNMENT, ONE_FLIGHT) if profile else (ROLE_ASSIGNMENT, FLIGHT_ANCHOR)
    if frame.search(text, max(0, start - 80), start) or (not profile and ROLE_ACTION.match(text, end)):
        return True
    breaks = [found.end() for found in SENTENCE_BREAK.finditer(text, 0, start)]
    after = SENTENCE_BREAK.search(text, end)
    return bool(anchor.search(text, breaks[-1] if breaks else 0, after.start() if after else len(text)))


def _names_airframe(facts):
    """A test of whether a sentence names the airframe: its registration, callsign, or address.
    A design role in such a sentence is said of that airframe, so story prose leaves it to the
    mission and speculation rows."""
    values = facts.airframe if facts is not None else ()
    patterns = [re.compile(r"(?<![\w-])" + re.escape(value) + r"(?![\w-])", re.I) for value in values]
    return lambda sentence: any(pattern.search(sentence) for pattern in patterns)


def _blank_roles(text, facts, *, profile, original=None):
    """The site's phrases and design roles blanked, so the claim and mission checks pass them.

    In profile prose, the type's own description, a design purpose passes after "designed for",
    before a hyphen, and before a role noun (_ROLE_NOUN), and before a type noun or "type" when
    the type's facts carry it. In story prose every design role needs that support, and a hyphen
    compound needs a role noun after it, and the role's sentence in `original` (the text before
    type names were blanked) must name no airframe (_names_airframe). No branch passes a role tied
    to one flight (_anchored).
    """
    supported = _role_words(facts)
    source = text if original is None else original
    airframe, spans = (None, ()) if profile else (_names_airframe(facts), sentence_spans(source))

    def of_an_airframe(position):
        start, end = next(((a, b) for a, b in spans if a <= position < b), (0, len(source)))
        return airframe(source[start:end])

    def blank(match):
        if match["site"]:
            return "x" * len(match.group())
        group = "designed" if match["designed"] else "purpose"
        noun = match["compound"] or match["noun"]
        end = match.end(next((name for name in ("compound", "noun", "hyphen") if match[name]), group))
        supports = match[group].split()[-1].lower().removesuffix("s") in supported
        if profile:
            named = bool(noun) and (supports or bool(_BASE_ROLE_NOUN.fullmatch(noun)))
            allowed = group == "designed" or named or (bool(match["hyphen"]) and not noun)
        else:
            allowed = supports and (group == "designed" or bool(noun)) and not of_an_airframe(match.start())
        if not allowed or _anchored(match.string, match, end, profile=profile):
            return match.group()
        start, stop = match.span(group)
        offset = match.start()
        return match.group()[: start - offset] + "x" * (stop - start) + match.group()[stop - offset :]

    return DESIGN_ROLE.sub(blank, text)


# Where a watchlist entry's own words quote it: before "tag", "category", "label", "the entry", or
# "the entry's", or after "tagged", "labelled", or "listed as" or "under" ("a maritime patrol
# tag"). "a maritime patrol entry" is no quotation.
_ENTRY_LEAD = r"(?P<lead>\b(?:tagged|labell?ed|listed\s+(?:as|under))\s+[\"\u201c]?)"
_ENTRY_TAIL = (
    r"(?P<tail>[\"\u201d]?(?:\s+(?:tags?|categor(?:y|ies)|labels?)|,?\s+the\s+entry(?:['\u2019]s)?)\b)"
)


def _blank_entry_words(text, facts):
    """A watchlist entry's tags, list category, and association blanked where the text quotes
    the entry (_ENTRY_LEAD, _ENTRY_TAIL), in any case, so the mission rows read no purpose in
    them. The same words elsewhere ("it flew a maritime patrol") stay claims."""
    phrases = getattr(facts, "entry_phrases", ())
    if not phrases:
        return text
    names = "|".join(re.escape(phrase) for phrase in _longest_first(phrases))
    pattern = re.compile(
        _ENTRY_LEAD + r"?(?<![\w-])(?P<phrase>" + names + r")(?![\w-])" + _ENTRY_TAIL + "?", re.I
    )

    def blank(match):
        if not (match["lead"] or match["tail"]):
            return match.group()
        return (match["lead"] or "") + "x" * len(match["phrase"]) + (match["tail"] or "")

    return pattern.sub(blank, text)


@functools.lru_cache(maxsize=1024)
def _restatement(phrase):
    """A packet sentence or phrase restated as written, case and spacing aside. The final full
    stop is left out, so a blanked restatement keeps its sentence boundary."""
    return re.compile(r"(?<![\w-])" + _phrase_words(phrase) + r"(?![\w-])", re.I)


@functools.lru_cache(maxsize=1024)
def _bounded_pattern(names, flags, plural):
    plurals = r"(?:e?s)?" if plural else ""
    return re.compile(
        r"(?<![\w-])(?:" + "|".join(re.escape(name) for name in names) + r")" + plurals + r"(?![\w-])", flags
    )


def _bounded(names, *, flags=re.I, plural=False):
    """Names as one whole-word pattern, longest first, plurals too when asked; None for none."""
    names = tuple(_longest_first({name for name in names if isinstance(name, str) and name.strip()}))
    return _bounded_pattern(names, flags, plural) if names else None


@functools.lru_cache(maxsize=256)
def _organization_pattern(phrase, label, associations=()):
    """A wl.who phrase restated as written: "<label>, <description>", where the description may
    take "a", "an", or "the" ("Acme Air, a regional airline in Ohio"). With the entry's own
    `associations`, the label may also be followed by the name the entry carries ("Michigan
    Flight Museum, which the entry lists as Yankee Air Museum: an aviation museum")."""
    if not isinstance(phrase, str) or not phrase.strip():
        return None
    if isinstance(label, str) and phrase.lower().startswith(label.lower() + ", "):
        listed = "|".join(_phrase_words(name) for name in _longest_first(associations))
        bridge = rf"(?:,\s+which\s+the\s+entry\s+lists\s+as\s+(?:{listed}):|,)" if listed else ","
        body = (
            _phrase_words(phrase[: len(label)])
            + bridge
            + r"\s+(?:(?:a|an|the)\s+)?"
            + _phrase_words(phrase[len(label) + 2 :])
        )
    else:
        body = _phrase_words(phrase)
    return re.compile(r"(?<![\w-])" + body + r"(?![\w-])", re.I)


def _restated(text, phrases):
    """Blank verbatim restatements of packet sentences, which are already checked."""
    for pattern in _restatements(frozenset(phrases)):
        text = _blank(text, pattern)
    return text


@functools.lru_cache(maxsize=1024)
def _restatements(phrases):
    """_restatement() for each phrase, longest first; a story's checks read many sentences
    against the same phrases."""
    return tuple(_restatement(phrase) for phrase in _longest_first(phrases))


def sentence_spans(text):
    """(start, end) of each sentence; abbreviations such as "U.S." and "a.m." do not end one."""
    spans, start = [], 0
    for match in re.finditer(r"[.!?]+[\"')\]]*(?=\s)|\n+", text):
        if match.group()[0] == "." and ABBREVIATION.search(
            text, max(start, match.start() - 12), match.start() + 1
        ):
            continue
        spans.append((start, match.end()))
        start = match.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _spans(pattern, text, start, end):
    pieces, cursor = [], start
    for match in pattern.finditer(text, start, end):
        pieces.append((cursor, match.start()))
        cursor = match.end()
    pieces.append((cursor, end))
    return [(a, b) for a, b in pieces if text[a:b].strip()]


def _stem(word):
    for suffix in ("ations", "ation", "ings", "ing", "ions", "ion", "ers", "er", "ed", "es", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


class Facts:
    """Typed values, times, identifiers and vocabulary that a packet supports."""

    def __init__(self):
        self.values = {}  # dimension: {(value, role)}
        self.fields = {}  # (dimension, value): {packet field each occurrence came from}
        self.counts = set()
        self.identifiers = set()
        self.exact = set()
        self.years = set()
        self.words = set()
        self.type_phrases = set()
        # A watchlist entry's tags, list category, and association (_facts_for, _blank_entry_words).
        self.entry_phrases = ()
        # A mismatch story's second-registry type (tc.type): a name, never the story's type, so
        # no sentence naming it is about the type or borrows the type's profile figures.
        self.registry_phrases = set()
        # Both sets together: names the checks blank, never quantities or claims.
        self.name_phrases = set()
        # What Wikidata calls a watchlist entry's organization (story fact wl.who): it supports
        # itself restated as written (_organization_restatements), never its words alone, so a
        # sentence cannot borrow a place from it.
        self.organizations = set()
        # Their compiled patterns, built once the packet is indexed (packet_facts).
        self.restatements = ()
        # The type's usual ground speed and band in these records (story_facts._norm): ranges
        # that support only a sentence about the type scoped to these records (_baselined).
        self.baseline = {}  # story fact id: (figure, unit)
        self.category_phrases = set()
        self.phrases = set()
        self.names = set()
        self.codes = set()
        self.times = []
        self.characters = set()
        self.airspeed = False
        self.associations = []
        self.label_words = set()
        self.label_terms = set()
        self.association_terms = set()
        self.caveats = set()
        self.caveat_words = {}
        # Sourced type-profile sentences: names, but figures only for sentences about the type.
        self.profiles = []
        self.profile_words = set()
        self.airframe = set()
        # (dimension, canonical value, time) for each reading that carries its own time.
        self.readings = []
        self.meaning_words = set()
        # Story facts: hypotheses and limits are the only explanations a story may offer, and
        # only a hypothesis may be stated as a cause.
        self.public = False
        self.offered = set()
        self.hypothesis_words = set()
        self.watchlist = False
        self.military = False
        # A cited rated limit of the type: its figures may be called certified, never airspeed.
        self.rated = False
        # The story's kind, and whether it names no cause for its reading (CAUSELESS_KINDS).
        self.kind = None
        self.causeless = False
        # Story facts behind each value and profile sentence, and those the checks relied on
        # (bound_facts): a writer cites the sources of what its text uses.
        self.ids = {}  # (dimension, value): {story fact ids}
        self.profile_ids = {}  # profile sentence: story fact id
        self.bound = set()
        self._stems = self._prefixes = None

    def add(self, dimension, value, role="obs", field="", fact_id=None):
        if finite(value):
            self.values.setdefault(dimension, set()).add((float(value), role))
            self.fields.setdefault((dimension, float(value)), set()).add(
                field if isinstance(field, str) else ""
            )
            if fact_id:
                self.ids.setdefault((dimension, float(value)), set()).add(fact_id)
            if dimension.startswith("count:"):
                self.counts.add(float(value))

    def add_unit(self, value, unit, role="obs", field="", fact_id=None):
        if isinstance(unit, str) and unit in PACKET_UNITS:
            dimension, factor = PACKET_UNITS[unit]
            self.add(dimension, value * factor, role, field, fact_id)

    def bind(self, dimension, value):
        """Record the story facts behind a value a written quantity was matched to."""
        self.bound.update(self.ids.get((dimension, value), ()))

    def add_time(self, stamp):
        self.times.append(stamp)
        self.exact.add(str(stamp.year))

    def add_text(self, text, key, fact_id=None):
        text = _clean(text)
        self.characters.update(character for character in text if ord(character) > 126)
        if key == "association_label":
            label = " ".join(text.split()).lower()
            if label and label not in self.associations:
                self.associations.append(label)
            terms = re.findall(r"[A-Za-z]{2,}", text)
            self.label_words.update(word.lower() for word in terms)
            self.label_terms.update(word for word in terms if word[0].isupper())
            return
        if key == "organization":
            self.organizations.add(" ".join(text.split()))
            return
        if key == "profile_text":
            # Names and designators ("707-based", "E-3") only; its figures are checked per sentence.
            self.profiles.append(_profile_text(text))
            if fact_id:
                self.profile_ids[self.profiles[-1]] = fact_id
            self.profile_words.update(_words(text))
            for match in TOKEN.finditer(text):
                if re.search(r"\d", match.group()) and re.search(r"[A-Za-z]", match.group()):
                    self.identifiers.update(_joins(match.group()))
            return
        self.words.update(_words(text))
        stripped = text.strip()
        if key in AIRFRAME_KEYS and stripped:
            self.airframe.add(stripped)
        if key in PHRASE_KEYS and 3 <= len(stripped) <= 80 and re.search(r"[A-Z]", stripped):
            self.phrases.add(stripped)
        if key == "code_definition":
            self.meaning_words.update(_words(text))
        if key in IDENTITY_KEYS:
            for match in TOKEN.finditer(text):
                if re.search(r"\d", match.group()):
                    self.identifiers.update(_joins(match.group()))
                    self.exact.update(re.findall(r"\d+(?:\.\d+)?", match.group()))
            words = text.split()
            for first, second in zip(words, words[1:], strict=False):
                if re.fullmatch(r"[A-Za-z][A-Za-z.&'-]*", first) and re.match(r"\d", second):
                    self.names.add(_normal(first) + " " + _normal(second.rstrip(".,;:")))
        if key in {"year", "database_year"} and re.fullmatch(r"(?:18|19|20)\d\d", stripped):
            self.years.add(int(stripped))
        # Short designators ("E-6", "C-5") name a type too; short words do not.
        if key in TYPE_PHRASE_KEYS and (
            len(stripped) >= 4 or (len(stripped) == 3 and re.search(r"\d", stripped))
        ):
            self.type_phrases.add(stripped.lower())
            self.name_phrases.add(stripped.lower())
        if key == "registry_type" and len(stripped) >= 3:
            self.registry_phrases.add(stripped.lower())
            self.name_phrases.add(stripped.lower())
        if key in CATEGORY_PHRASE_KEYS and len(stripped) >= 4:
            self.category_phrases.add(stripped.lower())
        if key in {"squawk", "reported_squawk", "code", "emergency", "emergency_status"}:
            if episode_evidence.octal_code(stripped) == stripped or stripped in CODE_MEANINGS:
                self.codes.add(stripped)
        if CAVEAT_KEY.search(key) and len(text) >= 20:
            self.caveats.add(" ".join(text.split()))
        # Numbers inside caveats describe rules and scopes, not new observations. A sentence
        # that restates the caveat may repeat them.
        for item in _quantities(text):
            for dimension, factor in item.units:
                value = abs(item.value) * factor
                if key in {"unit", "metric_unit"}:
                    self.add(dimension, value)
                    continue
                self.add(dimension, value, "caveat")
                relation = "range" if item.range else _relation(text, item.span[0])
                sentence = next((text[a:b] for a, b in sentence_spans(text) if a <= item.span[0] < b), text)
                self.caveat_words.setdefault((dimension, value), []).append(
                    (_content_words(sentence), relation, bool(NEGATION.search(sentence)))
                )
        if (stamp := moment(text)) is not None:
            self.add_time(stamp)

    def known(self, word):
        """Whether a hedged clause's word comes from the packet's own text."""
        if self._stems is None:
            self._stems = {_stem(item) for item in self.words}
            self._prefixes = {item[:6] for item in self.words if len(item) >= 6}
        return (
            word in self.words
            or _stem(word) in self._stems
            or (len(word) >= 6 and word[:6] in self._prefixes)
        )


def _role(key, dimension):
    if key in RULE_KEYS:
        return "rule"
    if dimension == "altitude":
        if "change" in key or "gain" in key or key == "metric_regained":
            return "change"
        if "excursion" in key or "separation" in key:
            return "span"
        return "level" if key in LEVEL_KEYS else "obs"
    return "obs"


def _unit_for(key, parent):
    if key in FIELD_UNITS:
        return FIELD_UNITS[key]
    if key == "metric_value":
        return parent.get("metric_unit")
    if key == "peak_value":
        return parent.get("unit")
    if key in episode_evidence.METRICS:
        return episode_evidence.METRICS[key][1]
    for suffix, unit in SUFFIX_UNITS:
        if key.endswith(suffix):
            return unit
    return None


def _sample_stamp(item):
    return next((found for name in SAMPLE_TIME_KEYS if (found := moment(item.get(name))) is not None), None)


def _index(packet, facts):
    """One pass over the packet: values, texts and times, window durations, ratios and timed readings.

    Returns the time series of each list of readings and every peak time, for sample spacing.
    """
    series, peaks = [], []

    def measured(value):
        field = value.get("field") or value.get("key")
        field = field if isinstance(field, str) else ""
        unit = value.get("unit") if isinstance(value.get("unit"), str) and value.get("unit") else None
        unit = unit or FIELD_UNITS.get(field)
        reading = value["value"]
        if finite(reading):
            if field in AIRSPEED_FIELDS:
                facts.airspeed = True
            if field in RATED_FIELDS:
                facts.rated = True
            dimension, factor = PACKET_UNITS.get(unit, (None, 1))
            role = "rule" if value.get("scope") == "detection-rule" else _role(field, dimension)
            facts.add_unit(reading, unit, role, field, value.get("id"))
            if dimension in TIMED_DIMENSIONS and (stamp := _sample_stamp(value)) is not None:
                facts.readings.append((dimension, reading * factor, stamp))
        elif isinstance(reading, str):
            facts.add_text(reading, field if field in {"squawk", "code", "advisory"} else "label")

    def windows(value):
        for key, start in value.items():
            if not isinstance(key, str):
                continue
            for suffix, other in WINDOW_PAIRS:
                if key.endswith(suffix):
                    first, last = moment(start), moment(value.get(key[: -len(suffix)] + other))
                    if first is not None and last is not None and last >= first:
                        facts.add("time", (last - first).total_seconds())

    def scalar(value, key, parent):
        if key in AIRSPEED_FIELDS:
            facts.airspeed = True
        unit = _unit_for(key, parent or {})
        dimension = PACKET_UNITS.get(unit, (None, 1))[0]
        role = "change" if key == "metric_value" and unit == "ft regained" else _role(key, dimension)
        facts.add_unit(value, unit, role, key)
        if TIME_KEY.search(key) and (stamp := moment(value)) is not None:
            facts.add_time(stamp)
        if key in {"database_year", "year"} and 1800 <= value <= 2100:
            facts.exact.add(str(int(value)))
            facts.years.add(int(value))
        parts = set(key.split("_"))
        if parts & {"fraction", "ratio", "share"}:
            facts.add("percent", value * 100)
        if "ratio" in parts:
            facts.add("ratio", value)

    def visit(value, key, parent):
        if key in SKIPPED_KEYS:
            return
        if isinstance(value, dict):
            if "baseline" in value and "unit" in value and finite(value["baseline"]):
                # A population figure, never a reading (facts_packet): only _baselined binds it.
                if isinstance(value.get("id"), str):
                    facts.baseline[value["id"]] = (float(value["baseline"]), value["unit"])
                return
            is_measured = "value" in value and ("unit" in value or "field" in value)
            if key == "watchlist" and isinstance(value.get("tags"), list):
                # "Its entry carries three tags" counts the entry's tags.
                facts.add("count:tags", len(value["tags"]))
            if is_measured:
                measured(value)
            elif (stamp := _sample_stamp(value)) is not None:
                # A sample such as {"ts", "gs", "alt_baro"}: each reading keeps the sample's time.
                for name, reading in value.items():
                    unit = FIELD_UNITS.get(name)
                    if finite(reading) and unit in PACKET_UNITS:
                        dimension, factor = PACKET_UNITS[unit]
                        if dimension in TIMED_DIMENSIONS:
                            facts.readings.append((dimension, reading * factor, stamp))
            windows(value)
            for child_key, child in value.items():
                name = child_key if isinstance(child_key, str) else ""
                if PEAK_KEY.search(name) and (stamp := moment(child)) is not None:
                    peaks.append(stamp)
                if not (is_measured and child_key == "value"):
                    visit(child, name, value)
        elif isinstance(value, list):
            if key == "labels" and isinstance(parent, dict) and parent.get("id") == "wl.tags":
                # Story facts give the entry's tags as wl.tags; "It has two tags" counts them too.
                facts.add("count:tags", len(value), fact_id="wl.tags")
            stamps = [
                stamp for item in value[:200] if isinstance(item, dict) and (stamp := _sample_stamp(item))
            ]
            if len(stamps) > 1:
                series.append(stamps)
            for item in value:
                visit(item, key, parent)
        elif isinstance(value, str):
            # A story fact's entry carries its id beside the text (facts_packet).
            facts.add_text(value, key, parent.get("id") if isinstance(parent, dict) else None)
        elif finite(value):
            scalar(value, key, parent)

    visit(packet, "", None)
    return series, peaks


def packet_facts(packet):
    """Everything in a story packet, notes payload or public event that prose may cite."""
    packet = packet if isinstance(packet, dict) else {}
    facts = Facts()
    series, peaks = _index(packet, facts)
    facts.restatements = _organization_restatements(facts)
    facts.add("count:aircraft", 1)
    participants = packet.get("participants")
    if isinstance(participants, list) and participants:
        facts.add("count:aircraft", len(participants))
        facts.add("count:aircraft", len(participants) + 1)
    kind = packet.get("kind")
    if isinstance(kind, str) and kind.startswith(rules.SQUAWK_PREFIXES):
        facts.codes.add(kind.rsplit("_", 1)[1])
    facts.kind = kind if isinstance(kind, str) else None
    facts.causeless = isinstance(kind, str) and (
        kind in CAUSELESS_KINDS or kind.startswith(CAUSELESS_PREFIXES)
    )
    received = packet.get("received") if isinstance(packet.get("received"), dict) else {}
    if isinstance(received.get("emergency_status"), str) and received["emergency_status"] in CODE_MEANINGS:
        facts.codes.add(received["emergency_status"])
    # Sample spacing: neighbours in each list of readings, and each reading's offset from a peak.
    for stamps in series:
        for first, second in zip(stamps, stamps[1:], strict=False):
            facts.add("time", abs((second - first).total_seconds()), "spacing")
        for stamp in stamps:
            for peak in peaks:
                facts.add("time", abs((stamp - peak).total_seconds()), "spacing")
    for peak in peaks:
        for stamp in facts.times:
            if abs((stamp - peak).total_seconds()) <= 3600:
                facts.add("time", abs((stamp - peak).total_seconds()), "spacing")
    for year in facts.years:
        for stamp in facts.times:
            facts.add("years", stamp.year - year)
            facts.add("years", stamp.year - year - 1)
    # Words used only by association labels identify the association wherever they appear;
    # a manufacturer's name in a type phrase ("Boeing 747-400 Dreamlifter") does not.
    typed = {word for phrase in facts.type_phrases for word in _words(phrase)}
    association_words = facts.label_words - facts.words - typed - STOPWORDS - FIXED_VOCABULARY
    # Capitalized, as names are written: "Swift" names the association, "swift" does not.
    facts.association_terms = {term for term in facts.label_terms if term.lower() in association_words}
    facts.identifiers.update(facts.codes)
    facts.exact.update(facts.codes)
    facts.public = isinstance(packet.get("hypotheses"), list)
    for key in ("hypotheses", "limitations"):
        for text in packet.get(key) or ():
            if isinstance(text, str):
                facts.offered.update(_words(_clean(text)))
                if key == "hypotheses":
                    facts.hypothesis_words.update(_words(_clean(text)))
    facts.offered |= facts.meaning_words
    watchlist = packet.get("watchlist")
    facts.watchlist = (
        bool(isinstance(watchlist, dict) and watchlist) or packet.get("watchlist_listed") is True
    )
    flags = (
        (packet.get("metadata") or {}).get("database_flags")
        if isinstance(packet.get("metadata"), dict)
        else None
    )
    facts.military = packet.get("military_flag") is True or (
        isinstance(flags, dict) and flags.get("military") is True
    )
    return facts


class _Item(NamedTuple):
    """A written quantity: its span, signed value, rounding step and unit."""

    span: tuple
    text: str
    value: float
    place: float
    unit: str
    units: list
    signed: bool = False
    number_span: tuple = ()
    multiplier: float = 1
    range: bool = False


def _quantities(text):
    """Digit numbers written with a known unit or count noun; a range's start shares its unit."""
    found = []
    for match in DIGITS.finditer(text):
        if match["ord"]:
            continue
        end, multiplier = match.end(), 1
        if scale := MULTIPLIER.match(text, end):
            multiplier, end = SCALES[scale["mult"].lower()], scale.end()
        unit = UNIT_AFTER.match(text, end)
        if not unit:
            continue
        magnitude = _number(match["n"]) * multiplier
        found.append(
            _Item(
                span=(match.start(), unit.end()),
                text=text[match.start() : unit.end()],
                value=-magnitude if match["sign"] == "-" else magnitude,
                place=_place(match["n"]) * multiplier,
                unit=unit["unit"],
                units=_units(unit["unit"]),
                signed=bool(match["sign"]),
                number_span=match.span("n"),
                multiplier=multiplier,
            )
        )
    ranged, starts = set(), []
    for index, item in enumerate(found):
        position = item.span[0]
        for _ in range(8):
            start = RANGE_START.search(text, max(0, position - 40), position)
            if not start or any(other.span[0] <= start.start("n") < other.span[1] for other in found):
                break
            ranged.add(index)
            starts.append(
                item._replace(
                    span=start.span("n"),
                    number_span=start.span("n"),
                    text=start["n"] + " " + item.unit,
                    value=_number(start["n"]) * item.multiplier,
                    place=_place(start["n"]) * item.multiplier,
                    signed=False,
                    range=True,
                )
            )
            position = start.start("n")
    return [
        item._replace(range=True) if index in ranged else item for index, item in enumerate(found)
    ] + starts


def _count(written):
    """A written count: 30, "thirty", "twenty-five", "a" or "an"; None counts as zero."""
    if written is None:
        return 0.0
    word = written.lower()
    if word in {"a", "an"}:
        return 1.0
    if word in WORD_VALUES:
        return float(WORD_VALUES[word])
    if re.fullmatch(r"[\d,]+(?:\.\d+)?", word):
        return _number(word)
    return float(_spelled_value(word))


def _spelled_value(phrase):
    words = [word for word in re.split(r"[\s-]+", phrase.lower()) if word and word not in {"and", "a", "an"}]
    total, current = 0, 0
    for word in words:
        if word in WORD_VALUES:
            current += WORD_VALUES[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word in {"thousand", "million"}:
            total += (current or 1) * SCALES[word]
            current = 0
    return total + current


def _close(written, place, candidate, signed, tolerance=0.1):
    """Rounded or truncated to the written precision, and within a relative tolerance."""
    value = written if signed else abs(written)
    candidate = candidate if signed else abs(candidate)
    gap = abs(value - candidate)
    slack = 1e-9 * max(1.0, abs(candidate))
    rounded = gap <= place / 2 + slack
    truncated = abs(value) <= abs(candidate) < abs(value) + place - slack and (value >= 0) == (candidate >= 0)
    if not (rounded or truncated):
        return False
    return candidate == 0 or gap <= tolerance * abs(candidate) + 1e-9


def _exact(written, place, candidate, signed):
    """A value stated without "about": rounding may move it by at most a twentieth.

    "600 mph" for 560 rounds to one significant figure and needs "about".
    """
    return _close(written, place, candidate, signed, 0.05)


def _relation(text, start):
    before = text[max(0, start - 48) : start]
    if before.rstrip().endswith("~"):
        return "about"
    match = RELATION.search(before)
    if not match:
        return "exact"
    name = next(name for name in ("never", "greater", "nearly", "less", "about") if match[name])
    word = match[name]
    # "over a 16-minute window" and "within the 30-second interval" are prepositions, not bounds.
    if re.fullmatch(r"over|above|below|under|within|beyond", word, re.I) and re.match(
        r"\s+(?:a|an|the|its|their|this|that|each|every)\b", before[match.end(name) :], re.I
    ):
        return "exact"
    return "over" if name == "greater" and word.lower() == "over" else name


@functools.lru_cache(maxsize=16)
def _breaks(text):
    """Sorted clause starts and ends for a whole text, computed once per text."""
    starts = sorted(
        {m.end() for m in re.finditer(r"(?<=[.!?])\s+|\n+", text)}
        | {m.end() for pattern in (SEGMENT_BREAK, CLAUSE_COMMA) for m in pattern.finditer(text)}
    )
    ends = sorted(
        {m.start() for pattern in (SEGMENT_BREAK, CLAUSE_COMMA) for m in pattern.finditer(text)}
        | {m.start() for m in re.finditer(r"[.!?](?=\s|$)", text)}
    )
    return starts, ends


def _clause_bounds(text, position):
    starts, ends = _breaks(text)
    before = bisect.bisect_right(starts, position)
    after = bisect.bisect_left(ends, position)
    return (starts[before - 1] if before else 0), (ends[after] if after < len(ends) else len(text))


def _sentence_bounds(text, position):
    return next(((a, b) for a, b in sentence_spans(text) if a <= position < b), (0, len(text)))


def _segment_bounds(text, position):
    """The part of a sentence between segment breaks (";", "but", ", and it") around a position."""
    start, end = _sentence_bounds(text, position)
    return next(
        ((a, b) for a, b in _spans(SEGMENT_BREAK, text, start, end) if a <= position < b), (start, end)
    )


def _sentence_before(text, position):
    """The current sentence up to a position, at most a few hundred characters."""
    start = _sentence_bounds(text, position)[0]
    return text[max(start, position - 300) : position]


def _candidates(facts, dimension, clause, *, change=None, relation="exact"):
    """Packet values for a written quantity; rules count only when the sentence is about a rule."""
    rule = bool(RULE_CONTEXT.search(clause))
    spacing = bool(SPACING_CONTEXT.search(clause))
    words = _content_words(clause)
    found = []
    for value, role in facts.values.get(dimension, ()):
        if (role == "rule" and not rule) or (role == "spacing" and not spacing):
            continue
        # A restated caveat keeps its own relation ("above", "within") and its negation.
        if role == "caveat" and not (
            rule
            or any(
                len(words & source) >= 3 and kind == relation and bool(NEGATION.search(clause)) == negated
                for source, kind, negated in facts.caveat_words.get((dimension, value), ())
            )
        ):
            continue
        if change is not None and dimension == "altitude":
            if role == "change" and (value > 0) != change and value != 0:
                continue
            if role not in {"change", "span"}:
                continue
        found.append((dimension, value, role))
    return found


def _named_field(original, clause_start, item):
    """The field a clause names for a written quantity ("true airspeed", "by pressure"), or None."""
    before = original[clause_start : item.span[0]]
    found = [match for match in NAMED_FIELDS.finditer(before)]
    match = found[-1] if found else FIELD_AFTER.match(original, item.span[1])
    if not match:
        return None
    return next(name for name, hit in match.groupdict().items() if hit)


def _field_fits(named, field):
    """Whether a packet field can stand behind a measurement named in prose."""
    if named in {"tas", "ias", "alt_geom", "estimated_airspeed"}:
        return field == named
    if named == "certified":
        # "Maximum operating altitude of 41,000 ft" takes the type's cited figure, never a reading.
        return field in RATED_FIELDS
    if named == "airspeed":
        return field in AIRSPEED_FIELDS
    if named == "gs":
        return field not in AIRSPEED_FIELDS
    # Pressure altitude: any altitude but a geometric one or an archived one of unknown reference.
    return field not in {"alt_geom", "archive_alt", "alt", "track_alt"}


def _window(text, position):
    """The clause around a position, at most CONTEXT characters either side."""
    start, end = _clause_bounds(text, position)
    return max(start, position - CONTEXT), min(end, position + CONTEXT)


def _supports(relation, candidate, item, written):
    """Whether a packet value, in the written unit, supports a quantity stated with this relation."""
    value, place, signed = item.value, item.place, item.signed
    if relation == "exact":
        return _exact(value, place, candidate, signed)
    if relation == "about":
        return _close(value, place, candidate, signed, 0.2)
    if relation == "greater":
        return candidate >= abs(written)
    if relation == "over":
        # "Over three minutes" can mean during them, so a rounded match also passes.
        return candidate >= abs(written) or _close(value, place, candidate, signed)
    if relation == "less":
        return candidate <= abs(written)
    if relation == "nearly":
        return max(abs(written) - place, 0.9 * abs(written)) <= candidate <= abs(written)
    return False


def _quantity_issue(item, facts, original):
    position = item.span[0]
    clause_start, clause_end = _window(original, position)
    clause = original[clause_start:clause_end]
    relation = _relation(original, position)
    value = item.value
    before = original[clause_start:position]
    change = None
    # "From 20,000 to 9,650 ft" and "between 8,000 and 15,000 ft" name levels, not changes.
    if not LEVEL_CONTEXT.search(before[-12:]) and not RANGE_LEVEL.search(before[-48:]):
        climb, descent = bool(CLIMB_WORDS.search(before)), bool(DESCENT_WORDS.search(before))
        if climb != descent:
            change = climb
    for dimension, factor in item.units:
        if dimension == "years" and 1800 <= value <= 2100 and item.unit.lower() == "year":
            return None if str(int(value)) in facts.exact else "Unsupported year: " + item.text
        if dimension == "count:times":
            dimension = (
                "count:loops"
                if LOOP_WORDS.search(clause)
                else "count:passes"
                if PASS_WORDS.search(clause)
                else "count:reversals"
                if REVERSAL_WORDS.search(clause)
                else "count:samples"
                if SAMPLE_WORDS.search(_sentence_before(original, position) + clause)
                else None
            )
            if dimension is None:
                return "Unsupported quantity: " + item.text.strip()
        range_relation = "range" if item.range else relation
        candidates = _candidates(
            facts,
            dimension,
            clause,
            change=change if dimension == "altitude" else None,
            relation=range_relation,
        )
        named = _named_field(original, clause_start, item)
        if named and dimension in NAMED_DIMENSIONS[named]:
            candidates = [
                entry
                for entry in candidates
                if any(_field_fits(named, field) for field in facts.fields.get((dimension, entry[1]), {""}))
            ]
        if dimension == "altitude" and not item.signed:
            # An unsigned altitude is never a negative level: "500 ft" does not restate -500 ft.
            candidates = [entry for entry in candidates if entry[1] >= 0 or entry[2] in {"change", "span"}]
        if dimension == "vrate" and not item.signed:
            climb, descent = bool(RATE_CLIMB.search(before)), bool(RATE_DESCENT.search(before))
            if climb != descent:
                directed = [entry for entry in candidates if (entry[1] > 0) == climb or entry[1] == 0]
                if not directed and any(
                    _close(value, item.place, c / factor, False) for _, c, _ in candidates
                ):
                    return "Vertical direction contradicts the packet: " + clause.strip()[:80]
                candidates = directed
        if change is not None and dimension == "altitude" and not candidates:
            others = _candidates(facts, dimension, clause, relation=range_relation)
            if any(
                role == "change" and _close(value, item.place, c / factor, False) for _, c, role in others
            ):
                return "Altitude direction contradicts the packet: " + clause.strip()[:80]
        # (packet value, value in the written unit) for every candidate, and for the observed ones.
        scaled = [(c, abs(c / factor) if not item.signed else c / factor) for _, c, _ in candidates]
        observed = [(c, abs(c / factor)) for _, c, role in candidates if role != "rule"]
        written = abs(value) if not item.signed else value
        if relation == "never":
            matched = observed if observed and max(c for _, c in observed) <= abs(written) else []
        else:
            pool = scaled if relation in {"exact", "about"} else observed or scaled
            matched = [entry for entry in pool if _supports(relation, entry[1], item, written)]
        if matched:
            for packet_value, _c in matched:
                facts.bind(dimension, packet_value)
            return None
    return "Unsupported quantity: " + item.text.strip()


def _clock_matches(stamp, clock):
    """Whether a stated hour, minute and second fit a reading's time of day."""
    hour, minute, second = clock
    base = stamp.replace(hour=hour, minute=minute or 0, second=second or 0, microsecond=0)
    if minute is None:
        return base - timedelta(minutes=30) <= stamp < base + timedelta(minutes=60)
    return abs(stamp - base) < timedelta(seconds=1 if second is not None else 60)


def _reading_time_issue(item, facts, original, clocks):
    """A reading stated with a time must be the reading saved at that time."""
    dimensions = {dimension: factor for dimension, factor in item.units if dimension in TIMED_DIMENSIONS}
    if not dimensions or not clocks:
        return None
    start, end = _segment_bounds(original, item.span[0])
    stated = [clock for position, clock in clocks if start <= position < end]
    if not stated:
        return None
    times = [
        stamp
        for dimension, value, stamp in facts.readings
        if dimension in dimensions
        and _close(item.value, item.place, value / dimensions[dimension], item.signed)
    ]
    if not times or any(_clock_matches(stamp, clock) for stamp in times for clock in stated):
        return None
    return "Reading and time do not match: " + item.text.strip()


def _bare_supported(value, place, position, facts, original, *, signed=False, nouns_only=False):
    """A value in the unit its clause named earlier, or else an exact count or a zero reading."""
    clause_start, _end = _clause_bounds(original, position)
    before = original[max(clause_start, position - 160) : position]
    named = [
        (found.end(), [(next(name for name, hit in found.groupdict().items() if hit), 1)], found.group())
        for found in FIELD_NOUNS.finditer(before)
    ]
    if not named and not nouns_only:
        if value == int(value) and not signed and value in facts.counts:
            return True
        if value == 0 and any(
            entry == 0 and role not in {"spacing", "caveat", "rule"}
            for entries in facts.values.values()
            for entry, role in entries
        ):
            return True
    for found in () if nouns_only else re.finditer(r"[A-Za-z][A-Za-z/-]*", before):
        units = _units(found.group())
        # "An altitude sample of 1000" names a sample, not the number's unit.
        if units and not any(
            dimension in {"count:samples", "count:aircraft", "count:times"} for dimension, _ in units
        ):
            named.append((found.end(), units, found.group()))
    if not named:
        return False
    _end, units, word = max(named, key=lambda entry: entry[0])
    item = _Item((position, position), f"{value:g} {word}", value, place, word, units, signed)
    return _quantity_issue(item, facts, original) is None


def _negated_count(value, position, facts, original):
    """A packet count repeated in a clause that denies what it counts: not 51 flights."""
    start, _end = _window(original, position)
    before = original[start:position]
    # The denial sits right before the number: "do not represent 51 flights".
    return (
        value in facts.counts
        and bool(re.search(r"\b(?:not|never|nor|rather than|instead of)\s+(?:[a-z]+\s+){0,2}$", before, re.I))
        and not re.search(r"\b(?:no|not)\s+(?:fewer|less|more)\b|\bat least\b", before, re.I)
    )


def _mask(text, span):
    start, end = span
    return text[:start] + " " * (end - start) + text[end:]


def _time_supported(facts, hour, minute=None, second=None, day=None):
    return any(
        (day is None or stamp.date() == day) and _clock_matches(stamp, (hour, minute, second))
        for stamp in facts.times
    )


def _zoned(written, text, end):
    """Colloquial times ("2 p.m.", "noon") must say UTC; a reader would otherwise take local time."""
    return bool(re.search(r"\b(?:UTC|GMT)\b|\dZ\b", written) or re.match(r"\s*(?:UTC|GMT|Z)\b", text[end:]))


def _date_supported(facts, month, day=None, year=None):
    return any(
        stamp.month == month and (day is None or stamp.day == day) and (year is None or stamp.year == year)
        for stamp in facts.times
    )


def _day(text):
    text = text.lower()
    return ORDINAL_WORDS.get(text) or int(re.sub(r"\D", "", text))


def _spoken(match, text):
    """[(hour, minute)] a spoken clock time can mean, or None when the phrase is not a clock time."""
    if match["base"]:
        unit = match["unit"].lower()
        if unit.startswith("quarter"):
            minutes = 15 * (_count(match["count"]) or 1)
        elif unit.startswith("half"):
            minutes = 30
        elif match["count"] is None:
            return None  # "minutes after midnight" names no clock time; NAMED_TIME reads it.
        else:
            minutes = _count(match["count"]) * (60 if unit.startswith("hour") else 1)
        sign = 1 if match["rel"].lower() in {"after", "past"} else -1
        total = ((0 if match["base"].lower() == "midnight" else 720) + sign * minutes) % 1440
        return [(int(total // 60), int(total % 60))]
    part, hour_text, rel = match["part"].lower(), match["h"].lower(), match["rel2"].lower()
    named = hour_text in {"midnight", "noon"}
    # "Two to three passes" is a range, not ten to three o'clock.
    if rel == "to" and part not in {"half", "quarter"} and not named:
        if not re.match(r"\s*(?:o'clock|UTC|GMT)\b", text[match.end() :], re.I):
            return None
    minutes = 30 if part == "half" else 15 if part == "quarter" else _count(part)
    if not 0 < minutes < 60:
        return None
    hours = [0] if hour_text == "midnight" else [12] if hour_text == "noon" else []
    if not named:
        hour = int(_count(hour_text)) % 12
        hours = [hour, hour + 12]
    if rel == "to":
        return [((hour - 1) % 24, int(60 - minutes)) for hour in hours]
    return [(hour, int(minutes)) for hour in hours]


def _repeat_supported(match, facts, original):
    """A measured span back in time: "82 years before" an observation of a 1944 build."""
    if not match["n"]:
        return False
    count = _count(match["n"])
    unit = (match["unit"] or match["unit2"] or match["unit3"]).lower()
    span = _Item(match.span(), match.group(), count, 1, unit, _units(unit))
    return _quantity_issue(span, facts, original) is None


class _Scan:
    """The text under the number checks. Each pass checks one family of numbers and masks it."""

    def __init__(self, text, facts):
        self.text = self.original = text
        self.facts = facts
        self.issues = []
        self.clocks = []  # (position, (hour, minute, second)) of each stated clock time

    def reject(self, kind, written):
        self.issues.append(f"Unsupported {kind}: {written.strip()[:60]}")

    def mask(self, span):
        self.text = _mask(self.text, span)

    def check(self, item):
        issue = _quantity_issue(item, self.facts, self.original) or _reading_time_issue(
            item, self.facts, self.original, self.clocks
        )
        if issue:
            self.issues.append(issue)


def _dates(scan):
    facts = scan.facts
    for match in CURRENCY.finditer(scan.text):
        scan.reject("money amount", match.group())
        scan.mask(match.span())
    for match in ISO.finditer(scan.text):
        try:
            day = datetime(int(match["y"]), int(match["mo"]), int(match["d"]), tzinfo=timezone.utc).date()
            if match["h"] and (int(match["h"]) > 23 or int(match["mi"]) > 59 or int(match["s"] or 0) > 59):
                raise ValueError
        except ValueError:
            scan.reject("date", match.group())
        else:
            zone = (match["zone"] or "Z").upper()
            if zone not in {"Z", "UTC", "GMT", "+00:00", "+0000", "-00:00"}:
                scan.reject("time zone", match.group())
            elif match["h"]:
                second = int(match["s"]) if match["s"] else None
                if not _time_supported(facts, int(match["h"]), int(match["mi"]), second, day):
                    scan.reject("date or time", match.group())
            elif not any(stamp.date() == day for stamp in facts.times):
                scan.reject("date", match.group())
        scan.mask(match.span())
    for match in NUMERIC_DATE.finditer(scan.text):
        a, b, c = int(match["a"]), int(match["b"]), int(match["c"])
        options = [(a, b, c)] if len(match["a"]) == 4 else [(c, a, b), (c, b, a)]
        if not any(
            1 <= month <= 12 and _date_supported(facts, month, day, year if year >= 100 else 2000 + year)
            for year, month, day in options
        ):
            scan.reject("date", match.group())
        scan.mask(match.span())
    for match in DATE_RANGE.finditer(scan.text):
        month = MONTHS[(match["mo"] or match["mo2"]).rstrip(".")]
        year = match["y"] or match["y2"]
        days = (match["d1"], match["d2"]) if match["d1"] else (match["d3"], match["d4"])
        if not all(_date_supported(facts, month, int(day), int(year) if year else None) for day in days):
            scan.reject("date", match.group())
        scan.mask(match.span())
    for pattern in (DAY_MONTH, MONTH_DAY):
        for match in pattern.finditer(scan.text):
            name = match["mo"].rstrip(".")
            if name not in MONTHS:
                continue
            if not _date_supported(
                facts, MONTHS[name], _day(match["d"]), int(match["y"]) if match["y"] else None
            ):
                scan.reject("date", match.group())
            scan.mask(match.span())
    for match in MONTH_YEAR.finditer(scan.text):
        if not _date_supported(facts, MONTHS[match["mo"].rstrip(".")], year=int(match["y"])):
            scan.reject("date", match.group())
        scan.mask(match.span())
    for match in MONTH_ALONE.finditer(scan.text):
        if not _date_supported(facts, MONTHS[match["mo"].rstrip(".")]):
            scan.reject("month", match.group())
        scan.mask(match.span())
    for match in RELATIVE_DATE.finditer(scan.text):
        scan.reject("relative date", match.group())
    for match in RELATIVE_REPEAT.finditer(scan.text):
        if not _repeat_supported(match, facts, scan.original):
            scan.reject("relative date", match.group())
        scan.mask(match.span())
    for name in WEEKDAYS:
        for match in re.finditer(r"\b" + name + r"s?\b", scan.text, re.I):
            if not any(WEEKDAYS[stamp.weekday()] == name for stamp in facts.times):
                scan.reject("weekday", match.group())


def _times(scan):
    facts, text = scan.facts, scan.text
    # "It held 39,000 ft for all 17 minutes" claims every report; "the record covers the full 17
    # minutes" only states a duration, which the measure checks compare with the facts.
    for match in SPAN_CLAIM.finditer(text):
        start, end = _sentence_bounds(text, match.start())
        if HELD_READING.search(text, start, end):
            scan.reject("span", match.group())
    for pattern in (CLOCK, MILITARY_TIME, HOUR_TIME):
        for match in pattern.finditer(scan.text):
            groups = match.groupdict()
            hour = int(groups["h"])
            meridiem = (groups.get("ap") or "").lower().replace(".", "")
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            minute = int(groups["mi"]) if groups.get("mi") else None
            second = int(groups["s"]) if groups.get("s") else None
            if meridiem and not _zoned(match.group(), scan.text, match.end()):
                scan.reject("time zone", match.group())
            elif not _time_supported(facts, hour, minute, second):
                scan.reject("time", match.group())
            scan.clocks.append((match.start(), (hour, minute, second)))
            scan.mask(match.span())
    for match in SPOKEN_TIME.finditer(scan.text):
        times = _spoken(match, scan.text)
        if times is None:
            continue
        if not _zoned(match.group(), scan.text, match.end()):
            scan.reject("time zone", match.group())
        elif not any(_time_supported(facts, hour, minute) for hour, minute in times):
            scan.reject("time", match.group())
        scan.clocks.extend((match.start(), (hour, minute, None)) for hour, minute in times)
        scan.mask(match.span())
    for match in NAMED_TIME.finditer(scan.text):
        hour = 12 if match["name"].lower() == "noon" else 0
        if not _zoned(match.group(), scan.text, match.end()):
            scan.reject("time zone", match.group())
        elif not _time_supported(facts, hour):
            scan.reject("time", match.group())
        scan.clocks.append((match.start(), (hour, None, None)))
        scan.mask(match.span())


def _levels(scan):
    for pattern, kind, units in (
        (FLIGHT_LEVEL, "flight level", [("altitude", 100)]),
        (MACH, "Mach number", [("mach", 1)]),
    ):
        for match in pattern.finditer(scan.text):
            written = next(group for group in match.groups() if group)
            item = _Item(match.span(), match.group(), _number(written), _place(written), kind, units)
            if _quantity_issue(item, scan.facts, scan.original):
                scan.reject(kind, match.group())
            scan.mask(match.span())
    for match in DECADE.finditer(scan.text):
        start = int(match["n"])
        if not any(str(year) in scan.facts.identifiers for year in range(start, start + 10)):
            scan.reject("decade", match.group())
        scan.mask(match.span())


def _names(scan):
    # Packet type names such as "Bell 407" and "Beech 200" are names, not quantities.
    for phrase in _longest_first(scan.facts.name_phrases):
        for match in re.finditer(re.escape(phrase), scan.text, re.I):
            scan.mask(match.span())
    # An entry's organization restated as written keeps its own figures.
    for pattern in scan.facts.restatements:
        for match in pattern.finditer(scan.text):
            scan.mask(match.span())
    for match in re.finditer(r"\b([A-Za-z][A-Za-z.&'-]*)\s+(\d[\w-]*)", scan.text):
        if _normal(match[1]) + " " + _normal(match[2]) in scan.facts.names:
            scan.mask(match.span(2))


def _measures(scan):
    facts = scan.facts
    for match in RATE_PER.finditer(scan.original):
        if not re.fullmatch(UNIT_PATTERN, match.group(), re.I):
            scan.reject("rate", match.group())
    for match in COMPOUND_DURATION.finditer(scan.text):
        parts = [match[name] for name in ("h", "m", "s")]
        if sum(part is not None for part in parts) < 2:
            continue
        seconds = sum(_count(part) * scale for part, scale in zip(parts, (3600, 60, 1), strict=True))
        scan.check(_Item(match.span(), match.group(), seconds, 1 if match["s"] else 60, "s", [("time", 1)]))
        scan.mask(match.span())
    for match in MULTIPLE.finditer(scan.text):
        word = match.group().split()[0].lower()
        value = {"twice": 2, "double": 2, "thrice": 3, "triple": 3, "quadruple": 4}.get(word)
        if value is None:
            value = _count(match["n"])
        if COMPARATIVE_AFTER.match(scan.text, match.end()) or word in {"double", "triple", "quadruple"}:
            if not any(_close(value, 1, ratio, False) for ratio, _ in facts.values.get("ratio", ())):
                scan.reject("comparison", match.group())
        else:
            scan.check(_Item(match.span(), match.group(), value, 1, "times", [("count:times", 1)]))
        scan.mask(match.span())
    for match in AND_A_HALF.finditer(scan.text):
        units = _units(match["unit"])
        scan.check(_Item(match.span(), match.group(), _count(match["n"]) + 0.5, 0.5, match["unit"], units))
        scan.mask(match.span())
    for match in FRACTION.finditer(scan.text):
        group = match.group().lower()
        if "half" in group:
            fraction = 0.5
        elif "third" in group:
            fraction = 2 / 3 if group.startswith("two") else 1 / 3
        else:
            fraction = 0.75 if group.startswith("three") else 0.25
        value = fraction + _count(match["whole"])
        unit = UNIT_AFTER.match(scan.text, match.end()) or re.match(
            r"\s+(?:of\s+)?(?:an?\s+)(?P<unit>" + UNIT_PATTERN + r")\b", scan.text[match.end() :], re.I
        )
        if unit and _units(unit["unit"]):
            text = match.group() + " " + unit["unit"]
            scan.check(_Item(match.span(), text, value, 0.5, unit["unit"], _units(unit["unit"])))
        elif not any(_close(value * 100, 10, share, False) for share, _ in facts.values.get("percent", ())):
            scan.reject("fraction", match.group())
        scan.mask(match.span())
    # Known serials and all-digit registrations are identifiers, not counts: "87-0043 reports",
    # "163920 reports". Codes and years keep their own checks.
    # Type numbers ("its 200 seats") stay quantities: only hyphenated or five-digit serials qualify.
    for match in re.finditer(r"(?<![\w.-])(?:\d+(?:-\d+)+|\d{5,})(?![\w.-])", scan.text):
        token = match.group()
        if _normal(token) in facts.identifiers and token not in facts.codes:
            scan.mask(match.span())
    for item in sorted(_quantities(scan.text), key=lambda found: found.span):
        written = scan.original[item.number_span[0] : item.number_span[1]]
        # "A 7700 report" or "the 1966 entry" names a code or year; "sent 1200 reports" is a count.
        known = (
            (written in facts.codes or (len(written) == 4 and written in facts.exact))
            and not item.unit.lower().endswith("s")
            and bool(DETERMINER.search(scan.original, max(0, item.number_span[0] - 24), item.number_span[0]))
        )
        if item.units and not (known and item.units[0][0].startswith("count:")):
            scan.check(item)
        scan.mask(item.number_span)
        scan.mask(item.span)


def _spelled(scan):
    facts, original = scan.facts, scan.original
    for match in SPELLED.finditer(scan.text):
        phrase = match["w"]
        value = _spelled_value(phrase)
        text = scan.text
        unit = UNIT_AFTER.match(text, match.end())
        following = NEXT_WORD.match(text, match.end())
        noun = (unit["unit"] if unit else following["word"] if following else "").lower()
        anaphoric = (
            value in {2, 3}
            and re.search(
                r"\b(?:the|these|those|both)\s+$", text[max(0, match.start() - 8) : match.start()], re.I
            )
            and MEASURE_NOUNS.fullmatch(noun)
        )
        if (
            unit
            and (phrase.lower() == "one" or anaphoric)
            and _units(unit["unit"])[:1]
            and (_units(unit["unit"])[0][0].startswith("count:"))
        ):
            scan.mask((match.start(), unit.end()))
            continue
        if unit:
            span = (match.start(), unit.end())
            scan.check(_Item(span, text[span[0] : span[1]], value, 1, unit["unit"], _units(unit["unit"])))
            scan.mask(span)
            continue
        words = [word for word in re.split(r"[\s-]+", phrase.lower()) if word not in {"and", "a", "an"}]
        if phrase.lower() == "one" or (
            all(word in SCALES for word in words)
            and phrase[0].isdigit() is False
            and not re.match(r"an?\s", phrase, re.I)
        ):
            continue
        if following and following["word"].lower() not in STOPWORDS:
            if not anaphoric and not _negated_count(value, match.start(), facts, original):
                scan.reject("quantity", phrase + " " + following["word"])
        elif value != 1 and not _bare_supported(value, 1, match.start(), facts, original):
            scan.reject("number", phrase)
        scan.mask(match.span())
    for match in ANOTHER_AIRCRAFT.finditer(scan.text):
        wanted = ORDINAL_WORDS[match["n"].lower()] if match["n"] else 2
        if not any(count >= wanted for count, _ in facts.values.get("count:aircraft", ())):
            scan.reject("quantity", match.group())
        scan.mask(match.span())
    ordinals = {value for value, _role in facts.values.get("ordinal", ())}
    for match in re.finditer(
        r"\b(?:(?:the|its|their|his|her)\s+)(?P<o>second)\b|\b(?P<w>" + SPELLED_DAY + r")\b", scan.text, re.I
    ):
        word = (match["o"] or match["w"]).lower()
        if word == "first" or (word == "second" and not match["o"]):
            continue
        if ORDINAL_WORDS[word] not in ordinals:
            scan.reject("ordinal", match.group())
        scan.mask(match.span())


def _identifiers(scan):
    facts = scan.facts
    for match in DIGIT_RUN.finditer(scan.text):
        if _normal(match.group()) in facts.identifiers:
            scan.mask(match.span())
    for match in IDENTIFIER.finditer(scan.text):
        token = match.group()
        if token.endswith("s") and _normal(token[:-1]) in facts.identifiers:
            scan.mask(match.span())
            continue
        pieces = re.split(r"[-/]", token)
        parts = [part for part in pieces if re.search(r"\d", part)]
        if _normal(token) not in facts.identifiers and not (
            parts and all(_normal(part) in facts.identifiers for part in parts) and len(parts) < len(pieces)
        ):
            scan.reject("identifier", token)
        scan.mask(match.span())


def _bare_numbers(scan):
    facts, original, text = scan.facts, scan.original, scan.text
    ordinals = {value for value, _role in facts.values.get("ordinal", ())}
    for match in DIGITS.finditer(text):
        written = match["n"]
        if match["ord"]:
            if not any(stamp.day == int(written) for stamp in facts.times) and int(written) not in ordinals:
                scan.reject("ordinal", match.group())
            continue
        if re.fullmatch(r"0{2,}", written):
            scan.reject("number", written)  # "0000" is an absent-code placeholder, never a reading.
            continue
        if OUT_OF.match(original, match.end()) and _number(written) in facts.counts:
            continue
        # A bare number repeats an identifier, code, year or count exactly, or takes the unit
        # its clause already stated; it is never matched against arbitrary packet values.
        exact = not match["sign"] and written in facts.exact
        following = NEXT_WORD.match(text, match.end())
        if (
            exact
            and re.fullmatch(r"(?:18|19|20)\d\d", written)
            and int(written) not in facts.years
            and BUILT.search(original, max(0, match.start() - 60), match.start())
        ):
            scan.reject("year", written)
            continue
        # An exact identifier after an article names the type: "A 777 appears", never "its 200 seats".
        plural = (
            following
            and re.fullmatch(r"[a-z]+s", following["word"])
            and following["word"].lower() not in (facts.words | VERBS)
        )
        named = (
            exact and not plural and DETERMINER.search(original, max(0, match.start() - 24), match.start())
        )
        # A database year or code keeps its own wording: "the listed year of 1974 yields".
        named = named or (
            exact
            and (written in facts.codes or (written.isdigit() and int(written) in facts.years))
            and YEAR_OR_CODE.search(original, max(0, match.start() - 48), match.start())
        )
        if (
            following
            and following["word"].lower() not in STOPWORDS
            and not (
                exact
                and (
                    named
                    or len(written) >= 5
                    or following["word"].lower() in {"squawk", "code", "status", "report"}
                )
            )
        ):
            value = _number(written)
            if not (
                _negated_count(value, match.start(), facts, original)
                or _bare_supported(value, _place(written), match.start(), facts, original, nouns_only=True)
            ):
                scan.reject("quantity", written + " " + following["word"])
        elif not exact and not _bare_supported(
            -_number(written) if match["sign"] == "-" else _number(written),
            _place(written),
            match.start(),
            facts,
            original,
            signed=bool(match["sign"]),
        ):
            scan.reject("number", match.group())


NUMBER_PASSES = (_dates, _times, _levels, _names, _measures, _spelled, _identifiers, _bare_numbers)


def _number_issues(text, facts):
    """Every date, time, number and identifier must resolve to a packet fact; passes run in order."""
    scan = _Scan(text, facts)
    for check in NUMBER_PASSES:
        check(scan)
    return scan.issues


class _Hedges:
    """Hedge matches in one scan text, found once per pattern; intensifiers are not hedges."""

    def __init__(self, scan):
        self.scan = _blank(scan, INTENSIFIERS)
        self.found = {}
        self.relatives = [match.span() for match in RELATIVE.finditer(self.scan)]

    def within(self, hedge, start, end, claim):
        if hedge not in self.found:
            self.found[hedge] = [(match.start(), match.end()) for match in hedge.finditer(self.scan)]
        # "The crew, who may have been tired, declared ...": the relative clause hedges only itself.
        excluded = [span for span in self.relatives if not span[0] <= claim < span[1]]
        matches = self.found[hedge]
        index = bisect.bisect_left(matches, (start, -1))
        while index < len(matches) and matches[index][0] < end:
            first, last = matches[index]
            if last <= end and not any(a <= first < b for a, b in excluded):
                return True
            index += 1
        return False


def _scope(scan, segment_start, clause, position, first):
    """Where a hedge must sit to govern a claim at a position.

    A subordinate clause ("when it crashed") and a later comma clause with its own
    subject and verb ("..., it was an emergency") need their own hedge. List items
    ("the operator, the occupants or the mission") share the segment's hedge.
    """
    clause_start, clause_end = clause
    cuts = [
        clause_start,
        *(m.start() for m in SUBORDINATE.finditer(scan, clause_start, clause_end)),
        clause_end,
    ]
    piece = max(index for index, cut in enumerate(cuts[:-1]) if cut <= position)
    start, end = cuts[piece], cuts[piece + 1]
    if piece:
        return start, end
    if not first and INDEPENDENT.match(scan, clause_start):
        return clause_start, end
    return segment_start, end


# Hedges that may follow their claim: "... and the data cannot show why", "... is beyond what
# the data shows".
TRAILING_HEDGES = (EPISTEMIC, SHOW_LIMIT, OCCUPANT_DISCLAIMER)


def _governed(hedged, scope, hedges, position, lead=False, *, trailing=True):
    """A hedge inside the claim's scope governs it; a leading "Hypothesis:" label covers its segment.

    "Cannot show" may follow its claim; a negation or possibility must come before it,
    so "it crashed and nobody survived" is not hedged.
    """
    if lead and any(hedge in LOOSE_HEDGES for hedge in hedges):
        return True
    start, end = scope
    return any(
        hedged.within(hedge, start, end if trailing and hedge in TRAILING_HEDGES else position, position)
        for hedge in hedges
    )


def _offered(phrase, facts):
    """Whether a hedged claim's words come from the story's hypotheses, limits or code meanings."""
    words = [word for word in _words(phrase) if len(word) >= 3 and word not in STOPWORDS]
    stems = {_stem(word) for word in facts.offered}
    return bool(words) and all(word in facts.offered or _stem(word) in stems for word in words)


def _verdict(hedged, scope, hedges, position, lead, facts, phrase):
    """None when the claim is hedged enough; otherwise the issue's opening words.

    In a public story a possibility hedge covers only a listed explanation: "may have
    been a decoding error" passes, "may have been on a charter" does not.
    """
    firm = tuple(hedge for hedge in hedges if hedge not in LOOSE_HEDGES)
    loose = tuple(hedge for hedge in hedges if hedge in LOOSE_HEDGES)
    if _governed(hedged, scope, firm, position):
        return None
    if loose and _governed(hedged, scope, loose, position, lead):
        return None if not facts.public or _offered(phrase, facts) else "Unlisted hedged"
    return "Unhedged"


def _code_mentions(scan, start, end, facts):
    """Packet codes named in a span, with their positions."""
    found = []
    for code in facts.codes:
        if code in CODE_MEANINGS:
            pattern = r"(?<![\w-])" + re.escape(code) + r"(?![\w-])"
            found.extend((code, m.start(), m.end()) for m in re.finditer(pattern, scan[start:end], re.I))
    return [(code, first + start, last + start) for code, first, last in found]


def _code_meaning(scan, mentions, match_start, match_end, phrase, segment_end):
    """A transmitted code's standard meaning, stated as a definition rather than an event."""
    for code, start, end in mentions:
        if not re.fullmatch(CODE_MEANINGS[code], phrase, re.I):
            continue
        window = scan[min(start, match_start) : max(end, match_end, segment_end)]
        asserted = any(
            not NEGATION.search(window[max(0, found.start() - 14) : found.start()])
            for found in ASSERTION.finditer(window)
        )
        if DEFINITION.search(window) and not asserted:
            return True
    return False


def _attributed(piece, facts):
    """Whether a passage puts a watchlist detail in the list's mouth.

    The list is named with a verb of attribution, or, in a watchlist story, referred back
    to ("the entry names", "the list files it under"), or the detail takes the house form
    of a headline ("N51EM, listed under Jane Example, spotted", "Jane Example-listed N51EM
    spotted"). A listed form beside another source ("listed under Jane Example in the FAA
    registry") is that source's, not the list's. The story checks separately require that
    such a story names Plane Alert DB in its paragraphs.
    """
    if ATTRIBUTION.search(piece) and (
        WATCHLIST_SOURCE.search(piece) or (facts.watchlist and WATCHLIST_REFERENCE.search(piece))
    ):
        return not (LISTED_FORM.search(piece) and OTHER_SOURCE.search(piece))
    return facts.watchlist and bool(LISTED_HEADING.search(piece)) and not OTHER_SOURCE.search(piece)


def _mentions_association(text, facts):
    """The label, a capitalized word only the label uses, or a personal pronoun next to it."""
    if not facts.associations:
        return False
    lowered = " ".join(text.lower().split())
    return (
        any(label in lowered for label in facts.associations)
        or any(re.search(r"\b" + re.escape(term) + r"\b", text) for term in facts.association_terms)
        or bool(PERSONAL.search(text))
    )


def _type_ngrams(facts):
    """Runs of two or more words from packet type descriptions that still name a type, longest first.

    A run must keep a model designator or a type noun, so "maritime patrol aircraft" and
    "P-38 Lightning" are names but "maritime patrol" alone is a claim.
    """
    found = set()
    for phrase in facts.type_phrases:
        words = phrase.split()
        for size in range(2, len(words) + 1):
            for start in range(len(words) - size + 1):
                run = words[start : start + size]
                if any(re.search(r"\d", word) or TYPE_NOUN.fullmatch(word) for word in run):
                    found.add(" ".join(run))
    return _longest_first(found)


def _generic_role(scan, word, position, segment):
    """Practice, not this flight's crew: "Pilots use 7700", "when a pilot needs to"."""
    before = scan[max(0, position - 12) : position]
    generic = (GENERIC_ROLES.fullmatch(word) and not DEFINITE.search(before)) or re.search(
        r"\b(?:a|an|any)\s+$", before, re.I
    )
    return bool(generic and HABITUAL.search(scan, *segment))


# Claims that take the first match in a clause: (pattern, hedges, message, skip(facts, rare_scope, clause)).
SINGLE_CLAIMS = (
    (OWNER_CLAIM, (EPISTEMIC,), "Unhedged owner claim", lambda facts, scoped, clause: False),
    # Rarity and normality need a scope ("in these records") or a statement of what the data cannot
    # show; general practice ("pilots sometimes ...") is not a claim about this flight.
    (RARITY, (EPISTEMIC, HABITUAL), "Unscoped rarity claim", lambda facts, scoped, clause: scoped),
    (
        AIRSPEED,
        (NEGATION,),
        "Airspeed claim without an airspeed report",
        lambda facts, scoped, clause: facts.airspeed or _rated_figure(facts, clause),
    ),
)


def _rated(facts, clause):
    """A clause stating the type's cited certified limit, which is no claim about this flight."""
    return facts.rated and bool(RATED_LIMIT.search(clause))


def _rated_figure(facts, clause):
    """A clause giving the certified figure in knots ("a maximum operating speed of 340
    knots"): the type's number, not an airspeed this flight reported."""
    return _rated(facts, clause) and bool(CERTIFIED_FIGURE.search(clause))


def _unknown_words(text, known):
    """Words of a hedged or causal clause that claim something: not generic, not a hedge, and not
    known(word)."""
    unknown = []
    for raw in re.findall(r"[a-z][a-z'-]*", text.lower()):
        word = raw.removesuffix("'s").strip("'-")
        if (
            len(word) >= 3
            and word not in STOPWORDS
            and word not in SPECULATION_GENERIC
            and not re.fullmatch(r"x+", word)
            and not SPECULATIVE.fullmatch(word)
            and not known(word)
        ):
            unknown.append(word)
    return unknown


def _speculation_issue(body, facts):
    """In a public story, what a possibility hedge offers must come from the packet's own text."""
    hedge = SPECULATIVE.search(body)
    if not hedge:
        return None
    # "The data cannot show whether it may have been ..." states a limit; a disclaimer after
    # the possibility ("..., which is unknown") does not.
    limit = EPISTEMIC.search(body)
    if limit and limit.start() <= hedge.start():
        return None
    unknown = _unknown_words(body[hedge.end() :], facts.known)
    return ", ".join(unknown[:4]) if unknown else None


def _cause_issue(body, facts):
    """A cause stated for a reading that the story's hypotheses do not list, hedged or not.

    A kind with no cause to offer (CAUSELESS_KINDS) refuses one whatever the clause names;
    another kind's clause must name a reading for its cause to count.
    """
    if not facts.causeless and not READING_NOUNS.search(body):
        return None
    for match in CAUSE.finditer(body):
        if not facts.hypothesis_words:
            return "Unlisted cause: "
        unknown = _unknown_words(
            body[match.start() :], lambda word: word in CAUSE_MARKER_WORDS or word in facts.hypothesis_words
        )
        if unknown:
            return f"Unlisted cause ({', '.join(unknown[:4])}): "
    return None


def _limit_vs_ground_speed(sentence, facts):
    """Whether a sentence sets the type's certified speed (its figure or its name) beside a
    ground speed: another speed, the words "ground speed", or a comparison."""

    def certified(item):
        fields = facts.fields.get(("speed", float(abs(item.value))))
        return bool(fields) and fields <= SPEED_LIMIT_FIELDS

    speeds = [item for item in _quantities(sentence) if any(name == "speed" for name, _ in item.units)]
    figures = [item for item in speeds if certified(item)]
    if not figures and not SPEED_LIMIT_NAMES.search(sentence):
        return False
    return bool(SPEED_COMPARISON.search(sentence)) or len(speeds) > len(figures)


def _causeless_issue(sentence):
    """In a story with no cause to offer, a sentence that names one: a cause noun ("a decoding
    error", "multipath"), a verdict in a sentence that names a source ("the database is wrong"),
    or a causal verb about the reading ("Bad data caused the jump") that no provenance explains."""
    if noun := CAUSE_NOUNS.search(sentence):
        return f"Unlisted cause ({noun.group().lower()}): "
    if NAMED_SOURCE.search(sentence) and (verdict := SOURCE_VERDICT.search(sentence)):
        return f"Unlisted cause ({verdict.group().lower()}): "
    verb = CAUSAL_VERBS.search(sentence)
    if verb and READING_SUBJECT.search(sentence) and not PROVENANCE.search(sentence):
        return f"Unlisted cause ({verb.group().lower()}): "
    return None


def _clause_issues(text, scan, hedged, facts, segment, clause, first, led, mentions, rare_scope):
    """Claims in one comma clause: owners, rarity, airspeed, the CLAIMS table and speculation."""
    clause_start, clause_end = clause
    body = scan[clause_start:clause_end]
    original = text[clause_start:clause_end].strip()[:80]
    scope = functools.partial(_scope, scan, segment[0], clause, first=first)
    issues = []
    for match in POSSESSIVE.finditer(text, clause_start, clause_end):
        name = match["name"]
        if _mentions_association(name, facts):
            issues.append("Watchlist association written as ownership: " + original)
        elif _normal(name) not in facts.identifiers and not all(
            word.lower() in facts.words for word in name.split()
        ):
            place = clause_start + match.start()
            if not _governed(hedged, scope(place), (EPISTEMIC,), place, led):
                issues.append("Unhedged owner claim: " + original)
    for pattern, hedges, message, skip in SINGLE_CLAIMS:
        match = pattern.search(body)
        if match and not skip(facts, rare_scope, body):
            place = clause_start + match.start()
            if not _governed(hedged, scope(place), hedges, place):
                issues.append(message + ": " + original)
    for name, pattern, hedges in CLAIMS:
        if name == "military" and facts.military:
            continue  # The database's military flag is a fact the story may state.
        for match in pattern.finditer(body):
            position = clause_start + match.start()
            if name == "capability" and NEVER_EXCEED.fullmatch(match.group()) and _rated(facts, body):
                continue  # "Its never-exceed speed is 223 knots" states the type's cited limit.
            if name in {"crew", "air traffic control"} and _generic_role(
                scan, match.group(), position, segment
            ):
                continue  # "Pilots use 7700 ..." describes practice, not this crew.
            if (
                name == "weather"
                and facts.kind == "winds_aloft_day"
                and match.group().lower() in {"strong wind", "strong winds"}
                and re.match(r"\s+aloft\b", body[match.end() :])
            ):
                continue  # "Strong winds aloft" names the kind, which its ground-speed comparison measures.
            if name == "emergency" and NO_STATUS.match(scan, position):
                continue  # "The emergency status was none" reports an empty field.
            if name == "occupant" and re.match(r"\s*who\b", body, re.I):
                continue  # "Who was aboard ... is beyond what the data shows" names the question.
            if name in {"emergency", "unlawful interference"} and _code_meaning(
                scan, mentions, position, clause_start + match.end(), match.group(), segment[1]
            ):
                continue
            verdict = _verdict(hedged, scope(position), hedges, position, led, facts, match.group())
            if verdict:
                issues.append(f"{verdict} {name} claim: " + original)
                break
    if facts.public:
        for match in SPECULATION.finditer(body):
            position = clause_start + match.start()
            # Only a hedge before it governs a purpose or near miss: "it was on a mission, which
            # cannot be shown" still states the mission.
            if not _governed(hedged, scope(position), (EPISTEMIC, NEGATION), position, trailing=False):
                issues.append("Speculative purpose or near-miss claim: " + original)
                break
        if unknown := _speculation_issue(body, facts):
            issues.append(f"Unlisted possibility ({unknown}): " + original)
        if cause := _cause_issue(body, facts):
            issues.append(cause + original)
    return issues


def claim_issues(text, facts, *, profile=False):
    """Claims that packet_facts() output does not support: owners, rarity, airspeed, the CLAIMS
    table and, in a public story, speculation. A `profile` sentence is the type's own
    description, so it may name a design role its facts do not carry (_blank_roles)."""
    scan = text
    # Type names, and the parts of a description that still name the type ("maritime patrol
    # aircraft", "P-38 Lightning"), are names rather than claims; "light" never blanks "flight".
    # A phrase that names a design role ("reconnaissance aircraft") is left to _blank_roles, which
    # reads its frames and anchors, and a role category's modifier alone is no name here.
    modifiers = _purpose_modifiers(facts)
    for phrase in (
        *_longest_first(facts.name_phrases),
        *_type_ngrams(facts),
    ):
        if not _ROLE_PHRASE.search(phrase) and phrase not in modifiers:
            scan = _blank_phrase(scan, phrase, bounded=True)
    # Split once type names are blanked: "Gulfstream V." ends a sentence, not an initial.
    spans = sentence_spans(scan)
    # With types blanked but labels intact, "Boeing" in "Boeing 747-400 Dreamlifter" is the
    # type, while "Boeing flew it" still names the association.
    typed = scan
    scan = _blank_entry_words(_blank_roles(scan, facts, profile=profile, original=text), facts)
    for label in _longest_first(facts.associations):
        # The association's own words cannot supply attribution or trigger a claim.
        scan = _blank_phrase(scan, label)
    issues = []
    hedged = _Hedges(scan)
    for start, end in spans:
        sentence = scan[start:end]
        if _attributed(sentence, facts):
            for phrase in _longest_first(facts.category_phrases):
                sentence = _blank_phrase(sentence, phrase)
            scan = scan[:start] + sentence + scan[end:]
            hedged = _Hedges(scan)
        if facts.watchlist:
            named = sentence
            for phrase in _longest_first(facts.phrases):
                named = _blank_phrase(named, phrase, bounded=True)
            if PERSON_ASSOCIATION.search(named):
                issues.append("Watchlist story names a kind of person: " + text[start:end].strip()[:80])
        if facts.public and facts.causeless and (cause := _causeless_issue(sentence)):
            issues.append(cause + text[start:end].strip()[:80])
        if facts.kind in SPEED_PROSE_KINDS and _limit_vs_ground_speed(sentence, facts):
            issues.append("claim:limit_vs_ground_speed: " + text[start:end].strip()[:80])
        lead = LEAD_HEDGE.match(scan, start)
        for segment in _spans(SEGMENT_BREAK, scan, start, end):
            piece = scan[segment[0] : segment[1]]
            led = lead is not None and segment[0] == lead.end()
            if _mentions_association(typed[segment[0] : segment[1]], facts):
                if not _attributed(piece, facts) and not EPISTEMIC.search(piece):
                    issues.append(
                        "Unattributed watchlist association: " + text[segment[0] : segment[1]].strip()[:80]
                    )
            mentions = _code_mentions(scan, *segment, facts)
            rare_scope = bool(RARE_SCOPE.search(piece))
            for index, clause in enumerate(_spans(CLAUSE_COMMA, scan, *segment)):
                issues.extend(
                    _clause_issues(
                        text, scan, hedged, facts, segment, clause, index == 0, led, mentions, rare_scope
                    )
                )
    return list(dict.fromkeys(issues))


def _typed_spans(text, facts):
    """sentence_spans() with the packet's type names blanked: "Gulfstream V." ends a sentence."""
    blanked = text
    for phrase in _longest_first(facts.name_phrases):
        blanked = _blank_phrase(blanked, phrase, bounded=True)
    return sentence_spans(blanked)


def _organization_restatements(facts):
    """Patterns for each wl.who phrase restated as written (_organization_pattern)."""
    patterns = []
    for phrase in facts.organizations:
        label = next(
            (
                label
                for label in _longest_first(facts.associations)
                if phrase.lower().startswith(label + ", ")
            ),
            None,
        )
        others = tuple(sorted(name for name in facts.associations if name != label))
        patterns.append(_organization_pattern(phrase, label, others))
    return tuple(patterns)


def _opens_with_town(sentence, words, index, towns):
    """Whether the words from index on spell a regional town (places.town_names), possessive
    aside, or the word's first hyphenated part is one ("Detroit-built")."""
    return re.split(r"[-']", words[index].group())[0] in towns or any(
        sentence[words[index].start() : words[index + count - 1].end()].removesuffix("'s") in towns
        for count in range(1, min(4, len(words) - index) + 1)
    )


def name_issues(text, facts, extra=frozenset(), *, strict_opening=False):
    """Capitalized names must come from the packet, the site's fixed vocabulary or the extra names given.

    A sentence's first word passes when the next word is lowercase ("Moments later the jet
    descended"). With strict_opening, as for a profile sentence, a regional town from the pinned
    places bundle (places.town_names) gets no exemption there and is checked like any other name
    ("Brighton tests came before the E-6" is refused unless the vocabulary has the word); a place
    outside the bundle passes ("Wichita tests came before the E-6"), and without the bundle no
    town is known.
    """
    issues = []
    for pattern in facts.restatements:
        # Its words name nothing alone: "the flight ended in Ohio" borrows no place from it.
        text = _blank(text, pattern)
    vocabulary = facts.words | facts.profile_words | facts.label_words | FIXED_VOCABULARY | extra
    towns = places.town_names() if strict_opening else frozenset()
    for start, end in _typed_spans(text, facts):
        sentence = text[start:end]
        # Headlines are sentence case too, so title case cannot hide a name.
        words = list(re.finditer(r"[A-Za-z][A-Za-z0-9&'-]*", sentence))
        for index, word in enumerate(words):
            token = word.group()
            if not token[0].isupper() or re.search(r"\d", token):
                continue
            initial = index == 0 or re.search(
                r"[:\"(]\s*$", sentence[max(0, word.start() - 8) : word.start()]
            )
            if initial and not (towns and _opens_with_town(sentence, words, index, towns)):
                following = words[index + 1].group() if index + 1 < len(words) else ""
                known_following = all(
                    part.lower() in vocabulary for part in re.split(r"[-']", following) if part[:1].isupper()
                )
                if (
                    token.lower() in STOPWORDS
                    or token.lower() in WORD_VALUES
                    or not (following[:1].isupper() and not re.search(r"\d", following))
                    or known_following
                ):
                    continue
            parts = [part for part in re.split(r"[-']", token) if part[:1].isupper()]
            if not all(
                part.lower() in vocabulary or (part.endswith("s") and part[:-1].lower() in vocabulary)
                for part in parts
            ):
                issues.append("Unsupported name: " + token[:60])
    return issues


def phrase_issues(text, kind):
    """What a phrase standing alone trips, against no facts: a kind of person, receiver-relative
    location wording, or a claim. An airport name and an organization's description pass it
    before a story may carry them."""
    empty = packet_facts({"kind": kind, "facts": [], "limitations": [], "hypotheses": [], "sources": []})
    issues = ["Names a kind of person"] if PERSON_ASSOCIATION.search(text) else []
    return issues + location_issues(text) + claim_issues(text, empty)


def location_issues(text):
    """Receiver-relative or private location phrasing; needs no packet."""
    issues = []
    for pattern in LOCATION:
        if match := pattern.search(text):
            issues.append("Receiver-relative or private location phrasing: " + match.group().strip())
    # Phrases that are location framing unless their own context explains them.
    for pattern, context in ((CLOSEST_APPROACH, PAIR_CONTEXT), (RECEIVERS, MLAT_CONTEXT)):
        for match in pattern.finditer(text):
            start, end = _window(text, match.start())
            if not context.search(text[start:end]):
                issues.append("Receiver-relative or private location phrasing: " + match.group())
    # "Here" places the reader at the receiver unless it means this journal ("its fourth story here").
    for match in HERE.finditer(text):
        before = re.split(r"[.!?]\s", text[max(0, match.start() - 48) : match.start()])[-1]
        if PLACE_HERE.search(before) or not JOURNAL_SCOPE.search(before):
            issues.append("Receiver-relative or private location phrasing: " + match.group())
    return issues


def _about_type(sentence, facts):
    """A sentence about the type, not this airframe: it names the type ("E-6s" too) and no aircraft,
    "this jet" or "this E-6B"."""
    lowered = sentence.lower()
    names = [re.escape(name) + r"(?:e?s)?(?![\w-])" for name in facts.type_phrases]
    return (
        any(re.search(r"(?<![\w-])" + name, lowered) for name in names)
        and not AIRFRAME_REFERENCE.search(sentence)
        and not any(re.search(r"\b(?:this|that|the same)\s+(?:[\w-]+\s+)?" + name, lowered) for name in names)
        and not any(
            re.search(r"(?<![\w-])" + re.escape(identifier) + r"(?![\w-])", sentence, re.I)
            for identifier in facts.airframe
        )
    )


def _figure(match):
    return " ".join(match.group().lower().split())


def _sourced(text, facts):
    """Text for the number checks, with sourced type-profile figures blanked where they apply.

    A verbatim restatement keeps its figures. Elsewhere a profile figure supports only a
    sentence about the type, never one about this airframe, and only when the sentence
    shares the profile sentence's wording.
    """
    if not facts.profiles:
        return text
    # A restated or paraphrased profile sentence is one the text relies on (bound_facts).
    restated = [profile for profile in facts.profiles if _restatement(profile).search(text)]
    text = _restated(text, facts.profiles)
    for start, end in sentence_spans(text):
        sentence = text[start:end]
        if not _about_type(sentence, facts):
            continue
        words = _content_words(sentence)
        for profile in facts.profiles:
            if len(words & _content_words(profile)) < 3:
                continue
            restated.append(profile)
            figures = {_figure(match) for match in FIGURE.finditer(profile)}
            sentence = FIGURE.sub(
                lambda match, figures=figures: (
                    "x" * len(match.group()) if _figure(match) in figures else match.group()
                ),
                sentence,
            )
        text = text[:start] + sentence + text[end:]
    facts.bound.update(facts.profile_ids[profile] for profile in restated if profile in facts.profile_ids)
    return text


# How a baseline range is written: the speeds as "380 to 430 knots", the band as "between 30,000
# and 40,000"; a lone figure, the two out of order, or speeds in another unit are never the range.
SPEED_UNIT = r"\s*(?:knots?|kn|kts?)\b"
BASELINE_FORMS = {
    "kn": r"{low}\s*(?:to|and|-)\s*{high}" + SPEED_UNIT,
    "ft": r"\bbetween\s+{low}\s+and\s+{high}",
}
# A sentence that gives a range may carry no other speed or altitude, in any unit: "so 651 knots
# stands out" or "against a record of 750 mph" compares it with an aircraft.
BASELINE_OTHER = re.compile(r"(?<![\w.,])" + SPEED_OR_ALTITUDE, re.I)
# A sentence that gives the range may not also compare it with this aircraft or make it a limit
# or a record.
BASELINE_REFUSED = re.compile(
    r"\bthis\s+(?:one|aircraft|airframe|jet|plane|airplane|airliner|helicopter)\b|\b(?:it|its|it's)\b"
    r"|\bthe\s+(?:jet|aircraft|airplane|helicopter)['\u2019]s\b"
    r"|\b(?:top(?:s|ped)?\s+out|cannot|can't|never|at\s+most|no\s+more\s+than|fastest|slowest|maximum"
    r"|limits?|ceilings?|exceed\w*)\b",
    re.I,
)


def _figure_pattern(value):
    """A figure as text may write it: 30000 or 30,000."""
    if float(value).is_integer():
        texts = {str(int(value)), f"{int(value):,}"}
    else:
        texts = {str(value)}
    return (
        r"(?<![\w.,])(?:"
        + "|".join(re.escape(text) for text in sorted(texts, key=len, reverse=True))
        + r")(?!\w|[.,]\d)"
    )


def _baseline_ranges(facts):
    """(written form, (low id, high id)) for each baseline range: facts named *_low and *_high."""
    ranges = []
    for low_id, (low, unit) in facts.baseline.items():
        high_id = low_id[: -len("_low")] + "_high"
        if (
            not low_id.endswith("_low")
            or unit not in BASELINE_FORMS
            or facts.baseline.get(high_id, (0, ""))[1] != unit
        ):
            continue
        form = BASELINE_FORMS[unit].format(
            low=_figure_pattern(low), high=_figure_pattern(facts.baseline[high_id][0])
        )
        ranges.append((re.compile(form, re.I), (low_id, high_id)))
    return ranges


def _type_scoped(sentence, facts):
    """A sentence about the type, not this airframe, scoped to these records."""
    return _about_type(sentence, facts) and RARE_SCOPE.search(sentence)


def _baselined(text, facts):
    """(text, issues): the text for the number checks, with baseline ranges blanked in their
    written form inside a sentence about the type scoped to these records that compares with no
    aircraft and names no limit or record; and each baseline figure left in such a sentence,
    written alone, out of order, or beside a comparison, a limit, or another speed or altitude.
    That figure is refused even where a reading would support the number ("cannot fly faster
    than 430 knots" beside a reading of 651 knots). Elsewhere a baseline figure stays visible
    and the number checks refuse it as unsupported: it is never this aircraft's reading or the
    type's performance.
    """
    ranges = _baseline_ranges(facts)
    if not ranges:
        return text, []
    figures = {figure for figure, _unit in facts.baseline.values()}
    issues = []
    for start, end in sentence_spans(text):
        sentence = text[start:end]
        if not _type_scoped(sentence, facts):
            continue
        if not BASELINE_REFUSED.search(sentence):
            blanked, bound = sentence, []
            for form, ids in ranges:
                if form.search(blanked):
                    blanked = form.sub(
                        lambda match: FIGURE.sub(lambda figure: "x" * len(figure.group()), match.group()),
                        blanked,
                    )
                    bound.extend(ids)
            # Any speed or altitude left beside the ranges is a comparison: none of them binds.
            if bound and not BASELINE_OTHER.search(blanked):
                facts.bound.update(bound)
                sentence = blanked
                text = text[:start] + sentence + text[end:]
        issues.extend(
            "Unsupported baseline figure: " + match.group()
            for match in FIGURE.finditer(sentence)
            if _figure_value(match) in figures
        )
    return text, issues


def _figure_value(match):
    """A FIGURE match's number ("30,000" is 30000.0), or None for a date or a spelled number."""
    try:
        return _number(match.group())
    except ValueError:
        return None


def _prose_issues(text, facts):
    issues = []
    # Characters are checked as written: the text is published as written, not as normalized.
    allowed = EXTRA_CHARACTERS | set(PUNCTUATION) | set(VULGAR_FRACTIONS) | facts.characters
    unknown = sorted(
        {
            character
            for character in unicodedata.normalize("NFC", text)
            if (ord(character) > 126 or (ord(character) < 32 and character != "\n"))
            and character not in allowed
        }
    )
    if unknown:
        issues.append(
            "Unsupported characters: " + " ".join(f"U+{ord(character):04X}" for character in unknown)
        )
    cleaned = _clean(text)
    if URLISH.search(cleaned):
        issues.append("Prose contains a URL, address, or markup")
    # The packet's own caveats are restated freely; sourced type facts keep their claims checked.
    cleaned = _restated(cleaned, facts.caveats)
    issues.extend(location_issues(cleaned))
    baselined, baseline_issues = _baselined(cleaned, facts)
    issues.extend(_number_issues(_sourced(baselined, facts), facts))
    issues.extend(baseline_issues)
    issues.extend(claim_issues(cleaned, facts))
    issues.extend(name_issues(cleaned, facts))
    return list(dict.fromkeys(issues))


def _checker(facts, failure=None):
    def check(text):
        if not isinstance(text, str):
            return ["Prose must be text"]
        if len(text) > MAX_PROSE:
            return ["Prose exceeds the length the checks accept"]
        if facts is None:
            return [failure]
        try:
            return _prose_issues(text, facts)
        except CHECK_ERRORS as error:
            # A check that cannot run rejects the text rather than letting it through.
            return [f"Prose checks could not run ({type(error).__name__})"]

    return check


def _facts_for(packet_or_facts):
    """(Facts, None) for a packet or story facts, or (None, failure) when they cannot be indexed."""
    try:
        packet = facts_packet(packet_or_facts) if is_story_facts(packet_or_facts) else packet_or_facts
        facts = packet_facts(packet)
        facts.entry_phrases = (*_list_phrases(packet_or_facts), *_association(packet_or_facts))
        return facts, None
    except CHECK_ERRORS as error:
        return None, f"Prose checks could not run ({type(error).__name__})"


def prose_issues(text, packet):
    """Problems that block one prose field; an empty list means it passes."""
    return prose_checker(packet)(text)


def prose_checker(packet):
    """prose_issues() for many texts against one packet or story facts, indexing it once.

    Plane Alert DB's own wording reads as "the entry" (see LIST_TOKEN).
    """
    return _masked(_checker(*_facts_for(packet)), _list_phrases(packet))


def _vocabulary(values):
    return frozenset(
        word.lower() for value in values if isinstance(value, str) for word in re.findall(r"[A-Za-z]+", value)
    )


def _phrase_pattern(phrases):
    """Whole phrases, case as written, at word boundaries and with plurals; None when none."""
    return _bounded(phrases, flags=0, plural=True)


def _mission(text, type_names, facts):
    """A mission or purpose word in a type fact, the type's own names (type_names, a _bounded
    pattern or None: "Combat Sent"), the site's fixed phrases (SITE_PHRASES), and design roles
    ("a surveillance aircraft") aside."""
    text = _blank_roles(text, facts, profile=True)
    if type_names:
        text = _blank(text, type_names)
    return PROFILE_MISSION.search(text)


def profile_checker(packet, *, vocabulary=(), phrases=()):
    """profile_issues() for many sentences against one packet or story facts, indexing it once."""
    facts, failure = _facts_for(packet)
    names = _vocabulary(vocabulary)
    named_phrases = _phrase_pattern(phrases)
    type_names = _bounded(facts.type_phrases, plural=True) if facts else None

    def check(text, source=None):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_PROSE:
            return ["Profile fact must be text"]
        if facts is None:
            return [failure]
        named = (
            names | _vocabulary(source.get(key) for key in ("publisher", "title"))
            if isinstance(source, dict)
            else names
        )
        cleaned = _clean(text)
        issues = []
        if URLISH.search(cleaned):
            issues.append("Prose contains a URL, address, or markup")
        if INSTRUCTION.search(cleaned):
            issues.append("Profile fact reads as an instruction")
        try:
            issues.extend(location_issues(cleaned))
            issues.extend(claim_issues(cleaned, facts, profile=True))
            # A phrase that names a type names nothing else: its words support no other name.
            unnamed = _blank(cleaned, named_phrases) if named_phrases else cleaned
            if _mission(unnamed, type_names, facts):
                issues.append("Profile fact states a mission or purpose")
            issues.extend(name_issues(unnamed, facts, named, strict_opening=True))
        except CHECK_ERRORS as error:
            issues.append(f"Prose checks could not run ({type(error).__name__})")
        return list(dict.fromkeys(issues))

    return check


def profile_issues(text, packet, *, source=None, vocabulary=(), phrases=()):
    """Why a researched type sentence cannot become a sourced profile fact; [] when it can.

    Profile facts reach public stories as sourced type background, so they pass the
    location, claim and name checks story prose passes, against the story's packet or
    facts, and carry no URL or markup. Names may also come from the source's publisher
    and title, from the caller's type vocabulary words, and from its phrases, which
    count only whole and as written. A regional town opening the sentence gets no
    first-word exemption (name_issues with strict_opening). A sentence that reads as an
    instruction, or that names a mission or purpose for the type's flights
    (PROFILE_MISSION), is refused.
    """
    return profile_checker(packet, vocabulary=vocabulary, phrases=phrases)(text, source)


# Story fields: a headline, a dek, paragraphs and an optional deterministic SEO title.
FIELD_LIMITS = {"headline": (40, 90), "dek": (70, 155), "paragraph": (100, 750), "seo_title": (10, 60)}
PARAGRAPHS = (1, 5)
FINAL_PUNCTUATION = {".", ":", ";", ","}
DASH = re.compile("[‒-―]|\\s--\\s|(?<=\\w)--(?=\\w)")
BANNED_PHRASES = re.compile(
    r"\b(?:type match|qualifying reports?|analysis window|saved reports|not (?:been )?independently verified)\b",
    re.I,
)
# A story is about the aircraft, never this site's own record keeping: no counts of its
# stories, no debut, no days counted without a type or until an aircraft returned, and
# no "the site's" limit, check or finding.
SELF_REFERENCE = re.compile(rf"\b(?:{SITE_WORDS}|(?:the|this) site's)\b", re.I)
# The site's detection machinery never shows in a story: no threshold, and no flag unless it
# is the aircraft database's military flag, which is a fact of the record.
SITE_ACTIONS = re.compile(
    r"\bthresholds?\b|\bdetectors?\b|\bcut-?offs?\b|\btripped (?:an? |the |its )?alerts?\b|\bWTF ADS-?B\b"
    # A flag carrier is an airline and a national flag a country's; neither is a check.
    r"|(?<!military )(?<!military-)(?<!interesting )(?<!database )(?<!database's )(?<!national )"
    r"\bflag(?:s|ged)?\b(?!\s+carriers?\b)(?!\s+(?:\S+\s+)?(?:as\s+)?military\b)",
    re.I,
)


@functools.cache
def _site_name(name):
    """The configured site name as a whole phrase, in any case: a story never names the site."""
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.I)


def _names_site(sentence):
    name = site_identity.configured_name()
    return bool(name and _site_name(name).search(sentence))


# The record's one hedge is its required sentence; another about wind or a faulty reading
# repeats it (paragraphs:repeated_limitations).
RECORD_HEDGE = re.compile(
    r"\b(?:(?:tail|head)?winds?|not (?:an? |the )?air ?speed|fault\w*|errors?|erroneous|glitch\w*|misread\w*"
    r"|ruled out|bad (?:data|readings?|reports?))\b",
    re.I,
)
# Season records (curation.RECORD_KINDS) and certified-limit stories (rules.RATED_KINDS).
RECORD_KINDS = curation.RECORD_KINDS
RATED_KINDS = rules.RATED_KINDS
# The one sentence a story of these kinds carries in a paragraph, as written, in place of the
# evidence card's longer caveats: the speed record's wind and faulty-reading hedge, a
# certified-limit story's doubt about its reading, a winds aloft day's unmeasured wind, and where a
# foreign military listing comes from. Both writers insert it verbatim (editorial's builders,
# public_writer.assemble for AI prose), so the checks find it by equality and read the rest of the
# story without it. A speed's names how its airspeed was obtained, by its lim.airspeed fact's
# basis (AIRSPEED_ORIGINS); a foreign military listing's names its fm.basis.
REQUIRED_SENTENCES = {
    "seasonal_speed_record": "Ground speed includes any tailwind, and a faulty reading can't be ruled out.",
    "above_rated_altitude": (
        "A pressure-altitude report is not a verified flight level, and a short excursion can read above the "
        "limit."
    ),
    "above_rated_speed": "The airspeed is {airspeed_origin}, so a faulty reading can't be ruled out.",
    "winds_aloft_day": "The wind itself was not measured; the difference in ground speeds is what the data show.",
    "foreign_military": "The listing comes from {fm_basis}, not from anything the flight itself transmitted.",
}
AIRSPEED_ORIGINS = {
    "derived": "an estimate from ground speed and modelled wind",
    "telemetry": "the aircraft's own report",
}
# A reading past a certified figure is no offence, and the evidence card names the wind service.
RATED_JUDGEMENT = re.compile(
    r"\b(?:violat\w*|illegal\w*|unlawful\w*|bust(?:s|ed|ing)?|breach\w*|infring\w*|overspeed\w*"
    r"|overstress\w*|investigat\w*|against (?:the )?(?:rules|regulations|law)"
    r"|broke (?:its|the|the type's|their) (?:own )?(?:(?:speed|altitude|certified) )?limits?"
    r"|(?:faster|higher) than (?:it is|it's|it was|they are|it were) (?:allowed|permitted)"
    r"|(?:not )?(?:allowed|permitted) to)\b",
    re.I,
)
# The reports over a limit are counted, never read as a value held for a span of time: "held
# 42,500 ft for 75 seconds" (claim:limit_held).
HELD_VALUE = re.compile(
    r"\b(?:held|holding|holds|sustain(?:s|ed|ing)?|maintain(?:s|ed|ing)?|kept|stayed)\b(?:[^.;:]|\.(?=\d)){0,80}?"
    r"\bfor\s+(?:about\s+|around\s+|over\s+|more than\s+|nearly\s+|at least\s+|almost\s+)?"
    r"(?:\d[\d,.]*|an?|one|two|three|four|five|six|seven|eight|nine|ten|[a-z]+-[a-z]+)\s*"
    r"(?:seconds?|secs?|minutes?|mins?|s|min)\b",
    re.I,
)
# A certified figure goes by its own name (claim:limit_name): the ceiling is the maximum operating
# altitude, never a service ceiling, and a type whose speed limits are Vmo and Mmo has no
# never-exceed speed.
SERVICE_CEILING = re.compile(r"\bservice ceilings?\b", re.I)
# Words that give a certificate figure; a sentence with one never names a class phrase
# (claim:class_limit).
CERTIFIED_WORDS = re.compile(
    r"\b(?:certif\w*|maximum operating|never[- ]exceed|data sheet|service ceiling|Vmo|Mmo|Vne)\b", re.I
)
WIND_SERVICE = re.compile(r"\bopen[- ]?meteo\b", re.I)
# Kinds whose headline is fixed, with its forms in order: the first whose names the story facts
# give is the headline (fixed_headline). Both writers use it, so it is not free text: no
# free-text headline rule applies to it, the checks require it word for word
# (headline:not_fixed), the variety guard reads its shared skeleton as the form, and the AI
# writer takes it as given. A first sighting names the type in words, or by its class phrase
# and type code when the database gives no name, or by the code alone when the designator
# table has no class for it ("<Type> spotted for the first time"). An operator's first names the
# operator, a winds aloft day its date, and an unlisted address the address in capitals.
FIXED_HEADLINES = {
    "first_type_here": (
        "{type} spotted for the first time",
        "{type_class_cap} {type_code} spotted for the first time",
        "{type_code} spotted for the first time",
    ),
    "first_country_here": (
        "First {country} address spotted: {type}",
        "First {country} address spotted: {ident}",
    ),
    "first_operator_here": ("{operator} spotted for the first time",),
    "winds_aloft_day": ("Strong winds aloft on {day_long}",),
    "unregistered_address": ("Address {hex_upper} flew with no registry entry",),
}
FIXED_HEADLINE_KINDS = frozenset(FIXED_HEADLINES)
# A watchlist headline leads with the aircraft and the name its entry carries: "{type} {ident}
# linked to {association} by Plane Alert DB", or for an entry that names nobody, its category or
# tags: "{type} {reg}, filed under "{category}", spotted". The older forms, "{type} {reg}, listed
# under {name}, spotted" and "{name}-listed {type} {reg} spotted", stay accepted, since stories
# already published carry them (headline:watchlist_form).
WATCHLIST_HEADLINE = re.compile(
    r"^(?P<linked>.+?) linked to (?P<association>.+?) by Plane Alert DB\b"
    r"|^(?P<aircraft>.+?), (?:(?:listed|filed) under|tagged) (?P<entry>.+?), spotted\b"
    r"|^(?P<listed>.+?)-listed (?P<subject>.+?) spotted\b"
)
# A foreign military headline leads with the aircraft and the State its listing names: "{type}
# {ident}, listed as military from {country}, spotted" (headline:foreign_military_form).
FOREIGN_MILITARY_HEADLINE = re.compile(
    r"^(?P<aircraft>.+?), listed as military from (?P<country>.+?), spotted\b"
)
# Kinds whose headline is a shared skeleton with the story's names in it. With the fixed kinds,
# the variety guard reads their shared form as the house form, never as a repeat.
SKELETON_HEADLINE_KINDS = frozenset({"watchlist_match", "foreign_military"})
HOUSE_HEADLINE_KINDS = FIXED_HEADLINE_KINDS | SKELETON_HEADLINE_KINDS
# A list of three or more written "A, B and C": the items are short and parallel, so a
# parenthetical ("the jet, listed in the database, flew north and landed") or a clause
# ("on 8 September, it climbed and left") is left alone. Missing a list beats rejecting prose.
_LIST_WORD = r"(?!(?:and|or|but|nor|so|yet)\b)[^\s,;:()]+"
_LIST_ITEM = rf"{_LIST_WORD}(?:\s{_LIST_WORD}){{0,2}}"
SERIAL_LIST = re.compile(
    rf"(?<![\w,])(?P<items>{_LIST_ITEM}(?:,\s{_LIST_ITEM})*),\s(?P<last>{_LIST_ITEM})\s(?P<conj>and|or)\s"
    rf"(?P<end>{_LIST_ITEM})(?=[\s,.;:!?]|$)"
)
# An item that opens a clause rather than naming a thing: a subject pronoun, a relative
# pronoun, a conjunction or a preposition ("between 96 and 123 knots" is a range).
CLAUSE_OPENERS = re.compile(
    r"^(?:it|its|it's|they|their|he|she|we|there|this|that|these|those|which|who|whose|whom|where|when"
    r"|while|if|because|as|than|so|but|and|or|nor|yet|between|from|at|in|on|near|over|under|with|by|to"
    r"|of|for|after|before|since|during|through|past|about|around|above|below|into|onto|off|out|up"
    r"|down|across|along|against|among|beside|beyond|despite|except|inside|outside|toward|towards"
    r"|until|upon|via|within|without|like|per|versus|not|no|then|also|still|just|only|even|such"
    r"|including|especially|namely|mainly|mostly|rather)$",
    re.I,
)
# Words that read as a verb: a past tense in -ed, or one of the irregular pasts stories use.
# A word in -s ("turns", "tags") may be either, so it fits any list.
VERB_LIKE = re.compile(
    r"^(?:[a-z]{3,}ed|flew|left|went|came|rose|fell|held|kept|sent|set|broke|ran|sat|began|ended"
    r"|made|took|gave|got|put|read|saw|stood|drew|hit|lay|led|met|spent|swung|wore|won|hung)$",
    re.I,
)
EITHER_LIKE = re.compile(r"^[a-z]{3,}s$", re.I)
# A time to the second reads as a log line; a story gives the minute when the moment matters.
CLOCK_SECONDS = re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b")
DAY_COUNT = re.compile(
    r"\babsence\b|\bdays? without\b|\b\d[\d,]*-day\b|\bwent \d[\d,]* days\b"
    r"|\b\d[\d,]* (?:earlier )?days (?:of|in) (?:these |the )?records\b|\b\d[\d,]* days of records\b"
    r"|\b(?:broke|breaks|ended|ends) (?:that |the |a )?run\b",
    re.I,
)
DATABASE_ATTRIBUTION = re.compile(
    r"\b(?:aircraft database|database lists|database records?|by (?:its|the) database|listed as|registered as"
    r"|database gives|database has|according to the database|database describes|database listings?"
    r"|database identif(?:ies|ied|y)|(?:aircraft )?metadata lists|listed in the (?:aircraft )?database"
    r"|in the (?:aircraft )?database|database entry)\b",
    re.I,
)
SUPERLATIVE = re.compile(
    r"\b(?:largest|biggest|fastest|slowest|highest|lowest|oldest|newest|youngest|rarest|longest|heaviest)\b",
    re.I,
)
FIRST_CLAIM = re.compile(
    r"\bfirst\s+(?:[\w'-]+\s+){0,3}?(?:aircraft|airframe|airframes|airliner|jet|plane|helicopter|type|types"
    r"|appearance|sighting|visit|time|story|stories|address|addresses)\b",
    re.I,
)
STORY_SCOPE = re.compile(rf"\b(?:{SCOPE_WORDS}|this season)\b", re.I)
# A headline or dek carries no scope at all, the site's own name for its records included:
# "debuts in these flight records", "new on the site", "a first for this journal".
HEADING_SCOPE = re.compile(
    rf"\b(?:{SCOPE_WORDS}|this season|(?:on|to|for|in|at) (?:this|the|our) (?:site|website|journal)"
    r"|this (?:site|website|journal)|our records)\b",
    re.I,
)
HEDGE = re.compile(
    r"\b(?:(?-i:may)|might|could|likely|possibly|perhaps|" + LIKENESS_HEDGES + r"|looks? like|like an?"
    r"|one possible|fits?|suggests?|appears? to|[a-z]+-(?:like|style))\b",
    re.I,
)
LIMIT_SENTENCE = re.compile(
    r"\b(?:cannot|can't|does not|do not|doesn't|did not|is not|are not|isn't|aren't)\s+(?:by itself\s+|alone\s+)?"
    r"(?:show|establish|confirm|prove|identify|tell|determine|verify|reveal|say)\b"
    r"|\bnot an? (?:emergency|distress|determination)\b|\bare not in the data\b",
    re.I,
)

# Stock frames that say nothing about the flight, and the watchlist closer the page's limitations
# already carry; a story uses none of them (filler:frame).
FILLER_FRAMES = re.compile(
    r"\b(?:gives a sense of the flight|a useful design detail to remember|worth noting"
    r"|adds (?:a|some) (?:context|color)|rounds out the picture|the entry does not show who was aboard)\b",
    re.I,
)
# The page shows the type box (tp. facts) beside the story, so a sentence that shares this many
# words in a row with one repeats it; a shorter fact repeats when a sentence carries all of its
# words in order (box:reused, and the box itself in publication).
BOX_RUN = 10
# A sentence restates the kind's context line (ctx.kind) when its figures are the line's and at
# least this share of the line's content words and of its own are shared (context:missing).
CONTEXT_REPHRASE = 0.8


def is_caveat(sentence, profiles=()):
    """A sentence that says what the data cannot show: a limitation or an epistemic hedge. A
    restated type fact (profiles) is never one."""

    def limits(text):
        return bool(LIMIT_SENTENCE.search(text) or EPISTEMIC.search(text))

    # Blanking a restatement can only remove a match, so only a match needs it.
    return limits(sentence) and (not profiles or limits(_restated(sentence, profiles)))


def _paragraph_sentences(paragraphs):
    """(paragraph index, sentence) for each sentence of the text paragraphs, in order."""
    return [
        (index, paragraph[start:end].strip())
        for index, paragraph in enumerate(paragraphs)
        if isinstance(paragraph, str)
        for start, end in sentence_spans(paragraph)
    ]


def last_sentence(paragraphs):
    """The story's final sentence, or ""."""
    found = _paragraph_sentences(paragraphs if isinstance(paragraphs, list) else [])
    return found[-1][1] if found else ""


def closes_on_caveat(closing, required=None, profiles=()):
    """Whether a story's final sentence is a caveat or the kind's required sentence, spacing
    aside; both writers keep it inside the story (paragraphs:closing_caveat). A restated type
    fact (profiles) is never a caveat."""
    return bool(closing) and (" ".join(closing.split()) == required or is_caveat(closing, profiles))


def insert_before_last(paragraphs, sentence):
    """paragraphs with sentence placed before the last sentence of the last paragraph that has two
    or more sentences; at the start of the last paragraph when none has."""
    paragraphs = list(paragraphs)
    for index in range(len(paragraphs) - 1, -1, -1):
        spans = sentence_spans(paragraphs[index])
        if len(spans) >= 2:
            cut = spans[-1][0]
            text = paragraphs[index]
            paragraphs[index] = " ".join(f"{text[:cut]} {sentence} {text[cut:]}".split())
            return paragraphs
    if paragraphs:
        paragraphs[-1] = f"{sentence} {paragraphs[-1]}".strip()
    return paragraphs


# Story-fact fields that the packet walker already understands by name.
FACT_FIELDS = {
    "association_label",
    "registration",
    "callsign",
    "icao_address",
    "type_code",
    "type_description",
    "squawk",
    "code",
    "advisory",
    "neutral_category",
    "emergency",
    "emitter_category",
    "registration_country",
    "allocation_country",
    "database_year",
}
STORY_HEDGE_TERMS = re.compile(r"[a-z][a-z-]{3,}")
# Lowercase words that a sentence-case headline never capitalizes mid-sentence.
TITLE_WORDS = {
    "near",
    "logged",
    "reports",
    "flies",
    "shows",
    "holds",
    "makes",
    "over",
    "after",
    "again",
    "back",
}
# Capitalized mid-headline, these words mark title case. "May" is left out: it is the month.
TITLE_CASE_WORDS = frozenset((STOPWORDS | TITLE_WORDS) - {"may"})
# Words in a hypothesis that also name ordinary facts; only its other words mark the hypothesis.
HYPOTHESIS_GENERIC = {
    "data",
    "aircraft",
    "flight",
    "type",
    "wrong",
    "high-altitude",
    "aerial",
    "rising",
    "database",
    "configuration",
    "aboard",
    "such",
    "long",
    "area",
    "track",
    "racetrack",
    "registry",
    "registration",
    "address",
    "transponder",
    "another",
    "entry",
    "site",
}


def _list_shape(item):
    """How a list item reads: "verb" ("climbed"), "noun" ("the jet"), "any" ("turns", "tags"), or
    None for a clause ("it climbed", "Airbus flew") or a phrase that opens one ("between 96")."""
    words = [word.strip("\"'.,!?") for word in item.split()]
    if not words or not words[0] or CLAUSE_OPENERS.match(words[0]):
        return None
    if VERB_LIKE.match(words[0]):
        return "verb"
    if any(VERB_LIKE.match(word) for word in words[1:]):
        return None
    return "any" if EITHER_LIKE.match(words[0]) else "noun"


def serial_comma_issues(text):
    """Lists of three or more items written without the comma before "and" or "or".

    The leftmost item may carry the sentence's subject ("It climbed"), so its tail is
    tried too. Two identifiers after a plural ("a pair of C-17s, 05-5140 and 06-6159")
    name the pair rather than extend a list.
    """
    issues = []
    for start, end in sentence_spans(text):
        issues.extend(_serial_comma_issues(_sentence_body(text[start:end])))
    return issues


def _sentence_body(sentence):
    """A sentence without its closing punctuation, as the list check reads it."""
    return sentence.rstrip(".!?\"')")


def _missing_serial_commas(text):
    """Each list in one sentence body that lacks its serial comma, as a SERIAL_LIST match."""
    for match in SERIAL_LIST.finditer(text):
        first, *middle = match["items"].split(", ")
        last, end = match["last"], match["end"]
        shapes = {_list_shape(item) for item in (*middle, last, end)}
        if None in shapes or len(shapes - {"any"}) > 1:
            continue
        fits = shapes - {"any"} or {"verb", "noun"}
        words = first.split()
        tails = [_list_shape(" ".join(words[index:])) for index in range(len(words))]
        if not any(tail in fits or tail == "any" for tail in tails):
            continue
        if not middle and _identifier(last) and _identifier(end) and not _identifier(words[-1]):
            continue
        yield match


def _serial_comma_issues(text):
    return ["Missing serial comma: " + match.group().strip()[:80] for match in _missing_serial_commas(text)]


def with_serial_commas(text):
    """The text with the serial comma added to each list serial_comma_issues() finds.

    Sourced type facts arrive in the source's own punctuation; a story that quotes one
    must still carry the comma, so the fact gets it when its profile is made or loaded.
    """
    positions = [
        start + match.end("last")
        for start, end in sentence_spans(text)
        for match in _missing_serial_commas(_sentence_body(text[start:end]))
    ]
    for position in sorted(positions, reverse=True):
        text = text[:position] + "," + text[position:]
    return text


def _identifier(item):
    """A registration, serial or address written as one token: "05-5140", "N51EM"."""
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9-]*", item)) and bool(re.search(r"\d", item))


def is_story_facts(value):
    """A story_facts.build() result: a kind and a non-empty list of facts that carry ids."""
    items = value.get("facts") if isinstance(value, dict) else None
    return (
        isinstance(items, list)
        and bool(items)
        and "kind" in value
        and all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in items)
    )


# Facts whose value is a name of its own kind, with the packet key it rides under: a second
# registry's type ("Boeing 757-200") is never the story's type, so it is no type phrase; what
# Wikidata calls a watchlist entry's organization supports itself restated as written
# (_organization_restatements), never its words alone, and the person check still reads it.
NAMED_FACT_KEYS = {"tc.type": "registry_type", "wl.who": "organization"}


def facts_packet(facts):
    """A story_facts.build() result in the packet shape that packet_facts() reads.

    Measurements keep their unit, field and time; identifiers keep their packet field
    names; type names and aliases count as type phrases; limitations may be restated
    verbatim. Sourced profile sentences supply names, and their figures only to
    sentences about the type. Baseline figures support only a sentence about the type
    scoped to these records.
    """
    entries, window, episode = [], {}, {}
    for item in facts["facts"]:
        identifier, value = item["id"], item.get("value")
        field, unit = item.get("field"), item.get("unit")
        if value is None or isinstance(value, bool):
            continue
        if identifier in {"obs.start", "obs.end"}:
            window["observed_" + identifier[4:]] = value
            continue
        if identifier in {"ep.start", "ep.end"}:
            episode[identifier[3:] + "_utc"] = value
            continue
        if item.get("basis") == "baseline" and unit and finite(value):
            # The type's usual ground speed and band in these records: a population figure, not a
            # measurement, so it supports no reading (_baselined).
            entry = {"baseline": value, "unit": unit}
        elif unit and finite(value):
            entry = {"field": field or "", "value": value, "unit": unit}
            if item.get("scope"):
                entry["scope"] = item["scope"]
            if item.get("at"):
                entry["at_utc"] = item["at"]
        elif field == "profile":
            entry = {"profile_text": value}
        elif field == "database_year":
            entry = {"database_year": value}
        elif field in FACT_FIELDS and isinstance(value, str):
            entry = {field: value}
        elif identifier in NAMED_FACT_KEYS and isinstance(value, str):
            entry = {NAMED_FACT_KEYS[identifier]: value}
        elif identifier == "nb.peak_at":
            entry = {"peak_at": value}
        elif field == "date" and isinstance(value, str):
            entry = {"at": value}
        elif identifier == "code.meaning" and isinstance(value, str):
            entry = {"code_definition": value}
        elif identifier == "wl.category_note" and isinstance(value, str):
            # What a Plane Alert DB category collects reads like its neutral label: beside
            # the list it describes the entry ("aircraft linked to famous people"), not a claim.
            entry = {"category": value}
        elif identifier == "code.sequence" and isinstance(value, list):
            entry = {"codes": [{"code": str(part)} for part in value if isinstance(part, str)]}
        elif isinstance(value, list):
            entry = {"labels": [str(part) for part in value if isinstance(part, (str, int, float))]}
        else:
            entry = {"label": value if isinstance(value, str) else str(value)}
        # The fact's id rides beside its value (a skipped key), so the checks can say which
        # facts a text relied on (bound_facts).
        entry["id"] = identifier
        entries.append(entry)
        alias_key = NAMED_FACT_KEYS.get(identifier, "desc")
        for alias in item.get("aliases") or ():
            entries.append({alias_key: alias})
        if item.get("category"):
            entries.append({"desc": item["category"]})
            # A category's modifier names the type too: "double-deck" in "double-deck airliner".
            # claim_issues reads one that ends in a purpose as a claim (_purpose_modifiers).
            modifier = item["category"].rsplit(" ", 1)[0] if " " in item["category"] else ""
            if len(modifier) >= 4:
                entries.append({"desc": modifier})
        for key in ("short", "state", "telephony", "designator", "name", "prefix"):
            if isinstance(item.get(key), str):
                entries.append({alias_key if identifier.endswith(".type") else "label": item[key]})
        if identifier in NAMED_FACT_KEYS and isinstance(item.get("code"), str):
            entries.append({alias_key: item["code"]})
        if identifier == "wl.who" and isinstance(item.get("label"), str):
            # The organization's label is one more name for the entry's association.
            entries.append({"association_label": item["label"]})
        for day in item.get("dates") or ():
            entries.append({"at": day})
        if isinstance(item.get("code"), str) and identifier == "code.meaning":
            entries.append({"code": item["code"]})
    if window:
        entries.append(window)
    if episode:
        entries.append(episode)
    packet = {
        "kind": facts.get("kind"),
        "facts": entries,
        "limitations": [item for item in facts.get("limits", []) if isinstance(item, str)],
        "hypotheses": [item for item in facts.get("hypotheses", []) if isinstance(item, str)],
        "sources": [
            {"publisher": source.get("publisher"), "title": source.get("title")}
            for source in facts.get("sources", [])
            if isinstance(source, dict)
        ],
    }
    if any(item["id"].startswith("wl.") for item in facts["facts"]):
        packet["watchlist_listed"] = True
    # A foreign military listing (fm.country) rests on the database's military flag or a State's
    # military address block, either of which the story may state as it states the flag.
    if any(
        (item["id"] == "db.military" and item.get("value") is True)
        or (item["id"] == "fm.country" and item.get("value"))
        for item in facts["facts"]
    ):
        packet["military_flag"] = True
    return packet


def _story_values(facts):
    """{id: fact} for story facts; public_prose sits below story_facts, so it keeps this small index."""
    return {item["id"]: item for item in facts["facts"]}


def _profile_text(value):
    return " ".join(str(value).split())


def profile_texts(facts):
    """The sourced type facts (tp.) of story facts as the checks read them, spaces collapsed;
    Facts.profiles holds the same text for a packet."""
    return [_profile_text(item["value"]) for item in facts["facts"] if item["id"].startswith("tp.")]


def fixed_headline(facts):
    """(headline, ids of the facts it names) for story facts whose kind's headline is fixed
    (FIXED_HEADLINES), from the first form whose names the facts give; else None.

    The names are the engine's slots (editorial._identity_slots): the type in words unless
    it names a whole group, the class phrase of a type with no name, the type code, the
    allocation country unless it is home, and the registration, callsign, or address; then the
    operator of a first operator sighting, a winds aloft day as "5 November 2026", and the
    address in capitals.
    """
    forms = FIXED_HEADLINES.get(facts.get("kind")) if is_story_facts(facts) else None
    if not forms:
        return None
    values = _story_values(facts)
    kind_type, country = values.get("id.type") or {}, values.get("alloc.country") or {}
    klass = (values.get("id.type_class") or {}).get("value")
    day = moment((values.get("wind.day") or {}).get("value"))
    address = (values.get("id.hex") or {}).get("value")
    names = {
        "type": (None if kind_type.get("group") else kind_type.get("value"), "id.type"),
        "type_class_cap": (
            klass[:1].upper() + klass[1:] if isinstance(klass, str) else None,
            "id.type_class",
        ),
        "type_code": ((values.get("id.type_code") or {}).get("value"), "id.type_code"),
        "country": (None if country.get("home") else country.get("value"), "alloc.country"),
        "ident": next(
            (
                (values[key]["value"], key)
                for key in ("id.registration", "id.callsign", "id.hex")
                if (values.get(key) or {}).get("value")
            ),
            (None, None),
        ),
        "operator": ((values.get("fs.operator") or {}).get("value"), "fs.operator"),
        "day_long": (f"{day.day} {MONTH_NAMES[day.month - 1]} {day.year}" if day else None, "wind.day"),
        "hex_upper": (address.upper() if isinstance(address, str) else None, "id.hex"),
    }
    for form in forms:
        used = re.findall(r"\{(\w+)\}", form)
        if all(isinstance(names[name][0], str) and names[name][0].strip() for name in used):
            return form.format(**{name: names[name][0] for name in used}), tuple(
                names[name][1] for name in used
            )
    return None


def _watchlist_headline_issue(headline, facts):
    """headline:watchlist_form unless a watchlist headline takes the house form: the aircraft by
    its registration or identifier, the name its entry carries (or, for an entry that names
    nobody, its category or a tag), then "spotted".

    A story whose facts carry no entry (the match allowed no public claim) has none to name.
    """
    values = _story_values(facts)
    # An entry that names someone (wl.association; vague ones such as "Private" are never
    # facts) is headlined by that name; only an entry that names nobody by its category or tags.
    association = (values.get("wl.association") or {}).get("value")
    candidates = (
        (association,)
        if isinstance(association, str) and association.strip()
        else (
            *((values.get(key) or {}).get("value") for key in ("wl.list_category", "wl.category")),
            *((values.get("wl.tags") or {}).get("value") or ()),
        )
    )
    names = [name for name in candidates if isinstance(name, str) and name.strip()]
    if not names:
        return None
    match = WATCHLIST_HEADLINE.match(headline) if isinstance(headline, str) else None
    if not match:
        return "headline:watchlist_form"
    aircraft = (match["linked"] or match["aircraft"] or match["subject"]).lower()
    entry = (match["association"] or match["entry"] or match["listed"]).lower()
    if not _names_ident(aircraft, values) or not any(name.lower() in entry for name in names):
        return "headline:watchlist_form"
    return None


def _names_ident(text, values):
    """Whether lowercase text names the aircraft by its registration, callsign, or address."""
    idents = [(values.get(key) or {}).get("value") for key in ("id.registration", "id.callsign", "id.hex")]
    return any(isinstance(ident, str) and ident.lower() in text for ident in idents)


def _foreign_military_headline_issue(headline, facts):
    """headline:foreign_military_form unless a foreign military headline takes the house form: the
    aircraft by its registration or identifier, then "listed as military from" the State its
    listing names, then "spotted". A story whose facts name no State has none to name."""
    values = _story_values(facts)
    country = (values.get("fm.country") or {}).get("value")
    if not isinstance(country, str) or not country.strip():
        return None
    match = FOREIGN_MILITARY_HEADLINE.match(headline) if isinstance(headline, str) else None
    if (
        not match
        or not _names_ident(match["aircraft"].lower(), values)
        or country.lower() not in match["country"].lower()
    ):
        return "headline:foreign_military_form"
    return None


def _story_fields(story):
    """(name, text) for each field a story carries; the SEO title only when it has one."""
    paragraphs = story.get("paragraphs") if isinstance(story.get("paragraphs"), list) else []
    fields = [("headline", story.get("headline")), ("dek", story.get("dek"))]
    if story.get("seo_title") is not None:
        fields.append(("seo_title", story["seo_title"]))
    fields.extend((f"paragraph[{index}]", text) for index, text in enumerate(paragraphs))
    return fields


def _shape_issues(story, names=(), listed=(), *, fixed_headline=False, question_allowed=False):
    """Field shapes; names are fact-derived phrases a headline keeps in their own capitals.

    A fixed headline ("Bell 407 spotted for the first time") is the engine's, not free
    text: none of the headline rules apply to it. A headline may ask a question only when
    question_allowed: the facts hold a TCAS advisory.
    """
    if not isinstance(story, dict):
        return ["story:not_an_object"]
    issues = []
    paragraphs = story.get("paragraphs")
    if not isinstance(paragraphs, list) or not PARAGRAPHS[0] <= len(paragraphs) <= PARAGRAPHS[1]:
        issues.append("paragraphs:count")
    # The one place a fixed headline leaves the rules: it is not among the fields they read.
    fields = [
        (name, text) for name, text in _story_fields(story) if not (fixed_headline and name == "headline")
    ]
    for name, text in fields:
        limit = FIELD_LIMITS["paragraph" if name.startswith("paragraph") else name]
        if not isinstance(text, str):
            issues.append(f"{name}:not_text")
            continue
        if text != text.strip() or "\n" in text or "  " in text:
            issues.append(f"{name}:whitespace")
        if not limit[0] <= len(text) <= limit[1]:
            issues.append(f"{name}:length")
        if DASH.search(text):
            issues.append(f"{name}:dash")
        if "!" in text:
            issues.append(f"{name}:exclamation")
        if BANNED_PHRASES.search(text):
            issues.append(f"{name}:internal_phrase")
    headline, dek = dict(fields).get("headline"), story.get("dek")
    if isinstance(headline, str) and headline:
        if not headline[0].isupper() and not headline[0].isdigit():
            issues.append("headline:sentence_case")
        if headline.rstrip()[-1:] in FINAL_PUNCTUATION:
            issues.append("headline:final_punctuation")
        # A quoted Plane Alert DB category such as "Don't you know who I am?" asks nothing.
        if "?" in _mask_list_phrases(headline, listed, ENTRY_STAND_IN) and not question_allowed:
            issues.append("headline:question")
        # Title case shows in capitalized function words. Names from the facts keep their own
        # capitals ("Antonov An-124", "Cape May County Airport"), as does a designation prefix.
        masked = headline
        for phrase in _longest_first(names):
            masked = re.sub(
                r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])",
                lambda match: "x" * len(match.group()),
                masked,
            )
        words = re.findall(r"(?<![:\"'] )(?<!^)\b[A-Z][a-z]+\b(?!-\d)", masked)
        if any(word.lower() in TITLE_CASE_WORDS for word in words):
            issues.append("headline:title_case")
    if isinstance(dek, str) and dek:
        plain = _mask_list_phrases(dek, listed, ENTRY_STAND_IN)
        if not re.search(r"[.]$", plain) or "?" in plain:
            issues.append("dek:not_sentences")
        elif not 1 <= len(sentence_spans(plain)) <= 2:
            issues.append("dek:sentence_count")
    seo = story.get("seo_title")
    if isinstance(seo, str) and seo.rstrip()[-1:] in FINAL_PUNCTUATION:
        issues.append("seo_title:final_punctuation")
    return issues


def _story_claim_issues(story, facts, phrases=(), required=None):
    """Checks that need the whole story: attribution, scope, hedges, lists, and repeated caveats.

    phrases are the entry's own words (_list_phrases), read as LIST_TOKEN; required is the
    kind's required sentence (required_sentence), which counts as the story's one caveat.
    """
    issues = []
    values = _story_values(facts)
    kind = str(facts.get("kind", ""))
    fields = [(name, text) for name, text in _story_fields(story) if isinstance(text, str)]
    raw = " ".join(text for _name, text in fields).lower()
    masked = [(name, _mask_list_phrases(text, phrases, LIST_TOKEN)) for name, text in fields]
    texts = [text for _name, text in masked]
    paragraphs = [text for name, text in masked if name.startswith("paragraph")]
    whole = " ".join(texts)
    # The dek and paragraphs, where attribution and the list's name belong.
    body = " ".join([*paragraphs, str(story.get("dek", ""))])
    type_fact = values.get("id.type", {})
    names = {
        name
        for name in (type_fact.get("value"), *(type_fact.get("aliases") or ()), type_fact.get("short"))
        if isinstance(name, str) and len(name) >= 3
    }
    # Type names blanked once: "Gulfstream V." ends a sentence, not an initial, and a type name
    # is no mention of a watchlist entry.
    typed = []
    for _name, text in masked:
        for name in _longest_first(names):
            text = _blank_phrase(text, name, bounded=True)
        typed.append(text)
    # Each sentence with the field it comes from; a heading is the headline, dek or title tag.
    labelled = [
        (name, text[start:end])
        for (name, text), blanked in zip(masked, typed, strict=True)
        for start, end in sentence_spans(blanked)
    ]
    sentences = [sentence for _name, sentence in labelled]
    registration = values.get("id.registration", {}).get("value")
    # A type with no name is named by its class phrase, in any case, and its code; they need
    # attribution too, but are no type names to blank. A one-word class phrase ("helicopter") is
    # a common noun, so only the code makes it a name.
    klass, code = ((values.get(key) or {}).get("value") for key in ("id.type_class", "id.type_code"))
    klass = klass if isinstance(klass, str) and len(klass.split()) >= 2 else None
    triggers = [(name, 0) for name in ([registration] if registration else []) + list(names)]
    triggers += [
        (name, flags)
        for name, flags in ((klass, re.I), (code, 0))
        if isinstance(name, str) and len(name) >= 3
    ]
    if any(_bounded([name], flags=flags).search(whole) for name, flags in triggers) and not (
        DATABASE_ATTRIBUTION.search(body)
    ):
        issues.append("paragraphs:database_attribution")
    profiles = profile_texts(facts)
    profile_text = " ".join(profiles).lower()
    # A winds aloft day may name its fastest eastbound jet, the top of the comparison its facts hold.
    scoped = (
        any(identifier.startswith("season.") for identifier in values)
        or kind.startswith("first_")
        or kind == "winds_aloft_day"
    )
    # "The first A380 here" is a first claim too: the story's own type names count as nouns.
    first_type = (
        re.compile(
            r"\bfirst\s+(?:[\w'-]+\s+){0,2}?(?:"
            + "|".join(re.escape(name) for name in _longest_first(names))
            + r")(?![\w-])",
            re.I,
        )
        if names
        else None
    )
    scopes = [bool(STORY_SCOPE.search(sentence)) for sentence in sentences]
    # A first, best or record claim is scoped inside a paragraph ("the first in these records");
    # the headline and dek may then carry it unscoped, and never carry the scope themselves.
    paragraph_scoped = any(
        scope
        for (name, _sentence), scope in zip(labelled, scopes, strict=True)
        if name.startswith("paragraph")
    )
    for name in ("headline", "dek"):
        if isinstance(story.get(name), str) and (match := HEADING_SCOPE.search(story[name])):
            issues.append(f"{name}:scope phrase: {match.group()}")
    for (name, sentence), scope in zip(labelled, scopes, strict=True):
        heading = not name.startswith("paragraph")
        in_scope = scoped and (scope or (heading and paragraph_scoped))
        for match in SUPERLATIVE.finditer(sentence):
            if match.group().lower() not in profile_text and not in_scope:
                issues.append("claim:unscoped_superlative:" + match.group().lower())
        # A sourced type fact restated as written may say "first": "Airbus flew the first A380 in 2005."
        if any(text in " ".join(sentence.split()) for text in profiles):
            continue
        # A story is about the aircraft, never this site's record keeping or its checks.
        if SELF_REFERENCE.search(sentence):
            issues.append("claim:self_reference")
        if SITE_ACTIONS.search(sentence) or _names_site(sentence):
            issues.append("claim:site_action")
        if CLOCK_SECONDS.search(sentence):
            issues.append("claim:clock_seconds")
        if DAY_COUNT.search(sentence):
            issues.append("claim:day_count")
        for match in [
            *FIRST_CLAIM.finditer(sentence),
            *(first_type.finditer(sentence) if first_type else ()),
        ]:
            if scoped and re.search(r"\bfirst\s+(?:wrote|appeared|featured|reported)\b", match.group(), re.I):
                continue
            if not in_scope:
                issues.append("claim:unscoped_first")
    type_words = " ".join(
        str(part)
        for part in (type_fact.get("value"), type_fact.get("category"), *(type_fact.get("aliases") or ()))
        if part
    ).lower()
    # A fixed headline is the engine's ("Strong winds aloft on 5 November 2026"), not a hypothesis,
    # and so is a title tag that shortens it ("Strong winds aloft on 5 November").
    fixed = fixed_headline(facts)
    claims = [
        sentence
        for name, sentence in labelled
        if not (fixed and name in ("headline", "seo_title") and fixed[0].startswith(sentence.strip()))
    ]
    for hypothesis in facts.get("hypotheses", []):
        if not isinstance(hypothesis, str):
            continue
        terms = [
            term
            for term in STORY_HEDGE_TERMS.findall(hypothesis.lower())
            if term not in STOPWORDS and term not in HYPOTHESIS_GENERIC and term not in type_words
        ]
        if not terms:
            continue
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(term) for term in terms) + r")\b", re.I)
        for sentence in claims:
            if pattern.search(sentence) and not HEDGE.search(sentence) and not EPISTEMIC.search(sentence):
                issues.append("claim:unhedged_hypothesis")
    lowered = [sentence.lower() for sentence in sentences]
    watchlisted = any(key.startswith("wl.") for key in values)
    category = values.get("wl.category", {}).get("value")
    if isinstance(category, str):
        for sentence, low in zip(sentences, lowered, strict=True):
            if category.lower() in low and not (
                WATCHLIST_SOURCE.search(sentence) or WATCHLIST_REFERENCE.search(sentence)
            ):
                issues.append("claim:watchlist_category_unattributed")
    # The list's own category name and tags are its claims: a sentence quoting one names the list.
    for sentence in sentences:
        if LIST_TOKEN in sentence and not (
            WATCHLIST_SOURCE.search(sentence)
            or LIST_REFERENCE.search(sentence)
            or LISTED_FORM.search(sentence)
        ):
            issues.append("claim:watchlist_detail_unattributed")
    association = values.get("wl.association", {}).get("value")
    if watchlisted and not PLANE_ALERT.search(body):
        # A story that uses the entry, by its name, its words, or the listed form of a
        # headline, names the list in the dek or a paragraph. Another kind's story may
        # leave an entry it never mentions alone; a type name is not a mention.
        mentioned = kind == "watchlist_match" or any(
            LIST_TOKEN in text
            or LISTED_FORM.search(text)
            or (isinstance(association, str) and association.lower() in text.lower())
            for text in typed
        )
        if mentioned:
            issues.append("claim:watchlist_source_missing")
    if kind == "watchlist_match" and isinstance(association, str):
        # A watchlist story says who or what the entry names, and why the list carries the aircraft.
        if association.lower() not in raw:
            issues.append("claim:watchlist_entry_unnamed")
        reasons = [
            values.get(key, {}).get("value")
            for key in ("wl.list_category", "wl.category", "wl.category_note")
        ] + list(values.get("wl.tags", {}).get("value") or ())
        reasons = [reason.lower() for reason in reasons if isinstance(reason, str) and reason]
        if reasons and not any(reason in raw for reason in reasons):
            issues.append("claim:watchlist_reason_missing")
    if kind in RATED_KINDS:
        # A rated story passes no judgement on the flight, and the evidence card credits the wind.
        if any(RATED_JUDGEMENT.search(sentence) for sentence in sentences):
            issues.append("claim:limit_violation")
        if any(HELD_VALUE.search(sentence) for sentence in sentences):
            issues.append("claim:limit_held")
        if any(WIND_SERVICE.search(sentence) for sentence in sentences):
            issues.append("claim:wind_service_named")
    if any(key.startswith("lim.") for key in values):
        # A story that cites the type's certified limits calls each by its own name.
        never_exceed = any(
            item.get("field") == "vne" for key, item in values.items() if key.startswith("lim.")
        )
        if any(SERVICE_CEILING.search(sentence) for sentence in sentences) or (
            not never_exceed and any(NEVER_EXCEED.search(sentence) for sentence in sentences)
        ):
            issues.append("claim:limit_name")
    issues.extend(_fact_rule_issues(sentences, values, profiles))
    # One caveat per story: the kind's required sentence, which the story reaches without, or one
    # sentence of the writer's own. A restated type fact is the type's background, never the caveat.
    caveats = sum(
        1 for name, sentence in labelled if name.startswith("paragraph") and is_caveat(sentence, profiles)
    )
    caveats += 1 if required else 0
    if caveats > 1 or (
        kind in RECORD_KINDS
        and any(name.startswith("paragraph") and RECORD_HEDGE.search(sentence) for name, sentence in labelled)
    ):
        issues.append("paragraphs:repeated_limitations")
    for _name, sentence in labelled:
        issues.extend(_serial_comma_issues(_sentence_body(sentence)))
    return list(dict.fromkeys(issues))


# Two sources presented as agreeing about a type: "both list", "also lists", "agrees". "Disagree"
# and "differ" say they do not, and so does a negated agreement (FactRule.negatable).
SOURCES_AGREE = re.compile(r"\b(?:both\s+list(?:s|ed)?|also\s+list(?:s|ed)?|agree(?:s|d|ing)?)\b", re.I)
# A negation up to two words before a forbidden word: "do not agree", "don't agree", "never
# fully agree".
NEGATED = re.compile(r"(?:" + NEGATION.pattern + r")(?:\s+[\w']+){0,2}\s*$", re.I)
# Where a sentence turns to another source's claim: "adsbdb lists a Boeing 757-200, while the
# aircraft database lists a Cessna 172" attributes each clause to its own source. A clock time's
# colon ("12:00") breaks nothing.
CLAUSE_BREAK = re.compile(r",\s+(?:while|but|whereas)\b|[;:](?=\s|$)", re.I)
# A later clause that points back at what an earlier one named: "..., but the aircraft database
# lists the same type", "; the database lists it that way too", ": so does the database", "; the
# database, too, lists it". "The same" before another noun ("for the same address"), and "as
# well" or "too" anywhere but the clause's end or set off by commas ("too fast"), point back at
# nothing.
CLAUSE_ANAPHOR = re.compile(
    r"\bthe same\s+(?:type|aircraft|model|way)\b|\b(?:the same|as well|too)\W*$"
    r"|,\s*(?:too|as well)\s*,|\b(?:that way|so (?:does|do|did)|likewise)\b",
    re.I,
)
# A sentence that names the Plane Alert DB entry, its link, or the list.
ENTRY_NAMED = re.compile(
    WATCHLIST_SOURCE.pattern + "|" + WATCHLIST_REFERENCE.pattern + r"|\b(?:its|the entry's|the) link\b", re.I
)


def _registry_names(values):
    """The second registry's type (tc.type, given only when the registry disagrees), without the
    names the database's type shares ("757" is the database's)."""
    own, other = values.get("id.type") or {}, values["tc.type"]
    shared = {
        name.lower()
        for name in (own.get("value"), own.get("short"), *(own.get("aliases") or ()))
        if isinstance(name, str)
    }
    return _bounded(
        [
            name
            for name in (
                other.get("value"),
                other.get("short"),
                *(other.get("aliases") or ()),
                other.get("code"),
            )
            if isinstance(name, str) and len(name) >= 3 and name.lower() not in shared
        ]
    )


def _registry_cited(values):
    return _bounded([values["tc.source"]["value"]])


def _class_names(values):
    klass = values["id.type_class"].get("value")
    return _bounded([klass]) if isinstance(klass, str) and klass else None


def _organization_names(values):
    who, association = values["wl.who"], (values.get("wl.association") or {}).get("value")
    listed = (association,) if isinstance(association, str) and association.strip() else ()
    return _organization_pattern(who.get("value"), who.get("label"), listed)


def _entry_names(values):
    """The Plane Alert DB entry's own names: the association and what Wikidata calls it."""
    association = (values.get("wl.association") or {}).get("value")
    who = values.get("wl.who") or {}
    return [name for name in (association, who.get("value"), who.get("label")) if isinstance(name, str)]


class FactRule(NamedTuple):
    """A sentence rule a story fact turns on. A sentence that names the fact (`names`, or every
    sentence when it gives None) must match `require` and may match none of `forbid`; `mask`
    gives words blanked before `forbid` reads the sentence, `exempt` passes a sentence that
    restates a sourced type fact as written, and with `negatable` a forbidden word right after a
    negation (NEGATED) is none. With `clauses`, `forbid` reads each clause (CLAUSE_BREAK) that
    names the fact alone, and `require` the sentence up to the end of that clause, so a citation
    in an earlier clause counts; a later clause that only points back at the fact
    (CLAUSE_ANAPHOR) is read against `forbid` alone. One issue per story."""

    issue: str
    fact: str
    names: Callable | None
    require: Callable | None = None
    forbid: tuple = ()
    mask: Callable | None = None
    exempt: bool = False
    clauses: bool = False
    negatable: bool = False


FACT_RULES = (
    # A second registry's type is that registry's: named with it and never credited to the aircraft
    # database, clause by clause, and never presented as agreeing with it anywhere in the sentence.
    FactRule(
        "claim:registry_attribution",
        "tc.type",
        _registry_names,
        require=_registry_cited,
        forbid=(DATABASE_ATTRIBUTION,),
        clauses=True,
    ),
    FactRule(
        "claim:registry_attribution", "tc.type", _registry_names, forbid=(SOURCES_AGREE,), negatable=True
    ),
    # A certificate limit belongs to the listed type code's data sheet, never to a class phrase.
    FactRule("claim:class_limit", "id.type_class", _class_names, forbid=(CERTIFIED_WORDS,)),
    # A story that names an airport says only how close the track came to it. The Plane Alert DB
    # entry's own names ("Falcon Landing LLC") are no airport use, and a sourced type fact restated
    # as written may still say "landing" or "approach".
    FactRule(
        "claim:airport_use", "track.airport", None, forbid=(AIRPORT_USE,), mask=_entry_names, exempt=True
    ),
    # What Wikidata calls the entry's organization is said beside the entry, its link, or Plane
    # Alert DB; Wikidata alone attributes nothing.
    FactRule(
        "claim:organization_attribution", "wl.who", _organization_names, require=lambda _values: ENTRY_NAMED
    ),
)


def _fact_rule_issues(sentences, values, profiles):
    """FACT_RULES over a story's sentences; profiles are the sourced type facts as written."""
    issues = []
    for rule in FACT_RULES:
        if rule.fact not in values:
            continue
        named = rule.names(values) if rule.names else None
        if rule.names and named is None:
            continue
        required = rule.require(values) if rule.require else None
        masks = [
            re.compile(r"(?<![\w-])" + re.escape(name) + r"(?!\w)", re.I)  # "Falcon Landing LLC-listed" too
            for name in _longest_first(set(rule.mask(values) if rule.mask else ()))
            if name.strip()
        ]
        for sentence in sentences:
            if rule.exempt and any(text in " ".join(sentence.split()) for text in profiles):
                continue
            if _sentence_breaks_rule(rule, sentence, named, required, masks):
                issues.append(rule.issue)
                break
    return issues


def _sentence_breaks_rule(rule, sentence, named, required, masks):
    """Whether a sentence breaks a rule, clause by clause with `clauses` (FactRule)."""
    breaks = [match.span() for match in CLAUSE_BREAK.finditer(sentence)] if rule.clauses else []
    starts = [0, *(end for _start, end in breaks)]
    ends = [*(start for start, _end in breaks), len(sentence)]
    seen = False
    for start, end in zip(starts, ends, strict=True):
        part = sentence[start:end]
        names = named is None or bool(named.search(part))
        if names and required is not None and not required.search(sentence[:end]):
            return True
        if (names or (seen and CLAUSE_ANAPHOR.search(part))) and _forbidden(rule, part, masks):
            return True
        seen = seen or names
    return False


def _forbidden(rule, text, masks):
    """Whether a sentence, or a clause of one, that names a rule's fact says what it forbids."""
    for mask in masks:
        text = _blank(text, mask)
    return any(
        not (rule.negatable and NEGATED.search(text[: match.start()]))
        for pattern in rule.forbid
        for match in pattern.finditer(text)
    )


# Plane Alert DB's own wording, quoted or several words long, stands in for the entry in the
# checks: its category "Don't you know who I am?" is no first person and a tag such as
# "Toys4Billionaires" no owner claim. The prose checks read it as ENTRY_STAND_IN; the story
# checks see LIST_TOKEN and make the list own each one.
ENTRY_STAND_IN = "the entry"
LIST_TOKEN = "listentry"


def _list_phrases(facts):
    """The entry's category name and tags, as the story facts give them; longest first."""
    if not is_story_facts(facts):
        return ()
    values = _story_values(facts)
    phrases = [
        values.get("wl.list_category", {}).get("value"),
        *(values.get("wl.tags", {}).get("value") or ()),
    ]
    return tuple(_longest_first({phrase for phrase in phrases if isinstance(phrase, str) and phrase.strip()}))


def _association(facts):
    """The association a story's Plane Alert DB entry names, as a one-item list, or []."""
    value = _story_values(facts).get("wl.association", {}).get("value") if is_story_facts(facts) else None
    return [value] if isinstance(value, str) and value.strip() else []


@functools.lru_cache(maxsize=256)
def _list_pattern(phrases):
    """Each phrase quoted, or bare when it runs to several words, longest first."""
    parts = []
    for phrase in phrases:
        parts.append('"' + re.escape(phrase) + '"')
        if " " in phrase:
            parts.append(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])")
    return re.compile("|".join(parts))


def _mask_list_phrases(text, phrases, token):
    return _list_pattern(phrases).sub(token, text) if phrases else text


def _masked(check, phrases):
    """check() reading Plane Alert DB's own wording as ENTRY_STAND_IN."""
    return (lambda text: check(_mask_list_phrases(text, phrases, ENTRY_STAND_IN))) if phrases else check


def required_sentence(facts):
    """The sentence a story's kind carries in a paragraph as written (REQUIRED_SENTENCES), or None
    for another kind, for a speed whose facts do not say how its airspeed was obtained, or for a
    foreign military listing whose facts do not say what marked it."""
    if (
        is_story_facts(facts)
        and facts.get("kind") == "identity_conflict"
        and _story_values(facts).get("idc.samples", {}).get("value") == 1
    ):
        return "This observation rests on one archived sample."
    template = REQUIRED_SENTENCES.get(facts.get("kind")) if is_story_facts(facts) else None
    used = re.findall(r"\{(\w+)\}", template or "")
    if not used:
        return template
    values = _story_values(facts)
    slots = {
        "airspeed_origin": AIRSPEED_ORIGINS.get((values.get("lim.airspeed") or {}).get("basis")),
        "fm_basis": (values.get("fm.basis") or {}).get("value"),
    }
    if not all(isinstance(slots.get(name), str) and slots[name].strip() for name in used):
        return None
    return template.format(**{name: slots[name] for name in used})


def _written(sentence):
    """A sentence as written, whitespace aside, standing alone between spaces or paragraph ends."""
    return re.compile(r"(?<!\S)" + r"\s+".join(re.escape(word) for word in sentence.split()) + r"(?!\S)")


def carries(paragraphs, sentence):
    """How many times paragraphs carry a sentence as written, whitespace aside."""
    pattern = _written(sentence)
    return sum(len(pattern.findall(paragraph)) for paragraph in paragraphs if isinstance(paragraph, str))


def _without(story, sentence):
    """The story with a sentence taken out of its paragraphs wherever they carry it as written."""
    paragraphs = story.get("paragraphs")
    if not sentence or not isinstance(paragraphs, list):
        return story
    pattern = _written(sentence)
    return {
        **story,
        "paragraphs": [
            " ".join(pattern.sub("", paragraph).split()) if isinstance(paragraph, str) else paragraph
            for paragraph in paragraphs
        ],
    }


def _plain_words(text):
    """Lowercase words and figures, punctuation stripped: "Full-length decks." is full, length, decks."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _filler_issues(story):
    """filler:frame when the dek or a paragraph uses a refused frame (FILLER_FRAMES)."""
    paragraphs = story.get("paragraphs") if isinstance(story.get("paragraphs"), list) else []
    texts = [text for text in (story.get("dek"), *paragraphs) if isinstance(text, str)]
    return ["filler:frame"] if any(FILLER_FRAMES.search(text) for text in texts) else []


def _box_runs(text):
    """(run length, word runs) of a type box fact: BOX_RUN words, or all of a shorter fact's."""
    words = _plain_words(text)
    size = min(BOX_RUN, len(words))
    return size, {tuple(words[index : index + size]) for index in range(len(words) - size + 1)}


def quotes(text, fact):
    """Whether text repeats a type box fact: a BOX_RUN-word run of it, or all of a shorter one."""
    size, runs = _box_runs(fact)
    words = _plain_words(text) if size and isinstance(text, str) else []
    return any(tuple(words[index : index + size]) in runs for index in range(len(words) - size + 1))


def box_issues(story, facts):
    """box:reused when a paragraph sentence of AI prose repeats a type box fact (tp.); the page
    shows the box beside the story. Template text quotes its type facts, and the box then lists
    the ones it leaves out (publication)."""
    paragraphs = story.get("paragraphs") if isinstance(story, dict) else None
    sentences = [sentence for _index, sentence in _paragraph_sentences(paragraphs or [])]
    profiles = profile_texts(facts) if is_story_facts(facts) else []
    if any(quotes(sentence, fact) for sentence in sentences for fact in profiles):
        return ["box:reused"]
    return []


# Facts written as sentences, whose capitalized first words are no names.
SENTENCE_FACTS = ("tp.", "ctx.kind")


def _fact_names(facts):
    """Lowercase name tokens the story facts give: each word of a string fact that is capitalized or
    carries a digit, from its value, list items, aliases, and short form."""
    names = set()
    for item in facts["facts"]:
        if item["id"].startswith(SENTENCE_FACTS):
            continue
        value = item.get("value")
        texts = [
            text
            for text in (
                *(value if isinstance(value, list) else [value]),
                *(item.get("aliases") or ()),
                item.get("short"),
            )
            if isinstance(text, str)
        ]
        for text in texts:
            for word in re.findall(r"[A-Za-z0-9]+", text):
                if (
                    word[0].isupper() or any(char.isdigit() for char in word)
                ) and word.lower() not in STOPWORDS:
                    names.add(word.lower())
    return names


def adds_to(headline, dek, facts):
    """Whether a dek gives a figure, or a name from the story facts, that the headline lacks."""
    fresh = set(_plain_words(dek)) - set(_plain_words(headline))
    names = _fact_names(facts)
    return any(any(char.isdigit() for char in token) or token in names for token in fresh)


def _dek_issues(story, facts):
    """dek:adds_nothing unless the dek adds a fact to the headline (adds_to)."""
    dek, headline = story.get("dek"), story.get("headline")
    if not isinstance(dek, str) or not isinstance(headline, str) or not dek.strip():
        return []
    return [] if adds_to(headline, dek, facts) else ["dek:adds_nothing"]


def context_line(facts):
    """The kind's context line (ctx.kind) of story facts, spaces collapsed, or None."""
    value = (_story_values(facts).get("ctx.kind") or {}).get("value") if is_story_facts(facts) else None
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


def _context_words(text):
    return {
        _stem(word) for word in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower()) if word not in STOPWORDS
    }


def _figures(text):
    return sorted(figure.replace(",", "") for figure in re.findall(r"\d(?:[\d,.]*\d)?", text))


def restates_context(sentence, line):
    """Whether a sentence is the kind's context line as written or lightly rephrased: the same
    figures, and CONTEXT_REPHRASE or more of the line's content words and of its own shared."""
    if not isinstance(sentence, str) or not line or _figures(sentence) != _figures(line):
        return False
    own, theirs = _context_words(sentence), _context_words(line)
    shared = len(own & theirs)
    return bool(own and theirs) and min(shared / len(own), shared / len(theirs)) >= CONTEXT_REPHRASE


def _context_sentences(story, line):
    """The paragraph sentences that restate the context line, as written."""
    paragraphs = story.get("paragraphs") if isinstance(story.get("paragraphs"), list) else []
    return [
        sentence for _index, sentence in _paragraph_sentences(paragraphs) if restates_context(sentence, line)
    ]


def story_issues(story, packet_or_facts, *, new=True):
    """Every reason a story fails; an empty list means it may be published.

    new: the story is new text. A published story is held to the checks it was written under, so
    with new=False the house headline forms (headline:watchlist_form, headline:foreign_military_form)
    and the story-quality checks (filler:frame, dek:adds_nothing, context:missing) do not apply.
    """
    facts, failure = _facts_for(packet_or_facts)
    listed = _list_phrases(packet_or_facts)
    # The entry's own names keep their capitals in a headline: "The Flying Bulls", "Display Team".
    names = (*(facts.phrases if facts else ()), *listed, *_association(packet_or_facts))
    told = is_story_facts(packet_or_facts)
    kind = packet_or_facts.get("kind") if told else None
    # A fixed headline is exempt from the free-text rules only when it is the fixed one; a kind
    # whose facts give no form keeps every rule.
    fixed = fixed_headline(packet_or_facts) if told else None
    # A headline asks a question only about a TCAS advisory, which the advisory's meaning answers.
    question = told and "ra.advisory" in _story_values(packet_or_facts)
    issues = _shape_issues(story, names, listed, fixed_headline=fixed is not None, question_allowed=question)
    if not isinstance(story, dict):
        return issues
    if fixed and story.get("headline") != fixed[0]:
        issues.append("headline:not_fixed")
    house = {
        "watchlist_match": _watchlist_headline_issue,
        "foreign_military": _foreign_military_headline_issue,
    }
    if new and kind in house and (form := house[kind](story.get("headline"), packet_or_facts)):
        issues.append(form)
    # The kind's required sentence is found by equality, once, in a paragraph; every other
    # check then reads the story without it.
    required = required_sentence(packet_or_facts)
    carried = carries(story.get("paragraphs") or (), required) if required else 0
    # The story as written: two sentences at least, and a closing sentence that is no caveat.
    sentences = _paragraph_sentences(
        story.get("paragraphs") if isinstance(story.get("paragraphs"), list) else []
    )
    if len(sentences) == 1:
        issues.append("paragraphs:too_short")
    if closes_on_caveat(sentences[-1][1] if sentences else "", required, facts.profiles if facts else ()):
        issues.append("paragraphs:closing_caveat")
    story = _without(story, required)
    if (required or kind in REQUIRED_SENTENCES) and carried != 1:
        missing = "claim:record_unhedged" if kind in RECORD_KINDS else "claim:limit_unhedged"
        issues.append("paragraphs:repeated_limitations" if carried else missing)
    if new:
        issues.extend(_filler_issues(story))
    if told:
        # The kind's context line is a sourced fact: a sentence that restates it is backed by it, so
        # the checks read the story without it, as they do the required sentence.
        line = context_line(packet_or_facts)
        restated = _context_sentences(story, line) if line else []
        if new and line and not restated:
            issues.append("context:missing")
        for sentence in restated:
            story = _without(story, sentence)
        if new:
            issues.extend(_dek_issues(story, packet_or_facts))
    check = _masked(_checker(facts, failure), listed)
    for name, text in _story_fields(story):
        if isinstance(text, str):
            issues.extend(f"{name}:{issue}" for issue in check(text))
    if told:
        try:
            issues.extend(_story_claim_issues(story, packet_or_facts, listed, required))
        except CHECK_ERRORS as error:
            issues.append(f"story:checks_failed:{type(error).__name__}")
    return list(dict.fromkeys(issues))


def bound_facts(facts, texts):
    """Ids of the story facts these texts rely on, as the checks bind them: the fact behind each
    figure, each type-profile sentence a sentence restates or draws on, and each sourced name or
    phrase restated as written. A writer cites the sources of these, declared or not."""
    indexed, _failure = _facts_for(facts)
    if indexed is None or not is_story_facts(facts):
        return set()
    phrases = {
        item["id"]: [
            name
            for name in (
                item.get("value"),
                item.get("short"),
                *(item.get("value") if isinstance(item.get("value"), list) else ()),
            )
            if isinstance(name, str) and len(name.strip()) >= 3
        ]
        for item in facts["facts"]
        if item.get("source")
    }
    for text in texts:
        if not isinstance(text, str) or len(text) > MAX_PROSE:
            continue
        cleaned = _restated(_clean(text), indexed.caveats)
        with suppress(*CHECK_ERRORS):
            # The number checks bind each figure's fact and each profile sentence they use.
            _number_issues(_sourced(_baselined(cleaned, indexed)[0], indexed), indexed)
        indexed.bound.update(
            fact_id
            for fact_id, names in phrases.items()
            if any(_restatement(name).search(cleaned) for name in names)
        )
    return set(indexed.bound)


def validate_story_fields(story, packet_or_facts):
    """Return the story unchanged, or raise ValueError listing every failed check.

    Each field keeps to FIELD_LIMITS (PARAGRAPHS paragraphs), sentence case, no dashes,
    exclamations or BANNED_PHRASES, and passes prose_issues(); the story runs to two
    sentences at least and never closes on a caveat. With story facts, it must also
    attribute database identity, scope its superlatives and first claims, hedge every
    hypothesis and keep to one caveat.
    """
    issues = story_issues(story, packet_or_facts)
    if issues:
        raise ValueError("Story failed checks: " + "; ".join(issues))
    return story
