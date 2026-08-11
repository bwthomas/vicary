/**
 * Read a language-neutral word list that ships beside the gazetteer.
 *
 * The stoplist used to be a literal in Python's `name_candidates.py`, which was
 * fine while there was one front door. With three, a hand-transliterated word
 * list is a second detector wearing the first one's name: the divergence shows up
 * as prose corruption in one language and not the others, and no parity check on
 * *masked output* would catch it, because a stop word going missing changes what
 * gets masked in essays nobody put in a fixture.
 *
 * So the list is authored once, language-neutrally, under `asset/lexicon/` in the
 * repository, and vendored into each package the same way the gazetteer is. This
 * is the reader; it owns nothing.
 *
 * Format
 * ------
 * `#!` lines are directives, `#` lines are comments, blank lines are skipped, and
 * every other line contributes whitespace-separated words. One directive is
 * required:
 *
 *     #!lexicon 1               format version
 *     #!list <name> <count>     the list's name, and its DISTINCT word count
 *
 * The count is asserted, not trusted. A short read here makes the redactor
 * **more** aggressive — fewer stop words means more capitalised ordinary words
 * become name candidates — which looks privacy-safe, corrupts prose, and passes
 * any check that only asks whether something was masked. Same reasoning as the
 * gazetteer's per-tier counts; same failure mode if it is skipped.
 *
 * This is the third reader of this format, after `asset/vicary_build/lexicon.py`
 * and `python/src/vicary/lexicon.py`. The duplication is deliberate — the build
 * tool must not import one of the three implementations it feeds — and it is only
 * honest because something compares the results. `test/lexicon.test.ts` pins this
 * reader's output against the count and the spot-checks that
 * `asset/tests/test_lexicon.py` pins the other two against.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { assetSearchPath } from "./asset.js";

/**
 * On-disk format version for a lexicon file. Bump when the parse changes, so a
 * stale vendored copy fails loudly rather than parsing to a different list.
 */
export const LEXICON_FORMAT = 1;

/** Filename suffix. Named so a second list costs a file rather than a refactor. */
export const SUFFIX = ".txt";

/**
 * A lexicon is absent, unreadable, or not the shape this reader understands.
 *
 * Its own type rather than a bare `Error` for the same reason `GazetteerAssetError`
 * has one: a caller that wants to tell "the install is incomplete" apart from
 * "something else threw" cannot do it on a message.
 */
export class LexiconError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LexiconError";
  }
}

/**
 * Where the vendored copy of `name` lives, whether or not it exists.
 *
 * Searched along the same path as the gazetteer, in the same order, so a package
 * cannot end up reading its stoplist from one cut and its gazetteer from another.
 */
export function lexiconPath(name: string): string {
  const filename = `${name}${SUFFIX}`;
  const tried = assetSearchPath();
  for (const directory of tried) {
    const candidate = join(directory, filename);
    if (existsSync(candidate)) return candidate;
  }
  // Return the first location rather than throwing, so `load` reports the same
  // "missing" error whether the directory is absent or the file inside it is.
  return join(tried[0] ?? ".", filename);
}

/**
 * Parse lexicon text into its case-folded distinct words.
 *
 * Exported so a test can feed it a deliberately malformed document. A parser
 * reachable only through a vendored file on disk is a parser whose failure paths
 * are never exercised — and every one of this parser's failure paths exists to
 * turn a silent short read into a loud one.
 *
 * @param where - a path, used only in error messages, so a failure names a file.
 */
export function parseLexicon(
  name: string,
  text: string,
  where: string,
): Set<string> {
  let declared: number | null = null;
  let sawFormat = false;
  const words = new Set<string>();

  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i += 1) {
    const lineno = i + 1;
    const stripped = (lines[i] ?? "").trim();
    if (stripped.startsWith("#!")) {
      const parts = stripped.slice(2).split(/\s+/).filter((part) => part !== "");
      if (parts.length === 0) {
        throw new LexiconError(`${where}:${lineno}: empty directive`);
      }
      if (parts[0] === "lexicon") {
        sawFormat = true;
        if (parts.length !== 2 || parts[1] !== String(LEXICON_FORMAT)) {
          throw new LexiconError(
            `${where}:${lineno}: lexicon format ${JSON.stringify(
              parts.slice(1).join(" "),
            )}, this build reads ${LEXICON_FORMAT}`,
          );
        }
      } else if (parts[0] === "list") {
        if (parts.length !== 3 || parts[1] !== name) {
          throw new LexiconError(
            `${where}:${lineno}: expected \`#!list ${name} <count>\`, got ` +
              `${JSON.stringify(stripped)}`,
          );
        }
        // `parseInt` would read "3x" as 3, where Python's `int()` raises. The
        // difference is the whole guard: a count this reader silently repaired is
        // a count that no longer proves anything about the parse.
        if (!/^\d+$/.test(parts[2]!)) {
          throw new LexiconError(
            `${where}:${lineno}: count ${JSON.stringify(parts[2])} is not an ` +
              `integer`,
          );
        }
        declared = Number.parseInt(parts[2]!, 10);
      } else {
        // Refused rather than ignored: an unrecognised directive means the file
        // was written by something that knows more than this reader, and guessing
        // which lines are still words is how a partial list loads as a whole one.
        throw new LexiconError(
          `${where}:${lineno}: unknown directive ${JSON.stringify(parts[0])}`,
        );
      }
      continue;
    }
    if (stripped === "" || stripped.startsWith("#")) continue;
    for (const word of stripped.split(/\s+/)) {
      if (word !== "") words.add(word.toLowerCase());
    }
  }

  if (!sawFormat) {
    throw new LexiconError(
      `${where}: no \`#!lexicon\` directive; not a lexicon file`,
    );
  }
  if (declared === null) {
    throw new LexiconError(`${where}: no \`#!list ${name} <count>\` directive`);
  }
  if (words.size !== declared) {
    throw new LexiconError(
      `${where}: declares ${declared} distinct words, parsed ${words.size}. A ` +
        `short read makes the redactor more aggressive, which is why this is an ` +
        `error and not a warning.`,
    );
  }
  return words;
}

/**
 * The case-folded distinct words of lexicon `name`.
 *
 * Throws {@link LexiconError} rather than returning a partial list. An empty or
 * truncated stoplist is the quiet failure this whole module exists to prevent.
 */
export function load(
  name: string,
  options: { path?: string } = {},
): ReadonlySet<string> {
  const path = options.path ?? lexiconPath(name);
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (error) {
    const reason = error as NodeJS.ErrnoException;
    if (reason.code === "ENOENT") {
      throw new LexiconError(
        `lexicon ${JSON.stringify(name)} missing at ${path}. The installed ` +
          `vicary package is incomplete — reinstall it, or vendor the asset with ` +
          `\`npm run sync-assets\` from a checkout.`,
      );
    }
    throw new LexiconError(`cannot read lexicon at ${path}: ${reason.message}`);
  }
  return parseLexicon(name, text, path);
}
