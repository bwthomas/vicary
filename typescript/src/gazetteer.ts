/**
 * Offline notability lookup: is this name a public figure, or somebody's cousin?
 *
 * The TypeScript port of `python/src/vicary/gazetteer.py`. Same tiers, same
 * verdicts, same asymmetry — **notable => KEEP, everything else => REDACT** — so
 * a miss here costs precision (a public figure masked) while a false positive
 * costs recall, which is the gap the library exists to close.
 *
 * Candidate generation over capitalised token sequences proposes
 * `Terrence Okonkwo` and `Vincent van Gogh` with equal confidence, because in
 * English prose they are the same thing: two capitalised words. No syntactic
 * feature separates them, so the filter is a set-membership lookup, and this
 * module is the lookup.
 *
 * **A candidate is never split into tokens and tested piecewise.** If it were,
 * `Priya Raghunathan-Bell` would resolve notable off `Bell` and a real student's
 * name would leak. Whole-string matching is what makes the multi-token tier safe
 * to populate broadly, and it is why honorifics are *not* stripped before lookup:
 * `Coach Bramwell` matches no label and therefore redacts, where stripping the
 * title would demote it to a bare surname — the shape most likely to collide with
 * a public figure. The accepted cost is that `President Lincoln` over-redacts.
 *
 * Two tiers are deliberately invisible to {@link GazetteerIndex.notability}:
 * `given` points the other way (a common first name is evidence of a *person*,
 * so on the inbound path it means redact), and `settlement` types a mask rather
 * than granting one. Wiring either into `notability()` would readmit the exact
 * PII the tiers exist to remove, which is why each has its own guard test.
 */

import { loadGazetteer, type Gazetteer } from "./asset.js";

/** Lookup verdicts. Strings rather than an enum so they survive a JSON round trip. */
export const NOT_NOTABLE = "not_notable";
export const TITLE = "title";
export const FULL_NAME = "full_name";
export const ICONIC_SHORT = "iconic_short";
export const PLACE = "place";
/**
 * A nationality or regional adjective — `Cuban`, `Nigerian`, `Bostonian`.
 *
 * Its own verdict rather than folded into PLACE because it is not a place: it is
 * a word *derived* from one, it is the only keep tier with no notability
 * evidence behind it, and eval attribution needs to see it separately to tell
 * whether this tier is where a leak came from.
 */
export const DEMONYM = "demonym";

export type Notability =
  | typeof NOT_NOTABLE
  | typeof TITLE
  | typeof FULL_NAME
  | typeof ICONIC_SHORT
  | typeof PLACE
  | typeof DEMONYM;

/**
 * Every tier this reader knows. An asset carrying a tier absent from this list
 * is refused rather than ignored — a tier added to the builder and forgotten
 * here would read back as an empty set, and an empty KEEP tier redacts
 * everything it was built to protect while presenting as over-aggressive tuning.
 */
export const TIER_NAMES = [
  "full",
  "short",
  "place",
  "given",
  "title",
  "demonym",
  "settlement",
] as const;

export type TierName = (typeof TIER_NAMES)[number];

/**
 * Name particles that may lead a two- or three-token *partial* surname.
 *
 * Kept in sync with the Python runtime's list by a unit test rather than by
 * import; the asset itself carries no copy.
 */
export const PARTICLES: ReadonlySet<string> = new Set([
  "van", "von", "de", "del", "della", "di", "da", "du", "la", "le",
  "les", "der", "den", "ten", "ter", "dos", "das", "al", "bin", "ibn",
  "mac", "mc", "st", "saint", "san", "abu", "ben", "op", "vander",
]);

/**
 * Honorifics and role titles. NOT stripped before lookup — exported because a
 * leading title is a positive signal that a candidate is a real person in the
 * student's life, which is a candidate-generator concern.
 */
export const ROLE_TITLES: ReadonlySet<string> = new Set([
  "mr", "mrs", "ms", "miss", "mx", "dr", "doctor", "prof", "professor",
  "coach", "principal", "officer", "sgt", "sergeant", "capt", "captain",
  "rev", "reverend", "father", "sister", "brother", "pastor", "rabbi",
  "imam", "nurse", "sen", "senator", "rep", "gov", "governor", "mayor",
  "sir", "dame", "lady", "lord", "aunt", "uncle", "grandma", "grandpa",
]);

/**
 * Curly quotes and dashes that NFKD leaves alone.
 *
 * Student prose is full of them — a word processor turns every apostrophe curly
 * — and without this mapping `Lincoln’s` folds to `lincoln s` and misses every
 * tier, silently over-masking a notable name on the most ordinary punctuation
 * there is. Identical to the Python runtime's `_SMART_QUOTES`; a unit test pins
 * them together.
 */
