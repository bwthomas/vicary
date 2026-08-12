/**
 * The false-positive control the fixture cannot provide.
 *
 * The fixture reports zero leaks on bare surnames partly because the private
 * surnames in it are rare — Okonkwo, Bramwell, Pritchard, Ybarra. A clean
 * control needs an *unlikely* clean, so this scores the single-token tiers
 * against **every American surname**: the population-weighted rate at which a
 * bare surname resolves notable, regardless of whose surname it is.
 *
 * Read the headline number as: *for a private person named by bare surname only
 * — no first name, no title, no same-document corroboration — this share
 * resolves "notable" and leaks.* It is conditional on that surface form, which
 * is a minority of private-name mentions in real prose, so it is not an
 * essay-level leak rate.
 *
 * The source is the US Census 2010 surname file, and this repository now ships
 * the two columns of it this measurement uses — see `conformance/census/`, built
 * by `tools/census_build.py`. So the gate is measured on a bare checkout and in
 * CI, which it was not: census.gov stopped serving the upstream, and the gate
 * reported NOT MEASURED everywhere but on a machine holding a hand-downloaded
 * copy.
 *
 * `VICARY_EVAL_CENSUS_CSV` still wins when set — an operator holding a newer
 * release gets the number their file gives.
 *
 * **For that operator file, this port reads the extracted `.csv` only.** Python
 * additionally accepts the distributed `.zip` because its standard library has a
 * zip reader and Node's does not. A `.zip` here is refused by name rather than
 * parsed as text, since the alternative is a binary read that yields zero rows —
 * which is a *lower* exposure rate than the truth, and the wrong direction to
 * fail in silently. The shipped table sidesteps this entirely: it is gzip, which
 * `node:zlib` reads.
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { gunzipSync } from "node:zlib";

import { conformanceDir } from "./conformance.js";
import { normalize, type GazetteerIndex } from "./gazetteer.js";

/** Where a locally-held copy of the Census surname file is configured. */
export const EVAL_CENSUS_CSV_ENV_VAR = "VICARY_EVAL_CENSUS_CSV";

/**
 * The member name inside the distributed archive, and the file this port wants
 * handed to it directly.
 */
export const CENSUS_SURNAMES_MEMBER = "Names_2010Census.csv";

/** Where the operator gets the file, quoted in the error when it is missing. */
export const CENSUS_SURNAMES_URL =
  "https://www2.census.gov/topics/genealogy/2010surnames/names.zip";

/**
 * The row-count floor. Not decoration: this list is scored *against* the
 * single-token tiers, so a short read shrinks the denominator and reports a
 * more comfortable exposure rate than the truth.
 */
const MINIMUM_ROWS = 100_000;

/** How much of the US surname population the single-token tiers claim. */
export interface Exposure {
  /** Distinct surnames in the Census file. */
  surnamesScored: number;
  /** Distinct surnames matching some single-token tier. */
  surnamesMatched: number;
  /** Total bearers across the file. */
  bearersTotal: number;
  /** Bearers whose surname matches some single-token tier. */
  bearersExposed: number;
  /** Bearers exposed via the `short` tier specifically. */
  bearersViaShort: number;
  /** Bearers exposed via a single-token `place` entry. */
  bearersViaPlace: number;
  /**
   * Bearers exposed via the `demonym` tier. Counted here for the same reason
   * the other two are: it is a KEEP granted to a bare single token, which is
   * exactly the surface form this control measures. Leaving it out would make
   * adding a tier look free.
   */
  bearersViaDemonym: number;
}

/** Population-weighted exposure, as a percentage. The headline. */
export function rate(exposure: Exposure): number {
  return (100.0 * exposure.bearersExposed) / exposure.bearersTotal;
}

export function shortRate(exposure: Exposure): number {
  return (100.0 * exposure.bearersViaShort) / exposure.bearersTotal;
}

export function placeRate(exposure: Exposure): number {
  return (100.0 * exposure.bearersViaPlace) / exposure.bearersTotal;
}

export function demonymRate(exposure: Exposure): number {
  return (100.0 * exposure.bearersViaDemonym) / exposure.bearersTotal;
}

