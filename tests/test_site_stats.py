"""Readership summaries from synthetic Caddy access log lines."""

import contextlib
import gzip
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import site_stats

DAY = date(2026, 10, 22)
NOON = datetime(2026, 10, 22, 12, tzinfo=UTC).timestamp()
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"
SAFARI = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)
CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
# The user agent our verify-live.mjs and check-production.mjs send.
OWN_CHECK = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "HeadlessChrome/154.0.0.0 Safari/537.36"
)


def record(
    path, ip="198.51.100.1", agent=FIREFOX, status=200, method="GET", referer="", country="US", at=NOON
):
    """One access log line as the public Caddyfile writes it."""
    return {
        "level": "info",
        "ts": at,
        "logger": "http.log.access.log0",
        "msg": "handled request",
        "request": {"client_ip": ip, "method": method, "uri": path},
        "status": status,
        "country": country,
        "referer": referer,
        "user_agent": agent,
    }


def entries(*records):
    return [site_stats.parse_entry(item) for item in records]


def summary(*records, salt=b"k" * 32):
    return site_stats.summarize(entries(*records), DAY, salt=salt)


class SiteStatsTest(unittest.TestCase):
    def test_page_views_visitors_and_readers_of_two_or_more_pages(self):
        result = summary(
            record("/", ip="198.51.100.1"),
            record("/stories/a/", ip="198.51.100.1"),
            # A repeat view revalidates: 304 is a page view.
            record("/stories/a/", ip="198.51.100.2", status=304),
            record("/stories/a/", ip="198.51.100.2"),
            record("/about/", ip="198.51.100.3", agent=SAFARI),
        )
        self.assertEqual(result["page_views"], 5)
        self.assertEqual(result["visitors"], 3)
        # The second reader saw one page twice, which is not two pages.
        self.assertEqual(result["visitors_two_or_more_pages"], 1)
        self.assertEqual(
            result["top_pages"],
            [
                {"path": "/stories/a/", "views": 3, "visitors": 2},
                {"path": "/", "views": 1, "visitors": 1},
                {"path": "/about/", "views": 1, "visitors": 1},
            ],
        )
        self.assertEqual(result["excluded"]["requests"], 0)

    def test_each_exclusion_has_its_reason(self):
        bots = [
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)",
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0)",
            "Mozilla/5.0 (compatible; Amazonbot/0.1)",
            "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15 (Applebot/0.1)",
            "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
            "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider)",
            "Mozilla/5.0 (compatible; SemrushBot/7~bl)",
            "Mozilla/5.0 (compatible; AhrefsBot/7.0)",
            "Mozilla/5.0 (compatible; Googlebot/2.1)",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) PhantomJS/2.1.1 Safari/538.1",
            "python-requests/2.32.3",
            "curl/8.10.1",
            "Wget/1.24.5",
            "Go-http-client/2.0",
            "",
        ]
        records = [
            record("/stories/a/", ip=f"192.0.2.{index}", agent=agent) for index, agent in enumerate(bots)
        ]
        records += [
            record("/", ip="203.0.113.50", agent=OWN_CHECK),
            record("/assets/css/screen.css", ip="203.0.113.50", agent=OWN_CHECK),
            # A scanner's probes and its other requests.
            record("/wp-admin/install.php", ip="203.0.113.60", agent=CHROME, status=404),
            record("/xmlrpc.php", ip="203.0.113.60", agent=CHROME, status=404),
            record("/", ip="203.0.113.60", agent=CHROME),
            record("/.env", ip="203.0.113.61", agent=CHROME, status=404),
            record("/.git/config", ip="203.0.113.62", agent=CHROME, status=404),
            record("/%2e%2e/%2e%2e/etc/passwd", ip="203.0.113.63", agent=CHROME, status=404),
            # A reader's own requests: methods, files and a missing page.
            record("/", method="HEAD"),
            record("/", method="POST", status=405),
            record("/assets/css/screen.css"),
            record("/assets/js/site.js"),
            record("/social/" + "a" * 64 + ".png"),
            record("/event-media/" + "b" * 64 + ".svg", status=304),
            record("/favicon.ico"),
            record("/apple-touch-icon.png"),
            record("/robots.txt"),
            record("/sitemap.xml"),
            record("/rss/"),
            record("/search-index/"),
            record("/stories/missing/", status=404),
            record("/stories/a/"),
        ]
        result = summary(*records)
        self.assertEqual(result["page_views"], 1)
        self.assertEqual(result["visitors"], 1)
        self.assertEqual(
            result["excluded"]["by_reason"],
            {
                "own_checks": 2,
                "bot": len(bots),
                "scanner": 6,
                "method": 2,
                "asset": 10,
                "status": 1,
                "rotating_proxy": 0,
            },
        )
        self.assertEqual(result["requests"], len(records))
        self.assertEqual(result["excluded"]["requests"], len(records) - 1)
        self.assertEqual(result["excluded"]["share"], round((len(records) - 1) / len(records), 4))
        # Page requests: every GET 200/304 for a page, whoever sent it.
        self.assertEqual(result["excluded"]["page_requests"], len(bots) + 3)
        self.assertEqual(result["excluded"]["page_request_share"], round(1 - 1 / (len(bots) + 3), 4))

    def test_readers_in_app_browsers_are_not_bots(self):
        for agent in (
            SAFARI,
            CHROME,
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Mobile/15E148 [LinkedInApp]/9.30",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Mobile/15E148 [FBAN/FBIOS;FBAV/480.0]",
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 "
            "Mobile Safari/537.36 Instagram 350.0.0.0",
            "Mozilla/5.0 (Linux; Android 10; CUBOT X30) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile",
        ):
            self.assertFalse(site_stats.is_bot(agent), agent)

    def test_a_rotating_proxy_cluster_is_excluded_and_reported(self):
        proxy = [
            record("/stories/b/", ip=f"10.0.{index // 250}.{index % 250}", agent=CHROME)
            for index in range(166)
        ]
        # Nineteen single-request clients are below the threshold.
        few = [record("/stories/c/", ip=f"10.9.0.{index}", agent=CHROME) for index in range(19)]
        # Readers who also load a file are not single-request clients.
        readers = []
        for index in range(25):
            ip = f"10.8.0.{index}"
            readers += [
                record("/stories/d/", ip=ip, agent=SAFARI),
                record("/assets/js/site.js", ip=ip, agent=SAFARI),
            ]
        result = summary(*proxy, *few, *readers)
        self.assertEqual(
            result["rotating_proxies"], [{"path": "/stories/b/", "browser": "Chrome 120", "visitors": 166}]
        )
        self.assertEqual(result["excluded"]["by_reason"]["rotating_proxy"], 166)
        self.assertEqual(result["page_views"], 19 + 25)
        self.assertEqual({row["path"] for row in result["top_pages"]}, {"/stories/c/", "/stories/d/"})

    def test_distinct_visitors_hash_ip_and_user_agent_under_a_run_key(self):
        records = [
            record("/", ip="198.51.100.7", agent=FIREFOX),
            record("/", ip="198.51.100.7", agent=SAFARI),
            record("/", ip="198.51.100.8", agent=FIREFOX),
            record("/about/", ip="198.51.100.8", agent=FIREFOX),
        ]
        self.assertEqual(summary(*records)["visitors"], 3)
        # The key changes the digest but never the counts.
        self.assertEqual(summary(*records, salt=b"a" * 32), summary(*records, salt=b"b" * 32))
        first = site_stats.visitor_key(b"a" * 32, "198.51.100.7", FIREFOX)
        self.assertEqual(first, site_stats.visitor_key(b"a" * 32, "198.51.100.7", FIREFOX))
        self.assertNotEqual(first, site_stats.visitor_key(b"b" * 32, "198.51.100.7", FIREFOX))
        self.assertNotEqual(first, site_stats.visitor_key(b"a" * 32, "198.51.100.7", SAFARI))
        # Without a key, each run makes its own.
        self.assertEqual(site_stats.summarize(entries(*records), DAY)["visitors"], 3)

    def referrers(self):
        return summary(
            record("/stories/a/", ip="198.51.100.1", referer="news.ycombinator.com", country="US"),
            record("/stories/a/", ip="198.51.100.2", referer="NEWS.ycombinator.com", country="DE"),
            record("/stories/b/", ip="198.51.100.2", referer="site.example", country="DE"),
            record("/", ip="198.51.100.3", referer="www.site.example", country="xx"),
            # An older proxy or client may still send a full URL.
            record(
                "/", ip="198.51.100.4", referer="https://user@old.reddit.com:443/r/ADSB/?a=1", country="T1"
            ),
            record("/", ip="198.51.100.5", referer="not a host", country="Germany"),
            record("/", ip="198.51.100.6", referer="", country=""),
        )

    def test_referrer_hosts_and_countries(self):
        with patch.dict(os.environ, {"SITE_ORIGIN": "https://site.example"}):
            result = self.referrers()
        self.assertEqual(
            result["referrers"],
            [
                {"host": "news.ycombinator.com", "views": 2},
                {"host": "(other)", "views": 1},
                {"host": "old.reddit.com", "views": 1},
            ],
        )
        self.assertEqual(
            result["countries"],
            [
                {"country": "(unknown)", "visitors": 2},
                {"country": "DE", "visitors": 1},
                {"country": "T1", "visitors": 1},
                {"country": "US", "visitors": 1},
                {"country": "XX", "visitors": 1},
            ],
        )
        # The site's own hosts come from SITE_ORIGIN; without it, from the example origin.
        self.assertEqual(site_stats.site_hosts({}), {"example.com", "www.example.com"})
        self.assertEqual(
            site_stats.site_hosts({"SITE_ORIGIN": "https://www.site.example"}),
            {"site.example", "www.site.example"},
        )
        with patch.dict(os.environ):
            os.environ.pop("SITE_ORIGIN", None)
            self.assertIn({"host": "site.example", "views": 1}, self.referrers()["referrers"])
        for value in ("http://site.example", "https://site.example/", "https://Site.example", "site.example"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                site_stats.site_hosts({"SITE_ORIGIN": value})

    def test_only_the_requested_utc_day_is_counted(self):
        midnight = datetime(2026, 10, 22, tzinfo=UTC).timestamp()
        result = summary(
            record("/", at=midnight - 1),
            record("/", at=midnight),
            record("/", at=midnight + 86399.9),
            record("/", at=midnight + 86400),
        )
        self.assertEqual((result["requests"], result["page_views"]), (2, 2))

    def test_the_tool_writes_private_aggregates_without_ips_queries_or_user_agents(self):
        agent = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/131.0 DistinctiveBuild/7.7"
        lines = [
            record(
                "/stories/a/?utm_source=newsletter&email=reader%40example.com",
                ip="198.51.100.77",
                agent=agent,
            ),
            record("/stories/b/#section", ip="2001:db8::77", agent=agent, referer="news.example.org"),
            record("/wp-login.php?redirect=1", ip="203.0.113.99", agent=CHROME, status=404),
        ]
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder, "access")
            logs.mkdir()
            earlier = record("/", at=NOON - 2 * 86400)
            with gzip.open(logs / "public-2026-10-21T00-00-00.000-size.log.gz", "wt") as handle:
                handle.write(json.dumps(earlier) + "\n")
            (logs / "public.log").write_text(
                "".join(json.dumps(line) + "\n" for line in lines) + "not json\n", encoding="utf-8"
            )
            (logs / "unrelated.txt").write_text("ignored", encoding="utf-8")
            output = Path(folder, "stats")
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                site_stats.main(["--logs", str(logs), "--date", DAY.isoformat(), "--output", str(output)])
            target = output / "2026-10-22.json"
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            text = target.read_text(encoding="utf-8")
            result = json.loads(text)
            report = io.StringIO()
            with contextlib.redirect_stdout(report):
                site_stats.main(["--report", str(target)])
            printed_summary = io.StringIO()
            with contextlib.redirect_stdout(printed_summary):
                site_stats.main(["--logs", str(logs), "--date", DAY.isoformat()])
        self.assertEqual((result["page_views"], result["visitors"]), (2, 2))
        self.assertEqual(result["excluded"]["by_reason"]["scanner"], 1)
        self.assertTrue(result["logs_cover_whole_day"])
        self.assertEqual(result["malformed_lines"], 1)
        self.assertEqual({row["path"] for row in result["top_pages"]}, {"/stories/a/", "/stories/b/"})
        self.assertIn("2026-10-22: 2 page views, 2 visitors", printed.getvalue())
        for output_text in (text, report.getvalue(), printed.getvalue(), printed_summary.getvalue()):
            for secret in (
                "198.51.100.77",
                "2001:db8::77",
                "203.0.113.99",
                "utm_source",
                "reader",
                "redirect",
            ):
                self.assertNotIn(secret, output_text)
            self.assertNotIn("DistinctiveBuild", output_text)
            self.assertNotIn("Gecko", output_text)
            self.assertNotIn("#section", output_text)

    def test_partial_logs_are_marked(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder, "public.log")
            log.write_text(json.dumps(record("/")) + "\n", encoding="utf-8")
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                site_stats.main(["--logs", str(log), "--date", DAY.isoformat()])
        self.assertFalse(json.loads(printed.getvalue())["logs_cover_whole_day"])
        with self.assertRaises(SystemExit):
            site_stats.main(["--logs", "/nonexistent/access", "--date", DAY.isoformat()])

    def test_a_report_reads_concatenated_aggregates(self):
        first = summary(record("/", ip="198.51.100.1", referer="lobste.rs", country="CA"))
        second = site_stats.summarize(
            entries(
                record("/", ip="198.51.100.1", at=NOON + 86400),
                record("/about/", ip="198.51.100.1", at=NOON + 86400),
            ),
            date(2026, 10, 23),
        )
        stream = json.dumps(second, indent=2) + "\n" + json.dumps(first, indent=2) + "\n"
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), patch("sys.stdin", io.StringIO(stream)):
            site_stats.main(["--report"])
        text = printed.getvalue()
        self.assertIn("Readership, 2026-10-22 to 2026-10-23 (2 days)", text)
        self.assertLess(text.index("\n2026-10-22 "), text.index("\n2026-10-23 "))
        self.assertIn("lobste.rs", text)
        self.assertRegex(text, r"\ntotal\s+3\s+2\s")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), patch("sys.stdin", io.StringIO(stream)):
            site_stats.main(["--report", "--days", "1", "--json"])
        combined = json.loads(printed.getvalue())
        self.assertEqual((combined["days"], combined["first"], combined["page_views"]), (1, "2026-10-23", 2))
        self.assertEqual(combined["top_pages"], [["/", 1], ["/about/", 1]])

    def test_aggregates_are_kept_for_a_year(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for name in ("2025-10-22.json", "2025-10-23.json", "2026-10-22.json", "notes.txt"):
                (directory / name).write_text("{}", encoding="utf-8")
            removed = site_stats.prune(directory, date(2026, 10, 23), 365)
            self.assertEqual(removed, ["2025-10-22.json"])
            self.assertEqual(
                sorted(path.name for path in directory.iterdir()),
                ["2025-10-23.json", "2026-10-22.json", "notes.txt"],
            )


if __name__ == "__main__":
    unittest.main()
