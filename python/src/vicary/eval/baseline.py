"""Is this build slower than the last release, and is that a fair question here?

The gate has asked this three ways. The first two are worth keeping in view,
because each looked correct until it decided a release.

**An absolute bar — 10 ms.** That is a claim about the machine as much as about
the code. It passed on a laptop and failed on the CI runner enforcing it, so
v0.2.3 published to PyPI and npm and was refused by RubyGems on the same commit.

**A stored baseline** — record each release's number, compare the next run
against it, and refuse to compare unless the run claims the profile the baseline
was recorded on. Better, and still wrong, for a reason no estimator can fix: the
profile `github-ubuntu-latest` is not a machine. Thirty-six processes across six
runners per port, on identical code, spread 67% in Ruby (6.53 ms on an Intel Xeon
6973P-C against 10.63 ms on an EPYC 7763), 26% in Python and 21% here — against
an 8% bar. One probe run drew five different CPU models from that one label, and
two runners of the *same* model still differed by 26%. So the stored baseline
red-lit `main` on unchanged code at +8.33%, which is the same failure as the
absolute bar wearing a relative costume.

**A pair, measured here.** The previous release's code and this checkout are
measured on the SAME machine, interleaved and counterbalanced, by
``tools/latency_pair.py``. Every property of the machine is common to both sides
and cancels; what is left is within-process noise — 0.7% in Python, 1.7% in Ruby,
3.3% in TypeScript — and the median over several rounds is tighter still.

Which leaves this module one job, the same one it has always had: **refusing to
compare when the two sides would not be like for like.** A machine difference
reported as a code regression is worse than no gate, because it trains the reader
to ignore it. What changed is that the refusals are now about the pair record —
is there one, is it this port's, was it measured on these essays, was it measured
for this commit — rather than about the profile of a machine somewhere else.

The reference implementation of the comparison. TypeScript and Ruby each read the
same record and reach their own verdict; none of them reads Python's.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import conformance as conf

#: The tolerance and the protocol, in the repository. Not a measurement: nothing
#: is recorded at release time any more, because the comparison point is the
#: previous release's *code*, which the repository already has.
SPEC_FILENAME = "latency_baseline.json"

#: Where ``tools/latency_pair.py`` left the paired measurement. Set by CI in the
#: same job, seconds before the gate runs. Absent on a laptop unless the harness
#: was run there by hand, and that absence is a refusal to compare rather than a
#: pass — measuring one side of a comparison is not a gate.
PAIR_ENV_VAR = "VICARY_LATENCY_PAIR"

#: What this reader understands. A pair record from a future shape is refused
#: rather than half-read: a partly-understood record still yields a number, and
#: a number is exactly what must not be invented here.
PAIR_DOCUMENT_VERSION = 1

IMPLEMENTATION = "python"

#: The bar, and it is a chosen one rather than a derived one — 8% is what a
#: reviewer is willing to call a regression, not what the noise dictates. What
#: the noise decides is whether the bar is *usable*, and under the pair it is:
#: the gate statistic measures sigma 1.71% in the noisiest port (twelve runs,
#: six CI runners, fixed head and tag), so 8% is 4.7 sigma out. It was about one
#: third of a sigma under the stored baseline, which is why that one red-lit
#: `main` on unchanged code. See the docstring on ``tools/latency_pair.py``.
#:
#: What this bar does NOT catch is drift: +5% per release passes every time and
#: compounds to +34% over six releases with nothing ever red. That is a property
#: of comparing against the last release, it is deliberate, and noticing it is a
#: human's job — this gate is for the step change, not the trend.
DEFAULT_TOLERANCE_PCT = 8.0


@dataclass(frozen=True)
class Comparison:
    """The gate's answer, and — when it declines — why."""

    measured_ms: float
    previous_ms: float | None
    current_ms: float | None
    regression_pct: float | None
    tolerance_pct: float
    against: str | None
    comparable: bool
    reason: str | None

    @property
    def holds(self) -> bool:
        if not self.comparable or self.regression_pct is None:
            return False
        return self.regression_pct <= self.tolerance_pct


def spec_path(directory: Path | None = None) -> Path | None:
    root = directory or conf.conformance_dir()
    if root is None:
        return None
    path = Path(root) / SPEC_FILENAME
    return path if path.exists() else None


