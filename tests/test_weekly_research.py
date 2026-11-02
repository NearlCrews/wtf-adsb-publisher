"""Unattended research must preserve publication identity, budget and source provenance."""

import json
import os
import sqlite3
import sys
import time
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import run_weekly
from test_weekly_spotlight import NOW, fixture

from wtf_adsb_publisher import budget_store as app
from wtf_adsb_publisher import curation, explain
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher import weekly_research as worker


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


def response():
    urls = ["https://www.navy.mil/aircraft/e6/", "https://www.boeing.com/history/e6/"]
    raw = {
        "sections": [
            {
                "heading": "Design background",
                "text": "This synthetic source explains the design and engineering of an aircraft type without identifying a particular airframe or its flight purpose.",
                "sources": [urls[0]],
            },
            {
                "heading": "Type history",
                "text": "The second synthetic source supplies historical background for this type. Its descriptions do not establish what any crew was doing during this observation.",
                "sources": [urls[1]],
            },
        ],
        "sources": [{"url": url, "title": f"Synthetic source {index}"} for index, url in enumerate(urls)],
    }
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

    def test_request_contains_only_type_and_has_hard_tool_limit(self):
        encoded = worker.request_data({**self.event, "private": "secret", "receiver": [1, 2]})
        body = json.loads(encoded)
        # Weekly research uses the public model, as type research does.
        self.assertEqual(body["model"], explain.PUBLIC_MODEL)
        self.assertEqual(body["max_tool_calls"], 1)
        self.assertEqual(body["tool_choice"], "required")
        self.assertFalse(body["store"])
        data = json.loads(body["input"])
        self.assertEqual(set(data), {"aircraft_type"})
        self.assertNotIn(self.event["aircraft"]["registration"], body["input"])
        self.assertNotIn("secret", body["input"])

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


if __name__ == "__main__":
    unittest.main()
