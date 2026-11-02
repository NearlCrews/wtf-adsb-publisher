import assert from "node:assert/strict";

// Readers for the head tags build.mjs writes, shared by the production and
// live checks.

// Character references decode in one pass, so "&amp;lt;" stays "&lt;".
const NAMED = {
  amp: "&",
  quot: '"',
  apos: "'",
  lt: "<",
  gt: ">",
  nbsp: "\u00a0",
};
export const decode = (value) =>
  value.replace(
    /&(?:#x([0-9a-f]{1,6})|#(\d{1,7})|([a-z]+));/gi,
    (match, hex, decimal, name) => {
      if (name) return NAMED[name.toLowerCase()] ?? match;
      const code = hex ? parseInt(hex, 16) : Number(decimal);
      return code <= 0x10ffff ? String.fromCodePoint(code) : match;
    },
  );

// Every content value of <meta {key}="{value}" content="...">, decoded.
export const metaValues = (html, key, value) =>
  [
    ...html.matchAll(
      new RegExp(`<meta ${key}="${value}" content="([^"]*)">`, "g"),
    ),
  ].map((match) => decode(match[1]));

// The one content value of a meta tag the build writes exactly once.
export function oneMeta(html, key, value, label = `${key}=${value}`) {
  const values = metaValues(html, key, value);
  assert.equal(values.length, 1, label);
  return values[0];
}

export const robotsOf = (html, label = "robots") =>
  oneMeta(html, "name", "robots", label);

export function canonicalOf(html, label = "canonical") {
  const values = [
    ...html.matchAll(/<link rel="canonical" href="([^"]*)">/g),
  ].map((match) => decode(match[1]));
  assert.equal(values.length, 1, label);
  return values[0];
}

// The page's one JSON-LD graph.
export function jsonLdGraph(html, label = "one JSON-LD block") {
  const scripts = [
    ...html.matchAll(
      /<script type="application\/ld\+json">([\s\S]*?)<\/script>/g,
    ),
  ];
  assert.equal(scripts.length, 1, label);
  const data = JSON.parse(scripts[0][1]);
  assert.equal(data["@context"], "https://schema.org", label);
  return data["@graph"];
}
