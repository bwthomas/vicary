/**
 * Tokenisation and span boundaries, pinned against the reference implementation.
 *
 * **Every expected value in this file was captured by running the Python
 * functions**, not by reading them. The probe fed 16 texts and 11 token lists
 * through `_is_stop`, `_classify`, `_trim`, `_WORD_TOKEN`, `_LOWER_TOKEN`,
 * `_ANY_TOKEN`, `_CANDIDATE_RE`, `_PROTECTED`, `_sentence_starts`,
 * `_emphasis_spans`, `_heading_spans` and `find_title_spans` in both languages
 * and diffed the JSON: 0 differences. What follows is the load-bearing subset of
 * that sweep, written out so a regression fails a build rather than waiting for
 * somebody to re-run a probe.
 *
 * The sweep found one real divergence before it found agreement, and the
 * `naïve` case below is its regression test. Python's `\b` is Unicode-aware and
 * JavaScript's is ASCII-only, so a transliterated `\b[a-z]` matches `ve` inside
 * `naïve` — a lowercase token the reference never produces, feeding a route whose
 * whole job is deciding what is a name.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ALLCAPS_RUN,
  ANY_TOKEN,
  CANDIDATE_RE,
  DETERMINERS,
  HEADING_MAX_CHARS,
  LOWERCASE_MIN_TOKENS,
  LOWER_TOKEN,
  ORG_SUFFIXES,
  PROTECTED,
  STOP_WORDS,
  TITLE_MAX_TOKENS,
  WORD_TOKEN,
  classify,
  emphasisSpans,
  findTitleSpans,
  headingSpans,
  isStop,
  overlaps,
  placeholderFor,
  sentenceStarts,
  trim,
  type Span,
} from "../src/candidates.js";

/** `[start, end, matched]` for every match, the shape the reference probe dumped. */
function matches(pattern: RegExp, text: string): [number, number, string][] {
  return [...text.matchAll(pattern)].map((match) => [
    match.index,
    match.index + match[0].length,
    match[0],
  ]);
}

function plain(spans: Span[]): [number, number][] {
  return spans.map(([start, end]) => [start, end]);
}

// ---------------------------------------------------------------------------
// The stoplist, and what a stop word is
// ---------------------------------------------------------------------------

test("the stoplist is the shipped 421 words, not a transliteration", () => {
  assert.equal(STOP_WORDS.size, 421);
});

test("a clitic is stripped before the stoplist is consulted", () => {
  // `[A-Z][A-Za-z'’]*` matches "I'm" as one token, so without stripping the tail
  // the stoplist never sees the word — "I'm" and "As" were the two most common
  // over-fires on real prose. Both apostrophes, because a word processor curls
  // them and half the corpus arrives that way.
  assert.equal(isStop("I'm"), true);
  assert.equal(isStop("I’m"), true);
  assert.equal(isStop("It's"), true);
  assert.equal(isStop("he'd"), true);
  // "Won" is not a stop word, so stripping the tail does not rescue it.
  assert.equal(isStop("Won't"), false);
  assert.equal(isStop("Won’t"), false);
});

test("the un-apostrophized spellings students type are stoplisted directly", () => {
  // There is no clitic boundary to find in "im", and "im" is a given name in
  // Wikidata — which is how "im faithfull" and "im going" became name candidates.
  for (const token of ["im", "dont", "thats"]) {
    assert.equal(isStop(token), true, `${token} should be a stop word`);
  }
});

test("a bare clitic is not itself a stop word", () => {
  // `len(word) > len(clitic)` in the reference: stripping "'s" off "'s" would
  // leave nothing, and an empty string is not a stoplist hit in either language.
  assert.equal(isStop("'s"), false);
  assert.equal(isStop("n't"), false);
  assert.equal(isStop("'"), false);
});

test("trailing punctuation and case do not hide a stop word", () => {
  assert.equal(isStop("The"), true);
  assert.equal(isStop("the"), true);
  assert.equal(isStop("Mrs."), true);
  assert.equal(isStop("Mrs"), true);
  assert.equal(isStop("A"), true);
});

