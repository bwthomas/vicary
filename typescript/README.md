# vicary (TypeScript)

The npm front door, published as
[`@bwthomas/vicary`](https://www.npmjs.com/package/@bwthomas/vicary).

**The scope is interim.** npm refuses the unscoped `vicary` as too similar to the
existing `vary` — the name is owned by nobody, so it reads as available in any
404 check and is not. An appeal is open; PyPI and RubyGems both carry the
unscoped name, so this is the one front door whose import path differs.

The detector, the data asset and the measured numbers are described in the
[project README](https://github.com/bwthomas/vicary#readme). What lives here is a
port, and the bar it had to clear before it could be published is the shared
conformance suite in [`conformance/`](../conformance): for every fixture frame it
must produce **byte-identical output to the Python implementation, placeholder
numbering included**.

`npm run conformance` reports **38 of 38 masking-required frames** and 54 of 54
overall, against the `local-gazetteer-lowercase` arm. Run it rather than trusting
this paragraph — the scoreboard prints on every test run, and this line is a
copy of a number that moves.

```ts
import { redact } from "@bwthomas/vicary";

redact("My cousin Vinny came over that summer and never left.", {
  firstName: "Marguerite",
  lastName: "Delacroix-Whitfield",
  schoolName: "Westfield High School",
});
// "My cousin {NAME_1} came over that summer and never left."
```

Pass the student's own identity. Every reference arm interpolates those three
strings, so omitting them measures a different system and misses the easiest
spans in any composition. `redactWithReport` returns the same bytes plus the
`restoreMap` that `restore` reads to put the originals back.

**The gates.** Of the nine in [`conformance/gates.json`](../conformance), this
port measures the five that need no operator-supplied data, and all five hold:

| gate | bar | measured |
|---|---|---|
| held-out recall | >= 100 % | **100 %** (16/16 spans) |
| KEEP precision | >= 100 % | **100 %** (21/21 spans) |
| round-trip | >= 100 % | **100 %** (54/54 frames) |
| unaccounted violations | == 0 | **0** |
| asset entries | >= 1 | **360,793** |

A sixth — bare-surname exposure — this port measures itself once you point
`VICARY_EVAL_CENSUS_CSV` at the **extracted** `Names_2010Census.csv`, reporting
1.20% of US surname bearers, the same figure Python and Ruby report from the same
file. A `.zip` is refused by name rather than read as text: Node's standard
library has no zip reader, and a binary read parsed as CSV yields zero rows —
a *lower* exposure than the truth, and the wrong direction to fail in silently.

The remaining three — held-out recall (carrier), over-fire on prose, latency p95
— need an essay corpus no package here ships, and this port measures them too
once `VICARY_EVAL_CORPUS_TSV` points at one: 100% carrier recall (29/29 held-out
REDACT spans), 0.60 over-fired spans per essay (15 across 25 essays) — both
identical to Python and Ruby — and 2.1–2.6 ms latency p95, which is this port's
own and the fastest of the three. Without a corpus they print `NOT MEASURED` per
gate rather than being reduced out of the denominator: **six of nine held is a
different statement from nine of nine**, and a badge cannot tell them apart.

The carrier essays are built from offsets recorded in `conformance/carrier.json`
rather than from a reimplementation of Python's RNG, and the suite asserts their
sha256 — so agreeing with the reference on a number means agreeing about the
redactor, not about three different inputs that happened to score alike.
