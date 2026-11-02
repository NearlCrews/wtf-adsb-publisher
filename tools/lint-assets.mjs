// Check SVG as XML as well as formatting it with Prettier's HTML parser.
// Python is already required by this project; no XML dependency is needed.
import { globSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

const svgFiles = globSync("themes/wtf-adsb/assets/**/*.svg");
const xmlCheck = `
import sys
from xml.etree import ElementTree
root = ElementTree.fromstring(sys.stdin.buffer.read())
if root.tag != "{http://www.w3.org/2000/svg}svg":
    raise ValueError("Expected an SVG root element with its namespace")
`;
let failures = 0;
for (const path of svgFiles) {
  const result = spawnSync("python3", ["-c", xmlCheck], {
    input: readFileSync(path),
    encoding: "utf8",
  });
  if (result.error || result.status !== 0) {
    console.error(`${path}: ${result.error?.message || result.stderr}`);
    failures += 1;
  }
}
if (failures) process.exitCode = 1;
else console.log(`SVG XML validation passed (${svgFiles.length} files).`);
