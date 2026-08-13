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

#: The invocation each publish path uploads with, per workflow. Includes the
#: ``run:``/``uses:`` prefix on purpose: these workflows discuss `npm publish` and
#: `gem push` at length in their comments, and a bare command name matches the
#: prose. A guard asserted against a comment is a guard asserted against nothing —
#: the first draft of this test did exactly that and failed, which is the only
#: reason it is written this way.
UPLOAD_COMMANDS = {
    "release.yml": "uses: pypa/gh-action-pypi-publish",
    "release-npm.yml": "run: npm publish --provenance",
    "release-gem.yml": "run: gem push vicary-",
}

#: What must appear in the guard on an upload. Textual on purpose: the assertion
#: is about the workflow's own source, and a YAML parse of an ``if:`` expression
#: would still leave the expression as a string to be matched.
TAG_GUARD = "startsWith(github.ref, 'refs/tags/v')"


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


@pytest.mark.parametrize("filename", sorted(PUBLISH_PATHS))
def test_no_publish_path_can_upload_without_a_tag(filename: str) -> None:
    """An upload happens because of a tag, or it does not happen.

    All three of these workflows answer ``workflow_dispatch`` as well as a tag
    push, and none of them used to require the tag before uploading. What stood
    in the way was only that the version already existed on the registry: PyPI
    rejects a duplicate file, ``npm publish`` errors on an existing version (and
    this workflow asks the registry first), and RubyGems refuses a version it
    already serves.

    That is a coincidence, not a guard, and it expires exactly when it matters.
    Bump ``VERSION``, do not tag it yet, dispatch one of these to check something
    unrelated, and the release publishes itself — from whatever the default branch
    happens to contain, with a real minted credential, at a moment nobody chose.
    Two of the three had a probe input that skips uploading, but a probe is opt-in,
    so leaving it off is the default path rather than a special case.

    The tag is also the only thing tying an upload to a reviewed version: the
    ``The tag must be the version`` step compares them, and it too is tag-gated,
    so on a dispatch there is nothing checking that the version means anything.
    """
    needle = UPLOAD_COMMANDS[filename]
    text = workflow_path(filename).read_text(encoding="utf-8")
    jobs = jobs_of(workflow_path(filename))

    assert needle in text, (
        f"{filename} no longer contains {needle!r}; this test is asserting "
        f"a guard on a command that has moved or been renamed, so it is "
        f"asserting nothing — update UPLOAD_COMMANDS"
    )

    # A job-level tag guard covers every step inside it; otherwise every step that
    # uploads has to carry the guard itself. Checked for ALL matching steps rather
    # than returning on the first: two upload steps and one guard is the same
    # exposure as no guard.
    checked = 0
    for job_name, body in jobs.items():
        if needle not in body:
            continue
        if TAG_GUARD in body.split("steps:", 1)[0]:
            checked += 1
            continue
        for block in body.split("      - "):
            if needle not in block:
                continue
            checked += 1
            assert TAG_GUARD in block, (
                f"{filename}: the step in job {job_name!r} that runs "
                f"{needle!r} is not gated on a tag, so a workflow_dispatch "
                f"run can upload. Add {TAG_GUARD} to its `if:`, or gate the "
                f"whole job on it.\n\n{block[:400]}"
            )

    assert checked, f"{filename}: found no job containing {needle!r}"


@pytest.mark.parametrize("filename", sorted(PUBLISH_PATHS))
def test_every_publish_path_can_rehearse_its_credential(filename: str) -> None:
    """A trust chain first exercised by a real release is a trust chain nobody has tested.

    Every one of these paths authenticates by trusted publishing (OIDC), and the
    claims are matched against the repository, the workflow *filename* and the
    environment. So a misregistered publisher is invisible to every other check
    here — the artifact can be perfect, the gates green, and the upload still
    refused with `invalid-publisher` at the last step of a release.

    ``release-npm`` and ``release-gem`` each carried a probe for that reason and
    ``release.yml`` did not, which made PyPI the one registry whose credential
    would first be exercised by a release rather than by a rehearsal. This asserts
    the capability exists on all three, not that it works — only running it proves
    that, which is what the probe is for.

    The probe input has to be a dispatch input rather than a hardcoded flag: it
    must be settable without editing the file, because editing the file is the
    thing whose effect on the OIDC claims is being rehearsed.
    """
    text = workflow_path(filename).read_text(encoding="utf-8")
    header = text.split("\njobs:\n", 1)[0]

    assert "workflow_dispatch:" in header, (
        f"{filename} cannot be dispatched manually, so its credential path can "
        f"only ever be exercised by a real release"
    )
    probes = [name for name in ("publish_probe", "push_probe") if f"{name}:" in header]
    assert probes, (
        f"{filename} declares no probe input, so the only way to find out whether "
        f"its trusted publisher is registered correctly is to publish. Add a "
        f"boolean dispatch input that mints the credential and stops."
    )
    assert "type: boolean" in header, (
        f"{filename}'s probe input {probes} is not declared boolean, so `if:` "
        f"comparisons against it are string comparisons and the guard is not "
        f"what it looks like"
    )
