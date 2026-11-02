#!/usr/bin/env python3
"""List type designators the story engine cannot name, and merge reviewed names into the table.

    python3 -B tools/type_name_gaps.py --journal COPY.sqlite3 [--ledger ledger.json] --out .private/type-name-gaps.json
    python3 -B tools/type_name_gaps.py --merge additions.json [--since YYYY-MM-DD]

The gap list is local traffic data: write it under .private/ and never commit it.
A merge adds entries that do not exist yet, records the review in the notes (the
first day counted, from --since, and any category words the entries bring), and
rewrites the content hash that story_facts.table verifies. It refuses a slug that
another type's entry already uses under a different name.
"""

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from wtf_adsb_publisher import (
    public_events,  # noqa: E402
    reference,  # noqa: E402
    story_facts,  # noqa: E402
)

TABLE = story_facts.DATA / story_facts.TABLES["type_names"][0]
PRIVATE = PROJECT / ".private"
FIELDS = ("name", "short", "slug", "category", "manufacturer", "aliases")
DEFAULT_SINCE = "2026-06-01"
# The review a merge records, and the sentence it adds for new category words.
REVIEW_NOTE = (
    "{today}: {count} designators seen in this collection since {since} were added, "
    "with names checked against ICAO Doc 8643 and the Mictronics aircraft descriptions."
)
CATEGORY_NOTE = " Categories added: {categories}."


def gaps(journal, ledger, since):
    """{code: {"days", "aircraft", ["stories",] "desc"}} for designators without a curated entry."""
    names = public_events.read_json(TABLE)["types"]
    found = {}
    if journal:
        uri = f"{Path(journal).resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            for code, days, aircraft in db.execute(
                "SELECT typecode, COUNT(DISTINCT day), COUNT(DISTINCT hex) FROM daily "
                "WHERE typecode<>'' AND hex NOT LIKE '~%' AND day>=? GROUP BY typecode",
                (since,),
            ):
                found[code] = {"days": days, "aircraft": aircraft}
    if ledger:
        for record in public_events.read_json(Path(ledger)).get("records", []):
            code = ((record.get("evidence") or {}).get("aircraft") or {}).get("type_code")
            if code:
                entry = found.setdefault(code, {"days": 0, "aircraft": 0})
                entry["stories"] = entry.get("stories", 0) + 1
    return {
        code: {**entry, "desc": (reference.type_info(code) or {}).get("description")}
        for code, entry in sorted(found.items())
        if code not in names
    }


def render(data):
    """The table's layout: one-space indents, with each type entry on a line of its own."""
    text = json.dumps({**data, "types": {}}, indent=1, ensure_ascii=False)
    body = ",\n".join(
        f"  {json.dumps(code)}: {json.dumps(entry, ensure_ascii=False)}"
        for code, entry in data["types"].items()
    )
    placeholder = '\n "types": {},\n'
    if placeholder not in text:
        # "types" last, with no comma after it, would be written empty.
        raise SystemExit('the table needs "types" before its last key')
    return text.replace(placeholder, '\n "types": {\n' + body + "\n },\n", 1) + "\n"


def merge(additions, since=DEFAULT_SINCE):
    data = public_events.read_json(TABLE)
    new = public_events.read_json(Path(additions))
    if not new:
        raise SystemExit("the additions are empty")
    known = {entry["category"] for entry in data["types"].values()}
    # A slug is one type's page; codes share it only when they name one type (E75L and E75S).
    slugs = {}
    for code, entry in data["types"].items():
        slugs.setdefault(entry.get("slug"), (code, entry["name"]))
    for code, entry in new.items():
        if code in data["types"]:
            raise SystemExit(f"{code} is already curated")
        missing = [field for field in FIELDS if field not in entry]
        if missing:
            raise SystemExit(f"{code} lacks {missing}")
        for field in ("slug", "short_slug"):
            if field in entry and not (
                isinstance(entry[field], str) and public_events.SLUG.fullmatch(entry[field])
            ):
                raise SystemExit(f"{code} has an invalid {field}")
        owner, name = slugs.setdefault(entry["slug"], (code, entry["name"]))
        if name != entry["name"]:
            raise SystemExit(f"{code} shares slug {entry['slug']} with {owner}, which names {name}")
    added = sorted({entry["category"] for entry in new.values()} - known)
    data["types"] = dict(sorted({**data["types"], **new}.items()))
    today = date.today().isoformat()
    data["prepared_on"] = today
    note = REVIEW_NOTE.format(today=today, count=len(new), since=since)
    data["notes"].append(note + (CATEGORY_NOTE.format(categories=", ".join(added)) if added else ""))
    content = {key: value for key, value in data.items() if key != "content_sha256"}
    data["content_sha256"] = reference.canonical_hash(content)
    public_events.write_bytes_atomic(TABLE, render(data).encode())
    print(f"{len(new)} entries merged; {len(data['types'])} in all")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--journal", type=Path, help="A copy of the engine journal (opened read-only)")
    parser.add_argument("--ledger", type=Path, help="A copy of the public-events ledger")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="First journal day counted (YYYY-MM-DD)")
    parser.add_argument("--out", type=Path, help="Gap list, which must be under .private/")
    parser.add_argument("--merge", type=Path, help="Reviewed additions to merge into type_names.json")
    args = parser.parse_args(argv)
    if args.merge and (args.journal or args.ledger or args.out):
        raise SystemExit("--merge takes no --journal, --ledger, or --out")
    if args.merge:
        merge(args.merge, args.since)
        return 0
    if not (args.journal or args.ledger):
        raise SystemExit("give --journal, --ledger, or --merge")
    private = PRIVATE.resolve()
    out = args.out.resolve() if args.out else None
    if out is None or out == private or not out.is_relative_to(private):
        raise SystemExit("--out must be under .private/")
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    found = gaps(args.journal, args.ledger, args.since)
    public_events.write_bytes_atomic(out, (json.dumps(found, indent=1) + "\n").encode(), private=True)
    print(f"{len(found)} designators without a name")
    return 0


if __name__ == "__main__":
    sys.exit(main())
