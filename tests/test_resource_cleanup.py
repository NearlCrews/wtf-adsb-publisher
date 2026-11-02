"""Request and connection failures release resources without changing error semantics."""

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from wtf_adsb_publisher import budget_store, explain


class ResourceCleanupTests(unittest.TestCase):
    def test_failed_budget_initialization_closes_the_connection(self):
        for stage in ("execute", "executescript", "chmod"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                database = Mock(spec=sqlite3.Connection)
                database.execute.return_value = []
                error = sqlite3.OperationalError("initialization failed")
                if stage != "chmod":
                    getattr(database, stage).side_effect = error
                with (
                    patch.object(budget_store.sqlite3, "connect", return_value=database),
                    patch.object(budget_store.os, "chmod", side_effect=error if stage == "chmod" else None),
                    self.assertRaises(sqlite3.OperationalError) as raised,
                ):
                    budget_store.initialize(Path(directory) / "budget.sqlite3")
                self.assertIs(raised.exception, error)
                database.close.assert_called_once_with()

    def test_http_refusal_closes_its_response_and_preserves_the_error(self):
        response = io.BytesIO(b"refused")
        error = HTTPError("https://api.openai.com/v1/responses", 429, "rate limited", {}, response)
        with self.assertRaises(HTTPError) as raised:
            explain.request(b"{}", "unused", timeout=1, opener=Mock(side_effect=error))
        self.assertIs(raised.exception, error)
        self.assertTrue(response.closed)
        self.assertTrue(explain.refused(error))


if __name__ == "__main__":
    unittest.main()
