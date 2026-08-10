// Vendor the gazetteer asset into this package.
//
// The asset is the product; the language is the wrapper. `notability.txt.gz` is
// ~2.1 MB of folded Wikidata, Census and SSA evidence with a format number and a
// sha256 manifest, and every front door must load THE SAME BYTES — a port with
// its own gazetteer is a second detector wearing the first one's name.
//
// So this copies rather than rebuilds, and it copies from the one tracked source
// in the repository. Vendoring rather than fetching at install time is deliberate:
// "no network, no per-request cost" is the product claim, and a build-time fetch
// puts a fetch back in the story.
//
// The copy is .gitignore'd. It is a build input reproduced from a tracked file,
// and a second tracked copy is a second thing to bump per asset cut — which is
// exactly how two front doors end up shipping different gazetteers.

import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const packageRoot = resolve(here, "..");
const repoRoot = resolve(packageRoot, "..");
const source = join(repoRoot, "python", "src", "vicary", "data");
const target = join(packageRoot, "assets");

const FILES = ["notability.txt.gz", "MANIFEST.json"];

if (!existsSync(source)) {
  console.error(
    `no asset source at ${source}\n` +
      `This script vendors from the monorepo. Outside a checkout there is nothing ` +
      `to vendor from, and a published package should already carry assets/.`,
  );
  process.exit(2);
}

mkdirSync(target, { recursive: true });
for (const name of FILES) {
  copyFileSync(join(source, name), join(target, name));
}

// Verify what landed, not what was copied. `copyFileSync` returning without
// throwing says the call succeeded; it does not say the bytes on disk are the
// bytes the manifest describes, and a truncated or half-written asset loads as a
// SMALLER gazetteer — which redacts more, looks privacy-safe, and is invisible to
// every test that only checks output was masked.
const manifest = JSON.parse(readFileSync(join(target, "MANIFEST.json"), "utf8"));
const entry = manifest.assets["notability.txt.gz"];
const bytes = readFileSync(join(target, "notability.txt.gz"));
const digest = createHash("sha256").update(bytes).digest("hex");

if (bytes.length !== entry.bytes) {
  console.error(
    `vendored asset is ${bytes.length} bytes, manifest says ${entry.bytes}`,
  );
  process.exit(1);
}
if (digest !== entry.sha256) {
  console.error(
    `vendored asset sha256 ${digest} does not match manifest ${entry.sha256}`,
  );
  process.exit(1);
}

// stderr, not stdout. This runs as `prepack`, and `npm pack --json` writes its
// manifest to stdout — a confirmation line printed there corrupts the JSON that
// the release workflow parses to check the tarball carries the asset. Diagnostics
// go to stderr so they stay visible in a log without being mistaken for data.
console.error(
  `vendored notability.txt.gz (${bytes.length} bytes, format ${entry.format}, ` +
    `cut ${entry.cut_date}) — sha256 verified`,
);
