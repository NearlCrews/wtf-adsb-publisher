"""Deterministic observation HTML for a Ghost editor HTML card; never publishes.

A full validated snapshot supplies one public event. Article prose and the Ghost
post remain separate; this card makes observation dates and evidence survive
native Ghost rendering without inventing custom post fields.
"""

import functools
import hashlib
import json
import re
from copy import deepcopy
from html import escape
from urllib.parse import quote

from . import kinds, public_prose, site_origin, stories, story_facts
from .public_events import (
    PARTICIPANT_ROLES,
    TYPE_LABELS,
    _instant,
    article_slug,
    ghost_post_id,
    now_utc,
    utc,
    validate_snapshot,
)

# Ghost stores links to its own site in a portable form and returns them
# absolute, so a root-relative link would come back changed and the owned block
# would look edited. Owned blocks link to the public site (site_origin) absolutely.
# The first mention of each term in story prose links to its field-guide section.
# The static site's own prose renderer (tools/ghost-theme/build.mjs) links the same
# terms, so a story reads the same whichever of the two renders it.
GLOSSARY = (
    ("ads-b", re.compile(r"(?<![\w-])ADS-B(?![\w])")),
    ("mlat", re.compile(r"(?<![\w-])(?:MLAT|multilateration)(?![\w-])", re.I)),
    ("ground-speed", re.compile(r"(?<![\w-])ground[ -]speeds?(?![\w-])", re.I)),
    ("geometric-altitude", re.compile(r"(?<![\w-])geometric altitudes?(?![\w-])", re.I)),
    ("squawk", re.compile(r"(?<![\w-])(?:squawk(?:s|ed|ing)?|transponder codes?)(?![\w-])", re.I)),
    (
        "type-match",
        re.compile(r"(?<![\w-])(?:type designators?|type codes?|aircraft database)(?![\w-])", re.I),
    ),
)
# Ghost fields the publisher writes; an owner's edit to any of them is kept.
GENERATED_FIELDS = (
    "title",
    "custom_excerpt",
    "meta_title",
    "meta_description",
    "og_title",
    "og_description",
    "twitter_title",
    "twitter_description",
)
RECORD_DETAILS = '<details class="record-details">'

SOURCE_LABELS = {
    "adsb_icao": "ADS-B, ICAO address",
    "adsb_icao_nt": "ADS-B, ICAO non-transponder address",
    "adsr_icao": "ADS-R relay, ICAO address",
    "tisb_icao": "TIS-B, ICAO address",
    "adsc": "ADS-C",
    "mlat": "Multilateration (MLAT)",
    "other": "Other source",
    "mode_s": "Mode S",
    "adsb_other": "ADS-B, other address type",
    "adsr_other": "ADS-R relay, other address type",
    "tisb_trackfile": "TIS-B track identifier",
    "tisb_other": "TIS-B, other address type",
    "mode_ac": "Mode A/C",
    "unknown": "Source not recorded",
}


def ghost_post_date(observed_start):
    """Use Ghost's verified whole-second precision; keep the observation intact."""
    return utc(_instant(observed_start).isoformat(timespec="seconds"))


def aircraft_references(event):
    """Reader lookup destinations derived from a validated public event."""
    references = deepcopy(event["references"])
    aircraft = event["aircraft"]
    registration = aircraft.get("registration")
    description = aircraft.get("type_description") or aircraft.get("type_code")
    extra = []
    if registration:
        extra.append(
            {
                "id": "registration_flightaware",
                "label": "FlightAware: tail number " + registration,
                "url": "https://www.flightaware.com/live/flight/" + quote(registration, safe=""),
            }
        )
    if description:
        extra.append(
            {
                "id": "type_wikipedia_search",
                "label": "Wikipedia: aircraft type search",
                "url": "https://en.wikipedia.org/w/index.php?search=" + quote(description, safe=""),
            }
        )
    urls = {reference["url"] for reference in references}
    references.extend(reference for reference in extra if reference["url"] not in urls)
    return references


def _time(value):
    label = _instant(value).strftime("%d %b %Y, %H:%M:%S UTC").lstrip("0")
    return f'<time datetime="{escape(value, quote=True)}">{label}</time>'


def _interval(start, end):
    if start == end:
        return _time(start)
    return _time(start) + " to " + _time(end)


