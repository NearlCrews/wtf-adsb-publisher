"""Template stories: angle coverage, validity, determinism, rotation, SEO titles and indexability."""

import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
import reference_fixture
import story_fixtures as fixtures

from wtf_adsb_publisher import editorial, public_events, public_prose, story_facts, watchlists

# What a record too thin to fill a paragraph fails: one short sentence, left unpublished.
# A thin record also leaves out its kind's context line, which is never padding.
THIN_ISSUES = {"paragraph[0]:length", "paragraphs:too_short", "context:missing"}


def setUpModule():
    reference_fixture.install()


def kind_facts(kind):
    return fixtures.facts(fixtures.kind_event(kind), place=fixtures.PLACES["coleman"])


def reported_speed_facts():
    """A rated-speed story whose airspeed is the aircraft's own report, not a wind estimate."""
    common = {
        "hexcode": "a1c2e3",
        "registration": "N8417Q",
        "callsign": None,
        "start": "2026-09-20T14:00:00Z",
    }
    raw = fixtures.rated_event("above_rated_speed", fixtures.stamp(common["start"]), common, reported=True)
    return fixtures.facts(raw, place=fixtures.PLACES["coleman"])


def registry_facts(state, code="B752"):
    """A speed-mismatch story whose second registry (adsbdb) agrees or lists another type."""
    raw = fixtures.with_registry(fixtures.kind_event("type_speed_mismatch"), state, code)
    return fixtures.facts(raw, place=fixtures.PLACES["coleman"])


def norm_facts(kind):
    """A speed story with the type's usual ground speed in these records (selection.type_norm)."""
    return fixtures.norm_facts(kind, place=fixtures.PLACES["coleman"])


def candidates(facts, angles, keys):
    """editorial._candidate() for each angle and key, as render() tries them."""
    slots = editorial.build_slots(facts)
    seo = editorial.seo_title(facts, slots)
    builder = editorial._Builder(facts, slots, editorial.Corpus([]))
    return [editorial._candidate(builder, angle, key, seo) for angle in angles for key in keys]


def thin(raw):
    """An event stripped to its address and times: no registration, type, readings or track."""
    aircraft = raw["evidence"]["aircraft"]
    for key in ("r", "flight", "t", "desc", "year", "alt_baro", "gs", "archive_alt", "alt_geom", "dbFlags"):
        aircraft.pop(key, None)
    raw["evidence"].pop("pattern_samples", None)
    return raw


def sentences(text):
    return [text[start:end].strip() for start, end in public_prose.sentence_spans(text)]


# Template stories never pad: no logbook sentence about when reports start or stop, how the
# page gives times, or how multilateration works. A thin record makes a short story, which
# the indexing gate (docs/production-launch.md) leaves unindexed until AI prose replaces it.
FILLER = re.compile(
    r"\b(?:Reports in this record stop|(?:final|last) report in this record|The record (?:ends|begins|starts|covers)"
    r"|The observation began|The reports run from|keeps the time of its own report|Times on this page"
    r"|multilateration, which estimates)\b|\bafter (?:the first|it began)\b",
    re.I,
)


def unique_words(story, others):
    """Words in sentences of six or more words that appear on no other page (design 1.2)."""
    elsewhere = {
        sentence for other in others for paragraph in other["paragraphs"] for sentence in sentences(paragraph)
    }
    elsewhere |= {other["headline"] for other in others} | {other["dek"] for other in others}
    count = 0
    for paragraph in story["paragraphs"]:
        for sentence in sentences(paragraph):
            words = sentence.split()
            if len(words) >= 6 and sentence not in elsewhere:
                count += len(words)
    return count


# Kinds whose story always leads with one hook have fewer than three angles: a watchlist
# story with its Plane Alert DB entry, a first sighting with the first.
LEAD_ANGLE_KINDS = {
    "watchlist_match": editorial.WATCHLIST_ANGLES,
    "first_type_here": editorial.FIRST_ANGLES,
    "first_country_here": (editorial.ALLOCATION,),
}


