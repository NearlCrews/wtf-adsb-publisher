// Shared by the offline media importer and the browser replay. These fields are
// presentation derived from a reviewed event, never a raw receiver export.
const ROOT_KEYS = new Set([
  "version",
  "kind",
  "event_id",
  "evidence_version",
  "image",
  "started_at",
  "width",
  "height",
  "points",
]);
const POINT_KEYS = new Set([
  "t",
  "x",
  "y",
  "distance_nm",
  "speed",
  "altitude",
  "altitude_reference",
  "break",
]);
const REFERENCES = new Set(["barometric", "geometric", "unspecified"]);
const object = (value) =>
  value && typeof value === "object" && !Array.isArray(value);
const onlyKeys = (value, keys) =>
  object(value) && Object.keys(value).every((key) => keys.has(key));
const finite = (value) => typeof value === "number" && Number.isFinite(value);

export function validateGeographicReplay(
  data,
  { eventId, evidenceVersion, image },
) {
  if (
    !onlyKeys(data, ROOT_KEYS) ||
    data.version !== 1 ||
    data.kind !== "geographic" ||
    data.event_id !== eventId ||
    data.evidence_version !== evidenceVersion ||
    data.image !== image ||
    !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(
      data.event_id,
    ) ||
    !/^[a-f0-9]{64}$/.test(data.evidence_version) ||
    !/^\/event-media\/[a-f0-9]{64}\.svg$/.test(data.image) ||
    typeof data.started_at !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(
      data.started_at,
    ) ||
    !Number.isFinite(Date.parse(data.started_at)) ||
    new Date(data.started_at).toISOString().slice(0, 19) !==
      data.started_at.slice(0, 19) ||
    data.width !== 1200 ||
    data.height !== 800 ||
    !Array.isArray(data.points) ||
    data.points.length < 3 ||
    data.points.length > 256
  )
    throw new Error("Replay does not match this recorded track");
  let previous;
  let connected = false;
  for (const point of data.points) {
    if (
      !onlyKeys(point, POINT_KEYS) ||
      !finite(point.t) ||
      point.t < 0 ||
      point.t > 86400 ||
      !finite(point.x) ||
      point.x < 90 ||
      point.x > 1110 ||
      !finite(point.y) ||
      point.y < 165 ||
      point.y > 605 ||
      !finite(point.distance_nm) ||
      point.distance_nm < 0 ||
      (point.speed !== null && (!finite(point.speed) || point.speed < 0)) ||
      (point.altitude !== null && !finite(point.altitude)) ||
      (point.altitude === null
        ? point.altitude_reference !== null
        : !REFERENCES.has(point.altitude_reference)) ||
      ("break" in point && point.break !== true) ||
      (!previous &&
        (point.t !== 0 || point.distance_nm !== 0 || point.break)) ||
      (previous &&
        (point.t <= previous.t ||
          point.distance_nm < previous.distance_nm ||
          (point.break && point.distance_nm !== previous.distance_nm) ||
          (!point.break &&
            (point.t - previous.t > 45 ||
              point.distance_nm - previous.distance_nm >
                (point.t - previous.t) / 3 + 0.0002))))
    )
      throw new Error("Replay contains invalid saved positions or readings");
    if (previous && !point.break) connected = true;
    previous = point;
  }
  if (!connected) throw new Error("Replay has no connected recorded segment");
  return data;
}
