#!/usr/bin/env python3
"""Private, cookieless readership counts from the public Caddy access log.

The public Caddyfile writes one JSON line per request to the site with the
time, client_ip (the visitor, from Cloudflare's Cf-Connecting-Ip), method, path
without its query, status, user agent, referrer host and Cloudflare's country
code. This tool reduces one UTC day of those lines to daily aggregates only:

- page views: GET requests for HTML pages answered 200, or 304 for a reader's
  repeat view, since pages are sent with no-cache and revalidate;
- distinct visitors: a keyed hash of client IP plus user agent, under a random
  key made for that run and day, held in memory and never written;
- visitors who viewed two or more distinct pages, the top pages, the referrer
  hosts other than the site itself, and visitors by country;
- the share of requests excluded, by reason, and rotating-proxy clusters.

Excluded, in this order: our own checks and other headless browsers
(verify-live.mjs and check-production.mjs drive the pinned Chrome for Testing,
whose user agent is "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML,
like Gecko) HeadlessChrome/<version> Safari/537.36"), known bots and tools by
user agent (BOT_TOKENS, any user agent that does not start with "Mozilla/",
and an empty one), every request of a client that probed a scanner path
(SCANNER_PATH), methods other than GET, assets and other files (anything but
a page), statuses other than 200 and 304, and rotating proxies: a (path, user
agent) pair fetched by PROXY_MIN_VISITORS or more distinct clients that each
made only that one request in the day.

Output never contains a client IP, a hash of one, a query string or an
individual's full user agent; a rotating-proxy cluster is reported by path and
browser family only.

  site_stats.py --logs DIR [--date YYYY-MM-DD ...] [--output DIR] [--keep-days N]
  site_stats.py --report [AGGREGATE.json ...] [--days N] [--json]

Without --date the summary covers yesterday (UTC). With --output it writes
DIR/YYYY-MM-DD.json (mode 0600) and deletes aggregates more than --keep-days
old; without it the aggregate is printed. --report reads aggregates from files
or, with none, from standard input (concatenated JSON is accepted) and prints a
multi-day table. The SITE_ORIGIN environment variable names the public origin
(https://example.com when unset), so that the site's own pages do not count as
referrers.
"""

import argparse
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import unquote

SCHEMA = 1
# The public origin, such as https://example.com, as publisher.json's site_origin
# names it. A referrer from its host or the www. form is the site itself.
SITE_ORIGIN = "SITE_ORIGIN"
EXAMPLE_ORIGIN = "https://example.com"
LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
ORIGIN = re.compile(rf"https://(?:www\.)?((?:{LABEL}\.)*{LABEL})")
TOP = 20
PROXY_MIN_VISITORS = 20
LOG_NAME = re.compile(r"^public(?:-[0-9A-Za-z:._-]+)?\.log(?:\.gz)?$")
AGGREGATE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")

