import Handlebars from "handlebars";
import { author } from "./demo-content.mjs";
import {
  BRAND,
  byRelease,
  shortDate,
  truncate,
  utcTime,
} from "./site-model.mjs";
import { SITE_TAGLINE, contactEmail } from "./site-settings.mjs";

// Head tags and structured data for the production build. Every value is
// deterministic: titles, images, and dates never change when AI prose replaces
// the template text; headings and descriptions do.

const escape = Handlebars.escapeExpression;
// The home heading is site_tagline in publisher.json; the title names the site first.
export const HOME_HEADING = SITE_TAGLINE;
export const HOME_TITLE = `${BRAND}: ${HOME_HEADING}`;
export const AUTHOR = author;
export const INDEX = "index, follow, max-image-preview:large";
export const NOINDEX = "noindex, follow";

// The site name follows a title while the whole stays within 72 characters,
// room for the story engine's 60-character SEO title plus " | " and a site
// name of up to nine characters. A longer title keeps its own words.
export const TITLE_LIMIT = 72;
export const withBrand = (title) =>
  title.includes(BRAND) || `${title} | ${BRAND}`.length > TITLE_LIMIT
    ? title
    : `${title} | ${BRAND}`;
export const pageTitle = (title, page = 1) =>
  withBrand(page > 1 ? `${title}, page ${page}` : title);

// Story <title>s are unique: a repeated seo_title gains the observation date,
// then the time. The engine already avoids repeats; this guards older stories.
// Earlier releases keep their titles, so a new story never retitles an old one.
export function storyTitles(stories) {
  const seen = new Set();
  const titles = new Map();
  for (const story of [...stories].sort(byRelease)) {
    let title = withBrand(story.seoTitle);
    for (const suffix of [
      `, ${shortDate(story.observedStart)}`,
      `, ${shortDate(story.observedStart)} ${utcTime(story.observedStart)}`,
      `, ${story.slug}`,
    ]) {
      if (!seen.has(title)) break;
      title = withBrand(`${story.seoTitle}${suffix}`);
    }
    seen.add(title);
    titles.set(story.id, title);
  }
  return titles;
}

// A story with engine prose describes itself with its dek. An older story's
// summary is shortened, and a repeated one gains its headline and date.
export function storyDescriptions(stories) {
  const seen = new Set();
  const result = new Map();
  for (const story of [...stories].sort(byRelease)) {
    let text = truncate(story.dek);
    if (!story.story && seen.has(text))
      text = truncate(
        `${story.headline}, ${shortDate(story.observedStart)}. ${story.dek}`,
      );
    seen.add(text);
    result.set(story.id, text);
  }
  return result;
}

export const absolute = (origin, path) => new URL(path, origin).href;

export function website(origin) {
  return {
    "@type": "WebSite",
    "@id": `${origin}/#website`,
    url: `${origin}/`,
    name: BRAND,
    alternateName: new URL(origin).hostname,
    inLanguage: "en",
    publisher: { "@id": `${origin}/#org` },
  };
}

// The site's organization; it names an email address only when one is configured.
export function organization(origin, logo, email = contactEmail()) {
  return {
    "@type": "Organization",
    "@id": `${origin}/#org`,
    name: BRAND,
    url: `${origin}/`,
    ...(email ? { email } : {}),
    ...(logo
      ? {
          logo: {
            "@type": "ImageObject",
            url: absolute(origin, logo.src),
            width: logo.width,
            height: logo.height,
          },
        }
      : {}),
  };
}

export function breadcrumbList(origin, crumbs) {
  return {
    "@type": "BreadcrumbList",
    itemListElement: crumbs.map((crumb, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: crumb.name,
      ...(crumb.url ? { item: absolute(origin, crumb.url) } : {}),
    })),
  };
}

export function collectionPage(origin, { url, name, description, stories }) {
  return {
    "@type": "CollectionPage",
    "@id": absolute(origin, url),
    url: absolute(origin, url),
    name,
    ...(description ? { description } : {}),
    inLanguage: "en",
    isPartOf: { "@id": `${origin}/#website` },
    mainEntity: {
      "@type": "ItemList",
      numberOfItems: stories.length,
      itemListElement: stories.map((story, index) => ({
        "@type": "ListItem",
        position: index + 1,
        url: absolute(origin, story.url),
        name: story.headline,
      })),
    },
  };
}

