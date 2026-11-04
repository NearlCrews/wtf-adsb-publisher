"""Weekly nominations and reviewed research attached to existing public events."""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser

VERSION = 1
MATCH = re.compile(r"\bmatch\b", re.I)
SCOPES = {
    "event_background": "About this kind of event",
    "type_background": "Aircraft type background",
    "airframe_history": "Individual aircraft history",
    "interpretation": "Interpretation",
}


# The weekly pick favors kinds the site seldom covers: a kind with n stories observed in
# the RARITY_DAYS before the week ends has rarity 1 / (1 + n). A kind picked in the
# REPEAT_WEEKS before a week ranks after every other candidate.
RARITY_DAYS = 90
REPEAT_WEEKS = 4
# Researched prose that reaches a page writes no em or en dash and no ampersand.
DASH_OR_AMPERSAND = re.compile("[\u2012-\u2015&]")
# "one" is a pronoun as often as a number ("each one", "one of"), so it is never refused.
ANY_TEXT_NUMBERS = frozenset({1.0})
# One-off explainers: a page written once, when enough stories of its kinds exist, that
# each such story links to. `allocation` is the ICAO allocation State of the stories'
# addresses; `minimum` is how many live stories it waits for.
EXPLAINERS = {
    "explainer:canadian-addresses": {
        "path": "/explainers/canadian-addresses/",
        "title": "When a Canadian address and its listed type disagree",
        "topic": "Aircraft addresses in Canada's allocation whose listed type cannot produce the reported speed or altitude",
        "kinds": ("type_altitude_mismatch", "type_speed_mismatch"),
        "allocation": "CA",
        "allocation_name": "Canada",
        "minimum": 3,
        # The 24 of a 24-bit address, which the page cannot explain without.
        "numbers": (24,),
        # Capitalized words the page may use besides the site's fixed vocabulary and its sources.
        "names": ("Canada", "Canadian", "International", "Civil", "Aviation", "Organization", "State"),
    }
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def week_start(value):
    from .public_events import _instant

    day = _instant(value).date()
    return day - timedelta(days=day.weekday())


def previous_week(as_of):
    end = week_start(as_of)
    return (end - timedelta(days=7)).isoformat(), end.isoformat()


def kind_counts(ledger, end):
    """Live stories (public_events.live_story) of each kind observed in the RARITY_DAYS
    before `end`, a UTC date. A record without a readable start is left out.
    """
    from .public_events import _instant, live_story

    until = _instant(f"{end}T00:00:00Z")
    since = until - timedelta(days=RARITY_DAYS)
    counts = Counter()
    for record in ledger.get("records", []):
        evidence = record.get("evidence") if isinstance(record, dict) else None
        if not isinstance(evidence, dict) or not live_story(record):
            continue
        try:
            began = _instant(evidence["observation"]["start_utc"])
        except (ValueError, TypeError, KeyError):
            continue
        if since <= began < until:
            counts[evidence.get("kind")] += 1
    return counts


def rarity(count):
    return 1 / (1 + count)


def picked_kinds(ledger, start):
    """The kinds of the live weekly picks (public_events.live_story) in the REPEAT_WEEKS
    before the week of `start`."""
    from .public_events import live_story

    since = (datetime.fromisoformat(start) - timedelta(weeks=REPEAT_WEEKS)).date().isoformat()
    kinds = set()
    for record in ledger.get("records", []):
        if not live_story(record):
            continue
        picked = ((record.get("editorial") or {}).get("weekly_spotlight") or {}).get("week_start")
        if isinstance(picked, str) and since <= picked < start:
            kinds.add((record.get("evidence") or {}).get("kind"))
    return kinds


def rank(candidates, *, picked=frozenset()):
    """Weekly candidates by rarity, then interest, then published priority, then the latest
    observation first.

    Each row carries its kind's `rarity` and the curation `score`; evidence, depth, and the
    type's recent coverage settle the remaining ties. A candidate of a `picked` kind
    (picked_kinds) follows every other candidate.
    """
    from .public_events import _instant

    ranked = sorted(
        candidates,
        key=lambda row: (
            -row["rarity"],
            -row["score"]["interest"],
            -row["score"]["exceptional"],
            -_instant(row["event"]["observed_start"]).timestamp(),
            -row["score"]["evidence"],
            -row["score"]["depth"],
            row["recent_picks"],
            row["recent_observations"],
            row["event"]["id"],
        ),
    )
    # A stable sort, so a demoted row keeps its order and is still the pick when nothing
    # else remains, and a failing candidate still rotates to the next one.
    return sorted(ranked, key=lambda row: row["event"]["kind"] in picked)


def explains(key, kind, address):
    """Whether explainer `key` covers a story of `kind` about the aircraft address `address`."""
    from . import reference

    spec = EXPLAINERS[key]
    if kind not in spec["kinds"] or not isinstance(address, str):
        return False
    found = reference.allocation(address)
    return bool(found) and found["country"] == spec["allocation"]


def prose_issues(texts, *, hypotheses=(), causeless=False, numbers=(), names=None):
    """Why researched prose cannot be saved; [] when it can.

    Each sentence of `texts` writes no em or en dash and no ampersand, names a listed
    hypothesis only in a sentence the story checks count as hedged (public_prose.HEDGE),
    and states no number other than `numbers` (and "one"). With `causeless`, as for
    public_prose.CAUSELESS_KINDS, a sentence names no cause: the listed hypotheses aside,
    a cause noun or a verdict on a source is refused, and so is a causal verb outside a
    hedged sentence that offers a hypothesis. With `names`, a set of lowercase words, every
    capitalized word must be among them or in public_prose.FIXED_VOCABULARY; a sentence's
    first word passes when the word after it is lowercase.
    """
    from . import public_prose as prose

    # A reading may be text, such as an altitude of "ground"; only numbers can be quoted as one.
    numbers = [value for value in numbers if prose.finite(value)]
    allowed = {float(abs(value)) for value in numbers} | {float(round(abs(value))) for value in numbers}
    allowed |= ANY_TEXT_NUMBERS
    hypotheses = [item for item in hypotheses if isinstance(item, str) and item]
    listed = [re.compile(r"(?<![\w-])" + re.escape(item) + r"(?![\w-])", re.I) for item in hypotheses]
    vocabulary = None if names is None else set(names) | prose.FIXED_VOCABULARY
    issues = []
    for text in texts:
        for start, end in prose.sentence_spans(text):
            sentence = text[start:end].strip()
            if DASH_OR_AMPERSAND.search(sentence):
                issues.append("Research prose has a dash or an ampersand")
            hedged = bool(prose.HEDGE.search(sentence))
            offered = [pattern for pattern in listed if pattern.search(sentence)]
            # A research sentence offers a hypothesis by its whole phrase (the story checks' rule).
            if prose.unhedged_hypotheses([sentence], hypotheses, whole=True):
                issues.append("A listed hypothesis is stated without a hedge")
            if causeless:
                blanked = sentence
                for pattern in offered:
                    blanked = pattern.sub(" ", blanked)
                if offered and hedged:
                    cause = prose.CAUSE_NOUNS.search(blanked) or (
                        prose.NAMED_SOURCE.search(blanked) and prose.SOURCE_VERDICT.search(blanked)
                    )
                    if cause:
                        issues.append(f"Unlisted cause ({cause.group().lower()})")
                elif cause := prose.causeless_issue(blanked):
                    issues.append(cause.rstrip(": "))
            for value in _text_numbers(sentence):
                if value not in allowed:
                    issues.append(f"Unsupported number ({value:g})")
            if vocabulary is not None:
                issues.extend(_name_issues(sentence, vocabulary))
    return list(dict.fromkeys(issues))


def _text_numbers(sentence):
    """The numbers a sentence states, in digits or in words."""
    from . import public_prose as prose

    for match in prose.DIGITS.finditer(sentence):
        yield abs(float(match["n"].replace(",", "")))
    for match in prose.SPELLED.finditer(sentence):
        if match["w"].lower() not in {"a", "an"}:
            yield float(prose.spelled_value(match["w"]))


def _name_issues(sentence, vocabulary):
    """Capitalized words outside `vocabulary`: a type, registration, operator, person, or place.
    The story checks' rule (public_prose.unlisted_names), with words that carry a digit checked too."""
    from . import public_prose as prose

    return [f"Unlisted name ({token})" for token in prose.unlisted_names(sentence, vocabulary, numbered=True)]


def nominate(report, as_of):
    """Use daily-selected episodes; uncertain leads remain a separate private list."""
    from . import curation
    from .public_events import utc

    if report.get("version") != curation.VERSION:
        raise ValueError("Rerun daily curation before nominating under the current selection policy")
    as_of = utc(as_of)
    start, end = previous_week(as_of)

    def in_week(row):
        day = datetime.fromtimestamp(row["first_seen"], timezone.utc).date().isoformat()
        return start <= day < end

    candidates = sorted(
        (row for row in report["selected"] if row["eligible"] and in_week(row)),
        key=lambda row: (
            -row["exceptional"],
            -row["interest"],
            -row["evidence"],
            -row["depth"],
            row["first_seen"],
            row["episode_key"],
            str(row["finding_id"]),
        ),
    )
    return {
        "schema_version": VERSION,
        "as_of_utc": as_of,
        "week_start": start,
        "week_end_exclusive": end,
        "curation_version": report["version"],
        "curation_sha256": digest(report),
        "candidate_count": len(candidates),
        "nominee": candidates[0] if candidates else None,
        "shortlist": candidates[:5],
        "research_leads": [row for row in report["priority_review"] if in_week(row)],
        "publication_status": "private_research_brief",
    }


def validate_research(raw, event, *, as_of=None):
    """Validate records and evidence binding, not the truth of researched claims."""
    from . import public_events as public

    public._keys(raw, ("week_start", "evidence_version", "reason", "sections", "sources", "limitations"))
    start = public._date(raw["week_start"])
    if start != week_start(event["observed_start"]).isoformat():
        raise ValueError("Weekly research must use the observation's Monday UTC date")
    if raw["evidence_version"] != event["evidence_version"]:
        raise ValueError("Research was prepared for different observation evidence")
    if as_of and start >= week_start(as_of).isoformat():
        raise ValueError("Weekly research requires a completed observation week")
    reason = public._text(raw["reason"], 600)
    if MATCH.search(reason):
        # The type is a database match, never identification, so a sighting is never "a match".
        raise ValueError("Weekly research must not call the sighting a match")
    normalized_sources = _sources(raw["sources"], as_of)
    ids = {source["id"] for source in normalized_sources}
    sections = raw["sections"]
    if not isinstance(sections, list) or not 2 <= len(sections) <= 8:
        raise ValueError("Weekly research needs two to eight substantive sections")
    normalized_sections, cited = [], set()
    for section in sections:
        public._keys(section, ("heading", "scope", "text", "sources"))
        if section["scope"] not in SCOPES:
            raise ValueError("Unknown research claim scope")
        if section["scope"] == "airframe_history" and not any(
            event["aircraft"].get(key) for key in ("registration", "icao_address")
        ):
            raise ValueError("Individual aircraft history requires an aircraft identity")
        checked = _section(section, ids)
        normalized_sections.append({**checked, "scope": section["scope"]})
        cited.update(checked["sources"])
    if cited != ids:
        raise ValueError("Every research source must support a section")
    return {
        "week_start": start,
        "evidence_version": event["evidence_version"],
        "reason": reason,
        "sections": normalized_sections,
        "sources": normalized_sources,
        "limitations": _limitations(raw["limitations"]),
    }


def _sources(sources, as_of):
    """Two to twelve distinct researched sources, one of them primary, normalized."""
    from . import public_events as public

    if not isinstance(sources, list) or not 2 <= len(sources) <= 12:
        raise ValueError("Weekly research needs two to twelve researched sources")
    normalized_sources = []
    for source in sources:
        public._keys(source, ("id", "title", "url", "publisher", "retrieved_at", "applies_to", "primary"))
        if not isinstance(source["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", source["id"]):
            raise ValueError("Invalid research source ID")
        reference = public._references(
            [{"id": source["id"], "label": source["title"], "url": source["url"]}]
        )[0]
        retrieved = public.utc(source["retrieved_at"])
        if as_of and public._instant(retrieved) > public._instant(as_of):
            raise ValueError("Research retrieval cannot be in the future")
        if type(source["primary"]) is not bool:
            raise ValueError("Research source needs an explicit primary-source flag")
        normalized_sources.append(
            {
                "id": reference["id"],
                "title": reference["label"],
                "url": reference["url"],
                "publisher": public._text(source["publisher"], 180),
                "retrieved_at": retrieved,
                "applies_to": public._text(source["applies_to"], 400),
                "primary": source["primary"],
            }
        )
    ids = {source["id"] for source in normalized_sources}
    if (
        len(ids) != len(sources)
        or len({source["url"] for source in sources}) != len(sources)
        or not any(source["primary"] for source in sources)
    ):
        raise ValueError("Use distinct sources, including at least one primary source")
    return normalized_sources


def _section(section, ids):
    """A section's heading, substantive text, and sorted known source IDs."""
    from . import public_events as public

    references = section["sources"]
    if (
        not isinstance(references, list)
        or not references
        or any(not isinstance(key, str) or key not in ids for key in references)
    ):
        raise ValueError("Every research section needs known source references")
    text = public._text(section["text"], 2500)
    if len(text) < 80:
        raise ValueError("Weekly research needs substantive context")
    return {
        "heading": public._text(section["heading"], 120),
        "text": text,
        "sources": sorted(set(references)),
    }


def _limitations(limitations):
    from . import public_events as public

    if not isinstance(limitations, list) or not 1 <= len(limitations) <= 8:
        raise ValueError("Record the material limits of the research")
    return [public._text(item, 600) for item in limitations]


def explainer_hypotheses(key):
    """The hypotheses the stories of explainer `key` list (story_facts.HYPOTHESES), in order."""
    from . import story_facts

    items = [item for kind in EXPLAINERS[key]["kinds"] for item in story_facts.HYPOTHESES.get(kind, ())]
    return list(dict.fromkeys(items))


EXPLAINER_FIELDS = (
    "key",
    "path",
    "title",
    "summary",
    "sections",
    "sources",
    "limitations",
    "facts",
    "written_at",
)


def validate_explainer(raw, *, as_of=None):
    """A one-off explainer page, normalized: its fixed key, path and title, sourced sections,
    and the facts it was written from (story count, registries consulted, hypotheses)."""
    from . import enrichment_rules
    from . import public_events as public

    public._keys(raw, EXPLAINER_FIELDS)
    spec = EXPLAINERS.get(raw["key"]) if isinstance(raw["key"], str) else None
    if spec is None or raw["path"] != spec["path"] or raw["title"] != spec["title"]:
        raise ValueError("Unknown explainer or a changed explainer page")
    written = public.utc(raw["written_at"])
    if as_of and public._instant(written) > public._instant(as_of):
        raise ValueError("An explainer cannot be written in the future")
    facts = raw["facts"]
    public._keys(facts, ("stories", "registries", "hypotheses"))
    if (
        type(facts["stories"]) is not int
        or facts["stories"] < spec["minimum"]
        or not isinstance(facts["registries"], list)
        or not facts["registries"]
        or facts["registries"] != sorted(set(facts["registries"]))
        or not set(facts["registries"]) <= enrichment_rules.REGISTRIES
        or not isinstance(facts["hypotheses"], list)
        or not 1 <= len(facts["hypotheses"]) <= 8
    ):
        raise ValueError("Invalid explainer facts")
    if as_of and facts["hypotheses"] != explainer_hypotheses(raw["key"]):
        # A new page lists its stories' hypotheses as they stand. A saved page keeps the
        # list it was written from, so a later change to the stories' list cannot refuse it.
        raise ValueError("Explainer hypotheses must be the ones its stories list")
    sources = _sources(raw["sources"], as_of)
    ids = {source["id"] for source in sources}
    sections = raw["sections"]
    if not isinstance(sections, list) or not 2 <= len(sections) <= 8:
        raise ValueError("An explainer needs two to eight substantive sections")
    normalized = []
    for section in sections:
        public._keys(section, ("heading", "text", "sources"))
        normalized.append(_section(section, ids))
    if {key for section in normalized for key in section["sources"]} != ids:
        raise ValueError("Every explainer source must support a section")
    return {
        "key": raw["key"],
        "path": spec["path"],
        "title": spec["title"],
        "summary": public._text(raw["summary"], 600),
        "sections": normalized,
        "sources": sources,
        "limitations": _limitations(raw["limitations"]),
        "facts": {
            "stories": facts["stories"],
            "registries": list(facts["registries"]),
            "hypotheses": [public._text(item, 160) for item in facts["hypotheses"]],
        },
        "written_at": written,
    }


def check_source(source, event, *, require_interest=True):
    """Bind source facts; new publication also requires current quality and interest.

    Structural recovery reads may withdraw a dossier accepted under older rules.
    A completed dossier never certifies a new publication's eligibility.
    """
    from . import public_events as public
    from . import selection, stories
    from .curation import context_only

    if not isinstance(source, dict):
        raise ValueError("Weekly research requires its original private finding")
    if not require_interest:
        # A newer measurement allowlist cannot reproduce every old projection.
        # Recovery validates the saved packet and dossier structurally; new
        # writes, approvals and exports must still regenerate their source below.
        return
    if context_only(source.get("kind"), source.get("evidence")):
        raise ValueError("Weekly research needs an independently interesting primary finding")
    if source.get("verdict") in {"routine", "bad_data"}:
        raise ValueError("The weekly source finding has been rejected")
    assessment = selection.assess({**source, "verdict": None}, {})
    if not assessment["story_eligible"] or assessment["review_required"]:
        raise ValueError("The weekly source finding still needs factual review")
    interval = tuple(public._instant(event[key]).timestamp() for key in ("observed_start", "observed_end"))
    # An absent saved match means no enrichment. Revalidating an approved
    # dossier must not consult a watchlist that may have changed since capture.
    source = {
        **source,
        "evidence": {**source["evidence"], "watchlist": source["evidence"].get("watchlist") or {}},
    }
    regenerated = stories.evidence_packet(source, include_track=False, observation_interval=interval)
    metadata = {"title": event["title"], "summary": event["summary"], "article_path": event["article_path"]}
    if "priority_evidence" not in event:
        # An event the publisher treats as ordinary carries no exception proof.
        metadata["priority"] = "ordinary"
    if public._projection(regenerated, metadata)["evidence_version"] != event["evidence_version"]:
        raise ValueError("Weekly source does not match the reviewed public evidence")


def binding(event):
    return digest({key: event[key] for key in ("id", "revision", "evidence_version", "weekly_spotlight")})


def research_html(event):
    research = event.get("weekly_spotlight")
    if not research:
        return ""
    research = validate_research(research, event)
    marker = binding(event)
    source_numbers = {source["id"]: index for index, source in enumerate(research["sources"], 1)}
    lines = [
        f'<section class="weekly-research" data-weekly-research="{marker}" aria-label="Weekly research">',
        "<h2>Why this was our weekly pick</h2>",
        f"<p>{escape(research['reason'])}</p>",
    ]
    for section in research["sections"]:
        citations = " ".join(
            f'<a href="#weekly-source-{key}" aria-label="Source {source_numbers[key]}">[{source_numbers[key]}]</a>'
            for key in section["sources"]
        )
        lines.extend(
            [
                f"<h2>{escape(section['heading'])}</h2>",
                f'<p class="research-scope">{SCOPES[section["scope"]]}</p>',
                f"<p>{escape(section['text'])} {citations}</p>",
            ]
        )
    lines.extend(["<h2>What remains uncertain</h2>", "<ul>"])
    lines.extend(f"<li>{escape(text)}</li>" for text in research["limitations"])
    lines.extend(["</ul>", "<h2>Research sources</h2>", "<ol>"])
    for source in research["sources"]:
        lines.append(
            f'<li id="weekly-source-{source["id"]}"><a href="{escape(source["url"], quote=True)}">'
            f"{escape(source['title'])}</a> — {escape(source['publisher'])}. "
            f"{escape(source['applies_to'])} "
            f'Accessed <time datetime="{source["retrieved_at"]}">{source["retrieved_at"][:10]}</time>.</li>'
        )
    lines.extend(["</ol>", "</section>"])
    return "\n".join(lines)


class PublishedBlocks(HTMLParser):
    """Locate active, nonduplicated research, observation and story prose sections in Ghost HTML."""

    MARKERS = (
        ("data-weekly-research", "research"),
        ("data-evidence-version", "evidence"),
        ("data-story-version", "story"),
    )

    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html, self.stack, self.blocks = html, [], []
        self.markers = 0
        self.lines = [0]
        for match in re.finditer("\n", html):
            self.lines.append(match.end())
        self.feed(html)
        self.close()

    def html_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        inert = (
            (self.stack and self.stack[-1][1])
            or tag in {"template", "script", "style", "noscript"}
            or "hidden" in attrs
            or attrs.get("aria-hidden") == "true"
            or bool(
                re.search(
                    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\s*(?:!important\s*)?(?:;|$)",
                    attrs.get("style", ""),
                    re.I,
                )
            )
        )
        marker = next((kind for attribute, kind in self.MARKERS if attribute in attrs), None)
        if marker == "research":
            self.markers += 1
        if tag not in self.VOID:
            self.stack.append((tag, bool(inert), marker, self.html_offset()))

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        _, inert, marker, start = self.stack[index]
        if marker and not inert:
            self.blocks.append((marker, self.html[start : self.html.find(">", self.html_offset()) + 1]))
        del self.stack[index:]


def published_research_matches(event, post):
    from . import publication

    tags = post.get("tags")
    if not isinstance(tags, list):
        return False
    names = {tag.get("name") for tag in tags if isinstance(tag, dict) and isinstance(tag.get("name"), str)}
    if not {"Weekly spotlight", "#weekly-" + binding(event)} <= names:
        return False
    parsed = PublishedBlocks(post.get("html", ""))
    researched = [html for kind, html in parsed.blocks if kind == "research"]
    evidence = [html for kind, html in parsed.blocks if kind == "evidence"]
    return (
        parsed.markers == 1
        and researched == [research_html(event)]
        and len(evidence) == 1
        and f'data-event-id="{event["id"]}"' in evidence[0]
        and f'data-evidence-version="{event["evidence_version"]}"' in evidence[0]
        and publication.observation_card_matches(event, evidence[0])
    )


def validate_picks(events, as_of):
    weeks = set()
    for event in events:
        if "weekly_spotlight" not in event:
            continue
        research = validate_research(event["weekly_spotlight"], event, as_of=as_of)
        if research != event["weekly_spotlight"]:
            raise ValueError("Research must be normalized")
        if research["week_start"] in weeks:
            raise ValueError("Only one weekly pick is allowed per observation week")
        weeks.add(research["week_start"])


def native_partials(snapshot):
    """Generate public-only Ghost templates with request-time week/post guards."""
    from . import public_events as public

    snapshot = public.validate_snapshot(snapshot)
    if snapshot["mode"] != "published":
        raise ValueError("Native weekly templates require a published snapshot")
    picks = [event for event in snapshot["events"] if "weekly_spotlight" in event]

    def literal(value):
        return json.dumps(value, ensure_ascii=False)

    def card(event):
        research = event["weekly_spotlight"]
        start = datetime.fromisoformat(research["week_start"]).date()

        def text(value):
            return escape(value, quote=True).replace("{", "&#123;").replace("}", "&#125;")

        label = f"{start.isoformat()} – {(start + timedelta(days=6)).isoformat()} UTC"
        url = text(event["article_path"])
        html = f'<article class="weekly-card"><div class="weekly-copy"><p class="eyebrow">{label}</p><h3><a href="{url}">{text(event["title"])}</a></h3><p>{text(research["reason"])}</p><a class="text-link" href="{url}">Read the researched story <span aria-hidden="true">↗</span></a></div>'
        if event.get("media"):
            html += f'<img class="weekly-image" src="{text(event["media"]["src"])}" alt="{text(event["media"]["alt"])}" width="720" height="480" loading="lazy" />'
        return html + "</article>"

    def changed_at(event):
        return public._instant(event["revised_at"]).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    empty = "<p>No researched pick is ready for last week yet.</p>"
    current = next(
        (
            event
            for event in picks
            if event["weekly_spotlight"]["week_start"] == previous_week(snapshot["as_of_utc"])[0]
        ),
        None,
    )
    active = empty
    if current:
        iso = public._instant(snapshot["as_of_utc"]).isocalendar()
        calendar_week = f"{iso.year}-W{iso.week:02d}"
        query = native_query(current)
        active = (
            '{{#match (date format="GGGG-[W]WW" timezone="UTC") ' + literal(calendar_week) + "}}"
            '{{#get "posts" filter=' + literal(query) + ' limit="1"}}'
            "{{#foreach posts}}" + card(current) + "{{else}}" + empty + "{{/foreach}}"
            "{{else}}" + empty + "{{/get}}"
            "{{else}}" + empty + "{{/match}}"
        )
    archived = []
    for event in picks:
        archived.append(
            "{{#match slug " + literal(public.article_slug(event["article_path"])) + "}}"
            '{{#match (date updated_at format="YYYY-MM-DDTHH:mm:ss.SSS[Z]" timezone="UTC") '
            + literal(changed_at(event))
            + "}}"
            "{{#has tag=" + literal("#weekly-" + binding(event)) + "}}" + card(event) + "{{/has}}"
            "{{/match}}{{/match}}"
        )
    return {
        "partials/weekly-current.hbs": active + "\n",
        "partials/weekly-approved-card.hbs": "\n".join(archived) + "\n",
    }


def native_query(event):
    from .public_events import _instant, article_slug

    # The pinned Ghost stores whole-second UTC SQL timestamps. ISO's T/Z
    # spelling does not compare equal in its SQLite channel queries.
    revised = _instant(event["revised_at"])
    if revised.microsecond:
        raise ValueError("Native weekly picks require Ghost's whole-second revision timestamps")
    revised = revised.strftime("%Y-%m-%d %H:%M:%S")
    return f"slug:{article_slug(event['article_path'])}+visibility:public+tag:weekly-spotlight+tag:hash-weekly-{binding(event)}+updated_at:'{revised}'"


def native_archive_filter(snapshot):
    from . import public_events as public

    snapshot = public.validate_snapshot(snapshot)
    if snapshot["mode"] != "published":
        raise ValueError("Native weekly routes require a published snapshot")
    queries = [f"({native_query(event)})" for event in snapshot["events"] if "weekly_spotlight" in event]
    return ",".join(queries) or "tag:hash-weekly-unconfigured"
