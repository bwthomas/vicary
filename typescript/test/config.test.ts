/**
 * Environment-variable resolution: which name wins, and what counts as unset.
 *
 * This port reads five variables — the asset path, the redaction arm, the corpus
 * TSV, the corpus directory and the Census CSV — and tested the resolution of
 * none. Python has 34 tests here, but most of them are the seven legacy names it
 * kept a fallback for when it became a library, which is history this port never
 * had. What was NOT justified is the other half, and this file is it.
 *
 * Why resolution specifically. Every one of these five is read once, at the edge,
 * and a wrong answer does not raise: pointing `VICARY_ASSET_PATH` at a directory
 * with no gazetteer loads an empty index and redacts every public figure in every
 * essay; a corpus variable resolving to `""` reports a gate as NOT MEASURED, which
 * reads as "operator supplied no data" rather than "the operator did and we
 * dropped it". Both look exactly like success from every log line.
 *
 * The whitespace cases are not padding. A `.env` file written by hand carries
 * trailing spaces, and `" "` must count as unset rather than as a path to a
 * directory whose name is a space.
 *
 * The declared gap this closes is recorded in `conformance/coverage.json`, and
 * `tools/tests/test_coverage_parity.py` fails if its entry outlives it.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ASSET_PATH_ENV_VAR,
  assetSearchPath,
} from "../src/asset.js";
import {
  EVAL_CENSUS_CSV_ENV_VAR,
  censusSource,
} from "../src/census.js";
import {
  EVAL_CORPUS_DIR_ENV_VAR,
  EVAL_CORPUS_PREFERRED_FILENAME,
  EVAL_CORPUS_TSV_ENV_VAR,
  corpusSource,
} from "../src/corpus.js";
import {
  DEFAULT_NAME_DETECTION,
  NAMES_GAZETTEER,
  NAMES_IDENTITY,
  NAMES_LOWERCASE,
  NAME_DETECTION_ENV_VAR,
  nameDetection,
} from "../src/redact.js";

/**
 * Run `body` with exactly these variables set, restoring the environment after.
 *
 * `undefined` deletes rather than setting the string "undefined" — the trap that
 * makes an "unset" case silently test a set one.
 */
