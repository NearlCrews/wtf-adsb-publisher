import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { airportPatternNote } from "./airport-patterns.mjs";
import { pageLinks } from "./site-model.mjs";
import {
  aircraftChoices,
  dateBounds,
  dateLabel,
  defaultQuery,
  DAY_MS,
  historyStart,
  isoDay,
  DEFAULT_PAGE_SIZE,
  PAGE_SIZES,
  prepareRecords,
  queryPage,
  QUERY_VERSION,
  searchRecord,
  sourceBadge,
  SOURCES,
  timeline,
} from "../../themes/wtf-adsb/assets/js/event-query.mjs";

const rendererVersion = JSON.parse(
  readFileSync(
    new URL("../../themes/wtf-adsb/package.json", import.meta.url),
    "utf8",
  ),
).version;
const stableJson = (value) =>
  JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(
          Object.keys(item)
            .sort()
            .map((key) => [key, item[key]]),
        )
      : item,
  );

export const types = [
  { id: "flight-patterns", label: "Flight patterns", icon: "↻" },
  { id: "unusual-aircraft", label: "Unusual aircraft", icon: "◇" },
  { id: "performance", label: "Performance", icon: "↗" },
  { id: "data-quality", label: "Data quality", icon: "≠" },
  { id: "transponder-reports", label: "Transponder reports", icon: "⌁" },
];
const stamp = (value) => {
  const time = Date.parse(value);
  if (!Number.isFinite(time) || !/Z$/.test(value))
    throw new Error("Expected a UTC timestamp");
  return time;
};
const localPath = (value) =>
  typeof value === "string" &&
  /^\/[a-zA-Z0-9][a-zA-Z0-9._/-]*$/.test(value) &&
  !value.includes("//") &&
  !value.split("/").some((part) => part === "." || part === "..");
export { dateLabel };
const timeFormatter = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
  hourCycle: "h23",
});
const numberFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 3,
});
const displayNumber = (value) =>
  (value !== 0 && Math.abs(value) < 0.001) || Math.abs(value) >= 1e12
    ? value.toExponential(2).replace(/\.?0+(?=e)/, "")
    : numberFormatter.format(value);
const sentences = new Intl.Segmenter("en", { granularity: "sentence" });
const preciseTime = (value) =>
  new Date(value)
    .toISOString()
    .slice(11)
    .replace(/\.000Z$/, " UTC")
    .replace(/Z$/, " UTC");
const preciseStamp = (value) => `${dateLabel(value)} · ${preciseTime(value)}`;
export const timeLabel = (value) =>
  timeFormatter.format(new Date(value)) + " UTC";

// Fixed dates make the examples reproducible. They are visibly dated examples,
// never a rolling claim about today's aircraft activity.
export function exampleSnapshot(stories) {
  const events = stories
    .filter((s) => s.is_event)
    .map((s, index) => ({
      id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
      observed_start: s.observation_start,
      observed_end: s.observation_end,
      primary_type: s.event_type,
      title: s.title,
      summary: s.excerpt,
      article_path: s.url,
      kind: s.kind,
      ...(s.context ? { context: s.context } : {}),
      ...(s.story ? { story: s.story } : {}),
      aircraft: { type_description: s.aircraft.type },
      source: s.source || "adsb_icao",
      measurements: s.measurements,
      media: { src: s.feature_image, alt: s.feature_image_alt },
      published_at: s.published_at,
      revised_at: s.updated_at,
      evidence_version: "synthetic-v2",
      synthetic: true,
      references: [],
    }));
  events.sort(
    (a, b) =>
      stamp(b.observed_start) - stamp(a.observed_start) ||
      a.id.localeCompare(b.id),
  );
  const start = "2026-09-05";
  const available = "2026-09-12";
  const days = Array.from({ length: 30 }, (_, i) => {
    const date = isoDay(stamp(`${start}T00:00:00Z`) + i * DAY_MS);
    const counts = Object.fromEntries(
      types.map((t) => [
        t.id,
        events.filter(
          (e) =>
            e.observed_start.slice(0, 10) === date && e.primary_type === t.id,
        ).length,
      ]),
    );
    return {
      date,
      status: date < available ? "unavailable" : "available",
      partial: i === 29,
      total:
        date < available
          ? null
          : Object.values(counts).reduce((a, b) => a + b, 0),
      counts: date < available ? null : counts,
    };
  });
  const snapshot = {
    schema_version: 1,
    mode: "review",
    publication_status: "unpublished",
    as_of_utc: "2026-10-04T16:00:00Z",
    available_from: available,
    window: { start, end: "2026-10-04", timezone: "UTC" },
    types,
    events,
    days,
  };
  snapshot.snapshot_id = createHash("sha256")
    .update(JSON.stringify(snapshot))
    .digest("hex");
  return snapshot;
}

