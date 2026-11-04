"""Evidence-driven publication without a human draft-approval queue.

The private ledger keeps decisions and first-release dates across receiver
retention, restarts and uncertain network outcomes. Ghost owns the published
articles; only an exact owned post can be adopted after a retry.
"""

import functools
import json
from collections import defaultdict
from copy import deepcopy
from typing import NamedTuple

from . import (
    airports,
    curation,
    places,
    public_prose,
    publication,
    selection,
    stories,
    story_facts,
    story_urls,
    watchlists,
)
from . import editorial as engine
from . import public_events as public
from .feed_adapter import binding_identity, record_associations, source_association
from .ghost_client import GHOST_ERRORS
from .rules import number
from .weekly_spotlight import PublishedBlocks, check_source

POLICY_VERSION = 4
UNPROJECTABLE_PAUSE = 3
# Automatic evidence rejections, as distinct from an owner's removal.
REJECTED = {"ineligible_source", "invalid_evidence"}
ERRORS = public.DATA_ERRORS
# Each post the publisher owns carries this tag followed by its record's UUID.
OWNER_TAG_PREFIX = "#autopilot-"
# A track's first or last position this much farther than its closest from an airport is still
# on that airport's runways (_airport).
ENDPOINT_MARGIN_NM = 0.5


def owner_tag(record_id):
    return OWNER_TAG_PREFIX + record_id


def has_owner_tag(post, record_id):
    """The post carries the ownership tag of this record."""
    tag = owner_tag(record_id)
    return any(isinstance(item, dict) and item.get("name") == tag for item in post.get("tags") or ())


def require_story_scheme(ledger):
    """Refuse a ledger written before the /stories/ URL scheme, before anything changes it.

    That ledger belongs to the code that wrote it: this code cannot project its
    records, yet stage() would add new records to it on every pass.
    """
    older = sum(
        isinstance(record, dict)
        and isinstance(record.get("editorial"), dict)
        and isinstance(path := record["editorial"].get("article_path"), str)
        and not path.startswith("/stories/")
        for record in ledger.get("records", [])
    )
    if older:
        raise ValueError(
            f"The ledger holds {older} articles outside /stories/, from before the redo; run this "
            "code only on a ledger the bootstrap made"
        )
    return ledger


def _quiet(function, *args, default=None):
    """function(*args), or `default` when the private record or finding is malformed."""
    try:
        return function(*args)
    except ERRORS:
        return default


def source_identity(source):
    # SQLite IDs can be reused after retention or a restored database.
    return tuple(source.get(key) for key in ("hex", "kind", "first_seen"))


def block(record, reason):
    record.setdefault("automation", {})["blocked"] = reason


def eligible(source):
    """Whether a saved finding may become an unattended story (selection.publishable)."""
    return selection.publishable(source)


def _eligible(source):
    return _quiet(eligible, source, default=False)


def _project(record):
    return _quiet(lambda: public._projection(record["evidence"], record["editorial"], strict=False))


def record_valid(record):
    """Whether one record validates on its own."""
    return _quiet(public.validate_record, record) is not None


def _released(record):
    automatic = record.get("automation") or {}
    return bool(
        automatic.get("released_at")
        or automatic.get("source_kind") == "frozen"
        or _quiet(public._approved_revision, record) is not None
    )


def _removed(record):
    """An owner withdrew, unpublished or deleted the story."""
    return record["state"] == "withdrawn" or bool((record.get("automation") or {}).get("removed"))


def _prior(record):
    """A released story curation weighs when it chooses later candidates.

    An owner's unpublication or deletion in Ghost counts as a repeat. A withdrawal
    in the ledger is passed on as "withdrawn": it holds no later candidate, but its
    own finding never returns as a story. An automatic suspension or an evidence
    rejection does not hold the aircraft or its identity cooldown.
    """
    if not _released(record):
        return False
    automatic = record.get("automation") or {}
    # A suspension caused by our own projection failure is technical, not a rejection.
    if _removed(record) or automatic.get("unprojectable"):
        return True
    if automatic.get("suspended") or automatic.get("blocked") in REJECTED:
        return False
    source = automatic.get("source")
    return source is None or _eligible(source)


def _prior_event(record, event=None):
    """The current projection, or the last published copy while it no longer projects.

    `event` is the current projection when the caller has already made it.
    """
    event = event if event is not None else _project(record)
    receipt = (record.get("automation") or {}).get("published_event")
    if event is None and isinstance(receipt, dict) and receipt.get("observed_start"):
        return receipt
    return event


def _canonical(record, event):
    """Whether the event carries its exception proof and the saved finding still passes the
    evidence and context rules, rerun without the descent context."""
    if not event.get("priority_evidence"):
        return False
    source = (record.get("automation") or {}).get("source")
    return source is None or _quiet(curation.exceptional_event, source, default=False)


def _exception_block(automatic):
    """The stored selection decision (automation.exception), or None when it is malformed or
    was made under another curation version."""
    block = automatic.get("exception")
    if (
        isinstance(block, dict)
        and type(block.get("exceptional")) is bool
        # A hand-edited reason may be unhashable; it makes the block absent, never an error.
        and isinstance(block.get("reason"), (str, type(None)))
        and block.get("curation_version") == curation.VERSION
    ):
        return block
    return None


def exception_standing(record, event, *, sticky=False):
    """Whether a record stands as exceptional, before the daily cap.

    The event must carry its exception proof and the saved finding must still pass the
    evidence and context rules (rerun without the descent context, so a repeated descent
    or a full per-aircraft cap is never promoted by the rerun). The selection's saved
    decision then decides: `automation.exceptional`, or the stored decision's
    `exceptional` when the boolean is missing. A `sticky` story already holds exceptional
    priority and keeps it through a later selection-time demotion, such as another descent
    nearby, but never through evidence or context that no longer qualifies.
    """
    automatic = record.get("automation") or {}
    stored = automatic.get("exceptional")
    if stored is None and (block := _exception_block(automatic)) is not None:
        stored = block["exceptional"]
    return _canonical(record, event) and (sticky or stored is not False)


def _facts(record, event):
    source = (record.get("automation") or {}).get("source")
    if not source:
        aircraft = event["aircraft"]
        source = {
            "kind": event["kind"],
            "hex": (aircraft.get("icao_address") or "").lower(),
            "evidence": {"aircraft": {"t": aircraft.get("type_code")}},
        }
    return curation.facts(source)


def _entry_exception(record, event):
    """A prior story's exceptional standing: what it was released with, or else its standing now."""
    receipt = (record.get("automation") or {}).get("published_event")
    return _published_exception(record, receipt) if receipt else exception_standing(record, event)


def _story(record, event, current=None):
    """A ledger record as a prior story for curation's shared repeat policy."""
    automatic = record.get("automation") or {}
    entry = {
        "id": record["id"],
        "episode_key": record["episode_key"],
        "kind": event["kind"],
        "start": public._instant(event["observed_start"]).timestamp(),
        "day": event["observed_start"][:10],
        "exceptional": _entry_exception(record, event),
        # Callers pass prior stories only; a removed or suspended one holds no daily slot.
        "live": not _removed(record) and not automatic.get("suspended"),
        "removed": _removed(record),
        # An in-place withdrawal retracts the story, so it never holds later candidates.
        "withdrawn": record["state"] == "withdrawn",
        **_facts(record, event),
    }
    if "routine_type" in automatic:
        # The selection's saved decision. Without one, curation works out whether the
        # type was routine on this story's own date.
        entry["routine_type"] = bool(automatic["routine_type"])
    source = automatic.get("source")
    if current is not None and source:
        group = current.get(source_association(source, automatic))
        current_source = (
            next(
                (item for item in group["events"] if item["id"] == automatic.get("engine_finding_id")), source
            )
            if group
            else source
        )
        entry.update(
            group_key=curation.episode_key(group) if group else None,
            source=list(source_identity(current_source)),
        )
    return entry


def history(ledger, current=None, *, events=None):
    """Prior stories from the ledger; `current` maps source identities to journal groups.

    `events` holds the records' current projections by ID when the caller has made them.
    """
    entries = []
    for record in ledger.get("records", []):
        event = _prior_event(record, (events or {}).get(record.get("id")))
        if event is None or not _prior(record):
            continue
        if (entry := _quiet(_story, record, event, current)) is not None:
            entries.append(entry)
    return entries


def repeat_reason(log, story):
    """Why curation's shared repeat rule holds back an ordinary story, or None.

    A story whose type the selection found locally routine is held by that rule too.
    """
    routine = {story["type"]} if story.get("routine_type") and story.get("type") else frozenset()
    return log.repeat(story, routine)


