"""The theme's topic and field guide copy quotes the publisher's own rules and names."""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wtf_adsb_publisher import curation, editorial, public_writer, rules, selection, story_facts

THEME = ROOT / "tools" / "ghost-theme"
# Rules the engine applies that the publisher keeps no constant for; the copy quotes them.
ENGINE_OPERATOR_WARM_UP_DAYS = 30
ENGINE_DESCENT_BANDS = (
    # (floor ft, rate ft per minute, seconds, reports)
    (10000, 6000, 60, 6),
    (0, 4500, 30, 4),
)
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 6: "six", 7: "seven"}


def days(seconds):
    return seconds // 86400


def figure(value):
    return f"{value:,}"


@unittest.skipUnless(shutil.which("node"), "Node reads the theme's copy")
class ThemeCopy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = (
            f"const topics = await import({json.dumps((THEME / 'topics.mjs').as_uri())});"
            f"const guide = await import({json.dumps((THEME / 'kind-guide.mjs').as_uri())});"
            "console.log(JSON.stringify({topics: topics.topicCopy, triggers: topics.kindTriggers,"
            " guide: guide.KIND_GUIDE}));"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
            cwd=THEME,
        )
        data = json.loads(result.stdout.splitlines()[-1])
        cls.topics, cls.triggers, cls.guide = data["topics"], data["triggers"], data["guide"]

    def intro(self, slug):
        return " ".join(self.topics[slug]["intro"])

    def test_kind_titles_are_the_field_guide_names(self):
        for kind, entry in self.guide.items():
            with self.subTest(kind=kind):
                self.assertEqual(story_facts.KIND_TITLES[kind], entry["name"])

    def test_the_type_variant_and_routine_windows(self):
        notable = self.intro("notable-aircraft")
        self.assertIn(
            f"at most once every {days(curation.TYPE_COOLDOWN)} days, whichever airframe flies it, and a "
            f"notable variant at most once every {days(curation.VARIANT_COOLDOWN)} days",
            notable,
        )
        self.assertIn(
            f"recorded on {curation.ROUTINE_DAYS} or more of the last {curation.ROUTINE_WINDOW} days of coverage "
            f"counts as routine and gets at most one identity story every {days(curation.ROUTINE_COOLDOWN)} days",
            notable,
        )
        self.assertIn(f"at most once every {days(curation.TYPE_COOLDOWN)} days", self.triggers["watch_type"])
        self.assertNotRegex(
            json.dumps([self.topics, self.triggers]), r"once every 14 days, whichever airframe"
        )

    def test_the_vintage_ages_and_wait(self):
        unseen = curation.VINTAGE_RARITY[0][1]
        ages = [curation.VINTAGE_YEARS + unseen - bonus for _days, bonus in curation.VINTAGE_RARITY[1:]]
        ages.append(curation.VINTAGE_YEARS + unseen)
        low, high = curation.VINTAGE_RARITY[0][0] + 1, curation.VINTAGE_RARITY[1][0]
        rule = (
            f"one or {WORDS[high]} of the last {curation.ROUTINE_WINDOW} days{{coverage}} needs {ages[0]} years, "
            f"one recorded on {WORDS[low + high]} to {WORDS[curation.VINTAGE_RARITY[2][0]]} days needs {ages[1]}, "
            f"and any other type needs {ages[2]}"
        )
        self.assertEqual(low, 1)
        self.assertIn(rule.format(coverage=" of coverage"), self.intro("vintage-aircraft"))
        self.assertIn(rule.format(coverage=""), self.triggers["vintage"])
        for text in (self.intro("vintage-aircraft"), self.triggers["vintage"]):
            with self.subTest(text=text[:40]):
                self.assertIn(f"at least {curation.VINTAGE_YEARS} years old", text)
                self.assertIn(f"{days(curation.VINTAGE_COOLDOWN)} days", text)

    def test_the_first_sighting_baselines(self):
        first = self.intro("first-sightings")
        self.assertEqual(rules.COVERAGE_SECONDS, 3600)
        coverage = f"at least {rules.FIRST_TYPE_COVERAGE_DAYS} days with an hour or more of recorded traffic"
        self.assertIn(coverage, first)
        self.assertIn(coverage, self.triggers["first_type_here"])
        other = f"more than {WORDS[rules.FIRST_TYPE_MAX_OTHER_TYPE_DAYS]} earlier days"
        self.assertIn(other, first)
        self.assertIn(other, self.triggers["first_type_here"])
        self.assertIn(f"after a {rules.FIRST_COUNTRY_COVERAGE_DAYS}-day warm-up", first)
        self.assertIn(
            f"after a {rules.FIRST_COUNTRY_COVERAGE_DAYS}-day warm-up", self.triggers["first_country_here"]
        )
        self.assertIn(f"Operator firsts wait {ENGINE_OPERATOR_WARM_UP_DAYS} days", first)
        self.assertIn(f"after {ENGINE_OPERATOR_WARM_UP_DAYS} days", self.triggers["first_operator_here"])

    def test_the_descent_bands(self):
        (floor, high_rate, high_seconds, high_reports), (_, low_rate, low_seconds, low_reports) = (
            ENGINE_DESCENT_BANDS
        )
        topic = self.topics["rapid-descents"]
        intro = self.intro("rapid-descents")
        self.assertIn(
            f"A descent that starts at {figure(floor)} ft or higher must hold {figure(high_rate)} ft per minute "
            f"for {high_seconds} seconds and {WORDS[high_reports]} reports; below that, {figure(low_rate)} ft per "
            f"minute for {low_seconds} seconds and {WORDS[low_reports]} reports is enough.",
            intro,
        )
        self.assertIn(
            f"held at {figure(high_rate)} ft per minute or more for {high_seconds} seconds and {WORDS[high_reports]} "
            f"reports when it starts at {figure(floor)} ft or higher, or at {figure(low_rate)} ft per minute or more "
            f"for {low_seconds} seconds and {WORDS[low_reports]} reports below that",
            self.triggers["rapid_descent"],
        )
        for text in (topic["title"], topic["description"]):
            with self.subTest(text=text):
                self.assertIn(figure(high_rate), text)
                self.assertIn(figure(low_rate), text)
        self.assertIn(figure(floor), topic["description"])
        self.assertLessEqual(len(topic["description"]), 160)

    def test_the_record_margin_and_baseline(self):
        knots, fraction = rules.RECORD_MARGIN
        margin = f"by {knots} knots or {fraction:.0%}, whichever is larger"
        baseline = f"at least {rules.SEASON_MIN_BASELINE_DAYS} days and {rules.SEASON_MIN_ADDRESSES} aircraft"
        self.assertEqual(rules.SEASON_SUSTAINED_SECONDS, 60)
        reports = f"at least {WORDS[rules.SEASON_MIN_SAMPLES]} of them over a minute or more"
        speed = self.intro("speed-and-altitude")
        for text in (speed, self.triggers["seasonal_speed_record"]):
            with self.subTest(text=text[:40]):
                self.assertIn(margin, text)
                self.assertIn(baseline, text)
        self.assertIn(reports, speed)
        self.assertIn(
            f"at least {WORDS[rules.SEASON_MIN_SAMPLES]} reports", self.triggers["seasonal_speed_record"]
        )

    def test_the_rare_type_rule(self):
        trigger = self.triggers["rare_here"]
        self.assertIn(
            f"{WORDS[rules.RARE_TYPE_ADDRESSES].capitalize()} or fewer aircraft carried the type in the last "
            f"{rules.RARE_TYPE_WINDOW_DAYS} days of records, no more than {rules.RARE_TYPE_SHARE:.0%} of the "
            "aircraft seen",
            trigger,
        )
        self.assertIn(f"at most {WORDS[selection.RARE_STORY_DAYS]} earlier day", trigger)
        self.assertIn(f"own last {rules.RARE_TYPE_WINDOW_DAYS} days", self.guide["rare_here"]["what"])

    def test_the_ground_speed_ceiling(self):
        ceiling = f"never above {rules.MAX_PLAUSIBLE_GS_KT} knots"
        self.assertIn(ceiling, self.intro("speed-and-altitude"))
        self.assertIn(
            f"never counts above {rules.MAX_PLAUSIBLE_GS_KT} knots", self.triggers["seasonal_speed_record"]
        )
        self.assertIn(f"above {rules.MAX_PLAUSIBLE_GS_KT} knots", self.triggers["outlier_gs"])

    def test_the_holding_story_length(self):
        minutes = curation.HOLDING_STORY_SECONDS // 60
        self.assertIn(f"shorter than {minutes} minutes", self.intro("holding-patterns"))
        self.assertIn(f"lasts {minutes} minutes or more", self.triggers["prolonged_holding"])

    def test_the_winds_series(self):
        self.assertIn(
            f"at most once every {WORDS[days(curation.WINDS_COOLDOWN)]} days",
            self.triggers["winds_aloft_day"],
        )

    def test_the_registry_week_is_one_rule(self):
        # "A week" is written out wherever the registry wait is quoted; a change to the wait
        # must change each of these.
        self.assertEqual(curation.REGISTRY_LAG_SECONDS, 7 * 86400)
        week = re.compile(r"\ba week\b")
        self.assertRegex(self.intro("unlisted-addresses"), week)
        self.assertRegex(self.topics["unlisted-addresses"]["description"], week)
        self.assertRegex(self.triggers["unregistered_address"], week)
        notes = [note for kinds, note in public_writer.KIND_NOTES if "unregistered_address" in kinds]
        self.assertTrue(notes)
        self.assertRegex(notes[0], week)
        templates = " ".join(
            text for angle in editorial.UNLISTED_ANGLES for text in (angle.focus, *angle.deks, *angle.openers)
        )
        self.assertRegex(templates, week)

    def test_topic_descriptions_fit_the_search_snippet(self):
        for slug, topic in self.topics.items():
            with self.subTest(slug=slug):
                self.assertLessEqual(len(topic["description"]), 160)

    def test_the_holding_topic_covers_holds_and_stacks(self):
        topic = self.topics["holding-patterns"]
        for text in (topic["title"], topic["heading"]):
            with self.subTest(text=text):
                self.assertRegex(text, r"(?i)\bholds?\b")
                self.assertRegex(text, r"(?i)\bstacks?\b|stacked")
        self.assertNotIn("a long hold flown for other reasons", self.intro("holding-patterns"))


if __name__ == "__main__":
    unittest.main()
