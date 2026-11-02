"""Type facts seeded from retired dossiers keep only citations that bind to one sentence."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wtf_adsb_publisher import weekly_research

NOW = "2026-10-05T15:00:00Z"
SERVICE = "The E-6 entered service with the U.S. Navy in 1989."
BUILT = "Boeing built the E-6 on the 707 airframe line for the U.S. Navy."


def source(identifier, title, url, publisher):
    return {
        "id": identifier,
        "title": title,
        "url": url,
        "publisher": publisher,
        "retrieved_at": NOW,
        "applies_to": "Type only.",
        "primary": True,
    }


BOEING = source("maker", "Type history", "https://www.boeing.com/history/products/e-6", "Boeing")
NAVY = source("navy", "Fact file", "https://www.navy.mil/Resources/Fact-Files/e-6/", "U.S. Navy")


def ledger(sections, sources):
    return {
        "records": [
            {
                "state": "approved",
                "evidence": {"aircraft": {"type_code": "E6", "type_description": "Boeing E-6B Mercury"}},
                "editorial": {"weekly_spotlight": {"sections": sections, "sources": sources}},
            }
        ]
    }


def seeded(sections, sources):
    profiles = weekly_research.seed_profiles(ledger(sections, sources), NOW)
    return [
        (fact["text"], fact["source"]["publisher"])
        for profile in profiles.values()
        for fact in profile["facts"]
    ]


class SeedCitationTests(unittest.TestCase):
    def test_a_multi_source_section_gives_no_fact_whichever_source_comes_first(self):
        section = {"scope": "type_background", "sources": ["maker", "navy"], "text": SERVICE}
        # The sentence is the Navy's; the section's first citation is Boeing's. Neither
        # order may bind the sentence to one of them.
        for sources in ((BOEING, NAVY), (NAVY, BOEING)):
            with self.subTest(first=sources[0]["publisher"]):
                self.assertEqual(seeded([section], sources), [])
        reversed_section = {**section, "sources": ["navy", "maker"]}
        self.assertEqual(seeded([reversed_section], (BOEING, NAVY)), [])

    def test_single_source_sections_still_seed_their_first_passing_fact(self):
        sections = [
            {"scope": "type_background", "sources": ["maker"], "text": BUILT},
            {"scope": "type_background", "sources": ["maker", "navy"], "text": SERVICE},
            {"scope": "type_background", "sources": ["navy"], "text": SERVICE},
        ]
        self.assertEqual(seeded(sections, (BOEING, NAVY)), [(BUILT, "Boeing"), (SERVICE, "U.S. Navy")])
        # A lone citation that names no listed source binds nothing either.
        unknown = [{"scope": "type_background", "sources": ["missing"], "text": BUILT}]
        self.assertEqual(seeded(unknown, (BOEING,)), [])
        self.assertEqual(
            seeded([{"scope": "type_background", "sources": "maker", "text": BUILT}], (BOEING,)), []
        )


if __name__ == "__main__":
    unittest.main()
