#!/usr/bin/env node
// The gate that decides whether this package may be published.
//
// A port that publishes while it reproduces 0 of 38 masking-required frames is a
// package named vicary that does not redact — worse than no package, because a
// host that installs it gets silence instead of an error. So the release workflow
// refuses to push unless every masking-required frame matches the reference output
// byte-for-byte.
//
// **Why this is a script and not four lines of shell in the workflow.** It was
// shell: `npm run conformance | grep 'frames requiring masking' | awk '{print $4}'`,
// comparing two strings scraped out of a human-readable report. That gate is the
// only thing standing between an unfinished port and npm, and it had three failure
// modes nobody could see — a relabelled report line, a changed column, and an
// `awk` that yields the empty string on both. The empty-string case was guarded;
// the other two silently compare whatever landed in column four. A gate whose own
// correctness is unobservable is not a gate.
//
// Here it reads the scoreboard object the harness already returns, so there is no
// text to misparse, and `typescript/test/packaging.test.ts` exercises the decision
// in both directions — refuse at 0 of 38, allow at 38 of 38 — without a network, a
// credential, or a tag.
//
// The counterpart of `ruby/scripts/release_gate.rb`, decision for decision.
//
// Usage:
//   node scripts/release-gate.mjs                # print the report; exit 1 unless complete
//   node scripts/release-gate.mjs --report-only   # print the report; always exit 0

/**
 * Why a complete board is required, said once so the workflow log and the test
 * read the same sentence.
 */
export const REFUSAL = [
  "REFUSING TO PUBLISH. Publishing now would ship a package named vicary that",
  "does not redact, and a caller cannot tell: `redact` would return text with",
  "names still in it. Raise the ratchet and land the detector first.",
].join("\n");

/**
 * @typedef {object} Decision
 * @property {number} matched
 * @property {number} total
 * @property {boolean} publishable
 * @property {string} reason
 */

/**
 * Decide from a scoreboard. Pure, so the test can hand it a board this port does
 * not produce yet and check the *allow* branch too — the branch that has never
 * once run here, and the expensive one to get wrong.
 *
 * @param {{matchedRequiringMasking: number, requiringMasking: number}} board
 * @returns {Decision}
 */
export function decide(board) {
  const matched = board.matchedRequiringMasking;
  const total = board.requiringMasking;

  if (total === null || total === undefined || total === 0) {
    return {
      matched,
      total,
      publishable: false,
      reason:
        "the spec reports 0 frames requiring masking, so this gate is scoring " +
        "nothing. Refusing to publish on a denominator of zero rather than " +
        "reading it as success.",
    };
  }

  if (matched === total) {
    return {
      matched,
      total,
      publishable: true,
      reason:
        `${matched} of ${total} masking-required frames match the reference ` +
        "output byte-for-byte.",
    };
  }

  return {
    matched,
    total,
    publishable: false,
    reason:
      `${matched} of ${total} masking-required frames match the reference ` +
      `output.\n\n${REFUSAL}`,
  };
}

/** Score this port against the shared spec. Imported lazily so `decide` needs no build. */
async function board() {
  const { loadSpec, score } = await import("../dist/conformance.js");
  const { redact } = await import("../dist/redact.js");
  const spec = loadSpec();
  return score(spec, (sentence, identity) => redact(sentence, identity));
}

async function main(argv, out = console) {
  const reportOnly = argv.includes("--report-only");
  const decision = decide(await board());

  out.log(`release gate: ${decision.publishable ? "PUBLISHABLE" : "BLOCKED"}`);
  out.log(decision.reason);

  return reportOnly || decision.publishable ? 0 : 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(await main(process.argv.slice(2)));
}
