"""What the reference measured on the corpus, as a file the other ports read.

``carrier.json`` says *where* to inject and is deliberately an input, not an
answer. This file is the answer: the numbers the Python reference gets when it
redacts the carrier text that plan produces. It exists because those numbers were
previously typed as literals into three test suites at once —
``assert.equal(corpus.recallHeldOutPassed, 29)`` in TypeScript,
``assert_equal 29, m.recall_held_out_passed`` in Ruby, and the corresponding
constants in Python.

**Why that was worse than it looked.** Three copies of a number is not three
checks of it. If the reference's figure legitimately moves — a fixture revision
adds a held-out span, a tier change closes an over-fire — Python's suite is
updated because that is where the change was made, and TypeScript and Ruby keep
asserting the stale value. Both stay green. They are now measuring a different
thing from the reference and reporting agreement, which is the exact failure the
conformance suite exists to prevent, arriving through the tests rather than
through the detector.

So the reference emits its measurements once and every port asserts against the
file. A port that diverges fails; a reference value that moves fails all three
until the file is regenerated, and regenerating it puts the change in a reviewable
diff with the envelope attached.

**The envelope travels with the numbers.** A figure measured on a different
corpus slice, a different fixture, or a different arm is a different figure, and
one that arrives without saying so is worse than no figure. ``envelope`` records
all three, and :func:`load_document` refuses a file whose envelope does not match
the caller's — a port measuring fixture 2026-08-11.2 against numbers taken at
2026-08-05.6 should stop, not compare.

**Latency is deliberately absent.** It is the one corpus gate whose answer says
nothing about the port's correctness: it measures the machine. Pinning it here
would make an ordinary CI box fail a parity suite for being busy. Each port
asserts latency against the published bar alone, and the omission is recorded in
``not_recorded`` rather than left as a gap a reader has to notice.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from vicary import config
from vicary.eval import carrier
from vicary.eval import conformance as conf

#: The file, beside the rest of the spec.
MEASURED_FILENAME = "measured.json"

#: Bumped when the meaning of a field changes, never when a value does.
DOCUMENT_VERSION = 1

#: The arm every number here was measured on. Named in the envelope because the
#: same detector measured without the gazetteer answers 0% held-out recall, and a
#: port comparing against the wrong arm would read that as a catastrophic
#: regression rather than as a different question.
REFERENCE_ARM = "local-gazetteer-lowercase"

#: What is measured elsewhere, and why it is not here. Emitted as data so a reader
#: of the file sees the omission without knowing to look for it.
NOT_RECORDED = {
    "latency_p50_ms": "Measures the machine, not the port. Each port asserts "
                      "its own against the published bar.",
    "latency_p95_ms": "Same. Pinning it would fail a correctness suite on a "
                      "busy CI box.",
}


def measure() -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Measure the reference: ``(carrier digest, corpus gates, envelope)``.

    Split out from :func:`build_document` so the Python gate suite can call the
    *same* code that wrote the file and compare. Without that, the reference is
    the one port never checked against the numbers it publishes: Python's
    measurement could drift, ``measured.json`` would go stale, and the first
    thing to notice would be TypeScript and Ruby failing against a file that
    describes nothing — which names the wrong two ports.
    """
    # Imported here rather than at module scope: `recall` pulls in the classifier
    # and the 2.1 MB gazetteer, and nothing that merely *reads* this file should
    # pay that.
    from vicary.eval.fixture import FIXTURE_VERSION
    from vicary.eval.fixture import frames as select_frames
    from vicary.eval.recall import build_cases_from_plan, load_set8, run

    tsv = config.eval_corpus_tsv()
    if not tsv:
        raise FileNotFoundError(
            f"no corpus: set {config.EVAL_CORPUS_TSV_ENV_VAR} or "
            f"{config.EVAL_CORPUS_DIR_ENV_VAR} to measure the reference"
        )

    plan = carrier.load_document()
    essays = load_set8(tsv, None, plan["corpus"]["limit"])
    cases = build_cases_from_plan(essays, plan, pool=select_frames())

    # The load-bearing parity anchor. Every number below is measured on this
    # text, so a port whose carrier text differs is answering a different
    # question and agreeing on the metrics would prove nothing. Anchored on a
    # digest rather than on the metrics because the metrics can coincide across
    # genuinely different inputs.
    digest = hashlib.sha256(
        "".join(case.text for case in cases).encode("utf-8")).hexdigest()

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        records = run(cases, REFERENCE_ARM,
                      str(Path(directory) / "recall.jsonl"), guardrail_id=None)

    mode = f"{REFERENCE_ARM}:INPUT:{FIXTURE_VERSION}"
    rows = [r for r in records if r["mode"] == mode]
    if not rows:
        raise RuntimeError(
            f"the reference arm {mode} produced no records, so there is nothing "
            "to publish — refusing to write a document of zeroes, which would "
            "pass every port's comparison against it"
        )

    # Counts, not just percentages: 100% of a wrong denominator is still 100%,
    # and the denominator is what moves when a fixture revision adds a span.
    held_out = [
        span for row in rows for span in row["spans"]
        if span["held_out"] and span["verdict"] == "redact"
    ]
    passed = sum(1 for span in held_out if span["passed"])
    over_fire_total = sum(row["base_fp_spans"] for row in rows)
    asap_rewrites = sum(row.get("base_asap_rewrites", 0) for row in rows)

    envelope = {
        "reference": "python",
        "arm": REFERENCE_ARM,
        "fixture_version": FIXTURE_VERSION,
        "corpus": plan["corpus"],
        "carrier_document_version": plan["document_version"],
    }
    gates = {
        "essays": len(rows),
        "recall_held_out_passed": passed,
        "recall_held_out_total": len(held_out),
        "recall_held_out_pct": (
            100.0 * passed / len(held_out) if held_out else 0.0),
        "over_fire_spans_total": over_fire_total,
        "over_fire_spans_per_essay": over_fire_total / len(rows),
        "asap_rewrites_per_essay": asap_rewrites / len(rows),
    }
    return digest, gates, envelope


