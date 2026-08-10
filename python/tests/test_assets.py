"""Asset resolution and verification.

Three claims get a failing case each, because a verification step nothing can
break is a green light with a comment on it:

* a corrupted asset fails the checksum;
* an asset requiring a newer package refuses to load;
* the ``VICARY_ASSET_PATH`` override actually redirects the load.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
from pathlib import Path

import pytest

from vicary import assets, config, gazetteer
from vicary._version import __version__


@pytest.fixture(autouse=True)
def _no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(config.ASSET_PATH_ENV_VAR, raising=False)
    gazetteer.reset_cache()


# ---------------------------------------------------------------------------
# Manifest.
# ---------------------------------------------------------------------------


def test_the_manifest_describes_the_shipped_asset() -> None:
    record = assets.record_for()
    path = assets.bundled_path()
    assert record.sha256 == assets.sha256_of(path)
    assert record.bytes == path.stat().st_size
    assert record.format == gazetteer.SUPPORTED_FORMAT


def test_the_manifest_tier_counts_match_the_asset_header() -> None:
    """The counts are what a reader would use to size a rebuild, and a manifest
    that disagrees with the file it describes is worse than no manifest."""
    record = assets.record_for()
    described = assets.describe(assets.bundled_path())
    assert record.tiers == described["tiers"]


def test_the_manifest_tier_counts_match_the_loaded_gazetteer() -> None:
    """The claim ``build.gazetteer.ASSET_RELPATH`` makes, actually asserted.

    The comment there says tier counts "are now asserted against the loaded
    gazetteer by a unit test rather than trusted from the build log". Until this
    test existed that was a claim about a test that did not exist — and the
    reason it is worth having is that the build log is the one place those counts
    HAD been read from, on a cut where the log reported 1,044 demonyms and the
    running process held 0.

    The manifest ↔ header pair above and ``_parse``'s truncation check close the
    file's internal consistency. What neither closes is the direction the
    2026-08-06 defect took: a tier this reader declares but the asset does not
    carry loads as an empty frozenset, in silence. So this compares the manifest
    against the frozensets a process actually gets, over
    :data:`~vicary.gazetteer.TIER_NAMES` rather than a literal list, so the next
    tier is covered without anyone remembering to extend it.
    """
    record = assets.record_for()
    loaded = gazetteer.load(force=True)
    assert set(record.tiers) == set(gazetteer.TIER_NAMES), (
        "manifest tiers and the reader's tiers disagree: manifest has "
        f"{sorted(set(record.tiers) - set(gazetteer.TIER_NAMES))} extra, "
        f"missing {sorted(set(gazetteer.TIER_NAMES) - set(record.tiers))}"
    )
    for tier, count in record.tiers.items():
        assert len(getattr(loaded, tier)) == count, (
            f"tier {tier!r}: manifest says {count:,}, the loaded gazetteer "
            f"holds {len(getattr(loaded, tier)):,}"
        )
        assert count > 0, f"tier {tier!r} shipped empty"


def test_the_reader_declares_a_field_for_every_tier_it_names() -> None:
    """``TIER_NAMES`` is the one list; the dataclass must agree with it.

    Red if a tier is added to :data:`~vicary.gazetteer.TIER_NAMES` without a
    field to hold it (``_parse`` would raise on every asset) or a frozenset field
    is added without a name (it would never be populated, which is the empty-tier
    failure again).
    """
    fields = {
        field.name
        for field in dataclasses.fields(gazetteer.Gazetteer)
        if not field.name.startswith("_") and field.name != "meta"
    }
    assert fields == set(gazetteer.TIER_NAMES)


def test_the_manifest_records_where_the_data_came_from() -> None:
    """Provenance is the part of a measurement envelope a data asset can carry."""
    record = assets.record_for()
    assert record.sources
    assert all(source.startswith("https://") for source in record.sources)


def test_an_unknown_asset_name_raises_rather_than_returning_nothing() -> None:
    with pytest.raises(assets.AssetError) as caught:
        assets.record_for("no-such-asset.txt.gz")
    assert "no-such-asset.txt.gz" in str(caught.value)


# ---------------------------------------------------------------------------
# Corruption. The failing case for the checksum.
# ---------------------------------------------------------------------------


def _copy_bundled(tmp_path: Path) -> Path:
    target = tmp_path / assets.NOTABILITY_ASSET
    target.write_bytes(assets.bundled_path().read_bytes())
    return target


def test_verify_catches_a_truncated_asset(tmp_path: Path) -> None:
    corrupt = _copy_bundled(tmp_path)
    data = corrupt.read_bytes()
    corrupt.write_bytes(data[: len(data) - 1024])

    report = assets.verify(path=corrupt)
    assert not report
    assert any("size" in problem for problem in report.problems)
    assert any("sha256" in problem for problem in report.problems)


def test_verify_catches_a_same_size_edit(tmp_path: Path) -> None:
    """The size check alone would miss this, which is why the checksum is there."""
    corrupt = _copy_bundled(tmp_path)
    data = bytearray(corrupt.read_bytes())
    data[-1] ^= 0xFF
    corrupt.write_bytes(bytes(data))

    report = assets.verify(path=corrupt)
    assert not report
    assert report.actual_bytes == report.expected_bytes
    assert any("sha256" in problem for problem in report.problems)


def test_verify_reports_a_missing_asset_without_raising(tmp_path: Path) -> None:
    report = assets.verify(path=tmp_path / "absent.txt.gz")
    assert not report
    assert not report.exists
    assert "not found" in report.problems[0]


def test_load_refuses_a_bundled_asset_that_fails_the_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failing case for the load-time check, forced by faking the report
    rather than corrupting the installed package."""
    broken = assets.VerifyReport(
        path=assets.bundled_path(), exists=True,
        expected_sha256="expected", actual_sha256="actual",
        expected_bytes=1, actual_bytes=1,
        problems=("sha256 is actual, manifest says expected",),
    )
    monkeypatch.setattr(assets, "verify", lambda *a, **k: broken)
    gazetteer.reset_cache()
    with pytest.raises(gazetteer.GazetteerAssetMissing) as caught:
        gazetteer.load(force=True)
    assert "manifest" in str(caught.value)


