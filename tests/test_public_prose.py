"""Deterministic evidence checks for AI-written public prose; no paid calls."""

import re
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reference_fixture
import story_fixtures

from wtf_adsb_publisher import editorial, explain, public_prose, publication, stories, story_facts
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher.public_prose import MAX_PROSE, packet_facts, prose_issues

NOW = 1_790_000_000  # 2026-09-21T14:13:20Z, a Monday


def setUpModule():
    reference_fixture.install()


def descent_event():
    stamps = [NOW - 600 + index * 10 for index in range(10)]
    return {
        "id": 3,
        "hex": "a54321",
        "kind": "rapid_descent",
        "category": "performance",
        "title": "Rapid descent reports",
        "value": -7600,
        "unit": "ft/min",
        "first_seen": stamps[0],
        "last_seen": stamps[-1],
        "hits": 7,
        "origin": "Live receiver",
        "evidence": {
            "aircraft": {
                "hex": "a54321",
                "r": "N54321",
                "t": "BE20",
                "desc": "BEECH 200 Super King Air",
                "type": "adsb_icao",
                "alt_baro": 15000,
                "baro_rate": -7600,
                "gs": 300,
                "squawk": "1200",
                "emergency": "none",
                "flight": "N54321",
            },
            "observed_at": stamps[2],
            "watchlist": {},
            "maneuver": {
                "confirmed": True,
                "quality_review_required": False,
                "quality_flags": [],
                "started_at": stamps[0],
                "ended_at": stamps[-1],
                "peak_at": stamps[2],
                "duration_seconds": 90,
                "sample_count": 10,
                "peak_rate_fpm": -7600,
                "median_rate_fpm": -7600,
                "average_rate_fpm": -6900,
                "threshold_fpm": 4500,
                "altitude_change_ft": -10350,
                "altitude_reference": "alt_baro",
                "state": "recovered",
            },
        },
    }


def watchlist_event():
    commit = "c" * 40
    return {
        "id": 4,
        "hex": "a00007",
        "kind": "watchlist_match",
        "category": "airframes",
        "title": "Watchlist",
        "value": None,
        "unit": "",
        "first_seen": NOW - 900,
        "last_seen": NOW - 60,
        "hits": 4,
        "evidence": {
            "aircraft": {
                "hex": "a00007",
                "r": "N729KF",
                "t": "GLF6",
                "desc": "GULFSTREAM G650",
                "type": "adsb_icao",
                "flight": "N729KF",
                "gs": 455.0,
                "alt_baro": 41000,
            },
            "observed_at": NOW - 60,
            "watchlist": {
                "hex": "a00007",
                "public_claim_allowed": True,
                "source_registration": "N729KF",
                "association": "Jane Example",
                "neutral_category": "Public figure association",
                "source": {
                    "url": "https://raw.githubusercontent.com/sdr-enthusiasts/plane-alert-db/"
                    + commit
                    + "/plane-alert-db.csv",
                    "commit": commit,
                    "source_date": "2026-09-01T00:00:00Z",
                    "fetched_at": "2026-09-02T00:00:00Z",
                },
            },
        },
    }


def circling_event():
    return {
        "id": 5,
        "hex": "a00008",
        "kind": "circling",
        "category": "patterns",
        "title": "Circling",
        "value": 12,
        "unit": "minutes",
        "first_seen": NOW - 720,
        "last_seen": NOW,
        "hits": 20,
        "evidence": {
            "aircraft": {
                "hex": "a00008",
                "r": "N12345",
                "t": "B407",
                "desc": "BELL 407",
                "type": "adsb_icao",
            },
            "observed_at": NOW,
            "watchlist": {},
            "pattern": {
                "started_at": NOW - 600,
                "ended_at": NOW,
                "duration_seconds": 600,
                "sample_count": 46,
                "closed_loops": 3,
                "observed_sources": ["adsb_icao"],
                "quality_review_required": False,
                "shape_verified": True,
            },
        },
    }


def speed_event():
    """A speed outlier with neighboring readings 15 seconds apart."""
    samples = [
        {"ts": NOW - 45, "gs": 94.6, "archive_alt": 1900},
        {"ts": NOW - 30, "gs": 66.7, "archive_alt": 1950},
        {"ts": NOW - 15, "gs": 82.7, "archive_alt": 2000},
        {"ts": NOW, "gs": 1201.0, "archive_alt": 2000},
        {"ts": NOW + 15, "gs": 137.4, "archive_alt": 2025},
    ]
    return {
        "id": 6,
        "hex": "a00009",
        "kind": "outlier_gs",
        "category": "performance",
        "title": "Speed outlier",
        "value": 1201.0,
        "unit": "kn",
        "first_seen": NOW - 45,
        "last_seen": NOW + 15,
        "hits": 2,
        "origin": "Saved compact replay",
        "evidence": {
            "aircraft": {
                "hex": "a00009",
                "r": "N2867Q",
                "t": "P28A",
                "desc": "PIPER PA-28-140/150/160",
                "year": "1966",
                "type": "mlat",
                "gs": 1201.0,
                "archive_alt": 2000,
            },
            "observed_at": NOW,
            "watchlist": {},
            "context_samples": samples,
        },
    }


def watched():
    """A related finding's watchlist block, as story_writer adds one."""
    return {"watchlist": packet(watchlist_event())["watchlist"]}


def packet(event):
    return stories.evidence_packet(event, include_track=False)


class ProseTests(unittest.TestCase):
    def setUp(self):
        self.packet = packet(descent_event())

    def assertPasses(self, text, evidence=None):
        self.assertEqual(prose_issues(text, evidence or self.packet), [], text)

    def assertFails(self, text, fragment, evidence=None):
        issues = prose_issues(text, evidence or self.packet)
        self.assertTrue(any(fragment in issue for issue in issues), (text, issues))


