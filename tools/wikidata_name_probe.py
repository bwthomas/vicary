"""Size, sample and price a Wikidata name population as a broader name list.

The `given` tier the gazetteer ships is SSA US births (names at >= 1,800 births
since 1880) and the surname evidence the gates score against is a US census
table. Both are US-weighted *by construction*, so the coverage they give a
transliterated or immigrant surname is thin for a reason no threshold can fix.
Wikidata is not US-weighted, and the build already sweeps it — `P31 wd:Q101352`
(family name) and the given-name classes are a **query change**, not a new
dependency.

This module answers the four questions that decide whether that is worth doing,
in the order that lets an expensive one be skipped:

1. ``counts``  — how big is the population? Counting queries only; nothing is
   downloaded until the size is known.
2. ``fetch``   — pull the single-token labels for a class, cached to disk.
3. ``sample``  — what does it reach that SSA and the census do not? Names with
   **no** US birth record at any floor and **no** census presence at >= 100
   bearers, printed rather than described.
4. ``price``   — what does it cost on the bearer-weighted exposure scale
   (:mod:`vicary.eval.lexicon_exposure`), and how many bytes.

Transport is :func:`vicary_build.gazetteer._query` — the same qlever endpoint,
the same retry and timeout handling, and the same refusal to treat an empty
result as an answer. That last property is the reason this file reuses the
builder's machinery rather than rolling its own: a name tier that silently comes
back empty is the failure mode `gazetteer`'s module docstring exists to warn
about, and a probe that reports "0 names" from a throttled endpoint would send
exactly the wrong verdict upstream.

Read the exposure number in the right direction, because the instrument was
built for the opposite one. :func:`vicary.eval.lexicon_exposure.measure` prices
a *veto* — a list that suppresses a name candidate, so a surname it claims is a
surname that leaks, and the shipped ``bare-surname exposure`` gate (1.199%,
bar 1.25%) is on that scale. A Wikidata name list used as a **corroboration**
channel points the other way: a surname it claims is a surname that gets
redacted. The same number is therefore *reach* in one role and *exposure* in the
other, and this module prints both readings side by side rather than picking
one. The cost of a corroboration list is over-fire on ordinary words, so
``price`` also reports the ordinary-word overlap, which is the quantity that
actually moves the binding gate.

Usage::

    python tools/wikidata_name_probe.py counts
    python tools/wikidata_name_probe.py fetch --class family --cache-dir DIR
    python tools/wikidata_name_probe.py sample --cache-dir DIR --samples 30 \\
        --ssa-zip PATH
    python tools/wikidata_name_probe.py price --cache-dir DIR
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "asset"))
sys.path.insert(0, str(REPO_ROOT / "python" / "src"))

from vicary_build import gazetteer  # noqa: E402

#: The Wikidata classes a name population could be drawn from, and why each one
#: is on the list rather than assumed.
#:
#: ``P31 wd:Q101352`` is the family-name class the idea names. The three
#: given-name classes are separate items in Wikidata rather than subclasses of a
#: single root that a ``P279*`` walk would collect, so each has to be counted on
#: its own — and at least one of them is routinely near-empty in favour of its
#: siblings, which is precisely the kind of thing a count answers and a guess
#: does not.
NAME_CLASSES: dict[str, tuple[str, str]] = {
    "family": ("Q101352", "family name"),
    "given": ("Q202444", "given name"),
    "female-given": ("Q11879590", "female given name"),
    "male-given": ("Q12308941", "male given name"),
    "unisex-given": ("Q3409032", "unisex given name"),
}

#: Label languages the gazetteer build accepts, restated here so the probe
#: measures the population the build could actually consume. ``mul`` is not
#: optional — Wikidata migrates labels spelled identically across languages into
#: it and *removes* the ``@en`` one, so an English-only filter silently drops
#: entries. See :data:`vicary_build.gazetteer.LABEL_LANGUAGES`.
LABEL_LANGUAGES = gazetteer.LABEL_LANGUAGES

#: The shipped gazetteer, for the size comparison. Relative to the repository
#: root; the probe is a checkout tool and has no meaning outside one.
GAZETTEER_ASSET = Path("asset") / "data" / "notability.txt.gz"

#: Default scratch directory for fetched labels. Deliberately inside the tree
#: and deliberately dot-prefixed: an operator can point ``--cache-dir`` at a
#: corpus directory outside the repository instead, and should, because these
#: are raw upstream rows rather than anything the repository wants to own.
DEFAULT_CACHE = Path("tools") / ".probe-cache"


def _lang_filter(var: str = "?l") -> str:
    langs = ", ".join(f"'{code}'" for code in LABEL_LANGUAGES)
    return f"FILTER(LANG({var}) IN ({langs})) "


def _count(tag: str, where: str, *, distinct: bool = True,
           timeout: int = 600) -> tuple[int | None, str]:
    """One counting query. Returns ``(count, note)``; ``None`` means it failed.

    Shaped ``(?l ?s)`` so it goes through the builder's parser unchanged: the
    tag rides in the label column and the count in the sitelink column.

    ``distinct`` wraps the pattern in a ``SELECT DISTINCT`` subquery rather than
    writing ``COUNT(DISTINCT ?x)``. Not a style choice — measured 2026-09-10,
    qlever answers the inline form with ``Operation timed out. Last operation:
    GroupBy (implicit)`` on ``rdfs:label`` over the family-name class, and
    answers the subquery form in seconds.

    A failure is returned rather than raised, and it is returned *as a failure*
    with the endpoint's own message attached. A counting probe that turns a
    throttled or timed-out query into a zero would report "this population does
    not exist", which is the exact confusion :mod:`vicary_build.gazetteer`'s
    docstring warns about; the caller prints the note and the cell reads
    ``FAILED``, never ``0``.
    """
    inner = (f"{{ SELECT DISTINCT ?x WHERE {{ {where} }} }}" if distinct
             else f"{{ {where} }}")
    try:
        rows = gazetteer._query(
            f'SELECT ("{tag}" AS ?l) (COUNT(?x) AS ?s) WHERE {inner}',
            timeout=timeout,
        )
    except (RuntimeError, OSError) as exc:  # endpoint said no; say so upward
        return None, str(exc).split("\n")[0][:160]
    return rows[0][1], ""


def _cell(value: object) -> str:
    """A count for the table, or ``FAILED`` — deliberately never ``0``."""
    return f"{value:,}" if isinstance(value, int) else "FAILED"


def counts(args: argparse.Namespace) -> int:
    """How big is it? Items, distinct labels, and the single-token slice."""
    print(f"endpoint: {gazetteer.SPARQL_ENDPOINT}")
    print(f"label languages accepted by the build: {', '.join(LABEL_LANGUAGES)}\n")
    header = (f"{'class':<14}{'qid':<12}{'items':>12}{'labels/all':>13}"
              f"{'labels/en+mul':>15}{'1-token/en+mul':>16}")
    print(header)
    print("-" * len(header))
    out: dict[str, dict[str, object]] = {}
    notes: list[str] = []
    for key, (qid, _label) in NAME_CLASSES.items():
        if args.only and key not in args.only:
            continue
        probes = {
            "items": (f"?x wdt:P31 wd:{qid}", True),
            "labels_all": (f"?i wdt:P31 wd:{qid} ; rdfs:label ?x", True),
            "labels_en_mul": (
                f"?i wdt:P31 wd:{qid} ; rdfs:label ?x . {_lang_filter('?x')}", True),
            "single_token_en_mul": (
                f"?i wdt:P31 wd:{qid} ; rdfs:label ?x . {_lang_filter('?x')}"
                'FILTER(!CONTAINS(STR(?x), " "))', True),
        }
        row: dict[str, object] = {"qid": qid}
        for field, (where, distinct) in probes.items():
            value, note = _count(key, where, distinct=distinct, timeout=args.timeout)
            row[field] = value
            if note:
                notes.append(f"{key}.{field}: {note}")
        out[key] = row

        cells = [_cell(row[f]) for f in
                 ("items", "labels_all", "labels_en_mul", "single_token_en_mul")]
        print(f"{key:<14}{qid:<12}{cells[0]:>12}{cells[1]:>13}"
              f"{cells[2]:>15}{cells[3]:>16}", flush=True)
    if notes:
        print("\nqueries the endpoint refused — these are NOT zeros:")
        for note in notes:
            print(f"  {note}")
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}-labels.json"


def fetch(args: argparse.Namespace) -> int:
    """Download the single-token labels for one class and cache them.

    Single-token only, and that is not a convenience. Every consumer this could
    feed — the given-name corroboration channel, a surname channel — tests a
    *bare token* against the list; a multi-word label can never match one, so
    fetching it would inflate the size and the exposure denominator without
    changing any decision.
    """
    cache_dir = Path(args.cache_dir).expanduser()
    cache_dir.mkdir(parents=True, exist_ok=True)
    for key in args.only or list(NAME_CLASSES):
        qid, label = NAME_CLASSES[key]
        target = _cache_path(cache_dir, key)
        if target.exists() and not args.force:
            print(f"{key}: cached ({target})")
            continue
        print(f"{key}: querying {qid} ({label}) ...", flush=True)
        rows = gazetteer._query(
            "SELECT DISTINCT ?l (1 AS ?s) WHERE { "
            f"?i wdt:P31 wd:{qid} ; rdfs:label ?l . {_lang_filter()}"
            'FILTER(!CONTAINS(STR(?l), " ")) }',
            timeout=args.timeout,
        )
        labels = sorted({row[0] for row in rows})
        target.write_text(json.dumps(labels, ensure_ascii=False), encoding="utf-8")
        print(f"{key}: {len(labels):,} distinct single-token labels -> {target}")
    return 0


def load_labels(cache_dir: Path, keys: list[str]) -> dict[str, list[str]]:
    """Read cached label lists, raising rather than substituting an empty one."""
    out: dict[str, list[str]] = {}
    for key in keys:
        path = _cache_path(cache_dir, key)
        if not path.exists():
            raise FileNotFoundError(f"{path} — run `fetch --class {key}` first")
        labels = json.loads(path.read_text(encoding="utf-8"))
        if not labels:
            raise RuntimeError(
                f"{path} holds zero labels. That is a failed query cached as a "
                "result, not a population of no names — delete it and re-fetch.")
        out[key] = labels
    return out


def _is_latin(label: str) -> bool:
    """True when every letter in ``label`` is Latin script.

    The single characteristic that separates a label a US student essay could
    carry from one it could not. Not a quality judgement — a Han or Cyrillic
    label is a perfectly good name, it simply cannot be the token a
    capitalisation-driven English detector proposed.
    """
    letters = [c for c in label if c.isalpha()]
    return bool(letters) and all("LATIN" in unicodedata.name(c, "") for c in letters)


def _keys(labels: list[str]) -> dict[str, str]:
    """``{normalised key: first label producing it}``, folded the builder's way."""
    out: dict[str, str] = {}
    for label in labels:
        key = gazetteer.normalize(label)
        if key and key not in out:
            out[key] = label
    return out