def measurement_scope(item):
    """Explain the scope using saved timestamps, without expanding its duration."""
    scope = item["scope"]
    if scope == "saved-report":
        if "at_utc" in item:
            return "Saved reading at " + _time(item["at_utc"]) + "."
        return "Saved reading; its individual timestamp was not retained."
    if scope == "archived-label":
        carried = " Archive labels are carried between saved positions."
        if "at_utc" in item:
            return "Last archived label at " + _time(item["at_utc"]) + "." + carried
        return "Last archived label; its own time was not retained." + carried
    if scope == "peak-report":
        if "at_utc" in item:
            return "Peak report at " + _time(item["at_utc"]) + "."
        return "Peak report; its individual timestamp was not retained."
    if scope == "detection-rule":
        return "Detection rule. This is a site setting, not a measurement."
    if scope == "reference-data":
        return "Reference figure: a certified limit, a modelled wind, or a registry record, not a reading from the aircraft."
    if scope == "analysis-window":
        if "start_utc" in item and "end_utc" in item:
            return "Analysis window: " + _interval(item["start_utc"], item["end_utc"]) + "."
        return "Selected analysis window; its start and end times were not retained."
    if scope == "discontinuity-pair-not-physical-travel":
        timing = (
            "Position pair: " + _interval(item["start_utc"], item["end_utc"]) + ". "
            if "start_utc" in item and "end_utc" in item
            else "Derived from a pair of positions. "
        )
        return timing + "The calculation does not establish physical travel."
    if scope == "surrounding-reports":
        if "at_utc" in item:
            return "Neighboring saved report at " + _time(item["at_utc"]) + "."
        return (
            "Other saved reports from "
            + _interval(item["start_utc"], item["end_utc"])
            + ", leaving out the outlying value."
        )
    raise ValueError("Unknown measurement scope")


def _value(value):
    if isinstance(value, int):
        text = f"{value:,}"
    elif isinstance(value, float):
        if value == 0:
            text = "0"
        elif abs(value) < 0.001 or abs(value) >= 1e12:
            text = f"{value:.3g}"
        else:
            text = f"{value:,.3f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    return escape(text)


def _participant_row(item):
    """One escaped row per other aircraft: identity, source and its own measurements."""
    names = [item.get(field) for field in ("registration", "callsign", "type_code") if item.get(field)]
    details = [" · ".join(escape(name) for name in names)] if names else []
    if item.get("type_description"):
        details.append(escape(item["type_description"]))
    if item.get("icao_address"):
        details.append("ICAO address " + escape(item["icao_address"]))
    details.append("Source: " + escape(SOURCE_LABELS[item["source"]]))
    for measurement in item["measurements"]:
        unit = f" <small>{escape(measurement['unit'])}</small>" if measurement["unit"] else ""
        details.append(escape(measurement["label"]) + ": " + _value(measurement["value"]) + unit)
    return PARTICIPANT_ROLES[item["role"]], "<br>".join(details)


def _watchlist_table(block):
    link = block.get("link")
    rows = [
        ("Source", escape(block["source_name"])),
        ("Association", escape(block["association"])),
        ("List category", escape(block.get("list_category") or "")),
        ("Tags", escape(", ".join(block.get("tags") or ()))),
        ("Group", escape(block.get("group") or "")),
        ("Listed aircraft", escape(block.get("description") or "")),
        (
            "Entry link",
            f'<a href="{escape(link, quote=True)}" rel="external noopener noreferrer">{escape(link)}</a>'
            if link
            else "",
        ),
        ("Category", escape(block["category"])),
        ("Pinned source version", _code(block["source_commit"])),
        ("Attribution", escape(block["attribution"])),
        ("Licences", escape("; ".join(block["licenses"]))),
        ("Scope", escape(block["claim_scope"])),
    ]
    # The entry's optional fields appear only when it has them.
    return _record_table("Watchlist association", [(label, value) for label, value in rows if value])


def _code(value):
    # Long hashes wrap without adding characters to their copyable text.
    return "<code>" + "<wbr>".join(escape(value[i : i + 16]) for i in range(0, len(value), 16)) + "</code>"


def _record_table(caption, rows):
    """Values and captions are escaped HTML prepared by the renderer below."""
    return (
        '<table class="record-table"><caption>'
        + caption
        + "</caption><tbody>"
        + "".join(f'<tr><th scope="row">{escape(label)}</th><td>{value}</td></tr>' for label, value in rows)
        + "</tbody></table>"
    )


