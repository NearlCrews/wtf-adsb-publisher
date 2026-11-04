import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import Handlebars from "handlebars";
import {
  BRAND,
  PER_PAGE,
  RELATED_AIRFRAME_STORIES,
  cardVisual,
  explainerPages,
  pageDigest,
  pageLinks,
  paginate,
  profileFigure,
  siteModel,
  storyPhotos,
  topicIndex,
  truncate,
  uniqueProseWords,
  withdrawnPageHtml,
  withdrawnPages,
} from "./site-model.mjs";
import { ORIGIN } from "./site-settings.mjs";
import {
  INDEX,
  NOINDEX,
  TITLE_LIMIT,
  blogPosting,
  headTags,
  jsonLd,
  storyDescriptions,
  storyTitles,
  withBrand,
} from "./seo.mjs";
import { FEED_SIZE, robots, rss, sitemaps } from "./feeds.mjs";
import { indexCopy, kindTriggers, topicCopy } from "./topics.mjs";
import { pages, siteNotes } from "./demo-content.mjs";
import { KIND_GUIDE, KIND_PARTS, kindAnchor } from "./kind-guide.mjs";
import {
  aircraftPhoto,
  aircraftPhotos,
  classPhoto,
  classPhotos,
  imageCreditsPage,
  loadTypeTable,
} from "./aircraft-photos.mjs";
import { engineTopics, output, theme } from "./paths.mjs";
import { fixtureTableSha256, writeTypeTable } from "./reference-fixture.mjs";
import {
  DEFAULT_PAGE_SIZE,
  PAGE_SIZES,
} from "../../themes/wtf-adsb/assets/js/event-query.mjs";

// Pure functions behind the site map: no browser, network, or snapshot file.
// A fixture origin for the functions that take one.
const SITE = "https://site.example";
const topicsData = JSON.parse(await fs.readFile(engineTopics, "utf8"));
const topics = topicIndex(topicsData);

let serial = 0;
function event({
  kind = "watch_type",
  primary = "unusual-aircraft",
  day = "2026-09-20",
  registration = null,
  type = null,
  operator = null,
  paragraphs = null,
  released = null,
  revised = null,
  contentRevised = null,
  weekly = false,
  topic = null,
  place = null,
}) {
  serial += 1;
  const id = `00000000-0000-4000-8000-${String(serial).padStart(12, "0")}`;
  return {
    id,
    kind,
    primary_type: primary,
    title: `Fixture story ${serial}`,
    summary: `A fixture dek for story ${serial}.`,
    article_path: `/stories/fixture-${serial}-20-sep-2026/`,
    observed_start: `${day}T12:00:00Z`,
    observed_end: `${day}T12:10:00Z`,
    published_at: `${day}T12:00:00Z`,
    released_at: released,
    revised_at: revised || `${day}T12:30:00Z`,
    ...(contentRevised ? { content_revised_at: contentRevised } : {}),
    aircraft: { registration, type_code: type?.code },
    ...(weekly ? { weekly_spotlight: { week_start: day } } : {}),
    ...(paragraphs
      ? {
          story: {
            headline: `Fixture story ${serial}`,
            dek: `A fixture dek for story ${serial}.`,
            paragraphs,
            seo_title: `Fixture ${serial}`,
            facts: type?.profile ? { [`tp.${type.code}.1`]: type.profile } : {},
            sources: type?.profile
              ? [
                  {
                    id: `src.${type.code}.1`,
                    title: "Source",
                    url: "https://example.com/source",
                  },
                ]
              : [],
          },
        }
      : {}),
    context: {
      topic,
      section: null,
      type: type
        ? {
            code: type.code,
            name: type.name,
            slug: type.slug,
            ...(type.sameAs ? { same_as: type.sameAs } : {}),
          }
        : null,
      registration_slug: registration ? registration.toLowerCase() : null,
      operator,
      place,
      country: null,
    },
  };
}
// Distinct invented words without digits: the prose gate reads any word with a
// digit as a filled-in value.
const letters = (index) =>
  index < 26
    ? String.fromCharCode(97 + index)
    : letters(Math.floor(index / 26) - 1) + letters(index % 26);
const words = (count, seed) =>
  Array.from(
    { length: count },
    (_, index) => `${seed}word${letters(index)}`,
  ).join(" ") + ".";

// Meta descriptions end on a sentence, or a word with an ellipsis.
assert.equal(truncate("Short."), "Short.");
const long = `${"A sentence that is long enough to matter here. ".repeat(3)}${"x".repeat(200)}`;
assert.ok(truncate(long).length <= 160);
assert.ok(truncate(long).endsWith("."));
const unbroken = `${"word ".repeat(60)}`;
assert.ok(truncate(unbroken).length <= 160);
assert.ok(truncate(unbroken).endsWith("…"));

// Topics: context wins, then the kind table, then the first topic of the
// event's section, as story_facts.topic_for falls back.
assert.equal(
  topics.topicFor({ kind: "watch_type", context: { topic: "soaring" } }),
  "soaring",
);
assert.equal(topics.topicFor({ kind: "vintage" }), "vintage-aircraft");
assert.equal(topics.topicFor({ kind: "special_squawk_7700" }), "squawk-codes");
for (const section of Object.keys(topicsData.sections))
  assert.equal(
    topics.topicFor({ kind: "unknown_kind", primary_type: section }),
    Object.keys(topicsData.topics).find(
      (slug) => topicsData.topics[slug].section === section,
    ),
    section,
  );
