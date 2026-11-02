import { existsSync } from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { root } from "./paths.mjs";

// A private (0700) scratch directory for check inputs: WTF_CHECK_SCRATCH when
// set, the repository's ignored .private/ when it exists, else the system
// temporary directory. Worktrees have no .private/, so checks still run there.
export async function privateScratch(prefix) {
  const base =
    process.env.WTF_CHECK_SCRATCH ||
    (existsSync(path.join(root, ".private"))
      ? path.join(root, ".private")
      : os.tmpdir());
  return fs.mkdtemp(path.join(base, prefix));
}
