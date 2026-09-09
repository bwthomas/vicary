/**
 * Offset translation between the masked text and the composition the student
 * holds — the TypeScript port of `python/src/vicary/redaction.py`'s span layer.
 *
 * Why a host needs it. `redactWithReport` hands back masked bytes and a restore
 * map, and between them they still say nothing about WHERE anything sits. A host
 * rendering a highlight works in the student's coordinates, the detector
 * produced its text in masked coordinates, and `{NAME_1}` is not the width of
 * the name it replaced: every offset after the first replacement is displaced by
 * the cumulative delta. Measured on a 56-paper corpus, redaction fired on 43
 * essays and not one recorded offset pair on those 43 landed on the original.
 *
 * **Nothing in the masker records anything for this to work.** That is the
 * point, and it is why this is a pure module rather than instrumentation:
 * `maskCandidates` is many passes over a string each pass replaces, so a span
 * recorded inside one pass is in that pass's intermediate coordinates and would
 * have to be composed forward through every later one. It is unnecessary — the
 * finished text still *contains* every placeholder, so each replacement's new
 * span is where its placeholder sits, and the restore map gives the original, so
 * its width is a `.length`.
 *
 * **The one place this port cannot be a transliteration.** `.length` and
 * `String.prototype.matchAll`'s `index` count UTF-16 code units, where Python
 * and Ruby count characters. For text in the Basic Multilingual Plane the two
 * agree exactly; above it — an emoji, a rarer CJK extension — a JavaScript
 * offset runs ahead of a Python one by one per astral character. That matters
 * here and not elsewhere in the port, because an offset is the one output that
 * crosses the language boundary: our own pipeline computes spans in Python and
 * renders the highlight in a browser. So the functions below work in code
 * points, not code units, and `conformance/spans.json` carries astral cases that
 * fail a transliterated implementation.
 */

/** A placeholder token as it appears in masked text, e.g. `{NAME_1}`. */
export const PLACEHOLDER = /\{[A-Z_]+(?:_\d+)?\}/gu;

/**
 * One replacement, located in both the original and the masked text.
 *
 * `origStart`..`origEnd` is what was removed; `newStart`..`newEnd` is the
 * placeholder standing in its place. The two widths differ — that difference is
 * the whole reason this type exists.
 */
export interface RedactionSpan {
  readonly origStart: number;
  readonly origEnd: number;
  readonly newStart: number;
  readonly newEnd: number;
}

/** How much one replacement moved everything after it. */
export function spanDelta(span: RedactionSpan): number {
  return span.newEnd - span.newStart - (span.origEnd - span.origStart);
}

/** Length in code points — see the module note on UTF-16. */
function codePointLength(text: string): number {
  let n = 0;
  for (const _ of text) n += 1;
  return n;
}

/**
 * The span map, derived from the masked text and the restore map alone.
 *
 * Replacement preserves order, so one left-to-right walk accumulating the
 * running delta recovers the original offsets exactly.
 *
 * Returns `[]` when `restoreMap` is empty or does not cover a placeholder
 * present in the text. A partial map is refused rather than answered for the
 * half it can place, because a translation that is right for some offsets and
 * silently wrong for others is worse than one that declines: the caller can
 * handle "no map" and cannot detect "wrong offset".
 */
export function deriveSpans(
  masked: string,
  restoreMap: Map<string, string> | undefined,
): RedactionSpan[] {
  if (restoreMap === undefined || restoreMap.size === 0) return [];
  const spans: RedactionSpan[] = [];
  let delta = 0;
  // Walked code point by code point rather than by `matchAll`, so `newStart`
  // is a code-point offset. A regex `index` is a code-unit offset and would
  // put every span after an astral character two apart from Python's answer.
  let offset = 0;
  const pattern = new RegExp(PLACEHOLDER.source, "u");
  for (let unit = 0; unit < masked.length; ) {
    const rest = masked.slice(unit);
    const match = pattern.exec(rest);
    if (match !== null && match.index === 0) {
      const original = restoreMap.get(match[0]);
      if (original === undefined) return [];
      const width = codePointLength(match[0]);
      const origStart = offset - delta;
      const span: RedactionSpan = {
        origStart,
        origEnd: origStart + codePointLength(original),
        newStart: offset,
        newEnd: offset + width,
      };
      spans.push(span);
      delta += spanDelta(span);
      offset += width;
      unit += match[0].length;
      continue;
    }
    const char = String.fromCodePoint(masked.codePointAt(unit) as number);
    unit += char.length;
    offset += 1;
  }
  return spans;
}

/**
 * A redacted-text offset in original coordinates.
 *
 * An offset landing INSIDE a placeholder maps to the start of the span it
 * replaced: the placeholder's interior has no counterpart in the original, so
 * any position within it is the same position in original terms.
 */
export function toOriginal(
  offset: number,
  spans: readonly RedactionSpan[],
): number {
  let result = offset;
  for (const span of spans) {
    if (offset < span.newStart) break;
    if (offset < span.newEnd) return span.origStart;
    result = span.origEnd + (offset - span.newEnd);
  }
  return result;
}

/**
 * An original-text offset in redacted coordinates.
 *
 * An offset inside a replaced span maps to the start of its placeholder, for the
 * mirror-image reason.
 */
export function toRedacted(
  offset: number,
  spans: readonly RedactionSpan[],
): number {
  let result = offset;
  for (const span of spans) {
    if (offset < span.origStart) break;
    if (offset < span.origEnd) return span.newStart;
    result = span.newEnd + (offset - span.origEnd);
  }
  return result;
}

/**
 * Reconstruct the pre-redaction text from the masked bytes and the map.
 *
 * Exact where a span map exists, and the masked text unchanged where one does
 * not — the honest answer rather than a partial restoration, for the reason
 * {@link deriveSpans} declines.
 */
export function originalText(
  masked: string,
  restoreMap: Map<string, string> | undefined,
): string {
  const spans = deriveSpans(masked, restoreMap);
  if (spans.length === 0) return masked;
  // Sliced by code point, so the pieces line up with the code-point offsets
  // the spans are expressed in.
  const chars = Array.from(masked);
  const out: string[] = [];
  let prev = 0;
  for (const span of spans) {
    out.push(chars.slice(prev, span.newStart).join(""));
    const placeholder = chars.slice(span.newStart, span.newEnd).join("");
    out.push(restoreMap?.get(placeholder) ?? placeholder);
    prev = span.newEnd;
  }
  out.push(chars.slice(prev).join(""));
  return out.join("");
}
