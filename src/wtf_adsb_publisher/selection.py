"""Deterministic editorial selection. Saved observations are never deleted here."""

from datetime import datetime

from . import curation, kinds
from .curation import ENVELOPE_KINDS, IDENTITY_KINDS, RECORD_KINDS, _mapping
from .rules import (
    ARCHIVE_SQUAWK_PREFIX,
    LIVE_SQUAWK_PREFIX,
    RATED_KINDS,
    SEASON_SUSTAINED_SECONDS,
    SQUAWK_PREFIXES,
    WATCH_TYPES,
    number,
    performance_thresholds,
)
from .telemetry import DIRECT

VERSION = 3
# The collector's own rows; every other origin is an archive import.
LIVE_ORIGIN = "Live receiver"
PROMOTION_SCORE = 65
STORY_SCORE = 75
# The publisher waits this long without reports before an encounter can be a story.
QUIET_SECONDS = 1800
# Spotting readings the collector confirms over sustained reports. Readings above a
# type envelope (curation.ENVELOPE_KINDS) or a type's certified limits
# (rules.RATED_KINDS), and season records (curation.RECORD_KINDS), are assessed
# from their own saved windows instead.
NUMERIC_KINDS = set(kinds.where(numeric=True))
NOVELTY_KINDS = set(kinds.where(novelty=True))


def _contextual_spike(event):
    """A documented isolated reading: neighbouring samples bracket one direct peak.

    It ranks a private review lead; it never publishes, because the owner
    publishes no one-report stories (the single_report gate, curation.gates).
    """
    evidence = event["evidence"]
    fields = {
        "outlier_gs": "gs",
        "type_speed_mismatch": "gs",
        "type_altitude_mismatch": "alt_baro",
        "outlier_alt_baro": "archive_alt",
        "outlier_archive_alt": "archive_alt",
    }
    field = fields.get(event["kind"])
    if field is None:
        return False
    peak = number(evidence.get("observed_at"))
    samples = evidence.get("context_samples", [])
    if peak is None or not isinstance(samples, list):
        return False
    before, after, at_peak = [], [], []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        stamp, value = number(sample.get("timestamp")), number(sample.get(field))
        if stamp is None or value is None or abs(stamp - peak) > 90:
            continue
        source = sample.get("source")
        if not isinstance(source, str) or source in {"", "unknown"}:
            continue
        reading = (stamp, value, source)
        if stamp < peak:
            before.append(reading)
        elif stamp > peak:
            after.append(reading)
        else:
            at_peak.append(reading)
    value = number(event.get("value"))
    if value is None or not before or not after or len(at_peak) != 1:
        return False
    previous, following, peak_reading = max(before), min(after), at_peak[0]
    # A multilateration value is the solver's estimate, not a transmitted field.
    # The value keeps its sign, so a negative spike compares by magnitude too.
    return (
        previous[2] == following[2] == peak_reading[2]
        and peak_reading[2] in DIRECT
        and abs(peak_reading[1] - value) <= 1
        and abs(value) > 2 * max(abs(previous[1]), abs(following[1]), 1)
    )


# Kinds scored by kind alone, with the reason they carry, in their recorded order.
FIXED_SCORES = kinds.table(
    "fixed_score",
    order=(
        "identity_conflict",
        "military",
        "database_watch",
        "unusual_emitter",
        "vertical",
        "wind_boost",
        "altitude_gap",
        "distant",
        "holding_stack",
        "winds_aloft_day",
        "foreign_military",
        "unregistered_address",
    ),
)
# A novelty claim (first sighting, notable variant) needs two reports at least 8 s apart.
NOVELTY_MIN_HITS = 2
NOVELTY_MIN_SECONDS = 8


def _brief(event):
    """Too few reports, or too short a span, to support a novelty claim."""
    first, last = number(event.get("first_seen")), number(event.get("last_seen"))
    return (number(event.get("hits")) or 0) < NOVELTY_MIN_HITS or (
        first is not None and last is not None and last - first < NOVELTY_MIN_SECONDS
    )


