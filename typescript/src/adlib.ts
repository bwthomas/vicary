/**
 * Adlib: stand an invented value in for every redacted span, instead of a placeholder.
 *
 * The TypeScript port of `python/src/vicary/adlib.py` and its `render_adlib`. The
 * README's "Adlib" section is the byte-level spec and `conformance/adlib.json`
 * the acceptance bar: same document and seed, same bytes as Python.
 *
 * It is a rendering mode, not a detector: it runs over the finished placeholder
 * output and its restore map, so the spans redacted are the placeholder mode's.
 *
 * Porting notes. Every offset and length here is in code points, as Python counts.
 * Python's `\w`, `\d`, `\s`, `isalpha`, `isdigit`, `islower`, `isupper` and
 * `casefold` are Unicode-aware; the helpers below reproduce them (`pyunicode.ts`
 * carries the two tables JavaScript has no built-in for). Python's `$` also
 * matches before a final newline, written here as `(?=\n?$)`.
 *
 * Never log the mapping. It is the de-identification key.
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { assetSearchPath } from "./asset.js";
import { isCommonGivenName, isNotable } from "./gazetteer.js";
import { load as loadLexicon, STOP_WORD_LISTS } from "./lexicon.js";
import { placeholderKind } from "./placeholders.js";
import { CASEFOLD_EXCEPTIONS, NON_DECIMAL_DIGITS } from "./pyunicode.js";
import type { RedactionResult } from "./redact.js";
import { deriveSpans, PLACEHOLDER, type RedactionSpan } from "./spans.js";

// ---------------------------------------------------------------------------
// Python string semantics
// ---------------------------------------------------------------------------

/** Python `\s` / `str.isspace`. */
const SP = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
/** Python `\w`: letters, numbers, underscore. */
const W = "\\p{L}\\p{N}_";
/** Python `[^\W\d_]`: a word character that is not a decimal digit or `_`. */
const LET = "\\p{L}\\p{Nl}\\p{No}";
/** Python `\d`: Unicode decimal digits. */
const D = "\\p{Nd}";
/** Python's `$` without MULTILINE: end, or before a final newline. */
const EOS = "(?=\\n?$)";

const ND = /^\p{Nd}$/u;
const DIGIT = new RegExp(`^[\\p{Nd}${NON_DECIMAL_DIGITS}]$`, "u");
const ALPHA = /^\p{L}$/u;
const SPACE = new RegExp(`^[${SP}]$`, "u");
const UPPER_OR_TITLE = /[\p{Uppercase}\p{Lt}]/u;
const LOWER_OR_TITLE = /[\p{Lowercase}\p{Lt}]/u;
const LOWERCASE = /\p{Lowercase}/u;
const UPPERCASE = /\p{Uppercase}/u;

const cps = (s: string): string[] => Array.from(s);
const cpLen = (s: string): number => {
  let n = 0;
  for (const _ of s) n += 1;
  return n;
};
const every = (s: string, re: RegExp): boolean => s !== "" && cps(s).every((c) => re.test(c));
const isDigitChar = (c: string): boolean => DIGIT.test(c);
const pyIsDigit = (s: string): boolean => every(s, DIGIT);
const pyIsAlpha = (s: string): boolean => every(s, ALPHA);
const pyIsSpace = (s: string): boolean => every(s, SPACE);
const isAscii = (s: string): boolean => /^[\x00-\x7f]*$/.test(s);

function pyIsLower(s: string): boolean {
  let cased = false;
  for (const c of s) {
    if (UPPER_OR_TITLE.test(c)) return false;
    if (!cased && LOWERCASE.test(c)) cased = true;
  }
  return cased;
}

function pyIsUpper(s: string): boolean {
  let cased = false;
  for (const c of s) {
    if (LOWER_OR_TITLE.test(c)) return false;
    if (!cased && UPPERCASE.test(c)) cased = true;
  }
  return cased;
}

/** Python `%`: the result takes the divisor's sign. */
const pymod = (a: number, n: number): number => ((a % n) + n) % n;

/** The value of a Unicode decimal digit: every `Nd` run starts at its zero. */
function digitValue(c: string): number {
  const cp = c.codePointAt(0) as number;
  let k = 0;
  while (cp - 1 - k >= 0 && ND.test(String.fromCodePoint(cp - 1 - k))) k += 1;
  return k % 10;
}

/** Python `int()` over a string of decimal digits; anything else raises. */
function pyInt(s: string): number {
  let n = 0;
  if (s === "") throw new Error("invalid literal for int() with base 10: ''");
  for (const c of s) {
    if (!ND.test(c)) throw new Error(`invalid literal for int() with base 10: '${s}'`);
    n = n * 10 + digitValue(c);
  }
  return n;
}

const pad = (n: number, width: number): string => String(n).padStart(width, "0");
const rstripDots = (s: string): string => s.replace(/\.+$/u, "");

/** NFC, Unicode casefold, then `’` and `ʼ` read `'`. */
export function fold(token: string): string {
  let out = "";
  for (const c of token.normalize("NFC")) out += CASEFOLD_EXCEPTIONS[c] ?? c.toLowerCase();
  return out.replace(/[’ʼ]/gu, "'");
}

function letters(s: string): string[] {
  return s.match(new RegExp(`[${LET}]+`, "gu")) ?? [];
}

// ---------------------------------------------------------------------------
// Pools
// ---------------------------------------------------------------------------

/** The pool names, as files `pools/<name>.txt`. */
export const POOL_NAMES = ["given", "org", "place", "school", "street", "surname"] as const;

/** Where `pools/<name>.txt` is read from: the vendored copy, else the checkout's. */
export function poolPath(name: string): string {
  const here = dirname(fileURLToPath(import.meta.url));
  const dirs = [
    ...assetSearchPath().map((d) => join(d, "pools")),
    resolve(here, "..", "..", "python", "src", "vicary", "pools"),
    resolve(here, "..", "..", "..", "python", "src", "vicary", "pools"),
  ];
  for (const dir of dirs) {
    const candidate = join(dir, `${name}.txt`);
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(`no adlib pool ${name}.txt found. Looked in: ${dirs.join(", ")}`);
}

const poolCache = new Map<string, readonly string[]>();

/** One pool: one entry per non-blank line, stripped, file order (never re-sorted). */
export function loadPool(name: string): readonly string[] {
  let pool = poolCache.get(name);
  if (pool === undefined) {
    const raw = readFileSync(poolPath(name), "utf8");
    const strip = new RegExp(`^[${SP}]+|[${SP}]+$`, "gu");
    pool = Object.freeze(
      raw
        .split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/u)
        .map((line) => line.replace(strip, ""))
        .filter((line) => line !== ""),
    );
    poolCache.set(name, pool);
  }
  return pool;
}

const GIVEN = "given";
const SURNAME = "surname";

// ---------------------------------------------------------------------------
// Constants (the README's tables)
// ---------------------------------------------------------------------------

