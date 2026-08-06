"""Unit tests for the config-gated redaction seam.

All tests use a faked Guardrails client — no live AWS calls.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from vicary.config import DEPLOY_ENV_VAR
from vicary.redaction import (
    GUARDRAIL_ID_ENV_VAR,
    GUARDRAIL_VERSION_ENV_VAR,
    MODE_GUARDRAIL,
    MODE_LOCAL,
    MODE_OFF,
    MODE_STUB,
    REDACTION_ENV_VAR,
    Redactor,
    build_redactor_if_enabled,
    guardrail_version,
    redaction_enabled,
    redaction_mode,
)


def unnumber(text: str) -> str:
    """Strip placeholder indices, so a test asserts the KIND it actually means.

    ``{NAME_1}`` -> ``{NAME}``. Numbering identifies *which* entity; almost every
    test here is about *what* the entity was typed as, and hardcoding an index
    would make each one break whenever an unrelated span is added earlier in the
    document. The tests that are genuinely about numbering assert on the raw
    output instead.
    """
    from vicary.eval.fixture import placeholder_kind

    return re.sub(r"\{[A-Za-z_0-9]*\}", lambda m: placeholder_kind(m.group(0)), text)


class FakeGuardrailsClient:
    """Stand-in for ``boto3.client('bedrock-runtime')``.

    Replaces any substring in ``replacements`` with its mapped placeholder and
    reports ``GUARDRAIL_INTERVENED`` when it changed the text. Records calls so
    tests can assert on identifier/version/source wiring.

    **``anonymize_on`` mirrors a real Bedrock behaviour and defaults to matching
    it.** ``ANONYMIZE`` masks only when ``source="OUTPUT"``; on ``INPUT`` the
    service returns ``action=NONE`` with an empty ``outputs`` list and bills
    nothing, so the caller silently receives the ORIGINAL text. This fake used to
    mask on either source, which let ``test_inbound_masks_pii`` pass green for as
    long as ``redact_inbound`` was calling ``source="INPUT"`` and redacting
    exactly 0 of 75 known PII spans in production (verified 2026-08-04 against a
    live Guardrail; see ``vicary.eval.recall``). A fake that cannot reproduce
    the production failure is a green light with a comment on it.
    """

    def __init__(self, replacements: dict[str, str] | None = None, *,
                 anonymize_on: str = "OUTPUT") -> None:
        self.replacements = replacements or {}
        self.anonymize_on = anonymize_on
        self.calls: list[dict[str, Any]] = []

    def apply_guardrail(
        self,
        *,
        guardrailIdentifier: str,
        guardrailVersion: str,
        source: str,
        content: list[dict[str, Any]],
    ) -> dict[str, Any]:
        text = content[0]["text"]["text"]
        self.calls.append(
            {
                "id": guardrailIdentifier,
                "version": guardrailVersion,
                "source": source,
                "text": text,
            }
        )
        if source != self.anonymize_on:
            # What Bedrock really returns: policy evaluated, nothing masked,
            # nothing billed, no output to fall back from.
            return {"action": "NONE", "outputs": []}
        masked = text
        for needle, repl in self.replacements.items():
            masked = masked.replace(needle, repl)
        action = "GUARDRAIL_INTERVENED" if masked != text else "NONE"
        return {"action": action, "outputs": [{"text": masked}]}


# ---------------------------------------------------------------------------
# Config resolution — configurable-default principle.
# ---------------------------------------------------------------------------


def test_redaction_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REDACTION_ENV_VAR, raising=False)
    assert redaction_enabled() is False


def test_redaction_env_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REDACTION_ENV_VAR, "true")
    assert redaction_enabled() is True


def test_redaction_kwarg_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REDACTION_ENV_VAR, "true")
    assert redaction_enabled(False) is False
    monkeypatch.setenv(REDACTION_ENV_VAR, "false")
    assert redaction_enabled(True) is True


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, MODE_OFF),
        ("", MODE_OFF),
        ("0", MODE_OFF),
        ("off", MODE_OFF),
        ("false", MODE_OFF),
        ("garbage", MODE_OFF),  # unrecognized → fail-safe off
        ("stub", MODE_STUB),
        ("simulate", MODE_STUB),
        # The generic "on" spellings now mean the free local classifier —
        # the production default since 2026-08-04. Bedrock is opt-in by name.
        ("1", MODE_LOCAL),
        ("on", MODE_LOCAL),
        ("local", MODE_LOCAL),
        ("classifier", MODE_LOCAL),
        ("guardrail", MODE_GUARDRAIL),
        ("bedrock", MODE_GUARDRAIL),
    ],
)
def test_redaction_mode_from_env(
    monkeypatch: pytest.MonkeyPatch, env_value: str | None, expected: str
) -> None:
    if env_value is None:
        monkeypatch.delenv(REDACTION_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(REDACTION_ENV_VAR, env_value)
    assert redaction_mode() == expected


def test_redaction_mode_kwarg_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REDACTION_ENV_VAR, "guardrail")
    assert redaction_mode(False) == MODE_OFF
    assert redaction_mode("stub") == MODE_STUB
    assert redaction_mode(True) == MODE_LOCAL
    assert redaction_mode("guardrail") == MODE_GUARDRAIL


def test_guardrail_version_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(GUARDRAIL_VERSION_ENV_VAR, raising=False)
    assert guardrail_version() == "DRAFT"


def test_guardrail_version_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GUARDRAIL_VERSION_ENV_VAR, "3")
    assert guardrail_version() == "3"


# ---------------------------------------------------------------------------
# Builder gating + fail-closed.
# ---------------------------------------------------------------------------


def test_builder_returns_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REDACTION_ENV_VAR, raising=False)
    assert build_redactor_if_enabled() is None


def test_builder_fails_closed_without_guardrail_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only GUARDRAIL mode needs a Guardrail; it still fails closed."""
    monkeypatch.setenv(REDACTION_ENV_VAR, "guardrail")
    monkeypatch.delenv(GUARDRAIL_ID_ENV_VAR, raising=False)
    with pytest.raises(ValueError, match="no Bedrock Guardrail"):
        build_redactor_if_enabled()


