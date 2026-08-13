/**
 * The three gates that need an essay corpus, measured by this port.
 *
 * Held-out recall in a carrier essay, over-firing on real prose, and latency at
 * essay length cannot be measured on isolated sentences. They need fixture
 * frames planted inside genuine student prose. That prose ships: `persuade-20`
 * lives in `conformance/corpora/` and is the registry default, so all three are
 * measured on a bare checkout. They fall back to NOT MEASURED only when the
 * corpus that *resolves* is operator-supplied — ASAP-AES, selected either by
 * `VICARY_EVAL_CORPUS` or by having `VICARY_EVAL_CORPUS_TSV` configured — and no
 * TSV is there to read.
 *
 * **Where the carrier text comes from.** Everything about building it is
 * deterministic except which sentence ends the frames land on, which the Python
 * reference draws from its Mersenne Twister. Rather than reimplement MT19937 and
 * `random.sample` here — several hundred lines with nothing to do with
 * redaction, whose failure mode is silent — the draw is recorded once in
 * `conformance/carrier.json` and read back. The plan is an *input*, exactly as
 * `frames.json` is: it says where to inject. What this port then measures from
 * the resulting text is recovered from its own output, never read from the spec.
 *
 * **Why the digest check is not paranoia.** An offset into the wrong essay is
 * not an error anything downstream notices; it produces a plausible number from
 * text nobody intended. So each essay is checked against the digest the plan was
 * built from, and a mismatch raises rather than measuring.
 */

import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

import {
  conformanceDir,
  type Identity,
  type Spec,
  type SpecFrame,
} from "./conformance.js";
import { align, scoreSpans, type SpanOutcome } from "./gates.js";

/** Where the operator's corpus TSV is configured. Both names, as Python reads. */
export const EVAL_CORPUS_TSV_ENV_VAR = "VICARY_EVAL_CORPUS_TSV";
export const EVAL_CORPUS_DIR_ENV_VAR = "VICARY_EVAL_CORPUS_DIR";

/** Preferred filename inside the directory form. */
export const EVAL_CORPUS_PREFERRED_FILENAME = "corpus.tsv";

const CARRIER_FILENAME = "carrier.json";

/**
 * Bumped when a field's meaning changes. An unknown version is refused.
 *
 * 2 keyed the plans by corpus id. A version-1 reader handed a version-2 file
 * finds no `cases` at the top level and builds zero carrier essays — which in a
 * `<=` gate is the most comfortable pass on the board, so the refusal is the
 * point of the number.
 */
const CARRIER_DOCUMENT_VERSION = 2;

/** Where the corpus profiles live, under `conformance/`. */
const CORPORA_DIRNAME = "corpora";
const CORPORA_INDEX_FILENAME = "index.json";
const CORPUS_PROFILE_FILENAME = "profile.json";
const PROFILE_DOCUMENT_VERSION = 1;

/** Names a corpus id directly, overriding the operator-TSV inference. */
export const EVAL_CORPUS_ENV_VAR = "VICARY_EVAL_CORPUS";

/** Source kinds a corpus profile may declare. */
export const KIND_SHIPPED = "shipped";
export const KIND_OPERATOR_TSV = "operator_tsv";

/**
 * How many times each essay is redacted for the latency figure. The recorded
 * number is the MEDIAN of these, not one sample. Must stay odd, so the median is
 * a sample rather than a mean of two.
 *
 * Why: the latency gate takes p95 across essays, and at n=20 that index *is* the
 * maximum. So a single-sample-per-essay design asked "did a GC pause land in any
 * one of twenty calls" and answered a `<=` gate with it. Measured on Ruby, five
 * consecutive runs of unchanged code gave 13.8, 7.4, 13.1, 7.7, 6.8 ms against a
 * 10 ms bar — two failures out of five, bimodal at 2x rather than noisy, which is
 * the signature of a pause landing on the one sample that decides the answer.
 * A median of three per essay means a pause has to hit the same essay twice.
 *
 * Five rather than three because the gated number is now a regression bar with
 * 8% of room, and the estimator has to reproduce itself to well inside that on
 * unchanged code. Every repeat re-redacts the whole corpus, so this is not free;
 * five is where the measured gain flattened. The same constant lives in all
 * three ports, because a gate two ports estimate differently is not the same
 * gate.
 */
