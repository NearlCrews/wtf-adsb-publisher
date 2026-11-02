#!/usr/bin/env python3
"""Validate a public snapshot and render its article bodies for the static build.

Reads the snapshot on stdin and writes JSON with the validated snapshot, each
event's article HTML, Ghost excerpt and reader links. ``public_events`` alone
decides what a snapshot may contain: the story engine's ``story``, ``context``,
``released_at`` and ``content_revised_at`` fields, the ``/stories/{slug}/``
article paths and every value the site renders from them, such as a hub path,
heading or source link. The build therefore accepts exactly what the exporter
writes, no more and no less.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import (
    public_events,  # noqa: E402
    publication,  # noqa: E402
)


def load(snapshot):
    """Validated snapshot plus the per-event HTML the theme inserts as content."""
    validated = public_events.validate_snapshot(snapshot)
    events = validated["events"]
    return {
        "snapshot": validated,
        "articles": {event["id"]: publication.article_html(event) for event in events},
        "excerpts": {event["id"]: publication.article_excerpt(event) for event in events},
        "references": {event["id"]: publication.aircraft_references(event) for event in events},
    }


def strict_json(text):
    """Parse like public_events.read_json: no duplicate keys or non-finite numbers."""

    def reject_constant(_value):
        raise ValueError("JSON contains a non-finite number")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON contains a duplicate key")
            result[key] = value
        return result

    return json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)


def main():
    json.dump(load(strict_json(sys.stdin.read())), sys.stdout)


if __name__ == "__main__":
    main()
