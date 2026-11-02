"""What Wikidata calls the organization a Plane Alert DB entry links to.

A watchlist story may say, once, what the entry's own link leads to: "Federal
Aviation Administration, United States government agency regulating civil
aviation", from the English Wikidata description (CC0) of the linked article's
item. People are never described: the watchlist rules refuse any sentence that
names a kind of person, and that is right. The host autopilot resolves a few
links per pass and caches each answer; nothing here runs in the collector.
Entries never refresh on their own, because a changed description would change
a listed fact and force a rewrite; refresh() marks them stale, and each stays in
use until a fresh answer replaces it. Each answer is keyed by the entry's link and
association (entity_rules.cache_key). The cache joins Plane Alert links to
Wikidata data and stays private. The description rules live in entity_rules, which
the facts layer applies again to each cached answer without loading this module.
"""

import http.client
import re
import time
from pathlib import Path
from urllib.parse import quote, unquote
from urllib.request import Request, build_opener

from . import public_events as public
from . import registry_http, site_origin, watchlists
from .entity_rules import ARTICLE, ITEM, cache_key, current_key, description_issue

WIKIPEDIA = "https://en.wikipedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
# Wikimedia asks for a User-Agent that says who is asking: site_origin.user_agent() names the
# public site.
# An item's claims come whole, so a large organization's entity runs to hundreds of kilobytes.
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 5
HUMAN = "Q5"
FILE = "entities.json"
# Seconds to wait after the first, second, and any later failed lookup of a link.
BACKOFF = (86400, 7 * 86400, 30 * 86400)
SHORT = re.compile(r"https://w\.wiki/[A-Za-z0-9$]+")
ANSWERS = frozenset({"available", "unavailable"})
# What a lookup raises when the network or the API fails; anything else is a bug.
TRANSPORT_ERRORS = (OSError, ValueError, http.client.HTTPException, registry_http.Unavailable)


def _head(url, *, timeout=TIMEOUT):
    """The final URL a short link lands on."""
    request = Request(url, method="HEAD", headers={"User-Agent": site_origin.user_agent()})
    with build_opener().open(request, timeout=timeout) as response:
        return response.geturl()


def fetch_json(url, *, timeout=TIMEOUT):
    """One bounded API answer through the enrichment workers' client (registry_http)."""
    return registry_http.get_json(
        url, max_bytes=MAX_BYTES, timeout=timeout, user_agent=site_origin.user_agent()
    )


def article_title(url, *, head):
    """The English Wikipedia article title a link names (entity_rules.ARTICLE), following a
    w.wiki short link once; a mobile link, a section, or a query string names the article itself."""
    if isinstance(url, str) and SHORT.fullmatch(url):
        url = head(url)
    match = ARTICLE.fullmatch(url) if isinstance(url, str) else None
    return unquote(match.group(1)) if match else None


def accept(label, aliases, description, association, instance_of):
    """None when the item may be described, else why not.

    The item must be no person, carry the entry's own association as its label or an
    alias, and have a short plain description that names no kind of person, private
    name, wrongdoing, or founder, owner, or leader, carries no capitalised name of its
    own (entity_rules), and passes the location and claim checks on its own.
    """
    if HUMAN in instance_of:
        return "human"
    names = {str(label).casefold(), *(str(alias).casefold() for alias in aliases)}
    if not isinstance(association, str) or association.casefold() not in names:
        return "label"
    return description_issue(description, (label, *aliases, association))


def _response(data, key):
    """An API answer that carries `key`; an error or a malformed answer raises ValueError, so a
    transient failure backs off instead of reading as a refusal."""
    if not isinstance(data, dict) or "error" in data or not isinstance(data.get(key), dict):
        raise ValueError("unexpected API response")
    return data[key]


def lookup(url, association, *, fetch=fetch_json, head=_head):
    """Resolve one entry link to {"state", ...}; a refusal is an answer, a transport error raises."""
    title = article_title(url, head=head)
    if not title:
        return {"state": "unavailable", "reason": "host"}
    pages = _response(
        fetch(
            f"{WIKIPEDIA}?action=query&prop=pageprops&ppprop=wikibase_item&redirects=1&format=json"
            f"&titles={quote(title)}"
        ),
        "query",
    )
    page = next(iter(pages.get("pages", {}).values()), {})
    item = page.get("pageprops", {}).get("wikibase_item")
    if not isinstance(item, str) or not ITEM.fullmatch(item):
        return {"state": "unavailable", "reason": "item"}
    # "info" carries lastrevid, the revision the story's source names.
    entities = _response(
        fetch(
            f"{WIKIDATA}?action=wbgetentities&ids={item}&props=info|labels|descriptions|aliases|claims"
            "&languages=en&format=json"
        ),
        "entities",
    )
    found = entities.get(item, {})
    if not isinstance(found, dict):
        return {"state": "unavailable", "reason": "item"}
    if "redirects" in found:
        moved = found["redirects"]
        if not (
            isinstance(moved, dict)
            and moved.get("from") == item
            and isinstance(moved.get("to"), str)
            and ITEM.fullmatch(moved["to"])
            and found.get("id", moved["to"]) == moved["to"]
        ):
            # A redirects record that does not lead from this item to the id the data carries
            # says nothing reliable about that data.
            return {"state": "unavailable", "reason": "item"}
        # A merged item answers under the requested id with the data, id, and revision of the
        # item it now redirects to; the story cites that item. One hop, never a chain.
        item = moved["to"]
    label = found.get("labels", {}).get("en", {}).get("value")
    description = found.get("descriptions", {}).get("en", {}).get("value")
    aliases = [entry.get("value") for entry in found.get("aliases", {}).get("en", [])]
    instance_of = {
        claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
        for claim in found.get("claims", {}).get("P31", [])
        if isinstance(claim, dict)
    }
    reason = accept(label, aliases, description, association, instance_of)
    if reason:
        return {"state": "unavailable", "reason": reason}
    return {
        "state": "available",
        "label": label,
        "aliases": [alias for alias in aliases if isinstance(alias, str)],
        "description": description.strip(),
        "item": item,
        "revision": found.get("lastrevid"),
    }


