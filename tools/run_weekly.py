#!/usr/bin/env python3
"""Research missing weekly features; the normal publisher releases completed dossiers.

--plan lists each week's state, top candidate and reserved research cost without
any request or write. --carry-over-from LEDGER --no-research attaches a retired
ledger's dossier to a week's top candidate of the same type (and the same
airframe, date and observation facts when the dossier is about the airframe or
its record), rebinding its evidence version. It makes no request and never reads
the API key.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from run_autopilot import privacy_guard

from wtf_adsb_publisher import editorial, explain, plugins, rules, weekly_spotlight
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher import weekly_research as weekly

# Sections about one aircraft or its flight bind a dossier to that airframe and date.
AIRFRAME_SCOPES = frozenset({"interpretation", "airframe_history"})
# The research prompt forbids calling the sighting a "match"; an older reason that
# does is replaced when its dossier is carried over.
MATCH_PHRASE = re.compile(r"\bmatch(?:es|ed|ing)?\b", re.I)


def receipt_file(directory, start, event):
    return directory / f"{start}-{event['id']}-{event['evidence_version']}.json"


def requested_weeks(config, at, count):
    requested = weekly.weeks(at, count)
    if config.get("weekly_since"):
        start = date.fromisoformat(public._date(config["weekly_since"]))
        if start.weekday() != 0:
            raise ValueError("weekly_since must be a Monday")
        end = weekly_spotlight.week_start(at)
        requested = [
            (start + timedelta(weeks=index)).isoformat() for index in range(max(0, (end - start).days // 7))
        ]
    return requested


def _text(value):
    return " ".join(value.split()).casefold() if isinstance(value, str) and value.strip() else None


def _numbers(block):
    """The numeric entries of an evidence block, as a comparable mapping."""
    if not isinstance(block, dict):
        return {}
    return {
        key: value
        for key, value in block.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _when(value):
    """A timestamp in normalized UTC, or as written when it is not one."""
    try:
        return public.utc(value)
    except ValueError:
        return value


def record_facts(evidence):
    """The observation facts research about one record rests on: the finding's kind,
    interval and metric, the readings and episode or pattern figures it saved, and
    the database build year. Read from a raw packet, so a missing block is empty."""
    if not isinstance(evidence, dict):
        return None
    observation = evidence.get("observation") if isinstance(evidence.get("observation"), dict) else {}
    received = evidence.get("received") if isinstance(evidence.get("received"), dict) else {}
    readings = received.get("readings") if isinstance(received.get("readings"), list) else []
    metadata = evidence.get("metadata") if isinstance(evidence.get("metadata"), dict) else {}
    return {
        "kind": evidence.get("kind"),
        "interval": [_when(observation.get("start_utc")), _when(observation.get("end_utc"))],
        "metric": [observation.get("metric_value"), observation.get("metric_unit")],
        "readings": {item.get("field"): item.get("value") for item in readings if isinstance(item, dict)},
        "episode": _numbers(evidence.get("maneuver")),
        "pattern": _numbers(evidence.get("pattern")),
        "database_year": metadata.get("database_year"),
    }


def old_dossiers(path):
    """A retired ledger's published dossiers by week. The ledger is read, never validated."""
    found = defaultdict(list)
    for record in public.read_json(path).get("records", []):
        research = (record.get("editorial") or {}).get("weekly_spotlight")
        evidence = record.get("evidence") or {}
        if record.get("state") != "approved" or not isinstance(research, dict):
            continue
        try:
            observed = public.utc((evidence.get("observation") or {}).get("start_utc"))
        except (ValueError, AttributeError):
            continue
        found[research.get("week_start")].append(
            {
                "record_id": record.get("id"),
                "research": research,
                "aircraft": evidence.get("aircraft") or {},
                "kind": evidence.get("kind"),
                "year": (evidence.get("metadata") or {}).get("database_year"),
                "observed_start": observed,
                "facts": record_facts(evidence),
            }
        )
    return found


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _compact(value):
    """An identifier without separators, casefolded: C-GABC and CGABC are one spelling."""
    compact = re.sub(r"[^a-z0-9]", "", _text(value) or "")
    return compact or None


def _token(name, spellings):
    """``name`` as a whole token: not inside a longer run of letters and digits. With
    ``spellings`` it is a compact identifier, found with or without a separator
    between any two of its characters (c-gabc, cgabc, a c-gabc-photo URL slug)."""
    inner = r"[-.]?".join(map(re.escape, name)) if spellings else re.escape(name)
    return r"(?<![a-z0-9])" + inner + r"(?![a-z0-9])"


