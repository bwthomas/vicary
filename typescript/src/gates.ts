/**
 * The gates, measured by this port rather than read from the spec.
 *
 * Five of the nine gates in `conformance/gates.json` need no data beyond the
 * fixture, so this port measures them unconditionally. The other four declare
 * `requires` — `corpus` or `census` — and no package here ships either. A
 * caller that supplies the data gets those measured too; one that does not gets
 * NOT MEASURED, spelled out per gate, because five of nine held is a different
 * statement from nine of nine and a badge cannot tell them apart.
 *
 * **Why this is measured and not asserted from the golden.** The spec already
 * carries `aligns` and `mapping` per frame, computed by the reference. Reading a
 * gate's answer out of the file would make the port's gate report a restatement
 * of Python's, which is exactly the self-report MUST #6 warns about wearing an
 * external costume. Everything below is recovered from the port's own output by
 * chunk matching — the same way the reference recovers it, and without asking
 * the masker to report on itself.
 */

import {
  score,
  type Gate,
  type GateSpec,
  type Identity,
  type Spec,
  type SpecFrame,
  type SpecSpan,
} from "./conformance.js";

// ---------------------------------------------------------------------------
// Placeholders
// ---------------------------------------------------------------------------

/**
 * Every placeholder the shipped classifier can emit.
 *
 * Anything else in masked output is malformed — a truncated or nested
 * placeholder is how a masking bug presents, and it reads as ordinary prose to a
 * downstream stage.
 */
export const KNOWN_PLACEHOLDERS: ReadonlySet<string> = new Set([
  "{NAME}",
  "{SCHOOL}",
  "{EMAIL}",
  "{URL}",
  "{US_SOCIAL_SECURITY_NUMBER}",
  "{IP_ADDRESS}",
  "{PHONE}",
  "{ADDRESS}",
  "{DATE_OF_BIRTH}",
  "{USERNAME}",
  "{ZIP_CODE}",
  "{AGE}",
  "{CREDIT_DEBIT_CARD_NUMBER}",
  "{ORGANIZATION}",
  "{LOCATION}",
]);

/** Deliberately loose, so it matches malformed output too — which is the point. */
const PLACEHOLDER_RE = /\{[A-Za-z_0-9]*\}/g;

const PLACEHOLDER_INDEX_RE = /_(\d+)\}$/;

/**
 * `"{NAME_3}"` → `"{NAME}"`; an unnumbered token is returned unchanged.
 *
 * The index identifies *which* entity, the kind identifies *what* it is, and
 * every invariant here is about the kind.
 */
export function placeholderKind(token: string): string {
  return token.replace(PLACEHOLDER_INDEX_RE, "}");
}

/** Escape a literal for use inside a pattern. Narrower than Python's
 * `re.escape` on purpose — `\-` is a SyntaxError under the `u` flag — and the
 * set below is every character that can change a pattern's meaning. */
