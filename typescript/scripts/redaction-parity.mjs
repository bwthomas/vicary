#!/usr/bin/env node
// Diff this port's masked OUTPUT against the Python reference, on prose no
// fixture contains.
//
// The counterpart of `ruby/scripts/redaction_parity.rb`, and it existed only in
// Ruby until now — which meant the claim "all three produce identical bytes" was
// checked between two of the three and asserted about the third.
//
// **Why it exists, measured rather than supposed.** The day the Ruby port landed,
// of eleven deliberate mutations to `candidates.rb` the 36 conformance frames
// caught one and the 2,526 primitive assertions caught seven. Three of the
// remaining were inert. The last was not: changing `\z` to `$` in
// `RELATION_ATTACHED_BEFORE` — the idiomatic Ruby spelling, and wrong — left every
// frame and every primitive green, because both of those corpora are single-line
// and the rule only diverges across a newline. JavaScript has the same trap
// spelled the same way, plus `\b` disagreeing with Python about accented letters.
//
// The probes come from `conformance/probes.json`, shared with the Ruby script
// rather than transcribed. Two ports probing different seams would reproduce
// exactly the drift these scripts exist to catch.
//
// It runs BOTH implementations and compares them. A probe that only printed this
// port's answers would need a hand-copied expected column, which is the failure
// mode the whole repository is arranged against: a constant somebody typed agrees
// with itself forever.
//
//     npm run parity:redaction                      # the shared probe list
//     npm run parity:redaction -- path/to/probes.json   # {"name": "text", …}
//
// Exits non-zero on any divergence, so it can gate a commit.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { redact, redactBatchWithReport } from "../dist/index.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");
const PYTHON = join(ROOT, "python", ".venv", "bin", "python");
const SPEC = join(ROOT, "conformance", "probes.json");

function loadSpec() {
  const raw = JSON.parse(readFileSync(SPEC, "utf8"));
  if (raw.document_version !== 1) {
    console.error(
      `probes.json is document_version ${raw.document_version}, and this ` +
        "reader knows 1. Refusing rather than probing a subset of it.",
    );
    process.exit(2);
  }
  return raw;
}

function probeTexts(spec, argv) {
  if (argv.length === 0) {
    return Object.fromEntries(spec.redaction_probes.map((p) => [p.id, p.text]));
  }
  return JSON.parse(readFileSync(argv[0], "utf8"));
}

// The batched pass, from the same shared spec. Each entry is a LIST of fields
// masked in one pass, and what is compared is the whole answer — masked bytes,
// the per-field restore maps, and whether the join round-tripped — because a
// port could reproduce the bytes while mis-assigning the maps, and a caller
// putting a word back reads the maps.
function batchProbes(spec, argv) {
  if (argv.length > 0) return {};
  return Object.fromEntries((spec.batch_probes ?? []).map((p) => [p.id, p.texts]));
}

function mineBatchOutput(batches, identity) {
  return Object.fromEntries(
    Object.entries(batches).map(([name, fields]) => {
      const r = redactBatchWithReport(fields, identity);
      return [
        name,
        {
          texts: r.texts,
          maps: r.restoreMaps.map((m) => Object.fromEntries(m)),
          batched: r.batched,
        },
      ];
    }),
  );
}

function referenceBatchOutput(batches, spec) {
  if (Object.keys(batches).length === 0) return {};
  // A FRESH redactor per batch. The reference carries notable keeps forward
  // from an inbound pass, and a reused one would be answering a question a
  // direction-agnostic port cannot be asked.
  const script = `
import json, sys
from types import SimpleNamespace
from vicary.eval.recall import build_redactor
identity = SimpleNamespace(**${JSON.stringify(spec.identity)})
batches = json.load(sys.stdin)
out = {}
for name, fields in batches.items():
    batch = build_redactor(${JSON.stringify(spec.arm)}, identity).redact_outbound_batch(fields)
    out[name] = {
        "texts": batch.texts,
        "maps": [dict(m) for m in batch.restore_maps],
        "batched": batch.batched,
    }
print(json.dumps(out, ensure_ascii=False))
`;
  try {
    return JSON.parse(
      execFileSync(PYTHON, ["-c", script], {
        input: JSON.stringify(batches),
        cwd: join(ROOT, "python"),
        encoding: "utf8",
        maxBuffer: 32 * 1024 * 1024,
      }),
    );
  } catch (error) {
    console.error("the reference implementation failed on the batch probes:");
    console.error(error.stderr ?? error.message);
    process.exit(2);
  }
}

