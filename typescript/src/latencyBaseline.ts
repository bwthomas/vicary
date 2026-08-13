/**
 * Is this build slower than the last release, and is that a fair question here?
 *
 * The gate has asked this three ways. The first two are worth keeping in view,
 * because each looked correct until it decided a release.
 *
 * **An absolute bar — 10 ms.** A claim about the machine as much as about the
 * code. It passed on a laptop and failed on the CI runner enforcing it, so
 * v0.2.3 published to PyPI and npm and was refused by RubyGems on one commit.
 *
 * **A stored baseline** — record each release's number and compare the next run
 * against it, refusing unless the run claims the profile the baseline was
 * recorded on. Better, and still wrong, for a reason no estimator fixes: the
 * profile `github-ubuntu-latest` is not a machine. Thirty-six processes across
 * six runners per port, on identical code, spread 67% in Ruby (6.53 ms on an
 * Intel Xeon 6973P-C against 10.63 ms on an EPYC 7763), 26% in Python and 21%
 * here — against an 8% bar. One probe run drew five CPU models from that one
 * label, and two runners of the same model still differed by 26%. So it red-lit
 * `main` on unchanged code at +8.33%: the absolute bar in a relative costume.
 *
 * **A pair, measured here.** The previous release's code and this checkout,
 * measured on the SAME machine, interleaved and counterbalanced, by
 * `tools/latency_pair.py`. Every property of the machine is common to both sides
 * and cancels; what is left is within-process noise — 3.3% in this port, the
 * noisiest of the three, which is why it takes three times as many rounds.
 *
 * Which leaves this module the job it has always had: REFUSING to compare when
 * the two sides would not be like for like. What changed is that the refusals
 * are about the pair record — is there one, is it this port's, was it measured
 * on these essays, was it measured for this commit — rather than about the
 * profile of a machine somewhere else.
 *
 * This port reaches its own verdict from the shared record. It does not read
 * Python's answer.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { conformanceDir } from "./conformance.js";

/**
 * The tolerance and the protocol, in the repository. Not a measurement: nothing
 * is recorded at release time any more, because the comparison point is the
 * previous release's *code*, which the repository already has.
 */
export const SPEC_FILENAME = "latency_baseline.json";

/**
 * Where `tools/latency_pair.py` left the paired measurement. Set by CI in the
 * same job, seconds before the gate runs. Absent on a laptop unless the harness
 * was run there by hand, and that absence is a refusal to compare rather than a
 * pass — measuring one side of a comparison is not a gate.
 */
export const PAIR_ENV_VAR = "VICARY_LATENCY_PAIR";

/**
 * What this reader understands. A record from a future shape is refused rather
 * than half-read: a partly-understood record still yields a number, and a number
 * is exactly what must not be invented here.
 */
export const PAIR_DOCUMENT_VERSION = 1;

export const IMPLEMENTATION = "typescript";

/**
 * The bar, chosen rather than derived — 8% is what a reviewer is willing to call
 * a regression. What the noise decides is whether the bar is USABLE, and this is
 * the port that answers it: twelve pair runs across six CI runners against a
 * fixed head and tag put the gate statistic at sigma 1.71%, so 8% is 4.7 sigma
 * out. Under the stored baseline it was about a third of a sigma, which is how
 * that one red-lit `main` on unchanged code. See `tools/latency_pair.py`.
 *
 * It does not catch drift: +5% a release passes every time and compounds. That
 * is deliberate — this gate is for the step change, not the trend.
 */
export const DEFAULT_TOLERANCE_PCT = 8.0;

export interface Comparison {
  measuredMs: number;
  previousMs: number | null;
  currentMs: number | null;
  regressionPct: number | null;
  tolerancePct: number;
  against: string | null;
  comparable: boolean;
  reason: string | null;
  holds: boolean;
}

export interface SpecDoc {
  tolerance_pct?: number;
}

export interface PairRecord {
  document_version?: number;
  implementation?: string;
  corpus?: string;
  head_sha?: string;
  previous_ms?: number;
  current_ms?: number;
  against?: { ref?: string; sha?: string };
}

export function specPath(directory?: string): string | null {
  const root = directory ?? conformanceDir();
  if (!root) return null;
  const path = join(root, SPEC_FILENAME);
  return existsSync(path) ? path : null;
}

export function loadSpecDoc(directory?: string): SpecDoc | null {
  const path = specPath(directory);
  if (path === null) return null;
  return JSON.parse(readFileSync(path, "utf8")) as SpecDoc;
}

/**
 * The paired measurement, or why there is none to read.
 *
 * An unreadable file and an absent one stay distinguishable: the first is a
 * broken harness and the second is an ordinary laptop, and they should not
 * report the same thing.
 */
