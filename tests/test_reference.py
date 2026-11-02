"""Pinned reference loading: byte bounds, the reference directory, and its pins."""

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import reference_fixture

from wtf_adsb_publisher import public_events, reference


class PinnedReferenceTests(unittest.TestCase):
    def test_exact_size_limit_accepts_valid_pinned_content(self):
        content = {"schema_version": 1, "label": "synthetic"}
        data = {**content, "content_sha256": reference.canonical_hash(content)}
        raw = json.dumps(data).encode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            path.write_bytes(raw)
            self.assertEqual(reference.pinned_json(path, max_bytes=len(raw)), data)
            with self.assertRaisesRegex(ValueError, "size bound"):
                reference.pinned_json(path, max_bytes=len(raw) - 1)

    def test_oversized_reference_is_bounded_and_its_file_is_closed(self):
        limit = 64

        class BoundedReader(io.BytesIO):
            def read(self, size=-1):
                if not 0 <= size <= limit + 1:
                    raise AssertionError("Reference read exceeded its memory bound")
                return super().read(size)

        source = BoundedReader(b"x" * (limit * 100))
        with patch.object(Path, "open", return_value=source):
            with self.assertRaisesRegex(ValueError, "size bound"):
                reference.pinned_json("synthetic.json", max_bytes=limit)
        self.assertTrue(source.closed)