export const LATENCY_REPEATS = 5;

/** Median of a sample, taking the lower of the two middles at even length. */
function medianOf(xs: number[]): number {
  if (xs.length === 0) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 === 1
    ? s[(s.length - 1) >> 1]!
    : (s[s.length / 2 - 1]! + s[s.length / 2]!) / 2;
}

/** The essays file a shipped corpus keeps beside its profile. */
const ESSAYS_FILENAME = "essays.json";

/** What a corpus profile says about where its essays come from. */
export interface CorpusProfile {
  id: string;
  name: string;
  kind: string;
  /** Row filter value for the operator-TSV kind, or `""`. */
  essaySet: string;
  limit: number;
  /** File the shipped kind reads, relative to the corpus directory. */
  textFile: string;
  /** `essayId -> sha256`, what the profile pins the shipped text to. */
  digests: Map<string, string>;
}

function readVersioned(path: string, what: string): Record<string, unknown> {
  const raw = JSON.parse(readFileSync(path, "utf8"));
  if (raw.document_version !== PROFILE_DOCUMENT_VERSION) {
    throw new Error(
      `${path} is document_version ${raw.document_version} and this reader ` +
        `knows ${PROFILE_DOCUMENT_VERSION}. Refusing to read the fields it ` +
        `recognises: a partly-read ${what} selects a different slice of prose ` +
        "without being detectably wrong.",
    );
  }
  return raw;
}

/** The corpus registry: which corpora exist, and which applies by default. */
export function loadCorpusIndex(directory?: string): Record<string, unknown> {
  const dir = directory ?? conformanceDir();
  return readVersioned(
    join(dir, CORPORA_DIRNAME, CORPORA_INDEX_FILENAME),
    "registry",
  );
}

/** One corpus's profile. */
export function loadCorpusProfile(
  corpusId: string,
  directory?: string,
): CorpusProfile {
  const dir = directory ?? conformanceDir();
  const raw = readVersioned(
    join(dir, CORPORA_DIRNAME, corpusId, CORPUS_PROFILE_FILENAME),
    "profile",
  );
  const source = raw["source"] as Record<string, unknown>;
  const filter = (source["filter"] ?? {}) as Record<string, unknown>;
  const selection = raw["selection"] as Record<string, unknown>;
  const essays = (raw["essays"] ?? []) as Array<Record<string, unknown>>;
  return {
    id: raw["id"] as string,
    name: raw["name"] as string,
    kind: source["kind"] as string,
    essaySet: (filter["equals"] as string | undefined) ?? "",
    limit: selection["limit"] as number,
    textFile: (source["text_file"] as string | undefined) ?? ESSAYS_FILENAME,
    digests: new Map(
      essays.map((e) => [e["id"] as string, e["sha256"] as string]),
    ),
  };
}

/**
 * Which corpus applies here, matching the reference's order exactly: an explicit
 * `VICARY_EVAL_CORPUS` wins, then an operator who has configured a TSV keeps
 * measuring the corpus they always measured, then the registry default.
 */
export function resolveCorpusId(directory?: string): string {
  const index = loadCorpusIndex(directory);
  const known = (index["corpora"] ?? []) as string[];
  const explicit = (process.env[EVAL_CORPUS_ENV_VAR] ?? "").trim();
  if (explicit !== "") {
    if (!known.includes(explicit)) {
      throw new Error(
        `${EVAL_CORPUS_ENV_VAR}=${explicit} is not a registered corpus; this ` +
          `checkout registers ${known.join(", ")}`,
      );
    }
    return explicit;
  }
  if (corpusSource() !== "" && index["operator_default"]) {
    return index["operator_default"] as string;
  }
  return index["default"] as string;
}

/**
 * One of ASAP's own anonymization tokens — `@PERSON1`, `@LOCATION2`.
 *
 * Load-bearing for the over-fire metric, because the two legs it separates are
 * unrelated. Masking genuine prose is a precision defect; masking `@PERSON1` is
 * not, since the PII is already gone. Summed they read as one catastrophic
 * precision failure while the prose leg is zero.
 */
const ASAP_TOKEN_RE = /^@[A-Z]+\d*$/;

export function isAsapToken(region: string): boolean {
  return ASAP_TOKEN_RE.test(region.trim());
}

