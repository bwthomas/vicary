/**
 * Read the shared spec and score this implementation against it.
 *
 * The spec lives in the repository's `conformance/` directory, is generated from
 * the Python implementation, and is what all three front doors run against. See
 * `conformance/README.md` for the bar; the short version is that every frame's
 * masked output must be byte-identical **including placeholder numbering**.
 *
 * **Why the scoreboard reports two denominators.** 16 of the 52 frames expect
 * nothing to be masked — they exist to catch over-redaction. An implementation
 * that returns its input unchanged therefore scores 16 of 52 and looks a third of
 * the way done while detecting nothing at all. So the number that leads is
 * `matched of framesRequiringMasking`, and the 52-frame total is reported beside
 * it rather than instead of it. A ratio whose numerator a null implementation can
 * inflate is not a measure of progress.
 */

import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { PrecedenceRow } from "./candidates.js";

export const DOCUMENT_VERSION = 1;

export interface SpecSpan {
  entity: string;
  literal: string;
  verdict: string;
  expectCount: number | null;
  expect: string | null;
  keptBy: string;
  redactedBy: string;
  note: string;
}

export interface SpecFrame {
  frameId: string;
  group: string;
  sentence: string;
  spans: SpecSpan[];
  heldOut: boolean;
  promptContext: string;
  note: string;
}

export interface Golden {
  masked: string;
  placeholders: string[];
  mapping: Array<[string, string]>;
  aligns: boolean;
}

export interface Identity {
  firstName: string;
  lastName: string;
  schoolName: string;
}

export interface Spec {
  fixtureVersion: string;
  referenceArm: string;
  identity: Identity;
  frames: SpecFrame[];
  golden: Map<string, Golden>;
}

export interface Gate {
  id: string;
  label: string;
  unit: string;
  op: string;
  bar: number;
  requires: string[];
  why: string;
}

export interface GateSpec {
  referenceArm: string;
  requirements: Record<string, string>;
  gates: Gate[];
}

/** Locate the repository's `conformance/` directory, or throw naming the search. */
export function conformanceDir(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  const tried: string[] = [];
  let current = resolve(here);
  for (let depth = 0; depth < 8; depth += 1) {
    const candidate = join(current, "conformance");
    tried.push(candidate);
    try {
      readFileSync(join(candidate, "frames.json"));
      return candidate;
    } catch {
      // keep walking up
    }
    const parent = resolve(current, "..");
    if (parent === current) break;
    current = parent;
  }
  throw new Error(
    `no conformance/frames.json found. Looked in: ${tried.join(", ")}. The spec ` +
      `lives in the repository, not in an installed package — a published copy ` +
      `would imply the installed one is authoritative.`,
  );
}

function requireVersion(version: unknown, file: string): void {
  if (version !== DOCUMENT_VERSION) {
    throw new Error(
      `${file} is document_version ${String(version)}, this reader understands ` +
        `${DOCUMENT_VERSION}. Refusing to read it rather than guessing which ` +
        `fields moved.`,
    );
  }
}

/** Load `conformance/frames.json`, applying the documented field defaults. */
export function loadSpec(directory?: string): Spec {
  const dir = directory ?? conformanceDir();
  const raw = JSON.parse(readFileSync(join(dir, "frames.json"), "utf8"));
  requireVersion(raw.document_version, "frames.json");

  const frames: SpecFrame[] = raw.frames.map((f: Record<string, unknown>) => ({
    frameId: f["frame_id"] as string,
    group: f["group"] as string,
    sentence: f["sentence"] as string,
    heldOut: (f["held_out"] as boolean | undefined) ?? false,
    promptContext: (f["prompt_context"] as string | undefined) ?? "",
    note: (f["note"] as string | undefined) ?? "",
    spans: (f["spans"] as Array<Record<string, unknown>>).map((s) => ({
      entity: s["entity"] as string,
      literal: s["literal"] as string,
      // The defaults are documented in conformance/README.md. Applying them
      // here rather than requiring the exporter to write them keeps the file
      // readable; getting one wrong silently changes what a frame asserts.
      verdict: (s["verdict"] as string | undefined) ?? "redact",
      expectCount: (s["expect_count"] as number | undefined) ?? null,
      expect: (s["expect"] as string | undefined) ?? null,
      keptBy: (s["kept_by"] as string | undefined) ?? "notability",
      redactedBy: (s["redacted_by"] as string | undefined) ?? "absence",
      note: (s["note"] as string | undefined) ?? "",
    })),
  }));

  const golden = new Map<string, Golden>();
  for (const [frameId, entry] of Object.entries(
    raw.golden as Record<string, Golden>,
  )) {
    golden.set(frameId, entry);
  }

  return {
    fixtureVersion: raw.fixture_version as string,
    referenceArm: raw.reference_arm as string,
    identity: {
      firstName: raw.identity.first_name as string,
      lastName: raw.identity.last_name as string,
      schoolName: raw.identity.school_name as string,
    },
    frames,
    golden,
  };
}

