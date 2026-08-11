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
  CONSISTENT,
  DETERMINERS,
  DROPS_CAPITALS_MIN_RATE,
  HEADING_MAX_CHARS,
  INCONSISTENT,
  LOWERCASE,
  LOWERCASE_MIN_TOKENS,
  LOWER_TOKEN,
  MARKS_PROPER_NOUNS_MIN,
  MID_SENTENCE_CAP,
  ORG_SUFFIXES,
  PROTECTED,
  SILENT,
  STOP_WORDS,
  TITLE_MAX_TOKENS,
  WORD_TOKEN,
  capitalIsTheOnlyEvidence,
  capitalisationHabit,
  classify,
  corroborated,
  dropsCapitals,
  emphasisSpans,
  findTitleSpans,
  headingSpans,
  isStop,
  marksProperNouns,
  midSentenceCapitals,
  overlaps,
  placeholderFor,
  sentenceStarts,
  suppressedAsAnUnevidencedCapital,
  trim,
  type CapitalisationHabit,
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

// ---------------------------------------------------------------------------
// How this writer uses capital letters
//
// Same provenance as everything above: 16 documents through both
// implementations' `capitalisation_habit`, `_mid_sentence_capitals` and
// `_capital_is_the_only_evidence`, diffed as JSON, 0 differences. What follows
// pins the four states, the heading exclusion, and the rate asymmetry.
// ---------------------------------------------------------------------------

test("a writer who marks proper nouns and keeps sentence capitals is consistent", () => {
  assert.equal(
    capitalisationHabit(
      "We drove to Akron in July. My cousin Terrence met us there. " +
        "Later Marisol showed up with Deshawn and we all went to Ohio.",
    ),
    CONSISTENT,
  );
});

test("a writer who marks nothing and drops openings is lowercase", () => {
  assert.equal(
    capitalisationHabit("then terrence okonkwo showed up. i was so happy. we went home."),
    LOWERCASE,
  );
});

test("a writer who does both is inconsistent, which is the cell the booleans had none for", () => {
  // Suppressing the lowercase route loses the names they wrote lower-case;
  // opening it wide fires on ordinary words. So there is no document-level answer
  // here — the band falls through to per-token evidence.
  assert.equal(
    capitalisationHabit(
      "My cousin Terrence came over. my aunt Marisol drove. " +
        "we went to Akron. then Deshawn showed up. i was tired.",
    ),
    INCONSISTENT,
  );
});

test("a document that says nothing either way is silent, and silence is not consent", () => {
  // Reading it as consent is what put "line circles" and "tone toward" in front
  // of a student: a 108-290 character feedback field is ordinary prose with
  // nothing in it to capitalise.
  assert.equal(
    capitalisationHabit("Nothing here is capitalised mid sentence at all. It is only prose."),
    SILENT,
  );
  assert.equal(capitalisationHabit(""), SILENT);
});

test("a bare lower-case i alone is enough to read the writer as dropping capitals", () => {
  // The higher-precision tell: 26 of the 27 un-scrubbed documents have none at
  // all, and the one that does has nine. So it stays a boolean on both sides of
  // the floor, with no rate to soften it.
  assert.equal(
    capitalisationHabit("The Dog barked at Marisol. i ran. Then Terrence came over."),
    INCONSISTENT,
  );
});

test("one mid-sentence capital is under the floor", () => {
  // 2 rather than 1 only to tolerate a single stray capital. Lower-cased, every
  // one of the 36 measured documents scores 0, so the separation is not delicate.
  assert.equal(MARKS_PROPER_NOUNS_MIN, 2);
  assert.equal(capitalisationHabit("I met Marisol today. She was nice."), SILENT);
  assert.equal(
    capitalisationHabit("I met Marisol today. I also saw Deshawn there."),
    CONSISTENT,
  );
});

