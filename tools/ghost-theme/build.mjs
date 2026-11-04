import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { parseArgs } from "node:util";
import Handlebars from "handlebars";
import {
  author,
  guideAnchors,
  pages,
  siteNotes,
  stories as demoStories,
} from "./demo-content.mjs";
import {
  createJournal,
  dateLabel,
  exampleSnapshot,
  timeLabel,
} from "./journal.mjs";
import { illustrations, makeTrack } from "./illustrations.mjs";
import {
  aircraftPhoto,
  classPhoto,
  imageCreditsPage,
  loadTypeTable,
} from "./aircraft-photos.mjs";
import { airframeFigure, loadAirframePhotos } from "./airframe-photos.mjs";
import { loadEventMedia } from "./media.mjs";
import { preview, root, output, theme, tools, topicsFile } from "./paths.mjs";
import {
  QUERY_VERSION,
  archiveResult,
} from "../../themes/wtf-adsb/assets/js/event-query.mjs";
import { weeklyView } from "./weekly.mjs";
import {
  BRAND,
  HOME_LATEST,
  TYPE_BLOCK,
  WEEKLY_PER_PAGE,
  cardVisual,
  compareText,
  datedGroups,
  pageLinks,
  pageUrl,
  paginate,
  shortDate,
  siteModel,
  storyPhotos,
  topicIndex,
  truncate,
  utcTime,
  withdrawnPageHtml,
} from "./site-model.mjs";
import {
  contactEmail,
  ORIGIN,
  requiredName,
  requiredOrigin,
  SITE_DESCRIPTION,
  supportLink,
} from "./site-settings.mjs";
import {
  HOME_HEADING,
  HOME_TITLE,
  absolute,
  blogPosting,
  breadcrumbList,
  collectionPage,
  headTags,
  organization,
  pageTitle,
  storyDescriptions,
  storyTitles,
  website,
} from "./seo.mjs";
import { IMMUTABLE_DIRECTORIES, robots, rss, sitemaps } from "./feeds.mjs";
import { indexCopy, topicCopy } from "./topics.mjs";
import { MARK, SIZES, renderSocialCards } from "./social-cards.mjs";
import { renderSiteIcons } from "./site-icons.mjs";

// The default is an explicitly synthetic, reproducible design fixture. Real
// review data is supplied as an allowlisted export, never a collector database.
const { values: options } = parseArgs({
  options: {
    snapshot: { type: "string" },
    output: { type: "string" },
    media: { type: "string" },
    "airframe-photos": { type: "string" },
    topics: { type: "string" },
    production: { type: "boolean", default: false },
    // The checks' fixture type table records its own content hash.
    "type-table-sha256": { type: "string" },
  },
});
const airframeFile = options["airframe-photos"];
// A production build names the configured origin and name, never the defaults.
if (options.production) {
  requiredOrigin();
  requiredName();
}
if (options["type-table-sha256"]) {
  // Production builds accept only the reviewed type table.
  if (options.production)
    throw new Error("--type-table-sha256 is for check builds only");
  await loadTypeTable(undefined, options["type-table-sha256"]);
}
if ((options.media || airframeFile) && !options.snapshot)
  throw new Error(
    "Generated event media and airframe photos require an observation snapshot",
  );
const target = options.output ? path.resolve(root, options.output) : preview;
if (!target.startsWith(output + path.sep))
  throw new Error("Build output must stay under artifacts/ghost-theme/");
await fs.mkdir(target, { recursive: true });
if (
  !(await fs.realpath(target)).startsWith(
    (await fs.realpath(output)) + path.sep,
  )
)
  throw new Error("Build output cannot follow a symlink outside artifacts");

// Validation, article bodies, and reader links come from the Python exporter,
// which owns the public event contract.
let imported;
if (options.snapshot) {
  const result = spawnSync(
    "python3",
    [path.join(tools, "import_snapshot.py")],
    {
      cwd: root,
      env: { ...process.env, PYTHONPATH: path.join(root, "src") },
      input: await fs.readFile(options.snapshot, "utf8"),
      encoding: "utf8",
      maxBuffer: 128 * 1024 * 1024,
      timeout: 120000,
    },
  );
  if (result.status !== 0)
    throw new Error(
      "Snapshot validation/card generation failed; inspect the input with export_public_events.py validate-snapshot.",
    );
  imported = JSON.parse(result.stdout);
}
const snapshot = imported?.snapshot || exampleSnapshot(demoStories);
const publicOrigin = options.production ? ORIGIN : "";
// Structured data always names the public site; previews never print it.
const origin = ORIGIN;
if (
  options.production &&
  (!imported ||
    snapshot.mode !== "published" ||
    snapshot.events.some((event) => event.synthetic))
)
  throw new Error(
    "Production requires a published snapshot without synthetic observations",
  );

const topicsPath = topicsFile(options.topics);
const topics = topicIndex(JSON.parse(await fs.readFile(topicsPath, "utf8")));

// Photos of the observed airframes, from the publisher's Planespotters step.
// They go into article pages only: never cards, journal data, feeds, search
// or social images.
const airframeInput = airframeFile
  ? await loadAirframePhotos(path.resolve(root, airframeFile), snapshot.events)
  : new Map();
// The airframe photos the stories show (storyPhotos decides).
const airframePhotos = new Map();
const generated = options.media
  ? await loadEventMedia(snapshot, path.resolve(root, options.media))
  : { media: {}, figures: {}, files: new Map() };
// The lead visual is the track map or chart when one exists, otherwise the
// representative type photo, otherwise any other figure. A type photo that
// does not lead moves to "About the type". A card shows the lead, except that
// the representative photo of the type's class replaces a summary figure.
const charted = (figure) =>
  ["track", "measurement-chart"].includes(figure?.kind);
const lead = {};
const photos = {};
const cardPhotos = {};
const cards = {};
// The altitude profile goes by its role, whichever figure leads.
const profiles = {};
// Cards fall back to an event's own media, so the journal (and its
// presentation digest) receives only generated figures and photos.
const journalMedia = {};
for (const event of snapshot.events) {
  const figure = generated.media[event.id] || event.media;
  const profile = generated.figures[event.id]?.find(
    (item) => item.role === "profile",
  );
  if (profile) profiles[event.id] = profile;
  // One photo of the aircraft per page: the airframe photo, else the type
  // photo. Cards keep the type or class photo either way.
  const chosen = storyPhotos(event, {
    registration: airframeInput.get(event.id),
    type: imported ? aircraftPhoto(event) : null,
    stock: imported ? classPhoto(event) : null,
  });
  const { typePhoto: photo, cardPhoto } = chosen;
  if (chosen.registrationPhoto)
    airframePhotos.set(event.id, chosen.registrationPhoto);
  if (photo) photos[event.id] = photo;
  if (cardPhoto) cardPhotos[event.id] = cardPhoto;
  if (charted(figure)) lead[event.id] = figure;
  else if (photo) lead[event.id] = photo;
  else if (figure) lead[event.id] = figure;
  const card = cardVisual(lead[event.id], cardPhoto);
  if (card) cards[event.id] = card;
  if (card && card !== event.media) journalMedia[event.id] = card;
}