class AdversarialTests(ProseTests):
    def test_review_adversarial_cases_are_rejected(self):
        self.assertFails("The aircraft descended at 25,000 ft/min.", "Unsupported quantity: 25,000 ft/min")
        self.assertFails("It was 14 nm from our receiver.", "Receiver-relative")
        self.assertFails("It was 14 nm from our receiver.", "Unsupported quantity: 14 nm")
        self.assertFails("The aircraft is owned by John Smith.", "Unhedged owner claim")
        self.assertFails("John Smith's jet descended quickly.", "Unhedged owner claim")
        self.assertFails("A hijacking was under way.", "Unhedged hijacking claim")
        self.assertFails("It reached Mach 1.4.", "Unsupported Mach number")
        self.assertEqual(prose_issues("N54321 descended at 7,600 ft/min.", self.packet), [])

    def test_fabrications_are_rejected(self):
        descent, watched, circling = self.packet, packet(watchlist_event()), packet(circling_event())
        for evidence, text in (
            (descent, "According to the FAA registry, the aircraft is owned by Acme Aviation LLC."),
            (descent, "Acme Aviation LLC owns this aircraft."),
            (descent, "Taylor Swift's Gulfstream descended at 7,600 ft/min."),
            (descent, "The jet, linked to Taylor Swift, descended quickly."),
            (descent, "The owner is John Smith, though the descent may reflect routine flying."),
            (watched, "Plane Alert DB lists Jane Example; it is her private jet."),
            (watched, "Plane Alert DB lists the jet under Jane Example, who flew it to Miami."),
            (watched, "Plane Alert DB lists Jane Example, who was onboard."),
            (watched, "Plane Alert DB lists Jane Example. Example flew it to Miami."),
            (watched, "Jane Example, long associated with this jet, flew it to Miami."),
            (descent, "John Smith was aboard and it is unknown why the aircraft descended."),
            (descent, "It passed 3 NM from the station."),
            (descent, "It passed close to the station."),
            (descent, "It passed within a few miles of us."),
            (descent, "It appeared to our north."),
            (descent, "It descended a few miles from where these reports were collected."),
            (descent, "We heard it 14 NM out."),
            (descent, "Its closest approach was 3 NM."),
            (descent, "It dropped out of reception."),
            (descent, "It sank below the radio horizon."),
            (descent, "The SDR dongle picked it up."),
            (descent, "It was in our vicinity."),
            (descent, "It passed the local airport."),
            (descent, "It passed at 14:03 Eastern Daylight Time."),
            (descent, "It descended at twenty-five thousand feet per minute."),
            (descent, "It flew at nine hundred knots."),
            (descent, "The King Air has two engines."),
            (descent, "It was its seventh descent of the day."),
            (descent, "Two-thirds of the reports exceeded the threshold."),
            (descent, "The peak was triple the threshold."),
            (descent, "It climbed to 20 thousand."),
            (descent, "The aircraft pulled 9 g."),
            (descent, "It burned 300 gallons of fuel."),
            (descent, "The jet cost $20 million."),
            (descent, "It reached Mach .84."),
            (descent, "It was within .5 NM."),
            (circling, "The helicopter circled 46 times."),
            (circling, "The helicopter circled 20 times."),
            (descent, "The reports were saved on 9/20/2026."),
            (descent, "The reports were saved on 20.09.2026."),
            (descent, "The reports were saved on the 20th."),
            (descent, "It happened in August."),
            (descent, "It happened yesterday."),
            (descent, "It happened 3 days ago."),
            (descent, "It happened at noon."),
            (descent, "It happened at three o'clock."),
            (descent, "It happened in the morning."),
            (descent, "The aircraft went transonic."),
            (descent, "It approached the speed of sound."),
            (descent, "The King Air climbed 10,350 feet in 90 seconds."),
            (descent, "It gained 10,350 ft."),
            (descent, "It descended at 4,500 ft/min."),
            (descent, "The descent rate exceeded 8,000 ft/min."),
            (descent, "The descent never exceeded 7,000 ft/min."),
            (descent, "The aircraft lost control and plummeted toward the ground."),
            (descent, "It lost cabin pressure."),
            (descent, "It suffered a radio failure."),
            (descent, "Armed men seized the aircraft."),
            (descent, "The aircraft had been stolen."),
            (descent, "It was tracking a suspect."),
            (descent, "It was on a search and rescue flight."),
            (descent, "It was headed to Washington."),
            (descent, "The jet was hijacked and its destination is unknown."),
            (descent, "The steep descent on 21 September 2026 indicated an emergency."),
            (descent, "Squawking 1200, the aircraft signalled an emergency."),
            (descent, "It broadcast 7600, signalling radio failure."),
            (descent, "The hi​jacking was real."),
            (descent, "The aircraft was hijаcked."),
            (descent, "See flightradar24.com or mail a@b.co for details."),
        ):
            with self.subTest(text=text):
                self.assertTrue(prose_issues(text, evidence), text)

    def test_second_review_bypasses_are_rejected(self):
        code = descent_event()
        code["evidence"]["aircraft"].update(squawk="7700", emergency="general")
        coded = packet(code)
        grouped = dict(self.packet, related_findings=[{"kind": "watchlist_match", **watched()}])
        circling, watchlisted, speed = (
            packet(circling_event()),
            packet(watchlist_event()),
            packet(speed_event()),
        )
        for evidence, text in (
            (coded, "The 7700 code confirms the emergency."),
            (coded, "Squawking 7700 proves the aircraft had an emergency."),
            (grouped, "Jane Example's jet descended at 7,600 ft/min."),
            (grouped, "Jane Example flew the King Air."),
            (watchlisted, "Example's Gulfstream cruised at 41,000 ft."),
            (watchlisted, "Jane flew past at 41,000 ft."),
            (watchlisted, "Plane Alert DB links the jet to Jane Example. She was in it."),
            (watchlisted, "Plane Alert DB lists Jane Example, who rode in it."),
            (watchlisted, "Jane Example, listed by Plane Alert DB, was flying the jet."),
            (watchlisted, "Plane Alert DB says Jane Example owns the jet."),
            (self.packet, "This was no ordinary descent, it was an emergency."),
            (self.packet, "Without question, the descent was an emergency."),
            (self.packet, "As anyone could see, the plane was in distress."),
            (self.packet, "The steep descent suggests a medical emergency."),
            (self.packet, "Records cannot show who was aboard when it crashed."),
            (self.packet, "Possibility: none; the crew declared an emergency."),
            (circling, "The helicopter returned to the area 20 times."),
            (self.packet, "It descended 7 times."),
            (self.packet, "It made no fewer than 10 landings."),
            (self.packet, "The two jets nearly collided."),
            (circling, "The two loops were tight."),
            (self.packet, "A second aircraft followed it down."),
            (self.packet, "It descended nearly 9,500 ft/min."),
            (self.packet, "Its ground speed was close to 370 knots."),
            (self.packet, "The track ended when the aircraft dropped below the horizon."),
            (self.packet, "The signal faded."),
            (self.packet, "The site saw it first."),
            (self.packet, "The aircraft passed a mile away."),
            (self.packet, "It was due west."),
            (self.packet, "It flew after dark."),
            (self.packet, "It was on final approach."),
            (self.packet, "It was a charter flight."),
            (self.packet, "The governor was on the jet."),
            (self.packet, "It ran out of fuel."),
            (speed, "The engine failed 15 seconds later."),
            (self.packet, "The wind pushed it along."),
            (self.packet, "Its indicated speed was 300 knots."),
            (self.packet, "It was instructed to descend."),
            (self.packet, "It set a record."),
            (self.packet, "The jet descended \u202e.dekcajih saw tI\u202c"),
            (self.packet, "Its ground speed was 7 at the peak."),
            (self.packet, "The jet was built in 2026."),
            (self.packet, "Its 200 seats were empty."),
            (self.packet, "WE tracked it."),
            (self.packet, "Let's look at the descent."),
            (self.packet, "It descended at 2 p.m."),
        ):
            with self.subTest(text=text):
                self.assertTrue(prose_issues(text, evidence), text)
        for evidence, text in (
            (coded, "The transponder broadcast 7700, the code for a general emergency."),
            (coded, "Its transponder showed 7700, which pilots use to declare an emergency."),
            (coded, "The aircraft reported the general emergency status."),
            (self.packet, "Pilots sometimes descend steeply for ordinary reasons."),
            (self.packet, "Nobody declared an emergency in these reports."),
            (self.packet, "The episode lasted one minute and 30 seconds."),
            (self.packet, "The descent peaked at 14:03 UTC."),
        ):
            with self.subTest(text=text):
                self.assertEqual(prose_issues(text, evidence), [], text)

    def test_verification_bypasses_are_rejected(self):
        code = descent_event()
        code["evidence"]["aircraft"].update(squawk="7700", emergency="general")
        coded = packet(code)
        tagged = descent_event()
        tagged["evidence"]["maneuver"]["context_tags"] = [
            "emergency_descent_profile",
            "airport_descent_context",
        ]
        tagged["evidence"]["maneuver"]["quality_flags"] = ["rate_field_conflict", "uncorroborated_rate"]
        caveats = packet(tagged)
        patrol = descent_event()
        patrol["evidence"]["aircraft"].update(t="P8", desc="BOEING P-8A Poseidon maritime patrol aircraft")
        patrolling = packet(patrol)
        watchlisted, speed = packet(watchlist_event()), packet(speed_event())
        for evidence, text in (
            (coded, "It squawked 7700, the code for a general emergency, which is exactly what happened."),
            (watchlisted, "Plane Alert DB lists Jane Example. She flew in it."),
            (watchlisted, "Her G650 cruised at 41,000 ft."),
            (watchlisted, "Plane Alert DB lists Jane Example, whose jet it is."),
            (self.packet, "There is no doubt it was an emergency."),
            (self.packet, "These records cannot show why the jet crashed."),
            (self.packet, "Records cannot show who was aboard when N54321 crashed."),
            (self.packet, "The crew, who may have been tired, declared an emergency."),
            (self.packet, "Another aircraft followed it down."),
            (self.packet, "It flew right above the site."),
            (self.packet, "It passed a few miles to the west."),
            (self.packet, "The signal was strong."),
            (self.packet, "It came in to land."),
            (self.packet, "Both engines flamed out."),
            (self.packet, "It held 300 knots indicated."),
            (self.packet, "It was sold in 2026."),
            (self.packet, "The 1200 readings were all high."),
            (self.packet, "It descended at nearly 10,000 ft/min."),
            (speed, "Its ground speed was nearly 2,000 knots."),
            (self.packet, "Controllers cleared it to land."),
            (self.packet, "It crashed and nobody survived."),
            (caveats, "The descent began at 25,000 ft and levelled off at 8,000 ft."),
            (caveats, "Two directly reported vertical rates disagreed by 1,500 ft/min."),
            (caveats, "A high vertical-rate report was matched by a neighbouring report within 25%."),
            (patrolling, "It was on a maritime patrol."),
        ):
            with self.subTest(text=text):
                self.assertTrue(prose_issues(text, evidence), text)
        for evidence, text in (
            (self.packet, "A descent this steep can happen when a pilot needs to lose height quickly."),
            (speed, "The peak came 15 seconds after the previous reading."),
            (self.packet, "The descent rate met the threshold in 7 of 10 reports."),
            (coded, "Code 7700 has long been reserved for emergencies."),
            (self.packet, "The address may belong to an aircraft correctly classified in the database."),
            (caveats, "The descent began above 25,000 ft and levelled off between 8,000 and 15,000 ft."),
            (patrolling, "The P-8A Poseidon is a maritime patrol aircraft."),
            (self.packet, "The recorded emergency status was none."),
        ):
            with self.subTest(text=text):
                self.assertEqual(prose_issues(text, evidence), [], text)
        named = packet(watchlist_event())
        named["watchlist"]["association_label"] = "Taylor Swift"
        self.assertEqual(prose_issues("The descent was swift.", named), [])
        self.assertTrue(prose_issues("Swift's jet descended.", named))

    def test_restated_caveats_may_repeat_their_own_figures(self):
        event = descent_event()
        event["evidence"]["maneuver"]["context_tags"] = ["emergency_descent_profile"]
        evidence = packet(event)
        note = evidence["maneuver"]["context_notes"][0]
        self.assertPasses(note, evidence)
        self.assertPasses(
            "The descent began above 25,000 ft and levelled off between 8,000 and 15,000 ft.", evidence
        )
        self.assertFails("The aircraft descended through 25,000 ft.", "Unsupported quantity", evidence)
        watchlisted = packet(watchlist_event())
        self.assertPasses(watchlisted["watchlist"]["attribution"], watchlisted)


