"""The kind registry holds every kind and the properties its tables derive from."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import (
    curation,
    editorial,
    kinds,
    maneuvers,
    public_events,
    public_prose,
    public_writer,
    rules,
    stories,
    story_facts,
)

JOURNAL = ROOT / "tools" / "ghost-theme" / "journal.mjs"
STORY_KINDS = kinds.where(story=True)


class RegistryShape(unittest.TestCase):
    def test_story_kinds_are_the_registry_entries_flagged_story(self):
        self.assertEqual(kinds.where(story=True), frozenset(stories.STORY_KINDS))

    def test_the_registry_holds_the_story_and_episode_kinds_and_the_two_context_kinds(self):
        self.assertEqual(
            set(kinds.KINDS),
            set(stories.STORY_KINDS) | set(maneuvers.KINDS) | {"distant", "vfr_code_above_fl180"},
        )

    def test_the_two_context_kinds_are_registered(self):
        self.assertEqual(kinds.kind("distant").origin, "context")
        self.assertEqual(kinds.kind("vfr_code_above_fl180").origin, "context")

    def test_each_entry_is_registered_under_its_own_name(self):
        for name, entry in kinds.KINDS.items():
            with self.subTest(kind=name):
                self.assertEqual(entry.name, name)

    def test_where_matches_properties_by_equality(self):
        self.assertEqual(kinds.where(rated="altitude"), frozenset({"above_rated_altitude"}))
        self.assertEqual(
            kinds.where(origin="context", story=False), frozenset({"distant", "vfr_code_above_fl180"})
        )
        self.assertEqual(kinds.where(numeric_fields=("gs",)), frozenset({"fast", "type_speed_mismatch"}))
        self.assertEqual(kinds.where(fixed_score=(30, None)), frozenset({"distant"}))
        self.assertEqual(kinds.where(family="outlier", limit_dimension="speed"), frozenset({"outlier_gs"}))

    def test_an_unknown_property_is_refused(self):
        with self.assertRaises(TypeError):
            kinds.where(no_such_property=True)
        with self.assertRaises(TypeError):
            kinds.table("no_such_property")

    def test_unknown_kind_raises(self):
        with self.assertRaises(KeyError):
            kinds.kind("no_such_kind")

    def test_table_keeps_a_falsy_value_that_is_set(self):
        self.assertEqual(kinds.table("interest")["selected_altitude_overshoot"], 0)

    def test_table_refuses_an_order_that_does_not_name_exactly_its_kinds(self):
        with self.assertRaises(ValueError):
            kinds.table("rated_field", order=("above_rated_altitude",))
        with self.assertRaises(ValueError):
            kinds.table("rated_field", order=("above_rated_altitude", "above_rated_speed", "fast"))
        self.assertEqual(
            list(kinds.table("rated_field", order=("above_rated_speed", "above_rated_altitude"))),
            ["above_rated_speed", "above_rated_altitude"],
        )

    def test_the_squawk_families_are_the_status_tables(self):
        live = {kinds.LIVE_SQUAWK_PREFIX + status for status in kinds.STATUS_CODES | kinds.STATUS_NAMES}
        archived = {kinds.ARCHIVE_SQUAWK_PREFIX + code for code in kinds.STATUS_CODES}
        self.assertEqual(frozenset(stories.SPECIAL_KINDS), live | archived)
        self.assertEqual(kinds.where(origin="special"), live | archived)

    def test_the_status_tables_are_re_exported_unchanged(self):
        for module in (rules, maneuvers):
            with self.subTest(module=module.__name__):
                self.assertIs(module.STATUS_MEANINGS, kinds.STATUS_MEANINGS)
                self.assertIs(module.STATUS_CODES, kinds.STATUS_CODES)
                self.assertIs(module.STATUS_NAMES, kinds.STATUS_NAMES)
        self.assertIs(rules.SQUAWK_PREFIXES, kinds.SQUAWK_PREFIXES)
        self.assertIs(maneuvers.PRIORITY_STATUSES, kinds.PRIORITY_STATUSES)

    def test_property_values_are_data_only(self):
        def data(value):
            if isinstance(value, tuple):
                return all(data(item) for item in value)
            return value is None or isinstance(value, (bool, int, str))

        for name, entry in kinds.KINDS.items():
            for prop in kinds.PROPERTIES:
                with self.subTest(kind=name, prop=prop):
                    self.assertTrue(data(getattr(entry, prop)))

    def test_the_registry_imports_nothing_from_the_package(self):
        source = Path(kinds.__file__).read_text()
        self.assertNotRegex(source, r"(?m)^\s*from\s+\.")
        project = {path.stem for path in Path(kinds.__file__).parent.glob("*.py")} - {"kinds"}
        for name in sorted(project | {"wtf_adsb_publisher"}):
            with self.subTest(module=name):
                self.assertNotRegex(source, rf"(?m)^\s*(from|import)\s+{re.escape(name)}\b")


class DerivedSetsKeepTheirRelations(unittest.TestCase):
    """What the replaced literals and unions stated, now that each derives from one property."""

    def test_status_exceptions_are_the_live_statuses_less_the_priority_declarations(self):
        statuses = kinds.STATUS_CODES | (kinds.STATUS_NAMES - kinds.PRIORITY_STATUSES)
        self.assertEqual(
            curation.STATUS_EXCEPTIONS,
            frozenset(kinds.LIVE_SQUAWK_PREFIX + status for status in statuses),
        )

    def test_episode_pattern_kinds_are_the_episode_kinds_outside_a_journal_category(self):
        self.assertEqual(kinds.where(origin="episode", pattern=True), frozenset(maneuvers.PATTERN_KINDS))
        self.assertEqual(stories.EPISODE_KINDS, maneuvers.KINDS)

    def test_the_unions_the_literals_spelled_out_still_hold(self):
        self.assertEqual(
            curation.SERIES_KINDS,
            frozenset({"identity_conflict", "winds_aloft_day"}) | curation.RECORD_KINDS | rules.RATED_KINDS,
        )
        self.assertLessEqual(stories.FIRST_SIGHTING_KINDS, set(kinds.where(cross_day=True)))
        self.assertEqual(curation.FIRST_KINDS, frozenset(stories.FIRST_SIGHTING_KINDS))


class PublisherTablesAgree(unittest.TestCase):
    """The publisher-side tables that rest on more than their property."""

    def test_site_topic_matches_the_topics_file(self):
        topics = story_facts.table("topics")["topics"]
        by_kind = {name: topic for topic, entry in topics.items() for name in entry["kinds"]}
        self.assertEqual(kinds.table("site_topic"), by_kind)

    def test_rated_limit_facts_follow_the_kind_limit_dimension(self):
        dimensions = kinds.table("limit_dimension")
        self.assertEqual(set(story_facts.RATED_LIMIT_KINDS), set(dimensions))
        for name, dimension in dimensions.items():
            with self.subTest(kind=name):
                self.assertIs(story_facts.RATED_LIMIT_KINDS[name], story_facts.LIMIT_FACTS[dimension])
        self.assertEqual(
            kinds.where(limit_dimension="speed"), frozenset({"type_speed_mismatch", "outlier_gs"})
        )


class WordingTablesCoverTheRegistry(unittest.TestCase):
    """Wording and behaviour stay in code, keyed by registered kinds; each table covers today's rule."""

    def test_every_wording_table_is_keyed_by_story_kinds(self):
        for name, table in (
            ("editorial.KIND_ANGLES", editorial.KIND_ANGLES),
            ("editorial.SEO_FACTS", editorial.SEO_FACTS),
            ("editorial.PAIR_SEO", editorial.PAIR_SEO),
            ("editorial.SHAPES", editorial.SHAPES),
            ("editorial.KIND_LIMIT_PREFERENCES", editorial.KIND_LIMIT_PREFERENCES),
            ("editorial.REQUIRED_BUILDERS", editorial.REQUIRED_BUILDERS),
            ("public_events.KIND_LIMITATIONS", public_events.KIND_LIMITATIONS),
            ("public_prose.REQUIRED_SENTENCES", public_prose.REQUIRED_SENTENCES),
            ("public_prose.FIXED_HEADLINES", public_prose.FIXED_HEADLINES),
            ("public_prose.FIXED_HEADLINE_KINDS", public_prose.FIXED_HEADLINE_KINDS),
            ("story_facts.HYPOTHESES", story_facts.HYPOTHESES),
        ):
            with self.subTest(table=name):
                self.assertTrue(table)
                self.assertLessEqual(set(table), STORY_KINDS, set(table) - STORY_KINDS)

    def test_angles_cover_every_story_kind(self):
        self.assertEqual(set(editorial.KIND_ANGLES), STORY_KINDS)

    def test_title_facts_cover_the_kinds_without_a_title_of_their_own(self):
        # Subject and first-sighting kinds lead with the aircraft, and a pair has its own title.
        self.assertEqual(editorial.PAIR_SEO.keys(), kinds.where(pair=True))
        self.assertEqual(
            set(editorial.SEO_FACTS),
            STORY_KINDS
            - kinds.where(slug_subject=True)
            - kinds.where(pair=True)
            - kinds.where(first_sighting=True),
        )

    def test_fixed_headlines_are_the_first_sightings(self):
        self.assertEqual(set(public_prose.FIXED_HEADLINES), kinds.where(first_sighting=True))
        self.assertEqual(public_prose.FIXED_HEADLINE_KINDS, kinds.where(first_sighting=True))

    def test_required_sentences_are_the_record_and_rated_kinds(self):
        self.assertEqual(
            set(public_prose.REQUIRED_SENTENCES),
            kinds.where(record=True) | set(kinds.table("rated")),
        )
        self.assertLessEqual(set(public_prose.REQUIRED_SENTENCES), set(editorial.REQUIRED_BUILDERS))

    def test_shapes_are_movement_patterns_and_hold_the_durative_ones(self):
        self.assertLessEqual(set(editorial.SHAPES), kinds.where(pattern=True))
        self.assertEqual(editorial.SHAPE_KINDS, tuple(editorial.SHAPES))
        self.assertLessEqual(editorial.DURATIVE_SHAPES, set(editorial.SHAPES))

    def test_the_transponder_angle_kinds_are_the_squawk_family(self):
        self.assertEqual(
            set(editorial.SQUAWK_CODE_KINDS) | set(editorial.STATUS_CODE_KINDS), kinds.where(family="squawk")
        )
        self.assertFalse(set(editorial.SQUAWK_CODE_KINDS) & set(editorial.STATUS_CODE_KINDS))

    def test_kind_notes_name_kinds_or_prefixes_of_story_kinds(self):
        prefixes = {*kinds.SQUAWK_PREFIXES, "outlier_", "gnss_integrity_"}
        for keys, _note in public_writer.KIND_NOTES:
            for key in keys:
                with self.subTest(key=key):
                    self.assertTrue(key in STORY_KINDS or key in prefixes, key)
                    self.assertTrue(any(name.startswith(key) for name in STORY_KINDS), key)


