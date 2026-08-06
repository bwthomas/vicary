"""Public figures a school essay names, held out from the tuning set.

Why this file exists
--------------------
The short (bare-surname) tier's two thresholds were both chosen while the redact
literals in :mod:`vicary.eval.fixture` were visible —
:data:`~vicary.build.gazetteer.SHORT_MIN_SITELINKS` sits in the gap between
the highest private-name surname in that fixture (84, "Bell") and the lowest
required KEEP (145, "Thoreau"), and
:data:`~vicary.build.gazetteer.SHORT_MAX_US_SURNAME_POPULATION` was set at
25,000 partly to leave "Lincoln" (16,477 bearers) a margin. The builder's own
docstring says so, and says the honest test is a held-out surname list.

This is that list. It matters because the fixture's KEEP frames exercise **two**
bare surnames, Lincoln and Washington, both of which clear; KEEP precision reads
100% and generalises to nothing.

How it was selected, so it stays held out
-----------------------------------------
The rule is *domain-first, gazetteer-blind*: enumerate the figures a US school
essay actually names — the authors on a ninth-grade reading list, the presidents
and founders, the scientists, the civil-rights figures, the artists and athletes
a personal essay reaches for — and write down the surname each one is written by.
No entry was added, removed, or reworded after seeing whether it resolves, and
none of the fixture's KEEP figures appear here (van Gogh, Lincoln, Washington,
Thoreau, Morrison, Joan of Arc, Gladwell, Parks, Atticus Finch are all excluded
by construction). Adding an entry *because* it fails, or dropping one because it
is awkward, converts this back into a tuning set and forfeits the whole point.

There is exactly one legitimate reason to remove an entry, and it has already
been used twice: **the fixture grew a frame naming that figure.** Richard Wright
and Jackie Robinson were both on this list until
``notable-surname-established-in-document`` and
``private-surname-shadowed-by-a-notable-one`` were added, at which point they
became visible and had to go — a figure the fixture mentions is not held out from
the fixture, and leaving them would double-count the same evidence in two places
while the list still called itself blind. Nothing is lost, because those frames
now score both figures exactly. ``test_the_held_out_list_is_disjoint_from_the_
fixture`` enforces the boundary against every frame's whole sentence, not just
its span literals, which is how the collision was caught rather than assumed.

Both directions are scored
--------------------------
A KEEP-only list is passed by a gazetteer that keeps everything, which would be
a catastrophic redactor. The negative control is the population-weighted Census
false-positive rate — ``python3 scratch/pii/gazetteer_coverage.py --census``,
currently 0.9% of US surname-bearers — and any change that lifts the numbers here
has to be quoted against that number moving. The asymmetry to respect is the one
the builder names: a false KEEP costs *recall*, which is the leg this effort
closed, so recovering a figure is worth less than leaking a classmate costs.
"""

from __future__ import annotations

from typing import NamedTuple


class Figure(NamedTuple):
    """A public figure, and the surface form a student writes them by."""

    surname: str
    full_name: str
    domain: str


#: Figures a school essay names. See the module docstring for the selection rule.
HELD_OUT_FIGURES: tuple[Figure, ...] = (
    # Authors and poets — the reading-list population, and the one most often
    # written by bare surname because literary analysis convention requires it
    # ("as Frost shows", "Baldwin argues"). This is why the tier matters at all.
    Figure("Steinbeck", "John Steinbeck", "author"),
    Figure("Hemingway", "Ernest Hemingway", "author"),
    Figure("Fitzgerald", "F. Scott Fitzgerald", "author"),
    Figure("Orwell", "George Orwell", "author"),
    Figure("Shakespeare", "William Shakespeare", "author"),
    Figure("Dickens", "Charles Dickens", "author"),
    Figure("Twain", "Mark Twain", "author"),
    Figure("Angelou", "Maya Angelou", "author"),
    Figure("Hurston", "Zora Neale Hurston", "author"),
    Figure("Baldwin", "James Baldwin", "author"),
    Figure("Ellison", "Ralph Ellison", "author"),
    Figure("Salinger", "J. D. Salinger", "author"),
    Figure("Bradbury", "Ray Bradbury", "author"),
    Figure("Vonnegut", "Kurt Vonnegut", "author"),
    Figure("Achebe", "Chinua Achebe", "author"),
    Figure("Carson", "Rachel Carson", "author"),
    Figure("Whitman", "Walt Whitman", "poet"),
    Figure("Dickinson", "Emily Dickinson", "poet"),
    Figure("Frost", "Robert Frost", "poet"),
    Figure("Poe", "Edgar Allan Poe", "poet"),
    Figure("Emerson", "Ralph Waldo Emerson", "essayist"),
    # Presidents and founders.
    Figure("Jefferson", "Thomas Jefferson", "president"),
    Figure("Roosevelt", "Franklin Roosevelt", "president"),
    Figure("Kennedy", "John F. Kennedy", "president"),
    Figure("Truman", "Harry Truman", "president"),
    Figure("Eisenhower", "Dwight Eisenhower", "president"),
    Figure("Madison", "James Madison", "president"),
    Figure("Hamilton", "Alexander Hamilton", "founder"),
    Figure("Franklin", "Benjamin Franklin", "founder"),
    # Scientists and inventors.
    Figure("Einstein", "Albert Einstein", "scientist"),
    Figure("Newton", "Isaac Newton", "scientist"),
    Figure("Darwin", "Charles Darwin", "scientist"),
    Figure("Curie", "Marie Curie", "scientist"),
    Figure("Pasteur", "Louis Pasteur", "scientist"),
    Figure("Galileo", "Galileo Galilei", "scientist"),
    Figure("Carver", "George Washington Carver", "scientist"),
    Figure("Goodall", "Jane Goodall", "scientist"),
    Figure("Edison", "Thomas Edison", "inventor"),
    # Civil rights, reform and statecraft.
    Figure("Douglass", "Frederick Douglass", "abolitionist"),
    Figure("Tubman", "Harriet Tubman", "abolitionist"),
    Figure("Mandela", "Nelson Mandela", "statesman"),
    Figure("Churchill", "Winston Churchill", "statesman"),
    Figure("Gorbachev", "Mikhail Gorbachev", "statesman"),
    Figure("Chavez", "Cesar Chavez", "labour leader"),
    Figure("Keller", "Helen Keller", "activist"),
    Figure("Anthony", "Susan B. Anthony", "suffragist"),
    # Athletes and astronauts — the population a personal essay reaches for.
    Figure("Owens", "Jesse Owens", "athlete"),
    Figure("Ali", "Muhammad Ali", "athlete"),
    Figure("Jordan", "Michael Jordan", "athlete"),
    Figure("Armstrong", "Neil Armstrong", "astronaut"),
    # Artists, composers, philosophers.
    Figure("Picasso", "Pablo Picasso", "artist"),
    Figure("Monet", "Claude Monet", "artist"),
    Figure("Kahlo", "Frida Kahlo", "artist"),
    Figure("Mozart", "Wolfgang Amadeus Mozart", "composer"),
    Figure("Beethoven", "Ludwig van Beethoven", "composer"),
    Figure("Marx", "Karl Marx", "philosopher"),
    Figure("Socrates", "Socrates", "philosopher"),
    Figure("Machiavelli", "Niccolo Machiavelli", "philosopher"),
)