class NumberTests(ProseTests):
    def test_numbers_match_packet_values_with_stated_rounding_and_conversions(self):
        for text in (
            "N54321 descended at 7,600 ft/min.",
            "The descent peaked near 7,600 feet per minute, about 2,300 m/min.",
            "The descent peaked at roughly 39 m/s.",
            "Pressure altitude fell 10,350 ft, or about 3,150 m, in 90 seconds.",
            "It lost about 10,000 ft in a minute and a half.",
            "The episode lasted 1.5 minutes across 10 telemetry samples.",
            "The episode lasted 1 minute 30 seconds.",
            "The episode lasted 1½ minutes.",
            "Seven consecutive reports exceeded the site's 4,500 ft/min detection threshold.",
            "The aircraft flew at 300 knots, about 345 mph or 556 km/h.",
            "It was at FL150 when the peak was reported.",
            "It lost more than 9,000 feet.",
            "The descent rate never exceeded 8,000 ft/min.",
            "The descent rate was in the range of 6,900 to 7,600 ft/min.",
            "The Beech 200 Super King Air transmitted 1200 throughout.",
            "Its pressure altitude was 15000 and its ground speed was 300.",
        ):
            with self.subTest(text=text):
                self.assertPasses(text)

    def test_rounding_cannot_hide_a_wrong_number(self):
        for text, fragment in (
            ("The descent peaked at 6,000 ft/min.", "Unsupported quantity"),
            ("The descent peaked at 7,500 ft/min.", "Unsupported quantity"),
            ("Pressure altitude fell 9,000 m.", "Unsupported quantity"),
            ("It was at FL250.", "Unsupported flight level"),
            ("The episode lasted 3 minutes.", "Unsupported quantity"),
            ("The episode lasted about two minutes.", "Unsupported quantity"),
            ("Twelve consecutive reports exceeded the threshold.", "Unsupported quantity"),
            ("It was registered as N12345.", "Unsupported identifier"),
            ("It squawked 7700.", "Unsupported number"),
            ("It squawked 0000.", "Unsupported number"),
            ("Its pressure altitude was 16000.", "Unsupported number"),
            ("It flew 3 practice approaches.", "Unsupported quantity"),
        ):
            with self.subTest(text=text):
                self.assertFails(text, fragment)

    def test_rule_values_count_only_in_sentences_about_the_rule(self):
        self.assertPasses("The detection threshold is 4,500 ft/min.")
        self.assertFails("It descended at 4,500 ft/min.", "Unsupported quantity")

    def test_vertical_direction_and_signs_must_match(self):
        self.assertFails("The aircraft climbed at 7,600 ft/min.", "Vertical direction contradicts")
        self.assertFails("It gained 10,350 ft.", "Altitude direction contradicts")
        self.assertFails("The signed rate was +7,600 ft/min.", "Unsupported quantity")
        self.assertPasses("The signed rate was -7,600 ft/min.")
        self.assertPasses("After climbing earlier, it descended at 7,600 ft/min.")

    def test_neighboring_readings_lists_and_spacing(self):
        evidence = packet(speed_event())
        for text in (
            "Earlier groundspeed values were 94.6, 66.7 and 82.7 kn.",
            "Fifteen seconds later the ground speed was 137.4 knots.",
            "The readings 15 seconds before and after the peak were 82.7 and 137.4 knots.",
            "The archived altitude was 2,000 at the peak.",
            "Its groundspeed is 1201.0; groundspeed is not airspeed.",
            "The database lists a 1966 year for this PA-28-140/150/160.",
            "These records rarely include a Piper PA-28.",
        ):
            with self.subTest(text=text):
                self.assertPasses(text, evidence)
        self.assertFails("Earlier groundspeed values were 94.6, 61.7 and 82.7 kn.", "61.7 kn", evidence)
        self.assertFails("The speed outlier lasted 15 seconds.", "Unsupported quantity", evidence)
        self.assertFails("The archived altitude was 2,400 at the peak.", "Unsupported number", evidence)
        self.assertFails("Piper PA-28 aircraft are common.", "Unscoped rarity claim", evidence)
        # A world record or a survivor count is a rarity claim too, and needs the same scope.
        for text in (
            "No PA-28 has ever gone faster.",
            "Few of these planes still fly.",
            "Only a handful remain airworthy.",
        ):
            with self.subTest(rarity=text):
                self.assertFails(text, "Unscoped rarity claim", evidence)
        self.assertPasses("No PA-28 in these records has ever gone faster.", evidence)

    def test_spelled_numbers_counts_and_ranges(self):
        evidence = packet(circling_event())
        for text in (
            "The helicopter flew three closed loops in 10 minutes.",
            "Three loops appear in 46 position samples.",
            "The B407 track spans 12 minutes.",
            "The Bell 407 helicopter flew three closed loops over a 10-minute window.",
            "The 20 qualifying windows overlap and are not 20 separate flights.",
            "The two readings need not match.",
        ):
            with self.subTest(text=text):
                self.assertPasses(text, evidence)
        self.assertFails("The helicopter flew four loops.", "Unsupported quantity", evidence)
        self.assertFails("Two aircraft circled together.", "Unsupported quantity", evidence)
        self.assertFails("It circled for between 20 and 30 minutes.", "Unsupported quantity", evidence)
        self.assertFails("The helicopter made 20 separate flights.", "Unsupported quantity", evidence)

    def test_ages_years_and_participant_counts(self):
        event = descent_event()
        event["evidence"]["aircraft"]["year"] = 1944
        evidence = packet(event)
        for text in (
            "The database lists a 1944 manufacture date, 82 years before the observation.",
            "The listed year makes the airframe about eight decades old.",
            "The listed year is more than three-quarters of a century before the observation.",
        ):
            with self.subTest(text=text):
                self.assertPasses(text, evidence)
        self.assertFails("The database lists a 1946 manufacture date.", "Unsupported", evidence)
        self.assertFails("It was built in the 1960s.", "Unsupported decade", evidence)
        self.assertFails("The airframe is about five decades old.", "Unsupported quantity", evidence)
        event["evidence"]["participants"] = [
            {"hex": "a11111", "role": "wingman", "source": "adsb_icao", "registration": "N11111"}
        ]
        self.assertPasses("Two aircraft were involved, including N11111.", packet(event))

    def test_named_measurements_bind_to_their_fields(self):
        event = watchlist_event()
        event["evidence"]["aircraft"].update(gs=487.0, tas=300.0, alt_baro=39000, alt_geom=40500)
        evidence = packet(event)
        for text in (
            "Its ground speed was 487 knots.",
            "Its true airspeed was 300 knots.",
            "It reported a pressure altitude of 39,000 ft.",
            "It reported a geometric altitude of 40,500 ft.",
            "It reported 39,000 ft by pressure and 40,500 ft by satellite.",
            "It was doing 487 knots over the ground.",
            "Its ground speed was about 560 mph.",
            "Its geometric altitude was about 12,300 m.",
            "It reported a ground speed of 487 knots, a true airspeed of 300 knots, and a geometric altitude of "
            "40,500 ft.",
            "It reported 40,500 ft and 300 knots.",
        ):
            with self.subTest(text=text):
                self.assertPasses(text, evidence)
        for text in (
            "Its true airspeed was 487 knots.",
            "Its ground speed was 300 knots.",
            "It reported a pressure altitude of 40,500 ft.",
            "It reported a geometric altitude of 39,000 ft.",
            "It reported 40,500 ft by pressure and 39,000 ft by satellite.",
            "It was doing 300 knots over the ground.",
            "Its true airspeed was about 560 mph.",
            "It reported a ground speed of 300 knots, a true airspeed of 487 knots, and a geometric altitude of "
            "39,000 ft.",
        ):
            with self.subTest(text=text):
                self.assertFails(text, "Unsupported quantity", evidence)

    def test_a_negative_altitude_keeps_its_sign(self):
        event = watchlist_event()
        event["evidence"]["aircraft"].update(alt_baro=-500, alt_geom=1300)
        evidence = packet(event)
        self.assertPasses("It reported -500 ft by pressure and 1,300 ft by satellite.", evidence)
        self.assertPasses("Its pressure altitude read -500 ft.", evidence)
        self.assertFails("It reported 500 ft by pressure.", "Unsupported quantity", evidence)
        self.assertFails("Its pressure altitude read 500 ft.", "Unsupported quantity", evidence)
        # A change still reads as a magnitude with its direction in words.
        self.assertPasses("It descended 10,350 ft.")
        self.assertFails("It climbed 10,350 ft.", "direction contradicts")

    def test_designators_come_from_the_packet(self):
        event = descent_event()
        event["evidence"]["aircraft"].update(t="BLCF", desc="BOEING 747-400LCF Dreamlifter")
        evidence = packet(event)
        self.assertPasses("The database lists a 747-400 Dreamlifter.", evidence)
        self.assertPasses("A 747 descended at 7,600 ft/min.", evidence)
        self.assertFails("The database lists a 747-8.", "Unsupported", evidence)
        self.assertFails("The Large Cargo Freighter descended.", "Unsupported name", evidence)


class DateTimeTests(ProseTests):
    def test_dates_and_times_resolve_to_packet_times(self):
        for text in (
            "The reports were saved on 21 September 2026 at 14:03 UTC.",
            "The reports were saved on Monday, September 21st.",
            "The peak report was at 2026-09-21T14:03:20Z.",
            "N54321 descended at a peak of 7,600 ft/min on Monday, 21 September 2026, at 14:03 UTC.",
        ):
            with self.subTest(text=text):
                self.assertPasses(text)
        for text, fragment in (
            ("The reports were saved on 22 September 2026.", "Unsupported date"),
            ("The reports were saved on Tuesday.", "Unsupported weekday"),
            ("The reports were saved at 15:03 UTC.", "Unsupported time"),
            ("The peak report was at 2026-09-21T14:03:50Z.", "Unsupported date or time"),
            ("The peak report was at 2026-09-21T14:03:20+02:00.", "Unsupported time zone"),
            ("The reports were saved today.", "Unsupported relative date"),
        ):
            with self.subTest(text=text):
                self.assertFails(text, fragment)

    def test_sample_times_must_match_to_the_second(self):
        evidence = packet(speed_event())
        self.assertPasses("The peak was at 14:13:20 UTC and the next reading at 14:13:35.", evidence)
        self.assertFails("The next reading was at 14:13:40 UTC.", "Unsupported time", evidence)


class ClaimTests(ProseTests):
    def test_unhedged_claims_are_rejected_and_hedged_ones_pass(self):
        rejected = (
            ("The crew declared an emergency.", "emergency"),
            ("The pilot was in distress.", "crew"),
            ("The owner was aboard.", "occupant"),
            ("The aircraft was on a surveillance mission.", "mission"),
            ("Police were searching for a suspect.", "mission"),
            ("The aircraft was hijacked.", "hijacking"),
            ("It flew 500 ft above the houses.", "terrain clearance"),
            ("A strong tailwind pushed it along.", "capability"),
            ("The aircraft went supersonic.", "capability"),
            ("The pilot declared an emergency, but the data cannot show why.", "emergency"),
        )
        for text, name in rejected:
            with self.subTest(text=text):
                self.assertFails(text, f"Unhedged {name} claim")
        for text in (
            "The data cannot show who was aboard.",
            "A steep descent can be intentional; the data cannot establish an emergency.",
            "These reports are not an emergency or distress determination.",
            "The track does not establish a mission or purpose.",
            "The descent may reflect routine flying.",
            "The data cannot establish the operator, the purpose of the flight, or whether anyone was aboard.",
            "The record does not establish the operator, the occupants or the mission.",
            "A steep descent alone is not an emergency.",
            "Pressure altitude is not height above the ground.",
            "Ground speed is not airspeed.",
            "Hypothesis: a genuine radio-failure report.",
            "Possibility: a training flight.",
            "MLAT combines timing from several receivers.",
            "Ground speed differs from airspeed because of wind, which the data cannot show.",
        ):
            with self.subTest(text=text):
                self.assertPasses(text)

    def test_type_descriptions_are_not_occupant_or_mission_claims(self):
        event = descent_event()
        event["evidence"]["aircraft"].update(t="P8", desc="BOEING P-8A Poseidon maritime patrol aircraft")
        evidence = packet(event)
        self.assertPasses("The database lists a Boeing P-8A Poseidon maritime patrol aircraft.", evidence)
        self.assertFails("The aircraft was on patrol.", "Unhedged mission claim", evidence)
        event["evidence"]["aircraft"].update(t="A388", desc="AIRBUS A-380-800")
        evidence = packet(event)
        self.assertPasses("The Airbus A380 is a passenger airliner.", evidence)
        self.assertFails("The A380 carried passengers.", "Unhedged occupant claim", evidence)

    def test_transmitted_code_meanings_are_not_unhedged_claims(self):
        event = descent_event()
        event["evidence"]["aircraft"]["squawk"] = "7700"
        evidence = packet(event)
        self.assertPasses("The transponder broadcast 7700, the code for a general emergency.", evidence)
        self.assertPasses("It squawked 7700, which signals a general emergency.", evidence)
        self.assertFails("The aircraft had an emergency.", "Unhedged emergency claim", evidence)
        self.assertFails("It squawked 7700 because of a hijacking.", "Unhedged hijacking claim", evidence)
        self.assertFails("It squawked 7700, signalling radio failure.", "Unhedged emergency claim", evidence)

    def test_airspeed_claims_need_an_airspeed_report(self):
        self.assertFails("Its true airspeed was 300 knots.", "Airspeed claim")
        event = descent_event()
        event["evidence"]["aircraft"]["tas"] = 300
        self.assertPasses("Its true airspeed was 300 knots.", packet(event))

    def test_watchlist_associations_must_be_attributed(self):
        evidence = packet(watchlist_event())
        self.assertPasses("Plane Alert DB lists N729KF in its Public figure association category.", evidence)
        self.assertPasses("Plane Alert DB associates the aircraft with Jane Example.", evidence)
        self.assertFails(
            "Jane Example flew past at 41,000 ft.", "Unattributed watchlist association", evidence
        )
        self.assertFails("The jet belongs to Jane Example.", "Unhedged owner claim", evidence)
        self.assertFails(
            "Plane Alert DB lists Jane Example, who was aboard.", "Unhedged occupant claim", evidence
        )
        self.assertPasses(
            "Plane Alert DB lists Jane Example, but the data cannot show whether she was aboard.", evidence
        )

    def test_occupant_disclaimers_are_not_occupant_claims(self):
        evidence = packet(watchlist_event())
        for text in (
            "The entry explains why spotters watch for the jet, not who was aboard.",
            "None of that shows who was aboard.",
            "Who was aboard and why it flew are beyond what the data shows.",
            "The listing will not say who was aboard.",
        ):
            with self.subTest(text=text):
                self.assertPasses(text, evidence)
        self.assertFails("She was aboard.", "Unhedged occupant claim", evidence)
        self.assertFails(
            "Jane Example was aboard, and the jet flew north.", "Unhedged occupant claim", evidence
        )
        # The disclaimer governs the occupant claim alone: "beyond what" excuses no owner or rarity.
        self.assertFails("Its owners are beyond what the data shows.", "Unhedged owner claim", evidence)
        self.assertFails("Its rare visits are beyond what the data shows.", "Unscoped rarity claim", evidence)

    def test_a_manufacturer_association_is_not_a_mention_of_the_type(self):
        event = watchlist_event()
        event["evidence"]["aircraft"].update(t="B74S", desc="Boeing 747-400 Dreamlifter")
        event["evidence"]["watchlist"]["association"] = "Boeing"
        evidence = packet(event)
        self.assertPasses("The aircraft database lists it as a Boeing 747-400 Dreamlifter.", evidence)
        self.assertPasses("Plane Alert DB lists the Dreamlifter under Boeing.", evidence)
        self.assertFails("Boeing flew it here.", "Unattributed watchlist association", evidence)
        self.assertFails("Boeing's jet passed at 41,000 ft.", "written as ownership", evidence)

    def test_tag_counts_come_from_the_entry(self):
        event = watchlist_event()
        event["evidence"]["watchlist"]["tags"] = ["Bizjet", "Bees", "Honey"]
        evidence = packet(event)
        self.assertPasses("Its entry carries three tags.", evidence)
        self.assertPasses("Plane Alert DB gives it 3 tags.", evidence)
        self.assertFails("Its entry carries two tags.", "Unsupported quantity", evidence)
        self.assertFails("Its entry carries four tags.", "Unsupported quantity", evidence)
        event["evidence"]["watchlist"]["tags"] = ["Bizjet", "Bees"]
        self.assertFails("Its entry carries three tags.", "Unsupported quantity", packet(event))
        # Story facts give the tags as wl.tags, a list of names: the count comes from there too.
        facts = story_fixtures.facts(event)
        self.assertEqual(story_facts.value(facts, "wl.tags"), ["Bizjet", "Bees"])
        self.assertPasses("Plane Alert DB gives it two tags.", facts)
        self.assertPasses("It has two tags in Plane Alert DB.", facts)
        self.assertFails("Plane Alert DB gives it three tags.", "Unsupported quantity", facts)

    def test_attributed_forms_refer_back_to_the_entry(self):
        event = watchlist_event()
        event["evidence"]["watchlist"]["tags"] = ["Bizjet", "Bees"]
        evidence = packet(event)
        for text in (
            "The entry names Jane Example as its association.",
            "Jane Example is the listed association.",
            "The list files it under Jane Example.",
            "Its entry carries the tags Bizjet and Bees.",
            "Gulfstream G650 N729KF, listed under Jane Example, spotted",
            "Jane Example-listed Gulfstream G650 N729KF spotted",
        ):
            with self.subTest(text=text):
                self.assertPasses(text, evidence)
        self.assertFails("Jane Example flew it.", "Unattributed watchlist association", evidence)
        self.assertFails("Jane Example's jet flew north.", "written as ownership", evidence)
        # The listed form is the list's only in the headline form or beside Plane Alert DB or the
        # entry; a sentence that names another source puts the name in that source's mouth.
        self.assertPasses("Plane Alert DB has N729KF listed under Jane Example.", evidence)
        for text in (
            "N729KF is listed under Jane Example in the FAA registry.",
            "N729KF is listed under Jane Example.",
            "Plane Alert DB and the FAA registry have it listed under Jane Example.",
            "Gulfstream G650 N729KF, listed under Jane Example in the registry, spotted",
        ):
            with self.subTest(text=text):
                self.assertFails(text, "Unattributed watchlist association", evidence)
        # Back-references name the list only in a watchlist story.
        plain = descent_event()
        plain["evidence"]["watchlist"] = {}
        self.assertFails(
            "The entry names Jane Example as its association.", "Unsupported name", packet(plain)
        )

    def test_rated_limits_are_the_types_figures(self):
        evidence = packet(descent_event())
        self.assertFails("The King Air's never-exceed speed is 263 knots indicated.", "capability", evidence)
        # A cited rated limit, as story facts carry it (story_facts.RATED_LIMIT_FIELDS).
        evidence = {**evidence, "rated_limits": {"value": 263, "unit": "kn", "field": "vne"}}
        self.assertPasses("The King Air's never-exceed speed is 263 knots indicated.", evidence)
        self.assertFails("Its indicated airspeed was 263 knots.", "Airspeed claim", evidence)
        self.assertFails("A tailwind pushed it to 263 knots.", "Unhedged capability claim", evidence)


