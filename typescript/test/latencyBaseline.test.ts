/**
 * The latency regression comparison, including every refusal to make it.
 *
 * A relative gate has two ways to be useless and only one of them is loud. It
 * can fail on differences that are not the code — which everybody notices,
 * because it red-lights a green build — or it can quietly decline to compare and
 * report that as a pass, which nobody notices until a regression ships. So the
 * cases below assert the *reason* as well as the verdict, and several prove the
 * gate still fails on an actual slowdown: a comparison that cannot fail is not a
 * gate.
 *
 * The refusals changed shape when the comparison did. They used to be about the
 * machine this run is on versus the machine a number was recorded on. They are
 * now about the paired record — is there one, is it this port's, was it taken on
 * these essays, was it taken for this commit — because both sides are now
 * measured on one machine and the machine cancels.
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
  PAIR_DOCUMENT_VERSION,
  PAIR_ENV_VAR,
  SPEC_FILENAME,
  compare,
  loadSpecDoc,
  render,
} from "../src/latencyBaseline.js";

const TOLERANCE = 8.0;
const CORPUS = "persuade-20";
const HEAD = "a".repeat(40);

interface PairOptions {
  previousMs?: number;
  currentMs?: number;
  implementation?: string;
  corpus?: string;
  head?: string;
  documentVersion?: number;
}

/** A directory holding the spec, and a pair record beside it. */
function fixture(options: PairOptions = {}): { directory: string; pairPath: string } {
  const directory = mkdtempSync(join(tmpdir(), "vicary-latency-"));
  writeFileSync(
    join(directory, SPEC_FILENAME),
    JSON.stringify({ document_version: 2, tolerance_pct: TOLERANCE }),
  );
  const pairPath = join(directory, "pair.json");
  writeFileSync(
    pairPath,
    JSON.stringify({
      document_version: options.documentVersion ?? PAIR_DOCUMENT_VERSION,
      implementation: options.implementation ?? "typescript",
      corpus: options.corpus ?? CORPUS,
      head_sha: options.head ?? HEAD,
      against: { ref: "v0.2.4", sha: "b".repeat(40) },
      previous_ms: options.previousMs ?? 10.0,
      current_ms: options.currentMs ?? 10.0,
    }),
  );
  return { directory, pairPath };
}

/** Compare 10 ms measured here against a pair record shaped by `options`. */
function compared(options: PairOptions = {}, buildingSha = "") {
  const { directory, pairPath } = fixture(options);
  return compare(10.0, CORPUS, { directory, pairPath, buildingSha });
}

// ---------------------------------------------------------------------------
// The refusals
// ---------------------------------------------------------------------------

test("no paired measurement declines", () => {
  // The ordinary laptop case, and the one that must never read as a pass.
  const { directory } = fixture();
  const previous = process.env[PAIR_ENV_VAR];
  delete process.env[PAIR_ENV_VAR];
  try {
    const c = compare(10.0, CORPUS, { directory });
    assert.equal(c.comparable, false);
    assert.equal(c.holds, false);
    assert.ok(c.reason?.includes(PAIR_ENV_VAR));
  } finally {
    if (previous !== undefined) process.env[PAIR_ENV_VAR] = previous;
  }
});

test("a missing record declines", () => {
  const { directory } = fixture();
  const c = compare(10.0, CORPUS, {
    directory,
    pairPath: join(directory, "nope.json"),
  });
  assert.equal(c.comparable, false);
  assert.ok(c.reason?.includes("does not exist"));
});

test("an unreadable record declines rather than passing", () => {
  // A broken harness and an absent one must not report the same thing.
  const { directory } = fixture();
  const pairPath = join(directory, "broken.json");
  writeFileSync(pairPath, "{not json");
  const c = compare(10.0, CORPUS, { directory, pairPath });
  assert.equal(c.comparable, false);
  assert.ok(c.reason?.includes("could not be read"));
});

test("a record this reader does not understand declines", () => {
  const c = compared({ documentVersion: 99 });
  assert.equal(c.comparable, false);
  assert.ok(c.reason?.includes("document_version 99"));
});

