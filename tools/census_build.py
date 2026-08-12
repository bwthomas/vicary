"""Build the shipped surname table the bare-surname gate is scored against.

Why this exists. `bare-surname exposure` is the one gate that still needed a file
no package shipped, and it is the false-positive control the essay fixture cannot
provide: it scores every single-token tier in the gazetteer against **every
American surname**, population-weighted. Without it a new short-tier threshold can
be adopted with nothing measuring what it costs.

The file it needed was the US Census 2010 surname release, fetched at build time.
That stopped working. As of 2026-08-11 census.gov answers the documented URL with
HTTP **200** and a WAF rejection page in the body, under any User-Agent — so the
status code is not evidence of anything, and there is no unattended fetch left to
write. The practical consequence was that one gate reported NOT MEASURED on every
machine but the one holding a hand-downloaded copy, CI included, and a board of
eight greens and one blank reads very much like a board of nine greens.

So the derivation is codified here and its product ships. This reads a local copy
of the Census release and writes the two columns the measurement actually uses —
normalised surname, and number of US bearers — as a sorted, gzipped table under
`conformance/census/`. That is 787 KB against the 9.4 MB original, and it is not a
sample: all 162,253 rows are kept, so the measured rate is the same number to the
last bearer. A truncated table would change the numerator and the denominator at
once and quietly redefine what the 1.25% bar means.

**It ships in the repository and in none of the three packages.** `conformance/`
is excluded from the wheel, the gem and the npm tarball exactly as it was before,
so no installed package grew — this is a measurement input, like `frames.json`,
not a runtime asset.

**On provenance, which is the part that has to survive census.gov.** The upstream
is a US Government work and therefore public domain, so redistribution needs no
grant. What it does need is a trail: `profile.json` pins the sha256 of the source
this table was derived from and of the table itself, and the reader checks the
latter on load, so a table edited or truncated in place fails loudly rather than
scoring the gazetteer against a smaller America.

Run it when the upstream release changes, never as part of a test:

    export VICARY_EVAL_CENSUS_CSV=/path/to/Names_2010Census.csv
    python tools/census_build.py --write
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where the derived table and its provenance live. Under `conformance/` because
#: this is a shared measurement input all three ports read, like `frames.json`,
#: and because that directory ships in no package.
OUTPUT_DIR = REPO_ROOT / "conformance" / "census"
TABLE_NAME = "surnames.txt.gz"
PROFILE_NAME = "profile.json"
NOTICE_NAME = "NOTICE"

#: The release these bytes come from. Named for the trail, not for fetching:
#: census.gov serves a WAF rejection page under a 200 status, so nothing here
#: reaches the network and the source must be supplied locally.
UPSTREAM_URL = "https://www2.census.gov/topics/genealogy/2010surnames/names.zip"
UPSTREAM_MEMBER = "Names_2010Census.csv"

#: sha256 of the two forms of the upstream this table has been built from — the
#: distributed archive and the member extracted out of it. Pinned so a rebuild
#: that silently got different bytes is a loud failure rather than a quietly
#: different denominator. A copy that matches neither is not refused, only
#: reported: census.gov may yet publish a 2020 release, and the point of pinning
#: is to make that visible in the diff rather than to forbid it.
KNOWN_UPSTREAM_SHA256 = {
    "117c41cb4668727b7627b2845b6df3f83eb2a22a1813f42c0ff4bdcab86de135": (
        "names.zip, the 2010 release as distributed"
    ),
    "6b4933178060b4e5ac8dd9a611ec15472d35eab98fe9027474bc5ccc5b7a8708": (
        f"{UPSTREAM_MEMBER}, extracted from that archive"
    ),
}

#: The same floor `vicary_build.gazetteer.parse_census_surnames` enforces, for
#: the same reason: this list is used to SUBTRACT from a permissive tier, so a
#: short read makes the gazetteer more permissive rather than less — the wrong
#: direction to fail in silently.
MINIMUM_ROWS = 100_000

#: Env var naming the operator's local copy of the upstream.
SOURCE_ENV_VAR = "VICARY_EVAL_CENSUS_CSV"


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def encode(counts: dict[str, int]) -> bytes:
    """The shipped table: ``name<TAB>bearers`` per line, sorted, gzipped.

    Sorted by name rather than by rank so the file is diffable and so a rebuild
    from a reordered upstream produces identical bytes. ``mtime=0`` for the same
    reason — a gzip header carrying the build clock would make every rebuild look
    like a changed table.
    """
    body = "".join(f"{name}\t{bearers}\n" for name, bearers in sorted(counts.items()))
    return gzip.compress(body.encode("utf-8"), 9, mtime=0)


def decode(payload: bytes) -> dict[str, int]:
    """``{normalised surname: bearers}`` from the shipped table.

    The inverse of :func:`encode`, and the reference the two other ports'
    readers are checked against.
    """
    counts: dict[str, int] = {}
    for line in gzip.decompress(payload).decode("utf-8").splitlines():
        name, _, bearers = line.partition("\t")
        if name:
            counts[name] = int(bearers)
    return counts


def load_upstream(source: str) -> tuple[dict[str, int], str]:
    """Parse the operator's local Census copy. Returns ``(counts, sha256)``."""
    from vicary_build import gazetteer as builder

    path = Path(source).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    upstream_sha256 = digest(path.read_bytes())
    note = KNOWN_UPSTREAM_SHA256.get(upstream_sha256)
    if note:
        print(f"source: {path.name} — {note}")
    else:
        print(
            f"source: {path.name} — sha256 {upstream_sha256} matches no pinned "
            "release. This is allowed, but it changes the denominator every gate "
            "number is scored against; read the profile.json diff.",
            file=sys.stderr,
        )
    return builder.read_census_surnames(path), upstream_sha256


