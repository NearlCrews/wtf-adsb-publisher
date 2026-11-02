// Representative type photos from each aircraft type's Wikidata image (P18),
// or from a Commons file the sources file names, and one representative photo
// for each class of aircraft that cards use when a type has no photo.
//
// node tools/ghost-theme/fetch-type-photos.mjs [--snapshot FILE] [--dry-run]
//
// The tool covers every type code with a Wikipedia article in the story
// engine's type table, every code in data/type-photo-sources.json and, with
// --snapshot, every code in that published snapshot. An article in the sources
// file takes precedence over the type table's. It resolves the article
// to its Wikidata item, reads the item's image and the Commons file's license
// and credit, and keeps only JPEG photographs under CC0, public domain, CC BY
// or CC BY-SA. A code under "files" skips Wikidata and uses that Commons file;
// each entry under "classes" names the file for one class photo. Both pass the
// same checks. The original is downloaded once and checked against Commons'
// SHA-1; the pinned Chromium resizes it to 1200 px and encodes a WebP with no
// metadata beside credits.json, which gains the credit record. A code or
// class that already has a photo is left alone, so a second run changes
// nothing. Requests go one at a time, a second apart, with a descriptive
// User-Agent; --dry-run reads the APIs and reports without downloading
// anything.
//
// Filename, description, and category checks reject diagrams, drawings,
// cockpits, models, and wrecks. They cannot see the picture: check new photos
// by eye, then record a rejection, credit, or alt text in the sources file.

import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { parseArgs } from "node:util";
import prettier from "prettier";
import { fold } from "../../themes/wtf-adsb/assets/js/event-query.mjs";
import { launchBrowser } from "./browser.mjs";
import { decode } from "./html-head.mjs";
import { output, root, theme, tools } from "./paths.mjs";
import { userAgent } from "./site-settings.mjs";

const USER_AGENT = userAgent();
const GAP_MS = 1000;
const WIDTH = 1200;
const MIN_WIDTH = 700;
const MAX_BYTES = 40 * 1024 * 1024;
const QUALITY = 0.8;
const CHANGES =
  "Resized and converted to WebP; embedded camera metadata removed. No crop or generated content.";
const HOSTS = new Set([
  "en.wikipedia.org",
  "www.wikidata.org",
  "commons.wikimedia.org",
  "upload.wikimedia.org",
]);
// Free licenses that allow reuse with changes. Anything else, including GFDL
// alone, the Open Government Licence, NC, and ND, is skipped.
const LICENSE_CODE = /^(?:pd|cc0|cc-by(?:-sa)?-\d\.\d(?:-[a-z]{2,3})?)$/;
const LICENSE_NAME =
  /^(?:Public domain|CC0|CC BY(?:-SA)? \d\.\d(?: [a-z]{2,3})?)$/;
// Words in a filename or description that mark a picture of something other
// than the aircraft itself.
const NOT_PHOTO =
  /\b(?:diagrams?|drawings?|line art|3-?view|three-view|silhouettes?|schematics?|blueprints?|cutaways?|sketch(?:es)?|render(?:ing)?s?|cgi|logos?|emblems?|insignia|cockpits?|flight deck|interiors?|cabin|instrument panel|seats?|scale models?|model aircraft|die-?cast|toys?|replicas?|mock-?ups?|wrecks?|wreckage|crash(?:ed|es)?|accidents?|posters?|stamps?|paintings?|illustrations?|maps?)\b/i;
// Preferred images first; deprecated ones are dropped.
const RANK = { preferred: 0, normal: 1 };
const NOT_PHOTO_CATEGORY =
  /(?:drawings|diagrams|cockpits|interiors|cabins|instrument panels|scale models|line art|3-view|logos|illustrations|renders|CGI|wrecks|crashes|accidents|paintings)/i;

