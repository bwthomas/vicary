"""The manifest writer — what every front door checks itself against.

Writing lives in the build mechanism and reading lives in each front door, so this
file tests the writer against the *reader*, not against itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from vicary_build import config, manifest


def _written(tmp_path: Path, **kwargs) -> dict:
    target = manifest.write(path=tmp_path / "MANIFEST.json", **kwargs)
    return json.loads(target.read_text(encoding="utf-8"))


def test_the_manifest_round_trips_through_the_readers(tmp_path: Path) -> None:
    """What this writes, the Python front door must parse — or the package is
    unverifiable, which is indistinguishable from verified."""
    from vicary import assets

    payload = _written(tmp_path, sources=("https://example.invalid/x",))
    entry = payload["assets"]["notability.txt.gz"]
    assert entry["sha256"] == assets.sha256_of(assets.bundled_path())
    assert entry["tiers"] == assets.record_for().tiers
    assert entry["sources"] == ["https://example.invalid/x"]


def test_both_assets_are_described(tmp_path: Path) -> None:
    """The gazetteer and every authored lexicon.

    A manifest that described only the gazetteer would let a front door ship a
    stoplist nothing checksummed — and the sync step's payload check would then
    reject the file it was told to carry.
    """
    payload = _written(tmp_path)
    assert set(payload["assets"]) == {"notability.txt.gz", "stop_words.txt"}
    assert payload["assets"]["stop_words.txt"]["entries"] == 421


def test_the_tier_counts_come_from_the_file_not_the_build(tmp_path: Path) -> None:
    """Read back off disk, so the numbers describe the file that exists.

    A build that reported the counts it *intended* would have said nothing was
    wrong on the day it wrote its output to a path nothing read.
    """
    payload = _written(tmp_path)
    tiers = payload["assets"]["notability.txt.gz"]["tiers"]
    assert sum(tiers.values()) == 392165
    assert set(tiers) == {
        "demonym", "full", "given", "place", "settlement", "short", "title",
    }


# ---------------------------------------------------------------------------
# min_package_version — the field that fails closed, and must not fail closed
# against its own user
# ---------------------------------------------------------------------------


def test_a_refresh_does_not_raise_an_untouched_assets_floor(tmp_path: Path) -> None:
    """The 0.1.0 gazetteer is readable by 0.1.0.

    A manifest refresh that stamped the *current* version into every entry would
    make every older install refuse a perfectly good file, for a reason no error
    message would explain. Red before the fix: the first refresh after the 0.2.0
    bump moved the gazetteer's floor from 0.1.0 to 0.2.0 while changing not one of
    its bytes.
    """
    target = tmp_path / "MANIFEST.json"
    target.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "assets": {
                    "notability.txt.gz": {
                        "format": 5,
                        "min_package_version": "0.0.1",
                        "sources": ["https://recorded.invalid/upstream"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    payload = json.loads(manifest.write(path=target).read_text(encoding="utf-8"))
    entry = payload["assets"]["notability.txt.gz"]
    assert entry["min_package_version"] == "0.0.1"
    # Provenance survives too: dropping it is a silent loss, and a manifest
    # refresh is not where anybody is looking for one.
    assert entry["sources"] == ["https://recorded.invalid/upstream"]


def test_a_rebuilt_asset_does_raise_its_floor(tmp_path: Path) -> None:
    target = tmp_path / "MANIFEST.json"
    target.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "assets": {
                    "notability.txt.gz": {"format": 5, "min_package_version": "0.0.1"}
                },
            }
        ),
        encoding="utf-8",
    )
    payload = json.loads(
        manifest.write(path=target, rebuilt={"notability.txt.gz"}).read_text(
            encoding="utf-8"
        )
    )
    floor = payload["assets"]["notability.txt.gz"]["min_package_version"]
    assert floor == config.version()


def test_a_format_change_raises_the_floor_without_being_asked(tmp_path: Path) -> None:
    """A format change is what the floor is *for*.

    Older code handed a newer format is the mismatch that answers plausibly and
    wrongly; a missing asset raises on its own. So this does not wait to be told.
    """
    target = tmp_path / "MANIFEST.json"
    target.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "assets": {
                    "notability.txt.gz": {"format": 4, "min_package_version": "0.0.1"}
                },
            }
        ),
        encoding="utf-8",
    )
    payload = json.loads(manifest.write(path=target).read_text(encoding="utf-8"))
    assert payload["assets"]["notability.txt.gz"]["min_package_version"] == (
        config.version()
    )


def test_a_new_asset_gets_the_current_floor(tmp_path: Path) -> None:
    """Nothing older has ever shipped it, so nothing older can read it."""
    payload = _written(tmp_path)
    assert payload["assets"]["stop_words.txt"]["min_package_version"] == (
        config.version()
    )
