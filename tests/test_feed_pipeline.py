"""Publication consumes a durable evidence view without receiver code or files."""

import builtins
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from test_autopilot import NOW, Ghost, candidate

from wtf_adsb_publisher import (
    autopilot,
    budget_store,
    curation,
    entity_context,
    entity_rules,
    explain,
    plugins,
    public_events,
)
from wtf_adsb_publisher.feed_adapter import feed_fresh

ROOT = Path(__file__).resolve().parents[1]
STAMP = public_events._instant(NOW).timestamp()


def feed(*candidates):
    records = []
    for item in candidates:
        source = deepcopy(item["source"])
        source["id"] = str(uuid4())
        source.update(origin="live-readsb", category="identity")
        event_id = str(uuid4())
        records.append(
            {
                "event_id": event_id,
                "revision": 1,
                "observation": {
                    "group": {
                        "id": event_id,
                        "events": [source],
                        "first_seen": source["first_seen"],
                        "last_seen": source["last_seen"],
                        "start": source["first_seen"],
                        "registration": source["evidence"]["aircraft"].get("r"),
                    },
                    "context": {"novelty": None, "policy_profile": "legacy-collection-v1"},
                    "media": {"by_finding": {}},
                },
            }
        )
    return {
        "stream_id": str(uuid4()),
        "cursor": len(records),
        "observations": records,
        "retractions": [],
        "health": {
            "ready": True,
            "complete": True,
            "mode": "live",
            "last_receiver_poll": STAMP,
            "analysis_watermark": STAMP,
        },
        "synthetic": False,
    }