const { values: options } = parseArgs({
  options: {
    snapshot: { type: "string" },
    "dry-run": { type: "boolean", default: false },
  },
});
const dryRun = options["dry-run"];
const photosDir = path.join(theme, "assets/images/aircraft");
const creditsPath = path.join(photosDir, "credits.json");
const sourcesPath = path.join(tools, "data/type-photo-sources.json");
const cacheDir = path.join(output, "type-photo-cache");
const reportPath = path.join(output, "type-photos-report.json");
const readJson = async (file) => JSON.parse(await fs.readFile(file, "utf8"));
const sha = (algorithm, data) =>
  createHash(algorithm).update(data).digest("hex");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// One request at a time, a second apart. Rate limits and server errors are
// retried up to three times, after the server's Retry-After bounded to 5, 10
// or 20 seconds at least and two minutes at most.
let last = 0;
async function request(url, { limit = 8 * 1024 * 1024 } = {}) {
  if (!HOSTS.has(new URL(url).host))
    throw new Error(`Unexpected host in ${url}`);
  for (let attempt = 0; ; attempt += 1) {
    const wait = last + GAP_MS - Date.now();
    if (wait > 0) await sleep(wait);
    last = Date.now();
    const response = await fetch(url, {
      headers: { "User-Agent": USER_AGENT, "Api-User-Agent": USER_AGENT },
      redirect: "error",
      signal: AbortSignal.timeout(120000),
    });
    if ((response.status === 429 || response.status >= 500) && attempt < 3) {
      const seconds = Number(response.headers.get("retry-after")) || 0;
      await sleep(Math.min(Math.max(seconds, 5 * 2 ** attempt), 120) * 1000);
      continue;
    }
    if (!response.ok)
      throw new Error(`HTTP ${response.status} from ${new URL(url).host}`);
    const data = Buffer.from(await response.arrayBuffer());
    if (data.length > limit) throw new Error(`Response too large from ${url}`);
    return data;
  }
}

// A read-only MediaWiki API query. Wikidata's maxlag follows its query
// service, which reads do not touch, so these queries do not send it.
async function api(host, params) {
  const url = `https://${host}/w/api.php?${new URLSearchParams({
    ...params,
    format: "json",
    formatversion: "2",
  })}`;
  const body = JSON.parse((await request(url)).toString());
  if (body.error) throw new Error(`${host}: ${body.error.code}`);
  return body;
}

// A query's normalized titles or redirects, from each title to its target.
const pairs = (list = []) => new Map(list.map((item) => [item.from, item.to]));
const chunks = (list, size) =>
  Array.from({ length: Math.ceil(list.length / size) }, (_, index) =>
    list.slice(index * size, index * size + size),
  );

function articleTitle(url) {
  const parsed = new URL(url);
  if (
    parsed.host !== "en.wikipedia.org" ||
    !parsed.pathname.startsWith("/wiki/")
  )
    throw new Error(`Not an English Wikipedia article: ${url}`);
  return decodeURIComponent(parsed.pathname.slice(6)).replaceAll("_", " ");
}

// Article titles to their resolved title and Wikidata item; disambiguation
// pages and pages without an item resolve to null.
async function resolveArticles(titles) {
  const resolved = new Map();
  for (const batch of chunks(titles, 50)) {
    const { query } = await api("en.wikipedia.org", {
      action: "query",
      prop: "pageprops",
      redirects: "1",
      titles: batch.join("|"),
    });
    const normalized = pairs(query.normalized);
    const redirected = pairs(query.redirects);
    const pages = new Map(query.pages.map((page) => [page.title, page]));
    for (const title of batch) {
      const name = normalized.get(title) || title;
      const page = pages.get(redirected.get(name) || name);
      const props = page?.pageprops || {};
      resolved.set(
        title,
        page &&
          !page.missing &&
          props.wikibase_item &&
          !("disambiguation" in props)
          ? { title: page.title, qid: props.wikibase_item }
          : null,
      );
    }
  }
  return resolved;
}

