import { createHash } from "node:crypto";
import {
  DEFAULT_PAGE_SIZE,
  dateLabel,
  sourceBadge,
} from "../../themes/wtf-adsb/assets/js/event-query.mjs";
import { ORIGIN, SITE_NAME } from "./site-settings.mjs";

// Pure functions that turn a validated snapshot into the site's pages: stories,
// topic landings, type, airframe, and operator hubs, related links, and the
// indexing gates. Slugs come from the story engine's context; nothing here
// derives a URL from free text.

// Stories per page on every static list. The journal's default page size, so
// its archive pages at that size match the static ones.
export const PER_PAGE = DEFAULT_PAGE_SIZE;
// The home page's latest stories and the weekly archive's picks per page.
export const HOME_LATEST = 8;
export const WEEKLY_PER_PAGE = 10;
// The site name (site_name in publisher.json).
export const BRAND = SITE_NAME;
const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
// RegExp.test turns undefined into the string "undefined", a valid slug.
const isSlug = (value) => typeof value === "string" && SLUG.test(value);
// The opening tags of the owned blocks in an article body (publication.py).
export const PROSE_BLOCK = /<section class="story-prose(?:\s[^"]*)?"[^>]*>/;
export const TYPE_BLOCK = /<section class="type-background(?:\s[^"]*)?"[^>]*>/;
// The indexing gate: unique prose, research, or an exceptional event.
export const UNIQUE_PROSE_WORDS = 60;
export const TOPIC_INTRO_WORDS = 250;
export const OPERATOR_HUB_STORIES = 3;
export const TYPE_HUB_STORIES = 2;
export const AIRFRAME_HUB_STORIES = 2;
export const RELATED_AIRFRAME_STORIES = 5;
// A revision more than this long after release shows an "Updated" date.
export const UPDATED_AFTER_MS = 24 * 3600 * 1000;

export const time = (value) => Date.parse(value);
// Orderings never depend on the host's locale: names sort with a fixed English
// collation; identifiers and timestamps sort by code point.
export const compareText = new Intl.Collator("en", {
  numeric: true,
  sensitivity: "base",
}).compare;
export const compareId = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
// "27 Sep 2026", the journal's one date style.
export const shortDate = dateLabel;
export const monthYear = (value) => {
  const date = new Date(value);
  return `${MONTH_NAMES[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
};
export const utcTime = (value) =>
  `${new Date(value).toISOString().slice(11, 16)} UTC`;
export const words = (text) =>
  String(text || "")
    .split(/\s+/)
    .filter(Boolean).length;
const latest = (values) =>
  values
    .filter(Boolean)
    .sort((a, b) => time(b) - time(a))
    .at(0) || null;
// A site page's published content: its title, excerpt, and body, plus the
// closing notes the build swaps into About. A page records the digest of the
// content its "updated" date describes; check-site-model.mjs fails when they
// part, so an edited page moves its sitemap lastmod.
// A page with settings filled in records the digest of its text before the
// filling, so a changed setting leaves its date alone.
export const pageDigest = (page, notes = null) => {
  const { title, html } = page.template || page;
  return createHash("sha256")
    .update(JSON.stringify([title, page.custom_excerpt, html, notes]))
    .digest("hex");
};

// Facts a template fills in by name: identity, type, operator, and place.
const FILLED_FACT = /^(?:id|op|alloc|variant)\.|^track\.place\b/;
const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// One pattern for a story's identifiers and names (registration, callsign,
// address, type, operator, place, and country), longest first, as whole words.
function filledNames(story) {
  const aircraft = story.aircraft || {};
  const facts = story.story?.facts || {};
  const names = new Set();
  for (const value of [
    story.registration,
    aircraft.registration,
    aircraft.callsign,
    aircraft.icao_address,
    aircraft.type_code,
    aircraft.type_description,
    story.type?.name,
    story.type?.code,
    story.operator?.name,
    story.operator?.designator,
    story.place,
    story.country,
    ...Object.entries(facts)
      .filter(([id]) => FILLED_FACT.test(id))
      .map(([, fact]) => fact),
  ]) {
    if (typeof value !== "string") continue;
    const name = value.toLowerCase().replace(/\s+/g, " ").trim();
    if (name.length >= 2) names.add(name);
    // "Coleman, Michigan" is also written "Coleman".
    const town = name.split(",")[0].trim();
    if (town.length >= 3) names.add(town);
  }
  if (!names.size) return null;
  const alternatives = [...names]
    .sort((a, b) => b.length - a.length || compareId(a, b))
    .map(escapeRegExp);
  return new RegExp(
    `(?<![a-z0-9])(?:${alternatives.join("|")})(?![a-z0-9])`,
    "g",
  );
}
// Sentences that differ only in their numbers or in the names a template
// fills in count as the same sentence, so a template about another aircraft,
// place or measurement is not unique prose. Any word with a digit (a value,
// registration, designator, or time) is one placeholder.
const sentenceKey = (text, names = null) =>
  (names ? text.toLowerCase().replace(names, " § ") : text.toLowerCase())
    .replace(/\S*\d\S*/g, " # ")
    .replace(/[^a-z#§]+/g, " ")
    .trim();
const sentencesOf = (text) =>
  String(text || "")
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter(Boolean);

// Meta descriptions stop at 160 characters on a sentence or word boundary.
export function truncate(text, limit = 160) {
  const value = String(text || "")
    .replace(/\s+/g, " ")
    .trim();
  if (value.length <= limit) return value;
  let sentence = "";
  for (const part of sentencesOf(value)) {
    const next = sentence ? `${sentence} ${part}` : part;
    if (next.length > limit) break;
    sentence = next;
  }
  if (sentence.length >= limit * 0.5) return sentence;
  const cut = value.slice(0, limit - 1);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 40 ? cut.lastIndexOf(" ") : cut.length).replace(/[\s,;:.]+$/, "")}…`;
}