def _reference_tables(args: argparse.Namespace) -> tuple[dict[str, int], dict[str, int]]:
    """The two US tables this population is being compared against."""
    from vicary.eval import census

    ssa = gazetteer.read_ssa_given_names(args.ssa_zip)
    surnames = (census.load_shipped_census() if census.shipped_dir() is not None
                else census.load_census())
    return ssa, surnames


def sample(args: argparse.Namespace) -> int:
    """What does it reach that SSA and the census do not?

    The residual is defined tightly on purpose: no SSA birth record at **any**
    floor (not the shipped 1,800 one — the whole table) and no census presence
    at >= 100 bearers. A name in that set is one neither US source could have
    supplied at any threshold, which is the only population that argues for
    adding a source rather than lowering a number.
    """
    cache_dir = Path(args.cache_dir).expanduser()
    keys = args.only or list(NAME_CLASSES)
    labels = load_labels(cache_dir, keys)
    ssa, surnames = _reference_tables(args)
    print(f"SSA table: {len(ssa):,} distinct given names (any birth count)")
    print(f"census table: {len(surnames):,} surnames at >= 100 bearers\n")

    rng = random.Random(args.seed)
    for key in keys:
        folded = _keys(labels[key])
        latin = {k: v for k, v in folded.items() if _is_latin(v)}
        residual = {k: v for k, v in latin.items()
                    if k not in ssa and k not in surnames}
        alpha = {k: v for k, v in residual.items()
                 if v.isalpha() and args.min_length <= len(v)}
        print(f"[{key}] {len(folded):,} normalised keys · {len(latin):,} Latin-script "
              f"· {len(residual):,} in neither US table "
              f"({100.0 * len(residual) / max(len(latin), 1):.1f}% of Latin) "
              f"· {len(alpha):,} alphabetic and >= {args.min_length} chars")
        picks = rng.sample(sorted(alpha.values()), min(args.samples, len(alpha)))
        for i, label in enumerate(sorted(picks), 1):
            print(f"  {i:>3}. {label}")
        print()
    return 0


