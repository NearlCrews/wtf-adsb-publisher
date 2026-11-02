"""Wikidata descriptions for the organizations watchlist entries link to; nothing on the network."""

import http.client
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
import reference_fixture
import story_fixtures as fixtures

from wtf_adsb_publisher import entity_context, entity_rules, site_origin, story_facts

PAGEPROPS = {
    "query": {
        "pages": {
            "1": {"title": "Federal Aviation Administration", "pageprops": {"wikibase_item": "Q335313"}}
        }
    }
}


def setUpModule():
    reference_fixture.install()


def entity(label, description, instance="Q327333", aliases=("FAA",), target="Q335313"):
    """A wbgetentities answer, as a function of the request: lastrevid comes only with props=info.
    A `target` other than the requested Q335313 is the item a merged one now redirects to: as
    Wikibase answers, its data sits under the requested id, with its own id and a redirects record."""

    def answer(url):
        found = {
            "labels": {"en": {"value": label}},
            "descriptions": {"en": {"value": description}},
            "aliases": {"en": [{"value": alias} for alias in aliases]},
            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": instance}}}}]},
        }
        props = url.split("props=", 1)[1].split("&", 1)[0].split("|")
        found["id"] = target
        if target != "Q335313":
            found["redirects"] = {"from": "Q335313", "to": target}
        return {"entities": {"Q335313": {**found, **({"lastrevid": 123456} if "info" in props else {})}}}

    return answer


def fetcher(responses):
    calls = []

    def fetch(url):
        calls.append(url)
        for needle, payload in responses:
            if needle in url:
                return payload(url) if callable(payload) else payload
        raise AssertionError(url)

    fetch.calls = calls
    return fetch


def key(link):
    """The cache key of one (entry link, association, ...) tuple."""
    return entity_rules.cache_key(link[0], link[1])


def lookup_with(url, association, label, description, **entity_options):
    """(lookup() of `url` for `association`, the fetcher): Wikipedia names item Q335313, whose
    Wikidata entity has the given label and description."""
    fetch = fetcher(
        [("en.wikipedia.org", PAGEPROPS), ("wikidata.org", entity(label, description, **entity_options))]
    )
    return entity_context.lookup(url, association, fetch=fetch, head=lambda url: url), fetch


