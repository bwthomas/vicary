/**
 * Adlib's scoreboard against `conformance/adlib.json`: every layer, byte for byte.
 *
 * The cases are Python's output. Each layer prints `matched/total` on every run,
 * green or not, and the hard assertions below name each case that misses.
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  AdlibPoolExhausted,
  acronym,
  cardPlug,
  draw,
  invent,
  loadPool,
  poolPath,
  renderAdlib,
  restoreAdlib,
  shiftDate,
  stopWords,
} from "../src/adlib.js";
import { isCommonGivenName, isNotable } from "../src/gazetteer.js";
import { loadSpec } from "../src/conformance.js";
import { redactWithReport } from "../src/redact.js";
import type { RedactionResult } from "../src/redact.js";

type Dict = Record<string, string>;
/** An ordered map as adlib.json ships it: [key, value] pairs in insertion order. */
type Pairs = [string, string][];

interface InventCase {
  case_id: string;
  document: string;
  identity: string[];
  oracles: "production" | "none";
  seed: number | string;
  spans: [string, string][] | { generator: string };
  stand_ins?: string[];
  tokens?: Pairs;
  raises?: string;
}

interface RenderCase {
  case_id: string;
  original: string;
  seed: number;
  placeholder_text: string;
  placeholder_restore_map: Pairs;
  text: string;
  restore_map: Pairs;
  adlib_map: Pairs;
  adlib_tokens: Pairs;
  spans: [number, number, number, number, string][];
}

interface RestoreCase {
  case_id: string;
  text: string;
  restore_map: Pairs;
  tokens: Pairs;
  restored: string;
}

interface AdlibSpec {
  document_version: number;
  pools: Record<string, { sha256: string; entries: number }>;
  draw: { args: [string, string, string, string, number, number, number]; index: number }[];
  acronym: { name: string; acronym: string }[];
  card_plug: { digits: string; variant: number; plug: string }[];
  shift_date: { date: string; offset: number; shifted: string }[];
  invent: InventCase[];
  render: RenderCase[];
  restore: RestoreCase[];
}

const here = dirname(fileURLToPath(import.meta.url));
const specPath = [
  resolve(here, "..", "..", "..", "conformance", "adlib.json"),
  resolve(here, "..", "..", "conformance", "adlib.json"),
].find((p) => {
  try {
    readFileSync(p);
    return true;
  } catch {
    return false;
  }
}) as string;
const spec = JSON.parse(readFileSync(specPath, "utf8")) as AdlibSpec;

/** One layer's misses, by case id. */
const layers = new Map<string, { total: number; misses: string[] }>();
function score(layer: string, id: string, ok: boolean, detail = ""): void {
  const entry = layers.get(layer) ?? { total: 0, misses: [] };
  entry.total += 1;
  if (!ok) entry.misses.push(detail ? `${id}: ${detail}` : id);
  layers.set(layer, entry);
}

const asDict = (m: Map<string, string>): Dict => Object.fromEntries(m);
/** Order-sensitive: restore is first-wins, so insertion order is part of the answer. */
const samePairs = (got: Map<string, string>, want: Pairs): boolean =>
  JSON.stringify([...got]) === JSON.stringify(want);

// --- pools -----------------------------------------------------------------
for (const [name, entry] of Object.entries(spec.pools)) {
  const digest = createHash("sha256").update(readFileSync(poolPath(name))).digest("hex");
  const n = loadPool(name).length;
  score("pools", name, digest === entry.sha256 && n === entry.entries,
    `sha256 ${digest.slice(0, 12)}, ${n} entries`);
}

// --- primitives --------------------------------------------------------------
spec.draw.forEach((c, i) => {
  const got = draw(...c.args);
  score("draw", String(i), got === c.index, `got ${got}, want ${c.index}`);
});
spec.acronym.forEach((c) => {
  const got = acronym(c.name);
  score("acronym", c.name, got === c.acronym, `got ${got}`);
});
spec.card_plug.forEach((c) => {
  const got = cardPlug(c.digits, c.variant);
  score("card_plug", `${c.digits}@${c.variant}`, got === c.plug, `got ${got}`);
});
spec.shift_date.forEach((c) => {
  const got = shiftDate(c.date, c.offset);
  score("shift_date", `${c.date}+${c.offset}`, got === c.shifted, `got ${got}`);
});

