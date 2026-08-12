/**
 * The gates this port measures, and the machinery that measures them.
 *
 * Five of the nine need no operator-supplied data. Their values are checked
 * against the Python gate report rather than against a hand-written
 * expectation — held-out recall 16/16, KEEP precision 21/21, round-trip 54/54,
 * unaccounted violations 0, asset entries 360,793. A port that agrees only with
 * itself proves nothing about the claim the repository makes.
 *
 * The remaining four stay NOT MEASURED and are asserted to stay that way: a gate
 * silently reduced out of the denominator is how "five of nine" becomes "all
 * green" without anybody deciding it should.
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";

import {
  censusSource,
  loadCensus,
  measureExposure,
  parseCensusSurnames,
  rate,
} from "../src/census.js";
import { loadGates, loadSpec, type Identity } from "../src/conformance.js";
import {
  type CarrierPlan,
  buildCases,
  isAsapToken,
  loadCarrierPlan,
  loadEssays,
  loadReferenceMeasurements,
  measureFromConfig,
  resolveCorpusId,
} from "../src/corpus.js";
import { load } from "../src/gazetteer.js";
import {
  ACCEPTED_VIOLATIONS,
  KNOWN_PLACEHOLDERS,
  align,
  checkFrame,
  leakProbes,
  measureGates,
  placeholderKind,
  reportGates,
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

/**
 * The same gates again, with the census requirement satisfied — or null when no
 * operator has pointed `VICARY_EVAL_CENSUS_CSV` at a copy of the file.
 *
 * Measured separately rather than folded into `report` so the assertions above
 * keep testing what they were written to test: that an *absent* requirement
 * yields NOT MEASURED. Both paths then have a test, which is the point — the
 * failure being guarded against is a gate that quietly acquires a value.
 */
const exposure =
  censusSource() === "" ? null : measureExposure(loadCensus(), load());
const censusReport =
  exposure === null
    ? null
    : measureGates(
        spec,
        gates,
        (sentence: string, identity: Identity) => redact(sentence, identity),
        { assetEntries: load().entryCount, bareSurnameExposure: rate(exposure) },
      );
const noCensus = "no VICARY_EVAL_CENSUS_CSV; see typescript/src/census.ts";

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
  assert.match(measurement("round_trip").detail, /^54\/54 /);
  assert.equal(measurement("unaccounted_violations").value, 0);
  assert.equal(measurement("asset_entries").value, 360793);
});

