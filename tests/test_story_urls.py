"""Story slugs: the dated scheme, disjointness from launch-day slugs and Ghost's slugify."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
import story_fixtures as fixtures

from wtf_adsb_publisher import public_events
from wtf_adsb_publisher import story_urls as urls

START = "2026-09-27T00:05:15Z"


def facts(**changes):
    """Minimal story facts for slug tests: kind, identity, type and participants."""
    values = {
        "kind": "watch_type",
        "registration": "HL7635",
        "callsign": "AAR224",
        "hex": "71BE35",
        "type_slug": "airbus-a380",
        "type_short": "a380",
        "partner_slug": None,
        "code": None,
        **changes,
    }
    items = []
    for key, identifier in (
        ("registration", "id.registration"),
        ("callsign", "id.callsign"),
        ("hex", "id.hex"),
    ):
        if values[key]:
            items.append({"id": identifier, "value": values[key]})
    if values["type_slug"]:
        items.append(
            {
                "id": "id.type",
                "value": "Type",
                "slug": values["type_slug"],
                "short_slug": values["type_short"],
            }
        )
    if values["partner_slug"]:
        items.append({"id": "p.1.type", "value": "Partner", "slug": values["partner_slug"]})
    if values["code"]:
        items.append({"id": "code.code", "value": values["code"]})
    return {"kind": values["kind"], "facts": items, "digest": "d" * 64}


class SlugSchemeTests(unittest.TestCase):
    def test_design_examples_and_new_family(self):
        self.assertEqual(urls.slug(facts(), START), "airbus-a380-hl7635-27-sep-2026")
        self.assertEqual(
            urls.slug(facts(registration="G-XLEB", hex="40688C"), "2026-06-04T04:00:00Z"),
            "airbus-a380-g-xleb-4-jun-2026",
        )
        self.assertEqual(
            urls.slug(facts(registration="87-0043", type_slug="c-5m-super-galaxy"), "2026-10-04T03:49:00Z"),
            "c-5m-super-galaxy-87-0043-4-oct-2026",
        )
        self.assertEqual(
            urls.slug(
                facts(kind="outlier_gs", registration="N6508K", type_slug="cessna-172", type_short="172"),
                "2026-09-18T16:13:15Z",
            ),
            "cessna-172-n6508k-ground-speed-spike-18-sep-2026",
        )
        self.assertEqual(
            urls.slug(
                facts(
                    kind="stratospheric_balloon", registration="N930TH", callsign="HBAL837", type_slug=None
                ),
                "2026-10-02T22:07:00Z",
            ),
            "hbal837-high-altitude-balloon-2-oct-2026",
        )
        pair = facts(
            kind="tcas_ra",
            type_slug="cessna-citation-cj4",
            type_short="citation-cj4",
            partner_slug="boeing-767-300",
        )
        self.assertEqual(
            urls.slug(pair, "2026-10-04T11:25:00Z"),
            "cessna-citation-cj4-boeing-767-300-tcas-advisory-4-oct-2026",
        )

    def test_generated_slugs_match_new_scheme_and_never_an_old_family(self):
        cases = [
            facts(),
            facts(registration="87-0043"),
            facts(registration="G-XLEB"),
            facts(registration=None, callsign=None, hex="A1B2C3"),
            facts(registration=None, callsign="N123AB", type_slug=None),
            facts(registration="163920", type_slug=None),
            facts(registration="N1", type_slug="x"),
            facts(registration="12", type_slug=None),
            facts(kind="special_use_code", code="1255"),
            facts(kind="formation_flight", partner_slug="boeing-c-17-globemaster"),
            facts(kind="some_future_kind"),
        ]
        days = ["2026-01-01T00:00:00Z", "2026-10-31T23:59:59Z", "2030-02-09T12:00:00Z", 1_790_000_000]
        for case in cases:
            for day in days:
                with self.subTest(case=case["facts"], day=day):
                    slug = urls.slug(case, day)
                    self.assertRegex(slug, urls.NEW_SLUG)
                    self.assertFalse(urls.is_old_slug(slug))
                    self.assertLessEqual(len(slug), urls.MAX_LENGTH)
                    self.assertTrue(slug[-3].isdigit() and slug[-5] == "-")
                    self.assertEqual(urls.ghost_post_slug(slug), slug)

    def test_old_slug_families_never_match_the_new_scheme(self):
        old = [
            "hl7635-watch-type-2026-09-27",
            "87-0043-watch-type-2026-10-04",
            "g-xleb-watch-type-2026-06-04",
            "a1b2c3-outlier-gs-2026-09-18",
            "n8704-vintage-2026-10-02",
            "observation-4053cdb19b3ec8b0c54234d0",
            "observation-000000000000000000000000",
        ]
        for slug in old:
            with self.subTest(slug=slug):
                self.assertTrue(urls.is_old_slug(slug))
                self.assertIsNone(urls.NEW_SLUG.match(slug))
        # Ghost pages, reserved slugs and numeric suffixes cannot collide either.
        for slug in sorted(urls.RESERVED) + ["about", "hl7635-watch-type-2026-09-27-2", "page", "2-oct-2026"]:
            with self.subTest(slug=slug):
                self.assertIsNone(urls.NEW_SLUG.match(slug))

    def test_collisions_add_the_start_time_then_a_hash_and_keep_the_ending(self):
        first = urls.slug(facts(kind="rapid_descent"), "2026-09-27T14:32:10Z")
        self.assertEqual(first, "airbus-a380-hl7635-rapid-descent-27-sep-2026")
        second = urls.slug(facts(kind="rapid_descent"), "2026-09-27T14:32:10Z", taken={first})
        self.assertEqual(second, "airbus-a380-hl7635-rapid-descent-1432z-27-sep-2026")
        third = urls.slug(facts(kind="rapid_descent"), "2026-09-27T14:32:10Z", taken={first, second})
        self.assertRegex(third, r"^airbus-a380-hl7635-rapid-descent-[0-9a-f]{4}-27-sep-2026$")
        for slug in (first, second, third):
            self.assertRegex(slug, urls.NEW_SLUG)
            self.assertFalse(urls.is_old_slug(slug))

    def test_subjectless_slug_is_kind_and_date(self):
        facts = {
            "kind": "winds_aloft_day",
            "facts": [{"id": "wind.day", "value": "2026-11-05", "basis": "telemetry"}],
            "digest": "abc",
        }
        self.assertEqual(urls.slug(facts, "2026-11-05T14:00:00Z"), "winds-aloft-5-nov-2026")
        self.assertEqual(
            urls.slug(facts, "2026-11-05T14:00:00Z", taken={"winds-aloft-5-nov-2026"}),
            "winds-aloft-2-5-nov-2026",
        )
        self.assertEqual(
            urls.slug(
                facts, "2026-11-05T14:00:00Z", taken={"winds-aloft-5-nov-2026", "winds-aloft-2-5-nov-2026"}
            ),
            "winds-aloft-3-5-nov-2026",
        )
        # The example aircraft a winds story names never reach its slug.
        built = fixtures.facts(fixtures.kind_event("winds_aloft_day"))
        self.assertTrue(urls.slug(built, START).startswith("winds-aloft-"))
        self.assertEqual(urls.slug(built, START), "winds-aloft-27-sep-2026")

    def test_same_type_pairs_name_the_type_once_and_unknown_kinds_stay_generic(self):
        pair = facts(
            kind="formation_flight",
            type_slug="boeing-c-17-globemaster",
            partner_slug="boeing-c-17-globemaster",
        )
        self.assertEqual(urls.slug(pair, START), "boeing-c-17-globemaster-hl7635-formation-27-sep-2026")
        self.assertEqual(
            urls.slug(facts(kind="some_future_kind"), START), "airbus-a380-hl7635-observation-27-sep-2026"
        )

    def test_long_subjects_drop_the_manufacturer_then_shorten_the_phenomenon(self):
        long_type = "very-long-manufacturer-name-with-an-extended-model-designation-x"
        case = facts(kind="outlier_baro_rate", type_slug=long_type, type_short="extended-model-x")
        slug = urls.slug(case, START)
        self.assertLessEqual(len(slug), urls.MAX_LENGTH)
        self.assertIn("hl7635", slug)
        self.assertTrue(slug.endswith("-27-sep-2026"))
        self.assertIn("extended-model-x", slug)

    def test_paths_round_trip_and_reject_other_shapes(self):
        slug = urls.slug(facts(), START)
        path = urls.article_path(slug)
        self.assertEqual(path, f"/stories/{slug}/")
        # public_events.article_slug is the one reader of a story path's slug.
        self.assertEqual(public_events.article_slug(path), slug)
        for bad in (
            "/hl7635-watch-type-2026-09-27/",
            "/stories/observation-4053cdb19b3ec8b0c54234d0/",
            "/x/",
            None,
        ):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                public_events.article_slug(bad)
        with self.assertRaises(ValueError):
            urls.article_path("hl7635-watch-type-2026-09-27")

    def test_slugs_are_deterministic_and_ignore_prose_policy_and_type_names(self):
        base = facts()
        self.assertEqual(urls.slug(base, START), urls.slug(base, START))
        renamed = facts()
        renamed["facts"][-1]["value"] = "A renamed display name"
        self.assertEqual(urls.slug(renamed, START), urls.slug(base, START))
        built = fixtures.facts(fixtures.watch_a380(), place=fixtures.PLACES["coleman"])
        from wtf_adsb_publisher import editorial

        before = urls.slug(built, START)
        story = editorial.render(built, "episode")
        self.assertEqual(urls.slug({**built, "story": story}, START), before)
        self.assertEqual(before, "airbus-a380-hl7635-27-sep-2026")

    def test_only_story_facts_give_a_slug(self):
        context = {
            "type": {"code": "A388", "name": "Airbus A380-800", "slug": "airbus-a380"},
            "registration_slug": "hl7635",
        }
        with self.assertRaisesRegex(ValueError, "story facts"):
            urls.slug({**context, "kind": "watch_type"}, START)
        self.assertEqual(urls.MONTHS[8], "sep")
        self.assertTrue({"weekly", "stories", "journal"} <= urls.RESERVED)
        self.assertNotIn("weekly-spotlights", urls.RESERVED)


class GhostSlugifyTests(unittest.TestCase):
    """A port of @tryghost/string 0.3.6 slugify, as the pinned Ghost image applies it."""

    def test_ghost_rules(self):
        self.assertEqual(urls.ghost_slugify("Hello World"), "hello-world")
        self.assertEqual(urls.ghost_slugify("a--b"), "a-b")
        self.assertEqual(urls.ghost_slugify("-lead-and-trail-"), "lead-and-trail")
        self.assertEqual(urls.ghost_slugify("v1.2"), "v1-2")
        self.assertEqual(urls.ghost_slugify("it's"), "its")
        self.assertEqual(urls.ghost_slugify("a\u2014b"), "a-b")
        self.assertEqual(urls.ghost_slugify("Caf\u00e9"), "cafe")
        self.assertEqual(urls.ghost_post_slug("rss"), "rss-post")

    def test_generated_slugs_are_fixed_points(self):
        for kind in ("watch_type", "outlier_gs", "tcas_ra", "stratospheric_balloon", "special_squawk_7700"):
            raw = fixtures.kind_event(kind)
            built = fixtures.facts(raw, place=fixtures.PLACES["coleman"])
            start = next(item["value"] for item in built["facts"] if item["id"] == "obs.start")
            slug = urls.slug(built, start)
            with self.subTest(kind=kind):
                self.assertEqual(urls.ghost_slugify(slug), slug)
                self.assertRegex(slug, re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$"))


if __name__ == "__main__":
    unittest.main()
