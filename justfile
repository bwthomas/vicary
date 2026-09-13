# vicary — one detector, three front doors.
#
# Every recipe here works across languages. Language-specific tasks live in the
# language's own directory and are invoked from there, so `cd python && pytest`
# stays the obvious thing and this file does not become a second build system.

set shell := ["bash", "-uc"]

python := "python/.venv/bin/python"

# Where `just latency-pairs` leaves each port's pair record and `just gates`
# looks for it. Keyed on the commit on purpose: a record measured for another
# commit is not this build's verdict, and the only witness of that locally is the
# file's name — CI has `GITHUB_SHA` and the readers check it there. So a stale
# record is simply ABSENT here, the gate says the file does not exist, and the
# run goes red naming it. The alternative — one fixed path per port — is a
# week-old measurement read as today's, silently.
pair_dir := "/tmp/vicary-latency-pairs"
head_sha := `git rev-parse --short HEAD 2>/dev/null || echo nohead`

_default:
    @just --list

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

# Create the Python venv, install the package with its dev extras and the asset
# builder, and vendor the asset. The vendor step is not optional: `data/` is a
# gitignored copy in every front door now, so a fresh checkout has no gazetteer
# and no stoplist until this runs, and importing vicary raises rather than
# answering from an empty one.
py-setup:
    cd python && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip \
      && .venv/bin/pip install -q -e '.[dev]' && .venv/bin/pip install -q -e ../asset
    @just asset-sync-python

# The Python front door only. The asset builder and the eval-harness tests are
# linted by `just tools-lint`, so a lint failure names the suite that owns the
# file rather than always naming Python.
py-lint:
    cd python && .venv/bin/ruff check src tests && .venv/bin/mypy src/vicary

py-test:
    cd python && .venv/bin/python -m pytest -m "not gates" -q

# The nine gates, all nine measured — see `gates` below.
py-gates:
    cd python && .venv/bin/python -m pytest -m gates -s -q