export function validateSnapshot(snapshot) {
  if (
    snapshot.schema_version !== 1 ||
    !["review", "published"].includes(snapshot.mode) ||
    !/^[a-f0-9]{16,64}$/.test(snapshot.snapshot_id)
  )
    throw new Error("Unsupported event snapshot");
  stamp(snapshot.as_of_utc);
  if (
    !Array.isArray(snapshot.events) ||
    !Array.isArray(snapshot.days) ||
    snapshot.days.length !== 30 ||
    snapshot.window?.timezone !== "UTC"
  )
    throw new Error("Expected a complete 30-day UTC snapshot");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(snapshot.available_from))
    throw new Error("Missing publication history boundary");
  const ids = new Set();
  const paths = new Set();
  for (const e of snapshot.events) {
    if (
      !/^[a-f0-9-]{36}$/.test(e.id) ||
      ids.has(e.id) ||
      paths.has(e.article_path)
    )
      throw new Error("Duplicate or invalid public event");
    ids.add(e.id);
    paths.add(e.article_path);
    if (
      !types.some((t) => t.id === e.primary_type) ||
      !localPath(e.article_path) ||
      !/^\/(?:stories\/)?[a-z0-9-]+\/$/.test(e.article_path)
    )
      throw new Error("Invalid event type or article path");
    if (
      stamp(e.observed_end) < stamp(e.observed_start) ||
      stamp(e.observed_end) > stamp(snapshot.as_of_utc)
    )
      throw new Error("Invalid observation interval");
    if (
      typeof e.title !== "string" ||
      typeof e.summary !== "string" ||
      !Array.isArray(e.measurements) ||
      !e.aircraft
    )
      throw new Error("Missing event content");
    if (e.media && (!localPath(e.media.src) || typeof e.media.alt !== "string"))
      throw new Error("Unsafe event media");
    if (snapshot.mode === "published" && e.synthetic !== false)
      throw new Error("Synthetic events cannot be published");
  }
  const start = stamp(`${snapshot.window.start}T00:00:00Z`);
  for (const [i, day] of snapshot.days.entries()) {
    if (day.date !== isoDay(start + i * DAY_MS) || day.partial !== (i === 29))
      throw new Error("Invalid daily window");
    if (day.date < snapshot.available_from !== (day.status === "unavailable"))
      throw new Error("Incorrect archive availability");
    if (day.status === "unavailable") {
      if (day.total !== null || day.counts !== null)
        throw new Error("Unavailable dates are not zero");
    } else if (day.status === "available") {
      let total = 0;
      for (const type of types) {
        const count = snapshot.events.filter(
          (e) =>
            e.observed_start.slice(0, 10) === day.date &&
            e.primary_type === type.id,
        ).length;
        if (count !== day.counts?.[type.id])
          throw new Error("Chart and archive counts disagree");
        total += count;
      }
      if (total !== day.total) throw new Error("Incorrect daily total");
    } else throw new Error("Unknown archive availability");
  }
  if (
    snapshot.days.at(-1).date !== snapshot.window.end ||
    snapshot.window.end !== snapshot.as_of_utc.slice(0, 10)
  )
    throw new Error("Snapshot window is not current at export time");
  return snapshot;
}