// ---------------------------------------------------------------------------
// The corpus
// ---------------------------------------------------------------------------

/** Configured path to the corpus TSV, or `""`. */
export function corpusSource(): string {
  const explicit = (process.env[EVAL_CORPUS_TSV_ENV_VAR] ?? "").trim();
  if (explicit !== "") return explicit;
  const directory = (process.env[EVAL_CORPUS_DIR_ENV_VAR] ?? "").trim();
  if (directory === "") return "";
  return join(directory, EVAL_CORPUS_PREFERRED_FILENAME);
}

/**
 * `[essayId, text]` for the first `limit` essays of the named set, in file
 * order.
 *
 * **Decoded as latin-1, matching the reference.** ASAP-AES is not UTF-8, and
 * decoding it as UTF-8 either throws or substitutes replacement characters —
 * either way the text diverges from Python's, the digests stop matching, and
 * every offset in the plan points somewhere slightly wrong.
 */
export function loadSet(
  tsv: string,
  essaySet: string,
  limit: number,
): Array<[string, string]> {
  // latin-1 is a byte-for-byte map onto the first 256 code points, so this
  // decode is exact rather than best-effort.
  const rows = parseDelimited(readFileSync(tsv, "latin1"), "\t", limit, essaySet);
  return rows;
}

/**
 * `[essayId, text]` for a corpus whose essays ship in this repository.
 *
 * **The essays ARE the baseline**, so every byte is checked against the digest
 * the profile pins. A corrupted or edited file has to fail here rather than
 * quietly rebase what every corpus gate means — the numbers describe this exact
 * prose and nothing warns you when the prose changes underneath them. The carrier
 * plan checks the same bytes again from its own digests, which is deliberate: two
 * independent records of what this corpus is, and either catches an edit to the
 * other.
 */
export function loadShipped(
  corpusId: string,
  directory?: string,
): Array<[string, string]> {
  const dir = directory ?? conformanceDir();
  const profile = loadCorpusProfile(corpusId, directory);
  const path = join(dir, CORPORA_DIRNAME, corpusId, profile.textFile);
  const document = readVersioned(path, "corpus");
  const essays = (document["essays"] as Array<Record<string, unknown>>).map(
    (e) => [e["id"] as string, e["text"] as string] as [string, string],
  );

  for (const [essayId, text] of essays) {
    const want = profile.digests.get(essayId);
    if (want === undefined) {
      throw new Error(
        `${corpusId}: ${profile.textFile} carries essay ${essayId}, which ` +
          `${CORPUS_PROFILE_FILENAME} does not list`,
      );
    }
    const got = createHash("sha256").update(text, "utf8").digest("hex");
    if (got !== want) {
      throw new Error(
        `${corpusId}: essay ${essayId} in ${profile.textFile} is sha256 ` +
          `${got}, and ${CORPUS_PROFILE_FILENAME} pins ${want}. Refusing: the ` +
          "essays are the baseline, so different text means every gate number " +
          "measured on this corpus describes different prose.",
      );
    }
  }
  if (profile.digests.size !== essays.length) {
    throw new Error(
      `${corpusId}: ${CORPUS_PROFILE_FILENAME} lists ${profile.digests.size} ` +
        `essays and ${profile.textFile} holds ${essays.length}`,
    );
  }
  return essays;
}

/**
 * The resolved corpus's essays, whichever kind it is.
 *
 * Returns `null` only for an operator corpus with no TSV configured — the one
 * case where the data genuinely is not here. A shipped corpus always loads, which
 * is the whole point of shipping one.
 */
export function loadEssays(
  corpusId?: string,
  directory?: string,
): Array<[string, string]> | null {
  const id = corpusId ?? resolveCorpusId(directory);
  const profile = loadCorpusProfile(id, directory);
  if (profile.kind === KIND_SHIPPED) return loadShipped(id, directory);
  if (profile.kind === KIND_OPERATOR_TSV) {
    const tsv = corpusSource();
    if (tsv === "") return null;
    return loadSet(tsv, profile.essaySet, profile.limit);
  }
  throw new Error(
    `corpus ${id} declares source kind ${profile.kind}; this reader knows ` +
      `${KIND_SHIPPED} and ${KIND_OPERATOR_TSV}`,
  );
}

