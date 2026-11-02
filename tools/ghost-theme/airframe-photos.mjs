import fs from "node:fs/promises";
import { decode } from "./html-head.mjs";

// Airframe photos from the Planespotters.net public photo API. The publisher
// (airframe_photos.py) caches the API's JSON privately for at most 24 hours
// and hands the build one photo per story; the build puts each into its
// article page and nowhere else. Readers' browsers load the thumbnail from
// the URL the API returned. The site never copies, proxies, or rewrites it.
// deploy/ghost/Caddyfile allows exactly these image hosts in img-src.
export const AIRFRAME_IMAGE_HOSTS = ["t.plnspttrs.net"];
export const AIRFRAME_LINK_HOST = "www.planespotters.net";
// The proxy's img-src, in order.
export const IMAGE_POLICY = [
  "'self'",
  "data:",
  ...AIRFRAME_IMAGE_HOSTS.map((host) => `https://${host}`),
];
// Photo data belongs in article pages only; no other published file names it.
export const PHOTO_DATA = /planespotters|plnspttrs/i;

const https = (value, hosts) => {
  if (typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      hosts.includes(url.host) &&
      !url.username &&
      !url.password &&
      url.href === value
    );
  } catch {
    return false;
  }
};
const size = (value) => Number.isInteger(value) && value > 0 && value <= 2000;

// Each event's photo from the publisher's file, checked again here: URLs on
// the allowed hosts and unchanged, a size and a photographer, and the
// registration the publisher bound the photo to (airframe_photos.py checks that
// the photo page names it) equal to the event's, so a photo of another
// airframe, however it reached the build, is dropped. Synthetic events, events
// without a registration or outside the snapshot, and anything malformed get
// none.
export async function loadAirframePhotos(file, events) {
  const { photos } = JSON.parse(await fs.readFile(file, "utf8"));
  if (!photos || typeof photos !== "object" || Array.isArray(photos))
    throw new Error("Airframe photo file has no photos object");
  const result = new Map();
  for (const event of events) {
    const photo = Object.hasOwn(photos, event.id) ? photos[event.id] : null;
    const registration = event.aircraft?.registration;
    if (
      event.synthetic === false &&
      typeof registration === "string" &&
      registration &&
      photo &&
      https(photo.src, AIRFRAME_IMAGE_HOSTS) &&
      https(photo.link, [AIRFRAME_LINK_HOST]) &&
      photo.registration === registration &&
      size(photo.width) &&
      size(photo.height) &&
      typeof photo.photographer === "string" &&
      photo.photographer.trim() &&
      photo.photographer.length <= 100
    )
      result.set(event.id, {
        src: photo.src,
        width: photo.width,
        height: photo.height,
        link: photo.link,
        photographer: photo.photographer.trim(),
      });
  }
  return result;
}

// The template's view of a photo, which shows the aircraft, not this flight.
export const airframeFigure = (photo, registration) => ({
  ...photo,
  subject: registration,
  alt: `Photo of ${registration} on Planespotters.net`,
});

