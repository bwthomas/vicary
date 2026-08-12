"""The false-positive control the fixture cannot provide.

The fixture reports zero leaks on bare surnames partly because the private
surnames in it are rare — Okonkwo, Bramwell, Pritchard, Ybarra. A clean control
needs an *unlikely* clean, so this scores the single-token tiers against **every
American surname**: the population-weighted rate at which a bare surname resolves
notable, regardless of whose surname it is.

Read the headline number as: *for a private person named by bare surname only —
no first name, no title, no same-document corroboration — this share resolves
"notable" and leaks.* It is conditional on that surface form, which is a minority
of private-name mentions in real prose, so it is not an essay-level leak rate. It
is the right number for deciding what belongs in the short tier, and it is the
number that moves when that tier's thresholds move.

The source is the US Census 2010 surname file, and this repository now ships the
two columns of it this measurement uses — see ``conformance/census/``, built by
``tools/census_build.py``. So the gate is measured on a bare checkout and in CI,
which it was not: census.gov stopped serving the upstream, and the gate reported
NOT MEASURED everywhere but on a machine holding a hand-downloaded copy.

``VICARY_EVAL_CENSUS_CSV`` still wins when set — an operator holding a newer
release gets the number their file gives — and accepts the distributed ``.zip`` or
an extracted ``.csv``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from vicary import config, gazetteer


@dataclass(frozen=True)
class Exposure:
    """How much of the US surname population the single-token tiers claim."""

    #: Distinct surnames in the Census file.
    surnames_scored: int
    #: Distinct surnames matching some single-token tier.
    surnames_matched: int
    #: Total bearers across the file.
    bearers_total: int
    #: Bearers whose surname matches some single-token tier.
    bearers_exposed: int
    #: Bearers exposed via the ``short`` tier specifically.
    bearers_via_short: int
    #: Bearers exposed via a single-token ``place`` entry.
    bearers_via_place: int
    #: Bearers exposed via the ``demonym`` tier. Counted here for the same reason
    #: the other two are: it is a KEEP granted to a bare single token, which is
    #: exactly the surface form this control measures. Leaving it out would make
    #: adding a tier look free.
    bearers_via_demonym: int = 0

    @property
    def rate(self) -> float:
        """Population-weighted exposure, as a percentage. The headline."""
        return 100.0 * self.bearers_exposed / self.bearers_total

    @property
    def short_rate(self) -> float:
        return 100.0 * self.bearers_via_short / self.bearers_total

    @property
    def place_rate(self) -> float:
        return 100.0 * self.bearers_via_place / self.bearers_total

    @property
    def demonym_rate(self) -> float:
        return 100.0 * self.bearers_via_demonym / self.bearers_total

    @property
    def distinct_rate(self) -> float:
        return 100.0 * self.surnames_matched / self.surnames_scored


#: Directory under ``conformance/`` holding the shipped table and its provenance.
SHIPPED_DIRNAME = "census"
SHIPPED_TABLE_FILENAME = "surnames.txt.gz"
SHIPPED_PROFILE_FILENAME = "profile.json"

#: The row-count floor, matching the builder's. This list is scored *against* the
#: single-token tiers, so a short read shrinks the denominator and reports a more
#: comfortable exposure rate than the truth.
MINIMUM_ROWS = 100_000


def census_source() -> str:
    """Configured path to a local Census surname file, or ``""``."""
    return config.get(config.EVAL_CENSUS_CSV_ENV_VAR)


def shipped_dir() -> Path | None:
    """``conformance/census/``, or ``None`` outside a checkout."""
    from vicary.eval import conformance as conf

    root = conf.conformance_dir()
    if root is None:
        return None
    candidate = root / SHIPPED_DIRNAME
    return candidate if candidate.is_dir() else None


def load_shipped_census(directory: Path | None = None) -> dict[str, int]:
    """``{normalised surname: bearers}`` from the table this repository ships.

    The digest in ``profile.json`` is checked, not trusted. This table is used to
    SUBTRACT exposure from a permissive tier, so a truncated or edited copy scores
    the gazetteer against a smaller America and reads as a *better* number — the
    one direction this measurement must never fail in quietly. A bad digest raises
    rather than degrading.
    """
    found = directory or shipped_dir()
    if found is None:
        raise FileNotFoundError(
            f"no conformance/{SHIPPED_DIRNAME}/ above this module. The shipped "
            "table lives in the repository, not in an installed package."
        )

    payload = (found / SHIPPED_TABLE_FILENAME).read_bytes()
    profile = json.loads(
        (found / SHIPPED_PROFILE_FILENAME).read_text(encoding="utf-8")
    )
    expected = profile.get("table", {}).get("sha256", "")
    actual = hashlib.sha256(payload).hexdigest()
    if expected and actual != expected:
        raise ValueError(
            f"{SHIPPED_TABLE_FILENAME} has sha256 {actual}, but "
            f"{SHIPPED_PROFILE_FILENAME} pins {expected}. Refusing to score the "
            "gazetteer against a table that is not the one this repository "
            "measured, because a short read reads as a better number. Rebuild "
            "with `python tools/census_build.py --write`."
        )

    counts: dict[str, int] = {}
    for line in gzip.decompress(payload).decode("utf-8").splitlines():
        name, _, bearers = line.partition("\t")
        if name:
            counts[name] = int(bearers)
    if len(counts) < MINIMUM_ROWS:
        raise ValueError(
            f"{SHIPPED_TABLE_FILENAME} parsed to only {len(counts):,} rows; "
            f"expected at least {MINIMUM_ROWS:,}."
        )
    return counts


def load_census(source: str | None = None, *, allow_network: bool = False
                ) -> dict[str, int]:
    """``{normalised surname: bearers}``.

    Resolution, in order:

    1. An explicit ``source``, or ``VICARY_EVAL_CENSUS_CSV``. An operator holding
       a newer Census release still wins, and gets the number *their* file gives.
    2. The table shipped in ``conformance/census/``, which is the same 162,253
       rows the 2010 release carries and therefore the same rate to the last
       bearer. This is why the gate no longer skips on a bare checkout.

    ``allow_network=True`` is kept for a caller that wants the upstream directly,
    but it is no longer a fallback anything reaches: census.gov answers the
    documented URL with a WAF rejection page under a 200 status, and the shipped
    table exists precisely because that fetch cannot be relied on.
    """
    from vicary_build import gazetteer as builder

    path = source or census_source()
    if path:
        return builder.read_census_surnames(path)
    if shipped_dir() is not None:
        return load_shipped_census()
    if allow_network:
        return builder.fetch_census_surnames()
    raise FileNotFoundError(
        f"no conformance/{SHIPPED_DIRNAME}/ in this tree and no "
        f"{config.EVAL_CENSUS_CSV_ENV_VAR} set. Point that at a copy of "
        f"{builder.CENSUS_SURNAMES_MEMBER}, or run from a checkout."
    )


def measure(census: dict[str, int] | None = None,
            gaz: gazetteer.Gazetteer | None = None,
            *, allow_network: bool = False) -> Exposure:
    """Score the loaded gazetteer's single-token tiers against the Census file."""
    census = census if census is not None else load_census(allow_network=allow_network)
    gaz = gaz or gazetteer.load()

    single_token_places = {n for n in gaz.place if " " not in n}
    single = single_token_places | set(gaz.short) | set(gaz.demonym)

    bearers_total = sum(census.values())
    bearers_exposed = sum(c for name, c in census.items() if name in single)
    surnames_matched = sum(1 for name in census if name in single)
    via_short = sum(c for name, c in census.items() if name in gaz.short)
    via_place = sum(c for name, c in census.items() if name in single_token_places)
    via_demonym = sum(c for name, c in census.items() if name in gaz.demonym)

    return Exposure(
        surnames_scored=len(census),
        surnames_matched=surnames_matched,
        bearers_total=bearers_total,
        bearers_exposed=bearers_exposed,
        bearers_via_short=via_short,
        bearers_via_place=via_place,
        bearers_via_demonym=via_demonym,
    )