def saved_source(finding):
    """Copy saved evidence and normalize an absent watchlist match for the ledger."""
    source = deepcopy(finding)
    source["evidence"]["watchlist"] = source["evidence"].get("watchlist") or {}
    return source


def evidence(source, interval):
    return stories.evidence_packet(saved_source(source), observation_interval=interval)


def _slug(record):
    """The bare Ghost slug of a record's story URL."""
    return public.article_slug(record["editorial"]["article_path"])


def observation_start(record):
    """When a record's observation began, or None when its evidence is unreadable."""
    return _quiet(lambda: public.instant(record["evidence"]["observation"]["start_utc"]))


def _point(raw):
    """A position as {lat, lon}, from a saved sample or a [lat, lon] media pair."""
    if isinstance(raw, dict):
        lat, lon = number(raw.get("lat")), number(raw.get("lon"))
        if raw.get("stale") or raw.get("conflicting_position"):
            return None
    elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
        lat, lon = number(raw[0]), number(raw[1])
    else:
        return None
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return {"lat": lat, "lon": lon}


SAMPLE_KEYS = ("maneuver_samples", "pattern_samples", "track")


# A TCAS advisory's own samples are its messages; its positions are the context samples around
# them, which the media step draws first too (tools/run_autopilot.py).
CONTEXT_SAMPLE_KINDS = frozenset({"tcas_ra"})


def samples(source):
    """A finding's saved samples, from the first of its sample lists that has any, or None; an
    advisory's context samples come first (CONTEXT_SAMPLE_KINDS)."""
    found = (source or {}).get("evidence") or {}
    keys = SAMPLE_KEYS
    if (source or {}).get("kind") in CONTEXT_SAMPLE_KINDS:
        keys = ("context_samples", *keys)
    return next((found[name] for name in keys if found.get(name)), None)


def _drawable(parts):
    """Whether saved media parts hold a segment the map can draw: two positions or more."""
    return isinstance(parts, list) and any(isinstance(part, list) and len(part) >= 2 for part in parts)


def _points(source=None, parts=None):
    """Aircraft positions for a place name: saved media parts with a drawable segment first, else
    the finding's samples, as the media step draws them."""
    if _drawable(parts):
        raw = [point for part in parts if isinstance(part, list) for point in part]
    else:
        raw = samples(source) or []
    return [point for item in (raw if isinstance(raw, list) else []) if (point := _point(item))] or None


def record_points(record):
    """The aircraft positions a record's story is placed by: its map parts, else its samples."""
    automatic = record.get("automation") or {}
    return _points(automatic.get("source"), automatic.get("media_parts"))


def _place(points):
    """The nearest named place to the aircraft's positions, never to the receiver."""
    return places.nearest_place(points) if points else None


def _airport(points, kind):
    """The nearest public airport to the aircraft's positions whose name a story may use, never
    one the track starts or ends at, which would read as a take-off or landing.

    A take-off or landing roll puts several positions on the runway, so the closest may be
    any of them: an airport within ENDPOINT_MARGIN_NM more than its distance of the first or
    last position counts as the track's start or end. The three closest airports are tried
    in turn. The latest answers are kept (_airport_answer), per runway table, positions, and kind.
    """
    if not points or kind in story_facts.AIRPORT_EXCLUDED_KINDS:
        return None
    try:
        positions = tuple((point["lat"], point["lon"]) for point in points)
        hash(positions)
    except (KeyError, TypeError):
        return _find_airport(points, kind)
    global _AIRPORT_TABLE
    table = airports._airports()
    if _AIRPORT_TABLE is not table:
        # A new runway table (a test's, or a cache clear) answers afresh.
        _AIRPORT_TABLE = table
        _airport_answer.cache_clear()
    found = _airport_answer(positions, kind)
    return dict(found) if found else None


# The runway table the kept answers were found in.
_AIRPORT_TABLE = None
# A writer pass reads every record's track twice, so the cache holds a few thousand tracks: a
# ledger larger than the cache would get no hits at all, and it grows by about nine a day.
AIRPORT_ANSWERS = 4096


@functools.lru_cache(maxsize=AIRPORT_ANSWERS)
def _airport_answer(positions, kind):
    """_find_airport for (lat, lon) pairs, the only values of a position it reads."""
    return _find_airport([{"lat": lat, "lon": lon} for lat, lon in positions], kind)


def _find_airport(points, kind):
    for found in airports.ranked(points)[:3]:
        candidate = airports.entry(found)
        ends = (airports.distance_nm(candidate["ident"], point) for point in (points[0], points[-1]))
        if (
            candidate["at"] in {0, len(points) - 1}
            or any(
                distance is not None and distance <= candidate["nm"] + ENDPOINT_MARGIN_NM for distance in ends
            )
            or not story_facts.airport_name(candidate["name"], kind)
        ):
            continue
        return candidate
    return None


def recent_stories(others, start, used=None):
    """Stories observed by `start`, for the variety guard, which applies its own windows.

    Later stories check themselves against this one when they are written, so a
    regeneration in observation order reads as if written day by day. `used`, when given,
    keeps each story's fact ids for the rest of a pass (StoryInputs.used).
    """
    recent = []
    for record in others:
        story = (record.get("editorial") or {}).get("story")
        began = observation_start(record)
        if (
            record.get("state") == "withdrawn"
            or not isinstance(story, dict)
            or began is None
            or began > start
        ):
            continue
        aircraft = record["evidence"].get("aircraft") or {}
        recent.append(
            {
                "kind": record["evidence"].get("kind"),
                "type_code": aircraft.get("type_code"),
                "registration": aircraft.get("registration"),
                "date": began.date().isoformat(),
                # The fact ids alone: the AI writer prefers type facts a recent story left unused.
                "facts": _used_facts(record, story, used),
                # The values name the dek skeleton as they named the stored ones (editorial.dek_skeleton).
                "fact_values": story.get("facts"),
                **{
                    key: story.get(key)
                    for key in (
                        "headline",
                        "dek",
                        "seo_title",
                        "paragraphs",
                        "angle",
                        "opener_skeleton",
                        "headline_skeleton",
                    )
                },
            }
        )
    return sorted(recent, key=lambda item: item["date"])


def _used_facts(record, story, used):
    """_listed_facts for a record's story, once per pass when `used` keeps them: a pass asks for
    every story's list once per other record. A story keys afresh when its facts, source, text,
    or database description change, so a rewrite within the pass is read anew."""
    kind = record["evidence"].get("kind")
    # The record's database description names the rebuilt type as build() did (profile_facts).
    description = (record["evidence"].get("aircraft") or {}).get("type_description")
    if used is None:
        return _listed_facts(story, kind, description)
    paragraphs = story.get("paragraphs")
    key = (
        record.get("episode_key"),
        kind,
        story.get("facts_digest"),
        story.get("source"),
        story.get("headline"),
        story.get("dek"),
        tuple(paragraphs) if isinstance(paragraphs, list) else paragraphs,
        tuple(story["facts"]) if isinstance(story.get("facts"), dict) else None,
        description,
    )
    try:
        if key not in used:
            used[key] = _listed_facts(story, kind, description)
    except TypeError:
        # A malformed story's text is not hashable; it is worked out each time.
        return _listed_facts(story, kind, description)
    return list(used[key])


def _listed_facts(story, kind, description=None):
    """The ids of the facts a story listed. An AI story keeps the template's type facts so its
    "About" section stays (public_writer.assemble); of those, only the ones its own text relies
    on count as used."""
    listed = story.get("facts") if isinstance(story.get("facts"), dict) else {}
    typed = tuple(
        (key, value)
        for key, value in sorted(listed.items())
        if key in ("id.type", "id.type_code") or key.startswith("tp.")
    )
    if story.get("source") != "ai" or not any(key.startswith("tp.") for key, _value in typed):
        return sorted(listed)
    texts = tuple(
        text
        for text in (story.get("headline"), story.get("dek"), *(story.get("paragraphs") or ()))
        if isinstance(text, str)
    )
    bound = _bound_profiles(kind, typed, texts, description)
    return sorted(key for key in listed if not key.startswith("tp.") or key in bound)


@functools.lru_cache(maxsize=4096)
def _bound_profiles(kind, typed, texts, description=None):
    """The type-profile fact ids among `typed` ((id, value) pairs) that the texts rely on; every
    pass reads the same published stories again, so each is checked once per process."""
    rebuilt = story_facts.profile_facts(kind, dict(typed), description)
    return frozenset(public_prose.bound_facts(rebuilt, list(texts)))


