"""Certified-limit stories from imported evidence: gates, packet, card, facts, prose, writer, slug, and site.

The comparisons come from story_fixtures.rated_event(), which builds them as the
engine's detector does. The publication gates run on the imported view
(inbox_support.inbox) through autopilot.collect_state and autopilot.stage; the
detector, wind, and type-check workers that write the comparison stay with the
engine. No network is used.
"""

import importlib.util
import itertools
import json
import re
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
import story_fixtures as fixtures
from inbox_support import inbox
from test_editorial import candidates

from wtf_adsb_publisher import (
    autopilot,
    curation,
    editorial,
    limits,
    public_prose,
    public_writer,
    publication,
    reference,
    rules,
    selection,
    stories,
    story_facts,
    story_urls,
)
from wtf_adsb_publisher import public_events as public

KINDS = ("above_rated_altitude", "above_rated_speed")
# The hedges the checks require word for word (public_prose.REQUIRED_SENTENCES).
ALTITUDE_HEDGE = public_prose.REQUIRED_SENTENCES["above_rated_altitude"]
ESTIMATE_HEDGE, REPORTED_HEDGE = (
    public_prose.REQUIRED_SENTENCES["above_rated_speed"].format(
        airspeed_origin=public_prose.AIRSPEED_ORIGINS[basis]
    )
    for basis in ("derived", "telemetry")
)
# The agreeing registry sentence, naming the type unless the sentence before it did.
REGISTRY_AGREED = "The aircraft database and adsbdb both list it as a Boeing 737-800."
REGISTRY_UNNAMED = "A second registry, adsbdb, lists the same type."
# Receiver-relative fields, and the positions and times the wind service was asked for.
PRIVATE_KEYS = {"lat", "lon", "r_dst", "r_dir", "distance", "bearing", "fetched_at", "attribution", "samples"}


def walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_keys(item)


def rated(kind, *, reported=False, start="2026-09-20T14:00:00Z", **options):
    common = {"hexcode": "a1c2e3", "registration": "N8417Q", "callsign": None}
    return fixtures.rated_event(kind, fixtures.stamp(start), common, reported=reported, **options)


def facts_for(raw, place=None):
    return fixtures.facts(raw, place=place)


def sentences(story):
    return [
        paragraph[start:end].strip()
        for paragraph in story["paragraphs"]
        for start, end in public_prose.sentence_spans(paragraph)
    ]


