"""The public-text deny list: built-in patterns, the operator's terms file, and literal terms."""

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
from wtf_adsb_publisher import private_terms  # noqa: E402

SAMPLES = (
    "fixture-person",
    "FIXTURE-PERSON",
    "host.example-tailnet.ts.net",
    "100.64.1.2",
    "100.128.1.2",
    "Reached 100.64.1.2.",
    "100.64.1.2.3",
    "receiver_lat",
    "tskey-abc",
    "a quiet afternoon",
    "Synthetic Host",
)


class PrivateTermsTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {private_terms.ENVIRONMENT: ""}))
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.terms = self.folder / "private-terms.txt"
        self.terms.write_text("fixture-person|fixture-host\n")
        self.terms.chmod(0o600)

    def test_builtin_patterns_apply_without_configuration(self):
        private = private_terms.compiled()
        self.assertEqual(private_terms.pattern({}), private_terms.builtin())
        for text in (
            "host.example.ts.net",
            "tskey-abc",
            "100.64.0.1",
            "100.127.255.9",
            "Reached 100.64.0.1.",
            "site_longitude",
        ):
            with self.subTest(text=text):
                self.assertRegex(text, private)
        for text in ("fixture-person", "100.128.0.1", "a quiet afternoon", "flight 100.64", "100.64.0.1.5"):
            with self.subTest(text=text):
                self.assertNotRegex(text, private)

    def test_file_and_literal_terms_join_the_builtin_patterns(self):
        config = {"private_terms_file": str(self.terms), "private_terms": ["a.b", ""]}
        private = private_terms.compiled(config)
        self.assertTrue(private.pattern.startswith(private_terms.builtin() + "|"))
        for text in ("Fixture-Person", "fixture-host", "A.B", "tskey-1"):
            with self.subTest(text=text):
                self.assertRegex(text, private)
        self.assertNotRegex("aXb", private)

    def test_environment_names_the_file_when_the_configuration_does_not(self):
        os.environ[private_terms.ENVIRONMENT] = str(self.terms)
        self.assertRegex("fixture-person", private_terms.compiled())
        self.assertRegex("fixture-person", private_terms.compiled({"private_terms": []}))
        other = self.folder / "other.txt"
        other.write_text("other-owner")
        configured = private_terms.compiled({"private_terms_file": str(other)})
        self.assertRegex("other-owner", configured)
        self.assertNotRegex("fixture-person", configured)

    def test_an_empty_file_is_refused(self):
        # An operator who wants no terms of their own deletes the key.
        for text in ("", "\n", "\ufeff\n"):
            self.terms.write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "empty"):
                private_terms.pattern({"private_terms_file": str(self.terms)})

    def test_a_byte_order_mark_is_not_part_of_the_first_term(self):
        self.terms.write_text("\ufefffixture-person|fixture-host\n", encoding="utf-8")
        private = private_terms.compiled({"private_terms_file": str(self.terms)})
        self.assertRegex("Seen by fixture-person.", private)

    def test_malformed_settings_are_refused(self):
        lines = self.folder / "lines.txt"
        lines.write_text("one\ntwo\n")
        invalid = self.folder / "invalid.txt"
        invalid.write_text("(unclosed")
        # Valid alone, but a global flag cannot follow the built-in patterns.
        flagged = self.folder / "flagged.txt"
        flagged.write_text("(?i)fixture-person")
        # A stray | matches every text, which would refuse every release.
        everything = self.folder / "everything.txt"
        everything.write_text("fixture-person|")
        for config in (
            {"private_terms_file": "relative/terms.txt"},
            {"private_terms_file": str(lines)},
            {"private_terms_file": str(invalid)},
            {"private_terms_file": str(flagged)},
            {"private_terms_file": str(everything)},
            {"private_terms_file": 7},
            {"private_terms": "a single string"},
            {"private_terms": [3]},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                private_terms.pattern(config)
        with self.assertRaises(OSError):
            private_terms.pattern({"private_terms_file": str(self.folder / "missing.txt")})

    def test_export_names_the_configured_file_for_child_processes(self):
        private_terms.export({})
        self.assertEqual(os.environ[private_terms.ENVIRONMENT], "")
        private_terms.export({"private_terms_file": str(self.terms)})
        self.assertEqual(os.environ[private_terms.ENVIRONMENT], str(self.terms))
        with self.assertRaises(ValueError):
            private_terms.export({"private_terms_file": "relative.txt"})
        # Valid alone, but not after the built-in patterns: refused at load time.
        flagged = self.folder / "flagged.txt"
        flagged.write_text("(?i)fixture-person\n")
        with self.assertRaisesRegex(ValueError, "does not join the built-in patterns"):
            private_terms.export({"private_terms_file": str(flagged)})
        self.assertEqual(os.environ[private_terms.ENVIRONMENT], str(self.terms))

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_theme_tools_match_what_the_python_checks_match(self):
        script = (
            f"import {{ privateNames }} from {json.dumps((ROOT / 'tools/ghost-theme/paths.mjs').as_uri())};"
            "const pattern = privateNames();"
            f"console.log(JSON.stringify({json.dumps(SAMPLES)}.map((text) => pattern.test(text))));"
        )
        marked = self.folder / "marked.txt"
        marked.write_text("\ufefffixture-person|fixture-host\n", encoding="utf-8")
        for value in ("", str(self.terms), str(marked)):
            with self.subTest(file=value):
                environment = {**os.environ, private_terms.ENVIRONMENT: value}
                result = subprocess.run(
                    ["node", "--input-type=module", "-e", script],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                with patch.dict(os.environ, {private_terms.ENVIRONMENT: value}):
                    private = private_terms.compiled()
                self.assertEqual(json.loads(result.stdout), [bool(private.search(text)) for text in SAMPLES])

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_theme_tools_refuse_the_files_python_refuses(self):
        script = (
            f"import {{ privateNames }} from {json.dumps((ROOT / 'tools/ghost-theme/paths.mjs').as_uri())};"
            "privateNames();"
        )
        relative = "relative/private-terms.txt"
        # (file text or None for a relative path, Node's message, Python's message)
        cases = (
            ("\n", "is empty", "is empty"),
            ("fixture-person|", "matches the empty string", "matches the empty string"),
            (
                "fixture-person\nfixture-host",
                "one regular expression on one line",
                "one regular expression on one line",
            ),
            ("fixture-(owner", "Invalid regular expression", "not a valid regular expression"),
            (None, "must be an absolute path", "must be an absolute path"),
        )
        for text, node_message, python_message in cases:
            path = relative if text is None else str(self.terms)
            if text is not None:
                self.terms.write_text(text)
            with self.subTest(text=text):
                result = subprocess.run(
                    ["node", "--input-type=module", "-e", script],
                    env={**os.environ, private_terms.ENVIRONMENT: path},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(node_message, result.stderr)
                with self.assertRaisesRegex(ValueError, python_message):
                    private_terms.pattern({"private_terms_file": path})


if __name__ == "__main__":
    unittest.main()
