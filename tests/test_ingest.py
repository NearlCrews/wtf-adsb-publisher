"""The publisher imports validated JSON using only its own package."""

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from wtf_adsb_publisher import ingest, wire

ROOT = Path(__file__).resolve().parents[1]
NOW = 1791150000


class FeedFixture:
    """A scratch feed directory and inbox, with the example batch and an empty manifest."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.feed = self.root / "feed"
        self.database = self.root / "inbox.sqlite3"
        self.batch = json.loads((ROOT / "examples/change-batch.example.json").read_text())
        self.manifest = {
            "schema": "wtf-adsb.manifest",
            "schema_version": wire.VERSION,
            "profile": wire.PROFILE,
            "stream_id": self.batch["stream_id"],
            "generation": self.batch["generation"],
            "head_sequence": 0,
            "first_sequence": 1,
            "updated_at": wire.utc(NOW),
            "health": {
                "ready": True,
                "complete": True,
                "mode": "live",
                "last_receiver_poll": NOW,
                "analysis_watermark": NOW,
            },
            "batches": [],
        }

    def append(self, batch):
        data = wire.canonical(batch)
        path = f"batches/{batch['sequence_start']}-{batch['sequence_end']}.json"
        wire.atomic_bytes(self.feed / path, data)
        self.manifest["batches"].append(
            {
                "path": path,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "sequence_start": batch["sequence_start"],
                "sequence_end": batch["sequence_end"],
            }
        )
        self.manifest["head_sequence"] = batch["sequence_end"]
        wire.atomic_bytes(self.feed / "manifest.json", wire.canonical(self.manifest))

    def run_import(self, **options):
        return ingest.ingest_directory(self.feed, self.database, allow_synthetic=True, **options)


class InboxTests(FeedFixture, unittest.TestCase):
    def revision(self):
        batch = copy.deepcopy(self.batch)
        batch.update(sequence_start=2, sequence_end=2)
        change = batch["changes"][0]
        change.update(sequence=2, revision=2)
        change["observation"]["group"]["events"][0]["hits"] += 1
        change["evidence_digest"] = wire.digest(change["observation"])
        return batch

    def test_duplicate_delivery_is_a_noop_and_revision_updates_same_observation(self):
        self.append(self.batch)
        first = self.run_import()
        self.assertEqual(first, self.run_import())
        self.append(self.revision())
        result = self.run_import()
        self.assertEqual(result["cursor"], 2)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["observations"][0]["revision"], 2)
        self.assertEqual(result["observations"][0]["event_id"], first["observations"][0]["event_id"])

    def test_deferred_readback_commits_identical_state_without_loading_observations(self):
        self.append(self.batch)
        with patch.object(ingest, "read_state", side_effect=AssertionError("Reader loads this later")):
            self.assertIsNone(self.run_import(readback=False))
        saved = ingest.read_state(self.database)
        self.assertEqual(saved["cursor"], 1)
        self.assertEqual(len(saved["observations"]), 1)
        self.assertEqual(saved, self.run_import())

    def test_duplicate_delivery_does_not_rewrite_unchanged_observations(self):
        self.append(self.batch)
        self.run_import()
        original = ingest.connect
        statements = []

        @contextmanager
        def traced(database):
            with original(database) as db:
                db.set_trace_callback(statements.append)
                yield db

        with patch.object(ingest, "connect", traced):
            self.run_import()
        self.assertFalse(any(sql.startswith("UPDATE feed_observations") for sql in statements))

    def test_read_state_neither_initializes_a_missing_database_nor_changes_permissions(self):
        missing = self.root / "missing" / "inbox.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            ingest.read_state(missing)
        self.assertFalse(missing.parent.exists())
        self.append(self.batch)
        self.run_import()
        self.database.chmod(0o400)
        self.assertEqual(ingest.read_state(self.database)["cursor"], 1)
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o400)

    def test_corrupt_second_batch_does_not_advance_cursor_and_marks_feed_unready(self):
        self.append(self.batch)
        self.run_import()
        self.append(self.revision())
        (self.feed / "batches/2-2.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            self.run_import()
        state = ingest.read_state(self.database)
        self.assertEqual(state["cursor"], 1)
        self.assertFalse(state["health"]["ready"])
        self.assertEqual(state["observations"][0]["revision"], 1)

    def test_invalid_revision_rolls_back_all_changes_in_its_batch(self):
        self.append(self.batch)
        self.run_import()
        batch = self.revision()
        third = copy.deepcopy(batch["changes"][0])
        third["sequence"] = 3
        batch["changes"].append(third)
        batch["sequence_end"] = 3
        self.append(batch)
        with self.assertRaises(ValueError):
            self.run_import()
        self.assertEqual(ingest.read_state(self.database)["cursor"], 1)
        self.assertEqual(ingest.read_state(self.database)["observations"][0]["revision"], 1)

    def test_retraction_removes_only_explicit_event_and_discards_stored_evidence(self):
        self.append(self.batch)
        self.run_import()
        batch = self.revision()
        change = batch["changes"][0]
        change.pop("observation")
        change.pop("evidence_digest")
        change.update(operation="retract", reason_code="invalid_evidence")
        self.append(batch)
        state = self.run_import()
        self.assertEqual(state["observations"], [])
        self.assertEqual(state["retractions"][0]["reason_code"], "invalid_evidence")

    def test_generation_change_retention_gap_and_manifest_rollback_are_rejected(self):
        self.append(self.batch)
        self.run_import()
        original = copy.deepcopy(self.manifest)
        for field, value in (
            ("generation", "00000000-0000-4000-8000-000000000099"),
            ("updated_at", wire.utc(NOW - 1)),
            ("first_sequence", 2),
        ):
            with self.subTest(field=field):
                self.manifest = {**original, field: value}
                wire.atomic_bytes(self.feed / "manifest.json", wire.canonical(self.manifest))
                with self.assertRaises(ValueError):
                    self.run_import()
                self.assertEqual(ingest.read_state(self.database)["cursor"], 1)

    def test_synthetic_stream_requires_optin_even_on_repeat_import(self):
        self.append(self.batch)
        with self.assertRaises(ValueError):
            ingest.ingest_directory(self.feed, self.database)
        self.run_import()
        with self.assertRaises(ValueError):
            ingest.ingest_directory(self.feed, self.database)

    def test_unknown_extension_is_durable_quarantine_not_executed_code(self):
        extension = {"namespace": "example.label", "schema_version": "1.0.0", "payload": {"label": "fixture"}}
        change = self.batch["changes"][0]
        change["observation"]["extensions"] = [extension]
        change["evidence_digest"] = wire.digest(change["observation"])
        self.batch["extension_schemas"] = [
            {"namespace": "example.label", "schema_version": "1.0.0", "sha256": "a" * 64}
        ]
        self.append(self.batch)
        state = self.run_import()
        self.assertEqual(state["cursor"], 1)
        self.assertEqual(state["quarantined"], 1)
        self.assertFalse(state["health"]["ready"])
        self.assertEqual(state["observations"], [])

    def test_partial_import_cannot_expose_a_ready_intermediate_prefix(self):
        self.append(self.batch)
        self.run_import()
        self.append(self.revision())
        third = self.revision()
        third.update(sequence_start=3, sequence_end=3)
        third["changes"][0].update(sequence=3, revision=3)
        self.append(third)
        read_file = wire.read_file
        intermediate = []

        def inspect(root, relative, *args):
            if relative == "batches/3-3.json":
                intermediate.append(ingest.read_state(self.database))
            return read_file(root, relative, *args)

        with patch.object(wire, "read_file", side_effect=inspect):
            completed = self.run_import()
        self.assertEqual(intermediate[0]["cursor"], 2)
        self.assertFalse(intermediate[0]["health"]["ready"])
        self.assertFalse(intermediate[0]["health"]["complete"])
        self.assertTrue(completed["health"]["ready"])

    def test_reader_has_one_snapshot_during_a_concurrent_commit(self):
        self.append(self.batch)
        self.run_import()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
        original = sqlite3.connect
        changed = []

        def concurrent(*args, **kwargs):
            db = original(*args, **kwargs)

            def interleave(sql):
                if sql.startswith("SELECT o.*,r.replacements") and not changed:
                    with closing(original(self.database)) as writer, writer:
                        writer.execute("UPDATE feed_stream SET cursor=2")
                        writer.execute("UPDATE feed_observations SET revision=2")
                    changed.append(True)

            db.set_trace_callback(interleave)
            return db

        with patch.object(ingest.sqlite3, "connect", concurrent):
            state = ingest.read_state(self.database)
        self.assertEqual(changed, [True])
        self.assertEqual(state["cursor"], 1)
        self.assertEqual(state["observations"][0]["revision"], 1)

    def test_reviewed_schema_can_clear_and_restore_quarantine_without_reimport(self):
        schema = {
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        }
        schema_path = self.root / "label.schema.json"
        schema_path.write_bytes(wire.canonical(schema))
        entry = {"namespace": "example.label", "schema_version": "1.0.0", "sha256": wire.digest(schema)}
        change = self.batch["changes"][0]
        change["observation"]["extensions"] = [
            {"namespace": "example.label", "schema_version": "1.0.0", "payload": {"label": "fixture"}}
        ]
        change["evidence_digest"] = wire.digest(change["observation"])
        self.batch["extension_schemas"] = [entry]
        self.append(self.batch)
        self.assertEqual(self.run_import()["quarantined"], 1)
        approved = ingest.load_extension_schemas([{**entry, "path": str(schema_path)}])
        state = self.run_import(approved_extensions=approved)
        self.assertEqual(state["quarantined"], 0)
        self.assertEqual(len(state["observations"]), 1)
        self.assertEqual(state["cursor"], 1)
        self.assertEqual(self.run_import()["quarantined"], 1)

    def test_symlink_payload_and_duplicate_json_fields_are_rejected(self):
        self.append(self.batch)
        path = self.feed / "batches/1-1.json"
        target = self.root / "other.json"
        path.replace(target)
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.run_import()
        with self.assertRaises(ValueError):
            wire.loads(b'{"schema":1,"schema":2}')


def _uuid(number):
    return f"00000000-0000-4000-8000-{number:012x}"


def fixture_observation(example, number, revision=1):
    """The example observation grown to a feed-like one: several track and media points,
    a day's type counts, and a type norm, all deterministic in `number`."""
    value = copy.deepcopy(example)
    group, finding = value["group"], _uuid(1000 + number)
    start = NOW - 86400 + number * 600
    group.update(id=_uuid(number), first_seen=start, last_seen=start + 300, start=start)
    event = group["events"][0]
    event.update(id=finding, first_seen=start, last_seen=start + 300, hits=2 + revision)
    track = [
        {
            "ts": start + 15 * index,
            "lat": 42 + index / 100,
            "lon": -84 + index / 100,
            "gs": 600 + index,
            "tas": 450,
            "alt_baro": 35000,
            "source": "adsb_icao",
        }
        for index in range(3 + number % 5)
    ]
    event["evidence"].update(track=track)
    event["evidence"]["confirmation"]["samples"] = track[:2]
    value["context"] = {
        "policy_profile": "legacy-collection-v1",
        "novelty": {
            "coverage_days": 30,
            "type_days": {f"T{index:02d}": (index * number) % 31 for index in range(1 + number % 20)},
        },
        "type_norms": {
            finding: None if number % 3 else next(iter(example["context"]["type_norms"].values()))
        },
    }
    value["media"] = {
        "by_finding": {
            finding: {
                "parts": [
                    [
                        {
                            "ts": point["ts"],
                            "lat": point["lat"],
                            "lon": point["lon"],
                            "source": "adsb_icao",
                            "speed": point["gs"],
                            "altitude": None if index % 2 else point["alt_baro"],
                            "altitude_reference": "barometric",
                        }
                        for index, point in enumerate(track)
                    ]
                ]
            }
        }
    }
    return value