// A kind is matched exactly; no pattern syntax.
assert.equal(
  topicIndex({
    topics: {
      first: { title: "First", section: "s", kinds: [] },
      odd: { title: "Odd", section: "s", kinds: ["special_*"] },
    },
    sections: { s: "S" },
  }).topicFor({ kind: "special_thing", primary_type: "s" }),
  "first",
);
assert.throws(() =>
  topicIndex({
    topics: { "Bad Slug": { section: "s" } },
    sections: { s: "S" },
  }),
);
// Inherited object keys are never topics or sections.
assert.equal(
  topics.topicFor({ kind: "vintage", context: { topic: "constructor" } }),
  "vintage-aircraft",
);
assert.equal(
  topics.topicFor({ kind: "unknown", primary_type: "toString" }),
  Object.keys(topicsData.topics)[0],
);
assert.throws(() =>
  topicIndex({
    topics: { odd: { title: "Odd", section: "toString" } },
    sections: {},
  }),
);
for (const slug of Object.keys(topicsData.topics))
  assert.ok(topicCopy[slug], `Topic ${slug} has no intro copy`);

// Public lists of three or more items take the serial comma ("A, B, and C").
// The pattern finds the common faulty shape, a list whose items each open
// with an article or another determiner ("the registration, the type
// designator and a description"). It cannot parse every sentence, so a pass
// does not prove the copy free of such lists; it keeps that shape out.
const DETERMINER =
  "(?:the|a|an|its|their|any|each|every|some|this|that|these|those)";
