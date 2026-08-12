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

# The nine gates. Four need data this repo does not ship — see `gates` below.
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
    cd tools && ../python/.venv/bin/ruff check tests
    cd asset && ../python/.venv/bin/ruff check vicary_build tests

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

# The nine gates, measured wherever they can be. FOUR OF NINE need data that is
# not packaged: three an essay corpus you supply, one the US Census surname file.
# They skip when it is absent and the report prints NOT MEASURED for each, so a
# green run means "the corpus-free gates hold", never "the gate set is clear".
#
#   export VICARY_EVAL_CORPUS_DIR=/path/to/corpus   # holds one .tsv
#   export VICARY_EVAL_CENSUS_CSV=/path/to/names.zip
#
# The census file takes every port from five of nine to six, and the corpus
# takes it to nine. Python reads the .zip or the extracted .csv; TypeScript and
# Ruby read the .csv only and refuse a .zip by name, so extract
# Names_2010Census.csv out of it to satisfy all three at once.
#
# Every port that can measure, not just the reference — all three now measure all
# nine when both files are configured, and each recovers its own numbers rather
# than reading Python's out of the spec, which is the only version of this that is
# evidence. The one thing they share is conformance/carrier.json, which records
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
    @# carrier.json needs the corpus, so it is regenerated only when one is
    @# configured. Left alone otherwise — an absent corpus must not silently
    @# rewrite the plan the committed gate numbers were measured against.
    @if [ -n "${VICARY_EVAL_CORPUS_TSV:-}${VICARY_EVAL_CORPUS_DIR:-}" ]; then \
      cd python && .venv/bin/python -m vicary.eval.carrier --write; \
    else echo "SKIPPED carrier.json — no VICARY_EVAL_CORPUS_TSV/_DIR set"; fi
    @# measured.json is the reference's ANSWERS on that plan — the counts all
    @# three ports assert against instead of typing 29 into three test suites.
    @# Same corpus condition, and for a sharper reason: regenerating it without
    @# one would publish zeroes, which every port would then agree with.
    @if [ -n "${VICARY_EVAL_CORPUS_TSV:-}${VICARY_EVAL_CORPUS_DIR:-}" ]; then \
      cd python && .venv/bin/python -m vicary.eval.measured --write; \
    else echo "SKIPPED measured.json — no VICARY_EVAL_CORPUS_TSV/_DIR set"; fi
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
ci: lint test gates conformance parity
