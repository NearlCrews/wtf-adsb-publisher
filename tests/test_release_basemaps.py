"""The release path builds its basemap cache from the theme tools' module."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(1, str(ROOT / "tools/ghost-theme"))

import run_autopilot  # noqa: E402


class ReleaseBasemapsTests(unittest.TestCase):
    def test_release_basemaps_is_the_theme_tools_cache_with_downloads_allowed(self):
        import basemap

        with tempfile.TemporaryDirectory() as directory:
            cache = run_autopilot.release_basemaps(Path(directory) / "basemap-cache")
        self.assertIsInstance(cache, basemap.BasemapCache)
        self.assertTrue(cache.fetch)
        self.assertTrue(cache.webp)


if __name__ == "__main__":
    unittest.main()
