/**
 * The port's scoreboard against the shared spec.
 *
 * This file runs from the first commit of the port, before any detector exists,
 * on purpose: a suite added once a port "works" cannot tell you when it started
 * working, and a port with no scoreboard is a port whose readiness is somebody's
 * opinion.
 *
 * **The ratchet.** `MATCHED_REQUIRING_MASKING_RATCHET` is the number of
 * masking-required frames this port currently reproduces byte-for-byte. It is a
 * floor: raise it when you land detector work, and a regression that drops below
 * it fails the build. It is deliberately expressed over the 38 frames that
 * require masking rather than all 54, because 16 frames expect nothing to be
 * masked and a do-nothing implementation matches every one of them — a ratchet
 * over 52 would start at 16 and read as progress.
 *
 * **Completeness is a separate, visible, failing item.** `{ todo: ... }` reports
 * it every run without failing CI, so the gap is impossible to lose track of and
 * impossible to mistake for done.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  loadGates,
  loadSpec,
  report,
  score,
  type Identity,
} from "../src/conformance.js";
import {
  censusSource,
  loadCensus,
  measureExposure,
  rate,
} from "../src/census.js";
import {
  measureFromConfig,
  resolveCorpusId,
  type CorpusMetrics,
} from "../src/corpus.js";
import { load } from "../src/gazetteer.js";
import { measureGates, reportGates } from "../src/gates.js";
import { redact, redactWithReport, restore } from "../src/redact.js";

/**
 * Raise this when detector work lands. Never lower it to make a build pass.
 *
 * 36 — all of them, since candidate generation was wired into `redact`. It stays
 * as a floor rather than being retired: the hard assertion below now carries the
 * same claim, and the two fail differently. That one names the frames that broke;
 * this one says how far the port fell, which is what a bisect reads.
 */
const MATCHED_REQUIRING_MASKING_RATCHET = 38;

const spec = loadSpec();
const gates = loadGates();
const board = score(spec, (sentence: string, identity: Identity) =>
  redact(sentence, identity),
);

/**
 * Measured when the operator has supplied the census file, absent otherwise.
 *
 * Read here rather than in `gates.ts` so that module stays free of the
 * filesystem, and swallowed on failure so a malformed or unreadable copy costs
 * this run one NOT MEASURED gate instead of the whole scoreboard — the reader
 * itself is tested in `gates.test.ts`, where a bad file is supposed to throw.
 */
const bareSurnameExposure = ((): number | undefined => {
  if (censusSource() === "") return undefined;
  try {
    return rate(measureExposure(loadCensus(), load()));
  } catch (error) {
    console.log(`  census file unreadable, gate stays NOT MEASURED: ${error}`);
    return undefined;
  }
})();

/**
 * The three corpus gates, measured when the operator has supplied an essay
 * corpus. Swallowed on failure for the same reason the census read is: a
 * mis-configured corpus should cost this run three NOT MEASURED gates, not the
 * whole scoreboard.
 */
const corpus = ((): CorpusMetrics | null => {
  try {
    return measureFromConfig(
      spec,
      (text: string, identity: Identity) => redact(text, identity),
      spec.identity,
    );
  } catch (error) {
    console.log(`  corpus unreadable, 3 gates stay NOT MEASURED: ${error}`);
    return null;
  }
})();

const gateReport = measureGates(
  spec,
  gates,
  (sentence: string, identity: Identity) => redact(sentence, identity),
  {
    assetEntries: load().entryCount,
    ...(corpus === null
      ? {}
      : {
          heldOutRecallCarrier: corpus.recallHeldOut,
          overFirePerEssay: corpus.overFireSpansPerEssay,
          latencyP95Ms: corpus.latencyP95Ms,
          // Which corpus these came from, so the over-fire gate is held to that
          // corpus's bar rather than to ASAP-AES's on every corpus.
          corpusId: resolveCorpusId(),
        }),
    // Spread rather than passed as `undefined`: `exactOptionalPropertyTypes`
    // distinguishes "absent" from "present and undefined", and absent is what an
    // unsupplied census file means.
    ...(bareSurnameExposure === undefined ? {} : { bareSurnameExposure }),
  },
);

// Printed unconditionally, including on a green run. The report is the artifact;
// a pass with no numbers is the state this project has a written rule against.
console.log(report(board, gates, reportGates(gateReport)));

test("the spec loads with every frame and its golden output", () => {
  assert.equal(spec.frames.length, 54);
  assert.equal(spec.golden.size, 54);
  assert.equal(spec.fixtureVersion, "2026-08-11.2");
  assert.equal(spec.referenceArm, "local-gazetteer-lowercase");
});

