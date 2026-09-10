"""Price a candidate rescue list on vicary's own gates, before anyone builds one.

Six proposals want to give ``mid_sentence_corroboration`` a second list — census
surname slices, low-floor SSA given-name slices, a morphology suffix set — and
each of them is argued on what it *recovers*. This module measures the other
half: what the list costs inside the library, on the axes the shipped gates
already defend, with nothing built and nothing shipped.

Four costs, and they are not interchangeable:

* **Bytes.** ``conformance/census/surnames.txt.gz`` is deliberately NOT in any
  published package — it ships to the repository for gate scoring only. Anything
  consulted at *runtime* has to be vendored into all three ports, so an entry
  count is not a size and the gzipped bytes are the number that matters. The
  yardstick is the shipped gazetteer asset, measured off disk rather than quoted.

* **Ordinary words.** A rescue list that claims ``season``, ``long`` or ``rose``
  spends its cost on over-fire, which is the gate with zero headroom. Two
  independent readings: the stoplist (curated, small, high-confidence) and
  ``/usr/share/dict/words`` (broad, noisy, and the same list
  :mod:`vicary.eval.lexicon_exposure` is calibrated against).

* **Bearer-weighted claim on the shipped scale.** :func:`lexicon_exposure.measure`
  against :func:`lexicon_exposure.surname_population`, so the number is the
  sibling of ``bare-surname exposure`` and ``stoplist surname exposure`` rather
  than a new axis. **Read the sign per list**: for a surname rescue list a high
  claim is REACH, not leak — it is the population the channel would rescue. For a
  given-name list or a suffix rule it is the share of surname bearers the rule
  fires on for a reason other than being that person's given name, which is where
  over-fire comes from. The gate ceilings (1.25% bare-surname, 0.60% stoplist)
  are KEEP-side and a redact-side list does not spend them; the one real gate
  interaction a rescue list has is measured by :func:`stoplist_exposure_by_given_floor`.

* **Load time and per-request lookups.** The library claims no network and no
  per-request cost, in three languages. A 25k-entry list is gunzipped, split and
  set-built at import in each, so it is measured in each — Python, Node and Ruby,
  same bytes, over stdin, no files written anywhere.

Usage::

    python -m vicary.eval.candidate_cost                 # every section
    python -m vicary.eval.candidate_cost --section census --section morphology
    python -m vicary.eval.candidate_cost --json out.json

Nothing here mutates the tree, downloads anything, or changes a ceiling. It
prices; it does not build.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vicary import gazetteer, lexicon
from vicary.eval import census as census_eval
from vicary.eval.lexicon_exposure import ExposureResult, load_word_list, measure

#: The SSA baby-names archive. Local-only by design — ``ssa.gov`` 403s some
#: networks on every path, so :mod:`vicary_build.gazetteer` refuses to download
#: it and so does this. A missing archive is a FINDING, not a fallback.
DEFAULT_SSA_ZIP = "~/Documents/Claude/Data/ssa/names.zip"

#: The broad ordinary-word reading. Present on macOS and most Linux boxes; its
#: absence is reported rather than silently skipped.
DEFAULT_WORDS = "/usr/share/dict/words"

#: Bearer floors for the census surname slices, from the proposal.
CENSUS_FLOORS: tuple[int, ...] = (1_000, 10_000, 100_000)

#: Birth floors for the given-name slices. 1,800 is what ships today
#: (``vicary_build.gazetteer.GIVEN_NAME_MIN_BIRTHS``) and is the control; 1,048
#: is ``Meisha``, the miss the source names; 1 is every name with any record.
SSA_FLOORS: tuple[int, ...] = (1_800, 1_048, 500, 100, 1)

#: The morphology channel, verbatim from the proposal. Ships no bytes; the cost
#: is entirely in what the shapes claim.
SUFFIXES: tuple[str, ...] = (
    "ez", "ski", "wicz", "escu", "oglu", "ian", "jian", "sen", "son",
    "berg", "stein", "akis", "opoulos",
)

#: Names the proposal argues about, plus the fixture's private surnames. A list
#: that cannot reach these is not an answer to the leak, whatever its aggregate
#: coverage says — ``Alvarez`` is the shipped example and ``Meisha`` is the miss
#: the given-name tier's own comment names as left unmade.
TARGET_NAMES: tuple[str, ...] = (
    "alvarez", "nguyen", "patel", "rodriguez", "hernandez", "kowalski",
    "okonkwo", "ybarra", "bramwell", "pritchard", "meisha",
)

#: Repeats for each load-time measurement. Small because the quantity is tens of
#: milliseconds and the median of seven separates the arms cleanly.
LOAD_REPEATS = 7

#: Lookups timed per arm when pricing the per-request cost.
LOOKUP_SAMPLES = 200_000


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def census_table() -> dict[str, int]:
    """``{surname: bearers}``, the table this repository ships for gate scoring."""
    if census_eval.shipped_dir() is None and not census_eval.census_source():
        raise FileNotFoundError(
            "no conformance/census/ in this tree and no VICARY_EVAL_CENSUS_CSV"
        )
    return (census_eval.load_shipped_census() if census_eval.shipped_dir() is not None
            else census_eval.load_census())


def ssa_births(source: str = DEFAULT_SSA_ZIP) -> dict[str, int]:
    """``{given name: total US births}`` from a locally-held SSA archive.

    Delegates to the builder's own parser rather than re-implementing it, so the
    counts here are the counts the `given` tier is cut from — including the
    row-count floor that refuses a truncated read.
    """
    from vicary_build import gazetteer as builder

    return builder.read_ssa_given_names(Path(source).expanduser())


def ssa_slice(births: Mapping[str, int], floor: int) -> frozenset[str]:
    """The `given` tier the builder would cut at ``floor``.

    The three filters are copied from ``vicary_build.gazetteer.build``'s `given`
    comprehension, not approximated: a slice that differs from what would ship is
    a price for a list nobody would build.
    """
    return frozenset(
        token for token, count in births.items()
        if count >= floor and len(token) >= 2 and "-" not in token and "'" not in token
    )


def census_slice(table: Mapping[str, int], floor: int) -> frozenset[str]:
    """Every surname borne by at least ``floor`` Americans."""
    return frozenset(name for name, bearers in table.items() if bearers >= floor)


def keep_tier_tokens() -> frozenset[str]:
    """Every single token the gazetteer already KEEPs.

    The second of proposal B's three guards. A rescue list that re-claims a token
    a KEEP tier owns does not rescue a child, it fights the oracle — and the
    fight is invisible in an entry count.
    """
    gaz = gazetteer.load()
    return frozenset(set(gaz.short) | set(gaz.demonym)
                     | {n for n in gaz.place if " " not in n})


def guarded(names: Iterable[str], *, min_length: int = 5) -> frozenset[str]:
    """``names`` after all three guards the proposal requires: not a stop word,
    not already a KEEP tier token, and at least ``min_length`` characters."""
    stops = lexicon.stop_words()
    keeps = keep_tier_tokens()
    return frozenset(n for n in names
                     if n not in stops and n not in keeps and len(n) >= min_length)


def suffix_member(min_length: int = 0,
                  suffixes: Iterable[str] = SUFFIXES) -> Callable[[str], bool]:
    """Membership for the morphology channel: ends in one of ``suffixes``."""
    tails = tuple(suffixes)

    def member(token: str) -> bool:
        low = token.lower()
        return len(low) >= min_length and low.endswith(tails)

    return member


# ---------------------------------------------------------------------------
# Bytes
# ---------------------------------------------------------------------------

def packed(entries: Iterable[str]) -> bytes:
    """The list as it would be vendored: sorted, newline-delimited, gzip -9.

    ``mtime=0`` and the sort match ``tools/census_build.py`` and the asset
    builder, so the size is the size a reproducible cut would actually have.
    """
    body = "\n".join(sorted(entries)) + "\n"
    return gzip.compress(body.encode("utf-8"), 9, mtime=0)


def asset_sizes() -> dict[str, int]:
    """On-disk bytes of the two yardsticks, read rather than quoted."""
    out: dict[str, int] = {}
    try:
        out["gazetteer_asset"] = gazetteer.asset_path().stat().st_size
    except Exception:  # pragma: no cover - only outside a checkout
        pass
    shipped = census_eval.shipped_dir()
    if shipped is not None:
        table = shipped / census_eval.SHIPPED_TABLE_FILENAME
        if table.exists():
            out["census_table"] = table.stat().st_size
    return out


# ---------------------------------------------------------------------------
# Ordinary words
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrdinaryWords:
    """What a list claims out of two readings of "an ordinary English word"."""

    entries: int
    stoplist_hits: tuple[str, ...]
    dict_hits: int
    dict_examples: tuple[str, ...]
    dict_available: bool

    @property
    def stoplist_count(self) -> int:
        return len(self.stoplist_hits)

    @property
    def dict_rate(self) -> float:
        return self.dict_hits / self.entries if self.entries else 0.0


def ordinary_words(entries: Iterable[str], words: frozenset[str] | None,
                   *, weight: Mapping[str, int] | None = None,
                   examples: int = 15) -> OrdinaryWords:
    """Intersect a list with the stoplist and with a system word list.

    ``weight`` ranks the examples by whatever makes an offender *worst* for that
    list — bearers for a surname slice, births for a given-name slice. Ranking by
    count rather than alphabetically is the difference between naming ``long``
    and naming ``aakre``.
    """
    items = sorted(set(entries))
    stops = lexicon.stop_words()
    rank: Callable[[str], tuple[int, str]] = (
        (lambda t: (-(weight or {}).get(t, 0), t)) if weight else (lambda t: (0, t))
    )
    hit_stop = sorted((t for t in items if t in stops), key=rank)
    if words is None:
        return OrdinaryWords(len(items), tuple(hit_stop), 0, (), False)
    hit_dict = sorted((t for t in items if t in words), key=rank)
    return OrdinaryWords(len(items), tuple(hit_stop), len(hit_dict),
                         tuple(hit_dict[:examples]), True)


def suffix_word_claims(words: frozenset[str], *, min_length: int = 0,
                       suffixes: Iterable[str] = SUFFIXES,
                       examples: int = 8) -> dict[str, dict[str, Any]]:
    """Per-suffix: how many ordinary words it claims, and which.

    The ``-son`` row is the one the proposal names (`season`, `reason`,
    `lesson`, `prison`, `poison`) and the one that decides whether a minimum
    length is a guard or a fig leaf.
    """
    out: dict[str, dict[str, Any]] = {}
    for suffix in suffixes:
        claimed = sorted(w for w in words
                         if len(w) >= min_length and w.endswith(suffix))
        out[suffix] = {"claimed": len(claimed),
                       "examples": claimed[:examples],
                       "longest": claimed[-1] if claimed else None}
    return out


def min_length_that_kills(words: frozenset[str], suffix: str) -> int | None:
    """The shortest ``min_length`` at which ``suffix`` claims no ordinary word.

    ``None`` when no length works — i.e. the word list carries an entry as long
    as any name the rule would ever see, which is the answer that says a length
    guard cannot save this suffix.
    """
    claimed = [w for w in words if w.endswith(suffix)]
    if not claimed:
        return 0
    longest = max(len(w) for w in claimed)
    return longest + 1


# ---------------------------------------------------------------------------
# Bearer-weighted claim, on the shipped scale
# ---------------------------------------------------------------------------

def measure_predicate(member: Callable[[str], bool], population: Mapping[str, int],
                      *, label: str, examples: int = 12) -> ExposureResult:
    """:func:`lexicon_exposure.measure` for a rule that is not a word list.

    Same population, same arithmetic, same dataclass — so a suffix rule's number
    sits in the same column as a list's and a reader can compare them without
    being told they are comparable.
    """
    claimed = sorted((name for name in population if member(name)),
                     key=lambda n: (-population[n], n))
    return ExposureResult(
        matcher=label, case_fold=False, plurals=False,
        population=len(population), claimed=len(claimed),
        bearers_total=sum(population.values()),
        bearers_claimed=sum(population[n] for n in claimed),
        examples=tuple(claimed[:examples]),
    )


def population_under_given_floor(table: Mapping[str, int],
                                 given: frozenset[str]) -> dict[str, int]:
    """The surname population :func:`surname_population` would report if the
    `given` tier were cut at the floor that produced ``given``.

    This is the one place a rescue list touches a shipped ceiling. The stoplist
    gate's denominator is the census table *minus what the given-name guard
    carries*, so lowering ``GIVEN_NAME_MIN_BIRTHS`` — proposal C — moves a gate
    that has 0.10 pp of headroom, without anyone editing the stoplist.
    """
    return {name: bearers for name, bearers in table.items() if name not in given}


def stoplist_exposure(population: Mapping[str, int]) -> ExposureResult:
    """``stoplist surname exposure``, recomputed against a given population."""
    return measure(lexicon.stop_words(), population=dict(population))


# ---------------------------------------------------------------------------
# Load time and per-request cost
# ---------------------------------------------------------------------------

_NODE_BENCH = r"""
const zlib = require("node:zlib");
const chunks = [];
process.stdin.on("data", (c) => chunks.push(c));
process.stdin.on("end", () => {
  const buf = Buffer.concat(chunks);
  // `node -e SCRIPT ARG` leaves the executable at argv[0] and ARG at argv[1] —
  // there is no script path in between, which is the off-by-one that silently
  // runs zero repeats and reports an empty set.
  const reps = Number(process.argv[process.argv.length - 1]);
  const times = [];
  let size = 0;
  for (let i = 0; i < reps; i++) {
    const t0 = process.hrtime.bigint();
    const text = zlib.gunzipSync(buf).toString("utf8");
    const set = new Set(text.split("\n"));
    const t1 = process.hrtime.bigint();
    size = set.size;
    times.push(Number(t1 - t0) / 1e6);
  }
  times.sort((a, b) => a - b);
  process.stdout.write(JSON.stringify({
    ms_median: times[Math.floor(times.length / 2)],
    ms_min: times[0], entries: size,
  }));
});
"""

_RUBY_BENCH = r"""
require "zlib"
require "json"
require "set"
buf = $stdin.binmode.read
reps = ARGV[0].to_i
times = []
size = 0
reps.times do
  t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
  set = Set.new(Zlib.gunzip(buf).force_encoding("UTF-8").split("\n"))
  t1 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
  size = set.size
  times << (t1 - t0) * 1000.0