def assess(event, profiles=None, *, independent=False):
    """Score one saved finding for private views and story eligibility.

    Context-only kinds stay below promotion unless `independent` says their
    encounter also holds an independently promoted finding.
    """
    evidence = _mapping(event.get("evidence"))
    aircraft = _mapping(evidence.get("aircraft"))
    kind, value = event["kind"], number(event.get("value"))
    reasons, review, private = [], False, False
    score = 40
    speed, altitude = performance_thresholds(aircraft, profiles)
    if kind in {"fast", "high"}:
        threshold = speed if kind == "fast" else altitude
        step = 10 if kind == "fast" else 1000
        score = (
            70 + min(15, int((value - threshold) / step)) if value is not None and value >= threshold else 30
        )
        reasons.append(f"Local promotion threshold: {threshold:g} {'kn' if kind == 'fast' else 'ft'}")
    elif kind == "vintage":
        story = curation.VINTAGE_YEARS
        score = (
            80 if value is not None and value >= story else 65 if value is not None and value >= 65 else 30
        )
        reasons.append(f"Ages below {story} stay private; below 65 they are searchable metadata")
    elif kind == "watch_type":
        score = 82 if isinstance(aircraft.get("t"), str) and aircraft["t"] in WATCH_TYPES else 45
    elif kind in NOVELTY_KINDS:
        score = 80
        if _brief(event):
            score, review = 50, True
            reasons.append("A novelty claim needs two reports at least 8 seconds apart")
    elif kind in FIXED_SCORES:
        score, reason = FIXED_SCORES[kind]
        if reason:
            reasons.append(reason)
    elif kind == "watchlist_match":
        match = _mapping(evidence.get("watchlist"))
        specific = isinstance(match.get("neutral_category"), str) and match.get("neutral_category") in {
            "Public figure association",
            "Historic aircraft",
            "Distinctive aircraft",
            "Royal aviation association",
            "Aerobatic display aircraft",
            "Specialized aircraft",
        }
        score = 80 if specific and match.get("reference_url") else 65 if specific else 50
        if (
            match.get("public_claim_allowed") is not True
            or match.get("match_status") != "icao_and_registration"
        ):
            score, review = 20, True
        source = _mapping(match.get("source"))
        try:
            source_date = datetime.fromisoformat(source["source_date"])
            stale = source_date.tzinfo is None or event["last_seen"] - source_date.timestamp() > 180 * 86400
        except (KeyError, TypeError, ValueError, AttributeError):
            stale = True
        if stale:
            score, review = min(score, 60), True
            reasons.append("Watchlist source age needs review; source dates do not verify associations")
    elif kind == "rare_here":
        rarity = _mapping(evidence.get("rarity"))
        aircraft_days, type_days = number(rarity.get("aircraft_days")), number(rarity.get("type_days"))
        review = aircraft_days is None or type_days is None
        resident = (aircraft_days or 0) > 2 or (type_days or 0) > 3
        score = 35 if resident else 70
        reasons.append("Repeat days matter as well as the number of aircraft addresses")
    elif kind.startswith(SQUAWK_PREFIXES):
        # Only a confirmed live episode below can make a status story-eligible.
        score, review, private = 60, True, True
        reasons.append(
            "A transponder status needs a confirmed live episode; archived labels never confirm one"
        )
    elif kind in {"circling", "repeated_passes", "loitering", "confined_activity"}:
        pattern = _mapping(evidence.get("pattern"))
        review = pattern.get("quality_review_required") is not False
        score = {"circling": 80, "repeated_passes": 78, "loitering": 75, "confined_activity": 45}[kind]
        if review:
            score = min(score, 60)
            reasons.append("Source or geometry needs review before promotion")
        if pattern.get("shape_verified") is not True:
            score, review = min(score, 60), True
            reasons.append("Saved pattern predates the closure/corridor checks")
    elif (
        kind in RECORD_KINDS
        or kind in RATED_KINDS
        or (kind in ENVELOPE_KINDS and _mapping(evidence.get("confirmation")).get("sustained") is True)
    ):
        confirmation = _mapping(evidence.get("confirmation"))
        window = _mapping(evidence.get("season_record"))
        if kind in RECORD_KINDS and not confirmation and window:
            # A seasonal record carries its own sustained direct-report window.
            confirmation = {"sustained": True, "duration_seconds": window.get("sustained_seconds")}
            source, reports = window.get("source"), number(window.get("samples")) or 0
        else:
            source, reports = aircraft.get("type"), event.get("hits", 0)
        score = 75
        if confirmation.get("sustained") is not True or (
            kind in RECORD_KINDS
            and (number(confirmation.get("duration_seconds")) or 0) < SEASON_SUSTAINED_SECONDS
        ):
            score, review = 50, True
            reasons.append("Sustained direct reports were not preserved")
        if confirmation.get("quality_flags") or reports < 2 or source not in DIRECT:
            score, review = min(score, 50), True
            reasons.append("Measurement provenance needs review; only sustained direct reports qualify")
        if kind in ENVELOPE_KINDS:
            reasons.append("Sustained readings beyond the listed type's envelope suggest a metadata mismatch")
    elif kind.startswith("outlier_") or kind in {"position_jump", "space_category"} | ENVELOPE_KINDS:
        score, review = 50, True
        if _contextual_spike(event):
            # A bracketed spike is a better private lead than a bare extreme, never a story.
            score = 60
            reasons.append("Neighboring samples document an isolated reading, not a flight capability")
        else:
            reasons.append("Extreme magnitude alone does not establish reliable evidence")
    if kind in NUMERIC_KINDS:
        confirmation = _mapping(evidence.get("confirmation"))
        if confirmation.get("quality_flags"):
            review = True
            reasons.append("Measurement or position provenance needs review")
        if confirmation.get("sustained") is not True:
            review = True
            reasons.append("Sustained field-coherent observations were not preserved")
        if event.get("hits", 0) < 2:
            score = min(score, 40)
        if not isinstance(aircraft.get("type"), str) or aircraft["type"] in {"mlat", "unknown"}:
            review = True
            reasons.append("Source reliability needs review; correlated fields are not independent evidence")
    verdict = event.get("verdict")
    episode = _mapping(evidence.get("maneuver"))
    if episode:
        review = episode.get("quality_review_required") is not False or episode.get("confirmed") is not True
        if kind in {"rapid_climb", "rapid_descent"} and (
            (number(episode.get("qualifying_duration_seconds")) or 0) < 30
            or (number(episode.get("qualifying_sample_count")) or 0) < 4
        ):
            review = True
            reasons.append("Consecutive above-threshold reports have not been established")
        score = 95 if kind.startswith(LIVE_SQUAWK_PREFIX) else 80
        private = private and review
        if review:
            score = min(score, 60)
            reasons.extend(episode.get("quality_flags", []))
            if episode.get("confirmed") is not True:
                reasons.append("Episode has not met its confirmation window")
    if kind.startswith(ARCHIVE_SQUAWK_PREFIX):
        score, review, private = min(score, 60), True, True
    tags = curation.context_tags(evidence)
    if kind in curation.CIRCUIT_KINDS and (evidence.get("airport_context") or tags & curation.AIRPORT_TAGS):
        score = min(score, 50)
        reasons.append("Observed near a runway's circuit or approach; retained as routine-context evidence")
    if evidence.get("quality_flags"):
        score, review = min(score, 60), True
        reasons.append("Blocking quality flags require review")
    if kind in IDENTITY_KINDS and _mapping(evidence.get("identity_checks")).get("conflict") is True:
        score, review = min(score, 60), True
        reasons.append("Identity checks disagree; the database identity cannot support this story")
    blocking = curation.gates(event)
    if blocking:
        # A second source's disagreement, too few reports, or an unsettled rated
        # verdict hold the finding for review whatever its score.
        review = True
        reasons.extend(reason for _, reason in blocking)
    hold = curation.type_hold(event)
    if hold:
        # Not a review hold: selection waits for the type check and resumes unattended.
        reasons.append(hold[1])
    if not independent and curation.context_only(kind, evidence):
        score = min(score, PROMOTION_SCORE - 1)
        reasons.append("Supporting context; promoted only beside an independent finding in its encounter")
    if verdict == "keep":
        score = max(80, score)
        reasons.append("Marked worth keeping; this is editorial feedback, not factual verification")
    elif verdict in {"routine", "bad_data"}:
        score = 0
        reasons.append("Marked " + verdict.replace("_", " "))
    return {
        "version": VERSION,
        "score": score,
        "promoted": score >= PROMOTION_SCORE and (not review or verdict == "keep"),
        "story_eligible": score >= STORY_SCORE
        and (not review or verdict == "keep")
        and kind != "distant"
        and not private,
        "review_required": review,
        "reasons": reasons,
        "blocking_rules": [rule for rule, _ in blocking],
        "publication_hold": hold[0] if hold else None,
        "verdict": verdict,
    }