def test_builder_returns_redactor_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(REDACTION_ENV_VAR, "guardrail")
    monkeypatch.setenv(GUARDRAIL_ID_ENV_VAR, "gr-test-123")
    redactor = build_redactor_if_enabled(client=FakeGuardrailsClient())
    assert isinstance(redactor, Redactor)
    assert redactor.guardrail_id == "gr-test-123"


def test_local_mode_needs_no_guardrail_and_bills_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the default: no resource, no AWS call, no spend."""
    monkeypatch.setenv(REDACTION_ENV_VAR, "local")
    monkeypatch.delenv(GUARDRAIL_ID_ENV_VAR, raising=False)

    redactor = build_redactor_if_enabled()

    assert isinstance(redactor, Redactor)
    assert redactor.local
    result = redactor.redact_inbound("call (330) 555-0148 now")
    # Numbered, because masking has to be reversible; the assertion is about the
    # TYPE, so the index is stripped. See PlaceholderMinter.
    assert "{PHONE}" in unnumber(result.text)
    assert "{PHONE_1}" in result.text, "the shipped default must number"
    assert result.char_units == 0
    assert result.intervened


def test_local_mode_masks_the_supplied_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vicary.local_classifier import StudentIdentity

    monkeypatch.setenv(REDACTION_ENV_VAR, "local")
    redactor = build_redactor_if_enabled(
        identity=StudentIdentity(first_name="Marguerite",
                                 last_name="Delacroix-Whitfield")
    )
    assert redactor is not None
    out = redactor.redact_inbound("Marguerite Delacroix-Whitfield wrote it.").text
    assert "Marguerite" not in out


# ---------------------------------------------------------------------------
# Redaction behavior — inbound / outbound, with a fake client.
# ---------------------------------------------------------------------------


def _redactor(client: FakeGuardrailsClient, version: str = "DRAFT") -> Redactor:
    return Redactor(guardrail_id="gr-x", version=version, client=client)


def test_inbound_masks_pii() -> None:
    client = FakeGuardrailsClient({"Jane Doe": "{NAME}"})
    result = _redactor(client).redact_inbound("Essay by Jane Doe about dogs.")
    assert result.text == "Essay by {NAME} about dogs."
    assert result.intervened is True
    # OUTPUT, on the INBOUND pass, deliberately: ANONYMIZE is OUTPUT-only, and
    # `source` is a mode selector on ApplyGuardrail rather than a statement about
    # which leg of our pipeline is calling.
    assert client.calls[0]["source"] == "OUTPUT"
    assert client.calls[0]["id"] == "gr-x"
    assert client.calls[0]["version"] == "DRAFT"


def test_inbound_would_redact_nothing_if_it_asked_for_INPUT() -> None:
    """The production failure, pinned. ``ANONYMIZE`` is OUTPUT-only, so an
    inbound pass that asks for ``INPUT`` masks nothing, bills nothing, and hands
    back the raw essay — indistinguishable from clean text at the call site.
    Measured live: 0 of 75 injected spans redacted on INPUT, 73 of 75 on OUTPUT.
    """
    essay = "Essay by Jane Doe about dogs."
    client = FakeGuardrailsClient({"Jane Doe": "{NAME}"})

    # Ask for the wrong mode explicitly — what `redact_inbound` used to do.
    leaked = _redactor(client)._apply(essay, source="INPUT")
    assert leaked.text == essay, "unredacted text came back looking fine"
    assert leaked.intervened is False

    # And the shipped call does mask it, on the same fake and the same text.
    assert _redactor(FakeGuardrailsClient({"Jane Doe": "{NAME}"})).redact_inbound(
        essay).text == "Essay by {NAME} about dogs."


def test_no_pii_passes_through_unchanged() -> None:
    client = FakeGuardrailsClient({"Jane Doe": "{NAME}"})
    result = _redactor(client).redact_inbound("An essay about the water cycle.")
    assert result.text == "An essay about the water cycle."
    assert result.intervened is False


def test_outbound_uses_output_source() -> None:
    client = FakeGuardrailsClient({"jane@x.com": "{EMAIL}"})
    result = _redactor(client).redact_outbound("Reach me at jane@x.com.")
    assert result.text == "Reach me at {EMAIL}."
    assert client.calls[0]["source"] == "OUTPUT"


def test_empty_text_skips_client_call() -> None:
    client = FakeGuardrailsClient()
    result = _redactor(client).redact_inbound("")
    assert result.text == ""
    assert result.char_units == 0
    assert client.calls == []


def test_char_units_ceil_per_1000() -> None:
    client = FakeGuardrailsClient()
    result = _redactor(client).redact_inbound("x" * 1500)
    assert result.char_units == 2  # ceil(1500/1000)


def test_version_threaded_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GUARDRAIL_VERSION_ENV_VAR, "7")
    monkeypatch.setenv(GUARDRAIL_ID_ENV_VAR, "gr-y")
    client = FakeGuardrailsClient()
    redactor = Redactor(client=client)  # picks up env id + version
    redactor.redact_inbound("hello world")
    assert client.calls[0]["version"] == "7"
    assert client.calls[0]["id"] == "gr-y"


# ---------------------------------------------------------------------------
# Stub mode — offline, zero-cost testing seam (mode ``stub``).
# ---------------------------------------------------------------------------


def test_stub_builder_needs_no_guardrail_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub mode must not require a Guardrail ID (it never calls AWS)."""
    monkeypatch.setenv(REDACTION_ENV_VAR, "stub")
    monkeypatch.delenv(GUARDRAIL_ID_ENV_VAR, raising=False)
    redactor = build_redactor_if_enabled()
    assert isinstance(redactor, Redactor)
    assert redactor.simulate is True


