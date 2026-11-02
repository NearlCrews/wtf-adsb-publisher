"""The public site origin: validation, defaults, derived names, and the environment it exports."""

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
from wtf_adsb_publisher import registry_http, site_origin  # noqa: E402

GOOD = ("https://example.com", "https://site.example", "https://www.site.example", "https://a-b.c1.example")
BAD = (
    "http://site.example",
    "https://site.example/",
    "https://site.example/blog",
    "https://site.example:8443",
    "https://Site.example",
    "https://user@site.example",
    "https://site.example?x=1",
    "https://site.example#top",
    "https://-site.example",
    "https://site..example",
    "site.example",
    " https://site.example",
    "https://site.example ",
    "https://site.example\n",
    "https://exämple.com",
    "https://xn--exmple-cua.com.",
    "https://192.0.2.1",
    "https://10.0.0.1",
    "https://0x7f000001",
    "https://1.0x7f",
    # The empty string stays last: the theme tools treat it as unset.
    "",
)

GOOD_CONTACTS = ("contact@example.com", "first.last+site@Mail.Site.example", "a_b-c@x1.example")
BAD_CONTACTS = (
    "Site <contact@site.example>",
    "contact@site",
    "contact@site.example.",
    "contact@-site.example",
    ".contact@site.example",
    "con..tact@site.example",
    "contact @site.example",
    "contact@site.example\n",
    "contäct@site.example",
    "mailto:contact@site.example",
    "a" * 65 + "@site.example",
    # The empty string stays last: the theme tools treat it as unset.
    "",
)


class SiteOriginTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop(site_origin.ENVIRONMENT, None)

    def test_only_a_bare_lower_case_https_origin_is_accepted(self):
        for value in GOOD:
            with self.subTest(value=value):
                self.assertEqual(site_origin.validate(value), value)
        for value in (*BAD, None, 1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "site_origin must be"):
                site_origin.validate(value)

    def test_errors_name_the_setting_the_value_came_from(self):
        with self.assertRaisesRegex(ValueError, "^site_origin must be"):
            site_origin.configured({"site_origin": "https://site.example/"})
        os.environ[site_origin.ENVIRONMENT] = "https://site.example/"
        with self.assertRaisesRegex(ValueError, "^SITE_ORIGIN must be"):
            site_origin.configured()

    def test_the_contact_address_is_optional_and_plain(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop(site_origin.CONTACT_ENVIRONMENT, None)
        self.assertIsNone(site_origin.contact_email())
        site_origin.export_contact({})
        self.assertNotIn(site_origin.CONTACT_ENVIRONMENT, os.environ)
        for value in GOOD_CONTACTS:
            with self.subTest(value=value):
                self.assertEqual(site_origin.contact_email({"contact_email": value}), value)
        for value in (*BAD_CONTACTS, None, 7):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^contact_email must be"):
                site_origin.validate_contact(value)
        site_origin.export_contact({"contact_email": "contact@site.example"})
        self.assertEqual(os.environ[site_origin.CONTACT_ENVIRONMENT], "contact@site.example")
        self.assertEqual(site_origin.contact_email(), "contact@site.example")
        os.environ[site_origin.CONTACT_ENVIRONMENT] = "Site <contact@site.example>"
        with self.assertRaisesRegex(ValueError, "^SITE_CONTACT_EMAIL must be"):
            site_origin.contact_email()

    def test_load_config_exports_the_contact_address(self):
        self.enterContext(patch.dict(os.environ))
        os.environ.pop(site_origin.CONTACT_ENVIRONMENT, None)
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = folder / "publisher.json"
        config.write_text(json.dumps({"contact_email": "contact@site.example"}))
        public.load_config(config)
        self.assertEqual(os.environ[site_origin.CONTACT_ENVIRONMENT], "contact@site.example")
        config.write_text(json.dumps({"contact_email": "contact@"}))
        with self.assertRaisesRegex(ValueError, "contact_email must be"):
            public.load_config(config)

    @unittest.skipUnless(shutil.which("node"), "Node runs the theme tools")
    def test_the_theme_tools_check_the_contact_address_and_omit_it_when_unset(self):
        site = (ROOT / "tools/ghost-theme/site-settings.mjs").as_uri()
        seo = (ROOT / "tools/ghost-theme/seo.mjs").as_uri()
        script = (
            f"const tools = await import({json.dumps(site)});"
            f"const seo = await import({json.dumps(seo)});"
            "console.log(JSON.stringify({email: tools.contactEmail(),"
            " org: seo.organization('https://site.example', null).email ?? null}));"
        )

        def run(value):
            environment = {
                key: item for key, item in os.environ.items() if key != site_origin.CONTACT_ENVIRONMENT
            }
            if value is not None:
                environment[site_origin.CONTACT_ENVIRONMENT] = value
            return subprocess.run(
                ["node", "--input-type=module", "-e", script],
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )

        for value in (None, ""):
            with self.subTest(value=value):
                result = run(value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"email": None, "org": None})
        for value in GOOD_CONTACTS:
            with self.subTest(value=value):
                result = run(value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"email": value, "org": value})
        for value in BAD_CONTACTS[:-1]:
            with self.subTest(value=value):
                result = run(value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SITE_CONTACT_EMAIL must be a plain email address", result.stderr)

    def test_the_example_origin_and_project_contact_stand_in_until_one_is_set(self):
        self.assertIsNone(site_origin.configured())
        self.assertEqual(site_origin.get(), "https://example.com")
        self.assertEqual(site_origin.hosts(), {"example.com", "www.example.com"})
        self.assertEqual(site_origin.user_agent(), f"WTF-ADSB/1.0 (+{site_origin.FALLBACK_CONTACT})")
        # The bounded registry client, which imports nothing from the package, has the same default.
        self.assertEqual(registry_http.USER_AGENT, site_origin.user_agent())

    def test_the_configuration_wins_over_the_environment(self):
        os.environ[site_origin.ENVIRONMENT] = "https://env.example"
        self.assertEqual(site_origin.get(), "https://env.example")
        config = {"site_origin": "https://www.site.example"}
        self.assertEqual(site_origin.get(config), "https://www.site.example")
        self.assertEqual(site_origin.hosts(config), {"site.example", "www.site.example"})
        self.assertEqual(
            site_origin.user_agent(config=config), "WTF-ADSB/1.0 (+https://www.site.example/about/)"
        )
        self.assertEqual(
            site_origin.user_agent("Map/1.0", config), "Map/1.0 (+https://www.site.example/about/)"
        )
        os.environ[site_origin.ENVIRONMENT] = "https://site.example/"
        with self.assertRaises(ValueError):
            site_origin.get()

    def test_load_config_exports_the_origin_and_production_requires_one(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = folder / "publisher.json"
        config.write_text(
            json.dumps({"production": True, "site_origin": "https://site.example", "site_name": "Site"})
        )
        self.assertEqual(public.load_config(config)["site_origin"], "https://site.example")
        self.assertEqual(os.environ[site_origin.ENVIRONMENT], "https://site.example")
        os.environ.pop(site_origin.ENVIRONMENT)
        config.write_text(json.dumps({"production": False}))
        public.load_config(config)
        self.assertNotIn(site_origin.ENVIRONMENT, os.environ)
        for value in ({"production": True}, {"production": True, "site_origin": None}):
            config.write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "site_origin is required"):
                public.load_config(config)
        for value in BAD:
            config.write_text(json.dumps({"site_origin": value}))
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "site_origin must be"):
                public.load_config(config)
        self.assertNotIn(site_origin.ENVIRONMENT, os.environ)

    @unittest.skipUnless(shutil.which("node"), "Node runs the theme tools")
    def test_the_theme_tools_apply_the_same_rules(self):
        module = (ROOT / "tools/ghost-theme/site-settings.mjs").as_uri()
        script = (
            f"const tools = await import({json.dumps(module)});"
            "console.log(JSON.stringify({origin: tools.ORIGIN, agent: tools.userAgent(),"
            " map: tools.userAgent('Map/1.0')}));"
            "tools.requiredOrigin();"
        )

        def run(value):
            environment = {key: item for key, item in os.environ.items() if key != site_origin.ENVIRONMENT}
            if value is not None:
                environment[site_origin.ENVIRONMENT] = value
            return subprocess.run(
                ["node", "--input-type=module", "-e", script],
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )

        for value in GOOD:
            with self.subTest(value=value):
                result = run(value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(result.stdout),
                    {
                        "origin": value,
                        "agent": site_origin.user_agent(config={"site_origin": value}),
                        "map": site_origin.user_agent("Map/1.0", {"site_origin": value}),
                    },
                )
        for value in BAD[:-1]:
            with self.subTest(value=value):
                result = run(value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SITE_ORIGIN must be an https:// origin", result.stderr)
        # Unset, previews name the example origin and a production build refuses to start.
        result = run(None)
        self.assertEqual(json.loads(result.stdout.splitlines()[0])["origin"], "https://example.com")
        self.assertEqual(json.loads(result.stdout.splitlines()[0])["agent"], site_origin.user_agent())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Production builds need SITE_ORIGIN", result.stderr)


if __name__ == "__main__":
    unittest.main()