class LibraryTests(unittest.TestCase):
    def test_every_public_kind_has_three_angles_with_two_variants_each(self):
        for kind in public_events.KIND_TYPES:
            angles = editorial.angles_for(kind)
            with self.subTest(kind=kind):
                self.assertIs(angles, editorial.KIND_ANGLES[kind])
                if kind in LEAD_ANGLE_KINDS:
                    self.assertEqual(angles, LEAD_ANGLE_KINDS[kind])
                else:
                    self.assertGreaterEqual(len(angles), 3)
                self.assertEqual(len({angle.id for angle in angles}), len(angles))
                for angle in angles:
                    # A fixed headline has one form; the dek and opener still vary.
                    if kind in public_prose.FIXED_HEADLINE_KINDS:
                        self.assertEqual(angle.headlines, ("{fixed_headline}",), angle.id)
                    else:
                        self.assertGreaterEqual(len(angle.headlines), 2, angle.id)
                    self.assertGreaterEqual(len(angle.deks), 2, angle.id)
                    self.assertGreaterEqual(len(angle.openers), 2, angle.id)
                    self.assertTrue(angle.focus)

    def test_templates_use_slots_that_facts_can_produce(self):
        produced = set()
        for kind in public_events.KIND_TYPES:
            produced |= set(editorial.build_slots(kind_facts(kind)))
        # An identity conflict may concern the callsign while the address and database agree.
        packet = fixtures.packet(fixtures.kind_event("identity_conflict"))
        packet["identity_checks"].update(
            us_registration_from_address=packet["aircraft"]["registration"],
            registration_matches_address=True,
        )
        produced |= set(editorial.build_slots(story_facts.build(packet)))
        # The type's rated limits, from the cited dataset the detection work is adding.
        with fixtures.rated_limits():
            for kind in ("type_speed_mismatch", "type_altitude_mismatch"):
                produced |= set(editorial.build_slots(kind_facts(kind)))
        for case in fixtures.design_cases():
            produced |= set(editorial.build_slots(fixtures.case_facts(case)))
        # A rated speed the aircraft reported itself, which no wind estimate gives.
        produced |= set(editorial.build_slots(reported_speed_facts()))
        # A mismatch story's second registry that lists another type.
        produced |= set(editorial.build_slots(registry_facts("disagrees")))
        # A designator with no name, named by its class phrase and type code.
        produced |= set(editorial.build_slots(fixtures.facts(fixtures.unnamed("descending_spiral"))))
        # The type's usual ground speed in these records (selection.type_norm).
        produced |= set(editorial.build_slots(norm_facts("seasonal_speed_record")))
        # The nearest public airport to the aircraft's positions (airports.nearest).
        produced |= set(
            editorial.build_slots(fixtures.facts(fixtures.kind_event("circling"), airport=fixtures.BRIGHTON))
        )
        # What Wikidata calls the organization a watchlist entry links to (entity_context).
        produced |= set(
            editorial.build_slots(
                fixtures.facts(fixtures.kind_event("watchlist_match"), entities=fixtures.watch_entity())
            )
        )
        # The same, when the entry carries one of the item's aliases instead of its label.
        alias = fixtures.watch_entity(label="Example Holdings", aliases=[fixtures.WATCH_ASSOCIATION])
        produced |= set(
            editorial.build_slots(fixtures.facts(fixtures.kind_event("watchlist_match"), entities=alias))
        )
        for raw, place, _registration in fixtures.a380_sequence(6):
            produced |= set(
                editorial.build_slots(fixtures.facts(raw, place=place, profiles=fixtures.A380_PROFILE))
            )
        templates = [
            template
            for angles in (*editorial.KIND_ANGLES.values(), editorial.GENERIC_ANGLES)
            for angle in angles
            for template in (*angle.headlines, *angle.deks, *angle.openers)
        ]
        templates += [template for templates in editorial.BUILDERS.values() for template in templates]
        missing = {name for template in templates for name in re.findall(r"\{(\w+)", template)} - produced
        self.assertEqual(missing, set())
        for angles in editorial.KIND_ANGLES.values():
            for angle in angles:
                for name in angle.body:
                    self.assertTrue(name == "/" or name in editorial.BUILDERS, name)

    def test_sentences_without_slots_only_run_when_their_facts_exist(self):
        for name, templates in editorial.BUILDERS.items():
            if all(re.search(r"\{\w+", template) for template in templates) or name == "identity_ref":
                continue
            with self.subTest(builder=name):
                self.assertIn(name, editorial.BUILDER_REQUIRES)
        samples = [kind_facts(kind) for kind in public_events.KIND_TYPES]
        samples += [reported_speed_facts(), registry_facts("agrees", code=None)]
        for slot in {slot for slots in editorial.BUILDER_REQUIRES.values() for slot in slots}:
            self.assertTrue(any(slot in editorial.build_slots(facts) for facts in samples), slot)

    def test_templates_use_the_serial_comma(self):
        templates = [template for templates in editorial.BUILDERS.values() for template in templates]
        for angles in (*editorial.KIND_ANGLES.values(), editorial.GENERIC_ANGLES):
            for angle in angles:
                templates.extend((*angle.headlines, *angle.deks, *angle.openers, angle.focus))
        templates.extend((*editorial.MINIMAL_HEADLINES, *editorial.MINIMAL_DEKS, *editorial.MINIMAL_OPENERS))
        templates.extend(editorial.LIMIT_REWRITES.values())
        for template in templates:
            with self.subTest(template=template):
                self.assertEqual(public_prose.serial_comma_issues(template), [])

    def test_templates_have_no_dashes_or_exclamations(self):
        for templates in editorial.BUILDERS.values():
            for template in templates:
                self.assertIsNone(public_prose.DASH.search(template), template)
                self.assertNotIn("!", template)
        for angles in editorial.KIND_ANGLES.values():
            for angle in angles:
                for template in (*angle.headlines, *angle.deks, *angle.openers):
                    self.assertIsNone(public_prose.DASH.search(template), template)
                    self.assertNotIn("!", template)
                    if angle.id != "explainer":
                        self.assertNotIn("?", template)


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rendered = {}
        for kind in public_events.KIND_TYPES:
            facts = kind_facts(kind)
            story, report = editorial.render_with_report(facts, f"episode-{kind}")
            cls.rendered[kind] = (story, facts, report)

    def test_every_kind_renders_a_valid_template_story(self):
        for kind, (story, facts, report) in self.rendered.items():
            with self.subTest(kind=kind):
                self.assertFalse(report.fallback, report)
                self.assertEqual(public_prose.story_issues(story, facts), [])
                self.assertIs(public_prose.validate_story_fields(story, facts), story)
                self.assertEqual(editorial.variety_issues(story, []), [])
                self.assertEqual(tuple(story), editorial.STORY_KEYS)
                self.assertEqual(story["source"], "template")
                self.assertEqual(story["version"], editorial.PROSE_POLICY)
                self.assertEqual(story["facts_digest"], facts["digest"])
                self.assertIn(story["angle"], {angle.id for angle in editorial.angles_for(kind)})

    def test_flags_sources_and_limitations_follow_the_facts(self):
        for kind, (story, facts, _report) in self.rendered.items():
            text = " ".join(story["paragraphs"]).lower()
            values = story_facts.fact_map(facts)
            slots = editorial.build_slots(facts)
            with self.subTest(kind=kind):
                if "db.military" not in values:
                    self.assertNotRegex(text, r"military flag|flags it as military")
                if not slots.get("mlat"):
                    self.assertNotIn("multilateration", text)
                if slots.get("limit"):
                    limited = [
                        sentence
                        for paragraph in story["paragraphs"]
                        for sentence in sentences(paragraph)
                        if public_prose.LIMIT_SENTENCE.search(sentence)
                    ]
                    # The kind's required sentence, when it has one, stands in for a limitation it restates.
                    required = public_prose.required_sentence(facts)
                    self.assertTrue(
                        slots["limit"] in " ".join(story["paragraphs"])
                        or limited
                        or (required and public_prose.carries(story["paragraphs"], required))
                    )
        variant = fixtures.kind_event("notable_variant")
        variant["evidence"]["aircraft"]["type"] = "adsb_icao"
        for sample in variant["evidence"]["pattern_samples"]:
            sample["source"] = "adsb_icao"
        built = fixtures.facts(variant, profiles=fixtures.E6_PROFILE)
        for index in range(6):
            story = editorial.render(built, f"episode-{index}")
            self.assertNotIn("multilateration", " ".join(story["paragraphs"]))

    def test_counts_read_as_words_or_figures_with_matching_nouns(self):
        for kind, (story, _facts, _report) in self.rendered.items():
            for paragraph in story["paragraphs"]:
                for sentence in sentences(paragraph):
                    with self.subTest(kind=kind, sentence=sentence):
                        self.assertIsNone(editorial.LEADING_COUNT.match(sentence))

    def test_standfirst_and_opener_add_to_what_came_before(self):
        for kind, (story, _facts, _report) in self.rendered.items():
            names = editorial._identity_names({**story, "facts": story["facts"]})
            opener = sentences(story["paragraphs"][0])[0]
            with self.subTest(kind=kind):
                self.assertFalse(editorial._echoes(story["headline"], story["dek"], names, 0.75))
                self.assertFalse(editorial._echoes(opener, story["dek"], names))

    def test_parts_try_another_clean_headline_before_an_echoing_opener(self):
        class Builder:
            names, kind, slots = (), "watch_type", {}

            def __init__(self, unvaried=()):
                self.unvaried = set(unvaried)

            def pick(self, templates, seed, limits, part, *, skip=(), demote=None, reject=None):
                options = [text for text in templates if text not in skip]
                # Like _Builder.pick: demoted variants, then ones with variety issues, come last.
                options.sort(key=lambda text: (text in self.unvaried, bool(demote and demote(text))))
                return (templates.index(options[0]), options[0], frozenset()) if options else None

            def varied(self, part, text):
                return text not in self.unvaried

        first, second = "Alpha bravo charlie delta echo", "Kilo lima mike november oscar papa quebec"
        echo = "Kilo lima mike november oscar papa quebec romeo."
        angle = SimpleNamespace(
            headlines=(first, second),
            deks=(echo, "Sierra tango uniform victor whiskey xray yankee."),
            openers=(echo,),
        )
        headline, dek, opener = editorial._parts(Builder(), angle, "seed")
        self.assertEqual((headline[1], dek[0]), (second, 1))
        self.assertFalse(editorial._echoes(opener[1], dek[1]))
        # A headline with variety issues never replaces a clean one; the first combination stands.
        headline, dek, _ = editorial._parts(Builder(unvaried={second}), angle, "seed")
        self.assertEqual((headline[1], dek[0]), (first, 0))

    def test_story_facts_never_list_context_tags(self):
        facts = fixtures.facts(fixtures.kind_event("rapid_descent"))
        facts["facts"].append({"id": "tags", "value": ["archive_carried_label"], "basis": "derived"})
        listed = editorial.story_fact_values(facts, {"tags", "id.registration"})
        self.assertNotIn("tags", listed)
        self.assertIn("id.registration", listed)

    def test_a_winds_day_may_name_strong_winds_aloft_and_no_other_weather(self):
        check = public_prose.prose_checker(kind_facts("winds_aloft_day"))
        self.assertEqual(check("Strong winds aloft marked the day."), [])
        for phrase in ("Turbulence", "Icing", "Storms", "High winds", "Gusty winds"):
            with self.subTest(phrase=phrase):
                self.assertTrue(check(f"{phrase} aloft marked the day."))

    def test_field_limits(self):
        for kind, (story, _facts, _report) in self.rendered.items():
            with self.subTest(kind=kind):
                # A fixed headline is not free text: it may run shorter than the floor.
                floor = 0 if kind in public_prose.FIXED_HEADLINE_KINDS else 40
                self.assertTrue(floor <= len(story["headline"]) <= 90, story["headline"])
                self.assertTrue(70 <= len(story["dek"]) <= 155, story["dek"])
                self.assertLessEqual(len(story["seo_title"]), 60)
                low, high = public_prose.PARAGRAPHS
                self.assertTrue(low <= len(story["paragraphs"]) <= high)
                for text in (story["headline"], story["dek"], story["seo_title"], *story["paragraphs"]):
                    self.assertIsNone(public_prose.DASH.search(text))

    def test_template_stories_carry_no_filler(self):
        stories = [story for story, _facts, _report in self.rendered.values()]
        for kind, (story, _facts, _report) in self.rendered.items():
            with self.subTest(kind=kind):
                for text in (story["dek"], *story["paragraphs"]):
                    for sentence in sentences(text):
                        self.assertIsNone(FILLER.search(sentence), sentence)
                # Short or not, every story says something no other story says.
                self.assertGreater(unique_words(story, [other for other in stories if other is not story]), 0)

    def test_thin_records_still_render_valid_stories(self):
        for kind in ("special_squawk_7700", "fast", "circling", "type_altitude_mismatch", "rapid_descent"):
            facts = story_facts.build(fixtures.packet(thin(fixtures.kind_event(kind))))
            story, report = editorial.render_with_report(facts, "episode")
            with self.subTest(kind=kind):
                self.assertFalse(report.fallback)
                self.assertEqual(public_prose.story_issues(story, facts), [])
                self.assertNotIn(" None", " ".join([story["headline"], story["dek"], *story["paragraphs"]]))

    def test_the_last_resort_story_is_a_valid_plain_record(self):
        # A registration and type with no readings, track or place: no angle applies. A short
        # registration and date still make a standfirst of the minimum length.
        raw = thin(fixtures.kind_event("watch_type", fixtures.stamp("2026-10-01T12:00:00Z")))
        raw["evidence"]["aircraft"].update(r="N52", t="A124", desc="Antonov An-124 Ruslan")
        facts = story_facts.build(fixtures.packet(raw))
        story, report = editorial.render_with_report(facts, "episode")
        self.assertTrue(report.fallback)
        self.assertTrue(report.publishable)
        self.assertEqual(report.issues, [])
        self.assertEqual(story["angle"], "minimal")
        self.assertEqual(tuple(story), editorial.STORY_KEYS)
        # Thin facts make a short story, never a padded one, and every part passes the checks.
        self.assertEqual(public_prose.story_issues(story, facts), [])
        self.assertIs(public_prose.validate_story_fields(story, facts), story)
        self.assertEqual(len(story["paragraphs"]), 1)
        text = " ".join([story["headline"], story["dek"], *story["paragraphs"]])
        self.assertNotIn(" None", text)
        self.assertIsNone(FILLER.search(text), text)
        self.assertIn("N52", story["headline"])
        self.assertRegex(text, public_prose.DATABASE_ATTRIBUTION)
        self.assertEqual(editorial.render(facts, "episode"), story)
        # With only an address and a date there is nothing to fill a paragraph with, and no
        # padding: the record is reported unpublishable, its one short sentence its only issue.
        bare = story_facts.build(fixtures.packet(thin(fixtures.kind_event("watch_type"))))
        story, report = editorial.render_with_report(bare, "episode")
        self.assertFalse(report.publishable)
        self.assertEqual(report.issues, ["paragraph[0]:length", "paragraphs:too_short", "context:missing"])
        self.assertEqual(len(sentences(story["paragraphs"][0])), 1)
        self.assertIn(story_facts.value(bare, "id.hex"), story["headline"])

    def test_the_plain_record_passes_for_every_kind_and_identity(self):
        # A minimal record for each kind, with every identity the facts may hold,
        # including a type and registration that need database attribution.
        identities = (
            {},
            {"r": "N52"},
            {"flight": "RCH123"},
            {"r": "N52", "t": "C172", "desc": "CESSNA 172 Skyhawk"},
            {"t": "C5M", "desc": "LOCKHEED C-5M Super Galaxy", "flight": "RCH4567"},
        )
        for kind in public_events.KIND_TYPES:
            # A record that runs past midnight, the harder case for dates and times.
            for start in ("2026-09-30T23:55:00Z",):
                for identity in identities:
                    raw = thin(fixtures.kind_event(kind, fixtures.stamp(start)))
                    raw["evidence"]["aircraft"].update(identity)
                    facts = story_facts.build(fixtures.packet(raw))
                    slots = editorial.build_slots(facts)
                    builder = editorial._Builder(facts, slots, editorial.Corpus([]))
                    story, issues = editorial._plain_record(
                        builder, "episode", editorial.seo_title(facts, slots)
                    )
                    with self.subTest(kind=kind, start=start, identity=identity):
                        if kind in public_prose.RATED_KINDS:
                            # The comparison names the 737-800 the fixture lists; under another
                            # type, or none, it is withheld, and a rated story without its cited
                            # figure has no hedge to carry and is never published.
                            self.assertIn("claim:limit_unhedged", issues)
                            self.assertLessEqual(set(issues), {*THIN_ISSUES, "claim:limit_unhedged"})
                        elif identity.get("t"):
                            self.assertEqual(issues, [])
                            self.assertEqual(public_prose.story_issues(story, facts), [])
                        else:
                            # Without a type, a record may be too thin to fill one paragraph; it is
                            # then left short and unpublished rather than padded.
                            self.assertLessEqual(set(issues), THIN_ISSUES)
                            self.assertEqual(public_prose.story_issues(story, facts), issues)
                        self.assertIsNone(FILLER.search(" ".join([story["dek"], *story["paragraphs"]])))
                        if identity.get("r") or identity.get("t"):
                            self.assertRegex(" ".join(story["paragraphs"]), public_prose.DATABASE_ATTRIBUTION)

    def test_facts_without_a_time_give_a_non_publishable_result(self):
        facts = {
            "kind": "watch_type",
            "facts": [{"id": "id.hex", "value": "ABC123", "basis": "telemetry", "field": "icao_address"}],
            "limits": [],
            "hypotheses": [],
            "sources": [],
            "context": {},
            "digest": "0" * 64,
        }
        story, report = editorial.render_with_report(facts, "episode")
        self.assertTrue(report.fallback)
        self.assertFalse(report.publishable)
        self.assertTrue(report.issues)
        self.assertEqual(report.issues, public_prose.story_issues(story, facts))
        self.assertEqual(tuple(story), editorial.STORY_KEYS)

    def test_story_facts_are_capped_with_identity_and_type_background_first(self):
        values = {f"m.extra_{index:02d}": index for index in range(40)}
        values.update({"id.registration": "N52", "obs.start": "2026-09-20T12:00:00Z", "tp.C172.1": "Fact."})
        values.update({"track.place": "Coleman, Michigan", "track.place.short": "Coleman, MI"})
        capped = editorial.cap_story_facts(values)
        self.assertLessEqual(len(capped), editorial.MAX_STORY_FACTS)
        self.assertEqual(editorial.MAX_STORY_FACTS, public_events.MAX_STORY_FACTS)
        self.assertTrue({"id.registration", "obs.start", "tp.C172.1"} <= set(capped))
        # A short form never stays without the fact it shortens.
        self.assertTrue("track.place.short" not in capped or "track.place" in capped)
        self.assertEqual(capped, editorial.cap_story_facts(dict(reversed(list(values.items())))))
        small = {"id.hex": "ABC123"}
        self.assertIs(editorial.cap_story_facts(small), small)

    def test_repeat_airframes_keep_prose_of_their_own(self):
        # Twenty A380 stories in twenty days, several about the same airframes: each one, short
        # or not, says something the others do not, and none is padded.
        run = [story for story, _facts, _report in fixtures.a380_run()]
        for story in run:
            with self.subTest(headline=story["headline"]):
                self.assertGreater(unique_words(story, [other for other in run if other is not story]), 0)
                self.assertIsNone(FILLER.search(" ".join([story["dek"], *story["paragraphs"]])))

    def test_unknown_kinds_fail_closed_to_the_generic_angle(self):
        raw = fixtures.kind_event("outlier_gs")
        raw["kind"] = "mystery_reading"
        packet = fixtures.packet(fixtures.outlier_cessna())
        packet["kind"] = "mystery_reading"
        facts = story_facts.build(packet, place=fixtures.PLACES["coleman"])
        story, report = editorial.render_with_report(facts, "episode")
        self.assertIn(story["angle"], {angle.id for angle in editorial.GENERIC_ANGLES})
        self.assertFalse(report.fallback)
        self.assertEqual(public_prose.story_issues(story, facts), [])
        text = " ".join([story["headline"], story["dek"], *story["paragraphs"]]).lower()
        self.assertNotIn("mystery", text)
        self.assertNotRegex(story["headline"], r"\breports$")
        self.assertIn("ground speed", text)
        self.assertIn("knots", text)
        self.assertEqual(story_facts.topic_for("mystery_reading"), (None, None))

    def test_generic_value_names_field_direction_and_unit(self):
        packet = fixtures.packet(fixtures.kind_event("rapid_descent"))
        packet["kind"] = "future_vertical_kind"
        facts = story_facts.build(packet)
        slots = editorial.build_slots(facts)
        self.assertEqual(slots["value_phrase"], "a descent rate of 7,600 ft/min")

    def test_choice_is_stable_and_rotates_across_episode_keys(self):
        facts = fixtures.facts(
            fixtures.watch_a380(), place=fixtures.PLACES["coleman"], profiles=fixtures.A380_PROFILE
        )
        first = editorial.render(facts, "episode-1")
        self.assertEqual(editorial.render(facts, "episode-1"), first)
        variants = {editorial.render(facts, f"episode-{index}")["variant"] for index in range(12)}
        self.assertGreater(len(variants), 3)
        options = editorial.angle_options(facts)
        self.assertEqual(options[0]["id"], first["angle"])
        self.assertEqual(options, editorial.angle_options(facts))
        self.assertTrue(all(set(option) == {"id", "focus"} for option in options))

    def test_recent_angles_are_demoted(self):
        facts = fixtures.facts(
            fixtures.watch_a380(), place=fixtures.PLACES["coleman"], profiles=fixtures.A380_PROFILE
        )
        first = editorial.angle_options(facts)[0]["id"]
        recent = [{"kind": "watch_type", "type_code": "A388", "date": "2026-09-26", "angle": first}]
        self.assertNotEqual(editorial.angle_options(facts, recent=recent)[0]["id"], first)

    def test_no_opener_skeleton_repeats_early_in_twenty_a380_stories(self):
        rendered = fixtures.a380_run()
        openers = [story["opener_skeleton"] for story, _facts, _report in rendered]
        # The first ten never repeat an opener; later, with fewer templates since the padding
        # went, a repeat is a relaxation the report names, and no skeleton serves more than three.
        self.assertEqual(len(set(openers[:10])), 10, Counter(openers).most_common(3))
        self.assertGreaterEqual(len(set(openers)), 15, Counter(openers).most_common(3))
        self.assertLessEqual(Counter(openers).most_common(1)[0][1], 3)
        for index, (story, _facts, report) in enumerate(rendered):
            if story["opener_skeleton"] in openers[:index]:
                self.assertIn("opener_repeat", report.issues, index)
        headlines = [story["headline"] for story, _facts, _report in rendered]
        self.assertEqual(len(set(headlines)), 20)
        for story, facts, _report in rendered:
            self.assertEqual(public_prose.story_issues(story, facts), [])
        angles = Counter(story["angle"] for story, _facts, _report in rendered)
        self.assertGreaterEqual(len(angles), 5)

    def test_seo_titles_are_unique_with_a_date_suffix(self):
        facts = fixtures.facts(fixtures.watch_a380(), place=fixtures.PLACES["coleman"])
        title = editorial.seo_title(facts)
        self.assertEqual(title, "Asiana A380 HL7635 near Coleman, MI")
        recent = [{"seo_title": title}]
        dated = editorial.seo_title(facts, recent=recent)
        self.assertNotEqual(dated, title)
        self.assertTrue(dated.endswith("27 Sep 2026"), dated)
        self.assertLessEqual(len(dated), 60)
        story = editorial.render(facts, "episode", recent=recent)
        self.assertEqual(story["seo_title"], dated)
        outlier = fixtures.facts(fixtures.outlier_cessna())
        self.assertEqual(editorial.seo_title(outlier), "Cessna 172 N6508K: one 1,064-knot ground-speed spike")

    def test_skeletons_work_for_ai_stories(self):
        story = {
            "headline": "Asiana A380 HL7635 shows up for a fourth time, at 39,000 ft near Coleman, Michigan",
            "paragraphs": ["HL7635 broadcast callsign AAR224 just after midnight UTC on 27 September 2026."],
            "facts": {"id.registration": "HL7635", "id.callsign": "AAR224", "id.type_code": "A388"},
        }
        story["dek"] = "Asiana's A380 HL7635 flew at 39,000 ft on 27 September."
        skeletons = editorial.skeletons(story)
        self.assertEqual(set(skeletons), {"opener_skeleton", "headline_skeleton"})
        self.assertEqual(skeletons["opener_skeleton"], "reg broadcast callsign reg just after")
        self.assertEqual(editorial.dek_skeleton(story), "op s type reg flew at num ft on date")
        self.assertTrue(skeletons["headline_skeleton"].startswith("op type reg shows up for a num time"))

    def test_a_recent_entry_names_its_variety_grams_as_a_candidate_does(self):
        # A recent entry lists its facts as ids and carries its story's values (fact_values); those
        # values name its callsign, operator, and place, as a candidate's own facts name its own.
        recent = {
            "kind": "watch_type",
            "type_code": "A388",
            "registration": "HL7635",
            "date": "2026-09-27",
            "facts": ["id.callsign", "track.place"],
            "fact_values": {
                "id.registration": "HL7635",
                "id.callsign": "AAR224",
                "track.place": "Coleman, Michigan",
            },
            "headline": "Asiana Airlines flight AAR224 cruises near Coleman, Michigan",
            "dek": "Asiana Airlines flight AAR224 cruised near Coleman, Michigan on 27 September.",
            "paragraphs": ["Asiana Airlines flight AAR224 cruised near Coleman, Michigan."],
        }
        candidate = {
            "kind": "watch_type",
            "type_code": "A388",
            "registration": "HL7628",
            "date": "2026-09-28",
            "facts": {
                "id.registration": "HL7628",
                "id.callsign": "AAR218",
                "track.place": "Midland, Michigan",
            },
            "headline": "Asiana Airlines flight AAR218 cruises near Midland, Michigan",
            "dek": "Asiana Airlines flight AAR218 cruised near Midland, Michigan on 28 September.",
            "paragraphs": ["Asiana Airlines flight AAR218 cruised near Midland, Michigan."],
        }
        entry, other = editorial._entry(recent), editorial._entry(candidate)
        self.assertEqual(entry.dek_grams, other.dek_grams)
        self.assertEqual(entry.headline_grams, other.headline_grams)
        self.assertEqual(entry.body_grams, other.body_grams)
        self.assertEqual(entry.opener, other.opener)
        self.assertIn(("op", "flight", "reg"), entry.dek_grams)

    def test_story_facts_and_sources_carry_what_the_text_used(self):
        facts = fixtures.facts(
            fixtures.watch_a380(), place=fixtures.PLACES["coleman"], profiles=fixtures.A380_PROFILE
        )
        for index in range(8):
            story = editorial.render(facts, f"episode-{index}")
            used = story["facts"]
            self.assertEqual(used["id.registration"], "HL7635")
            self.assertEqual(used["obs.date"], "2026-09-27")
            cited = {source["id"] for source in story["sources"]}
            profile_used = {key for key in used if key.startswith("tp.")}
            self.assertEqual(
                cited & {"src." + key[3:] for key in profile_used}, {"src." + key[3:] for key in profile_used}
            )
            for source in story["sources"]:
                self.assertTrue(source["url"].startswith("https://"))

    def test_design_cases_render_without_relaxation(self):
        for case in fixtures.design_cases():
            facts = fixtures.case_facts(case)
            story, report = editorial.render_with_report(facts, case["name"])
            with self.subTest(case=case["name"]):
                self.assertFalse(report.fallback)
                self.assertFalse(report.variety_relaxed)
                self.assertEqual(public_prose.story_issues(story, facts), [])


