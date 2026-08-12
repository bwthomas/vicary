#!/usr/bin/env node
// What `npm pack` would actually put in the tarball, and whether the asset is in it.
//
// A package that ships without `assets/notability.txt.gz` installs cleanly, imports
// cleanly, and redacts every public figure in every essay — privacy-safe,
// product-hostile, and invisible to every other check in the release. So the
// tarball's file list is asserted before publish.
//
// **Why this is a script and not eight lines of `node -e` in the workflow.** It
// was: an inline heredoc reading `raw[0].files`. `npm pack --json` changed shape
// in npm 12 — an ARRAY of one entry up to npm 11, an OBJECT KEYED BY PACKAGE NAME
// from 12 on — so `[0].files` became `undefined` and the step died with "Cannot
// read properties of undefined", which reads like a broken tarball and was a
// broken reader. Inline in a workflow, the only way to exercise either shape was
// to cut a release against that npm version.
//
// The distinction the whole check turns on: **"no files" and "cannot see the
// files" must not reach the same conclusion** when the conclusion is whether the
// gazetteer shipped. An unrecognised shape throws; an empty list is a legitimate
// answer that fails the asset check on its own terms.
//
// The counterpart of the packing half of `ruby/test/release_test.rb`, and
// `typescript/test/packaging.test.ts` drives every branch without running npm.
//
// Usage:
//   npm pack --dry-run --json > /tmp/pack.json && node scripts/pack-contents.mjs /tmp/pack.json

import { readFileSync } from "node:fs";

/** Files that must be in the tarball or the package cannot redact. */
export const REQUIRED_ASSET_FILES = [
  "assets/notability.txt.gz",
  "assets/MANIFEST.json",
  "assets/stop_words.txt",
];

/**
 * `JSON.stringify` for an error message, for any input.
 *
 * It returns `undefined` — the value, not the string — for `undefined` and for a
 * bare function, so calling `.slice()` on the result throws a TypeError from
 * inside the error path. That is exactly the failure this module exists to
 * prevent, one level up: the reader dying with "Cannot read properties of
 * undefined" instead of naming the shape it could not read.
 *
 * @param {unknown} value
 */
function describe(value) {
  return String(JSON.stringify(value)).slice(0, 400);
}

/**
 * The packed file paths, from either shape `npm pack --json` emits.
 *
 * Throws on a shape this does not know, rather than returning `[]`. Returning an
 * empty list would let an unreadable payload fail the asset check with the same
 * message a genuinely empty tarball produces, and those need different fixes.
 *
 * @param {unknown} raw parsed `npm pack --json` output
 * @returns {string[]} tarball-relative paths
 */
export function packedFiles(raw) {
  const entry = Array.isArray(raw)
    ? raw[0]
    : raw && typeof raw === "object"
      ? Object.values(raw)[0]
      : undefined;

  if (!entry || typeof entry !== "object" || !Array.isArray(entry.files)) {
    throw new Error(
      "npm pack --json gave a shape this check does not know: " + describe(raw),
    );
  }

  return entry.files.map((file) => {
    if (!file || typeof file !== "object" || typeof file.path !== "string") {
      throw new Error(
        "npm pack --json listed a file entry with no string path: " + describe(file),
      );
    }
    return file.path;
  });
}

/**
 * Which required asset files are absent. Empty means the tarball is publishable.
 *
 * @param {readonly string[]} files
 * @returns {string[]}
 */
export function missingAssets(files) {
  const present = new Set(files);
  return REQUIRED_ASSET_FILES.filter((need) => !present.has(need));
}

function main(argv, out = console) {
  const path = argv[0];
  if (!path) {
    out.error("usage: pack-contents.mjs PACK_JSON");
    return 2;
  }

  let files;
  try {
    files = packedFiles(JSON.parse(readFileSync(path, "utf8")));
  } catch (error) {
    out.error(String(error instanceof Error ? error.message : error));
    return 1;
  }

  const missing = missingAssets(files);
  if (missing.length > 0) {
    for (const need of missing) out.error(`tarball is missing ${need}`);
    return 1;
  }

  out.log(`tarball carries the gazetteer asset (${files.length} files)`);
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main(process.argv.slice(2)));
}
