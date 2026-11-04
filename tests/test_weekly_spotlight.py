"""Weekly picks retain episode identity, researched claims, and publication checks."""

import json
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_public_events import editorial

from wtf_adsb_publisher import autopilot, curation, episode_evidence, publication, selection, stories
from wtf_adsb_publisher import public_events as public
from wtf_adsb_publisher import weekly_spotlight as weekly

NOW = "2026-10-05T15:00:00Z"


def research(event, as_of=NOW):
    return {
        "week_start": weekly.week_start(event["observed_start"]).isoformat(),
        "evidence_version": event["evidence_version"],
        "reason": "The distinctive aircraft type merits a closer look at its documented background.",
        "sections": [
            {
                "heading": "Aircraft background",
                "scope": "type_background",
                "text": "This synthetic source describes an aircraft type and its documented design history. It establishes no mission or occupant for the observed flight.",
                "sources": ["manufacturer"],
            },
            {
                "heading": "What the shape can tell us",
                "scope": "interpretation",
                "text": "This synthetic research explains why similar flight shapes can have different purposes. The track alone does not identify what the crew was doing.",
                "sources": ["guide"],
            },
        ],
        "sources": [
            {
                "id": "manufacturer",
                "title": "Aircraft background",
                "url": "https://example.org/aircraft",
                "publisher": "Example manufacturer",
                "retrieved_at": as_of,
                "applies_to": "Type background only; no individual airframe claim.",
                "primary": True,
            },
            {
                "id": "guide",
                "title": "Reading a track",
                "url": "https://example.net/guide",
                "publisher": "Example guide",
                "retrieved_at": as_of,
                "applies_to": "General context for this synthetic example.",
                "primary": True,
            },
        ],
        "limitations": ["No flight purpose or occupants have been independently established."],
    }


def finding(start="2026-10-03T23:55:00Z", end="2026-10-04T00:05:00Z"):
    first, last = (datetime.fromisoformat(stamp).timestamp() for stamp in (start, end))
    return {
        "kind": "watch_type",
        "hex": "abc123",
        "first_seen": first,
        "last_seen": last,
        "hits": 40,
        "value": None,
        "unit": "",
        "evidence": {
            "watchlist": {},
            "aircraft": {
                "hex": "abc123",
                "r": "N12345",
                "t": "E6",
                "desc": "Boeing E-6B Mercury",
                "type": "adsb_icao",
                "gs": 70,
            },
            "observed_at": first,
            "pattern": {
                "shape_verified": True,
                "quality_review_required": False,
                "observed_sources": ["adsb_icao"],
                "started_at": first,
                "ended_at": last,
                "duration_seconds": last - first,
                "closed_loops": 3,
                "sample_count": 40,
            },
        },
    }


def fixture(start="2026-10-03T23:55:00Z", end="2026-10-04T00:05:00Z", as_of=NOW):
    ledger = public.new_ledger(start[:10])
    source = finding(start, end)
    evidence = stories.evidence_packet(
        source, include_track=False, observation_interval=(source["first_seen"], source["last_seen"])
    )
    metadata = editorial(
        weekly_source=source,
        title="Fixture E-6 type match",
        summary="A synthetic distinctive aircraft match.",
        secondary_labels=[],
    )
    base = public._projection(evidence, metadata)
    metadata["weekly_spotlight"] = research(base, as_of)
    identifier = public.put_event(ledger, "same-episode", evidence, metadata, at=as_of)
    public.set_review_state(ledger, identifier, "approved", at=as_of)
    review = public.export_snapshot(ledger, mode="review", as_of=as_of)
    event = review["events"][0]
    inventory = {
        "complete": True,
        "total": 1,
        "as_of_utc": as_of,
        "posts": [
            {
                "id": metadata["ghost_id"],
                "slug": public.article_slug(metadata["article_path"]),
                "status": "published",
                "visibility": "public",
                "published_at": as_of,
                "updated_at": as_of,
                "html": publication.article_html(event, display_review=False),
                "tags": [{"name": "Weekly spotlight"}, {"name": "#weekly-" + weekly.binding(event)}],
            }
        ],
    }
    return ledger, identifier, review, inventory


