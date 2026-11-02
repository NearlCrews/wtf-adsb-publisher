import path from "node:path";
import { fileURLToPath } from "node:url";
import { launchBrowser } from "./browser.mjs";

// Re-encode PNG map pixels as WebP with the pinned Chromium's canvas encoder.
// The Python media tools keep one encoder process for a whole run: it reads
// frames from stdin and writes one frame back for each, so the browser starts
// once rather than once per map. A frame is a 4-byte big-endian length and
// that many bytes; an empty reply frame means the image could not be encoded.

async function encodeOn(page, png, quality) {
  const encoded = await page.evaluate(
    async ({ data, quality }) => {
      // Runs in the page.
      const scope = globalThis;
      const bytes = Uint8Array.from(scope.atob(data), (c) => c.charCodeAt(0));
      const bitmap = await scope.createImageBitmap(
        new scope.Blob([bytes], { type: "image/png" }),
        { colorSpaceConversion: "none", premultiplyAlpha: "none" },
      );
      const canvas = new scope.OffscreenCanvas(bitmap.width, bitmap.height);
      canvas.getContext("2d").drawImage(bitmap, 0, 0);
      const blob = await canvas.convertToBlob({
        type: "image/webp",
        quality,
      });
      const output = new Uint8Array(await blob.arrayBuffer());
      let binary = "";
      for (const byte of output) binary += String.fromCharCode(byte);
      return scope.btoa(binary);
    },
    { data: Buffer.from(png).toString("base64"), quality },
  );
  return Buffer.from(encoded, "base64");
}

// One image in its own browser session.
export async function encodeWebp(png, quality) {
  const browser = await launchBrowser();
  try {
    return await encodeOn(await browser.newPage(), png, quality);
  } finally {
    await browser.close();
  }
}

const write = (data) =>
  new Promise((resolve, reject) =>
    process.stdout.write(data, (error) => (error ? reject(error) : resolve())),
  );

// Frames in, frames out, in order, until stdin closes.
async function serve(quality) {
  const browser = await launchBrowser();
  try {
    const page = await browser.newPage();
    let pending = Buffer.alloc(0);
    for await (const chunk of process.stdin) {
      pending = Buffer.concat([pending, chunk]);
      while (
        pending.length >= 4 &&
        pending.length >= 4 + pending.readUInt32BE(0)
      ) {
        const size = pending.readUInt32BE(0);
        const png = pending.subarray(4, 4 + size);
        pending = pending.subarray(4 + size);
        let webp = Buffer.alloc(0);
        try {
          webp = await encodeOn(page, png, quality);
        } catch (error) {
          process.stderr.write(`WebP encoding failed: ${error.message}\n`);
        }
        const header = Buffer.alloc(4);
        header.writeUInt32BE(webp.length);
        await write(Buffer.concat([header, webp]));
      }
    }
    if (pending.length) throw new Error("Truncated WebP request frame");
  } finally {
    await browser.close();
  }
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const quality = Number(process.argv[process.argv.indexOf("--quality") + 1]);
  if (!(quality > 0 && quality <= 1))
    throw new Error("Pass --quality between 0 and 1");
  if (process.argv.includes("--frames")) await serve(quality);
  else {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    process.stdout.write(await encodeWebp(Buffer.concat(chunks), quality));
  }
}
