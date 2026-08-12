"""The carrier plan: where each fixture frame is injected into each essay.

Three of the nine gates — held-out recall in a carrier essay, over-firing on real
prose, and latency at essay length — cannot be measured on isolated sentences.
They need frames planted inside genuine student prose.

Everything about building those carrier essays is already deterministic and
language-neutral **except one step**: which sentence ends the frames land on.
:func:`vicary.eval.recall.build_cases` draws those offsets from Python's Mersenne
Twister, and reproducing that draw in JavaScript and Ruby would mean porting
MT19937 and ``random.sample`` into both — several hundred lines with nothing to
do with redaction, and a silent failure mode: a subtly different draw yields
different carrier text, so the ports disagree on the gates while every one of
them looks healthy.

This module records the draw instead. ``conformance/carrier.json`` carries, per
essay: its id, a digest of its text, the frames injected, and the offsets they
were injected at. Every port then builds byte-identical carrier essays from its
own copy of the corpus, with no RNG anywhere.

**The plan is an input, not an answer.** It says where to inject, exactly as
``frames.json`` says what to inject. What each port then measures — how much it
recalled, how much it over-fired, how long it took — is recovered from that
port's own output. This is the same line the spec already draws by carrying
``sentence`` while refusing to carry ``aligns``.

**One plan per corpus, keyed by corpus id.** The file held a single plan for as
long as there was a single corpus, and the corpus was ASAP-AES — which no package
may ship, so the three gates above were unmeasurable on any machine without the
operator's own copy. Now that a corpus ships (see
:mod:`vicary.eval.corpus`), the offsets are per-corpus by construction: they are
character positions into specific essays, and there is no such thing as an offset
that means anything in two different corpora. Keying them by id is what stops a
plan built for one corpus from being read against another, where every digest
check would fail at once and the reason would look like a corrupted file.

**No essay text is recorded here, and none may be.** The plan holds ids, digests,
offsets and counts. That is what lets this file live in the repository whether or
not the corpus it describes does.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from vicary.eval import conformance as conf
from vicary.eval import corpus as corpus_mod
from vicary.eval import recall

#: The file, beside the rest of the spec.
CARRIER_FILENAME = "carrier.json"

#: Bumped when the meaning of a field changes, never when a value does. A reader
#: that does not recognise the number must refuse the file rather than skip what
#: it cannot parse — a partly-read plan builds partly-wrong carrier text.
#:
#: 2 keyed the plans by corpus id. A version-1 reader handed this file would find
#: no ``cases`` at the top level and build zero carrier essays, which is the
#: comfortable-pass failure this repository has already been bitten by once, so
#: the bump is what makes an old port fail loudly instead.
DOCUMENT_VERSION = 2

#: The seed the recorded draw came from. Kept so the draw is reproducible, not so
#: it is re-drawn: a regenerated plan with a different seed is a different plan.
GENERATED_WITH_SEED = 20260805


def build_plan(corpus_id: str, essays: list[tuple[str, str]] | None = None,
               directory: Path | None = None) -> dict[str, Any]:
    """Generate one corpus's plan, via the RNG path, once."""
    profile = corpus_mod.load_profile(corpus_id, directory)
    if essays is None:
        loaded_id, essays = corpus_mod.load_essays(corpus_id, directory)
        assert loaded_id == corpus_id

    # Seed passed explicitly rather than left to the default, so the number
    # recorded in the plan cannot drift from the one the draw actually used.
    cases = recall.build_cases(
        essays, seed=GENERATED_WITH_SEED,
        pool=conf.frames_from_document(conf.load_frames_document()))

    # An essay that offers too few places to cut in is named here rather than
    # quietly missing from `cases`. A plan short of its corpus measures fewer
    # essays than the profile claims, which lowers the two `<=` gates and reads
    # as a comfortable pass — so the count still has to reconcile exactly, and
    # what makes that possible is writing down which essays went where.
    unusable = [
        {"essay_id": essay_id, "reason": reason}
        for essay_id, base in essays
        if (reason := recall.unusable_for_injection(base)) is not None
    ]
    if len(cases) + len(unusable) != len(essays):
        raise ValueError(
            f"corpus {corpus_id!r}: {len(essays)} essays yielded {len(cases)} "
            f"cases and {len(unusable)} declared as unusable, which do not add "
            "up. Every essay is either carried or named, and an essay that is "
            "neither is one the plan silently dropped."
        )
    return {
        "corpus": {
            "id": corpus_id,
            "name": profile["name"],
            "selection": profile["selection"]["rule"],
            "limit": profile["selection"]["limit"],
        },
        "per_essay": recall.DEFAULT_PER_ESSAY,
        "generated_with_seed": GENERATED_WITH_SEED,
        "unusable": unusable,
        "cases": [
            {
                "essay_id": case.essay_id,
                "base_sha256": hashlib.sha256(
                    case.base.encode("utf-8")).hexdigest(),
                "base_chars": len(case.base),
                "frames": [frame.frame_id for frame in case.frames],
                "slots": list(case.slots),
            }
            for case in cases
        ],
    }