def _mentions(research, names, *, spellings=False):
    words = " ".join(_text(text) or "" for text in _strings(research))
    return any(re.search(_token(name, spellings), words) for name in names)


def _prose(research):
    """The dossier's reason and section prose, without its week, source dates or URLs."""
    sections = [section for section in research.get("sections") or () if isinstance(section, dict)]
    return [
        research.get("reason"),
        *(section.get(key) for section in sections for key in ("heading", "text")),
    ]


def names_airframe(research, aircraft):
    """Whether any of the dossier's text, sources included, names the observed airframe.

    A registration is matched in every spelling: the stored C-GABC is named by
    "CGABC" in prose or a c-gabc URL as well as by itself.
    """
    names = {_compact(aircraft.get(key)) for key in ("registration", "icao_address", "callsign")} - {None}
    return _mentions(research, names, spellings=True)


def names_record(research, dossier):
    """Whether the dossier's prose names its old record: its build year or observation date in
    the reason or sections, or the finding's kind in the reason, which describes the observation
    rather than the type. The week and the sources' retrieval dates are not prose.

    "The saved metadata pairs a WACF type code with a 1941 build year" is about
    one airframe's record, so carried to another WACF it would be a false claim.
    """
    observed = public._instant(dossier["observed_start"])
    month = editorial.MONTH_NAMES[observed.month - 1].casefold()
    names = {observed.date().isoformat(), f"{observed.day} {month}", f"{month} {observed.day}"}
    year = dossier.get("year")
    if isinstance(year, (int, float)) and not isinstance(year, bool) and 1900 <= year <= 2100:
        names.add(str(int(year)))
    kind = dossier.get("kind")
    return _mentions(_prose(research), names) or (
        isinstance(kind, str) and bool(kind) and _mentions(research.get("reason"), {kind.replace("_", " ")})
    )


def carry_match(dossier, event, record=None):
    """Whether an old dossier fits a new event: (True, basis) or (False, reason).

    Research about the airframe or its record moves only to the same aircraft on
    the same date, and only when ``record`` (the new event's ledger record) holds
    the observation facts the old one did: a corrected build year, a changed
    reading or another encounter that day gets fresh research instead.
    """
    old, new = dossier["aircraft"], event["aircraft"]
    if not _text(old.get("type_code")) or _text(old.get("type_code")) != _text(new.get("type_code")):
        return False, "type_mismatch"
    if _text(old.get("type_description")) != _text(new.get("type_description")):
        return False, "type_description_mismatch"
    research = dossier["research"]
    bound = (
        any(
            isinstance(section, dict) and section.get("scope") in AIRFRAME_SCOPES
            for section in research.get("sections", [])
        )
        or names_airframe(research, old)
        or names_record(research, dossier)
    )
    if not bound:
        return True, "type"
    # The same address under either registration spelling is the same aircraft; the
    # identity checks' normaliser (rules.parse_registration) decides what one spelling is.
    same_airframe = (
        _compact(old.get("icao_address")) is not None
        and _compact(old.get("icao_address")) == _compact(new.get("icao_address"))
        and rules.parse_registration(old.get("registration"))[0]
        == rules.parse_registration(new.get("registration"))[0]
    )
    if not same_airframe or dossier["observed_start"][:10] != public.utc(event["observed_start"])[:10]:
        return False, "airframe_bound"
    facts = record_facts((record or {}).get("evidence")) if isinstance(record, dict) else None
    if facts is None or dossier.get("facts") != facts:
        return False, "record_facts_changed"
    return True, "airframe_and_date"


def summarize(event):
    aircraft = event["aircraft"]
    return {
        "event_id": event["id"],
        "kind": event["kind"],
        "type_code": aircraft.get("type_code"),
        "type_description": aircraft.get("type_description"),
        "registration": aircraft.get("registration"),
        "observed_start": event["observed_start"],
        "article_path": event["article_path"],
    }


def choose_dossier(dossiers, event, record=None):
    """The one old dossier for this week that fits, with the reason when none does."""
    if not dossiers:
        return None, "no_old_dossier"
    if len(dossiers) > 1:
        return None, "ambiguous_old_dossiers"
    matched, basis = carry_match(dossiers[0], event, record)
    return (dossiers[0], basis) if matched else (None, basis)