def legacy_rate_fixture(kind="rapid_descent"):
    source = finding()
    rate = 6000 if kind == "rapid_climb" else -6000
    source.update(kind=kind, value=rate, unit="ft/min")
    source["evidence"]["maneuver"] = {
        "confirmed": True,
        "quality_review_required": False,
        "quality_flags": [],
        "duration_seconds": 600,
        "sample_count": 40,
        "peak_rate_fpm": rate,
        "median_rate_fpm": rate * 11 / 12,
        "threshold_fpm": 4500,
    }
    # The detector saved its threshold, but the old public allowlist omitted it.
    metrics = {key: value for key, value in public.EPISODE_MEASUREMENTS.items() if key != "threshold_fpm"}
    metrics["median_rate_fpm"] = ("Median signed vertical rate", "ft/min")
    with (
        patch(__name__ + ".finding", return_value=source),
        patch.object(curation, "CONTEXT_KINDS", curation.CONTEXT_KINDS - {"rapid_climb"}),
        patch.object(selection, "assess", return_value={"story_eligible": True, "review_required": False}),
        patch.object(episode_evidence, "METRICS", metrics),
        patch.object(public, "EPISODE_MEASUREMENTS", metrics),
    ):
        return fixture()


def export(ledger, inventory):
    return public.export_snapshot(ledger, mode="published", as_of=NOW, inventory=inventory)


def weekly_path(day):
    """The story path of one week's fixture pick, observed on `day`."""
    from wtf_adsb_publisher import story_urls

    return story_urls.article_path(
        "boeing-e-6b-mercury-fixture-" + story_urls.date_segment(f"{day}T12:00:00Z")
    )


def archive_fixture(count=12):
    start = weekly.week_start(NOW) - timedelta(weeks=count)
    ledger = public.new_ledger(start.isoformat())
    for index in range(count):
        day = start + timedelta(weeks=index)
        source = finding(f"{day}T12:00:00Z", f"{day}T12:10:00Z")
        evidence = stories.evidence_packet(
            source, include_track=False, observation_interval=(source["first_seen"], source["last_seen"])
        )
        metadata = editorial(
            path=weekly_path(day),
            weekly_source=source,
            title="Fixture E-6 type match",
            summary="A synthetic distinctive aircraft match.",
            secondary_labels=[],
        )
        metadata["weekly_spotlight"] = research(public._projection(evidence, metadata))
        public.put_event(ledger, str(day), evidence, metadata, at=NOW)
    return public.export_snapshot(ledger, mode="review", as_of=NOW)


