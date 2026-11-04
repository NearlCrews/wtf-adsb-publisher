"""Public history uses reviewed, durable episodes and a complete publication inventory."""

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import private_terms
from wtf_adsb_publisher import public_events as public

NOW = "2026-10-04T20:00:00Z"


def packet(start="2026-10-03T23:55:00Z", end="2026-10-04T00:05:00Z", kind="circling"):
    return {
        "schema_version": 1,
        "kind": kind,
        "observation": {"start_utc": start, "end_utc": end, "qualifying_count": 40},
        "aircraft": {"registration": "N12345", "type_code": "B407", "type_description": "Bell 407"},
        "received": {
            "readings": [{"field": "gs", "value": 70, "label": "PRIVATE LABEL", "unit": "PRIVATE UNIT"}],
            "saved_readings_at_utc": start,
        },
        "pattern": {"start_utc": start, "end_utc": end, "duration_seconds": 600, "closed_loops": 3},
        "links": [
            {
                "id": "type_reference",
                "label": "Bell 407",
                "url": "https://www.bellflight.com/products/bell-407",
            }
        ],
    }


def story_path(name):
    """A story URL in the story slug family."""
    return f"/stories/{name}-3-oct-2026/"


STORY_PATH = story_path("helicopter-circuits")
EVENT_PATH = story_path("event")


def editorial(path=STORY_PATH, **changes):
    return {
        "title": "Helicopter track contains three closed loops",
        "summary": "Three loops are present in this selected ten-minute analysis window.",
        "article_path": path,
        "ghost_id": hashlib.sha256(path.encode()).hexdigest()[:24],
        "secondary_labels": ["Helicopter", "Watchlist match"],
        **changes,
    }


def add(ledger, key="episode-a", evidence=None, metadata=None, approved=True):
    identifier = public.put_event(ledger, key, evidence or packet(), metadata or editorial(), at=NOW)
    if approved:
        public.set_review_state(ledger, identifier, "approved", at=NOW)
    return identifier


def inventory(ledger):
    return {
        "complete": True,
        "total": len(ledger["records"]),
        "as_of_utc": NOW,
        "posts": [
            {
                "slug": public.article_slug(row["editorial"]["article_path"]),
                "id": row["editorial"].get("ghost_id"),
                "status": "published",
                "visibility": "public",
                "published_at": NOW,
                "updated_at": NOW,
            }
            for row in ledger["records"]
        ],
    }


def published(ledger, source=None):
    return public.export_snapshot(ledger, mode="published", as_of=NOW, inventory=source or inventory(ledger))


