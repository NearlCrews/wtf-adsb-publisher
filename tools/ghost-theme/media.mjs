import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { output, privateNames, tools } from "./paths.mjs";
import { validateGeographicReplay } from "../../themes/wtf-adsb/assets/js/geographic-track.mjs";
import { validateAirportPattern } from "./airport-patterns.mjs";

// Generated media is presentation, not a revision of the observation evidence.
// Bind every image to the exact reviewed snapshot and evidence before rendering.
export async function loadEventMedia(snapshot, filename) {
  const folder = await fs.realpath(path.dirname(path.resolve(filename)));
  const artifactRoot = await fs.realpath(output);
  if (!folder.startsWith(artifactRoot + path.sep))
    throw new Error(
      "Event media must be generated under artifacts/ghost-theme/",
    );
  const manifest = JSON.parse(await fs.readFile(filename, "utf8"));
  if (
    manifest.schema_version !== 1 ||
    manifest.snapshot_id !== snapshot.snapshot_id ||
    !manifest.events ||
    Array.isArray(manifest.events) ||
    typeof manifest.events !== "object" ||
    Object.keys(manifest.events).length !== snapshot.events.length
  )
    throw new Error("Media manifest does not match the observation snapshot");
  const privateText = privateNames();
  const kinds = new Set(["track", "measurement-chart", "observation-summary"]);
  // Figures beside the lead image, by role, with each role's fixed size.
  const roles = { profile: { width: 1200, height: 400 } };
  const figureKeys = new Set([
    "role",
    "evidence_version",
    "src",
    "width",
    "height",
    "alt",
    "caption",
  ]);
  const media = {};
  // {event id: [figure, ...]}, apart from the lead so cards and the journal keep their media.
  const eventFigures = {};
  const files = new Map();
  const svgs = [];
  async function loadAsset(src, extension) {
    if (!new RegExp(`^/event-media/[a-f0-9]{64}\\.${extension}$`).test(src))
      throw new Error("Invalid event media asset path");
    const name = path.basename(src);
    const source = path.join(folder, "event-media", name);
    if ((await fs.realpath(source)) !== source)
      throw new Error("Event media must not follow symlinks");
    if ((await fs.stat(source)).size > 1024 * 1024)
      throw new Error("Event media asset is too large");
    const data = await fs.readFile(source);
    if (
      data.length > 1024 * 1024 ||
      createHash("sha256").update(data).digest("hex") + "." + extension !==
        name ||
      privateText.test(data.toString())
    )
      throw new Error("Invalid event media asset or content hash");
    files.set(name, data);
    return data;
  }
  for (const event of snapshot.events) {
    const entry = manifest.events[event.id];
    if (
      !entry ||
      entry.evidence_version !== event.evidence_version ||
      !/^\/event-media\/[a-f0-9]{64}\.svg$/.test(entry.src) ||
      !kinds.has(entry.kind) ||
      entry.width !== 1200 ||
      entry.height !== 800 ||
      typeof entry.alt !== "string" ||
      !entry.alt.trim() ||
      entry.alt.length > 300 ||
      typeof entry.caption !== "string" ||
      !entry.caption.trim() ||
      entry.caption.length > 1200 ||
      (entry.creditPath !== undefined &&
        entry.creditPath !== "/about/#map-data") ||
      (entry.replaySrc !== undefined && entry.kind !== "track") ||
      (entry.airportPattern !== undefined &&
        (entry.kind !== "track" || !entry.replaySrc)) ||
      (entry.figures !== undefined &&
        (!Array.isArray(entry.figures) ||
          !entry.figures.length ||
          entry.figures.length > Object.keys(roles).length)) ||
      privateText.test(JSON.stringify(entry))
    )
      throw new Error(`Invalid or stale event media for ${event.id}`);
    const data = await loadAsset(entry.src, "svg");
    if (
      !/^<svg\s[^>]*xmlns="http:\/\/www\.w3\.org\/2000\/svg"/.test(
        data.toString(),
      )
    )
      throw new Error(`Invalid event SVG for ${event.id}`);
    svgs.push({ svg: data.toString() });
    const figures = [];
    for (const figure of entry.figures || []) {
      const size = roles[figure?.role];
      if (
        !size ||
        Object.keys(figure).some((key) => !figureKeys.has(key)) ||
        figures.some((other) => other.role === figure.role) ||
        figure.evidence_version !== event.evidence_version ||
        figure.width !== size.width ||
        figure.height !== size.height ||
        typeof figure.alt !== "string" ||
        !figure.alt.trim() ||
        figure.alt.length > 300 ||
        typeof figure.caption !== "string" ||
        !figure.caption.trim() ||
        figure.caption.length > 1200 ||
        privateText.test(JSON.stringify(figure))
      )
        throw new Error(`Invalid or stale event figure for ${event.id}`);
      const figureData = await loadAsset(figure.src, "svg");
      if (
        !/^<svg\s[^>]*xmlns="http:\/\/www\.w3\.org\/2000\/svg"/.test(
          figureData.toString(),
        )
      )
        throw new Error(`Invalid event SVG for ${event.id}`);
      svgs.push({ svg: figureData.toString(), height: figure.height });
      figures.push({ ...figure });
    }
    if (entry.replaySrc !== undefined) {
      const replay = JSON.parse(
        (await loadAsset(entry.replaySrc, "json")).toString(),
      );
      validateGeographicReplay(replay, {
        eventId: event.id,
        evidenceVersion: event.evidence_version,
        image: entry.src,
      });
      if (entry.airportPattern !== undefined)
        validateAirportPattern(entry.airportPattern, event, replay);
      const start = Date.parse(replay.started_at);
      const end = start + replay.points.at(-1).t * 1000;
      const observedStart = Date.parse(event.observed_start);
      const observedEnd = Date.parse(event.observed_end);
      if (
        !Number.isFinite(observedStart) ||
        !Number.isFinite(observedEnd) ||
        start < observedStart - 1 ||
        end > observedEnd + 1
      )
        throw new Error(
          "Replay falls outside its selected observation interval",
        );
      if (privateText.test(JSON.stringify(replay)))
        throw new Error("Replay contains a private name or receiver field");
    }
    media[event.id] = {
      event_id: event.id,
      evidence_version: entry.evidence_version,
      src: entry.src,
      alt: entry.alt,
      caption: entry.caption,
      kind: entry.kind,
      width: entry.width,
      height: entry.height,
      ...(entry.creditPath ? { creditPath: entry.creditPath } : {}),
      ...(entry.replaySrc ? { replaySrc: entry.replaySrc } : {}),
      ...(entry.airportPattern ? { airportPattern: entry.airportPattern } : {}),
    };
    if (figures.length) eventFigures[event.id] = figures;
  }
  const checked = spawnSync(
    "python3",
    [path.join(tools, "validate_media.py")],
    {
      input: JSON.stringify(svgs),
      encoding: "utf8",
      timeout: Math.max(10000, Math.min(60000, svgs.length * 200)),
    },
  );
  if (checked.status !== 0)
    throw new Error(
      "Generated SVG contains invalid or unsupported diagram content",
    );
  return { media, figures: eventFigures, files };
}
