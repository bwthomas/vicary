// Vendor the shared asset payload into this package.
//
// The asset is the product; the language is the wrapper. `notability.txt.gz` is
// ~2.1 MB of folded Wikidata, Census and SSA evidence with a format number and a
// sha256 manifest, `stop_words.txt` is the 421-word stoplist that decides what
// becomes a name candidate at all, and every front door must load THE SAME BYTES —
// a port with its own gazetteer or its own stoplist is a second detector wearing
// the first one's name.
//
// So this copies rather than rebuilds, from the repository's `asset/`, which is not
// inside any of the three packages. That directory is the build mechanism's output
// and no front door's property; see `asset/README.md`. Vendoring rather than
// fetching at install time is deliberate: "no network, no per-request cost" is the
// product claim, and a build-time fetch puts a fetch back in the story.
//
// The copy is .gitignore'd. It is a build input reproduced from a tracked file,
// and a second tracked copy is a second thing to bump per asset cut — which is
// exactly how two front doors end up shipping different gazetteers.

import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const packageRoot = resolve(here, "..");
const repoRoot = resolve(packageRoot, "..");
const builtDir = join(repoRoot, "asset", "data");
const lexiconDir = join(repoRoot, "asset", "lexicon");
const target = join(packageRoot, "assets");

const MANIFEST = "MANIFEST.json";

for (const dir of [builtDir, lexiconDir]) {
  if (!existsSync(dir)) {
    console.error(
      `no asset source at ${dir}\n` +
        `This script vendors from the monorepo. Outside a checkout there is nothing ` +
        `to vendor from, and a published package should already carry assets/.`,
    );
    process.exit(2);
  }
}

// `[sourceDir, filename]`, mirroring `asset/vicary_build/vendor.py`. Built
// artifacts come from `asset/data/`; authored word lists from `asset/lexicon/`,
// where they are checksummed, rather than from a staged duplicate.
const payload = [
  [builtDir, "notability.txt.gz"],
  [builtDir, MANIFEST],
  ...readdirSync(lexiconDir)
    .filter((name) => name.endsWith(".txt"))
    .sort()
    .map((name) => [lexiconDir, basename(name)]),
];

mkdirSync(target, { recursive: true });
for (const [dir, name] of payload) {
  copyFileSync(join(dir, name), join(target, name));
}

const described = JSON.parse(readFileSync(join(target, MANIFEST), "utf8")).assets;

// Every manifest entry must have been vendored, and nothing else. Adding an asset
// without updating this list would otherwise ship a package whose manifest
// describes a file it does not carry — which fails at load time for a user, not at
// build time for us.
const vendored = payload.map(([, name]) => name).filter((name) => name !== MANIFEST);
const describedNames = Object.keys(described);
const missing = describedNames.filter((name) => !vendored.includes(name)).sort();
const extra = vendored.filter((name) => !describedNames.includes(name)).sort();
if (missing.length || extra.length) {
  console.error(
    `vendored payload does not match the manifest:\n` +
      `  described but not vendored: ${missing.length ? missing.join(", ") : "none"}\n` +
      `  vendored but not described: ${extra.length ? extra.join(", ") : "none"}`,
  );
  process.exit(1);
}

// Verify what landed, not what was copied. `copyFileSync` returning without
// throwing says the call succeeded; it does not say the bytes on disk are the bytes
// the manifest describes, and a truncated or half-written asset loads as a SMALLER
// gazetteer — which redacts more, looks privacy-safe, and is invisible to every
// test that only checks output was masked. The same argument runs the other way for
// the stoplist: a short read there makes the redactor MORE aggressive.
for (const name of describedNames.sort()) {
  const entry = described[name];
  const bytes = readFileSync(join(target, name));
  const digest = createHash("sha256").update(bytes).digest("hex");

  if (bytes.length !== entry.bytes) {
    console.error(`vendored ${name} is ${bytes.length} bytes, manifest says ${entry.bytes}`);
    process.exit(1);
  }
  if (digest !== entry.sha256) {
    console.error(
      `vendored ${name} sha256 ${digest} does not match manifest ${entry.sha256}`,
    );
    process.exit(1);
  }

  // stderr, not stdout. This runs as `prepack`, and `npm pack --json` writes its
  // manifest to stdout — a confirmation line printed there corrupts the JSON that
  // the release workflow parses to check the tarball carries the asset.
  console.error(`vendored ${name} (${bytes.length} bytes) — sha256 verified`);
}
