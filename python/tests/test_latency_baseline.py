"""The latency regression comparison, including every refusal to make it.

A relative gate has two ways to be useless and only one of them is loud. It can
fail on hardware differences, which everybody notices; or it can quietly decline
to compare and report that as a pass, which nobody notices until a regression
ships. So the cases below assert the *reason* as well as the verdict, and the
last two prove the gate still fails on an actual slowdown — a comparison that
cannot fail is not a gate.
"""
from __future__ import annotations

import json

import pytest

from vicary.eval import baseline

TOLERANCE = 8.0
PROFILE = "github-ubuntu-latest"
CORPUS = "persuade-20"


def write_baseline(tmp_path, *, python_ms=10.0, lang="3.13", corpus=CORPUS):
    doc = {
        "document_version": 1,
        "tolerance_pct": TOLERANCE,
        "profile": {"id": PROFILE, "language_versions": {"python": lang}},
        "corpus": corpus,
        "implementations": {"python": {"pooled_median_ms": python_ms}},
    }
    (tmp_path / baseline.BASELINE_FILENAME).write_text(json.dumps(doc))
    return tmp_path


@pytest.fixture
def on_profile(monkeypatch):
    monkeypatch.setenv(baseline.PROFILE_ENV_VAR, PROFILE)


def test_a_checkout_with_no_baseline_file_declines(tmp_path, on_profile):
    c = baseline.compare(10.0, CORPUS, directory=tmp_path)
    assert not c.comparable and not c.holds
    assert baseline.BASELINE_FILENAME in (c.reason or "")


def test_an_unclaimed_machine_declines(tmp_path, monkeypatch):
    """The common case: a laptop, which has no business comparing itself to a
    number recorded on a CI runner."""
    monkeypatch.delenv(baseline.PROFILE_ENV_VAR, raising=False)
    c = baseline.compare(10.0, CORPUS, directory=write_baseline(tmp_path))
    assert not c.comparable and not c.holds
    assert baseline.PROFILE_ENV_VAR in (c.reason or "")


def test_a_different_profile_declines(tmp_path, monkeypatch):
    monkeypatch.setenv(baseline.PROFILE_ENV_VAR, "someones-laptop")
    c = baseline.compare(10.0, CORPUS, directory=write_baseline(tmp_path))
    assert not c.comparable
    assert "someones-laptop" in (c.reason or "")


def test_a_different_interpreter_declines(tmp_path, on_profile):
    """3.11 measured 12.30 ms where 3.13 measured 9.10 on one commit — a gap
    several times the bar, so this must not be compared away as a regression."""
    c = baseline.compare(10.0, CORPUS, directory=write_baseline(tmp_path),
                         observed_language_version="3.11")
    assert not c.comparable
    assert "3.11" in (c.reason or "") and "3.13" in (c.reason or "")


def test_a_different_corpus_declines(tmp_path, on_profile):
    c = baseline.compare(10.0, "asap-aes-set8",
                         directory=write_baseline(tmp_path),
                         observed_language_version="3.13")
    assert not c.comparable
    assert "asap-aes-set8" in (c.reason or "")


def test_an_unrecorded_baseline_declines_rather_than_passes(tmp_path, on_profile):
    """Null is not zero and not a free pass. A gate with nothing to compare
    against must not report PASS."""
    c = baseline.compare(10.0, CORPUS,
                         directory=write_baseline(tmp_path, python_ms=None),
                         observed_language_version="3.13")
    assert not c.comparable and not c.holds


def test_unchanged_code_holds(tmp_path, on_profile):
    c = baseline.compare(10.0, CORPUS, directory=write_baseline(tmp_path),
                         observed_language_version="3.13")
    assert c.comparable and c.holds
    assert c.regression_pct == pytest.approx(0.0)


def test_within_the_tolerance_holds(tmp_path, on_profile):
    c = baseline.compare(10.7, CORPUS, directory=write_baseline(tmp_path),
                         observed_language_version="3.13")
    assert c.comparable and c.holds
    assert c.regression_pct == pytest.approx(7.0)


def test_a_real_slowdown_fails(tmp_path, on_profile):
    """The negative control. If this ever passes, every case above is decoration."""
    c = baseline.compare(12.0, CORPUS, directory=write_baseline(tmp_path),
                         observed_language_version="3.13")
    assert c.comparable
    assert c.regression_pct == pytest.approx(20.0)
    assert not c.holds


def test_just_over_the_bar_fails(tmp_path, on_profile):
    """The bar is a bar, not a suggestion — 8.1% is over 8%."""
    c = baseline.compare(10.81, CORPUS, directory=write_baseline(tmp_path),
                         observed_language_version="3.13")
    assert c.comparable and not c.holds
    assert c.regression_pct > TOLERANCE


def test_getting_faster_is_never_a_failure(tmp_path, on_profile):
    c = baseline.compare(5.0, CORPUS, directory=write_baseline(tmp_path),
                         observed_language_version="3.13")
    assert c.comparable and c.holds
    assert c.regression_pct == pytest.approx(-50.0)


def test_the_shipped_baseline_file_parses_and_declares_its_profile():
    """The real file, not a fixture — a malformed one would make every port
    decline to compare and read as nine quiet passes."""
    doc = baseline.load()
    assert doc is not None, "conformance/latency_baseline.json is missing"
    assert doc["tolerance_pct"] == TOLERANCE
    assert doc["profile"]["id"]
    assert set(doc["implementations"]) == {"python", "typescript", "ruby"}
    for impl, entry in doc["implementations"].items():
        assert "pooled_median_ms" in entry, impl
        assert doc["profile"]["language_versions"].get(impl), impl