test("honorifics that are not stoplisted stay available as name heads", () => {
  // "Dr" is stoplisted so a bare "Dr." cannot become a candidate; "Coach" is not,
  // and the asymmetry is the reference's, pinned here so a port cannot quietly
  // normalise it away.
  assert.equal(isStop("Dr"), true);
  assert.equal(isStop("Coach"), false);
  assert.equal(isStop("Terrence"), false);
  assert.equal(isStop("SLAM"), false);
});

// ---------------------------------------------------------------------------
// Trimming a match into runs
// ---------------------------------------------------------------------------

test("an interior stop word splits a span rather than trimming its edges", () => {
  // "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER" is one match, because in an
  // all-caps sentence every token is capitalised. The name is in the middle, so
  // edge-trimming would keep the whole shout.
  assert.deepEqual(
    trim(["My", "Best", "Friend", "Deshawn", "Pritchard", "Would", "Never"]),
    [["Deshawn", "Pritchard"]],
  );
  assert.deepEqual(trim(["Coach", "Ruiz", "And", "Marisol"]), [
    ["Coach", "Ruiz"],
    ["Marisol"],
  ]);
});

test("an honorific introducing a name keeps the span whole", () => {
  // Masking only the surname leaves the relationship and the surname's position
  // in the text, which is most of what a reader needed the name for.
  assert.deepEqual(trim(["Mrs.", "Okonkwo"]), [["Mrs.", "Okonkwo"]]);
});

test("an honorific introducing nothing is dropped", () => {
  assert.deepEqual(trim(["Mrs."]), []);
  assert.deepEqual(trim(["Dr", "The"]), []);
});

test("a span with no stop words survives unsplit", () => {
  assert.deepEqual(trim(["Terrence", "Okonkwo"]), [["Terrence", "Okonkwo"]]);
});

// ---------------------------------------------------------------------------
// Typing the span
// ---------------------------------------------------------------------------

test("an org suffix types the span ORGANIZATION with no oracle wired", () => {
  assert.equal(classify(["Acme", "Inc."]), "ORGANIZATION");
  assert.equal(classify(["Terrence", "Okonkwo"]), "NAME");
});

test("without a settlement oracle every non-organization span is a NAME", () => {
  // The behaviour before the tier existed, and the behaviour a caller that wires
  // no oracles still gets.
  assert.equal(classify(["Akron"]), "NAME");
  assert.equal(classify(["Springfield", "Township"]), "NAME");
});

test("the org suffix beats the settlement lookup", () => {
  // Load-bearing rather than arbitrary: the suffix is direct evidence about *this*
  // string, where the tier is evidence about a substring of it.
  const settlement = (name: string) =>
    new Set(["akron", "acme inc.", "springfield township"]).has(name.toLowerCase());
  assert.equal(classify(["Acme", "Inc."], settlement), "ORGANIZATION");
  assert.equal(classify(["Akron"], settlement), "LOCATION");
  assert.equal(classify(["Springfield", "Township"], settlement), "LOCATION");
});

test("only the last token is consulted for an org suffix", () => {
  assert.ok(ORG_SUFFIXES.has("school"));
  assert.equal(classify(["Westfield", "High", "School"]), "ORGANIZATION");
  assert.equal(classify(["School", "Of", "Rock"]), "NAME");
});

test("a placeholder is typed only for the kinds that have one", () => {
  assert.equal(placeholderFor("NAME"), "{NAME}");
  assert.equal(placeholderFor("ORGANIZATION"), "{ORGANIZATION}");
  assert.equal(placeholderFor("LOCATION"), "{LOCATION}");
});

// ---------------------------------------------------------------------------
// The token patterns
// ---------------------------------------------------------------------------

