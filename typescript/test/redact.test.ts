/**
 * The detection-level dial, and what each level actually wires in.
 *
 * The conformance suite scores exactly one arm — `local-gazetteer-lowercase` —
 * so nothing there distinguishes the other two levels from it, or from each
 * other. A level that silently resolved to the wrong bundle would leave every
 * frame matching and every log line reporting spans while the deployment found a
 * different set of names than it was configured to.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_NAME_DETECTION,
  NAMES_GAZETTEER,
  NAMES_IDENTITY,
  NAMES_LOWERCASE,
  gazetteerOracles,
  nameDetection,
  redact,
  redactWithReport,
  restore,
} from "../src/redact.js";

const IDENTITY = {
  firstName: "Marguerite",
  lastName: "Delacroix-Whitfield",
  schoolName: "Westfield High School",
};

// ---------------------------------------------------------------------------
// Resolving the level
// ---------------------------------------------------------------------------

test("each level's own name resolves to it", () => {
  assert.equal(nameDetection(NAMES_IDENTITY), NAMES_IDENTITY);
  assert.equal(nameDetection(NAMES_GAZETTEER), NAMES_GAZETTEER);
  assert.equal(nameDetection(NAMES_LOWERCASE), NAMES_LOWERCASE);
});

test("the aliases a host is likely to write resolve", () => {
  // A host configuring this from an env file writes "true", not
  // "gazetteer-lowercase". Matching the reference's alias sets rather than
  // demanding the canonical spelling.
  for (const alias of ["off", "none", "0", "false", "no"]) {
    assert.equal(nameDetection(alias), NAMES_IDENTITY, alias);
  }
  for (const alias of ["on", "1", "true", "yes", "names"]) {
    assert.equal(nameDetection(alias), NAMES_GAZETTEER, alias);
  }
  for (const alias of ["lowercase", "full", "max", "gazetteer_lowercase"]) {
    assert.equal(nameDetection(alias), NAMES_LOWERCASE, alias);
  }
  assert.equal(nameDetection("  GaZeTTeer  "), NAMES_GAZETTEER);
});

test("an unrecognized value falls to the default, not to identity", () => {
  // The opposite of the redaction-mode dial's fail-safe, and deliberately so.
  // There, a typo makes the host behave as it did before redaction existed — a
  // recoverable non-event. Here, dropping silently to `identity` would leave
  // redaction ON and reporting spans while finding none of the names a reader
  // would call PII: a failure that looks exactly like success from every log
  // line and every metric.
  assert.equal(nameDetection("gazeteer"), DEFAULT_NAME_DETECTION); // one 't'
  assert.equal(nameDetection("yes please"), DEFAULT_NAME_DETECTION);
  assert.equal(DEFAULT_NAME_DETECTION, NAMES_LOWERCASE);
});

test("an empty value is not a request for identity-only", () => {
  // "" is unset, not "off" — it has to reach the default. The alias set contains
  // several falsy-looking strings, so this is one guard away from inverting.
  assert.equal(nameDetection(""), DEFAULT_NAME_DETECTION);
  assert.equal(nameDetection("   "), DEFAULT_NAME_DETECTION);
});

// ---------------------------------------------------------------------------
// What a level wires in
// ---------------------------------------------------------------------------

test("the identity level loads no gazetteer and generates nothing", () => {
  const oracles = gazetteerOracles(NAMES_IDENTITY);
  assert.equal(oracles.candidates, false);
  assert.deepEqual(Object.keys(oracles), ["candidates"]);
});

test("generation and the oracle are one decision, not two", () => {
  // Generation alone masks every public figure a student writes about; the
  // oracle alone has nothing to judge. Neither level may supply half.
  for (const level of [NAMES_GAZETTEER, NAMES_LOWERCASE]) {
    const oracles = gazetteerOracles(level);
    assert.equal(oracles.candidates, true, level);
    assert.ok(oracles.notable !== undefined, level);
    assert.ok(oracles.notabilityTier !== undefined, level);
    assert.ok(oracles.title !== undefined, level);
    assert.ok(oracles.titlePrefix !== undefined, level);
  }
});

test("the lowercase route is the only difference between the two levels", () => {
  const gazetteer = gazetteerOracles(NAMES_GAZETTEER);
  const lowercase = gazetteerOracles(NAMES_LOWERCASE);
  assert.equal(gazetteer.givenName, undefined);
  assert.ok(lowercase.givenName !== undefined);
  // The settlement oracle is wired at BOTH, unlike `givenName`: it decides a
  // placeholder's TYPE, not a verdict, so it has nothing to do with which
  // candidate routes are on.
  assert.equal(gazetteer.settlement, lowercase.settlement);
  assert.deepEqual(
    Object.keys(lowercase).filter((k) => !(k in gazetteer)),
    ["givenName"],
  );
});

// ---------------------------------------------------------------------------
// The levels, end to end
// ---------------------------------------------------------------------------

test("only the lowercase level reaches a student who writes without capitals", () => {
  // The arm's whole reason to exist. Capitalisation is the primary signal, so a
  // composition typed in lowercase is invisible to the level below.
  const text = "then terrence okonkwo showed up and everything changed for me.";
  assert.equal(redact(text, IDENTITY, { names: NAMES_IDENTITY }), text);
  assert.equal(redact(text, IDENTITY, { names: NAMES_GAZETTEER }), text);
  assert.equal(
    redact(text, IDENTITY, { names: NAMES_LOWERCASE }),
    "then {NAME_1} showed up and everything changed for me.",
  );
});

test("the identity level still masks every structured entity", () => {
  // Turning name detection off is not turning redaction off. A caller who picks
  // `identity` for its precision still gets the syntax, which is the half regex
  // scored 100% on.
  assert.equal(
    redact("Call me at 555-123-4567 or rosa@example.org.", IDENTITY, {
      names: NAMES_IDENTITY,
    }),
    "Call me at {PHONE_1} or {EMAIL_1}.",
  );
});

test("a keep from the assignment prompt survives the detector", () => {
  // The prompt_context leg: exact, free, zero false positives. Left EMPTY when
  // the golden was generated, so no frame exercises it — this is the only thing
  // that does.
  const text = "I wrote about Ngozi Adeyemi for class last spring.";
  assert.match(redact(text, IDENTITY), /\{NAME_1\}/);
  assert.equal(redact(text, IDENTITY, { keep: new Set(["Ngozi Adeyemi"]) }), text);
});

// ---------------------------------------------------------------------------
// Numbering, across both passes
// ---------------------------------------------------------------------------

test("one minter serves both passes, so indices never collide", () => {
  // The defect numbering exists to remove: two minters would restart each
  // counter and hand {NAME_1} to the student and to a classmate both.
  const report = redactWithReport(
    "Marguerite Delacroix-Whitfield and Terrence Okonkwo both stayed after class.",
    IDENTITY,
  );
  assert.equal(report.text, "{NAME_1} and {NAME_2} both stayed after class.");
  assert.equal(report.nMasked, 2);
  assert.deepEqual([...report.restoreMap.entries()], [
    ["{NAME_1}", "Marguerite Delacroix-Whitfield"],
    ["{NAME_2}", "Terrence Okonkwo"],
  ]);
});

test("the unnumbered arm reproduces the older output, and is not restorable", () => {
  // Kept measurable rather than deleted, and this is the measurement: two people
  // collapse to one token, so no map keyed on it can put either back. Unnumbered
  // output round-tripped 36% of injected essays.
  const text = "Marguerite Delacroix-Whitfield and Terrence Okonkwo stayed.";
  const report = redactWithReport(text, IDENTITY, { numberPlaceholders: false });
  assert.equal(report.text, "{NAME} and {NAME} stayed.");
  assert.notEqual(restore(report.text, report.restoreMap), text);
});

test("empty text is returned unchanged with an empty report", () => {
  const report = redactWithReport("", IDENTITY);
  assert.equal(report.text, "");
  assert.equal(report.nMasked, 0);
  assert.equal(report.restoreMap.size, 0);
});
