/**
 * Structured entities and interpolated identity — the two legs regex does well.
 *
 * Structured entities (EMAIL, PHONE, SSN, CARD, IP, ZIP, street ADDRESS) are
 * *syntax*, and regex scored **100%** on them in the harness that measured the
 * Bedrock Guardrail at 97.3%. No model beats 100%, and a regex is free and
 * sub-millisecond.
 *
 * The student's own name and school are the NAME/SCHOOL spans that matter most,
 * and they are not being guessed at: the caller knows who submitted the essay.
 * Interpolating those into patterns turns the hardest category for a detector
 * into an exact match.
 *
 * **Order is the contract, not an optimisation.** The first pattern to claim a
 * span wins, and placeholder indices follow mint order, so reordering these
 * tables changes the output bytes even when it changes no verdict. EMAIL and URL
 * run first, because a school-issued address and a profile URL *contain* the
 * writer's name and both are anchored too tightly to take one out of prose;
 * identity next (an address line can otherwise swallow a surname); SSN and CARD
 * before the generic digit runs; ZIP and AGE last, because both are bare digits
 * and would claim characters belonging to a phone, card or address.
 *
 * ## Regex dialect
 *
 * These are ported from Python `re`, which differs from JavaScript `RegExp` in
 * two ways that touch this file. Both are documented at their sites and pinned by
 * a differential test rather than reasoned about:
 *
 * * `\w` and `\b` are Unicode-aware in Python and ASCII-only in JavaScript. Every
 *   `\w` here is written out as an explicit class so the two agree. `\b` is left
 *   as-is in the structured patterns, which is exact for the ASCII
 *   neighbourhoods they match; `wordPattern` cannot make that assumption, since
 *   the literal it wraps is a caller's name, so it spells its boundaries out.
 * * The `u` flag is set only where a pattern needs it — the three patterns built
 *   from `W`, and `wordPattern`. It is deliberately absent elsewhere, because it
 *   bans the identity escapes (`\#`, `\-`) that a direct port of the Python
 *   source produces and buys nothing on a pattern that is ASCII by construction.
 */

import { PlaceholderMinter } from "./minter.js";

// ---------------------------------------------------------------------------
// Structured entities
// ---------------------------------------------------------------------------

/**
 * Python's `\w`, written out.
 *
 * JavaScript's `\w` is `[A-Za-z0-9_]` with or without the `u` flag, where
 * Python's is Unicode-aware. A phone number preceded by an accented letter would
 * match here and not there if this were left as `\w`.
 */
const W = "\\p{L}\\p{N}_";

/**
 * Practical email shape. Deliberately not RFC 5322 — the full grammar matches
 * strings no student writes and is a known source of catastrophic backtracking.
 */
const EMAIL = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b/g;

/**
 * US SSN. Excludes the never-issued ranges (000/666/9xx area, 00 group, 0000
 * serial) so dates and score ranges don't trip it.
 */
const SSN = /\b(?!000|666|9\d{2})\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}\b/g;

/**
 * Candidate payment-card runs, 13–19 digits with optional space/hyphen grouping.
 * Luhn-checked below, because an un-checked pattern this loose eats any long
 * number a student writes.
 */
const CARD_CANDIDATE = /\b(?:\d[ -]?){12,18}\d\b/g;

/**
 * NANP phone, plus common international prefix. Requires separators or parens
 * somewhere so a bare 10-digit number isn't assumed to be a phone.
 */
const PHONE = new RegExp(
  `(?<![${W}-])` +
    `(?:\\+?\\d{1,3}[-.\\s]?)?` + // optional country code
    `(?:` +
    `\\(\\d{3}\\)[-.\\s]*\\d{3}[-.\\s]?\\d{4}` + // (555) 555-5555
    `|\\d{3}[-.\\s]\\d{3}[-.\\s]\\d{4}` + // 555-555-5555 / 555.555.5555
    `)` +
    `(?:\\s*(?:x|ext\\.?|extension)\\s*\\d{1,6})?` +
    `(?![${W}-])`,
  "gu",
);

const IP =
  /\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b/g;

/**
 * US street address: number + street words + a suffix. The suffix list is what
 * keeps this from matching "I ran 3 miles down the road" — a bare
 * number-plus-words pattern has an unacceptable false-positive rate in prose.
 */
const STREET_SUFFIX =
  "(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct" +
  "|Circle|Cir|Place|Pl|Terrace|Ter|Way|Parkway|Pkwy|Highway|Hwy|Trail|Trl" +
  "|Square|Sq|Loop|Alley|Commons)";

