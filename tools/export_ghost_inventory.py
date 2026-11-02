#!/usr/bin/env python3
"""Export every Ghost post and page, in every status, to a private file. Read-only.

The export feeds the redo's bootstrap slug check, the publisher's dry run and
the ownership checks. It reads the Admin API only; the key is never printed.
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher.ghost_client import GhostClient, GhostError

SCHEMA_VERSION = 1


def export(client, *, at=None):
    """Posts and pages with the counts needed to check them; as_of follows the last read."""

    def state(items):
        return {item["id"]: (item.get("slug"), item.get("status"), item.get("updated_at")) for item in items}

    posts = client.inventory()
    page_items = client.pages()
    # A post created, removed or changed while pages were read would make the export inconsistent.
    if state(client.inventory()) != state(posts):
        raise GhostError("Ghost posts changed during the export")
    slugs = Counter(item.get("slug") for item in [*posts, *page_items])
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of_utc": public.stamp(at),
        "complete": True,
        "total": len(posts),
        "posts": posts,
        "pages": page_items,
        "counts": {
            "posts": len(posts),
            "posts_by_status": dict(sorted(Counter(item.get("status") for item in posts).items())),
            "pages": len(page_items),
            "pages_by_status": dict(sorted(Counter(item.get("status") for item in page_items).items())),
            "duplicate_slugs": sorted(slug for slug, count in slugs.items() if slug and count > 1),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New private file for the export")
    args = parser.parse_args()
    os.umask(0o077)
    if args.output.exists():
        parser.error("Choose a new output file; earlier exports are kept")
    try:
        config = public.load_config(args.config)
        client = GhostClient(config["ghost_origin"], Path(config["ghost_key_file"]).read_text())
        result = export(client)
        public.write_json_atomic(args.output, result, private=True)
        print(json.dumps({**result["counts"], "as_of_utc": result["as_of_utc"], "output": str(args.output)}))
    except (OSError, ValueError, KeyError, TypeError, GhostError) as error:
        # Ghost's and this tool's messages are status lines, such as a refusal or a change
        # during the export; neither carries the key.
        detail = f": {error}" if isinstance(error, (ValueError, GhostError)) else ""
        parser.exit(1, f"Ghost export failed ({type(error).__name__}{detail}); nothing was changed.\n")


if __name__ == "__main__":
    main()