class WeeklyTests(unittest.TestCase):
    def test_oscillations_cannot_become_weekly_features_even_with_a_keep_vote(self):
        source = finding()
        source.update(kind="altitude_oscillation", value=20, unit="reversals", verdict="keep")
        source["evidence"]["maneuver"] = {
            "confirmed": True,
            "quality_review_required": False,
            "quality_flags": [],
            "duration_seconds": 600,
            "sample_count": 40,
        }
        with patch(__name__ + ".finding", return_value=source):
            with self.assertRaisesRegex(ValueError, "independently interesting primary finding"):
                fixture()

    def test_calendar_boundaries_and_explicit_offsets(self):
        for stamp, expected in (
            ("2026-10-04T23:59:59Z", ("2026-09-21", "2026-09-28")),
            ("2026-10-05T00:00:00Z", ("2026-09-28", "2026-10-05")),
            ("2026-10-04T20:00:00-04:00", ("2026-09-28", "2026-10-05")),
            ("2027-01-01T00:00:00Z", ("2026-12-21", "2026-12-28")),
        ):
            with self.subTest(stamp=stamp):
                self.assertEqual(weekly.previous_week(stamp), expected)

    def test_nomination_uses_interest_not_daily_diversity_and_stable_ties(self):
        def candidate(key, stamp, interest=40, **extra):
            return {
                "episode_key": key,
                "finding_id": key,
                "first_seen": datetime.fromisoformat(stamp).timestamp(),
                "eligible": True,
                "exceptional": False,
                "interest": interest,
                "evidence": 30,
                "depth": 10,
                "adjusted_score": 1,
                **extra,
            }

        rows = [
            candidate("b", "2026-09-30T00:00:00+00:00"),
            candidate("a", "2026-09-30T00:00:00+00:00"),
            candidate("outside", "2026-10-05T00:00:00+00:00", 45),
            candidate("bad", "2026-09-30T00:00:00+00:00", 45, eligible=False),
        ]
        report = {"version": curation.VERSION, "selected": rows, "priority_review": []}
        before = deepcopy(report)
        result = weekly.nominate(report, NOW)
        self.assertEqual(result["nominee"]["episode_key"], "a")
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(report, before)
        report["selected"].reverse()
        self.assertEqual(result["shortlist"], weekly.nominate(report, NOW)["shortlist"])
        with self.assertRaisesRegex(ValueError, "Rerun daily curation"):
            weekly.nominate({**report, "version": curation.VERSION - 1}, NOW)

    def test_research_completeness_source_privacy_and_claim_bindings(self):
        _, _, review, _ = fixture()
        event = review["events"][0]
        valid = event["weekly_spotlight"]
        mutations = [
            lambda r: r.update(evidence_version="0" * 64),
            lambda r: r.update(week_start="2026-09-29"),
            lambda r: r.update(sources=r["sources"][:1]),
            lambda r: r["sources"][0].update(url="https://secret.ts.net/aircraft"),
            lambda r: r["sources"][0].update(retrieved_at="2026-10-06T00:00:00Z"),
            lambda r: r["sections"][0].update(sources=["missing"]),
            lambda r: r["sections"][0].update(text="<script>bad</script>"),
            lambda r: r.update(receiver_lat=1),
            lambda r: r.update(limitations=[]),
            lambda r: r.update(reason="A P-51 match leads to a closer look at its documented background."),
        ]
        for change in mutations:
            raw = deepcopy(valid)
            change(raw)
            with self.subTest(change=change), self.assertRaises(ValueError):
                weekly.validate_research(raw, event, as_of=NOW)

    def test_research_must_belong_to_a_completed_week(self):
        _, _, review, _ = fixture()
        event = review["events"][0]
        with self.assertRaises(ValueError):
            weekly.validate_research(event["weekly_spotlight"], event, as_of="2026-10-04T23:59:59Z")

    def test_research_cannot_certify_an_unresolved_or_rejected_finding(self):
        ledger, _, review, _ = fixture()
        record = ledger["records"][0]
        self.assertNotIn("weekly_source", json.dumps(review))
        self.assertNotIn("quality_review_required", json.dumps(review))
        for change in (
            lambda source: source["evidence"]["aircraft"].update(t="C172"),
            lambda source: source.update(kind="circling"),
            lambda source: source.update(verdict="routine"),
            lambda source: source.update(verdict="bad_data"),
            lambda source: source["evidence"]["aircraft"].update(gs=90),
        ):
            metadata = deepcopy(record["editorial"])
            source = metadata["weekly_source"]
            source.update(verdict="keep", selection={"story_eligible": True, "review_required": False})
            change(source)
            with self.subTest(change=change), self.assertRaises(ValueError):
                public.put_event(ledger, "same-episode", record["evidence"], metadata, at=NOW)

    def test_old_routine_weekly_dossiers_can_be_withdrawn_or_revised_but_not_reapproved(self):
        # Build a dossier accepted under the old policy, without bypassing its
        # evidence checks. Its saved ledger must remain recoverable afterward.
        source = finding()
        source.update(kind="circling", value=3, unit="loops")
        with (
            patch(__name__ + ".finding", return_value=source),
            patch.object(curation, "CONTEXT_KINDS", frozenset()),
        ):
            ledger, identifier, _, _ = fixture()
        with self.assertRaisesRegex(ValueError, "independently interesting"):
            public.set_review_state(ledger, identifier, "approved", at=NOW)
        with self.assertRaisesRegex(ValueError, "independently interesting"):
            public.export_snapshot(ledger, mode="review", as_of=NOW)
        revised = deepcopy(ledger)
        record = revised["records"][0]
        metadata = {
            key: value
            for key, value in record["editorial"].items()
            if key not in {"weekly_spotlight", "weekly_source"}
        }
        self.assertEqual(
            public.put_event(revised, record["episode_key"], record["evidence"], metadata, at=NOW), identifier
        )
        self.assertNotIn(
            "weekly_spotlight", public.export_snapshot(revised, mode="review", as_of=NOW)["events"][0]
        )
        public.set_review_state(ledger, identifier, "withdrawn", at=NOW)
        self.assertFalse(public.export_snapshot(ledger, mode="review", as_of=NOW)["events"])

    def test_legacy_rate_research_remains_recoverable_after_evidence_rules_change(self):
        ledger, identifier, _, inventory = legacy_rate_fixture()
        record = ledger["records"][0]
        self.assertEqual(record["editorial"]["weekly_source"]["evidence"]["maneuver"]["threshold_fpm"], 4500)
        self.assertNotIn("threshold_fpm", record["evidence"]["maneuver"])
        public._validate_ledger(ledger)
        with self.assertRaisesRegex(ValueError, "factual review"):
            public.set_review_state(ledger, identifier, "approved", at=NOW)
        with self.assertRaisesRegex(ValueError, "factual review"):
            public.export_snapshot(ledger, mode="review", as_of=NOW)
        # Even an otherwise eligible finding must bind to current source facts
        # before it can be approved or exported again.
        with patch.object(
            selection, "assess", return_value={"story_eligible": True, "review_required": False}
        ):
            with self.assertRaisesRegex(ValueError, "does not match"):
                public.set_review_state(ledger, identifier, "approved", at=NOW)
            with self.assertRaisesRegex(ValueError, "does not match"):
                public.export_snapshot(ledger, mode="review", as_of=NOW)
            with self.assertRaisesRegex(ValueError, "does not match"):
                export(ledger, inventory)
        revised = deepcopy(ledger)
        record = revised["records"][0]
        metadata = {
            k: v for k, v in record["editorial"].items() if k not in {"weekly_spotlight", "weekly_source"}
        }
        public.put_event(revised, record["episode_key"], record["evidence"], metadata, at=NOW)
        public.set_review_state(ledger, identifier, "withdrawn", at=NOW)
        self.assertFalse(public.export_snapshot(ledger, mode="review", as_of=NOW)["events"])

    def test_ineligible_weekly_posts_are_excluded_when_absent_private_draft_or_replaced(self):
        ledger, _, _, inventory = legacy_rate_fixture()
        for state in ("absent", "members", "draft", "replacement"):
            changed = deepcopy(inventory)
            if state == "absent":
                changed.update(posts=[], total=0)
            elif state == "members":
                changed["posts"][0]["visibility"] = "members"
            elif state == "draft":
                changed["posts"][0]["status"] = "draft"
            else:
                changed["posts"][0]["id"] = "replacement-post-id"
            with self.subTest(state=state):
                self.assertFalse(export(ledger, changed)["events"])
        with self.assertRaisesRegex(ValueError, "factual review"):
            export(ledger, inventory)

    def test_autopilot_suspends_legacy_weekly_climb_and_exports_without_it(self):
        ledger, identifier, _, inventory = legacy_rate_fixture("rapid_climb")
        record = ledger["records"][0]
        record["automation"] = {
            "source": deepcopy(record["editorial"]["weekly_source"]),
            "source_kind": "frozen",
            "released_at": NOW,
            "policy_version": 1,
        }
        posts = deepcopy(inventory["posts"])
        posts[0]["tags"].append({"name": "#autopilot-" + identifier})
        client = Mock()
        client.inventory.side_effect = lambda: deepcopy(posts)

        def save(change, current):
            self.assertEqual(current["id"], posts[0]["id"])
            posts[0].update(change)
            return deepcopy(posts[0])

        client.save.side_effect = save
        snapshot, result = autopilot.publish(ledger, client, persist=lambda value: None, at=NOW)
        self.assertEqual(result["suspended"], 1)
        self.assertEqual(posts[0]["status"], "draft")
        self.assertFalse(snapshot["events"])
        self.assertTrue(record["automation"]["suspended"])
        self.assertEqual(record["state"], "approved")
        self.assertEqual(record["id"], identifier)
        self.assertIn("weekly_spotlight", record["editorial"])
        again, result = autopilot.publish(ledger, client, persist=lambda value: None, at=NOW)
        self.assertFalse(again["events"])
        self.assertEqual(result["suspended"], 0)
        self.assertEqual(client.save.call_count, 1)

    def test_published_content_must_contain_the_exact_researched_revision(self):
        ledger, _, _, inventory = fixture()
        event = export(ledger, inventory)["events"][0]
        self.assertIn("weekly_spotlight", event)
        for html in ("", inventory["posts"][0]["html"].replace("documented design", "invented design")):
            changed = deepcopy(inventory)
            changed["posts"][0]["html"] = html
            result = export(ledger, changed)
            self.assertNotIn("weekly_spotlight", result["events"][0])
            self.assertEqual(result["events"][0]["id"], event["id"])
            self.assertEqual(sum(day["total"] or 0 for day in result["days"]), 1)

    def test_pending_research_keeps_only_matching_approved_revision(self):
        ledger, identifier, _, inventory = fixture()
        old = export(ledger, inventory)["events"][0]
        record = ledger["records"][0]
        metadata = deepcopy(record["editorial"])
        metadata["weekly_spotlight"]["reason"] = (
            "A revised editorial reason for choosing this same observation."
        )
        public.put_event(ledger, record["episode_key"], record["evidence"], metadata, at=NOW)
        self.assertEqual(export(ledger, inventory)["events"][0], old)
        public.set_review_state(ledger, identifier, "approved", at=NOW)
        self.assertNotIn("weekly_spotlight", export(ledger, inventory)["events"][0])
        revised = public.export_snapshot(ledger, mode="review", as_of=NOW)["events"][0]
        inventory["posts"][0]["html"] = publication.article_html(revised, display_review=False)
        inventory["posts"][0]["tags"][1]["name"] = "#weekly-" + weekly.binding(revised)
        self.assertIn("weekly_spotlight", export(ledger, inventory)["events"][0])
        self.assertEqual(export(ledger, inventory)["events"][0]["revision"], 2)
        public.set_review_state(ledger, identifier, "withdrawn", at=NOW)
        self.assertFalse(export(ledger, inventory)["events"])

    def test_hidden_duplicate_or_stale_article_content_is_not_a_published_pick(self):
        ledger, _, review, inventory = fixture()
        html = inventory["posts"][0]["html"]
        for changed_html in (
            "<!--" + html + "-->",
            "<template>" + html + "</template>",
            "<div hidden>" + html + "</div>",
            '<div style="color:red; display: none !important">' + html + "</div>",
            '<div style="visibility: hidden">' + html + "</div>",
            html + weekly.research_html(review["events"][0]),
            html.replace("70 <small>", "90 <small>"),
        ):
            changed = deepcopy(inventory)
            changed["posts"][0]["html"] = changed_html
            with self.subTest(html=changed_html[:50]):
                self.assertNotIn("weekly_spotlight", export(ledger, changed)["events"][0])
        for tags in ([], [{"name": "Weekly spotlight"}]):
            changed = deepcopy(inventory)
            changed["posts"][0]["tags"] = tags
            self.assertNotIn("weekly_spotlight", export(ledger, changed)["events"][0])

    def test_revalidation_does_not_consult_live_watchlists(self):
        ledger, identifier, _, inventory = fixture()
        record = ledger["records"][0]
        metadata = deepcopy(record["editorial"])
        del metadata["weekly_source"]["evidence"]["watchlist"]
        with patch(
            "wtf_adsb_publisher.stories.watchlists.lookup",
            side_effect=AssertionError("Unexpected enrichment"),
        ):
            public.put_event(ledger, record["episode_key"], record["evidence"], metadata, at=NOW)
            public.set_review_state(ledger, identifier, "approved", at=NOW)
            export(ledger, inventory)
            public.set_review_state(ledger, identifier, "withdrawn", at=NOW)

    def test_duplicate_picks_and_stale_evidence_are_rejected(self):
        ledger, _, _, _ = fixture()
        record = ledger["records"][0]
        metadata = deepcopy(record["editorial"])
        metadata["article_path"] = "/stories/another-observation-3-oct-2026/"
        public.put_event(ledger, "second-episode", record["evidence"], metadata, at=NOW)
        with self.assertRaises(ValueError):
            public.export_snapshot(ledger, mode="review", as_of=NOW)
        changed = deepcopy(record["evidence"])
        changed["received"]["readings"][0]["value"] = 90
        with self.assertRaises(ValueError):
            public.put_event(ledger, "same-episode", changed, record["editorial"], at=NOW)

    def test_renderer_and_existing_post_update_keep_sources_and_replay(self):
        _, identifier, review, inventory = fixture()
        event = review["events"][0]
        html = publication.article_html(event)
        self.assertIn('href="#weekly-source-manufacturer"', html)
        self.assertIn("Individual aircraft history", weekly.SCOPES.values())
        bundle = publication.draft_request(review, identifier, prepared_at=NOW)
        children = json.loads(bundle["request"]["body"]["posts"][0]["lexical"])["root"]["children"]
        children.append(
            {
                "type": "html",
                "version": 1,
                "html": '<figure data-replay-src="/event-media/example.json">Existing replay</figure>',
            }
        )
        post = {
            **inventory["posts"][0],
            "lexical": json.dumps({"root": {"children": children}}),
            "tags": [{"name": "Existing topic"}],
        }
        before = deepcopy(post)
        updated = publication.research_update_request(review, identifier, post, expected_ghost_id=post["id"])
        payload = updated["request"]["body"]["posts"][0]
        self.assertEqual(post, before)
        self.assertEqual(payload["updated_at"], NOW)
        self.assertNotIn("status", payload)
        self.assertNotIn("published_at", payload)
        self.assertIn("Existing replay", payload["lexical"])
        self.assertEqual(payload["lexical"].count("data-weekly-research="), 1)
        self.assertIn({"name": "Existing topic"}, payload["tags"])
        for _ in range(3):
            post.update(payload)
            payload = publication.research_update_request(
                review, identifier, post, expected_ghost_id=post["id"]
            )["request"]["body"]["posts"][0]
        repeated = json.loads(payload["lexical"])["root"]["children"]
        self.assertEqual(len(repeated), 3)
        self.assertTrue(all(child["html"].strip() for child in repeated))
        with self.assertRaisesRegex(ValueError, "publication binding"):
            publication.research_update_request(review, identifier, post, expected_ghost_id="0" * 24)
        post["lexical"] = post["lexical"].replace("70 <small>", "90 <small>")
        with self.assertRaisesRegex(ValueError, "observation card"):
            publication.research_update_request(review, identifier, post, expected_ghost_id=post["id"])

    def test_native_templates_require_publication_and_guard_week_revision_and_visibility(self):
        ledger, _, review, inventory = fixture()
        with self.assertRaises(ValueError):
            weekly.native_partials(review)
        parts = weekly.native_partials(export(ledger, inventory))
        current = parts["partials/weekly-current.hbs"]
        self.assertIn("2026-W41", current)
        self.assertIn('timezone="UTC"', current)
        self.assertIn("visibility:public", current)
        self.assertIn("updated_at:", current)
        self.assertIn("updated_at:'2026-10-05 15:00:00'", current)
        self.assertIn("hash-weekly-", current)
        self.assertNotIn(inventory["posts"][0]["id"], current)
        fractional = {**export(ledger, inventory)["events"][0], "revised_at": "2026-10-05T15:00:00.123Z"}
        with self.assertRaisesRegex(ValueError, "whole-second"):
            weekly.native_query(fractional)
        # Ghost stores the slug segment; the public URL keeps its /stories/ prefix.
        slug = public.article_slug(review["events"][0]["article_path"])
        self.assertIn(f"slug:{slug}+", current)
        archived = parts["partials/weekly-approved-card.hbs"]
        self.assertIn('{{#match slug "' + slug + '"}}', archived)
        self.assertIn('href="' + review["events"][0]["article_path"] + '"', archived)

    def test_story_prose_is_a_third_owned_block(self):
        evidence = '<section class="observation-record" data-event-id="e" data-evidence-version="v"><p>x</p></section>'
        prose = (
            '<section class="story-prose" data-story-version="'
            + "a" * 64
            + '">\n<p>Story.</p>\n<section class="type-background"><h2>About</h2></section>\n</section>'
        )
        hidden = "<div hidden>" + prose.replace("a" * 64, "b" * 64) + "</div>"
        parsed = weekly.PublishedBlocks(prose + "\n" + evidence + hidden)
        self.assertEqual([kind for kind, _ in parsed.blocks], ["story", "evidence"])
        self.assertEqual(next(block for kind, block in parsed.blocks if kind == "story"), prose)
        self.assertEqual(parsed.markers, 0)


