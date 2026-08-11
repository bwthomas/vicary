/**
 * The asset layer, and the one claim it has to earn: the same bytes.
 *
 * "All three front doors produce byte-identical output" is impossible if they
 * read different gazetteers, so this file checks agreement against the manifest
 * the Python package ships rather than against constants written here. A
 * hand-copied expected tier count would agree with itself forever.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

import {
  SUPPORTED_FORMAT,
  loadGazetteer,
  parseAsset,
  resetGazetteerCache,
} from "../src/asset.js";
import { VERSION } from "../src/version.js";

interface Manifest {
  assets: Record<
    string,
    {
      sha256: string;
      bytes: number;
      format: number;
      tiers: Record<string, number>;
    }
  >;
}

function manifest(): Manifest["assets"][string] {
  const gazetteer = loadGazetteer();
  const raw = readFileSync(
    join(gazetteer.path, "..", "MANIFEST.json"),
    "utf8",
  );
  return (JSON.parse(raw) as Manifest).assets["notability.txt.gz"]!;
}

test("the asset loads at the format this reader supports", () => {
  const gazetteer = loadGazetteer();
  assert.equal(gazetteer.format, SUPPORTED_FORMAT);
  assert.equal(gazetteer.format, manifest().format);
});

test("the bytes read are the bytes the manifest describes", () => {
  // This is the parity claim at its root. If this digest ever differs from the
  // Python package's, nothing downstream about identical output is checkable.
  const gazetteer = loadGazetteer();
  assert.equal(gazetteer.sha256, manifest().sha256);
});

test("every tier parses to the count the manifest declares", () => {
  const gazetteer = loadGazetteer();
  const declared = manifest().tiers;
  const parsed = Object.fromEntries(
    [...gazetteer.tiers].map(([name, entries]) => [name, entries.size]),
  );
  // Compared as whole objects, not tier by tier: a loop over the manifest's
  // keys would pass while the reader invented an extra tier, and a loop over
  // the reader's keys would pass while it dropped one entirely.
  assert.deepEqual(parsed, declared);
});

test("a known entry resolves in the tier that should hold it", () => {
  const gazetteer = loadGazetteer();
  // Entries are normalised to lowercase by the builder, so lookups are on the
  // folded form. Rosa Parks is in the fixture as a KEEP span; if `full` cannot
  // answer for her, every keep frame fails for a reason that has nothing to do
  // with the detector.
  assert.ok(gazetteer.tiers.get("full")!.has("rosa parks"));
  assert.ok(gazetteer.tiers.get("settlement")!.has("akron"));
  assert.ok(gazetteer.tiers.get("given")!.has("deshawn"));
});

test("an unknown format is refused rather than partially read", () => {
  assert.throws(
    () => parseAsset("#!gazetteer 999\n#!tier full 0\n"),
    /format 999 is not/,
  );
});

test("a truncated tier is refused rather than silently smaller", () => {
  // The failure this guards is asymmetric: a short read means fewer notable
  // people, which means MORE redaction, which looks privacy-safe and passes any
  // check that only asks whether something was masked.
  assert.throws(
    () => parseAsset("#!gazetteer 5\n#!tier full 3\nabraham lincoln\n"),
    /declares 3 entries and parsed 1/,
  );
});

test("a directive the format number did not admit to is refused", () => {
  assert.throws(
    () => parseAsset("#!gazetteer 5\n#!tier full 1\nx\n#!newthing 1\n"),
    /format changed without its number changing/,
  );
});

test("an entry before any tier is an error, not an orphan", () => {
  assert.throws(
    () => parseAsset("#!gazetteer 5\nabraham lincoln\n"),
    /before any #!tier/,
  );
});

test("the cache can be reset, so a test can load a different directory", () => {
  const first = loadGazetteer();
  resetGazetteerCache();
  const second = loadGazetteer();
  assert.equal(first.sha256, second.sha256);
});

// ---------------------------------------------------------------------------
// The version, which is a cross-package claim rather than a package detail
// ---------------------------------------------------------------------------

test("the exported version matches package.json and the shared VERSION file", () => {
  // Nothing checked this, and it drifted: `version.ts` said 0.1.1 while
  // package.json, the root VERSION, the gem and the wheel all said 0.2.0 — so a
  // host reading `VERSION` from the module got a different answer than npm gave
  // it, in the same package. The parity claim is between *versions* ("one
  // detector, one number"), which makes a silent disagreement here a claim about
  // three implementations agreeing at a version that never existed.
  const pkg = JSON.parse(
    readFileSync(new URL("../../package.json", import.meta.url), "utf8"),
  );
  assert.equal(VERSION, pkg.version);
  const shared = readFileSync(
    new URL("../../../VERSION", import.meta.url),
    "utf8",
  ).trim();
  assert.equal(VERSION, shared);
});
