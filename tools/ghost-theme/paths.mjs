import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const tools = path.dirname(fileURLToPath(import.meta.url));
export const root = path.resolve(tools, "../..");
export const theme = path.join(root, "themes/wtf-adsb");
export const output = path.join(root, "artifacts/ghost-theme");
export const preview = path.join(output, "preview");

// Topic routing belongs to the story engine's table. Builds and checks
// resolve it the same way.
export const engineTopics = path.join(
  root,
  "src/wtf_adsb_publisher/data/topics.json",
);
export const topicsFile = (override) =>
  override ? path.resolve(root, override) : engineTopics;

// Private host, path, and receiver-field names, matched in any case; no public
// file may contain them. The built-in patterns always apply. A site's own names
// come from the one-line regular expression in the file PRIVATE_TERMS_FILE
// names, which the publisher sets from private_terms_file in publisher.json.
const builtinPrivatePatterns = path.join(
  root,
  "src/wtf_adsb_publisher/data/private-patterns.txt",
);
export const privateNames = () => {
  const parts = [readFileSync(builtinPrivatePatterns, "utf8").trim()];
  const file = process.env.PRIVATE_TERMS_FILE;
  if (file) {
    if (!path.isAbsolute(file))
      throw new Error("PRIVATE_TERMS_FILE must be an absolute path");
    const terms = readFileSync(file, "utf8").trim();
    // As private_terms.py refuses: an operator who wants no terms deletes the key.
    if (!terms)
      throw new Error(
        "PRIVATE_TERMS_FILE is empty; delete private_terms_file to add no terms",
      );
    if (terms.includes("\n"))
      throw new Error(
        "PRIVATE_TERMS_FILE must hold one regular expression on one line",
      );
    if (new RegExp(terms, "i").test(""))
      throw new Error(
        "PRIVATE_TERMS_FILE matches the empty string, so it would refuse every text",
      );
    parts.push(terms);
  }
  return new RegExp(parts.join("|"), "i");
};
