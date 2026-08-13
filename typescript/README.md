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

A sixth — bare-surname exposure — this port measures from the surname table
shipped in `conformance/census/`, reporting 1.20% of US surname bearers, the same
figure Python and Ruby report from the same table. `VICARY_EVAL_CENSUS_CSV`
overrides it with your own Census copy, and must be the **extracted**
`Names_2010Census.csv`: a `.zip` is refused by name rather than read as text,
because Node's standard library has no zip reader and a binary read parsed as CSV
yields zero rows — a *lower* exposure than the truth, and the wrong direction to
fail in silently. The shipped table is gzip, which `node:zlib` reads, so that
hazard does not arise on the default path.

Two of the remaining three — held-out recall (carrier) and over-fire on prose —
read the corpus the repository now ships in `conformance/corpora/`, so they
measure on a bare checkout with no environment set: 100% carrier recall and 8.150
over-fired spans per essay against a ≤ 8.15 bar, both identical to Python and
Ruby. `VICARY_EVAL_CORPUS_TSV` is an override for a different corpus, not a
requirement. (That over-fire figure sits on the bar in all three ports, which is
a knife-edge rather than noise — the root README says why.)

The ninth is latency, and it is no longer a p95 or a millisecond bar. It is a
ratio: this checkout against the **last release timed on the same machine**, held
to ≤ +8%. So it needs a pair record rather than a corpus, and prints `NOT
MEASURED` with the reason attached until it has one. This is the noisiest port on
that ratio — σ 1.98%, against 0.60% in Python and 0.46% in Ruby, which is why it
takes three times the rounds — and the least machine-sensitive on the absolute,
because at ~2 ms the JIT dominates the CPU.

Gates without their data print `NOT MEASURED` rather than dropping out of the
denominator: **eight of nine held is a different statement from nine of nine**,
and a badge cannot tell them apart.

The carrier essays are built from offsets recorded in `conformance/carrier.json`
rather than from a reimplementation of Python's RNG, and the suite asserts their
sha256 — so agreeing with the reference on a number means agreeing about the
redactor, not about three different inputs that happened to score alike.