const escape = Handlebars.escapeExpression;
const demoArticles = {};
if (!imported)
  for (const event of snapshot.events) {
    const story = demoStories.find((item) => item.url === event.article_path);
    demoArticles[event.id] =
      `<section class="article-observation" aria-label="Observation interval"><p><strong>Observed</strong> <time datetime="${escape(story.observation_start)}">${dateLabel(story.observation_start)} · ${timeLabel(story.observation_start)}</time> – <time datetime="${escape(story.observation_end)}">${dateLabel(story.observation_end)} · ${timeLabel(story.observation_end)}</time></p></section>` +
      story.html;
  }
// Article bodies link to the public site absolutely, because Ghost would change a
// root-relative link in an owned block. This site serves those pages itself, so
// they link root-relatively here, as every other page does, previews included.
const ownLinks = new RegExp(
  `(\\shref=")${ORIGIN.replaceAll(".", "\\.")}(/[^"]*")`,
  "g",
);
const siteRelative = (html) => html.replace(ownLinks, "$1$2");
const articles = imported
  ? Object.fromEntries(
      Object.entries(imported.articles).map(([id, html]) => [
        id,
        siteRelative(html),
      ]),
    )
  : demoArticles;
const model = siteModel(snapshot.events, {
  topics,
  topicCopy,
  articles,
  lead,
  photos,
  cardPhotos,
  profiles,
  retired: snapshot.retired || [],
});
const journal = createJournal(snapshot, {
  references: imported?.references,
  media: journalMedia,
  publicationOrigin: publicOrigin,
});
const weekly = weeklyView(snapshot, cards);
const publicationDescription = SITE_DESCRIPTION;

// Social images: one 1200x630 PNG per story figure, a site card, and the
// logo, plus a small JPEG of each drawn figure among the home page's latest
// stories. A production page never ships without its image, so a missing
// browser stops the build.
const drawn = (story) => {
  const figure = generated.media[story.id];
  const name = figure && path.basename(figure.src);
  return figure && generated.files.has(name)
    ? { figure, svg: generated.files.get(name) }
    : null;
};
let social = new Map();
if (options.production) {
  const jobs = [
    { id: "site", kind: "site", tagline: HOME_HEADING },
    { id: "logo", kind: "logo" },
  ];
  for (const story of model.stories) {
    const own = drawn(story);
    if (own) jobs.push({ id: story.id, kind: "card", svg: own.svg.toString() });
  }
  for (const story of model.stories.slice(0, HOME_LATEST)) {
    const own = drawn(story);
    if (own && story.cardMedia === own.figure)
      jobs.push({
        id: `tile:${story.id}`,
        kind: "tile",
        svg: own.svg.toString(),
      });
  }
  // Cards draw with the full source font; pages load its Latin subset.
  social = await renderSocialCards(jobs, {
    cache: path.join(output, "social-cache"),
    font: await fs.readFile(path.join(tools, "fonts/Newsreader.ttf")),
  });
  const missing = jobs.filter((job) => !social.has(job.id));
  if (missing.length)
    throw new Error(`${missing.length} social images were not drawn`);
  // A thumbnail is published only when it is smaller than the SVG it shows.
  for (const story of model.stories.slice(0, HOME_LATEST)) {
    const key = `tile:${story.id}`;
    const tile = social.get(key);
    if (tile && !(tile.data.length < drawn(story).svg.length))
      social.delete(key);
  }
}
// Browsers, crawlers and iOS ask for these at the root whatever the page says.
const siteIcons = options.production
  ? await renderSiteIcons({ cache: path.join(output, "social-cache") })
  : {};
// A home tile's image is the story's card image. A class photo carries a
// visible note, since it shows another aircraft.
function thumbnail(story) {
  const visual = story.cardMedia;
  if (!visual) return null;
  const tile = social.get(`tile:${story.id}`);
  if (tile) return { src: tile.src, width: tile.width, height: tile.height };
  return {
    src: visual.src,
    width: visual.width || 900,
    height: visual.height || 600,
    note: visual.note || null,
  };
}
const siteCard = social.get("site");
const logo = social.get("logo");
const siteImage = siteCard && {
  ...siteCard,
  alt: `${BRAND}: ${HOME_HEADING}`,
};

const h = Handlebars.create();
const safe = (text) => new h.SafeString(text);
const templates = {};
// Ghost wraps a template in default.hbs only when its source asks for it.
const usesLayout = {};
for (const name of [
  "default",
  "home",
  "index",
  "post",
  "page",
  "author",
  "error",
  "error-404",
  "weekly-spotlights",
  "topic",
  "aircraft",
  "operator",
]) {
  const source = await fs.readFile(path.join(theme, `${name}.hbs`), "utf8");
  usesLayout[name] = source.includes("{{!< default}}");
  templates[name] = h.compile(source);
}
for (const name of await fs.readdir(path.join(theme, "partials"))) {
  if (name.endsWith(".hbs"))
    h.registerPartial(
      name.slice(0, -4),
      await fs.readFile(path.join(theme, "partials", name), "utf8"),
    );
}
// Like Ghost, production asset URLs carry a content version, so the edge can
// cache them as immutable.
const assetVersions = new Map();
for (const name of [
  "css/screen.css",
  "css/journal.css",
  "css/geographic-replay.css",
  "css/event-cards.css",
  "css/weekly-spotlight.css",
  "js/appearance.js",
  "js/site.js",
  "js/journal.js",
  "js/geographic-replay.js",
  "js/weekly-spotlight.js",
  "fonts/InstrumentSans-latin.woff2",
  "fonts/Newsreader-latin.woff2",
])
  assetVersions.set(
    name,
    createHash("sha256")
      .update(await fs.readFile(path.join(theme, "assets", name)))
      .digest("hex")
      .slice(0, 10),
  );