test("with no data supplied, all four gates needing data stay NOT MEASURED", () => {
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

test("a gate whose data is absent is never given a value", () => {
  // The dangerous failure is not "unmeasured" — it is a plausible number
  // computed from the wrong inputs and printed under the right label.
  for (const m of report.measurements) {
    if (m.gate.requires.length > 0) assert.equal(m.value, null, m.gate.id);
  }
});

// ---------------------------------------------------------------------------
// The census gate, when the operator supplies the file
// ---------------------------------------------------------------------------

test(
  "bare-surname exposure is measured and holds when the census file is supplied",
  { skip: censusReport === null ? noCensus : false },
  () => {
    const m = censusReport!.measurements.find(
      (x) => x.gate.id === "bare_surname_exposure",
    )!;
    assert.notEqual(m.value, null);
    // Reconciled against `python -m vicary.eval.census` on the same file, to
    // three decimals rather than the two the report rounds to — the gate bar is
    // 1.25 and 1.2 would sit under it whatever the third digit did.
    assert.equal(m.value!.toFixed(4), "1.1992");
    assert.equal(exposure!.surnamesScored, 162253);
    assert.equal(exposure!.surnamesMatched, 792);
    assert.equal(exposure!.bearersTotal, 265667228);
    assert.equal(exposure!.bearersExposed, 3185816);
    assert.equal(m.passed, true);
  },
);

test(
  "supplying the census file measures that gate and no other",
  { skip: censusReport === null ? noCensus : false },
  () => {
    // A requirement satisfied is not a licence for the other three: the corpus
    // gates must stay NOT MEASURED, or "six of nine" silently becomes "nine".
    const stillUnmeasured = censusReport!.measurements
      .filter((m) => m.passed === null)
      .map((m) => m.gate.id)
      .sort();
    assert.deepEqual(stillUnmeasured, [
      "held_out_recall_carrier",
      "latency_p95",
      "over_fire_prose",
    ]);
  },
);

// ---------------------------------------------------------------------------
// The corpus gates, when the operator supplies an essay corpus
// ---------------------------------------------------------------------------

// No `corpusSource()` guard: a shipped corpus needs no operator TSV, and
// `measureFromConfig` returns null for exactly the case where the data is absent.
// Guarding on the env var here is what kept this port reporting NEEDS corpus
// against a corpus sitting in the repository.
const corpus = measureFromConfig(
  spec,
  (text: string, identity: Identity) => redact(text, identity),
  spec.identity,
);
const noCorpus =
  "the resolved corpus is operator-supplied and no VICARY_EVAL_CORPUS_TSV is " +
  "set; see typescript/src/corpus.ts";

test(
  "the carrier essays are byte-identical to the reference's",
  { skip: corpus === null ? noCorpus : false },
  () => {
    // The load-bearing parity assertion. Every corpus gate is measured on this
    // text, so if it diverges from Python's the three ports are answering
    // different questions and agreeing on the numbers would prove nothing.
    // Anchored on a digest rather than on the metrics, because the metrics can
    // coincide across genuinely different inputs.
    const corpusId = resolveCorpusId();
    const plan = loadCarrierPlan(corpusId);
    const cases = buildCases(loadEssays(corpusId)!, plan, spec);
    const digest = createHash("sha256")
      .update(cases.map((c) => c.text).join(""), "utf8")
      .digest("hex");
    assert.equal(digest, loadReferenceMeasurements().carrierTextSha256);
  },
);

test(
  "the corpus gates measure what the reference measures",
  { skip: corpus === null ? noCorpus : false },
  () => {
    // Read off `conformance/measured.json`, not typed here. These were literals
    // in this file, in Ruby's gate test and in Python's — and three copies of a
    // number is not three checks of it. When the reference's figure moves,
    // Python is updated because that is where the change was made, and the other
    // two go on asserting the stale value while staying green: measuring a
    // different thing from the reference and reporting agreement.
    const reference = loadReferenceMeasurements();

    // Before comparing anything, that the two are the same question. A count
    // taken at another fixture version fails as an off-by-a-few that reads like
    // a detector regression and costs a bisect to attribute.
    assert.equal(
      reference.fixtureVersion,
      spec.fixtureVersion,
      `measured.json was measured at fixture ${reference.fixtureVersion} and ` +
        `this port is scoring against ${spec.fixtureVersion} — regenerate it ` +
        "with `just sync-conformance` rather than comparing across fixtures",
    );

    // Counts, not just percentages: 100% of a wrong denominator is still 100%,
    // and the denominator is what moves when a fixture revision adds a span.
    assert.equal(corpus!.essays, reference.essays);
    assert.equal(corpus!.recallHeldOutPassed, reference.recallHeldOutPassed);
    assert.equal(corpus!.recallHeldOutTotal, reference.recallHeldOutTotal);
    assert.equal(corpus!.recallHeldOut, reference.recallHeldOutPct);
    assert.equal(corpus!.overFireSpansTotal, reference.overFireSpansTotal);
    assert.equal(
      corpus!.overFireSpansPerEssay,
      reference.overFireSpansPerEssay,
    );
    assert.equal(corpus!.asapRewritesPerEssay, reference.asapRewritesPerEssay);
  },
);

test(
  "latency is this port's own, and is not asserted against the reference's",
  { skip: corpus === null ? noCorpus : false },
  () => {
    // The one corpus gate whose answer Python's number says nothing about.
    // Asserted against the bar alone — pinning it to a figure would make an
    // ordinary CI machine fail a correctness suite for being busy.
    assert.ok(corpus!.latencyP95Ms > 0);
    assert.ok(
      corpus!.latencyP95Ms <= 10,
      `latency p95 ${corpus!.latencyP95Ms} ms exceeds the 10 ms bar`,
    );
  },
);

test("a corpus that supplies only some of the planned essays is refused", () => {
  // Caught in review by pointing the harness at a one-essay TSV: it built zero
  // cases, and over-firing and latency then computed as 0 — which in a `<=`
  // gate is the most comfortable pass on the board. Two gates went green on no
  // data at all. A subset must be refused, not averaged.
  const plan = loadCarrierPlan();
  assert.throws(
    () => buildCases([["not-a-planned-id", "some other essay"]], plan, spec),
    /Refusing to measure a subset/,
  );
});

test("a corpus essay the plan neither carries nor names is refused", () => {
  // The subset check above compares cases built against cases planned, so a plan
  // that quietly lost ten of its twenty-five essays matches itself perfectly and
  // measures fifteen — under the same gate, at the same bar. Unreachable while a
  // plan always covered its whole corpus, and reachable the moment `unusable`
  // made a short plan legitimate. So the count reconciles against the *corpus*:
  // carried plus named must equal supplied.
  const base =
    "The dog barked. The cat ran. The bird flew. The fish swam. " +
    "The cow mooed. And then it was quiet.";
  const plan: CarrierPlan = {
    corpusId: "test",
    essaySet: "test",
    limit: 2,
    perEssay: 1,
    cases: [
      {
        essayId: "carried",
        baseSha256: createHash("sha256").update(base, "utf8").digest("hex"),
        baseChars: base.length,
        frames: [spec.frames[0]!.frameId],
        slots: [16],
      },
    ],
    unusable: [],
  };
  const essays: Array<[string, string]> = [
    ["carried", base],
    ["neither-carried-nor-named", base],
  ];

  assert.throws(() => buildCases(essays, plan, spec), /dropped silently/);

  // And naming it is what makes the same corpus measurable — otherwise the check
  // would just be an assertion that plans are never short.
  plan.unusable = [
    { essayId: "neither-carried-nor-named", reason: "declared for this test" },
  ];
  assert.equal(buildCases(essays, plan, spec).length, 1);
});

test("a corpus essay that does not match the plan is refused", () => {
  // An offset into the wrong text is not an error anything downstream notices;
  // it produces a plausible number from text nobody intended.
  const plan = loadCarrierPlan();
  const first = plan.cases[0]!;
  assert.throws(
    () => buildCases([[first.essayId, "a different essay entirely"]], plan, spec),
    /does not match the one the carrier plan was built from/,
  );
});

test("an ASAP anonymization token is told from ordinary prose", () => {
  // The over-fire metric's whole meaning rests on this split.
  for (const token of ["@PERSON1", "@LOCATION2", "@CAPS", " @ORGANIZATION3 "]) {
    assert.ok(isAsapToken(token), token);
  }
  for (const prose of ["@", "@person1", "Mr. Okonkwo", "@PERSON1 and more"]) {
    assert.ok(!isAsapToken(prose), prose);
  }
});

// ---------------------------------------------------------------------------
// The census reader's guards — these need no census file
// ---------------------------------------------------------------------------

test("a truncated census file is refused rather than scored", () => {
  // The failure mode is silent and one-directional: fewer rows is a smaller
  // denominator, which reports a more comfortable exposure than the truth.
  const short = ["name,rank,count", "SMITH,1,2442977", "JOHNSON,2,1932812"].join(
    "\n",
  );
  assert.throws(() => parseCensusSurnames(short), /only 2 rows/);
});

test("a census file with no usable header is refused", () => {
  assert.throws(
    () => parseCensusSurnames("surname,total\nSMITH,2442977"),
    /no 'name'\/'count' header/,
  );
});

test("a .zip is refused by name rather than read as text", () => {
  // Reading the archive's bytes as CSV yields zero rows, and zero rows is the
  // most comfortable exposure rate there is.
  assert.throws(
    () => loadCensus("/nonexistent/names.zip"),
    /reads the extracted \.csv only/,
  );
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

// ---------------------------------------------------------------------------
// The board
// ---------------------------------------------------------------------------

// `npm run gates` used to print node's tick list and nothing else, while Python's
// `pytest -m gates -s` printed the nine numbers. Same gates, same bars, and only
// one of the three ports would tell you what it measured — so "all three agree"
// was a claim you had to run the reference to check.
//
// Assembled from the measurements above rather than re-measuring: the corpus arm
// redacts 50 essays, and paying for that twice to print it would make the report
// expensive enough to switch off.
//
// Registered with `process.on("exit")` so it lands after the assertions. A report
// that runs first prints an empty table and passes — the same ordering
// `python/tests/conftest.py` enforces, for the same reason.
process.on("exit", () => {
  const full = measureGates(
    spec,
    gates,
    (sentence: string, identity: Identity) => redact(sentence, identity),
    {
      assetEntries: load().entryCount,
      ...(exposure === null ? {} : { bareSurnameExposure: rate(exposure) }),
      ...(corpus === null
        ? {}
        : {
            heldOutRecallCarrier: corpus.recallHeldOut,
            overFirePerEssay: corpus.overFireSpansPerEssay,
            latencyP95Ms: corpus.latencyP95Ms,
          }),
    },
  );
  process.stdout.write(
    `\ngate report — fixture ${spec.fixtureVersion}, arm ${spec.referenceArm}\n` +
      `${reportGates(full)}\n`,
  );
});
