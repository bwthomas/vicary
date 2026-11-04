/**
 * Adlib behaviour beyond the byte cases in `conformance/adlib.json`: the
 * properties `python/tests/test_adlib.py` pins, ported test for test.
 *
 * Not ported: the `VICARY_RENDER` / `VICARY_ADLIB_SEED` environment tests (the
 * TypeScript redactor reads neither) and the logging test (it logs nothing).
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";

import {
  AdlibPoolExhausted,
  AGE_BANDS,
  CARD_PLUGS,
  PHONE_AREA_CODE,
  PHONE_PLUG,
  SSN_PLUG,
  URL_SHAPES,
  acronym,
  cardPlug,
  draw,
  invent,
  inventNames,
  loadPool,
  renderAdlib,
  restoreAdlib,
  shiftDate,
  stopWords,
  type InventOptions,
  type Seed,
} from "../src/adlib.js";
import {
  isCommonGivenName,
  isNotable,
  isSettlement,
  isTitle,
} from "../src/gazetteer.js";
import { placeholderKind } from "../src/placeholders.js";
import { redactBatchWithReport, redactWithReport, type RedactionResult } from "../src/redact.js";
import { deriveSpans } from "../src/spans.js";

const ESSAY =
  "Maria Lopez helped me study. Later Maria said Ms. Lopez was strict, but " +
  "Maria's notes saved me. I yelled MARIA LOPEZ across the gym. My friend " +
  "Terrence Okonkwo and Jane Quincy-Adams met mr. kowalski there. Abraham " +
  "Lincoln was the topic.";

const NO_IDENTITY = { firstName: "", lastName: "", schoolName: "" };
const GIVEN_POOL = loadPool("given");
const SURNAME_POOL = loadPool("surname");

function redactIt(text: string, render?: "adlib", adlibSeed?: Seed): RedactionResult {
  return redactWithReport(text, NO_IDENTITY, {
    ...(render ? { render } : {}),
    ...(adlibSeed === undefined ? {} : { adlibSeed }),
  });
}

const names = (originals: string[], options: InventOptions): Record<string, string> =>
  Object.fromEntries(inventNames(originals, options));
const one = (kind: string, original: string): string =>
  invent([[kind, original]], { document: "doc" }).standIns[0] as string;
const kinds = (spans: [string, string][]): string[] => invent(spans, { document: "doc" }).standIns;
const split2 = (s: string): [string, string] => s.split(" ") as [string, string];
const cps = (s: string): string[] => Array.from(s);

test("spans are identical to placeholder mode", () => {
  const placeholder = redactIt(ESSAY);
  const adlib = redactIt(ESSAY, "adlib");
  const pSpans = deriveSpans(placeholder.text, placeholder.restoreMap);
  assert.ok(pSpans.length > 0, "the fixture must exercise redaction");
  const aSpans = adlib.adlibSpans ?? [];
  assert.deepEqual(aSpans.map(({ span }) => [span.origStart, span.origEnd]),
    pSpans.map((s) => [s.origStart, s.origEnd]));
  // The original is recoverable from the spans, and each span's text is a restore key.
  const text = cps(adlib.text);
  let prev = 0;
  let back = "";
  for (const { span, original } of aSpans) {
    const standIn = text.slice(span.newStart, span.newEnd).join("");
    assert.ok(adlib.restoreMap.has(standIn));
    back += text.slice(prev, span.newStart).join("") + original;
    prev = span.newEnd;
  }
  assert.equal(back + text.slice(prev).join(""), ESSAY);
});

test("no placeholders, and public figures are kept", () => {
  const adlib = redactIt(ESSAY, "adlib");
  assert.ok(!adlib.text.includes("{"));
  assert.ok(adlib.text.includes("Abraham Lincoln"));
  assert.equal(adlib.render, "adlib");
});

test("person-level consistency across titles, possessives and case", () => {
  const out = names(["Maria Lopez", "Maria", "Ms. Lopez", "lopez", "Maria's", "MARIA LOPEZ", "Lopez, Maria"],
    { document: "x", seed: 3 });
  const [given, surname] = split2(out["Maria Lopez"] as string);
  assert.ok(GIVEN_POOL.includes(given) && SURNAME_POOL.includes(surname));
  assert.equal(out["Maria"], given);
  assert.equal(out["Ms. Lopez"], `Ms. ${surname}`);
  assert.equal(out["lopez"], surname.toLowerCase());
  assert.equal(out["Maria's"], `${given}'s`);
  assert.equal(out["MARIA LOPEZ"], `${given.toUpperCase()} ${surname.toUpperCase()}`);
  assert.equal(out["Lopez, Maria"], `${surname}, ${given}`);
});

test("hyphenated surname and initials", () => {
  const out = names(["Jane Quincy-Adams", "J. Smith", "K. Smith"], { document: "", seed: 0 });
  const [given, double] = split2(out["Jane Quincy-Adams"] as string);
  const [left, right] = double.split("-") as [string, string];
  assert.ok(GIVEN_POOL.includes(given) && SURNAME_POOL.includes(left) && SURNAME_POOL.includes(right));
  assert.notEqual(out["J. Smith"], out["K. Smith"]);
  const j = out["J. Smith"] as string;
  assert.equal(j[1], ".");
  assert.ok(!"JK".includes(j[0] as string));
});

test("possessive after s", () => {
  const standIn = names(["James'"], { document: "James' bag", seed: 0 })["James'"] as string;
  assert.ok(standIn.slice(0, -1).endsWith("s") ? standIn.endsWith("'") : standIn.endsWith("'s"));
});

test("unique people, injective tokens", () => {
  const people = ["Ann Lee", "Bo Diaz", "Cy Park", "Di Wu", "Ed Kim", "Fay Roe"];
  const out = Object.values(names(people, { document: people.join(" "), seed: 0 }));
  assert.equal(new Set(out).size, people.length);
  const tokens = out.flatMap((v) => v.split(" "));
  assert.equal(new Set(tokens).size, tokens.length);
});

test("a shared given name stays shared", () => {
  const out = names(["Maria Lopez", "Maria Chen"], { document: "", seed: 0 });
  assert.equal(split2(out["Maria Lopez"] as string)[0], split2(out["Maria Chen"] as string)[0]);
  assert.notEqual(out["Maria Lopez"], out["Maria Chen"]);
});

test("no collision with document words", () => {
  const first = names(["Maria"], { document: "Maria", seed: 0 })["Maria"] as string;
  const out = names(["Maria"], { document: `Maria met ${first} and ${first.toLowerCase()}.`, seed: 0 });
  assert.notEqual(out["Maria"], first);
});

test("deterministic, and the seed moves it", () => {
  assert.equal(redactIt(ESSAY, "adlib", 7).text, redactIt(ESSAY, "adlib", 7).text);
  assert.notEqual(redactIt(ESSAY, "adlib", 7).text, redactIt(ESSAY, "adlib", 8).text);
  // An integer seed is drawn as its decimal string.
  assert.equal(redactIt(ESSAY, "adlib", 7).text, redactIt(ESSAY, "adlib", "7").text);
});

test("the default render is placeholder", () => {
  assert.ok(redactIt(ESSAY).text.includes("{NAME_"));
  assert.equal(redactIt(ESSAY).render, undefined);
});

test("adlib refuses without a numbered restore map", () => {
  assert.throws(() => redactWithReport(ESSAY, NO_IDENTITY, { render: "adlib", numberPlaceholders: false }),
    /adlib rendering needs local mode with numbered placeholders/);
  assert.throws(() => renderAdlib({ text: "hi {NAME}", nMasked: 0, restoreMap: new Map() }, "hi Bo"),
    /adlib needs a numbered restore map to render over/);
  assert.throws(() => redactWithReport(ESSAY, NO_IDENTITY, { render: "names" as "adlib" }), /render must be/);
  assert.throws(() => redactBatchWithReport([ESSAY], NO_IDENTITY, { render: "adlib" }), /not a batch/);
});

test("text with nothing redacted renders unchanged", () => {
  const out = redactIt("Nothing to see here.", "adlib");
  assert.equal(out.text, "Nothing to see here.");
  assert.equal(out.render, "adlib");
});

test("re-running vicary redacts the stand-ins", () => {
  const first = redactIt(ESSAY, "adlib");
  const second = redactIt(first.text);
  assert.equal(deriveSpans(second.text, second.restoreMap).length, (first.adlibSpans ?? []).length);
});

test("pool exhaustion raises", () => {
  const many = Array.from({ length: GIVEN_POOL.length + 1 }, (_, i) => `Q${i}x Zz${i}`);
  assert.throws(() => inventNames(many, { document: "", seed: 0 }), AdlibPoolExhausted);
});

test("given and surname pool entries are safe", () => {
  const stop = stopWords();
  for (const name of GIVEN_POOL) {
    assert.ok(isCommonGivenName(name), name);
    assert.ok(!isNotable(name) && !isSettlement(name) && !isTitle(name), name);
    assert.ok(!stop.has(name.toLowerCase()), name);
  }
  for (const name of SURNAME_POOL) {
    assert.ok(!isCommonGivenName(name), name);
    assert.ok(!isNotable(name) && !isSettlement(name) && !isTitle(name), name);
    assert.ok(!stop.has(name.toLowerCase()), name);
  }
});

test("pools are disjoint and unique", () => {
  assert.equal(new Set(GIVEN_POOL).size, GIVEN_POOL.length);
  assert.equal(new Set(SURNAME_POOL).size, SURNAME_POOL.length);
  assert.ok(!GIVEN_POOL.some((n) => SURNAME_POOL.includes(n)));
});

// -- every other kind -----------------------------------------------------------

test("draw test vectors, and the draw spelled out", () => {
  const doc = createHash("sha256").update("Maria Lopez").digest("hex");
  assert.equal(doc, "ec7dafcd351fe5964d3cc607a9e5b12d036ea6cac582fa22dc70a48330d8dd2f");
  const empty = createHash("sha256").update("").digest("hex");
  const vectors: [[string, string, string, string, number, number, number], number][] = [
    [["0", doc, "given", "maria", 0, 0, 1000], 885],
    [["0", doc, "surname", "lopez", 0, 0, 1000], 882],
    [["7", doc, "scramble", "12345", 2, 3, 10], 6],
    [["seed", empty, "age", "14", 0, 0, 4], 0],
  ];
  for (const [args, expected] of vectors) {
    assert.equal(draw(...args), expected);
    const [seed, digest, purpose, key, bump, index, n] = args;
    const material = [seed, digest, purpose, key, `${bump}.${index}`].join("\0");
    const head = createHash("sha256").update(material).digest().readBigUInt64BE(0);
    assert.equal(Number(head % BigInt(n)), expected);
  }
});

test("plugs are pinned", () => {
  const cases: [string, string[], string[]][] = [
    ["US_SOCIAL_SECURITY_NUMBER", ["123-45-6789", "987-65-4321", "123456789"],
      ["799-99-9999", "799-99-9998", "799999997"]],
    ["ZIP_CODE", ["94110", "10001-1234", "60614"], ["99999", "99998-9999", "99997"]],
    ["PHONE", ["(973) 444-1234", "+1 973-444-9999 ext. 12", "973.444.0000"],
      ["(201) 555-0100", "+1 201-555-0101 ext. 12", "201.555.0102"]],
    ["IP_ADDRESS", ["10.1.2.3", "172.16.0.9"], ["192.0.2.1", "192.0.2.2"]],
    ["CREDIT_DEBIT_CARD_NUMBER", ["4012888888881881", "4000056655665556"],
      ["4111111111111111", "4111111111111103"]],
  ];
  for (const [kind, originals, plugs] of cases) {
    assert.deepEqual(kinds(originals.map((o) => [kind, o])), plugs, kind);
  }
});

test("card plug by brand", () => {
  const cases: [string, string][] = [
    ["4012 8888 8888 1881", "4111 1111 1111 1111"], ["3714-496353-98431", "3782-822463-10005"],
    ["5105105105105100", "5555555555554444"], ["6011000990139424", "6011111111111117"],
    ["30000000000004", "30569309025904"], ["3566002020360505", "3530111333300000"],
    ["4000000000006", "4222222222222"], ["9999888877776666", "4111111111111111"],
  ];
  for (const [original, plug] of cases) assert.equal(one("CREDIT_DEBIT_CARD_NUMBER", original), plug);
});

test("the phone plug never keeps the area code or uses 555 as one", () => {
  assert.equal(PHONE_PLUG, "201-555-0100");
  assert.equal(PHONE_AREA_CODE, "201");
  assert.ok(one("PHONE", "201-444-1234").startsWith("201-555-01"));
  assert.ok(!one("PHONE", "555-444-1234").startsWith("555-555"));
  assert.equal(one("PHONE", "444-1234"), "555-0100");
});

test("plug variants are distinct and repeat for one value", () => {
  assert.deepEqual(kinds([["US_SOCIAL_SECURITY_NUMBER", "123-45-6789"],
    ["US_SOCIAL_SECURITY_NUMBER", "222-33-4444"], ["US_SOCIAL_SECURITY_NUMBER", "123-45-6789"]]),
  [SSN_PLUG, "799-99-9998", SSN_PLUG]);
  const cards = kinds(["4012888888881881", "4000056655665556", "4242424242424242"]
    .map((c) => ["CREDIT_DEBIT_CARD_NUMBER", c]));
  assert.equal(cards[0], "4111111111111111");
  assert.equal(new Set(cards).size, 3);
});

test("every plug re-detects as its kind", () => {
  for (const text of [
    "My SSN is 799-99-9999.", "Card 4111111111111111 on file.",
    "Card 378282246310005 on file.", "Card 5555555555554444 on file.",
    "Card 6011111111111117 on file.", "Card 30569309025904 on file.",
    "Card 3530111333300000 on file.", "Card 4222222222222 on file.",
    "Call (201) 555-0100 now.", "From 192.0.2.1 today.", "zip 99999-9999",
  ]) {
    const found = new Set([...redactIt(text).restoreMap.keys()].map(placeholderKind));
    assert.equal(found.size, 1, text);
  }
});

test("plugs re-detect on a second pass over adlib output", () => {
  const text = "SSN 123-45-6789, card 4012 8888 8888 1881, call (973) 444-1234, IP 10.1.2.3, zip 94110-1234.";
  const second = redactIt(redactIt(text, "adlib").text);
  const kindsOf = (r: RedactionResult): string[] => [...r.restoreMap.keys()].map(placeholderKind).sort();
  assert.deepEqual(kindsOf(second), kindsOf(redactIt(text)));
});

test("card plugs are Luhn-valid", () => {
  const luhnOk = (digits: string): boolean => {
    let total = 0;
    [...digits].reverse().forEach((ch, i) => {
      const d = Number(ch) * (i % 2 ? 2 : 1);
      total += d > 9 ? d - 9 : d;
    });
    return total % 10 === 0;
  };
  for (const [, , brand] of CARD_PLUGS) {
    for (let variant = 0; variant < 5; variant++) assert.ok(luhnOk(cardPlug(brand, variant)), brand);
  }
  assert.ok(luhnOk(cardPlug("1".repeat(16), 0)));
});

test("an age stays in its band and moves", () => {
  for (const [lo, hi] of AGE_BANDS) {
    for (const age of new Set([lo, hi, Math.floor((lo + hi) / 2)])) {
      const out = Number(one("AGE", String(age)));
      assert.ok(lo <= out && out <= hi && out !== age, `${age} -> ${out}`);
    }
  }
});

test("ages are distinct and avoid other originals", () => {
  const out = kinds([["AGE", "26"], ["AGE", "27"], ["AGE", "28"]]);
  assert.equal(new Set(out).size, 3);
  assert.ok(!out.some((a) => ["26", "27", "28"].includes(a)));
  const tight = kinds([["AGE", "14"], ["AGE", "15"], ["AGE", "16"]]);
  assert.equal(new Set(tight).size, 3);
  assert.ok(tight.every((a) => Number(a) >= 14 && Number(a) <= 18));
  assert.ok(tight.every((a, i) => a !== ["14", "15", "16"][i]));
});

test("a DOB keeps year and format and never lands on an original", () => {
  const dobs = Array.from({ length: 28 }, (_, d) => `03/${String(d + 1).padStart(2, "0")}/2009`);
  const out = kinds(dobs.map((d) => ["DATE_OF_BIRTH", d]));
  assert.ok(out.every((o) => o.endsWith("/2009") && o.length === 10));
  assert.ok(!out.some((o) => dobs.includes(o)));
  assert.equal(new Set(out).size, out.length);
  assert.equal(shiftDate("12/31/2008", 1), "01/01/2008");
  assert.equal(shiftDate("2/29/2024", 365), "2/28/2024");
  assert.equal(shiftDate("25-12-09", 7), "01-01-09");
});

test("a school's stem and acronym follow the full name", () => {
  const out = kinds([["SCHOOL", "LHS"], ["SCHOOL", "Lincoln High School"]]);
  const full = out[1] as string;
  assert.ok(full.endsWith(" High School"));
  assert.ok(loadPool("school").includes(full.split(" High")[0] as string));
  assert.equal(out[0], acronym(full));
  assert.notEqual(out[0], "LHS");
});

test("name cues render as school or org", () => {
  const [school, org, person] = kinds([["NAME", "Lincoln Elementary"], ["NAME", "Riverside Chess Club"],
    ["NAME", "Lincoln High"]]) as [string, string, string];
  assert.ok(school.endsWith(" Elementary") && !school.includes("Lincoln"));
  assert.ok(loadPool("school").includes(school.split(" Elementary")[0] as string));
  assert.ok(org.endsWith(" Club") && loadPool("org").includes(org.slice(0, -" Club".length)));
  assert.ok(person.endsWith(" High"));
});

test("email rules", () => {
  const [name, school, provider] = kinds([["NAME", "Maria Lopez"], ["EMAIL", "maria.lopez09@lincoln.org"],
    ["EMAIL", "mlopez@gmail.com"]]) as [string, string, string];
  const [given, surname] = split2(name);
  assert.equal(school, `${given.toLowerCase()}.${surname.toLowerCase()}09@example.org`);
  const [local, domain] = provider.split("@") as [string, string];
  assert.equal(domain, "gmail.com");
  assert.ok(local !== "mlopez" && /^\p{L}+$/u.test(local));
});

test("a handle reuses the name map", () => {
  const [name, handle, joined] = kinds([["NAME", "Maria Lopez"], ["USERNAME", "@maria_lopez"],
    ["USERNAME", "@MariaLopez"]]) as [string, string, string];
  const [given, surname] = split2(name);
  assert.equal(handle, `@${given.toLowerCase()}_${surname.toLowerCase()}`);
  assert.equal(joined, `@${given}${surname}`);
});

test("a URL that is not a per-user shape is kept verbatim", () => {
  for (const url of [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "https://github.com/explore",
    "https://docs.google.com/document/d/1AbCdEf/edit#heading=h.x1",
    "https://en.wikipedia.org/wiki/Frida_Kahlo", "instagram.com/p/C1x2y3z4", "youtube.com/watch",
    "https://twitter.com/search?q=lopezia", "https://example.com:8080/user/mar", "https://lopezia.org/marial",
  ]) {
    assert.equal(kinds([["NAME", "Maria Lopez"], ["URL", url]])[1], url);
  }
});

test("an off-list URL takes the prose name map", () => {
  const [name, ...urls] = kinds([
    ["NAME", "Maria Lopez"],
    ["URL", "https://lincolnhs.org/staff/lopez"],
    ["URL", "https://marialopez.net/about/maria"],
    ["URL", "https://en.wikipedia.org/wiki/Maria_Lopez#early-life"],
    ["URL", "https://twitter.com/search?q=Lopez&src=typed"],
    ["URL", "https://example.com:8080/user/maria"],
    ["URL", "https://lopez.com"],
  ]);
  const [given, surname] = split2(name as string);
  const [g, s] = [given.toLowerCase(), surname.toLowerCase()];
  assert.deepEqual(urls, [
    `https://lincolnhs.org/staff/${s}`,
    `https://${g}${s}.net/about/${g}`,
    `https://en.wikipedia.org/wiki/${given}_${surname}#early-life`,
    `https://twitter.com/search?q=${surname}&src=typed`,
    `https://example.com:8080/user/${g}`,
    `https://${s}.com`,
  ]);
});

test("an off-list URL ignores names first met outside the prose", () => {
  const first = kinds([["NAME", "Maria Lopez"], ["USERNAME", "@zebediah"], ["URL", "https://site.org/zebediah"]]);
  const second = kinds([["NAME", "Maria Lopez"], ["URL", "https://site.org/zebediah"], ["USERNAME", "@zebediah"]]);
  assert.equal(first[2], "https://site.org/zebediah");
  assert.equal(second[1], "https://site.org/zebediah");
});

test("per-user URL shapes change only the handle or id", () => {
  const [name, insta, yt, gh, fb, x, sub, chan] = kinds([
    ["NAME", "Maria Lopez"],
    ["URL", "instagram.com/marialopez2009"],
    ["URL", "https://www.youtube.com/@MariaLopez/videos"],
    ["URL", "https://github.com/mlopez/essay-repo/blob/main/README.md#intro"],
    ["URL", "https://www.facebook.com/profile.php?id=100012345678&ref=bm"],
    ["URL", "https://x.com/maria_lopez/status/1234567890"],
    ["URL", "https://maria.substack.com/p/my-essay"],
    ["URL", "https://www.youtube.com/channel/UCabc123XYZ"],
  ]) as string[] as [string, string, string, string, string, string, string, string];
  const [given, surname] = split2(name);
  assert.ok(insta.startsWith(`instagram.com/${given.toLowerCase()}${surname.toLowerCase()}`));
  assert.ok(!insta.includes("2009") && /\d{4}$/.test(insta));
  assert.equal(yt, `https://www.youtube.com/@${given}${surname}/videos`);
  assert.ok(gh.startsWith("https://github.com/") && !gh.includes("mlopez"));
  assert.ok(gh.endsWith("/essay-repo/blob/main/README.md#intro"));
  assert.ok(fb.startsWith("https://www.facebook.com/profile.php?id=") && fb.endsWith("&ref=bm"));
  const fbId = (fb.split("id=")[1] as string).split("&")[0] as string;
  assert.ok(fbId !== "100012345678" && /^\d{12}$/.test(fbId));
  assert.ok(x.startsWith(`https://x.com/${given.toLowerCase()}_${surname.toLowerCase()}/status/`));
  assert.ok(!x.includes("1234567890") && (x.split("/").pop() as string).length === 10);
  assert.ok(sub.endsWith(".substack.com/p/my-essay") && !sub.startsWith("https://maria."));
  const chanId = chan.split("/").pop() as string;
  assert.ok(chanId !== "UCabc123XYZ" && chanId.length === "UCabc123XYZ".length);
});

test("a site page in a handle slot is not a handle", () => {
  for (const url of [
    "https://instagram.com/explore/tags/art", "https://github.com/orgs",
    "https://www.facebook.com/groups/12345", "https://twitter.com/home",
    "https://www.tiktok.com/discover", "https://www.facebook.com/profile.php",
  ]) {
    assert.equal(kinds([["NAME", "Maria Lopez"], ["URL", url]])[1], url);
  }
});

test("URL shapes are well formed", () => {
  for (const [host, pattern] of URL_SHAPES) {
    assert.ok(host === host.toLowerCase() && !host.startsWith("www.") && !host.startsWith("m."));
    const [path, query = ""] = pattern.split("?") as [string, string?];
    assert.ok(path.startsWith("/"));
    assert.ok(path.split("/").some((s) => ["{h}", "@{h}", "{i}"].includes(s)) || query);
    assert.ok(!query || query.endsWith("={i}"));
  }
});

test("an address keeps its shape", () => {
  const parts = one("ADDRESS", "1234 N Maple Ave Apt 5B").split(" ");
  const [number, direction] = parts as [string, string];
  const [suffix, apt, unit] = parts.slice(-3) as [string, string, string];
  const street = parts.slice(2, -3);
  assert.ok(number.length === 4 && number[0] !== "0" && number !== "1234");
  assert.deepEqual([direction, suffix, apt], ["N", "Ave", "Apt"]);
  assert.ok(loadPool("street").includes(street.join(" ")) && street.join(" ") !== "Maple");
  assert.ok(unit.length === 2 && /\d/.test(unit[0] as string) && /[A-Z]/.test(unit[1] as string));
});

test("a plural possessive with its singular present", () => {
  const out = names(["Smith", "Smiths'", "James'"], { document: "x", seed: 0 });
  const smith = out["Smith"] as string;
  const plural = smith + (/(s|x|z|ch|sh)$/.test(smith.toLowerCase()) ? "es" : "s");
  assert.equal(out["Smiths'"], `${plural}'`);
  assert.ok(/(s'|'s)$/.test(out["James'"] as string) && !(out["James'"] as string).endsWith("s's"));
});

test("a plural possessive without its singular is read singular (known gap, pinned)", () => {
  const out = names(["Smiths'"], { document: "x", seed: 0 })["Smiths'"] as string;
  const standIn = out.replace(/['s]+$/, "");
  assert.ok(GIVEN_POOL.includes(standIn) || SURNAME_POOL.includes(standIn));
  assert.equal(out, standIn + (standIn.endsWith("s") ? "'" : "'s"));
});

test("all caps and title case follow the original", () => {
  const out = kinds([["NAME", "MARIA"], ["NAME", "Maria"], ["SCHOOL", "LINCOLN HIGH SCHOOL"],
    ["SCHOOL", "Lincoln High School"]]);
  assert.equal(out[0], (out[1] as string).toUpperCase());
  assert.notEqual(out[0], out[1]);
  assert.equal(out[2], (out[3] as string).toUpperCase());
});

test("identity words are never handed out", () => {
  const [given, surname] = split2(names(["Maria Lopez"], { document: "x", seed: 0 })["Maria Lopez"] as string);
  const guarded = names(["Maria Lopez"], { document: "x", seed: 0, identity: [given, `${surname} Academy`] });
  const words = (guarded["Maria Lopez"] as string).split(" ");
  assert.ok(!words.includes(given) && !words.includes(surname));
});

test("the redactor's identity is off-limits to the stand-ins", () => {
  const free = redactIt(ESSAY, "adlib");
  const [given] = split2(free.adlibMap?.get("Maria Lopez") as string);
  const guarded = redactWithReport(ESSAY, { firstName: given, lastName: "", schoolName: "" }, { render: "adlib" });
  assert.ok(!(guarded.adlibMap?.get("Maria Lopez") as string).split(" ").includes(given));
});

test("restore round trip, and a lone token", () => {
  const result = redactIt(ESSAY, "adlib");
  const tokens = result.adlibTokens as Map<string, string>;
  assert.equal(restoreAdlib(result.text, result.restoreMap, tokens), ESSAY);
  const [given] = split2(result.adlibMap?.get("Maria Lopez") as string);
  assert.equal(restoreAdlib(`${given} wrote well.`, result.restoreMap, tokens), "Maria wrote well.");
  assert.equal(restoreAdlib(`${given.toUpperCase()}!`, {}, tokens), "MARIA!");
});

test("offsets count code points above the Basic Multilingual Plane", () => {
  const text = "😀 Maria Lopez met 😀 Maria.";
  const result = redactIt(text, "adlib");
  const chars = Array.from(result.text);
  for (const { span } of result.adlibSpans ?? []) {
    assert.ok(result.restoreMap.has(chars.slice(span.newStart, span.newEnd).join("")));
  }
});

test("the invent oracles are optional", () => {
  // "none" in the conformance spec: no stop words, no oracles.
  const out = invent([["NAME", "Maria Lopez"]], { document: "x" });
  assert.equal(out.standIns.length, 1);
  assert.equal(out.tokens.size, 2);
});