const FORMAL_TITLES: ReadonlySet<string> = new Set([
  "mr", "mrs", "ms", "miss", "mx", "dr", "doctor", "prof", "professor",
  "coach", "principal", "officer", "sgt", "sergeant", "capt", "captain",
  "rev", "reverend", "pastor", "rabbi", "imam", "nurse", "sen", "senator",
  "rep", "gov", "governor", "mayor", "sir", "dame", "madam", "chief", "fr",
  "mister", "judge", "detective", "lord", "lady",
]);
const KIN_TITLES: ReadonlySet<string> = new Set([
  "aunt", "auntie", "uncle", "grandma", "grandpa", "grandmother",
  "grandfather", "cousin", "sister", "brother", "father", "mother",
]);
const TITLES: ReadonlySet<string> = new Set([...FORMAL_TITLES, ...KIN_TITLES]);

const APOSTROPHES = "'’ʼ";
const POSSESSIVE = new RegExp(`(?:[${APOSTROPHES}][sS]|(?<=[sS])[${APOSTROPHES}])${EOS}`, "u");
const SEPARATORS_SPLIT = new RegExp(`([${SP}]+|,)`, "u");
const SEPARATORS_FULL = new RegExp(`^(?:[${SP}]+|,)$`, "u");
const EDGE = new RegExp(`^([^${W}]*)([\\s\\S]*?)([^${W}]*)$`, "u");
const WHITESPACE_SPLIT = new RegExp(`([${SP}]+)`, "u");

export const NAME = "NAME";
export const SCHOOL = "SCHOOL";
export const ORGANIZATION = "ORGANIZATION";
export const LOCATION = "LOCATION";

const DESCRIPTIVE: ReadonlySet<string> = new Set([
  "elementary", "middle", "high", "school", "schools", "academy", "college",
  "university", "prep", "preparatory", "charter", "montessori", "institute",
  "junior", "senior", "jr", "sr", "community", "christian", "catholic",
  "day", "district", "unified", "isd", "usd", "magnet", "primary",
  "secondary", "intermediate", "kindergarten", "preschool", "early",
  "learning", "center", "centre", "club", "inc", "llc", "ltd", "co", "corp",
  "corporation", "company", "church", "temple", "mosque", "synagogue",
  "foundation", "library", "museum", "hospital", "clinic", "team", "league",
  "society", "association", "council", "scouts", "troop", "band", "choir",
  "orchestra", "park", "lake", "river", "creek", "county", "city", "town",
  "township", "village", "mount", "mt", "valley", "beach", "island",
  "springs", "falls", "heights", "hills", "bay", "harbor", "port", "north",
  "south", "east", "west", "new", "saint", "st", "fort", "the", "of", "and",
  "&", "for", "at", "in", "on", "state", "national", "public",
]);
const SCHOOL_CUES: ReadonlySet<string> = new Set(["elementary", "school", "academy", "college", "university"]);
const SCHOOL_TAIL_CUES: ReadonlySet<string> = new Set(["high", "middle"]);
const ORG_CUES: ReadonlySet<string> = new Set([
  "inc", "llc", "ltd", "corp", "corporation", "company", "club", "church",
  "foundation", "library", "museum", "society", "association", "league",
  "council", "center", "centre",
]);
const RUN_POOLS: Readonly<Record<string, string>> = { SCHOOL: "school", ORGANIZATION: "org", LOCATION: "place" };

const DIRECTIONS: ReadonlySet<string> = new Set([
  "n", "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east",
  "west", "northeast", "northwest", "southeast", "southwest",
]);
const STREET_SUFFIXES: ReadonlySet<string> = new Set([
  "street", "st", "avenue", "ave", "boulevard", "blvd", "road", "rd",
  "drive", "dr", "lane", "ln", "court", "ct", "circle", "cir", "place", "pl",
  "terrace", "ter", "way", "parkway", "pkwy", "highway", "hwy", "trail",
  "trl", "square", "sq", "loop", "alley", "commons",
]);
const UNIT = new RegExp(
  `([${SP}]*(?:(?:Apt|Apartment|Suite|Ste|Unit)\\.?|#)[${SP}]*)([${W}-]+)([^${W}]*)${EOS}`,
  "iu",
);

/** Mail providers kept verbatim: naming one identifies nobody. */
export const PROVIDERS: ReadonlySet<string> = new Set([
  "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
  "aol.com", "protonmail.com", "proton.me", "live.com", "msn.com", "me.com",
  "mac.com", "comcast.net",
]);
/** The only URLs adlib changes: per-user shapes. First matching entry wins. */
export const URL_SHAPES: readonly (readonly [string, string])[] = [
  ["youtube.com", "/@{h}"], ["youtube.com", "/c/{h}"],
  ["youtube.com", "/channel/{i}"], ["youtube.com", "/user/{h}"],
  ["instagram.com", "/stories/{h}"], ["instagram.com", "/{h}"],
  ["twitter.com", "/{h}/status/{i}"], ["twitter.com", "/{h}"],
  ["x.com", "/{h}/status/{i}"], ["x.com", "/{h}"],
  ["tiktok.com", "/@{h}/video/{i}"], ["tiktok.com", "/@{h}"],
  ["threads.net", "/@{h}"], ["threads.com", "/@{h}"],
  ["facebook.com", "/profile.php?id={i}"], ["facebook.com", "/people/{h}/{i}"],
  ["facebook.com", "/{h}"],
  ["linkedin.com", "/in/{h}"], ["linkedin.com", "/pub/{h}"],
  ["github.com", "/orgs/{h}"], ["github.com", "/sponsors/{h}"], ["github.com", "/{h}"],
  ["reddit.com", "/user/{h}"], ["reddit.com", "/u/{h}"],
  ["old.reddit.com", "/user/{h}"], ["old.reddit.com", "/u/{h}"],
  ["medium.com", "/@{h}"], ["mastodon.social", "/@{h}"],
  ["bsky.app", "/profile/{h}"], ["snapchat.com", "/add/{h}"],
  ["pinterest.com", "/{h}"], ["twitch.tv", "/{h}"], ["soundcloud.com", "/{h}"],
  ["linktr.ee", "/{h}"], ["vsco.co", "/{h}"], ["patreon.com", "/{h}"],
  ["tumblr.com", "/{h}"], ["deviantart.com", "/{h}"], ["codepen.io", "/{h}"],
  ["replit.com", "/@{h}"], ["scratch.mit.edu", "/users/{h}"],
  ["roblox.com", "/users/{i}"], ["khanacademy.org", "/profile/{h}"],
  ["open.spotify.com", "/user/{h}"], ["steamcommunity.com", "/id/{h}"],
  ["steamcommunity.com", "/profiles/{i}"], ["flickr.com", "/people/{h}"],
  ["flickr.com", "/photos/{h}"], ["wattpad.com", "/user/{h}"],
  ["archiveofourown.org", "/users/{h}"], ["quizlet.com", "/user/{h}"],
  ["goodreads.com", "/user/show/{h}"],
];
/** First segments that are a site's own pages, never a handle or id. */
export const URL_RESERVED: ReadonlySet<string> = new Set([
  "about", "accounts", "add", "apps", "c", "channel", "collections", "compose",
  "dashboard", "developer", "direct", "directory", "discover", "enterprise",
  "events", "explore", "features", "gaming", "groups", "hashtag", "help", "home",
  "i", "ideas", "intent", "issues", "join", "legal", "login", "marketplace",
  "messages", "new", "notifications", "orgs", "p", "pages", "people",
  "permalink.php", "photo.php", "pin", "policies", "pricing", "privacy",
  "profile.php", "pulls", "reel", "reels", "register", "search", "settings",
  "share", "sharer.php", "signup", "sponsors", "stories", "stream", "tagged",
  "today", "topics", "tos", "trending", "tv", "upload", "user", "videos",
  "watch", "you",
]);
/** Hosts that give each user a subdomain: `maria.substack.com`. */
export const URL_USER_SUBDOMAINS: ReadonlySet<string> = new Set([
  "substack.com", "tumblr.com", "blogspot.com", "wordpress.com", "github.io",
  "neocities.org", "weebly.com", "wixsite.com", "carrd.co",
]);

