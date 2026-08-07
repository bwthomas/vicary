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
| `VICARY_NAME_DETECTION` | how hard `local` looks for names it was not handed: `identity` / `gazetteer` / `gazetteer-lowercase` |
| `VICARY_NAME_DETECTION_OUTBOUND` | the same dial for the outbound pass. Unset, it inherits the inbound one |
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

One file, `vicary/data/notability.txt.gz`, holding six independent tiers:

| tier | entries | answers |
|---|---|---|
| `full` | 295,049 | full name of a public figure |
| `short` | 1,229 | bare surname iconic enough to stand alone |
| `place` | 25,444 | public place or landmark (settlements excluded — a town name is where a student lives) |
| `given` | 10,589 | common given name — a **redact** signal, not a keep |
| `title` | 38,024 | published work or fictional character |
| `demonym` | 1,044 | nationality or regional adjective — `Cuban`, `Nigerian` |

`demonym` is the only tier with no notability evidence behind it: it is a keep
granted to a bare token for being a *word*. So it is subtracted harder than any
other — anything already in `given` is dropped (a common first name is evidence
of a person), as is any surname borne by 10,000+ Americans. That bar is 2.5×
stricter than the short tier's and `Horner` is why: a demonym of Horn and 23,881
Americans' surname, which would otherwise stop a coach named Horner redacting.
13 of 1,057 demonyms are dropped; `English`, `Welsh`, `Thai`, `French`, `German`
and `Roman` still over-fire, which is the direction this tier may fail in.

A tier is a lookup, and a lookup answers about a *string*, not about the person
in front of you. **578 keys in `title` and 33,682 in `full`** are a common given
name beside an ordinary US surname — "Alice Adams" is a 1921 novel, "Alan Ford" a
footballer, and both are also somebody's neighbour. No threshold separates them
from the people students write about (Atticus Finch sits at 17 sitelinks, the
hole "Alice Adams" at 24), so a keep is overridable by the sentence: a
**first-person relation attached to the name** — immediately before it, or in the
appositive immediately after — beats a `title` or `full` keep.

```
My neighbor Alice Adams walked me to the bus stop  -> redact
Atticus Finch is the kind of father who ...        -> keep  (relation, not the writer's)
I read Harry Potter with my little brother         -> keep  (first person, not attached)
```

Both halves are required, and the guards say why: characters are *described by*
their relations, so a bare relation cue in the window refuses six of seven
curriculum characters, and first person alone redacts a book whenever a student
says who they read it with. On 27 un-scrubbed student documents the override
fires 0 times; it recovers 508 of the 578 name-shaped title keys and 33,182 of
the 33,269 full-name ones.

What is *not* a relation, and this is the load-bearing half: **admiration
invocations**. "My hero Abraham Lincoln", "my muse Joan Jett", "my inspiration
Vincent van Gogh", "my role model Rosa Parks" all keep, because *hero*, *muse*,
*inspiration* and *role model* attach to a public figure as readily as to a
relative and are therefore evidence of nothing. That is why the cue list is
closed and hand-written rather than "any noun between *my* and the name", and it
is pinned by a held-out frame.

### Carrying a keep across the two passes

Feedback about a memoir by Narciso Rodriguez reads *"introducing who Narciso
is"*. The essay writes the full name and the gazetteer keeps it; the feedback
writes only the first name, which is in the `given` tier — a **redact** signal —
so a student read `introducing who {NAME} is` about the author they had just
written about.

No lookup fixes that. The `given` tier is *built from* the first tokens of the
full tier, so every entry in it heads some notable full name, and Narciso
Rodriguez sits far below the short tier's floor. The evidence has to be
per-document, and the document holding it is the **essay**, not the feedback. So
`redact_inbound` records the bare tokens of the notable full names it kept, and
`redact_outbound` treats them as topical:

```python
redactor.redact_inbound(essay)        # records: {"narciso", "rodriguez"}
redactor.redact_outbound(feedback)    # "Narciso" and "Narciso's" now survive
```

This is safe because **outbound text is generated from inbound-redacted input**:
a classmate named Narciso was masked on the way in, so the model never saw the
token and cannot write it back. That is also the condition — a host redacting
outbound text from some *other* source must pass `carry_notable_keeps=False`.
Two keeps are refused anyway: tokens of the student's own name (identity masking
is exact-match, so it never enters the notable set), and any token a *second,
private* full name in the same essay also claims.

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

## How it reads the writer's capitalisation