class StoryInputs(NamedTuple):
    """What a story's facts read beside its packet, loaded once per pass from the state
    directory: the sourced type profiles (weekly_research.story_profiles) and the cached
    Wikidata answers for watchlist entry links (entity_context.load). `used`, a dict a pass
    starts empty, keeps each published story's fact ids (recent_stories) for that pass; None
    works them out for every story each time."""

    profiles: dict | None = None
    entities: dict | None = None
    used: dict | None = None


NO_INPUTS = StoryInputs()


def _story_facts(packet, points, inputs=NO_INPUTS, related=()):
    """The story facts for a packet, with the place and nearest public airport of `points`
    and the earlier stories of its registration (related_stories)."""
    return story_facts.build(
        packet,
        public._projection(packet, public.PLACEHOLDER_EDITORIAL),
        place=_place(points),
        airport=_airport(points, packet.get("kind")),
        profiles=inputs.profiles,
        entities=inputs.entities,
        related=related,
    )


class RelatedIndex:
    """A ledger's records by registration, for related_stories over one pass.

    It indexes the records once and again only when the ledger gains or loses a record.
    related_stories still checks each record's own registration, so an indexed record whose
    evidence changed in the pass never matches the wrong airframe; a change to related is
    never a correction, so one gained in place waits for the next pass.
    """

    def __init__(self, ledger):
        self.ledger = ledger
        self.count = None
        self.by_registration = {}

    def candidates(self, registration):
        records = self.ledger.get("records", [])
        if self.count != len(records):
            self.by_registration = {}
            for record in records:
                evidence = record.get("evidence") if isinstance(record, dict) else None
                aircraft = evidence.get("aircraft") if isinstance(evidence, dict) else None
                listed = aircraft.get("registration") if isinstance(aircraft, dict) else None
                if isinstance(listed, str):
                    self.by_registration.setdefault(listed, []).append(record)
            self.count = len(records)
        return self.by_registration.get(registration, ())


def related_stories(ledger, packet, key, index=None):
    """The live stories of the packet's registration observed by its start and at most
    story_facts.REPEAT_DAYS before it, newest first, as
    [{"date", "kind", "path", "title", "observed_start"}].

    The story's own record (episode `key`) is never among them. A subjectless story's
    aircraft are examples, so it has none and is none. story_facts.build reads the list for
    repeat.previous; editorial() keeps the newest public.KEPT_RELATED, and each export
    lists the newest public.MAX_RELATED of those it publishes. `index`, a RelatedIndex of
    the same ledger, saves the scan of every record when a pass asks for many stories.
    """
    registration = (packet.get("aircraft") or {}).get("registration")
    start = _quiet(lambda: public.instant(packet["observation"]["start_utc"]))
    if packet.get("kind") in public.SUBJECTLESS_KINDS or not registration or start is None:
        return []
    found = []
    records = index.candidates(registration) if index is not None else ledger.get("records", [])
    for record in records:
        evidence = record.get("evidence") or {}
        # The cheap registration test first: without an index, every record is scanned.
        if (
            not isinstance(evidence, dict)
            or (evidence.get("aircraft") or {}).get("registration") != registration
        ):
            continue
        editorial_ = record.get("editorial") or {}
        if (
            record.get("episode_key") == key
            or evidence.get("kind") in public.SUBJECTLESS_KINDS
            or _quiet(_removed, record, default=True)
            or (record.get("automation") or {}).get("suspended")
        ):
            continue
        began = observation_start(record)
        path = _quiet(public._article_path, editorial_.get("article_path"))
        title = _quiet(public._text, editorial_.get("title"), 160)
        observed = _quiet(public.utc, (evidence.get("observation") or {}).get("start_utc"))
        if (
            began is None
            or path is None
            or title is None
            or observed is None
            or began > start
            or (start.date() - began.date()).days > story_facts.REPEAT_DAYS
        ):
            continue
        found.append(
            {
                "date": began.date().isoformat(),
                "kind": evidence.get("kind"),
                "path": path,
                "title": title,
                "observed_start": observed,
                "at": began.timestamp(),
            }
        )
    found.sort(key=lambda item: (item["at"], item["path"]), reverse=True)
    return [{key_: value for key_, value in item.items() if key_ != "at"} for item in found]


def _page_related(related):
    """The newest related stories as a record keeps them, or None for none."""
    listed = [
        {key: item[key] for key in ("path", "title", "observed_start")}
        for item in (related or ())[: public.KEPT_RELATED]
    ]
    return listed or None


def _with_related(metadata, related):
    """`metadata` with its related list set from related_stories() output, or dropped."""
    metadata = {key: value for key, value in metadata.items() if key != "related"}
    if listed := _page_related(related):
        metadata["related"] = listed
    return metadata


def editorial_inputs(
    ledger,
    packet,
    key,
    *,
    points=None,
    inputs=NO_INPUTS,
    recent_until=None,
    facts=None,
    related=None,
    related_index=None,
):
    """Keyword arguments for editorial(): earlier stories, typed facts and slugs in use.

    stage() and the bootstrap both call this, so every story is written from the
    same inputs. `ledger` holds the stories published or planned so far (the
    bootstrap passes records without automation); `points` are the aircraft
    positions the media renderer draws; `inputs` are the pass's StoryInputs; `facts`,
    when a caller has built them from the same arguments, are used as given. `recent`
    holds the stories observed by this one's start, or by `recent_until` (an aware
    datetime) when a caller, such as the AI writer, compares against later stories too.
    `related`, when a caller has already listed them (related_stories), is used as given;
    `related_index` (RelatedIndex) serves the lookup otherwise.
    """
    others = [record for record in ledger.get("records", []) if record.get("episode_key") != key]
    if related is None:
        related = related_stories(ledger, packet, key, related_index)
    if facts is None:
        facts = _story_facts(packet, points, inputs, related)
    # Withdrawn records keep their Ghost drafts, so their slugs stay taken.
    taken = frozenset(slug for record in others if (slug := _quiet(_slug, record)))
    until = recent_until or public.instant(packet["observation"]["start_utc"])
    return {
        "recent": recent_stories(others, until, inputs.used),
        "facts": facts,
        "taken": taken,
        "related": related,
    }


def editorial(packet, key, *, recent=None, facts=None, taken=None, exceptional=None, related=None):
    """Deterministic story text, routing and a stable story URL; publication never waits for AI.

    Callers pass editorial_inputs(); without them the story has no place
    or type background. The URL comes from the facts and never from the prose.
    `exceptional` False marks exception-grade evidence the publisher treats as
    ordinary, so the public record does not present it as an exception. `related`
    (related_stories) gives the page its newest other stories of the registration.

    Raises editorial.StoryUnavailable, a ValueError, when not even the engine's
    plain record passes the story checks; the record is then not written, and
    stage() tries again on its next pass.
    """
    if facts is None:
        facts = story_facts.build(packet, public._projection(packet, public.PLACEHOLDER_EDITORIAL))
    story, report = engine.render_with_report(facts, key, recent=recent)
    if not report.publishable:
        raise engine.StoryUnavailable("No story passes the story checks: " + "; ".join(report.issues[:8]))
    story = public_prose.validate_story_fields(story, facts)
    slug = story_urls.slug(facts, packet["observation"]["start_utc"], taken=frozenset(taken or ()))
    metadata = {
        "title": story["headline"],
        "summary": story["dek"],
        "article_path": story_urls.article_path(slug),
        "synthetic": False,
        "story": story,
        "context": engine.context(facts),
    }
    if _ordinary(packet, exceptional):
        metadata["priority"] = "ordinary"
    return _with_related(metadata, related)


def written(
    ledger,
    packet,
    key,
    *,
    points=None,
    inputs=NO_INPUTS,
    exceptional=None,
    facts=None,
    related=None,
    related_index=None,
):
    """The story metadata for a packet, written from editorial_inputs(), and its facts.

    Every production path writes stories through here, so none can leave out the
    earlier stories, place, type profiles, or entry organizations a story is written from.
    """
    parts = editorial_inputs(
        ledger,
        packet,
        key,
        points=points,
        inputs=inputs,
        facts=facts,
        related=related,
        related_index=related_index,
    )
    return editorial(packet, key, exceptional=exceptional, **parts), parts["facts"]


_ABSENT = object()


def _listed_value(values, key):
    """What a story lists under `key`: a fact's value, or the attribute of the fact it derives from,
    such as track.place.short from track.place (editorial.DERIVED_FACTS)."""
    parent = engine.DERIVED_FACTS.get(key)
    if parent is None:
        return values[key].get("value", _ABSENT) if key in values else _ABSENT
    return values[parent].get(key[len(parent) + 1 :], _ABSENT) if parent in values else _ABSENT