# The card leads with at most five headline measurements (the kind's
# stories.HEADLINE_MEASUREMENTS), each with a one-line meaning for a reader new to
# ADS-B; every measurement, with its scope and time, follows in a collapsed details
# element with the evidence version.
KEY_FACT_LIMIT = stories.KEY_FACT_LIMIT
MEASUREMENT_MEANINGS = {
    "gs": "speed over the ground; wind adds to or subtracts from it",
    "tas": "speed through the air, as the aircraft reported it",
    "mach": "speed as a fraction of the speed of sound",
    "alt_baro": "pressure altitude, the figure pilots and controllers use",
    "alt_geom": "GPS altitude, usually within a few hundred feet of pressure altitude",
    "archive_alt": "an archived altitude whose reference, pressure or GPS, was not kept",
    "baro_rate": "climb or descent rate from pressure altitude; negative is down",
    "geom_rate": "climb or descent rate from GPS altitude; negative is down",
    "nav_altitude_mcp": "the altitude set on the autopilot, not the altitude flown",
    "nav_qnh": "the altimeter setting the crew entered",
    "peak_rate_fpm": "the fastest rate in the episode; negative is down",
    "altitude_change_ft": "height lost or gained over the episode",
    "duration_seconds": "how long the detected episode lasted, which can be shorter than the saved track",
    "sample_count": "how many saved positions the analysis used",
    "closed_loops": "how many times the track closed a full circle",
    "min_separation_nm": "the closest the two aircraft came horizontally",
    "vertical_separation_ft": "the height difference at that moment",
    "participant_count": "how many aircraft flew together",
    "asymmetry_kn": "how much faster the typical eastbound jet moved over the ground",
    "east_median_kn": "the middle ground speed of the eastbound aircraft",
    "west_median_kn": "the middle ground speed of the westbound aircraft",
    "east_addresses": "how many eastbound aircraft were counted",
    "west_addresses": "how many westbound aircraft were counted",
    "reversals": "how many times the climb turned into a descent or back",
    "altitude_excursion_ft": "the height range between the lowest and highest points",
    "span_nm": "the diagonal of the area the pattern fit inside",
    "max_turn_rate_dps": "how quickly the heading changed in the sharpest sustained turn",
    "racetrack_legs": "how many straight legs the racetrack held",
    "leg_length_nm": "the average length of one straight leg",
    "lateral_spacing_nm": "the distance between the two parallel lines",
}
# A duration over a pattern's analysis window, not over a detected episode.
WINDOW_DURATION_MEANING = "how long the analyzed stretch of track lasted"
# A measurement without a line of its own says what kind of figure it is.
SCOPE_MEANINGS = {
    "saved-report": "one saved report from the aircraft",
    "peak-report": "the peak report in the episode",
    "analysis-window": "measured over the analyzed interval",
    "archived-label": "the last archived label, carried between saved positions",
    "reference-data": "a reference figure, not a reading from the aircraft",
    "discontinuity-pair-not-physical-travel": "calculated between inconsistent positions; not physical travel",
    "surrounding-reports": "from the saved reports around the outlying value",
}
# General caveats every record repeats; the field guide explains them once.
BOILERPLATE_LIMITATIONS = (
    "Aircraft metadata is a current database match",
    "Archive timestamps do not establish",
    "Individual field ages are unknown",
)
FIELD_GUIDE_LINK = "How to read these numbers"
# The kinds the field guide explains, each under the anchor #kind-<kind> (tools/ghost-theme/
# kind-guide.mjs): every kind a site topic routes that can lead a story. A context kind only adds
# to another story, except a hold, which becomes a story once it lasts long enough
# (curation.HOLDING_STORY_SECONDS).
GUIDE_KINDS = frozenset(
    name
    for name, entry in kinds.KINDS.items()
    if entry.site_topic and (not entry.context_only or name == "prolonged_holding")
)


def field_guide_url(kind):
    """The field guide, opened at the kind's entry when it has one."""
    anchor = "#kind-" + kind.replace("_", "-") if kind in GUIDE_KINDS else ""
    return f"{site_origin.get()}/field-guide/{anchor}"


def key_facts(event):
    """The headline measurements as (label, value HTML, unit, meaning), at most five.

    The list is the kind's stories.HEADLINE_MEASUREMENTS, which a snapshot event's
    headline_measurements must equal; a ledger projection, which the card checks
    render too, does not carry that field. With none of its keys recorded, the first
    five measurements lead; a detection-rule figure is a site setting and never does.
    """
    first = {}
    for item in event["measurements"]:
        first.setdefault(item["key"], item)
    headline = stories.HEADLINE_MEASUREMENTS.get(event["kind"], ())
    chosen = [first[key] for key in headline if key in first]
    if not chosen:
        chosen = [item for item in event["measurements"] if item["scope"] != "detection-rule"]
    return [_key_fact(item) for item in chosen[:KEY_FACT_LIMIT]]


def _key_fact(item):
    value, unit = item["value"], item["unit"]
    if item["key"] == "min_separation_nm" and isinstance(value, (int, float)) and value < 1:
        # Under a nautical mile, the separation reads in feet, rounded to ten (half up).
        feet = _value(story_facts.separation_feet(value))
        return item["label"], feet, "ft", f"about {feet} feet apart"
    meaning = MEASUREMENT_MEANINGS.get(item["key"]) or SCOPE_MEANINGS.get(item["scope"], "a recorded value")
    if item["key"] == "duration_seconds" and item["label"] == "Analysis duration":
        meaning = WINDOW_DURATION_MEANING
    if unit == "s" and isinstance(value, (int, float)) and value >= 60:
        # A duration of a minute or more reads in minutes and seconds.
        minutes, seconds = divmod(round(value * 1000), 60000)
        text = f"{minutes:,} min" + (f" {_value(seconds / 1000)} sec" if seconds else "")
        return item["label"], text, "", meaning
    return item["label"], _value(value), unit, meaning


def card_limitations(event):
    """The kind's own limitations; the field guide covers the general caveats."""
    return [text for text in event["limitations"] if not text.startswith(BOILERPLATE_LIMITATIONS)]


def _key_facts_html(facts):
    rows = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{value}"
        + (f" <small>{escape(unit)}</small>" if unit else "")
        + f'</dd><dd class="event-key-meaning">{escape(meaning)}</dd></div>'
        for label, value, unit, meaning in facts
    )
    return f'<dl class="event-key-facts">{rows}</dl>'


def event_card(snapshot, event_id):
    """Return escaped HTML for one event after validating the entire snapshot."""
    return _event_card(_event(snapshot, event_id))