export function loadPair(
  path?: string,
): { record: PairRecord | null; reason: string | null } {
  const given = (path ?? process.env[PAIR_ENV_VAR] ?? "").trim();
  if (given === "") {
    return {
      record: null,
      reason:
        `${PAIR_ENV_VAR} is unset, so no paired measurement was taken on this ` +
        `machine; the gate compares this build against the last release ` +
        `measured HERE, and one side of a comparison is not a gate`,
    };
  }
  if (!existsSync(given)) {
    return { record: null, reason: `${PAIR_ENV_VAR}='${given}' does not exist` };
  }
  try {
    return { record: JSON.parse(readFileSync(given, "utf8")) as PairRecord, reason: null };
  } catch (err) {
    return {
      record: null,
      reason: `the pair record at ${given} could not be read: ${String(err)}`,
    };
  }
}

export interface CompareOptions {
  directory?: string;
  implementation?: string;
  pairPath?: string;
  buildingSha?: string;
}

/**
 * Compare the pair measured on this machine, for this port.
 *
 * `measuredMs` is this process's own figure. It is reported either way and it is
 * never the verdict: the verdict comes from the two numbers in the pair record,
 * taken back to back on one machine. Mixing this process's measurement with the
 * pair's other side would reintroduce exactly the machine difference the pair
 * exists to cancel.
 */
export function compare(
  measuredMs: number,
  corpusId: string,
  options: CompareOptions = {},
): Comparison {
  const spec = loadSpecDoc(options.directory);
  const tolerancePct = spec?.tolerance_pct ?? DEFAULT_TOLERANCE_PCT;
  const implementation = options.implementation ?? IMPLEMENTATION;

  const declined = (reason: string): Comparison => ({
    measuredMs,
    previousMs: null,
    currentMs: null,
    regressionPct: null,
    tolerancePct,
    against: null,
    comparable: false,
    reason,
    holds: false,
  });

  const { record, reason } = loadPair(options.pairPath);
  if (record === null) return declined(reason ?? "no paired measurement");

  if (record.document_version !== PAIR_DOCUMENT_VERSION) {
    return declined(
      `the pair record is document_version ${record.document_version} and ` +
        `this reader knows ${PAIR_DOCUMENT_VERSION}`,
    );
  }
  if (record.implementation !== implementation) {
    return declined(
      `the pair record measures '${record.implementation}', not '${implementation}'`,
    );
  }
  if (record.corpus !== corpusId) {
    return declined(
      `the pair was measured on corpus '${record.corpus}' and this run is ` +
        `'${corpusId}'; latency scales with essay length`,
    );
  }

  // Only where there is something to check against. `GITHUB_SHA` names the
  // commit the job is building, so a record left over from an earlier commit is
  // caught here rather than being read as this build's verdict. Locally there is
  // no such witness and no such risk: the harness is run by hand, minutes
  // before, on the tree in front of you.
  const building = (options.buildingSha ?? process.env["GITHUB_SHA"] ?? "").trim();
  const head = record.head_sha ?? "";
  if (building !== "" && head !== "" && building !== head) {
    return declined(
      `the pair was measured for commit ${head.slice(0, 12)} and this job is ` +
        `building ${building.slice(0, 12)}; the record is stale`,
    );
  }

  const previous = record.previous_ms;
  const current = record.current_ms;
  if (typeof previous !== "number" || typeof current !== "number") {
    return declined("the pair record carries no pair of measurements");
  }
  if (previous <= 0) {
    return declined(
      `the previous release measured ${previous} ms, which is not positive`,
    );
  }

  const regressionPct = (current / previous - 1.0) * 100.0;
  return {
    measuredMs,
    previousMs: previous,
    currentMs: current,
    regressionPct,
    tolerancePct,
    against: record.against?.ref ?? null,
    comparable: true,
    reason: null,
    holds: regressionPct <= tolerancePct,
  };
}

export function render(c: Comparison): string {
  if (!c.comparable) {
    return (
      `latency ${c.measuredMs.toFixed(3)} ms — NOT COMPARED against the last ` +
      `release: ${c.reason}`
    );
  }
  const sign = (c.regressionPct ?? 0) >= 0 ? "+" : "";
  return (
    `latency ${c.measuredMs.toFixed(3)} ms here; paired on this machine, ` +
    `${c.currentMs!.toFixed(3)} ms against ${c.against ?? "the last release"}'s ` +
    `${c.previousMs!.toFixed(3)} ms — ${sign}${c.regressionPct!.toFixed(2)}% ` +
    `against a ${c.tolerancePct.toFixed(0)}% bar`
  );
}

/**
 * The fields `measureGates` wants, spread into its options.
 *
 * Returns the *detail* rather than a value when the comparison was declined, so
 * the gate reports NOT MEASURED with the reason attached instead of quietly
 * passing on a machine that was never entitled to an opinion.
 */
export function latencyGateFields(
  measuredMs: number,
  corpusId: string,
  options: CompareOptions = {},
): { latencyRegressionPct?: number; latencyRegressionDetail?: string } {
  const c = compare(measuredMs, corpusId, options);
  return c.comparable && c.regressionPct !== null
    ? { latencyRegressionPct: c.regressionPct }
    : { latencyRegressionDetail: render(c) };
}
