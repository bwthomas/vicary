"""The redaction seam: one object a host pipeline calls on the way in and out.

This is the module a consuming application imports. It is a *config-gated seam*:
with nothing configured it does nothing, and the host runs byte-for-byte as it
did before it was wired in. When enabled, student text is scrubbed before it is
sent to any model provider, and generated text is scrubbed again on the way back
out — so a name a model echoes never reaches a caller.

Four modes
----------
  * ``off``       — inert. The default with nothing configured.
  * ``local``     — the offline detector in :mod:`vicary.local_classifier`:
                    regex for structured identifiers, plus name candidates
                    (:mod:`vicary.name_candidates`) filtered against the offline
                    notability gazetteer (:mod:`vicary.gazetteer`). Free, no
                    network, no cloud resource. **This is the recommended
                    production mode** and the one the eval gates measure.
  * ``stub``      — a deterministic regex-only redactor that masks structured
                    identifiers (EMAIL, PHONE, SSN, card, IP) and nothing else.
                    Its purpose is to exercise the whole code path — masking →
                    threading masked text through the host's stages → outbound
                    scrub → cost folding — without spend or a cloud dependency.
                    It cannot mask names, so it is a wiring test, never a
                    privacy control.
  * ``guardrail`` — an **AWS Bedrock Guardrail** ``ApplyGuardrail`` pass, via
                    managed entity detection with the ``ANONYMIZE`` action.
                    Requires ``vicary[bedrock]``, a provisioned Guardrail
                    (:mod:`vicary.bedrock.guardrail`), and it is billed. It ships
                    mainly as the external comparison arm the eval scores
                    ``local`` against — a library whose only benchmark is itself
                    has no benchmark.

Resolution order (the configurable-default principle)
-----------------------------------------------------
Four layers, most-specific wins:

  1. **Call-site argument** — whatever the host passes down to
     :func:`build_redactor_if_enabled`: ``True`` → ``local``, ``False`` → ``off``,
     or the mode string directly. Overrides the environment.
  2. **Environment** — ``VICARY_REDACTION``: ``stub``/``simulate`` → stub;
     ``1/true/yes/on/local/classifier`` → local; ``guardrail``/``bedrock``/``aws``
     → guardrail; ``0/false/no/off`` → off. An explicit ``off`` here beats the
     per-environment default below, on purpose: a default that could override
     what an operator typed is undebuggable.
  3. **Deployment environment** — with the var unset, a
     ``VICARY_DEPLOY_ENV``/``ENVIRONMENT`` naming production resolves to
     ``local``. Everything else, unset included, stays ``off``. See
     :func:`vicary.config.deployment_is_production` for why that direction is
     the safe one.
  4. **Code default** — ``off``. A no-config install changes nothing.

Every name read here is declared in :mod:`vicary.config`, which also accepts the
legacy ``GRADER_*`` spellings at lower precedence.

A note on cost, for hosts that fold redaction into a cost model
---------------------------------------------------------------
Redaction **changes downstream token counts**: masking ``Jane Quincy-Adams`` to
a placeholder shrinks (or, for verbose placeholders, grows) the prompt the
downstream model sees, so a host billing provider-reported tokens is already
correct. What a host must add is the redactor's *own* cost, which is zero in
``local`` and ``stub`` mode and non-zero in ``guardrail`` mode.

``ApplyGuardrail`` bills per "text unit" of 1000 characters **rounded up, per
call**, which makes the call *count* the cost driver rather than the character
count. Measured over a 523-essay capture whose final stage emitted 7.26
free-text fields per essay totalling 2,186 characters: one call per field bills
7.260 units against 2.790 for a single joined call — a 2.6x overcharge for
identical text. :meth:`Redactor.redact_outbound_batch` is the fix and the
outbound path uses it; keep any new outbound field inside that one call rather
than adding a second one.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from vicary import config
from vicary.local_classifier import LocalNameClassifier, StudentIdentity
from vicary.name_candidates import (
    GivenNameOracle,
    NotabilityOracle,
    NotabilityTierOracle,
    TitleOracle,
    established_name_tokens,
    find_candidates,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config resolution (configurable-default principle).
# ---------------------------------------------------------------------------

#: Re-exported from :mod:`vicary.config`, which owns every name this library
#: reads and the legacy-spelling fallback for each. Kept as module attributes
#: because they are the natural handle for a test that wants to set one.
REDACTION_ENV_VAR: str = config.REDACTION_ENV_VAR
GUARDRAIL_ID_ENV_VAR: str = config.GUARDRAIL_ID_ENV_VAR
GUARDRAIL_VERSION_ENV_VAR: str = config.GUARDRAIL_VERSION_ENV_VAR

# Default version when a Guardrail ID is configured but no version is given.
# "DRAFT" is the working version every Bedrock Guardrail has; operators pin a
# published numeric version ("1", "2", …) for prod.
_DEFAULT_GUARDRAIL_VERSION: str = "DRAFT"


# Redaction modes.
MODE_OFF: str = "off"
MODE_STUB: str = "stub"
MODE_GUARDRAIL: str = "guardrail"
#: Local regex + interpolated-identity classifier. Free, offline, and the
#: production default since 2026-08-04 (see src/vicary/local_classifier.py).
MODE_LOCAL: str = "local"

_STUB_ALIASES = {"stub", "simulate", "sim"}
_GUARDRAIL_ALIASES = {"guardrail", "bedrock", "aws"}
_LOCAL_ALIASES = {"1", "true", "yes", "on", "local", "classifier"}
_OFF_ALIASES = {"", "0", "false", "no", "off"}


def redaction_mode(kwarg_value: bool | str | None = None) -> str:
    """Resolve the redaction mode. See the module docstring for the full ladder.

    Returns ``MODE_OFF`` / ``MODE_STUB`` / ``MODE_LOCAL`` / ``MODE_GUARDRAIL``.

      1. Explicit ``kwarg_value`` — the host's call-site override: ``True`` →
         local, ``False`` → off, or a mode string.
      2. ``VICARY_REDACTION`` (legacy ``GRADER_PII_REDACTION``).
      3. A production deployment environment → local.
      4. Code default: **off**.

    An unrecognized non-empty value resolves to ``off``. That is fail-*safe*
    rather than fail-closed, and it is the deliberate choice: a typo'd flag makes
    the host behave exactly as it did before redaction was wired in, whereas
    guessing a mode from an inscrutable value could enable a billed cloud call
    nobody asked for.
    """
    if kwarg_value is not None:
        if isinstance(kwarg_value, bool):
            return MODE_LOCAL if kwarg_value else MODE_OFF
        raw = kwarg_value.strip().lower()
    else:
        raw = config.get(REDACTION_ENV_VAR).lower()
    if raw in _STUB_ALIASES:
        return MODE_STUB
    if raw in _GUARDRAIL_ALIASES:
        return MODE_GUARDRAIL
    if raw in _LOCAL_ALIASES:
        return MODE_LOCAL
    if raw in _OFF_ALIASES and raw != "":
        # An EXPLICIT off wins over the environment default. Someone who typed
        # `VICARY_REDACTION=0` in production means it, and a per-env default that
        # overrode an explicit setting would be undebuggable.
        return MODE_OFF
    if raw == "" and deployment_is_production():
        return MODE_LOCAL
    return MODE_OFF


# Deployment-environment resolution lives in :mod:`vicary.config` — including
# which names mean production, which a host extends with
# ``config.add_production_alias()`` rather than by patching a literal here.
deployment_environment = config.deployment_environment
deployment_is_production = config.deployment_is_production


def redaction_enabled(kwarg_value: bool | str | None = None) -> bool:
    """Whether redaction runs at all (any mode other than ``off``).

    Thin back-compat wrapper over :func:`redaction_mode`.
    """
    return redaction_mode(kwarg_value) != MODE_OFF


# How hard ``local`` mode looks for names nobody told it about.
#
#: Structured entities plus the student's own interpolated name and school, and
#: nothing else. **This finds 0% of third-party names** — a classmate, a
#: teacher, a neighbour — because it has no way to tell one from a public figure
#: without a lookup. It is the honest floor, not a safe setting.
NAMES_IDENTITY: str = "identity"
#: Adds candidate generation filtered by the bundled offline gazetteer: public
#: figures, places, published works and fictional characters are KEPT, everything
#: else name-shaped is redacted.
NAMES_GAZETTEER: str = "gazetteer"
#: Adds the case-insensitive route, the only one that reaches a writer who does
#: not capitalise. Also enables a precision filter on the capitalised route, so
#: it is not purely additive — see the measurement in the package README.
NAMES_LOWERCASE: str = "gazetteer-lowercase"

_NAMES_IDENTITY_ALIASES = {"identity", "off", "none", "0", "false", "no"}
_NAMES_GAZETTEER_ALIASES = {"gazetteer", "on", "1", "true", "yes", "names"}
_NAMES_LOWERCASE_ALIASES = {
    "gazetteer-lowercase", "lowercase", "gazetteer_lowercase", "full",
}

#: Code default for :func:`name_detection`.
#:
#: **``gazetteer-lowercase``, set 2026-08-06**, from a three-way comparison run
#: through :func:`build_redactor_if_enabled` itself — not through a redactor a
#: harness assembled. Inbound: 25 first-in-file-order ASAP set-8 essays at
#: fixture 2026-08-05.6 (:mod:`vicary.eval.recall`, ``--modes path-*``).
#: Outbound: 14 real generated responses / 121 shown-to-the-reader fields
#: (:mod:`vicary.eval.overfire`), rate per response.
#:
#: ===========================  ==========  ==========  ====================
#: axis                          identity    gazetteer   gazetteer-lowercase
#: ===========================  ==========  ==========  ====================
#: held-out third-party recall       0.0%       90.5%                 100.0%
#: KEEP precision                  100.0%       93.5%                 100.0%
#: over-fire, inbound prose           0.00        3.36                   1.20
#: over-fire, outbound response       0.00        1.21                   0.57
#: leaks over 25 essays                 45           4                      2
#: latency p95                      0.7 ms      3.2 ms                 4.4 ms
#: ===========================  ==========  ==========  ====================
#:
#: ``gazetteer-lowercase`` is not a trade-off against ``gazetteer`` — it wins on
#: every axis except 1.2 ms. Passing the given-name oracle does two things, and
#: only one of them is the case-insensitive route: it also gates a precision
#: filter that drops a candidate whose only evidence is a sentence-initial
#: capital. Without it, outbound text loses the first word of its own imperative
#: sentences (``Double``-check, ``Push`` this further, ``Reread`` the opening —
#: 12 of that arm's 17 outbound over-fires).
#:
#: **The outbound row reverses if you score the wrong field set**, which is why
#: the harness makes the field list explicit. Over the explanatory field alone
#: the two levels run 6 spans to 8 — ``gazetteer`` ahead. Add the imperative
#: suggestion field, which the same host redacts in the same call, and it goes
#: 17 to 8 the other way. The imperatives are the entire effect.
#:
#: ``identity``'s clean over-fire columns are not a virtue. It finds **none** of
#: the third-party names — the classmate, the teacher, the neighbour — because
#: it has no lookup to tell one from a public figure, and it is the level every
#: deployment silently ran until this dial existed.
#:
#: Two things this measurement did NOT settle, both recorded rather than
#: smoothed over. The residual outbound over-fire is 0.25 spans/essay of
#: student-visible feedback (``line makes``, ``tone toward``) plus two
#: keep-destroyed spans the gazetteer should have caught (``Narciso``,
#: ``Cuban``); and the inbound over-fire figure is a FLOOR, because ASAP replaced
#: every proper noun with a placeholder before we ever saw it.
DEFAULT_NAME_DETECTION: str = NAMES_LOWERCASE


def name_detection(kwarg_value: str | None = None) -> str:
    """Resolve how hard ``local`` mode looks for names. Three levels.

    Returns :data:`NAMES_IDENTITY`, :data:`NAMES_GAZETTEER` or
    :data:`NAMES_LOWERCASE`.

      1. Explicit ``kwarg_value`` — the host's call-site override.
      2. ``VICARY_NAME_DETECTION``.
      3. Code default: :data:`DEFAULT_NAME_DETECTION`.

    An unrecognized non-empty value resolves to the **default**, not to
    ``identity``. That is the opposite of :func:`redaction_mode`'s fail-safe, and
    deliberately so: there, a typo makes the host behave as it did before
    redaction existed, which is a recoverable non-event. Here, silently dropping
    to ``identity`` would leave redaction *on* and reporting spans while finding
    none of the names a reader would call PII — a failure that looks exactly like
    success from every log line and metric.
    """
    raw = (kwarg_value or config.get(config.NAME_DETECTION_ENV_VAR)).strip().lower()
    if raw in _NAMES_IDENTITY_ALIASES and raw != "":
        return NAMES_IDENTITY
    if raw in _NAMES_GAZETTEER_ALIASES:
        return NAMES_GAZETTEER
    if raw in _NAMES_LOWERCASE_ALIASES:
        return NAMES_LOWERCASE
    if raw:
        logger.warning(
            "%s=%r is not a recognized level (%s); using the default %r.",
            config.NAME_DETECTION_ENV_VAR, raw,
            "/".join((NAMES_IDENTITY, NAMES_GAZETTEER, NAMES_LOWERCASE)),
            DEFAULT_NAME_DETECTION,
        )
    return DEFAULT_NAME_DETECTION


def name_detection_outbound(
    kwarg_value: str | None = None, *, inbound: str | None = None
) -> str:
    """Resolve the OUTBOUND detection level. Defaults to whatever inbound is.

    Same three levels and the same fail-to-the-default rule as
    :func:`name_detection`; the only difference is the fallback. Unset, outbound
    inherits inbound, so adding this dial changes nothing until somebody sets it.

    It exists because the two directions are not the same decision. Inbound, an
    over-redaction costs a placeholder in text a model already reads as
    ``@PERSON1`` — the ASAP training distribution is *entirely* placeholders — so
    recall is what to buy. Outbound, the same over-redaction is a hole in
    feedback a student reads, and the residual is real: 0.57 spans per response
    of student-visible text at the current default. One setting served both, so
    tightening the direction that needs it meant loosening the one that does not.
    """
    raw = (kwarg_value or config.get(config.NAME_DETECTION_OUTBOUND_ENV_VAR)).strip()
    if not raw:
        return inbound if inbound is not None else name_detection()
    return name_detection(raw)


def guardrail_identifier() -> str | None:
    """Configured Bedrock Guardrail ID from env, or ``None`` if unset."""
    return config.get(GUARDRAIL_ID_ENV_VAR) or None


def guardrail_version() -> str:
    """Configured Guardrail version from env, defaulting to ``DRAFT``."""
    return config.get(GUARDRAIL_VERSION_ENV_VAR) or _DEFAULT_GUARDRAIL_VERSION


# ---------------------------------------------------------------------------
# Guardrails client seam — a Protocol so tests inject a fake (no live AWS).
# ---------------------------------------------------------------------------


class GuardrailsClient(Protocol):
    """Minimal surface the redactor needs from a Bedrock Guardrails client.

    Mirrors ``boto3.client("bedrock-runtime").apply_guardrail`` — the one call
    we make. Declaring it as a Protocol lets unit tests pass a fake that returns
    canned ``apply_guardrail`` payloads without touching AWS.
    """

    def apply_guardrail(
        self,
        *,
        guardrailIdentifier: str,
        guardrailVersion: str,
        source: str,
        content: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


def _default_client() -> GuardrailsClient:
    """Build the real Bedrock runtime client lazily.

    Imported inside the function so boto3 is only needed when ``guardrail`` mode
    is actually used — keeps the default path free of an AWS dependency at import
    time and keeps unit tests fully offline. Install ``vicary[bedrock]`` to get
    it.
    """
    import boto3

    # Region comes from configuration, NOT a literal, and there is deliberately
    # no fallback region here. A hardcoded one made every guardrail-mode call
    # raise ``ValidationException: The guardrail identifier or version provided
    # in the request does not exist`` against a Guardrail that lived in another
    # region — a cross-region lookup, reported as if the resource were missing.
    # A library cannot know which region a caller's Guardrail is in, so an unset
    # region resolves through boto3's own chain and raises ``NoRegionError``,
    # which says what is wrong.
    return boto3.client(
        "bedrock-runtime",
        region_name=config.get(config.GUARDRAIL_REGION_ENV_VAR) or None,
    )


# ---------------------------------------------------------------------------
# Offline stub client — the non-prod testing seam (mode ``stub``).
# ---------------------------------------------------------------------------

# Structured-PII patterns the stub can mask deterministically without a model.
# Ordered: the most specific / longest-digit patterns first so a card number
# is not partially consumed as a phone number. Placeholders mirror the Bedrock
# Guardrail entity-type tokens (``{NAME}`` etc.) so downstream text looks the
# same shape it will in prod. NAME/ADDRESS/AGE are intentionally absent — they
# need the model, and the stub never claims to cover them (see module docstring).
_STUB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("{EMAIL}", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    (
        "{US_SOCIAL_SECURITY_NUMBER}",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "{CREDIT_DEBIT_CARD_NUMBER}",
        re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    ),
    (
        "{PHONE}",
        re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    ),
    ("{IP_ADDRESS}", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
)


class _StubGuardrailsClient:
    """A zero-cost, offline stand-in for the Bedrock Guardrails client.

    Implements the same ``apply_guardrail`` surface but masks structured PII
    with local regex — no AWS call, no spend, no Guardrail resource required.
    Deterministic (regex-only) so dev runs and tests are reproducible. Returns
    ``GUARDRAIL_INTERVENED`` iff it changed the text, mirroring the real client.
    """

    def apply_guardrail(
        self,
        *,
        guardrailIdentifier: str,
        guardrailVersion: str,
        source: str,
        content: list[dict[str, Any]],
    ) -> dict[str, Any]:
        text = content[0]["text"]["text"]
        masked = text
        for placeholder, pattern in _STUB_PATTERNS:
            masked = pattern.sub(placeholder, masked)
        action = "GUARDRAIL_INTERVENED" if masked != text else "NONE"
        return {"action": action, "outputs": [{"text": masked}]}


# ---------------------------------------------------------------------------
# Redactor.
# ---------------------------------------------------------------------------


#: Separator for the batched outbound pass. Chosen to be something a writing-coach
#: model will not emit and a PII policy will not touch: no words, no digits, no
#: name-shaped or address-shaped substrings for an entity detector to bite on, and
#: distinctive enough that a stray single character cannot fake it. A round-trip
#: check runs anyway — see :meth:`Redactor.redact_outbound_batch` — because a
#: separator that "should" survive is exactly the kind of assumption that silently
#: mis-aligns feedback fields.
BATCH_SEPARATOR: str = "\n␞␞␞\n"


@dataclass
class RedactionResult:
    """Outcome of one ``apply_guardrail`` pass.

    Attributes:
        text: The (possibly) masked text to use downstream. Equal to the input
            when the Guardrail made no changes.
        intervened: True when the Guardrail masked or blocked anything.
        char_units: Billed text-unit count (ceil(len/1000)) for this call —
            surfaced so the cost wiring can attribute Guardrail spend per the
            module docstring's WIRING NOTE.
    """

    text: str
    intervened: bool
    char_units: int


class Redactor:
    """Filters/redacts PII through a Bedrock Guardrail in both directions.

    Construct once per pipeline run and reuse for the inbound (student text)
    and outbound (generated feedback) passes. The ``source`` argument to
    ``ApplyGuardrail`` distinguishes the two: ``"INPUT"`` for student text
    headed to a provider, ``"OUTPUT"`` for model-generated text headed back to
    a caller.
    """

    def __init__(
        self,
        *,
        guardrail_id: str | None = None,
        version: str | None = None,
        client: GuardrailsClient | None = None,
        simulate: bool = False,
        local: bool = False,
        identity: StudentIdentity | None = None,
        local_candidates: bool = False,
        notable: NotabilityOracle | None = None,
        topical: frozenset[str] = frozenset(),
        given_name: GivenNameOracle | None = None,
        title: TitleOracle | None = None,
        title_prefix: TitleOracle | None = None,
        corroborate: bool = True,
        notability_tier: NotabilityTierOracle | None = None,
        number_placeholders: bool = True,
        headings_are_orthographic: bool = True,
        relation_refusal: bool = True,
        title_relation_refusal: bool = True,
        outbound_oracles: dict | None = None,
        carry_notable_keeps: bool = True,
    ) -> None:
        self.simulate = simulate
        self.local = local
        self.identity = identity
        self.carry_notable_keeps = carry_notable_keeps
        #: Bare tokens of the notable full names the inbound pass kept, so the
        #: outbound pass can recognise them. Populated by ``redact_inbound``.
        self._carried_keeps: frozenset[str] = frozenset()
        # ``local_candidates`` turns on third-party name detection. Off by
        # default: without a notability oracle it masks every public figure a
        # student writes about, which is a visible product defect outbound.
        self._classifier = (
            LocalNameClassifier(
                identity,
                candidates=local_candidates,
                notable=notable,
                topical=topical,
                given_name=given_name,
                title=title,
                title_prefix=title_prefix,
                corroborate=corroborate,
                notability_tier=notability_tier,
                number_placeholders=number_placeholders,
                headings_are_orthographic=headings_are_orthographic,
                relation_refusal=relation_refusal,
                title_relation_refusal=title_relation_refusal,
            )
            if local
            else None
        )
        # A second classifier for the outbound pass, when the host asked for a
        # different level there. Absent, outbound runs the inbound one and the
        # two directions are the same object — which is what shipped, and what
        # keeps this dial inert until it is set.
        self._outbound_classifier = (
            LocalNameClassifier(
                identity,
                # `identity` level supplies no oracles, and generation without
                # an oracle is the recall-maximal arm that masks every public
                # figure. So the level decides generation too, exactly as it
                # does inbound — hardcoding True here made "outbound=identity"
                # mask MORE than gazetteer-lowercase, which is the opposite of
                # what the name says.
                candidates=bool(outbound_oracles.get("local_candidates")),
                topical=topical,
                corroborate=corroborate,
                number_placeholders=number_placeholders,
                headings_are_orthographic=headings_are_orthographic,
                relation_refusal=relation_refusal,
                title_relation_refusal=title_relation_refusal,
                **{k: v for k, v in outbound_oracles.items()
                   if k != "local_candidates"},
            )
            if local and outbound_oracles is not None
            else None
        )
        if local:
            # No Guardrail resource, no AWS client, no spend. The classifier
            # does the masking in-process; _apply short-circuits to it.
            self.guardrail_id = "local-classifier"
            self._client: GuardrailsClient | None = None
            self.guardrail_version = version or "n/a"
            return
        if simulate:
            # Stub mode: no real Guardrail needed and nothing is billed. Use the
            # offline masker unless a test injects its own client.
            self.guardrail_id = guardrail_id or guardrail_identifier() or "stub-local"
            self._client = client or _StubGuardrailsClient()
        else:
            resolved_id = guardrail_id or guardrail_identifier()
            if not resolved_id:
                # Fail closed: enabling guardrail-mode redaction with no Guardrail
                # configured would otherwise either crash mid-flight or (worse)
                # silently no-op and ship student PII unredacted. Raise at
                # construction so the misconfiguration surfaces before any call.
                raise ValueError(
                    "guardrail-mode redaction is enabled but no Bedrock "
                    f"Guardrail is configured. Set {GUARDRAIL_ID_ENV_VAR} (and "
                    f"optionally {GUARDRAIL_VERSION_ENV_VAR}) or pass "
                    "guardrail_id=. This code creates no Guardrail resource; see "
                    "vicary.bedrock.guardrail to provision one."
                )
            self.guardrail_id = resolved_id
            self._client = client  # lazily built on first use when None
        self.guardrail_version = version or guardrail_version()

    def _ensure_client(self) -> GuardrailsClient:
        if self._client is None:
            self._client = _default_client()
        return self._client

    def _apply(self, text: str, *, source: str,
               outbound: bool = False) -> RedactionResult:
        if not text:
            return RedactionResult(text=text, intervened=False, char_units=0)
        # `source` cannot carry the direction: BOTH passes call ApplyGuardrail
        # with source="OUTPUT", because ANONYMIZE only masks there. So the leg of
        # OUR pipeline that is calling has to be passed separately, and used to
        # be unrepresented entirely.
        classifier = (self._outbound_classifier if outbound
                      and self._outbound_classifier is not None
                      else self._classifier)
        if classifier is not None:
            # Local mode: in-process, no network hop, zero billed units.
            result = classifier.mask(
                text, self._carried_keeps if outbound else frozenset()
            )
            if result.intervened:
                logger.info(
                    "Local PII classifier masked %d span(s) on %s.",
                    result.n_masked,
                    source,
                )
            return RedactionResult(
                text=result.text, intervened=result.intervened, char_units=0
            )
        client = self._ensure_client()
        response = client.apply_guardrail(
            guardrailIdentifier=self.guardrail_id,
            guardrailVersion=self.guardrail_version,
            source=source,
            content=[{"text": {"text": text}}],
        )
        masked = _extract_masked_text(response, fallback=text)
        action = response.get("action")
        intervened = action == "GUARDRAIL_INTERVENED"
        # Stub mode does the masking locally and pays nothing, so it bills zero
        # text-units — the cost fold-in (orchestrator) skips the line item and
        # dev ``cost_total_usd`` stays truthful (MUST #6). Guardrail mode bills
        # ceil(len/1000) units, matching what ``ApplyGuardrail`` charges.
        char_units = 0 if self.simulate else (len(text) + 999) // 1000
        if intervened:
            logger.info(
                "PII guardrail intervened on %s (%d char-units).",
                source,
                char_units,
            )
        return RedactionResult(
            text=masked, intervened=intervened, char_units=char_units
        )

    def redact_inbound(self, text: str) -> RedactionResult:
        """Scrub PII from student text before it is sent to a provider.

        **Calls with ``source="OUTPUT"`` even though this is the inbound pass,
        and that is not a typo.** ``ANONYMIZE`` masks only on ``OUTPUT``. On
        ``source="INPUT"`` Bedrock evaluates the policy and then does nothing:
        measured over 25 injected set-8 essays (75 known PII spans), INPUT
        returned ``action=NONE``, ``outputs=[]``, **0 billed units and 0 of 75
        spans redacted**, while the identical text and Guardrail on OUTPUT
        redacted **73 of 75 (97.3%)**. ``guardrailCoverage`` reported 207/207
        characters guarded in both cases, so nothing about the response
        distinguishes "clean text" from "silently unredacted" — and
        :func:`_extract_masked_text` falls back to the unmasked input on an empty
        ``outputs``, so the pipeline saw the raw essay and read it as scrubbed.
        For the inbound direction Bedrock's only INPUT-side action is ``BLOCK``,
        which for a scoring pipeline means the student gets nothing back at all.

        ``source`` is just a mode selector on ``ApplyGuardrail`` — the API has no
        notion of which leg of *our* pipeline is calling — so asking for the
        OUTPUT behaviour on inbound text is the supported way to get masking.

        Harness: ``python -m vicary.eval.recall``. Re-run it if this line
        changes; a 0% detector is invisible from every other vantage point.
        """
        if self.carry_notable_keeps and self._classifier is not None:
            self._carried_keeps = self.carried_keeps(text)
        return self._apply(text, source="OUTPUT")

    def carried_keeps(self, composition: str) -> frozenset[str]:
        """Bare tokens the outbound pass may keep, given what this essay named.

        **The defect this closes.** Stage-5 feedback about a memoir by Narciso
        Rodriguez reads "introducing who Narciso is". The essay writes the full
        name, the gazetteer keeps it, and the feedback writes only the first
        name — which is in the ``given`` tier, a *redact* signal, so a student
        reads "introducing who {NAME} is" about the author they just wrote about.
        Measured on 14 real Stage-5 responses, this was 2 of the 3 remaining
        over-fires at the shipped level.

        No lookup can fix it. The ``given`` tier is *built* from the first tokens
        of the full tier, so every entry in it heads some notable full name and a
        blanket "given names that head a notable name keep" rule empties the tier.
        Narciso Rodriguez is far below the short tier's 100-sitelink floor, so no
        threshold reaches him either. The evidence has to be per-document, and
        the document that has it is the *essay*, not the feedback.

        **Why this is safe, and the condition it depends on.** Outbound text is
        generated from the inbound-redacted composition, so a name the inbound
        pass masked never reached the model and cannot come back in its output.
        The only "Narciso" that can appear in feedback about this essay is one
        the inbound pass kept. That makes carrying a *first* name sound here
        where :func:`surname_forms` rightly refuses to carry one inbound.

        Two subtractions, because the argument above has two holes:

        * **The student's own name.** Identity masking is exact-match, not
          gazetteer-driven, so a student named Narciso is masked inbound without
          ever entering the notable set — and would then be un-masked outbound by
          a token carried from the designer. Identity tokens are removed.
        * **A second, private full name sharing the token.** A document naming
          both Narciso Rodriguez and a cousin Narciso Delgado establishes the
          token in two roles at once; the feedback's bare "Narciso" is then
          genuinely ambiguous, so it keeps neither.

        The second subtraction counts only **multi-token** non-notable
        candidates, and getting that wrong is what made the first version of this
        measure exactly zero. Scored over every candidate, the essay's own bare
        "Narciso" — not notable on its own, which is the entire premise — was
        read as evidence of a private person and cancelled the keep it was meant
        to license. A bare mention is the *symptom*; only a competing full name
        is *evidence*.

        Strictly, neither subtraction is load-bearing given the paragraph above —
        a private name is masked inbound and so cannot be echoed at all. They are
        kept because they are nearly free and they still hold if a host ever
        calls the legs out of order.

        Returns an empty set when the redactor has no notability oracle — there
        is nothing to establish from — which is what keeps this inert for a host
        running the identity-only level.
        """
        classifier = self._classifier
        if classifier is None or classifier.notable is None:
            return frozenset()
        established = established_name_tokens(
            composition,
            classifier.notable,
            classifier.topical,
            tier=classifier.notability_tier,
        )
        if not established:
            return frozenset()
        private = {
            token
            for candidate in find_candidates(composition)
            if len(candidate.text.split()) > 1
            and not classifier.notable(candidate.text)
            for token in candidate.text.lower().split()
        }
        identity = self.identity
        if identity is not None:
            for value in (identity.first_name, identity.last_name,
                          *identity.extra_names):
                if value:
                    private.update(value.lower().split())
        return frozenset(established - private)

    def redact_outbound(self, text: str) -> RedactionResult:
        """Scrub PII a model may have echoed before returning it to a caller."""
        return self._apply(text, source="OUTPUT", outbound=True)

    def redact_outbound_batch(
        self, texts: Sequence[str]
    ) -> tuple[list[str], int, bool]:
        """Scrub several fields in ONE ``ApplyGuardrail`` call.

        Returns ``(masked_texts, char_units, batched)``, positionally aligned with
        ``texts``.

        **Why this exists — it is a pure billing fix, measured.** ``ApplyGuardrail``
        bills ``ceil(len/1000)`` text units, so **every short field rounds up to a
        full 1000-char unit**. Measured over the 523-essay set-8 capture, Stage 5
        emits **7.26 free-text fields per essay totalling 2,186 chars**: billed
        per field that is 7.260 units, billed as one joined call it is 2.790 —
        **$0.001093 → $0.000646 per essay, $1,968 → $1,163/yr at 1.8M essays**, for
        identical text in and identical text out. Nothing about detection quality
        changes; the Guardrail sees the same characters either way.

        **Where it can go wrong, and what happens then.** The join/split round trip
        is the risk: masking changes lengths, so offsets cannot be trusted, and the
        split relies on :data:`BATCH_SEPARATOR` surviving the pass intact. If the
        response does not split back into exactly ``len(texts)`` parts, this method
        **falls back to per-field calls and says so** (``batched=False``) rather
        than returning a mis-aligned list — a suggestion pasted into the wrong
        glow is a worse outcome than paying $805/yr. The fallback costs the extra
        units for that essay only.
        """
        if not texts:
            return [], 0, True
        nonempty = [i for i, t in enumerate(texts) if t]
        if not nonempty:
            return list(texts), 0, True
        if len(nonempty) == 1:
            only = self._apply(texts[nonempty[0]], source="OUTPUT")
            out = list(texts)
            out[nonempty[0]] = only.text
            return out, only.char_units, True

        joined = BATCH_SEPARATOR.join(texts[i] for i in nonempty)
        result = self._apply(joined, source="OUTPUT")
        parts = result.text.split(BATCH_SEPARATOR)
        if len(parts) != len(nonempty):
            logger.warning(
                "PII outbound batch did not round-trip (%d fields in, %d parts "
                "back); falling back to per-field calls for this essay. The "
                "separator was altered by the Guardrail or present in the "
                "feedback text.",
                len(nonempty), len(parts),
            )
            out = list(texts)
            units = result.char_units      # the batch call was already billed
            for i in nonempty:
                single = self._apply(texts[i], source="OUTPUT")
                out[i] = single.text
                units += single.char_units
            return out, units, False

        out = list(texts)
        for i, part in zip(nonempty, parts, strict=True):  # equal by the guard above
            out[i] = part
        return out, result.char_units, True


def _extract_masked_text(response: dict[str, Any], *, fallback: str) -> str:
    """Pull the masked text out of an ``apply_guardrail`` response.

    The response carries an ``outputs`` list of ``{"text": "..."}`` items; when
    the Guardrail anonymizes PII the masked text appears there. When the
    Guardrail made no changes ``outputs`` may be empty — fall back to the input.
    """
    outputs = response.get("outputs") or []
    parts = [o.get("text", "") for o in outputs if isinstance(o, dict)]
    joined = "".join(p for p in parts if p)
    return joined or fallback


def _gazetteer_oracles(level: str) -> dict:
    """Keyword arguments that wire the bundled gazetteer into ``local`` mode.

    Imported lazily: a host running at :data:`NAMES_IDENTITY` never touches the
    2.1 MB asset, and a host that is not redacting at all never imports the
    gazetteer module. At the other two levels the first lookup pays the
    decompression; call :func:`vicary.gazetteer.load` at container init to move
    that off the first request.
    """
    if level == NAMES_IDENTITY:
        return {}
    from vicary.gazetteer import (
        is_common_given_name,
        is_notable,
        is_title,
        is_title_prefix,
        notability,
    )

    return {
        # Generation and the oracle are ONE decision, not two. Generation alone
        # masks every public figure a student writes about; the oracle alone has
        # nothing to judge. There is no supported way to ask for half of this.
        "local_candidates": True,
        "notable": is_notable,
        "notability_tier": notability,
        "title": is_title,
        "title_prefix": is_title_prefix,
        # The one difference between the two gazetteer levels.
        "given_name": (
            is_common_given_name if level == NAMES_LOWERCASE else None
        ),
    }


def build_redactor_if_enabled(
    kwarg_value: bool | str | None = None,
    *,
    client: GuardrailsClient | None = None,
    identity: StudentIdentity | None = None,
    names: str | None = None,
    names_outbound: str | None = None,
) -> Redactor | None:
    """Return a :class:`Redactor` for the resolved mode, or ``None`` (off).

    The single entry point a host calls. ``None`` means the seam is inert and
    the caller proceeds unchanged.

    * ``local`` (the production default) — the in-process offline classifier.
      Free, no network hop. ``identity`` supplies the writer's own name and
      school; without it the classifier still masks every structured entity,
      just not the names.
    * ``stub`` — the older offline masker, kept for the dev seam.
    * ``guardrail`` — the real billed Bedrock pass. Raises when enabled without
      a configured Guardrail ID: fail closed rather than ship PII.

    ``names`` selects how hard ``local`` mode looks for names it was not handed
    — see :func:`name_detection`. It is a second dial rather than a fourth mode
    because it is orthogonal to *where* redaction runs, and because the two
    questions had been silently answered together: this function used to build
    the identity-only classifier unconditionally, so every deployment that
    turned redaction "on" got a detector with **0% recall on third-party names**
    and no signal anywhere that it had.
    """
    mode = redaction_mode(kwarg_value)
    if mode == MODE_OFF:
        return None
    if mode == MODE_LOCAL:
        level = name_detection(names)
        outbound = name_detection_outbound(names_outbound, inbound=level)
        return Redactor(
            local=True, identity=identity, **_gazetteer_oracles(level),
            # None, not an equal dict: identical levels must share ONE classifier
            # rather than build two that behave the same. Two objects that agree
            # today are two objects that can stop agreeing, and the outbound
            # number would move with nothing in the config to explain it.
            outbound_oracles=(_gazetteer_oracles(outbound)
                              if outbound != level else None),
        )
    if mode == MODE_STUB:
        return Redactor(client=client, simulate=True)
    return Redactor(client=client)
