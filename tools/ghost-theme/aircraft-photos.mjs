import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import Handlebars from "handlebars";

const readJson = async (relative) =>
  JSON.parse(await fs.readFile(new URL(relative, import.meta.url), "utf8"));

// Curated local assets, with their license record included in the theme ZIP.
// Builds never fetch photos or turn tracking-page links into image URLs.
const manifest = await readJson(
  "../../themes/wtf-adsb/assets/images/aircraft/credits.json",
);
export const aircraftPhotos = manifest.photos;
// Class photos stand in on cards for a type that has no photo of its own.
export const classPhotos = aircraftPhotos.filter((photo) => photo.classes);
const typePhotos = aircraftPhotos.filter((photo) => !photo.classes);
const photosByType = new Map(
  typePhotos.flatMap((photo) => photo.typeCodes.map((code) => [code, photo])),
);

// Canonical JSON (sorted keys, compact, ASCII), the form whose SHA-256 the
// story engine's pinned data files record (reference.canonical_hash).
export const canonical = (value) =>
  Array.isArray(value)
    ? `[${value.map(canonical).join(",")}]`
    : value && typeof value === "object"
      ? `{${Object.keys(value)
          .sort()
          .map((key) => `${canonical(key)}:${canonical(value[key])}`)
          .join(",")}}`
      : JSON.stringify(value).replace(
          /[\u0080-\uffff]/g,
          (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`,
        );
// Data, refused when its content no longer matches the hash it records.
function verified(data, name) {
  const { content_sha256: recorded, ...content } = data;
  if (
    createHash("sha256").update(canonical(content)).digest("hex") !== recorded
  )
    throw new Error(`${name} does not match its recorded content hash`);
  return data;
}
// One of the story engine's bundled data files.
const pinned = async (name) =>
  verified(await readJson(`../../src/wtf_adsb_publisher/data/${name}`), name);

// Each designator's ICAO Doc 8643 description ("L2J": landplane, two jet
// engines) and the reference list of gliders come from the story engine's
// aircraft-type table. It does not ship: the operator supplies it in the
// reference directory, WTF_ADSB_REFERENCE_DIR, which the publisher sets from
// reference_dir in publisher.json. Without a readable, verified table no
// designator has a class, so no card gets a class photo.
const TYPE_TABLE = "reference-aircraft-types-v1.json";
// The content hash the reviewed table records. reference.FILES pins the same
// file by its bytes; the checks pass their fixture table's hash instead.
export const TYPE_TABLE_SHA256 =
  "cc56fabbf7787c0b47c706be382757f097ad2e6e6c0aa1acbf3b74ad730c521f";
let typeTable = null;
let gliders = new Set();

const plainObject = (value) =>
  value !== null &&
  typeof value === "object" &&
  Object.getPrototypeOf(value) === Object.prototype;

// Read the type table from `directory`; false, with a warning when a
// directory was named, if it is missing, malformed, or not the table whose
// recorded content hash is `expected`.
export async function loadTypeTable(
  directory = process.env.WTF_ADSB_REFERENCE_DIR,
  expected = TYPE_TABLE_SHA256,
) {
  typeTable = null;
  gliders = new Set();
  if (!directory?.trim()) return false;
  const file = path.join(directory, TYPE_TABLE);
  try {
    const data = verified(JSON.parse(await fs.readFile(file, "utf8")), file);
    if (data.content_sha256 !== expected)
      throw new Error("not the reviewed table");
    if (data.schema_version !== 1) throw new Error("unsupported schema");
    if (!plainObject(data.types) || !Array.isArray(data.glider_types))
      throw new Error("malformed table");
    gliders = new Set(data.glider_types);
    typeTable = data;
    return true;
  } catch (error) {
    console.warn(
      `${TYPE_TABLE} unavailable; no class photos (${error.code ?? error.message})`,
    );
    return false;
  }
}
await loadTypeTable();
// The curated categories ("business jet", "airliner") of the commonest types.
const typeNames = (await pinned("type_names.json")).types;

// A designator's description and category, or null when the table does not
// list it or is unavailable. A glider the curated table leaves out is still a
// glider.
export function typeClass(code) {
  if (
    typeof code !== "string" ||
    !typeTable ||
    !Object.hasOwn(typeTable.types, code)
  )
    return null;
  return {
    description: typeTable.types[code][0],
    category:
      (Object.hasOwn(typeNames, code) && typeNames[code].category) ||
      (gliders.has(code) ? "glider" : null),
  };
}

// The class photo for a designator. A photo that lists the type's category
// as well as its description wins over one that lists descriptions only.
// Jet photos list categories, since the description alone cannot tell a
// business jet from an airliner; a jet without a category gets none.
export function classPhotoFor(code) {
  const type = typeClass(code);
  if (!type) return null;
  const fits = (photo) => photo.classes.includes(type.description);
  return (
    classPhotos.find(
      (photo) => fits(photo) && photo.categories?.includes(type.category),
    ) ||
    classPhotos.find((photo) => fits(photo) && !photo.categories) ||
    null
  );
}

export const CREDITS_PATH = "/about/image-credits/";

const figure = (photo, kind) => ({
  src: photo.src,
  alt: photo.alt,
  kind,
  caption: photo.caption,
  width: photo.width,
  height: photo.height,
  type: "image/webp",
  creditPath: `${CREDITS_PATH}#${photo.id}`,
});

