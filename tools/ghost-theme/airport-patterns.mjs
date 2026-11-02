import fs from "node:fs/promises";
import { createHash } from "node:crypto";

const rule = JSON.parse(
  await fs.readFile(
    new URL("./data/airport-pattern-rule-v1.json", import.meta.url),
    "utf8",
  ),
);
const rawDataset = await fs.readFile(
  new URL("./data/ourairports-runways-v1.json", import.meta.url),
);
if (
  createHash("sha256").update(rawDataset).digest("hex") !==
  rule.dataset_file_sha256
)
  throw new Error("Runway reference data differs from the reviewed version");
const dataset = JSON.parse(rawDataset.toString());
const fields = new Set([
  "version",
  "classifier",
  "dataset_version",
  "dataset_sha256",
  "airport_ident",
  "airport_name",
  "runway",
  "aligned_passes",
  "closed_loops",
]);

export function validateAirportPattern(value, event, replay) {
  const loops = (event.measurements || []).filter(
    (measurement) => measurement.key === "closed_loops",
  );
  if (
    !value ||
    Array.isArray(value) ||
    typeof value !== "object" ||
    Object.keys(value).length !== fields.size ||
    Object.keys(value).some((field) => !fields.has(field)) ||
    value.version !== rule.version ||
    value.classifier !== rule.classifier ||
    value.dataset_version !== rule.dataset_version ||
    value.dataset_sha256 !== rule.dataset_sha256 ||
    dataset.content_sha256 !== rule.dataset_sha256 ||
    event.synthetic ||
    event.kind !== "circling" ||
    !rule.fixed_wing_types.includes(event.aircraft?.type_code) ||
    loops.length !== 1 ||
    loops[0].scope !== "analysis-window" ||
    loops[0].value !== value.closed_loops ||
    !Number.isSafeInteger(value.closed_loops) ||
    value.closed_loops < 2 ||
    value.closed_loops > 100 ||
    !Number.isSafeInteger(value.aligned_passes) ||
    value.aligned_passes < 2 ||
    value.aligned_passes > value.closed_loops + 1 ||
    !replay ||
    replay.points.length < 20 ||
    replay.points.at(-1).t < 300 ||
    replay.points.at(-1).t > 3600 ||
    Date.parse(loops[0].start_utc) !== Date.parse(replay.started_at) ||
    Math.abs(
      Date.parse(loops[0].end_utc) -
        Date.parse(replay.started_at) -
        replay.points.at(-1).t * 1000,
    ) > 1 ||
    !Number.isFinite(Date.parse(loops[0].end_utc)) ||
    !dataset.runways.some(
      (runway) =>
        runway.airport_ident === value.airport_ident &&
        runway.airport_name === value.airport_name &&
        runway.runway === value.runway,
    )
  )
    throw new Error("Invalid airport-pattern presentation annotation");
  return value;
}

export function airportPatternNote(value) {
  return (
    `Airport-pattern candidate: ${value.aligned_passes} observed passes in one continuous track segment align with the ${value.runway} runway axis at ${value.airport_name} (${value.airport_ident}), with offset return legs in the opposite direction. ` +
    `The selected analysis window contains ${value.closed_loops} recorded loops. ` +
    "This may be airport circuit practice, but the geometry does not confirm training, landings, or which runway was used. " +
    "This optional filter uses current aircraft-type metadata and a pinned runway reference; neither verifies the historical operation. Helicopters, unknown types, and insufficient evidence stay visible."
  );
}