class ImportResultTests(FeedFixture, unittest.TestCase):
    """A fixed feed imports to the same inbox rows, so a faster validator changes no result."""

    # sha256 over every inbox table's rows; recorded from the import before the speed-up.
    RECORDED = "3386bb44286335bc8e992cd6f98a8fd10c324026260bb1f725932289996d7d60"

    def fixture_feed(self):
        example = self.batch["changes"][0]["observation"]
        changes = [("upsert", number, 1) for number in range(1, 41)]
        changes += [("upsert", number, 2) for number in range(1, 41, 4)]
        changes += [("retract", 5, 3)]
        self.batch["synthetic"] = False
        sequence = 0
        for chunk in range(0, len(changes), 20):
            batch = copy.deepcopy(self.batch)
            batch["changes"] = []
            for operation, number, revision in changes[chunk : chunk + 20]:
                sequence += 1
                change = {
                    "sequence": sequence,
                    "operation": operation,
                    "event_id": _uuid(number),
                    "revision": revision,
                    "updated_at": wire.utc(NOW + sequence),
                }
                if operation == "upsert":
                    observation = fixture_observation(example, number, revision)
                    change.update(evidence_digest=wire.digest(observation), observation=observation)
                else:
                    change.update(reason_code="superseded", superseded_by=[_uuid(6)])
                batch["changes"].append(change)
            batch.update(sequence_start=batch["changes"][0]["sequence"], sequence_end=sequence)
            self.append(batch)

    def rows(self):
        with closing(sqlite3.connect(self.database)) as db:
            tables = [
                name
                for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            ]
            return {table: sorted(db.execute(f"SELECT * FROM {table}").fetchall()) for table in tables}

    def test_a_fixed_feed_imports_to_the_recorded_rows(self):
        self.fixture_feed()
        state = ingest.ingest_directory(self.feed, self.database)
        self.assertEqual(
            (state["cursor"], len(state["observations"]), len(state["retractions"])), (51, 39, 1)
        )
        rows = self.rows()
        self.assertEqual(len(rows["feed_changes"]), 51)
        found = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        self.assertEqual(found, self.RECORDED)


