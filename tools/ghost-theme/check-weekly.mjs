import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import AxeBuilder from "@axe-core/playwright";
import { startPreview } from "./browser.mjs";
import { root, output, tools } from "./paths.mjs";
import { privateScratch } from "./scratch.mjs";

const privateDir = await privateScratch("weekly-browser-");
const directory = await fs.mkdtemp(path.join(output, "weekly-browser-"));
const run = (command, args) => {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    timeout: 60000,
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
};
let close;
try {
  const fixture = run("python3", [
    "-B",
    "-c",
    "import sys,json;sys.path.insert(0,'tests');from test_weekly_spotlight import fixture;print(json.dumps(fixture()[2]))",
  ]);
  const input = path.join(privateDir, "snapshot.json");
  await fs.writeFile(input, fixture, { mode: 0o600 });
  run("node", [
    path.join(tools, "build.mjs"),
    "--snapshot",
    input,
    "--output",
    directory,
  ]);
  const preview = await startPreview({ directory });
  close = preview.close;
  for (const javaScriptEnabled of [true, false]) {
    for (const width of [390, 1280]) {
      const context = await preview.browser.newContext({
        javaScriptEnabled,
        viewport: { width, height: 900 },
      });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.addInitScript(() => {
        Date.now = () => Date.parse("2026-10-05T15:00:00Z");
      });
      await page.goto(preview.base);
      assert.equal(await page.locator(".weekly-card").isVisible(), true);
      assert.equal(
        await page.locator(".home-latest [data-event-id]").count(),
        1,
      );
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth + 1,
        ),
        false,
      );
      if (javaScriptEnabled) {
        assert.equal(
          await page.locator("#weekly-title").innerText(),
          "Most Interesting Last Week",
        );
        const link = page.locator(".weekly-card .text-link");
        await link.focus();
        assert.equal(
          await link.evaluate((node) => node === document.activeElement),
          true,
        );
        assert.deepEqual(
          (
            await new AxeBuilder({ page })
              .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
              .analyze()
          ).violations,
          [],
        );
        await page.evaluate(() => {
          Date.now = () => Date.parse("2026-10-12T00:00:00Z");
          window.dispatchEvent(new Event("pageshow"));
        });
        assert.equal(await page.locator(".weekly-card").isVisible(), false);
        assert.equal(
          await page.locator("[data-weekly-expired]").isVisible(),
          true,
        );
      } else {
        assert.equal(
          await page.locator("#weekly-title").innerText(),
          "Weekly spotlight",
        );
        assert.match(
          await page.locator(".weekly-card .eyebrow").innerText(),
          /28 Sept? 2026/i,
        );
      }
      await page.goto(preview.base + "/weekly/");
      assert.equal(await page.locator("h1").innerText(), "Weekly picks");
      assert.equal(await page.locator(".weekly-card").count(), 1);
      await page.locator(".weekly-card .text-link").click();
      assert.equal(await page.locator(".weekly-research").count(), 1);
      assert.equal(await page.locator(".weekly-research ol li").count(), 2);
      assert.equal(
        await page.locator('a[href="#weekly-source-manufacturer"]').count(),
        1,
      );
      assert.deepEqual(errors, []);
      await context.close();
    }
  }
  // A reader who opened page one keeps the same ordering after a newer build.
  const older = run("python3", [
    "-B",
    "-c",
    "import sys,json;sys.path.insert(0,'tests');from test_weekly_spotlight import archive_fixture;print(json.dumps(archive_fixture()))",
  ]);
  await fs.writeFile(input, older, { mode: 0o600 });
  run("node", [
    path.join(tools, "build.mjs"),
    "--snapshot",
    input,
    "--output",
    directory,
  ]);
  const context = await preview.browser.newContext({
    javaScriptEnabled: false,
  });
  const page = await context.newPage();
  await page.goto(preview.base + "/weekly/");
  assert.equal(await page.locator(".weekly-card").count(), 10);
  const firstPage = await page
    .locator(".weekly-card .text-link")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));
  // Previous and Next above and below the picks; the first page's Previous
  // stays in place without an address.
  assert.deepEqual(
    await page
      .locator("nav.pagination")
      .evaluateAll((navs) => navs.map((nav) => nav.getAttribute("aria-label"))),
    ["Story pages, top of list", "Story pages"],
  );
  assert.equal(
    await page.locator('.page-prev[aria-disabled="true"]:not([href])').count(),
    2,
  );
  const next = await page
    .locator('nav.page-steps a[rel="next"]')
    .getAttribute("href");
  assert.ok(next.startsWith(`/event-pages/${JSON.parse(older).snapshot_id}/`));
  assert.equal(
    await page.locator('nav.page-numbers a[rel="next"]').getAttribute("href"),
    next,
  );
  await fs.writeFile(input, fixture, { mode: 0o600 });
  run("node", [
    path.join(tools, "build.mjs"),
    "--snapshot",
    input,
    "--output",
    directory,
  ]);
  await page.goto(preview.base + next);
  const secondPage = await page
    .locator(".weekly-card .text-link")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("href")));
  assert.equal(secondPage.length, 2);
  assert.equal(new Set([...firstPage, ...secondPage]).size, 12);
  // The last page steps back within the same snapshot, and has no Next.
  assert.ok(
    (
      await page.locator('nav.page-steps a[rel="prev"]').getAttribute("href")
    ).startsWith(`/event-pages/${JSON.parse(older).snapshot_id}/`),
  );
  assert.equal(
    await page.locator('.page-next[aria-disabled="true"]:not([href])').count(),
    2,
  );
  // The retired archive path is gone; the archive lives at /weekly/ only.
  assert.equal(
    (await context.request.get(preview.base + "/weekly-spotlights/")).status(),
    404,
  );
  await context.close();
  console.log(
    "Weekly spotlight checks passed: one event, researched article, citations, UTC rollover, archive with Previous and Next, keyboard, mobile, and no-JavaScript.",
  );
} finally {
  if (close) await close();
  await fs.rm(privateDir, { recursive: true, force: true });
  await fs.rm(directory, { recursive: true, force: true });
}
