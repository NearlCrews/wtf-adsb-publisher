"""Observer failures cannot masquerade as a successful soak checkpoint."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("shadow_observer", ROOT / "tools/check_cutover_shadow.py")
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class ShadowObserverTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        key = self.root / "key"
        key.write_text("test fixture")
        config = self.root / "production.json"
        config.write_text(json.dumps({"ghost_origin": "https://example.com", "ghost_key_file": str(key)}))
        self.settings = {
            "evidence_directory": str(self.root / "evidence"),
            "production_config": str(config),
            "production_journal": "unused",
            "approved_artifacts_sha256": "approved",
            "configuration_files": [],
            "minimum_free_bytes": 0,
            "engine_image_id": "sha256:" + "a" * 64,
            "publisher_image_id": "sha256:" + "b" * 64,
            **{key: str(self.root) for key in ("legacy_root", "engine_root", "publisher_root")},
        }

    def test_the_artifact_digest_ignores_session_notes_caches_and_staged_merges(self):
        tree = self.root / "checkout"
        source = tree / "src" / "module.py"
        source.parent.mkdir(parents=True)
        source.write_text("VALUE = 1\n")
        before = observer.artifact_digest([tree])
        noise = [
            *(
                tree / name / "notes.json"
                for name in (
                    ".claude",
                    ".remember",
                    ".superpowers",
                    ".sync",
                    ".mypy_cache",
                    ".hypothesis",
                    "htmlcov",
                    "build",
                    "wtf_adsb_publisher.egg-info",
                )
            ),
            tree / "src" / "nested" / ".claude" / "settings.json",
            tree / "src" / "module.py.sync-merge",
        ]
        for path in noise:
            with self.subTest(path=path.relative_to(tree)):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("written by a session or the sync tool\n")
                self.assertEqual(observer.artifact_digest([tree]), before)
        # A tracked source change still moves the digest, and so does a new source file.
        source.write_text("VALUE = 2\n")
        changed = observer.artifact_digest([tree])
        self.assertNotEqual(changed, before)
        (tree / "src" / "builder.py").write_text("")
        self.assertNotEqual(observer.artifact_digest([tree]), changed)

    def test_unapproved_artifacts_abort_before_ghost_or_native_requests(self):
        with (
            patch.object(observer, "artifact_digest", return_value="unapproved"),
            patch.object(observer, "GhostClient") as ghost,
            patch.object(observer, "native_health") as native,
        ):
            result = observer.checkpoint(self.settings)
        self.assertFalse(result["latest_passed"])
        ghost.assert_not_called()
        native.assert_not_called()

    def test_failed_comparison_or_native_restart_persists_failed_checkpoint(self):
        first = {"runtime_identity": "first", "feed": {"stream_id": "stream", "generation": "generation"}}
        for fails in (False, True):
            with self.subTest(comparison_failure=fails):
                self.settings["evidence_directory"] = str(self.root / str(fails))

                def command(arguments, *, failure=fails, **kwargs):
                    work = Path(arguments[arguments.index("--work") + 1])
                    work.mkdir()
                    (work / "result.json").write_text('{"passed":true}')
                    return SimpleNamespace(returncode=int(failure))

                with (
                    patch.object(observer, "artifact_digest", return_value="approved"),
                    patch.object(observer, "GhostClient"),
                    patch.object(observer.export_ghost_inventory, "export", return_value={}),
                    patch.object(observer, "command_json", return_value={"at": 1000}),
                    patch.object(observer.comparison, "inspect_image", side_effect=lambda image: image),
                    patch.object(observer.subprocess, "run", side_effect=command),
                    patch.object(
                        observer,
                        "native_health",
                        side_effect=[first, {**first, "runtime_identity": "restarted"}],
                    ),
                ):
                    result = observer.checkpoint(self.settings)
                self.assertFalse(result["latest_passed"])
                checkpoint = json.loads(Path(result["latest"]).read_text())
                self.assertFalse(checkpoint["passed"])
                self.assertFalse(result["gate_passed"])

    def test_failed_readiness_keeps_stdout_stderr_and_exit_status_private(self):
        failed = subprocess.CompletedProcess(["docker"], 1, '{"ready":false}', "x" * 70000)
        with patch.object(observer.subprocess, "run", return_value=failed):
            with self.assertRaises(observer.CommandFailure) as caught:
                observer.command_json(["docker", "exec", "fixture"])
        with (
            patch.object(observer, "artifact_digest", return_value="approved"),
            patch.object(observer.comparison, "inspect_image", side_effect=lambda image: image),
            patch.object(observer, "native_health", side_effect=caught.exception),
            patch.object(observer, "GhostClient") as ghost,
        ):
            status = observer.checkpoint(self.settings)
        ghost.assert_not_called()
        self.assertFalse(status["latest_passed"])
        path = Path(status["latest"]).with_name("command-failure.json")
        saved = json.loads(path.read_text())
        self.assertEqual(saved["stdout"], '{"ready":false}')
        self.assertEqual(saved["stderr"], "x" * 65536)
        self.assertTrue(saved["stderr_truncated"])
        self.assertEqual(saved["returncode"], 1)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def readiness(self, cursor=1, *, ready=False, stamp=1000, generation="generation", status="ready"):
        return {
            "ready": ready,
            "stream_id": "stream",
            "generation": generation,
            "head_sequence": 2,
            "health": {
                "ready": True,
                "complete": True,
                "mode": "live",
                "last_receiver_poll": stamp,
                "analysis_watermark": stamp,
            },
            "outputs": [{"name": "files", "cursor": cursor, "status": status}],
        }

    def failure(self, state):
        return observer.CommandFailure(subprocess.CompletedProcess([], 1, json.dumps(state), ""))

    def test_only_progressing_delivery_can_use_readiness_grace(self):
        first = self.readiness()
        ready = self.readiness(2, ready=True)
        with (
            patch.object(observer.time, "time", return_value=1000),
            patch.object(observer.time, "sleep") as sleep,
            patch.object(observer, "command_json", side_effect=[self.failure(first), ready]),
        ):
            result, probes = observer.delivered_readiness("fixture")
        self.assertTrue(result["ready"])
        self.assertEqual(len(probes), 1)
        sleep.assert_called_once_with(2)
        for second in (first, self.readiness(2, ready=True, generation="changed")):
            with (
                self.subTest(state=second),
                patch.object(observer.time, "time", return_value=1000),
                patch.object(observer.time, "sleep"),
                patch.object(observer, "command_json", side_effect=[self.failure(first), second]),
            ):
                with self.assertRaises(observer.CommandFailure):
                    observer.delivered_readiness("fixture")

    def test_stale_analysis_and_failed_output_do_not_get_a_retry(self):
        for state in (self.readiness(stamp=699), self.readiness(status="error")):
            with (
                self.subTest(state=state),
                patch.object(observer.time, "time", return_value=1000),
                patch.object(observer.time, "sleep") as sleep,
                patch.object(observer, "command_json", side_effect=self.failure(state)),
            ):
                with self.assertRaises(observer.CommandFailure):
                    observer.delivered_readiness("fixture")
                sleep.assert_not_called()

    def test_native_feed_must_belong_to_observed_engine_and_reach_its_head(self):
        self.settings.update(shadow_container="fixture", shadow_feed="feed", shadow_inbox="inbox.sqlite3")
        details = {
            "Id": "container",
            "Image": self.settings["engine_image_id"],
            "RestartCount": 0,
            "State": {"Health": {"Status": "healthy"}, "StartedAt": "start"},
        }
        state = self.readiness(2, ready=True)
        feed = {"stream_id": "stream", "generation": "generation", "cursor": 2, "health": {}}
        for change, accepted in (
            ({}, True),
            ({"cursor": 3}, True),
            ({"cursor": 1}, False),
            ({"stream_id": "unrelated"}, False),
            ({"generation": "other"}, False),
        ):
            with (
                self.subTest(change=change),
                patch.object(observer, "command_json", side_effect=[[details], {}]),
                patch.object(observer, "delivered_readiness", return_value=(state, [])),
                patch.object(observer.comparison, "image_ready_feed", return_value={**feed, **change}),
                patch.object(observer, "feed_fresh", return_value=True),
            ):
                if accepted:
                    self.assertEqual(
                        observer.native_health(self.settings)["feed"]["cursor"], change.get("cursor", 2)
                    )
                else:
                    with self.assertRaisesRegex(ValueError, "does not match"):
                        observer.native_health(self.settings)


if __name__ == "__main__":
    unittest.main()
