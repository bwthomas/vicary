/**
 * That the built artifact carries the asset, and that the release path's own logic
 * works before a release exercises it.
 *
 * The counterpart of `ruby/test/release_test.rb` and `python/tests/test_packaging.py`,
 * and this port had neither. A packaging break is invisible to every other suite:
 * every test here passes against a working tree, and the tarball is what somebody
 * else installs. This is the one front door whose publish path has never completed
 * — see the repository README on the blocked unscoped name — so its release logic
 * is also the least exercised in the repository.
 *
 * Three pieces of logic used to live as inline shell and `node -e` heredocs inside
 * `.github/workflows/release-npm.yml`, where the only way to run them was to cut a
 * release: the conformance gate, the pack-contents reader, and the "is it really
 * published" check. They are now `scripts/release-gate.mjs`,
 * `scripts/pack-contents.mjs` and `scripts/registry-serves.mjs`, and this file
 * drives all three in both directions with no network, no credential and no tag —
 * including the allow branches this port has never once reached.
 *
 * The declared gap this closes is recorded in `conformance/coverage.json`, and
 * `tools/tests/test_coverage_parity.py` fails if its entry outlives it.
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

/** The scripts live outside `src`, so they are loaded by URL rather than imported. */
const SCRIPTS = new URL("../../scripts/", import.meta.url);

/* eslint-disable @typescript-eslint/no-explicit-any */
const packContents: any = await import(new URL("pack-contents.mjs", SCRIPTS).href);
const releaseGate: any = await import(new URL("release-gate.mjs", SCRIPTS).href);
const registryServes: any = await import(new URL("registry-serves.mjs", SCRIPTS).href);
/* eslint-enable @typescript-eslint/no-explicit-any */

/** The npm package's own root — where `package.json`, `src` and `test` live. */
const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

function board(matched: number, requiring: number) {
  return {
    fixtureVersion: "test",
    referenceArm: "test",
    total: 54,
    matched,
    requiringMasking: requiring,
    matchedRequiringMasking: matched,
    outcomes: [],
  };
}

/** Collects what a script would have printed, so the give-up path is inspectable. */
function recorder() {
  const lines: string[] = [];
  return {
    lines,
    log: (line = "") => lines.push(String(line)),
    error: (line = "") => lines.push(String(line)),
    text: () => lines.join("\n"),
  };
}

// ---------------------------------------------------------------------------
// The conformance gate
// ---------------------------------------------------------------------------

test("an incomplete port may not publish", () => {
  const decision = releaseGate.decide(board(0, 38));
  assert.equal(
    decision.publishable,
    false,
    "a port matching 0 of 38 masking-required frames was cleared to publish. " +
      "That is a package called vicary that does not redact.",
  );
  assert.match(decision.reason, /REFUSING TO PUBLISH/);
});

test("one frame short may not publish", () => {
  // The interesting boundary. An off-by-one here reads as done on every log line
  // that prints a ratio and rounds it.
  assert.equal(releaseGate.decide(board(37, 38)).publishable, false);
});

test("a complete port may publish", () => {
  // The branch this test existed to reach before any release had. If the gate only
  // ever refuses, it is indistinguishable from a gate that is stuck shut.
  const decision = releaseGate.decide(board(38, 38));
  assert.equal(decision.publishable, true, decision.reason);
  assert.match(decision.reason, /38 of 38/);
  assert.doesNotMatch(decision.reason, /REFUSING/);
});

test("a denominator of zero is refused rather than read as success", () => {
  // `0 === 0` is the shape of every scraped-number gate that ever passed by
  // accident. A spec that scores nothing must not clear a publish.
  for (const total of [0, null, undefined]) {
    const decision = releaseGate.decide({
      matchedRequiringMasking: 0,
      requiringMasking: total,
    });
    assert.equal(decision.publishable, false, `total=${String(total)}`);
    assert.match(decision.reason, /denominator of zero/);
  }
});

// ---------------------------------------------------------------------------
// What the tarball carries
// ---------------------------------------------------------------------------

