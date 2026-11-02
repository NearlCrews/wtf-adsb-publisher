import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import Handlebars from "handlebars";
import { executablePath, launchBrowser } from "./browser.mjs";
import browserVersion from "./browser-version.json" with { type: "json" };
import { SITE_NAME } from "./site-settings.mjs";

// Raster images for social previews, structured data, and home thumbnails.
// Social networks do not render SVG, so the build draws each story's track,
// chart or summary SVG into a 1200x630 PNG with the pinned Chromium, and into
// a 720x480 JPEG thumbnail for the home page. Images are cached by input
// digest and published under /social/ by output digest, so a URL never
// changes content. Card text is the site's name and tagline; headlines are never drawn,
// because an AI prose upgrade must not change a story's image. Uncached
// images draw on a few tabs at once, each holding one page with the font
// loaded, so a job swaps in only its own styles and figure.

export const RECIPE = "social-card-v2";
// Tabs drawing uncached images at once.
const TABS = 3;
export const SIZES = {
  card: { width: 1200, height: 630 },
  logo: { width: 512, height: 512 },
  tile: { width: 720, height: 480 },
};
const TILE_QUALITY = 80;
const escape = Handlebars.escapeExpression;
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const sha256 = (data) => createHash("sha256").update(data).digest("hex");
// A browser override has no pinned version, so its path keys the cache.
export const BROWSER = process.env.CHROMIUM_PATH
  ? `path:${executablePath}`
  : browserVersion.version;

function pngSize(data) {
  if (data.length < 24 || !data.subarray(0, 8).equals(PNG)) return null;
  return { width: data.readUInt32BE(16), height: data.readUInt32BE(20) };
}

// The frame size from a baseline or progressive JPEG's SOF segment.
function jpegSize(data) {
  if (data.length < 4 || data[0] !== 0xff || data[1] !== 0xd8) return null;
  let offset = 2;
  while (offset + 9 < data.length) {
    if (data[offset] !== 0xff) return null;
    const marker = data[offset + 1];
    const length = data.readUInt16BE(offset + 2);
    if (marker >= 0xc0 && marker <= 0xc2)
      return {
        height: data.readUInt16BE(offset + 5),
        width: data.readUInt16BE(offset + 7),
      };
    offset += 2 + length;
  }
  return null;
}

// The site mark: the favicon, the logo image, and the root icons.
export const MARK =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="5" fill="#20312d"/><path d="m5 9 5 15 6-10 6 10 5-15" fill="none" stroke="#f2eddb" stroke-width="2.5"/><circle cx="26" cy="25" r="2" fill="#ffa685"/></svg>';
const figure = (svg) =>
  `data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}`;

// The page every tab holds: the font and the styles all images share. A job
// fills the second stylesheet and the body.
function shell(font) {
  const face = font
    ? `@font-face{font-family:Newsreader;src:url(data:font/ttf;base64,${font.toString("base64")}) format("truetype");font-weight:400 600}`
    : "";
  return `<!doctype html><html><head><style>${face}html,body{margin:0;overflow:hidden}body{font-family:Newsreader,Georgia,serif}</style><style id="job"></style></head><body></body></html>`;
}

// One image's own styles and markup.
export function drawing(job) {
  if (job.kind === "logo")
    return {
      style: "img{display:block;width:512px;height:512px}",
      body: `<img alt="" src="${figure(MARK)}">`,
    };
  if (job.kind === "site")
    return {
      style:
        "body{width:1200px;height:630px;background:#20312d;color:#f2eddb;display:flex;flex-direction:column;justify-content:center;padding:0 96px;box-sizing:border-box}h1{font-size:132px;font-weight:600;letter-spacing:-0.03em;margin:0}h1 span{color:#ffa685}p{font-size:46px;margin:24px 0 0;color:#d8dccf;max-width:900px;line-height:1.2}",
      body: `<h1>${escape(SITE_NAME)}<span>.</span></h1><p>${escape(job.tagline)}</p>`,
    };
  // A home thumbnail: the 1200x800 figure alone, scaled to 720x480.
  if (job.kind === "tile")
    return {
      style:
        "body{width:720px;height:480px;background:#eceee4}img{display:block;width:720px;height:480px}",
      body: `<img alt="" src="${figure(job.svg)}">`,
    };
  // A story card: the 1200x800 figure scaled to the card height, centred on
  // the figure's own paper colour, with a vertical wordmark in the margin.
  return {
    style:
      "body{width:1200px;height:630px;background:#eceee4;position:relative}img{position:absolute;left:127px;top:0;width:945px;height:630px}b{position:absolute;left:64px;top:315px;transform:translate(-50%,-50%) rotate(-90deg);font-size:34px;font-weight:600;color:#193e39;white-space:nowrap;letter-spacing:0.02em}",
    body: `<b>${escape(SITE_NAME)}</b><img alt="" src="${figure(job.svg)}">`,
  };
}