// Each item's images (P18), in RANK order.
async function itemImages(qids) {
  const images = new Map();
  for (const batch of chunks(qids, 50)) {
    const { entities } = await api("www.wikidata.org", {
      action: "wbgetentities",
      ids: batch.join("|"),
      props: "claims",
    });
    for (const qid of batch) {
      const claims = entities[qid]?.claims?.P18 || [];
      images.set(
        qid,
        claims
          .filter(
            (claim) =>
              claim.rank in RANK &&
              typeof claim.mainsnak?.datavalue?.value === "string",
          )
          .sort((a, b) => RANK[a.rank] - RANK[b.rank])
          .map((claim) => `File:${claim.mainsnak.datavalue.value}`),
      );
    }
  }
  return images;
}

// Commons image information for each file title, under the title asked for.
async function fileInfo(titles) {
  const info = new Map();
  for (const batch of chunks(titles, 50)) {
    const { query } = await api("commons.wikimedia.org", {
      action: "query",
      prop: "imageinfo",
      iiprop: "url|size|mime|sha1|extmetadata",
      iiextmetadatalanguage: "en",
      titles: batch.join("|"),
    });
    const normalized = pairs(query.normalized);
    const pages = new Map(query.pages.map((page) => [page.title, page]));
    for (const title of batch) {
      const page = pages.get(normalized.get(title) || title);
      info.set(title, page?.imageinfo?.[0] || null);
    }
  }
  return info;
}

// The text of a Commons metadata field, which may hold HTML.
const plain = (html) =>
  decode(String(html || "").replace(/<[^>]*>/g, " "))
    .replace(/\s+/g, " ")
    .replace(/\( /g, "(")
    .replace(/ \)/g, ")")
    .trim();

// A credit line from the Commons attribution or author field, or null when it
// is missing, unknown, names only the uploader, or is mixed with license text.
function creditOf(meta) {
  const text = plain(meta.Attribution?.value || meta.Artist?.value).replace(
    /^user:/i,
    "",
  );
  if (
    !text ||
    text.length > 80 ||
    /unknown|uploader|public domain|licen[cs]e|https?:|\.(?:jpe?g|png)\b|\bfalse\b/i.test(
      text,
    )
  )
    return null;
  return text;
}

// The license's page: the file's licensing section for public domain, else
// the Creative Commons legal code. Null for anything else.
function licenseUrl(meta, sourceUrl) {
  if (meta.License?.value === "pd") return `${sourceUrl}#Licensing`;
  if (!URL.canParse(meta.LicenseUrl?.value)) return null;
  const url = new URL(meta.LicenseUrl.value);
  if (url.host !== "creativecommons.org") return null;
  return `https://creativecommons.org${url.pathname.replace(/deed\.[a-z-]+$/, "").replace(/\/?$/, "/")}`;
}

// Whether a Commons file is a free photograph of the aircraft, and its record.
function assess(title, info, sources) {
  if (!info) return { reason: "Commons has no such file" };
  if (sources.rejected[title])
    return { reason: `Rejected on review: ${sources.rejected[title]}` };
  const meta = info.extmetadata || {};
  const code = plain(meta.License?.value);
  const name = plain(meta.LicenseShortName?.value);
  if (!LICENSE_CODE.test(code) || !LICENSE_NAME.test(name))
    return { reason: `License not accepted: ${name || code || "none"}` };
  if (plain(meta.NonFree?.value) === "true" || plain(meta.Restrictions?.value))
    return { reason: "The file carries use restrictions" };
  if (info.mime !== "image/jpeg")
    return { reason: `Not a JPEG photo: ${info.mime}` };
  if (info.width < MIN_WIDTH) return { reason: `Too small: ${info.width} px` };
  const aspect = info.width / info.height;
  if (aspect < 1.2 || aspect > 2.7)
    return { reason: `Not a landscape photo: ${info.width}x${info.height}` };
  if (info.size > MAX_BYTES) return { reason: "Original over 40 MB" };
  const description = plain(meta.ImageDescription?.value);
  if (NOT_PHOTO.test(title) || NOT_PHOTO.test(description))
    return {
      reason:
        "Filename or description names something other than a photo of the aircraft",
    };
  if (NOT_PHOTO_CATEGORY.test(plain(meta.Categories?.value)))
    return {
      reason: "A Commons category marks a drawing, model, cockpit, or wreck",
    };
  const author = sources.authors[title] || creditOf(meta);
  if (!author) return { reason: "No plain author credit" };
  const upload = new URL(info.url);
  const source = new URL(info.descriptionurl);
  if (
    upload.host !== "upload.wikimedia.org" ||
    source.host !== "commons.wikimedia.org"
  )
    return { reason: "Unexpected Commons URL" };
  const license = licenseUrl(meta, info.descriptionurl);
  if (!license) return { reason: "No license page" };
  return {
    record: {
      author,
      sourceTitle: title,
      sourceUrl: info.descriptionurl,
      originalUrl: info.url,
      license: code === "pd" ? "Public domain" : name,
      licenseUrl: license,
      originalSha1: info.sha1,
      bytes: info.size,
    },
  };
}

