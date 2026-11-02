"""Private migration rehearsal checks; normal publication does not use these."""

import fcntl
import hashlib
import json
import os
import time
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path

from . import public_events as public
from .cli import copy_state
from .feed_adapter import feed_fresh
from .ingest import ingest_directory


@contextmanager
def stopped_state(directory):
    """Take all writer locks without waiting behind a paid request."""
    with ExitStack() as stack:
        for name in ("writer/status.json.lock", "weekly/status.json.lock", "ledger.json.lock"):
            path = Path(directory) / name
            # Existing deployments create these locks themselves. A missing lock
            # is a setup error; a rehearsal must not create live state files.
            handle = stack.enter_context(path.open("r"))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BlockingIOError("Publisher is busy; retry the snapshot later") from error
        yield


def capture_state(source, destination):
    """Capture legacy JSON state. New inbox SQLite state requires stopped writers."""
    with stopped_state(source):
        return copy_state(source, destination)


def digest_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def ready_feed(directory, inbox, *, at=None, maximum_age=300):
    """Import first, then reject incomplete, stale, synthetic or quarantined input."""
    for _ in range(3):
        state = ingest_directory(directory, inbox)
        manifest = public.read_json(Path(directory) / "manifest.json")
        if (state["stream_id"], state["generation"], state["cursor"]) == (
            manifest["stream_id"],
            manifest["generation"],
            manifest["head_sequence"],
        ):
            break
    if (
        state["stream_id"] != manifest["stream_id"]
        or state["generation"] != manifest["generation"]
        or state["cursor"] != manifest["head_sequence"]
        or state.get("quarantined")
        or state.get("synthetic")
        or state.get("health", {}).get("mode") != "live"
        or not feed_fresh(state, time.time() if at is None else at, maximum_age)
    ):
        raise ValueError("Comparison requires a complete fresh live feed at its captured head")
    return {key: state[key] for key in ("stream_id", "generation", "cursor", "health")}


def _new_identity(record):
    source = record.get("automation", {}).get("source", {})
    return tuple(source.get(key) for key in ("hex", "kind", "first_seen"))


def _record_history(record, normalized_source=None, normalized_media=None):
    value = deepcopy(record)
    for revision in (value, *value.get("history", [])):
        automatic = revision.get("automation", {})
        for key in ("engine_stream_id", "engine_event_id", "engine_finding_id", "engine_revision"):
            automatic.pop(key, None)
    if normalized_source is not None:
        value["automation"]["source"] = normalized_source
    if normalized_media is not None:
        value["automation"].setdefault("media_parts", normalized_media)
        value["automation"].setdefault(
            "media_evidence_version",
            public._projection(value["evidence"], value["editorial"])["evidence_version"],
        )
    return value


