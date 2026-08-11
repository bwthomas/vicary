"""Copy the shared asset payload into one front door's package directory.

Every front door vendors the same files from the same two tracked source
directories, and each verifies what *landed* against the manifest. This module is
the Python front door's sync step and the reference the node and ruby ones mirror
(``typescript/scripts/sync-assets.mjs``, ``ruby/scripts/sync_assets.rb``); each
package manager wants its own hook in its own language, so the mechanism is
shared and the invocation is not.

Vendoring rather than fetching at install time is deliberate: "no network, no
per-request cost" is the product claim, and a build-time fetch puts a fetch back
in the story.

The vendored copies are gitignored in all three packages, Python included. A
second *tracked* copy is a second thing to bump per asset cut, which is how two
front doors end up shipping different gazetteers.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from vicary_build import config, lexicon, manifest

#: Built artifacts, from ``asset/data/``.
BUILT = ("notability.txt.gz", manifest.MANIFEST_NAME)


def payload() -> list[tuple[Path, str]]:
    """``(source_dir, filename)`` for every file a front door must carry."""
    files = [(config.DATA_DIR, name) for name in BUILT]
    files += [
        (config.LEXICON_DIR, f"{name}{lexicon.SUFFIX}") for name in lexicon.names()
    ]
    return files


def _verify(target: Path) -> int:
    """Check what is on disk in ``target`` against the manifest beside it.

    Its own function because it must run against bytes it did not just write. A
    successful copy call says the call succeeded; it does not say the bytes on disk
    are the bytes the manifest describes, and a truncated gazetteer loads as a
    SMALLER one — which redacts more, looks privacy-safe, and is invisible to every
    test that only checks that something was masked. The same argument runs the
    other way for a stoplist: a short read there makes the redactor MORE
    aggressive.
    """
    described = json.loads(
        (target / manifest.MANIFEST_NAME).read_text(encoding="utf-8")
    )["assets"]

    # Every manifest entry must have been vendored, and nothing else. Adding an
    # asset without updating a front door's file list would otherwise ship a
    # package whose manifest describes a file it does not carry — which fails at
    # load time for a user, not at build time for us.
    vendored = {name for _, name in payload() if name != manifest.MANIFEST_NAME}
    if vendored != set(described):
        missing = sorted(set(described) - vendored)
        extra = sorted(vendored - set(described))
        print(
            f"vendored payload does not match the manifest for {target}:\n"
            f"  described but not vendored: {missing or 'none'}\n"
            f"  vendored but not described: {extra or 'none'}",
            file=sys.stderr,
        )
        return 1

    for name, entry in sorted(described.items()):
        landed = target / name
        actual = manifest.sha256_of(landed)
        size = landed.stat().st_size
        if size != entry["bytes"]:
            print(
                f"vendored {name} is {size} bytes, manifest says {entry['bytes']}",
                file=sys.stderr,
            )
            return 1
        if actual != entry["sha256"]:
            print(
                f"vendored {name} sha256 {actual} does not match manifest "
                f"{entry['sha256']}",
                file=sys.stderr,
            )
            return 1
        print(f"vendored {name} ({size:,} bytes) — sha256 verified", file=sys.stderr)
    return 0


def vendor(target: Path) -> int:
    """Copy the payload into ``target`` and verify it. Returns a process exit code."""
    for source, _ in payload():
        if not source.is_dir():
            print(
                f"no asset source at {source}\n"
                "This vendors from the monorepo. Outside a checkout there is "
                "nothing to vendor from, and an installed package should already "
                "carry its assets.",
                file=sys.stderr,
            )
            return 2

    target.mkdir(parents=True, exist_ok=True)
    for source, name in payload():
        shutil.copyfile(source / name, target / name)
    return _verify(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vicary_build.vendor",
        description=__doc__,
    )
    parser.add_argument(
        "target",
        type=Path,
        help="package data directory to vendor into, e.g. python/src/vicary/data",
    )
    args = parser.parse_args(argv)
    return vendor(args.target.resolve())


if __name__ == "__main__":
    sys.exit(main())
