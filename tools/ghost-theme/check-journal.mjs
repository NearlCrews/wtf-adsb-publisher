import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { startPreview } from "./browser.mjs";
import { stories } from "./demo-content.mjs";
import { createJournal, exampleSnapshot } from "./journal.mjs";
import {
  DEFAULT_PAGE_SIZE,
  pageNumbers,
} from "../../themes/wtf-adsb/assets/js/event-query.mjs";
import { preview } from "./paths.mjs";
import "./check-event-query.mjs";

const snapshot = exampleSnapshot(stories);
const journal = createJournal(snapshot);
// Rate stories should lead with the vertical evidence, even when ground speed
// appears first in the saved report measurements.
for (const kind of ["rapid_climb", "rapid_descent"]) {
  const rateSnapshot = exampleSnapshot([
    {
      ...stories[0],
      event_type: "performance",
      measurements: [
        {
          key: "gs",
          label: "Ground speed",
          value: 250,
          unit: "kn",
          scope: "saved-report",
        },
        {
          key: "peak_rate_fpm",
          label: "Peak signed vertical rate",
          value: 8000,
          unit: "ft/min",
          scope: "analysis-window",
        },
        {
          key: "altitude_change_ft",
          label: "Observed altitude change",
          value: 4000,
          unit: "ft",
          scope: "analysis-window",
        },
        {
          key: "duration_seconds",
          label: "Episode duration",
          value: 30,
          unit: "s",
          scope: "analysis-window",
        },
      ],
    },
  ]);
  rateSnapshot.events[0].kind = kind;
  const lead = createJournal(rateSnapshot).context().feed.lead;
  assert.deepEqual(
    lead.details.measurementGroups.flatMap((group) =>
      group.rows.map((m) => m.label),
    ),
    [
      "Peak signed vertical rate",
      "Observed altitude change",
      "Episode duration",
    ],
  );
}
// Archived labels, rules, other aircraft, and watchlist sources keep their own
// presentation and never become key facts by accident.
{
  const extra = exampleSnapshot([stories[0]]);
  Object.assign(extra.events[0], {
    kind: "formation_flight",
    measurements: [
      {
        key: "squawk",
        label: "Transponder code",
        value: "4102",
        unit: "",
        scope: "archived-label",
      },
      {
        key: "callsign",
        label: "Callsign",
        value: "RCH2023",
        unit: "",
        scope: "archived-label",
      },
      {
        key: "threshold_fpm",
        label: "Detection threshold (magnitude)",
        value: 4500,
        unit: "ft/min",
        scope: "detection-rule",
      },
      {
        key: "peak_rate_fpm",
        label: "Peak signed vertical rate",
        value: -7600,
        unit: "ft/min",
        scope: "peak-report",
        at_utc: "2026-10-01T12:00:00Z",
      },
    ],
    participants: [
      {
        role: "wingman",
        icao_address: "A22222",
        registration: "N22222",
        source: "adsb_icao",
        measurements: [
          {
            key: "min_separation_nm",
            label: "Closest horizontal separation between the aircraft",
            value: 0.3,
            unit: "NM",
          },
        ],
      },
    ],
    watchlist: {
      source_name: "Plane Alert DB",
      category: "Distinctive aircraft",
      source_commit: "c".repeat(40),
      attribution: "Aircraft association/category: Plane Alert DB.",
      licenses: ["Open Database License 1.0", "Database Contents License 1.0"],
      claim_scope: "Source association only.",
    },
  });
  const { details } = createJournal(extra).context().feed.lead;
  assert.deepEqual(details.participants, [
    {
      label: "Wingman",
      value:
        "N22222; ICAO address A22222; Closest horizontal separation between the aircraft: 0.3 NM",
    },
  ]);
  assert.ok(
    details.notes.some((note) =>
      note.startsWith("Watchlist association source: Plane Alert DB"),
    ),
  );
  assert.deepEqual(
    details.measurementGroups.flatMap((group) => group.rows.map((m) => m.key)),
    ["peak_rate_fpm"],
  );
  assert.deepEqual(
    details.allMeasurementGroups.map((group) => group.scope),
    [
      "Last archived label; carried between saved positions",
      "Detection rule; a site setting, not a measurement",
      "Peak report",
    ],
  );
}
// A transponder episode leads with its own code facts, without repeating the code.
{
  const window = {
    unit: "",
    scope: "analysis-window",
    start_utc: "2026-10-01T11:59:00Z",
    end_utc: "2026-10-01T12:01:00Z",
  };
  const extra = exampleSnapshot([
    {
      ...stories[0],
      event_type: "transponder-reports",
      measurements: [
        {
          key: "squawk",
          label: "Transponder code",
          value: "7700",
          unit: "",
          scope: "saved-report",
        },
        {
          key: "advisory_complement",
          label: "Advisory complement",
          value: "Do not pass below",
          ...window,
        },
        { key: "code", label: "Transponder code", value: "7700", ...window },
        {
          key: "advisory",
          label: "Resolution advisory text",
          value: "Climb",
          ...window,
        },
      ],
    },
  ]);
  extra.events[0].kind = "tcas_ra";
  const { details } = createJournal(extra).context().feed.lead;
  assert.deepEqual(
    details.measurementGroups.flatMap((group) => group.rows.map((m) => m.key)),
    ["code", "advisory", "advisory_complement"],
  );
}
const { browser, base, close } = await startPreview();
// The interactive journal sits on the story archive; its first feed is the
// plain, crawlable list of every story.
const archive = `${base}/stories/`;
const errors = [];
const idle = async (page) => {
  await page.locator("[data-open-filters]").waitFor();
  await page.waitForFunction(
    () =>
      !document.querySelector("[data-event-feed]").hasAttribute("aria-busy") &&
      !document
        .querySelector("[data-journal-status]")
        .textContent.includes("Searching"),
  );
};
async function open(options = {}) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 900 },
    ...options,
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(archive);
  await page.locator("[data-open-filters]").waitFor();
  return page;
}
async function openFilters(page) {
  if (!(await page.locator("[data-filter-dialog]").evaluate((el) => el.open)))
    await page.locator("[data-open-filters]").click();
}
async function applyFilters(page) {
  await page.locator('[data-event-filters] [type="submit"]').click();
  await idle(page);
}
async function search(page, change) {
  await openFilters(page);
  await change();
  await applyFilters(page);
}
const ids = (page) =>
  page
    .locator("[data-event-id]")
    .evaluateAll((els) => els.map((el) => el.dataset.eventId));