def test_stub_masks_structured_pii_offline() -> None:
    """The stub masks regex-able entities with no injected client (fully offline)."""
    redactor = Redactor(simulate=True)
    result = redactor.redact_inbound(
        "Email jane@example.com, SSN 123-45-6789, IP 10.0.0.1."
    )
    assert "{EMAIL}" in result.text
    assert "{US_SOCIAL_SECURITY_NUMBER}" in result.text
    assert "{IP_ADDRESS}" in result.text
    assert "jane@example.com" not in result.text
    assert result.intervened is True


def test_stub_bills_zero_char_units() -> None:
    """Stub pays nothing → char_units is 0 so no phantom cost is folded in."""
    redactor = Redactor(simulate=True)
    result = redactor.redact_inbound("Contact jane@example.com " + "x" * 3000)
    assert result.intervened is True
    assert result.char_units == 0


def test_stub_no_pii_passes_through() -> None:
    redactor = Redactor(simulate=True)
    result = redactor.redact_inbound("An essay about the water cycle.")
    assert result.text == "An essay about the water cycle."
    assert result.intervened is False


# ---------------------------------------------------------------------------
# Batched outbound pass — a billing fix, not a behaviour change.
# ---------------------------------------------------------------------------


def test_outbound_batch_is_one_call_for_every_field() -> None:
    """The whole point: `ApplyGuardrail` bills ceil(len/1000), so N short fields
    cost N units billed separately and ~1 billed together. Measured on the 523
    capture: 7.260 units per-field vs 2.790 batched."""
    from vicary.redaction import Redactor

    client = FakeGuardrailsClient({"Jane": "{NAME}"})
    r = Redactor(guardrail_id="gr-1", client=client)

    texts = ["about Jane here", "second field", "third field", "fourth field"]
    masked, units, batched = r.redact_outbound_batch(texts)

    assert len(client.calls) == 1, "one call, or the saving does not exist"
    assert batched is True
    assert masked == ["about {NAME} here", "second field", "third field",
                      "fourth field"], "positional alignment is the contract"
    assert units == 1, "58 chars of joined text is one billed unit"