test("the candidate pattern keeps an honorific, initials, hyphens and the possessive", () => {
  assert.deepEqual(matches(CANDIDATE_RE, "Mrs. Okonkwo taught us. Dr Ruiz did not."), [
    [0, 12, "Mrs. Okonkwo"],
    [24, 31, "Dr Ruiz"],
  ]);
  assert.deepEqual(
    matches(CANDIDATE_RE, "J. R. R. Tolkien wrote it, and so did T.S. Eliot."),
    [
      [0, 16, "J. R. R. Tolkien"],
      [38, 48, "T.S. Eliot"],
    ],
  );
  assert.deepEqual(
    matches(CANDIDATE_RE, "Marguerite Delacroix-Whitfield and O'Brien were there."),
    [
      [0, 30, "Marguerite Delacroix-Whitfield"],
      [35, 42, "O'Brien"],
    ],
  );
  // The possessive comes with the name rather than being left behind as a
  // fragment, curly apostrophe included.
  assert.deepEqual(
    matches(
      CANDIDATE_RE,
      "Terrence's older brother, Narciso's friend, and Lincoln’s hat.",
    ),
    [
      [0, 10, "Terrence's"],
      [26, 35, "Narciso's"],
      [48, 57, "Lincoln’s"],
    ],
  );
});

test("a lowercase particle stays inside the name", () => {
  // Without this "Vincent van Gogh" generates two candidates and the gazetteer
  // has to know both halves.
  assert.deepEqual(
    matches(CANDIDATE_RE, "My inspiration, Vincent van Gogh, painted for years."),
    [
      [0, 2, "My"],
      [16, 32, "Vincent van Gogh"],
    ],
  );
});

test("the candidate pattern sees the word inside a placeholder, which is why PROTECTED exists", () => {
  // Not a defect to fix here: the bare word inside the braces is capitalised, so
  // generation produces it and the protected-span pass is what removes it. Pinned
  // so a port that "fixes" the pattern instead diverges from the reference.
  assert.deepEqual(
    matches(CANDIDATE_RE, "{NAME_1} met @PERSON2 and {LOCATION} last June."),
    [
      [1, 5, "NAME"],
      [14, 20, "PERSON"],
      [27, 35, "LOCATION"],
      [42, 46, "June"],
    ],
  );
  assert.deepEqual(
    matches(PROTECTED, "{NAME_1} met @PERSON2 and {LOCATION} last June."),
    [
      [0, 8, "{NAME_1}"],
      [13, 21, "@PERSON2"],
      [26, 36, "{LOCATION}"],
    ],
  );
});

test("the lowercase route cannot claim the tail of a capitalised word", () => {
  // There is no word boundary between the "T" and the "errence" of "Terrence", so
  // the capitalised route keeps exclusive claim on anything it can see.
  assert.deepEqual(matches(LOWER_TOKEN, "Terrence Okonkwo came over"), [
    [17, 21, "came"],
    [22, 26, "over"],
  ]);
});

test("an accented letter is a word character, as it is in the reference", () => {
  // The divergence the parity sweep caught. JavaScript's `\b` is ASCII-only, so a
  // transliterated `\b[a-z]` finds a boundary inside "naïve" and emits "ve" —
  // a lowercase token the reference never produces. The reference emits "na" and
  // stops, because `ï` is a word character to Python and the boundary is not
  // there.
  assert.deepEqual(matches(LOWER_TOKEN, "naïve café Renée went home. i did too."), [
    [0, 2, "na"],
    [6, 9, "caf"],
    [17, 21, "went"],
    [22, 26, "home"],
    [28, 29, "i"],
    [30, 33, "did"],
    [34, 37, "too"],
  ]);
});

test("the word patterns differ only in the case they admit", () => {
  const text = "Then SLAM! the door closed and Marisol laughed.";
  assert.deepEqual(matches(WORD_TOKEN, text), matches(ANY_TOKEN, text));
  assert.deepEqual(
    matches(WORD_TOKEN, text).map(([, , token]) => token),
    ["Then", "SLAM", "the", "door", "closed", "and", "Marisol", "laughed"],
  );
});

// ---------------------------------------------------------------------------
// Sentence starts
// ---------------------------------------------------------------------------

test("a sentence begins at the start of the text", () => {
  assert.deepEqual([...sentenceStarts("My cousin Terrence Okonkwo came over.")], [0]);
});

test("an opening quote begins a sentence, so its capital is orthographic", () => {
  // Quoted material is how feedback refers to a student's own words, and "vivid
  // words like 'Giggles filled the school'" put a capital on `Giggles` for the
  // same orthographic reason a full stop does. It masked as a name in text a
  // student reads.
  assert.deepEqual(
    [...sentenceStarts('vivid words like "Giggles filled the school" stand out')].sort(
      (a, b) => a - b,
    ),
    [0, 18],
  );
});