h.registerHelper("asset", (name) =>
  safe(
    escape(
      options.production && assetVersions.has(name)
        ? `/assets/${name}?v=${assetVersions.get(name)}`
        : `/assets/${name}`,
    ).replaceAll("&#x3D;", "="),
  ),
);
h.registerHelper("img_url", (url) => url);
h.registerHelper("encode", (value) => safe(encodeURIComponent(value)));
h.registerHelper("content", function () {
  return safe(this.html || "");
});
h.registerHelper("excerpt", function (options) {
  return (this.excerpt || this.custom_excerpt || "")
    .split(/\s+/)
    .slice(0, Number(options.hash.words) || 50)
    .join(" ");
});
h.registerHelper("reading_time", () => "1 min read");
h.registerHelper("date", function (options) {
  const date = new Date(this.published_at);
  return options.hash.format === "YYYY-MM-DD"
    ? date.toISOString().slice(0, 10)
    : dateLabel(date);
});
h.registerHelper("authors", function () {
  return safe(
    (this.authors || [])
      .map((a) => `<a href="${escape(a.url)}">${escape(a.name)}</a>`)
      .join(", "),
  );
});
h.registerHelper("body_class", function () {
  return `${this.template}-template`;
});
h.registerHelper("post_class", function () {
  return `post ${this.primary_tag ? `tag-${this.primary_tag.slug}` : ""}`;
});
// Ghost's context test, for the templates this preview renders.
h.registerHelper("is", function (contexts, options) {
  const wanted = String(contexts)
    .split(",")
    .map((item) => item.trim());
  return wanted.includes(options.data.root.template)
    ? options.fn(this)
    : options.inverse(this);
});
h.registerHelper("foreach", function (items, options) {
  const from = Math.max(0, (Number(options.hash.from) || 1) - 1);
  const selected = (items || []).slice(
    from,
    options.hash.limit ? from + Number(options.hash.limit) : undefined,
  );
  if (selected.length === 0) return options.inverse(this);
  return selected
    .map((item, index) =>
      options.fn(item, {
        data: {
          ...options.data,
          index,
          number: index + 1,
          first: index === 0,
          last: index === selected.length - 1,
        },
      }),
    )
    .join("");
});
h.registerHelper("get", function (resource, options) {
  if (resource !== "posts")
    throw new Error("Only the posts helper is supported in this preview.");
  return options.fn(
    { posts: model.stories.slice(0, Number(options.hash.limit)) },
    { data: options.data },
  );
});
const primaryNavigation = [
  ["Stories", "/stories/"],
  ["Aircraft", "/aircraft/"],
  ["Topics", "/topics/"],
  ["Weekly", "/weekly/"],
  ["Field guide", "/field-guide/"],
  ["About", "/about/"],
];
const secondaryNavigation = [
  ["All stories", "/stories/"],
  ["Topics", "/topics/"],
  ["Aircraft", "/aircraft/"],
  ["Operators", "/operators/"],
  ["Weekly picks", "/weekly/"],
  ["Field guide", "/field-guide/"],
  ["About", "/about/"],
  ...(options.production ? [["RSS feed", "/rss/"]] : []),
];
h.registerHelper("navigation", function (options) {
  const nav =
    options.hash.type === "secondary" ? secondaryNavigation : primaryNavigation;
  const route = options.data.root.route;
  // The page itself is "page"; a section the page belongs to is "true".
  const navigation = nav.map(([label, url]) => ({
    label,
    url,
    slug: label.toLowerCase().replaceAll(" ", "-"),
    current: route === url,
    section: route !== url && url !== "/" && route.startsWith(url),
  }));
  return safe(
    h.compile(h.partials.navigation)({ navigation }, { data: options.data }),
  );
});
// Ghost's own list pagination (partials/pagination.hbs) runs only in the
// templates' native branches; the static lists page with pageLinks().
h.registerHelper("page_url", (page) => pageUrl("/stories/", page));
h.registerHelper("pagination", function (options) {
  return safe(
    h.compile(h.partials.pagination)(this.pagination, { data: options.data }),
  );
});
const icon =
  '<link rel="icon" href="/examples/favicon.svg" type="image/svg+xml">';
h.registerHelper("ghost_head", function () {
  if (!options.production)
    return safe(
      `<meta name="robots" content="noindex, nofollow"><meta name="description" content="${escape(this.seo?.description || (options.snapshot ? "Saved aircraft observations for private review." : `An unpublished design prototype for ${BRAND}, using illustrative flight stories.`))}">${icon}<link rel="stylesheet" href="/preview.css">`,
    );
  return safe(icon + headTags(origin, this.seo));
});
// Ghost writes its footer scripts here; an empty line would fail the HTML lint.
h.registerHelper("ghost_foot", () => safe("<!-- ghost_foot -->"));

await fs.mkdir(target, { recursive: true });
for (const entry of await fs.readdir(target)) {
  if (options.production || !IMMUTABLE_DIRECTORIES.includes(entry))
    await fs.rm(path.join(target, entry), { recursive: true, force: true });
}
await fs.mkdir(path.join(target, "examples"), { recursive: true });
await fs.cp(path.join(theme, "assets"), path.join(target, "assets"), {
  recursive: true,
});
if (generated.files.size) {
  await fs.mkdir(path.join(target, "event-media"), { recursive: true });
  for (const [name, data] of generated.files)
    await fs.writeFile(path.join(target, "event-media", name), data);
}
if (social.size) {
  await fs.mkdir(path.join(target, "social"), { recursive: true });
  for (const card of social.values())
    await fs.writeFile(path.join(target, card.src.slice(1)), card.data);
}
if (!options.production) {
  for (const [name, text] of Object.entries(illustrations()))
    await fs.writeFile(path.join(target, "examples", name), text);
  await fs.writeFile(
    path.join(target, "examples/circling.json"),
    JSON.stringify(makeTrack()),
  );
  await fs.cp(
    path.join(tools, "preview.css"),
    path.join(target, "preview.css"),
  );
}
await fs.writeFile(path.join(target, "examples/favicon.svg"), MARK);

// The support link shows in the header and footer only when one is configured.
const support = supportLink();
const data = {
  page: { show_title_and_feature_image: true },
  site: {
    url: publicOrigin,
    title: BRAND,
    locale: "en",
    description: publicationDescription,
  },
  custom: {
    default_appearance: "paper",
    site_tagline: HOME_HEADING,
    support_url: support?.url || "",
    support_label: support?.label || "",
    publication_description: publicationDescription,
    contact_email: contactEmail() || "",
  },
};

// Plain-text views of the model for the templates.
const count = (value, singular, plural = `${singular}s`) =>
  `${value} ${value === 1 ? singular : plural}`;
const stories = (value) => count(value, "story", "stories");
const observedLabel = (story) =>
  `${shortDate(story.observedStart)}, ${utcTime(story.observedStart)}`;
// A list on a topic or type page omits the link back to that same page.
const entry = (story, nested, hide = {}) => ({
  id: story.id,
  url: story.url,
  headline: story.headline,
  dek: story.dek,
  primaryType: story.primaryType,
  topicUrl: hide.topic ? null : story.topicHub.url,
  topicTitle: hide.topic ? null : story.topicHub.title,
  typeUrl: hide.type ? null : story.typeHub?.url || null,
  typeName: hide.type ? null : story.typeHub?.name || story.typeLabel || null,
  observedStart: story.observedStart,
  observedLabel: observedLabel(story),
  sourceBadge: story.sourceBadge,
  nested,
});
const listView = (chunk, { nested = false, hide = {} } = {}) => ({
  nested,
  groups: datedGroups(chunk.items).map((group) => ({
    ...group,
    nested,
    stories: group.stories.map((story) => entry(story, nested, hide)),
  })),
  pagination: pageLinks(chunk),
});
const trail = (...items) => [{ name: "Home", url: "/" }, ...items];
const visibleCrumbs = (items) => ({
  breadcrumbs: items.map((item, index) => ({
    name: item.name,
    url: index === items.length - 1 ? null : item.url,
  })),
});
const topicsView = (sections, index) =>
  sections.map((section) => ({
    slug: section.slug,
    title: section.title,
    index,
    topics: section.topics.map((topic) => ({
      url: topic.url,
      title: topic.title,
      description: index ? topic.description : null,
      countLabel: stories(topic.count),
    })),
  }));