class RatedPublicationGateTests(unittest.TestCase):
    """A sustained reading past a certified limit, from the imported view to a staged story."""

    @staticmethod
    def candidate(event):
        return {
            "key": f"live-{event['id']}",
            "source": event,
            "interval": (event["first_seen"], event["last_seen"]),
        }

    @staticmethod
    def collect(event):
        """The candidates autopilot.collect_state chooses from the engine's export of `event`."""
        now = event["last_seen"] + 3600
        return autopilot.collect_state(inbox(RatedPublicationGateTests.candidate(event), now=now), now)

    def stage(self, candidate):
        ledger = public.new_ledger("2026-09-01")
        source = candidate["source"]
        result = autopilot.stage(ledger, [candidate], at=public.epoch_utc(source["last_seen"] + 3600))
        return ledger, result

    @staticmethod
    def pending():
        """A speed finding before the modelled wind arrives: the detector's own block, verdict pending."""
        event = rated("above_rated_speed")
        aircraft = {"hex": event["hex"], "t": "B738", "type": "adsb_icao", "track": 90.0}
        finding = rules._rated_speed(aircraft, 530.0, 37000.0, reference.limits("B738"), None)
        event["evidence"]["rated_limit"] = finding.evidence["rated_limit"]
        return event

    def assert_blocked(self, event, rule):
        self.assertIn(rule, selection.assess(event, {})["blocking_rules"])
        self.assertFalse(selection.publishable(event))
        self.assertEqual(self.collect(event), [])
        ledger, result = self.stage(self.candidate(event))
        self.assertEqual((result, ledger["records"]), ({"changed": 0, "rejected": 1}, []))

    def test_each_kind_reaches_a_staged_story_with_an_agreeing_type_check(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                event = rated(kind)
                self.assertEqual(event["evidence"]["rated_limit"]["verdict"], "exceeded")
                decision = selection.assess(event, {})
                self.assertEqual(
                    (decision["blocking_rules"], decision["publication_hold"]),
                    ([], None),
                    decision["reasons"],
                )
                self.assertTrue(decision["story_eligible"])
                self.assertEqual(curation.topic(kind), "performance")
                self.assertTrue(selection.publishable(event))
                chosen = self.collect(event)
                self.assertEqual([item["source"]["id"] for item in chosen], [event["id"]])
                ledger, result = self.stage(chosen[0])
                self.assertEqual(result, {"changed": 1, "rejected": 0})
                record = ledger["records"][0]
                packet = record["evidence"]
                self.assertEqual(packet["kind"], kind)
                self.assertEqual(packet["type_check"], {"state": "agrees", "source": "adsbdb"})
                self.assertEqual(packet["rated_limit"]["sustained"]["reports"], 6)
                event_public = public._projection(packet, record["editorial"])
                keys = {item["key"] for item in event_public["measurements"]}
                self.assertIn("type_check", keys)
                self.assertTrue({"rated_reports", "rated_seconds"} <= keys)
                story = record["editorial"]["story"]
                facts = autopilot.editorial_inputs(
                    {"records": []}, packet, record["episode_key"], points=autopilot._points(event)
                )["facts"]
                self.assertEqual(story["facts_digest"], facts["digest"])
                self.assertEqual(public_prose.story_issues(story, facts), [])
                text = " ".join(sentences(story))
                self.assertIn("The type certificate data sheet lists a maximum operating", text)
                self.assertIn(ALTITUDE_HEDGE if kind == "above_rated_altitude" else ESTIMATE_HEDGE, text)
                slug = public.article_slug(record["editorial"]["article_path"])
                self.assertRegex(
                    slug,
                    "-above-certified-ceiling-"
                    if kind == "above_rated_altitude"
                    else "-above-maximum-speed-",
                )

    def test_a_pending_verdict_never_publishes(self):
        # No wind answer yet: the candidate's verdict stays pending.
        event = self.pending()
        self.assertEqual(event["evidence"]["rated_limit"]["verdict"], "pending")
        self.assert_blocked(event, "rated_verdict")

    def test_a_reading_within_the_limit_after_the_wind_never_publishes(self):
        # A 40-knot tailwind leaves 490 KTAS: about 4% over Mmo, under the 10% that counts.
        event = rated("above_rated_speed", winds=[(40.0, 270.0)] * 6)
        self.assertEqual(event["evidence"]["rated_limit"]["verdict"], "within")
        self.assert_blocked(event, "rated_verdict")

    def test_archive_findings_build_the_same_packet_shape(self):
        # The frozen corpus and archive replay save the detector's block without a live
        # confirmation window: the packet keeps its shape, with no sustained summary,
        # and selection never publishes it.
        event = rated("above_rated_altitude")
        live = stories.evidence_packet(event)
        archived = deepcopy(event)
        archived["evidence"].pop("confirmation")
        archived["evidence"]["historical_corpus"] = {"day": "2026-10-04"}
        packet = stories.evidence_packet(archived)
        self.assertEqual(set(packet["rated_limit"]), set(live["rated_limit"]))
        self.assertIsNone(packet["rated_limit"]["sustained"])
        self.assertEqual(packet["type_check"], live["type_check"])
        self.assertEqual(
            public._projection(packet, public.PLACEHOLDER_EDITORIAL)["kind"], "above_rated_altitude"
        )
        self.assertFalse(selection.publishable(archived))
        self.assertEqual(self.collect(archived), [])

    def test_an_exceeded_verdict_without_its_comparison_is_held_for_review(self):
        # Reports whose airspeed's origin was not kept: no story could state the comparison,
        # so curation refuses the finding instead of taking a daily slot it cannot fill.
        event = rated("above_rated_speed")
        for sample in event["evidence"]["rated_limit"]["samples"]:
            sample["airspeed_basis"] = None
        self.assertEqual(event["evidence"]["rated_limit"]["verdict"], "exceeded")
        self.assertIsNone(limits.comparison("above_rated_speed", event["evidence"]))
        self.assert_blocked(event, "rated_incomplete")


class RatedPacketTests(unittest.TestCase):
    def test_the_packet_carries_the_comparison_and_a_reduced_type_check(self):
        common = {"field", "limit_name", "limit", "verdict", "type", "source", "sustained"}
        speed_only = {"mmo"}
        for kind in KINDS:
            with self.subTest(kind=kind):
                packet = stories.evidence_packet(fixtures.kind_event(kind))
                block = packet["rated_limit"]
                self.assertEqual(set(block), common | (speed_only if kind == "above_rated_speed" else set()))
                self.assertEqual(set(block["source"]), {"title", "url"})
                self.assertEqual(packet["type_check"], {"state": "agrees", "source": "adsbdb"})
                self.assertFalse(PRIVATE_KEYS & set(walk_keys(block)))
                self.assertEqual((block["sustained"]["reports"], block["sustained"]["seconds"]), (6, 75))
        speed = stories.evidence_packet(fixtures.kind_event("above_rated_speed"))["rated_limit"]
        # The airspeed's origin and its modelled wind are the decisive report's, kept once.
        self.assertEqual(
            speed["sustained"]["wind"],
            {"speed_kt": 8.0, "direction_deg": 270.0, "level_hpa": 200.0, "source": "open-meteo"},
        )
        self.assertEqual(speed["sustained"]["airspeed_basis"], "modelled_wind")
        # Mmo binds at FL370: 522 KTAS is Mach 0.91, 10.9% over 0.82 (a floor, never rounded up).
        self.assertEqual(
            {key: speed["sustained"][key] for key in ("airspeed_kt", "binding_limit", "margin_percent")},
            {"airspeed_kt": 522.0, "binding_limit": "mmo", "margin_percent": 10.9},
        )
        reported = stories.evidence_packet(rated("above_rated_speed", reported=True))["rated_limit"]
        self.assertEqual(
            (reported["sustained"]["airspeed_basis"], reported["sustained"]["wind"]), ("reported", None)
        )

    def test_an_airspeed_of_unknown_origin_gives_no_comparison(self):
        raw = fixtures.kind_event("above_rated_speed")
        for sample in raw["evidence"]["rated_limit"]["samples"]:
            sample["airspeed_basis"] = None
        packet = stories.evidence_packet(raw)
        self.assertIsNone(packet["rated_limit"]["sustained"])
        event = public._projection(packet, public.PLACEHOLDER_EDITORIAL)
        self.assertFalse(
            {"rated_estimated_tas_kt", "rated_reported_tas_kt", "rated_margin_percent", "wind_speed_kt"}
            & {item["key"] for item in event["measurements"]}
        )
        self.assertNotIn("lim.airspeed", story_facts.fact_map(story_facts.build(packet, event)))

    def test_a_report_within_the_limit_is_never_counted(self):
        # The second report is 490 kn over the ground, 482 KTAS after the tailwind: within the
        # limit. It ends the first report's run; the five after it are the sustained run.
        speeds = [530, 490, 530, 530, 530, 530, 530]
        packet = stories.evidence_packet(rated("above_rated_speed", speeds=speeds))
        sustained = packet["rated_limit"]["sustained"]
        self.assertEqual((sustained["reports"], sustained["ground_speed_kt"]), (5, 530))
        story, report, _facts = RatedStoryTests.render(None, rated("above_rated_speed", speeds=speeds))
        self.assertTrue(report.publishable)
        text = " ".join([story["dek"], *story["paragraphs"]])
        self.assertNotRegex(text, r"\bsix\b")
        self.assertNotRegex(text, r"(?i)\bheld\b")

    def test_the_decisive_report_gives_one_calculation(self):
        # A turn into a 60-knot westerly: 590 kn eastbound is 530 KTAS, 535 kn southbound is
        # 538 KTAS. The report closest to the limit is the eastbound one, and the story's ground
        # speed, wind, and airspeed are all that report's.
        raw = rated(
            "above_rated_speed",
            speeds=[590, 590, 590, 535, 535, 535],
            tracks=[90, 90, 90, 180, 180, 180],
            winds=[(60, 270)] * 6,
        )
        sustained = stories.evidence_packet(raw)["rated_limit"]["sustained"]
        self.assertEqual(
            {key: sustained[key] for key in ("ground_speed_kt", "decisive_ground_speed_kt", "airspeed_kt")},
            {"ground_speed_kt": 535, "decisive_ground_speed_kt": 590, "airspeed_kt": 530},
        )
        self.assertEqual(sustained["wind"]["speed_kt"], 60)
        story, report, _facts = RatedStoryTests.render(None, raw)
        self.assertTrue(report.publishable)
        text = " ".join(story["paragraphs"])
        self.assertRegex(
            text,
            r"modelled 60-knot wind, a ground speed of 590 knots works out to an estimated airspeed of 530 knots|590 knots over the ground; with a modelled 60-knot wind taken out, that is an estimated airspeed of 530 knots",
        )
        # Mixed altitudes: the margin is a floor over every report, from the one closest to the limit.
        mixed = stories.evidence_packet(
            rated("above_rated_speed", speeds=[530] * 3 + [545] * 3, levels=[37000] * 3 + [30000] * 3)
        )["rated_limit"]["sustained"]
        self.assertEqual((mixed["altitude_ft"], mixed["margin_percent"]), (37000, 10.9))

    def test_a_comparison_for_another_type_or_kind_is_left_out(self):
        raw = fixtures.kind_event("above_rated_altitude")
        raw["evidence"]["aircraft"]["t"] = "A320"
        self.assertNotIn("rated_limit", stories.evidence_packet(raw))
        other = fixtures.kind_event("high")
        other["evidence"]["rated_limit"] = fixtures.kind_event("above_rated_altitude")["evidence"][
            "rated_limit"
        ]
        other["evidence"]["type_check"] = dict(fixtures.AGREEING_CHECK)
        packet = stories.evidence_packet(other)
        self.assertNotIn("rated_limit", packet)
        self.assertNotIn("type_check", packet)

    def test_the_card_shows_reference_rows_and_the_window_over_the_limit(self):
        raw = fixtures.kind_event("above_rated_speed")
        packet = stories.evidence_packet(raw)
        event = public._projection(packet, public.PLACEHOLDER_EDITORIAL)
        # Six reports 15 s apart: the window over the limit is the whole confirmation.
        window = (public.epoch_utc(raw["first_seen"]), public.epoch_utc(raw["first_seen"] + 75))
        rows = {
            item["key"]: item
            for item in event["measurements"]
            if item["scope"] in {"reference-data", "analysis-window"}
        }
        self.assertEqual(
            {key for key, item in rows.items() if item["scope"] == "reference-data"},
            {
                "rated_vmo_kias",
                "rated_mmo",
                "wind_speed_kt",
                "wind_direction_deg",
                "wind_level_hpa",
                "type_check",
            },
        )
        # Under Mmo the comparison is the Mach number, so no equivalent airspeed is shown.
        self.assertEqual(
            {key for key, item in rows.items() if item["scope"] == "analysis-window"},
            {
                "rated_lowest_gs_kt",
                "rated_decisive_gs_kt",
                "rated_estimated_tas_kt",
                "rated_mach",
                "rated_decisive_alt_ft",
                "rated_margin_percent",
                "rated_reports",
                "rated_seconds",
            },
        )
        self.assertEqual((rows["rated_vmo_kias"]["value"], rows["rated_vmo_kias"]["unit"]), (340, "KIAS"))
        self.assertEqual(rows["type_check"]["value"], "adsbdb")
        self.assertEqual(rows["type_check"]["label"], "Type confirmed by a second source")
        for item in rows.values():
            if item["scope"] == "reference-data":
                self.assertFalse({"at_utc", "start_utc", "end_utc"} & set(item))
            else:
                self.assertEqual((item["start_utc"], item["end_utc"]), window)
        self.assertIn(public.RATED_SPEED, event["limitations"])
        self.assertIn(public.WIND_ATTRIBUTION, event["limitations"])
        # The registry's own registration never reaches the card.
        self.assertNotIn(raw["evidence"]["type_check"]["registration"], json.dumps(event["measurements"]))
        altitude = public._projection(
            stories.evidence_packet(fixtures.kind_event("above_rated_altitude")), public.PLACEHOLDER_EDITORIAL
        )
        keys = {item["key"] for item in altitude["measurements"]}
        self.assertTrue(
            {"rated_max_altitude_ft", "rated_lowest_alt_ft", "rated_margin_ft", "type_check"} <= keys
        )
        self.assertFalse({"wind_speed_kt", "rated_mmo"} & keys)
        self.assertIn(public.RATED_ALTITUDE, altitude["limitations"])
        self.assertNotIn(public.WIND_ATTRIBUTION, altitude["limitations"])
        reported = public._projection(
            stories.evidence_packet(rated("above_rated_speed", reported=True)), public.PLACEHOLDER_EDITORIAL
        )
        keys = {item["key"] for item in reported["measurements"]}
        self.assertIn("rated_reported_tas_kt", keys)
        self.assertFalse({"rated_estimated_tas_kt", "wind_speed_kt"} & keys)
        self.assertNotIn(public.WIND_ATTRIBUTION, reported["limitations"])
        # Under Vmo, low down, the equivalent airspeed is the figure the limit is stated in.
        low = public._projection(stories.evidence_packet(vmo_bound()), public.PLACEHOLDER_EDITORIAL)
        self.assertIn("rated_equivalent_kt", {item["key"] for item in low["measurements"]})

    def test_only_an_agreeing_check_is_shown_and_a_missing_block_is_refused(self):
        raw = fixtures.kind_event("above_rated_altitude")
        raw["evidence"]["type_check"] = {
            **fixtures.AGREEING_CHECK,
            "state": "disagrees",
            "type_code": "E295",
        }
        event = public._projection(stories.evidence_packet(raw), public.PLACEHOLDER_EDITORIAL)
        self.assertNotIn("type_check", {item["key"] for item in event["measurements"]})
        packet = stories.evidence_packet(fixtures.kind_event("above_rated_altitude"))
        packet.pop("rated_limit")
        with self.assertRaisesRegex(ValueError, "certified-limit comparison"):
            public._projection(packet, public.PLACEHOLDER_EDITORIAL)
        # A structural read keeps the record readable without the rows.
        structural = public._projection(packet, public.PLACEHOLDER_EDITORIAL, strict=False)
        self.assertFalse(
            {"rated_reports", "type_check"} & {item["key"] for item in structural["measurements"]}
        )

    def test_the_snapshot_validates_the_rows_and_refuses_altered_ones(self):
        ledger = public.new_ledger("2026-09-01")
        for kind in KINDS:
            packet = stories.evidence_packet(fixtures.kind_event(kind))
            editorial_fields = {
                "title": f"Fixture {kind}",
                "summary": "A synthetic certified-limit fixture.",
                "article_path": f"/stories/boeing-737-800-{kind.replace('_', '-')}-20-sep-2026/",
                "synthetic": True,
            }
            public.put_event(ledger, kind, packet, editorial_fields, at="2026-09-21T00:00:00Z")
        snapshot = public.export_snapshot(ledger, mode="review", as_of="2026-09-21T00:00:00Z")
        self.assertEqual(public.validate_snapshot(snapshot)["snapshot_id"], snapshot["snapshot_id"])

        def altered(kind, change):
            value = deepcopy(snapshot)
            event = next(item for item in value["events"] if item["kind"] == kind)
            change(event)
            event["evidence_version"] = public.evidence_version(event)
            value["search"] = public._search(value["events"])
            value.pop("snapshot_id")
            value["snapshot_id"] = public._digest(value)
            return value

        def row(event, key):
            return next(item for item in event["measurements"] if item["key"] == key)

        def moved(event):
            speed = next(item for item in snapshot["events"] if item["kind"] == "above_rated_speed")
            event["measurements"].append(deepcopy(row(speed, "wind_speed_kt")))

        speed, altitude = "above_rated_speed", "above_rated_altitude"
        for kind, change, message in (
            (speed, lambda event: row(event, "type_check").update(value="N8ROLE"), "type-check source"),
            (speed, lambda event: row(event, "type_check").update(value=["adsbdb"]), "type-check source"),
            (speed, lambda event: row(event, "rated_mmo").update(at_utc=event["observed_start"]), "timed"),
            (speed, lambda event: row(event, "rated_mmo").update(value=-0.82), "out of range"),
            (speed, lambda event: row(event, "wind_direction_deg").update(value=999), "out of range"),
            (speed, lambda event: row(event, "rated_reports").update(value=-6), "never negative"),
            (speed, lambda event: row(event, "rated_mach").pop("end_utc"), "keeps its window"),
            (speed, lambda event: row(event, "wind_speed_kt").update(label="Wind"), "label or unit"),
            (speed, lambda event: event["limitations"].remove(public.WIND_ATTRIBUTION), "attribution"),
            (altitude, moved, "Unknown public measurement"),
            (
                altitude,
                lambda event: event["measurements"].append(deepcopy(row(event, "rated_max_altitude_ft"))),
                "appears twice",
            ),
            (speed, lambda event: event.update(kind="high", primary_type="performance"), "Unknown public"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                public.validate_snapshot(altered(kind, change))

    def test_the_evidence_version_follows_the_comparison(self):
        packet = stories.evidence_packet(fixtures.kind_event("above_rated_speed"))
        first = public._projection(packet, public.PLACEHOLDER_EDITORIAL)["evidence_version"]
        packet["type_check"]["source"] = "hexdb"
        self.assertNotEqual(
            public._projection(packet, public.PLACEHOLDER_EDITORIAL)["evidence_version"], first
        )


def vmo_bound(**options):
    """A 737-800 at FL200, where its Vmo (340 KIAS, about 466 KTAS) binds before its Mmo."""
    return rated("above_rated_speed", speeds=[560.0] * 6, levels=[20000.0] * 6, **options)


class RatedFactTests(unittest.TestCase):
    def values(self, raw):
        return {item["id"]: item for item in facts_for(raw)["facts"]}

    def test_altitude_facts(self):
        raw = fixtures.kind_event("above_rated_altitude")
        built = facts_for(raw)
        facts = {item["id"]: item for item in built["facts"]}
        self.assertEqual(
            {
                key: (item["value"], item.get("unit"), item["basis"])
                for key, item in facts.items()
                if key.startswith(("lim.", "tc."))
            },
            {
                "lim.limit": (41000, "ft", "reference"),
                "lim.reading": (42400, "ft", "telemetry"),
                "lim.margin": (1400, "ft", "derived"),
                "lim.reports": (6, "reports", "derived"),
                "lim.seconds": (75, "s", "derived"),
                "tc.source": ("adsbdb", None, "database"),
            },
        )
        self.assertEqual(facts["lim.limit"]["source"], "src.limits.B738")
        self.assertEqual(facts["lim.reading"]["field"], "alt_baro")
        # The agreeing registry is cited as a mismatch story's is (story_facts._registry_check).
        self.assertEqual(
            (facts["tc.source"]["source"], facts["tc.source"]["state"]), ("src.adsbdb", "agrees")
        )
        self.assertEqual(
            built["sources"],
            [
                {
                    "id": "src.adsbdb",
                    "publisher": "adsbdb",
                    "title": "adsbdb JSON API record for this address",
                    "url": story_facts.REGISTRY_PAGES["adsbdb"].format(hex=raw["hex"].upper()),
                },
                {
                    "id": "src.limits.B738",
                    "publisher": "European Union Aviation Safety Agency",
                    "title": "EASA Type Certificate Data Sheet IM.A.120 (Boeing 737), Issue 29",
                    "url": "https://www.easa.europa.eu/en/downloads/7297/en",
                },
            ],
        )
        # The card's rated rows are facts only as lim.* facts, each with its source.
        self.assertFalse([key for key in facts if key.startswith("m.rated_") or key == "m.type_check"])

    def test_speed_facts_come_from_the_decisive_report_and_cite_the_wind_service(self):
        built = facts_for(fixtures.kind_event("above_rated_speed"))
        facts = {item["id"]: item for item in built["facts"]}
        self.assertEqual(
            (facts["lim.limit"]["value"], facts["lim.limit"]["unit"], facts["lim.limit"]["field"]),
            (0.82, "Mach", "mmo"),
        )
        self.assertEqual(
            (facts["lim.airspeed"]["value"], facts["lim.airspeed"]["field"], facts["lim.airspeed"]["basis"]),
            (522.0, "estimated_airspeed", "derived"),
        )
        self.assertEqual((facts["lim.reading"]["value"], facts["lim.decisive_gs"]["value"]), (530, 530))
        self.assertEqual((facts["lim.mach"]["value"], facts["lim.margin"]["value"]), (0.91, 10.9))
        self.assertEqual((facts["lim.wind"]["value"], facts["lim.wind"]["direction"]), (8.0, 270.0))
        # Under Mmo the Mach number is the comparison; no equivalent airspeed is given.
        self.assertNotIn("lim.indicated", facts)
        for key in ("lim.airspeed", "lim.mach", "lim.wind"):
            self.assertEqual(facts[key]["source"], story_facts.OPEN_METEO["id"], key)
        self.assertIn(story_facts.OPEN_METEO, built["sources"])

    def test_a_reported_airspeed_is_the_aircrafts_own(self):
        facts = self.values(rated("above_rated_speed", reported=True))
        self.assertEqual(
            (facts["lim.airspeed"]["field"], facts["lim.airspeed"]["basis"]), ("tas", "telemetry")
        )
        self.assertNotIn("source", facts["lim.airspeed"])
        self.assertNotIn("lim.wind", facts)

    def test_vmo_binds_low_down_and_the_check_must_agree(self):
        raw = vmo_bound()
        self.assertEqual(raw["evidence"]["rated_limit"]["verdict"], "exceeded")
        facts = self.values(raw)
        self.assertEqual(
            (facts["lim.limit"]["value"], facts["lim.limit"]["unit"], facts["lim.limit"]["field"]),
            (340, "kn", "vmo"),
        )
        # The margin is the equivalent airspeed over Vmo, which a reader can check.
        indicated, margin = facts["lim.indicated"]["value"], facts["lim.margin"]["value"]
        self.assertAlmostEqual(margin, (indicated / 340 - 1) * 100, delta=0.2)
        raw["evidence"]["type_check"] = {**fixtures.AGREEING_CHECK, "state": "unavailable"}
        self.assertNotIn("tc.source", self.values(raw))

    def test_the_registry_is_cited_only_beside_a_comparison_that_stands(self):
        """A rated story names the agreeing registry only when its identity is unconflicted and
        its certified figure comes from a cited sheet."""
        raw = fixtures.kind_event("above_rated_altitude")
        self.assertIn("tc.source", self.values(raw))
        conflicted = deepcopy(raw)
        conflicted["evidence"]["identity_checks"] = {
            **conflicted["evidence"].get("identity_checks", {}),
            "conflict": True,
        }
        with self.subTest("conflict"):
            facts = self.values(conflicted)
            self.assertNotIn("lim.limit", facts)
            self.assertNotIn("tc.source", facts)
        # The public projection refuses a rated packet without its sheet; the facts layer reads
        # the packet alone when there is no projection, and still cites no registry.
        packet = fixtures.packet(raw)
        packet["rated_limit"] = {**packet["rated_limit"], "source": None}
        facts = story_facts.fact_map(story_facts.build(packet))
        self.assertNotIn("lim.limit", facts)
        self.assertNotIn("tc.source", facts)


class RatedStoryTests(unittest.TestCase):
    def render(self, raw, key="episode", place=fixtures.PLACES["coleman"], recent=None):
        facts = facts_for(raw, place=place)
        story, report = editorial.render_with_report(facts, key, recent=recent)
        return story, report, facts

    def test_each_kind_renders_a_publishable_story_with_its_figure_and_one_hedge(self):
        cases = (
            (fixtures.kind_event("above_rated_altitude"), ALTITUDE_HEDGE, "altitude of 41,000 ft."),
            (fixtures.kind_event("above_rated_speed"), ESTIMATE_HEDGE, "Mach number of Mach 0.82."),
            (rated("above_rated_speed", reported=True), REPORTED_HEDGE, "Mach number of Mach 0.82."),
            (vmo_bound(), ESTIMATE_HEDGE, "speed of 340 knots."),
        )
        named = []
        for raw, hedge, figure in cases:
            for key in ("episode-a", "episode-b", "episode-c", "episode-d"):
                with self.subTest(kind=raw["kind"], figure=figure, hedge=hedge[:20], key=key):
                    story, report, facts = self.render(raw, key)
                    self.assertTrue(report.publishable, report)
                    self.assertFalse(report.fallback, report)
                    self.assertEqual(public_writer.check(story, facts, []), [])
                    found = sentences(story)
                    self.assertEqual(found.count(hedge), 1)
                    # The agreeing registry is named as a mismatch story names it (registry_agrees).
                    registry = [text for text in found if re.search(r"(?i)registry|adsbdb", text)]
                    self.assertLessEqual(set(registry), {REGISTRY_AGREED, REGISTRY_UNNAMED})
                    named += registry
                    # Never the type in two sentences running, as identity then registry_agrees did.
                    for before, after in itertools.pairwise(found):
                        self.assertFalse("Boeing 737-800" in before and "Boeing 737-800" in after, after)
                    self.assertEqual(
                        found.count("The type certificate data sheet lists a maximum operating " + figure), 1
                    )
                    self.assertIn("src.limits.B738", {source["id"] for source in story["sources"]})
                    text = " ".join([story["headline"], story["dek"], *story["paragraphs"]])
                    self.assertNotRegex(
                        text, r"(?i)violat|illegal|\bbust|breach|overspeed|open-?meteo|emergency|pilot error"
                    )
                    # Counted reports, never a value held for a span of time.
                    self.assertNotRegex(text, r"(?i)\bheld\b|or more for \d")
        self.assertIn(REGISTRY_UNNAMED, named)
        story, _report, _facts = self.render(vmo_bound(), "episode-a")
        self.assertRegex(
            " ".join(story["paragraphs"]), r"or about \d+ knots on the same airspeed scale as the limit"
        )

    def test_headlines_name_the_airframe_in_the_house_forms(self):
        forms = {
            ("above_rated_altitude", False): (
                r"^Boeing 737-800 N\w+ reported above its certified ceiling$",
                r"^Boeing 737-800 N\w+ reported [\d,]+ ft, above the type's 41,000-foot ceiling$",
                r"^Boeing 737-800 N\w+ reported [\d,]+ ft or more, over its 41,000-foot ceiling$",
            ),
            ("above_rated_speed", False): (
                r"^Boeing 737-800 N\w+ estimated past its maximum operating Mach number$",
                r"^Boeing 737-800 N\w+ at an estimated Mach 0\.91, past its certified limit$",
                r"^Boeing 737-800 N\w+ estimated at least 10 percent past its maximum operating Mach number$",
            ),
            # A reported airspeed's Mach number is still computed, so no headline calls it reported.
            ("above_rated_speed", True): (
                r"^Boeing 737-800 N\w+ reported past its maximum operating Mach number$",
                None,
                r"^Boeing 737-800 N\w+ reported at least 12 percent past its maximum operating Mach number$",
            ),
        }
        for (kind, reported), patterns in forms.items():
            raw = rated(kind, reported=True) if reported else fixtures.kind_event(kind)
            facts = facts_for(raw)
            slots = editorial.build_slots(facts)
            angle = editorial.angles_for(kind)[0]
            rendered = [editorial.fill(template, slots)[0] for template in angle.headlines]
            with self.subTest(kind=kind, reported=reported):
                self.assertEqual(len(rendered), len(patterns))
                for text, pattern in zip(rendered, patterns, strict=True):
                    if pattern is None:
                        self.assertIsNone(text)
                        continue
                    self.assertRegex(text, pattern)
                    self.assertEqual(public_prose.prose_checker(facts)(text), [])
        # Under Vmo the Mach number is no comparison, so no headline gives it.
        slots = editorial.build_slots(facts_for(vmo_bound()))
        self.assertNotIn("lim_mach_estimate", slots)

    def test_every_angle_renders_and_a_profile_opener_hands_over_to_the_airframe(self):
        # A type-profile opener is about the type, so the next sentence names the airframe.
        profile = fixtures.profile("B738", "Boeing delivered the first 737-800 in 1998.")
        for kind in KINDS:
            for place in (None, fixtures.PLACES["coleman"]):
                facts = fixtures.facts(fixtures.kind_event(kind), place=place, profiles=profile)
                builder = editorial._Builder(facts, editorial.build_slots(facts), editorial.Corpus([]))
                seo = editorial.seo_title(facts)
                registration = story_facts.value(facts, "id.registration")
                for angle in editorial.angles_for(kind):
                    found = editorial._candidate(builder, angle, "episode", seo)
                    with self.subTest(kind=kind, angle=angle.id, place=bool(place)):
                        self.assertIsNotNone(found)
                        story = found[0]
                        self.assertEqual(public_writer.check(story, facts, []), [])
                        if angle.id == "type_profile":
                            self.assertIn(registration, sentences(story)[1])

    def test_the_registry_names_the_type_only_when_the_sentence_before_did_not(self):
        forms = set()
        profile = fixtures.profile("B738", "Boeing delivered the first 737-800 in 1998.")
        for kind in KINDS:
            facts = fixtures.facts(fixtures.kind_event(kind), profiles=profile)
            angles = editorial.angles_for(kind)
            for angle, found in zip(angles, candidates(facts, angles, ("episode",)), strict=True):
                with self.subTest(kind=kind, angle=angle.id):
                    self.assertIsNotNone(found)
                    texts = sentences(found[0])
                    for before, text in itertools.pairwise(texts):
                        if text in (REGISTRY_AGREED, REGISTRY_UNNAMED):
                            expected = REGISTRY_UNNAMED if "Boeing 737-800" in before else REGISTRY_AGREED
                            self.assertEqual(text, expected, before)
                            forms.add(text)
        self.assertEqual(forms, {REGISTRY_AGREED, REGISTRY_UNNAMED})

    def test_the_rules_refuse_a_missing_hedge_a_judgement_and_the_wind_service(self):
        raw = fixtures.kind_event("above_rated_speed")
        story, _report, facts = self.render(raw)
        self.assertEqual(public_prose.story_issues(story, facts), [])

        def without(text, hedge):
            changed = deepcopy(text)
            changed["paragraphs"] = [
                paragraph.replace(" " + hedge, "").replace(hedge, "") for paragraph in text["paragraphs"]
            ]
            changed["paragraphs"] = [paragraph for paragraph in changed["paragraphs"] if paragraph]
            return changed

        unhedged = without(story, ESTIMATE_HEDGE)
        self.assertIn("claim:limit_unhedged", public_prose.story_issues(unhedged, facts))
        # The hedge in a headline does not count; it belongs in a paragraph.
        moved = deepcopy(unhedged)
        moved["headline"] = "The airspeed may be a faulty reading for this Boeing 737-800"
        self.assertIn("claim:limit_unhedged", public_prose.story_issues(moved, facts))
        # An estimate carries the estimate's hedge, not the report's.
        swapped = deepcopy(unhedged)
        swapped["paragraphs"][-1] += " " + REPORTED_HEDGE
        self.assertIn("claim:limit_unhedged", public_prose.story_issues(swapped, facts))
        # Words that only sit near a hedge's are no hedge.
        altitude, _report, altitude_facts = self.render(fixtures.kind_event("above_rated_altitude"))
        for decoy in (
            "Its pressure altitude held above flight level 420 for 75 seconds.",
            "The pressure altitude shows no excursion; it stayed high the whole time.",
        ):
            fake = without(altitude, ALTITUDE_HEDGE)
            fake["paragraphs"][-1] += " " + decoy
            with self.subTest(decoy=decoy):
                self.assertIn("claim:limit_unhedged", public_prose.story_issues(fake, altitude_facts))
        for word in ("violation", "illegal", "bust", "busted", "breach", "overspeed", "investigation"):
            judged = deepcopy(story)
            judged["paragraphs"][-1] += f" Nothing here shows a {word} of the limit."
            with self.subTest(word=word):
                self.assertIn("claim:limit_violation", public_prose.story_issues(judged, facts))
        for sentence in (
            "It flew against the rules.",
            "It broke its speed limit.",
            "It went faster than it is allowed to.",
            "Nothing says it was allowed to fly that fast.",
        ):
            judged = deepcopy(story)
            judged["paragraphs"][-1] += " " + sentence
            with self.subTest(judged=sentence):
                self.assertIn("claim:limit_violation", public_prose.story_issues(judged, facts))
        # A value is never held for a span of time; the reports are counted.
        for sentence in (
            "It held Mach 0.91 for 75 seconds.",
            "It sustained that pace for about a minute.",
            "Its airspeed stayed above the limit for 75 s.",
        ):
            held = deepcopy(story)
            held["paragraphs"][-1] += " " + sentence
            with self.subTest(held=sentence):
                self.assertIn("claim:limit_held", public_prose.story_issues(held, facts))
        # The figures go by their own names: no service ceiling, and no never-exceed speed for a
        # type whose limits are Vmo and Mmo.
        for target, target_facts, sentence in (
            (altitude, altitude_facts, "The type's service ceiling is lower."),
            (story, facts, "The type has a never-exceed speed too."),
        ):
            renamed = deepcopy(target)
            renamed["paragraphs"][-1] = sentence + " " + renamed["paragraphs"][-1]
            with self.subTest(renamed=sentence):
                self.assertIn("claim:limit_name", public_prose.story_issues(renamed, target_facts))
        named = deepcopy(story)
        named["paragraphs"][-1] += " The wind came from Open-Meteo."
        self.assertIn("claim:wind_service_named", public_prose.story_issues(named, facts))
        # The rules belong to rated stories only.
        other = fixtures.facts(fixtures.kind_event("fast"))
        other_story = editorial.render(other, "episode")
        self.assertFalse(
            {"claim:limit_unhedged", "claim:limit_violation"}
            & set(public_prose.story_issues(other_story, other))
        )

    def test_named_figures_take_their_values_from_their_fields(self):
        speed = public_prose.prose_checker(facts_for(fixtures.kind_event("above_rated_speed")))
        altitude = public_prose.prose_checker(facts_for(fixtures.kind_event("above_rated_altitude")))
        accepted = (
            (speed, "It held an estimated airspeed of 522 knots."),
            (speed, "It reported a ground speed of 530 knots."),
            (speed, "Its airspeed reached 522 knots."),
            (speed, "The type certificate data sheet lists a maximum operating speed of Mach 0.82."),
            (altitude, "The type certificate data sheet lists a maximum operating altitude of 41,000 ft."),
        )
        refused = (
            # 530 knots is the ground speed: no estimate, true airspeed, or bare airspeed has it.
            (speed, "It held an estimated airspeed of 530 knots."),
            (speed, "It held a true airspeed of 522 knots."),
            (speed, "Its airspeed reached 530 knots."),
            (speed, "It reported a ground speed of 522 knots."),
            # A reading or an estimate is never the certificate's figure.
            (speed, "The type certificate data sheet lists a maximum operating speed of Mach 0.91."),
            (speed, "Its certified Mmo is Mach 0.91."),
            (altitude, "The type certificate data sheet lists a maximum operating altitude of 42,400 ft."),
            # "Certified" no longer lifts the capability and airspeed checks from a clause.
            (speed, "It went supersonic above its certified limit."),
            (altitude, "It broke the sound barrier past its certified limit."),
        )
        for check, text in accepted:
            with self.subTest(text=text):
                self.assertEqual(check(text), [])
        for check, text in refused:
            with self.subTest(text=text):
                self.assertTrue(check(text))
        reported = public_prose.prose_checker(facts_for(rated("above_rated_speed", reported=True)))
        self.assertEqual(reported("It reported a true airspeed of 530 knots."), [])
        # A ground-speed story with a cited limit still needs an airspeed report to speak of airspeed.
        fast = public_prose.prose_checker(fixtures.facts(fixtures.kind_event("fast")))
        self.assertTrue(fast("Its airspeed beat the rated limit."))

    def test_stories_vary_across_a_run_of_findings(self):
        recent, headlines = [], set()
        for index in range(6):
            raw = rated("above_rated_altitude", start=f"2026-09-{10 + index:02d}T14:00:00Z")
            raw["evidence"]["aircraft"]["r"] = f"N84{index}7Q"
            story, report, facts = self.render(raw, key=f"episode-{index}", recent=list(recent))
            self.assertTrue(report.publishable, report)
            headlines.add(re.sub(r"N84\d7Q", "REG", story["headline"]))
            recent.append(fixtures.recent_entry(story, facts))
        self.assertGreaterEqual(len(headlines), 3)


class RatedWriterTests(unittest.TestCase):
    def test_kind_notes_give_the_figure_the_hedge_and_the_forbidden_words(self):
        altitude = public_writer.kind_note("above_rated_altitude")
        speed = public_writer.kind_note("above_rated_speed")
        self.assertIn(ALTITUDE_HEDGE, altitude)
        self.assertIn(ESTIMATE_HEDGE, speed)
        self.assertIn(REPORTED_HEDGE, speed)
        for note in (altitude, speed):
            self.assertIn("The type certificate data sheet lists a maximum operating", note)
            self.assertIn(
                "Never write violation, illegal, bust, breach, overspeed, emergency, pilot error, against the "
                "rules, allowed to, or broke its limit",
                note,
            )
            self.assertIn("never a service ceiling, and only a Vne is a never-exceed speed", note)
            self.assertIn("never rounded up", note)
            self.assertEqual(public_prose.serial_comma_issues(note), [])
        self.assertIn("Never name the weather service", speed)
        self.assertIn("never-exceed speed", speed)
        self.assertIn("lim.indicated against a Vmo or Vne, given in knots on the same airspeed scale", speed)
        # Each hedge the notes quote is the one the checks require, for its own basis.
        self.assertEqual(
            public_prose.required_sentence(facts_for(fixtures.kind_event("above_rated_altitude"))),
            ALTITUDE_HEDGE,
        )
        self.assertEqual(
            public_prose.required_sentence(facts_for(fixtures.kind_event("above_rated_speed"))),
            ESTIMATE_HEDGE,
        )
        self.assertEqual(
            public_prose.required_sentence(facts_for(rated("above_rated_speed", reported=True))),
            REPORTED_HEDGE,
        )

    def test_the_writer_check_accepts_the_engine_and_refuses_an_unhedged_candidate(self):
        facts = facts_for(fixtures.kind_event("above_rated_altitude"))
        story = editorial.render(facts, "episode")
        self.assertEqual(public_writer.check(story, facts, []), [])
        unhedged = deepcopy(story)
        unhedged["paragraphs"] = [
            paragraph.replace(" " + ALTITUDE_HEDGE, "") for paragraph in story["paragraphs"]
        ]
        self.assertIn("claim:limit_unhedged", public_writer.check(unhedged, facts, []))


class RatedSlugTests(unittest.TestCase):
    def test_slugs_name_the_phenomenon(self):
        for kind, ending in (
            ("above_rated_altitude", "above-certified-ceiling"),
            ("above_rated_speed", "above-maximum-speed"),
        ):
            raw = fixtures.kind_event(kind)
            facts = facts_for(raw)
            with self.subTest(kind=kind):
                slug = story_urls.slug(facts, raw["first_seen"])
                self.assertRegex(slug, rf"^boeing-737-800-n\d+(?:tx|qk)-{ending}-20-sep-2026$")
                self.assertTrue(story_urls.is_new_slug(slug))
        self.assertEqual(story_urls.PHENOMENA["above_rated_speed"][1], "max-speed")


class RatedSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "site_fixtures_rated", ROOT / "tools/ghost-theme/site_fixtures.py"
        )
        cls.fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixtures)
        cls.snapshot = cls.fixtures.site_snapshot("published", extra=False)

    def test_the_site_fixtures_carry_one_engine_story_of_each_kind(self):
        events = {event["kind"]: event for event in self.snapshot["events"] if event["kind"] in KINDS}
        self.assertEqual(set(events), set(KINDS))
        specs = {
            spec["kind"]: self.fixtures.rated_story(spec) for spec in self.fixtures.SPECS if spec.get("rated")
        }
        for kind, event in events.items():
            with self.subTest(kind=kind):
                self.assertEqual(event["context"]["topic"], "speed-and-altitude")
                self.assertEqual(event["primary_type"], "performance")
                self.assertEqual(event["story"], specs[kind]["story"])
                self.assertEqual(public_writer.check(event["story"], specs[kind]["facts"], []), [])
                self.assertIn("reference-data", {item["scope"] for item in event["measurements"]})
                self.assertRegex(
                    event["article_path"], r"^/stories/(?:embraer-e195-e2-n295rl|airbus-a320-n320rs)-above-"
                )

    def test_sources_take_a_second_level_heading_without_type_research(self):
        event = next(event for event in self.snapshot["events"] if event["kind"] == "above_rated_altitude")
        html = publication._type_background(event)
        self.assertIn('<aside class="story-sources" aria-label="Sources"><h2>Sources</h2>', html)
        researched = next(
            event
            for event in self.snapshot["events"]
            if any(key.startswith("tp.") for key in event.get("story", {}).get("facts", {}))
        )
        self.assertIn("<h3>Sources</h3>", publication._type_background(researched))
        # A post published under the earlier heading, without a stored digest, is still ours.
        older = publication.story_html(event, legacy=True).rstrip("\n")
        self.assertIn("<h3>Sources</h3>", older)
        self.assertTrue(publication._generated_story(older, None, event))
        self.assertTrue(publication._generated_story(publication.story_html(event).rstrip("\n"), None, event))
        self.assertFalse(publication._generated_story(older.replace("Sources", "Reading"), None, event))


if __name__ == "__main__":
    unittest.main()
