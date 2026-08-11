/**
 * The port's tokenisation and capitalisation primitives, against the shared spec.
 *
 * `conformance/frames.json` scores finished output, which is the right final bar
 * and a poor first one: a port with nothing implemented scores 0 of 35 and learns
 * nothing about which of the forty-odd primitives underneath is wrong.
 * `primitives.json` is that missing layer — generated from the Python functions,
 * byte-compared against a fresh export by `python/tests/test_conformance.py`, and
 * read here rather than transcribed.
 *
 * **Not a score and not a gate.** A port can be green in this file and mask
 * nothing. The frames are still what says a port works; this only says which brick
 * is crooked, and says it in one run instead of a bisect.
 *
 * The narrative version of these cases is `candidates.test.ts`, which explains
 * *why* each answer is the answer. This file is the exhaustive half: every
 * primitive over every corpus entry, with no room for a case to be quietly
 * dropped. Both are wanted — a table cannot carry a reason, and a reason cannot
 * carry 27 inputs.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { loadPrimitives } from "../src/conformance.js";
import {
  ALLCAPS_RUN,
  ANY_TOKEN,
  CANDIDATE_RE,
  DROPS_CAPITALS_MIN_RATE,
  HEADING_MAX_CHARS,
  LANDMARK_SUFFIXES,
  LOWERCASE_MIN_TOKENS,
  LOWER_TOKEN,
  MARKS_PROPER_NOUNS_MIN,
  ORG_SUFFIXES,
  PRECEDENCE,
  PROTECTED,
  STOP_WORDS,
  TITLE_MAX_TOKENS,
  WORD_TOKEN,
  capitalisationHabit,
  classify,
  classifyTags,
  emphasisSpans,
  findTitleSpans,
  headingSpans,
  isStop,
  midSentenceCapitals,
  resolve,
  sentenceStarts,
  trim,
  type Span,
} from "../src/candidates.js";

const spec = loadPrimitives();

/**
 * The stand-in oracles, built from the spec's own lists.
 *
 * Their semantics are stated in the generator and must be implemented exactly;
 * a port that folds differently here is measuring its fold, not its scan. Real
 * gazetteer tiers are deliberately not used — a primitive that disagreed would
 * then be indistinguishable from a tier lookup that disagreed.
 */
const settlements = new Set(spec.oracles.settlements);
const titles = spec.oracles.titles;
const isSettlement = (name: string) => settlements.has(name.toLowerCase());
const isTitle = (text: string) =>
  titles.includes(text.toLowerCase().replaceAll("’", "'"));
const isTitlePrefix = (key: string) =>
  titles.some((title) => title === key || title.startsWith(`${key} `));

/** `[start, end, matched]` per match — the shape the generator emits. */
function matches(pattern: RegExp, text: string): [number, number, string][] {
  return [...text.matchAll(pattern)].map((match) => [
    match.index,
    match.index + match[0].length,
    match[0],
  ]);
}

const plain = (spans: readonly Span[]) => spans.map(([start, end]) => [start, end]);

/**
 * Run one section over every case the spec lists for it.
 *
 * The section is read out of `spec.cases` rather than iterated from the corpus, so
 * a section the generator stopped emitting fails loudly here instead of passing
 * vacuously. That is the same reasoning as the asset's declared tier counts: a
 * silent shrinkage reads as a pass.
 */
function section(
  name: string,
  inputs: Record<string, unknown>,
  produce: (input: never, caseName: string) => unknown,
): void {
  test(`primitives: ${name}`, () => {
    const cases = spec.cases[name];
    assert.ok(cases, `the spec has no \`${name}\` section`);
    assert.ok(
      Object.keys(cases).length > 0,
      `the spec's \`${name}\` section is empty, so this test checks nothing`,
    );
    for (const [caseName, expected] of Object.entries(cases)) {
      const input = inputs[caseName];
      assert.ok(
        input !== undefined,
        `\`${name}\` names case ${caseName}, which the spec's inputs do not`,
      );
      assert.deepEqual(
        JSON.parse(JSON.stringify(produce(input as never, caseName))),
        expected,
        `${name}[${caseName}] — input ${JSON.stringify(input)}`,
      );
    }
  });
}

const corpus = spec.corpus as Record<string, unknown>;
const lists = spec.tokenLists as Record<string, unknown>;
const stopTokens = Object.fromEntries(spec.stopTokens.map((t) => [t, t]));