class ReviewFindingTests(unittest.TestCase):
    """Regression tests for the findings of the two G1a code reviews."""

    def test_every_usable_angle_and_variant_can_render(self):
        samples = [kind_facts(kind) for kind in public_events.KIND_TYPES]
        samples += [fixtures.case_facts(case) for case in fixtures.design_cases()]
        rendered, seen = set(), set()
        for facts in samples:
            slots = editorial.build_slots(facts)
            seo = editorial.seo_title(facts, slots)
            check = public_prose.prose_checker(facts)
            builder = editorial._Builder(facts, slots, editorial.Corpus([]))
            for angle in editorial.angles_for(facts["kind"]):
                if not angle.usable(slots):
                    continue
                with self.subTest(kind=facts["kind"], angle=angle.id):
                    candidate = editorial._candidate(builder, angle, "probe", seo)
                    if angle is editorial.SNAPSHOT and not (
                        slots.get("alt") and slots.get("gs") and (slots.get("type") or slots.get("place"))
                    ):
                        # The snapshot stays an angle the AI writer may take for a thin record,
                        # but its template text, never padded, needs both readings and a type or
                        # place to reach the field minimums; the plain record covers the rest.
                        continue
                    self.assertIsNotNone(candidate)
                # A fixed headline is not free text: no length window applies to it.
                headline_limits = (
                    None
                    if facts["kind"] in public_prose.FIXED_HEADLINE_KINDS
                    else public_prose.FIELD_LIMITS["headline"]
                )
                for part, templates, limits in (
                    ("h", angle.headlines, headline_limits),
                    ("d", angle.deks, public_prose.FIELD_LIMITS["dek"]),
                    ("o", angle.openers, (20, 400)),
                ):
                    for template in templates:
                        if editorial.fill(template, slots)[0] is None:
                            continue
                        seen.add((angle.id, part, template))
                        text, _ = editorial.fill(template, slots, limits=limits)
                        if text and not check(text):
                            rendered.add((angle.id, part, template))
        self.assertEqual(seen - rendered, set())

    def test_a_negative_altitude_renders_with_its_sign(self):
        raw = fixtures.kind_event("outlier_archive_alt")
        for sample in raw["evidence"]["context_samples"]:
            if sample["archive_alt"] > 40000:
                sample["archive_alt"] = -50000
        raw["value"] = -50000
        raw["evidence"]["aircraft"]["archive_alt"] = -50000
        facts = fixtures.facts(raw)
        for index in range(4):
            story, report = editorial.render_with_report(facts, f"episode-{index}")
            text = " ".join([story["headline"], story["dek"], *story["paragraphs"]])
            with self.subTest(index=index):
                self.assertTrue(report.publishable, report.issues)
                self.assertIn("-50,000", text)
                self.assertNotRegex(text, r"(?<![-\d,])50,000 ft")
        restated = {
            **story,
            "paragraphs": [paragraph.replace("-50,000", "50,000") for paragraph in story["paragraphs"]],
        }
        self.assertTrue(
            any(
                "Unsupported quantity: 50,000 ft" in issue
                for issue in public_prose.story_issues(restated, facts)
            )
        )
        gap = fixtures.kind_event("altitude_gap")
        gap["evidence"]["aircraft"].update(alt_baro=-500, alt_geom=1300)
        gap["value"] = 1800.0
        facts = fixtures.facts(gap)
        story, report = editorial.render_with_report(facts, "episode")
        self.assertTrue(report.publishable, report.issues)
        self.assertIn("-500 ft by pressure", " ".join(story["paragraphs"]))
        self.assertEqual(editorial.unit_value(-500, "ft", signed=True), "-500 ft")
        self.assertEqual(editorial.unit_value(-500, "ft"), "500 ft")
        # A descent keeps its magnitude: the direction is in the words around it.
        descent = editorial.render(kind_facts("rapid_descent"), "episode")
        self.assertIn("14,200", " ".join([descent["headline"], descent["dek"], *descent["paragraphs"]]))

    def test_unlawful_interference_standfirsts_always_carry_the_caveat(self):
        for kind in ("special_squawk_7500", "special_squawk_unlawful", "archive_squawk_7500"):
            facts = kind_facts(kind)
            recent = []
            for index in range(5):
                story = editorial.render(facts, f"episode-{index}", recent=recent)
                with self.subTest(kind=kind, index=index):
                    self.assertRegex(story["dek"], r"does not (?:confirm|show)")
                recent.append(fixtures.recent_entry(story, facts))

    def test_rates_name_their_direction(self):
        facts = kind_facts("outlier_baro_rate")
        for index in range(6):
            story = editorial.render(facts, f"episode-{index}")
            text = " ".join([story["headline"], story["dek"], *story["paragraphs"]])
            with self.subTest(index=index):
                self.assertIn("descent", text)
                for match in re.finditer(r"[\d,]+ ft/min", text):
                    window = text[max(0, match.start() - 30) : match.end() + 12]
                    self.assertRegex(window, r"descent|climb", window)

    def test_spike_claims_need_a_lone_spike(self):
        raw = fixtures.kind_event("type_speed_mismatch")
        for sample in raw["evidence"]["context_samples"]:
            sample["gs"] = 1050.0 + sample["ts"] % 7
        facts = fixtures.facts(raw)
        self.assertNotIn("lone_spike", editorial.build_slots(facts))
        for index in range(6):
            text = " ".join(editorial.render(facts, f"episode-{index}")["paragraphs"]).lower()
            self.assertNotRegex(
                text, r"stands alone|single value that far|lone reading|disagrees with everything"
            )

    def test_readings_are_dated_by_their_own_time(self):
        raw = fixtures.watch_a380()
        raw["evidence"]["observed_at"] = raw["last_seen"]
        facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"], profiles=fixtures.A380_PROFILE)
        start = editorial.clock(story_facts.value(facts, "obs.start"))
        for index in range(10):
            story = editorial.render(facts, f"episode-{index}")
            for text in (story["dek"], *story["paragraphs"]):
                for sentence in sentences(text):
                    with self.subTest(sentence=sentence):
                        self.assertFalse(start in sentence and "39,000 ft" in sentence, sentence)

    def test_multilateration_tracks_leave_the_method_to_the_evidence_card(self):
        case = next(case for case in fixtures.design_cases() if case["name"].startswith("3 "))
        for facts in (fixtures.case_facts(case), kind_facts("notable_variant")):
            self.assertTrue(story_facts.value(facts, "track.mlat"))
            for index in range(6):
                story, report = editorial.render_with_report(facts, f"episode-{index}")
                text = " ".join(story["paragraphs"])
                with self.subTest(kind=facts["kind"], index=index):
                    # The positions are estimates; the evidence card says so, not a story sentence.
                    self.assertTrue(report.publishable)
                    self.assertEqual(public_prose.story_issues(story, facts), [])
                    self.assertNotIn("multilateration", text)
                    self.assertNotRegex(text, r"\b(?:exact|precise) positions?\b")

    def test_multilateration_is_never_claimed_for_altitude_or_rate(self):
        for kind in ("outlier_alt_baro", "outlier_archive_alt", "outlier_baro_rate"):
            self.assertNotIn("mlat_solution", {angle.id for angle in editorial.angles_for(kind)})

    def test_number_words_read_naturally(self):
        self.assertEqual(
            [editorial.article(word) for word in ("eighth", "11th", "18th", "12th", "fourth")],
            ["an eighth", "an 11th", "an 18th", "a 12th", "a fourth"],
        )
        self.assertEqual(editorial.duration(1), "1 second")
        self.assertEqual(editorial.duration(150), "2.5 minutes")
        raw = fixtures.kind_event("vintage")
        raw["evidence"]["aircraft"]["year"] = str(2026 - story_facts.MIN_AGE_YEARS)
        slots = editorial.build_slots(fixtures.facts(raw))
        self.assertEqual(slots["age"], str(story_facts.MIN_AGE_YEARS))
        # A younger airframe's age is no story.
        raw["evidence"]["aircraft"]["year"] = "2025"
        self.assertFalse({"age", "built"} & set(editorial.build_slots(fixtures.facts(raw))))

    def test_formation_spacing_is_a_closest_approach(self):
        facts = kind_facts("formation_flight")
        for index in range(8):
            text = " ".join(editorial.render(facts, f"episode-{index}")["paragraphs"]).lower()
            with self.subTest(index=index):
                self.assertNotRegex(text, r"stayed within|flew within|hold [\d.]+ nm")

    def test_watchlist_stories_name_the_entry_and_its_category_beside_plane_alert_db(self):
        raw = fixtures.kind_event("watchlist_match")
        raw["evidence"]["watchlist"].update(
            source_category="Dictator Alert", neutral_category="Political association", group="Gov"
        )
        facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"])
        self.assertEqual(story_facts.value(facts, "wl.category"), "Political association")
        self.assertEqual(story_facts.value(facts, "wl.list_category"), "Dictator Alert")
        self.assertEqual(story_facts.value(facts, "wl.association"), fixtures.WATCH_ASSOCIATION)
        options = [option["id"] for option in editorial.angle_options(facts)]
        self.assertEqual(options[0], "list_association")
        # The type name alone is no reason: the entry's category or tags are.
        reasons = ("Dictator Alert", "political association", "Business Jet", "Corporate")
        for index in range(8):
            story = editorial.render(facts, f"episode-{index}")
            text = " ".join([story["headline"], story["dek"], story["seo_title"], *story["paragraphs"]])
            with self.subTest(index=index):
                self.assertEqual(public_prose.story_issues(story, facts), [])
                self.assertIn(fixtures.WATCH_ASSOCIATION, text)
                self.assertTrue(any(reason in text for reason in reasons), text)
                self.assertIn("src.plane-alert-db", {source["id"] for source in story["sources"]})
                for sentence in sentences(text):
                    if fixtures.WATCH_ASSOCIATION in sentence or "Dictator Alert" in sentence:
                        self.assertIn("Plane Alert DB", sentence)
        # Named without the list beside it, the category is an unattributed claim.
        named = editorial.render(facts, "episode")
        named["paragraphs"] = [
            named["paragraphs"][0] + " It is a political association aircraft.",
            *named["paragraphs"][1:],
        ]
        self.assertIn("claim:watchlist_category_unattributed", public_prose.story_issues(named, facts))

    def test_a_watchlist_story_says_once_what_wikidata_calls_the_linked_organization(self):
        raw = fixtures.kind_event("watchlist_match")
        facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"], entities=fixtures.watch_entity())
        said = 0
        for index in range(12):
            story = editorial.render(facts, f"episode-{index}")
            text = " ".join(story["paragraphs"])
            with self.subTest(index=index):
                self.assertEqual(public_prose.story_issues(story, facts), [])
                self.assertLessEqual(text.count("aircraft leasing company in Ohio"), 1)
                if "aircraft leasing company in Ohio" in text:
                    said += 1
                    self.assertIn("Wikidata", text)
                    self.assertIn("src.wikidata.Q4242", {source["id"] for source in story["sources"]})
        self.assertGreater(said, 0)
        # The writer may restate it with an article, beside the entry or its link; Wikidata alone
        # attributes nothing, and its words name no place the aircraft went.
        story = editorial.render(facts, "episode-0")
        said = next(
            re.search(r"[^.]*Wikidata[^.]*\.", paragraph).group().strip()
            for paragraph in story["paragraphs"]
            if "Wikidata" in paragraph
        )
        name = fixtures.WATCH_ASSOCIATION
        for sentence, allowed in (
            (
                f"The entry's link leads to {name}, an aircraft leasing company in Ohio, according to Wikidata.",
                True,
            ),
            (
                f"Plane Alert DB links {name}, an aircraft leasing company in Ohio, according to Wikidata.",
                True,
            ),
            (f"Its link in Plane Alert DB leads to {name}, an aircraft leasing company in Ohio.", True),
            (f"According to Wikidata, {name} is an aircraft leasing company in Ohio.", False),
            (f"Wikidata calls it {name}, an aircraft leasing company in Ohio.", False),
            (f"Wikidata describes {name} as an aircraft leasing company in Ohio.", False),
            ("The flight ended in Ohio.", False),
        ):
            edited = {
                **story,
                "paragraphs": [paragraph.replace(said, sentence) for paragraph in story["paragraphs"]],
            }
            with self.subTest(sentence=sentence):
                issues = public_prose.story_issues(edited, facts)
                self.assertEqual(issues == [], allowed, issues)
                if sentence.startswith("Wikidata calls"):
                    # The whole phrase, restated as written, with nothing of the entry beside it.
                    self.assertIn("claim:organization_attribution", issues)
        museum = fixtures.facts(
            raw,
            place=fixtures.PLACES["coleman"],
            entities=fixtures.watch_entity("aviation museum in Van Buren Township, Michigan"),
        )
        story = editorial.render(museum, "episode-0")
        self.assertIn("aviation museum in Van Buren Township, Michigan", " ".join(story["paragraphs"]))
        self.assertEqual(public_prose.story_issues(story, museum), [])
        story["paragraphs"][-1] += " Later that hour it flew over Van Buren Township."
        self.assertTrue(
            any("Unsupported name" in issue for issue in public_prose.story_issues(story, museum)),
            public_prose.story_issues(story, museum),
        )
        # A description naming a kind of person never gets past the acceptance filter; were one
        # cached anyway, the story checks still refuse it.
        person = fixtures.facts(raw, entities=fixtures.watch_entity("American businessman and investor"))
        story = editorial.render(person, "episode")
        self.assertNotIn("businessman", " ".join(story["paragraphs"]))
        story["paragraphs"][-1] += (
            f" The entry's link leads to {fixtures.WATCH_ASSOCIATION}, American businessman and investor, "
            "according to Wikidata."
        )
        issues = public_prose.story_issues(story, person)
        self.assertTrue(any("names a kind of person" in issue for issue in issues), issues)

    def test_the_organization_sentence_takes_articles(self):
        faa = "Federal Aviation Administration"
        raw = fixtures.kind_event("watchlist_match")
        raw["evidence"]["watchlist"]["association"] = faa
        entities = fixtures.watch_entity(
            "United States government agency regulating civil aviation", label=faa, association=faa
        )
        facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"], entities=entities)
        self.assertEqual(
            editorial.build_slots(facts)["who"],
            "the Federal Aviation Administration, a United States government agency regulating civil aviation",
        )
        leasing = fixtures.facts(
            fixtures.kind_event("watchlist_match"),
            place=fixtures.PLACES["coleman"],
            entities=fixtures.watch_entity(),
        )
        self.assertEqual(
            editorial.build_slots(leasing)["who"],
            fixtures.WATCH_ASSOCIATION + ", an aircraft leasing company in Ohio",
        )
        # A name whose last word is a listed head noun takes "the"; any other stands alone.
        for built, phrase in (
            (facts, "the Federal Aviation Administration, a United"),
            (leasing, fixtures.WATCH_ASSOCIATION + ", an aircraft"),
        ):
            said = []
            for index in range(12):
                story = editorial.render(built, f"episode-{index}")
                with self.subTest(phrase=phrase, index=index):
                    self.assertEqual(public_prose.story_issues(story, built), [])
                said += [p for p in story["paragraphs"] if phrase in p]
            self.assertTrue(said, phrase)
        museum = fixtures.watch_entity(
            "aviation museum in Ohio", label="Acme Air Museum", association="Acme Air Museum"
        )
        raw["evidence"]["watchlist"]["association"] = "Acme Air Museum"
        built = fixtures.facts(raw, place=fixtures.PLACES["coleman"], entities=museum)
        self.assertEqual(
            editorial.build_slots(built)["who"], "the Acme Air Museum, an aviation museum in Ohio"
        )

    def test_an_organization_matched_by_an_alias_names_both(self):
        # Wikidata's label differs from the name the entry carries, which is one of its aliases.
        raw = fixtures.kind_event("watchlist_match")
        raw["evidence"]["watchlist"]["association"] = "Yankee Air Museum"
        entities = fixtures.watch_entity(
            "aviation museum in Van Buren Township, Michigan",
            label="Michigan Flight Museum",
            aliases=["Yankee Air Museum"],
            association="Yankee Air Museum",
        )
        facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"], entities=entities)
        slots = editorial.build_slots(facts)
        self.assertNotIn("who", slots)
        self.assertEqual(
            (slots["who_label"], slots["who_desc"]),
            ("the Michigan Flight Museum", "an aviation museum in Van Buren Township, Michigan"),
        )
        sentence = (
            "The entry's link leads to the Michigan Flight Museum, which the entry lists as Yankee Air "
            "Museum: an aviation museum in Van Buren Township, Michigan, according to Wikidata."
        )
        said = []
        for index in range(12):
            story = editorial.render(facts, f"episode-{index}")
            with self.subTest(index=index):
                self.assertEqual(public_prose.story_issues(story, facts), [])
            said += [p for p in story["paragraphs"] if sentence in p]
        self.assertTrue(said)
        # The description's place is still no name for the rest of the story.
        story["paragraphs"][-1] += " Later that hour it flew over Van Buren Township."
        self.assertTrue(
            any("Unsupported name" in issue for issue in public_prose.story_issues(story, facts)),
            public_prose.story_issues(story, facts),
        )
        # A name other than the entry's own between the label and the description supports nothing.
        story = editorial.render(facts, "episode-0")
        edited = " ".join(story["paragraphs"]).replace(
            "lists as Yankee Air Museum:", "lists as Detroit Air Museum:"
        )
        story["paragraphs"] = [edited]
        self.assertNotEqual(public_prose.story_issues(story, facts), [])

    def test_the_description_article_follows_the_sound_and_never_doubles(self):
        cases = (
            ("Airbus", "European multinational aerospace corporation", "Airbus, a European multinational"),
            ("Acme Air", "one of the regional airlines in Ohio", "Acme Air, one of the regional airlines"),
            (
                "Federal Aviation Administration",
                "U.S. government agency regulating civil aviation",
                "the Federal Aviation Administration, a U.S. government",
            ),
            ("Acme Air", "the state airline of Ohio", "Acme Air, the state airline"),
            (
                "Yankee Air Museum",
                "aviation museum in Van Buren Township, Michigan",
                "the Yankee Air Museum, an aviation museum",
            ),
        )
        for label, description, start in cases:
            raw = fixtures.kind_event("watchlist_match")
            raw["evidence"]["watchlist"]["association"] = label
            entities = fixtures.watch_entity(description, label=label, association=label)
            facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"], entities=entities)
            with self.subTest(description=description):
                self.assertTrue(editorial.build_slots(facts)["who"].startswith(start))
                for index in range(4):
                    story = editorial.render(facts, f"episode-{index}")
                    self.assertEqual(public_prose.story_issues(story, facts), [])
        self.assertEqual(
            [editorial.article(word) for word in ("useful", "unidentified", "one-off", "eagle")],
            ["a useful", "an unidentified", "a one-off", "an eagle"],
        )

    def test_a_watchlist_entry_without_description_or_tags_still_gives_its_reason(self):
        raw = fixtures.kind_event("watchlist_match")
        raw["evidence"]["watchlist"].update(
            source_category="Dictator Alert",
            neutral_category="Political association",
            tags=[],
            aircraft_description=None,
        )
        facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"])
        self.assertIsNone(story_facts.value(facts, "wl.description"))
        for index in range(12):
            story, report = editorial.render_with_report(facts, f"episode-{index}")
            with self.subTest(index=index):
                # The reason sentence stays even when the opener already names the list.
                self.assertTrue(report.publishable, report.issues)
                self.assertFalse(report.fallback)
                self.assertIn('"Dictator Alert"', " ".join([story["dek"], *story["paragraphs"]]))

    def test_every_plane_alert_category_note_gives_a_publishable_reason(self):
        # Without tags or a description, the category and its note are the only reason given.
        for category in watchlists.CATEGORY_NOTES:
            raw = fixtures.kind_event("watchlist_match")
            raw["evidence"]["watchlist"].update(
                source_category=category,
                neutral_category=watchlists.NEUTRAL_CATEGORIES.get(category, watchlists.GENERIC_CATEGORY),
                tags=[],
                aircraft_description=None,
            )
            facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"])
            for index in range(2):
                story, report = editorial.render_with_report(facts, f"episode-{index}")
                with self.subTest(category=category, index=index):
                    self.assertTrue(report.publishable, report.issues)
                    self.assertFalse(report.fallback)
                    self.assertIn(category, " ".join([story["headline"], story["dek"], *story["paragraphs"]]))

    def test_home_registrations_and_address_blocks_go_unsaid(self):
        home = kind_facts("high")
        self.assertEqual(story_facts.value(home, "reg.country"), story_facts.HOME_COUNTRY)
        slots = editorial.build_slots(home)
        self.assertFalse({"country_the", "prefix", "alloc_country"} & set(slots))
        for index in range(6):
            story = editorial.render(home, f"episode-{index}")
            self.assertNotIn(story_facts.HOME_COUNTRY, " ".join([story["dek"], *story["paragraphs"]]))
        abroad = editorial.build_slots(
            fixtures.facts(fixtures.watch_a380(), place=fixtures.PLACES["coleman"])
        )
        self.assertEqual((abroad["country_the"], abroad["alloc_country"]), ("South Korea", "South Korea"))

    def test_bad_profile_facts_are_screened(self):
        profiles = fixtures.profile(
            "A388",
            "Airbus built the A380 with two full-length passenger decks",
            "The A380 \u2014 the largest airliner \u2014 first flew in 2005.",
            "The A380 first flew in April 2005 and entered airline service in 2007.",
        )
        facts = fixtures.facts(fixtures.watch_a380(), place=fixtures.PLACES["coleman"], profiles=profiles)
        texts = [item["value"] for item in facts["facts"] if item["id"].startswith("tp.")]
        self.assertEqual(texts, ["The A380 first flew in April 2005 and entered airline service in 2007."])
        story, report = editorial.render_with_report(facts, "episode")
        self.assertFalse(report.fallback)
        self.assertEqual(public_prose.story_issues(story, facts), [])


