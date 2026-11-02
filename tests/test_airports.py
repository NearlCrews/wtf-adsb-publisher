"""Nearest public airport from an aircraft's own positions; no receiver value enters."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from wtf_adsb_publisher import airports, autopilot

RUNWAYS = [
    {
        "airport_ident": "KOZW",
        "airport_name": "Livingston County Spencer J. Hardy Airport",
        "runway": "13/31",
        "ends": [[-83.985, 42.635], [-83.975, 42.622]],
    },
    {
        "airport_ident": "45G",
        "airport_name": "Brighton Airport",
        "runway": "04/22",
        "ends": [[-83.800, 42.570], [-83.795, 42.575]],
    },
    {
        "airport_ident": "MI45",
        "airport_name": "Beam Farms Airport",
        "runway": "09/27",
        "ends": [[-83.900, 42.600], [-83.895, 42.600]],
    },
    {
        "airport_ident": "CYXX",
        "airport_name": "Northern Airport",
        "runway": "09/27",
        "ends": [[-80.000, 49.500], [-79.990, 49.500]],
    },
]


def point(lat, lon):
    return {"lat": lat, "lon": lon}


class Mocked(unittest.TestCase):
    """The runway data, its coverage and its availability, replaced by RUNWAYS."""

    def setUp(self):
        airports._airports.cache_clear()
        patcher = mock.patch.object(airports.airport_context, "runway_data", return_value=RUNWAYS)
        patcher.start()
        self.addCleanup(patcher.stop)
        available = mock.patch.object(airports.airport_context, "available", return_value=True)
        available.start()
        self.addCleanup(available.stop)
        bounds = mock.patch.object(
            airports.airport_context, "coverage", return_value=(-93.0, 38.0, -74.0, 50.0)
        )
        bounds.start()
        self.addCleanup(bounds.stop)


class Nearest(Mocked):
    def test_the_closest_public_runway_wins_and_private_strips_are_ignored(self):
        found = airports.nearest([point(42.70, -83.90), point(42.6285, -83.980), point(42.60, -83.90)])
        self.assertEqual(found["ident"], "KOZW")
        self.assertEqual(found["at"], 1)
        self.assertLess(found["nm"], 0.3)

    def test_distance_is_to_the_segment_not_its_ends(self):
        beside_middle = airports.nearest([point(42.6285, -83.980)])
        beside_end = airports.nearest([point(42.635, -83.985)])
        self.assertLess(beside_middle["nm"], 0.3)
        self.assertLess(beside_end["nm"], 0.1)

    def test_a_runway_east_or_west_at_the_northern_edge_is_still_found(self):
        # At 49.5 north the runway's ends lie 0.23 and 0.22 degrees west: about 9.0 and 8.6 NM.
        found = airports.nearest([point(49.5, -79.77)])
        self.assertEqual(found["ident"], "CYXX")
        self.assertAlmostEqual(found["nm"], 8.6, delta=0.1)

    def test_ten_statute_miles_is_the_cut_off_inside_the_box(self):
        # Due north of KOZW's northern runway end: 0.15 degrees is 9.0 NM, within BOX but past MAX_NM.
        self.assertIsNone(airports.nearest([point(42.785, -83.985)]))
        found = airports.nearest([point(42.635 + 8.6 / 60, -83.985)])
        self.assertEqual((found["ident"], found["nm"]), ("KOZW", 8.6))

    def test_distance_to_one_airport_is_to_its_nearest_runway(self):
        self.assertLess(airports.distance_nm("KOZW", point(42.6285, -83.980)), 0.3)
        self.assertIsNone(airports.distance_nm("MI45", point(42.6285, -83.980)))
        self.assertIsNone(airports.distance_nm("KOZW", {"lat": None}))

    def test_too_far_outside_coverage_or_excluded_gives_none(self):
        self.assertIsNone(airports.nearest([point(43.5, -83.9)]))
        self.assertIsNone(airports.nearest([point(42.6285, -70.0)]))
        self.assertIsNone(airports.nearest([point(42.6285, -83.980)], exclude={"KOZW", "45G"}))
        self.assertIsNone(airports.nearest([]))
        self.assertIsNone(airports.nearest([{"lat": "x", "lon": None}]))

    def test_four_letters_or_any_three_characters_are_public_use(self):
        # An ICAO ident, or an FAA location identifier of three characters, digits included.
        for ident in ("KOZW", "CYXX", "OZW", "45G", "Y47", "3CK"):
            with self.subTest(ident=ident):
                self.assertTrue(airports.PUBLIC_IDENT.fullmatch(ident))
        # Four characters with a digit is the FAA's private-use pattern.
        for ident in ("MI45", "0MI3", "7MI5", "K45G", "45", "OZ", "KOZWX", "ozw", "4-5G"):
            with self.subTest(ident=ident):
                self.assertFalse(airports.PUBLIC_IDENT.fullmatch(ident))

    def test_a_public_field_with_a_digit_in_its_ident_is_found_and_a_private_strip_is_not(self):
        found = airports.nearest([point(42.70, -83.70), point(42.5725, -83.7975), point(42.45, -83.70)])
        self.assertEqual((found["ident"], found["name"]), ("45G", "Brighton Airport"))
        self.assertIsNone(airports.distance_nm("MI45", point(42.600, -83.8975)))
        self.assertNotIn("MI45", {ident for _nm, ident, _at in airports.ranked([point(42.600, -83.8975)])})


def on_runway(share):
    """A position on KOZW's runway centreline, `share` of the way from its first end."""
    return point(42.635 - 0.013 * share, -83.985 + 0.01 * share)