class TypeClassTests(unittest.TestCase):
    @staticmethod
    def unnamed(kind):
        return story_fixtures.facts(story_fixtures.unnamed(kind))

    def test_first_sighting_headline_uses_the_class_phrase_before_the_bare_code(self):
        facts = self.unnamed("first_type_here")
        self.assertEqual(
            public_prose.fixed_headline(facts),
            ("Twin-turboshaft helicopter A129 spotted for the first time", ("id.type_class", "id.type_code")),
        )

    def test_a_story_naming_only_the_class_must_still_attribute_it(self):
        facts = self.unnamed("descending_spiral")
        story = editorial.render(facts, "attr-1")
        self.assertEqual(public_prose.story_issues(story, facts), [])
        # No registration to trigger the check: only the class phrase and the type code name the type.
        anonymous = {**facts, "facts": [item for item in facts["facts"] if item["id"] != "id.registration"]}
        unattributed = {
            "headline": "Helicopter circles on 20 September",
            "dek": "A twin-turboshaft helicopter circled on 20 September.",
            "paragraphs": ["Its positions traced a descending spiral."],
        }
        self.assertIn("paragraphs:database_attribution", public_prose.story_issues(unattributed, anonymous))
        coded = {**unattributed, "dek": "Type code A129 circled on 20 September."}
        self.assertIn("paragraphs:database_attribution", public_prose.story_issues(coded, anonymous))

    def test_a_one_word_class_phrase_alone_needs_no_attribution(self):
        # "airplane" is a common noun before it is a class phrase; the code still needs attribution.
        facts = story_fixtures.facts(story_fixtures.unnamed("descending_spiral", "C08T"))
        self.assertEqual(story_facts.value(facts, "id.type_class"), "airplane")
        anonymous = {**facts, "facts": [item for item in facts["facts"] if item["id"] != "id.registration"]}
        story = {
            "headline": "Airplane circles on 20 September in a slow spiral",
            "dek": "An airplane traced a descending spiral on 20 September.",
            "paragraphs": ["Its positions traced a descending spiral. The airplane kept turning."],
        }
        self.assertNotIn("paragraphs:database_attribution", public_prose.story_issues(story, anonymous))
        coded = {**story, "dek": "Type code C08T traced a descending spiral on 20 September."}
        self.assertIn("paragraphs:database_attribution", public_prose.story_issues(coded, anonymous))


class CaveatPlacement(unittest.TestCase):
    """One caveat at most, never the story's last sentence; two sentences at least; no question
    headline but a TCAS advisory's; and no certified figure given to a class phrase."""

    @staticmethod
    def facts():
        return story_fixtures.facts(
            story_fixtures.kind_event("descending_spiral"), place=story_fixtures.PLACES["coleman"]
        )

    def story(self, paragraphs, headline="Bell 407 N6721TX spirals down near Coleman, Michigan"):
        facts = self.facts()
        base = editorial.render(facts, "caveat-1")
        return {**base, "headline": headline, "paragraphs": paragraphs}, facts

    def test_a_story_whose_last_sentence_is_a_caveat_is_refused(self):
        story, facts = self.story(
            [
                "N6721TX spiraled down over Coleman, Michigan on 20 September, losing 1,000 ft in six turns.",
                "The aircraft database lists N6721TX as a Bell 407. These reports are not an emergency determination.",
            ]
        )
        self.assertIn("paragraphs:closing_caveat", public_prose.story_issues(story, facts))

    def test_an_epistemic_closer_counts_as_a_caveat(self):
        story, facts = self.story(
            [
                "N6721TX spiraled down over Coleman, Michigan on 20 September, losing 1,000 ft in six turns.",
                "The aircraft database lists N6721TX as a Bell 407. Why it circled is unknown.",
            ]
        )
        self.assertIn("paragraphs:closing_caveat", public_prose.story_issues(story, facts))

    def test_one_caveat_inside_the_story_passes(self):
        story, facts = self.story(
            [
                "N6721TX spiraled down over Coleman, Michigan on 20 September, losing 1,000 ft in six turns.",
                "These reports are not an emergency determination. The aircraft database lists N6721TX as a Bell 407.",
            ]
        )
        issues = public_prose.story_issues(story, facts)
        self.assertNotIn("paragraphs:closing_caveat", issues)
        self.assertNotIn("paragraphs:repeated_limitations", issues)

    def test_a_required_sentence_is_the_one_caveat_and_may_not_close(self):
        facts = story_fixtures.facts(
            story_fixtures.kind_event("seasonal_speed_record"), place=story_fixtures.PLACES["coleman"]
        )
        required = public_prose.required_sentence(facts)
        story = editorial.render(facts, "record-1")
        paragraphs = [" ".join(text.replace(required, "").split()) for text in story["paragraphs"]]
        closing = {**story, "paragraphs": [*paragraphs[:-1], f"{paragraphs[-1]} {required}"]}
        self.assertIn("paragraphs:closing_caveat", public_prose.story_issues(closing, facts))
        self.assertNotIn("paragraphs:closing_caveat", public_prose.story_issues(story, facts))
        doubled = public_prose.insert_before_last(story["paragraphs"], "Why it flew so fast is unknown.")
        self.assertIn(
            "paragraphs:repeated_limitations",
            public_prose.story_issues({**story, "paragraphs": doubled}, facts),
        )

    def test_fewer_than_two_sentences_is_too_short(self):
        story, facts = self.story(
            [
                "N6721TX, which the aircraft database lists as a Bell 407, spiraled down over Coleman, Michigan on "
                "20 September."
            ]
        )
        self.assertIn("paragraphs:too_short", public_prose.story_issues(story, facts))
        story, facts = self.story(
            [
                "N6721TX spiraled down over Coleman, Michigan on 20 September, in six turns.",
                "The aircraft database lists N6721TX as a Bell 407.",
            ]
        )
        self.assertNotIn("paragraphs:too_short", public_prose.story_issues(story, facts))

    def test_question_headlines_need_a_tcas_advisory(self):
        story, facts = self.story(
            [
                "N6721TX spiraled down over Coleman, Michigan on 20 September.",
                "The aircraft database lists N6721TX as a Bell 407, a light helicopter.",
            ],
            headline="Why did Bell 407 N6721TX spiral down near Coleman, Michigan?",
        )
        self.assertIn("headline:question", public_prose.story_issues(story, facts))
        tcas = story_fixtures.facts(
            story_fixtures.kind_event("tcas_ra"), place=story_fixtures.PLACES["coleman"]
        )
        rendered = editorial.render(tcas, "tcas-1")
        asked = {**rendered, "headline": "What does a TCAS advisory mean for a Cessna Citation CJ4?"}
        self.assertNotIn("headline:question", public_prose.story_issues(asked, tcas))

    def test_insert_before_last_keeps_the_sentence_off_the_end(self):
        paragraphs = ["One. Two.", "Three. Four."]
        self.assertEqual(
            public_prose.insert_before_last(paragraphs, "Caveat."), ["One. Two.", "Three. Caveat. Four."]
        )
        self.assertEqual(
            public_prose.insert_before_last(["One. Two.", "Three."], "Caveat."),
            ["One. Caveat. Two.", "Three."],
        )
        self.assertEqual(public_prose.insert_before_last(["Only one."], "Caveat."), ["Caveat. Only one."])
        self.assertEqual(
            public_prose.insert_before_last(["One.", "Two."], "Caveat."), ["One.", "Caveat. Two."]
        )
        self.assertEqual(public_prose.last_sentence(paragraphs), "Four.")
        self.assertEqual(public_prose.last_sentence([]), "")

    unnamed_jet = staticmethod(story_fixtures.unnamed_jet)

    def test_a_certified_limit_given_to_a_class_phrase_is_refused(self):
        facts = self.unnamed_jet()
        self.assertEqual(story_facts.value(facts, "id.type_class"), "twin-jet airplane")
        story = editorial.render(facts, "class-limit")
        claimed = public_prose.insert_before_last(
            story["paragraphs"],
            "A twin-jet airplane like this has a certified maximum operating speed of 335 knots.",
        )
        self.assertIn("claim:class_limit", public_prose.story_issues({**story, "paragraphs": claimed}, facts))

    def test_a_certified_limit_given_to_the_type_code_passes(self):
        facts = self.unnamed_jet()
        story = editorial.render(facts, "class-limit")
        cited = public_prose.insert_before_last(
            story["paragraphs"],
            "The data sheet for type code CRJX lists a maximum operating speed of 335 knots.",
        )
        self.assertNotIn(
            "claim:class_limit", public_prose.story_issues({**story, "paragraphs": cited}, facts)
        )


