/**
 * Find the person-names a student wrote, so the notability filter can decide.
 *
 * A port of `python/src/vicary/name_candidates.py`, landing in pieces. **What is
 * here so far** is everything that reads the text rather than deciding about it:
 * the patterns that say what a word is, where a sentence begins, which runs of
 * capitals are a shout rather than a name, which lines are headings, which
 * stretches are already redacted; the title scan, which runs against the raw text
 * before any candidate exists; and the capitalisation-habit inference, which is
 * what the routes below will consult instead of obeying a capital.
 *
 * What is NOT here yet: the lowercase route, the sentence-initial corroboration
 * guard, the relation override, and `findCandidates` itself. Nothing in this
 * module is wired into `redact` — the conformance scoreboard is unchanged by it on
 * purpose, because a piece of a detector that moves the number is a piece that was
 * scored before it was checked.
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
 * What a span can be. A span carries *every* tag its evidence supports, because
 * the world does not hand out one nature per name: "Allen Park" is a real town of
 * 28,000 people AND ends in a landmark suffix; "Falls Church" is a real town AND
 * ends in an organisation suffix. Choosing between them is a decision, and the
 * decision lives in {@link PRECEDENCE} where it can be read.
 */
export type Tag = "ORGANIZATION" | "LOCATION" | "LANDMARK" | "PERSON";

/** One row of the precedence table: a tag, and what it decides. */
export interface PrecedenceRow {
  readonly tag: Tag;
  /** Mask or keep. The safety half of the verdict. */
  readonly mask: boolean;
  /** The placeholder kind when masking; `null` on a keeping row. */
  readonly kind: CandidateKind | null;
}

/**
 * The precedence table. The first row whose tag the span carries decides both
 * the mask/keep verdict and the placeholder, and that is the whole
 * classification policy.
 *
 * Pinned against `precedence` in `conformance/primitives.json`, because this is
 * the one part of the detector a port can get wrong while passing every frame:
 * reordering two rows changes which spans survive, and only a colliding span can
 * tell. The reference's frame set had no colliding span for the detector's whole
 * life, which is how 383 real settlements came to be kept.
 *
 * The order, and why each position is where it is:
 *
 * 1. `ORGANIZATION` over `LOCATION` is nearly free — the collision is 16 entries
 *    in a tier of 23,234, and both rows mask, so all that turns on it is which
 *    word a student reads outbound.
 * 2. `LOCATION` over `LANDMARK` is the redact-wins rule. A landmark suffix is a
 *    *guess* from a word ending; settlement membership is a *lookup*. A guess
 *    must not beat a lookup when the guess keeps and the lookup masks.
 * 3. `LANDMARK` over `PERSON` is not a redact-wins violation, because `PERSON` is
 *    the absence of evidence rather than evidence. Keeping "Lincoln Memorial" is
 *    the row's purpose.
 * 4. `PERSON` last, and always matching, so the table is total.
 */
export const PRECEDENCE: readonly PrecedenceRow[] = [
  { tag: "ORGANIZATION", mask: true, kind: "ORGANIZATION" },
  { tag: "LOCATION", mask: true, kind: "LOCATION" },
  { tag: "LANDMARK", mask: false, kind: null },
  { tag: "PERSON", mask: true, kind: "NAME" },
];

/**
 * Every tag the evidence supports for this span. Decides nothing.
 *
 * Separated from the decision on purpose: this reads evidence and
 * {@link PRECEDENCE} applies policy, so changing what we do about a collision is
 * an edit to a table rather than to a detector.
 *
 * `settlement` absent means the `LOCATION` tag is never reachable — the
 * behaviour before the tier existed, and the behaviour a caller that wires no
 * oracles still gets.
 */
export function classifyTags(
  tokens: readonly string[],
  settlement?: SettlementOracle,
): ReadonlySet<Tag> {
  // PERSON is unconditional: the span reached the table because it is
  // name-shaped, so the tag records that there is no evidence *beyond* the
  // shape. Making it unconditional is what makes the table total.
  const tags = new Set<Tag>(["PERSON"]);
  // No tokens is no evidence, which is what a bare PERSON tag already says.
  if (tokens.length === 0) return tags;
  const tail = strip(tokens[tokens.length - 1]!.toLowerCase(), ".,");
  if (ORG_SUFFIXES.has(tail)) tags.add("ORGANIZATION");
  if (settlement !== undefined && settlement(tokens.join(" "))) tags.add("LOCATION");
  // Multi-token only: a bare "Park" is a surname far more often than a place.
  if (tokens.length > 1 && LANDMARK_SUFFIXES.has(tail)) tags.add("LANDMARK");
  return tags;
}