Capitalisation is the cheapest evidence in the document and the easiest to
over-trust. A writer who marks proper nouns with capitals has told us something
about every *lowercase* token — probably not a name. A writer who does not has
told us nothing, and the given-name tier is the only handle left. So the detector
classifies the document once, into one of four states, and every rule that weighs
case reads the same verdict:

| state | the writer | a lowercase seed then needs |
|---|---|---|
| `consistent` | marks proper nouns, keeps sentence capitals | the same word capitalised elsewhere |
| `inconsistent` | does both — marks some, drops some | the same word capitalised elsewhere |
| `lowercase` | drops capitals and marks nothing | nothing; the given-name tier stands alone |
| `silent` | no proper nouns, no dropped capitals | the same word capitalised elsewhere |

`inconsistent` is why this is not a boolean. On 27 un-scrubbed student documents
**7 satisfied both of the predicates this replaced** — "capitalises its proper
nouns" *and* "does not keep standard capitalisation" — so the treatment they got
depended on which predicate a call site happened to read. Neither
document-level treatment fits them: suppressing the lowercase route loses the
names they wrote lower-case, and opening it wide fires on ordinary words. They
get no document-level answer on purpose, and fall through to per-token evidence.

`silent` is the other half, and it is a defect that shipped. **Silence is not
consent**: a 108–290 character feedback field is ordinary prose with nothing in it
to capitalise, and reading its zero mid-sentence capitals as "this writer drops
capitals" put `tone toward`, `line makes` and `line circles` in front of students.
`tone` and `line` are both genuine given names, so the seed was legitimate and the
absent guard was the whole defect.

Two things were measured and are worth stating because they are the obvious
repairs and they do not work:

* **A rate does not fix the presence side.** The floor is a count of 2, which
  decides 6 of the 27 documents, so marks-per-1,000-characters looks like the
  obvious repair. It re-orders that band rather than separating it, and gets the
  two closest cases backwards: `141-693` marks "Powerball" twice in 3,478
  characters (0.58/1k, a real capitaliser) and `141-433` marks "The" and "There"
  in 1,144 (1.75/1k, both artefacts of a missed sentence break). What separates
  them is the *content* of the mark, which is per-token evidence.
* **A rate fixes the absence side only where there is a presence signal to weigh
  it against.** Above the floor it is right, and it stops one line wrap in sixty
  sentences libelling a writer who marks 26 proper nouns correctly. Below the
  floor it costs a held-out name: at a 1.7% drop rate one carrier essay was
  demoted to `silent`, the permissive path was withdrawn, and held-out recall went
  28/28 → 27/28 to buy one span of over-firing. Below the floor the document has
  offered one bit and it is taken as given.

## Measurement

The gates live with the library, in `tests/test_gates.py`, and are ordinary
pytest tests with a bar. Each prints the number it measured.

```sh
pytest                        # everything
pytest -m "not gates"         # fast unit tests only
pytest -m gates -s            # the gates, with their numbers
vicary-eval --frames          # per-frame scoring table, no corpus needed
```

Current numbers, fixture `2026-08-06.3`, arm `local-gazetteer-lowercase`:

| gate | bar | measured |
|---|---|---|
| held-out recall | ≥ 100% | 100% |
| KEEP precision | ≥ 100% | 100% |
| round-trip restorability | ≥ 100% | 100% |
| unaccounted invariant violations | 0 | 0 |
| over-firing on real prose | ≤ 0.72 spans/essay | 0.72 |
| bare-surname Census exposure | ≤ 1.25% | 1.20% |
| latency p95 (essay-length) | ≤ 10 ms | 3.4 ms |

Measured separately, on 14 real generated-feedback responses rather than on
essays: **over-firing on the outbound pass is 0.00 spans/response**, down from
0.21. Two of the three residual spans needed the essay to close, not a rule —
see *Carrying a keep across the two passes* below.

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
* The relation override reaches `title` and `full`, not `place`. A private
  person whose name is also a public *place* still keeps.
* 87 of the 33,269 full-tier holes survive the override, and a title span still
  shelters a bare uncommon given name ("my cousin Vinny" with no surname) —
  a single mid-sentence capital needs corroboration the given-name tier cannot
  supply for an uncommon name.
* Raising the single-token place floor to 150 dropped `Auschwitz`, `Alsace`,
  `Burgundy`, `Bohemia` and `Anatolia` from the place tier, so a bare mention of
  one over-fires. Multi-token forms are unaffected — "Auschwitz concentration
  camp" still resolves.
* It makes no network call and creates no cloud resource unless you choose
  `guardrail` mode.
