/**
 * Find the person-names a student wrote, so the notability filter can decide.
 *
 * A port of `python/src/vicary/name_candidates.py`, landing in pieces. **This
 * piece is tokenisation and span boundaries**: the patterns that decide what a
 * word is, where a sentence begins, which runs of capitals are a shout rather
 * than a name, which lines are headings, and which stretches of text are already
 * redacted and must be left alone. It also carries the title scan, which runs
 * against the raw text before any candidate exists.
 *
 * What is NOT here yet: the capitalisation-habit inference, the lowercase route,
 * the sentence-initial corroboration guard, the relation override, and
 * `findCandidates` itself. Nothing in this module is wired into `redact` — the
 * conformance scoreboard is unchanged by it on purpose, because a piece of a
 * detector that moves the number is a piece that was scored before it was
 * checked.
 *
 * Why generation runs before the notability lookup, rather than instead of it
 * -----------------------------------------------------------------------------
 * Finding capitalised name-shaped spans in English student prose is close to
 * free. The hard half is deciding which ones to *keep*, and the two cases look
 * identical syntactically:
 *
 *     My cousin Terrence Okonkwo came over that summer     → redact
 *     My inspiration, Vincent van Gogh, painted for years  → keep
 *
 * Both are first-person possessive, so a relational-trigger rule gets van Gogh
 * wrong. The discriminator has to be **notability**, which is a lookup rather
 * than a model. So: generate broadly here, then `notable → keep, everything else
 * → redact`.
 *
 * Capitalisation is a clue, never the answer
 * ------------------------------------------
 * Every rule in this file weighs case rather than obeying it, because a writer
 * who capitalises most of their proper nouns still misses some and informal
 * writers shout in ALL CAPS. Each threshold below was measured on 27 un-scrubbed
 * student documents rather than argued from the shape of English; the numbers
 * travel with the constants.
 */

import { load as loadLexicon } from "./lexicon.js";

/**
 * Python's `\b` is Unicode-aware; JavaScript's is ASCII-only, so `\b` before a
 * letter is a *different assertion* in the two languages. In `naïve`, Python
 * reads `ï` as a word character and finds no boundary; JavaScript reads it as a
 * non-word character, finds one, and matches `ve` as a lowercase token that
 * Python never produced.
 *
 * So every `\b`-before-a-letter in the source patterns is spelled out as this
 * lookbehind under the `u` flag instead. `[\p{L}\p{N}_]` is the same set
 * `gazetteer.ts` uses for `str.isalnum()`, plus the underscore Python's `\w`
 * adds.
 */
const NOT_WORD_BEFORE = "(?<![\\p{L}\\p{N}_])";

/**
 * Role titles and honorifics that introduce a name. Part of the span: masking
 * "Okonkwo" out of "Mrs. Okonkwo" leaves the relationship and the surname's
 * position, and students name teachers and coaches constantly.
 */
export const HONORIFICS: readonly string[] = [
  "Mr", "Mrs", "Ms", "Miss", "Mx", "Dr", "Prof", "Professor", "Coach",
  "Officer", "Principal", "Rev", "Reverend", "Sgt", "Sergeant", "Capt",
  "Captain", "Sir", "Madam", "Fr", "Sister", "Brother", "Nurse", "Chief",
  "Aunt", "Uncle", "Grandma", "Grandpa", "Grandmother", "Grandfather",
  "Cousin", "Auntie",
];

/**
 * Lowercase particles that sit *inside* a name. Without these, "Vincent van
 * Gogh" generates two candidates and the gazetteer has to know both halves.
 */
export const PARTICLES: readonly string[] = [
  "van", "von", "de", "del", "della", "der", "den", "di", "da", "du", "la",
  "le", "los", "bin", "ibn", "al", "of", "the", "y",
];

/**
 * Suffixes that make a capitalised span an organisation rather than a person.
 * Typed separately because the placeholder is what a student reads outbound.
 */
