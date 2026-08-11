/**
 * The structured pass and the numbering it mints through.
 *
 * Every probe here is checked against the Python implementation's output rather
 * than against a hand-written expectation where one was available — the port's
 * claim is byte-identity, and a test that agrees with itself proves nothing about
 * that. What is written out longhand below are the *properties* numbering has to
 * hold, which no single frame demonstrates.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { PlaceholderMinter, restore } from "../src/minter.js";
import { redact, redactWithReport } from "../src/redact.js";
import {
  escapeLiteral,
  identityPatterns,
  luhnOk,
  schoolAcronym,
  wordPattern,
} from "../src/structured.js";

const IDENTITY = {
  firstName: "Marguerite",
  lastName: "Delacroix-Whitfield",
  schoolName: "Westfield High School",
};

// ---------------------------------------------------------------------------
// Numbering — the property the golden layer exists to pin
// ---------------------------------------------------------------------------

test("the same original always mints the same placeholder", () => {
  // A name written five times masks to one placeholder, not five. Beyond
  // restorability: a scoring model reading "{NAME_1} argued … {NAME_1}
  // concluded" sees one person doing two things.
  const minter = new PlaceholderMinter();
  assert.equal(minter.mint("NAME", "Terrence"), "{NAME_1}");
  assert.equal(minter.mint("NAME", "Terrence"), "{NAME_1}");
  assert.equal(minter.mint("NAME", "Marisol"), "{NAME_2}");
  assert.equal(minter.mint("NAME", "Terrence"), "{NAME_1}");
});

test("distinct originals never share a placeholder", () => {
  // Injectivity is what makes restore well-defined. Without it one token means
  // "Marisol" in one paragraph and "Terrence" in the next, and no map keyed on
  // the token can put either back.
  const minter = new PlaceholderMinter();
  const seen = new Set<string>();
  for (const name of ["A", "B", "C", "D"]) {
    const token = minter.mint("NAME", name);
    assert.equal(seen.has(token), false, token);
    seen.add(token);
  }
});

test("each kind counts independently", () => {
  const minter = new PlaceholderMinter();
  assert.equal(minter.mint("NAME", "x"), "{NAME_1}");
  assert.equal(minter.mint("PHONE", "y"), "{PHONE_1}");
  assert.equal(minter.mint("NAME", "z"), "{NAME_2}");
});

test("indices follow mint order, not position in the text", () => {
  // The property a port is most likely to get wrong, because left-to-right
  // numbering passes every leak check, every keep check and every semantic
  // expectation while emitting a restoration mapping that is wrong. Here the
  // later-positioned span is minted first and therefore numbered first.
  const minter = new PlaceholderMinter();
  assert.equal(minter.mint("NAME", "second-in-text"), "{NAME_1}");
  assert.equal(minter.mint("NAME", "first-in-text"), "{NAME_2}");
});

test("numbering off reproduces the unnumbered output", () => {
  const minter = new PlaceholderMinter({ number: false });
  assert.equal(minter.mint("NAME", "Terrence"), "{NAME}");
  assert.equal(minter.mint("NAME", "Marisol"), "{NAME}");
  assert.equal(minter.assigned.size, 0);
});

test("the restore map puts the exact bytes back", () => {
  const result = redactWithReport(
    "Call Marguerite at 555-123-4567 or a@b.com.",
    IDENTITY,
  );
  assert.notEqual(result.text, "Call Marguerite at 555-123-4567 or a@b.com.");
  assert.equal(
    restore(result.text, result.restoreMap),
    "Call Marguerite at 555-123-4567 or a@b.com.",
  );
});

test("restore is not confused by a placeholder that prefixes another", () => {
  // {NAME_1} must not be partially consumed while {NAME_11} is pending, which is
  // why restore works longest-first.
  const map = new Map([
    ["{NAME_1}", "Ann"],
    ["{NAME_11}", "Bea"],
  ]);
  assert.equal(restore("{NAME_11} and {NAME_1}", map), "Bea and Ann");
});

// ---------------------------------------------------------------------------
// Structured entities
// ---------------------------------------------------------------------------

test("a card is masked only when it passes Luhn", () => {
  // An un-checked pattern this loose eats any long number a student writes.
  assert.equal(luhnOk("4111111111111111"), true);
  assert.equal(luhnOk("4111111111111112"), false);
  assert.match(redact("She read out 4111 1111 1111 1111.", IDENTITY), /\{CREDIT_DEBIT_CARD_NUMBER_1\}/);
  assert.equal(
    redact("She read out 4111 1111 1111 1112.", IDENTITY),
    "She read out 4111 1111 1111 1112.",
  );
});

test("never-issued SSN ranges are not masked", () => {
  // So dates and score ranges don't trip it.
  for (const bad of ["000-12-3456", "666-12-3456", "900-12-3456", "123-00-6789", "123-45-0000"]) {
    assert.equal(redact(`The form said ${bad}.`, IDENTITY), `The form said ${bad}.`, bad);
  }
  assert.match(redact("The form said 123-45-6789.", IDENTITY), /\{US_SOCIAL_SECURITY_NUMBER_1\}/);
});

test("an address needs a street suffix", () => {
  // A bare number-plus-words pattern has an unacceptable false-positive rate in
  // prose, which is what the suffix list is for.
  assert.equal(
    redact("I ran 3 miles down the road and nothing happened.", IDENTITY),
    "I ran 3 miles down the road and nothing happened.",
  );
  assert.match(redact("We moved to 1428 Elm Street that fall.", IDENTITY), /\{ADDRESS_1\}/);
});

test("upstream anonymization markers survive", () => {
  // Text arriving with these has already been redacted, so masking them again
  // destroys information while adding none — and rewriting @PERSON1 to
  // {USERNAME} moves every essay in the evaluation corpus off the distribution
  // the scoring model was trained on.
  const text = "Upstream markers @PERSON1 and @CAPS2 must survive.";
  assert.equal(redact(text, IDENTITY), text);
  assert.match(redact("My handle is @terrence_o now.", IDENTITY), /\{USERNAME_1\}/);
});

test("a bare ten-digit number is not assumed to be a phone", () => {
  const text = "Her number is 5551234567 with no separators.";
  assert.equal(redact(text, IDENTITY), text);
});

test("the age pattern masks the digits and leaves the prose", () => {
  // "I am … years old" is the student's own writing and has to survive; only the
  // number is PII.
  assert.equal(
    redact("I am 14 years old and this is the first thing I finished.", IDENTITY),
    "I am {AGE_1} years old and this is the first thing I finished.",
  );
});

// ---------------------------------------------------------------------------
// Identity interpolation
// ---------------------------------------------------------------------------

test("the full name is masked as one span, not two", () => {
  // Ordered most-specific-first, so "Marguerite Delacroix-Whitfield" becomes one
  // {NAME} rather than two adjacent placeholders.
  assert.equal(
    redact("Marguerite Delacroix-Whitfield wrote this.", IDENTITY),
    "{NAME_1} wrote this.",
  );
});

test("the roster order is matched too", () => {
  assert.equal(
    redact("Delacroix-Whitfield, Marguerite is the roster order.", IDENTITY),
    "{NAME_1} is the roster order.",
  );
});

test("a possessive is masked with the name", () => {
  // "\\b" alone mis-handles a trailing apostrophe-s, which is exactly how a name
  // appears in student prose.
  assert.match(redact("Marguerite's essay was late.", IDENTITY), /^\{NAME_1\} essay/);
});

test("a curly possessive is NOT matched, reproducing Python", () => {
  // The Python source reads `(?:'s|'s|s')` — the second branch repeats the first
  // rather than being the curly form it resembles, so a word processor's
  // apostrophe misses. Pinned rather than fixed: changing it changes the golden
  // bytes, and that is a fixture decision, not a port decision.
  const out = redact("Marguerite’s essay was late.", IDENTITY);
  assert.equal(out, "{NAME_1}’s essay was late.");
});

test("the school acronym is matched case-sensitively", () => {
  // Lowercasing it would match ordinary words; three-letter acronyms shaped like
  // "was"/"his" are a real hazard.
  assert.equal(schoolAcronym("Westfield High School"), "WHS");
  assert.equal(schoolAcronym("Lincoln High School"), "LHS");
  assert.equal(schoolAcronym("Bay School"), null); // two letters is too short
  assert.match(redact("I go to WHS on the east side.", IDENTITY), /\{SCHOOL_1\}/);
  const lower = "I said whs and meant nothing by it.";
  assert.equal(redact(lower, IDENTITY), lower);
});

test("an ambiguous given name is left alone standing on its own", () => {
  // "Will you go", "a Grace period" — a bare first-name match on one of these
  // destroys prose. The full name and the surname still mask.
  const patterns = identityPatterns({ firstName: "Grace", lastName: "Okonkwo" });
  const kinds = patterns.map(([kind]) => kind);
  assert.equal(kinds.length, 3); // full, roster, surname — no bare "Grace"
  const text = "We had a Grace period before the deadline.";
  assert.equal(redact(text, { ...IDENTITY, firstName: "Grace", lastName: "Okonkwo" }), text);
});

test("an ambiguous surname is left alone too", () => {
  const patterns = identityPatterns({ firstName: "Terrence", lastName: "Young" });
  const kinds = patterns.map(([kind]) => kind);
  assert.equal(kinds.length, 3); // full, roster, given — no bare "Young"
});

test("an empty identity contributes no patterns", () => {
  // A caller that knows nothing still gets the structured entities.
  assert.deepEqual(identityPatterns({}), []);
  assert.equal(
    redact("Call me at 555-123-4567.", { firstName: "", lastName: "", schoolName: "" }),
    "Call me at {PHONE_1}.",
  );
});

test("a literal with regex metacharacters is escaped", () => {
  // A surname is user data. "O'Brien (Jr.)" must not compile as a group.
  assert.equal(escapeLiteral("O'Brien (Jr.)"), "O'Brien \\(Jr\\.\\)");
  assert.doesNotThrow(() => wordPattern("O'Brien (Jr.)"));
  assert.equal(
    redact("O'Brien was here.", {
      firstName: "",
      lastName: "O'Brien",
      schoolName: "",
    }),
    "{NAME_1} was here.",
  );
});

test("a literal ending in punctuation cannot match, in both languages", () => {
  // Not a port defect — verified identical in Python. `_word_pattern` closes
  // with `\b`, and a literal ending in `)` or `.` puts a non-word character
  // there, so the boundary can never hold. A caller passing a suffixed surname
  // ("O'Brien (Jr.)") silently gets no masking for it.
  //
  // Pinned rather than fixed for the same reason as the curly possessive above:
  // the fold is shared with the reference arm, so changing it changes golden
  // bytes. Worth knowing before a host feeds this roster data.
  assert.equal(
    redact("O'Brien (Jr.) was here.", {
      firstName: "",
      lastName: "O'Brien (Jr.)",
      schoolName: "",
    }),
    "O'Brien (Jr.) was here.",
  );
});

test("a hyphenated surname survives escaping", () => {
  // Python's re.escape writes `\-`, which is a SyntaxError under the u flag.
  // This port escapes narrower on purpose; the behaviour must be identical.
  assert.equal(
    redact("Delacroix-Whitfield lent me her notes.", IDENTITY),
    "{NAME_1} lent me her notes.",
  );
});

// ---------------------------------------------------------------------------
// Ordering — reordering the tables changes the bytes even when it changes no
// verdict, so the order is pinned here
// ---------------------------------------------------------------------------

test("identity runs before the structured patterns", () => {
  // An address line can otherwise swallow a surname, and a name half-eaten by
  // another pattern leaks the remainder.
  const result = redactWithReport(
    "Marguerite lives at 1428 Elm Street.",
    IDENTITY,
  );
  const tokens = [...result.restoreMap.keys()];
  assert.deepEqual(tokens, ["{NAME_1}", "{ADDRESS_1}"]);
});

test("a phone is claimed before the bare-digit patterns", () => {
  // ZIP and AGE run last precisely so they cannot claim characters belonging to
  // a phone, SSN, card or street address.
  assert.equal(
    redact("Reach me at 330-555-0142 today.", IDENTITY),
    "Reach me at {PHONE_1} today.",
  );
});

test("email is claimed before phone", () => {
  // An email can contain digits that look like a phone.
  assert.equal(
    redact("Write to a555.123.4567b@example.com now.", IDENTITY),
    "Write to {EMAIL_1} now.",
  );
});
