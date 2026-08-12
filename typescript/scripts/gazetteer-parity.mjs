#!/usr/bin/env node
// Diff this port's raw gazetteer verdicts against the Python reference.
//
// The counterpart of `ruby/scripts/parity_probe.rb`, and the layer underneath
// `redaction-parity.mjs`. That one runs the whole detector and compares bytes;
// this compares the fold and the verdict, name by name.
//
// The conformance suite scores masked *output*, which is the claim that matters
// and also the coarsest one: two implementations can disagree about which tier
// matched, or about how a name folds, and still produce identical text on every
// frame in the set. A divergence here shows up as a diff rather than waiting for
// a frame that happens to collide.
//
// Names come from `conformance/probes.json`, shared with the Ruby probe rather
// than transcribed — the same reason the redaction probes are shared.
//
//     npm run parity:gazetteer                    # the shared name list
//     npm run parity:gazetteer -- path/to/names.txt   # one name per line
//
// Exits non-zero on any divergence, so it can gate a commit.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  isCommonGivenName,
  isSettlement,
  normalize,
  notability,
} from "../dist/index.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");
const PYTHON = join(ROOT, "python", ".venv", "bin", "python");
const SPEC = join(ROOT, "conformance", "probes.json");

function probeNames(argv) {
  if (argv.length > 0) {
    return readFileSync(argv[0], "utf8")
      .split("\n")
      .map((line) => line.replace(/\r$/, ""))
      .filter((line) => line.length > 0);
  }
  const raw = JSON.parse(readFileSync(SPEC, "utf8"));
  if (raw.document_version !== 1) {
    console.error(
      `probes.json is document_version ${raw.document_version}, and this ` +
        "reader knows 1. Refusing rather than probing a subset of it.",
    );
    process.exit(2);
  }
  return raw.gazetteer_names.map((entry) => entry.name);
}

// Tab-separated so a divergence is a one-line diff rather than a nested object
// comparison. `notability` is stringified by the same rule in both ports.
function mineRows(names) {
  return names.map((name) =>
    [
      name,
      normalize(name),
      notability(name),
      String(isSettlement(name)),
      String(isCommonGivenName(name)),
    ].join("\t"),
  );
}

function referenceRows(names) {
  if (!existsSync(PYTHON)) {
    console.error(`no reference interpreter at ${PYTHON}.`);
    console.error(
      "This probe compares two implementations; with only one of them it " +
        "would be a script that agrees with itself. Run `just py-setup` from " +
        "the repository root first.",
    );
    process.exit(2);
  }
  const script = `
import sys
from vicary import gazetteer as g
for line in sys.stdin.read().split("\\n"):
    if not line:
        continue
    print("\\t".join([
        line,
        g.normalize(line),
        g.notability(line),
        str(g.is_settlement(line)).lower(),
        str(g.is_common_given_name(line)).lower(),
    ]))
`;
  try {
    const out = execFileSync(PYTHON, ["-c", script], {
      input: names.join("\n"),
      cwd: join(ROOT, "python"),
      encoding: "utf8",
      maxBuffer: 32 * 1024 * 1024,
    });
    return out.split("\n").filter((line) => line.length > 0);
  } catch (error) {
    console.error("the reference implementation failed:");
    console.error(error.stderr ?? error.message);
    process.exit(2);
  }
}

const names = probeNames(process.argv.slice(2));
const mine = mineRows(names);
const reference = referenceRows(names);

const divergent = names
  .map((name, i) => ({ name, mine: mine[i], reference: reference[i] }))
  .filter((row) => row.mine !== row.reference);

if (divergent.length === 0) {
  console.log(`${names.length} names, no divergence from the Python reference.`);
  console.log("(fold, notability tier, settlement and given-name verdicts)");
  process.exit(0);
}

console.error(
  `${divergent.length} of ${names.length} names diverge from the reference:`,
);
console.error("");
console.error("  name\tnormalize\tnotability\tsettlement\tgiven");
for (const row of divergent) {
  console.error(`  reference: ${row.reference}`);
  console.error(`  this port: ${row.mine}`);
  console.error("");
}
process.exit(1);
