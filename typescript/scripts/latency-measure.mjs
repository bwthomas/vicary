// Measure this checkout's redaction latency once, and print it as JSON.
//
// One process, one number, no verdict. The verdict is `tools/latency_pair.py`'s
// job, because a latency number only means something next to another one taken
// on the same machine — see that file's header for what these measurements are
// for.
//
// The library it measures is `../dist` by default, or whatever `VICARY_DIST`
// points at. The second form is how the pair driver measures the previous
// release: same script, same corpus, same estimator, different library.
//
//     node scripts/latency-measure.mjs
//     VICARY_DIST=/tmp/prev/typescript/dist node scripts/latency-measure.mjs
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";

const dist = process.env.VICARY_DIST ?? new URL("../dist/", import.meta.url).pathname;
const from = async (name) =>
  import(pathToFileURL(`${dist.replace(/\/$/, "")}/${name}`).href);

const { loadSpec } = await from("conformance.js");
const { LATENCY_REPEATS, buildCases, loadCarrierPlan, loadEssays, resolveCorpusId } =
  await from("corpus.js");
const { redact } = await from("redact.js");

const corpusId = resolveCorpusId();
const essays = loadEssays(corpusId);
const cases = essays === null ? [] : buildCases(essays, loadCarrierPlan(corpusId), loadSpec());
if (cases.length === 0) {
  console.log(JSON.stringify({ error: "no corpus in this checkout" }));
  process.exit(1);
}
const identity = loadSpec().identity;

/**
 * One pass over every essay, timed or not.
 *
 * The untimed pass is the warmup, and it is not a formality. It is measured: on
 * a GitHub runner this port's first four essays run at about twice their
 * steady-state cost while V8 tiers the redaction path up, which put a quarter of
 * the pooled samples above the steady state and made the estimator's value
 * depend on when the JIT happened to finish. Python and Ruby barely move, which
 * is the other half of the reason it is here — the three ports have to estimate
 * the same way or the gate is three different gates.
 */
function sweep(timed) {
  const out = [];
  for (const testCase of cases) {
    const timings = [];
    for (let i = 0; i < LATENCY_REPEATS; i += 1) {
      const started = performance.now();
      redact(testCase.text, identity);
      timings.push(performance.now() - started);
    }
    if (timed) out.push(timings);
    // The clean-prose pass the gate's own loop does between essays. Untimed
    // there and untimed here, but it runs, so the process is in the same state
    // from one timed essay to the next.
    redact(testCase.base, identity);
  }
  return out;
}

// The asset load, before the clock: a one-time cost that whichever essay came
// first would otherwise pay in full.
redact(cases[0].base.slice(0, 200), identity);

sweep(false);
const pooled = sweep(true).flat().sort((a, b) => a - b);
const median =
  pooled.length % 2 === 1
    ? pooled[(pooled.length - 1) >> 1]
    : (pooled[pooled.length / 2 - 1] + pooled[pooled.length / 2]) / 2;

console.log(
  JSON.stringify({
    impl: "typescript",
    runtime: String(process.versions.node.split(".")[0]),
    corpus: corpusId,
    corpus_sha256: createHash("sha256")
      .update(cases.map((c) => c.text).join(""), "utf8")
      .digest("hex"),
    essays: cases.length,
    repeats: LATENCY_REPEATS,
    pooled_median_ms: Number(median.toFixed(6)),
  }),
);
