"""Story slugs: /stories/{type}-{registration}[-{phenomenon}]-{day}-{mon}-{year}/.

Ghost keeps a bare slug unique across every post and page, drafts included, and
answers a taken slug with a silent "-2". New slugs therefore end with a day, a
three-letter month and a four-digit year: the third character from the end is a
digit and the fifth is a hyphen. A launch-day slug ends with an ISO date (third
character from the end a hyphen) or with 24 hex digits (no hyphen in the last 24
characters), so no new slug can equal any old one, whatever their subjects.

A slug is computed once, at first staging, from the type, identifier, kind and
observation date. Prose, prose policy and later type-name edits never change it.
"""

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

from . import episode_evidence, kinds
from .public_prose import MONTH_NAMES

MONTHS = tuple(name[:3].lower() for name in MONTH_NAMES)
MAX_LENGTH = 80
DATE = r"(?:[1-9]|[12][0-9]|3[01])-(?:" + "|".join(MONTHS) + r")-(?:19|20)[0-9]{2}"
NEW_SLUG = re.compile(r"(?=[a-z0-9-]{1," + str(MAX_LENGTH) + r"}\Z)[a-z0-9]+(?:-[a-z0-9]+)*-" + DATE + r"\Z")
OLD_SLUG_FAMILIES = (
    # Frozen launch records: {registration|callsign|hex}-{kind}-{YYYY-MM-DD}.
    re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*-[0-9]{4}-[0-9]{2}-[0-9]{2}\Z"),
    # Live launch records: observation-{24 hex digits}.
    re.compile(r"observation-[0-9a-f]{24}\Z"),
)
# Ghost's own protected slugs plus every route, page and file this site serves at its root; the
# bootstrap's collision check reads this list.
RESERVED = frozenset(
    {
        "about",
        "aircraft",
        "amp",
        "api",
        "assets",
        "author",
        "edit",
        "errors",
        "event-data",
        "event-media",
        "event-pages",
        "examples",
        "field-guide",
        "ghost",
        "image-credits",
        "journal",
        "operators",
        "p",
        "page",
        "public",
        "robots",
        "robots.txt",
        "rss",
        "search",
        "search-index",
        "sitemap",
        "sitemap.xml",
        "social",
        "stories",
        "tag",
        "tags",
        "topics",
        "weekly",
        "wtf",
    }
)
# Ghost pagination and feeds below any collection.
RESERVED_LEAVES = frozenset({"page", "rss", "amp"})
# Kinds whose story is the aircraft itself, so the slug names no phenomenon.
SUBJECT_KINDS = kinds.where(slug_subject=True)
# Two aircraft share the story; the subject names both types and no identifier.
PAIR_KINDS = kinds.where(pair=True)
# Balloons are followed by their callsign rather than a registration.
CALLSIGN_FIRST = kinds.where(callsign_first=True)
# Each kind's slug phenomenon and its short form, in their recorded order.
PHENOMENA = kinds.table(
    "slug",
    order=(
        "first_type_here",
        "first_country_here",
        "outlier_gs",
        "outlier_alt_baro",
        "outlier_archive_alt",
        "outlier_baro_rate",
        "type_speed_mismatch",
        "type_altitude_mismatch",
        "position_jump",
        "altitude_gap",
        "identity_conflict",
        "emitter_type_mismatch",
        "unusual_emitter",
        "space_category",
        "correlated_data_anomaly",
        "gnss_integrity_loss",
        "gnss_integrity_cluster",
        "special_squawk_7400",
        "special_squawk_7500",
        "special_squawk_unlawful",
        "special_squawk_7600",
        "special_squawk_nordo",
        "special_squawk_7700",
        "special_squawk_general",
        "special_squawk_lifeguard",
        "special_squawk_minfuel",
        "special_squawk_downed",
        "archive_squawk_7400",
        "archive_squawk_7500",
        "archive_squawk_7600",
        "archive_squawk_7700",
        "special_use_code",
        "tcas_ra",
        "stratospheric_balloon",
        "formation_flight",
        "survey_grid",
        "parallel_passes",
        "repeated_passes",
        "holding_stack",
        "prolonged_holding",
        "hovering",
        "descending_spiral",
        "circling",
        "loitering",
        "confined_activity",
        "course_reversal",
        "altitude_oscillation",
        "departure_return",
        "possible_go_around",
        "extreme_turn_rate",
        "thermal_soaring",
        "rapid_descent",
        "rapid_climb",
        "vertical",
        "fast",
        "high",
        "wind_boost",
        "seasonal_speed_record",
        "above_rated_altitude",
        "above_rated_speed",
        "selected_altitude_overshoot",
    ),
)
INVISIBLE = re.compile(
    "[\\x00-\\x1f\\x7f-\\x9f\\xad\\u0600-\\u0605\\u061c\\u06dd\\u070f\\u180e\\u200b-\\u200f\\u202a-\\u202e"
    "\\u2060-\\u2064\\u2066-\\u206f\\ufeff\\ufff9-\\ufffb\\ue000-\\uf8ff]"
)
RESERVED_CHARACTERS = re.compile(
    r"(\s|\.|@|:|/|\?|#|\[|\]|!|\$|&|\(|\)|\*|\+|,|;|=|\\|%|<|>|\||\^|~|\"|\{|\}|`|"
    "\u2013|\u2014)"
)