class ClassPhraseSlots(unittest.TestCase):
    def test_slots_and_the_identity_sentence_use_the_class_and_code(self):
        raw = fixtures.unnamed("descending_spiral")
        facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"])
        slots = editorial.build_slots(facts)
        self.assertEqual(slots["type_class"], "twin-turboshaft helicopter")
        self.assertEqual(slots["type_class_a"], "a twin-turboshaft helicopter")
        self.assertEqual(slots["type_short"], "twin-turboshaft helicopter")
        self.assertEqual(slots["type_code"], "A129")
        self.assertNotIn("type", slots)
        story = editorial.render(facts, "class-1")
        text = " ".join(story["paragraphs"])
        self.assertIn("type code A129", text)
        self.assertRegex(text, public_prose.DATABASE_ATTRIBUTION)
        self.assertNotRegex(story["headline"], r"\bA129\b")
        self.assertEqual(public_prose.story_issues(story, facts), [])

    def test_the_fallback_title_starts_a_class_phrase_in_upper_case(self):
        facts = fixtures.facts(fixtures.unnamed("descending_spiral"), place=fixtures.PLACES["coleman"])
        slots = editorial.build_slots(facts)
        # With no template usable, the title is the type and identifier, dated.
        with mock.patch.object(editorial, "seo_candidates", return_value=()):
            title = editorial.seo_title(facts, slots)
        self.assertEqual(title, f"Twin-turboshaft helicopter {slots['ident']}, {slots['date_short']}")

    def test_a_type_limit_is_never_given_as_a_property_of_a_class_phrase(self):
        # The class phrase names no type, so it cannot carry a type certificate's figure.
        class_limit = re.compile(
            r"As a type, the (?:single|twin|three|four|six|eight)"
            r"|twin-jet airplane[^.]*(?:maximum operating|certified)"
        )
        rated = 0
        for kind in ("type_speed_mismatch", "type_altitude_mismatch"):
            facts = fixtures.unnamed_jet(kind)
            self.assertEqual(story_facts.value(facts, "id.type_class"), "twin-jet airplane")
            rated += bool(editorial.build_slots(facts).get("rated_phrase"))
            for key in ("episode", "class-1", "class-2", "class-3", "class-4"):
                with self.subTest(kind=kind, key=key):
                    story = editorial.render(facts, key)
                    self.assertNotRegex(" ".join(story["paragraphs"]), class_limit)
                    self.assertEqual(public_prose.story_issues(story, facts), [])
        self.assertTrue(rated)


