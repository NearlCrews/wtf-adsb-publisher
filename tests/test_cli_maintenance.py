"""Private maintenance paths refuse aliases and retain restrictive permissions."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wtf_adsb_publisher import cli, public_events

ROOT = Path(__file__).resolve().parents[1]


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.ledger = public_events.new_ledger("2026-10-01")
        public_events.write_json_atomic(self.source / "ledger.json", self.ledger, private=True)

    def test_state_copy_refuses_symlink_roots_and_contents(self):
        alias = self.root / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        destination = self.root / "copied"
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            cli.copy_state(alias, destination)
        self.assertFalse(destination.exists())
        alias.unlink()
        alias.symlink_to(destination, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            cli.copy_state(self.source, alias)
        self.assertFalse(destination.exists())
        (self.source / "nested").symlink_to(self.source / "ledger.json")
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            cli.copy_state(self.source, destination)
        self.assertFalse(destination.exists())

    def test_state_copy_preserves_content_and_private_permissions(self):
        destination = self.root / "copied"
        self.assertEqual(cli.copy_state(self.source, destination), {"records": 0, "copied": True})
        self.assertEqual(public_events.read_json(destination / "ledger.json"), self.ledger)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination / "ledger.json").stat().st_mode & 0o777, 0o600)

    def test_state_copy_detects_changed_receipt_even_when_ledger_is_unchanged(self):
        receipt = self.source / "receipt.json"
        receipt.write_text('{"reserved": true}')
        original_copy = cli.shutil.copytree

        def changing_copy(*args, **kwargs):
            result = original_copy(*args, **kwargs)
            receipt.write_text('{"reserved": false}')
            return result

        destination = self.root / "copied"
        with patch.object(cli.shutil, "copytree", side_effect=changing_copy):
            with self.assertRaisesRegex(ValueError, "Source state changed"):
                cli.copy_state(self.source, destination)
        self.assertTrue(destination.exists())
        self.assertFalse((destination / "migration-receipts").exists())

    def test_ledger_command_creates_private_parents_under_permissive_umask(self):
        ledger = self.root / ".private/events/ledger.json"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/export_public_events.py"),
                "--ledger",
                str(ledger),
                "init",
                "--available-from",
                "2026-10-01",
            ],
            check=True,
            capture_output=True,
            umask=0o022,
        )
        for directory in (ledger.parent, ledger.parent.parent):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(ledger.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