export const ORG_SUFFIXES: ReadonlySet<string> = new Set([
  "inc", "inc.", "llc", "ltd", "corp", "corp.", "corporation", "company",
  "co", "co.", "insurance", "bank", "hospital", "clinic", "university",
  "college", "school", "academy", "institute", "foundation", "church",
  "temple", "mosque", "synagogue", "association", "society", "union",
  "department", "agency", "bureau", "committee", "council", "league",
  "team", "club", "store", "market", "restaurant", "airlines", "motors",
  "industries", "systems", "technologies", "group", "partners", "holdings",
]);

/**
 * Suffixes that make a capitalised span a public landmark — topical by
 * construction, so kept without consulting the gazetteer. "Lincoln Memorial" is
 * the essay's subject; "Akron" in the same sentence is the student's town.
 */
export const LANDMARK_SUFFIXES: ReadonlySet<string> = new Set([
  "memorial", "monument", "museum", "cathedral", "capitol", "bridge",
  "tower", "stadium", "arena", "park", "gardens", "canyon", "falls",
  "island", "mountain", "mountains", "river", "lake", "ocean", "sea",
  "desert", "valley", "peninsula", "statue", "palace", "castle", "temple",
  "pyramid", "wall", "trail", "highway", "zoo", "aquarium", "planetarium",
  "observatory", "library",
]);

/**
 * Capitalised words that are not names, read from the vendored lexicon.
 *
 * Deliberately broad: this list is the only thing standing between candidate
 * generation and "mask every capitalised word", and a capitalised ordinary word
 * is overwhelmingly sentence-initial. Skewed toward over-inclusion on purpose — a
 * missed name is one span and shows up in the recall number, while a
 * wrongly-masked common word corrupts every essay that uses it and shows up
 * nowhere unless somebody reads the prose.
 *
 * It is *data*, not a literal, because all three front doors need the same 421
 * words and a hand-transliterated stoplist diverges silently. Loaded at module
 * initialisation, matching Python's load-at-import, so an incomplete install
 * fails when the module is first reached rather than on the first essay.
 */
export const STOP_WORDS: ReadonlySet<string> = loadLexicon("stop_words");

/**
 * Contraction and possessive tails. `[A-Z][A-Za-z'’]*` matches "I'm" as one
 * token, so without stripping these the stoplist never sees the word — "I'm" and
 * "As" were the two most common over-fires on real prose. The *un*-apostrophized
 * spellings students actually type ("im", "dont", "thats") cannot be stripped
 * this way because there is no clitic boundary to find, so they are listed in the
 * stoplist directly. "im" is a given name in Wikidata, which is how "im
 * faithfull" and "im going" became name candidates.
 */
export const CLITICS: readonly string[] = [
  "n't", "n’t", "'s", "’s", "'m", "’m", "'re", "’re", "'ve", "’ve",
  "'ll", "’ll", "'d", "’d", "'t", "’t",
];

/**
 * An all-caps run this long or longer means capitalisation is not a signal, so
 * the stoplist carries the whole decision and a capital neither helps nor hurts.
 * A run *shorter* than this in an otherwise mixed-case document is the opposite
 * case: informal writers put one or two words in caps to shout, and "SLAM",
 * "WHACK" and "Nooooooo" are not names. Measured on 27 un-scrubbed student
 * documents, short all-caps runs were emphasis in every instance.
 */
export const ALLCAPS_RUN = 3;