export const SSN_PLUG = "799-99-9999";
export const ZIP_PLUG = "99999";
export const PHONE_AREA_CODE = "201";
export const PHONE_PLUG = "201-555-0100";
export const IP_RANGES: readonly string[] = ["192.0.2.", "198.51.100.", "203.0.113."];
export const CARD_PLUGS: readonly (readonly [readonly string[], number, string])[] = [
  [["34", "37"], 15, "378282246310005"],
  [["4"], 16, "4111111111111111"],
  [["4"], 13, "4222222222222"],
  [["51", "52", "53", "54", "55", "22", "23", "24", "25", "26", "27"], 16, "5555555555554444"],
  [["6011", "64", "65"], 16, "6011111111111117"],
  [["30", "36", "38"], 14, "30569309025904"],
  [["35"], 16, "3530111333300000"],
];
export const AGE_BANDS: readonly (readonly [number, number])[] = [
  [0, 4], [5, 7], [8, 10], [11, 13], [14, 18], [19, 24], [25, 34],
  [35, 44], [45, 54], [55, 64], [65, 74], [75, 84], [85, 99],
];
const DATE = new RegExp(`([${D}]{1,2})([/-])([${D}]{1,2})\\2([${D}]{2,4})`, "u");
const ACRONYM = /^[A-Z]{3,6}$/;
export const MAX_BUMPS = 256;
const PLUG_KINDS: ReadonlySet<string> = new Set([
  "US_SOCIAL_SECURITY_NUMBER", "ZIP_CODE", "IP_ADDRESS", "CREDIT_DEBIT_CARD_NUMBER", "PHONE",
]);

export type Oracle = (value: string) => boolean;
export type Seed = number | bigint | string;

/** Every pool entry is taken or collides with the document. Raised rather than
 * falling back to a placeholder, which adlib prose promises never to carry. */
export class AdlibPoolExhausted extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AdlibPoolExhausted";
  }
}

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

/** The one source of randomness: sha256 of the NUL-joined fields, first 8 bytes
 * big-endian, mod `n`. */
export function draw(
  seed: string, documentDigest: string, purpose: string, key: string,
  bump: number, index: number, n: number,
): number {
  const material = [seed, documentDigest, purpose, key, `${bump}.${index}`].join("\0");
  const digest = createHash("sha256").update(material, "utf8").digest();
  return Number(digest.readBigUInt64BE(0) % BigInt(n));
}

/** `"Lincoln High School"` -> `"LHS"`. */
export function acronym(name: string): string {
  const words = name.match(new RegExp(`[A-Za-z][${W}'-]*`, "gu")) ?? [];
  return words.map((w) => w[0]).join("").toUpperCase();
}

function luhnDigit(payload: string): string {
  let total = 0;
  cps(payload).reverse().forEach((ch, i) => {
    const d = ((ch.codePointAt(0) as number) - 48) * (i % 2 === 0 ? 2 : 1);
    total += d > 9 ? d - 9 : d;
  });
  return String(pymod(10 - pymod(total, 10), 10));
}

/** `original` with its digits replaced, in order, by `digits`. */
function fillDigits(original: string, digits: string): string {
  const supply = cps(digits);
  let at = 0;
  return cps(original).map((c) => {
    if (!isDigitChar(c)) return c;
    // Python's `next()` past the end inside a generator is a RuntimeError.
    if (at >= supply.length) throw new Error("generator raised StopIteration");
    return supply[at++] as string;
  }).join("");
}

/** The test card number standing in for `digits` (see {@link CARD_PLUGS}). */
export function cardPlug(digits: string, variant: number): string {
  const length = cpLen(digits);
  let base = CARD_PLUGS.find(([prefixes, len]) =>
    len === length && prefixes.some((p) => digits.startsWith(p)))?.[2];
  if (base === undefined) {
    const payload = "4" + "1".repeat(Math.max(length - 2, 0));
    base = payload + luhnDigit(payload);
  }
  if (!variant) return base;
  let payload = base.slice(0, -1);
  const last4 = pymod(pyInt(payload.slice(-4)) - variant, 10000);
  payload = payload.slice(0, -4) + pad(last4, 4);
  return payload + luhnDigit(payload);
}