def _cites_current(story, facts):
    """Whether every fact a saved story lists still has the value it was written with."""
    values = story_facts.fact_map(facts)
    listed = story.get("facts")
    return isinstance(listed, dict) and all(
        _listed_value(values, key) == value for key, value in listed.items()
    )


def _holds(story, facts):
    """Whether a saved story still passes the story checks and every fact it lists is unchanged.
    It is held to the checks for published text (story_issues with new=False), so a check added
    for new stories never replaces one already published."""
    return _cites_current(story, facts) and not public_prose.story_issues(story, facts, new=False)


def _carried(previous, fresh, facts):
    """A new revision's editorial: `fresh`, with the URL, Ghost post and prose that still hold.

    `previous` is the record's current editorial. AI prose stays while it passes the
    story checks against the new facts and lists none that changed, so the writer
    never pays for it again; other prose is rewritten, keeping the title tag while
    that still passes.
    """
    # The story URL is fixed when a record is first staged.
    metadata = {**fresh, "article_path": previous["article_path"]}
    if previous.get("ghost_id"):
        metadata["ghost_id"] = previous["ghost_id"]
    story, old = fresh.get("story"), previous.get("story")
    if isinstance(story, dict) and isinstance(old, dict):
        if old.get("source") == "ai" and _holds(old, facts):
            story = old
        elif isinstance(old.get("seo_title"), str) and _holds(
            {**story, "seo_title": old["seo_title"]}, facts
        ):
            story = {**story, "seo_title": old["seo_title"]}
        metadata.update(title=story["headline"], summary=story["dek"], story=story)
    return metadata


def _ordinary(packet, exceptional):
    """Whether exception-grade evidence is published as an ordinary story."""
    return exceptional is False and bool(
        curation.priority_evidence(
            packet["kind"], packet["observation"].get("metric_value"), packet.get("maneuver") or {}
        )
    )


def _selection_priority(existing, candidate):
    """The selection's exceptional decision for editorial(); None keeps the current one.

    A story already published with exceptional standing keeps its public proof
    through a later selection-time demotion, as the publisher keeps its priority.
    A story the publisher demoted at release, or released as ordinary, without its
    proof stays ordinary, even while its Ghost write is still failing; otherwise
    each pass would restore the proof and the publisher remove it again.
    """
    automatic = (existing or {}).get("automation") or {}
    if (
        existing
        and (automatic.get("published_exceptional") is False or automatic.get("demoted_at_release"))
        and existing["editorial"].get("priority") == "ordinary"
    ):
        return False
    if "exceptional" in candidate:
        if not candidate["exceptional"] and existing and automatic.get("published_exceptional") is True:
            return None
        return bool(candidate["exceptional"])
    if existing and existing["editorial"].get("priority") == "ordinary":
        return False
    return None


def collect(database, now, *, since=0, ledger=None):
    """Choose articles from the publisher inbox; never open a receiver journal."""
    from .ingest import read_state

    return collect_state(read_state(database), now, since=since, ledger=ledger)


def collect_state(state, now, *, since=0, ledger=None):
    """Apply the preserved site policy to a complete imported evidence view."""
    from .feed_adapter import collection

    all_groups, novelty, sources, bindings, norms = collection(state, ledger, at=public.epoch_utc(now))
    groups, keys = [], set()
    for group in all_groups:
        if not since <= group["last_seen"] <= now - selection.QUIET_SECONDS:
            continue
        # A malformed or duplicate encounter is skipped, never the whole run.
        try:
            key = curation.episode_key(group)
            if key in keys or number(group["first_seen"]) is None:
                continue
            qualified = {
                **group,
                "events": [event for event in group["events"] if _eligible(event)],
                "balloon": any(event["kind"] == "stratospheric_balloon" for event in group["events"]),
            }
        except ERRORS:
            continue
        keys.add(key)
        groups.append((key, group, qualified))
    # Each finding in the journal with its encounter.
    current_sources = {
        identity: (event, group)
        for group in all_groups
        for event in group["events"]
        for identity in (source_identity(event), binding_identity(bindings[event["id"]]))
    }
    descents = curation.descent_starts(all_groups)
    selected = curation.choose(
        [qualified for _, _, qualified in groups],
        audit=False,
        history=history(ledger, {identity: group for identity, (_, group) in current_sources.items()})
        if ledger
        else (),
        novelty=lambda day: novelty.get(day),
        descents=descents,
    )["selected"]
    by_key = {key: group for key, group, _ in groups}

    def norm(raw):
        """The type's usual ground speed in these records, as the engine exported it for the
        finding (context.type_norms); None for another kind or a feed without the norm."""
        if raw.get("kind") not in curation.NORM_KINDS:
            return None
        return deepcopy(norms.get(raw["id"]))

    candidates, stories_selected, groups_selected = [], set(), set()
    for row in sorted(selected, key=lambda row: (-row["exceptional"], -row["first_seen"], row["rank"])):
        group = by_key[row["episode_key"]]
        chosen = next(event for event in group["events"] if event["id"] == row["finding_id"])
        try:
            raw = deepcopy(sources[row["finding_id"]])
        except ERRORS:
            continue
        stories_selected.add(row["story_key"])
        groups_selected.add(row["episode_key"])
        candidates.append(
            {
                "key": row["story_key"],
                "source": raw,
                "interval": (group["first_seen"], group["last_seen"]),
                "exceptional": row["exceptional"],
                "exception": curation.selected_exception(chosen, row, descents),
                "rank": row["rank"],
                "routine_type": row["routine_type"],
                "rarity": row.get("rarity"),
                "type_norm": norm(raw),
            }
        )
    # A saved pending packet is not permission to publish after its live
    # finding is rejected, reranked or still receiving more observations.
    for record in (ledger or {}).get("records", []):
        automatic = record.get("automation", {})
        source = automatic.get("source")
        if (
            not source
            or record["state"] == "withdrawn"
            or (automatic.get("source_kind") == "frozen" and not binding_identity(automatic))
        ):
            continue
        retained = current_sources.get(source_association(source, automatic))
        if not retained:
            if not automatic.get("released_at"):
                block(record, "source_unavailable")
            continue
        compact, group = retained
        if group["last_seen"] > now - selection.QUIET_SECONDS:
            if not automatic.get("released_at"):
                block(record, "encounter_active")
            continue
        if curation.episode_key(group) in groups_selected or record["episode_key"] in stories_selected:
            continue
        if not automatic.get("released_at"):
            block(record, "not_selected")
            continue
        try:
            raw = deepcopy(sources[compact["id"]])
        except ERRORS:
            continue
        candidate = {
            "key": record["episode_key"],
            "source": raw,
            "interval": (group["first_seen"], group["last_seen"]),
        }
        if "type_norm" not in automatic:
            # A record staged before norms existed takes the norm once (_staged_norm).
            candidate["type_norm"] = norm(raw)
        candidates.append(candidate)
    for candidate in candidates:
        candidate.update(bindings[candidate["source"]["id"]])
    return candidates


def _entity_link(packet):
    """(entry link, association, list category, neutral category) of a watchlist packet whose
    link may be described, from the entry's public block as story_facts reads it; else None."""
    if not isinstance(packet, dict) or packet.get("kind") != "watchlist_match":
        return None
    block = public._watchlist(packet, strict=False)
    if not block or not block.get("link"):
        return None
    fields = (block["association"], block.get("list_category"), block["category"])
    return (block["link"], *fields) if watchlists.describable(*fields) else None


def entity_links(candidates, ledger=None):
    """The entry links of the watchlist candidates, then of the ledger's watchlist records that
    are not withdrawn, in the form entity_context.resolve() takes. resolve() looks up only those
    with no current answer, each key once, within one limit, so a record staged before its link
    was answered gets the fact, and refresh_prose() offers it on the next rewrite."""

    def packets():
        for candidate in candidates:
            source = candidate.get("source") or {}
            if source.get("kind") != "watchlist_match":
                continue
            try:
                packet = evidence(source, candidate.get("interval"))
            except ERRORS:
                continue
            yield packet
        for record in (ledger or {}).get("records", ()):
            if record.get("state") != "withdrawn":
                yield record.get("evidence")

    links = []
    for packet in packets():
        try:
            link = _entity_link(packet)
        except ERRORS:
            continue
        if link:
            links.append(link)
    return links


def _sources(record):
    """Every finding a record has carried, in any revision."""
    return record_associations(record)