test("the spec's constants are this build's constants", () => {
  // Emitted as data because a port that reads the corpus off the spec and the
  // thresholds off a literal it typed can pass every case above and still be
  // tuned differently — the corpus simply may not contain the input that
  // separates 2 from 3.
  assert.deepEqual(spec.constants, {
    allcaps_run: ALLCAPS_RUN,
    drops_capitals_min_rate: DROPS_CAPITALS_MIN_RATE,
    heading_max_chars: HEADING_MAX_CHARS,
    lowercase_min_tokens: LOWERCASE_MIN_TOKENS,
    marks_proper_nouns_min: MARKS_PROPER_NOUNS_MIN,
    stop_words: STOP_WORDS.size,
    title_max_tokens: TITLE_MAX_TOKENS,
  });
});

section("is_stop", stopTokens, (token: string) => isStop(token));

section("trim", lists, (tokens: string[]) => trim(tokens));
section("classify", lists, (tokens: string[]) => classify(tokens));
section("classify_with_settlement", lists, (tokens: string[]) =>
  classify(tokens, isSettlement),
);
section("classify_tags", lists, (tokens: string[]) =>
  [...classifyTags(tokens)].sort(),
);
section("classify_tags_with_settlement", lists, (tokens: string[]) =>
  [...classifyTags(tokens, isSettlement)].sort(),
);
section("masks_with_settlement", lists, (tokens: string[]) =>
  resolve(classifyTags(tokens, isSettlement)).mask,
);

test("the spec's precedence table is this build's precedence table", () => {
  // The rows are emitted as data for the same reason the constants are: a port
  // that reads the corpus off the spec and orders its own rows by hand can pass
  // every case above and still resolve a collision the other way. `classify`
  // cannot catch that on its own — a kept span and a span typed NAME are the
  // same string there — which is what `masks_with_settlement` is for.
  assert.deepEqual(spec.precedence, PRECEDENCE);
});

test("the spec's suffix lists are this build's suffix lists", () => {
  // The two arms above are only as ported as the words they read, and the token
  // lists reach 3 of 46 organisation suffixes and 3 of 36 landmark suffixes —
  // `inc`, `school`, `church` and `library`, `memorial`, `park`. Every other
  // entry was hand-transliterated and, until this assertion, checked by nothing:
  // a port missing `hospital` or `valley` stayed green here, in the frames, and
  // in the gates, and would quietly keep a town or mask a landmark in prose the
  // fixture happens not to contain.
  assert.deepEqual([...ORG_SUFFIXES].sort(), spec.suffixes.organization);
  assert.deepEqual([...LANDMARK_SUFFIXES].sort(), spec.suffixes.landmark);
});

section("word_token", corpus, (text: string) => matches(WORD_TOKEN, text));
section("lower_token", corpus, (text: string) => matches(LOWER_TOKEN, text));
section("any_token", corpus, (text: string) => matches(ANY_TOKEN, text));
section("candidate_re", corpus, (text: string) => matches(CANDIDATE_RE, text));
section("protected", corpus, (text: string) => matches(PROTECTED, text));

section("sentence_starts", corpus, (text: string) =>
  [...sentenceStarts(text)].sort((a, b) => a - b),
);
section("emphasis_spans", corpus, (text: string) => plain(emphasisSpans(text)));
section("heading_spans", corpus, (text: string) => plain(headingSpans(text)));

section("title_spans", corpus, (text: string) =>
  plain(findTitleSpans(text, isTitle, isTitlePrefix)),
);
section("title_spans_requires_capital", corpus, (text: string) =>
  plain(findTitleSpans(text, isTitle, isTitlePrefix, true)),
);

section("capitalisation_habit", corpus, (text: string) => capitalisationHabit(text));
section("capitalisation_habit_with_headings", corpus, (text: string) =>
  capitalisationHabit(text, headingSpans(text)),
);

section("mid_sentence_capitals", corpus, (text: string) =>
  [...midSentenceCapitals(text, sentenceStarts(text))].sort(),
);
section("mid_sentence_capitals_with_headings", corpus, (text: string) =>
  [...midSentenceCapitals(text, sentenceStarts(text), headingSpans(text))].sort(),
);

test("every section the spec carries is checked by this file", () => {
  // The one assertion that cannot be written as a section, and the one that keeps
  // this file honest: a primitive added to the generator and not wired up here
  // would otherwise ship unchecked, which is the exact gap the file exists to
  // close.
  const checked = new Set([
    "is_stop", "trim", "classify", "classify_with_settlement",
    "classify_tags", "classify_tags_with_settlement", "masks_with_settlement",
    "word_token", "lower_token", "any_token", "candidate_re", "protected",
    "sentence_starts", "emphasis_spans", "heading_spans",
    "title_spans", "title_spans_requires_capital",
    "capitalisation_habit", "capitalisation_habit_with_headings",
    "mid_sentence_capitals", "mid_sentence_capitals_with_headings",
  ]);
  assert.deepEqual(
    Object.keys(spec.cases).filter((name) => !checked.has(name)),
    [],
    "the spec carries a primitive this port does not check",
  );
});