class FeedPipelineTests(unittest.TestCase):
    def setUp(self):
        self.ledger = public_events.new_ledger("2026-10-01")
        self.ghost = Ghost()

    def publish(self):
        return autopilot.publish(self.ledger, self.ghost, persist=lambda _: None, at=NOW)

    def test_imported_evidence_preserves_real_story_and_idempotent_ghost_publication(self):
        source = candidate(1)
        state = feed(source)
        original_import = builtins.__import__

        def independent(name, *args, **kwargs):
            if name.startswith("wtf_adsb_engine") or name in {"app", "history_backfill"}:
                raise AssertionError("Publisher tried to import receiver code")
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=independent),
            patch("sqlite3.connect", side_effect=AssertionError("Selection opened a receiver database")),
        ):
            choices = autopilot.collect_state(state, STAMP, ledger=self.ledger)
            self.assertEqual(len(choices), 1)
            self.assertEqual(autopilot.stage(self.ledger, choices, at=NOW)["changed"], 1)
            self.publish()
            record = deepcopy(self.ledger["records"][0])
            self.assertEqual(record["automation"]["engine_event_id"], state["observations"][0]["event_id"])
            writes = self.ghost.writes
            choices = autopilot.collect_state(state, STAMP, ledger=self.ledger)
            self.assertEqual(autopilot.stage(self.ledger, choices, at=NOW)["changed"], 0)
            self.publish()
            self.assertEqual(self.ghost.writes, writes)
            self.assertEqual(self.ledger["records"][0]["id"], record["id"])

    def test_existing_url_and_owner_identity_survive_first_import(self):
        source = candidate(2)
        autopilot.stage(self.ledger, [source], at=NOW)
        self.publish()
        original = deepcopy(self.ledger["records"][0])
        state = feed(source)
        choices = autopilot.collect_state(state, STAMP, ledger=self.ledger)
        autopilot.stage(self.ledger, choices, at=NOW)
        self.publish()
        current = self.ledger["records"][0]
        self.assertEqual(len(self.ledger["records"]), 1)
        self.assertEqual(current["id"], original["id"])
        self.assertEqual(current["episode_key"], original["episode_key"])
        self.assertEqual(current["editorial"]["article_path"], original["editorial"]["article_path"])

    def test_stable_finding_binding_updates_a_corrected_legacy_story(self):
        source = candidate(2)
        autopilot.stage(self.ledger, [source], at=NOW)
        self.publish()
        state = feed(source)
        autopilot.stage(self.ledger, autopilot.collect_state(state, STAMP, ledger=self.ledger), at=NOW)
        original = deepcopy(self.ledger["records"][0])
        item = state["observations"][0]
        group = item["observation"]["group"]
        source = group["events"][0]
        source["first_seen"] -= 10
        group["first_seen"] -= 10
        group["start"] -= 10
        item["revision"] += 1
        choices = autopilot.collect_state(state, STAMP, ledger=self.ledger)
        self.assertEqual(len(choices), 1)
        result = autopilot.stage(self.ledger, choices, at=NOW)
        self.assertEqual(result["changed"], 1)
        self.publish()
        current = self.ledger["records"][0]
        self.assertEqual(len(self.ledger["records"]), 1)
        self.assertEqual(current["id"], original["id"])
        self.assertEqual(current["episode_key"], original["episode_key"])
        self.assertEqual(current["editorial"]["article_path"], original["editorial"]["article_path"])
        self.assertEqual(current["automation"]["source"]["first_seen"], source["first_seen"])
        self.assertEqual(current["automation"]["engine_revision"], 2)

    def test_ineligible_correction_blocks_the_bound_story_after_timestamp_change(self):
        state = feed(candidate(2))
        autopilot.stage(self.ledger, autopilot.collect_state(state, STAMP, ledger=self.ledger), at=NOW)
        self.publish()
        item = state["observations"][0]
        group = item["observation"]["group"]
        group["first_seen"] -= 10
        group["start"] -= 10
        source = group["events"][0]
        source["first_seen"] -= 10
        source["evidence"]["absorbed_by"] = {"superseded": True}
        item["revision"] += 1
        selected = autopilot.collect_state(state, STAMP, ledger=self.ledger)
        self.assertEqual(len(selected), 1)
        result = autopilot.stage(self.ledger, selected, at=NOW)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(self.ledger["records"][0]["automation"]["blocked"], "ineligible_source")
        snapshot, _ = self.publish()
        self.assertEqual(snapshot["events"], [])

    def test_initial_migration_binds_earlier_source_revisions_to_stable_ids(self):
        earlier, replacement = candidate(2), candidate(2, "rapid_descent", -9000)
        autopilot.stage(self.ledger, [earlier], at=NOW)
        self.publish()
        autopilot.stage(self.ledger, [replacement], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        state = feed(earlier, replacement)
        from wtf_adsb_publisher.feed_adapter import collection

        collection(state, self.ledger, at=NOW)
        before_id = state["observations"][0]["observation"]["group"]["events"][0]["id"]
        after_id = state["observations"][1]["observation"]["group"]["events"][0]["id"]
        self.assertEqual(record["history"][0]["automation"]["engine_finding_id"], before_id)
        self.assertEqual(record["automation"]["engine_finding_id"], after_id)
        indexed = autopilot._Index(self.ledger["records"])
        for identifier in (before_id, after_id):
            self.assertIs(indexed.by_source[("engine", state["stream_id"], identifier)], record)

    def test_missing_frozen_watchlist_evidence_never_uses_a_current_lookup(self):
        from wtf_adsb_publisher import watchlists

        state = feed(candidate(2))
        with patch.object(watchlists, "lookup", side_effect=AssertionError("Unfrozen enrichment lookup")):
            choices = autopilot.collect_state(state, STAMP, ledger=self.ledger)
            self.assertEqual(autopilot.stage(self.ledger, choices, at=NOW)["changed"], 1)
            self.publish()
        self.assertNotIn("watchlist", self.ledger["records"][0]["evidence"])

    def test_a_frozen_story_is_not_bound_to_a_matching_receiver_journal_row(self):
        source = candidate(6)
        autopilot.stage(self.ledger, [source], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        record["automation"]["source_kind"] = "frozen"
        state = feed(source)
        autopilot.collect_state(state, STAMP, since=STAMP, ledger=self.ledger)
        self.assertNotIn("engine_event_id", record["automation"])

    def test_positive_retraction_withdraws_an_article_but_retention_does_not(self):
        state = feed(candidate(3))
        choices = autopilot.collect_state(state, STAMP, ledger=self.ledger)
        autopilot.stage(self.ledger, choices, at=NOW)
        self.publish()
        event_id = state["observations"][0]["event_id"]
        empty = {**state, "observations": []}
        autopilot.collect_state(empty, STAMP, ledger=self.ledger)
        self.assertEqual(self.ledger["records"][0]["state"], "approved")
        empty["retractions"] = [{"event_id": event_id, "reason_code": "invalidated"}]
        autopilot.collect_state(empty, STAMP, ledger=self.ledger)
        snapshot, _ = self.publish()
        self.assertFalse(snapshot["events"])
        self.assertEqual(self.ledger["records"][0]["state"], "withdrawn")

    def test_superseded_group_rebinds_surviving_finding_before_withdrawing_parent(self):
        state = feed(candidate(3))
        autopilot.stage(self.ledger, autopilot.collect_state(state, STAMP, ledger=self.ledger), at=NOW)
        self.publish()
        original = deepcopy(self.ledger["records"][0])
        item = state["observations"][0]
        parent, replacement = item["event_id"], str(uuid4())
        item["event_id"] = replacement
        item["observation"]["group"]["id"] = replacement
        state["retractions"] = [
            {"event_id": parent, "reason_code": "superseded", "superseded_by": [replacement]}
        ]
        autopilot.stage(self.ledger, autopilot.collect_state(state, STAMP, ledger=self.ledger), at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        self.assertEqual(record["state"], "approved")
        self.assertEqual(record["automation"]["engine_event_id"], replacement)
        self.assertEqual(record["id"], original["id"])
        self.assertEqual(record["editorial"]["article_path"], original["editorial"]["article_path"])

    def test_partial_supersession_suspends_then_recovers_without_losing_article_identity(self):
        from wtf_adsb_publisher.feed_adapter import apply_retractions

        state = feed(candidate(3))
        autopilot.stage(self.ledger, autopilot.collect_state(state, STAMP, ledger=self.ledger), at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        original_id = record["id"]
        item = state["observations"][0]
        parent, replacement = item["event_id"], str(uuid4())
        state["observations"] = []
        state["retractions"] = [
            {"event_id": parent, "reason_code": "superseded", "superseded_by": [replacement]}
        ]
        apply_retractions(state, self.ledger, at=NOW)
        snapshot, _ = self.publish()
        self.assertFalse(snapshot["events"])
        self.assertNotEqual(record["state"], "withdrawn")
        self.assertEqual(record["automation"]["blocked"], "engine_replacement_pending")
        item["event_id"] = replacement
        item["observation"]["group"]["id"] = replacement
        state["observations"] = [item]
        apply_retractions(state, self.ledger, at=NOW)
        self.assertEqual(record["automation"]["engine_event_id"], replacement)
        autopilot.stage(self.ledger, autopilot.collect_state(state, STAMP, ledger=self.ledger), at=NOW)
        snapshot, _ = self.publish()
        self.assertEqual(snapshot["events"][0]["id"], original_id)
        self.assertEqual(len(self.ledger["records"]), 1)

    def test_the_site_limit_and_exception_policy_apply_after_ingestion(self):
        state = feed(*(candidate(i) for i in range(1, 15)), candidate(99, "rapid_descent", -9000))
        selected = autopilot.collect_state(state, STAMP, ledger=self.ledger)
        self.assertLessEqual(sum(not item["exceptional"] for item in selected), curation.MAX_PER_DAY)
        self.assertTrue(any(item["exceptional"] for item in selected))

    def test_media_uses_exported_segments_with_no_archive_reader(self):
        state = feed(candidate(4))
        observation = state["observations"][0]["observation"]
        source = observation["group"]["events"][0]
        points = [
            {
                "ts": source["first_seen"] + step * 15,
                "lat": -45.0,
                "lon": -150.0 + step * 0.01,
                "source": "adsb_icao",
                "speed": 102,
                "altitude": 3500,
                "altitude_reference": "barometric",
            }
            for step in range(20)
        ]
        observation["media"]["by_finding"][source["id"]] = {"parts": [points]}
        choices = autopilot.collect_state(state, STAMP, ledger=self.ledger)
        autopilot.stage(self.ledger, choices, at=NOW)
        snapshot, _ = self.publish()
        spec = importlib.util.spec_from_file_location("publisher_runner", ROOT / "tools/run_autopilot.py")
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as directory:
            manifest = runner.generate_media(snapshot, self.ledger, Path(directory))
            item = json.loads(manifest.read_text())["events"][snapshot["events"][0]["id"]]
            self.assertEqual(item["kind"], "track")
            self.assertTrue(item["replaySrc"])

    def test_stale_or_unfinished_feed_cannot_authorize_new_publication(self):
        state = feed(candidate(5))
        self.assertTrue(feed_fresh(state, STAMP))
        state["health"]["complete"] = False
        self.assertFalse(feed_fresh(state, STAMP))
        state["health"]["complete"] = True
        self.assertFalse(feed_fresh(state, STAMP + 301))
        state["health"]["analysis_watermark"] = STAMP - 400
        self.assertFalse(feed_fresh(state, STAMP))
        state["health"] = {"ready": False}
        self.assertFalse(feed_fresh(state, STAMP))


class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "publisher_runner_tests", ROOT / "tools/run_autopilot.py"
        )
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def test_invalid_renderer_stops_before_ingest_or_ghost_writes(self):
        from wtf_adsb_publisher import ingest

        ghost = Ghost()
        with patch.object(
            ingest, "ingest_directory", side_effect=AssertionError("Ingest began before preflight")
        ):
            with self.assertRaisesRegex(ValueError, "not installed"):
                self.runner.run(
                    {"feed_directory": "unused", "plugins": {"renderer": {"plugin": "missing"}}}, client=ghost
                )
        self.assertEqual(ghost.writes, 0)

    def test_synthetic_feed_holds_real_runs_for_preview_and_production(self):
        state = feed(candidate(2))
        state["synthetic"] = True
        for production in (False, True):
            with self.subTest(production=production), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ledger = public_events.new_ledger("2026-10-01")
                public_events.write_json_atomic(root / "ledger.json", ledger, private=True)
                config = {
                    "production": production,
                    "state_dir": str(root),
                    "inbox_database": str(root / "inbox.sqlite3"),
                    "live_since": "2026-10-01T00:00:00Z",
                    "preview_ssh_host": "unused",
                }
                ghost = Ghost()
                with (
                    patch.object(plugins.JsonInbox, "read", return_value=state),
                    patch.object(public_events, "now_utc", return_value=NOW),
                    patch.object(self.runner, "airframe_photos", return_value=({}, {})),
                    patch.object(self.runner, "release_fingerprint", return_value="fixture"),
                ):
                    result = self.runner.run(config, client=ghost, deploy=False)
                self.assertEqual(ghost.writes, 0)
                self.assertEqual(result["changed"], 0)
                self.assertIn("feed_synthetic", result["warnings"])
                saved = json.loads((root / "ledger.json").read_text())
                self.assertFalse(saved["records"])

    def test_synthetic_evidence_can_be_simulated_with_no_external_writes(self):
        state = feed(candidate(2))
        state["synthetic"] = True
        ledger = public_events.new_ledger("2026-10-01")
        ghost = self.runner.SimulatedGhost({"posts": [], "pages": []}, at=NOW)
        with tempfile.TemporaryDirectory() as directory:
            config = {"production": True, "state_dir": directory, "live_since": "2026-10-01T00:00:00Z"}
            with patch.object(plugins.JsonInbox, "read", return_value=state):
                snapshot, staged, _, warnings = self.runner.publication_pass(
                    config, ledger, {}, lambda: ghost, lambda _: None, NOW, simulate=True
                )
            self.assertEqual(staged["changed"], 1)
            self.assertEqual(len(snapshot["events"]), 1)
            self.assertNotIn("feed_synthetic", warnings)
            self.assertTrue(ghost.writes)

    def test_ingest_failure_preserves_verified_withdrawals_and_holds_new_stories(self):
        from wtf_adsb_publisher import ingest

        ledger = public_events.new_ledger("2026-10-01")
        state = feed(candidate(2))
        ghost = Ghost()
        autopilot.stage(ledger, autopilot.collect_state(state, STAMP, ledger=ledger), at=NOW)
        autopilot.publish(ledger, ghost, persist=lambda _: None, at=NOW)
        state["retractions"] = [
            {"event_id": state["observations"][0]["event_id"], "reason_code": "invalid_evidence"}
        ]
        state["observations"] = feed(candidate(3))["observations"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_events.write_json_atomic(root / "ledger.json", ledger, private=True)
            config = {
                "state_dir": str(root),
                "inbox_database": str(root / "inbox.sqlite3"),
                "feed_directory": str(root / "feed"),
                "live_since": "2026-10-01T00:00:00Z",
                "preview_ssh_host": "unused",
            }
            with (
                patch.object(ingest, "ingest_directory", side_effect=ValueError("Later batch invalid")),
                patch.object(plugins.JsonInbox, "read", return_value=state),
                patch.object(self.runner, "airframe_photos", return_value=({}, {})),
                patch.object(self.runner, "release_fingerprint", return_value="fixture"),
            ):
                result = self.runner.run(config, client=ghost, deploy=False)
            saved = json.loads((root / "ledger.json").read_text())
            self.assertEqual(len(saved["records"]), 1)
            self.assertEqual(saved["records"][0]["state"], "withdrawn")
            self.assertEqual(result["events"], 0)
            self.assertIn("feed_ingest_error", result["warnings"])
            self.assertEqual(ghost.posts[0]["status"], "draft")

    def test_a_pass_looks_up_entry_links_first_and_a_simulation_only_reads_the_cache(self):
        state = feed(candidate(2))
        ledger = public_events.new_ledger("2026-10-01")
        cached = {
            entity_rules.cache_key("https://en.wikipedia.org/wiki/Example", "Example"): {
                "state": "unavailable",
                "reason": "label",
                "association": "Example",
                "retrieved_at": 1,
            }
        }
        links = [("https://en.wikipedia.org/wiki/Other", "Other", "Distinctive", None)]
        staged, original = [], autopilot.stage

        def stage(ledger, candidates, **options):
            staged.append(options["inputs"].entities)
            return original(ledger, candidates, **options)

        with tempfile.TemporaryDirectory() as directory:
            entity_context.save(directory, cached)
            config = {"state_dir": directory, "live_since": "2026-10-01T00:00:00Z"}
            ghost = self.runner.SimulatedGhost({"posts": [], "pages": []}, at=NOW)
            with (
                patch.object(plugins.JsonInbox, "read", return_value=state),
                patch.object(autopilot, "entity_links", return_value=links),
                patch.object(autopilot, "stage", side_effect=stage),
                # No failure in the lookups, not even an unexpected one, stops the pass.
                patch.object(entity_context, "resolve", side_effect=RuntimeError("unexpected")) as resolve,
            ):
                with self.assertLogs("discoveries.run_autopilot", "WARNING") as logged:
                    _, result, _, warnings = self.runner.publication_pass(
                        config, ledger, {}, lambda: ghost, lambda _: None, NOW
                    )
                self.assertEqual(result["changed"], 1)
                self.assertIn("entity_lookup_failed", warnings)
                self.assertIn("RuntimeError", logged.output[0])
                resolve.assert_called_once()
                self.assertEqual(resolve.call_args.args, (directory, links))
                # A simulated pass, as dry_run() runs it, only reads the cache.
                self.runner.publication_pass(
                    config,
                    ledger,
                    {},
                    lambda: ghost,
                    lambda _: None,
                    NOW,
                    simulate=True,
                    lookup_entities=False,
                )
                resolve.assert_called_once()
        self.assertEqual(staged, [cached, cached])

    def test_a_norm_kind_story_from_a_feed_without_norms_is_staged_with_a_warning(self):
        from test_type_norms import seasonal

        for exported in (False, True):
            with self.subTest(exported=exported), tempfile.TemporaryDirectory() as directory:
                state = feed(seasonal())
                observation = state["observations"][0]["observation"]
                finding = observation["group"]["events"][0]["id"]
                if exported:
                    # A 1.1.0 observation names every norm-kind finding, null when there is no norm.
                    observation["context"]["type_norms"] = {finding: None}
                ledger = public_events.new_ledger("2026-10-01")
                status = {"type_norm_unexported": {"stories": ["an earlier pass"]}}
                config = {"state_dir": directory, "live_since": "2026-10-01T00:00:00Z"}
                ghost = self.runner.SimulatedGhost({"posts": [], "pages": []}, at=NOW)
                with (
                    patch.object(plugins.JsonInbox, "read", return_value=state),
                    patch.object(autopilot, "_place", return_value=None),
                ):
                    _, staged, _, warnings = self.runner.publication_pass(
                        config, ledger, status, lambda ghost=ghost: ghost, lambda _: None, NOW, simulate=True
                    )
                # The story is staged either way, without a norm; only the 1.0.0 one is reported.
                self.assertEqual(staged["changed"], 1)
                record = ledger["records"][0]
                self.assertIsNone(record["automation"]["type_norm"])
                if exported:
                    self.assertNotIn("type_norm_unexported", warnings)
                    self.assertNotIn("type_norm_unexported", status)
                else:
                    self.assertIn("type_norm_unexported", warnings)
                    self.assertEqual(status["type_norm_unexported"], {"stories": [record["episode_key"]]})

    def test_refresh_entities_marks_every_answer_stale(self):
        failed = {"state": "failed", "failures": 1, "failed_at": 1, "error": "OSError", "association": "C"}
        a, b, c = (entity_rules.cache_key(f"https://en.wikipedia.org/wiki/{name}", name) for name in "ABC")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            public_events.write_json_atomic(path, {"state_dir": directory})
            entity_context.save(
                directory,
                {
                    a: {"state": "available", "association": "A"},
                    b: {"state": "unavailable", "association": "B"},
                    c: failed,
                },
            )
            with (
                patch.object(sys, "argv", ["run_autopilot.py", "--config", str(path), "--refresh-entities"]),
                # main() narrows the process umask, which would leak into later tests.
                patch.object(self.runner.os, "umask"),
                patch("builtins.print") as printed,
            ):
                self.runner.main()
            printed.assert_called_once_with(json.dumps({"marked_stale": 2}))
            self.assertEqual(
                entity_context.load(directory),
                {
                    a: {"state": "available", "association": "A", "stale": True},
                    b: {"state": "unavailable", "association": "B", "stale": True},
                    c: failed,
                },
            )

    def test_refresh_entities_takes_only_the_configuration(self):
        for extra in (["--reproject"], ["--no-deploy"], ["--apply"]):
            argv = ["run_autopilot.py", "--config", "x", "--refresh-entities", *extra]
            with (
                self.subTest(extra=extra),
                patch.object(sys, "argv", argv),
                patch("sys.stderr"),
                self.assertRaises(SystemExit),
            ):
                self.runner.main()


class BudgetMigrationTests(unittest.TestCase):
    def test_failed_migration_keeps_new_destination_paused(self):
        for failure in ("schema", "metadata"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                source, destination = (Path(directory) / name for name in ("old.sqlite3", "new.sqlite3"))
                budget_store.initialize(source)
                with budget_store.connect(source) as db:
                    if failure == "schema":
                        db.execute("DROP TABLE ai_runs")
                    else:
                        db.execute("INSERT INTO meta VALUES ('ai_one_time_spent','invalid JSON')")
                before = source.read_bytes()
                with self.assertRaises(ValueError):
                    budget_store.migrate(source, destination)
                self.assertTrue(explain.journal_paused(destination))
                self.assertEqual(source.read_bytes(), before)

    def test_runtime_never_recreates_a_missing_or_uninitialized_spending_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing" / "budget.sqlite3"
            with self.assertRaises(sqlite3.OperationalError):
                explain.journal_paused(missing)
            self.assertFalse(missing.parent.exists())
            for name, schema in (("empty", ""), ("unrelated", "CREATE TABLE other (id INTEGER)")):
                path = root / (name + ".sqlite3")
                with closing(sqlite3.connect(path)) as database:
                    database.executescript(schema)
                before = path.read_bytes()
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "initialized or migrated"):
                    explain.journal_paused(path)
                self.assertEqual(path.read_bytes(), before)

    def test_migration_refuses_source_aliases_without_changing_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "budget.sqlite3"
            budget_store.initialize(source)
            before = source.read_bytes()
            symlink, hardlink = (Path(directory) / name for name in ("symlink.sqlite3", "hardlink.sqlite3"))
            symlink.symlink_to(source)
            hardlink.hardlink_to(source)
            for destination in (source, symlink, hardlink):
                with self.subTest(destination=destination.name), self.assertRaisesRegex(ValueError, "differ"):
                    budget_store.migrate(source, destination)
                self.assertEqual(source.read_bytes(), before)

    def test_uncertain_reservation_and_cumulative_budget_survive_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = (Path(directory) / name for name in ("old.sqlite3", "budget.sqlite3"))
            budget_store.initialize(source)
            with budget_store.connect(source) as db:
                db.execute(
                    "INSERT INTO ai_runs (started_at,status,reserved_cost) VALUES (?,?,?)",
                    (STAMP, "running", 0.55),
                )
                budget_store.put_meta(db, "ai_one_time_spent", 91.5)
            result = budget_store.migrate(source, destination)
            self.assertEqual(result, {"runs": 1, "paused": True})
            with budget_store.connect(destination) as db:
                self.assertEqual(db.execute("SELECT reserved_cost FROM ai_runs").fetchone()[0], 0.55)
                self.assertEqual(budget_store.meta(db, "ai_one_time_spent"), 91.5)
                self.assertTrue(budget_store.meta(db, "ai_paused"))
            with self.assertRaises(ValueError):
                budget_store.migrate(source, destination)

    def test_migration_refuses_cumulative_spend_even_without_retained_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = (Path(directory) / name for name in ("old.sqlite3", "new.sqlite3"))
            budget_store.initialize(source)
            budget_store.initialize(target)
            with budget_store.connect(target) as db:
                budget_store.put_meta(db, "ai_one_time_spent", 25.0)
            with self.assertRaisesRegex(ValueError, "spending already exists"):
                budget_store.migrate(source, target)
            with budget_store.connect(target) as db:
                self.assertEqual(budget_store.meta(db, "ai_one_time_spent"), 25.0)

    def test_runtime_budget_connection_refuses_receiver_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receiver.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE events (id INTEGER)")
                db.commit()
            with self.assertRaises(ValueError):
                budget_store.connect(path)


class PluginTests(unittest.TestCase):
    def test_every_builtin_uses_installed_entry_points(self):
        for role, name in plugins.DEFAULTS.items():
            self.assertIn(name, plugins.discover(role))
            self.assertEqual(plugins.load(role, {}).descriptor.role, role)

    def test_extension_catalog_requires_absolute_reviewed_schema_paths(self):
        self.assertEqual(plugins.approved_extensions({}), {})
        with self.assertRaises(ValueError):
            plugins.approved_extensions({"extension_schemas": [{"path": "relative.json"}]})
        with self.assertRaises(ValueError):
            plugins.approved_extensions({"extension_schemas": {}})

    def test_unknown_or_malformed_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            plugins.load("reader", {"plugins": {"reader": {"plugin": "missing"}}})
        with self.assertRaises(ValueError):
            plugins.load("reader", {"plugins": {"reader": {"settingz": {}}}})


if __name__ == "__main__":
    unittest.main()