def _staged_norm(existing, candidate):
    """The norm a staged packet carries. A record keeps the norm it was first staged with, None
    included, so a drifting or newly thick baseline never revises a released story; a record
    staged before norms existed takes the candidate's once."""
    automatic = (existing or {}).get("automation") or {}
    return automatic["type_norm"] if "type_norm" in automatic else candidate.get("type_norm")


def _attach_fixed(packet, record, candidate):
    """Put the norm a record keeps (_staged_norm) on its packet, and return it.

    Only a packet of a norm kind carries one: when an episode's finding changes to another
    kind, the saved block is dropped from the packet and from automation.type_norm, so a
    later change back takes a norm afresh, once."""
    if packet.get("kind") not in curation.NORM_KINDS:
        packet.pop("type_norm", None)
        ((record or {}).get("automation") or {}).pop("type_norm", None)
        return None
    norm = _staged_norm(record, candidate)
    if norm:
        packet["type_norm"] = norm
    return norm


def _settle(record, source, candidate, norm, **automation):
    """A staged record's automation block after a pass: its finding, the norm a norm kind keeps
    (None included), and the selection decisions; any block is lifted."""
    automatic = record.setdefault("automation", {})
    if automatic.get("source") != source:
        # A stored decision belongs to the finding it was made on. _decide() stores the new
        # one, unless the story was removed; a refreshed finding without one keeps none.
        automatic.pop("exception", None)
    automatic.update(source=deepcopy(source), **automation)
    if source.get("kind") in curation.NORM_KINDS:
        automatic["type_norm"] = norm
    automatic.pop("blocked", None)
    _decide(record, candidate)


def _decide(record, candidate):
    # Selection-time decisions that publication cannot recompute without the journal.
    # A removed story keeps the decisions it was published with.
    if _removed(record):
        return
    # Rarity is the type's recent coverage-day count, which weekly ranking reuses.
    for field in (
        "exceptional",
        "exception",
        "rank",
        "routine_type",
        "rarity",
        "engine_event_id",
        "engine_finding_id",
        "engine_revision",
        "engine_stream_id",
        "media_parts",
    ):
        if field in candidate:
            record["automation"][field] = deepcopy(candidate[field])
    if "media_parts" in candidate:
        record["automation"]["media_evidence_version"] = public._projection(
            record["evidence"], record["editorial"], strict=False
        )["evidence_version"]


class _Index:
    """Records by episode key and by the finding they carry, kept current through stage()."""

    def __init__(self, records):
        self.by_key, self.by_source = {}, {}
        for record in records:
            self.add(record)

    def add(self, record):
        self.by_key[record["episode_key"]] = record
        for identity in _sources(record):
            self.by_source.setdefault(identity, record)


def stage(ledger, candidates, *, at, inputs=NO_INPUTS):
    """Recheck each finding; persist rejected decisions without stopping other events.

    New records get their story text and URL from editorial(). An existing record
    keeps its URL and Ghost post; while its evidence is unchanged nothing is
    rendered, and after a change it keeps its AI prose, or else its title tag,
    while they still pass the story checks against the new facts. `inputs` holds the
    sourced type profiles stories may cite and the cached Wikidata answers for
    watchlist entry links (StoryInputs). A story that cannot be written
    now is listed under `editorial_failures`, and the next pass tries again.
    """
    rejected, changed, failures = 0, 0, []
    index = _Index(ledger["records"])
    related_index = RelatedIndex(ledger)
    for candidate in candidates:
        source, key = candidate["source"], candidate["key"]
        identity = source_association(source, candidate)
        existing = index.by_key.get(key)
        if existing is None and (existing := index.by_source.get(identity)):
            key = existing["episode_key"]
        sibling = (
            index.by_key.get(existing["episode_key"] + ":exceptional")
            if existing and _removed(existing)
            else None
        )
        if sibling and identity in _sources(sibling):
            # Later decisions about the replacement's finding go to the replacement.
            existing, key = sibling, sibling["episode_key"]
        elif (
            existing
            and _removed(existing)
            and candidate.get("exceptional")
            and identity not in _sources(existing)
            and isinstance(receipt := existing.get("automation", {}).get("published_event"), dict)
            and not _published_exception(existing, receipt)
        ):
            # A later exceptional finding is a new story, not a reversal of the owner's removal.
            key = existing["episode_key"] + ":exceptional"
            existing = index.by_key.get(key)
        if existing and existing["state"] == "withdrawn":
            continue
        # A record that no longer validates waits for a fix; it blocks no other write.
        if existing and not record_valid(existing):
            rejected += 1
            continue
        try:
            if not eligible(source):
                if existing:
                    block(existing, "ineligible_source")
                rejected += 1
                continue
            interval = candidate["interval"]
            if existing:
                previous = existing["evidence"]["observation"]
                interval = (
                    min(interval[0], public._instant(previous["start_utc"]).timestamp()),
                    max(interval[1], public._instant(previous["end_utc"]).timestamp()),
                )
            packet = evidence(source, interval)
            norm = _attach_fixed(packet, existing, candidate)
            exceptional = _selection_priority(existing, candidate)
            if existing and _unchanged(existing, packet, exceptional):
                # Preserve the existing researched story, URL and wording; nothing is rendered.
                _settle(existing, source, candidate, norm)
                continue
            # Evidence the rules refuse blocks the record below, whatever its story.
            plain = dict(public.PLACEHOLDER_EDITORIAL)
            if _ordinary(packet, exceptional):
                plain["priority"] = "ordinary"
            check_source(source, public._projection(packet, plain))
            try:
                metadata, facts = written(
                    ledger,
                    packet,
                    key,
                    points=_points(source),
                    inputs=inputs,
                    exceptional=exceptional,
                    related_index=related_index,
                )
                if existing:
                    metadata = _carried(existing["editorial"], metadata, facts)
                public._projection(packet, metadata)
            except (*ERRORS, OSError) as error:
                # A story that cannot be written now is not invalid evidence: a live
                # article stays as it is, and the next pass tries again.
                rejected += 1
                failures.append({"key": key, "kind": source.get("kind"), "error": type(error).__name__})
                continue
            event_id = public.put_event(ledger, key, packet, metadata, at=at)
            row = existing or public.find_record(ledger, event_id)
            _settle(row, source, candidate, norm, policy_version=POLICY_VERSION)
            index.add(row)
            changed += 1
        except ERRORS:
            if existing:
                block(existing, "invalid_evidence")
            rejected += 1
    result = {"changed": changed, "rejected": rejected}
    if failures:
        result["editorial_failures"] = failures
    return result


def _same_packet(record, packet):
    """Whether a regenerated packet is the record's saved one, block for block.

    The projected evidence version covers the public card. A story is written
    from the whole packet, which also carries the database build year, a season
    comparison, the replay track and the readings around the finding; a change
    there needs a revision as much as one the card shows.
    """
    return public._digest(packet) == public._digest(record["evidence"])


def _unchanged(existing, packet, exceptional):
    """Whether new evidence projects to the existing record's evidence version and
    regenerates the packet its story was written from."""
    metadata = {key: value for key, value in existing["editorial"].items() if key != "priority"}
    if _ordinary(packet, exceptional):
        metadata["priority"] = "ordinary"
    try:
        current = public._projection(existing["evidence"], existing["editorial"])
        fresh = public._projection(packet, metadata)
    except ERRORS:
        # A stale dossier or story must not prevent a valid replacement.
        return False
    return fresh["evidence_version"] == current["evidence_version"] and _same_packet(existing, packet)


def refresh_prose(ledger, at, *, inputs=NO_INPUTS):
    """Refresh stories after facts or prose requirements change; returns revisions.

    A current-policy story stays when its facts digest is unchanged. When facts
    change, its cited values and the story checks must still hold, including any
    newly required qualification. Older AI prose also stays while it passes
    those checks; other outdated stories receive deterministic replacements.
    Each replacement preserves the URL and article identity and keeps a title
    tag that still passes. The next publication pass releases the revision.
    """
    revised = 0
    related_index = RelatedIndex(ledger)
    for record in list(ledger["records"]):
        story = record["editorial"].get("story") if isinstance(record.get("editorial"), dict) else None
        if record.get("state") == "withdrawn" or not isinstance(story, dict) or not record_valid(record):
            continue
        try:
            packet, key = record["evidence"], record["episode_key"]
            points = record_points(record)
            related = related_stories(ledger, packet, key, related_index)
            facts = _story_facts(packet, points, inputs, related)
            if story.get("version", 0) >= engine.PROSE_POLICY:
                # New facts can require a qualification even when cited values stay the same.
                if facts["digest"] == story.get("facts_digest") or _holds(story, facts):
                    continue
            elif story.get("source") == "ai" and _holds(story, facts):
                continue
            fresh, facts = written(
                ledger, packet, key, points=points, inputs=inputs, facts=facts, related=related
            )
            replacement = _carried(record["editorial"], fresh, facts)["story"]
            metadata = {
                **{name: value for name, value in record["editorial"].items() if name != "related"},
                "title": replacement["headline"],
                "summary": replacement["dek"],
                "story": replacement,
                "context": fresh["context"],
                **({"related": fresh["related"]} if "related" in fresh else {}),
            }
            public.put_event(ledger, key, packet, metadata, at=at)
            revised += 1
        except ERRORS:
            continue
    return revised


