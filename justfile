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

# Create the Python venv, install the package with its dev extras and the build
# mechanism, and vendor the asset. The vendor step is not optional: `data/` is a
# gitignored copy in every front door now, so a fresh checkout has no gazetteer
# and no stoplist until this runs, and importing vicary raises rather than
# answering from an empty one.
py-setup:
    cd python && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip \
      && .venv/bin/pip install -q -e '.[dev]' && .venv/bin/pip install -q -e ../asset
    @just asset-sync-python

py-lint:
    cd python && .venv/bin/ruff check src tests && .venv/bin/mypy src/vicary
    cd python && .venv/bin/ruff check ../asset/vicary_build ../asset/tests

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

# The build mechanism's own tests. Its own suite because it is not part of any
# front door: it is the thing all three consume.
asset-test:
    cd python && .venv/bin/python -m pytest ../asset/tests -q

# ---------------------------------------------------------------------------
# Across every front door
# ---------------------------------------------------------------------------

# Every implementation's own suite. Languages that are not present yet are
# skipped out loud, never silently: a run that tested one of three and said
# nothing is the failure mode this whole repository exists to prevent.
test:
    @just asset-test
    @just py-test
    @if [ -f typescript/package.json ]; then cd typescript && npm test; \
      else echo "SKIPPED typescript — no package.json yet"; fi
    @if [ -f ruby/Rakefile ]; then cd ruby && rake test; \
      else echo "SKIPPED ruby — no Rakefile yet"; fi

lint:
    @just py-lint

# The nine gates, measured wherever they can be. FOUR OF NINE need data that is
# not packaged: three an essay corpus you supply, one the US Census surname file.
# They skip when it is absent and the report prints NOT MEASURED for each, so a
# green run means "the corpus-free gates hold", never "the gate set is clear".
#
#   export VICARY_EVAL_CORPUS_DIR=/path/to/corpus   # holds one .tsv
#   export VICARY_EVAL_CENSUS_CSV=/path/to/names.zip
gates:
    @just py-gates

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

py-conformance:
    cd python && .venv/bin/python -m pytest tests/test_conformance.py -q

# Regenerate conformance/*.json from the Python implementation, which defines the
# spec. READ THE DIFF before committing: a changed `golden` block means the
# detector's output changed, which is either the improvement you intended or a
# regression all three front doors are about to inherit.
sync-conformance:
    cd python && .venv/bin/python -m vicary.eval.conformance --write
    @git --no-pager diff --stat -- conformance/ || true

# A missing spec must stop the run. Otherwise `just conformance` on a tree with
# no conformance data prints three cheerful SKIPPEDs and exits 0 — a green light
# with a comment on it.
_conformance-check:
    @test -f conformance/frames.json \
      || { echo "conformance/frames.json is missing — there is no spec to run"; exit 1; }

# Everything CI runs, in CI's order.
ci: lint test gates conformance
