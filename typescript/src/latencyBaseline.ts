/**
 * Is this build slower than the last release, and is that a fair question here?
 *
 * The latency gate used to hold an absolute number — 10 ms — which is a claim
 * about the machine as much as about the code. It passed on a laptop and failed
 * on the CI runner enforcing it, so v0.2.3 published to PyPI and npm and was
 * refused by RubyGems on the same commit.
 *
 * What replaced it asks a relative question: is this port slower than it was at
 * the last release, by more than the tolerance. That only means something
 * between measurements taken on comparable hardware, so this module's real work
 * is REFUSING to compare when they are not — a machine difference reported as a
 * code regression is worse than no gate, because it trains the reader to ignore
 * it.
 *
 * This port reaches its own verdict from the shared file. It does not read
 * Python's answer.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { conformanceDir } from "./conformance.js";

export const BASELINE_FILENAME = "latency_baseline.json";

/**
 * Set by CI on the one matrix entry whose language version matches the recorded
 * profile. Absent everywhere else on purpose: a developer's laptop measures the
 * same commit two to three times faster than the runner, and comparing that
 * against a runner baseline reports a large phantom improvement.
 */
export const PROFILE_ENV_VAR = "VICARY_LATENCY_PROFILE";

export const IMPLEMENTATION = "typescript";

export interface Comparison {
  measuredMs: number;
  baselineMs: number | null;
  regressionPct: number | null;
  tolerancePct: number;
  comparable: boolean;
  reason: string | null;
  holds: boolean;
}

export interface BaselineDoc {
  tolerance_pct?: number;
  corpus?: string;
  profile?: {
    id?: string;
    language_versions?: Record<string, string>;
  };
  implementations?: Record<string, { pooled_median_ms?: number | null }>;
}

export function baselinePath(directory?: string): string | null {
  const root = directory ?? conformanceDir();
  if (!root) return null;
  const path = join(root, BASELINE_FILENAME);
  return existsSync(path) ? path : null;
}

export function loadBaseline(directory?: string): BaselineDoc | null {
  const path = baselinePath(directory);
  if (path === null) return null;
  return JSON.parse(readFileSync(path, "utf8")) as BaselineDoc;
}

/** `major.minor` of the running Node, matching how the profile records it. */
export function languageVersion(): string {
  return String(process.versions.node.split(".")[0]);
}

export interface CompareOptions {
  directory?: string;
  implementation?: string;
  observedLanguageVersion?: string;
  profileEnv?: string;
}

/**
 * Compare `measuredMs` against the recorded baseline for this port.
 *
 * Every `reason` below is a refusal to compare, not a failure to measure: the
 * number was measured either way and is reported either way. What is withheld
 * is the verdict, because the two sides would not be like for like.
 */
export function compare(
  measuredMs: number,
  corpusId: string,
  options: CompareOptions = {},
): Comparison {
  const doc = loadBaseline(options.directory);
  const tolerancePct = doc?.tolerance_pct ?? 8.0;
  const implementation = options.implementation ?? IMPLEMENTATION;
  const lang = options.observedLanguageVersion ?? languageVersion();

  const declined = (reason: string, baselineMs: number | null = null): Comparison => ({
    measuredMs,
    baselineMs,
    regressionPct: null,
    tolerancePct,
    comparable: false,
    reason,
    holds: false,
  });

  if (doc === null) return declined(`no ${BASELINE_FILENAME} in this checkout`);

  const wantProfile = doc.profile?.id;
  const haveProfile = (
    options.profileEnv ?? process.env[PROFILE_ENV_VAR] ?? ""
  ).trim();
  if (haveProfile === "") {
    return declined(
      `${PROFILE_ENV_VAR} is unset, so this machine does not claim to be ` +
        `'${wantProfile}'; the baseline was recorded there`,
    );
  }
  if (haveProfile !== wantProfile) {
    return declined(
      `${PROFILE_ENV_VAR}='${haveProfile}' but the baseline was recorded on ` +
        `'${wantProfile}'`,
    );
  }

  const wantLang = doc.profile?.language_versions?.[implementation];
  if (wantLang !== undefined && String(wantLang) !== lang) {
    return declined(
      `${implementation} ${lang} is not the ${wantLang} the baseline was ` +
        `recorded on; runtime versions differ by more than the bar`,
    );
  }

  const wantCorpus = doc.corpus;
  if (wantCorpus !== undefined && wantCorpus !== corpusId) {
    return declined(
      `corpus '${corpusId}' is not the '${wantCorpus}' the baseline was ` +
        `recorded on; latency scales with essay length`,
    );
  }

  const recorded = doc.implementations?.[implementation]?.pooled_median_ms;
  if (recorded === null || recorded === undefined) {
    return declined(
      `no baseline recorded for ${implementation} yet — the next release ` +
        `records one`,
    );
  }
  if (recorded <= 0) {
    return declined(
      `recorded baseline for ${implementation} is not positive`,
      recorded,
    );
  }

  const regressionPct = (measuredMs / recorded - 1.0) * 100.0;
  return {
    measuredMs,
    baselineMs: recorded,
    regressionPct,
    tolerancePct,
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
    `latency ${c.measuredMs.toFixed(3)} ms vs ${c.baselineMs!.toFixed(3)} ms ` +
    `at the last release — ${sign}${c.regressionPct!.toFixed(2)}% against a ` +
    `${c.tolerancePct.toFixed(0)}% bar`
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
