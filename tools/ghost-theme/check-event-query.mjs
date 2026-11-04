import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import {
  aircraftChoices,
  archiveParams,
  dateBounds,
  dayTime,
  DEFAULT_PAGE_SIZE,
  defaultQuery,
  hasQueryParams,
  historyStart,
  PAGE_SIZES,
  pageAfterResize,
  pageNumbers,
  prepareRecords,
  presetRange,
  privateHost,
  PRIVATE_HOST_SUFFIXES,
  queryFromParams,
  queryPage,
  queryParams,
  searchRecord,
  plainSource,
  sourceBadge,
  SOURCES,
  timeline,
  validateQuery,
} from "../../themes/wtf-adsb/assets/js/event-query.mjs";
import {
  createJournal,
  exampleSnapshot,
  measurementGroups,
} from "./journal.mjs";
import { stories } from "./demo-content.mjs";
import { root } from "./paths.mjs";

const snapshot = exampleSnapshot(stories);
const journal = createJournal(snapshot);
const defaults = defaultQuery(snapshot);
const records = prepareRecords(journal.records, snapshot.types);
const scoped = [
  {
    key: "duration_seconds",
    label: "Duration",
    value: 510,
    unit: "s",
    scope: "analysis-window",
    start_utc: "2026-09-07T22:55:00Z",
    end_utc: "2026-09-07T23:03:30Z",
  },
  {
    key: "sample_count",
    label: "Saved positions",
    value: 26,
    unit: "samples",
    scope: "analysis-window",
    start_utc: "2026-09-07T22:55:00Z",
    end_utc: "2026-09-07T23:03:30Z",
  },
  {
    key: "gs",
    label: "Ground speed",
    value: 100,
    unit: "kn",
    scope: "saved-report",
    at_utc: "2026-09-07T23:03:15Z",
  },
  {
    key: "squawk",
    label: "Transponder code",
    value: "0042",
    unit: "",
    scope: "saved-report",
    at_utc: "2026-09-07T23:03:30Z",
  },
];
const beforeGrouping = structuredClone(scoped);
const grouped = measurementGroups(scoped, { compact: true });
assert.equal(grouped.length, 3);
assert.equal(grouped[0].rows.length, 2);
assert.equal(grouped[0].rows[0].value, "8 min 30 sec");
assert.match(grouped[0].interval, /23:03:30 UTC/);
assert.equal(grouped[2].rows[0].value, "0042");
assert.deepEqual(scoped, beforeGrouping);
for (const [value, expected] of [
  [119.9999, "2 min"],
  [60.0000000001, "1 min"],
])
  assert.equal(
    measurementGroups([{ ...scoped[0], value }], { compact: true })[0].rows[0]
      .value,
    expected,
  );
assert.equal(
  Number(
    measurementGroups([{ ...scoped[2], value: 0.000001 }])[0].rows[0].value,
  ),
  0.000001,
);
const cardFixture = {
  ...snapshot.events[0],
  summary:
    "A saved speed report reads 1063.9 kn. Nearby reports do not verify that speed. Its timestamp is archival.",
  measurements: scoped,
};
const presented = journal.card(cardFixture, true);
assert.match(presented.details.summary, /Nearby reports do not verify/);
assert.ok(presented.details.notes.includes("Its timestamp is archival."));
assert.equal(
  presented.details.allMeasurementGroups.flatMap((g) => g.rows).length,
  scoped.length,
);

