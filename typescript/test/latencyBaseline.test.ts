/**
 * The latency regression comparison, including every refusal to make it.
 *
 * A relative gate has two ways to be useless and only one of them is loud. It
 * can fail on hardware differences, which everybody notices; or it can quietly
 * decline to compare and report that as a pass, which nobody notices until a
 * regression ships. So the cases below assert the *reason* as well as the
 * verdict, and the last few prove the gate still fails on an actual slowdown —
 * a comparison that cannot fail is not a gate.
 *
 * This port's own suite. It checks TypeScript's implementation of the
 * comparison, not Python's answer about it.
 */
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
  BASELINE_FILENAME,
  compare,
  loadBaseline,
} from "../src/latencyBaseline.js";

const TOLERANCE = 8.0;
const PROFILE = "github-ubuntu-latest";
const CORPUS = "persuade-20";
const LANG = "22";

function writeBaseline(
  pooledMedianMs: number | null,
  { lang = LANG, corpus = CORPUS }: { lang?: string; corpus?: string } = {},
): string {
  const dir = mkdtempSync(join(tmpdir(), "vicary-baseline-"));
  writeFileSync(
    join(dir, BASELINE_FILENAME),
    JSON.stringify({
      document_version: 1,
      tolerance_pct: TOLERANCE,
      profile: { id: PROFILE, language_versions: { typescript: lang } },
      corpus,
      implementations: { typescript: { pooled_median_ms: pooledMedianMs } },
    }),
  );
  return dir;
}

const onProfile = {
  profileEnv: PROFILE,
  observedLanguageVersion: LANG,
};

test("a checkout with no baseline file declines", () => {
  const dir = mkdtempSync(join(tmpdir(), "vicary-empty-"));
  const c = compare(10.0, CORPUS, { directory: dir, ...onProfile });
  assert.equal(c.comparable, false);
  assert.equal(c.holds, false);
  assert.match(c.reason!, new RegExp(BASELINE_FILENAME));
});

test("an unclaimed machine declines", () => {
  // The common case: a laptop, which has no business comparing itself against
  // a number recorded on a CI runner.
  const c = compare(10.0, CORPUS, {
    directory: writeBaseline(10.0),
    profileEnv: "",
    observedLanguageVersion: LANG,
  });
  assert.equal(c.comparable, false);
  assert.match(c.reason!, /VICARY_LATENCY_PROFILE/);
});

test("a different profile declines", () => {
  const c = compare(10.0, CORPUS, {
    directory: writeBaseline(10.0),
    profileEnv: "someones-laptop",
    observedLanguageVersion: LANG,
  });
  assert.equal(c.comparable, false);
  assert.match(c.reason!, /someones-laptop/);
});

test("a different runtime major declines", () => {
  // Node 20 and 22 differ by more than the bar on this workload, so a baseline
  // recorded on one says nothing about the other.
  const c = compare(10.0, CORPUS, {
    directory: writeBaseline(10.0),
    profileEnv: PROFILE,
    observedLanguageVersion: "20",
  });
  assert.equal(c.comparable, false);
  assert.match(c.reason!, /20/);
});

test("a different corpus declines", () => {
  const c = compare(10.0, "asap-aes-set8", {
    directory: writeBaseline(10.0),
    ...onProfile,
  });
  assert.equal(c.comparable, false);
  assert.match(c.reason!, /asap-aes-set8/);
});

test("an unrecorded baseline declines rather than passes", () => {
  // Null is not zero and not a free pass.
  const c = compare(10.0, CORPUS, {
    directory: writeBaseline(null),
    ...onProfile,
  });
  assert.equal(c.comparable, false);
  assert.equal(c.holds, false);
});

test("unchanged code holds", () => {
  const c = compare(10.0, CORPUS, { directory: writeBaseline(10.0), ...onProfile });
  assert.ok(c.comparable && c.holds);
  assert.ok(Math.abs(c.regressionPct!) < 1e-9);
});

test("within the tolerance holds", () => {
  const c = compare(10.7, CORPUS, { directory: writeBaseline(10.0), ...onProfile });
  assert.ok(c.comparable && c.holds);
  assert.ok(Math.abs(c.regressionPct! - 7.0) < 1e-9);
});

test("a real slowdown fails", () => {
  // The negative control. If this ever passes, every case above is decoration.
  const c = compare(12.0, CORPUS, { directory: writeBaseline(10.0), ...onProfile });
  assert.ok(c.comparable);
  assert.ok(Math.abs(c.regressionPct! - 20.0) < 1e-9);
  assert.equal(c.holds, false);
});

test("just over the bar fails", () => {
  const c = compare(10.81, CORPUS, { directory: writeBaseline(10.0), ...onProfile });
  assert.ok(c.comparable);
  assert.equal(c.holds, false);
  assert.ok(c.regressionPct! > TOLERANCE);
});

test("getting faster is never a failure", () => {
  const c = compare(5.0, CORPUS, { directory: writeBaseline(10.0), ...onProfile });
  assert.ok(c.comparable && c.holds);
  assert.ok(Math.abs(c.regressionPct! + 50.0) < 1e-9);
});

test("the shipped baseline file parses and declares its profile", () => {
  // The real file, not a fixture — a malformed one would make every port
  // decline to compare and read as eight quiet passes.
  const doc = loadBaseline();
  assert.ok(doc !== null, "conformance/latency_baseline.json is missing");
  assert.equal(doc!.tolerance_pct, TOLERANCE);
  assert.ok(doc!.profile?.id);
  assert.deepEqual(Object.keys(doc!.implementations ?? {}).sort(), [
    "python",
    "ruby",
    "typescript",
  ]);
  for (const impl of ["python", "ruby", "typescript"]) {
    assert.ok(
      "pooled_median_ms" in (doc!.implementations?.[impl] ?? {}),
      impl,
    );
    assert.ok(doc!.profile?.language_versions?.[impl], impl);
  }
});
