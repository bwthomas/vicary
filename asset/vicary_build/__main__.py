"""``python -m vicary_build`` — the build mechanism's front door.

Two verbs, because the build produces two distinct things a person wants
separately: ``fetch`` rebuilds the asset from its upstreams and rewrites the
manifest, and ``vendor`` copies the tracked payload into one package.

``fetch`` rewrites the manifest and then verifies the file it just wrote, in that
order and unconditionally. The failure this guards against has happened: a build
that wrote to a path nothing read, checksummed the *old* asset, verified that, and
printed a pass. A rebuild that changes nothing and reports success is worse than
one that fails.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vicary_build import config, lexicon, manifest, reference, vendor

# `gazetteer` is imported per-command, not here. It loads the stoplist at module
# scope, so importing it up front makes every verb — including the one whose job
# is to rewrite that file and its declared count — fail to start whenever the
# count is stale. Which is precisely when somebody reaches for `lexicon`.


def _cmd_fetch(args: argparse.Namespace) -> int:
    from vicary_build import gazetteer

    forwarded: list[str] = []
    if args.stats:
        forwarded.append("--stats")
    if args.cache_dir:
        forwarded += ["--cache-dir", args.cache_dir]
    if args.out:
        forwarded += ["--out", args.out]
    rc = gazetteer.main(forwarded) or 0
    if rc or args.stats:
        return rc
    if args.out:
        # Wrote somewhere other than the canonical directory, so the manifest
        # there does not describe it. Say so instead of rewriting a manifest
        # against a file the caller deliberately put elsewhere.
        print(
            "manifest NOT rewritten: --out wrote outside the canonical "
            f"{config.DATA_DIR}, so the manifest would describe a "
            "different file than the one just built.",
            file=sys.stderr,
        )
        return 0
    # Before the manifest, because a rebuilt `given` tier changes which plurals
    # the lexicon may emit, and a manifest that checksummed the pre-rebuild word
    # list would describe a file this command is about to change.
    rc = _cmd_lexicon(args)
    if rc:
        return rc
    written = manifest.write(
        rebuilt={gazetteer.ASSET_NAME},
        sources=(gazetteer.SPARQL_ENDPOINT, gazetteer.CENSUS_SURNAMES_URL),
    )
    print(f"manifest rewritten: {written}")
    return 0


def _cmd_lexicon(args: argparse.Namespace) -> int:
    """Rebuild the generated inflection region of every authored word list.

    Separate from `fetch` as well as called by it. A lexicon regenerates from two
    tracked reference tables and touches no network, so somebody who has just
    added a stop word can run this in a second — where making it fetch-only would
    put a ~30-query SPARQL rebuild between them and a one-word edit, and the
    predictable result is a hand-edited generated block.
    """
    try:
        veto = reference.veto()
    except reference.ReferenceError as exc:
        print(exc, file=sys.stderr)
        return 2
    for name in lexicon.names():
        path, changed = lexicon.rewrite(name, veto=veto)
        entries = len(lexicon.load(name))
        state = "rewritten" if changed else "unchanged"
        print(f"{path.name} {state} — {entries} distinct words", file=sys.stderr)
    return 0


def _cmd_manifest(args: argparse.Namespace) -> int:
    """Refresh the manifest without rebuilding, for when a lexicon changed.

    Deliberately does NOT raise any existing asset's `min_package_version`: see
    `manifest.write`. Editing a word list is not a reason to lock older installs
    out of a gazetteer they can read.
    """
    written = manifest.write()
    print(f"manifest rewritten: {written}")
    return 0


def _cmd_vendor(args: argparse.Namespace) -> int:
    return vendor.vendor(args.target.resolve())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m vicary_build", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser(
        "fetch", help="rebuild the asset from its upstreams and rewrite the manifest"
    )
    fetch.add_argument(
        "--stats", action="store_true",
        help="report what a rebuild would produce; write nothing",
    )
    fetch.add_argument(
        "--out", default=None,
        help="write the asset here instead of the canonical asset/data/",
    )
    # Without this a threshold sweep re-runs every SPARQL query against donated
    # infrastructure on each step, which costs ~30 queries per step instead of one
    # fetch and N offline re-folds.
    fetch.add_argument(
        "--cache-dir", default=None,
        help="cache raw SPARQL rows here and reuse them "
             "(delete it after changing a query)",
    )
    fetch.set_defaults(func=_cmd_fetch)

    inflect = sub.add_parser(
        "lexicon",
        help="rebuild each word list's generated inflections from the "
             "tracked census and given-name tables",
    )
    inflect.set_defaults(func=_cmd_lexicon)

    refresh = sub.add_parser(
        "manifest", help="re-checksum the tracked payload without rebuilding it"
    )
    refresh.set_defaults(func=_cmd_manifest)

    vend = sub.add_parser("vendor", help="copy the payload into one package")
    vend.add_argument("target", type=Path)
    vend.set_defaults(func=_cmd_vendor)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