// Compact cards must preserve identity distinctions, every reference, and the
// complete measurement record, even when only selected facts are visible.
const cardReferences = [
  [
    "flight_lookup",
    "FlightAware callsign",
    "https://www.flightaware.com/live/flight/TEST12",
  ],
  [
    "registration_flightaware",
    "FlightAware tail",
    "https://www.flightaware.com/live/flight/N123TEST",
  ],
  [
    "aircraft_photos",
    "Aircraft photos",
    "https://www.jetphotos.com/registration/N123TEST",
  ],
  [
    "aircraft_history",
    "Aircraft history",
    "https://www.flightradar24.com/data/aircraft/n123test",
  ],
  [
    "type_wikipedia_search",
    "Wikipedia type search",
    "https://en.wikipedia.org/w/index.php?search=Cessna172",
  ],
  [
    "type_lookup",
    "ICAO type reference",
    "https://www.icao.int/publications/DOC8643/Pages/Search.aspx",
  ],
].map(([id, label, url]) => ({ id, label, url }));
const compactFixture = {
  ...cardFixture,
  observed_start: "2026-09-07T23:50:00Z",
  observed_end: "2026-09-08T00:20:00Z",
  aircraft: { registration: "N123TEST", callsign: "TEST12", type_code: "C172" },
  synthetic: false,
  references: cardReferences,
  limitations: ["The source does not establish the cause."],
};
const compactBefore = structuredClone(compactFixture);
for (const lead of [true, false]) {
  const { details } = journal.card(compactFixture, lead);
  assert.equal(details.registration, "N123TEST");
  assert.equal(details.distinctCallsign, "TEST12");
  assert.match(details.aircraftNote, /historical assignments unverified/);
  assert.match(details.observationInterval, /7 Sept? 2026.*8 Sept? 2026/);
  assert.deepEqual(
    details.primaryReferences.map((ref) => ref.id),
    ["registration_flightaware", "aircraft_photos", "type_wikipedia_search"],
  );
  assert.match(details.primaryReferences[0].label, /tail/);
  assert.match(
    details.moreReferences.find((ref) => ref.id === "flight_lookup").label,
    /callsign/,
  );
  assert.deepEqual(
    [...details.primaryReferences, ...details.moreReferences]
      .map((ref) => ref.url)
      .sort(),
    cardReferences.map((ref) => ref.url).sort(),
  );
  assert.ok(details.notes.includes(compactFixture.limitations[0]));
  const rows = details.allMeasurementGroups.flatMap((group) => group.rows);
  for (const item of scoped) {
    const row = rows.find((candidate) => candidate.key === item.key);
    assert.equal(row.value, String(item.value));
    for (const key of ["unit", "at_utc", "start_utc", "end_utc"])
      assert.equal(row[key], item[key]);
    assert.ok(row.scope && row.interval);
  }
}
assert.deepEqual(compactFixture, compactBefore);
const noRegistration = journal.card(
  { ...compactFixture, aircraft: { callsign: "TEST12" } },
  false,
);
assert.equal(noRegistration.details.registration, "Not recorded");
assert.equal(noRegistration.details.distinctCallsign, "TEST12");
assert.equal(noRegistration.details.typeName, "Aircraft type not recorded");
assert.equal(
  journal.card(
    {
      ...compactFixture,
      aircraft: { registration: "N123TEST", callsign: "N123TEST" },
    },
    false,
  ).details.distinctCallsign,
  "",
);
const patternFixture = {
  ...compactFixture,
  kind: "circling",
  primary_type: "flight-patterns",
  measurements: [
    scoped[0],
    { ...scoped[0], key: "closed_loops", value: 5, unit: "loops" },
  ],
};
const patternCard = journal.card(patternFixture, false);
assert.match(patternCard.details.summary, /5 closed loops.*8 min 30 sec/);
assert.match(
  patternCard.details.summary,
  /does not establish the flight's purpose/,
);
assert.ok(patternCard.details.notes.includes(patternFixture.summary));
const differentWindow = structuredClone(patternFixture);
differentWindow.measurements[1].start_utc = "2026-09-07T22:54:00Z";
assert.equal(
  journal.card(differentWindow, false).details.summary,
  presented.details.summary,
);
const speedOutlier = {
  ...compactFixture,
  kind: "outlier_gs",
  primary_type: "data-quality",
};
assert.deepEqual(
  journal
    .card(speedOutlier, false)
    .details.measurementGroups.flatMap((group) => group.rows)
    .map((row) => row.key),
  ["gs"],
);
const missingSpeed = {
  ...speedOutlier,
  measurements: speedOutlier.measurements.filter((item) => item.key !== "gs"),
};
assert.deepEqual(
  journal.card(missingSpeed, false).details.measurementGroups,
  [],
);
assert.equal(
  journal
    .card(missingSpeed, false)
    .details.allMeasurementGroups.flatMap((group) => group.rows).length,
  missingSpeed.measurements.length,
);
assert.ok(
  journal
    .card({ ...compactFixture, primary_type: "transponder-reports" }, false)
    .details.measurementGroups.flatMap((group) => group.rows)
    .some((row) => row.key === "squawk" && row.value === "0042"),
);
// The default page size is the static archive's (site-model.mjs PER_PAGE).
assert.deepEqual(PAGE_SIZES, [10, 20, 50, 100]);
assert.equal(DEFAULT_PAGE_SIZE, 20);
assert.equal(defaults.size, 20);
// Links from pages kept from earlier snapshots may still name 25.
assert.equal(validateQuery({ ...defaults, size: 25 }, snapshot).size, 25);
assert.equal(journal.context().feed.count, 6);
assert.equal(journal.context().feed.rows.length, 5);
assert.equal(timeline(records, defaults, snapshot).buckets.length, 30);
assert.equal(
  timeline(records, defaults, snapshot).buckets.filter((b) => b.total === null)
    .length,
  7,
);
assert.equal(
  queryPage(records, { ...defaults, q: '"parallel passes"' }, snapshot).count,
  1,
);
assert.equal(
  queryPage(records, { ...defaults, q: "helicopter circuits" }, snapshot).count,
  1,
);
assert.equal(
  queryPage(records, { ...defaults, q: "helicopter nonexistent" }, snapshot)
    .count,
  0,
);
assert.equal(queryPage(records, { ...defaults, q: "7700" }, snapshot).count, 1);
assert.equal(
  queryPage(records, { ...defaults, q: "transponder reports" }, snapshot).count,
  1,
);
assert.equal(
  queryPage(records, { ...defaults, type: "flight-patterns" }, snapshot).count,
  2,
);
const oneDay = { ...defaults, from: "2026-10-04", to: "2026-10-04" };
assert.equal(queryPage(records, oneDay, snapshot).count, 1);
assert.equal(timeline(records, oneDay, snapshot).buckets.length, 1);
assert.equal(timeline(records, oneDay, snapshot).buckets[0].partial, true);
assert.equal(
  timeline(records, { ...defaults, to: "2026-10-03" }, snapshot).buckets.some(
    (b) => b.partial,
  ),
  false,
);
for (const input of [
  { from: "2026-02-30" },
  { from: "2026-10-05" },
  { from: "2026-10-04", to: "2026-10-03" },
  { size: 11 },
  { size: NaN },
  { page: 0 },
  { page: 1.1 },
  { type: "not-a-type" },
  { aircraft: "missing" },
  { q: "x".repeat(201) },
  { hideAirportPatterns: "true" },
  { hideAirportPatterns: 1 },
  { source: "adsb_icao" },
  { source: "radar" },
])
  assert.throws(() => validateQuery({ ...defaults, ...input }, snapshot));
