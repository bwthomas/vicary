/**
 * The gates this port measures, and the machinery that measures them.
 *
 * Five of the nine need no operator-supplied data. Their values are checked
 * against the Python gate report rather than against a hand-written
 * expectation — held-out recall 16/16, KEEP precision 21/21, round-trip 52/52,
 * unaccounted violations 0, asset entries 360,793. A port that agrees only with
 * itself proves nothing about the claim the repository makes.
 *
 * The remaining four stay NOT MEASURED and are asserted to stay that way: a gate
 * silently reduced out of the denominator is how "five of nine" becomes "all
 * green" without anybody deciding it should.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { loadGates, loadSpec, type Identity } from "../src/conformance.js";
import { load } from "../src/gazetteer.js";
import {
  ACCEPTED_VIOLATIONS,
  KNOWN_PLACEHOLDERS,
  align,
  checkFrame,
  leakProbes,
  measureGates,
  placeholderKind,
  restoreByToken,
  roundTrips,
  violationKey,
} from "../src/gates.js";
import { redact } from "../src/redact.js";

const spec = loadSpec();
const gates = loadGates();
const report = measureGates(
  spec,
  gates,
  (sentence: string, identity: Identity) => redact(sentence, identity),
  { assetEntries: load().entryCount },
);

const measurement = (id: string) =>
  report.measurements.find((m) => m.gate.id === id)!;

// ---------------------------------------------------------------------------
// The gates
// ---------------------------------------------------------------------------

test("every gate needing no operator data is measured and holds", () => {
  const measured = report.measurements.filter((m) => m.passed !== null);
  assert.equal(measured.length, 5);
  const failed = measured
    .filter((m) => !m.passed)
    .map((m) => `${m.gate.label} measured ${m.value} ${m.gate.unit}: ${m.detail}`);
  assert.deepEqual(failed, []);
});

test("the measured values match the Python gate report", () => {
  // Reconciled against `pytest tests/test_gates.py -s`, not against a number
  // typed here from memory. Counts as well as percentages, because 100% of a
  // wrong denominator is still 100%.
  assert.equal(measurement("held_out_recall").value, 100);
  assert.match(measurement("held_out_recall").detail, /^16\/16 /);
  assert.equal(measurement("keep_precision").value, 100);
  assert.match(measurement("keep_precision").detail, /^21\/21 /);
  assert.equal(measurement("round_trip").value, 100);
  assert.match(measurement("round_trip").detail, /^52\/52 /);
  assert.equal(measurement("unaccounted_violations").value, 0);
  assert.equal(measurement("asset_entries").value, 360793);
});

test("the four gates needing data stay NOT MEASURED", () => {
  const unmeasured = report.measurements
    .filter((m) => m.passed === null)
    .map((m) => m.gate.id)
    .sort();
  assert.deepEqual(unmeasured, [
    "bare_surname_exposure",
    "held_out_recall_carrier",
    "latency_p95",
    "over_fire_prose",
  ]);
  // Not measurable *because the data is absent*, not because the port declined.
  for (const m of report.measurements.filter((x) => x.passed === null)) {
    assert.ok(m.gate.requires.length > 0, m.gate.id);
  }
});

test("a gate that needs data is never given a value", () => {
  // The dangerous failure is not "unmeasured" — it is a plausible number
  // computed from the wrong inputs and printed under the right label.
  for (const m of report.measurements) {
    if (m.gate.requires.length > 0) assert.equal(m.value, null, m.gate.id);
  }
});

// ---------------------------------------------------------------------------
// The accounted-for violations
// ---------------------------------------------------------------------------

test("no violation appears that is not already accounted for", () => {
  assert.deepEqual(
    report.unaccounted.map((v) => `${v.kind}:${v.detail}`),
    [],
  );
});

test("every accepted violation still actually happens", () => {
  // The load-bearing half. An exemption going stale IS the pass: without this,
  // a fixed defect leaves an entry behind that shelters the next defect of the
  // same shape. Two entries were retired from the Python list exactly this way.
  assert.deepEqual(report.missingAccepted, []);
  assert.ok(ACCEPTED_VIOLATIONS.size > 0);
});

test("the accepted violation is the documented Robinson keep", () => {
  // Named rather than counted, so a different violation cannot inherit the
  // exemption by arriving at the same total.
  assert.deepEqual([...ACCEPTED_VIOLATIONS], ["leak\u0000NAME:Robinson"]);
  assert.equal(
    report.violations.map(violationKey).filter((k) => k === "leak\u0000NAME:Robinson")
      .length,
    1,
  );
});

// ---------------------------------------------------------------------------
// Alignment
// ---------------------------------------------------------------------------

test("alignment recovers what each placeholder replaced", () => {
  const alignment = align(
    "Terrence Okonkwo and Marisol stayed.",
    "{NAME_1} and {NAME_2} stayed.",
  );
  assert.ok(alignment.ok);
  assert.deepEqual(alignment.pairs, [
    ["{NAME_1}", "Terrence Okonkwo"],
    ["{NAME_2}", "Marisol"],
  ]);
});

test("alignment is anchored, so a short chunk cannot misalign it", () => {
  // The defect this guards: a trailing "." after a masked email also occurs
  // INSIDE the address, and a greedy per-chunk scan collapses the recovered
  // region to one character. Anchoring the whole reconstruction rejects that.
  const alignment = align(
    "Write to a.b@example.org.",
    "Write to {EMAIL_1}.",
  );
  assert.ok(alignment.ok);
  assert.deepEqual(alignment.pairs, [["{EMAIL_1}", "a.b@example.org"]]);
});

test("alignment refuses text that was rewritten rather than replaced", () => {
  const alignment = align("She stayed late.", "He stayed late.");
  assert.equal(alignment.ok, false);
  assert.match(alignment.reason, /no placeholder emitted/);

  const rewritten = align("She stayed late.", "{NAME_1} departed late.");
  assert.equal(rewritten.ok, false);
  assert.match(rewritten.reason, /rewritten, reordered or dropped/);
});

test("unmasked text aligns with no pairs", () => {
  const alignment = align("Nothing to mask here.", "Nothing to mask here.");
  assert.ok(alignment.ok);
  assert.deepEqual(alignment.pairs, []);
});

test("a literal with regex metacharacters does not break alignment", () => {
  // The original is student data; a surname like "O'Brien (Jr.)" must not
  // compile as a group when it is escaped into the reconstruction pattern.
  const alignment = align("O'Brien (Jr.) was here.", "{NAME_1} was here.");
  assert.ok(alignment.ok);
  assert.deepEqual(alignment.pairs, [["{NAME_1}", "O'Brien (Jr.)"]]);
});

// ---------------------------------------------------------------------------
// Invariants
// ---------------------------------------------------------------------------

test("a partial leak is caught even though the whole literal is gone", () => {
  // Worse than a miss, because it LOOKS redacted: "{NAME_1} Okonkwo" reads as a
  // working redactor in every summary statistic while publishing the surname,
  // and recall — which tests for the whole literal — scores it as a pass.
  const frame = {
    frameId: "probe",
    group: "probe",
    sentence: "Terrence Okonkwo sat behind me.",
    heldOut: false,
    promptContext: "",
    note: "",
    spans: [
      {
        entity: "NAME",
        literal: "Terrence Okonkwo",
        verdict: "redact",
        expectCount: null,
        expect: null,
        keptBy: "notability",
        redactedBy: "absence",
        note: "",
      },
    ],
  };
  const violations = checkFrame(frame, "{NAME_1} Okonkwo sat behind me.");
  assert.deepEqual(
    violations.map((v) => v.kind),
    ["partial-leak"],
  );
  assert.equal(checkFrame(frame, "{NAME_1} sat behind me.").length, 0);
});

test("a span masked as the wrong entity is caught", () => {
  // No fixture frame produces a `wrong-type` violation, so nothing else here
  // exercises this arm: deleting the check outright left the whole suite green.
  // A hometown typed {NAME} rather than {LOCATION} is masked either way, so it
  // is invisible to recall — but outbound it is what the student reads.
  const frame = {
    frameId: "probe",
    group: "probe",
    sentence: "We drove from Akron that morning.",
    heldOut: false,
    promptContext: "",
    note: "",
    spans: [
      {
        entity: "LOCATION",
        literal: "Akron",
        verdict: "redact",
        expectCount: null,
        expect: "{LOCATION}",
        keptBy: "notability",
        redactedBy: "absence",
        note: "",
      },
    ],
  };
  assert.deepEqual(
    checkFrame(frame, "We drove from {NAME_1} that morning.").map((v) => v.kind),
    ["wrong-type"],
  );
  // The correctly-typed mask is NOT a violation. Asserted because the first
  // version of this check re-braced `expect` and made every correct span a
  // `wrong-type` — 41 of them, each reading "expected {NAME} got {NAME}".
  assert.deepEqual(checkFrame(frame, "We drove from {LOCATION_1} that morning."), []);
});

test("a placeholder nobody emits is caught", () => {
  // A truncated or nested placeholder is how a masking bug presents, and it
  // reads as ordinary prose to a downstream stage.
  const frame = {
    frameId: "probe",
    group: "probe",
    sentence: "Akron is where we lived.",
    heldOut: false,
    promptContext: "",
    note: "",
    spans: [],
  };
  assert.deepEqual(
    checkFrame(frame, "{PERSON_1} is where we lived.").map((v) => v.kind),
    ["unknown-placeholder"],
  );
});

test("a destroyed KEEP span is caught", () => {
  // Recall alone rewards a redactor that masks everything; this is the invariant
  // that stops that being a clean run.
  const frame = {
    frameId: "probe",
    group: "probe",
    sentence: "I wrote about Jackie Robinson for class.",
    heldOut: false,
    promptContext: "",
    note: "",
    spans: [
      {
        entity: "NAME",
        literal: "Jackie Robinson",
        verdict: "keep",
        expectCount: null,
        expect: null,
        keptBy: "notability",
        redactedBy: "absence",
        note: "",
      },
    ],
  };
  assert.deepEqual(
    checkFrame(frame, "I wrote about {NAME_1} for class.").map((v) => v.kind),
    ["keep-destroyed"],
  );
  assert.deepEqual(checkFrame(frame, frame.sentence), []);
});

test("one placeholder standing for two originals is caught", () => {
  // `not-restorable` — the deficit numbering fixes. Unnumbered output produced
  // 37 of these across 25 injected essays.
  const frame = {
    frameId: "probe",
    group: "probe",
    sentence: "Terrence and Marisol stayed.",
    heldOut: false,
    promptContext: "",
    note: "",
    spans: [],
  };
  assert.deepEqual(
    checkFrame(frame, "{NAME} and {NAME} stayed.").map((v) => v.kind),
    ["not-restorable"],
  );
});

test("weak tokens do not count as a partial leak", () => {
  // "van", "de", "the" surviving proves nothing — they are not the name.
  assert.deepEqual(
    leakProbes({
      entity: "NAME",
      literal: "Vincent van Gogh",
      verdict: "redact",
      expectCount: null,
      expect: null,
      keptBy: "notability",
      redactedBy: "absence",
      note: "",
    }),
    ["Vincent", "Gogh"],
  );
});

test("a non-name entity has no leak probes", () => {
  // Half a phone number is not an identifying fragment the way half a name is.
  assert.deepEqual(
    leakProbes({
      entity: "PHONE",
      literal: "(330) 555-0148",
      verdict: "redact",
      expectCount: null,
      expect: null,
      keptBy: "notability",
      redactedBy: "absence",
      note: "",
    }),
    [],
  );
});

// ---------------------------------------------------------------------------
// Placeholders and round-trip
// ---------------------------------------------------------------------------

test("the kind is separable from the index", () => {
  assert.equal(placeholderKind("{NAME_3}"), "{NAME}");
  assert.equal(placeholderKind("{NAME}"), "{NAME}");
  assert.equal(placeholderKind("{ZIP_CODE_2}"), "{ZIP_CODE}");
  // The one that would break a naive strip-after-underscore rule.
  assert.equal(
    placeholderKind("{CREDIT_DEBIT_CARD_NUMBER_1}"),
    "{CREDIT_DEBIT_CARD_NUMBER}",
  );
  assert.ok(KNOWN_PLACEHOLDERS.has(placeholderKind("{LOCATION_9}")));
});

test("an unnumbered document does not round-trip", () => {
  // The measurement numbering exists to answer, not an opinion about it: one
  // token standing for two people cannot be put back by any map keyed on it.
  const frame = {
    frameId: "probe",
    group: "probe",
    sentence: "Terrence and Marisol stayed.",
    heldOut: false,
    promptContext: "",
    note: "",
    spans: [],
  };
  assert.equal(roundTrips(frame, "{NAME_1} and {NAME_2} stayed."), true);
  assert.equal(roundTrips(frame, "{NAME} and {NAME} stayed."), false);
});

test("restore by token is keyed on what a consumer actually sees", () => {
  // Distinct from minter.restore, which is handed the map the masker built. This
  // one has only the echoed token, which is the situation a downstream stage is
  // in.
  assert.equal(
    restoreByToken(
      "{NAME_1} and {NAME_2} stayed.",
      new Map([
        ["{NAME_1}", "Terrence"],
        ["{NAME_2}", "Marisol"],
      ]),
    ),
    "Terrence and Marisol stayed.",
  );
  // An unmapped token is left alone rather than dropped — losing it would
  // silently shorten the text and read as a successful restore.
  assert.equal(restoreByToken("{NAME_9} stayed.", new Map()), "{NAME_9} stayed.");
});