def render(exposure: Exposure) -> str:
    """The report block, for a CLI or a gate's failure message."""
    return "\n".join(
        (
            "BARE-SURNAME FALSE-POSITIVE RATE (US Census 2010 surname file)",
            f"  distinct surnames scored   {exposure.surnames_scored:,}",
            f"  any single-token tier hit  {exposure.surnames_matched:,} "
            f"({exposure.distinct_rate:.2f}% of distinct)",
            f"  population-weighted rate   {exposure.rate:.2f}% "
            f"({exposure.bearers_exposed:,} / {exposure.bearers_total:,} bearers)",
            f"    via the short tier       {exposure.short_rate:.2f}%",
            f"    via single-token places  {exposure.place_rate:.2f}%",
            f"    via the demonym tier     {exposure.demonym_rate:.2f}%",
            "  reads as: for a private person named by BARE SURNAME ONLY — no",
            "            first name, no title, no corroboration — this share",
            "            resolves 'notable'. Conditional on that surface form.",
        )
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m vicary.eval.census",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
    )
    parser.add_argument("--census", default="",
                        help="path to the Census surname .zip or .csv "
                             f"(default: {config.EVAL_CENSUS_CSV_ENV_VAR})")
    parser.add_argument("--allow-network", action="store_true",
                        help="download the file if no local copy is configured")
    args = parser.parse_args(argv)

    census = load_census(args.census or None, allow_network=args.allow_network)
    print(render(measure(census)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
