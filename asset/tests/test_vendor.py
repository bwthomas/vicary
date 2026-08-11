"""The sync step — the thing that makes "all three load the same bytes" true.

Three scripts do this, one per package manager (this one,
``typescript/scripts/sync-assets.mjs``, ``ruby/scripts/sync_assets.rb``). This is
the one with tests, and the two guards below are the two those scripts also
implement; if a guard changes here it has to change in all three.
"""

from __future__ import annotations

import json
from pathlib import Path

from vicary_build import manifest, vendor


def test_the_payload_is_the_whole_manifest() -> None:
    """Adding an asset without updating a front door's file list must not ship.

    Otherwise the package carries a manifest describing a file it does not have,
    which fails at load time for a user instead of at build time for us.
    """
    described = set(manifest.existing())
    vendored = {name for _, name in vendor.payload()} - {manifest.MANIFEST_NAME}
    assert vendored == described


def test_a_clean_vendor_verifies(tmp_path: Path) -> None:
    assert vendor.vendor(tmp_path) == 0
    assert (tmp_path / "notability.txt.gz").exists()
    assert (tmp_path / "stop_words.txt").exists()
    assert (tmp_path / manifest.MANIFEST_NAME).exists()


def test_a_truncated_asset_is_caught_after_landing(tmp_path: Path) -> None:
    """Verify what landed, not what was copied.

    A successful copy call says the call succeeded. It does not say the bytes on
    disk are the bytes the manifest describes, and a truncated gazetteer loads as a
    SMALLER one — which redacts more, looks privacy-safe, and is invisible to every
    test that only checks that something was masked.

    Verified red by making the copy lie: the manifest is edited after the copy so
    the bytes on disk no longer match it, which is the same shape of mismatch a
    half-written file produces.
    """
    assert vendor.vendor(tmp_path) == 0
    described = json.loads(
        (tmp_path / manifest.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    described["assets"]["notability.txt.gz"]["bytes"] += 1
    (tmp_path / manifest.MANIFEST_NAME).write_text(
        json.dumps(described), encoding="utf-8"
    )
    # Re-copying overwrites the doctored manifest, so point the check at a target
    # whose manifest is the doctored one by copying it into place first.
    poisoned = tmp_path / "poisoned"
    poisoned.mkdir()
    for name in ("notability.txt.gz", "stop_words.txt"):
        (poisoned / name).write_bytes((tmp_path / name).read_bytes())
    (poisoned / manifest.MANIFEST_NAME).write_text(
        json.dumps(described), encoding="utf-8"
    )
    assert vendor._verify(poisoned) == 1


def test_a_short_stoplist_is_caught_too(tmp_path: Path) -> None:
    """The same guard, running the other way.

    A truncated gazetteer redacts more. A truncated STOPLIST also redacts more,
    because fewer stop words means more capitalised ordinary words become name
    candidates. Both look privacy-safe; neither is.
    """
    assert vendor.vendor(tmp_path) == 0
    (tmp_path / "stop_words.txt").write_text(
        "#!lexicon 1\n#!list stop_words 1\nthe\n", encoding="utf-8"
    )
    assert vendor._verify(tmp_path) == 1


def test_a_tree_with_no_asset_source_says_so(tmp_path: Path, monkeypatch) -> None:
    """Not a crash and not a silent empty copy.

    An installed package outside a checkout has nothing to vendor from and already
    carries its assets, so this is a real state rather than an error — but it must
    never be mistaken for a successful sync.
    """
    monkeypatch.setattr(vendor.config, "DATA_DIR", tmp_path / "absent")
    assert vendor.vendor(tmp_path / "out") == 2