const guideLabels = {
  "ads-b": "ADS-B",
  mlat: "MLAT",
  "ground-speed": "Ground speed and airspeed",
  "geometric-altitude": "Pressure and geometric altitude",
  squawk: "Squawk codes",
  "type-match": "Type matches and aircraft databases",
  callsigns: "Callsigns and registrations",
  gaps: "Gaps in a track",
  purpose: "What a track cannot show",
  counting: "How the journal counts observations",
};
for (const anchor of guideAnchors)
  if (!guideLabels[anchor])
    throw new Error(`Field-guide anchor ${anchor} has no label`);

const routes = [];
const allRoutes = new Set();
const sitemapEntries = {
  stories: [],
  aircraft: [],
  operators: [],
  topics: [],
  pages: [],
};
async function render(template, route, context, { audit = true } = {}) {
  if (allRoutes.has(route)) throw new Error(`Two pages share ${route}`);
  allRoutes.add(route);
  const seo = context.seo || {};
  const ctx = {
    pagination: { page: 1, pages: 1, prev: null, next: null },
    ...context,
    route,
    template,
    meta_title: seo.title || BRAND,
  };
  let html = templates[template](ctx, { data });
  if (usesLayout[template])
    html = templates.default({ ...ctx, body: html }, { data });
  if (!options.production)
    html = html.replace(
      /(<body[^>]*>)/,
      `$1\n<aside class="preview-notice" aria-label="Design preview"><strong>Private review</strong><span>${journal.events.some((e) => e.synthetic) ? "Synthetic examples · not receiver history" : snapshot.mode === "review" ? "Saved observations · unpublished review" : "Saved observations · private preview"}</span></aside>`,
    );
  const folder = path.join(target, route);
  await fs.mkdir(folder, { recursive: true });
  await fs.writeFile(
    path.join(folder, "index.html"),
    html.replace(/[ \t]+$/gm, ""),
  );
  if (audit) routes.push(route);
  if (seo.indexable && seo.sitemap)
    sitemapEntries[seo.sitemap].push({ url: route, lastmod: seo.lastmod });
}
const graph = (...nodes) => nodes.filter(Boolean);
const fallbackDate = model.lastmod || snapshot.as_of_utc;
// Lists without their own description get the archive's.
const listSeo = ({
  url,
  title,
  heading,
  description,
  items,
  crumbs,
  page,
}) => ({
  url,
  title: pageTitle(title, page),
  socialTitle: heading,
  description,
  image: siteImage,
  jsonLd: graph(
    collectionPage(origin, {
      url,
      name: heading,
      description,
      stories: items,
    }),
    breadcrumbList(origin, crumbs),
  ),
});
// A list page's heading past page 1: "Heading, page N".
const pageHeading = (heading, chunk) =>
  chunk.page > 1 ? `${heading}, page ${chunk.page}` : heading;
// Later pages of a list describe themselves as such, so no two pages share a
// meta description.
const pageDescription = (description, chunk) => {
  if (chunk.page === 1) return description;
  const suffix = ` Page ${chunk.page}.`;
  return truncate(description, 160 - suffix.length) + suffix;
};
// Head tags for one page of a paginated list: only page 1 enters its sitemap,
// and every page links its neighbours.
function pagedSeo(
  chunk,
  { title, heading, description, crumbs, indexable, sitemap, lastmod },
) {
  const pages = pageLinks(chunk);
  return {
    ...listSeo({
      url: chunk.url,
      title,
      heading: pageHeading(heading, chunk),
      description: pageDescription(description, chunk),
      items: chunk.items,
      crumbs,
      page: chunk.page,
    }),
    indexable,
    sitemap: chunk.page === 1 ? sitemap : null,
    lastmod,
    prev: pages?.prev,
    next: pages?.next,
  };
}

// Home: latest stories, the weekly pick, topics, aircraft, and the chart.
const homeChart = journal.context("all");
await render("home", "/", {
  home: {
    heading: HOME_HEADING,
    intro: indexCopy.home,
    latest: model.stories.slice(0, HOME_LATEST).map((story) => ({
      ...entry(story, false),
      thumb: thumbnail(story),
    })),
    sections: topicsView(model.sections, false),
    types: [...model.types]
      .sort((a, b) => b.count - a.count || compareText(a.name, b.name))
      .slice(0, 8)
      .map((hub) => ({
        url: hub.url,
        name: hub.name,
        countLabel: stories(hub.count),
      })),
    firsts: (model.topicsBySlug.get("first-sightings")?.stories || [])
      .slice(0, 4)
      .map((story) => entry(story, true)),
    chart: { ...homeChart, staticChart: true },
  },
  weekly,
  chartStyles: homeChart.chartStyles,
  seo: {
    url: "/",
    title: HOME_TITLE,
    socialTitle: HOME_TITLE,
    description: indexCopy.home,
    indexable: true,
    sitemap: "pages",
    lastmod: fallbackDate,
    image: siteImage,
    feed: true,
    jsonLd: graph(
      website(origin),
      organization(origin, logo),
      collectionPage(origin, {
        url: "/",
        name: HOME_TITLE,
        description: indexCopy.home,
        stories: model.stories.slice(0, HOME_LATEST),
      }),
    ),
  },
});

// The story archive: every story, newest first, behind the chart and search.
for (const chunk of paginate(model.stories, "/stories/")) {
  const heading = pageHeading("All stories", chunk);
  const list = listView(chunk, { nested: chunk.page === 1 });
  const crumbs = trail({ name: "Stories", url: "/stories/" });
  const seo = {
    ...pagedSeo(chunk, {
      title: "All stories",
      heading: "All stories",
      description: indexCopy.archive,
      crumbs,
      indexable: true,
      sitemap: "pages",
      lastmod: fallbackDate,
    }),
    feed: true,
  };
  if (chunk.page === 1) {
    const context = journal.context("all");
    await render("index", chunk.url, {
      journal: {
        ...context,
        heading,
        intro: indexCopy.archive,
        ...visibleCrumbs(crumbs),
        archive: {
          ...list,
          page: 1,
          heading: "Every story, newest first",
          result: archiveResult(0, chunk.items.length, model.stories.length),
        },
      },
      chartStyles: context.chartStyles,
      seo,
    });
  } else
    await render("index", chunk.url, {
      archive: {
        heading,
        intro: indexCopy.archive,
        list,
        ...visibleCrumbs([...crumbs, { name: heading }]),
      },
      seo,
    });
}