const WORD = "[\\w'’-]+(?:,\\d{3})*";
const missingSerialComma = new RegExp(
  `, ${DETERMINER} ${WORD}(?: ${WORD}){0,7} (?:and|or) ${DETERMINER} `,
  "i",
);
assert.match(
  "the registration, the type and a description ",
  missingSerialComma,
);
assert.doesNotMatch(
  "the registration, the type, and a description ",
  missingSerialComma,
);
const copyTexts = (value) =>
  typeof value === "string"
    ? [value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ")]
    : value && typeof value === "object"
      ? Object.values(value).flatMap(copyTexts)
      : [];
for (const [where, copy] of [
  ...Object.entries(topicCopy),
  ["index copy", indexCopy],
  ["kind triggers", kindTriggers],
  ["kind guide", KIND_GUIDE],
  ...[...pages, imageCreditsPage].map((page) => [page.url, page]),
  ["site notes", siteNotes],
])
  for (const text of copyTexts(copy)) {
    const list = text.match(missingSerialComma);
    assert.equal(
      list,
      null,
      `${where}: serial comma missing in "${list?.[0]}"`,
    );
  }

// A site page's lastmod is the date of its last content change: editing a page
// without moving its date fails here.
for (const page of [...pages, imageCreditsPage]) {
  const digest = pageDigest(page, page.slug === "about" ? siteNotes : null);
  assert.equal(
    digest,
    page.contentSha256,
    `${page.url} changed: set its updated date to the day of the change and contentSha256 to ${digest}`,
  );
  assert.match(page.updated, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
}

// The field guide's kinds of story: one entry per guided kind, in topic
// order, each under its own anchor (a story's limits link there) with the
// four labeled parts. The trigger text is the topic copy's, so the guide and
// the topic pages quote the same rules. tests/test_site_build.py checks that
// the guided kinds are exactly those that can lead a story.
{
  const guide = pages.find((page) => page.slug === "field-guide").html;
  const routed = Object.values(topicsData.topics).flatMap(
    (topic) => topic.kinds,
  );
  const guided = Object.keys(KIND_GUIDE);
  assert.deepEqual(
    guided.filter((kind) => !routed.includes(kind)),
    [],
    "Field-guide entries for kinds no topic routes",
  );
  assert.deepEqual(Object.keys(kindTriggers).sort(), [...guided].sort());
  assert.deepEqual(
    [...guide.matchAll(/<h4 id="kind-([a-z0-9-]+)">/g)].map(
      (match) => `kind-${match[1]}`,
    ),
    routed.filter((kind) => guided.includes(kind)).map(kindAnchor),
  );
  assert.equal(kindAnchor("rapid_descent"), "kind-rapid-descent");
  const ids = [...guide.matchAll(/ id="([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(
    ids.filter((id, index) => ids.indexOf(id) !== index),
    [],
    "Field-guide anchors repeat",
  );
  // Headings descend one level at a time: h2 sections, h3 topics, h4 kinds.
  const levelsOf = [...guide.matchAll(/<h([2-6])\b/g)].map((match) =>
    Number(match[1]),
  );
  levelsOf.forEach((level, index) =>
    assert.ok(
      level <= (levelsOf[index - 1] ?? 2) + 1,
      `Field-guide heading h${level} skips a level`,
    ),
  );
  for (const entry of guide.split('<h4 id="kind-').slice(1)) {
    const body = entry.slice(0, entry.indexOf("</dl>"));
    assert.deepEqual(
      [...body.matchAll(/<dt>([^<]+)<\/dt>/g)].map((match) => match[1]),
      KIND_PARTS,
      `Field-guide entry ${entry.slice(0, 30)} lacks its labeled parts`,
    );
    for (const text of body.matchAll(/<dd>([^<]*)<\/dd>/g))
      assert.ok(
        text[1].trim().length > 20,
        `Empty part in ${entry.slice(0, 30)}`,
      );
  }
  for (const [kind, entry] of Object.entries(KIND_GUIDE))
    for (const field of ["name", "what", "why", "falseAlarms"])
      assert.ok(
        typeof entry[field] === "string" && entry[field].trim(),
        `${kind} has no ${field}`,
      );
}

// Hubs at their thresholds, related links, breadcrumbs, and the prose gate.
const a380 = {
  code: "A388",
  name: "Airbus A380-800",
  slug: "airbus-a380",
  profile: "A two-deck airliner.",
};
const c172 = { code: "C172", name: "Cessna 172", slug: "cessna-172" };
const asiana = {
  name: "Asiana Airlines",
  slug: "asiana-airlines",
  basis: "callsign_designator",
  designator: "AAR",
};
const events = [
  event({
    registration: "HL7635",
    type: a380,
    operator: asiana,
    day: "2026-09-27",
    paragraphs: [words(70, "a")],
    released: "2026-09-27T13:00:00Z",
    revised: "2026-09-29T13:00:01Z",
  }),
  event({
    registration: "HL7635",
    type: a380,
    operator: asiana,
    day: "2026-09-16",
    paragraphs: [words(30, "b")],
  }),
  event({
    registration: "HL7640",
    type: a380,
    operator: asiana,
    day: "2026-09-20",
    paragraphs: [words(70, "c")],
  }),
  event({ kind: "outlier_gs", primary: "data-quality", type: c172 }),
  event({ kind: "vintage", weekly: true }),
];
const model = siteModel(events, { topics, topicCopy });
const hub = (list, slug) => list.find((item) => item.slug === slug);
assert.equal(hub(model.types, "airbus-a380").indexable, true);
assert.equal(hub(model.types, "airbus-a380").hasProfile, true);
assert.equal(hub(model.types, "cessna-172").indexable, false);
assert.deepEqual(
  model.airframes.map((frame) => frame.url),
  ["/aircraft/airbus-a380/hl7635/"],
);
assert.equal(hub(model.operators, "asiana-airlines").indexable, true);
const [first, second, third, fourth, fifth] = events.map((item) =>
  model.stories.find((story) => story.id === item.id),
);
assert.equal(first.uniqueWords, 70);
assert.equal(first.indexable, true);
assert.equal(second.uniqueWords, 30);
assert.equal(second.indexable, false);
assert.equal(third.indexable, true);
// Research or an exceptional event lifts the gate without prose.
assert.equal(fifth.indexable, true);
// Structured data dates a story by its release, else by its observation
// start, whatever date Ghost lists; a later Ghost revision (an evidence card
// refresh) never dates the page.
assert.equal(first.datePublished, "2026-09-27T13:00:00Z");
assert.equal(first.dateModified, "2026-09-27T13:00:00Z");
const unreleased = siteModel(
  [{ ...events[1], published_at: "2026-10-01T00:00:00Z" }],
  { topics, topicCopy },
).stories[0];
assert.equal(unreleased.datePublished, events[1].observed_start);
assert.equal(unreleased.dateModified, events[1].observed_start);
assert.equal(first.updatedAt, null);
assert.equal(second.dateModified, "2026-09-16T12:00:00Z");
// The last prose or evidence change dates the modification; only one made more
// than a day after release shows "Updated". A revision time without a release
// is ignored.
const revisedStory = (contentRevised, released = "2026-09-27T13:00:00Z") =>
  siteModel(
    [
      event({
        day: "2026-09-27",
        paragraphs: [words(70, "r")],
        released,
        contentRevised,
      }),
    ],
    { topics, topicCopy },
  ).stories[0];
const sameDay = revisedStory("2026-09-28T12:59:59Z");
assert.equal(sameDay.dateModified, "2026-09-28T12:59:59Z");
assert.equal(sameDay.updatedAt, null);
const nextDay = revisedStory("2026-09-28T13:00:01Z");
assert.equal(nextDay.dateModified, "2026-09-28T13:00:01Z");
assert.equal(nextDay.updatedAt, "2026-09-28T13:00:01Z");
assert.equal(nextDay.datePublished, "2026-09-27T13:00:00Z");
const unreleasedRevision = revisedStory("2026-09-30T00:00:00Z", null);
assert.equal(unreleasedRevision.dateModified, "2026-09-27T12:00:00Z");
assert.equal(unreleasedRevision.updatedAt, null);
assert.deepEqual(
  first.related.groups.map((group) => group.title),
  ["More about HL7635", "Other Airbus A380-800 stories"],
);
assert.deepEqual(
  first.related.groups.flatMap((group) => group.stories.map((s) => s.id)),
  [second.id, third.id],
);
assert.deepEqual(
  first.breadcrumbs.map((crumb) => crumb.url || null),
  [
    "/",
    "/aircraft/",
    "/aircraft/airbus-a380/",
    "/aircraft/airbus-a380/hl7635/",
    null,
  ],
);
assert.deepEqual(
  fifth.breadcrumbs.map((crumb) => crumb.url || null),
  ["/", "/topics/", "/topics/vintage-aircraft/", null],
);
assert.equal(fourth.topic, "ads-b-glitches");
assert.throws(() =>
  siteModel(
    [events[0], { ...events[1], article_path: events[0].article_path }],
    {
      topics,
    },
  ),
);
// Hubs whose every story is below the prose gate stay out of search results,
// however many stories they list.
const thin = siteModel(
  Array.from({ length: 3 }, (_, index) =>
    event({
      registration: "N2",
      type: c172,
      operator: asiana,
      day: `2026-09-1${index}`,
      paragraphs: [words(20, `thin${letters(index)}`)],
    }),
  ),
  { topics, topicCopy },
);
assert.ok(thin.stories.every((story) => !story.indexable));
assert.equal(hub(thin.types, "cessna-172").indexable, false);
assert.deepEqual(
  thin.airframes.map((frame) => [frame.url, frame.indexable]),
  [["/aircraft/cessna-172/n2/", false]],
);
assert.equal(hub(thin.operators, "asiana-airlines").indexable, false);
// The same template sentence about another aircraft, type, or place is not new
// prose, even for a registration without digits.
const template = (registration, type, town) =>
  `${registration}, a ${type.name}, circled near ${town} for several minutes before it left the area.`;
const pair = siteModel(
  [
    event({
      registration: "N100AA",
      type: c172,
      day: "2026-09-10",
      released: "2026-09-10T13:00:00Z",
      place: "Coleman, Michigan",
      paragraphs: [template("N100AA", c172, "Coleman")],
    }),
    event({
      registration: "G-XLEB",
      type: a380,
      day: "2026-09-11",
      released: "2026-09-11T13:00:00Z",
      place: "Fenton, Michigan",
      paragraphs: [template("G-XLEB", a380, "Fenton")],
    }),
  ],
  { topics, topicCopy },
).stories;
assert.deepEqual(
  pair.map((story) => [story.registration, story.uniqueWords]),
  [
    ["G-XLEB", 0],
    ["N100AA", 15],
  ],
);
// Routing values that are not slugs, a missing slug included, build no hub.
const unslugged = siteModel(
  [
    {
      ...event({ registration: "N3", type: c172 }),
      context: {
        topic: null,
        type: { code: "C172", name: "Cessna 172" },
        operator: { name: "Unslugged" },
      },
    },
  ],
  { topics, topicCopy },
);
assert.deepEqual(
  [
    unslugged.stories[0].type,
    unslugged.stories[0].registrationSlug,
    unslugged.stories[0].operator,
    unslugged.types.length,
    unslugged.airframes.length,
  ],
  [null, null, null, 0, 0],
);
// One airframe key: a registration reused on another type is another airframe.
const reused = siteModel(
  [
    event({ registration: "N5", type: c172, day: "2026-09-10" }),
    event({ registration: "N5", type: c172, day: "2026-09-11" }),
    event({ registration: "N5", type: a380, day: "2026-09-12" }),
  ],
  { topics },
).stories;
assert.deepEqual(
  reused.map(
    (story) =>
      story.related.groups.find((group) => group.title === "More about N5")
        ?.stories.length || 0,
  ),
  [0, 1, 1],
);
// A verified reference page for the type reaches the hub and structured data.
const wiki = "https://en.wikipedia.org/wiki/Airbus_A380";
const referenced = siteModel(
  [event({ type: { ...a380, sameAs: wiki } }), event({ type: a380 })],
  { topics },
);
assert.equal(referenced.types[0].sameAs, wiki);
assert.equal(
  blogPosting(SITE, referenced.stories[1], {
    description: "d",
    images: [],
  }).about.sameAs,
  wiki,
);
assert.equal(
  siteModel([event({ type: { ...a380, sameAs: "http://example.com/" } })], {
    topics,
  }).types[0].sameAs,
  null,
);

// Unique prose: only earlier releases count against a story, once per
// sentence; numbers alone and the story's own dek do not make a sentence new.
const prose = (id, released, paragraphs, dek = "") => ({
  id,
  releasedAt: released,
  observedStart: released,
  paragraphs,
  dek,
  headline: "",
});
const older = prose("a", "2026-09-01T00:00:00Z", [
  "One shared line. Only here. Only here.",
]);
const newer = prose("b", "2026-09-02T00:00:00Z", [
  "One shared line. The aircraft climbed to 41,000 feet.",
]);
const unique = uniqueProseWords([newer, older]);
// A topic whose only story stays out of search results stays out as well, and
// a story page lists at most five other stories of its airframe.
const solo = siteModel(
  [
    event({
      kind: "formation_flight",
      primary: "flight-patterns",
      paragraphs: [words(20, "f")],
    }),
  ],
  { topics, topicCopy },
);
assert.equal(solo.stories[0].indexable, false);
assert.equal(
  solo.topics.find((topic) => topic.slug === "formation-flights").indexable,
  false,
);
const frames = siteModel(
  Array.from({ length: 8 }, (_, index) =>
    event({
      registration: "N1",
      type: c172,
      day: `2026-09-${String(10 + index).padStart(2, "0")}`,
    }),
  ),
  { topics },
);
assert.equal(
  frames.stories[0].related.groups[0].stories.length,
  RELATED_AIRFRAME_STORIES,
);
assert.equal(unique.get("a"), 5);
assert.equal(unique.get("b"), 6);
const later = uniqueProseWords([
  newer,
  older,
  prose("c", "2026-09-03T00:00:00Z", [
    "Only here. The aircraft climbed to 38,000 feet. A new line.",
  ]),
]);
assert.equal(later.get("a"), 5);
assert.equal(later.get("b"), 6);
assert.equal(later.get("c"), 3);
assert.equal(
  uniqueProseWords([
    prose("d", "2026-09-04T00:00:00Z", ["Its dek. Its body."], "Its dek."),
  ]).get("d"),
  2,
);
assert.deepEqual(
  paginate([1, 2, 3], "/x/", 2).map((page) => [page.url, page.items]),
  [
    ["/x/", [1, 2]],
    ["/x/page/2/", [3]],
  ],
);

// Static lists page at the journal's default size, so its archive pages at
// that size match them. A list's page links name every page; one page has
// none.
assert.equal(PER_PAGE, DEFAULT_PAGE_SIZE);
assert.equal(pageLinks(paginate([1], "/x/")[0]), null);
const hubPages = paginate(
  Array.from({ length: 45 }, (_, index) => index),
  "/topics/x/",
).map((chunk) => pageLinks(chunk));
assert.deepEqual(
  hubPages.map((links) => [links.page, links.total, links.prev, links.next]),
  [
    [1, 3, null, "/topics/x/page/2/"],
    [2, 3, "/topics/x/", "/topics/x/page/3/"],
    [3, 3, "/topics/x/page/2/", null],
  ],
);
assert.deepEqual(
  hubPages[1].numbers.map((item) => [item.number, item.url, item.current]),
  [
    [1, "/topics/x/", false],
    [2, "/topics/x/page/2/", true],
    [3, "/topics/x/page/3/", false],
  ],
);
assert.equal(
  pageLinks(paginate([1, 2, 3], "/e/", 1)[0], {
    label: "Event pages",
    noun: "events",
    url: (page) => `/e/${page}/`,
  }).next,
  "/e/2/",
);
// The partials draw Previous and Next above and below every paged list. On
// the first and last pages the missing step stays in place as a link without
// an address (no focus, aria-disabled); the two navigations have distinct
// labels, and only the one below lists the page numbers.
{
  const hbs = Handlebars.create();
  hbs.registerHelper("foreach", (items, options) =>
    (items || []).map((item) => options.fn(item)).join(""),
  );
  for (const name of [
    "page-prev",
    "page-next",
    "page-steps",
    "page-numbers",
    "page-size",
  ])
    hbs.registerPartial(
      name,
      await fs.readFile(path.join(theme, "partials", `${name}.hbs`), "utf8"),
    );
  const render = (name, links) =>
    hbs.compile(`{{> "${name}"}}`)(links).replace(/\s+/g, " ");
  // The picker offers the sizes the journal accepts, under the label it is
  // given.
  const picker = render("page-size", { label: "Stories per page" });
  assert.deepEqual(
    [...picker.matchAll(/<option value="(\d+)">\1<\/option>/g)].map(
      ([, size]) => Number(size),
    ),
    PAGE_SIZES,
  );
  assert.match(
    picker,
    /<label for="event-page-size">Stories per page<\/label>/,
  );
  const step = (html, kind) =>
    html.match(new RegExp(`<a class="page-step page-${kind}"[^>]*>`))[0];
  for (const [index, links] of hubPages.entries()) {
    const top = render("page-steps", links);
    const bottom = render("page-numbers", links);
    assert.match(
      top,
      /^<nav class="pagination page-steps" aria-label="Story pages, top of list">/,
    );
    assert.match(
      bottom,
      /^<nav class="pagination page-numbers" aria-label="Story pages">/,
    );
    assert.match(
      top,
      new RegExp(`<span class="page-status">Page ${index + 1} of 3</span>`),
    );
    assert.doesNotMatch(top, /<ol>/);
    assert.equal(bottom.match(/<li>/g).length, 3);
    assert.match(
      bottom,
      new RegExp(`<span aria-current="page">${index + 1}</span>`),
    );
    for (const html of [top, bottom])
      for (const [kind, rel, href, name] of [
        ["prev", "prev", links.prev, "Previous page (newer stories)"],
        ["next", "next", links.next, "Next page (older stories)"],
      ]) {
        const tag = step(html, kind);
        assert.ok(tag.includes(`aria-label="${name}"`), tag);
        if (href) {
          assert.ok(tag.includes(`href="${href}"`), tag);
          assert.ok(tag.includes(`rel="${rel}"`), tag);
          assert.doesNotMatch(tag, /aria-disabled|role=/);
        } else {
          assert.ok(tag.includes('role="link" aria-disabled="true"'), tag);
          assert.doesNotMatch(tag, /href=|rel=|tabindex/);
        }
      }
  }
}

// Titles stay unique and within 72 characters: the site name follows a title
// while the whole fits.
const suffix = ` | ${BRAND}`;
const room = TITLE_LIMIT - suffix.length;
assert.equal(withBrand("Short title"), `Short title${suffix}`);
assert.equal(withBrand("x".repeat(room)), `${"x".repeat(room)}${suffix}`);
assert.equal(withBrand("x".repeat(room + 1)), "x".repeat(room + 1));
const titled = storyTitles([
  {
    id: "1",
    url: "/a/",
    seoTitle: "Same",
    observedStart: "2026-09-27T00:05:00Z",
    slug: "a",
  },
  {
    id: "2",
    url: "/b/",
    seoTitle: "Same",
    observedStart: "2026-09-28T00:05:00Z",
    slug: "b",
  },
  {
    id: "3",
    url: "/c/",
    seoTitle: "Same",
    observedStart: "2026-09-28T00:05:00Z",
    slug: "c",
  },
]);
assert.equal(new Set(titled.values()).size, 3);
assert.equal(titled.get("1"), `Same${suffix}`);
assert.equal(titled.get("2"), `Same, 28 Sep 2026${suffix}`);
// An earlier release keeps its title when a newer story repeats it.
const retitled = storyTitles([
  {
    id: "9",
    url: "/a-new/",
    seoTitle: "Same",
    releasedAt: "2026-09-30T00:00:00Z",
    observedStart: "2026-09-29T00:05:00Z",
    slug: "a-new",
  },
  {
    id: "8",
    url: "/z-old/",
    seoTitle: "Same",
    releasedAt: "2026-09-20T00:00:00Z",
    observedStart: "2026-09-19T00:05:00Z",
    slug: "z-old",
  },
]);
assert.equal(retitled.get("8"), `Same${suffix}`);
assert.equal(retitled.get("9"), `Same, 29 Sep 2026${suffix}`);
const described = storyDescriptions([
  {
    id: "1",
    url: "/a/",
    dek: "Repeated.",
    headline: "One",
    observedStart: "2026-09-27T00:00:00Z",
  },
  {
    id: "2",
    url: "/b/",
    dek: "Repeated.",
    headline: "Two",
    observedStart: "2026-09-28T00:00:00Z",
  },
]);
assert.equal(new Set(described.values()).size, 2);

// Sitemaps carry lastmod everywhere; the feed and robots follow the design.
const maps = sitemaps(SITE, {
  stories: [{ url: "/stories/a/", lastmod: "2026-09-27T00:00:00Z" }],
  pages: [{ url: "/", lastmod: "2026-09-28T00:00:00Z" }],
});
assert.match(maps["sitemap.xml"], /<sitemapindex/);
assert.equal((maps["sitemap.xml"].match(/<sitemap>/g) || []).length, 2);
assert.match(
  maps["sitemap-stories.xml"],
  /<lastmod>2026-09-27T00:00:00Z<\/lastmod>/,
);
assert.throws(() => sitemaps(SITE, { stories: [{ url: "/x/" }] }));
const feedStories = Array.from({ length: FEED_SIZE + 5 }, (_, index) => ({
  id: `id-${index}`,
  url: `/stories/s-${index}/`,
  headline: `Story <${index}> & co`,
  dek: "Dek",
  releasedAt: new Date(Date.UTC(2026, 8, 1) + index * 3600000).toISOString(),
  topicHub: { title: "Topic" },
}));
const feed = rss(SITE, feedStories, { description: "Site" });
assert.equal((feed.match(/<item>/g) || []).length, FEED_SIZE);
assert.match(feed, /<title>Story &lt;54&gt; &amp; co<\/title>/);
assert.match(feed, /<guid isPermaLink="false">id-54<\/guid>/);
assert.doesNotMatch(feed, /id-4<\/guid>/);
assert.match(robots(SITE), /^Disallow: \/event-pages\/$/m);

// Head tags: robots directive, raster image tags, and escaped JSON-LD.
const head = headTags(SITE, {
  url: "/stories/a/",
  description: 'A "quoted" dek',
  socialTitle: "Headline",
  indexable: true,
  image: {
    src: "/social/a.png",
    type: "image/png",
    width: 1200,
    height: 630,
    alt: "Alt",
  },
  article: { published: "p", modified: "m", section: "S", tags: ["A", "B"] },
  jsonLd: [{ "@type": "Thing", name: "</script>" }],
});
assert.match(head, new RegExp(`<meta name="robots" content="${INDEX}">`));
assert.match(head, /<meta property="og:image:width" content="1200">/);
assert.equal((head.match(/article:tag/g) || []).length, 2);
assert.match(head, /content="A &quot;quoted&quot; dek"/);
assert.doesNotMatch(head, /<\/script>"/);
assert.match(
  headTags(SITE, {
    url: "/x/",
    description: "d",
    socialTitle: "t",
    indexable: false,
  }),
  new RegExp(`content="${NOINDEX}"`),
);
assert.match(jsonLd([{ a: "<b>" }]), /\\u003cb>/);

// Withdrawn stories: a type that still has a live story sends readers to its
// hub, named as the hub names it (by the newest story); a type without one,
// or no type, to the journal. A path a live story holds stays the story's.
{
  const older = event({
    day: "2026-09-10",
    type: { code: "C172", name: "Cessna 172 Skyhawk", slug: "cessna-172" },
  });
  const newer = event({
    day: "2026-09-20",
    type: { code: "C172", name: "Cessna 172", slug: "cessna-172" },
  });
  const live = [newer, older];
  const retired = [
    {
      path: "/stories/a-withdrawn-1-sep-2026/",
      type: "cessna-172",
      withdrawn_at: "2026-10-01T00:00:00Z",
    },
    {
      path: "/stories/b-withdrawn-1-sep-2026/",
      type: "piper-pa-28",
      withdrawn_at: "2026-10-01T00:00:00Z",
    },
    {
      path: "/stories/c-withdrawn-1-sep-2026/",
      type: null,
      withdrawn_at: "2026-10-01T00:00:00Z",
    },
    {
      path: newer.article_path,
      type: null,
      withdrawn_at: "2026-10-01T00:00:00Z",
    },
  ];
  const { pages: withdrawn, collisions } = withdrawnPages(retired, live);
  assert.deepEqual(withdrawn, [
    {
      path: "/stories/a-withdrawn-1-sep-2026/",
      hub: "/aircraft/cessna-172/",
      label: "other Cessna 172 stories",
    },
    {
      path: "/stories/b-withdrawn-1-sep-2026/",
      hub: "/stories/",
      label: "the story journal",
    },
    {
      path: "/stories/c-withdrawn-1-sep-2026/",
      hub: "/stories/",
      label: "the story journal",
    },
  ]);
  assert.deepEqual(collisions, [newer.article_path]);
  // The site model gives the same pages, and they reach no story list, hub,
  // related link or count.
  const withdrawnModel = siteModel(live, { topics, retired });
  assert.deepEqual(withdrawnModel.withdrawn, withdrawn);
  assert.deepEqual(withdrawnModel.withdrawnCollisions, collisions);
  assert.equal(withdrawnModel.typesBySlug.get("cessna-172").name, "Cessna 172");
  assert.deepEqual(
    withdrawnModel.stories.map((story) => story.url),
    live.map((item) => item.article_path),
  );
  assert.equal(withdrawnModel.typesBySlug.get("cessna-172").count, 2);
  const linked = withdrawnModel.stories.flatMap((story) => [
    ...story.related.groups.flatMap((group) =>
      group.stories.map((item) => item.url),
    ),
    ...story.related.hubs.map((hub) => hub.url),
  ]);
  assert.ok(linked.length > 0);
  assert.ok(!linked.some((url) => url.includes("withdrawn-1-sep")));
  assert.deepEqual(withdrawnPages(undefined, live), {
    pages: [],
    collisions: [],
  });
  // The page: noindex, canonical and refresh to the hub, one escaped link,
  // and nothing to load.
  const html = withdrawnPageHtml({
    hub: "/aircraft/x/",
    label: `other "A&B" <i> stories`,
  });
  assert.match(html, /^<!doctype html>\n<html lang="en">/);
  assert.match(html, /<meta charset="utf-8">/);
  assert.match(html, /<meta name="viewport" content="[^"]+">/);
  assert.match(html, /<title>Story withdrawn<\/title>/);
  assert.match(html, /<meta name="robots" content="noindex">/);
  assert.ok(
    html.includes(`<link rel="canonical" href="${ORIGIN}/aircraft/x/">`),
  );
  assert.match(
    html,
    /<meta http-equiv="refresh" content="0; url=\/aircraft\/x\/">/,
  );
  assert.ok(
    html.includes(
      'See <a href="/aircraft/x/">other &quot;A&amp;B&quot; &lt;i&gt; stories</a>.',
    ),
  );
  assert.doesNotMatch(html, /<script|<style|<img|stylesheet|\/assets\//);
}

// Cards: a story whose type has no photo shows the representative photo of
// its class in place of a summary figure, or of no figure at all. Maps,
// charts, and type photos stay, and the article keeps its own lead.
{
  // The type table comes from a fixture reference directory.
  await fs.mkdir(output, { recursive: true });
  const references = await fs.mkdtemp(path.join(output, "site-model-types-"));
  assert.equal(
    await loadTypeTable(await writeTypeTable(references), fixtureTableSha256),
    true,
  );
  await fs.rm(references, { recursive: true, force: true });
  const helicopter = {
    ...event({ type: { code: "R44", name: "Robinson R44", slug: "r44" } }),
    synthetic: false,
  };
  const stock = classPhoto(helicopter);
  assert.equal(stock.kind, "aircraft-class-photo");
  assert.equal(stock.creditPath, "/about/image-credits/#class-helicopter");
  assert.equal(stock.note, "Representative helicopter");
  assert.match(
    stock.alt,
    /^Representative helicopter, not the aircraft in this story: /,
  );
  assert.match(
    stock.caption,
    /^Representative helicopter photo \(.+ shown\)\.$/,
  );
  assert.match(stock.src, /^\/assets\/images\/aircraft\/class-helicopter-/);
  // The R44 has no type photo; a class photo never stands in for one.
  assert.equal(aircraftPhoto(helicopter), null);
  // Only saved observations get one, as with type photos.
  assert.equal(classPhoto({ ...helicopter, synthetic: true }), null);
  assert.equal(classPhoto({ ...helicopter, media: { src: "/x.svg" } }), null);
  for (const type_code of [undefined, "ZZZZ", "NOPE"])
    assert.equal(
      classPhoto({ ...helicopter, aircraft: { type_code } }),
      null,
      type_code,
    );
  const summary = { kind: "observation-summary", src: "/event-media/a.svg" };
  const track = { kind: "track", src: "/event-media/b.svg" };
  const chart = { kind: "measurement-chart", src: "/event-media/c.svg" };
  const typePhoto = { kind: "aircraft-type-photo", src: "/assets/x.webp" };
  assert.equal(cardVisual(summary, stock), stock);
  assert.equal(cardVisual(undefined, stock), stock);
  for (const lead of [track, chart, typePhoto])
    assert.equal(cardVisual(lead, stock), lead);
  assert.equal(cardVisual(summary, null), summary);
  assert.equal(cardVisual(undefined, null), null);
  const cards = siteModel([helicopter], {
    topics,
    lead: { [helicopter.id]: summary },
    cardPhotos: { [helicopter.id]: stock },
  }).stories[0];
  assert.equal(cards.leadMedia, summary);
  assert.equal(cards.cardMedia, stock);
  assert.equal(cards.profileImage, null);
  // The altitude profile travels by its role, beside whichever figure leads.
  const profile = {
    role: "profile",
    src: "/event-media/d.svg",
    width: 1200,
    height: 400,
  };
  const profiled = siteModel([helicopter], {
    topics,
    lead: { [helicopter.id]: summary },
    profiles: { [helicopter.id]: profile },
  }).stories[0];
  assert.equal(profiled.profileImage, profile);
  assert.equal(profiled.leadMedia, summary);
  // The credits page lists the class photos in their own section, after the
  // type photos, each under a third-level heading.
  const html = imageCreditsPage.html;
  const section = html.indexOf(
    '<section aria-labelledby="class-photos"><h2 id="class-photos">Representative class photos</h2>',
  );
  assert.ok(section > 0);
  assert.ok(classPhotos.length >= 7);
  for (const photo of aircraftPhotos) {
    const heading = photo.classes ? "h3" : "h2";
    const at = html.indexOf(`<${heading} id="${photo.id}">`);
    assert.ok(at > 0, photo.id);
    assert.equal(at > section, Boolean(photo.classes), photo.id);
  }
}
// One photo of the aircraft per page. A registration photo is left out when
// the story is about a disputed identity or the identity checks found a
// conflict, since it may show another aircraft; the representative type photo
// shows on the page only when no registration photo does. Cards never show a
// registration photo, so the card photo (the type photo, else the class
// photo) stays whatever the page shows. A subjectless story shows none.
{
  const registration = { src: "https://t.plnspttrs.net/1/1_280.jpg" };
  const type = { kind: "aircraft-type-photo", src: "/assets/x.webp" };
  const stock = { kind: "aircraft-class-photo", src: "/assets/y.webp" };
  const photos = { registration, type, stock };
  const none = { registrationPhoto: null, typePhoto: null, cardPhoto: null };
  // Registration plus type: the page shows the registration photo, the card
  // keeps the type photo.
  assert.deepEqual(storyPhotos(event({ kind: "watch_type" }), photos), {
    registrationPhoto: registration,
    typePhoto: null,
    cardPhoto: type,
  });
  for (const kind of [
    "identity_conflict",
    "type_speed_mismatch",
    "type_altitude_mismatch",
    "unregistered_address",
  ])
    assert.deepEqual(
      storyPhotos(event({ kind }), photos),
      { ...none, typePhoto: type, cardPhoto: type },
      kind,
    );
  assert.deepEqual(
    storyPhotos({ ...event({}), identity_conflict: true }, photos),
    { ...none, typePhoto: type, cardPhoto: type },
  );
  assert.deepEqual(storyPhotos(event({}), { type, stock }), {
    ...none,
    typePhoto: type,
    cardPhoto: type,
  });
  // Registration plus class photo: the card keeps the class photo.
  assert.deepEqual(storyPhotos(event({}), { registration, stock }), {
    ...none,
    registrationPhoto: registration,
    cardPhoto: stock,
  });
  assert.deepEqual(
    storyPhotos(event({ kind: "type_speed_mismatch" }), {
      registration,
      stock,
    }),
    { ...none, cardPhoto: stock },
  );
  assert.deepEqual(storyPhotos(event({}), {}), none);
  assert.deepEqual(
    storyPhotos(
      { ...event({ kind: "winds_aloft_day" }), subjectless: true },
      photos,
    ),
    none,
  );
  // A card whose page lead gave way to the airframe photo shows the card
  // photo in place of the summary figure; a map still leads the card.
  const summary = { kind: "observation-summary", src: "/event-media/s.svg" };
  const map = { kind: "track", src: "/event-media/t.svg" };
  assert.equal(cardVisual(summary, type), type);
  assert.equal(cardVisual(undefined, type), type);
  assert.equal(cardVisual(map, type), map);
}
// "More about this aircraft" lists the publisher's related links (event
// `related`): its stories with their dates, then the airframe's other
// stories, newest first, up to RELATED_AIRFRAME_STORIES in all, then the
// explainer pages, each labeled "Background". A story with that list leaves
// the airframe group out of "Related stories", and the stories it lists
// appear in no other group. A subjectless story lists none.
{
  const a380 = { code: "A388", name: "Airbus A380-800", slug: "airbus-a380" };
  const older = event({ registration: "N77", type: a380, day: "2026-09-10" });
  const middle = event({ registration: "N77", type: a380, day: "2026-09-14" });
  const latest = event({ registration: "N77", type: a380, day: "2026-09-18" });
  // Newer than the story, so outside the publisher's list of earlier stories.
  const newer = event({ registration: "N77", type: a380, day: "2026-09-20" });
  const page = {
    path: "/explainers/canadian-addresses/",
    title: "When a Canadian address and its listed type disagree",
    observed_start: null,
  };
  latest.related = [
    {
      path: middle.article_path,
      title: middle.title,
      observed_start: middle.observed_start,
    },
    page,
  ];
  const winds = {
    ...event({ kind: "winds_aloft_day", primary: "performance" }),
    aircraft: {},
    subjectless: true,
    related: [
      {
        path: older.article_path,
        title: older.title,
        observed_start: older.observed_start,
      },
    ],
  };
  const model = siteModel([older, middle, latest, newer, winds], { topics });
  const byId = new Map(model.stories.map((story) => [story.id, story]));
  const story = byId.get(latest.id);
  assert.deepEqual(story.aircraftRelated, [
    {
      url: middle.article_path,
      title: middle.title,
      observedStart: middle.observed_start,
      dateLabel: "14 Sep 2026",
      background: false,
    },
    {
      url: newer.article_path,
      title: newer.title,
      observedStart: newer.observed_start,
      dateLabel: "20 Sep 2026",
      background: false,
    },
    {
      url: older.article_path,
      title: older.title,
      observedStart: older.observed_start,
      dateLabel: "10 Sep 2026",
      background: false,
    },
    {
      url: page.path,
      title: page.title,
      observedStart: null,
      dateLabel: null,
      background: true,
    },
  ]);
  assert.ok(
    !story.related.groups.some((group) => group.title === "More about N77"),
  );
  assert.ok(
    !story.related.groups
      .flatMap((group) => group.stories)
      .some((other) => [middle.id, newer.id, older.id].includes(other.id)),
  );
  // The publisher's stories and the airframe's others stop at
  // RELATED_AIRFRAME_STORIES in all.
  const days = Array.from({ length: 7 }, (_, index) =>
    event({
      registration: "N88",
      type: a380,
      day: `2026-09-0${index + 1}`,
    }),
  );
  days[6].related = [
    {
      path: days[2].article_path,
      title: days[2].title,
      observed_start: days[2].observed_start,
    },
  ];
  const capped = siteModel(days, { topics }).stories.find(
    (item) => item.id === days[6].id,
  );
  assert.equal(RELATED_AIRFRAME_STORIES, 5);
  assert.deepEqual(
    capped.aircraftRelated.map((item) => item.url),
    [2, 5, 4, 3, 1].map((index) => days[index].article_path),
  );
  // Without the list, the airframe group stays.
  assert.equal(byId.get(middle.id).aircraftRelated.length, 0);
  assert.equal(byId.get(middle.id).related.groups[0].title, "More about N77");
  // A subjectless story shows no list, whatever its event carries.
  assert.equal(byId.get(winds.id).subjectless, true);
  assert.deepEqual(byId.get(winds.id).aircraftRelated, []);
  assert.equal(byId.get(latest.id).subjectless, false);

  // Explainer pages: their sections with numbered sources, the facts as short
  // lines, and the stories whose related list links to them, newest first.
  const explainer = {
    key: "explainer:canadian-addresses",
    path: page.path,
    title: page.title,
    summary: "ICAO assigns each State a block of 24-bit aircraft addresses.",
    sections: [
      { heading: "First", text: "One.", sources: ["b"] },
      { heading: "Second", text: "Two.", sources: ["a", "b"] },
    ],
    sources: [
      { id: "a", title: "A", url: "https://a.example/", publisher: "A Org" },
      { id: "b", title: "B", url: "https://b.example/", publisher: "B Org" },
    ],
    limitations: ["General, not about one aircraft."],
    facts: {
      stories: 3,
      registries: ["adsbdb", "hexdb"],
      hypotheses: ["a stale registry entry", "a re-registration", "a typo"],
    },
    written_at: "2026-10-04T12:30:00Z",
  };
  const [built] = explainerPages([explainer], model.stories);
  assert.equal(built.url, page.path);
  assert.deepEqual(
    built.sections.map((section) => section.sources),
    [[2], [1, 2]],
  );
  assert.deepEqual(
    built.sources.map((source) => source.number),
    [1, 2],
  );
  assert.deepEqual(built.facts, [
    "Written from 3 stories in this journal.",
    "Registries consulted: ADSBdb and HexDB.",
    "Explanations it weighs: a stale registry entry, a re-registration, or a typo.",
  ]);
  assert.deepEqual(
    built.stories.map((item) => item.id),
    [latest.id],
  );
  assert.equal(built.writtenAt, explainer.written_at);
  assert.deepEqual(explainerPages(undefined, model.stories), []);
  assert.deepEqual(
    siteModel([older], { topics, explainers: [explainer] }).explainers.length,
    1,
  );

  // The altitude profile keeps the publisher's alt text and caption.
  const figure = {
    role: "profile",
    src: "/event-media/p.svg",
    width: 1200,
    height: 400,
    alt: "Chart of N1's pressure altitude over 4 minutes, from 3,000 to 3,400 ft.",
    caption: "Pressure altitude reported by N1.",
  };
  assert.deepEqual(profileFigure(figure), {
    kind: "altitude-profile",
    src: figure.src,
    width: 1200,
    height: 400,
    alt: figure.alt,
    caption: figure.caption,
  });
  assert.equal(profileFigure(null), null);
}
console.log(
  "Site model checks passed: topics, hubs, thresholds, related links, breadcrumbs, prose gate, titles, descriptions, sitemaps, feed, robots, head tags, withdrawn-story pages, card class photos, story photo rules, list page links, aircraft related lists, explainer pages, and altitude profiles.",
);
