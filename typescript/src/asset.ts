/**
 * Load the gazetteer asset — the same bytes the Python package loads.
 *
 * The asset is a gzipped, line-oriented text file, chosen over a binary format
 * precisely so three languages can read it without a schema compiler:
 *
 *     #!gazetteer 5                 format number, checked not sniffed
 *     #!meta {"cut_date": ...}      provenance, one JSON object
 *     #!tier demonym 1047           tier name and its DECLARED entry count
 *     abidjanese                    one normalised entry per line
 *     ...
 *     #!tier full 295049
 *     ...
 *
 * Two properties matter more than convenience here.
 *
 * **The format number is refused, not tolerated.** An unknown format means the
 * file's meaning has changed, and a reader that skips lines it does not recognise
 * degrades into a smaller gazetteer — which redacts MORE, reads as privacy-safe,
 * and is invisible to any test that only checks something was masked.
 *
 * **The declared tier count is checked against the parsed count.** A truncated
 * read is the same silent failure in a different costume: fewer notable people
 * means fewer public figures kept, so a student's essay about Rosa Parks starts
 * coming back with her name removed. Failing loudly on a short read is the whole
 * point; see the same reasoning in the Python builder, which refuses a truncated
 * upstream parse for exactly this reason.
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Asset format this reader understands. Refuse anything else. */
export const SUPPORTED_FORMAT = 5;

export const ASSET_FILENAME = "notability.txt.gz";
export const MANIFEST_FILENAME = "MANIFEST.json";

/** Environment override, spelled the same as the Python package's. */
export const ASSET_PATH_ENV_VAR = "VICARY_ASSET_PATH";

export interface AssetMeta {
  readonly cut_date?: string;
  readonly [key: string]: unknown;
}

export interface Gazetteer {
  /** Asset format number, as declared in the file's first line. */
  readonly format: number;
  /** The `#!meta` object: provenance and build parameters. */
  readonly meta: AssetMeta;
  /** Tier name to its entries. Entries are already normalised (lowercased). */
  readonly tiers: ReadonlyMap<string, ReadonlySet<string>>;
  /** sha256 of the compressed bytes actually read. */
  readonly sha256: string;
  /** Where it was read from, for error messages that name a file. */
  readonly path: string;
}

/**
 * Candidate asset locations, most specific first.
 *
 * The env override comes first so an operator can point at a different cut
 * without reinstalling. Then this package's own vendored copy, which is what a
 * published install has. Then the monorepo's Python package, which is what a
 * checkout has before `npm run sync-assets` — so `git clone && npm test` works
 * with no bootstrap step, rather than failing in a way that reads as a broken
 * port.
 */
export function assetSearchPath(): string[] {
  const here = dirname(fileURLToPath(import.meta.url));
  // dist/ at runtime, src/ under a type-stripping runner — resolve from both.
  const packageRoot = resolve(here, "..");
  const repoRoot = resolve(packageRoot, "..");
  const candidates: string[] = [];
  const override = (process.env[ASSET_PATH_ENV_VAR] ?? "").trim();
  if (override) candidates.push(override);
  candidates.push(join(packageRoot, "assets"));
  candidates.push(resolve(packageRoot, "..", "assets"));
  candidates.push(join(repoRoot, "python", "src", "vicary", "data"));
  return candidates;
}

function locate(): string {
  const tried = assetSearchPath();
  for (const directory of tried) {
    if (existsSync(join(directory, ASSET_FILENAME))) return directory;
  }
  throw new Error(
    `no ${ASSET_FILENAME} found. Looked in: ${tried.join(", ")}. ` +
      `In a checkout, run \`npm run sync-assets\`; set ${ASSET_PATH_ENV_VAR} to ` +
      `override.`,
  );
}

/**
 * Parse the decompressed asset text.
 *
 * Exported so a test can feed it a deliberately malformed document. A parser
 * reachable only through a 2.1 MB file on disk is a parser whose failure paths
 * are never exercised.
 */
