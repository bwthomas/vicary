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

**What is still open.** The nine gates in [`conformance/gates.json`](../conformance)
are unmeasured by this port — the scoreboard says so on every run. Four of them
need corpus or census data no package ships. A green suite here means the 52
frames match; it does not mean the gate set is clear.
