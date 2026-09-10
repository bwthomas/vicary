"""Who the residual surname leak actually reaches — coverage and recall by stratum.

`mid_sentence_corroboration` suppresses a span whose only evidence is a capital,
in a document that has already proved its capitals unreliable
(:func:`vicary.name_candidates.capitalises_ordinary_words`). On that path a
surname written **once**, mid-sentence, has exactly two ways to survive: the
document's own testimony — the same token capitalised elsewhere — and the
**given-name tier**, 8,138 names at SSA births >= 1,800. A surname that is
nobody's first name reaches neither, and leaks.

The claim this module exists to *measure* rather than assert: "given-name
coverage is thinner for non-Anglo names, so the children this leaks are not a
random sample."

**What the numbers are about, stated once and repeated in every rendered
table.** The Census 2010 surname release carries, per name, the share of its
bearers reporting each race/ethnicity category. Those shares are a property of a
**NAME**, never of a person. Everything below is therefore coverage and recall
over **surname-bearer populations** — "of the 1.87M people who bear a surname
whose bearers are predominantly Hispanic, what fraction bear a surname the
given-name tier can rescue". It is not, and cannot be turned into, a statement
about any individual student. A child called Alvarez is not "a Hispanic name";
the *name* Alvarez has 92.8% Hispanic bearers, and that is a fact about the
2010 census long form.

Three things are measured, in this order:

``coverage``
    Bearer-weighted reach of every candidate rescue list, per stratum: the
    shipped ``given`` tier, the SSA births population at five floors (1,800 /
    1,048 / 500 / 100 / 1), and the census bearer floors a surname tier would
    ship at (>= 1,000 / 10,000 / 100,000). Pure arithmetic over two tables; no
    detector runs.

``recall``
    The number that matters. A stratified, bearer-weighted sample of surnames is
    injected into carrier documents **in the shape the residual leak has** — one
    mid-sentence occurrence, in a document that trips
    ``capitalises_ordinary_words`` — and the shipped detector is run over each.
    Recall is the fraction of injected surnames whose literal is absent from the
    masked text.

``gate``
    Whether the worst stratum's recall is stable enough to gate on: the width of
    its Wilson interval, and the bar the current detector would sit at.

Run ``python -m vicary.eval.strata --help``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Sources. Both are operator-held: the shares-bearing census release is NOT the
# reduced table this repository ships (`conformance/census/surnames.txt.gz` kept
# name + count and dropped the six share columns), and the SSA archive has no
# download path at all — ssa.gov 403s some networks.
# --------------------------------------------------------------------------

#: Environment overrides, so a box with the data somewhere else needs no edit.
CENSUS_ENV_VAR = "VICARY_CENSUS_SHARES_CSV"
SSA_ENV_VAR = "VICARY_SSA_NAMES_ZIP"

DEFAULT_CENSUS = "~/Documents/Claude/Data/census/Names_2010Census.csv"
DEFAULT_SSA = "~/Documents/Claude/Data/ssa/names.zip"

#: The six share columns, in the order the release writes them, paired with the
#: stratum label this module reports. The labels are the census categories and
#: are deliberately not softened: renaming `api` to something friendlier would
#: make the figure harder to trace back to the source table.
SHARE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("pctwhite", "WHITE"),
    ("pctblack", "BLACK"),
    ("pctapi", "API"),
    ("pctaian", "AIAN"),
    ("pct2prace", "TWO_OR_MORE"),
    ("pcthispanic", "HISPANIC"),
)

STRATA: tuple[str, ...] = tuple(label for _, label in SHARE_COLUMNS)

#: The release writes `(S)` where a share is suppressed for confidentiality —
#: too few bearers in that category to publish. Read as 0.0, which cannot change
#: an argmax: a share is suppressed *because* it is small, so it was never going
#: to be the maximum. Counted and reported anyway, because "we treated a
#: suppression as a zero" is the kind of decision that has to be visible.
SUPPRESSED = "(S)"

#: Bearer floor below which the census release does not publish a name at all.
CENSUS_PUBLICATION_FLOOR = 100

#: The aggregate row the release appends for everything under the floor. It has
#: no name and must not be stratified.
AGGREGATE_ROW = "ALL OTHER NAMES"

#: A name's stratum assignment is *contested* when the runner-up share is within
#: this many percentage points of the winner. Reported rather than resolved: a
#: contested name is still assigned to its argmax, and the sensitivity arm
#: re-runs coverage with contested names dropped so the reader can see whether
#: the answer depends on them.
CONTESTED_MARGIN_PP = 10.0

#: SSA birth floors to sweep. 1,800 is the shipped `GIVEN_NAME_MIN_BIRTHS`;
#: 1,048 is `Meisha`, named in `gazetteer.py` as the miss left unmade; 1 is
#: "has any US birth record at all".
SSA_FLOORS: tuple[int, ...] = (1800, 1048, 500, 100, 1)

#: Census bearer floors a vendored surname tier would ship at.
CENSUS_FLOORS: tuple[int, ...] = (1000, 10_000, 100_000)


# --------------------------------------------------------------------------
# Census with shares
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Surname:
    """One published census surname, with its bearer count and shares."""

    name: str
    count: int
    shares: tuple[float, ...]
    #: Index into :data:`SHARE_COLUMNS` of the largest share.
    top: int
    #: Winner minus runner-up, in percentage points.
    margin: float
    #: How many of this name's six shares the release suppressed.
    suppressed: int

    @property
    def stratum(self) -> str:
        return SHARE_COLUMNS[self.top][1]

    @property
    def contested(self) -> bool:
        return self.margin < CONTESTED_MARGIN_PP

    @property
    def display(self) -> str:
        """The name as a student would write it.

        The release is upper-case only, so this is a reconstruction rather than
        a reading: ``.title()`` renders ``O'BRIEN`` correctly and ``MCDONALD``
        as ``Mcdonald``. That is a limitation of the source, it is applied
        identically to every stratum, and the detector's candidate rule only
        needs the leading capital — so it cannot move a comparison between
        strata. Stated because an unstated normalisation is how a rendering
        artefact becomes a finding.
        """
        return self.name.title()


def census_shares_path(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get(CENSUS_ENV_VAR)
                or DEFAULT_CENSUS).expanduser()


def load_census_shares(path: str | Path | None = None) -> list[Surname]:
    """Parse the full Census 2010 surname release, shares included.

    Deliberately not :func:`vicary.eval.census.load_census`: that reads the
    reduced table this repository ships, which dropped the six share columns
    this module is entirely about.
    """
    import csv

    resolved = census_shares_path(str(path) if path else None)
    if not resolved.exists():
        raise SystemExit(
            f"census release not found at {resolved}. This is the FULL release "
            f"(name,rank,count,prop100k,cum_prop100k,{','.join(c for c, _ in SHARE_COLUMNS)}), "
            f"not the repository's reduced `surnames.txt.gz`. Set "
            f"{CENSUS_ENV_VAR} or pass --census."
        )

    out: list[Surname] = []
    dropped_aggregate = 0
    with resolved.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c, _ in SHARE_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(
                f"{resolved} has no {missing} column(s) — this is the reduced "
                "table, not the full release."
            )
        for row in reader:
            name = (row.get("name") or "").strip().upper()
            if not name:
                continue
            if name == AGGREGATE_ROW:
                dropped_aggregate += 1
                continue
            try:
                count = int(row["count"])
            except (KeyError, TypeError, ValueError):
                continue
            shares: list[float] = []
            suppressed = 0
            for column, _ in SHARE_COLUMNS:
                raw = (row.get(column) or "").strip()
                if raw == SUPPRESSED or not raw:
                    suppressed += 1
                    shares.append(0.0)
                    continue
                try:
                    shares.append(float(raw))
                except ValueError:
                    suppressed += 1
                    shares.append(0.0)
            order = sorted(range(len(shares)), key=lambda i: (-shares[i], i))
            top = order[0]
            runner_up = shares[order[1]] if len(order) > 1 else 0.0
            out.append(Surname(
                name=name, count=count, shares=tuple(shares), top=top,
                margin=round(shares[top] - runner_up, 4),
                suppressed=suppressed,
            ))
    if not out:
        raise SystemExit(f"{resolved} parsed 0 surnames")
    if dropped_aggregate != 1:
        # Not fatal, but it means the file is not the shape this was written
        # against, and a silent difference in the denominator is exactly the
        # kind of thing that makes two runs disagree for no visible reason.
        print(f"note: {dropped_aggregate} {AGGREGATE_ROW!r} rows dropped "
              "(expected 1)", file=sys.stderr)
    return out


# --------------------------------------------------------------------------
# SSA births
# --------------------------------------------------------------------------


def ssa_path(explicit: str | None = None) -> Path:
    return Path(explicit or os.environ.get(SSA_ENV_VAR)
                or DEFAULT_SSA).expanduser()


def load_ssa_births(path: str | Path | None = None) -> dict[str, int]:
    """``{normalised given name: total US births}``, via the asset build.

    Reuses :func:`vicary_build.gazetteer.read_ssa_given_names` rather than
    re-parsing, so the floors swept here are measured against exactly the
    population the shipped tier was cut from — including its row-count sanity
    floor. A second parser that summed the years differently would make every
    floor in this document incomparable with the one in the asset.
    """
    resolved = ssa_path(str(path) if path else None)
    if not resolved.exists():
        raise SystemExit(
            f"SSA archive not found at {resolved}. It has no download path — "
            f"ssa.gov 403s some networks. Set {SSA_ENV_VAR} or pass --ssa."
        )
    _ensure_vicary_build_importable()
    from vicary_build.gazetteer import read_ssa_given_names

    return read_ssa_given_names(resolved)


def _ensure_vicary_build_importable() -> None:
    """Put ``asset/`` on the path when the build package is not installed."""
    try:
        import vicary_build  # noqa: F401
        return
    except ImportError:
        pass
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "asset"
        if (candidate / "vicary_build" / "gazetteer.py").exists():
            sys.path.insert(0, str(candidate))
            return
    raise SystemExit(
        "cannot import vicary_build; run from a vicary checkout or install the "
        "asset build package."
    )


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


@dataclass
class Coverage:
    """Bearer-weighted reach of one predicate, aggregate and by stratum."""

    label: str
    #: Fraction of ALL published surname bearers the predicate reaches.
    aggregate: float
    #: ``{stratum: fraction}``.
    by_stratum: dict[str, float]
    #: ``{stratum: bearers reached}`` — the numerators, kept so a reader can
    #: re-derive any figure without re-running.
    bearers_reached: dict[str, int]
    #: Names reached, unweighted, for the same reason.
    names_reached: dict[str, int]


@dataclass
class StratumProfile:
    """What a stratum is, before any predicate is applied to it."""

    stratum: str
    names: int
    bearers: int
    bearer_share: float
    contested_names: int
    contested_bearers: int
    #: Mean of the winning share, bearer-weighted. A stratum whose winner
    #: averages 55% is a much weaker statement than one averaging 92%.
    mean_top_share: float


def profile_strata(surnames: Sequence[Surname]) -> tuple[list[StratumProfile], dict]:
    total_bearers = sum(s.count for s in surnames)
    by: dict[str, list[Surname]] = defaultdict(list)
    for s in surnames:
        by[s.stratum].append(s)
    profiles: list[StratumProfile] = []
    for stratum in STRATA:
        rows = by.get(stratum, [])
        bearers = sum(r.count for r in rows)
        contested = [r for r in rows if r.contested]
        weighted_top = (
            sum(r.shares[r.top] * r.count for r in rows) / bearers
            if bearers else 0.0
        )
        profiles.append(StratumProfile(
            stratum=stratum,
            names=len(rows),
            bearers=bearers,
            bearer_share=bearers / total_bearers if total_bearers else 0.0,
            contested_names=len(contested),
            contested_bearers=sum(r.count for r in contested),
            mean_top_share=weighted_top,
        ))
    meta = {
        "names": len(surnames),
        "bearers": total_bearers,
        "publication_floor": CENSUS_PUBLICATION_FLOOR,
        "contested_margin_pp": CONTESTED_MARGIN_PP,
        "names_with_a_suppressed_share": sum(1 for s in surnames if s.suppressed),
        "bearers_with_a_suppressed_share": sum(
            s.count for s in surnames if s.suppressed),
        "exact_ties": sum(1 for s in surnames if s.margin == 0.0),
    }
    return profiles, meta


def measure_coverage(surnames: Sequence[Surname], label: str,
                     predicate: Callable[[Surname], bool]) -> Coverage:
    bearers_reached: dict[str, int] = {s: 0 for s in STRATA}
    names_reached: dict[str, int] = {s: 0 for s in STRATA}
    bearers_total: dict[str, int] = {s: 0 for s in STRATA}
    for s in surnames:
        bearers_total[s.stratum] += s.count
        if predicate(s):
            bearers_reached[s.stratum] += s.count
            names_reached[s.stratum] += 1
    total = sum(bearers_total.values())
    hit = sum(bearers_reached.values())
    return Coverage(
        label=label,
        aggregate=hit / total if total else 0.0,
        by_stratum={
            s: (bearers_reached[s] / bearers_total[s]) if bearers_total[s] else 0.0
            for s in STRATA
        },
        bearers_reached=bearers_reached,
        names_reached=names_reached,
    )


def coverage_suite(surnames: Sequence[Surname],
                   ssa: dict[str, int]) -> list[Coverage]:
    """Every rescue list, on one bearer-weighted scale."""
    from vicary.gazetteer import is_common_given_name, normalize

    out: list[Coverage] = [
        measure_coverage(
            surnames, "vicary `given` tier (shipped asset)",
            lambda s: is_common_given_name(normalize(s.name)),
        )
    ]
    for floor in SSA_FLOORS:
        def by_births(s: Surname, f: int = floor) -> bool:
            return ssa.get(normalize(s.name), 0) >= f

        out.append(measure_coverage(
            surnames, f"SSA births >= {floor:,}", by_births))
    for floor in CENSUS_FLOORS:
        def by_bearers(s: Surname, f: int = floor) -> bool:
            return s.count >= f

        out.append(measure_coverage(
            surnames, f"census bearers >= {floor:,}", by_bearers))
    return out


# --------------------------------------------------------------------------
# Recall, in the shape the leak has
# --------------------------------------------------------------------------

#: Carrier sentences. Each puts the surname **mid-sentence**, bare, once, with
#: no channel the detector could use to rescue it other than the given-name
#: tier: no first-person relation ("my friend X" is the relation channel and
#: would rescue every stratum equally), no title, no full name, no second
#: occurrence. Three of them rather than one so the measurement is not a
#: property of a single sentence; the per-template spread is reported.
CARRIER_SENTENCES: tuple[str, ...] = (
    "Later that morning {name} came back with the same question.",
    "The report that {name} handed in covered the same three points.",
    "Everyone in the group agreed with {name} about the second option.",
)

#: The corpus whose essays carry the injections. persuade-20 ships in
#: `conformance/corpora/`, so this runs from a clean checkout.
DEFAULT_CORPUS = "persuade-20"

#: Fallback when the whole corpus is drawn from: how many of its essays trip
#: `capitalises_ordinary_words`. Recorded in the result, never assumed.


@dataclass
class RecallDraw:
    """One injected surname and what the detector did with it."""

    name: str
    display: str
    stratum: str
    count: int
    essay_id: str
    template: int
    redacted: bool
    trips: bool
    in_given_tier: bool
    #: True when the gazetteer vouches for the token, so a surviving literal is
    #: a deliberate KEEP rather than the mid-sentence guard firing. Read from
    #: :func:`vicary.gazetteer.is_notable` rather than from the tier string:
    #: the tier for an unknown name is ``"not_notable"``, and an attribution
    #: table written against a guessed ``"none"`` sentinel reported every single
    #: leak as notable.
    notable: bool
    notability_tier: str
    is_title: bool
    is_settlement: bool


@dataclass
class RecallResult:
    arm: str
    corpus_id: str
    essay_ids: list[str]
    draws: list[RecallDraw] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)


def sloppy_essays(corpus_id: str = DEFAULT_CORPUS
                  ) -> tuple[str, list[tuple[str, str]], int]:
    """The corpus essays that trip ``capitalises_ordinary_words``.

    A document that does not trip it cannot exhibit the leak at all — the
    mid-sentence guard is off there and a bare capital is sufficient evidence on
    its own. Measuring recall over a mixed set would therefore report a number
    that is mostly about how many of the essays were sloppy.
    """
    from vicary.eval import corpus as corpus_mod
    from vicary.name_candidates import capitalises_ordinary_words

    loaded_id, essays = corpus_mod.load_essays(corpus_id)
    trips = [(eid, text) for eid, text in essays
             if capitalises_ordinary_words(text)]
    return loaded_id, trips, len(essays)


def draw_sample(surnames: Sequence[Surname], per_stratum: int, *,
                seed: int, weighting: str) -> dict[str, list[Surname]]:
    """``{stratum: [surname, ...]}``, ``per_stratum`` draws each.

    ``weighting="bearers"`` draws **with replacement, proportional to bearer
    count**, which makes the sample mean an unbiased estimate of the
    bearer-weighted recall of that stratum and makes a binomial interval on the
    draws the right interval. Duplicates are kept rather than deduplicated:
    dropping them would silently reweight the sample toward the tail, which is
    the opposite of what a bearer-weighted figure means. The count of DISTINCT
    names drawn is reported alongside, because a stratum that concentrates its
    bearers in a hundred names is a different kind of estimate than one that
    spreads them over ten thousand, even at the same n.

    ``weighting="names"`` draws uniformly over distinct names — a different
    question ("of the names in this stratum, what fraction leak") and reported
    as a secondary figure, never as the headline.
    """
    rng = random.Random(seed)
    by: dict[str, list[Surname]] = defaultdict(list)
    for s in surnames:
        by[s.stratum].append(s)
    out: dict[str, list[Surname]] = {}
    for stratum in STRATA:
        rows = by.get(stratum, [])
        if not rows:
            out[stratum] = []
            continue
        if weighting == "bearers":
            weights = [r.count for r in rows]
            out[stratum] = rng.choices(rows, weights=weights, k=per_stratum)
        else:
            k = min(per_stratum, len(rows))
            out[stratum] = rng.sample(rows, k=k)
    return out


#: Names injected per :func:`vicary.eval.recall.build_cases` call. The whole
#: census at once would hold ~160k copies of a 3 KB essay in memory at the same
#: time; chunking bounds that at a few hundred MB with no effect on any figure,
#: since each case is scored independently of every other.
CHUNK = 4000


def measure_recall(ordered: Sequence[tuple[str, Surname]], *,
                   corpus_id: str = DEFAULT_CORPUS,
                   arm: str = "path-gazetteer-lowercase",
                   seed: int = 20260910,
                   progress: bool = False,
                   sidecar: str | Path | None = None) -> RecallResult:
    """Inject every surname in ``ordered`` and score the shipped detector.

    The injection goes through :func:`vicary.eval.recall.build_cases`, which is
    the same machinery the carrier gate uses — one frame per document, dropped
    at an offset :func:`vicary.eval.recall.injection_points` certified as a real
    sentence end, so the carrier text reads as prose rather than as a suffix.
    ``per_essay=1`` is the leak's shape: one bare surname, once, in a document
    whose capitals have already been discredited.

    ``sidecar`` writes one JSONL record per injected surname as it is scored.
    Any crossing the rendered tables do not print — stratum by carrier sentence,
    say, which is what a gate's stability actually depends on — is then a
    re-derivation rather than a twenty-minute re-run, and the run is
    interrogable after the fact rather than only summarisable.
    """
    from vicary.eval.fixture import Frame, Span
    from vicary.eval.recall import build_cases, build_redactor
    from vicary.gazetteer import (
        is_common_given_name,
        is_notable,
        is_settlement,
        is_title,
        normalize,
        notability,
    )
    from vicary.name_candidates import capitalises_ordinary_words

    loaded_id, essays, _total = sloppy_essays(corpus_id)
    if not essays:
        raise SystemExit(
            f"no essay in corpus {loaded_id!r} trips capitalises_ordinary_words; "
            "there is no document here in which the leak can occur."
        )

    skipped: dict[str, int] = defaultdict(int)
    seen: dict[str, int] = defaultdict(int)
    redactor = build_redactor(arm, None)
    draws: list[RecallDraw] = []
    sink = (Path(sidecar).expanduser().open("w", encoding="utf-8")
            if sidecar else None)

    for start in range(0, len(ordered), CHUNK):
        block = ordered[start:start + CHUNK]
        frames: list[Frame] = []
        carriers: list[tuple[str, str]] = []
        meta: list[tuple[str, Surname, str, int]] = []
        for offset, (stratum, surname) in enumerate(block):
            index = start + offset
            # Carrier essay and carrier sentence are chosen from a WITHIN-STRATUM
            # counter, never from the global index. Assigning them by global
            # index confounded them with the stratum completely: `flatten`
            # interleaves six strata, so `index % 3` gave every stratum exactly
            # one of the three sentences and `index % 4` gave it two of the four
            # essays. The first run of this harness reported a 97.0% / 10.7%
            # spread between carrier sentences that was really the strata, and a
            # 95 pp spread between strata that was partly the sentences. They
            # were unseparable in that design, which is what makes this a
            # confound rather than noise.
            seen[stratum] += 1
            turn = seen[stratum]
            essay_id, base = essays[turn % len(essays)]
            display = surname.display
            if display in base or surname.name in base:
                # The base essay already writes this token, so the injected
                # occurrence is not the only one and presence can no longer
                # decide the span. Skipped rather than scored, and counted.
                skipped["already_in_carrier"] += 1
                continue
            template = turn % len(CARRIER_SENTENCES)
            sentence = CARRIER_SENTENCES[template].format(name=display)
            frames.append(Frame(
                frame_id=f"strata-{index}",
                sentence=sentence,
                spans=(Span(entity="NAME", literal=display),),
            ))
            carriers.append((f"{essay_id}#{index}", base))
            meta.append((stratum, surname, essay_id, template))
        if not frames:
            continue

        cases = build_cases(carriers, seed=seed + start, per_essay=1,
                            pool=tuple(frames))
        if len(cases) != len(frames):
            skipped["no_injection_point"] += len(frames) - len(cases)
        by_id = {case.essay_id: case for case in cases}
        for (stratum, surname, essay_id, template), carrier in zip(
                meta, carriers, strict=True):
            case = by_id.get(carrier[0])
            if case is None:
                continue
            masked = redactor._apply(case.text, source="INPUT").text
            key = normalize(surname.name)
            draws.append(RecallDraw(
                name=surname.name,
                display=surname.display,
                stratum=stratum,
                count=surname.count,
                essay_id=essay_id,
                template=template,
                redacted=surname.display not in masked,
                trips=capitalises_ordinary_words(case.text),
                in_given_tier=is_common_given_name(key),
                notable=is_notable(key),
                notability_tier=notability(key),
                is_title=is_title(key),
                is_settlement=is_settlement(key),
            ))
            if sink is not None:
                sink.write(json.dumps(vars(draws[-1])) + "\n")
        if progress:
            print(f"  {len(draws):,}/{len(ordered):,} injected",
                  file=sys.stderr)

    if sink is not None:
        sink.close()
    if not draws:
        raise SystemExit("every case was skipped; nothing to measure")

    return RecallResult(
        arm=arm, corpus_id=loaded_id,
        essay_ids=[eid for eid, _ in essays],
        draws=draws, skipped=dict(skipped),
    )


@dataclass
class CrossingResult:
    """Every carrier applied to every name in a head sample."""

    #: ``{(stratum, name): {(essay_id, template): redacted}}``
    verdicts: dict[tuple[str, str], dict[tuple[str, int], bool]]
    counts: dict[tuple[str, str], int]
    essay_ids: list[str]
    top_n: int


def measure_carrier_crossing(surnames: Sequence[Surname], *, top_n: int = 60,
                             corpus_id: str = DEFAULT_CORPUS,
                             arm: str = "path-gazetteer-lowercase",
                             seed: int = 20260910,
                             progress: bool = False) -> CrossingResult:
    """The SAME names under EVERY carrier — the only clean carrier effect.

    :func:`measure_recall` gives each surname exactly one (essay, sentence)
    pair, so its per-carrier columns compare *different subsets of names*. For
    the aggregate that is harmless — 54,000 names a bucket averages out — but
    per stratum it is not, because a stratum's bearer weight is concentrated in
    a handful of head names and which bucket a head name landed in then moves
    the whole row. On the 162,201-name enumeration that artefact reads as a
    37.1 pp "carrier effect" for BLACK, which is not a carrier effect at all.

    So the head of each stratum is run through all twelve combinations. Any
    difference here is the carrier, because the name is held fixed. This is the
    number a worst-stratum gate's stability has to be argued from.
    """
    from vicary.eval.fixture import Frame, Span
    from vicary.eval.recall import build_cases, build_redactor

    _loaded, essays, _total = sloppy_essays(corpus_id)
    by: dict[str, list[Surname]] = defaultdict(list)
    for entry in surnames:
        by[entry.stratum].append(entry)
    head: list[tuple[str, Surname]] = []
    for stratum in STRATA:
        rows = sorted(by.get(stratum, []), key=lambda r: -r.count)[:top_n]
        head.extend((stratum, r) for r in rows)

    redactor = build_redactor(arm, None)
    verdicts: dict[tuple[str, str], dict[tuple[str, int], bool]] = defaultdict(dict)
    counts = {(st, r.display): r.count for st, r in head}
    for essay_id, base in essays:
        for template, pattern in enumerate(CARRIER_SENTENCES):
            frames: list[Frame] = []
            carriers: list[tuple[str, str]] = []
            keys: list[tuple[str, str]] = []
            for index, (stratum, entry) in enumerate(head):
                if entry.display in base or entry.name in base:
                    continue
                frames.append(Frame(
                    frame_id=f"cross-{index}",
                    sentence=pattern.format(name=entry.display),
                    spans=(Span(entity="NAME", literal=entry.display),),
                ))
                carriers.append((f"{essay_id}#{template}#{index}", base))
                keys.append((stratum, entry.display))
            cases = {c.essay_id: c for c in build_cases(
                carriers, seed=seed, per_essay=1, pool=tuple(frames))}
            for key, (case_id, _base) in zip(keys, carriers, strict=True):
                case = cases.get(case_id)
                if case is None:
                    continue
                masked = redactor._apply(case.text, source="INPUT").text
                verdicts[key][(essay_id, template)] = key[1] not in masked
            if progress:
                print(f"  crossing {essay_id} sentence {template}",
                      file=sys.stderr)
    return CrossingResult(verdicts=dict(verdicts), counts=counts,
                          essay_ids=[eid for eid, _ in essays], top_n=top_n)


def render_crossing(result: CrossingResult) -> str:
    """Per-stratum bearer-weighted recall under each of the twelve carriers."""
    combos = sorted({combo for v in result.verdicts.values() for combo in v})
    lines = [
        f"## True carrier effect — the top {result.top_n} surnames of each "
        "stratum under all twelve carriers",
        "",
        CAVEAT,
        "",
        "The name is held fixed and the carrier varied, so every difference "
        "here IS the carrier. Bearer-weighted over the head sample only, which "
        "is deliberately the part of each stratum a gate would be most "
        "leveraged on.",
        "",
        "| stratum | head bearers | min | max | range | names whose verdict "
        "ever flips |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for stratum in STRATA:
        keys = [k for k in result.verdicts if k[0] == stratum]
        if not keys:
            lines.append(f"| {stratum} | — | — | — | — | — |")
            continue
        totals = []
        for combo in combos:
            b = sum(result.counts[k] for k in keys if combo in result.verdicts[k])
            ok = sum(result.counts[k] for k in keys
                     if result.verdicts[k].get(combo))
            if b:
                totals.append(ok / b)
        flips = sum(1 for k in keys if len(set(result.verdicts[k].values())) > 1)
        head_bearers = sum(result.counts[k] for k in keys)
        lines.append(
            f"| {stratum} | {head_bearers:,} | {_pct(min(totals))} | "
            f"{_pct(max(totals))} | {_pct(max(totals) - min(totals))} | "
            f"{flips}/{len(keys)} |")
    return "\n".join(lines)


def flatten(sample: dict[str, list[Surname]]) -> list[tuple[str, Surname]]:
    """``{stratum: [name]}`` in :data:`STRATA` order, ready for injection.

    Interleaved rather than concatenated so a stratum cannot end up correlated
    with a carrier essay: ``measure_recall`` assigns essays round-robin, and
    with six strata written end to end and four essays, WHITE would take one
    subset of the essays and HISPANIC another. Interleaving makes every stratum
    see every carrier in the same proportion, which is what lets the strata be
    compared at all.
    """
    queues = {s: list(sample.get(s, [])) for s in STRATA}
    out: list[tuple[str, Surname]] = []
    while any(queues.values()):
        for stratum in STRATA:
            if queues[stratum]:
                out.append((stratum, queues[stratum].pop()))
    return out


def every_surname(surnames: Sequence[Surname]) -> dict[str, list[Surname]]:
    """The census enumeration itself — no sampling, hence no sampling error."""
    by: dict[str, list[Surname]] = {s: [] for s in STRATA}
    for s in surnames:
        by[s.stratum].append(s)
    return by


# --------------------------------------------------------------------------
# Intervals
# --------------------------------------------------------------------------


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Correct at the extremes, where Wald is not.

    Every stratum here is expected to sit near 0 or near 1, which is exactly
    where a normal-approximation interval produces bounds outside [0, 1] and a
    reader stops trusting the table.
    """
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