export function distinctRate(exposure: Exposure): number {
  return (100.0 * exposure.surnamesMatched) / exposure.surnamesScored;
}

/** Configured path to a local Census surname file, or `""`. */
export function censusSource(): string {
  return (process.env[EVAL_CENSUS_CSV_ENV_VAR] ?? "").trim();
}

/**
 * `{normalised surname: number of US bearers}` from the Census CSV text.
 *
 * Field-indexed off the header rather than positional, so a column added
 * upstream shifts nothing. The file carries no quoted fields — every row is 11
 * bare comma-separated values — so this splits rather than running a full CSV
 * parser, and a row that does not yield an integer count is skipped the same
 * way Python's `DictReader` loop skips it.
 */
export function parseCensusSurnames(text: string): Map<string, number> {
  const counts = new Map<string, number>();
  const lines = text.split(/\r?\n/);
  const header = (lines[0] ?? "").split(",");
  const nameAt = header.indexOf("name");
  const countAt = header.indexOf("count");
  if (nameAt === -1 || countAt === -1) {
    throw new Error(
      `Census surname file has no 'name'/'count' header; got ${header.join(",")}`,
    );
  }

  for (const line of lines.slice(1)) {
    if (line === "") continue;
    const fields = line.split(",");
    const name = normalize(fields[nameAt] ?? "");
    if (name === "" || name === "all other names") continue;
    const count = Number(fields[countAt]);
    if (!Number.isInteger(count)) continue;
    counts.set(name, count);
  }

  if (counts.size < MINIMUM_ROWS) {
    throw new Error(
      `Census surname file parsed to only ${counts.size} rows; expected ~162k. ` +
        "Refusing to score exposure against a truncated list, because the " +
        "failure mode is a more comfortable rate than the truth.",
    );
  }
  return counts;
}

/** Directory under `conformance/` holding the shipped table and its provenance. */
const SHIPPED_DIRNAME = "census";
const SHIPPED_TABLE_FILENAME = "surnames.txt.gz";
const SHIPPED_PROFILE_FILENAME = "profile.json";

/** `conformance/census/`, or `undefined` outside a checkout. */
export function shippedDir(): string | undefined {
  let root: string;
  try {
    root = conformanceDir();
  } catch {
    return undefined;
  }
  const candidate = join(root, SHIPPED_DIRNAME);
  return existsSync(join(candidate, SHIPPED_TABLE_FILENAME))
    ? candidate
    : undefined;
}

/**
 * `{normalised surname: bearers}` from the table this repository ships.
 *
 * The digest in `profile.json` is checked, not trusted. This table is used to
 * SUBTRACT exposure from a permissive tier, so a truncated or edited copy scores
 * the gazetteer against a smaller America and reads as a *better* number — the
 * one direction this measurement must never fail in quietly. A bad digest throws
 * rather than degrading.
 */
export function loadShippedCensus(directory?: string): Map<string, number> {
  const dir = directory ?? shippedDir();
  if (dir === undefined) {
    throw new Error(
      `no conformance/${SHIPPED_DIRNAME}/ above this module. The shipped table ` +
        `lives in the repository, not in an installed package.`,
    );
  }

  const payload = readFileSync(join(dir, SHIPPED_TABLE_FILENAME));
  const profile = JSON.parse(
    readFileSync(join(dir, SHIPPED_PROFILE_FILENAME), "utf8"),
  );
  const expected: string = profile?.table?.sha256 ?? "";
  const actual = createHash("sha256").update(payload).digest("hex");
  if (expected !== "" && actual !== expected) {
    throw new Error(
      `${SHIPPED_TABLE_FILENAME} has sha256 ${actual}, but ` +
        `${SHIPPED_PROFILE_FILENAME} pins ${expected}. Refusing to score the ` +
        `gazetteer against a table that is not the one this repository ` +
        `measured, because a short read reads as a better number. Rebuild with ` +
        `\`python tools/census_build.py --write\`.`,
    );
  }

  const counts = new Map<string, number>();
  for (const line of gunzipSync(payload).toString("utf8").split("\n")) {
    if (line === "") continue;
    const tab = line.indexOf("\t");
    if (tab === -1) continue;
    counts.set(line.slice(0, tab), Number(line.slice(tab + 1)));
  }
  if (counts.size < MINIMUM_ROWS) {
    throw new Error(
      `${SHIPPED_TABLE_FILENAME} parsed to only ${counts.size} rows; expected ` +
        `at least ${MINIMUM_ROWS}.`,
    );
  }
  return counts;
}