class ReferenceDirectoryTests(unittest.TestCase):
    """Files resolve in WTF_ADSB_REFERENCE_DIR first, then in the bundle; three never ship."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(reference.ENVIRONMENT, None)
        files = patch.dict(reference.FILES)
        files.start()
        self.addCleanup(files.stop)
        reference.clear_cache()
        self.addCleanup(reference.clear_cache)

    def use(self, directory):
        os.environ[reference.ENVIRONMENT] = str(directory)
        reference.clear_cache()

    def assert_unavailable(self):
        self.assertIsNone(reference.type_info("C172"))
        self.assertIsNone(reference.allocation("a00001"))
        self.assertIsNone(reference.registration_parts("N123AB"))
        self.assertIsNone(reference.notable_variant("Boeing E-6B Mercury"))
        self.assertIsNone(reference.mission_callsign("SAM27"))
        # Built-in rules still answer.
        self.assertTrue(reference.is_group_designator("ZZZZ"))
        self.assertTrue(reference.rotorcraft("R44"))
        self.assertEqual(reference.us_registration("a00001"), "N1")

    def test_the_bundle_no_longer_holds_the_operator_supplied_files(self):
        for name in reference.OPERATOR_SUPPLIED:
            self.assertFalse((reference.DATA / reference.FILES[name][0]).exists(), name)
            self.assertEqual(reference.candidates(name), [])
        self.assertEqual(reference.candidates("limits"), [reference.DATA / "reference-limits-v1.json"])

    def test_fixture_files_in_the_reference_directory_answer_lookups(self):
        reference.FILES.update(reference_fixture.write(self.directory))
        self.use(self.directory)
        self.assertEqual(reference.type_info("C172")["class"], "airplane")
        self.assertTrue(reference.rotorcraft("R44"))
        self.assertEqual(reference.earliest_year("C172"), 1951)
        self.assertEqual(reference.allocation("a00001"), {"country": "US", "name": "United States"})
        self.assertTrue(reference.military_allocation("ae0412"))
        self.assertEqual(reference.registration_parts("N123AB"), ("N", "123AB"))
        self.assertEqual(reference.registration_countries("D-AIMA"), ("DE",))
        self.assertTrue(reference.operator_designator("UAL"))
        self.assertEqual(reference.notable_variant("boeing  e-6b mercury")["label"], "E-6B Mercury")
        self.assertTrue(reference.mission_callsign("SAM27"))
        self.assertTrue(all(entry["loaded"] for entry in reference.status().values()))

    def test_no_reference_directory_keeps_the_built_in_rules(self):
        with self.assertLogs("discoveries.reference", "WARNING") as logs:
            self.assert_unavailable()
        self.assertIn("no reference directory is set", "\n".join(logs.output))
        status = reference.status()
        self.assertFalse(status["identity"]["loaded"])
        self.assertTrue(status["limits"]["loaded"])

    def test_a_missing_reference_directory_fails_soft(self):
        self.use(self.directory / "absent")
        with self.assertLogs("discoveries.reference", "WARNING") as logs:
            self.assert_unavailable()
        self.assertIn("not found", "\n".join(logs.output))
        # The bundled files still load.
        self.assertIsNotNone(reference.beacon_code("7700"))
        self.assertIsNotNone(reference.dataset("limits"))

    def test_a_file_that_differs_from_its_pin_counts_as_missing(self):
        pins = reference_fixture.write(self.directory)
        reference.FILES.update({name: (filename, "0" * 64) for name, (filename, _) in pins.items()})
        self.use(self.directory)
        with self.assertLogs("discoveries.reference", "WARNING") as logs:
            self.assert_unavailable()
        output = "\n".join(logs.output)
        self.assertIn("refused; treated as missing", output)
        self.assertIn("differs from the reviewed version", output)
        self.assertIn("differs from the reviewed version", reference.status()["identity"]["error"])

    def test_the_reference_directory_takes_precedence_for_a_bundled_file(self):
        filename, _ = reference.FILES["beacon-codes"]
        bundled = json.loads((reference.DATA / filename).read_text())
        content = {key: value for key, value in bundled.items() if key != "content_sha256"}
        content["dataset_version"] = "operator-beacon-codes"
        raw = json.dumps(reference_fixture.document(content)).encode()
        (self.directory / filename).write_bytes(raw)
        self.use(self.directory)
        # A copy that differs from its pin is skipped for the bundled one.
        with self.assertLogs("discoveries.reference", "WARNING"):
            self.assertEqual(reference.dataset("beacon-codes")["dataset_version"], bundled["dataset_version"])
        reference.FILES["beacon-codes"] = (filename, hashlib.sha256(raw).hexdigest())
        reference.clear_cache()
        self.assertEqual(reference.dataset("beacon-codes")["dataset_version"], "operator-beacon-codes")

    def test_an_operator_supplied_file_is_never_read_from_the_bundle(self):
        pins = reference_fixture.write(self.directory)
        reference.FILES.update(pins)
        with (
            patch.object(reference, "DATA", self.directory),
            self.assertLogs("discoveries.reference", "WARNING"),
        ):
            self.assertIsNone(reference.type_info("C172"))


class ReferenceConfigTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(reference.ENVIRONMENT, None)
        self.addCleanup(reference.clear_cache)

    def load(self, **settings):
        path = self.directory / "publisher.json"
        path.write_text(json.dumps(settings))
        return public_events.load_config(path)

    def test_load_config_exports_the_reference_directory(self):
        generation = reference.GENERATION
        self.load(reference_dir="/srv/references")
        self.assertEqual(os.environ[reference.ENVIRONMENT], "/srv/references")
        self.assertEqual(reference.reference_dir(), Path("/srv/references"))
        self.assertGreater(reference.GENERATION, generation, "a changed directory reloads the data")

    def test_without_the_key_the_environment_is_left_alone(self):
        self.load()
        self.assertNotIn(reference.ENVIRONMENT, os.environ)
        os.environ[reference.ENVIRONMENT] = "/srv/from-environment"
        self.load()
        self.assertEqual(os.environ[reference.ENVIRONMENT], "/srv/from-environment")

    def test_a_relative_or_non_string_directory_is_refused(self):
        for value in ("references", "", " /srv/references", 7, ["/srv"]):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "reference_dir"):
                self.load(reference_dir=value)
        self.assertNotIn(reference.ENVIRONMENT, os.environ)


if __name__ == "__main__":
    unittest.main()