def ranked(kind, interest, observed, identifier, rarity):
    """A weekly candidate row as weekly_research.candidates() builds it."""
    return {
        "event": {"kind": kind, "observed_start": observed, "id": identifier},
        "score": {"interest": interest, "exceptional": False, "evidence": 30, "depth": 10},
        "recent_picks": 0,
        "recent_observations": 0,
        "rarity": rarity,
    }


def kind_ledger(*rows):
    """Ledger records reduced to what the rarity count reads: state, kind, and start."""
    return {
        "records": [
            {
                "state": state,
                "evidence": {"kind": kind, "observation": {"start_utc": start}},
                "editorial": {"synthetic": synthetic},
            }
            for kind, start, state, synthetic in rows
        ]
    }


class RankTests(unittest.TestCase):
    def test_a_rare_kind_outranks_three_stories_of_a_more_interesting_kind(self):
        week = [
            ("tcas_ra", "2026-09-29T12:00:00Z"),
            ("descending_spiral", "2026-09-29T13:00:00Z"),
            ("descending_spiral", "2026-09-30T13:00:00Z"),
            ("descending_spiral", "2026-10-01T13:00:00Z"),
        ]
        counts = weekly.kind_counts(
            kind_ledger(*((kind, start, "approved", False) for kind, start in week)), "2026-10-05"
        )
        self.assertEqual(counts, {"tcas_ra": 1, "descending_spiral": 3})
        self.assertEqual(weekly.rarity(counts["tcas_ra"]), 0.5)
        self.assertEqual(weekly.rarity(counts["descending_spiral"]), 0.25)
        interest = {kind: curation.INTEREST[kind] for kind in counts}
        self.assertLess(interest["tcas_ra"], interest["descending_spiral"])
        rows = [
            ranked(kind, interest[kind], start, f"event-{index}", weekly.rarity(counts[kind]))
            for index, (kind, start) in enumerate(reversed(week))
        ]
        self.assertEqual(weekly.rank(rows)[0]["event"]["kind"], "tcas_ra")

    def test_interest_then_recency_order_candidates_of_equal_rarity(self):
        rows = [
            ranked("circling", 30, "2026-09-29T12:00:00Z", "older", 0.5),
            ranked("circling", 30, "2026-10-01T12:00:00Z", "newer", 0.5),
            ranked("racetrack_orbit", 35, "2026-09-28T12:00:00Z", "interesting", 0.5),
        ]
        self.assertEqual([row["event"]["id"] for row in weekly.rank(rows)], ["interesting", "newer", "older"])

    def test_a_kind_picked_in_the_last_four_weeks_ranks_after_the_others(self):
        first_week = [
            ranked("tcas_ra", 40, "2026-09-22T12:00:00Z", "tcas-1", 0.5),
            ranked("circling", 30, "2026-09-23T12:00:00Z", "circling-1", 0.25),
        ]
        ledger = kind_ledger(("circling", "2026-09-01T12:00:00Z", "approved", False))
        self.assertEqual(weekly.picked_kinds(ledger, "2026-09-21"), set())
        pick = weekly.rank(first_week, picked=weekly.picked_kinds(ledger, "2026-09-21"))[0]
        self.assertEqual(pick["event"]["id"], "tcas-1")
        ledger["records"].append(
            {
                "state": "approved",
                "evidence": {"kind": "tcas_ra", "observation": {"start_utc": "2026-09-22T12:00:00Z"}},
                "editorial": {"weekly_spotlight": {"week_start": "2026-09-21"}},
            }
        )
        second_week = [
            ranked("tcas_ra", 40, "2026-09-29T12:00:00Z", "tcas-2", 0.5),
            ranked("descending_spiral", 42, "2026-09-30T12:00:00Z", "spiral", 0.25),
        ]
        picked = weekly.picked_kinds(ledger, "2026-09-28")
        self.assertEqual(picked, {"tcas_ra"})
        # The repeated kind follows every other candidate; it is demoted, not dropped.
        self.assertEqual(
            [row["event"]["id"] for row in weekly.rank(second_week, picked=picked)], ["spiral", "tcas-2"]
        )
        # With no other candidate, the repeated kind is still the week's pick.
        self.assertEqual(weekly.rank(second_week[:1], picked=picked)[0]["event"]["id"], "tcas-2")
        # The fourth week after the pick still passes it over, the fifth may pick it again,
        # and a withdrawn pick never counts.
        self.assertEqual(weekly.picked_kinds(ledger, "2026-10-19"), {"tcas_ra"})
        self.assertEqual(weekly.picked_kinds(ledger, "2026-10-26"), set())
        ledger["records"][-1]["state"] = "withdrawn"
        self.assertEqual(weekly.picked_kinds(ledger, "2026-09-28"), set())

    def test_an_exceptional_story_wins_over_a_newer_ordinary_one_of_equal_rarity_and_interest(self):
        exceptional = ranked("circling", 30, "2026-09-29T12:00:00Z", "exceptional", 0.5)
        exceptional["score"]["exceptional"] = True
        ordinary = ranked("circling", 30, "2026-10-01T12:00:00Z", "ordinary", 0.5)
        self.assertEqual(weekly.rank([ordinary, exceptional])[0]["event"]["id"], "exceptional")

    def test_rarity_counts_live_stories_in_the_ninety_days_before_the_week_ends(self):
        ledger = kind_ledger(
            ("tcas_ra", "2026-07-07T00:00:00Z", "approved", False),
            ("tcas_ra", "2026-07-06T23:59:59Z", "approved", False),
            ("tcas_ra", "2026-10-04T23:59:59Z", "pending", False),
            ("tcas_ra", "2026-10-05T00:00:00Z", "approved", False),
            ("tcas_ra", "2026-09-30T00:00:00Z", "withdrawn", False),
            ("tcas_ra", "2026-09-30T00:00:00Z", "approved", True),
        )
        ledger["records"].append({"state": "approved", "evidence": {"kind": "tcas_ra"}, "editorial": {}})
        self.assertEqual(weekly.kind_counts(ledger, "2026-10-05"), {"tcas_ra": 2})