class PublicEventTests(unittest.TestCase):
    def setUp(self):
        self.ledger = public.new_ledger("2026-09-20")

    def test_a_write_checks_only_its_record_and_every_holder_sees_the_revision(self):
        identifier = add(self.ledger)
        other = add(self.ledger, key="episode-b", metadata=editorial(story_path("other")))
        broken = public.find_record(self.ledger, other)
        broken["evidence"]["kind"] = "no-such-kind"
        self.assertEqual(public.broken_records(self.ledger), {other})
        with self.assertRaises(ValueError):
            public._validate_ledger(self.ledger)
        record = public.find_record(self.ledger, identifier)
        automation = record["automation"] = {"source": {"hex": "a12345"}}
        # Another record that no longer validates does not block this one's revision or approval.
        public.put_event(self.ledger, "episode-a", packet(), editorial(summary="A revised summary."), at=NOW)
        public.set_review_state(self.ledger, identifier, "approved", at=NOW)
        self.assertIs(public.find_record(self.ledger, identifier), record)
        self.assertIs(record["automation"], automation)
        self.assertEqual((record["revision"], record["state"]), (2, "approved"))
        self.assertEqual(record["history"][0]["editorial"]["summary"], editorial()["summary"])
        # The pages, IDs and episode keys stay unique across the ledger, and a refused
        # write changes nothing.
        before = deepcopy(self.ledger)
        for key, path in (("episode-c", STORY_PATH), ("episode-c", story_path("other"))):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "share one event page"):
                public.put_event(self.ledger, key, packet(), editorial(path), at=NOW)
        self.assertEqual(self.ledger, before)
        self.assertIsNone(public.find_record(self.ledger, "no-such-id"))

    def test_one_episode_has_one_stable_id_and_primary_count_after_revision(self):
        identifier = add(self.ledger)
        updated = public.put_event(
            self.ledger, "episode-a", packet(), editorial(summary="A revised explanation."), at=NOW
        )
        self.assertEqual(identifier, updated)
        self.assertEqual(len(self.ledger["records"]), 1)
        before_approval = published(self.ledger)["events"]
        self.assertEqual(len(before_approval), 1)
        self.assertEqual(before_approval[0]["summary"], editorial()["summary"])
        self.assertEqual(before_approval[0]["revision"], 1)
        self.assertEqual(
            public.export_snapshot(self.ledger, mode="review", as_of=NOW)["events"][0]["summary"],
            "A revised explanation.",
        )
        public.set_review_state(self.ledger, identifier, "approved", at=NOW)
        snapshot = published(self.ledger)
        self.assertEqual(len(snapshot["events"]), 1)
        day = next(row for row in snapshot["days"] if row["date"] == "2026-10-03")
        self.assertEqual(day["total"], 1)
        self.assertEqual(sum(day["counts"].values()), 1)
        self.assertEqual(day["counts"]["flight-patterns"], 1)
        self.assertEqual(
            self.ledger["records"][0]["history"][0]["editorial"]["summary"], editorial()["summary"]
        )

    def test_full_inventory_over_one_hundred_events_is_counted_and_searchable(self):
        for index in range(125):
            day = date(2026, 9, 20) + timedelta(days=index // 9)
            add(
                self.ledger,
                key=f"episode-{index}",
                metadata=editorial(path=story_path(f"event-{index}")),
                evidence=packet(f"{day}T12:00:00Z", f"{day}T12:10:00Z"),
            )
        snapshot = published(self.ledger)
        self.assertEqual(len(snapshot["events"]), 125)
        self.assertEqual(len(snapshot["search"]), 125)
        self.assertEqual(sum(day["total"] or 0 for day in snapshot["days"]), 125)

    def test_publication_ceiling_is_nine_per_observation_date_review_is_unlimited(self):
        for index in range(9):
            add(self.ledger, key=f"episode-{index}", metadata=editorial(path=story_path(f"event-{index}")))
        self.assertEqual(len(published(self.ledger)["events"]), 9)
        add(self.ledger, key="tenth", metadata=editorial(path=story_path("tenth")))
        with self.assertRaisesRegex(ValueError, "nine ordinary events"):
            published(self.ledger)
        review = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        self.assertEqual(len(review["events"]), 10)
        review.update(mode="published", publication_status="published")
        for event in review["events"]:
            event.update(publication_status="published", published_at=NOW)
        with self.assertRaisesRegex(ValueError, "nine ordinary events"):
            public.validate_snapshot(review)

    def test_utc_midnight_crossing_counts_start_date_only(self):
        evidence = packet("2026-10-03T19:55:00-04:00", "2026-10-03T20:05:00-04:00")
        add(self.ledger, evidence=evidence)
        snapshot = published(self.ledger)
        self.assertEqual(snapshot["events"][0]["observed_start"], "2026-10-03T23:55:00Z")
        self.assertEqual(snapshot["days"][-2]["total"], 1)
        self.assertEqual(snapshot["days"][-1]["total"], 0)
        self.assertTrue(snapshot["days"][-1]["partial"])

    def test_observation_sort_and_hash_stay_stable_across_record_order(self):
        earlier = add(self.ledger, key="old", evidence=packet("2026-09-01T00:00:00Z", "2026-09-01T01:00:00Z"))
        later = add(self.ledger, key="new", metadata=editorial(path=story_path("newer")))
        tied = add(self.ledger, key="tied", metadata=editorial(path=story_path("tied")))
        first = published(self.ledger)
        self.assertEqual([event["id"] for event in first["events"]], [*sorted((later, tied)), earlier])
        self.ledger["records"].reverse()
        second = published(self.ledger)
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(first["events"], second["events"])

    def test_unavailable_days_are_distinct_from_zero_and_old_feed_entries_are_kept(self):
        add(self.ledger, evidence=packet("2026-09-10T00:00:00Z", "2026-09-10T00:20:00Z"))
        snapshot = published(self.ledger)
        self.assertEqual(len(snapshot["days"]), 30)
        self.assertEqual(len(snapshot["events"]), 1)
        self.assertEqual(snapshot["window"]["start"], "2026-09-05")
        unknown = next(row for row in snapshot["days"] if row["date"] == "2026-09-10")
        self.assertIsNone(unknown["total"])
        self.assertIsNone(unknown["counts"])
        self.assertEqual(unknown["status"], "unavailable")
        self.assertEqual(snapshot["days"][-1]["total"], 0)

    def test_withdrawals_and_publication_visibility_update_all_views(self):
        identifier = add(self.ledger)
        for field, value in (
            ("status", "draft"),
            ("status", "scheduled"),
            ("visibility", "members"),
            ("visibility", "paid"),
        ):
            with self.subTest(field=field, value=value):
                source = inventory(self.ledger)
                source["posts"][0][field] = value
                snapshot = published(self.ledger, source)
                self.assertEqual(snapshot["events"], [])
                self.assertEqual(snapshot["search"], [])
                self.assertEqual(sum(day["total"] or 0 for day in snapshot["days"]), 0)
        public.set_review_state(self.ledger, identifier, "withdrawn", at=NOW)
        self.assertEqual(published(self.ledger)["events"], [])
        self.assertEqual(public.export_snapshot(self.ledger, mode="review", as_of=NOW)["events"], [])

    def test_a_released_story_that_is_withdrawn_leaves_only_its_path_type_and_time(self):
        cessna = {"code": "C172", "name": "Cessna 172", "slug": "cessna-172"}
        routing = {"topic": "ads-b-glitches", "section": "data-quality", "type": cessna}
        released = add(
            self.ledger, key="released", metadata=editorial(story_path("released"), context=routing)
        )
        untyped = add(self.ledger, key="untyped", metadata=editorial(story_path("untyped")))
        never = add(self.ledger, key="never", metadata=editorial(story_path("never")), approved=False)
        live = add(self.ledger, key="live", metadata=editorial(story_path("live")))
        for identifier in (released, untyped, live):
            public.find_record(self.ledger, identifier)["automation"] = {"released_at": NOW}
        for identifier in (released, untyped, never):
            public.set_review_state(self.ledger, identifier, "withdrawn", at=NOW)
        snapshot = published(self.ledger)
        self.assertEqual([event["article_path"] for event in snapshot["events"]], [story_path("live")])
        # Sorted by path; a story never released has no public path to retire.
        self.assertEqual(
            snapshot["retired"],
            [
                {"path": story_path("released"), "type": "cessna-172", "withdrawn_at": NOW},
                {"path": story_path("untyped"), "type": None, "withdrawn_at": NOW},
            ],
        )
        # Nothing else from the record: no evidence, identity, prose or private ID.
        text = json.dumps(snapshot["retired"])
        for private in ("N12345", "Bell 407", "Helicopter", public.find_record(self.ledger, released)["id"]):
            self.assertNotIn(private, text)
        self.assertEqual(public.validate_snapshot(snapshot), snapshot)
        # A review export never lists withdrawn stories.
        self.assertEqual(public.export_snapshot(self.ledger, mode="review", as_of=NOW)["retired"], [])

    def test_a_withdrawn_story_counts_as_released_by_an_approved_revision_with_a_release(self):
        identifier = add(self.ledger)
        record = public.find_record(self.ledger, identifier)
        record["automation"] = {"released_at": NOW}
        public.put_event(self.ledger, "episode-a", packet(), editorial(summary="A revised summary."), at=NOW)
        record["automation"] = {}
        public.set_review_state(self.ledger, identifier, "withdrawn", at=NOW)
        self.assertEqual([entry["path"] for entry in published(self.ledger)["retired"]], [STORY_PATH])
        # Without that release the approved revision alone does not make a public path.
        record["history"][0]["automation"] = {}
        self.assertEqual(published(self.ledger)["retired"], [])

    def test_withdrawal_times_stop_at_the_snapshot_and_the_latest_withdrawals_are_kept(self):
        for index, minute in enumerate((10, 30, 20)):
            identifier = add(
                self.ledger, key=f"episode-{index}", metadata=editorial(story_path(f"story-{index}"))
            )
            public.find_record(self.ledger, identifier)["automation"] = {"released_at": NOW}
            public.set_review_state(self.ledger, identifier, "withdrawn", at=f"2026-10-04T20:{minute}:00Z")

        def export(as_of):
            source = {**inventory(self.ledger), "as_of_utc": as_of}
            return public.export_snapshot(self.ledger, mode="published", as_of=as_of, inventory=source)

        # A withdrawal recorded after the snapshot time is dated at the snapshot.
        self.assertEqual(
            [entry["withdrawn_at"] for entry in export("2026-10-04T20:15:00Z")["retired"]],
            ["2026-10-04T20:10:00Z", "2026-10-04T20:15:00Z", "2026-10-04T20:15:00Z"],
        )
        with patch.object(public, "MAX_RETIRED", 2):
            kept = export("2026-10-04T21:00:00Z")["retired"]
        self.assertEqual([entry["path"] for entry in kept], [story_path("story-1"), story_path("story-2")])

    def test_the_withdrawn_story_list_is_required_and_validated(self):
        identifier = add(self.ledger)
        public.find_record(self.ledger, identifier)["automation"] = {"released_at": NOW}
        add(self.ledger, key="other", metadata=editorial(story_path("other")))
        public.set_review_state(self.ledger, identifier, "withdrawn", at=NOW)
        good = published(self.ledger)
        entry = good["retired"][0]
        changes = [
            lambda value: value.pop("retired"),
            lambda value: value.update(retired={}),
            lambda value: value["retired"][0].update(registration="N12345"),
            lambda value: value["retired"][0].pop("type"),
            lambda value: value["retired"][0].update(path="/an-old-story/"),
            lambda value: value["retired"][0].update(path="/stories/x/"),
            lambda value: value["retired"][0].update(path=None),
            lambda value: value["retired"][0].update(type="Cessna 172"),
            lambda value: value["retired"][0].update(type=""),
            lambda value: value["retired"][0].update(withdrawn_at="2026-10-05T00:00:00Z"),
            lambda value: value["retired"][0].update(withdrawn_at="2026-10-04T20:00:00+00:00"),
            lambda value: value["retired"].append(dict(entry)),
            lambda value: value["retired"].insert(0, {**entry, "path": story_path("zzz")}),
            lambda value: value.update(
                retired=[{**entry, "path": story_path(f"s{i:03d}")} for i in range(public.MAX_RETIRED + 1)]
            ),
        ]

        def redigest(value):
            value["snapshot_id"] = public._digest({k: v for k, v in value.items() if k != "snapshot_id"})
            return value

        for index, change in enumerate(changes):
            with self.subTest(index=index):
                bad = deepcopy(good)
                change(bad)
                with self.assertRaises(ValueError):
                    public.validate_snapshot(redigest(bad))
        # A review snapshot validates without withdrawn stories and is refused with one.
        review = deepcopy(good)
        review.update(mode="review", publication_status="unpublished", retired=[])
        for event in review["events"]:
            event.update(publication_status="unpublished", published_at=None)
        public.validate_snapshot(redigest(review))
        review["retired"] = deepcopy(good["retired"])
        with self.assertRaisesRegex(ValueError, "withdrawn stories"):
            public.validate_snapshot(redigest(review))
        # The snapshot digest covers the list, so a changed list needs a new digest.
        changed = deepcopy(good)
        changed["retired"] = []
        with self.assertRaisesRegex(ValueError, "does not match its content"):
            public.validate_snapshot(changed)
        changed["snapshot_id"] = public._digest({k: v for k, v in changed.items() if k != "snapshot_id"})
        self.assertNotEqual(changed["snapshot_id"], good["snapshot_id"])
        self.assertEqual(public.validate_snapshot(changed), changed)

    def test_missing_ghost_entry_is_a_withdrawal_or_deletion(self):
        add(self.ledger)
        snapshot = published(self.ledger, {"complete": True, "total": 0, "as_of_utc": NOW, "posts": []})
        self.assertEqual(snapshot["events"], [])

    def test_withdrawal_removes_previous_approved_revisions_even_after_new_pending_edit(self):
        identifier = add(self.ledger)
        public.put_event(self.ledger, "episode-a", packet(), editorial(summary="New draft."), at=NOW)
        self.assertEqual(len(published(self.ledger)["events"]), 1)
        public.set_review_state(self.ledger, identifier, "withdrawn", at=NOW)
        public.put_event(self.ledger, "episode-a", packet(), editorial(summary="Later draft."), at=NOW)
        self.assertEqual(published(self.ledger)["events"], [])
        public.set_review_state(self.ledger, identifier, "approved", at=NOW)
        self.assertEqual(published(self.ledger)["events"][0]["summary"], "Later draft.")

    def test_ghost_post_id_prevents_reused_slug_from_republishing_old_event(self):
        add(self.ledger)
        source = inventory(self.ledger)
        source["posts"][0]["id"] = "reused-slug-different-post-id"
        self.assertEqual(published(self.ledger, source)["events"], [])
        self.assertNotIn("ghost_id", json.dumps(published(self.ledger)))

    def test_callsign_and_address_do_not_imply_a_database_identity_match(self):
        evidence = packet()
        evidence["aircraft"] = {"callsign": "TEST123", "icao_address": "a12345"}
        evidence["received"]["source"] = "adsb_icao"
        add(self.ledger, evidence=evidence)
        event = published(self.ledger)["events"][0]
        self.assertFalse(any("database match" in text for text in event["limitations"]))
        evidence["aircraft"]["type_code"] = "B407"
        add(self.ledger, key="matched", evidence=evidence, metadata=editorial(path=story_path("matched")))
        event = next(
            event for event in published(self.ledger)["events"] if event["aircraft"].get("type_code")
        )
        self.assertTrue(any("database match" in text for text in event["limitations"]))

    def test_audit_times_cannot_move_backwards_or_change_ledger_on_rejection(self):
        identifier = add(self.ledger, approved=False)
        before = deepcopy(self.ledger)
        with self.assertRaisesRegex(ValueError, "precede"):
            public.set_review_state(self.ledger, identifier, "approved", at="2026-01-01T00:00:00Z")
        self.assertEqual(self.ledger, before)
        public.set_review_state(self.ledger, identifier, "approved", at="2026-10-04T20:10:00Z")
        before = deepcopy(self.ledger)
        with self.assertRaisesRegex(ValueError, "precede"):
            public.put_event(self.ledger, "episode-a", packet(), editorial(), at="2026-10-04T20:05:00Z")
        self.assertEqual(self.ledger, before)
        with self.assertRaisesRegex(ValueError, "precede"):
            public.set_review_state(self.ledger, identifier, "withdrawn", at=NOW)
        self.assertEqual(self.ledger, before)
        for field in ("updated_at", "reviewed_at"):
            invalid = deepcopy(self.ledger)
            invalid["records"][0][field] = "2026-01-01T00:00:00Z"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "precede"):
                public.export_snapshot(invalid, mode="review", as_of="2026-10-04T21:00:00Z")

    def test_inventory_must_be_fresh_complete_and_explicit(self):
        add(self.ledger)
        invalid = [None, {}, {"complete": True, "posts": []}]
        for key, value in (
            ("complete", False),
            ("complete", 1),
            ("total", 2),
            ("as_of_utc", "2026-10-04T19:44:59Z"),
            ("as_of_utc", "2026-10-04T20:00:01Z"),
        ):
            invalid.append({**inventory(self.ledger), key: value})
        for field in ("id", "status", "visibility", "published_at", "updated_at"):
            source = inventory(self.ledger)
            del source["posts"][0][field]
            invalid.append(source)
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(ValueError):
                public.export_snapshot(self.ledger, mode="published", as_of=NOW, inventory=source)

    def test_synthetic_records_are_review_only_and_review_never_claims_publication(self):
        add(self.ledger, metadata=editorial(synthetic=True), approved=False)
        review = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        self.assertEqual(review["publication_status"], "unpublished")
        self.assertEqual(review["events"][0]["publication_status"], "unpublished")
        self.assertIsNone(review["events"][0]["published_at"])
        self.assertTrue(review["events"][0]["synthetic"])
        public.set_review_state(self.ledger, review["events"][0]["id"], "approved", at=NOW)
        self.assertEqual(published(self.ledger)["events"], [])

    def test_evidence_survives_original_data_changes_and_retention(self):
        evidence = packet()
        add(self.ledger, evidence=evidence)
        evidence.clear()
        snapshot = published(self.ledger)
        self.assertEqual(snapshot["events"][0]["measurements"][0]["value"], 70)

    def test_limitation_sentences_use_the_serial_comma(self):
        from wtf_adsb_publisher import public_prose

        texts = [text for texts in public.KIND_LIMITATIONS.values() for text in texts]
        texts += [public.MISSION, public.GNSS, public.TYPE_MISMATCH, public.WATCHLIST_SCOPE]
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(public_prose.serial_comma_issues(text), [])
        self.assertIn("a mission, purpose, or activity", public.MISSION)
        self.assertIn("an address error, or corrupted data", public.TYPE_MISMATCH)

    def test_public_projection_drops_private_keys_and_preserves_measurement_scope(self):
        evidence = packet()
        evidence.update(receiver_lat=12.345678, hostname="example.ts.net", row_id=424242)
        evidence["aircraft"].update(receiver_lon=-76.543219, r_dst=88, r_dir=99)
        evidence["pattern"].update(receiver_lat=12.345678, private_notes="PRIVATE SENTENCE")
        evidence["received"]["readings"].append({"field": "receiver_lat", "value": 12.345678})
        add(self.ledger, key="PRIVATE EPISODE", evidence=evidence)
        snapshot = published(self.ledger)
        encoded = json.dumps(snapshot)
        for token in (
            "12.345678",
            "-76.543219",
            "example.ts.net",
            "424242",
            "PRIVATE",
            "r_dst",
            "r_dir",
            "row_id",
        ):
            self.assertNotIn(token, encoded)
        measurement = snapshot["events"][0]["measurements"][0]
        self.assertEqual(measurement["scope"], "saved-report")
        self.assertEqual(measurement["unit"], "kn")
        self.assertEqual(measurement["at_utc"], "2026-10-03T23:55:00Z")
        pattern = snapshot["events"][0]["measurements"][1]
        self.assertEqual(pattern["scope"], "analysis-window")
        self.assertEqual(pattern["end_utc"], "2026-10-04T00:05:00Z")

    def test_categorical_ground_and_transponder_reports_keep_their_timestamp(self):
        evidence = packet(kind="special_squawk_7700")
        evidence["received"].update(source="mlat", squawk="7700", emergency_status="general")
        evidence["received"]["readings"].append({"field": "alt_baro", "value": "ground"})
        evidence["pattern"].update(quality_review_required=True, shape_verified=False)
        add(self.ledger, evidence=evidence)
        event = published(self.ledger)["events"][0]
        readings = {item["key"]: item for item in event["measurements"]}
        self.assertEqual(readings["alt_baro"]["value"], "ground")
        self.assertEqual(readings["alt_baro"]["unit"], "")
        self.assertEqual(readings["squawk"]["value"], "7700")
        self.assertEqual(readings["squawk"]["at_utc"], evidence["received"]["saved_readings_at_utc"])
        self.assertEqual(readings["emergency_status"]["value"], "general")
        self.assertEqual(event["source"], "mlat")
        self.assertTrue(any("MLAT" in item for item in event["limitations"]))
        self.assertTrue(any("requires review" in item for item in event["limitations"]))
        self.assertTrue(any("does not confirm" in item for item in event["limitations"]))

    def test_malformed_transponder_values_fail_closed(self):
        for field, value in (("squawk", "8888"), ("squawk", 7700), ("emergency_status", "unknown-emergency")):
            evidence = packet(kind="special_squawk_7700")
            evidence["received"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                add(self.ledger, evidence=evidence)

    def test_snapshot_import_rejects_unknown_nested_fields_and_mismatched_views(self):
        add(self.ledger)
        good = published(self.ledger)
        self.assertEqual(public.validate_snapshot(good), good)
        for target in ("root", "event", "aircraft", "measurement", "reference", "day", "search"):
            bad = deepcopy(good)
            destinations = {
                "root": bad,
                "event": bad["events"][0],
                "aircraft": bad["events"][0]["aircraft"],
                "measurement": bad["events"][0]["measurements"][0],
                "reference": bad["events"][0]["references"][0],
                "day": bad["days"][0],
                "search": bad["search"][0],
            }
            destinations[target]["receiver_lat"] = 12.345678
            with self.subTest(target=target), self.assertRaises(ValueError):
                public.validate_snapshot(bad)
        for field, value in (("total", 900), ("status", "unavailable"), ("partial", False)):
            bad = deepcopy(good)
            bad["days"][-1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                public.validate_snapshot(bad)
        bad = deepcopy(good)
        bad["events"][0]["measurements"][0]["scope"] = "receiver-relative"
        with self.assertRaises(ValueError):
            public.validate_snapshot(bad)
        bad = deepcopy(good)
        bad["snapshot_id"] = "f" * 64
        with self.assertRaises(ValueError):
            public.validate_snapshot(bad)

    def test_approved_content_cannot_be_edited_in_place_without_reapproval(self):
        add(self.ledger)
        self.ledger["records"][0]["editorial"]["summary"] = "Edited after review."
        with self.assertRaisesRegex(ValueError, "without review"):
            published(self.ledger)

    def test_unrecognized_kinds_and_malformed_measurements_fail_closed(self):
        for kind in ("distant", "unknown_kind", {"receiver_lat": 123}):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                add(self.ledger, evidence=packet(kind=kind))
        for value in (True, "70", float("nan"), float("inf")):
            evidence = packet()
            evidence["received"]["readings"][0]["value"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                add(self.ledger, evidence=evidence)
        evidence = packet()
        evidence["schema_version"] = 99
        with self.assertRaisesRegex(ValueError, "packet version"):
            add(self.ledger, evidence=evidence)

    def test_private_hosts_markup_and_invalid_public_paths_are_rejected(self):
        for path in (
            "https://evil.example/story/",
            "//evil.example/story/",
            "/ghost/",
            "/a/../ghost/",
            "/%2e%2e/ghost/",
            "/article/?receiver_lat=1",
            "/a\\b/",
            "/story/#secret",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                add(self.ledger, metadata=editorial(path=path))
        for summary in (
            "Private endpoint example.ts.net",
            "<script>bad()</script>",
            "The rssi field read -12.",
            "Field rr_lat was present.",
            "Reached through 100.101.12.7 only.",
        ):
            with self.subTest(summary=summary), self.assertRaises(ValueError):
                add(self.ledger, metadata=editorial(summary=summary))
        # Ordinary numbers and words that only contain a private name stay public.
        add(
            self.ledger,
            key="ordinary",
            metadata=editorial(story_path("ordinary"), summary="Crossing 100.5 knots, a crossing pass."),
        )
        evidence = packet()
        evidence["links"][0]["url"] = "https://127.0.0.1/private"
        with self.assertRaises(ValueError):
            add(self.ledger, evidence=evidence)

    def test_two_episodes_cannot_share_a_counted_article(self):
        add(self.ledger)
        before = deepcopy(self.ledger)
        with self.assertRaisesRegex(ValueError, "share one event page"):
            add(self.ledger, key="different-episode")
        self.assertEqual(before, self.ledger)

    def test_naive_future_or_reversed_times_are_rejected(self):
        for evidence in (
            packet("2026-10-03T23:55:00", "2026-10-04T00:05:00Z"),
            packet("2026-10-04T01:00:00Z", "2026-10-04T00:05:00Z"),
        ):
            with self.assertRaises(ValueError):
                add(self.ledger, evidence=evidence)
        add(self.ledger, evidence=packet("2026-10-05T00:00:00Z", "2026-10-05T00:05:00Z"))
        with self.assertRaises(ValueError):
            published(self.ledger)

    def test_atomic_files_are_complete_and_private_ledger_permissions_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            public.write_json_atomic(path, self.ledger, private=True)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                public.write_json_atomic(path, {"bad": float("nan")}, private=True)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(public.read_json(path), self.ledger)
            path.write_text('{"field":1,"field":2}')
            with self.assertRaises(ValueError):
                public.read_json(path)

    def test_cli_init_put_review_export_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            ledger = path / "ledger.json"
            evidence = path / "evidence.json"
            evidence.write_text(json.dumps(packet()))
            command = [
                sys.executable,
                str(ROOT / "tools" / "export_public_events.py"),
                "--ledger",
                str(ledger),
            ]

            def run(*arguments):
                return subprocess.run([*command, *arguments], capture_output=True, text=True, check=False)

            self.assertEqual(run("init", "--available-from", "2026-09-20").returncode, 0)
            self.assertNotEqual(run("init", "--available-from", "2026-09-01").returncode, 0)
            result = run(
                "put",
                "--episode-key",
                "private-episode",
                "--evidence",
                str(evidence),
                "--title",
                editorial()["title"],
                "--summary",
                editorial()["summary"],
                "--article-path",
                story_path("review-event"),
                "--at",
                NOW,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = path / "snapshot.json"
            result = run("export", "--mode", "review", "--as-of", NOW, "--output", str(output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(public.read_json(output)["mode"], "review")
            self.assertNotIn("private-episode", output.read_text())
            validated = path / "validated.json"
            result = run("validate-snapshot", "--input", str(output), "--output", str(validated))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), validated.read_bytes())
            before = ledger.read_bytes()
            self.assertNotEqual(
                run("export", "--mode", "review", "--as-of", NOW, "--output", str(ledger)).returncode, 0
            )
            self.assertEqual(before, ledger.read_bytes())


def episode_packet(kind="rapid_descent", **changes):
    evidence = packet(kind=kind)
    evidence.pop("pattern")
    evidence["maneuver"] = {
        "confirmed": True,
        "quality_review_required": False,
        "limitations": [],
        "start_utc": "2026-10-03T23:56:00Z",
        "end_utc": "2026-10-03T23:58:00Z",
        "peak_at_utc": "2026-10-03T23:57:10Z",
        "duration_seconds": 120,
        "sample_count": 12,
        "peak_rate_fpm": -7600,
        "median_rate_fpm": -7400,
        "threshold_fpm": 4500,
        "qualifying_duration_seconds": 60,
        "qualifying_sample_count": 7,
        "footprint_nm": 0.1,
        **changes,
    }
    return evidence


def projection(evidence, path=EVENT_PATH):
    return public._projection(evidence, editorial(path=path))


def keyed(event):
    return {(item["key"], item["scope"]): item for item in event["measurements"]}


class PublicOutputTests(unittest.TestCase):
    def setUp(self):
        self.ledger = public.new_ledger("2026-09-20")

    def test_contract_kinds_have_public_types_and_status_kinds_stay_transponder_reports(self):
        expected = {
            "tcas_ra": "transponder-reports",
            "special_squawk_7400": "transponder-reports",
            "archive_squawk_7400": "transponder-reports",
            "special_use_code": "transponder-reports",
            "stratospheric_balloon": "unusual-aircraft",
            "formation_flight": "flight-patterns",
            "holding_stack": "flight-patterns",
            "gnss_integrity_loss": "data-quality",
            "gnss_integrity_cluster": "data-quality",
            "survey_grid": "flight-patterns",
            "thermal_soaring": "flight-patterns",
            "selected_altitude_overshoot": "performance",
            "extreme_turn_rate": "flight-patterns",
            "identity_conflict": "data-quality",
            "first_type_here": "unusual-aircraft",
            "first_country_here": "unusual-aircraft",
            "notable_variant": "unusual-aircraft",
            "emitter_type_mismatch": "data-quality",
            "type_speed_mismatch": "data-quality",
            "type_altitude_mismatch": "data-quality",
            "seasonal_speed_record": "performance",
        }
        for kind, primary in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(public.KIND_TYPES[kind], primary)
                self.assertEqual(projection(packet(kind=kind))["primary_type"], primary)

    def test_limitations_follow_kind_and_claim_basis(self):
        required = {
            "hovering": public.MISSION,
            "descending_spiral": public.MISSION,
            "possible_go_around": public.MISSION,
            "rapid_descent": public.NOT_EMERGENCY,
            "special_squawk_7700": public.NOT_EMERGENCY,
            "archive_squawk_7600": public.CODE_ALONE,
            "fast": public.GROUND_SPEED,
            "outlier_gs": public.GROUND_SPEED,
            "high": public.ALTITUDE_REFERENCES,
            "type_altitude_mismatch": public.TYPE_MISMATCH,
            "watchlist_match": public.WATCHLIST_SCOPE,
            "first_type_here": public.FIRST_HERE,
            "gnss_integrity_cluster": public.GNSS,
        }
        for kind, sentence in required.items():
            evidence = packet(kind=kind)
            evidence.pop("pattern")
            with self.subTest(kind=kind):
                self.assertIn(sentence, projection(evidence)["limitations"])
        wind = projection(packet(kind="wind_boost"))["limitations"]
        self.assertTrue(any("not a measured wind vector" in item for item in wind))
        self.assertEqual(public.claim_basis("hovering"), "geometry")
        self.assertEqual(public.claim_basis("special_use_code"), "status")
        self.assertEqual(public.claim_basis("watch_type"), "database")
        self.assertEqual(public.claim_basis("rapid_descent"), "telemetry")
        evidence = packet(kind="watch_type")
        evidence["identity_checks"] = {"conflict": True}
        evidence["context_tags"] = ["metadata_identity_conflict", "not_a_tag"]
        limits = projection(evidence)["limitations"]
        self.assertIn(public.IDENTITY_CONFLICT, limits)
        self.assertIn(public.episode_evidence.CONTEXT_NOTES["metadata_identity_conflict"], limits)
        self.assertLessEqual(len(limits), public.MAX_LIMITATIONS)

    def test_episode_peak_rule_and_window_rows_have_their_own_scopes(self):
        event = projection(episode_packet())
        rows = keyed(event)
        self.assertEqual(rows[("peak_rate_fpm", "peak-report")]["at_utc"], "2026-10-03T23:57:10Z")
        self.assertNotIn("start_utc", rows[("peak_rate_fpm", "peak-report")])
        self.assertEqual(rows[("threshold_fpm", "detection-rule")]["value"], 4500)
        self.assertEqual(
            set(rows[("threshold_fpm", "detection-rule")]), {"key", "label", "value", "unit", "scope"}
        )
        labels = [item["label"] for item in event["measurements"]]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(rows[("footprint_nm", "analysis-window")]["unit"], "NM")
        self.assertIn(public.SIGN_NOTE, event["limitations"])
        self.assertIn(public.NOT_EMERGENCY, event["limitations"])

    def test_tampered_scopes_fail_snapshot_validation(self):
        identifier = add(
            self.ledger, evidence=episode_packet(), metadata=editorial(path=story_path("descent"))
        )
        good = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        self.assertTrue(identifier)

        def tamper(change):
            bad = deepcopy(good)
            change(bad["events"][0]["measurements"])
            bad["events"][0]["evidence_version"] = public.evidence_version(bad["events"][0])
            bad["snapshot_id"] = public._digest({k: v for k, v in bad.items() if k != "snapshot_id"})
            return bad

        def find(items, key):
            return next(item for item in items if item["key"] == key)

        for name, change in (
            ("rule with time", lambda items: find(items, "threshold_fpm").update(at_utc=NOW)),
            ("rule as window", lambda items: find(items, "threshold_fpm").update(scope="analysis-window")),
            (
                "peak with window",
                lambda items: find(items, "peak_rate_fpm").update(start_utc=NOW, end_utc=NOW),
            ),
            (
                "status none",
                lambda items: items.append(
                    {
                        "key": "emergency_status",
                        "label": "Reported emergency status",
                        "value": "none",
                        "unit": "",
                        "scope": "saved-report",
                    }
                ),
            ),
            (
                "placeholder code",
                lambda items: items.append(
                    {
                        "key": "squawk",
                        "label": "Transponder code",
                        "value": "0000",
                        "unit": "",
                        "scope": "saved-report",
                    }
                ),
            ),
            (
                "label window",
                lambda items: items.append(
                    {
                        "key": "callsign",
                        "label": "Callsign",
                        "value": "TEST123",
                        "unit": "",
                        "scope": "archived-label",
                        "start_utc": NOW,
                        "end_utc": NOW,
                    }
                ),
            ),
            (
                "bad sequence",
                lambda items: items.append(
                    {
                        "key": "code_sequence",
                        "label": "Transponder code sequence",
                        "value": "7700 then <b>",
                        "unit": "",
                        "scope": "analysis-window",
                    }
                ),
            ),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                public.validate_snapshot(tamper(change))

    def test_episode_text_reports_are_allowlisted(self):
        evidence = episode_packet(
            kind="special_use_code",
            code="1277",
            advisory="Level Off",
            code_sequence=[{"code": "7700", "seconds": 95}, {"code": "7600", "seconds": 60}],
        )
        rows = keyed(projection(evidence))
        self.assertEqual(rows[("code", "analysis-window")]["value"], "1277")
        self.assertEqual(rows[("advisory", "analysis-window")]["value"], "Level Off")
        self.assertEqual(
            rows[("code_sequence", "analysis-window")]["value"], "7700 for 95 s, then 7600 for 60 s"
        )
        for field, value in (
            ("code", "0000"),
            ("code", "8888"),
            ("advisory", "Call me"),
            ("advisory_complement", "Do not call"),
            ("code_sequence", [{"code": "x"}]),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                projection(episode_packet(kind="special_use_code", **{field: value}))
        rows = keyed(projection(episode_packet(kind="tcas_ra", advisory_complement="Do not pass below")))
        self.assertEqual(rows[("advisory_complement", "analysis-window")]["value"], "Do not pass below")

    def test_loop_counts_keep_ground_and_drift_corrected_loops_apart(self):
        evidence = packet(kind="circling")
        evidence["pattern"] = {
            "kind": "circling",
            "duration_seconds": 600.0,
            "closed_loops": 3,
            "ground_closed_loops": 1,
            "drift_corrected_loops": 3,
            "orbit_center_drift_nm": 0.4,
            "observed_sources": ["adsb_icao"],
            "quality_review_required": False,
            "start_utc": "2026-10-03T23:55:00Z",
            "end_utc": "2026-10-04T00:05:00Z",
        }
        evidence["context_tags"] = ["drifting_orbit"]
        projected = projection(evidence)
        rows = keyed(projected)
        self.assertEqual(rows[("closed_loops", "analysis-window")]["value"], 3)
        self.assertEqual(rows[("ground_closed_loops", "analysis-window")]["value"], 1)
        self.assertEqual(rows[("drift_corrected_loops", "analysis-window")]["value"], 3)
        self.assertEqual(rows[("orbit_center_drift_nm", "analysis-window")]["unit"], "NM")
        self.assertIn(public.episode_evidence.CONTEXT_NOTES["drifting_orbit"], projected["limitations"])

    def test_archived_labels_never_borrow_the_position_time(self):
        evidence = packet(kind="watch_type")
        evidence["aircraft"]["callsign"] = "RCH2023"
        evidence["received"].update(squawk="4102", label_provenance="archived_label")
        rows = keyed(projection(evidence))
        self.assertEqual(set(rows[("squawk", "archived-label")]), {"key", "label", "value", "unit", "scope"})
        self.assertNotIn(("squawk", "saved-report"), rows)
        evidence["received"]["label_at_utc"] = "2026-10-03T23:54:00Z"
        rows = keyed(projection(evidence))
        self.assertEqual(rows[("callsign", "archived-label")]["at_utc"], "2026-10-03T23:54:00Z")
        self.assertEqual(rows[("squawk", "archived-label")]["at_utc"], "2026-10-03T23:54:00Z")
        evidence["received"]["label_provenance"] = "saved_report"
        rows = keyed(projection(evidence))
        self.assertEqual(rows[("squawk", "saved-report")]["at_utc"], "2026-10-03T23:55:00Z")
        evidence["received"]["label_provenance"] = "guessed"
        with self.assertRaises(ValueError):
            projection(evidence)

    def test_participants_are_projected_hashed_validated_and_rendered_safely(self):
        evidence = packet(kind="formation_flight")
        evidence["aircraft"]["icao_address"] = "A12345"
        evidence["participants"] = [
            {
                "icao_address": "A22222",
                "role": "wingman",
                "registration": "N22222",
                "type_code": "C130",
                "source": "adsb_icao",
                "measurements": {"min_separation_nm": 0.3, "vertical_separation_ft": 200},
            },
            {"icao_address": "A12345", "role": "lead", "source": "adsb_icao", "measurements": {}},
        ]
        add(self.ledger, evidence=evidence)
        event = published(self.ledger)["events"][0]
        self.assertEqual(len(event["participants"]), 1)
        other = event["participants"][0]
        self.assertEqual(
            other["measurements"][0],
            {
                "key": "min_separation_nm",
                "label": "Closest horizontal separation between the aircraft",
                "value": 0.3,
                "unit": "NM",
            },
        )
        without = projection(packet(kind="formation_flight"), path=story_path("other"))
        self.assertNotIn("participants", without)
        for change in (
            lambda entry: entry.update(receiver_lat=12.345678),
            lambda entry: entry.update(role="spy"),
            lambda entry: entry.update(icao_address="A12345"),
            lambda entry: entry["measurements"].append(
                {"key": "r_dst", "label": "Range", "value": 4, "unit": "NM"}
            ),
        ):
            bad = deepcopy(published(self.ledger))
            change(bad["events"][0]["participants"][0])
            bad["events"][0]["evidence_version"] = public.evidence_version(bad["events"][0])
            bad["snapshot_id"] = public._digest({k: v for k, v in bad.items() if k != "snapshot_id"})
            with self.subTest(change=change), self.assertRaises(ValueError):
                public.validate_snapshot(bad)
        evidence["participants"][0]["role"] = "spy"
        with self.assertRaises(ValueError):
            projection(evidence, path=story_path("spy"))

    def test_public_references_need_a_canonical_public_hostname(self):
        private = (
            "https://office.local./",
            "https://admin.internal./",
            "https://Office.LOCAL/",
            "https://127.1/",
            "https://0x7f.0.0.1/",
            "https://0177.0.0.1/",
            "https://2130706433/",
            "https://10.0.0.1/jane",
            "https://[::1]/",
            "https://[::ffff:127.0.0.1]/",
            "https://[2001:db8::1]/page",
            "https://printer.lan/",
            "https://user:pw@www.example.com/",
            "https://www.example.com:8443/",
            "http://www.example.com/",
        )
        for url in private:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public.public_url(url)
        for url in (
            "https://www.example.com/page",
            "https://WWW.Example.COM./page",
            "https://github.com/sdr-enthusiasts/plane-alert-db",
            "https://1.2.3.4.example.com/",
            "https://xn--bcher-kva.example/",
            "https://www.example.com:443/",
        ):
            with self.subTest(url=url):
                self.assertEqual(public.public_url(url), url)
        self.assertEqual(public.public_host("WWW.Example.COM."), "www.example.com")
        self.assertIsNone(public.public_host("0x7f.0.0.1"))
        # The full projection path: a private link leaves the watchlist block, and a private
        # reference or source fails the event.
        commit = "c" * 40
        evidence = packet(kind="watchlist_match")
        evidence["watchlist"] = {
            "source_name": "Plane Alert DB",
            "association_label": "Jane Example",
            "category": "Public figure association",
            "link": "https://office.local./jane",
            "source_commit": commit,
            "attribution": public.WATCHLIST_ATTRIBUTION,
            "licenses": [dict(item) for item in public.watchlists.LICENSES],
            "claim_scope": public.WATCHLIST_CLAIM_SCOPE,
        }
        projected = projection(evidence, path=story_path("jane"))
        self.assertNotIn("link", projected["watchlist"])
        self.assertNotIn("office.local", json.dumps(projected))
        for url in ("https://0x7f.0.0.1/ref", "https://admin.internal./ref"):
            tainted = deepcopy(evidence)
            tainted["links"] = [{"id": "x", "label": "X", "url": url}]
            with self.subTest(url=url), self.assertRaises(ValueError):
                projection(tainted, path=story_path("jane"))
        self.assertEqual(public.public_url("https://example.com./page"), "https://example.com./page")

    def test_watchlist_block_carries_attribution_licences_and_claim_scope(self):
        commit = "c" * 40
        evidence = packet(kind="watchlist_match")
        evidence["watchlist"] = {
            "source_name": "Plane Alert DB",
            "association_label": "Jane Example",
            "category": "Public figure association",
            "list_category": "Don't you know who I am?",
            "tags": ["Jane Example", "Author"],
            "group": "civil",
            "description": "Gulfstream G650",
            "link": "https://www.example.com/jane-example",
            "source_url": "https://github.com/sdr-enthusiasts/plane-alert-db/blob/"
            + commit
            + "/plane-alert-db.csv",
            "source_commit": commit,
            "attribution": public.WATCHLIST_ATTRIBUTION,
            "licenses": [dict(item) for item in public.watchlists.LICENSES],
            "claim_scope": public.WATCHLIST_CLAIM_SCOPE,
        }
        add(self.ledger, evidence=evidence)
        event = published(self.ledger)["events"][0]
        # The entry is named publicly, with the list's own reasons, beside its attribution and scope.
        block = event["watchlist"]
        self.assertEqual(
            {key: block[key] for key in ("association", "category", "list_category", "tags", "group")},
            {
                "association": "Jane Example",
                "category": "Public figure association",
                "list_category": "Don't you know who I am?",
                "tags": ["Jane Example", "Author"],
                "group": "civil",
            },
        )
        self.assertEqual(block["description"], "Gulfstream G650")
        self.assertEqual(block["link"], "https://www.example.com/jane-example")
        self.assertEqual(block["claim_scope"], public.WATCHLIST_CLAIM_SCOPE)
        self.assertEqual(block["attribution"], public.WATCHLIST_ATTRIBUTION)
        self.assertEqual(block["licenses"], ["Open Database License 1.0", "Database Contents License 1.0"])
        self.assertIn(public.WATCHLIST_SCOPE, event["limitations"])
        # The source row and file URL stay private.
        self.assertNotIn("source_url", block)
        self.assertNotIn("source_row", json.dumps(event))
        police = public._watchlist_block(
            {"category": "Police aviation", "source_commit": commit, "association": "Example County Police"}
        )
        self.assertEqual(
            (police["category"], police["association"]), ("Police aviation", "Example County Police")
        )
        self.assertFalse({"list_category", "tags", "group", "description", "link"} & set(police))
        for field, value in (
            ("source_commit", "main"),
            ("attribution", "Someone else"),
            ("category", "Celebrity"),
            ("association_label", None),
            ("association_label", " "),
            ("association_label", "Jane\nExample"),
            ("group", "political"),
            ("tags", ["One", "Two", "Three", "Four"]),
        ):
            changed = deepcopy(evidence)
            changed["watchlist"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                projection(changed, path=story_path("other"))
        # A link that is no public HTTPS page is left out; the entry stays.
        for link in ("https://10.0.0.1/jane", "https://intranet.local/jane", "http://www.example.com/jane"):
            changed = deepcopy(evidence)
            changed["watchlist"]["link"] = link
            with self.subTest(link=link):
                projected = public._watchlist(changed)
                self.assertNotIn("link", projected)
                self.assertEqual(projected["association"], "Jane Example")
        bad = deepcopy(published(self.ledger))
        bad["events"][0]["watchlist"]["licenses"] = []
        with self.assertRaises(ValueError):
            public.validate_snapshot(bad)
        for field, value in (("category", "Celebrity"), ("association", ""), ("link", "https://10.0.0.1/x")):
            changed = deepcopy(published(self.ledger))
            changed["events"][0]["watchlist"][field] = value
            with self.subTest(snapshot=field), self.assertRaises(ValueError):
                public.validate_snapshot(changed)

    def test_saved_evidence_in_an_earlier_wording_still_reads_in_the_current_one(self):
        # Every legacy wording maps to one current sentence, which is never itself a legacy wording.
        olds = [old for values in public.LEGACY_TEXT.values() for old in values]
        self.assertEqual(len(olds), len(set(olds)))
        self.assertFalse(set(olds) & set(public.LEGACY_TEXT))
        for current, values in public.LEGACY_TEXT.items():
            for old in values:
                self.assertEqual(public.current_text(old), current)
                self.assertEqual(old.replace(",", ""), current.replace(",", ""), old)
        old_scope = public.LEGACY_TEXT[public.WATCHLIST_CLAIM_SCOPE][0]
        commit = "c" * 40
        evidence = packet(kind="watchlist_match")
        evidence["watchlist"] = {
            "source_name": "Plane Alert DB",
            "association_label": "Jane Example",
            "category": "Public figure association",
            "source_commit": commit,
            "attribution": public.WATCHLIST_ATTRIBUTION,
            "licenses": [dict(item) for item in public.watchlists.LICENSES],
            "claim_scope": old_scope,
        }
        # A packet saved before the serial comma projects strictly, in the current wording.
        add(self.ledger, evidence=evidence)
        snapshot = published(self.ledger)
        self.assertEqual(snapshot["events"][0]["watchlist"]["claim_scope"], public.WATCHLIST_CLAIM_SCOPE)
        # A snapshot exported before the change still validates.
        older = deepcopy(snapshot)
        event = older["events"][0]
        event["watchlist"]["claim_scope"] = old_scope
        event["evidence_version"] = public.evidence_version(event)
        older["snapshot_id"] = public._digest({k: v for k, v in older.items() if k != "snapshot_id"})
        self.assertEqual(public.validate_snapshot(older)["events"][0]["watchlist"]["claim_scope"], old_scope)
        # An episode caveat in its earlier wording is kept, in the current wording.
        old_caveat = public.LEGACY_TEXT[public.episode_evidence.LIMITATIONS["participant_uncertain_source"]][
            0
        ]
        episode = episode_packet(kind="formation_flight", limitations=[old_caveat])
        limitations = projection(episode)["limitations"]
        self.assertIn(public.episode_evidence.LIMITATIONS["participant_uncertain_source"], limitations)
        self.assertNotIn(old_caveat, limitations)
        # Any other wording fails the export, which names the repair and its order.
        evidence["watchlist"]["claim_scope"] = "Some other scope."
        self.ledger["records"][0]["evidence"] = evidence
        self.ledger["records"][0]["approved_content_hash"] = public._digest(
            {key: self.ledger["records"][0][key] for key in ("evidence", "editorial")}
        )
        with self.assertRaises(ValueError) as caught:
            published(self.ledger)
        self.assertIn("Invalid watchlist association", str(caught.exception))
        self.assertIn(public.BINDING_REPAIR, str(caught.exception))
        self.assertIn("migrate-evidence --apply, then", public.BINDING_REPAIR)
        # The command line names the repair too, and nothing from the record.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            public.write_json_atomic(path, self.ledger, private=True)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "export_public_events.py"),
                    "--ledger",
                    str(path),
                    "export",
                    "--mode",
                    "review",
                    "--as-of",
                    NOW,
                    "--output",
                    str(Path(directory) / "review.json"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn(public.BINDING_REPAIR, result.stderr)
        self.assertNotIn("Some other scope", result.stderr + result.stdout)


def story(**changes):
    """A story object in the snapshot contract's exact shape."""
    return {
        "version": 1,
        "source": "template",
        "angle": "snapshot",
        "variant": "h1/d0/o2",
        "headline": "Bell 407 N12345 circles three times on 3 October 2026",
        "seo_title": "Bell 407 N12345",
        "dek": "The aircraft database lists N12345 as a Bell 407 in a record from 3 October 2026.",
        "paragraphs": ["First paragraph of the story.", "Second paragraph of the story."],
        "opener_skeleton": "first paragraph of the story",
        "headline_skeleton": "bell num n num circles three times on num october num",
        "facts_digest": "a" * 64,
        "facts": {
            "id.registration": "N12345",
            "tp.B407.1": "A sourced type fact.",
            "wl.tags": ["Air Ambulance"],
        },
        "sources": [
            {
                "id": "src.B407.1",
                "publisher": "Bell",
                "title": "Bell 407",
                "url": "https://www.bellflight.com/products/bell-407",
            }
        ],
        **changes,
    }


def routing(**changes):
    return {
        "topic": "flight-shapes",
        "section": "flight-patterns",
        "type": {"code": "B407", "name": "Bell 407", "slug": "bell-407"},
        "registration_slug": "n12345",
        "operator": {
            "name": "Example Air",
            "slug": "example-air",
            "basis": "callsign_designator",
            "designator": "EXA",
        },
        "place": "Coleman, Michigan",
        "country": "United States",
        **changes,
    }


def told(**changes):
    """Editorial metadata whose title and summary are the story's headline and dek."""
    told_story = story(**changes)
    return editorial(
        title=told_story["headline"], summary=told_story["dek"], story=told_story, context=routing()
    )


class StoryContractTests(unittest.TestCase):
    def setUp(self):
        self.ledger = public.new_ledger("2026-09-20")

    def test_story_context_and_release_time_reach_the_snapshot_outside_the_evidence_version(self):
        identifier = add(self.ledger, metadata=told())
        record = self.ledger["records"][0]
        record["automation"] = {"released_at": "2026-10-04T10:00:00Z"}
        snapshot = published(self.ledger)
        event = snapshot["events"][0]
        self.assertEqual(event["id"], identifier)
        self.assertEqual(event["story"]["facts"]["wl.tags"], ["Air Ambulance"])
        self.assertEqual(event["context"], routing())
        self.assertEqual(event["released_at"], "2026-10-04T10:00:00Z")
        self.assertEqual(snapshot["search"][0]["excerpt"], event["story"]["dek"])
        plain = public._projection(packet(), editorial())
        self.assertEqual(event["evidence_version"], plain["evidence_version"])
        self.assertEqual(public.validate_snapshot(snapshot), snapshot)
        # Review snapshots never carry a release time.
        self.assertNotIn(
            "released_at", public.export_snapshot(self.ledger, mode="review", as_of=NOW)["events"][0]
        )

    def test_release_times_are_held_between_the_observation_and_the_snapshot(self):
        add(self.ledger, metadata=told())
        record = self.ledger["records"][0]
        # A correction that moves the observation end past the first release dates the
        # release from the new end, never back to the observation start.
        for released, expected in (
            ("2026-10-03T23:00:00Z", "2026-10-04T00:05:00Z"),
            ("2026-10-05T00:00:00Z", NOW),
            ("2026-10-04T10:00:00+00:00", "2026-10-04T10:00:00Z"),
        ):
            with self.subTest(released=released):
                record["automation"] = {"released_at": released}
                self.assertEqual(published(self.ledger)["events"][0]["released_at"], expected)
        record["automation"] = {"released_at": "not a time"}
        self.assertNotIn("released_at", published(self.ledger)["events"][0])
        record["automation"] = {"released_at": "2026-10-04T10:00:00Z"}
        snapshot = published(self.ledger)
        snapshot["events"][0]["released_at"] = "2026-10-03T23:00:00Z"
        snapshot["snapshot_id"] = public._digest(
            {key: value for key, value in snapshot.items() if key != "snapshot_id"}
        )
        with self.assertRaisesRegex(ValueError, "release time"):
            public.validate_snapshot(snapshot)

    def test_the_content_revision_time_follows_the_release_and_stays_out_of_the_evidence(self):
        add(self.ledger, metadata=told())
        record = self.ledger["records"][0]
        record["automation"] = {
            "released_at": "2026-10-04T10:00:00Z",
            "content_revised_at": "2026-10-04T12:00:00Z",
        }
        snapshot = published(self.ledger)
        event = snapshot["events"][0]
        self.assertEqual(event["content_revised_at"], "2026-10-04T12:00:00Z")
        self.assertEqual(event["evidence_version"], public.evidence_version(event))
        self.assertEqual(public.validate_snapshot(snapshot), snapshot)
        # Before the release or after the snapshot: held at that bound.
        for revised, expected in (
            ("2026-10-04T09:00:00Z", "2026-10-04T10:00:00Z"),
            ("2026-10-05T00:00:00Z", NOW),
        ):
            with self.subTest(revised=revised):
                record["automation"] = {"released_at": "2026-10-04T10:00:00Z", "content_revised_at": revised}
                self.assertEqual(published(self.ledger)["events"][0]["content_revised_at"], expected)
        # Unreadable or without a release: left out.
        for automation in (
            {"released_at": "2026-10-04T10:00:00Z", "content_revised_at": "soon"},
            {"content_revised_at": "2026-10-04T12:00:00Z"},
        ):
            with self.subTest(automation=automation):
                record["automation"] = automation
                self.assertNotIn("content_revised_at", published(self.ledger)["events"][0])
        self.assertNotIn(
            "content_revised_at", public.export_snapshot(self.ledger, mode="review", as_of=NOW)["events"][0]
        )
        for change in (
            {"content_revised_at": "2026-10-04T09:00:00Z"},
            {"content_revised_at": "2026-10-04T12:00:00+00:00"},
            {"released_at": None},
        ):
            with self.subTest(change=change):
                edited = deepcopy(snapshot)
                edited["events"][0].update(change)
                if edited["events"][0]["released_at"] is None:
                    del edited["events"][0]["released_at"]
                edited["snapshot_id"] = public._digest(
                    {key: value for key, value in edited.items() if key != "snapshot_id"}
                )
                with self.assertRaisesRegex(ValueError, "content revision"):
                    public.validate_snapshot(edited)

    def test_the_content_version_ignores_publication_times_and_the_card(self):
        event = public._projection(packet(), told())
        base = public.content_version(event)
        self.assertEqual(
            public.content_version({**event, "revised_at": NOW, "released_at": NOW, "revision": 3}), base
        )
        for change in (
            {"story": {**event["story"], "paragraphs": ["A new first paragraph.", "Second."]}},
            {"story": {**event["story"], "facts": {**event["story"]["facts"], "tp.B407.1": "Another fact."}}},
            {"evidence_version": "0" * 64},
            {"weekly_spotlight": {"reason": "Researched."}},
        ):
            with self.subTest(change=sorted(change)):
                self.assertNotEqual(public.content_version({**event, **change}), base)
        # Facts the page never shows are not content.
        story = {**event["story"], "facts": {**event["story"]["facts"], "wl.tags": ["Hospital"]}}
        self.assertEqual(public.content_version({**event, "story": story}), base)

    def test_story_facts_are_capped_and_places_carry_no_digits(self):
        facts = {f"m.extra_{index:02d}": index for index in range(public.MAX_STORY_FACTS)}
        public._projection(packet(), told(facts=facts))
        with self.assertRaises(ValueError):
            public._projection(packet(), told(facts={**facts, "id.registration": "N12345"}))
        for field, value in (("place", "Coleman 2, Michigan"), ("country", "Zone 51")):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "no digits"):
                public._projection(packet(), {**told(), "context": routing(**{field: value})})

    def test_the_engine_context_is_the_public_routing_and_nothing_else(self):
        event = public._projection(packet(), {**told(), "context": routing()})
        self.assertEqual(event["context"], routing())
        self.assertEqual(public._routing(event["context"]), event["context"])
        # Keys an older engine added for slugs and the variety guard are refused, top level or nested.
        for extra in (
            {"kind": "circling"},
            {"facts_digest": "a" * 64},
            {"participant_types": [{"name": "Bell 407", "slug": "bell-407"}]},
            {"callsign": "EXA12"},
            {"icao_address": "A12345"},
            {"code": None},
            {"type": {**routing()["type"], "short_slug": "407"}},
            {"type": {**routing()["type"], "owner": "x"}},
            {"operator": {**routing()["operator"], "x": 1}},
        ):
            with self.subTest(extra=sorted(extra)), self.assertRaises(ValueError):
                public._projection(packet(), {**told(), "context": {**routing(), **extra}})

    def test_a_type_reference_article_is_routed_only_as_a_public_https_url(self):
        article = "https://en.wikipedia.org/wiki/Bell_407"
        engine = {**routing(), "type": {**routing()["type"], "same_as": article}}
        event = public._projection(packet(), {**told(), "context": engine})
        self.assertEqual(event["context"]["type"], {**routing()["type"], "same_as": article})
        self.assertEqual(public._routing(event["context"]), event["context"])
        # The evidence never depends on routing, and a type without an article routes without one.
        self.assertEqual(event["evidence_version"], public._projection(packet(), told())["evidence_version"])
        self.assertNotIn("same_as", public._projection(packet(), told())["context"]["type"])
        for url in (
            "http://en.wikipedia.org/wiki/Bell_407",
            "https://10.0.0.1/wiki",
            "https://x.ts.net/a",
            7,
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public._projection(
                    packet(), {**told(), "context": {**engine, "type": {**engine["type"], "same_as": url}}}
                )

    def test_public_text_refuses_every_name_the_release_guard_refuses(self):
        self.enterContext(patch.object(public, "_private_text", None))
        self.enterContext(patch.dict(os.environ, {private_terms.ENVIRONMENT: ""}))
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        terms = folder / "private-terms.txt"
        terms.write_text("fixture-person|fixture[.]example\n")
        terms.chmod(0o600)
        self.assertEqual(public._text("Seen by fixture-person."), "Seen by fixture-person.")
        config = folder / "publisher.json"
        config.write_text(json.dumps({"private_terms_file": str(terms), "private_terms": ["Synthetic Host"]}))
        loaded = public.load_config(config)
        shared = private_terms.pattern(loaded)
        self.assertTrue(public.private_text().pattern.startswith(shared + "|"))
        self.assertEqual(os.environ[private_terms.ENVIRONMENT], str(terms))
        for text in (
            "An endpoint at localhost.",
            "A path under /.private",
            "The key tskey-abc12.",
            "Seen by FIXTURE-PERSON.",
            "Served from fixture.example today.",
            "A synthetic host name.",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                public._text(text)
        self.assertEqual(public._text("Served from fixtureXexample."), "Served from fixtureXexample.")

    def test_files_are_replaced_whole_and_epoch_times_are_normalized(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "media.bin"
            public.write_bytes_atomic(path, b"\x89PNG first")
            public.write_bytes_atomic(path, b"\x89PNG second", private=True)
            self.assertEqual(path.read_bytes(), b"\x89PNG second")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual([item.name for item in path.parent.iterdir()], ["media.bin"])
        self.assertEqual(public.epoch_utc(1_790_000_000), "2026-09-21T14:13:20Z")
        self.assertEqual(public.epoch_utc(1_790_000_000.25), "2026-09-21T14:13:20.250000Z")

    def test_a_variant_naming_every_phrase_builder_projects(self):
        builders = ".".join(f"builder{index}" for index in range(20))
        variant = "h1/d0/o2/" + builders
        self.assertGreater(len(variant), 150)
        event = public._projection(packet(), told(variant=variant))
        self.assertEqual(event["story"]["variant"], variant)

    def test_story_and_routing_fields_are_allowlisted_and_checked(self):
        cases = {
            "extra field": told(private_note="x"),
            "unknown source": told(source="draft"),
            "markup": told(paragraphs=["<script>bad</script>", "Second."]),
            "private host": told(paragraphs=["See host.example.ts.net for details.", "Second."]),
            "plain http source": told(
                sources=[{"id": "s", "publisher": "P", "title": "T", "url": "http://example.org/x"}]
            ),
            "bad digest": told(facts_digest="xyz"),
            "overlong variant": told(variant="h1/d0/o2/" + "a" * 200),
            "nested fact": told(facts={"tp.B407.1": {"text": "nested"}}),
            "bad fact id": told(facts={"Fact": "value"}),
        }
        for name, metadata in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                public._projection(packet(), metadata)
        with self.assertRaisesRegex(ValueError, "headline and dek"):
            public._projection(packet(), {**told(), "title": "Another title"})
        for name, change in {
            "unknown key": {"route": "x"},
            "bad topic": {"topic": "Notable Aircraft"},
            "bad type code": {"type": {"code": "toolong", "name": "Bell 407", "slug": "bell-407"}},
            "bad operator basis": {
                "operator": {
                    "name": "Example Air",
                    "slug": "example-air",
                    "basis": "Owner!",
                    "designator": "EXA",
                }
            },
        }.items():
            metadata = {**told(), "context": routing(**change)}
            with self.subTest(name=name), self.assertRaises(ValueError):
                public._projection(packet(), metadata)
        minimal = {**told(), "context": {"topic": "flight-shapes", "section": "flight-patterns"}}
        self.assertEqual(public._projection(packet(), minimal)["context"], minimal["context"])

    def test_only_story_paths_in_the_story_slug_family_are_accepted(self):
        for path in (
            "/stories/airbus-a380-hl7635-27-sep-2026/",
            "/stories/hbal837-high-altitude-balloon-2-oct-2026/",
            "/stories/boeing-737-n123ab-rapid-descent-1432z-27-sep-2026/",
        ):
            with self.subTest(path=path):
                self.assertEqual(public._article_path(path), path)
                self.assertEqual(public.article_slug(path), path.split("/")[2])
        for path in (
            "/hl7635-watch-type-2026-09-27/",
            "/observation-4053cdb19b3ec8b0c54234d0/",
            "/stories/hl7635-watch-type-2026-09-27/",
            "/stories/airbus-a380-hl7635-27-sep-2026",
            "/airbus-a380-hl7635-27-sep-2026/",
            "/stories/page/",
            "/stories/Airbus-A380-27-sep-2026/",
            "/stories/a/b-1-oct-2026/",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                public._article_path(path)

    def test_neighbor_readings_around_an_outlier_are_deterministic_measurements(self):
        evidence = packet(kind="outlier_gs")
        evidence.pop("pattern")
        evidence["observation"]["metric_value"] = 1063.9
        evidence["received"]["saved_readings_at_utc"] = "2026-10-03T23:58:00Z"
        base = 1_791_071_880  # 2026-10-03T23:58:00Z
        evidence["surrounding_readings"] = [
            {"timestamp": base - 20, "gs": 95.8},
            {"timestamp": base - 10, "gs": 110.8},
            {"timestamp": base, "gs": 1063.9},
            {"timestamp": base + 10, "gs": 118.1},
            {"timestamp": base + 20, "gs": 123.0},
            {"timestamp": base + 30, "alt_baro": "ground"},
        ]
        event = public._projection(evidence, editorial())
        rows = {item["key"]: item for item in event["measurements"] if item["scope"] == "surrounding-reports"}
        self.assertEqual(rows["previous_report"]["value"], 110.8)
        self.assertEqual(rows["previous_report"]["at_utc"], "2026-10-03T23:57:50Z")
        self.assertEqual(rows["next_report"]["value"], 118.1)
        self.assertEqual(
            (rows["lowest_surrounding_report"]["value"], rows["highest_surrounding_report"]["value"]),
            (95.8, 123.0),
        )
        self.assertEqual(
            (rows["surrounding_report_count"]["value"], rows["surrounding_report_count"]["unit"]),
            (4, "reports"),
        )
        self.assertEqual(rows["lowest_surrounding_report"]["start_utc"], "2026-10-03T23:57:40Z")
        identifier = add(self.ledger, evidence=evidence)
        snapshot = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        self.assertEqual(snapshot["events"][0]["id"], identifier)
        forged = deepcopy(snapshot)
        item = next(
            row for row in forged["events"][0]["measurements"] if row["key"] == "surrounding_report_count"
        )
        item["unit"] = "kn"
        forged["events"][0]["evidence_version"] = public.evidence_version(forged["events"][0])
        forged["snapshot_id"] = public._digest(
            {key: value for key, value in forged.items() if key != "snapshot_id"}
        )
        with self.assertRaises(ValueError):
            public.validate_snapshot(forged)
        # Without the outlier among the readings there is nothing to compare.
        evidence["observation"]["metric_value"] = 999.0
        self.assertFalse(
            [
                row
                for row in public._projection(evidence, editorial())["measurements"]
                if row["scope"] == "surrounding-reports"
            ]
        )

    def test_a_publisher_ordinary_priority_omits_the_exception_proof(self):
        from test_autopilot import candidate

        from wtf_adsb_publisher import autopilot

        item = candidate(1, "rapid_descent", -9000)
        evidence = autopilot.evidence(item["source"], item["interval"])
        proven = public._projection(evidence, editorial())
        ordinary = public._projection(evidence, editorial(priority="ordinary"))
        self.assertIn("priority_evidence", proven)
        self.assertNotIn("priority_evidence", ordinary)
        self.assertNotEqual(proven["evidence_version"], ordinary["evidence_version"])
        self.assertEqual(proven["measurements"], ordinary["measurements"])
        with self.assertRaisesRegex(ValueError, "priority"):
            public._projection(evidence, editorial(priority="exceptional"))


WINDS = {
    "day": "2026-10-03",
    "east_addresses": 35,
    "west_addresses": 33,
    "east_median_kn": 560.0,
    "west_median_kn": 380.0,
    "east_quartiles_kn": [540.0, 575.0],
    "west_quartiles_kn": [360.0, 395.0],
    "asymmetry_kn": 180,
    "altitude_floor_ft": 28000,
}


def winds_packet(**changes):
    evidence = episode_packet(
        "winds_aloft_day",
        peak_rate_fpm=None,
        median_rate_fpm=None,
        threshold_fpm=None,
        qualifying_duration_seconds=None,
        qualifying_sample_count=None,
        footprint_nm=None,
    )
    evidence["maneuver"] = {key: value for key, value in evidence["maneuver"].items() if value is not None}
    evidence["winds_aloft"] = {**WINDS, **changes}
    evidence["participants"] = [
        {
            "role": "member",
            "icao_address": "a00001",
            "source": "adsb_icao",
            "measurements": {"median_gs_kn": 545.0},
        }
    ]
    return evidence


class NewKindEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.ledger = public.new_ledger("2026-09-20")

    def test_the_new_kinds_carry_their_limitations(self):
        stories = public.stories
        expected = {
            "winds_aloft_day": (
                "Ground-speed differences by direction are consistent with winds aloft; the wind itself was not measured.",
                "Open-Meteo values, when shown, are a model forecast for one grid cell, not an observation.",
            ),
            "foreign_military": (
                stories.DATABASE_FLAG_CAVEAT,
                "An address block names the State of registry; a re-registered aircraft can keep an old address.",
            ),
            "first_operator_here": (
                "First in this collection's retained records; an operator designator in a callsign is a label "
                "the crew enters, not a confirmed operator.",
            ),
            "aerobatic_practice": (stories.PATTERN_LIMITATION,),
            "unregistered_address": (
                "No registration was found in the registries the site consults on the dates shown; the aircraft "
                "may be registered elsewhere.",
            ),
            "racetrack_orbit": (stories.PATTERN_LIMITATION,),
        }
        for kind, lines in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(public.KIND_LIMITATIONS[kind], lines)
                limitations = projection(packet(kind=kind))["limitations"]
                for line in lines:
                    self.assertIn(line, limitations)

    def test_a_winds_day_projects_its_medians_over_the_episode_window(self):
        event = projection(winds_packet())
        rows = keyed(event)
        for key, value in (
            ("asymmetry_kn", 180),
            ("east_median_kn", 560.0),
            ("west_median_kn", 380.0),
            ("east_addresses", 35),
            ("west_addresses", 33),
        ):
            with self.subTest(key=key):
                row = rows[(key, "analysis-window")]
                self.assertEqual(row["value"], value)
                self.assertEqual(
                    (row["start_utc"], row["end_utc"]), ("2026-10-03T23:56:00Z", "2026-10-03T23:58:00Z")
                )
        self.assertNotIn(public.WIND_ATTRIBUTION, event["limitations"])
        (member,) = event["participants"]
        self.assertEqual(
            member["measurements"],
            [
                {
                    "key": "median_gs_kn",
                    "label": "Median ground speed for the UTC day",
                    "value": 545.0,
                    "unit": "kn",
                }
            ],
        )
        # Another kind never shows the block, and a modelled wind brings its attribution.
        other = winds_packet()
        other["kind"] = "formation_flight"
        self.assertFalse({key for key, _scope in keyed(projection(other))} & set(public.WINDS_MEASUREMENTS))
        windy = winds_packet(
            wind_250_hpa={
                "speed_kn": 140.0,
                "direction_deg": 260.0,
                "source": "open-meteo",
                "hour": "2026-10-03T23:00",
            }
        )
        self.assertIn(public.WIND_ATTRIBUTION, projection(windy)["limitations"])

    def test_snapshot_events_carry_their_headline_measurements(self):
        add(self.ledger, evidence=winds_packet(), metadata=editorial(path=EVENT_PATH))
        add(self.ledger, key="episode-b", evidence=packet())
        good = published(self.ledger)
        by_kind = {event["kind"]: event for event in good["events"]}
        self.assertEqual(
            by_kind["winds_aloft_day"]["headline_measurements"],
            ["asymmetry_kn", "east_median_kn", "west_median_kn", "east_addresses", "west_addresses"],
        )
        self.assertEqual(by_kind["circling"]["headline_measurements"], ["gs", "alt_baro"])
        review = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        self.assertTrue(all(event["headline_measurements"] for event in review["events"]))

        def redigest(value):
            value["snapshot_id"] = public._digest({k: v for k, v in value.items() if k != "snapshot_id"})
            return value

        for change in (
            lambda event: event.update(headline_measurements=["gs"]),
            lambda event: event.update(headline_measurements=["receiver_lat", "alt_baro"]),
            lambda event: event.update(headline_measurements="gs"),
            lambda event: event.update(headline_key_facts=["gs"]),
        ):
            bad = deepcopy(good)
            change(bad["events"][0])
            with self.subTest(event=bad["events"][0].get("headline_measurements")):
                with self.assertRaises(ValueError):
                    public.validate_snapshot(redigest(bad))
        # Older snapshots without the list still validate.
        older = deepcopy(good)
        for event in older["events"]:
            event.pop("headline_measurements")
        self.assertEqual(public.validate_snapshot(redigest(older)), older)

    def test_a_subjectless_event_names_no_aircraft(self):
        event = projection(winds_packet())
        self.assertEqual(event["aircraft"], {})
        self.assertIs(event["subjectless"], True)
        self.assertNotIn("subjectless", projection(packet()))
        add(self.ledger, evidence=winds_packet(), metadata=editorial(path=EVENT_PATH))
        add(self.ledger, key="episode-b", evidence=packet())
        good = published(self.ledger)
        by_kind = {item["kind"]: item for item in good["events"]}
        self.assertEqual(by_kind["winds_aloft_day"]["aircraft"], {})
        self.assertIs(by_kind["winds_aloft_day"]["subjectless"], True)
        self.assertNotIn("subjectless", by_kind["circling"])
        winds = good["events"].index(by_kind["winds_aloft_day"])
        circling = good["events"].index(by_kind["circling"])

        def redigest(value):
            for item in value["events"]:
                item["evidence_version"] = public.evidence_version(item)
            value["snapshot_id"] = public._digest({k: v for k, v in value.items() if k != "snapshot_id"})
            return value

        for index, change in (
            (winds, lambda item: item.update(aircraft={"registration": "N12345"})),
            (winds, lambda item: item.pop("subjectless")),
            (winds, lambda item: item.update(subjectless=1)),
            (circling, lambda item: item.update(subjectless=True)),
            (circling, lambda item: item.update(subjectless=False)),
        ):
            bad = deepcopy(good)
            change(bad["events"][index])
            with self.subTest(event=bad["events"][index]["kind"]), self.assertRaises(ValueError):
                public.validate_snapshot(redigest(bad))

    def test_related_stories_reach_the_snapshot_outside_the_evidence(self):
        related = [
            {
                "path": story_path("earlier"),
                "title": "An earlier story",
                "observed_start": "2026-10-01T10:00:00Z",
            },
            {
                "path": story_path("older"),
                "title": "An older story",
                "observed_start": "2026-09-30T10:00:00Z",
            },
        ]
        for name, item in zip(("episode-earlier", "episode-older"), related, strict=True):
            start = item["observed_start"]
            end = start.replace("T10:00", "T10:10")
            metadata = editorial(path=item["path"], title=item["title"])
            add(self.ledger, key=name, evidence=packet(start, end), metadata=metadata)
        # A story not yet published is in the ledger but not in the export.
        unreleased = story_path("unreleased")
        add(self.ledger, key="episode-unreleased", metadata=editorial(path=unreleased), approved=False)
        add(self.ledger, metadata=editorial(related=[{**related[0], "path": unreleased}, *related]))
        good = published(self.ledger)
        event = next(item for item in good["events"] if item["article_path"] == STORY_PATH)
        self.assertEqual(event["related"], related)
        # A linked story's later headline reaches every page that links to it.
        earlier = self.ledger["records"][0]
        renamed = {**earlier["editorial"], "title": "A corrected headline"}
        public.put_event(self.ledger, "episode-earlier", earlier["evidence"], renamed, at=NOW)
        public.set_review_state(self.ledger, earlier["id"], "approved", at=NOW)
        relinked = next(
            item for item in published(self.ledger)["events"] if item["article_path"] == STORY_PATH
        )
        self.assertEqual(relinked["related"][0], {**related[0], "title": "A corrected headline"})
        self.assertEqual(self.ledger["records"][-1]["editorial"]["related"][1]["title"], "An earlier story")
        # A review export lists every story, so it keeps the unreleased one too.
        review = public.export_snapshot(self.ledger, mode="review", as_of=NOW)["events"]
        self.assertEqual(
            next(item for item in review if item["article_path"] == STORY_PATH)["related"][0]["path"],
            unreleased,
        )
        # With none of its related stories exported, an event lists none.
        for record in self.ledger["records"][:2]:
            public.set_review_state(self.ledger, record["id"], "withdrawn", at=NOW)
        alone = published(self.ledger)
        self.assertNotIn(
            "related", next(item for item in alone["events"] if item["article_path"] == STORY_PATH)
        )
        plain = public._projection(packet(), editorial())
        self.assertNotIn("related", plain)
        self.assertEqual(event["evidence_version"], plain["evidence_version"])
        self.assertEqual(public.content_version(event), public.content_version(plain))
        too_many = [
            {**related[0], "path": story_path(f"story-{index}")} for index in range(public.KEPT_RELATED + 1)
        ]
        refused = (
            [{**related[0], "path": "/about/"}],
            [{**related[0], "path": STORY_PATH}],
            [{**related[0], "kind": "vintage"}],
            [{**related[0], "observed_start": "yesterday"}],
            [related[0], related[0]],
            list(reversed(related)),
            too_many,
            [],
            related[0],
        )
        for bad in refused:
            with self.subTest(related=bad), self.assertRaises(ValueError):
                public.put_event(
                    public.new_ledger("2026-09-20"), "episode-x", packet(), editorial(related=bad)
                )
        index = good["events"].index(event)
        absent = [{**related[0], "path": story_path("absent")}]
        retitled = [{**related[0], "title": "Not its headline"}, related[1]]
        moved = [{**related[0], "observed_start": "2026-10-01T09:00:00Z"}, related[1]]
        for bad in (*refused, [{**related[0], "title": "  Spaced  "}], absent, retitled, moved):
            tampered = deepcopy(good)
            tampered["events"][index]["related"] = bad
            tampered["snapshot_id"] = public._digest(
                {k: v for k, v in tampered.items() if k != "snapshot_id"}
            )
            with self.subTest(snapshot=bad), self.assertRaises(ValueError):
                public.validate_snapshot(tampered)

    def test_a_page_lists_the_newest_five_published_related_stories(self):
        stored = []
        for index in range(8):
            start = f"2026-09-2{index + 1}T08:00:00Z"
            path = story_path(f"linked-{index}")
            # The two newest are not yet published, so the export skips them.
            metadata = editorial(path=path, title=f"Linked story {index}")
            add(
                self.ledger,
                key=f"linked-{index}",
                evidence=packet(start, start),
                metadata=metadata,
                approved=index < 6,
            )
            stored.insert(0, {"path": path, "title": f"Linked story {index}", "observed_start": start})
        add(
            self.ledger,
            evidence=packet("2026-09-29T08:00:00Z", "2026-09-29T08:10:00Z"),
            metadata=editorial(related=stored),
        )
        snapshot = published(self.ledger)
        event = next(item for item in snapshot["events"] if item["article_path"] == STORY_PATH)
        self.assertEqual(event["related"], stored[2:7])
        review = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        self.assertEqual(
            next(item for item in review["events"] if item["article_path"] == STORY_PATH)["related"],
            stored[:5],
        )

    def test_headline_keys_are_public_measurement_keys(self):
        known = (
            set(public.READINGS)
            | set(public.EPISODE_MEASUREMENTS)
            | set(public.PATTERN_MEASUREMENTS)
            | set(public.WINDS_MEASUREMENTS)
        )
        for kind, keys in public.stories.HEADLINE_MEASUREMENTS.items():
            with self.subTest(kind=kind):
                self.assertLessEqual(set(keys), known)


if __name__ == "__main__":
    unittest.main()
