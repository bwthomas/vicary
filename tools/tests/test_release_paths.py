"""Every path that can publish must measure the latency gate, not just run it.

The defect this exists to prevent has now happened twice, in two different
shapes, and both times it split one commit across three registries.

The first shape: ``release.yml`` ran no gates at all, so PyPI took 0.2.3 while
RubyGems refused it — the registry that checked was the only one that could
refuse. The second shape is subtler and was invisible to every existing test.
``release-npm.yml`` and ``release-gem.yml`` *did* run their gate suites, but
without ``VICARY_LATENCY_PROFILE`` set, and the latency gate declines to compare
on a machine that does not claim the profile its baseline was recorded on. So
both workflows measured the number, printed it, reported NOT MEASURED, and
published. npm 0.2.4 shipped having measured a figure that would have been
+18.5% against the recorded baseline had anything compared it.

That is the failure mode this file checks: a gate that is *present* but *not
entitled to an opinion* looks exactly like a passing gate in a green check list.
Two things have to be true of a publish path, and neither is visible from inside
the gate code:

1. The profile env var is set, so the comparison is entitled to happen; and
2. the job that sets it pins the language version the baseline was recorded on,
   because the gate refuses across interpreter versions — and a refusal is
   reported the same way whether it came from a missing env var or a version
   mismatch. Setting the var on the wrong interpreter buys nothing and reads
   like a fix.

Both facts are read out of ``conformance/latency_baseline.json`` rather than
typed here, so moving the recorded profile moves what this asserts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from vicary.eval import conformance

#: Publish path -> which port's baseline entry its gate job is measuring, and
#: the workflow key that pins that port's language version.
PUBLISH_PATHS = {
    "release.yml": ("python", "python-version"),
    "release-npm.yml": ("typescript", "node-version"),
    "release-gem.yml": ("ruby", "ruby-version"),
}

PROFILE_ENV_VAR = "VICARY_LATENCY_PROFILE"


def repo_root() -> Path:
    return Path(conformance.conformance_dir()).parent


def baseline() -> dict:
    path = Path(conformance.conformance_dir()) / "latency_baseline.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def jobs_of(workflow: Path) -> dict[str, str]:
    """Split a workflow into ``job name -> its text``.

    Deliberately textual rather than a YAML parse: the repository has no YAML
    dependency, and what this test needs is a job's *extent*, which the two-space
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


@pytest.mark.parametrize("filename", sorted(PUBLISH_PATHS))
def test_every_publish_path_lets_the_latency_gate_compare(filename: str) -> None:
    implementation, version_key = PUBLISH_PATHS[filename]
    doc = baseline()
    profile_id = doc["profile"]["id"]
    want_version = doc["profile"]["language_versions"][implementation]

    workflow = repo_root() / ".github" / "workflows" / filename
    jobs = jobs_of(workflow)

    setting = {n: t for n, t in jobs.items() if f"{PROFILE_ENV_VAR}: {profile_id}" in t}
    assert setting, (
        f"{filename} never sets {PROFILE_ENV_VAR}: {profile_id}, so its latency "
        f"gate measures the number and reports NOT MEASURED — which publishes"
    )

    # In the job that claims the profile, on the interpreter it was recorded on.
    entitled = {
        n: t for n, t in setting.items() if f'{version_key}: "{want_version}"' in t
    }
    assert entitled, (
        f"{filename} sets {PROFILE_ENV_VAR} in {sorted(setting)} but none of "
        f"those jobs pins {version_key} \"{want_version}\", which is the "
        f"version {implementation}'s baseline was recorded on. The gate refuses "
        f"across versions, and that refusal reads exactly like the missing env "
        f"var this same test caught"
    )


def test_the_recorded_profile_is_the_one_ci_claims() -> None:
    """The everyday path claims the same profile the publish paths do.

    ``ci.yml`` sets the var conditionally, on the one matrix entry per port that
    matches the baseline. If its spelling of the profile id ever drifts from the
    baseline's, main goes green while every release path compares and CI does
    not — the asymmetry that shipped 0.2.3 to two registries out of three.
    """
    profile_id = baseline()["profile"]["id"]
    ci = (repo_root() / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    claims = ci.count(f"'{profile_id}'")
    assert claims == len(PUBLISH_PATHS), (
        f"ci.yml claims profile '{profile_id}' {claims} times; expected one per "
        f"port ({len(PUBLISH_PATHS)}). A port whose CI job never claims the "
        f"profile has no gate on main, only on release"
    )