assert(Number.isNaN(dayTime("2025-02-29")));
assert(Number.isFinite(dayTime("2024-02-29")));

// Retained events can predate the boundary for a complete archive.
const historical = structuredClone(snapshot);
historical.events.push({
  ...historical.events[0],
  id: "00000000-0000-4000-8000-999999999999",
  article_path: "/old-observation/",
  observed_start: "2025-02-28T23:59:59Z",
  observed_end: "2025-03-01T00:02:00Z",
  aircraft: {
    registration: "C-FTST",
    callsign: "TEST42",
    type_code: "R44",
    type_description: "Robinson R44",
    icao_address: "ABCDEF",
  },
});
const historicalJournal = createJournal(historical);
const historicalRecords = prepareRecords(
  historicalJournal.records,
  historical.types,
);
assert.equal(historicalJournal.context().feed.count, 6);
assert.equal(historicalJournal.context("archive").feed.count, 7);
assert.equal(historyStart(historical), "2025-02-28");
assert.equal(dateBounds(historical).min, "2025-02-28");
const all = { ...defaults, ...presetRange("all", historical) };
assert.equal(queryPage(historicalRecords, all, historical).count, 7);
for (const q of ["cftst", "C-FTST", "test42", "ABCdef", "r44", "Robinson"])
  assert.equal(
    queryPage(historicalRecords, { ...all, q }, historical).count,
    1,
    q,
  );