class PublishedSchemaTests(unittest.TestCase):
    """The compiled observation validator decides as the complete published schema does.

    wire._validator leaves jsonValue, context.novelty, and media to Python checks; these tests
    compare its verdicts with the complete schema walk (tools/fuzz_wire.complete_schema) over
    deterministic mutations. tools/fuzz_wire.py runs the same comparison over seeded random
    mutations of any feed's observations, offline.
    """

    def setUp(self):
        from importlib.resources import files

        from jsonschema import Draft202012Validator

        schema = json.loads(
            files("wtf_adsb_publisher").joinpath("contracts/observation-v1.schema.json").read_text()
        )
        self.published = Draft202012Validator(schema)
        example = json.loads((ROOT / "examples/change-batch.example.json").read_text())["changes"][0][
            "observation"
        ]
        self.base = fixture_observation(example, 7)

    @staticmethod
    def accepted(observation):
        try:
            wire.validate_observation(observation)
        except ValueError:
            return False
        return True

    def mutations(self):
        """Each value replaced, removed, or joined by an extra field, through the whole tree."""
        values = (None, True, -1, 1.5, 91, -181, "", "AB\n", "y" * 20001, [], {"x": 1})

        def paths(node, prefix=()):
            if prefix:
                yield prefix
            if isinstance(node, dict):
                for key, item in node.items():
                    yield from paths(item, (*prefix, key))
            elif isinstance(node, list):
                for index, item in enumerate(node[:1]):
                    yield from paths(item, (*prefix, index))

        for path in paths(self.base):
            for value in (*values, "remove", "extra"):
                mutated = copy.deepcopy(self.base)
                parent = mutated
                for key in path[:-1]:
                    parent = parent[key]
                if value == "remove":
                    if not isinstance(parent, dict):
                        continue
                    del parent[path[-1]]
                elif value == "extra":
                    target = parent[path[-1]]
                    if not isinstance(target, dict):
                        continue
                    target["AB\n" if path[-1] == "type_days" else "extra"] = 1
                else:
                    parent[path[-1]] = copy.deepcopy(value)
                yield path, mutated

    def test_no_mutation_the_published_schema_rejects_is_accepted(self):
        self.assertTrue(self.published.is_valid(self.base))
        self.assertTrue(self.accepted(self.base))
        refused = 0
        for path, mutated in self.mutations():
            if not self.published.is_valid(mutated):
                refused += 1
                with self.subTest(path=path):
                    self.assertFalse(self.accepted(mutated))
        # The mutations reach the evidence, novelty, norm, and media rules.
        self.assertGreater(refused, 500)

    def test_the_compiled_validator_agrees_with_the_complete_schema(self):
        from tools.fuzz_wire import verdicts

        for path, mutated in self.mutations():
            compiled, complete = verdicts(mutated)
            with self.subTest(path=path):
                self.assertEqual(compiled, complete)


if __name__ == "__main__":
    unittest.main()