function referenceOutput(texts, arm) {
  if (!existsSync(PYTHON)) {
    console.error(`no reference interpreter at ${PYTHON}.`);
    console.error(
      "This probe compares two implementations; with only one of them it " +
        "would be a script that agrees with itself. Run `just py-setup` from " +
        "the repository root first.",
    );
    process.exit(2);
  }
  // The arm the conformance golden was produced by, read off the shared spec. A
  // probe against any other arm would diff two different detectors and report
  // the difference as a bug.
  const script = `
import json, sys
from vicary.eval.recall import build_redactor
redactor = build_redactor(${JSON.stringify(arm)}, None)
texts = json.load(sys.stdin)
print(json.dumps({
    name: redactor._apply(text, source="INPUT").text
    for name, text in texts.items()
}, ensure_ascii=False))
`;
  try {
    const out = execFileSync(PYTHON, ["-c", script], {
      input: JSON.stringify(texts),
      cwd: join(ROOT, "python"),
      encoding: "utf8",
      maxBuffer: 32 * 1024 * 1024,
    });
    return JSON.parse(out);
  } catch (error) {
    console.error("the reference implementation failed:");
    console.error(error.stderr ?? error.message);
    process.exit(2);
  }
}

const spec = loadSpec();
const texts = probeTexts(spec, process.argv.slice(2));
const identity = {
  firstName: spec.identity.first_name,
  lastName: spec.identity.last_name,
  schoolName: spec.identity.school_name,
};

const mine = Object.fromEntries(
  Object.entries(texts).map(([name, text]) => [name, redact(text, identity)]),
);
const reference = referenceOutput(texts, spec.arm);

const divergent = Object.keys(texts).filter((n) => mine[n] !== reference[n]);

const batches = batchProbes(spec, process.argv.slice(2));
const batchMine = mineBatchOutput(batches, identity);
const batchReference = referenceBatchOutput(batches, spec);
const batchDivergent = Object.keys(batches).filter(
  (n) => JSON.stringify(batchMine[n]) !== JSON.stringify(batchReference[n]),
);

if (divergent.length === 0 && batchDivergent.length === 0) {
  const n = Object.keys(texts).length;
  console.log(`${n} probes, no divergence from the Python reference.`);
  console.log(
    "(masked bytes identical, placeholder numbering included, on prose the",
  );
  console.log(" conformance frames and the primitives corpus do not contain)");
  const b = Object.keys(batches).length;
  if (b > 0) {
    console.log(`${b} batch probes, no divergence either.`);
    console.log("(masked bytes, per-field restore maps and the round-trip flag all");
    console.log(" identical, which is what a host putting a word back depends on)");
  }
  process.exit(0);
}

if (batchDivergent.length > 0) {
  console.error(
    `${batchDivergent.length} of ${Object.keys(batches).length} BATCH probes diverge:`,
  );
  console.error("");
  for (const name of batchDivergent) {
    console.error(`  ${name}`);
    console.error(`    input:     ${JSON.stringify(batches[name])}`);
    console.error(`    reference: ${JSON.stringify(batchReference[name])}`);
    console.error(`    this port: ${JSON.stringify(batchMine[name])}`);
    console.error("");
  }
}

if (divergent.length === 0) {
  console.error("The single-text probes are all green, which is the point:");
  console.error("masked bytes can agree while the per-field maps a restore");
  console.error("reads do not.");
  process.exit(1);
}

console.error(
  `${divergent.length} of ${Object.keys(texts).length} probes diverge from ` +
    "the reference:",
);
console.error("");
for (const name of divergent) {
  console.error(`  ${name}`);
  console.error(`    input:     ${JSON.stringify(texts[name])}`);
  console.error(`    reference: ${JSON.stringify(reference[name])}`);
  console.error(`    this port: ${JSON.stringify(mine[name])}`);
  console.error("");
}
console.error("The frames and the primitives spec may both still be green —");
console.error("they are single-line corpora, and several rules only diverge");
console.error("across a newline.");
process.exit(1);
