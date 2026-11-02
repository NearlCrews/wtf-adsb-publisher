"""Configured integrations are reused, invalidate releases and keep paid-call guards."""

import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import run_autopilot
import run_weekly
from test_autopilot import NOW, Ghost, candidate
from test_feed_pipeline import feed
from test_public_writer import LATER, TYPE_FACT, TYPE_SOURCE, answer, good
from test_weekly_research import NOW as WEEKLY_NOW
from test_weekly_research import ordinary, response

from wtf_adsb_publisher import autopilot, budget_store, explain, plugins, public_writer, weekly_research
from wtf_adsb_publisher import public_events as public


def entry(role, factory, *, name=None, version="1.0"):
    return SimpleNamespace(
        name=name or plugins.DEFAULTS[role],
        value=f"synthetic_plugins:{role}",
        dist=SimpleNamespace(metadata={"Name": "synthetic-plugins"}, version=version),
        load=lambda: factory,
    )


class PluginRuntimeTests(unittest.TestCase):
    def test_one_preflight_instance_per_role_is_used_through_publication_and_deployment(self):
        made, used = {}, []
        ghost = Ghost()

        def factory(role):
            def create(settings):
                instance = SimpleNamespace(descriptor=plugins.Descriptor(">=1,<2", role))
                made.setdefault(role, []).append(instance)

                def operation(*args, **kwargs):
                    used.append((role, instance))
                    if role == "reader":
                        return feed()
                    if role == "policy":
                        return []
                    if role == "publisher":
                        return ghost
                    if role == "renderer":
                        destination = args[3]
                        path = destination / "manifest.json"
                        public.write_json_atomic(path, {"events": {}}, private=True)
                        return path
                    if role == "deployer":
                        args[-1]["release"] = {"healthy": True}

                setattr(instance, plugins.METHODS[role], operation)
                return instance

            return create

        entries = {role: entry(role, factory(role)) for role in plugins.DEFAULTS}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            public.write_json_atomic(state / "ledger.json", public.new_ledger("2026-10-01"), private=True)
            config = {"state_dir": str(state), "live_since": NOW, "preview_ssh_host": "synthetic"}

            def build(arguments, **kwargs):
                site = Path(arguments[arguments.index("--output") + 1])
                site.mkdir(parents=True)
                (site / "index.html").write_text("<html>Offline test</html>")

            with (
                patch.object(
                    plugins, "discover", side_effect=lambda role: {entries[role].name: entries[role]}
                ),
                patch.object(run_autopilot, "ROOT", root),
                patch.object(run_autopilot, "source_digest", return_value="fixed-code"),
                patch.object(run_autopilot, "command", side_effect=build),
                patch.object(run_autopilot, "privacy_guard", return_value=lambda _: None),
                patch.object(run_autopilot, "airframe_photos", return_value=({}, {})),
                patch.object(run_autopilot, "media_module", return_value=SimpleNamespace()),
                patch.object(run_autopilot, "release_basemaps", return_value=None),
                patch.object(public, "now_utc", return_value=NOW),
            ):
                result = run_autopilot.run(config)
        expected = {"reader", "policy", "publisher", "renderer", "deployer"}
        self.assertTrue(result["deployed"])
        self.assertEqual(set(made), expected)
        self.assertTrue(all(len(instances) == 1 for instances in made.values()))
        self.assertEqual(Counter(role for role, _ in used), Counter(dict.fromkeys(expected, 1)))
        for role, instance in used:
            self.assertIs(instance, made[role][0])

    def test_release_fingerprint_tracks_plugin_settings_and_installed_implementation(self):
        factory = Mock(side_effect=AssertionError("Fingerprint imported a plugin"))
        entries = {role: entry(role, factory) for role in ("renderer", "deployer")}
        snapshot = {"events": [], "as_of_utc": NOW}
        config = {"preview_ssh_host": "synthetic"}
        with (
            patch.object(plugins, "discover", side_effect=lambda role: {entries[role].name: entries[role]}),
            patch.object(run_autopilot, "source_digest", return_value="fixed-code"),
        ):
            initial = run_autopilot.release_fingerprint(snapshot, config)
            explicit = {**config, "plugins": {"renderer": {"plugin": "site-media", "settings": {}}}}
            self.assertEqual(initial, run_autopilot.release_fingerprint(snapshot, explicit))
            for role in entries:
                changed = {**config, "plugins": {role: {"settings": {"format": "new"}}}}
                self.assertNotEqual(initial, run_autopilot.release_fingerprint(snapshot, changed))
                entries[role].dist.version = "1.1"
                self.assertNotEqual(initial, run_autopilot.release_fingerprint(snapshot, config))
                entries[role].dist.version = "1.0"
                entries[role].value += "_replacement"
                self.assertNotEqual(initial, run_autopilot.release_fingerprint(snapshot, config))
                entries[role].value = f"synthetic_plugins:{role}"
                entries[role].dist.metadata["Name"] = "replacement-provider"
                self.assertNotEqual(initial, run_autopilot.release_fingerprint(snapshot, config))
                entries[role].dist.metadata["Name"] = "synthetic-plugins"
            self.assertEqual(initial, run_autopilot.release_fingerprint(snapshot, config))
        factory.assert_not_called()

    def test_builtin_transport_preserves_story_and_research_request_limits(self):
        writer = plugins.OpenAIWriter({})
        with patch.object(explain, "PUBLIC_TIMEOUT", 420), patch.object(explain, "request") as send:
            writer.send(b'{"text":{},"tools":[]}', "synthetic-key")
            self.assertEqual(
                send.call_args.kwargs, {"timeout": 420, "max_bytes": public_writer.MAX_RESPONSE_BYTES}
            )
            writer.send(b'{"tools":[{"type":"web_search"}]}', "synthetic-key")
            self.assertEqual(send.call_args.kwargs, {"timeout": weekly_research.REQUEST_TIMEOUT})

    def test_configured_transport_handles_story_type_and_weekly_requests_with_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            database = state / "budget.sqlite3"
            budget_store.initialize(database)
            key = state / "key"
            key.write_text("synthetic-key")
            config = {"state_dir": str(state), "budget_database": str(database), "weekly_key_file": str(key)}
            ledger = public.new_ledger("2026-10-01")
            autopilot.stage(ledger, [candidate(1)], at=NOW)
            autopilot.publish(ledger, Ghost(), persist=lambda _: None, at=NOW)
            public.write_json_atomic(state / "ledger.json", ledger, private=True)
            calls = []

            def send(encoded, credential):
                self.assertEqual(credential, "synthetic-key")
                with budget_store.connect(database) as connection:
                    row = connection.execute("SELECT * FROM ai_runs ORDER BY id DESC LIMIT 1").fetchone()
                self.assertEqual((row["status"], row["purpose"]), ("running", "public"))
                self.assertGreater(row["reserved_cost"], 0)
                name = json.loads(encoded)["text"]["format"]["name"]
                calls.append(name)
                if name == "public_story":
                    return answer(good("N10001"), good("N10001", 1))
                if name == "weekly_research":
                    return response()
                self.assertEqual(name, "type_profile")
                profile = {
                    "facts": [{"text": TYPE_FACT, "source": "source-1"}],
                    "sources": [{"id": "source-1", **TYPE_SOURCE}],
                }
                return {
                    "status": "completed",
                    "usage": {"input_tokens": 1000, "output_tokens": 500},
                    "output": [
                        {
                            "type": "web_search_call",
                            "status": "completed",
                            "action": {"sources": [{"url": TYPE_SOURCE["url"]}]},
                        },
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": json.dumps(profile)}],
                        },
                    ],
                }

            factory = Mock(
                return_value=SimpleNamespace(descriptor=plugins.Descriptor(">=1,<2", "writer"), send=send)
            )
            provider = entry("writer", factory)
            with (
                patch.object(plugins, "discover", return_value={provider.name: provider}),
                patch.object(explain, "urlopen", side_effect=AssertionError("Unexpected network request")),
            ):
                result = public_writer.run(config, at=LATER, limit=1)
                self.assertEqual((result["researched"], result["written"]), (1, 1))
                factory.assert_called_once_with({})
                self.assertEqual(calls, ["type_profile", "public_story"])
                ledger, snapshot = ordinary()
                public.write_json_atomic(state / "ledger.json", ledger, private=True)
                public.write_json_atomic(state / "snapshot.json", snapshot, private=True)
                report = run_weekly.run(config, count=1, at=WEEKLY_NOW)
                self.assertEqual(report["attached"], 1)
            self.assertEqual(calls, ["type_profile", "public_story", "weekly_research"])
            self.assertEqual(factory.call_count, 2)
            with budget_store.connect(database) as connection:
                rows = connection.execute(
                    "SELECT status,reserved_cost,estimated_cost FROM ai_runs"
                ).fetchall()
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["status"] == "complete" and row["reserved_cost"] == 0 for row in rows))
            self.assertTrue(all(row["estimated_cost"] > 0 for row in rows))