class SerialCommaTests(unittest.TestCase):
    def test_lists_of_three_need_the_serial_comma(self):
        for text in (
            "It climbed, turned and left.",
            "The hook is bizjets, tags and links.",
            "a code, a first sighting or a record",
            "It reported 39,000 ft, 487 knots and nine positions.",
            'Its tags are "Business Jet", "Corporate" and "Bees".',
            "coast guard, customs and border patrol aircraft",
            "the jet, the helicopter and the balloon",
        ):
            with self.subTest(text=text):
                issues = public_prose.serial_comma_issues(text)
                self.assertEqual(len(issues), 1, (text, issues))
                self.assertTrue(issues[0].startswith("Missing serial comma: "), issues)

    def test_parentheticals_clauses_ranges_and_pairs_are_not_lists(self):
        for text in (
            "It climbed, turned, and left.",
            "The jet, listed in the database, flew north and landed.",
            "On 8 September, it climbed and left.",
            "Near Coleman, Michigan, N123 climbed and left.",
            "05-5140 and 06-6159, a pair of C-17s, fly close together.",
            "A pair of C-17s, 05-5140 and 06-6159, fly close together.",
            "The spike, between 96 and 123 knots, stands alone.",
            "unique or special aircraft, such as testbeds and NASA aircraft. The list carries it.",
            "One report read 1,005.6 knots, between neighbors of about 15 knots, and looks like a data error.",
            "Ground speed includes any tailwind, and a faulty reading can't be ruled out.",
            "At the closest recorded point between the aircraft, they were 3.2 NM and 1,100 ft apart.",
            "Flying as AAR224 on 27 September, the double-deck Airbus reported 487 knots and 39,000 ft.",
        ):
            with self.subTest(text=text):
                self.assertEqual(public_prose.serial_comma_issues(text), [], text)

    def test_series_joins_with_the_serial_comma(self):
        self.assertEqual(public_prose.series([]), "")
        self.assertEqual(public_prose.series(["A"]), "A")
        self.assertEqual(public_prose.series(["A", "B"]), "A and B")
        self.assertEqual(public_prose.series(["A", "B", "C"]), "A, B, and C")
        self.assertEqual(public_prose.series(["A", "B", "C", "D"], "or"), "A, B, C, or D")
        self.assertEqual(public_prose.series(["A", None, "", "C"]), "A and C")


class NameAndLocationTests(ProseTests):
    def test_organization_restatements_are_built_once_per_packet(self):
        evidence = {
            "watchlist": {
                "association_label": "Acme Air",
                "organization": "Acme Air, a regional airline in Ohio",
            }
        }
        self.assertEqual(len(packet_facts(evidence).restatements), 1)
        check = public_prose.prose_checker(evidence)
        built = patch.object(
            public_prose, "_organization_restatements", wraps=public_prose._organization_restatements
        )
        with built as restatements:
            for text in (
                "The entry names Acme Air, a regional airline in Ohio, as its organization.",
                "The entry links to Acme Air, a regional airline in Ohio.",
            ):
                with self.subTest(text=text):
                    self.assertEqual(check(text), [])
            # Its words alone still name nothing.
            self.assertIn("Unsupported name: Ohio", check("The flight ended in Ohio."))
        restatements.assert_not_called()

    def test_names_come_from_the_packet(self):
        event = descent_event()
        event["evidence"]["aircraft"].update(t="BE35", desc="BEECH 35 Bonanza")
        evidence = packet(event)
        self.assertPasses("Beech 35 Bonanzas are a long-established design.", evidence)
        self.assertFails("The Cessna descended at 7,600 ft/min.", "Unsupported name", evidence)
        self.assertFails(
            "The call sign Air Force One applies only when the President is aboard.", "Unsupported name"
        )
        self.assertPasses("Compare the readings before and after the peak.")
        # Story prose keeps the following-word exemption; only profile sentences are strict.
        for text in (
            "Moments later the aircraft descended.",
            "Overnight, it descended.",
            "Unusually, it descended.",
        ):
            with self.subTest(text=text):
                self.assertFalse(
                    [issue for issue in prose_issues(text, self.packet) if "Unsupported name" in issue], text
                )
        self.assertPasses("Steep descent recorded near the peak")
        self.assertFails("Steep Descent Recorded Near Peak", "Unsupported name")

    def test_receiver_relative_and_private_location_phrasing_is_refused(self):
        for text in (
            "The receiver picked it up.",
            "Our antenna saw it first.",
            "It passed directly overhead.",
            "It flew 20 km away.",
            "It was north of us.",
            "Our station is near the airport.",
            "The signal strength was high.",
            "It stayed inside our coverage.",
            "The time was 10:03 EDT.",
            "It appeared at 10:03 local time.",
            "See example.ts.net for details.",
            "Review the local replay.",
            "It circled a nearby town.",
            "I saw it descend.",
            "Its last report here came at 14:03 UTC.",
            "It is the fastest descent seen here this season.",
        ):
            with self.subTest(text=text):
                self.assertFails(text, "Receiver-relative or private location phrasing")
        for text in (
            "Nearby readings show the same descent.",
            "Other nearby groundspeed values were similar.",
            "MLAT combines timing from multiple receivers.",
            "The Type I classification is not used in these records.",
            "It is the fourth story about the A380 here.",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    [issue for issue in prose_issues(text, self.packet) if "location" in issue], [], text
                )


class InputTests(ProseTests):
    def test_hidden_characters_homoglyphs_markup_and_limits(self):
        self.assertPasses("N54321 descended at 7,600 ft/min.")
        self.assertFails("The hi​jacking was real.", "Unhedged hijacking claim")
        self.assertFails("The aircraft was hijаcked.", "Unsupported characters")
        self.assertFails("Read more at https://example.com.", "URL, address, or markup")
        self.assertFails("<b>Bold</b> claims.", "URL, address, or markup")
        self.assertFails("**Bold** claims.", "URL, address, or markup")
        self.assertEqual(prose_issues(None, self.packet), ["Prose must be text"])
        self.assertEqual(
            prose_issues("a" * (MAX_PROSE + 1), self.packet), ["Prose exceeds the length the checks accept"]
        )
        self.assertEqual(prose_issues("", self.packet), [])

    def test_worst_case_inputs_finish_quickly(self):
        evidence = packet(speed_event())
        for text in (
            " ".join(str(index) for index in range(4000)),
            "1" + " " * (MAX_PROSE - 2) + "a",
            "-".join("1" for _ in range(9000)),
            "Aa " * 6000,
            "aboard, " * 2400,
            "a minute and a half " * 900,
            "U.S. " * 3900,
            " to ".join(str(index) for index in range(3000)),
        ):
            with self.subTest(text=text[:20]):
                start = time.monotonic()
                prose_issues(text[:MAX_PROSE], evidence)
                self.assertLess(time.monotonic() - start, 5)

    def test_private_and_opaque_values_are_not_facts(self):
        evidence = dict(self.packet, replay={"points": [{"latitude": 35.123456}]})
        self.assertFails("The aircraft passed 35.123456 degrees north.", "Unsupported", evidence)
        facts = packet_facts({"links": [{"id": "x", "label": "Lookup", "url": "https://x.example/4455"}]})
        self.assertNotIn("4455", facts.exact)
        self.assertNotIn("4455", facts.identifiers)
        self.assertEqual(prose_issues("The aircraft descended.", None), [])

    def test_public_events_and_notes_payloads_are_accepted_as_evidence(self):
        projected = public._projection(
            self.packet,
            {"title": "Descent", "summary": "Saved reports.", "article_path": "/stories/descent-3-oct-2026/"},
        )
        self.assertPasses("The peak report reached 7,600 ft/min.", projected)
        self.assertFails("The peak report reached 8,600 ft/min.", "Unsupported quantity", projected)
        event = descent_event()
        event.update(why="PRIVATE", explanation="PRIVATE", confidence="Coherent", origin="Live receiver")
        payload = explain.payload_for(event)
        self.assertPasses("The descent peaked at 7,600 ft/min.", payload)
        self.assertFails("The descent peaked at 7,900 ft/min.", "Unsupported quantity", payload)


