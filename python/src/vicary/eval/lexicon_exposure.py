"""Price a word-list membership test by how many real names it claims.

Any proposal that suppresses a name candidate because the token "is an ordinary
English word" needs two numbers, not one: what it recovers, and what it exposes.
The first is easy — run it on a corpus of known over-fires and count. The second
is the one that has no instrument, and this module is that instrument.

Why the leak fixture cannot serve. :mod:`vicary.eval.fixture` carries 44 redact
spans, 16 of them held out. Measured 2026-09-10, adding adjacent-transposition
matching to a common-noun veto reads **zero leaks, zero held-out leaks** on the
fixture while claiming **2,234 additional real surnames** — a widening the gate
had no power to see. A zero on 44 spans is the absence of an instrument, not
evidence of safety, and the whole adoption case for a veto rests on "zero
measured leaks".

The population is the point, and it is not a new one: every American surname
:mod:`vicary.eval.census` already ships, minus what
:func:`vicary.gazetteer.is_common_given_name` protects. Reusing that table buys
the property a hand-rolled population cannot have — it is **bearer-weighted**,
so claiming ``Young`` costs what claiming ``Young`` actually costs, while a rare
name counts as the one family it is. It is also the sibling of the shipped
``bare-surname exposure`` gate, so the two numbers are on the same scale and a
reader can hold them side by side.

Read the output as a BOUND, not a leak rate. The contextual guards — relation
cue, organisation suffix, sentence position — cannot be exercised on a bare
token, so a surname this probe counts is one whose protection rests *entirely*
on those guards. That is a statement about where the risk sits, not a prediction
that it leaks.

Usage::

    python -m vicary.eval.lexicon_exposure --words /usr/share/dict/words
    python -m vicary.eval.lexicon_exposure --words /usr/share/dict/words \\
        --case-fold --plurals --matcher transposition

Ranking measured on ``/usr/share/dict/words``, 2026-09-10 — exact < transposition
< vowel-substitution < keyboard-substitution < damerau-1. Adjacent transposition
is the only fuzzy channel whose reach is worth its exposure on a
handwritten-and-transcribed corpus, and it bought 2 recovered spans of 117.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from vicary import gazetteer
from vicary.eval import census

#: Adjacent-key neighbours on a physical QWERTY layout. Present so a caller can
#: measure a keyboard-weighted matcher, NOT because it is recommended: keyboard
#: adjacency models a *typing* channel, and it under-performs plain transposition
#: on handwritten-and-transcribed corpora, where the errors are spelling and
#: transcription rather than mistyping.
_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
KEYBOARD_NEIGHBOURS: dict[str, frozenset[str]] = {}
for _ri, _row in enumerate(_ROWS):
    for _ci, _ch in enumerate(_row):
        _nb: set[str] = set()
        for _dr in (-1, 0, 1):
            for _dc in (-1, 0, 1):
                if _dr == _dc == 0:
                    continue
                _r, _c = _ri + _dr, _ci + _dc
                if 0 <= _r < len(_ROWS) and 0 <= _c < len(_ROWS[_r]):
                    _nb.add(_ROWS[_r][_c])
        KEYBOARD_NEIGHBOURS[_ch] = frozenset(_nb)

ALPHABET = "abcdefghijklmnopqrstuvwxyz"
VOWELS = "aeiouy"


def _transpositions(token: str) -> set[str]:
    return {token[:i] + token[i + 1] + token[i] + token[i + 2:]
            for i in range(len(token) - 1)}


def _vowel_substitutions(token: str) -> set[str]:
    return {token[:i] + v + token[i + 1:]
            for i, c in enumerate(token) if c in VOWELS
            for v in VOWELS if v != c}


def _keyboard_substitutions(token: str) -> set[str]:
    return {token[:i] + k + token[i + 1:]
            for i, c in enumerate(token)
            for k in KEYBOARD_NEIGHBOURS.get(c, ())}


def _damerau_1(token: str) -> set[str]:
    out = _transpositions(token)
    out |= {token[:i] + token[i + 1:] for i in range(len(token))}
    out |= {token[:i] + a + token[i + 1:] for i in range(len(token)) for a in ALPHABET}
    out |= {token[:i] + a + token[i:] for i in range(len(token) + 1) for a in ALPHABET}
    return out


#: Named edit channels. ``exact`` is the control and must stay first.
MATCHERS: dict[str, Callable[[str], Iterable[str]]] = {
    "exact": lambda t: (),
    "transposition": _transpositions,
    "vowel-substitution": _vowel_substitutions,
    "keyboard-substitution": _keyboard_substitutions,
    "damerau-1": _damerau_1,
}


def depluralise(token: str) -> set[str]:
    """Bare-plural forms of ``token``. Possessives are deliberately untouched —
    stripping ``'s`` turns every possessive name into its bare name."""
    forms = {token}
    if token.endswith("es") and len(token) > 3:
        forms.add(token[:-2])
    if token.endswith("s") and len(token) > 2:
        forms.add(token[:-1])
    return forms