def reproject(ledger, at, *, inputs=NO_INPUTS):
    """Re-derive saved packets after a change in the evidence code; returns a report.

    publish() regenerates each record's packet from its saved finding on every
    pass and drafts a live article whose evidence no longer matches. stage()
    restages live records while the collector keeps their findings; frozen
    records, and live ones whose findings are gone, never come back to it. For
    every other record with a saved finding whose regenerated evidence differs,
    this records a revision with the regenerated packet. The URL and Ghost post
    stay; the prose and title tag stay while they pass the story checks against
    the new facts; and an approved record is approved again.

    A weekly dossier or map excerpt is bound to an evidence version. It moves to
    the new one when it was made for the record's previous evidence and the kind,
    interval and identity it describes are unchanged. A change in card wording
    alone leaves the packet as it was but changes the version, and those bindings
    move with it; a dossier's move is a revision, a map's is not. A record that
    cannot move completely stays as it was and is reported with a reason. The
    report holds counts, record IDs and reason codes only.
    """
    report = {
        "checked": 0,
        "unchanged": 0,
        "reprojected": 0,
        "prose_rewritten": 0,
        "research_rebound": 0,
        "media_rebound": 0,
        "approved": 0,
        "unresolved": [],
    }
    for record in ledger["records"]:
        automatic = record.get("automation") or {}
        source = automatic.get("source")
        if record.get("state") == "withdrawn" or not isinstance(source, dict):
            continue
        report["checked"] += 1
        try:
            change = _reprojection(ledger, record, source, inputs)
            if change is None:
                report["unchanged"] += 1
                continue
            approved = change["editorial"] is not None and record["state"] == "approved"
            if change["editorial"] is not None:
                public.put_event(ledger, record["episode_key"], change["packet"], change["editorial"], at=at)
                if approved:
                    public.set_review_state(ledger, record["id"], "approved", at=at)
        except (*ERRORS, OSError) as error:
            report["unresolved"].append({"id": record.get("id"), "reason": public._reason(error)})
            continue
        version = change["evidence_version"]
        if change["media"]:
            automatic["media_evidence_version"] = version
        automatic.setdefault("reprojections", []).append(
            {
                "at": at,
                "evidence_version": version,
                "revision": change["editorial"] is not None,
                **{key: change[key] for key in ("rewritten", "research", "media")},
            }
        )
        report["reprojected"] += change["editorial"] is not None
        report["prose_rewritten"] += change["rewritten"]
        report["research_rebound"] += change["research"]
        report["media_rebound"] += change["media"]
        report["approved"] += approved
    return report


def _reprojection(ledger, record, source, inputs=NO_INPUTS):
    """What reproject changes for one record, or None when nothing would.

    Returns {"packet", "editorial" (None when no revision is needed),
    "evidence_version", "rewritten", "research", "media"}.
    """
    observation = record["evidence"]["observation"]
    interval = tuple(public._instant(observation[key]).timestamp() for key in ("start_utc", "end_utc"))
    packet = evidence(source, interval)
    _attach_fixed(packet, record, {})
    metadata = deepcopy(record["editorial"])
    research = metadata.pop("weekly_spotlight", None)
    # As publish() compares it: the regenerated packet with the record's own editorial.
    after = public._projection(packet, metadata)
    before = _project(record)
    target = after["evidence_version"]
    # A packet change the card does not show still changes what the story is written from.
    changed = before is None or before["evidence_version"] != target or not _same_packet(record, packet)

    def moves(version):
        """Whether a binding to `version` moves to the target version."""
        if version == target:
            return False
        if not changed:
            # The packet regenerates unchanged, so only the card wording moved its version.
            return True
        return (
            before is not None
            and version == before["evidence_version"]
            and public._binding_identity(before) == public._binding_identity(after)
        )

    rebind = research is not None and moves(
        research.get("evidence_version") if isinstance(research, dict) else None
    )
    if (
        research is not None
        and not rebind
        and (not isinstance(research, dict) or research.get("evidence_version") != target)
    ):
        raise ValueError("weekly_binding_changed")
    media = (record.get("automation") or {}).get("media_evidence_version")
    result = {"packet": packet, "editorial": None, "evidence_version": target, "rewritten": False}
    result.update(research=rebind, media=media is not None and moves(media))
    if not changed and not rebind:
        return result if result["media"] else None
    if research is not None:
        metadata["weekly_spotlight"] = {**research, "evidence_version": target} if rebind else research
    story = metadata.get("story")
    if changed and isinstance(story, dict):
        # The facts decide first, so a story that still holds is never rendered again.
        editorial_kwargs = editorial_inputs(
            ledger,
            packet,
            record["episode_key"],
            points=record_points(record),
            inputs=inputs,
        )
        if not _holds(story, editorial_kwargs["facts"]):
            fresh = editorial(packet, record["episode_key"], **editorial_kwargs)
            story = _carried(metadata, fresh, editorial_kwargs["facts"])["story"]
            metadata.update(title=story["headline"], summary=story["dek"], story=story)
            result["rewritten"] = True
        metadata["context"] = engine.context(editorial_kwargs["facts"])
        metadata = _with_related(metadata, editorial_kwargs["related"])
    # A wording change keeps the saved packet; only the projection of it moved.
    result.update(packet=packet if changed else record["evidence"], editorial=metadata)
    public._projection(result["packet"], metadata)
    return result


def _owned(post, record):
    return post.get("slug") == _slug(record) and has_owner_tag(post, record["id"])


def _live(post):
    return bool(post and post.get("status") == "published" and post.get("visibility") == "public")


def remember_published(ledger, snapshot):
    """Retain exact prior rendering facts across changes to labels and policy.

    A receipt grants no eligibility. It is used only when the owned Ghost card
    still matches it exactly, to distinguish generated text from owner edits.
    """
    if snapshot.get("snapshot_id") != public._digest(
        {k: v for k, v in snapshot.items() if k != "snapshot_id"}
    ):
        raise ValueError("Invalid saved publication snapshot")
    records = {record["id"]: record for record in ledger["records"]}
    for event in snapshot.get("events", []):
        record = records.get(event.get("id"))
        if (
            record
            and record.get("automation")
            and "published_event" not in record["automation"]
            and event.get("article_path") == record["editorial"]["article_path"]
            and event.get("evidence_version") == public.evidence_version(event)
        ):
            record["automation"]["published_event"] = deepcopy(event)


def _content_changed(previous, event):
    """Whether readers see new prose or evidence; an unreadable receipt counts as a change."""
    if not isinstance(previous, dict):
        return True
    return _quiet(lambda: public.content_version(previous) != public.content_version(event), default=True)


def _current_exception(event):
    return curation.exceptional(
        event["kind"], event.get("priority_evidence", {}).get("value"), event.get("priority_evidence", {})
    )


def _published_exception(record, event):
    """Whether the live article took exceptional priority when it was released."""
    return (
        _current_exception(event)
        and (record.get("automation") or {}).get("published_exceptional") is not False
    )


def post_evidence(record, post):
    """Resolve a committed correction even if its API response was lost.

    The digest stored at publication recognizes an unedited card without
    rendering it again, so a later renderer change never looks like an edit.
    """
    cards = [block for kind, block in PublishedBlocks(post.get("html", "")).blocks if kind == "evidence"]
    automatic = record.get("automation") or {}
    receipt = automatic.get("published_event")
    digest = (automatic.get("published_post") or {}).get("card_sha256")
    if (
        receipt
        and receipt.get("id") == record["id"]
        and receipt.get("article_path") == record["editorial"]["article_path"]
        and len(cards) == 1
        and (
            (digest is not None and publication.card_digest(cards[0]) == digest)
            or publication.observation_card_matches(receipt, cards[0])
        )
    ):
        return receipt
    for revision in (record, *reversed(record.get("history", []))):
        try:
            event = public._projection(revision["evidence"], revision["editorial"], strict=False)
        except ERRORS:
            # A retired revision cannot match the card; another one still can.
            continue
        if len(cards) == 1 and publication.observation_card_matches(
            {**event, "id": record["id"], "revision": revision["revision"]}, cards[0]
        ):
            return event
    # Unknown edited evidence cannot reserve an exceptional slot.
    event = public._projection(record["evidence"], record["editorial"], strict=False)
    event.pop("priority_evidence", None)
    return event