class AirportClaimTests(unittest.TestCase):
    def test_airport_use_verbs_are_refused_and_the_distance_binds_to_the_airport_fact(self):
        airport = story_fixtures.BRIGHTON
        facts = story_fixtures.facts(
            story_fixtures.kind_event("descending_spiral"),
            place=story_fixtures.PLACES["coleman"],
            airport=airport,
        )
        story = editorial.render(facts, "ap-claims")
        for phrase in (
            "It landed at Brighton Airport.",
            "It lands at Brighton Airport.",
            "It made landings at Brighton Airport.",
            "It touched down at Brighton Airport.",
            "It touches down at Brighton Airport.",
            "It took off from Brighton Airport.",
            "It takes off from Brighton Airport.",
            "Its take-off from Brighton Airport was logged.",
            "It departs Brighton Airport.",
            "It departed Brighton Airport.",
            "It arrives at Brighton Airport.",
            "It returned to Brighton Airport.",
            "It was bound for Brighton Airport.",
            "It flew an inbound leg to Brighton Airport.",
            "It flew an outbound leg from Brighton Airport.",
            "It made an approach to Brighton Airport.",
            "It approaches Brighton Airport.",
            "It was on final for Brighton Airport.",
            "It headed toward Brighton Airport.",
            "It was heading for Brighton Airport.",
            "It flew toward Brighton Airport.",
            "It flies towards Brighton Airport.",
            "It would arrive at Brighton Airport.",
            "Arrivals at Brighton Airport were light.",
            "Departures from Brighton Airport were light.",
            "It flew to Brighton Airport.",
            "It had flown to Brighton Airport.",
            "It was en route to Brighton Airport.",
            "It diverted to Brighton Airport.",
        ):
            with self.subTest(phrase=phrase):
                bad = {
                    **story,
                    "paragraphs": [story["paragraphs"][0], f"{phrase} " + story["paragraphs"][-1]],
                }
                self.assertIn("claim:airport_use", public_prose.story_issues(bad, facts))
        bound = public_prose.bound_facts(facts, ["It came within about 2 miles of Brighton Airport."])
        self.assertIn("track.airport_nm", bound)
        self.assertNotIn("track.nm", bound)

    def test_an_entry_association_naming_a_landing_is_no_airport_use(self):
        raw = story_fixtures.kind_event("watchlist_match")
        raw["evidence"]["watchlist"]["association"] = "Falcon Landing LLC"
        airport = story_fixtures.BRIGHTON
        facts = story_fixtures.facts(raw, place=story_fixtures.PLACES["coleman"], airport=airport)
        self.assertIn("track.airport", story_facts.fact_map(facts))
        for n in range(4):
            story = editorial.render(facts, f"ap-entry-{n}")
            with self.subTest(n=n):
                self.assertIn("Falcon Landing LLC", " ".join(story["paragraphs"]))
                self.assertEqual(public_prose.story_issues(story, facts), [])
        # The entry's name is masked, not the sentence: a landing claim beside it is still refused.
        bad = {
            **story,
            "paragraphs": [
                *story["paragraphs"][:-1],
                "It landed at Brighton Airport. " + story["paragraphs"][-1],
            ],
        }
        self.assertIn("claim:airport_use", public_prose.story_issues(bad, facts))

    def test_landing_gear_and_a_restated_type_fact_are_no_airport_use(self):
        airport = story_fixtures.BRIGHTON
        restated = "Bell designed the 407 to approach small helipads."
        facts = story_fixtures.facts(
            story_fixtures.kind_event("descending_spiral"),
            place=story_fixtures.PLACES["coleman"],
            airport=airport,
            profiles=story_fixtures.profile("B407", restated),
        )
        story = editorial.render(facts, "ap-gear")
        for sentence in ("Its landing gear stayed down.", restated):
            with self.subTest(sentence=sentence):
                good = {
                    **story,
                    "paragraphs": [story["paragraphs"][0], f"{sentence} " + story["paragraphs"][-1]],
                }
                self.assertEqual(public_prose.story_issues(good, facts), [])

    def test_to_before_an_altitude_or_speed_figure_is_no_airport_use(self):
        facts = story_fixtures.facts(
            story_fixtures.kind_event("descending_spiral"),
            place=story_fixtures.PLACES["coleman"],
            airport=story_fixtures.BRIGHTON,
        )
        story = editorial.render(facts, "ap-figures")
        for phrase in (
            "It flew to 41,000 ft.",
            "It had flown to 41,000 feet.",
            "It climbed to 39,000 ft.",
            "It descended to 3,000 ft.",
            "It returned to 250 knots.",
            "It was heading to 12000 ft.",
            "It slowed to 180 kn.",
            "It flew to 41000 ft and then to 250 kt.",
            # A flight level and a speed in any unit are figures too; the figure checks judge them.
            "It flew to FL410.",
            "It climbed to flight level 390.",
            "It slowed to 300 km/h.",
        ):
            with self.subTest(phrase=phrase):
                good = {
                    **story,
                    "paragraphs": [story["paragraphs"][0], f"{phrase} " + story["paragraphs"][-1]],
                }
                issues = public_prose.story_issues(good, facts)
                self.assertNotIn("claim:airport_use", issues)
                # Nor is it a destination claim in a story without an airport.
                self.assertFalse([issue for issue in issues if "destination" in issue], issues)
        # A figure without an altitude or speed unit, or an airport after it, is still airport use.
        for phrase in (
            "It flew to Brighton Airport at 41,000 ft.",
            "It headed to within 2 miles of Brighton Airport.",
        ):
            with self.subTest(phrase=phrase):
                bad = {
                    **story,
                    "paragraphs": [story["paragraphs"][0], f"{phrase} " + story["paragraphs"][-1]],
                }
                self.assertIn("claim:airport_use", public_prose.story_issues(bad, facts))


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.facts = story_fixtures.norm_facts(
            "seasonal_speed_record", place=story_fixtures.PLACES["coleman"]
        )
        self.story = editorial.render(self.facts, "norm-1")
        self.plural = editorial.build_slots(self.facts)["type_plural"]

    def issues(self, sentence):
        story = {
            **self.story,
            "paragraphs": [self.story["paragraphs"][0], sentence + " " + self.story["paragraphs"][-1]],
        }
        return public_prose.story_issues(story, self.facts)

    def test_a_baseline_figure_supports_only_a_type_sentence_scoped_to_these_records(self):
        good = (
            f"In these records, {self.plural} in level flight between 30,000 and 40,000 ft usually show ground-speed "
            "readings of 380 to 430 knots."
        )
        self.assertEqual(self.issues(good), [])
        self.assertTrue(
            {"norm.gs_low", "norm.gs_high", "norm.band_low", "norm.band_high"}
            <= public_prose.bound_facts(self.facts, [good])
        )

        def unsupported(*figures):
            return [f"paragraph[1]:Unsupported quantity: {figure} knots" for figure in figures]

        def baseline(*figures):
            return [f"paragraph[1]:Unsupported baseline figure: {figure}" for figure in figures]

        plural = self.plural
        for bad, issues in (
            # About this aircraft, not the type.
            ("The jet reached 430 knots.", unsupported(430)),
            ("In these records, this jet usually shows 380 to 430 knots.", unsupported(380, 430)),
            # About the type, but with no scope: the type's performance, not these records.
            (f"{plural} in level flight usually show 380 to 430 knots.", unsupported(380, 430)),
            # The norm covers 30 days of these records, never a season.
            (f"This season, {plural} in level flight usually show 380 to 430 knots.", unsupported(380, 430)),
            # A lone figure, a comparison with this aircraft, or a limit or record is never the range.
            (
                f"In these records, {plural} in level flight usually show 380 knots, and this one showed 430 knots.",
                unsupported(380, 430) + baseline(380, 430),
            ),
            (
                f"In these records, {plural} usually reach 430 knots, as this one did.",
                unsupported(430) + baseline(430),
            ),
            (f"In these records, {plural} top out at 430 knots.", unsupported(430) + baseline(430)),
            # A reading of 651 knots supports "faster than 430" as a number; the baseline rule refuses it.
            (f"In these records, {plural} cannot fly faster than 430 knots.", baseline(430)),
            (f"In these records, the fastest {plural} reached 430 knots.", unsupported(430) + baseline(430)),
            (
                f"In these records, {plural} usually show 380 to 430 knots, and this one did too.",
                unsupported(380, 430) + baseline(380, 430),
            ),
            (
                f"In these records, {plural} usually show 430 to 380 knots.",
                unsupported(430, 380) + baseline(430, 380),
            ),
            (
                f"In these records, {plural} usually fly at least 380 to 430 knots at their limit.",
                baseline(380, 430),
            ),
        ):
            with self.subTest(bad=bad):
                self.assertEqual(self.issues(bad), issues)
        # A range written in another unit, beside another speed, or as a ceiling is no baseline.
        for bad in (
            f"In these records, {plural} in level flight usually show 380 to 430 ft.",
            f"In these records, {plural} usually show 380 to 430 knots, so 651 knots stands out.",
            f"In these records, {plural} usually show 380 to 430 knots, less than the jet's reading.",
            f"In these records, {plural} in level flight rarely exceed 380 to 430 knots.",
            # Another speed or altitude figure, in any unit, is a comparison too.
            f"In these records, {plural} usually show 380 to 430 knots, against a record of 750 mph.",
            f"In these records, {plural} usually show 380 to 430 knots, and 39,000 ft stands out.",
            f"In these records, {plural} usually show 380 to 430 knots, against 45,000 feet for a record.",
            f"In these records, {plural} usually show 380 to 430 knots, so 651 kt stands out.",
            f"In these records, {plural} usually show 380 to 430 knots, and 651 kn stands out.",
            f"In these records, {plural} usually show 380 to 430 knots, against 1,200 km/h for a record.",
        ):
            with self.subTest(bad=bad):
                issues = self.issues(bad)
                self.assertIn("paragraph[1]:Unsupported baseline figure: 380", issues)
                self.assertIn("paragraph[1]:Unsupported baseline figure: 430", issues)
                self.assertFalse(
                    public_prose.bound_facts(self.facts, [bad]) & {"norm.gs_low", "norm.gs_high"}
                )
        for text in ("The jet reached 430 knots.", f"In these records, {plural} top out at 430 knots."):
            self.assertEqual(
                public_prose.bound_facts(self.facts, [text]) & {"norm.gs_low", "norm.gs_high"}, set()
            )

    def test_baseline_figures_are_no_measurements_in_the_writer_packet(self):
        entries = [entry for entry in public_prose.facts_packet(self.facts)["facts"] if "baseline" in entry]
        self.assertEqual(
            sorted((entry["id"], entry["baseline"], entry["unit"]) for entry in entries),
            [
                ("norm.band_high", 40000, "ft"),
                ("norm.band_low", 30000, "ft"),
                ("norm.gs_high", 430, "kn"),
                ("norm.gs_low", 380, "kn"),
            ],
        )
        self.assertFalse(any("value" in entry for entry in entries))


class CaveatTests(unittest.TestCase):
    def test_a_restated_type_fact_is_no_caveat_and_only_a_caveat_is_blanked(self):
        unknown = "Whether the type ever flew in service is unknown."
        self.assertTrue(public_prose.is_caveat(unknown))
        self.assertFalse(public_prose.is_caveat(unknown, [unknown]))
        self.assertTrue(public_prose.is_caveat("It does not show who was aboard.", [unknown]))
        # A sentence that says nothing the data cannot show is read once, with nothing blanked.
        with patch.object(public_prose, "_restated", wraps=public_prose._restated) as restated:
            self.assertFalse(public_prose.is_caveat("It flew at 41,000 ft.", [unknown]))
        restated.assert_not_called()


def kind_story(kind, **changes):
    """(facts, story): a kind's template story, with fact values changed by id first."""
    facts = story_fixtures.facts(story_fixtures.kind_event(kind), place=story_fixtures.PLACES["coleman"])
    for item in facts["facts"]:
        if item["id"] in changes:
            item["value"] = changes[item["id"]]
    return facts, editorial.render(facts, "episode")


def with_sentence(story, sentence):
    """The story with a sentence added before the last sentence of its last paragraph."""
    return {**story, "paragraphs": public_prose.insert_before_last(story["paragraphs"], sentence)}


def without(story, sentence):
    return {
        **story,
        "paragraphs": [" ".join(text.replace(sentence, "").split()) for text in story["paragraphs"]],
    }