def price(args: argparse.Namespace) -> int:
    """What does it cost — on the exposure scale, and in bytes."""
    from vicary.eval import lexicon_exposure

    cache_dir = Path(args.cache_dir).expanduser()
    keys = args.only or list(NAME_CLASSES)
    labels = load_labels(cache_dir, keys)
    population = lexicon_exposure.surname_population()
    bar = args.bar

    print(f"population: {len(population):,} US surnames, "
          f"{sum(population.values()):,} bearers "
          f"(census table, minus what the given-name guard carries)")
    print(f"shipped bare-surname exposure gate: 1.199% against a {bar:.3f}% bar\n")

    header = (f"{'label set':<30}{'entries':>10}{'surnames':>10}"
              f"{'bearers':>13}{'share':>9}{'gz KB':>9}")
    print(header)
    print("-" * len(header))

    combined: set[str] = set()
    for key in keys:
        folded = set(_keys(labels[key]))
        combined |= folded
        _row(key, folded, population, lexicon_exposure)
    if len(keys) > 1:
        _row("ALL " + "+".join(keys), combined, population, lexicon_exposure)
    guarded = _guarded(combined)
    _row("^ guarded", guarded, population, lexicon_exposure)

    asset = REPO_ROOT / GAZETTEER_ASSET
    if asset.exists():
        print(f"\nshipped gazetteer {GAZETTEER_ASSET}: "
              f"{asset.stat().st_size / 1024:,.0f} KB gzipped")

    # The mirror reading. A corroboration list's real cost is the ordinary words
    # it claims, not the surnames — see the module docstring.
    if args.words and Path(args.words).exists():
        words = lexicon_exposure.load_word_list(args.words)
        print(f"\nordinary-word overlap ({args.words}, {len(words):,} "
              "lowercase-only entries):")
        for key in keys:
            folded = set(_keys(labels[key]))
            hit = folded & words
            print(f"  {key:<20}{len(hit):>9,} of {len(folded):,} labels "
                  f"({100.0 * len(hit) / max(len(folded), 1):.1f}%) are ordinary "
                  "English words")
        hit = combined & words
        print(f"  {'ALL':<20}{len(hit):>9,} of {len(combined):,} labels "
              f"({100.0 * len(hit) / max(len(combined), 1):.1f}%)")
        print(f"  examples: {', '.join(sorted(hit)[:20])}")

    corpus = REPO_ROOT / args.corpus
    if corpus.exists():
        _prose_proxy(corpus, combined, min_length=args.min_length, label="raw")
        # The steel-manned form. Idea B names three guards for a list-driven
        # channel — stoplist, KEEP-tier subtraction, minimum length — and a
        # verdict reached without them prices a strawman. Applied here so the
        # number that decides is the guarded one.
        print(f"\nguards: -{len(combined) - len(guarded):,} entries "
              f"(stop words, and anything a KEEP tier already claims) -> "
              f"{len(guarded):,}")
        _prose_proxy(corpus, guarded, min_length=args.min_length, label="guarded")
    return 0