assert.equal(
  queryPage(historicalRecords, { ...all, aircraft: "R44" }, historical).count,
  1,
);
assert.equal(
  queryPage(
    historicalRecords,
    { ...all, q: "cftst", type: "performance" },
    historical,
  ).count,
  0,
);
const oldDay = timeline(
  historicalRecords,
  { ...all, from: "2025-02-28", to: "2025-02-28" },
  historical,
).buckets[0];
assert.equal(oldDay.total, 1);
assert.equal(oldDay.incomplete, true);
assert.equal(oldDay.unavailable, false);
const gap = timeline(
  historicalRecords,
  { ...all, from: "2025-03-01", to: "2025-03-01" },
  historical,
).buckets[0];
assert.equal(gap.total, null);
assert.equal(gap.unavailable, true);
const chart = timeline(historicalRecords, all, historical);
assert.equal(chart.unit, "week");
assert.equal(chart.buckets[0].from, "2025-02-28");
assert.equal(chart.buckets.at(-1).to, snapshot.window.end);
assert.equal(
  chart.buckets.reduce((sum, b) => sum + (b.total || 0), 0),
  7,
);

// Page size changes affect the feed, never total or chart counts.
const many = Array.from({ length: 117 }, (_, i) => ({
  ...searchRecord(historical.events[0]),
  id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
  title: `Example ${i}`,
  observed_start: "2026-10-03T12:00:00Z",
}));
const prepared = prepareRecords(many, snapshot.types);
for (const size of PAGE_SIZES) {
  const first = queryPage(prepared, { ...defaults, size }, snapshot);
  assert.equal(first.records.length, size);
  assert.equal(first.count, 117);
  const ids = [];
  for (let page = 1; page <= first.pages; page++)
    ids.push(
      ...queryPage(prepared, { ...defaults, size, page }, snapshot).records.map(
        (e) => e.id,
      ),
    );
  assert.equal(new Set(ids).size, 117);
  assert.equal(
    timeline(prepared, { ...defaults, size }, snapshot).buckets.reduce(
      (n, b) => n + (b.total || 0),
      0,
    ),
    117,
  );
}
assert.throws(() => queryPage(prepared, { ...defaults, page: 13 }, snapshot));
// A new page size keeps the first entry shown on the page it opens.
for (const [page, size, newSize, expected] of [
  [1, 20, 50, 1],
  [2, 20, 10, 3],
  [3, 10, 20, 2],
  [6, 20, 50, 3],
  [6, 20, 100, 2],
  [12, 10, 100, 2],
]) {
  assert.equal(pageAfterResize(page, size, newSize), expected);
  const first = queryPage(prepared, { ...defaults, size, page }, snapshot)
    .records[0].id;
  assert.ok(
    queryPage(
      prepared,
      { ...defaults, size: newSize, page: expected },
      snapshot,
    ).records.some((record) => record.id === first),
  );
}
// Short lists number every page; longer ones keep the first, the last, and
// three in a row around the current page, with null for each gap.
assert.deepEqual(pageNumbers(1, 1), [1]);
assert.deepEqual(pageNumbers(5, 12), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
assert.deepEqual(pageNumbers(1, 21), [1, 2, 3, 4, null, 21]);
assert.deepEqual(pageNumbers(4, 21), [1, 2, 3, 4, 5, null, 21]);
assert.deepEqual(pageNumbers(10, 21), [1, null, 9, 10, 11, null, 21]);
assert.deepEqual(pageNumbers(18, 21), [1, null, 17, 18, 19, 20, 21]);
assert.deepEqual(pageNumbers(21, 21), [1, null, 18, 19, 20, 21]);
for (let total = 13; total <= 30; total++)
  for (let current = 1; current <= total; current++) {
    const numbers = pageNumbers(current, total);
    assert.ok(numbers.includes(current) && numbers.length <= 7);
    assert.deepEqual(
      [numbers[0], numbers.at(-1)],
      [1, total],
      `${current} of ${total}`,
    );
  }
// On the archive's address only a size and a page keep the archive; any
// filter key makes a query, and a shared link's own keys count for neither.
for (const [search, expected] of [
  ["", { size: 20, page: 1 }],
  ["fbclid=abc", { size: 20, page: 1 }],
  ["size=50", { size: 50, page: 1 }],
  ["page=3&utm_source=x", { size: 20, page: 3 }],
  ["size=10&page=x", { size: 10, page: NaN }],
  ["size=10&q=r44", null],
  ["from=2026-09-01", null],
  ["date=2026-10-01", null],
])
  assert.deepEqual(
    archiveParams(new URLSearchParams(search), DEFAULT_PAGE_SIZE),
    expected,
    search,
  );
const uri = {
  ...all,
  q: 'C-FTST "parallel passes" & test',
  aircraft: "r44",
  type: "flight-patterns",
  size: 50,
  page: 2,
};
assert.deepEqual(queryFromParams(queryParams(uri), defaults, historical), uri);
// Only the journal's own keys make a query: a shared link's click identifier
// or campaign tag leaves the page's own view (the story archive) in place.
for (const foreign of ["", "fbclid=abc", "utm_source=x&utm_medium=social"])
  assert.equal(hasQueryParams(new URLSearchParams(foreign)), false, foreign);
for (const own of [
  "q=",
  "page=2",
  "date=2026-10-01",
  "utm_source=x&source=mlat",
])
  assert.equal(hasQueryParams(new URLSearchParams(own)), true, own);
// Every key a query link writes is one the reader counts as the journal's.
for (const key of queryParams({
  ...uri,
  source: "mlat",
  hideAirportPatterns: true,
}).keys())
  assert.equal(hasQueryParams(new URLSearchParams([[key, ""]])), true, key);
assert.deepEqual(
  aircraftChoices([
    { aircraft: { type_code: "R44" } },
    { aircraft: { type_code: "R44", type_description: "Robinson R44" } },
  ]),
  [{ value: "r44", label: "R44 · Robinson R44" }],
);

const longMeta = {
  ...snapshot,
  available_from: "2020-01-01",
  earliest_event: "2020-01-01",
};
const longChart = timeline(
  records,
  { ...defaults, ...presetRange("all", longMeta) },
  longMeta,
);
assert.equal(longChart.unit, "month");
assert.equal(longChart.buckets.at(-1).to, "2026-10-04");
assert.equal(
  longChart.buckets.reduce((sum, b) => sum + (b.total || 0), 0),
  6,
);

// A reversible presentation flag changes the feed and chart together, without
// deleting evidence or treating an older, unannotated record as a candidate.
const tagged = prepared.map((record, index) => ({
  ...record,
  airport_pattern_candidate: index < 17,
}));
const hidden = { ...defaults, hideAirportPatterns: true, size: 25 };
assert.equal(queryPage(tagged, defaults, snapshot).count, 117);
assert.equal(queryPage(tagged, hidden, snapshot).count, 100);
assert.equal(queryPage(tagged, hidden, snapshot).pages, 4);
assert.equal(
  timeline(tagged, hidden, snapshot).buckets.reduce(
    (sum, bucket) => sum + (bucket.total || 0),
    0,
  ),
  100,
);
assert.deepEqual(
  queryFromParams(queryParams(hidden), defaults, snapshot),
  hidden,
);
assert.equal(queryPage(records, hidden, snapshot).count, records.length);
assert.equal(
  validateQuery(defaults, { ...snapshot, version: 1 }).hideAirportPatterns,
  false,
);
assert.throws(
  () => validateQuery(hidden, { ...snapshot, version: 1 }),
  /older snapshot/,
);
for (const value of ["true", "false", "", "2"])
  assert.throws(() =>
    queryFromParams(
      new URLSearchParams({ hide_airport_patterns: value }),
      defaults,
      snapshot,
    ),
  );
// The story engine's tables that the browser module copies: the long source
// labels and the private host suffixes.
const engine = spawnSync(
  "python3",
  [
    "-B",
    "-c",
    "import json; from wtf_adsb_publisher import publication, public_events; print(json.dumps({'labels': publication.SOURCE_LABELS, 'private': public_events.PRIVATE_SUFFIXES}))",
  ],
  {
    cwd: root,
    env: { ...process.env, PYTHONPATH: path.join(root, "src") },
    encoding: "utf8",
    timeout: 60000,
  },
);
assert.equal(engine.status, 0, engine.stderr);
const tables = JSON.parse(engine.stdout);
// Reception sources: every code the engine labels maps onto one of six badge
// families with the engine's long label, except another or an unrecorded
// source, which gets no badge; the search index carries the code, and the
// filter selects by family.
assert.deepEqual(
  SOURCES.map((family) => family.label),
  ["ADS-B", "ADS-R", "TIS-B", "ADS-C", "MLAT", "Mode S"],
);
for (const [code, label] of Object.entries(tables.labels))
  assert.equal(
    sourceBadge(code)?.title ?? null,
    ["other", "mode_ac", "unknown"].includes(code) ? null : label,
    code,
  );
assert.deepEqual(
  SOURCES.flatMap((family) => Object.keys(family.codes)).filter(
    (code) => !Object.hasOwn(tables.labels, code),
  ),
  [],
);
// A badge shows the short label and gives a screen reader the long label in
// its place, so every long label has to contain the short one.
for (const family of SOURCES)
  for (const title of Object.values(family.codes))
    assert.ok(title.includes(family.label), `${title} names ${family.label}`);
assert.deepEqual(sourceBadge("adsb_icao_nt"), {
  id: "adsb",
  label: "ADS-B",
  title: "ADS-B, ICAO non-transponder address",
});
for (const code of [undefined, "", "constructor", "__proto__"])
  assert.equal(sourceBadge(code), null);
// List cards leave the badge off plain ADS-B only, the usual source.
assert.deepEqual(
  SOURCES.flatMap((family) => Object.keys(family.codes)).filter(plainSource),
  ["adsb_icao"],
);
// The journal refuses a reference link to every host the exporter refuses.
assert.deepEqual(
  PRIVATE_HOST_SUFFIXES.map((suffix) => `.${suffix}`),
  tables.private,
);
for (const host of [
  "nas.lan",
  "printer.home.arpa",
  "office.local.",
  "LOCALHOST",
])
  assert.equal(privateHost(host), true, host);
for (const host of ["example.com", "local.example.com", "lan.example"])
  assert.equal(privateHost(host), false, host);
assert.equal(
  searchRecord(snapshot.events[0]).source,
  snapshot.events[0].source,
);
const sourced = prepareRecords(
  journal.records.map((record, index) => ({
    ...record,
    source: ["mlat", "adsb_icao", "adsb_other", "tisb_icao", "other"][
      index % 5
    ],
  })),
  snapshot.types,
);
const bySource = (source) =>
  queryPage(sourced, { ...defaults, source }, snapshot).records.map(
    (record) => record.source,
  );
assert.deepEqual(bySource("mlat"), ["mlat", "mlat"]);
assert.deepEqual(bySource("adsb").sort(), ["adsb_icao", "adsb_other"]);
assert.deepEqual(bySource("adsr"), []);
assert.equal(bySource("").length, sourced.length);
// A record without the field, from an index built before the badge, never
// matches a source.
const unsourced = prepareRecords(
  journal.records.map((record) => ({ ...record, source: undefined })),
  snapshot.types,
);
assert.deepEqual(
  queryPage(unsourced, { ...defaults, source: "adsb" }, snapshot).records,
  [],
);
assert.equal(queryPage(unsourced, defaults, snapshot).count, records.length);
const mlat = { ...defaults, source: "mlat" };
assert.deepEqual(queryFromParams(queryParams(mlat), defaults, snapshot), mlat);
assert.equal(queryParams(mlat).get("source"), "mlat");
assert.equal(queryParams(defaults).has("source"), false);
assert.deepEqual(
  queryFromParams(new URLSearchParams("source=mlat"), defaults, snapshot),
  mlat,
);
assert.throws(
  () => validateQuery(mlat, { ...snapshot, version: 1 }),
  /older snapshot/,
);
assert.deepEqual(journal.card(snapshot.events[0], true).sourceBadge, {
  id: "mlat",
  label: "MLAT",
  title: "Multilateration (MLAT)",
});
assert.equal(journal.card(snapshot.events[0], true).plainAdsb, false);
assert.deepEqual(
  journal.context().sources,
  SOURCES.map(({ id, label }) => ({ id, label })),
);

const annotation = {
  airport_ident: "KPTK",
  airport_name: "Oakland County International Airport",
  runway: "09L/27R",
  aligned_passes: 3,
  closed_loops: 4,
};
const originalSnapshot = JSON.stringify(snapshot);
const annotatedJournal = createJournal(snapshot, {
  media: { [snapshot.events[0].id]: { airportPattern: annotation } },
});
assert.equal(
  annotatedJournal.records.filter((record) => record.airport_pattern_candidate)
    .length,
  1,
);
assert.match(annotatedJournal.records[0].terms, /airport pattern candidate/);
assert.ok(
  annotatedJournal
    .card(snapshot.events[0], true)
    .details.notes.some((note) => note.includes("does not confirm training")),
);
assert.equal(JSON.stringify(snapshot), originalSnapshot);
assert.equal(createJournal(snapshot).base, journal.base);
assert.match(journal.base, /\/query-v2\/[a-f0-9]{64}\/$/);
assert.notEqual(annotatedJournal.base, journal.base);
const mediaFixture = {
  [snapshot.events[0].id]: {
    src: "/event-media/example.svg",
    caption: "Recorded aircraft positions.",
    replaySrc: "/event-media/example.json",
    airportPattern: annotation,
  },
};
const mediaJournal = createJournal(snapshot, { media: mediaFixture });
const reorderedMedia = {
  [snapshot.events[0].id]: Object.fromEntries(
    Object.entries(mediaFixture[snapshot.events[0].id]).reverse(),
  ),
};
assert.equal(
  createJournal(snapshot, { media: reorderedMedia }).base,
  mediaJournal.base,
);
for (const [key, value] of [
  ["src", "/event-media/replacement.svg"],
  ["caption", "Updated map description."],
  ["replaySrc", "/event-media/replacement.json"],
  ["airportPattern", { ...annotation, aligned_passes: 2 }],
]) {
  const changed = structuredClone(mediaFixture);
  changed[snapshot.events[0].id][key] = value;
  assert.notEqual(
    createJournal(snapshot, { media: changed }).base,
    mediaJournal.base,
  );
}
assert.notEqual(
  createJournal(snapshot, {
    references: {
      [snapshot.events[0].id]: [
        { label: "Resource", url: "https://example.com/aircraft" },
      ],
    },
  }).base,
  journal.base,
);
console.log(
  "Event query checks passed: combined identity/text/type filters, inclusive UTC ranges, older retained observations, unknown coverage, date grouping, page sizes, resizing in place, page-number lists, archive links, and URL round trips.",
);
