"""Retiring a ledger's posts drafts only owned posts, resumes after failures and restores."""

import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
import retire_ledger_posts as retire

from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher.ghost_client import GhostError

IDS = [f"{index:024x}" for index in range(1, 7)]
RECORDS = [f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 6)]


def post(identifier, slug, *, status="published", tags=(), updated_at="2026-10-05T12:00:00.000Z"):
    return {
        "id": identifier,
        "slug": slug,
        "status": status,
        "visibility": "public",
        "title": slug.replace("-", " "),
        "published_at": "2026-10-05T12:00:00.000Z",
        "updated_at": updated_at,
        "html": "",
        "tags": [{"name": name} for name in tags],
    }


class FakeGhost:
    """Ghost's update-token and status behavior, with injectable failures."""

    def __init__(self, posts):
        self.posts = {item["id"]: deepcopy(item) for item in posts}
        self.saves, self.clock = [], 0
        self.fail_before, self.fail_after, self.refuse = set(), set(), set()

    def inventory(self):
        return [deepcopy(item) for item in self.posts.values()]

    def save(self, change, current):
        self.saves.append((current["id"], deepcopy(change)))
        stored = self.posts[current["id"]]
        if current["updated_at"] != stored["updated_at"]:
            raise RuntimeError("Update collision")
        if current["id"] in self.fail_before:
            self.fail_before.discard(current["id"])
            raise OSError("Connection refused")
        if current["id"] in self.refuse:
            self.refuse.discard(current["id"])
            raise GhostError("Ghost request failed (HTTP 409)", 409)
        self.clock += 1
        stored.update(change, updated_at=stamp(seconds=self.clock / 1000))
        if current["id"] in self.fail_after:
            self.fail_after.discard(current["id"])
            raise OSError("Reply lost after Ghost committed the change")
        return deepcopy(stored)


def stamp(**offset):
    """Ghost's update-time format, relative to now."""
    moment = datetime.now(timezone.utc) + timedelta(**offset)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ledger(*rows):
    records = []
    for record_id, slug, ghost_id in rows:
        editorial = {"article_path": f"/{slug}/"}
        if ghost_id:
            editorial["ghost_id"] = ghost_id
        records.append({"id": record_id, "editorial": editorial, "history": []})
    return {"schema_version": 1, "available_from": "2026-06-01", "records": records}


class RetireTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.ledger = self.folder / "ledger.json"
        public.write_json_atomic(
            self.ledger,
            ledger(
                (RECORDS[0], "first-story", IDS[0]),
                (RECORDS[1], "second-story", IDS[1]),
                (RECORDS[2], "third-story", IDS[2]),
                (RECORDS[3], "draft-story", IDS[3]),
                (RECORDS[4], "unpinned-story", None),
            ),
            private=True,
        )
        tag = lambda index: ["#autopilot-" + RECORDS[index]]  # noqa: E731
        self.ghost = FakeGhost(
            [
                post(IDS[0], "first-story", tags=tag(0)),
                post(IDS[1], "second-story", tags=tag(1)),
                post(IDS[2], "third-story", tags=tag(2)),
                post(IDS[3], "draft-story", status="draft", tags=tag(3)),
                # The record's slug and tag without its pinned ID are not proof of ownership.
                post(IDS[4], "unpinned-story", tags=tag(4)),
                # A post owned by another ledger, or by nobody.
                post(IDS[5], "new-ledger-story", tags=["#autopilot-other"]),
            ]
        )
        self.plan_path = self.folder / "plan.json"
        self.receipt = self.folder / "receipt.json"

    def make_plan(self):
        plan = retire.plan(self.ghost, self.ledger, at="2026-10-06T04:00:00Z")
        public.write_json_atomic(self.plan_path, plan, private=True)
        return plan

    def status(self):
        return {key: item["status"] for key, item in self.ghost.posts.items()}

    def test_plan_lists_only_owned_published_posts_and_writes_nothing(self):
        plan = self.make_plan()
        self.assertEqual(self.ghost.saves, [])
        self.assertEqual([item["post_id"] for item in plan["to_draft"]], IDS[:3])
        self.assertEqual([item["post_id"] for item in plan["owned_drafts"]], [IDS[3]])
        self.assertEqual([item["post_id"] for item in plan["incomplete_ownership"]], [IDS[4]])
        self.assertEqual({item["post_id"] for item in plan["unowned"]}, {IDS[4], IDS[5]})
        self.assertEqual(plan["records_without_post"], [RECORDS[4]])
        self.assertEqual(plan["summary"]["to_draft"], 3)

    def test_apply_drafts_exactly_the_plan_and_never_touches_other_posts(self):
        self.make_plan()
        summary = retire.apply(self.ghost, self.plan_path, self.receipt, at="2026-10-06T05:00:00Z")
        self.assertEqual(summary, {"drafted": 3, "failed": 0})
        self.assertEqual(
            self.status(),
            dict(zip(IDS, ["draft", "draft", "draft", "draft", "published", "published"], strict=True)),
        )
        self.assertEqual([change for _, change in self.ghost.saves], [{"status": "draft"}] * 3)
        receipt = retire.unsealed(public.read_json(self.receipt), "retire-receipt")
        self.assertEqual(receipt["posts"][IDS[0]]["before"]["status"], "published")
        self.assertEqual(receipt["posts"][IDS[0]]["after"]["status"], "draft")
        # A rerun finds nothing left to do.
        self.assertEqual(retire.apply(self.ghost, self.plan_path, self.receipt), {"drafted": 3, "failed": 0})
        self.assertEqual(len(self.ghost.saves), 3)
        self.assertFalse(hasattr(self.ghost, "delete"))

    def test_partial_failure_and_a_lost_reply_resume_without_double_writes(self):
        self.make_plan()
        self.ghost.fail_after.add(IDS[1])
        summary = retire.apply(self.ghost, self.plan_path, self.receipt)
        self.assertEqual(summary, {"drafted": 1, "pending": 1, "uncertain": 1, "failed": 2})
        self.assertEqual(self.status()[IDS[1]], "draft")
        self.ghost.fail_before.add(IDS[2])
        summary = retire.apply(self.ghost, self.plan_path, self.receipt)
        # The committed change is recognized, not repeated; the refused one stays uncertain.
        self.assertEqual(summary, {"drafted": 2, "uncertain": 1, "failed": 1})
        self.assertEqual([item for item, _ in self.ghost.saves], [IDS[0], IDS[1], IDS[2]])
        summary = retire.apply(self.ghost, self.plan_path, self.receipt)
        self.assertEqual(summary, {"drafted": 3, "failed": 0})
        self.assertEqual([item for item, _ in self.ghost.saves], [IDS[0], IDS[1], IDS[2], IDS[2]])

    def test_posts_changed_after_planning_are_left_alone(self):
        self.make_plan()
        self.ghost.posts[IDS[0]]["updated_at"] = "2026-10-06T04:30:00.000Z"
        self.ghost.posts[IDS[1]]["status"] = "draft"
        self.ghost.posts[IDS[2]]["slug"] = "renamed"
        summary = retire.apply(self.ghost, self.plan_path, self.receipt)
        self.assertEqual(
            summary, {"already_draft": 1, "changed_since_plan": 1, "ownership_changed": 1, "failed": 0}
        )
        self.assertEqual(self.ghost.saves, [])

    def test_restore_republishes_exactly_the_drafted_posts_with_their_dates(self):
        self.make_plan()
        # Someone else drafted this one; restoring must not publish it.
        self.ghost.posts[IDS[2]]["status"] = "draft"
        self.assertEqual(
            retire.apply(self.ghost, self.plan_path, self.receipt),
            {"already_draft": 1, "drafted": 2, "failed": 0},
        )
        output = self.folder / "restore.json"
        self.ghost.fail_after.add(IDS[1])
        self.assertEqual(
            retire.restore(self.ghost, self.receipt, output), {"republished": 1, "uncertain": 1, "failed": 1}
        )
        self.assertEqual(retire.restore(self.ghost, self.receipt, output), {"republished": 2, "failed": 0})
        statuses = self.status()
        self.assertEqual([statuses[key] for key in IDS[:3]], ["published", "published", "draft"])
        restored = [change for _, change in self.ghost.saves if change["status"] == "published"]
        self.assertEqual(restored, [{"status": "published", "published_at": "2026-10-05T12:00:00.000Z"}] * 2)
        self.assertEqual(retire.restore(self.ghost, self.receipt, output), {"republished": 2, "failed": 0})

    def test_a_post_edited_while_retired_is_not_republished(self):
        self.make_plan()
        retire.apply(self.ghost, self.plan_path, self.receipt)
        self.ghost.posts[IDS[0]]["updated_at"] = "2026-10-07T00:00:00.000Z"
        summary = retire.restore(self.ghost, self.receipt, self.folder / "restore.json")
        self.assertEqual(summary, {"changed": 1, "republished": 2, "failed": 0})
        self.assertEqual(self.status()[IDS[0]], "draft")

    def test_a_lost_draft_reply_is_restored_without_another_apply(self):
        self.make_plan()
        self.ghost.fail_after.add(IDS[1])
        retire.apply(self.ghost, self.plan_path, self.receipt)
        # Rollback straight away: the committed but unconfirmed draft is republished too.
        summary = retire.restore(self.ghost, self.receipt, self.folder / "restore.json")
        self.assertEqual(summary, {"failed": 0, "republished": 2})
        self.assertEqual([self.status()[key] for key in IDS[:3]], ["published"] * 3)
        restored = retire.unsealed(public.read_json(self.folder / "restore.json"), "restore-receipt")
        self.assertEqual(restored["posts"][IDS[1]]["retired_as"], "uncertain")
        # An unconfirmed draft request that never reached Ghost leaves nothing to restore.
        self.setUp()
        self.make_plan()
        self.ghost.fail_before.add(IDS[1])
        retire.apply(self.ghost, self.plan_path, self.receipt)
        summary = retire.restore(self.ghost, self.receipt, self.folder / "restore.json")
        self.assertEqual(summary, {"failed": 0, "not_drafted": 1, "republished": 1})

    def test_an_owner_edit_inside_the_recovery_window_is_never_republished(self):
        edits = {
            "title": {"title": "Owner's unreleased title"},
            "body": {"html": "<p>Owner's unreleased text</p>", "lexical": '{"root":{"children":[]}}'},
            "both": {"title": "Owner's unreleased title", "html": "<p>Owner's unreleased text</p>"},
        }
        for name, edit in edits.items():
            with self.subTest(edit=name):
                self.setUp()
                self.make_plan()
                self.ghost.fail_after.add(IDS[1])
                retire.apply(self.ghost, self.plan_path, self.receipt)
                # Ten seconds later, inside the window Ghost's update time is judged by.
                self.ghost.posts[IDS[1]].update(edit, updated_at=stamp(seconds=10))
                summary = retire.restore(self.ghost, self.receipt, self.folder / "restore.json")
                # The pass stopped at the lost reply, so only the first post is republished.
                self.assertEqual(summary, {"changed": 1, "failed": 0, "republished": 1})
                edited = self.ghost.posts[IDS[1]]
                self.assertEqual(edited["status"], "draft")
                self.assertEqual({key: edited[key] for key in edit}, edit)
                published = [
                    post_id for post_id, change in self.ghost.saves if change["status"] == "published"
                ]
                self.assertEqual(published, [IDS[0]])
        # Apply's own reconciliation treats such a draft as someone else's too.
        self.setUp()
        self.make_plan()
        self.ghost.fail_after.add(IDS[1])
        retire.apply(self.ghost, self.plan_path, self.receipt)
        self.ghost.posts[IDS[1]].update(edits["both"], updated_at=stamp(seconds=10))
        self.assertEqual(
            retire.apply(self.ghost, self.plan_path, self.receipt),
            {"already_draft": 1, "drafted": 2, "failed": 0},
        )
        self.assertEqual(
            retire.restore(self.ghost, self.receipt, self.folder / "restore.json"),
            {"failed": 0, "republished": 2},
        )
        self.assertEqual(self.ghost.posts[IDS[1]]["status"], "draft")
        # A receipt written without the content digest cannot establish ownership.
        entry = {"attempted_at": stamp(), "sent_token": "x"}
        self.assertFalse(retire.ours(entry, self.ghost.posts[IDS[0]]))

    def test_a_refused_request_changed_nothing_and_is_retried(self):
        self.make_plan()
        self.ghost.refuse.add(IDS[0])
        summary = retire.apply(self.ghost, self.plan_path, self.receipt)
        # Ghost's refusal is definite, so the pass continues with the other posts.
        self.assertEqual(summary, {"drafted": 2, "failed": 1, "refused": 1})
        self.assertEqual(self.status()[IDS[0]], "published")
        # If someone else drafts it meanwhile, it was never this tool's draft.
        self.ghost.posts[IDS[0]]["status"] = "draft"
        self.assertEqual(
            retire.apply(self.ghost, self.plan_path, self.receipt),
            {"already_draft": 1, "drafted": 2, "failed": 0},
        )
        self.assertEqual(
            retire.restore(self.ghost, self.receipt, self.folder / "restore.json"),
            {"failed": 0, "republished": 2},
        )
        self.assertEqual(self.status()[IDS[0]], "draft")
        self.assertTrue(retire.refused(GhostError("Ghost request failed (HTTP 422)", 422)))
        self.assertFalse(retire.refused(GhostError("Ghost request failed (HTTP 502)", 502)))
        # Only the status Ghost answered with counts, never the wording of a message.
        self.assertFalse(retire.refused(GhostError("Ghost request failed (HTTP 422)")))
        self.assertFalse(retire.refused(OSError("timed out")))

    def test_a_rerun_after_restore_does_not_retire_again(self):
        self.make_plan()
        self.ghost.fail_before.add(IDS[1])
        retire.apply(self.ghost, self.plan_path, self.receipt)
        retire.restore(self.ghost, self.receipt, self.folder / "restore.json")
        saves = len(self.ghost.saves)
        # The rolled-back retirement cannot resume and draft the posts it never reached.
        with self.assertRaisesRegex(ValueError, "restored"):
            retire.apply(self.ghost, self.plan_path, self.receipt)
        self.assertEqual(len(self.ghost.saves), saves)
        self.assertEqual([self.status()[key] for key in IDS[:3]], ["published"] * 3)
        # A second restore resumes against the same reviewed plan.
        self.assertEqual(
            retire.restore(self.ghost, self.receipt, self.folder / "restore.json"),
            {"failed": 0, "not_drafted": 1, "republished": 1},
        )

    def test_unconfirmed_writes_are_settled_by_time_and_token(self):
        # (a) The draft never reached Ghost; much later someone else drafts the post.
        self.make_plan()
        self.ghost.fail_before.add(IDS[0])
        retire.apply(self.ghost, self.plan_path, self.receipt)
        self.ghost.posts[IDS[0]].update(status="draft", updated_at=stamp(hours=1))
        summary = retire.restore(self.ghost, self.receipt, self.folder / "a.json")
        self.assertEqual(summary, {"changed": 1, "failed": 0})
        self.assertEqual(self.status()[IDS[0]], "draft")
        # (b) The draft committed and its reply was lost; the post was edited while retired.
        self.setUp()
        self.make_plan()
        self.ghost.fail_after.add(IDS[0])
        retire.apply(self.ghost, self.plan_path, self.receipt)
        self.ghost.posts[IDS[0]]["updated_at"] = stamp(hours=2)
        summary = retire.restore(self.ghost, self.receipt, self.folder / "b.json")
        self.assertEqual(summary, {"changed": 1, "failed": 0})
        # (c) Both replies were lost, then the owner drafted the post again: no second write.
        self.setUp()
        self.make_plan()
        self.ghost.fail_after.add(IDS[0])
        retire.apply(self.ghost, self.plan_path, self.receipt)
        self.ghost.fail_after.add(IDS[0])
        output = self.folder / "c.json"
        self.assertEqual(retire.restore(self.ghost, self.receipt, output), {"failed": 1, "uncertain": 1})
        self.assertEqual(self.status()[IDS[0]], "published")
        self.ghost.posts[IDS[0]].update(status="draft", updated_at=stamp(hours=3))
        saves = len(self.ghost.saves)
        self.assertEqual(retire.restore(self.ghost, self.receipt, output), {"changed": 1, "failed": 0})
        self.assertEqual(len(self.ghost.saves), saves)
        # Apply's own reconciliation uses the same window: a later draft is not its own.
        self.setUp()
        self.make_plan()
        self.ghost.fail_before.add(IDS[0])
        retire.apply(self.ghost, self.plan_path, self.receipt)
        self.ghost.posts[IDS[0]].update(status="draft", updated_at=stamp(hours=1))
        summary = retire.apply(self.ghost, self.plan_path, self.receipt)
        self.assertEqual(summary, {"already_draft": 1, "drafted": 2, "failed": 0})
        self.assertEqual(
            retire.restore(self.ghost, self.receipt, self.folder / "d.json"), {"failed": 0, "republished": 2}
        )
        self.assertEqual(self.status()[IDS[0]], "draft")

    def test_a_post_pinned_by_two_records_is_retired_once(self):
        raw = public.read_json(self.ledger)
        raw["records"][1]["editorial"].update(article_path="/first-story/", ghost_id=IDS[0])
        public.write_json_atomic(self.ledger, raw, private=True)
        self.ghost.posts[IDS[0]]["tags"].append({"name": "#autopilot-" + RECORDS[1]})
        plan = retire.plan(self.ghost, self.ledger)
        self.assertEqual([item["post_id"] for item in plan["to_draft"]].count(IDS[0]), 1)
        self.assertIn("claimed_twice", {item["reason"] for item in plan["incomplete_ownership"]})

    def test_a_renamed_pinned_post_is_listed_for_review(self):
        self.ghost.posts[IDS[0]]["slug"] = "renamed-by-owner"
        plan = retire.plan(self.ghost, self.ledger)
        listed = {item["post_id"]: item["reason"] for item in plan["incomplete_ownership"]}
        self.assertEqual(listed, {IDS[0]: "slug_or_tag_changed", IDS[4]: "not_the_pinned_post"})
        self.assertNotIn(IDS[0], [item["post_id"] for item in plan["to_draft"]])

    def test_receipts_and_plans_cannot_be_mixed_or_edited(self):
        self.make_plan()
        retire.apply(self.ghost, self.plan_path, self.receipt)
        other = self.folder / "other-plan.json"
        public.write_json_atomic(other, {**public.read_json(self.plan_path), "created_at": "x"}, private=True)
        with self.assertRaisesRegex(ValueError, "unmodified"):
            retire.apply(self.ghost, other, self.receipt)
        changed = retire.plan(self.ghost, self.ledger, at="2026-10-06T06:00:00Z")
        public.write_json_atomic(other, changed, private=True)
        with self.assertRaisesRegex(ValueError, "another plan"):
            retire.apply(self.ghost, other, self.receipt)

    def test_command_line_requires_a_reviewed_plan_and_receipt(self):
        for argv in (
            ["--config", "c", "--ledger", "l"],
            ["--config", "c", "--ledger", "l", "--plan", "p", "--apply"],
            ["--config", "c", "--restore", "r", "--ledger", "l"],
            ["--config", "c", "--ledger", "l", "--plan", "p", "--receipt", "r"],
        ):
            with (
                self.subTest(argv=argv),
                patch.object(sys, "argv", ["retire_ledger_posts.py", *argv]),
                patch("sys.stderr"),
                self.assertRaises(SystemExit) as raised,
            ):
                retire.main()
            self.assertEqual(raised.exception.code, 2)

    def test_restore_refuses_the_same_file_before_locking_or_marking_it_rolled_back(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "receipt.json"
            source.write_text("original")
            symlink, hardlink = (Path(directory) / name for name in ("symlink.json", "hardlink.json"))
            symlink.symlink_to(source)
            hardlink.hardlink_to(source)
            for destination in (source, symlink, hardlink):
                with (
                    self.subTest(destination=destination.name),
                    patch.object(retire.public, "ledger_lock") as lock,
                ):
                    client = Mock()
                    with self.assertRaisesRegex(ValueError, "different files"):
                        retire.restore(client, source, destination)
                    lock.assert_not_called()
                    client.inventory.assert_not_called()
                    self.assertEqual(source.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