def event_cards(snapshot):
    """Return {public_event_id: html}, validating once before rendering any card."""
    snapshot = validate_snapshot(snapshot)
    return {event["id"]: _event_card(event) for event in snapshot["events"]}


def _event(snapshot, event_id):
    validated = validate_snapshot(snapshot)
    event = next((item for item in validated["events"] if item["id"] == event_id), None)
    if event is None:
        raise ValueError("Public event not found in this snapshot")
    return event


def draft_request(snapshot, event_id, *, prepared_at=None):
    """Prepare a draft request and private audit seed without publishing anything.

    Ghost's published_at is deliberately the observation date. It is not an
    import or release timestamp. Successful requests must be recorded separately
    with record_response; preparing this bundle establishes none of those events.
    """
    event = _event(snapshot, event_id)
    prepared_at = utc(prepared_at or now_utc())
    if _instant(prepared_at) < _instant(event["observed_end"]):
        raise ValueError("A historical post cannot use a future observation")
    html = article_html(event, display_review=False)
    lexical = {
        "root": {
            "children": [{"type": "html", "version": 1, "html": html}],
            "direction": None,
            "format": "",
            "indent": 0,
            "type": "root",
            "version": 1,
        }
    }
    post = {
        "slug": article_slug(event["article_path"]),
        "lexical": json.dumps(lexical, ensure_ascii=False),
        "status": "draft",
        "visibility": "public",
        "published_at": ghost_post_date(event["observed_start"]),
        "email_only": False,
        # Ghost accepts at most 300 characters in the excerpt. A longer summary
        # stays complete in the body rather than losing a qualification.
        **{field: value for field, value in post_fields(event).items() if value is not None},
    }
    tags = []
    if event.get("context"):
        # The primary public tag is the topic, matching /topics/{slug}/.
        tags.append(topic_tag(event["context"]["topic"]))
    if "weekly_spotlight" in event:
        from .weekly_spotlight import binding

        tags.extend([{"name": "Weekly spotlight"}, {"name": "#weekly-" + binding(event)}])
    if tags:
        post["tags"] = tags
    return {
        "schema_version": 1,
        "date_policy": "observation-start",
        "request": {
            "method": "POST",
            "path": "/ghost/api/admin/posts/",
            "headers": {"Content-Type": "application/json", "Accept-Version": "v6.0"},
            "body": {"posts": [post]},
        },
        "audit": {
            "schema_version": 1,
            "event_id": event["id"],
            "event_revision": event["revision"],
            "evidence_version": event["evidence_version"],
            "research_version": binding(event) if "weekly_spotlight" in event else None,
            "synthetic": event["synthetic"],
            "article_path": event["article_path"],
            "observed_start": event["observed_start"],
            "observed_end": event["observed_end"],
            "prepared_at": prepared_at,
            "imported_at": None,
            "first_released_at": None,
            "last_released_at": None,
            "last_changed_at": None,
            "ghost_post_id": None,
            "ghost_created_at": None,
            "ghost_updated_at": None,
            "history": [],
        },
    }


def article_excerpt(event):
    """The story's dek; Ghost's deck has a 300-character limit, so longer summaries stay in the body."""
    if event.get("story"):
        return event["story"]["dek"]
    return event["summary"] if len(event["summary"]) <= 300 else None


def post_fields(event):
    """Every Ghost field the publisher writes from one event; None leaves a field empty."""
    fields = dict.fromkeys(GENERATED_FIELDS)
    fields.update(title=event["title"], custom_excerpt=article_excerpt(event))
    if story := event.get("story"):
        fields.update(
            meta_title=story["seo_title"],
            meta_description=story["dek"],
            og_title=story["headline"],
            og_description=story["dek"],
            twitter_title=story["headline"],
            twitter_description=story["dek"],
        )
    return fields


@functools.cache
def _topic_titles():
    """Topic titles from the verified topics table; an unreadable table names no topic."""
    topics = (story_facts.table("topics") or {}).get("topics", {})
    return {
        slug: item["title"]
        for slug, item in topics.items()
        if isinstance(item, dict) and isinstance(item.get("title"), str)
    }


def topic_tag(slug):
    """The topic's Ghost tag; the slug keeps it the same tag if its title is edited."""
    name = _topic_titles().get(slug) or slug.replace("-", " ").capitalize()
    return {"name": name, "slug": slug}


def _linked(paragraphs):
    """Escaped paragraphs whose first mention of each glossary term links to the field guide."""
    pending, output = list(GLOSSARY), []
    guide = site_origin.get() + "/field-guide/"
    for paragraph in paragraphs:
        text, found = escape(paragraph), []
        for anchor, pattern in pending:
            match = pattern.search(text)
            if match and not any(match.start() < end and start < match.end() for start, end, _ in found):
                found.append((match.start(), match.end(), anchor))
        pending = [item for item in pending if item[0] not in {anchor for _, _, anchor in found}]
        for start, end, anchor in sorted(found, reverse=True):
            text = text[:start] + f'<a href="{guide}#{anchor}">{text[start:end]}</a>' + text[end:]
        output.append(text)
    return output


