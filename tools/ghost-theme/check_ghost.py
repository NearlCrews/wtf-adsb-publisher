#!/usr/bin/env python3
"""Render the packaged theme in the pinned Ghost image and look for the private admin origin.

Ghost runs offline in a temporary container with a scratch SQLite database. A
control run with Ghost's stock settings must show the leak; the run with the
example configuration's safeguards must not. Nothing touches the real site.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile

from airframe_photos import release_violations

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
ARTIFACTS = ROOT / "artifacts" / "ghost-theme"
sys.path.insert(0, str(ROOT / "src"))
from wtf_adsb_publisher import private_terms, site_origin  # noqa: E402

ADMIN = "https://admin-private.example.ts.net"
# The example configuration's public URL: Ghost answers as this site, and the
# owned blocks and static pages this check makes name the same origin.
PUBLIC = json.loads((ROOT / "deploy/ghost/ghost.production.example.json").read_text())["url"]
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
SAFEGUARDS = ("portal", "sodoSearch", "comments")
PAGES = ["/", "/stories/", "/stories/coming-soon/", "/about/", "/search-index/", "/sitemap.xml"]
OTHER = [
    "/ghost/",
    "/stories/coming-soon/edit/",
    "/ghost/api/content/posts/?key=none",
    "/no-such-page/",
    "/stories/rss/",
    "/topics/news/",
    "/sitemap-posts.xml",
    "/sitemap-pages.xml",
    "/robots.txt",
    "/members/api/site/",
    "/.well-known/recommendations.json",
    "/public/cards.min.js",
]
# Runs inside the container: request each path as the public host behind the proxy.
PROBE = """
const http = require('http');
const paths = JSON.parse(process.argv[1]);
const headers = {Host: process.argv[2], 'X-Forwarded-Proto': 'https'};
const get = (path) => new Promise((resolve, reject) => {
  const request = http.get({host: '127.0.0.1', port: 2368, path, headers}, (r) => {
    let body = '';
    r.on('data', (chunk) => (body += chunk));
    r.on('end', () => resolve({path, status: r.statusCode, headers: JSON.stringify(r.headers), body}));
  }).on('error', reject);
  request.setTimeout(10000, () => request.destroy(new Error('Offline Ghost page request timed out')));
});
Promise.all(paths.map(get)).then((results) => console.log(JSON.stringify(results)), () => process.exit(1));
"""
DATE_API_PROBE = """
const http = require('http');
const crypto = require('crypto');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const {key, bundle} = JSON.parse(input);
  const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
  const request = (method, path, body) => new Promise((resolve, reject) => {
    const iat = Math.floor(Date.now()/1000);
    const token = encode({alg:'HS256',typ:'JWT',kid:key.id}) + '.' + encode({iat,exp:iat+300,aud:'/admin/'});
    const signed = token + '.' + crypto.createHmac('sha256', Buffer.from(key.secret,'hex')).update(token).digest('base64url');
    const headers = {Host:'admin-private.example.ts.net','X-Forwarded-Proto':'https','Content-Type':'application/json','Accept-Version':'v6.0',Authorization:'Ghost '+signed};
    const req = http.request({host:'127.0.0.1',port:2368,method,path,headers}, response => {
      let text = ''; response.on('data',chunk=>text+=chunk);
      response.on('end',()=>{
        if(response.statusCode < 200 || response.statusCode >= 300) return reject(new Error('Offline Ghost API returned '+response.statusCode+': '+text.slice(0,1200)));
        try { resolve(JSON.parse(text).posts[0]); } catch(error) { reject(error); }
      });
    });
    req.on('error',reject);
    req.setTimeout(10000,()=>req.destroy(new Error('Offline Ghost API request timed out')));
    req.end(body ? JSON.stringify(body) : undefined);
  });
  const selected = post => Object.fromEntries(['id','slug','status','created_at','updated_at','published_at','email_only','email','newsletter'].map(key=>[key,post[key]??null]));
  try {
    const draft = await request(bundle.request.method, bundle.request.path, bundle.request.body);
    const path = '/ghost/api/admin/posts/'+draft.id+'/';
    const current = await request('GET',path);
    const released = await request('PUT',path,{posts:[{updated_at:current.updated_at,published_at:bundle.request.body.posts[0].published_at,status:'published'}]});
    const latest = await request('GET',path);
    const revised = await request('PUT',path+'?save_revision=true',{posts:[{updated_at:latest.updated_at,title:latest.title+' revised'}]});
    console.log(JSON.stringify({draft:selected(draft),released:selected(released),revised:selected(revised)}));
  } catch(error) { console.error(error.message); process.exitCode=1; }
});
"""
ADMIN_API_PROBE = """
const http = require('http');
const crypto = require('crypto');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const {key, requests} = JSON.parse(input);
  const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
  const request = ({method, path, body}) => new Promise((resolve, reject) => {
    if (!['GET', 'POST', 'PUT'].includes(method) || !path.startsWith('/ghost/api/admin/posts/')) {
      return reject(new Error('The offline fixture only accesses the local posts API'));
    }
    const iat = Math.floor(Date.now()/1000);
    const token = encode({alg:'HS256',typ:'JWT',kid:key.id}) + '.' + encode({iat,exp:iat+300,aud:'/admin/'});
    const signed = token + '.' + crypto.createHmac('sha256', Buffer.from(key.secret,'hex')).update(token).digest('base64url');
    const headers = {Host:'admin-private.example.ts.net','X-Forwarded-Proto':'https','Content-Type':'application/json','Accept-Version':'v6.0',Authorization:'Ghost '+signed};
    const req = http.request({host:'127.0.0.1',port:2368,method,path,headers}, response => {
      let text = ''; response.on('data',chunk=>text+=chunk);
      response.on('end',()=>{
        if (response.statusCode < 200 || response.statusCode >= 300) {
          return reject(new Error('Offline Ghost API returned '+response.statusCode+': '+text.slice(0,1200)));
        }
        try { resolve(JSON.parse(text).posts[0]); } catch(error) { reject(error); }
      });
    });
    req.on('error',reject);
    req.setTimeout(10000,()=>req.destroy(new Error('Offline Ghost API request timed out')));
    req.end(body ? JSON.stringify(body) : undefined);
  });
  try {
    const posts = [];
    for (const item of requests) posts.push(await request(item));
    console.log(JSON.stringify(posts));
  } catch(error) { console.error(error.message); process.exitCode=1; }
});
"""
DATE_HELPER_PROBE = """
const date = require('/var/lib/ghost/current/core/frontend/helpers/date');
const moment = require('/var/lib/ghost/current/node_modules/moment-timezone');
// Exercise the pinned helper with its own clock, independent of the host date.
for (const [instant, timezone, utcWeek, siteWeek] of [
  ['2026-10-04T23:59:59Z', 'Pacific/Kiritimati', '2026-W40', '2026-W41'],
  ['2026-10-05T00:00:00Z', 'Pacific/Honolulu', '2026-W41', '2026-W40'],
  ['2027-01-04T00:00:00Z', 'Pacific/Honolulu', '2027-W01', '2026-W53']
]) {
  moment.now = () => Date.parse(instant);
  const options = {hash:{format:'GGGG-[W]WW',timezone:'UTC'},data:{site:{timezone,locale:'en'}}};
  const current = String(date.call({}, options));
  const explicit = String(date.call({}, instant, options));
  delete options.hash.timezone;
  const local = String(date.call({}, options));
  if (current !== utcWeek || explicit !== utcWeek || local !== siteWeek) {
    throw new Error('Ghost date helper lost UTC calendar boundaries: '+JSON.stringify({instant,current,explicit,local}));
  }
}
"""


def private_text():
    """Private hosts, paths, and receiver fields: the built-in patterns and PRIVATE_TERMS_FILE."""
    return private_terms.compiled()


def story_path(base, day):
    """An article path the installed exporter accepts.

    The story engine's /stories/{slug}/ paths end in the observation date; older
    exporters accept a single root-level slug. Either way Ghost serves the post
    under routes.yaml's /stories/ permalink.
    """
    from wtf_adsb_publisher import public_events

    candidate = f"/stories/{base}-{int(day[8:10])}-{MONTHS[int(day[5:7]) - 1]}-{day[:4]}/"
    try:
        return public_events._article_path(candidate)
    except ValueError:
        return f"/{base}/"


def ghost_url(article_path):
    return article_path if article_path.startswith("/stories/") else "/stories" + article_path


def docker(*arguments, check=True):
    return subprocess.run(["docker", *arguments], check=check, capture_output=True, text=True)


def configuration(scratch, name, safeguards):
    config = json.loads((ROOT / "deploy/ghost/ghost.production.example.json").read_text())
    config["admin"]["url"] = ADMIN
    config["database"] = {
        "client": "sqlite3",
        "connection": {"filename": "/var/lib/ghost/content/data/ghost.db"},
        "useNullAsDefault": True,
    }
    config["mail"] = {"transport": "Direct"}
    if not safeguards:
        del config["admin"]["redirects"]
        for key in SAFEGUARDS:
            del config[key]
    path = scratch / name
    path.write_text(json.dumps(config))
    return path


def probe(container, paths):
    """Return Ghost's responses once it has finished starting."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = docker(
            "exec", container, "node", "-e", PROBE, json.dumps(paths), urlsplit(PUBLIC).hostname, check=False
        )
        if result.returncode == 0:
            responses = json.loads(result.stdout)
            # Ghost answers 503 while its first-boot migrations are still running.
            if all(response["status"] != 503 for response in responses):
                return responses
        time.sleep(2)
    raise SystemExit("Ghost did not answer within two minutes:\n" + docker("logs", container).stderr[-2000:])


