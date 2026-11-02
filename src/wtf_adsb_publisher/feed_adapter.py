"""Translate the collection profile into this site's preserved selection policy."""

import math
from copy import deepcopy
from datetime import datetime

from . import curation, public_events


def feed_fresh(state, now, maximum_age=300):
    if (
        isinstance(maximum_age, bool)
        or not isinstance(maximum_age, (int, float))
        or not 0 < maximum_age < math.inf
    ):
        raise ValueError("max_feed_age_seconds must be finite and positive")
    health = state.get("health", {})
    if health.get("ready") is not True or health.get("complete") is not True:
        return False
    if health.get("mode") == "replay":
        return True
    stamp = health.get("last_receiver_poll")
    if isinstance(stamp, str):
        try:
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return False
    watermark = health.get("analysis_watermark")
    if isinstance(watermark, str):
        try:
            watermark = datetime.fromisoformat(watermark.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return False
    return all(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0 <= now - value <= maximum_age
        for value in (stamp, watermark)
    )


def _identity(source):
    return tuple(source.get(key) for key in ("hex", "kind", "first_seen"))


def binding_identity(binding):
    stream, finding = (binding.get(key) for key in ("engine_stream_id", "engine_finding_id"))
    return ("engine", stream, finding) if stream and finding else None


def source_association(source, binding):
    return binding_identity(binding) or _identity(source)


def record_associations(record):
    """A bound record follows opaque engine IDs, even when source timestamps change."""
    automatic = [
        item
        for revision in (record, *record.get("history", []))
        if isinstance(item := revision.get("automation"), dict)
    ]
    return {
        source_association(item["source"], item) for item in automatic if isinstance(item.get("source"), dict)
    }


def collection(state, ledger=None, *, at):
    """The imported groups in the shape the site policy selects from, with the novelty counts
    per UTC day, each finding's source and engine binding by finding id, and the type norms the
    engine exported for norm-kind findings (context.type_norms, contract 1.1.0) by finding id."""
    groups, novelty, sources, bindings, norms = [], {}, {}, {}, {}
    records = (ledger or {}).get("records", [])
    # A pre-split article keeps its original episode identity and public URL.
    prior, current_records = {}, {}
    for record in records:
        for revision in (record, *record.get("history", [])):
            automatic = revision.get("automation") or {}
            source = automatic.get("source")
            if source and (automatic.get("source_kind") != "frozen" or binding_identity(automatic)):
                identity = source_association(source, automatic)
                prior.setdefault(identity, set()).add(record["episode_key"])
                current_records.setdefault(identity, []).append(revision)
    incoming = {}
    for item in state.get("observations", []):
        for source in item["observation"]["group"]["events"]:
            incoming.setdefault(_identity(source), set()).add(source["id"])
    for identity, matches in incoming.items():
        if len(matches) > 1 and any(
            record.get("automation", {}).get("engine_finding_id") not in matches
            for record in current_records.get(identity, [])
        ):
            raise ValueError(
                "A legacy article matches multiple engine findings; reconcile its identity first"
            )
    for item in state.get("observations", []):
        observation = item["observation"]
        group = deepcopy(observation["group"])
        day = curation.utc_day(group["first_seen"])
        counts = observation["context"].get("novelty")
        if day in novelty and novelty[day] != counts:
            raise ValueError("Conflicting novelty context for one observation day")
        novelty[day] = counts
        # A 1.0.0 observation carries no norms; its norm-kind findings read as having none.
        norms.update(deepcopy(observation["context"].get("type_norms", {})))
        existing = {
            key
            for source in group["events"]
            for identity in (("engine", state["stream_id"], source["id"]), _identity(source))
            for key in prior.get(identity, ())
        }
        # A merged observation can cover several existing stories. Keep its new
        # selection identity; stage() resolves each source to its existing row.
        if len(existing) == 1:
            group["id"] = next(iter(existing))
        else:
            group["id"] = item["event_id"]
        for source in group["events"]:
            identifier = source["id"]
            if identifier in sources:
                raise ValueError("One finding cannot belong to multiple current observations")
            sources[identifier] = source
            binding = {
                "engine_event_id": item["event_id"],
                "engine_finding_id": identifier,
                "engine_revision": item["revision"],
                "engine_stream_id": state["stream_id"],
            }
            media = observation.get("media", {}).get("by_finding", {}).get(identifier, {})
            binding["media_parts"] = deepcopy(media.get("parts", []))
            bindings[identifier] = binding
            matched_records = [
                record
                for identity in (binding_identity(binding), _identity(source))
                for record in current_records.get(identity, [])
            ]
            for record in matched_records:
                automatic = record.get("automation", {})
                if automatic.get("engine_finding_id") not in {None, identifier}:
                    continue
                automatic.update({key: value for key, value in binding.items() if key != "media_parts"})
        groups.append(group)
    apply_retractions(state, ledger, at=at)
    return groups, novelty, sources, bindings, norms


def unexported_norms(state, candidates):
    """The story keys of norm-kind candidates that take no norm because their observation
    predates contract 1.1.0, sorted.

    A 1.1.0 observation names every norm-kind finding in context.type_norms, with null when
    the engine had no norm; a 1.0.0 one names none. Such a story is still staged, and keeps
    the None it was staged with, so the pass reports it instead of freezing it silently.
    """
    exported = {
        finding
        for item in state.get("observations", [])
        for finding in item["observation"]["context"].get("type_norms", {})
    }
    return sorted(
        candidate["key"]
        for candidate in candidates
        if candidate["source"].get("kind") in curation.NORM_KINDS
        and "type_norm" in candidate
        and candidate["type_norm"] is None
        and candidate["source"].get("id") not in exported
    )


def apply_retractions(state, ledger, *, at):
    """Trusted durable withdrawals remain actionable when the rest of a feed is held."""
    retracted = {item["event_id"]: item for item in state.get("retractions", [])}
    records = (ledger or {}).get("records", [])
    current = {}
    active_ids = set()
    for item in state.get("observations", []):
        active_ids.add(item["event_id"])
        for source in item["observation"]["group"]["events"]:
            current.setdefault(source["id"], []).append(item)
    for record in records:
        automatic = record.get("automation", {})
        event = automatic.get("engine_event_id")
        if automatic.get("engine_stream_id") != state.get("stream_id") or event not in retracted:
            continue
        retirement = retracted[event]
        replacements = retirement.get("superseded_by", [])
        if retirement.get("reason_code") == "superseded" and replacements:
            automatic["engine_superseded_by"] = deepcopy(replacements)
            matches = current.get(automatic.get("engine_finding_id"), [])
            if len(matches) == 1 and matches[0]["event_id"] != event:
                # Group topology changed, but this article's finding survives.
                # Rebind even during a partial import without re-running selection.
                automatic.update(
                    engine_event_id=matches[0]["event_id"], engine_revision=matches[0]["revision"]
                )
                continue
            if not set(replacements) <= active_ids:
                # A later committed batch may carry the replacement. Suspension
                # can recover automatically; permanent withdrawal would lose its URL.
                automatic["blocked"] = "engine_replacement_pending"
                continue
        automatic["blocked"] = "engine_retraction"
        automatic["engine_retraction"] = retirement.get("reason_code", "invalidated")
        if record["state"] != "withdrawn":
            public_events.set_review_state(ledger, record["id"], "withdrawn", at=at)
