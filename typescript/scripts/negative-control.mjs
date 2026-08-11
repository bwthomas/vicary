/**
 * Prove the primitives suite can fail.
 *
 * A negative control that mutates nothing produces the same output as one the
 * harness correctly survived: PASS. So this asserts the mutation landed — exactly
 * one occurrence replaced, file bytes changed — BEFORE it reads the suite's
 * verdict, and reports a control whose edit was a no-op as a hard error rather
 * than as a green run.
 *
 * Not wired into `npm test`: it rewrites `src/candidates.ts` in place and restores
 * it, which is not something a test run should do to a working tree. Run it by
 * hand after changing a primitive, and read the table.
 */
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

const SOURCE = new URL("../src/candidates.ts", import.meta.url);

/** Each control names the rule it removes, and the case that must notice. */
const CONTROLS = [
  {
    rule: "the corroborating tier is `full_name`",
    from: 'export const CORROBORATING_TIER = "full_name";',
    to: 'export const CORROBORATING_TIER = "person";',
  },
  {
    rule: "the possessive folds to the citation form",
    from: `if (token.endsWith("'s") && token.length > 3) token = token.slice(0, -2);`,
    to: `if (false) token = token.slice(0, -2);`,
  },
  {
    rule: "a bare surname key spans up to three particle-led tokens",
    from: "if (tokens.length <= 3 && tokens.slice(0, -1).every",
    to: "if (tokens.length <= 2 && tokens.slice(0, -1).every",
  },
  {
    rule: "a three-particle run yields all three forms",
    from: "if (tokens.length >= 3 && PARTICLE_SET.has(tokens[tokens.length - 3]!)) {",
    to: "if (false) {",
  },
  {
    rule: "a first name is never a surname form",
    from: "const forms = [tokens[tokens.length - 1]!];",
    to: "const forms = [tokens[tokens.length - 1]!, tokens[0]!];",
  },
  {
    rule: "a seed directly after a determiner is dropped",
    from: "if (index > 0 && DETERMINERS.has(tokens[index - 1]![0])) {",
    to: "if (false) {",
  },
  {
    rule: "a lowercase span may not end on a particle",
    from: "while (reach > index && PARTICLE_SET.has(tokens[reach]![0])) reach -= 1;",
    to: "// control: particle trim removed",
  },
  {
    rule: "a lowercase span needs two tokens",
    from: "if (reach - index + 1 < LOWERCASE_MIN_TOKENS ||",
    to: "if (reach - index + 1 < 1 ||",
  },
  {
    rule: "a trailing apostrophe is the closing quote, not part of the name",
    from: `while (end > 0 && (joined[end - 1] === "'" || joined[end - 1] === "’")) {`,
    to: "while (false) {",
  },
  {
    rule: "the capitalised route claims a span before the lowercase route",
    from: "claimed.some(([start, end]) => candidate.start < end! && candidate.end > start!)",
    to: "false",
  },
  {
    rule: "an unevidenced sentence-initial capital is suppressed",
    from: "        givenName !== undefined &&\n        suppressedAsAnUnevidencedCapital(",
    to: "        false &&\n        suppressedAsAnUnevidencedCapital(",
  },
  {
    rule: "a heading's capitals are orthographic",
    from: "const headings = headingsAreOrthographic ? headingSpans(text) : [];",
    to: "const headings: Span[] = [];",
  },
  {
    rule: "a title span with an attached first-person relation loses protection",
    from: "!namesSomeoneTheWriterKnows(text, s, e) &&",
    to: "true &&",
  },
  {
    rule: "a title that IS the writer's own relation loses protection",
    from: "titleIsTheWritersOwnRelation(text, s, e)\n          ),",
    to: "false\n          ),",
  },
  {
    rule: "a short document's own mixed case answers for the missing habit",
    from: "relationLedTitleIsInternallyMixed(text, s, e)) &&",
    to: "false) &&",
  },
  // --- the masking gates ---
  {
    rule: "a keep matches the possessive as well as the citation form",
    from: `loweredKeep.has(surnameTokens(name).join(" "))`,
    to: "false",
  },
  {
    rule: "the precedence table, not a suffix, decides keep-or-mask",
    from: "if (!resolve(classifyTags(name.split(/\\s+/).filter(Boolean), settlement)).mask) {",
    to: "if (false) {",
  },
  {
    rule: "a notable name is kept",
    from: "if (notable !== undefined && notable(name)) {",
    to: "if (false) {",
  },
  {
    rule: "only an overridable tier's keep may be refused",
    from: "OVERRIDABLE_TIERS.has(notabilityTier(name)) &&",
    to: "true &&",
  },
  {
    rule: "an attached first-person relation outranks a tier keep",
    from: "namesSomeoneTheWriterKnows(text, candidate.start, candidate.end)",
    to: "true",
  },
  {
    rule: "a document-established bare surname is kept",
    from: "if (established.size > 0 && bare !== null && established.has(bare)) {",
    to: "if (false) {",
  },
  {
    rule: "the sentence may refuse a document-level corroboration",
    from: "namesSomeoneInTheWritersLife(text, candidate.start, candidate.end)",
    to: "true",
  },
  {
    rule: "masking runs right to left so earlier offsets stay valid",
    from: "const ordered = [...candidates].sort((a, b) => b.start - a.start);",
    to: "const ordered = [...candidates].sort((a, b) => a.start - b.start);",
  },
  {
    rule: "without a minter the unnumbered placeholder is emitted",
    from: "minter === undefined\n        ? placeholderFor(candidate.kind)",
    to: "false\n        ? placeholderFor(candidate.kind)",
  },
];

const original = readFileSync(SOURCE, "utf8");
const rows = [];
let bad = 0;

for (const control of CONTROLS) {
  const occurrences = original.split(control.from).length - 1;
  // The whole point: a `from` that matches nothing, or matches twice, is a
  // control that proves nothing about the suite. Report it as a failure of the
  // CONTROL rather than letting the suite's verdict stand in for it.
  if (occurrences !== 1) {
    rows.push([control.rule, `NO-OP (${occurrences} matches)`]);
    bad += 1;
    continue;
  }
  const mutated = original.replace(control.from, control.to);
  if (mutated === original) {
    rows.push([control.rule, "NO-OP (replacement is identical)"]);
    bad += 1;
    continue;
  }
  writeFileSync(SOURCE, mutated);
  let noticed;
  try {
    execSync("npm run --silent build:test && node --test dist-test/test/primitives.test.js", {
      cwd: new URL("..", import.meta.url),
      stdio: "pipe",
    });
    noticed = false;
  } catch {
    // A compile error counts: the port stopped building, which is also the suite
    // refusing the mutation. What must never happen is a clean green run.
    noticed = true;
  } finally {
    writeFileSync(SOURCE, original);
  }
  rows.push([control.rule, noticed ? "caught" : "SURVIVED"]);
  if (!noticed) bad += 1;
}

const width = Math.max(...rows.map(([rule]) => rule.length));
for (const [rule, verdict] of rows) {
  console.log(`${verdict === "caught" ? "  ok  " : "  XX  "} ${rule.padEnd(width)}  ${verdict}`);
}
console.log(`\n${rows.length - bad}/${rows.length} controls caught`);
execSync("npm run --silent build:test", { cwd: new URL("..", import.meta.url), stdio: "ignore" });
process.exit(bad === 0 ? 0 : 1);
