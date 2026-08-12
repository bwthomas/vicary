"""Which essay corpus the corpus gates measure, and how it is loaded.

Three of the nine gates need real student prose. Until now that prose was
ASAP-AES and only ASAP-AES: the essay set, the row limit, the column names and
the file encoding were constants spread across three ports, and the corpus
reached the code through one environment variable pointing at the operator's own
copy. Two things followed from that, and neither was a feature.

The first is that four gates were unmeasurable anywhere but on a machine that
happened to hold a non-redistributable file. A board of five greens and four
blanks reads very much like a board of nine greens, and CI had never once
measured them.

The second is subtler and is the reason this module exists rather than a second
environment variable. Because the selection was welded in, *pointing the harness
at different prose was not a supported operation* — so the obvious way to try one
was to hand it a file of the same shape and read the numbers off the same bars.
Two of the three corpus gates are properties of the prose, not of the detector:
over-firing per essay scales with how many keepable names the essays contain, and
latency p95 scales with how long they are. A corpus swapped in under the previous
corpus's bars is a gate that reports on one thing and is judged against another.

So a corpus is now declared, not assumed. `conformance/corpora/<id>/profile.json`
says where the text comes from, how to parse it, which essays are in and in what
order; `conformance/carrier.json` and `conformance/measured.json` are keyed by the
same ids, so a corpus cannot report under another corpus's baseline. Adding one is
a profile plus a regenerated plan, in one place, for all three ports.

**Resolution order, and why the default is not the historical one.** An explicit
:data:`CORPUS_ENV_VAR` wins. Failing that, an operator who has configured an
ASAP-AES TSV keeps measuring ASAP-AES — the machine that has always measured it
does not change its answer because this module landed. Only failing both does the
shipped corpus apply. The effect is that Blake's box measures what it always
measured, and every other checkout, CI included, measures the three corpus gates
for the first time.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from vicary import config
from vicary.eval import conformance as conf

#: Directory under `conformance/` holding one subdirectory per corpus.
CORPORA_DIRNAME = "corpora"

#: The registry: which corpora exist, and which applies by default.
INDEX_FILENAME = "index.json"

#: Per-corpus files. `essays.json` is present only for a shipped corpus.
PROFILE_FILENAME = "profile.json"
ESSAYS_FILENAME = "essays.json"

#: Bumped when the meaning of a profile field changes, never when a value does.
PROFILE_DOCUMENT_VERSION = 1

#: Names a corpus id directly, overriding both the operator-TSV inference and the
#: shipped default. The one knob that makes "measure this other prose" a
#: first-class operation rather than a file swap.
CORPUS_ENV_VAR = "VICARY_EVAL_CORPUS"

#: Source kinds a profile may declare.
KIND_SHIPPED = "shipped"
KIND_OPERATOR_TSV = "operator_tsv"


def corpora_dir(directory: Path | None = None) -> Path:
    """Where the corpus profiles live, beside the rest of the spec."""
    if directory is None:
        found = conf.conformance_dir()
        if found is None:
            raise FileNotFoundError(
                "no conformance/ directory above this module — the corpus "
                "profiles live in the repository, not in an installed "
                "distribution"
            )
        directory = found
    return directory / CORPORA_DIRNAME


def _load_versioned(path: Path, what: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    version = document.get("document_version")
    if version != PROFILE_DOCUMENT_VERSION:
        raise ValueError(
            f"{path.name} is document_version {version!r} and this reader knows "
            f"{PROFILE_DOCUMENT_VERSION}. Refusing to read the fields it "
            f"recognises: a partly-read {what} selects a different slice of prose "
            "without being detectably wrong."
        )
    return document


def load_index(directory: Path | None = None) -> dict[str, Any]:
    """The corpus registry."""
    return _load_versioned(corpora_dir(directory) / INDEX_FILENAME, "registry")


def available(directory: Path | None = None) -> list[str]:
    """Corpus ids this checkout knows, in registry order."""
    return list(load_index(directory).get("corpora", []))


def load_profile(corpus_id: str, directory: Path | None = None) -> dict[str, Any]:
    """One corpus's profile. Raises when the id is not registered."""
    known = available(directory)
    if corpus_id not in known:
        raise KeyError(
            f"unknown corpus {corpus_id!r}; this checkout registers "
            f"{', '.join(known)}. Add a profile under "
            f"conformance/{CORPORA_DIRNAME}/ and list it in {INDEX_FILENAME}."
        )
    return _load_versioned(
        corpora_dir(directory) / corpus_id / PROFILE_FILENAME, "profile")


def _operator_tsv_configured() -> bool:
    """Whether an ASAP-AES-shaped TSV is configured, without raising.

    :func:`vicary.config.eval_corpus_tsv` raises when a configured directory is
    ambiguous, and that is the right behaviour when someone asked for the corpus.
    Here the question is only "did the operator configure one at all", and a
    resolution *error* still means yes — so it is reported as configured and the
    error surfaces later, at the load, where it names the actual problem.
    """
    try:
        return bool(config.eval_corpus_tsv())
    except Exception:
        return True


def unreadable_reason(corpus_id: str | None = None,
                      directory: Path | None = None) -> str | None:
    """Why the resolved corpus cannot be read here, or ``None`` if it can.

    There is exactly one legitimate reason: the corpus that resolves here is
    operator-supplied and no TSV is configured. A *shipped* corpus needs no
    operator setup at all, which is the whole point of shipping one.

    This exists because the caller that needs the distinction is a test, and the
    guard it used to carry — "is ``VICARY_EVAL_CORPUS_TSV`` set" — asks about the
    operator rather than about the corpus. Once ``persuade-20`` became the
    default, that guard reported NEEDS corpus against twenty essays sitting in
    the repository, and it did so in the reference port while TypeScript and Ruby
    measured them. See :func:`vicary.eval.corpus.load_essays`, which is what the
    other two ports' ``measure_from_config`` consults for the same question.
    """
    if corpus_id is None:
        corpus_id = resolve_corpus_id(directory)
    profile = load_profile(corpus_id, directory)
    if profile["source"]["kind"] == KIND_OPERATOR_TSV and not _operator_tsv_configured():
        return (
            f"the resolved corpus {corpus_id!r} is operator-supplied and no "
            f"{config.EVAL_CORPUS_TSV_ENV_VAR} is set; see "
            f"vicary/eval/corpus.py"
        )
    return None


