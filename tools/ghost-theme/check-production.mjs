import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import {
  IMAGE_POLICY,
  PHOTO_DATA,
  guardRequests,
  thirdPartyResources,
} from "./airframe-photos.mjs";
import { startPreview } from "./browser.mjs";
import { decode, jsonLdGraph, oneMeta, robotsOf } from "./html-head.mjs";
import { privateNames, root, output, tools, topicsFile } from "./paths.mjs";
import { privateScratch } from "./scratch.mjs";
import { FAVICON_SIZES, TOUCH_SIZE, readIco } from "./site-icons.mjs";
import { indexCopy, topicCopy } from "./topics.mjs";

// Production builds from invented fixtures: the site map, head tags and
// structured data, sitemaps, the feed and robots.txt, indexing gates, hub thresholds,
// crawl depth, the public proxy's headers, and a desktop/mobile pass.
const directory = await fs.mkdtemp(path.join(output, "production-check-"));
const media = await fs.mkdtemp(path.join(output, "production-media-"));
const legacyDirectory = await fs.mkdtemp(path.join(output, "production-old-"));
const privateDir = await privateScratch("production-check-");
const network = `wtf-production-check-${process.pid}`;
const backend = `${network}-site`;
const frontend = `${network}-public`;
// Proxies that each lose one public rule; the live checker must fail them.
const broken = [`${network}-routes`, `${network}-cache`, `${network}-images`];
// A fixture public origin, set for every build and tool this check starts.
const ORIGIN = "https://site.example";
process.env.SITE_ORIGIN = ORIGIN;
// A fixture site identity, set the same way: the name, home heading, author,
// and support link that every page shows.
const NAME = "Fixture Findings";
const TAGLINE = "Fixture readings from invented aircraft";
const SUPPORT = {
  url: "https://support.example/fixture",
  label: "Back the fixture",
};
Object.assign(process.env, {
  SITE_NAME: NAME,
  SITE_TAGLINE: TAGLINE,
  SITE_AUTHOR: "Fixture Author",
  SITE_SUPPORT_URL: SUPPORT.url,
  SITE_SUPPORT_LABEL: SUPPORT.label,
});
const AUTHOR_PAGE = "/author/fixture-author/";
// The origin as a regular expression.
const SITE = ORIGIN.replaceAll(".", "\\.");
const run = (command, args) => {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    timeout: 180000,
    maxBuffer: 64 * 1024 * 1024,
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
};
const docker = (...args) => run("docker", args);
const build = (input, target, ...extra) => [
  path.join(tools, "build.mjs"),
  "--snapshot",
  input,
  "--output",
  target,
  ...extra,
];
let close;
try {
  // An older snapshot without story fields (its story under a /stories/ path)
  // must still build.
  const older = JSON.parse(
    run("python3", [
      "-B",
      "-c",
      `
import json,sys
sys.path.insert(0,'tests');sys.path.insert(0,'src')
from test_weekly_spotlight import fixture,export
from wtf_adsb_publisher import public_events as public,publication,weekly_spotlight
ledger,identifier,review,inventory=fixture()
inventory['posts'][0]['html']=publication.article_html(review['events'][0],display_review=False)
inventory['posts'][0]['tags']=[{'name':'Weekly spotlight'},{'name':'#weekly-'+weekly_spotlight.binding(review['events'][0])}]
print(json.dumps({'review':json.dumps(review),'published':json.dumps(export(ledger,inventory))}))
`,
    ]),
  );
  const input = path.join(privateDir, "snapshot.json");
  await fs.writeFile(input, older.review, { mode: 0o600 });
  const rejected = spawnSync(
    "node",
    build(input, legacyDirectory, "--production"),
    { cwd: root, encoding: "utf8" },
  );
  assert.notEqual(rejected.status, 0);
  assert.match(rejected.stderr, /Production requires a published snapshot/);
  // A production build without the public origin stops before it writes anything.
  const withoutOrigin = { ...process.env };
  delete withoutOrigin.SITE_ORIGIN;
  const unnamed = spawnSync(
    "node",
    build(input, legacyDirectory, "--production"),
    { cwd: root, encoding: "utf8", env: withoutOrigin },
  );
  assert.notEqual(unnamed.status, 0);
  assert.match(unnamed.stderr, /Production builds need SITE_ORIGIN/);
  // So does one without the site's name.
  const withoutName = { ...process.env };
  delete withoutName.SITE_NAME;
  const nameless = spawnSync(
    "node",
    build(input, legacyDirectory, "--production"),
    { cwd: root, encoding: "utf8", env: withoutName },
  );
  assert.notEqual(nameless.status, 0);
  assert.match(nameless.stderr, /Production builds need SITE_NAME/);
  run("node", build(input, legacyDirectory));
  const previous = JSON.parse(
    await fs.readFile(path.join(legacyDirectory, "audit-routes.json")),
  );
  const privatePage = previous.find((route) =>
    route.startsWith("/event-pages/"),
  );
  await fs.writeFile(input, older.published, { mode: 0o600 });
  run("node", build(input, legacyDirectory, "--production"));
  // The first public release excludes every prelaunch generation.
  await assert.rejects(
    fs.access(path.join(legacyDirectory, privatePage, "index.html")),
    { code: "ENOENT" },
  );
  await assert.rejects(
    fs.access(path.join(legacyDirectory, "examples/circling.json")),
    { code: "ENOENT" },
  );
  const legacyEvent = JSON.parse(older.published).events[0];
  await fs.access(
    path.join(legacyDirectory, legacyEvent.article_path, "index.html"),
  );
  const legacyRoutes = JSON.parse(
    await fs.readFile(path.join(legacyDirectory, "audit-routes.json")),
  );
  const immutable = legacyRoutes.find((route) =>
    route.startsWith("/event-pages/"),
  );
  const original = await fs.readFile(
    path.join(legacyDirectory, immutable, "index.html"),
    "utf8",
  );
  run("node", build(input, legacyDirectory, "--production"));
  assert.equal(
    await fs.readFile(
      path.join(legacyDirectory, immutable, "index.html"),
      "utf8",
    ),
    original,
  );

  // The story-engine fixture, with media, in production.
  const fixtureJson = run("python3", [
    "-B",
    path.join(tools, "site_fixtures.py"),
    "--mode",
    "published",
    "--media",
    media,
  ]);
  await fs.writeFile(input, fixtureJson, { mode: 0o600 });
  const fixture = JSON.parse(fixtureJson);
  // Airframe photos for a story with a map and one without, as the
  // publisher's Planespotters step hands them to the build.
  const airframeInput = path.join(privateDir, "airframe-photos.json");
  const airframeStories = [
    "/stories/airbus-a380-hl7635-27-sep-2026/",
    "/stories/airbus-a380-hl7640-20-sep-2026/",
  ];
  const airframePhotos = Object.fromEntries(
    fixture.events
      .filter((event) => airframeStories.includes(event.article_path))
      .map((event, index) => [
        event.id,
        {
          src: `https://t.plnspttrs.net/00000/${index + 1}_test_280.jpg`,
          width: 420,
          height: 280,
          link: `https://www.planespotters.net/photo/${index + 1}/${event.aircraft.registration.toLowerCase()}-test?utm_source=api`,
          photographer: "Test Photographer",
          registration: event.aircraft.registration,
        },
      ]),
  );
  await fs.writeFile(
    airframeInput,
    JSON.stringify({ photos: airframePhotos }),
    { mode: 0o600 },
  );
  // A production page never ships without its social image: a browser that
  // cannot start stops the build.
  const blind = spawnSync(
    "node",
    build(
      input,
      path.join(output, `production-blind-${process.pid}`),
      "--production",
      "--media",
      path.join(media, "manifest.json"),
    ),
    {
      cwd: root,
      encoding: "utf8",
      env: { ...process.env, CHROMIUM_PATH: "/nonexistent/chrome" },
    },
  );
  assert.notEqual(blind.status, 0);
  assert.match(blind.stderr, /Social images need the pinned browser/);
  await fs.rm(path.join(output, `production-blind-${process.pid}`), {
    recursive: true,
    force: true,
  });
  run(
    "node",
    build(
      input,
      directory,
      "--production",
      "--media",
      path.join(media, "manifest.json"),
      "--airframe-photos",
      airframeInput,
    ),
  );
  const file = (route) =>
    fs.readFile(
      path.join(directory, route, route.endsWith("/") ? "index.html" : ""),
      "utf8",
    );
  const exists = (route) =>
    fs
      .access(path.join(directory, route, "index.html"))
      .then(() => true)
      .catch(() => false);
  const one = oneMeta;
  const title = (html) => decode(html.match(/<title>([^<]*)<\/title>/)[1]);
  const h1 = (html) => {
    const all = [...html.matchAll(/<h1[^>]*>([\s\S]*?)<\/h1>/g)];
    assert.equal(all.length, 1, "one h1");
    return decode(
      all[0][1].replace(/<[^>]+>/g, "").replace(/\s+/g, " "),
    ).trim();
  };
  const graph = (html) => jsonLdGraph(html);
  const node = (html, type) =>
    graph(html).find((item) => item["@type"] === type);
  const indexed = (html) => robotsOf(html).startsWith("index");
  // An independent oracle for seo.withBrand: the brand rule restated, not imported.
  const brand = (value) =>
    `${value} | ${NAME}`.length > 72 ? value : `${value} | ${NAME}`;

  // Every route of the site map exists; retired launch paths do not. Topics
  // come from the same table the build read.
  const topics = JSON.parse(await fs.readFile(topicsFile(), "utf8")).topics;
  const required = [
    "/",
    "/stories/",
    "/stories/page/2/",
    "/topics/",
    ...Object.keys(topics).map((slug) => `/topics/${slug}/`),
    "/aircraft/",
    "/aircraft/airbus-a380/",
    "/aircraft/airbus-a380/hl7635/",
    "/aircraft/douglas-dc-3/",
    "/aircraft/cessna-172/",
    "/operators/",
    "/operators/asiana-airlines/",
    "/operators/british-airways/",
    "/weekly/",
    "/field-guide/",
    "/about/",
    "/about/image-credits/",
    AUTHOR_PAGE,
    "/errors/404/",
    ...fixture.events.map((event) => event.article_path),
  ];
  for (const route of required) assert.ok(await exists(route), route);
  // Withdrawn stories' paths, each with the hub its page must send readers
  // to and the link it shows: the Cessna 172 hub still has a story, the Piper
  // has none, so that page points to the journal.
  const withdrawn = new Map([
    [
      "/stories/cessna-172-n4172q-ground-speed-spike-12-sep-2026/",
      { hub: "/aircraft/cessna-172/", label: "other Cessna 172 stories" },
    ],
    [
      "/stories/piper-pa-28-n2817p-above-maximum-speed-14-sep-2026/",
      { hub: "/stories/", label: "the story journal" },
    ],
  ]);
  assert.deepEqual(
    fixture.retired.map((entry) => entry.path),
    [...withdrawn.keys()],
  );
  for (const retired of [
    "/tag/data-oddities/",
    "/tag/data-quality/",
    "/weekly-spotlights/",
    "/image-credits/",
    "/author/wtf-adsb/",
    "/aircraft/airbus-a380/hl7640/",
    "/weekly/page/2/",
  ])
    assert.equal(await exists(retired), false, retired);

  // Collect every built page and its indexing directive.
  const pages = new Map();
  async function walk(folder) {
    for (const entry of await fs.readdir(folder, { withFileTypes: true })) {
      const target = path.join(folder, entry.name);
      if (entry.isDirectory()) await walk(target);
      else if (entry.name === "index.html") {
        const route = `/${path.relative(directory, folder)}/`.replace(
          /^\/\.?\//,
          "/",
        );
        pages.set(route, await fs.readFile(target, "utf8"));
      }
    }
  }
  await walk(directory);
  // Every machine-readable date in a page is a real date.
  for (const [route, html] of pages)
    for (const [, value] of html.matchAll(/datetime="([^"]*)"/g))
      assert.match(
        value,
        /^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z)?$/,
        `${route} datetime ${value}`,
      );
  const indexable = new Set(
    [...pages].filter(([, html]) => indexed(html)).map(([route]) => route),
  );
  // Each withdrawn path is a noindex page whose canonical, refresh and one
  // link name its hub. It loads nothing, from this site or another, and names
  // nothing of the withdrawn story but its path.
  for (const [route, { hub, label }] of withdrawn) {
    const html = pages.get(route);
    assert.ok(html, route);
    assert.equal(title(html), "Story withdrawn", route);
    assert.equal(robotsOf(html), "noindex", route);
    assert.equal(
      html.match(/<link rel="canonical" href="([^"]+)">/)[1],
      ORIGIN + hub,
      route,
    );
    assert.equal(one(html, "http-equiv", "refresh"), `0; url=${hub}`, route);
    assert.match(
      html,
      new RegExp(
        `<p>This story was withdrawn\\. See <a href="${hub}">${label}</a>\\.</p>`,
      ),
      route,
    );
    assert.equal((html.match(/<a /g) || []).length, 1, route);
    assert.doesNotMatch(
      html,
      /<(?:script|img|style|iframe)\b|<link (?!rel="canonical")|n4172q|n2817p|piper/i,
      route,
    );
    assert.ok(pages.has(hub), hub);
    // No other page links to it: not a list, hub, related story or count.
    for (const [other, page] of pages)
      if (other !== route)
        assert.ok(!page.includes(`href="${route}"`), `${other} links ${route}`);
  }
  // Nothing outside the site map asks to be indexed.
  for (const route of indexable)
    assert.match(
      route,
      /^\/(?:$|stories\/|topics\/|aircraft\/|operators\/|weekly\/|field-guide\/|about\/|author\/fixture-author\/)/,
      route,
    );
  for (const [route, html] of pages)
    if (route.startsWith("/event-pages/") || route.startsWith("/errors/"))
      assert.equal(robotsOf(html), "noindex, follow", route);
  for (const route of indexable)
    assert.equal(
      robotsOf(pages.get(route)),
      "index, follow, max-image-preview:large",
    );

  // Titles and headings follow the published formats.
  const home = pages.get("/");
  assert.equal(title(home), `${NAME}: ${TAGLINE}`);
  assert.equal(h1(home), TAGLINE);
  // The site card's alt text, the footer, and the error pages name the
  // configured site.
  assert.equal(one(home, "property", "og:image:alt"), `${NAME}: ${TAGLINE}`);
  assert.match(
    home,
    new RegExp(`<a class="footer-name" href="${SITE}/">${NAME}<span`),
  );
  // The header and the footer each show the support link.
  const supportLinks = [
    ...home.matchAll(
      /<a class="support-cta-link" href="([^"]+)"[^>]*>[\s\S]*?<\/svg>\s*([^<]*?) <span/g,
    ),
  ].map((match) => [match[1], match[2]]);
  assert.deepEqual(supportLinks, [
    [SUPPORT.url, SUPPORT.label],
    [SUPPORT.url, SUPPORT.label],
  ]);
  assert.equal(title(await file("/errors/500/")), `500 | ${NAME}`);
  assert.equal(title(await file("/errors/404/")), `Page not found | ${NAME}`);
  // The home description names the state only.
  assert.equal(one(home, "name", "description"), indexCopy.home);
  assert.deepEqual(
    graph(home)
      .map((item) => item["@type"])
      .sort(),
    ["CollectionPage", "Organization", "WebSite"],
  );
  assert.equal(title(pages.get("/stories/")), `All stories | ${NAME}`);
  assert.equal(h1(pages.get("/stories/")), "All stories");
  assert.equal(
    title(pages.get("/stories/page/2/")),
    `All stories, page 2 | ${NAME}`,
  );
  assert.equal(h1(pages.get("/stories/page/2/")), "All stories, page 2");
  // The archive's pages link each other above and below the list, with rel
  // on each step and the missing step disabled without an address; the
  // head's prev and next name the same pages.
  const pageSteps = (html) =>
    [...html.matchAll(/<a class="page-step page-(prev|next)"([^>]*)>/g)].map(
      ([, kind, attributes]) => [
        kind,
        attributes.match(/ href="([^"]*)"/)?.[1] ?? null,
        attributes.match(/ rel="([^"]*)"/)?.[1] ?? null,
        attributes.includes('aria-disabled="true"'),
      ],
    );
  for (const [route, previous, next] of [
    ["/stories/", null, "/stories/page/2/"],
    ["/stories/page/2/", "/stories/", null],
  ]) {
    const html = pages.get(route);
    const expected = [
      ["prev", previous, previous && "prev", !previous],
      ["next", next, next && "next", !next],
    ];
    assert.deepEqual(pageSteps(html), [...expected, ...expected], route);
    assert.match(
      html,
      /<nav class="pagination page-steps" aria-label="Story pages, top of list">/,
    );
    assert.match(
      html,
      /<nav class="pagination page-numbers" aria-label="Story pages">/,
    );
    for (const [rel, target] of [
      ["prev", previous],
      ["next", next],
    ])
      assert.equal(
        html.includes(`<link rel="${rel}" href="${ORIGIN}${target}">`),
        Boolean(target),
        `${route} head ${rel}`,
      );
  }
  // The journal's archive data lists every story in the archive's order, and
  // each entry is the static first page's own markup.
  const archiveConfig = JSON.parse(
    pages.get("/stories/").match(/data-journal-config>([\s\S]*?)<\/script>/)[1],
  );
  const archiveData = JSON.parse(
    await file(`${archiveConfig.base}archive.json`),
  );
  assert.deepEqual(
    [archiveData.version, archiveData.presentation, archiveData.snapshot_id],
    [archiveConfig.version, archiveConfig.presentation, archiveConfig.snapshot],
  );
  const archiveEntries = (html) =>
    [
      ...html.matchAll(
        /<article class="event-card archive-entry[^"]*" data-event-id="([^"]+)">[\s\S]*?<\/article>/g,
      ),
    ].map(([markup, id]) => ({ id, markup: markup.replace(/\s+/g, " ") }));
  const firstPage = archiveEntries(pages.get("/stories/"));
  const staticPages = [pages.get("/stories/")];
  while (pages.has(`/stories/page/${staticPages.length + 1}/`))
    staticPages.push(pages.get(`/stories/page/${staticPages.length + 1}/`));
  assert.deepEqual(
    archiveData.stories.map((story) => story.id),
    staticPages.flatMap(archiveEntries).map((entry) => entry.id),
  );
  assert.equal(archiveData.stories.length, fixture.events.length);
  assert.deepEqual(
    archiveData.stories
      .slice(0, firstPage.length)
      .map((story) => story.html.replace(/\s+/g, " ").trim()),
    firstPage.map((entry) => entry.markup),
  );
  for (const [slug, topic] of Object.entries(topics)) {
    const html = pages.get(`/topics/${slug}/`);
    assert.equal(title(html), brand(topicCopy[slug]?.title || topic.title));
    assert.equal(h1(html), topicCopy[slug]?.heading || topic.title);
  }
  const typeHub = pages.get("/aircraft/airbus-a380/");
  assert.equal(
    title(typeHub),
    brand("Airbus A380-800 sightings and ADS-B records"),
  );
  assert.equal(h1(typeHub), "Airbus A380-800 observations");
  const airframe = pages.get("/aircraft/airbus-a380/hl7635/");
  assert.equal(
    title(airframe),
    brand("HL7635 (Asiana Airlines Airbus A380-800) sightings"),
  );
  assert.equal(h1(airframe), "HL7635: Airbus A380-800");
  const operator = pages.get("/operators/asiana-airlines/");
  assert.equal(
    title(operator),
    brand("Asiana Airlines flights in the journal"),
  );
  assert.equal(h1(operator), "Asiana Airlines");
  for (const route of [
    "/stories/",
    "/topics/notable-aircraft/",
    "/aircraft/airbus-a380/",
    "/operators/asiana-airlines/",
  ]) {
    const collection = node(pages.get(route), "CollectionPage");
    assert.equal(collection.url, ORIGIN + route);
    assert.equal(collection.mainEntity["@type"], "ItemList");
    assert.ok(collection.mainEntity.itemListElement.length > 0);
    assert.ok(node(pages.get(route), "BreadcrumbList"));
  }

  // Story heads: unique titles and descriptions, raster images, BlogPosting.
  const titles = new Set();
  const descriptions = new Set();
  for (const event of fixture.events) {
    const html = pages.get(event.article_path);
    const pageTitle = title(html);
    const description = one(html, "name", "description");
    assert.ok(!titles.has(pageTitle), `Duplicate title ${pageTitle}`);
    assert.ok(!descriptions.has(description), `Duplicate ${description}`);
    titles.add(pageTitle);
    descriptions.add(description);
    assert.ok(description.length <= 160, description);
    assert.equal(one(html, "property", "og:url"), ORIGIN + event.article_path);
    assert.equal(
      html.match(/<link rel="canonical" href="([^"]+)">/)[1],
      ORIGIN + event.article_path,
    );
    if (event.story) {
      assert.equal(pageTitle, brand(event.story.seo_title));
      assert.equal(description, event.story.dek);
    }
    assert.equal(h1(html), event.title);
    const image = one(html, "property", "og:image");
    assert.match(image, new RegExp(`^${SITE}/.+\\.(?:png|webp|jpe?g)$`));
    assert.match(
      one(html, "property", "og:image:type"),
      /^image\/(?:png|webp|jpeg)$/,
    );
    assert.ok(Number(one(html, "property", "og:image:width")) >= 1200);
    assert.ok(Number(one(html, "property", "og:image:height")) > 0);
    assert.ok(one(html, "property", "og:image:alt"));
    assert.equal(one(html, "name", "twitter:image"), image);
    assert.equal(one(html, "name", "twitter:card"), "summary_large_image");
    // The release dates the page, falling back to the observation start; the
    // last prose or evidence change dates its modification. A later Ghost
    // revision, such as a card refresh, moves neither date.
    const released = event.released_at || event.observed_start;
    const modified =
      (event.released_at && event.content_revised_at) || released;
    assert.equal(one(html, "property", "article:published_time"), released);
    assert.equal(one(html, "property", "article:modified_time"), modified);
    const posting = node(html, "BlogPosting");
    assert.equal(posting.mainEntityOfPage["@id"], ORIGIN + event.article_path);
    assert.equal(posting.url, ORIGIN + event.article_path);
    assert.equal(posting.headline, event.title);
    assert.equal(posting.description, description);
    if (event.story) assert.equal(posting.description, event.story.dek);
    assert.equal(posting.datePublished, released);
    assert.equal(posting.contentReferenceTime, event.observed_start);
    assert.equal(posting.dateModified, modified);
    // The byline keeps the observation date beside the release date, and adds
    // the revision date only when it came more than a day after release.
    const byline = html.match(/<p class="article-byline">([\s\S]*?)<\/p>/)[1];
    const updated =
      event.released_at &&
      event.content_revised_at &&
      Date.parse(event.content_revised_at) - Date.parse(event.released_at) >
        86400000
        ? event.content_revised_at
        : null;
    assert.deepEqual(
      [...byline.matchAll(/<time datetime="([^"]+)"/g)].map(
        (match) => match[1],
      ),
      [event.observed_start, event.released_at, updated].filter(Boolean),
      event.article_path,
    );
    assert.match(
      posting.publisher.logo.url,
      new RegExp(`^${SITE}/social/[a-f0-9]{64}\\.png$`),
    );
    for (const url of posting.image)
      assert.match(url, new RegExp(`^${SITE}/.+\\.(?:png|webp)$`));
    assert.equal(JSON.stringify(posting).includes("contentLocation"), false);
    // The breadcrumb trail follows the aircraft or the topic and ends at the story.
    const crumbs = node(html, "BreadcrumbList").itemListElement;
    assert.equal(crumbs[0].item, `${ORIGIN}/`);
    assert.equal(crumbs.at(-1).name, event.story?.seo_title || event.title);
    assert.equal(crumbs.at(-1).item, undefined);
    for (const crumb of crumbs.slice(1, -1)) {
      const route = new URL(crumb.item).pathname;
      assert.ok(pages.has(route), route);
    }
    const parent = new URL(crumbs.at(-2).item).pathname;
    assert.ok(
      event.context?.type
        ? parent.startsWith("/aircraft/")
        : parent.startsWith("/topics/"),
      parent,
    );
  }
  // Template-only prose and older stories: the indexing gate.
  const byPath = (slug) =>
    fixture.events.find((event) => event.article_path.includes(slug));
  for (const [slug, expected] of [
    ["airbus-a380-hl7635-27-sep-2026", true],
    ["douglas-dc-3-n8704-2-oct-2026", true],
    ["airbus-a380-g-xleb-4-oct-2026", false],
    ["boeing-737-800-n3730b-squawk-7700-25-sep-2026", false],
    ["c-5m-super-galaxy-87-0043-4-oct-2026", false],
    ["helicopter-circuits", true],
  ])
    assert.equal(indexable.has(byPath(slug).article_path), expected, slug);
  // Ghost's updated_at also moves for card refreshes, so a refresh two days
  // after release shows no "Updated" label and leaves dateModified alone.
  const refreshed = byPath("douglas-dc-3-n8704-2-oct-2026");
  assert.ok(
    Date.parse(refreshed.revised_at) - Date.parse(refreshed.released_at) >
      86400000,
  );
  assert.equal(refreshed.content_revised_at, refreshed.released_at);
  // A prose revision more than a day after release shows "Updated"; one within
  // a day only moves dateModified and the sitemap's lastmod.
  const revisedLater = byPath("airbus-a380-hl7635-27-sep-2026");
  const revisedSoon = byPath(
    "cessna-172-n6508k-ground-speed-spike-18-sep-2026",
  );
  assert.ok(
    Date.parse(revisedLater.content_revised_at) -
      Date.parse(revisedLater.released_at) >
      86400000,
  );
  assert.ok(revisedSoon.content_revised_at > revisedSoon.released_at);
  const labelled = [...pages]
    .filter(([, html]) => /<span>Updated <time /.test(html))
    .map(([route]) => route);
  assert.deepEqual(labelled, [revisedLater.article_path]);
  assert.match(
    pages.get(revisedLater.article_path),
    new RegExp(
      `<span>Updated <time datetime="${revisedLater.content_revised_at}">`,
    ),
  );
  assert.equal(
    one(
      pages.get(revisedSoon.article_path),
      "property",
      "article:modified_time",
    ),
    revisedSoon.content_revised_at,
  );

  // Hubs exist, and ask to be indexed, exactly at their thresholds.
  for (const [route, expected] of [
    ["/aircraft/airbus-a380/", true],
    ["/aircraft/douglas-dc-3/", true],
    ["/aircraft/cessna-172/", false],
    ["/aircraft/boeing-737-800/", false],
    ["/aircraft/airbus-a380/hl7635/", true],
    ["/operators/asiana-airlines/", true],
    ["/operators/british-airways/", false],
    ["/operators/delta-air-lines/", false],
    ["/topics/notable-aircraft/", true],
    ["/topics/formation-flights/", false],
  ])
    assert.equal(indexable.has(route), expected, route);

  // Sitemap index, child sitemaps with lastmod, only indexable pages.
  const sitemapIndex = await file("/sitemap.xml");
  const children = [
    ...sitemapIndex.matchAll(
      /<sitemap><loc>([^<]+)<\/loc><lastmod>([^<]+)<\/lastmod><\/sitemap>/g,
    ),
  ];
  assert.deepEqual(children.map((match) => new URL(match[1]).pathname).sort(), [
    "/sitemap-aircraft.xml",
    "/sitemap-operators.xml",
    "/sitemap-pages.xml",
    "/sitemap-stories.xml",
    "/sitemap-topics.xml",
  ]);
  const listed = new Map();
  for (const [, loc] of children) {
    const xml = await file(new URL(loc).pathname);
    const urls = [
      ...xml.matchAll(
        /<url><loc>([^<]+)<\/loc><lastmod>([^<]+)<\/lastmod><\/url>/g,
      ),
    ];
    assert.equal(urls.length, (xml.match(/<url>/g) || []).length, loc);
    for (const [, url, lastmod] of urls) {
      const route = new URL(url).pathname;
      assert.ok(indexable.has(route), `${route} is not indexable`);
      assert.ok(!listed.has(route), `${route} listed twice`);
      assert.match(lastmod, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
      listed.set(route, lastmod);
    }
  }
  for (const route of withdrawn.keys())
    assert.ok(!listed.has(route), `${route} is in a sitemap`);
  for (const event of fixture.events) {
    assert.equal(
      listed.has(event.article_path),
      indexable.has(event.article_path),
    );
    // A story's lastmod is its last prose or evidence change, else its release.
    if (listed.has(event.article_path))
      assert.equal(
        listed.get(event.article_path),
        (event.released_at && event.content_revised_at) ||
          event.released_at ||
          event.observed_start,
        event.article_path,
      );
  }
  const robots = await file("/robots.txt");
  assert.match(robots, /^Disallow: \/event-pages\/$/m);
  assert.match(robots, /^Disallow: \/event-data\/$/m);
  assert.match(robots, new RegExp(`^Sitemap: ${SITE}/sitemap\\.xml$`, "m"));
  // Root icons: a 16/32/48 pixel ICO and the 180 pixel iOS touch icon.
  const favicon = await fs.readFile(path.join(directory, "favicon.ico"));
  assert.deepEqual(
    readIco(favicon)?.map((frame) => [frame.width, frame.height]),
    FAVICON_SIZES.map((size) => [size, size]),
    "favicon.ico frames",
  );
  const touchIcon = await fs.readFile(
    path.join(directory, "apple-touch-icon.png"),
  );
  assert.deepEqual(
    [touchIcon.readUInt32BE(16), touchIcon.readUInt32BE(20)],
    [TOUCH_SIZE, TOUCH_SIZE],
    "apple-touch-icon.png size",
  );

  // The feed: latest stories by release, GUID = event ID.
  const feed = await fs.readFile(path.join(directory, "rss/index.xml"), "utf8");
  const guids = [
    ...feed.matchAll(/<guid isPermaLink="false">([^<]+)<\/guid>/g),
  ].map((match) => match[1]);
  assert.equal(guids.length, Math.min(50, fixture.events.length));
  // Neither the feed nor the theme search names a withdrawn story.
  const searchIndex = await fs.readFile(
    path.join(directory, "search-index/index.txt"),
    "utf8",
  );
  for (const route of withdrawn.keys()) {
    assert.ok(!feed.includes(route), `${route} is in the feed`);
    assert.ok(!searchIndex.includes(route), `${route} is in the search`);
  }
  assert.deepEqual(
    [...guids].sort(),
    fixture.events.map((event) => event.id).sort(),
  );
  assert.match(home, /<link rel="alternate" type="application\/rss\+xml"/);

  // Every story is within three crawlable clicks of the home page.
  const depth = new Map([["/", 0]]);
  const queue = ["/"];
  while (queue.length) {
    const route = queue.shift();
    const html = pages.get(route);
    if (!html || depth.get(route) >= 3) continue;
    for (const [, href] of html.matchAll(/href="(\/[^"#?]*)/g)) {
      if (
        /^\/(?:event-pages|event-data|search-index|assets|social|event-media)\//.test(
          href,
        )
      )
        continue;
      if (!depth.has(href)) {
        depth.set(href, depth.get(route) + 1);
        queue.push(href);
      }
    }
  }
  for (const event of fixture.events)
    assert.ok(depth.get(event.article_path) <= 3, event.article_path);

  // routes.json lists every generated route as one sorted list of paths.
  const inventory = JSON.parse(await file("/routes.json"));
  assert.ok(inventory.every((route) => typeof route === "string"));
  assert.deepEqual(inventory, [...new Set(inventory)].sort());
  for (const route of pages.keys()) assert.ok(inventory.includes(route), route);
  // routes.json keeps each withdrawn path, so no later story takes it; the
  // browser audit leaves them out.
  const audited = JSON.parse(await file("/audit-routes.json"));
  for (const route of withdrawn.keys())
    assert.ok(!audited.includes(route), `${route} is audited`);
  const segments = new Set(inventory.map((route) => route.split("/")[1]));
  for (const segment of [
    "stories",
    "topics",
    "aircraft",
    "operators",
    "weekly",
    "about",
    "field-guide",
    "author",
    "rss",
    "robots.txt",
    "sitemap.xml",
    "favicon.ico",
    "apple-touch-icon.png",
    "social",
    "event-media",
    "event-pages",
  ])
    assert.ok(segments.has(segment), segment);

  // No built file names a private host or a receiver-relative field, and
  // only pages name Planespotters.
  const privateText = privateNames();
  async function scan(folder) {
    for (const entry of await fs.readdir(folder, { withFileTypes: true })) {
      const target = path.join(folder, entry.name);
      if (entry.isDirectory()) await scan(target);
      else if (/\.(?:html|xml|json|svg|txt|css|js|mjs)$/.test(entry.name)) {
        const text = await fs.readFile(target, "utf8");
        const name = path.relative(directory, target);
        assert.doesNotMatch(text, privateText, name);
        if (!entry.name.endsWith(".html"))
          assert.doesNotMatch(text, PHOTO_DATA, name);
      }
    }
  }
  await scan(directory);

  // Pages load nothing from another origin but the airframe images inside
  // their figures on story pages, which appear only in those two articles.
  for (const [route, html] of pages) {
    assert.deepEqual(
      thirdPartyResources(html, ORIGIN, {
        story: /^\/stories\/[^/]+\/$/.test(route),
      }),
      [],
      route,
    );
    assert.equal(
      html.includes('class="airframe-photo'),
      airframeStories.includes(route),
      route,
    );
  }

  // Pages load only the Latin-subset WOFF2 fonts. The body font's preload and
  // its @font-face rule share one versioned URL, and no TrueType file ships.
  const fontUrls = [
    ...home.matchAll(
      /src:url\("(\/assets\/fonts\/[^"]+)"\) format\("woff2"\)/g,
    ),
  ].map((match) => match[1]);
  assert.deepEqual(
    fontUrls.map((url) => url.replace(/\?v=[a-f0-9]{10}$/, "")),
    [
      "/assets/fonts/InstrumentSans-latin.woff2",
      "/assets/fonts/Newsreader-latin.woff2",
    ],
  );
  const preload = `<link rel="preload" href="${fontUrls[0]}" as="font" type="font/woff2" crossorigin>`;
  for (const [route, html] of pages) {
    if (withdrawn.has(route)) continue;
    assert.ok(html.includes(preload), `${route} preloads the body font`);
    assert.doesNotMatch(html, /\.ttf\b/, route);
  }
  assert.deepEqual(
    (await fs.readdir(path.join(directory, "assets/fonts"))).sort(),
    [
      "InstrumentSans-latin.woff2",
      "Newsreader-latin.woff2",
      "OFL-instrumentsans.txt",
      "OFL-newsreader.txt",
    ],
  );

  // The public proxy: headers, private paths, immutable hashed files.
  const image = (
    await fs.readFile(
      path.join(root, "deploy/ghost-preview/compose.yaml"),
      "utf8",
    )
  ).match(/image: (caddy:\S+)/)[1];
  // Preserve public routing/header rules; only replace TLS virtual hosts with
  // an ephemeral loopback listener for this offline fixture. The fixture's
  // requests arrive from the Docker network, which stands in for Cloudflare's
  // ranges, so the access log takes the client from Cf-Connecting-Ip.
  const published = (
    await fs.readFile(path.join(root, "deploy/ghost/Caddyfile"), "utf8")
  ).split("\nwww.")[0];
  const caddy = published
    .replace("{$DOMAIN}", ":8080")
    .replace(
      /trusted_proxies static [^\n]+/,
      "trusted_proxies static private_ranges",
    )
    .replace(
      "{\n\tservers {",
      "{\n\tadmin off\n\tauto_https off\n\tpersist_config off\n\tservers {",
    );
  assert.match(caddy, /admin off[\s\S]*private_ranges[\s\S]*\n:8080 \{/);
  const config = path.join(privateDir, "Caddyfile");
  await fs.writeFile(config, caddy, { mode: 0o600 });
  docker("network", "create", network);
  const startBackend = () =>
    docker(
      "run",
      "--detach",
      "--name",
      backend,
      "--network",
      network,
      "--network-alias",
      "wtf-adsb-site",
      "--mount",
      `type=bind,src=${directory},dst=/srv,readonly`,
      "--mount",
      `type=bind,src=${path.join(root, "deploy/ghost-preview/Caddyfile")},dst=/etc/caddy/Caddyfile,readonly`,
      image,
    );
  startBackend();
  // A public proxy on a loopback port, once it answers.
  async function startProxy(name, file) {
    docker(
      "run",
      "--detach",
      "--name",
      name,
      "--network",
      network,
      "--publish",
      "127.0.0.1::8080",
      "--mount",
      `type=bind,src=${file},dst=/etc/caddy/Caddyfile,readonly`,
      image,
    );
    const address = `http://${docker("port", name, "8080/tcp")}`;
    let response;
    for (let attempt = 0; attempt < 50; attempt += 1) {
      try {
        response = await fetch(address, { signal: AbortSignal.timeout(1000) });
        if (response.status === 200) break;
      } catch {
        // A detached container may not have bound its port yet.
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert.ok(response, "Public Caddy did not start");
    return { address, response };
  }
  const { address: base, response } = await startProxy(frontend, config);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-robots-tag"), null);
  assert.equal(response.headers.get("cache-control"), "no-cache, no-transform");
  assert.match(
    response.headers.get("content-security-policy"),
    /script-src 'self'/,
  );
  // Images come from the site and Planespotters' image host, and only there.
  assert.deepEqual(
    response.headers
      .get("content-security-policy")
      .split(";")
      .map((part) => part.trim().split(/\s+/))
      .find(([name]) => name === "img-src")
      .slice(1),
    IMAGE_POLICY,
  );
  for (const route of [
    "/ghost/",
    "/ghost/api/admin/site/",
    "/content/images/",
    "/members/api/member/",
    "/publication.json",
    "/event-snapshot.json",
    "/routes.json",
    "/audit-routes.json",
    "/.env",
    "/missing/",
    "/tag/data-quality/",
    "/weekly-spotlights/",
  ])
    assert.equal((await fetch(base + route)).status, 404, route);
  assert.equal((await fetch(base, { method: "POST" })).status, 405);
  // The access log keeps only what tools/site_stats.py reads: the visitor
  // from Cf-Connecting-Ip, the method, the path without its query, the
  // status, the user agent, the referrer's host and the country. No other
  // header, no cookie and no response header is written.
  const reader = {
    "Cf-Connecting-Ip": "203.0.113.7",
    "Cf-Ipcountry": "CA",
    Cookie: "session=private-cookie-value",
    Authorization: "Bearer private-token-value",
    Referer: "https://news.example.org/item?id=private-referrer-query",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Firefox/131.0",
  };
  assert.equal(
    (
      await fetch(base + "/stories/?utm_source=private-query", {
        headers: reader,
      })
    ).status,
    200,
  );
  let accessLog = "";
  for (let attempt = 0; attempt < 50; attempt += 1) {
    accessLog = docker("exec", frontend, "cat", "/data/access/public.log");
    if (accessLog.includes("203.0.113.7")) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(
    docker("exec", frontend, "stat", "-c", "%a", "/data/access/public.log"),
    "600",
  );
  for (const secret of [
    "private-cookie-value",
    "private-token-value",
    "private-referrer-query",
    "private-query",
    "resp_headers",
    "remote_ip",
  ])
    assert.ok(!accessLog.includes(secret), `The access log keeps ${secret}`);
  const logged = accessLog
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line))
    .find((line) => line.request.client_ip === "203.0.113.7");
  assert.deepEqual(
    {
      keys: Object.keys(logged).sort(),
      request: logged.request,
      status: logged.status,
      referer: logged.referer,
      country: logged.country,
      userAgent: logged.user_agent,
    },
    {
      keys: [
        "country",
        "level",
        "logger",
        "msg",
        "referer",
        "request",
        "status",
        "ts",
        "user_agent",
      ],
      request: { client_ip: "203.0.113.7", method: "GET", uri: "/stories/" },
      status: 200,
      referer: "news.example.org",
      country: "CA",
      userAgent: reader["User-Agent"],
    },
  );
  // The summary reads the same log: one page view by one visitor today.
  const logCopy = path.join(privateDir, "public.log");
  await fs.writeFile(logCopy, accessLog, { mode: 0o600 });
  const stats = JSON.parse(
    run("python3", [
      "-B",
      path.join(root, "tools/site_stats.py"),
      "--logs",
      logCopy,
      "--date",
      new Date().toISOString().slice(0, 10),
    ]),
  );
  assert.deepEqual(
    [stats.page_views, stats.visitors, stats.referrers, stats.countries],
    [
      1,
      1,
      [{ host: "news.example.org", views: 1 }],
      [{ country: "CA", visitors: 1 }],
    ],
  );
  assert.match(
    await (await fetch(base + "/missing/")).text(),
    /Page not found/,
  );
  const stylesheet = home.match(
    /href="(\/assets\/css\/screen\.css\?v=[a-f0-9]+)"/,
  )[1];
  const socialImage = home.match(
    new RegExp(`content="${SITE}(/social/[a-f0-9]{64}\\.png)"`),
  )[1];
  const figure = (await fs.readdir(path.join(directory, "event-media"))).find(
    (name) => name.endsWith(".svg"),
  );
  // Browsers keep hashed story media for a year; the CDN revalidates it, so a
  // withdrawal at the origin takes effect there at once.
  for (const route of [socialImage, `/event-media/${figure}`]) {
    const hashed = await fetch(base + route);
    assert.equal(hashed.status, 200, route);
    assert.equal(
      hashed.headers.get("cache-control"),
      "public, max-age=31536000, s-maxage=0, immutable",
      route,
    );
  }
  for (const route of [stylesheet, ...fontUrls]) {
    const versioned = await fetch(base + route);
    assert.equal(versioned.status, 200, route);
    assert.equal(
      versioned.headers.get("cache-control"),
      "public, max-age=31536000, immutable",
      route,
    );
  }
  // WOFF2 is already compressed, so the proxy sends it as stored.
  const font = await fetch(base + fontUrls[1]);
  assert.match(font.headers.get("content-type"), /^font\/woff2/);
  assert.equal(font.headers.get("content-encoding"), null);
  // A missing hashed or versioned file is an ordinary, uncached 404.
  for (const route of [
    `/social/${"a".repeat(64)}.png`,
    `/event-media/${"b".repeat(64)}.svg`,
    stylesheet.replace("/assets/", "/ASSETS/"),
  ]) {
    const missing = await fetch(base + route);
    assert.equal(missing.status, 404, route);
    assert.equal(
      missing.headers.get("cache-control"),
      "no-cache, no-transform",
      route,
    );
  }
  for (const route of ["/stories/", "/assets/css/screen.css"])
    assert.equal(
      (await fetch(base + route)).headers.get("cache-control"),
      "no-cache, no-transform",
      route,
    );
  const rss = await fetch(base + "/rss/");
  assert.equal(rss.status, 200);
  assert.match(rss.headers.get("content-type"), /^application\/rss\+xml/);
  assert.match(await rss.text(), /^<\?xml version="1\.0"/);
  const sitemap = await fetch(base + "/sitemap.xml");
  assert.equal(sitemap.status, 200);
  assert.match(sitemap.headers.get("content-type"), /xml/);
  // Root icons are stable root files: an image type, revalidated like pages.
  for (const [route, type] of [
    ["/favicon.ico", /^image\/(?:vnd\.microsoft\.icon|x-icon)/],
    ["/apple-touch-icon.png", /^image\/png/],
  ]) {
    const icon = await fetch(base + route);
    assert.equal(icon.status, 200, route);
    assert.match(icon.headers.get("content-type"), type, route);
    assert.equal(
      icon.headers.get("cache-control"),
      "no-cache, no-transform",
      route,
    );
  }
  // The live checker passes through the same proxy rules, with the public
  // origin answered by this proxy, including its header and caching checks.
  const liveCheck = (address, ...extra) =>
    spawnSync(
      "node",
      [
        path.join(tools, "verify-live.mjs"),
        "--base",
        address,
        "--canonical",
        ORIGIN,
        "--snapshot",
        input,
        "--media",
        path.join(media, "manifest.json"),
        ...extra,
      ],
      { cwd: root, encoding: "utf8", timeout: 180000 },
    );
  // An old path in a 404 list that is now a withdrawn story's page is checked
  // as a redirect instead.
  const retiredList = path.join(privateDir, "retired.txt");
  await fs.writeFile(
    retiredList,
    `/an-old-story/\n${[...withdrawn.keys()][0]}\n`,
    { mode: 0o600 },
  );
  const redirectArguments = [...withdrawn].flatMap(([route, { hub }]) => [
    "--expect-redirect",
    `${route}=${hub}`,
  ]);
  const checked = liveCheck(
    base,
    "--expect-404",
    retiredList,
    ...redirectArguments,
  );
  assert.equal(checked.status, 0, checked.stderr);
  const live = JSON.parse(checked.stdout.trim().split("\n").at(-1));
  assert.deepEqual(
    [live.local, live.privateRoutesBlocked, live.cacheControl],
    [false, true, true],
  );
  assert.equal(live.articles, fixture.events.length);
  assert.equal(live.withdrawnRedirects, withdrawn.size);
  assert.equal(live.expected404, 1);
  // A path expected to redirect that serves no withdrawn story's page, or a
  // hub other than the snapshot's, fails the live checker.
  for (const [argument, message] of [
    ["/stories/never-withdrawn-1-oct-2026/=/stories/", "Withdrawn story"],
    [
      `${[...withdrawn.keys()][0]}=/aircraft/piper-pa-28/`,
      "disagrees with the snapshot's hub",
    ],
  ]) {
    const failed = liveCheck(base, "--expect-redirect", argument);
    assert.notEqual(failed.status, 0, argument);
    assert.ok(
      failed.stderr.includes(message),
      `${argument}: ${failed.stderr.slice(-800)}`,
    );
  }
  assert.equal(
    live.indexed,
    fixture.events.filter((event) => listed.has(event.article_path)).length,
  );
  assert.ok(live.replay);
  // A proxy that serves the route list, lets the CDN keep story images or
  // blocks the airframe photos fails the live checker.
  const rules = await fs.readFile(config, "utf8");
  for (const [name, edit, message] of [
    [broken[0], (text) => text.replace(" /routes.json", ""), "/routes.json"],
    [
      broken[1],
      (text) => text.replace("s-maxage=0, ", ""),
      "Cache-Control on /social/",
    ],
    [
      broken[2],
      (text) =>
        text.replace(
          `img-src ${IMAGE_POLICY.join(" ")};`,
          "img-src 'self' data:;",
        ),
      "img-src on /",
    ],
  ]) {
    const file = `${config}-${name}`;
    assert.notEqual(edit(rules), rules, name);
    await fs.writeFile(file, edit(rules), { mode: 0o600 });
    const failed = liveCheck((await startProxy(name, file)).address);
    assert.notEqual(failed.status, 0, name);
    assert.ok(
      failed.stderr.includes(message),
      `${name}: ${failed.stderr.slice(-800)}`,
    );
    docker("rm", "--force", name);
  }
  docker("rm", "--force", backend);
  const duringReplacement = fetch(base);
  await new Promise((resolve) => setTimeout(resolve, 400));
  startBackend();
  assert.equal((await duringReplacement).status, 200);

  const preview = await startPreview({ directory });
  close = preview.close;
  const weeklyEvent = fixture.events.find((event) => event.weekly_spotlight);
  assert.ok(weeklyEvent);
  for (const width of [390, 1440]) {
    const context = await preview.browser.newContext({
      viewport: { width, height: 950 },
    });
    const errors = [];
    // A stand-in answers for the airframe photos; any other request to
    // another origin fails the check. These pages come through the public
    // proxy, whose strict-origin-when-cross-origin policy reaches the photo
    // request: the site's origin alone, never the page's path.
    const photoReferers = [];
    await guardRequests(context, {
      isSite: (url) => url.hostname === "127.0.0.1",
      images: new Set(Object.values(airframePhotos).map((photo) => photo.src)),
      failures: errors,
      referers: photoReferers,
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    const fontRequests = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname.startsWith("/assets/fonts/"))
        fontRequests.push(url.pathname + url.search);
    });
    await page.addInitScript(() => {
      Date.now = () => Date.parse("2026-10-05T15:00:00Z");
    });
    for (const route of [
      "/",
      weeklyEvent.article_path,
      "/stories/airbus-a380-hl7635-27-sep-2026/",
      "/aircraft/airbus-a380/",
      "/weekly/",
      "/about/",
    ]) {
      assert.equal((await page.goto(base + route)).status(), 200, route);
      assert.equal(await page.locator(".preview-notice").count(), 0);
      assert.equal(
        await page.locator('link[rel="canonical"]').getAttribute("href"),
        ORIGIN + route,
      );
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth + 1,
        ),
        false,
      );
      if (route === "/") {
        assert.equal(await page.locator(".weekly-card").count(), 1);
        assert.equal(await page.locator(".home-latest .story-tile").count(), 8);
        // Both families load, and the preloaded body font is fetched once.
        assert.deepEqual(
          await page.evaluate(async () => {
            await document.fonts.ready;
            return [...document.fonts]
              .filter((face) => face.status === "loaded")
              .map((face) => face.family.replaceAll('"', ""))
              .sort();
          }),
          ["Instrument Sans", "Newsreader"],
        );
        assert.deepEqual([...fontRequests].sort(), [...fontUrls].sort());
      }
      if (route === weeklyEvent.article_path)
        assert.equal(await page.locator(".weekly-research").count(), 1);
      if (airframeStories.includes(route)) {
        const photo = page.locator("figure.airframe-photo img");
        await photo.scrollIntoViewIfNeeded();
        await photo.evaluate((image) => image.decode());
        assert.deepEqual(
          photoReferers,
          [`${new URL(base).origin}/`],
          `Photo Referer on ${route}`,
        );
      }
      if (route === "/about/") {
        const text = await page.locator("main").textContent();
        assert.doesNotMatch(text, /private preview|invented flight data/i);
        assert.match(text, /How publication works/);
        assert.match(text, /Open Data Commons Attribution License/);
      }
    }
    // The archive's journal still searches without leaving the page, and
    // pages the archive at another size through the public proxy.
    await page.goto(base + "/stories/");
    await page.locator("[data-open-filters]").waitFor({ state: "visible" });
    await page.locator("[data-page-size]").selectOption("10");
    await page.waitForURL(`${base}/stories/?size=10`);
    await page.waitForFunction(
      () =>
        document.querySelectorAll('[data-view="archive"] [data-event-id]')
          .length === 10,
    );
    await page
      .locator('[data-event-feed] nav.page-steps [data-event-page="next"]')
      .click();
    await page.waitForURL(`${base}/stories/?size=10&page=2`);
    await page.waitForFunction(
      () =>
        document.querySelector('[data-view="archive"]')?.dataset.page === "2",
    );
    assert.deepEqual(
      await page
        .locator('[data-view="archive"] [data-event-id]')
        .evaluateAll((items) => items.map((item) => item.dataset.eventId)),
      archiveData.stories.slice(10, 20).map((story) => story.id),
    );
    assert.deepEqual(errors, []);
    await context.close();
  }
  console.log(
    `Production checks passed: ${pages.size} pages, ${indexable.size} indexable, ${listed.size} in sitemaps; titles, heads, JSON-LD, gates, hubs, archive page links and data, crawl depth, feed, robots, root icons, fonts, caching, the access log, privacy, withdrawn-story redirects, and the old snapshot shape.`,
  );
} finally {
  if (close) await close();
  for (const name of [frontend, backend, ...broken])
    spawnSync("docker", ["rm", "--force", name], { stdio: "ignore" });
  spawnSync("docker", ["network", "rm", network], { stdio: "ignore" });
  for (const folder of [directory, media, legacyDirectory, privateDir])
    await fs.rm(folder, { recursive: true, force: true });
}