// The page source stands in for the drawing recipe, so any change to its
// markup or styles renders new images.
const DRAWING = sha256(shell.toString() + drawing.toString() + MARK);

export const fontDigest = (font) => (font ? sha256(font) : null);

// Everything that decides the pixels: the job, the site name, the font (by its
// digest), the page markup and the browser build.
export function jobKey(job, digest = null) {
  return sha256(
    JSON.stringify([
      RECIPE,
      DRAWING,
      BROWSER,
      digest,
      SITE_NAME,
      job.kind,
      job.svg ? sha256(job.svg) : job.tagline || "",
    ]),
  );
}

const output = (kind) =>
  kind === "tile"
    ? { extension: "jpg", type: "image/jpeg", size: jpegSize }
    : { extension: "png", type: "image/png", size: pngSize };

// jobs: [{id, kind: "card"|"site"|"logo"|"tile", svg?, tagline?}]. Returns
// Map(id → {src, width, height, type, data}) with an entry for every job, or
// throws when the pinned browser cannot draw the uncached ones.
export async function renderSocialCards(jobs, { cache, font }) {
  const results = new Map();
  const pending = [];
  const digest = fontDigest(font);
  await fs.mkdir(cache, { recursive: true });
  for (const job of jobs) {
    const size = SIZES[job.kind] || SIZES.card;
    const format = output(job.kind);
    const file = path.join(cache, `${jobKey(job, digest)}.${format.extension}`);
    const cached = await fs.readFile(file).catch(() => null);
    const dimensions = cached && format.size(cached);
    if (dimensions?.width === size.width && dimensions?.height === size.height)
      results.set(job.id, entry(cached, size, format));
    else pending.push({ job, file, size, format });
  }
  if (!pending.length) return results;
  let browser;
  try {
    browser = await launchBrowser();
  } catch (error) {
    throw new Error(
      `Social images need the pinned browser (npm --prefix tools/ghost-theme run browser:install): ${error.message.split("\n")[0]}`,
      { cause: error },
    );
  }
  try {
    const context = await browser.newContext({ deviceScaleFactor: 1 });
    const queue = [...pending];
    async function draw(tab) {
      await tab.setContent(shell(font), { waitUntil: "load" });
      // Runs in the page: load the embedded font once for every job.
      await tab.evaluate(async () => {
        const fonts = globalThis.document.fonts;
        await Promise.all(
          ["400 16px Newsreader", "600 16px Newsreader"].map((face) =>
            fonts.load(face),
          ),
        );
      });
      for (let item = queue.shift(); item; item = queue.shift()) {
        const { job, file, size, format } = item;
        await tab.setViewportSize(size);
        // Runs in the page: swap in the job, then wait for its layout, font
        // and figure.
        await tab.evaluate(async ({ style, body }) => {
          const page = globalThis.document;
          page.getElementById("job").textContent = style;
          page.body.innerHTML = body;
          page.body.getBoundingClientRect();
          await page.fonts.ready;
          await Promise.all(
            [...page.images].map((image) => image.decode().catch(() => {})),
          );
        }, drawing(job));
        const data = await tab.screenshot({
          type: format.extension === "jpg" ? "jpeg" : "png",
          ...(format.extension === "jpg" ? { quality: TILE_QUALITY } : {}),
          clip: { x: 0, y: 0, ...size },
          animations: "disabled",
        });
        // A unique temporary name lets concurrent builds share the cache.
        const temporary = `${file}.${process.pid}.${randomBytes(6).toString("hex")}.tmp`;
        await fs.writeFile(temporary, data);
        await fs.rename(temporary, file);
        results.set(job.id, entry(data, size, format));
      }
    }
    await Promise.all(
      Array.from({ length: Math.min(TABS, pending.length) }, async () =>
        draw(await context.newPage()),
      ),
    );
    await context.close();
  } finally {
    await browser.close();
  }
  return results;
}

function entry(data, size, format) {
  return {
    src: `/social/${sha256(data)}.${format.extension}`,
    type: format.type,
    data,
    ...size,
  };
}