class ExplainerDeks(unittest.TestCase):
    def test_the_data_explainer_deks_name_the_aircraft_they_describe(self):
        # A dek that opens on "Its" leans on a headline that may name fields, not the aircraft.
        for dek in editorial.DATA_EXPLAINER.deks:
            with self.subTest(dek=dek):
                self.assertIn("{ident}", dek)
                self.assertFalse(dek.startswith("Its "))


class TypeEchoes(unittest.TestCase):
    def test_the_registry_sentence_follows_any_naming_of_the_type_without_repeating_it(self):
        raw = fixtures.kind_event("type_speed_mismatch")
        raw = fixtures.with_registry(raw, "agrees", code=raw["evidence"]["aircraft"]["t"])
        evidence = fixtures.packet(raw)
        evidence["type_norm"] = fixtures.NORM
        facts = story_facts.build(evidence, fixtures.projection(evidence))
        builder = editorial._Builder(facts, editorial.build_slots(facts), editorial.Corpus([]))
        # The type's usual speeds name it in the plural (type_plural); the registry then says
        # "the same type".
        sentences = editorial._paragraphs(builder, ("norm", "registry_agrees"), "It flew.", "echo")[0]
        self.assertIn("Cessna 172s", sentences[1])
        self.assertEqual(sentences[2], "A second registry, adsbdb, lists the same type.")
        # With nothing naming the type before it, the registry sentence names it.
        sentences = editorial._paragraphs(builder, ("registry_agrees",), "It flew.", "echo")[0]
        self.assertEqual(
            sentences[1], "The aircraft database and adsbdb both list it as a Cessna 172 Skyhawk."
        )


