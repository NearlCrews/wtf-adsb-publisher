"""The type-name gap tool keeps its traffic output under .private/ and merges reviewed names."""

import json
import sqlite3
import sys
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
import type_name_gaps

from wtf_adsb_publisher import reference, story_facts

ENTRY = {
    "name": "Testmaker T-1",
    "short": "T-1",
    "slug": "testmaker-t-1",
    "category": "light aircraft",
    "manufacturer": "Testmaker",
    "aliases": [],
}


def journal(path, rows):
    """A journal copy with the engine's daily table (its collector schema) and these rows."""
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE daily (day TEXT, hex TEXT, typecode TEXT, PRIMARY KEY(day,hex))")
        db.executemany("INSERT INTO daily (day, hex, typecode) VALUES (?, ?, ?)", rows)
        db.commit()


class GapTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.private = self.root / ".private"
        self.enterContext(patch.object(type_name_gaps, "PRIVATE", self.private))
        # A fixture table, so later curation of A129 or A342 cannot change what counts as a gap.
        table = self.root / "type_names.json"
        table.write_text(json.dumps({"types": {"C172": ENTRY}}))
        self.enterContext(patch.object(type_name_gaps, "TABLE", table))
        self.enterContext(redirect_stdout(StringIO()))

    def test_out_outside_private_is_refused(self):
        source = self.root / "journal.sqlite3"
        journal(source, [])
        for out in (self.root / "gaps.json", self.private / ".." / "gaps.json", self.private):
            with self.subTest(out=out), self.assertRaises(SystemExit):
                type_name_gaps.main(["--journal", str(source), "--out", str(out)])
        self.assertFalse((self.root / "gaps.json").exists())
        self.assertFalse(self.private.exists())

    def test_lists_uncurated_designators_from_journal_and_ledger(self):
        source = self.root / "journal.sqlite3"
        journal(
            source,
            [
                ("2026-06-02", "a00001", "A129"),
                ("2026-06-03", "a00001", "A129"),
                ("2026-06-03", "a00002", "A129"),
                ("2026-06-03", "~00003", "A748"),
                ("2026-05-31", "a00004", "A25"),
                ("2026-06-04", "a00005", "C172"),
                ("2026-06-04", "a00006", ""),
            ],
        )
        ledger = self.root / "ledger.json"
        ledger.write_text(
            json.dumps(
                {
                    "records": [
                        {"evidence": {"aircraft": {"type_code": "A129"}}},
                        {"evidence": {"aircraft": {"type_code": "A342"}}},
                        {"evidence": {"aircraft": {"type_code": "C172"}}},
                    ]
                }
            )
        )
        out = self.private / "gaps" / "type-name-gaps.json"
        type_name_gaps.main(["--journal", str(source), "--ledger", str(ledger), "--out", str(out)])
        gaps = json.loads(out.read_text())
        self.assertEqual(sorted(gaps), ["A129", "A342"])
        description = lambda code: (reference.type_info(code) or {}).get("description")  # noqa: E731
        self.assertEqual(gaps["A129"], {"days": 2, "aircraft": 2, "stories": 1, "desc": description("A129")})
        self.assertEqual(gaps["A342"], {"days": 0, "aircraft": 0, "stories": 1, "desc": description("A342")})
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.private.stat().st_mode & 0o777, 0o700)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.table = self.root / "type_names.json"
        self.table.write_text((ROOT / "src/wtf_adsb_publisher/data/type_names.json").read_text())
        self.enterContext(patch.object(type_name_gaps, "TABLE", self.table))
        self.enterContext(redirect_stdout(StringIO()))

    def test_render_keeps_the_table_layout(self):
        raw = self.table.read_text()
        self.assertEqual(type_name_gaps.render(json.loads(raw)), raw)

    def test_render_refuses_a_layout_it_cannot_fill(self):
        # With "types" last, the placeholder has no comma after it and would be written empty.
        data = json.loads(self.table.read_text())
        types = data.pop("types")
        with self.assertRaisesRegex(SystemExit, "types"):
            type_name_gaps.render({**data, "types": types})

    def additions(self, entries):
        path = self.root / "additions.json"
        path.write_text(json.dumps(entries))
        return path

    def test_merge_adds_sorted_entries_with_a_note_and_a_verifiable_hash(self):
        before = json.loads(self.table.read_text())
        new = {"ZZ01": ENTRY, "AAA1": {**ENTRY, "slug": "testmaker-t-2", "category": "test category"}}
        type_name_gaps.main(["--merge", str(self.additions(new)), "--since", "2026-07-01"])
        raw = self.table.read_text()
        after = json.loads(raw)
        self.assertEqual(raw, type_name_gaps.render(after))
        self.assertIn('\n  "ZZ01": ' + json.dumps(ENTRY) + "\n }", raw)
        self.assertEqual(list(after["types"]), sorted(after["types"]))
        self.assertEqual(after["types"], {**before["types"], **new})
        self.assertEqual(after["notes"][:-1], before["notes"])
        self.assertIn(": 2 designators seen in this collection since 2026-07-01", after["notes"][-1])
        # The category words come from the entries themselves.
        self.assertTrue(after["notes"][-1].endswith(" Categories added: test category."))
        content = {key: value for key, value in after.items() if key != "content_sha256"}
        self.assertEqual(
            after["content_sha256"],
            story_facts.hashlib.sha256(story_facts._canonical(content).encode()).hexdigest(),
        )

    def test_merge_refuses_curated_codes_incomplete_entries_and_empty_additions(self):
        before = self.table.read_text()
        incomplete = {key: value for key, value in ENTRY.items() if key != "aliases"}
        for entries, message in (
            ({"C172": ENTRY}, "C172 is already curated"),
            ({"ZZ01": incomplete}, r"ZZ01 lacks \['aliases'\]"),
            ({"ZZ01": {**ENTRY, "slug": "Testmaker T1"}}, "ZZ01 has an invalid slug"),
            ({"ZZ01": {**ENTRY, "short_slug": "T 1"}}, "ZZ01 has an invalid short_slug"),
            ({"ZZ01": {**ENTRY, "short_slug": None}}, "ZZ01 has an invalid short_slug"),
            # A slug is one type's page: another type's slug is refused unless the names match.
            ({"ZZ01": {**ENTRY, "slug": "cessna-172"}}, "ZZ01 shares slug cessna-172 with C172"),
            (
                {"ZZ01": ENTRY, "ZZ02": {**ENTRY, "name": "Testmaker T-2"}},
                "ZZ02 shares slug testmaker-t-1 with ZZ01",
            ),
            ({}, "the additions are empty"),
        ):
            with self.subTest(entries=entries), self.assertRaisesRegex(SystemExit, message):
                type_name_gaps.main(["--merge", str(self.additions(entries))])
        self.assertEqual(self.table.read_text(), before)

    def test_codes_naming_one_type_may_share_its_slug(self):
        # The E75L and E75S convention: two designators, one name, one slug.
        cessna = json.loads(self.table.read_text())["types"]["C172"]
        type_name_gaps.main(["--merge", str(self.additions({"ZZ01": cessna, "ZZ02": ENTRY, "ZZ03": ENTRY}))])
        after = json.loads(self.table.read_text())["types"]
        self.assertEqual((after["ZZ01"], after["ZZ02"], after["ZZ03"]), (cessna, ENTRY, ENTRY))

    def test_merge_with_gap_list_options_is_refused(self):
        private = self.root / ".private"
        self.enterContext(patch.object(type_name_gaps, "PRIVATE", private))
        before = self.table.read_text()
        additions = str(self.additions({"ZZ01": ENTRY}))
        for extra in (
            ["--journal", str(self.root / "journal.sqlite3")],
            ["--ledger", str(self.root / "ledger.json")],
            ["--out", str(private / "gaps.json")],
        ):
            with self.subTest(extra=extra), self.assertRaisesRegex(SystemExit, "--merge takes no"):
                type_name_gaps.main(["--merge", additions, *extra])
        self.assertEqual(self.table.read_text(), before)
        self.assertFalse(private.exists())


if __name__ == "__main__":
    unittest.main()