/** The first row of {@link PRECEDENCE} this span carries the tag for. */
export function resolve(tags: ReadonlySet<Tag>): PrecedenceRow {
  for (const row of PRECEDENCE) {
    if (tags.has(row.tag)) return row;
  }
  // Unreachable: PERSON is unconditional, so the last row always matches.
  throw new Error(`no precedence row matched ${[...tags].sort().join(",")}`);
}

/**
 * Which placeholder kind this span would mask as.
 *
 * The kind half of the table's verdict. A span the table *keeps* has no
 * placeholder, and types `NAME` here as an inert default — nothing reads it,
 * because the masking pass asks the same table for the verdict first.
 *
 * Note on the reference's docstring, which is wrong in a way worth recording:
 * it states the org-over-settlement rule with "Springfield Township" and "Akron
 * Public Library", and neither demonstrates it — neither `township` nor
 * `library` is an org suffix (`library` is a landmark suffix), so both type
 * `LOCATION` under a settlement oracle. Measured in both languages and identical
 * in both; the example is wrong, not the behaviour.
 */
export function classify(
  tokens: readonly string[],
  settlement?: SettlementOracle,
): CandidateKind {
  return resolve(classifyTags(tokens, settlement)).kind ?? "NAME";
}

/**
 * Whether `name` carries the `LANDMARK` tag — a suffix guess, no lookup.
 *
 * A tag, not a verdict. It says the span *looks* like a landmark, which is all a
 * word ending can say; whether that keeps the span is {@link PRECEDENCE}'s call,
 * and a settlement lookup outranks it.
 */