test("another port's record declines", () => {
  // Three ports write records side by side; reading Ruby's is a wrong answer
  // rather than a missing one — the ports are 2-3x apart in absolute cost.
  const c = compared({ implementation: "ruby" });
  assert.equal(c.comparable, false);
  assert.ok(c.reason?.includes("'ruby'"));
});

test("another corpus declines", () => {
  const c = compared({ corpus: "asap-aes-set8" });
  assert.equal(c.comparable, false);
  assert.ok(c.reason?.includes("asap-aes-set8"));
});

test("a record from another commit declines", () => {
  // A stale artifact is the failure this design invites: the record is a file,
  // and a file outlives the job that wrote it.
  const c = compared({}, "c".repeat(40));
  assert.equal(c.comparable, false);
  assert.ok(c.reason?.includes("stale"));
});

test("the commit check passes when the record is this build", () => {
  const c = compared({}, HEAD);
  assert.equal(c.comparable, true);
  assert.equal(c.holds, true);
});

test("a previous measurement of zero declines", () => {
  const c = compared({ previousMs: 0 });
  assert.equal(c.comparable, false);
  assert.ok(c.reason?.includes("not positive"));
});

// ---------------------------------------------------------------------------
// The verdicts
// ---------------------------------------------------------------------------

test("unchanged code holds", () => {
  const c = compared({ previousMs: 10.0, currentMs: 10.0 });
  assert.equal(c.comparable, true);
  assert.equal(c.holds, true);
  assert.equal(c.regressionPct, 0);
  assert.equal(c.against, "v0.2.4");
});

test("within the tolerance holds", () => {
  const c = compared({ previousMs: 10.0, currentMs: 10.7 });
  assert.equal(c.holds, true);
  assert.ok(Math.abs(c.regressionPct! - 7.0) < 1e-9);
});

test("just over the bar fails", () => {
  const c = compared({ previousMs: 10.0, currentMs: 10.81 });
  assert.equal(c.comparable, true);
  assert.equal(c.holds, false);
});

test("a real slowdown fails", () => {
  const c = compared({ previousMs: 10.0, currentMs: 13.0 });
  assert.equal(c.comparable, true);
  assert.equal(c.holds, false);
  assert.ok(Math.abs(c.regressionPct! - 30.0) < 1e-9);
});

test("getting faster is never a failure", () => {
  const c = compared({ previousMs: 10.0, currentMs: 6.0 });
  assert.equal(c.holds, true);
  assert.ok(c.regressionPct! < 0);
});

test("the verdict comes from the pair and not from this process", () => {
  // The property the whole design rests on. This process's own figure is
  // reported and never gated: here it is 10 ms against a pair measured at 3 ms
  // — the laptop-versus-runner gap that broke both earlier designs — and the
  // verdict still comes from the two numbers taken back to back on one machine.
  const c = compared({ previousMs: 3.0, currentMs: 3.1 });
  assert.equal(c.comparable, true);
  assert.equal(c.holds, true);
  assert.equal(c.measuredMs, 10.0);
  assert.ok(Math.abs(c.regressionPct! - (3.1 / 3.0 - 1) * 100) < 1e-9);
  assert.ok(render(c).includes("10.000 ms here"));
});

// ---------------------------------------------------------------------------
// The file that ships
// ---------------------------------------------------------------------------

test("the shipped spec declares a tolerance and a protocol", () => {
  // It carries no measurements on purpose, and a reader should be able to see
  // that this is deliberate rather than an empty file.
  const doc = loadSpecDoc() as Record<string, unknown> | null;
  assert.ok(doc !== null);
  assert.ok((doc!["tolerance_pct"] as number) > 0);
  assert.ok(String(doc!["protocol"]).includes("paired"));
  assert.ok(
    !("implementations" in doc!),
    "recorded per-release measurements are what the paired protocol replaced; " +
      "leaving them here would let a stale number be read as a gate",
  );
});
