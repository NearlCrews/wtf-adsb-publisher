"""The files shared with the engine repository match the digests recorded in sync-state.json.

tools/record_shared_digests.py records the sha256 of each shared copy; the engine and the
publisher must carry identical copies.
"""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ("change-batch-v1.schema.json", "manifest-v1.schema.json", "observation-v1.schema.json")
SHARED = (
    "src/wtf_adsb_publisher/wire.py",
    "src/wtf_adsb_publisher/kinds.py",
    "src/wtf_adsb_publisher/enrichment_rules.py",
    *(f"src/wtf_adsb_publisher/contracts/{name}" for name in SCHEMAS),
    *(f"contracts/{name}" for name in SCHEMAS),
)


class SharedDigestTests(unittest.TestCase):
    def test_shared_files_match_the_recorded_digests(self):
        recorded = json.loads((ROOT / "sync-state.json").read_text(encoding="utf-8"))["shared_digests"]
        self.assertEqual(set(recorded), set(SHARED), "every shared file has a recorded digest")
        for path in SHARED:
            with self.subTest(path=path):
                digest = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                self.assertEqual(digest, recorded[path], f"{path} differs from its recorded digest")

    def test_a_shared_module_without_dest_sha256_is_named(self):
        spec = importlib.util.spec_from_file_location(
            "record_digests", ROOT / "tools/record_shared_digests.py"
        )
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "shared.py").write_text("VALUE = 1\n")
            state = root / "sync-state.json"
            state.write_text(
                json.dumps(
                    {"shared_digests": {"shared.py": "0" * 64}, "modules": {"shared": {"dest": "shared.py"}}}
                )
            )
            with self.assertRaisesRegex(SystemExit, "missing dest_sha256 for shared"):
                tool.record(state, root)


if __name__ == "__main__":
    unittest.main()
