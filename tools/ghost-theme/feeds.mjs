import { BRAND, compareId, time } from "./site-model.mjs";

// The sitemap index, its child sitemaps, the story feed, and robots.txt.
export const SITEMAP_SECTIONS = [
  "stories",
  "aircraft",
  "operators",
  "topics",
  "pages",
];
export const FEED_SIZE = 50;
// Snapshot generations whose URLs never change content: the journal's data and
// pages and the content-addressed media. Preview builds keep them, and the
// release receiver retains older generations (receive-autopilot.py IMMUTABLE).
export const IMMUTABLE_DIRECTORIES = [
  "event-data",
  "event-pages",
  "event-media",
];
// robots.txt keeps crawlers out of Ghost, the journal's data generations and
// the search index, and only out of them.
export const DISALLOWED = [
  "/ghost/",
  "/event-data/",
  "/event-pages/",
  "/search-index/",
];

const xml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
const newest = (values) =>
  values
    .filter(Boolean)
    .sort((a, b) => time(b) - time(a))
    .at(0);

// entries: {section: [{url, lastmod}]}; only indexable pages belong here.
export function sitemaps(origin, entries) {
  const files = {};
  const index = [];
  for (const section of SITEMAP_SECTIONS) {
    const urls = [...(entries[section] || [])].sort((a, b) =>
      compareId(a.url, b.url),
    );
    if (urls.some((entry) => !entry.lastmod))
      throw new Error(`Sitemap entry without lastmod in ${section}`);
    const name = `sitemap-${section}.xml`;
    files[name] =
      `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
      urls
        .map(
          (entry) =>
            `<url><loc>${xml(new URL(entry.url, origin).href)}</loc><lastmod>${xml(entry.lastmod)}</lastmod></url>\n`,
        )
        .join("") +
      "</urlset>\n";
    const lastmod = newest(urls.map((entry) => entry.lastmod));
    if (lastmod)
      index.push(
        `<sitemap><loc>${xml(new URL(`/${name}`, origin).href)}</loc><lastmod>${xml(lastmod)}</lastmod></sitemap>\n`,
      );
  }
  files["sitemap.xml"] =
    `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${index.join("")}</sitemapindex>\n`;
  return files;
}

export function robots(origin) {
  return [
    "User-agent: *",
    "Allow: /",
    ...DISALLOWED.map((route) => `Disallow: ${route}`),
    `Sitemap: ${origin}/sitemap.xml`,
    "",
  ].join("\n");
}

const rfc822 = (value) => new Date(value).toUTCString();

// The latest stories by release time; the GUID is the stable event ID. The
// feed is for readers, so it lists every published story, including those
// below the indexing gate: that gate decides what search engines index, not
// what subscribers read. Search engines still read each page's noindex.
export function rss(origin, stories, { description }) {
  const released = (story) =>
    story.releasedAt || story.datePublished || story.observedStart;
  const items = [...stories]
    .sort(
      (a, b) => time(released(b)) - time(released(a)) || compareId(a.id, b.id),
    )
    .slice(0, FEED_SIZE);
  const built = newest(items.map(released)) || new Date(0).toISOString();
  return (
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n` +
    `<title>${xml(BRAND)}</title>\n<link>${xml(`${origin}/`)}</link>\n` +
    `<description>${xml(description)}</description>\n<language>en</language>\n` +
    `<lastBuildDate>${rfc822(built)}</lastBuildDate>\n` +
    `<atom:link href="${xml(`${origin}/rss/`)}" rel="self" type="application/rss+xml"/>\n` +
    items
      .map(
        (story) =>
          `<item><title>${xml(story.headline)}</title><link>${xml(new URL(story.url, origin).href)}</link>` +
          `<guid isPermaLink="false">${xml(story.id)}</guid><pubDate>${rfc822(released(story))}</pubDate>` +
          `<description>${xml(story.dek)}</description>` +
          `<category>${xml(story.topicHub.title)}</category></item>\n`,
      )
      .join("") +
    `</channel>\n</rss>\n`
  );
}