/** Any word token, used to find all-caps runs and mid-sentence capitals. */
export const WORD_TOKEN = /[A-Za-z][A-Za-z'’-]*/g;

/**
 * Where a sentence begins: start of text, after terminal punctuation and any
 * closing quote, after a line break, or immediately inside an *opening* quote. A
 * capital in one of these positions is required by orthography, so it is evidence
 * of nothing — which is the whole of the objection to treating a capital as proof
 * that a word is a name.
 *
 * The opening-quote arm was missing, and quoted material is how feedback refers to
 * a student's own words: "vivid words like 'Giggles filled the school'" put a
 * capital on `Giggles` for the same orthographic reason a full stop does, and it
 * masked as a name in text a student reads. Only the *capital* is discounted — a
 * real name inside quotes still carries the given-name tier.
 *
 * An apostrophe inside a word cannot match: the quote must not be preceded by a
 * letter, so "don't" and "Narciso's" are untouched.
 *
 * `^` rather than `\A`, with no `m` flag, so it is start-of-text and not
 * start-of-line — the two differ here and the difference is every hard-wrapped
 * line in the corpus.
 */
export const SENTENCE_BREAK =
  /(?:^|[.!?]["'’”)]*\s+|\n+|(?:(?<=\s)|^)["'‘“](?=[A-Za-z]))\s*/g;

/**
 * One entirely-lowercase word. The leading boundary is what keeps this from
 * matching the tail of a capitalised word — there is no word boundary between the
 * "T" and the "errence" of "Terrence", so the capitalised route keeps exclusive
 * claim on anything it can see.
 */
export const LOWER_TOKEN = new RegExp(`${NOT_WORD_BEFORE}[a-z][a-z'’-]*`, "gu");

/**
 * Tokens a lowercase span must reach before it is emitted at all. Set to 2
 * deliberately, and it is the single decision that makes the lowercase route
 * affordable.
 */
export const LOWERCASE_MIN_TOKENS = 2;

/**
 * Determiners that make the word after them a common noun rather than a name.
 * "a little bit", "the guy thats", "our joy" — English does not put a bare
 * determiner in front of a person's given name, so this is a clean structural
 * signal rather than a word blacklist, and it does not grow with the corpus.
 * Measured on 25 ASAP essays it accounted for 22 of ~34 lowercase over-fire seeds,
 * `a` alone for 12. Possessives are included: a student writes "my cousin
 * terrence", never "my terrence".
 */
export const DETERMINERS: ReadonlySet<string> = new Set(
  `
  a an the this that these those
  my your his her its our their
  some any no every each either neither both all
  another other such one two three
  most much many few several enough
  `
    .split(/\s+/)
    .filter((word) => word !== ""),
);

/** What a candidate masks as. */
export type CandidateKind = "NAME" | "ORGANIZATION" | "LOCATION";

/**
 * A name-shaped span, with the placeholder it would be masked as.
 *
 * `LOCATION` was absent until 2026-08-07 on the argument that telling a place from
 * a person needs NER and the inbound path does not need the distinction — both are
 * placeholders in the training distribution. The first half of that is what
 * changed: a settlement gazetteer tier is not NER, and it types the case that
 * actually occurs. The second half was always the weaker claim, because the
 * inbound path is not the only reader — a host that echoes the placeholder back
 * writes "your trip to {NAME}".
 *
 * Still no `LOCATION` for a place that is *kept* (a landmark, a country): a kept
 * span is never masked, so it has no placeholder to type.
 */
export interface Candidate {
  readonly text: string;
  readonly start: number;
  readonly end: number;
  readonly kind: CandidateKind;
}

/** The placeholder a candidate of this kind masks as. */
export function placeholderFor(kind: CandidateKind): string {
  return kind === "ORGANIZATION" || kind === "LOCATION"
    ? `{${kind}}`
    : "{NAME}";
}

const HONORIFIC_SET: ReadonlySet<string> = new Set(
  HONORIFICS.map((honorific) => honorific.toLowerCase()),
);

/**
 * One capitalised word, hyphens and apostrophes included so "Raghunathan-Bell"
 * and "O'Brien" stay whole, and the possessive comes with the name rather than
 * being left behind as a fragment.
 */
const WORD = "[A-Z][A-Za-z'’]*(?:-[A-Z][A-Za-z'’]*)*";

/**
 * A capitalised, name-shaped span: an optional honorific, optional initials, then
 * one or more capitalised words joined by optional lowercase particles.
 *
 * The honorific alternation is leftmost-first in both languages, which is what
 * makes "Mrs." work: `Mr` matches first, its trailing `\s+` fails against the
 * "s", and the engine backtracks into `Mrs`.
 */
export const CANDIDATE_RE = new RegExp(
  NOT_WORD_BEFORE +
    `(?:(?:${HONORIFICS.join("|")})\\.?\\s+)?` +
    `(?:[A-Z]\\.\\s*)*` +
    WORD +
    `(?:\\s+(?:(?:${PARTICLES.join("|")})\\s+)?${WORD})*`,
  "gu",
);

/**
 * Spans that are already redacted and must be left strictly alone. Two kinds, and
 * both were live defects rather than hypotheticals:
 *
 * * `{NAME}` — our own placeholders. The bare word inside the braces is
 *   capitalised, so without this a second pass generates "NAME" as a candidate and
 *   masking stops being idempotent. Both directions run this classifier and the
 *   outbound pass sees text the inbound pass already masked.
 * * `@PERSON1` — an upstream anonymization marker. The `@` is not part of a
 *   capitalised-word match, so `PERSON` matched on its own and every ASAP marker's
 *   kind-word became a candidate: 23.24 spans/essay of "over-firing" that was
 *   really this.
 */
export const PROTECTED = /\{[A-Za-z_0-9]*\}|@[A-Za-z]+\d*/g;

/**
 * Any word token, either case. Used only by the title scan, which cannot key on
 * capitalisation because a student may write a title however they like.
 */
export const ANY_TOKEN = /[A-Za-z][A-Za-z'’-]*/g;

/**
 * The one fold the title scan applies before consulting the prefix index. A word
 * processor turns every apostrophe curly, so "Charlotte’s Web" tokenises with a
 * character the gazetteer's keys never contain and the walk would stop on its
 * first token. Deliberately not the gazetteer's full `normalize`: that does an
 * NFKD decomposition and a per-character rebuild, and this runs once per word of
 * every essay. An accented title head still fails the walk, which loses a keep and
 * never a redaction.
 */
const CURLY_APOSTROPHE = /[’‘ʼ′]/g;

/** How many tokens a title match may span. See {@link findTitleSpans}. */
export const TITLE_MAX_TOKENS = 8;

/**
 * Longest line still readable as a heading. Body prose in these documents is
 * hard-wrapped at ~60–590 chars per line, so length alone does not separate a
 * heading from a wrapped line — the blank line above it is what does.
 */
export const HEADING_MAX_CHARS = 60;

/** A `[start, end)` character range, half-open, in the units the text is indexed in. */
export type Span = readonly [number, number];

/** Python's `str.strip(chars)`: drop any of `chars` from both ends. */
function strip(text: string, chars: string): string {
  let begin = 0;
  let end = text.length;
  while (begin < end && chars.includes(text[begin]!)) begin += 1;
  while (end > begin && chars.includes(text[end - 1]!)) end -= 1;
  return text.slice(begin, end);
}

/**
 * Python's `str.isupper()` for a token this module's patterns can produce.
 *
 * True when the token has at least one cased character and none of them is
 * lowercase. Every token here starts with an ASCII letter, so the cased set is
 * non-empty and the comparison is the whole test; the apostrophes and hyphens in
 * between are uncased and drop out of it in both languages.
 */
function isUpper(token: string): boolean {
  return token === token.toUpperCase() && token !== token.toLowerCase();
}

/** Whether `token` is an ordinary word that must never become a candidate. */
export function isStop(token: string): boolean {
  let word = strip(token.toLowerCase(), ".,");
  for (const clitic of CLITICS) {
    if (word.endsWith(clitic) && word.length > clitic.length) {
      word = word.slice(0, -clitic.length);
      break;
    }
  }
  return STOP_WORDS.has(strip(word, "'’"));
}

/** Answers "is this a town?" — see the Python `SettlementOracle`. */
export type SettlementOracle = (name: string) => boolean;

/** Answers "is this string a published work or a fictional character?" */
export type TitleOracle = (name: string) => boolean;

/**
 * Which placeholder kind this span should mask as.
 *
 * The org suffix wins over the settlement lookup, and the order is load-bearing
 * rather than arbitrary: a name like "Westfield High School" resolves as a
 * settlement under a prefix match and is an organization. The suffix is direct
 * evidence about *this* string; the tier is evidence about a substring of it.
 *
 * The reference states the same rule with "Springfield Township" and "Akron
 * Public Library", which do not demonstrate it — neither `township` nor `library`
 * is an org suffix (`library` is a landmark suffix), so both type `LOCATION`
 * under a settlement oracle. Measured in both languages, and identical in both;
 * the example is wrong, not the behaviour.
 *
 * `settlement` absent means every non-organization span types `NAME` — the
 * behaviour before the tier existed, and the behaviour a caller that wires no
 * oracles still gets.
 */
export function classify(
  tokens: readonly string[],
  settlement?: SettlementOracle,
): CandidateKind {
  const tail = strip(tokens[tokens.length - 1]!.toLowerCase(), ".,");
  if (ORG_SUFFIXES.has(tail)) return "ORGANIZATION";
  if (settlement !== undefined && settlement(tokens.join(" "))) return "LOCATION";
  return "NAME";
}

/**
 * Drop stoplisted tokens, splitting the span where one sits inside it.
 *
 * "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER" is one match, because in an
 * all-caps sentence every token is capitalised. Trimming the edges is not enough —
 * the name is in the middle — so an interior stopword ends the run and starts a
 * new one.
 *
 * The exception is an honorific introducing a name. "Mrs" and "Dr" are in the
 * stoplist so that a bare "Mrs." cannot become a candidate on its own, but "Mrs.
 * Okonkwo" has to stay whole: masking only the surname leaves the relationship and
 * the surname's position in the text.
 */
export function trim(tokens: readonly string[]): string[][] {
  const runs: string[][] = [];
  let current: string[] = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index]!;
    const introducesAName =
      HONORIFIC_SET.has(strip(token.toLowerCase(), ".,")) &&
      index + 1 < tokens.length &&
      !isStop(tokens[index + 1]!);
    if (isStop(token) && !introducesAName) {
      if (current.length > 0) {
        runs.push(current);
        current = [];
      }
      continue;
    }
    current.push(token);
  }
  if (current.length > 0) runs.push(current);
  return runs;
}

/** Offsets at which a sentence begins. */
export function sentenceStarts(text: string): Set<number> {
  const out = new Set<number>();
  for (const match of text.matchAll(SENTENCE_BREAK)) {
    out.add(match.index + match[0].length);
  }
  return out;
}

/**
 * Character ranges of all-caps runs SHORTER than {@link ALLCAPS_RUN}.
 *
 * A long all-caps run is a writer who has stopped using case at all, and the
 * stoplist handles it. A one- or two-word run inside mixed-case prose is
 * emphasis — the informal register's italics — and it is where "SLAM", "WHACK",
 * "LAUGHTER" and "REDACT" came from on real student writing.
 *
 * Single-character tokens are excluded: "I" is upper-case for every writer, and
 * the initials in "J. R. Tolkien" are part of a name rather than a shout.
 */
export function emphasisSpans(text: string): Span[] {
  const runs: Span[][] = [];
  let current: Span[] = [];
  for (const match of text.matchAll(WORD_TOKEN)) {
    const token = match[0];
    if (token.length > 1 && isUpper(token)) {
      current.push([match.index, match.index + token.length]);
      continue;
    }
    if (current.length > 0) {
      runs.push(current);
      current = [];
    }
  }
  if (current.length > 0) runs.push(current);
  return runs
    .filter((run) => run.length < ALLCAPS_RUN)
    .map((run) => [run[0]![0], run[run.length - 1]![1]] as const);
}

/**
 * Character ranges of lines that are section headings, not prose.
 *
 * A heading is title-cased by convention, so **every capital in it is
 * orthographic** and none of it is testimony about any word. This replaces a rule
 * that read the same spans as emphasis, which the data does not support: across
 * the 27 un-scrubbed documents there was not one instance of a writer capitalising
 * an initial letter for emphasis. Emphasis in student prose is ALL CAPS ("this is
 * BULLSHIT") or mixed caps, and {@link emphasisSpans} already has it. What
 * actually generates these spans is layout — "Horses" on its own line, "Horse
 * Families", "Breeds I Like", "My Description of a Horse".
 *
 * Three conditions, all structural and none of them a word list:
 *
 * * short — under {@link HEADING_MAX_CHARS};
 * * no terminal punctuation — a heading is not a sentence;
 * * preceded by a blank line, or first in the document.
 *
 * The blank line is load-bearing rather than belt-and-braces. Body prose here is
 * hard-wrapped, so "The INternet as we know it today first" is a short unpunctuated
 * line too, and without the blank-line test it would read as a heading and take a
 * real name's evidence with it.
 */
export function headingSpans(text: string): Span[] {
  const out: Span[] = [];
  let offset = 0;
  let previousBlank = true; // start of document counts
  for (const line of text.split("\n")) {
    const stripped = line.trim();
    if (
      stripped !== "" &&
      // Code points, not UTF-16 units: the threshold was tuned against Python,
      // which counts characters. Everywhere else in this module a length is
      // offset arithmetic and must stay in the units the text is indexed in.
      Array.from(stripped).length < HEADING_MAX_CHARS &&
      !".!?".includes(stripped[stripped.length - 1]!) &&
      previousBlank
    ) {
      out.push([offset, offset + line.length]);
    }
    previousBlank = stripped === "";
    offset += line.length + 1;
  }
  return out;
}

/** Whether `[start, end)` overlaps any of `spans`. */
export function overlaps(spans: readonly Span[], start: number, end: number): boolean {
  return spans.some(([spanStart, spanEnd]) => start < spanEnd && end > spanStart);
}

/**
 * Character ranges covered by a work title or a fictional character name.
 *
 * Runs against the raw text *before* candidate generation, longest match first,
 * and the ranges it returns are protected exactly like an upstream anonymization
 * marker. That ordering is the whole point: the notability oracle cannot save a
 * title, because generation never hands it one. "To Kill a Mockingbird" is split by
 * the stoplisted "a" into two candidates, and no lookup on either half recovers the
 * book.
 *
 * Matches do not overlap — once a span is claimed the scan resumes after it — so
 * "The Lion King" cannot also match a shorter title inside itself.
 *
 * The 8-token limit is a named limit, not an oversight: the tier's longest entry is
 * 36 tokens, but scanning that far costs 36 lookups per token position for titles
 * nobody writes in an essay. 8 covers "To Kill a Mockingbird"; "The Curious
 * Incident of the Dog in the Night-Time" is 10 and is NOT matched.
 *
 * @param isPrefix - answers "does some title start with these folded tokens?" and
 *   is the automaton this scan walks. It doubles as the first-token prefilter,
 *   since a length-1 prefix *is* a title head. Supplied, the walk stops as soon as
 *   no title can still be reached — one or two tokens on ordinary prose, against
 *   the eight-lookup worst case the length-descending scan paid at every position
 *   whose first word happens to head some title ("the", "a", "my"). Absent, every
 *   length up to {@link TITLE_MAX_TOKENS} is tried and the result is identical;
 *   only the cost differs.
 * @param requiresCapital - in a document that capitalises its proper nouns, a
 *   title's first word is capitalised too. Requiring that skips almost every
 *   position in ordinary prose. Documents that do NOT capitalise are scanned at
 *   every position, because there the case carries nothing.
 */
export function findTitleSpans(
  text: string,
  isTitle: TitleOracle,
  isPrefix?: TitleOracle,
  requiresCapital = false,
): Span[] {
  const tokens = [...text.matchAll(ANY_TOKEN)].map(
    (match) =>
      [
        match.index,
        match.index + match[0].length,
        match[0].toLowerCase().replace(CURLY_APOSTROPHE, "'"),
      ] as const,
  );
  const spans: Span[] = [];
  let index = 0;
  while (index < tokens.length) {
    const [headStart, headEnd] = tokens[index]!;
    if (requiresCapital && !/[A-Z]/.test(text[headStart]!)) {
      index += 1;
      continue;
    }
    let longest = 0;
    let longestEnd = headEnd;
    let key = "";
    const limit = Math.min(TITLE_MAX_TOKENS, tokens.length - index);
    for (let length = 1; length <= limit; length += 1) {
      const [, tokenEnd, tokenKey] = tokens[index + length - 1]!;
      key = length === 1 ? tokenKey : `${key} ${tokenKey}`;
      // Multi-token only: "It" and "Up" must not make ordinary words permanently
      // notable.
      if (length > 1 && isTitle(text.slice(headStart, tokenEnd))) {
        longest = length;
        longestEnd = tokenEnd;
      }
      if (isPrefix !== undefined && !isPrefix(key)) break;
    }
    if (longest > 0) spans.push([headStart, longestEnd]);
    index += longest > 0 ? longest : 1;
  }
  return spans;
}
