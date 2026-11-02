"""Container loss, wrapper loss and fencing must not duplicate publisher work."""

import copy
import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("container_jobs", ROOT / "tools/run_container_job.py")
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)
IMAGE = "sha256:" + "a" * 64


class FakeDocker:
    def __init__(self, config):
        self.config = config
        self.rows = []
        self.creations = 0
        self.starts = 0
        self.removals = 0
        self.followups = 0
        self.autopilot_busy = False
        self.lost_reply = False
        self.fail_remove = False
        self.finish_on_sleep = True
        self.exit_code = 0

    def configuration(self):
        return {
            "services": {
                role: {
                    "image": IMAGE,
                    "pull_policy": "never",
                    "restart": "no",
                    "read_only": True,
                    "volumes": [
                        {"target": "/state", "source": self.config["publisher_state_directory"]},
                        {
                            "target": "/control",
                            "source": str(Path(self.config["activation_hold"]).parent),
                            "read_only": True,
                        },
                        {"target": "/feed", "source": "/unused/feed", "read_only": True},
                    ],
                }
                for role in jobs.ROLES
            }
        }

    def containers(self):
        return copy.deepcopy(self.rows)

    def create(self, role, intent):
        self.creations += 1
        self.rows.append(
            {
                "Id": "b" * 64,
                "Name": "/" + intent["name"],
                "Image": intent["image"],
                "Config": {
                    "Labels": {
                        jobs.LABEL + key: intent[key]
                        for key in ("role", "run_id", "image", "configuration_sha256")
                    }
                },
                "State": {
                    "Status": "running",
                    "Running": True,
                    "Pid": 123,
                    "ExitCode": 0,
                    "StartedAt": "2026-10-09T00:00:00Z",
                    "FinishedAt": "",
                    "OOMKilled": False,
                },
            }
        )
        if self.lost_reply:
            raise jobs.JobError("Docker reply lost after container started")
        return self.rows[-1]["Id"]

    def start(self, identifier):
        self.starts += 1
        self.rows[0]["State"].update(Status="running", Running=True, Pid=123)

    def logs(self, identifier):
        return {"text": "private job output", "truncated": False}

    def remove(self, identifier):
        self.removals += 1
        if self.fail_remove:
            raise jobs.JobError("Docker unavailable during removal")
        self.rows = [row for row in self.rows if row["Id"] != identifier]

    def follow_up(self):
        if self.autopilot_busy:
            return False
        self.followups += 1
        return True

    def sleep(self, duration):
        if not self.finish_on_sleep:
            raise jobs.JobError("Wrapper interrupted")
        for row in self.rows:
            row["State"].update(
                Status="exited",
                Running=False,
                Pid=0,
                ExitCode=self.exit_code,
                FinishedAt="2026-10-09T00:01:00Z",
            )


class ContainerJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "state").mkdir()
        (self.root / "control").mkdir()
        for name in ("compose.yaml", "compose.env", "publisher.json"):
            (self.root / name).write_text("test input")
        (self.root / "state/ledger.json").write_text('{"records":[]}')
        with closing(sqlite3.connect(self.root / "state/budget.sqlite3")) as database:
            database.executescript("CREATE TABLE meta(key,value); CREATE TABLE ai_runs(id);")
        for name in (
            "writer/status.json.lock",
            "weekly/status.json.lock",
            "ledger.json.lock",
            "budget.ai.lock",
        ):
            path = self.root / "state" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.config = {
            "version": 1,
            "project": "wtf-publisher",
            "compose_file": str(self.root / "compose.yaml"),
            "env_file": str(self.root / "compose.env"),
            "job_directory": str(self.root / "jobs"),
            "activation_hold": str(self.root / "control/activation.hold"),
            "publisher_state_directory": str(self.root / "state"),
            "budget_database": str(self.root / "state/budget.sqlite3"),
            "configuration_files": [str(self.root / "publisher.json")],
        }
        self.docker = FakeDocker(self.config)
        self.runner = jobs.Runner(
            self.config, docker=self.docker, clock=lambda: 1000, sleep=self.docker.sleep
        )

    def receipts(self):
        return list((self.root / "jobs/receipts").glob("*.json"))

    def pending(self):
        return list((self.root / "jobs/pending-followups").glob("*.json"))

    def interrupted(self, role="write"):
        self.docker.finish_on_sleep = False
        with self.assertRaises(jobs.JobError):
            self.runner.run_role(role)
        self.docker.finish_on_sleep = True

    def test_completed_job_saves_private_receipt_before_removal(self):
        remove = self.docker.remove

        def checked_remove(identifier):
            receipt = jobs.read_json(self.receipts()[0])
            self.assertEqual(receipt["container_id"], identifier)
            self.assertEqual(receipt["logs"]["text"], "private job output")
            remove(identifier)

        self.docker.remove = checked_remove
        self.assertEqual(self.runner.run_role("publish"), 0)
        self.assertEqual(self.receipts()[0].stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.runner.intent_path("publish").exists())

    def test_lost_create_reply_recovers_exact_container_without_new_work(self):
        self.docker.lost_reply = True
        with self.assertRaises(jobs.JobError):
            self.runner.run_role("write")
        self.assertEqual(self.runner.run_role("write"), 0)
        self.assertEqual(self.docker.creations, 1)

    def test_wrapper_interruption_and_fence_still_allow_reconciliation(self):
        self.interrupted()
        Path(self.config["activation_hold"]).touch()
        self.assertIsNone(self.runner.run_role("write", reconcile_only=True))
        self.docker.sleep(5)
        self.assertEqual(self.runner.run_role("write", reconcile_only=True), 0)
        self.assertEqual(self.docker.creations, 1)

    def test_missing_or_mismatched_container_holds_instead_of_repeating(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.setUp()
                self.interrupted()
                if missing:
                    self.docker.rows.clear()
                else:
                    self.docker.rows[0]["Config"]["Labels"][jobs.LABEL + "run_id"] = "other"
                with self.assertRaises(jobs.JobError):
                    self.runner.run_role("write")
                self.assertEqual(self.docker.creations, 1)
                self.assertTrue(self.runner.intent_path("write").exists())

    def test_receipt_survives_remove_failure_and_recovery_does_not_relaunch(self):
        self.docker.fail_remove = True
        with self.assertRaises(jobs.JobError):
            self.runner.run_role("publish")
        self.assertEqual(len(self.receipts()), 1)
        self.docker.fail_remove = False
        self.assertEqual(self.runner.run_role("publish"), 0)
        self.assertEqual(self.docker.creations, 1)

    def test_reconcile_never_creates_or_starts_unstarted_jobs(self):
        self.assertIsNone(self.runner.run_role("weekly", reconcile_only=True))
        self.interrupted("weekly")
        self.docker.rows[0]["State"].update(Status="created", Running=False, Pid=0, StartedAt="")
        self.assertIsNone(self.runner.run_role("weekly", reconcile_only=True))
        self.assertEqual(self.docker.starts, 0)
        self.assertEqual(self.docker.creations, 1)

    def test_changed_config_cannot_start_prepared_job(self):
        self.interrupted()
        self.docker.rows[0]["State"].update(Status="created", Running=False, Pid=0, StartedAt="")
        (self.root / "publisher.json").write_text("changed")
        with self.assertRaises(jobs.JobError):
            self.runner.run_role("write")
        self.assertEqual(self.docker.starts, 0)

    def test_weekly_followup_is_durable_and_waits_for_fence_removal(self):
        self.runner.run_role("weekly")
        hold = Path(self.config["activation_hold"])
        hold.touch()
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 0)
        self.assertTrue(jobs.read_json(self.receipts()[0])["follow_up_pending"])
        self.assertEqual(self.pending()[0].stat().st_mode & 0o777, 0o600)
        hold.unlink()
        self.runner.follow_up()
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 1)
        self.assertEqual(self.pending(), [])

    def test_busy_systemd_unit_keeps_followup_until_queue_is_accepted(self):
        self.runner.run_role("weekly")
        self.docker.autopilot_busy = True
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 0)
        self.assertEqual(len(self.pending()), 1)
        self.assertTrue(jobs.read_json(self.receipts()[0])["follow_up_pending"])
        self.docker.autopilot_busy = False
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 1)
        self.assertEqual(self.pending(), [])

    def test_followup_waits_for_running_and_stopped_publisher_intent_reconciliation(self):
        self.runner.run_role("weekly")
        self.interrupted("publish")
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 0)
        self.docker.sleep(5)
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 0)
        self.runner.run_role("publish", reconcile_only=True)
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 1)
        self.assertEqual(self.docker.creations, 2)

    def test_marker_write_failure_retains_intent_and_recovers_from_existing_receipt(self):
        save = jobs.private_json

        def interrupted_save(path, value):
            if path.parent.name == "pending-followups":
                raise OSError("Interrupted before marker became durable")
            save(path, value)

        with patch.object(jobs, "private_json", side_effect=interrupted_save), self.assertRaises(OSError):
            self.runner.run_role("weekly")
        self.assertTrue(self.runner.intent_path("weekly").exists())
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.docker.removals, 0)
        self.runner.run_role("weekly")
        self.assertEqual(len(self.pending()), 1)
        self.assertFalse(self.runner.intent_path("weekly").exists())
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 1)
        self.assertEqual(self.docker.creations, 1)

    def test_marker_survives_container_removal_failure(self):
        self.docker.fail_remove = True
        with self.assertRaises(jobs.JobError):
            self.runner.run_role("weekly")
        self.assertEqual(len(self.pending()), 1)
        self.docker.fail_remove = False
        self.runner.run_role("weekly")
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 1)
        self.assertEqual(self.docker.creations, 1)

    def test_lost_followup_ack_keeps_marker_and_allows_reenqueue(self):
        self.runner.run_role("weekly")
        save = jobs.private_json

        def interrupted_save(path, value):
            if "follow_up_queued_at" in value:
                raise OSError("Interrupted after systemd accepted the start")
            save(path, value)

        with patch.object(jobs, "private_json", side_effect=interrupted_save), self.assertRaises(OSError):
            self.runner.follow_up()
        self.assertEqual(self.docker.followups, 1)
        self.assertEqual(len(self.pending()), 1)
        self.assertTrue(jobs.read_json(self.receipts()[0])["follow_up_pending"])
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 2)
        self.assertEqual(self.pending(), [])

    def test_durable_ack_with_stale_marker_is_not_enqueued_again(self):
        self.runner.run_role("weekly")
        marker = self.pending()[0]
        unlink = Path.unlink

        def interrupted_unlink(path, *args, **kwargs):
            if path == marker:
                raise OSError("Interrupted before marker removal")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", interrupted_unlink), self.assertRaises(OSError):
            self.runner.follow_up()
        self.assertFalse(jobs.read_json(self.receipts()[0])["follow_up_pending"])
        self.assertEqual(self.docker.followups, 1)
        self.runner.follow_up()
        self.assertEqual(self.pending(), [])
        self.assertEqual(self.docker.followups, 1)

    def test_followup_reads_only_receipts_in_pending_index(self):
        self.runner.run_role("weekly")
        receipt_path = self.receipts()[0]
        for number in range(100):
            (receipt_path.parent / f"historical-publish-{number}.json").write_text(
                "unused private log" * 4096
            )
        with patch.object(jobs, "read_json", wraps=jobs.read_json) as read:
            self.runner.follow_up()
        read.assert_called_once_with(receipt_path)
        self.assertEqual(self.docker.followups, 1)

    def test_newly_queued_wrapper_waits_for_followup_acknowledgment(self):
        self.runner.run_role("weekly")
        entered = Event()
        results = []

        def queued_wrapper():
            entered.set()
            try:
                self.runner.reconcile(exclude="publish")
                results.append(self.runner.run_role("publish"))
            except Exception as error:
                results.append(error)

        thread = Thread(target=queued_wrapper, daemon=True)

        def enqueue():
            thread.start()
            self.assertTrue(entered.wait(1))
            return True

        self.docker.follow_up = enqueue
        self.runner.follow_up()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [0])
        self.assertEqual(self.docker.creations, 2)

    def test_systemd_start_requires_inactive_unit_without_queued_job(self):
        docker = jobs.Docker(self.config)
        for state, job, accepted in (
            ("active", "", False),
            ("activating", "123", False),
            ("deactivating", "", False),
            ("inactive", "123", False),
            ("inactive", "", True),
            ("failed", "0", True),
        ):
            with (
                self.subTest(state=state, job=job),
                patch.object(docker, "command", return_value=f"ActiveState={state}\nJob={job}\n") as command,
            ):
                self.assertEqual(docker.follow_up(), accepted)
                self.assertEqual(command.call_count, 2 if accepted else 1)
                if accepted:
                    command.assert_called_with(
                        ["systemctl", "--user", "start", "--no-block", "wtf-adsb-autopilot.service"]
                    )

    def test_failed_weekly_does_not_queue_publication(self):
        self.docker.exit_code = 1
        self.assertEqual(self.runner.run_role("weekly"), 1)
        self.runner.follow_up()
        self.assertEqual(self.docker.followups, 0)

    def test_fence_including_dangling_symlink_and_role_lock_block_launches(self):
        hold = Path(self.config["activation_hold"])
        hold.symlink_to(self.root / "missing")
        with self.assertRaises(jobs.Busy):
            self.runner.run_role("publish")
        hold.unlink()
        with jobs.locked(self.root / "jobs/publish.lock"), self.assertRaises(jobs.Busy):
            self.runner.run_role("publish")
        self.assertEqual(self.docker.creations, 0)

    def test_drain_fences_but_never_kills_orphan_and_checks_shared_locks(self):
        self.interrupted()
        with self.assertRaises(jobs.Busy):
            self.runner.drain()
        self.assertTrue(Path(self.config["activation_hold"]).exists())
        self.assertEqual(self.docker.removals, 0)
        self.docker.sleep(5)
        self.runner.run_role("write", reconcile_only=True)
        with jobs.locked(self.root / "state/budget.ai.lock"), self.assertRaises(jobs.Busy):
            self.runner.drain()
        self.assertTrue(self.runner.drain()["drained"])

    def test_drain_waits_for_launch_bookkeeping_then_fences_new_work(self):
        entered, finished = Event(), Event()
        results = []

        def drain():
            entered.set()
            try:
                results.append(self.runner.drain())
            except Exception as error:
                results.append(error)
            finally:
                finished.set()

        thread = Thread(target=drain, daemon=True)
        try:
            with jobs.locked(self.root / "jobs/launch.lock"):
                thread.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(finished.wait(0.05))
                self.assertFalse(Path(self.config["activation_hold"]).exists())
        finally:
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], dict)
        self.assertTrue(results[0]["drained"])
        self.assertTrue(Path(self.config["activation_hold"]).exists())
        with self.assertRaises(jobs.Busy):
            self.runner.run_role("publish")
        self.assertEqual(self.docker.creations, 0)

    def test_draining_unknown_outcome_never_claims_success(self):
        self.interrupted()
        self.docker.rows.clear()
        with self.assertRaises(jobs.JobError):
            self.runner.drain()

    def test_drain_cancels_only_proven_never_started_container(self):
        self.docker.lost_reply = True
        with self.assertRaises(jobs.JobError):
            self.runner.run_role("weekly")
        self.docker.rows[0]["State"].update(Status="created", Running=False, Pid=0, StartedAt="")
        self.assertTrue(self.runner.drain()["drained"])
        receipt = jobs.read_json(self.receipts()[0])
        self.assertEqual(receipt["phase"], "cancelled_before_start")
        self.assertEqual(receipt["container_id"], "b" * 64)
        self.assertFalse(receipt["follow_up_pending"])
        self.assertEqual(self.docker.starts, 0)

    def test_recovery_after_removal_before_intent_cleanup_uses_receipt(self):
        self.docker.fail_remove = True
        with self.assertRaises(jobs.JobError):
            self.runner.run_role("write")
        self.docker.rows.clear()
        self.assertEqual(self.runner.run_role("write"), 0)
        self.assertEqual(self.docker.creations, 1)

    def test_held_created_normal_job_is_not_reported_as_success(self):
        self.interrupted()
        self.docker.rows[0]["State"].update(Status="created", Running=False, Pid=0, StartedAt="")
        Path(self.config["activation_hold"]).touch()
        with self.assertRaises(jobs.Busy):
            self.runner.run_role("write")
        self.assertEqual(self.docker.starts, 0)

    def test_reconcile_running_job_is_nonblocking_and_creates_nothing(self):
        self.interrupted("publish")
        self.docker.finish_on_sleep = False
        self.assertEqual(self.runner.reconcile(), 0)
        self.assertEqual(self.docker.creations, 1)
        self.assertTrue(self.runner.intent_path("publish").exists())

    def test_drain_refuses_missing_application_locks_or_spending_store(self):
        (self.root / "state/ledger.json.lock").unlink()
        with self.assertRaises(jobs.JobError):
            self.runner.drain()
        (self.root / "state/ledger.json.lock").touch()
        (self.root / "state/budget.sqlite3").unlink()
        with self.assertRaises(sqlite3.Error):
            self.runner.drain()


if __name__ == "__main__":
    unittest.main()
