"""Story packets: the newer evidence blocks, their rejections, and each kind's headline measurements."""

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
import story_fixtures as fixtures

from wtf_adsb_publisher import episode_evidence, kinds, stories

WINDS = {
    "day": "2026-11-05",
    "east_addresses": 35,
    "west_addresses": 33,
    "east_median_kn": 560.0,
    "west_median_kn": 380.0,
    "east_quartiles_kn": [540.0, 575.0],
    "west_quartiles_kn": [360.0, 395.0],
    "asymmetry_kn": 180,
    "altitude_floor_ft": 28000,
    "evaluated_at": 1.0,
}
FOREIGN = {"country": "DE", "country_name": "Germany", "basis": ["dbFlags"], "registration_country": None}
UNLISTED = {"callsign": "TEST01", "category": "A2", "source": "adsb_icao"}


def packet(kind, **evidence):
    return fixtures.packet(fixtures.event(kind, **evidence))


class NewEvidenceValidates(unittest.TestCase):
    def test_winds_aloft_block_is_accepted(self):
        block = stories.winds_aloft(WINDS)
        self.assertEqual(
            block,
            {key: value for key, value in WINDS.items() if key != "evaluated_at"},
        )
        self.assertEqual(packet("winds_aloft_day", winds_aloft=WINDS)["winds_aloft"], block)

    def test_winds_aloft_keeps_only_an_open_meteo_wind(self):
        wind = {"speed_kn": 140.0, "direction_deg": 260.0, "source": "open-meteo", "hour": "2026-11-05T12:00"}
        self.assertEqual(stories.winds_aloft({**WINDS, "wind_250_hpa": wind})["wind_250_hpa"], wind)
        for bad in ({**wind, "source": "nws"}, {**wind, "direction_deg": 400}, {**wind, "hour": "noon"}):
            with self.subTest(wind=bad):
                self.assertNotIn("wind_250_hpa", stories.winds_aloft({**WINDS, "wind_250_hpa": bad}))

    def test_winds_aloft_rejects_a_malformed_or_inconsistent_block(self):
        for change in (
            {"day": "5 November"},
            {"east_addresses": -1},
            {"west_addresses": 2.5},
            {"east_median_kn": "fast"},
            {"west_median_kn": None},
            {"east_quartiles_kn": [575.0, 540.0]},
            {"west_quartiles_kn": [360.0]},
            {"asymmetry_kn": 250},
            {"altitude_floor_ft": 0},
        ):
            with self.subTest(change=change):
                self.assertIsNone(stories.winds_aloft({**WINDS, **change}))
        self.assertNotIn("winds_aloft", packet("winds_aloft_day", winds_aloft={**WINDS, "day": None}))
        # The block belongs to its own kind only.
        self.assertNotIn("winds_aloft", packet("formation_flight", winds_aloft=WINDS))

    def test_unknown_basis_is_rejected(self):
        self.assertEqual(stories.foreign_military(FOREIGN), FOREIGN)
        evidence = {**FOREIGN, "basis": ["flag"]}
        self.assertIsNone(stories.foreign_military(evidence))
        self.assertNotIn("foreign_military", packet("foreign_military", foreign_military=evidence))
        both = {**FOREIGN, "basis": ["dbFlags", "military_allocation"], "registration_country": "DE"}
        self.assertEqual(packet("foreign_military", foreign_military=both)["foreign_military"], both)
        for change in (
            {"basis": []},
            {"basis": ["dbFlags", "dbFlags"]},
            {"basis": "dbFlags"},
            {"country": "Germany"},
            {"country_name": "<b>Germany</b>"},
            {"registration_country": "de"},
        ):
            with self.subTest(change=change):
                self.assertIsNone(stories.foreign_military({**FOREIGN, **change}))

    def test_unregistered_block_is_accepted_and_checked(self):
        self.assertEqual(packet("unregistered_address", unregistered=UNLISTED)["unregistered"], UNLISTED)
        self.assertEqual(
            stories.unregistered({"callsign": None, "category": None, "source": "adsb_icao"}),
            {"callsign": None, "category": None, "source": "adsb_icao"},
        )
        self.assertEqual(stories.unregistered({**UNLISTED, "category": "Z9"})["category"], None)
        self.assertIsNone(stories.unregistered({**UNLISTED, "source": "radar"}))
        self.assertNotIn("unregistered", packet("unregistered_address", unregistered=[]))

    def test_an_operator_first_sighting_carries_its_designator_and_operator(self):
        raw = {"scope": "operator", "designator": "DLH", "operator": "Lufthansa", "coverage_days": 91}
        self.assertEqual(
            stories.first_sighting({**raw, "since": "2026-08-01"}),
            {
                "scope": "operator",
                "coverage_days": 91,
                "since": "2026-08-01",
                "designator": "DLH",
                "operator": "Lufthansa",
            },
        )
        self.assertEqual(
            packet("first_operator_here", first_sighting=raw)["first_sighting"]["designator"], "DLH"
        )
        self.assertIsNone(stories.first_sighting({**raw, "designator": "dlh1"}))
        self.assertIsNone(stories.first_sighting({**raw, "scope": "airline"}))
        # A type scope keeps its earlier shape.
        self.assertEqual(
            stories.first_sighting({"scope": "type", "coverage_days": 3}),
            {"scope": "type", "coverage_days": 3, "since": None},
        )

    def test_new_metric_keys_and_the_basis_survive(self):
        maneuver = {
            "span_nm": 1.8,
            "max_turn_rate_dps": 9.5,
            "basis": "turn_rate",
            "legs": 4,
            "leg_length_nm": 12.0,
            "axis_heading_deg": 90.0,
            "lateral_spacing_nm": 6.0,
            "line_repeat_nm": 0.4,
            "duration_s": 3420,
            "runway_within_nm": 50.0,
        }
        summary = episode_evidence.summary(maneuver)
        self.assertEqual({key: summary[key] for key in maneuver}, maneuver)
        self.assertNotIn("basis", episode_evidence.summary({**maneuver, "basis": "rumour"}))
        pattern = stories.pattern_summary({key: value for key, value in maneuver.items() if key != "basis"})
        self.assertEqual(pattern["legs"], 4)
        self.assertEqual(pattern["runway_within_nm"], 50.0)

    def test_new_context_tags_have_notes(self):
        for tag in (
            "registry_gap",
            "tanker_type",
            "military_flag",
            "airport_context",
            "airport_maneuver_hypothesis",
        ):
            with self.subTest(tag=tag):
                self.assertEqual(episode_evidence.tags({"context_tags": [tag, "unknown"]}), [tag])

    def test_a_participant_keeps_its_median_ground_speed(self):
        evidence = deepcopy(fixtures.event("winds_aloft_day")["evidence"])
        evidence["participants"] = [
            {"hex": "a00001", "role": "member", "measurements": {"median_gs_kn": 545.0, "private": 1}}
        ]
        (member,) = stories.participants(evidence)
        self.assertEqual(member["measurements"], {"median_gs_kn": 545.0})


