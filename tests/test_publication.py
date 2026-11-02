"""Observation evidence survives as safe native Ghost content without API calls."""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import public_events, publication
from wtf_adsb_publisher.weekly_spotlight import PublishedBlocks

NOW = "2026-10-04T20:00:00Z"
START = "2026-10-03T23:55:00Z"
END = "2026-10-04T00:05:00Z"
READING = "2026-10-03T23:58:17Z"
WINDOW_START = "2026-10-03T23:57:00Z"
WINDOW_END = "2026-10-04T00:02:00Z"


def fixture(*, published=False, synthetic=False, ground_speed=70, squawk="1200", observed_start=START):
    ledger = public_events.new_ledger("2026-09-05")
    evidence = {
        "schema_version": 1,
        "kind": "circling",
        "observation": {"start_utc": observed_start, "end_utc": END},
        "aircraft": {
            "registration": "N12345",
            "type_code": "B407",
            "type_description": 'Bell & "example" helicopter',
            "receiver_lat": 12.345678,
        },
        "received": {
            "source": "mlat",
            "readings": [{"field": "gs", "value": ground_speed}],
            "saved_readings_at_utc": READING,
            "squawk": squawk,
            "emergency_status": "none",
        },
        "pattern": {
            "start_utc": WINDOW_START,
            "end_utc": WINDOW_END,
            "duration_seconds": 300,
            "closed_loops": 3,
            "quality_review_required": True,
        },
        "links": [
            {
                "id": "reference",
                "label": 'Reference & "details"',
                "url": "https://example.com/reference?aircraft=N12345&view=photos",
            }
        ],
    }
    event_id = public_events.put_event(
        ledger,
        "PRIVATE-EPISODE-KEY",
        evidence,
        {
            "title": "Selected positions contain three loops",
            "summary": "The loop count applies to the selected five-minute window.",
            "article_path": "/stories/example-observation-3-oct-2026/",
            "ghost_id": "0123456789abcdef01234567",
            "synthetic": synthetic,
        },
        at=NOW,
    )
    inventory = None
    if published:
        public_events.set_review_state(ledger, event_id, "approved", at=NOW)
        inventory = {
            "complete": True,
            "total": 1,
            "as_of_utc": NOW,
            "posts": [
                {
                    "slug": "example-observation-3-oct-2026",
                    "id": "0123456789abcdef01234567",
                    "status": "published",
                    "visibility": "public",
                    "published_at": "2026-10-04T12:00:00Z",
                    "updated_at": "2026-10-04T15:00:00Z",
                }
            ],
        }
    return (
        public_events.export_snapshot(
            ledger, mode="published" if published else "review", as_of=NOW, inventory=inventory
        ),
        event_id,
    )