class Lookup(unittest.TestCase):
    def test_an_organization_with_a_matching_label_is_available(self):
        found, fetch = lookup_with(
            "https://en.wikipedia.org/wiki/Federal_Aviation_Administration",
            "Federal Aviation Administration",
            "Federal Aviation Administration",
            "United States government agency regulating civil aviation",
        )
        self.assertEqual(found["state"], "available")
        self.assertEqual(found["label"], "Federal Aviation Administration")
        self.assertEqual(found["description"], "United States government agency regulating civil aviation")
        self.assertEqual(found["item"], "Q335313")
        self.assertEqual(found["revision"], 123456)
        self.assertEqual(len(fetch.calls), 2)
        # An alias names the item too: the entry may carry the short name.
        found, _fetch = lookup_with(
            "https://en.wikipedia.org/wiki/FAA",
            "faa",
            "Federal Aviation Administration",
            "civil aviation regulator",
        )
        self.assertEqual((found["state"], found["label"]), ("available", "Federal Aviation Administration"))
        self.assertEqual(entity_context.accept("FAA", (), "agency", "FAA", set()), "description")

    def test_humans_mismatched_labels_deny_words_and_other_hosts_are_unavailable(self):
        for url, association, label, description, options, reason in (
            ("Jeff_Vinik", "Jeff Vinik", "Jeff Vinik", "American businessman", {"instance": "Q5"}, "human"),
            (
                "Tampa_Bay_Lightning",
                "Jeff Vinik",
                "Tampa Bay Lightning",
                "ice hockey team in Florida",
                {},
                "label",
            ),
            ("Acme_Air", "Acme Air", "Acme Air", "airline convicted of fraud", {}, "deny"),
        ):
            with self.subTest(reason=reason):
                found, _fetch = lookup_with(
                    "https://en.wikipedia.org/wiki/" + url, association, label, description, **options
                )
                self.assertEqual(found["reason"], reason)
        self.assertEqual(
            entity_context.lookup("https://example.com/x", "Acme", fetch=fetcher([]), head=lambda url: url)[
                "reason"
            ],
            "host",
        )
        short = entity_context.lookup(
            "https://w.wiki/abc",
            "Acme",
            fetch=fetcher([]),
            head=lambda url: "https://de.wikipedia.org/wiki/Acme",
        )
        self.assertEqual(short["reason"], "host")
        missing = fetcher(
            [("en.wikipedia.org", {"query": {"pages": {"-1": {"title": "Acme", "missing": ""}}}})]
        )
        self.assertEqual(
            entity_context.lookup(
                "https://en.wikipedia.org/wiki/Acme", "Acme", fetch=missing, head=lambda url: url
            )["reason"],
            "item",
        )

    def test_a_short_link_is_followed_to_its_article(self):
        fetch = fetcher(
            [
                ("en.wikipedia.org", PAGEPROPS),
                ("wikidata.org", entity("Acme Air", "regional airline in Ohio")),
            ]
        )
        found = entity_context.lookup(
            "https://w.wiki/abc",
            "Acme Air",
            fetch=fetch,
            head=lambda url: "https://en.wikipedia.org/wiki/Acme_Air",
        )
        self.assertEqual(found["state"], "available")
        self.assertIn("titles=Acme_Air", fetch.calls[0])

    def test_descriptions_naming_a_kind_of_person_or_a_private_name_are_refused(self):
        for description in (
            "American businessman and investor",
            "holding company of a billionaire family",
            "company founded by a famous actor",
        ):
            with self.subTest(description=description):
                self.assertEqual(entity_context.accept("Acme", (), description, "Acme", {"Q4830453"}), "deny")
        self.assertEqual(
            entity_context.accept("Acme", (), "regional airline in Ohio", "Acme", {"Q5"}), "human"
        )
        self.assertEqual(
            entity_context.accept("Acme", (), "regional airline <b>in</b> Ohio", "Acme", set()), "description"
        )
        self.assertIsNone(
            entity_context.accept("Acme", ("Acme Air",), "regional airline in Ohio", "acme air", set())
        )

    def test_descriptions_naming_people_are_refused(self):
        for description in (
            "American spacecraft manufacturer founded by Elon Musk",
            "privately held company of Donald Trump",
            "religious organization led by Kenneth Copeland",
            "charity run by the Clinton family",
            "airline owned by a holding company",
            "Trump family company",
        ):
            with self.subTest(description=description):
                self.assertIsNotNone(entity_context.accept("Acme", (), description, "Acme", {"Q4830453"}))
        # Without a deny word, a capitalised name the label does not carry is refused on its own.
        self.assertEqual(
            entity_context.accept("Acme", (), "privately held company of Donald Trump", "Acme", set()), "name"
        )
        self.assertEqual(entity_context.accept("Acme", (), "Musk's rocket company", "Acme", set()), "name")
        # "of" with a State after it does not make a person's name a place.
        self.assertEqual(
            entity_context.accept(
                "Acme", (), "holding company of Donald Trump, United States", "Acme", set()
            ),
            "name",
        )
        # A superlative the story could not scope is never repeated.
        self.assertEqual(
            entity_context.accept("Acme", (), "the oldest airline in Ohio", "Acme", set()), "checks"
        )

    def test_a_cached_description_naming_a_person_gives_no_fact(self):
        # An answer cached before the name rule existed is judged again when the facts are built.
        raw = fixtures.kind_event("watchlist_match")
        for description in ("privately held company of Donald Trump", "charity run by the Clinton family"):
            with self.subTest(description=description):
                built = fixtures.facts(raw, entities=fixtures.watch_entity(description))
                self.assertIsNone(story_facts.value(built, "wl.who"))
        built = fixtures.facts(raw, entities=fixtures.watch_entity())
        self.assertIsNotNone(story_facts.value(built, "wl.who"))

    def test_a_cached_alias_may_be_capitalised_in_its_description(self):
        raw = fixtures.kind_event("watchlist_match")
        entities = fixtures.watch_entity("FAA's regional office in Ohio", aliases=["FAA"])
        self.assertIsNotNone(story_facts.value(fixtures.facts(raw, entities=entities), "wl.who"))
        found, _fetch = lookup_with(
            "https://en.wikipedia.org/wiki/Example",
            "FAA",
            "Federal Aviation Administration",
            "civil aviation regulator",
        )
        self.assertEqual(found["aliases"], ["FAA"])

    def test_descriptions_with_abbreviations_nationalities_places_and_the_label_pass(self):
        for label, description in (
            ("Federal Aviation Administration", "U.S. government agency regulating civil aviation"),
            ("Yankee Air Museum", "aviation museum in Van Buren Township, Michigan, United States"),
            ("Acme Air", "German airline based in Frankfurt, Germany"),
            ("Acme Air", "American regional airline"),
            ("Acme Air", "regional subsidiary of Acme Air"),
            (
                "Michigan Department of Transportation",
                "Department of Transportation of the State of Michigan",
            ),
            ("Acme Air", "Airline and airline holding company"),
        ):
            with self.subTest(description=description):
                self.assertIsNone(entity_context.accept(label, (), description, label, set()))

    def test_the_item_revision_reaches_the_story_source(self):
        found, _fetch = lookup_with(
            "https://en.wikipedia.org/wiki/Example",
            fixtures.WATCH_ASSOCIATION,
            fixtures.WATCH_ASSOCIATION,
            "aircraft leasing company in Ohio",
        )
        raw = fixtures.kind_event("watchlist_match")
        link = raw["evidence"]["watchlist"]["reference_url"]
        key = entity_rules.cache_key(link, fixtures.WATCH_ASSOCIATION)
        built = fixtures.facts(raw, entities={key: {**found, "association": fixtures.WATCH_ASSOCIATION}})
        self.assertEqual(
            story_facts.value(built, "wl.who"),
            fixtures.WATCH_ASSOCIATION + ", aircraft leasing company in Ohio",
        )
        source = {item["id"]: item for item in built["sources"]}["src.wikidata.Q335313"]
        self.assertEqual(source["title"], "Wikidata item Q335313, revision 123456, CC0")

    def test_fragments_queries_and_mobile_links_name_their_article(self):
        for url in (
            "https://en.wikipedia.org/wiki/Acme_Air#History",
            "https://en.wikipedia.org/wiki/Acme_Air?oldid=1",
            "https://en.m.wikipedia.org/wiki/Acme_Air",
            "https://en.m.wikipedia.org/wiki/Acme_Air?useskin=vector#Fleet",
        ):
            with self.subTest(url=url):
                self.assertEqual(entity_context.article_title(url, head=lambda url: url), "Acme_Air")
                found, fetch = lookup_with(url, "Acme Air", "Acme Air", "regional airline in Ohio")
                self.assertEqual(found["state"], "available")
                self.assertIn("titles=Acme_Air&", fetch.calls[0] + "&")
        for url in (
            "https://de.m.wikipedia.org/wiki/Acme",
            "https://en.wikipedia.org/wiki/",
            "http://en.m.wikipedia.org/wiki/Acme",
        ):
            with self.subTest(url=url):
                self.assertIsNone(entity_context.article_title(url, head=lambda url: url))

    def test_a_redirected_item_is_followed_once(self):
        # A merged item answers under the requested id with the target's data, id, and revision;
        # the story cites the target, so the revision is the target's own.
        found, fetch = lookup_with(
            "https://en.wikipedia.org/wiki/Acme_Air",
            "Acme Air",
            "Acme Air",
            "regional airline in Ohio",
            target="Q999",
        )
        self.assertEqual((found["state"], found["item"], found["revision"]), ("available", "Q999", 123456))
        self.assertEqual(len(fetch.calls), 2)
        raw = fixtures.kind_event("watchlist_match")
        link = raw["evidence"]["watchlist"]["reference_url"]
        cached = {**found, "label": fixtures.WATCH_ASSOCIATION, "association": fixtures.WATCH_ASSOCIATION}
        built = fixtures.facts(
            raw, entities={entity_rules.cache_key(link, fixtures.WATCH_ASSOCIATION): cached}
        )
        source = {item["id"]: item for item in built["sources"]}["src.wikidata.Q999"]
        self.assertEqual(source["title"], "Wikidata item Q999, revision 123456, CC0")
        # A redirects record that does not lead from the item asked for to a Q id it carries says
        # nothing reliable about the data beside it, so the item is refused under the requested id.
        moved = entity("Acme Air", "regional airline in Ohio", target="Q999")("props=info")["entities"][
            "Q335313"
        ]
        for changed in (
            {"redirects": {"from": "Q1", "to": "Q999"}},
            {"redirects": {"from": "Q335313", "to": "P31"}, "id": "P31"},
            {"redirects": {"from": "Q335313", "to": "Q999"}, "id": "Q998"},
            {"redirects": "Q999"},
        ):
            with self.subTest(changed=changed):
                answer = {"entities": {"Q335313": {**moved, **changed}}}
                fetch = fetcher([("en.wikipedia.org", PAGEPROPS), ("wikidata.org", answer)])
                found = entity_context.lookup(
                    "https://en.wikipedia.org/wiki/Acme_Air", "Acme Air", fetch=fetch, head=lambda url: url
                )
                self.assertEqual(found, {"state": "unavailable", "reason": "item"})

    def test_an_entity_that_is_not_an_object_is_refused(self):
        for value in ("Q335313", ["Q335313"], None):
            with self.subTest(value=value):
                answer = {"entities": {"Q335313": value}}
                fetch = fetcher([("en.wikipedia.org", PAGEPROPS), ("wikidata.org", answer)])
                found = entity_context.lookup(
                    "https://en.wikipedia.org/wiki/Acme_Air", "Acme Air", fetch=fetch, head=lambda url: url
                )
                self.assertEqual(found, {"state": "unavailable", "reason": "item"})

    def test_a_description_with_a_figure_is_refused(self):
        # Dates, counts, and years in a description are figures no story could support.
        for description in (
            "airline founded in 1926",
            "one of 3 regional airlines in Ohio",
            "museum since 1981",
        ):
            with self.subTest(description=description):
                self.assertEqual(entity_context.accept("Acme", (), description, "Acme", set()), "number")
        raw = fixtures.kind_event("watchlist_match")
        built = fixtures.facts(
            raw, entities=fixtures.watch_entity("aircraft leasing company founded in 1990")
        )
        self.assertIsNone(story_facts.value(built, "wl.who"))

    def test_api_errors_and_truncated_answers_are_transport_failures(self):
        for answer in ({"error": {"code": "maxlag"}}, {"batchcomplete": ""}, []):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                entity_context.lookup(
                    "https://en.wikipedia.org/wiki/Acme",
                    "Acme",
                    fetch=fetcher([("en.wikipedia.org", answer)]),
                    head=lambda url: url,
                )
        with self.assertRaises(ValueError):
            entity_context.lookup(
                "https://en.wikipedia.org/wiki/Acme",
                "Acme",
                fetch=fetcher([("en.wikipedia.org", PAGEPROPS), ("wikidata.org", {"error": {"code": "x"}})]),
                head=lambda url: url,
            )
        with tempfile.TemporaryDirectory() as state:
            links = [
                ("https://en.wikipedia.org/wiki/Acme_Air", "Acme Air", "Civ", None),
                ("https://en.wikipedia.org/wiki/Acme_Rail", "Acme Rail", "Civ", None),
            ]

            def truncated(url, association, **_):
                if url.endswith("Rail"):
                    raise http.client.IncompleteRead(b"{")
                return {"state": "unavailable", "reason": "label"}

            self.assertEqual(entity_context.resolve(state, links, now=5, lookup=truncated), 1)
            cache = entity_context.load(state)
            self.assertEqual(cache[key(links[0])]["state"], "unavailable")
            self.assertEqual(
                {name: cache[key(links[1])][name] for name in ("state", "failures", "failed_at", "error")},
                {"state": "failed", "failures": 1, "failed_at": 5, "error": "IncompleteRead"},
            )

            def broken(url, association, **_):
                raise KeyError("a bug, not the network")

            with self.assertRaises(KeyError):
                entity_context.resolve(
                    state,
                    [("https://en.wikipedia.org/wiki/Other", "Other", "Civ", None)],
                    now=6,
                    lookup=broken,
                )

    def test_requests_name_the_public_site(self):
        seen = []

        class Opener:
            def open(self, request, timeout):
                seen.append(request.get_header("User-agent"))
                raise OSError("down")

        with mock.patch.object(entity_context, "build_opener", Opener):
            for environment, expected in (
                ({"SITE_ORIGIN": "https://site.example"}, "WTF-ADSB/1.0 (+https://site.example/about/)"),
                ({}, f"WTF-ADSB/1.0 (+{site_origin.FALLBACK_CONTACT})"),
            ):
                with mock.patch.dict(os.environ, environment, clear=True), self.assertRaises(OSError):
                    entity_context._head("https://w.wiki/x")
                self.assertEqual(seen[-1], expected)
                self.assertNotIn("@", seen[-1])

    def test_api_requests_use_the_shared_bounded_client(self):
        seen = {}

        def get_json(url, opener=None, **options):
            seen.update(url=url, **options)
            raise entity_context.registry_http.Unavailable(503)

        with (
            mock.patch.object(entity_context.registry_http, "get_json", get_json),
            mock.patch.dict(os.environ, {"SITE_ORIGIN": "https://site.example"}),
        ):
            with self.assertRaises(entity_context.TRANSPORT_ERRORS):
                entity_context.fetch_json("https://www.wikidata.org/w/api.php?x", timeout=2.5)
        self.assertEqual(
            seen,
            {
                "url": "https://www.wikidata.org/w/api.php?x",
                "max_bytes": entity_context.MAX_BYTES,
                "timeout": 2.5,
                "user_agent": "WTF-ADSB/1.0 (+https://site.example/about/)",
            },
        )


