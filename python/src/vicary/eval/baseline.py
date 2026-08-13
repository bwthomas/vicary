"""Is this build slower than the last release, and is that a fair question here?

The latency gate used to hold an absolute number — 10 ms — which is a claim
about the machine as much as about the code. It passed on a laptop and failed on
the CI runner enforcing it, so v0.2.3 published to PyPI and npm and was refused
by RubyGems on the same commit.

What replaced it asks a relative question: is this port slower than it was at the
last release, by more than the tolerance. That only means something between
measurements taken on comparable hardware, so this module's real work is
REFUSING to compare when they are not — a machine difference reported as a code
regression is worse than no gate, because it trains the reader to ignore it.

The reference implementation of the comparison. TypeScript and Ruby each read
the same file and reach their own verdict; none of them reads Python's.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import conformance as conf

BASELINE_FILENAME = "latency_baseline.json"

#: Set by CI on the one matrix entry whose language version matches the recorded
#: profile. Absent everywhere else on purpose: a developer's laptop measures the
#: same commit two to three times faster than the runner, and comparing that
#: against a runner baseline reports a large phantom improvement.
PROFILE_ENV_VAR = "VICARY_LATENCY_PROFILE"

IMPLEMENTATION = "python"


@dataclass(frozen=True)
class Comparison:
    """The gate's answer, and — when it declines — why."""

    measured_ms: float
    baseline_ms: float | None
    regression_pct: float | None
    tolerance_pct: float
    comparable: bool
    reason: str | None

    @property
    def holds(self) -> bool:
        if not self.comparable or self.regression_pct is None:
            return False
        return self.regression_pct <= self.tolerance_pct


def baseline_path(directory: Path | None = None) -> Path | None:
    root = directory or conf.conformance_dir()
    if root is None:
        return None
    path = Path(root) / BASELINE_FILENAME
    return path if path.exists() else None


def load(directory: Path | None = None) -> dict[str, Any] | None:
    path = baseline_path(directory)
    if path is None:
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def language_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def compare(measured_ms: float, corpus_id: str,
            directory: Path | None = None,
            implementation: str = IMPLEMENTATION,
            observed_language_version: str | None = None) -> Comparison:
    """Compare ``measured_ms`` against the recorded baseline for this port.

    Every ``reason`` below is a refusal to compare, not a failure to measure:
    the number was measured either way and is reported either way. What is
    withheld is the verdict, because the two sides would not be like for like.
    """
    doc = load(directory)
    tolerance = float((doc or {}).get("tolerance_pct", 8.0))
    lang = observed_language_version or language_version()

    def declined(reason: str, baseline_ms: float | None = None) -> Comparison:
        return Comparison(measured_ms=measured_ms, baseline_ms=baseline_ms,
                          regression_pct=None, tolerance_pct=tolerance,
                          comparable=False, reason=reason)

    if doc is None:
        return declined(f"no {BASELINE_FILENAME} in this checkout")

    profile = doc.get("profile") or {}
    want_profile = profile.get("id")
    have_profile = (os.environ.get(PROFILE_ENV_VAR) or "").strip()
    if not have_profile:
        return declined(
            f"{PROFILE_ENV_VAR} is unset, so this machine does not claim to be "
            f"{want_profile!r}; the baseline was recorded there"
        )
    if have_profile != want_profile:
        return declined(
            f"{PROFILE_ENV_VAR}={have_profile!r} but the baseline was recorded "
            f"on {want_profile!r}"
        )

    want_lang = (profile.get("language_versions") or {}).get(implementation)
    if want_lang is not None and str(want_lang) != lang:
        return declined(
            f"{implementation} {lang} is not the {want_lang} the baseline was "
            f"recorded on; interpreter versions differ by more than the bar"
        )

    want_corpus = doc.get("corpus")
    if want_corpus is not None and want_corpus != corpus_id:
        return declined(
            f"corpus {corpus_id!r} is not the {want_corpus!r} the baseline was "
            f"recorded on; latency scales with essay length"
        )

    entry = (doc.get("implementations") or {}).get(implementation) or {}
    recorded = entry.get("pooled_median_ms")
    if recorded is None:
        return declined(
            f"no baseline recorded for {implementation} yet — the next release "
            f"records one"
        )

    recorded = float(recorded)
    if recorded <= 0:
        return declined(f"recorded baseline for {implementation} is not positive",
                        recorded)

    return Comparison(
        measured_ms=measured_ms,
        baseline_ms=recorded,
        regression_pct=(measured_ms / recorded - 1.0) * 100.0,
        tolerance_pct=tolerance,
        comparable=True,
        reason=None,
    )


def render(c: Comparison) -> str:
    if not c.comparable:
        return (f"latency {c.measured_ms:.3f} ms — NOT COMPARED against the "
                f"last release: {c.reason}")
    sign = "+" if (c.regression_pct or 0.0) >= 0 else ""
    return (f"latency {c.measured_ms:.3f} ms vs {c.baseline_ms:.3f} ms at the "
            f"last release — {sign}{c.regression_pct:.2f}% against a "
            f"{c.tolerance_pct:.0f}% bar")
