"""The pair's baseline is a release, not a tag.

``v0.2.13`` is tagged, merged into ``main``, and was published to none of the
three registries — all three release workflows failed the latency gate on it. The
driver took the newest merged tag, so 0.2.14 would have been timed against it:
-3.60% / -14.75% / -10.49%, a pass in every port, because the slow code sits on
BOTH sides of that ratio. A 0.2.14 that fixed nothing would have passed too, and
the regression against the 0.2.12 that users actually have would have shipped
without ever being printed. ``v0.2.10`` is the same story one release earlier —
``9eaa538`` cut 0.2.11 because of it.

A tag is this repository describing its own release; a registry is the release.
So these drive the burned-tag case explicitly: a tag that exists, is merged into
HEAD, and was never published. The gate must walk past it.

The registry payload shapes are parsed here too, without a network, for the
reason ``typescript/scripts/registry-serves.mjs`` gives: a shape checked by a
release is a shape checked when it is too late.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: `published_releases` carries dataclasses, and
    # a dataclass in a module absent from sys.modules cannot resolve its own
    # annotations.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def published():
    return _module("published_releases")


@pytest.fixture(scope="module")
def latency_pair(published):
    return _module("latency_pair")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    )


@pytest.fixture
def history(tmp_path: Path) -> Path:
    """A repository with v0.2.12, v0.2.13 and a commit after them."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for message, tag in (("0.2.12", "v0.2.12"), ("0.2.13", "v0.2.13"), ("after", None)):
        (repo / "VERSION").write_text(f"{message}\n")
        _git(repo, "add", "VERSION")
        _git(repo, "commit", "-q", "-m", message)
        if tag:
            _git(repo, "tag", tag)
    return repo


def _answer(published, versions, source="PyPI"):
    return published.Answer(versions=tuple(versions), error=None, source=source)


# ---------------------------------------------------------------------------
# The burned tag
# ---------------------------------------------------------------------------


def test_a_tag_the_registry_never_took_is_not_the_baseline(latency_pair, published, history,
                                                           capsys) -> None:
    """The whole defect, in one assertion: skip v0.2.13, land on v0.2.12."""
    ref, sha, skipped = latency_pair.previous_release(
        history, "python", _answer(published, ["0.2.12", "0.2.8"]))
    assert ref == "v0.2.12", (
        f"the baseline is {ref}, which PyPI has never served — a tag records that "
        f"a release was attempted, not that one happened"
    )
    assert skipped == ["v0.2.13"]
    assert sha == subprocess.run(
        ["git", "rev-list", "-n", "1", "v0.2.12"], cwd=history,
        capture_output=True, text=True, check=True).stdout.strip()
    # Named on stdout, not merely returned: a baseline that silently moved a
    # release back is a number whose provenance the reader cannot reconstruct.
    out = capsys.readouterr().out
    assert "v0.2.13" in out and "does not serve" in out


def test_the_newest_published_tag_wins_when_it_is_the_newest_tag(latency_pair, published,
                                                                 history) -> None:
    """The ordinary case still behaves: nothing is skipped when nothing is burned."""
    ref, _, skipped = latency_pair.previous_release(
        history, "python", _answer(published, ["0.2.12", "0.2.13"]))
    assert (ref, skipped) == ("v0.2.13", [])


def test_a_tag_on_head_is_still_not_its_own_baseline(latency_pair, published,
                                                     history) -> None:
    """The release-day rule the published check must not have broken: the tag push
    that publishes 0.2.14 compares against 0.2.13, never against itself."""
    _git(history, "tag", "v0.2.14")
    ref, _, _ = latency_pair.previous_release(
        history, "python", _answer(published, ["0.2.12", "0.2.13", "0.2.14"]))
    assert ref == "v0.2.13"


def test_every_tag_unpublished_is_a_refusal_not_the_newest_one(latency_pair, published,
                                                               history) -> None:
    """Nothing to compare against beats comparing against nothing."""
    with pytest.raises(SystemExit) as exit_info:
        latency_pair.previous_release(history, "python", _answer(published, ["0.1.0"]))
    assert exit_info.value.code == 1


# ---------------------------------------------------------------------------
# Offline fails closed
# ---------------------------------------------------------------------------