class CaveatOrdering(unittest.TestCase):
    """A story's one caveat sits inside it, never last; only a TCAS advisory headline asks."""

    @staticmethod
    def body(facts, angle, key, extras=True):
        """(builder, sentences) as _paragraphs returns them for one angle and key, before
        _caveat_inside moves a closing caveat and _split makes paragraphs; sentences is None when
        the angle renders no headline, dek, and opener (_parts)."""
        slots = editorial.build_slots(facts)
        builder = editorial._Builder(facts, slots, editorial.Corpus([]))
        seed = editorial._hash(key, angle.id, editorial.PROSE_POLICY)
        parts = editorial._parts(builder, angle, seed)
        if parts is None:
            return builder, None
        with mock.patch.object(editorial, "EXTRA_BUILDERS", editorial.EXTRA_BUILDERS if extras else ()):
            sentences = editorial._paragraphs(builder, angle.body, parts[2][1], seed, parts[2][2])[0]
        return builder, sentences

    @staticmethod
    def usable(facts):
        slots = editorial.build_slots(facts)
        return [angle for angle in editorial.angles_for(facts["kind"]) if angle.usable(slots)]

    def test_every_angle_renders_a_story_that_closes_on_a_fact(self):
        for kind in sorted(editorial.KIND_ANGLES):
            facts = kind_facts(kind)
            for angle in self.usable(facts):
                for found in candidates(facts, (angle,), ["close-a", "close-b"]):
                    with self.subTest(kind=kind, angle=angle.id):
                        self.assertIsNotNone(found)
                        story = found[0]
                        self.assertEqual(public_prose.story_issues(story, facts), [])
                        closing = public_prose.last_sentence(story["paragraphs"])
                        self.assertFalse(
                            public_prose.closes_on_caveat(closing, public_prose.required_sentence(facts))
                        )

    def test_sentences_reach_the_split_with_the_caveat_inside(self):
        for kind in sorted(editorial.KIND_ANGLES):
            facts = kind_facts(kind)
            required = public_prose.required_sentence(facts)
            for angle in self.usable(facts):
                with mock.patch.object(editorial, "_split", wraps=editorial._split) as split:
                    candidates(facts, (angle,), ["inside"])
                for call in split.call_args_list:
                    with self.subTest(kind=kind, angle=angle.id):
                        self.assertFalse(public_prose.closes_on_caveat(call.args[0][-1], required))

    def test_padding_follows_the_limitation(self):
        checked = 0
        for kind in sorted(editorial.KIND_ANGLES):
            facts = kind_facts(kind)
            for angle in self.usable(facts):
                builder, sentences = self.body(facts, angle, "pad")
                _builder, plain = self.body(facts, angle, "pad", extras=False)
                if not sentences:
                    continue
                padding = [index for index, text in enumerate(sentences) if text not in plain]
                caveats = [
                    index
                    for index, text in enumerate(sentences)
                    if public_prose.closes_on_caveat(text, builder.required)
                ]
                if padding and caveats:
                    checked += 1
                    with self.subTest(kind=kind, angle=angle.id):
                        self.assertGreater(min(padding), max(caveats), sentences)
        self.assertGreater(checked, 10)

    def test_a_closing_caveat_swaps_with_the_sentence_before_it(self):
        aboard = "It does not show who was aboard."
        inside = editorial._caveat_inside
        self.assertEqual(inside(["It flew.", "It turned.", aboard]), ["It flew.", aboard, "It turned."])
        self.assertEqual(inside(["It flew.", "It turned."]), ["It flew.", "It turned."])
        self.assertEqual(inside([aboard]), [aboard])
        required = "This observation rests on one archived sample."
        self.assertEqual(inside(["It flew.", required], required), [required, "It flew."])
        # The required sentence is found whatever its spacing, as the story checks find it.
        spaced = required.replace(" rests", "  rests")
        self.assertEqual(inside(["It flew.", spaced], required), [spaced, "It flew."])
        # A restated type fact is the type's background, never the caveat.
        unknown = "Whether the type ever flew in service is unknown."
        self.assertEqual(inside(["It flew.", unknown], None, [unknown]), ["It flew.", unknown])
        # The short registry form leans on the sentence that named the type; the caveat goes
        # ahead of both when the opener is not the one that names the type.
        named = "The aircraft database lists N1 as a Cessna 172."
        same = "A second registry, adsbdb, lists the same type."
        self.assertEqual(inside(["It flew.", named, same, aboard]), ["It flew.", aboard, named, same])
        # The first sentence is the opener, and the opener skeleton is built from it, so a
        # caveat never moves ahead of an opener that names the type.
        self.assertEqual(inside([named, same, aboard]), [named, aboard, same])

    def test_the_plain_record_of_a_watchlist_entry_says_why_the_list_carries_it(self):
        facts = fixtures.facts(fixtures.kind_event("watchlist_match"), place=fixtures.PLACES["coleman"])
        slots = editorial.build_slots(facts)
        builder = editorial._Builder(facts, slots, editorial.Corpus([]))
        story, issues = editorial._plain_record(builder, "plain", editorial.seo_title(facts, slots))
        self.assertEqual(issues, [])
        self.assertIn('"Distinctive"', " ".join(story["paragraphs"]))

    def test_the_plain_record_keeps_its_opener_first_when_it_adds_the_attribution(self):
        # An opener and one caveat, then the attribution a type with no registration needs: the
        # caveat is placed after that insert, so the story opens on the opener and closes on a fact.
        # The kind's context line, which would otherwise close the story, is left out.
        raw = fixtures.kind_event("rapid_descent")
        raw["evidence"]["aircraft"].pop("r", None)
        facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"])
        facts["facts"] = [item for item in facts["facts"] if item["id"] != "ctx.kind"]
        self.assertIsNone(story_facts.value(facts, "id.registration"))
        slots = editorial.build_slots(facts)
        builder = editorial._Builder(facts, slots, editorial.Corpus([]))
        with (
            mock.patch.object(editorial, "MINIMAL_BODY", ("limit",)),
            mock.patch.object(editorial, "EXTRA_BUILDERS", ()),
        ):
            story, issues = editorial._plain_record(builder, "plain", editorial.seo_title(facts, slots))
        self.assertEqual(issues, [])
        written = editorial.sentences(" ".join(story["paragraphs"]))
        self.assertEqual(len(written), 3)
        self.assertTrue(written[0].startswith("On 20 September 2026"), written)
        self.assertEqual(written[1], slots["limit"])
        self.assertTrue(public_prose.DATABASE_ATTRIBUTION.search(written[2]), written)

    def test_a_restated_type_fact_leaves_room_for_the_storys_own_caveat(self):
        # The engine counts caveats as the checks do: a profile sentence that says "whether" or
        # "unknown" is no caveat, so the angle's limitation still has its place.
        unknown = "Whether the C-17 will get new engines is unknown."
        profiles = fixtures.profile("C17", unknown, publisher="Boeing")
        facts = fixtures.facts(
            fixtures.kind_event("military"), place=fixtures.PLACES["coleman"], profiles=profiles
        )
        self.assertEqual(story_facts.value(facts, "tp.C17.1"), unknown)
        found = 0
        for angle in self.usable(facts):
            if "limit" not in angle.body or "profile" not in angle.body:
                continue
            builder, sentences = self.body(facts, angle, "profile")
            if not sentences or unknown not in sentences:
                continue
            found += 1
            with self.subTest(angle=angle.id):
                limits = [text for text in sentences if public_prose.is_caveat(text) and text != unknown]
                self.assertEqual(len(limits), 1, sentences)
        self.assertTrue(found)

    def test_no_angle_asks_a_question_except_about_a_tcas_advisory(self):
        for kind, angles in editorial.KIND_ANGLES.items():
            for angle in angles:
                for form in angle.headlines:
                    with self.subTest(kind=kind, angle=angle.id, form=form):
                        if "?" in form:
                            self.assertEqual(kind, "tcas_ra")
                            self.assertIn("advisory", form)

    def test_split_allows_one_short_paragraph(self):
        sentences = [
            "N1 flew on 20 September near Coleman, Michigan at 3,000 ft.",
            "The aircraft database lists N1 as a Cessna 172, a light aircraft.",
        ]
        self.assertEqual(editorial._split(sentences), [" ".join(sentences)])


