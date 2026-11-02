"""The trimmed registry client: one bounded GET for a JSON answer, and no application imports."""

import ast
import io
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import registry_http

SOURCE = ROOT / "src" / "wtf_adsb_publisher" / "registry_http.py"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener(body=b"", error=None):
    def open_(request, timeout):
        opener.request, opener.timeout = request, timeout
        if error is not None:
            raise error
        return Response(body)

    return open_


class RegistryClientTests(unittest.TestCase):
    def test_it_imports_nothing_from_the_application_or_a_sibling_module(self):
        tree = ast.parse(SOURCE.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, "registry_http imports a sibling module")
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
        self.assertNotIn("app", imported)
        self.assertLessEqual(imported, {"json", "urllib"})
        # Only the client entity_context uses is left: no backoff gate, journal state or loop.
        self.assertEqual(
            {name for name in vars(registry_http) if not name.startswith("_")}
            - {"json", "HTTPError", "URLError", "HTTPRedirectHandler", "Request", "build_opener"},
            {"USER_AGENT", "TIMEOUT", "Unavailable", "NoRedirect", "get_json"},
        )

    def test_get_json_reads_one_bounded_answer(self):
        self.assertEqual(
            registry_http.get_json("https://example.test/a", opener(b'{"a": 1}'), max_bytes=64, timeout=3),
            {"a": 1},
        )
        self.assertEqual(opener.timeout, 3)
        self.assertEqual(opener.request.get_method(), "GET")
        self.assertEqual(opener.request.get_header("User-agent"), registry_http.USER_AGENT)
        self.assertIsNone(registry_http.get_json("https://example.test/a", opener(b"  "), max_bytes=64))

    def test_failures_raise_unavailable_without_reading_the_error(self):
        for body, error in (
            (b"x" * 65, None),
            (b"not json", None),
            (b"", HTTPError("https://example.test/a", 503, "busy", {}, io.BytesIO(b"secret"))),
            (b"", OSError("down")),
        ):
            with self.subTest(body=body[:8], error=error), self.assertRaises(registry_http.Unavailable):
                registry_http.get_json("https://example.test/a", opener(body, error), max_bytes=64)
        absent = HTTPError("https://example.test/a", 404, "missing", {}, io.BytesIO(b""))
        self.assertIsNone(
            registry_http.get_json("https://example.test/a", opener(error=absent), max_bytes=64, absent={404})
        )

    def test_redirects_are_never_followed(self):
        self.assertIsNone(registry_http.NoRedirect().redirect_request())


if __name__ == "__main__":
    unittest.main()