/**
 * `{normalised surname: bearers}`, resolved in this order:
 *
 * 1. An explicit `source`, or `VICARY_EVAL_CENSUS_CSV`. An operator holding a
 *    newer Census release still wins, and gets the number *their* file gives.
 * 2. The table shipped in `conformance/census/`, which is the same 162,253 rows
 *    the 2010 release carries and therefore the same rate to the last bearer.
 *    This is why the gate no longer skips on a bare checkout.
 *
 * There is no third step. census.gov answers the documented URL with a WAF
 * rejection page under a 200 status, which is why the shipped table exists.
 */
export function loadCensus(source?: string): Map<string, number> {
  const path = (source ?? censusSource()).trim();
  if (path === "") {
    if (shippedDir() !== undefined) return loadShippedCensus();
    throw new Error(
      `no conformance/${SHIPPED_DIRNAME}/ in this tree and no ` +
        `${EVAL_CENSUS_CSV_ENV_VAR} set. Point that at a copy of ` +
        `${CENSUS_SURNAMES_MEMBER}, extracted from ${CENSUS_SURNAMES_URL}, or ` +
        `run from a checkout.`,
    );
  }
  if (path.toLowerCase().endsWith(".zip")) {
    throw new Error(
      `${path} is a .zip and this port reads the extracted .csv only. ` +
        `Extract ${CENSUS_SURNAMES_MEMBER} from it and point ` +
        `${EVAL_CENSUS_CSV_ENV_VAR} at that.`,
    );
  }
  return parseCensusSurnames(readFileSync(path, "utf8"));
}

/** Score the loaded gazetteer's single-token tiers against the Census file. */
export function measureExposure(
  census: Map<string, number>,
  gaz: GazetteerIndex,
): Exposure {
  const singleTokenPlaces = new Set(
    [...gaz.place].filter((name) => !name.includes(" ")),
  );
  const single = new Set([...singleTokenPlaces, ...gaz.short, ...gaz.demonym]);

  let bearersTotal = 0;
  let bearersExposed = 0;
  let surnamesMatched = 0;
  let viaShort = 0;
  let viaPlace = 0;
  let viaDemonym = 0;

  for (const [name, count] of census) {
    bearersTotal += count;
    if (single.has(name)) {
      bearersExposed += count;
      surnamesMatched += 1;
    }
    if (gaz.short.has(name)) viaShort += count;
    if (singleTokenPlaces.has(name)) viaPlace += count;
    if (gaz.demonym.has(name)) viaDemonym += count;
  }

  return {
    surnamesScored: census.size,
    surnamesMatched,
    bearersTotal,
    bearersExposed,
    bearersViaShort: viaShort,
    bearersViaPlace: viaPlace,
    bearersViaDemonym: viaDemonym,
  };
}

const group = (value: number): string => value.toLocaleString("en-US");

/** The report block, for a CLI or a gate's failure message. */
export function renderExposure(exposure: Exposure): string {
  return [
    "BARE-SURNAME FALSE-POSITIVE RATE (US Census 2010 surname file)",
    `  distinct surnames scored   ${group(exposure.surnamesScored)}`,
    `  any single-token tier hit  ${group(exposure.surnamesMatched)} ` +
      `(${distinctRate(exposure).toFixed(2)}% of distinct)`,
    `  population-weighted rate   ${rate(exposure).toFixed(2)}% ` +
      `(${group(exposure.bearersExposed)} / ${group(exposure.bearersTotal)} bearers)`,
    `    via the short tier       ${shortRate(exposure).toFixed(2)}%`,
    `    via single-token places  ${placeRate(exposure).toFixed(2)}%`,
    `    via the demonym tier     ${demonymRate(exposure).toFixed(2)}%`,
    "  reads as: for a private person named by BARE SURNAME ONLY — no",
    "            first name, no title, no corroboration — this share",
    "            resolves 'notable'. Conditional on that surface form.",
  ].join("\n");
}
