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

# Create the Python venv and install the package with its dev extras.
py-setup:
    cd python && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip \
      && .venv/bin/pip install -q -e '.[dev]'

py-lint:
    cd python && .venv/bin/ruff check src tests && .venv/bin/mypy src/vicary

py-test:
    cd python && .venv/bin/python -m pytest -m "not gates" -q

# The nine gates. Four need data this repo does not ship — see `gates` below.
py-gates:
    cd python && .venv/bin/python -m pytest -m gates -s -q

py-build:
    cd python && rm -rf dist && .venv/bin/python -m build && .venv/bin/python -m twine check dist/*

# ---------------------------------------------------------------------------
# Across every front door
# ---------------------------------------------------------------------------

# Every implementation's own suite. Languages that are not present yet are
# skipped out loud, never silently: a run that tested one of three and said
# nothing is the failure mode this whole repository exists to prevent.
test:
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

# A missing spec must stop the run. Otherwise `just conformance` on a tree with
# no conformance data prints three cheerful SKIPPEDs and exits 0 — a green light
# with a comment on it.
_conformance-check:
    @test -f conformance/frames.json \
      || { echo "conformance/frames.json is missing — there is no spec to run"; exit 1; }

# Everything CI runs, in CI's order.
ci: lint test gates conformance
