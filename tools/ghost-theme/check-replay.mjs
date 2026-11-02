import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { startPreview } from "./browser.mjs";
import { stories } from "./demo-content.mjs";
import { output } from "./paths.mjs";

const { browser, base, close } = await startPreview();
const fixture = (gap) => ({
  version: 1,
  kind: "schematic",
  points: [
    { t: 0, x: 10, y: 10, speed: 60, altitude: 1200 },
    { t: 10, x: 20, y: 20, break: gap, speed: 61, altitude: 1225 },
    { t: 20, x: 30, y: 20, speed: 62, altitude: 1250 },
  ],
});
const report = [];
try {
  for (const scenario of [
    "continuous",
    "gap",
    "invalid-gap",
    "invalid-point",
    "redirect",
    "fractional-duration",
  ]) {
    const context = await browser.newContext();
    let redirected = false;
    await context.route("**/redirected-replay.json", async (route) => {
      redirected = true;
      await route.fulfill({ json: fixture(false) });
    });
    await context.route("**/examples/circling.json", async (route) => {
      if (scenario === "redirect") {
        await route.fulfill({
          status: 302,
          headers: { location: "/redirected-replay.json" },
        });
      } else {
        const data = fixture(
          scenario === "gap"
            ? true
            : scenario === "invalid-gap"
              ? "false"
              : false,
        );
        if (scenario === "invalid-point") data.points[1].x = 101;
        if (scenario === "fractional-duration") {
          data.points = [data.points[0], { ...data.points[2], t: 10.8 }];
        }
        await route.fulfill({ json: data });
      }
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(base + stories[0].url);
    if (scenario === "fractional-duration") {
      await page.locator(".replay-controls:not([hidden])").waitFor();
      await page.locator(".replay-scrubber input").focus();
      await page.keyboard.press("End");
      assert.equal(
        await page.locator(".replay-scrubber input").inputValue(),
        "10.8",
      );
      assert.equal(
        await page.locator("[data-replay-speed]").innerText(),
        "62 kn",
      );
      assert.match(
        await page.locator(".replay-plane").getAttribute("transform"),
        /translate\(30 20\)/,
      );
    } else if (["continuous", "gap"].includes(scenario)) {
      await page.locator(".replay-controls:not([hidden])").waitFor();
      await page.locator(".replay-scrubber input").fill("5");
      const track = await page.locator(".track-base").getAttribute("d");
      assert.equal(
        (track.match(/M/g) || []).length,
        scenario === "gap" ? 2 : 1,
      );
      assert.equal(
        await page.locator(".replay-plane").isVisible(),
        scenario !== "gap",
      );
      assert.equal(
        await page.locator("[data-replay-speed]").innerText(),
        scenario === "gap" ? "—" : "60 kn",
      );
      if (scenario === "gap") {
        assert.equal(
          await page.locator("[data-replay-phase]").innerText(),
          "Gap in the record",
        );
        await page.locator(".replay-scrubber input").fill("10");
        assert.equal(await page.locator(".replay-plane").isVisible(), true);
        assert.equal(
          await page.locator("[data-replay-speed]").innerText(),
          "61 kn",
        );
      }
    } else {
      await page.locator(".replay-error[data-replay-status]").waitFor();
      assert.equal(await page.locator(".replay-controls").isVisible(), false);
      assert.equal(await page.locator(".replay-plane").count(), 0);
      assert.equal(redirected, false);
    }
    assert.deepEqual(errors, []);
    report.push({ scenario, passed: true });
    await context.close();
  }
  await fs.writeFile(
    path.join(output, "replay-checks.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(
    "Replay checks passed: gaps, continuous tracks, fractional durations, invalid data, and blocked redirects.",
  );
} finally {
  await close();
}
