"""The site identity settings: validation, the production rule, and the environment they export."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import public_events as public  # noqa: E402
from wtf_adsb_publisher import public_prose, site_identity  # noqa: E402

VARIABLES = (
    *site_identity.ENVIRONMENT.values(),
    site_identity.SUPPORT_URL,
    site_identity.SUPPORT_LABEL,
    site_identity.ABOUT_ENVIRONMENT,
)
GOOD_TEXT = ("ADS-B findings", "Fixture Findings", "Café & <Skies>", "x" * 60)
BAD_TEXT = (
    "",
    " Fixture",
    "Fixture ",
    "\ufeffFixture",
    "Fix\ufeffture",
    "Two\nlines",
    "Tab\there",
    "Line\u2028separator",
    "x" * 61,
    None,
    7,
)
GOOD_URLS = (
    "https://example.com",
    "https://support.example/fixture",
    "https://buymeacoffee.com/someone?ref=site#top",
)
BAD_URLS = (
    "http://support.example",
    "https://Support.example",
    "https://support.example:8443/",
    "https://user@support.example",
    "https://192.0.2.1/",
    "https://support.example/a b",
    'https://support.example/"x"',
    "javascript:alert(1)",
    "support.example",
    "https://support.example/" + "x" * 2000,
    "",
)


def environment(**values):
    """os.environ without the identity variables, plus `values`."""
    result = {key: value for key, value in os.environ.items() if key not in VARIABLES}
    result.update(values)
    return result


class SiteIdentityTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, environment(), clear=True))
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def load(self, value):
        config = self.folder / "publisher.json"
        config.write_text(json.dumps(value))
        return public.load_config(config)

    def test_text_settings_are_one_trimmed_line_within_their_limit(self):
        for value in GOOD_TEXT:
            with self.subTest(value=value):
                self.assertEqual(site_identity.validate_text(value, "site_name", 60), value)
        for value in BAD_TEXT:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^site_name must be 1 to 60"):
                site_identity.validate_text(value, "site_name", 60)
        self.assertEqual(site_identity.validate_author("José Ñ"), "José Ñ")
        with self.assertRaisesRegex(ValueError, "^author_name must contain a letter or digit"):
            site_identity.validate_author("---")

    def test_the_support_link_is_an_https_address_and_a_label(self):
        for url in GOOD_URLS:
            with self.subTest(url=url):
                value = {"url": url, "label": "Back the site"}
                self.assertEqual(site_identity.validate_support(value), value)
        for url in BAD_URLS:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "^support_link.url must be"):
                site_identity.validate_support({"url": url, "label": "Back the site"})
        for value in ("https://support.example", {"url": "https://support.example"}, {"label": "x"}, []):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "^support_link must be an object"),
            ):
                site_identity.validate_support(value)
        with self.assertRaisesRegex(ValueError, "^support_link.label must be"):
            site_identity.validate_support({"url": "https://support.example", "label": " "})

    def test_production_requires_a_site_name(self):
        for value in ({"production": True}, {"production": True, "site_name": None}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "site_name is required"):
                self.load({**value, "site_origin": "https://site.example"})
        self.load(
            {"production": True, "site_origin": "https://site.example", "site_name": "Fixture Findings"}
        )
        self.assertEqual(os.environ["SITE_NAME"], "Fixture Findings")
        # Outside production the name is optional, and nothing is exported for it.
        os.environ.pop("SITE_NAME")
        self.load({"production": False})
        self.assertFalse(set(VARIABLES) & set(os.environ))

    def test_load_config_checks_and_exports_every_setting(self):
        about = self.folder / "about.md"
        about.write_text("## Who we are\n\nA fixture receiver.\n")
        self.load(
            {
                "site_name": "Fixture Findings",
                "site_tagline": "Odd readings from invented aircraft",
                "site_description": "Invented readings from a fixture receiver",
                "author_name": "Fixture Author",
                "support_link": {"url": "https://support.example/fixture", "label": "Back the fixture"},
                "about_file": str(about),
            }
        )
        self.assertEqual(
            {key: os.environ[key] for key in VARIABLES},
            {
                "SITE_NAME": "Fixture Findings",
                "SITE_TAGLINE": "Odd readings from invented aircraft",
                "SITE_DESCRIPTION": "Invented readings from a fixture receiver",
                "SITE_AUTHOR": "Fixture Author",
                "SITE_SUPPORT_URL": "https://support.example/fixture",
                "SITE_SUPPORT_LABEL": "Back the fixture",
                "SITE_ABOUT_FILE": str(about),
            },
        )
        for value, message in (
            ({"site_name": "Two\nlines"}, "^site_name must be"),
            ({"site_tagline": "x" * 121}, "^site_tagline must be 1 to 120"),
            ({"site_description": "x" * 161}, "^site_description must be 1 to 160"),
            ({"about_file": "about.md"}, "^about_file must be an absolute path"),
            ({"author_name": "..."}, "^author_name must contain"),
            ({"support_link": {"url": "http://support.example", "label": "x"}}, "^support_link.url must be"),
        ):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                self.load(value)

    def about_cases(self):
        """(path, expected) pairs: the path when the file is accepted, None when it counts as unset,
        or the error's start."""
        good = self.folder / "good.md"
        good.write_bytes("\ufeff# Café\n\nText.\n".encode())
        largest = self.folder / "largest.md"
        largest.write_bytes(b"x" * site_identity.ABOUT_LIMIT)
        large = self.folder / "large.md"
        large.write_bytes(b"x" * (site_identity.ABOUT_LIMIT + 1))
        binary = self.folder / "binary.md"
        binary.write_bytes(b"\xff\xfe text")
        empty = self.folder / "empty.md"
        empty.write_text(" \n")
        return (
            (str(good), str(good)),
            (str(largest), str(largest)),
            (os.devnull, None),
            (str(large), "must be at most"),
            (str(binary), "must be UTF-8 text"),
            (str(empty), "is empty"),
            (str(self.folder / "missing.md"), "cannot be read"),
            (str(self.folder), "must name a regular file"),
            ("relative/about.md", "must be an absolute path"),
        )

    def test_the_about_file_is_utf8_text_within_the_limit_and_dev_null_is_unset(self):
        for path, expected in self.about_cases():
            with self.subTest(path=path):
                if expected is None or expected == path:
                    self.assertEqual(site_identity.about_file(path), expected)
                else:
                    with self.assertRaisesRegex(ValueError, f"^about_file {expected}"):
                        site_identity.about_file(path)
        # /dev/null, the container template's stand-in, exports nothing.
        self.load({"about_file": os.devnull})
        self.assertNotIn(site_identity.ABOUT_ENVIRONMENT, os.environ)
        self.assertIsNone(site_identity.about_sha256({"about_file": os.devnull}))
        self.assertIsNone(site_identity.about_sha256({}))
        good = self.about_cases()[0][0]
        self.assertEqual(
            site_identity.about_sha256({"about_file": good}),
            hashlib.sha256(Path(good).read_bytes()).hexdigest(),
        )

    def test_every_setting_that_reaches_the_pages_changes_the_release_fingerprint(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import run_autopilot

        references = self.folder / "references"
        references.mkdir()
        about = self.folder / "about.md"
        about.write_text("First text.\n")
        snapshot = {"events": [], "as_of_utc": "2026-11-01T00:00:00Z"}
        config = {"preview_ssh_host": "synthetic", "reference_dir": str(references), "about_file": str(about)}
        with patch.object(run_autopilot, "source_digest", return_value="fixed-code"):
            first = run_autopilot.release_fingerprint(snapshot, config)
            self.assertEqual(first, run_autopilot.release_fingerprint(snapshot, config))
            changes = {
                "site_name": "Fixture Findings",
                "site_tagline": "Odd readings",
                "site_description": "Invented readings",
                "author_name": "Fixture Author",
                "support_link": {"url": "https://support.example", "label": "Back us"},
                "contact_email": "contact@example.com",
            }
            for key, value in changes.items():
                with self.subTest(key=key):
                    self.assertNotEqual(
                        first, run_autopilot.release_fingerprint(snapshot, {**config, key: value})
                    )
            about.write_text("Second text.\n")
            second = run_autopilot.release_fingerprint(snapshot, config)
            self.assertNotEqual(first, second)
            # A pinned file in the reference directory counts; another file does not.
            (references / "notes.txt").write_text("not pinned\n")
            self.assertEqual(second, run_autopilot.release_fingerprint(snapshot, config))
            filename = next(iter(run_autopilot.reference_data.FILES.values()))[0]
            (references / filename).write_text("{}\n")
            third = run_autopilot.release_fingerprint(snapshot, config)
            self.assertNotEqual(second, third)
            (references / filename).write_text("[]\n")
            self.assertNotEqual(third, run_autopilot.release_fingerprint(snapshot, config))

    def test_a_story_never_names_the_configured_site(self):
        self.assertFalse(public_prose._names_site("Fixture Findings logged it."))
        os.environ["SITE_NAME"] = "Fixture Findings"
        self.assertTrue(public_prose._names_site("fixture findings logged it."))
        self.assertFalse(public_prose._names_site("Fixture Findingsworth logged it."))

    @unittest.skipUnless(shutil.which("node"), "Node runs the theme tools")
    def test_the_theme_tools_apply_the_same_rules(self):
        module = (ROOT / "tools/ghost-theme/site-settings.mjs").as_uri()
        script = (
            f"const tools = await import({json.dumps(module)});"
            "console.log(JSON.stringify({name: tools.SITE_NAME, tagline: tools.SITE_TAGLINE,"
            " description: tools.SITE_DESCRIPTION, author: tools.authorName(),"
            " support: tools.supportLink(), about: tools.aboutText()?.text ?? null}));"
            "tools.requiredName();"
        )

        def run(**values):
            return subprocess.run(
                ["node", "--input-type=module", "-e", script],
                env=environment(**values),
                capture_output=True,
                text=True,
                timeout=60,
            )

        # Unset, the defaults apply and a production build refuses to start.
        result = run()
        self.assertEqual(
            json.loads(result.stdout.splitlines()[0]),
            {
                "name": site_identity.DEFAULT_NAME,
                "tagline": "Unusual aircraft and odd ADS-B readings",
                "description": "Unusual aircraft and odd ADS-B readings from one receiver",
                "author": "the site",
                "support": None,
                "about": None,
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Production builds need SITE_NAME", result.stderr)
        for value in GOOD_TEXT:
            with self.subTest(value=value):
                result = run(SITE_NAME=value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["name"], value)
        for value in BAD_TEXT[1:-2]:
            with self.subTest(value=value):
                result = run(SITE_NAME=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SITE_NAME must be 1 to 60 characters", result.stderr)
        for url in GOOD_URLS:
            with self.subTest(url=url):
                result = run(SITE_NAME="x", SITE_SUPPORT_URL=url, SITE_SUPPORT_LABEL="Back us")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["support"], {"url": url, "label": "Back us"})
        for url in BAD_URLS[:-1]:
            with self.subTest(url=url):
                result = run(SITE_NAME="x", SITE_SUPPORT_URL=url, SITE_SUPPORT_LABEL="Back us")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SITE_SUPPORT_URL must be an https:// address", result.stderr)
        for values in ({"SITE_SUPPORT_URL": "https://support.example"}, {"SITE_SUPPORT_LABEL": "Back us"}):
            with self.subTest(values=values):
                result = run(SITE_NAME="x", **values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must be set together", result.stderr)
        result = run(SITE_NAME="x", SITE_AUTHOR="---")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SITE_AUTHOR must contain a letter or digit", result.stderr)
        result = run(SITE_NAME="x", SITE_DESCRIPTION="Invented readings")
        self.assertEqual(json.loads(result.stdout)["description"], "Invented readings")
        result = run(SITE_NAME="x", SITE_DESCRIPTION="x" * 161)
        self.assertIn("SITE_DESCRIPTION must be 1 to 160 characters", result.stderr)
        # The About file: Node accepts and refuses the same files as Python.
        for path, expected in self.about_cases():
            with self.subTest(path=path):
                result = run(SITE_NAME="x", SITE_ABOUT_FILE=path)
                if expected is None or expected == path:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    about = json.loads(result.stdout)["about"]
                    self.assertEqual(about, None if expected is None else Path(path).read_text("utf-8-sig"))
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertRegex(result.stderr, "SITE_ABOUT_FILE|ENOENT")

    @unittest.skipUnless(shutil.which("node"), "Node renders the About text")
    def test_the_about_markdown_renders_escaped_html(self):
        module = (ROOT / "tools/ghost-theme/markdown.mjs").as_uri()
        source = "\n".join(
            (
                "# Who runs this",
                "",
                "We run a *small* receiver for **fun** & <science>,",
                "with `readsb` and [our notes](https://notes.example/a?b=1&c=2).",
                "",
                "See [x](https://a.example/*p*/) *now*.",
                "",
                "### Parts",
                "- An antenna",
                "  on the roof",
                "* A [bad link](javascript:void0)",
                "* A [bad data link](data:text/html,x)",
                "1. First",
                "2. [Field guide](/field-guide/)",
                "",
                '<p class="note">Raw <em>HTML</em> stays.</p>',
            )
        )
        result = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                f"const {{renderMarkdown}} = await import({json.dumps(module)});"
                "process.stdout.write(renderMarkdown(process.argv[1]));",
                source,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "<h2>Who runs this</h2>\n"
            "<p>We run a <em>small</em> receiver for <strong>fun</strong> &amp; &lt;science&gt;, with "
            '<code>readsb</code> and <a href="https://notes.example/a?b=1&amp;c=2" '
            'rel="external noopener noreferrer">our notes</a>.</p>\n'
            '<p>See <a href="https://a.example/*p*/" rel="external noopener noreferrer">x</a> '
            "<em>now</em>.</p>\n"
            "<h3>Parts</h3>\n"
            "<ul>\n<li>An antenna on the roof</li>\n<li>A [bad link](javascript:void0)</li>\n"
            "<li>A [bad data link](data:text/html,x)</li>\n</ul>\n"
            '<ol>\n<li>First</li>\n<li><a href="/field-guide/">Field guide</a></li>\n</ol>\n'
            '<p class="note">Raw <em>HTML</em> stays.</p>\n',
        )


if __name__ == "__main__":
    unittest.main()