def load_word_list(path: str, *, case_fold: bool = False,
                   exclude_names_above: int | None = None) -> frozenset[str]:
    """Read a newline-delimited word list.

    ``case_fold`` admits capitalised-only entries. It is the highest-leverage
    knob in the file and it cuts BOTH ways, which is the thing to know before
    turning it on: the entries it wants are ``English`` and ``Halloween``, but a
    system word list is not a lexicon of common nouns. ``/usr/share/dict/words``
    on macOS carries ``Martinez``, ``Nguyen``, ``Moore`` and ``William`` as
    ordinary entries, so case-folding alone walks real surnames into a
    common-noun veto — measured at 17.1% -> 20.7% of American surname bearers,
    about seven million people.

    ``exclude_names_above`` is the repair: drop any entry the census table
    reports as a surname borne by at least that many Americans. The threshold is
    load-bearing and a blanket exclusion is measurably wrong — the census carries
    160k surnames, most of them rare, and many rare ones are ordinary words. On
    the NWP AWC corpus, excluding *every* census surname costs 16 of 35 recovered
    spans (``Cold``, ``Space``, ``Boys``, ``Stuff``, ``English``); excluding by
    bearer count costs nothing until the threshold falls below ~100,000.

    The frontier, measured 2026-09-10 (recovered spans of 117 / share of surname
    bearers the lexicon claims): none 35 / 27.3% · 100,000 **35 / 21.1%** ·
    10,000 33 / 11.0% · 1,000 29 / 3.6% · all 19 / 0.4%. 100,000 is free; below
    it is a priced dial. **The knee is selected on 56 papers and is in-sample** —
    treat any value under 100,000 as needing an unseen corpus before it ships.
    """
    with open(path, encoding="utf-8", errors="replace") as handle:
        entries = [line.strip() for line in handle if line.strip()]
    words = ({e.lower() for e in entries} if case_fold
             else {e for e in entries if e[0].islower()})
    if exclude_names_above is None:
        return frozenset(words)
    table = (census.load_shipped_census() if census.shipped_dir() is not None
             else census.load_census())
    return frozenset(w for w in words
                     if table.get(w, 0) < exclude_names_above
                     and not gazetteer.is_common_given_name(w))


def build_membership(lexicon: frozenset[str], *, matcher: str = "exact",
                     plurals: bool = False, min_length: int = 0,
                     ) -> Callable[[str], bool]:
    """A membership predicate over ``lexicon``.

    Plural stripping applies to the EXACT test only, never to a fuzzy
    candidate. Composing the two reaches edit-distance 2 by the back door —
    measured: a keyboard-substitution arm matched ``Feild`` via ``feils`` ->
    ``feil``, which is two edits away and looks like one in the report.
    """
    if matcher not in MATCHERS:
        raise ValueError(f"unknown matcher {matcher!r}; have {sorted(MATCHERS)}")
    generate = MATCHERS[matcher]

    def member(raw: str) -> bool:
        token = raw.lower()
        forms = depluralise(token) if plurals else {token}
        if any(form in lexicon for form in forms):
            return True
        if len(token) < min_length:
            return False
        return any(candidate in lexicon for candidate in generate(token))

    return member


def surname_population() -> dict[str, int]:
    """``{surname: bearers}`` for every American surname, minus the ones the
    given-name guard already protects.

    This is :mod:`vicary.eval.census`'s table, not a new one. Reusing it matters
    for a reason beyond thrift: it is **population-weighted**, so a common name
    counts for what it is worth, and it is US-shaped — the distribution of the
    classroom rosters this redaction protects. A population derived from the
    gazetteer's own surnames instead reads 125,535 tokens that skew
    international, which prices a matcher's reach rather than its exposure.

    Names :func:`gazetteer.is_common_given_name` carries are removed: the
    cheapest guard any veto will have already protects them, so counting them
    would inflate every arm equally and rank nothing.
    """
    if census.shipped_dir() is not None:
        table = census.load_shipped_census()
    else:  # operator-supplied source; the builder path resolves it
        table = census.load_census()
    return {name: bearers for name, bearers in table.items()
            if not gazetteer.is_common_given_name(name)}