// --- invent ------------------------------------------------------------------
function expandSpans(spans: InventCase["spans"]): [string, string][] {
  if (Array.isArray(spans)) return spans;
  const m = /^NAME '(.*)' for i in 0\.\.(\d+)$/.exec(spans.generator);
  if (m === null) throw new Error(`unknown generator ${spans.generator}`);
  const out: [string, string][] = [];
  for (let i = 0; i <= Number(m[2]); i++) out.push(["NAME", (m[1] as string).replaceAll("{i}", String(i))]);
  return out;
}

for (const c of spec.invent) {
  const production = c.oracles === "production";
  const run = (): ReturnType<typeof invent> => invent(expandSpans(c.spans), {
    document: c.document,
    seed: c.seed,
    identity: c.identity,
    ...(production ? { stopWords: stopWords(), givenName: isCommonGivenName, notable: isNotable } : {}),
  });
  if (c.raises) {
    let raised = "";
    try {
      run();
    } catch (error) {
      raised = error instanceof AdlibPoolExhausted ? "AdlibPoolExhausted" : String(error);
    }
    score("invent", c.case_id, raised === c.raises, `raised ${raised || "nothing"}`);
    continue;
  }
  try {
    const got = run();
    const ok = JSON.stringify(got.standIns) === JSON.stringify(c.stand_ins) &&
      samePairs(got.tokens, c.tokens ?? []);
    score("invent", c.case_id, ok,
      `got ${JSON.stringify(got.standIns)} ${JSON.stringify(asDict(got.tokens))}`);
  } catch (error) {
    score("invent", c.case_id, false, `threw ${error}`);
  }
}

// --- render --------------------------------------------------------------------
const identity = loadSpec().identity;
for (const c of spec.render) {
  const id = `${c.case_id}@${c.seed}`;
  const input: RedactionResult = {
    text: c.placeholder_text,
    nMasked: c.placeholder_restore_map.length,
    restoreMap: new Map(c.placeholder_restore_map),
  };
  try {
    const got = renderAdlib(input, c.original, { seed: c.seed });
    const spans = (got.adlibSpans ?? []).map(({ span, original }) =>
      [span.origStart, span.origEnd, span.newStart, span.newEnd, original]);
    const misses = [
      got.text !== c.text ? `text ${JSON.stringify(got.text)}` : "",
      !samePairs(got.restoreMap, c.restore_map) ? "restore_map" : "",
      !samePairs(got.adlibMap ?? new Map(), c.adlib_map) ? "adlib_map" : "",
      !samePairs(got.adlibTokens ?? new Map(), c.adlib_tokens) ? "adlib_tokens" : "",
      JSON.stringify(spans) !== JSON.stringify(c.spans) ? "spans" : "",
    ].filter(Boolean);
    score("render", id, misses.length === 0, misses.join("; "));
  } catch (error) {
    score("render", id, false, `threw ${error}`);
  }
  // This port's own placeholder pass, given the identity the reference arm used,
  // must reproduce render's placeholder input, map order included.
  const own = redactWithReport(c.original, identity);
  score("render_input", id,
    own.text === c.placeholder_text && samePairs(own.restoreMap, c.placeholder_restore_map),
    `got ${JSON.stringify(own.text)}`);
}

// --- restore -------------------------------------------------------------------
for (const c of spec.restore) {
  try {
    const got = restoreAdlib(c.text, new Map(c.restore_map), new Map(c.tokens));
    score("restore", c.case_id, got === c.restored, `got ${JSON.stringify(got)}`);
  } catch (error) {
    score("restore", c.case_id, false, `threw ${error}`);
  }
}

const lines = [...layers].map(([layer, { total, misses }]) =>
  `  ${layer.padEnd(11)} ${total - misses.length}/${total}`);
console.log(`adlib conformance (document_version ${spec.document_version})\n${lines.join("\n")}`);

for (const [layer, { misses }] of layers) {
  test(`adlib ${layer}: every case matches Python`, () => {
    assert.deepEqual(misses, []);
  });
}

test("the spec carries every layer", () => {
  assert.equal(spec.document_version, 1);
  for (const layer of ["pools", "draw", "acronym", "card_plug", "shift_date", "invent", "render", "restore"]) {
    assert.ok((layers.get(layer)?.total ?? 0) > 0, `no ${layer} cases scored`);
  }
});