def test_per_field_billing_is_what_the_batch_replaces() -> None:
    """The counterfactual, so the saving is asserted rather than asserted-about."""
    from vicary.redaction import Redactor

    texts = ["a" * 300, "b" * 300, "c" * 300, "d" * 300]

    per_field = Redactor(guardrail_id="gr-1", client=FakeGuardrailsClient())
    per_field_units = sum(
        per_field.redact_outbound(t).char_units for t in texts)

    batched = Redactor(guardrail_id="gr-1", client=FakeGuardrailsClient())
    _, batch_units, _ = batched.redact_outbound_batch(texts)

    assert per_field_units == 4, "four rounded-up units for 1,200 chars"
    assert batch_units == 2, "1,209 joined chars is two units"
    assert batch_units < per_field_units


def test_a_separator_collision_falls_back_rather_than_misaligning() -> None:
    """A suggestion pasted onto the wrong glow is worse than paying for the
    extra units, so a failed round trip must degrade to per-field."""
    from vicary.redaction import _BATCH_SEPARATOR, Redactor

    # A Guardrail that eats the separator — the exact failure the guard exists for.
    client = FakeGuardrailsClient({_BATCH_SEPARATOR: " "})
    r = Redactor(guardrail_id="gr-1", client=client)

    masked, units, batched = r.redact_outbound_batch(["first", "second", "third"])

    assert batched is False, "the round-trip check must catch this"
    assert masked == ["first", "second", "third"], "still correctly aligned"
    assert len(client.calls) == 4, "the failed batch plus one call per field"
    assert units == 4, "the wasted batch call is billed, and counted honestly"


