"""Authentication, inventory completeness and conflict protection without a server."""

import base64
import hashlib
import hmac
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wtf_adsb_publisher.ghost_client import GhostClient, GhostError, NoRedirects


class GhostClientTests(unittest.TestCase):
    def client(self, opener):
        return GhostClient("https://admin.example.test", "a" * 24 + ":" + "ab" * 32, opener=opener)

    def test_signed_short_lived_token_and_fresh_update_timestamp(self):
        captured = []

        def opener(request, timeout):
            captured.append(request)
            self.assertEqual(timeout, 30)
            return io.StringIO('{"posts":[{"id":"confirmed"}]}')

        with patch("wtf_adsb_publisher.ghost_client.time.time", return_value=1000):
            self.client(opener).save({"status": "published"}, {"id": "b" * 24, "updated_at": "fresh"})
        request = captured[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(json.loads(request.data)["posts"][0]["updated_at"], "fresh")
        encoded = request.get_header("Authorization").removeprefix("Ghost ")
        head, payload, signature = encoded.split(".")

        def decode(value):
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

        self.assertEqual(json.loads(decode(head))["kid"], "a" * 24)
        self.assertEqual(json.loads(decode(payload)), {"iat": 1000, "exp": 1300, "aud": "/admin/"})
        self.assertEqual(
            decode(signature),
            hmac.new(bytes.fromhex("ab" * 32), (head + "." + payload).encode(), hashlib.sha256).digest(),
        )

    def test_rejects_incomplete_or_changing_inventory(self):
        for pages in (
            [{"posts": [{"id": "one"}], "meta": {"pagination": {"total": 2, "next": None}}}],
            [
                {"posts": [{"id": "one"}], "meta": {"pagination": {"total": 2, "next": 2}}},
                {"posts": [{"id": "two"}], "meta": {"pagination": {"total": 3, "next": None}}},
            ],
            [{"posts": [{"id": "one"}, {"id": "one"}], "meta": {"pagination": {"total": 2, "next": None}}}],
        ):
            with self.subTest(pages=pages):
                iterator = iter(pages)
                client = self.client(
                    lambda request, timeout, iterator=iterator: io.StringIO(json.dumps(next(iterator)))
                )
                with self.assertRaises(GhostError):
                    client.inventory()

    def test_slugs_in_use_pages_through_posts_and_pages_in_every_status(self):
        responses = {
            ("posts", "1"): {
                "posts": [{"id": "p1", "slug": "a-post"}, {"id": "p2", "slug": "a-draft"}],
                "meta": {"pagination": {"total": 3, "next": 2}},
            },
            ("posts", "2"): {
                "posts": [{"id": "p3", "slug": "scheduled"}],
                "meta": {"pagination": {"total": 3, "next": None}},
            },
            ("pages", "1"): {
                "pages": [{"id": "g1", "slug": "about"}],
                "meta": {"pagination": {"total": 1, "next": None}},
            },
        }
        requested = []

        def opener(request, timeout):
            parts = urlsplit(request.full_url)
            query = parse_qs(parts.query)
            resource = parts.path.rstrip("/").rsplit("/", 1)[-1]
            requested.append((resource, query))
            self.assertEqual(query["fields"], ["id,slug"])
            self.assertNotIn("filter", query)
            return io.StringIO(json.dumps(responses[(resource, query["page"][0])]))

        self.assertEqual(self.client(opener).slugs_in_use(), {"a-post", "a-draft", "scheduled", "about"})
        self.assertEqual([resource for resource, _ in requested], ["posts", "posts", "pages"])
        broken = {
            **responses,
            ("pages", "1"): {"pages": [{"id": "g1"}], "meta": {"pagination": {"total": 1, "next": None}}},
        }
        responses.clear()
        responses.update(broken)
        with self.assertRaises(GhostError):
            self.client(opener).slugs_in_use()

    def test_errors_do_not_disclose_server_payload_or_credentials(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 401, "private payload", {}, None)

        with self.assertRaisesRegex(GhostError, r"^Ghost request failed \(HTTP 401\)$") as caught:
            self.client(opener).inventory()
        # The status alone tells the publisher whether only this post was refused.
        self.assertEqual(caught.exception.status, 401)
        self.assertIsNone(NoRedirects().redirect_request(None, None, 302, "", {}, "https://other.test"))

        def unreachable(request, timeout):
            raise TimeoutError("timed out")

        with self.assertRaises(GhostError) as caught:
            self.client(unreachable).inventory()
        self.assertIsNone(caught.exception.status)


if __name__ == "__main__":
    unittest.main()
