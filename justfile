# vicary — one detector, three front doors.
#
# Every recipe here works across languages. Language-specific tasks live in the
# language's own directory and are invoked from there, so `cd python && pytest`
# stays the obvious thing and this file does not become a second build system.

set shell := ["bash", "-uc"]

python := "python/.venv/bin/python"

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
    cd tools && ../python/.venv/bin/ruff check tests coverage_board.py version_sync.py
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
# Across every front door
# ---------------------------------------------------------------------------

# All four suites: the tools, then each implementation's own. Languages that are
# not present yet are skipped out loud, never silently — a run that tested one of
# three and said nothing is the failure mode this whole repository exists to
# prevent.
test:
    @just tools
    @just py-test
    @if [ -f typescript/package.json ]; then cd typescript && npm test; \
      else echo "SKIPPED typescript — no package.json yet"; fi
    @if [ -f ruby/Rakefile ]; then cd ruby && rake test; \
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
    @just py-gates
    @if [ -f typescript/package.json ]; then cd typescript && npm run gates; \
      else echo "SKIPPED typescript — no package.json yet"; fi
    @if [ -f ruby/Rakefile ]; then cd ruby && rake gates; \
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

# Everything CI runs, in CI's order.
# Which concern each front door tests, printed as one board.
#
# The three suites report different totals — granularity and scope, not depth —
# and this is what makes that reconcilable without running all three and guessing.
# `just tools` is what ENFORCES it; this only prints it.
coverage:
    @{{python}} tools/coverage_board.py

ci: lint test gates conformance parity coverage