def build_document() -> dict[str, Any]:
    """Measure the corpus gates on the reference and return the document.

    Needs the corpus: this is a measurement, and there is nothing to measure
    without one.
    """
    digest, gates, envelope = measure()
    return {
        "document_version": DOCUMENT_VERSION,
        "envelope": envelope,
        "carrier_text_sha256": digest,
        "corpus_gates": gates,
        "not_recorded": NOT_RECORDED,
    }


def measured_path(directory: Path | None = None) -> Path:
    """Where the measurements live, beside ``frames.json``."""
    if directory is None:
        found = conf.conformance_dir()
        if found is None:
            raise FileNotFoundError(
                "no conformance/ directory above this module — the spec lives "
                "in the repository, not in an installed distribution"
            )
        directory = found
    return directory / MEASURED_FILENAME


def load_document(path: Path | None = None) -> dict[str, Any]:
    """Read the measurements. Raises when absent or of an unknown version."""
    document = json.loads((path or measured_path()).read_text(encoding="utf-8"))
    version = document.get("document_version")
    if version != DOCUMENT_VERSION:
        raise ValueError(
            f"{MEASURED_FILENAME} is document_version {version!r}, and this "
            f"reader knows {DOCUMENT_VERSION}. Refusing rather than reading the "
            "fields it recognises: a partly-read document compares this port "
            "against numbers whose meaning it is guessing at."
        )
    return document


def check_envelope(document: dict[str, Any], *, fixture_version: str) -> None:
    """Refuse measurements taken in a different envelope from the caller's.

    The one check that cannot be left to the comparison itself. A port scored
    against fixture 2026-08-11.2 comparing its counts to numbers taken at
    2026-08-05.6 does not get a clean failure — it gets an off-by-a-few that
    reads like a detector regression and costs a bisect to attribute.
    """
    recorded = document["envelope"]["fixture_version"]
    if recorded != fixture_version:
        raise ValueError(
            f"{MEASURED_FILENAME} was measured at fixture {recorded} and this "
            f"port is scoring against {fixture_version}. Regenerate it with "
            "`just sync-conformance` (needs the corpus) rather than comparing "
            "across fixtures."
        )


def write(directory: Path | None = None) -> Path:
    """Regenerate the measurements in place. Needs the corpus."""
    path = measured_path(directory)
    path.write_text(
        json.dumps(build_document(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m vicary.eval.measured",
        description="Publish what the reference measures on the corpus, so the "
                    "other two ports assert against a file instead of against "
                    "a number typed into three test suites.",
    )
    parser.add_argument("--write", action="store_true",
                        help=f"write conformance/{MEASURED_FILENAME} in place "
                             "(default: print it to stdout)")
    args = parser.parse_args(argv)

    if args.write:
        print(f"wrote {write()}")
    else:
        print(json.dumps(build_document(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
