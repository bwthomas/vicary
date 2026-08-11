/**
 * The redaction entry point — **partially ported**, and honest about which part.
 *
 * What works: the student's own identity (name, school, school acronym) and every
 * structured entity — email, URL, SSN, IP, phone, street address, date of birth,
 * @handle, payment card behind a Luhn gate, ZIP and age. Those are syntax, and
 * regex scored 100% on them in the harness that measured a cloud Guardrail at
 * 97.3%.
 *
 * What does NOT work yet: **third-party names.** A classmate, a teacher, a
 * relative or a public figure the student mentions is not in the identity the
 * caller hands over, and no regex finds it. That is candidate generation plus the
 * notability filter — `name_candidates.py`, the 1,750-line core, still to port.
 *
 * That gap is why this module is not re-exported from `index.ts`. A partially
 * ported redactor is a reasonable thing to *measure* and an unreasonable thing to
 * hand a host: it would mask a phone number, miss every name in the essay, and
 * give the caller no way to tell. The package's public surface therefore still
 * offers no `redact`, and `release-npm.yml` refuses to publish below 35 of 35
 * masking-required frames. `npm run conformance` is where the real number lives.
 */

import type { Identity } from "./conformance.js";
import { PlaceholderMinter, restore } from "./minter.js";
import { maskStructured, type StudentIdentity } from "./structured.js";

/**
 * Whether this build detects third-party names.
 *
 * Exported so a host can assert on it rather than infer it from a version
 * number. It flips in the commit that lands candidate generation.
 */
export const DETECTS_NAMES = false;

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

export interface RedactionResult {
  /** The masked text. */
  text: string;
  /** How many spans were replaced. */
  nMasked: number;
  /** `{placeholder: original}` — what restore needs, in discovery order. */
  restoreMap: Map<string, string>;
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
 * order across every pass. Candidate generation will number into these same
 * counters when it lands.
 */
export function redactWithReport(
  text: string,
  identity: Identity,
): RedactionResult {
  const minter = new PlaceholderMinter({ number: true });
  const masked = maskStructured(text, toStudentIdentity(identity), minter);
  return {
    text: masked.text,
    nMasked: masked.nMasked,
    restoreMap: minter.assigned,
  };
}

/**
 * Redact personal names and structured PII from `text`.
 *
 * @param text - the composition to redact.
 * @param identity - the student the detector is told about. Every reference arm
 *   interpolates these strings, so a caller that omits them is measuring a
 *   different system and misses the easiest spans in the fixture.
 */
export function redact(text: string, identity: Identity): string {
  return redactWithReport(text, identity).text;
}

/** Put the originals back into masked text. */
export { restore };