def ghost_slugify(value):
    """Ghost's slugify (@tryghost/string 0.3.6, MIT), as Ghost applies it to a requested slug.

    Unicode transliteration (unidecode) is approximated by NFKD folding; generated
    slugs are ASCII, so the two agree on everything this module produces.
    """
    value = INVISIBLE.sub("", value or "")
    value = value.replace("\u00a3", "-")
    # unidecode turns dashes into hyphens before Ghost replaces reserved characters.
    value = re.sub("[\u2010-\u2015]", "-", value)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = RESERVED_CHARACTERS.sub("-", value).replace("'", "").lower()
    value = re.sub(r"-+", "-", value)
    value = re.sub(r"-$", "", value)
    value = re.sub(r"^-", "", value)
    return value.strip()


def ghost_post_slug(value):
    """The slug Ghost would store for a requested post slug before duplicate checks."""
    slug = ghost_slugify(value)[:185]
    return slug + "-post" if slug in {"ghost", "rss", "amp"} else slug or "post"


def token(value):
    """One slug segment: lowercase letters, digits and single inner hyphens."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ghost_slugify(value))).strip("-")


def is_new_slug(value):
    return isinstance(value, str) and bool(NEW_SLUG.match(value))


def is_old_slug(value):
    return isinstance(value, str) and any(pattern.match(value) for pattern in OLD_SLUG_FAMILIES)


def article_path(slug):
    if not is_new_slug(slug):
        raise ValueError("A story slug must use the dated story scheme")
    return f"/stories/{slug}/"


def _instant(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("The observation start needs a time zone")
        return parsed.astimezone(timezone.utc)
    raise ValueError("The observation start must be a timestamp")


def date_segment(observed_start):
    moment = _instant(observed_start)
    return f"{moment.day}-{MONTHS[moment.month - 1]}-{moment.year:04d}"


def _subject(facts):
    """Kind, type slugs, identifiers, transmitted code and seed from story facts."""
    if not isinstance(facts, dict) or not isinstance(facts.get("facts"), list):
        raise ValueError("Slug input must be story facts")
    values = {item.get("id"): item for item in facts["facts"] if isinstance(item, dict)}
    types = [
        (item["slug"], item.get("short_slug"))
        for fact_id in ("id.type", *(f"p.{index}.type" for index in range(1, 13)))
        if (item := values.get(fact_id)) and item.get("slug")
    ]
    identifiers = {
        name: (values.get(fact_id) or {}).get("value")
        for name, fact_id in (
            ("registration", "id.registration"),
            ("callsign", "id.callsign"),
            ("hex", "id.hex"),
        )
    }
    code = (values.get("code.code") or values.get("code.squawk") or {}).get("value")
    return facts.get("kind"), types, identifiers, code, facts.get("digest") or ""


def _phenomenon(kind, code):
    if kind in SUBJECT_KINDS:
        return None
    if kind == "special_use_code" and episode_evidence.octal_code(code):
        return (f"squawk-{code}", code)
    if kind in PHENOMENA:
        return PHENOMENA[kind]
    # A kind without its own term gets a generic one, never its internal name.
    return ("observation", "observation")


def _join(*parts):
    return "-".join(part for part in parts if part)


def slug(facts, observed_start, *, taken=frozenset()):
    """The /stories/{slug}/ segment for one story's facts; never reuses a taken slug."""
    kind, types, identifiers, code, seed = _subject(facts)
    date = date_segment(observed_start)
    order = (
        ("callsign", "registration", "hex") if kind in CALLSIGN_FIRST else ("registration", "callsign", "hex")
    )
    identifier = next((token(identifiers[name]) for name in order if token(identifiers.get(name))), "")
    type_slugs = [(token(full), token(short or "")) for full, short in types if token(full)]
    if kind in PAIR_KINDS and len(type_slugs) >= 2 and type_slugs[0][0] != type_slugs[1][0]:
        subjects = [type_slugs[:2], None]
    else:
        subjects = [type_slugs[:1], identifier]
    phenomenon = _phenomenon(kind, code)

    def build(shorten_type, short_phenomenon, drop_type=False, marker=""):
        names = (
            []
            if drop_type
            else [short or full if shorten_type and short else full for full, short in subjects[0]]
        )
        subject = _join(*names, subjects[1] or "")
        if not subject:
            subject = identifier or "aircraft"
        term = phenomenon[1] if short_phenomenon and phenomenon else phenomenon[0] if phenomenon else ""
        return _join(subject, term, marker, date)

    def fitted(marker=""):
        for options in ((False, False), (True, False), (True, True), (True, True, True)):
            candidate = build(*options, marker=marker)
            if len(candidate) <= MAX_LENGTH:
                return candidate
        return build(True, True, True, marker=marker)[-MAX_LENGTH:].lstrip("-")

    taken = {item for item in taken if isinstance(item, str)}
    candidate = fitted()
    if candidate in taken:
        moment = _instant(observed_start)
        candidate = fitted(f"{moment.hour:02d}{moment.minute:02d}z")
    counter = 0
    while candidate in taken:
        mark = hashlib.sha256(f"{seed}|{date}|{counter}".encode()).hexdigest()[:4]
        candidate = fitted(mark)
        counter += 1
    if not is_new_slug(candidate) or is_old_slug(candidate) or ghost_post_slug(candidate) != candidate:
        raise ValueError("Generated slug left the dated story scheme")
    return candidate