def _addresses(record, event):
    return frozenset(_quiet(lambda: _facts(record, event)["addresses"], default=[]))


def _live_slot(record, event):
    """The slot a live article holds, from the evidence it shows."""
    return curation.LiveSlot(
        event["observed_start"][:10], _addresses(record, event), _published_exception(record, event)
    )


def _claim(record, event, live, granted):
    """A record's claim on a publication slot; `live` is the slot its live article holds, and
    `granted` whether that slot is an exceptional one (only then is the sticky standing read)."""
    if event is None:
        return curation.Claim(record["id"], None, live)
    rank = record["automation"].get("rank")
    return curation.Claim(
        record["id"],
        curation.Slot(event["observed_start"][:10], _addresses(record, event)),
        live,
        standing=exception_standing(record, event),
        sticky_standing=granted and exception_standing(record, event, sticky=True),
        rank=rank if type(rank) is int else None,
        start_ts=public._instant(event["observed_start"]).timestamp(),
    )


def _stranded(record, by_slug):
    """A live article whose record can no longer be exported."""
    try:
        post = by_slug.get(_slug(record))
        return _live(post) and (_owned(post, record) or record["editorial"].get("ghost_id") == post.get("id"))
    except ERRORS:
        # An unreadable record may still have a live article; keep the previous release.
        return True


def _sound(record):
    """Whether one record can be exported on its own."""
    try:
        public.validate_record(record)
        public._approved_revision(record)
    except ERRORS:
        return False
    return True


# Ghost answers that change nothing and concern only the post sent: invalid, gone,
# too large or unprocessable. Authentication, conflicts and rate limits stop the pass.
GHOST_REFUSED = frozenset({400, 404, 413, 415, 422})


def _ghost_status(error):
    """The HTTP status Ghost answered a failed request with, or None."""
    status = getattr(error, "status", None)
    return status if isinstance(status, int) else None


def _same_tag(tag, other):
    """Ghost matches a tag by slug when both have one, and by name otherwise."""
    if tag.get("slug") and other.get("slug"):
        return tag["slug"] == other["slug"]
    return tag["name"].casefold() == other["name"].casefold()