export function measurementView(measurement) {
  const scope =
    {
      "saved-report": "Saved report",
      "archived-label": "Last archived label; carried between saved positions",
      "peak-report": "Peak report",
      "detection-rule": "Detection rule; a site setting, not a measurement",
      "reference-data":
        "Reference figure: a certified limit, a modelled wind, or a registry record",
      "analysis-window": "Analyzed interval",
      "discontinuity-pair-not-physical-travel":
        "Calculated between inconsistent positions; not physical travel",
    }[measurement.scope] || measurement.scope;
  const interval = measurement.at_utc
    ? preciseStamp(measurement.at_utc)
    : measurement.start_utc && measurement.end_utc
      ? `${preciseStamp(measurement.start_utc)} to ${measurement.start_utc.slice(0, 10) === measurement.end_utc.slice(0, 10) ? preciseTime(measurement.end_utc) : preciseStamp(measurement.end_utc)}`
      : "";
  return {
    ...measurement,
    value:
      typeof measurement.value === "number"
        ? displayNumber(measurement.value)
        : measurement.value,
    scope,
    interval,
  };
}

export function measurementGroups(measurements, { compact = false } = {}) {
  const groups = new Map();
  for (const measurement of measurements) {
    const key = JSON.stringify([
      measurement.scope,
      measurement.at_utc,
      measurement.start_utc,
      measurement.end_utc,
    ]);
    const row = measurementView(measurement);
    if (!groups.has(key))
      groups.set(key, { scope: row.scope, interval: row.interval, rows: [] });
    if (
      compact &&
      measurement.unit === "s" &&
      typeof measurement.value === "number" &&
      measurement.value >= 60 &&
      measurement.value <= Number.MAX_SAFE_INTEGER / 1000
    ) {
      const milliseconds = Math.round(measurement.value * 1000);
      const minutes = Math.floor(milliseconds / 60000);
      const seconds = (milliseconds % 60000) / 1000;
      row.value = `${numberFormatter.format(minutes)} min${seconds ? ` ${numberFormatter.format(seconds)} sec` : ""}`;
      row.unit = "";
    }
    groups.get(key).rows.push(row);
  }
  return [...groups.values()];
}

const participantRoles = {
  lead: "Lead aircraft",
  member: "Other aircraft",
  wingman: "Wingman",
  threat: "Aircraft named in the advisory",
};

// Other aircraft in a multi-aircraft episode, with their own allowlisted values.
function participantRows(participants = []) {
  return participants.map((item) => ({
    label: participantRoles[item.role] || "Other aircraft",
    value: [
      [item.registration, item.callsign, item.type_code]
        .filter(Boolean)
        .join(" · "),
      item.type_description,
      item.icao_address && `ICAO address ${item.icao_address}`,
      ...(item.measurements || []).map((m) => {
        const view = measurementView(m);
        return `${m.label}: ${[view.value, m.unit].filter(Boolean).join(" ")}`;
      }),
    ]
      .filter(Boolean)
      .join("; "),
  }));
}

function resourceView(reference, all) {
  const names = {
    aircraft_history: "Flightradar24",
    aircraft_photos: "Photos",
    flight_lookup: all.some((ref) => ref.id === "registration_flightaware")
      ? "FlightAware · callsign"
      : "FlightAware",
    registration_flightaware: all.some((ref) => ref.id === "flight_lookup")
      ? "FlightAware · tail"
      : "FlightAware",
    address_lookup: "ADS-B Exchange",
    type_lookup: "ICAO aircraft type lookup",
    type_reference: "Aircraft reference",
    type_wikipedia_search: "Type search",
  };
  const label = names[reference.id] || reference.label;
  return {
    ...reference,
    label,
    ariaLabel: reference.label.startsWith(label)
      ? reference.label
      : `${label}: ${reference.label}`,
  };
}

