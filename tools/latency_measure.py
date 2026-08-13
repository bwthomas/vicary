"""Measure this checkout's redaction latency once, and print it as JSON.

One process, one number, no verdict. The verdict is `latency_pair.py`'s job,
because a latency number only means something next to another one taken on the
same machine — see that file's header for what the measurements here are for.

Run from `python/` so `src` is importable, or point ``PYTHONPATH`` at the `src`
of some *other* checkout to measure that one with this script. The second form
is how the pair driver measures the previous release: same script, same corpus,
same estimator, different library.

    python ../tools/latency_measure.py
    PYTHONPATH=/tmp/prev/python/src python ../tools/latency_measure.py
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.environ.get("VICARY_MEASURE_SRC", "src"))

from vicary.eval import carrier  # noqa: E402
from vicary.eval import corpus as corpus_mod  # noqa: E402
from vicary.eval.recall import (  # noqa: E402
    LATENCY_REPEATS,
    build_cases_from_plan,
    build_redactor,
    select_frames,
)

#: The arm the gate measures: the shipped gazetteer, lowercase-tolerant.
GATE_ARM = "local-gazetteer-lowercase"
SOURCE = "INPUT"


def sweep(redactor, cases, timed: bool) -> list[list[float]]:
    """One pass over every essay, timed or not.

    The untimed pass is the warmup, and it is not a formality. It is measured:
    on a GitHub runner TypeScript's first four essays run at about twice their
    steady-state cost while V8 tiers the redaction path up, which put a quarter
    of the pooled samples above the steady state and made the estimator's value
    depend on when the JIT happened to finish. Python and Ruby barely move,
    which is the other half of the reason it is here — the three ports have to
    estimate the same way or the gate is three different gates.
    """
    out: list[list[float]] = []
    for case in cases:
        timings = []
        for _ in range(LATENCY_REPEATS):
            t0 = time.monotonic()
            redactor._apply(case.text, source=SOURCE)
            timings.append((time.monotonic() - t0) * 1000.0)
        out.append(timings)
        # The clean-prose pass the gate's own loop does between essays. Untimed
        # there and untimed here, but it runs, so the process is in the same
        # state from one timed essay to the next.
        redactor._apply(case.base, source=SOURCE)
    return out if timed else []


def main() -> int:
    corpus_id, essays = corpus_mod.load_essays()
    plan = carrier.load_plan(corpus_id)
    cases = build_cases_from_plan(essays, plan, pool=select_frames())
    if not cases:
        print(json.dumps({"error": "no corpus in this checkout"}))
        return 1

    redactor = build_redactor(GATE_ARM, None)
    # The asset load, before the clock: a one-time ~84 ms cost that whichever
    # essay came first would otherwise pay in full.
    redactor._apply(cases[0].base[:200], source=SOURCE)

    sweep(redactor, cases, timed=False)
    samples = sweep(redactor, cases, timed=True)
    pooled = [t for reps in samples for t in reps]

    digest = hashlib.sha256("".join(c.text for c in cases).encode("utf-8"))
    print(json.dumps({
        "impl": "python",
        "runtime": f"{sys.version_info.major}.{sys.version_info.minor}",
        "corpus": corpus_id,
        "corpus_sha256": digest.hexdigest(),
        "essays": len(cases),
        "repeats": LATENCY_REPEATS,
        "pooled_median_ms": round(statistics.median(pooled), 6),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