end
times.sort!
print JSON.generate({ ms_median: times[times.length / 2], ms_min: times.first,
                      entries: size })
"""


def load_cost_python(blob: bytes, repeats: int = LOAD_REPEATS) -> dict[str, float]:
    """Gunzip + split + set-build, the shape every port's asset loader uses."""
    times = []
    size = 0
    for _ in range(repeats):
        t0 = time.perf_counter()
        names = frozenset(gzip.decompress(blob).decode("utf-8").split("\n"))
        times.append((time.perf_counter() - t0) * 1000.0)
        size = len(names)
    times.sort()
    return {"ms_median": times[len(times) // 2], "ms_min": times[0], "entries": size}


def _run_bench(argv: list[str], script: str, blob: bytes,
               repeats: int) -> dict[str, float] | None:
    try:
        proc = subprocess.run(argv + [script, str(repeats)], input=blob,
                              capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return json.loads(proc.stdout.decode("utf-8"))


def load_cost_node(blob: bytes, repeats: int = LOAD_REPEATS) -> dict[str, float] | None:
    return _run_bench(["node", "-e"], _NODE_BENCH, blob, repeats)


def load_cost_ruby(blob: bytes, repeats: int = LOAD_REPEATS) -> dict[str, float] | None:
    return _run_bench(["ruby", "-e"], _RUBY_BENCH, blob, repeats)


def lookup_cost(tokens: list[str], names: frozenset[str],
                member: Callable[[str], bool]) -> dict[str, float]:
    """Nanoseconds per lookup, set membership versus the suffix rule.

    Timed on tokens the corpus actually produces rather than synthetic strings,
    because a hit and a miss are not the same work in either arm.
    """
    sample = (tokens * (LOOKUP_SAMPLES // max(len(tokens), 1) + 1))[:LOOKUP_SAMPLES]
    t0 = time.perf_counter()
    hits = sum(1 for t in sample if t in names)
    set_ns = (time.perf_counter() - t0) * 1e9 / len(sample)
    t0 = time.perf_counter()
    shape_hits = sum(1 for t in sample if member(t))
    shape_ns = (time.perf_counter() - t0) * 1e9 / len(sample)
    return {"set_ns": set_ns, "suffix_ns": shape_ns, "samples": len(sample),
            "set_hits": hits, "suffix_hits": shape_hits}


def corpus_tokens(corpus_id: str = "persuade-20") -> tuple[list[str], int]:
    """Lower-cased capitalised tokens from the over-fire corpus, and the essay
    count — the upper bound on how many lookups a corroboration channel adds per
    essay, since it can only ever ask about a token a capital already proposed.
    """
    from vicary.eval import corpus as corpus_mod

    _, essays = corpus_mod.load_essays(corpus_id)
    tokens: list[str] = []
    for _cid, text in essays:
        tokens.extend(m.group(0).lower() for m in re.finditer(r"\b[A-Z][a-z]{1,}\b", text))
    return tokens, len(essays)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

@dataclass
class ListPrice:
    """Every priced number for one candidate list."""

    name: str
    kind: str
    entries: int
    gz_bytes: int
    stoplist_claims: int
    stoplist_examples: tuple[str, ...]
    dict_claims: int
    dict_rate: float
    dict_examples: tuple[str, ...]
    claim_rate: float
    claim_surnames: int
    claim_bearers: int
    claim_examples: tuple[str, ...]
    notes: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> str:
        gz = f"{self.gz_bytes / 1024:,.0f} KB" if self.gz_bytes else "none"
        return (f"{self.name:<34}{self.entries:>9,}{gz:>12}"
                f"{self.stoplist_claims:>8}{self.dict_claims:>9}"
                f"{self.claim_rate * 100:>10.2f}%")


HEADER = (f"{'candidate list':<34}{'entries':>9}{'gz':>12}"
          f"{'stop':>8}{'dict':>9}{'claim':>10}")


def price_lists(*, ssa_zip: str = DEFAULT_SSA_ZIP, words_path: str = DEFAULT_WORDS,
                sections: Iterable[str] = ("census", "ssa", "morphology"),
                ) -> tuple[list[ListPrice], dict[str, Any]]:
    """Every list, every cost. Returns the rows and the surrounding context."""
    sections = set(sections)
    table = census_table()
    population = {name: bearers for name, bearers in table.items()
                  if not gazetteer.is_common_given_name(name)}
    try:
        words = load_word_list(words_path)
    except OSError:
        words = None
    context: dict[str, Any] = {
        "census_rows": len(table),
        "population": len(population),
        "bearers_total": sum(population.values()),
        "words_path": words_path if words is not None else None,
        "words_entries": len(words) if words is not None else 0,
        "asset_sizes": asset_sizes(),
        "stoplist_entries": len(lexicon.stop_words()),
    }
    rows: list[ListPrice] = []

    if "census" in sections:
        for floor in CENSUS_FLOORS:
            names = census_slice(table, floor)
            ow = ordinary_words(names, words, weight=table)
            ex = measure(frozenset(names), population=population)
            rows.append(ListPrice(
                name=f"census surnames >= {floor:,} bearers", kind="surname",
                entries=len(names), gz_bytes=len(packed(names)),
                stoplist_claims=ow.stoplist_count,
                stoplist_examples=ow.stoplist_hits[:12],
                dict_claims=ow.dict_hits, dict_rate=ow.dict_rate,
                dict_examples=ow.dict_examples, claim_rate=ex.rate,
                claim_surnames=ex.claimed, claim_bearers=ex.bearers_claimed,
                claim_examples=ex.examples))
            kept = guarded(names)
            gow = ordinary_words(kept, words, weight=table)
            gex = measure(frozenset(kept), population=population)
            rows.append(ListPrice(
                name="  \u2514 guarded (stop+KEEP+len>=5)", kind="surname-guarded",
                entries=len(kept), gz_bytes=len(packed(kept)),
                stoplist_claims=gow.stoplist_count,
                stoplist_examples=gow.stoplist_hits[:12],
                dict_claims=gow.dict_hits, dict_rate=gow.dict_rate,
                dict_examples=gow.dict_examples, claim_rate=gex.rate,
                claim_surnames=gex.claimed, claim_bearers=gex.bearers_claimed,
                claim_examples=gex.examples,
                notes={"dropped_total": len(names) - len(kept),
                       "dropped_stop": sum(1 for n in names
                                           if n in lexicon.stop_words()),
                       "dropped_keep": sum(1 for n in names
                                           if n in keep_tier_tokens()),
                       "dropped_short": sum(1 for n in names if len(n) < 5)}))

    if "ssa" in sections:
        births = ssa_births(ssa_zip)
        context["ssa_names_parsed"] = len(births)
        context["ssa_zip"] = str(Path(ssa_zip).expanduser())
        shipped_given = ssa_slice(births, 1_800)
        context["given_tier_reconstruction"] = _check_given_reconstruction(
            table, shipped_given)
        for floor in SSA_FLOORS:
            names = ssa_slice(births, floor)
            ow = ordinary_words(names, words, weight=births)
            ex = measure(frozenset(names), population=population)
            stop_pop = population_under_given_floor(table, names)
            stop_ex = stoplist_exposure(stop_pop)
            label = f"SSA given >= {floor:,} births" + (
                "  (SHIPPED)" if floor == 1_800 else "")
            rows.append(ListPrice(
                name=label, kind="given",
                entries=len(names), gz_bytes=len(packed(names)),
                stoplist_claims=ow.stoplist_count,
                stoplist_examples=ow.stoplist_hits[:12],
                dict_claims=ow.dict_hits, dict_rate=ow.dict_rate,
                dict_examples=ow.dict_examples, claim_rate=ex.rate,
                claim_surnames=ex.claimed, claim_bearers=ex.bearers_claimed,
                claim_examples=ex.examples,
                notes={"stoplist_gate_pct": stop_ex.rate * 100,
                       "stoplist_gate_population": len(stop_pop),
                       "delta_vs_shipped_tier": len(names) - len(shipped_given)}))

    if "morphology" in sections:
        for min_len in (0, 5, 6, 7, 8):
            member = suffix_member(min_len)
            ex = measure_predicate(member, population,
                                   label=f"suffix rule (min length {min_len})")
            claims = (sum(1 for w in words if member(w)) if words is not None else 0)
            stop_hits = tuple(sorted(w for w in lexicon.stop_words() if member(w)))
            examples = (tuple(sorted((w for w in words if member(w)),
                                     key=lambda w: (len(w), w))[:15])
                        if words is not None else ())
            rows.append(ListPrice(
                name=f"morphology suffixes, min len {min_len}", kind="shape",
                entries=len(SUFFIXES), gz_bytes=0,
                stoplist_claims=len(stop_hits), stoplist_examples=stop_hits[:12],
                dict_claims=claims,
                dict_rate=claims / len(words) if words else 0.0,
                dict_examples=examples, claim_rate=ex.rate,
                claim_surnames=ex.claimed, claim_bearers=ex.bearers_claimed,
                claim_examples=ex.examples))
        per_suffix: dict[str, dict[str, Any]] = {}
        for suffix in SUFFIXES:
            ex = measure_predicate(suffix_member(0, (suffix,)), population,
                                   label=f"-{suffix}")
            per_suffix[suffix] = {
                "surnames": ex.claimed, "bearers": ex.bearers_claimed,
                "claim_pct": ex.rate * 100, "examples": list(ex.examples[:6]),
                "dict_words": (sum(1 for w in words if w.endswith(suffix))
                               if words is not None else None),
            }
        context["per_suffix"] = per_suffix
        if words is not None:
            context["suffix_word_claims"] = suffix_word_claims(words)
            context["suffix_min_length_kill"] = {
                s: min_length_that_kills(words, s) for s in SUFFIXES}

    return rows, context


def _check_given_reconstruction(table: Mapping[str, int],
                                given: frozenset[str]) -> dict[str, Any]:
    """Does a floor-1,800 cut reproduce what the SHIPPED given tier removes?

    The simulated lower floors are only trustworthy if the simulation reproduces
    the shipped one, so it is checked rather than asserted. A mismatch on census
    surnames is the number that matters, because that is the population every
    figure here is weighed against.
    """
    shipped_removed = {n for n in table if gazetteer.is_common_given_name(n)}
    simulated_removed = {n for n in table if n in given}
    return {
        "shipped_removes": len(shipped_removed),
        "simulated_removes": len(simulated_removed),
        "only_shipped": sorted(shipped_removed - simulated_removed)[:10],
        "only_simulated": sorted(simulated_removed - shipped_removed)[:10],
        "identical": shipped_removed == simulated_removed,
    }


def price_load(rows: Iterable[ListPrice], *, ssa_zip: str = DEFAULT_SSA_ZIP,
               repeats: int = LOAD_REPEATS) -> dict[str, Any]:
    """Load time in three languages, plus the per-request lookup cost."""
    table = census_table()
    arms = {
        "census >= 1,000 (24,889)": census_slice(table, 1_000),
        "census >= 10,000 (3,567)": census_slice(table, 10_000),
        "census >= 100,000 (314)": census_slice(table, 100_000),
    }
    try:
        births = ssa_births(ssa_zip)
        arms["SSA given >= 1,800 (shipped)"] = ssa_slice(births, 1_800)
        arms["SSA given >= 100"] = ssa_slice(births, 100)
        arms["SSA given >= 1 (any record)"] = ssa_slice(births, 1)
    except (OSError, ValueError):
        pass
    blobs: dict[str, bytes] = {label: packed(names) for label, names in arms.items()}
    # The yardstick, and it is the SHIPPED bytes rather than a rebuild: the
    # question a load number has to answer is "how much of what import already
    # costs would this add", so the denominator must be the real asset.
    try:
        blobs["shipped gazetteer asset"] = gazetteer.asset_path().read_bytes()
    except OSError:  # pragma: no cover - only outside a checkout
        pass
    out: dict[str, Any] = {}
    for label, blob in blobs.items():
        out[label] = {
            "gz_bytes": len(blob),
            "python": load_cost_python(blob, repeats),
            "node": load_cost_node(blob, repeats),
            "ruby": load_cost_ruby(blob, repeats),
        }
    tokens, essays = corpus_tokens()
    biggest = max(arms.values(), key=len)
    out["lookups"] = dict(
        lookup_cost(tokens, frozenset(biggest), suffix_member(6)),
        tokens=len(tokens), essays=essays,
        capitalised_tokens_per_essay=len(tokens) / essays)
    return out


def probe_targets(table: Mapping[str, int], births: Mapping[str, int] | None,
                  names: Iterable[str] = TARGET_NAMES) -> list[dict[str, Any]]:
    """Per name: census bearers, SSA births, and the floor each list needs.

    An aggregate coverage figure cannot answer "does this rescue Alvarez", and
    that is the question the proposal is actually asking. A name with no birth
    record is unreachable by a given-name list at ANY floor, which is a different
    kind of answer from "needs a lower floor".
    """
    out = []
    for name in names:
        bearers = table.get(name, 0)
        record = (births or {}).get(name, 0)
        out.append({
            "name": name,
            "bearers": bearers,
            "births": record,
            "census_floor_needed": bearers or None,
            "given_floor_needed": record or None,
            "suffix_hit": suffix_member(0)(name),
        })
    return out


def complementarity(population: Mapping[str, int], arms: Mapping[str, Any],
                    ) -> dict[str, dict[str, Any]]:
    """Bearers each arm reaches that the others do not.

    The morphology channel's whole argument is that "the tail it reaches is
    exactly the tail a US-births list misses". That is an overlap claim, and an
    overlap claim has to be measured against the other arms rather than alone.
    ``arms`` maps a label to either a set of names or a membership predicate.
    """
    def member_of(arm: object) -> Callable[[str], bool]:
        if callable(arm):
            return arm
        names = frozenset(arm)  # type: ignore[call-overload]
        return lambda token: token in names

    members = {label: member_of(arm) for label, arm in arms.items()}
    hits = {label: {n for n in population if fn(n)} for label, fn in members.items()}
    total = sum(population.values())
    out: dict[str, dict[str, Any]] = {}
    for label, names in hits.items():
        others: set[str] = set()
        for other, got in hits.items():
            if other != label:
                others |= got
        only = names - others
        out[label] = {
            "surnames": len(names),
            "bearers": sum(population[n] for n in names),
            "pct": 100.0 * sum(population[n] for n in names) / total,
            "unique_surnames": len(only),
            "unique_bearers": sum(population[n] for n in only),
            "unique_pct": 100.0 * sum(population[n] for n in only) / total,
            "unique_examples": sorted(only, key=lambda n: -population[n])[:6],
        }
    return out


def report(rows: list[ListPrice], context: dict[str, Any]) -> str:
    sizes = context.get("asset_sizes", {})
    lines = [
        "CANDIDATE RESCUE LISTS, PRICED ON VICARY'S OWN GATES",
        "",
        f"population: {context['population']:,} US surnames, "
        f"{context['bearers_total']:,} bearers "
        f"(census table minus what the given-name guard carries)",
        f"stoplist: {context['stoplist_entries']} words · word list: "
        f"{context.get('words_path') or 'ABSENT'} "
        f"({context.get('words_entries', 0):,} lowercase entries)",
        f"yardsticks: gazetteer asset "
        f"{sizes.get('gazetteer_asset', 0) / 1024:,.0f} KB · shipped census table "
        f"{sizes.get('census_table', 0) / 1024:,.0f} KB (repo only, never packaged)",
        "",
        HEADER,
        "-" * 82,
    ]
    lines += [row.as_row() for row in rows]
    lines += [
        "-" * 82,
        "stop = entries on the shipped stoplist · dict = entries in the system",
        "word list · claim = bearer-weighted share of the surname population the",
        "list claims, on the same scale as `bare-surname exposure` (1.25% bar)",
        "and `stoplist surname exposure` (0.60% bar). For a SURNAME list a high",
        "claim is reach; for a given-name list or a shape rule it is over-fire",
        "pressure. Neither spends the KEEP-side ceilings.",
        "",
        "The SHIPPED given tier reads 0.00% BY CONSTRUCTION — the population",
        "subtracts it — so every lower given floor's claim is the MARGINAL",
        "surname population a wider tier would newly claim, over what ships.",
    ]
    for row in rows:
        if row.stoplist_examples:
            lines.append(f"  {row.name}: stoplist -> "
                         + ", ".join(row.stoplist_examples))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vicary.eval.candidate_cost",
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Prices candidate lists; builds nothing and ships nothing.")
    parser.add_argument("--section", action="append",
                        choices=["census", "ssa", "morphology", "load"],
                        help="repeatable; defaults to every section")
    parser.add_argument("--ssa-zip", default=DEFAULT_SSA_ZIP,
                        help=f"SSA baby-names archive (default: {DEFAULT_SSA_ZIP})")
    parser.add_argument("--words", default=DEFAULT_WORDS,
                        help=f"system word list (default: {DEFAULT_WORDS})")
    parser.add_argument("--load-repeats", type=int, default=LOAD_REPEATS,
                        metavar="N",
                        help=f"repeats per load-time arm (default {LOAD_REPEATS}); "
                             "raise it before quoting a ratio — the yardstick is "
                             "the noisiest term in one")
    parser.add_argument("--json", default=None, metavar="PATH",
                        help="also write every measured number as JSON")
    args = parser.parse_args(argv)

    sections = set(args.section or ["census", "ssa", "morphology", "load"])
    rows, context = price_lists(ssa_zip=args.ssa_zip, words_path=args.words,
                                sections=sections)
    print(report(rows, context))

    if "ssa" in sections:
        print("\nGIVEN-FLOOR EFFECT ON `stoplist surname exposure` (bar 0.60%)")
        print("  the one shipped ceiling a rescue list moves: the gate's")
        print("  denominator is the census minus the given tier, so widening the")
        print("  tier re-weights it without anyone touching the stoplist.")
        for row in rows:
            if row.kind == "given":
                print(f"  {row.name:<34}"
                      f"{row.notes['stoplist_gate_pct']:>8.3f}%   population "
                      f"{row.notes['stoplist_gate_population']:,}")
        check = context.get("given_tier_reconstruction", {})
        print(f"  reconstruction check at 1,800: shipped removes "
              f"{check.get('shipped_removes'):,} census surnames, simulated "
              f"{check.get('simulated_removes'):,} — "
              f"{'identical' if check.get('identical') else 'DIFFERENT'}")

    if "morphology" in sections and context.get("per_suffix"):
        print("\nPER-SUFFIX BEARER-WEIGHTED CLAIM (no length guard)")
        print(f"  {'suffix':<12}{'surnames':>9}{'bearers':>12}{'claim':>9}"
              f"{'dict':>7}  most-borne")
        for suffix, info in context["per_suffix"].items():
            print(f"  -{suffix:<11}{info['surnames']:>9,}{info['bearers']:>12,}"
                  f"{info['claim_pct']:>8.2f}%{info['dict_words']:>7}  "
                  + ", ".join(info["examples"][:4]))

    if "morphology" in sections and context.get("suffix_word_claims"):
        print("\nPER-SUFFIX ORDINARY-WORD CLAIMS (no length guard)")
        print(f"  {'suffix':<12}{'words':>7}  examples / longest")
        for suffix, info in context["suffix_word_claims"].items():
            kill = context["suffix_min_length_kill"].get(suffix)
            print(f"  -{suffix:<11}{info['claimed']:>7}  "
                  f"{', '.join(info['examples'][:5])}"
                  f"{' … longest ' + str(info['longest']) if info['longest'] else ''}"
                  f"  [min length to kill: {kill}]")

    if {"census", "ssa"} & sections:
        table = census_table()
        try:
            births = ssa_births(args.ssa_zip)
        except (OSError, ValueError):
            births = None
        print("\nDOES ANY LIST REACH THE NAMES THE PROPOSAL ARGUES ABOUT?")
        print(f"  {'name':<12}{'bearers':>10}{'births':>8}  census floor / given "
              f"floor / suffix")
        probes = probe_targets(table, births)
        for probe in probes:
            cf = (f"<= {probe['census_floor_needed']:,}"
                  if probe["census_floor_needed"] else "NOT IN CENSUS")
            gf = (f"<= {probe['given_floor_needed']:,}"
                  if probe["given_floor_needed"] else "NO BIRTH RECORD - "
                  "UNREACHABLE AT ANY FLOOR")
            print(f"  {probe['name']:<12}{probe['bearers']:>10,}"
                  f"{probe['births']:>8,}  {cf} / {gf} / "
                  f"{'yes' if probe['suffix_hit'] else 'no'}")
        context["target_probes"] = probes

        population = {n: b for n, b in table.items()
                      if not gazetteer.is_common_given_name(n)}
        arms: dict[str, Any] = {
            "census >= 10,000 (guarded)": guarded(census_slice(table, 10_000)),
            "morphology (min len 6)": suffix_member(6),
        }
        if births is not None:
            arms["SSA given >= 100"] = ssa_slice(births, 100)
        overlap = complementarity(population, arms)
        context["complementarity"] = overlap
        print("\nWHAT EACH ARM REACHES THAT THE OTHERS DO NOT (bearer-weighted)")
        print(f"  {'arm':<30}{'reach':>9}{'unique':>9}  unique examples")
        for label, info in overlap.items():
            print(f"  {label:<30}{info['pct']:>8.2f}%{info['unique_pct']:>8.2f}%  "
                  + ", ".join(info["unique_examples"][:4]))

    load: dict[str, Any] = {}
    if "load" in sections:
        load = price_load(rows, ssa_zip=args.ssa_zip, repeats=args.load_repeats)
        print("\nLOAD TIME, THREE PORTS (gunzip + split + set, "
              f"{args.load_repeats} repeats)")
        print(f"  {'list':<32}{'gz KB':>8}{'python':>12}{'node':>12}{'ruby':>12}"
              "   (median/min ms)")
        for label, info in load.items():
            if label == "lookups":
                continue
            def _ms(port: str, row: dict[str, Any] = info) -> str:
                # Median AND min, because a single run's median is not stable:
                # measured 2026-09-10, Ruby's own asset-load median moved 127.5
                # -> 82.5 ms between two consecutive runs on an idle machine,
                # which is a 1.5x swing in the DENOMINATOR of every ratio a
                # reader would compute. The min is the estimator to quote.
                got = row.get(port)
                return (f"{got['ms_median']:.1f}/{got['ms_min']:.1f}"
                        if got else "n/a")
            print(f"  {label:<32}{info['gz_bytes'] / 1024:>8.0f}"
                  f"{_ms('python'):>12}{_ms('node'):>12}{_ms('ruby'):>12}")
        look = load["lookups"]
        print(f"\n  per-request: {look['capitalised_tokens_per_essay']:.0f} "
              f"capitalised tokens/essay over {look['essays']} essays; "
              f"set membership {look['set_ns']:.0f} ns/lookup, suffix rule "
              f"{look['suffix_ns']:.0f} ns/lookup")

    if args.json:
        payload = {"context": context,
                   "lists": [row.__dict__ for row in rows],
                   "load": load}
        Path(args.json).write_text(json.dumps(payload, indent=2, default=str),
                                   encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