def build_profile(counts: dict[str, int], table: bytes,
                  upstream_sha256: str) -> dict[str, Any]:
    """Provenance and the totals a reader can check itself against."""
    return {
        "document_version": 1,
        "id": "us-census-2010-surnames",
        "name": "US Census 2010 surname frequencies",
        "description": (
            "Every surname borne by 100 or more people at the 2010 US census, "
            "with bearer counts. The false-positive control the bare-surname "
            "exposure gate is scored against."
        ),
        "license": {
            "id": "public-domain-us-gov",
            "statement": (
                "A work of the US Census Bureau, and therefore a US Government "
                "work in the public domain. No grant is required to redistribute "
                "it; see NOTICE."
            ),
        },
        "source": {
            "kind": "derived",
            "upstream_url": UPSTREAM_URL,
            "upstream_member": UPSTREAM_MEMBER,
            "upstream_sha256": upstream_sha256,
            "transform": (
                "Two columns kept — `name` lowercased and whitespace-normalised, "
                "and `count`. The `ALL OTHER NAMES` aggregate row is dropped, as "
                "it names nobody. Every other row is kept: this is the whole "
                "release's two load-bearing columns, not a sample, so the "
                "measured rate is identical to the number the upstream gives."
            ),
            "reproducer": "tools/census_build.py",
            "why_not_fetched": (
                "census.gov answers UPSTREAM_URL with HTTP 200 and a WAF "
                "rejection page in the body under any User-Agent, so there is no "
                "unattended fetch to write and the status code cannot be trusted. "
                "Supply a local copy via " + SOURCE_ENV_VAR + "."
            ),
        },
        "table": {
            "file": TABLE_NAME,
            "format": (
                "gzip of UTF-8 `name<TAB>bearers` lines, sorted by name ascending"
            ),
            "sha256": digest(table),
            "gzip_bytes": len(table),
            "surnames": len(counts),
            "bearers_total": sum(counts.values()),
            "minimum_rows": MINIMUM_ROWS,
        },
        "why_this_file": (
            "The reader checks `table.sha256` on load. This table is used to "
            "SUBTRACT exposure from a permissive tier, so a truncated or edited "
            "copy would score the gazetteer against a smaller America and read as "
            "a better number — the one direction this must never fail in quietly."
        ),
    }