def _guarded(folded: set[str]) -> set[str]:
    """``folded`` minus the two subtractions any such channel would carry."""
    from vicary_build import lexicon

    from vicary import gazetteer as runtime

    stop = {gazetteer.normalize(w) for w in lexicon.stop_words()}
    return {key for key in folded
            if key not in stop
            and not runtime.is_notable(key)
            and not runtime.is_common_given_name(key)}


def _prose_proxy(corpus: Path, folded: set[str], *, min_length: int,
                 label: str = "raw") -> None:
    """How often the list would say "that is a name" on real student prose.

    The over-fire proxy, and the number that decides a corroboration channel.
    Over-fire on ``persuade-20`` is the binding gate — 7.400 spans/essay against
    a 7.40 ceiling, **zero** headroom — and a corroboration channel spends its
    entire cost there, because it cannot create a candidate, only un-suppress
    one capitalisation already proposed.

    So the population that matters is *capitalised* tokens, split by sentence
    position: a mid-sentence capital is the surface form
    ``mid_sentence_corroboration`` adjudicates, and a sentence-initial capital
    is the one it cannot lean on. Both are reported because a channel consulted
    at either position pays at both.
    """
    import re

    essays = json.loads(corpus.read_text(encoding="utf-8"))["essays"]
    token_re = re.compile(r"[A-Za-z][A-Za-z'-]*")
    initial = mid = initial_hit = mid_hit = 0
    hits: dict[str, int] = {}
    for essay in essays:
        text = essay["text"]
        at_start = True
        for match in token_re.finditer(text):
            token = match.group(0)
            before = text[:match.start()].rstrip()
            at_start = not before or before[-1] in ".!?\"')" or before[-1] == "\n"
            if not token[0].isupper():
                continue
            key = gazetteer.normalize(token)
            claimed = len(token) >= min_length and key in folded
            if at_start:
                initial += 1
                initial_hit += claimed
            else:
                mid += 1
                mid_hit += claimed
            if claimed:
                hits[token] = hits.get(token, 0) + 1
    n = len(essays)
    print(f"\nprose proxy [{label}] on {corpus.name} ({n} essays), "
          f"tokens >= {min_length} chars:")
    print(f"  mid-sentence capitals      {mid:>7,}  claimed {mid_hit:>6,} "
          f"({100.0 * mid_hit / max(mid, 1):.1f}%)  = {mid_hit / n:.2f} per essay")
    print(f"  sentence-initial capitals  {initial:>7,}  claimed {initial_hit:>6,} "
          f"({100.0 * initial_hit / max(initial, 1):.1f}%)  = "
          f"{initial_hit / n:.2f} per essay")
    top = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    print("  most-claimed tokens: "
          + ", ".join(f"{tok}({count})" for tok, count in top))


