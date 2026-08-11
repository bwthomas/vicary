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
| TypeScript | `vicary` on npm | detector complete, **36 of 36**, 5 of 5 measurable gates PASS; unpublished — see [`typescript/`](typescript/) |
| Ruby | `vicary` on RubyGems | asset layer + conformance harness; detector **0 of 36** — see [`ruby/`](ruby/) |

That fraction is the number of masking-required fixture frames the port reproduces
byte-for-byte, printed by every `npm test` / `rake test` run and ratcheted by it.
TypeScript reaches all 36 (and 52 of 52 overall) against the
`local-gazetteer-lowercase` arm, numbering included — the detector is ported, not
just the structured pass. It also measures **five of the nine gates** in
`conformance/gates.json` — held-out recall, KEEP precision, round-trip,
unaccounted violations and asset entries — and all five hold, at the same values
Python reports. The other four need an essay corpus or the Census surname file
that no package here ships; the scoreboard prints `NOT MEASURED` beside each on
every run rather than letting a green suite imply a clear gate set.

Ruby is still the earlier state, and the paragraph the TypeScript port used to
share: its frames are carried by the structured pass and the identity
interpolation alone, with no frame yet carried by a *detected* name. Both ports
load the identical gazetteer bytes as Python — same sha256, same seven tier
counts, checked against the manifest rather than against a copied constant — and
Ruby still **raises rather than returning the text unchanged** when asked to
redact, so it cannot be mistaken for a working redactor while it is not one. Both
release workflows refuse to publish until the number reaches 36 of 36.

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
asset/         the shared gazetteer, and the mechanism that builds it
conformance/   the spec, as language-neutral data: fixture frames and the gates
VERSION        the one number all three front doors declare
.github/       one CI workflow across all three; one release workflow per registry
```

Each language directory is a self-contained package: its own manifest, its own
`LICENSE`, its own README for its registry page, and its own build output (all
`.gitignore`d **anchored** — see the comment in `.gitignore` for the release this
nearly broke).

`asset/` is deliberately none of their property. It holds the tracked gazetteer,
the language-neutral word lists, and the code that fetches them from Wikidata, the
US Census and SSA — and every front door vendors a gitignored copy from it by its
own sync step. It used to live inside the Python package, which made one of three
peers the structural owner of the shared input and shipped a SPARQL client to every
host that ran `pip install vicary`. See [`asset/README.md`](asset/README.md).

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

## Working in here

```sh
just --list          # every task
just test            # every language's suite
just gates           # the nine gates (four need data you supply — see below)
just conformance     # the shared suite, across every implementation present
just asset-sync      # vendor the shared asset into every front door present
```

**A fresh checkout has no gazetteer until `just asset-sync` runs.** The vendored
copies are gitignored in all three packages, so importing the library raises rather
than answering from an empty one — which is the intended failure. `just py-setup`
does the sync for you.

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
