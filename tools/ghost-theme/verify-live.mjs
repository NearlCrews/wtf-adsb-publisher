// Live checks of a published journal release, at desktop and phone widths.
//
// Checks every article in the published snapshot (status, private text, robots,
// one JSON-LD graph, a raster og:image), the sitemap index and its children,
// the feed, robots.txt, the root icons, the private and retired routes,
// layouts, both searches,
// replay, and the story and weekly archives with and without JavaScript. Each
// withdrawn story's path in the snapshot, and each --expect-redirect PATH=HUB,
// must serve a noindex page that sends readers to its hub, loads nothing, and
// stays out of the sitemaps, feed, and search. A page may load nothing from
// another origin except an airframe photo's image named by its article's
// figure, and the proxy's img-src allows exactly that host. The edge address
// is passed in, never stored: --edge maps the site's host to it with normal
// TLS validation. --serve DIR checks a built site on a local server, skipping
// the checks that need the public proxy (headers, caching, private routes, the
// www redirect). A --base other than the --canonical origin, such as a proxy
// under test, gets every check but the www redirect.
//
// node tools/ghost-theme/verify-live.mjs --base https://example.com --edge ADDRESS \
//   --snapshot snapshot.json --media manifest.json [--config config.json] \
//   [--expect-404 paths.txt ...] [--expect-redirect /stories/SLUG/=/aircraft/TYPE/ ...] \
//   [--output results.json]
// node tools/ghost-theme/verify-live.mjs --serve artifacts/ghost-theme/DIR/site --snapshot ...
// node tools/ghost-theme/verify-live.mjs --base http://127.0.0.1:PORT \
//   --canonical https://example.com --snapshot ... --media ...
//
// Each group of checks is a named function below, run in order at the end.

/* global document, innerWidth */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { parseArgs } from "node:util";
import { chromium } from "playwright";
import {
  AIRFRAME_LINK_HOST,
  IMAGE_POLICY,
  PHOTO_DATA,
  guardRequests,
  thirdPartyResources,
} from "./airframe-photos.mjs";
import { executablePath } from "./browser.mjs";
import { DISALLOWED, FEED_SIZE } from "./feeds.mjs";
import {
  canonicalOf as canonicalTag,
  decode,
  jsonLdGraph,
  oneMeta,
  robotsOf as robotsTag,
} from "./html-head.mjs";
import { privateNames } from "./paths.mjs";
import { AUTHOR, INDEX, NOINDEX } from "./seo.mjs";
import { startServer } from "./serve.mjs";
import {
  HOME_LATEST,
  PER_PAGE as ARCHIVE_PER_PAGE,
  WEEKLY_PER_PAGE,
  WITHDRAWN_ROBOTS,
  compareId,
  withdrawnPages,
} from "./site-model.mjs";
import { ORIGIN as PUBLIC_ORIGIN } from "./site-settings.mjs";
import { weeklyView } from "./weekly.mjs";