class HeadlineMeasurements(unittest.TestCase):
    def test_headline_measurements_are_at_most_five_known_keys(self):
        for kind, keys in stories.HEADLINE_MEASUREMENTS.items():
            with self.subTest(kind=kind):
                self.assertLessEqual(len(keys), 5, kind)
                self.assertIn(kind, kinds.KINDS)
                self.assertEqual(len(keys), len(set(keys)))

    def test_every_public_kind_has_a_list_in_registry_order(self):
        self.assertEqual(list(stories.HEADLINE_MEASUREMENTS), list(kinds.table("section")))

    def test_the_named_picks_and_the_reading_rule(self):
        table = stories.HEADLINE_MEASUREMENTS
        self.assertEqual(
            table["rapid_descent"],
            ("peak_rate_fpm", "altitude_change_ft", "duration_seconds", "gs", "alt_baro"),
        )
        self.assertEqual(
            table["formation_flight"],
            (
                "min_separation_nm",
                "vertical_separation_ft",
                "participant_count",
                "duration_seconds",
                "alt_baro",
            ),
        )
        self.assertEqual(
            table["winds_aloft_day"],
            ("asymmetry_kn", "east_median_kn", "west_median_kn", "east_addresses", "west_addresses"),
        )
        self.assertEqual(table["gnss_integrity_loss"], ("nic_baro", "sil", "gva", "sda", "version"))
        self.assertEqual(table["selected_altitude_overshoot"], ("nav_altitude_mcp", "nav_qnh"))
        self.assertEqual(table["circling"], ("gs", "alt_baro"))


if __name__ == "__main__":
    unittest.main()
