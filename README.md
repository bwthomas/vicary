# vicary

Scrub personal names out of student compositions. Offline, no model, no network,
no per-request cost.

Structured identifiers — email, phone, SSN, card numbers — are a solved regex
exercise. Names are not, because in English prose a classmate and a public figure
are the same object: two capitalised words.

```
My cousin Terrence Okonkwo came over            -> redact
My inspiration, Vincent van Gogh, painted ...   -> keep
```

No syntactic feature separates those, so the separation has to be a lookup.
vicary ships one: a 2.1 MB gazetteer of public figures, places, published works
and fictional characters, built from Wikidata and the US Census surname list,
consulted by candidate *shape*, and combined with evidence about how the writer
uses each name elsewhere in the same document.

> The name is the root of *vicarious*. Why that is the right name for this is
> worth writing down, and will be, in a later revision of this file.

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
wiring it in changes nothing until you configure it. Redaction is reversible:
`result.restore_map` maps each placeholder back to the span it replaced, for a
host that needs to show a student their own words.

### Modes

| mode | what it does | cost |
|---|---|---|
| `off` | nothing. The default. | — |
| `local` | offline regex + name candidates + the gazetteer. **Recommended.** | free |
| `stub` | regex-only, structured identifiers, cannot mask names | free |
| `guardrail` | AWS Bedrock `ApplyGuardrail`, managed entity detection | billed per call |

`stub` exists to exercise a host's whole redaction code path without spend; it is
a wiring test, never a privacy control. `guardrail` is mostly here as the external
baseline the eval scores `local` against — a library whose only benchmark is
itself has no benchmark.

## Configuration

Every variable vicary reads, resolved in `vicary/config.py`:

| variable | meaning |
|---|---|
| `VICARY_REDACTION` | mode: `off` / `local` / `stub` / `guardrail` |
| `VICARY_DEPLOY_ENV` | environment name; a production one defaults the mode to `local` |
| `VICARY_ASSET_PATH` | load a different gazetteer asset (file or directory) |
| `VICARY_BEDROCK_GUARDRAIL_ID` | Guardrail identifier, `guardrail` mode only |
| `VICARY_BEDROCK_GUARDRAIL_VERSION` | Guardrail version, default `DRAFT` |
| `VICARY_BEDROCK_GUARDRAIL_REGION` | Guardrail region; no default, on purpose |
| `VICARY_EVAL_CORPUS_TSV` | eval corpus path (see *Measurement*) |
| `VICARY_EVAL_CORPUS_DIR` | directory holding `training_set_rel3.tsv` |
| `VICARY_EVAL_CENSUS_CSV` | local copy of the US Census surname file, for the false-positive control |

`ENVIRONMENT` is honoured as a host convention where `VICARY_DEPLOY_ENV` is
unset. The earlier `GRADER_*` spellings of these are still read, at lower
precedence, with a one-time deprecation warning.

A host whose production environment has a local name registers it rather than
patching a literal:

```python
from vicary import config
config.add_production_alias("acme-prod")
```

## The data asset

One file, `vicary/data/notability.txt.gz`, holding five independent tiers:

| tier | entries | answers |
|---|---|---|
| `full` | 294,995 | full name of a public figure |
| `short` | 1,229 | bare surname iconic enough to stand alone |
| `place` | 25,824 | public place or landmark (settlements excluded — a town name is where a student lives) |
| `given` | 10,588 | common given name — a **redact** signal, not a keep |
| `title` | 38,017 | published work or fictional character |

A tier is a lookup, and a lookup answers about a *string*, not about the person
in front of you. 578 keys in `title` are a common given name beside an ordinary
US surname — "Alice Adams" is a 1921 novel and also somebody's neighbour — and no
sitelink floor separates them from the curriculum, because Atticus Finch sits at
17 sitelinks and the hole "Alice Adams" at 24. So a keep is overridable by the
sentence: a **first-person relation attached to the name** — immediately before
it, or in the appositive immediately after — beats a title-tier keep.

