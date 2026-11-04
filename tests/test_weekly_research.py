"""Unattended research must preserve publication identity, budget and source provenance."""

import json
import os
import sqlite3
import sys
import time
import unittest
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import run_weekly
import story_fixtures
from test_public_events import editorial as story_editorial
from test_public_events import story_path
from test_weekly_spotlight import NOW, fixture

from wtf_adsb_publisher import budget_store as app
from wtf_adsb_publisher import curation, explain, reference, stories, story_facts
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher import weekly_research as worker
from wtf_adsb_publisher import weekly_spotlight as weekly

EXPLAINER = "explainer:canadian-addresses"


def ordinary():
    ledger, identifier, _, inventory = fixture()
    record = ledger["records"][0]
    source = record["editorial"].pop("weekly_source")
    source["id"] = 1
    record["editorial"].pop("weekly_spotlight")
    record["automation"] = {"source": source, "source_kind": "frozen", "released_at": NOW}
    public.set_review_state(ledger, identifier, "approved", at=NOW)
    snapshot = public.export_snapshot(ledger, mode="published", as_of=NOW, inventory=inventory)
    return ledger, snapshot


TEXTS = [
    "This synthetic source explains the design and engineering of an aircraft type without identifying a particular airframe or its flight purpose.",
    "The second synthetic source supplies historical background for this aircraft type and its variants. Its descriptions do not establish what any crew was doing during this observation.",
    "A third synthetic section repeats type background that the dossier must leave out, since the type gets one section.",
]


def response(scopes=None, texts=None, count=2):
    """A recorded research response; `scopes` gives each section the scope the current schema asks for."""
    urls = ["https://www.navy.mil/aircraft/e6/", "https://www.boeing.com/history/e6/"]
    headings = ["Design background", "Type history", "More history"]
    texts = texts or TEXTS
    sections = [
        {"heading": headings[index], "text": texts[index], "sources": [urls[min(index, 1)]]}
        for index in range(count)
    ]
    for section, scope in zip(sections, scopes or (), strict=False):
        section["scope"] = scope
    return recorded(
        {
            "sections": sections,
            "sources": [{"url": url, "title": f"Synthetic source {index}"} for index, url in enumerate(urls)],
        },
        urls,
    )


def recorded(raw, urls):
    return {
        "status": "completed",
        "usage": {"input_tokens": 1000, "output_tokens": 500},
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "action": {"type": "search", "sources": [{"url": url} for url in urls]},
            },
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(raw)}]},
        ],
    }