class AirportSentence(unittest.TestCase):
    def test_a_shape_story_can_say_the_track_came_within_miles_of_the_airport(self):
        airport = fixtures.BRIGHTON
        facts = fixtures.facts(
            fixtures.kind_event("descending_spiral"), place=fixtures.PLACES["coleman"], airport=airport
        )
        slots = editorial.build_slots(facts)
        self.assertEqual(slots["airport"], "Brighton Airport")
        self.assertEqual(slots["airport_miles"], "about 2 miles")
        stories = [editorial.render(facts, f"ap-{n}") for n in range(6)]
        self.assertTrue(
            any("within about 2 miles of Brighton Airport" in " ".join(s["paragraphs"]) for s in stories)
        )
        for story in stories:
            self.assertEqual(public_prose.story_issues(story, facts), [])

    def test_the_airport_sentence_is_left_out_when_recent_stories_carried_it(self):
        facts = fixtures.facts(
            fixtures.kind_event("descending_spiral"),
            place=fixtures.PLACES["coleman"],
            airport=fixtures.BRIGHTON,
        )
        # One recent sentence naming the field, in one form and at another distance, is enough.
        carried = "It came within about 5 miles of Brighton Airport."
        self.assertNotIn(
            carried,
            [editorial.fill(text, editorial.build_slots(facts))[0] for text in editorial.BUILDERS["airport"]],
        )
        recent = [{"kind": "circling", "date": story_facts.value(facts, "obs.date"), "paragraphs": [carried]}]
        for n in range(6):
            with self.subTest(n=n):
                story = editorial.render(facts, f"ap-{n}", recent=recent)
                self.assertNotIn("Brighton", " ".join(story["paragraphs"]))

    def test_the_recent_airport_name_is_matched_as_whole_words(self):
        facts = fixtures.facts(
            fixtures.kind_event("descending_spiral"),
            place=fixtures.PLACES["coleman"],
            airport=fixtures.BRIGHTON,
        )
        for carried, named in (
            ("It flew over New Brighton Airportville.", False),
            ("It crossed Brighton Airport's runway.", True),
            ("It passed near Brighton Airport.", True),
        ):
            recent = [
                {"kind": "circling", "date": story_facts.value(facts, "obs.date"), "paragraphs": [carried]}
            ]
            for n in range(6):
                with self.subTest(carried=carried, n=n):
                    story = editorial.render(facts, f"ap-{n}", recent=recent)
                    self.assertEqual("Brighton Airport" not in " ".join(story["paragraphs"]), named)

    def test_a_recent_story_naming_another_airport_keeps_this_ones(self):
        facts = fixtures.facts(
            fixtures.kind_event("descending_spiral"),
            place=fixtures.PLACES["coleman"],
            airport=fixtures.BRIGHTON,
        )
        other = "It came within about 3 miles of Howell Airport."
        recent = [{"kind": "circling", "date": story_facts.value(facts, "obs.date"), "paragraphs": [other]}]
        for n in range(6):
            with self.subTest(n=n):
                alone = " ".join(editorial.render(facts, f"ap-{n}")["paragraphs"])
                story = editorial.render(facts, f"ap-{n}", recent=recent)
                self.assertIn("Brighton Airport", alone)
                self.assertIn("Brighton Airport", " ".join(story["paragraphs"]))
                self.assertEqual(public_prose.story_issues(story, facts), [])

    def test_kinds_where_an_airport_reads_as_a_landing_or_hold_never_name_it(self):
        airport = {**fixtures.BRIGHTON, "nm": 0.6}
        required = {
            "rapid_descent",
            "rapid_climb",
            "vertical",
            "possible_go_around",
            "departure_return",
            "holding_stack",
            "prolonged_holding",
            "course_reversal",
            "tcas_ra",
            *(
                f"{prefix}_{code}"
                for prefix in ("special_squawk", "archive_squawk")
                for code in (7400, 7500, 7600, 7700)
            ),
            *(
                f"special_squawk_{status}"
                for status in ("unlawful", "nordo", "general", "lifeguard", "minfuel", "downed")
            ),
        }
        self.assertLessEqual(required, story_facts.AIRPORT_EXCLUDED_KINDS)
        for kind in sorted(story_facts.AIRPORT_EXCLUDED_KINDS):
            with self.subTest(kind=kind):
                facts = fixtures.facts(
                    fixtures.kind_event(kind), place=fixtures.PLACES["coleman"], airport=airport
                )
                self.assertNotIn("track.airport", story_facts.fact_map(facts))
                for n in range(4):
                    story = editorial.render(facts, f"ap-{kind}-{n}")
                    self.assertNotIn("Brighton", json.dumps(story))

    def test_miles_hedge_only_a_whole_number(self):
        nm = 1 / editorial.MILES_PER_NM
        for statute, text in (
            (0.97, None),
            (1.03, "about 1 mile"),
            (1.5, "1.5 miles"),
            (1.74, "1.7 miles"),
            (1.96, "about 2 miles"),
            (2.04, "about 2 miles"),
            (2.94, "2.9 miles"),
            (3.4, "about 3 miles"),
            (17.6, "about 18 miles"),
        ):
            with self.subTest(statute=statute):
                self.assertEqual(editorial.miles(statute * nm), text)

    def test_under_a_mile_reads_as_within_a_mile(self):
        airport = {**fixtures.BRIGHTON, "nm": 0.6}
        facts = fixtures.facts(fixtures.kind_event("descending_spiral"), airport=airport)
        self.assertEqual(editorial.build_slots(facts)["airport_miles"], "a mile")


class RegistrySentences(unittest.TestCase):
    def stories(self, facts):
        """Both mismatch angles' stories for a few keys; render() takes one angle per facts digest."""
        found = candidates(
            facts, (editorial.MISMATCH, editorial.MISMATCH_EXPLAINER), [f"reg-{n}" for n in range(4)]
        )
        self.assertNotIn(None, found)
        # The last story is render()'s, whose angle may be an outlier angle without the sentence.
        return [story for story, _entry, _issues in found] + [editorial.render(facts, "reg-render")]

    def test_a_mismatch_story_states_the_other_registrys_type_without_a_cause(self):
        facts = registry_facts("disagrees")
        stated = re.compile(r"registry, adsbdb, lists (?:the same|this) address as a Boeing 757-200\.")
        stories = self.stories(facts)
        for story in stories:
            text = " ".join(story["paragraphs"])
            if story is not stories[-1]:
                self.assertRegex(text, stated)
            self.assertEqual(public_prose.story_issues(story, facts), [])
            self.assertNotRegex(text, r"\b(?:because|explains?|fits|wrong)\b")

    def test_an_agreeing_registry_is_named_beside_the_listed_type(self):
        facts = registry_facts("agrees", code=None)
        for story in self.stories(facts)[:-1]:
            self.assertIn(
                "The aircraft database and adsbdb both list it as a Cessna 172 Skyhawk.",
                story["paragraphs"][-1],
            )
            self.assertEqual(public_prose.story_issues(story, facts), [])


class NormSentence(unittest.TestCase):
    def stories(self, facts, angles):
        found = candidates(facts, angles, [f"norm-{n}" for n in range(3)])
        self.assertNotIn(None, found)
        return [story for story, _entry, _issues in found]

    def test_speed_stories_give_the_types_usual_speed_in_these_records(self):
        band = r"in level flight between 30,000 and 40,000 ft usually show ground-speed readings of 380 to 430 knots"
        stated = re.compile(
            rf"In these records, [A-Z][\w -]+s {band}\.|[A-Z][\w -]+s {band} in these records\."
        )
        for kind, angles in (
            ("seasonal_speed_record", editorial.SEASON_ANGLES),
            ("type_speed_mismatch", (editorial.MISMATCH, editorial.MISMATCH_EXPLAINER)),
        ):
            facts = norm_facts(kind)
            slots = editorial.build_slots(facts)
            self.assertEqual(slots["norm_band"], "between 30,000 and 40,000 ft")
            self.assertEqual(slots["norm_phase"], "in level flight")
            self.assertEqual(slots.sources["type_plural"], slots.sources["type_short"])
            for story in self.stories(facts, angles):
                with self.subTest(kind=kind, angle=story["angle"]):
                    self.assertRegex(" ".join(story["paragraphs"]), stated)
                    self.assertEqual(public_prose.story_issues(story, facts), [])
                    self.assertTrue({"norm.gs_low", "norm.gs_high"} <= set(story["facts"]))

    def test_the_plural_of_a_type_short_name(self):
        slots = editorial.Slots()
        for short, plural in (
            ("Skyhawk", "Skyhawks"),
            ("Citation Ultra", "Citation Ultras"),
            ("Lynx", "Lynxes"),
        ):
            slots.put("type", short, "id.type")
            slots.put("type_short", short, "id.type")
            editorial._norm_slots(
                slots,
                lambda key: {
                    "norm.gs_low": 1,
                    "norm.gs_high": 2,
                    "norm.band_low": 0,
                    "norm.band_high": 10000,
                    "norm.phase": "climb",
                }[key],
                "x",
            )
            self.assertEqual(slots["type_plural"], plural)
        self.assertEqual(slots["norm_phase"], "climbing")
        # A name ending in a consonant and "y" ("Husky") would need a spelling the checks do not
        # read as the type, so it gets no norm sentence.
        for short in ("Husky", "Navajo Chieftain Pony"):
            slots = editorial.Slots()
            slots.put("type", short, "id.type")
            slots.put("type_short", short, "id.type")
            editorial._norm_slots(slots, lambda key: {"norm.phase": "level"}.get(key, 1), "x")
            self.assertFalse({"type_plural", "norm_low", "norm_band"} & set(slots))