// Immutable journal views share one snapshot; search URLs land on these.
for (const [key, view] of journal.views)
  for (let page = 1; page <= view.pages; page += 1) {
    const context = journal.context(key, page);
    const heading =
      context.heading.charAt(0).toUpperCase() + context.heading.slice(1);
    await render(
      "index",
      journal.viewPath(key, page),
      {
        journal: { ...context, heading },
        chartStyles: context.chartStyles,
        seo: {
          url: journal.viewPath(key, page),
          title: pageTitle(heading),
          socialTitle: heading,
          description: indexCopy.archive,
          indexable: false,
        },
      },
      { audit: false },
    );
  }
// Include one immutable page in the broad accessibility audit.
routes.push(journal.viewPath("all", 1));
await fs.mkdir(path.join(target, journal.base), { recursive: true });
await fs.writeFile(
  path.join(target, journal.base, "chart.css"),
  journal.chartCss,
);
await fs.writeFile(
  path.join(target, journal.base, "index.json"),
  JSON.stringify({
    version: QUERY_VERSION,
    presentation: journal.presentation,
    snapshot_id: snapshot.snapshot_id,
    events: journal.records,
  }),
);
await fs.mkdir(path.join(target, journal.base, "cards"), { recursive: true });
// Handlebars keeps a partial's source until its first use compiles it.
const partialTemplate = (name) =>
  typeof h.partials[name] === "function"
    ? h.partials[name]
    : h.compile(h.partials[name]);
const cardTemplate = partialTemplate("event-card");
for (const event of journal.events)
  await fs.writeFile(
    path.join(target, journal.base, "cards", `${event.id}.json`),
    JSON.stringify({
      version: QUERY_VERSION,
      presentation: journal.presentation,
      snapshot_id: snapshot.snapshot_id,
      id: event.id,
      lead: cardTemplate(journal.card(event, true), { data }),
      row: cardTemplate(journal.card(event, false), { data }),
    }),
  );
// The story archive's entries, newest first, for the archive pages the
// journal draws itself. Its stories and their order come from this snapshot,
// which names the folder, so those pages neither skip nor repeat a story;
// each entry is the static list's markup, whose topic links follow the build's
// topic table.
const entryTemplate = partialTemplate("story-entry");
await fs.writeFile(
  path.join(target, journal.base, "archive.json"),
  JSON.stringify({
    version: QUERY_VERSION,
    presentation: journal.presentation,
    snapshot_id: snapshot.snapshot_id,
    stories: model.stories.map((story) => ({
      id: story.id,
      observed_start: story.observedStart,
      html: entryTemplate(entry(story, true), { data }),
    })),
  }),
);
await fs.writeFile(
  path.join(target, "event-snapshot.json"),
  JSON.stringify(snapshot),
);

// The owned "About the type" block in an article body has no photo. When a
// track map or chart leads, the representative type photo joins that block,
// credited, as it would appear in the theme's own type block (design 4.5.10).
const typePhotoHtml = (photo) =>
  `<figure class="type-photo"><img src="${escape(photo.src)}" alt="${escape(photo.alt)}" width="${escape(String(photo.width))}" height="${escape(String(photo.height))}" loading="lazy" decoding="async"><figcaption>${escape(photo.caption)} <a href="${escape(photo.creditPath)}">Image source and license</a></figcaption></figure>`;
// The same opening tag that marks the block as present (TYPE_BLOCK), so a
// detected block always receives the photo.
const typeBlock = new RegExp(`(${TYPE_BLOCK.source}[\\s\\S]*?)(</section>)`);
const withTypePhoto = (html, photo) =>
  html.replace(
    typeBlock,
    (_, inner, end) => `${inner}${typePhotoHtml(photo)}\n${end}`,
  );

// Stories.
const storyTitle = storyTitles(model.stories);
const storyDescription = storyDescriptions(model.stories);
for (const story of model.stories) {
  // A map or chart leads the article, else the airframe photo, else the
  // story's other lead (the type photo or a summary). An airframe photo below
  // a map keeps its own size; a type photo that does not lead joins "About
  // the type". Cards, feeds, and social images keep the story's lead.
  const airframe = airframePhotos.get(story.id);
  const visual = airframe && !charted(story.leadMedia) ? null : story.leadMedia;
  const caption = visual?.caption
    ? escape(visual.caption) +
      (visual.creditPath
        ? ` <a href="${escape(visual.creditPath)}">${visual.replaySrc ? "Map data sources" : "Image credits and license"}</a>.`
        : "")
    : "";
  const typeName = story.typeHub?.name;
  const typePhoto = story.photo && story.photo !== visual ? story.photo : null;
  const sources = story.profile.sources;
  const typeBackground =
    typeName && !story.typeInBody && (story.profile.facts.length || typePhoto)
      ? {
          name: typeName,
          hubUrl: story.typeHub.url,
          facts: story.profile.facts.map((fact) => ({
            text: fact.text,
            number: fact.source
              ? sources.findIndex((source) => source.id === fact.source.id) + 1
              : null,
          })),
          sources: sources.map((source, index) => ({
            ...source,
            number: index + 1,
          })),
          photo: typePhoto,
        }
      : null;
  const description = storyDescription.get(story.id);
  const card = social.get(story.id);
  // The story's own figure card first, then the representative type photo
  // (the card photo, which the page may leave out for an airframe photo),
  // which shows the type rather than this aircraft, then the site card, for
  // og:image and structured data alike. A class photo is never a social
  // image, and a type photo narrower than a social card's 1200 pixels is
  // left to the site card.
  const typeImage =
    story.cardPhoto?.kind === "aircraft-type-photo" ? story.cardPhoto : null;
  const images = [
    card && { ...card, alt: story.leadMedia?.alt || story.headline },
    typeImage?.width >= SIZES.card.width && typeImage,
  ].filter(Boolean);
  const ogImage = images[0] || siteImage;
  const post = {
    id: story.id,
    title: story.headline,
    custom_excerpt: story.dek,
    url: story.url,
    html:
      story.typeInBody && typePhoto
        ? withTypePhoto(story.html, typePhoto)
        : story.html,
    authors: [author],
    published_at: story.event.published_at,
    unpublished: story.event.publication_status === "unpublished",
    primary_tag: {
      slug: story.topic,
      name: story.topicHub.title,
      url: story.topicHub.url,
    },
    eyebrow: [
      { label: story.topicHub.title, url: story.topicHub.url },
      story.typeHub && { label: story.typeHub.name, url: story.typeHub.url },
    ].filter(Boolean),
    sourceBadge: story.sourceBadge,
    byline: [
      {
        label: "Observed",
        datetime: story.observedStart,
        text: observedLabel(story),
      },
      story.releasedAt && {
        label: "Published",
        datetime: story.releasedAt,
        text: shortDate(story.releasedAt),
      },
      story.updatedAt && {
        label: "Updated",
        datetime: story.updatedAt,
        text: shortDate(story.updatedAt),
      },
    ].filter(Boolean),
    // The headline follows the trail, so the visible trail stops at its parent.
    breadcrumbs: story.breadcrumbs.slice(0, -1),
    feature_media: visual ? { ...visual, event_id: story.id } : undefined,
    feature_image_caption: caption,
    airframe_photo: airframe
      ? airframeFigure(airframe, story.registration)
      : undefined,
    type_background: typeBackground,
    type_background_first: story.proseInBody,
    related:
      story.related.groups.length || story.related.hubs.length
        ? {
            groups: story.related.groups.map((group) => ({
              title: group.title,
              stories: group.stories.map((item) => entry(item, true)),
            })),
            hubs: story.related.hubs,
          }
        : null,
  };
  await render("post", story.url, {
    post,
    replay: Boolean(visual?.replaySrc),
    seo: {
      url: story.url,
      title: storyTitle.get(story.id),
      socialTitle: story.headline,
      description,
      indexable: story.indexable,
      sitemap: "stories",
      lastmod: story.dateModified,
      image: ogImage,
      article: {
        published: story.datePublished,
        modified: story.dateModified,
        section: story.topicHub.title,
        tags: [
          ...new Set(
            [
              story.typeHub?.name || story.typeLabel,
              story.topicHub.title,
            ].filter(Boolean),
          ),
        ],
      },
      jsonLd: graph(
        blogPosting(origin, story, {
          description,
          images: images.length ? images : [siteImage].filter(Boolean),
          logo,
        }),
        breadcrumbList(origin, story.breadcrumbs),
      ),
    },
  });
}

