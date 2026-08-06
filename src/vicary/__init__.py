"""vicary — scrub personal names out of student compositions, offline.

The problem is narrower and harder than "detect PII". Structured identifiers —
email, phone, SSN, card numbers — are a solved regex exercise. Names are not,
because in English prose a classmate and a public figure are the same object: two
capitalised words.

    My cousin Terrence Okonkwo came over            -> redact
    My inspiration, Vincent van Gogh, painted ...   -> keep

No syntactic feature separates those, so the separation is a lookup: a bundled
offline gazetteer of public figures, places, published works and fictional
characters, consulted by candidate *shape*, combined with same-document evidence
about how the writer uses each name. No model, no network, no cloud resource, no
per-request cost.

Typical use, from a host pipeline::

    from vicary import StudentIdentity, build_redactor_if_enabled

    redactor = build_redactor_if_enabled(identity=StudentIdentity(...))
    if redactor:
        result = redactor.redact_inbound(essay_text)
        ...                                   # score result.text
        feedback = redactor.redact_outbound(feedback)

``build_redactor_if_enabled`` returns ``None`` when redaction is configured off,
which is the default, so wiring it in changes nothing until it is turned on. See
:mod:`vicary.redaction` for the modes and :mod:`vicary.config` for every
environment variable this library reads.

Redaction is reversible: a result carries a ``restore_map`` from placeholder back
to the original span, so a host that needs to show a student their own words can
put them back.

Subpackages, none of which the runtime path imports:

* :mod:`vicary.build`   — rebuild the gazetteer asset from its public upstreams.
* :mod:`vicary.eval`    — the measurement corpus, fixture, and scoring harness.
* :mod:`vicary.bedrock` — the optional AWS Bedrock Guardrail arm, which exists
  mainly so the offline detector has an external baseline to be scored against.
"""

from vicary._version import __version__
from vicary.local_classifier import (
    LocalNameClassifier,
    LocalRedactionResult,
    StudentIdentity,
)
from vicary.redaction import (
    MODE_GUARDRAIL,
    MODE_LOCAL,
    MODE_OFF,
    MODE_STUB,
    RedactionResult,
    Redactor,
    build_redactor_if_enabled,
    redaction_enabled,
    redaction_mode,
)

__all__ = [
    "LocalNameClassifier",
    "LocalRedactionResult",
    "MODE_GUARDRAIL",
    "MODE_LOCAL",
    "MODE_OFF",
    "MODE_STUB",
    "RedactionResult",
    "Redactor",
    "StudentIdentity",
    "__version__",
    "build_redactor_if_enabled",
    "redaction_enabled",
    "redaction_mode",
]