def resolve_corpus_id(directory: Path | None = None) -> str:
    """Which corpus applies here. See the module docstring for the order."""
    index = load_index(directory)
    explicit = (config.get(CORPUS_ENV_VAR) or "").strip()
    if explicit:
        known = list(index.get("corpora", []))
        if explicit not in known:
            raise KeyError(
                f"{CORPUS_ENV_VAR}={explicit!r} is not a registered corpus; "
                f"this checkout registers {', '.join(known)}"
            )
        return explicit
    if _operator_tsv_configured():
        operator_default = index.get("operator_default")
        if operator_default:
            return str(operator_default)
    return str(index["default"])


def read_operator_tsv(path: str, source: dict[str, Any], limit: int,
                      wanted_ids: set[str] | None = None) -> list[tuple[str, str]]:
    """Read essays from an operator-supplied delimited file, per the profile.

    Everything that used to be a constant — encoding, delimiter, which column
    holds the id and which the text, and the row filter that picks one essay set
    out of several — comes off ``source`` here.
    """
    out: list[tuple[str, str]] = []
    id_column = source["id_column"]
    text_column = source["text_column"]
    row_filter = source.get("filter") or {}
    filter_column = row_filter.get("column")
    filter_value = row_filter.get("equals")
    with open(path, encoding=source.get("encoding", "utf-8"), newline="") as fh:
        for row in csv.DictReader(fh, delimiter=source.get("delimiter", "\t")):
            if filter_column is not None and row.get(filter_column) != filter_value:
                continue
            essay_id = row.get(id_column, "")
            if wanted_ids is not None and essay_id not in wanted_ids:
                continue
            out.append((essay_id, row.get(text_column, "")))
            if len(out) >= limit:
                break
    return out


def _read_shipped(corpus_id: str, profile: dict[str, Any],
                  directory: Path | None = None) -> list[tuple[str, str]]:
    source = profile["source"]
    path = corpora_dir(directory) / corpus_id / source.get(
        "text_file", ESSAYS_FILENAME)
    document = _load_versioned(path, "corpus")
    essays = [(entry["id"], entry["text"]) for entry in document["essays"]]

    # The essays ARE the baseline: every corpus-gate number describes this exact
    # text, so a corrupted or edited file must fail here rather than quietly
    # rebase what the gates mean. The carrier plan checks the same bytes again
    # from its own digests, which is deliberate — two independent records of what
    # this corpus is, and either one catches an edit to the other.
    expected = {entry["id"]: entry["sha256"] for entry in profile.get("essays", [])}
    for essay_id, text in essays:
        want = expected.get(essay_id)
        if want is None:
            raise ValueError(
                f"{corpus_id}: {path.name} carries essay {essay_id!r}, which "
                f"{PROFILE_FILENAME} does not list"
            )
        got = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if got != want:
            raise ValueError(
                f"{corpus_id}: essay {essay_id!r} in {path.name} is sha256 "
                f"{got}, and {PROFILE_FILENAME} pins {want}. Refusing: the "
                "essays are the baseline, so different text means every gate "
                "number measured on this corpus describes different prose."
            )
    if len(expected) != len(essays):
        raise ValueError(
            f"{corpus_id}: {PROFILE_FILENAME} lists {len(expected)} essays and "
            f"{path.name} holds {len(essays)}"
        )
    return essays


def load_essays(corpus_id: str | None = None,
                directory: Path | None = None) -> tuple[str, list[tuple[str, str]]]:
    """The corpus's essays, in plan order, with the id of the corpus loaded.

    Returns the id alongside the essays because every caller needs to record
    *which* corpus it measured — a number filed under the wrong corpus is the
    failure this module exists to prevent, and returning them together means a
    caller cannot forget to ask.
    """
    if corpus_id is None:
        corpus_id = resolve_corpus_id(directory)
    profile = load_profile(corpus_id, directory)
    limit = int(profile["selection"]["limit"])
    kind = profile["source"]["kind"]

    if kind == KIND_SHIPPED:
        essays = _read_shipped(corpus_id, profile, directory)
    elif kind == KIND_OPERATOR_TSV:
        tsv = config.eval_corpus_tsv()
        if not tsv:
            raise FileNotFoundError(
                f"corpus {corpus_id!r} is operator-supplied: set "
                f"{config.EVAL_CORPUS_TSV_ENV_VAR} or "
                f"{config.EVAL_CORPUS_DIR_ENV_VAR}, or select a shipped corpus "
                f"with {CORPUS_ENV_VAR}"
            )
        essays = read_operator_tsv(tsv, profile["source"], limit)
    else:
        raise ValueError(
            f"corpus {corpus_id!r} declares source kind {kind!r}; this reader "
            f"knows {KIND_SHIPPED!r} and {KIND_OPERATOR_TSV!r}"
        )

    if len(essays) != limit:
        raise ValueError(
            f"corpus {corpus_id!r} yielded {len(essays)} essays and its profile "
            f"plans {limit}. Refusing a partial corpus: a short read lowers "
            "over-firing and latency, and both are `<=` gates, so it is the most "
            "comfortable pass on the board."
        )
    return corpus_id, essays