// Topic landings and their index. Empty topics are built and linked; only the
// populated ones, or one with a long sourced intro, ask to be indexed.
const topicsCrumbs = trail({ name: "Topics", url: "/topics/" });
await render("topic", "/topics/", {
  topicIndex: {
    heading: "Topics",
    intro: indexCopy.topics,
    sections: topicsView(model.sections, true),
    ...visibleCrumbs(topicsCrumbs),
  },
  seo: {
    url: "/topics/",
    title: pageTitle("Topics"),
    socialTitle: "Topics",
    description: indexCopy.topics,
    indexable: true,
    sitemap: "topics",
    lastmod: fallbackDate,
    image: siteImage,
    jsonLd: graph(
      {
        ...collectionPage(origin, {
          url: "/topics/",
          name: "Topics",
          description: indexCopy.topics,
          stories: [],
        }),
        mainEntity: {
          "@type": "ItemList",
          numberOfItems: model.topics.length,
          itemListElement: model.topics.map((topic, index) => ({
            "@type": "ListItem",
            position: index + 1,
            url: absolute(origin, topic.url),
            name: topic.title,
          })),
        },
      },
      breadcrumbList(origin, topicsCrumbs),
    ),
  },
});
// The browser audit covers every populated topic and one empty one.
const auditTopics = new Set([
  ...model.topics.filter((topic) => topic.count).map((topic) => topic.slug),
  model.topics.find((topic) => !topic.count)?.slug,
]);
for (const topic of model.topics) {
  const crumbs = [...topicsCrumbs, { name: topic.title, url: topic.url }];
  for (const chunk of paginate(topic.stories, topic.url)) {
    await render(
      "topic",
      chunk.url,
      {
        topic: {
          heading: pageHeading(topic.heading, chunk),
          sectionTitle: topic.sectionTitle,
          showIntro: chunk.page === 1 && topic.intro.length > 0,
          intro: topic.intro,
          sources: topic.sources,
          storiesHeading: topic.count ? stories(topic.count) : "No stories yet",
          list: topic.count
            ? listView(chunk, { nested: true, hide: { topic: true } })
            : null,
          guide: topic.guide.map((anchor) => ({
            url: `/field-guide/#${anchor}`,
            label: guideLabels[anchor] || anchor,
          })),
          related: topic.related.map((other) => ({
            url: other.url,
            title: other.title,
            countLabel: stories(other.count),
          })),
          ...visibleCrumbs(crumbs),
        },
        seo: pagedSeo(chunk, {
          title: topic.seoTitle,
          heading: topic.heading,
          description: topic.description || indexCopy.topics,
          crumbs,
          indexable: topic.indexable,
          sitemap: "topics",
          lastmod: topic.lastmod || fallbackDate,
        }),
      },
      { audit: chunk.page === 1 && auditTopics.has(topic.slug) },
    );
  }
}