class WeeklyResearchTests(unittest.TestCase):
    def setUp(self):
        self.ledger, self.snapshot = ordinary()
        self.event = self.snapshot["events"][0]
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.db = self.path / "journal.sqlite3"
        app.initialize(self.db)
        self.key = self.path / "key"
        self.key.write_text("synthetic-test-key")
        # Every test runs without the network; the one real opener must never be reached.
        network = self.enterContext(patch.object(explain, "urlopen", Mock(side_effect=AssertionError)))
        self.addCleanup(lambda: self.assertFalse(network.called))

    def test_ranking_uses_the_priority_the_article_was_published_with(self):
        start = worker.weekly.week_start(self.event["observed_start"]).isoformat()
        original = curation.rate
        with patch.object(
            curation,
            "rate",
            side_effect=lambda event, **kwargs: {**original(event, **kwargs), "exceptional": True},
        ):
            ranked = worker.candidates(self.ledger, self.snapshot, start)
        self.assertTrue(ranked)
        self.assertFalse(ranked[0]["score"]["exceptional"])

    def test_completed_weeks_and_year_boundary(self):
        self.assertEqual(worker.weeks(NOW)[0], "2026-07-06")
        self.assertEqual(worker.weeks(NOW)[-1], "2026-09-28")
        self.assertEqual(worker.weeks("2027-01-01T00:00:00Z", 1), ["2026-12-21"])
        for count in (0, 53, True):
            with self.assertRaises(ValueError):
                worker.weeks(NOW, count)

    def test_request_contains_only_type_and_event_kind_and_has_hard_tool_limit(self):
        encoded = worker.request_data({**self.event, "private": "secret", "receiver": [1, 2]})
        body = json.loads(encoded)
        # Weekly research uses the public model, as type research does.
        self.assertEqual(body["model"], explain.PUBLIC_MODEL)
        self.assertEqual(body["max_tool_calls"], 1)
        self.assertEqual(body["tool_choice"], "required")
        self.assertFalse(body["store"])
        data = json.loads(body["input"])
        self.assertEqual(set(data), {"aircraft_type", "event"})
        # The event is its kind and headline readings: no identity, time, or place.
        self.assertEqual(
            data["event"],
            {
                "kind": "watch_type",
                "kind_name": "Watched aircraft type",
                "causeless": False,
                "measurements": [{"label": "Ground speed", "value": 70.0, "unit": "kn"}],
            },
        )
        for private in (
            self.event["aircraft"]["registration"],
            self.event["aircraft"]["icao_address"],
            self.event["observed_start"],
            self.event["title"],
            "secret",
        ):
            self.assertNotIn(private, body["input"])

    def test_instructions_explain_the_event_and_give_the_type_one_sentence(self):
        paragraphs = [" ".join(paragraph.split()) for paragraph in worker.INSTRUCTIONS.split("\n\n")]
        self.assertIn("explain the event", paragraphs[0].lower())
        self.assertIn("at most one sentence", paragraphs[1])
        # The fact and source rules stay.
        for rule in ("ONE search call", "Paraphrase; do not quote", "Never fill gaps", "serial comma"):
            self.assertIn(rule, worker.INSTRUCTIONS)
        self.assertNotRegex(worker.INSTRUCTIONS, "[\u2014&]")
        schema = worker.SCHEMA["properties"]["sections"]["items"]
        self.assertEqual(schema["properties"]["scope"]["enum"], ["event", "type"])
        self.assertIn("scope", schema["required"])

    def test_event_sections_lead_and_the_type_keeps_one_sentence(self):
        data = response(
            scopes=["event", "type"],
            texts=[
                "A synthetic explanation of this kind of observation: how the reported readings arise and why these readings stand apart from the usual case.",
                "This synthetic aircraft type first flew decades ago and is described by its maker. A second sentence about its history must not reach the dossier.",
            ],
        )
        result = worker.dossier(data, self.event, NOW)
        self.assertEqual(
            [section["scope"] for section in result["sections"]], ["event_background", "type_background"]
        )
        self.assertEqual(
            result["sections"][1]["text"],
            "This synthetic aircraft type first flew decades ago and is described by its maker.",
        )
        self.assertEqual(
            [source["applies_to"] for source in result["sources"]],
            [
                "Background on this kind of event; not this airframe or flight.",
                "Aircraft type background; not this airframe or flight.",
            ],
        )
        self.assertIn("this kind of event", result["limitations"][0])
        with self.assertRaisesRegex(ValueError, "explain the event"):
            worker.dossier(response(scopes=["type", "type"]), self.event, NOW)
        self.assertEqual(
            [
                section["heading"]
                for section in worker.dossier(
                    response(scopes=["event", "type", "type"], count=3), self.event, NOW
                )["sections"]
            ],
            ["Design background", "Type history"],
        )

    def test_event_sections_pass_the_prose_checks_before_they_are_saved(self):
        event = "This synthetic explanation describes this kind of observation and how such readings are reported to the ground."
        dossier = worker.dossier(response(scopes=["event", "event"], texts=[event, event]), self.event, NOW)
        self.assertEqual(len(dossier["sections"]), 2)
        mismatch = {**self.event, "kind": "type_speed_mismatch"}
        for text, rule in (
            (event.replace("observation and", "observation \u2014"), "dash"),
            (event + " The format dates from 1993.", "Unsupported number"),
            (event + " A stale registry entry pairs the address with another type.", "without a hedge"),
            (event + " The reading was a decoding glitch.", "Unlisted cause"),
        ):
            with self.subTest(rule=rule), self.assertRaisesRegex(ValueError, rule):
                worker.dossier(response(scopes=["event", "event"], texts=[event, text]), mismatch, NOW)
        # The headline readings may be quoted, and a reading given as text is no number.
        quoted = event + " This one reported a ground speed of 70 knots."
        grounded = deepcopy(self.event)
        grounded["measurements"].append(
            {"key": "alt_baro", "label": "Pressure altitude", "value": "ground", "unit": "ft"}
        )
        self.assertIn("alt_baro", grounded["headline_measurements"])
        for subject in (self.event, grounded):
            worker.dossier(response(scopes=["event", "event"], texts=[event, quoted]), subject, NOW)
        # The type sentence keeps the dash and ampersand rule too.
        typed = "This synthetic aircraft type \u2014 built by its maker for several decades \u2014 is described by the second source."
        with self.assertRaisesRegex(ValueError, "dash or an ampersand"):
            worker.dossier(response(scopes=["event", "type"], texts=[event, typed]), self.event, NOW)

    def test_candidates_carry_the_rarity_of_their_kind(self):
        start = worker.weekly.week_start(self.event["observed_start"]).isoformat()
        ranked = worker.candidates(self.ledger, self.snapshot, start)
        self.assertEqual([row["rarity"] for row in ranked], [0.5])
        with patch.object(worker.weekly, "rank", side_effect=lambda rows, **options: list(rows)) as rank:
            worker.candidates(self.ledger, self.snapshot, start)
        rank.assert_called_once()

    def test_unconsulted_and_private_sources_rejected(self):
        for url in (
            "https://example.net/fake/",
            "https://navy.mil.attacker.example/x",
            "https://user@navy.mil/x",
            "http://navy.mil/x",
            "https://127.0.0.1/x",
            "https://navy.mil:8443/x",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                worker.source_url(url)
        bad = response()
        bad["output"][0]["action"]["sources"] = []
        with self.assertRaises(ValueError):
            worker.dossier(bad, self.event, NOW)
        result = worker.dossier(response(), self.event, NOW)
        self.assertTrue(all(source["primary"] for source in result["sources"]))
        self.assertTrue(all(section["scope"] == "type_background" for section in result["sections"]))

    def test_missing_search_and_incomplete_responses_rejected(self):
        for change in (
            lambda item: item.update(status="incomplete"),
            lambda item: item["output"].pop(0),
            lambda item: item["output"].insert(0, deepcopy(item["output"][0])),
        ):
            data = response()
            change(data)
            with self.assertRaises(ValueError):
                worker.dossier(data, self.event, NOW)

    def test_only_released_current_eligible_evidence_is_nominated(self):
        self.assertEqual(len(worker.candidates(self.ledger, self.snapshot, "2026-09-28")), 1)
        for modify in (
            lambda row: row.update(state="withdrawn"),
            lambda row: row.update(revision=row["revision"] + 1),
            lambda row: row["automation"]["source"].update(verdict="routine"),
            lambda row: row["automation"].pop("source"),
        ):
            ledger = deepcopy(self.ledger)
            modify(ledger["records"][0])
            self.assertEqual(worker.candidates(ledger, self.snapshot, "2026-09-28"), [])

    def test_attach_preserves_identity_and_cannot_duplicate_week(self):
        candidate = worker.candidates(self.ledger, self.snapshot, "2026-09-28")[0]
        dossier = worker.dossier(response(), self.event, NOW)
        old = deepcopy(self.ledger["records"][0])
        self.assertTrue(worker.attach(self.ledger, candidate, dossier, NOW))
        new = self.ledger["records"][0]
        for key in ("id", "episode_key", "evidence"):
            self.assertEqual(new[key], old[key])
        self.assertEqual(new["editorial"]["article_path"], old["editorial"]["article_path"])
        self.assertEqual(new["revision"], old["revision"] + 1)
        self.assertEqual(new["state"], "pending")
        self.assertFalse(worker.attach(self.ledger, candidate, dossier, NOW))

    def test_evidence_change_or_withdrawal_during_research_blocks_attach(self):
        candidate = worker.candidates(self.ledger, self.snapshot, "2026-09-28")[0]
        dossier = worker.dossier(response(), self.event, NOW)
        for modify in (
            lambda row: row.update(revision=2),
            lambda row: row.update(state="withdrawn"),
            lambda row: row["automation"]["source"].update(verdict="bad_data"),
        ):
            ledger = deepcopy(self.ledger)
            modify(ledger["records"][0])
            self.assertFalse(worker.attach(ledger, candidate, dossier, NOW))

    def test_budget_reserves_search_and_reports_actual_tool_cost(self):
        reserved = []

        def requester(encoded, key):
            with closing(sqlite3.connect(self.db)) as db, db:
                reserved.append(explain.spent_today(db, time.time()))
            # The reservation covers this request's own bytes, not the size limit, plus a full search.
            self.assertAlmostEqual(reserved[0], worker.request_reservation(len(encoded)))
            self.assertLess(reserved[0], worker.request_reservation())
            self.assertGreaterEqual(reserved[0], explain.search_reservation(worker.PRICES))
            self.assertEqual(key, "synthetic-test-key")
            return response()

        receipt, state = worker.research(self.db, self.event, key_file=self.key, requester=requester)
        self.assertEqual(state, "researched")
        self.assertEqual(receipt["research"]["evidence_version"], self.event["evidence_version"])
        with closing(sqlite3.connect(self.db)) as db, db:
            cost, kept, purpose = db.execute(
                "SELECT estimated_cost,reserved_cost,purpose FROM ai_runs"
            ).fetchone()
        prices = explain.PUBLIC_PRICES
        self.assertAlmostEqual(cost, explain.SEARCH_COST + 1000 * prices[0] + 500 * prices[1])
        self.assertEqual((kept, purpose), (0, "public"))

    def test_budget_exhaustion_or_writer_lock_never_calls_api(self):
        requester = Mock(side_effect=AssertionError("Unexpected paid request"))
        with patch.object(explain, "DAILY_BUDGET", 0):
            self.assertEqual(
                worker.research(self.db, self.event, key_file=self.key, requester=requester),
                (None, "budget_exhausted"),
            )
        with explain.writer_lock(self.db), patch.object(explain.time, "sleep"):
            self.assertEqual(
                worker.research(self.db, self.event, key_file=self.key, requester=requester),
                (None, "writer_busy"),
            )
        requester.assert_not_called()

    def test_public_research_may_use_the_reserve_private_notes_cannot(self):
        with app.connect(self.db) as db:
            explain.record_purposes(db)
            db.execute(
                "INSERT INTO ai_runs(started_at,status,estimated_cost,reserved_cost,purpose) VALUES (?,?,?,0,?)",
                (time.time(), "complete", explain.private_cap(), "private"),
            )
        receipt, state = worker.research(
            self.db, self.event, key_file=self.key, requester=lambda *_: response()
        )
        self.assertEqual(state, "researched")
        with app.connect(self.db) as db:
            self.assertIsNone(explain.reserve_run(db, time.time(), 1))

    def test_reasons_avoid_match_phrasing_and_recent_openings(self):
        data = json.loads(json.loads(worker.request_data(self.event, avoid=["The Mercury flew as"]))["input"])
        self.assertEqual(data["avoid_reason_openings"], ["The Mercury flew as"])
        self.assertIn("Do not call the sighting a match", worker.INSTRUCTIONS)
        silent = response()
        content = json.loads(silent["output"][1]["content"][0]["text"])
        self.assertNotIn("reason", content)
        dossier = worker.dossier(silent, self.event, NOW)
        self.assertNotIn("match", dossier["reason"])
        picks = {
            "events": [
                {
                    "weekly_spotlight": {
                        "week_start": "2026-09-28",
                        "reason": "Boeing built the E-6 on the 707 line for the Navy.",
                    }
                }
            ]
        }
        self.assertEqual(worker.reason_openings(picks), ["Boeing built the E-6 on the"])

    def test_ranking_uses_the_rarity_saved_at_selection(self):
        start = worker.weekly.week_start(self.event["observed_start"]).isoformat()
        self.ledger["records"][0]["automation"]["rarity"] = 9
        rates = []
        original = curation.rate

        def rate(event, **options):
            rates.append(options)
            return original(event)

        with patch.object(curation, "rate", side_effect=rate):
            worker.candidates(self.ledger, self.snapshot, start)
        self.assertEqual(rates, [{"rarity": 9}])

    def test_research_waits_briefly_for_another_writer_then_leaves(self):
        requester = Mock(side_effect=AssertionError("Unexpected paid request"))
        with (
            patch.object(explain, "LOCK_ATTEMPTS", 3),
            patch.object(explain, "LOCK_WAIT_SECONDS", 0),
            patch.object(explain.time, "sleep") as sleep,
            explain.writer_lock(self.db),
        ):
            self.assertEqual(
                worker.research(self.db, self.event, key_file=self.key, requester=requester),
                (None, "writer_busy"),
            )
        self.assertEqual(sleep.call_count, 2)
        requester.assert_not_called()

    def test_weekly_research_honours_the_ai_switches_and_the_budget_settings(self):
        config = self.config()
        researcher = Mock(side_effect=AssertionError("Unexpected paid request"))
        with patch.dict(os.environ, {"AI_ENABLED": "false"}):
            self.assertEqual(run_weekly.run(config, count=1, researcher=researcher, at=NOW)["paused"], 1)
        with app.connect(self.db) as db:
            explain.set_paused(db, True)
        self.assertEqual(run_weekly.run(config, count=1, researcher=researcher, at=NOW)["paused"], 1)
        with app.connect(self.db) as db:
            explain.set_paused(db, False)
        with patch.object(explain, "PUBLIC_RESERVE", 0.10), self.assertRaises(explain.BudgetError):
            run_weekly.run(config, count=1, researcher=researcher, at=NOW)
        researcher.assert_not_called()
        self.assertFalse((self.path / "weekly").exists())

    def test_research_avoids_the_openings_of_recent_reasons(self):
        config = self.config()
        snapshot = deepcopy(self.snapshot)
        researcher = Mock(
            return_value=({"research": worker.dossier(response(), self.event, NOW)}, "researched")
        )
        with patch.object(worker, "reason_openings", return_value=["The Mercury flew as"]) as openings:
            run_weekly.run(config, count=1, researcher=researcher, at=NOW)
        self.assertEqual(openings.call_args.args[0]["events"], snapshot["events"])
        self.assertEqual(researcher.call_args.kwargs["avoid"], ["The Mercury flew as"])

    def test_uncertain_request_keeps_reservation(self):
        with self.assertRaises(OSError):
            worker.research(
                self.db, self.event, key_file=self.key, requester=Mock(side_effect=OSError("timeout"))
            )
        with closing(sqlite3.connect(self.db)) as db, db:
            state, reserved = db.execute("SELECT status,reserved_cost FROM ai_runs").fetchone()
        self.assertEqual(state, "failed")
        self.assertGreaterEqual(reserved, explain.search_reservation(worker.PRICES))

    def config(self):
        public.write_json_atomic(self.path / "ledger.json", self.ledger, private=True)
        public.write_json_atomic(self.path / "snapshot.json", self.snapshot, private=True)
        return {
            "state_dir": str(self.path),
            "budget_database": str(self.db),
            "weekly_key_file": str(self.key),
        }

    def test_scheduler_is_idempotent_and_preserves_cached_research(self):
        config = self.config()
        researcher = Mock(
            return_value=({"research": worker.dossier(response(), self.event, NOW)}, "researched")
        )
        first = run_weekly.run(config, count=1, researcher=researcher, at=NOW)
        second = run_weekly.run(config, count=1, researcher=researcher, at=NOW)
        self.assertEqual(first["attached"], 1)
        self.assertEqual(second["existing"], 1)
        self.assertEqual(researcher.call_count, 1)
        saved = public.read_json(self.path / "ledger.json")
        self.assertEqual(len(saved["records"]), 1)

    def test_a_one_time_budget_reaches_the_researcher(self):
        config = self.config()
        researcher = Mock(
            return_value=({"research": worker.dossier(response(), self.event, NOW)}, "researched")
        )
        budget = explain.RunBudget(5)
        self.assertEqual(
            run_weekly.run(config, count=1, researcher=researcher, at=NOW, budget=budget)["attached"], 1
        )
        self.assertIs(researcher.call_args.kwargs["budget"], budget)

    def test_configured_backfill_start_covers_thirteen_completed_weeks(self):
        config = {**self.config(), "weekly_since": "2026-07-06"}
        researcher = Mock(
            return_value=({"research": worker.dossier(response(), self.event, NOW)}, "researched")
        )
        result = run_weekly.run(config, count=1, researcher=researcher, at=NOW)
        self.assertEqual(result["attached"], 1)
        status = public.read_json(self.path / "weekly/status.json")
        self.assertEqual(sorted(status["weeks"]), worker.weeks(NOW, 13))
        with self.assertRaises(ValueError):
            run_weekly.run({**config, "weekly_since": "2026-07-07"}, researcher=researcher, at=NOW)

    def test_scheduler_failures_wait_a_day_and_leave_article_untouched(self):
        config = self.config()
        researcher = Mock(side_effect=ValueError("unusable research"))
        first = run_weekly.run(config, count=1, researcher=researcher, at=NOW)
        second = run_weekly.run(config, count=1, researcher=researcher, at=NOW)
        self.assertEqual(first["failed"], 1)
        self.assertEqual(second["waiting"], 1)
        self.assertEqual(researcher.call_count, 1)
        self.assertEqual(public.read_json(self.path / "ledger.json"), self.ledger)

    def test_scheduler_privacy_guard_rejects_research_before_attach(self):
        config = {**self.config(), "private_terms": ["synthetic source"]}
        researcher = Mock(
            return_value=({"research": worker.dossier(response(), self.event, NOW)}, "researched")
        )
        self.assertEqual(run_weekly.run(config, count=1, researcher=researcher, at=NOW)["failed"], 1)
        self.assertEqual(public.read_json(self.path / "ledger.json"), self.ledger)

    def test_block_or_suspension_before_and_during_research(self):
        candidate = worker.candidates(self.ledger, self.snapshot, "2026-09-28")[0]
        dossier = worker.dossier(response(), self.event, NOW)
        for name in ("blocked", "suspended"):
            ledger = deepcopy(self.ledger)
            ledger["records"][0]["automation"][name] = True
            self.assertEqual(worker.candidates(ledger, self.snapshot, "2026-09-28"), [])
            self.assertFalse(worker.attach(ledger, candidate, dossier, NOW))

    def test_unusable_weekly_pick_is_retired_with_history(self):
        candidate = worker.candidates(self.ledger, self.snapshot, "2026-09-28")[0]
        worker.attach(self.ledger, candidate, worker.dossier(response(), self.event, NOW), NOW)
        self.ledger["records"][0]["automation"]["blocked"] = "ineligible_source"
        self.assertIsNone(worker.existing_pick(self.ledger, "2026-09-28", at=NOW))
        record = self.ledger["records"][0]
        self.assertNotIn("weekly_spotlight", record["editorial"])
        self.assertIn("weekly_spotlight", record["history"][-1]["editorial"])

    def test_manual_published_dossier_keeps_its_slot(self):
        ledger, identifier, _, inventory = fixture()
        snapshot = public.export_snapshot(ledger, mode="published", as_of=NOW, inventory=inventory)
        before = deepcopy(ledger)
        self.assertEqual(
            worker.existing_pick(ledger, "2026-09-28", at=NOW, snapshot=snapshot)["id"], identifier
        )
        self.assertEqual(ledger, before)

    def test_published_predecessor_keeps_slot_during_pending_removal(self):
        ledger, identifier, _, inventory = fixture()
        record = ledger["records"][0]
        metadata = {
            key: value
            for key, value in record["editorial"].items()
            if key not in {"weekly_source", "weekly_spotlight"}
        }
        public.put_event(ledger, record["episode_key"], record["evidence"], metadata, at=NOW)
        snapshot = public.export_snapshot(ledger, mode="published", as_of=NOW, inventory=inventory)
        self.assertIn("weekly_spotlight", snapshot["events"][0])
        before = deepcopy(ledger)
        self.assertEqual(
            worker.existing_pick(ledger, "2026-09-28", at=NOW, snapshot=snapshot)["id"], identifier
        )
        self.assertEqual(ledger, before)

    def test_owner_unpublished_pick_frees_week_without_restoring_article(self):
        candidate = worker.candidates(self.ledger, self.snapshot, "2026-09-28")[0]
        worker.attach(self.ledger, candidate, worker.dossier(response(), self.event, NOW), NOW)
        public.set_review_state(self.ledger, self.event["id"], "approved", at=NOW)
        missing = {"events": [], "as_of_utc": "2026-10-06T15:00:00Z"}
        self.assertIsNone(
            worker.existing_pick(self.ledger, "2026-09-28", at=missing["as_of_utc"], snapshot=missing)
        )
        record = self.ledger["records"][0]
        self.assertNotIn("weekly_spotlight", record["editorial"])
        self.assertNotIn("suspended", record["automation"])
        self.assertEqual(record["automation"]["released_at"], NOW)

    def test_pending_dossier_waits_only_until_publisher_has_checked_it(self):
        candidate = worker.candidates(self.ledger, self.snapshot, "2026-09-28")[0]
        worker.attach(self.ledger, candidate, worker.dossier(response(), self.event, NOW), NOW)
        missing = {"events": [], "as_of_utc": "2026-10-05T14:00:00Z"}
        self.assertIsNotNone(worker.existing_pick(self.ledger, "2026-09-28", at=NOW, snapshot=missing))
        missing["as_of_utc"] = "2026-10-06T15:00:00Z"
        self.assertIsNone(
            worker.existing_pick(self.ledger, "2026-09-28", at=missing["as_of_utc"], snapshot=missing)
        )

    def test_unknown_type_is_rejected_before_reserving_budget(self):
        event = deepcopy(self.event)
        event["aircraft"].pop("type_code", None)
        event["aircraft"].pop("type_description", None)
        requester = Mock()
        with self.assertRaises(ValueError):
            worker.research(self.db, event, key_file=self.key, requester=requester)
        requester.assert_not_called()
        with app.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM ai_runs").fetchone()[0], 0)

    def test_malformed_response_containers_fail_closed(self):
        malformed = [
            None,
            {"output": [None]},
            {"output": [{"type": "message", "content": None}]},
            {"output": [{"type": "web_search_call", "action": None}]},
        ]
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    worker.research(
                        self.db, self.event, key_file=self.key, requester=Mock(return_value=value)
                    )
                # The answer was paid for and reported no usage, so its run keeps the reservation.
                with app.connect(self.db) as db:
                    self.assertGreater(db.execute("SELECT reserved_cost FROM ai_runs").fetchone()[0], 0)
                    # Two kept research reservations fill the day, so each value starts afresh.
                    db.execute("DELETE FROM ai_runs")
        with self.assertRaises(ValueError):
            worker.source_url(123)

    def test_source_ids_and_tracking_parameters_match_consulted_sources(self):
        data = response()
        raw = json.loads(data["output"][1]["content"][0]["text"])
        for index, source in enumerate(raw["sources"], 1):
            source["id"] = f"source-{index}"
            source["url"] += "?utm_source=chatgpt.com"
            raw["sections"][index - 1]["sources"] = [source["id"]]
        data["output"][1]["content"][0]["text"] = json.dumps(raw)
        self.assertEqual(len(worker.dossier(data, self.event, NOW)["sources"]), 2)

    def test_pdf_download_variants_cannot_count_as_two_sources(self):
        data = response()
        raw = json.loads(data["output"][1]["content"][0]["text"])
        urls = [
            "https://www.nasa.gov/history.pdf?pubDate=20260101",
            "https://www.nasa.gov/history.pdf?pubDate=20260202",
        ]
        for index, source in enumerate(raw["sources"]):
            source["url"] = urls[index]
            raw["sections"][index]["sources"] = [urls[index]]
        data["output"][0]["action"]["sources"] = [{"url": url} for url in urls]
        data["output"][1]["content"][0]["text"] = json.dumps(raw)
        with self.assertRaises(ValueError):
            worker.dossier(data, self.event, NOW)

    def test_duplicate_source_ids_merge_without_losing_section_citations(self):
        data = response()
        raw = json.loads(data["output"][1]["content"][0]["text"])
        raw["sources"].append(deepcopy(raw["sources"][0]))
        for index, source in enumerate(raw["sources"], 1):
            source["id"] = f"source-{index}"
        raw["sections"][0]["sources"] = ["source-3"]
        raw["sections"][1]["sources"] = ["source-2"]
        data["output"][1]["content"][0]["text"] = json.dumps(raw)
        dossier = worker.dossier(data, self.event, NOW)
        self.assertEqual(len(dossier["sources"]), 2)
        self.assertEqual(dossier["sections"][0]["sources"], ["source-1"])

    def test_crash_after_raw_response_recovers_without_spending_or_waiting(self):
        config = self.config()
        directory = self.path / "weekly"
        directory.mkdir()
        public.write_json_atomic(
            directory / "status.json",
            {"weeks": {"2026-09-28": {"status": "researching", "attempts": 1, "last_attempt": NOW}}},
            private=True,
        )
        path = run_weekly.receipt_file(directory, "2026-09-28", self.event)
        public.write_json_atomic(
            path.with_suffix(".response.json"), {"response": response(), "at": NOW}, private=True
        )
        candidates = worker.candidates(self.ledger, self.snapshot, "2026-09-28")
        second = deepcopy(candidates[0])
        second["event"]["id"] = "second-should-not-be-researched"
        researcher = Mock(side_effect=AssertionError("Duplicate paid call"))
        with patch.object(worker, "candidates", return_value=candidates + [second]):
            self.assertEqual(run_weekly.run(config, count=1, researcher=researcher, at=NOW)["attached"], 1)
        researcher.assert_not_called()

    def test_transient_failure_retries_the_same_top_candidate(self):
        config = self.config()
        candidates = worker.candidates(self.ledger, self.snapshot, "2026-09-28")
        second = deepcopy(candidates[0])
        second["event"]["id"] = "ordinary-fallback"
        researcher = Mock(side_effect=OSError("Timeout"))
        with patch.object(worker, "candidates", return_value=candidates + [second]):
            run_weekly.run(config, count=1, researcher=researcher, at=NOW)
            run_weekly.run(config, count=1, researcher=researcher, at="2026-10-06T15:00:00Z")
        self.assertEqual(researcher.call_count, 2)
        self.assertEqual([call.args[1]["id"] for call in researcher.call_args_list], [self.event["id"]] * 2)

    def test_a_failing_fresh_kind_rotates_to_a_demoted_recent_kind(self):
        config = self.config()
        fresh = worker.candidates(self.ledger, self.snapshot, "2026-09-28")[0]
        recent = deepcopy(fresh)
        recent["event"].update(id="recent-kind", kind="tcas_ra")
        ranked = worker.weekly.rank([recent, fresh], picked={"tcas_ra"})
        self.assertEqual([row["event"]["id"] for row in ranked], [fresh["event"]["id"], "recent-kind"])
        researcher = Mock(side_effect=ValueError("unusable research"))
        with patch.object(worker, "candidates", return_value=ranked):
            run_weekly.run(config, count=1, researcher=researcher, at=NOW)
            run_weekly.run(config, count=1, researcher=researcher, at="2026-10-06T15:00:00Z")
        self.assertEqual(
            [call.args[1]["id"] for call in researcher.call_args_list], [fresh["event"]["id"], "recent-kind"]
        )

    def test_instructions_name_the_refused_words_and_the_hedge(self):
        for instructions in (worker.INSTRUCTIONS, worker.EXPLAINER_INSTRUCTIONS):
            flat = " ".join(instructions.split())
            for words in (
                "decode or decoding",
                "glitch",
                "multipath",
                '"could" or "may"',
                '"possible explanations include" is not enough',
            ):
                self.assertIn(words, flat)
        self.assertIn("Write every heading in sentence case", " ".join(worker.EXPLAINER_INSTRUCTIONS.split()))

    def test_research_connections_close_after_success(self):
        opened = []

        original = app.connect

        def connect(path):
            db = original(path)
            opened.append(db)
            return db

        with patch.object(app, "connect", side_effect=connect):
            worker.research(self.db, self.event, key_file=self.key, requester=Mock(return_value=response()))
        self.assertEqual(len(opened), 2)
        for db in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")


def canada(address):
    """The allocation lookup for these tests: Canada's block, and the United States elsewhere."""
    if 0xC00000 <= int(address, 16) <= 0xC3FFFF:
        return {"country": "CA", "name": "Canada"}
    return {"country": "US", "name": "United States"}


def add_mismatches(ledger, addresses, *, state="approved", first=0):
    """Mismatch stories, alternating speed and altitude, one per address."""
    for index, address in enumerate(addresses, first):
        kind = ("type_speed_mismatch", "type_altitude_mismatch")[index % 2]
        raw = story_fixtures.kind_event(kind)
        raw["hex"] = raw["evidence"]["aircraft"]["hex"] = address
        identifier = public.put_event(
            ledger,
            f"mismatch-{index}",
            stories.evidence_packet(raw),
            story_editorial(path=story_path(f"mismatch-{index}")),
            at=NOW,
        )
        if state != "pending":
            public.set_review_state(ledger, identifier, state, at=NOW)
        if state == "approved":
            # A published story: the publisher records its release beside the approval.
            record = next(item for item in ledger["records"] if item["id"] == identifier)
            record.setdefault("automation", {})["released_at"] = NOW
    return ledger


BRIEF = {
    "stories": 3,
    "registries": ["adsbdb", "hexdb"],
    "hypotheses": list(story_facts.MISMATCH_HYPOTHESES),
}


def explainer_response():
    """A recorded explainer response: two consulted official pages, two sections, and a reason."""
    urls = [
        "https://www.icao.int/airnavigation/aircraft-addresses/",
        "https://www.canada.ca/en/transport-canada/aircraft-registry.html",
    ]
    return recorded(
        {
            "reason": "ICAO assigns each State a block of 24-bit aircraft addresses, and Canada's registry ties one to each registered airframe.",
            "sections": [
                {
                    "heading": "How an address reaches an airframe",
                    "text": "This synthetic section explains that ICAO gives each State a block of 24-bit addresses and that the national registry assigns one to each registered aircraft.",
                    "sources": ["source-1"],
                },
                {
                    "heading": "When the registry and the broadcasts disagree",
                    "text": "This synthetic section explains how a stale registry entry, a re-registration, a transponder set to the wrong address, or a database typo could each pair an address with the wrong type.",
                    "sources": ["source-2"],
                },
            ],
            "sources": [
                {"id": f"source-{index}", "url": url, "title": f"Synthetic page {index}"}
                for index, url in enumerate(urls, 1)
            ],
        },
        urls,
    )


class ExplainerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(reference, "allocation", side_effect=canada))
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.db = self.path / "journal.sqlite3"
        app.initialize(self.db)
        self.key = self.path / "key"
        self.key.write_text("synthetic-test-key")
        network = self.enterContext(patch.object(explain, "urlopen", Mock(side_effect=AssertionError)))
        self.addCleanup(lambda: self.assertFalse(network.called))

    def page(self):
        return worker.explainer(explainer_response(), EXPLAINER, BRIEF, NOW)

    def test_explainer_waits_for_three_canadian_mismatch_stories(self):
        ledger = add_mismatches(public.new_ledger("2026-09-01"), ["c01234", "c3fffe", "a01236"])
        add_mismatches(ledger, ["c01237"], state="withdrawn", first=3)
        self.assertIsNone(worker.explainer_brief(ledger, EXPLAINER))
        # A pending story may never be published, so it does not count.
        add_mismatches(ledger, ["c20000"], state="pending", first=4)
        self.assertIsNone(worker.explainer_brief(ledger, EXPLAINER))
        add_mismatches(ledger, ["c20001"], first=5)
        self.assertEqual(worker.explainer_brief(ledger, EXPLAINER), BRIEF)
        self.assertTrue(worker.add_explainer(ledger, self.page()))
        self.assertFalse(worker.add_explainer(ledger, self.page()))
        self.assertIsNone(worker.explainer_brief(ledger, EXPLAINER))
        public._validate_ledger(ledger)

    def test_one_off_sends_only_the_facts_and_writes_one_page(self):
        requester = Mock(return_value=explainer_response())
        receipt, outcome = worker.one_off(self.db, EXPLAINER, BRIEF, key_file=self.key, requester=requester)
        self.assertEqual(outcome, "researched")
        body = json.loads(requester.call_args.args[0])
        self.assertEqual((body["max_tool_calls"], body["tool_choice"]), (1, "required"))
        data = json.loads(body["input"])
        self.assertEqual(data["stories"], 3)
        self.assertEqual(data["registries"], ["adsbdb", "hexdb"])
        self.assertEqual(data["hypotheses"], list(story_facts.MISMATCH_HYPOTHESES))
        self.assertEqual(data["allocation"], "Canada")
        self.assertIn(worker.EXPLAINER_INSTRUCTIONS, body["instructions"])
        self.assertIn("explain the event", worker.EXPLAINER_INSTRUCTIONS)
        self.assertNotRegex(worker.EXPLAINER_INSTRUCTIONS, "[\u2014&]")
        page = receipt["page"]
        self.assertEqual(receipt["brief"], BRIEF)
        self.assertEqual(page["key"], EXPLAINER)
        self.assertEqual(page["path"], "/explainers/canadian-addresses/")
        self.assertEqual(page["facts"], BRIEF)
        self.assertEqual(len(page["sections"]), 2)
        self.assertTrue(page["summary"].startswith("ICAO assigns"))
        self.assertEqual(weekly.validate_explainer(page, as_of=receipt["at"]), page)
        for change in (
            lambda item: item.update(path="/explainers/other/"),
            lambda item: item.update(key="explainer:other"),
            lambda item: item["facts"].update(stories=2),
            lambda item: item["facts"].update(registries=["opensky"]),
            lambda item: item["facts"].update(hypotheses=["a guess about the flight"]),
            lambda item: item["sections"].pop(),
            lambda item: item.update(written_at="2027-01-01T00:00:00Z"),
        ):
            broken = deepcopy(page)
            change(broken)
            with self.subTest(change=change), self.assertRaises(ValueError):
                weekly.validate_explainer(broken, as_of=receipt["at"])

    def test_explainer_prose_passes_the_checks_before_it_is_saved(self):
        good = explainer_response()
        text = json.loads(good["output"][1]["content"][0]["text"])["sections"][1]["text"]
        for change, rule in (
            (text.replace("explains how", "explains \u2014 how"), "dash"),
            (text.replace(", or a database typo", " & a database typo"), "dash or an ampersand"),
            (text.replace("could each pair", "each pair"), "without a hedge"),
            (text + " The reading was a decoding glitch.", "Unlisted cause"),
            (text + " Seven registries were checked.", "Unsupported number"),
            (text + " One such address belonged to a Boeing near Toronto.", "Unlisted name"),
            (text + " Registration C-GABC is one example.", "Unlisted name"),
        ):
            data = json.loads(good["output"][1]["content"][0]["text"])
            data["sections"][1]["text"] = change
            broken = deepcopy(good)
            broken["output"][1]["content"][0]["text"] = json.dumps(data)
            with self.subTest(rule=rule), self.assertRaisesRegex(ValueError, rule):
                worker.explainer(broken, EXPLAINER, BRIEF, NOW)

    def test_run_writes_the_explainer_once_and_records_its_key(self):
        ledger, snapshot = ordinary()
        add_mismatches(ledger, ["c01234", "c01235", "c01236"])
        public.write_json_atomic(self.path / "ledger.json", ledger, private=True)
        public.write_json_atomic(self.path / "snapshot.json", snapshot, private=True)
        config = {
            "state_dir": str(self.path),
            "budget_database": str(self.db),
            "weekly_key_file": str(self.key),
        }
        researcher = Mock(
            return_value=({"research": worker.dossier(response(), snapshot["events"][0], NOW)}, "researched")
        )
        writer = Mock(return_value=({"page": self.page()}, "researched"))
        planned = run_weekly.plan(config, count=1, at=NOW)["explainers"]
        self.assertEqual([(entry["key"], entry["state"]) for entry in planned], [(EXPLAINER, "research")])
        self.assertGreater(planned[0]["reserved_usd"], 0)
        first = run_weekly.run(config, count=1, researcher=researcher, explainer=writer, at=NOW)
        second = run_weekly.run(config, count=1, researcher=researcher, explainer=writer, at=NOW)
        self.assertEqual(run_weekly.plan(config, count=1, at=NOW)["explainers"][0]["state"], "written")
        self.assertEqual((first["explainers"], second["explainers"]), (1, 0))
        writer.assert_called_once()
        self.assertEqual(writer.call_args.args[1:], (EXPLAINER, BRIEF))
        saved = public.read_json(self.path / "ledger.json")
        self.assertEqual(list(saved["explainers"]), [EXPLAINER])
        status = public.read_json(self.path / "weekly/status.json")
        self.assertEqual(status["explainers"][EXPLAINER]["status"], "written")

    def test_crash_after_the_raw_explainer_response_recovers_without_spending(self):
        ledger, snapshot = ordinary()
        # A fourth story arrived after the request; the page keeps the count it was written from.
        add_mismatches(ledger, ["c01234", "c01235", "c01236", "c01237"])
        public.write_json_atomic(self.path / "ledger.json", ledger, private=True)
        public.write_json_atomic(self.path / "snapshot.json", snapshot, private=True)
        config = {
            "state_dir": str(self.path),
            "budget_database": str(self.db),
            "weekly_key_file": str(self.key),
        }
        directory = self.path / "weekly"
        directory.mkdir()
        public.write_json_atomic(
            directory / "status.json",
            {"weeks": {}, "explainers": {EXPLAINER: {"status": "researching", "last_attempt": NOW}}},
            private=True,
        )
        public.write_json_atomic(
            run_weekly.explainer_file(directory, EXPLAINER).with_suffix(".response.json"),
            {"response": explainer_response(), "at": NOW, "brief": BRIEF},
            private=True,
        )
        planned = run_weekly.plan(config, count=1, at=NOW)
        self.assertEqual(planned["explainers"], [{"key": EXPLAINER, "state": "recover"}])
        weeks = sum(entry.get("reserved_usd", 0) for entry in planned["weeks"])
        self.assertAlmostEqual(planned["reserved_usd"], weeks)
        researcher = Mock(
            return_value=({"research": worker.dossier(response(), snapshot["events"][0], NOW)}, "researched")
        )
        writer = Mock(side_effect=AssertionError("Duplicate paid call"))
        # The recovered page is validated and privacy checked once, when it is recovered.
        with patch.object(run_weekly, "checked_explainer", wraps=run_weekly.checked_explainer) as checked:
            result = run_weekly.run(config, count=1, researcher=researcher, explainer=writer, at=NOW)
        checked.assert_called_once()
        self.assertEqual(result["explainers"], 1)
        writer.assert_not_called()
        self.assertEqual(public.read_json(self.path / "ledger.json")["explainers"][EXPLAINER], self.page())

    def test_failed_explainer_waits_a_day_and_leaves_the_ledger(self):
        ledger, snapshot = ordinary()
        add_mismatches(ledger, ["c01234", "c01235", "c01236"])
        public.write_json_atomic(self.path / "ledger.json", ledger, private=True)
        public.write_json_atomic(self.path / "snapshot.json", snapshot, private=True)
        config = {
            "state_dir": str(self.path),
            "budget_database": str(self.db),
            "weekly_key_file": str(self.key),
        }
        researcher = Mock(
            return_value=({"research": worker.dossier(response(), snapshot["events"][0], NOW)}, "researched")
        )
        writer = Mock(side_effect=ValueError("unusable explainer"))
        self.assertEqual(
            run_weekly.run(config, count=1, researcher=researcher, explainer=writer, at=NOW)["failed"], 1
        )
        run_weekly.run(config, count=1, researcher=researcher, explainer=writer, at=NOW)
        writer.assert_called_once()
        self.assertNotIn("explainers", public.read_json(self.path / "ledger.json"))

    def test_an_explainer_refused_three_times_waits_thirty_days(self):
        ledger, snapshot = ordinary()
        add_mismatches(ledger, ["c01234", "c01235", "c01236"])
        public.write_json_atomic(self.path / "ledger.json", ledger, private=True)
        public.write_json_atomic(self.path / "snapshot.json", snapshot, private=True)
        config = {
            "state_dir": str(self.path),
            "budget_database": str(self.db),
            "weekly_key_file": str(self.key),
        }
        researcher = Mock(
            return_value=({"research": worker.dossier(response(), snapshot["events"][0], NOW)}, "researched")
        )
        writer = Mock(side_effect=ValueError("unusable explainer"))

        def run(days):
            at = public.utc((public._instant(NOW) + timedelta(days=days)).isoformat())
            run_weekly.run(config, count=1, researcher=researcher, explainer=writer, at=at)
            return public.read_json(self.path / "weekly/status.json")["explainers"][EXPLAINER], at

        # One attempt a day: the third refusal gives the explainer up, with its date.
        for day in range(3):
            entry, at = run(day * 2)
        self.assertEqual(writer.call_count, 3)
        self.assertEqual((entry["status"], entry["attempts"], entry["given_up_at"]), ("given_up", 3, at))
        planned = run_weekly.plan(config, count=1, at=at)["explainers"]
        self.assertEqual([(item["key"], item["state"]) for item in planned], [(EXPLAINER, "given_up")])
        # Inside the thirty days nothing is spent.
        run(4 + 29)
        self.assertEqual(writer.call_count, 3)
        # After them it is tried once more, and a refusal gives it up again from that date.
        entry, at = run(4 + 31)
        self.assertEqual(writer.call_count, 4)
        self.assertEqual((entry["status"], entry["given_up_at"]), ("given_up", at))
        run(4 + 33)
        self.assertEqual(writer.call_count, 4)
        self.assertNotIn("explainers", public.read_json(self.path / "ledger.json"))

    def test_export_links_canadian_mismatch_stories_to_the_explainer(self):
        ledger = add_mismatches(public.new_ledger("2026-09-01"), ["c01234", "c01235", "a01236"])
        plain = public.export_snapshot(ledger, mode="review", as_of=NOW)
        self.assertNotIn("explainers", plain)
        self.assertFalse(any("related" in event for event in plain["events"]))
        page = self.page()
        worker.add_explainer(ledger, page)
        snapshot = public.export_snapshot(ledger, mode="review", as_of=NOW)
        self.assertEqual(snapshot["explainers"], [page])
        link = {"path": page["path"], "title": page["title"], "observed_start": None}
        related = {event["aircraft"]["icao_address"]: event.get("related") for event in snapshot["events"]}
        self.assertEqual(related, {"C01234": [link], "C01235": [link], "A01236": None})

        def resigned(change):
            copy = deepcopy(snapshot)
            change(copy)
            copy["snapshot_id"] = public._digest(
                {key: value for key, value in copy.items() if key != "snapshot_id"}
            )
            return copy

        public.validate_snapshot(resigned(lambda copy: None))
        for change in (
            lambda copy: copy.pop("explainers"),
            lambda copy: copy["explainers"][0].update(title="Another title"),
            lambda copy: copy["events"][0]["related"][0].update(title="Another title"),
            lambda copy: copy["events"][0]["related"][0].update(path="/explainers/other/"),
            lambda copy: copy.update(explainers=[]),
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                public.validate_snapshot(resigned(change))
        story = {
            "path": story_path("older"),
            "title": "Older story",
            "observed_start": "2026-09-01T00:00:00Z",
        }
        self.assertEqual(public._related([story, link], story_path("own"), pages=True), [story, link])
        for raw in ([link, story], [link]):
            with self.assertRaises(ValueError):
                public._related(raw, story_path("own"))
        with self.assertRaises(ValueError):
            public._related([link, story], story_path("own"), pages=True)
        ledger["explainers"][EXPLAINER]["title"] = "Another title"
        with self.assertRaises(ValueError):
            public._validate_ledger(ledger)


if __name__ == "__main__":
    unittest.main()
