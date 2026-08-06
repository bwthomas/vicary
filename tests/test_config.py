"""Configuration resolution: precedence, legacy names, and what counts as unset.

The reason this file exists at all is that seven environment variables changed
name when the code became a library. A rename with a fallback is only as good as
the test that proves the fallback still fires and that the new name still wins,
so each of those two claims gets a failing case here rather than a comment.
"""

from __future__ import annotations

import logging

import pytest

from vicary import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every name any test here touches, removed. Otherwise a developer's own
    exported ``ENVIRONMENT`` decides the result."""
    for name in (
        *config.LEGACY_NAMES,
        *(n for names in config.LEGACY_NAMES.values() for n in names),
        *(n for names in config.HOST_FALLBACKS.values() for n in names),
        config.ASSET_PATH_ENV_VAR,
        config.REDACTION_ENV_VAR,
    ):
        monkeypatch.delenv(name, raising=False)
    config.reset_deprecation_warnings()


# ---------------------------------------------------------------------------
# Precedence.
# ---------------------------------------------------------------------------


def test_the_current_name_wins_over_the_legacy_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.REDACTION_ENV_VAR, "local")
    monkeypatch.setenv("GRADER_PII_REDACTION", "guardrail")
    assert config.get(config.REDACTION_ENV_VAR) == "local"


def test_the_legacy_name_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the rename having a fallback."""
    monkeypatch.setenv("GRADER_PII_REDACTION", "guardrail")
    assert config.get(config.REDACTION_ENV_VAR) == "guardrail"


def test_the_legacy_name_beats_a_host_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GRADER_ENV`` used to take precedence over ``ENVIRONMENT``; it still does.

    A deployment that set both, relying on the specific one, must not silently
    start obeying the generic one.
    """
    monkeypatch.setenv("GRADER_ENV", "production")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    assert config.deployment_environment() == "production"


def test_the_host_fallback_is_read_when_nothing_else_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert config.deployment_is_production()


def test_an_empty_value_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A shell wrapper exporting ``FOO=`` is indistinguishable from not setting it,
    and treating the two differently produces bugs nobody can reproduce."""
    monkeypatch.setenv(config.DEPLOY_ENV_VAR, "   ")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert config.deployment_is_production()


def test_a_default_is_returned_when_nothing_is_set() -> None:
    assert config.get(config.REDACTION_ENV_VAR, "fallback") == "fallback"


def test_names_for_reports_the_whole_chain() -> None:
    chain = config.names_for(config.DEPLOY_ENV_VAR)
    assert chain == (config.DEPLOY_ENV_VAR, "GRADER_ENV", "ENVIRONMENT")


# ---------------------------------------------------------------------------
# Deprecation notice.
# ---------------------------------------------------------------------------


def test_reading_a_legacy_name_warns_and_names_the_replacement(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("GRADER_PII_GUARDRAIL_ID", "gr-abc")
    with caplog.at_level(logging.WARNING, logger="vicary.config"):
        assert config.get(config.GUARDRAIL_ID_ENV_VAR) == "gr-abc"
    assert "GRADER_PII_GUARDRAIL_ID" in caplog.text
    assert config.GUARDRAIL_ID_ENV_VAR in caplog.text


def test_the_deprecation_notice_fires_once_per_process(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """This resolves on a per-request path in some hosts. A log line per request
    is how a useful warning becomes noise somebody filters out."""
    monkeypatch.setenv("GRADER_PII_GUARDRAIL_ID", "gr-abc")
    with caplog.at_level(logging.WARNING, logger="vicary.config"):
        for _ in range(5):
            config.get(config.GUARDRAIL_ID_ENV_VAR)
    assert caplog.text.count("GRADER_PII_GUARDRAIL_ID") == 1


def test_the_current_name_never_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(config.GUARDRAIL_ID_ENV_VAR, "gr-abc")
    with caplog.at_level(logging.WARNING, logger="vicary.config"):
        config.get(config.GUARDRAIL_ID_ENV_VAR)
    assert caplog.text == ""


def test_the_host_convention_is_not_deprecated(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """``ENVIRONMENT`` is a name the deployment owns, not one we renamed."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    with caplog.at_level(logging.WARNING, logger="vicary.config"):
        config.deployment_environment()
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# Production aliases — extendable rather than a patched literal.
# ---------------------------------------------------------------------------


def test_no_host_specific_alias_ships_in_the_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The extracted code carried ``examplecorp-prod`` as a literal. A library that
    hardcodes one deployment's environment names cannot be embedded in a second
    one, and the value is unguessable from outside that deployment."""
    monkeypatch.setenv("ENVIRONMENT", "examplecorp-prod")
    assert not config.deployment_is_production()


def test_a_host_can_register_its_own_production_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = set(config.PRODUCTION_ALIASES)
    try:
        config.add_production_alias("Acme-Prod")
        monkeypatch.setenv("ENVIRONMENT", "acme-prod")
        assert config.deployment_is_production()
    finally:
        config.PRODUCTION_ALIASES.clear()
        config.PRODUCTION_ALIASES.update(original)


@pytest.mark.parametrize("value", ["prod", "production", "live", "PRODUCTION"])
def test_the_generic_aliases_are_recognised(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", value)
    assert config.deployment_is_production()


# ---------------------------------------------------------------------------
# Eval corpus, which is deliberately absent.
# ---------------------------------------------------------------------------


def test_an_unconfigured_corpus_resolves_to_empty_not_a_guess() -> None:
    """Callers key "skip this measurement" off the empty string. A speculative
    default path would make a corpus-dependent gate report on nothing and pass."""
    assert config.eval_corpus_tsv() == ""


def test_an_explicit_corpus_tsv_wins_over_a_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.EVAL_CORPUS_TSV_ENV_VAR, "/data/mine.tsv")
    monkeypatch.setenv(config.EVAL_CORPUS_DIR_ENV_VAR, "/data/corpus")
    assert config.eval_corpus_tsv() == "/data/mine.tsv"


def test_a_corpus_directory_is_joined_with_the_expected_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.EVAL_CORPUS_DIR_ENV_VAR, "/data/corpus")
    assert config.eval_corpus_tsv() == f"/data/corpus/{config.EVAL_CORPUS_FILENAME}"


def test_the_legacy_corpus_variables_still_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRADER_CORPUS_REPO", "/data/corpus")
    assert config.eval_corpus_tsv() == f"/data/corpus/{config.EVAL_CORPUS_FILENAME}"


def test_every_name_the_library_owns_carries_the_prefix() -> None:
    """A name that slipped back into a host's namespace is the defect this whole
    module exists to prevent, and it is trivially checkable."""
    owned = [
        config.REDACTION_ENV_VAR,
        config.GUARDRAIL_ID_ENV_VAR,
        config.GUARDRAIL_VERSION_ENV_VAR,
        config.GUARDRAIL_REGION_ENV_VAR,
        config.DEPLOY_ENV_VAR,
        config.ASSET_PATH_ENV_VAR,
        config.EVAL_CORPUS_TSV_ENV_VAR,
        config.EVAL_CORPUS_DIR_ENV_VAR,
    ]
    assert set(owned) == set(config.LEGACY_NAMES) | {
        config.ASSET_PATH_ENV_VAR
    }, "a name was added without a legacy entry or an explicit exemption"
    for name in owned:
        assert name.startswith(config.VAR_PREFIX), name
