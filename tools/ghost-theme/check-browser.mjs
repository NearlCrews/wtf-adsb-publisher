import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import AxeBuilder from "@axe-core/playwright";
import {
  PHOTO_DATA,
  guardRequests,
  thirdPartyResources,
} from "./airframe-photos.mjs";
import { startPreview } from "./browser.mjs";
import { author, stories } from "./demo-content.mjs";
import { output, preview, privateNames, root, tools } from "./paths.mjs";
import { privateScratch } from "./scratch.mjs";
import { fixtureTableSha256, writeTypeTable } from "./reference-fixture.mjs";

const { browser, base, close } = await startPreview();
const routes = JSON.parse(
  await fs.readFile(path.join(preview, "audit-routes.json"), "utf8"),
);
// A snapshot fixture with story, context, and older events covers what the
// synthetic preview cannot: story prose, type background, related stories,
// type, airframe and operator hubs, numbered pages, and legacy stories.
const scratch = await privateScratch("browser-fixture-");
const fixtureDirectory = await fs.mkdtemp(
  path.join(output, "browser-fixture-"),
);
const fixtureRun = (command, args) => {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    timeout: 120000,
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
};
// A review snapshot from site_fixtures.py, kept in the private scratch
// directory, with its media in the fixture directory.
async function fixtureSnapshot(name, ...options) {
  const input = path.join(scratch, `${name}-snapshot.json`);
  const json = fixtureRun("python3", [
    "-B",
    path.join(tools, "site_fixtures.py"),
    "--mode",
    "review",
    ...options,
    "--media",
    path.join(fixtureDirectory, `${name}-media`),
  ]);
  await fs.writeFile(input, json, { mode: 0o600 });
  return { name, input, events: JSON.parse(json).events };
}
// The site build of a fixture snapshot; returns its directory.
function buildFixture(snapshot, ...options) {
  const directory = path.join(fixtureDirectory, snapshot.name);
  fixtureRun("node", [
    path.join(tools, "build.mjs"),
    "--snapshot",
    snapshot.input,
    "--media",
    path.join(fixtureDirectory, `${snapshot.name}-media/manifest.json`),
    "--type-table-sha256",
    fixtureTableSha256,
    ...options,
    "--output",
    directory,
  ]);
  return directory;
}
// The builds read a fixture aircraft-type table from the reference directory,
// for the class photos below.
process.env.WTF_ADSB_REFERENCE_DIR = await writeTypeTable(
  path.join(scratch, "references"),
);
const fixture = await fixtureSnapshot("site");
// Two airframe photos: one under a map, one leading a story that has none.
// A stand-in answers for Planespotters' image host (see open() below).
const mappedStory = "/stories/airbus-a380-hl7635-27-sep-2026/";
const leadingStory = "/stories/airbus-a380-hl7640-20-sep-2026/";
const photoIds = { [mappedStory]: "1001", [leadingStory]: "1002" };
const airframeInput = path.join(scratch, "airframe-photos.json");
const airframeByPath = {};
const airframePhotos = {};
for (const event of fixture.events) {
  const id = photoIds[event.article_path];
  if (!id) continue;
  airframeByPath[event.article_path] = airframePhotos[event.id] = {
    src: `https://t.plnspttrs.net/00000/${id}_test_280.jpg`,
    width: 420,
    height: 280,
    link: `https://www.planespotters.net/photo/${id}/${event.aircraft.registration.toLowerCase()}-test?utm_source=api`,
    photographer: "Test Photographer",
    registration: event.aircraft.registration,
  };
}
await fs.writeFile(airframeInput, JSON.stringify({ photos: airframePhotos }), {
  mode: 0o600,
});
const fixtureFolder = buildFixture(fixture, "--airframe-photos", airframeInput);
// Photo data stays in article pages, and the only third-party loads in any
// page are the airframe images inside their figures on story pages.
const airframePages = [];
async function scanBuild(folder) {
  for (const entry of await fs.readdir(folder, { withFileTypes: true })) {
    const file = path.join(folder, entry.name);
    if (entry.isDirectory()) await scanBuild(file);
    else if (entry.name.endsWith(".html")) {
      const html = await fs.readFile(file, "utf8");
      const route = `/${path.relative(fixtureFolder, path.dirname(file))}/`;
      assert.deepEqual(
        thirdPartyResources(html, "http://127.0.0.1", {
          story: /^\/stories\/[^/]+\/$/.test(route),
        }),
        [],
        file,
      );
      if (html.includes('class="airframe-photo')) airframePages.push(route);
    } else if (/\.(?:json|txt|xml|svg|css|js)$/.test(entry.name))
      assert.doesNotMatch(await fs.readFile(file, "utf8"), PHOTO_DATA, file);
  }
}
await scanBuild(fixtureFolder);
assert.deepEqual(airframePages.sort(), Object.keys(airframeByPath).sort());
const fixtureSite = await startPreview({ directory: fixtureFolder });
// A second, later snapshot of the main stories alone: one of them is then
// older than the journal's default 30-day window, which tells the story
// archive apart from the default query.
const olderSite = await startPreview({
  directory: buildFixture(
    await fixtureSnapshot(
      "older",
      "--as-of",
      "2026-10-12T15:00:00Z",
      "--no-extra",
    ),
  ),
});
// More circling stories page the archive and their topic three times over:
// the static lists' Previous and Next, and the journal's archive pages.
const pagedSite = await startPreview({
  directory: buildFixture(
    await fixtureSnapshot("paged", "--extra-count", "45"),
  ),
});
const pagedLists = [
  ["/stories/", 3],
  ["/topics/flight-shapes/", 3],
];
const pagedRoutes = pagedLists.flatMap(([list, pages]) =>
  Array.from({ length: pages }, (_, index) =>
    index ? `${list}page/${index + 1}/` : list,
  ),
);
// The weekly fixture's researched story, under the story path family.
const weeklyStory = "/stories/helicopter-circuits-3-oct-2026/";
// The one fixture story whose readings came by multilateration.
const mlatStory = "/stories/douglas-dc-3-n8704-2-oct-2026/";
// The newest fixture story, whose type (the R44) has no type photo.
const classStory = "/stories/robinson-r44-n34fx-circling-5-oct-2026/";
const mlatId = fixture.events.find(
  (event) => event.article_path === mlatStory,
).id;
const fixtureRoutes = [
  "/",
  "/stories/",
  "/stories/page/2/",
  mappedStory,
  leadingStory,
  "/stories/cessna-172-n6508k-ground-speed-spike-18-sep-2026/",
  "/stories/c-5m-super-galaxy-87-0043-4-oct-2026/",
  // The story engine's own certified-limit stories, with reference-data rows.
  "/stories/embraer-e195-e2-n295rl-above-certified-ceiling-24-sep-2026/",
  "/stories/airbus-a320-n320rs-above-maximum-speed-25-sep-2026/",
  weeklyStory,
  "/topics/",
  "/topics/notable-aircraft/",
  "/aircraft/",
  "/aircraft/airbus-a380/",
  "/aircraft/airbus-a380/hl7635/",
  "/operators/",
  "/operators/asiana-airlines/",
  "/weekly/",
  author.url,
];
// The packager applies the same expression to the theme files it publishes.
const privateText = privateNames();
const replayStory = stories[0].url;
const report = { pages: [], interactions: [], issues: [] };
const checkedLinks = new Set();

