"""Cutover checks fail closed on lost history, stale evidence and incomplete soaks."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wtf_adsb_publisher import cutover


class CutoverTests(unittest.TestCase):
    def comparison(self):
        record = {
            "id": "retained",
            "state": "withdrawn",
            "editorial": {"article_path": "/stories/retained/", "ghost_id": "post"},
            "automation": {"source": {"id": 12, "evidence": {"fact": 1}}},
            "history": [{"revision": 1, "automation": {"source": {"id": 12}}}],
        }
        baseline = {"records": [record]}
        result = {
            "ledger": copy.deepcopy(baseline),
            "report": {"privacy": "ok", "build": "ok", "basemaps_missing": 0},
            "snapshot": {"events": []},
            "writes": [],
            "existing_post_ids": ["post"],
            "posts": [{"id": "post", "tags": ["owner:retained"], "status": "draft"}],
            "media": {"retained": {"image": "retained.svg"}},
        }
        return baseline, result, copy.deepcopy(result)

    def test_withdrawn_article_ownership_url_history_and_media_are_preserved(self):
        baseline, old, new = self.comparison()
        self.assertTrue(cutover.compare_runs(baseline, old, new)["passed"])
        mutations = (
            lambda data: data["ledger"]["records"][0]["history"].clear(),
            lambda data: data["ledger"]["records"][0]["editorial"].update(article_path="/changed/"),
            lambda data: data["ledger"]["records"][0]["editorial"].update(ghost_id="wrong"),
            lambda data: data["posts"][0]["tags"].clear(),
            lambda data: data["media"].clear(),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                new = copy.deepcopy(old)
                mutate(new)
                self.assertFalse(cutover.compare_runs(baseline, old, new)["passed"])

    def test_sanitized_current_source_does_not_mask_prior_history_changes(self):
        baseline, old, new = self.comparison()
        new["ledger"]["records"][0]["automation"].update(
            source={"id": "opaque", "evidence": {"fact": 1}}, engine_event_id="opaque"
        )
        for result in (old, new):
            result["normalized_sources"] = {"retained": {"evidence": {"fact": 1}}}
        self.assertTrue(cutover.compare_runs(baseline, old, new)["passed"])
        new["ledger"]["records"][0]["history"][0]["automation"]["source"]["id"] = 13
        self.assertFalse(cutover.compare_runs(baseline, old, new)["passed"])

    def test_missing_basemap_holds_even_when_build_succeeds(self):
        baseline, old, new = self.comparison()
        new["report"]["basemaps_missing"] = 1
        self.assertFalse(cutover.compare_runs(baseline, old, new)["passed"])

    def test_pending_new_candidate_source_change_fails_comparison(self):
        from test_public_events import editorial, packet

        _, old, _ = self.comparison()
        record = {
            "id": "new",
            "state": "draft",
            "evidence": packet(),
            "editorial": editorial(),
            "automation": {"source": {"hex": "a12345", "kind": "circling", "first_seen": 1}},
        }
        record["evidence"]["received"]["source"] = "adsb_icao"
        old["ledger"]["records"] = [record]
        new = copy.deepcopy(old)
        self.assertTrue(cutover.compare_runs({"records": []}, old, new)["passed"])
        new["ledger"]["records"][0]["evidence"]["received"]["source"] = "adsb_icao_nt"
        self.assertFalse(cutover.compare_runs({"records": []}, old, new)["passed"])
        new = copy.deepcopy(old)
        for result in (old, new):
            result["normalized_media"] = {"new": [[{"lat": 40, "lon": -90, "at": 1}]]}
        new["ledger"]["records"][0]["automation"]["media_parts"] = []
        self.assertFalse(cutover.compare_runs({"records": []}, old, new)["passed"])

    def test_shadow_requires_full_continuous_window_after_failure_or_change(self):
        start = 10 * 86400 + 4000
        samples = [{"at": start + n * 1800, "passed": True, "artifacts_sha256": "a"} for n in range(49)]
        self.assertTrue(cutover.shadow_gate(samples, now=samples[-1]["at"]))
        self.assertFalse(cutover.shadow_gate(samples[:-1], now=samples[-1]["at"]))
        self.assertFalse(cutover.shadow_gate(samples, now=samples[-1]["at"] + 2101))
        self.assertTrue(
            cutover.shadow_gate(
                [{"at": start - 1800, "passed": False, "artifacts_sha256": "a"}, *samples],
                now=samples[-1]["at"],
            )
        )
        for key, value in (
            ("passed", False),
            ("artifacts_sha256", "b"),
            ("at", start + 100),
            ("configuration_sha256", "changed"),
            ("feed_identity", ["other", "generation"]),
            ("runtime_identity", "restarted"),
        ):
            changed = copy.deepcopy(samples)
            changed[1][key] = value
            self.assertFalse(cutover.shadow_gate(changed, now=samples[-1]["at"]))

    def test_feed_head_advancement_retries_but_stale_feed_still_holds(self):
        state = {"stream_id": "stream", "generation": "generation", "cursor": 1, "health": {"mode": "live"}}
        advanced = {**state, "cursor": 2}
        manifest = {"stream_id": "stream", "generation": "generation", "head_sequence": 2}
        with (
            patch.object(cutover, "ingest_directory", side_effect=[state, advanced]) as ingest,
            patch.object(cutover.public, "read_json", return_value=manifest),
            patch.object(cutover, "feed_fresh", return_value=True),
        ):
            self.assertEqual(cutover.ready_feed("feed", "inbox", at=1)["cursor"], 2)
            self.assertEqual(ingest.call_count, 2)
        with (
            patch.object(cutover, "ingest_directory", return_value=advanced),
            patch.object(cutover.public, "read_json", return_value=manifest),
            patch.object(cutover, "feed_fresh", return_value=False),
        ):
            with self.assertRaises(ValueError):
                cutover.ready_feed("feed", "inbox", at=1)

    def test_live_readiness_uses_time_after_import(self):
        state = {"stream_id": "stream", "generation": "generation", "cursor": 2, "health": {"mode": "live"}}
        manifest = {"stream_id": "stream", "generation": "generation", "head_sequence": 2}
        with (
            patch.object(cutover, "ingest_directory", return_value=state),
            patch.object(cutover.public, "read_json", return_value=manifest),
            patch.object(cutover.time, "time", return_value=1200),
            patch.object(cutover, "feed_fresh", return_value=True) as fresh,
        ):
            cutover.ready_feed("feed", "inbox")
        fresh.assert_called_once_with(state, 1200, 300)

    def test_commit_is_durable_exclusive_and_cannot_be_overridden(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "commit.json"
            evidence = {
                name + "_sha256": "hash"
                for name in ("artifacts", "configuration", "inventory", "state", "reservations")
            }
            evidence.update(feed={"cursor": 4}, version=999, phase="safe", at=-1)
            with patch.object(cutover.time, "time", return_value=100):
                receipt = cutover.commit_receipt(path, evidence)
            self.assertEqual(receipt["phase"], "external_mutations_may_have_started")
            self.assertEqual(receipt["version"], 1)
            self.assertEqual(receipt["at"], 100)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                cutover.commit_receipt(path, evidence)


if __name__ == "__main__":
    unittest.main()
