// A small aircraft-type table for the checks, in the shape of the operator's
// reference-aircraft-types-v1.json, which does not ship. It lists only the
// designators the class-photo checks name; it is not reference data.
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { canonical } from "./aircraft-photos.mjs";

const content = {
  schema_version: 1,
  dataset_version: "fixture",
  types: {
    A320: ["L2J", "M"],
    A388: ["L4J", "H"],
    AS14: ["L1P", "L"],
    B407: ["H1T", "L"],
    BALL: ["B0-", "-"],
    BE20: ["L2T", "M"],
    BE58: ["L2P", "L"],
    C130: ["L4T", "M"],
    C172: ["L1P", "L"],
    C750: ["L2J", "M"],
    E390: ["L2J", "M"],
    E75L: ["L2J", "M"],
    EL10: ["G1P", "L"],
    GLID: ["L0-", "-"],
    HDJT: ["L2J", "L"],
    P46T: ["L1T", "L"],
    R44: ["H1P", "L"],
    ZZZZ: ["-0-", "-"],
  },
  earliest_year: {},
  group_designators: { BALL: "balloon", GLID: "glider", ZZZZ: "unassigned" },
  glider_types: ["AS14", "GLID"],
  uav_types: [],
};

export const fixtureTypes = Object.keys(content.types);
// The fixture's recorded content hash: the `expected` argument of
// loadTypeTable() and the value of a check build's --type-table-sha256.
export const fixtureTableSha256 = createHash("sha256")
  .update(canonical(content))
  .digest("hex");

// Write the table into `directory` (created when missing) and return the
// directory, for loadTypeTable() or a build's WTF_ADSB_REFERENCE_DIR.
export async function writeTypeTable(directory) {
  await fs.mkdir(directory, { recursive: true, mode: 0o700 });
  const data = { ...content, content_sha256: fixtureTableSha256 };
  await fs.writeFile(
    path.join(directory, "reference-aircraft-types-v1.json"),
    JSON.stringify(data),
  );
  return directory;
}