// The original, once: checked against its Commons SHA-1 and size, then cached
// under that SHA-1, so a damaged download is fetched again.
async function original(record) {
  const file = path.join(cacheDir, `${record.originalSha1}.jpg`);
  const matches = (data) =>
    data &&
    data.length === record.bytes &&
    sha("sha1", data) === record.originalSha1;
  const cached = await fs.readFile(file).catch(() => null);
  if (matches(cached)) return cached;
  const data = await request(record.originalUrl, { limit: MAX_BYTES });
  if (!matches(data))
    throw new Error(`Download does not match Commons: ${record.sourceTitle}`);
  await fs.mkdir(cacheDir, { recursive: true });
  await fs.writeFile(file, data);
  return data;
}

// The pinned Chromium decodes the JPEG (applying its orientation and color
// profile), resizes it and encodes a WebP. The canvas copies no camera
// metadata; simpleWebp drops the sRGB profile Chrome attaches.
async function encode(page, jpeg) {
  await page.route("https://encoder.invalid/source.jpg", (route) =>
    route.fulfill({ body: jpeg, contentType: "image/jpeg" }),
  );
  try {
    return await page.evaluate(
      async ({ width, quality }) => {
        // Runs in the page.
        const scope = globalThis;
        const blob = await (await scope.fetch("/source.jpg")).blob();
        const full = await scope.createImageBitmap(blob);
        const w = Math.min(width, full.width);
        const h = Math.round((full.height * w) / full.width);
        full.close();
        const bitmap = await scope.createImageBitmap(blob, {
          resizeWidth: w,
          resizeHeight: h,
          resizeQuality: "high",
        });
        const canvas = new scope.OffscreenCanvas(w, h);
        canvas.getContext("2d").drawImage(bitmap, 0, 0);
        const out = new Uint8Array(
          await (
            await canvas.convertToBlob({ type: "image/webp", quality })
          ).arrayBuffer(),
        );
        let binary = "";
        for (const byte of out) binary += String.fromCharCode(byte);
        return { data: scope.btoa(binary), width: w, height: h };
      },
      { width: WIDTH, quality: QUALITY },
    );
  } finally {
    await page.unroute("https://encoder.invalid/source.jpg");
  }
}

// Chrome wraps its WebP in an extended header to attach the canvas's sRGB
// profile. Browsers assume sRGB without one, so only the VP8 bitstream is
// kept, in the simple format; an image with alpha or animation is refused.
// raster_webp.normalize_webp does the same for map backgrounds, and
// check-media validates every stored photo with its validate_webp.
function simpleWebp(data) {
  let frame = null;
  for (let offset = 12; offset + 8 <= data.length;) {
    const kind = data.toString("latin1", offset, offset + 4);
    const length = data.readUInt32LE(offset + 4);
    if (kind === "VP8 ") frame = data.subarray(offset, offset + 8 + length);
    else if (kind === "VP8X" && data[offset + 8] & ~0x20) return null;
    else if (!["VP8X", "ICCP"].includes(kind)) return null;
    offset += 8 + length + (length % 2);
  }
  // A VP8 key frame: three tag bytes, then the start code.
  if (!frame || frame.length < 18 || frame.readUIntBE(11, 3) !== 0x9d012a)
    return null;
  const padded = Buffer.concat([frame, Buffer.alloc(frame.length % 2)]);
  const header = Buffer.alloc(12);
  header.write("RIFF", 0, "latin1");
  header.writeUInt32LE(4 + padded.length, 4);
  header.write("WEBP", 8, "latin1");
  return Buffer.concat([header, padded]);
}

