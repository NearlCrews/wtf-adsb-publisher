import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import Handlebars from "handlebars";
import AxeBuilder from "@axe-core/playwright";
import { startPreview } from "./browser.mjs";
import { output, theme } from "./paths.mjs";

const { browser, base, close } = await startPreview();
const h = Handlebars.create();
h.registerPartial(
  "geographic-replay",
  await fs.readFile(path.join(theme, "partials/geographic-replay.hbs"), "utf8"),
);
const render = h.compile(
  await fs.readFile(path.join(theme, "partials/event-visual.hbs"), "utf8"),
);
const hash = (text) => createHash("sha256").update(text).digest("hex");
const image =
  '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800"><rect width="1200" height="800" fill="#f7f6f0"/><path d="M180 260L200 280L225 295M450 300L475 320" fill="none" stroke="#20312d" stroke-width="7"/></svg>';
const imagePath = `/event-media/${hash(image)}.svg`;
const fixture = () => ({
  version: 1,
  kind: "geographic",
  event_id: "00000000-0000-4000-8000-000000000001",
  evidence_version: "b".repeat(64),
  image: imagePath,
  started_at: "2026-10-03T10:00:00Z",
  width: 1200,
  height: 800,
  points: [
    {
      t: 0,
      x: 180,
      y: 260,
      distance_nm: 0,
      speed: 60,
      altitude: 1200,
      altitude_reference: "unspecified",
    },
    {
      t: 10,
      x: 200,
      y: 280,
      distance_nm: 0.17,
      speed: 61,
      altitude: 1250,
      altitude_reference: "barometric",
    },
    {
      t: 20,
      x: 225,
      y: 295,
      distance_nm: 0.35,
      speed: 62,
      altitude: 1300,
      altitude_reference: "geometric",
    },
    {
      t: 100,
      x: 450,
      y: 300,
      distance_nm: 0.35,
      speed: null,
      altitude: null,
      altitude_reference: null,
      break: true,
    },
    {
      t: 110.5,
      x: 475,
      y: 320,
      distance_nm: 0.53,
      speed: 65,
      altitude: 1400,
      altitude_reference: "unspecified",
    },
  ],
});
const report = [];