def _path(state_dir):
    return Path(state_dir) / FILE


def load(state_dir):
    """{cache_key(entry link, association): cached answer}; a missing or damaged cache reads as
    empty.

    A key written by an earlier version maps to the current one (entity_rules.current_key): one
    keyed by the link alone is rekeyed by its answer's association, and one keyed by a link
    with a section, a query string, or the mobile host by its canonical link. A failure saved
    under a link alone recorded no association, so it is dropped and its link looked up again.
    So is a "host" refusal under an old key: such links name their article now. Where two keys
    meet on one, an available answer wins over any other entry, then the newer one.
    """
    try:
        data = public.read_json(_path(state_dir))
    except (OSError, ValueError):
        return {}
    entities = data.get("entities") if isinstance(data, dict) else None
    if not isinstance(entities, dict):
        return {}
    cache = {}
    for key, entry in entities.items():
        if not isinstance(entry, dict):
            continue
        association = entry.get("association")
        if not isinstance(association, str):
            if "\t" not in key:
                continue
            association = key.split("\t", 1)[1]
        current = current_key(key, association)
        if current != key and entry.get("state") == "unavailable" and entry.get("reason") == "host":
            continue
        if current not in cache or _rank(entry) > _rank(cache[current]):
            cache[current] = entry
    return cache


def _rank(entry):
    """How load() orders two entries for one key: an available answer, then the newer."""
    times = (entry.get(field) for field in ("retrieved_at", "failed_at"))
    return (
        entry.get("state") == "available",
        max(when if isinstance(when, (int, float)) and not isinstance(when, bool) else 0 for when in times),
    )


def save(state_dir, cache):
    public.write_json_atomic(_path(state_dir), {"version": 1, "entities": cache}, private=True)


def refresh(state_dir):
    """Mark every answer stale so the next passes look each link up again; returns how many.

    A stale answer stays in use until a fresh one replaces it, so no story loses its fact
    while the links are looked up a few at a time.
    """
    cache = load(state_dir)
    marked = 0
    for entry in cache.values():
        if isinstance(entry, dict) and entry.get("state") in ANSWERS and not entry.get("stale"):
            entry["stale"] = True
            marked += 1
    if marked:
        save(state_dir, cache)
    return marked


def _due(entry, now):
    """Whether a link needs a lookup now: never looked up, stale, or past its failure backoff."""
    if entry is None:
        return True
    if entry.get("state") in ANSWERS and not entry.get("stale"):
        return False
    failures = entry.get("failures", 0)
    if isinstance(failures, int) and failures > 0:
        return now - entry.get("failed_at", 0) >= BACKOFF[min(failures, len(BACKOFF)) - 1]
    return True


def resolve(state_dir, links, *, now, limit=10, budget=15.0, lookup=lookup, clock=time.monotonic):
    """Look up to `limit` due links within `budget` seconds of `clock`, stale ones first, the
    rest in the order given; returns how many it answered. Each request's timeout is what is left
    of the budget, at most TIMEOUT, so a pass holds the ledger lock for about `budget` seconds at
    most.

    `links` holds (entry link, association, list category, neutral category) tuples, each
    answered under cache_key(link, association). An answer, available or not, stays until
    refresh() marks it stale and a fresh answer replaces it. A failed lookup keeps any earlier
    answer and waits out BACKOFF. The cache is saved after each lookup, so a later error loses
    none of them.
    """
    cache = load(state_dir)
    due, seen = [], set()
    for url, association, list_category, category in links:
        if not isinstance(url, str) or not watchlists.describable(association, list_category, category):
            continue
        key = cache_key(url, association)
        if key in seen:
            continue
        seen.add(key)
        entry = cache.get(key) if isinstance(cache.get(key), dict) else None
        if _due(entry, now):
            due.append((key, url, association, entry))
    due.sort(key=lambda link: not (link[3] or {}).get("stale"))
    deadline, answered = clock() + budget, 0

    def timeout():
        """Each request waits TIMEOUT at most, and never past the pass's budget."""
        left = deadline - clock()
        if left <= 0:
            raise TimeoutError("lookup budget spent")
        return min(TIMEOUT, left)

    def fetch(url):
        return fetch_json(url, timeout=timeout())

    def head(url):
        return _head(url, timeout=timeout())

    for key, url, association, entry in due[:limit]:
        if clock() > deadline:
            break
        try:
            found = lookup(url, association, fetch=fetch, head=head)
        except TRANSPORT_ERRORS as error:
            if clock() >= deadline:
                # The pass ran out of time, which says nothing about the link.
                break
            failure = {
                "failures": (entry or {}).get("failures", 0) + 1,
                "failed_at": now,
                "error": type(error).__name__,
            }
            answer = entry if entry and entry.get("state") in ANSWERS else {"state": "failed"}
            cache[key] = {**answer, **failure, "association": association}
        else:
            cache[key] = {**found, "association": association, "retrieved_at": now}
            answered += 1
        save(state_dir, cache)
    return answered
