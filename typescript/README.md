# vicary (TypeScript)

The npm front door. **Not published yet** — the conformance bar is met; the
release still is not cut.

The detector, the data asset and the measured numbers are described in the
[project README](https://github.com/bwthomas/vicary#readme). What lives here is a
port, and the bar it had to clear before it could be published is the shared
conformance suite in [`conformance/`](../conformance): for every fixture frame it
must produce **byte-identical output to the Python implementation, placeholder
numbering included**.

`npm run conformance` reports **36 of 36 masking-required frames** and 52 of 52
overall, against the `local-gazetteer-lowercase` arm. Run it rather than trusting
this paragraph — the scoreboard prints on every test run, and this line is a
copy of a number that moves.

```ts
import { redact } from "vicary";

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
| round-trip | >= 100 % | **100 %** (52/52 frames) |
| unaccounted violations | == 0 | **0** |
| asset entries | >= 1 | **360,793** |

The other four — held-out recall (carrier), over-fire on prose, latency p95,
bare-surname exposure — need an essay corpus or the US Census surname file, and
no package here ships either. They print `NOT MEASURED` per gate rather than
being reduced out of the denominator: **five of nine held is a different
statement from nine of nine**, and a badge cannot tell them apart. Supply
`VICARY_EVAL_CORPUS_TSV` / `VICARY_EVAL_CENSUS_CSV` and run the Python harness to
measure the rest.
