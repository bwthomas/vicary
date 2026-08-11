# asset — the build mechanism, and what it builds

The detector is a lookup. This directory holds the lookup, and the code that
builds it from public upstreams. It is **not part of any of the three packages**,
and that is the point: all three need the same bytes, none of them should own
them, and none of them should ship the fetch machinery that produced them.

```
asset/
  data/                    built artifacts — TRACKED, canonical
    notability.txt.gz      the folded gazetteer, seven tiers
    MANIFEST.json          sha256, byte count, tier counts, format, cut date
  lexicon/                 authored word lists — TRACKED, language-neutral
    stop_words.txt         421 words that must never become name candidates
  vicary_build/            the fetch mechanism (Python, stdlib only)
  tests/                   its own suite, run by `just asset-test`
```

Each front door gets a **vendored, gitignored copy** of that payload —
`python/src/vicary/data/`, `typescript/assets/`, `ruby/assets/` — reproduced by its
own sync step in its own language. The mechanism is shared; the invocation is not,
because each package manager wants its own hook.

## Why it is not inside the Python package

It was, as `vicary.build`, until it was lifted out. Three costs, all real:

- `pip install vicary` carried a SPARQL client no host wanted, on the request path
  of a library whose entire claim is "no model, no network, no per-request cost".
- The npm package and the gem vendored their gazetteer out of
  `python/src/vicary/data/`, which made one of three equal front doors the
  structural owner of the shared input. Parity between peers is checkable; parity
  with an original is just copying.
- The builder imported the Python detector's stoplist. A build tool that depends
  on one of its own consumers is not shared, whatever directory it sits in.

The stoplist is now `lexicon/stop_words.txt`, read by the builder and by all three
detectors. It is data for the same reason the gazetteer is: a 421-word list
transliterated by hand into a second language diverges silently, and the divergence
shows up as prose corruption in one language and not the others — which no parity
check on *masked output* would catch, because a stop word going missing changes
what gets masked in essays nobody put in a fixture.

## Commands

```sh
just asset-stats          # what a rebuild would produce; writes nothing
just asset-fetch          # rebuild from upstreams, rewrite the manifest
just asset-sync           # vendor the tracked payload into every front door
just asset-test           # this directory's own tests
```

A rebuild needs a local copy of the SSA baby-names archive, because `ssa.gov`
returns an Akamai 403 to some networks on every path including the site root:

```sh
export VICARY_BUILD_SSA_NAMES_ZIP=/path/to/names.zip
```

**Read the manifest diff after any `asset-fetch`.** A changed `sha256` with
unchanged tier counts, or the reverse, is the interesting case. And the tier counts
are asserted against the *loaded* gazetteer by a unit test rather than trusted from
the build log — a build that wrote to a path nothing reads has already happened
here once, and it printed a pass.

## The two guards worth knowing before you change anything

**A short read makes the redactor more aggressive.** Fewer gazetteer entries means
fewer public figures recognised, so more of them get masked. Fewer stop words means
more capitalised ordinary words become name candidates. Both directions look
privacy-safe, corrupt prose, and pass any check that only asks whether something
was masked. That is why every tier and every lexicon declares its own count in its
own header, and why every reader asserts the count rather than trusting it.

**The vendored copies are not tracked, including Python's.** A second tracked copy
is a second thing to bump per asset cut, which is how two front doors end up
shipping different gazetteers. The cost of that symmetry is that a wheel built
without `just asset-sync` ships no gazetteer; `python/tests/test_packaging.py` and
CI's `python-build` job both fail when it does.
