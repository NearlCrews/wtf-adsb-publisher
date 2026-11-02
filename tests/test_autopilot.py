"""The afternoon exception, automatic decisions and uncertain publication retries."""

import hashlib
import json
import re
import sys
import unittest
from copy import deepcopy
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import reference_fixture
import story_fixtures
from inbox_support import inbox
from test_curation import finding

from wtf_adsb_publisher import autopilot, curation, entity_context, episode_evidence, publication, selection
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher.ghost_client import GhostError

NOW = "2026-10-01T18:00:00Z"


def setUpModule():
    reference_fixture.install()


def candidate(identifier, kind="vintage", value=100, **kwargs):
    source = finding(identifier, kind, value=value, **kwargs)
    source["evidence"]["aircraft"].update(hex=source["hex"], r=f"N{10000 + identifier}", year=1920)
    source["unit"] = "years" if kind == "vintage" else "ft/min"
    if kind in {"rapid_descent", "rapid_climb"}:
        source["evidence"]["maneuver"]["peak_rate_fpm"] = value
    return {"key": str(identifier), "source": source, "interval": (source["first_seen"], source["last_seen"])}


def exceptional_descent(identifier=1, **decision):
    """An exception-grade descent candidate with the selection's stored decision; `decision`
    overrides its fields, and the candidate's boolean follows its `exceptional`."""
    item = candidate(identifier, "rapid_descent", -9000)
    item["exception"] = {
        "grade": True,
        "exceptional": True,
        "reason": None,
        "curation_version": curation.VERSION,
        **decision,
    }
    item["exceptional"] = item["exception"]["exceptional"]
    return item