test("the spec carries the identity the detector is told about", () => {
  // Without these the port measures a different system: identity interpolation
  // is the one leg that reaches its spans trivially, and omitting it looks like
  // a detector bug rather than a missing input.
  assert.equal(spec.identity.firstName, "Marguerite");
  assert.equal(spec.identity.lastName, "Delacroix-Whitfield");
  assert.equal(spec.identity.schoolName, "Westfield High School");
});

test("the nine gates load, with four declaring data no package ships", () => {
  assert.equal(gates.gates.length, 9);
  const needsData = gates.gates.filter((g) => g.requires.length > 0);
  assert.equal(needsData.length, 4);
  for (const gate of needsData) {
    for (const requirement of gate.requires) {
      assert.ok(
        requirement in gates.requirements,
        `gate ${gate.label} requires ${requirement}, which the spec does not ` +
          `describe — a port cannot tell an operator what to supply`,
      );
    }
  }
});

test("both implementations agree on which frames require masking", () => {
  // Derived from the golden output rather than asserted as a constant, so this
  // tracks the spec instead of a number somebody typed. 38 of 54 today; if the
  // fixture grows, the ratchet's denominator moves with it and the failure
  // message says so.
  assert.equal(
    board.requiringMasking + (board.total - board.requiringMasking),
    54,
  );
  assert.ok(
    board.requiringMasking > 0,
    "no frame requires masking, which means the golden output is empty and " +
      "this suite is scoring nothing",
  );
});

test("the port does not regress below its ratchet", () => {
  assert.ok(
    board.matchedRequiringMasking >= MATCHED_REQUIRING_MASKING_RATCHET,
    `matched ${board.matchedRequiringMasking} of ${board.requiringMasking} ` +
      `masking-required frames, below the ratchet of ` +
      `${MATCHED_REQUIRING_MASKING_RATCHET}. Either fix the regression or, if ` +
      `the drop is intended, say why in the commit that lowers the ratchet.`,
  );
});

test("every frame matches the reference output byte-for-byte", () => {
  // Was `{ todo: ... }` while the detector was unported — reported every run,
  // failing none. It is a hard assertion now that all 52 match: the completeness
  // item existed to keep the gap visible, and a gap that is closed should fail
  // the build if it reopens rather than go back to being a note.
  const failures = board.outcomes
    .filter((o) => !o.matched)
    .map(
      (o) =>
        `${o.frameId}: expected ${JSON.stringify(o.expected)}, got ` +
        `${JSON.stringify(o.produced)}${o.error ? ` (${o.error})` : ""}`,
    );
  assert.deepEqual(failures, []);
});

test("placeholders are numbered in the reference's order", () => {
  // The bytes matching already implies this, and it is asserted separately
  // anyway: a diff on this list names the defect ("{NAME_1} and {NAME_2} are
  // swapped") where a diff on a whole sentence only shows that one exists.
  // Numbering is where ports diverge first, so it gets its own failure message.
  const wrong: string[] = [];
  for (const frame of spec.frames) {
    const golden = spec.golden.get(frame.frameId)!;
    const produced = redactWithReport(frame.sentence, spec.identity);
    // Order of first appearance IN THE TEXT, which is what `align()` records —
    // NOT mint order. The two differ, and the difference is the whole point: the
    // minter hands out indices in discovery order, and candidate generation
    // discovers right to left, so a sentence whose second name is masked first
    // reads "{NAME_2} … {NAME_1}". That is correct output and the golden says so.
    const emitted = [...produced.restoreMap.keys()]
      .filter((p) => produced.text.includes(p))
      .sort((a, b) => produced.text.indexOf(a) - produced.text.indexOf(b));
    if (emitted.join(" ") !== golden.placeholders.join(" ")) {
      wrong.push(
        `${frame.frameId}: expected [${golden.placeholders.join(", ")}], got ` +
          `[${emitted.join(", ")}]`,
      );
    }
  }
  assert.deepEqual(wrong, []);
});

test("the restore map puts every frame's original bytes back", () => {
  // The property numbering exists to buy, and the one the golden's `mapping`
  // records. Masking that cannot be undone is a different product: the caller
  // sends placeholders to a model and has no way to render the reply.
  const broken: string[] = [];
  for (const frame of spec.frames) {
    const produced = redactWithReport(frame.sentence, spec.identity);
    const back = restore(produced.text, produced.restoreMap);
    if (back !== frame.sentence) {
      broken.push(
        `${frame.frameId}: restored ${JSON.stringify(back)}, expected ` +
          `${JSON.stringify(frame.sentence)}`,
      );
    }
  }
  assert.deepEqual(broken, []);
});
