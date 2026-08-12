/**
 * Where JavaScript's regex dialect differs from Python's, pinned rather than reasoned about.
 *
 * The counterpart of `ruby/test/dialect_test.rb`. The detector was written against
 * Python `re`; this port transliterated it. Three of JavaScript's defaults disagree
 * with Python's, and each is a silent behaviour change rather than an error:
 *
 * * `\b` is **ASCII-only** in JavaScript and Unicode-aware in Python, so
 *   JavaScript finds a word boundary inside `naïve` and `cousinä` that the
 *   reference never finds.
 * * `\w` and `\d` are ASCII-only here and Unicode-aware in Python.
 * * `$` matches **before a trailing newline** in Python and only at end-of-input
 *   in JavaScript — and gains Ruby's line-anchored meaning the moment the `m` flag
 *   is added.
 *
 * `\s` is the one that does NOT disagree, which is worth a test of its own: it
 * matches a non-breaking space in both, so narrowing it would be a change rather
 * than a correction.
 *
 * Every site is already written the Python way. This file exists because **neither
 * shared spec layer catches it if somebody writes it back.** Measured, not assumed:
 * adding the `m` flag to `ZIP` leaves all 54 conformance frames green and every
 * primitive assertion green, because the fixture corpus is single-line and this
 * rule only diverges across a newline.
 *
 * So each test below asserts the divergence in both directions: that the pattern as
 * written gives the reference answer, AND that the idiomatic-JavaScript spelling
 * gives a different one. The second half is what makes this a test rather than a
 * restatement — an assertion that only pins the current answer would still pass if
 * the difference evaporated, and then it would be guarding nothing.
 *
 * The declared gap this closes is recorded in `conformance/coverage.json`, and
 * `tools/tests/test_coverage_parity.py` fails if its entry outlives it.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LOWER_TOKEN,
  NOT_WORD_AFTER,
  NOT_WORD_BEFORE,
  namesSomeoneTheWriterKnows,
} from "../src/candidates.js";
import { ZIP } from "../src/structured.js";

/** The same pattern with a flag added, refusing if it was already there. */
function withFlags(pattern: RegExp, flags: string): RegExp {
  for (const flag of flags) {
    assert.ok(
      !pattern.flags.includes(flag),
      `this pattern already carries the \`${flag}\` flag, so this test is checking nothing`,
    );
  }
  return new RegExp(pattern.source, pattern.flags + flags);
}

/** The same pattern with one piece of its source substituted, refusing a no-op. */
function loosened(pattern: RegExp, from: string, to: string): RegExp {
  const source = pattern.source;
  assert.notEqual(
    source,
    source.replace(from, to),
    `\`${from}\` is no longer in this pattern, so this test is checking nothing`,
  );
  return new RegExp(source.replace(from, to), pattern.flags);
}

function matched(pattern: RegExp, text: string): string[] {
  return [...text.matchAll(pattern)].map((m) => m[0]);
}

// ---------------------------------------------------------------------------
// The word boundary
// ---------------------------------------------------------------------------

test("javascript's word boundary is ASCII-only, which is why the lookarounds are spelled out", () => {
  // The divergence the parity sweep caught before it found agreement. Both
  // directions, because both occur in the source patterns.
  assert.equal(/\bcousin\b/.test("cousinä"), true, "JavaScript's \\b was expected to disagree");
  assert.equal(/\bve/.test("naïve"), true);

  // The spelled-out form agrees with Python, and needs the `u` flag to do it.
  assert.equal(new RegExp(`cousin${NOT_WORD_AFTER}`, "u").test("cousinä"), false);
  assert.equal(new RegExp(`cousin${NOT_WORD_AFTER}`, "u").test("my cousin came over"), true);
  assert.equal(new RegExp(`${NOT_WORD_BEFORE}ve`, "u").test("naïve"), false);
  assert.equal(new RegExp(`${NOT_WORD_BEFORE}ve`, "u").test("the ve token"), true);
});

test("the u flag is not optional decoration on a property escape", () => {
  // The JavaScript-specific trap, and the reason the lookarounds cannot simply be
  // pasted into a pattern that forgot the flag. Without `u`, `\p` is an identity
  // escape: the pattern matches the literal text `p{L}` and NOTHING else, so it
  // silently stops matching letters instead of raising.
  //
  // Built with `new RegExp` rather than written as a literal because tsc REFUSES
  // the literal form (TS1530), which is worth knowing precisely: the compiler
  // already guards the spelling nobody would use here, and the form this port
  // actually uses — a lookaround kept as a string and compiled at module load —
  // is the one it cannot see. So the dynamic form is what gets pinned.
  const noFlag = new RegExp("\\p{L}");
  assert.equal(noFlag.test("Z"), false, "without `u` this must not match a letter");
  assert.equal(noFlag.test("p{L}"), true, "without `u` this is the literal string");
  assert.equal(new RegExp("\\p{L}", "u").test("Z"), true);
  // Which is why every pattern built from these strings carries the flag.
  assert.ok(LOWER_TOKEN.flags.includes("u"), "LOWER_TOKEN must carry the u flag");
  assert.equal(new RegExp(NOT_WORD_BEFORE + "ve").test("naïve"), true, "the unflagged " +
    "lookbehind is inert, which is the silent failure this flag prevents");
});

