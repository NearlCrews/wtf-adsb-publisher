"""Saved ledger packets must project like packets regenerated from the same source.

publish() calls weekly_spotlight.check_source, which regenerates the packet from the
saved finding and compares the public evidence versions. A difference suspends a
live article, and frozen records are never restaged. The legacy packets in
public_legacy_packets.json were written by the evidence_packet code from before the
public-output overhaul (commit cc19976), from the synthetic sources stored beside
them: invented identities, with every position in the South Pacific frame the other
fixtures use.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher import stories

FIXTURES = json.loads((Path(__file__).with_name("public_legacy_packets.json")).read_text())
EDITORIAL = {
    "title": "Fixture",
    "summary": "Fixture summary.",
    "article_path": "/stories/fixture-3-oct-2026/",
}
# An archive source whose saved position had no altitude left no archive marker in
# its old packet. Its labels are now correctly projected as archived labels, so the
# regenerated evidence differs until `export_public_events.py migrate-evidence` adds
# the label provenance its finding implies (see test_public_migration.py).
CHANGED = {"heatmap_watch_type_no_altitude"}


def projections(name):
    item = FIXTURES[name]
    regenerated = stories.evidence_packet(
        item["source"], include_track=False, observation_interval=tuple(item["interval"])
    )
    return public._projection(item["legacy_packet"], EDITORIAL), public._projection(regenerated, EDITORIAL)


def rows(event):
    return {(item["key"], item["scope"]): item for item in event["measurements"]}


class LegacyPacketTests(unittest.TestCase):
    def test_saved_packets_keep_matching_their_regenerated_evidence(self):
        for name in sorted(set(FIXTURES) - CHANGED):
            with self.subTest(name=name):
                legacy, regenerated = projections(name)
                self.assertEqual(legacy, regenerated)

    def test_only_the_documented_archive_case_changes_evidence(self):
        legacy, regenerated = projections("heatmap_watch_type_no_altitude")
        self.assertNotEqual(legacy["evidence_version"], regenerated["evidence_version"])
        self.assertIn(("squawk", "archived-label"), rows(regenerated))
        self.assertNotIn("at_utc", rows(regenerated)[("squawk", "archived-label")])

    def test_absent_code_placeholder_never_becomes_a_reading(self):
        for name in ("heatmap_watch_type_0000", "heatmap_vintage"):
            legacy, regenerated = projections(name)
            for event in (legacy, regenerated):
                self.assertFalse(any(item["value"] == "0000" for item in event["measurements"]), name)
        packet = stories.evidence_packet(FIXTURES["heatmap_outlier_gs"]["source"], include_track=False)
        self.assertNotIn("0000", json.dumps(packet["surrounding_readings"]))

    def test_carried_archive_labels_have_no_position_time(self):
        for name in ("heatmap_watch_type_carried", "trace_watch_type", "heatmap_outlier_gs"):
            legacy, _ = projections(name)
            labels = {key: item for (key, scope), item in rows(legacy).items() if scope == "archived-label"}
            self.assertIn("squawk", labels, name)
            self.assertTrue(all("at_utc" not in item for item in labels.values()), name)
            self.assertNotIn(("squawk", "saved-report"), rows(legacy))
            self.assertIn(
                "The callsign or transponder code is an archived label carried between saved positions, "
                "not a reading at the position time.",
                legacy["limitations"],
            )
            self.assertIn(
                "Archive timestamps do not establish individual message freshness.", legacy["limitations"]
            )
        legacy, _ = projections("heatmap_watch_type_carried")
        self.assertEqual(rows(legacy)[("callsign", "archived-label")]["value"], "ZZZ2023")

    def test_live_codes_keep_their_saved_reading_time_and_status_none_is_omitted(self):
        legacy, _ = projections("live_special_7700")
        self.assertEqual(rows(legacy)[("squawk", "saved-report")]["value"], "7700")
        self.assertIn("at_utc", rows(legacy)[("squawk", "saved-report")])
        legacy, _ = projections("live_watchlist")
        self.assertNotIn(("emergency_status", "saved-report"), rows(legacy))
        self.assertIn(("squawk", "saved-report"), rows(legacy))

    def test_legacy_episode_cards_gain_peak_time_rule_row_and_caveats(self):
        legacy, _ = projections("live_rapid_descent")
        peak = rows(legacy)[("peak_rate_fpm", "peak-report")]
        self.assertEqual(
            peak["at_utc"],
            FIXTURES["live_rapid_descent"]["legacy_packet"]["maneuver"]["peak_at_utc"].replace("+00:00", "Z"),
        )
        self.assertEqual(rows(legacy)[("threshold_fpm", "detection-rule")]["value"], 4500)
        self.assertIn(public.NOT_EMERGENCY, legacy["limitations"])
        self.assertIn(public.SIGN_NOTE, legacy["limitations"])
        self.assertEqual(legacy["priority_evidence"]["value"], peak["value"])
        legacy, _ = projections("live_hovering")
        self.assertIn(public.MISSION, legacy["limitations"])
        self.assertEqual(rows(legacy)[("footprint_nm", "analysis-window")]["unit"], "NM")

    def test_legacy_watchlist_and_mlat_records_gain_their_caveats(self):
        legacy, _ = projections("live_watchlist")
        self.assertEqual(legacy["watchlist"]["category"], "Distinctive aircraft")
        self.assertEqual(legacy["watchlist"]["licenses"], public.WATCHLIST_LICENSES)
        self.assertIn(public.WATCHLIST_SCOPE, legacy["limitations"])
        jump, _ = projections("live_position_jump")
        self.assertIn(
            "The value comes from a multilateration position solution, not a field the aircraft transmitted.",
            jump["limitations"],
        )
        self.assertEqual(
            rows(jump)[("displacement_nm", "discontinuity-pair-not-physical-travel")]["unit"], "NM"
        )


if __name__ == "__main__":
    unittest.main()