# Our own checks use the pinned Chrome for Testing through Playwright; every
# other headless browser is excluded with them.
OWN_CHECKS = re.compile(r"HeadlessChrome", re.I)
# User agent fragments of crawlers, AI agents, SEO tools, link previews,
# monitors, scanners, automation and HTTP libraries. Matched case-insensitively
# as substrings. In-app browsers (LinkedInApp, FBAN, Instagram) are readers and
# must not match. Keep this list maintained when new agents appear in the logs.
BOT_TOKENS = (
    # Generic markers. "cubot" is a phone brand, not a bot.
    "crawl",
    "spider",
    "slurp",
    "scrap",
    "fetch",
    "archiv",
    "feed",
    "+http",
    "@",
    # AI crawlers and agents.
    "gptbot",
    "chatgpt-user",
    "oai-searchbot",
    "claudebot",
    "claude-user",
    "claude-searchbot",
    "anthropic-ai",
    "perplexitybot",
    "perplexity-user",
    "amazonbot",
    "applebot",
    "meta-externalagent",
    "meta-externalfetcher",
    "facebookexternalhit",
    "bytespider",
    "ccbot",
    "google-extended",
    "googleother",
    "google-inspectiontool",
    "cohere-ai",
    "diffbot",
    "youbot",
    "timpibot",
    "imagesiftbot",
    "petalbot",
    "ai2bot",
    "duckassistbot",
    "mistralai-user",
    "omgili",
    # SEO tools.
    "semrush",
    "ahrefs",
    "mj12",
    "dotbot",
    "blexbot",
    "barkrowler",
    "dataforseo",
    "serpstat",
    "screaming frog",
    "sistrix",
    "seokicks",
    "megaindex",
    # Link previews.
    "whatsapp",
    "telegrambot",
    "slackbot",
    "discordbot",
    "twitterbot",
    "linkedinbot",
    "skypeuripreview",
    "iframely",
    "embedly",
    "mastodon",
    "pleroma",
    "akkoma",
    "misskey",
    "cardyb",
    # Headless browsers, automation and performance tools.
    "headless",
    "phantomjs",
    "puppeteer",
    "playwright",
    "selenium",
    "webdriver",
    "lighthouse",
    "pagespeed",
    "ptst",
    "gtmetrix",
    # Monitors.
    "pingdom",
    "uptime",
    "statuscake",
    "site24x7",
    "monitor",
    # HTTP libraries and command-line tools.
    "python",
    "curl",
    "wget",
    "go-http",
    "okhttp",
    "java/",
    "libwww",
    "httpclient",
    "http-client",
    "axios",
    "node-fetch",
    "undici",
    "node.js",
    "aiohttp",
    "httpx",
    "scrapy",
    "guzzle",
    "http.rb",
    "reqwest",
    "postman",
    "powershell",
    "winhttp",
    # Scanners.
    "nmap",
    "masscan",
    "zgrab",
    "nuclei",
    "censys",
    "expanse",
    "paloalto",
    "l9explore",
    "nikto",
    "sqlmap",
    "shodan",
    "netcraft",
    "internet-measurement",
    "leakix",
    "wpscan",
)
BOT_PATTERN = re.compile(r"(?<!cu)bot|" + "|".join(re.escape(token) for token in BOT_TOKENS), re.I)
# Paths no reader asks for: WordPress and PHP probes, admin consoles, dotfiles
# such as .env and .git, archives and configuration files, and path traversal.
SCANNER_PATH = re.compile(
    r"(?:^|/)(?:wp-admin|wp-login|wp-content|wp-includes|wp-json|wordpress|xmlrpc|phpmyadmin|pma"
    r"|cgi-bin|vendor/phpunit|boaform|hnap1|actuator|server-status|owa|autodiscover|telescope|solr"
    r"|administrator|admin)(?:[/.]|$)"
    r"|(?:^|/)\.(?!well-known(?:/|$))"
    r"|\.(?:php\d?|phtml|asp|aspx|jsp|cgi|env|ini|sql|bak|old|swp|ya?ml|cfg|conf|log|tar|gz|tgz|zip|rar|7z)$"
    r"|\.\./|/etc/passwd",
    re.I,
)
# Files and data, never pages: theme assets, story media, social images, the
# journal's data and search index, the feed, and anything with an extension
# other than .html (robots.txt, sitemaps, the root icons).
ASSET_PREFIXES = (
    "/assets/",
    "/social/",
    "/event-media/",
    "/event-data/",
    "/search-index/",
    "/examples/",
    "/rss",
)
PAGE_STATUSES = frozenset({200, 304})
COUNTRY = re.compile(r"^[A-Z0-9]{2}$")
HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$")
BROWSERS = (
    ("Edge", re.compile(r"\bEdg(?:e|A|iOS)?/(\d+)")),
    ("Firefox", re.compile(r"\b(?:Firefox|FxiOS)/(\d+)")),
    ("Chrome", re.compile(r"\b(?:Chrome|CriOS)/(\d+)")),
    ("Safari", re.compile(r"\bVersion/(\d+)[^ ]* (?:Mobile/\S+ )?Safari/")),
)
REASONS = ("own_checks", "bot", "scanner", "method", "asset", "status", "rotating_proxy")


def clean_path(uri):
    """The path of a logged URI, without any query or fragment the log kept."""
    return re.split(r"[?#]", str(uri or ""), maxsplit=1)[0] or "/"


def is_bot(agent):
    return not agent or not agent.startswith("Mozilla/") or bool(BOT_PATTERN.search(agent))


def is_scanner(path):
    return bool(SCANNER_PATH.search(unquote(path)))


