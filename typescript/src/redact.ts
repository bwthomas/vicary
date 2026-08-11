/**
 * The redaction entry point — the whole detector, wired end to end.
 *
 * Two passes over one document, in this order and for this reason:
 *
 * 1. **The identity and structured pass** (`structured.ts`) — the student's own
 *    name, school and school acronym, then every syntactic entity: email, URL,
 *    SSN, IP, phone, street address, date of birth, `@handle`, payment card
 *    behind a Luhn gate, ZIP and age. These are exact patterns, and they run
 *    first so that no looser match can consume part of one.
 * 2. **Candidate generation** (`candidates.ts`) — the third-party names nothing
 *    hands over: the classmate, the teacher, the relative, the neighbour. High
 *    recall by construction, filtered by the offline notability oracle so the
 *    public figures a student writes *about* survive.
 *
 * Generation runs LAST, for the same reason it does in the reference: a broad
 * capitalised-word match run early would swallow the first token of an address
 * or the local part of an email, and a name half-eaten by another pattern leaks
 * the remainder.
 *
 * **One minter for the whole document.** Placeholder indices follow mint order
 * across both passes, so `{NAME_1}` means one person from the first line to the
 * last. Two minters would restart each counter and hand the same token to two
 * different people, which is the defect numbering exists to remove.
 *
 * The arm this reproduces is `local-gazetteer-lowercase` — generation, plus the
 * gazetteer notability oracle, plus the lowercase route. That is the arm the
 * conformance golden was produced by, and a port comparing against those bytes
 * while implementing a different arm is measuring two changes at once.
 */

import {
  maskCandidates,
  type GivenNameOracle,
  type NotabilityOracle,
  type NotabilityTierOracle,
  type SettlementOracle,
  type TitleOracle,
} from "./candidates.js";
import type { Identity } from "./conformance.js";
import {
  isCommonGivenName,
  isNotable,
  isSettlement,
  isTitle,
  isTitlePrefix,
  notability,
} from "./gazetteer.js";
import { PlaceholderMinter, restore } from "./minter.js";
import { maskStructured, type StudentIdentity } from "./structured.js";

/**
 * Whether this build detects third-party names.
 *
 * Exported so a host can assert on it rather than infer it from a version
 * number. True since candidate generation landed; it is still meaningful,
 * because {@link NAMES_IDENTITY} turns the whole route back off at runtime.
 */
export const DETECTS_NAMES = true;

/**
 * Thrown by nothing today, and kept because the reason it existed still holds:
 * the alternative to an error is a silent no-op redactor, which type-checks,
 * satisfies every caller, and redacts nothing. Retained so the completeness
 * checks can raise it rather than inventing a new type when they need to.
 */
export class NotPortedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NotPortedError";
  }
}

// ---------------------------------------------------------------------------
// How hard to look for names
// ---------------------------------------------------------------------------

/** Only the identity the caller handed over, plus the structured entities. No
 * gazetteer is loaded and no candidate is generated — 0% recall on third-party
 * names, which is a defensible choice only when it is a chosen one. */
export const NAMES_IDENTITY = "identity";
/** Generation plus the offline notability oracle: the shippable arm. */
export const NAMES_GAZETTEER = "gazetteer";
/** …and the lowercase route, the only one that reaches a student who writes
 * without capitals. */
export const NAMES_LOWERCASE = "gazetteer-lowercase";

/** The default, matching the reference. Recall is what to buy inbound. */
export const DEFAULT_NAME_DETECTION = NAMES_LOWERCASE;

export const NAME_DETECTION_ENV_VAR = "VICARY_NAME_DETECTION";

const IDENTITY_ALIASES = new Set([
  "identity",
  "off",
  "none",
  "0",
  "false",
  "no",
]);
const GAZETTEER_ALIASES = new Set([
  "gazetteer",
  "on",
  "1",
  "true",
  "yes",
  "names",
]);
const LOWERCASE_ALIASES = new Set([
  "gazetteer-lowercase",
  "gazetteer_lowercase",
  "lowercase",
  "full",
  "max",
]);

/**
 * Resolve how hard the detector looks for names it was not handed.
 *
 * Explicit argument, then `VICARY_NAME_DETECTION`, then the code default.
 *
 * An unrecognized non-empty value resolves to the **default**, not to
 * `identity`. Dropping silently to `identity` would leave redaction on and
 * reporting spans while finding none of the names a reader would call PII — a
 * failure that looks exactly like success from every log line and metric.
 */
export function nameDetection(value?: string): string {
  const raw = (value ?? process.env[NAME_DETECTION_ENV_VAR] ?? "")
    .trim()
    .toLowerCase();
  if (raw !== "" && IDENTITY_ALIASES.has(raw)) return NAMES_IDENTITY;
  if (GAZETTEER_ALIASES.has(raw)) return NAMES_GAZETTEER;
  if (LOWERCASE_ALIASES.has(raw)) return NAMES_LOWERCASE;
  return DEFAULT_NAME_DETECTION;
}

/** The oracle bundle a detection level wires in. Empty at `identity`. */
export interface Oracles {
  readonly candidates: boolean;
  readonly notable?: NotabilityOracle;
  readonly notabilityTier?: NotabilityTierOracle;
  readonly title?: TitleOracle;
  readonly titlePrefix?: TitleOracle;
  readonly settlement?: SettlementOracle;
  readonly givenName?: GivenNameOracle;
}