CAVEAT = (
    "Shares are a property of a NAME, not of a person. Every figure below is "
    "coverage or recall over SURNAME-BEARER POPULATIONS and is not a claim "
    "about any individual student."
)


def _pct(x: float, places: int = 1) -> str:
    return f"{100 * x:.{places}f}%"


def render_profile(profiles: Sequence[StratumProfile], meta: dict) -> str:
    lines = [
        "## Strata — what the census release says the population is",
        "",
        CAVEAT,
        "",
        f"{meta['names']:,} published surnames (bearer floor "
        f"{meta['publication_floor']}), {meta['bearers']:,} bearers. "
        f"{meta['exact_ties']:,} names have an exact tie for the top share; "
        f"{meta['names_with_a_suppressed_share']:,} names "
        f"({meta['bearers_with_a_suppressed_share']:,} bearers) carry at least "
        "one share the release suppressed, read here as 0.0.",
        "",
        "| stratum | names | bearers | bearer share | mean top share | contested (<"
        f"{meta['contested_margin_pp']:.0f} pp) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for p in profiles:
        lines.append(
            f"| {p.stratum} | {p.names:,} | {p.bearers:,} | "
            f"{_pct(p.bearer_share)} | {p.mean_top_share:.1f}% | "
            f"{p.contested_names:,} names / {p.contested_bearers:,} bearers |"
        )
    return "\n".join(lines)


def render_coverage(coverages: Sequence[Coverage], title: str) -> str:
    lines = [
        f"## {title}",
        "",
        CAVEAT,
        "",
        "| rescue list | AGGREGATE | " + " | ".join(STRATA) + " |",
        "|---|---:|" + "---:|" * len(STRATA),
    ]
    for c in coverages:
        cells = " | ".join(_pct(c.by_stratum[s]) for s in STRATA)
        lines.append(f"| {c.label} | **{_pct(c.aggregate)}** | {cells} |")
    return "\n".join(lines)


def recall_figures(rows: Sequence[RecallDraw], *,
                   enumeration: bool) -> dict[str, Any]:
    """One set of cases reduced to a figure, with the denominator it earned.

    **The two modes do not compute the headline the same way, and conflating
    them double-weights.** Under ``enumeration`` every published surname is
    injected exactly once, so the bearer-weighted figure has to be formed here,
    by weighting each case by its census bearer count. Under sampling the draws
    were *already* taken proportional to bearer count, so the bearer-weighted
    figure is the plain mean over draws and weighting again by count would
    apply the same weight twice — which is not a rounding difference: the first
    run of this harness reported WHITE at 79.5% bearer-weighted against 31.8%
    name-weighted, and part of that gap was the second application of the
    weight, not the population.

    ``name_recall`` — every surname counted once — is a different question and
    the right denominator for sizing a fix rather than describing who is
    affected. It is only available under ``enumeration``; a bearer-proportional
    sample cannot estimate it, and returning something that looked like it
    would invite exactly the mix-up above.
    """
    n = len(rows)
    k = sum(1 for d in rows if d.redacted)
    out: dict[str, Any] = {
        "n": n,
        "redacted": k,
        "distinct_names": len({d.name for d in rows}),
        "enumeration": enumeration,
    }
    if enumeration:
        bearers = sum(d.count for d in rows)
        bearers_ok = sum(d.count for d in rows if d.redacted)
        out.update({
            "bearers": bearers,
            "bearers_redacted": bearers_ok,
            "bearer_recall": (bearers_ok / bearers) if bearers else None,
            "bearer_ci95": None,
            "name_recall": (k / n) if n else None,
        })
    else:
        lo, hi = wilson(k, n)
        out.update({
            "bearers": None,
            "bearers_redacted": None,
            "bearer_recall": (k / n) if n else None,
            "bearer_ci95": [lo, hi] if n else None,
            "name_recall": None,
        })
    return out


def render_recall(result: RecallResult, *, exhaustive: bool,
                  title: str | None = None) -> str:
    figures = recall_figures(result.draws, enumeration=exhaustive)
    heading = title or (
        "Recall in the leak's shape — one bare surname, mid-sentence, "
        "in a document that trips `capitalises_ordinary_words`")
    if exhaustive:
        census_note = (
            "Every published census surname is injected exactly once, so both "
            "columns are ENUMERATIONS of the published surname population "
            "rather than estimates — no sampling error, no interval. The "
            "residual randomness is which carrier essay and which carrier "
            "sentence a name drew, and that spread is reported below."
        )
        aggregate = (
            f"**AGGREGATE — bearer-weighted recall "
            f"{_pct(figures['bearer_recall'] or 0.0)} "
            f"({figures['bearers_redacted']:,} of {figures['bearers']:,} "
            f"surname-bearers); name-weighted recall "
            f"{_pct(figures['name_recall'] or 0.0)} "
            f"({figures['redacted']:,} of {figures['n']:,} surnames).**"
        )
        header = ("| stratum | surnames | bearers | bearer-weighted recall | "
                  "name-weighted recall | bearers leaked |")
        rule = "|---|---:|---:|---:|---:|---:|"
    else:
        census_note = (
            "Surnames are drawn WITH REPLACEMENT proportional to bearer count "
            "within each stratum. The draws are therefore i.i.d. from the "
            "bearer distribution, the plain mean over draws IS the "
            "bearer-weighted recall, and a Wilson interval on the draws is the "
            "right interval. Name-weighted recall is not estimable from this "
            "sample and is reported as `—` rather than approximated."
        )
        aggregate = (
            f"**Pooled over draws — {_pct(figures['bearer_recall'] or 0.0)} "
            f"[{_pct(figures['bearer_ci95'][0])}, "
            f"{_pct(figures['bearer_ci95'][1])}], n = {figures['n']:,} draws. "
            "This is NOT the population aggregate**: every stratum is drawn "
            "the same number of times, so pooling them averages six strata "
            "whose bearer shares run from 0.0% to 76.3%. Only the per-stratum "
            "rows are population figures in this mode; run without `--sample` "
            "for an aggregate."
        )
        header = ("| stratum | draws | distinct names | bearer-weighted recall "
                  "| 95% CI | leaked draws |")
        rule = "|---|---:|---:|---:|---|---:|"
    lines = [
        f"## {heading}",
        "",
        CAVEAT,
        "",
        f"Arm `{result.arm}` — the level a host gets with no configuration "
        f"(`DEFAULT_NAME_DETECTION`). Corpus `{result.corpus_id}`, carrier "
        f"essays {', '.join(result.essay_ids)}; every one of them trips "
        "`capitalises_ordinary_words`, which is the precondition for the "
        "mid-sentence guard to be armed at all.",
        "",
        census_note,
        "",
        aggregate,
        "",
        header,
        rule,
    ]
    for stratum in STRATA:
        rows = [d for d in result.draws if d.stratum == stratum]
        if not rows:
            lines.append(f"| {stratum} | 0 | 0 | — | — | — |")
            continue
        f = recall_figures(rows, enumeration=exhaustive)
        places = 1 if f["n"] >= 200 else 0
        if exhaustive:
            lines.append(
                f"| {stratum} | {f['n']:,} | {f['bearers']:,} | "
                f"**{_pct(f['bearer_recall'], places)}** | "
                f"{_pct(f['name_recall'], places)} | "
                f"{f['bearers'] - f['bearers_redacted']:,} |")
        else:
            lo, hi = f["bearer_ci95"]
            lines.append(
                f"| {stratum} | {f['n']:,} | {f['distinct_names']:,} | "
                f"**{_pct(f['bearer_recall'], places)}** | "
                f"[{_pct(lo, places)}, {_pct(hi, places)}] | "
                f"{f['n'] - f['redacted']:,} |")
    if result.skipped:
        lines += ["", "Skipped cases: " + ", ".join(
            f"{k} = {v:,}" for k, v in sorted(result.skipped.items()))
            + ". A surname the carrier essay already writes cannot be scored "
              "by presence, so it is dropped rather than guessed at."]
    return "\n".join(lines)


def render_spread(result: RecallResult, *, exhaustive: bool) -> str:
    """How much of the answer is the carrier rather than the name.

    Read this table before the strata table. Carrier essay and carrier sentence
    are assigned from a WITHIN-STRATUM counter precisely so that they are
    orthogonal to the stratum; if they were not, a spread here would be
    indistinguishable from a spread there. An earlier revision of this harness
    assigned both from the global index, which — with six strata interleaved,
    three sentences and four essays — gave every stratum exactly one sentence
    and two of the four essays, and reported a 97.0% / 10.7% "carrier sentence"
    effect that was the strata wearing a costume.
    """
    unit = "bearer-weighted recall" if exhaustive else "recall over draws"
    lines = [
        "## Instrument spread — carrier sentence and carrier essay",
        "",
        "Carrier and stratum are orthogonal by construction (a within-stratum "
        "counter picks both), so this is how much of the answer is the "
        "sentence rather than the name. If the strata differ by less than "
        "these rows move, the strata are not what is being measured.",
        "",
        f"| carrier | cases | {unit} |",
        "|---|---:|---:|",
    ]
    for t in sorted({d.template for d in result.draws}):
        rows = [d for d in result.draws if d.template == t]
        f = recall_figures(rows, enumeration=exhaustive)
        lines.append(
            f"| sentence {t}: \"{CARRIER_SENTENCES[t].format(name='X')}\" | "
            f"{f['n']:,} | {_pct(f['bearer_recall'])} |")
    for eid in sorted({d.essay_id for d in result.draws}):
        rows = [d for d in result.draws if d.essay_id == eid]
        f = recall_figures(rows, enumeration=exhaustive)
        lines.append(
            f"| essay {eid} | {f['n']:,} | {_pct(f['bearer_recall'])} |")
    trips = sum(1 for d in result.draws if d.trips)
    lines += ["", f"Documents still tripping `capitalises_ordinary_words` "
                  f"after injection: {trips:,}/{len(result.draws):,}."]
    return "\n".join(lines)


def render_mechanism(result: RecallResult) -> str:
    """What rescued the spans that were rescued, and what a leak was made of."""
    lines = [
        "## Why each span survived or leaked",
        "",
        "Name-weighted counts. `given` tier is the shipped rescue list on this "
        "path; `notable` means the gazetteer vouches for the token, so a leak "
        "there is a deliberate KEEP rather than the mid-sentence guard firing.",
        "",
        "| stratum | masked, in `given` tier | masked, not in tier | "
        "leaked, gazetteer-notable | leaked, unknown to every tier |",
        "|---|---:|---:|---:|---:|",
    ]
    for stratum in STRATA:
        rows = [d for d in result.draws if d.stratum == stratum]
        if not rows:
            lines.append(f"| {stratum} | — | — | — | — |")
            continue
        a = sum(1 for d in rows if d.redacted and d.in_given_tier)
        b = sum(1 for d in rows if d.redacted and not d.in_given_tier)
        c = sum(1 for d in rows if not d.redacted and d.notable)
        e = sum(1 for d in rows if not d.redacted and not d.notable)
        lines.append(f"| {stratum} | {a:,} | {b:,} | {c:,} | {e:,} |")
    return "\n".join(lines)


def render_gate(result: RecallResult, *, exhaustive: bool) -> str:
    scored = []
    for stratum in STRATA:
        rows = [d for d in result.draws if d.stratum == stratum]
        if not rows:
            continue
        f = recall_figures(rows, enumeration=exhaustive)
        scored.append((f["bearer_recall"], stratum, f))
    scored.sort(key=lambda r: r[0])
    if not scored:
        return "## Is a worst-stratum gate feasible?\n\nNo strata scored."
    worst, best = scored[0], scored[-1]
    lines = [
        "## Is a worst-stratum gate feasible?",
        "",
        f"Worst stratum **{worst[1]}** at {_pct(worst[0])}. Best stratum "
        f"{best[1]} at {_pct(best[0])}. Spread {_pct(best[0] - worst[0])}.",
        "",
    ]
    if exhaustive:
        # The smallest stratum is 123 names and 24,627 bearers. Letting it set
        # a gate would mean gating on a population two orders of magnitude
        # below every other row, so the bar a gate could realistically carry is
        # named separately rather than left to whichever row sorts lowest.
        material = [r for r in scored if r[2]["bearers"] >= 1_000_000]
        if material:
            lines += [
                f"Worst stratum carrying >= 1,000,000 bearers: "
                f"**{material[0][1]}** at {_pct(material[0][0])} "
                f"({material[0][2]['n']:,} surnames, "
                f"{material[0][2]['bearers']:,} bearers).",
                "",
            ]
    if exhaustive:
        lines += [
            "| stratum | bearer-weighted recall | name-weighted recall | "
            "surnames | bearers |",
            "|---|---:|---:|---:|---:|",
        ]
        for point, stratum, f in scored:
            lines.append(
                f"| {stratum} | {_pct(point)} | {_pct(f['name_recall'])} | "
                f"{f['n']:,} | {f['bearers']:,} |")
        lines += [
            "",
            "There is no sampling interval on these: every published census "
            "surname was injected, so the figures ARE the population. What a "
            "gate would have to tolerate instead is the carrier spread in the "
            "table above, plus whatever a gazetteer rebuild moves.",
        ]
    else:
        lines += [
            "| stratum | bearer-weighted recall | 95% CI | CI width |",
            "|---|---:|---|---:|",
        ]
        for point, stratum, f in scored:
            lo, hi = f["bearer_ci95"]
            lines.append(f"| {stratum} | {_pct(point)} | "
                         f"[{_pct(lo)}, {_pct(hi)}] | {_pct(hi - lo, 2)} |")
    return "\n".join(lines)


def _to_jsonable(result: RecallResult, profiles: Sequence[StratumProfile],
                 meta: dict, coverages: Sequence[Coverage],
                 sensitivity: Sequence[Coverage] | None,
                 exhaustive: bool) -> dict[str, Any]:
    def recall_block(r: RecallResult) -> dict[str, Any]:
        block: dict[str, Any] = {
            "arm": r.arm,
            "corpus_id": r.corpus_id,
            "carrier_essays": r.essay_ids,
            "exhaustive": exhaustive,
            "aggregate": recall_figures(r.draws, enumeration=exhaustive),
            "skipped": r.skipped,
            "by_stratum": {},
            "by_carrier_sentence": {},
            "by_carrier_essay": {},
        }
        for stratum in STRATA:
            rows = [d for d in r.draws if d.stratum == stratum]
            entry = (recall_figures(rows, enumeration=exhaustive)
                     if rows else None)
            if entry is not None:
                leaked = sorted(
                    (d for d in rows if not d.redacted),
                    key=lambda d: -d.count)
                entry["biggest_leaks"] = [
                    {"name": d.display, "bearers": d.count,
                     "gazetteer_tier": d.notability_tier}
                    for d in leaked[:20]
                ]
                entry["mechanism"] = {
                    "masked_in_given_tier": sum(
                        1 for d in rows if d.redacted and d.in_given_tier),
                    "masked_not_in_given_tier": sum(
                        1 for d in rows if d.redacted and not d.in_given_tier),
                    "leaked_gazetteer_notable": sum(
                        1 for d in rows if not d.redacted and d.notable),
                    "leaked_unknown": sum(
                        1 for d in rows if not d.redacted and not d.notable),
                }
            block["by_stratum"][stratum] = entry
        for t in sorted({d.template for d in r.draws}):
            block["by_carrier_sentence"][CARRIER_SENTENCES[t]] = recall_figures(
                [d for d in r.draws if d.template == t],
                enumeration=exhaustive)
        for eid in sorted({d.essay_id for d in r.draws}):
            block["by_carrier_essay"][eid] = recall_figures(
                [d for d in r.draws if d.essay_id == eid],
                enumeration=exhaustive)
        return block

    out: dict[str, Any] = {
        "caveat": CAVEAT,
        "carrier_sentences": list(CARRIER_SENTENCES),
        "census": meta,
        "strata": [vars(p) for p in profiles],
        "coverage": [
            {"label": c.label, "aggregate": c.aggregate,
             "by_stratum": c.by_stratum,
             "bearers_reached": c.bearers_reached,
             "names_reached": c.names_reached}
            for c in coverages
        ],
        "recall": recall_block(result),
    }
    if sensitivity is not None:
        out["coverage_contested_dropped"] = [
            {"label": c.label, "aggregate": c.aggregate,
             "by_stratum": c.by_stratum}
            for c in sensitivity
        ]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vicary.eval.strata",
        description=(
            "Measure who the residual bare-surname leak reaches. Stratifies the "
            "Census 2010 surname release by predominant bearer population, "
            "reports bearer-weighted coverage of every candidate rescue list, "
            "and measures the shipped detector's recall on surnames injected in "
            "the leak's shape: one bare mid-sentence occurrence in a document "
            "that trips capitalises_ordinary_words.\n\n"
            "Shares are a property of a NAME, not of a person. Every figure is "
            "coverage or recall over SURNAME-BEARER POPULATIONS and is not a "
            "claim about any individual student."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--census", default=None,
                        help=f"full Census 2010 release CSV (default: "
                             f"${CENSUS_ENV_VAR} or {DEFAULT_CENSUS})")
    parser.add_argument("--ssa", default=None,
                        help=f"SSA names.zip (default: ${SSA_ENV_VAR} or "
                             f"{DEFAULT_SSA})")
    parser.add_argument("--corpus", default=DEFAULT_CORPUS,
                        help="carrier corpus id (default: %(default)s)")
    parser.add_argument("--arm", default="path-gazetteer-lowercase",
                        help="redactor arm; the default is what a host gets "
                             "with no configuration (default: %(default)s)")
    parser.add_argument("--sample", type=int, default=None, metavar="N",
                        help="draw N surnames per stratum, with replacement and "
                             "proportional to bearers, instead of injecting "
                             "every published surname. Faster; adds sampling "
                             "error the default does not have.")
    parser.add_argument("--seed", type=int, default=20260910,
                        help="carrier/sampling seed (default: %(default)s)")
    parser.add_argument("--coverage-only", action="store_true",
                        help="skip the detector runs")
    parser.add_argument("--no-sensitivity", action="store_true",
                        help="skip the contested-names-dropped coverage arm")
    parser.add_argument("--json", default=None,
                        help="write the full result to this path")
    parser.add_argument("--carrier-crossing", type=int, default=None,
                        metavar="N",
                        help="also run the top N surnames of each stratum "
                             "through EVERY carrier, which is the only clean "
                             "measurement of the carrier's own effect")
    parser.add_argument("--draws-jsonl", default=None,
                        help="write one JSONL record per injected surname, so "
                             "any crossing the tables do not print can be "
                             "re-derived without re-running")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress progress on stderr")
    args = parser.parse_args(argv)

    surnames = load_census_shares(args.census)
    profiles, meta = profile_strata(surnames)
    if not args.quiet:
        print(f"loaded {len(surnames):,} surnames with shares", file=sys.stderr)
    ssa = load_ssa_births(args.ssa)
    if not args.quiet:
        print(f"loaded {len(ssa):,} SSA given names", file=sys.stderr)

    coverages = coverage_suite(surnames, ssa)
    sensitivity = None
    if not args.no_sensitivity:
        firm = [s for s in surnames if not s.contested]
        sensitivity = coverage_suite(firm, ssa)

    print(render_profile(profiles, meta))
    print()
    print(render_coverage(coverages, "Coverage — bearer-weighted reach of each "
                                     "rescue list"))
    if sensitivity is not None:
        print()
        print(render_coverage(
            sensitivity,
            f"Coverage, sensitivity arm — names whose top share leads by "
            f"< {CONTESTED_MARGIN_PP:.0f} pp dropped"))

    result: RecallResult | None = None
    exhaustive = args.sample is None
    if not args.coverage_only:
        if exhaustive:
            selection = every_surname(surnames)
        else:
            selection = draw_sample(surnames, args.sample, seed=args.seed,
                                    weighting="bearers")
        result = measure_recall(flatten(selection), corpus_id=args.corpus,
                                arm=args.arm, seed=args.seed,
                                progress=not args.quiet,
                                sidecar=args.draws_jsonl)
        print()
        print(render_recall(result, exhaustive=exhaustive))
        print()
        print(render_spread(result, exhaustive=exhaustive))
        print()
        print(render_mechanism(result))
        print()
        print(render_gate(result, exhaustive=exhaustive))
        if args.carrier_crossing:
            crossing = measure_carrier_crossing(
                surnames, top_n=args.carrier_crossing, corpus_id=args.corpus,
                arm=args.arm, seed=args.seed, progress=not args.quiet)
            print()
            print(render_crossing(crossing))

    if args.json and result is not None:
        payload = _to_jsonable(result, profiles, meta, coverages, sensitivity,
                               exhaustive)
        Path(args.json).expanduser().write_text(
            json.dumps(payload, indent=2), encoding="utf-8")
        if not args.quiet:
            print(f"wrote {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
