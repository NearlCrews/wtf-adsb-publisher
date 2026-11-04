import { displayNumber, measurementGroups } from "./journal.mjs";

// The evidence block on a story page: up to five headline measurements, each
// with a one-line meaning for a reader new to ADS-B, the rest of the record in
// a collapsed details element with the evidence version, and only the
// limitations that belong to the kind, ending with a field guide link.

export const KEY_FACT_LIMIT = 5;

// What a headline measurement means, by measurement key.
export const MEANINGS = {
  gs: "speed over the ground; wind adds to or subtracts from it",
  alt_baro: "pressure altitude, the figure pilots and controllers use",
  alt_geom:
    "GPS altitude, usually within a few hundred feet of pressure altitude",
  baro_rate: "climb or descent rate from pressure altitude; negative is down",
  peak_rate_fpm: "the fastest rate in the episode; negative is down",
  altitude_change_ft: "height lost or gained over the episode",
  duration_seconds: "how long the episode lasted",
  min_separation_nm: "the closest the two aircraft came horizontally",
  vertical_separation_ft: "the height difference at that moment",
  asymmetry_kn: "eastbound median minus westbound median",
  reversals: "how many times the climb turned into a descent or back",
  span_nm: "the width of the area the pattern fit inside",
  lateral_spacing_nm: "the distance between the two parallel lines",
};

// A measurement without a line of its own says what kind of figure it is.
const SCOPE_MEANINGS = {
  "saved-report": "one saved report from the aircraft",
  "peak-report": "the peak report in the episode",
  "analysis-window": "measured over the analyzed interval",
  "archived-label": "the last archived label, carried between saved positions",
  "reference-data": "a reference figure, not a reading from the aircraft",
  "discontinuity-pair-not-physical-travel":
    "calculated between inconsistent positions; not physical travel",
  "surrounding-reports": "from the saved reports around the outlying value",
};

// General caveats every record repeats; the field guide explains them once.
export const BOILERPLATE_LIMITATIONS = [
  "Aircraft metadata is a current database match",
  "Archive timestamps do not establish",
  "Individual field ages are unknown",
];

export const FIELD_GUIDE_LIMITATION = {
  text: "How to read these numbers",
  href: "/field-guide/",
};

const FEET_PER_NM = 6076.12;

function keyFact(measurement) {
  if (
    measurement.key === "min_separation_nm" &&
    typeof measurement.value === "number" &&
    measurement.value < 1
  ) {
    const feet = displayNumber(
      Math.round((measurement.value * FEET_PER_NM) / 10) * 10,
    );
    return {
      key: measurement.key,
      label: measurement.label,
      value: feet,
      unit: "ft",
      meaning: `about ${feet} feet apart`,
    };
  }
  // The card's compact form: a duration of a minute or more in minutes.
  const row = measurementGroups([measurement], { compact: true })[0].rows[0];
  return {
    key: measurement.key,
    label: row.label,
    value: row.value,
    unit: row.unit,
    meaning:
      MEANINGS[measurement.key] ||
      SCOPE_MEANINGS[measurement.scope] ||
      "a recorded value",
  };
}

export function evidenceView(event) {
  const measurements = event.measurements || [];
  const first = new Map();
  for (const item of measurements)
    if (!first.has(item.key)) first.set(item.key, item);
  const headline = [...new Set(event.headline_measurements || [])]
    .map((key) => first.get(key))
    .filter(Boolean);
  // A detection-rule figure is a site setting, so it never leads.
  const chosen = (
    headline.length
      ? headline
      : measurements.filter((item) => item.scope !== "detection-rule")
  ).slice(0, KEY_FACT_LIMIT);
  const detailMeasurements = measurements.filter(
    (item) => !chosen.includes(item),
  );
  return {
    keyFacts: chosen.map(keyFact),
    detailMeasurements,
    detailGroups: measurementGroups(detailMeasurements),
    limitations: [
      ...(event.limitations || [])
        .filter(
          (text) =>
            !BOILERPLATE_LIMITATIONS.some((opening) =>
              text.startsWith(opening),
            ),
        )
        .map((text) => ({ text })),
      FIELD_GUIDE_LIMITATION,
    ],
    evidenceHash: event.evidence_version,
  };
}

// The exporter's observation card lists every measurement, then every
// limitation, and ends with the record details. The static build puts the
// rendered evidence block where the measurements were (or, with none, where
// the limitations or the lookup links begin) and drops the evidence version
// from the record details, since the block now shows it.
const RECORD = /<section class="observation-record[\s"]/;
const MEASUREMENTS =
  /<h3>Measurements<\/h3><p class="measurement-note">[^<]*<\/p>\n(?:<div class="record-measurements-group">[\s\S]*?<\/div>\n)*/;
const LIMITS = /<aside class="record-limits"[^>]*>[\s\S]*?<\/aside>\n?/;
const HASH = /<p>Evidence version: <code>[\s\S]*?<\/code>\.<\/p>\n?/;
const LATER = [/<aside class="sources"/, /<details class="record-details"/];

export function withEvidenceBlock(html, event, block) {
  if (!RECORD.test(html)) return html;
  const cut = (text, pattern) => {
    const match = pattern.exec(text);
    return match
      ? {
          at: match.index,
          text:
            text.slice(0, match.index) +
            text.slice(match.index + match[0].length),
        }
      : { at: -1, text };
  };
  const measured = cut(html.replace(HASH, ""), MEASUREMENTS);
  if (measured.at < 0 && event.measurements?.length)
    throw new Error(`Story ${event.id} has an unrecognized measurement list`);
  const limited = cut(measured.text, LIMITS);
  const at =
    measured.at >= 0
      ? measured.at
      : limited.at >= 0
        ? limited.at
        : LATER.map((pattern) => limited.text.search(pattern)).find(
            (index) => index >= 0,
          );
  if (at === undefined)
    throw new Error(`Story ${event.id} has no place for its evidence block`);
  return limited.text.slice(0, at) + block + limited.text.slice(at);
}