class Score(NamedTuple):
    """What a gazetteer does to :data:`HELD_OUT_FIGURES`."""

    total: int
    surname_kept: int
    full_name_kept: int
    surname_destroyed: tuple[Figure, ...]
    full_name_destroyed: tuple[Figure, ...]

    @property
    def surname_rate(self) -> float:
        """Fraction of held-out figures whose BARE SURNAME survives."""
        return self.surname_kept / self.total if self.total else 0.0

    @property
    def full_name_rate(self) -> float:
        """Fraction whose FULL NAME survives — the tier's easier leg."""
        return self.full_name_kept / self.total if self.total else 0.0

    @property
    def recoverable_by_corroboration(self) -> tuple[Figure, ...]:
        """Destroyed surnames whose full name *does* resolve.

        These are the ones a same-document rule can recover for free: if the
        essay writes "Richard Wright" anywhere, a bare "Wright" in that same
        essay is that person, and no threshold has to move. Sizing this is the
        difference between a gazetteer change and a lookup change.
        """
        full_ok = {f.surname for f in self.surname_destroyed} - {
            f.surname for f in self.full_name_destroyed
        }
        return tuple(f for f in self.surname_destroyed if f.surname in full_ok)


def score(is_notable) -> Score:
    """Score a notability predicate against the held-out list.

    Takes the predicate rather than a Gazetteer so a candidate lookup change —
    same-document corroboration, a relaxed bearer cutoff — is measurable through
    the same function as the shipped asset.
    """
    surname_bad = tuple(f for f in HELD_OUT_FIGURES if not is_notable(f.surname))
    full_bad = tuple(f for f in HELD_OUT_FIGURES if not is_notable(f.full_name))
    return Score(
        total=len(HELD_OUT_FIGURES),
        surname_kept=len(HELD_OUT_FIGURES) - len(surname_bad),
        full_name_kept=len(HELD_OUT_FIGURES) - len(full_bad),
        surname_destroyed=surname_bad,
        full_name_destroyed=full_bad,
    )


def render(result: Score) -> str:
    """Human-readable report, for the CLI and for failure messages."""
    lines = [
        f"held-out figures: {result.total}",
        f"  bare surname kept  {result.surname_kept:3}/{result.total} "
        f"({100 * result.surname_rate:.1f}%)",
        f"  full name kept     {result.full_name_kept:3}/{result.total} "
        f"({100 * result.full_name_rate:.1f}%)",
    ]
    if result.surname_destroyed:
        lines.append("  surnames destroyed:")
        for figure in result.surname_destroyed:
            mark = "*" if figure in result.recoverable_by_corroboration else " "
            lines.append(
                f"   {mark} {figure.surname:14} {figure.full_name:26} {figure.domain}"
            )
        lines.append(
            f"  * recoverable from the full name in the same document: "
            f"{len(result.recoverable_by_corroboration)}"
        )
    if result.full_name_destroyed:
        lines.append("  FULL NAMES destroyed (no corroboration can help these):")
        for figure in result.full_name_destroyed:
            lines.append(f"     {figure.full_name:26} {figure.domain}")
    return "\n".join(lines)


def main() -> int:
    from vicary import gazetteer

    print(render(score(gazetteer.is_notable)))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