def is_page(path):
    if path.startswith(ASSET_PREFIXES):
        return False
    last = path.rsplit("/", 1)[-1]
    return path.endswith("/") or "." not in last or last.endswith(".html")


def browser_family(agent):
    """A coarse browser label, such as "Chrome 120", never the full user agent."""
    for name, pattern in BROWSERS:
        match = pattern.search(agent or "")
        if match:
            return f"{name} {match.group(1)}"
    return "other"


def site_hosts(environ=os.environ):
    """The site's own hosts: SITE_ORIGIN's host (EXAMPLE_ORIGIN's when unset) and its www. form."""
    match = ORIGIN.fullmatch(environ.get(SITE_ORIGIN) or EXAMPLE_ORIGIN)
    if not match:
        raise ValueError("SITE_ORIGIN must be an https:// origin such as https://example.com")
    return frozenset({match[1], "www." + match[1]})


def referrer_host(value, site=frozenset()):
    """The referrer's host, lower-cased, or None when there is none or it is in `site`."""
    host = str(value or "").strip().lower()
    host = re.sub(r"^(?:[a-z][a-z0-9+.-]*:)?(?://)?(?:[^/?#@]*@)?", "", host)
    host = re.split(r"[/?#:]", host, maxsplit=1)[0].rstrip(".")
    if not host or host in site:
        return None
    return host if HOST.match(host) else "(other)"


def country_code(value):
    code = str(value or "").strip().upper()
    return code if COUNTRY.match(code) else "(unknown)"


def visitor_key(salt, client_ip, agent):
    """A keyed digest of IP and user agent; the key never outlives the run."""
    return hmac.new(salt, f"{client_ip}\n{agent}".encode(), hashlib.sha256).digest()


def log_files(paths):
    """Caddy's current log file and its rolled copies, in the given files or directories."""
    files = []
    for path in map(Path, paths):
        if path.is_dir():
            files.extend(sorted(item for item in path.iterdir() if LOG_NAME.match(item.name)))
        elif path.is_file():
            files.append(path)
        else:
            raise SystemExit(f"No access log at {path}")
    return files


def parse_entry(item):
    """(timestamp, request fields) from one decoded access log line."""
    request = item.get("request") or {}
    return float(item["ts"]), {
        "ip": str(request.get("client_ip") or ""),
        "method": str(request.get("method") or ""),
        "path": clean_path(request.get("uri")),
        "status": int(item.get("status") or 0),
        "agent": str(item.get("user_agent") or ""),
        "referer": item.get("referer"),
        "country": item.get("country"),
    }


def read_entries(files):
    """Every access log line as (timestamp, request fields), the earliest
    timestamp, and the number of lines that could not be read."""
    entries, first, malformed = [], None, 0
    for path in files:
        opener = gzip.open if path.name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    stamp, entry = parse_entry(json.loads(line))
                except (ValueError, TypeError, KeyError, AttributeError):
                    malformed += 1
                    continue
                first = stamp if first is None else min(first, stamp)
                entries.append((stamp, entry))
    return entries, first, malformed