class NewProseRules(unittest.TestCase):
    """The new kinds' headlines and required sentences, and the story-quality checks."""

    def test_fixed_headlines_for_new_kinds(self):
        cases = (
            ("first_operator_here", {"fs.operator": "Qantas"}, "Qantas spotted for the first time"),
            ("winds_aloft_day", {"wind.day": "2026-11-05"}, "Strong winds aloft on 5 November 2026"),
            ("unregistered_address", {"id.hex": "a1b2c3"}, "Address A1B2C3 flew with no registry entry"),
        )
        for kind, changes, headline in cases:
            with self.subTest(kind=kind):
                facts = story_fixtures.facts(story_fixtures.kind_event(kind))
                for item in facts["facts"]:
                    if item["id"] in changes:
                        item["value"] = changes[item["id"]]
                self.assertEqual(public_prose.fixed_headline(facts)[0], headline)

    def test_new_required_sentences(self):
        winds = story_fixtures.facts(story_fixtures.kind_event("winds_aloft_day"))
        self.assertEqual(
            public_prose.required_sentence(winds),
            "The wind itself was not measured; the difference in ground speeds is what the data show.",
        )
        foreign = story_fixtures.facts(story_fixtures.kind_event("foreign_military"))
        self.assertEqual(
            public_prose.required_sentence(foreign),
            "The listing comes from the aircraft database and the address block; the flight's own broadcasts do "
            "not say why it flew.",
        )
        self.assertNotRegex(public_prose.REQUIRED_SENTENCES["foreign_military"], public_prose.MISSION_CLAIM)
        # A listing whose facts do not say what marked it has no required sentence to carry.
        foreign["facts"] = [item for item in foreign["facts"] if item["id"] != "fm.basis"]
        self.assertIsNone(public_prose.required_sentence(foreign))
        for kind in ("winds_aloft_day", "foreign_military"):
            with self.subTest(kind=kind):
                facts, story = kind_story(kind)
                required = public_prose.required_sentence(facts)
                self.assertEqual(public_prose.carries(story["paragraphs"], required), 1)
                self.assertEqual(public_prose.story_issues(story, facts), [])
                self.assertIn(
                    "claim:limit_unhedged", public_prose.story_issues(without(story, required), facts)
                )

    def test_filler_frames_are_refused(self):
        facts, story = kind_story("watch_type")
        self.assertEqual(public_prose.story_issues(story, facts), [])
        for sentence in (
            "A reported altitude of 33,000 feet gives a sense of the flight.",
            "The double deck is a useful design detail to remember.",
            "It is worth noting that the record holds nine positions.",
            "The track adds some context to the reading.",
            "Its callsign rounds out the picture.",
            "The entry does not show who was aboard.",
        ):
            with self.subTest(sentence=sentence):
                issues = public_prose.story_issues(with_sentence(story, sentence), facts)
                self.assertIn("filler:frame", issues)
                self.assertNotIn(
                    "filler:frame",
                    public_prose.story_issues(with_sentence(story, sentence), facts, new=False),
                )
        self.assertIn(
            "filler:frame", public_prose.story_issues({**story, "dek": "It is worth noting."}, facts)
        )

    def test_box_facts_cannot_be_reused_in_ai_prose(self):
        profiles = story_fixtures.profile(
            "A388",
            "Airbus built the A380 with two full-length passenger decks.",
            "The A380 first flew in April 2005 and entered airline service two years later, in 2007.",
            publisher="Airbus",
        )
        facts = story_fixtures.facts(
            story_fixtures.watch_a380(), place=story_fixtures.PLACES["coleman"], profiles=profiles
        )
        story = {"paragraphs": ["It passed at 39,000 ft. The A380 has two decks."]}
        self.assertEqual(public_prose.box_issues(story, facts), [])
        # A short fact repeats when a sentence carries all of it; a longer one, ten words in a row.
        for sentence in (
            "Airbus built the A380 with two full-length passenger decks, the type box says.",
            "It first flew in April 2005 and entered airline service two years later.",
        ):
            with self.subTest(sentence=sentence):
                reused = {"paragraphs": [f"It passed at 39,000 ft. {sentence}"]}
                self.assertEqual(public_prose.box_issues(reused, facts), ["box:reused"])
        # A fact under six words never counts as repeated.
        self.assertFalse(public_prose.quotes("It passed. The A380 has two decks.", "The A380 has two decks."))
        self.assertTrue(
            public_prose.quotes("It passed. The A380 has two big decks.", "The A380 has two big decks.")
        )
        # The story checks leave the box to the AI writer's check: template text quotes it.
        template = editorial.render(facts, "episode")
        self.assertNotIn("box:reused", public_prose.story_issues(template, facts))

    def test_the_type_box_lists_the_profile_facts_the_text_does_not_quote(self):
        texts = (
            "Airbus built the A380 with two full-length passenger decks.",
            "The A380 first flew in April 2005 and entered airline service two years later, in 2007.",
            "Airbus delivered 251 A380s before the production line closed in 2021.",
        )
        profiles = story_fixtures.profile("A388", *texts, publisher="Airbus")
        facts = story_fixtures.facts(
            story_fixtures.watch_a380(), place=story_fixtures.PLACES["coleman"], profiles=profiles
        )
        context = editorial.context(facts)
        self.assertEqual(context["type"]["profile"], ["tp.A388.1", "tp.A388.2", "tp.A388.3"])
        self.assertEqual(public._routing(context)["type"]["profile"], context["type"]["profile"])
        template = editorial.render(facts, "episode")
        # A template story lists every profile fact, quoted or not, and cites each one's source.
        self.assertLessEqual(set(context["type"]["profile"]), set(template["facts"]))
        quoting = {
            **template,
            "paragraphs": [
                f"{template['paragraphs'][0]} {texts[0]}",
                f"{template['paragraphs'][-1]} {texts[1]}",
            ],
        }
        self.assertEqual(publication.type_box({"story": quoting, "context": context}), [texts[2]])
        plain = {**template, "paragraphs": ["HL7635 passed near Coleman, Michigan."]}
        self.assertEqual(publication.type_box({"story": plain, "context": context}), list(texts))
        # A record staged before the routing named the profile keeps the facts its story lists.
        legacy = {**quoting, "facts": {"tp.A388.1": texts[0], "tp.A388.2": texts[1]}}
        self.assertEqual(publication.type_box({"story": legacy, "context": {}}), [texts[0], texts[1]])
        self.assertEqual(publication.type_box({"story": legacy, "context": context}), [texts[0], texts[1]])

    def test_a_dek_adds_a_fact_the_headline_lacks(self):
        facts, story = kind_story("watch_type")
        self.assertEqual(public_prose.story_issues(story, facts), [])
        registration = story_facts.value(facts, "id.registration")
        echo = {
            **story,
            "headline": f"{registration} passes overhead",
            "dek": f"{registration} passes overhead.",
        }
        self.assertIn("dek:adds_nothing", public_prose.story_issues(echo, facts))
        self.assertNotIn("dek:adds_nothing", public_prose.story_issues(echo, facts, new=False))
        headline = f"{registration} passes"
        altitude = story_facts.value(facts, "m.alt_baro") or story_facts.value(facts, "m.archive_alt")
        # A figure, date, or name from the facts counts; another figure or a capitalized word does not.
        for dek, adds in (
            (f"It reported {altitude:,.0f} ft.", True),
            ("It flew near Coleman, Michigan.", True),
            ("It flew on 20 September.", True),
            ("It reported 123,456 ft.", False),
            ("Spotters Watched It Pass.", False),
            ("It passed in the evening.", False),
        ):
            with self.subTest(dek=dek):
                self.assertEqual(public_prose.adds_to(headline, dek, facts), adds)

    def test_every_kind_context_line_passes_quoted_verbatim_in_a_story_of_its_kind(self):
        for kind, line in story_facts.KIND_CONTEXT.items():
            with self.subTest(kind=kind):
                facts, story = kind_story(kind)
                self.assertEqual(public_prose.context_line(facts), line)
                self.assertEqual(public_prose.carries(story["paragraphs"], line), 1)
                self.assertEqual(public_prose.story_issues(story, facts), [])
                self.assertIn("context:missing", public_prose.story_issues(without(story, line), facts))
                self.assertNotIn(
                    "context:missing", public_prose.story_issues(without(story, line), facts, new=False)
                )

    def test_a_light_rephrase_of_the_context_line_counts_and_keeps_its_figures(self):
        line = story_facts.KIND_CONTEXT["thermal_soaring"]
        rephrase = "By circling in rising air, gliders and motor gliders gain height."
        self.assertTrue(public_prose.restates_context(line, line))
        self.assertTrue(public_prose.restates_context(rephrase, line))
        # A word the line lacks, or a dropped hedge or negation, is no light rephrase.
        self.assertFalse(
            public_prose.restates_context(
                "Gliders and motor gliders gain height by circling in air that rises.", line
            )
        )
        mismatch = story_facts.KIND_CONTEXT["type_speed_mismatch"]
        self.assertFalse(public_prose.restates_context(mismatch.replace(" usually", ""), mismatch))
        # A claim about this flight wrapped around the line is no rephrase.
        self.assertFalse(
            public_prose.restates_context(
                "This glider gained height by circling in rising air near the field, as gliders and motor gliders "
                "often do on warm afternoons.",
                line,
            )
        )
        self.assertFalse(
            public_prose.restates_context(
                "It climbed 3,000 feet in rising air.", "It climbed 2,000 feet in rising air."
            )
        )
        facts, story = kind_story("thermal_soaring")
        rephrased = {**story, "paragraphs": [text.replace(line, rephrase) for text in story["paragraphs"]]}
        self.assertNotIn("context:missing", public_prose.story_issues(rephrased, facts))

    def test_house_headline_forms(self):
        facts, story = kind_story("watchlist_match")
        registration = story_facts.value(facts, "id.registration")
        association = story_fixtures.WATCH_ASSOCIATION
        self.assertRegex(
            story["headline"],
            rf"^(?:Cessna )?Citation CJ4 {registration} linked to {association} by Plane Alert DB$",
        )
        listed = {
            **story,
            "headline": f"Cessna Citation CJ4 {registration}, listed under {association}, spotted",
        }
        self.assertNotIn("headline:watchlist_form", public_prose.story_issues(listed, facts))
        loose = {
            **story,
            "headline": f"Cessna Citation CJ4 {registration} flies for {association} near Coleman",
        }
        # The watchlist forms, old and new, hold for published text too.
        self.assertIn("headline:watchlist_form", public_prose.story_issues(loose, facts))
        self.assertIn("headline:watchlist_form", public_prose.story_issues(loose, facts, new=False))
        facts, story = kind_story("foreign_military")
        country = story_facts.value(facts, "fm.country")
        self.assertRegex(story["headline"], rf", listed as military from {country}, spotted")
        loose = {
            **story,
            "headline": story["headline"].replace(", listed as military from", ", a military aircraft of"),
        }
        self.assertIn("headline:foreign_military_form", public_prose.story_issues(loose, facts))
        # Published text keeps only the names: the aircraft, its type, and the State.
        self.assertNotIn("headline:foreign_military_form", public_prose.story_issues(loose, facts, new=False))
        registration = story_facts.value(facts, "id.registration")
        nameless = {**story, "headline": f"Military aircraft {registration} passes near Coleman, Michigan"}
        self.assertIn("headline:foreign_military_form", public_prose.story_issues(nameless, facts, new=False))
        untyped = {**story, "headline": f"{registration}, listed as military from {country}, spotted"}
        self.assertIn("headline:foreign_military_form", public_prose.story_issues(untyped, facts))

    def test_a_context_line_with_a_claim_wrapped_around_it_is_refused(self):
        cases = (
            ("watchlist_match", "and the governor was aboard"),
            ("identity_conflict", "and this disagreement is a database error"),
            ("vintage", "and this one flew to an air show from a private strip"),
            ("racetrack_orbit", "as with tankers refueling fighters"),
        )
        for kind, claim in cases:
            with self.subTest(kind=kind):
                facts, story = kind_story(kind)
                line = story_facts.KIND_CONTEXT[kind]
                wrapped = f"{line[:-1]}, {claim}."
                changed = {
                    **story,
                    "paragraphs": [text.replace(line, wrapped) for text in story["paragraphs"]],
                }
                self.assertFalse(public_prose.restates_context(wrapped, line))
                issues = public_prose.story_issues(changed, facts)
                self.assertTrue([issue for issue in issues if issue != "context:missing"], (wrapped, issues))
        # A line as written still answers to the occupant, purpose, and hypothesis checks.
        facts, _story = kind_story("watchlist_match")
        indexed, failure = public_prose._facts_for(facts)
        self.assertTrue(
            public_prose._context_claim_issues(["The governor was aboard."], indexed, failure, facts)
        )

    def test_a_likeness_compound_hedges_only_its_own_hypothesis(self):
        facts, story = kind_story("aerobatic_practice")
        practice = with_sentence(story, "It flew six reversals of aerobatic-style practice.")
        self.assertEqual(public_prose.story_issues(practice, facts), [])
        facts, story = kind_story("racetrack_orbit")
        hold = with_sentence(story, "It flew a long racetrack hold in an airline-style pattern.")
        self.assertIn("claim:unhedged_hypothesis", public_prose.story_issues(hold, facts))
        # A refueling-style track stated as fact is a mission claim, as before; offered hedged, it is
        # one of the kind's listed possibilities.
        stated = with_sentence(story, "It flew a refueling-style track.")
        self.assertTrue(
            [issue for issue in public_prose.story_issues(stated, facts) if "Unhedged mission claim" in issue]
        )
        offered = with_sentence(story, "One explanation may be a refueling-style track.")
        self.assertEqual(public_prose.story_issues(offered, facts), [])

    def test_a_likeness_compound_covers_its_own_phrase_and_not_the_sentence(self):
        facts, story = kind_story("survey_grid")
        # The compound hedges its phrase; the same hypothesis stated again in the sentence is not.
        both = with_sentence(story, "It flew survey-style legs that were mapping the fields.")
        self.assertIn("claim:unhedged_hypothesis", public_prose.story_issues(both, facts))
        alone = with_sentence(story, "It flew survey-style legs over the fields.")
        self.assertNotIn("claim:unhedged_hypothesis", public_prose.story_issues(alone, facts))
        self.assertEqual(
            public_prose.unhedged_hypotheses(
                ["Six aerobatic-style climbs, then aerobatic practice."], ["aerobatic practice"]
            ),
            [("Six aerobatic-style climbs, then aerobatic practice.", "aerobatic practice")],
        )
        self.assertEqual(
            public_prose.unhedged_hypotheses(
                ["Six reversals of aerobatic-style practice."], ["aerobatic practice"]
            ),
            [],
        )

    def test_a_dek_adds_a_figure_only_when_the_whole_figure_is_a_fact(self):
        facts = {
            "facts": [{"id": "m.alt", "value": 200, "unit": "ft"}, {"id": "m.len", "value": 5, "unit": "NM"}]
        }
        # A fragment of a larger figure, after its grouping comma or decimal point, is no fact.
        self.assertFalse(public_prose.adds_to("N1 passes", "It reported 1,200 ft.", facts))
        self.assertFalse(public_prose.adds_to("N1 passes", "Its legs ran 12.5 NM.", facts))
        self.assertTrue(public_prose.adds_to("N1 passes", "It reported 200 ft.", facts))
        self.assertTrue(public_prose.adds_to("N1 passes", "Its legs ran 5 NM.", facts))

    def test_one_run_detector_serves_the_box_and_the_required_sentence(self):
        fact = "The Bell 407 is a light single-engine utility helicopter with four main rotor blades."
        self.assertTrue(
            public_prose.quotes(
                "It is a light single-engine utility helicopter with four main rotor blades.", fact
            )
        )
        self.assertFalse(public_prose.quotes("It is a light helicopter.", fact))
        self.assertFalse(
            public_prose.quotes("A light single-engine utility helicopter.", "Five words only here now.")
        )
        runs = public_prose.word_runs("The wind itself was not measured.", 4)
        self.assertTrue(public_prose.shares_run("Again, the wind itself was not measured here.", runs, 4))
        self.assertTrue(public_prose.shares_run("the wind itself was gone", "The wind itself was not.", 4))
        self.assertFalse(public_prose.shares_run("The wind was not measured.", runs, 4))

    def test_one_long_date_form_and_one_home_for_the_hypothesis_wording(self):
        from datetime import date, datetime, timezone

        from wtf_adsb_publisher import public_writer

        self.assertEqual(public_prose.long_date("2026-11-05"), "5 November 2026")
        self.assertEqual(public_prose.long_date("2026-11-05T23:00:00Z"), "5 November 2026")
        self.assertEqual(public_prose.long_date(date(2026, 1, 2)), "2 January 2026")
        self.assertEqual(public_prose.long_date(datetime(2026, 1, 2, tzinfo=timezone.utc)), "2 January 2026")
        self.assertIsNone(public_prose.long_date(None))
        self.assertIs(editorial.long_date, public_prose.long_date)
        self.assertEqual(
            public_writer.OPERATOR_LABEL_SENTENCE.format(designator="QFA"),
            editorial.BUILDERS["fs_label"][0].format(fs_designator="QFA"),
        )
        for builder, kind in (("aer_hyp", "aerobatic_practice"), ("unr_hyp", "unregistered_address")):
            listed = public_prose.series(story_facts.HYPOTHESES[kind], "or")
            for text in editorial.BUILDERS[builder]:
                with self.subTest(builder=builder):
                    self.assertIn(listed.lower(), text.lower())

    def test_the_shared_checks_serve_the_research_prose(self):
        # Researched prose offers a hypothesis by its whole phrase.
        self.assertEqual(
            public_prose.unhedged_hypotheses(
                ["The registry answered."], ["a stale registry entry"], whole=True
            ),
            [],
        )
        self.assertTrue(
            public_prose.unhedged_hypotheses(
                ["A stale registry entry pairs it."], ["a stale registry entry"], whole=True
            )
        )
        self.assertTrue(public_prose.is_causeless("outlier_gs"))
        self.assertFalse(public_prose.is_causeless("circling"))
        self.assertFalse(public_prose.is_causeless(None))
        self.assertEqual(
            public_prose.unlisted_names("Boeing built it near Wichita.", {"boeing"}), ["Wichita"]
        )
        self.assertEqual(public_prose.unlisted_names("It was a B738.", set()), [])
        self.assertEqual(public_prose.unlisted_names("It was a B738.", set(), strict=True), ["B738"])
        # Researched prose may name nobody: the strict mode drops the story rule's exemptions.
        vocabulary = {"the", "records", "show", "first", "operator", "was", "in", "service", "he", "said"}
        vocabulary |= {"boeing", "jets", "flew", "it", "ordered", "list", "air", "as", "often"}
        for sentence, name in (
            ("The records show: Smith flew it often.", "Smith"),
            ("The first operator was (Lufthansa) in service.", "Lufthansa"),
            ("Lufthansa Boeing jets flew it.", "Lufthansa"),
            ('He said "Delta" ordered it.', "Delta"),
            ("Records list Air-france as operator.", "Air-france"),
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(name, public_prose.unlisted_names(sentence, vocabulary, strict=True))
                self.assertNotIn(name, public_prose.unlisted_names(sentence, vocabulary))
        self.assertEqual(public_prose.unlisted_names("Records show it.", vocabulary, strict=True), [])
        # The weekly modules reach no private helper of the story checks.
        for name in ("weekly_spotlight.py", "weekly_research.py"):
            source = (Path(__file__).resolve().parents[1] / "src" / "wtf_adsb_publisher" / name).read_text()
            self.assertIsNone(re.search(r"\b(?:prose|public_prose)\._\w", source), name)
        # The compiled hypothesis patterns are built once per hypothesis.
        public_prose._hypothesis_patterns.cache_clear()
        for _ in range(3):
            public_prose.unhedged_hypotheses(["A plain sentence."], ["aerial survey or mapping"])
        self.assertEqual(public_prose._hypothesis_patterns.cache_info().misses, 1)

    def test_house_headlines_share_their_skeleton_without_a_variety_issue(self):
        for kind in ("watchlist_match", "foreign_military"):
            with self.subTest(kind=kind):
                facts, story = kind_story(kind)
                recent = [story_fixtures.recent_entry(story, facts)]
                again = editorial.render_with_report(facts, "episode-2", recent=recent)[1]
                self.assertNotIn("headline_similar", again.issues)

    def test_mismatch_and_new_kind_hypotheses_are_offered_hedged(self):
        self.assertIn("a stale registry entry", story_facts.HYPOTHESES["type_speed_mismatch"])
        self.assertEqual(
            story_facts.HYPOTHESES["type_altitude_mismatch"], story_facts.HYPOTHESES["type_speed_mismatch"]
        )
        for kind in ("winds_aloft_day", "aerobatic_practice", "unregistered_address", "racetrack_orbit"):
            self.assertTrue(story_facts.HYPOTHESES[kind], kind)
        facts, story = kind_story("type_speed_mismatch")
        self.assertEqual(public_prose.story_issues(story, facts), [])
        hedged = with_sentence(story, "One explanation may be a stale registry entry.")
        self.assertEqual(public_prose.story_issues(hedged, facts), [])
        bare = with_sentence(story, "The address carries a stale registry entry.")
        self.assertIn("claim:unhedged_hypothesis", public_prose.story_issues(bare, facts))
        # The fixed winds headline is the engine's, never an unhedged hypothesis.
        facts, story = kind_story("winds_aloft_day")
        self.assertTrue(story["headline"].startswith("Strong winds aloft on "))
        self.assertEqual(public_prose.story_issues(story, facts), [])

    def test_turn_rate_forms(self):
        check = public_prose.prose_checker(
            story_fixtures.facts(story_fixtures.kind_event("aerobatic_practice"))
        )
        for unit in ("degrees a second", "degrees each second", "degree a second"):
            with self.subTest(unit=unit):
                self.assertEqual(check(f"It turned at 24 {unit}."), [])
        self.assertTrue(check("It turned at 31 degrees a second."))


if __name__ == "__main__":
    unittest.main()