/**
 * Read the TSV the way Python's `csv` module does, because splitting on tabs
 * and newlines does not.
 *
 * ASAP essays contain `"` characters, and some records span more than one
 * physical line inside a quoted field — 12,980 lines for 12,976 records. A naive
 * split silently truncates those essays mid-sentence, which changes their
 * digests and moves every gate that reads them. Quoting rules are RFC4180 as
 * Python implements them: a quote opens a field only at its start, `""` inside
 * one is a literal quote, and anything after the closing quote is taken
 * literally.
 *
 * Stops as soon as `limit` matching rows are found, so this walks only as far
 * into a 16 MB file as it has to.
 */
function parseDelimited(
  text: string,
  delimiter: string,
  limit: number,
  essaySet: string,
): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  let header: string[] | null = null;
  let setAt = -1;
  let idAt = -1;
  let essayAt = -1;

  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  let i = 0;

  const endRow = (): boolean => {
    row.push(field);
    field = "";
    const finished = row;
    row = [];
    if (header === null) {
      header = finished;
      setAt = header.indexOf("essay_set");
      idAt = header.indexOf("essay_id");
      essayAt = header.indexOf("essay");
      if (setAt === -1 || idAt === -1 || essayAt === -1) {
        throw new Error(
          "corpus has no essay_set/essay_id/essay header; got " +
            header.join(","),
        );
      }
      return false;
    }
    if (finished.length === 1 && finished[0] === "") return false; // blank line
    if (finished[setAt] !== essaySet) return false;
    out.push([finished[idAt] ?? "", finished[essayAt] ?? ""]);
    return out.length >= limit;
  };

  while (i < text.length) {
    const ch = text[i]!;
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i += 1;
        continue;
      }
      field += ch;
      i += 1;
      continue;
    }
    if (ch === '"' && field === "") {
      inQuotes = true;
      i += 1;
      continue;
    }
    if (ch === delimiter) {
      row.push(field);
      field = "";
      i += 1;
      continue;
    }
    if (ch === "\r" || ch === "\n") {
      if (ch === "\r" && text[i + 1] === "\n") i += 1;
      i += 1;
      if (endRow()) return out;
      continue;
    }
    field += ch;
    i += 1;
  }
  if (field !== "" || row.length > 0) endRow();
  return out;
}

// ---------------------------------------------------------------------------
// The carrier plan
// ---------------------------------------------------------------------------

export interface CarrierCase {
  essayId: string;
  baseSha256: string;
  baseChars: number;
  frames: string[];
  slots: number[];
}

/** An essay the plan deliberately carries nothing in, and why. */
export interface CarrierUnusable {
  essayId: string;
  reason: string;
}

export interface CarrierPlan {
  corpusId: string;
  essaySet: string;
  limit: number;
  perEssay: number;
  cases: CarrierCase[];
  /** Named rather than merely absent, so a short plan can be told from a lossy
   * one. See the reconciliation at the end of {@link buildCases}. */
  unusable: CarrierUnusable[];
}

export function loadCarrierPlan(
  corpusId?: string,
  directory?: string,
): CarrierPlan {
  const dir = directory ?? conformanceDir();
  const raw = JSON.parse(readFileSync(join(dir, CARRIER_FILENAME), "utf8"));
  if (raw.document_version !== CARRIER_DOCUMENT_VERSION) {
    throw new Error(
      `${CARRIER_FILENAME} is document_version ${raw.document_version}, and ` +
        `this reader knows ${CARRIER_DOCUMENT_VERSION}. Refusing rather than ` +
        "reading the fields it recognises, because a partly-read plan produces " +
        "carrier text that is wrong without being detectably wrong.",
    );
  }
  const id = corpusId ?? resolveCorpusId(directory);
  const plans = (raw.plans ?? {}) as Record<string, Record<string, unknown>>;
  const plan = plans[id];
  if (plan === undefined) {
    throw new Error(
      `${CARRIER_FILENAME} holds no plan for corpus ${id}; it has ` +
        `${Object.keys(plans).sort().join(", ") || "none"}. Regenerate with ` +
        "`python -m vicary.eval.carrier --write` on a machine that can read " +
        "that corpus.",
    );
  }
  // The row filter and the essay count are properties of the corpus, so they
  // come off its profile rather than being restated in the plan — two records of
  // one fact is how they drift.
  const profile = loadCorpusProfile(id, directory);
  return {
    corpusId: id,
    essaySet: profile.essaySet,
    limit: profile.limit,
    perEssay: plan["per_essay"] as number,
    cases: (plan["cases"] as Array<Record<string, unknown>>).map((c) => ({
      essayId: c["essay_id"] as string,
      baseSha256: c["base_sha256"] as string,
      baseChars: c["base_chars"] as number,
      frames: c["frames"] as string[],
      slots: c["slots"] as number[],
    })),
    unusable: ((plan["unusable"] ?? []) as Array<Record<string, unknown>>).map(
      (u) => ({
        essayId: u["essay_id"] as string,
        reason: u["reason"] as string,
      }),
    ),
  };
}