def summarize(entries, day, *, coverage_start=None, salt=None, malformed=0):
    """One UTC day's aggregate from (timestamp, entry) pairs. Other days are skipped."""
    salt = salt or secrets.token_bytes(32)
    start = datetime.combine(day, time(), UTC).timestamp()
    end = start + 86400
    rows = []
    for stamp, entry in entries:
        if start <= stamp < end:
            entry = dict(entry)
            entry["key"] = visitor_key(salt, entry["ip"], entry["agent"])
            del entry["ip"]
            rows.append(entry)

    requests_by_client = Counter(row["key"] for row in rows)
    scanners = {row["key"] for row in rows if is_scanner(row["path"])}
    reasons = Counter()
    candidates = []
    page_requests = 0
    for row in rows:
        page_like = row["method"] == "GET" and row["status"] in PAGE_STATUSES and is_page(row["path"])
        page_requests += page_like
        if OWN_CHECKS.search(row["agent"]):
            reasons["own_checks"] += 1
        elif is_bot(row["agent"]):
            reasons["bot"] += 1
        elif row["key"] in scanners:
            reasons["scanner"] += 1
        elif row["method"] != "GET":
            reasons["method"] += 1
        elif not is_page(row["path"]):
            reasons["asset"] += 1
        elif row["status"] not in PAGE_STATUSES:
            reasons["status"] += 1
        else:
            candidates.append(row)

    # A rotating proxy: many addresses, one request each, one path and one
    # identical user agent.
    singles = defaultdict(list)
    for row in candidates:
        if requests_by_client[row["key"]] == 1:
            singles[(row["path"], row["agent"])].append(row)
    proxies = []
    flagged = set()
    for (path, agent), group in singles.items():
        if len(group) >= PROXY_MIN_VISITORS:
            proxies.append({"path": path, "browser": browser_family(agent), "visitors": len(group)})
            flagged.update(id(row) for row in group)
    reasons["rotating_proxy"] = len(flagged)
    views = [row for row in candidates if id(row) not in flagged]

    pages_by_visitor = defaultdict(set)
    country_by_visitor = {}
    page_views = Counter()
    page_visitors = defaultdict(set)
    referrers = Counter()
    site = site_hosts()
    for row in views:
        pages_by_visitor[row["key"]].add(row["path"])
        country_by_visitor.setdefault(row["key"], country_code(row["country"]))
        page_views[row["path"]] += 1
        page_visitors[row["path"]].add(row["key"])
        host = referrer_host(row["referer"], site)
        if host:
            referrers[host] += 1

    excluded = sum(reasons.values())
    total = len(rows)
    return {
        "schema": SCHEMA,
        "date": day.isoformat(),
        "logs_cover_whole_day": coverage_start is not None and coverage_start <= start,
        "requests": total,
        "page_views": len(views),
        "visitors": len(pages_by_visitor),
        "visitors_two_or_more_pages": sum(len(paths) >= 2 for paths in pages_by_visitor.values()),
        "top_pages": [
            {"path": path, "views": count, "visitors": len(page_visitors[path])}
            for path, count in ranked(page_views)
        ],
        "referrers": [{"host": host, "views": count} for host, count in ranked(referrers)],
        "countries": [
            {"country": code, "visitors": count}
            for code, count in sorted(
                Counter(country_by_visitor.values()).items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "excluded": {
            "requests": excluded,
            "share": round(excluded / total, 4) if total else 0.0,
            "page_requests": page_requests,
            "page_request_share": round(1 - len(views) / page_requests, 4) if page_requests else 0.0,
            "by_reason": {reason: reasons[reason] for reason in REASONS},
        },
        "rotating_proxies": sorted(proxies, key=lambda item: (-item["visitors"], item["path"])),
        "malformed_lines": malformed,
    }


def write_private(path, text):
    """Write text atomically as a mode-0600 file."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def prune(directory, today, keep_days):
    """Delete aggregates dated more than keep_days before today; return their names."""
    removed = []
    for path in directory.iterdir():
        match = AGGREGATE_NAME.match(path.name)
        if match and date.fromisoformat(match.group(1)) < today - timedelta(days=keep_days):
            path.unlink()
            removed.append(path.name)
    return sorted(removed)


def parse_aggregates(text):
    """Every aggregate in text holding one or more concatenated JSON objects."""
    decoder = json.JSONDecoder()
    found = []
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            return found
        item, index = decoder.raw_decode(text, index)
        if not isinstance(item, dict) or "date" not in item:
            raise SystemExit("Not a readership aggregate")
        found.append(item)


def ranked(counter):
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:TOP]


def combine(aggregates):
    """Totals over several days. Visitors cannot be deduplicated across days,
    since each day's key is discarded, so they are summed as visitor-days."""
    totals = {"days": len(aggregates), "page_views": 0, "visitor_days": 0, "requests": 0}
    pages, referrers, countries, reasons = Counter(), Counter(), Counter(), Counter()
    for item in aggregates:
        totals["page_views"] += item["page_views"]
        totals["visitor_days"] += item["visitors"]
        totals["requests"] += item["requests"]
        pages.update({row["path"]: row["views"] for row in item["top_pages"]})
        referrers.update({row["host"]: row["views"] for row in item["referrers"]})
        countries.update({row["country"]: row["visitors"] for row in item["countries"]})
        reasons.update(item["excluded"]["by_reason"])
    return {
        **totals,
        "first": aggregates[0]["date"] if aggregates else None,
        "last": aggregates[-1]["date"] if aggregates else None,
        "top_pages": ranked(pages),
        "referrers": ranked(referrers),
        "countries": ranked(countries),
        "excluded_by_reason": {reason: reasons[reason] for reason in REASONS},
    }


def percent(value):
    return f"{100 * value:.1f}%"


def report(aggregates):
    """A short text report of one or more days."""
    aggregates = sorted(aggregates, key=lambda item: item["date"])
    if not aggregates:
        return "No readership aggregates.\n"
    total = combine(aggregates)
    lines = [
        f"Readership, {total['first']} to {total['last']} ({total['days']} days)",
        "",
        f"{'date':<10}  {'views':>6}  {'visitors':>8}  {'2+ pages':>8}  {'excluded':>8}  "
        f"{'pages excl.':>11}  {'proxy':>5}",
    ]
    for item in aggregates:
        mark = "" if item.get("logs_cover_whole_day", True) else "  (partial log)"
        lines.append(
            f"{item['date']:<10}  {item['page_views']:>6}  {item['visitors']:>8}  "
            f"{item['visitors_two_or_more_pages']:>8}  {percent(item['excluded']['share']):>8}  "
            f"{percent(item['excluded']['page_request_share']):>11}  "
            f"{item['excluded']['by_reason']['rotating_proxy']:>5}{mark}"
        )
    lines.append(
        f"{'total':<10}  {total['page_views']:>6}  {total['visitor_days']:>8}  (visitors summed by day)"
    )
    for title, rows, unit in (
        ("Top pages", total["top_pages"], "views"),
        ("Referrer hosts", total["referrers"], "views"),
        ("Countries", total["countries"], "visitor-days"),
    ):
        lines += ["", f"{title} ({unit})"]
        lines += [f"  {count:>6}  {name}" for name, count in rows] or ["  none"]
    lines += ["", "Excluded requests by reason"]
    lines += [f"  {count:>6}  {reason}" for reason, count in total["excluded_by_reason"].items()]
    clusters = [(item["date"], cluster) for item in aggregates for cluster in item["rotating_proxies"]]
    if clusters:
        lines += ["", "Rotating-proxy clusters (excluded)"]
        lines += [
            f"  {day}  {cluster['visitors']:>5} addresses  {cluster['browser']}  {cluster['path']}"
            for day, cluster in clusters
        ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--logs", nargs="+", help="access log files or directories")
    parser.add_argument(
        "--date", action="append", type=date.fromisoformat, help="UTC day (default yesterday)"
    )
    parser.add_argument("--output", type=Path, help="directory for YYYY-MM-DD.json aggregates")
    parser.add_argument("--keep-days", type=int, default=366, help="aggregate retention with --output")
    parser.add_argument("--report", action="store_true", help="print a report of aggregates")
    parser.add_argument("aggregates", nargs="*", type=Path, help="aggregate files for --report")
    parser.add_argument("--days", type=int, help="with --report, only the newest N days")
    parser.add_argument("--json", action="store_true", help="with --report, print combined JSON")
    args = parser.parse_args(argv)

    if args.report:
        text = "".join(path.read_text(encoding="utf-8") for path in args.aggregates) or sys.stdin.read()
        aggregates = sorted(parse_aggregates(text), key=lambda item: item["date"])
        if args.days:
            aggregates = aggregates[-args.days :]
        if args.json:
            print(json.dumps(combine(aggregates), indent=2))
        else:
            print(report(aggregates), end="")
        return 0
    if not args.logs or args.aggregates:
        parser.error("summaries need --logs and take no aggregate files")

    entries, first, malformed = read_entries(log_files(args.logs))
    today = datetime.now(UTC).date()
    if args.output:
        args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for day in args.date or [today - timedelta(days=1)]:
        # Each day is summarized under its own key.
        aggregate = summarize(entries, day, coverage_start=first, malformed=malformed)
        text = json.dumps(aggregate, indent=2, sort_keys=True) + "\n"
        if args.output:
            write_private(args.output / f"{day.isoformat()}.json", text)
            print(
                f"{day}: {aggregate['page_views']} page views, {aggregate['visitors']} visitors, "
                f"{percent(aggregate['excluded']['share'])} of {aggregate['requests']} requests excluded"
            )
        else:
            print(text, end="")
    if args.output:
        for name in prune(args.output, today, args.keep_days):
            print(f"removed {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
