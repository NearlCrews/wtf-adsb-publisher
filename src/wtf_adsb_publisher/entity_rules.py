"""Whether a Wikidata description may be repeated beside an aircraft; pure rules, no network.

entity_context applies them to a fresh answer before caching it, and story_facts applies
them again to each cached answer, so one saved before a rule existed is judged by it too.
Both find an answer by cache_key(). Here rather than in entity_context, so the facts layer
never loads the lookup module.
"""

import functools
import re

from . import public_events as public
from . import public_prose, reference

# Digits stay in the character class so a description with a figure is refused as "number"
# (description_issue), which says why, rather than as a malformed "description".
DESCRIPTION = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9 ,.'()-]{10,120}")
# A Wikidata item id, as a story cites it.
ITEM = re.compile(r"Q\d+")
# An English Wikipedia article link. The mobile site names the same article, and a section or
# query string leaves its title as it is.
ARTICLE = re.compile(r"https://en\.(?:m\.)?wikipedia\.org/wiki/([^#?/]+)(?:[?#].*)?", re.S)
# A description that tells of wrongdoing, death, or illness, or says who founded, owns, or runs
# the organization, is never repeated beside an aircraft.
DENY = re.compile(
    r"\b(?:crim\w*|convict\w*|fraud\w*|scandal\w*|sanction\w*|indict\w*|arrest\w*|prison\w*|murder\w*|dead|death"
    r"|died|killed|suicide|illness|cancer|terror\w*|cartel|traffick\w*|abuse\w*|assault\w*|lawsuit|sued|alleged\w*"
    r"|(?:founded|owned|led|run|headed|controlled|chaired)\s+by)\b",
    re.I,
)
# A description names no person. Each capitalised word must be one of the label's, an alias's,
# or the association's own words, a dotted abbreviation ("U.S."), a nationality, a word of the
# public-body vocabulary below, a State or U.S. state name, or part of a place written after
# "in", "at", "near", or "from" whose last part is a State or U.S. state ("in Van Buren
# Township, Michigan, United States"). After "of", such a run counts only when each of its words
# is a place word or public-body word ("of the County, Michigan"), since "of Donald Trump, United
# States" names a person. The first word may be capitalised when the description also uses it
# in lowercase. Anything else ("company of Donald Trump") reads as a name.
US_STATES = frozenset(
    {
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
        "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
        "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey",
        "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
        "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah",
        "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "Puerto Rico",
    }
)  # fmt: skip
NATIONALITIES = frozenset(
    {
        "American", "Argentine", "Australian", "Austrian", "Belgian", "Brazilian", "British", "Canadian",
        "Chilean", "Chinese", "Czech", "Danish", "Dutch", "Egyptian", "Emirati", "English", "European",
        "Finnish", "French", "German", "Greek", "Icelandic", "Indian", "Irish", "Israeli", "Italian",
        "Japanese", "Korean", "Mexican", "Norwegian", "Pakistani", "Polish", "Portuguese", "Qatari",
        "Russian", "Saudi", "Scottish", "Singaporean", "Spanish", "Swedish", "Swiss", "Taiwanese",
        "Turkish", "Ukrainian", "Welsh",
    }
)  # fmt: skip
PUBLIC_BODY_WORDS = frozenset(
    {
        "Air", "Armed", "Army", "Coast", "Corps", "Defence", "Defense", "Department", "Federal", "Force",
        "Forces", "Government", "Guard", "Marine", "Marines", "National", "Naval", "Navy", "Police", "Space",
        "State",
    }
)  # fmt: skip
# Words that name a kind of place, never a person.
PLACE_TERMS = frozenset(
    {
        "City",
        "County",
        "District",
        "Island",
        "Islands",
        "Parish",
        "Province",
        "Region",
        "Republic",
        "Township",
    }
)
ABBREVIATION = re.compile(r"[A-Z](?:\.[A-Z])+\.?")
WORD = re.compile(r"[^\W\d_][\w'.]*")
PLACE_MARK = "\x00"
CAPITALS = r"[A-Z][^\s,]*(?:\s+[A-Z][^\s,]*)*"
PLACE_RUN = re.compile(rf"\b(in|of|at|near|from)\s+({CAPITALS}(?:,\s+{CAPITALS})*),\s+{PLACE_MARK}")


def _place_pattern():
    """Every State and U.S. state name as a whole phrase, longest first."""
    return _place_pattern_for(reference.GENERATION)


@functools.lru_cache(maxsize=1)
def _place_pattern_for(generation):
    # Keyed on reference.GENERATION, so reference.clear_cache() and a late
    # load_config rebuild the pattern from the current country names.
    names = sorted(reference.country_names() | US_STATES, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(name) for name in names) + r")(?![\w-])")


def _names_someone(text, own):
    """Whether a description carries a capitalised word the rule above (US_STATES) does not allow.
    `own` holds the casefolded words of the label, the aliases, and the association."""
    text = _place_pattern().sub(PLACE_MARK, text)

    def place(match):
        run = match.group(2)
        if match.group(1) == "of" and not set(re.findall(r"[^\s,]+", run)) <= PLACE_TERMS | PUBLIC_BODY_WORDS:
            return match.group()
        return match.group().replace(run, "x" * len(run))

    text = PLACE_RUN.sub(place, text)
    words = WORD.findall(text)
    lowercase = {word.casefold() for word in words if word[0].islower()}
    for index, raw in enumerate(words):
        if not raw[0].isupper() or ABBREVIATION.fullmatch(raw):
            continue
        word = re.sub(r"'s$", "", raw.rstrip("."))
        if word.casefold() in own or word in NATIONALITIES or word in PUBLIC_BODY_WORDS:
            continue
        if index == 0 and word.casefold() in lowercase:
            continue
        return True
    return False


def canonical_link(link):
    """The plain form of an English Wikipedia article link (ARTICLE): the desktop host, without a
    section or query string. Any other link, a w.wiki short link included, stays as it is, so a
    short link with a query string or section never keys like the plain one."""
    match = ARTICLE.fullmatch(link)
    return f"https://en.wikipedia.org/wiki/{match.group(1)}" if match else link


def cache_key(link, association):
    """The entities.json key of an entry's answer: its canonical link and the association it
    was looked up for, casefolded, so two entries sharing a link each get their own answer, and
    every form of one article link shares the key."""
    return f"{canonical_link(link)}\t{association.casefold()}"


def current_key(key, association):
    """The cache_key() of an answer saved under `key` for `association`. A key written before
    the key named the association (the link alone), or before it took the canonical link, maps
    to the key cache_key() gives now; a current key maps to itself."""
    return cache_key(key.split("\t", 1)[0], association)


def description_issue(description, names):
    """None when a description may be repeated beside an aircraft, else why not. `names` are
    the label, aliases, and association whose words it may capitalise."""
    if not isinstance(description, str) or not DESCRIPTION.fullmatch(description.strip()):
        return "description"
    text = description.strip()
    # A date, count, or year is a figure no story fact supports, so the story checks would drop it.
    if re.search(r"\d", text):
        return "number"
    if (
        DENY.search(text)
        or public.private_text().search(text)
        or public_prose.PERSON_ASSOCIATION.search(text)
    ):
        return "deny"
    own = {word.casefold() for name in names for word in WORD.findall(str(name))}
    if _names_someone(text, own):
        return "name"
    # A superlative the story could not scope to these records ("the oldest airline").
    if public_prose.SUPERLATIVE.search(text) or public_prose.phrase_issues(text, "watchlist_match"):
        return "checks"
    return None