def test_a_registry_that_cannot_be_reached_refuses_to_measure(latency_pair, published,
                                                              history, capsys) -> None:
    """Silently falling back to the newest tag is the defect, not the remedy."""
    unknown = published.Answer(versions=None, error="connection refused", source="PyPI")
    with pytest.raises(SystemExit) as exit_info:
        latency_pair.previous_release(history, "python", unknown)
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "connection refused" in err
    # The way out is named, and it is an assertion an operator makes out loud
    # rather than an inference the driver makes quietly.
    assert f"{published.ENV_VAR}_PYTHON" in err


def test_an_operator_can_assert_what_the_registry_serves(published) -> None:
    """The offline path, and it is per port because the registries disagree."""
    env = {f"{published.ENV_VAR}_RUBY": "0.2.12, 0.2.11", published.ENV_VAR: "0.2.8"}
    assert published.env_override("ruby", env) == ("0.2.11", "0.2.12")
    assert published.env_override("python", env) == ("0.2.8",)
    assert published.env_override("python", {}) is None
    answer = published.published_versions("ruby", environ=env)
    assert answer.serving("0.2.12") and not answer.serving("0.2.13")
    assert "asserted" in answer.source


def test_a_failed_fetch_is_unknown_rather_than_empty(published) -> None:
    """`versions is None` and `versions == ()` are different facts.

    The second says the registry serves no such package; only the first says the
    lookup failed. Collapsing them is how "I could not tell" reads as "it is not
    published" — the bug `registry-serves.mjs` was written to end, in the port
    that had no equivalent.
    """
    def refuse(url: str, timeout: float) -> str:
        raise OSError("connection refused")

    answer = published.fetch("python", opener=refuse)
    assert answer.unknown and "connection refused" in (answer.error or "")
    # A registry that answers, and has never heard of the package, is the other
    # fact: an empty tuple, and the driver refuses on it for a different reason.
    assert published._parse_npm(json.dumps({"versions": {}})).versions == ()


# ---------------------------------------------------------------------------
# The payload shapes, checked here rather than by a release
# ---------------------------------------------------------------------------


def test_pypi_yanked_only_versions_are_not_installable_releases(published) -> None:
    body = json.dumps({"releases": {
        "0.2.12": [{"filename": "w.whl", "yanked": False}],
        "0.2.11": [{"filename": "w.whl", "yanked": True}],
        "0.2.10": [],
    }})
    answer = published._parse_pypi(body)
    assert answer.versions == ("0.2.12",)


def test_npm_reads_the_packument_versions(published) -> None:
    answer = published._parse_npm(json.dumps({"versions": {"0.2.12": {}, "0.2.11": {}}}))
    assert answer.versions == ("0.2.11", "0.2.12")


def test_rubygems_reads_the_versions_api(published) -> None:
    answer = published._parse_rubygems(json.dumps([{"number": "0.2.12"}, {"number": "0.2.11"}]))
    assert answer.versions == ("0.2.11", "0.2.12")


@pytest.mark.parametrize("impl,body", [
    ("python", "not json"),
    ("python", json.dumps({"info": {"version": "0.2.12"}})),
    ("typescript", json.dumps({"name": "@bwthomas/vicary"})),
    ("ruby", json.dumps([{"version": "0.2.12"}])),
    ("ruby", json.dumps({"versions": []})),
])
def test_a_payload_whose_shape_moved_is_unknown_not_empty(published, impl, body) -> None:
    """A shape change must stop the gate, not quietly empty the release list —
    an empty list would burn every tag and refuse everything, which reads as a
    repository with no releases rather than as a parser that needs fixing."""
    answer = published.parse_for(impl, body)
    assert answer.unknown, f"{impl} read {body!r} as an answer"
    assert answer.error


def test_each_port_is_pointed_at_the_registry_it_publishes_to(published) -> None:
    """The package names are not the port names, and a typo here would make every
    tag look unpublished."""
    assert set(published.REGISTRIES) == {"python", "typescript", "ruby"}
    assert f'name = "{published.REGISTRIES["python"].package}"' in \
        (ROOT / "python" / "pyproject.toml").read_text()
    assert f'"name": "{published.REGISTRIES["typescript"].package}"' in \
        (ROOT / "typescript" / "package.json").read_text()
    assert f'spec.name = "{published.REGISTRIES["ruby"].package}"' in \
        (ROOT / "ruby" / "vicary.gemspec").read_text()
    for impl, registry in published.REGISTRIES.items():
        assert registry.url.startswith("https://"), impl