def type_box(event):
    """The sourced type facts (tp.) an event's type box lists: the type's profile facts (the routing's
    type profile) that the story's text does not quote (public_prose.quotes), so the box never
    repeats the story. A record staged before the routing named the profile, or whose story does not
    list every profile fact, was written when the box listed the facts the text used, and keeps them."""
    story = event["story"]
    listed = story["facts"]
    profile = ((event.get("context") or {}).get("type") or {}).get("profile")
    if not isinstance(profile, list) or any(key not in listed for key in profile):
        return [value for key, value in listed.items() if key.startswith("tp.") and isinstance(value, str)]
    texts = [
        text
        for text in (story.get("headline"), story.get("dek"), *(story.get("paragraphs") or ()))
        if isinstance(text, str)
    ]
    facts = [listed[key] for key in profile if isinstance(listed[key], str)]
    return [fact for fact in facts if not any(public_prose.quotes(text, fact) for text in texts)]


def _type_background(event, *, legacy=False):
    """The sourced type facts the story's type box lists (type_box), and the sources behind its text.

    `legacy` renders the Sources heading as every story had it before October 2026,
    always a third-level one, so an older live block is still recognised as ours.
    """
    story, routing = event["story"], event.get("context") or {}
    aircraft = routing.get("type")
    facts = type_box(event)
    # A story whose text quotes every type fact keeps the section and its hub link, without the list.
    profiled = any(key.startswith("tp.") for key in story["facts"])
    lines = []
    if aircraft and (facts or profiled):
        name = escape(aircraft["name"])
        lines.extend(
            [
                '<section class="type-background">',
                f"<h2>About the {name}</h2>",
                *(["<ul>", *(f"<li>{escape(fact)}</li>" for fact in facts), "</ul>"] if facts else []),
                f'<p><a href="{site_origin.get()}/aircraft/{aircraft["slug"]}/">Other {name} stories</a></p>',
                "</section>",
            ]
        )
    # Two facts may cite the same page under different source ids; the page is listed once,
    # where it first appeared.
    sources = {}
    for source in story["sources"]:
        sources.setdefault((source["url"], source["title"]), source)
    sources = list(sources.values())
    if sources:
        # Under "About the type" the list is a subsection; without it, as for a certified
        # limit or a code meaning, a third-level heading would skip a level after the title.
        level = "h3" if lines or legacy else "h2"
        lines.extend(
            [f'<aside class="story-sources" aria-label="Sources"><{level}>Sources</{level}>', "<ol>"]
        )
        lines.extend(
            f'<li><a href="{escape(source["url"], quote=True)}" rel="external noopener noreferrer">'
            f"{escape(source['title'])}</a>, {escape(source['publisher'])}</li>"
            for source in sources
        )
        lines.extend(["</ol>", "</aside>"])
    return "\n".join(lines)


def story_html(event, *, legacy=False):
    """The owned prose block. Its marker is the digest of its content."""
    if not event.get("story"):
        return ""
    inner = "\n".join(f"<p>{paragraph}</p>" for paragraph in _linked(event["story"]["paragraphs"]))
    if background := _type_background(event, legacy=legacy):
        inner += "\n" + background
    marker = hashlib.sha256(inner.encode()).hexdigest()
    return f'<section class="story-prose" data-story-version="{marker}">\n{inner}\n</section>\n'


def article_html(event, *, display_review=True):
    """One article body for the validated static preview and native Ghost card."""
    from .weekly_spotlight import research_html

    return (
        story_html(event)
        + ("" if article_excerpt(event) else "<p>" + escape(event["summary"]) + "</p>\n")
        + research_html(event)
        + "\n"
        + _event_card(event, display_review=display_review)
    )


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def owned_digests(html):
    """Digests of the generated evidence card (before its record details) and story block.

    Stored at publication, they identify an unedited live block without rendering
    it again, so a later change in the renderer never looks like an owner's edit.
    """
    from .weekly_spotlight import PublishedBlocks

    blocks = PublishedBlocks(html).blocks
    cards = [block for kind, block in blocks if kind == "evidence"]
    stories = [block for kind, block in blocks if kind == "story"]
    return {
        "card_sha256": _digest(cards[0].partition(RECORD_DETAILS)[0]) if len(cards) == 1 else None,
        "story_sha256": _digest(stories[0]) if len(stories) == 1 else None,
    }


def card_digest(block):
    return _digest(block.partition(RECORD_DETAILS)[0])


def observation_card_matches(event, html):
    """Compare rendered facts, allowing a newer editorial revision of those facts."""
    prepared = {"publication_status": "published", "published_at": None, "revised_at": None, **event}
    expected = _event_card(prepared, display_review=False)
    return html.partition(RECORD_DETAILS)[0] == expected.partition(RECORD_DETAILS)[0]


def _generated_story(block, story_digest, previous_event):
    """Whether a live prose block is still exactly the one the publisher wrote."""
    if story_digest is not None:
        return _digest(block) == story_digest
    # Without a stored digest the block is rendered again; the renderer's earlier Sources
    # heading counts too, so that change never reads as an owner's edit.
    # any() stops at the current rendering, so the legacy one is built only when it differs.
    return previous_event is not None and any(
        block == story_html(previous_event, legacy=legacy).rstrip("\n") for legacy in (False, True)
    )