function daysOf(year: number): number[] {
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  return [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
}

/** Move a `m/d/y` (or `d/m/y` when the first field > 12) date forward `offset`
 * days within its own year, wrapping at year end. */
export function shiftDate(date: string, offset: number): string {
  const match = DATE.exec(date);
  if (match === null) return date;
  const [whole, first, sep, second, yearText] = match as unknown as [string, string, string, string, string];
  const dayFirst = pyInt(first) > 12;
  const [monthText, dayText] = dayFirst ? [second, first] : [first, second];
  const year = pyInt(yearText) + (cpLen(yearText) === 2 ? 2000 : 0);
  const days = daysOf(year);
  let month = Math.min(Math.max(pyInt(monthText), 1), 12);
  const day = Math.min(Math.max(pyInt(dayText), 1), days[month - 1] as number);
  const total = days.reduce((a, b) => a + b, 0);
  let doy = days.slice(0, month - 1).reduce((a, b) => a + b, 0) + day;
  doy = pymod(doy - 1 + offset, total) + 1;
  month = 1;
  while (doy > (days[month - 1] as number)) {
    doy -= days[month - 1] as number;
    month += 1;
  }
  const newMonth = pad(month, cpLen(monthText));
  const newDay = pad(doy, cpLen(dayText));
  const fields = dayFirst ? [newDay, newMonth] : [newMonth, newDay];
  return date.slice(0, match.index) + `${fields[0]}${sep}${fields[1]}${sep}${yearText}` +
    date.slice(match.index + whole.length);
}

// ---------------------------------------------------------------------------
// Names
// ---------------------------------------------------------------------------

interface Part {
  kind: "sep" | "title" | "name" | "raw";
  text: string;
  key: string;
  lead: string;
  tail: string;
  chunk: number;
  plural: boolean;
}

const part = (kind: Part["kind"], text: string, extra: Partial<Part> = {}): Part =>
  ({ kind, text, key: "", lead: "", tail: "", chunk: 0, plural: false, ...extra });

function edge(chunk: string): [string, string, string] {
  const m = EDGE.exec(chunk) as RegExpExecArray;
  let [lead, core, tail] = [m[1] ?? "", m[2] ?? "", m[3] ?? ""];
  const possessive = POSSESSIVE.exec(core);
  if (possessive) {
    tail = core.slice(possessive.index) + tail;
    core = core.slice(0, possessive.index);
  }
  return [lead, core, tail];
}

function splitSpan(original: string): Part[] {
  const parts: Part[] = [];
  original.split(SEPARATORS_SPLIT).forEach((chunk, index) => {
    if (!chunk) return;
    if (SEPARATORS_FULL.test(chunk)) {
      parts.push(part("sep", chunk));
      return;
    }
    const [lead, core, tail] = edge(chunk);
    if (!core) {
      parts.push(part("raw", chunk));
      return;
    }
    if (TITLES.has(rstripDots(fold(core))) && !parts.some((p) => p.kind === "name")) {
      parts.push(part("title", chunk));
      return;
    }
    const pieces = core.split("-");
    pieces.forEach((piece, i) => {
      const pieceLead = i === 0 ? lead : "-";
      const pieceTail = i === pieces.length - 1 ? tail : "";
      if (letters(piece).length === 0) {
        parts.push(part("raw", pieceLead + piece + pieceTail));
        return;
      }
      parts.push(part("name", piece, {
        key: rstripDots(fold(piece)), lead: pieceLead, tail: pieceTail, chunk: index,
      }));
    });
  });
  return parts;
}

function isInitial(key: string): boolean {
  return letters(key).length === 1 && cpLen(rstripDots(key)) === 1;
}

function roles(spans: Part[][], givenName: Oracle | undefined): Map<string, string> {
  const votes = new Map<string, [number, number]>();
  for (const parts of spans) {
    const names = parts.filter((p) => p.kind === "name" && !isInitial(p.key));
    if (names.length === 0) continue;
    for (const p of names) if (!votes.has(p.key)) votes.set(p.key, [0, 0]);
    const comma = parts.some((p) => p.kind === "sep" && p.text === ",");
    const titled = parts.some((p) => p.kind === "title" && FORMAL_TITLES.has(rstripDots(fold(p.text))));
    const chunks = [...new Set(names.map((p) => p.chunk))].sort((a, b) => a - b);
    const lastChunk = chunks[chunks.length - 1];
    if (titled) {
      for (const p of names) {
        const v = votes.get(p.key) as [number, number];
        if (p.chunk === lastChunk) v[1] += 2;
        else v[0] += 1;
      }
    } else if (chunks.length > 1) {
      const surname = comma ? chunks[0] : lastChunk;
      for (const p of names) (votes.get(p.key) as [number, number])[p.chunk === surname ? 1 : 0] += 1;
    }
  }
  const out = new Map<string, string>();
  for (const [key, [given, surname]] of votes) {
    if (given !== surname) out.set(key, given > surname ? GIVEN : SURNAME);
    else if (givenName !== undefined && !givenName(key)) out.set(key, SURNAME);
    else out.set(key, GIVEN);
  }
  return out;
}

/** `standIn` in the case form of `like`: lower, UPPER or as written. */
function cased(standIn: string, like: string): string {
  const l = letters(like).join("");
  if (pyIsLower(l)) return standIn.toLowerCase();
  if (pyIsUpper(l) && cpLen(l) > 1) return standIn.toUpperCase();
  return standIn;
}

function bareApostropheTail(tail: string): boolean {
  const t = cps(tail);
  return t.length > 0 && APOSTROPHES.includes(t[0] as string) && (t.length === 1 || !pyIsAlpha(t[1] as string));
}

/** Keep the possessive, but "James'" over a stand-in not ending in s is "'s". */
function possessiveTail(tail: string, standIn: string): string {
  if (bareApostropheTail(tail) && !standIn.toLowerCase().endsWith("s")) {
    const t = cps(tail);
    return (t[0] as string) + (pyIsUpper(standIn) ? "S" : "s") + t.slice(1).join("");
  }
  return tail;
}

function initials(seed: string, digest: string, keys: string[], forbidden: Set<string>): Map<string, string> {
  const alphabet = "abcdefghijklmnopqrstuvwxyz";
  const out = new Map<string, string>();
  const used = new Set<string>();
  for (const key of keys) {
    const letter = rstripDots(key);
    if (out.has(letter)) continue;
    const start = draw(seed, digest, "initial", letter, 0, 0, alphabet.length);
    let candidate = letter;
    for (let step = 0; step < alphabet.length; step++) {
      const c = alphabet[(start + step) % alphabet.length] as string;
      if (!forbidden.has(c) && !used.has(c)) {
        candidate = c;
        break;
      }
    }
    out.set(letter, candidate);
    used.add(candidate);
  }
  return out;
}

function plural(word: string): string {
  return word + (/(s|x|z|ch|sh)$/.test(word.toLowerCase()) ? "es" : "s");
}

function isPluralPossessive(p: Part): boolean {
  return bareApostropheTail(p.tail) && p.key.endsWith("s");
}

function resolvePlurals(spans: Map<string, Part[]>): void {
  const bases = new Set<string>();
  for (const parts of spans.values()) {
    for (const p of parts) if (p.kind === "name" && !isPluralPossessive(p)) bases.add(p.key);
  }
  for (const parts of spans.values()) {
    parts.forEach((p, i) => {
      if (p.kind !== "name" || !isPluralPossessive(p)) return;
      const key = cps(p.key);
      for (const cut of [1, 2]) {
        const base = key.slice(0, key.length - cut).join("");
        if ((cut === 1 || p.key.endsWith("es")) && bases.has(base)) {
          parts[i] = { ...p, key: base, plural: true };
          break;
        }
      }
    });
  }
}

function effectiveKind(kind: string, original: string): string {
  if (kind === "PERSON") kind = NAME;
  if (kind !== NAME) return kind;
  const words = original.split(new RegExp(`[${SP}]+`, "u")).filter((w) => w !== "")
    .map((w) => rstripDots(fold(w)));
  if (words.length === 0) return kind;
  if (words.some((w) => SCHOOL_CUES.has(w)) || SCHOOL_TAIL_CUES.has(words[words.length - 1] as string)) {
    return SCHOOL;
  }
  if (words.some((w) => ORG_CUES.has(w))) return ORGANIZATION;
  return kind;
}

function urlHost(host: string): [string, string] {
  const m = /^((?:www|m|mobile)\.)?([\s\S]*)$/iu.exec(host) as RegExpExecArray;
  return [m[1] ?? "", m[2] ?? ""];
}

/** Python `str.partition` / `rpartition`. */
function partition(s: string, sep: string, fromRight = false): [string, string, string] {
  const at = fromRight ? s.lastIndexOf(sep) : s.indexOf(sep);
  if (at < 0) return fromRight ? ["", "", s] : [s, "", ""];
  return [s.slice(0, at), sep, s.slice(at + sep.length)];
}

// ---------------------------------------------------------------------------
// The engine: one document's state
// ---------------------------------------------------------------------------

class Engine {
  readonly seed: string;
  readonly digest: string;
  readonly taken: Set<string>;
  names = new Map<string, string>();
  /** The prose name map alone, frozen before any other kind renders. */
  proseNames = new Map<string, string>();
  usedNames = new Set<string>();
  readonly stems = new Map<string, string>();
  readonly usedStems = new Map<string, Set<string>>();
  readonly tokens = new Map<string, string>();
  dobOffset = 1;

  constructor(
    document: string, seed: Seed, stopWords: ReadonlySet<string>, identity: Iterable<string>,
    readonly givenName: Oracle | undefined, readonly notable: Oracle | undefined,
  ) {
    this.seed = String(seed);
    this.digest = createHash("sha256").update(document, "utf8").digest("hex");
    this.taken = new Set(letters(document).map(fold));
    for (const w of stopWords) this.taken.add(w);
    for (const value of identity) for (const w of letters(value || "")) this.taken.add(fold(w));
  }

  draw(purpose: string, key: string, bump: number, index: number, n: number): number {
    return draw(this.seed, this.digest, purpose, key, bump, index, n);
  }

  private setToken(standIn: string, like: string): void {
    if (!this.tokens.has(standIn)) this.tokens.set(standIn, like);
  }

  inventNames(originals: string[]): Map<string, string> {
    const spans = new Map<string, Part[]>();
    for (const o of originals) if (!spans.has(o)) spans.set(o, splitSpan(o));
    resolvePlurals(spans);
    const role = roles([...spans.values()], this.givenName);
    const order: string[] = [];
    const surface = new Map<string, string>();
    for (const parts of spans.values()) {
      for (const p of parts) {
        if (p.kind === "name" && !isInitial(p.key) && !surface.has(p.key)) {
          order.push(p.key);
          surface.set(p.key, p.text);
        }
      }
    }
    for (const key of order) if (!role.has(key)) role.set(key, GIVEN);
    const pools: Record<string, readonly string[]> = { given: loadPool(GIVEN), surname: loadPool(SURNAME) };
    const bumps = new Map<string, number>();

    const assign = (): Map<string, string> => {
      const used = new Set<string>();
      const assigned = new Map<string, string>();
      for (const key of order) {
        const r = role.get(key) as string;
        const pool = pools[r] as readonly string[];
        const start = draw(this.seed, this.digest, r, key, bumps.get(key) ?? 0, 0, pool.length);
        let found = false;
        for (let step = 0; step < pool.length; step++) {
          const candidate = pool[(start + step) % pool.length] as string;
          const folded = fold(candidate);
          if (!this.taken.has(folded) && !used.has(folded)) {
            assigned.set(key, candidate);
            used.add(folded);
            found = true;
            break;
          }
        }
        if (!found) throw new AdlibPoolExhausted(`no unused ${r} stand-in left for this document`);
      }
      return assigned;
    };

    let assigned = assign();
    // A stand-in pair that names a public figure is re-drawn by bumping its last token.
    for (let round = 0; round < loadPool(SURNAME).length; round++) {
      if (this.notable === undefined) break;
      let clash: string | undefined;
      for (const parts of spans.values()) {
        const keys = parts.filter((p) => p.kind === "name" && assigned.has(p.key)).map((p) => p.key);
        if (keys.length > 1 && this.notable(keys.map((k) => assigned.get(k)).join(" "))) {
          clash = keys[keys.length - 1];
          break;
        }
      }
      if (clash === undefined) break;
      bumps.set(clash, (bumps.get(clash) ?? 0) + 1);
      assigned = assign();
    }
    this.names = new Map(assigned);
    this.proseNames = new Map(assigned);
    this.usedNames = new Set([...assigned.values()].map(fold));
    for (const [key, standIn] of assigned) this.setToken(standIn, surface.get(key) as string);

    const initialKeys: string[] = [];
    for (const parts of spans.values()) {
      for (const p of parts) if (p.kind === "name" && isInitial(p.key)) initialKeys.push(p.key);
    }
    const letterMap = initials(this.seed, this.digest, initialKeys, new Set(initialKeys.map(rstripDots)));
    const out = new Map<string, string>();
    for (const [original, parts] of spans) {
      const rendered: string[] = [];
      for (const p of parts) {
        if (p.kind !== "name") {
          rendered.push(p.text);
        } else if (isInitial(p.key)) {
          const letter = letterMap.get(rstripDots(p.key)) as string;
          const target = cps(rstripDots(p.text)).pop() as string;
          const replacement = cased(letter.toUpperCase(), p.text);
          const core = p.text.replace(target, () => replacement);
          rendered.push(p.lead + core + possessiveTail(p.tail, core));
        } else if (p.plural) {
          rendered.push(p.lead + cased(plural(assigned.get(p.key) as string), p.text) + p.tail);
        } else {
          const core = cased(assigned.get(p.key) as string, p.text);
          rendered.push(p.lead + core + possessiveTail(p.tail, core));
        }
      }
      out.set(original, rendered.join(""));
    }
    return out;
  }

  /** A name stand-in for a token first met in an email, handle or URL. */
  nameToken(key: string, like: string): string {
    if (!this.names.has(key)) {
      const pool = loadPool(GIVEN);
      const start = this.draw(GIVEN, key, 0, 0, pool.length);
      let chosen: string | undefined;
      let folded = "";
      for (let step = 0; step < pool.length; step++) {
        const candidate = pool[(start + step) % pool.length] as string;
        folded = fold(candidate);
        if (!this.taken.has(folded) && !this.usedNames.has(folded)) {
          chosen = candidate;
          break;
        }
      }
      if (chosen === undefined) throw new AdlibPoolExhausted("no unused given stand-in left for this document");
      this.names.set(key, chosen);
      this.usedNames.add(folded);
      this.setToken(chosen, like);
    }
    return cased(this.names.get(key) as string, like);
  }

  stem(poolName: string, key: string, like: string): string {
    const memo = `${poolName}\0${key}`;
    if (!this.stems.has(memo)) {
      const pool = loadPool(poolName);
      let used = this.usedStems.get(poolName);
      if (used === undefined) this.usedStems.set(poolName, (used = new Set()));
      const start = this.draw(poolName, key, 0, 0, pool.length);
      let chosen: string | undefined;
      for (let step = 0; step < pool.length; step++) {
        const candidate = pool[(start + step) % pool.length] as string;
        const words = letters(candidate).map(fold);
        if (!used.has(fold(candidate)) && !words.some((w) => this.taken.has(w))) {
          chosen = candidate;
          break;
        }
      }
      if (chosen === undefined) throw new AdlibPoolExhausted(`no unused ${poolName} stem left for this document`);
      used.add(fold(chosen));
      this.stems.set(memo, chosen);
      this.setToken(chosen, like);
    }
    return cased(this.stems.get(memo) as string, like);
  }

  /** Replace each maximal run of non-`kept` words with one pool stem. */
  runs(text: string, poolName: string, kept: (word: string) => boolean): string {
    const out: string[] = [];
    const run: [string, string, string][] = [];
    let pending = "";
    const flush = (): void => {
      if (run.length) {
        const like = run.map(([, core]) => core).join(" ");
        const key = run.map(([, core]) => fold(core)).join(" ");
        out.push((run[0] as [string, string, string])[0] + this.stem(poolName, key, like) +
          (run[run.length - 1] as [string, string, string])[2]);
        run.length = 0;
      }
      out.push(pending);
      pending = "";
    };
    for (const chunk of text.split(WHITESPACE_SPLIT)) {
      if (!chunk) continue;
      if (pyIsSpace(chunk)) {
        if (run.length) pending += chunk;
        else out.push(chunk);
        continue;
      }
      const [lead, core, tail] = edge(chunk);
      if (letters(core).length === 0 || kept(rstripDots(fold(core)))) {
        flush();
        out.push(chunk);
        continue;
      }
      if (run.length && (run[run.length - 1] as [string, string, string])[2]) flush();
      pending = "";
      run.push([lead, core, tail]);
    }
    flush();
    return out.join("");
  }

  /** Shape-preserving: digit for digit, letter for letter, case kept. */
  scramble(original: string, bump: number): string {
    return cps(original).map((c, i) => {
      if (isDigitChar(c)) return String(this.draw("scramble", original, bump, i, 10));
      if (c >= "a" && c <= "z") return String.fromCharCode(97 + this.draw("scramble", original, bump, i, 26));
      if (c >= "A" && c <= "Z") return String.fromCharCode(65 + this.draw("scramble", original, bump, i, 26));
      return c;
    }).join("");
  }

  /** Email local parts and handles: names mapped, digits scrambled. */
  labels(text: string): string {
    const out: string[] = [];
    for (const piece of text.split(new RegExp(`([A-Za-z]+|[${D}]+)`, "u"))) {
      if (!piece) continue;
      if (pyIsDigit(piece)) {
        out.push(cpLen(piece) >= 3 ? this.scramble(piece, 0) : piece);
      } else if (pyIsAlpha(piece) && isAscii(piece)) {
        const key = fold(piece);
        const joined = this.names.has(key) ? "" : this.joinedNames(key);
        if (this.names.has(key)) out.push(cased(this.names.get(key) as string, piece));
        else if (joined) out.push(cased(joined, piece));
        else if (cpLen(piece) <= 2) out.push(piece);
        else out.push(this.nameToken(key, piece));
      } else {
        out.push(piece);
      }
    }
    return out.join("");
  }

  joinedNames(key: string): string {
    for (let cut = 2; cut < key.length - 1; cut++) {
      const a = this.names.get(key.slice(0, cut));
      const b = this.names.get(key.slice(cut));
      if (a !== undefined && b !== undefined) return a + b;
    }
    return "";
  }

  email(original: string, bump: number): string {
    let [local, , domain] = partition(original, "@", true);
    local = this.labels(local) + (bump ? String(bump + 1) : "");
    if (!PROVIDERS.has(fold(domain))) {
      const tld = domain.split(".").pop() as string;
      domain = "example." + (["com", "net", "org"].includes(tld.toLowerCase()) ? tld : "org");
    }
    return `${local}@${domain}`;
  }

  url(original: string, bump: number): string {
    const m = /^((?:[a-z][a-z0-9+.-]*:\/\/)?)([^/?#]*)([^?#]*)(\?[^#]*)?(#[\s\S]*)?$/iu
      .exec(original) as RegExpExecArray;
    const scheme = m[1] ?? "";
    const host = m[2] ?? "";
    let path = m[3] ?? "";
    let query = m[4] ?? "";
    let fragment = m[5] ?? "";
    const [lead, bareHost] = urlHost(host);
    let bare = bareHost;
    let changed = this.urlShape(bare, path, query);
    if (changed === null) {
      const [label, dot, suffix] = partition(bare, ".");
      if (dot && URL_USER_SUBDOMAINS.has(fold(suffix)) && label &&
          !URL_RESERVED.has(fold(label)) && fold(label) !== "www") {
        changed = [this.labels(label) + dot + suffix, path, query];
      }
    }
    if (changed === null) {
      // Off-list URL: kept as written except whole words the prose name map covers.
      const [stem, dot, tld] = partition(bare, ".", true);
      bare = dot ? this.mappedWords(stem) + dot + tld : bare;
      path = this.mappedWords(path);
      query = this.mappedWords(query);
      fragment = this.mappedWords(fragment);
    } else {
      [bare, path, query] = changed;
    }
    return scheme + lead + bare + path + query + fragment + (bump ? String(bump + 1) : "");
  }

  /** Each ASCII letter run that is a prose-mapped name (3+ letters), or two joined. */
  mappedWords(text: string): string {
    const out: string[] = [];
    for (const piece of text.split(/([A-Za-z]+)/)) {
      const key = cps(fold(piece));
      const whole = this.proseNames.get(key.join(""));
      if (key.length >= 3 && whole !== undefined) {
        out.push(cased(whole, piece));
        continue;
      }
      let hit: string | undefined;
      const surface = cps(piece);
      for (let cut = 3; cut < key.length - 2; cut++) {
        const a = this.proseNames.get(key.slice(0, cut).join(""));
        const b = this.proseNames.get(key.slice(cut).join(""));
        if (a !== undefined && b !== undefined) {
          hit = cased(a, surface.slice(0, cut).join("")) + cased(b, surface.slice(cut).join(""));
          break;
        }
      }
      out.push(hit ?? piece);
    }
    return out.join("");
  }

  urlShape(host: string, path: string, query: string): [string, string, string] | null {
    const foldedHost = fold(host);
    const segments = path.split("/");
    outer: for (const [shapeHost, pattern] of URL_SHAPES) {
      if (shapeHost !== foldedHost) continue;
      const [pathPattern, , queryPattern] = partition(pattern, "?");
      const wanted = pathPattern.split("/");
      if (segments.length < wanted.length || (queryPattern && segments.length !== wanted.length)) continue;
      const out = [...segments];
      for (let at = 0; at < wanted.length; at++) {
        const want = wanted[at] as string;
        const seg = segments[at] as string;
        if (want === "{h}" || want === "{i}" || want === "@{h}") {
          let body = want === "@{h}" ? cps(seg).slice(1).join("") : seg;
          if ((want === "@{h}" && !seg.startsWith("@")) || !body || URL_RESERVED.has(fold(body))) {
            continue outer;
          }
          body = want === "{i}" ? this.scramble(body, 0) : this.labels(body);
          out[at] = (want === "@{h}" ? "@" : "") + body;
        } else if (fold(seg) !== want) {
          continue outer;
        }
      }
      let newQuery = query;
      if (queryPattern) {
        const [key] = partition(queryPattern, "=");
        const pairs = query ? cps(query).slice(1).join("").split(/([&;])/) : [];
        let hit = false;
        pairs.forEach((pair, n) => {
          const [name, eq, value] = partition(pair, "=");
          if (eq && fold(name) === key && value) {
            pairs[n] = name + eq + this.scramble(value, 0);
            hit = true;
          }
        });
        if (!hit) continue;
        newQuery = "?" + pairs.join("");
      }
      return [host, out.join("/"), newQuery];
    }
    return null;
  }

  username(original: string, bump: number): string {
    return "@" + this.labels(cps(original).slice(1).join("")) + (bump ? String(bump + 1) : "");
  }

  /** House number redrawn, unit scrambled, street words from the street pool. */
  address(original: string, bump: number): string {
    const unit = UNIT.exec(original);
    let head = unit ? original.slice(0, unit.index) : original;
    const number = new RegExp(`^[${D}]+`, "u").exec(head);
    if (number) head = this.houseNumber(number[0], bump) + head.slice(number[0].length);
    head = this.runs(head, "street", (w) => DIRECTIONS.has(w) || STREET_SUFFIXES.has(w));
    if (unit) head += (unit[1] ?? "") + this.scramble(unit[2] ?? "", bump) + (unit[3] ?? "");
    return head;
  }

  houseNumber(digits: string, bump: number): string {
    const out = [String(1 + this.draw("address-number", digits, bump, 0, 9))];
    for (let i = 1; i < cpLen(digits); i++) out.push(String(this.draw("address-number", digits, bump, i, 10)));
    return out.join("");
  }

  age(original: string, used: Set<string>, originals: Set<string>): string {
    const value = pyInt(original);
    const [lo, hi] = AGE_BANDS.find(([l, h]) => l <= value && value <= h) ?? [0, 99];
    const pool: number[] = [];
    for (let a = lo; a <= hi; a++) if (a !== value) pool.push(a);
    let free = pool.filter((a) => !originals.has(String(a)));
    if (free.length === 0) free = pool;
    const start = this.draw("age", original, 0, 0, free.length);
    const walk = free.map((_, step) => free[(start + step) % free.length] as number);
    walk.push(...pool.filter((a) => !free.includes(a)));
    const candidate = String(walk.find((a) => !used.has(String(a))) ?? free[start]);
    return cpLen(original) > cpLen(candidate) ? candidate.padStart(cpLen(original), "0") : candidate;
  }

  plug(kind: string, original: string, variant: number): string {
    if (kind === "US_SOCIAL_SECURITY_NUMBER") return fillDigits(original, `79999${pad(pymod(9999 - variant, 10000), 4)}`);
    if (kind === "ZIP_CODE") return fillDigits(original, pad(pymod(99999 - variant, 100000), 5) + "9999");
    if (kind === "IP_ADDRESS") return (IP_RANGES[Math.floor(variant / 254) % 3] as string) + String((variant % 254) + 1);
    if (kind === "CREDIT_DEBIT_CARD_NUMBER") {
      return fillDigits(original, cardPlug(original.replace(/\P{Nd}/gu, ""), variant));
    }
    // PHONE: an extension is kept; the last 7 digits become 555-01xx, the 3
    // before them PHONE_AREA_CODE, any country code is kept.
    const ext = new RegExp(`[${SP}]*(?:x|ext\\.?|extension)[${SP}]*[${D}]{1,6}${EOS}`, "iu").exec(original);
    const main = ext ? original.slice(0, ext.index) : original;
    let number = cps(main.replace(/\P{Nd}/gu, ""));
    const line = cps(`55501${pad(variant % 100, 2)}`);
    number = number.length >= 7 ? [...number.slice(0, number.length - 7), ...line] : line;
    if (number.length >= 10) number.splice(number.length - 10, 3, ...cps(PHONE_AREA_CODE));
    return fillDigits(main, number.join("")) + (ext ? ext[0] : "");
  }
}

// ---------------------------------------------------------------------------
// invent / restore
// ---------------------------------------------------------------------------

export interface InventOptions {
  /** The full original text; every word in it is off-limits. */
  readonly document: string;
  /** Varies the draw; an integer is drawn as its decimal string. Default 0. */
  readonly seed?: Seed;
  /** Words an invented token must never be (case-folded). */
  readonly stopWords?: ReadonlySet<string>;
  /** Role tie-breaker for a name token only ever seen alone. */
  readonly givenName?: Oracle;
  /** A full name stand-in this oracle calls notable is re-drawn. */
  readonly notable?: Oracle;
  /** The writer's own strings; no invented token may be one of their words. */
  readonly identity?: Iterable<string>;
}

export interface Invented {
  /** One stand-in per input span, in order. */
  readonly standIns: string[];
  /** `{invented token: original token}`, insertion-ordered. */
  readonly tokens: Map<string, string>;
}

/** A stand-in for every `[kind, original]` span of one document. */
export function invent(spans: readonly (readonly [string, string])[], options: InventOptions): Invented {
  const { document, seed = 0, stopWords = new Set<string>(), givenName, notable, identity = [] } = options;
  const engine = new Engine(document, seed, stopWords, identity, givenName, notable);
  const kinds = spans.map(([kind, original]) => effectiveKind(kind, original));
  const names = engine.inventNames(spans.filter((_, i) => kinds[i] === NAME).map(([, o]) => o));

  const dobDates = new Set<string>();
  spans.forEach(([, o], i) => {
    if (kinds[i] !== "DATE_OF_BIRTH") return;
    const m = DATE.exec(o);
    if (m) dobDates.add(m[0]);
  });
  for (let bump = 0; bump < MAX_BUMPS; bump++) {
    engine.dobOffset = 1 + engine.draw("dob-offset", "", bump, 0, 364);
    if (![...dobDates].some((d) => dobDates.has(shiftDate(d, engine.dobOffset)))) break;
  }
  const ages = new Set(spans.filter((_, i) => kinds[i] === "AGE").map(([, o]) => o));
  const originalFolds = new Set(spans.map(([, o]) => fold(o)));

  const out: string[] = spans.map(() => "");
  const memo = new Map<string, string>();
  const memoKind = new Map<string, [string, string]>();
  const owner = new Map<string, string>();
  const variants = new Map<string, number>();
  const acronyms: number[] = [];
  const memoKey = (kind: string, original: string): string => `${kind}\0${original}`;

  const render = (kind: string, original: string, bump: number): string => {
    const runPool = RUN_POOLS[kind];
    if (runPool !== undefined) return engine.runs(original, runPool, (w) => DESCRIPTIVE.has(w));
    if (kind === "EMAIL") return engine.email(original, bump);
    if (kind === "URL") return engine.url(original, bump);
    if (kind === "USERNAME") return engine.username(original, bump);
    if (kind === "ADDRESS") return engine.address(original, bump);
    if (kind === "DATE_OF_BIRTH") return shiftDate(original, engine.dobOffset);
    if (kind === "AGE" && pyIsDigit(original)) {
      const used = new Set<string>();
      for (const [k, [kk]] of memoKind) if (kk === "AGE") used.add(memo.get(k) as string);
      return engine.age(original, used, ages);
    }
    if (PLUG_KINDS.has(kind)) return engine.plug(kind, original, bump);
    return engine.scramble(original, bump);
  };

  spans.forEach(([, original], index) => {
    const kind = kinds[index] as string;
    if (kind === NAME) {
      out[index] = names.get(original) as string;
      return;
    }
    const key = memoKey(kind, original);
    if (memo.has(key)) {
      out[index] = memo.get(key) as string;
      return;
    }
    if (kind === SCHOOL && ACRONYM.test(original)) {
      acronyms.push(index);
      return;
    }
    const start = PLUG_KINDS.has(kind) ? variants.get(kind) ?? 0 : 0;
    const own = fold(original);
    let candidate = "";
    let folded = "";
    let bump = start;
    let accepted = false;
    for (; bump < start + MAX_BUMPS; bump++) {
      candidate = render(kind, original, bump);
      folded = fold(candidate);
      if (RUN_POOLS[kind] !== undefined || kind === "DATE_OF_BIRTH" || kind === "AGE") { accepted = true; break; }
      if (kind === "URL" && folded === own) { accepted = true; break; }
      if (originalFolds.has(folded) && folded !== own) continue;
      if ((owner.get(memoKey(kind, folded)) ?? own) !== own) continue;
      if (folded === own) continue;
      accepted = true;
      break;
    }
    if (!accepted) throw new AdlibPoolExhausted(`no distinct ${kind} stand-in left for this document`);
    if (PLUG_KINDS.has(kind)) variants.set(kind, bump + 1);
    owner.set(memoKey(kind, folded), own);
    memo.set(key, candidate);
    memoKind.set(key, [kind, original]);
    out[index] = candidate;
  });

  const schools: [string, string][] = [];
  for (const [k, [kind, o]] of memoKind) if (kind === SCHOOL) schools.push([o, memo.get(k) as string]);
  for (const index of acronyms) {
    const original = (spans[index] as readonly [string, string])[1];
    const key = memoKey(SCHOOL, original);
    if (!memo.has(key)) {
      const hit = schools.find(([full, standIn]) => acronym(full) === original && acronym(standIn) !== original);
      memo.set(key, (hit ? acronym(hit[1]) : "") || engine.scramble(original, 0));
      memoKind.set(key, [SCHOOL, original]);
    }
    out[index] = memo.get(key) as string;
  }
  return { standIns: out, tokens: new Map(engine.tokens) };
}

/** `{original span: stand-in}` for NAME spans; {@link invent} for all kinds. */
export function inventNames(originals: readonly string[], options: InventOptions): Map<string, string> {
  const result = invent(originals.map((o) => [NAME, o] as const), options);
  return new Map(originals.map((o, i) => [o, result.standIns[i] as string]));
}

type StringMap = ReadonlyMap<string, string> | Readonly<Record<string, string>>;
const entriesOf = (m: StringMap): [string, string][] =>
  m instanceof Map ? [...m.entries()] : Object.entries(m as Record<string, string>);

function compareCodePoints(a: string, b: string): number {
  const x = cps(a);
  const y = cps(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = (x[i] as string).codePointAt(0)! - (y[i] as string).codePointAt(0)!;
    if (d) return d;
  }
  return x.length - y.length;
}

/**
 * Put originals back into text that carries adlib stand-ins (a model reply).
 *
 * One pass over every key of `restoreMap` (`{stand-in: original}`) and
 * `tokenMap` (`{invented token: original token}`), longest first,
 * case-insensitive, word-bounded; a whole-span key wins over a token key of the
 * same text. The original is re-cased to the match: ALL CAPS, lower, or as written.
 */
export function restoreAdlib(text: string, restoreMap: StringMap, tokenMap?: StringMap): string {
  const table = new Map<string, string>();
  for (const mapping of [restoreMap, tokenMap ?? {}]) {
    for (const [key, original] of entriesOf(mapping)) {
      if (key && !table.has(fold(key))) table.set(fold(key), original);
    }
  }
  if (table.size === 0) return text;
  const keys = [...table.keys()].sort((a, b) => cpLen(b) - cpLen(a) || compareCodePoints(a, b));
  const escape = (k: string): string => k.replace(/[\\^$.*+?()[\]{}|/]/g, "\\$&");
  const pattern = new RegExp(`(?<![${W}])(?:${keys.map(escape).join("|")})(?![${W}])`, "giu");
  return text.replace(pattern, (seen) => {
    const original = table.get(fold(seen));
    if (original === undefined) throw new Error(`KeyError: ${JSON.stringify(fold(seen))}`);
    const l = letters(seen).join("");
    if (pyIsUpper(l) && cpLen(l) > 1) return original.toUpperCase();
    if (pyIsLower(l)) return original.toLowerCase();
    return original;
  });
}

// ---------------------------------------------------------------------------
// render_adlib
// ---------------------------------------------------------------------------

/** Kinds whose trailing bare apostrophe is a possessive adlib must re-form. */
const POSSESSIVE_KINDS: ReadonlySet<string> = new Set(["NAME", "PERSON", "SCHOOL", "ORGANIZATION", "LOCATION"]);

/** One rendered span and the original it stands in for. */
export interface AdlibSpan {
  readonly span: RedactionSpan;
  readonly original: string;
}

export interface RenderAdlibOptions {
  readonly seed?: Seed;
  /** The writer's own strings (first, last, school, extra names); off-limits. */
  readonly identity?: readonly string[];
}

let stopWordCache: ReadonlySet<string> | null = null;

/** Both halves of the shipped stoplist, unioned (Python's `lexicon.stop_words()`). */
export function stopWords(): ReadonlySet<string> {
  if (stopWordCache === null) {
    const all = new Set<string>();
    for (const name of STOP_WORD_LISTS) for (const w of loadLexicon(name)) all.add(w);
    stopWordCache = all;
  }
  return stopWordCache;
}

/**
 * Rewrite a placeholder result with invented values in place of every placeholder.
 *
 * Covers exactly the spans `result` covers. `original` is the text `result` was
 * redacted from: every word in it is off-limits, and it keys the draw with `seed`.
 * Throws when `result` carries placeholders its restore map cannot place.
 */
export function renderAdlib(
  result: RedactionResult, original: string, options: RenderAdlibOptions = {},
): RedactionResult {
  const { seed = 0, identity = [] } = options;
  const spans = deriveSpans(result.text, result.restoreMap);
  if (spans.length === 0) {
    if (new RegExp(PLACEHOLDER.source, "u").test(result.text)) {
      throw new Error("adlib needs a numbered restore map to render over");
    }
    return { ...result, restoreMap: new Map(result.restoreMap), render: "adlib" };
  }
  const textCps = cps(result.text);
  const origCps = cps(original);
  const kinded: [string, string][] = spans.map((span) => {
    const token = textCps.slice(span.newStart, span.newEnd).join("");
    return [placeholderKind(token), result.restoreMap.get(token) as string];
  });
  // A bare possessive apostrophe left outside a name-like span joins it.
  spans.forEach((span, i) => {
    const after = textCps.slice(span.newEnd, span.newEnd + 2);
    const [kind, source] = kinded[i] as [string, string];
    if (POSSESSIVE_KINDS.has(kind) && (after[0] === "'" || after[0] === "’") &&
        !(after.length > 1 && pyIsAlpha(after[1] as string)) && origCps[span.origEnd] === after[0]) {
      spans[i] = { origStart: span.origStart, origEnd: span.origEnd + 1, newStart: span.newStart, newEnd: span.newEnd + 1 };
      kinded[i] = [kind, source + after[0]];
    }
  });
  const invented = invent(kinded, {
    document: original, seed, stopWords: stopWords(),
    givenName: isCommonGivenName, notable: isNotable, identity: identity.filter((v) => v),
  });
  const out: string[] = [];
  let length = 0;
  const rendered: AdlibSpan[] = [];
  const restore = new Map<string, string>();
  const adlibMap = new Map<string, string>();
  let prev = 0;
  spans.forEach((span, i) => {
    const source = (kinded[i] as [string, string])[1];
    const standIn = invented.standIns[i] as string;
    const gap = textCps.slice(prev, span.newStart).join("");
    out.push(gap, standIn);
    const newStart = length + cpLen(gap);
    length = newStart + cpLen(standIn);
    rendered.push({ span: { origStart: span.origStart, origEnd: span.origEnd, newStart, newEnd: length }, original: source });
    if (!restore.has(standIn)) restore.set(standIn, source);
    if (!adlibMap.has(source)) adlibMap.set(source, standIn);
    prev = span.newEnd;
  });
  out.push(textCps.slice(prev).join(""));
  return {
    text: out.join(""), nMasked: result.nMasked, restoreMap: restore, render: "adlib",
    adlibMap, adlibSpans: rendered, adlibTokens: invented.tokens,
  };
}
