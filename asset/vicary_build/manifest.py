"""Write ``asset/data/MANIFEST.json`` — the checksum every front door checks.

Writing lives here and reading lives in each front door, deliberately. The
manifest is how three implementations prove they loaded the same bytes, and a
library that could rewrite it could also paper over a mismatch it caused: the
check becomes a check of the library against itself. So the only thing that
produces a manifest is the thing that produces the assets.

This used to be ``vicary.assets.write_manifest``, called by a ``fetch``
subcommand on the Python library. That put the fetch mechanism — and the network,
and the SPARQL endpoints — inside one of the three packages, where the other two
could not reach it and where a host installing the library got it whether it
wanted it or not.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from vicary_build import config, lexicon

MANIFEST_NAME = "MANIFEST.json"

#: 1 MiB: large enough that the syscall count is irrelevant, small enough that a
#: 2 MB asset never doubles peak memory.
_HASH_CHUNK = 1 << 20


def manifest_path() -> Path:
    return config.DATA_DIR / MANIFEST_NAME


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe_gazetteer(path: Path) -> dict:
    """Format number, per-tier counts and metadata header of a built gazetteer.

    Read back off disk rather than carried over from the build, so the numbers in
    the manifest describe the file that exists rather than the one the build
    believes it wrote.
    """
    tiers: dict[str, int] = {}
    format_version = 0
    meta: dict = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("#!"):
                continue
            # `partition`, not `split` — the `#!meta` payload is JSON and contains
            # spaces, so any maxsplit that leaves room for a third field truncates
            # it into invalid JSON.
            head, _, rest = line[2:].rstrip("\n").partition(" ")
            if head == "gazetteer":
                format_version = int(rest.strip())
            elif head == "meta":
                meta = json.loads(rest)
            elif head == "tier":
                tier, _, count = rest.partition(" ")
                tiers[tier] = int(count)
    return {"format": format_version, "tiers": tiers, "meta": meta}


def describe_lexicon(name: str) -> dict:
    """Format number and distinct-word count of an authored lexicon."""
    return {
        "format": lexicon.LEXICON_FORMAT,
        "entries": len(lexicon.load(name)),
    }


def existing(path: Path | None = None) -> dict[str, dict]:
    """The manifest entries currently on disk, or ``{}`` if there is no manifest."""
    target = path or manifest_path()
    try:
        return dict(json.loads(target.read_text(encoding="utf-8"))["assets"])
    except (FileNotFoundError, KeyError, ValueError):
        return {}


def write(
    *,
    rebuilt: frozenset[str] | set[str] | None = None,
    sources: tuple[str, ...] = (),
    path: Path | None = None,
) -> Path:
    """(Re)write the manifest from what is on disk.

    ``min_package_version`` is the field that turns a semantics change into a loud
    failure instead of a quiet difference in answers: every front door refuses to
    load an asset naming a floor above its own version. So it is only *raised* for
    an asset named in ``rebuilt``, plus any asset the manifest has never described.

    A refresh must not raise it for an asset it did not change. The 0.1.0
    gazetteer is readable by 0.1.0, and a manifest rewritten by a later version
    that helpfully stamped its own number there would make every 0.1.0 install
    refuse a perfectly good file — the check failing closed against its own user,
    for a reason no error message would explain. ``sources`` is preserved for the
    same reason: dropping provenance is a silent loss, and a manifest refresh is
    not where anybody is looking for it.
    """
    changed = set(rebuilt or ())
    previous = existing(path)
    version = config.version()

    def floor(name: str, format_version: int) -> str:
        if name in changed or name not in previous:
            return version
        # A format change is what the floor is *for*: older code reading a newer
        # format is the mismatch that answers plausibly and wrongly, where a
        # missing asset raises on its own. Content changes within a format do not
        # qualify — editing a word list is not a reason to lock older installs out.
        if int(previous[name].get("format", format_version)) != format_version:
            return version
        return str(previous[name].get("min_package_version", version))

    entries: dict[str, dict] = {}

    gazetteer = config.DATA_DIR / "notability.txt.gz"
    described = describe_gazetteer(gazetteer)
    entries[gazetteer.name] = {
        "format": described["format"],
        "sha256": sha256_of(gazetteer),
        "bytes": gazetteer.stat().st_size,
        "tiers": described["tiers"],
        "cut_date": described["meta"].get("cut_date", ""),
        "min_package_version": floor(gazetteer.name, described["format"]),
        "sources": list(
            sources or previous.get(gazetteer.name, {}).get("sources", ())
        ),
    }

    # Checksummed where they are authored, not from a staged copy. A second
    # tracked copy inside `data/` would be a second thing to bump per cut, which
    # is exactly how two front doors end up with different word lists.
    for name in lexicon.names():
        authored = lexicon.lexicon_path(name)
        described = describe_lexicon(name)
        entries[authored.name] = {
            "format": described["format"],
            "sha256": sha256_of(authored),
            "bytes": authored.stat().st_size,
            "entries": described["entries"],
            "min_package_version": floor(authored.name, described["format"]),
        }

    target = path or manifest_path()
    payload = {
        "manifest_version": 1,
        "written_by": f"vicary {version}",
        "assets": entries,
    }
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target