class Ghost:
    def __init__(self):
        self.posts = []
        self.pages = []
        self.writes = 0
        self.requests = []
        self.lose_response = False
        self.rename = None

    def inventory(self):
        return deepcopy(self.posts)

    def slugs_in_use(self):
        return {post["slug"] for post in self.posts} | {page["slug"] for page in self.pages}

    def save(self, post, current=None):
        self.writes += 1
        self.requests.append(deepcopy(post))
        saved = {
            **deepcopy(current or {}),
            **deepcopy(post),
            "id": current["id"] if current else hashlib.sha256(post["slug"].encode()).hexdigest()[:24],
            "created_at": current["created_at"] if current else NOW,
            "updated_at": NOW,
        }
        if "lexical" in saved:
            saved["html"] = "\n".join(
                child["html"]
                for child in json.loads(saved["lexical"])["root"]["children"]
                if child.get("type") == "html"
            )
        if self.rename and saved["slug"] == self.rename:
            # Like Ghost when another post or page took the slug first.
            saved["slug"] += "-2"
            self.rename = None
        self.posts = [item for item in self.posts if item["id"] != saved["id"]] + [saved]
        if self.lose_response:
            self.lose_response = False
            raise RuntimeError("Response lost after Ghost committed the post")
        return deepcopy(saved)


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.ledger = public.new_ledger("2026-10-01")
        self.ghost = Ghost()
        self.saved = None

    def persist(self, ledger):
        self.saved = deepcopy(ledger)

    def publish(self):
        return autopilot.publish(self.ledger, self.ghost, persist=self.persist, at=NOW)

    def database(self, *items):
        """The findings the engine would export; collect() builds their observations."""
        return items

    def collect(self, items, now, **options):
        """Selection over the imported view of `items` (inbox_support.inbox) at `now`, where the
        live tests read a collector journal; `norms` are the engine's exported type norms."""
        norms = options.pop("norms", None)
        return autopilot.collect_state(inbox(*items, now=now, norms=norms), now, **options)

    def old_weekly_dossier(self, source, *, suspended=False):
        import test_weekly_spotlight as weekly_tests

        labels = {**public.EPISODE_MEASUREMENTS, "median_rate_fpm": ("Median signed vertical rate", "ft/min")}
        with (
            patch.object(weekly_tests, "finding", return_value=source),
            patch.object(curation, "CONTEXT_KINDS", frozenset()),
            patch.object(
                selection, "assess", return_value={"story_eligible": True, "review_required": False}
            ),
            patch.object(public, "EPISODE_MEASUREMENTS", labels),
        ):
            self.ledger, _, review, inventory = weekly_tests.fixture()
        event = review["events"][0]
        record = self.ledger["records"][0]
        record["automation"] = {
            "source": deepcopy(source),
            "policy_version": 1,
            "released_at": weekly_tests.NOW,
            "published_event": deepcopy(event),
        }
        if suspended:
            record["automation"]["suspended"] = True
        post = inventory["posts"][0]
        post.update(
            status="draft" if suspended else "published",
            title=event["title"],
            custom_excerpt=autopilot.publication.article_excerpt(event),
            created_at=weekly_tests.NOW,
            lexical=json.dumps(
                {"root": {"children": [{"type": "html", "version": 1, "html": post["html"]}]}}
            ),
        )
        post["tags"].append({"name": "#autopilot-" + record["id"]})
        self.ghost.posts = [post]
        return weekly_tests.NOW

    def test_valid_replacement_resumes_a_suspended_old_routine_weekly_dossier(self):
        import test_weekly_spotlight as weekly_tests

        routine = weekly_tests.finding()
        routine.update(kind="circling", value=3, unit="loops")
        at = self.old_weekly_dossier(routine, suspended=True)
        before = deepcopy(self.ledger["records"][0])
        source = weekly_tests.finding()
        item = {
            "key": before["episode_key"],
            "source": source,
            "interval": (source["first_seen"], source["last_seen"]),
        }
        self.assertTrue(autopilot.eligible(source))
        self.assertEqual(autopilot.stage(self.ledger, [item], at=at), {"changed": 1, "rejected": 0})
        record = self.ledger["records"][0]
        self.assertEqual(record["id"], before["id"])
        self.assertEqual(record["editorial"]["article_path"], before["editorial"]["article_path"])
        self.assertEqual(
            record["history"][-1], {key: value for key, value in before.items() if key != "history"}
        )
        self.assertEqual(record["evidence"]["kind"], "watch_type")
        self.assertNotIn("weekly_spotlight", record["editorial"])
        self.assertNotIn("weekly_source", record["editorial"])
        with patch(__name__ + ".NOW", at):
            snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual([event["id"] for event in snapshot["events"]], [before["id"]])
        self.assertEqual(self.ghost.posts[0]["status"], "published")
        self.assertNotIn("weekly-research", self.ghost.posts[0]["html"])
        self.assertNotIn("suspended", record["automation"])

    def test_current_weekly_research_survives_unchanged_evidence(self):
        import test_weekly_spotlight as weekly_tests

        source = weekly_tests.finding()
        at = self.old_weekly_dossier(source)
        before = deepcopy(self.ledger["records"][0])
        item = {
            "key": before["episode_key"],
            "source": source,
            "interval": (source["first_seen"], source["last_seen"]),
        }
        self.assertEqual(autopilot.stage(self.ledger, [item], at=at), {"changed": 0, "rejected": 0})
        self.assertEqual(self.ledger["records"][0], before)

    def test_reproject_moves_a_weekly_dossier_made_under_older_card_wording(self):
        import test_weekly_spotlight as weekly_tests

        source = weekly_tests.finding()
        older = {source["kind"]: ("An older fixture wording of this kind's limitation.",)}
        with patch.dict(public.KIND_LIMITATIONS, older):
            at = self.old_weekly_dossier(source)
        record = self.ledger["records"][0]
        record["automation"]["source_kind"] = "frozen"
        research, state = deepcopy(record["editorial"]["weekly_spotlight"]), record["state"]
        with self.assertRaisesRegex(ValueError, "different observation evidence"):
            public._projection(record["evidence"], record["editorial"])
        report = autopilot.reproject(self.ledger, at)
        self.assertEqual(
            (report["reprojected"], report["research_rebound"], report["unresolved"]), (1, 1, [])
        )
        record = self.ledger["records"][0]
        event = public._projection(record["evidence"], record["editorial"])
        moved = record["editorial"]["weekly_spotlight"]
        self.assertEqual(moved, {**research, "evidence_version": event["evidence_version"]})
        self.assertEqual(record["state"], state)
        self.assertEqual(record["evidence"], record["history"][-1]["evidence"])
        self.assertEqual(autopilot.reproject(self.ledger, at)["unchanged"], 1)

    def test_old_researched_climb_is_suspended_and_remains_recoverable(self):
        source = candidate(1, "rapid_climb", 8000, day=3)["source"]
        metrics = source["evidence"]["maneuver"]
        metrics.pop("qualifying_duration_seconds")
        metrics.pop("qualifying_sample_count")
        metrics["median_rate_fpm"] = 8000
        at = self.old_weekly_dossier(source)
        before = deepcopy(self.ledger["records"][0])
        with patch(__name__ + ".NOW", at):
            snapshot, result = self.publish()
            self.assertEqual(result["suspended"], 1)
            self.assertFalse(snapshot["events"])
            self.assertEqual(self.ghost.posts[0]["status"], "draft")
            self.ledger = deepcopy(self.saved)
            writes = self.ghost.writes
            snapshot, result = self.publish()
        self.assertFalse(snapshot["events"])
        self.assertEqual(self.ghost.writes, writes)
        record = self.ledger["records"][0]
        self.assertTrue(record["automation"]["suspended"])
        for key in ("id", "evidence", "editorial", "history"):
            self.assertEqual(record[key], before[key])

    def test_old_climb_only_posts_are_suspended_without_losing_their_records(self):
        with patch.object(curation, "CONTEXT_KINDS", curation.CONTEXT_KINDS - {"rapid_climb"}):
            autopilot.stage(self.ledger, [candidate(1, "rapid_climb", 8000)], at=NOW)
            before, result = self.publish()
            self.assertEqual(result["published"], 1)
        record = self.ledger["records"][0]
        record["automation"]["source_kind"] = "frozen"
        after, result = self.publish()
        self.assertFalse(after["events"])
        self.assertEqual(result["suspended"], 1)
        self.assertEqual(record["id"], before["events"][0]["id"])
        self.assertEqual(record["editorial"]["article_path"], before["events"][0]["article_path"])
        self.assertEqual(self.ghost.posts[0]["status"], "draft")
        writes = self.ghost.writes
        self.publish()
        self.assertEqual(self.ghost.writes, writes)

    def test_climb_with_an_independent_signal_publishes_that_signal(self):
        climb = candidate(1, "rapid_climb", 8000)
        rare = candidate(2, "watch_type", 1, address=climb["source"]["hex"])
        rare["source"]["evidence"]["aircraft"]["t"] = "B52"
        db = self.database(climb, rare)
        candidates = self.collect(db, public._instant(NOW).timestamp())
        self.assertEqual([c["source"]["kind"] for c in candidates], ["watch_type"])
        autopilot.stage(self.ledger, candidates, at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual(snapshot["events"][0]["kind"], "watch_type")

    def test_oscillation_only_posts_retire_and_an_independent_signal_keeps_their_identity(self):
        item = candidate(1, "altitude_oscillation", 4)
        item["source"]["unit"] = "reversals"
        item["source"]["evidence"]["aircraft"]["t"] = "B52"
        db = self.database(item)
        with patch.object(curation, "CONTEXT_KINDS", curation.CONTEXT_KINDS - {"altitude_oscillation"}):
            item = self.collect(db, public._instant(NOW).timestamp())[0]
            autopilot.stage(self.ledger, [item], at=NOW)
            before, result = self.publish()
            self.assertEqual(result["published"], 1)
        record = self.ledger["records"][0]
        record["automation"]["source_kind"] = "frozen"
        evidence = deepcopy(record["evidence"])
        after, result = self.publish()
        self.assertFalse(after["events"])
        self.assertEqual(result["suspended"], 1)
        self.assertEqual(record["evidence"], evidence)
        self.assertEqual(self.ghost.posts[0]["status"], "draft")
        writes = self.ghost.writes
        self.publish()
        self.assertEqual(self.ghost.writes, writes)

        rare = candidate(2, "watch_type", 1, address=item["source"]["hex"])
        rare["source"]["evidence"]["aircraft"].update(r=item["source"]["evidence"]["aircraft"]["r"], t="B52")
        db = self.database(item, rare)
        candidates = self.collect(db, public._instant(NOW).timestamp())
        self.assertEqual([c["source"]["kind"] for c in candidates], ["watch_type"])
        self.assertEqual(candidates[0]["key"], item["key"])
        autopilot.stage(self.ledger, candidates, at=NOW)
        after, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual(after["events"][0]["id"], before["events"][0]["id"])
        self.assertEqual(after["events"][0]["article_path"], before["events"][0]["article_path"])
        self.assertEqual(after["events"][0]["kind"], "watch_type")
        self.assertEqual(after["events"][0]["aircraft"]["type_code"], "B52")
        self.assertEqual(self.ghost.posts[0]["title"], after["events"][0]["story"]["headline"])
        self.assertNotIn("oscillation", self.ghost.posts[0]["html"])

    def test_old_generated_climb_text_is_replaced_when_an_independent_reason_wins(self):
        item = candidate(1, "rapid_climb", 6000)
        item["source"]["evidence"]["maneuver"]["median_rate_fpm"] = 5500
        labels = {**public.EPISODE_MEASUREMENTS, "median_rate_fpm": ("Median signed vertical rate", "ft/min")}
        with (
            patch.object(curation, "CONTEXT_KINDS", curation.CONTEXT_KINDS - {"rapid_climb"}),
            patch.object(public, "EPISODE_MEASUREMENTS", labels),
        ):
            autopilot.stage(self.ledger, [item], at=NOW)
            before, _ = self.publish()
        # Upgrade an existing installation whose ledger predates rendering receipts.
        self.ledger["records"][0]["automation"].pop("published_event")
        autopilot.remember_published(self.ledger, before)
        replacement = candidate(2, "watch_type", 1, address=item["source"]["hex"])
        replacement["key"] = item["key"]
        replacement["source"]["evidence"]["aircraft"]["t"] = "B52"
        autopilot.stage(self.ledger, [replacement], at=NOW)
        after, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual(after["events"][0]["id"], before["events"][0]["id"])
        self.assertEqual(self.ghost.posts[0]["title"], after["events"][0]["title"])
        self.assertEqual(after["events"][0]["aircraft"]["type_code"], "B52")
        self.assertEqual(self.ghost.posts[0]["custom_excerpt"], after["events"][0]["story"]["dek"])
        self.assertNotEqual(before["events"][0]["story"]["headline"], after["events"][0]["story"]["headline"])

    def test_exception_rejects_missing_conflicting_and_outside_interval_times(self):
        autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -8000)], at=NOW)
        original = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        for mode in ("missing", "zero", "short", "outside", "conflict"):
            with self.subTest(mode=mode):
                snapshot = deepcopy(original)
                event = snapshot["events"][0]
                rows = [m for m in event["measurements"] if m["key"] in curation.SUSTAINED_FIELDS]
                for row in rows[:1] if mode == "conflict" else rows:
                    if mode == "missing":
                        row.pop("start_utc")
                        row.pop("end_utc")
                    elif mode == "outside":
                        row.update(start_utc="2026-10-02T12:00:00Z", end_utc="2026-10-02T12:00:30Z")
                    else:
                        row["end_utc"] = row["start_utc"] if mode == "zero" else "2026-10-01T12:00:10Z"
                event["evidence_version"] = public.evidence_version(event)
                snapshot["snapshot_id"] = public._digest(
                    {k: v for k, v in snapshot.items() if k != "snapshot_id"}
                )
                with self.assertRaises(ValueError):
                    public.validate_snapshot(snapshot)

    def test_editorial_is_the_story_with_a_stable_story_url(self):
        item = candidate(1, "rapid_descent", -8000)
        packet = autopilot.evidence(item["source"], item["interval"])
        inputs = autopilot.editorial_inputs(self.ledger, packet, item["key"])
        metadata = autopilot.editorial(packet, item["key"], **inputs)
        story = metadata["story"]
        self.assertEqual((metadata["title"], metadata["summary"]), (story["headline"], story["dek"]))
        self.assertEqual(story["source"], "template")
        slug = public.article_slug(metadata["article_path"])
        self.assertTrue(metadata["article_path"].startswith("/stories/"))
        self.assertEqual(
            autopilot.editorial(packet, item["key"], **inputs)["article_path"], metadata["article_path"]
        )
        self.assertTrue(slug.endswith("-1-oct-2026"))
        # A slug held by another record moves the new one aside, keeping the date at the end.
        taken = autopilot.editorial(packet, item["key"], **{**inputs, "taken": frozenset({slug})})
        self.assertNotEqual(public.article_slug(taken["article_path"]), slug)
        self.assertTrue(public.article_slug(taken["article_path"]).endswith("-1-oct-2026"))
        projected = public._projection(packet, metadata)
        self.assertEqual(projected["story"], story)
        self.assertEqual(projected["context"]["topic"], metadata["context"]["topic"])
        self.assertNotIn("story", public.PUBLIC_EVIDENCE_FIELDS + public.OPTIONAL_EVIDENCE_FIELDS)
        self.assertEqual(
            projected["evidence_version"],
            public._projection(packet, public.PLACEHOLDER_EDITORIAL)["evidence_version"],
        )

    def test_sustained_exception_proof_is_bound_to_public_interval_measurements(self):
        autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -8000)], at=NOW)
        original = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        for field in curation.SUSTAINED_FIELDS:
            with self.subTest(field=field):
                snapshot = deepcopy(original)
                event = snapshot["events"][0]
                measurement = next(m for m in event["measurements"] if m["key"] == field)
                measurement["value"] += 1
                event["evidence_version"] = public.evidence_version(event)
                snapshot["snapshot_id"] = public._digest(
                    {k: v for k, v in snapshot.items() if k != "snapshot_id"}
                )
                with self.assertRaisesRegex(ValueError, "does not match"):
                    public.validate_snapshot(snapshot)

    def staged_exception(self, **decision):
        """A staged exception-grade descent with a stored selection decision."""
        autopilot.stage(self.ledger, [exceptional_descent(1, **decision)], at=NOW)
        record = self.ledger["records"][0]
        # The proof stays on the record so each reader sees exception-grade evidence.
        record["editorial"].pop("priority", None)
        event = autopilot._project(record)
        self.assertIn("priority_evidence", event)
        return record, event

    def test_the_stored_decision_is_saved_beside_the_boolean(self):
        record, _ = self.staged_exception(exceptional=False, reason="repeated_descent")
        automatic = record["automation"]
        self.assertIs(automatic["exceptional"], False)
        self.assertEqual(
            automatic["exception"],
            {
                "grade": True,
                "exceptional": False,
                "reason": "repeated_descent",
                "curation_version": curation.VERSION,
            },
        )

    def test_the_boolean_decides_over_the_stored_decision(self):
        record, event = self.staged_exception()
        self.assertTrue(autopilot.exception_standing(record, event))
        record["automation"]["exceptional"] = False
        self.assertFalse(autopilot.exception_standing(record, event))
        record, event = self.staged_exception(exceptional=False, reason="repeated_descent")
        self.assertFalse(autopilot.exception_standing(record, event))
        record["automation"]["exceptional"] = True
        self.assertTrue(autopilot.exception_standing(record, event))

    def test_exception_standing_reads_the_stored_decision_without_the_boolean(self):
        record, event = self.staged_exception(exceptional=False, reason="repeated_descent")
        del record["automation"]["exceptional"]
        self.assertFalse(autopilot.exception_standing(record, event))
        # The rerun without the descent context would promote it.
        self.assertTrue(autopilot._canonical(record, event))
        record["automation"]["exception"].update(exceptional=True, reason=None)
        self.assertTrue(autopilot.exception_standing(record, event))

    def test_a_malformed_or_outdated_stored_decision_counts_as_absent(self):
        for change in (
            {"reason": ["repeated_descent"]},
            {"exceptional": "no"},
            {"curation_version": curation.VERSION - 1},
        ):
            with self.subTest(change=change):
                self.setUp()
                record, event = self.staged_exception(exceptional=False, reason="repeated_descent")
                record["automation"]["exception"].update(change)
                del record["automation"]["exceptional"]
                # Without a valid decision or the boolean, the rerun decides.
                self.assertTrue(autopilot.exception_standing(record, event))

    def test_a_record_with_neither_keeps_the_older_rule(self):
        autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -9000)], at=NOW)
        record = self.ledger["records"][0]
        self.assertNotIn("exceptional", record["automation"])
        self.assertNotIn("exception", record["automation"])
        event = autopilot._project(record)
        self.assertTrue(autopilot.exception_standing(record, event))
        record["automation"]["source"]["evidence"]["aircraft"]["t"] = "C208"
        record["automation"]["source"]["evidence"]["maneuver"]["context_tags"] = ["airport_descent_context"]
        self.assertFalse(autopilot.exception_standing(record, event))

    def test_a_refreshed_source_without_a_decision_drops_the_block(self):
        record, _ = self.staged_exception()
        # A later pass that does not reselect the released record refreshes its finding only.
        again = candidate(1, "rapid_descent", -9000)
        again["source"]["evidence"]["aircraft"]["t"] = "C208"
        again["source"]["evidence"]["maneuver"]["context_tags"] = ["airport_descent_context"]
        autopilot.stage(self.ledger, [again], at=NOW)
        record = self.ledger["records"][0]
        record["editorial"].pop("priority", None)
        event = autopilot._project(record)
        self.assertIn("priority_evidence", event)
        self.assertNotIn("exception", record["automation"])
        self.assertFalse(autopilot.exception_standing(record, event))
        self.assertFalse(autopilot.exception_standing(record, event, sticky=True))
        # The same pass over an unchanged finding keeps the stored decision.
        record, _ = self.staged_exception()
        autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -9000)], at=NOW)
        self.assertIn("exception", self.ledger["records"][0]["automation"])

    def test_a_removed_story_drops_the_block_when_its_source_changes(self):
        record, _ = self.staged_exception()
        record["automation"]["removed"] = True
        again = exceptional_descent(1)
        again["source"]["evidence"]["maneuver"]["context_tags"] = ["emergency_descent_profile"]
        autopilot._settle(record, again["source"], again, None)
        # A removed story keeps the decisions it was published with, but not one about another finding.
        self.assertNotIn("exception", record["automation"])
        self.assertIs(record["automation"]["exceptional"], True)

    def test_a_stored_decision_holds_only_while_the_saved_finding_qualifies(self):
        for decision in ({}, {"exceptional": False, "reason": "review"}):
            with self.subTest(decision=decision):
                self.setUp()
                record, event = self.staged_exception(**decision)
                source = record["automation"]["source"]["evidence"]
                source["aircraft"]["t"] = "C208"
                source["maneuver"]["context_tags"] = ["airport_descent_context"]
                self.assertFalse(autopilot.exception_standing(record, event))
                self.assertFalse(autopilot.exception_standing(record, event, sticky=True))

    def test_an_unhashable_stored_reason_is_an_invalid_block_not_an_error(self):
        record, event = self.staged_exception(exceptional=False, reason="repeated_descent")
        record["automation"]["exception"]["reason"] = ["repeated_descent"]
        del record["automation"]["exceptional"]
        # Without a valid block or the boolean, the older rule decides.
        self.assertTrue(autopilot.exception_standing(record, event))
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)

    def test_exception_standing_keeps_the_proof_gate(self):
        record, event = self.staged_exception()
        unproved = {key: value for key, value in event.items() if key != "priority_evidence"}
        self.assertFalse(autopilot.exception_standing(record, unproved))
        self.assertFalse(autopilot.exception_standing(record, unproved, sticky=True))

    def test_a_sticky_story_keeps_its_standing_while_the_saved_finding_qualifies(self):
        # The saved finding here passes the rerun whatever the stored reason. The selection
        # gives no_grade, crewed_7400, and jump_type only to findings that fail it.
        for reason in curation.EXCEPTION_REASONS + ("aircraft_cap",):
            with self.subTest(reason=reason):
                self.setUp()
                record, event = self.staged_exception(exceptional=False, reason=reason)
                self.assertFalse(autopilot.exception_standing(record, event))
                self.assertTrue(autopilot.exception_standing(record, event, sticky=True))

    def test_nine_already_live_then_exceptional_afternoon_event_publishes_automatically(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 10)], at=NOW)
        initial, result = self.publish()
        self.assertEqual(result["published"], 9)
        original = {event["id"]: event["article_path"] for event in initial["events"]}
        autopilot.stage(self.ledger, [candidate(10), candidate(11, "rapid_descent", -9000)], at=NOW)
        later, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual(len(later["events"]), 10)
        self.assertEqual(len(self.ghost.posts), 10)
        self.assertEqual(result["deferred"], 1)
        self.assertTrue(any(event.get("priority_evidence") for event in later["events"]))
        self.assertTrue(
            all(
                any(event["id"] == key and event["article_path"] == path for event in later["events"])
                for key, path in original.items()
            )
        )
        # A new process uses persisted release state, not today's receiver retention.
        self.ledger = deepcopy(self.saved)
        again, result = self.publish()
        self.assertEqual(again, later)
        self.assertEqual(result["published"], 0)
        self.assertEqual(self.ghost.writes, 10)

    def test_lost_post_response_is_reconciled_without_duplicate_or_manual_approval(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.ghost.lose_response = True
        snapshot, result = self.publish()
        self.assertEqual(result["failed"], 1)
        self.assertFalse(snapshot["events"])
        snapshot, result = self.publish()
        self.assertEqual(len(snapshot["events"]), 1)
        self.assertEqual(self.ghost.writes, 1)
        self.assertEqual(self.ledger["records"][0]["state"], "approved")

    def test_lost_put_response_never_allows_a_tenth_ordinary_post_between_writes(self):
        exceptional_versions, ordinary_after_write = set(), []
        original_save = self.ghost.save

        def save_and_count(post, current=None):
            try:
                return original_save(post, current)
            finally:
                # Count the evidence actually committed to Ghost, including a
                # successful write whose response never reached the worker.
                ordinary = 0
                for saved in self.ghost.posts:
                    if saved["status"] != "published" or saved["visibility"] != "public":
                        continue
                    versions = re.findall(r'data-evidence-version="([a-f0-9]{64})"', saved["html"])
                    self.assertEqual(len(versions), 1)
                    ordinary += versions[0] not in exceptional_versions
                ordinary_after_write.append(ordinary)

        def later(item):
            item["source"]["first_seen"] += 3600
            item["source"]["last_seen"] += 3600
            metrics = item["source"]["evidence"]["maneuver"]
            for key in ("started_at", "ended_at", "sustained_started_at", "sustained_ended_at"):
                if key in metrics:
                    metrics[key] += 3600
            item["interval"] = (item["source"]["first_seen"], item["source"]["last_seen"])
            return item

        with patch.object(self.ghost, "save", side_effect=save_and_count):
            autopilot.stage(self.ledger, [candidate(i) for i in range(1, 9)], at=NOW)
            self.publish()
            autopilot.stage(
                self.ledger,
                [candidate(11, "rapid_descent", -9000), later(candidate(12, "rapid_descent", -9000))],
                at=NOW,
            )
            for record in self.ledger["records"]:
                event = public._projection(record["evidence"], record["editorial"])
                if event.get("priority_evidence"):
                    exceptional_versions.add(event["evidence_version"])
            initial, _ = self.publish()
            self.assertEqual(len(initial["events"]), 10)
            self.assertEqual(ordinary_after_write[-1], 8)
            autopilot.stage(self.ledger, [candidate(11, "rapid_descent", -5000)], at=NOW)
            self.ghost.lose_response = True
            _, result = self.publish()
            self.assertEqual(result["failed"], 1)
            self.assertEqual(ordinary_after_write[-1], 9)
            pending = next(record for record in self.ledger["records"] if record["episode_key"] == "11")
            self.assertEqual(pending["state"], "pending")
            # The later observation sorts first. Accounting must recognize the
            # earlier committed correction before deciding whether to write it.
            autopilot.stage(self.ledger, [later(candidate(12, "rapid_descent", -5000))], at=NOW)
            snapshot, result = self.publish()
            self.assertEqual(result["failed"], 0)
            self.assertEqual(result["suspended"], 1)
            self.assertEqual(len(snapshot["events"]), 9)
            self.assertTrue(all(not event.get("priority_evidence") for event in snapshot["events"]))
            self.assertEqual(max(ordinary_after_write), 9)
            writes = self.ghost.writes
            self.publish()
            self.assertEqual(self.ghost.writes, writes)

    def test_owner_unpublication_after_a_lost_post_response_is_respected(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.ghost.lose_response = True
        _, result = self.publish()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.ghost.posts[0]["status"], "published")
        self.ghost.posts[0]["status"] = "draft"
        for _ in range(2):
            snapshot, _ = self.publish()
            self.assertFalse(snapshot["events"])
            self.assertEqual(self.ghost.posts[0]["status"], "draft")
        self.assertEqual(self.ghost.writes, 1)

    def test_uncertain_exception_is_skipped_even_with_a_keep_vote(self):
        item = candidate(1, "rapid_descent", -9000, flags=("rate_altitude_disagreement",))
        item["source"]["verdict"] = "keep"
        result = autopilot.stage(self.ledger, [item, candidate(2)], at=NOW)
        self.assertEqual(result["rejected"], 1)
        snapshot, result = self.publish()
        self.assertEqual(len(snapshot["events"]), 1)
        self.assertEqual(snapshot["events"][0]["kind"], "vintage")

    def test_legacy_archive_squawk_hits_do_not_certify_fresh_transponder_evidence(self):
        for verdict in (None, "keep"):
            with self.subTest(verdict=verdict):
                self.ledger = public.new_ledger("2026-10-01")
                self.ghost = Ghost()
                item = candidate(1, "archive_squawk_7700", 7700)
                source = item["source"]
                source["evidence"].pop("maneuver")
                source["evidence"]["aircraft"]["squawk"] = "7700"
                source.update(verdict=verdict, hits=100, unit="code")
                self.assertFalse(autopilot.eligible(source))
                result = autopilot.stage(self.ledger, [item, candidate(2)], at=NOW)
                self.assertEqual(result["rejected"], 1)
                snapshot, _ = self.publish()
                self.assertEqual([event["kind"] for event in snapshot["events"]], ["vintage"])
                self.assertEqual(self.ghost.writes, 1)

    def test_contradictory_quality_flags_cannot_gain_exception_status(self):
        item = candidate(1, "rapid_descent", -9000, flags=("rate_altitude_disagreement",))
        item["source"]["evidence"]["maneuver"]["quality_review_required"] = False
        packet = autopilot.evidence(item["source"], item["interval"])
        projected = public._projection(packet, autopilot.editorial(packet, item["key"]))
        self.assertNotIn("priority_evidence", projected)
        self.assertFalse(autopilot.eligible(item["source"]))
        self.assertEqual(autopilot.stage(self.ledger, [item], at=NOW)["rejected"], 1)
        snapshot, _ = self.publish()
        self.assertFalse(snapshot["events"])
        self.assertEqual(self.ghost.writes, 0)

    def test_rejected_latest_source_blocks_a_previously_staged_event(self):
        item = candidate(1)
        autopilot.stage(self.ledger, [item], at=NOW)
        rejected = deepcopy(item)
        rejected["source"]["verdict"] = "bad_data"
        self.assertEqual(autopilot.stage(self.ledger, [rejected], at=NOW)["rejected"], 1)
        snapshot, _ = self.publish()
        self.assertFalse(snapshot["events"])
        self.assertEqual(self.ghost.writes, 0)

    def test_collect_reconciles_a_reviewed_out_pending_source(self):
        item = candidate(1)
        database = self.database(item)
        now = public._instant(NOW).timestamp()
        selected = self.collect(database, now, ledger=self.ledger)
        self.assertEqual(len(selected), 1)
        autopilot.stage(self.ledger, selected, at=NOW)
        # The engine exports the reviewer's verdict with the finding.
        reviewed = deepcopy(item)
        reviewed["source"]["verdict"] = "bad_data"
        selected = self.collect(self.database(reviewed), now, ledger=self.ledger)
        self.assertFalse(selected)
        autopilot.stage(self.ledger, selected, at=NOW)
        snapshot, _ = self.publish()
        self.assertFalse(snapshot["events"])
        self.assertEqual(self.ghost.writes, 0)

    def test_a_record_staged_before_norms_takes_one_when_collect_restages_it(self):
        item = candidate(2, "seasonal_speed_record", value=350)
        item["source"]["unit"] = "kn"
        database = self.database(item)
        now = public._instant(NOW).timestamp()
        with patch.object(autopilot, "_place", return_value=None):
            autopilot.stage(self.ledger, [item], at=NOW)
        automatic = self.ledger["records"][0]["automation"]
        automatic.pop("type_norm", None)
        automatic["released_at"] = NOW
        block = {**story_fixtures.NORM, "low": 300, "high": 340}
        norms = {item["source"]["id"]: block}
        with patch.object(autopilot.curation, "choose", return_value={"selected": []}):
            restaged = self.collect(database, now, ledger=self.ledger, norms=norms)
            self.assertEqual([entry["type_norm"] for entry in restaged], [block])
            # A record that already holds its norm, None included, is restaged without one.
            automatic["type_norm"] = None
            self.assertNotIn("type_norm", self.collect(database, now, ledger=self.ledger, norms=norms)[0])

    def test_uncertain_kept_finding_does_not_hide_a_valid_type_match_in_its_encounter(self):
        distinctive = candidate(1, "watch_type", None)
        distinctive["source"]["evidence"]["aircraft"]["t"] = "B52"
        uncertain = candidate(
            2,
            "rapid_descent",
            -9000,
            address=distinctive["source"]["hex"],
            flags=("rate_altitude_disagreement",),
        )
        uncertain["source"]["evidence"]["aircraft"] = deepcopy(distinctive["source"]["evidence"]["aircraft"])
        for item in (distinctive, uncertain):
            item["source"]["evidence"]["encounter_start"] = distinctive["source"]["first_seen"]
        # The engine exports the reviewer's Keep vote with the uncertain finding.
        uncertain["source"]["verdict"] = "keep"
        database = self.database(distinctive, uncertain)
        now = public._instant(NOW).timestamp()
        selected = self.collect(database, now, ledger=self.ledger)
        self.assertEqual([item["source"]["id"] for item in selected], [distinctive["source"]["id"]])
        autopilot.stage(self.ledger, selected, at=NOW)
        snapshot, _ = self.publish()
        self.assertEqual([event["kind"] for event in snapshot["events"]], ["watch_type"])
        self.assertNotIn("priority_evidence", snapshot["events"][0])

    def test_ordinary_quota_includes_approved_legacy_publications(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 10)], at=NOW)
        original, _ = self.publish()
        for record in self.ledger["records"]:
            record.pop("automation")
        for post in self.ghost.posts:
            post["tags"] = []
        autopilot.stage(self.ledger, [candidate(10)], at=NOW)
        snapshot, result = self.publish()

        def without_release(snapshot):
            # Release times live in the automation blocks this test removes.
            return [
                {
                    key: value
                    for key, value in event.items()
                    if key not in {"released_at", "content_revised_at"}
                }
                for event in snapshot["events"]
            ]

        self.assertEqual(without_release(snapshot), without_release(original))
        self.assertEqual(result["published"], 0)
        self.assertEqual(self.ghost.writes, 9)
        self.assertEqual(len(self.ghost.posts), 9)

    def test_corrected_overflow_is_suspended_before_it_can_exceed_the_ordinary_quota(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 10)], at=NOW)
        self.publish()
        autopilot.stage(self.ledger, [candidate(11, "rapid_descent", -9000)], at=NOW)
        initial, _ = self.publish()
        exceptional = next(event for event in initial["events"] if event.get("priority_evidence"))
        slug = public.article_slug(exceptional["article_path"])
        writes = self.ghost.writes
        autopilot.stage(self.ledger, [candidate(11, "rapid_descent", -5000)], at=NOW)
        snapshot, _ = self.publish()
        public.validate_snapshot(snapshot)
        self.assertEqual(len(snapshot["events"]), 9)
        updates = [request for request in self.ghost.requests[writes:] if request.get("slug", slug) == slug]
        self.assertTrue(updates)
        self.assertTrue(all(request["status"] == "draft" for request in updates))
        corrected = next(post for post in self.ghost.posts if post["slug"] == slug)
        self.assertEqual(corrected["status"], "draft")
        record = next(row for row in self.ledger["records"] if row["id"] == exceptional["id"])
        self.assertEqual(record["editorial"]["article_path"], exceptional["article_path"])
        # A policy suspension can resume automatically; an owner-unpublished post stays unpublished.
        ordinary = next(post for post in self.ghost.posts if post["slug"] != slug)
        ordinary["status"] = "draft"
        restored, _ = self.publish()
        self.assertEqual(len(restored["events"]), 9)
        event = next(event for event in restored["events"] if event["id"] == exceptional["id"])
        self.assertNotIn("priority_evidence", event)
        self.assertEqual(event["article_path"], exceptional["article_path"])
        self.assertEqual(
            next(post for post in self.ghost.posts if post["id"] == ordinary["id"])["status"], "draft"
        )

    def test_interval_correction_into_a_full_previous_utc_day_suspends_before_writing(self):
        self.ledger = public.new_ledger("2026-09-30")
        autopilot.stage(self.ledger, [candidate(i, day=-1) for i in range(1, 10)], at=NOW)
        self.publish()
        item = candidate(11)
        autopilot.stage(self.ledger, [item], at=NOW)
        initial, _ = self.publish()
        moving = next(
            event for event in initial["events"] if event["observed_start"].startswith("2026-10-01")
        )
        self.assertEqual(len(initial["events"]), 10)
        corrected = deepcopy(item)
        corrected["interval"] = (
            public._instant("2026-09-30T23:55:00Z").timestamp(),
            item["interval"][1],
        )
        writes = self.ghost.writes
        autopilot.stage(self.ledger, [corrected], at=NOW)
        snapshot, _ = self.publish()
        self.assertEqual(len(snapshot["events"]), 9)
        self.assertTrue(all(event["observed_start"].startswith("2026-09-30") for event in snapshot["events"]))
        self.assertNotIn(moving["id"], {event["id"] for event in snapshot["events"]})
        public.validate_snapshot(snapshot)
        requests = self.ghost.requests[writes:]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["status"], "draft")
        post = next(
            post for post in self.ghost.posts if post["slug"] == public.article_slug(moving["article_path"])
        )
        self.assertEqual(post["status"], "draft")
        record = next(row for row in self.ledger["records"] if row["id"] == moving["id"])
        self.assertEqual(record["editorial"]["article_path"], moving["article_path"])

    def test_retention_of_the_same_encounter_does_not_duplicate_an_exception(self):
        item = candidate(1, "rapid_descent", -9000)
        source = item["source"]
        start = source["first_seen"] - 600
        context = candidate(2, "circling", 3, address=source["hex"])
        context["source"]["evidence"]["aircraft"] = deepcopy(source["evidence"]["aircraft"])
        context["source"].update(first_seen=start, last_seen=source["first_seen"] + 60)
        for candidate_item in (item, context):
            candidate_item["source"]["evidence"]["encounter_start"] = start
        now = public._instant(NOW).timestamp()
        selected = self.collect(self.database(item, context), now, ledger=self.ledger)
        self.assertEqual(len(selected), 1)
        autopilot.stage(self.ledger, selected, at=NOW)
        initial, _ = self.publish()
        # Retention drops the earlier finding of the encounter from the engine's view.
        selected = self.collect(self.database(item), now, ledger=self.ledger)
        autopilot.stage(self.ledger, selected, at=NOW)
        retained, _ = self.publish()
        self.assertEqual(len(self.ledger["records"]), 1)
        self.assertEqual(len(self.ghost.posts), 1)
        self.assertEqual(len(retained["events"]), 1)
        for key in ("id", "article_path", "observed_start", "observed_end"):
            self.assertEqual(retained["events"][0][key], initial["events"][0][key])

    def test_foreign_slug_collision_is_not_overwritten(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.ghost.posts.append(
            {
                "slug": public.article_slug(self.ledger["records"][0]["editorial"]["article_path"]),
                "id": "a" * 24,
                "status": "draft",
                "visibility": "public",
                "tags": [],
                "updated_at": NOW,
            }
        )
        snapshot, result = self.publish()
        self.assertEqual(result["failed"], 1)
        self.assertFalse(snapshot["events"])
        self.assertEqual(self.ghost.writes, 0)

    def test_one_uneditable_owned_article_does_not_starve_other_publications(self):
        for broken in ("missing_evidence", "invalid_lexical"):
            with self.subTest(broken=broken):
                self.ledger = public.new_ledger("2026-10-01")
                self.ghost = Ghost()
                item = candidate(1)
                item["source"]["first_seen"] += 3600
                item["source"]["last_seen"] += 3600
                item["interval"] = (item["source"]["first_seen"], item["source"]["last_seen"])
                autopilot.stage(self.ledger, [item], at=NOW)
                self.publish()
                if broken == "invalid_lexical":
                    self.ghost.posts[0]["lexical"] = "invalid editor JSON"
                else:
                    lexical = json.loads(self.ghost.posts[0]["lexical"])
                    lexical["root"]["children"] = [
                        {"type": "html", "version": 1, "html": "<p>Owner removed the evidence block.</p>"}
                    ]
                    self.ghost.posts[0]["lexical"] = json.dumps(lexical)
                damaged = deepcopy(self.ghost.posts[0])
                autopilot.stage(self.ledger, [candidate(2)], at=NOW)
                snapshot, result = self.publish()
                self.assertEqual(result["failed"], 1)
                self.assertEqual(result["published"], 1)
                self.assertEqual(len(snapshot["events"]), 2)
                self.assertEqual(self.ghost.writes, 2)
                self.assertEqual(
                    next(post for post in self.ghost.posts if post["id"] == damaged["id"]), damaged
                )

    def test_user_unpublication_and_withdrawal_are_not_undone(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        self.ghost.posts[0]["status"] = "draft"
        snapshot, result = self.publish()
        self.assertFalse(snapshot["events"])
        self.assertEqual(self.ghost.writes, 1)
        public.set_review_state(self.ledger, self.ledger["records"][0]["id"], "withdrawn", at=NOW)
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.assertEqual(self.ledger["records"][0]["state"], "withdrawn")

    def test_withdrawn_unmanaged_old_policy_dossier_does_not_block_live_publication(self):
        import test_weekly_spotlight as weekly_tests

        routine = weekly_tests.finding()
        routine.update(kind="circling", value=3, unit="loops")
        with (
            patch.object(weekly_tests, "finding", return_value=routine),
            patch.object(curation, "CONTEXT_KINDS", frozenset()),
        ):
            self.ledger, withdrawn_id, _, _ = weekly_tests.fixture()
        public.set_review_state(self.ledger, withdrawn_id, "withdrawn", at=weekly_tests.NOW)
        with patch(__name__ + ".NOW", weekly_tests.NOW):
            autopilot.stage(self.ledger, [candidate(1, day=3)], at=NOW)
            snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual(len(snapshot["events"]), 1)
        self.assertNotEqual(snapshot["events"][0]["id"], withdrawn_id)

    def test_deleted_released_post_is_not_recreated(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        self.ghost.posts.clear()
        for _ in range(2):
            snapshot, _ = self.publish()
            self.assertFalse(snapshot["events"])
            self.assertFalse(self.ghost.posts)
        self.assertEqual(self.ghost.writes, 1)

    def test_deleting_an_automatically_suspended_post_prevents_its_recreation(self):
        item = candidate(1)
        autopilot.stage(self.ledger, [item], at=NOW)
        self.publish()
        rejected = deepcopy(item)
        rejected["source"]["verdict"] = "bad_data"
        autopilot.stage(self.ledger, [rejected], at=NOW)
        self.publish()
        self.assertEqual(self.ghost.posts[0]["status"], "draft")
        writes = self.ghost.writes
        self.ghost.posts.clear()
        autopilot.stage(self.ledger, [item], at=NOW)
        for _ in range(2):
            snapshot, _ = self.publish()
            self.assertFalse(snapshot["events"])
            self.assertFalse(self.ghost.posts)
        self.assertEqual(self.ghost.writes, writes)

    def test_repeated_unchanged_passes_never_rewrite_the_article(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        initial, _ = self.publish()
        post = deepcopy(self.ghost.posts[0])
        for _ in range(3):
            snapshot, result = self.publish()
            self.assertEqual(snapshot, initial)
            self.assertEqual(result["published"], 0)
            self.assertEqual(result["unchanged"], 1)
            self.assertEqual(self.ghost.posts[0], post)
        self.assertEqual(self.ghost.writes, 1)

    def test_factual_update_preserves_owner_title_deck_and_surrounding_content(self):
        autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -5000)], at=NOW)
        self.publish()
        post = self.ghost.posts[0]
        post.update(title="Owner's edited headline", custom_excerpt="Owner's edited deck.")
        lexical = json.loads(post["lexical"])
        introduction = "<p>An owner-written introduction.</p>"
        closing = "<p>An owner-written closing paragraph.</p>"
        lexical["root"]["children"][0]["html"] = (
            introduction + lexical["root"]["children"][0]["html"] + closing
        )
        paragraph = {
            "type": "paragraph",
            "version": 1,
            "children": [{"type": "text", "version": 1, "text": "A separate editor paragraph."}],
        }
        lexical["root"]["children"].append(paragraph)
        post["lexical"] = json.dumps(lexical)
        post["tags"].append({"name": "Owner topic"})
        original_id, original_date = post["id"], post["published_at"]
        autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -6000)], at=NOW)
        snapshot, _ = self.publish()
        revised = self.ghost.posts[0]
        self.assertEqual(revised["id"], original_id)
        self.assertEqual(revised["published_at"], original_date)
        self.assertEqual(revised["title"], "Owner's edited headline")
        self.assertEqual(revised["custom_excerpt"], "Owner's edited deck.")
        self.assertIn({"name": "Owner topic"}, revised["tags"])
        updated = json.loads(revised["lexical"])["root"]["children"]
        self.assertEqual(updated[-1], paragraph)
        self.assertTrue(updated[0]["html"].startswith(introduction))
        self.assertTrue(updated[0]["html"].endswith(closing))
        self.assertIn(snapshot["events"][0]["evidence_version"], revised["html"].replace("<wbr>", ""))
        self.assertEqual(
            next(
                item["value"]
                for item in snapshot["events"][0]["measurements"]
                if item["key"] == "peak_rate_fpm"
            ),
            -6000,
        )
        writes = self.ghost.writes
        for _ in range(2):
            self.publish()
        self.assertEqual(self.ghost.writes, writes)

    def test_unedited_generated_title_and_deck_follow_factual_corrections(self):
        original_type = candidate(1, "watch_type", None)
        original_type["source"]["evidence"]["aircraft"]["t"] = "E6"
        corrected_type = deepcopy(original_type)
        corrected_type["source"]["evidence"]["aircraft"]["t"] = "U2"
        cases = (
            (candidate(1, "rapid_descent", -5000), candidate(1, "rapid_descent", -6000)),
            (original_type, corrected_type),
        )
        original_editorial = autopilot.editorial

        def revised(packet, key, **options):
            # Corrected evidence yields new generated text, as a changed type or value does.
            metadata = original_editorial(packet, key, **options)
            story = {
                **metadata["story"],
                "headline": metadata["story"]["headline"] + " after a correction",
                "dek": "A revised deck that follows the corrected record.",
            }
            return {**metadata, "title": story["headline"], "summary": story["dek"], "story": story}

        for original, corrected in cases:
            with self.subTest(kind=original["source"]["kind"]):
                self.ledger = public.new_ledger("2026-10-01")
                self.ghost = Ghost()
                autopilot.stage(self.ledger, [original], at=NOW)
                self.publish()
                old = deepcopy(self.ghost.posts[0])
                with patch.object(autopilot, "editorial", side_effect=revised):
                    autopilot.stage(self.ledger, [corrected], at=NOW)
                snapshot, _ = self.publish()
                event = snapshot["events"][0]
                post = self.ghost.posts[0]
                self.assertNotEqual(post["custom_excerpt"], old["custom_excerpt"])
                self.assertEqual(post["custom_excerpt"], event["summary"])
                self.assertEqual(post["title"], event["title"])
                self.assertNotEqual(post["title"], old["title"])
                self.assertEqual(post["meta_description"], event["story"]["dek"])
                self.assertEqual(post["og_title"], event["story"]["headline"])
                self.assertEqual(post["meta_title"], event["story"]["seo_title"])
                self.assertEqual(post["slug"], old["slug"])
                self.assertEqual(post["published_at"], old["published_at"])
                writes = self.ghost.writes
                self.publish()
                self.assertEqual(self.ghost.writes, writes)

    def test_unedited_long_generated_summary_updates_without_removing_owner_prose(self):
        original_editorial = autopilot.editorial

        def long_editorial(packet, key, **options):
            # An older record without a story object keeps its long summary in the body.
            metadata = original_editorial(packet, key, **options)
            metadata = {
                field: value for field, value in metadata.items() if field not in {"story", "context"}
            }
            metadata["summary"] += (
                " This synthetic account distinguishes received observations from the limits of a database match."
                * 2
                + " The database description reads "
                + packet["aircraft"]["type_description"]
                + "."
            )
            return metadata

        self.enterContext(patch.object(autopilot, "editorial", side_effect=long_editorial))
        item = candidate(1, "watch_type", None)
        item["source"]["evidence"]["aircraft"].update(
            t="E6", desc="Original synthetic description " + "x" * 129
        )
        autopilot.stage(self.ledger, [item], at=NOW)
        original, _ = self.publish()
        old_summary = original["events"][0]["summary"]
        self.assertGreater(len(old_summary), 300)
        self.assertFalse(self.ghost.posts[0].get("custom_excerpt"))
        lexical = json.loads(self.ghost.posts[0]["lexical"])
        owner = "<p>Owner-written context stays in the article.</p>"
        lexical["root"]["children"][0]["html"] = owner + lexical["root"]["children"][0]["html"]
        self.ghost.posts[0]["lexical"] = json.dumps(lexical)
        corrected = deepcopy(item)
        corrected["source"]["evidence"]["aircraft"]["desc"] = "Corrected synthetic description " + "y" * 128
        autopilot.stage(self.ledger, [corrected], at=NOW)
        snapshot, _ = self.publish()
        html = self.ghost.posts[0]["html"]
        self.assertIn(owner, html)
        self.assertIn(escape(snapshot["events"][0]["summary"]), html)
        self.assertNotIn(escape(old_summary), html)
        writes = self.ghost.writes
        self.publish()
        self.assertEqual(self.ghost.writes, writes)

    def test_generated_summary_crossing_deck_limit_clears_and_restores_excerpt(self):
        autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -5000)], at=NOW)
        self.publish()
        self.assertTrue(self.ghost.posts[0].get("custom_excerpt"))
        original_editorial = autopilot.editorial

        def long_editorial(packet, key, **options):
            # An older record without a story object keeps its long summary in the body.
            metadata = original_editorial(packet, key, **options)
            metadata = {
                field: value for field, value in metadata.items() if field not in {"story", "context"}
            }
            metadata["summary"] += " Additional synthetic explanation of the source and its limits." * 5
            return metadata

        with patch.object(autopilot, "editorial", side_effect=long_editorial):
            autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -6000)], at=NOW)
        long_snapshot, _ = self.publish()
        long_summary = long_snapshot["events"][0]["summary"]
        self.assertGreater(len(long_summary), 300)
        self.assertIsNone(self.ghost.posts[0].get("custom_excerpt"))
        self.assertIn(escape(long_summary), self.ghost.posts[0]["html"])
        autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -5500)], at=NOW)
        short_snapshot, _ = self.publish()
        self.assertEqual(self.ghost.posts[0]["custom_excerpt"], short_snapshot["events"][0]["summary"])
        self.assertNotIn(escape(long_summary), self.ghost.posts[0]["html"])
        writes = self.ghost.writes
        self.publish()
        self.assertEqual(self.ghost.writes, writes)

    def test_weak_or_forged_exception_does_not_bypass_snapshot_cap(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 11)], at=NOW)
        review = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        event = review["events"][0]
        event["priority_evidence"] = {
            "confirmed": True,
            "quality_review_required": False,
            "duration_seconds": 30,
            "sample_count": 4,
            "value": -9000,
        }
        event["evidence_version"] = public.evidence_version(event)
        review["snapshot_id"] = public._digest(
            {key: value for key, value in review.items() if key != "snapshot_id"}
        )
        with self.assertRaisesRegex(ValueError, "exceptional-event"):
            public.validate_snapshot(review)

    def test_exception_proof_cannot_claim_a_stronger_rate_than_the_measurement(self):
        autopilot.stage(self.ledger, [candidate(1, "rapid_descent", -5000)], at=NOW)
        review = public.export_snapshot(self.ledger, mode="review", as_of=NOW)
        event = review["events"][0]
        self.assertNotIn("priority_evidence", event)
        event["priority_evidence"] = {
            "confirmed": True,
            "quality_review_required": False,
            "duration_seconds": 720,
            "sample_count": 46,
            "value": -9000,
        }
        event["evidence_version"] = public.evidence_version(event)
        review["snapshot_id"] = public._digest(
            {key: value for key, value in review.items() if key != "snapshot_id"}
        )
        with self.assertRaises(ValueError):
            public.validate_snapshot(review)