function escapeForPattern(literal: string): string {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// ---------------------------------------------------------------------------
// Alignment
// ---------------------------------------------------------------------------

export interface Alignment {
  /** `[placeholder, originalRegion]` in output order. */
  pairs: Array<[string, string]>;
  /** False when the masked text cannot be explained as "delete spans, insert
   * placeholders" — which means something rewrote prose it should not have. */
  ok: boolean;
  reason: string;
}

/**
 * Recover the span→placeholder mapping by matching the surviving prose.
 *
 * Splits `masked` at placeholder boundaries and reconstructs which region of
 * `original` each placeholder replaced. Recovered by chunk matching rather than
 * asked of the redactor, so it works against any masker without that masker
 * having to report its own spans.
 */
export function align(original: string, masked: string): Alignment {
  const placeholders = masked.match(PLACEHOLDER_RE) ?? [];
  const parts = masked.split(PLACEHOLDER_RE);
  if (placeholders.length === 0) {
    if (masked !== original) {
      return {
        pairs: [],
        ok: false,
        reason: "text changed with no placeholder emitted",
      };
    }
    return { pairs: [], ok: true, reason: "" };
  }

  // Anchored, all at once, rather than a left-to-right scan for each chunk in
  // turn. A greedy per-chunk `indexOf` misaligns whenever a surviving chunk is
  // short enough to also occur inside the span that was just removed — a
  // trailing "." after a masked email address matches the "." inside the
  // address, and the recovered region collapses to one character. Anchoring the
  // whole reconstruction makes it consistent simultaneously, so a candidate that
  // cannot be completed to the end of the original is rejected and the engine
  // backtracks. The chunks are long, distinctive prose, which is what keeps the
  // lazy quantifiers from exploring.
  const pattern =
    "^" +
    escapeForPattern(parts[0]!) +
    parts
      .slice(1)
      .map((chunk) => `([\\s\\S]*?)${escapeForPattern(chunk)}`)
      .join("") +
    "$";
  const found = new RegExp(pattern).exec(original);
  if (found === null) {
    return {
      pairs: [],
      ok: false,
      reason:
        "masked text is not the original with spans replaced — prose was " +
        "rewritten, reordered or dropped",
    };
  }
  const regions = found.slice(1);
  return {
    pairs: placeholders.map((p, i) => [p, regions[i] ?? ""] as [string, string]),
    ok: true,
    reason: "",
  };
}

/**
 * Put the originals back the way an echo-fidelity restore would have to.
 *
 * Keyed on the placeholder token, because that is all a downstream consumer has:
 * the model echoes `{NAME}` and the caller must decide which name it meant. With
 * one token per entity type it cannot, which is what `not-restorable` counts.
 * Distinct from `restore` in `minter.ts`, which is handed a map the masker built.
 */
export function restoreByToken(
  masked: string,
  mapping: ReadonlyMap<string, string>,
): string {
  return masked.replace(PLACEHOLDER_RE, (token) => mapping.get(token) ?? token);
}

/** True when the frame's sentence survives mask-then-restore exactly. */
export function roundTrips(frame: SpecFrame, masked: string): boolean {
  const alignment = align(frame.sentence, masked);
  if (!alignment.ok) return false;
  const mapping = new Map<string, string>();
  for (const [placeholder, region] of alignment.pairs) {
    if (!mapping.has(placeholder)) mapping.set(placeholder, region);
  }
  return restoreByToken(masked, mapping) === frame.sentence;
}

// ---------------------------------------------------------------------------
// Invariants
// ---------------------------------------------------------------------------

export interface Violation {
  kind: string;
  detail: string;
}

const WEAK_TOKENS: ReadonlySet<string> = new Set([
  "of",
  "van",
  "de",
  "la",
  "the",
  "der",
  "von",
  "mrs",
  "mr",
  "ms",
]);

/**
 * Substrings whose survival proves a partial leak of `span`.
 *
 * A name masked halfway still identifies the person, so "the whole literal is
 * gone" is too weak a test on multi-token names.
 */
export function leakProbes(span: SpecSpan): string[] {
  if (!["NAME", "SCHOOL", "ORGANIZATION", "LOCATION"].includes(span.entity)) {
    return [];
  }
  return span.literal
    .split(/[\s\-]+/)
    .filter((t) => t !== "")
    .map((t) => t.replace(/^[.,']+/, "").replace(/[.,']+$/, ""))
    .filter((t) => t.length >= 3 && !WEAK_TOKENS.has(t.toLowerCase()));
}

const isKeep = (span: SpecSpan): boolean => span.verdict === "keep";

/**
 * Every structural invariant the masked text must satisfy.
 *
 * `leak` — a REDACT literal survived. `partial-leak` — the literal is gone but a
 * name token of it survived; worse than a miss, because it *looks* redacted and
 * recall scores it as a pass. `keep-destroyed` — a KEEP literal was masked.
 * `unknown-placeholder` — output carries a brace token nobody emits.
 * `chunk-alignment` — prose was rewritten rather than replaced.
 * `not-restorable` — one placeholder stands for two different originals.
 * `wrong-type` — masked, but as the wrong entity.
 */
export function checkFrame(frame: SpecFrame, masked: string): Violation[] {
  const out: Violation[] = [];

  for (const token of new Set(masked.match(PLACEHOLDER_RE) ?? [])) {
    if (!KNOWN_PLACEHOLDERS.has(placeholderKind(token))) {
      out.push({ kind: "unknown-placeholder", detail: token });
    }
  }

  for (const span of frame.spans.filter((s) => !isKeep(s))) {
    if (masked.includes(span.literal)) {
      out.push({ kind: "leak", detail: `${span.entity}:${span.literal}` });
      continue;
    }
    for (const probe of leakProbes(span)) {
      if (new RegExp(`\\b${escapeForPattern(probe)}\\b`).test(masked)) {
        out.push({
          kind: "partial-leak",
          detail: `${span.entity}:${span.literal} → ${probe}`,
        });
      }
    }
  }

  for (const span of frame.spans.filter(isKeep)) {
    if (!masked.includes(span.literal)) {
      out.push({ kind: "keep-destroyed", detail: `${span.entity}:${span.literal}` });
    }
  }

  const alignment = align(frame.sentence, masked);
  if (!alignment.ok) {
    out.push({ kind: "chunk-alignment", detail: alignment.reason });
    return out;
  }

  const seen = new Map<string, string>();
  for (const [placeholder, region] of alignment.pairs) {
    const prior = seen.get(placeholder);
    if (prior !== undefined && prior !== region) {
      out.push({
        kind: "not-restorable",
        detail: `${placeholder} ← ${JSON.stringify(prior)} and ${JSON.stringify(region)}`,
      });
    }
    if (!seen.has(placeholder)) seen.set(placeholder, region);
  }

  for (const span of frame.spans.filter((s) => !isKeep(s))) {
    if (span.expect === null || masked.includes(span.literal)) continue;
    const covering = alignment.pairs
      .filter(([, region]) => region.includes(span.literal))
      .map(([p]) => placeholderKind(p));
    // `expect` carries its own braces — "{NAME}", not "NAME" — so it is compared
    // to `placeholderKind` output directly. Wrapping it again silently made
    // every correctly-typed span a `wrong-type`, which read as 41 violations and
    // printed "expected {NAME} got {NAME}".
    if (covering.length > 0 && !covering.includes(span.expect)) {
      out.push({
        kind: "wrong-type",
        detail:
          `${JSON.stringify(span.literal)} expected ${span.expect} got ` +
          `${covering[0]}`,
      });
    }
  }

  return out;
}

// ---------------------------------------------------------------------------
// Scoring spans
// ---------------------------------------------------------------------------

export interface SpanOutcome {
  frameId: string;
  entity: string;
  literal: string;
  verdict: string;
  heldOut: boolean;
  passed: boolean;
}

export function scoreSpans(frame: SpecFrame, masked: string): SpanOutcome[] {
  return frame.spans.map((span) => {
    let passed: boolean;
    if (span.expectCount !== null) {
      // Presence cannot decide a bare surname that also occurs inside a kept
      // full name, so this one is counted rather than tested for absence.
      passed = occurrences(masked, span.literal) === span.expectCount;
    } else {
      const present = masked.includes(span.literal);
      passed = isKeep(span) ? present : !present;
    }
    return {
      frameId: frame.frameId,
      entity: span.entity,
      literal: span.literal,
      verdict: span.verdict,
      heldOut: frame.heldOut,
      passed,
    };
  });
}

function occurrences(haystack: string, needle: string): number {
  if (needle === "") return 0;
  let count = 0;
  let at = haystack.indexOf(needle);
  while (at !== -1) {
    count += 1;
    at = haystack.indexOf(needle, at + needle.length);
  }
  return count;
}

// ---------------------------------------------------------------------------
// The accounted-for violations
// ---------------------------------------------------------------------------

/**
 * Invariant violations present at this fixture version, each one accounted for.
 *
 * Gated as an exact SET rather than a count, so a *new* violation fails even
 * though these do not — a ceiling of one would let a second defect in by
 * silently displacing this one.
 *
 * * `Robinson` — the documented, deliberately unpaid cost: once a document
 *   establishes "Jackie Robinson", a bare "Robinson" in it keeps, including a
 *   neighbour who shares the surname. No surname-level rule separates them.
 *
 * The companion check is the load-bearing half: an entry here that STOPS
 * occurring fails too, so a stale exemption cannot shelter the next defect of
 * the same shape. Two entries were retired from the Python list exactly that
 * way.
 */
export const ACCEPTED_VIOLATIONS: ReadonlySet<string> = new Set([
  "leak\u0000NAME:Robinson",
]);

/** The key `ACCEPTED_VIOLATIONS` is written in. NUL, because neither half can
 * contain one. */
export function violationKey(violation: Violation): string {
  return `${violation.kind}\u0000${violation.detail}`;
}

// ---------------------------------------------------------------------------
// Measuring
// ---------------------------------------------------------------------------

export interface GateMeasurement {
  gate: Gate;
  /** Null when this port does not measure it — never 0, which would read as a
   * measured failure. */
  value: number | null;
  passed: boolean | null;
  detail: string;
}

export interface GateReport {
  measurements: GateMeasurement[];
  violations: Violation[];
  unaccounted: Violation[];
  missingAccepted: string[];
}

function compare(value: number, op: string, bar: number): boolean {
  if (op === ">=") return value >= bar;
  if (op === "<=") return value <= bar;
  if (op === "==") return value === bar;
  throw new Error(`unknown gate operator ${op}`);
}

/**
 * Measure every gate this port can measure from the fixture, plus any whose
 * `requires` the caller has satisfied by supplying the data.
 *
 * `assetEntries` and `bareSurnameExposure` are passed in rather than read here
 * so this module stays free of the gazetteer and the filesystem — a caller that
 * wants those gates supplies the number, and one that does not gets NOT
 * MEASURED rather than a load.
 */
export function measureGates(
  spec: Spec,
  gateSpec: GateSpec,
  redact: (sentence: string, identity: Identity) => string,
  options: {
    assetEntries?: number;
    bareSurnameExposure?: number;
    heldOutRecallCarrier?: number;
    overFirePerEssay?: number;
    latencyP95Ms?: number;
  } = {},
): GateReport {
  const outcomes: SpanOutcome[] = [];
  const violations: Violation[] = [];
  let roundTripped = 0;

  for (const frame of spec.frames) {
    const masked = redact(frame.sentence, spec.identity);
    outcomes.push(...scoreSpans(frame, masked));
    violations.push(...checkFrame(frame, masked));
    if (roundTrips(frame, masked)) roundTripped += 1;
  }

  const heldOutRedact = outcomes.filter(
    (o) => o.heldOut && o.verdict !== "keep",
  );
  const keeps = outcomes.filter((o) => o.verdict === "keep");
  const unaccounted = violations.filter(
    (v) => !ACCEPTED_VIOLATIONS.has(violationKey(v)),
  );
  const occurred = new Set(violations.map(violationKey));
  const missingAccepted = [...ACCEPTED_VIOLATIONS].filter(
    (k) => !occurred.has(k),
  );

  const pct = (passed: number, total: number): number | null =>
    total === 0 ? null : (100.0 * passed) / total;

  const values: Record<string, { value: number | null; detail: string }> = {
    held_out_recall: {
      value: pct(
        heldOutRedact.filter((o) => o.passed).length,
        heldOutRedact.length,
      ),
      detail: `${heldOutRedact.filter((o) => o.passed).length}/${heldOutRedact.length} held-out REDACT spans`,
    },
    keep_precision: {
      value: pct(keeps.filter((o) => o.passed).length, keeps.length),
      detail: `${keeps.filter((o) => o.passed).length}/${keeps.length} KEEP spans intact`,
    },
    round_trip: {
      value: pct(roundTripped, spec.frames.length),
      detail: `${roundTripped}/${spec.frames.length} frames restore exactly`,
    },
    unaccounted_violations: {
      value: unaccounted.length,
      detail:
        unaccounted.length === 0
          ? `${violations.length} violation(s), all accounted for`
          : unaccounted.map((v) => `${v.kind}:${v.detail}`).join("; "),
    },
    asset_entries: {
      value: options.assetEntries ?? null,
      detail:
        options.assetEntries === undefined
          ? "not supplied by the caller"
          : `${options.assetEntries} entries`,
    },
  };

  // Kept in a SEPARATE map from `values` on purpose. A gate declaring
  // `requires` may be measured only from data that actually satisfies that
  // requirement — never from anything derived from the fixture, because
  // computing something else and calling it that gate is the more dangerous
  // failure. Two maps make that structural rather than a rule to remember.
  const supplied: Record<string, { value: number | null; detail: string }> = {
    bare_surname_exposure: {
      value: options.bareSurnameExposure ?? null,
      detail:
        options.bareSurnameExposure === undefined
          ? "no census file supplied by the caller"
          : `${round3(options.bareSurnameExposure)}% of US surname bearers`,
    },
    held_out_recall_carrier: {
      value: options.heldOutRecallCarrier ?? null,
      detail:
        options.heldOutRecallCarrier === undefined
          ? "no corpus supplied by the caller"
          : `${round3(options.heldOutRecallCarrier)}% of held-out REDACT spans in carrier essays`,
    },
    over_fire_prose: {
      value: options.overFirePerEssay ?? null,
      detail:
        options.overFirePerEssay === undefined
          ? "no corpus supplied by the caller"
          : `${round3(options.overFirePerEssay)} spans masked per essay of un-injected prose`,
    },
    latency_p95: {
      value: options.latencyP95Ms ?? null,
      detail:
        options.latencyP95Ms === undefined
          ? "no corpus supplied by the caller"
          : `${round3(options.latencyP95Ms)} ms at essay length, one-time asset load excluded`,
    },
  };

  const measurements = gateSpec.gates.map((gate): GateMeasurement => {
    if (gate.requires.length > 0) {
      const given = supplied[gate.id];
      if (given === undefined || given.value === null) {
        return { gate, value: null, passed: null, detail: "" };
      }
      return {
        gate,
        value: given.value,
        passed: compare(given.value, gate.op, gate.bar),
        detail: given.detail,
      };
    }
    const found = values[gate.id];
    if (found === undefined || found.value === null) {
      return { gate, value: null, passed: null, detail: found?.detail ?? "" };
    }
    return {
      gate,
      value: found.value,
      passed: compare(found.value, gate.op, gate.bar),
      detail: found.detail,
    };
  });

  return { measurements, violations, unaccounted, missingAccepted };
}

/**
 * Render the gate block, NOT MEASURED spelled out per gate.
 *
 * Replaces the placeholder block `report()` printed while nothing was measured.
 */
export function reportGates(report: GateReport): string {
  const lines: string[] = ["  gates:"];
  for (const { gate, value, passed, detail } of report.measurements) {
    // `FROM` rather than `NEEDS` once it holds a value, so the line never reads
    // as though a measured gate were still waiting on its data — and so the
    // provenance of an operator-supplied number stays attached to it.
    const needs =
      gate.requires.length === 0
        ? ""
        : `  ${passed === null ? "NEEDS" : "FROM"} ${gate.requires.join("+")}`;
    const status =
      passed === null ? "NOT MEASURED" : passed ? "PASS        " : "FAIL        ";
    const measured =
      value === null ? "" : `   measured ${round3(value)} ${gate.unit}`;
    lines.push(
      `    ${status}  ${gate.label.padEnd(28)} ${gate.op} ${gate.bar}` +
        ` ${gate.unit}${needs}${measured}`,
    );
    if (passed === false && detail !== "") lines.push(`                  ${detail}`);
  }
  const measured = report.measurements.filter((m) => m.passed !== null);
  const held = measured.filter((m) => m.passed).length;
  lines.push(
    `  -> ${held} of ${measured.length} measured gates hold; ` +
      `${report.measurements.length - measured.length} are NOT MEASURED and ` +
      `need operator-supplied data.`,
  );
  return lines.join("\n");
}

function round3(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(3);
}

/** Re-exported so a caller can build the scoreboard and the gates together. */
export { score };
