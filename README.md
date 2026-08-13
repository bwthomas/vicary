# vicary

[![ci](https://github.com/bwthomas/vicary/actions/workflows/ci.yml/badge.svg)](https://github.com/bwthomas/vicary/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/vicary.svg)](https://pypi.org/project/vicary/)
[![Gem](https://img.shields.io/gem/v/vicary.svg)](https://rubygems.org/gems/vicary)
[![License](https://img.shields.io/pypi/l/vicary.svg)](LICENSE)

Offline redaction of personal names in student compositions — one detector, three
front doors.

vicary finds the names a student writes about (classmates, teachers, relatives,
neighbours), replaces them with numbered placeholders a later pass can restore,
and leaves the public figures they are writing *about* alone. No model, no
network, no per-request cost: it is a folded gazetteer plus a candidate generator,
and it answers in single-digit milliseconds.

| front door | package | status |
|---|---|---|
| Python | [`vicary`](https://pypi.org/project/vicary/) on PyPI | **published**, 9 of 9 gates PASS — see [`python/`](python/) |
| Ruby | [`vicary`](https://rubygems.org/gems/vicary) on RubyGems | **published**, **38 of 38**, 9 of 9 gates PASS — see [`ruby/`](ruby/) |
| TypeScript | [`@bwthomas/vicary`](https://www.npmjs.com/package/@bwthomas/vicary) on npm | **published**, **38 of 38**, 9 of 9 gates PASS — scoped because npm refuses the bare `vicary` as too similar to `vary`; the appeal for it is open — see [`typescript/`](typescript/) |

All three ports measure the same nine gates now, and 9 of 9 is the CI figure — the
one gate that needs more than a checkout is latency, which needs a pair record
from the same machine. On a bare checkout without one, every port reports 8 of 9
and prints `NOT MEASURED` with the reason for the ninth.

That fraction is the number of masking-required fixture frames the port reproduces
byte-for-byte, printed by every `npm test` / `rake test` run and ratcheted by it.
Both ports reach all 38 (and 54 of 54 overall) against the
`local-gazetteer-lowercase` arm, numbering included — the detector is ported in
each, not just the structured pass. All three load the identical gazetteer bytes:
same sha256, same seven tier counts, checked against the manifest rather than
against a copied constant.

Both ports also measure **five of the nine gates** in `conformance/gates.json`
from the fixture alone — held-out recall, KEEP precision, round-trip, unaccounted
violations and asset entries — and all five hold in each, at the same values
Python reports: 16/16 held-out REDACT spans, 21/21 KEEP spans intact, 54/54
frames restoring exactly, one violation and it the accounted-for one, 360,793
asset entries. The counts are stated alongside the percentages because 100% of a
wrong denominator is also 100%.

**All three ports measure all nine on a bare checkout, with no setup at all.**
The other four gates declare a data requirement, and the repository carries both
kinds: `conformance/corpora/` ships an essay corpus, `conformance/census/` ships
the US surname table. Each port reports nine of nine — agreeing exactly on the
eight that are properties of the detector, and each reporting its own on the one
that is a property of the language. Neither file is in any published package;
`conformance/` never has been.

The `NOT MEASURED` machinery stays and is still tested, by withholding the inputs
on purpose. A gate whose data is absent is never given a value — not even 0,
which in a `<=` gate would read as the most comfortable pass on the board — and a
requirement satisfied buys only its own gate: supplied values are carried in a map
separate from the fixture-derived ones, so nothing measured from the fixture can
ever be printed under a gate that asked for a corpus.

Each port measures rather than reads: the spec carries `aligns` and `mapping` per
frame, and a port that quoted those would only be restating Python's answer back.
The span-to-placeholder mapping is recovered from the port's own output by chunk
matching, so the masker is never the witness for its own redaction.

**38 of 38 is the bar to publish, not the bar to trust.** Measured on the Ruby
port the day it landed: of eleven deliberate mutations to its candidate
generator, the frames caught one. So each port is checked at three depths — the
frames, the shared `primitives.json` corpus underneath them, and a differential
probe that runs both implementations over prose no fixture contains and diffs the
bytes. The third exists because the first two are single-line corpora, and
several rules only diverge across a newline. Both release workflows refuse to
publish until the number reaches 38 of 38 regardless.

## Why names need more than a regex

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

### Why "vicary"

Latin *vicarius*: one who stands in the place of another. It is the root of
*vicarious*, and of *vicar* — and a placeholder is exactly that, a substitute
holding a name's position in the text so the position survives when the name does
not. `{NAME_1}` is not the name deleted; it is a stand-in that keeps the sentence
a sentence, keeps two mentions of one person the same person, and keeps the way
back.

The other half of the word is the point. *Vicarious* experience is experience had
through a substitute, and that is precisely the relationship a scoring model is
put into here: it reads the essay through stand-ins, never through the student's
actual classmates and neighbours. It grades the writing and never meets the
people. That is the whole design in one word — which is why the placeholders are
numbered and the redaction is reversible. A substitute that cannot be traced back
is not standing in for anything.

## Install

```sh
pip install vicary                    # Python 3.11+, stdlib only
gem install vicary                    # Ruby 3.1+
npm install @bwthomas/vicary          # scoped: npm refuses the bare name
```

The Python package has **no runtime dependencies** by default;
`pip install 'vicary[bedrock]'` adds the AWS Bedrock Guardrail arm and
`'vicary[dev]'` the tests, lint and type-check.

## Use

Every front door takes the composition and the student's own identity, and
returns the masked bytes. Pass the identity: every reference arm interpolates
those three strings, so omitting them measures a different system and misses the
easiest spans in any composition.

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

The ports expose the same detector behind each language's ordinary shape —
[`ruby/README.md`](ruby/README.md) and
[`typescript/README.md`](typescript/README.md) show theirs. The output bytes are
identical across all three, placeholder numbering included, and that is gated
rather than asserted.

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
itself has no benchmark. `guardrail` is Python-only; the ports implement `local`
and `stub`, which are the two that need no network.

## Configuration

Every variable vicary reads, resolved in `vicary/config.py` and by each port's
equivalent:

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
| `VICARY_EVAL_CORPUS_DIR` | directory holding the corpus TSV (see *Measurement*) |
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

## The asset is the product; the language is the wrapper

`notability.txt.gz` is ~2.1 MB of folded Wikidata, US Census and SSA evidence
carrying a format version and a sha256 manifest. It is language-neutral, and
every front door loads the *same bytes* rather than rebuilding its own — a port
with its own gazetteer is a second detector wearing the first one's name.

It is vendored into each published package, deliberately, rather than fetched at
build time: "no network, no per-request cost" is the claim, and a build-time
fetch puts a fetch back in the story.

The 421-word stoplist that decides what becomes a name candidate at all is shared
on the same terms, for a sharper reason: a word list transliterated by hand into a
second language diverges silently, and the divergence shows up as prose corruption
in one language and not the others — which no parity check on *masked output* would
catch, because a missing stop word changes what gets masked in essays nobody put in
a fixture.

### The seven tiers

One file, `vicary/data/notability.txt.gz`, holding seven independent tiers:

| tier | entries | answers |
|---|---|---|
| `full` | 295,049 | full name of a public figure |
| `short` | 1,229 | bare surname iconic enough to stand alone |
| `place` | 25,444 | public place or landmark (settlements excluded — a town name is where a student lives) |
| `given` | 8,138 | common given name — a **redact** signal, not a keep |
| `title` | 38,024 | published work or fictional character |
| `demonym` | 1,044 | nationality or regional adjective — `Cuban`, `Nigerian` |
| `settlement` | 23,277 | town or city — a **typing** signal, neither keep nor redact |

Five of the seven grant a keep. `given` and `settlement` do not, and they are the
two the asset's `entry_count` leaves out for that reason.

`given` is built from **SSA US birth counts**, all years and both sexes, at
1,800 births or more — not from the first tokens of notable people's names, which
is what it was until 2026-08-07. That was an equity defect rather than a tuning
miss: it answered "was a famous person called this", and the misses skewed toward
Black and South Asian given names — `Deshawn`, `Ayaan` and `Meisha` absent while
`Marguerite`, `Terrence`, `Priya` and `Marisol` were present. Births are a
*dense* signal where a bearer count is sparse, so the tier got better on both
legs at once: it closed `Deshawn` (visible recall 96.2% → 100%) **and** over-firing
fell 0.72 → 0.60 spans/essay. No US child is registered as `Like` or `Pride` at
any threshold, which is why the ordinary-word collision that capped the old
approach does not exist here.

`settlement` answers a question the others never ask: not *should this be
masked*, but *which placeholder should it get*. A student's hometown must
redact — which is exactly why `place` excludes settlements in its SPARQL — and
before this tier existed it redacted as `{NAME}`, so a host echoing the
placeholder wrote "great job describing your trip to {NAME}". The tier is built
from the names that exclusion throws away, and it can only relabel a span that
was already going to be masked: it cannot keep anything and cannot suppress a
mask.

Half of American town names are somebody's surname, because the towns were named
after the people, so a settlement that is also a common given name or a surname
borne by 10,000+ Americans is dropped and falls back to `{NAME}` — the
conservative type, since "your friend {LOCATION}" is a worse thing for a student
to read than "your trip to {NAME}". That drops `Jackson`, `Austin`, `Houston`,
`Cleveland`, `Madison`, `Brooklyn` and `Aurora`; `Akron`, `Westfield`,
`Springfield` and `Phoenix` survive.

`demonym` is the only tier with no notability evidence behind it: it is a keep
granted to a bare token for being a *word*. So it is subtracted harder than any
other — anything already in `given` is dropped (a common first name is evidence
of a person), as is any surname borne by 10,000+ Americans. That bar is 2.5×
stricter than the short tier's and `Horner` is why: a demonym of Horn and 23,881
Americans' surname, which would otherwise stop a coach named Horner redacting.
13 of 1,057 demonyms are dropped; `English`, `Welsh`, `Thai`, `French`, `German`
and `Roman` still over-fire, which is the direction this tier may fail in.

### A lookup answers about a string, not about a person

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

No lookup fixes that. `Narciso` is in the `given` tier because US children are
named Narciso, not because the tier knows about Narciso Rodriguez — who sits far
below the short tier's floor — so nothing in the asset connects the bare first
name to the designer. The evidence has to be per-document, and the document
holding it is the **essay**, not the feedback. So `redact_inbound` records the
bare tokens of the notable full names it kept, and `redact_outbound` treats them
as topical:

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

### Where the asset comes from

It ships as package data — vendored from the repository's `asset/`, which no front
door owns — and `data/MANIFEST.json` records its SHA-256, byte count, tier counts,
cut date, upstream sources, and the minimum vicary version that can read it.
`load()` checks all of that and **raises rather than degrading**: an empty
gazetteer would mask every public figure in every essay, which is privacy-safe,
product-hostile, and indistinguishable from over-aggressive tuning until somebody
notices months later.

```sh
vicary-assets show      # tier counts, cut date, provenance, which file is loaded
vicary-assets verify    # checksum the installed asset against the manifest
```

There is deliberately no `fetch`. Rebuilding the gazetteer is the repository's tooling,
not this package: installing a redaction library should not install a
SPARQL client, and three front doors that each rebuild their own gazetteer are three
detectors sharing a name. From a checkout it is `just asset-fetch`; it reaches the
network and takes a while, and both endpoints are donated infrastructure, so a
deployment doing large rebuilds should set the builder's `USER_AGENT_SUFFIX` to a
contact address.

## Order is the contract

The detector makes two passes over one document, and their order changes the
output bytes even where it changes no verdict:

1. **The structured and identity pass.** Email and URL first, because a
   school-issued address *is* the writer's name (`first.last@district.org`) and a
   profile URL ends in a name slug — both are anchored on structure a name cannot
   supply, so claiming them first cannot cost a surname in prose, while leaving
   them until after identity interpolation shredded them into
   `{NAME_2}.{NAME_1}{USERNAME_1}.k12.oh.us`. Then the student's own name, school
   and acronym. Then every remaining syntactic entity: SSN, IP, phone, street
   address, date of birth, `@handle`, payment card behind a Luhn gate, ZIP and age.
2. **Candidate generation.** The third-party names nothing hands over — the
   classmate, the teacher, the relative, the neighbour. High recall by
   construction, filtered by the offline notability oracle so the public figures a
   student writes *about* survive. It runs last, because a broad capitalised-word
   match run early would swallow the first token of an address, and a name
   half-eaten by another pattern leaks the remainder.

**One minter for the whole document.** Placeholder indices follow mint order
across both passes, so `{NAME_1}` means one person from the first line to the
last. Counters are per kind, so `{EMAIL_1}` is the first email regardless of where
the email pattern sits in the order. Two minters would restart each counter and
hand the same token to two different people, which is the defect numbering exists
to remove.

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
pytest tests with a bar. Each prints the number it measured. Their bars are
language-neutral data in [`conformance/gates.json`](conformance), so a port reads
the same nine rather than a copy.

```sh
just gates                    # the nine, in every language present
pytest -m gates -s            # the Python gates, with their numbers
vicary-eval --frames          # per-frame scoring table, no corpus needed
```

**Five need nothing an operator has to supply, and every CI run measures them.**
Fixture `2026-08-11.2`, arm `local-gazetteer-lowercase`:

| gate | bar | measured |
|---|---|---|
| held-out recall | ≥ 100% | **100%** (16/16 spans) |
| KEEP precision | ≥ 100% | **100%** (21/21 spans) |
| round-trip restorability | ≥ 100% | **100%** (54/54 frames) |
| unaccounted invariant violations | 0 | **0** |
| asset entries | ≥ 1 | **360,793** |

**A sixth needs one file you fetch once, and all three ports then measure it.**
Point `VICARY_EVAL_CENSUS_CSV` at the US Census 2010 surname file and the gate
goes from `NEEDS census` to `FROM census`. Python reads the distributed `.zip` or
the extracted `.csv`; the TypeScript and Ruby ports read the `.csv` only, because
neither standard library has a zip reader and a binary read parsed as CSV yields
zero rows — which is a *lower* exposure than the truth, and the wrong direction
to fail in silently. All three refuse a file parsing to under 100,000 rows for
the same reason.

| gate | bar | measured | agreeing |
|---|---|---|---|
| bare-surname Census exposure | ≤ 1.25% | **1.20%** (3,185,816 / 265,667,228 bearers) | Python, TypeScript, Ruby — byte-identical reports |

**Three need an essay corpus no package here ships**, and all three ports measure
them once you supply one. Two are properties of the detector, so the ports agree
exactly; the third is a property of the language, so each reports its own:

| gate | bar | Python | TypeScript | Ruby |
|---|---|---|---|---|
| held-out recall (carrier) | ≥ 100% | **100%** (29/29) | **100%** (29/29) | **100%** (29/29) |
| over-firing on real prose | ≤ 0.6 spans/essay | **0.60** (15 spans) | **0.60** (15 spans) | **0.60** (15 spans) |
| latency vs last release | ≤ +8% | per port, against the last release timed on the same machine | ″ | ″ |

**The envelope, because a rate without one is not a quotable number:** the first
25 essays of ASAP-AES set 8, taken in file order so the sample is reproducible
rather than sampled; fixture frames injected at recorded offsets; arm
`local-gazetteer-lowercase`; single-threaded, no network. Each port builds
byte-identical carrier text — sha256 `78f6926f…` over the 25 injected essays,
asserted in all three suites — so an agreement between them is an agreement about
the redactor rather than a coincidence of three different inputs.

Three things that envelope makes visible and a table of percentages does not.

**The over-fire gate passes with no margin at all.** 15 over-fired spans across
25 essays is exactly 0.60 against a bar of ≤ 0.60, so one further span anywhere
in the sample reads 0.64 and fails. It is deterministic across runs and identical
in all three ports, so this is a knife-edge and not noise.

**The latency gate compares two measurements taken on the same machine, minutes
apart.** It has been three things. It read ≤ 10 ms, which is a claim about the
machine as much as the code, and it split the 0.2.3 release across three
registries: `release-gem` runs the gates and refused, `release-npm` runs them and
happened to land at 9.75 ms, and the PyPI workflow did not run them at all. It
then became a stored per-release baseline, compared only on the runner profile it
was recorded on — which failed for the same reason one level down, because
`github-ubuntu-latest` is not a machine. Thirty-six processes across six of those
runners, on identical code, spread **67% in Ruby** (6.53 ms on an Intel Xeon
6973P-C against 10.63 ms on an EPYC 7763), 26% in Python and 21% in TypeScript.
One probe run drew five CPU models from that one label, and two runners of the
*same* model still differed by 26%. Against an 8% bar, that gate red-lit `main`
on code nobody had touched.

What replaced it does not compare across machines at all. `tools/latency_pair.py`
checks the **previous release** out of this repository's history and times it and
this checkout alternately, on the machine running the gate, counterbalancing the
order each round; the gate compares those two numbers and nothing else. Every
property of the machine is common to both sides and cancels. Nothing is recorded
between releases, nothing is pinned to a runner, and the gate works on a laptop:
`just latency-pair ruby`. Without a pair it reports NOT MEASURED with the reason
attached, because one side of a comparison is not a gate.

**How much of the bar the noise uses, measured rather than inferred.** Each
port's gate statistic was run repeatedly against a fixed head and tag, so its
true value is constant and the spread is the noise:

| port | σ of the gate statistic | 95% CI | 8% bar |
|---|---|---|---|
| Python | 0.60% | 0.44–0.93% | 13.3 σ |
| Ruby | 0.46% | 0.34–0.72% | 17.2 σ |
| TypeScript | 1.98% | 1.56–2.69% | 4.0 σ |

Under the stored baseline, 8% was about *one third* of a sigma. Every mean is
indistinguishable from zero, and 24 runner allocations drawing three CPU models
say why: Ruby's **absolute** figure spreads 31.8% across those models — the axis
that killed the stored baseline — while its **ratio** spreads 0.36 pp. With 14
runner groups pooled, the between-runner variance component is not distinguishable
from zero (F = 0.93 on df 13, 14). TypeScript inverts the pattern, and that is
the reason it takes three times the rounds: at ~2 ms the JIT dominates the
machine, so its absolute figure barely notices the CPU while its ratio is the
noisiest of the three.

What the bar does **not** catch is drift. +5% per release passes every time and
compounds to +34% over six releases with nothing ever red. That is inherent to
comparing against the last release, and deliberate: this gate is for the step
change. The trend is a human's job.

**The measured figure is a pooled median, not a percentile, taken after a full
warmup pass.** At n=20 essays a p95 *is* the maximum, and a maximum moves on a
scheduling pause rather than on code. The warmup is the whole corpus rather than
one short call because TypeScript's first four essays run at about twice their
steady-state cost while V8 tiers the redaction path up — a quarter of the pooled
samples above steady state, and an estimator whose value depended on when the JIT
finished. A machine-speed calibrator was tried and dropped: normalising by it
helped Ruby, hurt TypeScript, and still left 18–25% spread between runners.

**The one-time asset load is excluded from latency, deliberately and in all three
ports.** Loading 360,793 gazetteer entries costs ~84 ms in Python and ~207 ms in
Ruby, and whichever essay runs first pays all of it — one sample large enough to
set the answer by itself. Including it made the
same code report 3.1 ms or 4.0 ms depending only on whether something earlier in
the process had touched the asset. The gate's claim is essay-length redaction
latency, not process startup.

Measured separately, on 14 real generated-feedback responses rather than on
essays: **over-firing on the outbound pass is 0.00 spans/response**, down from
0.21. Two of the three residual spans needed the essay to close, not a rule —
see *Carrying a keep across the two passes* above.

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

**No essay corpus ships with any package here, and none is redistributed by
them.** The corpus numbers above were measured against third-party essay data
obtained separately, under whatever terms its own distributor sets; this project
makes no claim to those terms and grants no rights in that data. What it ships is
the *harness* — you point it at a corpus you are entitled to use, and it measures.
The Census surname file is likewise not packaged, for the duller reason that it is
3 MB the redaction path never reads.

**How three languages inject into the same essays.** Everything about building a
carrier essay is deterministic except which sentence ends the frames land on,
which the Python reference draws from its Mersenne Twister. Reproducing that draw
would mean porting MT19937 and `random.sample` into JavaScript and Ruby — a lot
of code with nothing to do with redaction, whose failure mode is silent, since a
subtly different draw just yields different numbers. So the draw is recorded once
in `conformance/carrier.json`: per essay, its id, a digest of its text, the
frames injected and the offsets they went in at. **No essay text is in that file
and none may be** — it holds ids, digests, offsets and counts, which is what lets
it live in the repository while the corpus does not. Each port checks every essay
against its recorded digest before using an offset into it, because an offset
into the wrong text produces a plausible number rather than an error.

Like `frames.json`, the plan is an *input*: it says where to inject. What each
port then measures from the resulting text is recovered from its own output.

The harness reads a two-column TSV of `essay_id`/`essay`. Point
`VICARY_EVAL_CORPUS_TSV` at the file, or `VICARY_EVAL_CORPUS_DIR` at a directory
holding it — a directory resolves `corpus.tsv` if present, otherwise the single
`.tsv` it contains, whatever your corpus's distributor named it. A directory
holding none or several raises rather than resolving to nothing, because a
skipped gate must mean "no corpus configured" and never "configured, mis-named".

```sh
export VICARY_EVAL_CORPUS_DIR=/path/to/your/corpus     # holds one .tsv
export VICARY_EVAL_CENSUS_CSV=/path/to/names.zip       # census.gov 2010 surnames
pytest -m gates -s
```

The Census file is `names.zip` from the census.gov 2010 surnames release, public
domain and needing no credentials. The TypeScript and Ruby ports want the
`Names_2010Census.csv` extracted out of it, and refuse a `.zip` by name rather
than reading it as text:

```sh
unzip names.zip Names_2010Census.csv
export VICARY_EVAL_CENSUS_CSV=/path/to/Names_2010Census.csv
just gates                    # all three ports; an operator copy overrides
                              #   the shipped table, and the rate is the same
```

## What it does not do

* It is not a general PII scanner. It is tuned for first-person student prose,
  and its bias — over-redact rather than leak — assumes a downstream model for
  which a placeholder token is ordinary input.
* It does not detect names it has no evidence for. A private surname written
  lower-case throughout, or a bare surname in a document that also names a famous
  bearer of it, are known and documented misses rather than bugs.
* The `given` tier's births floor leaves the rarest tail out. `Meisha` has 1,048
  US births since 1880; reaching her needs a floor at or below that, which
  measures 0.80 spans/essay and fails the over-firing gate. That trade is left
  unmade rather than overlooked — it buys the rarest names at the cost of the
  tightest gate.
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
* Placeholder *types* are three — `{NAME}`, `{ORGANIZATION}`, `{LOCATION}` —
  and everything else masked as a third-party name gets `{NAME}`. A holiday, a
  brand or a pet that over-fires is typed as a person, and `Christmas` is the
  measured case: it is a US town at exactly the settlement floor, so on real
  student prose it retypes to `{LOCATION}`. Both labels are wrong; the span is a
  pre-existing over-fire that should not have been masked at all, so the tier
  relabels a defect rather than creating one.
* It makes no network call and creates no cloud resource unless you choose
  `guardrail` mode.

## Why one repository

Because the product claim is that all three implementations produce
**byte-identical output for the same input, placeholder numbering included**, and
a claim split across three repositories' CI is a claim nobody checks. One
`ci.yml` runs every language and the shared conformance suite, so parity is a
build result rather than an intention.

Numbering is where ports diverge first — it depends on iteration order over
candidate spans — and a mismatch breaks placeholder restoration across a service
boundary. That property is the whole reason to prefer this over a cloud
redaction API, so it is the property that gets gated.

## Layout

```
python/        the Python package  (src/, tests/, pyproject.toml)
typescript/    the npm package
ruby/          the gem
asset/         the shared gazetteer, and the builder that produces it
tools/         the fourth suite: everything that is not one of the three redactors
conformance/   the spec, as language-neutral data: fixture frames, gates,
               the reference's own measurements, and the parity probes
VERSION        the one number all three front doors declare
.github/       one CI workflow across all four; one release workflow per registry
```

`tools/` is the odd one out and deliberately so. `python/`, `typescript/` and
`ruby/` are *front doors* — three implementations of one detector, held to the same
coverage and diffed against each other. Everything they stand on is tooling: the
eval fixture all three are scored against, the gazetteer builder, the over-fire
harness, and the generator behind `conformance/*.json`. That used to live in
`python/tests/` purely because it is written in Python, which made the Python front
door look like it had twice the coverage of the other two — 435 of its 773
collected tests were tooling — and named Python as the culprit for breakages that
had nothing to do with it. See `tools/README.md`.

Each language directory is a self-contained package: its own manifest, its own
`LICENSE`, its own README for its registry page, and its own build output (all
`.gitignore`d **anchored** — see the comment in `.gitignore` for the release this
nearly broke). This document is the narrative for all three; a package README
covers installing and calling that one front door, and links here rather than
restating it, so there is one description of the detector instead of three that
drift.

`asset/` is deliberately none of their property. It holds the tracked gazetteer,
the language-neutral word lists, and the code that fetches them from Wikidata, the
US Census and SSA — and every front door vendors a gitignored copy from it by its
own sync step. It used to live inside the Python package, which made one of three
peers the structural owner of the shared input and shipped a SPARQL client to every
host that ran `pip install vicary`. See [`asset/README.md`](asset/README.md).

## Working in here

```sh
just --list          # every task
just test            # every language's suite
just gates           # the nine gates, all nine measured on a bare checkout
just conformance     # the shared suite, across every implementation present
just asset-sync      # vendor the shared asset into every front door present
just sync-conformance  # regenerate conformance/*.json from the Python reference
```

**A fresh checkout has no gazetteer until `just asset-sync` runs.** The vendored
copies are gitignored in all three packages, so importing the library raises rather
than answering from an empty one — which is the intended failure. `just py-setup`
does the sync for you.

**All nine gates are measured on a bare checkout, in every port.** Nothing needs
an operator any more: `persuade-20` ships in `conformance/corpora/` for the three
corpus gates, and `conformance/census/` ships the surname table for bare-surname
exposure. CI measures nine of nine, which it never did before — the census file
came from census.gov, and census.gov now answers that URL with a rejection page
under a 200 status.

The discipline that got us here stays. A gate a runner cannot reach prints
`NOT MEASURED` **by name**, never reduced out of the denominator, and each port
asserts that behaviour by withholding the inputs on purpose. A green badge that
means less than it appears to is worse than no badge.

Two overrides, both optional. `VICARY_EVAL_CORPUS_TSV` measures the ASAP-AES
corpus instead, or `VICARY_EVAL_CORPUS` names a registered one per run; two corpus
gates carry a **per-corpus bar**, so the report names the corpus it measured in its
header rather than leaving you to infer it. `VICARY_EVAL_CENSUS_CSV` points at
your own Census copy, which is how you would score against a newer release.

## Licence

MIT — see [`LICENSE`](LICENSE). No essay corpus ships inside the published
Python, npm or Ruby packages — none of the three includes `conformance/`. The
repository does ship one: twenty PERSUADE 2.0 essays under their owner's CC BY 4.0
grant, with attribution and a note on a conflicting third-party licence claim in
[`conformance/corpora/persuade-20/NOTICE`](conformance/corpora/persuade-20/NOTICE).
Any other measurement data is supplied by the operator under whatever terms its
own distributor sets; this project makes no claim to those terms and grants no
rights in that data.