// Aircraft: the A to Z index, type hubs, and airframe hubs.
const aircraftCrumbs = trail({ name: "Aircraft", url: "/aircraft/" });
const letters = [];
for (const hub of model.types) {
  const letter = /^[a-z]/i.test(hub.name) ? hub.name[0].toUpperCase() : "#";
  if (letters.at(-1)?.letter !== letter) letters.push({ letter, types: [] });
  letters.at(-1).types.push({
    url: hub.url,
    name: hub.name,
    typeCode: hub.code,
    countLabel: stories(hub.count),
  });
}
await render("aircraft", "/aircraft/", {
  aircraftIndex: {
    heading: "Aircraft types",
    intro: indexCopy.aircraft,
    letters,
    ...visibleCrumbs(aircraftCrumbs),
  },
  seo: {
    url: "/aircraft/",
    title: pageTitle("Aircraft types in the journal"),
    socialTitle: "Aircraft types",
    description: indexCopy.aircraft,
    // Like any hub, the index is a search result only when it lists one.
    indexable: model.types.some((hub) => hub.indexable),
    sitemap: "aircraft",
    lastmod: fallbackDate,
    image: siteImage,
    jsonLd: graph(
      {
        ...collectionPage(origin, {
          url: "/aircraft/",
          name: "Aircraft types",
          description: indexCopy.aircraft,
          stories: [],
        }),
        mainEntity: {
          "@type": "ItemList",
          numberOfItems: model.types.length,
          itemListElement: model.types.map((hub, index) => ({
            "@type": "ListItem",
            position: index + 1,
            url: absolute(origin, hub.url),
            name: hub.name,
          })),
        },
      },
      breadcrumbList(origin, aircraftCrumbs),
    ),
  },
});
for (const hub of model.types) {
  const crumbs = [...aircraftCrumbs, { name: hub.name, url: hub.url }];
  const description = truncate(
    `${stories(hub.count)} about the ${hub.name} in the journal since ${hub.since}, each with its recorded measurements and the limits of the data.`,
  );
  const heading = `${hub.name} observations`;
  for (const chunk of paginate(hub.stories, hub.url)) {
    await render("aircraft", chunk.url, {
      typeHub: {
        heading: pageHeading(heading, chunk),
        name: hub.name,
        typeCode: hub.code,
        summary: `${stories(hub.count)} since ${hub.since} (${hub.range})`,
        showProfile: chunk.page === 1,
        facts: hub.facts.map((fact) => ({
          text: fact.text,
          number: fact.source
            ? hub.sources.findIndex(
                (source) => source.url === fact.source.url,
              ) + 1
            : null,
        })),
        sources: hub.sources.map((source, index) => ({
          ...source,
          number: index + 1,
        })),
        airframes: hub.airframes.map((frame) => ({
          url: frame.url,
          registration: frame.registration,
          countLabel: stories(frame.count),
        })),
        operators: hub.operators.map((operator) => ({
          url: operator.url,
          name: operator.name,
          countLabel: stories(operator.count),
        })),
        list: listView(chunk, { nested: true, hide: { type: true } }),
        ...visibleCrumbs(crumbs),
      },
      seo: pagedSeo(chunk, {
        title: `${hub.name} sightings and ADS-B records`,
        heading,
        description,
        crumbs,
        indexable: hub.indexable,
        sitemap: "aircraft",
        lastmod: hub.lastmod,
      }),
    });
  }
}
for (const hub of model.airframes) {
  const typeName = hub.type?.name || hub.typeSlug;
  const operator = hub.operators[0];
  const crumbs = [
    ...aircraftCrumbs,
    { name: typeName, url: `/aircraft/${hub.typeSlug}/` },
    { name: hub.registration, url: hub.url },
  ];
  const label = [operator?.name, typeName].filter(Boolean).join(" ");
  await render("aircraft", hub.url, {
    airframeHub: {
      heading: `${hub.registration}: ${typeName}`,
      registration: hub.registration,
      typeName,
      typeUrl: `/aircraft/${hub.typeSlug}/`,
      operators: hub.operators.map((item) => ({
        name: item.name,
        designator: item.designator,
        url: `/operators/${item.slug}/`,
      })),
      countries: hub.countries.join(", "),
      summary: `${stories(hub.count)} (${hub.range})`,
      list: listView(
        { items: hub.stories, page: 1, pages: 1, url: hub.url },
        { nested: true, hide: { type: true } },
      ),
      ...visibleCrumbs(crumbs),
    },
    seo: {
      ...listSeo({
        url: hub.url,
        title: `${hub.registration} (${label}) sightings`,
        heading: `${hub.registration}: ${typeName}`,
        description: truncate(
          `Every story about ${hub.registration}, listed in the aircraft database as ${typeName}: ${stories(hub.count)} in the journal (${hub.range}).`,
        ),
        items: hub.stories,
        crumbs,
        page: 1,
      }),
      indexable: hub.indexable,
      sitemap: "aircraft",
      lastmod: hub.lastmod,
    },
  });
}

// Operators come only from broadcast callsign designators.
const operatorCrumbs = trail({ name: "Operators", url: "/operators/" });
await render("operator", "/operators/", {
  operatorIndex: {
    heading: "Operators",
    intro: indexCopy.operators,
    operators: model.operators.map((hub) => ({
      url: hub.url,
      name: hub.name,
      designator: hub.designator,
      countLabel: stories(hub.count),
    })),
    ...visibleCrumbs(operatorCrumbs),
  },
  seo: {
    url: "/operators/",
    title: pageTitle("Operators"),
    socialTitle: "Operators",
    description: indexCopy.operators,
    indexable: model.operators.some((hub) => hub.indexable),
    sitemap: "operators",
    lastmod: fallbackDate,
    image: siteImage,
    jsonLd: graph(
      {
        ...collectionPage(origin, {
          url: "/operators/",
          name: "Operators",
          description: indexCopy.operators,
          stories: [],
        }),
        mainEntity: {
          "@type": "ItemList",
          numberOfItems: model.operators.length,
          itemListElement: model.operators.map((hub, index) => ({
            "@type": "ListItem",
            position: index + 1,
            url: absolute(origin, hub.url),
            name: hub.name,
          })),
        },
      },
      breadcrumbList(origin, operatorCrumbs),
    ),
  },
});
for (const hub of model.operators) {
  const crumbs = [...operatorCrumbs, { name: hub.name, url: hub.url }];
  const basis = hub.designator
    ? `Stories in which the aircraft broadcast a callsign assigned to ${hub.name} (designator ${hub.designator}).`
    : `Stories in which the aircraft broadcast a callsign assigned to ${hub.name}.`;
  for (const chunk of paginate(hub.stories, hub.url))
    await render("operator", chunk.url, {
      operatorHub: {
        heading: pageHeading(hub.name, chunk),
        basis,
        summary: `${stories(hub.count)} (${hub.range})`,
        types: hub.types.map((type) => ({ url: type.url, name: type.name })),
        list: listView(chunk, { nested: true }),
        ...visibleCrumbs(crumbs),
      },
      seo: pagedSeo(chunk, {
        title: `${hub.name} flights in the journal`,
        heading: hub.name,
        description: truncate(
          `${basis} ${stories(hub.count)} since ${hub.since}.`,
        ),
        crumbs,
        indexable: hub.indexable,
        sitemap: "operators",
        lastmod: hub.lastmod,
      }),
    });
}

// Weekly picks: ten per page. /weekly/ shows the first page; its page links
// lead into this snapshot's immutable archive, so a newer build cannot
// reshuffle a reader's next page. Stories in older picks stay reachable from
// the archive, topic, and aircraft lists.
const weeklyBase = `/event-pages/${snapshot.snapshot_id}/weekly-${journal.presentation}/`;
const weeklyCrumbs = trail({ name: "Weekly picks", url: "/weekly/" });
const weeklyDate =
  model.stories
    .filter((story) => story.weekly)
    .map((story) => story.dateModified)
    .sort()
    .at(-1) || fallbackDate;
for (const chunk of paginate(weekly.picks, weeklyBase, WEEKLY_PER_PAGE)) {
  const { page } = chunk;
  const context = {
    weeklyArchive: {
      base: weeklyBase,
      picks: chunk.items,
      pagination: pageLinks(chunk),
    },
  };
  const seo = {
    url: "/weekly/",
    title: pageTitle("Weekly picks", page),
    socialTitle: pageHeading("Weekly picks", chunk),
    description: pageDescription(
      "One observation each week, chosen from the completed week and explained with sourced research.",
      chunk,
    ),
    indexable: true,
    sitemap: "pages",
    lastmod: weeklyDate,
    image: siteImage,
    jsonLd: graph(breadcrumbList(origin, weeklyCrumbs)),
  };
  if (page === 1)
    await render("weekly-spotlights", seo.url, { ...context, seo });
  await render(
    "weekly-spotlights",
    chunk.url,
    {
      ...context,
      seo: { ...seo, url: chunk.url, indexable: false, sitemap: null },
    },
    { audit: false },
  );
}