NOTICE_TEXT = """\
US Census 2010 surname frequencies — redistributed in this repository
=====================================================================

`surnames.txt.gz` in this directory is derived from the US Census Bureau's 2010
surname release. It is not vicary's work.

    Frequently Occurring Surnames from the 2010 Census
    US Census Bureau
    {url}

It is a work of the United States Government and is therefore in the public
domain. No licence grant is required to redistribute it, and none is claimed
over it here; the rest of this repository is MIT-licensed.

What it is for: the `bare-surname exposure` gate scores the gazetteer's
single-token tiers against every American surname, population-weighted. That is
the false-positive control the essay fixture cannot provide, and it is the number
that moves when a single-token threshold moves.

Why a derived copy rather than the original: census.gov stopped serving the file.
As of 2026-08-11 the URL above answers with HTTP 200 and a WAF rejection page in
the body under any User-Agent, so a build-time fetch cannot work and a status-code
check would call the rejection a success. The gate reported NOT MEASURED
everywhere but on a machine holding a hand-downloaded copy.

What was changed: two columns were kept — the surname, lowercased and
whitespace-normalised, and its bearer count — and the `ALL OTHER NAMES` aggregate
row, which names nobody, was dropped. Nothing else. All {surnames:,} remaining
rows are present, so this is the whole release's load-bearing content rather than
a sample, and the rate measured from it equals the rate measured from the
original to the last bearer. `profile.json` pins the digest of the source it came
from and `tools/census_build.py` reproduces it.

This directory ships in the repository and in none of the three packages. The
wheel, the gem and the npm tarball all exclude `conformance/`, as they did before
this file existed.
"""


def write(counts: dict[str, int], upstream_sha256: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    table = encode(counts)
    profile = build_profile(counts, table, upstream_sha256)

    (OUTPUT_DIR / TABLE_NAME).write_bytes(table)
    (OUTPUT_DIR / PROFILE_NAME).write_text(
        json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUTPUT_DIR / NOTICE_NAME).write_text(
        NOTICE_TEXT.format(url=UPSTREAM_URL, surnames=len(counts)), encoding="utf-8"
    )

    relative = OUTPUT_DIR.relative_to(REPO_ROOT)
    print(f"wrote {relative}/{TABLE_NAME}  {len(table):,} bytes")
    print(f"      {len(counts):,} surnames, {sum(counts.values()):,} bearers")
    print(f"      sha256 {profile['table']['sha256']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/census_build.py",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
    )
    parser.add_argument(
        "--source",
        default="",
        help=f"the Census .zip or .csv (default: ${SOURCE_ENV_VAR})",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the table, profile and NOTICE; otherwise report and stop",
    )
    args = parser.parse_args(argv)

    source = args.source or os.environ.get(SOURCE_ENV_VAR, "")
    if not source:
        parser.error(
            f"no Census surname file. Set {SOURCE_ENV_VAR} or pass --source. "
            f"The upstream is {UPSTREAM_URL}, which census.gov no longer serves "
            "to an unattended client — see the module docstring."
        )

    counts, upstream_sha256 = load_upstream(source)
    if len(counts) < MINIMUM_ROWS:
        print(
            f"parsed only {len(counts):,} rows; expected ~162k. Refusing to ship "
            "a truncated table, because it would score the gazetteer against a "
            "smaller America and read as a better number.",
            file=sys.stderr,
        )
        return 1

    if not args.write:
        table = encode(counts)
        print(f"{len(counts):,} surnames, {sum(counts.values()):,} bearers")
        print(f"would write {len(table):,} gzipped bytes, sha256 {digest(table)}")
        print("pass --write to write it")
        return 0

    write(counts, upstream_sha256)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