class Cache(unittest.TestCase):
    def test_cache_backs_off_and_never_refreshes_on_its_own(self):
        with tempfile.TemporaryDirectory() as state:
            calls = []

            def failing(url, association, **_):
                calls.append(url)
                raise OSError("down")

            links = [("https://en.wikipedia.org/wiki/Acme_Air", "Acme Air", "Civ", "Acme Air")]
            self.assertEqual(entity_context.resolve(state, links, now=1000, lookup=failing), 0)
            self.assertEqual(entity_context.resolve(state, links, now=2000, lookup=failing), 0)
            self.assertEqual(len(calls), 1)  # one day of backoff
            self.assertEqual(entity_context.resolve(state, links, now=1000 + 86401, lookup=failing), 0)
            self.assertEqual(len(calls), 2)
            # The second failure waits a week.
            entity_context.resolve(state, links, now=1000 + 2 * 86400 + 2, lookup=failing)
            self.assertEqual(len(calls), 2)

            def good(url, association, **_):
                return {
                    "state": "available",
                    "label": "Acme Air",
                    "description": "airline",
                    "item": "Q1",
                    "revision": 1,
                }

            entity_context.resolve(state, links, now=1000 + 9 * 86400, lookup=good)
            cache = entity_context.load(state)
            self.assertEqual(cache[key(links[0])]["state"], "available")
            self.assertEqual(cache[key(links[0])]["retrieved_at"], 1000 + 9 * 86400)
            self.assertNotIn("failures", cache[key(links[0])])
            entity_context.resolve(state, links, now=1000 + 400 * 86400, lookup=failing)
            self.assertEqual(entity_context.load(state)[key(links[0])]["state"], "available")
            self.assertEqual(len(calls), 2)
            # The cache joins Plane Alert links to Wikidata data and stays private.
            self.assertEqual(os.stat(Path(state) / entity_context.FILE).st_mode & 0o777, 0o600)

    def test_vague_and_privacy_entries_are_skipped_without_a_request(self):
        with tempfile.TemporaryDirectory() as state:
            calls = []
            links = [
                ("https://en.wikipedia.org/wiki/Acme", "Private", "Civ", "Private"),
                ("https://en.wikipedia.org/wiki/Acme", "Acme", "PIA", "Acme"),
                ("https://en.wikipedia.org/wiki/Acme", "Acme", "Civ", "Privacy-related entry"),
            ]
            entity_context.resolve(
                state, links, now=1, lookup=lambda *a, **k: calls.append(a) or {"state": "available"}
            )
            self.assertEqual(calls, [])
            self.assertEqual(entity_context.load(state), {})

    def test_a_pass_looks_up_at_most_its_limit(self):
        with tempfile.TemporaryDirectory() as state:
            links = [
                (f"https://en.wikipedia.org/wiki/Acme_{index}", "Acme", "Civ", "Acme") for index in range(4)
            ]

            def refused(url, association, **_):
                return {"state": "unavailable", "reason": "label"}

            self.assertEqual(entity_context.resolve(state, links, now=1, limit=3, lookup=refused), 3)
            self.assertEqual(entity_context.resolve(state, links, now=2, limit=3, lookup=refused), 1)
            self.assertEqual(entity_context.resolve(state, links, now=3, limit=3, lookup=refused), 0)

    def test_refresh_keeps_each_answer_in_use_until_a_fresh_one_replaces_it(self):
        with tempfile.TemporaryDirectory() as state:
            links = [
                (f"https://en.wikipedia.org/wiki/Acme_{index}", f"Acme {index}", "Civ", None)
                for index in range(3)
            ]
            new = [("https://en.wikipedia.org/wiki/Newer", "Newer", "Civ", None)]

            def answer(description):
                def found(url, association, **_):
                    return {
                        "state": "available",
                        "label": association,
                        "description": description,
                        "item": "Q1",
                        "revision": 1,
                    }

                return found

            def failing(url, association, **_):
                raise OSError("down")

            entity_context.resolve(state, links, now=1, lookup=answer("old description"))
            self.assertEqual(entity_context.refresh(state), 3)
            self.assertEqual(entity_context.refresh(state), 0)
            cache = entity_context.load(state)
            self.assertTrue(all(entry["stale"] and entry["state"] == "available" for entry in cache.values()))
            # Stale answers go first, ahead of a link never looked up.
            calls = []

            def counting(url, association, **_):
                calls.append(url)
                return answer("new description")(url, association)

            entity_context.resolve(state, new + links[:1], now=2, limit=1, lookup=counting)
            self.assertEqual(calls, [links[0][0]])
            cache = entity_context.load(state)
            self.assertEqual(cache[key(links[0])]["description"], "new description")
            self.assertNotIn("stale", cache[key(links[0])])
            # A failed lookup keeps the stale answer in use and backs off.
            entity_context.resolve(state, links[1:2], now=3, lookup=failing)
            kept = entity_context.load(state)[key(links[1])]
            self.assertEqual(
                (kept["state"], kept["description"], kept["stale"], kept["failures"], kept["failed_at"]),
                ("available", "old description", True, 1, 3),
            )
            entity_context.resolve(state, links[1:2], now=4, lookup=counting)
            self.assertEqual(len(calls), 1)
            entity_context.resolve(state, links[1:2], now=3 + 86400, lookup=counting)
            replaced = entity_context.load(state)[key(links[1])]
            self.assertEqual(replaced["description"], "new description")
            self.assertFalse({"stale", "failures", "failed_at"} & set(replaced))

    def test_each_request_waits_no_longer_than_the_budget_left(self):
        clock = [100.0]
        timeouts = []

        def fetch_json(url, *, timeout):
            timeouts.append(timeout)
            clock[0] += 6
            raise TimeoutError("slow")

        def looking(url, association, *, fetch, head):
            return fetch(url)

        links = [(f"https://en.wikipedia.org/wiki/Acme_{index}", "Acme", "Civ", None) for index in range(4)]
        with (
            tempfile.TemporaryDirectory() as state,
            mock.patch.object(entity_context, "fetch_json", fetch_json),
        ):
            self.assertEqual(
                entity_context.resolve(state, links, now=1, lookup=looking, clock=lambda: clock[0]), 0
            )
            cache = entity_context.load(state)
        # Five seconds each while the budget allows, then what is left of it; the lookup cut short
        # by the spent budget is no failure of the link, so it waits no backoff.
        self.assertEqual(timeouts, [entity_context.TIMEOUT, entity_context.TIMEOUT, 3.0])
        self.assertEqual(sorted(entry["failures"] for entry in cache.values()), [1, 1])
        self.assertNotIn(key(links[2]), cache)

    def test_each_association_sharing_a_link_gets_its_own_answer(self):
        link = "https://en.wikipedia.org/wiki/Acme"
        links = [
            (link, "Acme Air", "Civ", None),
            (link, "ACME AIR", "Civ", None),
            (link, "Acme Rail", "Civ", None),
        ]
        with tempfile.TemporaryDirectory() as state:
            calls = []

            def found(url, association, **_):
                calls.append(association)
                return {"state": "available", "label": association, "description": "airline", "item": "Q1"}

            self.assertEqual(entity_context.resolve(state, links, now=1, lookup=found), 2)
            self.assertEqual(calls, ["Acme Air", "Acme Rail"])
            self.assertEqual(sorted(entity_context.load(state)), [link + "\tacme air", link + "\tacme rail"])
            # A story reads the answer for the association its own entry carries.
            self.assertEqual(entity_rules.cache_key(link, "ACME Rail"), link + "\tacme rail")

    def test_every_form_of_an_article_link_shares_one_key(self):
        forms = (
            "https://en.wikipedia.org/wiki/Acme_Air",
            "https://en.wikipedia.org/wiki/Acme_Air#History",
            "https://en.wikipedia.org/wiki/Acme_Air?oldid=1",
            "https://en.m.wikipedia.org/wiki/Acme_Air?useskin=vector#Fleet",
        )
        self.assertEqual(
            {entity_rules.cache_key(link, "Acme Air") for link in forms},
            {"https://en.wikipedia.org/wiki/Acme_Air\tacme air"},
        )
        with tempfile.TemporaryDirectory() as state:
            calls = []

            def found(url, association, **_):
                calls.append(url)
                return {"state": "unavailable", "reason": "label"}

            links = [(link, "Acme Air", "Civ", None) for link in forms]
            self.assertEqual(entity_context.resolve(state, links, now=1, lookup=found), 1)
            self.assertEqual(calls, [forms[0]])
            # A cache keyed by a link alone is rekeyed to the same key.
            (Path(state) / entity_context.FILE).write_text(
                json.dumps({"entities": {forms[3]: {"state": "unavailable", "association": "Acme Air"}}})
            )
            self.assertEqual(
                list(entity_context.load(state)), ["https://en.wikipedia.org/wiki/Acme_Air\tacme air"]
            )
        # A story whose entry carries another form of the article link reads the same answer.
        raw = fixtures.kind_event("watchlist_match")
        raw["evidence"]["watchlist"]["reference_url"] = forms[0]
        answer = next(iter(fixtures.watch_entity().values()))
        entities = {entity_rules.cache_key(forms[0], fixtures.WATCH_ASSOCIATION): answer}
        for link in forms:
            with self.subTest(link=link):
                raw["evidence"]["watchlist"]["reference_url"] = link
                self.assertIsNotNone(story_facts.value(fixtures.facts(raw, entities=entities), "wl.who"))

    def test_a_short_link_with_a_query_or_section_keys_apart_from_the_plain_one(self):
        # article_title follows only a plain w.wiki link (SHORT), so another form must not read
        # the answer the plain one was given.
        plain = entity_rules.cache_key("https://w.wiki/abc", "Acme Air")
        self.assertEqual(plain, "https://w.wiki/abc\tacme air")
        for link in ("https://w.wiki/abc?x=1", "https://w.wiki/abc#History"):
            with self.subTest(link=link):
                self.assertNotEqual(entity_rules.cache_key(link, "Acme Air"), plain)
                self.assertIsNone(entity_context.article_title(link, head=lambda url: self.fail(url)))
        self.assertEqual(
            entity_rules.canonical_link("https://en.m.wikipedia.org/wiki/Acme_Air?useskin=vector#Fleet"),
            "https://en.wikipedia.org/wiki/Acme_Air",
        )

    def test_keys_written_before_the_canonical_link_are_rekeyed(self):
        # An earlier version keyed the link as given, a tab, and the association.
        old = {
            "https://en.m.wikipedia.org/wiki/Acme_Air#Fleet\tacme air": {
                "state": "available",
                "label": "Acme Air",
                "description": "airline",
                "item": "Q1",
                "association": "Acme Air",
                "retrieved_at": 1,
            },
            "https://en.wikipedia.org/wiki/Acme_Rail?oldid=1\tacme rail": {"state": "failed", "failures": 1},
        }
        with tempfile.TemporaryDirectory() as state:
            (Path(state) / entity_context.FILE).write_text(json.dumps({"version": 1, "entities": old}))
            cache = entity_context.load(state)
        self.assertEqual(
            sorted(cache),
            [
                "https://en.wikipedia.org/wiki/Acme_Air\tacme air",
                "https://en.wikipedia.org/wiki/Acme_Rail\tacme rail",
            ],
        )
        self.assertEqual(cache["https://en.wikipedia.org/wiki/Acme_Air\tacme air"]["item"], "Q1")

    def test_a_rekeyed_host_refusal_is_dropped_and_an_answer_wins_a_collision(self):
        # Before this version a section or mobile link was refused as "host" and kept for good;
        # it names its article now, so such a refusal is looked up again, and where two old keys
        # meet on one, the available answer stays in use.
        plain = "https://en.wikipedia.org/wiki/Yankee_Air_Museum"
        found = {
            "state": "available",
            "label": "Yankee Air Museum",
            "description": "aviation museum in Michigan",
            "item": "Q1",
            "association": "Yankee Air Museum",
            "retrieved_at": 1,
        }
        host = {
            "state": "unavailable",
            "reason": "host",
            "association": "Yankee Air Museum",
            "retrieved_at": 2,
        }
        refused = {
            "state": "unavailable",
            "reason": "label",
            "association": "Yankee Air Museum",
            "retrieved_at": 3,
        }
        key = entity_rules.cache_key(plain, "Yankee Air Museum")
        for old, expected in (
            ({plain: found, plain + "#History": host}, found),
            ({plain + "#History": host, plain: found}, found),
            ({plain + "#History": host}, None),
            ({plain + "?oldid=1": refused, plain: found}, found),
            ({plain: found, plain + "?oldid=1": refused}, found),
            ({plain + "#History": refused}, refused),
        ):
            with self.subTest(old=sorted(old)), tempfile.TemporaryDirectory() as state:
                (Path(state) / entity_context.FILE).write_text(json.dumps({"version": 1, "entities": old}))
                self.assertEqual(entity_context.load(state).get(key), expected)
        # The newer of two answers of the same standing wins.
        newer = {**refused, "reason": "item", "retrieved_at": 4}
        for old in ({plain: refused, plain + "#History": newer}, {plain + "#History": newer, plain: refused}):
            with self.subTest(old=sorted(old)), tempfile.TemporaryDirectory() as state:
                (Path(state) / entity_context.FILE).write_text(json.dumps({"version": 1, "entities": old}))
                self.assertEqual(entity_context.load(state)[key], newer)
        # A host refusal under a current key is an answer like any other.
        with tempfile.TemporaryDirectory() as state:
            current = {
                entity_rules.cache_key("https://example.com/x", "Acme"): {**host, "association": "Acme"}
            }
            (Path(state) / entity_context.FILE).write_text(json.dumps({"version": 1, "entities": current}))
            self.assertEqual(entity_context.load(state), current)

    def test_a_cache_keyed_by_link_alone_is_rekeyed_by_each_answers_association(self):
        raw = fixtures.kind_event("watchlist_match")
        link = raw["evidence"]["watchlist"]["reference_url"]
        old = {
            link: next(iter(fixtures.watch_entity().values())),
            "https://en.wikipedia.org/wiki/Acme": {
                "state": "unavailable",
                "reason": "label",
                "association": "Acme Air",
                "retrieved_at": 1,
            },
            # A failure recorded no association, so it cannot be rekeyed; the link is looked up again.
            "https://en.wikipedia.org/wiki/Down": {"state": "failed", "failures": 1, "failed_at": 1},
            "https://en.wikipedia.org/wiki/Acme\tacme rail": {
                "state": "unavailable",
                "association": "Acme Rail",
            },
        }
        with tempfile.TemporaryDirectory() as state:
            (Path(state) / entity_context.FILE).write_text(json.dumps({"version": 1, "entities": old}))
            cache = entity_context.load(state)
            self.assertEqual(
                sorted(cache),
                sorted(
                    [
                        entity_rules.cache_key(link, fixtures.WATCH_ASSOCIATION),
                        "https://en.wikipedia.org/wiki/Acme\tacme air",
                        "https://en.wikipedia.org/wiki/Acme\tacme rail",
                    ]
                ),
            )
            self.assertEqual(
                cache["https://en.wikipedia.org/wiki/Acme\tacme air"],
                old["https://en.wikipedia.org/wiki/Acme"],
            )
            self.assertIsNotNone(story_facts.value(fixtures.facts(raw, entities=cache), "wl.who"))
            # The rekeyed answers are no new lookups; the failed link is, and is saved under the new key.
            calls = []

            def counting(url, association, **_):
                calls.append(url)
                return {"state": "unavailable", "reason": "item"}

            links = [
                (link, fixtures.WATCH_ASSOCIATION, "Civ", None),
                ("https://en.wikipedia.org/wiki/Acme", "Acme Air", "Civ", None),
                ("https://en.wikipedia.org/wiki/Down", "Down", "Civ", None),
            ]
            self.assertEqual(entity_context.resolve(state, links, now=2, lookup=counting), 1)
            self.assertEqual(calls, ["https://en.wikipedia.org/wiki/Down"])
            saved = json.loads((Path(state) / entity_context.FILE).read_text())["entities"]
            self.assertTrue(all("\t" in name for name in saved), sorted(saved))

    def test_a_third_failure_waits_30_days(self):
        with tempfile.TemporaryDirectory() as state:
            calls = []

            def failing(url, association, **_):
                calls.append(now)
                raise OSError("down")

            links = [("https://en.wikipedia.org/wiki/Acme_Air", "Acme Air", "Civ", None)]
            day = 86400
            for now in (0, day, 8 * day, 37 * day - 1, 38 * day, 67 * day, 68 * day):
                entity_context.resolve(state, links, now=now, lookup=failing)
            # A day after the first, a week after the second, then 30 days after each later one.
            self.assertEqual(calls, [0, day, 8 * day, 38 * day, 68 * day])
            failed = entity_context.load(state)[key(links[0])]
            self.assertEqual(
                (failed["failures"], failed["failed_at"], failed["state"]), (5, 68 * day, "failed")
            )
            self.assertEqual(failed["association"], "Acme Air")

    def test_a_pass_stops_when_its_15_second_budget_is_spent(self):
        clock = [0.0]

        def slow(url, association, **_):
            clock[0] += 4
            return {"state": "unavailable", "reason": "label"}

        links = [(f"https://en.wikipedia.org/wiki/Acme_{index}", "Acme", "Civ", None) for index in range(6)]
        with tempfile.TemporaryDirectory() as state:
            # Lookups start at 0, 4, 8, and 12 seconds; at 16 the budget is gone.
            self.assertEqual(
                entity_context.resolve(state, links, now=1, lookup=slow, clock=lambda: clock[0]), 4
            )
            self.assertEqual(sorted(entity_context.load(state)), sorted(key(link) for link in links[:4]))

    def test_a_damaged_cache_reads_as_empty(self):
        with tempfile.TemporaryDirectory() as state:
            (Path(state) / entity_context.FILE).write_text("{not json")
            self.assertEqual(entity_context.load(state), {})
