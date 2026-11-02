"""Bindings saved for one public evidence version survive a projection-only change."""

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from test_public_compat import FIXTURES
from test_public_events import editorial
from test_weekly_spotlight import NOW, fixture

from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher import stories

ZERO = {"packets": 0, "research": 0, "media": 0, "approvals": 0, "unresolved": []}


def published_before_change():
    """A frozen weekly feature whose dossier, map and receipt use the previous card wording."""
    ledger, identifier, review, _inventory = fixture()
    record = ledger["records"][0]
    current = review["events"][0]
    receipt = deepcopy(current)
    receipt["limitations"] = receipt["limitations"][1:]
    receipt["evidence_version"] = public.evidence_version(receipt)
    record["editorial"]["weekly_spotlight"]["evidence_version"] = receipt["evidence_version"]
    record["approved_content_hash"] = public._digest(
        {"evidence": record["evidence"], "editorial": record["editorial"]}
    )
    record["automation"] = {
        "source": deepcopy(record["editorial"]["weekly_source"]),
        "source_kind": "frozen",
        "published_event": receipt,
        "media_evidence_version": receipt["evidence_version"],
        "media_parts": [[[0.0, 0.0], [1.0, 1.0]]],
    }
    return ledger, identifier, current


class EvidenceMigrationTests(unittest.TestCase):
    def test_weekly_dossier_map_and_approval_follow_the_same_observation(self):
        ledger, identifier, current = published_before_change()
        with self.assertRaisesRegex(ValueError, "migrate-evidence"):
            public.export_snapshot(ledger, mode="review", as_of=NOW)
        report = public.migrate_evidence_bindings(ledger)
        self.assertEqual(report, {**ZERO, "research": 1, "media": 1, "approvals": 1})
        record = ledger["records"][0]
        self.assertEqual(
            record["editorial"]["weekly_spotlight"]["evidence_version"], current["evidence_version"]
        )
        self.assertEqual(record["automation"]["media_evidence_version"], current["evidence_version"])
        # The receipt still records what was published.
        self.assertNotEqual(
            record["automation"]["published_event"]["evidence_version"], current["evidence_version"]
        )
        exported = public.export_snapshot(ledger, mode="review", as_of=NOW)
        self.assertEqual(exported["events"][0]["id"], identifier)
        self.assertIn("weekly_spotlight", exported["events"][0])
        self.assertEqual(public.migrate_evidence_bindings(ledger), ZERO)

    def test_a_different_observation_is_never_rebound(self):
        ledger, _identifier, _current = published_before_change()
        record = ledger["records"][0]
        record["automation"]["published_event"]["observed_start"] = "2026-10-03T23:50:00Z"
        before = deepcopy(ledger)
        report = public.migrate_evidence_bindings(ledger)
        self.assertEqual(
            report["unresolved"][0], {"id": record["id"], "reason": "revision 1: observation_changed"}
        )
        self.assertIn(
            {"id": record["id"], "reason": "revision 1: weekly_binding_unproven"}, report["unresolved"]
        )
        self.assertEqual(report["research"] + report["media"] + report["approvals"], 0)
        self.assertEqual(ledger, before)

    def test_unreviewed_content_is_never_re_signed(self):
        ledger, _identifier, _current = published_before_change()
        record = ledger["records"][0]
        record["editorial"]["summary"] = "Changed after approval."
        before = deepcopy(ledger)
        report = public.migrate_evidence_bindings(ledger)
        self.assertEqual(
            report["unresolved"][0], {"id": record["id"], "reason": "revision 1: approval_hash_mismatch"}
        )
        self.assertEqual(ledger, before)

    def test_saved_archive_packet_gains_the_label_provenance_its_finding_implies(self):
        item = FIXTURES["heatmap_watch_type_no_altitude"]
        ledger = public.new_ledger("2026-09-01")
        identifier = public.put_event(ledger, "frozen", item["legacy_packet"], editorial(), at=NOW)
        public.set_review_state(ledger, identifier, "approved", at=NOW)
        record = ledger["records"][0]
        record["automation"] = {"source": deepcopy(item["source"]), "source_kind": "frozen"}
        regenerated = stories.evidence_packet(
            item["source"], include_track=False, observation_interval=tuple(item["interval"])
        )
        target = public._projection(regenerated, editorial())["evidence_version"]
        self.assertNotEqual(public._projection(record["evidence"], editorial())["evidence_version"], target)
        report = public.migrate_evidence_bindings(ledger)
        self.assertEqual(report, {**ZERO, "packets": 1, "approvals": 1})
        record = ledger["records"][0]
        self.assertEqual(record["evidence"]["received"]["label_provenance"], "archived_label")
        self.assertEqual(public._projection(record["evidence"], editorial())["evidence_version"], target)
        self.assertIs(public._approved_revision(record), record)
        self.assertEqual(public.migrate_evidence_bindings(ledger), ZERO)

    def test_records_without_automation_or_needed_changes_are_untouched(self):
        ledger, _identifier, _review, _inventory = fixture()
        before = deepcopy(ledger)
        self.assertEqual(public.migrate_evidence_bindings(ledger), ZERO)
        self.assertEqual(ledger, before)

    def test_every_revision_moves_with_its_own_finding_and_the_map_follows_the_published_one(self):
        ledger, _identifier, current = published_before_change()
        record = ledger["records"][0]
        later = NOW.replace("15:00:00", "16:00:00")
        # A pending revision written by the previous code, as put_event records one.
        record["history"].append({key: deepcopy(value) for key, value in record.items() if key != "history"})
        record.update(
            revision=2, state="pending", updated_at=later, reviewed_at=None, approved_content_hash=None
        )
        record["editorial"]["summary"] = "Revised."
        # The publisher's finding has since grown; each dossier keeps its own saved finding.
        source = record["automation"]["source"]
        source["first_seen"] -= 3600
        record["automation"]["published_event"]["revision"] = 1
        report = public.migrate_evidence_bindings(ledger, at=later)
        self.assertEqual(report, {**ZERO, "research": 2, "media": 1, "approvals": 1})
        record = ledger["records"][0]
        self.assertEqual(record["state"], "pending")
        self.assertEqual(
            record["history"][-1]["editorial"]["weekly_spotlight"]["evidence_version"],
            current["evidence_version"],
        )
        self.assertIs(public._approved_revision(record), record["history"][-1])
        self.assertEqual(record["automation"]["media_evidence_version"], current["evidence_version"])
        audit = record["automation"]["evidence_migrations"]
        self.assertEqual(len(audit), 1)
        self.assertEqual(
            (audit[0]["at"], audit[0]["to"], audit[0]["research"]), (later, current["evidence_version"], 2)
        )

    def test_unproven_and_manual_dossiers_are_reported_without_changes(self):
        ledger, _identifier, _current = published_before_change()
        record = ledger["records"][0]
        record.pop("automation")
        before = deepcopy(ledger)
        report = public.migrate_evidence_bindings(ledger)
        self.assertEqual(
            report["unresolved"][0], {"id": record["id"], "reason": "revision 1: weekly_binding_unproven"}
        )
        self.assertTrue(report["unresolved"][1]["reason"].startswith("export_check: "))
        self.assertEqual(ledger, before)

    def test_odd_saved_findings_never_abort_the_migration(self):
        ledger, _identifier, current = published_before_change()
        record = ledger["records"][0]
        record["automation"]["source"] = {"evidence": {"aircraft": ["not", "a", "mapping"]}}
        record["editorial"]["weekly_source"]["evidence"]["aircraft"] = ["not", "a", "mapping"]
        record["approved_content_hash"] = public._digest(
            {"evidence": record["evidence"], "editorial": record["editorial"]}
        )
        report = public.migrate_evidence_bindings(ledger)
        self.assertEqual(report["research"], 1)
        self.assertEqual(
            ledger["records"][0]["editorial"]["weekly_spotlight"]["evidence_version"],
            current["evidence_version"],
        )

    def test_command_reports_by_default_and_writes_only_with_apply(self):
        ledger, _identifier, current = published_before_change()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            public.write_json_atomic(path, ledger, private=True)
            command = [
                sys.executable,
                "-B",
                str(ROOT / "tools/export_public_events.py"),
                "--ledger",
                str(path),
            ]
            dry = subprocess.run([*command, "migrate-evidence"], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(dry.stdout)["research"], 1)
            self.assertFalse(json.loads(dry.stdout)["applied"])
            self.assertEqual(public.read_json(path), ledger)
            subprocess.run(
                [*command, "migrate-evidence", "--apply"], capture_output=True, text=True, check=True
            )
            saved = public.read_json(path)
            self.assertEqual(
                saved["records"][0]["editorial"]["weekly_spotlight"]["evidence_version"],
                current["evidence_version"],
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
