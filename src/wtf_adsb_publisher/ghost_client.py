"""Small server-side Ghost client. Credentials and raw API errors stay private."""

import base64
import hashlib
import hmac
import json
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class GhostError(RuntimeError):
    """A failed Ghost request; `status` is the HTTP status when Ghost answered."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# What a Ghost write and its confirmation can raise: Ghost's refusal (GhostError, a
# RuntimeError), a network failure, or a reply that does not confirm the change.
GHOST_ERRORS = (RuntimeError, OSError, ValueError, KeyError)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class GhostClient:
    def __init__(self, origin, key, *, opener=None):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ghost needs an HTTPS admin origin")
        self.origin = origin.rstrip("/")
        self.key_id, secret = key.strip().split(":", 1)
        self.secret = bytes.fromhex(secret)
        if len(self.key_id) != 24 or len(self.secret) != 32:
            raise ValueError("Invalid Ghost integration key")
        self.opener = opener or build_opener(NoRedirects()).open

    def request(self, path, *, method="GET", body=None):
        if not path.startswith("/ghost/api/admin/") or ".." in path:
            raise ValueError("Invalid Ghost API path")
        now = int(time.time())
        header = _encode(json.dumps({"alg": "HS256", "typ": "JWT", "kid": self.key_id}).encode())
        payload = _encode(json.dumps({"iat": now, "exp": now + 300, "aud": "/admin/"}).encode())
        signed = header + "." + payload
        token = signed + "." + _encode(hmac.new(self.secret, signed.encode(), hashlib.sha256).digest())
        request = Request(
            self.origin + path,
            method=method,
            data=None if body is None else json.dumps(body, allow_nan=False).encode(),
            headers={
                "Authorization": "Ghost " + token,
                "Accept-Version": "v6.0",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            error.close()
            raise GhostError(f"Ghost request failed (HTTP {error.code})", error.code) from None
        except (OSError, ValueError) as error:
            raise GhostError(f"Ghost request failed ({type(error).__name__})") from None

    def _browse(self, resource, options):
        """Every item of one Admin API collection, refusing a list that changes while paging."""
        items, identifiers, page, total = [], set(), 1, None
        while page:
            result = self.request(
                f"/ghost/api/admin/{resource}/?"
                + urlencode({"page": page, "limit": 100, **options, "order": "id asc"})
            )
            pagination = result["meta"]["pagination"]
            if total is not None and total != pagination["total"]:
                raise GhostError("Ghost inventory changed during pagination")
            total = pagination["total"]
            if type(total) is not int or total < 0:
                raise GhostError("Invalid Ghost inventory total")
            batch = result[resource]
            batch_ids = {item["id"] for item in batch}
            if identifiers & batch_ids or len(batch_ids) != len(batch) or len(items) + len(batch) > total:
                raise GhostError("Inconsistent Ghost inventory")
            identifiers.update(batch_ids)
            items.extend(batch)
            following = pagination["next"]
            if following is not None and (
                type(following) is not int or following != page + 1 or not batch or len(items) >= total
            ):
                raise GhostError("Invalid Ghost pagination")
            page = following
        if len(items) != total or len({item["id"] for item in items}) != total:
            raise GhostError("Incomplete Ghost inventory")
        return items

    def inventory(self):
        """Every post in every status, with its content and tags."""
        return self._browse("posts", {"formats": "html,lexical", "include": "tags,authors"})

    def pages(self):
        """Every page in every status, with its content and tags."""
        return self._browse("pages", {"formats": "html,lexical", "include": "tags,authors"})

    def slugs_in_use(self):
        """Slugs of every post and page in any status; Ghost keeps them unique across both."""
        slugs = set()
        for resource in ("posts", "pages"):
            for item in self._browse(resource, {"fields": "id,slug"}):
                if not isinstance(item.get("slug"), str) or not item["slug"]:
                    raise GhostError("Ghost returned an item without a slug")
                slugs.add(item["slug"])
        return slugs

    def save(self, post, current=None):
        if current:
            return self.request(
                "/ghost/api/admin/posts/" + current["id"] + "/?save_revision=true",
                method="PUT",
                body={"posts": [{**post, "updated_at": current["updated_at"]}]},
            )["posts"][0]
        return self.request("/ghost/api/admin/posts/", method="POST", body={"posts": [post]})["posts"][0]