```
My neighbor Alice Adams walked me to the bus stop  -> redact
Atticus Finch is the kind of father who ...        -> keep  (relation, not the writer's)
I read Harry Potter with my little brother         -> keep  (first person, not attached)
```

Both halves are required, and the guards say why: characters are *described by*
their relations, so a bare relation cue in the window refuses six of seven
curriculum characters, and first person alone redacts a book whenever a student
says who they read it with. On 27 un-scrubbed student documents the override
fires 0 times; on the 578 name-shaped keys it recovers 508.

It ships as package data, and `data/MANIFEST.json` records its SHA-256, byte
count, tier counts, cut date, upstream sources, and the minimum vicary version
that can read it. `load()` checks all of that and **raises rather than
degrading**: an empty gazetteer would mask every public figure in every essay,
which is privacy-safe, product-hostile, and indistinguishable from
over-aggressive tuning until somebody notices months later.

```sh
vicary-assets show      # tier counts, cut date, provenance, which file is loaded
vicary-assets verify    # checksum the installed asset against the manifest
vicary-assets fetch     # rebuild from Wikidata + Census, rewrite the manifest
```

`fetch` reaches the network and takes a while. Both endpoints are donated
infrastructure; a deployment doing large rebuilds should set
`vicary.build.gazetteer.USER_AGENT_SUFFIX` to a contact address.

## Measurement

The gates live with the library, in `tests/test_gates.py`, and are ordinary
pytest tests with a bar. Each prints the number it measured.

```sh
pytest                        # everything
pytest -m "not gates"         # fast unit tests only
pytest -m gates -s            # the gates, with their numbers
vicary-eval --frames          # per-frame scoring table, no corpus needed
```

Current numbers, fixture `2026-08-06.1`, arm `local-gazetteer-lowercase`:

| gate | bar | measured |
|---|---|---|
| held-out recall | ≥ 100% | 100% |
| KEEP precision | ≥ 100% | 100% |
| round-trip restorability | ≥ 100% | 100% |
| unaccounted invariant violations | 0 | 0 |
| over-firing on real prose | ≤ 1.20 spans/essay | 1.20 |
| bare-surname Census exposure | ≤ 1.5% | 1.47% |
| latency p95 (essay-length) | ≤ 10 ms | 3.4 ms |

Recall cannot be measured on a pre-anonymized corpus — ASAP set-8 already
replaced every name with `@PERSON1`-style tokens before publication, so a
detector that does nothing scores perfectly. The harness therefore injects ground
truth whose literals it knows, from `vicary/eval/fixture.py`, and reports the
**held-out** frames separately: once a detector has been tuned against a fixture,
only the held-out half of its recall is honest.

Three known invariant violations are listed, with reasons, in
`ACCEPTED_VIOLATIONS`. The gate is that no *unlisted* violation appears, and a
second test fails if a listed one stops occurring — a stale exemption would
shelter the next real defect of the same shape.

Neither the essay corpus nor the Census surname file is packaged: one is licensed
third-party data, the other is 3 MB the redaction path never reads. Gates that
need them skip, and the report names what it could not measure, so a partial run
cannot read as a clean sweep.

```sh
export VICARY_EVAL_CORPUS_DIR=/path/to/asap-aes        # training_set_rel3.tsv
export VICARY_EVAL_CENSUS_CSV=/path/to/names.zip       # census.gov 2010 surnames
pytest -m gates -s
```

## What it does not do

* It is not a general PII scanner. It is tuned for first-person student prose,
  and its bias — over-redact rather than leak — assumes a downstream model for
  which a placeholder token is ordinary input.
* It does not detect names it has no evidence for. A private surname written
  lower-case throughout, or a bare surname in a document that also names a famous
  bearer of it, are known and documented misses rather than bugs.
* The relation override reaches the `title` tier only. 66 name-shaped keys
  resolve as real people in `full` instead — "My best friend Alan Ford" still
  keeps — because overriding that tier would also redact "my hero Abraham
  Lincoln", and which of those costs more has not been measured yet.
* It makes no network call and creates no cloud resource unless you choose
  `guardrail` mode.
