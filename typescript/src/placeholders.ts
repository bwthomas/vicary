/**
 * The host boundary: assert that no placeholder reaches a reader — the
 * TypeScript port of `python/src/vicary/placeholders.py`.
 *
 * vicary masks what it redacts as typed placeholders (`{NAME_1}`,
 * `{LOCATION_2}`, `{EMAIL}`) and a host restores the ones a reader may see.
 * Everything else must never be displayed, and two ways one gets out anyway are
 * invisible to the masker: a model echoes a placeholder the host's restore map
 * does not cover, or paraphrases one — `[NAME_1]`, `{Name_1}`, `{ NAME_1 }`, a
 * bare `NAME_1` — so a host looking for vicary's exact grammar does not see it.
 *
 * `assertNoPlaceholders` raises before the text is shown; `scrubPlaceholders`
 * removes them (a prose sentence carrying one is dropped, a quote elides it with
 * `…`). A placeholder is never rewritten back into a word here — there is no map
 * and nothing is guessed.
 *
 * **The pattern source is byte-identical in all three front doors**, written in
 * the dialect the engines share: ASCII classes rather than `\b`/`\w`/`\d`/`\s`,
 * which mean different things in JavaScript, and case folding spelled out
 * because the inline `(?i:…)` modifier does not parse on Node 20.
 * `conformance/placeholders.json` records the source and the test compares it.
 *
 * Offsets count code points, as in `spans.ts`, so a match position computed
 * here agrees with Python's and Ruby's above the Basic Multilingual Plane.
 */

const KINDS = [
  "ADDRESS", "AGE", "AWS_ACCESS_KEY", "AWS_SECRET_KEY",
  "CREDIT_DEBIT_CARD_CVV", "CREDIT_DEBIT_CARD_EXPIRY",
  "CREDIT_DEBIT_CARD_NUMBER", "DATE_OF_BIRTH", "DRIVER_ID", "EMAIL",
  "IP_ADDRESS", "LICENSE_PLATE", "LOCATION", "MAC_ADDRESS", "NAME",
  "ORGANIZATION", "PASSWORD", "PERSON", "PHONE", "PIN", "SCHOOL", "URL",
  "US_BANK_ACCOUNT_NUMBER", "US_BANK_ROUTING_NUMBER", "US_PASSPORT_NUMBER",
  "US_SOCIAL_SECURITY_NUMBER", "USERNAME", "ZIP_CODE",
];

/**
 * Every placeholder kind vicary or its Guardrail arm can mint, plus `PERSON`,
 * which is what a model writes when it paraphrases `NAME`. Longest first, so a
 * match reports the whole kind.
 */
export const PLACEHOLDER_KINDS: readonly string[] = Object.freeze(
  [...KINDS].sort((a, b) =>
    b.length - a.length || (a < b ? -1 : a > b ? 1 : 0),
  ),
);

/** The kind reported for a `{UPPER_CASE}` token that names no known kind. Its
 * letters are text from the document, so they are not repeated back. */
export const OTHER_KIND = "OTHER";

/** `NAME` -> `[Nn][Aa][Mm][Ee]`: case-insensitive in every dialect. */
function fold(kind: string): string {
  return [...kind]
    .map((c) => (/[A-Z]/.test(c) ? `[${c}${c.toLowerCase()}]` : c))
    .join("");
}

const WORD = "[A-Za-z0-9_]";

/**
 * Anything placeholder-shaped: vicary's own `{KIND}` / `{KIND_n}` for any
 * upper-case kind; a known kind in any case inside `{}`, `[]` or `<>` with
 * stray spaces or tabs; or a bare upper-case `KIND_n` as a whole word.
 * `NAME`, `Ms. Name` and `{x}` do not match; `{name}` does, since in text bound
 * for a reader it is a template that was never filled.
 */
export const PLACEHOLDER_LIKE = new RegExp(
  "\\{[A-Z][A-Z_]*(?:_[0-9]+)?\\}" +
    "|[{\\[<][ \\t]*(?:" + PLACEHOLDER_KINDS.map(fold).join("|") +
    ")(?:_[0-9]+)?[ \\t]*[}\\]>]" +
    "|(?<!" + WORD + ")(?:" + PLACEHOLDER_KINDS.join("|") +
    ")_[0-9]+(?!" + WORD + ")",
  "g",
);

/**
 * A sentence ends at `.`, `!` or `?` followed by whitespace — but not after an
 * honorific, an initial, `e.g.`, `i.e.` or `vs.`, so "Unlike Ms. {NAME_1}, you…"
 * is one sentence and goes whole.
 */
export const SENTENCE_END = new RegExp(
  "(?<![A-Z][a-z]\\.)" +
    "(?<!(?<!" + WORD + ")[A-Z][a-z][a-z]\\.)" +
    "(?<!(?<!" + WORD + ")[A-Z]\\.)" +
    "(?<!(?<!" + WORD + ")e\\.g\\.)" +
    "(?<!(?<!" + WORD + ")i\\.e\\.)" +
    "(?<!(?<!" + WORD + ")vs\\.)" +
    "(?<=[.!?])[ \\t\\n\\r\\f\\v]+",
);