test("the rate is consulted above the floor and not below it, and the same rate decides opposite ways", () => {
  // The asymmetry is the measurement, not an oversight, and this pair is it: two
  // documents one percentage point apart in drop rate, treated differently
  // because only one of them has a presence signal to weigh the drop against.
  //
  // Above — 3 marks, 1 stylistic lower-case opening in 12 sentences (8.3%). The
  // rate says "a writer who typed one typo", which is `marching-to-his-own-beat`,
  // an NWP anchor paper marking 26 proper nouns correctly that the boolean
  // libelled.
  const typoCapitaliser =
    "Marisol went to Akron. She met Deshawn. They saw Terrence. We drove home. " +
    "She waved. He smiled. They left. It rained. We slept. boy did we laugh. " +
    "She called again. He answered.";
  assert.equal(capitalisationHabit(typoCapitaliser), CONSISTENT);

  // Below — 0 marks, 1 lower-case opening in 11 sentences (9.1%). Nothing to weigh
  // it against, so the one bit is taken conservatively. Applying the rate here
  // cost a held-out name: in carrier essay 20739 it demoted a genuine
  // lower-case-writing document to `silent`, withdrew the permissive path, and
  // leaked "terrence okonkwo". Held-out recall 28/28 to 27/28.
  const belowFloor =
    "The dog barked. The cat ran. The bird flew. The fish swam. The cow mooed. " +
    "The pig oinked. The hen clucked. The duck quacked. The goat bleated. " +
    "The horse neighed. the sheep baaed.";
  assert.equal(capitalisationHabit(belowFloor), LOWERCASE);
  assert.equal(DROPS_CAPITALS_MIN_RATE, 0.1);
});

test("the heading exclusion can move a document across the floor", () => {
  // A heading is title-cased, so every capital in it is orthographic. Counting
  // them let a heading vouch for its own words. Here it is the whole difference
  // between a document that marks proper nouns and one that says nothing.
  const text =
    "Horse Families\n\nThe first horses were small. They lived in herds.\n\n" +
    "Breeds I Like\n\nMy favourite is the Arabian.";
  assert.equal(capitalisationHabit(text, headingSpans(text)), SILENT);
  assert.equal(capitalisationHabit(text), CONSISTENT);
});

test("the two predicates cannot contradict each other, which is the point of the four states", () => {
  // `141-433` has two mid-sentence capitals and six lower-case sentence openings,
  // so under the old booleans it was simultaneously a writer who capitalises and
  // a writer who does not, and whichever predicate a call site read decided the
  // treatment.
  const table: [CapitalisationHabit, boolean, boolean][] = [
    [CONSISTENT, true, false],
    [INCONSISTENT, true, true],
    [LOWERCASE, false, true],
    [SILENT, false, false],
  ];
  for (const [habit, marks, drops] of table) {
    assert.equal(marksProperNouns(habit), marks, habit);
    assert.equal(dropsCapitals(habit), drops, habit);
  }
});

test("the habit state is the string the reference's enum carries", () => {
  // So the two languages can be diffed on the wire without a mapping table in
  // between, which is where a fifth state would otherwise appear.
  assert.deepEqual([CONSISTENT, INCONSISTENT, LOWERCASE, SILENT], [
    "consistent",
    "inconsistent",
    "lowercase",
    "silent",
  ]);
});

// ---------------------------------------------------------------------------
// Per-token testimony
// ---------------------------------------------------------------------------

test("a capital the writer chose is testimony; one orthography forced is not", () => {
  // A writer who put a capital on "Cade" somewhere other than a sentence start has
  // told us "Cade" is a name in this document; one who only ever writes
  // "Eventually" after a full stop has told us nothing, because orthography would
  // have put that capital there anyway.
  //
  // "i" is in the answer, and it is unreachable rather than wrong. The two
  // channels that read capitals disagree about the first person: the
  // document-level counter matches `[A-Z][a-z]{2,}`, so a bare "I" cannot vote on
  // whether the writer marks proper nouns — every writer capitalises it. This
  // per-token channel has no such filter.
  //
  // It cannot reach a decision. The only consumer is the corroboration guard,
  // which asks this set about the tokens of a candidate *run*, and runs come from
  // `trim`, which drops stop words. "I" is a stop word and is not an honorific, so
  // `trim(["I"])` is `[]` and no run can ever carry the token this entry would
  // answer for. Measured, not argued: removing "i" from the set leaves all 51
  // golden frames byte-identical.
  //
  // So it is pinned as-is. A port that "fixes" it diverges from the reference for
  // no behavioural gain.
  const text = "I met Marisol today. Eventually I also saw Deshawn there.";
  assert.deepEqual(
    [...midSentenceCapitals(text, sentenceStarts(text))].sort(),
    ["deshawn", "i", "marisol"],
  );
  // The document-level counter sees the same two names and not the "I".
  assert.deepEqual(
    [...text.matchAll(MID_SENTENCE_CAP)].map((match) => match[1]),
    ["Marisol", "Deshawn"],
  );
});