test("both shapes npm pack has emitted are read the same way", () => {
  // An ARRAY of one entry up to npm 11, an OBJECT KEYED BY PACKAGE NAME from 12
  // on. Reading `[0].files` off the object is what died with "Cannot read
  // properties of undefined" — a broken reader that read like a broken tarball.
  const files = [{ path: "dist/index.js" }, { path: "assets/MANIFEST.json" }];
  assert.deepEqual(packContents.packedFiles([{ files }]), [
    "dist/index.js",
    "assets/MANIFEST.json",
  ]);
  assert.deepEqual(packContents.packedFiles({ "@bwthomas/vicary": { files } }), [
    "dist/index.js",
    "assets/MANIFEST.json",
  ]);
});

test("a shape the reader does not know throws rather than reporting no files", () => {
  // The distinction the whole check turns on: "no files" and "cannot see the
  // files" must not reach the same conclusion when the conclusion is whether the
  // gazetteer shipped.
  for (const raw of [null, undefined, 42, "a string", {}, [], [{}], { pkg: {} }]) {
    assert.throws(
      () => packContents.packedFiles(raw),
      /shape this check does not know/,
      `${JSON.stringify(raw) ?? "undefined"} should not read as an empty tarball`,
    );
  }
  // A file entry with no path is the same class of defect one level down.
  assert.throws(
    () => packContents.packedFiles([{ files: [{ size: 1 }] }]),
    /no string path/,
  );
});

test("the asset check names every file it needs, and passes only on all of them", () => {
  assert.deepEqual(packContents.missingAssets(packContents.REQUIRED_ASSET_FILES), []);
  assert.deepEqual(packContents.missingAssets([]), packContents.REQUIRED_ASSET_FILES);
  // The gazetteer alone is not enough — the stoplist and the manifest are both
  // read at load, and a missing manifest fails the digest check rather than the
  // asset check.
  assert.deepEqual(packContents.missingAssets(["assets/notability.txt.gz"]), [
    "assets/MANIFEST.json",
    "assets/stop_words.txt",
  ]);
});

test("the package declares the asset and the build output as shipped files", () => {
  // The `files` allow-list is what actually decides the tarball's contents, and
  // dropping an entry from it is a one-character edit with no local symptom.
  const manifest = JSON.parse(
    readFileSync(resolve(PACKAGE_ROOT, "package.json"), "utf8"),
  ) as { files: string[] };
  assert.ok(manifest.files.includes("assets"), "package.json files must ship assets/");
  assert.ok(manifest.files.includes("dist"), "package.json files must ship dist/");
});

test("the vendored asset is on disk, where the tracking check cannot look for it", () => {
  // The gazetteer and the stoplist are deliberately untracked in this package —
  // they are vendored from the repository's `asset/` by `npm run sync-assets`, so
  // that no one of three front doors owns the shared input. The cost of that
  // symmetry is that a tarball built in a tree where the sync never ran packs
  // cleanly and loads nothing, which means redacting every public figure in every
  // essay.
  for (const need of packContents.REQUIRED_ASSET_FILES) {
    assert.ok(
      existsSync(resolve(PACKAGE_ROOT, need)),
      `${need} is missing — run \`npm run sync-assets\``,
    );
  }
});