const slug = (text) =>
  fold(text)
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
// "Business jet" reads "business jet" inside a sentence.
const lowerFirst = (text) => text.charAt(0).toLowerCase() + text.slice(1);

async function main() {
  const credits = await readJson(creditsPath);
  const sources = await readJson(sourcesPath);
  const names = (
    await readJson(
      path.join(root, "src/wtf_adsb_publisher/data/type_names.json"),
    )
  ).types;
  const covered = new Set(credits.photos.flatMap((photo) => photo.typeCodes));
  const ids = new Set(credits.photos.map((photo) => photo.id));
  const codes = new Set([
    ...Object.keys(names).filter((code) => names[code].same_as),
    ...Object.keys(sources.articles),
    ...Object.keys(sources.files),
    ...Object.keys(sources.skip),
  ]);
  if (options.snapshot)
    for (const event of (await readJson(options.snapshot)).events)
      if (event.aircraft?.type_code) codes.add(event.aircraft.type_code);
  const skipped = [];
  const wanted = new Map();
  // Types and classes whose Commons file the sources file names.
  const named = [];
  for (const code of [...codes].sort()) {
    if (covered.has(code)) continue;
    const file = sources.files[code];
    if (file && (sources.skip[code] || sources.articles[code]))
      throw new Error(`${code} has a file and also an article or a skip`);
    // The photo's title: the sources file's, else the type table's name.
    const title = sources.titles[code] || names[code]?.name;
    // The sources file can name a variant or family article whose image is
    // usable when the type's own is not.
    const article = sources.articles[code] || names[code]?.same_as;
    if (file && title) named.push({ codes: [code], title, file });
    else if (file)
      skipped.push({ codes: [code], reason: "No title for the type" });
    else if (sources.skip[code])
      skipped.push({ codes: [code], reason: sources.skip[code] });
    else if (!article)
      skipped.push({
        codes: [code],
        reason: "No Wikipedia article for the type",
      });
    else wanted.set(code, articleTitle(article));
  }
  const articles = await resolveArticles([...new Set(wanted.values())]);
  // Codes that share a Wikidata item share one photo.
  const items = new Map();
  for (const [code, title] of wanted) {
    const article = articles.get(title);
    if (!article) {
      skipped.push({ codes: [code], reason: `No Wikidata item for ${title}` });
      continue;
    }
    if (!items.has(article.qid))
      items.set(article.qid, { ...article, codes: [] });
    items.get(article.qid).codes.push(code);
  }
  for (const [key, spec] of Object.entries(sources.classes)) {
    if (!spec.title || !spec.file || !spec.classes?.length)
      throw new Error(`Class ${key} needs a title, a file, and classes`);
    const id = `class-${key}`;
    if (!ids.has(id))
      named.push({
        id,
        codes: [],
        classes: spec.classes,
        categories: spec.categories,
        title: spec.title,
        file: spec.file,
      });
  }
  const images = await itemImages([...items.keys()]);
  const info = await fileInfo([
    ...new Set([
      ...[...images.values()].flat(),
      ...named.map((item) => item.file),
    ]),
  ]);
  const chosen = [];
  for (const item of named) {
    const result = assess(item.file, info.get(item.file), sources);
    if (result.record) chosen.push({ ...item, record: result.record });
    else
      skipped.push({
        ...(item.classes ? { class: item.id } : { codes: item.codes }),
        reason: `${item.file}: ${result.reason}`,
      });
  }
  for (const [qid, item] of items) {
    const reasons = [];
    let found = null;
    for (const file of images.get(qid)) {
      const result = assess(file, info.get(file), sources);
      if (result.record) {
        found = result.record;
        break;
      }
      reasons.push(`${file}: ${result.reason}`);
    }
    if (found) chosen.push({ ...item, record: found });
    else
      skipped.push({
        codes: item.codes,
        reason: reasons.join("; ") || `${item.title} has no Wikidata image`,
      });
  }
  const added = [];
  if (!dryRun && chosen.length) {
    const browser = await launchBrowser();
    try {
      const page = await browser.newPage();
      await page.route("https://encoder.invalid/", (route) =>
        route.fulfill({
          body: "<!doctype html><title>encoder</title>",
          contentType: "text/html",
        }),
      );
      await page.goto("https://encoder.invalid/");
      const ids = new Set(credits.photos.map((photo) => photo.id));
      for (const item of chosen) {
        const { record } = item;
        const jpeg = await original(record);
        const encoded = await encode(page, jpeg);
        const webp = simpleWebp(Buffer.from(encoded.data, "base64"));
        if (!webp)
          throw new Error(
            `Encoded WebP failed its checks: ${record.sourceTitle}`,
          );
        let id = item.id || slug(item.title);
        if (!item.id && ids.has(id)) id = `${id}-${slug(item.codes[0])}`;
        ids.add(id);
        const digest = sha("sha256", webp);
        const src = `/assets/images/aircraft/${id}-${digest.slice(0, 12)}.webp`;
        await fs.writeFile(path.join(theme, src), webp);
        const reviewed = sources.descriptions[record.sourceTitle] || {};
        // A class photo shows another type: "Representative business jet".
        const subject = item.classes ? lowerFirst(item.title) : item.title;
        const entry = {
          id,
          typeCodes: [...item.codes].sort(),
          ...(item.classes && {
            classes: item.classes,
            ...(item.categories && { categories: item.categories }),
          }),
          title: item.title,
          src,
          width: encoded.width,
          height: encoded.height,
          alt:
            reviewed.alt ||
            (item.classes
              ? `Representative ${subject}, not the aircraft in this story.`
              : `Representative ${item.title} photo from Wikimedia Commons.`),
          caption:
            reviewed.caption ||
            `Representative ${subject} photo${reviewed.shown ? ` (${reviewed.shown} shown)` : ""}.`,
          author: record.author,
          sourceTitle: record.sourceTitle,
          sourceUrl: record.sourceUrl,
          originalUrl: record.originalUrl,
          license: record.license,
          licenseUrl: record.licenseUrl,
          changes: CHANGES,
          originalSha1: record.originalSha1,
          originalSha256: sha("sha256", jpeg),
          sha256: digest,
          bytes: webp.length,
          // A photo found through Wikidata names its item and article.
          ...(item.qid && {
            wikidata: item.qid,
            article: `https://en.wikipedia.org/wiki/${encodeURIComponent(item.title.replaceAll(" ", "_")).replaceAll("%2F", "/")}`,
          }),
        };
        credits.photos.push(entry);
        added.push({
          id,
          typeCodes: entry.typeCodes,
          ...(entry.classes && { classes: entry.classes }),
          reviewed: Boolean(reviewed.alt),
        });
      }
    } finally {
      await browser.close();
    }
    credits.checkedAt = new Date().toISOString().slice(0, 10);
    const text = JSON.stringify(credits, null, 2);
    const formatted = await prettier.format(text, {
      ...(await prettier.resolveConfig(creditsPath)),
      filepath: creditsPath,
    });
    await fs.writeFile(creditsPath, formatted);
  }
  const report = {
    dryRun,
    added,
    wouldAdd: dryRun
      ? chosen.map((item) => ({
          ...(item.classes ? { class: item.id } : { codes: item.codes }),
          file: item.record.sourceTitle,
          license: item.record.license,
          author: item.record.author,
        }))
      : undefined,
    skipped,
  };
  await fs.mkdir(output, { recursive: true });
  await fs.writeFile(reportPath, JSON.stringify(report, null, 2));
  console.log(
    `${dryRun ? `Would add ${chosen.length}` : `Added ${added.length}`} photos; skipped ${skipped.length} types or classes. Report: ${path.relative(root, reportPath)}`,
  );
  if (added.some((item) => !item.reviewed))
    console.log(
      "Check each new photo by eye, then add its alt text to data/type-photo-sources.json or reject it there.",
    );
}

await main();
