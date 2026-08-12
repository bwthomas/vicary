# The tools suite

Everything in this repository that is **not one of the three redactors**.

`python/`, `typescript/` and `ruby/` are front doors: three implementations of
the same detector, which is why their suites are held to the same coverage and
compared against each other. The machinery underneath them is a different thing
with a different failure mode, and it used to live in `python/tests/` purely
because it is written in Python. That made the Python front door look like it had
twice the coverage of the other two when most of the difference was this — 435 of
its 773 collected tests were these files.

## What is in here

| file | tests | what it covers |
|---|---|---|
| `tests/test_fixture.py` | 288 | The shared PII fixture and its invariant checker — `vicary.eval.fixture` |
| `tests/test_gazetteer_build.py` | 100 | The gazetteer builder that produces the shipped asset — `vicary_build.gazetteer` |
| `tests/test_demonyms_and_carryover.py` | 20 | Demonym derivation and tier carry-over in the build |
| `tests/test_conformance.py` | 13 | The generator behind `conformance/*.json`, the spec all three ports read |
| `tests/test_overfire.py` | 12 | The over-fire measurement harness — `vicary.eval.overfire` |

`asset/tests/` (27 tests) is the fourth member of this suite and stays where it
is: `vicary_build` is an installable package, and Python's convention is for a
package's tests to sit beside it. `just tools` runs both directories; so does
the `tools` CI job.

## Why the split matters beyond bookkeeping

A break in here is not a break in the detector, and the two want different
responses. If `vicary_build.gazetteer` regresses, no shipped redactor changed —
the *next asset build* would be wrong. If `vicary.eval.fixture` regresses, no
redactor changed either, but every number all three ports report is suspect,
because they are all scored against that fixture. Neither is "the Python package
is broken", which is what a failure in `python/tests/` means and what a reader
scanning a checks list will assume.

The conformance generator is the sharpest case. It emits the spec that
TypeScript and Ruby check themselves against, so a defect here fails **all
three** ports or, worse, passes all three against a spec that no longer describes
anything. Filing that under the Python front door named the wrong culprit.

## Running it

```sh
just tools                       # both directories, from the repository root
pytest tools/tests asset/tests   # the same thing by hand
```

Both packages must be installed first — `pip install -e ./python -e ./asset`, or
`just py-setup`, which does that and vendors the asset.

## What must NOT move in here

Anything that answers "does this port redact correctly". `test_gates.py` stays in
`python/tests/` even though it consumes the eval harness heavily, because the
question it asks is about the Python redactor's measured behaviour, and the other
two ports ask it too — `typescript/test/gates.test.ts` and
`ruby/test/gates_test.rb` are its counterparts. A gate is a front-door claim
measured with this suite's tooling, not a claim about the tooling.