def load(directory: Path | None = None) -> dict[str, Any] | None:
    path = spec_path(directory)
    if path is None:
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_pair(path: str | Path | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """The paired measurement, or why there is none to read.

    Returns ``(record, reason)`` with exactly one of them set, so an unreadable
    file and an absent one stay distinguishable — the first is a broken harness
    and the second is an ordinary laptop, and they should not report the same
    thing.
    """
    given = str(path) if path is not None else (os.environ.get(PAIR_ENV_VAR) or "").strip()
    if not given:
        return None, (
            f"{PAIR_ENV_VAR} is unset, so no paired measurement was taken on "
            f"this machine; the gate compares this build against the last "
            f"release measured HERE, and one side of a comparison is not a gate"
        )
    if not os.path.exists(given):
        return None, f"{PAIR_ENV_VAR}={given!r} does not exist"
    try:
        with open(given, encoding="utf-8") as fh:
            return json.load(fh), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"the pair record at {given} could not be read: {exc}"


def compare(measured_ms: float, corpus_id: str,
            directory: Path | None = None,
            implementation: str = IMPLEMENTATION,
            pair_path: str | Path | None = None) -> Comparison:
    """Compare the pair measured on this machine, for this port.

    ``measured_ms`` is this process's own figure. It is reported either way and
    it is never the verdict: the verdict comes from the two numbers in the pair
    record, which were taken back to back on one machine. Mixing this process's
    measurement with the pair's other side would reintroduce exactly the machine
    difference the pair exists to cancel.
    """
    doc = load(directory) or {}
    tolerance = float(doc.get("tolerance_pct", DEFAULT_TOLERANCE_PCT))

    def declined(reason: str) -> Comparison:
        return Comparison(measured_ms=measured_ms, previous_ms=None,
                          current_ms=None, regression_pct=None,
                          tolerance_pct=tolerance, against=None,
                          comparable=False, reason=reason)

    record, why = load_pair(pair_path)
    if record is None:
        return declined(why or "no paired measurement")

    version = record.get("document_version")
    if version != PAIR_DOCUMENT_VERSION:
        return declined(
            f"the pair record is document_version {version} and this reader "
            f"knows {PAIR_DOCUMENT_VERSION}"
        )

    measured_impl = record.get("implementation")
    if measured_impl != implementation:
        return declined(
            f"the pair record measures {measured_impl!r}, not {implementation!r}"
        )

    pair_corpus = record.get("corpus")
    if pair_corpus != corpus_id:
        return declined(
            f"the pair was measured on corpus {pair_corpus!r} and this run is "
            f"{corpus_id!r}; latency scales with essay length"
        )

    # Only where there is something to check against. `GITHUB_SHA` names the
    # commit the job is building, so a record left over from an earlier commit
    # is caught here rather than being read as this build's verdict. Locally
    # there is no such witness and no such risk: the harness is run by hand,
    # minutes before, on the tree in front of you.
    building = (os.environ.get("GITHUB_SHA") or "").strip()
    head = str(record.get("head_sha") or "")
    if building and head and building != head:
        return declined(
            f"the pair was measured for commit {head[:12]} and this job is "
            f"building {building[:12]}; the record is stale"
        )

    previous = record.get("previous_ms")
    current = record.get("current_ms")
    if not isinstance(previous, (int, float)) or not isinstance(current, (int, float)):
        return declined("the pair record carries no pair of measurements")
    if previous <= 0:
        return declined(f"the previous release measured {previous} ms, which is not positive")

    against = (record.get("against") or {}).get("ref")
    return Comparison(
        measured_ms=measured_ms,
        previous_ms=float(previous),
        current_ms=float(current),
        regression_pct=(float(current) / float(previous) - 1.0) * 100.0,
        tolerance_pct=tolerance,
        against=str(against) if against else None,
        comparable=True,
        reason=None,
    )


def render(c: Comparison) -> str:
    if not c.comparable:
        return (f"latency {c.measured_ms:.3f} ms — NOT COMPARED against the "
                f"last release: {c.reason}")
    sign = "+" if (c.regression_pct or 0.0) >= 0 else ""
    return (f"latency {c.measured_ms:.3f} ms here; paired on this machine, "
            f"{c.current_ms:.3f} ms against {c.against or 'the last release'}'s "
            f"{c.previous_ms:.3f} ms — {sign}{c.regression_pct:.2f}% against a "
            f"{c.tolerance_pct:.0f}% bar")
