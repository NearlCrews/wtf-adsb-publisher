import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { launchBrowser } from "./browser.mjs";
import { BROWSER, MARK } from "./social-cards.mjs";

// Root icons for production releases. Browsers and crawlers ask for
// /favicon.ico whatever a page's <link rel="icon"> says, and iOS asks for
// /apple-touch-icon.png. Both are drawn from the site mark with the pinned
// Chromium: the ICO holds 16, 32 and 48 pixel frames with transparent corners,
// stored as PNG (valid in ICO files since Windows Vista and read by every
// current browser); the touch icon is an opaque 180 pixel square, since iOS
// rounds its corners itself. Results are cached by the mark, this recipe and
// the browser build, so an unchanged release writes identical bytes.

export const RECIPE = "site-icons-v1";
export const FAVICON_SIZES = [16, 32, 48];
export const TOUCH_SIZE = 180;
const BACKGROUND = "#20312d";
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const sha256 = (data) => createHash("sha256").update(data).digest("hex");

function page(size, opaque) {
  const source = `data:image/svg+xml;base64,${Buffer.from(MARK).toString("base64")}`;
  const background = opaque ? `background:${BACKGROUND}` : "background:none";
  return `<!doctype html><html><head><style>html,body{margin:0;overflow:hidden;${background}}img{display:block;width:${size}px;height:${size}px}</style></head><body><img alt="" src="${source}"></body></html>`;
}

// An ICO file whose frames are PNG images: a 6-byte header, a 16-byte
// directory entry per frame, then the frames.
export function packIco(frames) {
  const header = Buffer.alloc(6 + 16 * frames.length);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(frames.length, 4);
  let offset = header.length;
  frames.forEach(({ size, data }, index) => {
    const entry = 6 + 16 * index;
    header.writeUInt8(size % 256, entry);
    header.writeUInt8(size % 256, entry + 1);
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(data.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += data.length;
  });
  return Buffer.concat([header, ...frames.map((frame) => frame.data)]);
}

// The frames of an ICO file: [{width, height, data}], or null when the file
// is not an ICO whose frames are all PNG images of the sizes it declares.
export function readIco(data) {
  if (
    data.length < 6 ||
    data.readUInt16LE(0) !== 0 ||
    data.readUInt16LE(2) !== 1
  )
    return null;
  const count = data.readUInt16LE(4);
  if (!count || data.length < 6 + 16 * count) return null;
  const frames = [];
  for (let index = 0; index < count; index += 1) {
    const entry = 6 + 16 * index;
    const length = data.readUInt32LE(entry + 8);
    const offset = data.readUInt32LE(entry + 12);
    const frame = data.subarray(offset, offset + length);
    if (
      frame.length !== length ||
      length < 24 ||
      !frame.subarray(0, 8).equals(PNG)
    )
      return null;
    const width = frame.readUInt32BE(16);
    const height = frame.readUInt32BE(20);
    if ((data[entry] || 256) !== width || (data[entry + 1] || 256) !== height)
      return null;
    frames.push({ width, height, data: frame });
  }
  return frames;
}

const pngSide = (data) =>
  data.length >= 24 &&
  data.subarray(0, 8).equals(PNG) &&
  data.readUInt32BE(16) === data.readUInt32BE(20)
    ? data.readUInt32BE(16)
    : null;

function valid(favicon, touch) {
  const frames = favicon && readIco(favicon);
  return (
    frames?.map((frame) => frame.width).join() === FAVICON_SIZES.join() &&
    touch &&
    pngSide(touch) === TOUCH_SIZE
  );
}

// Returns {"favicon.ico": Buffer, "apple-touch-icon.png": Buffer}.
export async function renderSiteIcons({ cache }) {
  const key = sha256(
    JSON.stringify([
      RECIPE,
      MARK,
      BROWSER,
      page.toString(),
      packIco.toString(),
    ]),
  );
  const files = {
    "favicon.ico": path.join(cache, `icons-${key}.ico`),
    "apple-touch-icon.png": path.join(cache, `icons-${key}.png`),
  };
  const read = (file) => fs.readFile(file).catch(() => null);
  const cached = {
    "favicon.ico": await read(files["favicon.ico"]),
    "apple-touch-icon.png": await read(files["apple-touch-icon.png"]),
  };
  if (valid(cached["favicon.ico"], cached["apple-touch-icon.png"]))
    return cached;

  let browser;
  try {
    browser = await launchBrowser();
  } catch (error) {
    throw new Error(
      `Site icons need the pinned browser (npm --prefix tools/ghost-theme run browser:install): ${error.message.split("\n")[0]}`,
      { cause: error },
    );
  }
  const result = {};
  try {
    const context = await browser.newContext({ deviceScaleFactor: 1 });
    const tab = await context.newPage();
    async function draw(size, opaque) {
      await tab.setViewportSize({ width: size, height: size });
      await tab.setContent(page(size, opaque), { waitUntil: "load" });
      // Runs in the page: wait until the mark is decoded.
      await tab.evaluate(() => globalThis.document.images[0].decode());
      return tab.screenshot({
        type: "png",
        clip: { x: 0, y: 0, width: size, height: size },
        omitBackground: !opaque,
        animations: "disabled",
      });
    }
    const frames = [];
    for (const size of FAVICON_SIZES)
      frames.push({ size, data: await draw(size, false) });
    result["favicon.ico"] = packIco(frames);
    result["apple-touch-icon.png"] = await draw(TOUCH_SIZE, true);
    await context.close();
  } finally {
    await browser.close();
  }
  if (!valid(result["favicon.ico"], result["apple-touch-icon.png"]))
    throw new Error("The pinned browser drew site icons of the wrong size");
  await fs.mkdir(cache, { recursive: true });
  for (const [name, file] of Object.entries(files)) {
    // A unique temporary name lets concurrent builds share the cache.
    const temporary = `${file}.${process.pid}.${randomBytes(6).toString("hex")}.tmp`;
    await fs.writeFile(temporary, result[name]);
    await fs.rename(temporary, file);
  }
  return result;
}
