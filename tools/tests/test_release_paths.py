"""Every path that can publish must measure the latency gate, not just run it.

The defect this exists to prevent has happened twice, in two shapes, and both
times it split one commit across three registries.

The first shape: ``release.yml`` ran no gates at all, so PyPI took 0.2.3 while
RubyGems refused it — the registry that checked was the only one that could
refuse. The second is subtler and was invisible to every other test.
``release-npm.yml`` and ``release-gem.yml`` *did* run their gate suites, but with
nothing for the latency gate to compare against, and that gate declines rather
than fails when it cannot compare. So both workflows measured the number, printed
it, reported NOT MEASURED, and published. npm 0.2.4 shipped having measured a
figure that would have been +18.5% against the baseline of the day.

That is the failure mode: a gate that is *present* but *not entitled to an
opinion* looks exactly like a passing gate in a green check list. Under the
paired protocol two things have to be true of a publish path, and neither is
visible from inside the gate code:

1. the pair is actually taken there — ``tools/latency_pair.py`` runs, timing the
   previous release and this checkout on that runner; and
2. the gate is pointed at what it wrote, via the env var the ports read.

A third is true of the workflow rather than the gate: the checkout must be deep.
The pair's comparison point is a release TAG in this repository's history, and
``actions/checkout``'s default shallow fetch has no tags at all — so a workflow
that takes the pair on a shallow checkout fails at the first step, or worse,
would silently have nothing to compare against.

The env var name is read out of the port's own module rather than typed here, so
renaming it in the gate renames it in this assertion too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from vicary.eval import baseline, conformance

#: Publish path -> the implementation its gate job measures.
PUBLISH_PATHS = {
    "release.yml": "python",
    "release-npm.yml": "typescript",
    "release-gem.yml": "ruby",
}

#: Everyday CI. Not a publish path, but the same asymmetry applies: a port whose
#: CI job never takes the pair has a latency gate only on release day, which is
#: the worst possible moment to discover it fails.
CI_WORKFLOW = "ci.yml"


def repo_root() -> Path:
    return Path(conformance.conformance_dir()).parent


def jobs_of(workflow: Path) -> dict[str, str]:
    """Split a workflow into ``job name -> its text``.

    Deliberately textual rather than a YAML parse: the repository has no YAML
    dependency, and what this needs is a job's *extent*, which the two-space
    indentation under ``jobs:`` gives unambiguously in these files.
    """
    text = workflow.read_text(encoding="utf-8")
    body = text.split("\njobs:\n", 1)
    assert len(body) == 2, f"{workflow.name} has no jobs: block"
    out: dict[str, str] = {}
    name: str | None = None
    lines: list[str] = []
    for line in body[1].splitlines():
        header = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if header:
            if name is not None:
                out[name] = "\n".join(lines)
            name, lines = header.group(1), []
            continue
        lines.append(line)
    if name is not None:
        out[name] = "\n".join(lines)
    return out


def workflow_path(filename: str) -> Path:
    return repo_root() / ".github" / "workflows" / filename


@pytest.mark.parametrize("filename", sorted(PUBLISH_PATHS))
def test_every_publish_path_lets_the_latency_gate_compare(filename: str) -> None:
    implementation = PUBLISH_PATHS[filename]
    jobs = jobs_of(workflow_path(filename))

    taking = {
        name: text for name, text in jobs.items()
        if f"latency_pair.py --impl {implementation}" in text
    }
    assert taking, (
        f"{filename} never runs the latency pair for {implementation}, so its "
        f"latency gate has nothing to compare against and reports NOT MEASURED "
        f"— which publishes"
    )

    pointed = {
        name: text for name, text in taking.items()
        if f"{baseline.PAIR_ENV_VAR}:" in text
    }
    assert pointed, (
        f"{filename} takes the pair in {sorted(taking)} but never sets "
        f"{baseline.PAIR_ENV_VAR}, so the gate never reads it. Measuring both "
        f"sides and then not comparing them is the same green check as not "
        f"measuring at all"
    )

    for name, text in pointed.items():
        assert "fetch-depth: 0" in text, (
            f"{filename}'s {name} job takes the pair on a shallow checkout. The "
            f"comparison point is a release tag in this history, and the default "
            f"fetch brings none"
        )


def test_every_port_takes_the_pair_on_ordinary_ci() -> None:
    """Not only on release day.

    A latency gate that runs for the first time on a `v*` tag is a gate whose
    first verdict lands when the release is already in flight — which is exactly
    how 0.2.3 got split, one registry refusing what two had already taken.
    """
    jobs = jobs_of(workflow_path(CI_WORKFLOW))
    text = "\n".join(jobs.values())
    for implementation in sorted(set(PUBLISH_PATHS.values())):
        assert f"latency_pair.py --impl {implementation}" in text, (
            f"{CI_WORKFLOW} never takes the latency pair for {implementation}"
        )
    assert text.count(f"{baseline.PAIR_ENV_VAR}:") == len(set(PUBLISH_PATHS.values())), (
        f"{CI_WORKFLOW} should point exactly one job per port at its pair record"
    )


def test_the_spec_the_gate_reads_carries_no_recorded_measurements() -> None:
    """The stored baseline is gone, and it has to stay gone.

    While a recorded number sits in the file, a port that still reads it compares
    this machine against some other machine's figure — the design that red-lit
    main on unchanged code. The pair replaced it precisely because that number
    could not mean anything across GitHub's runner pool.
    """
    path = Path(conformance.conformance_dir()) / baseline.SPEC_FILENAME
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert "implementations" not in doc and "profile" not in doc, (
        f"{baseline.SPEC_FILENAME} carries recorded measurements again; the "
        f"paired protocol exists because a stored number is not comparable "
        f"across machines"
    )
    assert float(doc["tolerance_pct"]) > 0