# ---------------------------------------------------------------------------
# Version floor. The mismatch that answers wrongly instead of failing.
# ---------------------------------------------------------------------------


def test_an_asset_from_a_newer_release_is_refused() -> None:
    record = assets.record_for()
    future = type(record)(**{**record.__dict__, "min_package_version": "99.0.0"})
    with pytest.raises(assets.AssetError) as caught:
        assets.check_package_version(future)
    assert "99.0.0" in str(caught.value)
    assert __version__ in str(caught.value)


def test_the_shipped_asset_is_readable_by_the_shipped_code() -> None:
    assets.check_package_version(assets.record_for())


@pytest.mark.parametrize(
    ("installed", "required", "allowed"),
    [
        ("0.1.0", "0.1.0", True),
        ("0.2.0", "0.1.0", True),
        ("1.0.0", "0.9.9", True),
        ("0.1.0", "0.2.0", False),
        ("0.9.9", "1.0.0", False),
        # A pre-release sorts with its release: refusing a good asset because the
        # developer is on 0.2.0.dev1 would be the check failing against its user.
        ("0.2.0.dev1", "0.2.0", True),
    ],
)
def test_the_version_floor_orders_releases(
    installed: str, required: str, allowed: bool,
) -> None:
    record = assets.record_for()
    candidate = type(record)(**{**record.__dict__, "min_package_version": required})
    if allowed:
        assets.check_package_version(candidate, package_version=installed)
    else:
        with pytest.raises(assets.AssetError):
            assets.check_package_version(candidate, package_version=installed)


# ---------------------------------------------------------------------------
# The override.
# ---------------------------------------------------------------------------


def test_the_override_redirects_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    elsewhere = _copy_bundled(tmp_path)
    monkeypatch.setenv(config.ASSET_PATH_ENV_VAR, str(elsewhere))
    path, is_bundled = assets.resolve()
    assert path == elsewhere
    assert not is_bundled