class ProseCheckTests(unittest.TestCase):
    HYPOTHESES = ("a stale registry entry", "a database typo")

    def issues(self, text, **options):
        return weekly.prose_issues([text], **{"hypotheses": self.HYPOTHESES, **options})

    def test_dashes_and_ampersands_are_refused(self):
        self.assertEqual(self.issues("A plain sentence about addresses, registries, and broadcasts."), [])
        for text in (
            "Addresses \u2014 one per airframe.",
            "Addresses \u2013 one per airframe.",
            "Addresses & registries.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.issues(text), ["Research prose has a dash or an ampersand"])

    def test_a_listed_hypothesis_needs_a_hedge(self):
        self.assertEqual(self.issues("A stale registry entry could pair an address with another type."), [])
        self.assertEqual(
            self.issues("A stale registry entry pairs an address with another type."),
            ["A listed hypothesis is stated without a hedge"],
        )

    def test_a_causeless_kind_names_no_cause(self):
        self.assertEqual(
            self.issues("A database typo may pair an address with another type.", causeless=True), []
        )
        for text in (
            "The reading was a decoding glitch.",
            "The registry is wrong about this address.",
            "Interference caused the reading.",
            "A database typo may reflect a decoding error.",
        ):
            with self.subTest(text=text):
                issues = self.issues(text, causeless=True)
                self.assertTrue(issues and issues[0].startswith("Unlisted cause"), issues)
        # A cause noun is a cause only where the kind names none.
        self.assertEqual(self.issues("The reading was a decoding glitch."), [])

    def test_numbers_come_from_the_facts_or_the_readings(self):
        self.assertEqual(
            self.issues("The aircraft reported 651 knots across three reports.", numbers=(651.2, 3)), []
        )
        self.assertEqual(self.issues("Each one of the addresses is 24 bits long.", numbers=(24,)), [])
        for text in ("The system dates from 1993.", "Seven registries list it.", "It reached 652 knots."):
            with self.subTest(text=text):
                issues = self.issues(text, numbers=(651.2, 3))
                self.assertTrue(issues and issues[0].startswith("Unsupported number"), issues)

    def test_names_come_from_the_vocabulary(self):
        names = {"canada", "icao", "state"}
        self.assertEqual(
            self.issues("ICAO gives each State a block, and Canada assigns them.", names=names), []
        )
        for text in (
            "The Boeing listed for one address flew past.",
            "Registration C-GABC carried the address.",
            "Air Toronto operates from Toronto.",
            "John Smith keeps the register.",
            "Cessna Aircraft builds light airplanes.",
        ):
            with self.subTest(text=text):
                issues = self.issues(text, names=names)
                self.assertTrue(issues and issues[0].startswith("Unlisted name"), issues)
        # Without a vocabulary, as for weekly research, names are not checked.
        self.assertEqual(self.issues("The Boeing listed for one address flew past."), [])


if __name__ == "__main__":
    unittest.main()