class StoryPublicationTests(unittest.TestCase):
    """Story URLs, Ghost fields and owned prose: the redo's publication hardening."""

    def setUp(self):
        self.ledger = public.new_ledger("2026-10-01")
        self.ghost = Ghost()

    def publish(self, at=NOW):
        return autopilot.publish(self.ledger, self.ghost, persist=lambda value: None, at=at)

    def post(self, record):
        return next(post for post in self.ghost.posts if post["slug"] == autopilot._slug(record))

    def test_recent_stories_carry_the_fact_ids_a_story_listed(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        record = self.ledger["records"][0]
        story = record["editorial"]["story"]
        recent = autopilot.recent_stories(self.ledger["records"], autopilot.observation_start(record))
        self.assertEqual(recent[0]["facts"], sorted(story["facts"]))
        self.assertTrue(recent[0]["facts"])

    def test_a_pass_works_out_each_storys_fact_ids_once(self):
        autopilot.stage(self.ledger, [candidate(index) for index in (1, 2, 3)], at=NOW)
        records = self.ledger["records"]
        self.assertEqual(len(records), 3)
        later = public.instant("2030-01-01T00:00:00Z")

        def recent(inputs):
            return [
                autopilot.editorial_inputs(
                    self.ledger, record["evidence"], record["episode_key"], inputs=inputs, recent_until=later
                )["recent"]
                for record in records
            ]

        expected = recent(autopilot.NO_INPUTS)
        self.assertEqual([len(stories) for stories in expected], [2, 2, 2])
        inputs = autopilot.StoryInputs(used={})
        with patch.object(autopilot, "_listed_facts", wraps=autopilot._listed_facts) as listed:
            self.assertEqual(recent(inputs), expected)
            self.assertEqual(recent(inputs), expected)
            # Six pairs of records, each story worked out once.
            self.assertEqual(listed.call_count, 3)
            # A story rewritten within the pass is read anew.
            story = records[0]["editorial"]["story"]
            records[0]["editorial"]["story"] = {**story, "paragraphs": [*story["paragraphs"], "Another."]}
            recent(inputs)
            self.assertEqual(listed.call_count, 4)

    def test_an_ai_story_lists_only_the_type_facts_its_text_relies_on(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        record = self.ledger["records"][0]
        story = record["editorial"]["story"]
        used = "Cessna built the 172 Skyhawk with a high wing and fixed landing gear."
        carried = "The Cessna 172 first flew in 1955."
        story["facts"].update({"tp.C172.1": used, "tp.C172.2": carried})
        story["paragraphs"] = [story["paragraphs"][0] + " " + used]
        start = autopilot.observation_start(record)
        # A template story lists what its own sentences used; an AI story's carried type facts
        # come from the template, so only the ones its text restates count as used.
        self.assertIn("tp.C172.2", autopilot.recent_stories(self.ledger["records"], start)[0]["facts"])
        story["source"] = "ai"
        listed = autopilot.recent_stories(self.ledger["records"], start)[0]["facts"]
        self.assertIn("tp.C172.1", listed)
        self.assertNotIn("tp.C172.2", listed)
        self.assertIn("id.type", listed)

    def test_new_posts_carry_the_story_meta_fields_and_topic_tag(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        event, request = snapshot["events"][0], self.ghost.requests[0]
        story = event["story"]
        self.assertEqual(request["slug"], public.article_slug(event["article_path"]))
        self.assertTrue(event["article_path"].startswith("/stories/"))
        self.assertEqual(
            {field: request[field] for field in publication.GENERATED_FIELDS},
            {
                "title": story["headline"],
                "custom_excerpt": story["dek"],
                "meta_title": story["seo_title"],
                "meta_description": story["dek"],
                "og_title": story["headline"],
                "og_description": story["dek"],
                "twitter_title": story["headline"],
                "twitter_description": story["dek"],
            },
        )
        # The topic is the primary tag; the ownership tag stays internal.
        self.assertEqual(request["tags"][0], publication.topic_tag(event["context"]["topic"]))
        self.assertIn({"name": "#autopilot-" + event["id"]}, request["tags"])
        self.assertIn('data-story-version="', self.ghost.posts[0]["html"])
        self.assertEqual(event["released_at"], NOW)
        receipt = self.ledger["records"][0]["automation"]["published_post"]
        self.assertEqual(receipt["fields"]["meta_title"], story["seo_title"])
        self.assertTrue(receipt["card_sha256"] and receipt["story_sha256"])

    def test_a_slug_held_by_any_post_or_page_blocks_the_record_without_a_post(self):
        autopilot.stage(self.ledger, [candidate(1), candidate(2)], at=NOW)
        record = self.ledger["records"][0]
        self.ghost.pages.append({"slug": autopilot._slug(record)})
        snapshot, result = self.publish()
        self.assertEqual((result["published"], result["failed"]), (1, 1))
        self.assertEqual(self.ghost.writes, 1)
        self.assertEqual(record["automation"]["slug_conflict"], autopilot._slug(record))
        self.assertNotIn(record["id"], {event["id"] for event in snapshot["events"]})
        # Once the page gives the slug back, the next pass posts the story.
        self.ghost.pages.clear()
        snapshot, result = self.publish()
        self.assertEqual((result["published"], result["failed"]), (1, 0))
        self.assertNotIn("slug_conflict", record["automation"])

    def test_a_renamed_slug_is_drafted_and_never_posted_twice(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        record = self.ledger["records"][0]
        expected = autopilot._slug(record)
        self.ghost.rename = expected
        snapshot, result = self.publish()
        self.assertEqual(result["failed"], 1)
        self.assertFalse(snapshot["events"])
        stray = self.ghost.posts[0]
        self.assertEqual((stray["slug"], stray["status"]), (expected + "-2", "draft"))
        writes = self.ghost.writes
        # The next pass finds the post by its tag and repairs the slug instead of posting again.
        snapshot, result = self.publish()
        self.assertEqual((result["published"], result["failed"]), (1, 0))
        self.assertEqual(len(self.ghost.posts), 1)
        self.assertEqual(self.ghost.writes, writes + 1)
        self.assertEqual(
            (self.ghost.posts[0]["slug"], self.ghost.posts[0]["status"]), (expected, "published")
        )
        self.assertEqual(len(snapshot["events"]), 1)

    def test_a_live_post_under_another_slug_is_drafted_while_its_slug_is_taken(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        record = self.ledger["records"][0]
        expected = autopilot._slug(record)
        self.ghost.rename = expected
        self.ghost.lose_response = True
        self.publish()
        # A lost reply left the suffixed post live; another post now holds the slug.
        self.ghost.posts[0]["status"] = "published"
        self.ghost.pages.append({"slug": expected})
        for _ in range(2):
            snapshot, result = self.publish()
            self.assertEqual(result["failed"], 1)
            self.assertFalse(snapshot["events"])
        self.assertEqual(len(self.ghost.posts), 1)
        self.assertEqual(self.ghost.posts[0]["status"], "draft")
        self.assertTrue(all(request.get("slug") != expected for request in self.ghost.requests[1:]))

    def test_owner_edits_to_the_prose_block_and_fields_stay_and_are_recorded(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        post = self.post(record)
        lexical = json.loads(post["lexical"])
        prose = next(
            block for kind, block in autopilot.PublishedBlocks(post["html"]).blocks if kind == "story"
        )
        edited = (
            '<section class="story-prose" data-story-version="'
            + "0" * 64
            + '">\n<p>The owner rewrote this story.</p>\n</section>'
        )
        lexical["root"]["children"][0]["html"] = lexical["root"]["children"][0]["html"].replace(prose, edited)
        post.update(lexical=json.dumps(lexical), meta_description="An owner's meta description.")
        post["html"] = lexical["root"]["children"][0]["html"]
        corrected = candidate(1)
        corrected["source"]["evidence"]["aircraft"]["t"] = "C182"
        autopilot.stage(self.ledger, [corrected], at=NOW)
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        record = self.ledger["records"][0]
        post = self.post(record)
        self.assertIn("The owner rewrote this story.", post["html"])
        self.assertEqual(post["meta_description"], "An owner's meta description.")
        self.assertEqual(post["title"], snapshot["events"][0]["title"])
        self.assertIn("C182", post["html"])
        self.assertEqual(record["automation"]["owner_edits"], ["meta_description", "story"])
        writes = self.ghost.writes
        self.publish()
        self.assertEqual(self.ghost.writes, writes)

    def test_a_changed_card_renderer_never_looks_like_an_owner_edit(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        original_card = publication._event_card

        def renamed(event, **options):
            return original_card(event, **options).replace("<h3>Measurements</h3>", "<h3>Readings</h3>")

        corrected = candidate(1)
        corrected["source"]["evidence"]["aircraft"]["t"] = "C182"
        original_editorial = autopilot.editorial

        def retitled(packet, key, **options):
            metadata = original_editorial(packet, key, **options)
            story = {**metadata["story"], "headline": metadata["story"]["headline"] + " again"}
            return {**metadata, "title": story["headline"], "story": story}

        with patch.object(publication, "_event_card", side_effect=renamed):
            with patch.object(autopilot, "editorial", side_effect=retitled):
                autopilot.stage(self.ledger, [corrected], at=NOW)
            snapshot, _ = self.publish()
        record = self.ledger["records"][0]
        post = self.post(record)
        self.assertEqual(post["title"], snapshot["events"][0]["title"])
        self.assertTrue(post["title"].endswith(" again"))
        self.assertNotIn("owner_edits", record["automation"])

    def test_entity_links_name_each_watchlist_entry_as_story_facts_read_it(self):
        watch = story_fixtures.kind_event("watchlist_match")
        entry = watch["evidence"]["watchlist"]
        entry.update(
            association="  Example   Aviation Holdings ",
            reference_url="https://en.wikipedia.org/wiki/Example",
        )
        withheld = deepcopy(watch)
        withheld["evidence"]["watchlist"]["public_claim_allowed"] = False
        unlinked = deepcopy(watch)
        unlinked["evidence"]["watchlist"]["reference_url"] = None
        # The public block drops a link that is no HTTPS page, so story facts never look it up.
        plain = deepcopy(watch)
        plain["evidence"]["watchlist"]["reference_url"] = "http://en.wikipedia.org/wiki/Example"
        candidates = [{"source": source} for source in (watch, withheld, unlinked, plain)] + [candidate(1)]
        self.assertEqual(
            autopilot.entity_links(candidates),
            [
                (
                    "https://en.wikipedia.org/wiki/Example",
                    "Example Aviation Holdings",
                    "Distinctive",
                    "Distinctive aircraft",
                )
            ],
        )

    def test_entity_links_add_existing_watchlist_records_after_the_candidates(self):
        def watch(name, link):
            source = story_fixtures.kind_event("watchlist_match")
            source["evidence"]["watchlist"].update(association=name, reference_url=link)
            return source

        fields = ("Distinctive", "Distinctive aircraft")
        new = watch("Acme Air", "https://en.wikipedia.org/wiki/Acme_Air")
        records = [
            {"state": "published", "evidence": autopilot.evidence(watch(name, link), None)}
            for name, link in (
                ("Acme Rail", "https://en.wikipedia.org/wiki/Acme_Rail"),
                # The pass's own candidate comes again from its record; resolve() looks it up once.
                ("Acme Air", "https://en.wikipedia.org/wiki/Acme_Air"),
                ("Acme Sea", "https://en.wikipedia.org/wiki/Acme_Sea"),
            )
        ]
        records.append(
            {
                "state": "withdrawn",
                "evidence": autopilot.evidence(watch("Gone", "https://en.wikipedia.org/wiki/Gone"), None),
            }
        )
        records.append({"state": "published", "evidence": autopilot.evidence(candidate(1)["source"], None)})
        records.append({"state": "published", "evidence": None})
        found = autopilot.entity_links([{"source": new}, candidate(2)], {"records": records})
        self.assertEqual(
            found,
            [
                ("https://en.wikipedia.org/wiki/Acme_Air", "Acme Air", *fields),
                ("https://en.wikipedia.org/wiki/Acme_Rail", "Acme Rail", *fields),
                ("https://en.wikipedia.org/wiki/Acme_Air", "Acme Air", *fields),
                ("https://en.wikipedia.org/wiki/Acme_Sea", "Acme Sea", *fields),
            ],
        )
        calls = []
        with TemporaryDirectory() as state:
            answered = entity_context.resolve(
                state,
                found,
                now=1,
                lookup=lambda url, association, **_: (
                    calls.append(association) or {"state": "unavailable", "reason": "label"}
                ),
            )
        self.assertEqual((answered, calls), (3, ["Acme Air", "Acme Rail", "Acme Sea"]))
        # The pass's limit covers both: twelve due links, the first ten looked up, candidates first.
        links = [
            ("https://en.wikipedia.org/wiki/New_%d" % index, f"New {index}", *fields) for index in range(3)
        ]
        links += [
            ("https://en.wikipedia.org/wiki/Old_%d" % index, f"Old {index}", *fields) for index in range(9)
        ]
        calls = []
        with TemporaryDirectory() as state:
            entity_context.resolve(
                state,
                links,
                now=1,
                lookup=lambda url, association, **_: (
                    calls.append(url) or {"state": "unavailable", "reason": "label"}
                ),
            )
        self.assertEqual(calls, [link[0] for link in links[:10]])

    def test_stage_and_bootstrap_share_editorial_inputs(self):
        calls = []
        original = autopilot.editorial_inputs

        def inputs(ledger, packet, key, *, points=None, inputs=autopilot.NO_INPUTS, facts=None):
            calls.append((key, points, *inputs))
            return original(ledger, packet, key, points=points, inputs=inputs, facts=facts)

        item = candidate(1)
        item["source"]["evidence"]["track"] = [
            {"ts": 1, "lat": -40.5, "lon": 172.5},
            {"ts": 2, "lat": 91, "lon": 0},
        ]
        used = {}
        with patch.object(autopilot, "editorial_inputs", side_effect=inputs):
            autopilot.stage(
                self.ledger,
                [item],
                at=NOW,
                inputs=autopilot.StoryInputs(
                    {"C172": {"facts": [], "retrieved_at": NOW}},
                    {"https://en.wikipedia.org/wiki/Example": {"state": "unavailable"}},
                    used,
                ),
            )
        self.assertEqual(
            calls,
            [
                (
                    "1",
                    [{"lat": -40.5, "lon": 172.5}],
                    {"C172": {"facts": [], "retrieved_at": NOW}},
                    {"https://en.wikipedia.org/wiki/Example": {"state": "unavailable"}},
                    used,
                )
            ],
        )
        self.assertIs(calls[0][-1], used)

    def test_refresh_and_reprojection_pass_the_entities_on(self):
        inputs = autopilot.StoryInputs(
            None, {"https://en.wikipedia.org/wiki/Example": {"state": "unavailable"}}
        )
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        seen = []
        story_facts, reprojection = autopilot._story_facts, autopilot._reprojection

        def facts(packet, points, given=autopilot.NO_INPUTS):
            seen.append(("refresh", given))
            return story_facts(packet, points, given)

        def reprojected(ledger, record, source, given=autopilot.NO_INPUTS):
            seen.append(("reproject", given))
            return reprojection(ledger, record, source, given)

        with (
            patch.object(autopilot, "_story_facts", side_effect=facts),
            patch.object(autopilot, "_reprojection", side_effect=reprojected),
        ):
            autopilot.refresh_prose(self.ledger, NOW, inputs=inputs)
            autopilot.reproject(self.ledger, NOW, inputs=inputs)
        self.assertEqual(seen, [("refresh", inputs), ("reproject", inputs)])

    def test_prior_stories_mark_withdrawals_and_routine_types(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        record["automation"]["routine_type"] = True
        entry = autopilot.history(self.ledger)[0]
        self.assertEqual((entry["withdrawn"], entry["routine_type"]), (False, True))
        public.set_review_state(self.ledger, record["id"], "withdrawn", at=NOW)
        self.assertTrue(autopilot.history(self.ledger)[0]["withdrawn"])

    def test_exception_grade_evidence_the_selection_treats_as_ordinary_carries_no_proof(self):
        demoted = candidate(1, "rapid_descent", -9000)
        demoted["exceptional"] = False
        promoted = candidate(2, "rapid_descent", -9000)
        promoted["exceptional"] = True
        autopilot.stage(self.ledger, [demoted, promoted], at=NOW)
        events = {
            record["episode_key"]: public._projection(record["evidence"], record["editorial"])
            for record in self.ledger["records"]
        }
        self.assertNotIn("priority_evidence", events["1"])
        self.assertIn("priority_evidence", events["2"])
        self.assertEqual(self.ledger["records"][0]["editorial"]["priority"], "ordinary")
        # The flag survives a later pass without a selection decision, and weekly checks agree.
        again = candidate(1, "rapid_descent", -9000)
        self.assertEqual(autopilot.stage(self.ledger, [again], at=NOW), {"changed": 0, "rejected": 0})
        autopilot.check_source(again["source"], events["1"])
        snapshot, _ = self.publish()
        self.assertEqual(sorted("priority_evidence" in event for event in snapshot["events"]), [False, True])

    def test_robust_archive_shapes_publish_with_the_freshness_note(self):
        note = episode_evidence.CONTEXT_NOTES["archive_field_freshness_unknown"]
        for kind in ("stratospheric_balloon", "formation_flight"):
            with self.subTest(kind=kind):
                self.ledger, self.ghost = public.new_ledger("2026-10-01"), Ghost()
                item = candidate(1, kind, 3)
                item["source"]["evidence"]["maneuver"].update(
                    context_tags=["archive_field_freshness_unknown"],
                    quality_flags=[],
                    quality_review_required=False,
                )
                # A saved archive position, so the older freshness sentence would also apply.
                item["source"]["evidence"]["aircraft"]["archive_alt"] = 64500
                self.assertTrue(autopilot.eligible(item["source"]))
                packet = autopilot.evidence(item["source"], item["interval"])
                self.assertIn(note, packet["maneuver"]["context_notes"])
                event = public._projection(packet, public.PLACEHOLDER_EDITORIAL)
                self.assertIn(note, event["limitations"])
                self.assertNotIn(public.ARCHIVE_FRESHNESS, event["limitations"])
                autopilot.stage(self.ledger, [item], at=NOW)
                snapshot, result = self.publish()
                self.assertEqual(result["published"], 1)
                self.assertIn(note, snapshot["events"][0]["limitations"])
                self.assertIn(escape(note), self.ghost.posts[0]["html"])

    def test_a_new_prose_policy_re_renders_templates_but_keeps_the_url_and_title_tag(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        before = deepcopy(record["editorial"])
        engine = sys.modules["wtf_adsb_publisher.editorial"]
        with patch.object(engine, "PROSE_POLICY", engine.PROSE_POLICY + 1):
            self.assertEqual(autopilot.refresh_prose(self.ledger, NOW), 1)
            self.assertEqual(autopilot.refresh_prose(self.ledger, NOW), 0)
        record = self.ledger["records"][0]
        story = record["editorial"]["story"]
        self.assertEqual(story["version"], before["story"]["version"] + 1)
        self.assertEqual(story["seo_title"], before["story"]["seo_title"])
        self.assertEqual(record["editorial"]["article_path"], before["article_path"])
        self.assertEqual(record["editorial"]["ghost_id"], before["ghost_id"])
        self.assertEqual(record["state"], "pending")

    def test_a_live_exception_demoted_with_room_under_the_cap_is_updated_and_the_pass_goes_on(self):
        autopilot.stage(self.ledger, [candidate(i) for i in range(1, 5)], at=NOW)
        self.publish()
        autopilot.stage(self.ledger, [candidate(11, "rapid_descent", -9000)], at=NOW)
        initial, _ = self.publish()
        exceptional = next(event for event in initial["events"] if event.get("priority_evidence"))
        autopilot.stage(self.ledger, [candidate(11, "rapid_descent", -5000), candidate(5)], at=NOW)
        snapshot, result = self.publish()
        self.assertEqual((result["failed"], result["published"]), (0, 2))
        self.assertEqual(len(snapshot["events"]), 6)
        event = next(event for event in snapshot["events"] if event["id"] == exceptional["id"])
        self.assertNotIn("priority_evidence", event)
        record = next(row for row in self.ledger["records"] if row["id"] == exceptional["id"])
        self.assertFalse(record["automation"]["published_exceptional"])
        writes = self.ghost.writes
        self.publish()
        self.assertEqual(self.ghost.writes, writes)

    def test_unchanged_evidence_is_never_rendered_and_a_failed_render_never_retracts_a_story(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        broken = patch.object(autopilot, "editorial", side_effect=ValueError("engine failure"))
        with broken as editorial:
            self.assertEqual(
                autopilot.stage(self.ledger, [candidate(1)], at=NOW), {"changed": 0, "rejected": 0}
            )
            self.assertFalse(editorial.called)
            corrected = candidate(1)
            corrected["source"]["evidence"]["aircraft"]["t"] = "C182"
            self.assertEqual(
                autopilot.stage(self.ledger, [corrected], at=NOW),
                {
                    "changed": 0,
                    "rejected": 1,
                    "editorial_failures": [{"key": "1", "kind": "vintage", "error": "ValueError"}],
                },
            )
        record = self.ledger["records"][0]
        self.assertNotIn("blocked", record["automation"])
        self.assertEqual(record["state"], "approved")
        snapshot, result = self.publish()
        self.assertEqual((len(snapshot["events"]), result["failed"]), (1, 0))
        self.assertEqual(self.post(record)["status"], "published")
        # The next pass that can write the story revises the record.
        self.assertEqual(autopilot.stage(self.ledger, [corrected], at=NOW), {"changed": 1, "rejected": 0})

    def test_a_thin_record_publishes_the_engines_plain_record(self):
        # No reading, track or type history: no angle fills two paragraphs on its own.
        item = candidate(8, "watch_type", None)
        item["source"]["evidence"]["aircraft"]["t"] = "DC3"
        packet = autopilot.evidence(item["source"], item["interval"])
        metadata = autopilot.editorial(packet, item["key"])
        self.assertEqual(metadata["story"]["angle"], "minimal")
        self.assertEqual(autopilot.stage(self.ledger, [item], at=NOW), {"changed": 1, "rejected": 0})
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertEqual(snapshot["events"][0]["story"]["angle"], "minimal")

    def test_a_record_without_any_valid_story_waits_and_the_pass_goes_on(self):
        engine = sys.modules["wtf_adsb_publisher.editorial"]
        original = engine.render_with_report

        def render(facts, key, **options):
            story, report = original(facts, key, **options)
            if key == "1":
                report = engine.Report(fallback=True, publishable=False, issues=["paragraphs:count"])
            return story, report

        items = [candidate(1), candidate(2)]
        with patch.object(engine, "render_with_report", side_effect=render):
            packet = autopilot.evidence(items[0]["source"], items[0]["interval"])
            with self.assertRaisesRegex(engine.StoryUnavailable, "paragraphs:count"):
                autopilot.editorial(packet, "1")
            # The failure and its reason are listed, never silently counted with rejected evidence.
            self.assertEqual(
                autopilot.stage(self.ledger, items, at=NOW),
                {
                    "changed": 1,
                    "rejected": 1,
                    "editorial_failures": [{"key": "1", "kind": "vintage", "error": "StoryUnavailable"}],
                },
            )
        self.assertEqual([record["episode_key"] for record in self.ledger["records"]], ["2"])
        _, result = self.publish()
        self.assertEqual((result["published"], result["failed"]), (1, 0))
        # The next pass that can write the story stages it.
        self.assertEqual(autopilot.stage(self.ledger, items, at=NOW), {"changed": 1, "rejected": 0})

    def test_a_story_released_as_ordinary_carries_no_proof_and_stays_ordinary(self):
        item = candidate(1, "rapid_descent", -9000)
        item["exceptional"] = True
        autopilot.stage(self.ledger, [item], at=NOW)
        record = self.ledger["records"][0]
        self.assertIn("priority_evidence", public._projection(record["evidence"], record["editorial"]))
        # A decision the publisher reads at release time, as in a plan made before
        # the bootstrap passed the curation's decision to the editorial.
        record["automation"]["exceptional"] = False
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        record = self.ledger["records"][0]
        self.assertNotIn("priority_evidence", snapshot["events"][0])
        self.assertEqual(record["editorial"]["priority"], "ordinary")
        self.assertIs(record["automation"]["published_exceptional"], False)
        self.assertNotIn("priority_evidence", self.post(record)["html"])
        self.assertNotIn("Exceptional", self.post(record)["html"])
        # Later passes keep it ordinary: no new revision and no Ghost write.
        revision, writes = record["revision"], self.ghost.writes
        self.assertEqual(autopilot.stage(self.ledger, [item], at=NOW), {"changed": 0, "rejected": 0})
        _, result = self.publish()
        self.assertEqual((result["unchanged"], self.ghost.writes), (1, writes))
        self.assertEqual(self.ledger["records"][0]["revision"], revision)

    def test_a_demotion_holds_while_its_ghost_write_fails_and_never_blocks_a_dossier(self):
        item = candidate(1, "rapid_descent", -9000)
        item["exceptional"] = True
        autopilot.stage(self.ledger, [item], at=NOW)
        self.ledger["records"][0]["automation"]["exceptional"] = False
        refused = GhostError("Ghost request failed (HTTP 422)", 422)
        with patch.object(self.ghost, "save", side_effect=refused):
            _, result = self.publish()
        self.assertEqual((result["published"], result["failed"]), (0, 1))
        record = self.ledger["records"][0]
        self.assertEqual(record["editorial"]["priority"], "ordinary")
        self.assertTrue(record["automation"]["demoted_at_release"])
        # The selection's exceptional verdict does not restore the proof meanwhile.
        revision = record["revision"]
        self.assertEqual(autopilot.stage(self.ledger, [item], at=NOW), {"changed": 0, "rejected": 0})
        self.assertEqual(self.ledger["records"][0]["revision"], revision)
        snapshot, result = self.publish()
        self.assertEqual(result["published"], 1)
        self.assertNotIn("priority_evidence", snapshot["events"][0])
        # A revision the rules refuse, such as a weekly dossier bound to the proof,
        # leaves the story as it is instead of failing every pass.
        self.ledger, self.ghost = public.new_ledger("2026-10-01"), Ghost()
        autopilot.stage(self.ledger, [item], at=NOW)
        self.ledger["records"][0]["automation"]["exceptional"] = False
        with patch.object(public, "put_event", side_effect=ValueError("Research is bound to other evidence")):
            snapshot, result = self.publish()
        self.assertEqual((result["published"], result["failed"]), (1, 0))
        self.assertIn("priority_evidence", snapshot["events"][0])
        self.assertNotIn("demoted_at_release", self.ledger["records"][0]["automation"])

    def test_the_content_revision_time_moves_only_for_new_prose_or_evidence(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        snapshot, _ = self.publish()
        record = self.ledger["records"][0]
        automatic = record["automation"]
        self.assertEqual(automatic["content_revised_at"], automatic["released_at"])
        self.assertEqual(snapshot["events"][0]["content_revised_at"], NOW)
        later, latest = "2026-10-01T20:00:00Z", "2026-10-02T21:00:00Z"
        # An unchanged pass, and a Ghost write that changes no prose or evidence
        # (here the lost topic tag is restored), leave it alone.
        self.publish(at=later)
        post = self.post(record)
        post["tags"] = [tag for tag in post["tags"] if tag["name"].startswith("#autopilot-")]
        writes = self.ghost.writes
        self.publish(at=later)
        self.assertGreater(self.ghost.writes, writes)
        self.assertEqual(self.ledger["records"][0]["automation"]["content_revised_at"], NOW)
        # New prose is a revision readers see.
        record = self.ledger["records"][0]
        story = {**record["editorial"]["story"], "paragraphs": [*record["editorial"]["story"]["paragraphs"]]}
        story["paragraphs"][-1] += " Each value in the evidence card below keeps the time of its own report."
        metadata = {**record["editorial"], "story": story}
        public.put_event(self.ledger, record["episode_key"], record["evidence"], metadata, at=later)
        snapshot, result = self.publish(at=latest)
        self.assertEqual(result["published"], 1)
        record = self.ledger["records"][0]
        self.assertEqual(record["automation"]["content_revised_at"], latest)
        self.assertEqual(record["automation"]["released_at"], NOW)
        self.assertEqual(
            (snapshot["events"][0]["released_at"], snapshot["events"][0]["content_revised_at"]), (NOW, latest)
        )

    def test_a_corrected_build_year_outside_the_card_revises_the_story(self):
        item = candidate(1)
        item["source"]["evidence"]["aircraft"]["year"] = 1960
        autopilot.stage(self.ledger, [item], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        story = record["editorial"]["story"]
        self.assertEqual((story["facts"]["age.built"], story["facts"]["age.years"]), (1960, 66))
        revision, path, ghost_id = (
            record["revision"],
            record["editorial"]["article_path"],
            record["editorial"]["ghost_id"],
        )
        # The card does not show the build year, so its evidence version is the same.
        corrected = candidate(1)
        corrected["source"]["evidence"]["aircraft"]["year"] = 1956
        self.assertEqual(autopilot.stage(self.ledger, [corrected], at=NOW), {"changed": 1, "rejected": 0})
        record = self.ledger["records"][0]
        self.assertEqual(record["evidence"]["metadata"]["database_year"], 1956)
        self.assertEqual(record["automation"]["source"]["evidence"]["aircraft"]["year"], 1956)
        story = record["editorial"]["story"]
        self.assertEqual((story["facts"]["age.built"], story["facts"]["age.years"]), (1956, 70))
        self.assertIn("1956", " ".join(story["paragraphs"]))
        self.assertNotIn("1960", " ".join(story["paragraphs"]))
        self.assertEqual(record["revision"], revision + 1)
        self.assertEqual(
            (record["editorial"]["article_path"], record["editorial"]["ghost_id"]), (path, ghost_id)
        )
        # The prose was rewritten with the record, so the refresh has nothing left to do.
        self.assertEqual(autopilot.refresh_prose(self.ledger, NOW), 0)
        snapshot, result = self.publish()
        self.assertEqual((result["published"], len(self.ghost.posts)), (1, 1))
        self.assertIn("1956", self.ghost.posts[0]["html"])
        self.assertEqual(autopilot.stage(self.ledger, [corrected], at=NOW), {"changed": 0, "rejected": 0})

    def test_changed_season_comparisons_and_replay_tracks_revise_the_record(self):
        def seasonal(previous):
            item = candidate(2, "seasonal_speed_record", value=350)
            item["source"]["unit"] = "kn"
            item["source"]["evidence"]["aircraft"]["gs"] = 350
            item["source"]["evidence"]["season_record"] = {
                "previous_max_gs": previous,
                "baseline_start": "2026-07-01",
                "season_start": "2026-09-01",
                "baseline_days": 40,
            }
            return item

        def descent(spacing):
            item = candidate(3, "rapid_descent", value=-8000)
            start = item["source"]["first_seen"]
            item["source"]["evidence"]["maneuver_samples"] = [
                {
                    "ts": start + i * 10,
                    "lat": 42.5 + i * spacing,
                    "lon": -83.3,
                    "alt_baro": 10000 - i * 1000,
                    "gs": 200,
                    "source": "adsb_icao",
                }
                for i in range(5)
            ]
            return item

        with patch.object(autopilot, "_place", return_value=None):
            autopilot.stage(self.ledger, [seasonal(300), descent(0.01)], at=NOW)
            self.publish()
            season, track = self.ledger["records"]
            self.assertEqual(season["editorial"]["story"]["facts"]["season.previous"], 300)
            self.assertEqual(track["evidence"]["replay"]["points"][1]["latitude"], 42.51)
            revisions = [record["revision"] for record in self.ledger["records"]]
            # Neither block is on the card; both are inputs to the story.
            self.assertEqual(
                autopilot.stage(self.ledger, [seasonal(320), descent(0.02)], at=NOW),
                {"changed": 2, "rejected": 0},
            )
            season, track = self.ledger["records"]
            self.assertEqual(season["evidence"]["season_record"]["previous_max_gs"], 320)
            self.assertEqual(season["editorial"]["story"]["facts"]["season.previous"], 320)
            self.assertEqual(track["evidence"]["replay"]["points"][1]["latitude"], 42.52)
            self.assertEqual(
                [record["revision"] for record in self.ledger["records"]], [r + 1 for r in revisions]
            )
            self.assertEqual(
                autopilot.stage(self.ledger, [seasonal(320), descent(0.02)], at=NOW),
                {"changed": 0, "rejected": 0},
            )
            # A frozen record's reprojection sees the same change.
            for record in self.ledger["records"]:
                record["automation"]["source_kind"] = "frozen"
            self.ledger["records"][0]["automation"]["source"] = deepcopy(seasonal(330)["source"])
            report = autopilot.reproject(self.ledger, NOW)
            self.assertEqual((report["reprojected"], report["unchanged"], report["unresolved"]), (1, 1, []))
            self.assertEqual(self.ledger["records"][0]["evidence"]["season_record"]["previous_max_gs"], 330)

    def test_a_type_norm_is_fixed_on_the_record_at_first_staging(self):
        def seasonal(low):
            item = candidate(2, "seasonal_speed_record", value=350)
            item["source"]["unit"] = "kn"
            item["source"]["evidence"]["aircraft"]["gs"] = 350
            item["source"]["evidence"]["season_record"] = {
                "previous_max_gs": 300,
                "baseline_start": "2026-07-01",
                "season_start": "2026-09-01",
                "baseline_days": 40,
            }
            item["type_norm"] = {**story_fixtures.NORM, "low": low, "high": low + 40}
            return item

        first = seasonal(100)["type_norm"]
        with patch.object(autopilot, "_place", return_value=None):
            self.assertEqual(
                autopilot.stage(self.ledger, [seasonal(100)], at=NOW), {"changed": 1, "rejected": 0}
            )
            record = self.ledger["records"][0]
            self.assertEqual(record["evidence"]["type_norm"], first)
            self.assertEqual(record["automation"]["type_norm"], first)
            self.assertEqual(record["editorial"]["story"]["facts"]["norm.gs_low"], 100)
            revision = record["revision"]
            # A drifting baseline never revises the record.
            self.assertEqual(
                autopilot.stage(self.ledger, [seasonal(110)], at=NOW), {"changed": 0, "rejected": 0}
            )
            self.assertEqual(record["evidence"]["type_norm"], first)
            self.assertEqual(record["automation"]["type_norm"], first)
            self.assertEqual(record["revision"], revision)
            # Nor does a frozen record's reprojection, which regenerates its packet from the finding.
            record["automation"]["source_kind"] = "frozen"
            report = autopilot.reproject(self.ledger, NOW)
            self.assertEqual((report["reprojected"], report["unchanged"], report["unresolved"]), (0, 1, []))
            self.assertEqual(record["evidence"]["type_norm"], first)
            self.assertEqual(record["revision"], revision)

    def test_a_saved_norm_rides_only_on_a_packet_of_a_norm_kind(self):
        block = story_fixtures.NORM
        record = {"automation": {"type_norm": block}}
        packet = {"kind": "seasonal_speed_record"}
        self.assertEqual(autopilot._attach_fixed(packet, record, {"type_norm": None}), block)
        self.assertEqual(packet["type_norm"], block)
        # The episode's finding is now of a kind without a norm: the saved block is dropped.
        packet = {"kind": "fast", "type_norm": block}
        self.assertIsNone(autopilot._attach_fixed(packet, record, {"type_norm": block}))
        self.assertNotIn("type_norm", packet)
        self.assertNotIn("type_norm", record["automation"])
        # Back to a norm kind, it is a record with no saved value and takes the candidate's once.
        packet = {"kind": "type_speed_mismatch"}
        self.assertEqual(autopilot._attach_fixed(packet, record, {"type_norm": block}), block)
        self.assertIsNone(autopilot._attach_fixed({"kind": "fast"}, None, {"type_norm": block}))

    def test_a_record_first_staged_without_a_norm_never_takes_one_later(self):
        item = candidate(2, "seasonal_speed_record", value=350)
        item["source"]["unit"] = "kn"
        item["source"]["evidence"]["aircraft"]["gs"] = 350
        item["source"]["evidence"]["season_record"] = {
            "previous_max_gs": 300,
            "baseline_start": "2026-07-01",
            "season_start": "2026-09-01",
            "baseline_days": 40,
        }
        item["type_norm"] = None
        with patch.object(autopilot, "_place", return_value=None):
            self.assertEqual(autopilot.stage(self.ledger, [item], at=NOW), {"changed": 1, "rejected": 0})
            record = self.ledger["records"][0]
            self.assertIsNone(record["automation"]["type_norm"])
            revision = record["revision"]
            # The context later grows enough for a norm; the record keeps none.
            later = deepcopy(item)
            later["type_norm"] = {**story_fixtures.NORM, "low": 300, "high": 340}
            self.assertEqual(autopilot.stage(self.ledger, [later], at=NOW), {"changed": 0, "rejected": 0})
            self.assertNotIn("type_norm", record["evidence"])
            self.assertEqual(record["revision"], revision)

    def test_corrected_evidence_the_rules_refuse_still_blocks_the_record(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        corrected = candidate(1)
        corrected["source"]["evidence"]["aircraft"]["desc"] = "localhost &lt;b&gt;x&lt;/b&gt;"
        self.assertEqual(autopilot.stage(self.ledger, [corrected], at=NOW), {"changed": 0, "rejected": 1})
        record = self.ledger["records"][0]
        self.assertEqual(record["automation"]["blocked"], "invalid_evidence")
        self.publish()
        self.assertEqual(self.post(record)["status"], "draft")

    def test_a_renamed_topic_tag_stays_one_tag_and_causes_no_rewrites(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        post = self.post(record)
        topic = post["tags"][0]
        self.assertEqual(topic["slug"], record["editorial"]["context"]["topic"])
        topic["name"] = "Renamed by the owner"
        post["tags"].append({"name": "Owner tag", "slug": "owner-tag"})
        writes = self.ghost.writes
        self.publish()
        self.assertEqual(self.ghost.writes, writes)
        with patch.dict(publication._topic_titles(), {topic["slug"]: "A new topic title"}):
            self.publish()
            self.assertEqual(self.ghost.writes, writes)
            corrected = candidate(1)
            corrected["source"]["evidence"]["aircraft"]["t"] = "C182"
            autopilot.stage(self.ledger, [corrected], at=NOW)
            self.publish()
        tags = self.ghost.requests[-1]["tags"]
        self.assertEqual(tags[0]["slug"], topic["slug"])
        slugs = [tag["slug"] for tag in tags if tag.get("slug")]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertIn("owner-tag", slugs)

    def test_a_post_ghost_refuses_is_recorded_and_the_pass_goes_on(self):
        autopilot.stage(self.ledger, [candidate(1), candidate(2)], at=NOW)
        save, refused = self.ghost.save, []

        def refuse_first(post, current=None):
            if not refused:
                refused.append(post["slug"])
                raise GhostError("Ghost request failed (HTTP 422)", 422)
            return save(post, current)

        with patch.object(self.ghost, "save", side_effect=refuse_first):
            snapshot, result = self.publish()
        self.assertEqual((result["failed"], result["published"]), (1, 1))
        record = next(row for row in self.ledger["records"] if autopilot._slug(row) == refused[0])
        self.assertEqual(record["automation"]["ghost_refused"], {"status": 422, "at": NOW})
        snapshot, result = self.publish()
        self.assertEqual((result["failed"], result["published"]), (0, 1))
        self.assertNotIn("ghost_refused", record["automation"])
        # Authentication failures, conflicts and rate limits still stop the pass.
        for status in (401, 409, 429):
            self.assertNotIn(status, autopilot.GHOST_REFUSED)
        self.assertEqual(autopilot._ghost_status(GhostError("Ghost request failed (HTTP 404)", 404)), 404)
        # Only the status Ghost answered with counts, never the wording of a message.
        self.assertIsNone(autopilot._ghost_status(RuntimeError("Ghost request failed (HTTP 404)")))

    def test_a_confirmed_live_post_is_no_longer_suspended_so_a_later_unpublication_is_respected(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        # A suspension the owner overrode by publishing the post again, with nothing to write.
        record["automation"]["suspended"] = True
        writes = self.ghost.writes
        self.publish()
        self.assertEqual(self.ghost.writes, writes)
        self.assertNotIn("suspended", record["automation"])
        self.post(record)["status"] = "draft"
        snapshot, _ = self.publish()
        self.assertFalse(snapshot["events"])
        self.assertTrue(record["automation"]["removed"])
        self.assertEqual(self.ghost.writes, writes)

    def test_owned_blocks_round_trip_through_ghost_absolute_links_and_rendered_html(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        post = self.post(record)
        # Ghost returns its own links absolute; owned blocks never hold a root-relative link.
        self.assertNotRegex(publication.editor_html(post), r'(?:href|src)="/(?!/)')
        # Ghost renders cards with markers around them; edits are read from the editor document.
        post["html"] = "<!--kg-card-begin: html-->" + post["html"] + "<!--kg-card-end: html-->"
        writes = self.ghost.writes
        self.publish()
        self.assertEqual(self.ghost.writes, writes)
        self.assertNotIn("owner_edits", record["automation"])

    def revise_story(self, **changes):
        """The record's current story with `changes`, saved as a released revision."""
        record = self.ledger["records"][0]
        story = {**record["editorial"]["story"], **changes}
        metadata = {
            **record["editorial"],
            "title": story["headline"],
            "summary": story["dek"],
            "story": story,
        }
        public.put_event(self.ledger, record["episode_key"], record["evidence"], metadata, at=NOW)
        self.publish()
        return story

    def test_a_non_text_evidence_change_keeps_ai_prose_and_the_title_tag(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        written = self.revise_story(source="ai")
        # A reading the story never mentions changes the evidence, not the prose.
        corrected = candidate(1)
        corrected["source"]["evidence"]["aircraft"]["gs"] = 112
        self.assertEqual(autopilot.stage(self.ledger, [corrected], at=NOW), {"changed": 1, "rejected": 0})
        record = self.ledger["records"][0]
        self.assertIn("gs", [item["field"] for item in record["evidence"]["received"]["readings"]])
        self.assertEqual(record["editorial"]["story"], written)
        # A template story is written again, under the title tag it already had.
        title = self.revise_story(source="template", seo_title="Cessna 172 N10001 on 1 October 2026")[
            "seo_title"
        ]
        corrected["source"]["evidence"]["aircraft"]["gs"] = 114
        autopilot.stage(self.ledger, [corrected], at=NOW)
        story = self.ledger["records"][0]["editorial"]["story"]
        self.assertEqual((story["source"], story["seo_title"]), ("template", title))

    def test_a_story_whose_facts_change_is_rewritten_with_a_title_tag_that_holds(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        written = self.revise_story(source="ai")
        corrected = candidate(1)
        corrected["source"]["evidence"]["aircraft"].update(t="C182", desc="CESSNA 182")
        autopilot.stage(self.ledger, [corrected], at=NOW)
        record = self.ledger["records"][0]
        story = record["editorial"]["story"]
        # The prose and title tag named the old type, so neither stays.
        self.assertEqual(story["source"], "template")
        self.assertNotEqual(story["seo_title"], written["seo_title"])
        self.assertNotIn("172", story["seo_title"])
        self.assertEqual(
            record["editorial"]["article_path"], record["history"][0]["editorial"]["article_path"]
        )

    def test_a_story_citing_a_changed_type_fact_is_rewritten_on_the_next_refresh(self):
        from wtf_adsb_publisher import weekly_research

        source = {"title": "Cessna 172 Skyhawk", "url": "https://cessna.txtav.com/en/piston/cessna-skyhawk"}

        def profiles(fact):
            profile = weekly_research.make_profile(
                "C172", "Cessna 172", [{"text": fact, "source": source}], NOW, "research"
            )
            return weekly_research.story_profiles({"C172": profile})

        old = "The Cessna 172 is a light single-engine aircraft with four seats and a high wing."
        autopilot.stage(
            self.ledger,
            [candidate(i) for i in range(1, 4)],
            at=NOW,
            inputs=autopilot.StoryInputs(profiles(old)),
        )
        self.publish()
        citing = [row for row in self.ledger["records"] if old in row["editorial"]["story"]["facts"].values()]
        self.assertTrue(citing)
        # Nothing a story states has changed.
        self.assertEqual(
            autopilot.refresh_prose(self.ledger, NOW, inputs=autopilot.StoryInputs(profiles(old))), 0
        )
        new = autopilot.StoryInputs(profiles("The Cessna 172 first flew in 1955 and is still built today."))
        self.assertEqual(autopilot.refresh_prose(self.ledger, NOW, inputs=new), len(citing))
        for row in citing:
            self.assertNotIn(old, row["editorial"]["story"]["facts"].values())
            self.assertEqual(row["state"], "pending")
        self.assertEqual(autopilot.refresh_prose(self.ledger, NOW, inputs=new), 0)

    def test_new_single_sample_requirement_refreshes_an_otherwise_current_template(self):
        import story_fixtures as fixtures

        packet = fixtures.packet(fixtures.kind_event("identity_conflict"))
        packet["received"]["label_provenance"] = "archived_label"
        packet["observation"].update(qualifying_count=1, end_utc=packet["observation"]["start_utc"])
        facts = autopilot.story_facts.build(packet)
        old_facts = deepcopy(facts)
        old_facts["facts"] = [fact for fact in facts["facts"] if fact["id"] != "idc.samples"]
        old_facts["digest"] = autopilot.story_facts.digest(old_facts)
        old = autopilot.editorial(packet, "single-sample", facts=old_facts)
        identifier = public.put_event(self.ledger, "single-sample", packet, old, at=NOW)
        required = autopilot.public_prose.required_sentence(facts)
        self.assertNotIn(required, " ".join(old["story"]["paragraphs"]))
        self.assertTrue(autopilot._cites_current(old["story"], facts))
        # Isolate prose migration from the separate source-journal verification.
        with (
            patch.object(autopilot, "record_valid", return_value=True),
            patch.object(autopilot, "record_points", return_value=[]),
            patch.object(autopilot, "_story_facts", return_value=facts),
        ):
            self.assertEqual(autopilot.refresh_prose(self.ledger, NOW), 1)
            updated = self.ledger["records"][0]
            self.assertEqual(updated["id"], identifier)
            self.assertEqual(updated["editorial"]["article_path"], old["article_path"])
            self.assertIn(required, " ".join(updated["editorial"]["story"]["paragraphs"]))
            self.assertEqual(autopilot.refresh_prose(self.ledger, NOW), 0)

    def test_an_airport_met_at_either_end_or_with_an_unusable_name_is_passed_over(self):
        # The answers are kept for the process; this test's are dropped after it.
        self.addCleanup(autopilot._airport_answer.cache_clear)
        autopilot._airport_answer.cache_clear()
        points = [{"lat": 42.5, "lon": -83.3 + index * 0.01} for index in range(5)]
        names = {"EPA": "Endpoint Airport", "EAG": "Eagles Landing Airport", "BTN": "Brighton Airport"}
        ranked = [(0.2, "EPA", 4), (0.5, "EAG", 2), (1.7, "BTN", 1), (2.0, "XYZ", 2)]

        def entry(found):
            distance, ident, index = found
            return {"name": names.get(ident, "Other Airport"), "ident": ident, "nm": distance, "at": index}

        with (
            patch.object(autopilot.airports, "ranked", return_value=ranked) as scan,
            patch.object(autopilot.airports, "entry", side_effect=entry),
        ):
            self.assertEqual(autopilot._airport(points, "circling")["ident"], "BTN")
            # One scan, and the same positions ask no second one.
            self.assertEqual(autopilot._airport(points, "circling")["ident"], "BTN")
        self.assertEqual(scan.call_count, 1)
        with patch.object(autopilot.airports, "ranked", side_effect=AssertionError):
            self.assertIsNone(autopilot._airport(points, "rapid_descent"))
            self.assertIsNone(autopilot._airport(None, "circling"))

    def test_stories_naming_a_place_keep_their_prose_while_the_place_holds(self):
        place = {"name": "Testville, Michigan", "short": "Testville, MI", "state": "Michigan"}
        with patch.object(autopilot, "_place", return_value=place):
            autopilot.stage(self.ledger, [candidate(1)], at=NOW)
            self.publish()
            written = self.revise_story(source="ai")
            self.assertEqual(written["facts"]["track.place.short"], "Testville, MI")
            # The derived short name and state are read from the place they come from.
            self.assertEqual(autopilot.refresh_prose(self.ledger, NOW), 0)
            corrected = candidate(1)
            corrected["source"]["evidence"]["aircraft"]["gs"] = 112
            autopilot.stage(self.ledger, [corrected], at=NOW)
            self.assertEqual(self.ledger["records"][0]["editorial"]["story"], written)
        moved = {**place, "name": "Otherville, Michigan", "short": "Otherville, MI"}
        with patch.object(autopilot, "_place", return_value=moved):
            self.assertEqual(autopilot.refresh_prose(self.ledger, NOW), 1)
        story = self.ledger["records"][0]["editorial"]["story"]
        self.assertEqual(
            (story["source"], story["facts"]["track.place.short"]), ("template", "Otherville, MI")
        )

    def test_reproject_moves_bindings_a_wording_change_left_behind(self):
        autopilot.stage(self.ledger, [candidate(1)], at=NOW)
        self.publish()
        record = self.ledger["records"][0]
        record["automation"]["source_kind"] = "frozen"
        old = public._projection(record["evidence"], record["editorial"])["evidence_version"]
        record["automation"]["media_evidence_version"] = old
        revision = record["revision"]
        # A later projection words the kind's limitation differently; the packet is unchanged.
        with patch.dict(public.KIND_LIMITATIONS, {"vintage": ("A reworded fixture limitation.",)}):
            new = public._projection(record["evidence"], record["editorial"])["evidence_version"]
            self.assertNotEqual(new, old)
            report = autopilot.reproject(self.ledger, NOW)
            self.assertEqual(
                {key: report[key] for key in ("unchanged", "reprojected", "media_rebound", "approved")},
                {"unchanged": 0, "reprojected": 0, "media_rebound": 1, "approved": 0},
            )
            # A map binding moves without a revision; nothing else changes.
            self.assertEqual(record["automation"]["media_evidence_version"], new)
            self.assertEqual(record["revision"], revision)
            self.assertEqual(autopilot.reproject(self.ledger, NOW)["unchanged"], 1)

    def test_reproject_moves_frozen_records_to_regenerated_evidence_without_new_urls(self):
        items = [candidate(1), candidate(2)]
        autopilot.stage(self.ledger, items, at=NOW)
        self.publish()
        for record in self.ledger["records"]:
            record["automation"]["source_kind"] = "frozen"
            record["automation"]["media_evidence_version"] = public._projection(
                record["evidence"], record["editorial"]
            )["evidence_version"]
        before = deepcopy(self.ledger)
        self.assertEqual(
            autopilot.reproject(self.ledger, NOW),
            {
                "checked": 2,
                "unchanged": 2,
                "reprojected": 0,
                "prose_rewritten": 0,
                "research_rebound": 0,
                "media_rebound": 0,
                "approved": 0,
                "unresolved": [],
            },
        )
        self.assertEqual(self.ledger, before)
        # A later evidence_packet() derives a limitation the saved packets lack: publish()
        # would draft both articles, and frozen records are never restaged.
        packet = autopilot.stories.evidence_packet

        def newer(finding, *args, **kwargs):
            result = packet(finding, *args, **kwargs)
            result["identity_checks"] = {"conflict": True}
            return result

        with patch.object(autopilot.stories, "evidence_packet", side_effect=newer):
            report = autopilot.reproject(self.ledger, "2026-10-01T19:00:00Z")
            self.assertEqual(
                {key: report[key] for key in ("checked", "reprojected", "media_rebound", "approved")},
                {"checked": 2, "reprojected": 2, "media_rebound": 2, "approved": 2},
            )
            self.assertEqual(report["unresolved"], [])
            for record, old in zip(self.ledger["records"], before["records"], strict=True):
                self.assertEqual(record["state"], "approved")
                self.assertEqual(record["editorial"]["article_path"], old["editorial"]["article_path"])
                self.assertEqual(record["editorial"]["ghost_id"], old["editorial"]["ghost_id"])
                self.assertEqual(record["revision"], old["revision"] + 1)
                event = public._projection(record["evidence"], record["editorial"])
                self.assertIn(public.IDENTITY_CONFLICT, event["limitations"])
                self.assertEqual(record["automation"]["media_evidence_version"], event["evidence_version"])
                autopilot.check_source(record["automation"]["source"], event)
            # The next pass updates the cards in place and drafts nothing.
            snapshot, result = self.publish(at="2026-10-01T19:00:00Z")
            self.assertEqual((result["published"], result["suspended"], result["failed"]), (2, 0, 0))
            self.assertEqual(len(snapshot["events"]), 2)
            self.assertEqual(autopilot.reproject(self.ledger, "2026-10-01T19:30:00Z")["unchanged"], 2)


if __name__ == "__main__":
    unittest.main()