def test_the_override_accepts_a_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A deployment mounting an asset directory should not have to know the
    filename the library chose."""
    _copy_bundled(tmp_path)
    monkeypatch.setenv(config.ASSET_PATH_ENV_VAR, str(tmp_path))
    path, _ = assets.resolve()
    assert path == tmp_path / assets.NOTABILITY_ASSET


def test_an_overridden_asset_is_not_checksummed_against_the_bundled_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Pointing somewhere else is the entire purpose of the override. A manifest
    comparison would reject every asset it was ever used for.

    The substitute here is a small, valid, deliberately *different* asset: two
    entries, correct header, correct declared counts.
    """
    substitute = tmp_path / assets.NOTABILITY_ASSET
    with gzip.open(substitute, "wt", encoding="utf-8") as fh:
        fh.write(f"#!gazetteer {gazetteer.SUPPORTED_FORMAT}\n")
        fh.write('#!meta {"cut_date": "2026-01-01"}\n')
        fh.write("#!tier full 1\n")
        fh.write("abraham lincoln\n")
        fh.write("#!tier short 1\n")
        fh.write("lincoln\n")

    monkeypatch.setenv(config.ASSET_PATH_ENV_VAR, str(substitute))
    gazetteer.reset_cache()
    loaded = gazetteer.load(force=True)
    assert loaded.full == frozenset({"abraham lincoln"})
    assert loaded.meta["cut_date"] == "2026-01-01"
    gazetteer.reset_cache()


def test_a_missing_override_target_says_which_variable_to_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv(config.ASSET_PATH_ENV_VAR, str(tmp_path / "absent.txt.gz"))
    gazetteer.reset_cache()
    with pytest.raises(gazetteer.GazetteerAssetMissing) as caught:
        gazetteer.load(force=True)
    assert config.ASSET_PATH_ENV_VAR in str(caught.value)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def test_show_prints_the_tier_counts(capsys: pytest.CaptureFixture[str]) -> None:
    assert assets.main(["show"]) == 0
    out = capsys.readouterr().out
    for tier in ("full", "short", "place", "given", "title"):
        assert tier in out
    assert f"{assets.record_for().tiers['full']:,}" in out


def test_verify_exits_zero_on_the_shipped_asset(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert assets.main(["verify"]) == 0
    assert capsys.readouterr().out.startswith("OK ")


def test_verify_exits_nonzero_when_the_asset_is_wrong(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    broken = assets.VerifyReport(
        path=assets.bundled_path(), exists=False,
        expected_sha256="a", actual_sha256="",
        expected_bytes=1, actual_bytes=-1,
        problems=("asset not found",),
    )
    monkeypatch.setattr(assets, "verify", lambda *a, **k: broken)
    assert assets.main(["verify"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_verify_says_so_rather_than_printing_a_meaningless_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With an override set there is nothing to compare against. Printing ``OK``
    would report a check that did not happen."""
    _copy_bundled(tmp_path)
    monkeypatch.setenv(config.ASSET_PATH_ENV_VAR, str(tmp_path))
    assert assets.main(["verify"]) == 0
    out = capsys.readouterr().out
    assert "OVERRIDE" in out
    assert "not manifest-checked" in out


def test_the_manifest_round_trips(tmp_path: Path) -> None:
    """``write_manifest`` must produce what ``manifest`` parses, or ``fetch``
    leaves the package unverifiable."""
    written = assets.write_manifest(
        {assets.NOTABILITY_ASSET: {"sources": ["https://example.invalid/x"]}},
        path=tmp_path / "MANIFEST.json",
    )
    payload = json.loads(written.read_text(encoding="utf-8"))
    entry = payload["assets"][assets.NOTABILITY_ASSET]
    assert entry["sha256"] == assets.sha256_of(assets.bundled_path())
    assert entry["tiers"] == assets.record_for().tiers
    assert entry["min_package_version"] == __version__