def rebound(dossier, event, at, check_private):
    """The old dossier bound to the new event's evidence, validated and privacy-checked."""
    research = {**deepcopy(dossier["research"]), "evidence_version": event["evidence_version"]}
    if not isinstance(research.get("reason"), str) or MATCH_PHRASE.search(research["reason"]):
        research["reason"] = weekly.default_reason(event)
    research = weekly_spotlight.validate_research(research, event, as_of=at)
    check_private(json.dumps(research, ensure_ascii=False).encode())
    return research


def plan(config, *, count=13, at=None, carry_over=None):
    """Read-only: each week's state, top candidate, carry-over match and reserved cost."""
    at = public.utc(at or public.now_utc())
    state = Path(config["state_dir"])
    ledger = public._validate_ledger(public.read_json(state / "ledger.json"))
    snapshot = public.validate_snapshot(public.read_json(state / "snapshot.json"))
    status_path = state / "weekly" / "status.json"
    status = public.read_json(status_path) if status_path.exists() else {"weeks": {}}
    dossiers = old_dossiers(carry_over) if carry_over else {}
    check_private = privacy_guard(config)
    weeks, reserved = [], 0.0
    for start in requested_weeks(config, at, count):
        entry = {"week_start": start, "last_status": (status["weeks"].get(start) or {}).get("status")}
        copy = deepcopy(ledger)
        existing = weekly.existing_pick(copy, start, at=at, snapshot=snapshot)
        if public._digest(copy) != public._digest(ledger):
            entry["retires_stale_dossier"] = True
        if existing:
            entry.update(state="existing", event_id=existing["id"])
        elif not (shortlist := weekly.candidates(ledger, snapshot, start)):
            entry["state"] = "no_candidate"
        else:
            event = shortlist[0]["event"]
            entry["top_candidate"] = summarize(event)
            dossier, basis = (
                choose_dossier(dossiers.get(start, []), event, public.find_record(ledger, event["id"]))
                if carry_over
                else (None, None)
            )
            if dossier:
                try:
                    rebound(dossier, event, at, check_private)
                except (ValueError, TypeError, KeyError):
                    dossier, basis = None, "dossier_rejected"
            if dossier:
                entry.update(state="carry_over", old_record_id=dossier["record_id"], basis=basis)
            else:
                cost = weekly.request_reservation(len(weekly.request_data(event)))
                entry.update(state="research", reserved_usd=round(cost, 6))
                reserved += cost
                if carry_over:
                    entry["carry_over"] = basis
        weeks.append(entry)
    return {
        "weeks": weeks,
        "research_weeks": sum(entry["state"] == "research" for entry in weeks),
        "reserved_usd": round(reserved, 6),
        "requests": 0,
    }


def carry_over(config, path, *, count=13, at=None):
    """Attach fitting dossiers from a retired ledger to weeks without a pick. No request."""
    at = public.utc(at or public.now_utc())
    state = Path(config["state_dir"])
    directory = state / "weekly"
    ledger_path, status_path = state / "ledger.json", directory / "status.json"
    if Path(path).resolve() == ledger_path.resolve():
        raise ValueError("Carry over from the retired ledger, not the current one")
    dossiers = old_dossiers(path)
    check_private = privacy_guard(config)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    result = {"attached": 0, "existing": 0, "rejected": 0, "uncovered": [], "skipped": [], "researched": 0}
    with public.ledger_lock(status_path):
        status = public.read_json(status_path) if status_path.exists() else {"weeks": {}}
        for start in reversed(requested_weeks(config, at, count)):
            with public.ledger_lock(ledger_path):
                ledger = public.read_json(ledger_path)
                public._validate_ledger(ledger)
                snapshot = public.validate_snapshot(public.read_json(state / "snapshot.json"))
                before = public._digest(ledger)
                existing = weekly.existing_pick(ledger, start, at=at, snapshot=snapshot)
                if before != public._digest(ledger):
                    public.write_json_atomic(ledger_path, ledger, private=True)
                if existing:
                    result["existing"] += 1
                    continue
                shortlist = weekly.candidates(ledger, snapshot, start)
                dossier, basis = (
                    choose_dossier(
                        dossiers.get(start, []),
                        shortlist[0]["event"],
                        public.find_record(ledger, shortlist[0]["event"]["id"]),
                    )
                    if shortlist
                    else (None, "no_candidate")
                )
                attached = False
                if dossier:
                    candidate = shortlist[0]
                    event = candidate["event"]
                    try:
                        research = rebound(dossier, event, at, check_private)
                        attached = weekly.attach(ledger, candidate, research, at, snapshot=snapshot)
                    except (ValueError, TypeError, KeyError):
                        basis = "dossier_rejected"
                    if attached:
                        public.write_json_atomic(ledger_path, ledger, private=True)
                    else:
                        basis = basis if basis == "dossier_rejected" else "attach_refused"
                        # A matching dossier that cannot attach needs a look before research.
                        result["rejected"] += 1
            if attached:
                # Uncovered weeks keep their status, so the researcher is not delayed.
                status["weeks"][start] = {
                    "status": "attached",
                    "event_id": event["id"],
                    "evidence_version": event["evidence_version"],
                    "carried_over_from": dossier["record_id"],
                    "attached_at": at,
                    "policy_version": weekly.VERSION,
                }
                result["attached"] += 1
            else:
                result["uncovered"].append(start)
                result["skipped"].append({"week_start": start, "reason": basis})
        result["uncovered"].sort()
        result["skipped"].sort(key=lambda item: item["week_start"])
        status["carry_over"] = {"source": str(Path(path).resolve()), "at": at, "result": result}
        public.write_json_atomic(status_path, status, private=True)
    return result