# Vendor first, always. `python/src/vicary/data/` is gitignored, so a build in a
# tree where the sync has not run produces a wheel that installs cleanly and loads
# an empty gazetteer — which redacts every public figure in every essay.
py-build: asset-sync-python
    cd python && rm -rf dist && .venv/bin/python -m build && .venv/bin/python -m twine check dist/*

# ---------------------------------------------------------------------------
# The shared asset — see asset/README.md
# ---------------------------------------------------------------------------

# Rebuild the gazetteer from its public upstreams and rewrite the manifest. Slow
# (a full Wikidata sweep) and it reaches the network. Needs
# VICARY_BUILD_SSA_NAMES_ZIP; READ THE MANIFEST DIFF afterwards.
asset-fetch:
    cd python && .venv/bin/python -m vicary_build fetch

# What a rebuild would produce, writing nothing.
asset-stats:
    cd python && .venv/bin/python -m vicary_build fetch --stats

# Vendor the tracked payload into every front door present. This is what makes
# "all three load the same bytes" true rather than intended.
# Regenerate the inflection region of every authored word list, from the census
# and given-name tables this repository already tracks. No network. Run it after
# adding a stop word, then `asset-manifest` and `asset-sync`.
asset-lexicon:
    cd python && .venv/bin/python -m vicary_build lexicon

asset-manifest:
    cd python && .venv/bin/python -m vicary_build manifest

asset-sync:
    @just asset-sync-python
    @if [ -f typescript/package.json ]; then cd typescript && npm run sync-assets; \
      else echo "SKIPPED typescript — no package.json yet"; fi
    @if [ -f ruby/Rakefile ]; then cd ruby && rake sync_assets; \
      else echo "SKIPPED ruby — no Rakefile yet"; fi

asset-sync-python:
    cd python && .venv/bin/python -m vicary_build vendor src/vicary/data

# The asset builder's own tests. Kept as a named recipe because `asset/` is an
# installable package and its tests sit beside it; `just tools` runs this and the
# rest of the tools suite together.
asset-test:
    cd python && .venv/bin/python -m pytest ../asset/tests -q

# ---------------------------------------------------------------------------
# The tools — see tools/README.md
# ---------------------------------------------------------------------------

# The fourth suite: everything that is not one of the three redactors. The eval
# fixture, the over-fire harness, the gazetteer builder, and the generator behind
# `conformance/*.json` that the other two ports check themselves against.
#
# Two directories, one suite. `asset/tests` stays beside the package it tests
# because `vicary_build` is installable and that is where Python looks; the rest
# has no package to sit beside, which is the whole reason it used to be filed
# under the Python front door and made it look twice as covered as the others.
tools:
    cd python && .venv/bin/python -m pytest ../tools/tests ../asset/tests -q

# Each directory is linted from its own rootdir. Running both through `python/`
# would apply that package's `src` setting to files outside it, which reclassifies
# `vicary` as third-party and demands a reshuffle of every import block here.
tools-lint:
    cd tools && ../python/.venv/bin/ruff check tests coverage_board.py version_sync.py \
      latency_pair.py latency_measure.py published_releases.py
    cd asset && ../python/.venv/bin/ruff check vicary_build tests

# ---------------------------------------------------------------------------
# The version
# ---------------------------------------------------------------------------

# Set the repository version, or re-sync the five files that must restate it.
#
# One detector, one number — but the number cannot be READ from one place at
# runtime, because every file that carries it is read somewhere the repository
# root is not: an installed wheel, gem or npm tarball, or a build backend running
# before any of our code does. So it is WRITTEN to five from one, here, and
# `asset/tests/test_version.py` fails on any drift between them.
#
#   just version 0.3.0    # set VERSION and rewrite all five
#   just version          # rewrite all five from the current VERSION
version version="":
    @{{python}} tools/version_sync.py {{version}}

# ---------------------------------------------------------------------------
# The latency gate's other half
# ---------------------------------------------------------------------------

# Measure the previous release and this checkout on THIS machine, and write the
# record the latency gate reads.
#
# Nothing is stored between releases any more. The gate's comparison point is the
# previous release's CODE, checked out of this repository's own history into a
# worktree, timed here, minutes before this checkout is timed here. That is the
# only arrangement that answers "did this change make it slower" on hardware
# nobody controls: identical code spread 67% across six of GitHub's own
# ubuntu-latest runners in Ruby, and a stored number cannot tell that apart from
# a regression.
#
#   just latency-pair ruby           # writes /tmp/vicary-latency-pair-ruby.json
#   cd ruby && VICARY_LATENCY_PAIR=/tmp/vicary-latency-pair-ruby.json rake gates
#
# `just latency-pairs` below is the same thing for every port at once, into the
# location `just gates` reads, and `just ci` runs it. This form is for measuring
# one port by hand.
#
# It takes a minute per port and needs the port set up the way its tests need it
# — `npm ci` in typescript/, the venv in python/ — because it builds the previous
# release with this checkout's toolchain rather than downloading one.
#
# Time the last release and this checkout here, and write the gate's record.
latency-pair impl out="":
    @{{python}} tools/latency_pair.py --impl {{impl}} \
      --out {{ if out == "" { "/tmp/vicary-latency-pair-" + impl + ".json" } else { out } }}

# Take the pair for every port present, into the per-commit location `just gates`
# reads. This is what `just ci` runs, and running it is the whole point: `ci` used
# to be `lint test gates conformance parity coverage`, the pair was a manual
# recipe nobody in a hurry types, and so the gate set printed `NOT MEASURED (1):
# latency vs last release` and `-> this run does not clear the gate set` on every
# local run — under a `20 passed, 1 skipped` that exited 0. Two halves of the same
# defect: the suite now fails on an unmeasured gate, and this makes the gate
# measurable without anyone remembering to.
#
# It costs about a minute per port, which is what it costs to know. `just gates`
# alone still works and still refuses to pass without this, naming the file it
# wanted.
latency-pairs:
    @mkdir -p {{pair_dir}}
    @{{python}} tools/latency_pair.py --impl python \
      --out {{pair_dir}}/{{head_sha}}-python.json
    @if [ -f typescript/package.json ]; then {{python}} tools/latency_pair.py \
      --impl typescript --out {{pair_dir}}/{{head_sha}}-typescript.json; \
      else echo "SKIPPED typescript — no package.json yet"; fi
    @if [ -f ruby/Rakefile ]; then {{python}} tools/latency_pair.py \
      --impl ruby --out {{pair_dir}}/{{head_sha}}-ruby.json; \
      else echo "SKIPPED ruby — no Rakefile yet"; fi

# ---------------------------------------------------------------------------
# Across every front door
# ---------------------------------------------------------------------------

# All four suites: the tools, then each implementation's own. Languages that are
# not present yet are skipped out loud, never silently — a run that tested one of
# three and said nothing is the failure mode this whole repository exists to
# prevent.
# TypeScript and Ruby are pointed at their pair records here as well as in
# `gates`, because in those two ports the gate set IS part of the test suite —
# `npm test` and `rake test` run `gates.test.ts` and `gates_test.rb`, which is
# how CI measures the gates there. Python's front door splits them (`pytest -m
# "not gates"`), so it needs nothing here. An unmeasured gate now fails, so a
# bare `just test` in those two ports refuses until the pair exists, naming the
# file it wanted: `just latency-pairs`, or `just ci`, which takes it first.
test:
    @just tools
    @just py-test
    @if [ -f typescript/package.json ]; then cd typescript && \
      VICARY_LATENCY_PAIR={{pair_dir}}/{{head_sha}}-typescript.json npm test; \
      else echo "SKIPPED typescript — no package.json yet"; fi
    @if [ -f ruby/Rakefile ]; then cd ruby && \
      VICARY_LATENCY_PAIR={{pair_dir}}/{{head_sha}}-ruby.json rake test; \
      else echo "SKIPPED ruby — no Rakefile yet"; fi

lint:
    @just py-lint
    @just tools-lint

# The nine gates. ALL NINE are measured on a bare checkout, in every port, with
# no environment set. The four that declare a data requirement read it out of the
# repository: `conformance/corpora/` ships an essay corpus and `conformance/census/`
# ships the US surname table. Neither is in any published package.
#
# The NOT MEASURED machinery stays and is still tested — each port measures those
# four with their inputs withheld on purpose and asserts they report NOT MEASURED
# by name, never reduced out of the denominator. That is what will make the next
# unreachable gate visible.
#
# Two optional overrides, for measuring something other than what ships:
#
#   export VICARY_EVAL_CORPUS_DIR=/path/to/corpus   # holds one .tsv; ASAP-AES
#   export VICARY_EVAL_CENSUS_CSV=/path/to/names.zip
#
# The corpus variables select the ASAP-AES corpus this library was developed
# against; `VICARY_EVAL_CORPUS` names a registered corpus per run. Which corpus
# was measured is printed in the report header, because two of the three corpus
# gates carry a per-corpus bar. For the census override, Python reads the .zip or
# the extracted .csv; TypeScript and Ruby read the .csv only and refuse a .zip by
# name, so extract Names_2010Census.csv out of it to satisfy all three at once.
#
# Every port that can measure, not just the reference — all three measure all
# nine, and each recovers its own numbers rather than reading Python's out of the
# spec, which is the only version of this that is evidence. The one thing they share is conformance/carrier.json, which records
# WHERE each frame is injected into each essay so three languages build the same
# carrier text without three copies of Python's Mersenne Twister. That is an
# input, like frames.json; the measurements stay each port's own. Absent
# languages are skipped out loud, never silently.
gates:
    @VICARY_LATENCY_PAIR={{pair_dir}}/{{head_sha}}-python.json just py-gates
    @if [ -f typescript/package.json ]; then cd typescript && \
      VICARY_LATENCY_PAIR={{pair_dir}}/{{head_sha}}-typescript.json npm run gates; \
      else echo "SKIPPED typescript — no package.json yet"; fi
    @if [ -f ruby/Rakefile ]; then cd ruby && \
      VICARY_LATENCY_PAIR={{pair_dir}}/{{head_sha}}-ruby.json rake gates; \
      else echo "SKIPPED ruby — no Rakefile yet"; fi

# Diff each port's answers against the Python reference directly, on the seams
# `conformance/frames.json` and `primitives.json` cannot reach — both are
# single-line corpora, and several rules only diverge across a newline.
#
# Two layers per port: gazetteer verdicts name by name, then whole-detector
# masked bytes. The probes are shared (`conformance/probes.json`), so the two
# ports are answering the same questions rather than each picking its own.
#
# Python is the reference here, so it has nothing to diff against and no recipe.
parity:
    @if [ -f typescript/package.json ]; then cd typescript && npm run --silent parity; \
      else echo "SKIPPED typescript — no package.json yet"; fi
    @if [ -f ruby/Rakefile ]; then cd ruby && rake parity && rake redaction_parity; \
      else echo "SKIPPED ruby — no Rakefile yet"; fi

# The shared conformance suite: the same frames and the same bars, run against
# every implementation present. This is what makes "parity" a build result
# instead of an opinion.
conformance:
    @just _conformance-check
    @just py-conformance
    @if [ -f typescript/package.json ]; then cd typescript && npm run conformance; \
      else echo "SKIPPED typescript — no package.json yet"; fi
    @if [ -f ruby/Rakefile ]; then cd ruby && rake conformance; \
      else echo "SKIPPED ruby — no Rakefile yet"; fi

# The spec-still-matches-the-reference direction. This lives in the tools suite,
# not the Python front door: it asks whether `conformance/*.json` still describes
# what the reference does, which is a question about the shared spec rather than
# about the package. It moved there with the rest of the fourth suite and this
# recipe kept pointing at the old path, so `just conformance` — and therefore
# `just ci` — failed on a fresh checkout while GitHub Actions passed, because
# `.github/workflows/ci.yml` had been updated and this had not.
py-conformance:
    cd python && .venv/bin/python -m pytest ../tools/tests/test_conformance.py -q

# Regenerate conformance/*.json from the Python implementation, which defines the
# spec. READ THE DIFF before committing: a changed `golden` block means the
# detector's output changed, which is either the improvement you intended or a
# regression all three front doors are about to inherit.
sync-conformance:
    cd python && .venv/bin/python -m vicary.eval.conformance --write
    @# carrier.json and measured.json are per-corpus now, and both generators
    @# MERGE: each regenerates the corpora this machine can read and keeps the
    @# ones it cannot, naming every skip on stdout. So they no longer need an
    @# operator corpus to run at all — a checkout with only the shipped corpus
    @# rebuilds that corpus's plan and answers without touching ASAP-AES's, which
    @# is what the earlier blanket skip existed to protect. What must still never
    @# happen is regenerating a plan from an absent corpus and publishing zeroes
    @# every port would then agree with; that is now the generators' own refusal
    @# rather than a shell guard here.
    cd python && .venv/bin/python -m vicary.eval.carrier --write
    cd python && .venv/bin/python -m vicary.eval.measured --write
    @git --no-pager diff --stat -- conformance/ || true

# A missing spec must stop the run. Otherwise `just conformance` on a tree with
# no conformance data prints three cheerful SKIPPEDs and exits 0 — a green light
# with a comment on it.
_conformance-check:
    @test -f conformance/frames.json \
      || { echo "conformance/frames.json is missing — there is no spec to run"; exit 1; }
    @test -f conformance/primitives.json \
      || { echo "conformance/primitives.json is missing — the ports would check their tokenisation against nothing"; exit 1; }
    @test -f conformance/spans.json \
      || { echo "conformance/spans.json is missing — the ports would check their offset translation against nothing"; exit 1; }

# Everything CI runs, in CI's order.
# Which concern each front door tests, printed as one board.
#
# The three suites report different totals — granularity and scope, not depth —
# and this is what makes that reconcilable without running all three and guessing.
# `just tools` is what ENFORCES it; this only prints it.
coverage:
    @{{python}} tools/coverage_board.py

# The pair comes FIRST, before `test` and not merely before `gates`: in
# TypeScript and Ruby the gate set is inside the test suite, so a pair taken
# after it would leave those two ports reporting NOT MEASURED in the one place
# CI reads them.
#
# Nothing is cached between runs on purpose. A pair record keyed on the commit
# would still be stale the moment the working tree moves under it, and a stale
# `current` side measured against a fresh `previous` one is the same wrong answer
# the stored baseline used to give — with none of the noise that made it obvious.
ci: lint latency-pairs test gates conformance parity coverage
