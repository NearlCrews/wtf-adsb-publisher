"""Failed offline build processes stay failed and retain private diagnostics."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cutover_comparison", ROOT / "tools/compare_cutover.py")
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


class ComparisonDiagnosticsTests(unittest.TestCase):
    def test_exit_and_timeout_keep_original_failure_and_bounded_private_stderr(self):
        for check, timeout in ((False, False), (True, False), (True, True)):
            with self.subTest(check=check, timeout=timeout), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "tools").mkdir()
                (root / "tools/run_autopilot.py").write_text(
                    "import subprocess, sys\n"
                    "def publication_pass(*args, **kwargs): pass\n"
                    "def dry_run(config, *args, **kwargs):\n"
                    "    subprocess.run([sys.executable, '-c', config['child']],\n"
                    "                   capture_output=True, check=config['check'],\n"
                    "                   timeout=config['timeout'])\n"
                    "    raise RuntimeError('Build failed')\n"
                )
                ledger = root / "ledger.json"
                ledger.write_text('{"records":[]}')
                child = "import sys,time; sys.stderr.write('x'*70000); sys.stderr.flush(); "
                child += "time.sleep(30)" if timeout else "sys.exit(7)"
                config = root / "config.json"
                config.write_text(
                    json.dumps({"state_dir": str(root), "child": child, "check": check, "timeout": 0.5})
                )
                expected = (
                    subprocess.TimeoutExpired
                    if timeout
                    else subprocess.CalledProcessError
                    if check
                    else RuntimeError
                )
                output = root / "output"
                previous = sys.path[:]
                try:
                    with self.assertRaises(expected):
                        comparison.simulate(root, config, root / "unused", output, "2026-10-09T00:00:00Z")
                finally:
                    sys.path[:] = previous
                receipt = output / "subprocess-failure.json"
                failure = json.loads(receipt.read_text())
                self.assertEqual(failure["returncode"], None if timeout else 7)
                self.assertEqual(failure["timeout"], 0.5 if timeout else None)
                self.assertEqual(failure["stderr"], "x" * 65536)
                self.assertTrue(failure["stderr_truncated"])
                self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
                self.assertFalse((output / "site").exists())
                self.assertEqual(ledger.read_text(), '{"records":[]}')


if __name__ == "__main__":
    unittest.main()
