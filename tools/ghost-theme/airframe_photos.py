"""Airframe photos from the Planespotters.net public photo API, for story pages.

The site follows the API terms (https://www.planespotters.net/photo/api):

- Reader browsers fetch each image from the `thumbnail_large` URL the API
  returned. Nothing here downloads, stores, proxies or rewrites an image, and
  no image goes into a social card or a generated file.
- Each photo shows its photographer's credit beside it and links to its
  Planespotters page with the returned `link`, as a plain anchor.
- Requests carry a descriptive User-Agent with a contact URL, one at a time,
  within a request and time budget per pass, for at most MAX_AIRCRAFT recent
  aircraft. A 403 or 429 stops them for hours, other failures for half an hour.
- Only the JSON responses are cached, in the private state directory, and for
  at most 24 hours. Every URL is used exactly as returned.
- Photo data reaches the rendered article HTML and nothing else: not the
  snapshot, feeds, search, journal data or the AI story writer.

`release_violations` is the release guard's half of this: a published file may
load a resource from another origin only for an airframe photo's image, in its
figure on a story page, and Planespotters' image host appears nowhere else.
"""

import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from wtf_adsb_publisher import site_origin  # noqa: E402

API = "https://api.planespotters.net/pub/photos"
# The hosts the API returns for thumbnails and photo pages. deploy/ghost/Caddyfile
# allows exactly these image hosts in its Content-Security-Policy.
IMAGE_HOSTS = frozenset({"t.plnspttrs.net"})
LINK_HOST = "www.planespotters.net"
# The terms allow a response to be cached for 24 hours. Passes refresh an entry
# after 20, so a photo stays up while the publisher runs every half hour.
MAX_AGE = 24 * 3600
REFRESH_AFTER = 20 * 3600
# The newest stories' aircraft, at most this many, are looked up: a few hundred
# requests a day at most. Older stories keep their type photo.
MAX_AIRCRAFT = 300
REQUEST_GAP = 1.0
TIMEOUT = 10
BUDGET = 60
BUDGET_SECONDS = 120
LIMITED_BACKOFF = 6 * 3600
ERROR_BACKOFF = 30 * 60
MAX_BACKOFF = 7 * 24 * 3600
MAX_RESPONSE = 64 * 1024
HEX = re.compile(r"[0-9A-F]{6}")
REGISTRATION = re.compile(r"[A-Z0-9][A-Z0-9-]{0,9}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Photo API requests must not redirect")


def lookup(aircraft):
    """The cache key and API path for an aircraft: its ICAO address, else its registration.

    A registration can move between airframes, so the address comes first.
    """
    address = str(aircraft.get("icao_address") or "").upper()
    if HEX.fullmatch(address):
        return f"hex-{address}", f"/hex/{address}"
    registration = str(aircraft.get("registration") or "").upper()
    if REGISTRATION.fullmatch(registration):
        return f"reg-{registration}", f"/reg/{quote(registration)}"
    return None


def _split(url):
    """A URL's parts, with backslashes read as slashes the way browsers read them, or None."""
    try:
        return urlsplit(url.strip().replace("\\", "/"))
    except ValueError:
        return None


def _https(url, hosts):
    parts = _split(url) if isinstance(url, str) else None
    return bool(
        parts
        and parts.scheme == "https"
        and parts.hostname in hosts
        and parts.netloc == parts.hostname
        and parts.path
    )