def editor_html(post):
    """The HTML cards of a post's Lexical document, the representation the publisher edits."""
    try:
        children = json.loads(post.get("lexical") or "").get("root", {}).get("children")
    except (ValueError, AttributeError):
        children = None
    if not isinstance(children, list):
        return post.get("html") or ""
    return "\n".join(
        child["html"]
        for child in children
        if isinstance(child, dict) and child.get("type") == "html" and isinstance(child.get("html"), str)
    )


def story_edited(html, story_digest, previous_event):
    """Whether the owner changed or removed the published prose block; `html` is editor_html(post)."""
    from .weekly_spotlight import PublishedBlocks

    blocks = [block for kind, block in PublishedBlocks(html).blocks if kind == "story"]
    if not blocks:
        return story_digest is not None
    return not _generated_story(blocks[0], story_digest, previous_event)


def automatic_article_update(event, current_post, previous_event=None, *, story_digest=None):
    """Update owned blocks while retaining other Lexical nodes and HTML content.

    The evidence card and research are always regenerated. The prose block is
    replaced only while it is still the generated one, so an owner's edit stays;
    `story_digest` is the stored digest of the last generated block.
    """
    from .weekly_spotlight import PublishedBlocks, research_html

    lexical = json.loads(current_post["lexical"])
    children = lexical.get("root", {}).get("children")
    if not isinstance(children, list):
        raise ValueError("Missing Ghost editor document")
    if previous_event and previous_event["summary"] != event["summary"]:
        old_intro = (
            ("<p>" + escape(previous_event["summary"]) + "</p>\n")
            if not article_excerpt(previous_event)
            else ""
        )
        new_intro = ("<p>" + escape(event["summary"]) + "</p>\n") if not article_excerpt(event) else ""
        for child in children:
            html = child.get("html", "")
            if child.get("type") == "html" and 'class="observation-record ' in html:
                if old_intro and html.count(old_intro) == 1:
                    child["html"] = html.replace(old_intro, new_intro, 1)
                elif not old_intro and new_intro:
                    child["html"] = new_intro + html
                break
    replacement = _event_card(event, display_review=False).rstrip("\n")
    prose = story_html(event).rstrip("\n")
    evidence_count, research_count, story_count = 0, 0, 0
    for child in children:
        if child.get("type") != "html" or not isinstance(child.get("html"), str):
            continue
        html = child["html"]
        for kind, block in PublishedBlocks(html).blocks:
            if html.count(block) != 1:
                raise ValueError("Ambiguous owned article block")
            if kind == "evidence":
                if f'data-event-id="{event["id"]}"' not in block:
                    raise ValueError("Observation block belongs to another event")
                html = html.replace(block, replacement, 1)
                evidence_count += 1
            elif kind == "research":
                html = html.replace(block, research_html(event).rstrip("\n"), 1)
                research_count += 1
            elif kind == "story":
                story_count += 1
                if _generated_story(block, story_digest, previous_event):
                    html = html.replace(block, prose, 1)
        child["html"] = html
    if evidence_count != 1 or research_count > 1 or story_count > 1:
        raise ValueError("Missing or duplicate owned article blocks")
    if "weekly_spotlight" in event and not research_count:
        children.append({"type": "html", "version": 1, "html": research_html(event)})
    # A post that never had a prose block gains one above its card. One the
    # owner removed after publication stays removed.
    if prose and not story_count and story_digest is None and not (previous_event or {}).get("story"):
        child = next(
            child
            for child in children
            if child.get("type") == "html" and 'class="observation-record ' in child.get("html", "")
        )
        child["html"] = prose + "\n" + child["html"]
    return json.dumps(lexical, ensure_ascii=False)


