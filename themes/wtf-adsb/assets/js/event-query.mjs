// Shared by the static renderer and browser so cards and chart use one query.
export const DAY_MS = 86400000;
// The page sizes a reader can choose. The default is also the static story
// archive's page size (site-model.mjs PER_PAGE), so the archive a page draws
// and the one the script draws at the default size page alike.
export const PAGE_SIZES = [10, 20, 50, 100];
export const DEFAULT_PAGE_SIZE = 20;
// Pages kept from earlier snapshots still offer 25, and their links say so.
const RETAINED_PAGE_SIZES = [25];
// "10, 20, 50, or 100", for messages about the sizes on offer.
export const PAGE_SIZE_CHOICES = `${PAGE_SIZES.slice(0, -1).join(", ")}, or ${PAGE_SIZES.at(-1)}`;
// The story archive's count line, the same on static and scripted pages: the
// whole archive when one page holds it, else the stories this page shows.
export const archiveResult = (offset, shown, total) =>
  shown === total
    ? `${total} published stor${total === 1 ? "y" : "ies"}`
    : `Showing ${offset + 1}–${offset + shown} of ${total} published stories.`;
export const QUERY_VERSION = 2;
export const SUPPORTED_QUERY_VERSIONS = [1, QUERY_VERSION];

export const isoDay = (time) => new Date(time).toISOString().slice(0, 10);
// One date style everywhere, "27 Sep 2026", matching story URLs; locale data
// varies ("Sept"), so the month names are fixed here.
const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
export const dateLabel = (value) => {
  const date = new Date(value);
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
};
export function dayTime(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
    return NaN;
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && isoDay(time) === value ? time : NaN;
}
export const fold = (value) =>
  String(value || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
const compact = (value) => fold(value).replace(/[^a-z0-9]/g, "");
export const aircraftKey = (event) =>
  fold(event.aircraft?.type_code || event.aircraft?.type_description).trim();

// Reception sources: the public event's `source` code names how the receiver
// got the readings. A badge and the journal's filter group the codes into six
// families, listed in the filter's order; each code keeps the story engine's
// long label (publication.py SOURCE_LABELS, which check-event-query.mjs
// compares). Readings from another or an unrecorded source belong to no family
// and get no badge.
export const SOURCES = [
  {
    id: "adsb",
    label: "ADS-B",
    codes: {
      adsb_icao: "ADS-B, ICAO address",
      adsb_icao_nt: "ADS-B, ICAO non-transponder address",
      adsb_other: "ADS-B, other address type",
    },
  },
  {
    id: "adsr",
    label: "ADS-R",
    codes: {
      adsr_icao: "ADS-R relay, ICAO address",
      adsr_other: "ADS-R relay, other address type",
    },
  },
  {
    id: "tisb",
    label: "TIS-B",
    codes: {
      tisb_icao: "TIS-B, ICAO address",
      tisb_other: "TIS-B, other address type",
      tisb_trackfile: "TIS-B track identifier",
    },
  },
  { id: "adsc", label: "ADS-C", codes: { adsc: "ADS-C" } },
  { id: "mlat", label: "MLAT", codes: { mlat: "Multilateration (MLAT)" } },
  { id: "mode_s", label: "Mode S", codes: { mode_s: "Mode S" } },
];
// The badge for a source code: its family's short label, with the code's long
// label as its title; null for a code in no family.
export function sourceBadge(code) {
  const family = SOURCES.find((item) => Object.hasOwn(item.codes, code));
  return family
    ? { id: family.id, label: family.label, title: family.codes[code] }
    : null;
}
export const sourceFamily = (code) => sourceBadge(code)?.id || "";

// Host name suffixes that never reach the public internet (public_events.py
// PRIVATE_SUFFIXES, which check-event-query.mjs compares), without their
// leading dot: the release guard refuses a public file that spells out the
// tailnet's suffix. A reference link to such a host is refused here, as the
// exporter refuses it.
export const PRIVATE_HOST_SUFFIXES = [
  "local",
  "internal",
  "ts.net",
  "localhost",
  "lan",
  "home",
  "intranet",
  "arpa",
  "onion",
];
export function privateHost(hostname) {
  const host = String(hostname).toLowerCase().replace(/\.$/, "");
  return (
    host === "localhost" ||
    PRIVATE_HOST_SUFFIXES.some((suffix) => host.endsWith(`.${suffix}`))
  );
}

export function aircraftChoices(events) {
  const choices = new Map();
  for (const event of events) {
    const aircraft = event.aircraft || {};
    const key = aircraftKey(event);
    if (!key) continue;
    const label = [aircraft.type_code, aircraft.type_description]
      .filter(Boolean)
      .join(" · ");
    // Prefer the description when another event only contains the type code.
    if (!choices.has(key) || label.length > choices.get(key).length)
      choices.set(key, label);
  }
  return [...choices]
    .map(([value, label]) => ({ value, label }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function historyStart(meta) {
  return [
    meta.available_from,
    meta.earliest_event,
    ...(meta.events || []).map((event) => event.observed_start.slice(0, 10)),
  ]
    .filter(Boolean)
    .sort()[0];
}

export function dateBounds(meta) {
  return {
    min: [historyStart(meta), meta.window.start].sort()[0],
    max: meta.window.end,
  };
}

export function defaultQuery(meta) {
  return {
    q: "",
    aircraft: "",
    type: "",
    source: "",
    hideAirportPatterns: false,
    from: meta.window.start,
    to: meta.window.end,
    size: DEFAULT_PAGE_SIZE,
    page: 1,
  };
}

export function validateQuery(input, meta) {
  const query = { ...defaultQuery(meta), ...input };
  if (typeof query.q !== "string" || query.q.length > 200)
    throw new Error("Use no more than 200 characters in the search.");
  if (typeof query.aircraft !== "string" || query.aircraft.length > 160)
    throw new Error("Choose an aircraft type from the list.");
  query.q = query.q.trim();
  query.aircraft = fold(query.aircraft).trim();
  const choices = meta.aircraftChoices || aircraftChoices(meta.events || []);
  if (
    query.aircraft &&
    !choices.some((choice) => choice.value === query.aircraft)
  )
    throw new Error(
      "That aircraft type is not available in this snapshot. Choose one from the list.",
    );
  if (query.type && !meta.types.some((type) => type.id === query.type))
    throw new Error("Choose an event type from the list.");
  if (query.source && !SOURCES.some((family) => family.id === query.source))
    throw new Error("Choose a reception source from the list.");
  if (query.source && meta.version === 1)
    throw new Error(
      "This older snapshot does not support the source filter. Open the latest observations to use it.",
    );
  if (typeof query.hideAirportPatterns !== "boolean")
    throw new Error("Choose whether to hide airport-pattern candidates.");
  if (query.hideAirportPatterns && meta.version === 1)
    throw new Error(
      "This older snapshot does not support the airport-pattern filter. Open the latest observations to use it.",
    );
  if (![query.from, query.to].every((day) => Number.isFinite(dayTime(day))))
    throw new Error("Enter both dates as valid calendar dates.");
  if (query.from > query.to)
    throw new Error("The start date must be on or before the end date.");
  const bounds = dateBounds(meta);
  if (query.from < bounds.min || query.to > bounds.max)
    throw new Error(
      `Choose dates between ${bounds.min} and ${bounds.max}, the range covered by this snapshot.`,
    );
  if (![...PAGE_SIZES, ...RETAINED_PAGE_SIZES].includes(query.size))
    throw new Error(`Choose ${PAGE_SIZE_CHOICES} events per page.`);
  if (!Number.isSafeInteger(query.page) || query.page < 1)
    throw new Error("Choose a valid results page.");
  return query;
}

export function presetRange(name, meta) {
  if (name === "all") return { from: historyStart(meta), to: meta.window.end };
  const count = Number(name);
  if (![7, 30, 90].includes(count)) throw new Error("Unknown date preset");
  const start = isoDay(dayTime(meta.window.end) - (count - 1) * DAY_MS);
  return {
    from: start < dateBounds(meta).min ? dateBounds(meta).min : start,
    to: meta.window.end,
  };
}

export function searchRecord(event, airportPattern = null) {
  return {
    id: event.id,
    observed_start: event.observed_start,
    primary_type: event.primary_type,
    title: event.title,
    summary: event.summary,
    aircraft: event.aircraft,
    source: event.source,
    airport_pattern_candidate: Boolean(airportPattern),
    terms: [
      event.kind,
      ...(event.secondary_labels || []),
      ...(airportPattern
        ? [
            "airport pattern candidate",
            airportPattern.airport_ident,
            airportPattern.airport_name,
          ]
        : []),
      ...event.measurements.flatMap((m) => [m.label, m.value]),
    ]
      .filter((value) => value !== undefined && value !== null)
      .join(" "),
  };
}

export function prepareRecords(events, types) {
  const labels = new Map(types.map((type) => [type.id, type.label]));
  return events
    .map((event) => ({
      ...event,
      searchText: fold(
        [
          event.title,
          event.summary,
          event.primary_type.replaceAll("-", " "),
          labels.get(event.primary_type),
          ...Object.values(event.aircraft || {}),
          event.terms,
        ].join(" "),
      ),
      identifiers: [
        event.aircraft?.registration,
        event.aircraft?.callsign,
        event.aircraft?.icao_address,
        event.aircraft?.type_code,
      ]
        .filter(Boolean)
        .map(compact),
      sourceFamily: sourceFamily(event.source),
    }))
    .sort(
      (a, b) =>
        Date.parse(b.observed_start) - Date.parse(a.observed_start) ||
        a.id.localeCompare(b.id),
    );
}

export function matchingRecords(records, query) {
  const tokens = (query.q.match(/"[^"]+"|\S+/g) || []).map((token) => {
    const text = fold(token.replace(/^"|"$/g, ""));
    return { text, identifier: compact(text) };
  });
  return records.filter((event) => {
    const date = event.observed_start.slice(0, 10);
    return (
      date >= query.from &&
      date <= query.to &&
      (!query.type || event.primary_type === query.type) &&
      (!query.source || event.sourceFamily === query.source) &&
      (!query.aircraft || aircraftKey(event) === query.aircraft) &&
      (!query.hideAirportPatterns || !event.airport_pattern_candidate) &&
      tokens.every(
        ({ text, identifier }) =>
          event.searchText.includes(text) ||
          (identifier &&
            event.identifiers.some((id) => id.includes(identifier))),
      )
    );
  });
}

export function queryPage(records, input, meta) {
  const query = validateQuery(input, meta);
  const matching = matchingRecords(records, query);
  const pages = Math.max(1, Math.ceil(matching.length / query.size));
  if (query.page > pages)
    throw new Error(
      `These filters have ${pages} results page${pages === 1 ? "" : "s"}.`,
    );
  return {
    query,
    matching,
    pages,
    count: matching.length,
    records: matching.slice(
      (query.page - 1) * query.size,
      query.page * query.size,
    ),
  };
}

export function timeline(records, input, meta) {
  const query = validateQuery(input, meta);
  const first = dayTime(query.from);
  const last = dayTime(query.to);
  const dayCount = Math.round((last - first) / DAY_MS) + 1;
  const unit =
    dayCount <= 90
      ? "day"
      : dayCount <= 630
        ? "week"
        : dayCount <= 3653
          ? "month"
          : "year";
  const buckets = [];
  let cursor = first;
  while (cursor <= last) {
    const date = new Date(cursor);
    let next;
    if (unit === "day" || unit === "week")
      next = cursor + (unit === "day" ? 1 : 7) * DAY_MS;
    else {
      if (unit === "month") date.setUTCMonth(date.getUTCMonth() + 1, 1);
      else date.setUTCFullYear(date.getUTCFullYear() + 1, 0, 1);
      next = date.getTime();
    }
    const from = isoDay(cursor);
    const to = isoDay(Math.min(next - DAY_MS, last));
    const unavailable = to < meta.available_from;
    buckets.push({
      from,
      to,
      unavailable,
      incomplete: !unavailable && from < meta.available_from,
      partial: from <= meta.window.end && to >= meta.window.end,
      total: unavailable ? null : 0,
      counts: unavailable
        ? null
        : Object.fromEntries(meta.types.map((t) => [t.id, 0])),
    });
    cursor = next;
  }
  for (const event of matchingRecords(records, query)) {
    const day = event.observed_start.slice(0, 10);
    let low = 0;
    let high = buckets.length - 1;
    while (low < high) {
      const mid = Math.floor((low + high) / 2);
      if (day > buckets[mid].to) low = mid + 1;
      else high = mid;
    }
    const bucket = buckets[low];
    if (bucket?.unavailable) {
      bucket.unavailable = false;
      bucket.incomplete = true;
      bucket.total = 0;
      bucket.counts = Object.fromEntries(
        meta.types.map((type) => [type.id, 0]),
      );
    }
    if (bucket && bucket.counts) {
      bucket.total += 1;
      bucket.counts[event.primary_type] += 1;
    }
  }
  return {
    buckets,
    dayCount,
    unit,
    max: Math.max(1, ...buckets.map((bucket) => bucket.total || 0)),
  };
}

export function queryParams(query) {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.aircraft) params.set("aircraft", query.aircraft);
  if (query.type) params.set("type", query.type);
  if (query.source) params.set("source", query.source);
  if (query.hideAirportPatterns) params.set("hide_airport_patterns", "1");
  params.set("from", query.from);
  params.set("to", query.to);
  params.set("size", String(query.size));
  params.set("page", String(query.page));
  return params;
}

// The address keys a query link may carry: those queryParams writes, and
// "date" from older links. Any other key, such as a click identifier or a
// campaign tag added when a link is shared, is not part of a query.
const QUERY_KEYS = [
  "q",
  "aircraft",
  "type",
  "source",
  "hide_airport_patterns",
  "from",
  "to",
  "size",
  "page",
  "date",
];
export const hasQueryParams = (params) =>
  QUERY_KEYS.some((key) => params.has(key));

// On the story archive's own address, a link that names only a page size or
// a page shows the archive at that size and page; any filter key makes it a
// query. Returns null for a query, else the requested size and page (still
// to be checked against the archive).
export function archiveParams(params, defaultSize) {
  if (
    QUERY_KEYS.some((key) => !["size", "page"].includes(key) && params.has(key))
  )
    return null;
  return {
    size: Number(params.get("size") || defaultSize),
    page: Number(params.get("page") || 1),
  };
}

// The page at a new size that holds the first entry of the current page, so
// a reader who changes the size keeps their place in the list.
export const pageAfterResize = (page, size, newSize) =>
  Math.floor(((page - 1) * size) / newSize) + 1;

// The numbered page links the script draws: every page while the list stays
// short, then the first and last pages around three in a row that include the
// current one, with null for each gap. The static pages list every page, so
// a crawler reaches all of them from the first (site-model.mjs pageLinks).
const FULL_PAGE_LIST = 12;
export function pageNumbers(current, total) {
  if (total <= FULL_PAGE_LIST)
    return Array.from({ length: total }, (_, index) => index + 1);
  const start = Math.min(Math.max(current - 1, 2), total - 3);
  const numbers = [];
  for (const number of [1, start, start + 1, start + 2, total]) {
    const gap = numbers.length ? number - numbers.at(-1) : 1;
    // A gap of one page shows that page; a longer one, an ellipsis.
    if (gap === 2) numbers.push(number - 1);
    else if (gap > 2) numbers.push(null);
    numbers.push(number);
  }
  return numbers;
}

export function queryFromParams(params, initial, meta) {
  if (!hasQueryParams(params)) return { ...initial };
  const defaults = defaultQuery(meta);
  if (
    params.has("hide_airport_patterns") &&
    !["0", "1"].includes(params.get("hide_airport_patterns"))
  )
    throw new Error("Invalid airport-pattern filter in this link.");
  return validateQuery(
    {
      q: params.get("q") || "",
      aircraft: params.get("aircraft") || "",
      type: params.get("type") || "",
      source: params.get("source") || "",
      hideAirportPatterns: params.get("hide_airport_patterns") === "1",
      from: params.get("from") || params.get("date") || defaults.from,
      to: params.get("to") || params.get("date") || defaults.to,
      size: Number(params.get("size") || DEFAULT_PAGE_SIZE),
      page: Number(params.get("page") || 1),
    },
    meta,
  );
}