const from = (page) => page.locator("[data-range-from]").inputValue();
const to = (page) => page.locator("[data-range-to]").inputValue();

try {
  const page = await open();
  assert.equal(await page.locator("[data-filter-dialog]").isVisible(), false);
  assert.equal(await page.locator("[data-event-filters]").isVisible(), false);
  assert.match(
    await page.locator("[data-filter-summary]").innerText(),
    /Last 30 days/,
  );
  const initialUrl = page.url();
  await openFilters(page);
  assert.equal(
    await page
      .locator("#event-filter-title")
      .evaluate((el) => el === document.activeElement),
    true,
  );
  await page.locator("[data-keyword-filter]").fill("discard this draft");
  assert.equal(page.url(), initialUrl);
  assert.equal((await ids(page)).length, 6);
  assert.deepEqual(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
    [],
  );
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    assert.equal(
      await page
        .locator("[data-filter-dialog]")
        .evaluate((el) => el.contains(document.activeElement)),
      true,
    );
  }
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("[data-filter-dialog]").isVisible(), false);
  assert.equal(
    await page
      .locator("[data-open-filters]")
      .evaluate((el) => el === document.activeElement),
    true,
  );
  await openFilters(page);
  assert.equal(await page.locator("[data-keyword-filter]").inputValue(), "");
  await page.locator("[data-close-filters]").click();
  assert.equal(await page.locator("[data-day]").count(), 30);
  assert.equal(await page.locator("[data-day]:disabled").count(), 7);
  // The archive offers its page size too, at the static archive's size.
  assert.equal(await page.locator("[data-page-size]").inputValue(), "20");
  assert.equal(
    await page.locator(".page-size-field label").innerText(),
    "Stories per page",
  );
  assert.equal(await page.locator("[data-page-size]").isVisible(), true);
  assert.equal(
    await page.locator('[data-range-preset="90"]').isDisabled(),
    true,
  );
  assert.equal(await page.locator(".event-lead").count(), 0);
  assert.equal(await page.locator(".archive-entry").count(), 6);
  assert.equal((await ids(page)).length, 6);
  await search(page, () =>
    page.locator("[data-keyword-filter]").fill("helicopter"),
  );
  await idle(page);
  assert.equal((await ids(page)).length, 1);
  assert.match(
    await page.locator("[data-journal-status]").innerText(),
    /1 matching event/,
  );
  assert.equal(await page.locator("[data-day]").count(), 30);
  const pinned = page.url();
  await page.reload();
  await idle(page);
  assert.equal(
    await page.locator("[data-keyword-filter]").inputValue(),
    "helicopter",
  );
  assert.equal((await ids(page)).length, 1);
  await search(page, () => page.locator('[data-range-preset="7"]').click());
  await idle(page);
  assert.equal(await from(page), "2026-09-28");
  assert.equal(await to(page), "2026-10-04");
  assert.equal(await page.locator("[data-day]").count(), 7);
  assert.equal(await page.locator("[data-chart-table] tr").count(), 7);
  await page.goBack();
  await idle(page);
  assert.equal(page.url(), pinned);
  assert.equal(await page.locator("[data-day]").count(), 30);
  await page.locator('[data-day="2026-10-04"]').click();
  await idle(page);
  assert.equal(await page.locator("[data-day]").count(), 1);
  assert.equal(await from(page), "2026-10-04");
  assert.equal(
    await page
      .locator("#feed-heading")
      .evaluate((el) => el === document.activeElement),
    true,
  );
  await search(page, () => page.locator("[data-range-to]").fill("2026-10-03"));
  await idle(page);
  assert.match(
    await page.locator("[data-filter-status]").innerText(),
    /start date must/,
  );
  assert.equal((await ids(page)).length, 1);
  assert.equal(await page.locator("[data-day]").count(), 1);
  await search(page, () => page.locator("[data-clear-filters]").click());
  await idle(page);
  const aircraftOption = await page
    .locator("[data-aircraft-filter] option")
    .evaluateAll(
      (options) =>
        options.find((option) => option.value.includes("boeing"))?.value,
    );
  assert(aircraftOption);
  await search(page, () =>
    page.locator("[data-aircraft-filter]").selectOption(aircraftOption),
  );
  await idle(page);
  assert.equal((await ids(page)).length, 1);
  await search(page, () =>
    page.locator('[data-type="flight-patterns"]').click(),
  );
  await idle(page);
  assert.equal((await ids(page)).length, 0);
  assert.match(
    await page.locator(".event-empty").innerText(),
    /wider date range/,
  );
  await search(page, () => page.locator("[data-clear-filters]").click());
  await idle(page);
  await search(page, () => page.locator("[data-keyword-filter]").fill("7700"));
  await idle(page);
  assert.equal((await ids(page)).length, 1);
  await page.locator(".daily-table summary").click();
  assert.deepEqual(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
    [],
  );
  await page.context().close();

  // Filtering must retain public aircraft lookups while rejecting unsafe links
  // and external images. No test navigates to an external destination.
  for (const [markup, allowed] of [
    [
      '<a href="https://www.flightaware.com/live/flight/TEST123" rel="external noopener noreferrer">FlightAware</a>',
      true,
    ],
    [
      '<a href="https://en.wikipedia.org/w/index.php?search=Robinson%20R44" rel="external noopener noreferrer">Wikipedia type search</a>',
      true,
    ],
    [
      '<a href="http://www.flightaware.com/" rel="external noopener noreferrer">Insecure</a>',
      false,
    ],
    [
      '<a href="javascript:void(0)" rel="external noopener noreferrer">Invalid</a>',
      false,
    ],
    [
      '<a href="https://private.example.ts.net/" rel="external noopener noreferrer">Private</a>',
      false,
    ],
    [
      '<a href="https://nas.lan/" rel="external noopener noreferrer">Private network</a>',
      false,
    ],
    [
      '<a href="https://127.0.0.1/" rel="external noopener noreferrer">Local</a>',
      false,
    ],
    [
      '<a href="https://user:password@example.com/" rel="external noopener noreferrer">Credentials</a>',
      false,
    ],
    ['<a href="https://example.com/">Missing relationship</a>', false],
    [`<a href="${base}/">Same origin without relationship</a>`, false],
    [
      `<a href="${base.replace("http://", "http://user:password@")}/" rel="external noopener noreferrer">Same origin credentials</a>`,
      false,
    ],
    ['<img src="https://example.com/aircraft.png" alt="External">', false],
    [
      '<img src="/assets/images/wordmark.svg" srcset="https://example.com/aircraft.png 1x" alt="External candidate">',
      false,
    ],
    [
      '<picture><source srcset="https://example.com/aircraft.png"><img src="/assets/images/wordmark.svg" alt="External source"></picture>',
      false,
    ],
    [
      '<svg xmlns="http://www.w3.org/2000/svg"><a xlink:href="javascript:window.injected=true"><text>Unsafe link</text></a></svg>',
      false,
    ],
    [
      '<svg xmlns="http://www.w3.org/2000/svg"><a href="/"><animate attributeName="href" values="javascript:window.injected=true"/><text>Mutating link</text></a></svg>',
      false,
    ],
    ['<video poster="https://example.com/aircraft.png"></video>', false],
    [
      '<a href="https://example.com/" rel="external noopener noreferrer" ping="https://example.com/track">Tracking link</a>',
      false,
    ],
    [
      '<button formaction="https://example.com/submit">External action</button>',
      false,
    ],
  ]) {
    const links = await open();
    await links.route("**/query-v2/*/cards/*.json", async (route) => {
      const response = await route.fetch();
      const payload = await response.json();
      for (const variant of ["lead", "row"])
        payload[variant] = payload[variant].replace(
          "</article>",
          markup.includes("Wikipedia type search")
            ? `<details class="event-more-links"><summary>More aircraft links</summary><ul class="event-references"><li>${markup}</li></ul></details></article>`
            : `<ul class="event-references"><li>${markup}</li></ul></article>`,
        );
      await route.fulfill({ response, body: JSON.stringify(payload) });
    });
    await search(links, () =>
      links.locator("[data-keyword-filter]").fill("helicopter"),
    );
    await idle(links);
    assert.equal((await ids(links)).length, allowed ? 1 : 6, markup);
    if (allowed) {
      assert.equal(await links.locator(".event-references a").count(), 1);
      if (markup.includes("Wikipedia type search")) {
        assert.equal(
          await links.locator(".event-references a").isVisible(),
          false,
        );
        await links.locator(".event-more-links > summary").focus();
        await links.keyboard.press("Enter");
        assert.equal(
          await links.locator(".event-references a").isVisible(),
          true,
        );
      }
      await links.reload();
      await idle(links);
      assert.equal((await ids(links)).length, 1);
    } else {
      assert.match(
        await links.locator("[data-journal-status]").innerText(),
        /previous view is still shown/,
      );
    }
    await links.context().close();
  }

  // Several draft edits apply together and leave the committed view untouched.
  const rapid = await open();
  await rapid.route("**/query-v2/*/index.json", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 350));
    await route.continue();
  });
  await openFilters(rapid);
  await rapid.locator('[data-range-preset="7"]').click();
  await rapid.locator('[data-type="flight-patterns"]').click();
  assert.equal((await ids(rapid)).length, 6);
  assert.equal(await rapid.locator("[data-day]").count(), 30);
  await applyFilters(rapid);
  await idle(rapid);
  assert.equal(await from(rapid), "2026-09-28");
  assert.equal(
    await rapid
      .locator('[data-type="flight-patterns"]')
      .getAttribute("aria-pressed"),
    "true",
  );
  assert.equal((await ids(rapid)).length, 2);
  await rapid.context().close();

  // Closing a submitted draft cancels the request; a late response cannot apply it.
  const canceled = await open();
  let releaseCanceled;
  const canceledGate = new Promise((resolve) => {
    releaseCanceled = resolve;
  });
  await canceled.route("**/query-v2/*/cards/*.json", async (route) => {
    await canceledGate;
    await route.continue().catch(() => {});
  });
  const beforeCancel = canceled.url();
  await openFilters(canceled);
  await canceled.locator("[data-keyword-filter]").fill("helicopter");
  await canceled.locator('[data-event-filters] [type="submit"]').click();
  await canceled.waitForFunction(() =>
    document.querySelector("[data-event-feed]").hasAttribute("aria-busy"),
  );
  for (const key of ["Shift+Tab", "Tab", "Tab", "Tab"]) {
    await canceled.keyboard.press(key);
    assert.equal(
      await canceled
        .locator("[data-filter-dialog]")
        .evaluate((el) => el.contains(document.activeElement)),
      true,
    );
  }
  await canceled.locator("[data-close-filters]").click();
  releaseCanceled();
  await idle(canceled);
  await canceled.waitForTimeout(300);
  assert.equal(canceled.url(), beforeCancel);
  assert.equal((await ids(canceled)).length, 6);
  await openFilters(canceled);
  assert.equal(
    await canceled.locator("[data-keyword-filter]").inputValue(),
    "",
  );
  await canceled.context().close();

  const landscape = await open({
    viewport: { width: 844, height: 390 },
    isMobile: true,
    hasTouch: true,
  });
  await openFilters(landscape);
  assert.equal(
    await landscape
      .locator("#event-filter-title")
      .evaluate((el) => el === document.activeElement),
    true,
  );
  assert.equal(
    await landscape
      .locator("[data-filter-dialog]")
      .evaluate((el) => el.scrollWidth > el.clientWidth + 1),
    false,
  );
  await landscape.context().close();

  // Opening a draft during a deep-link restore must keep the URL and view aligned.
  const restoring = await open();
  let releaseRestore;
  const restoreGate = new Promise((resolve) => {
    releaseRestore = resolve;
  });
  await restoring.route("**/query-v2/*/index.json", async (route) => {
    await restoreGate;
    await route.continue().catch(() => {});
  });
  await restoring.goto(archive + "?q=helicopter");
  await restoring.locator("[data-open-filters]").waitFor();
  await restoring.waitForFunction(() =>
    document.querySelector("[data-event-feed]").hasAttribute("aria-busy"),
  );
  await openFilters(restoring);
  await restoring.keyboard.press("Escape");
  releaseRestore();
  await idle(restoring);
  await restoring.waitForTimeout(100);
  assert.equal(new URL(restoring.url()).searchParams.get("q"), null);
  assert.equal((await ids(restoring)).length, 6);
  assert.match(
    await restoring.locator("[data-filter-summary]").innerText(),
    /Last 30 days/,
  );
  await restoring.context().close();

  // Retained pages with the earlier inline form still combine pending edits.
  const legacy = await open();
  await legacy.route(archive, async (route) => {
    const response = await route.fetch();
    let html = await response.text();
    const form = html.match(/<form class="event-filters"[\s\S]*?<\/form>/)[0];
    const picker = html.match(
      /<div class="filter-field page-size-field"[\s\S]*?<\/div>/,
    )[0];
    // The earlier picker offered 25 in place of 20.
    const retainedPicker = picker.replace(
      /(<select[^>]*>)[\s\S]*(<\/select>)/,
      "$1" +
        [10, 25, 50, 100]
          .map((size) => `<option value="${size}">${size}</option>`)
          .join("") +
        "$2",
    );
    html = html
      .replace(picker, "")
      .replace(
        /<dialog class="event-filter-dialog"[\s\S]*?<\/dialog>/,
        form.replace("</form>", retainedPicker + "</form>"),
      );
    await route.fulfill({ response, body: html });
  });
  await legacy.goto(archive);
  await legacy.locator("[data-event-filters]:not([hidden])").waitFor();
  await legacy.locator("[data-keyword-filter]").fill("helicopter");
  await legacy.locator("[data-page-size]").selectOption("25");
  await legacy.waitForFunction(
    () => document.querySelectorAll("[data-event-id]").length === 1,
  );
  assert.equal(
    await legacy.locator("[data-keyword-filter]").inputValue(),
    "helicopter",
  );
  assert.equal(new URL(legacy.url()).searchParams.get("q"), "helicopter");
  assert.equal(new URL(legacy.url()).searchParams.get("size"), "25");
  await legacy.context().close();

  // Forward to the displayed state must cancel a pending Back request. The
  // archive itself comes back without a request, so the race runs between
  // two query states: a reload empties the card cache, and Back then has to
  // fetch the wider query's cards again.
  const race = await open();
  await search(race, () => {});
  await idle(race);
  const widerUrl = race.url();
  await search(race, () =>
    race.locator("[data-keyword-filter]").fill("helicopter"),
  );
  await idle(race);
  const searchUrl = race.url();
  assert.notEqual(searchUrl, widerUrl);
  await race.reload();
  await idle(race);
  assert.equal((await ids(race)).length, 1);
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  await race.route("**/query-v2/*/cards/*.json", async (route) => {
    await gate;
    await route.continue().catch(() => {});
  });
  await race.goBack();
  await race.waitForFunction(() =>
    document.querySelector("[data-event-feed]").hasAttribute("aria-busy"),
  );
  await race.goForward();
  release();
  await idle(race);
  assert.equal(race.url(), searchUrl);
  assert.equal(
    await race.locator("[data-keyword-filter]").inputValue(),
    "helicopter",
  );
  assert.equal((await ids(race)).length, 1);
  await race.context().close();

  for (const failedAsset of ["index.json", "cards/*.json"]) {
    const failed = await open();
    const beforeFeed = await failed.locator("[data-event-feed]").innerHTML();
    const beforeChart = await failed
      .locator("[data-chart-buckets]")
      .innerHTML();
    await failed.route(`**/query-v2/*/${failedAsset}`, (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ snapshot_id: "wrong" }),
      }),
    );
    await search(failed, () =>
      failed.locator("[data-keyword-filter]").fill("helicopter"),
    );
    await idle(failed);
    assert.equal(
      await failed.locator("[data-event-feed]").innerHTML(),
      beforeFeed,
    );
    assert.equal(
      await failed.locator("[data-chart-buckets]").innerHTML(),
      beforeChart,
    );
    assert.equal(
      await failed.locator("[data-keyword-filter]").inputValue(),
      "helicopter",
    );
    assert.match(
      await failed.locator("[data-journal-status]").innerText(),
      /previous view is still shown/,
    );
    assert.equal(
      await failed.locator("[data-filter-dialog]").isVisible(),
      true,
    );
    assert.match(
      await failed.locator("[data-filter-status]").innerText(),
      /Try again/,
    );
    await failed.unroute(`**/query-v2/*/${failedAsset}`);
    await applyFilters(failed);
    assert.equal((await ids(failed)).length, 1);
    assert.equal(
      await failed.locator("[data-filter-dialog]").isVisible(),
      false,
    );
    await failed.context().close();
  }

  // A larger browser fixture proves pagination, identity searches, and older
  // history without adding invented registrations to the reader's preview.
  const fixture = Array.from({ length: 117 }, (_, i) => ({
    id: `00000000-0000-4000-8000-${String(i + 1000).padStart(12, "0")}`,
    observed_start:
      i === 116
        ? "2020-01-01T12:00:00Z"
        : i === 115
          ? "2026-06-01T12:00:00Z"
          : "2026-10-03T12:00:00Z",
    primary_type: "flight-patterns",
    title: `Synthetic pagination fixture ${i}`,
    summary: "Synthetic test observation.",
    aircraft: {
      registration: i === 115 ? "C-FTST" : `TEST${i}`,
      callsign: `TEST${i}`,
      type_code: "R44",
      type_description: "Robinson R44",
    },
    terms: "circling 7700",
    airport_pattern_candidate: i < 17,
  }));
  // The archive lists the same stories, newest first, with the static list's
  // markup; archive.json carries them as the build writes it.
  const newestFirst = [...fixture].sort(
    (a, b) =>
      Date.parse(b.observed_start) - Date.parse(a.observed_start) ||
      a.id.localeCompare(b.id),
  );
  const archiveEntries = newestFirst.map((record) => ({
    id: record.id,
    observed_start: record.observed_start,
    html: `<article class="event-card archive-entry type-flight-patterns" data-event-id="${record.id}"><p class="archive-entry-meta"><time datetime="${record.observed_start}">${record.observed_start}</time></p><h4><a href="/stories/${record.id}/">${record.title}</a></h4><p class="archive-entry-dek">${record.summary}</p></article>`,
  }));
  // A page whose journal reads the fixture in place of the preview's six
  // events. Its archive still draws the preview's own first page, so archive
  // checks start from another size or page.
  async function fixturePage({ width = 320, entries = archiveEntries } = {}) {
    // An explicit context, which the accessibility checker requires.
    const context = await browser.newContext({
      viewport: { width, height: 900 },
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route(
      (url) =>
        url.origin === base &&
        !url.pathname.startsWith("/assets/") &&
        !url.pathname.startsWith("/event-data/") &&
        !url.pathname.startsWith("/examples/"),
      async (route) => {
        const response = await route.fetch();
        let html = await response.text();
        html = html
          .replace(
            /(<script type="application\/json" data-journal-config>)(.*?)(<\/script>)/s,
            (_match, start, json, end) => {
              const config = JSON.parse(json);
              config.eventCount = fixture.length;
              config.earliest_event = "2020-01-01";
              config.aircraftChoices = [
                { value: "r44", label: "R44 · Robinson R44" },
              ];
              return start + JSON.stringify(config) + end;
            },
          )
          .replace(
            /(<select[^>]*data-aircraft-filter>).*?(<\/select>)/s,
            '$1<option value="">All aircraft types</option><option value="r44">R44 · Robinson R44</option>$2',
          )
          .replaceAll('min="2026-09-05"', 'min="2020-01-01"');
        await route.fulfill({ response, body: html });
      },
    );
    const data = (key, value) =>
      JSON.stringify({
        version: 2,
        presentation: journal.presentation,
        snapshot_id: snapshot.snapshot_id,
        [key]: value,
      });
    await page.route("**/query-v2/*/index.json", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: data("events", fixture),
      }),
    );
    await page.route("**/query-v2/*/archive.json", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: data("stories", entries),
      }),
    );
    await page.route("**/query-v2/*/cards/*.json", (route) => {
      const id = new URL(route.request().url()).pathname
        .split("/")
        .at(-1)
        .replace(/\.json$/, "");
      const record = fixture.find((event) => event.id === id);
      assert(record);
      const card = (lead) =>
        `<article class="event-card${lead ? " event-lead" : ""}" data-event-id="${id}"><h3>${record.title}</h3></article>`;
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          version: 2,
          presentation: journal.presentation,
          snapshot_id: snapshot.snapshot_id,
          id,
          lead: card(true),
          row: card(false),
        }),
      });
    });
    return page;
  }
  const status = (page) => page.locator("[data-journal-status]").innerText();
  const steps = (page) =>
    page.locator("[data-event-feed] nav.pagination").evaluateAll((navs) =>
      navs.map((nav) => ({
        label: nav.getAttribute("aria-label"),
        steps: [...nav.querySelectorAll(".page-step")].map((step) => ({
          href: step.getAttribute("href"),
          rel: step.getAttribute("rel"),
          disabled: step.getAttribute("aria-disabled"),
          name: step.getAttribute("aria-label"),
          focusable: step.tabIndex >= 0 && step.hasAttribute("href"),
        })),
        numbers: [...nav.querySelectorAll("ol > li")].map((item) =>
          item.textContent.trim(),
        ),
        status: nav.querySelector(".page-status")?.textContent ?? null,
      })),
    );
  const address = (page) => {
    const url = new URL(page.url());
    return url.pathname + url.search;
  };

  const many = await fixturePage();
  await many.goto(archive);
  await many.locator("[data-open-filters]").waitFor();
  await openFilters(many);
  await applyFilters(many);
  await idle(many);
  const firstPage = await ids(many);
  assert.equal(firstPage.length, 20);
  assert.equal(await many.locator(".event-lead").count(), 1);
  assert.match(await status(many), /115 matching events/);
  assert.equal(
    await many.locator(".page-size-field label").innerText(),
    "Results per page",
  );
  // Previous and Next above and below the results; on the first page the
  // missing step keeps its place without an address.
  const queryNavs = await steps(many);
  assert.deepEqual(
    queryNavs.map((nav) => nav.label),
    ["Event pages, top of list", "Event pages"],
  );
  for (const nav of queryNavs) {
    assert.equal(nav.steps[0].disabled, "true");
    assert.equal(nav.steps[0].focusable, false);
    assert.equal(nav.steps[0].name, "Previous page (newer events)");
    assert.equal(nav.steps[1].rel, "next");
    assert.equal(nav.steps[1].name, "Next page (older events)");
    assert.match(nav.steps[1].href, /[?&]page=2(&|$)/);
  }
  assert.equal(queryNavs[0].status, "Page 1 of 6");
  assert.deepEqual(queryNavs[1].numbers, ["1", "2", "3", "4", "5", "6"]);
  await many
    .locator('[data-event-feed] nav.page-steps [data-event-page="next"]')
    .click();
  await idle(many);
  const secondPage = await ids(many);
  assert.equal(
    secondPage.some((id) => firstPage.includes(id)),
    false,
  );
  assert.equal(await many.locator(".event-lead").count(), 0);
  assert.equal(
    await many
      .locator("#feed-heading")
      .evaluate((el) => el === document.activeElement),
    true,
  );
  // A new size opens the page that holds the first result shown.
  for (const [count, page, pages] of [
    [10, 3, 12],
    [50, 1, 3],
    [100, 1, 2],
  ]) {
    await many.locator("[data-page-size]").selectOption(String(count));
    await idle(many);
    const shown = await ids(many);
    assert.equal(shown.length, count);
    assert.ok(shown.includes(secondPage[0]), `${count} per page`);
    assert.equal(await many.locator(".event-lead").count(), page === 1 ? 1 : 0);
    assert.equal(
      await status(many),
      `115 matching events. Page ${page} of ${pages}.`,
    );
    assert.equal(new URL(many.url()).searchParams.get("size"), String(count));
    assert.equal(new URL(many.url()).searchParams.get("page"), String(page));
    assert.equal(
      await many
        .locator("[data-page-size]")
        .evaluate((el) => el === document.activeElement),
      true,
    );
  }
  await many.reload();
  await idle(many);
  assert.equal(await many.locator("[data-page-size]").inputValue(), "100");
  assert.equal((await ids(many)).length, 100);
  await search(many, () => many.locator('[data-range-preset="all"]').click());
  await idle(many);
  assert.equal(await from(many), "2020-01-01");
  assert.match(
    await many.locator("[data-journal-status]").innerText(),
    /117 matching events/,
  );
  assert.match(
    await many.locator("[data-chart-aggregation]").innerText(),
    /Monthly/,
  );
  assert.equal(await many.locator("[data-day]").count(), 82);
  assert(
    await many
      .locator("[data-day]")
      .evaluateAll((buttons) =>
        buttons.every((button) => button.getBoundingClientRect().width > 0.5),
      ),
  );
  assert.match(
    await many.locator('[data-day="2020-01-01"]').getAttribute("aria-label"),
    /1 matching event.*archive incomplete/,
  );
  assert.equal(
    await many.locator('[data-range-preset="90"]').isEnabled(),
    true,
  );
  await openFilters(many);
  await many.locator("[data-airport-pattern-filter]").check();
  assert.match(
    await many.locator("[data-journal-status]").innerText(),
    /117 matching events/,
  );
  await many.locator("[data-close-filters]").click();
  await openFilters(many);
  assert.equal(
    await many.locator("[data-airport-pattern-filter]").isChecked(),
    false,
  );
  await many.locator("[data-airport-pattern-filter]").check();
  await applyFilters(many);
  assert.match(
    await many.locator("[data-journal-status]").innerText(),
    /100 matching events/,
  );
  assert.match(
    await many.locator("[data-filter-summary]").innerText(),
    /Airport-pattern candidates hidden/,
  );
  assert.equal(
    new URL(many.url()).searchParams.get("hide_airport_patterns"),
    "1",
  );
  assert.equal(
    await many
      .locator("[data-chart-table] tr td:nth-child(2)")
      .evaluateAll((cells) =>
        cells.reduce((sum, cell) => sum + (Number(cell.textContent) || 0), 0),
      ),
    100,
  );
  await many.locator("[data-page-size]").selectOption("20");
  await idle(many);
  await many
    .locator('[data-event-feed] nav.page-numbers [data-event-page="next"]')
    .click();
  await idle(many);
  assert.match(
    await many.locator("[data-journal-status]").innerText(),
    /100 matching events. Page 2 of 5/,
  );
  await many.reload();
  await idle(many);
  assert.equal(
    await many.locator("[data-airport-pattern-filter]").isChecked(),
    true,
  );
  assert.match(
    await many.locator("[data-journal-status]").innerText(),
    /100 matching events. Page 2 of 5/,
  );
  await search(many, () =>
    many.locator("[data-airport-pattern-filter]").uncheck(),
  );
  assert.match(
    await many.locator("[data-journal-status]").innerText(),
    /117 matching events/,
  );
  await search(many, () => many.locator('[data-range-preset="90"]').click());
  await idle(many);
  assert.equal(await many.locator("[data-day]").count(), 90);
  assert(
    await many
      .locator("[data-day]")
      .evaluateAll((buttons) =>
        buttons.every((button) => button.getBoundingClientRect().width > 0.5),
      ),
  );
  await search(many, () => many.locator('[data-range-preset="all"]').click());
  await idle(many);
  await search(many, () => many.locator("[data-keyword-filter]").fill("cftst"));
  await idle(many);
  assert.equal((await ids(many)).length, 1);
  await search(many, () =>
    many.locator("[data-aircraft-filter]").selectOption("r44"),
  );
  await idle(many);
  assert.equal((await ids(many)).length, 1);
  await search(many, () => many.locator('[data-type="performance"]').click());
  await idle(many);
  assert.equal((await ids(many)).length, 0);
  assert.equal(
    await many.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 1,
    ),
    false,
  );
  await many.context().close();

  // The archive pages on its own address at every offered size: Previous and
  // Next above and below the list, the page numbers below, and the size
  // picker, with Back, Forward, and reload restoring each page. A new size
  // opens the page that holds the first story shown.
  const storyPages = await fixturePage();
  const archiveUrl = (size, page) => {
    const params = new URLSearchParams();
    if (size !== DEFAULT_PAGE_SIZE) params.set("size", String(size));
    if (page !== 1) params.set("page", String(page));
    return `/stories/${params.size ? `?${params}` : ""}`;
  };
  async function expectArchive(size, page) {
    const pages = Math.ceil(fixture.length / size);
    const offset = (page - 1) * size;
    const where = `${size} per page, page ${page}`;
    assert.equal(address(storyPages), archiveUrl(size, page), where);
    assert.deepEqual(
      await storyPages
        .locator('[data-event-feed] [data-view="archive"] [data-event-id]')
        .evaluateAll((els) => els.map((el) => el.dataset.eventId)),
      newestFirst.slice(offset, offset + size).map((record) => record.id),
      where,
    );
    assert.equal(
      await storyPages.locator("[data-page-size]").inputValue(),
      String(size),
    );
    assert.equal(
      await storyPages.locator(".page-size-field label").innerText(),
      "Stories per page",
    );
    assert.equal(
      await status(storyPages),
      `${fixture.length} published stories. Page ${page} of ${pages}.`,
    );
    const navs = await steps(storyPages);
    assert.deepEqual(
      navs.map((nav) => nav.label),
      ["Story pages, top of list", "Story pages"],
    );
    for (const nav of navs) {
      const [previous, next] = nav.steps;
      assert.equal(previous.name, "Previous page (newer stories)");
      assert.equal(next.name, "Next page (older stories)");
      for (const [step, target, rel] of [
        [previous, page - 1, "prev"],
        [next, page + 1, "next"],
      ])
        if (target < 1 || target > pages)
          assert.deepEqual(
            [step.href, step.rel, step.disabled, step.focusable],
            [null, null, "true", false],
            where,
          );
        else
          assert.deepEqual(
            [step.href, step.rel, step.disabled, step.focusable],
            [archiveUrl(size, target), rel, null, true],
            where,
          );
    }
    assert.equal(navs[0].status, `Page ${page} of ${pages}`);
    assert.deepEqual(
      navs[1].numbers,
      pageNumbers(page, pages).map((number) => (number ? String(number) : "…")),
    );
  }
  await storyPages.goto(archive + "?size=10");
  await idle(storyPages);
  await expectArchive(10, 1);
  // The keyboard reaches Next straight from the picker: the disabled
  // Previous takes no focus. Enter opens the next page at its heading.
  const disabledPrevious = storyPages.locator(
    "[data-event-feed] nav.page-steps .page-prev",
  );
  assert.equal(
    await disabledPrevious.evaluate((el) => {
      el.focus();
      return el === document.activeElement;
    }),
    false,
  );
  await storyPages.locator("[data-page-size]").focus();
  await storyPages.keyboard.press("Tab");
  assert.equal(
    await storyPages
      .locator("[data-event-feed] nav.page-steps .page-next")
      .evaluate((el) => el === document.activeElement),
    true,
  );
  await storyPages.keyboard.press("Enter");
  await idle(storyPages);
  await expectArchive(10, 2);
  assert.equal(
    await storyPages
      .locator("#feed-heading")
      .evaluate((el) => el === document.activeElement),
    true,
  );
  await storyPages
    .locator('[data-event-feed] nav.page-numbers a[data-event-page="12"]')
    .click();
  await idle(storyPages);
  await expectArchive(10, 12);
  for (const [size, page] of [
    [50, 3],
    [100, 2],
  ]) {
    await storyPages.locator("[data-page-size]").selectOption(String(size));
    await idle(storyPages);
    await expectArchive(size, page);
  }
  for (const [move, size, page] of [
    ["back", 50, 3],
    ["back", 10, 12],
    ["back", 10, 2],
    ["forward", 10, 12],
    ["reload", 10, 12],
  ]) {
    if (move === "back") await storyPages.goBack();
    else if (move === "forward") await storyPages.goForward();
    else await storyPages.reload();
    await idle(storyPages);
    await expectArchive(size, page);
  }
  await storyPages.locator("[data-page-size]").selectOption("20");
  await idle(storyPages);
  await expectArchive(20, 6);
  await storyPages
    .locator('[data-event-feed] nav.page-steps [data-event-page="previous"]')
    .click();
  await idle(storyPages);
  await expectArchive(20, 5);
  // A search from an archive page keeps the reader's size.
  await search(storyPages, () => {});
  assert.equal(new URL(storyPages.url()).searchParams.get("size"), "20");
  await storyPages.goBack();
  await idle(storyPages);
  await expectArchive(20, 5);
  assert.deepEqual(
    (
      await new AxeBuilder({ page: storyPages })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
    [],
  );
  assert.equal(
    await storyPages.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 1,
    ),
    false,
  );
  await storyPages.context().close();
  // Archive data that is unsafe or does not match the snapshot leaves the
  // archive as it was: a script, a link or image candidate on another site,
  // a repeated story, or a missing one.
  for (const entries of [
    archiveEntries.map((entry, index) =>
      index === 3
        ? {
            ...entry,
            html: entry.html.replace(
              "</article>",
              "<script>window.injected = true</script></article>",
            ),
          }
        : entry,
    ),
    archiveEntries.map((entry, index) =>
      index === 2
        ? {
            ...entry,
            html: entry.html.replace(
              `href="/stories/${entry.id}/"`,
              'href="https://example.com/"',
            ),
          }
        : entry,
    ),
    archiveEntries.map((entry, index) =>
      index === 2
        ? {
            ...entry,
            html: entry.html.replace(
              "</article>",
              '<img src="/assets/images/wordmark.svg" srcset="https://example.com/aircraft.png 1x" alt="External candidate"></article>',
            ),
          }
        : entry,
    ),
    archiveEntries.map((entry, index) =>
      index === 5 ? { ...entry, id: archiveEntries[4].id } : entry,
    ),
    archiveEntries.slice(1),
  ]) {
    const broken = await fixturePage({ entries });
    await broken.goto(archive);
    await idle(broken);
    const before = await broken.locator("[data-event-feed]").innerHTML();
    await broken.locator("[data-page-size]").selectOption("10");
    await idle(broken);
    assert.match(await status(broken), /previous view is still shown/);
    assert.equal(address(broken), "/stories/");
    assert.equal(await broken.locator("[data-page-size]").inputValue(), "20");
    assert.equal(await broken.locator("[data-event-feed]").innerHTML(), before);
    assert.equal(await broken.evaluate(() => window.injected), undefined);
    await broken.context().close();
  }

  // Latest assets also serve retained query-v1 pages. Their unannotated records
  // remain searchable and are never silently hidden by the new presentation flag.
  const retainedContext = await browser.newContext({
    viewport: { width: 390, height: 900 },
  });
  const retained = await retainedContext.newPage();
  retained.on("pageerror", (error) => errors.push(error.message));
  await retained.route("**/query-v1/**", async (route) => {
    const response = await route.fetch({
      url: route
        .request()
        .url()
        .replace("query-v1/", `query-v2/${journal.presentation}/`),
    });
    let body = await response.text();
    if (route.request().resourceType() === "document") {
      body = body
        .replaceAll(`query-v2/${journal.presentation}/`, "query-v1/")
        .replace(/<div class="airport-pattern-filter">.*?<\/div>/s, "")
        .replace(
          /(<script type="application\/json" data-journal-config>)(.*?)(<\/script>)/s,
          (_match, start, json, end) => {
            const config = JSON.parse(json);
            config.version = 1;
            delete config.initial.hideAirportPatterns;
            return start + JSON.stringify(config) + end;
          },
        );
    } else if (route.request().url().endsWith(".json")) {
      const data = JSON.parse(body);
      data.version = 1;
      for (const event of data.events || [])
        delete event.airport_pattern_candidate;
      body = JSON.stringify(data);
    }
    await route.fulfill({ response, body });
  });
  await retained.goto(
    `${base}${journal.viewPath("all", 1).replace(`query-v2/${journal.presentation}/`, "query-v1/")}`,
  );
  await retained.locator('[data-journal][data-search-ready="true"]').waitFor();
  assert.equal(
    await retained.locator("[data-airport-pattern-filter]").count(),
    0,
  );
  await search(retained, () =>
    retained.locator("[data-keyword-filter]").fill("7700"),
  );
  assert.equal((await ids(retained)).length, 1);
  assert.match(retained.url(), /query-v1/);
  await retainedContext.close();

  const noJs = await browser.newPage({
    javaScriptEnabled: false,
    // Native focus can start smooth scrolling before the next pointer action.
    reducedMotion: "reduce",
    viewport: { width: 390, height: 900 },
  });
  // Without JavaScript the archive is a plain list that links every story.
  await noJs.goto(archive);
  assert.equal(await noJs.locator("[data-event-filters]").isVisible(), false);
  assert.equal(await noJs.locator("[data-page-size]").isVisible(), false);
  const listed = await noJs
    .locator(".archive-entry h4 a")
    .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
  assert.deepEqual(
    listed.sort(),
    snapshot.events.map((event) => event.article_path).sort(),
  );
  for (const href of listed)
    assert.equal((await noJs.request.get(base + href)).status(), 200, href);
  // Immutable snapshot pages keep the card layout for search results.
  await noJs.goto(base + journal.viewPath("all", 1));
  assert.equal(await noJs.locator(".event-lead").count(), 1);
  const staticRow = noJs.locator(".event-entry:not(.event-lead)").first();
  const staticDetails = staticRow.locator(".event-expanded");
  assert.equal(
    await staticDetails.locator(".event-aircraft-table").isVisible(),
    false,
  );
  assert.equal(await staticRow.locator(".event-thumbnail").isVisible(), true);
  await staticDetails.locator("summary").focus();
  await noJs.keyboard.press("Enter");
  assert.equal(
    await staticDetails.locator(".event-aircraft-table").isVisible(),
    true,
  );
  assert.equal(
    await staticDetails.locator(".event-visual img").isVisible(),
    true,
  );
  assert.equal(await staticRow.locator(".event-thumbnail").isVisible(), false);
  assert.match(
    await staticDetails.innerText(),
    /Complete observation interval/,
  );
  const staticId = await staticRow.getAttribute("data-event-id");
  assert.equal(
    await staticDetails.locator(".event-full-measurements tbody tr").count(),
    snapshot.events.find((event) => event.id === staticId).measurements.length,
  );
  assert(
    (await noJs
      .locator('[data-day="2026-10-04"] .count-1')
      .first()
      .evaluate((el) => el.getBoundingClientRect().height)) > 0,
  );
  await noJs.locator(".daily-table summary").click();
  assert.equal(await noJs.locator("[data-chart-table] tr").count(), 30);
  await noJs.locator("noscript a").click();
  assert.match(noJs.url(), /query-v2\/[a-f0-9]{64}\/archive\/1/);
  assert.equal((await ids(noJs)).length, 6);
  await noJs.context().close();

  const disk = JSON.parse(
    await fs.readFile(path.join(preview, "event-snapshot.json"), "utf8"),
  );
  assert.equal(disk.snapshot_id, snapshot.snapshot_id);
  for (const [key, view] of journal.views)
    for (let page = 1; page <= view.pages; page++) {
      const html = await fs.readFile(
        path.join(preview, journal.viewPath(key, page), "index.html"),
        "utf8",
      );
      const ids = [...html.matchAll(/data-event-id="([^"]+)"/g)].map(
        (match) => match[1],
      );
      assert.equal(new Set(ids).size, ids.length);
      assert.deepEqual(
        ids,
        view.events
          .slice((page - 1) * DEFAULT_PAGE_SIZE, page * DEFAULT_PAGE_SIZE)
          .map((event) => event.id),
      );
    }
  assert.deepEqual(errors, []);
  console.log(
    "Journal browser checks passed: combined search, identities, ranges, chart/table agreement, 117-event pagination, 10/20/50/100 sizes kept in place, archive paging with Previous and Next, keyboard steps, older history, phone chart bars, URL/reload/history races, failures, focus, and no-JavaScript fallback.",
  );
} finally {
  await close();
}
