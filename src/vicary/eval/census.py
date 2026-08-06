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

The source is the US Census 2010 surname file. Set ``VICARY_EVAL_CENSUS_CSV`` to
a locally-held copy — the distributed ``.zip`` or an extracted ``.csv`` — and this
runs offline and reproducibly. Without it, the measurement needs the network and
the gate skips rather than silently reporting nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    def distinct_rate(self) -> float:
        return 100.0 * self.surnames_matched / self.surnames_scored


def census_source() -> str:
    """Configured path to a local Census surname file, or ``""``."""
    return config.get(config.EVAL_CENSUS_CSV_ENV_VAR)


def load_census(source: str | None = None, *, allow_network: bool = False
                ) -> dict[str, int]:
    """``{normalised surname: bearers}``.

    Prefers a local copy, because a control that only runs with network access is
    a control that stops running. ``allow_network=True`` falls back to the
    download; without it, a missing local copy raises rather than returning a
    partial dict that would read as a lower exposure rate than the truth.
    """
    from vicary.build import gazetteer as builder

    path = source or census_source()
    if path:
        return builder.read_census_surnames(path)
    if allow_network:
        return builder.fetch_census_surnames()
    raise FileNotFoundError(
        "no local Census surname file. Set "
        f"{config.EVAL_CENSUS_CSV_ENV_VAR} to a copy of "
        f"{builder.CENSUS_SURNAMES_MEMBER} (or the .zip from "
        f"{builder.CENSUS_SURNAMES_URL}), or pass allow_network=True."
    )


def measure(census: dict[str, int] | None = None,
            gaz: gazetteer.Gazetteer | None = None,
            *, allow_network: bool = False) -> Exposure:
    """Score the loaded gazetteer's single-token tiers against the Census file."""
    census = census if census is not None else load_census(allow_network=allow_network)
    gaz = gaz or gazetteer.load()

    single_token_places = {n for n in gaz.place if " " not in n}
    single = single_token_places | set(gaz.short)

    bearers_total = sum(census.values())
    bearers_exposed = sum(c for name, c in census.items() if name in single)
    surnames_matched = sum(1 for name in census if name in single)
    via_short = sum(c for name, c in census.items() if name in gaz.short)
    via_place = sum(c for name, c in census.items() if name in single_token_places)

    return Exposure(
        surnames_scored=len(census),
        surnames_matched=surnames_matched,
        bearers_total=bearers_total,
        bearers_exposed=bearers_exposed,
        bearers_via_short=via_short,
        bearers_via_place=via_place,
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