function withEnv<T>(vars: Record<string, string | undefined>, body: () => T): T {
  const saved = new Map<string, string | undefined>();
  for (const name of Object.keys(vars)) saved.set(name, process.env[name]);
  try {
    for (const [name, value] of Object.entries(vars)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    return body();
  } finally {
    for (const [name, value] of saved) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
}

// ---------------------------------------------------------------------------
// The asset path
// ---------------------------------------------------------------------------

test("the asset override is consulted before either bundled location", () => {
  // An operator pointing at a different cut must not be silently overruled by
  // the copy this package vendored, which is the whole purpose of the override.
  withEnv({ [ASSET_PATH_ENV_VAR]: "/tmp/some-other-cut" }, () => {
    assert.equal(assetSearchPath()[0], "/tmp/some-other-cut");
  });
});

test("an unset asset override contributes no candidate at all", () => {
  // Not an empty-string entry in the list: `join("", filename)` resolves to a
  // relative path, so an empty candidate silently searches the process's working
  // directory.
  const withOverride = withEnv({ [ASSET_PATH_ENV_VAR]: "/tmp/x" }, assetSearchPath);
  const without = withEnv({ [ASSET_PATH_ENV_VAR]: undefined }, assetSearchPath);
  assert.equal(without.length, withOverride.length - 1);
  for (const candidate of without) assert.notEqual(candidate, "");
});

test("a whitespace-only asset override is unset, not a directory named space", () => {
  for (const blank of ["", " ", "   ", "\t", "\n"]) {
    const path = withEnv({ [ASSET_PATH_ENV_VAR]: blank }, assetSearchPath);
    assert.deepEqual(
      path,
      withEnv({ [ASSET_PATH_ENV_VAR]: undefined }, assetSearchPath),
      JSON.stringify(blank),
    );
  }
});

test("the asset override is trimmed rather than used raw", () => {
  withEnv({ [ASSET_PATH_ENV_VAR]: "  /tmp/padded  " }, () => {
    assert.equal(assetSearchPath()[0], "/tmp/padded");
  });
});

test("the bundled locations stay in most-specific-first order", () => {
  // The vendored copy is what an installed package has; the monorepo's Python
  // package is what a checkout has before `npm run sync-assets`. Reversing them
  // makes a checkout read a stale asset that a publish would never ship.
  const path = withEnv({ [ASSET_PATH_ENV_VAR]: undefined }, assetSearchPath);
  const vendored = path.findIndex((p) => p.includes("assets"));
  const monorepo = path.findIndex((p) => p.includes("python"));
  assert.ok(vendored >= 0, `no vendored candidate in ${JSON.stringify(path)}`);
  assert.ok(monorepo >= 0, `no monorepo candidate in ${JSON.stringify(path)}`);
  assert.ok(vendored < monorepo, "the vendored copy must be searched first");
});

// ---------------------------------------------------------------------------
// The redaction arm
// ---------------------------------------------------------------------------

test("the environment supplies the arm when the caller does not", () => {
  withEnv({ [NAME_DETECTION_ENV_VAR]: "identity" }, () => {
    assert.equal(nameDetection(), NAMES_IDENTITY);
  });
  withEnv({ [NAME_DETECTION_ENV_VAR]: "gazetteer" }, () => {
    assert.equal(nameDetection(), NAMES_GAZETTEER);
  });
});

test("an explicit argument beats the environment", () => {
  // The precedence a host depends on to override a deployment-wide default for
  // one call. Reversing it makes the argument decorative, and every caller that
  // passes one keeps getting the env's answer.
  withEnv({ [NAME_DETECTION_ENV_VAR]: "identity" }, () => {
    assert.equal(nameDetection(NAMES_LOWERCASE), NAMES_LOWERCASE);
    assert.equal(nameDetection(NAMES_GAZETTEER), NAMES_GAZETTEER);
  });
});

test("an unset environment reaches the code default", () => {
  withEnv({ [NAME_DETECTION_ENV_VAR]: undefined }, () => {
    assert.equal(nameDetection(), DEFAULT_NAME_DETECTION);
    assert.equal(nameDetection(), NAMES_LOWERCASE);
  });
});

test("a whitespace-only arm is unset rather than unrecognised", () => {
  // Both land on the default here, so this pins WHY rather than what: an unset
  // variable and a typo must not be distinguishable to a caller, because the
  // fail-safe for both is the same and a future edit that split them would
  // change the typo case into `identity`.
  for (const blank of ["", " ", "\t\n"]) {
    withEnv({ [NAME_DETECTION_ENV_VAR]: blank }, () => {
      assert.equal(nameDetection(), DEFAULT_NAME_DETECTION, JSON.stringify(blank));
    });
  }
});

test("the arm variable is spelled the same as every other port's", () => {
  // Three front doors reading three different names is a deployment that thinks
  // it configured all of them.
  assert.equal(NAME_DETECTION_ENV_VAR, "VICARY_NAME_DETECTION");
});

// ---------------------------------------------------------------------------
// The corpus
// ---------------------------------------------------------------------------

test("the explicit TSV wins over the directory form", () => {
  withEnv(
    {
      [EVAL_CORPUS_TSV_ENV_VAR]: "/data/explicit.tsv",
      [EVAL_CORPUS_DIR_ENV_VAR]: "/data/dir",
    },
    () => assert.equal(corpusSource(), "/data/explicit.tsv"),
  );
});

test("the directory form appends the preferred filename", () => {
  withEnv(
    { [EVAL_CORPUS_TSV_ENV_VAR]: undefined, [EVAL_CORPUS_DIR_ENV_VAR]: "/data/dir" },
    () => {
      assert.equal(corpusSource(), `/data/dir/${EVAL_CORPUS_PREFERRED_FILENAME}`);
      assert.equal(EVAL_CORPUS_PREFERRED_FILENAME, "corpus.tsv");
    },
  );
});

test("neither variable set resolves to the empty string, not to a bare filename", () => {
  // `""` is what the gate suite reads as NOT MEASURED. A bare "corpus.tsv" would
  // instead be looked up relative to the working directory, so a gate would
  // measure whatever happened to be beside the process.
  withEnv(
    { [EVAL_CORPUS_TSV_ENV_VAR]: undefined, [EVAL_CORPUS_DIR_ENV_VAR]: undefined },
    () => assert.equal(corpusSource(), ""),
  );
});

test("a whitespace-only corpus variable is unset, and falls through to the next", () => {
  // The realistic `.env` defect: the TSV name is present but empty, and the
  // directory below it is the real configuration. Treating `" "` as set makes the
  // corpus unreadable while reporting a path.
  withEnv(
    { [EVAL_CORPUS_TSV_ENV_VAR]: "   ", [EVAL_CORPUS_DIR_ENV_VAR]: "/data/dir" },
    () => assert.equal(corpusSource(), `/data/dir/${EVAL_CORPUS_PREFERRED_FILENAME}`),
  );
  withEnv(
    { [EVAL_CORPUS_TSV_ENV_VAR]: " ", [EVAL_CORPUS_DIR_ENV_VAR]: "  " },
    () => assert.equal(corpusSource(), ""),
  );
});

test("both corpus paths are trimmed rather than used raw", () => {
  withEnv({ [EVAL_CORPUS_TSV_ENV_VAR]: "  /data/padded.tsv \n" }, () =>
    assert.equal(corpusSource(), "/data/padded.tsv"),
  );
  withEnv(
    { [EVAL_CORPUS_TSV_ENV_VAR]: undefined, [EVAL_CORPUS_DIR_ENV_VAR]: " /data/dir " },
    () => assert.equal(corpusSource(), `/data/dir/${EVAL_CORPUS_PREFERRED_FILENAME}`),
  );
});

// ---------------------------------------------------------------------------
// The Census file
// ---------------------------------------------------------------------------

test("the Census path is read, trimmed, and empty when unset", () => {
  withEnv({ [EVAL_CENSUS_CSV_ENV_VAR]: "/data/Names_2010Census.csv" }, () =>
    assert.equal(censusSource(), "/data/Names_2010Census.csv"),
  );
  withEnv({ [EVAL_CENSUS_CSV_ENV_VAR]: "  /data/padded.csv  " }, () =>
    assert.equal(censusSource(), "/data/padded.csv"),
  );
  for (const blank of [undefined, "", " ", "\t"]) {
    withEnv({ [EVAL_CENSUS_CSV_ENV_VAR]: blank }, () =>
      assert.equal(censusSource(), "", JSON.stringify(blank)),
    );
  }
});

test("every variable this port reads is spelled the way the others spell it", () => {
  // The list is asserted rather than described, so adding a sixth variable to one
  // port and not the others shows up here as a failing name rather than as a
  // deployment that configured two of three front doors.
  assert.deepEqual(
    [
      ASSET_PATH_ENV_VAR,
      NAME_DETECTION_ENV_VAR,
      EVAL_CORPUS_TSV_ENV_VAR,
      EVAL_CORPUS_DIR_ENV_VAR,
      EVAL_CENSUS_CSV_ENV_VAR,
    ].sort(),
    [
      "VICARY_ASSET_PATH",
      "VICARY_EVAL_CENSUS_CSV",
      "VICARY_EVAL_CORPUS_DIR",
      "VICARY_EVAL_CORPUS_TSV",
      "VICARY_NAME_DETECTION",
    ],
  );
});