@dataclass(frozen=True)
class ExposureResult:
    """What one membership test claims out of the surname population."""

    matcher: str
    case_fold: bool
    plurals: bool
    population: int
    claimed: int
    bearers_total: int
    bearers_claimed: int
    examples: tuple[str, ...]

    @property
    def rate(self) -> float:
        """Population-weighted exposure. The headline, and the one comparable to
        :func:`vicary.eval.census.Exposure.rate`."""
        return self.bearers_claimed / self.bearers_total if self.bearers_total else 0.0

    @property
    def distinct_rate(self) -> float:
        """Share of distinct surnames. Always the smaller worry — a rare name
        claimed costs one family; a common one costs a school district."""
        return self.claimed / self.population if self.population else 0.0

    @property
    def label(self) -> str:
        bits = ["case-folded" if self.case_fold else "lowercase-only"]
        if self.plurals:
            bits.append("plurals")
        if self.matcher != "exact":
            bits.append(self.matcher)
        return " + ".join(bits)


def measure(lexicon: frozenset[str], *, matcher: str = "exact",
            case_fold: bool = False, plurals: bool = False,
            min_length: int = 0, population: dict[str, int] | None = None,
            examples: int = 12) -> ExposureResult:
    """Count the surnames — and the bearers — a membership test claims."""
    names = population if population is not None else surname_population()
    member = build_membership(lexicon, matcher=matcher, plurals=plurals,
                              min_length=min_length)
    claimed = sorted((name for name in names if member(name)),
                     key=lambda n: (-names[n], n))
    return ExposureResult(matcher=matcher, case_fold=case_fold, plurals=plurals,
                          population=len(names), claimed=len(claimed),
                          bearers_total=sum(names.values()),
                          bearers_claimed=sum(names[n] for n in claimed),
                          examples=tuple(claimed[:examples]))


def report(results: list[ExposureResult]) -> str:
    """A table, plus the caveat the numbers must not travel without."""
    lines = [
        f"population: {results[0].population if results else 0} US surnames, "
        f"{results[0].bearers_total if results else 0:,} bearers "
        f"(census table, minus what the given-name guard carries)",
        "",
        f"{'membership test':<50}{'surnames':>10}{'bearers':>12}{'bound':>8}",
        "-" * 80,
    ]
    for result in results:
        lines.append(f"{result.label:<50}{result.claimed:>10}"
                     f"{result.bearers_claimed:>12,}{result.rate:>8.1%}")
    lines += [
        "-" * 80,
        "A BOUND, not a leak rate: the contextual guards (relation cue, org",
        "suffix) cannot be exercised on a bare token, so this counts the names",
        "whose protection rests entirely on them. Bearer-weighted, so it is on",
        "the same scale as the shipped bare-surname exposure gate.",
    ]
    if results:
        lines += ["", f"examples ({results[-1].label}): {', '.join(results[-1].examples)}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--words", required=True, help="newline-delimited word list")
    parser.add_argument("--matcher", action="append", choices=sorted(MATCHERS),
                        help="repeatable; defaults to every matcher")
    parser.add_argument("--case-fold", action="store_true",
                        help="admit capitalised-only entries")
    parser.add_argument("--exclude-names-above", type=int, default=None,
                        metavar="BEARERS",
                        help="drop entries the census reports as a surname borne "
                             "by >= BEARERS Americans (the repair for --case-fold; "
                             "100000 is free, below that is a priced dial)")
    parser.add_argument("--plurals", action="store_true",
                        help="strip bare plurals on the exact test")
    parser.add_argument("--min-length", type=int, default=0,
                        help="skip fuzzy matching below this token length")
    parser.add_argument("--max-bound", type=float, default=None,
                        help="fail (exit 1) if any arm's bound exceeds this fraction")
    args = parser.parse_args(argv)

    lexicon = load_word_list(args.words, case_fold=args.case_fold,
                             exclude_names_above=args.exclude_names_above)
    population = surname_population()
    results = [
        measure(lexicon, matcher=m, case_fold=args.case_fold, plurals=args.plurals,
                min_length=args.min_length, population=population)
        for m in (args.matcher or sorted(MATCHERS, key=lambda k: k != "exact"))
    ]
    print(report(results))
    if args.max_bound is not None:
        over = [r for r in results if r.rate > args.max_bound]
        if over:
            print(f"\nFAIL: {len(over)} arm(s) above --max-bound {args.max_bound:.1%}: "
                  + ", ".join(f"{r.label} at {r.rate:.1%}" for r in over))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
