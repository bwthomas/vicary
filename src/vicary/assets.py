"""Where the data asset comes from, and proof that it is the one we think it is.

vicary's notability lookup is a 2.1 MB compressed asset built from two public
upstreams (see :mod:`vicary.build.gazetteer`). It ships **inside the wheel** as
package data. That is not a relocation, it is the removal of a defect class: the
asset previously reached deployment through a build-time file-copy allowlist, and
an allowlist that must be remembered is an allowlist that gets forgotten — the
failure mode being a container that builds clean and raises at request time
instead.

Three things live here.

**Resolution.** :func:`resolve` finds the asset: an explicit
``VICARY_ASSET_PATH`` if set, otherwise the bundled copy, whose location is
*computed* from this module's own path so a checkout, a virtualenv and a
container all work with nothing configured.

**Verification.** ``data/MANIFEST.json`` records each asset's SHA-256, byte
count, per-tier entry counts, format number, cut date, upstream sources, and
``min_package_version``. :func:`verify` checks the first two; :func:`load` in
:mod:`vicary.gazetteer` calls it for the bundled asset. The check that actually
earns its keep is ``min_package_version``: the dangerous mismatch is not old code
with a *missing* asset — that raises on its own — it is old code handed a *newer*
asset whose tier semantics moved, which answers plausibly and wrongly. The
asset's own format header covers the other direction.

An asset supplied through ``VICARY_ASSET_PATH`` is checked against its own
embedded header, not against the bundled manifest. Pointing somewhere else is the
entire purpose of the override; refusing to load what it points at would make it
useless.

**A front door for rebuilding.** ``python -m vicary.assets`` — ``show``,
``verify``, ``fetch``. The builder was always here; what did not exist was a way
to ask an installed copy what it is holding.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from vicary import config
from vicary._version import __version__

#: The one asset that exists today. Named rather than assumed so a second one
#: costs a manifest entry instead of a refactor.
NOTABILITY_ASSET = "notability.txt.gz"

#: Package-data directory, computed from this module's location.
DATA_DIR: Path = Path(__file__).resolve().parent / "data"

MANIFEST_NAME = "MANIFEST.json"

#: Chunk size for hashing. 1 MiB: large enough that the syscall count is
#: irrelevant, small enough that a 2 MB asset never doubles peak memory.
_HASH_CHUNK = 1 << 20


class AssetError(RuntimeError):
    """An asset is missing, unreadable, or not the one the manifest describes.

    Raised rather than degrading. For the notability asset specifically, the
    degraded behaviour would be "nothing is notable" — privacy-safe and
    product-hostile, masking every public figure in every essay, and presenting
    as a tuning regression rather than a packaging bug for as long as it took
    somebody to notice.
    """


@dataclass(frozen=True)
class AssetRecord:
    """One manifest entry."""

    name: str
    format: int
    sha256: str
    bytes: int
    tiers: dict[str, int]
    cut_date: str
    min_package_version: str
    sources: tuple[str, ...]

    @property
    def entries(self) -> int:
        return sum(self.tiers.values())


def manifest_path() -> Path:
    return DATA_DIR / MANIFEST_NAME


def manifest() -> dict[str, AssetRecord]:
    """Parse the bundled manifest.

    Raises :class:`AssetError` when it is absent: a wheel without it was built
    wrong, and guessing is how an unverified asset gets loaded forever.
    """
    path = manifest_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AssetError(
            f"asset manifest missing at {path}. The installed vicary package is "
            "incomplete — reinstall it, or regenerate the manifest with "
            "`python -m vicary.assets fetch`."
        ) from exc
    except (OSError, ValueError) as exc:
        raise AssetError(f"cannot read asset manifest at {path}: {exc}") from exc

    records: dict[str, AssetRecord] = {}
    for name, entry in raw.get("assets", {}).items():
        records[name] = AssetRecord(
            name=name,
            format=int(entry["format"]),
            sha256=str(entry["sha256"]),
            bytes=int(entry["bytes"]),
            tiers={str(k): int(v) for k, v in entry.get("tiers", {}).items()},
            cut_date=str(entry.get("cut_date", "")),
            min_package_version=str(entry.get("min_package_version", "0")),
            sources=tuple(entry.get("sources", ())),
        )
    return records


def record_for(name: str = NOTABILITY_ASSET) -> AssetRecord:
    records = manifest()
    if name not in records:
        raise AssetError(
            f"{name!r} is not described in {manifest_path()}; known assets: "
            f"{sorted(records) or '(none)'}"
        )
    return records[name]


def bundled_path(name: str = NOTABILITY_ASSET) -> Path:
    """Path to the copy that ships in the wheel, whether or not it exists."""
    return DATA_DIR / name


def resolve(name: str = NOTABILITY_ASSET) -> tuple[Path, bool]:
    """``(path, is_bundled)`` for ``name``.

    ``VICARY_ASSET_PATH`` wins when set. It may name a file directly or a
    directory holding one — a deployment mounting a whole asset directory should
    not have to spell out the filename the library chose.
    """
    override = config.get(config.ASSET_PATH_ENV_VAR)
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            candidate = candidate / name
        return candidate, False
    return bundled_path(name), True


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AssetError(f"cannot read asset at {path}: {exc}") from exc
    return digest.hexdigest()


def _version_tuple(text: str) -> tuple[int, ...]:
    """Leading numeric components of a version, for ordering.

    Deliberately not a full PEP 440 parser — that would mean a dependency, and
    the only comparison this library makes is "is the installed package at least
    this old release". A trailing ``.dev0``/``rc1`` sorts with its release, which
    is the lenient direction and the right one: refusing to load a good asset
    because the developer is on a pre-release would be the check failing closed
    against its own user.
    """
    parts: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def check_package_version(record: AssetRecord, package_version: str = __version__) -> None:
    """Raise when the installed package is older than the asset requires.

    This is the mismatch that answers wrongly instead of failing: a newer asset,
    whose tier semantics moved, loaded by code that predates the move.
    """
    if _version_tuple(package_version) < _version_tuple(record.min_package_version):
        raise AssetError(
            f"asset {record.name!r} requires vicary >= "
            f"{record.min_package_version} but this is {package_version}. The "
            "asset's tier semantics are newer than this code; upgrade vicary, or "
            f"point {config.ASSET_PATH_ENV_VAR} at an asset this version can read."
        )


@dataclass(frozen=True)
class VerifyReport:
    """What :func:`verify` found. Truthy when everything matched."""

    path: Path
    exists: bool
    expected_sha256: str
    actual_sha256: str
    expected_bytes: int
    actual_bytes: int
    problems: tuple[str, ...]

    def __bool__(self) -> bool:
        return not self.problems


def verify(name: str = NOTABILITY_ASSET, *, path: Path | None = None) -> VerifyReport:
    """Check a bundled asset against the manifest. Reports; does not raise.

    :func:`vicary.gazetteer.load` turns a failing report into an exception. This
    function stays non-raising so the CLI can print every problem at once rather
    than the first one.
    """
    record = record_for(name)
    target = path or bundled_path(name)
    problems: list[str] = []

    if not target.exists():
        return VerifyReport(
            path=target, exists=False,
            expected_sha256=record.sha256, actual_sha256="",
            expected_bytes=record.bytes, actual_bytes=-1,
            problems=(f"asset not found at {target}",),
        )

    actual_bytes = target.stat().st_size
    if actual_bytes != record.bytes:
        problems.append(
            f"size is {actual_bytes} bytes, manifest says {record.bytes}"
        )
    actual_sha = sha256_of(target)
    if actual_sha != record.sha256:
        problems.append(
            f"sha256 is {actual_sha}, manifest says {record.sha256}"
        )
    try:
        check_package_version(record)
    except AssetError as exc:
        problems.append(str(exc))

    return VerifyReport(
        path=target, exists=True,
        expected_sha256=record.sha256, actual_sha256=actual_sha,
        expected_bytes=record.bytes, actual_bytes=actual_bytes,
        problems=tuple(problems),
    )


def describe(path: Path) -> dict:
    """Read an asset's own header — format, metadata, per-tier counts.

    Works on any asset, bundled or not, which is what makes the
    ``VICARY_ASSET_PATH`` override inspectable rather than opaque.
    """
    info: dict = {"path": str(path), "format": None, "meta": {}, "tiers": {}}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("#!"):
                    continue
                head, _, rest = line[2:].rstrip("\n").partition(" ")
                if head == "gazetteer":
                    info["format"] = int(rest.strip())
                elif head == "meta":
                    info["meta"] = json.loads(rest)
                elif head == "tier":
                    tier, _, count = rest.partition(" ")
                    info["tiers"][tier] = int(count)
    except OSError as exc:
        raise AssetError(f"cannot read asset at {path}: {exc}") from exc
    return info


def write_manifest(
    assets: dict[str, dict],
    *,
    path: Path | None = None,
    min_package_version: str = __version__,
) -> Path:
    """(Re)write ``MANIFEST.json`` from assets on disk.

    Called by ``fetch`` after a rebuild. ``min_package_version`` defaults to the
    version doing the writing, which is the correct floor: the code that produced
    an asset is by definition able to read it.
    """
    target = path or manifest_path()
    entries: dict[str, dict] = {}
    payload: dict = {
        "manifest_version": 1,
        "written_by": f"vicary {__version__}",
        "assets": entries,
    }
    for name, extra in assets.items():
        asset = DATA_DIR / name
        described = describe(asset)
        entries[name] = {
            "format": described["format"],
            "sha256": sha256_of(asset),
            "bytes": asset.stat().st_size,
            "tiers": described["tiers"],
            "cut_date": described["meta"].get("cut_date", ""),
            "min_package_version": min_package_version,
            **extra,
        }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

#: Upstreams recorded in the manifest for provenance. Read from the builder so
#: there is one place they are declared.
def _default_sources() -> tuple[str, ...]:
    from vicary.build import gazetteer as builder

    return (builder.SPARQL_ENDPOINT, builder.CENSUS_SURNAMES_URL)


def _cmd_show(args: argparse.Namespace) -> int:
    path, is_bundled = resolve(args.name)
    print(f"asset:      {args.name}")
    print(f"path:       {path}")
    print(f"source:     {'bundled package data' if is_bundled else config.ASSET_PATH_ENV_VAR}")
    if not path.exists():
        print("state:      MISSING")
        return 1
    described = describe(path)
    meta = described["meta"]
    print(f"format:     {described['format']}")
    print(f"cut date:   {meta.get('cut_date', 'unknown')}")
    print(f"bytes:      {path.stat().st_size:,}")
    print("tiers:")
    for tier, count in described["tiers"].items():
        print(f"  {tier:<8} {count:>9,}")
    print(f"  {'total':<8} {sum(described['tiers'].values()):>9,}")
    if is_bundled:
        record = record_for(args.name)
        print(f"manifest:   requires vicary >= {record.min_package_version}; "
              f"installed {__version__}")
        print(f"sources:    {', '.join(record.sources) or 'unrecorded'}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    path, is_bundled = resolve(args.name)
    if not is_bundled:
        # Nothing to compare an override against; the load-time header and
        # tier-count checks are its verification. Say so rather than printing a
        # green line that means nothing.
        described = describe(path)
        print(f"OVERRIDE {path}")
        print(f"  format {described['format']}, "
              f"{sum(described['tiers'].values()):,} entries, "
              f"cut {described['meta'].get('cut_date', 'unknown')}")
        print(f"  not manifest-checked: {config.ASSET_PATH_ENV_VAR} is set, so "
              "this is deliberately not the bundled asset.")
        return 0
    report = verify(args.name)
    if report:
        print(f"OK {report.path}")
        print(f"  sha256 {report.actual_sha256}")
        print(f"  {report.actual_bytes:,} bytes")
        return 0
    print(f"FAILED {report.path}")
    for problem in report.problems:
        print(f"  - {problem}")
    return 1


def _cmd_fetch(args: argparse.Namespace) -> int:
    from vicary.build import gazetteer as builder

    argv: list[str] = []
    if args.stats:
        argv.append("--stats")
    if args.cache_dir:
        argv += ["--cache-dir", args.cache_dir]
    rc = builder.main(argv) or 0
    if rc or args.stats:
        return rc
    written = write_manifest({args.name: {"sources": list(_default_sources())}})
    print(f"manifest rewritten: {written}")
    return _cmd_verify(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vicary.assets",
        description="Inspect, verify, and rebuild vicary's data assets.",
    )
    parser.add_argument("--name", default=NOTABILITY_ASSET,
                        help="asset to act on (default: %(default)s)")
    sub = parser.add_subparsers(dest="command")

    show = sub.add_parser("show", help="print what the installed asset holds")
    show.set_defaults(func=_cmd_show)

    check = sub.add_parser("verify", help="checksum the asset against the manifest")
    check.set_defaults(func=_cmd_verify)

    fetch = sub.add_parser(
        "fetch",
        help="rebuild the asset from its upstreams and rewrite the manifest",
    )
    fetch.add_argument("--stats", action="store_true",
                       help="report what a rebuild would produce; write nothing")
    # Forwarded to the builder. Without this the documented rebuild command
    # re-runs every SPARQL query against donated infrastructure on each attempt,
    # which makes a threshold sweep — the reason the cache exists — cost ~30
    # queries per step instead of one fetch and N offline re-folds.
    fetch.add_argument("--cache-dir", default=None,
                       help="cache raw SPARQL rows here and reuse them "
                            "(delete it after changing a query)")
    fetch.set_defaults(func=_cmd_fetch)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args.func = _cmd_show
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