const SMART_QUOTES: ReadonlyMap<string, string> = new Map([
  ["‘", "'"], ["’", "'"], ["ʼ", "'"], ["′", "'"],
  ["“", '"'], ["”", '"'],
  ["‐", "-"], ["‑", "-"], ["‒", "-"], ["–", "-"],
  ["—", "-"], ["−", "-"],
]);

const SMART_QUOTE_PATTERN = new RegExp(
  `[${[...SMART_QUOTES.keys()].join("")}]`,
  "gu",
);

/** Letters and digits, matching Python's Unicode-aware `str.isalnum()`. */
const ALNUM = /[\p{L}\p{N}]/u;

/**
 * The asset is absent, unreadable, or not the shape this reader understands.
 *
 * Raised rather than degrading to "nothing is notable". That fallback is
 * privacy-safe and product-hostile — every public figure in every essay masked —
 * and it looks like a tuning regression rather than a packaging bug for however
 * long it takes somebody to notice.
 */
export class GazetteerAssetError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GazetteerAssetError";
  }
}

/**
 * Fold a name to its lookup key.
 *
 * Accent-stripped, lower-cased, punctuation reduced to spaces. The apostrophe
 * and internal hyphen survive because they belong to the name (`O'Keeffe`,
 * `Raghunathan-Bell`) rather than surrounding it. A trailing possessive is
 * dropped, because `Terrence's older brother` presents the name as `Terrence's`
 * and a lookup that misses on the clitic is a leak.
 *
 * Must fold identically to the Python runtime's `normalize`, because the asset
 * is keyed by one fold and probed by the other. If they drift, every lookup
 * silently misses and the gazetteer answers "nothing is notable" while looking
 * perfectly healthy.
 *
 * **One documented divergence from Python, unreachable on this asset.** Python
 * drops characters whose *canonical combining class* is non-zero; JavaScript
 * exposes no combining class, so this drops `\p{M}` — every mark. The two sets
 * differ only for marks with a combining class of zero (some Thai and Indic
 * vowel signs), which Python turns into a space and this drops outright. That
 * changes a key only when such a mark sits *between* two alphanumerics, which
 * cannot happen in a gazetteer whose keys are Latin-folded, nor in the English
 * prose the conformance frames carry.
 */
