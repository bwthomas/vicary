/**
 * The redaction entry point — **not implemented yet**, and it throws.
 *
 * Throwing is the design, not a placeholder that nobody got round to. The
 * alternative shape — return the input unchanged until the detector lands — is a
 * silent no-op redactor: it type-checks, it satisfies every caller, and a host
 * that wires it up gets exactly zero redaction with no error to notice. This
 * project has already shipped that failure once in another form (a detection
 * level that scored 0% recall on the only class of name it could not interpolate,
 * for months, because nothing failed). A pass-through here would be the same
 * mistake with a fresh coat of paint.
 *
 * So: until the port reproduces the reference output, calling this is an error,
 * and `npm run conformance` is where you watch it stop being one.
 */

import type { Identity } from "./conformance.js";

export class NotPortedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NotPortedError";
  }
}

/**
 * Redact personal names from `text`.
 *
 * @param text - the composition to redact.
 * @param identity - the student the detector is told about. Every reference arm
 *   interpolates these strings, so a caller that omits them is measuring a
 *   different system.
 * @throws NotPortedError - always, for now.
 */
export function redact(text: string, identity: Identity): string {
  void text;
  void identity;
  throw new NotPortedError(
    "the TypeScript detector is not ported yet. This deliberately throws " +
      "rather than returning the text unchanged, because a redactor that " +
      "silently does nothing is worse than one that is absent: the caller " +
      "cannot tell. Track progress with `npm run conformance`; use the Python " +
      "package (pip install vicary) until it reports 35 of 35.",
  );
}
