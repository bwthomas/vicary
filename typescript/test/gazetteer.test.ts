/**
 * The notability lookup, and the guards that keep its asymmetries pointing the
 * right way.
 *
 * These mirror `python/tests/test_gazetteer.py` case for case, against the same
 * shipped asset. That is the point: a port whose lookup agrees with Python on
 * every probe here has a candidate generator's worth of behaviour already
 * pinned, before a single conformance frame moves.
 *
 * The probes are literals rather than fixture reads on purpose — each names a
 * defect that actually shipped, and a test that derives its expectations from
 * the same asset it is checking agrees with itself forever.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEMONYM,
  FULL_NAME,
  GazetteerIndex,
  ICONIC_SHORT,
  NOT_NOTABLE,
  PARTICLES,
  PLACE,
  TIER_NAMES,
  TITLE,
  load,
  normalize,
} from "../src/gazetteer.js";

// ---------------------------------------------------------------------------
// The fold — the asset is keyed by one normalize and probed by another
// ---------------------------------------------------------------------------

test("normalize strips the possessive clitic", () => {
  // "Terrence's older brother" hands the lookup "Terrence's". A fold that keeps
  // the clitic misses the gazetteer and, for a notable name, over-masks.
  assert.equal(normalize("Lincoln's"), "lincoln");
  assert.equal(normalize("Terrence's"), "terrence");
  assert.equal(normalize("Lincoln’s"), "lincoln");
});

test("normalize keeps hyphens and apostrophes inside names", () => {
  assert.equal(normalize("Raghunathan-Bell"), "raghunathan-bell");
  assert.equal(normalize("O'Keeffe"), "o'keeffe");
});

test("curly apostrophes fold like straight ones", () => {
  // Word processors emit U+2019, and NFKD does not touch it. Without the
  // explicit mapping "Lincoln’s" folds to "lincoln s" and misses every tier — a
  // notable name silently over-masked on the commonest punctuation in student
  // prose.
  assert.equal(normalize("Lincoln’s"), normalize("Lincoln's"));
  assert.equal(normalize("Lincoln’s"), "lincoln");
  assert.equal(normalize("O’Keeffe"), "o'keeffe");
});

test("normalize strips accents without splitting the token", () => {
  // NFKD decomposes, and the combining mark must be dropped rather than turned
  // into a separator: "Ángel" folding to "a ngel" misses every tier.
  assert.equal(normalize("José"), "jose");
  assert.equal(normalize("Ángel"), "angel");
  assert.equal(normalize("Beyoncé"), "beyonce");
});

test("a short key is not mistaken for a possessive", () => {
  // The guard is `len(key) > len(clitic) + 1`, so "as" survives whole. Folding
  // it to "a" would be a different word.
  assert.equal(normalize("as"), "as");
  assert.equal(normalize("Cs"), "cs");
});

// ---------------------------------------------------------------------------
// Precision — the names that must survive redaction
// ---------------------------------------------------------------------------

test("Blake's pair splits", () => {
  // The whole reason this module exists, in two lines. Identical syntax,
  // opposite verdicts. If these ever agree, the notability filter has stopped
  // doing the only job it has.
  const gz = load();
  assert.equal(gz.isNotable("Vincent van Gogh"), true);
  assert.equal(gz.isNotable("Terrence Okonkwo"), false);
});

test("Washington resolves notable despite being a place and a surname", () => {
  // Notable person, US state, and one of the most common American surnames, all
  // one string. It must be KEEP.
  assert.equal(load().isNotable("Washington"), true);
});

test("a landmark keeps while a hometown in the same sentence redacts", () => {
  // "We drove from Akron all the way to see the Lincoln Memorial." Both are
  // LOCATION spans; only one is PII.
  const gz = load();
  assert.equal(gz.isNotable("Lincoln Memorial"), true);
  assert.equal(gz.isNotable("Akron"), false);
});

// ---------------------------------------------------------------------------
// Recall — the names that must NOT survive
// ---------------------------------------------------------------------------

test("the lookup does not decompose a candidate into tokens", () => {
  // The test that fails the obvious "improvement". "Lincoln" is in the short
  // tier; a lookup that tried each token would resolve "Priya Lincoln" and
  // "Coach Lincoln" notable and leak a real student's name off a coincidence.
  const gz = load();
  assert.equal(gz.isNotable("Lincoln"), true);
  assert.equal(gz.isNotable("Priya Lincoln"), false);
  assert.equal(gz.isNotable("Coach Lincoln"), false);
  assert.equal(gz.isNotable("Lincoln Okonkwo"), false);
});

test("honorifics are not stripped before lookup", () => {
  // Deliberate, and it costs precision on "President Lincoln". Stripping the
  // title would demote a titled name to a bare surname, the highest-collision
  // surface form there is.
  const gz = load();
  assert.equal(gz.isNotable("Mrs. Okonkwo"), false);
  assert.equal(gz.isNotable("Coach Bramwell"), false);
  assert.equal(gz.isNotable("President Lincoln"), false);
});

test("bare first names never resolve notable", () => {
  // The single most common private-name surface form in student prose.
  const gz = load();
  for (const name of [
    "Terrence", "Marisol", "Deshawn", "Terry", "Marguerite", "Ayaan",
  ]) {
    assert.equal(gz.isNotable(name), false, name);
  }
});

test("single-token places are held to the strict bar", () => {
  // "Lee" is a minor geographic feature and one of the twenty most common
  // American surnames. Before single-token places were held to the same bar as
  // single-token person names, it resolved notable via `place` and sailed past
  // the short tier's Census exclusion — a channel that exposed 7.91% of US
  // surname-bearers.
  const gz = load();
  for (const surname of ["Lee", "Bell", "Ford", "Hill", "Wood"]) {
    assert.equal(
      gz.notability(surname),
      NOT_NOTABLE,
      `${surname} resolves ${gz.notability(surname)} — a common American ` +
        `surname is notable via a single-token tier`,
    );
  }
  // The bar must not have swallowed the place names the fixture needs.
  assert.equal(gz.notability("Delaware"), PLACE);
  assert.equal(gz.notability("Washington"), PLACE);
});

// ---------------------------------------------------------------------------
// The settlement tier: typing only, and the guard that keeps it that way
// ---------------------------------------------------------------------------

test("the settlement tier reads back from the shipped asset", () => {
  // A tier that builds clean and reads back empty is the invisible failure: it
  // has no symptom for a tier that only types. Every town would simply mask
  // {NAME}, which is what shipped before the tier existed.
  const gz = load();
  assert.ok(
    gz.settlement.size > 1_000,
    `settlement tier read back with ${gz.settlement.size} entries — the fold ` +
      `produced it but the asset round trip lost it`,
  );
  assert.equal(gz.isSettlement("Akron"), true);
});

test("the settlement tier grants no keep", () => {
  // The one way adding this tier could have done real damage. `notability()`'s
  // contract is `verdict != NOT_NOTABLE => KEEP`, so a settlement leaking into
  // that function would turn every student's hometown into a keep — the exact
  // PII the place tier's settlement exclusion exists to redact, readmitted
  // through the back door by the tier built from what that exclusion discards.
  //
  // Red if anyone wires `settlement` into notability(), which is the plausible
  // mistake: it is the function that already answers "which tier matched", so
  // it looks like the natural home.
  const gz = load();
  const typed = ["Akron", "Westfield", "Springfield", "Phoenix"].filter((name) =>
    gz.isSettlement(name),
  );
  assert.ok(typed.length > 0, "no probe resolved as a settlement — this test proves nothing");
  for (const name of typed) {
    assert.equal(gz.notability(name), NOT_NOTABLE, name);
    assert.equal(gz.isNotable(name), false, name);
  }
});

test("the settlement tier drops names people carry", () => {
  // Half of American town names are somebody's surname, because of the people.
  // Typing on settlement membership alone relabels a classmate named Jackson as
  // {LOCATION} — the Akron defect with the sign flipped, on a far commoner
  // population, and pointing the wrong way: a host reading the type back writes
  // "your friend {LOCATION}".
  const gz = load();
  for (const name of [
    "Jackson", "Madison", "Houston", "Austin", "Cleveland", "Brooklyn", "Aurora",
  ]) {
    assert.equal(
      gz.isSettlement(name),
      false,
      `${name} types as a location — the settlement subtractions regressed, ` +
        `and a person now masks as {LOCATION}`,
    );
  }
});

test("the settlement tier is outside the keep entry count", () => {
  // `entryCount` answers "how much notability", so a non-keep tier is out.
  // Counting 23k towns as notability entries would inflate the one number the
  // asset gate reads.
  const gz = load();
  assert.equal(
    gz.entryCount,
    gz.full.size + gz.short.size + gz.place.size + gz.title.size +
      gz.demonym.size,
  );
});

test("settlements are not in the place tier", () => {
  // A town name is a student's hometown, which is exactly the PII in scope.
  const gz = load();
  for (const town of ["Akron", "Cleveland", "Dayton", "Westfield", "Brooklyn"]) {
    assert.equal(gz.place.has(town.toLowerCase()), false, town);
  }
});

// ---------------------------------------------------------------------------
// The inverse signal — common given names, for the case-insensitive frames
// ---------------------------------------------------------------------------

test("common given names are recognised as a redact signal", () => {
  // The list that makes the lowercase/allcaps frames reachable at all.
  // Capitalisation-based candidate generation scores zero on "then terrence
  // okonkwo showed up" by construction.
  const gz = load();
  assert.equal(gz.isCommonGivenName("terrence"), true);
  assert.equal(gz.isCommonGivenName("Marisol"), true);
  assert.equal(gz.isCommonGivenName("TERRY"), true);
  // Surnames are not given names — the tier is built from label-leading tokens.
  assert.equal(gz.isCommonGivenName("okonkwo"), false);
  assert.equal(gz.isCommonGivenName("pritchard"), false);
  // Multi-token input is not a given name by definition.
  assert.equal(gz.isCommonGivenName("terrence okonkwo"), false);
});

test("given names do not leak into the notability decision", () => {
  // The given tier points the OTHER way, and must never make a name notable.
  // If notability() ever consulted it, the fixture's most basic redact case
  // would start being kept.
  const gz = load();
  assert.equal(gz.isCommonGivenName("terrence"), true);
  assert.equal(gz.notability("terrence"), NOT_NOTABLE);
  assert.equal(gz.notability("Terrence Okonkwo"), NOT_NOTABLE);
});

// ---------------------------------------------------------------------------
// Titles — works and fictional characters
// ---------------------------------------------------------------------------

test("a single-token title is not notable", () => {
  // The safety property that makes the tier affordable. "It", "Up", "Her",
  // "Room" and "Brave" are all films; a single-token title tier would make those
  // ordinary words permanently notable, and notable means KEEP.
  const gz = load();
  for (const word of ["It", "Up", "Her", "Room", "Brave", "Cats"]) {
    assert.equal(gz.isNotable(word), false, word);
  }
});

test("a title of only ordinary words is dropped", () => {
  // "My Best Friend" is a film, and admitting it broke the allcaps frame: with
  // it in the tier, "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER DO THAT" was
  // protected whole and recall on that frame went 100% -> 0%.
  assert.equal(load().isNotable("My Best Friend"), false);
});

test("a person outranks a same-named title", () => {
  // Both verdicts are KEEP; the tier attributed is what telemetry reads. "Joan
  // of Arc" and "van Gogh" are also film titles, and the person is who the
  // student wrote about.
  const gz = load();
  assert.equal(gz.notability("Joan of Arc"), FULL_NAME);
  assert.equal(gz.notability("van Gogh"), ICONIC_SHORT);
  assert.equal(gz.notability("My Cousin Vinny"), TITLE);
});

test("the prefix index reaches every title head", () => {
  // `titleHeads` is the length-1 case of `titlePrefixes`. If they ever
  // disagreed, the scan would skip a position whose first word does start a
  // title.
  const gz = load();
  for (const head of gz.titleHeads) {
    assert.ok(gz.isTitlePrefix(head), head);
  }
});

test("the prefix index stops a walk that cannot reach a title", () => {
  // What the automaton is for: the early exit.
  const gz = load();
  assert.equal(gz.isTitlePrefix("to"), true);
  assert.equal(gz.isTitlePrefix("to kill"), true);
  assert.equal(gz.isTitlePrefix("to kill a mockingbird"), true); // the whole title
  assert.equal(gz.isTitlePrefix("to kill a spider"), false);
});

test("isTitle requires more than one token", () => {
  const gz = load();
  assert.equal(gz.isTitle("To Kill a Mockingbird"), true);
  assert.equal(gz.isTitle("Room"), false);
});

test("a scanner is told how far to look ahead", () => {
  assert.ok(load().maxTitleTokens > 1);
});

// ---------------------------------------------------------------------------
// The asset contract
// ---------------------------------------------------------------------------

test("an asset carrying an unknown tier is refused, not ignored", () => {
  // A tier this reader drops reads back empty, and an empty keep tier redacts
  // everything it was built to protect while looking like over-aggressive
  // tuning. Same reasoning as the format check in the asset layer.
  assert.throws(
    () =>
      new GazetteerIndex({
        format: 5,
        meta: {},
        tiers: new Map([["surnames", new Set(["okonkwo"])]]),
        sha256: "",
        path: "",
      }),
    /unknown gazetteer tier/,
  );
});

test("a tier the asset omits reads as empty rather than throwing", () => {
  // The tiers are additive across asset cuts; a reader that required all of them
  // could not load an older asset at all.
  const index = new GazetteerIndex({
    format: 5,
    meta: {},
    tiers: new Map([["full", new Set(["toni morrison"])]]),
    sha256: "",
    path: "",
  });
  assert.equal(index.notability("Toni Morrison"), FULL_NAME);
  assert.equal(index.settlement.size, 0);
  assert.equal(index.isSettlement("Akron"), false);
});

test("every tier the shipped asset carries is one this reader knows", () => {
  // The reconciliation the Python package does against its manifest, done here
  // against the same asset: a tier added to the builder and forgotten in
  // TIER_NAMES would now be a red test rather than a silent refusal.
  const gz = load();
  assert.deepEqual(
    [...TIER_NAMES].sort(),
    [
      "demonym", "full", "given", "place", "settlement", "short", "title",
    ],
  );
  assert.ok(gz.full.size > 0);
});

test("the particle list is the one the partial-surname rule reads", () => {
  // "van Gogh" and "de Gaulle" reach the short tier only through this set; a
  // particle dropped from it turns an iconic short name into a redaction.
  assert.ok(PARTICLES.has("van"));
  assert.ok(PARTICLES.has("de"));
  assert.equal(load().notability("van Gogh"), ICONIC_SHORT);
});

test("a demonym is its own verdict", () => {
  // Not folded into PLACE: it is a word derived from one, it is the only keep
  // tier with no notability evidence behind it, and eval attribution needs to
  // see it separately to tell whether this tier is where a leak came from.
  const gz = load();
  const found = ["Cuban", "Nigerian", "Bostonian", "Kenyan"].filter(
    (word) => gz.notability(word) === DEMONYM,
  );
  assert.ok(found.length > 0, "no probe resolved as a demonym — this proves nothing");
});