class CardParser(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


class PublicationCardTests(unittest.TestCase):
    def test_aircraft_lookups_keep_tail_and_callsign_separate_without_mutating_evidence(self):
        snapshot, _identifier = fixture()
        event = deepcopy(snapshot["events"][0])
        event["aircraft"]["callsign"] = "TEST123"
        event["references"].append(
            {
                "id": "flight_lookup",
                "label": "Callsign lookup",
                "url": "https://www.flightaware.com/live/flight/TEST123",
            }
        )
        original = deepcopy(event)
        links = publication.aircraft_references(event)
        urls = [link["url"] for link in links]
        self.assertIn("https://www.flightaware.com/live/flight/TEST123", urls)
        self.assertIn("https://www.flightaware.com/live/flight/N12345", urls)
        wiki = next(link for link in links if link["id"] == "type_wikipedia_search")
        self.assertEqual(
            parse_qs(urlsplit(wiki["url"]).query)["search"],
            [event["aircraft"]["type_description"]],
        )
        self.assertEqual(event, original)
        event["references"] = links
        self.assertEqual(publication.aircraft_references(event), links)
        event["aircraft"] = {}
        event["references"] = []
        self.assertEqual(publication.aircraft_references(event), [])

    def test_numeric_display_rounds_without_changing_saved_values_or_code_strings(self):
        snapshot, identifier = fixture(ground_speed=1984.2960000038147, squawk="0042")
        original = deepcopy(snapshot)
        html = publication.event_card(snapshot, identifier)
        self.assertIn("1,984.296", html)
        self.assertNotIn("1984.2960000038147", html)
        self.assertIn(">0042</td>", html)
        self.assertIn("Displayed measurements may be rounded", html)
        self.assertEqual(snapshot, original)
        tiny, identifier = fixture(ground_speed=0.000001)
        self.assertIn("1e-06", publication.event_card(tiny, identifier))

    def test_observation_reports_and_analysis_windows_keep_separate_times(self):
        snapshot, identifier = fixture()
        html = publication.event_card(snapshot, identifier)
        parsed = CardParser(html)
        stamps = [attrs["datetime"] for tag, attrs in parsed.tags if tag == "time"]
        for stamp in (START, END, READING, WINDOW_START, WINDOW_END, NOW):
            self.assertIn(stamp, stamps)
        self.assertIn("Analysis window:", html)
        self.assertIn("Saved reading at", html)
        self.assertEqual(html.count('datetime="' + READING + '"'), 1)
        self.assertEqual(html.count('datetime="' + WINDOW_START + '"'), 1)
        self.assertEqual(html.count('datetime="' + WINDOW_END + '"'), 1)
        self.assertTrue(all(attrs.get("scope") == "row" for tag, attrs in parsed.tags if tag == "th"))
        self.assertEqual(sum(tag == "table" for tag, _ in parsed.tags), 4)
        self.assertIn("Multilateration (MLAT)", html)
        self.assertIn("Source or geometry quality requires review", html)
        self.assertIn("Unpublished review", html)
        self.assertIn("Review updated", html)
        self.assertNotIn('<th scope="row">Post date</th>', html)
        self.assertNotIn("12.345678", html)
        self.assertNotIn("PRIVATE-EPISODE-KEY", html)
        self.assertNotIn("saved-report", html)
        self.assertNotIn("analysis-window", html)
        self.assertIn("Transponder code", html)
        self.assertIn("1200", html)
        self.assertEqual(set(publication.SOURCE_LABELS), public_events.POSITION_SOURCES)

    def test_text_and_reference_urls_are_escaped_and_identifiers_are_copyable(self):
        snapshot, identifier = fixture()
        html = publication.event_card(snapshot, identifier)
        parsed = CardParser(html)
        text = "".join(parsed.text)
        self.assertIn('Bell & "example" helicopter', text)
        self.assertIn("Bell &amp; &quot;example&quot; helicopter", html)
        self.assertIn(snapshot["events"][0]["evidence_version"], text)
        self.assertIn(identifier, text)
        self.assertIn("Record revision: 1", text)
        self.assertIn("Tail number (registration)", text)
        self.assertIn("N12345", text)
        self.assertIn("Wikipedia: aircraft type search", text)
        link = next(attrs for tag, attrs in parsed.tags if tag == "a")
        self.assertEqual(link["href"], snapshot["events"][0]["references"][0]["url"])
        self.assertIn("&amp;view=photos", html)
        self.assertFalse(any(tag in {"script", "iframe"} for tag, _attrs in parsed.tags))

    def test_published_card_uses_publication_dates_without_moving_observation(self):
        snapshot, identifier = fixture(published=True)
        html = publication.event_card(snapshot, identifier)
        self.assertIn('<th scope="row">Post date</th>', html)
        self.assertIn('datetime="2026-10-04T12:00:00Z"', html)
        self.assertIn('<th scope="row">Last revised</th>', html)
        self.assertIn('datetime="2026-10-04T15:00:00Z"', html)
        self.assertIn(f'datetime="{START}"', html)
        self.assertNotIn("Unpublished review", html)

    def test_synthetic_notice_and_batch_output_do_not_mutate_the_snapshot(self):
        snapshot, identifier = fixture(synthetic=True)
        original = deepcopy(snapshot)
        cards = publication.event_cards(snapshot)
        self.assertEqual(cards, {identifier: publication.event_card(snapshot, identifier)})
        self.assertIn("Invented observation", cards[identifier])
        self.assertEqual(snapshot, original)

    def test_invalid_snapshot_or_unknown_event_never_produces_a_card(self):
        snapshot, identifier = fixture()
        for location, field, value in (
            ("event", "receiver_lat", 12.345678),
            ("event", "title", "<script>bad</script>"),
            ("snapshot", "private_token", "hidden"),
        ):
            changed = deepcopy(snapshot)
            target = changed["events"][0] if location == "event" else changed
            target[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                publication.event_card(changed, identifier)
        with self.assertRaises(ValueError):
            publication.event_card(snapshot, "not-in-the-snapshot")

    def test_draft_payload_backdates_only_the_post_date_without_release_or_email(self):
        snapshot, identifier = fixture()
        bundle = publication.draft_request(snapshot, identifier, prepared_at=NOW)
        request = bundle["request"]
        post = request["body"]["posts"][0]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/ghost/api/admin/posts/")
        self.assertEqual(post["published_at"], START)
        self.assertEqual(post["status"], "draft")
        self.assertFalse(post["email_only"])
        self.assertNotIn("created_at", post)
        self.assertNotIn("updated_at", post)
        self.assertNotIn("newsletter", post)
        self.assertNotIn("email_segment", request["path"])
        self.assertIsNone(bundle["audit"]["imported_at"])
        self.assertIsNone(bundle["audit"]["first_released_at"])
        self.assertEqual(bundle["audit"]["prepared_at"], NOW)
        lexical = json.loads(post["lexical"])
        self.assertEqual(lexical["root"]["children"][0]["type"], "html")
        self.assertIn("Recorded data for N12345", lexical["root"]["children"][0]["html"])
        self.assertNotIn("Unpublished review", lexical["root"]["children"][0]["html"])
        previously_published, published_id = fixture(published=True)
        prepared = publication.draft_request(previously_published, published_id, prepared_at=NOW)
        prepared_post = prepared["request"]["body"]["posts"][0]
        self.assertEqual(prepared_post["published_at"], START)
        prepared_html = json.loads(prepared_post["lexical"])["root"]["children"][0]["html"]
        self.assertNotIn('<th scope="row">Post date</th>', prepared_html)
        self.assertNotIn(previously_published["events"][0]["published_at"], prepared_html)
        with self.assertRaises(ValueError):
            publication.draft_request(snapshot, identifier, prepared_at=START)

    def test_date_only_update_preserves_post_state_relations_and_collision_token(self):
        snapshot, identifier = fixture()
        current = {
            "id": "0123456789abcdef01234567",
            "slug": "example-observation-3-oct-2026",
            "status": "published",
            "visibility": "public",
            "updated_at": "2026-10-04T20:01:00Z",
            "tags": [{"name": "Preserve this tag"}],
        }
        request = publication.date_update_request(snapshot, identifier, current)
        self.assertEqual(request["method"], "PUT")
        self.assertEqual(
            request["body"]["posts"],
            [{"id": current["id"], "updated_at": current["updated_at"], "published_at": START}],
        )
        self.assertNotIn("?", request["path"])
        for accepted in ("example-ghost-id-01", "example_ghost_id_01"):
            changed = publication.date_update_request(snapshot, identifier, {**current, "id": accepted})
            self.assertEqual(changed["body"]["posts"][0]["id"], accepted)
        for invalid in ("é" * 24, "bad/id/" * 3, "too-short", "a" * 81, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                publication.date_update_request(snapshot, identifier, {**current, "id": invalid})
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                public_events.ghost_post_id(invalid)
        for change in ({"slug": "wrong-post"}, {"status": "scheduled"}, {"newsletter": {"id": "mail"}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                publication.date_update_request(snapshot, identifier, {**current, **change})

    def test_audit_keeps_actual_import_release_and_revision_times_separate(self):
        snapshot, identifier = fixture()
        audit = publication.draft_request(snapshot, identifier, prepared_at=NOW)["audit"]
        imported = {
            "id": "0123456789abcdef01234567",
            "slug": "example-observation-3-oct-2026",
            "status": "draft",
            "visibility": "public",
            "created_at": "2026-10-04T20:01:00Z",
            "updated_at": "2026-10-04T20:01:00Z",
            "published_at": START,
        }
        audit = publication.record_response(
            audit, imported, operation="import", completed_at="2026-10-04T20:01:01Z"
        )
        self.assertEqual(audit["imported_at"], "2026-10-04T20:01:01Z")
        self.assertIsNone(audit["first_released_at"])
        delayed_import_audit = {
            **audit,
            "imported_at": "2026-10-04T20:05:00Z",
            "last_changed_at": "2026-10-04T20:05:00Z",
        }
        with self.assertRaisesRegex(ValueError, "already recorded"):
            publication.record_response(
                delayed_import_audit,
                {**imported, "status": "published", "updated_at": "2026-10-04T20:02:00Z"},
                operation="release",
                completed_at="2026-10-04T20:02:01Z",
            )
        released = {**imported, "status": "published", "updated_at": "2026-10-04T21:00:00Z"}
        audit = publication.record_response(
            audit, released, operation="release", completed_at="2026-10-04T21:00:01Z"
        )
        self.assertEqual(audit["first_released_at"], "2026-10-04T21:00:01Z")
        revised = {**released, "updated_at": "2026-10-05T10:00:00Z"}
        audit = publication.record_response(
            audit, revised, operation="revise", completed_at="2026-10-05T10:00:01Z"
        )
        self.assertEqual(audit["observed_start"], START)
        self.assertEqual(audit["imported_at"], "2026-10-04T20:01:01Z")
        self.assertEqual(audit["first_released_at"], "2026-10-04T21:00:01Z")
        self.assertEqual(audit["last_changed_at"], "2026-10-05T10:00:01Z")
        self.assertEqual(len(audit["history"]), 3)
        self.assertEqual(
            publication.record_response(
                audit, revised, operation="revise", completed_at="2026-10-05T10:00:02Z"
            ),
            audit,
        )
        for change in (
            {"published_at": NOW},
            {"email": {"id": "sent"}},
            {"id": "another-post-identifier"},
            {"created_at": "2026-10-04T20:02:00Z"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                publication.record_response(
                    audit, {**revised, **change}, operation="revise", completed_at="2026-10-05T10:00:03Z"
                )
        synthetic = deepcopy(audit)
        synthetic["synthetic"] = True
        with self.assertRaises(ValueError):
            publication.record_response(
                synthetic, revised, operation="release", completed_at="2026-10-05T10:00:03Z"
            )

    def test_ghost_date_precision_keeps_original_observation_and_accepts_real_response(self):
        observed = "2026-10-03T23:55:00.123456Z"
        snapshot, identifier = fixture(observed_start=observed)
        bundle = publication.draft_request(snapshot, identifier, prepared_at=NOW)
        expected = "2026-10-03T23:55:00Z"
        self.assertEqual(bundle["request"]["body"]["posts"][0]["published_at"], expected)
        self.assertEqual(bundle["audit"]["observed_start"], observed)
        self.assertIn(f'datetime="{observed}"', publication.event_card(snapshot, identifier))
        imported = {
            "id": "0123456789abcdef01234567",
            "slug": "example-observation-3-oct-2026",
            "status": "draft",
            "visibility": "public",
            "created_at": "2026-10-04T20:01:00.001Z",
            "updated_at": "2026-10-04T20:01:00.001Z",
            "published_at": "2026-10-03T23:55:00.000Z",
        }
        audit = publication.record_response(
            bundle["audit"], imported, operation="import", completed_at="2026-10-04T20:01:00.123456Z"
        )
        self.assertEqual(audit["observed_start"], observed)
        self.assertEqual(audit["imported_at"], "2026-10-04T20:01:00.123456Z")
        request = publication.date_update_request(snapshot, identifier, imported)
        self.assertEqual(request["body"]["posts"][0]["published_at"], expected)
        self.assertEqual(
            publication.ghost_post_date("2026-10-03T23:59:59.999999Z"),
            "2026-10-03T23:59:59Z",
            "Truncation must not move an event into the next UTC day",
        )

    def test_cli_writes_private_review_card_and_preserves_inputs_on_error(self):
        snapshot, identifier = fixture()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path / "snapshot.json"
            output = path / "card.html"
            source.write_text(json.dumps(snapshot))
            command = [
                sys.executable,
                str(ROOT / "tools/export_publication_card.py"),
                "--snapshot",
                str(source),
                "--event-id",
                identifier,
                "--output",
                str(output),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("nothing was published", result.stdout)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            before = output.read_text()
            invalid = command.copy()
            invalid[invalid.index("--event-id") + 1] = "absent"
            self.assertNotEqual(subprocess.run(invalid, capture_output=True, check=False).returncode, 0)
            self.assertEqual(output.read_text(), before)
            command[-1] = str(source)
            self.assertNotEqual(subprocess.run(command, capture_output=True, check=False).returncode, 0)
            self.assertEqual(json.loads(source.read_text()), snapshot)


def public_snapshot(evidence, path="/stories/episode-observation-3-oct-2026/"):
    ledger = public_events.new_ledger("2026-09-05")
    event_id = public_events.put_event(
        ledger,
        "PRIVATE-EPISODE-KEY",
        evidence,
        {"title": "Saved reports", "summary": "Saved reports for review.", "article_path": path},
        at=NOW,
    )
    return public_events.export_snapshot(ledger, mode="review", as_of=NOW), event_id


class PublicOutputCardTests(unittest.TestCase):
    def test_episode_card_separates_peak_time_rule_and_window_with_distinct_labels(self):
        evidence = {
            "schema_version": 1,
            "kind": "rapid_descent",
            "observation": {"start_utc": START, "end_utc": END},
            "aircraft": {"registration": "N12345", "type_code": "BE20"},
            "received": {
                "source": "adsb_icao",
                "readings": [{"field": "baro_rate", "value": -7600}],
                "saved_readings_at_utc": READING,
                "squawk": "1200",
                "emergency_status": "none",
            },
            "maneuver": {
                "confirmed": True,
                "start_utc": WINDOW_START,
                "end_utc": WINDOW_END,
                "peak_at_utc": READING,
                "duration_seconds": 300,
                "peak_rate_fpm": -7600,
                "threshold_fpm": 4500,
                "qualifying_duration_seconds": 60,
                "qualifying_sample_count": 7,
                "footprint_nm": 0.25,
            },
            "links": [],
        }
        snapshot, identifier = public_snapshot(evidence)
        html = publication.event_card(snapshot, identifier)
        text = "".join(CardParser(html).text)
        self.assertIn("Peak report at", text)
        self.assertIn("Detection rule. This is a site setting, not a measurement.", text)
        self.assertIn("Detection threshold (magnitude)", text)
        self.assertIn("Duration of consecutive reports above the detection threshold", text)
        self.assertIn("Consecutive reports above the detection threshold", text)
        self.assertIn("NM", text)
        self.assertNotIn(" nm", text)
        self.assertNotIn("Reported emergency status", text)
        self.assertIn("negative values are descents", text)
        self.assertIn("not an emergency or distress determination", text)

    def test_archived_labels_render_as_last_archived_label_without_position_time(self):
        evidence = {
            "schema_version": 1,
            "kind": "watch_type",
            "observation": {"start_utc": START, "end_utc": END},
            "aircraft": {"registration": "N12345", "callsign": "RCH2023", "type_code": "C5M"},
            "received": {
                "source": "adsb_icao",
                "readings": [{"field": "archive_alt", "value": 31000}],
                "saved_readings_at_utc": READING,
                "squawk": "4102",
            },
            "links": [],
        }
        snapshot, identifier = public_snapshot(evidence)
        html = publication.event_card(snapshot, identifier)
        self.assertIn("Last archived label; its own time was not retained.", html)
        self.assertEqual(html.count('datetime="' + READING + '"'), 1)
        self.assertIn("Archive timestamps do not establish individual message freshness.", html)
        evidence["received"]["label_at_utc"] = WINDOW_START
        evidence["received"]["label_provenance"] = "archived_label"
        snapshot, identifier = public_snapshot(evidence)
        html = publication.event_card(snapshot, identifier)
        self.assertIn("Last archived label at", html)
        self.assertIn('datetime="' + WINDOW_START + '"', html)
        evidence["received"]["squawk"] = "0000"
        snapshot, identifier = public_snapshot(evidence)
        self.assertNotIn(">0000<", publication.event_card(snapshot, identifier))

    def test_other_aircraft_and_watchlist_source_render_escaped(self):
        commit = "c" * 40
        evidence = {
            "schema_version": 1,
            "kind": "formation_flight",
            "observation": {"start_utc": START, "end_utc": END},
            "aircraft": {"registration": "N12345", "icao_address": "A12345", "type_code": "C130"},
            "received": {"source": "adsb_icao", "readings": []},
            "participants": [
                {
                    "icao_address": "A22222",
                    "role": "wingman",
                    "registration": "N22222",
                    "type_description": 'Lockheed & "Hercules"',
                    "source": "adsb_icao",
                    "measurements": {"min_separation_nm": 0.3},
                }
            ],
            "watchlist": {
                "source_name": "Plane Alert DB",
                "association_label": 'Example & "Wing" Aviation',
                "category": "Specialized military aircraft",
                "list_category": "Oxcart",
                "tags": ["Hercules", "Special & Rare"],
                "group": "military",
                "description": "C-130 Hercules",
                "link": "https://www.example.com/aircraft/n12345?a=1&b=2",
                "source_commit": commit,
                "attribution": public_events.WATCHLIST_ATTRIBUTION,
                "licenses": [dict(item) for item in public_events.watchlists.LICENSES],
                "claim_scope": public_events.WATCHLIST_CLAIM_SCOPE,
            },
            "links": [],
        }
        snapshot, identifier = public_snapshot(evidence)
        html = publication.event_card(snapshot, identifier)
        parsed = CardParser(html)
        text = "".join(parsed.text)
        self.assertIn("Other aircraft", text)
        self.assertIn("Wingman", text)
        self.assertIn("ICAO address A22222", text)
        self.assertIn('Lockheed & "Hercules"', text)
        self.assertIn("Lockheed &amp; &quot;Hercules&quot;", html)
        self.assertIn("Closest horizontal separation between the aircraft: 0.3", text)
        self.assertIn("Watchlist association", text)
        # The Plane Alert DB entry is named, with its own category, tags, group and link.
        for row in (
            'Example & "Wing" Aviation',
            "Oxcart",
            "Hercules, Special & Rare",
            "military",
            "C-130 Hercules",
            "Specialized military aircraft",
        ):
            self.assertIn(row, text)
        self.assertIn("Example &amp; &quot;Wing&quot; Aviation", html)
        self.assertIn("Hercules, Special &amp; Rare", html)
        self.assertIn(
            '<a href="https://www.example.com/aircraft/n12345?a=1&amp;b=2" rel="external noopener noreferrer">',
            html,
        )
        self.assertIn("Open Database License 1.0; Database Contents License 1.0", text)
        self.assertIn(public_events.WATCHLIST_ATTRIBUTION, text)
        self.assertIn(public_events.WATCHLIST_SCOPE, text)
        self.assertTrue(all(attrs.get("scope") == "row" for tag, attrs in parsed.tags if tag == "th"))


def story_event(**story_changes):
    """A reviewed event with a story, built through the public projection."""
    from test_public_events import editorial, packet, routing, story

    told = story(**story_changes)
    ledger = public_events.new_ledger("2026-09-20")
    identifier = public_events.put_event(
        ledger,
        "story-episode",
        packet(),
        editorial(title=told["headline"], summary=told["dek"], story=told, context=routing()),
        at=NOW,
    )
    snapshot = public_events.export_snapshot(ledger, mode="review", as_of=NOW)
    return snapshot, identifier, snapshot["events"][0]


class StoryArticleTests(unittest.TestCase):
    def test_the_prose_block_leads_the_article_and_links_the_first_glossary_terms(self):
        paragraphs = [
            "The ADS-B reports & MLAT positions show a ground speed of 70 knots.",
            "A second ADS-B mention and another ground speed stay plain, as does geometric altitude data.",
            "The aircraft database gives the type; a second aircraft database mention stays plain.",
        ]
        snapshot, identifier, event = story_event(paragraphs=paragraphs)
        with patch.dict(os.environ, {"SITE_ORIGIN": "https://site.example"}):
            html = publication.article_html(event, display_review=False)
        self.assertTrue(html.startswith('<section class="story-prose" data-story-version="'))
        self.assertLess(html.index("story-prose"), html.index("observation-record"))
        self.assertEqual(html.count('href="https://site.example/field-guide/#ads-b"'), 1)
        self.assertEqual(html.count('href="https://site.example/field-guide/#ground-speed"'), 1)
        self.assertIn('<a href="https://site.example/field-guide/#mlat">MLAT</a>', html)
        self.assertIn(
            '<a href="https://site.example/field-guide/#geometric-altitude">geometric altitude</a>', html
        )
        # The same terms the static site's renderer links.
        self.assertEqual(html.count('href="https://site.example/field-guide/#type-match"'), 1)
        self.assertIn('<a href="https://site.example/field-guide/#type-match">aircraft database</a>', html)
        self.assertIn("reports &amp; ", html)
        marker = re.search(r'data-story-version="([0-9a-f]{64})"', html).group(1)
        self.assertNotEqual(
            marker,
            re.search(r'data-story-version="([0-9a-f]{64})"', publication.story_html(story_event()[2])).group(
                1
            ),
        )

    def test_about_the_type_lists_sourced_facts_and_links_the_type_hub(self):
        _snapshot, _identifier, event = story_event()
        html = publication.story_html(event)
        self.assertIn("<h2>About the Bell 407</h2>", html)
        self.assertIn("<li>A sourced type fact.</li>", html)
        # Without a configured origin, owned links name the example origin.
        self.assertIn('href="https://example.com/aircraft/bell-407/"', html)
        self.assertIn(
            'href="https://www.bellflight.com/products/bell-407" rel="external noopener noreferrer"', html
        )
        self.assertNotIn(
            "About the", publication.story_html(story_event(facts={"id.registration": "N12345"})[2])
        )

    def test_a_source_cited_under_two_fact_ids_is_listed_once(self):
        brochure = {
            "publisher": "De Havilland",
            "title": "Dash 8-400 brochure",
            "url": "https://www.example.com/dash8-400",
        }
        other = {
            "id": "src.limits.DH8D",
            "publisher": "Example Aviation Authority",
            "title": "Type certificate data sheet",
            "url": "https://www.example.com/tcds",
        }
        sources = [{"id": "src.DH8D.1", **brochure}, other, {"id": "src.DH8D.2", **brochure}]
        _snapshot, _identifier, event = story_event(
            facts={"id.registration": "N12345", "tp.B407.1": "A sourced type fact."}, sources=sources
        )
        html = publication.story_html(event)
        self.assertEqual(html.count("Dash 8-400 brochure</a>, De Havilland"), 1)
        # The first citation keeps its place, before the other source.
        self.assertLess(html.index("Dash 8-400 brochure"), html.index("Type certificate data sheet"))
        self.assertEqual(html.count("<li><a href="), 2)

    def test_the_card_uses_the_new_headings_and_neighbor_scopes(self):
        snapshot, identifier, event = story_event()
        card = publication.event_card(snapshot, identifier)
        self.assertIn("<h2>Recorded data for N12345</h2>", card)
        self.assertIn("<h3>Measurements</h3>", card)
        self.assertIn("<h3>What this record cannot show</h3>", card)
        self.assertIn("<h3>Look up N12345</h3>", card)
        self.assertNotIn("Recorded observation", card)
        self.assertEqual(
            publication.measurement_scope({"scope": "surrounding-reports", "at_utc": READING}),
            f'Neighboring saved report at <time datetime="{READING}">3 Oct 2026, 23:58:17 UTC</time>.',
        )
        self.assertIn(
            "leaving out the outlying value",
            publication.measurement_scope(
                {"scope": "surrounding-reports", "start_utc": START, "end_utc": END}
            ),
        )

    def test_draft_requests_send_the_slug_segment_meta_fields_and_topic_tag(self):
        snapshot, identifier, event = story_event()
        post = publication.draft_request(snapshot, identifier, prepared_at=NOW)["request"]["body"]["posts"][0]
        self.assertEqual(post["slug"], "helicopter-circuits-3-oct-2026")
        self.assertEqual(
            (post["title"], post["custom_excerpt"]), (event["story"]["headline"], event["story"]["dek"])
        )
        self.assertEqual(post["meta_title"], "Bell 407 N12345")
        self.assertEqual(
            (post["og_title"], post["twitter_description"]),
            (event["story"]["headline"], event["story"]["dek"]),
        )
        self.assertEqual(post["tags"], [{"name": "Flight shapes", "slug": "flight-shapes"}])
        with patch.object(
            publication, "_topic_titles", return_value={"flight-shapes": "Flight shapes and loops"}
        ):
            self.assertEqual(
                publication.topic_tag("flight-shapes"),
                {"name": "Flight shapes and loops", "slug": "flight-shapes"},
            )

    def test_generated_prose_follows_updates_while_an_owner_edit_or_removal_stays(self):
        snapshot, identifier, event = story_event()
        html = publication.article_html(event, display_review=False)
        post = {"lexical": json.dumps({"root": {"children": [{"type": "html", "version": 1, "html": html}]}})}
        digest = publication.owned_digests(html)["story_sha256"]
        _s, _i, revised = story_event(
            paragraphs=["A revised first paragraph.", "A revised second paragraph."]
        )
        revised = {**revised, "id": event["id"], "evidence_version": event["evidence_version"]}
        updated = json.loads(publication.automatic_article_update(revised, post, event, story_digest=digest))
        updated_html = updated["root"]["children"][0]["html"]
        self.assertIn("A revised first paragraph.", updated_html)
        self.assertFalse(
            publication.story_edited(
                updated_html, publication.owned_digests(updated_html)["story_sha256"], revised
            )
        )
        owner = html.replace("First paragraph of the story.", "The owner's own words.")
        edited = {
            "lexical": json.dumps({"root": {"children": [{"type": "html", "version": 1, "html": owner}]}})
        }
        kept = json.loads(publication.automatic_article_update(revised, edited, event, story_digest=digest))
        self.assertIn("The owner's own words.", kept["root"]["children"][0]["html"])
        self.assertTrue(publication.story_edited(owner, digest, event))
        removed_html = owner.replace(
            next(block for kind, block in PublishedBlocks(owner).blocks if kind == "story"), ""
        )
        removed = {
            "lexical": json.dumps(
                {"root": {"children": [{"type": "html", "version": 1, "html": removed_html}]}}
            )
        }
        stays_removed = json.loads(
            publication.automatic_article_update(revised, removed, event, story_digest=digest)
        )
        self.assertNotIn("story-prose", stays_removed["root"]["children"][0]["html"])
        self.assertTrue(publication.story_edited(removed_html, digest, event))
        # A post published before prose blocks existed gains one above its card.
        legacy_event = {key: value for key, value in event.items() if key not in {"story", "context"}}
        legacy = {
            "lexical": json.dumps(
                {
                    "root": {
                        "children": [
                            {
                                "type": "html",
                                "version": 1,
                                "html": publication.article_html(legacy_event, display_review=False),
                            }
                        ]
                    }
                }
            )
        }
        gained = json.loads(publication.automatic_article_update(revised, legacy, legacy_event))
        self.assertTrue(gained["root"]["children"][0]["html"].startswith('<section class="story-prose"'))


if __name__ == "__main__":
    unittest.main()