def test_empty_fields_cost_nothing_and_keep_their_slot() -> None:
    from vicary.redaction import Redactor

    client = FakeGuardrailsClient()
    r = Redactor(guardrail_id="gr-1", client=client)
    masked, units, batched = r.redact_outbound_batch(["", "", ""])
    assert (masked, units, batched) == (["", "", ""], 0, True)
    assert client.calls == []


def test_a_single_nonempty_field_needs_no_separator_at_all() -> None:
    from vicary.redaction import Redactor

    client = FakeGuardrailsClient({"Jane": "{NAME}"})
    r = Redactor(guardrail_id="gr-1", client=client)
    masked, units, batched = r.redact_outbound_batch(["", "hi Jane", ""])
    assert masked == ["", "hi {NAME}", ""]
    assert (units, batched) == (1, True)
    assert len(client.calls) == 1
    assert "␞" not in client.calls[0]["text"], (
        "a lone field must not be wrapped in separators it does not need")


def test_the_batch_runs_as_OUTPUT_not_INPUT() -> None:
    from vicary.redaction import Redactor

    client = FakeGuardrailsClient()
    Redactor(guardrail_id="gr-1", client=client).redact_outbound_batch(
        ["one", "two"])
    assert client.calls[0]["source"] == "OUTPUT"


# ---------------------------------------------------------------------------
# Per-environment default (Blake, 2026-08-04): off in dev, on in prod.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["prod", "production", "live", "PROD"])
def test_production_turns_redaction_on_with_no_flag(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from vicary.redaction import MODE_LOCAL, redaction_mode

    monkeypatch.delenv(REDACTION_ENV_VAR, raising=False)
    monkeypatch.delenv(DEPLOY_ENV_VAR, raising=False)
    monkeypatch.delenv("GRADER_ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", value)
    assert redaction_mode() == MODE_LOCAL


@pytest.mark.parametrize("value", ["dev", "staging", "preview", "test", ""])
def test_every_non_production_environment_stays_off(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from vicary.redaction import MODE_OFF, redaction_mode

    monkeypatch.delenv(REDACTION_ENV_VAR, raising=False)
    monkeypatch.delenv(DEPLOY_ENV_VAR, raising=False)
    monkeypatch.delenv("GRADER_ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", value)
    assert redaction_mode() == MODE_OFF


def test_an_unset_environment_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production is a positive assertion. Inferring it from a missing variable
    would turn a config gap into a hard ValueError at construction."""
    from vicary.redaction import MODE_OFF, redaction_mode

    monkeypatch.delenv(REDACTION_ENV_VAR, raising=False)
    monkeypatch.delenv(DEPLOY_ENV_VAR, raising=False)
    monkeypatch.delenv("GRADER_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert redaction_mode() == MODE_OFF


def test_an_explicit_off_beats_the_production_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vicary.redaction import MODE_OFF, redaction_mode

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(REDACTION_ENV_VAR, "off")
    assert redaction_mode() == MODE_OFF


def test_stub_mode_still_wins_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So a prod-shaped integration run can exercise the path without billing."""
    from vicary.redaction import MODE_STUB, redaction_mode

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(REDACTION_ENV_VAR, "stub")
    assert redaction_mode() == MODE_STUB


def test_the_librarys_own_deploy_var_beats_the_host_convention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vicary.redaction import MODE_LOCAL, redaction_mode

    monkeypatch.delenv(REDACTION_ENV_VAR, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv(DEPLOY_ENV_VAR, "production")
    assert redaction_mode() == MODE_LOCAL


def test_an_inscrutable_flag_is_still_off_even_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vicary.redaction import MODE_OFF, redaction_mode

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(REDACTION_ENV_VAR, "banana")
    assert redaction_mode() == MODE_OFF