test("a line break begins a sentence and terminal punctuation does too", () => {
  assert.deepEqual(
    [...sentenceStarts("One line.\n\nAnother line.\nA third.")].sort((a, b) => a - b),
    [0, 11, 25],
  );
});

test("an apostrophe inside a word is not an opening quote", () => {
  // The quote must not be preceded by a letter, so "don't" and "Narciso's" are
  // untouched — otherwise every possessive would start a sentence.
  assert.deepEqual([...sentenceStarts("I don't think Narciso's cat minds")], [0]);
});

// ---------------------------------------------------------------------------
// Emphasis
// ---------------------------------------------------------------------------

test("a short all-caps run is emphasis", () => {
  // Where "SLAM", "WHACK", "LAUGHTER" and "REDACT" came from on real student
  // writing: the informal register's italics.
  assert.deepEqual(plain(emphasisSpans("Then SLAM! the door closed and Marisol laughed.")), [
    [5, 9],
  ]);
});

test("a run of ALLCAPS_RUN words or more is not emphasis", () => {
  // A long all-caps run is a writer who has stopped using case at all, and the
  // stoplist carries the whole decision there.
  assert.equal(ALLCAPS_RUN, 3);
  assert.deepEqual(
    plain(emphasisSpans("MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER.")),
    [],
  );
  assert.deepEqual(plain(emphasisSpans("THIS IS BAD")), []);
  assert.deepEqual(plain(emphasisSpans("THIS IS")), [[0, 7]]);
});

test("single-character tokens are not a shout", () => {
  // "I" is upper-case for every writer, and the initials in "J. R. Tolkien" are
  // part of a name rather than a shout.
  assert.deepEqual(
    plain(emphasisSpans("J. R. R. Tolkien wrote it, and so did T.S. Eliot.")),
    [],
  );
  assert.deepEqual(plain(emphasisSpans("I went home")), []);
});

// ---------------------------------------------------------------------------
// Headings
// ---------------------------------------------------------------------------

test("a short unpunctuated line after a blank line is a heading", () => {
  const text =
    "Horses\n\nThe first horses were small.\n\nHorse Families\n\nThey live in herds.";
  assert.deepEqual(plain(headingSpans(text)), [
    [0, 6],
    [38, 52],
  ]);
});

test("the blank line is what separates a heading from a wrapped line", () => {
  // Body prose here is hard-wrapped, so "The INternet as we know it today first"
  // is a short unpunctuated line too. First-in-document counts as preceded by a
  // blank, which is why this one is still read as a heading — and why the second
  // line, mid-paragraph, is not.
  const text = "The INternet as we know it today first\nappeared in a lab.";
  assert.deepEqual(plain(headingSpans(text)), [[0, 38]]);
});

test("a sentence is not a heading, however short", () => {
  assert.deepEqual(plain(headingSpans("My cousin Terrence Okonkwo came over.")), []);
  assert.deepEqual(plain(headingSpans("Short.")), []);
  assert.deepEqual(plain(headingSpans("Short!")), []);
  assert.deepEqual(plain(headingSpans("Short?")), []);
});

test("a line at or over the length limit is prose", () => {
  const long = "x".repeat(HEADING_MAX_CHARS);
  assert.deepEqual(plain(headingSpans(long)), []);
  assert.deepEqual(plain(headingSpans("x".repeat(HEADING_MAX_CHARS - 1))), [
    [0, HEADING_MAX_CHARS - 1],
  ]);
});

test("overlap is half-open on both ends", () => {
  const spans: Span[] = [[5, 9]];
  assert.equal(overlaps(spans, 0, 5), false);
  assert.equal(overlaps(spans, 9, 12), false);
  assert.equal(overlaps(spans, 4, 6), true);
  assert.equal(overlaps(spans, 8, 12), true);
  assert.equal(overlaps([], 0, 100), false);
});

// ---------------------------------------------------------------------------
// The title scan
// ---------------------------------------------------------------------------