// ---------------------------------------------------------------------------
// What the reference measured
// ---------------------------------------------------------------------------

const MEASURED_FILENAME = "measured.json";

/**
 * 2 keyed the measurements by corpus id. Two of the three numbers here are
 * properties of the prose rather than of the detector, so an unkeyed block
 * invited comparing one corpus's figures against another's and reading the
 * difference as a regression in the port.
 */
const MEASURED_DOCUMENT_VERSION = 2;

/**
 * The counts the Python reference gets on the carrier text this plan produces.
 *
 * Read rather than transcribed. These numbers used to be literals in this port's
 * gate test — `assert.equal(corpus.recallHeldOutPassed, 29)` — and in Ruby's, and
 * in Python's. Three copies of a number is not three checks of it: when the
 * reference's figure legitimately moves, Python's suite is updated because that
 * is where the change was made, and the other two keep asserting the stale value
 * and stay green while measuring something else.
 */
export interface ReferenceMeasurements {
  /** sha256 of every carrier essay's text, concatenated in plan order. */
  carrierTextSha256: string;
  /** The fixture the numbers were measured against. Compared, not assumed. */
  fixtureVersion: string;
  /** The arm. Without the gazetteer the same detector reads 0% held-out. */
  arm: string;
  essays: number;
  recallHeldOutPassed: number;
  recallHeldOutTotal: number;
  recallHeldOutPct: number;
  overFireSpansTotal: number;
  overFireSpansPerEssay: number;
  asapRewritesPerEssay: number;
}

export function loadReferenceMeasurements(
  corpusId?: string,
  directory?: string,
): ReferenceMeasurements {
  const dir = directory ?? conformanceDir();
  const raw = JSON.parse(readFileSync(join(dir, MEASURED_FILENAME), "utf8"));
  if (raw.document_version !== MEASURED_DOCUMENT_VERSION) {
    throw new Error(
      `${MEASURED_FILENAME} is document_version ${raw.document_version}, and ` +
        `this reader knows ${MEASURED_DOCUMENT_VERSION}. Refusing rather than ` +
        "reading the fields it recognises: a partly-read document compares " +
        "this port against numbers whose meaning it is guessing at.",
    );
  }
  const id = corpusId ?? resolveCorpusId(directory);
  const corpora = (raw.corpora ?? {}) as Record<
    string,
    Record<string, unknown>
  >;
  const entry = corpora[id];
  if (entry === undefined) {
    throw new Error(
      `${MEASURED_FILENAME} holds no measurements for corpus ${id}; it has ` +
        `${Object.keys(corpora).sort().join(", ") || "none"}. Regenerate with ` +
        "`just sync-conformance` on a machine that can read that corpus.",
    );
  }
  const gates = entry["corpus_gates"] as Record<string, number>;
  const envelope = entry["envelope"] as Record<string, string>;
  return {
    carrierTextSha256: entry["carrier_text_sha256"] as string,
    fixtureVersion: envelope["fixture_version"]!,
    arm: envelope["arm"]!,
    essays: gates["essays"]!,
    recallHeldOutPassed: gates["recall_held_out_passed"]!,
    recallHeldOutTotal: gates["recall_held_out_total"]!,
    recallHeldOutPct: gates["recall_held_out_pct"]!,
    overFireSpansTotal: gates["over_fire_spans_total"]!,
    overFireSpansPerEssay: gates["over_fire_spans_per_essay"]!,
    asapRewritesPerEssay: gates["asap_rewrites_per_essay"]!,
  };
}

