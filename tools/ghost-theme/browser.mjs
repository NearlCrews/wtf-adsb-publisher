import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startServer } from "./serve.mjs";
import { tools } from "./paths.mjs";
import browserVersion from "./browser-version.json" with { type: "json" };

export const executablePath =
  process.env.CHROMIUM_PATH ||
  path.join(
    tools,
    "node_modules/.cache/chrome-for-testing",
    browserVersion.version,
    "chrome-linux64/chrome",
  );

// The pinned Chrome for Testing build, for checks and build-time rendering.
export const launchBrowser = () =>
  chromium.launch({ executablePath, args: ["--no-sandbox"] });

// The preview server and one Chromium instance, shared by the browser checks.
export async function startPreview({ directory } = {}) {
  const server = await startServer(0, directory);
  const closeServer = () => new Promise((resolve) => server.close(resolve));
  let browser;
  try {
    browser = await launchBrowser();
  } catch (error) {
    await closeServer();
    throw error;
  }
  return {
    browser,
    base: `http://127.0.0.1:${server.address().port}`,
    async close() {
      try {
        await browser.close();
      } finally {
        await closeServer();
      }
    },
  };
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    const version = execFileSync(executablePath, ["--version"], {
      encoding: "utf8",
    }).trim();
    if (
      !process.env.CHROMIUM_PATH &&
      !version.endsWith(` ${browserVersion.version}`)
    ) {
      throw new Error(
        "The cached test browser does not match browser-version.json.",
      );
    }
    console.log(version);
  } catch {
    console.error(
      "Install the test browser with npm --prefix tools/ghost-theme run browser:install, or set CHROMIUM_PATH.",
    );
    process.exitCode = 1;
  }
}
