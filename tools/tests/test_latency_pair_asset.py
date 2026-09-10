"""The pair shares this checkout's asset — except across a format boundary.

Both sides of the latency pair normally read the same inputs, so a change to the
gazetteer or the word lists cannot masquerade as a code regression. That breaks
the moment the payload's format moves: 0.2.10 split `stop_words.txt` into two
files, the pair handed a 0.2.8 worktree this checkout's asset, and the previous
release raised `lexicon "stop_words" missing` before timing a single essay. Both
release workflows and CI went red on the tag; `just ci` on a developer box had
been green, because it never takes the pair.

The manifest already carried the answer — every entry declares the
`min_package_version` that can read it — so what was missing was a reader of it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _module():
    spec = importlib.util.spec_from_file_location(
        "latency_pair", ROOT / "tools" / "latency_pair.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def latency_pair():
    return _module()


def test_versions_compare_as_numbers_not_as_strings(latency_pair) -> None:
    """The trap that makes a string comparison wrong at exactly ten."""
    assert latency_pair._version_tuple("0.2.10") > latency_pair._version_tuple("0.2.9")
    assert latency_pair._version_tuple("0.2.8") < latency_pair._version_tuple("0.2.10")
    assert latency_pair._version_tuple("") == ()


def test_a_release_below_the_asset_floor_keeps_its_own_asset(latency_pair) -> None:
    """0.2.8 cannot read a 0.2.10 payload, and the refusal names the entries."""
    readable, gating = latency_pair._asset_is_readable_by(ROOT, "0.2.8")
    manifest = json.loads((ROOT / "asset" / "data" / "MANIFEST.json").read_text())
    floors = {
        str(entry.get("min_package_version", "0"))
        for entry in manifest["assets"].values()
    }
    if all(latency_pair._version_tuple(f) <= latency_pair._version_tuple("0.2.8")
           for f in floors):
        pytest.skip("no asset entry currently declares a floor above 0.2.8")
    assert not readable
    assert gating, "a refusal that names nothing tells the operator nothing"


def test_this_checkout_can_always_read_its_own_asset(latency_pair) -> None:
    """The floor is a claim about readers, so the writer must satisfy it."""
    version = (ROOT / "VERSION").read_text().strip()
    readable, gating = latency_pair._asset_is_readable_by(ROOT, version)
    assert readable, f"{version} cannot read the asset it ships: {gating}"