/** One injected essay plus the ground truth of what went into it. */
export interface Case {
  essayId: string;
  /** The essay with the frames injected. */
  text: string;
  /** The essay as the corpus holds it, for the over-fire leg. */
  base: string;
  frames: SpecFrame[];
}

/**
 * Rebuild the carrier essays from the plan.
 *
 * Slots are applied in the order recorded — descending — so an earlier insertion
 * cannot shift a later one.
 */
export function buildCases(
  essays: Array<[string, string]>,
  plan: CarrierPlan,
  spec: Spec,
): Case[] {
  const byId = new Map(spec.frames.map((f) => [f.frameId, f]));
  const planned = new Map(plan.cases.map((c) => [c.essayId, c]));
  const cases: Case[] = [];

  for (const [essayId, base] of essays) {
    const entry = planned.get(essayId);
    if (entry === undefined) continue;
    const digest = createHash("sha256").update(base, "utf8").digest("hex");
    if (digest !== entry.baseSha256) {
      throw new Error(
        `essay ${essayId} in this corpus does not match the one the carrier ` +
          `plan was built from (sha256 ${digest.slice(0, 12)} vs ` +
          `${entry.baseSha256.slice(0, 12)}). The recorded offsets point into ` +
          "different text, so every number downstream would be wrong without " +
          "being detectably wrong.",
      );
    }
    const picks = entry.frames.map((id) => {
      const frame = byId.get(id);
      if (frame === undefined) {
        throw new Error(`carrier plan names frame ${id}, absent from the spec`);
      }
      return frame;
    });
    let text = base;
    picks.forEach((frame, i) => {
      const at = entry.slots[i]!;
      text = text.slice(0, at) + " " + frame.sentence + text.slice(at);
    });
    cases.push({ essayId, text, base, frames: picks });
  }

  // Every planned essay, or none of them. A corpus that matches the plan only
  // partly would measure a *subset* and report it under the same gate — and the
  // degenerate case of matching nothing is worse than wrong, because over-firing
  // and latency both then compute as 0, which in a `<=` gate is the most
  // comfortable pass on the board. Refusing is the only outcome that cannot be
  // mistaken for a green run.
  if (cases.length !== plan.cases.length) {
    const found = new Set(cases.map((c) => c.essayId));
    const missing = plan.cases
      .filter((c) => !found.has(c.essayId))
      .map((c) => c.essayId);
    throw new Error(
      `the carrier plan names ${plan.cases.length} essays and this corpus ` +
        `supplied ${cases.length} of them; missing ${missing.slice(0, 5).join(", ")}` +
        `${missing.length > 5 ? " …" : ""}. Refusing to measure a subset, ` +
        "because over-firing and latency on an empty or partial set compute as " +
        "0 and read as a pass.",
    );
  }

  // And every *corpus* essay is either carried or named unusable. The check
  // above only proves the plan got what it asked for; it cannot see an essay the
  // plan never asked about. That was safe while a plan always covered its whole
  // corpus, and stopped being safe when `unusable` made a short plan legitimate
  // — without this, a plan that quietly lost ten essays would measure the fifteen
  // it kept and report them under the same gate.
  const accounted = new Set(cases.map((c) => c.essayId));
  for (const entry of plan.unusable) accounted.add(entry.essayId);
  const unaccounted = essays
    .filter(([essayId]) => !accounted.has(essayId))
    .map(([essayId]) => essayId);
  if (unaccounted.length > 0) {
    throw new Error(
      `the corpus supplies ${essays.length} essays and the carrier plan accounts ` +
        `for ${accounted.size} of them — ${plan.cases.length} carried and ` +
        `${plan.unusable.length} declared unusable. Unaccounted: ` +
        `${unaccounted.slice(0, 5).join(", ")}${unaccounted.length > 5 ? " …" : ""}. ` +
        "An essay the plan neither carries nor names is one it dropped silently, " +
        "which is the same comfortable pass as a partial match.",
    );
  }
  return cases;
}

// ---------------------------------------------------------------------------
// The measurement
// ---------------------------------------------------------------------------

