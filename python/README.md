# vicary (Python)

[![ci](https://github.com/bwthomas/vicary/actions/workflows/ci.yml/badge.svg)](https://github.com/bwthomas/vicary/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/vicary.svg)](https://pypi.org/project/vicary/)
[![Python](https://img.shields.io/pypi/pyversions/vicary.svg)](https://pypi.org/project/vicary/)
[![License](https://img.shields.io/pypi/l/vicary.svg)](LICENSE)

Scrub personal names out of student compositions. Offline, no model, no network,
no per-request cost.

Structured identifiers — email, phone, SSN, card numbers — are a solved regex
exercise. Names are not, because in English prose a classmate and a public figure
are the same object: two capitalised words.

```
My cousin Terrence Okonkwo came over            -> redact
My inspiration, Vincent van Gogh, painted ...   -> keep
```

The separation is a lookup: a 2.1 MB gazetteer of public figures, places,
published works and fictional characters, built from Wikidata, the US Census
surname list and SSA birth counts, combined with evidence about how the writer
uses each name elsewhere in the same document.

This is the Python front door, and it is also the **reference implementation** —
the Ruby and TypeScript ports are gated on reproducing its output byte for byte,
placeholder numbering included. How the detector works, where the asset comes
from, how it reads the writer's capitalisation, what was measured and what it
deliberately does not do are all in the
[project README](https://github.com/bwthomas/vicary#readme), which describes all
three front doors rather than being restated in each.

## Install

```sh
pip install vicary                    # the offline detector, stdlib only
pip install 'vicary[bedrock]'         # adds the AWS Bedrock Guardrail arm
pip install 'vicary[dev]'             # tests, lint, type-check
```

Python 3.11+. The default install has **no runtime dependencies**.

## Use

```python
from vicary import StudentIdentity, build_redactor_if_enabled

redactor = build_redactor_if_enabled(
    identity=StudentIdentity(first_name="Marisol", last_name="Okonkwo"),
)
if redactor:                                  # None when configured off
    result = redactor.redact_inbound(essay_text)
    scored = my_pipeline.score(result.text)   # the model never sees a name
    feedback = redactor.redact_outbound(scored.feedback)
```

`build_redactor_if_enabled` returns `None` unless redaction is turned on, so
wiring it in changes nothing until you configure it. Pass the student's own
identity: every reference arm interpolates those strings, so omitting them
measures a different system and misses the easiest spans in any composition.

Redaction is reversible: `result.restore_map` maps each placeholder back to the
span it replaced, for a host that needs to show a student their own words.

`VICARY_REDACTION` selects the mode — `off` (the default), `local` (recommended),
`stub` or `guardrail` — and `VICARY_NAME_DETECTION` decides how hard `local` looks
for names it was not handed. The full variable table is in the
[project README](https://github.com/bwthomas/vicary#configuration); all of them
resolve in `vicary/config.py`.

## Checking it

```sh
pytest                        # everything
pytest -m "not gates"         # fast unit tests only
pytest -m gates -s            # the nine gates, with their numbers
vicary-eval --frames          # per-frame scoring table, no corpus needed
vicary-assets show            # tier counts, cut date, provenance
vicary-assets verify          # checksum the installed asset against the manifest
```

Five of the nine gates need nothing an operator supplies and hold on every CI
run — held-out recall, KEEP precision, round-trip restorability, unaccounted
invariant violations and asset entries. A sixth — bare-surname exposure — is
measured once `VICARY_EVAL_CENSUS_CSV` points at the US Census surname file,
which this package reads as either the distributed `.zip` or the extracted
`.csv`. The remaining three need an essay corpus you supply, and print
`NOT MEASURED` rather than being reduced out of the denominator: **six of nine
held is a different statement from nine of nine.** Point
`VICARY_EVAL_CORPUS_DIR` at your own copy to measure those. Numbers and bars:
[Measurement](https://github.com/bwthomas/vicary#measurement).

**No essay corpus ships with this package, and none is redistributed by it.**

## Licence

MIT — see [`LICENSE`](LICENSE).