def publish(ledger, client, *, persist, at=None, validate_post=None):
    """Publish automatically, preserving prior releases and recovering POST retries.

    Posts are indexed by slug and by ownership tag. A slug held by another post
    or page blocks the record without a write, and a record's own post under
    another slug is drafted and never posted twice. Ghost meta fields, the topic
    tag and the prose block follow the story; an owner's edits to them stay.
    """
    at = public.stamp(at)
    posts = client.inventory()
    by_slug = {post["slug"]: post for post in posts}
    by_tag = defaultdict(list)
    taken = None

    def owner_tags(post):
        return [
            tag["name"]
            for tag in post.get("tags") or ()
            if isinstance(tag, dict) and str(tag.get("name", "")).startswith(OWNER_TAG_PREFIX)
        ]

    def remember(saved):
        for slug, post in list(by_slug.items()):
            if post.get("id") == saved.get("id"):
                del by_slug[slug]
        by_slug[saved["slug"]] = saved
        for name in list(by_tag):
            by_tag[name] = [post for post in by_tag[name] if post.get("id") != saved.get("id")]
        for name in owner_tags(saved):
            by_tag[name].append(saved)
        if taken is not None:
            taken.add(saved["slug"])

    for post in posts:
        for name in owner_tags(post):
            by_tag[name].append(post)

    def held(slug):
        """Whether any post or page in Ghost uses the slug; read once, when first needed."""
        nonlocal taken
        if taken is None:
            # Ghost keeps post and page slugs unique across both, in every status.
            taken = set(client.slugs_in_use()) | {post["slug"] for post in posts}
        return slug in taken

    def suspend(record, post, owns=_owned):
        """Draft one of the record's live posts; Ghost must confirm the draft and its owner.

        The caller persists its intent first, so a lost reply is not read as an
        owner's removal on the next pass.
        """
        saved = client.save({"status": "draft"}, post)
        if saved.get("status") != "draft" or not owns(saved, record):
            raise ValueError("Ghost did not confirm automatic suspension")
        remember(saved)

    # A live article whose record no longer projects is suspended below. Several
    # point to a code or schema change, so nothing is written until that is fixed.
    events = {record["id"]: _project(record) for record in ledger["records"]}
    if (
        sum(events[record["id"]] is None and _stranded(record, by_slug) for record in ledger["records"])
        >= UNPROJECTABLE_PAUSE
    ):
        raise ValueError(
            "Several records no longer project; publication pauses and the previous release remains"
        )
    live_records = []
    for record in ledger["records"]:
        post = by_slug.get(_quiet(_slug, record))
        if not _live(post):
            continue
        approved = _quiet(public._approved_revision, record)
        if not _owned(post, record) and not (
            approved and approved["editorial"].get("ghost_id") == post["id"]
        ):
            continue
        try:
            event = post_evidence(record, post)
        except ERRORS:
            # The article still occupies its date; count it as an ordinary story.
            fallback = events[record["id"]] or (record.get("automation") or {}).get("published_event")
            if not isinstance(fallback, dict) or "observed_start" not in fallback:
                continue
            event = {key: value for key, value in fallback.items() if key != "priority_evidence"}
        (record.get("automation") or {}).pop("removed", None)
        live_records.append((event["observed_start"], record["id"], record, event))
    # At most two exceptional stories per address and UTC date hold priority.
    live_slots = {
        record["id"]: _live_slot(record, event)
        for _, _, record, event in sorted(live_records, key=lambda item: item[:2])
    }
    quota = curation.DayQuota(live_slots)
    log = curation.StoryLog(history(ledger, events=events))
    # A slug conflict counts as a failure and stays recorded on its record.
    result = {"published": 0, "unchanged": 0, "deferred": 0, "suspended": 0, "failed": 0, "invalid": 0}

    # Record IDs are unique in a valid ledger.
    claimants = {row["id"]: row for row in ledger["records"] if row.get("automation")}
    # The live-equivalence invariant: a projectable record has a live slot exactly when its
    # post at the story's slug is live and its own (an unowned one stops the record before any
    # decision), so for a projectable record `claim.live` is the only test of "live" below.
    claims = [
        _claim(row, events[key], live_slots.get(key), key in quota.granted) for key, row in claimants.items()
    ]
    for claim in quota.order(claims):
        record = claimants[claim.id]
        automatic = record["automation"]
        if record["editorial"].get("synthetic"):
            continue
        event = events[record["id"]]
        if event is None:
            # No block is saved, so the record recovers once its evidence projects
            # again. Its live article is suspended like any ineligible story.
            current = by_slug.get(_quiet(_slug, record))
            # Ghost decides here: a live post whose evidence cannot be read has no live slot.
            if not (current and _live(current) and _quiet(_owned, current, record)):
                result["invalid"] += 1
                continue
            automatic.update(suspended=True, unprojectable=True)
            persist(ledger)
            try:
                suspend(record, current)
            except GHOST_ERRORS:
                result["failed"] += 1
                break
            quota.release(claim)
            result["suspended"] += 1
            result["invalid"] += 1
            continue
        source = automatic.get("source")
        expected = _slug(record)
        current = by_slug.get(expected)
        if current and not _owned(current, record):
            # Another post holds this story's slug; it is never overwritten.
            automatic["slug_conflict"] = expected
            result["failed"] += 1
            continue
        strays = [post for post in by_tag.get(owner_tag(record["id"]), ()) if post["slug"] != expected]
        if strays:
            # This record's own post under another slug, as after Ghost suffixed a
            # slug that had become taken. It is never posted again. A live one is
            # drafted. Without a post at the story's slug, a draft moves back to
            # that slug once the slug is free.
            if any(_live(post) for post in strays):
                if not current:
                    # Intent first, so the next pass does not read an owner's removal.
                    automatic.update(suspended=True, slug_conflict=expected)
                    persist(ledger)
                try:
                    for post in strays:
                        if _live(post):
                            suspend(record, post, lambda saved, record: has_owner_tag(saved, record["id"]))
                except GHOST_ERRORS:
                    result["failed"] += 1
                    break
                result["suspended"] += 1
                if not current:
                    result["failed"] += 1
                    continue
            elif not current:
                try:
                    blocked = len(strays) > 1 or held(expected)
                except GHOST_ERRORS:
                    result["failed"] += 1
                    break
                if blocked:
                    automatic["slug_conflict"] = expected
                    result["failed"] += 1
                    continue
                current = strays[0]
        live = claim.live is not None
        decision = quota.decide(claim)
        priority = decision.priority
        valid = (
            record["state"] != "withdrawn" and not automatic.get("blocked") and source and _eligible(source)
        )
        if valid:
            try:
                check_source(source, event)
            except (ValueError, TypeError, KeyError):
                valid = False
        if not valid or not decision.admit:
            if live:
                automatic["suspended"] = True
                persist(ledger)
                try:
                    suspend(record, current)
                except GHOST_ERRORS:
                    result["failed"] += 1
                    break
                quota.release(claim)
                result["suspended"] += 1
            result["deferred"] += 1
            continue
        # An owner's unpublish/delete stays in effect. Automatic suspensions
        # can recover when their evidence or quota becomes eligible again.
        if (automatic.get("released_at") and not current) or (
            current and not live and not automatic.get("suspended")
        ):
            automatic["removed"] = True
            result["deferred"] += 1
            continue
        story = None
        if not live:
            story = _quiet(_story, record, event)
            if story is None:
                result["deferred"] += 1
                continue
            if not priority and repeat_reason(log, story):
                result["deferred"] += 1
                continue
        if not current:
            # Ghost would silently suffix a taken slug, so a held slug blocks the POST.
            try:
                conflict = held(expected)
            except GHOST_ERRORS:
                result["failed"] += 1
                break
            if conflict:
                automatic["slug_conflict"] = expected
                result["failed"] += 1
                continue
        write_attempted = committed = False
        try:
            if not priority and event.get("priority_evidence"):
                # Released as ordinary, as when the per-aircraft cap is full, the
                # public record carries no exception proof; stage() keeps it so.
                try:
                    public.put_event(
                        ledger,
                        record["episode_key"],
                        record["evidence"],
                        {**record["editorial"], "priority": "ordinary"},
                        at=at,
                    )
                except ERRORS:
                    # A weekly dossier is bound to the evidence it was researched on, so
                    # such a story keeps its proof rather than failing every pass.
                    pass
                else:
                    automatic["demoted_at_release"] = True
                    event = events[record["id"]] = public._projection(
                        record["evidence"], record["editorial"], strict=False
                    )
                    if story is not None:
                        story = _story(record, event)
            snapshot = public.export_snapshot({**ledger, "records": [record]}, mode="review", as_of=at)
            generated_event = snapshot["events"][0]
            post = publication.draft_request(snapshot, record["id"], prepared_at=at)["request"]["body"][
                "posts"
            ][0]
            post.update(status="published")
            generated = publication.post_fields(generated_event)
            receipt = automatic.get("published_post") or {}
            edits = []
            if current:
                previous_content = post_evidence(record, current)
                post["lexical"] = publication.automatic_article_update(
                    generated_event, current, previous_content, story_digest=receipt.get("story_sha256")
                )
                # The fields generated last time identify an owner's edit exactly.
                last_fields = receipt.get("fields") or publication.post_fields(previous_content)
                for field in publication.GENERATED_FIELDS:
                    if field in current and current[field] != last_fields.get(field):
                        edits.append(field)
                        post[field] = current[field]
                    else:
                        post[field] = generated[field]
                if publication.story_edited(
                    publication.editor_html(current), receipt.get("story_sha256"), previous_content
                ):
                    edits.append("story")
            topic = (generated_event.get("context") or {}).get("topic")
            moved = receipt.get("topic") if receipt.get("topic") != topic else None
            wanted = [*post.get("tags", []), {"name": owner_tag(record["id"])}]
            tags = []
            for tag in (current or {}).get("tags", []):
                # Ghost knows a tag by its slug, so a renamed topic is still one tag.
                match = next((other for other in wanted if _same_tag(tag, other)), None)
                if match is not None:
                    if not any(item is match for item in tags):
                        tags.append(match)
                elif not (
                    tag["name"].startswith("#weekly-")
                    or tag["name"] == "Weekly spotlight"
                    or (moved and tag.get("slug") == moved)
                ):
                    # Other tags stay, in the owner's order.
                    tags.append({key: tag[key] for key in ("name", "slug") if tag.get(key)})
            tags.extend(other for other in wanted if not any(item is other for item in tags))
            # The topic tag leads, so Ghost makes it the primary tag.
            post["tags"] = sorted(tags, key=lambda tag: not topic or tag.get("slug") != topic)
            if validate_post:
                validate_post(post)
            same = (
                live
                and all(
                    current.get(key) == value
                    for key, value in post.items()
                    if key not in {"tags", "published_at", "lexical"}
                )
                and json.loads(current["lexical"]) == json.loads(post["lexical"])
                and len(current["tags"]) == len(post["tags"])
                and all(_same_tag(*pair) for pair in zip(current["tags"], post["tags"], strict=True))
            )
            write_attempted = not same
            saved = current if same else client.save(post, current)
            committed = write_attempted
            if saved.get("slug") != expected:
                # Ghost changed the requested slug, as after a race with another post
                # or page. The post never stays live under an unexpected URL.
                automatic.update(suspended=True, slug_conflict=expected)
                persist(ledger)
                if _live(saved) and has_owner_tag(saved, record["id"]):
                    suspend(record, saved, lambda draft, record: has_owner_tag(draft, record["id"]))
                raise ValueError("Ghost changed the requested slug")
            if not _owned(saved, record) or not _live(saved):
                raise ValueError("Ghost did not confirm the intended publication")
            needs_record = (
                not same
                or record["state"] != "approved"
                or record["editorial"].get("ghost_id") != saved["id"]
                or not automatic.get("released_at")
            )
            if needs_record:
                record["editorial"]["ghost_id"] = public.ghost_post_id(saved["id"])
                # Only this record is validated, so an unrelated broken record cannot block approval.
                public.set_review_state(ledger, record["id"], "approved", at=at)
                automatic.update(
                    policy_version=POLICY_VERSION, released_at=automatic.get("released_at") or at
                )
                automatic.pop("unprojectable", None)
            # A live, confirmed post is not suspended, even when nothing needed writing,
            # so a later unpublication reads as the owner's decision.
            automatic.pop("suspended", None)
            # The last prose or evidence change readers can see; a card refresh is not one.
            if _content_changed(automatic.get("published_event"), generated_event):
                automatic["content_revised_at"] = at
            elif not automatic.get("content_revised_at"):
                automatic["content_revised_at"] = automatic["released_at"]
            automatic["published_event"] = deepcopy(generated_event)
            automatic["published_exceptional"] = bool(priority)
            # The exact generated fields and block digests, compared on later passes
            # instead of rendering the previous version again.
            automatic["published_post"] = {
                "fields": generated,
                **publication.owned_digests(publication.article_html(generated_event, display_review=False)),
                "topic": topic,
            }
            automatic.pop("slug_conflict", None)
            automatic.pop("ghost_refused", None)
            if edits:
                automatic["owner_edits"] = edits
            else:
                automatic.pop("owner_edits", None)
            if needs_record:
                persist(ledger)
            quota.commit(claim, decision)
            if story is not None:
                log.add(story)
            remember(saved)
            result["unchanged" if same else "published"] += 1
        except GHOST_ERRORS as error:
            # A timed-out POST may have succeeded. The next full inventory
            # reconciles ownership before another write or quota check.
            result["failed"] += 1
            # A displaced live story counts conservatively for the rest of this pass.
            quota.hold_conservatively(claim)
            status = _ghost_status(error)
            if status in GHOST_REFUSED and not committed:
                # Ghost refused this post and changed nothing; the others still publish.
                automatic["ghost_refused"] = {"status": status, "at": at}
            elif write_attempted:
                break
    posts = client.inventory()
    stamp = max([at, *(public.utc(post["updated_at"]) for post in posts)], key=public._instant)
    inventory = {"complete": True, "total": len(posts), "as_of_utc": stamp, "posts": posts}
    sound = [record for record in ledger["records"] if _sound(record)]
    persist(ledger)
    by_slug = {post["slug"]: post for post in posts}
    if any(_stranded(record, by_slug) for record in ledger["records"] if record not in sound):
        # Dropping a live article from the site would hide it without a decision.
        raise ValueError("A live article's record can no longer be exported; the previous release remains")
    # A record without a live article leaves the export instead of stopping it.
    snapshot = public.export_snapshot(
        {**ledger, "records": sound}, mode="published", as_of=stamp, inventory=inventory
    )
    return snapshot, result