def publishable(source):
    """Whether one saved finding may become an unattended story: the one eligibility check.

    A Keep vote never certifies uncertain telemetry, so a rejected verdict or any
    quality flag refuses it. Archived labels never confirm a status, and a live
    status needs its confirmed episode (8 s and two reports). A second source
    that disagrees about the type (unless a mismatch story names it), or none
    for a performance kind, refuses it through the review gates; a check still
    pending is a selection hold
    (curation.type_hold), not a refusal, so a released story is never
    unpublished for a check that has not run. The rest is assess() without the
    vote: story eligible, no review, and not context only.
    """
    if curation.absorbed(source) or source.get("verdict") in {"routine", "bad_data"}:
        return False
    raw = source.get("evidence", {})
    metrics = raw.get("maneuver", {})
    if (
        metrics.get("quality_flags")
        or raw.get("confirmation", {}).get("quality_flags")
        or raw.get("quality_flags")
    ):
        return False
    if source["kind"].startswith(ARCHIVE_SQUAWK_PREFIX):
        return False
    # A kind the story renderer cannot handle has no unattended story.
    from .stories import STORY_KINDS

    if source["kind"] not in STORY_KINDS:
        return False
    if source["kind"].startswith(LIVE_SQUAWK_PREFIX) and not (
        metrics.get("confirmed") is True
        and metrics.get("quality_review_required") is False
        and (number(metrics.get("duration_seconds")) or 0) >= 8
        and (number(metrics.get("sample_count")) or 0) >= 2
    ):
        return False
    decision = assess({**source, "verdict": None}, {})
    return bool(
        decision["story_eligible"]
        and not decision["review_required"]
        and not curation.context_only(source["kind"], raw)
    )