// Pages may load from another origin only the fixture's airframe images,
// which a stand-in answers; any other third-party request fails the check.
// The preview server answers every page with Referrer-Policy: no-referrer,
// and the image inherits it, so no photo request may carry a Referer.
const airframeImages = new Set(
  Object.values(airframePhotos).map((photo) => photo.src),
);
const photoReferers = [];
async function open(options) {
  const context = await browser.newContext(options);
  const errors = [];
  await guardRequests(context, {
    isSite: (url) => url.hostname === "127.0.0.1",
    images: airframeImages,
    failures: errors,
    referers: photoReferers,
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  return {
    context,
    page,
    async done() {
      assert.deepEqual(errors, []);
      await context.close();
    },
  };
}
async function audit(page, details) {
  const { violations } = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "best-practice"])
    .analyze();
  if (violations.length)
    report.issues.push({
      ...details,
      violations: violations.map((v) => ({
        id: v.id,
        impact: v.impact,
        nodes: v.nodes.map((n) => n.target),
      })),
    });
  return violations.length;
}
const appearance = (page) =>
  page.locator("html").getAttribute("data-appearance");

// WCAG contrast ratios, measured in the page: an element's text against the
// first opaque background behind it ("text"), or a field's border against the
// field's own fill and against the background around it ("boundary"). The axe
// scan does not measure boundaries or every appearance, so the checks keep
// their own assertions. Playwright sends this function into the page as
// source text, which is why its helpers live inside it.
function pageContrast(element, kind) {
  const parse = (color) => {
    const [r, g, b, a = 1] = color
      .match(/[\d.]+/g)
      .map(Number)
      .slice(0, 4);
    return a === 0 ? null : [r, g, b];
  };
  const luminance = ([r, g, b]) => {
    const channel = (value) => {
      const c = value / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
  };
  const contrast = (a, b) => {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
  };
  const backdrop = (start) => {
    for (let node = start; node; node = node.parentElement) {
      const color = parse(getComputedStyle(node).backgroundColor);
      if (color) return color;
    }
    return [255, 255, 255];
  };
  const style = getComputedStyle(element);
  if (kind === "text") return contrast(parse(style.color), backdrop(element));
  const border = parse(style.borderTopColor);
  return {
    inner: contrast(border, backdrop(element)),
    outer: contrast(border, backdrop(element.parentElement)),
  };
}

// The contrast of a field's border (WCAG 1.4.11, 3:1 for a boundary that
// identifies a control), with the field left empty and unfocused first.
async function boundaryContrast(page, selector) {
  const field = page.locator(selector).first();
  await field.evaluate((element) => {
    element.value = "";
    element.blur();
  });
  return field.evaluate(pageContrast, "boundary");
}
const isFocused = (locator) =>
  locator.evaluate((el) => el === document.activeElement);
async function openReplay(page) {
  await page.goto(base + replayStory);
  await page.locator(".replay-controls:not([hidden])").waitFor();
}

// WCAG 1.4.12 text spacing: readers may apply these without losing content.
const TEXT_SPACING = `* {
  line-height: 1.5 !important;
  letter-spacing: 0.12em !important;
  word-spacing: 0.16em !important;
}
p { margin-bottom: 2em !important; }`;
const overflows = (page) =>
  page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth + 1,
  );

// One page per route: a single h1, no overflow, visible images, no private
// names, working internal links, and an accessibility audit. On a phone the
// page also keeps to the viewport with the text spacing overrides applied
// (tables scroll inside their own box, so they never widen the page).
async function auditRoutes(page, context, origin, list, width, label) {
  const checked = label === "preview" ? checkedLinks : new Set();
  for (const route of list) {
    const response = await page.goto(origin + route);
    assert.equal(response.status(), 200, route);
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.locator("h1").count(), 1, `One h1: ${route}`);
    assert.equal(await page.locator(".preview-notice").count(), 1);
    assert.equal(
      await overflows(page),
      false,
      `Horizontal overflow at ${width}: ${route}`,
    );
    if (width <= 390) {
      const spacing = await page.addStyleTag({ content: TEXT_SPACING });
      assert.equal(
        await overflows(page),
        false,
        `Horizontal overflow with text spacing at ${width}: ${route}`,
      );
      await spacing.evaluate((style) => style.remove());
    }
    for (const visual of await page
      .locator(".event-visual img, .event-thumbnail img")
      .all()) {
      const wasCollapsed = await visual.evaluate((image) => {
        const details = image.closest("details:not([open])");
        if (!details) return false;
        details.open = true;
        return true;
      });
      assert.equal(
        await visual.isVisible(),
        true,
        `Card image hidden at ${width}: ${route}`,
      );
      await visual.scrollIntoViewIfNeeded();
      await visual.evaluate((image) => image.decode());
      assert.ok(await visual.evaluate((image) => image.naturalWidth > 0));
      if (wasCollapsed)
        await visual.evaluate((image) => {
          image.closest("details").open = false;
        });
    }
    // Every source badge carries its long label as hidden text, since a
    // title on a span that takes no focus reaches neither keyboard nor most
    // screen-reader users.
    for (const badge of await page
      .locator(".source-badge")
      .evaluateAll((elements) =>
        elements.map((element) => ({
          title: element.title,
          hidden: [...element.querySelectorAll(".sr-only")]
            .map((node) => node.textContent)
            .join(""),
        })),
      ))
      assert.ok(
        badge.title && badge.hidden.includes(badge.title),
        `Source badge without its long label as text on ${route}: ${badge.title}`,
      );
    await page.evaluate(() => window.scrollTo(0, 0));
    assert.doesNotMatch(await page.content(), privateText);
    assert.equal(
      await page.locator('a[href=""]').count(),
      0,
      `Empty link: ${route}`,
    );
    for (const url of await page
      .locator('a[href^="/"]')
      .evaluateAll((elements) => elements.map((e) => e.getAttribute("href")))) {
      if (checked.has(url)) continue;
      checked.add(url);
      const result = await context.request.get(origin + url.split("#")[0]);
      assert.equal(result.status(), 200, `Broken link ${url} on ${route}`);
    }
    report.pages.push({
      build: label,
      route,
      width,
      accessibilityViolations: await audit(page, {
        build: label,
        route,
        width,
      }),
    });
  }
}

