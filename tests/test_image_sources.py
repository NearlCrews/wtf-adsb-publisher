"""Reject build inputs that escape the checked source tree through directory links."""

import tempfile
import unittest
from pathlib import Path

from tools.check_image import OPERATOR_FILES, source_digest
from wtf_adsb_publisher import reference

ROOT = Path(__file__).resolve().parents[1]
# The single files the image copies besides its source directories (tools/check_image.py).
COPIED = (
    "Dockerfile",
    ".dockerignore",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "package.json",
    "package-lock.json",
    "deploy/container/entrypoint.py",
    "deploy/container/build_metadata.py",
    "deploy/ghost-preview/routes.yaml",
)


class ImageSourceTests(unittest.TestCase):
    def test_directory_symlinks_are_rejected_before_hashing(self):
        for relative in (".", "src", "src/plugin", "deploy", "deploy/container", "deploy/ghost-preview"):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "checkout"
                outside = Path(directory) / "outside"
                outside.mkdir()
                (outside / "plugin.py").write_text("external = True\n")
                link = root / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symbolic links"):
                    source_digest(root)

    def test_staged_sync_merges_are_neither_sent_nor_hashed(self):
        # The sync tool stages `<module>.sync-merge` beside a module it could not merge.
        self.assertIn("**/*.sync-merge", (ROOT / ".dockerignore").read_text().splitlines())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (*COPIED, "src/module.py"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(name)
            before = source_digest(root)
            (root / "src" / "module.py.sync-merge").write_text("<<<<<<< ours\n")
            self.assertEqual(source_digest(root), before)

    def test_operator_supplied_reference_files_are_neither_sent_nor_hashed(self):
        self.assertEqual({reference.FILES[name][0] for name in reference.OPERATOR_SUPPLIED}, OPERATOR_FILES)
        ignored = (ROOT / ".dockerignore").read_text().splitlines()
        for name in OPERATOR_FILES:
            self.assertIn(f"**/{name}", ignored)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (*COPIED, "src/module.py"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(name)
            before = source_digest(root)
            for name in OPERATOR_FILES:
                (root / "src" / name).write_text("{}")
            self.assertEqual(source_digest(root), before)


if __name__ == "__main__":
    unittest.main()
