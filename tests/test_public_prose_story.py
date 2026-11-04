"""Story-level checks: per-field limits, dashes, attribution, scope and hedges; no paid calls."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
import reference_fixture
import story_fixtures as fixtures

from wtf_adsb_publisher import editorial, public_prose, story_facts, watchlists
from wtf_adsb_publisher.public_prose import facts_packet, prose_issues, story_issues, validate_story_fields

RATED = ("above_rated_altitude", "above_rated_speed")


def setUpModule():
    reference_fixture.install()


def a380():
    return fixtures.facts(
        fixtures.watch_a380(),
        place=fixtures.PLACES["coleman"],
        profiles=fixtures.A380_PROFILE,
    )


def good_story():
    return {
        "angle": "operator_snapshot",
        "headline": "Asiana A380 HL7635 passes at 39,000 ft near Coleman, Michigan",
        "seo_title": "Asiana A380 HL7635 near Coleman, MI",
        "dek": "Flying as AAR224, the double-deck Airbus reported 487 knots. Airbus built the A380 with two "
        "full-length passenger decks.",
        "paragraphs": [
            "HL7635 broadcast callsign AAR224 just after midnight UTC on 27 September 2026. The AAR prefix is "
            "assigned to Asiana Airlines, and the aircraft database lists the registration as an Airbus A380-800.",
            "Nine saved positions near Coleman, Michigan, cover about 18 miles of its path, and the archive recorded "
            "39,000 ft and a ground speed of 487 knots. "
            + story_facts.KIND_CONTEXT["watch_type"]
            + " The A380 "
            "first flew in April 2005 and entered airline service in 2007.",
        ],
    }


class FieldTests(unittest.TestCase):
    def setUp(self):
        self.facts = a380()

    def assertIssue(self, story, fragment):
        issues = story_issues(story, self.facts)
        self.assertTrue(any(fragment in issue for issue in issues), (fragment, issues))

    def test_a_good_story_passes_and_is_returned_unchanged(self):
        story = good_story()
        self.assertEqual(story_issues(story, self.facts), [])
        self.assertIs(validate_story_fields(story, self.facts), story)

    def test_field_lengths(self):
        cases = {
            "headline": ("A380 at 39,000 ft", "headline:length"),
            "dek": ("HL7635 reported 487 knots.", "dek:length"),
            "seo_title": (
                "Asiana A380 HL7635 near Coleman, MI, logged on 27 September 2026",
                "seo_title:length",
            ),
        }
        for field, (text, issue) in cases.items():
            with self.subTest(field=field):
                self.assertIssue({**good_story(), field: text}, issue)
        # A thin story may be one paragraph, never none and never more than five.
        self.assertNotIn(
            "paragraphs:count",
            story_issues({**good_story(), "paragraphs": good_story()["paragraphs"][:1]}, self.facts),
        )
        self.assertIssue({**good_story(), "paragraphs": []}, "paragraphs:count")
        self.assertIssue({**good_story(), "paragraphs": good_story()["paragraphs"] * 3}, "paragraphs:count")
        self.assertIssue(
            {**good_story(), "paragraphs": ["HL7635 reported 487 knots.", "Short."]}, "paragraph[0]:length"
        )
        long_paragraph = " ".join(["HL7635 reported 487 knots at 39,000 ft."] * 25)
        self.assertIssue(
            {**good_story(), "paragraphs": [long_paragraph, long_paragraph]}, "paragraph[0]:length"
        )

    def test_em_and_en_dashes_and_double_hyphens_are_rejected_in_every_field(self):
        for dash in ("\u2014", "\u2013", " -- "):
            for field in ("headline", "dek", "seo_title"):
                with self.subTest(dash=dash, field=field):
                    story = good_story()
                    story[field] = story[field].replace(" at ", dash, 1).replace(", ", dash, 1)
                    self.assertIssue(story, f"{field}:dash")
            story = good_story()
            story["paragraphs"] = [
                story["paragraphs"][0].replace(". The", dash + "the", 1),
                story["paragraphs"][1],
            ]
            self.assertIssue(story, "paragraph[0]:dash")
        hyphenated = good_story()
        hyphenated["dek"] = (
            "Flying as AAR224, the double-deck Airbus reported 487 knots at a well-known altitude."
        )
        self.assertNotIn("dek:dash", story_issues(hyphenated, self.facts))

    def test_headline_case_punctuation_and_questions(self):
        self.assertIssue(
            {**good_story(), "headline": good_story()["headline"] + "."}, "headline:final_punctuation"
        )
        self.assertIssue(
            {
                **good_story(),
                "headline": "Asiana A380 HL7635 Shows Up For A Fourth Time Near Coleman, Michigan",
            },
            "headline:title_case",
        )
        # A designation prefix is part of a type name, not a title-cased word.
        for headline, flagged in (
            ("Antonov An-124 Ruslan RA-82078 logged at 12:00 UTC on 5 July 2026", False),
            ("Antonov An-124 RA-82078 Logged An Hour Near Coleman, Michigan", True),
        ):
            with self.subTest(headline=headline):
                story = {**good_story(), "headline": headline}
                self.assertEqual("headline:title_case" in story_issues(story, self.facts), flagged)
        # Only a TCAS advisory's meaning answers a question headline; an explainer angle asks none.
        question = "Why did HL7635 show up for a fourth time near Coleman, Michigan?"
        self.assertIssue({**good_story(), "headline": question}, "headline:question")
        self.assertIssue({**good_story(), "headline": question, "angle": "explainer"}, "headline:question")
        self.assertIssue(
            {**good_story(), "headline": "Asiana A380 HL7635 shows up for a fourth time near Coleman!"},
            "exclamation",
        )

    def test_dek_is_one_or_two_sentences(self):
        self.assertIssue(
            {**good_story(), "dek": "Flying as AAR224 at 39,000 ft and 487 knots near Coleman, Michigan"},
            "dek:not_sentences",
        )
        three = "HL7635 flew as AAR224. It reported 487 knots. It was at 39,000 ft near Coleman, Michigan."
        self.assertIssue({**good_story(), "dek": three}, "dek:sentence_count")

    def test_internal_phrases_are_rejected(self):
        for phrase in (
            "type match",
            "qualifying reports",
            "analysis window",
            "saved reports",
            "not independently verified",
        ):
            with self.subTest(phrase=phrase):
                story = good_story()
                story["paragraphs"] = [
                    story["paragraphs"][0],
                    story["paragraphs"][1] + f" The {phrase} is noted.",
                ]
                self.assertIssue(story, "internal_phrase")


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.facts = a380()

    def assertRejects(self, text):
        story = good_story()
        story["paragraphs"] = [story["paragraphs"][0], story["paragraphs"][1] + " " + text]
        self.assertTrue(story_issues(story, self.facts), text)

    def test_public_seven_repros_are_rejected(self):
        for text in (
            "It descended at 25,000 ft/min.",
            "It was 14 nm from our receiver.",
            "The aircraft is owned by John Smith.",
            "A hijacking was under way.",
            "It reached Mach 1.4.",
        ):
            with self.subTest(text=text):
                self.assertRejects(text)

    def test_routes_types_identities_hypotheses_and_markup_are_rejected(self):
        for text in (
            "It was en route to Incheon.",
            "The A350 climbed away.",
            "HL7699 followed it.",
            "Jane Example was aboard.",
            "See https://example.com for more.",
            "It was **fast**.",
            "It was the largest airliner ever logged.",
            "It is the first A380 to cross the state.",
        ):
            with self.subTest(text=text):
                self.assertRejects(text)
        # A position jump still offers its hypothesis; the story must hedge it.
        jump = fixtures.facts(fixtures.kind_event("position_jump"))
        story = editorial.render(jump, "episode")
        story["paragraphs"] = [
            story["paragraphs"][0],
            story["paragraphs"][1] + " The jump was a decoding error.",
        ]
        self.assertIn("claim:unhedged_hypothesis", story_issues(story, jump))
        story["paragraphs"][1] = story["paragraphs"][1].replace(
            "The jump was a decoding error.", "The jump may be a decoding error."
        )
        self.assertEqual(story_issues(story, jump), [])
        # A glitch story lists no cause, so one stated as fact or as a possibility is refused.
        outlier = fixtures.facts(fixtures.outlier_cessna())
        story = editorial.render(outlier, "episode")
        for sentence in (
            "The spike was a decoding error.",
            "The spike may be a decoding error.",
            "The spike was a data error.",
            "The reading was caused by corrupted data.",
            "The value is due to a decoding fault.",
            "The spike was the result of a glitch.",
            "The report stems from interference.",
            "The figure can be explained by a faulty reading.",
        ):
            with self.subTest(sentence=sentence):
                issues = story_issues(
                    {
                        **story,
                        "paragraphs": [story["paragraphs"][0], story["paragraphs"][1] + " " + sentence],
                    },
                    outlier,
                )
                self.assertTrue(
                    any(issue.startswith("paragraph[1]:Unlisted cause") for issue in issues), issues
                )
        # A story with no cause to offer refuses a cause noun anywhere and a causal verb about the
        # reading, with or without a reading noun or a hedge.
        for sentence in (
            "The spike came from a decoding error.",
            "It was a decoding error.",
            "A decoding error produced the spike.",
            "Bad data caused the jump.",
            "The jump reflects corrupted data.",
            "Corrupted data explains the reading.",
            "Such spikes usually come from multipath reflections.",
            "That explains the jump.",
            "Something else produced this reading.",
        ):
            with self.subTest(outlier=sentence):
                issues = story_issues(
                    {
                        **story,
                        "paragraphs": [story["paragraphs"][0], story["paragraphs"][1] + " " + sentence],
                    },
                    outlier,
                )
                self.assertTrue(
                    any(issue.startswith("paragraph[1]:Unlisted cause") for issue in issues), issues
                )
        # Where a value came from is no cause, and a sentence about the type is not about the reading.
        for sentence in (
            "Its type comes from the aircraft database.",
            "The figure comes from a multilateration solution.",
        ):
            with self.subTest(provenance=sentence):
                self.assertEqual(public_prose.prose_checker(outlier)(sentence), [])
        # Mismatch stories and certified-limit stories offer no cause either; their required
        # sentence, "a faulty reading can't be ruled out", is the page's and still passes.
        with fixtures.rated_limits():
            limited = fixtures.facts(fixtures.kind_event("type_speed_mismatch"))
        causeless = [limited, *(fixtures.facts(fixtures.kind_event(kind)) for kind in RATED)]
        for facts in causeless:
            rendered = editorial.render(facts, "episode")
            self.assertEqual(story_issues(rendered, facts), [], facts["kind"])
            for sentence in ("A faulty altimeter caused the reading.", "The reading was a glitch."):
                last = sentence + " " + rendered["paragraphs"][-1]
                changed = {**rendered, "paragraphs": [*rendered["paragraphs"][:-1], last]}
                issues = story_issues(changed, facts)
                with self.subTest(kind=facts["kind"], sentence=sentence):
                    self.assertTrue([issue for issue in issues if "Unlisted cause" in issue], issues)
        # A mismatch story's one line is a statement, never a cause; other causes stay refused.
        mismatch = fixtures.facts(fixtures.kind_event("type_speed_mismatch"))
        check = public_prose.prose_checker(mismatch)
        self.assertEqual(check("The reading and the listed type do not agree."), [])
        self.assertTrue(check("The reading was corrupted data."))
        self.assertTrue(check("The reading may be due to wrong type metadata."))

    def test_allowed_roundings_conversions_and_negated_caveats(self):
        evidence = facts_packet(self.facts)
        for text in (
            "It reported 487 knots, about 560 mph.",
            "It was at 39,000 ft, about 11,900 m.",
            "Nine positions cover about 18 miles of its path.",
            "The record does not confirm an emergency.",
        ):
            with self.subTest(text=text):
                self.assertEqual(prose_issues(text, evidence), [])
        self.assertEqual(prose_issues("It reported 487 knots.", self.facts), [])

    def test_database_attribution_scope_and_watchlist_category(self):
        story = good_story()
        story["paragraphs"] = [
            "HL7635 broadcast callsign AAR224 just after midnight UTC on 27 September 2026, near Coleman, Michigan.",
            "Nine saved positions near Coleman, Michigan, cover about 18 miles of its path, and the archive recorded "
            "39,000 ft and a ground speed of 487 knots.",
        ]
        story["dek"] = (
            "Flying as AAR224, the double-deck Airbus reported 487 knots on 27 September near Coleman, Michigan."
        )
        self.assertIn("paragraphs:database_attribution", story_issues(story, self.facts))
        watch = fixtures.facts(fixtures.kind_event("watchlist_match"))
        rendered = editorial.render(watch, "episode")
        self.assertEqual(story_issues(rendered, watch), [])
        rendered["paragraphs"] = [
            *rendered["paragraphs"][:-1],
            rendered["paragraphs"][-1] + " It is a distinctive aircraft.",
        ]
        self.assertIn("claim:watchlist_category_unattributed", story_issues(rendered, watch))

    def test_the_seo_title_is_checked_for_claims_too(self):
        story = {**good_story(), "seo_title": "The largest A380 HL7635 near Coleman, MI"}
        self.assertIn("claim:unscoped_superlative:largest", story_issues(story, self.facts))

    def test_one_limitation_sentence_at_most(self):
        story = good_story()
        story["paragraphs"] = [
            story["paragraphs"][0] + " The record does not show who was aboard.",
            story["paragraphs"][1] + " The data cannot show why it flew.",
        ]
        self.assertIn("paragraphs:repeated_limitations", story_issues(story, self.facts))

    def test_a_restated_type_fact_is_never_the_storys_caveat(self):
        unknown = "Whether the A380 ever flew a passenger in a third deck is unknown."
        profiles = fixtures.profile(
            "A388",
            "Airbus built the A380 with two full-length passenger decks.",
            "The A380 first flew in April 2005 and entered airline service in 2007.",
            unknown,
            publisher="Airbus",
        )
        facts = fixtures.facts(fixtures.watch_a380(), place=fixtures.PLACES["coleman"], profiles=profiles)
        self.assertIn(unknown, [item["value"] for item in facts["facts"] if item["id"].startswith("tp.")])
        story = good_story()
        first, second = story["paragraphs"]
        # The story's own caveat and the restated type fact: one caveat, not two.
        story["paragraphs"] = [first + " The record does not show who was aboard.", second + " " + unknown]
        self.assertNotIn("paragraphs:repeated_limitations", story_issues(story, facts))
        # Nor does the restated fact close the story on a caveat.
        self.assertNotIn("paragraphs:closing_caveat", story_issues(story, facts))
        self.assertEqual(story_issues(story, facts), [])

    def test_packets_work_like_story_facts(self):
        packet = fixtures.packet(fixtures.watch_a380())
        self.assertEqual(public_prose.prose_issues("HL7635 reported 487 knots.", packet), [])
        self.assertTrue(public_prose.prose_issues("HL7635 reported 900 knots.", packet))
        with self.assertRaisesRegex(ValueError, "Story failed checks"):
            validate_story_fields({"headline": "x"}, packet)


class FinalReviewTests(unittest.TestCase):
    """The final reviews' AI-writer findings: claims, hedges, profiles, readings and location."""

    def setUp(self):
        self.facts = a380()
        self.check = public_prose.prose_checker(self.facts)

    def assertRefused(self, text, fragment=""):
        issues = self.check(text)
        self.assertTrue(issues and any(fragment in issue for issue in issues), (text, issues))

    def test_hedged_speculation_offers_only_listed_explanations(self):
        for text in (
            "It may have been on a surveillance mission.",
            "It could have been relaying orders to submarines.",
            "The two aircraft may have come close to colliding.",
            "Separation may have been lost for a moment.",
            "It may have been a spy balloon.",
            "Asiana A380 HL7635 may have been a charter over Coleman, Michigan",
            "Possibility: a training flight.",
            "It could have been vectored around weather.",
        ):
            with self.subTest(text=text):
                self.assertRefused(text)
        # A glitch story offers no cause: only a position jump still lists one.
        outlier = fixtures.facts(fixtures.outlier_cessna())
        check = public_prose.prose_checker(outlier)
        self.assertTrue(check("The spike may be a decoding or data error."))
        self.assertTrue(check("The spike may be a radar reflection."))
        jump = public_prose.prose_checker(fixtures.facts(fixtures.kind_event("position_jump")))
        self.assertEqual(jump("The jump may be a decoding or data error."), [])
        # A limitation that denies the near-miss framing stays allowed.
        self.assertEqual(
            self.check("The broadcast does not establish a near miss or a loss of separation."), []
        )

    def test_a_trailing_disclaimer_hedges_nothing_before_it(self):
        for text in (
            "It may have been carrying smuggled cargo, which is unknown.",
            "Perhaps it was carrying the president, which is unverified.",
            "The two aircraft nearly collided, which the broadcast does not establish.",
            "It was on a training flight, which the data cannot show.",
        ):
            with self.subTest(text=text):
                self.assertRefused(text)
        for text in (
            "The data cannot show whether it was a training flight.",
            "The data cannot show who was aboard.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.check(text), [])

    def test_the_month_may_is_no_hedge(self):
        squawk = fixtures.facts(
            fixtures.kind_event("special_squawk_7700", fixtures.stamp("2026-05-20T14:00:00Z"))
        )
        registration = story_facts.value(squawk, "id.registration")
        check = public_prose.prose_checker(squawk)
        self.assertTrue(check(f"On 20 May {registration} was in a general emergency."))
        self.assertTrue(check(f"In May {registration} was a rare visitor."))
        self.assertEqual(check(f"{registration} may have been in a general emergency."), [])
        self.assertEqual(check(f"{registration} transmitted code 7700 on 20 May."), [])
        # Nor does it make a sentence-case headline read as title case.
        for kind in ("gnss_integrity_loss", "seasonal_speed_record", "correlated_data_anomaly"):
            with self.subTest(kind=kind):
                facts = fixtures.facts(fixtures.kind_event(kind, fixtures.stamp("2026-05-20T14:00:00Z")))
                story, report = editorial.render_with_report(facts, "episode")
                self.assertTrue(report.publishable, report.issues)
                self.assertEqual(public_prose.story_issues(story, facts), [])

    def test_unsupported_world_claims_are_refused(self):
        for text, fragment in (
            ("Few A380s ever pass over Michigan.", "rarity"),
            ("Asiana has retired most of its A380 fleet.", "fleet status"),
            ("The jet is part of a military fleet.", "military"),
            ("There was no sign of an emergency.", "safety"),
            ("It left a long contrail.", "visibility"),
            ("That ground speed was 600 mph.", "Unsupported quantity"),
            ("Nine positions cover 20 miles of its path.", "Unsupported quantity"),
        ):
            with self.subTest(text=text):
                self.assertRefused(text, fragment)
        for text in (
            "That ground speed was about 600 mph.",
            "That ground speed was 560 mph.",
            "The data cannot show whether the flight was safe.",
            "These records rarely include the A380.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.check(text), [], text)

    def test_readings_keep_their_own_times_and_spans(self):
        for text, fragment in (
            ("It reported 487 knots at 00:22 UTC.", "time do not match"),
            ("It held 39,000 ft for all 17 minutes.", "span"),
            ("It reported nine positions per minute.", "rate"),
            ("It passed a quarter of an hour after midnight UTC.", "time"),
            ("It returned the following day.", "relative date"),
            ("It first appeared a month earlier.", "relative date"),
        ):
            with self.subTest(text=text):
                self.assertRefused(text, fragment)
        self.assertEqual(self.check("It reported 487 knots at 00:05 UTC."), [])

    def test_type_profile_figures_support_only_sentences_about_the_type(self):
        self.assertEqual(self.check("The A380 entered airline service in 2007."), [])
        self.assertEqual(
            self.check("The A380 first flew in April 2005 and entered airline service in 2007."), []
        )
        for text in (
            "HL7635 entered airline service in 2007.",
            "The airframe is from 2005.",
            "It first flew in 2005.",
        ):
            with self.subTest(text=text):
                self.assertRefused(text, "Unsupported")

    def test_restated_type_profile_text_keeps_its_claims_checked(self):
        packet = {
            "kind": "watch_type",
            "facts": [
                {"type_description": "Boeing E-6B Mercury"},
                {"profile_text": "The E-6B carries the Secretary of Defense during national emergencies."},
            ],
            "limitations": [],
            "hypotheses": [],
        }
        issues = public_prose.prose_issues(
            "The E-6B carries the Secretary of Defense during national emergencies.", packet
        )
        self.assertTrue(any("emergency" in issue for issue in issues), issues)

    def test_watchlist_stories_never_name_a_kind_of_person(self):
        raw = fixtures.kind_event("watchlist_match")
        raw["evidence"]["watchlist"]["neutral_category"] = "Public figure association"
        watch = fixtures.facts(raw)
        check = public_prose.prose_checker(watch)
        for text in (
            "Plane Alert DB ties this jet to a well-known person.",
            "The jet may be used by a public figure, according to Plane Alert DB.",
            "Plane Alert DB lists it among aircraft of celebrities.",
        ):
            with self.subTest(text=text):
                self.assertTrue(check(text), text)
        self.assertEqual(check("Plane Alert DB includes N51EM in its list."), [])
        # The list's own category is named as the list gives it, beside the list.
        self.assertEqual(check('Plane Alert DB files N51EM under "Public figure association".'), [])

    def test_here_is_never_a_location_and_a_story_never_counts_stories(self):
        for text in (
            "Its last report here came at 00:22 UTC.",
            "It shows up here for the first time.",
            "This story follows HL7635 as it passed near here at 39,000 ft.",
        ):
            with self.subTest(text=text):
                self.assertRefused(text, "location")
        story = good_story()
        story["paragraphs"] = [
            story["paragraphs"][0],
            story["paragraphs"][1] + " It is the fourth story here.",
        ]
        self.assertIn("claim:self_reference", story_issues(story, self.facts))

    def test_second_review_of_the_story_checks(self):
        # A sourced type fact restated as written may say "first".
        first = fixtures.profile("A388", "Airbus flew the first A380 in April 2005.", publisher="Airbus")
        facts = fixtures.facts(fixtures.watch_a380(), place=fixtures.PLACES["coleman"], profiles=first)
        for key in ("one", "two", "three"):
            story, report = editorial.render_with_report(facts, key)
            self.assertFalse(report.fallback, key)
        # Profile figures support sentences about the type, plurals included, never "this E-6B".
        e6 = next(
            built
            for built in map(fixtures.case_facts, fixtures.design_cases())
            if story_facts.value(built, "tp.E6.3")
        )
        check = public_prose.prose_checker(e6)
        self.assertEqual(check("Boeing delivered 16 E-6s to the Navy from 1989 to 1992."), [])
        self.assertTrue(check("Boeing delivered this E-6B to the Navy in 1989."))
        self.assertEqual(
            self.check("The A380 entered airline service in 2007, after its first flight in April 2005."), []
        )
        # A span needs a held reading to be a claim; a negated modal states a limit.
        self.assertEqual(self.check("The record covers the full 17 minutes between 00:05 and 00:22 UTC."), [])
        self.assertRefused("It held 39,000 ft for all 17 minutes.", "span")
        self.assertEqual(self.check("The value could not be confirmed by other reports."), [])
        self.assertIsNone(public_prose.moment("9999-12-31T23:59:59-05:00"))

    def test_separation_figures_are_measurements_and_watchlist_stories_add_no_kind_of_person(self):
        tcas = next(
            built for built in map(fixtures.case_facts, fixtures.design_cases()) if built["kind"] == "tcas_ra"
        )
        check = public_prose.prose_checker(tcas)
        self.assertEqual(check("The minimum separation between the two aircraft was 3.2 NM."), [])
        self.assertTrue(check("Separation may have been lost for a moment."))
        watch = next(
            built
            for built in map(fixtures.case_facts, fixtures.design_cases())
            if built["kind"] == "watchlist_match"
        )
        registration = story_facts.value(watch, "id.registration")
        check = public_prose.prose_checker(watch)
        for text in (
            f"Plane Alert DB links {registration} to a businessman.",
            f"The watchlist entry for {registration} names a tech founder.",
        ):
            with self.subTest(text=text):
                self.assertTrue(any("kind of person" in issue for issue in check(text)), text)
        # The entry itself is named, beside the list and never as an owner or occupant.
        self.assertEqual(
            check(f"Plane Alert DB associates {registration} with {fixtures.WATCH_ASSOCIATION}."), []
        )
        for text in (
            f"{registration} belongs to {fixtures.WATCH_ASSOCIATION}.",
            f"{fixtures.WATCH_ASSOCIATION} flew {registration} that day.",
        ):
            with self.subTest(text=text):
                self.assertTrue(
                    any("Unattributed watchlist association" in issue for issue in check(text)), text
                )

    def test_headline_capitals_from_the_facts_are_not_title_case(self):
        facts = fixtures.facts(
            fixtures.watch_a380(),
            place={"name": "Cape May, New Jersey", "short": "Cape May, NJ", "state": "New Jersey"},
        )
        story = editorial.render(facts, "episode")
        story["headline"] = "Asiana A380 HL7635 at 39,000 ft near Cape May, New Jersey"
        self.assertNotIn("headline:title_case", story_issues(story, facts))
        story["headline"] = "Asiana A380 HL7635 Near Cape May at 39,000 ft"
        self.assertIn("headline:title_case", story_issues(story, facts))


class StoryPolicyTests(unittest.TestCase):
    """A story is about the aircraft: no self-reference or day counts, scoped firsts, named entries."""

    def with_sentence(self, story, sentence):
        return {**story, "paragraphs": [story["paragraphs"][0], story["paragraphs"][1] + " " + sentence]}

    def test_stories_never_mention_this_site_or_count_days(self):
        facts = a380()
        self.assertEqual(story_issues(good_story(), facts), [])
        for sentence, issue in (
            ("The journal first wrote about this airframe on 16 June.", "claim:self_reference"),
            ("These stories had no A380 before 27 September.", "claim:self_reference"),
            ("These records went 91 days without an A380.", "claim:day_count"),
            ("None of the 91 earlier days in these records included an A380.", "claim:day_count"),
            ("HL7635 broke that run on 27 September.", "claim:day_count"),
            ("It ended a 91-day absence.", "claim:day_count"),
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(issue, story_issues(self.with_sentence(good_story(), sentence), facts))
        # A sourced type sentence restated as written may say "story".
        storied = fixtures.profile(
            "A388", "Airbus stacked the two passenger decks of the A380, one full story above the other."
        )
        facts = fixtures.facts(fixtures.watch_a380(), place=fixtures.PLACES["coleman"], profiles=storied)
        restated = self.with_sentence(
            good_story(),
            "Airbus stacked the two passenger decks of the A380, one full story above the other.",
        )
        self.assertNotIn("claim:self_reference", story_issues(restated, facts))

    def test_a_first_sighting_headline_needs_a_sentence_scoped_to_these_records(self):
        facts = fixtures.facts(fixtures.first_embraer())
        story = {
            "angle": "first_in_records",
            "headline": "Embraer C-390 Millennium spotted for the first time",
            "dek": "The aircraft database lists 26903 as an Embraer C-390 Millennium. It reported 23,200 ft and "
            "406 knots on 8 September.",
            "paragraphs": [
                "26903 flew on 8 September 2026, and the aircraft database lists it as an Embraer C-390 "
                "Millennium. Its transponder address, 497CDE, sits in the block ICAO allocates to Portugal.",
                "At 15:10 UTC, it reported a pressure altitude of 23,200 ft and 406 knots over the ground. It is "
                "the first of its type in these records. " + story_facts.KIND_CONTEXT["first_type_here"],
            ],
        }
        self.assertEqual(story_issues(story, facts), [])
        unscoped = {
            **story,
            "paragraphs": [
                story["paragraphs"][0],
                story["paragraphs"][1].replace(
                    "It is the first of its type in these records.", "The record ends at 15:20 UTC."
                ),
            ],
        }
        self.assertIn("claim:unscoped_first", story_issues(unscoped, facts))
        # Another kind's headline may not claim a first, even beside a sentence about these records.
        claimed = self.with_sentence(
            {**good_story(), "headline": "Asiana A380 HL7635 spotted for the first time"},
            "These records hold nine positions for it.",
        )
        self.assertIn("claim:unscoped_first", story_issues(claimed, a380()))

    def test_a_watchlist_story_names_the_entry_and_why_it_is_listed(self):
        # The entry's description usually repeats the type name, so it is no reason by itself.
        facts = fixtures.facts(fixtures.kind_event("watchlist_match"), place=fixtures.PLACES["coleman"])
        self.assertEqual(story_facts.value(facts, "wl.description"), "Citation CJ4")
        story = editorial.render(facts, "episode")
        self.assertEqual(story_issues(story, facts), [])
        text = " ".join([story["headline"], story["dek"], *story["paragraphs"]])
        self.assertIn(fixtures.WATCH_ASSOCIATION, text)
        self.assertRegex(text, r"does not show who was aboard|not evidence of .* anyone aboard")
        registration = story_facts.value(facts, "id.registration")
        plain = {
            "angle": "snapshot",
            "headline": f"Cessna Citation CJ4 {registration} reports 509 knots near Coleman, Michigan",
            "dek": "The aircraft database lists it as a Cessna Citation CJ4. It reported a geometric altitude of "
            "46,050 ft.",
            "paragraphs": [
                f"{registration} reported a ground speed of 509 knots at a geometric altitude of 46,050 ft near "
                "Coleman, Michigan. The aircraft database lists the registration as a Cessna Citation CJ4.",
                "Its track holds 30 positions over about 7 minutes. Geometric altitude comes from satellite "
                "positioning and can differ from the pressure altitude pilots fly by. "
                + story_facts.KIND_CONTEXT["watchlist_match"],
            ],
        }
        issues = story_issues(plain, facts)
        self.assertIn("claim:watchlist_entry_unnamed", issues)
        self.assertIn("claim:watchlist_reason_missing", issues)
        named = self.with_sentence(
            plain,
            f"Plane Alert DB, a community list, associates {registration} with {fixtures.WATCH_ASSOCIATION}.",
        )
        issues = story_issues(named, facts)
        self.assertNotIn("claim:watchlist_entry_unnamed", issues)
        self.assertIn("claim:watchlist_reason_missing", issues)
        reasoned = self.with_sentence(named, 'Plane Alert DB files it under "Distinctive".')
        # The headline still leads with the aircraft and its entry.
        self.assertEqual(story_issues(reasoned, facts), ["headline:watchlist_form"])
        # An entry that names someone is headlined by that name, never by its category.
        reasoned["headline"] = f'Cessna Citation CJ4 {registration}, filed under "Distinctive", spotted'
        self.assertEqual(story_issues(reasoned, facts), ["headline:watchlist_form"])
        reasoned["headline"] = (
            f"Cessna Citation CJ4 {registration} linked to {fixtures.WATCH_ASSOCIATION} by Plane Alert DB"
        )
        self.assertEqual(story_issues(reasoned, facts), [])
        # The older listed form stays accepted for stories already published.
        reasoned["headline"] = (
            f"Cessna Citation CJ4 {registration}, listed under {fixtures.WATCH_ASSOCIATION}, spotted"
        )
        self.assertEqual(story_issues(reasoned, facts), [])
        # The list's own category name and a name-like tag are claims of the list.
        for sentence in ('It is a "Distinctive" jet.', 'It is a "Business Jet" in the air.'):
            with self.subTest(sentence=sentence):
                self.assertIn(
                    "claim:watchlist_detail_unattributed",
                    story_issues(self.with_sentence(reasoned, sentence), facts),
                )
        self.assertEqual(
            story_issues(self.with_sentence(reasoned, 'Plane Alert DB tags it "Business Jet".'), facts), []
        )


class PlaneAlertWordingTests(unittest.TestCase):
    """The list's own wording is quoted beside Plane Alert DB and never read as the site's claim."""

    def facts(self, **entry):
        raw = fixtures.kind_event("watchlist_match")
        raw["evidence"]["watchlist"].update(entry)
        return fixtures.facts(raw, place=fixtures.PLACES["coleman"])

    def test_list_categories_and_tags_are_quoted_only_beside_the_list(self):
        facts = self.facts(
            association="Jordan Example",
            source_category="Don't you know who I am?",
            neutral_category="Public figure association",
            tags=["Toys4Billionaires", "Jordan Example"],
        )
        check = public_prose.prose_checker(facts)
        for text in (
            'Plane Alert DB files N51EM under "Don\'t you know who I am?".',
            'Plane Alert DB tags N51EM "Toys4Billionaires".',
            "Plane Alert DB associates N51EM with Jordan Example.",
        ):
            with self.subTest(text=text):
                self.assertEqual(check(text), [])
        story = editorial.render(facts, "episode")
        self.assertEqual(story_issues(story, facts), [])
        for sentence in ('It is a "Don\'t you know who I am?" jet.', 'The jet is "Toys4Billionaires".'):
            with self.subTest(sentence=sentence):
                changed = {
                    **story,
                    "paragraphs": [story["paragraphs"][0] + " " + sentence, *story["paragraphs"][1:]],
                }
                self.assertIn("claim:watchlist_detail_unattributed", story_issues(changed, facts))
        # A question mark inside the quoted category neither ends a headline nor counts as a
        # question; the only issue is that this entry names someone, so its name leads instead.
        headline = 'Citation CJ4 N51EM, filed under "Don\'t you know who I am?", spotted near Coleman'
        self.assertEqual(story_issues({**story, "headline": headline}, facts), ["headline:watchlist_form"])
        headline = "Citation CJ4 N51EM, listed under Jordan Example, spotted near Coleman"
        self.assertEqual(story_issues({**story, "headline": headline}, facts), [])

    def test_category_notes_explain_the_entry_only_beside_the_list(self):
        facts = self.facts(
            source_category="Oxcart", neutral_category="Specialized military aircraft", tags=[]
        )
        self.assertEqual(story_facts.value(facts, "wl.category_note"), watchlists.CATEGORY_NOTES["Oxcart"])
        self.assertEqual(story_facts.fact_map(facts)["wl.category_note"]["basis"], "reference")
        check = public_prose.prose_checker(facts)
        note = watchlists.CATEGORY_NOTES["Oxcart"]
        for text in (
            f'Plane Alert DB files N51EM under "Oxcart", its category for {note}.',
            f'In Plane Alert DB, N51EM sits under "Oxcart", its category for {note}.',
        ):
            with self.subTest(text=text):
                self.assertEqual(check(text), [])
        self.assertTrue(check(f"N51EM is one of the {note}."))

    def test_vague_associations_are_not_named(self):
        for vague in ("Private", "Unknown", " private owner "):
            with self.subTest(association=vague):
                facts = self.facts(association=vague)
                self.assertIsNone(story_facts.value(facts, "wl.association"))
                self.assertEqual(story_facts.value(facts, "wl.list_category"), "Distinctive")
        named = self.facts(association="US Marine Corps", group="Mil")
        self.assertEqual(story_facts.value(named, "wl.association"), "US Marine Corps")
        self.assertEqual(
            public_prose.prose_checker(named)("Plane Alert DB lists N51EM under US Marine Corps."), []
        )


class HeadlineAndHedgeTests(unittest.TestCase):
    """Scope stays in the paragraphs, watchlist headlines lead with the aircraft, glitch stories
    give no cause, speed records carry one hedge, and every list takes the serial comma."""

    def with_sentence(self, story, sentence):
        return {**story, "paragraphs": [story["paragraphs"][0], story["paragraphs"][1] + " " + sentence]}

    def test_scope_phrases_stay_out_of_the_headline_and_dek(self):
        facts = fixtures.facts(fixtures.first_embraer())
        story = editorial.render(facts, "episode")
        self.assertEqual(story_issues(story, facts), [])
        self.assertEqual(story["headline"], "Embraer C-390 Millennium spotted for the first time")
        self.assertNotRegex(story["dek"], public_prose.STORY_SCOPE)
        self.assertRegex(" ".join(story["paragraphs"]), public_prose.STORY_SCOPE)
        scoped = {**story, "headline": "Embraer C-390 Millennium is the first in these records"}
        self.assertIn("headline:scope phrase: these records", story_issues(scoped, facts))
        scoped = {**story, "dek": story["dek"] + " It is the first of its type in this collection."}
        self.assertIn("dek:scope phrase: this collection", story_issues(scoped, facts))
        # The site's other names for its records are scope phrases too.
        for field, text, phrase in (
            ("headline", "Agusta AW-119 Koala debuts in these flight records", "these flight records"),
            ("dek", story["dek"] + " It is new on the site.", "on the site"),
            ("dek", story["dek"] + " It is a first for this journal.", "for this journal"),
            ("dek", story["dek"] + " This site had never logged one.", "This site"),
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(f"{field}:scope phrase: {phrase}", story_issues({**story, field: text}, facts))
        # Title tags, fixed at first publication, no longer carry one for new stories.
        for kind in ("seasonal_speed_record", "first_type_here", "first_country_here"):
            for template in editorial.seo_candidates(kind):
                self.assertNotRegex(template, public_prose.HEADING_SCOPE, kind)
        # The paragraphs still scope the first; without that sentence the headline's first is bare.
        unscoped = {
            **story,
            "paragraphs": [
                public_prose.STORY_SCOPE.sub("elsewhere", text).replace("first", "latest")
                for text in story["paragraphs"]
            ],
        }
        self.assertIn("claim:unscoped_first", story_issues(unscoped, facts))

    def test_a_watchlist_headline_leads_with_the_aircraft_and_the_paragraphs_name_the_list(self):
        facts = fixtures.facts(fixtures.kind_event("watchlist_match"), place=fixtures.PLACES["camden"])
        story = editorial.render(facts, "episode")
        self.assertEqual(story_issues(story, facts), [])
        self.assertRegex(story["headline"], r" linked to .+ by Plane Alert DB$")
        self.assertIn("Plane Alert DB", " ".join([story["dek"], *story["paragraphs"]]))
        registration = story_facts.value(facts, "id.registration")
        headline = f"Cessna Citation CJ4 {registration}, listed under {fixtures.WATCH_ASSOCIATION}, spotted near Camden"
        self.assertEqual(story_issues({**story, "headline": headline}, facts), [])
        # The headline's listed form needs the dek or a paragraph to name Plane Alert DB.
        unnamed = {
            **story,
            "headline": headline,
            "dek": "It reported 509 knots at a geometric altitude of 46,050 ft. Its track ran near Camden, Michigan.",
            "paragraphs": [
                f"The aircraft database lists {registration} as a Cessna Citation CJ4. The entry names "
                f'{fixtures.WATCH_ASSOCIATION} as its association and files it under "Distinctive", with the '
                'tags "Business Jet" and "Corporate".',
                story_facts.KIND_CONTEXT["watchlist_match"] + " It reported a ground speed of 509 knots at a "
                "geometric altitude of 46,050 ft near Camden, Michigan. Its track holds 30 positions over about 7 "
                "minutes.",
            ],
        }
        # The context line names Plane Alert DB in general, never as this entry's source.
        self.assertIn("claim:watchlist_source_missing", story_issues(unnamed, facts))
        named = {
            **unnamed,
            "dek": 'Plane Alert DB files it under "Distinctive". It reported 509 knots at 46,050 ft.',
        }
        self.assertEqual(story_issues(named, facts), [])

    def test_fixed_and_watchlist_headlines_are_enforced(self):
        facts = fixtures.facts(fixtures.first_embraer())
        story = editorial.render(facts, "episode")
        self.assertEqual(public_prose.fixed_headline(facts)[0], story["headline"])
        # A first sighting's headline is the fixed one, word for word; no free-text form passes.
        for headline in (
            "Agusta AW-119 Koala debuts in these flight records",
            "Embraer C-390 Millennium makes its first recorded appearance",
            "Look! " + "An Embraer C-390 Millennium flew over the field today " * 4,
        ):
            with self.subTest(headline=headline[:40]):
                self.assertIn("headline:not_fixed", story_issues({**story, "headline": headline}, facts))
        # A type the database names only by its code still gets the fixed form: its class phrase
        # and code, or the bare code when the designator table has no class for it.
        for code, headline in (
            ("SLK5", "Single-engine piston airplane SLK5 spotted for the first time"),
            ("SLH9", "SLH9 spotted for the first time"),
        ):
            with self.subTest(code=code):
                coded = fixtures.first_embraer()
                coded["evidence"]["aircraft"].update(t=code, desc=None)
                coded = fixtures.facts(coded)
                self.assertIsNone(story_facts.value(coded, "id.type"))
                rendered = editorial.render(coded, "episode")
                self.assertEqual(rendered["headline"], headline)
                self.assertEqual(story_issues(rendered, coded), [])
        self.assertIn(
            "headline:not_fixed",
            story_issues({**rendered, "headline": "An SLH9 aircraft adds a new type to the list"}, coded),
        )
        # A watchlist headline leads with the aircraft and the name its entry carries.
        watch = fixtures.facts(fixtures.kind_event("watchlist_match"), place=fixtures.PLACES["camden"])
        story = editorial.render(watch, "episode")
        self.assertEqual(story_issues(story, watch), [])
        registration = story_facts.value(watch, "id.registration")
        for headline in (
            f"Cessna Citation CJ4 {registration} has an association in Plane Alert DB",
            f"Plane Alert DB's pick: Citation CJ4 {registration} near Camden",
            # The form, but naming neither the entry nor the aircraft.
            "Cessna Citation CJ4, listed under a corporate owner, spotted near Camden",
        ):
            with self.subTest(headline=headline):
                self.assertIn("headline:watchlist_form", story_issues({**story, "headline": headline}, watch))
        # Every non-fixed headline keeps the length rule.
        tiny = f'{registration}, tagged "Corporate", spotted'
        self.assertIn("headline:length", story_issues({**story, "headline": tiny}, watch))

    def test_a_speed_record_carries_one_short_hedge_in_a_paragraph(self):
        facts = fixtures.facts(fixtures.kind_event("seasonal_speed_record"))
        story = editorial.render(facts, "episode")
        self.assertEqual(story_issues(story, facts), [])
        hedge = public_prose.REQUIRED_SENTENCES["seasonal_speed_record"]
        self.assertEqual(public_prose.required_sentence(facts), hedge)
        text = " ".join(story["paragraphs"])
        self.assertEqual(text.count(hedge), 1, text)
        # A paraphrase of the hedge beside it is a second hedge, as an exact copy is.
        for extra in (
            "Ground speed includes wind, so this is not airspeed. A faulty report is possible.",
            "A tailwind may explain part of it.",
            "The reading could be an error.",
        ):
            with self.subTest(extra=extra):
                self.assertIn(
                    "paragraphs:repeated_limitations", story_issues(self.with_sentence(story, extra), facts)
                )
        for field in ("headline", "dek"):
            self.assertNotRegex(story[field], public_prose.STORY_SCOPE, field)
            self.assertNotIn("tailwind", story[field])
        self.assertRegex(text, public_prose.STORY_SCOPE)
        bare = {
            **story,
            "paragraphs": [paragraph.replace(hedge, "").strip() for paragraph in story["paragraphs"]],
        }
        self.assertIn("claim:record_unhedged", story_issues(bare, facts))
        # The hedge is checked word for word: a paraphrase is no hedge, and its wind is a claim.
        paraphrase = story_issues(
            self.with_sentence(
                bare, "A tailwind adds to ground speed, and an erroneous report cannot be ruled out."
            ),
            facts,
        )
        self.assertIn("claim:record_unhedged", paraphrase)
        self.assertTrue([issue for issue in paraphrase if "Unhedged capability claim" in issue], paraphrase)
        spaced = {
            **story,
            "paragraphs": [
                " ".join(paragraph.split()).replace(", and", ",\n and") for paragraph in story["paragraphs"]
            ],
        }
        self.assertNotIn("claim:record_unhedged", story_issues(spaced, facts))
        self.assertIn(
            "paragraphs:repeated_limitations", story_issues(self.with_sentence(story, hedge), facts)
        )
        headline = story_issues({**story, "headline": "A tailwind carries A380 HL7634 to 651 knots"}, facts)
        self.assertTrue(
            [issue for issue in headline if issue.startswith("headline:Unhedged capability")], headline
        )
        # The hedge is the story's one caveat: a real limit beside it is a second.
        limited = self.with_sentence(story, "The data cannot show why it went that fast.")
        self.assertIn("paragraphs:repeated_limitations", story_issues(limited, facts))

    def test_glitch_stories_give_the_reading_and_the_type_and_never_a_cause(self):
        banned = r"decoding|corrupt|metadata|site's|threshold|flag"
        for kind in ("outlier_gs", "outlier_alt_baro", "type_speed_mismatch", "type_altitude_mismatch"):
            facts = fixtures.facts(fixtures.kind_event(kind))
            self.assertNotIn("a decoding or data error", facts["hypotheses"])
            for index in range(4):
                story = editorial.render(facts, f"episode-{index}")
                text = " ".join([story["headline"], story["dek"], *story["paragraphs"]])
                with self.subTest(kind=kind, index=index):
                    self.assertEqual(story_issues(story, facts), [])
                    self.assertNotRegex(text, banned)
        with fixtures.rated_limits():
            speed = fixtures.facts(fixtures.kind_event("type_speed_mismatch"))
            altitude = fixtures.facts(fixtures.kind_event("type_altitude_mismatch"))
        self.assertEqual(story_facts.value(speed, "lim.speed"), 163)
        self.assertEqual(story_facts.value(altitude, "lim.altitude"), 14000)
        self.assertIn(
            "never-exceed airspeed of 163 knots", " ".join(editorial.render(speed, "e")["paragraphs"])
        )
        # A certified speed is an airspeed: it never sits beside a ground speed or a comparison.
        check = public_prose.prose_checker(speed)
        self.assertEqual(check("The Cessna 172 has a never-exceed airspeed of 163 knots."), [])
        for sentence in (
            "N509TX reported 1,063.9 knots, well past the Cessna 172's never-exceed speed.",
            "The Cessna 172 has a never-exceed airspeed of 163 knots, and one report read 1,063.9 knots.",
            "That ground speed is far above its Vne.",
            "Its 163-knot never-exceed speed is far below the reading.",
            "It went faster than the type's speed limit.",
        ):
            with self.subTest(sentence=sentence):
                self.assertTrue(
                    [issue for issue in check(sentence) if issue.startswith("claim:limit_vs_ground_speed")],
                    check(sentence),
                )
        # A ground-speed record states no certified speed at all, so none is supported there.
        with fixtures.rated_limits():
            record = fixtures.facts(fixtures.kind_event("seasonal_speed_record"))
        check = public_prose.prose_checker(record)
        for sentence in (
            "HL7634 reported 651 knots, well past the A380's maximum operating speed.",
            "The A380 has a maximum operating speed of 340 knots.",
        ):
            with self.subTest(record=sentence):
                self.assertTrue(check(sentence))
        rendered = editorial.render(altitude, "episode")
        self.assertIn("certified to 14,000 ft", " ".join([rendered["dek"], *rendered["paragraphs"]]))
        self.assertEqual(story_issues(rendered, altitude), [])
        # The site's checks never show in a story; the database's military flag may.
        story = good_story()
        for sentence, issue in (
            ("It flew past the site's 32,000-foot limit.", "claim:self_reference"),
            ("The detection threshold is 32,000 ft.", "claim:site_action"),
            ("The reading was flagged.", "claim:site_action"),
            ("This site's checks flag it.", "claim:self_reference"),
            ("The detector caught it.", "claim:site_action"),
            ("It passed the cutoff.", "claim:site_action"),
            ("It tripped an alert.", "claim:site_action"),
            ("WTF ADS-B logged it.", "claim:site_action"),
        ):
            with self.subTest(sentence=sentence):
                self.assertIn(issue, story_issues(self.with_sentence(story, sentence), a380()))
        # A flag carrier is an airline and a national flag a country's: neither is a check.
        for sentence in ("Asiana is a South Korean flag carrier.", "It wears no national flag."):
            with self.subTest(sentence=sentence):
                self.assertNotIn(
                    "claim:site_action", story_issues(self.with_sentence(story, sentence), a380())
                )
        military = fixtures.facts(fixtures.kind_event("military"))
        rendered = editorial.render(military, "episode")
        self.assertNotIn("claim:site_action", story_issues(rendered, military))
        flagged = self.with_sentence(rendered, "The aircraft database flags it as military.")
        self.assertNotIn("claim:site_action", story_issues(flagged, military))

    def test_a_missing_serial_comma_is_a_story_issue(self):
        facts = a380()
        story = self.with_sentence(good_story(), "It climbed, turned and left.")
        self.assertIn("Missing serial comma: It climbed, turned and left", story_issues(story, facts))
        self.assertEqual(
            story_issues(self.with_sentence(good_story(), "It climbed, turned, and left."), facts), []
        )
        self.assertEqual(
            story_issues(
                self.with_sentence(good_story(), "The jet, listed in the database, flew north and climbed."),
                facts,
            ),
            [],
        )
        headline = {**good_story(), "headline": "Asiana A380 HL7635 climbs, turns and leaves near Coleman"}
        self.assertTrue(
            any(
                issue.startswith("Missing serial comma: ") and "turns and leaves" in issue
                for issue in story_issues(headline, facts)
            )
        )


class DesignExampleTests(unittest.TestCase):
    """The ten section-5 "after" examples pass with the facts the facts layer builds for them."""

    def test_design_examples_pass(self):
        for case in fixtures.design_cases():
            facts = fixtures.case_facts(case)
            after = case["after"]
            with self.subTest(case=case["name"]):
                for field in ("headline", "dek", "paragraph"):
                    self.assertEqual(prose_issues(after[field], facts), [], (field, after[field]))
                # A new story carries its kind's context line, here opening the second paragraph.
                context = public_prose.context_line(facts)
                story = {
                    "angle": "explainer",
                    "headline": after["headline"],
                    "dek": after["dek"],
                    "paragraphs": [after["paragraph"], " ".join(filter(None, [context, after["paragraph"]]))],
                }
                shape = [issue for issue in story_issues(story, facts) if not issue.startswith("paragraph")]
                self.assertEqual(shape, [])

    def test_engine_output_for_design_cases_passes(self):
        for case in fixtures.design_cases():
            facts = fixtures.case_facts(case)
            story = editorial.render(facts, case["name"])
            with self.subTest(case=case["name"]):
                self.assertEqual(story_issues(story, facts), [])


class ProfileTexts(unittest.TestCase):
    def test_the_builder_and_both_checkers_read_the_same_profile_text(self):
        facts = fixtures.facts(fixtures.watch_a380(), profiles=fixtures.A380_PROFILE)
        texts = public_prose.profile_texts(facts)
        self.assertTrue(texts)
        self.assertEqual(public_prose.packet_facts(facts_packet(facts)).profiles, texts)
        builder = editorial._Builder(facts, editorial.build_slots(facts), editorial.Corpus([]))
        self.assertEqual(builder.profiles, texts)


class RegistryClaims(unittest.TestCase):
    """A mismatch story's second-registry type is that registry's, stated with it and never as a
    verdict on either source."""

    def setUp(self):
        raw = fixtures.with_registry(fixtures.kind_event("type_speed_mismatch"), "disagrees")
        code = raw["evidence"]["aircraft"]["t"]
        profiles = fixtures.profile(code, "The Cessna 172 first flew on 12 June 1955.")
        self.facts = fixtures.facts(raw, place=fixtures.PLACES["coleman"], profiles=profiles)
        self.reg = story_facts.value(self.facts, "id.registration")
        self.story = editorial.render(self.facts, "registry-claims")
        self.assertEqual(story_issues(self.story, self.facts), [])

    def issues(self, sentence):
        paragraphs = [*self.story["paragraphs"][:-1], self.story["paragraphs"][-1] + " " + sentence]
        return story_issues({**self.story, "paragraphs": paragraphs}, self.facts)

    def test_the_template_sentences_pass(self):
        for sentence in (
            "Another registry, adsbdb, lists the same address as a Boeing 757-200.",
            "A second registry, adsbdb, lists this address as a Boeing 757-200.",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(self.issues(sentence), [])

    def test_the_registry_type_borrows_no_profile_figures(self):
        issues = self.issues("The Boeing 757-200 first flew on 12 June 1955.")
        self.assertIn("paragraph[1]:Unsupported date: 12 June 1955", issues)

    def test_the_registry_type_is_attributed_to_the_registry(self):
        for sentence in (
            f"{self.reg} is a Boeing 757-200.",
            f"The aircraft database lists {self.reg} as a Boeing 757-200.",
            "The aircraft database and adsbdb both list it as a Boeing 757-200.",
            "adsbdb also lists it as a Boeing 757-200.",
            # Attribution is read per clause, and agreement anywhere in the sentence.
            "adsbdb lists the address, while the aircraft database lists it as a Boeing 757-200.",
            "adsbdb lists it as a Boeing 757-200; the aircraft database agrees.",
            # A later clause that refers back to the type gives it to the database too.
            "adsbdb lists the address as a Boeing 757-200, but the aircraft database lists the same type.",
            "adsbdb lists the address as a Boeing 757-200; the aircraft database lists it that way too.",
            "adsbdb lists the address as a Boeing 757-200: so does the aircraft database.",
            "adsbdb lists the address as a Boeing 757-200, but the aircraft database lists it too.",
            "adsbdb lists the address as a Boeing 757-200; the aircraft database lists the same.",
            # So does "too" or "as well" set off by commas mid-clause.
            "adsbdb lists the address as a Boeing 757-200; the aircraft database, too, lists it.",
            "adsbdb lists the address as a Boeing 757-200; the aircraft database, as well, lists it.",
            # A citation in an earlier clause never excuses crediting the database in a later one.
            "adsbdb has an answer for the address: the aircraft database lists a Boeing 757-200.",
        ):
            with self.subTest(sentence=sentence):
                self.assertIn("claim:registry_attribution", self.issues(sentence))

    def test_a_contrast_or_a_negated_agreement_attributes_each_clause(self):
        for sentence in (
            "adsbdb lists the address as a Boeing 757-200, while the aircraft database lists a Cessna 172.",
            "adsbdb lists the address as a Boeing 757-200, but the aircraft database lists a Cessna 172.",
            "adsbdb lists the address as a Boeing 757-200, whereas the aircraft database lists a Cessna 172.",
            "The aircraft database lists a Cessna 172; adsbdb lists a Boeing 757-200.",
            "The two sources do not agree: adsbdb lists a Boeing 757-200.",
            "The aircraft database and adsbdb do not agree: adsbdb lists a Boeing 757-200.",
            "adsbdb does not agree, and lists the address as a Boeing 757-200.",
            "adsbdb disagrees and lists a Boeing 757-200.",
            "The two sources differ: adsbdb lists a Boeing 757-200.",
            # A clock time's colon is no clause break.
            "adsbdb, as read at 12:00 UTC, lists the address as a Boeing 757-200.",
            # A negated agreement, however it is spaced or worded.
            "The two sources do  not agree: adsbdb lists a Boeing 757-200.",
            "The two sources never agree: adsbdb lists a Boeing 757-200.",
            "The two sources don't agree: adsbdb lists a Boeing 757-200.",
            # "The same" before another noun, and "as well" or "too" in an unrelated clause, point
            # back at nothing.
            "adsbdb lists a Boeing 757-200, while the aircraft database lists a Cessna 172 for the same address.",
            "adsbdb lists the address as a Boeing 757-200; it was seen at 12:00 UTC as well.",
            "adsbdb lists the address as a Boeing 757-200; it was built in 1978 as well.",
        ):
            with self.subTest(sentence=sentence):
                self.assertNotIn("claim:registry_attribution", self.issues(sentence))

    def test_a_citation_in_an_earlier_clause_attributes_the_type(self):
        for sentence in (
            "adsbdb has another answer for the address: a Boeing 757-200.",
            "A second registry, adsbdb, gives a different type: a Boeing 757-200.",
            "adsbdb lists one type for the address, but it is a Boeing 757-200.",
            "adsbdb lists the address as a Boeing 757-200, but the transponder reported speeds that fit a jet too.",
        ):
            with self.subTest(sentence=sentence):
                self.assertNotIn("claim:registry_attribution", self.issues(sentence))

    def test_neither_source_is_judged(self):
        for sentence, word in (
            (
                "Another registry, adsbdb, lists the same address as a Boeing 757-200, so the database is wrong.",
                "wrong",
            ),
            (f"The database is mistaken about {self.reg}.", "mistaken"),
        ):
            with self.subTest(sentence=sentence):
                self.assertTrue(any(f"Unlisted cause ({word})" in issue for issue in self.issues(sentence)))

    def test_verdict_words_judge_only_in_a_sentence_that_names_a_source(self):
        for sentence in (
            "It turned right after the peak reading.",
            "It made a right-hand turn.",
            "The reading is correct to the nearest knot.",
            "It was not mistaken for another aircraft.",
        ):
            with self.subTest(sentence=sentence):
                self.assertFalse([issue for issue in self.issues(sentence) if "Unlisted cause" in issue])
        for sentence, word in (
            ("The registry is right about the type.", "right"),
            ("adsbdb has the correct type.", "correct"),
            ("The type in the aircraft database is in error.", "error"),
            ("hexdb lists the wrong type.", "wrong"),
        ):
            with self.subTest(sentence=sentence):
                self.assertTrue(any(f"Unlisted cause ({word})" in issue for issue in self.issues(sentence)))


class DesignRoleTests(unittest.TestCase):
    """A purpose word directly before a role noun ("a surveillance aircraft") names a design role.
    Story prose may say so only of a purpose its type facts support, in a sentence that names no
    airframe, and never in a frame that assigns the role to one flight; what a flight was for
    stays a claim."""

    TYPES = {
        "P8": "Boeing P-8A Poseidon",
        "R135": "Boeing RC-135V",
        "E6": "Boeing E-6B Mercury",
        "C172": "Cessna 172 Skyhawk",
    }
    # Supported by the type's description or category and said of the type: they pass story and
    # profile checks. A preposition or a name ending in "ing" is no participle.
    ROLES = (
        ("P8", "The P-8 is a maritime patrol aircraft."),
        ("P8", "The P-8 was designed for maritime patrol."),
        ("R135", "The RC-135 is a surveillance aircraft."),
        ("R135", "The RC-135V is the signals intelligence variant."),
        ("R135", "The RC-135 is a surveillance-equipped aircraft."),
        ("R135", "The RC-135 remained a reconnaissance aircraft during several decades."),
        ("R135", "The RC-135 is a reconnaissance aircraft including several versions."),
        ("R135", "The RC-135 is a reconnaissance aircraft Boeing developed from the C-135."),
        ("R135", "RC-135s are surveillance aircraft."),
        ("R135", "This RC-135 is a surveillance aircraft."),
        # The role's sentence need not open with the type; only the airframe's names, frames, and
        # anchors refuse it (the second sentence's implicature is accepted).
        ("R135", "It is a reconnaissance aircraft Boeing developed from the C-135."),
        ("R135", "Boeing's RC-135 is a surveillance aircraft."),
        ("R135", "The RC-135 is a surveillance aircraft, and this one circled the city."),
    )
    # A profile sentence is the type's own description, so these need no support, no type subject,
    # and none of the story-only gates (a participle, "used as", "over", "for the", "while").
    PROFILE_ROLES = (
        ("E6", "The E-6B also flies in a training role."),
        ("E6", "The E-6B is a combat aircraft."),
        ("R135", "The RC-135 is a surveillance aircraft."),
        ("R135", "Boeing built the RC-135 as a reconnaissance platform."),
        ("R135", "It is a reconnaissance aircraft Boeing developed from the C-135."),
        ("R135", "The RC-135 is a reconnaissance aircraft carrying signals equipment."),
        ("P8", "The P-8 is a maritime patrol aircraft operating from land bases."),
        ("R135", "The RC-135 is used as a reconnaissance platform by the air force."),
    )
    # A role assigned to one flight, an unsupported role, a flight's purpose, a use one airframe
    # is put to, or a wrongdoing.
    REFUSED = (
        ("R135", "The RC-135 was working as a surveillance aircraft over the city."),
        ("R135", "It served as the surveillance platform for the operation."),
        ("R135", "The RC-135 flew a surveillance mission over the lake."),
        # Every branch takes the flight anchors and frames: "designed for", a hyphen compound, and
        # a role noun, including a type's own category ("reconnaissance aircraft").
        ("R135", "N555DZ was intended for surveillance that day."),
        ("R135", "N555DZ was built for surveillance over the city."),
        ("R135", "The RC-135 flew a surveillance-heavy pattern over the lake."),
        ("R135", "The RC-135 flew a surveillance-style orbit."),
        ("R135", "N555DZ is a surveillance aircraft watching the coast."),
        ("R135", "N555DZ is a surveillance aircraft circling the city."),
        ("R135", "It was a surveillance aircraft keeping watch on the harbor."),
        ("R135", "On this flight it was a reconnaissance platform."),
        ("R135", "It served as the reconnaissance aircraft for the operation."),
        ("R135", "N555DZ was working as a reconnaissance aircraft over the city."),
        ("P8", "N555DZ was working as a maritime patrol aircraft over the city."),
        ("P8", "The P-8 serves as the main maritime patrol aircraft of several navies."),
        ("P8", "The P-8 served as the main maritime patrol aircraft of several navies."),
        ("R135", "The RC-135 flew reconnaissance missions over Vietnam."),
        # A participle after the role is read as one flight's action in story prose.
        ("R135", "The RC-135 is a reconnaissance aircraft carrying signals equipment."),
        ("R135", "The RC-135 is a reconnaissance aircraft carrying signals equipment today."),
        ("P8", "The P-8 is a maritime patrol aircraft operating from land bases this morning."),
        # A role in a sentence that names the airframe's registration or callsign.
        ("R135", "N555DZ, a surveillance aircraft, circled the harbor."),
        ("R135", "As a surveillance aircraft, N555DZ circled the harbor."),
        ("R135", "N555DZ is a surveillance aircraft, and it circled the city for an hour."),
        ("R135", "N555DZ was a reconnaissance aircraft near the coast."),
        ("R135", "N555DZ was acting like a surveillance aircraft."),
        ("R135", "The RC-135 that DAL1 flew is a surveillance aircraft."),
        ("R135", "Boeing built the RC-135 as a reconnaissance platform."),
        # Any verb with "as a" or "like a" before the role assigns it in story prose, and so does
        # a flight's task or duration.
        ("R135", "The RC-135 circled the harbor as a surveillance aircraft."),
        ("R135", "It was acting like a surveillance aircraft."),
        ("R135", "As a surveillance aircraft, it circled the harbor for an hour."),
        ("R135", "It is a surveillance aircraft at work."),
        ("R135", "It stayed a surveillance platform for hours."),
        ("E6", "Every E-6B flight is a nuclear readiness drill."),
        ("E6", "The E-6B also flies in a training role."),
        ("P8", "It was on a maritime patrol."),
        ("P8", "It flew a maritime patrol."),
        ("C172", "N555DZ is a spy aircraft."),
        ("C172", "N555DZ is a surveillance aircraft."),
        ("C172", "N555DZ may be a surveillance aircraft."),
        ("C172", "N555DZ flew as an escort aircraft for the motorcade."),
        ("C172", "It was a search and rescue helicopter looking for a missing hiker."),
        ("C172", "N555DZ was the refueling tanker for the jets."),
        ("C172", "N555DZ was the interceptor jet that day."),
        ("C172", "N555DZ is a multi-mission aircraft."),
        ("C172", "N555DZ is a charter aircraft."),
        ("C172", "N555DZ is a smuggling aircraft."),
        ("C172", "N555DZ was built for surveillance."),
    )
    # A flight's purpose, or a role assigned to one flight, in a profile sentence.
    PROFILE_REFUSED = (
        ("R135", "The RC-135 flew a surveillance mission over the lake."),
        ("R135", "The RC-135 was working as a surveillance aircraft over the city."),
        ("R135", "It served as the surveillance platform for the operation."),
        ("E6", "Every E-6B flight is a nuclear readiness drill."),
        ("P8", "It flew a maritime patrol."),
        ("R135", "The RC-135 flew reconnaissance missions over Vietnam."),
        ("P8", "The P-8 serves as the main maritime patrol aircraft of several navies."),
        ("R135", "The RC-135 was a reconnaissance aircraft that day."),
        ("R135", "N555DZ was intended for surveillance that day."),
    )
    MISSION = re.compile(r"mission claim|Speculative purpose|Unlisted possibility|mission or purpose")

    def story_issues(self, code, sentence):
        facts = fixtures.facts(
            fixtures.event(
                "watch_type", registration="N555DZ", callsign="DAL1", typecode=code, desc=self.TYPES[code]
            )
        )
        story = editorial.render(facts, "episode")
        self.assertEqual(story_issues(story, facts), [])
        paragraphs = [*story["paragraphs"][:-1], story["paragraphs"][-1] + " " + sentence]
        return story_issues({**story, "paragraphs": paragraphs}, facts)

    def profile_issues(self, code, sentence):
        return story_facts.profile_issues(sentence, code, self.TYPES[code])

    def test_a_supported_design_role_passes_the_story_and_profile_checks(self):
        for code, sentence in self.ROLES:
            with self.subTest(sentence=sentence):
                self.assertEqual(self.story_issues(code, sentence), [])
                self.assertEqual(self.profile_issues(code, sentence), [])
        for code, sentence in self.PROFILE_ROLES:
            with self.subTest(sentence=sentence):
                self.assertEqual(self.profile_issues(code, sentence), [])

    def new_issues(self, facts, sentence, field="paragraph"):
        """What a sentence adds to the kind's template story, as its dek or in its last paragraph."""
        story = editorial.render(facts, "episode")
        if field == "dek":
            changed = {**story, "dek": sentence}
        else:
            changed = {
                **story,
                "paragraphs": [*story["paragraphs"][:-1], story["paragraphs"][-1] + " " + sentence],
            }
        before = set(story_issues(story, facts))
        return [issue for issue in story_issues(changed, facts) if issue not in before]

    def test_the_site_names_an_aircraft_by_its_class_and_quotes_its_entry(self):
        # A notable variant named by its category's modifier and class noun; the profile fact
        # that supports "135 series" stays a profile fact, and the class phrase names the type.
        raw = fixtures.kind_event("notable_variant")
        raw["evidence"]["aircraft"].update(t="R135", desc="Boeing RC-135V")
        facts = fixtures.facts(
            raw,
            profiles=fixtures.profile(
                "R135",
                "Boeing developed the RC-135 reconnaissance family through modifications to its 135 series "
                "airframes.",
            ),
        )
        self.assertIn("tp.R135.1", {item["id"] for item in facts["facts"]})
        family = "The reconnaissance family developed through modifications to Boeing 135 series airframes."
        self.assertEqual(self.new_issues(facts, family), [])
        # The dek here tests the names it uses, not whether it adds to the headline.
        self.assertEqual(
            [
                issue
                for issue in self.new_issues(facts, family[:-1] + ", and this is one of them.", "dek")
                if issue != "dek:adds_nothing"
            ],
            [],
        )
        # A P-8 named by its role, not its name.
        p8 = fixtures.facts(
            fixtures.event(
                "watch_type", registration="N555DZ", callsign="DAL1", typecode="P8", desc=self.TYPES["P8"]
            )
        )
        self.assertEqual(self.new_issues(p8, "It is a maritime patrol aircraft."), [])
        # A vintage patrol bomber named by its category, with its age or "vintage".
        raw = fixtures.kind_event("vintage")
        raw["evidence"]["aircraft"].update(t="P4Y", desc="CONSOLIDATED PB4Y-2 Privateer")
        vintage = fixtures.facts(raw)
        self.assertEqual(
            self.new_issues(vintage, "An 82-year-old patrol bomber was recorded in flight on 20 September."),
            [],
        )
        self.assertEqual(
            self.new_issues(
                vintage,
                "On 20 September the vintage patrol bomber was recorded in flight, 82 years after it was built.",
                "dek",
            ),
            [],
        )
        # A watchlist entry's tag quoted as the entry's, and the same words as an activity.
        raw = fixtures.kind_event("watchlist_match")
        raw["evidence"]["watchlist"]["tags"] = ["Navy", "Maritime Patrol"]
        raw["evidence"]["aircraft"].update(t="P8", desc=self.TYPES["P8"])
        watch = fixtures.facts(raw)
        self.assertEqual(
            self.new_issues(
                watch, "Plane Alert DB gives the entry a Navy association and a maritime patrol tag."
            ),
            [],
        )
        for sentence in (
            "It flew a maritime patrol.",
            "It was on a maritime patrol.",
            "It flew a maritime patrol entry into the zone.",
        ):
            with self.subTest(sentence=sentence):
                self.assertTrue(any(self.MISSION.search(issue) for issue in self.new_issues(watch, sentence)))

    def test_a_frame_that_opens_the_text_assigns_the_role_too(self):
        facts = fixtures.facts(
            fixtures.event(
                "watch_type", registration="N555DZ", callsign="DAL1", typecode="R135", desc=self.TYPES["R135"]
            )
        )
        story = editorial.render(facts, "episode")
        opening = "As a surveillance aircraft, it circled the harbor for an hour."
        for changed in (
            {**story, "paragraphs": [opening + " " + story["paragraphs"][0], *story["paragraphs"][1:]]},
            {**story, "dek": opening + " " + story["dek"]},
        ):
            with self.subTest(dek=changed["dek"][:20]):
                issues = story_issues(changed, facts)
                self.assertTrue(any(self.MISSION.search(issue) for issue in issues), issues)

    def test_a_role_the_facts_do_not_support_or_a_flight_purpose_is_refused(self):
        for code, sentence in self.REFUSED:
            with self.subTest(sentence=sentence):
                issues = self.story_issues(code, sentence)
                self.assertTrue(any(self.MISSION.search(issue) for issue in issues), issues)
        for code, sentence in self.PROFILE_REFUSED:
            with self.subTest(sentence=sentence):
                issues = self.profile_issues(code, sentence)
                self.assertTrue(any(self.MISSION.search(issue) for issue in issues), issues)


if __name__ == "__main__":
    unittest.main()
