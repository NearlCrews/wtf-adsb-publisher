"""The Planespotters photo cache, its response checks and the release's third-party guard, offline."""

import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/ghost-theme"))
import airframe_photos as photos  # noqa: E402

IMAGE = "https://t.plnspttrs.net/09400/1538509_151c8e97b4_280.jpg"
LINK = "https://www.planespotters.net/photo/1538509/n52py-private-aero-commander-690?utm_source=api"


def entry(**changes):
    value = {
        "id": "1538509",
        "thumbnail": {
            "src": "https://t.plnspttrs.net/09400/1538509_151c8e97b4_t.jpg",
            "size": {"width": 200, "height": 133},
        },
        "thumbnail_large": {"src": IMAGE, "size": {"width": 420, "height": 280}},
        "link": LINK,
        "photographer": "Nick Sheeder",
    }
    value.update(changes)
    return value


class Opener:
    """Answers photo API requests in order; an exception is raised instead of answered."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else {"photos": []}
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(reply if isinstance(reply, bytes) else json.dumps(reply).encode())


def limited(code, retry=None):
    headers = Message()
    if retry is not None:
        headers["Retry-After"] = str(retry)
    return HTTPError(photos.API, code, "refused", headers, None)


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def event(identifier, address="A687F1", registration="N52PY", synthetic=False):
    return {
        "id": identifier,
        "synthetic": synthetic,
        "aircraft": {"icao_address": address, "registration": registration},
    }


class ResponseTests(unittest.TestCase):
    def test_the_address_comes_before_the_registration(self):
        self.assertEqual(
            photos.lookup({"icao_address": "a687f1", "registration": "N52PY"}), ("hex-A687F1", "/hex/A687F1")
        )
        self.assertEqual(
            photos.lookup({"icao_address": "~1234AB", "registration": "c-fwol"}),
            ("reg-C-FWOL", "/reg/C-FWOL"),
        )
        self.assertIsNone(photos.lookup({"icao_address": "~1234AB", "registration": "N 52/PY"}))

    def test_a_photo_keeps_every_url_as_returned(self):
        self.assertEqual(
            photos.photo({"photos": [entry()]}, "N52PY"),
            {
                "src": IMAGE,
                "width": 420,
                "height": 280,
                "link": LINK,
                "photographer": "Nick Sheeder",
                "registration": "N52PY",
            },
        )
        # The small thumbnail would be stretched to the figure's width.
        self.assertIsNone(photos.photo({"photos": [entry(thumbnail_large=None)]}, "N52PY"))

    def test_photos_on_other_hosts_of_other_aircraft_or_without_credit_are_dropped(self):
        for changes in (
            {
                "thumbnail_large": {
                    "src": "https://cdn.example.com/a.jpg",
                    "size": {"width": 420, "height": 280},
                }
            },
            {
                "thumbnail_large": {
                    "src": IMAGE.replace("https", "http"),
                    "size": {"width": 420, "height": 280},
                }
            },
            {"thumbnail_large": {"src": IMAGE, "size": {"width": 0, "height": 280}}},
            {"thumbnail_large": {"src": IMAGE, "size": {"width": "420", "height": 280}}},
            {"link": LINK.replace("www.planespotters.net", "planespotters.example")},
            # A placeholder address can lead to another airframe's photo.
            {
                "link": "https://www.planespotters.net/photo/787388/fac1285-fuerza-aerea-colombiana?utm_source=api"
            },
            {"link": "https://www.planespotters.net/photo/1538509/n52pyx-private?utm_source=api"},
            {"photographer": ""},
            {"photographer": "x" * 101},
            {"photographer": "Name\nInjected"},
            {"link": "https://[www.planespotters.net/photo/1538509/n52py-x"},
        ):
            with self.subTest(changes=changes):
                self.assertIsNone(photos.photo({"photos": [entry(**changes)]}, "N52PY"))
        # A later usable photo is taken instead.
        self.assertEqual(photos.photo({"photos": [entry(photographer=""), entry()]}, "N52PY")["src"], IMAGE)
        self.assertIsNone(photos.photo({"photos": [entry()]}, None))
        self.assertIsNone(photos.photo({"error": "x"}, "N52PY"))

    def test_vintage_us_registrations_match_their_painted_prefix(self):
        link = "https://www.planespotters.net/photo/1707500/nc32000-private-waco-upf-7?utm_source=api"
        self.assertEqual(photos.photo({"photos": [entry(link=link)]}, "N32000")["link"], link)
        self.assertIsNone(photos.photo({"photos": [entry(link=link)]}, "G-32000"))


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "state" / "planespotters"
        self.clock = Clock()
        self.slept = []

    def cache(self, opener, **options):
        return photos.PhotoCache(
            self.directory, fetch=True, clock=self.clock, sleep=self.slept.append, opener=opener, **options
        )

    def test_a_pass_fetches_once_per_aircraft_and_stores_only_the_response(self):
        opener = Opener({"photos": [entry()]})
        snapshot = {"events": [event("a"), event("b"), event("c", synthetic=True)]}
        with patch.dict(os.environ, {"SITE_ORIGIN": "https://site.example"}):
            found = photos.story_photos(snapshot, self.cache(opener))
        self.assertEqual(set(found), {"a", "b"})
        [request] = opener.requests
        self.assertEqual(request.full_url, photos.API + "/hex/A687F1")
        self.assertEqual(request.get_header("User-agent"), "WTF-ADSB/1.0 (+https://site.example/about/)")
        saved = json.loads((self.directory / "hex-A687F1.json").read_text())
        self.assertEqual(saved, {"fetched_at": self.clock.now, "response": {"photos": [entry()]}})
        self.assertEqual(stat.S_IMODE((self.directory / "hex-A687F1.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        # Nothing is fetched again within 20 hours, and the photo stays.
        self.clock.now += 19 * 3600
        again = Opener()
        self.assertEqual(set(photos.story_photos(snapshot, self.cache(again))), {"a", "b"})
        self.assertEqual(again.requests, [])

    def test_responses_expire_after_24_hours_and_are_deleted(self):
        photos.story_photos({"events": [event("a")]}, self.cache(Opener({"photos": [entry()]})))
        self.clock.now += 24 * 3600
        reader = photos.PhotoCache(self.directory, clock=self.clock)
        self.assertEqual(photos.story_photos({"events": [event("a")]}, reader), {})
        self.assertTrue((self.directory / "hex-A687F1.json").exists())
        # A pass that may fetch deletes it first, even when the refresh then
        # fails; the failure record that takes its place holds no photo.
        photos.story_photos({"events": [event("a")]}, self.cache(Opener(URLError("down"))))
        record = json.loads((self.directory / "hex-A687F1.json").read_text())
        self.assertEqual(record["response"], {"photos": []})
        self.assertEqual(record["failed_at"], self.clock.now)

    def test_aging_entries_refresh_oldest_first_within_the_budget(self):
        snapshot = {"events": [event(str(n), address=f"A0000{n}", registration=f"N{n}A") for n in range(4)]}
        photos.story_photos({"events": snapshot["events"][:2]}, self.cache(Opener()))
        self.clock.now += 2 * 3600
        photos.story_photos(snapshot, self.cache(Opener()))
        # Entries 0 and 1 are now 22 hours old, entries 2 and 3 are 20.
        self.clock.now += 20 * 3600
        opener = Opener()
        self.slept.clear()
        photos.story_photos(snapshot, self.cache(opener, budget=3))
        self.assertEqual(
            [request.full_url[-6:] for request in opener.requests], ["A00000", "A00001", "A00002"]
        )
        self.assertEqual(self.slept, [photos.REQUEST_GAP] * 2)

    def test_a_failing_aircraft_waits_its_turn_without_holding_up_the_rest(self):
        snapshot = {"events": [event(str(n), address=f"A0000{n}", registration=f"N{n}A") for n in range(3)]}
        # A request refused for one aircraft moves on to the next.
        opener = Opener(limited(404), {"photos": [entry(link=LINK.replace("n52py", "n1a"))]})
        cache = self.cache(opener)
        self.assertEqual(set(photos.story_photos(snapshot, cache)), {"1"})
        self.assertEqual(len(opener.requests), 3)
        self.assertIsNone(cache.failure)
        # A server error stops the pass; that aircraft then goes last.
        self.clock.now += 21 * 3600
        photos.story_photos(snapshot, self.cache(Opener(limited(503))))
        self.clock.now += photos.ERROR_BACKOFF
        opener = Opener()
        photos.story_photos(snapshot, self.cache(opener))
        self.assertEqual([request.full_url[-6:] for request in opener.requests], ["A00001", "A00002"])
        # A transport failure counts against the aircraft the same way: after
        # the backoff the others get their turn, and it is retried only once
        # its own wait has passed.
        self.clock.now += 21 * 3600
        opener = Opener(URLError("timed out"))
        cache = self.cache(opener)
        photos.story_photos(snapshot, cache)
        self.assertEqual([request.full_url[-6:] for request in opener.requests], ["A00000"])
        self.assertEqual(cache.failure, "URLError")
        self.clock.now += photos.ERROR_BACKOFF
        opener = Opener()
        photos.story_photos(snapshot, self.cache(opener))
        self.assertEqual([request.full_url[-6:] for request in opener.requests], ["A00001", "A00002"])
        self.clock.now += photos.REFRESH_AFTER
        opener = Opener()
        photos.story_photos(snapshot, self.cache(opener))
        self.assertEqual([request.full_url[-6:] for request in opener.requests][0], "A00000")

    def test_a_pass_stops_at_its_time_budget(self):
        snapshot = {"events": [event(str(n), address=f"A0000{n}", registration=f"N{n}A") for n in range(4)]}
        clock = self.clock

        class Slow(Opener):
            def open(self, request, timeout):
                clock.now += 61
                return super().open(request, timeout)

        opener = Slow()
        photos.story_photos(snapshot, self.cache(opener))
        self.assertEqual(len(opener.requests), 2)

    def test_only_the_newest_stories_aircraft_are_looked_up(self):
        events = [
            {
                **event(str(n), address=f"A0000{n}", registration=f"N{n}A"),
                "released_at": f"2026-10-0{n + 1}T00:00:00Z",
            }
            for n in range(3)
        ]
        opener = Opener()
        with patch.object(photos, "MAX_AIRCRAFT", 2):
            photos.story_photos({"events": events}, self.cache(opener))
        self.assertEqual(sorted(request.full_url[-6:] for request in opener.requests), ["A00001", "A00002"])

    def test_aircraft_without_a_registration_are_not_looked_up(self):
        opener = Opener()
        photos.story_photos({"events": [event("a", registration=None)]}, self.cache(opener))
        self.assertEqual(opener.requests, [])

    def test_rate_limits_stop_requests_for_hours(self):
        snapshot = {"events": [event("a"), event("b", address="A00001", registration="N1A")]}
        opener = Opener(limited(429))
        cache = self.cache(opener)
        self.assertEqual(photos.story_photos(snapshot, cache), {})
        self.assertEqual((len(opener.requests), cache.failure), (1, "HTTP 429"))
        self.clock.now += photos.LIMITED_BACKOFF - 1
        later = Opener()
        photos.story_photos(snapshot, self.cache(later))
        self.assertEqual(later.requests, [])
        self.clock.now += 1
        photos.story_photos(snapshot, self.cache(later))
        self.assertEqual(len(later.requests), 2)
        # A longer Retry-After is honored; a 403 counts the same.
        opener = Opener(limited(403, retry=10 * 3600))
        cache = self.cache(opener)
        self.clock.now += 21 * 3600
        photos.story_photos(snapshot, cache)
        self.assertEqual(cache.backoff(), self.clock.now + 10 * 3600)

    def test_other_failures_back_off_briefly_and_keep_fresh_photos(self):
        snapshot = {"events": [event("a")]}
        for failure in (
            limited(500),
            URLError("down"),
            b"not json",
            b"x" * (photos.MAX_RESPONSE + 1),
            {"photo": []},
        ):
            with self.subTest(failure=repr(failure)[:40]):
                shutil.rmtree(self.directory, ignore_errors=True)
                photos.story_photos(snapshot, self.cache(Opener({"photos": [entry()]})))
                self.clock.now += 21 * 3600
                cache = self.cache(Opener(failure))
                # The 21-hour-old response is still within its 24 hours.
                self.assertEqual(set(photos.story_photos(snapshot, cache)), {"a"})
                self.assertIsNotNone(cache.failure)
                self.assertEqual(cache.backoff(), self.clock.now + photos.ERROR_BACKOFF)

    def test_a_read_only_cache_never_requests_or_writes(self):
        opener = Opener()
        reader = photos.PhotoCache(self.directory, clock=self.clock, opener=opener)
        self.assertEqual(photos.story_photos({"events": [event("a")]}, reader), {})
        self.assertEqual(opener.requests, [])
        self.assertFalse(self.directory.exists())

    def test_redirects_are_refused(self):
        with self.assertRaises(ValueError):
            photos.NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://example.com/")


class ReleaseGuardTests(unittest.TestCase):
    FIGURE = (
        f'<figure class="airframe-photo column"><a href="{LINK}"><img src="{IMAGE}" alt="x"></a></figure>'
    )

    def setUp(self):
        # The site's own origin, which a page may load from.
        environment = patch.dict(os.environ, {"SITE_ORIGIN": "https://site.example"})
        environment.start()
        self.addCleanup(environment.stop)

    def check(self, html, story=True):
        return photos.release_violations(html.encode(), page=True, story=story)

    def test_a_page_may_load_only_an_airframe_image_inside_its_figure(self):
        self.assertEqual(
            self.check(
                '<link rel="canonical" href="https://site.example/a/"><link rel="alternate" href="https://example.com/rss">'
                '<link rel="stylesheet" href="/assets/css/screen.css?v=1"><a href="https://example.com/">x</a>'
                '<img src="https://site.example/social/a.png"><img src="https://www.site.example/b.png"><img src="data:image/png;base64,AA">'
                + self.FIGURE
            ),
            [],
        )
        for html in (
            f'<img src="{IMAGE}">',
            f'<figure class="type-photo"><img src="{IMAGE}"></figure>',
            f'<figure class="airframe-photo"><img srcset="{IMAGE} 1x"></figure>',
            '<figure class="airframe-photo"><img src="https://cdn.example.com/a.jpg"></figure>',
            '<script src="https://cdn.example.com/a.js"></script>',
            '<link rel="preload" href="//fonts.example.com/a.woff2">',
            '<div style="background: url(https://example.com/a.png)"></div>',
            '<style>@import "https://example.com/a.css";</style>',
            '<iframe src="https://example.com/"></iframe>',
            '<img src="http://site.example/a.png">',
            '<img src="https://example.com/a.png">',
            '<video poster="https://example.com/a.jpg"></video>',
            '<img src="\\\\cdn.example.com/a.png">',
            '<img src="/\\cdn.example.com/a.png">',
            '<img src="https&colon;//cdn.example.com/a.png">',
            '<img src="https://[x/">',
            f'<figure class="airframe-photo"><img src="https://cdn.example.com/a.jpg" src="{IMAGE}"></figure>',
            '<svg><image href="https://cdn.example.com/a.png"/></svg>',
            '<svg><use href="https://cdn.example.com/a.svg#x"/></svg>',
            "<div style=\"background-image: image-set('/a.png' 1x, 'https://cdn.example.com/b.png' 2x)\"></div>",
        ):
            with self.subTest(html=html):
                self.assertTrue(self.check(html))

    def test_the_image_host_appears_only_as_an_airframe_image_on_a_story_page(self):
        self.assertEqual(len(self.check(self.FIGURE, story=False)), 2)
        for html in (
            f'<meta property="og:image" content="{IMAGE}">',
            f'<script type="application/ld+json">{{"image": "{IMAGE}"}}</script>',
            f"<p>{IMAGE}</p>",
        ):
            with self.subTest(html=html):
                self.assertEqual(
                    self.check(self.FIGURE + html), ["Planespotters image host outside an airframe photo"]
                )

    def test_stylesheets_load_nothing_from_elsewhere(self):
        def check(text):
            return photos.release_violations(text.encode(), style=True)

        self.assertEqual(check('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'), [])
        self.assertEqual(
            check(".a { background: url(/assets/a.png) } .b { background: url(data:image/png;base64,AA) }"),
            [],
        )
        self.assertEqual(len(check(".a { background: url(https://cdn.example.com/a.png) }")), 1)
        self.assertEqual(len(check('@import "https://cdn.example.com/a.css";')), 1)

    def test_files_other_than_pages_never_name_planespotters(self):
        self.assertTrue(photos.release_violations(json.dumps({"src": IMAGE}).encode(), page=False))
        self.assertTrue(photos.release_violations(b"<a>Planespotters.net</a>", page=False))
        self.assertEqual(photos.release_violations(b'{"title": "A Boeing 747"}', page=False), [])


class BoundaryTests(unittest.TestCase):
    def test_the_proxy_allows_exactly_the_photo_image_hosts(self):
        caddy = (ROOT / "deploy/ghost/Caddyfile").read_text()
        policy = re.search(r'Content-Security-Policy "([^"]+)"', caddy)[1]
        directives = {part.split()[0]: part.split()[1:] for part in policy.split(";")}
        self.assertEqual(
            directives["img-src"],
            ["'self'", "data:", *sorted(f"https://{host}" for host in photos.IMAGE_HOSTS)],
        )
        for name in ("default-src", "script-src", "connect-src", "font-src", "media-src"):
            self.assertNotIn("plnspttrs", " ".join(directives[name]))
        build = (ROOT / "tools/ghost-theme/airframe-photos.mjs").read_text()
        hosts = re.search(r"AIRFRAME_IMAGE_HOSTS = \[([^\]]*)\]", build)[1]
        self.assertEqual(set(re.findall(r'"([^"]+)"', hosts)), set(photos.IMAGE_HOSTS))
        self.assertIn(f'AIRFRAME_LINK_HOST = "{photos.LINK_HOST}"', build)

    def test_photo_data_never_reaches_the_story_engine_or_its_writers(self):
        for path in (ROOT / "src").rglob("*"):
            if path.is_file() and path.suffix in (".py", ".json", ".md", ".txt", ".js", ".html"):
                with self.subTest(path=path.name):
                    self.assertNotRegex(
                        path.read_text(errors="replace"), r"(?i)planespotters|plnspttrs|airframe_photos"
                    )


if __name__ == "__main__":
    unittest.main()
