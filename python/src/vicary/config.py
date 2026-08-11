"""Environment configuration — one module, one precedence order.

Every environment variable vicary reads is declared here, resolved here, and
tested here. Seven of them used to be spelled ``GRADER_*``, in the namespace of
the application that happened to host the code first. A library reading its
host's namespace is a library that cannot be embedded twice, so the names moved
into vicary's own — and the old ones stay readable at lower precedence, because
a rename that breaks a running deployment is not a rename, it is an outage.

Precedence, most-specific first:

  1. the ``VICARY_*`` name;
  2. the legacy ``GRADER_*`` name, which logs a deprecation **once per process**
     naming its replacement;
  3. any generic host-app fallback (only ``ENVIRONMENT`` and the ``AWS_*``
     region vars, which are conventions a library should honour rather than
     names a library owns — these are *not* deprecated);
  4. the default passed by the caller.

An empty string counts as unset throughout: a var exported as ``""`` by a shell
wrapper is indistinguishable from an absent one, and treating them differently
produces bugs nobody can reproduce.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: Prefix every name this library owns carries.
VAR_PREFIX = "VICARY_"

# ---------------------------------------------------------------------------
# The names.
# ---------------------------------------------------------------------------

#: Redaction mode: ``off`` / ``stub`` / ``local`` / ``guardrail``.
REDACTION_ENV_VAR: str = "VICARY_REDACTION"

#: How hard ``local`` mode looks for names it was not told about. See
#: :func:`vicary.redaction.name_detection` for the three levels and what each
#: one measures. This is a *separate* dial from :data:`REDACTION_ENV_VAR`,
#: because "is redaction on" and "does it find third-party names" are separate
#: questions and conflating them is how a deployment ended up running redaction
#: at 0% recall on the only class of name it could not interpolate.
NAME_DETECTION_ENV_VAR: str = "VICARY_NAME_DETECTION"

#: The same dial for the OUTBOUND pass. Unset, outbound inherits the inbound
#: level, which is the behaviour that shipped and stays the default. It is a
#: separate name because the two directions have genuinely different error costs
#: — inbound an over-redaction is a placeholder in a prompt the model reads as
#: ordinary input, outbound it is a hole in feedback a student reads — and one
#: setting cannot express both.
NAME_DETECTION_OUTBOUND_ENV_VAR: str = "VICARY_NAME_DETECTION_OUTBOUND"

#: Bedrock Guardrail wiring, used only by the optional ``guardrail`` mode.
GUARDRAIL_ID_ENV_VAR: str = "VICARY_BEDROCK_GUARDRAIL_ID"
GUARDRAIL_VERSION_ENV_VAR: str = "VICARY_BEDROCK_GUARDRAIL_VERSION"
GUARDRAIL_REGION_ENV_VAR: str = "VICARY_BEDROCK_GUARDRAIL_REGION"

#: Deployment environment name, which decides the per-environment default.
DEPLOY_ENV_VAR: str = "VICARY_DEPLOY_ENV"

#: Override for the bundled notability asset. See :mod:`vicary.assets`.
ASSET_PATH_ENV_VAR: str = "VICARY_ASSET_PATH"

#: Eval corpus location. The corpus is third-party essay data the operator
#: supplies and is deliberately NOT packaged; corpus-dependent gates skip when
#: it is absent.
EVAL_CORPUS_TSV_ENV_VAR: str = "VICARY_EVAL_CORPUS_TSV"
EVAL_CORPUS_DIR_ENV_VAR: str = "VICARY_EVAL_CORPUS_DIR"

#: Preferred filename inside :data:`EVAL_CORPUS_DIR_ENV_VAR`. Generic on
#: purpose: this library is not tied to one essay corpus, and baking a specific
#: third-party dataset's filename into a published artifact both named that
#: dataset and implied it was the only one that works. Any single ``.tsv`` in
#: the directory resolves too, so a directory laid out before this change still
#: works without renaming anything — see :func:`eval_corpus_tsv`.
EVAL_CORPUS_PREFERRED_FILENAME: str = "corpus.tsv"

#: Extension the directory scan accepts. The corpus reader parses TSV.
EVAL_CORPUS_SUFFIX: str = ".tsv"

#: Local copy of the US Census surname file (``.zip`` or extracted ``.csv``), for
#: the bare-surname false-positive control in :mod:`vicary.eval.census`. Not
#: packaged: it is 3 MB of data the redaction path never reads, and it changes on
#: the Census Bureau's schedule rather than ours.
EVAL_CENSUS_CSV_ENV_VAR: str = "VICARY_EVAL_CENSUS_CSV"

# The SSA baby-names archive path (``VICARY_BUILD_SSA_NAMES_ZIP``) used to be
# declared here. It is read at BUILD time only — the redaction path never touches
# it — so it moved to ``asset/vicary_build/config.py`` with the rest of the build
# mechanism. It was documented on the runtime library's configuration surface,
# where a host integrating the library would find it and reasonably expect it to
# matter.

#: Legacy spellings, accepted at lower precedence with a deprecation notice.
#: Keyed by the current name. Deleting an entry is a breaking change; adding one
#: is not.
LEGACY_NAMES: dict[str, tuple[str, ...]] = {
    REDACTION_ENV_VAR: ("GRADER_PII_REDACTION",),
    GUARDRAIL_ID_ENV_VAR: ("GRADER_PII_GUARDRAIL_ID",),
    GUARDRAIL_VERSION_ENV_VAR: ("GRADER_PII_GUARDRAIL_VERSION",),
    GUARDRAIL_REGION_ENV_VAR: ("GRADER_PII_GUARDRAIL_REGION",),
    DEPLOY_ENV_VAR: ("GRADER_ENV",),
    EVAL_CORPUS_TSV_ENV_VAR: ("GRADER_CORPUS_TSV",),
    EVAL_CORPUS_DIR_ENV_VAR: ("GRADER_CORPUS_REPO",),
}

#: Host-app conventions honoured after the legacy names. Not deprecated: these
#: are names the *deployment* owns, and a library that ignored them would make
#: every host set the same value twice.
HOST_FALLBACKS: dict[str, tuple[str, ...]] = {
    DEPLOY_ENV_VAR: ("ENVIRONMENT",),
    GUARDRAIL_REGION_ENV_VAR: ("AWS_DEFAULT_REGION", "AWS_REGION"),
}

#: Names already warned about, so a per-request code path does not emit a log
#: line per request. Process-scoped on purpose; tests clear it.
_warned: set[str] = set()


def _warn_once(legacy: str, current: str) -> None:
    if legacy in _warned:
        return
    _warned.add(legacy)
    logger.warning(
        "%s is deprecated and will stop being read in a future release; "
        "set %s instead", legacy, current,
    )


def reset_deprecation_warnings() -> None:
    """Forget which legacy names have been warned about. For tests."""
    _warned.clear()


def get(name: str, default: str = "") -> str:
    """Resolve one configured value, stripped, or ``default``.

    ``name`` must be one of the ``*_ENV_VAR`` constants above; passing an
    arbitrary string works but skips the legacy and host-fallback chains, which
    is almost never what a caller wants.
    """
    raw = (os.environ.get(name) or "").strip()
    if raw:
        return raw
    for legacy in LEGACY_NAMES.get(name, ()):
        raw = (os.environ.get(legacy) or "").strip()
        if raw:
            _warn_once(legacy, name)
            return raw
    for fallback in HOST_FALLBACKS.get(name, ()):
        raw = (os.environ.get(fallback) or "").strip()
        if raw:
            return raw
    return default


def names_for(name: str) -> tuple[str, ...]:
    """Every variable consulted for ``name``, in precedence order.

    Exists so an error message can tell an operator all the spellings that
    would have worked, and so a test can assert the chain without reaching into
    two dicts.
    """
    return (name, *LEGACY_NAMES.get(name, ()), *HOST_FALLBACKS.get(name, ()))


# ---------------------------------------------------------------------------
# Deployment environment.
# ---------------------------------------------------------------------------

#: Values that mean "this is the real thing". A set rather than a frozenset:
#: hosts name their production environment whatever they like, and patching a
#: library literal to add one is worse than calling
#: :func:`add_production_alias`.
PRODUCTION_ALIASES: set[str] = {"prod", "production", "live"}


def add_production_alias(*aliases: str) -> None:
    """Register additional environment names that mean production.

    A host whose production environment is called ``acme-prod`` calls this once
    at import time rather than editing :data:`PRODUCTION_ALIASES` in place.
    """
    PRODUCTION_ALIASES.update(a.strip().lower() for a in aliases if a.strip())


def deployment_environment() -> str:
    """The deployment environment name, lowercased, or ``""`` when unset."""
    return get(DEPLOY_ENV_VAR).lower()


def deployment_is_production() -> bool:
    """True when this process is running in production.

    **Fails toward "not production", deliberately**, even though redaction is
    the protective feature. The per-environment default turns redaction *on* in
    production, and the ``guardrail`` mode raises without a configured Guardrail
    — so inferring production from a missing variable would convert a config gap
    into a total failure of the host pipeline. Production is a named, positive
    assertion; nothing infers it.
    """
    return deployment_environment() in PRODUCTION_ALIASES


# ---------------------------------------------------------------------------
# Eval corpus.
# ---------------------------------------------------------------------------


def eval_corpus_tsv() -> str:
    """Path to the eval corpus TSV, or ``""`` when it is not configured.

    Callers must treat ``""`` as "skip this measurement", never as "the corpus
    is empty" — a corpus-dependent gate that silently passes on no data is a
    green light with a comment on it.

    ``""`` means **unconfigured**, and nothing else. A configured directory that
    does not resolve to exactly one TSV raises :class:`CorpusDirectoryError`
    instead of returning ``""``, because an operator who set the variable and
    got a skipped gate has been told their corpus is absent when it is in fact
    mis-named — the one failure this function must not report as a pass.

    Resolution inside a directory, in order:

      1. :data:`EVAL_CORPUS_PREFERRED_FILENAME`, if present;
      2. the single ``*.tsv`` in the directory, whatever it is called;
      3. otherwise raise, listing what was found.

    Rule 2 is what keeps a directory laid out under an older release working
    without a rename, and it is why no specific dataset's filename appears here.
    """
    explicit = get(EVAL_CORPUS_TSV_ENV_VAR)
    if explicit:
        return explicit
    directory = get(EVAL_CORPUS_DIR_ENV_VAR)
    if not directory:
        return ""
    return _resolve_corpus_in_directory(directory)


class CorpusDirectoryError(RuntimeError):
    """A configured corpus directory does not hold exactly one TSV."""


def _resolve_corpus_in_directory(directory: str) -> str:
    """The one corpus TSV in ``directory``, or raise saying why there isn't one."""
    preferred = os.path.join(directory, EVAL_CORPUS_PREFERRED_FILENAME)
    if os.path.isfile(preferred):
        return preferred
    try:
        entries = sorted(os.listdir(directory))
    except OSError as exc:
        raise CorpusDirectoryError(
            f"{EVAL_CORPUS_DIR_ENV_VAR}={directory!r} cannot be read ({exc}). "
            f"Point it at a directory, or name the file directly with "
            f"{EVAL_CORPUS_TSV_ENV_VAR}."
        ) from exc
    candidates = [
        name for name in entries
        if name.endswith(EVAL_CORPUS_SUFFIX)
        and os.path.isfile(os.path.join(directory, name))
    ]
    if len(candidates) == 1:
        return os.path.join(directory, candidates[0])
    found = ", ".join(candidates) if candidates else "none"
    raise CorpusDirectoryError(
        f"{EVAL_CORPUS_DIR_ENV_VAR}={directory!r} holds "
        f"{len(candidates)} {EVAL_CORPUS_SUFFIX} files ({found}); vicary cannot "
        f"tell which is the corpus. Name it "
        f"{EVAL_CORPUS_PREFERRED_FILENAME}, or set "
        f"{EVAL_CORPUS_TSV_ENV_VAR} to the file directly."
    )