// A representative photo of the event's aircraft type, for "About the type",
// the social image and, when no map or chart exists, the lead visual.
export function aircraftPhoto(event) {
  if (event.synthetic !== false || event.media) return null;
  const photo = photosByType.get(event.aircraft?.type_code);
  return photo ? figure(photo, "aircraft-type-photo") : null;
}

// A representative photo of the type's class, for cards whose story has no
// map, chart, or type photo. It shows another aircraft, and its alt text,
// caption, and card note say so. Article pages never show it.
export function classPhoto(event) {
  if (event.synthetic !== false || event.media) return null;
  const photo = classPhotoFor(event.aircraft?.type_code);
  return photo
    ? {
        ...figure(photo, "aircraft-class-photo"),
        note: `Representative ${photo.title.charAt(0).toLowerCase()}${photo.title.slice(1)}`,
      }
    : null;
}

const escape = Handlebars.escapeExpression;
const externalLink = (url, label) =>
  `<a href="${escape(url)}" rel="external noopener noreferrer">${escape(label)}</a>`;
const byTitle = (a, b) => a.title.localeCompare(b.title, "en");
const credit = (photo, heading) =>
  `<section aria-labelledby="${escape(photo.id)}"><${heading} id="${escape(photo.id)}">${escape(photo.title)}</${heading}><figure><img src="${escape(photo.src)}" alt="${escape(photo.alt)}" width="${photo.width}" height="${photo.height}" loading="lazy" /><figcaption>${escape(photo.caption)}</figcaption></figure><p>Photo: ${escape(photo.author)}. Source: ${externalLink(photo.sourceUrl, photo.sourceTitle.replace(/^File:/, ""))} on Wikimedia Commons · ${externalLink(photo.licenseUrl, photo.license)}.</p><p>${escape(photo.changes)}</p></section>`;

export const imageCreditsPage = {
  slug: "image-credits",
  url: CREDITS_PATH,
  // The date of the last content change, and that content's pageDigest.
  updated: "2026-11-04T00:00:00Z",
  contentSha256:
    "59c8961d542e3dd4483e5231aacf893ffbbe6cf2a63dcd9f10354dff4dcb8b97",
  title: "Aircraft photo credits",
  custom_excerpt:
    "Sources, photographers, and licenses for the representative aircraft photos.",
  html:
    "<p>These photos illustrate aircraft types. They are not verified photographs of the aircraft or flights in the observation cards. A type code can cover several variants; captions identify the variant pictured where needed.</p>" +
    "<p>Most are the image on the type’s Wikidata entry, used only when Wikimedia Commons lists it under CC0, the public domain, CC BY, or CC BY-SA. The images are served locally. The copies below retain their individual licenses; the theme’s code license does not apply to them. Photographers and licensors do not endorse this site.</p>" +
    "<p>A story page can also show a photo of the aircraft itself from Planespotters.net. Those images are not copied here: your browser loads each one from Planespotters.net, and it names its photographer and links to its page there.</p>" +
    [...typePhotos]
      .sort(byTitle)
      .map((photo) => credit(photo, "h2"))
      .join("\n") +
    '\n<section aria-labelledby="class-photos"><h2 id="class-photos">Representative class photos</h2>' +
    "<p>A story card uses one of these when its aircraft type has no photo above. The type’s ICAO description, which gives its number and kind of engines, picks the photo; for a jet, so does whether the type is listed as a business jet or an airliner. Each caption names the model pictured, which may differ from the type in the story, and the photo is never the aircraft in the story. Story pages do not show these photos.</p>" +
    [...classPhotos]
      .sort(byTitle)
      .map((photo) => credit(photo, "h3"))
      .join("\n") +
    "</section>",
};