export function blogPosting(origin, story, { description, images, logo }) {
  const url = absolute(origin, story.url);
  const keywords = [
    story.type?.name || story.typeLabel,
    story.registration,
    story.operator?.name,
    "ADS-B",
  ].filter(Boolean);
  return {
    "@type": "BlogPosting",
    "@id": `${url}#article`,
    mainEntityOfPage: { "@type": "WebPage", "@id": url },
    url,
    headline: story.headline,
    description,
    ...(images.length
      ? { image: images.map((image) => absolute(origin, image.src)) }
      : {}),
    datePublished: story.datePublished,
    dateModified: story.dateModified,
    contentReferenceTime: story.observedStart,
    articleSection: story.topicHub.title,
    keywords: [...new Set(keywords)],
    ...(story.typeHub
      ? {
          about: {
            "@type": "Thing",
            name: story.typeHub.name,
            url: absolute(origin, story.typeHub.url),
            // The type's verified reference page, when its routing has one.
            ...(story.typeHub.sameAs ? { sameAs: story.typeHub.sameAs } : {}),
          },
        }
      : {}),
    inLanguage: "en",
    isPartOf: { "@id": `${origin}/#website` },
    author: {
      "@type": "Person",
      name: AUTHOR.name,
      url: absolute(origin, AUTHOR.url),
    },
    publisher: organization(origin, logo),
  };
}

export function jsonLd(nodes) {
  return `<script type="application/ld+json">${JSON.stringify({
    "@context": "https://schema.org",
    "@graph": nodes,
  }).replaceAll("<", "\\u003c")}</script>`;
}

// The emulated ghost_head for one production page.
export function headTags(origin, seo) {
  const url = absolute(origin, seo.url);
  const tags = [
    `<meta name="description" content="${escape(seo.description)}">`,
    `<link rel="canonical" href="${escape(url)}">`,
    `<meta name="robots" content="${seo.indexable ? INDEX : NOINDEX}">`,
    `<meta property="og:site_name" content="${BRAND}">`,
    `<meta property="og:type" content="${seo.article ? "article" : "website"}">`,
    `<meta property="og:title" content="${escape(seo.socialTitle)}">`,
    `<meta property="og:description" content="${escape(seo.description)}">`,
    `<meta property="og:url" content="${escape(url)}">`,
    `<meta property="og:locale" content="en_US">`,
  ];
  if (seo.article) {
    tags.push(
      `<meta property="article:published_time" content="${escape(seo.article.published)}">`,
      `<meta property="article:modified_time" content="${escape(seo.article.modified)}">`,
      `<meta property="article:section" content="${escape(seo.article.section)}">`,
      ...seo.article.tags.map(
        (tag) => `<meta property="article:tag" content="${escape(tag)}">`,
      ),
    );
  }
  const image = seo.image;
  tags.push(
    `<meta name="twitter:card" content="${image ? "summary_large_image" : "summary"}">`,
    `<meta name="twitter:title" content="${escape(seo.socialTitle)}">`,
    `<meta name="twitter:description" content="${escape(seo.description)}">`,
  );
  if (image) {
    const src = escape(absolute(origin, image.src));
    tags.push(
      `<meta property="og:image" content="${src}">`,
      `<meta property="og:image:type" content="${escape(image.type)}">`,
      `<meta property="og:image:width" content="${image.width}">`,
      `<meta property="og:image:height" content="${image.height}">`,
      `<meta property="og:image:alt" content="${escape(image.alt)}">`,
      `<meta name="twitter:image" content="${src}">`,
      `<meta name="twitter:image:alt" content="${escape(image.alt)}">`,
    );
  }
  if (seo.feed)
    tags.push(
      `<link rel="alternate" type="application/rss+xml" title="${BRAND} stories" href="${escape(absolute(origin, "/rss/"))}">`,
    );
  if (seo.prev)
    tags.push(`<link rel="prev" href="${escape(absolute(origin, seo.prev))}">`);
  if (seo.next)
    tags.push(`<link rel="next" href="${escape(absolute(origin, seo.next))}">`);
  return tags.join("") + (seo.jsonLd ? jsonLd(seo.jsonLd) : "");
}