const ADDRESS = new RegExp(
  `\\b\\d{1,6}\\s+` +
    `(?:[NSEW]\\.?|North|South|East|West|Northeast|Northwest|Southeast|Southwest)?\\s*` +
    `(?:[A-Z][A-Za-z.'-]*\\s+){0,4}` +
    `${STREET_SUFFIX}\\b\\.?` +
    // `#` unescaped: `\#` is a SyntaxError under the `u` flag and an identity
    // escape without it. The Python source writes `\#`; they mean the same char.
    `(?:\\s*(?:Apt|Apartment|Suite|Ste|Unit|#)\\s*[${W}-]+)?`,
  "gu",
);

/** US ZIP, with the optional +4. Bounded so it can't eat a 5-digit year range. */
const ZIP = /\b\d{5}(?:-\d{4})?\b(?=\s*$|\s*[,.]|\s+[A-Z]{2}\b)/g;

/** Explicit age statements. Bare numbers are not ages; the phrasing is. */
const AGE =
  /\b(?:(?:I\s+am|I'm|aged?|age(?:d)?\s+of)\s+)(\d{1,2})\b(?=\s*(?:years?\s+old)?)|\b(\d{1,2})\s+years?\s+old\b/gi;

/** URLs. Student essays cite them, and a personal profile URL is PII. */
const URL_PATTERN =
  /\bhttps?:\/\/[^\s<>"']+|\bwww\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+[^\s<>"']*/g;

/**
 * Anonymization markers somebody upstream already substituted for real PII.
 *
 * Text arriving with these in it has *already been redacted*, so masking them
 * again destroys information while adding none. The kinds are the closed set the
 * ASAP corpus authors used, measured over the full training set rather than taken
 * from their documentation: 14 distinct kinds across 64,166 occurrences.
 *
 * Why this is in the shipped classifier and not just the eval harness: real
 * student prose contains none of these, so production behaviour is unchanged.
 * What changes is every measurement taken over that corpus — a model trained on
 * it saw these tokens at ~22 per essay, and rewriting them to `{USERNAME}` hands
 * it a token it has never seen.
 */
const UPSTREAM_ANON_KINDS = [
  "CAPS", "NUM", "PERSON", "LOCATION", "ORGANIZATION", "MONTH", "DATE",
  "PERCENT", "TIME", "MONEY", "EMAIL", "STATE", "CITY", "DR",
] as const;

/**
 * @handles. Requires the `@` so it can't eat ordinary words, and a length floor
 * so it can't eat an email's local part (email runs first anyway). The lookahead
 * spares upstream anonymization markers; a genuine all-caps handle colliding with
 * one of those 14 words is the accepted cost, and it is the right way round — a
 * missed handle is one span, and eating `@PERSON1` corrupts every essay in the
 * evaluation corpus.
 */
const USERNAME = new RegExp(
  `(?<![${W}@.])@(?!(?:${UPSTREAM_ANON_KINDS.join("|")})\\d*\\b)[A-Za-z0-9_]{3,30}\\b`,
  "gu",
);

/** Date of birth, explicitly labelled. */
const DOB =
  /\b(?:date\s+of\s+birth|d\.?o\.?b\.?|born\s+on)\s*:?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b/gi;

/** Luhn checksum. Cuts the card pattern's false positives on long numbers. */
export function luhnOk(digits: string): boolean {
  let total = 0;
  for (let i = 0; i < digits.length; i += 1) {
    let d = digits.charCodeAt(digits.length - 1 - i) - 48;
    if (i % 2) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    total += d;
  }
  return total % 10 === 0;
}

/**
 * The two structured patterns that run BEFORE identity interpolation, because
 * their match text can legitimately *contain* the writer's own name:
 * `first.last@district.org` and a profile URL ending in a name slug. Identity
 * interpolation is a literal-name substitution, so running it first left these
 * shredded rather than masked — `{NAME_2}.{NAME_1}{USERNAME_1}.k12.oh.us`
 * instead of `{EMAIL_1}`, with the domain tail surviving in the clear and the
 * span unrestorable on the round trip.
 *
 * Putting them first is safe in the direction that matters, and that asymmetry
 * is the whole argument. Both are anchored on structure a name cannot supply —
 * EMAIL needs an `@` and a dotted TLD, URL needs a scheme or a `www.` — so
 * neither can reach into prose and take a bare surname out of it. Patterns that
 * *could* still run after identity.
 */
const STRUCTURED_BEFORE_IDENTITY: ReadonlyArray<readonly [string, RegExp]> = [
  ["EMAIL", EMAIL],
  ["URL", URL_PATTERN],
];

/**
 * (placeholder kind, pattern) in application order, running AFTER identity
 * interpolation.
 *
 * CARD is handled separately because it needs the Luhn gate; ZIP and AGE run
 * after it for the reason in the module docstring.
 *
 * Numbering is unaffected by which of these two tuples a pattern sits in: the
 * minter counts per kind, so `{EMAIL_1}` is the first email whether emails are
 * matched before or after names.
 */
const STRUCTURED: ReadonlyArray<readonly [string, RegExp]> = [
  ["US_SOCIAL_SECURITY_NUMBER", SSN],
  ["IP_ADDRESS", IP],
  ["PHONE", PHONE],
  ["ADDRESS", ADDRESS],
  ["DATE_OF_BIRTH", DOB],
  ["USERNAME", USERNAME],
];

// ---------------------------------------------------------------------------
// Identity interpolation — the leg regex alone cannot do
// ---------------------------------------------------------------------------

/**
 * Given names that are also ordinary English words.
 *
 * A bare first-name match on one of these destroys prose ("Will you go", "the
 * Art of war", "a Grace period"), so a standalone occurrence is left alone; the
 * full name and the surname still mask. Skewed toward over-inclusion on purpose:
 * a missed first name is one span, a wrongly-masked common word corrupts every
 * essay that uses it.
 */
const AMBIGUOUS_GIVEN_NAMES: ReadonlySet<string> = new Set([
  "art", "bill", "brook", "chase", "dawn", "drew", "faith", "frank",
  "grace", "grant", "hope", "jack", "joy", "june", "mark", "may",
  "mercy", "miles", "nick", "pat", "patience", "penny", "rich",
  "robin", "rose", "sky", "summer", "sunny", "trinity", "will", "wills",
]);

/** Surnames common enough as words to need the same treatment. */
const AMBIGUOUS_SURNAMES: ReadonlySet<string> = new Set([
  "young", "white", "black", "green", "brown", "king", "moore", "price",
  "rich", "stone",
]);

/**
 * Who wrote the essay, so their own PII can be masked exactly.
 *
 * Every field is optional — an absent field contributes no patterns, so a caller
 * that knows only the surname still gets the surname masked.
 */
export interface StudentIdentity {
  firstName?: string | undefined;
  lastName?: string | undefined;
  schoolName?: string | undefined;
  /** Extra strings to mask verbatim (a preferred name, a district). */
  extraNames?: readonly string[] | undefined;
}

export function identityIsEmpty(identity: StudentIdentity): boolean {
  return (
    !identity.firstName &&
    !identity.lastName &&
    !identity.schoolName &&
    (identity.extraNames === undefined || identity.extraNames.length === 0)
  );
}

/**
 * Escape a literal for use inside a pattern.
 *
 * Narrower than Python's `re.escape`, which also escapes spaces and hyphens as
 * `\ ` and `\-`. Both are identity escapes there and neither is special in a
 * JavaScript pattern outside a character class, so escaping them would be a
 * no-op at best and a `u`-flag SyntaxError at worst.
 */
export function escapeLiteral(literal: string): string {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Possessive tails, straight and curly.
 *
 * A word processor turns every apostrophe curly, so the straight forms alone
 * miss the majority of real prose. `s'` is the plural-family form ("the
 * Delacroix-Whitfields' house").
 */
const POSSESSIVE_TAIL = "(?:['’]s|s['’])?";

/**
 * Leading and trailing boundary assertions appropriate to `literal`.
 *
 * `\b` is a boundary only when there is a word character beside it, so a literal
 * *ending* in punctuation — "O'Brien (Jr.)", which is exactly the shape roster
 * data arrives in — can never satisfy a trailing `\b` and silently matches
 * nothing at all. Asserting only on the side that has a word character to assert
 * against masks that literal, and is identical to `\b` for every literal that
 * does not.
 *
 * Written as lookarounds over {@link W} rather than `\b` because JavaScript's
 * `\b` is ASCII-only where Python's is Unicode-aware; these agree with Python
 * for an accented name.
 */
function literalBoundaries(literal: string): [string, string] {
  const isWord = (ch: string) => ch.length > 0 && new RegExp(`^[${W}]$`, "u").test(ch);
  return [
    isWord(literal.slice(0, 1)) ? `(?<![${W}])` : "",
    isWord(literal.slice(-1)) ? `(?![${W}])` : "",
  ];
}

/**
 * Case-insensitive whole-token match for a literal, possessive-tolerant.
 *
 * A bare boundary mis-handles a trailing apostrophe-s, which is exactly how a
 * name appears in student prose ("Sarah's essay"), so the possessive is part of
 * the match and gets masked with the name.
 *
 * The `u` flag is set here, unlike the ASCII-by-construction patterns above,
 * because a caller's surname is arbitrary Unicode: it makes the boundary classes
 * mean what they say and makes `i` fold the way Python's `re.IGNORECASE` does.
 * `escapeLiteral` is already `u`-safe — that is why it is narrower than
 * `re.escape`.
 */
export function wordPattern(literal: string): RegExp {
  const [lead, trail] = literalBoundaries(literal);
  return new RegExp(
    `${lead}${escapeLiteral(literal)}${POSSESSIVE_TAIL}${trail}`,
    "giu",
  );
}

/**
 * `"Lincoln High School"` → `"LHS"`. Null when it would be too short.
 *
 * Students write the acronym far more often than the full name, and a two-letter
 * acronym collides with ordinary words and state codes.
 */
export function schoolAcronym(name: string): string | null {
  const words = name.match(/[A-Za-z][\p{L}\p{N}_'-]*/gu) ?? [];
  const acronym = words.map((word) => word[0]!).join("").toUpperCase();
  return acronym.length >= 3 ? acronym : null;
}

/**
 * Patterns masking this student's own identifying strings.
 *
 * Ordered most-specific-first: the full name is matched before either part of
 * it, so "Jane Quincy-Adams" becomes one `{NAME}` rather than two adjacent
 * placeholders.
 */
export function identityPatterns(
  identity: StudentIdentity,
): Array<readonly [string, RegExp]> {
  const out: Array<readonly [string, RegExp]> = [];
  const first = (identity.firstName ?? "").trim();
  const last = (identity.lastName ?? "").trim();
  const school = (identity.schoolName ?? "").trim();

  if (first && last) {
    out.push(["NAME", wordPattern(`${first} ${last}`)]);
    // "Adams, Jane" — the roster/header order.
    out.push(["NAME", wordPattern(`${last}, ${first}`)]);
  }
  if (last && !AMBIGUOUS_SURNAMES.has(last.toLowerCase())) {
    out.push(["NAME", wordPattern(last)]);
  }
  if (first && !AMBIGUOUS_GIVEN_NAMES.has(first.toLowerCase())) {
    out.push(["NAME", wordPattern(first)]);
  }
  for (const raw of identity.extraNames ?? []) {
    const extra = raw.trim();
    if (extra) out.push(["NAME", wordPattern(extra)]);
  }
  if (school) {
    out.push(["SCHOOL", wordPattern(school)]);
    const acronym = schoolAcronym(school);
    if (acronym !== null) {
      // Case-SENSITIVE for the acronym: lowercasing it would match ordinary
      // words (three-letter acronyms shaped like "was"/"his" are a real hazard).
      out.push(["SCHOOL", new RegExp(`\\b${escapeLiteral(acronym)}\\b`, "g")]);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// The pass
// ---------------------------------------------------------------------------

export interface StructuredResult {
  text: string;
  nMasked: number;
}

/**
 * Mask identity and structured spans, minting through the caller's minter.
 *
 * The minter is passed in rather than created here because it must serve the
 * whole document: candidate generation numbers into the same counters, and a
 * second minter would emit `{NAME_1}` for two different people.
 */
export function maskStructured(
  text: string,
  identity: StudentIdentity,
  minter: PlaceholderMinter,
): StructuredResult {
  if (!text) return { text, nMasked: 0 };

  let masked = text;
  let n = 0;

  // Identity patterns run early: a name is the span most likely to be partially
  // consumed by a looser pattern (an address line can swallow a surname), and
  // masking it first makes that impossible.
  //
  // Early, not first. Email and URL precede it, because those two are the
  // patterns whose own match text contains a name — see
  // STRUCTURED_BEFORE_IDENTITY for why that direction is the safe one.
  for (const [kind, pattern] of [
    ...STRUCTURED_BEFORE_IDENTITY,
    ...identityPatterns(identity),
    ...STRUCTURED,
  ]) {
    const result = minter.substitute(kind, pattern, masked);
    masked = result.text;
    n += result.count;
  }

  // Cards need the Luhn gate, so they can't go through a plain substitution.
  masked = masked.replace(CARD_CANDIDATE, (match) => {
    const digits = match.replace(/\D/g, "");
    if (!luhnOk(digits)) return match;
    n += 1;
    return minter.mint("CREDIT_DEBIT_CARD_NUMBER", match);
  });

  const zip = minter.substitute("ZIP_CODE", ZIP, masked);
  masked = zip.text;
  n += zip.count;

  masked = masked.replace(AGE, (match) => {
    n += 1;
    // Only the digits are the age; the surrounding "I am … years old" is the
    // student's prose and has to survive, so this mints against the digit run
    // rather than the whole match.
    const digits = /\d{1,2}/.exec(match);
    if (digits === null) return match;
    return (
      match.slice(0, digits.index) +
      minter.mint("AGE", digits[0]) +
      match.slice(digits.index + digits[0].length)
    );
  });

  return { text: masked, nMasked: n };
}