def _size(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 2000


def _names_registration(link, registration):
    """Whether a photo page's address names the registration, as /photo/{id}/{registration}-...

    Some aircraft broadcast a placeholder address, which Planespotters matches to
    another airframe; a page that names a different registration is dropped.
    Vintage U.S. aircraft can carry an NC, NL, NR or NX registration, which
    Planespotters lists in that form.
    """
    match = re.fullmatch(r"/photo/\d+/([a-z0-9-]+)", _split(link).path)
    if not match:
        return False
    names = [registration.lower()]
    if re.fullmatch(r"N[0-9][0-9A-Z]*", registration.upper()):
        names += ["n" + prefix + registration[1:].lower() for prefix in "clrx"]
    return any(match[1].startswith(name + "-") for name in names)


def photo(response, registration):
    """The first usable photo of this registration in an API response, or None.

    A photo needs a large thumbnail on an allowed image host with its size, a
    link to its Planespotters page that names the registration, and a
    photographer to credit. Anything else is dropped. URLs are kept exactly as
    returned. The photo records the registration it was bound to, which the
    build compares with its story's.
    """
    if not isinstance(registration, str) or not REGISTRATION.fullmatch(registration.upper()):
        return None
    photos = response.get("photos") if isinstance(response, dict) else None
    for entry in photos if isinstance(photos, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("thumbnail_large"), dict):
            continue
        image = entry["thumbnail_large"]
        size = image.get("size") if isinstance(image.get("size"), dict) else {}
        photographer = entry.get("photographer")
        if (
            _https(image.get("src"), IMAGE_HOSTS)
            and _size(size.get("width"))
            and _size(size.get("height"))
            and _https(entry.get("link"), {LINK_HOST})
            and _names_registration(entry["link"], registration)
            and isinstance(photographer, str)
            and 0 < len(photographer.strip()) <= 100
            and photographer.isprintable()
        ):
            return {
                "src": image["src"],
                "width": size["width"],
                "height": size["height"],
                "link": entry["link"],
                "photographer": photographer.strip(),
                "registration": registration,
            }
    return None


class PhotoCache:
    """Planespotters API responses cached in a private directory for at most 24 hours.

    Without `fetch`, the cache is only read. `refresh` sends at most `budget`
    requests, a second apart and within BUDGET_SECONDS. A 403 or 429 stops
    requests for six hours (longer if the server asks), and a network or
    server failure for half an hour. An aircraft whose own request fails waits
    20 hours for its next turn, keeping any response still under 24 hours old,
    so it never holds up the others. Failures never stop a release: a story
    without a fresh response shows no airframe photo.
    """

    def __init__(
        self, directory, *, fetch=False, budget=BUDGET, clock=time.time, sleep=time.sleep, opener=None
    ):
        self.directory = Path(directory)
        self.fetch = fetch
        self.budget = budget
        self.clock = clock
        self.sleep = sleep
        self.opener = opener or build_opener(NoRedirect)
        self.requests = 0
        self.failure = None

    def _path(self, key):
        return self.directory / f"{key}.json"

    def _read(self, path):
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        fetched = entry.get("fetched_at") if isinstance(entry, dict) else None
        if not isinstance(fetched, (int, float)) or isinstance(fetched, bool):
            return None
        return entry

    def _write(self, path, value):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        temporary = path.with_name(f".{path.name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, sort_keys=True)
        os.replace(temporary, path)

    def age(self, key):
        """Time since the aircraft's last request, answered or failed, or None."""
        entry = self._read(self._path(key))
        if entry is None:
            return None
        failed = entry.get("failed_at")
        last = max(entry["fetched_at"], failed if isinstance(failed, (int, float)) else 0)
        return self.clock() - last

    def response(self, key):
        """A cached response younger than 24 hours, or None."""
        entry = self._read(self._path(key))
        if entry is None or not 0 <= self.clock() - entry["fetched_at"] < MAX_AGE:
            return None
        return entry.get("response")

    def backoff(self):
        entry = self._read(self.directory / "backoff.json")
        return entry["until"] if entry and isinstance(entry.get("until"), (int, float)) else 0

    def _stop(self, seconds, reason):
        now = self.clock()
        self._write(
            self.directory / "backoff.json", {"fetched_at": now, "until": now + seconds, "reason": reason}
        )
        self.failure = reason

    def prune(self, *, everything=False):
        """Delete responses older than the 24 hours the terms allow, or all of them."""
        if not self.directory.is_dir():
            return
        for path in self.directory.glob("*.json"):
            if path.name == "backoff.json" and not everything:
                continue
            entry = self._read(path)
            if everything or entry is None or self.clock() - entry["fetched_at"] >= MAX_AGE:
                path.unlink(missing_ok=True)

    def refresh(self, lookups):
        """Fetch missing and aging responses for {cache key: API path}, oldest first, within the budget."""
        if not self.fetch:
            return
        self.prune()
        if self.clock() < self.backoff():
            return
        due = []
        for key, route in lookups.items():
            age = self.age(key)
            if age is None or not 0 <= age < REFRESH_AFTER:
                due.append((-(float("inf") if age is None else age), key, route))
        started = self.clock()
        for _, key, route in sorted(due)[: self.budget]:
            if self.requests:
                if self.clock() - started >= BUDGET_SECONDS:
                    return
                self.sleep(REQUEST_GAP)
            self.requests += 1
            request = Request(
                API + route, headers={"User-Agent": site_origin.user_agent(), "Accept": "application/json"}
            )
            try:
                with self.opener.open(request, timeout=TIMEOUT) as reply:
                    raw = reply.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ValueError("Photo API response too large")
                response = json.loads(raw)
                if not isinstance(response, dict) or not isinstance(response.get("photos"), list):
                    raise ValueError("Unexpected photo API response")
            except HTTPError as error:
                error.close()
                if error.code in (403, 429):
                    retry = error.headers.get("Retry-After", "") if error.headers else ""
                    wait = int(retry) if retry.isdigit() else 0
                    self._stop(max(LIMITED_BACKOFF, min(wait, MAX_BACKOFF)), f"HTTP {error.code}")
                    return
                self._failed(key)
                if error.code >= 500 or error.code == 408:
                    self._stop(ERROR_BACKOFF, f"HTTP {error.code}")
                    return
                continue
            except ValueError as error:
                self._failed(key)
                self._stop(ERROR_BACKOFF, type(error).__name__)
                return
            except (OSError, HTTPException) as error:
                # The aircraft's turn is spent too, so one that keeps timing
                # out cannot head the queue after every backoff.
                self._failed(key)
                self._stop(ERROR_BACKOFF, type(error).__name__)
                return
            self._write(self._path(key), {"fetched_at": self.clock(), "response": response})

    def _failed(self, key):
        """Record a failed request, so the aircraft waits its turn; a response it had stays."""
        path = self._path(key)
        entry = self._read(path) or {"fetched_at": self.clock(), "response": {"photos": []}}
        self._write(path, {**entry, "failed_at": self.clock()})


def _newest_first(event):
    return event.get("released_at") or event.get("observed_start") or ""


def story_photos(snapshot, cache):
    """{event id: photo} for the newest stories' aircraft that have a registration and a fresh response.

    An aircraft without a registration is not looked up: no photo page could be
    checked against it. Only the newest MAX_AIRCRAFT aircraft are.
    """
    events = sorted(snapshot.get("events", []), key=_newest_first, reverse=True)
    wanted = {}
    keys = set()
    for event in events:
        aircraft = event.get("aircraft") or {}
        registration = str(aircraft.get("registration") or "")
        found = lookup(aircraft)
        if (
            event.get("synthetic") is not False
            or not found
            or not REGISTRATION.fullmatch(registration.upper())
        ):
            continue
        if found[0] not in keys and len(keys) >= MAX_AIRCRAFT:
            continue
        keys.add(found[0])
        wanted[event["id"]] = (*found, registration)
    cache.refresh({key: route for key, route, _ in wanted.values()})
    photos = {}
    for identifier, (key, _, registration) in wanted.items():
        if chosen := photo(cache.response(key), registration):
            photos[identifier] = chosen
    return photos


# Elements and attributes that make a browser load a resource. Links are loads
# only for these relationships; an ordinary anchor or a canonical link is not.
RESOURCE_ATTRIBUTES = {
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "script": ("src", "href", "xlink:href"),
    "iframe": ("src",),
    "frame": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "video": ("src", "poster"),
    "audio": ("src",),
    "track": ("src",),
    "input": ("src",),
    "link": ("href", "imagesrcset"),
    "image": ("href", "xlink:href"),
    "use": ("href", "xlink:href"),
    "feimage": ("href", "xlink:href"),
}
LOADING_RELS = {
    "stylesheet",
    "preload",
    "modulepreload",
    "prefetch",
    "icon",
    "apple-touch-icon",
    "manifest",
    "preconnect",
    "dns-prefetch",
}
# What CSS loads: url(...) and @import targets, and the strings in image-set(...).
CSS_URL = re.compile(r"""url\(\s*['"]?\s*([^'")\s]+)|@import\s+['"]([^'"]+)['"]""", re.I)
IMAGE_SET = re.compile(r"image-set\(([^)]*)\)", re.I)
QUOTED = re.compile(r"""['"]([^'"]+)['"]""")
IMAGE_HOST_TEXT = re.compile(r"plnspttrs", re.I)
PHOTO_DATA = re.compile(rb"planespotters|plnspttrs", re.I)


def _urls(attribute, value):
    if attribute.endswith("srcset"):
        return [part.strip().split()[0] for part in value.split(",") if part.strip()]
    return [value]


def _foreign(url):
    """Whether a URL loads from another origin than the site. A URL that cannot be read is foreign."""
    parts = _split("https:" + url.strip() if url.strip().startswith(("//", "\\\\", "/\\", "\\/")) else url)
    if parts is None:
        return True
    if parts.scheme in ("", "data") and not parts.netloc:
        return False
    return (
        parts.scheme != "https" or parts.hostname not in site_origin.hosts() or parts.netloc != parts.hostname
    )


def _css_urls(text):
    urls = [first or second for first, second in CSS_URL.findall(text)]
    for inner in IMAGE_SET.findall(text):
        urls += QUOTED.findall(inner)
    return urls


class _Resources(HTMLParser):
    def __init__(self, story):
        super().__init__(convert_charrefs=True)
        self.story = story
        self.figures = []
        self.style = False
        self.found = []
        self.allowed = 0

    def handle_starttag(self, tag, attrs):
        # Browsers keep the first of two attributes with the same name.
        values = {}
        for name, value in attrs:
            values.setdefault(name, value or "")
        if tag == "figure":
            self.figures.append("airframe-photo" in values.get("class", "").split())
        if tag == "style":
            self.style = True
        self.found.extend(f"style url {url}" for url in _css_urls(values.get("style", "")) if _foreign(url))
        if tag == "link" and not LOADING_RELS & set(values.get("rel", "").lower().split()):
            return
        for attribute in RESOURCE_ATTRIBUTES.get(tag, ()):
            for url in _urls(attribute, values[attribute]) if attribute in values else ():
                if (
                    self.story
                    and tag == "img"
                    and attribute == "src"
                    and any(self.figures)
                    and _https(url, IMAGE_HOSTS)
                ):
                    self.allowed += 1
                elif _foreign(url):
                    self.found.append(f"<{tag} {attribute}> {url}")

    def handle_endtag(self, tag):
        if tag == "figure" and self.figures:
            self.figures.pop()
        if tag == "style":
            self.style = False

    def handle_data(self, data):
        if self.style:
            self.found.extend(f"style url {url}" for url in _css_urls(data) if _foreign(url))


def release_violations(content, *, page=False, story=False, style=False):
    """Third-party loads and leaked photo data in one published file.

    A story page may load from another origin only an airframe photo's image,
    from an allowed host, inside its figure; other pages load nothing from
    elsewhere. The image host may appear in a page only as such an image, so
    never in its head, structured data or scripts. A stylesheet or SVG file
    (`style`) may not load from another origin either, and no file other than
    a page, such as JSON data, a search index, a feed or a sitemap, may name
    Planespotters.
    """
    text = content.decode("utf-8", "replace")
    if not page:
        found = [f"css url {url}" for url in _css_urls(text) if _foreign(url)] if style else []
        return found + (["Planespotters data outside a page"] if PHOTO_DATA.search(content) else [])
    parser = _Resources(story)
    parser.feed(text)
    parser.close()
    if len(IMAGE_HOST_TEXT.findall(text)) > parser.allowed:
        parser.found.append("Planespotters image host outside an airframe photo")
    return parser.found