class FormatsAgreeWithTheRegistry(unittest.TestCase):
    """The topics file and the theme's key-facts map name registered kinds and their sections."""

    def test_each_topic_routes_its_kinds_to_their_section(self):
        data = story_facts.table("topics")
        self.assertEqual(set(data["sections"]), set(kinds.table("section").values()))
        for topic, entry in data["topics"].items():
            for name in entry["kinds"]:
                with self.subTest(topic=topic, kind=name):
                    self.assertEqual(kinds.kind(name).section, entry["section"])

    def test_new_kinds_are_routed(self):
        topics = story_facts.table("topics")["topics"]
        routed = {kind: topic for topic, entry in topics.items() for kind in entry["kinds"]}
        self.assertEqual(routed["winds_aloft_day"], "winds-aloft")
        self.assertEqual(routed["foreign_military"], "notable-aircraft")
        self.assertEqual(routed["first_operator_here"], "first-sightings")
        self.assertEqual(routed["aerobatic_practice"], "flight-shapes")
        self.assertEqual(routed["unregistered_address"], "unlisted-addresses")
        self.assertEqual(routed["racetrack_orbit"], "holding-patterns")
        self.assertEqual(topics["winds-aloft"]["section"], "performance")
        self.assertEqual(topics["unlisted-addresses"]["section"], "data-quality")

    def test_the_theme_key_facts_map_names_registered_kinds_or_sections(self):
        named = theme_key_facts(JOURNAL.read_text())
        sections = set(story_facts.table("topics")["sections"])
        self.assertIn("flight-patterns", named)
        self.assertIn("above_rated_speed", named)
        self.assertLessEqual(named, STORY_KINDS | sections, named - STORY_KINDS - sections)


def theme_key_facts(text):
    """The keys of journal.mjs's `KEY_FACTS = new Map([...])`: each entry's first string, not its list's."""
    start = text.index("[", text.index("new Map(", text.index("const KEY_FACTS")))
    keys, depth, entry_open = set(), 0, False
    for match in re.finditer(r'"([^"\\]*)"|[\[\]]', text[start:]):
        token = match.group(0)
        if token == "[":
            depth += 1
            entry_open = depth == 2
        elif token == "]":
            depth -= 1
            if depth == 0:
                return keys
        elif depth == 2 and entry_open:
            keys.add(match.group(1))
            entry_open = False
    raise ValueError("KEY_FACTS map is not closed")


if __name__ == "__main__":
    unittest.main()
