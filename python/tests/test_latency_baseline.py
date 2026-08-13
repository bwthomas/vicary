"""The latency regression comparison, including every refusal to make it.

A relative gate has two ways to be useless and only one of them is loud. It can
fail on differences that are not the code — which everybody notices, because it
red-lights a green build — or it can quietly decline to compare and report that
as a pass, which nobody notices until a regression ships. So the cases below
assert the *reason* as well as the verdict, and several of them prove the gate
still fails on an actual slowdown: a comparison that cannot fail is not a gate.

The refusals changed shape when the comparison did. They used to be about the
machine this run is on versus the machine a number was recorded on. They are now
about the paired record — is there one, is it this port's, was it taken on these
essays, was it taken for this commit — because both sides of the comparison are
now measured on one machine and the machine cancels.
"""
from __future__ import annotations

import json

import pytest

from vicary.eval import baseline

TOLERANCE = 8.0
CORPUS = "persuade-20"
HEAD = "a" * 40


def write_spec(tmp_path, *, tolerance=TOLERANCE):
    (tmp_path / baseline.SPEC_FILENAME).write_text(
        json.dumps({"document_version": 2, "tolerance_pct": tolerance})
    )
    return tmp_path


def write_pair(tmp_path, *, previous_ms=10.0, current_ms=10.0,
               implementation="python", corpus=CORPUS, head=HEAD,
               document_version=baseline.PAIR_DOCUMENT_VERSION, name="pair.json"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "document_version": document_version,
        "implementation": implementation,
        "corpus": corpus,
        "head_sha": head,
        "against": {"ref": "v0.2.4", "sha": "b" * 40},
        "previous_ms": previous_ms,
        "current_ms": current_ms,
    }))
    return str(path)


@pytest.fixture(autouse=True)
def no_ambient_pair(monkeypatch):
    """Nothing here may read the record a real run left in the environment."""
    monkeypatch.delenv(baseline.PAIR_ENV_VAR, raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)


def compare(tmp_path, **kwargs):
    write_spec(tmp_path)
    return baseline.compare(10.0, CORPUS, directory=tmp_path,
                            pair_path=write_pair(tmp_path, **kwargs))


# ---------------------------------------------------------------------------
# The refusals
# ---------------------------------------------------------------------------


def test_no_paired_measurement_declines(tmp_path):
    """The ordinary laptop case, and the one that must never read as a pass."""
    write_spec(tmp_path)
    c = baseline.compare(10.0, CORPUS, directory=tmp_path)
    assert not c.comparable and not c.holds
    assert baseline.PAIR_ENV_VAR in (c.reason or "")


def test_a_missing_record_declines(tmp_path):
    write_spec(tmp_path)
    c = baseline.compare(10.0, CORPUS, directory=tmp_path,
                         pair_path=str(tmp_path / "nope.json"))
    assert not c.comparable
    assert "does not exist" in (c.reason or "")


def test_an_unreadable_record_declines_rather_than_passing(tmp_path):
    """A broken harness and an absent one must not report the same thing."""
    write_spec(tmp_path)
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    c = baseline.compare(10.0, CORPUS, directory=tmp_path, pair_path=str(path))
    assert not c.comparable
    assert "could not be read" in (c.reason or "")


def test_a_record_this_reader_does_not_understand_declines(tmp_path):
    c = compare(tmp_path, document_version=99)
    assert not c.comparable
    assert "document_version 99" in (c.reason or "")


def test_another_ports_record_declines(tmp_path):
    """Three ports write records side by side; reading Ruby's is a wrong answer,
    not a missing one — the two are 2-3x apart in absolute cost."""
    c = compare(tmp_path, implementation="ruby")
    assert not c.comparable
    assert "'ruby'" in (c.reason or "")


def test_another_corpus_declines(tmp_path):
    c = compare(tmp_path, corpus="asap-aes-set8")
    assert not c.comparable
    assert "asap-aes-set8" in (c.reason or "")


def test_a_record_from_another_commit_declines(tmp_path, monkeypatch):
    """A stale artifact is the one failure this design invites: the record is a
    file, and a file outlives the job that wrote it."""
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    c = compare(tmp_path)
    assert not c.comparable
    assert "stale" in (c.reason or "")


def test_the_commit_check_passes_when_the_record_is_this_build(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", HEAD)
    c = compare(tmp_path)
    assert c.comparable and c.holds


def test_a_previous_measurement_of_zero_declines(tmp_path):
    c = compare(tmp_path, previous_ms=0.0)
    assert not c.comparable
    assert "not positive" in (c.reason or "")


# ---------------------------------------------------------------------------
# The verdicts
# ---------------------------------------------------------------------------


def test_unchanged_code_holds(tmp_path):
    c = compare(tmp_path, previous_ms=10.0, current_ms=10.0)
    assert c.comparable and c.holds
    assert c.regression_pct == pytest.approx(0.0)
    assert c.against == "v0.2.4"


def test_within_the_tolerance_holds(tmp_path):
    c = compare(tmp_path, previous_ms=10.0, current_ms=10.7)
    assert c.holds and c.regression_pct == pytest.approx(7.0)


def test_just_over_the_bar_fails(tmp_path):
    c = compare(tmp_path, previous_ms=10.0, current_ms=10.81)
    assert c.comparable and not c.holds


def test_a_real_slowdown_fails(tmp_path):
    c = compare(tmp_path, previous_ms=10.0, current_ms=13.0)
    assert c.comparable and not c.holds
    assert c.regression_pct == pytest.approx(30.0)


def test_getting_faster_is_never_a_failure(tmp_path):
    c = compare(tmp_path, previous_ms=10.0, current_ms=6.0)
    assert c.holds and c.regression_pct == pytest.approx(-40.0)


def test_the_verdict_comes_from_the_pair_and_not_from_this_process(tmp_path):
    """The property the whole design rests on.

    This process's own figure is reported and never gated. Here it is 10 ms
    against a pair measured at 3 ms — the laptop-versus-runner gap that broke
    both earlier designs — and the verdict still comes from the two numbers that
    were taken back to back on one machine.
    """
    write_spec(tmp_path)
    path = write_pair(tmp_path, previous_ms=3.0, current_ms=3.1)
    c = baseline.compare(10.0, CORPUS, directory=tmp_path, pair_path=path)
    assert c.comparable and c.holds
    assert c.measured_ms == 10.0
    assert c.regression_pct == pytest.approx((3.1 / 3.0 - 1.0) * 100.0)
    assert "10.000 ms here" in baseline.render(c)


# ---------------------------------------------------------------------------
# The file that ships
# ---------------------------------------------------------------------------


def test_the_shipped_spec_declares_a_tolerance_and_a_protocol():
    """It carries no measurements on purpose, and a reader should be able to see
    that this is deliberate rather than an empty file."""
    doc = baseline.load()
    assert doc is not None
    assert float(doc["tolerance_pct"]) > 0
    assert "paired" in doc["protocol"]
    assert "implementations" not in doc, (
        "recorded per-release measurements are what the paired protocol "
        "replaced; leaving them here would let a stale number be read as a gate"
    )