def research_update_request(snapshot, event_id, current_post, *, expected_ghost_id):
    """Replace only the weekly research block; keep the article, media and status."""
    from .weekly_spotlight import PublishedBlocks, binding, research_html

    event = _event(snapshot, event_id)
    if "weekly_spotlight" not in event:
        raise ValueError("The event has no completed weekly research")
    _post_identity(current_post, event["article_path"])
    if current_post["id"] != ghost_post_id(expected_ghost_id):
        raise ValueError("The current post does not match the private publication binding")
    updated = utc(current_post.get("updated_at"))
    lexical = json.loads(current_post.get("lexical", ""))
    children = lexical.get("root", {}).get("children")
    if not isinstance(children, list):
        raise ValueError("A current Ghost Lexical document is required")
    parsed = PublishedBlocks(
        "\n".join(child.get("html", "") for child in children if child.get("type") == "html")
    )
    cards = [html for kind, html in parsed.blocks if kind == "evidence"]
    if len(cards) != 1 or not observation_card_matches(event, cards[0]):
        raise ValueError(
            "Refresh the article's observation card before attaching research for changed evidence"
        )
    # Our publication bundles use top-level HTML cards. Refuse an ambiguous
    # nested research block rather than replacing unrelated article content.
    marker = re.compile(
        r'<section class="weekly-research" data-weekly-research="[a-f0-9]{64}"[^>]*>.*?</section>', re.S
    )
    replaced = 0
    for child in children:
        if child.get("type") == "html" and isinstance(child.get("html"), str):
            child["html"], count = marker.subn("", child["html"])
            replaced += count
    if replaced > 1 or "data-weekly-research=" in json.dumps(lexical):
        raise ValueError("Ambiguous existing research blocks")
    children[:] = [
        child for child in children if child.get("type") != "html" or child.get("html", "").strip()
    ]
    children.append({"type": "html", "version": 1, "html": research_html(event)})
    tags = current_post.get("tags")
    if not isinstance(tags, list) or any(not isinstance(tag.get("name"), str) for tag in tags):
        raise ValueError("Read the existing post with its tags before preparing an update")
    names = [tag["name"] for tag in tags if not tag["name"].startswith("#weekly-")]
    names = list(dict.fromkeys([*names, "Weekly spotlight", "#weekly-" + binding(event)]))
    return {
        "schema_version": 1,
        "request": {
            "method": "PUT",
            "path": f"/ghost/api/admin/posts/{current_post['id']}/?save_revision=true",
            "headers": {"Content-Type": "application/json", "Accept-Version": "v6.0"},
            "body": {
                "posts": [
                    {
                        "updated_at": updated,
                        "lexical": json.dumps(lexical, ensure_ascii=False),
                        "tags": [{"name": name} for name in names],
                    }
                ]
            },
        },
        "audit": {
            "event_id": event["id"],
            "event_revision": event["revision"],
            "evidence_version": event["evidence_version"],
            "research_version": binding(event),
            "ghost_post_id": current_post["id"],
        },
    }


def date_update_request(snapshot, event_id, current_post):
    """Set only the post date, using the last GET's collision token.

    This does not change draft/published state, content, authors or tags. A future
    publisher must read the current post immediately before sending the update.
    """
    event = _event(snapshot, event_id)
    identifier, updated_at = _post_identity(current_post, event["article_path"])
    return {
        "method": "PUT",
        "path": "/ghost/api/admin/posts/" + quote(identifier, safe="") + "/",
        "headers": {"Content-Type": "application/json", "Accept-Version": "v6.0"},
        "body": {
            "posts": [
                {
                    "id": identifier,
                    "updated_at": updated_at,
                    "published_at": ghost_post_date(event["observed_start"]),
                }
            ]
        },
    }


def _post_identity(post, article_path):
    if not isinstance(post, dict):
        raise ValueError("A successful Ghost post response is required")
    identifier = ghost_post_id(post.get("id"))
    if post.get("slug") != article_slug(article_path):
        raise ValueError("Ghost post does not match the event article")
    if post.get("status") not in {"draft", "published"} or post.get("visibility") != "public":
        raise ValueError("Only ordinary public-visibility web posts are supported")
    if post.get("email_only") or post.get("email") is not None or post.get("newsletter") is not None:
        raise ValueError("Newsletter or email-only posts are outside this workflow")
    return identifier, utc(post.get("updated_at"))


def record_response(audit, post, *, operation, completed_at):
    """Return a private audit revision after a successful future Ghost request.

    Callers persist this sidecar. A response's backdated published_at never sets
    imported_at or first_released_at. This function sends no request, grants no
    editorial approval and never stores a complete Ghost response.
    """
    if operation not in {"import", "release", "revise"}:
        raise ValueError("Unknown publication audit operation")
    if not isinstance(audit, dict) or audit.get("schema_version") != 1:
        raise ValueError("Invalid publication audit")
    identifier, updated_at = _post_identity(post, audit["article_path"])
    completed_at, created_at = utc(completed_at), utc(post.get("created_at"))
    if _instant(completed_at) < _instant(audit["prepared_at"]):
        raise ValueError("Response precedes preparation")
    if audit["last_changed_at"] and _instant(completed_at) < _instant(audit["last_changed_at"]):
        raise ValueError("Response precedes an already recorded operation")
    if _instant(created_at) > _instant(updated_at) or _instant(updated_at) > _instant(completed_at):
        raise ValueError("Ghost timestamps exceed the response time")
    if utc(post.get("published_at")) != ghost_post_date(audit["observed_start"]):
        raise ValueError("Ghost post date does not match observation start")
    if audit["ghost_post_id"] not in {None, identifier}:
        raise ValueError("Ghost post identity changed")
    if audit["ghost_created_at"] not in {None, created_at}:
        raise ValueError("Ghost post creation time changed")
    if audit["ghost_updated_at"] and _instant(updated_at) < _instant(audit["ghost_updated_at"]):
        raise ValueError("Stale Ghost response")
    if operation == "import" and post["status"] != "draft":
        raise ValueError("Backfill imports must start as drafts")
    if operation != "import" and (post["status"] != "published" or audit["synthetic"]):
        raise ValueError("A release audit requires a non-synthetic published web post")
    if operation != "import" and audit["imported_at"] is None:
        raise ValueError("Record the import before a release or revision")
    if operation == "revise" and audit["first_released_at"] is None:
        raise ValueError("Record the first release before a revision")
    receipt = {"operation": operation, "completed_at": completed_at, "ghost_updated_at": updated_at}
    result = deepcopy(audit)
    if any(
        row["operation"] == operation and row["ghost_updated_at"] == updated_at for row in result["history"]
    ):
        return result
    if operation == "import":
        if result["imported_at"] is not None:
            raise ValueError("This post was already imported")
        result["imported_at"] = completed_at
    elif operation == "release":
        result["first_released_at"] = result["first_released_at"] or completed_at
        result["last_released_at"] = completed_at
    result.update(
        ghost_post_id=identifier,
        ghost_created_at=created_at,
        ghost_updated_at=updated_at,
        last_changed_at=completed_at,
    )
    result["history"].append(receipt)
    return result