test("an all-caps token cannot corroborate itself", () => {
  // Load-bearing rather than tidy: without the exclusion "SLAM" is its own
  // mid-sentence capital, so every emphasis shout would clear the bar the
  // emphasis rule had just raised.
  const text = "Then SLAM! the door closed and Marisol laughed at Deshawn.";
  assert.deepEqual(
    [...midSentenceCapitals(text, sentenceStarts(text))].sort(),
    ["deshawn", "marisol"],
  );
});

test("a heading's capitals are not testimony about its own words", () => {
  // Counting them let "The First Horses" vouch for "Horses" as a name — the
  // heading corroborating itself, one line removed.
  const text =
    "Horse Families\n\nThe first horses were small. They lived in herds.\n\n" +
    "Breeds I Like\n\nMy favourite is the Arabian.";
  assert.deepEqual(
    [...midSentenceCapitals(text, sentenceStarts(text), headingSpans(text))].sort(),
    ["arabian"],
  );
  assert.deepEqual(
    [...midSentenceCapitals(text, sentenceStarts(text))].sort(),
    ["arabian", "families", "i", "like"],
  );
});

test("a multi-token span carries evidence beyond its capitals", () => {
  // "Sadie Johnson" is a *shape*, which a single capitalised word is not.
  const text = "Terrence Okonkwo came over that summer.";
  const starts = sentenceStarts(text);
  assert.equal(capitalIsTheOnlyEvidence(["Terrence"], 0, starts, []), true);
  assert.equal(capitalIsTheOnlyEvidence(["Terrence", "Okonkwo"], 0, starts, []), false);
});

test("inside a heading, a multi-token span is not a shape", () => {
  // Title case capitalises every word, so the second capital is as orthographic as
  // the first. "My Brother Terrence Okonkwo" as a heading needs the given-name
  // tier rather than its own capitals — the bar every unevidenced capital clears.
  const text = "The INternet as we know it today first\nappeared in a lab in Ohio.";
  const starts = sentenceStarts(text);
  const headings = headingSpans(text);
  assert.deepEqual(headings.map(([s, e]) => [s, e]), [[0, 38]]);
  assert.equal(
    capitalIsTheOnlyEvidence(["Terrence", "Okonkwo"], 0, starts, [], headings),
    true,
  );
  assert.equal(capitalIsTheOnlyEvidence(["Terrence", "Okonkwo"], 0, starts, []), false);
});

test("a capital inside an emphasis shout is not the writer's choice either", () => {
  const text = "Then SLAM! the door closed and Marisol laughed.";
  const starts = sentenceStarts(text);
  const emphasis = emphasisSpans(text);
  assert.deepEqual(plain(emphasis), [[5, 9]]);
  assert.equal(capitalIsTheOnlyEvidence(["SLAM"], 5, starts, emphasis), true);
  // Mid-sentence, outside the shout: a choice the writer made.
  assert.equal(capitalIsTheOnlyEvidence(["Marisol"], 31, starts, emphasis), false);
});

// ---------------------------------------------------------------------------
// Piece 3 — the sentence-initial corroboration guard
//
// Every answer below was recorded by running the reference implementation and
// this one over the same corpus and diffing, not by reading either source. The
// harness was negative-controlled first: giving the two channels different strip
// sets — the exact defect the reference's own comment records having fixed —
// diverges `corroborated` on four corpus entries and the guard on the closing-
// quote one, so an agreement here is a measurement rather than a coincidence.
//
// The guard is ~98% precise on real prose: 133 occurrences over 101 distinct
// spans suppressed, 99 of the 101 correctly. It is NOT the defect and must not be
// "fixed" — the tier feeding it was, and that was addressed in 0.1.0.
// ---------------------------------------------------------------------------

/** The stand-in given-name tier the probe used. */
const isGiven = (name: string) =>
  new Set(["terrence", "marisol", "sadie", "deshawn", "cade"]).has(name);

test("an unevidenced sentence-initial capital is suppressed", () => {
  // The whole purpose. "Words" opens the sentence, so orthography put the capital
  // there; no tier knows it and the document never capitalises it mid-sentence.
  const text = "Words like 'Terrence' stand out in that chapter.";
  const starts = sentenceStarts(text);
  const written = midSentenceCapitals(text, starts, headingSpans(text));
  assert.deepEqual([...written], []);
  assert.equal(
    suppressedAsAnUnevidencedCapital(["Words"], 0, starts, [], [], written, isGiven),
    true,
  );
});

