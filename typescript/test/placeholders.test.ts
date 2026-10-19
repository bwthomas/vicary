/**
 * The port's placeholder guard, against the shared spec.
 *
 * `conformance/placeholders.json` is generated from the Python reference and
 * byte-compared against a fresh export by `tools/tests/test_conformance.py`, so
 * the cases are read rather than transcribed. The pattern's SOURCE is compared
 * first: all three ports compile the same bytes, and a port whose pattern
 * drifted fails there, naming the cause, before it fails on a case.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { loadPlaceholders } from "../src/conformance.js";
import {
  GENERIC_STAND_INS,
  OTHER_KIND,
  PLACEHOLDER_KINDS,
  PLACEHOLDER_LIKE,
  PlaceholderLeakError,
  SENTENCE_END,
  assertNoPlaceholders,
  findPlaceholders,
  hasPlaceholders,
  placeholderKind,
  scrubPlaceholders,
} from "../src/placeholders.js";

const SPEC = loadPlaceholders();
const CASES = SPEC.cases;

test("the spec is not empty", () => {
  assert.ok(CASES.length >= 30, `placeholders.json shrank to ${CASES.length}`);
  assert.ok(CASES.some((c) => c.leaks));
  assert.ok(CASES.some((c) => !c.leaks));
});

test("the pattern source is the reference's, byte for byte", () => {
  assert.equal(PLACEHOLDER_LIKE.source, SPEC.pattern);
  assert.equal(SENTENCE_END.source, SPEC.sentence_end);
  assert.deepEqual([...PLACEHOLDER_KINDS], SPEC.kinds);
  assert.equal(OTHER_KIND, SPEC.other_kind);
  assert.deepEqual({ ...GENERIC_STAND_INS }, SPEC.generic_stand_ins);
});

test("matches, kinds and code-point offsets match the reference", () => {
  for (const kase of CASES) {
    assert.deepEqual(
      findPlaceholders(kase.text).map((m) => ({ ...m })),
      kase.matches.map((m) => ({ ...m })),
      kase.case_id,
    );
    assert.equal(hasPlaceholders(kase.text), kase.leaks, kase.case_id);
  }
});

test("both scrubs match the reference", () => {
  for (const kase of CASES) {
    assert.deepEqual(
      { ...scrubPlaceholders(kase.text) },
      kase.scrub_prose,
      `${kase.case_id} (prose)`,
    );
    assert.deepEqual(
      { ...scrubPlaceholders(kase.text, { quote: true }) },
      kase.scrub_quote,
      `${kase.case_id} (quote)`,
    );
  }
});

test("every kind vicary can mint is caught in its own grammar", () => {
  for (const kind of PLACEHOLDER_KINDS) {
    for (const token of [`{${kind}}`, `{${kind}_1}`, `{${kind}_42}`]) {
      assert.deepEqual(
        findPlaceholders(`a ${token} b`).map((m) => m.text),
        [token],
      );
      assert.equal(placeholderKind(token), kind);
    }
  }
});

test("the error names kinds and count, never the text", () => {
  const text = "Priya told {NAME_1} at [EMAIL] about {JOHN} and {NAME_2}.";
  assert.throws(
    () => assertNoPlaceholders(text),
    (error: unknown) => {
      assert.ok(error instanceof PlaceholderLeakError);
      assert.ok(error instanceof Error);
      assert.deepEqual(error.kinds, ["EMAIL", "NAME", OTHER_KIND]);
      assert.equal(error.count, 4);
      for (const fragment of ["Priya", "told", "JOHN", "{NAME_1}"]) {
        assert.ok(!error.message.includes(fragment), fragment);
      }
      return true;
    },
  );
});

test("clean text passes through unchanged", () => {
  const text = "A clear thesis. Ms. Name would agree.";
  assert.equal(assertNoPlaceholders(text), text);
  assert.deepEqual(scrubPlaceholders(text), { text, removed: 0 });
});

test("the shared global pattern carries no state between calls", () => {
  // `PLACEHOLDER_LIKE` has the `g` flag, which makes `test`/`exec` stateful;
  // every function here must be immune to a caller having used it.
  PLACEHOLDER_LIKE.lastIndex = 5;
  assert.equal(hasPlaceholders("{NAME_1}"), true);
  assert.equal(findPlaceholders("{NAME_1}").length, 1);
  assert.equal(hasPlaceholders("{NAME_1}"), true);
});