// A story reads along one axis: its heading, deck, byline, lead figure,
// airframe photo, prose, related stories, and closing link share the left edge
// of the reading column, and the wide data breakout centers on that column.
async function checkReadingAxis(page, origin, route, width) {
  await page.goto(origin + route);
  await page.evaluate(() => document.fonts.ready);
  const axis = await page.evaluate(() => {
    const box = (selector) =>
      document.querySelector(selector)?.getBoundingClientRect();
    const left = (selector) => {
      const rect = box(selector);
      return rect ? Math.round(rect.left) : null;
    };
    const center = (selector) => {
      const rect = box(selector);
      return rect ? Math.round(rect.left + rect.width / 2) : null;
    };
    return {
      h1: left("h1"),
      parts: [
        ".article-deck",
        ".article-byline",
        "figure.article-cover",
        "figure.airframe-photo",
        ".gh-content > p, .gh-content > .story-prose > p",
        ".related-stories",
        ".article-end",
      ].map((selector) => [selector, left(selector)]),
      column: center("h1"),
      wide: center(".gh-content > .kg-width-wide"),
    };
  });
  assert.ok(axis.h1 !== null && axis.wide !== null, route);
  for (const [selector, left] of axis.parts)
    if (left !== null)
      assert.equal(left, axis.h1, `${selector} off the axis at ${width}`);
  assert.equal(axis.wide, axis.column, `Wide breakout off center at ${width}`);
}