def start(container, image, scratch, config):
    docker("rm", "--force", container, check=False)
    docker(
        "run",
        "--detach",
        "--name",
        container,
        "--network",
        "none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "NODE_ENV=development",
        "--volume",
        f"{scratch / 'content'}:/var/lib/ghost/content",
        "--volume",
        f"{config}:/var/lib/ghost/config.development.json:ro",
        image,
    )


def leaks(responses):
    private = private_text()
    return [r["path"] for r in responses if private.search(r["body"] + r["headers"])]


def admin_posts(container, key, requests):
    """Use the ephemeral key only inside the networkless container, never in argv."""
    result = subprocess.run(
        ["docker", "exec", "-i", container, "node", "-e", ADMIN_API_PROBE],
        input=json.dumps({"key": key, "requests": requests}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise ValueError("Offline weekly API check failed: " + result.stderr[-1600:])
    return json.loads(result.stdout)


def current_post(container, key, identifier):
    return admin_posts(
        container,
        key,
        [{"method": "GET", "path": f"/ghost/api/admin/posts/{identifier}/?formats=html,lexical"}],
    )[0]


def update_post(container, key, post, **changes):
    return admin_posts(
        container,
        key,
        [
            {
                "method": "PUT",
                "path": f"/ghost/api/admin/posts/{post['id']}/?save_revision=true",
                "body": {"posts": [{"updated_at": post["updated_at"], **changes}]},
            }
        ],
    )[0]


def publication_fixture():
    """Use the actual snapshot validator and card renderer, with invented input."""
    from wtf_adsb_publisher import public_events, publication

    ledger = public_events.new_ledger("2026-09-05")
    evidence = {
        "schema_version": 1,
        "kind": "circling",
        "observation": {"start_utc": "2026-10-03T23:55:00Z", "end_utc": "2026-10-04T00:05:00Z"},
        "aircraft": {"registration": "N12345", "type_code": "B407", "type_description": "Fixture helicopter"},
        "received": {
            "source": "mlat",
            "readings": [{"field": "gs", "value": 70}],
            "saved_readings_at_utc": "2026-10-03T23:58:17Z",
            "squawk": "1200",
        },
        "pattern": {
            "start_utc": "2026-10-03T23:57:00Z",
            "end_utc": "2026-10-04T00:02:00Z",
            "duration_seconds": 300,
            "closed_loops": 3,
            "quality_review_required": True,
        },
        "links": [],
    }
    editorial = {
        "title": "Offline observation fixture",
        "summary": "Three loops are present in this invented five-minute analysis window.",
        "article_path": story_path("coming-soon", "2026-10-03"),
        "synthetic": True,
    }
    public_events.put_event(ledger, "offline-fixture", evidence, editorial, at="2026-10-04T12:00:00Z")
    identifier = public_events.put_event(
        ledger, "offline-fixture", evidence, editorial, at="2026-10-04T13:00:00Z"
    )
    api_identifier = public_events.put_event(
        ledger,
        "offline-backdate-fixture",
        {
            **evidence,
            "observation": {
                **evidence["observation"],
                "start_utc": "2026-10-03T23:55:00.123456Z",
            },
        },
        {**editorial, "article_path": story_path("backdated-observation-check", "2026-10-03")},
        at="2026-10-04T13:00:00Z",
    )
    rated = rated_fixtures(ledger, editorial)
    snapshot = public_events.export_snapshot(ledger, mode="review", as_of="2026-10-04T20:00:00Z")
    event = next(item for item in snapshot["events"] if item["id"] == identifier)
    bundle = publication.draft_request(snapshot, api_identifier, prepared_at="2026-10-04T20:00:00Z")
    # The certified-limit cards ride in the same post, so Ghost renders their reference rows too.
    cards = [publication.event_card(snapshot, item) for item in (identifier, *rated)]
    return "".join(cards), event, bundle


def rated_fixtures(ledger, editorial):
    """Certified-limit findings shaped as the collector saves them, added to the ledger."""
    import site_fixtures

    return [
        site_fixtures.put(
            ledger,
            {
                "key": "offline-" + kind,
                "kind": kind,
                "rated": True,
                "start": start,
                "aircraft": {"hex": hexcode, "r": registration, "t": "B738", "desc": "BOEING 737-800"},
            },
            {**editorial, "article_path": story_path(kind.replace("_", "-") + "-check", start[:10])},
            "2026-10-04T13:00:00Z",
        )
        for kind, start, hexcode, registration in (
            ("above_rated_altitude", "2026-10-03T21:00:00Z", "a9c8b1", "N738RL"),
            ("above_rated_speed", "2026-10-03T22:00:00Z", "a9c8b2", "N738RS"),
        )
    ]


def withdrawn_pages():
    """The static pages that the published site fixture's withdrawn stories' URLs serve.

    Public Caddy serves these from the release where the Ghost post used to be,
    so they face the same private-name and third-party checks as Ghost's public
    responses. Returns {path: html}.
    """
    import site_fixtures

    snapshot = site_fixtures.site_snapshot("published", extra=False)
    script = (
        f"import {{ withdrawnPageHtml, withdrawnPages }} from {json.dumps((TOOLS / 'site-model.mjs').as_uri())};"
        "let input = '';"
        "for await (const chunk of process.stdin) input += chunk;"
        "const { retired, events } = JSON.parse(input);"
        "console.log(JSON.stringify(Object.fromEntries(withdrawnPages(retired, events).pages"
        ".map((page) => [page.path, withdrawnPageHtml(page)]))));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(snapshot),
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    pages = json.loads(result.stdout)
    if sorted(pages) != [entry["path"] for entry in snapshot["retired"]]:
        raise ValueError("The fixture's withdrawn stories did not each get a page")
    return pages


def seed_test_key(database):
    """An ephemeral admin integration exists only in the networkless scratch DB."""
    identifier, integration = secrets.token_hex(12), secrets.token_hex(12)
    secret = secrets.token_hex(32)
    role = database.execute("SELECT id FROM roles WHERE name='Admin Integration'").fetchone()
    if role is None:
        raise RuntimeError("Ghost's Admin Integration role is missing")
    database.execute(
        "INSERT INTO integrations (id,type,name,slug,created_at) VALUES (?,?,?,?,datetime('now'))",
        (integration, "custom", "Offline date check", "offline-date-check"),
    )
    database.execute(
        "INSERT INTO api_keys (id,type,secret,role_id,integration_id,created_at) VALUES (?,?,?,?,?,datetime('now'))",
        (identifier, "admin", secret, role[0], integration),
    )
    return {"id": identifier, "secret": secret}


def check_weekly(container, key, scratch):
    """Exercise generated native templates and same-post research through real Ghost."""
    sys.path.insert(0, str(ROOT / "tests"))
    from test_weekly_spotlight import fixture

    from wtf_adsb_publisher import public_events as public
    from wtf_adsb_publisher import publication
    from wtf_adsb_publisher import weekly_spotlight as weekly

    def stamp(value=None):
        return public.utc((value or datetime.now(timezone.utc)).isoformat())

    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def rendered_text(html):
        return " ".join(unescape(re.sub(r"<[^>]*>", " ", html)).split())

    def cards(html):
        return re.findall(r'<article class="weekly-card">.*?</article>', html, re.S)

    def homepage_box(html):
        match = re.search(r'<section class="wrap weekly-spotlight".*?</section>', html, re.S)
        require(match is not None, "The native homepage lost its weekly section")
        return match[0]

    now = datetime.now(timezone.utc)
    monday = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    literal = 'Café — "quoted" \\ path {{literal}}'
    reason = literal + "\nA synthetic observation supports checking native research rendering."
    replay = '<figure data-replay-src="/offline-replay.json"><figcaption>Preserved offline replay</figcaption></figure>'
    records, posts = [], []
    print("Checking researched weekly articles inside offline Ghost...", flush=True)
    docker("exec", container, "node", "-e", DATE_HELPER_PROBE)
    for index in range(5):
        first = monday - timedelta(weeks=index + 1) + timedelta(hours=12)
        ledger, identifier, _, _ = fixture(stamp(first), stamp(first + timedelta(minutes=10)), stamp())
        record = ledger["records"][0]
        record["episode_key"] = f"offline-weekly-{index}"
        metadata = deepcopy(record["editorial"])
        metadata.update(
            title=f"Offline weekly {index}: {literal}",
            article_path=story_path(f"weekly-native-{index}", stamp(first)[:10]),
        )
        metadata["weekly_spotlight"]["reason"] = reason
        public.put_event(ledger, record["episode_key"], record["evidence"], metadata, at=stamp())
        public.set_review_state(ledger, identifier, "approved", at=stamp())
        review = public.export_snapshot(ledger, mode="review", as_of=stamp())
        event = review["events"][0]
        bundle = publication.draft_request(review, identifier, prepared_at=stamp())
        initial = bundle["request"]["body"]["posts"][0]
        lexical = json.loads(initial["lexical"])
        research = weekly.research_html(event)
        require(research in lexical["root"]["children"][0]["html"], "The draft omitted researched content")
        lexical["root"]["children"][0]["html"] = lexical["root"]["children"][0]["html"].replace(research, "")
        lexical["root"]["children"].append({"type": "html", "version": 1, "html": replay})
        initial.update(lexical=json.dumps(lexical), tags=[{"name": "Offline preserved topic"}])
        draft = admin_posts(container, key, [bundle["request"]])[0]
        published = update_post(
            container, key, draft, status="published", published_at=initial["published_at"]
        )
        current = current_post(container, key, published["id"])
        metadata["ghost_id"] = current["id"]
        record = ledger["records"][0]
        public.put_event(ledger, record["episode_key"], record["evidence"], metadata, at=stamp())
        public.set_review_state(ledger, identifier, "approved", at=stamp())
        review = public.export_snapshot(ledger, mode="review", as_of=stamp())
        update = publication.research_update_request(
            review, identifier, current, expected_ghost_id=current["id"]
        )
        admin_posts(container, key, [update["request"]])
        saved = current_post(container, key, current["id"])
        require(saved["id"] == draft["id"], "Weekly research created another Ghost article")
        require(saved["status"] == "published", "Weekly research changed publication state")
        require(
            saved["published_at"] == published["published_at"], "Weekly research changed the observation date"
        )
        require(
            saved["created_at"] == draft["created_at"], "Weekly research changed the original import time"
        )
        require("Preserved offline replay" in saved["html"], "Weekly research removed the existing replay")
        require(
            saved["html"].count("data-weekly-research=") == 1, "Ghost lost or duplicated the research block"
        )
        require('href="#weekly-source-manufacturer"' in saved["html"], "Ghost lost research citations")
        require(
            any(tag["name"] == "Offline preserved topic" for tag in saved["tags"]),
            "Weekly research replaced an unrelated tag",
        )
        require(
            not saved.get("email_only") and saved.get("email") is None and saved.get("newsletter") is None,
            "Weekly research initiated email delivery",
        )
        records.extend(ledger["records"])
        posts.append(saved)

    # More unapproved tagged posts than one page expose filtering performed
    # after pagination: these newer posts must never push approved picks away.
    noise = []
    for index in range(11):
        noise.append(
            {
                "method": "POST",
                "path": "/ghost/api/admin/posts/",
                "body": {
                    "posts": [
                        {
                            "title": f"Unapproved offline weekly {index}",
                            "slug": f"weekly-unapproved-{index}",
                            "status": "published",
                            "visibility": "public",
                            "email_only": False,
                            "lexical": json.dumps(
                                {
                                    "root": {
                                        "type": "root",
                                        "version": 1,
                                        "children": [
                                            {
                                                "type": "html",
                                                "version": 1,
                                                "html": "<p>Synthetic unapproved archive fixture.</p>",
                                            }
                                        ],
                                    }
                                }
                            ),
                            "tags": [{"name": "Weekly spotlight"}],
                        }
                    ]
                },
            }
        )
    noise_posts = admin_posts(container, key, noise)
    ledger = public.new_ledger(records[-1]["evidence"]["observation"]["start_utc"][:10])
    ledger["records"] = records
    inventory = {
        "complete": True,
        "total": len(posts) + len(noise_posts),
        "as_of_utc": stamp(),
        "posts": [*posts, *noise_posts],
    }
    snapshot = public.export_snapshot(
        ledger, mode="published", as_of=inventory["as_of_utc"], inventory=inventory
    )
    require(
        sum("weekly_spotlight" in event for event in snapshot["events"]) == 5,
        "Real Ghost content did not satisfy weekly publication checks",
    )
    require(len(snapshot["events"]) == 5, "Weekly research changed the counted event inventory")
    require(
        weekly.previous_week(stamp())[0] == weekly.previous_week(snapshot["as_of_utc"])[0],
        "The UTC week changed during the native fixture; rerun the check",
    )

    docker("stop", container)
    theme = scratch / "content/themes/wtf-adsb"
    for name, content in weekly.native_partials(snapshot).items():
        require(not private_text().search(content), "Native weekly templates expose private provenance")
        require(not any(post["id"] in content for post in posts), "Native weekly templates expose Ghost IDs")
        (theme / name).write_text(content)
    routes = (ROOT / "deploy/ghost-preview/routes.yaml").read_text()
    placeholder = "filter: tag:hash-weekly-unconfigured"
    require(routes.count(placeholder) == 1, "The native weekly route placeholder changed")
    routes = routes.replace(placeholder, "filter: " + json.dumps(weekly.native_archive_filter(snapshot)))
    (scratch / "content/settings/routes.yaml").write_text(routes)
    docker("start", container)

    paths = [
        "/",
        "/weekly/",
        "/weekly/page/2/",
        *[ghost_url(event["article_path"]) for event in snapshot["events"]],
    ]
    responses = {row["path"]: row for row in probe(container, paths)}
    require(not leaks(responses.values()), "A native weekly response exposes private metadata")
    home = homepage_box(responses["/"]["body"])
    require(len(cards(home)) == 1, "The native homepage did not render exactly one current weekly pick")
    require("Most Interesting Last Week" in home, "The native homepage lost its current-week heading")
    require(
        " ".join(literal.split()) in rendered_text(home),
        "Native weekly text changed Unicode, quotes, backslashes, or braces",
    )
    require(
        " ".join(reason.split()) in rendered_text(home),
        "Native weekly text changed a multiline editorial reason",
    )
    archive = responses["/weekly/"]
    require(
        archive["status"] == 200 and len(cards(archive["body"])) == 5,
        "The native archive paginated before filtering approved picks",
    )
    require(
        "Page 1 of 1" in rendered_text(archive["body"]), "Native archive pagination counts rejected picks"
    )
    require(
        responses["/weekly/page/2/"]["status"] == 404,
        "Unapproved tagged posts created another archive page",
    )
    for event in snapshot["events"]:
        article = responses[ghost_url(event["article_path"])]
        require(
            article["status"] == 200 and "Research sources" in article["body"],
            "The native weekly link lacks its researched article",
        )

    # Each changed post exercises a different live guard without republishing
    # the snapshot. The current pick remains until its own revision changes.
    update_post(container, key, posts[1], tags=[{"name": "Weekly spotlight"}])
    update_post(container, key, posts[2], visibility="members")
    update_post(container, key, posts[3], status="draft")
    changed = {row["path"]: row for row in probe(container, ["/", "/weekly/"])}
    require(
        len(cards(homepage_box(changed["/"]["body"]))) == 1, "Changing an older pick removed the current pick"
    )
    require(
        len(cards(changed["/weekly/"]["body"])) == 2,
        "Tag removal, private visibility, or withdrawal remained in the native archive",
    )
    update_post(container, key, posts[0], title=posts[0]["title"] + " changed after approval")
    changed = {row["path"]: row for row in probe(container, ["/", "/weekly/"])}
    home = homepage_box(changed["/"]["body"])
    require(
        not cards(home) and "No researched pick is ready for last week" in home,
        "A changed weekly revision remained current or an older pick replaced it",
    )
    require(
        len(cards(changed["/weekly/"]["body"])) == 1,
        "A changed revision remained in the native archive or removed an older approved pick",
    )
    update_post(container, key, posts[4], status="draft")
    empty_archive = probe(container, ["/weekly/"])[0]
    require(not cards(empty_archive["body"]), "A withdrawn pick remained in the native archive")
    require(
        "No researched weekly picks have been published yet" in empty_archive["body"],
        "A fully filtered native archive has no empty state",
    )


def main():
    os.environ[site_origin.ENVIRONMENT] = site_origin.validate(PUBLIC)
    package = json.loads((ROOT / "themes/wtf-adsb/package.json").read_text())
    archive = ARTIFACTS / f"wtf-adsb-{package['version']}.zip"
    if not archive.is_file():
        raise SystemExit("Package the theme first: make lint-theme")
    compose = docker(
        "compose",
        "--env-file",
        str(ROOT / "deploy/ghost/.env.example"),
        "-f",
        str(ROOT / "deploy/ghost/compose.yaml"),
        "config",
        "--format",
        "json",
    )
    image = json.loads(compose.stdout)["services"]["ghost"]["image"]
    if docker("image", "inspect", image, check=False).returncode:
        raise SystemExit(f"This check runs offline. Pull the pinned image first: docker pull {image}")
    # A name fixed per checkout lets the next run remove a container that a
    # killed run left behind, without touching a check running in another worktree.
    container = "wtf-adsb-ghost-check-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    scratch = Path(tempfile.mkdtemp(prefix="wtf-adsb-ghost-check-"))
    failures = []
    try:
        theme = scratch / "content/themes/wtf-adsb"
        theme.mkdir(parents=True)
        with ZipFile(archive) as packaged:
            packaged.extractall(theme)
        (scratch / "content/settings").mkdir()
        shutil.copy(ROOT / "deploy/ghost-preview/routes.yaml", scratch / "content/settings/routes.yaml")
        stock = configuration(scratch, "stock.json", safeguards=False)
        guarded = configuration(scratch, "guarded.json", safeguards=True)

        # First boot creates the database; then select the packaged theme.
        start(container, image, scratch, stock)
        probe(container, ["/"])
        docker("stop", container)
        card, event, bundle = publication_fixture()
        lexical = {
            "root": {
                "children": [{"type": "html", "version": 1, "html": card}],
                "direction": None,
                "format": "",
                "indent": 0,
                "type": "root",
                "version": 1,
            }
        }
        with sqlite3.connect(scratch / "content/data/ghost.db") as database:
            database.execute("UPDATE settings SET value='wtf-adsb' WHERE key='active_theme'")
            # Ghost's seeded post takes its feature image from static.ghost.org.
            # Our posts carry none, so the fixture drops it, and the third-party
            # check below covers what the theme and configuration load.
            updated = database.execute(
                "UPDATE posts SET html=?, lexical=?, feature_image=NULL WHERE slug='coming-soon'",
                (card, json.dumps(lexical)),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Ghost's expected fixture post is missing")
            key = seed_test_key(database)
        docker("start", container)
        control = probe(container, PAGES)
        if "/" not in leaks(control):
            failures.append(
                "Control: stock Ghost settings no longer put the admin origin on the home page; "
                "this check can no longer show that the safeguards work."
            )

        start(container, image, scratch, guarded)
        responses = {r["path"]: r for r in probe(container, PAGES + OTHER)}
        # Requests are confined to 127.0.0.1 inside a --network none container.
        # No newsletter query is supplied, and no request uses scheduled status.
        try:
            date_check = subprocess.run(
                ["docker", "exec", "-i", container, "node", "-e", DATE_API_PROBE],
                input=json.dumps({"key": key, "bundle": bundle}),
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            date_check = None
            failures.append("Offline Ghost post-date API check exceeded one minute.")
        if date_check is not None and date_check.returncode:
            failures.append("Offline Ghost post-date API check failed: " + date_check.stderr[-1600:])
        elif date_check is not None:
            dates = json.loads(date_check.stdout)
            expected = bundle["request"]["body"]["posts"][0]["published_at"]
            for stage, post in dates.items():
                if datetime.fromisoformat(post["published_at"]) != datetime.fromisoformat(expected):
                    failures.append(
                        f"Ghost changed the observation date during {stage}: "
                        f"expected {expected}, received {post['published_at']}."
                    )
                if post["created_at"] <= expected:
                    failures.append(f"Ghost backdated the import timestamp during {stage}.")
                if post["email"] is not None or post["newsletter"] is not None or post["email_only"]:
                    failures.append(f"Ghost associated email delivery with the {stage} request.")
            if dates["draft"]["status"] != "draft" or dates["released"]["status"] != "published":
                failures.append("Ghost did not preserve the draft-first publication sequence.")
            if dates["draft"]["created_at"] != dates["revised"]["created_at"]:
                failures.append("A revision changed Ghost's actual post creation time.")
            backdated = probe(container, [ghost_url(bundle["audit"]["article_path"])])[0]
            if backdated["status"] != 200 or 'datetime="2026-10-03"' not in backdated["body"]:
                failures.append("The native Ghost post date does not show the observation day.")
            if 'datetime="2026-10-03T23:55:00.123456Z"' not in backdated["body"]:
                failures.append("The native observation card lost the original timestamp precision.")
            if private_text().search(backdated["body"] + backdated["headers"]):
                failures.append("The backdated native post names private metadata.")
        home = responses["/"]["body"]
        if "/assets/js/site.js" not in home:
            failures.append("The packaged theme is not the active theme.")
        for path in PAGES:
            if responses[path]["status"] != 200:
                failures.append(f"{path} returned {responses[path]['status']}")
        for path in leaks(responses.values()):
            failures.append(f"{path} names a private host, path, or receiver field.")
        # The only third-party load a public page may make is an airframe photo's
        # image, and Ghost renders none.
        for path, response in responses.items():
            page = "text/html" in response["headers"].lower()
            for found in release_violations(response["body"].encode(), page=page):
                failures.append(f"{path} loads a third-party resource: {found}")
        for script in ("portal.min.js", "sodo-search"):
            if script in home:
                failures.append(f"The home page still loads {script}.")
        if responses["/ghost/"]["status"] != 404:
            failures.append(f"/ghost/ returned {responses['/ghost/']['status']} on the public host.")
        article = responses["/stories/coming-soon/"]["body"]
        for expected in (
            f'data-event-id="{event["id"]}"',
            f'data-evidence-version="{event["evidence_version"]}"',
            'datetime="2026-10-03T23:55:00Z"',
            'datetime="2026-10-04T00:05:00Z"',
            'datetime="2026-10-03T23:58:17Z"',
            'datetime="2026-10-03T23:57:00Z"',
            'datetime="2026-10-04T00:02:00Z"',
            "Multilateration (MLAT)",
            "Source or geometry quality requires review",
            "Transponder code",
            "Record revision: 2",
            "Unpublished review",
            "Certified maximum operating altitude",
            "Reference figure: a certified limit, a modelled wind, or a registry record",
            "Type confirmed by a second source",
            "Weather data by Open-Meteo.com (CC BY 4.0)",
        ):
            if expected not in article:
                failures.append(f"Native Ghost content lost observation metadata: {expected}")
        try:
            index = json.loads(responses["/search-index/"]["body"])
            if not index or not {"title", "url", "excerpt", "topic"} <= set(index[0]):
                failures.append("The search index has no usable entries.")
        except ValueError:
            failures.append("The search index is not valid JSON.")
        try:
            check_weekly(container, key, scratch)
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            failures.append("Native weekly check failed: " + str(error))
        # A withdrawn story's URL now serves a static noindex redirect page. It
        # names nothing private and loads nothing, from this site or another.
        try:
            withdrawn = withdrawn_pages()
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            withdrawn = {}
            failures.append("Withdrawn story pages could not be built: " + str(error))
        for path, html in withdrawn.items():
            if private_text().search(html):
                failures.append(
                    f"The withdrawn story page {path} names a private host, path, or receiver field."
                )
            for found in release_violations(html.encode(), page=True, story=True):
                failures.append(f"The withdrawn story page {path} loads a third-party resource: {found}")
            if '<meta name="robots" content="noindex">' not in html or re.search(
                r"<script|<link (?!rel=\"canonical\")", html
            ):
                failures.append(
                    f"The withdrawn story page {path} is indexable or loads a script or stylesheet."
                )
    finally:
        docker("rm", "--force", container, check=False)
        shutil.rmtree(scratch, ignore_errors=True)
    if failures:
        raise SystemExit("Ghost privacy check failed:\n- " + "\n- ".join(failures))
    print(
        f"Ghost privacy check passed: {len(PAGES) + len(OTHER) + 1} public responses from {image.split('@')[0]} "
        "name no private host; the control run without the safeguards did. "
        "The native article preserves observation dates, scoped measurements, and record metadata. "
        "The isolated API preserves an observation-date post through draft, web publication, and revision without email. "
        "Native weekly articles retain research and replay, and their homepage/archive guards filter changed posts before pagination. "
        "Withdrawn stories' static redirect pages are noindex, load nothing, and name nothing private."
    )


if __name__ == "__main__":
    sys.exit(main())