// Elements and attributes that make a browser load something. A link loads
// only for these relationships; an anchor or a canonical link does not.
const RESOURCES = {
  img: ["src", "srcset"],
  source: ["src", "srcset"],
  script: ["src", "href", "xlink:href"],
  iframe: ["src"],
  frame: ["src"],
  embed: ["src"],
  object: ["data"],
  video: ["src", "poster"],
  audio: ["src"],
  track: ["src"],
  input: ["src"],
  link: ["href", "imagesrcset"],
  image: ["href", "xlink:href"],
  use: ["href", "xlink:href"],
  feimage: ["href", "xlink:href"],
};
const LOADING_RELS = new Set([
  "stylesheet",
  "preload",
  "modulepreload",
  "prefetch",
  "icon",
  "apple-touch-icon",
  "manifest",
  "preconnect",
  "dns-prefetch",
]);
// What CSS loads: url(...) and @import targets, and the strings in image-set().
const cssUrls = (text) => [
  ...[
    ...text.matchAll(
      /url\(\s*['"]?\s*([^'")\s]+)|@import\s+['"]([^'"]+)['"]/gi,
    ),
  ].map(([, url, imported]) => url ?? imported),
  ...[...text.matchAll(/image-set\(([^)]*)\)/gi)].flatMap(([, inner]) =>
    [...inner.matchAll(/['"]([^'"]+)['"]/g)].map(([, url]) => url),
  ),
];

// Resources a page would load from an origin other than `origin`. The one
// exception, on a story page, is an airframe photo's image from an allowed
// host inside its figure, and the image host may appear nowhere else in the
// page. The same rule as the release guard in airframe_photos.py, which reads
// pages with a full HTML parser; here a reference this simpler reader cannot
// decode counts as foreign.
export function thirdPartyResources(html, origin, { story = false } = {}) {
  const foreign = (value) => {
    // Browsers read backslashes as slashes and drop tabs and newlines.
    const url = value
      .trim()
      .replace(/[\t\n\r]/g, "")
      .replaceAll("\\", "/");
    if (/&(?:[a-z]+;|#)/i.test(url)) return true;
    if (!/^(?:[a-z][a-z0-9+.-]*:|\/\/)/i.test(url) || /^data:/i.test(url))
      return false;
    try {
      return new URL(url, origin).origin !== origin;
    } catch {
      return true;
    }
  };
  const found = [];
  const figures = [];
  let style = false;
  let allowed = 0;
  const tags =
    /<(\/?)([a-z][a-z0-9-]*)\b((?:[^>"']|"[^"]*"|'[^']*')*)>|([^<]+)/gi;
  for (const [, closing, rawName, rawAttributes, text] of html.matchAll(tags)) {
    if (text !== undefined) {
      if (style)
        for (const url of cssUrls(text))
          if (foreign(url)) found.push(`style url ${url}`);
      continue;
    }
    const name = rawName.toLowerCase();
    if (closing) {
      if (name === "figure") figures.pop();
      if (name === "style") style = false;
      continue;
    }
    // Browsers keep the first of two attributes with the same name.
    const attributes = {};
    for (const [, key, double, single, bare] of rawAttributes.matchAll(
      /([^\s=/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g,
    ))
      attributes[key.toLowerCase()] ??= decode(double ?? single ?? bare ?? "");
    if (name === "figure") {
      figures.push(
        (attributes.class || "").split(/\s+/).includes("airframe-photo"),
      );
      if (/\/\s*$/.test(rawAttributes)) figures.pop();
    }
    if (name === "style") style = true;
    for (const url of cssUrls(attributes.style || ""))
      if (foreign(url)) found.push(`style url ${url}`);
    if (
      name === "link" &&
      !(attributes.rel || "")
        .toLowerCase()
        .split(/\s+/)
        .some((rel) => LOADING_RELS.has(rel))
    )
      continue;
    for (const attribute of RESOURCES[name] || []) {
      if (!(attribute in attributes)) continue;
      const urls = attribute.endsWith("srcset")
        ? attributes[attribute]
            .split(",")
            .map((part) => part.trim().split(/\s+/)[0])
            .filter(Boolean)
        : [attributes[attribute]];
      for (const url of urls) {
        if (
          story &&
          name === "img" &&
          attribute === "src" &&
          figures.some(Boolean) &&
          https(url, AIRFRAME_IMAGE_HOSTS)
        )
          allowed += 1;
        else if (foreign(url)) found.push(`<${name} ${attribute}> ${url}`);
      }
    }
  }
  if ((html.match(/plnspttrs/gi) || []).length > allowed)
    found.push("Planespotters image host outside an airframe photo");
  return found;
}

// A 1x1 GIF that answers for an airframe image when a check runs offline.
const STAND_IN = Buffer.from(
  "R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==",
  "base64",
);

// For browser checks: a request outside `isSite` fails the check (it is
// recorded in `failures` and refused), except an image in `images`, the
// airframe photo URLs the pages name. The stand-in answers those, or with
// `live` the request goes out, as a reader's browser would send it. Each
// photo request's Referer, or null, is appended to `referers`: the image
// carries no referrer policy of its own, so the page's Referrer-Policy header
// decides what the photo host learns (the public proxy sends the site's
// origin, the private preview nothing).
export const guardRequests = (
  context,
  { isSite, images, failures, live, referers },
) =>
  context.route(
    (url) => !isSite(url),
    async (route) => {
      const request = route.request();
      if (request.resourceType() === "image" && images.has(request.url())) {
        referers?.push(request.headers().referer ?? null);
        await (live
          ? route.continue()
          : route.fulfill({ body: STAND_IN, contentType: "image/gif" }));
      } else {
        failures.push(`Third-party request: ${request.url()}`);
        await route.abort();
      }
    },
  );
