"""The bounded HTTP client entity_context uses for public registry lookups.

A trimmed copy of the live project's registry_http: only get_json and the
redirect guard it needs. Each request is one bounded GET for a small JSON answer
that never follows a redirect. The enrichment workers' backoff gate, journal
state and loop stay with the engine; this module opens no journal and imports
nothing from an application module.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

# The default User-Agent, naming the project. Callers pass one naming their site
# (site_origin.user_agent); this module imports nothing from the package.
USER_AGENT = "WTF-ADSB/1.0 (+https://github.com/NearlCrews/wtf-adsb-publisher)"
TIMEOUT = 10


class Unavailable(Exception):
    """A service could not answer: network trouble, a server error, a refusal, or a body
    that was oversized or not JSON. ``status`` is the HTTP status, when there was one."""

    def __init__(self, status=None):
        super().__init__("Service unavailable")
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        # A redirect target is not the service's answer, and never receives a credential.
        return None


def get_json(url, opener=None, *, max_bytes, absent=frozenset(), timeout=TIMEOUT, user_agent=USER_AGENT):
    """The JSON value a GET returns, or None for an empty body or an HTTP status in `absent`.

    Raises Unavailable for any other failure, a body over max_bytes, or one that
    is not JSON. The error text of a failed response is never read.
    """
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"}, method="GET")
    opener = opener or build_opener(NoRedirect()).open
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
    except HTTPError as error:
        error.close()
        if error.code in absent:
            return None
        raise Unavailable(error.code) from None
    except (URLError, OSError, ValueError) as error:
        raise Unavailable() from error
    if len(raw) > max_bytes:
        raise Unavailable()
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise Unavailable() from None