test("both channels see the same stripped token, closing quote included", () => {
  // The candidate pattern treats `'` as a name character, so "words like
  // 'Terrence'" arrives as `Terrence'`. Stripping `.,'’` on BOTH channels is what
  // lets the tier recognise it; stripping only `.,` asks the tier about
  // `terrence'` and is told no. This case is what the negative control moved.
  const text = "Words like 'Terrence' stand out in that chapter.";
  const starts = sentenceStarts(text);
  const written = midSentenceCapitals(text, starts, headingSpans(text));
  assert.equal(corroborated(["Terrence'"], written, isGiven), true);
  // An opening quote counts as a sentence start, which is why this span reaches
  // the guard at all — and the corroboration is what keeps it.
  assert.equal(
    suppressedAsAnUnevidencedCapital(["Terrence'"], 12, starts, [], [], written, isGiven),
    false,
  );
});

test("the document's own mid-sentence capital vouches for a later sentence start", () => {
  // Channel one, with no tier involvement: the writer capitalised "Cade" at offset
  // 6, where they had a lower-case alternative and declined it. That testimony
  // carries the sentence-initial "Cade" at 21.
  const text = "I saw Cade at lunch. Cade never sits with anyone else.";
  const starts = sentenceStarts(text);
  const written = midSentenceCapitals(text, starts, headingSpans(text));
  assert.deepEqual([...written], ["cade"]);
  assert.equal(capitalIsTheOnlyEvidence(["Cade"], 21, starts, []), true);
  assert.equal(
    suppressedAsAnUnevidencedCapital(["Cade"], 21, starts, [], [], written, isGiven),
    false,
  );
});

test("the given-name tier vouches on its own, with no capital to read", () => {
  // Channel two. The document capitalises only "Johnson" mid-sentence, so "Sadie"
  // at a sentence start has nothing but the tier — and that is enough.
  const text = "Sadie came over. Later Johnson called. Nobody answered him.";
  const starts = sentenceStarts(text);
  const written = midSentenceCapitals(text, starts, headingSpans(text));
  assert.deepEqual([...written].sort(), ["johnson"]);
  assert.equal(
    suppressedAsAnUnevidencedCapital(["Sadie"], 0, starts, [], [], written, isGiven),
    false,
  );
});

test("ANY token corroborates, not just the first", () => {
  // The heading rule is what made this load-bearing. "My Brother Terrence Okonkwo"
  // in a heading is multi-token but title-cased, so it reaches the guard; it leads
  // with an honorific, so checking only the first token consults "Brother" and
  // leaks the name. "Terrence" is the third token.
  const text = "\n\nMy Brother Terrence Okonkwo\n\nHe taught me how to ride a bike.\n";
  const starts = sentenceStarts(text);
  const headings = headingSpans(text);
  const written = midSentenceCapitals(text, starts, headings);
  const run = ["Brother", "Terrence", "Okonkwo"];
  assert.equal(capitalIsTheOnlyEvidence(run, 5, starts, [], headings), true);
  assert.equal(corroborated(["Brother"], written, isGiven), false);
  assert.equal(corroborated(run, written, isGiven), true);
  assert.equal(
    suppressedAsAnUnevidencedCapital(run, 5, starts, [], headings, written, isGiven),
    false,
  );
});

test("an emphasis shout cannot corroborate itself", () => {
  // midSentenceCapitals excludes an entirely upper-case token, so "SLAM" is not
  // its own testimony — without that exclusion every shout would clear the bar the
  // emphasis rule had just raised.
  const text = "And then *SLAM* the door shut behind us.";
  const starts = sentenceStarts(text);
  const emphasis = emphasisSpans(text);
  const written = midSentenceCapitals(text, starts, headingSpans(text));
  assert.deepEqual([...written], []);
  assert.equal(
    suppressedAsAnUnevidencedCapital(["SLAM"], 10, starts, emphasis, [], written, isGiven),
    true,
  );
});

test("a mid-sentence multi-token span never reaches the guard", () => {
  // Not suppressed because `capitalIsTheOnlyEvidence` is already false: the shape
  // is the evidence. The corroboration channels are irrelevant here, and the guard
  // must not consult them — an oracle-free caller has to behave identically.
  const text = "I asked Marisol Ybarra what she thought about the ending.";
  const starts = sentenceStarts(text);
  const written = midSentenceCapitals(text, starts, headingSpans(text));
  assert.equal(capitalIsTheOnlyEvidence(["Marisol", "Ybarra"], 8, starts, []), false);
  assert.equal(
    suppressedAsAnUnevidencedCapital(
      ["Marisol", "Ybarra"], 8, starts, [], [], written, () => false,
    ),
    false,
  );
});

test("no tokens corroborate nothing", () => {
  assert.equal(corroborated([], new Set(["terrence"]), isGiven), false);
});