def carrier_path(directory: Path | None = None) -> Path:
    """Where the plan lives, beside ``frames.json``."""
    if directory is None:
        found = conf.conformance_dir()
        if found is None:
            raise FileNotFoundError(
                "no conformance/ directory above this module — the spec lives "
                "in the repository, not in an installed distribution"
            )
        directory = found
    return directory / CARRIER_FILENAME


def load_document(path: Path | None = None) -> dict[str, Any]:
    """Read the file. Raises when it is absent or of an unknown version."""
    document = json.loads(
        (path or carrier_path()).read_text(encoding="utf-8"))
    version = document.get("document_version")
    if version != DOCUMENT_VERSION:
        raise ValueError(
            f"{CARRIER_FILENAME} is document_version {version!r}, and this "
            f"reader knows {DOCUMENT_VERSION}. Refusing rather than reading the "
            "fields it recognises, because a partly-read plan produces carrier "
            "text that is wrong without being detectably wrong."
        )
    return document


def load_plan(corpus_id: str, path: Path | None = None) -> dict[str, Any]:
    """One corpus's plan.

    A missing plan is an error naming the corpus, not a ``KeyError`` on
    ``cases`` several frames later — the id is the thing the caller got wrong.
    """
    plans = load_document(path).get("plans", {})
    if corpus_id not in plans:
        raise KeyError(
            f"{CARRIER_FILENAME} holds no plan for corpus {corpus_id!r}; it has "
            f"{', '.join(sorted(plans)) or 'none'}. Regenerate with "
            f"`python -m vicary.eval.carrier --write` on a machine that can read "
            f"that corpus."
        )
    return plans[corpus_id]


def build_document(directory: Path | None = None,
                   existing: dict[str, Any] | None = None,
                   corpus_ids: list[str] | None = None) -> dict[str, Any]:
    """Regenerate the plans this machine can reach, keeping the ones it cannot.

    A machine without the operator's ASAP-AES copy can still regenerate the
    shipped corpus's plan, and **must not drop the plan it cannot rebuild** —
    that would delete the reference baseline from the repository as a side effect
    of running the generator somewhere ordinary. So plans merge, and every corpus
    that is skipped says so on stdout rather than vanishing quietly.
    """
    plans: dict[str, Any] = dict((existing or {}).get("plans", {}))
    skipped: dict[str, str] = {}
    for corpus_id in (corpus_ids or corpus_mod.available(directory)):
        try:
            plans[corpus_id] = build_plan(corpus_id, directory=directory)
        except (FileNotFoundError, ValueError) as exc:
            skipped[corpus_id] = str(exc)
    return {
        "document_version": DOCUMENT_VERSION,
        "note": (
            "Offsets into each essay where the named frames were injected, in "
            "the order applied — descending, so an earlier insertion cannot "
            "shift a later one. frames[i] goes at slots[i]. An input, not an "
            "answer: what each port measures from the resulting text is its own. "
            "Keyed by corpus id, because an offset means nothing in another "
            "corpus's essays."
        ),
        "plans": plans,
        "_skipped": skipped,
    }


def write(directory: Path | None = None,
          corpus_ids: list[str] | None = None) -> Path:
    """Regenerate in place, merging over whatever is already recorded."""
    path = carrier_path(directory)
    existing: dict[str, Any] | None = None
    if path.exists():
        try:
            existing = load_document(path)
        except ValueError:
            # An older version is exactly what a regeneration is for; the plans
            # it holds are not readable as v2 plans, so they are not carried
            # over. Loud, because the caller is about to lose them.
            print(f"note: {path.name} was an older document_version; its plans "
                  "are not carried forward")
    document = build_document(directory, existing=existing, corpus_ids=corpus_ids)
    skipped = document.pop("_skipped", {})
    for corpus_id, reason in skipped.items():
        print(f"SKIPPED {corpus_id} — {reason.splitlines()[0]}")
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m vicary.eval.carrier",
        description="Record where each fixture frame is injected into each "
                    "corpus essay, so every port builds the same carrier text.",
    )
    parser.add_argument("--write", action="store_true",
                        help=f"write conformance/{CARRIER_FILENAME} in place "
                             "(default: print it to stdout)")
    parser.add_argument("--corpus", action="append", default=None,
                        help="regenerate only this corpus id (repeatable); "
                             "default is every registered corpus this machine "
                             "can read")
    args = parser.parse_args(argv)

    if args.write:
        print(f"wrote {write(corpus_ids=args.corpus)}")
    else:
        document = build_document(corpus_ids=args.corpus)
        skipped = document.pop("_skipped", {})
        for corpus_id, reason in skipped.items():
            print(f"SKIPPED {corpus_id} — {reason.splitlines()[0]}")
        print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