export function normalize(name: string): string {
  let folded = name.replace(
    SMART_QUOTE_PATTERN,
    (char) => SMART_QUOTES.get(char) ?? char,
  );
  folded = folded.normalize("NFKD");
  folded = folded.replace(/\p{M}/gu, "");
  folded = folded.toLowerCase();

  let key = Array.from(folded, (char) =>
    ALNUM.test(char) || char === "'" || char === "-" ? char : " ",
  )
    .join("")
    .split(" ")
    .filter((token) => token !== "")
    .join(" ");

  for (const clitic of ["'s", "s'"]) {
    if (key.endsWith(clitic) && Array.from(key).length > clitic.length + 1) {
      key = key.slice(0, -clitic.length).replace(/'+$/, "").trim();
      break;
    }
  }
  return key;
}

const EMPTY: ReadonlySet<string> = new Set<string>();

/**
 * An immutable, loaded notability index over the tiers the asset carries.
 *
 * The derived indices (`titleHeads`, `titlePrefixes`) are memoized on first use
 * rather than taken as constructor arguments, because they are functions of
 * `title` and must never be able to disagree with it.
 */
export class GazetteerIndex {
  readonly full: ReadonlySet<string>;
  readonly short: ReadonlySet<string>;
  readonly place: ReadonlySet<string>;
  /** Common given names. The INVERSE signal — see {@link isCommonGivenName}. */
  readonly given: ReadonlySet<string>;
  /** Works and fictional characters — multi-token only. See {@link isTitle}. */
  readonly title: ReadonlySet<string>;
  /** English demonyms — `cuban`, `nigerian`. A KEEP, see {@link DEMONYM}. */
  readonly demonym: ReadonlySet<string>;
  /** Human settlements. Neither a keep nor a redact signal — the only tier that
   * is neither. See {@link isSettlement}. */
  readonly settlement: ReadonlySet<string>;
  readonly meta: Readonly<Record<string, unknown>>;

  #heads: ReadonlySet<string> | null = null;
  #prefixes: ReadonlySet<string> | null = null;
  #maxTitleTokens: number | null = null;

  constructor(asset: Gazetteer) {
    for (const name of asset.tiers.keys()) {
      if (!(TIER_NAMES as readonly string[]).includes(name)) {
        throw new GazetteerAssetError(
          `unknown gazetteer tier ${JSON.stringify(name)}. Refusing the asset ` +
            `rather than ignoring the tier: a tier this reader drops is a tier ` +
            `that reads back empty, and an empty keep tier redacts everything ` +
            `it was built to protect while looking like over-aggressive tuning.`,
        );
      }
    }
    const tier = (name: TierName): ReadonlySet<string> =>
      asset.tiers.get(name) ?? EMPTY;
    this.full = tier("full");
    this.short = tier("short");
    this.place = tier("place");
    this.given = tier("given");
    this.title = tier("title");
    this.demonym = tier("demonym");
    this.settlement = tier("settlement");
    this.meta = asset.meta;
  }

  /**
   * Entries that can make something KEEP.
   *
   * `given` and `settlement` are excluded on purpose: neither grants a keep, so
   * counting them would inflate the one number that answers "how much notability
   * does this asset carry".
   */
  get entryCount(): number {
    return (
      this.full.size +
      this.short.size +
      this.place.size +
      this.title.size +
      this.demonym.size
    );
  }

  /**
   * First tokens of every title, so a scanner can skip most positions.
   *
   * Without this the title scan costs one lookup per candidate length at every
   * token. With it the common case is a single set miss.
   */
  get titleHeads(): ReadonlySet<string> {
    if (this.#heads === null) {
      const heads = new Set<string>();
      for (const key of this.title) {
        const space = key.indexOf(" ");
        heads.add(space === -1 ? key : key.slice(0, space));
      }
      this.#heads = heads;
    }
    return this.#heads;
  }

  /**
   * Every token-prefix of every title, so a scan can stop the moment no title
   * can still be reached.
   *
   * This is the automaton the per-position n-gram scan was standing in for: a
   * walk advances only while some title still starts with what it has read,
   * which on ordinary prose is one or two tokens. A flat set of pre-joined
   * prefixes rather than a trie of objects — same asymptotics, a fraction of the
   * allocations, built by a single pass over keys that are already normalised.
   */
  get titlePrefixes(): ReadonlySet<string> {
    if (this.#prefixes === null) {
      const prefixes = new Set<string>();
      for (const key of this.title) {
        const tokens = key.split(" ");
        for (let length = 1; length < tokens.length; length += 1) {
          prefixes.add(tokens.slice(0, length).join(" "));
        }
      }
      this.#prefixes = prefixes;
    }
    return this.#prefixes;
  }

  /** Longest title in tokens, so a scanner knows how far to look ahead. */
  get maxTitleTokens(): number {
    if (this.#maxTitleTokens === null) {
      let longest = 0;
      for (const key of this.title) {
        let tokens = 1;
        for (let i = 0; i < key.length; i += 1) {
          if (key[i] === " ") tokens += 1;
        }
        if (tokens > longest) longest = tokens;
      }
      this.#maxTitleTokens = longest;
    }
    return this.#maxTitleTokens;
  }

  /**
   * True when some title starts with (or equals) the token sequence `key`.
   *
   * `key` is an already-folded lookup key — space-joined lower-cased tokens —
   * not raw text. The scan folds each token of the document once and joins,
   * rather than re-normalising a growing substring at every length.
   */
  isTitlePrefix(key: string): boolean {
    return this.titlePrefixes.has(key) || this.title.has(key);
  }

  /**
   * True when `name` is a published work or a fictional character.
   *
   * The full tier is `P31 wd:Q5` — human — so before this tier existed every
   * work title and every fictional character redacted: "Harry Potter taught me
   * about friendship" came back as "{NAME} taught me about friendship".
   *
   * Multi-token by construction, and that is a safety property rather than a
   * convenience. "It", "Up", "Her", "Room", "Brave" and "Cats" are all films; a
   * single-token title tier would make those ordinary words permanently notable,
   * and notable means KEEP, so the cost would land on recall.
   */
  isTitle(name: string): boolean {
    const key = normalize(name);
    return key.includes(" ") && this.title.has(key);
  }

  /**
   * True when `token` is a first name lots of notable people share.
   *
   * Not part of the notability decision, and deliberately not consulted by
   * {@link notability} — it points the other way. A given-name hit is evidence
   * the token names a *person*, which on the inbound path means redact.
   *
   * It exists for the two frames capitalisation cannot reach: `then terrence
   * okonkwo showed up` and `MY BEST FRIEND DESHAWN PRITCHARD` score zero for any
   * candidate generator keyed on capitalisation, by construction. A
   * case-insensitive scan closes that, and a scan needs a list. This is the
   * list; the scan belongs to the candidate generator.
   */
  isCommonGivenName(token: string): boolean {
    const key = normalize(token);
    return key !== "" && !key.includes(" ") && this.given.has(key);
  }

  /**
   * True when `name` is a town, city or village.
   *
   * **Not part of the notability decision, and deliberately not consulted by**
   * {@link notability}. A settlement is a student's hometown, so it must redact;
   * that is the whole reason settlements are subtracted from the place tier.
   * What this answers is the *next* question, asked only about a span already
   * being masked: which placeholder does it get. A host that reads the type back
   * writes "great job describing your trip to {LOCATION}", and before this tier
   * existed it wrote "{NAME}".
   *
   * The failure modes are not symmetric with a keep tier's: a miss types a place
   * `{NAME}` and a false positive types a person `{LOCATION}`. Both are already
   * redacted.
   */
  isSettlement(name: string): boolean {
    const key = normalize(name);
    return key !== "" && this.settlement.has(key);
  }

  /**
   * Classify `name`. One of the verdict constants above.
   *
   * Places are checked first: the string is being judged on what it *names*, and
   * a place-name that is also a surname (`Washington`, `Delaware`) is keepable
   * either way, so resolving it as a place costs nothing and saves a probe.
   */
  notability(name: string): Notability {
    const key = normalize(name);
    if (key === "") return NOT_NOTABLE;
    const tokens = key.split(" ");
    if (this.place.has(key)) return PLACE;
    if (tokens.length === 1) {
      if (this.short.has(key)) return ICONIC_SHORT;
      // After `short`, because a token that is both — none today, but the tiers
      // are rebuilt from a moving upstream — should report the tier that carries
      // notability evidence rather than the one that does not.
      return this.demonym.has(key) ? DEMONYM : NOT_NOTABLE;
    }
    if (tokens.length <= 3 && PARTICLES.has(tokens[0]!) && this.short.has(key)) {
      // "van Gogh", "de Gaulle" — a partial, not a full name, so it is held to
      // the strict short-tier threshold.
      return ICONIC_SHORT;
    }
    if (this.full.has(key)) return FULL_NAME;
    // Titles resolve LAST. "Joan of Arc" and "van Gogh" are both also film
    // titles, and attributing them to the title tier would be true but less
    // specific — the person is who the student wrote about. Either way the
    // verdict is KEEP; only the reported tier changes, and that tier is what
    // eval attribution and telemetry read.
    if (tokens.length > 1 && this.title.has(key)) return TITLE;
    return NOT_NOTABLE;
  }

  isNotable(name: string): boolean {
    return this.notability(name) !== NOT_NOTABLE;
  }
}

let cached: GazetteerIndex | null = null;

/**
 * Load (and memoize) the notability index.
 *
 * Lazy: importing this module reads nothing. Call it at process init to move the
 * decompression off the first request's latency; otherwise the first lookup pays
 * it.
 */
export function load(options: { directory?: string } = {}): GazetteerIndex {
  if (cached !== null && options.directory === undefined) return cached;
  const index = new GazetteerIndex(loadGazetteer(options));
  if (options.directory === undefined) cached = index;
  return index;
}

/** Drop the memoized index. For tests that swap in a fixture asset. */
export function resetCache(): void {
  cached = null;
}

/** True when `name` is a public figure or a public place. `notable => KEEP`. */
export function isNotable(name: string): boolean {
  return load().isNotable(name);
}

/** Which tier matched `name`, for telemetry and for eval attribution. */
export function notability(name: string): Notability {
  return load().notability(name);
}

/** True when `token` is a common given name — a REDACT signal, not a KEEP. */
export function isCommonGivenName(token: string): boolean {
  return load().isCommonGivenName(token);
}

/** True when `name` is a town or city — a TYPING signal, not a keep. */
export function isSettlement(name: string): boolean {
  return load().isSettlement(name);
}

/** True when `name` is a published work or a fictional character. */
export function isTitle(name: string): boolean {
  return load().isTitle(name);
}

/**
 * True when some title *starts* with `token` — the scan's cheap prefilter.
 *
 * Deliberately uses `toLowerCase` rather than {@link normalize}. This runs once
 * per word of every essay, and `normalize` does an NFKD decomposition and a
 * per-character rebuild. The heads are already folded and overwhelmingly plain
 * ASCII, so the only cost is that a title beginning with an accented word fails
 * the prefilter and is not matched. That loses a keep, never a redaction.
 */
export function isTitleHead(token: string): boolean {
  return load().titleHeads.has(token.toLowerCase());
}

/** True when some title starts with the folded token sequence `key`. */
export function isTitlePrefix(key: string): boolean {
  return load().isTitlePrefix(key);
}

/** Longest title in tokens — how far a title scanner must look ahead. */
export function maxTitleTokens(): number {
  return load().maxTitleTokens;
}