// An https URL from validated routing, else null.
const httpsUrl = (value) =>
  typeof value === "string" && /^https:\/\/[^\s"<>]+$/.test(value)
    ? value
    : null;

export function topicIndex(topicsData) {
  const topics = topicsData?.topics;
  const sections = topicsData?.sections;
  if (!topics || typeof topics !== "object" || !sections)
    throw new Error("Topic data needs topics and sections");
  const byKind = new Map();
  for (const [slug, topic] of Object.entries(topics)) {
    if (!isSlug(slug) || !Object.hasOwn(sections, topic.section))
      throw new Error(`Invalid topic ${slug}`);
    for (const kind of topic.kinds || []) byKind.set(kind, slug);
  }
  return {
    topics,
    sections,
    // The story's own routing, then the kind table. An unlisted kind falls
    // back to the first topic of its section (the event type), exactly as
    // story_facts.topic_for does, so Ghost and this site agree; never to a
    // new URL.
    topicFor(event) {
      const fromContext = event.context?.topic;
      if (typeof fromContext === "string" && Object.hasOwn(topics, fromContext))
        return fromContext;
      return (
        byKind.get(event.kind) ||
        Object.keys(topics).find(
          (slug) => topics[slug].section === event.primary_type,
        ) ||
        Object.keys(topics)[0]
      );
    },
  };
}

// Type-profile facts ("tp.*") with their sources, matched by the engine's id
// convention (tp.A388.1 cites src.A388.1) and otherwise listed together.
function profileOf(story) {
  if (!story?.facts) return { facts: [], sources: [] };
  const sources = new Map(
    (story.sources || []).map((source) => [source.id, source]),
  );
  const facts = Object.entries(story.facts)
    .filter(([id, value]) => id.startsWith("tp.") && typeof value === "string")
    .map(([id, text]) => ({
      id,
      text,
      source: sources.get(`src.${id.slice(3)}`) || null,
    }));
  const cited = new Set(facts.map((fact) => fact.source?.id).filter(Boolean));
  return {
    facts,
    sources: facts.length
      ? [...sources.values()].filter(
          (source) => cited.has(source.id) || !cited.size,
        )
      : [],
  };
}

// A story's type routing when it names a hub slug, else null. Every such
// type gets a hub.
const storyType = (event) => {
  const kind = event.context?.type;
  return kind && isSlug(kind.slug) ? kind : null;
};
export const typeHubUrl = (slug) => `/aircraft/${slug}/`;
// The story journal, where a withdrawn story without a type hub points.
export const JOURNAL_URL = "/stories/";
// A withdrawn story's page: noindex, and it sends readers on at once.
export const WITHDRAWN_ROBOTS = "noindex";

// A card's image: the story's lead (a track map, chart, or type photo),
// except that a summary card, or no figure at all, gives way to the card
// photo (storyPhotos) when there is one. Article pages keep their lead.
export function cardVisual(lead, cardPhoto) {
  return cardPhoto && (!lead || lead.kind === "observation-summary")
    ? cardPhoto
    : lead || null;
}

// Kinds whose story is that the aircraft's identifiers disagree, so a photo
// found by its listed registration may show another aircraft.
export const NO_REGISTRATION_PHOTO = new Set([
  "identity_conflict",
  "type_speed_mismatch",
  "type_altitude_mismatch",
  "unregistered_address",
]);

// The photos a story may show. Its page shows at most one of the aircraft:
// the registration photo (Planespotters) unless the kind disputes the
// identity or the identity checks found a conflict (the publisher's
// identity_conflict flag), else the representative type photo. Cards never
// show a registration photo, so the card photo is the type photo, else the
// representative photo of the type's class, whatever the page shows. A
// subjectless story names no aircraft, so it shows none.
export function storyPhotos(event, { registration, type, stock } = {}) {
  if (event.subjectless)
    return { registrationPhoto: null, typePhoto: null, cardPhoto: null };
  const registrationPhoto =
    registration &&
    !NO_REGISTRATION_PHOTO.has(event.kind) &&
    event.identity_conflict !== true
      ? registration
      : null;
  return {
    registrationPhoto,
    typePhoto: registrationPhoto ? null : type || null,
    cardPhoto: type || stock || null,
  };
}

// One link of the publisher's related list (event `related`): a story of the
// same aircraft with its date, or an explainer page (null observed_start),
// which the page labels "Background" in place of a date.
const relatedLink = (item) => ({
  url: item.path,
  title: item.title,
  observedStart: item.observed_start,
  dateLabel:
    item.observed_start === null ? null : shortDate(item.observed_start),
  background: item.observed_start === null,
});

// The altitude profile figure (media role "profile") as the page shows it,
// below the track map or in its place. Its caption names both aircraft only
// for an advisory whose profile draws the other one, as its own alt text says.
export const PROFILE_ALT = "Altitude over time for this episode";
const OTHER_AIRCRAFT = /beside the other aircraft in the advisory/;
export function profileFigure(profile, kind) {
  if (!profile) return null;
  const both = kind === "tcas_ra" && OTHER_AIRCRAFT.test(profile.alt || "");
  return {
    kind: "altitude-profile",
    src: profile.src,
    width: profile.width,
    height: profile.height,
    alt: PROFILE_ALT,
    caption: both
      ? "Pressure altitude against time for both aircraft in the advisory."
      : "Pressure altitude against time.",
  };
}

export function storyModel(
  event,
  { topics, html = "", leadMedia, photo, cardPhoto, profileImage },
) {
  const story = event.story || null;
  const context = event.context || {};
  const topic = topics.topicFor(event);
  const kind = storyType(event);
  const registration = event.aircraft?.registration || null;
  const registrationSlug = isSlug(context.registration_slug)
    ? context.registration_slug
    : null;
  const released = event.released_at || null;
  // Structured data dates a story by its release; a story without one falls
  // back to its observation start, the date Ghost publishes it under. The
  // byline keeps showing both dates.
  const datePublished = released || event.observed_start;
  // dateModified and the sitemap's lastmod record the last prose or evidence
  // change, which is the release until a revision. The snapshot's revised_at
  // is Ghost's updated_at, which also moves when an evidence card is
  // refreshed, so it never dates a revision.
  const revised = (released && event.content_revised_at) || null;
  const dateModified = revised || datePublished;
  // Readers see "Updated" only for a revision more than a day after release.
  const updatedAt =
    revised && time(revised) - time(released) > UPDATED_AFTER_MS
      ? revised
      : null;
  const paragraphs = story?.paragraphs || [];
  return {
    id: event.id,
    event,
    kind: event.kind,
    primaryType: event.primary_type,
    url: event.article_path,
    slug: event.article_path.split("/").filter(Boolean).at(-1),
    headline: event.title,
    dek: event.summary,
    seoTitle: story?.seo_title || event.title,
    story,
    paragraphs,
    profile: profileOf(story),
    // The owned prose and "About the type" blocks the exporter put in the body.
    proseInBody: PROSE_BLOCK.test(html),
    typeInBody: TYPE_BLOCK.test(html),
    observedStart: event.observed_start,
    observedEnd: event.observed_end,
    observedDate: event.observed_start.slice(0, 10),
    datePublished,
    dateModified,
    releasedAt: released,
    updatedAt,
    topic,
    section: topics.topics[topic].section,
    type: kind,
    typeKey: kind
      ? `slug:${kind.slug}`
      : event.aircraft?.type_code
        ? `code:${event.aircraft.type_code}`
        : null,
    typeLabel:
      kind?.name ||
      event.aircraft?.type_description ||
      event.aircraft?.type_code ||
      null,
    registration,
    registrationSlug,
    // The airframe hub's key, type, and registration slug, so "More about REG"
    // and the hub list the same stories; a story without both falls back to
    // its registration.
    airframeKey:
      kind && registrationSlug
        ? `${kind.slug}/${registrationSlug}`
        : registration
          ? `registration:${registration.toUpperCase()}`
          : null,
    operator:
      context.operator && isSlug(context.operator.slug)
        ? context.operator
        : null,
    place: context.place || null,
    country: context.country || null,
    aircraft: event.aircraft || {},
    // The readings' reception source as a badge, or null for a source the
    // badge does not name.
    sourceBadge: sourceBadge(event.source),
    weekly: Boolean(event.weekly_spotlight),
    exceptional: Boolean(event.priority_evidence),
    leadMedia: leadMedia || null,
    cardMedia: cardVisual(leadMedia, cardPhoto),
    // The type or class photo cards fall back to (storyPhotos).
    cardPhoto: cardPhoto || null,
    // The altitude profile figure (media role "profile"), which the page places below the map.
    profileImage: profileImage || null,
    // A story about a day, not an aircraft (winds_aloft_day): no photo, type
    // box, map, or related list.
    subjectless: event.subjectless === true,
    // "More about this aircraft": the publisher's related links, the stories
    // first, then the explainer pages.
    aircraftRelated: event.subjectless
      ? []
      : (event.related || []).map(relatedLink),
    photo: photo || null,
    html,
  };
}

// Publication order: release time, then observation time, then the event id.
export function byRelease(a, b) {
  return (
    time(a.releasedAt || a.datePublished) -
      time(b.releasedAt || b.datePublished) ||
    time(a.observedStart) - time(b.observedStart) ||
    compareId(a.id, b.id)
  );
}

// Words in prose sentences that no earlier story used. A story is compared
// only with stories released before it, so a later story never changes an
// earlier page's count. Each sentence counts once per story, and a sentence
// that repeats the story's own dek or headline (shown on list pages) does not
// count.
export function uniqueProseWords(stories) {
  const seen = new Set();
  const result = new Map();
  for (const story of [...stories].sort(byRelease)) {
    const names = filledNames(story);
    const listed = new Set(
      [...sentencesOf(story.dek), story.headline || ""]
        .map((sentence) => sentenceKey(sentence, names))
        .filter(Boolean),
    );
    const sentences = story.paragraphs
      .flatMap(sentencesOf)
      .map((sentence) => [sentence, sentenceKey(sentence, names)]);
    const counted = new Set();
    let total = 0;
    for (const [sentence, key] of sentences) {
      if (!key || seen.has(key) || listed.has(key) || counted.has(key))
        continue;
      counted.add(key);
      total += words(sentence);
    }
    result.set(story.id, total);
    for (const key of listed) seen.add(key);
    for (const [, key] of sentences) if (key) seen.add(key);
  }
  return result;
}

function byNewest(a, b) {
  return time(b.observedStart) - time(a.observedStart) || compareId(a.id, b.id);
}

export const pageUrl = (base, page) =>
  page === 1 ? base : `${base}page/${page}/`;

export function paginate(items, base, perPage = PER_PAGE) {
  const pages = Math.max(1, Math.ceil(items.length / perPage));
  return Array.from({ length: pages }, (_, index) => {
    const page = index + 1;
    return {
      page,
      pages,
      base,
      url: pageUrl(base, page),
      items: items.slice(index * perPage, page * perPage),
    };
  });
}

// The links of one page of a list, for its navigation above and below the
// list (partials page-steps and page-numbers) and the head's prev and next.
// Every page number is a link, so each story is three clicks from home; the
// script condenses the list only for the pages it draws (pageNumbers).
// "noun" names what the list holds, newest first, in the step links' labels.
export function pageLinks(
  chunk,
  {
    label = "Story pages",
    noun = "stories",
    url = (page) => pageUrl(chunk.base, page),
  } = {},
) {
  if (chunk.pages < 2) return null;
  return {
    label,
    noun,
    page: chunk.page,
    total: chunk.pages,
    prev: chunk.page > 1 ? url(chunk.page - 1) : null,
    next: chunk.page < chunk.pages ? url(chunk.page + 1) : null,
    numbers: Array.from({ length: chunk.pages }, (_, index) => ({
      number: index + 1,
      url: url(index + 1),
      current: index + 1 === chunk.page,
    })),
  };
}

// Stories grouped by a key, each group newest first like the input.
function groupBy(stories, key) {
  const groups = new Map();
  for (const story of stories) {
    const value = key(story);
    if (value == null) continue;
    if (!groups.has(value)) groups.set(value, []);
    groups.get(value).push(story);
  }
  return groups;
}

function hubSummary(stories) {
  const sorted = [...stories].sort(byNewest);
  const first = sorted.at(-1);
  const last = sorted[0];
  return {
    count: sorted.length,
    since: first ? monthYear(first.observedStart) : "",
    range:
      first && last
        ? first.observedDate === last.observedDate
          ? shortDate(first.observedStart)
          : `${shortDate(first.observedStart)} to ${shortDate(last.observedStart)}`
        : "",
    lastmod: latest(sorted.map((story) => story.dateModified)),
  };
}

export function siteModel(
  events,
  {
    topics,
    topicCopy = {},
    articles = {},
    lead = {},
    photos = {},
    cardPhotos = {},
    profiles = {},
    retired = [],
    explainers = [],
  },
) {
  const stories = events
    .map((event) =>
      storyModel(event, {
        topics,
        html: articles[event.id] || "",
        leadMedia: lead[event.id],
        photo: photos[event.id],
        cardPhoto: cardPhotos[event.id],
        profileImage: profiles[event.id],
      }),
    )
    .sort(byNewest);
  const routes = new Set();
  for (const story of stories) {
    if (routes.has(story.url)) throw new Error(`Duplicate story ${story.url}`);
    routes.add(story.url);
  }
  const unique = uniqueProseWords(stories);
  for (const story of stories) {
    story.uniqueWords = unique.get(story.id);
    story.indexable =
      story.uniqueWords >= UNIQUE_PROSE_WORDS ||
      story.weekly ||
      story.exceptional;
  }
  const anyIndexable = (list) => list.some((story) => story.indexable);
  const byTopic = groupBy(stories, (story) => story.topic);

  // Topic landings: every topic is built; an empty one is indexed only with a
  // sourced intro long enough to stand alone.
  const sections = Object.entries(topics.sections).map(([slug, title]) => ({
    slug,
    title,
    topics: [],
  }));
  const topicList = Object.entries(topics.topics).map(([slug, topic]) => {
    const copy = topicCopy[slug] || {};
    const members = byTopic.get(slug) || [];
    const introWords = (copy.intro || []).reduce(
      (sum, paragraph) => sum + words(paragraph),
      0,
    );
    const entry = {
      slug,
      title: topic.title,
      // The <title> uses the copy's search phrase when there is one.
      seoTitle: copy.title || topic.title,
      heading: copy.heading || topic.title,
      description: copy.description || null,
      intro: copy.intro || [],
      introWords,
      sources: copy.sources || [],
      guide: copy.guide || [],
      section: topic.section,
      sectionTitle: topics.sections[topic.section],
      url: `/topics/${slug}/`,
      stories: members,
      count: members.length,
      // A topic asks to be indexed with a story that is itself indexed, or a
      // sourced intro long enough to stand alone.
      indexable:
        anyIndexable(members) ||
        (introWords >= TOPIC_INTRO_WORDS && (copy.sources || []).length > 0),
      ...hubSummary(members),
    };
    sections
      .find((section) => section.slug === topic.section)
      ?.topics.push(entry);
    return entry;
  });
  const topicsBySlug = new Map(topicList.map((topic) => [topic.slug, topic]));
  for (const topic of topicList)
    topic.related = (topicCopy[topic.slug]?.related || [])
      .map((slug) => topicsBySlug.get(slug))
      .filter(Boolean)
      .concat(
        topicList.filter(
          (other) =>
            other.section === topic.section &&
            other.slug !== topic.slug &&
            !(topicCopy[topic.slug]?.related || []).includes(other.slug),
        ),
      )
      .slice(0, 6);

  // Type hubs: every typed story's type is built and linked. Like a topic, a
  // hub asks to be indexed with a sourced profile, or with enough stories of
  // which at least one is itself indexed; a list of thin pages stays out.
  const types = new Map();
  for (const story of stories) {
    if (!story.type) continue;
    const slug = story.type.slug;
    if (!types.has(slug))
      types.set(slug, {
        slug,
        code: story.type.code,
        name: story.type.name,
        sameAs: null,
        url: typeHubUrl(slug),
        stories: [],
        facts: new Map(),
        sources: new Map(),
      });
    const hub = types.get(slug);
    hub.stories.push(story);
    // The type's verified reference page (Wikipedia), when routing names one.
    hub.sameAs ||= httpsUrl(story.type.same_as);
    for (const fact of story.profile.facts)
      if (!hub.facts.has(fact.id)) hub.facts.set(fact.id, fact);
    for (const source of story.profile.sources)
      if (!hub.sources.has(source.url)) hub.sources.set(source.url, source);
  }
  const typeList = [...types.values()]
    .map((hub) => ({
      ...hub,
      facts: [...hub.facts.values()],
      sources: [...hub.sources.values()],
      ...hubSummary(hub.stories),
    }))
    .map((hub) => ({
      ...hub,
      hasProfile: hub.facts.length > 0,
      indexable:
        hub.facts.length > 0 ||
        (hub.count >= TYPE_HUB_STORIES && anyIndexable(hub.stories)),
    }))
    .sort((a, b) => compareText(a.name, b.name) || compareId(a.slug, b.slug));
  const typesBySlug = new Map(typeList.map((hub) => [hub.slug, hub]));

  // Airframe hubs exist only for airframes with two or more stories, and ask
  // to be indexed when one of them is.
  const airframes = new Map();
  for (const story of stories) {
    if (!story.type || !story.registrationSlug || !story.registration) continue;
    const key = story.airframeKey;
    if (!airframes.has(key))
      airframes.set(key, {
        key,
        typeSlug: story.type.slug,
        slug: story.registrationSlug,
        registration: story.registration,
        url: `/aircraft/${story.type.slug}/${story.registrationSlug}/`,
        stories: [],
      });
    airframes.get(key).stories.push(story);
  }
  const airframeList = [...airframes.values()]
    .filter((hub) => hub.stories.length >= AIRFRAME_HUB_STORIES)
    .map((hub) => {
      const operators = [
        ...new Map(
          hub.stories
            .filter((story) => story.operator)
            .map((story) => [story.operator.slug, story.operator]),
        ).values(),
      ];
      return {
        ...hub,
        type: typesBySlug.get(hub.typeSlug),
        operators,
        countries: [
          ...new Set(hub.stories.map((story) => story.country).filter(Boolean)),
        ],
        indexable: anyIndexable(hub.stories),
        ...hubSummary(hub.stories),
      };
    })
    .sort(
      (a, b) =>
        compareText(a.registration, b.registration) || compareId(a.key, b.key),
    );
  const airframesByKey = new Map(airframeList.map((hub) => [hub.key, hub]));

  // Operator hubs come only from broadcast callsign designators. One asks to
  // be indexed with enough stories, at least one of them indexed.
  const operators = new Map();
  for (const story of stories) {
    if (!story.operator) continue;
    const slug = story.operator.slug;
    if (!operators.has(slug))
      operators.set(slug, {
        slug,
        name: story.operator.name,
        designator: story.operator.designator || null,
        basis: story.operator.basis || null,
        url: `/operators/${slug}/`,
        stories: [],
      });
    operators.get(slug).stories.push(story);
  }
  const operatorList = [...operators.values()]
    .map((hub) => ({
      ...hub,
      types: [
        ...new Set(
          hub.stories.map((story) => story.type?.slug).filter(Boolean),
        ),
      ]
        .map((slug) => typesBySlug.get(slug))
        .filter(Boolean),
      indexable:
        hub.stories.length >= OPERATOR_HUB_STORIES && anyIndexable(hub.stories),
      ...hubSummary(hub.stories),
    }))
    .sort((a, b) => compareText(a.name, b.name) || compareId(a.slug, b.slug));
  const operatorsBySlug = new Map(operatorList.map((hub) => [hub.slug, hub]));
  const framesByType = new Map();
  for (const frame of airframeList) {
    if (!framesByType.has(frame.typeSlug)) framesByType.set(frame.typeSlug, []);
    framesByType.get(frame.typeSlug).push(frame);
  }
  const operatorsByType = new Map();
  for (const operator of operatorList)
    for (const type of operator.types) {
      if (!operatorsByType.has(type.slug)) operatorsByType.set(type.slug, []);
      operatorsByType.get(type.slug).push(operator);
    }
  for (const hub of typeList) {
    hub.airframes = framesByType.get(hub.slug) || [];
    hub.operators = operatorsByType.get(hub.slug) || [];
  }

  // Related links: the airframe, the type, then the topic, never repeating.
  // The airframe hub lists every story, so a story page links the five newest.
  // Each list is read only until it yields enough unused stories.
  // A story with the publisher's related list ("More about this aircraft")
  // shows it in place of the airframe group, and its stories join no other
  // group; the airframe hub's link still lists every story.
  const byAirframe = groupBy(stories, (story) => story.airframeKey);
  const byType = groupBy(stories, (story) => story.typeKey);
  const byUrl = new Map(stories.map((story) => [story.url, story]));
  for (const story of stories) {
    const used = new Set([story.id]);
    const listed = story.aircraftRelated.filter((item) => !item.background);
    for (const item of listed)
      if (byUrl.has(item.url)) used.add(byUrl.get(item.url).id);
    const take = (list, limit) => {
      const picked = [];
      for (const other of list || []) {
        if (picked.length >= limit) break;
        if (used.has(other.id)) continue;
        used.add(other.id);
        picked.push(other);
      }
      return picked;
    };
    const airframe = listed.length
      ? []
      : take(byAirframe.get(story.airframeKey), RELATED_AIRFRAME_STORIES);
    const sameType = take(byType.get(story.typeKey), 3);
    const sameTopic = take(byTopic.get(story.topic), 2);
    const topic = topicsBySlug.get(story.topic);
    const typeHub = story.type ? typesBySlug.get(story.type.slug) : null;
    const airframeHub = airframesByKey.get(story.airframeKey) || null;
    const operatorHub = story.operator
      ? operatorsBySlug.get(story.operator.slug)
      : null;
    story.topicHub = topic;
    story.typeHub = typeHub || null;
    story.airframeHub = airframeHub || null;
    story.operatorHub = operatorHub || null;
    story.related = {
      groups: [
        airframe.length && {
          title: `More about ${story.registration}`,
          stories: airframe,
        },
        sameType.length && {
          title: `Other ${story.typeLabel} stories`,
          stories: sameType,
        },
        sameTopic.length && {
          title: `More in ${topic.title}`,
          stories: sameTopic,
        },
      ].filter(Boolean),
      hubs: [
        typeHub && { label: `All ${typeHub.name} stories`, url: typeHub.url },
        airframeHub && {
          label: `${airframeHub.registration}: every story`,
          url: airframeHub.url,
        },
        operatorHub && {
          label: `${operatorHub.name} in the journal`,
          url: operatorHub.url,
        },
        { label: topic.title, url: topic.url },
      ].filter(Boolean),
    };
    // Breadcrumbs follow the aircraft when it has a hub, otherwise the topic.
    // The story's own crumb uses the fixed SEO title, so an AI headline
    // upgrade never changes structured data that identifies the page.
    story.breadcrumbs = typeHub
      ? [
          { name: "Home", url: "/" },
          { name: "Aircraft", url: "/aircraft/" },
          { name: typeHub.name, url: typeHub.url },
          ...(airframeHub
            ? [{ name: airframeHub.registration, url: airframeHub.url }]
            : []),
          { name: story.seoTitle },
        ]
      : [
          { name: "Home", url: "/" },
          { name: "Topics", url: "/topics/" },
          { name: topic.title, url: topic.url },
          { name: story.seoTitle },
        ];
  }

  // Withdrawn stories are no stories: they reach no list, hub, count, feed,
  // search or sitemap, only their own redirect pages.
  const withdrawn = withdrawnPages(
    retired,
    stories.map((story) => story.event),
  );
  return {
    stories,
    topics: topicList,
    topicsBySlug,
    sections,
    types: typeList,
    typesBySlug,
    airframes: airframeList,
    operators: operatorList,
    withdrawn: withdrawn.pages,
    withdrawnCollisions: withdrawn.collisions,
    explainers: explainerPages(explainers, stories),
    lastmod: latest(stories.map((story) => story.dateModified)),
  };
}

// The registries an explainer's facts name, as their sites name themselves.
const REGISTRY_NAMES = { adsbdb: "ADSBdb", hexdb: "HexDB" };
// "a, b, and c" (or "or"), with the serial comma.
const listText = (items, word = "and") =>
  items.length < 3
    ? items.join(` ${word} `)
    : `${items.slice(0, -1).join(", ")}, ${word} ${items.at(-1)}`;

// The snapshot's explainer pages (its `explainers` key, written once by the
// weekly research): the sections with their numbered sources, the facts the
// page was written from as short lines, and the stories whose related list
// links to it, in the order given (newest first in the site model).
export function explainerPages(explainers = [], stories = []) {
  return explainers.map((page) => {
    const numbers = new Map(
      page.sources.map((source, index) => [source.id, index + 1]),
    );
    const { facts } = page;
    return {
      key: page.key,
      url: page.path,
      title: page.title,
      summary: page.summary,
      sections: page.sections.map((section) => ({
        heading: section.heading,
        text: section.text,
        sources: section.sources
          .map((id) => numbers.get(id))
          .sort((a, b) => a - b),
      })),
      sources: page.sources.map((source, index) => ({
        title: source.title,
        url: source.url,
        publisher: source.publisher,
        number: index + 1,
      })),
      limitations: [...page.limitations],
      facts: [
        `Written from ${facts.stories} ${facts.stories === 1 ? "story" : "stories"} in this journal.`,
        `Registries consulted: ${listText(facts.registries.map((name) => REGISTRY_NAMES[name] || name))}.`,
        `Explanations it weighs: ${listText(facts.hypotheses, "or")}.`,
      ],
      writtenAt: page.written_at,
      stories: stories.filter((story) =>
        story.aircraftRelated.some(
          (item) => item.background && item.url === page.path,
        ),
      ),
    };
  });
}

// The pages for withdrawn stories' old URLs (a published snapshot's `retired`
// list), given the live events newest first. Each sends readers to its type's
// hub while a live story still builds that hub, named as the hub names its
// type, and otherwise to the journal. A path a live story holds stays the
// story's and is returned in `collisions` for the build to report.
export function withdrawnPages(retired = [], events = []) {
  const live = new Set(events.map((event) => event.article_path));
  const hubs = new Map();
  for (const event of events) {
    const kind = storyType(event);
    if (kind && !hubs.has(kind.slug)) hubs.set(kind.slug, kind.name);
  }
  const pages = [];
  const collisions = [];
  for (const entry of retired) {
    if (live.has(entry.path)) {
      collisions.push(entry.path);
      continue;
    }
    const name = isSlug(entry.type) ? hubs.get(entry.type) : undefined;
    pages.push(
      name
        ? {
            path: entry.path,
            hub: typeHubUrl(entry.type),
            label: `other ${name} stories`,
          }
        : { path: entry.path, hub: JOURNAL_URL, label: "the story journal" },
    );
  }
  return { pages, collisions };
}

const escapeHtml = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[character],
  );

// A withdrawn story's page: noindex, canonical to the page it sends readers
// to, a refresh there at once, and one sentence with the link. It loads
// nothing, so it needs no theme assets and runs no script.
export const withdrawnPageHtml = ({ hub, label }) => `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Story withdrawn</title>
    <meta name="robots" content="${WITHDRAWN_ROBOTS}">
    <link rel="canonical" href="${escapeHtml(ORIGIN + hub)}">
    <meta http-equiv="refresh" content="0; url=${escapeHtml(hub)}">
  </head>
  <body>
    <p>This story was withdrawn. See <a href="${escapeHtml(hub)}">${escapeHtml(label)}</a>.</p>
  </body>
</html>
`;

// Archive and hub lists group entries under their UTC observation dates.
// (A field named "date" would call Ghost's date helper in a template.)
export function datedGroups(stories) {
  const groups = [];
  for (const story of stories) {
    const label = shortDate(story.observedStart);
    if (groups.at(-1)?.label !== label)
      groups.push({ label, datetime: story.observedDate, stories: [] });
    groups.at(-1).stories.push(story);
  }
  return groups;
}
