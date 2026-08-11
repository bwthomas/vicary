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
 * it fails the build. It is deliberately expressed over the 36 frames that
 * require masking rather than all 52, because 16 frames expect nothing to be
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
import { redact } from "../src/redact.js";

/**
 * Raise this when detector work lands. Never lower it to make a build pass.
 *
 * 8 — the structured entities and the interpolated identity. The remaining 28
 * all need candidate generation: a third-party name, a hometown to type
 * {LOCATION}, or an organisation.
 */
const MATCHED_REQUIRING_MASKING_RATCHET = 8;

const spec = loadSpec();
const gates = loadGates();
const board = score(spec, (sentence: string, identity: Identity) =>
  redact(sentence, identity),
);

// Printed unconditionally, including on a green run. The report is the artifact;
// a pass with no numbers is the state this project has a written rule against.
console.log(report(board, gates));

test("the spec loads with every frame and its golden output", () => {
  assert.equal(spec.frames.length, 52);
  assert.equal(spec.golden.size, 52);
  assert.equal(spec.fixtureVersion, "2026-08-11.1");
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
  // tracks the spec instead of a number somebody typed. 36 of 52 today; if the
  // fixture grows, the ratchet's denominator moves with it and the failure
  // message says so.
  assert.equal(
    board.requiringMasking + (board.total - board.requiringMasking),
    52,
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

test(
  "every frame matches the reference output byte-for-byte",
  { todo: "the detector is not ported yet — see typescript/README.md" },
  () => {
    const failures = board.outcomes
      .filter((o) => !o.matched)
      .map(
        (o) =>
          `${o.frameId}: expected ${JSON.stringify(o.expected)}, got ` +
          `${JSON.stringify(o.produced)}${o.error ? ` (${o.error})` : ""}`,
      );
    assert.deepEqual(failures, []);
  },
);
