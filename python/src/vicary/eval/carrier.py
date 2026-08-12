"""The carrier plan: where each fixture frame is injected into each essay.

Three of the nine gates — held-out recall in a carrier essay, over-firing on real
prose, and latency at essay length — cannot be measured on isolated sentences.
They need frames planted inside genuine student prose, and the prose is an essay
corpus no package here ships.

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
own local copy of the corpus, with no RNG anywhere.

**The plan is an input, not an answer.** It says where to inject, exactly as
``frames.json`` says what to inject. What each port then measures — how much it
recalled, how much it over-fired, how long it took — is recovered from that
port's own output. This is the same line the spec already draws by carrying
``sentence`` while refusing to carry ``aligns``.

**No essay text is recorded here, and none may be.** The plan holds ids, digests,
offsets and counts. The corpus itself stays where the operator put it, which is
what lets this file live in the repository while ASAP-AES does not.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from vicary import config
from vicary.eval import conformance as conf
from vicary.eval import recall

#: The file, beside the rest of the spec.
CARRIER_FILENAME = "carrier.json"

#: Bumped when the meaning of a field changes, never when a value does. A reader
#: that does not recognise the number must refuse the file rather than skip what
#: it cannot parse — a partly-read plan builds partly-wrong carrier text.
DOCUMENT_VERSION = 1

#: Which essays, in which order. Recorded so a port cannot quietly measure a
#: different slice of the corpus and report it under the same gate.
CORPUS_ESSAY_SET = "8"
CORPUS_LIMIT = 25


def build_document(essays: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Generate the plan from the corpus, via the RNG path, once."""
    if essays is None:
        tsv = config.eval_corpus_tsv()
        if not tsv:
            raise FileNotFoundError(
                f"no corpus: set {config.EVAL_CORPUS_TSV_ENV_VAR} or "
                f"{config.EVAL_CORPUS_DIR_ENV_VAR} to generate a carrier plan"
            )
        essays = recall.load_set8(tsv, None, CORPUS_LIMIT)

    cases = recall.build_cases(essays, pool=conf.frames_from_document(
        conf.load_frames_document()))
    return {
        "document_version": DOCUMENT_VERSION,
        "corpus": {
            "name": "ASAP-AES",
            "essay_set": CORPUS_ESSAY_SET,
            "selection": "first N essays of the set, in file order",
            "limit": CORPUS_LIMIT,
        },
        "per_essay": recall.DEFAULT_PER_ESSAY,
        "generated_with_seed": 20260805,
        "note": (
            "Offsets into each essay where the named frames were injected, in "
            "the order applied — descending, so an earlier insertion cannot "
            "shift a later one. frames[i] goes at slots[i]. An input, not an "
            "answer: what each port measures from the resulting text is its own."
        ),
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
    """Read the plan. Raises when it is absent or of an unknown version."""
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


def write(directory: Path | None = None) -> Path:
    """Regenerate the plan in place. Needs the corpus."""
    path = carrier_path(directory)
    document = build_document()
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
    args = parser.parse_args(argv)

    if args.write:
        print(f"wrote {write()}")
    else:
        print(json.dumps(build_document(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