test("npm pack really would carry the asset", () => {
  // The end-to-end version of the three tests above: not what the allow-list says
  // and not what is on disk, but what npm resolves the two into. `--ignore-scripts`
  // keeps `prepack` from rebuilding, so this measures the file selection rather
  // than the compiler.
  const raw = execFileSync(
    "npm",
    ["pack", "--dry-run", "--json", "--ignore-scripts"],
    { cwd: PACKAGE_ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
  );
  const files: string[] = packContents.packedFiles(JSON.parse(raw));
  assert.deepEqual(
    packContents.missingAssets(files),
    [],
    `tarball of ${files.length} files is missing an asset file`,
  );
});

// ---------------------------------------------------------------------------
// Whether the registry really serves it
// ---------------------------------------------------------------------------

test("it reads the version numbers the registry returns", () => {
  const answer = registryServes.parse(
    JSON.stringify({ name: "@bwthomas/vicary", versions: { "0.1.0": {}, "0.2.1": {} } }),
  );
  assert.deepEqual(answer.versions, ["0.1.0", "0.2.1"]);
  assert.equal(answer.error, null);
  assert.equal(registryServes.serving(answer, "0.2.1"), true);
  assert.equal(registryServes.serving(answer, "0.3.0"), false);
  assert.equal(registryServes.unknown(answer), false);
});

test("a payload that is not JSON is unknown rather than absent", () => {
  // An HTML error page from a proxy is the realistic case, and it must not read as
  // "this version is not published".
  const answer = registryServes.parse("<html>503 Service Unavailable</html>");
  assert.equal(answer.versions, null);
  assert.equal(registryServes.unknown(answer), true);
  assert.equal(registryServes.serving(answer, "0.2.1"), false);
  assert.match(answer.error, /did not return JSON/);
});

test("a payload whose shape moved is unknown rather than absent", () => {
  for (const body of ['{"name":"vicary"}', "[]", '"a string"', "null"]) {
    const answer = registryServes.parse(body);
    assert.equal(answer.versions, null, body);
    assert.equal(registryServes.unknown(answer), true, body);
  }
});

test("a package the registry has never heard of is absent, not unknown", async () => {
  // The one case where an empty answer is the truth: exactly what a first release
  // looks like, right up until the moment it is not.
  const answer = await registryServes.fetchVersions("@bwthomas/vicary", {
    get: async () => new Response("not found", { status: 404 }),
  });
  assert.deepEqual(answer.versions, []);
  assert.equal(registryServes.unknown(answer), false);
  assert.equal(registryServes.serving(answer, "0.2.1"), false);
});

test("a registry it cannot reach is unknown, not absent", async () => {
  const refused = await registryServes.fetchVersions("@bwthomas/vicary", {
    get: async () => {
      throw new Error("ECONNREFUSED");
    },
  });
  assert.equal(registryServes.unknown(refused), true);
  assert.match(refused.error, /could not be reached/);

  const failed = await registryServes.fetchVersions("@bwthomas/vicary", {
    get: async () => new Response("", { status: 503, statusText: "Service Unavailable" }),
  });
  assert.equal(registryServes.unknown(failed), true);
  assert.match(failed.error, /503/);
});

test("a scoped name is encoded, or the registry reads it as a path", async () => {
  let seen = "";
  await registryServes.fetchVersions("@bwthomas/vicary", {
    get: async (url: string) => {
      seen = url;
      return new Response("{}", { status: 200 });
    },
  });
  assert.match(seen, /%2fvicary$/);
  assert.doesNotMatch(seen, /@bwthomas\/vicary/);
});

test("it returns success once the registry serves the version", async () => {
  // The version appears on the third attempt, which is the shape a real
  // propagation delay has. The sleeper is injected, so this costs microseconds.
  const out = recorder();
  let calls = 0;
  const code = await registryServes.waitFor("@bwthomas/vicary", "0.2.1", {
    attempts: 5,
    out,
    sleeper: async () => {},
    fetcher: async () => {
      calls += 1;
      return calls < 3 ? { versions: ["0.1.0"], error: null } : { versions: ["0.1.0", "0.2.1"], error: null };
    },
  });
  assert.equal(code, 0);
  assert.equal(calls, 3);
  assert.match(out.text(), /is serving @bwthomas\/vicary 0\.2\.1/);
});

test("it fails when the registry never serves the version", async () => {
  const out = recorder();
  const code = await registryServes.waitFor("@bwthomas/vicary", "0.2.1", {
    attempts: 3,
    out,
    sleeper: async () => {},
    fetcher: async () => ({ versions: ["0.1.0"], error: null }),
  });
  assert.equal(code, 1);
  assert.match(out.text(), /never served it across 3 attempts/);
  assert.match(out.text(), /The publish reported success; the registry disagrees/);
});

test("an unreadable registry fails rather than passing", async () => {
  // The failure mode the whole script exists for: every attempt could not tell,
  // and that must exit non-zero rather than being treated as "not published yet"
  // and then as "fine".
  const out = recorder();
  const code = await registryServes.waitFor("@bwthomas/vicary", "0.2.1", {
    attempts: 2,
    out,
    sleeper: async () => {},
    fetcher: async () => ({ versions: null, error: "connection reset" }),
  });
  assert.equal(code, 1);
  assert.match(out.text(), /could not read the registry — connection reset/);
});