export function isPublicLandmark(name: string): boolean {
  return classifyTags(name.split(/\s+/).filter(Boolean)).has("LANDMARK");
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

// ---------------------------------------------------------------------------
// How this writer uses capital letters
// ---------------------------------------------------------------------------

/**
 * A capitalised word that is *not* sentence-initial. "I" is excluded because
 * every writer capitalises it whether or not they capitalise names, so it is the
 * one capital that says nothing about their habits.
 */
export const MID_SENTENCE_CAP = /(?<=[a-z,;:]\s)([A-Z][a-z]{2,})/g;

/**
 * Mid-sentence capitals above which a document is taken to mark its proper nouns
 * with capitals — at which point a *lowercase* token is evidence against a name.
 *
 * Measured on 36 un-scrubbed essay documents (~3,300 chars each, Project
 * Gutenberg) against a lower-cased copy of the same text: as written the median is
 * 10.5 and 35/36 documents are non-zero; lower-cased every document is 0. Clean
 * separation, so the threshold is not delicate — 2 rather than 1 only to tolerate
 * a single stray capital.
 *
 * **A rate was measured against this floor and rejected.** A count is
 * length-blind, so the obvious repair is marks per 1,000 characters — and on the
 * 27 un-scrubbed student documents that does not separate the deciding band, it
 * only re-orders it. Both documents sitting at exactly 2 marks with the closest
 * rates are decided *the wrong way round* by a rate: `141-693` marks "Powerball"
 * twice in 3,478 characters (0.58 per 1k, a genuine capitaliser) and `141-433`
 * marks "The" and "There" in 1,144 (1.75 per 1k, both artefacts of a sentence
 * break the detector missed). A rate threshold demotes the real one and promotes
 * the false one. What actually separates them is the *content* of the mark, which
 * is per-token evidence — so the band falls through to
 * {@link midSentenceCapitals} rather than being decided at document level, and
 * that is what `INCONSISTENT` is for.
 */
export const MARKS_PROPER_NOUNS_MIN = 2;

/**
 * A sentence opening on a lower-case letter, which is the writer telling us
 * directly that they are not keeping standard capitalisation. Matched at the start
 * of the text as well as after a sentence break.
 */
export const LOWERCASE_SENTENCE_START = /(?:^|(?<=[.!?]\s))\s*[a-z]/g;

/**
 * A bare lower-case first-person "i" — the other unambiguous tell, and the one
 * that survives a writer who does capitalise sentence openings. Non-global,
 * because it is only ever searched: a global regex carries `lastIndex` between
 * calls and would answer differently on its second question about the same text.
 */
export const BARE_LOWERCASE_I = new RegExp(
  `${NOT_WORD_BEFORE}i(?![\\p{L}\\p{N}_])`,
  "u",
);

/**
 * One terminal-punctuation unit. The denominator for the drop rate, and it has to
 * be this rather than {@link sentenceStarts}: that counts `\n` as a break too, and
 * these documents are hard-wrapped, so it would report a wrapped line as a
 * sentence and halve the rate. This is the population
 * {@link LOWERCASE_SENTENCE_START} actually draws from.
 */
export const SENTENCE_UNIT = /[^.!?]+[.!?]*/g;

/**
 * Fraction of sentence openings that must be lower-case before a writer who *does*
 * mark proper nouns is read as also dropping capitals, rather than as having made
 * a typo. Read {@link capitalisationHabit} for the reason this is consulted on
 * only one side of the floor — it is the load-bearing half.
 *
 * On the 27 un-scrubbed student documents the boolean "any lower-case opening"
 * fires on 8, and the openings split in two with a gap between 12.5% and 7%:
 *
 * * habit — `my-fabit-book` 2 of 3 openings (67%), `141-433` 6 of 35 (17%),
 *   `121-816` 1 of 8 (12.5%);
 * * not — `my-first-tooth-gone` 1 of 14 (7%), `marching-to-his-own-beat` 3 of 60
 *   (5%), `141-140` 2 of 41 (5%), `121-502` 1 of 25 (4%).
 *
 * Every opening in the second group was read, and they are line wraps, citations
 * and one stylistic `Boy! did we cry`. `marching-to-his-own-beat` is an NWP anchor
 * paper that marks 26 proper nouns correctly; the boolean called it a writer who
 * does not keep standard capitalisation, on three artefacts.
 */
export const DROPS_CAPITALS_MIN_RATE = 0.1;

/**
 * What a document has told us about how its writer uses capital letters.
 *
 * This replaces two booleans — "does it capitalise its proper nouns" and "does it
 * drop standard capitals" — which were consulted separately and *contradict each
 * other on 7 of 27 un-scrubbed student documents*. `141-433` has two mid-sentence
 * capitals and six lower-case sentence openings, so it was simultaneously a writer
 * who capitalises and a writer who does not, and whichever predicate a call site
 * happened to read decided the treatment.
 *
 * Four states, because the two signals are independent and all four cells occur:
 *
 * `consistent`
 *     Marks its proper nouns, and does not drop sentence capitals. A lower-case
 *     token here is evidence *against* a name. 15 of the 27.
 * `inconsistent`
 *     Does both. This is the writer the booleans had no cell for, and **both
 *     document-level treatments are wrong for them** — suppressing the lowercase
 *     route loses the names they wrote lower-case, and opening it wide fires on
 *     ordinary words. So there is no document-level answer here on purpose: the
 *     band falls through to per-token evidence ({@link midSentenceCapitals}),
 *     which is the right granularity and already existed. 4 of the 27.
 * `lowercase`
 *     Drops capitals and marks nothing. The given-name tier is the only handle
 *     left, and the lowercase route runs without corroboration. 1 of the 27.
 * `silent`
 *     Says nothing either way: no proper nouns to capitalise, and no dropped
 *     openings. **Silence is not consent.** Reading it as consent is what put
 *     "line circles" and "tone toward" in front of a student, because a 108-290
 *     character feedback field is ordinary prose with nothing in it to capitalise.
 *     Treated like `inconsistent`: per-token evidence, never the permissive path.
 *     7 of the 27.
 *
 * **No metric moves.** Held-out recall, KEEP precision, round-trip, over-firing
 * and Census exposure are identical to the two booleans on every arm, and that is
 * the intended result rather than a disappointment: this replaces two predicates
 * that disagreed with one that cannot, and the states they *both* got right are
 * most of them. What changes is that `inconsistent` now has a name and a defined
 * treatment, so the next rule to weigh case has somewhere to attach.
 *
 * A string union with two predicate functions rather than a class with two
 * properties, matching how `gazetteer.ts` spells `Notability`. The reference's
 * `CapitalisationHabit.CONSISTENT.value` is this string, so the two languages can
 * be diffed on the wire without a mapping table in between.
 */
export const CONSISTENT = "consistent";
export const INCONSISTENT = "inconsistent";
export const LOWERCASE = "lowercase";
export const SILENT = "silent";

export type CapitalisationHabit =
  | typeof CONSISTENT
  | typeof INCONSISTENT
  | typeof LOWERCASE
  | typeof SILENT;

/**
 * Whether the writer puts capitals on proper nouns at all.
 *
 * True for both `consistent` and `inconsistent`: an inconsistent writer who
 * capitalised "Vinny" and left "cousin" lower-case made a choice, and that choice
 * is testimony. It is the *absence* of a capital that means nothing in a
 * `lowercase` or `silent` document.
 */
export function marksProperNouns(habit: CapitalisationHabit): boolean {
  return habit === CONSISTENT || habit === INCONSISTENT;
}

/** Whether the writer drops standard capitals as a habit. */
export function dropsCapitals(habit: CapitalisationHabit): boolean {
  return habit === INCONSISTENT || habit === LOWERCASE;
}

/**
 * Classify how this document's writer uses capitals. See {@link CapitalisationHabit}.
 *
 * Two independent readings, each taken from evidence the writer supplied rather
 * than inferred from what is missing.
 *
 * **Does it mark proper nouns?** Count mid-sentence capitals, excluding any that
 * fall inside a heading. Sentence-initial capitals are not counted at all: a
 * student who capitalises the start of each sentence but not the names inside them
 * is exactly the case the lowercase route exists for, and counting those would
 * suppress the route on them. The heading exclusion brings this counter into line
 * with {@link midSentenceCapitals}, which the inconsistent band falls through to —
 * the two channels were reading the same evidence through different rules, which is
 * a defect whatever the threshold is. Its measured effect on the 27 documents is
 * **none**: it lowers five counts (`horses` 52 to 27 is the largest) and none of
 * them crosses the floor. It is a precision repair, not a fix, and is recorded as
 * one.
 *
 * **Does it drop standard capitals?** A bare lower-case "i" anywhere, or a
 * lower-case sentence opening. Both are the writer's own doing rather than an
 * inference from what is missing.
 *
 * **The rate is consulted on only one side of the floor, and that asymmetry is the
 * measurement, not an oversight.** Above the floor there is a presence signal to
 * weigh the drop side against, so the rate can say "26 marks and 3 dropped openings
 * is a writer who typed three typos" — which is `marching-to-his-own-beat`, an NWP
 * anchor paper the boolean libelled. Below the floor there is nothing to weigh it
 * against, and applying it there **costs a held-out name**: the `lowercase-writing`
 * fixture frame rides in two carrier essays, and in 20739 (one mid-sentence
 * capital, one lower-case opening in 59 sentences, no bare "i") a 1.7% drop rate
 * demoted a genuine lower-case-writing document to `silent`, withdrew the
 * permissive path, and leaked "terrence okonkwo". Held-out recall 28/28 to 27/28
 * for one span of over-firing — the wrong direction for a tool whose whole bias is
 * over-redact rather than leak.
 *
 * So below the floor the document has given us one bit and it is taken
 * conservatively: any tell at all means `lowercase`. The cost of that is
 * `my-first-tooth-gone` staying on the permissive path when it is really a
 * capitaliser with nothing to capitalise — and that cost was measured at **zero**
 * spans, because its only candidate is "Boy" from the capitalised route under
 * either reading. A guard whose failing case costs nothing, against a rate whose
 * correction costs a name, is not a guard worth having.
 *
 * @param headings - spans whose capitals are orthographic because title case put
 *   them there. Passed in rather than computed so the arm that turns the heading
 *   rule off stays coherent — with it off, this reads headings as prose, exactly
 *   like every other consumer of that flag.
 */
export function capitalisationHabit(
  text: string,
  headings: readonly Span[] = [],
): CapitalisationHabit {
  let marks = 0;
  for (const match of text.matchAll(MID_SENTENCE_CAP)) {
    // The lookbehind consumes nothing, so group 1 starts where the match does —
    // which is what the reference's `m.start(1)` resolves to as well.
    if (!overlaps(headings, match.index, match.index + match[0].length)) {
      marks += 1;
    }
  }
  const openings = [...text.matchAll(LOWERCASE_SENTENCE_START)].length;
  // The bare "i" stays a boolean on both sides. It is the higher-precision tell —
  // 26 of the 27 un-scrubbed documents have none at all, and the one that does has
  // nine — so there is no noise for a rate to remove.
  const bareI = BARE_LOWERCASE_I.test(text);

  if (marks >= MARKS_PROPER_NOUNS_MIN) {
    let sentences = 0;
    for (const match of text.matchAll(SENTENCE_UNIT)) {
      if (match[0].trim() !== "") sentences += 1;
    }
    const habitual = openings / Math.max(1, sentences) >= DROPS_CAPITALS_MIN_RATE;
    return bareI || habitual ? INCONSISTENT : CONSISTENT;
  }
  return bareI || openings > 0 ? LOWERCASE : SILENT;
}

/**
 * Lower-cased forms of every word this document capitalises mid-sentence.
 *
 * The document's own testimony about a particular word, which is the graded
 * version of {@link capitalisationHabit}, and what its `inconsistent` state falls
 * through to. A writer who put a capital on "Cade" somewhere other than a sentence
 * start has told us "Cade" is a name in this document; one who only ever writes
 * "Eventually" after a full stop has told us nothing, because orthography would
 * have put that capital there anyway.
 *
 * An entirely upper-case token is excluded, and that exclusion is load-bearing
 * rather than tidy. Without it "SLAM" corroborates itself — the token is its own
 * mid-sentence capital — so every emphasis shout would clear the bar the emphasis
 * rule had just raised. A capital is testimony only where the writer had a
 * lower-case alternative and declined it.
 *
 * A heading is excluded for the same reason: it is title-cased, so its non-initial
 * capitals are orthographic too. Counting them let "The First Horses" vouch for
 * "Horses" as a name — the heading corroborating itself, one line removed.
 */
export function midSentenceCapitals(
  text: string,
  starts: ReadonlySet<number>,
  headings: readonly Span[] = [],
): Set<string> {
  const out = new Set<string>();
  for (const match of text.matchAll(WORD_TOKEN)) {
    const token = match[0];
    if (starts.has(match.index) || !/[A-Z]/.test(token[0]!)) continue;
    if (token.length > 1 && isUpper(token)) continue;
    if (overlaps(headings, match.index, match.index + token.length)) continue;
    out.add(strip(token.toLowerCase(), "'’"));
  }
  return out;
}

/**
 * Whether this span rests on a capital that had to be there anyway.
 *
 * Three shapes are excluded, because each carries evidence beyond the capital: a
 * multi-token span ("Sadie Johnson") is a *shape*; an honorific in front of the
 * name is a relationship; and a capital in the middle of a sentence is a choice
 * the writer made rather than one orthography made for them.
 *
 * A heading is the exception to the first of those. Title case capitalises every
 * word, so "Horse Families" is not a shape there — the second capital is as
 * orthographic as the first, and a multi-token span inside a heading has no more
 * evidence than a single-token one. So the multi-token exemption does not apply
 * inside a heading, and "My Brother Terrence Okonkwo" as a heading is still
 * caught: it needs the given-name tier rather than its own capitals, which is
 * exactly the bar every other unevidenced capital has to clear.
 */
export function capitalIsTheOnlyEvidence(
  tokens: readonly string[],
  start: number,
  starts: ReadonlySet<number>,
  emphasis: readonly Span[],
  headings: readonly Span[] = [],
): boolean {
  const end = start + tokens.join(" ").length;
  const inHeading = overlaps(headings, start, end);
  if (tokens.length > 1 && !inHeading) return false;
  if (inHeading) return true;
  if (overlaps(emphasis, start, end)) return true;
  return starts.has(start);
}