/**
 * Wire the bundled gazetteer into a detection level.
 *
 * Generation and the oracle are ONE decision, not two: generation alone masks
 * every public figure a student writes about, and the oracle alone has nothing
 * to judge. There is deliberately no supported way to ask for half of it.
 *
 * At {@link NAMES_IDENTITY} this returns nothing and the 2.1 MB asset is never
 * touched. At the other two levels the first lookup pays the decompression;
 * call `load()` at process start to move that off the first request.
 */
export function gazetteerOracles(level: string): Oracles {
  if (level === NAMES_IDENTITY) return { candidates: false };
  return {
    candidates: true,
    notable: isNotable,
    notabilityTier: notability,
    title: isTitle,
    titlePrefix: isTitlePrefix,
    // Wired at BOTH gazetteer levels, unlike `givenName` below. This one decides
    // a placeholder's type, not a verdict, so it has nothing to do with which
    // candidate routes are on.
    settlement: isSettlement,
    // The one difference between the two gazetteer levels. Absent rather than
    // undefined, so `gazetteer` and `gazetteer-lowercase` differ by the presence
    // of a key rather than by a value the spread would have to strip.
    ...(level === NAMES_LOWERCASE ? { givenName: isCommonGivenName } : {}),
  };
}

// ---------------------------------------------------------------------------
// The pass
// ---------------------------------------------------------------------------

export interface RedactionResult {
  /** The masked text. */
  text: string;
  /** How many spans were replaced. */
  nMasked: number;
  /** `{placeholder: original}` — what restore needs, in discovery order. */
  restoreMap: Map<string, string>;
}

export interface RedactOptions {
  /** How hard to look for names. See {@link nameDetection}. */
  readonly names?: string;
  /** Exact strings to keep regardless, case-insensitively — the assignment
   * prompt's own names. Topical by construction, so exact, free and
   * zero-false-positive: the first rung of the notability filter, ahead of any
   * gazetteer. Empty by default, and the conformance golden is produced with it
   * empty, so the gazetteer carries every frame unaided. */
  readonly keep?: ReadonlySet<string>;
  /** Emit `{NAME}` rather than `{NAME_1}`. Off, and it should stay off outside a
   * measurement: unnumbered output round-tripped 36% of injected essays. */
  readonly numberPlaceholders?: boolean;
  /** Read a section heading's capitals as required by title case rather than
   * chosen by the writer. On; overridable so the arm stays measurable. */
  readonly headingsAreOrthographic?: boolean;
  /** Keep a bare surname the document itself established. On; see
   * `corroboratedSurnames`. */
  readonly corroborate?: boolean;
  /** Refuse corroboration where the sentence marks the surname as someone in the
   * writer's life. On; no effect without `corroborate`. */
  readonly relationRefusal?: boolean;
  /** Refuse a title-tier keep where a first-person relation is attached to the
   * name. On; overridable so the arm stays measurable. */
  readonly titleRelationRefusal?: boolean;
}

function toStudentIdentity(identity: Identity): StudentIdentity {
  return {
    firstName: identity.firstName,
    lastName: identity.lastName,
    schoolName: identity.schoolName,
  };
}

/**
 * Redact `text`, returning the masked bytes and everything needed to undo it.
 *
 * One minter for the whole document, because placeholder indices follow mint
 * order across every pass.
 */
export function redactWithReport(
  text: string,
  identity: Identity,
  options: RedactOptions = {},
): RedactionResult {
  const {
    names,
    keep = new Set<string>(),
    numberPlaceholders = true,
    headingsAreOrthographic = true,
    corroborate = true,
    relationRefusal = true,
    titleRelationRefusal = true,
  } = options;

  const minter = new PlaceholderMinter({ number: numberPlaceholders });
  if (!text) return { text, nMasked: 0, restoreMap: minter.assigned };

  const structured = maskStructured(text, toStudentIdentity(identity), minter);
  let masked = structured.text;
  let n = structured.nMasked;

  // Candidate generation runs LAST, so every exact pattern has already claimed
  // its span.
  const { candidates, ...oracles } = gazetteerOracles(nameDetection(names));
  if (candidates) {
    const found = maskCandidates(masked, {
      ...oracles,
      keep,
      corroborate,
      minter,
      headingsAreOrthographic,
      relationRefusal,
      titleRelationRefusal,
    });
    masked = found.text;
    n += found.count;
  }

  return { text: masked, nMasked: n, restoreMap: minter.assigned };
}

/**
 * Redact personal names and structured PII from `text`.
 *
 * @param text - the composition to redact.
 * @param identity - the student the detector is told about. Every reference arm
 *   interpolates these strings, so a caller that omits them is measuring a
 *   different system and misses the easiest spans in the fixture.
 * @param options - see {@link RedactOptions}. The defaults are the reference
 *   arm; every flag exists so its arm stays separately measurable.
 */
export function redact(
  text: string,
  identity: Identity,
  options: RedactOptions = {},
): string {
  return redactWithReport(text, identity, options).text;
}

/** Put the originals back into masked text. */
export { restore };