const TRIM = /^[ \t\n\r\f\v]+|[ \t\n\r\f\v]+$/g;
const TRIM_TOKEN = /^[{}[\]<> \t\n\r\f\v]+|[{}[\]<> \t\n\r\f\v]+$/g;
const ELISION = /[ \t\n\r\f\v]*…[ \t\n\r\f\v]*/g;
const ELISION_BEFORE_PUNCT = / … ([.,;:!?])/g;
const NUMBER_SUFFIX = /_[0-9]+$/;

/** What stands in for a placeholder when every sentence of a prose field carries
 * one. Any other kind reads "this". */
export const GENERIC_STAND_INS: Readonly<Record<string, string>> = Object.freeze({
  NAME: "someone",
  PERSON: "someone",
  LOCATION: "a place",
  SCHOOL: "the school",
  ORGANIZATION: "an organization",
});

/** A copy of the pattern for `matchAll`, which starts from the original's
 * `lastIndex` — so a caller who had used the exported global pattern would make
 * a walk over the shared one skip the start of the text. */
function freshPattern(): RegExp {
  return new RegExp(PLACEHOLDER_LIKE.source, PLACEHOLDER_LIKE.flags);
}

/** One placeholder found in text. `start`/`end` count code points. */
export interface PlaceholderMatch {
  readonly text: string;
  readonly kind: string;
  readonly start: number;
  readonly end: number;
}

/** Text with every placeholder removed, and how many were. */
export interface ScrubResult {
  readonly text: string;
  readonly removed: number;
}

/**
 * Text bound for a reader still carries a redaction placeholder. Names the kinds
 * and the count, never the text around them: the message is going to a log.
 */
export class PlaceholderLeakError extends Error {
  readonly kinds: readonly string[];
  readonly count: number;

  constructor(kinds: readonly string[], count: number) {
    const noun = count === 1 ? "placeholder" : "placeholders";
    super(
      `${count} redaction ${noun} would reach a reader ` +
        `(kinds: ${kinds.join(", ")})`,
    );
    this.name = "PlaceholderLeakError";
    this.kinds = kinds;
    this.count = count;
  }
}

/** The kind a placeholder token names: `[ Name_2 ]` -> `NAME`; else `OTHER`. */
export function placeholderKind(token: string): string {
  const kind = token.replace(TRIM_TOKEN, "").toUpperCase().replace(NUMBER_SUFFIX, "");
  return PLACEHOLDER_KINDS.includes(kind) ? kind : OTHER_KIND;
}

/** Every placeholder in `text`, in order, with code-point offsets. */
export function findPlaceholders(text: string): PlaceholderMatch[] {
  if (!text) return [];
  const out: PlaceholderMatch[] = [];
  // One forward walk converting UTF-16 indices to code points as it goes.
  let unit = 0;
  let point = 0;
  const advance = (to: number): number => {
    while (unit < to) {
      const code = text.charCodeAt(unit);
      unit += code >= 0xd800 && code <= 0xdbff && unit + 1 < text.length ? 2 : 1;
      point += 1;
    }
    return point;
  };
  for (const m of text.matchAll(freshPattern())) {
    const start = advance(m.index);
    const end = advance(m.index + m[0].length);
    out.push({ text: m[0], kind: placeholderKind(m[0]), start, end });
  }
  return out;
}

/** Whether `text` carries any placeholder. */
export function hasPlaceholders(text: string): boolean {
  // `search` ignores `lastIndex`, so the shared global pattern carries no state.
  return Boolean(text) && text.search(PLACEHOLDER_LIKE) !== -1;
}

/**
 * Return `text` unchanged, or throw `PlaceholderLeakError`. Call it on the last
 * string a host builds before a reader sees it.
 */
export function assertNoPlaceholders(text: string): string {
  const found = findPlaceholders(text);
  if (found.length > 0) {
    const kinds = [...new Set(found.map((m) => m.kind))].sort();
    throw new PlaceholderLeakError(kinds, found.length);
  }
  return text;
}

/**
 * Remove every placeholder from `text`.
 *
 * Prose (the default): each sentence carrying a placeholder is dropped and the
 * rest are joined with single spaces; if every sentence carries one, each
 * placeholder is replaced by its `GENERIC_STAND_INS` entry instead. With
 * `{ quote: true }`, for a verbatim quote of the writer's own words, each is
 * elided as `…` so the quote stays a quote.
 */
export function scrubPlaceholders(
  text: string,
  options: { readonly quote?: boolean } = {},
): ScrubResult {
  if (!hasPlaceholders(text)) return { text, removed: 0 };
  const removed = [...text.matchAll(freshPattern())].length;
  if (options.quote) {
    const out = text
      .replace(PLACEHOLDER_LIKE, "…")
      .replace(ELISION, " … ")
      .replace(ELISION_BEFORE_PUNCT, " …$1")
      .replace(TRIM, "");
    return { text: out, removed };
  }
  const kept = text
    .replace(TRIM, "")
    .split(SENTENCE_END)
    .filter((sentence) => !hasPlaceholders(sentence));
  if (kept.length > 0) return { text: kept.join(" "), removed };
  return {
    text: text.replace(
      PLACEHOLDER_LIKE,
      (token) => GENERIC_STAND_INS[placeholderKind(token)] ?? "this",
    ),
    removed,
  };
}