def compare_runs(baseline, old, new):
    """Compare deterministic decisions; random new IDs are matched by evidence."""
    failures = []
    old_records = {record["id"]: record for record in old["ledger"]["records"]}
    new_records = {record["id"]: record for record in new["ledger"]["records"]}
    baseline_ids = {record["id"] for record in baseline["records"]}
    for identifier in sorted(baseline_ids):
        if identifier not in old_records or identifier not in new_records:
            failures.append({"check": "missing_history", "id": identifier})
        elif _record_history(
            old_records[identifier],
            old.get("normalized_sources", {}).get(identifier),
            old.get("normalized_media", {}).get(identifier),
        ) != _record_history(
            new_records[identifier],
            new.get("normalized_sources", {}).get(identifier),
            new.get("normalized_media", {}).get(identifier),
        ):
            failures.append({"check": "changed_history", "id": identifier})
    for label, result in (("legacy", old), ("publisher", new)):
        report = result["report"]
        if (
            report.get("failed")
            or report.get("slug_conflicts")
            or report.get("basemaps_missing")
            or report.get("privacy") != "ok"
            or report.get("build") != "ok"
        ):
            failures.append({"check": "invalid_release", "implementation": label})
    old_events = {event["id"]: event for event in old["snapshot"]["events"]}
    new_events = {event["id"]: event for event in new["snapshot"]["events"]}
    for identifier in sorted(baseline_ids):
        if old_events.get(identifier) != new_events.get(identifier):
            failures.append({"check": "changed_article", "id": identifier})

    def additions(records, events, run):
        result = {}
        for identifier, record in records.items():
            if identifier in baseline_ids:
                continue
            identity = _new_identity(record)
            if None in identity or identity in result:
                raise ValueError("New shadow candidates have an ambiguous factual identity")
            automatic = record.get("automation", {})
            event = public.project(record["evidence"], record["editorial"], strict=False)
            result[identity] = {
                "state": record["state"],
                "published": identifier in events,
                "exceptional": automatic.get("published_exceptional"),
                "rank": automatic.get("rank"),
                "blocked": automatic.get("blocked"),
                "removed": automatic.get("removed"),
                "source": run.get("normalized_sources", {}).get(identifier),
                "media_parts": automatic.get("media_parts", run.get("normalized_media", {}).get(identifier)),
                "facts": {
                    key: event.get(key)
                    for key in (
                        "article_path",
                        "evidence_version",
                        "source",
                        "primary_type",
                        "synthetic",
                        "aircraft",
                        "kind",
                        "observed_start",
                        "observed_end",
                        "measurements",
                        "limitations",
                        "context",
                        "references",
                        "secondary_labels",
                        "participants",
                        "priority_evidence",
                        "watchlist",
                    )
                },
            }
        return result

    if additions(old_records, old_events, old) != additions(new_records, new_events, new):
        failures.append({"check": "changed_new_candidates"})
    old_actions = sorted(old["writes"], key=lambda row: json.dumps(row, sort_keys=True))
    new_actions = sorted(new["writes"], key=lambda row: json.dumps(row, sort_keys=True))
    if old_actions != new_actions:
        failures.append({"check": "changed_ghost_actions"})
    if old["existing_post_ids"] != new["existing_post_ids"]:
        failures.append({"check": "changed_ghost_baseline"})
    for key in ("posts", "media"):
        if key == "posts":
            ids = set(old["existing_post_ids"])
            first = {item["id"]: item for item in old[key] if item["id"] in ids}
            second = {item["id"]: item for item in new[key] if item["id"] in ids}
        else:
            first = {identifier: old[key].get(identifier) for identifier in baseline_ids}
            second = {identifier: new[key].get(identifier) for identifier in baseline_ids}
        if first != second:
            failures.append({"check": "changed_" + key})
    return {
        "passed": not failures,
        "failures": failures,
        "retained_records": len(baseline_ids),
        "articles": len(new_events),
    }


def shadow_gate(samples, *, now=None, duration=86400):
    """A continuous UTC-crossing interval is required, with no missing checkpoints."""
    now = time.time() if now is None else now
    samples = sorted(samples, key=lambda item: item["at"])
    # A failed checkpoint, changed artifact or missed interval starts a new soak.
    window = []
    for sample in samples:
        if sample.get("passed") is not True or not sample.get("artifacts_sha256"):
            window = []
            continue
        if window and (
            sample["artifacts_sha256"] != window[-1]["artifacts_sha256"]
            or sample.get("runtime_identity") != window[-1].get("runtime_identity")
            or sample.get("configuration_sha256") != window[-1].get("configuration_sha256")
            or sample.get("feed_identity") != window[-1].get("feed_identity")
            or not 0 < sample["at"] - window[-1]["at"] <= 2100
        ):
            window = []
        window.append(sample)
    if not window:
        return False
    stamps = [item["at"] for item in window]
    return (
        stamps[-1] - stamps[0] >= duration
        and 0 <= now - stamps[-1] <= 2100
        and int(stamps[0] // 86400) < int(stamps[-1] // 86400)
    )


def commit_receipt(path, evidence):
    """Persist the conservative point of no automatic rollback before activation."""
    path = Path(path)
    required = {
        "artifacts_sha256",
        "configuration_sha256",
        "inventory_sha256",
        "state_sha256",
        "reservations_sha256",
        "feed",
    }
    if not required <= evidence.keys() or any(not evidence[key] for key in required):
        raise ValueError("Activation requires complete verified cutover evidence")
    if path.exists():
        raise ValueError("Activation has already been committed; reconcile forward")
    value = {
        **deepcopy(evidence),
        "version": 1,
        "at": time.time(),
        "phase": "external_mutations_may_have_started",
    }
    # O_EXCL prevents concurrent activation controllers overwriting this boundary.
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    # An incomplete receipt is still a hold. Never remove it on failure.
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, allow_nan=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return value
