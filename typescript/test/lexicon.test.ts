/**
 * The lexicon reader, and the guards that make a short read loud.
 *
 * Every front door ships its own reader for this format, because the build tool
 * must not import one of the three implementations it feeds. The duplication is
 * only honest if something compares the results — `asset/tests/test_lexicon.py`
 * does that for the two Python readers, and this file holds the third to the same
 * numbers, against the same shipped bytes.
 *
 * The count is checked twice on purpose: once against the literal 421, and once
 * against the manifest's own `entries` field. The literal catches a truncated
 * vendored file; the manifest cross-check catches the case the literal cannot —
 * an asset cut that changed the list, where a hand-updated constant in one
 * language and not the others is exactly the drift the shared lexicon exists to
 * prevent.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { assetSearchPath, MANIFEST_FILENAME } from "../src/asset.js";
import {
  LEXICON_FORMAT,
  LexiconError,
  lexiconPath,
  load,
  parseLexicon,
} from "../src/lexicon.js";

const here = dirname(fileURLToPath(import.meta.url));

/** Parse a probe document, so the failure paths are reachable without a file. */
function probe(body: string, name = "probe"): Set<string> {
  return parseLexicon(name, body, "probe.txt");
}

test("the shipped stoplist parses to its declared 421 words", () => {
  const words = load("stop_words");
  assert.equal(words.size, 421);
  // Spot-checks at the two ends of the file, so a truncated read fails here and
  // not only on the count. Same two words the Python suite checks.
  assert.ok(words.has("the"));
  assert.ok(words.has("favorite"));
  // Case-folded on read, so a reader never has to remember to fold.
  for (const word of words) assert.equal(word, word.toLowerCase());
});

test("the parsed count matches what the manifest declares", () => {
  const directory = dirname(lexiconPath("stop_words"));
  const manifest = JSON.parse(
    readFileSync(join(directory, MANIFEST_FILENAME), "utf8"),
  ) as { assets: Record<string, { entries?: number; format?: number }> };
  const entry = manifest.assets["stop_words.txt"];
  assert.ok(entry, "the manifest does not describe stop_words.txt");
  assert.equal(load("stop_words").size, entry.entries);
  assert.equal(entry.format, LEXICON_FORMAT);
});

test("the stoplist is read from the same directory as the gazetteer", () => {
  // Not decoration: a package that finds its stoplist in one cut and its
  // gazetteer in another has two halves of two different detectors, and every
  // symptom of that is a masking decision nobody can reproduce.
  const directory = dirname(lexiconPath("stop_words"));
  assert.ok(
    assetSearchPath().includes(directory),
    `stoplist resolved to ${directory}, which is not on the asset search path`,
  );
});

// ---------------------------------------------------------------------------
// The guards. Each has a plausible failing case, written out rather than implied.
// ---------------------------------------------------------------------------

test("a declared count that disagrees is an error", () => {
  // The guard that matters most, and the one whose absence is invisible. A short
  // read makes every reader of this list *more* aggressive about what counts as a
  // name — fewer stop words means more capitalised ordinary words become
  // candidates. That looks privacy-safe, corrupts prose, and passes any check
  // that only asks whether something was masked.
  assert.throws(
    () => probe("#!lexicon 1\n#!list probe 3\nalpha beta\n"),
    (error: unknown) =>
      error instanceof LexiconError &&
      /declares 3 distinct words, parsed 2/.test(error.message),
  );
});

test("duplicates count once", () => {
  // The groupings in the source file overlap on purpose ("else", "may", "us").
  // Enforcing uniqueness in the source would make the list harder to read for no
  // benefit, so the count is of DISTINCT words and this is what that means.
  const words = probe("#!lexicon 1\n#!list probe 2\nalpha beta\nbeta alpha\n");
  assert.deepEqual([...words].sort(), ["alpha", "beta"]);
});

test("a file with no format directive is refused", () => {
  assert.throws(
    () => probe("#!list probe 1\nalpha\n"),
    /no `#!lexicon` directive/,
  );
});

test("a format this build does not read is refused", () => {
  assert.throws(
    () => probe("#!lexicon 2\n#!list probe 1\nalpha\n"),
    /lexicon format "2", this build reads 1/,
  );
});

test("a file with no list directive is refused", () => {
  assert.throws(() => probe("#!lexicon 1\nalpha\n"), /no `#!list probe <count>`/);
});

test("a list directive naming a different list is refused", () => {
  // The name is how a caller says which list it asked for. A reader that accepts
  // any `#!list` line will happily load the wrong file under the right name.
  assert.throws(
    () => probe("#!lexicon 1\n#!list other 1\nalpha\n"),
    /expected `#!list probe <count>`/,
  );
});

test("an unrecognised directive is refused rather than skipped", () => {
  // Skipping it means the file was written by something that knows more than this
  // reader, and guessing which lines are still words is how a partial list loads
  // as a whole one.
  assert.throws(
    () => probe("#!lexicon 1\n#!list probe 1\n#!tier given 5\nalpha\n"),
    /unknown directive "tier"/,
  );
});

test("a non-integer count is refused rather than repaired", () => {
  // JavaScript's `parseInt` reads "3x" as 3, where Python's `int()` raises. A
  // count this reader silently repaired is a count that no longer proves anything
  // about the parse, so the two languages have to refuse the same input.
  assert.throws(
    () => probe("#!lexicon 1\n#!list probe 3x\nalpha beta\n"),
    /count "3x" is not an integer/,
  );
});

test("comments and blank lines contribute no words", () => {
  const words = probe(
    "#!lexicon 1\n#!list probe 1\n# a comment\n\n   \nalpha\n# another\n",
  );
  assert.deepEqual([...words], ["alpha"]);
});

test("a missing lexicon names the file and how to get it", () => {
  assert.throws(
    () => load("stop_words", { path: join(here, "no-such-lexicon.txt") }),
    (error: unknown) =>
      error instanceof LexiconError &&
      error.message.includes("no-such-lexicon.txt") &&
      error.message.includes("npm run sync-assets"),
  );
});