export function parseAsset(text: string): {
  format: number;
  meta: AssetMeta;
  tiers: Map<string, Set<string>>;
} {
  const lines = text.split("\n");
  const first = lines[0] ?? "";
  const header = /^#!gazetteer (\d+)$/.exec(first);
  if (!header) {
    throw new Error(
      `asset does not begin with a #!gazetteer header (got ${JSON.stringify(
        first.slice(0, 40),
      )})`,
    );
  }
  const format = Number(header[1]);
  if (format !== SUPPORTED_FORMAT) {
    throw new Error(
      `asset format ${format} is not ${SUPPORTED_FORMAT}. Refusing to read it ` +
        `rather than skipping the parts that changed: a partially understood ` +
        `gazetteer is a smaller one, and a smaller one redacts more while ` +
        `looking correct.`,
    );
  }

  let meta: AssetMeta = {};
  const tiers = new Map<string, Set<string>>();
  const declared = new Map<string, number>();
  let current: Set<string> | null = null;

  for (let i = 1; i < lines.length; i += 1) {
    const line = lines[i] ?? "";
    if (line === "") continue;
    if (line.startsWith("#!meta ")) {
      meta = JSON.parse(line.slice("#!meta ".length)) as AssetMeta;
      continue;
    }
    const tier = /^#!tier (\S+) (\d+)$/.exec(line);
    if (tier) {
      const name = tier[1]!;
      current = new Set<string>();
      tiers.set(name, current);
      declared.set(name, Number(tier[2]));
      continue;
    }
    if (line.startsWith("#!")) {
      // An unrecognised directive is a format change the header did not admit
      // to. Same reasoning as the format check: do not skip it.
      throw new Error(
        `unrecognised directive ${JSON.stringify(line.slice(0, 40))} at line ` +
          `${i + 1}; the asset format changed without its number changing`,
      );
    }
    if (current === null) {
      throw new Error(`entry at line ${i + 1} appears before any #!tier`);
    }
    current.add(line);
  }

  for (const [name, count] of declared) {
    const actual = tiers.get(name)!.size;
    if (actual !== count) {
      throw new Error(
        `tier ${name} declares ${count} entries and parsed ${actual}. A short ` +
          `read here removes public figures from the keep list, so an essay ` +
          `about a historical figure comes back with their name redacted.`,
      );
    }
  }
  return { format, meta, tiers };
}

let cached: Gazetteer | null = null;

/** Load and cache the gazetteer. */
export function loadGazetteer(options: { directory?: string } = {}): Gazetteer {
  if (cached !== null && options.directory === undefined) return cached;
  const directory = options.directory ?? locate();
  const assetPath = join(directory, ASSET_FILENAME);
  const compressed = readFileSync(assetPath);
  const sha256 = createHash("sha256").update(compressed).digest("hex");

  const manifestPath = join(directory, MANIFEST_FILENAME);
  if (existsSync(manifestPath)) {
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as {
      assets: Record<string, { sha256: string; bytes: number; format: number }>;
    };
    const entry = manifest.assets[ASSET_FILENAME];
    if (entry !== undefined && entry.sha256 !== sha256) {
      throw new Error(
        `${assetPath} sha256 ${sha256} does not match the manifest's ` +
          `${entry.sha256}. The asset was modified or truncated in transit; ` +
          `every front door must load identical bytes or "byte-identical ` +
          `output" is not a claim anybody can make.`,
      );
    }
  }

  const text = gunzipSync(compressed).toString("utf8");
  const { format, meta, tiers } = parseAsset(text);
  const gazetteer: Gazetteer = {
    format,
    meta,
    tiers: tiers as ReadonlyMap<string, ReadonlySet<string>>,
    sha256,
    path: assetPath,
  };
  if (options.directory === undefined) cached = gazetteer;
  return gazetteer;
}

/** Forget the cached gazetteer. For tests. */
export function resetGazetteerCache(): void {
  cached = null;
}
