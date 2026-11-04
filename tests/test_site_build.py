"""The static build's snapshot import: story fields, story paths and fixtures."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT / "tools/ghost-theme"
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(TOOLS))
import import_snapshot  # noqa: E402
import reference_fixture  # noqa: E402

from wtf_adsb_publisher import (
    private_terms,  # noqa: E402
    public_events,  # noqa: E402
    story_facts,  # noqa: E402
)

SPEC = importlib.util.spec_from_file_location("site_fixtures_tests", TOOLS / "site_fixtures.py")
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


def setUpModule():
    reference_fixture.install()


def snapshot():
    return fixtures.site_snapshot("published", extra=False)


def story_event(value):
    """The first story with sources and an operator, so every field can be varied."""
    return next(
        event
        for event in value["events"]
        if event.get("story", {}).get("sources") and event.get("context", {}).get("operator")
    )


# The newest fixture story: an R44, a type without a type photo.
CLASS_STORY = "/stories/robinson-r44-n34fx-circling-5-oct-2026/"

# The story engine's optional event fields, all outside the evidence version.
STORY_EXTENSIONS = ("story", "context", "released_at", "content_revised_at")


def redigest(value):
    value = deepcopy(value)
    value["search"] = public_events._search(value["events"])
    value.pop("snapshot_id")
    value["snapshot_id"] = public_events._digest(value)
    return value


class SiteImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = snapshot()

    def test_fixture_mixes_story_and_older_events_and_validates(self):
        events = self.fixture["events"]
        self.assertTrue(any("story" in event and "context" in event for event in events))
        self.assertTrue(any("story" not in event for event in events))
        # Story-less events keep the older shape, but every event has a /stories/ path.
        self.assertTrue(all(public_events.STORY_PATH.fullmatch(event["article_path"]) for event in events))
        loaded = import_snapshot.load(self.fixture)
        self.assertEqual(loaded["snapshot"], self.fixture)
        self.assertEqual(set(loaded["articles"]), {event["id"] for event in events})
        for event in events:
            self.assertIn(f'data-event-id="{event["id"]}"', loaded["articles"][event["id"]])

    def test_story_fields_never_change_the_evidence_version(self):
        event = story_event(self.fixture)
        bare = {key: value for key, value in event.items() if key not in STORY_EXTENSIONS}
        self.assertEqual(public_events.evidence_version(bare), event["evidence_version"])

    def test_only_story_paths_import(self):
        """The exporter's /stories/{slug}/ rule is the build's; a root-level path is refused."""
        value = deepcopy(self.fixture)
        event = story_event(value)
        event["article_path"] = "/" + event["article_path"].split("/")[-2] + "/"
        with self.assertRaises(ValueError):
            import_snapshot.load(redigest(value))

    def test_unknown_or_unsafe_story_fields_are_refused(self):
        changes = [
            lambda e: e["story"].update(extra="x"),
            lambda e: e["story"].update(headline="<b>Bold</b> headline"),
            lambda e: e["story"].update(paragraphs=[]),
            lambda e: e["story"].update(source="human"),
            lambda e: e["story"]["sources"].append(
                {"id": "src.x", "title": "X", "url": "http://example.com/"}
            ),
            lambda e: e["story"]["sources"].append(
                {"id": "src.y", "title": "Y", "url": "https://user@example.com/"}
            ),
            lambda e: e["story"]["sources"].append(
                {"id": "src.z", "title": "Z", "url": "https://host.example.ts.net/"}
            ),
            lambda e: e["story"]["facts"].update({"tp.bad": {"nested": "object"}}),
            lambda e: e["story"]["facts"].update({"Bad Key": 1}),
            lambda e: e["context"].update(owner="Someone"),
            lambda e: e["context"].update(topic="../etc"),
            lambda e: e["context"]["type"].update(slug="Airbus A380"),
            lambda e: e["context"].update(registration_slug="hl7635/x"),
            lambda e: e["context"].update(place="42.68, -83.41"),
            lambda e: e["context"].update(operator={"name": "X", "slug": "x", "owner": "Y"}),
            lambda e: e.update(released_at="2026-09-01T00:00:00Z"),
            lambda e: e.update(released_at="2026-12-01T00:00:00Z"),
            lambda e: e.update(released_at="2026-10-04T12:00:00+00:00"),
            lambda e: e.update(content_revised_at="2026-09-01T00:00:00Z"),
            lambda e: e.update(content_revised_at="2026-12-01T00:00:00Z"),
            lambda e: e.update(content_revised_at=e["content_revised_at"].replace("Z", "+00:00")),
            lambda e: e.pop("released_at"),
            lambda e: e.update(title="A title that is not the headline"),
            lambda e: e.update(summary="A summary that is not the dek."),
            lambda e: e["story"]["facts"].update({"track.positions": 10**400}),
            lambda e: e["story"]["sources"][0].update(retrieved_at="2026-10-01T00:00:00Z"),
            lambda e: e["context"]["operator"].update(designator="aar"),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                value = deepcopy(self.fixture)
                change(story_event(value))
                with self.assertRaises(ValueError):
                    import_snapshot.load(redigest(value))

    def test_values_the_story_engine_exports_are_accepted(self):
        changes = [
            lambda e: e["context"]["operator"].update(designator="3V1"),
            lambda e: e["story"].update(variant="H2/d1:o2.v3"),
            lambda e: e["story"]["sources"][0].update(id="SRC.A388-1"),
            lambda e: e["story"]["sources"][0].update(publisher="P" * 180),
            lambda e: e["story"]["facts"].update({"code.sequence": ["7700", "7600"] * 8}),
            lambda e: e.update(content_revised_at=e["released_at"]),
            # Source URLs the exporter's _public_url accepts, which an older
            # build-side copy of the check refused.
            lambda e: e["story"]["sources"][0].update(url="https://www.airbus.com:443/en/a380"),
            lambda e: e["story"]["sources"][0].update(url="https://docs_host.example.com/a380"),
            lambda e: e["story"]["sources"][0].update(url="https://example.com./a380"),
            lambda e: e["story"]["sources"][0].update(url="https://example.xn--p1ai/a380"),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                value = deepcopy(self.fixture)
                change(story_event(value))
                import_snapshot.load(redigest(value))

    def test_only_a_published_snapshot_names_a_release_time(self):
        review = fixtures.site_snapshot("review", extra=False)
        event = story_event(review)
        self.assertEqual(review["mode"], "review")
        self.assertNotIn("released_at", event)
        event["released_at"] = event["observed_end"]
        with self.assertRaises(ValueError):
            import_snapshot.load(redigest(review))

    def test_a_changed_story_needs_a_new_snapshot_digest(self):
        value = deepcopy(self.fixture)
        dek = "A different, still valid dek for this invented test story."
        story_event(value)["story"]["dek"] = dek
        story_event(value)["summary"] = dek
        with self.assertRaises(ValueError):
            import_snapshot.load(value)
        self.assertEqual(story_event(import_snapshot.load(redigest(value))["snapshot"])["story"]["dek"], dek)

    def test_strict_json_refuses_duplicate_keys_and_non_finite_numbers(self):
        self.assertEqual(import_snapshot.strict_json('{"a": 1}'), {"a": 1})
        for text in ('{"a": 1, "a": 2}', '{"a": NaN}', '{"a": Infinity}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                import_snapshot.strict_json(text)

    def test_older_snapshots_still_import(self):
        older = fixtures.legacy_snapshot()
        self.assertFalse(any("story" in event for event in older["events"]))
        self.assertEqual(import_snapshot.load(json.loads(json.dumps(older)))["snapshot"], older)

    @unittest.skipUnless(shutil.which("node"), "Node runs the site model")
    def test_the_build_routes_every_kind_to_the_engine_topic(self):
        """site-model topicFor and story_facts.topic_for agree, unlisted kinds included."""
        data = json.loads((PROJECT / "src/wtf_adsb_publisher/data/topics.json").read_text())
        # A second table drops each topic's first kind, so those kinds take the fallback.
        trimmed = deepcopy(data)
        for topic in trimmed["topics"].values():
            topic["kinds"] = topic["kinds"][1:]
        kinds = sorted(public_events.KIND_TYPES.items())
        expected = []
        for table in (data, trimmed):
            with patch.object(story_facts, "table", lambda name, table=table: table):
                expected.append([story_facts.topic_for(kind)[0] for kind, _ in kinds])
        script = (
            f"import {{ topicIndex }} from {json.dumps((TOOLS / 'site-model.mjs').as_uri())};"
            "let input = '';"
            "for await (const chunk of process.stdin) input += chunk;"
            "const { tables, kinds } = JSON.parse(input);"
            "console.log(JSON.stringify(tables.map((table) => {"
            "  const index = topicIndex(table);"
            "  return kinds.map(([kind, section]) => index.topicFor({ kind, primary_type: section }));"
            "})));"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            input=json.dumps({"tables": [data, trimmed], "kinds": kinds}),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertNotEqual(expected[0], expected[1])

    def test_withdrawn_stories_import_with_their_path_type_and_time_only(self):
        self.assertEqual(
            [(entry["path"], entry["type"]) for entry in self.fixture["retired"]],
            [(entry["path"], entry["type"]) for entry in fixtures.RETIRED],
        )
        self.assertEqual(fixtures.site_snapshot("review", extra=False)["retired"], [])
        changes = [
            lambda value: value["retired"][0].update(registration="N4172Q"),
            lambda value: value["retired"][0].update(path="/cessna-172-n4172q-12-sep-2026/"),
            lambda value: value["retired"].reverse(),
            lambda value: value["retired"][0].update(type="Cessna 172"),
            lambda value: value["retired"][0].update(withdrawn_at="2027-01-01T00:00:00Z"),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                value = deepcopy(self.fixture)
                change(value)
                with self.assertRaises(ValueError):
                    import_snapshot.load(redigest(value))

    @unittest.skipUnless(shutil.which("node"), "Node runs the site build")
    def test_the_build_writes_a_noindex_redirect_page_for_each_withdrawn_story(self):
        """Each withdrawn path sends readers to its type hub, or the journal; a live story keeps its path."""
        import tempfile

        from airframe_photos import release_violations

        value = deepcopy(self.fixture)
        live = story_event(value)["article_path"]
        value["retired"] = sorted(
            [*value["retired"], {"path": live, "type": None, "withdrawn_at": value["as_of_utc"]}],
            key=lambda entry: entry["path"],
        )
        value = redigest(value)
        artifacts = PROJECT / "artifacts/ghost-theme"
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts) as folder:
            snapshot_file = Path(folder) / "snapshot.json"
            snapshot_file.write_text(json.dumps(value))
            site = Path(folder) / "site"
            # The build validates the snapshot with this Python.
            environment = {
                **os.environ,
                "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
                "SITE_ORIGIN": "https://site.example",
            }
            result = subprocess.run(
                ["node", str(TOOLS / "build.mjs"), "--snapshot", str(snapshot_file), "--output", str(site)],
                cwd=PROJECT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            # The live story wins its path, and the build says so.
            self.assertIn(f"Withdrawn story path {live} is also a live page", result.stderr)
            self.assertNotIn("Story withdrawn", (site / live.strip("/") / "index.html").read_text())
            self.assertIn("2 withdrawn-story redirects", result.stdout)
            routes = json.loads((site / "routes.json").read_text())
            audited = json.loads((site / "audit-routes.json").read_text())
            search = (site / "search-index/index.txt").read_text()
            pages = {
                "/" + str(page.parent.relative_to(site)) + "/": page.read_text()
                for page in site.rglob("index.html")
            }
            for path, (hub, label) in fixtures.RETIRED_HUBS.items():
                with self.subTest(path=path):
                    html = pages[path]
                    self.assertTrue(html.startswith("<!doctype html>\n"))
                    self.assertIn("<title>Story withdrawn</title>", html)
                    self.assertIn('<meta name="robots" content="noindex">', html)
                    self.assertIn(f'<link rel="canonical" href="https://site.example{hub}">', html)
                    self.assertIn(f'<meta http-equiv="refresh" content="0; url={hub}">', html)
                    self.assertIn(f'This story was withdrawn. See <a href="{hub}">{label}</a>.', html)
                    self.assertIn(hub, pages)
                    self.assertNotRegex(html, r"<script|<style|<img|stylesheet|/assets/")
                    self.assertEqual(release_violations(html.encode(), page=True, story=True), [])
                    # Reserved in the route list; out of the audit, the search and every other page.
                    self.assertIn(path, routes)
                    self.assertNotIn(path, audited)
                    self.assertNotIn(path, search)
                    self.assertEqual([other for other, page in pages.items() if f'href="{path}"' in page], [])

    @unittest.skipUnless(shutil.which("node"), "Node runs the site build")
    def test_cards_show_the_class_photo_when_the_type_has_none(self):
        """The newest story's R44 has no type photo: its cards show the local helicopter class photo.

        The home tile carries a visible note and the journal card alt text and a caption that say
        the photo is not the aircraft; the story page keeps its summary figure, the credits page
        lists the photo, and no page loads anything from another origin.
        """
        import tempfile

        from airframe_photos import release_violations

        value = fixtures.site_snapshot("published")
        story = next(event for event in value["events"] if event["article_path"] == CLASS_STORY)
        self.assertEqual(story["aircraft"]["type_code"], "R44")
        self.assertEqual(max(value["events"], key=lambda event: event["observed_start"]), story)
        artifacts = PROJECT / "artifacts/ghost-theme"
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts) as folder:
            snapshot_file = Path(folder) / "snapshot.json"
            snapshot_file.write_text(json.dumps(value))
            manifest = fixtures.site_media(value, Path(folder) / "media")
            site = Path(folder) / "site"
            environment = {
                **os.environ,
                "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
                "SITE_ORIGIN": "https://site.example",
            }
            result = subprocess.run(
                [
                    "node",
                    str(TOOLS / "build.mjs"),
                    "--snapshot",
                    str(snapshot_file),
                    "--media",
                    str(manifest),
                    # The build accepts the fixture type table by its recorded hash.
                    "--type-table-sha256",
                    reference_fixture.document(reference_fixture.AIRCRAFT_TYPES)["content_sha256"],
                    "--output",
                    str(site),
                ],
                cwd=PROJECT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            photo = r"/assets/images/aircraft/class-helicopter-[0-9a-f]{12}\.webp"
            home = (site / "index.html").read_text()
            tile = re.search(rf'<a class="card-image" href="{CLASS_STORY}"[^>]*>(.*?)</a>', home)
            self.assertRegex(tile[1], rf'<img src="{photo}" alt=""')
            self.assertIn('<span class="card-image-note">Representative helicopter</span>', tile[1])
            self.assertEqual(home.count("card-image-note"), 1)
            page = (site / CLASS_STORY.strip("/") / "index.html").read_text()
            self.assertIn('data-media-kind="observation-summary"', page)
            self.assertNotIn("/images/aircraft/class-", page)
            card = json.loads(next(site.glob(f"event-data/*/*/*/cards/{story['id']}.json")).read_text())
            self.assertRegex(
                card["lead"],
                rf'<img src="{photo}" alt="Representative helicopter, not the aircraft in this story: ',
            )
            self.assertIn("Representative helicopter photo (Robinson R44 shown).", card["row"])
            self.assertIn('href="/about/image-credits/#class-helicopter"', card["row"])
            credits = (site / "about/image-credits/index.html").read_text()
            self.assertLess(
                credits.index('<h2 id="class-photos">'), credits.index('<h3 id="class-helicopter">')
            )
            for path in site.rglob("index.html"):
                with self.subTest(path=str(path.relative_to(site))):
                    story_page = path.parent.parent.name == "stories"
                    self.assertEqual(release_violations(path.read_bytes(), page=True, story=story_page), [])

    def test_fixture_media_binds_every_event_without_receiver_fields(self):
        import tempfile

        artifacts = PROJECT / "artifacts/ghost-theme"
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as terms_dir, tempfile.TemporaryDirectory(dir=artifacts) as folder:
            # The media tools refuse the operator's terms too; a fixture file stands in for them.
            terms = Path(terms_dir) / "private-terms.txt"
            terms.write_text("fixture-person|fixture-tailnet\n")
            terms.chmod(0o600)
            with patch.dict(os.environ, {private_terms.ENVIRONMENT: str(terms)}):
                path = fixtures.site_media(self.fixture, folder)
                private = private_terms.compiled()
            self.assertRegex("fixture-person", private)
            manifest = json.loads(path.read_text())
            self.assertEqual(set(manifest["events"]), {event["id"] for event in self.fixture["events"]})
            kinds = {entry["kind"] for entry in manifest["events"].values()}
            self.assertEqual(kinds, {"track", "measurement-chart", "observation-summary"})
            # Every written file, maps and replays included, stays free of receiver fields.
            files = [item for item in Path(folder).rglob("*") if item.is_file()]
            self.assertGreater(len(files), len(manifest["events"]))
            for item in files:
                text = item.read_text()
                self.assertNotRegex(text, private, item.name)
                self.assertNotIn("receiver", text.lower(), item.name)
        with self.assertRaises(ValueError):
            fixtures.site_media(self.fixture, PROJECT / "tests")


# A fixture site identity, passed the way the publisher passes publisher.json's settings.
IDENTITY = {
    "SITE_NAME": "Fixture Findings",
    "SITE_TAGLINE": "Odd readings from invented aircraft",
    "SITE_AUTHOR": "Fixture Author",
    "SITE_SUPPORT_URL": "https://support.example/fixture",
    "SITE_SUPPORT_LABEL": "Back the fixture",
    "SITE_DESCRIPTION": "Invented readings from a fixture receiver",
}
# Text about one deployment that no generic page may hold.
DEPLOYMENT_TEXT = r"Oakland|PTK|Michigan|Nooelec|SAWbird|amazon\.com|DigitalOcean"


class SiteIdentityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node runs the site build")
    def test_the_configured_identity_reaches_the_pages(self):
        """The name reaches the home title, the footer, the error page, and the About page; the
        tagline heads the home page, the description follows it, the author names the author page,
        the support link shows, and the About file replaces the generic introduction."""
        import tempfile

        artifacts = PROJECT / "artifacts/ghost-theme"
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts) as folder:
            site = Path(folder) / "site"
            about = Path(folder) / "about.md"
            about.write_text("## Who runs this\n\nRun by [Fixture Author](/author/fixture-author/).\n")
            result = subprocess.run(
                ["node", str(TOOLS / "build.mjs"), "--output", str(site)],
                cwd=PROJECT,
                env={**os.environ, **IDENTITY, "SITE_ABOUT_FILE": str(about)},
                capture_output=True,
                text=True,
                timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            home = (site / "index.html").read_text()
            title = re.compile(r"<title>([^<]*)</title>")
            self.assertEqual(title.search(home)[1], "Fixture Findings: Odd readings from invented aircraft")
            self.assertIn('<h1 id="home-title">Odd readings from invented aircraft</h1>', home)
            self.assertIn('<a class="footer-name" href="/">Fixture Findings<span', home)
            # The description: under the heading, in the footer, and as the meta description.
            self.assertEqual(home.count("<p>Invented readings from a fixture receiver</p>"), 2)
            self.assertIn(
                '<meta name="description" content="Invented readings from a fixture receiver">', home
            )
            support = re.findall(
                r'<a class="support-cta-link" href="([^"]+)"[^>]*>.*?</svg>\s*([^<]*?) <span', home, re.S
            )
            self.assertEqual(support, [("https://support.example/fixture", "Back the fixture")] * 2)
            self.assertEqual(
                title.search((site / "errors/500/index.html").read_text())[1], "500 | Fixture Findings"
            )
            about = (site / "about/index.html").read_text()
            self.assertIn("<h1>About Fixture Findings</h1>", about)
            self.assertIn(
                '<h2>Who runs this</h2>\n<p>Run by <a href="/author/fixture-author/">Fixture Author</a>.</p>\n'
                '<h2 id="writing">Writing and review</h2>',
                about,
            )
            self.assertNotIn("generic introduction", about)
            self.assertNotRegex(about, DEPLOYMENT_TEXT)
            self.assertIn(
                '<a href="https://support.example/fixture" rel="external noopener noreferrer">', about
            )
            self.assertTrue((site / "author/fixture-author/index.html").is_file())
            self.assertNotRegex(
                "".join(page.read_text() for page in site.rglob("*.html")), r"WTF ADS-B|buymeacoffee|Nearl"
            )

    @unittest.skipUnless(shutil.which("node"), "Node renders the site pages")
    def test_the_field_guide_explains_every_kind_that_can_lead_a_story(self):
        """Each kind a story can lead with has an entry the story's limits link to, with its four
        labeled parts; context kinds, which never lead a story, have none."""
        from wtf_adsb_publisher import publication

        script = (
            f"const {{pages}} = await import({json.dumps((TOOLS / 'demo-content.mjs').as_uri())});"
            "console.log(pages.find((page) => page.slug === 'field-guide').html);"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        html = result.stdout
        anchors = set(re.findall(r'<h4 id="kind-([a-z0-9-]+)">', html))
        self.assertEqual(anchors, {kind.replace("_", "-") for kind in publication.GUIDE_KINDS})
        self.assertIn("prolonged-holding", anchors)
        self.assertNotIn("circling", anchors)
        for entry in re.split(r'<h4 id="kind-', html)[1:]:
            with self.subTest(entry=entry[:40]):
                body = entry[: entry.index("</dl>")]
                self.assertEqual(
                    re.findall(r"<dt>([^<]+)</dt>", body),
                    ["What it is", "Why it can be interesting", "What sets it off", "Common false alarms"],
                )

    @unittest.skipUnless(shutil.which("node"), "Node renders the site pages")
    def test_the_default_about_page_and_description_name_no_deployment(self):
        """Without an About file or a description, the pages say what the site does and how an
        operator supplies their own text, and name no place, hardware, or host. An About file's
        modification time dates the page when it is later than the page's own date."""
        import tempfile

        script = (
            f"const {{pages}} = await import({json.dumps((TOOLS / 'demo-content.mjs').as_uri())});"
            f"const {{indexCopy, topicCopy}} = await import({json.dumps((TOOLS / 'topics.mjs').as_uri())});"
            "const about = pages.find((page) => page.slug === 'about');"
            "console.log(JSON.stringify({about: about.html, updated: about.updated, home: indexCopy.home,"
            " topics: Object.values(topicCopy).map((topic) => topic.description).join(' ')}));"
        )

        def pages(**values):
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script],
                env={key: value for key, value in os.environ.items() if not key.startswith("SITE_")} | values,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        generic = pages()
        self.assertIn("writes about unusual aircraft and odd ADS-B readings", generic["about"])
        self.assertIn("<code>about_file</code>", generic["about"])
        self.assertEqual(generic["home"], "Unusual aircraft and odd ADS-B readings from one receiver")
        theme = json.loads((PROJECT / "themes/wtf-adsb/package.json").read_text())
        self.assertEqual(theme["config"]["custom"]["publication_description"]["default"], generic["home"])
        for name in ("about", "home", "topics"):
            with self.subTest(name=name):
                self.assertNotRegex(generic[name], DEPLOYMENT_TEXT)
        with tempfile.TemporaryDirectory() as folder:
            about = Path(folder) / "about.md"
            about.write_text("Fixture text.\n")
            os.utime(about, (1893456000, 1893456000))
            self.assertEqual(pages(SITE_ABOUT_FILE=str(about))["updated"], "2030-01-01T00:00:00Z")
            os.utime(about, (946684800, 946684800))
            self.assertEqual(pages(SITE_ABOUT_FILE=str(about))["updated"], generic["updated"])
            self.assertEqual(pages(SITE_ABOUT_FILE=os.devnull)["about"], generic["about"])

    @unittest.skipUnless(shutil.which("node"), "Node draws the social cards")
    def test_the_social_cards_draw_the_configured_name(self):
        """The site card and the story cards draw the name, and a new name draws new images."""
        module = (TOOLS / "social-cards.mjs").as_uri()
        script = (
            f"const cards = await import({json.dumps(module)});"
            "const card = {kind: 'card', svg: '<svg xmlns=\"http://www.w3.org/2000/svg\"/>'};"
            "console.log(JSON.stringify({site: cards.drawing({kind: 'site', tagline: 'T'}).body,"
            " card: cards.drawing(card).body, key: cards.jobKey(card)}));"
        )

        def draw(values):
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script],
                env={**{key: value for key, value in os.environ.items() if key not in IDENTITY}, **values},
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        named = draw({"SITE_NAME": "Fixture Findings"})
        self.assertIn("<h1>Fixture Findings<span>.</span></h1><p>T</p>", named["site"])
        self.assertIn("<b>Fixture Findings</b>", named["card"])
        default = draw({})
        self.assertIn("<b>ADS-B findings</b>", default["card"])
        self.assertNotEqual(named["key"], default["key"])


if __name__ == "__main__":
    unittest.main()
