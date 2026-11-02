import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import http from "node:http";
import path from "node:path";
import { startPreview } from "./browser.mjs";
import { tools } from "./paths.mjs";

// A launch failure must exit by itself, with no listening server left behind.
const launchFailure = execFileSync(
  process.execPath,
  [
    "--input-type=module",
    "--eval",
    `import assert from "node:assert/strict";
     import { startPreview } from ${JSON.stringify(new URL("./browser.mjs", import.meta.url).href)};
     await assert.rejects(startPreview({ directory: ${JSON.stringify(tools)} }));
     console.log("launch failure cleaned up");`,
  ],
  {
    env: {
      ...process.env,
      CHROMIUM_PATH: path.join(tools, "missing-browser-for-lifecycle-check"),
    },
    encoding: "utf8",
    timeout: 10000,
  },
);
assert.equal(launchFailure.trim(), "launch failure cleaned up");

function request(base) {
  return new Promise((resolve, reject) => {
    const client = http.get(
      `${base}/browser.mjs`,
      { agent: false },
      (response) => {
        response.resume();
        response.once("end", () => resolve(response.statusCode));
      },
    );
    client.once("error", reject);
    client.setTimeout(2000, () =>
      client.destroy(new Error("Preview request timed out")),
    );
  });
}

for (const failBrowserClose of [false, true]) {
  const preview = await startPreview({ directory: tools });
  const closeBrowser = preview.browser.close.bind(preview.browser);
  try {
    assert.equal(await request(preview.base), 200);
    if (failBrowserClose) {
      const failure = new Error("Simulated browser shutdown failure");
      preview.browser.close = async () => {
        throw failure;
      };
      await assert.rejects(preview.close(), (error) => error === failure);
    } else {
      await preview.close();
    }
    await assert.rejects(request(preview.base), { code: "ECONNREFUSED" });
  } finally {
    preview.browser.close = closeBrowser;
    await preview.close();
  }
}

console.log("Preview lifecycle checks passed.");