function referenceGroups(references) {
  const links = references.map((reference) =>
    resourceView(reference, references),
  );
  const primary = [
    ["registration_flightaware", "flight_lookup", "aircraft_history"],
    ["aircraft_photos"],
    ["type_reference", "type_wikipedia_search", "type_lookup"],
  ]
    .map((ids) =>
      ids.map((id) => links.find((link) => link.id === id)).find(Boolean),
    )
    .filter(Boolean);
  return { primary, more: links.filter((link) => !primary.includes(link)) };
}

function patternFinding(event) {
  if (event.primary_type !== "flight-patterns") return null;
  const key = { circling: "closed_loops", repeated_passes: "complete_passes" }[
    event.kind
  ];
  if (!key && event.kind !== "loitering") return null;
  const count = event.measurements.find(
    (item) =>
      item.key === key &&
      item.scope === "analysis-window" &&
      Number.isInteger(item.value) &&
      item.value > 0,
  );
  const duration = event.measurements.find(
    (item) =>
      item.key === "duration_seconds" &&
      item.scope === "analysis-window" &&
      item.unit === "s" &&
      Number.isFinite(item.value) &&
      item.value > 0 &&
      item.start_utc &&
      item.end_utc &&
      (!key ||
        (item.start_utc === count?.start_utc &&
          item.end_utc === count?.end_utc)),
  );
  if (!duration || (key && !count)) return null;
  const formatted = measurementGroups([duration], { compact: true })[0].rows[0];
  const length = [formatted.value, formatted.unit].filter(Boolean).join(" ");
  if (!key)
    return `The saved positions form a loitering-shaped track during the ${length} analyzed interval. The track does not confirm an operational hold or flight purpose.`;
  const shape =
    key === "closed_loops"
      ? `closed loop${count.value === 1 ? "" : "s"}`
      : `alternating pass${count.value === 1 ? "" : "es"}`;
  return `The recorded track contains ${displayNumber(count.value)} ${shape} within the ${length} analyzed interval. Its shape does not establish the flight's purpose.`;
}

function mediaCaption(media, event) {
  if (!media) return "";
  if (media.kind === "track")
    return "Recorded aircraft positions. Gaps remain visible; distances cover the saved segments.";
  if (media.kind === "measurement-chart")
    return "Saved speed reports. The spike is unconfirmed.";
  // Photo captions retain variant and attribution qualifiers in full.
  return media.caption || (event.synthetic ? "Illustrative example" : "");
}

// The measurements a card leads with, in this order: by event type first,
// then by kind, then by a pattern in the kind's name; ground speed otherwise.
const RATE_FACTS = ["peak_rate_fpm", "altitude_change_ft", "duration_seconds"];
const KEY_FACTS = new Map([
  [
    "flight-patterns",
    [
      "complete_passes",
      "closed_loops",
      "duration_seconds",
      "sample_count",
      "accumulated_turns",
    ],
  ],
  [
    "transponder-reports",
    [
      "code_sequence",
      "code",
      "advisory",
      "advisory_complement",
      "squawk",
      "emergency_status",
    ],
  ],
  ["stratospheric_balloon", ["alt_geom", "alt_baro", "max_altitude_ft", "gs"]],
  ["position_jump", ["implied_speed_kn", "displacement_nm", "elapsed_seconds"]],
  [
    "above_rated_altitude",
    ["rated_lowest_alt_ft", "rated_max_altitude_ft", "rated_margin_ft"],
  ],
  [
    "above_rated_speed",
    [
      "rated_estimated_tas_kt",
      "rated_reported_tas_kt",
      "rated_mach",
      "rated_margin_percent",
    ],
  ],
  ["rapid_climb", RATE_FACTS],
  ["rapid_descent", RATE_FACTS],
]);
const KEY_FACT_PATTERNS = [
  [/^gnss_integrity/, ["nic", "nac_p", "sil"]],
  [/alt|high/, ["archive_alt", "alt_baro", "alt_geom"]],
  [/vertical|rate/, ["baro_rate", "geom_rate"]],
];