async function open({
  scenario = "valid",
  width = 390,
  reducedMotion = "no-preference",
  javaScriptEnabled = true,
  disclosure = false,
  holdReplay = false,
} = {}) {
  const context = await browser.newContext({
    viewport: { width, height: 844 },
    reducedMotion,
    javaScriptEnabled,
  });
  const data = fixture();
  if (scenario === "binding")
    data.event_id = "00000000-0000-4000-8000-000000000002";
  if (scenario === "gap-distance") data.points[3].distance_nm = 0.4;
  let body = JSON.stringify(data);
  const replayPath = `/event-media/${hash(body)}.json`;
  if (scenario === "changed-asset") body += " ";
  if (scenario === "oversized") body += " ".repeat(200001);
  const media = {
    src: imagePath,
    alt: "Recorded aircraft positions with a gap in the middle.",
    caption: "Selected saved positions. The gap remains visible.",
    width: 1200,
    height: 800,
    kind: "track",
    event_id: fixture().event_id,
    evidence_version: data.evidence_version,
    replaySrc:
      scenario === "external" ? `https://example.com${replayPath}` : replayPath,
    article: true,
    lead: true,
  };
  const figure = render(media);
  const content = disclosure
    ? `<details class="event-expanded"><summary>Show observation details</summary><details open><summary>Recorded track</summary>${figure}</details></details>`
    : figure;
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let requested = 0;
  let redirected = false;
  let releaseReplay;
  const replayReady = new Promise((resolve) => {
    releaseReplay = resolve;
  });
  await context.route("**/assets/js/geographic-*", async (route) => {
    const filename = path.basename(new URL(route.request().url()).pathname);
    await route.fulfill({
      contentType: "text/javascript",
      body: await fs.readFile(path.join(theme, "assets/js", filename), "utf8"),
    });
  });
  await context.route("**/assets/css/geographic-replay.css", async (route) => {
    await route.fulfill({
      contentType: "text/css",
      body: await fs.readFile(
        path.join(theme, "assets/css/geographic-replay.css"),
        "utf8",
      ),
    });
  });
  await context.route(`**${imagePath}`, (route) =>
    route.fulfill({ contentType: "image/svg+xml", body: image }),
  );
  await context.route("**/redirected-geographic-replay.json", (route) => {
    redirected = true;
    return route.fulfill({ contentType: "application/json", body });
  });
  await context.route(`**${replayPath}`, async (route) => {
    requested += 1;
    if (holdReplay) await replayReady;
    if (scenario === "redirect")
      return route.fulfill({
        status: 302,
        headers: { location: "/redirected-geographic-replay.json" },
      });
    if (scenario === "retry" && requested === 1)
      return route.fulfill({ status: 503 });
    return route.fulfill({ contentType: "application/json", body });
  });
  await context.route("**/geographic-test/", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Recorded track replay</title><link rel="stylesheet" href="/assets/css/screen.css"><link rel="stylesheet" href="/assets/css/geographic-replay.css"><script src="/assets/js/geographic-replay.js" defer></script></head><body><main><h1 class="wrap">Recorded track replay</h1>${content}<div style="height:1800px" aria-hidden="true"></div><p>End of test page.</p></main></body></html>`,
    }),
  );
  await page.goto(base + "/geographic-test/");
  const replay = page.locator("[data-geographic-replay]").first();
  const play = replay.locator("[data-geographic-play]");
  const range = replay.locator("[data-geographic-position]");
  return {
    page,
    context,
    replay,
    play,
    range,
    figure,
    releaseReplay,
    count: () => requested,
    redirected: () => redirected,
    async done() {
      releaseReplay();
      assert.deepEqual(errors, []);
      await context.close();
    },
  };
}

async function load(test) {
  await test.play.click();
  await test.range.waitFor({ state: "visible" });
}

try {
  const test = await open();
  const { page, replay, play, range } = test;
  assert.equal(test.count(), 0, "Replay JSON must not load on page entry");
  assert.equal(await range.isVisible(), false);
  await load(test);
  assert.equal(test.count(), 1);
  assert.equal(await range.inputValue(), "0");
  assert.equal(await play.textContent(), "Play replay");
  await page.waitForTimeout(100);
  assert.equal(await range.inputValue(), "0", "Loading must not start motion");
  assert.equal(
    await play.evaluate((node) => node === document.activeElement),
    true,
  );
  await range.fill("5");
  assert.equal(
    await replay.locator(".geographic-replay-marker").getAttribute("cx"),
    "190",
  );
  assert.equal(
    await replay.locator("[data-geographic-groundspeed]").textContent(),
    "60 kn",
  );
  assert.equal(
    await replay.locator("[data-geographic-distance]").textContent(),
    "≈ 0.0 nmi",
  );
  assert.match(
    await replay.locator("[data-geographic-report]").textContent(),
    /10:00:00$/,
  );
  assert.match(
    await replay.locator("[data-geographic-time]").textContent(),
    /10:00:05$/,
  );
  await range.fill("50");
  assert.equal(
    await replay.locator(".geographic-replay-marker").isVisible(),
    false,
  );
  for (const name of ["report", "distance", "groundspeed", "altitude"])
    assert.equal(
      await replay.locator(`[data-geographic-${name}]`).textContent(),
      "Not recorded",
    );
  assert.match(
    await replay.locator("[data-geographic-status]").textContent(),
    /^Data gap/,
  );
  await range.fill("100");
  assert.equal(
    await replay.locator(".geographic-replay-marker").isVisible(),
    true,
  );
  assert.equal(
    await replay.locator("[data-geographic-groundspeed]").textContent(),
    "Not recorded",
  );
  await range.focus();
  await page.keyboard.press("End");
  assert.equal(await range.inputValue(), "110.5");
  assert.equal(
    await replay.locator("[data-geographic-groundspeed]").textContent(),
    "65 kn",
  );
  const flown = await replay
    .locator(".geographic-replay-flown")
    .getAttribute("d");
  assert.equal((flown.match(/M/g) || []).length, 2);
  await page.keyboard.press("Home");
  assert.equal(await range.inputValue(), "0");
  await replay.locator("[data-geographic-speed]").selectOption("60");
  await play.click();
  await page.waitForFunction(
    () =>
      Number(document.querySelector("[data-geographic-position]").value) > 1,
  );
  await play.click();
  const paused = await range.inputValue();
  await page.waitForTimeout(100);
  assert.equal(await range.inputValue(), paused);
  await play.click();
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForFunction(
    () =>
      document.querySelector("[data-geographic-play]").textContent ===
      "Play replay",
  );
  await play.click();
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  assert.equal(await play.textContent(), "Play replay");
  await page.evaluate(() => {
    delete document.hidden;
  });
  await page.evaluate(
    (figure) =>
      document.querySelector("main").insertAdjacentHTML("afterbegin", figure),
    test.figure,
  );
  const dynamic = page.locator("[data-geographic-replay]").first();
  await dynamic.locator("[data-geographic-play]").waitFor({ state: "visible" });
  assert.equal(test.count(), 1, "Dynamic cards also load only on request");
  await dynamic.locator("[data-geographic-play]").click();
  await dynamic
    .locator("[data-geographic-position]")
    .waitFor({ state: "visible" });
  assert.equal(test.count(), 2);
  await dynamic.locator("[data-geographic-play]").click();
  await dynamic.evaluate((node) => node.remove());
  await page.waitForTimeout(100);
  await test.done();
  report.push({
    scenario:
      "playback, gaps, readings, keyboard, offscreen, hidden, dynamic cards",
    passed: true,
  });

  const collapsed = await open({ disclosure: true });
  const summary = collapsed.page.locator(".event-expanded > summary");
  assert.equal(collapsed.count(), 0);
  assert.equal(await collapsed.play.isVisible(), false);
  await summary.focus();
  await collapsed.page.keyboard.press("Enter");
  assert.equal(await collapsed.replay.locator("img").isVisible(), true);
  assert.equal(
    collapsed.count(),
    0,
    "Opening details must not fetch replay data",
  );
  await load(collapsed);
  await collapsed.play.click();
  await collapsed.page.waitForFunction(
    () =>
      Number(document.querySelector("[data-geographic-position]").value) > 1,
  );
  await summary.click();
  await collapsed.page.waitForFunction(
    () =>
      document.querySelector("[data-geographic-play]").textContent ===
      "Play replay",
  );
  assert.equal(await collapsed.replay.locator("img").isVisible(), false);
  const collapsedPosition = await collapsed.range.inputValue();
  // A queued activation must not restart a replay inside a closed ancestor.
  await collapsed.play.evaluate((button) => button.click());
  await collapsed.page.waitForTimeout(100);
  assert.equal(await collapsed.range.inputValue(), collapsedPosition);
  assert.equal(await collapsed.play.textContent(), "Play replay");
  await summary.click();
  await collapsed.page.waitForTimeout(100);
  assert.equal(await collapsed.range.inputValue(), collapsedPosition);
  assert.equal(await collapsed.play.textContent(), "Play replay");
  assert.equal(
    collapsed.count(),
    1,
    "Reopening details must reuse loaded data",
  );
  assert.equal(
    await collapsed.replay.locator(".geographic-replay-overlay").count(),
    1,
  );
  await collapsed.done();
  report.push({
    scenario: "native nested details pause and reopen without hidden motion",
    passed: true,
  });

  for (const scenario of ["valid", "retry"]) {
    const delayed = await open({
      disclosure: true,
      holdReplay: true,
      scenario,
    });
    const summary = delayed.page.locator(".event-expanded > summary");
    await summary.click();
    const requested = delayed.page.waitForRequest(
      /\/event-media\/[a-f0-9]{64}\.json$/,
    );
    await delayed.play.click();
    await requested;
    assert.equal(await delayed.play.textContent(), "Loading…");
    await summary.click();
    delayed.releaseReplay();
    const expected = scenario === "valid" ? "Play replay" : "Retry replay";
    await delayed.page.waitForFunction(
      (label) =>
        document.querySelector("[data-geographic-play]").textContent === label,
      expected,
    );
    assert.equal(await delayed.play.isVisible(), false);
    assert.equal(
      await delayed.replay
        .locator("[data-geographic-controls]")
        .getAttribute("aria-busy"),
      null,
    );
    await summary.click();
    assert.equal(await delayed.play.textContent(), expected);
    if (scenario === "retry") await load(delayed);
    assert.equal(await delayed.range.inputValue(), "0");
    await delayed.page.waitForTimeout(100);
    assert.equal(await delayed.range.inputValue(), "0");
    assert.equal(await delayed.play.textContent(), "Play replay");
    assert.equal(delayed.count(), scenario === "valid" ? 1 : 2);
    await delayed.done();
    report.push({
      scenario: `details closed during ${scenario === "valid" ? "successful" : "failed"} replay load`,
      passed: true,
    });
  }

  for (const scenario of [
    "binding",
    "gap-distance",
    "changed-asset",
    "oversized",
    "redirect",
    "external",
    "retry",
  ]) {
    const failed = await open({ scenario });
    await failed.play.click();
    await failed.page.waitForFunction(
      () =>
        document.querySelector("[data-geographic-play]").textContent ===
        "Retry replay",
    );
    assert.equal(await failed.range.isVisible(), false);
    assert.equal(await failed.replay.locator("img").isVisible(), true);
    assert.equal(
      await failed.replay.locator(".geographic-replay-overlay").count(),
      0,
    );
    assert.equal(failed.redirected(), false);
    if (scenario === "external") assert.equal(failed.count(), 0);
    if (scenario === "retry") {
      await load(failed);
      assert.equal(failed.count(), 2);
    }
    await failed.done();
    report.push({ scenario, passed: true });
  }

  for (const width of [320, 390, 844, 1440]) {
    const mobile = await open({ width, reducedMotion: "reduce" });
    await load(mobile);
    assert.equal(await mobile.play.textContent(), "Advance 30s");
    assert.equal(
      await mobile.replay.locator("[data-geographic-speed]").isVisible(),
      false,
    );
    await mobile.play.click();
    assert.equal(await mobile.range.inputValue(), "30");
    await mobile.page.waitForTimeout(100);
    assert.equal(await mobile.range.inputValue(), "30");
    const overflow = await mobile.page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 1,
    );
    assert.equal(overflow, false, `Replay overflow at ${width}px`);
    const { violations } = await new AxeBuilder({ page: mobile.page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa", "best-practice"])
      .analyze();
    assert.deepEqual(
      violations.map((entry) => ({
        id: entry.id,
        nodes: entry.nodes.map((node) => node.target),
      })),
      [],
    );
    await mobile.done();
    report.push({
      scenario: `reduced motion, layout, and accessibility at ${width}px`,
      passed: true,
    });
  }
  const staticPage = await open({ javaScriptEnabled: false });
  assert.equal(await staticPage.play.isVisible(), false);
  assert.equal(await staticPage.replay.locator("img").isVisible(), true);
  assert.equal(await staticPage.replay.locator("figcaption").isVisible(), true);
  assert.equal(staticPage.count(), 0);
  await staticPage.done();
  report.push({ scenario: "no JavaScript static fallback", passed: true });
  const staticDetails = await open({
    javaScriptEnabled: false,
    disclosure: true,
  });
  const staticSummary = staticDetails.page.locator(".event-expanded > summary");
  assert.equal(await staticDetails.replay.locator("img").isVisible(), false);
  await staticSummary.focus();
  await staticDetails.page.keyboard.press("Enter");
  assert.equal(await staticDetails.replay.locator("img").isVisible(), true);
  assert.equal(
    await staticDetails.replay.locator("figcaption").isVisible(),
    true,
  );
  assert.equal(await staticDetails.play.isVisible(), false);
  assert.equal(staticDetails.count(), 0);
  await staticDetails.done();
  report.push({
    scenario: "no JavaScript native disclosure fallback",
    passed: true,
  });
  await fs.writeFile(
    path.join(output, "geographic-replay-checks.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(
    "Geographic replay checks passed: lazy loading, gap semantics, keyboard controls, lifecycle, static fallback, and accessible responsive layouts.",
  );
} finally {
  await close();
}