// The public proxy blocks these: Ghost, its content and membership routes,
// release files and dotfiles (deploy/ghost/Caddyfile @private).
const PRIVATE_ROUTES = [
  "/ghost/",
  "/ghost/api/admin/site/",
  "/content/",
  "/content/images/",
  "/members/",
  "/members/api/member/",
  "/event-snapshot.json",
  "/publication.json",
  "/routes.json",
  "/audit-routes.json",
  "/.env",
];
// Launch-day routes the new site no longer serves, and later weekly pages,
// which stay in the snapshot's archive. Old article paths come from
// --expect-404 lists; a withdrawn story's path redirects instead.
const RETIRED_ROUTES = [
  "/weekly/page/2/",
  "/weekly-spotlights/",
  "/weekly-spotlights/page/2/",
  "/image-credits/",
  "/author/wtf-adsb/",
  "/tag/unusual-aircraft/",
  "/tag/flight-patterns/",
  "/tag/performance/",
  "/tag/transponder-reports/",
  "/tag/data-oddities/",
  "/tag/data-quality/",
];
const RASTER = {
  png: [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
  jpeg: [0xff, 0xd8, 0xff],
};

const { values: options } = parseArgs({
  options: {
    base: { type: "string" },
    serve: { type: "string" },
    canonical: { type: "string" },
    edge: { type: "string" },
    snapshot: { type: "string" },
    media: { type: "string" },
    config: { type: "string" },
    "expect-404": { type: "string", multiple: true, default: [] },
    "expect-redirect": { type: "string", multiple: true, default: [] },
    output: { type: "string" },
    widths: { type: "string", default: "1440,390" },
  },
});
if (!options.snapshot) throw new Error("--snapshot is required");
if (Boolean(options.base) === Boolean(options.serve))
  throw new Error("Give exactly one of --base and --serve");
if (options.edge && (options.serve || !net.isIP(options.edge)))
  throw new Error("--edge needs --base and an IP address");
// The edge address applies to the browser only, so it needs a base the
// browser visits itself: the canonical origin.
if (
  options.edge &&
  options.canonical &&
  new URL(options.canonical).origin !== new URL(options.base).origin
)
  throw new Error("--edge needs --base at the canonical origin");
// A live release always has media; without the manifest the replay check would be skipped.
if (options.base && !options.media)
  throw new Error("--base needs --media for the replay check");

const readJson = async (file) => JSON.parse(await fs.readFile(file, "utf8"));
const snapshot = await readJson(options.snapshot);
assert.equal(
  snapshot.mode,
  "published",
  "Live checks need a published snapshot",
);
const media = options.media ? await readJson(options.media) : { events: {} };
const config = options.config ? await readJson(options.config) : {};
const privateText = privateNames();
const terms = (config.private_terms || [])
  .filter(Boolean)
  .map((term) => term.toLowerCase());
const leaks = (body) =>
  privateText.test(body) ||
  terms.some((term) => body.toLowerCase().includes(term));
const assertPublic = (row) =>
  assert.equal(leaks(row.body), false, "Private text in " + row.path);

const events = snapshot.events;
const articles = events.map((event) => event.article_path);
const current = new Set(articles);
const byPath = new Map(events.map((event) => [event.article_path, event]));
const listed404 = (
  await Promise.all(
    options["expect-404"].map((file) => fs.readFile(file, "utf8")),
  )
)
  .flatMap((text) => text.split("\n"))
  .map((line) => line.trim())
  .filter(Boolean);
const malformed = listed404.filter((line) => !line.startsWith("/"));
assert.deepEqual(malformed, [], "--expect-404 lines must be site paths");
// Withdrawn stories' pages: those the snapshot lists, with the hub and link
// text the build gives them, and those named by --expect-redirect PATH=HUB.
// A path in both must agree on its hub.
const sitePathPattern = /^\/[^\s?#]*$/;
const withdrawn = new Map(
  withdrawnPages(snapshot.retired ?? [], events).pages.map((page) => [
    page.path,
    page,
  ]),
);
for (const argument of options["expect-redirect"]) {
  const [from, hub, ...rest] = argument.split("=");
  assert.ok(
    !rest.length && sitePathPattern.test(from) && sitePathPattern.test(hub),
    `--expect-redirect needs PATH=HUB with site paths: ${argument}`,
  );
  assert.ok(!current.has(from), `--expect-redirect ${from} is a live article`);
  assert.equal(
    (withdrawn.get(from) ?? { hub }).hub,
    hub,
    `--expect-redirect ${from} disagrees with the snapshot's hub`,
  );
  if (!withdrawn.has(from)) withdrawn.set(from, { path: from, hub });
}
// An old path reused by a new article, or an unchanged image the release
// still serves, is checked as part of the release instead.
const currentMedia = new Set(
  Object.values(media.events).flatMap((item) => [item.src, item.replaySrc]),
);
const expected404 = [...new Set(listed404)].filter(
  (line) =>
    !current.has(line) && !currentMedia.has(line) && !withdrawn.has(line),
);
const retired = [...RETIRED_ROUTES, ...expected404].filter(
  (route, index, list) => list.indexOf(route) === index,
);
const picks = events
  .filter((event) => event.weekly_spotlight)
  .sort((a, b) =>
    b.weekly_spotlight.week_start.localeCompare(a.weekly_spotlight.week_start),
  );
const homePick = weeklyView(snapshot).pick;
const widths = options.widths.split(",").map(Number);
// The archive journal on /stories/ searches its default window (today and the
// 29 days before) first; a search for a recent article's identifier must find
// it: its registration, else its callsign or address, else its headline. The
// result names the field searched; with no story in the window the search
// check is skipped and the result says so. The home page's dialog searches
// every headline.
const windowed = events.filter(
  (event) => event.observed_start.slice(0, 10) >= snapshot.window.start,
);
const [searched, searchedBy, searchText] =
  ["registration", "callsign", "icao_address"]
    .map((field) => {
      const event = windowed.find((item) => item.aircraft[field]);
      return event && [event, field, event.aircraft[field]];
    })
    .find(Boolean) ||
  (windowed.length
    ? [windowed[0], "headline", windowed[0].title]
    : [null, null, null]);
const homeSearched = events[0];
// The date structured data gives a story: its release, else its observation.
const released = (event) => event.released_at || event.observed_start;
// Its modification date: the last prose or evidence change, else that date.
const modified = (event) =>
  (event.released_at && event.content_revised_at) || released(event);
// Weekly research and exceptional events always pass the indexing gate.
const mustIndex = (event) =>
  Boolean(event.weekly_spotlight || event.priority_evidence);

let server;
let base = options.base?.replace(/\/$/, "");
if (options.serve) {
  server = await startServer(0, path.resolve(options.serve));
  base = `http://127.0.0.1:${server.address().port}`;
}
const local = Boolean(options.serve);
const canonical = (options.canonical || (local ? PUBLIC_ORIGIN : base)).replace(
  /\/$/,
  "",
);
const host = new URL(base).hostname;
// Pages are visited at the canonical origin. When another server answers for
// it (--serve, or a proxy under test), every request for that origin goes to
// the server instead, as the edge does live. Other origins are refused (an
// airframe image gets a local stand-in), the browser cannot resolve the
// public host, and a redirect is refused rather than followed, so nothing
// reaches the public site or Planespotters.
const mapped = new URL(base).origin !== new URL(canonical).origin;
const site = mapped ? new URL(canonical).origin : base;
const edge =
  options.edge && net.isIP(options.edge) === 6
    ? `[${options.edge}]`
    : options.edge;
const publicHost = new URL(canonical).hostname;
const browser = await chromium.launch({
  executablePath,
  args: [
    "--no-sandbox",
    ...(edge
      ? [`--host-resolver-rules=MAP ${host} ${edge}, MAP www.${host} ${edge}`]
      : mapped
        ? [
            `--host-resolver-rules=MAP ${publicHost} ~NOTFOUND, MAP *.${publicHost} ~NOTFOUND`,
          ]
        : []),
  ],
});
const redirects = [];
// The only resource a page may load from another origin is an airframe
// photo's image, named by an airframe figure in an article (checkArticles
// collects them). Against a server standing in for the site a stand-in
// answers for it; any other third-party request is refused and recorded.
const airframeImages = new Set();
const thirdParty = [];
// The Referer each airframe photo request carried. The image sets no policy
// of its own, so the page's Referrer-Policy header decides: the public proxy
// sends the site's origin and nothing more, the preview server nothing.
const photoReferers = [];
let airframeStory = null;
// Every open context, so teardown can drop its routes before closing it: a route
// callback still running when its context closes is an uncaught exception that
// would replace the checker's own failure message.
const contexts = new Set();
async function newContext(contextOptions = {}) {
  const context = await browser.newContext(contextOptions);
  contexts.add(context);
  context.on("close", () => contexts.delete(context));
  await guardRequests(context, {
    // The www host is the site too: the redirect check navigates there.
    isSite: (url) =>
      url.origin === site || url.origin === `https://www.${host}`,
    images: airframeImages,
    failures: thirdParty,
    live: !mapped,
    referers: photoReferers,
  });
  if (mapped) {
    await context.route(`${site}/**`, async (route) => {
      const url = new URL(route.request().url());
      const response = await context.request.fetch(
        base + url.pathname + url.search,
        { method: route.request().method(), maxRedirects: 0 },
      );
      if (response.status() >= 300 && response.status() < 400) {
        redirects.push(
          `${url.pathname}${url.search} to ${response.headers().location}`,
        );
        await route.abort();
      } else await route.fulfill({ response });
    });
  }
  return context;
}

// Routes first, ignoring any callback still in flight, then the context. Teardown
// never throws over the error that brought it about.
async function closeContext(context) {
  try {
    await context.unrouteAll({ behavior: "ignoreErrors" });
  } catch {
    // The context may already be gone.
  }
  await context.close();
}

// Same-origin requests from a page, six at a time: status, headers, and the
// body as text, or its first bytes for images.
async function fetchAll(page, paths, { bytes = false } = {}) {
  const results = [];
  for (let index = 0; index < paths.length; index += 6) {
    results.push(
      ...(await page.evaluate(
        async ({ batch, bytes }) =>
          Promise.all(
            batch.map(async (route) => {
              const response = await fetch(route, { cache: "no-store" });
              return {
                path: route,
                status: response.status,
                headers: Object.fromEntries(response.headers),
                body: bytes
                  ? await response
                      .arrayBuffer()
                      .then((data) => [
                        ...new Uint8Array(
                          data,
                          0,
                          Math.min(12, data.byteLength),
                        ),
                      ])
                  : await response.text(),
              };
            }),
          ),
        { batch: paths.slice(index, index + 6), bytes },
      )),
    );
  }
  return results;
}

const robotsOf = (html, route) => robotsTag(html, `robots on ${route}`);
const canonicalOf = (html, route) =>
  canonicalTag(html, `canonical on ${route}`);
// A same-site URL in a head tag, as a path on the checked server.
function sitePath(url, label) {
  const parsed = new URL(url);
  assert.equal(parsed.origin, new URL(canonical).origin, label);
  return parsed.pathname + parsed.search;
}
const directives = (value) =>
  new Set(
    String(value || "")
      .split(",")
      .map((part) => part.trim().toLowerCase())
      .filter(Boolean),
  );
function assertCache(row, kind) {
  const set = directives(row.headers["cache-control"]);
  const label = `Cache-Control on ${row.path}: ${row.headers["cache-control"]}`;
  if (kind === "page" || kind === "missing") {
    assert.ok(set.has("no-cache") && !set.has("immutable"), label);
    return;
  }
  assert.ok(
    set.has("public") && set.has("immutable") && set.has("max-age=31536000"),
    label,
  );
  // Story images keep a browser lifetime but the CDN revalidates them, so a
  // withdrawal takes effect at once; versioned theme assets need neither.
  assert.equal(set.has("s-maxage=0"), kind === "hashed", label);
}
const isRaster = (start) =>
  Object.values(RASTER).some((magic) =>
    magic.every((byte, index) => start[index] === byte),
  ) ||
  (String.fromCharCode(...start.slice(0, 4)) === "RIFF" &&
    String.fromCharCode(...start.slice(8, 12)) === "WEBP");

// The sitemap index and every child: lastmod everywhere, and no child's
// lastmod is older than its newest entry.
async function sitemapEntries(page) {
  const [root] = await fetchAll(page, ["/sitemap.xml"]);
  assert.equal(root.status, 200, "/sitemap.xml");
  assertPublic(root);
  assert.match(root.body, /<sitemapindex /, "/sitemap.xml is an index");
  const children = [
    ...root.body.matchAll(
      /<sitemap><loc>([^<]+)<\/loc><lastmod>([^<]+)<\/lastmod><\/sitemap>/g,
    ),
  ].map(([, loc, lastmod]) => ({
    path: sitePath(decode(loc), "Sitemap location"),
    lastmod,
  }));
  assert.equal(
    children.length,
    (root.body.match(/<sitemap>/g) || []).length,
    "Every child sitemap has a lastmod",
  );
  assert.ok(children.length > 0, "The sitemap index lists its children");
  const entries = new Map();
  for (const row of await fetchAll(
    page,
    children.map((child) => child.path),
  )) {
    assert.equal(row.status, 200, row.path);
    assertPublic(row);
    const urls = [
      ...row.body.matchAll(
        /<url><loc>([^<]+)<\/loc><lastmod>([^<]+)<\/lastmod><\/url>/g,
      ),
    ];
    assert.equal(
      urls.length,
      (row.body.match(/<url>/g) || []).length,
      `Every entry in ${row.path} has a lastmod`,
    );
    for (const [, loc, lastmod] of urls) {
      assert.ok(!Number.isNaN(Date.parse(lastmod)), `${loc} lastmod`);
      const route = sitePath(decode(loc), `Sitemap entry ${loc}`);
      assert.ok(!entries.has(route), `${route} listed twice`);
      entries.set(route, { lastmod, loc: decode(loc) });
    }
    const { lastmod } = children.find((child) => child.path === row.path);
    for (const [, loc, entry] of urls)
      assert.ok(
        Date.parse(lastmod) >= Date.parse(entry),
        `${row.path} lastmod ${lastmod} is older than ${loc}`,
      );
  }
  return entries;
}

// The weekly archive's first page, then its second when there is one. Its
// cards use h2 and list the picks newest week first.
async function checkWeeklyArchive(page, selector) {
  const shown = await page
    .locator(`${selector} h2 a`)
    .evaluateAll((links) => links.map((link) => new URL(link.href).pathname));
  assert.deepEqual(
    shown,
    picks.slice(0, WEEKLY_PER_PAGE).map((event) => event.article_path),
    "Weekly archive page 1",
  );
  if (picks.length <= WEEKLY_PER_PAGE) return;
  await Promise.all([
    // Pagination follows the snapshot's immutable archive pages.
    page.waitForURL(/\/event-pages\/.+\/page\/2\/$/),
    page.locator("nav.pagination a").last().click(),
  ]);
  await page.waitForLoadState("load");
  assert.equal(
    await page.locator('meta[name="robots"]').getAttribute("content"),
    NOINDEX,
    "Weekly archive page 2",
  );
  assert.equal(
    await page.locator(selector).count(),
    Math.min(WEEKLY_PER_PAGE, picks.length - WEEKLY_PER_PAGE),
    "Weekly archive page 2",
  );
}

// The static story archive: page 1 lists the newest entries, and numbered
// page links reach the rest.
async function checkStoryArchive(page, { follow }) {
  const entries = page.locator(
    "[data-event-feed] article.event-card.archive-entry[data-event-id]",
  );
  assert.deepEqual(
    await entries.evaluateAll((items) =>
      items.map((item) => item.dataset.eventId),
    ),
    events.slice(0, ARCHIVE_PER_PAGE).map((event) => event.id),
    "Story archive page 1",
  );
  const pagination = page.locator(
    '[data-event-feed] nav.pagination.page-numbers a[href="/stories/page/2/"]',
  );
  assert.equal(
    (await pagination.count()) > 0,
    events.length > ARCHIVE_PER_PAGE,
    "Story archive page links",
  );
  if (!follow || events.length <= ARCHIVE_PER_PAGE) return;
  await Promise.all([
    page.waitForURL(/\/stories\/page\/2\/$/),
    pagination.first().click(),
  ]);
  assert.deepEqual(
    await page
      .locator("article.event-card.archive-entry[data-event-id]")
      .evaluateAll((items) => items.map((item) => item.dataset.eventId)),
    events
      .slice(ARCHIVE_PER_PAGE, 2 * ARCHIVE_PER_PAGE)
      .map((event) => event.id),
    "Story archive page 2",
  );
}

// Each withdrawn story's page answers with a noindex page whose canonical,
// refresh and one link name its hub, which answers. It runs no script and
// loads nothing, from this site or another. Returns the number checked.
async function checkWithdrawn(scan) {
  const pages = [...withdrawn.values()];
  for (const row of await fetchAll(
    scan,
    pages.map((page) => page.path),
  )) {
    const { hub, label } = withdrawn.get(row.path);
    assert.equal(row.status, 200, `Withdrawn story ${row.path}`);
    assertPublic(row);
    if (!local) assertCache(row, "page");
    assert.equal(robotsOf(row.body, row.path), WITHDRAWN_ROBOTS, row.path);
    assert.equal(canonicalOf(row.body, row.path), canonical + hub, row.path);
    assert.equal(
      oneMeta(row.body, "http-equiv", "refresh", `refresh on ${row.path}`),
      `0; url=${hub}`,
      `Redirect from ${row.path}`,
    );
    const links = [...row.body.matchAll(/<a href="([^"]*)">([^<]*)<\/a>/g)];
    assert.deepEqual(
      links.map(([, href]) => decode(href)),
      [hub],
      `Link on ${row.path}`,
    );
    if (label) assert.equal(decode(links[0][2]), label, `Link on ${row.path}`);
    assert.doesNotMatch(
      row.body,
      /<(?:script|img|iframe|object|embed|video|audio|source|style)\b|<link (?![^>]*rel="canonical")/i,
      `Withdrawn page ${row.path} loads nothing`,
    );
    assert.deepEqual(
      thirdPartyResources(row.body, canonical),
      [],
      `Third-party resources on ${row.path}`,
    );
  }
  for (const row of await fetchAll(scan, [
    ...new Set(pages.map((page) => page.hub)),
  ]))
    assert.equal(row.status, 200, `Hub ${row.path}`);
  return pages.length;
}

// A browser follows each withdrawn page's refresh to its hub, without
// JavaScript.
async function followWithdrawn() {
  if (!withdrawn.size) return;
  const context = await newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  for (const { path: route, hub } of withdrawn.values()) {
    await page.goto(site + route, { waitUntil: "commit" });
    await page.waitForURL(site + hub, { timeout: 15000 });
  }
  await closeContext(context);
}

// Every article: status, private text, robots, one JSON-LD graph, and a social
// image. Returns the indexed paths, the image paths, and the first article with
// a recorded track and its replay, which the layout checks play.
async function checkArticles(scan) {
  const replaySources = new Set(
    events
      .filter((event) => media.events[event.id]?.replaySrc)
      .map((event) => event.article_path),
  );
  let replay;
  const indexed = new Set();
  const images = new Set();
  for (const row of await fetchAll(scan, articles)) {
    if (
      !replay &&
      replaySources.has(row.path) &&
      row.body.includes("data-geographic-replay=")
    )
      replay = row.path;
    assert.equal(row.status, 200, row.path);
    assertPublic(row);
    const event = byPath.get(row.path);
    if (event.weekly_spotlight)
      assert.match(row.body, /class="weekly-research"/, row.path);
    if (!local) assertCache(row, "page");
    assert.equal(canonicalOf(row.body, row.path), canonical + row.path);
    const robots = robotsOf(row.body, row.path);
    assert.ok([INDEX, NOINDEX].includes(robots), row.path);
    if (robots === INDEX) indexed.add(row.path);
    else assert.ok(!mustIndex(event), `${row.path} must be indexed`);
    const graph = jsonLdGraph(row.body, `One JSON-LD graph on ${row.path}`);
    const nodes = (type) => graph.filter((node) => node["@type"] === type);
    const [posting, ...extra] = nodes("BlogPosting");
    assert.ok(posting && !extra.length, `One BlogPosting on ${row.path}`);
    assert.equal(posting.url, canonical + row.path);
    assert.equal(posting.mainEntityOfPage["@id"], canonical + row.path);
    assert.equal(posting.headline, event.title, `headline on ${row.path}`);
    assert.equal(
      posting.datePublished,
      released(event),
      `datePublished on ${row.path}`,
    );
    assert.equal(
      posting.dateModified,
      modified(event),
      `dateModified on ${row.path}`,
    );
    assert.equal(
      posting.contentReferenceTime,
      event.observed_start,
      `contentReferenceTime on ${row.path}`,
    );
    assert.equal(nodes("BreadcrumbList").length, 1, row.path);
    // Only an airframe photo's image, in its figure, loads from elsewhere,
    // with its photographer's credit and plain links to its page beside it.
    assert.deepEqual(
      thirdPartyResources(row.body, canonical, { story: true }),
      [],
      `Third-party resources on ${row.path}`,
    );
    for (const [figure] of row.body.matchAll(
      /<figure class="airframe-photo[^"]*"[\s\S]*?<\/figure>/g,
    )) {
      const links = [...figure.matchAll(/<a ([^>]*)>/g)].map(([, tag]) => tag);
      assert.ok(links.length > 0, `Airframe photo links on ${row.path}`);
      for (const tag of links) {
        assert.match(tag, /^href="[^"]+"$/, `Plain link on ${row.path}`);
        assert.equal(
          new URL(decode(tag.slice(6, -1))).host,
          AIRFRAME_LINK_HOST,
          row.path,
        );
      }
      assert.match(
        figure,
        /<figcaption>[^<]*Photo: [^<]+ \/ <a href="[^"]+">Planespotters\.net<\/a><\/figcaption>/,
        `Airframe photo credit on ${row.path}`,
      );
      airframeImages.add(decode(figure.match(/<img src="([^"]+)"/)[1]));
      airframeStory ??= row.path;
    }
    images.add(
      sitePath(
        oneMeta(row.body, "property", "og:image", `og:image on ${row.path}`),
        `og:image on ${row.path}`,
      ),
    );
  }
  if (replaySources.size)
    assert.ok(replay, "An article with a recorded track must show its replay");
  return { replay, indexed, images };
}

// Social images answer and are raster; story images are hashed.
async function checkSocialImages(scan, images) {
  for (const row of await fetchAll(scan, [...images], { bytes: true })) {
    assert.equal(row.status, 200, row.path);
    assert.ok(isRaster(row.body), `${row.path} is not a raster image`);
    if (!local) {
      assert.match(
        row.headers["content-type"] || "",
        /^image\/(?:png|jpeg|webp)\b/,
        row.path,
      );
      if (/^\/social\/[a-f0-9]{64}\.(?:png|jpg)$/.test(row.path))
        assertCache(row, "hashed");
    }
  }
}

// /favicon.ico and /apple-touch-icon.png, which browsers and crawlers request
// at the root whatever a page links: an ICO and a PNG, typed as images through
// the proxy and revalidated like pages.
const ICONS = {
  "/favicon.ico": [0x00, 0x00, 0x01, 0x00],
  "/apple-touch-icon.png": RASTER.png,
};
async function checkIcons(scan) {
  // Playwright never routes a URL that ends in /favicon.ico, and with any
  // route installed (the third-party guard always is) a page fetch of one
  // fails, so each icon is asked with an empty query, which the proxy ignores.
  const query = "?";
  for (const row of await fetchAll(
    scan,
    Object.keys(ICONS).map((route) => route + query),
    { bytes: true },
  )) {
    row.path = row.path.slice(0, row.path.length - query.length);
    assert.equal(row.status, 200, row.path);
    assert.ok(
      ICONS[row.path].every((byte, index) => row.body[index] === byte),
      `${row.path} is not the expected image`,
    );
    if (!local) {
      assert.match(row.headers["content-type"] || "", /^image\//, row.path);
      assertCache(row, "page");
    }
  }
  return Object.keys(ICONS).length;
}

// Site files, the feed, and the routes that must be gone. Returns the number
// of feed items.
async function checkFiles(scan) {
  const accessible = [
    "/robots.txt",
    "/sitemap.xml",
    "/search-index/",
    "/about/image-credits/",
    AUTHOR.url,
  ];
  const feedPath = "/rss/";
  const missing = [...(local ? [] : PRIVATE_ROUTES), "/missing/", ...retired];
  const fetched = await fetchAll(scan, [...accessible, feedPath, ...missing]);
  // A CDN rule in front of the site, such as a Cloudflare custom rule, may answer
  // dotfile probes such as /.env with a 403 before they reach the origin.
  const edgeBlocked = (route) => !local && /^\/\./.test(route);
  for (const row of fetched) {
    if (missing.includes(row.path) && edgeBlocked(row.path))
      assert.ok([403, 404].includes(row.status), `${row.path}: ${row.status}`);
    else
      assert.equal(
        row.status,
        missing.includes(row.path) ? 404 : 200,
        row.path,
      );
    assertPublic(row);
    // Only pages name Planespotters, and of these only the image credits.
    if (row.path !== "/about/image-credits/")
      assert.doesNotMatch(row.body, PHOTO_DATA, row.path);
  }
  const file = (route) => fetched.find((row) => row.path === route);
  // robots.txt keeps crawlers out of the journal data, and only out of it,
  // and names the sitemap index.
  const robotsTxt = file("/robots.txt")
    .body.split(/\r?\n/)
    .map((line) => line.trim());
  assert.deepEqual(
    robotsTxt
      .filter((line) => /^disallow:/i.test(line))
      .map((line) => line.replace(/^disallow:\s*/i, ""))
      .sort(),
    [...DISALLOWED].sort(),
    "robots.txt Disallow lines",
  );
  assert.ok(
    robotsTxt.includes(`Sitemap: ${canonical}/sitemap.xml`),
    "robots.txt names the sitemap index",
  );
  // The theme search index lists every story, and no withdrawn one.
  const index = JSON.parse(file("/search-index/").body);
  assert.deepEqual(
    index.map((entry) => entry.url).sort(),
    [...articles].sort(),
    "Search index",
  );
  assert.deepEqual(
    index.filter((entry) => withdrawn.has(entry.url)),
    [],
    "Withdrawn stories in the search index",
  );
  // The feed: an RSS document, the newest stories, GUID = event ID.
  const feed = file(feedPath);
  assert.match(
    feed.headers["content-type"] || "",
    /^application\/rss\+xml/,
    feedPath,
  );
  assert.match(
    feed.body,
    /^<\?xml [^>]*\?>\s*<rss version="2\.0"/,
    "The feed is an RSS document",
  );
  const items = [...feed.body.matchAll(/<item>([\s\S]*?)<\/item>/g)].map(
    ([, item], index) => {
      const link = item.match(/<link>([^<]+)<\/link>/);
      const guid = item.match(/<guid isPermaLink="false">([^<]+)<\/guid>/);
      assert.ok(link && guid, `Feed item ${index + 1} has a link and a GUID`);
      return { link: decode(link[1]), guid: guid[1] };
    },
  );
  for (const item of items) {
    const route = sitePath(item.link, `Feed link ${item.link}`);
    assert.ok(!withdrawn.has(route), `Withdrawn story in the feed: ${route}`);
    const event = byPath.get(route);
    assert.ok(event, `Feed link ${item.link}`);
    assert.equal(item.guid, event.id, `Feed GUID for ${item.link}`);
  }
  assert.deepEqual(
    items.map((item) => item.guid),
    [...events]
      .sort(
        (a, b) =>
          Date.parse(released(b)) - Date.parse(released(a)) ||
          compareId(a.id, b.id),
      )
      .slice(0, FEED_SIZE)
      .map((event) => event.id),
    "Feed items, newest release first",
  );
  return items.length;
}

// The sitemaps list exactly the indexable pages, retired paths never, and
// every other listed page answers, indexable, at its own canonical URL.
// Returns the listed paths.
async function checkSitemaps(scan, indexed) {
  const listed = await sitemapEntries(scan);
  assert.deepEqual(
    articles.filter((route) => listed.has(route)).sort(),
    [...indexed].sort(),
    "Sitemap articles must be exactly the indexed ones",
  );
  const stale = [...listed.keys()].filter(
    (route) =>
      retired.includes(route) ||
      withdrawn.has(route) ||
      DISALLOWED.some((prefix) => route.startsWith(prefix)),
  );
  assert.deepEqual(
    stale,
    [],
    "Retired, withdrawn, or private paths in the sitemap",
  );
  for (const row of await fetchAll(
    scan,
    [...listed.keys()].filter((route) => !current.has(route)),
  )) {
    assert.equal(row.status, 200, "Sitemap path " + row.path);
    assertPublic(row);
    assert.equal(robotsOf(row.body, row.path), INDEX, row.path);
    assert.equal(
      canonicalOf(row.body, row.path),
      listed.get(row.path).loc,
      row.path,
    );
  }
  return listed;
}

// The journal's data generation (its index, archive list, and every card) and
// each replay carry no private text. The pre-deploy guard scans them too; this
// is the public copy. Returns the number of files scanned.
async function checkJournalData(scan) {
  const [archive] = await fetchAll(scan, ["/stories/"]);
  assert.equal(archive.status, 200, "/stories/");
  const config = archive.body.match(
    /<script type="application\/json" data-journal-config>([\s\S]*?)<\/script>/,
  );
  assert.ok(config, "The story archive names its journal data");
  const base = JSON.parse(config[1]).base;
  assert.match(base, /^\/event-data\/[^?#]+\/$/, "Journal data path");
  const replays = Object.values(media.events)
    .map((item) => item.replaySrc)
    .filter(Boolean);
  const paths = [
    `${base}index.json`,
    `${base}archive.json`,
    ...events.map((event) => `${base}cards/${event.id}.json`),
    ...new Set(replays),
  ];
  for (const row of await fetchAll(scan, paths)) {
    assert.equal(row.status, 200, row.path);
    assertPublic(row);
    assert.doesNotMatch(row.body, PHOTO_DATA, row.path);
  }
  return paths.length;
}

// Hashed story media keep a year in browsers and revalidate at the CDN;
// versioned theme assets are immutable; pages and missing files revalidate.
// Then the www host redirects to the apex.
async function checkCaching(scan) {
  const home = (await fetchAll(scan, ["/"]))[0].body;
  const versioned = [...home.matchAll(/(\/assets\/[^"?]+\?v=[a-f0-9]+)"/g)].map(
    (match) => match[1],
  );
  assert.ok(versioned.length, "Versioned theme assets on the home page");
  const hashed = Object.values(media.events)
    .flatMap((item) => [item.src, item.replaySrc])
    .filter((src) =>
      /^\/event-media\/[a-f0-9]{64}\.(?:svg|json)$/.test(src || ""),
    )
    .slice(0, 2);
  for (const row of await fetchAll(scan, [...new Set(versioned), ...hashed])) {
    assert.equal(row.status, 200, row.path);
    assertCache(row, row.path.startsWith("/assets/") ? "versioned" : "hashed");
  }
  for (const row of await fetchAll(scan, [
    `/social/${"0".repeat(64)}.png`,
    `/event-media/${"0".repeat(64)}.svg`,
  ])) {
    assert.equal(row.status, 404, row.path);
    assertCache(row, "missing");
  }
  result.cacheControl = true;
  if (!mapped) {
    await scan.goto(`https://www.${host}/weekly/`);
    assert.equal(scan.url(), canonical + "/weekly/");
    result.wwwRedirect = true;
  }
}

// The home page: its weekly card, latest stories, and header search dialog.
async function checkHome(page) {
  assert.equal(await page.locator(".weekly-card").count(), homePick ? 1 : 0);
  assert.deepEqual(
    await page
      .locator(".home-latest article.story-tile[data-event-id]")
      .evaluateAll((items) => items.map((item) => item.dataset.eventId)),
    events.slice(0, HOME_LATEST).map((event) => event.id),
    "Latest stories on the home page",
  );
  // The header search opens the theme's dialog over every headline.
  if (!homeSearched) return;
  await page
    .getByRole("button", { name: "Search stories", exact: true })
    .click();
  const field = page.locator("#story-search");
  await field.fill("zzzz-no-result");
  await page
    .locator(".search-count", { hasText: "No stories match" })
    .waitFor();
  await field.fill(homeSearched.title);
  await page.waitForFunction(
    (target) =>
      [...document.querySelectorAll(".search-results a")].some(
        (link) => new URL(link.href).pathname === target,
      ),
    homeSearched.article_path,
  );
  await page.keyboard.press("Escape");
}

// The story archive's static pages and its journal search.
async function checkArchiveSearch(page) {
  await checkStoryArchive(page, { follow: false });
  await page.locator('[data-journal][data-search-ready="true"]').waitFor();
  // The search panel closes once results show, so each search reopens it.
  const search = async (text) => {
    const field = page.locator("[data-keyword-filter]");
    if (!(await field.isVisible()))
      await page.locator("[data-open-filters]").click();
    await field.fill(text);
    await page
      .getByRole("button", { name: "Show results", exact: true })
      .click();
  };
  const results = '[data-event-feed] .event-feed[data-view="search"]';
  await search("zzzz-no-result");
  await page.locator(`${results} .event-empty`).waitFor();
  if (!searched) return;
  await search(searchText);
  await page
    .locator(`${results} article.event-card[data-event-id="${searched.id}"]`)
    .first()
    .waitFor();
}

// The replay plays and shows a reading.
async function checkReplay(page) {
  await page.locator("[data-geographic-controls]:not([hidden])").waitFor();
  await page.locator("[data-geographic-play]").click();
  await page
    .locator("[data-geographic-position]")
    .waitFor({ state: "visible" });
  assert.notEqual(
    await page.locator("[data-geographic-time]").textContent(),
    "—",
  );
}

// Key pages at each width: status, headers, canonical, robots, no horizontal
// overflow and no browser errors, with the page-specific checks above.
async function checkLayouts(replay) {
  const routes = [
    "/",
    "/stories/",
    "/weekly/",
    ...(picks.length ? [picks[0].article_path] : []),
    ...(replay ? [replay] : []),
    ...(airframeStory ? [airframeStory] : []),
    "/about/",
  ];
  for (const width of widths) {
    const context = await newContext({
      viewport: { width, height: 1000 },
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    for (const route of [...new Set(routes)]) {
      const response = await page.goto(site + route, {
        waitUntil: "networkidle",
      });
      assert.equal(response.status(), 200, route);
      if (!local) {
        const headers = response.headers();
        assert.equal(headers["x-robots-tag"], undefined, route);
        assert.match(
          headers["content-security-policy"] || "",
          /script-src 'self'/,
          route,
        );
        // Images may come only from the site and Planespotters' image host.
        assert.deepEqual(
          (headers["content-security-policy"] || "")
            .split(";")
            .map((part) => part.trim().split(/\s+/))
            .find(([name]) => name === "img-src")
            ?.slice(1),
          IMAGE_POLICY,
          `img-src on ${route}`,
        );
        assertCache({ path: route, headers }, "page");
      }
      assert.equal(await page.locator(".preview-notice").count(), 0, route);
      assert.equal(
        await page.locator('link[rel="canonical"]').getAttribute("href"),
        canonical + route,
      );
      // Stories below the indexing gate are noindex; the article checks
      // match each story's directive against the sitemap.
      const robots = await page
        .locator('meta[name="robots"]')
        .getAttribute("content");
      if (current.has(route))
        assert.ok([INDEX, NOINDEX].includes(robots), `robots on ${route}`);
      else assert.equal(robots, INDEX, `robots on ${route}`);
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth + 1,
        ),
        false,
        `Horizontal overflow on ${route} at ${width}px`,
      );
      if (route === "/") await checkHome(page);
      if (route === "/stories/") await checkArchiveSearch(page);
      if (route === "/weekly/")
        await checkWeeklyArchive(page, ".weekly-archive .weekly-card");
      if (picks.length && route === picks[0].article_path)
        assert.equal(await page.locator(".weekly-research").count(), 1);
      if (route === replay) await checkReplay(page);
      checks.push({ width, route, status: 200 });
    }
    assert.deepEqual(errors, [], `Browser errors at ${width}px`);
    await closeContext(context);
  }
}

// Without JavaScript the story and weekly archives still page.
async function checkWithoutJavaScript() {
  const context = await newContext({
    javaScriptEnabled: false,
    viewport: { width: 390, height: 900 },
  });
  const page = await context.newPage();
  await page.goto(site + "/stories/");
  await checkStoryArchive(page, { follow: true });
  await page.goto(site + "/weekly/");
  await checkWeeklyArchive(page, ".weekly-card");
  await closeContext(context);
}

const checks = [];
const result = {};
try {
  // Fetch checks run first: they are quick, and most failures show there.
  const probe = await newContext();
  const scan = await probe.newPage();
  await scan.goto(site + "/", { waitUntil: "domcontentloaded" });
  const withdrawnChecked = await checkWithdrawn(scan);
  const { replay, indexed, images } = await checkArticles(scan);
  await checkSocialImages(scan, images);
  const feedItems = await checkFiles(scan);
  const icons = await checkIcons(scan);
  const listed = await checkSitemaps(scan, indexed);
  const dataFiles = await checkJournalData(scan);
  Object.assign(result, {
    indexed: indexed.size,
    images: images.size,
    feedItems,
    icons,
    sitemapPaths: listed.size,
    dataFiles,
  });
  if (!local) await checkCaching(scan);
  await closeContext(probe);
  await checkLayouts(replay);
  await checkWithoutJavaScript();
  await followWithdrawn();
  assert.deepEqual(redirects, [], "Redirects from the server under test");
  assert.deepEqual(thirdParty, [], "Third-party requests");
  // A served copy answers with no-referrer; the public proxy or site with
  // strict-origin-when-cross-origin, so Planespotters sees the origin only.
  if (airframeStory)
    assert.ok(photoReferers.length, "An airframe photo was requested");
  assert.deepEqual(
    [...new Set(photoReferers)],
    photoReferers.length ? [local ? null : `${site}/`] : [],
    "Referer on airframe photo requests",
  );
  if (!searched)
    console.error(
      "Journal search check skipped: no story in the default 30-day window.",
    );
  Object.assign(result, {
    base,
    local,
    snapshotId: snapshot.snapshot_id,
    checks,
    articles: articles.length,
    weekly: picks.length,
    replay: replay || null,
    searched: searched?.article_path || null,
    searchedBy,
    ...(searched
      ? {}
      : { searchSkipped: "no story in the default 30-day window" }),
    homeSearched: homeSearched?.article_path || null,
    airframePhotos: airframeImages.size,
    airframePhotoReferer: photoReferers[0] ?? null,
    expected404: expected404.length,
    retired: retired.length,
    withdrawnRedirects: withdrawnChecked,
    privateRoutesBlocked: !local,
    noJavaScriptPagination: true,
    errors: 0,
  });
  if (options.output)
    await fs.writeFile(options.output, JSON.stringify(result, null, 2) + "\n", {
      mode: 0o600,
    });
  console.log(JSON.stringify({ ...result, checks: checks.length }));
} catch (error) {
  if (redirects.length)
    throw new Error(
      `Redirects from the server under test: ${redirects.join(", ")}`,
      { cause: error },
    );
  throw error;
} finally {
  for (const context of [...contexts]) {
    try {
      await context.unrouteAll({ behavior: "ignoreErrors" });
    } catch {
      // Teardown must not mask the error that ended the run.
    }
  }
  await browser.close();
  if (server) await new Promise((resolve) => server.close(resolve));
}