async function checkWidth(width) {
  const { context, page, done } = await open({
    viewport: { width, height: 1000 },
    reducedMotion: "reduce",
  });
  await auditRoutes(page, context, base, routes, width, "preview");
  await auditRoutes(page, context, pagedSite.base, pagedRoutes, width, "paged");
  await checkReadingAxis(page, base, replayStory, width);
  if (width !== 320) {
    await auditRoutes(
      page,
      context,
      fixtureSite.base,
      fixtureRoutes,
      width,
      "fixture",
    );
    await checkReadingAxis(page, fixtureSite.base, mappedStory, width);
  }

  await page.goto(base);
  await page.screenshot({
    path: path.join(output, `home-${width}.png`),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Night view" }).click();
  assert.equal(await appearance(page), "night");
  await audit(page, { route: "/", width, appearance: "night" });
  if (width === 1440)
    await page.screenshot({
      path: path.join(output, "home-night.png"),
      fullPage: true,
    });
  await openReplay(page);
  await audit(page, { route: replayStory, width, appearance: "night" });
  // The saved choice is applied before first paint, without the deferred script.
  await page.route("**/assets/js/site.js", (route) => route.abort());
  await page.reload();
  assert.equal(await appearance(page), "night");
  await page.unroute("**/assets/js/site.js");

  // Away from the archive, the header search reads the theme's search index.
  await page.goto(base);
  await page.getByRole("button", { name: "Day view" }).click();
  const search = page.getByRole("button", { name: "Search stories" });
  await search.click();
  await page.locator("#story-search").fill("transponder");
  await page.locator(".search-results a").first().waitFor();
  assert.equal(
    new URL(
      await page.locator(".search-results a").first().getAttribute("href"),
      base,
    ).pathname,
    "/stories/transponder-code-changed-in-three-samples/",
  );
  await page.keyboard.press("Escape");
  // On the archive, the same button opens the journal's search dialog.
  await page.goto(base + "/stories/");
  await page.locator('[data-journal][data-search-ready="true"]').waitFor();
  await search.click();
  await page.locator("[data-keyword-filter]").fill("helicopter");
  await page.getByRole("button", { name: "Show results", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelectorAll("[data-event-id]").length === 1,
  );
  assert.match(
    await page.locator("[data-event-feed]").innerText(),
    /helicopter/i,
  );
  await search.click();
  await page.locator("[data-keyword-filter]").fill("zzzz-no-result");
  await page.getByRole("button", { name: "Show results", exact: true }).click();
  await page.locator(".event-empty").waitFor();
  assert.match(
    await page.locator(".event-empty").innerText(),
    /No matching events/,
  );
  await search.click();
  await page.keyboard.press("Escape");
  await page.waitForFunction(() =>
    document.activeElement?.matches("[data-search-index]"),
  );

  await openReplay(page);
  await page.locator("[data-replay-play]").click();
  assert.equal(await page.locator("[data-replay-time]").innerText(), "00:30");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: path.join(output, `story-${width}.png`),
    fullPage: true,
  });
  await page.locator(".wordmark").click();
  assert.equal(new URL(page.url()).pathname, "/");
  await page.goto(base + "/stories/");
  await page.keyboard.press("Tab");
  assert.equal(await isFocused(page.locator(".skip-link")), true);
  await page.keyboard.press("Enter");
  assert.equal(await isFocused(page.locator("#main")), true);
  report.interactions.push({
    width,
    readingAxis: true,
    appearancePersistence: true,
    search: true,
    searchFocusReturn: true,
    replayReducedMotion: true,
    homeLink: true,
    skipLink: true,
  });
  await done();
}

try {
  await Promise.all([1440, 390, 320].map(checkWidth));
  report.pages.sort(
    (a, b) => b.width - a.width || a.route.localeCompare(b.route),
  );

  const motion = await open({ viewport: { width: 1280, height: 900 } });
  await openReplay(motion.page);
  await motion.page.locator("[data-replay-play]").click();
  await motion.page.waitForFunction(
    () => document.querySelector("[data-replay-time]").textContent !== "00:00",
  );
  const scrubber = motion.page.locator(".replay-scrubber input");
  const status = motion.page.locator("[data-replay-status]");
  // A drag that interrupts playback says so, and keeps saying so as it continues.
  await scrubber.fill("810");
  assert.equal(
    await motion.page.locator("[data-replay-time]").innerText(),
    "13:30",
  );
  assert.equal(await status.textContent(), "Replay paused.");
  await scrubber.fill("1620");
  assert.equal(
    await motion.page.locator("[data-replay-time]").innerText(),
    "27:00",
  );
  assert.equal(await status.textContent(), "Replay paused.");
  assert.doesNotMatch(
    await motion.page.locator(".replay-plane").getAttribute("transform"),
    /rotate\(90\)/,
  );
  // A message from the button goes stale once the reader moves the slider.
  await motion.page.locator("[data-replay-play]").click();
  await motion.page.locator("[data-replay-play]").click();
  assert.match(await status.textContent(), /^Paused at /);
  await scrubber.fill("60");
  assert.equal(await status.textContent(), "");
  report.interactions.push({
    animatedReplay: true,
    scrubPause: true,
    endpointHeading: true,
  });
  await motion.done();

  // Story, hub, and archive templates render every part they promise.
  const parts = await open({ viewport: { width: 1280, height: 900 } });
  const text = (selector) =>
    parts.page
      .locator(selector)
      .evaluateAll((nodes) => nodes.map((node) => node.textContent.trim()));
  await parts.page.goto(
    fixtureSite.base + "/stories/airbus-a380-hl7635-27-sep-2026/",
  );
  assert.deepEqual(await text(".breadcrumbs li"), [
    "Home",
    "Aircraft",
    "Airbus A380-800",
    "HL7635",
  ]);
  assert.deepEqual(await text(".story-eyebrow a"), [
    "Notable aircraft",
    "Airbus A380-800",
  ]);
  // A review copy has no release, so its byline names the observation only.
  const byline = (await text(".article-byline"))[0].replace(/\s+/g, " ");
  assert.match(byline, /Observed 27 Sep 2026, 00:05 UTC/);
  assert.doesNotMatch(byline, /Published|Updated/);
  // The story belongs to the Stories section; only the archive is that page.
  assert.equal(
    await parts.page
      .locator('.site-navigation a[href="/stories/"]')
      .getAttribute("aria-current"),
    "true",
  );
  assert.equal(
    await parts.page
      .locator('.story-prose a[href="/field-guide/#type-match"]')
      .count(),
    1,
  );
  assert.equal(
    await parts.page.locator("figure[data-geographic-replay] img").count(),
    1,
  );
  // The article body carries the owned prose block, with the "About" section
  // and its sources inside it; the theme adds the credited type photo there.
  assert.equal(
    await parts.page.locator(".story-prose[data-story-version] > p").count(),
    2,
  );
  assert.equal(
    await parts.page.locator(".type-background h2").innerText(),
    "About the Airbus A380-800",
  );
  assert.equal(await parts.page.locator(".type-background").count(), 1);
  assert.equal(
    await parts.page.locator(".type-background .type-photo img").count(),
    1,
  );
  // The airframe photo follows the map, credits its photographer beside the
  // image and links to its Planespotters page with plain anchors.
  const mapped = airframeByPath[mappedStory];
  const airframe = parts.page.locator("figure.airframe-photo");
  assert.equal(await airframe.count(), 1);
  assert.deepEqual(
    await airframe
      .locator("a")
      .evaluateAll((links) =>
        links.map((link) => [link.getAttribute("href"), link.rel]),
      ),
    [
      [mapped.link, ""],
      [mapped.link, ""],
    ],
  );
  const airframeImage = airframe.locator("img");
  assert.equal(await airframeImage.getAttribute("src"), mapped.src);
  assert.equal(
    await airframeImage.getAttribute("alt"),
    "Photo of HL7635 on Planespotters.net",
  );
  // The page's Referrer-Policy header governs the photo request; the image
  // sets no policy of its own (R01).
  assert.equal(await airframeImage.getAttribute("referrerpolicy"), null);
  assert.equal(
    await airframe.locator("figcaption").innerText(),
    "HL7635, photographed on another occasion, not during this flight. Photo: Test Photographer / Planespotters.net",
  );
  assert.ok(
    await parts.page.evaluate(() =>
      Boolean(
        document
          .querySelector("figure[data-geographic-replay]")
          .compareDocumentPosition(
            document.querySelector("figure.airframe-photo"),
          ) & Node.DOCUMENT_POSITION_FOLLOWING,
      ),
    ),
  );
  await airframeImage.scrollIntoViewIfNeeded();
  await airframeImage.evaluate((image) => image.decode());
  assert.ok(await airframeImage.evaluate((image) => image.naturalWidth > 0));
  assert.equal(await parts.page.locator(".story-sources li").count(), 1);
  assert.deepEqual(await text(".related-group h3"), [
    "More about HL7635",
    "Other Airbus A380-800 stories",
    "More in Notable aircraft",
  ]);
  assert.ok((await text(".related-hubs a")).includes("HL7635: every story"));
  await parts.page.goto(fixtureSite.base + leadingStory);
  // Without a map the airframe photo leads, and the type photo joins "About
  // the type".
  assert.equal(
    await parts.page.locator("article figure").first().getAttribute("class"),
    "airframe-photo column",
  );
  assert.equal(
    await parts.page.locator("figure.airframe-photo img").getAttribute("src"),
    airframeByPath[leadingStory].src,
  );
  assert.equal(await parts.page.locator("figure.article-cover").count(), 0);
  assert.equal(
    await parts.page.locator(".type-background .type-photo img").count(),
    1,
  );
  assert.equal(
    await parts.page
      .locator('.story-prose a[href="/field-guide/#ads-b"]')
      .count(),
    1,
  );
  await parts.page.goto(fixtureSite.base + "/stories/");
  assert.equal(await parts.page.locator(".archive-entry").count(), 20);
  assert.deepEqual(await text(".breadcrumbs li"), ["Home", "Stories"]);
  assert.equal(
    await parts.page
      .locator('.site-navigation a[href="/stories/"][aria-current="page"]')
      .count(),
    1,
  );
  for (const value of await parts.page
    .locator(".story-day-heading time")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("datetime"))))
    assert.match(value, /^\d{4}-\d{2}-\d{2}$/);
  // Day groups are not landmarks.
  assert.equal(await parts.page.locator("section.story-day").count(), 0);
  assert.equal(
    await parts.page
      .locator('.page-numbers ol a[href="/stories/page/2/"]')
      .count(),
    1,
  );
  await parts.page.goto(fixtureSite.base + "/stories/page/2/");
  assert.equal(
    await parts.page.locator("h1").innerText(),
    "All stories, page 2",
  );
  assert.equal(await parts.page.locator(".archive-entry").count(), 9);
  await parts.page.goto(fixtureSite.base + "/aircraft/airbus-a380/hl7635/");
  assert.equal(
    await parts.page.locator("h1").innerText(),
    "HL7635: Airbus A380-800",
  );
  assert.equal(
    await parts.page.locator('a[href="/operators/asiana-airlines/"]').count(),
    1,
  );
  await parts.page.goto(fixtureSite.base + "/operators/asiana-airlines/");
  assert.match(
    await parts.page.locator(".archive-heading").innerText(),
    /callsign assigned to Asiana Airlines \(designator AAR\)/,
  );
  await parts.page.goto(
    fixtureSite.base + "/stories/c-5m-super-galaxy-87-0043-4-oct-2026/",
  );
  assert.equal(await parts.page.locator(".story-prose").count(), 0);
  assert.deepEqual(await text(".breadcrumbs li"), [
    "Home",
    "Topics",
    "Notable aircraft",
  ]);
  await parts.page.goto(fixtureSite.base + weeklyStory);
  assert.equal(await parts.page.locator(".weekly-research").count(), 1);
  // Source badges: the reception source beside the topic on a story page
  // and in the meta line of every archive entry, home tile, and search
  // card. A badge reads as [the short label a reader sees, the text a screen
  // reader announces in its place, the title shown on hover].
  const badge = (selector) =>
    parts.page
      .locator(selector)
      .first()
      .evaluate((element) => {
        const textWithout = (excluded) => {
          const copy = element.cloneNode(true);
          for (const node of copy.querySelectorAll(excluded)) node.remove();
          return copy.textContent;
        };
        return [
          textWithout(".sr-only"),
          textWithout('[aria-hidden="true"]'),
          element.title,
        ];
      });
  const mlatBadge = [
    "MLAT",
    "Reception source: Multilateration (MLAT)",
    "Multilateration (MLAT)",
  ];
  await parts.page.goto(fixtureSite.base + mlatStory);
  assert.deepEqual(await badge(".story-eyebrow .source-badge"), mlatBadge);
  await parts.page.goto(fixtureSite.base + mappedStory);
  assert.deepEqual(await badge(".story-eyebrow .source-badge"), [
    "ADS-B",
    "Reception source: ADS-B, ICAO address",
    "ADS-B, ICAO address",
  ]);
  await parts.page.goto(fixtureSite.base + "/stories/");
  assert.equal(
    await parts.page.locator(".archive-entry .source-badge").count(),
    await parts.page.locator(".archive-entry").count(),
  );
  assert.deepEqual(
    await badge(`.archive-entry[data-event-id="${mlatId}"] .source-badge`),
    mlatBadge,
  );
  await parts.page.goto(fixtureSite.base + "/");
  assert.equal(
    await parts.page.locator(".story-tile .source-badge").count(),
    await parts.page.locator(".story-tile").count(),
  );
  assert.deepEqual(
    await badge(`.story-tile[data-event-id="${mlatId}"] .source-badge`),
    mlatBadge,
  );
  // Class photos. The newest story's type, the R44, has no photo of its own,
  // so its home tile shows the local helicopter class photo with a visible
  // note; every other tile has a map, chart, type photo, or no type at all.
  // Journal cards show the photo with alt text and a caption that say it is
  // not the aircraft. The story page keeps its summary figure, and the
  // credits page lists the photo among the class photos. The context fails
  // on any request to another origin.
  const classPhoto =
    /^\/assets\/images\/aircraft\/class-helicopter-[0-9a-f]{12}\.webp$/;
  const loaded = async (image) => {
    await image.scrollIntoViewIfNeeded();
    await image.evaluate((element) => element.decode());
    return image.evaluate((element) => element.naturalWidth > 0);
  };
  const classTile = parts.page.locator(
    `.story-tile:has(a[href="${classStory}"])`,
  );
  assert.match(
    await classTile.locator(".card-image img").getAttribute("src"),
    classPhoto,
  );
  assert.ok(await loaded(classTile.locator(".card-image img")));
  assert.equal(
    await classTile.locator(".card-image-note").innerText(),
    "Representative helicopter",
  );
  assert.equal(await parts.page.locator(".card-image-note").count(), 1);
  await parts.page.goto(fixtureSite.base + "/stories/?aircraft=r44");
  await parts.page.locator('[data-event-feed] [data-view="search"]').waitFor();
  const leadCard = parts.page.locator('[data-view="search"] .event-lead');
  const leadImage = leadCard.locator(".event-entry-visual img");
  assert.match(await leadImage.getAttribute("src"), classPhoto);
  assert.match(
    await leadImage.getAttribute("alt"),
    /^Representative helicopter, not the aircraft in this story: /,
  );
  assert.ok(await loaded(leadImage));
  assert.match(
    await leadCard.locator(".event-entry-visual figcaption").innerText(),
    /^Representative helicopter photo \(Robinson R44 shown\)\.\s+Image source and license$/,
  );
  const rowThumbnails = parts.page.locator(
    '[data-view="search"] .event-entry:not(.event-lead) .event-thumbnail',
  );
  assert.ok((await rowThumbnails.count()) > 0);
  for (const thumbnail of await rowThumbnails.all()) {
    assert.match(
      await thumbnail.locator("img").getAttribute("src"),
      classPhoto,
    );
    assert.match(
      await thumbnail.locator("figcaption").innerText(),
      /^Representative helicopter photo/,
    );
  }
  await parts.page.goto(fixtureSite.base + classStory);
  assert.equal(
    await parts.page
      .locator("figure.article-cover")
      .getAttribute("data-media-kind"),
    "observation-summary",
  );
  assert.equal(
    await parts.page.locator('img[src*="/images/aircraft/class-"]').count(),
    0,
  );
  await parts.page.goto(
    fixtureSite.base + "/about/image-credits/#class-helicopter",
  );
  const classCredit = parts.page.locator(
    'section[aria-labelledby="class-photos"] section[aria-labelledby="class-helicopter"]',
  );
  assert.equal(await classCredit.locator("h3").innerText(), "Helicopter");
  assert.match(
    await classCredit.locator("img").getAttribute("src"),
    classPhoto,
  );
  assert.ok(await loaded(classCredit.locator("img")));
  // The journal filters on the source family, from a link and from its
  // select, and names the filter in the summary.
  const searchIds = () =>
    parts.page
      .locator('[data-event-feed] [data-view="search"] [data-event-id]')
      .evaluateAll((elements) =>
        elements.map((element) => element.dataset.eventId),
      );
  await parts.page.goto(fixtureSite.base + "/stories/?source=mlat");
  await parts.page.locator('[data-event-feed] [data-view="search"]').waitFor();
  assert.deepEqual(await searchIds(), [mlatId]);
  assert.match(
    await parts.page.locator("[data-journal-status]").innerText(),
    /^1 matching event\./,
  );
  assert.match(
    await parts.page.locator("[data-filter-summary]").innerText(),
    /MLAT readings/,
  );
  assert.deepEqual(
    await badge('[data-view="search"] .event-card .source-badge'),
    mlatBadge,
  );
  await parts.page.goto(fixtureSite.base + "/stories/");
  await parts.page
    .locator('[data-journal][data-search-ready="true"]')
    .waitFor();
  await parts.page.getByRole("button", { name: "Search stories" }).click();
  await parts.page.locator("[data-source-filter]").selectOption("mlat");
  await parts.page
    .getByRole("button", { name: "Show results", exact: true })
    .click();
  await parts.page.locator('[data-event-feed] [data-view="search"]').waitFor();
  assert.deepEqual(await searchIds(), [mlatId]);
  assert.equal(new URL(parts.page.url()).searchParams.get("source"), "mlat");
  report.interactions.push({ fixtureTemplates: true, sourceBadges: true });
  await parts.done();

  // Paged lists, the story archive and a topic: Previous and Next above and
  // below the list, the numbers below, rel on each step, and on the first and
  // last pages the missing step in place as a disabled link without an
  // address. The journal draws the archive's later pages as the static ones.
  const paging = await open({ viewport: { width: 390, height: 900 } });
  const pagedUrl = (list, number) =>
    number === 1 ? list : `${list}page/${number}/`;
  const navState = (page) =>
    page.locator("main nav.pagination").evaluateAll((navs) =>
      navs.map((nav) => ({
        label: nav.getAttribute("aria-label"),
        steps: [...nav.querySelectorAll(".page-step")].map((step) => [
          step.getAttribute("href"),
          step.getAttribute("rel"),
          step.getAttribute("aria-disabled"),
          step.getAttribute("aria-label"),
        ]),
        status: nav.querySelector(".page-status")?.textContent ?? null,
        current: nav.querySelector("[aria-current]")?.textContent ?? null,
      })),
    );
  async function expectSteps(page, list, number, pages, url = pagedUrl) {
    const navs = await navState(page);
    assert.deepEqual(
      navs.map((nav) => nav.label),
      ["Story pages, top of list", "Story pages"],
      `${list} page ${number}`,
    );
    const step = (target, rel, name) =>
      target < 1 || target > pages
        ? [null, null, "true", name]
        : [url(list, target), rel, null, name];
    for (const nav of navs)
      assert.deepEqual(nav.steps, [
        step(number - 1, "prev", "Previous page (newer stories)"),
        step(number + 1, "next", "Next page (older stories)"),
      ]);
    assert.equal(navs[0].status, `Page ${number} of ${pages}`);
    assert.equal(navs[1].current, String(number));
  }
  const listedIds = (page) =>
    page
      .locator("main [data-event-id]")
      .evaluateAll((elements) =>
        elements.map((element) => element.dataset.eventId),
      );
  const staticIds = {};
  for (const [list, pages] of pagedLists)
    for (let number = 1; number <= pages; number++) {
      await paging.page.goto(pagedSite.base + pagedUrl(list, number));
      await expectSteps(paging.page, list, number, pages);
      staticIds[pagedUrl(list, number)] = await listedIds(paging.page);
    }
  // From the keyboard: Tab passes from Previous to Next, Enter follows it,
  // and a disabled step takes no focus.
  await paging.page.goto(pagedSite.base + "/topics/flight-shapes/page/2/");
  await paging.page.locator("main nav.page-steps .page-prev").focus();
  await paging.page.keyboard.press("Tab");
  assert.equal(
    await isFocused(paging.page.locator("main nav.page-steps .page-next")),
    true,
  );
  await Promise.all([
    paging.page.waitForURL(/\/topics\/flight-shapes\/page\/3\/$/),
    paging.page.keyboard.press("Enter"),
  ]);
  const lastNext = paging.page.locator("main nav.page-numbers .page-next");
  await lastNext.evaluate((element) => element.focus());
  assert.equal(await isFocused(lastNext), false);
  // The journal's archive at the static size shows the static pages' stories,
  // at its own address; a new size keeps the first story shown.
  await paging.page.goto(pagedSite.base + "/stories/");
  await paging.page
    .locator('[data-journal][data-search-ready="true"]')
    .waitFor();
  assert.equal(await paging.page.locator("[data-page-size]").isVisible(), true);
  assert.equal(
    await paging.page.locator(".page-size-field label").innerText(),
    "Stories per page",
  );
  const archiveSettled = () =>
    paging.page.waitForFunction(
      () =>
        !document.querySelector("[data-event-feed]").hasAttribute("aria-busy"),
    );
  for (const number of [2, 3]) {
    await paging.page
      .locator('[data-event-feed] nav.page-steps [data-event-page="next"]')
      .click();
    await archiveSettled();
    assert.equal(
      new URL(paging.page.url()).search,
      `?page=${number}`,
      "Archive page address",
    );
    assert.deepEqual(
      await listedIds(paging.page),
      staticIds[pagedUrl("/stories/", number)],
    );
    // The journal's own page links stay on the archive's address.
    await expectSteps(paging.page, "/stories/", number, 3, (list, target) =>
      target === 1 ? list : `${list}?page=${target}`,
    );
  }
  await paging.page.locator("[data-page-size]").selectOption("10");
  await archiveSettled();
  assert.equal(new URL(paging.page.url()).search, "?size=10&page=5");
  assert.deepEqual(
    await listedIds(paging.page),
    staticIds["/stories/page/3/"].slice(0, 10),
  );
  // The all-history query page (view key "archive") pages as a query, with
  // event cards, never as the story archive.
  const { pageBase } = JSON.parse(
    await paging.page.locator("[data-journal-config]").textContent(),
  );
  await paging.page.goto(pagedSite.base + pageBase + "archive/1/");
  await paging.page
    .locator('[data-journal][data-search-ready="true"]')
    .waitFor();
  await paging.page
    .locator('[data-event-feed] nav.page-steps [data-event-page="next"]')
    .click();
  await paging.page.locator('[data-event-feed] [data-view="search"]').waitFor();
  await archiveSettled();
  const queryPage = new URL(paging.page.url());
  assert.equal(queryPage.pathname, pageBase + "all/1/");
  assert.equal(queryPage.searchParams.get("page"), "2");
  assert.equal(
    await paging.page.locator("[data-event-feed] .archive-entry").count(),
    0,
  );
  assert.equal(
    await paging.page.locator("[data-event-feed] article.event-card").count(),
    20,
  );
  await paging.done();
  // Without JavaScript the static pages step through the list, and the size
  // picker, which needs the script, stays hidden.
  const staticPaging = await open({
    javaScriptEnabled: false,
    viewport: { width: 390, height: 900 },
  });
  await staticPaging.page.goto(pagedSite.base + "/stories/");
  assert.equal(
    await staticPaging.page.locator("[data-page-size]").isVisible(),
    false,
  );
  for (const [selector, target] of [
    ["nav.page-steps .page-next", "/stories/page/2/"],
    ["nav.page-numbers .page-next", "/stories/page/3/"],
    ["nav.page-numbers .page-prev", "/stories/page/2/"],
  ]) {
    await Promise.all([
      staticPaging.page.waitForURL(pagedSite.base + target),
      staticPaging.page.locator(`main ${selector}`).click(),
    ]);
    assert.deepEqual(
      await listedIds(staticPaging.page),
      staticIds[target],
      target,
    );
  }
  await staticPaging.done();
  // The steps in both appearances: text of at least 4.5:1, a disabled step's
  // muted text too, an enabled step's outline at least 3:1, and 44-pixel
  // targets on a phone.
  const stepLooks = await open({ viewport: { width: 390, height: 900 } });
  const looks = [];
  for (const mode of ["paper", "night"]) {
    // The last page: an enabled Previous and a disabled Next.
    await stepLooks.page.goto(pagedSite.base + "/stories/page/3/");
    if (mode === "night")
      await stepLooks.page.getByRole("button", { name: "Night view" }).click();
    assert.equal(await appearance(stepLooks.page), mode);
    for (const selector of [
      "nav.page-steps .page-prev",
      "nav.page-steps .page-status",
      "nav.page-numbers .page-next",
      "nav.page-numbers [aria-current]",
      "nav.page-numbers ol a",
    ]) {
      const element = stepLooks.page.locator(`main ${selector}`).first();
      const text = await element.evaluate(pageContrast, "text");
      assert.ok(text >= 4.5, `${selector} text in ${mode}: ${text.toFixed(2)}`);
      looks.push({ mode, selector, text: Number(text.toFixed(2)) });
    }
    const { inner, outer } = await stepLooks.page
      .locator("main nav.page-steps .page-prev")
      .evaluate(pageContrast, "boundary");
    assert.ok(
      inner >= 3 && outer >= 3,
      `Step outline in ${mode}: ${inner.toFixed(2)}, ${outer.toFixed(2)}`,
    );
    for (const box of await stepLooks.page
      .locator("main nav.pagination :is(.page-step, ol a, [aria-current])")
      .evaluateAll((elements) =>
        elements.map((element) => element.getBoundingClientRect()),
      ))
      assert.ok(box.width >= 44 && box.height >= 44, JSON.stringify(box));
  }
  report.interactions.push({ pagedLists: true, stepContrast: looks });
  await stepLooks.done();

  // Empty, unfocused text fields and selects show a boundary of at least
  // 3:1 in both appearances: the header search box, the journal's keyword
  // field and its page-size select.
  const fields = await open({ viewport: { width: 1280, height: 900 } });
  const boundaries = [];
  const tileBadges = [];
  for (const mode of ["paper", "night"]) {
    await fields.page.goto(base);
    if (mode === "night")
      await fields.page.getByRole("button", { name: "Night view" }).click();
    assert.equal(await appearance(fields.page), mode);
    // The source badges on the home page's story tiles: small text, so at
    // least 4.5:1 against the tile, and in the badge's own weight (600 in
    // screen.css), which the tile's meta line must not lighten.
    const badges = await fields.page.locator(".story-tile .source-badge").all();
    assert.ok(badges.length > 0, "Home tiles have source badges");
    for (const tileBadge of badges) {
      tileBadges.push([mode, await tileBadge.evaluate(pageContrast, "text")]);
      assert.equal(
        await tileBadge.evaluate(
          (element) => getComputedStyle(element).fontWeight,
        ),
        "600",
        `Tile source badge weight in ${mode}`,
      );
    }
    await fields.page.getByRole("button", { name: "Search stories" }).click();
    boundaries.push([
      mode,
      "#story-search",
      await boundaryContrast(fields.page, "#story-search"),
    ]);
    await fields.page.keyboard.press("Escape");
    await fields.page.goto(base + "/stories/");
    await fields.page
      .locator('[data-journal][data-search-ready="true"]')
      .waitFor();
    assert.equal(await appearance(fields.page), mode);
    boundaries.push([
      mode,
      "[data-page-size]",
      await boundaryContrast(
        fields.page,
        ".page-size-field:not([hidden]) [data-page-size]",
      ),
    ]);
    await fields.page.getByRole("button", { name: "Search stories" }).click();
    boundaries.push([
      mode,
      "[data-keyword-filter]",
      await boundaryContrast(fields.page, "[data-keyword-filter]"),
    ]);
    await fields.page.keyboard.press("Escape");
  }
  for (const [mode, selector, { inner, outer }] of boundaries)
    assert.ok(
      inner >= 3 && outer >= 3,
      `${selector} boundary contrast in ${mode}: ${inner.toFixed(2)} inside, ${outer.toFixed(2)} outside`,
    );
  for (const [mode, ratio] of tileBadges)
    assert.ok(
      ratio >= 4.5,
      `Tile source badge text contrast in ${mode}: ${ratio.toFixed(2)}`,
    );
  report.interactions.push({
    fieldBoundaries: boundaries.map(([mode, selector, ratio]) => ({
      mode,
      selector,
      inner: Number(ratio.inner.toFixed(2)),
      outer: Number(ratio.outer.toFixed(2)),
    })),
    tileBadgeContrast: Object.fromEntries(
      ["paper", "night"].map((mode) => [
        mode,
        Number(
          Math.min(
            ...tileBadges
              .filter(([badgeMode]) => badgeMode === mode)
              .map(([, ratio]) => ratio),
          ).toFixed(2),
        ),
      ]),
    ),
  });
  await fields.done();

  // The story archive is a state of its own: after a search, Back brings
  // every story back with the archive's heading, pagination, chart and
  // status, not the default 30-day query, and Forward and reload return the
  // search. The older snapshot has a story outside that window.
  const history = await open({ viewport: { width: 1280, height: 900 } });
  const journalState = () =>
    history.page.evaluate(() => ({
      view: document.querySelector("[data-event-feed] > section")?.dataset.view,
      ids: [...document.querySelectorAll("[data-event-feed] [data-event-id]")]
        .map((element) => element.dataset.eventId)
        .sort(),
      heading: document.querySelector("#feed-heading").textContent,
      result: document.querySelector("#feed-heading + p").textContent,
      pagination:
        document.querySelector("[data-event-feed] nav")?.textContent ?? "",
      days: document.querySelectorAll("[data-day]").length,
      chartRange: document.querySelector("[data-chart-range]").textContent,
      status: document.querySelector("[data-journal-status]").textContent,
      url: location.pathname + location.search,
    }));
  const ready = () =>
    history.page.locator('[data-journal][data-search-ready="true"]').waitFor();
  const archiveShown = async () => {
    await history.page
      .locator('[data-event-feed] [data-view="archive"]')
      .waitFor();
    await ready();
  };
  const emptyShown = () => history.page.locator(".event-empty").waitFor();
  await history.page.goto(olderSite.base + "/stories/");
  await ready();
  const archiveState = await journalState();
  assert.equal(archiveState.view, "archive");
  assert.equal(archiveState.ids.length, 14);
  assert.equal(archiveState.url, "/stories/");
  const searchStories = history.page.getByRole("button", {
    name: "Search stories",
  });
  await searchStories.click();
  await history.page.locator("[data-keyword-filter]").fill("no-such-plane");
  await history.page
    .getByRole("button", { name: "Show results", exact: true })
    .click();
  await emptyShown();
  const searchState = await journalState();
  assert.equal(searchState.view, "search");
  assert.deepEqual(searchState.ids, []);
  assert.match(searchState.url, /\?q=no-such-plane/);
  await history.page.goBack();
  await archiveShown();
  assert.deepEqual(await journalState(), archiveState);
  await history.page.goForward();
  await emptyShown();
  assert.deepEqual(await journalState(), searchState);
  await history.page.reload();
  await ready();
  await emptyShown();
  assert.deepEqual(await journalState(), searchState);
  await history.page.goBack();
  await archiveShown();
  assert.deepEqual(await journalState(), archiveState);
  await history.page.reload();
  await ready();
  assert.deepEqual(await journalState(), archiveState);
  // The default query alone would drop the oldest story.
  await searchStories.click();
  await history.page
    .getByRole("button", { name: "Show results", exact: true })
    .click();
  await history.page
    .locator('[data-event-feed] [data-view="search"]')
    .waitFor();
  assert.match((await journalState()).status, /^13 matching events\./);
  await history.page.goBack();
  await archiveShown();
  assert.deepEqual(await journalState(), archiveState);
  // Shared links often carry parameters the journal does not own, such as a
  // click identifier or a campaign tag. Only the journal's filter keys make a
  // query, so such a link opens the archive, on load and on Back.
  const settled = () =>
    history.page.waitForFunction(
      () =>
        document.querySelector('[data-journal][data-search-ready="true"]') &&
        !document.querySelector("[data-event-feed]").hasAttribute("aria-busy"),
    );
  for (const foreign of ["?fbclid=abc", "?utm_source=x"]) {
    const address = "/stories/" + foreign;
    await history.page.goto(olderSite.base + address);
    await settled();
    assert.deepEqual(await journalState(), { ...archiveState, url: address });
    await searchStories.click();
    await history.page.locator("[data-keyword-filter]").fill("no-such-plane");
    await history.page
      .getByRole("button", { name: "Show results", exact: true })
      .click();
    await emptyShown();
    await history.page.goBack();
    await settled();
    assert.deepEqual(await journalState(), { ...archiveState, url: address });
  }
  report.interactions.push({ archiveHistory: true, foreignParameters: true });
  await history.done();

  const noJs = await open({
    javaScriptEnabled: false,
    viewport: { width: 390, height: 900 },
  });
  await noJs.page.goto(base + replayStory);
  assert.equal(await noJs.page.locator(".replay-controls").isVisible(), false);
  assert.equal(await noJs.page.locator("noscript").isVisible(), true);
  // Controls that need the script are not offered without it.
  assert.equal(
    await noJs.page.locator(".header-controls button:visible").count(),
    0,
  );
  report.interactions.push({ noJavaScriptReadable: true });
  await noJs.done();

  // Every airframe photo request of this run left the private preview's
  // origin out: the server's no-referrer policy reached the image.
  assert.ok(photoReferers.length >= 2, "Airframe photos were requested");
  assert.deepEqual(
    photoReferers.filter((referer) => referer !== null),
    [],
    "Airframe photo requests sent a Referer",
  );
  report.interactions.push({ airframePhotoReferer: "none" });

  assert.deepEqual(
    report.issues,
    [],
    "Accessibility issues: see artifacts/ghost-theme/browser-checks.json",
  );
  console.log(
    `Browser checks passed: ${report.pages.length} page/viewport combinations; paper/night, search, replay, paged lists, privacy, and no-JavaScript checks.`,
  );
} finally {
  await fs.writeFile(
    path.join(output, "browser-checks.json"),
    JSON.stringify(report, null, 2),
  );
  await close();
  await fixtureSite.close();
  await olderSite.close();
  await pagedSite.close();
  await fs.rm(fixtureDirectory, { recursive: true, force: true });
  await fs.rm(scratch, { recursive: true, force: true });
}