def _event_card(event, *, display_review=True):
    aircraft = event["aircraft"]
    name = aircraft.get("registration") or aircraft.get("callsign") or aircraft.get("icao_address")
    output = [
        '<section class="observation-record kg-width-wide" aria-label="Observation evidence" '
        f'data-event-id="{escape(event["id"], quote=True)}" '
        f'data-evidence-version="{escape(event["evidence_version"], quote=True)}">',
        f"<h2>Recorded data for {escape(name)}</h2>" if name else "<h2>Recorded data</h2>",
    ]
    if event["synthetic"]:
        output.append(
            '<p class="observation-note"><strong>Invented observation</strong> '
            "The flight, dates, and measurements are synthetic examples, not receiver history.</p>"
        )
    if event["publication_status"] == "unpublished" and display_review:
        output.append(
            '<p class="observation-note"><strong>Unpublished review</strong> '
            "This is an unpublished review copy.</p>"
        )
    overview = [
        ("Observed", _interval(event["observed_start"], event["observed_end"])),
        ("Event type", escape(TYPE_LABELS[event["primary_type"]])),
        ("Recorded source", escape(SOURCE_LABELS[event["source"]])),
    ]
    if event["published_at"] and display_review:
        overview.append(("Post date", _time(event["published_at"])))
    if event["revised_at"] != event["published_at"] and display_review:
        label = "Review updated" if event["publication_status"] == "unpublished" else "Last revised"
        overview.append((label, _time(event["revised_at"])))
    output.append(_record_table("Observation", overview))
    identity = [
        (label, aircraft[field])
        for field, label in (
            ("type_description", "Aircraft type"),
            ("type_code", "Type designator"),
            ("registration", "Tail number (registration)"),
            ("callsign", "Callsign"),
            ("icao_address", "ICAO address"),
        )
        if aircraft.get(field)
    ]
    if identity:
        output.append(_record_table("Aircraft", [(label, escape(value)) for label, value in identity]))
    if event.get("participants"):
        output.append(
            _record_table("Other aircraft", [_participant_row(item) for item in event["participants"]])
        )
    if event["measurements"]:
        output.append(
            '<h3>Measurements</h3><p class="measurement-note">Displayed measurements may be rounded.</p>'
        )
        facts = key_facts(event)
        if facts:
            output.append(_key_facts_html(facts))
    evidence = '<p class="evidence-hash">Evidence version: ' + _code(event["evidence_version"]) + ".</p>"
    if not event["measurements"]:
        # Nothing to fold away: the evidence version stands on its own.
        output.append(evidence)
    else:
        output.append(
            '<details class="evidence-details"><summary>All measurements and the evidence record</summary>'
        )
    groups = {}
    for item in event["measurements"]:
        key = tuple(item.get(field) for field in ("scope", "at_utc", "start_utc", "end_utc"))
        groups.setdefault(key, []).append(item)
    for items in groups.values():
        rows = []
        for item in items:
            unit = f" <small>{escape(item['unit'])}</small>" if item["unit"] else ""
            rows.append((item["label"], _value(item["value"]) + unit))
        output.append(
            '<div class="record-measurements-group">'
            + _record_table(measurement_scope(items[0]), rows)
            + "</div>"
        )
    if event["measurements"]:
        output.append(evidence + "</details>")
    if "watchlist" in event:
        output.append(_watchlist_table(event["watchlist"]))
    output.append(
        '<aside class="record-limits" aria-label="What this record cannot show">'
        "<h3>What this record cannot show</h3><ul>"
    )
    output.extend("<li>" + escape(limit) + "</li>" for limit in card_limitations(event))
    output.append(f'<li><a href="{field_guide_url(event["kind"])}">{FIELD_GUIDE_LINK}</a></li></ul></aside>')
    references = aircraft_references(event)
    if references:
        lookup = escape("Look up " + (name or "this aircraft"))
        output.append(f'<aside class="sources" aria-label="{lookup}"><h3>{lookup}</h3><ul>')
        for reference in references:
            output.append(
                f'<li><a href="{escape(reference["url"], quote=True)}" '
                f'rel="external noopener noreferrer">{escape(reference["label"])}</a></li>'
            )
        output.append(
            "</ul><p>These links are reference destinations. Their presence does not establish that their "
            "contents verify this observation.</p></aside>"
        )
    output.extend(
        [
            '<details class="record-details"><summary>Record details</summary>',
            "<p>Public event ID: " + _code(event["id"]) + ".</p>",
            f"<p>Record revision: {event['revision']}.</p>",
            "</details></section>",
        ]
    )
    return "\n".join(output) + "\n"
