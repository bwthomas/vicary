# vicary

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
| TypeScript | `vicary` on npm | asset layer + conformance harness; detector **0 of 35** — see [`typescript/`](typescript/) |
| Ruby | `vicary` on RubyGems | asset layer + conformance harness; detector **0 of 35** — see [`ruby/`](ruby/) |

"0 of 35" is the number of masking-required fixture frames the port reproduces
byte-for-byte, printed by every `npm test` / `rake test` run. Both ports load the
identical gazetteer bytes as Python — same sha256, same seven tier counts, checked
against the manifest rather than against a copied constant — and both **raise
rather than return the text unchanged** when asked to redact, so neither can be
mistaken for a working redactor while it is not one. Their release workflows refuse
to publish until that number reaches 35 of 35.

The full narrative — modes, the data asset, how it reads the writer's
capitalisation, what was measured and what it deliberately does not do — is in
**[`python/README.md`](python/README.md)**, which is also what PyPI renders. It
moves up to this file when the second front door ships, so that one document
serves all three rather than three documents drifting.

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
conformance/   the spec, as language-neutral data: fixture frames and the gates
.github/       one CI workflow across all three; one release workflow per registry
```

Each language directory is a self-contained package: its own manifest, its own
`LICENSE`, its own README for its registry page, and its own build output (all
`.gitignore`d **anchored** — see the comment in `.gitignore` for the release this
nearly broke).

## The asset is the product; the language is the wrapper

`notability.txt.gz` is ~2.1 MB of folded Wikidata, US Census and SSA evidence
carrying a format version and a sha256 manifest. It is language-neutral, and
every front door loads the *same bytes* rather than rebuilding its own — a port
with its own gazetteer is a second detector wearing the first one's name.

It is vendored into each published package, deliberately, rather than fetched at
build time: "no network, no per-request cost" is the claim, and a build-time
fetch puts a fetch back in the story.

## Working in here

```sh
just --list          # every task
just test            # every language's suite
just gates           # the nine gates (four need data you supply — see below)
just conformance     # the shared suite, across every implementation present
```

Four of the nine gates need data that is not packaged: three need an essay corpus
you supply, one needs the US Census surname file. They **skip** when it is
absent, and the gate report prints `NOT MEASURED` for each one, so a green run
means "the corpus-free gates hold" and never "the gate set is clear". Same
discipline is required of every port; a green badge that means less than the
Python one is worse than no badge.

## Licence

MIT — see [`LICENSE`](LICENSE). No essay corpus ships with any package here, and
none is redistributed by them. Measurement data is supplied by the operator under
whatever terms its own distributor sets; this project makes no claim to those
terms and grants no rights in that data.
