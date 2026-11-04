import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import {
  aircraftPhotos,
  canonical,
  classPhotoFor,
  classPhotos,
  loadTypeTable,
  typeClass,
} from "./aircraft-photos.mjs";
import { loadAirframePhotos, thirdPartyResources } from "./airframe-photos.mjs";
import { loadEventMedia } from "./media.mjs";
import { output, theme, tools } from "./paths.mjs";
import {
  fixtureTableSha256,
  fixtureTypes,
  writeTypeTable,
} from "./reference-fixture.mjs";

// A fixture origin: the site's own URLs, which are not third-party loads.
const ORIGIN = "https://site.example";

await fs.mkdir(output, { recursive: true });
const folder = await fs.mkdtemp(path.join(output, "media-check-"));
const id = "00000000-0000-4000-8000-000000000001";
const snapshot = {
  snapshot_id: "a".repeat(64),
  events: [
    {
      id,
      evidence_version: "b".repeat(64),
      observed_start: "2026-10-01T12:00:00Z",
      observed_end: "2026-10-01T12:30:00Z",
    },
  ],
};
const svg =
  '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800"><title>Recorded track</title><path d="M1 2L3 4" /></svg>';
const filename = createHash("sha256").update(svg).digest("hex") + ".svg";
const source = path.join(folder, "event-media", filename);
const manifestPath = path.join(folder, "manifest.json");
const manifest = {
  schema_version: 1,
  snapshot_id: snapshot.snapshot_id,
  events: {
    [id]: {
      evidence_version: snapshot.events[0].evidence_version,
      src: `/event-media/${filename}`,
      kind: "track",
      width: 1200,
      height: 800,
      alt: "An aircraft's recorded path.",
      caption: "Selected aircraft observations; gaps remain visible.",
    },
  },
};
async function writeManifest(value = manifest) {
  await fs.writeFile(manifestPath, JSON.stringify(value));
}
try {
  await fs.mkdir(path.dirname(source));
  await fs.writeFile(source, svg);
  await writeManifest();
  const result = await loadEventMedia(snapshot, manifestPath);
  assert.equal(result.media[id].src, manifest.events[id].src);
  assert.equal(result.files.get(filename).toString(), svg);
  assert.equal(snapshot.events[0].media, undefined);

  for (const change of [
    (value) => (value.snapshot_id = "c".repeat(64)),
    (value) => (value.events[id].evidence_version = "c".repeat(64)),
    (value) => (value.events[id].src = "/event-media/../../private.svg"),
    (value) => (value.events[id].caption = "private.example.ts.net"),
    (value) => delete value.events[id],
    (value) => (value.events.extra = value.events[id]),
  ]) {
    const invalid = structuredClone(manifest);
    change(invalid);
    await writeManifest(invalid);
    await assert.rejects(loadEventMedia(snapshot, manifestPath));
  }
  await writeManifest();
  await fs.writeFile(source, svg.replace("L3 4", "L8 9"));
  await assert.rejects(loadEventMedia(snapshot, manifestPath));

  for (const body of [
    "<script>alert(1)</script>",
    '<image href="https://example.com/image.svg" />',
    '<rect onclick="alert(1)" />',
    '<a><animate attributeName="href" values="javascript:alert(1)" /><text>Click</text></a>',
    '<set attributeName="fill" to="url(https://example.com/image.svg)" />',
    '<rect fill="url(https://example.com/image.svg)" />',
    "<text>private.example.t&#115;.net</text>",
  ]) {
    const unsafe = svg.replace("</svg>", `${body}</svg>`);
    const hash = createHash("sha256").update(unsafe).digest("hex");
    const changed = structuredClone(manifest);
    changed.events[id].src = `/event-media/${hash}.svg`;
    await fs.writeFile(path.join(folder, "event-media", `${hash}.svg`), unsafe);
    await writeManifest(changed);
    await assert.rejects(loadEventMedia(snapshot, manifestPath));
  }
  await fs.writeFile(source, svg);
  const replay = {
    version: 1,
    kind: "geographic",
    event_id: id,
    evidence_version: snapshot.events[0].evidence_version,
    image: manifest.events[id].src,
    started_at: "2026-10-01T12:00:00Z",
    width: 1200,
    height: 800,
    points: [
      {
        t: 0,
        x: 100,
        y: 200,
        distance_nm: 0,
        speed: 100,
        altitude: 1200,
        altitude_reference: "unspecified",
      },
      {
        t: 15,
        x: 120,
        y: 210,
        distance_nm: 0.4,
        speed: null,
        altitude: null,
        altitude_reference: null,
      },
      {
        t: 100,
        x: 150,
        y: 215,
        distance_nm: 0.4,
        speed: 110,
        altitude: 1300,
        altitude_reference: "barometric",
        break: true,
      },
    ],
  };
  async function replayManifest(value) {
    const raw = JSON.stringify(value);
    const hash = createHash("sha256").update(raw).digest("hex");
    const changed = structuredClone(manifest);
    changed.events[id].replaySrc = `/event-media/${hash}.json`;
    await fs.writeFile(path.join(folder, "event-media", `${hash}.json`), raw);
    await writeManifest(changed);
    return changed;
  }
  const withReplay = await replayManifest(replay);
  const loaded = await loadEventMedia(snapshot, manifestPath);
  assert.equal(loaded.media[id].replaySrc, withReplay.events[id].replaySrc);
  assert.equal(loaded.media[id].evidence_version, replay.evidence_version);
  assert.equal(loaded.files.size, 2);
  for (const change of [
    (value) => (value.event_id = "00000000-0000-4000-8000-000000000002"),
    (value) => (value.evidence_version = "c".repeat(64)),
    (value) => (value.image = `/event-media/${"c".repeat(64)}.svg`),
    (value) => (value.receiver_lat = 12.345678),
    (value) => (value.points[0].latitude = 12.345678),
    (value) => (value.points[0].x = -1),
    (value) => (value.points[0].t = 1),
    (value) => (value.points[1].t = 0),
    (value) => (value.points[1].distance_nm = 9),
    (value) => (value.points[1].speed = "100"),
    (value) => delete value.points[0].altitude_reference,
    (value) => delete value.points[2].break,
    (value) => (value.points[2].distance_nm = 2),
    (value) => (value.started_at = "2026-02-31T12:00:00Z"),
    (value) => (value.started_at = "2026-10-01T24:00:00Z"),
    (value) => (value.started_at = "2026-10-02T12:00:00Z"),
    (value) => (value.started_at = "2026-10-01T12:29:00Z"),
  ]) {
    const invalid = structuredClone(replay);
    change(invalid);
    await replayManifest(invalid);
    await assert.rejects(loadEventMedia(snapshot, manifestPath));
  }
  await writeManifest(withReplay);
  const airportReplay = {
    ...replay,
    points: Array.from({ length: 20 }, (_, index) => ({
      t: index * 30,
      x: 100 + index * 5,
      y: 200,
      distance_nm: index * 0.1,
      speed: 100,
      altitude: null,
      altitude_reference: null,
    })),
  };
  const airportSnapshot = structuredClone(snapshot);
  Object.assign(airportSnapshot.events[0], {
    kind: "circling",
    aircraft: { type_code: "C172" },
    measurements: [
      {
        key: "closed_loops",
        scope: "analysis-window",
        value: 2,
        start_utc: replay.started_at,
        end_utc: "2026-10-01T12:09:30Z",
      },
    ],
  });
  const airportManifest = await replayManifest(airportReplay);
  airportManifest.events[id].airportPattern = {
    version: 1,
    classifier: "runway-circuits-v1",
    dataset_version: "ourairports-runways-v1",
    dataset_sha256:
      "10d4d7f9b9810c531e2a22ec2e5393887a69db32aaf9a92f9cbf5388c4a3a39e",
    airport_ident: "KPTK",
    airport_name: "Oakland County International Airport",
    runway: "09L/27R",
    aligned_passes: 2,
    closed_loops: 2,
  };
  await writeManifest(airportManifest);
  const airportLoaded = await loadEventMedia(airportSnapshot, manifestPath);
  assert.deepEqual(
    airportLoaded.media[id].airportPattern,
    airportManifest.events[id].airportPattern,
  );
  for (const change of [
    (value) => (value.aircraft.type_code = "B407"),
    (value) => (value.aircraft.type_code = "UNKNOWN"),
    (value) => (value.synthetic = true),
    (value) => (value.kind = "loitering"),
    (value) => (value.measurements[0].scope = "observation"),
    (value) => delete value.measurements[0].start_utc,
    (value) => (value.measurements[0].start_utc = "2026-10-01T12:00:01Z"),
    (value) => (value.measurements[0].end_utc = "2026-10-01T12:20:00Z"),
    (value) => (value.measurements[0].value = 3),
  ]) {
    const invalid = structuredClone(airportSnapshot);
    change(invalid.events[0]);
    await assert.rejects(loadEventMedia(invalid, manifestPath));
  }
  for (const change of [
    (value) => (value.airportPattern.dataset_sha256 = "c".repeat(64)),
    (value) => (value.airportPattern.airport_name = "Other airport"),
    (value) => (value.airportPattern.receiver_lat = 1),
    (value) => (value.airportPattern.aligned_passes = 1),
    (value) => delete value.replaySrc,
  ]) {
    const invalid = structuredClone(airportManifest);
    change(invalid.events[id]);
    await writeManifest(invalid);
    await assert.rejects(loadEventMedia(airportSnapshot, manifestPath));
  }
  await writeManifest(withReplay);
  await fs.writeFile(
    path.join(folder, withReplay.events[id].replaySrc.slice(1)),
    "{}",
  );
  await assert.rejects(loadEventMedia(snapshot, manifestPath));

  // A second figure: the altitude profile, 1200 by 400, listed by its role.
  const profileSvg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="400" viewBox="0 0 1200 400"><title>Altitude profile</title><path d="M150 300L1110 100" fill="none" stroke="#187268"/></svg>';
  const profileName =
    createHash("sha256").update(profileSvg).digest("hex") + ".svg";
  await fs.writeFile(path.join(folder, "event-media", profileName), profileSvg);
  const profileFigure = {
    role: "profile",
    evidence_version: snapshot.events[0].evidence_version,
    src: `/event-media/${profileName}`,
    width: 1200,
    height: 400,
    alt: "Altitude over time for this episode.",
    caption: "Pressure altitude against time.",
  };
  const withProfile = structuredClone(manifest);
  withProfile.events[id].figures = [profileFigure];
  await writeManifest(withProfile);
  const profiled = await loadEventMedia(snapshot, manifestPath);
  assert.deepEqual(profiled.figures[id], [profileFigure]);
  assert.equal(profiled.media[id].figures, undefined);
  assert.deepEqual(result.figures, {});
  assert.equal(profiled.files.get(profileName).toString(), profileSvg);
  assert.equal((await loadEventMedia(snapshot, manifestPath)).files.size, 2);
  for (const change of [
    (figures) => (figures[0].role = "chart"),
    (figures) => (figures[0].height = 800),
    (figures) => (figures[0].width = 1000),
    (figures) => (figures[0].alt = " "),
    (figures) => delete figures[0].caption,
    (figures) => (figures[0].evidence_version = "c".repeat(64)),
    (figures) => (figures[0].src = "/event-media/../private.svg"),
    (figures) => (figures[0].caption = "private.example.ts.net"),
    (figures) => (figures[0].replaySrc = manifest.events[id].src),
    (figures) => figures.push(structuredClone(figures[0])),
    (figures) => figures.splice(0),
  ]) {
    const invalid = structuredClone(withProfile);
    change(invalid.events[id].figures);
    await writeManifest(invalid);
    await assert.rejects(loadEventMedia(snapshot, manifestPath));
  }
  // A profile drawn at the map's size, or a map at the profile's, fails the
  // SVG check; so does a map background inside a profile.
  for (const body of [
    profileSvg.replaceAll("400", "800"),
    profileSvg.replace(
      "</svg>",
      `<image x="90" y="165" width="1020" height="440" href="data:image/png;base64,AA=="/></svg>`,
    ),
  ]) {
    const hash = createHash("sha256").update(body).digest("hex");
    await fs.writeFile(path.join(folder, "event-media", `${hash}.svg`), body);
    const invalid = structuredClone(withProfile);
    invalid.events[id].figures[0].src = `/event-media/${hash}.svg`;
    await writeManifest(invalid);
    await assert.rejects(loadEventMedia(snapshot, manifestPath));
  }
  await writeManifest();

  // The renderer: a formation map draws the other aircraft's saved track in
  // the second color under a two-entry legend, and a TCAS profile plots both
  // aircraft's pressure altitude on a 1200 by 400 figure the SVG check accepts.
  const rendered = spawnSync(
    "python3",
    [
      "-B",
      "-c",
      `
import importlib.util, json, sys
sys.path.insert(0, ${JSON.stringify(tools)})
spec = importlib.util.spec_from_file_location("event_media", ${JSON.stringify(path.join(tools, "event-media.py"))})
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)
from validate_media import validate
start = 1790000000
def track(lat, climb):
    return [{"ts": start + i * 15, "lat": lat + i * 0.002, "lon": -150 + i * 0.003, "alt_baro": 20000 + i * climb, "gs": 300, "source": "adsb_icao"} for i in range(20)]
evidence = {"maneuver_samples": track(-45, 0), "participants": [{"hex": "ae5678", "role": "wingman", "track": track(-44.999, 0)}]}
event = {"id": "formation", "kind": "formation_flight", "evidence_version": "b" * 64, "aircraft": {"registration": "05-5140", "type_code": "C17"}, "observed_start": "2026-09-21T12:13:20+00:00", "observed_end": "2026-09-21T12:18:05+00:00"}
parts = media.segments(evidence["maneuver_samples"], start, start + 285)
others = media.participant_tracks(evidence, start, start + 285)
image = media.track_image(event, parts, analysis_window=False, others=others)
validate(image["svg"])
tcas = {**event, "kind": "tcas_ra"}
threat = media.participant_tracks({"participants": [{"role": "threat", "track": track(-44.99, 120)}]}, start, start + 285)
profile = media.profile_image(tcas, media.segments(track(-45, -100), start, start + 285), threat)
validate(profile["svg"], height=400)
try:
    validate(profile["svg"])
except ValueError:
    pass
else:
    raise SystemExit("A profile passed as a map")
print(json.dumps({"map": image["svg"], "caption": image["caption"], "profile": profile["svg"], "role": profile["role"], "size": [profile["width"], profile["height"]]}))
`,
    ],
    { encoding: "utf8", timeout: 60000 },
  );
  assert.equal(rendered.status, 0, rendered.stderr);
  const drawn = JSON.parse(rendered.stdout);
  const strokes = (svgText, color) =>
    svgText.split(`fill="none" stroke="${color}" stroke-width="7"`).length - 1;
  assert.equal(strokes(drawn.map, "#187268"), 1);
  assert.equal(strokes(drawn.map, "#af4735"), 1);
  assert.match(drawn.map, /05-5140/);
  assert.match(drawn.map, /Other aircraft/);
  assert.match(drawn.caption, /other aircraft/);
  assert.equal(drawn.role, "profile");
  assert.deepEqual(drawn.size, [1200, 400]);
  assert.match(drawn.profile, /viewBox="0 0 1200 400"/);
  assert.match(drawn.profile, /Pressure altitude \(ft\)/);
  assert.match(drawn.profile, /Minutes from the first report/);
  assert.equal(
    drawn.profile.split('fill="none" stroke-linejoin="round"').length - 1,
    2,
  );

  // The pinned browser re-encodes a map background as a still, untagged WebP
  // that the SVG validator accepts; the same input always gives the same bytes,
  // whether the run's shared browser session or a fresh one encodes it.
  const webp = spawnSync(
    "python3",
    [
      "-B",
      "-c",
      `
import base64, struct, subprocess, sys, zlib
sys.path.insert(0, ${JSON.stringify(tools)})
from raster_png import normalize_png
from raster_webp import QUALITY, encode_webp, normalize_webp
from validate_media import validate
def chunk(name, data):
    return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
rows = b"".join(b"\\0" + bytes(((x // 17) * 29 + (y // 11) * 13 + (x * y) % 7) % 256 for x in range(1020) for _ in range(3)) for y in range(440))
png = normalize_png(b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1020, 440, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
first, second = encode_webp(png), encode_webp(png)
assert first == second, "WebP encoding is not deterministic"
fresh = subprocess.run(["node", ${JSON.stringify(path.join(tools, "webp.mjs"))}, "--quality", str(QUALITY)], input=png, capture_output=True, check=True).stdout
assert normalize_webp(fresh) == first, "A shared encoder session changes the bytes"
image = '<image x="90" y="165" width="1020" height="440" href="data:image/webp;base64,' + base64.b64encode(first).decode() + '"/>'
validate('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800">' + image + "</svg>")
print(len(png), len(first))
`,
    ],
    { encoding: "utf8", timeout: 120000 },
  );
  assert.equal(webp.status, 0, webp.stderr);

  // Every type photo matches its credit record: file, hash, size, frame and
  // an accepted license, with no file left without a record.
  const photoDir = path.join(theme, "assets/images/aircraft");
  const ids = new Set();
  const codes = new Set();
  for (const photo of aircraftPhotos) {
    assert.ok(!ids.has(photo.id), photo.id);
    ids.add(photo.id);
    for (const code of photo.typeCodes) {
      assert.ok(!codes.has(code), `${code} has two photos`);
      codes.add(code);
    }
    const data = await fs.readFile(path.join(theme, photo.src));
    const digest = createHash("sha256").update(data).digest("hex");
    assert.equal(
      photo.src,
      `/assets/images/aircraft/${photo.id}-${digest.slice(0, 12)}.webp`,
    );
    assert.equal(photo.sha256, digest, photo.id);
    assert.equal(photo.bytes, data.length, photo.id);
    assert.match(
      photo.license,
      /^(?:Public domain(?: \(United States\))?|CC0|CC BY(?:-SA)? \d\.\d(?: [a-z]{2,3})?)$/,
      photo.id,
    );
    assert.match(photo.licenseUrl, /^https:\/\//, photo.id);
    assert.match(photo.sourceUrl, /^https:\/\/commons\.wikimedia\.org\//);
    assert.match(photo.caption, /^Representative /, photo.id);
    assert.match(photo.alt, /representative/i, photo.id);
    for (const field of ["title", "alt", "author", "changes", "originalSha256"])
      assert.ok(photo[field]?.trim(), `${photo.id} ${field}`);
  }
  assert.deepEqual(
    (await fs.readdir(photoDir))
      .filter((name) => name.endsWith(".webp"))
      .sort(),
    aircraftPhotos.map((photo) => path.basename(photo.src)).sort(),
  );
  // Class photos name ICAO descriptions (landplane, two jet engines: "L2J")
  // and, optionally, type-table categories, but no type code. Their alt text
  // says the photo is not the story's aircraft, and the caption names the
  // model shown.
  const description = /^[A-Z-][0-9][A-Z-]$/;
  for (const photo of classPhotos) {
    assert.match(photo.id, /^class-[a-z0-9]+(?:-[a-z0-9]+)*$/);
    assert.deepEqual(photo.typeCodes, [], photo.id);
    assert.ok(photo.classes.length, photo.id);
    for (const value of photo.classes) assert.match(value, description);
    if (photo.categories)
      for (const value of photo.categories)
        assert.ok(typeof value === "string" && value.trim(), photo.id);
    assert.match(
      photo.alt,
      /^Representative [^,]+, not the aircraft in this story: /,
      photo.id,
    );
    assert.match(photo.caption, /^Representative .+ photo \(.+ shown\)\.$/);
  }
  // No two class photos claim the same type: photos that share a description
  // need categories on one side only (the one with categories wins) or
  // disjoint categories on both.
  for (const [index, a] of classPhotos.entries())
    for (const b of classPhotos.slice(index + 1)) {
      if (!a.classes.some((value) => b.classes.includes(value))) continue;
      assert.ok(
        Boolean(a.categories) !== Boolean(b.categories) ||
          (a.categories &&
            !a.categories.some((value) => b.categories.includes(value))),
        `${a.id} and ${b.id} claim the same types`,
      );
    }
  // Each designator's photo, from a fixture type table in the reference
  // directory. Without a table, no designator has a class photo.
  const classOf = (code) => classPhotoFor(code)?.id ?? null;
  assert.equal(await loadTypeTable(path.join(folder, "missing")), false);
  assert.equal(classOf("R44"), null);
  assert.equal(typeClass("C750"), null);
  // Only the table whose recorded hash is the expected one loads: the
  // fixture is refused against the reviewed table's hash, and a table whose
  // types are not an object is refused rather than breaking typeClass().
  const references = await writeTypeTable(path.join(folder, "references"));
  assert.equal(await loadTypeTable(references), false);
  assert.equal(typeClass("C750"), null);
  const malformed = path.join(folder, "malformed");
  await fs.mkdir(malformed);
  const table = { schema_version: 1, types: [], glider_types: [] };
  const recorded = createHash("sha256").update(canonical(table)).digest("hex");
  await fs.writeFile(
    path.join(malformed, "reference-aircraft-types-v1.json"),
    JSON.stringify({ ...table, content_sha256: recorded }),
  );
  assert.equal(await loadTypeTable(malformed, recorded), false);
  assert.equal(typeClass("C750"), null);
  assert.equal(await loadTypeTable(references, fixtureTableSha256), true);
  for (const [code, id] of Object.entries({
    C172: "class-single-engine-piston",
    BE58: "class-multi-engine-piston",
    P46T: "class-turboprop",
    BE20: "class-turboprop",
    C750: "class-business-jet",
    HDJT: "class-business-jet",
    A320: "class-airliner",
    E75L: "class-airliner",
    A388: "class-airliner",
    R44: "class-helicopter",
    B407: "class-helicopter",
    GLID: "class-glider",
    // A self-launching glider that the reference table lists as a glider.
    AS14: "class-glider",
    // A jet without a category, a four-engine turboprop and a gyroplane
    // have no class photo; nor do group or unknown designators.
    E390: null,
    C130: null,
    EL10: null,
    ZZZZ: null,
    BALL: null,
    NOPE: null,
  }))
    assert.equal(classOf(code), id, code);
  assert.deepEqual(typeClass("C750"), {
    description: "L2J",
    category: "business jet",
  });
  assert.equal(typeClass(undefined), null);
  // Every class photo stands in for some designator.
  const used = new Set(fixtureTypes.map(classOf));
  for (const photo of classPhotos) assert.ok(used.has(photo.id), photo.id);
  // Each is a still WebP of its recorded frame with no metadata, by the same
  // validator as the map backgrounds.
  const frames = spawnSync(
    "python3",
    [
      "-B",
      "-c",
      `
import json, sys
sys.path.insert(0, ${JSON.stringify(tools)})
from raster_webp import validate_webp
for photo in json.load(sys.stdin):
    with open(photo["file"], "rb") as image:
        validate_webp(image.read(), photo["width"], photo["height"])
`,
    ],
    {
      input: JSON.stringify(
        aircraftPhotos.map((photo) => ({
          file: path.join(theme, photo.src),
          width: photo.width,
          height: photo.height,
        })),
      ),
      encoding: "utf8",
      timeout: 60000,
    },
  );
  assert.equal(frames.status, 0, frames.stderr);

  // Airframe photos: the build keeps only allowed hosts and unchanged URLs.
  const airframeFile = path.join(folder, "airframe-photos.json");
  const good = {
    src: "https://t.plnspttrs.net/09400/1538509_151c8e97b4_280.jpg",
    width: 420,
    height: 280,
    link: "https://www.planespotters.net/photo/1538509/n52py-private-aero-commander-690?utm_source=api",
    photographer: "Test Photographer",
    registration: "N52PY",
  };
  // The publisher bound each photo to a registration (a vintage NC, NL, NR or
  // NX page included, as for "j"); a photo bound to another airframe, or to
  // none, is dropped.
  const events = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"].map(
    (name) => ({
      id: name,
      synthetic: name === "g",
      aircraft:
        name === "h" ? {} : { registration: name === "j" ? "N123" : "N52PY" },
    }),
  );
  await fs.writeFile(
    airframeFile,
    JSON.stringify({
      photos: {
        a: good,
        b: { ...good, src: "https://cdn.example.com/photo.jpg" },
        c: { ...good, link: "http://www.planespotters.net/photo/1/x" },
        d: { ...good, src: "https://T.plnspttrs.net/09400/x.jpg" },
        e: { ...good, photographer: " " },
        f: { ...good, width: 0 },
        g: good,
        h: good,
        i: {
          ...good,
          link: "https://www.planespotters.net/photo/1538509/n12345-other-aircraft?utm_source=api",
          registration: "N12345",
        },
        j: {
          ...good,
          link: "https://www.planespotters.net/photo/2/nc123-vintage-biplane?utm_source=api",
          registration: "N123",
        },
        k: { ...good, registration: undefined },
        missing: good,
      },
    }),
  );
  assert.deepEqual(
    [...(await loadAirframePhotos(airframeFile, events)).keys()],
    ["a", "j"],
  );
  // Third-party loads: only an airframe photo's image, inside its figure.
  const figure = `<figure class="airframe-photo column"><a href="${good.link}"><img src="${good.src}" alt="x"></a></figure>`;
  assert.deepEqual(
    thirdPartyResources(
      `<link rel="canonical" href="${ORIGIN}/x/"><link rel="alternate" href="https://example.com/feed"><a href="https://example.com/">x</a><img src="/assets/x.webp"><img src="data:image/png;base64,AA">${figure}`,
      ORIGIN,
      { story: true },
    ),
    [],
  );
  // Only on a story page, and the image host nowhere else in the page.
  assert.ok(thirdPartyResources(figure, ORIGIN).length);
  for (const html of [
    `<meta property="og:image" content="${good.src}">`,
    `<script type="application/ld+json">{"image":"${good.src}"}</script>`,
  ])
    assert.deepEqual(
      thirdPartyResources(figure + html, ORIGIN, { story: true }),
      ["Planespotters image host outside an airframe photo"],
      html,
    );
  for (const html of [
    `<img src="${good.src}">`,
    `<figure class="type-photo"><img src="${good.src}"></figure>`,
    `<figure class="airframe-photo"><img srcset="${good.src} 1x"></figure>`,
    `<figure class="airframe-photo"><img src="https://cdn.example.com/a.jpg"></figure>`,
    `<script src="https://cdn.example.com/a.js"></script>`,
    `<link rel="stylesheet" href="//fonts.example.com/a.css">`,
    `<div style="background:url(https://example.com/a.png)"></div>`,
    `<style>@import "https://example.com/a.css";</style>`,
    `<iframe src="https://example.com/"></iframe>`,
    `<img src="http://site.example/a.png">`,
    `<img src="\\\\cdn.example.com/a.png">`,
    `<img src="/\\cdn.example.com/a.png">`,
    `<img src="https&colon;//cdn.example.com/a.png">`,
    `<figure class="airframe-photo"><img src="https://cdn.example.com/a.jpg" src="${good.src}"></figure>`,
    `<svg><image href="https://cdn.example.com/a.png"/></svg>`,
    `<svg><use href="https://cdn.example.com/a.svg#x"/></svg>`,
    `<div style="background-image: image-set('/a.png' 1x, 'https://cdn.example.com/b.png' 2x)"></div>`,
  ])
    assert.ok(thirdPartyResources(html, ORIGIN, { story: true }).length, html);
  console.log(
    "Media evidence, content hashes, altitude profiles, second tracks, type and class photo credits, class photo selection, airframe photo input, and unsafe-image checks passed.",
  );
} finally {
  await fs.rm(folder, { recursive: true, force: true });
}
