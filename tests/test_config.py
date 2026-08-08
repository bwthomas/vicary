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
        config.EVAL_CENSUS_CSV_ENV_VAR,
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
    """The extracted code carried ``scrible-prod`` as a literal. A library that
    hardcodes one deployment's environment names cannot be embedded in a second
    one, and the value is unguessable from outside that deployment."""
    monkeypatch.setenv("ENVIRONMENT", "scrible-prod")
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


#: Names introduced by this library rather than renamed from the host, so they
#: have no legacy spelling to accept. Adding to this set is the deliberate act
#: the test below forces.
NEW_SINCE_EXTRACTION = {
    config.ASSET_PATH_ENV_VAR,
    config.EVAL_CENSUS_CSV_ENV_VAR,
    # No legacy spelling because the setting did not exist: hosts got the
    # identity-only detector unconditionally, with no way to ask for anything
    # else. There is nothing to stay compatible with.
    config.NAME_DETECTION_ENV_VAR,
    # Newer still, and inert unset: outbound inherits inbound, so there is no
    # prior behaviour for a legacy name to preserve.
    config.NAME_DETECTION_OUTBOUND_ENV_VAR,
    # Build-time only, and read by no deployment: it points the gazetteer builder
    # at a locally-downloaded SSA archive. Nothing ever set an older name for it
    # because nothing outside a rebuild reads it at all.
    config.BUILD_SSA_NAMES_ZIP_ENV_VAR,
}


def _declared_names() -> set[str]:
    """Every ``*_ENV_VAR`` constant the config module declares.

    Discovered rather than listed, so a name added without a legacy entry fails
    this file instead of quietly working.
    """
    return {
        value
        for name, value in vars(config).items()
        if name.endswith("_ENV_VAR") and isinstance(value, str)
    }


def test_every_name_the_library_owns_carries_the_prefix() -> None:
    """A name that slipped back into a host's namespace is the defect this whole
    module exists to prevent, and it is trivially checkable."""
    for name in _declared_names():
        assert name.startswith(config.VAR_PREFIX), name


def test_every_renamed_variable_still_accepts_its_old_spelling() -> None:
    """A rename without a fallback is an outage on somebody's next deploy.

    Seven variables moved out of the host's namespace. Each must appear in
    ``LEGACY_NAMES``, or be explicitly listed as new.
    """
    missing = _declared_names() - set(config.LEGACY_NAMES) - NEW_SINCE_EXTRACTION
    assert not missing, (
        f"{sorted(missing)} declare no legacy spelling and are not listed in "
        "NEW_SINCE_EXTRACTION. Either add the old name or say it is new."
    )


# ---------------------------------------------------------------------------
# The two directions are separately configurable


def test_outbound_inherits_inbound_when_unset(monkeypatch) -> None:
    """The dial is inert until set — adding it changed no deployment."""
    from vicary.redaction import name_detection_outbound

    monkeypatch.delenv(config.NAME_DETECTION_OUTBOUND_ENV_VAR, raising=False)
    monkeypatch.setenv(config.NAME_DETECTION_ENV_VAR, "gazetteer")
    assert name_detection_outbound(inbound="gazetteer") == "gazetteer"


def test_outbound_can_differ_from_inbound(monkeypatch) -> None:
    from vicary.redaction import name_detection_outbound

    monkeypatch.setenv(config.NAME_DETECTION_OUTBOUND_ENV_VAR, "identity")
    assert name_detection_outbound(inbound="gazetteer-lowercase") == "identity"


def test_matching_levels_share_one_classifier(monkeypatch) -> None:
    """Not two equal ones. Two objects that agree today can stop agreeing.

    A second classifier built from an identical level would let the outbound
    number drift with nothing in the configuration to explain it, which is the
    shape of every defect this module has had.
    """
    from vicary.redaction import build_redactor_if_enabled

    monkeypatch.setenv(config.REDACTION_ENV_VAR, "local")
    monkeypatch.setenv(config.NAME_DETECTION_ENV_VAR, "gazetteer")
    monkeypatch.setenv(config.NAME_DETECTION_OUTBOUND_ENV_VAR, "gazetteer")
    r = build_redactor_if_enabled(True)
    assert r is not None and r._outbound_classifier is None


def test_a_stricter_outbound_level_actually_reaches_the_outbound_pass(
    monkeypatch,
) -> None:
    """The guard that makes the dial a control rather than a label.

    Both passes call ApplyGuardrail with source="OUTPUT", so the direction was
    not represented anywhere in the call. A dial that resolved correctly and then
    fed the same classifier to both legs would pass every test above.
    """
    from vicary.redaction import build_redactor_if_enabled

    monkeypatch.setenv(config.REDACTION_ENV_VAR, "local")
    monkeypatch.setenv(config.NAME_DETECTION_ENV_VAR, "gazetteer-lowercase")
    monkeypatch.setenv(config.NAME_DETECTION_OUTBOUND_ENV_VAR, "identity")
    r = build_redactor_if_enabled(True)
    assert r is not None
    text = "My cousin Terrence Okonkwo came over that summer."
    assert "Terrence Okonkwo" not in r.redact_inbound(text).text
    assert "Terrence Okonkwo" in r.redact_outbound(text).text