// Site pages: the field guide, About, and its image credits.
const notes =
  !imported || journal.events.some((event) => event.synthetic)
    ? null
    : options.production
      ? ["How publication works", siteNotes.production]
      : ["About this preview", siteNotes.review];
for (const page of [...pages, imageCreditsPage]) {
  const post = { ...page };
  if (post.slug === "about" && notes)
    post.html = post.html.replace(
      /<h2 id="this-site">About this preview<\/h2>\n<p>[^\n]*<\/p>/,
      `<h2 id="this-site">${notes[0]}</h2>\n${notes[1]}`,
    );
  const crumbs =
    post.url === imageCreditsPage.url
      ? trail(
          { name: "About", url: "/about/" },
          { name: post.title, url: post.url },
        )
      : trail({ name: post.title, url: post.url });
  await render("page", post.url, {
    post: { ...post, ...visibleCrumbs(crumbs) },
    seo: {
      url: post.url,
      title: pageTitle(post.title),
      socialTitle: post.title,
      description: post.custom_excerpt,
      indexable: true,
      sitemap: "pages",
      lastmod: post.updated,
      image: siteImage,
      jsonLd: graph(
        {
          "@type": "WebPage",
          "@id": absolute(origin, post.url),
          url: absolute(origin, post.url),
          name: post.title,
          description: post.custom_excerpt,
          inLanguage: "en",
          isPartOf: { "@id": `${origin}/#website` },
        },
        breadcrumbList(origin, crumbs),
      ),
    },
  });
}

// The author page: a short bio and the latest stories.
const authorCrumbs = trail({ name: author.name, url: author.url });
await render("author", author.url, {
  authorPage: {
    name: author.name,
    bio: author.bio,
    list: listView(
      { items: model.stories.slice(0, 20), page: 1, pages: 1, url: author.url },
      { nested: true },
    ),
    more: model.stories.length > 20,
    ...visibleCrumbs(authorCrumbs),
  },
  seo: {
    url: author.url,
    title: pageTitle(author.name),
    socialTitle: author.name,
    description: truncate(author.bio),
    indexable: true,
    sitemap: "pages",
    lastmod: fallbackDate,
    image: siteImage,
    jsonLd: graph(
      {
        "@type": "ProfilePage",
        "@id": absolute(origin, author.url),
        url: absolute(origin, author.url),
        name: author.name,
        isPartOf: { "@id": `${origin}/#website` },
        mainEntity: {
          "@type": "Person",
          name: author.name,
          url: absolute(origin, author.url),
          description: author.bio,
        },
      },
      breadcrumbList(origin, authorCrumbs),
    ),
  },
});
await render("error-404", "/errors/404/", {
  statusCode: 404,
  seo: {
    url: "/errors/404/",
    title: pageTitle("Page not found"),
    socialTitle: "Page not found",
    description: `This page is not on ${BRAND}.`,
    indexable: false,
  },
});
await render("error", "/errors/500/", {
  statusCode: 500,
  seo: {
    url: "/errors/500/",
    title: pageTitle("Unexpected error"),
    socialTitle: "Unexpected error",
    description: "This page could not be shown.",
    indexable: false,
  },
});

// A withdrawn story's URL serves a noindex page that sends readers to its
// type's hub, or the journal. The release carries it, so the redirect needs
// no change to the server's configuration. It is not a story: it stays out of
// the sitemaps, feed, search, lists, and counts, and out of the page count and
// browser audit, but routes.json lists it so no later story takes the path.
const withdrawnRoutes = new Set();
const collisions = [...model.withdrawnCollisions];
for (const page of model.withdrawn) {
  if (allRoutes.has(page.path)) {
    collisions.push(page.path);
    continue;
  }
  await fs.mkdir(path.join(target, page.path), { recursive: true });
  await fs.writeFile(
    path.join(target, page.path, "index.html"),
    withdrawnPageHtml(page),
  );
  withdrawnRoutes.add(page.path);
}
for (const route of collisions)
  console.error(
    `Withdrawn story path ${route} is also a live page; the live page stays.`,
  );

// The theme's own search reads this JSON index (see routes.yaml). The static
// preview stores it as a directory index its file server can return.
const searchIndex = JSON.stringify(
  model.stories.map((story) => ({
    title: encodeURIComponent(story.headline),
    excerpt: encodeURIComponent(story.dek),
    topic: encodeURIComponent(story.topicHub.title),
    url: story.url,
  })),
);
JSON.parse(searchIndex);
await fs.mkdir(path.join(target, "search-index"));
await fs.writeFile(path.join(target, "search-index/index.txt"), searchIndex);

const files = ["/search-index/"];
if (options.production) {
  for (const [name, content] of Object.entries(
    sitemaps(origin, sitemapEntries),
  )) {
    await fs.writeFile(path.join(target, name), content);
    files.push(`/${name}`);
  }
  await fs.writeFile(path.join(target, "robots.txt"), robots(origin));
  await fs.mkdir(path.join(target, "rss"));
  // The public proxy serves /rss/ from this file as an RSS document.
  await fs.writeFile(
    path.join(target, "rss/index.xml"),
    rss(origin, model.stories, { description: indexCopy.home }),
  );
  files.push("/robots.txt", "/rss/");
  for (const [name, data] of Object.entries(siteIcons)) {
    await fs.writeFile(path.join(target, name), data);
    files.push(`/${name}`);
  }
}
// The pages the browser checks audit.
await fs.writeFile(
  path.join(target, "audit-routes.json"),
  JSON.stringify(routes, null, 2),
);
// Every generated route as one sorted JSON list of paths, for tools that must
// avoid collisions with them, including the publisher's story slug checks: each
// page, each file and, by prefix, each directory of content-addressed files.
const directories = [
  "/assets/",
  "/examples/",
  ...IMMUTABLE_DIRECTORIES.filter(
    (name) => name !== "event-media" || generated.files.size,
  ).map((name) => `/${name}/`),
  ...(social.size ? ["/social/"] : []),
];
await fs.writeFile(
  path.join(target, "routes.json"),
  JSON.stringify(
    [
      ...new Set([...allRoutes, ...withdrawnRoutes, ...files, ...directories]),
    ].sort(),
    null,
    2,
  ),
);
await fs.writeFile(
  path.join(target, "publication.json"),
  JSON.stringify({ mode: options.production ? "production" : "preview" }),
);
console.log(
  `Built ${allRoutes.size} ${options.production ? "production" : "preview"} pages (${routes.length} audited) from the native Ghost templates; topics from ${path.relative(root, topicsPath)}${options.production ? `; ${social.size} social images` : ""}${airframeFile ? `; ${airframePhotos.size} airframe photos` : ""}${withdrawnRoutes.size ? `; ${withdrawnRoutes.size} withdrawn-story redirects` : ""}.`,
);
console.log(target);