class TrackEnds(Mocked):
    """An airport the track starts or ends at is never named, however its roll is sampled."""

    def test_a_track_that_rolls_out_or_starts_its_roll_on_the_runway_names_no_airport(self):
        approach = [point(42.75, -84.10), point(42.70, -84.05), point(42.66, -84.01)]
        landing = [*approach, on_runway(0.2), on_runway(0.5), on_runway(0.8)]
        self.assertNotIn(airports.nearest(landing)["at"], {0, len(landing) - 1})
        self.assertIsNone(autopilot._airport(landing, "circling"))
        self.assertIsNone(autopilot._airport(list(reversed(landing)), "circling"))

    def test_a_track_that_passes_the_airport_midway_still_names_it(self):
        passing = [point(42.70, -84.05), point(42.66, -84.01), point(42.6285, -83.980), point(42.59, -83.94)]
        passing.append(point(42.55, -83.90))
        found = autopilot._airport(passing, "circling")
        self.assertEqual(found["ident"], "KOZW")

    def test_a_field_named_for_a_family_gives_way_to_the_next_airport(self):
        # Over KOZW's runway, then about 0.6 NM from 45G's.
        passing = [
            point(42.70, -84.10),
            point(42.6285, -83.980),
            point(42.585, -83.800),
            point(42.50, -83.60),
        ]
        self.assertEqual(autopilot._airport(passing, "circling")["ident"], "KOZW")
        # A family's name with no public body or town in it (story_facts.airport_name).
        renamed = [dict(RUNWAYS[0], airport_name="Gross Airport"), *RUNWAYS[1:]]
        with mock.patch.object(airports.airport_context, "runway_data", return_value=renamed):
            airports._airports.cache_clear()
            self.addCleanup(airports._airports.cache_clear)
            self.assertEqual(autopilot._airport(passing, "circling")["ident"], "45G")

    def test_answers_are_kept_for_the_latest_tracks_and_handed_out_as_copies(self):
        passing = [point(42.70, -84.05), point(42.66, -84.01), point(42.6285, -83.980), point(42.59, -83.94)]
        passing.append(point(42.55, -83.90))
        autopilot._airport(passing, "circling")["ident"] = "changed"
        self.assertEqual(autopilot._airport(passing, "circling")["ident"], "KOZW")
        # Bounded: once AIRPORT_ANSWERS later tracks are asked about, the first is found afresh.
        for index in range(autopilot.AIRPORT_ANSWERS):
            autopilot._airport([point(45 + index / 10000, -84.0), point(45.0, -84.1)], "circling")
        self.assertEqual(autopilot._airport_answer.cache_info().currsize, autopilot.AIRPORT_ANSWERS)
        misses = autopilot._airport_answer.cache_info().misses
        self.assertEqual(autopilot._airport(passing, "circling")["ident"], "KOZW")
        self.assertEqual(autopilot._airport_answer.cache_info().misses, misses + 1)
