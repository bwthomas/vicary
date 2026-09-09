/**
 * The port's offset arithmetic, against the shared spec.
 *
 * `conformance/spans.json` is generated from the Python reference and
 * byte-compared against a fresh export by `tools/tests/test_conformance.py`, so
 * the cases here are read rather than transcribed. They are of two kinds, and
 * the second is why the file exists: most come from the fixture frames — real
 * multi-pass masker output, mixed entity types, length deltas of both signs —
 * and the rest are hand-built degenerates no essay produces, including the two
 * where the contract is to *refuse*.
 *
 * **What a failure here means, and no other suite would say.** A one-character
 * disagreement with the reference is a highlight landing on the wrong word in a
 * student's essay. `conformance.test.ts` compares masked bytes and would be
 * green; `redact.test.ts` compares the restore map and would be green. Both are
 * about WHAT is masked and what it is called; this is the only place that asks
 * where it came from.
 *
 * **And this is the port where that can go wrong for a reason the other two
 * cannot have.** JavaScript offsets count UTF-16 code units; Python and Ruby
 * count characters. A transliterated implementation is exactly right on ASCII
 * and one-per-astral-character wrong above the BMP, which is why the spec
 * carries astral cases and why they are asserted here by name.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { loadSpans } from "../src/conformance.js";
import {
  deriveSpans,
  originalText,
  spanDelta,
  toOriginal,
  toRedacted,
} from "../src/spans.js";

const SPEC = loadSpans();
const CASES = SPEC.cases;

function mapOf(record: Record<string, string>): Map<string, string> {
  return new Map(Object.entries(record));
}

test("the spec is not empty", () => {
  // The one assertion that catches a spec this suite could otherwise pass
  // vacuously: an empty `cases` array would make every loop below a no-op and
  // print the same green as full agreement.
  assert.ok(
    CASES.length >= 20,
    `spans.json shrank — ${CASES.length} cases is fewer than the edge table ` +
      `alone, so frames stopped contributing`,
  );
});

test("derived spans match the reference", () => {
  for (const kase of CASES) {
    const actual = deriveSpans(kase.masked, mapOf(kase.restore_map)).map(
      (span) => ({
        orig_start: span.origStart,
        orig_end: span.origEnd,
        new_start: span.newStart,
        new_end: span.newEnd,
      }),
    );
    assert.deepEqual(actual, kase.spans.map((s) => ({ ...s })), kase.case_id);
  }
});

test("toOriginal matches the reference at every probe", () => {
  for (const kase of CASES) {
    const spans = deriveSpans(kase.masked, mapOf(kase.restore_map));
    for (const [offset, expected] of kase.to_original) {
      assert.equal(
        toOriginal(offset, spans),
        expected,
        `${kase.case_id} toOriginal(${offset})`,
      );
    }
  }
});

test("toRedacted matches the reference at every probe", () => {
  for (const kase of CASES) {
    const spans = deriveSpans(kase.masked, mapOf(kase.restore_map));
    for (const [offset, expected] of kase.to_redacted) {
      assert.equal(
        toRedacted(offset, spans),
        expected,
        `${kase.case_id} toRedacted(${offset})`,
      );
    }
  }
});

test("reconstruction matches the reference", () => {
  for (const kase of CASES) {
    assert.equal(
      originalText(kase.masked, mapOf(kase.restore_map)),
      kase.original,
      kase.case_id,
    );
  }
});

// ---------------------------------------------------------------------------
// Behaviour named directly, so a failure says which rule broke rather than
// which case differs. Same division of labour as primitives vs candidates.
// ---------------------------------------------------------------------------

test("an absent map yields no spans", () => {
  // Not "nothing was masked" — the text plainly contains a placeholder. The
  // Guardrail arm is exactly this state: masked bytes back from a service, no
  // map, so nothing can be placed and saying so is the whole contract.
  assert.deepEqual(deriveSpans("I sat next to {NAME_1}.", new Map()), []);
  assert.deepEqual(deriveSpans("I sat next to {NAME_1}.", undefined), []);
});

test("a partial map is refused rather than half answered", () => {
  assert.deepEqual(
    deriveSpans("{NAME_1} and {NAME_2} left.", mapOf({ "{NAME_1}": "Marguerite" })),
    [],
    "a map covering one of two placeholders must yield no spans: a caller " +
      "can handle none and cannot detect a wrong offset",
  );
});

test("a repeated placeholder yields one span per occurrence", () => {
  // Keyed on occurrences in the text, not on map entries. One entry, two
  // spans, and the second's original offset depends on the first's delta.
  const spans = deriveSpans("{NAME_1} saw {NAME_1}.", mapOf({ "{NAME_1}": "Deshawn" }));
  assert.equal(spans.length, 2);
  assert.equal(spans[1]?.newStart, 13);
  assert.equal(spans[1]?.origStart, 12);
});

test("delta signs both ways", () => {
  assert.equal(
    spanDelta(deriveSpans("{NAME_1} won.", mapOf({ "{NAME_1}": "Bo" }))[0]!),
    6,
  );
  assert.equal(
    spanDelta(
      deriveSpans("{NAME_1} won.", mapOf({ "{NAME_1}": "Bartholomew Okonkwo" }))[0]!,
    ),
    -11,
  );
});

test("an offset inside a placeholder collapses to the span start", () => {
  const spans = deriveSpans("{NAME_1} won.", mapOf({ "{NAME_1}": "Bo" }));
  for (let inside = 0; inside <= 7; inside += 1) {
    assert.equal(toOriginal(inside, spans), 0, `offset ${inside} is inside`);
  }
  assert.equal(toOriginal(8, spans), 2);
});

test("offsets are code points, not UTF-16 code units", () => {
  // The divergence this port can have and the other two cannot. An astral
  // character is one character to Python and Ruby and two units to a
  // JavaScript `.length` or a regex `index`, so a transliterated derive puts
  // every later span two ahead of the reference. Asserted directly as well as
  // through the spec, because the spec's astral coverage is a handful of cases
  // and this names the rule.
  const spans = deriveSpans("🎇 {NAME_1} won.", mapOf({ "{NAME_1}": "Bo" }));
  assert.equal(spans.length, 1);
  assert.equal(spans[0]?.newStart, 2, "one emoji is ONE code point");
  assert.equal(spans[0]?.newEnd, 10);
  assert.equal(spans[0]?.origStart, 2);
  assert.equal(toOriginal(10, spans), 4);
  assert.equal(originalText("🎇 {NAME_1} won.", mapOf({ "{NAME_1}": "Bo" })), "🎇 Bo won.");
});

test("the translations are inverse at span boundaries", () => {
  for (const kase of CASES) {
    const spans = deriveSpans(kase.masked, mapOf(kase.restore_map));
    for (const span of spans) {
      assert.equal(
        toRedacted(span.origStart, spans),
        span.newStart,
        `${kase.case_id} start round trip`,
      );
      assert.equal(
        toRedacted(span.origEnd, spans),
        span.newEnd,
        `${kase.case_id} end round trip`,
      );
    }
  }
});