export function createJournal(
  input,
  {
    perPage = DEFAULT_PAGE_SIZE,
    references = {},
    media = {},
    publicationOrigin = "",
  } = {},
) {
  const snapshot = validateSnapshot(input);
  if (!PAGE_SIZES.includes(perPage)) throw new Error("Unsupported page size");
  const events = [...snapshot.events].sort(
    (a, b) =>
      stamp(b.observed_start) - stamp(a.observed_start) ||
      a.id.localeCompare(b.id),
  );
  const records = events.map((event) =>
    searchRecord(event, media[event.id]?.airportPattern),
  );
  const prepared = prepareRecords(records, types);
  const queryMetadata = {
    types,
    window: snapshot.window,
    available_from: snapshot.available_from,
    earliest_event:
      events.at(-1)?.observed_start.slice(0, 10) || snapshot.available_from,
    aircraftChoices: aircraftChoices(events),
  };
  const presentation = createHash("sha256")
    .update(
      stableJson({
        rendererVersion,
        queryVersion: QUERY_VERSION,
        perPage,
        snapshot: { ...snapshot, events },
        media,
        references,
        ...(publicationOrigin ? { publicationOrigin } : {}),
      }),
    )
    .digest("hex");
  const namespace = `${snapshot.snapshot_id}/query-v${QUERY_VERSION}/${presentation}/`;
  const base = `/event-data/${namespace}`;
  const pageBase = `/event-pages/${namespace}`;
  const viewKey = (date = "", type = "") =>
    [date ? `day-${date}` : "all", type].filter(Boolean).join("--");
  const viewPath = (key, page) => `${pageBase}${key}/${page}/`;
  const views = new Map();
  for (const date of [
    "",
    "archive",
    ...snapshot.days.filter((d) => d.status === "available").map((d) => d.date),
  ]) {
    for (const type of ["", ...types.map((t) => t.id)]) {
      const query = {
        ...defaultQuery(queryMetadata),
        from:
          date === "archive"
            ? historyStart(queryMetadata)
            : date || snapshot.window.start,
        to: date && date !== "archive" ? date : snapshot.window.end,
        type,
        size: perPage,
      };
      const result = queryPage(prepared, query, queryMetadata);
      const selectedIds = new Set(result.matching.map((event) => event.id));
      const selected = events.filter((event) => selectedIds.has(event.id));
      const key =
        date === "archive"
          ? ["archive", type].filter(Boolean).join("--")
          : viewKey(date, type);
      views.set(key, {
        key,
        date,
        type,
        count: selected.length,
        pages: Math.max(1, Math.ceil(selected.length / perPage)),
        events: selected,
        query,
        chart: timeline(prepared, query, queryMetadata),
      });
    }
  }
  const chartDays = (chart) =>
    chart.buckets.map((day, index) => ({
      ...day,
      date: day.from,
      label:
        day.from === day.to
          ? dateLabel(day.from)
          : `${dateLabel(day.from)} – ${dateLabel(day.to)}`,
      detail: `${day.from}${day.to !== day.from ? ` to ${day.to}` : ""}: ${day.total === null ? "archive unavailable" : `${day.total} matching event${day.total === 1 ? "" : "s"}`}${day.partial ? "; includes partial day" : ""}${day.incomplete ? "; archive incomplete" : ""}`,
      tick: [
        0,
        Math.floor((chart.buckets.length - 1) / 2),
        chart.buckets.length - 1,
      ].includes(index)
        ? dateLabel(day.from).replace(/ \d{4}$/, "")
        : "",
      segments: types.map((type) => ({
        ...type,
        count: day.counts?.[type.id] || 0,
      })),
    }));
  function preferredMeasurements(event) {
    const kind = event.kind || "";
    const keys = KEY_FACTS.get(event.primary_type) ||
      KEY_FACTS.get(kind) ||
      KEY_FACT_PATTERNS.find(([pattern]) => pattern.test(kind))?.[1] || ["gs"];
    const rank = (m) =>
      keys.includes(m.key) ? keys.indexOf(m.key) : keys.length;
    return [...event.measurements].sort((a, b) => rank(a) - rank(b));
  }
  function card(event, lead) {
    const visual = media[event.id] || event.media;
    const ordered = preferredMeasurements(event);
    // An episode's own code row already names the same transponder code.
    const episodeCode = ordered.find((item) => item.key === "code")?.value;
    const keyMeasurements = ordered
      .filter((item) =>
        event.kind === "outlier_gs"
          ? item.key === "gs"
          : item.scope !== "detection-rule" &&
            item.key !== "callsign" &&
            (item.key !== "squawk" ||
              (event.primary_type === "transponder-reports" &&
                item.value !== episodeCode)),
      )
      .slice(0, event.kind === "outlier_gs" ? 1 : 3);
    const links = references[event.id] || event.references || [];
    const parts = [...sentences.segment(event.summary)].map((part) =>
      part.segment.trim(),
    );
    const aircraft = event.aircraft;
    const finding = patternFinding(event);
    const resources = referenceGroups(links);
    const typeName = aircraft.type_description
      ? aircraft.type_description +
        (aircraft.type_code ? ` (${aircraft.type_code})` : "")
      : aircraft.type_code;
    const watchlist = event.watchlist
      ? `Watchlist association source: ${event.watchlist.source_name}, which associates the aircraft with ${event.watchlist.association} (category: ${event.watchlist.list_category || event.watchlist.category}${event.watchlist.tags?.length ? `; tags: ${event.watchlist.tags.join(", ")}` : ""}). ${event.watchlist.attribution} Licences: ${event.watchlist.licenses.join("; ")}. ${event.watchlist.claim_scope}`
      : "";
    const notes = [
      finding ? event.summary : parts.slice(2).join(" "),
      watchlist,
      ...(event.limitations || []),
      visual?.caption,
      visual?.airportPattern && airportPatternNote(visual.airportPattern),
    ].filter(Boolean);
    return {
      ...event,
      lead,
      media: visual,
      details: {
        summary: finding || parts.slice(0, 2).join(" "),
        compactSummary: finding
          ? event.kind === "loitering"
            ? "An operational hold or flight purpose is unconfirmed."
            : "The track does not establish the flight's purpose."
          : parts.slice(0, 2).join(" "),
        registration: aircraft.registration || "Not recorded",
        typeName: typeName || "Aircraft type not recorded",
        distinctCallsign:
          aircraft.callsign && aircraft.callsign !== aircraft.registration
            ? aircraft.callsign
            : "",
        aircraft: [
          ["Tail number", aircraft.registration],
          ["Aircraft type", typeName],
          ["Callsign", aircraft.callsign],
          ["ICAO address", aircraft.icao_address],
        ].map(([label, value]) => ({ label, value: value || "Not recorded" })),
        participants: participantRows(event.participants),
        aircraftNote: event.synthetic
          ? "Illustrative aircraft; no real identity supplied."
          : aircraft.registration || typeName
            ? "Tail/type: current database matches; historical assignments unverified."
            : "",
        observationInterval:
          event.observed_start === event.observed_end
            ? preciseStamp(event.observed_start)
            : `${preciseStamp(event.observed_start)} to ${preciseStamp(event.observed_end)}`,
        measurementGroups: measurementGroups(keyMeasurements, {
          compact: true,
        }),
        allMeasurementGroups: measurementGroups(ordered),
        primaryReferences: resources.primary,
        moreReferences: resources.more,
        notes: [...new Set(notes)],
        mediaCaption: mediaCaption(visual, event),
        thumbnailCaption:
          visual?.kind === "track"
            ? "Track preview. Gaps retained."
            : mediaCaption(visual, event),
        airportPattern: visual?.airportPattern,
      },
      type: types.find((t) => t.id === event.primary_type),
      sourceBadge: sourceBadge(event.source),
      day: dateLabel(event.observed_start),
      time: timeLabel(event.observed_start),
    };
  }
  function pageContext(key, page = 1) {
    const view = views.get(key);
    if (!view || !Number.isInteger(page) || page < 1 || page > view.pages)
      throw new Error("Invalid journal page");
    const selected = view.events.slice((page - 1) * perPage, page * perPage);
    const lead = page === 1 && selected.length ? card(selected[0], true) : null;
    const rows = (lead ? selected.slice(1) : selected).map((e) =>
      card(e, false),
    );
    let lastDay = "";
    for (const row of rows) {
      row.newDay = row.day !== lastDay;
      lastDay = row.day;
    }
    const type = types.find((t) => t.id === view.type);
    return {
      key,
      page,
      pages: view.pages,
      count: view.count,
      date: view.date,
      type: view.type,
      lead,
      rows,
      title: [
        type ? `${type.label} stories` : "Stories",
        view.date === "archive"
          ? "all available history"
          : view.date
            ? `observed ${dateLabel(view.date)}`
            : `${dateLabel(view.query.from)} to ${dateLabel(view.query.to)}`,
      ].join(", "),
      heading: [
        type?.label || "All events",
        `${dateLabel(view.query.from)} – ${dateLabel(view.query.to)}`,
      ].join(" · "),
      result: `${view.count} ${snapshot.mode === "review" ? "review" : "published"} event${view.count === 1 ? "" : "s"} in this date range${type ? ` · ${type.label}` : ""}`,
      featuredLabel:
        view.date || view.type ? "Latest matching event" : "Latest event",
      pagination: pageLinks(
        { page, pages: view.pages },
        { label: "Event pages", noun: "events", url: (n) => viewPath(key, n) },
      ),
    };
  }
  const config = {
    ...queryMetadata,
    sources: SOURCES.map(({ id, label }) => ({ id, label })),
    version: QUERY_VERSION,
    presentation,
    snapshot: snapshot.snapshot_id,
    base,
    pageBase,
    mode: snapshot.mode,
    as_of_utc: snapshot.as_of_utc,
    eventCount: records.length,
    airportPatternCount: records.filter(
      (record) => record.airport_pattern_candidate,
    ).length,
  };
  function context(key = "all", page = 1) {
    const feed = pageContext(key, page);
    const view = views.get(key);
    const days = chartDays(view.chart);
    return {
      feed,
      heading: page > 1 ? `${feed.title}, page ${page}` : feed.title,
      chartStyles: `${base}chart.css`,
      days,
      types,
      max: view.chart.max,
      dayCount: view.chart.dayCount,
      aggregation: {
        day: "Daily counts",
        week: "Counts in 7-day groups",
        month: "Monthly counts",
        year: "Yearly counts",
      }[view.chart.unit],
      from: view.query.from,
      to: view.query.to,
      bounds: dateBounds(queryMetadata),
      aircraftChoices: config.aircraftChoices,
      sources: config.sources,
      airportPatternCount: config.airportPatternCount,
      hasPartial: days.some((day) => day.partial),
      allHistory: viewPath("archive", 1),
      chartTitle:
        snapshot.mode === "review"
          ? "Review events by observation date"
          : "Published events by observation date",
      range: `${dateLabel(view.query.from)} – ${dateLabel(view.query.to)} · UTC`,
      asOf: `${dateLabel(snapshot.as_of_utc)} · ${timeLabel(snapshot.as_of_utc)}`,
      timestamp: snapshot.as_of_utc,
      review: snapshot.mode === "review",
      synthetic: events.some((e) => e.synthetic),
      config: JSON.stringify({
        ...config,
        initial: { ...view.query, page },
      }).replaceAll("<", "\\u003c"),
    };
  }
  const rules = new Set();
  for (const view of views.values())
    for (const day of chartDays(view.chart))
      for (const { count } of day.segments)
        rules.add(
          `.daily-chart[data-chart-scale="${view.chart.max}"] .count-${count} { height: ${(count * 100) / view.chart.max}%; }`,
        );
  const chartCss = [...rules].join("\n");
  return {
    snapshot,
    events,
    records,
    views,
    context,
    pageContext,
    viewPath,
    chartCss,
    base,
    card,
    presentation,
  };
}
