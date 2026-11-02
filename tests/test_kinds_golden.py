"""Every kind-keyed table equals the value, container type, and order recorded before the registry."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from golden_support import assert_tables_match

from tools import kinds_golden

FIXTURE = ROOT / "tests" / "fixtures" / "kinds-golden.json"


def seeded_dump(seed):
    """The golden tool's dump in a fresh interpreter under the given string-hashing seed."""
    run = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools" / "kinds_golden.py")],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": seed},
    )
    return json.loads(run.stdout)


class KindTablesMatchTheRecording(unittest.TestCase):
    def test_every_kind_table_equals_its_recorded_value_under_any_hash_seed(self):
        # This process's own seed and two fixed ones: no table may take its order from string hashing.
        recorded = json.loads(FIXTURE.read_text())
        for label, dumped in (
            ("this process", kinds_golden.dump()),
            ("seed 1", seeded_dump("1")),
            ("seed 2", seeded_dump("2")),
        ):
            assert_tables_match(self, recorded, dumped, dump=label)


if __name__ == "__main__":
    unittest.main()