/**
 * The primitives layer: the tokenisation and capitalisation answers a port must
 * reproduce before any frame can come out right.
 *
 * Deliberately untyped past this shape. Every section is `caseName -> answer`,
 * and the answer's type is whatever that primitive returns — spans, strings,
 * booleans, nested token runs. A discriminated union over eighteen sections would
 * be a transliteration of the generator's structure, which is the thing this file
 * exists to avoid; the test compares with `deepEqual` and does not need to know.
 */
export interface Primitives {
  corpus: Record<string, string>;
  tokenLists: Record<string, string[]>;
  stopTokens: string[];
  /**
   * Inputs for the surname-folding functions, which take a name rather than a
   * text, a token list or a span — the fourth input group.
   */
  nameForms: string[];
  /** The `keep` set the masking arm is generated with — the assignment prompt's
   * own names, as a stand-in. */
  keeps: string[];
  oracles: {
    settlements: string[];
    titles: string[];
    givenNames: string[];
    fullNames: string[];
    iconicSurnames: string[];
  };
  /** The classification policy, in order. See `PRECEDENCE` in `candidates.ts`. */
  precedence: PrecedenceRow[];
  /**
   * The word lists the classification arms read, sorted and in full.
   *
   * Carried because the token lists exercise only a handful of each, so a port
   * that transliterated one of them short passes every case in `cases` anyway.
   */
  suffixes: { organization: string[]; landmark: string[] };
  /**
   * Inputs for the relation predicates, which take `(text, start, end)` rather
   * than a whole text or a token list. Offsets are resolved by the generator, so
   * a port compares answers instead of reproducing the spec's own arithmetic.
   */
  spanCases: Record<string, { text: string; start: number; end: number }>;
  /**
   * The hand-typed lists behind candidate generation, in SOURCE ORDER.
   *
   * Order is load-bearing: honorifics and particles are joined into regex
   * alternations, and `withoutClitic` strips the first clitic that matches. A
   * port that sorted any of them would build a different pattern.
   */
  wordLists: { honorifics: string[]; particles: string[]; clitics: string[] };
  /** The relation override's word lists, and the tiers it may override. */
  relation: {
    cues: string[];
    proximityCues: string[];
    firstPerson: string[];
    overridableTiers: string[];
  };
  /** The one tier a candidate may establish a surname from. Policy, not a count:
   * a port comparing against another string corroborates nothing and every case
   * still passes, because the spans involved were being masked either way. */
  corroboration: { tier: string };
  constants: Record<string, number>;
  cases: Record<string, Record<string, unknown>>;
}

/** Load `conformance/primitives.json`. */
export function loadPrimitives(directory?: string): Primitives {
  const dir = directory ?? conformanceDir();
  const raw = JSON.parse(readFileSync(join(dir, "primitives.json"), "utf8"));
  requireVersion(raw.document_version, "primitives.json");
  return {
    corpus: raw.corpus as Record<string, string>,
    tokenLists: raw.token_lists as Record<string, string[]>,
    stopTokens: raw.stop_tokens as string[],
    nameForms: raw.name_forms as string[],
    keeps: raw.keeps as string[],
    oracles: {
      settlements: raw.oracles.settlements as string[],
      titles: raw.oracles.titles as string[],
      givenNames: raw.oracles.given_names as string[],
      fullNames: raw.oracles.full_names as string[],
      iconicSurnames: raw.oracles.iconic_surnames as string[],
    },
    precedence: raw.precedence as PrecedenceRow[],
    suffixes: raw.suffixes as { organization: string[]; landmark: string[] },
    spanCases: raw.span_cases as Record<
      string,
      { text: string; start: number; end: number }
    >,
    wordLists: raw.word_lists as {
      honorifics: string[];
      particles: string[];
      clitics: string[];
    },
    relation: {
      cues: raw.relation.cues as string[],
      proximityCues: raw.relation.proximity_cues as string[],
      firstPerson: raw.relation.first_person as string[],
      overridableTiers: raw.relation.overridable_tiers as string[],
    },
    corroboration: { tier: raw.corroboration.tier as string },
    constants: raw.constants as Record<string, number>,
    cases: raw.cases as Record<string, Record<string, unknown>>,
  };
}