const TITLES = ["to kill a mockingbird", "the lion king", "charlotte's web", "the lion"];
const isTitle = (text: string) =>
  TITLES.includes(text.toLowerCase().replaceAll("’", "'"));
const isPrefix = (key: string) =>
  TITLES.some((title) => title === key || title.startsWith(`${key} `));

test("a title is claimed whole, across the stop word that would split it", () => {
  // The whole reason this runs before generation: "To Kill a Mockingbird" splits
  // on the stoplisted "a" and comes back as "To {NAME} a {NAME}", which no lookup
  // on either half can undo.
  assert.deepEqual(plain(findTitleSpans("I read To Kill a Mockingbird last year.", isTitle, isPrefix)), [
    [7, 28],
  ]);
});

test("the longest title wins and the scan resumes after it", () => {
  // "The Lion" is also a title here, so a shortest-match scan would claim it and
  // leave "King" to generation.
  assert.deepEqual(
    plain(findTitleSpans("The Lion King is my favourite film.", isTitle, isPrefix)),
    [[0, 13]],
  );
});

test("a curly apostrophe is folded before the prefix walk", () => {
  // A word processor turns every apostrophe curly, so "Charlotte’s Web" tokenises
  // with a character the gazetteer's keys never contain and the walk would stop on
  // its first token.
  assert.deepEqual(
    plain(findTitleSpans("We read Charlotte’s Web in class.", isTitle, isPrefix)),
    [[8, 23]],
  );
});

test("the prefix walk and the exhaustive scan agree", () => {
  // `isPrefix` is a cost optimisation, not a semantic one. If the two disagree the
  // optimisation is a behaviour change wearing a performance costume.
  for (const text of [
    "I read To Kill a Mockingbird last year.",
    "The Lion King is my favourite film.",
    "We read Charlotte’s Web in class.",
    "Nothing here matches any title at all.",
  ]) {
    assert.deepEqual(
      plain(findTitleSpans(text, isTitle, isPrefix)),
      plain(findTitleSpans(text, isTitle)),
      text,
    );
  }
});

test("requiresCapital skips a lowercase title head", () => {
  // In a document that capitalises its proper nouns, a title's first word is
  // capitalised too. Documents that do NOT capitalise are scanned at every
  // position, because there the case carries nothing.
  const lower = "i read to kill a mockingbird last year.";
  assert.deepEqual(plain(findTitleSpans(lower, isTitle, isPrefix)), [[7, 28]]);
  assert.deepEqual(plain(findTitleSpans(lower, isTitle, isPrefix, true)), []);
});

test("a single-token title is never matched", () => {
  // "It" and "Up" must not make ordinary words permanently notable.
  const single = (text: string) => text.toLowerCase() === "it";
  assert.deepEqual(plain(findTitleSpans("It was a dark night.", single)), []);
});

test("the token limit is a named limit, not an oversight", () => {
  // The tier's longest entry is 36 tokens, but scanning that far costs 36 lookups
  // per token position for titles nobody writes in an essay. 8 covers "To Kill a
  // Mockingbird"; "The Curious Incident of the Dog in the Night-Time" is 10.
  assert.equal(TITLE_MAX_TOKENS, 8);
  const nine = "a b c d e f g h i";
  assert.deepEqual(plain(findTitleSpans(nine, (text) => text === nine)), []);
  const eight = "a b c d e f g h";
  assert.deepEqual(plain(findTitleSpans(eight, (text) => text === eight)), [[0, 15]]);
});

// ---------------------------------------------------------------------------
// The constants the later pieces read
// ---------------------------------------------------------------------------

test("the determiner list is the structural signal it claims to be", () => {
  // English does not put a bare determiner in front of a person's given name, so
  // this does not grow with the corpus. Possessives are in it: a student writes
  // "my cousin terrence", never "my terrence".
  assert.equal(DETERMINERS.size, 35);
  for (const word of ["a", "an", "the", "my", "their", "enough"]) {
    assert.ok(DETERMINERS.has(word), `${word} should be a determiner`);
  }
  assert.equal(DETERMINERS.has(""), false);
});

test("the lowercase route's floor is two tokens", () => {
  // The single decision that makes that route affordable.
  assert.equal(LOWERCASE_MIN_TOKENS, 2);
});