def _row(name: str, folded: set[str], population: dict[str, int], module) -> None:
    result = module.measure(frozenset(folded), matcher="exact", population=population)
    payload = gzip.compress("\n".join(sorted(folded)).encode("utf-8"), 9)
    print(f"{name:<30}{len(folded):>10,}{result.claimed:>10,}"
          f"{result.bearers_claimed:>13,}{100.0 * result.rate:>8.3f}%"
          f"{len(payload) / 1024:>9,.0f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Steps are ordered so an expensive one can be skipped: run "
               "`counts` before `fetch`.")
    sub = parser.add_subparsers(dest="step", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--class", dest="only", action="append",
                       choices=sorted(NAME_CLASSES),
                       help="repeatable; defaults to every class")

    p_counts = sub.add_parser("counts", help="size the population; no download")
    add_common(p_counts)
    p_counts.add_argument("--json", help="also write the counts here")
    p_counts.add_argument("--timeout", type=int, default=600)
    p_counts.set_defaults(func=counts)

    p_fetch = sub.add_parser("fetch", help="download single-token labels")
    add_common(p_fetch)
    p_fetch.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    p_fetch.add_argument("--force", action="store_true", help="re-query a cached class")
    p_fetch.add_argument("--timeout", type=int, default=600)
    p_fetch.set_defaults(func=fetch)

    p_sample = sub.add_parser(
        "sample", help="names in neither SSA (any floor) nor the census (>=100)")
    add_common(p_sample)
    p_sample.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    p_sample.add_argument("--ssa-zip", required=True,
                          help="local copy of the SSA names archive")
    p_sample.add_argument("--samples", type=int, default=30)
    p_sample.add_argument("--min-length", type=int, default=3)
    p_sample.add_argument("--seed", type=int, default=20260910)
    p_sample.set_defaults(func=sample)

    p_price = sub.add_parser("price", help="bearer-weighted exposure and bytes")
    add_common(p_price)
    p_price.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    p_price.add_argument("--bar", type=float, default=1.25,
                         help="the shipped bare-surname exposure bar, in percent")
    p_price.add_argument("--words", default="/usr/share/dict/words",
                         help="word list for the ordinary-word mirror reading")
    p_price.add_argument("--corpus",
                         default=str(Path("conformance") / "corpora"
                                     / "persuade-20" / "essays.json"),
                         help="prose corpus for the over-fire proxy, relative "
                              "to the repository root")
    p_price.add_argument("--min-length", type=int, default=3,
                         help="ignore claimed tokens shorter than this, the "
                              "cheapest guard any such channel would carry")
    p_price.set_defaults(func=price)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