export interface CorpusMetrics {
  essays: number;
  /** Held-out REDACT spans masked, as a percentage. */
  recallHeldOut: number;
  recallHeldOutPassed: number;
  recallHeldOutTotal: number;
  /** Spans this port masked in prose nobody planted anything in, per essay. */
  overFireSpansPerEssay: number;
  overFireSpansTotal: number;
  /** ASAP's own `@`-tokens rewritten, per essay. Not a defect; reported apart. */
  asapRewritesPerEssay: number;
  latencyP50Ms: number;
  latencyP95Ms: number;
  /** Median of every essay x every repeat. What the regression gate reads. */
  latencyPooledMedianMs: number;
}

/**
 * Measure the three corpus gates.
 *
 * Each essay is redacted twice — once with the frames injected, to score recall,
 * and once bare, to see what the redactor does to prose with nothing planted in
 * it. The bare pass is where over-firing comes from, and it is why the metric
 * means anything: the frames cannot contaminate it.
 */
export function measureCorpus(
  cases: Case[],
  redact: (text: string, identity: Identity) => string,
  identity: Identity,
): CorpusMetrics {
  const outcomes: SpanOutcome[] = [];
  const latencies: number[] = [];
  const pooled: number[] = [];
  let overFireSpans = 0;
  let asapRewrites = 0;

  // Load the gazetteer before the clock starts. It is a one-time cost — ~84 ms
  // in Python, ~207 ms in Ruby — and whichever essay happens to be first pays
  // all of it: at n=25 that single sample lands at or above p95 and sets the
  // gate's answer by itself. The number the gate claims is essay-length
  // redaction latency, not process startup, and leaving this in made the same
  // code report different figures depending only on whether something earlier
  // in the process had touched the asset. Excluded in all three ports alike.
  if (cases.length > 0) redact(cases[0]!.base.slice(0, 200), identity);

  for (const testCase of cases) {
    // The median of LATENCY_REPEATS, not one sample — see that constant.
    const timings: number[] = [];
    let masked = "";
    for (let i = 0; i < LATENCY_REPEATS; i += 1) {
      const started = performance.now();
      masked = redact(testCase.text, identity);
      timings.push(performance.now() - started);
    }
    timings.sort((a, b) => a - b);
    latencies.push(timings[(timings.length - 1) >> 1]!);
    // Every sample, not just the essay's collapsed value: the gated figure is
    // the median of the POOLED samples, which is what reproduces itself to
    // inside the regression bar. See LATENCY_REPEATS.
    pooled.push(...timings);

    for (const frame of testCase.frames) {
      outcomes.push(...scoreSpans(frame, masked));
    }

    const maskedBase = redact(testCase.base, identity);
    const pairs = align(testCase.base, maskedBase).pairs;
    const prose = pairs.filter(([, region]) => !isAsapToken(region));
    overFireSpans += prose.length;
    asapRewrites += pairs.length - prose.length;
  }

  const heldOutRedact = outcomes.filter(
    (o) => o.heldOut && o.verdict !== "keep",
  );
  const passed = heldOutRedact.filter((o) => o.passed).length;
  const sorted = [...latencies].sort((a, b) => a - b);
  const at = (q: number): number =>
    sorted.length === 0
      ? 0
      : sorted[Math.min(Math.floor(sorted.length * q), sorted.length - 1)]!;

  return {
    essays: cases.length,
    recallHeldOut:
      heldOutRedact.length === 0 ? 0 : (100.0 * passed) / heldOutRedact.length,
    recallHeldOutPassed: passed,
    recallHeldOutTotal: heldOutRedact.length,
    overFireSpansPerEssay:
      cases.length === 0 ? 0 : overFireSpans / cases.length,
    overFireSpansTotal: overFireSpans,
    asapRewritesPerEssay: cases.length === 0 ? 0 : asapRewrites / cases.length,
    latencyP50Ms: at(0.5),
    latencyP95Ms: at(0.95),
    latencyPooledMedianMs: medianOf(pooled),
  };
}

/** Load the corpus, rebuild the carriers, and measure. `null` with no corpus. */
export function measureFromConfig(
  spec: Spec,
  redact: (text: string, identity: Identity) => string,
  identity: Identity,
): CorpusMetrics | null {
  const corpusId = resolveCorpusId();
  const essays = loadEssays(corpusId);
  if (essays === null || essays.length === 0) return null;
  const plan = loadCarrierPlan(corpusId);
  return measureCorpus(buildCases(essays, plan, spec), redact, identity);
}