def recover(path, event, check_private):
    """Recover paid work after any crash between response, validation and attach."""
    raw_path = path.with_suffix(".response.json")
    if path.exists():
        receipt = public.read_json(path)
    elif raw_path.exists():
        receipt = public.read_json(raw_path)
        receipt["research"] = weekly.dossier(receipt["response"], event, receipt["at"])
    else:
        return None
    weekly_spotlight.validate_research(receipt["research"], event, as_of=public.now_utc())
    check_private(json.dumps(receipt["research"], ensure_ascii=False).encode())
    return receipt


def run(config, *, count=13, researcher=None, at=None, budget=None):
    """Research the requested weeks' picks; AI_ENABLED, the budget store's kill switch and cap apply.

    `budget` is a one-time RunBudget (--max-spend) in place of the daily cap.
    """
    result = {"attached": 0, "existing": 0, "waiting": 0, "failed": 0}
    if not explain.writing_enabled():
        return {**result, "paused": 1}
    explain.check_budget()
    if explain.journal_paused(config["budget_database"]):
        return {**result, "paused": 1}
    if researcher is None:
        researcher = partial(weekly.research, requester=plugins.load("writer", config).send)
    at = public.utc(at or public.now_utc())
    state = Path(config["state_dir"])
    directory = state / "weekly"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    ledger_path, status_path = state / "ledger.json", directory / "status.json"
    check_private = privacy_guard(config)
    requested = requested_weeks(config, at, count)
    # This lock prevents simultaneous researchers. Network requests never hold
    # the publication ledger lock, so ordinary stories continue to publish.
    with public.ledger_lock(status_path):
        status = public.read_json(status_path) if status_path.exists() else {"weeks": {}}
        # Keep the current homepage feature ahead of a historical retry backlog.
        for start in reversed(requested):
            with public.ledger_lock(ledger_path):
                ledger = public.read_json(ledger_path)
                public._validate_ledger(ledger)
                snapshot = public.read_json(state / "snapshot.json")
                public.validate_snapshot(snapshot)
                before = public._digest(ledger)
                existing = weekly.existing_pick(ledger, start, at=public.now_utc(), snapshot=snapshot)
                if before != public._digest(ledger):
                    public.write_json_atomic(ledger_path, ledger, private=True)
            if existing:
                status["weeks"][start] = {"status": "attached", "event_id": existing["id"]}
                result["existing"] += 1
                continue
            previous = status["weeks"].get(start, {})
            last_attempt = previous.get("last_attempt")
            shortlist = weekly.candidates(ledger, snapshot, start)
            if not shortlist:
                status["weeks"][start] = {"status": "no_eligible_observation", "checked_at": at}
                result["waiting"] += 1
                continue
            candidate_index = previous.get("candidate_index", 0)
            candidate = shortlist[candidate_index % min(5, len(shortlist))]
            receipt = None
            # A cached success can be attached immediately, including after an
            # uncertain write. Do not rotate or buy text just to recover a crash.
            for option in shortlist[:5]:
                try:
                    saved = recover(
                        receipt_file(directory, start, option["event"]), option["event"], check_private
                    )
                except (OSError, ValueError, TypeError, KeyError):
                    continue
                if saved:
                    candidate, receipt = option, saved
                    break
            if (
                receipt is None
                and last_attempt
                and (public._instant(at) - public._instant(last_attempt)).total_seconds()
                < weekly.RETRY_SECONDS
            ):
                result["waiting"] += 1
                continue
            event = candidate["event"]
            receipt_path = receipt_file(directory, start, event)
            details = {
                "event_id": event["id"],
                "evidence_version": event["evidence_version"],
                "last_attempt": at,
                "attempts": previous.get("attempts", 0) + 1,
                "candidate_index": candidate_index,
                "candidate_count": len(shortlist),
                "policy_version": weekly.VERSION,
            }
            try:
                # Persist before a paid request; a crash cannot cause immediate
                # repeat spending. Completed receipts survive attachment failure.
                status["weeks"][start] = {**details, "status": "researching"}
                public.write_json_atomic(status_path, status, private=True)
                if receipt is None:
                    receipt, outcome = researcher(
                        config["budget_database"],
                        event,
                        key_file=config["weekly_key_file"],
                        save_response=lambda value, path=receipt_path: public.write_json_atomic(
                            path.with_suffix(".response.json"), value, private=True
                        ),
                        # Recent reasons' openings, so a new one does not start the same way.
                        avoid=weekly.reason_openings(snapshot),
                        **({"budget": budget} if budget is not None else {}),
                    )
                if receipt is None:
                    # A busy writer or exhausted budget is not a failed topic.
                    status["weeks"][start] = {**previous, "status": outcome}
                    result["waiting"] += 1
                    break
                research = receipt["research"]
                weekly_spotlight.validate_research(research, event, as_of=public.now_utc())
                check_private(json.dumps(research, ensure_ascii=False).encode())
                public.write_json_atomic(receipt_path, receipt, private=True)
                with public.ledger_lock(ledger_path):
                    current = public.read_json(ledger_path)
                    current_snapshot = public.read_json(state / "snapshot.json")
                    attached = weekly.attach(
                        current, candidate, research, public.now_utc(), snapshot=current_snapshot
                    )
                    if attached:
                        public.write_json_atomic(ledger_path, current, private=True)
                status["weeks"][start] = {**details, "status": "attached" if attached else "evidence_changed"}
                result["attached" if attached else "waiting"] += 1
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
                status["weeks"][start] = {
                    **details,
                    "status": "research_failed",
                    "error_type": type(error).__name__,
                    "candidate_index": candidate_index
                    + int(isinstance(error, (ValueError, TypeError, KeyError))),
                }
                result["failed"] += 1
            public.write_json_atomic(status_path, status, private=True)
        status.update(last_run=public.now_utc(), result=result)
        public.write_json_atomic(status_path, status, private=True)
    return result


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--weeks",
        type=int,
        default=13,
        help="Completed weeks to backfill; weekly_since in config takes precedence",
    )
    parser.add_argument(
        "--plan", action="store_true", help="List weeks and reserved cost; no request or write"
    )
    parser.add_argument("--carry-over-from", type=Path, metavar="LEDGER", help="A retired ledger's dossiers")
    parser.add_argument("--no-research", action="store_true", help="Make no research request")
    parser.add_argument(
        "--max-spend",
        type=float,
        metavar="USD",
        help=f"One-time budget for this run in place of the daily cap; all one-time runs together spend "
        f"at most ${explain.MAX_ONE_TIME_SPEND:g} (kept in the publisher budget store)",
    )
    args = parser.parse_args()
    if args.carry_over_from and not (args.no_research or args.plan):
        parser.error("--carry-over-from needs --no-research (or --plan)")
    if args.no_research and not args.carry_over_from and not args.plan:
        parser.error("--no-research is for --carry-over-from")
    if args.max_spend is not None and (args.plan or args.carry_over_from):
        parser.error("--max-spend is for research runs")
    os.umask(0o077)
    try:
        config = public.load_config(args.config)
        if args.plan:
            result = plan(config, count=args.weeks, carry_over=args.carry_over_from)
        elif args.carry_over_from:
            result = carry_over(config, args.carry_over_from, count=args.weeks)
        else:
            budget = explain.RunBudget(args.max_spend) if args.max_spend is not None else None
            result = run(config, count=args.weeks, budget=budget)
        print(json.dumps(result))
        if result.get("failed") or result.get("rejected"):
            raise SystemExit(1)
    except explain.BudgetError as error:
        # The settings, not a secret, so the message is safe to show.
        parser.exit(1, f"Weekly research stopped: {error}; existing articles remain available.\n")
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(
            1, f"Weekly research stopped ({type(error).__name__}); existing articles remain available.\n"
        )


if __name__ == "__main__":
    main()