/** Load `conformance/gates.json`. */
export function loadGates(directory?: string): GateSpec {
  const dir = directory ?? conformanceDir();
  const raw = JSON.parse(readFileSync(join(dir, "gates.json"), "utf8"));
  requireVersion(raw.document_version, "gates.json");
  return {
    referenceArm: raw.reference_arm as string,
    requirements: raw.requirements as Record<string, string>,
    gates: raw.gates as Gate[],
  };
}

export interface FrameOutcome {
  frameId: string;
  requiresMasking: boolean;
  matched: boolean;
  expected: string;
  produced: string;
  /** Set when the implementation threw rather than returned. */
  error?: string;
}

export interface Scoreboard {
  fixtureVersion: string;
  referenceArm: string;
  total: number;
  matched: number;
  requiringMasking: number;
  matchedRequiringMasking: number;
  outcomes: FrameOutcome[];
}

/**
 * Score an implementation against every frame.
 *
 * `redact` returns the masked text. It is passed the sentence and the identity
 * the detector is told about, which is the same input every Python arm receives —
 * omitting the identity measures a different system and misses the easiest spans.
 */
export function score(
  spec: Spec,
  redact: (sentence: string, identity: Identity) => string,
): Scoreboard {
  const outcomes: FrameOutcome[] = [];
  for (const frame of spec.frames) {
    const golden = spec.golden.get(frame.frameId);
    if (golden === undefined) {
      throw new Error(
        `frame ${frame.frameId} has no golden output in the spec; the file is ` +
          `internally inconsistent and scoring against it would be meaningless`,
      );
    }
    const requiresMasking = golden.placeholders.length > 0;
    let produced: string;
    let error: string | undefined;
    try {
      produced = redact(frame.sentence, spec.identity);
    } catch (caught) {
      produced = "";
      error = caught instanceof Error ? caught.message : String(caught);
    }
    outcomes.push({
      frameId: frame.frameId,
      requiresMasking,
      matched: error === undefined && produced === golden.masked,
      expected: golden.masked,
      produced,
      ...(error === undefined ? {} : { error }),
    });
  }

  const requiring = outcomes.filter((o) => o.requiresMasking);
  return {
    fixtureVersion: spec.fixtureVersion,
    referenceArm: spec.referenceArm,
    total: outcomes.length,
    matched: outcomes.filter((o) => o.matched).length,
    requiringMasking: requiring.length,
    matchedRequiringMasking: requiring.filter((o) => o.matched).length,
    outcomes,
  };
}

/**
 * Render the scoreboard.
 *
 * Leads with the masking-required ratio, because that is the one a null
 * implementation cannot inflate. The gate list is printed with NOT MEASURED
 * spelled out per gate rather than reduced out of the denominator — five of nine
 * held is a different statement from nine of nine, and a badge cannot tell them
 * apart.
 */
export function report(board: Scoreboard, gates: GateSpec): string {
  const lines: string[] = [];
  lines.push(`conformance — fixture ${board.fixtureVersion}, arm ${board.referenceArm}`);
  lines.push("-".repeat(58));
  lines.push(
    `  frames requiring masking   ${board.matchedRequiringMasking
      .toString()
      .padStart(3)} / ${board.requiringMasking}`,
  );
  lines.push(
    `  all frames                 ${board.matched
      .toString()
      .padStart(3)} / ${board.total}   ` +
      `(${board.total - board.requiringMasking} expect no masking, so an ` +
      `identity function scores that many)`,
  );
  lines.push("-".repeat(58));
  lines.push("  gates:");
  for (const gate of gates.gates) {
    const needs =
      gate.requires.length === 0
        ? ""
        : `  NEEDS ${gate.requires.join("+")}`;
    lines.push(
      `    NOT MEASURED  ${gate.label.padEnd(28)} ${gate.op} ${gate.bar}` +
        ` ${gate.unit}${needs}`,
    );
  }
  lines.push(
    "  -> no gate is measured by this port yet. A green run here means the " +
      "spec loads,",
  );
  lines.push("     never that the gate set is clear.");
  return lines.join("\n");
}