test("the lowercase route agrees with the reference on an accented word", () => {
  // The end-to-end version of the boundary rule: `ï` is a word character to
  // Python, so the reference emits `na` and stops rather than finding `ve` inside
  // `naïve` — a lowercase token feeding a route whose whole job is deciding what
  // is a name.
  assert.deepEqual(matched(LOWER_TOKEN, "naïve café Renée went home. i did too."), [
    "na",
    "caf",
    "went",
    "home",
    "i",
    "did",
    "too",
  ]);
  // The ASCII control: with no accented character in play the naive spelling
  // agrees, which is what isolates the Unicode difference above.
  const naive = new RegExp("\\b[a-z][a-z'’-]*", "gu");
  assert.deepEqual(
    matched(LOWER_TOKEN, "the cat sat down"),
    matched(naive, "the cat sat down"),
  );
});

test("an accented word tail may not satisfy a relation cue", () => {
  // What the boundary rule buys at the call site. With JavaScript's `\b` the cue
  // `cousin` matches inside `cousinä`, which wrongly overrides a title keep or
  // wrongly refuses corroboration for a public figure's surname.
  assert.equal(
    namesSomeoneTheWriterKnows("naïmy cousin Terrence came over that summer.", 13, 21),
    false,
  );
  assert.equal(
    namesSomeoneTheWriterKnows("Alice Adams, my cousinä came over that summer.", 0, 11),
    false,
  );
  // Genuinely adjacent still attaches, so the guard is not simply switched off.
  assert.equal(
    namesSomeoneTheWriterKnows("My neighbor Alice Adams walked me to the bus stop.", 12, 23),
    true,
  );
});

// ---------------------------------------------------------------------------
// The end-of-input anchor
// ---------------------------------------------------------------------------

test("the multiline flag turns the ZIP anchor into a line anchor and it over-fires", () => {
  // `\s*$` under `m` is satisfied at the end of every line of a hard-wrapped
  // essay, so any 5-digit number a student ends a line on — a locker combination,
  // a population, a year range — masks as a ZIP code.
  const text = "I live at 12345\nMy locker combination is 90210 and I forget it.";

  assert.deepEqual(matched(ZIP, text), [], "a 5-digit number at a line end is not a ZIP");
  assert.deepEqual(
    matched(withFlags(ZIP, "m"), text),
    ["12345"],
    "the line-anchored spelling was expected to over-fire here; if it no longer " +
      "does, this guard has stopped guarding anything",
  );

  // ...and the cases a real ZIP arrives in still match, so the anchor is not
  // simply switched off.
  assert.deepEqual(matched(ZIP, "Send it to 12345."), ["12345"]);
  assert.deepEqual(matched(ZIP, "12345"), ["12345"]);
  assert.deepEqual(matched(ZIP, "Akron 12345 OH"), ["12345"]);
});

test("python's dollar matches before a trailing newline and javascript's does not", () => {
  // The raw divergence, pinned even though `ZIP` is immune to it: `\s*` consumes
  // the newline before `$` is consulted, so the transliteration is correct there
  // by construction rather than by intent. A future pattern using a bare `$`
  // where the reference used one would inherit this silently.
  assert.equal(/12345$/.test("12345\n"), false, "JavaScript's $ is end-of-input");
  // Python's `$` is this, and Python's `\Z` is JavaScript's bare `$`.
  assert.equal(/12345(?=\n?$)/.test("12345\n"), true);
  // Which is why ZIP survives: the whitespace run eats the newline itself.
  assert.deepEqual(matched(ZIP, "Send it to 12345\n"), ["12345"]);
  assert.deepEqual(matched(loosened(ZIP, "\\s*$", "$"), "Send it to 12345\n"), []);
});

// ---------------------------------------------------------------------------
// The shorthand classes
// ---------------------------------------------------------------------------

test("the shorthand classes are ASCII-only, which is the real narrowing", () => {
  // `\w` is where JavaScript actually parts company with Python, and it is why
  // NOT_WORD_BEFORE spells its class out instead of using `\w`.
  assert.equal(/^\w$/u.test("ä"), false, "JavaScript's \\w was expected to be ASCII-only");
  assert.equal(new RegExp(`^[${"\\p{L}\\p{N}_"}]$`, "u").test("ä"), true);

  // `\d` diverges the same way and is deliberately left as-is, matching the Ruby
  // port, which has the identical narrowing and reproduces every frame. Asserted
  // rather than assumed so the choice is visible: if a future fixture contains a
  // non-ASCII digit, this is the test that says which way the three ports will
  // disagree.
  assert.equal(/^\d$/u.test("٣"), false, "Python matches an Arabic-Indic digit here");
});

test("the whitespace class is the one that already agrees, so narrowing it would be a change", () => {
  // Recorded because the reasoning above does NOT extend to `\s`, and extending it
  // would have been a plausible-sounding lie. Both languages match a non-breaking
  // space, so the patterns that use `\s` need no correction.
  assert.equal(/^\s$/u.test(" "), true, "JavaScript's \\s matches a non-breaking space");
  assert.equal(/^\s$/u.test(" "), true, "...and an em space");
});
