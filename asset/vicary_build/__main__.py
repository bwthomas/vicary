"""``python -m vicary_build`` — the build mechanism's front door.

Verbs, because the build produces distinct things a person wants separately:
``fetch`` rebuilds the asset from its upstreams and rewrites the manifest, and
``vendor`` copies the tracked payload into one package. ``lexicon`` and
``given-tiers`` re-cut one tracked artifact each from a LOCAL source, without a
network sweep — see their own help for why that is a verb rather than a flag.

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


def _cmd_given_tiers(args: argparse.Namespace) -> int:
    """Re-cut the two SSA-derived tiers of the existing asset, in place.

    Separate from `fetch` for the same reason `lexicon` is, one layer down. The
    given-name tiers come from a local, offline archive; every other tier comes
    from a live ~30-query SPARQL sweep. So running `fetch` to move a births
    floor re-cuts seven tiers nobody meant to touch, and they move with whatever
    Wikidata did since the last cut — which makes the floor change unmeasurable,
    because the thing under test is no longer the only thing that differs.
    """
    from vicary_build import gazetteer

    source = args.ssa or config.get(config.SSA_NAMES_ZIP_ENV_VAR)
    if not source:
        print(
            "--ssa, or "
            f"{config.SSA_NAMES_ZIP_ENV_VAR}, must name a local copy of the SSA "
            "baby-names archive: ssa.gov answers 403 to some networks on every "
            "path, so there is no download to fall back on.",
            file=sys.stderr,
        )
        return 2
    births = gazetteer.read_ssa_given_names(source)
    path = config.DATA_DIR / gazetteer.ASSET_NAME
    written, counts = gazetteer.rebuild_given_tiers(path, births)
    for tier, entries in counts.items():
        print(f"tier {tier}: {entries:,} entries", file=sys.stderr)
    print(f"{path.name} rewritten: {written:,} bytes", file=sys.stderr)
    rewritten = manifest.write(rebuilt={gazetteer.ASSET_NAME})
    print(f"manifest rewritten: {rewritten}")
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

    recut = sub.add_parser(
        "given-tiers",
        help="re-cut the two SSA-derived given-name tiers in place, offline",
    )
    recut.add_argument(
        "--ssa", default=None,
        help="local names.zip or extracted directory; defaults to "
             f"${config.SSA_NAMES_ZIP_ENV_VAR}",
    )
    recut.set_defaults(func=_cmd_given_tiers)

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