class ExplainerHeadlines(unittest.TestCase):
    """The outlier, data, and mismatch explainers state a fact in the headline and pass the checks."""

    @staticmethod
    def form(template):
        """A headline form as a pattern: each slot any text, each bracketed part optional."""
        pattern = re.escape(template)
        pattern = re.sub(r"\\\{[^}]*\\\}", ".+?", pattern)
        return re.sub(r"\\\[(.*?)\\\]", r"(?:\1)?", pattern)

    def test_each_explainer_renders_a_statement_headline(self):
        cases = [
            *(
                (kind, editorial.KIND_ANGLES[kind])
                for kind in ("outlier_gs", "outlier_alt_baro", "outlier_baro_rate")
            ),
            ("identity_conflict", editorial.IDENTITY_CONFLICT_ANGLES),
            ("type_speed_mismatch", editorial.MISMATCH_ANGLES),
            ("type_altitude_mismatch", editorial.ALTITUDE_MISMATCH_ANGLES),
        ]
        for kind, angles in cases:
            explainer = next(angle for angle in angles if angle.id == "explainer")
            facts = kind_facts(kind)
            forms = [re.compile(self.form(form) + "$") for form in explainer.headlines]
            for n, found in enumerate(candidates(facts, (explainer,), [f"explain-{n}" for n in range(4)])):
                with self.subTest(kind=kind, n=n):
                    self.assertIsNotNone(found)
                    story = found[0]
                    self.assertTrue(any(form.match(story["headline"]) for form in forms), story["headline"])
                    self.assertEqual(public_prose.story_issues(story, facts), [])

    @staticmethod
    def words(template):
        """A template's content words, slots and stopwords aside."""
        text = re.sub(r"\{[^}]*\}", " ", template)
        return {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z'-]*", text)} - public_prose.STOPWORDS

    def test_explainer_standfirsts_do_not_repeat_the_headline_forms(self):
        explainers = {
            angle.id + ":" + kind: angle
            for kind, angles in editorial.KIND_ANGLES.items()
            for angle in angles
            if angle.id == "explainer"
        }
        self.assertTrue(any(key.endswith(":tcas_ra") for key in explainers))
        for key, angle in explainers.items():
            headline_words = set().union(*(self.words(form) for form in angle.headlines))
            for dek in angle.deks:
                with self.subTest(angle=key, dek=dek):
                    self.assertLess(len(self.words(dek) & headline_words), 3)
        # The TCAS explainer keeps its question forms.
        tcas = next(angle for angle in editorial.KIND_ANGLES["tcas_ra"] if angle.id == "explainer")
        self.assertTrue(all(form.endswith("?") for form in tcas.headlines))


class FinalReviewTests(unittest.TestCase):
    """Regression tests for the final reviews' story-text findings."""

    def texts(self, story):
        return " ".join([story["headline"], story["dek"], story["seo_title"], *story["paragraphs"]])

    def test_every_season_record_angle_states_the_record_from_its_baseline(self):
        for baseline in ("2026-09-01", "2026-09-12"):
            raw = fixtures.kind_event("seasonal_speed_record")
            raw["evidence"]["season_record"] = {
                "previous_max_gs": 622.0,
                "season_start": "2026-09-01",
                "baseline_start": baseline,
            }
            facts = fixtures.facts(raw)
            since = editorial.day_month(baseline)
            for index in range(6):
                story = editorial.render(facts, f"episode-{index}")
                with self.subTest(baseline=baseline, index=index):
                    self.assertIn(f"since {since}", story["headline"] + " " + story["dek"])
                    self.assertNotIn("here", self.texts(story))
                    if baseline != "2026-09-01":
                        self.assertNotIn("season", self.texts(story).lower())

    def test_first_sightings_are_firsts_in_these_records_without_day_counts(self):
        for kind in ("first_type_here", "first_country_here"):
            facts = kind_facts(kind)
            self.assertEqual(
                editorial.angles_for(kind)[0].id,
                "first_in_records" if kind == "first_type_here" else "allocation",
            )
            for index in range(6):
                story = editorial.render(facts, f"episode-{index}")
                text = self.texts(story)
                with self.subTest(kind=kind, index=index):
                    self.assertEqual(public_prose.story_issues(story, facts), [])
                    self.assertIn("these records", text)
                    self.assertNotRegex(text, r"\bhere\b|\bdays\b|\bjournal\b|\bstor(?:y|ies)\b")
        # The headline is fixed: the type, then "spotted for the first time", whatever the key.
        headlines = {
            editorial.render(kind_facts("first_type_here"), f"episode-{index}")["headline"]
            for index in range(12)
        }
        self.assertEqual(headlines, {"Embraer C-390 Millennium spotted for the first time"})
        self.assertEqual(
            editorial.fixed_headline(kind_facts("first_type_here")),
            "Embraer C-390 Millennium spotted for the first time",
        )
        country = kind_facts("first_country_here")
        self.assertEqual(
            editorial.fixed_headline(country), "First Portugal address spotted: Embraer C-390 Millennium"
        )
        self.assertEqual(editorial.render(country, "episode")["headline"], editorial.fixed_headline(country))
        untyped = fixtures.kind_event("first_country_here")
        for key in ("t", "desc"):
            untyped["evidence"]["aircraft"].pop(key, None)
        untyped = fixtures.facts(untyped)
        registration = story_facts.value(untyped, "id.registration")
        self.assertEqual(editorial.fixed_headline(untyped), f"First Portugal address spotted: {registration}")
        self.assertIsNone(editorial.fixed_headline(kind_facts("watch_type")))
        # A fixed headline is shorter than the ordinary minimum and still passes.
        short = kind_facts("first_type_here")
        for item in short["facts"]:
            if item["id"] == "id.type":
                item.update(value="Bell 407", short="407", aliases=[])
        story, report = editorial.render_with_report(short, "episode")
        self.assertTrue(report.publishable, report.issues)
        self.assertEqual(story["headline"], "Bell 407 spotted for the first time")
        # Its shared skeleton is the form, not a repeat; the dek and opener still vary.
        recent = [fixtures.recent_entry(story, short)]
        again = editorial.render_with_report(kind_facts("first_type_here"), "episode-2", recent=recent)[1]
        self.assertNotIn("headline_similar", again.issues)
        for kind in editorial.FIXED_HEADLINES:
            self.assertIn(kind, public_prose.FIXED_HEADLINE_KINDS)
        self.assertEqual(set(editorial.FIXED_HEADLINES), set(public_prose.FIXED_HEADLINE_KINDS))

    def test_watchlist_headlines_lead_with_the_aircraft_and_the_entry_name(self):
        facts = kind_facts("watchlist_match")
        # The fixture's entry names someone: the category angle (for entries that name
        # nobody) is never offered, so every headline carries the name.
        self.assertNotIn("list_category", {option["id"] for option in editorial.angle_options(facts)})
        for index in range(8):
            story = editorial.render(facts, f"episode-{index}")
            with self.subTest(index=index):
                self.assertIn(fixtures.WATCH_ASSOCIATION, story["headline"])
                self.assertRegex(story["headline"], r" N51EM linked to .+ by Plane Alert DB$")
                self.assertIn("Plane Alert DB", " ".join([story["dek"], *story["paragraphs"]]))
                self.assertNotIn("'s ", story["headline"])
        self.assertEqual(editorial.series(["A", "B", "C"]), "A, B, and C")
        slots = editorial.build_slots(facts)
        self.assertEqual(slots["wl_tags"], '"Business Jet" and "Corporate"')
        tagged = fixtures.kind_event("watchlist_match")
        tagged["evidence"]["watchlist"]["tags"] = ["Business Jet", "Corporate", "Charter"]
        self.assertEqual(
            editorial.build_slots(fixtures.facts(tagged))["wl_tags"],
            '"Business Jet", "Corporate", and "Charter"',
        )

    def test_glitch_and_record_templates_state_the_type_limit_and_no_cause(self):
        self.assertNotIn("outlier_hyp", editorial.BUILDERS)
        self.assertNotIn("mismatch_hyp", editorial.BUILDERS)
        self.assertNotIn("env_speed", editorial.BUILDERS)
        with fixtures.rated_limits():
            speed = kind_facts("type_speed_mismatch")
            altitude = kind_facts("type_altitude_mismatch")
            record = kind_facts("seasonal_speed_record")
            outlier = fixtures.facts(fixtures.outlier_cessna())
        # A ground-speed record states no certified speed: the type's limits are airspeeds.
        self.assertIsNone(story_facts.value(record, "lim.speed"))
        self.assertIsNone(story_facts.value(record, "lim.mach"))
        self.assertFalse(any(source["id"] == "src.limits.A388" for source in record["sources"]))
        for angle in editorial.SEASON_ANGLES:
            self.assertNotIn("rated", angle.body, angle.id)
        banned = re.compile(r"decoding|corrupt|metadata|site's|threshold|flag|most likely", re.I)
        for name, facts in (
            ("speed", speed),
            ("altitude", altitude),
            ("record", record),
            ("outlier", outlier),
        ):
            for index in range(4):
                story = editorial.render(facts, f"episode-{index}")
                text = " ".join([story["headline"], story["dek"], *story["paragraphs"]])
                with self.subTest(name=name, index=index):
                    self.assertEqual(public_prose.story_issues(story, facts), [])
                    self.assertNotRegex(text, banned)
        for angle in (*editorial.MISMATCH_ANGLES, *editorial.ALTITUDE_MISMATCH_ANGLES):
            self.assertIn("rated", angle.body, angle.id)
        # The speed limit is named as the airspeed it is, in a sentence of its own.
        for index in range(4):
            rendered = editorial.render(speed, f"episode-{index}")
            found = [
                sentence
                for text in (rendered["dek"], *rendered["paragraphs"])
                for sentence in re.split(r"(?<=\.) ", text)
                if "163 knots" in sentence
            ]
            with self.subTest(index=index):
                self.assertTrue(found)
                for sentence in found:
                    self.assertIn("never-exceed airspeed of 163 knots", sentence)
                    self.assertNotRegex(sentence, r"1,063|ground speed|report")
        rendered = editorial.render(altitude, "e")
        text = " ".join([rendered["dek"], *rendered["paragraphs"]])
        self.assertIn("is certified to 14,000 ft", text)
        # It says "certified" once: "By its certification, ... is certified to" said it twice.
        self.assertNotIn("By its certification", text)
        self.assertNotIn("By its certification", " ".join(editorial.BUILDERS["rated"]))
        self.assertIn("src.limits.C172", {source["id"] for source in rendered["sources"]})
        # Without the dataset the stories still render, saying nothing about the limit.
        plain = kind_facts("type_altitude_mismatch")
        self.assertIsNone(story_facts.value(plain, "lim.altitude"))
        self.assertEqual(public_prose.story_issues(editorial.render(plain, "e"), plain), [])

    def test_the_data_explainer_names_only_flagged_fields(self):
        raw = fixtures.kind_event("identity_conflict")
        checks = raw["evidence"]["identity_checks"]
        for key in ("registration_matches_address", "allocation_matches_registration"):
            checks[key] = None
        facts = fixtures.facts(raw)
        slots = editorial.build_slots(facts)
        self.assertEqual(slots["callsign_conflict"], "N4417QK")
        self.assertNotIn("emitter", slots)
        self.assertNotIn("alt_baro", slots)
        story, _ = editorial._candidate(
            editorial._Builder(facts, slots, editorial.Corpus([])),
            editorial.DATA_EXPLAINER,
            "episode",
            editorial.seo_title(facts, slots),
        )[:2]
        text = " ".join(story["paragraphs"])
        self.assertIn("N4417QK", text)
        self.assertNotRegex(text, r"emitter category|altitude fields|by satellite")
        # Without a flagged field the explainer does not apply.
        plain = fixtures.facts(fixtures.kind_event("correlated_data_anomaly"))
        self.assertFalse(editorial.DATA_EXPLAINER.usable(editorial.build_slots(plain)))

    def test_circling_groups_are_not_called_holding(self):
        facts = kind_facts("holding_stack")
        for index in range(6):
            story = editorial.render(facts, f"episode-{index}")
            with self.subTest(index=index):
                self.assertNotRegex(story["headline"] + story["seo_title"], r"\bhold(?:s|ing)?\b")

    def test_no_template_story_places_the_reader_here_or_mentions_this_site(self):
        for kind in public_events.KIND_TYPES:
            story = editorial.render(kind_facts(kind), f"episode-{kind}")
            for sentence in re.split(r"(?<=[.!?])\s+", self.texts(story)):
                with self.subTest(kind=kind, sentence=sentence):
                    self.assertNotRegex(sentence, r"\bhere\b")
                    self.assertIsNone(public_prose.SELF_REFERENCE.search(sentence))
                    self.assertIsNone(public_prose.DAY_COUNT.search(sentence))

    def test_a_time_after_midnight_always_carries_its_date(self):
        start = fixtures.stamp("2026-09-30T23:55:00Z")
        dated = (("m_clock", "m_day_month"), ("peak_clock", "peak_day_month"))
        for kind in public_events.KIND_TYPES:
            raw = fixtures.kind_event(kind, start)
            raw["last_seen"] = max(raw["last_seen"], start + 900)
            # The saved readings come from after midnight too.
            raw["evidence"]["observed_at"] = start + 480
            facts = fixtures.facts(raw)
            slots = editorial.build_slots(facts)
            pairs = [(slots[clock], slots[day]) for clock, day in dated if slots.get(day)]
            if not pairs:
                continue
            for index in range(3):
                story = editorial.render(facts, f"episode-{index}")
                text = " ".join([story["headline"], story["dek"], *story["paragraphs"]])
                for sentence in re.split(r"(?<=\.)\s+", text):
                    for clock, day in pairs:
                        with self.subTest(kind=kind, sentence=sentence):
                            if clock in sentence:
                                self.assertIn(day, sentence)

    def test_every_builder_can_be_reached(self):
        named = {name for angles in editorial.KIND_ANGLES.values() for angle in angles for name in angle.body}
        named |= {name for angle in editorial.GENERIC_ANGLES for name in angle.body}
        named |= set(editorial.EXTRA_BUILDERS) | set(editorial.MINIMAL_BODY) | {"mlat", "identity_ref"}
        # The context line is placed in every body of a kind that has one (_with_context).
        named.add(editorial.CONTEXT_BUILDER)
        named |= {name for names in editorial.BUILDER_FALLBACKS.values() for name in names}
        self.assertEqual(set(editorial.BUILDERS) - named, set())

    def test_a_watchlist_reading_keeps_its_own_time(self):
        facts = kind_facts("watchlist_match")
        for index in range(8):
            story = editorial.render(facts, f"episode-{index}")
            with self.subTest(index=index):
                self.assertNotIn("last report", story["dek"])


if __name__ == "__main__":
    unittest.main()
