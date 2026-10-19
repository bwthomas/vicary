"""The host boundary: assert that no placeholder reaches a reader.

vicary masks what it redacts as typed placeholders — ``{NAME_1}``,
``{LOCATION_2}``, ``{EMAIL}`` — and a host restores the ones a reader may see.
Everything else must never be displayed. Two ways one gets out anyway, both seen
in a real host and both invisible to the masker:

- a model *echoes* a placeholder it read in masked text, and the restore map the
  host holds, numbered over a different pass, does not cover it;
- a model *paraphrases* one — ``[NAME_1]``, ``{Name_1}``, ``{ NAME_1 }``, a bare
  ``NAME_1`` — so a host looking for vicary's exact grammar does not see it.

A student was shown "How exactly does microplastic affect a specific {NAME_1}".
The fix belongs upstream, where the placeholder arose; this module is the layer
that holds when an upstream fix does not. It owns the grammar so that hosts stop
copying it:

* :func:`find_placeholders` / :func:`has_placeholders` — what is there;
* :func:`assert_no_placeholders` — raise :class:`PlaceholderLeakError` before
  the text is shown;
* :func:`scrub_placeholders` — remove them. A prose sentence carrying one is
  dropped whole; a quote of the student's own words elides it with ``…``, the way
  a quotation elides any omitted words. A placeholder is never rewritten back
  into a word here — there is no map and nothing is guessed.

**The pattern is one source, byte-identical in all three front doors.** It is
written in the dialect the three regex engines agree on — ASCII classes instead
of ``\\b``/``\\w``/``\\d``/``\\s``, case folding spelled out instead of an
inline ``(?i:…)`` that Node 20 cannot parse — and ``conformance/placeholders.json``
records the source text, so a port whose pattern drifts fails on the source
before it fails on a case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Every placeholder kind vicary or its Guardrail arm can mint: the name pass
#: (``NAME``, ``LOCATION``, ``ORGANIZATION``, ``SCHOOL``), the structured pass,
#: and every entity :data:`vicary.bedrock.guardrail.PII_ENTITIES` anonymises.
#: ``PERSON`` is not minted by vicary but is what a model writes when it
#: paraphrases ``NAME``. Longest first, so a match reports the whole kind.
PLACEHOLDER_KINDS: tuple[str, ...] = tuple(sorted(
    (
        "ADDRESS", "AGE", "AWS_ACCESS_KEY", "AWS_SECRET_KEY",
        "CREDIT_DEBIT_CARD_CVV", "CREDIT_DEBIT_CARD_EXPIRY",
        "CREDIT_DEBIT_CARD_NUMBER", "DATE_OF_BIRTH", "DRIVER_ID", "EMAIL",
        "IP_ADDRESS", "LICENSE_PLATE", "LOCATION", "MAC_ADDRESS", "NAME",
        "ORGANIZATION", "PASSWORD", "PERSON", "PHONE", "PIN", "SCHOOL", "URL",
        "US_BANK_ACCOUNT_NUMBER", "US_BANK_ROUTING_NUMBER", "US_PASSPORT_NUMBER",
        "US_SOCIAL_SECURITY_NUMBER", "USERNAME", "ZIP_CODE",
    ),
    key=lambda kind: (-len(kind), kind),
))

#: The kind reported for a ``{UPPER_CASE}`` token that names no known kind. Its
#: letters are text from the document, so they are not repeated back.
OTHER_KIND = "OTHER"


def _fold(kind: str) -> str:
    """``NAME`` -> ``[Nn][Aa][Mm][Ee]``: case-insensitive in every dialect."""
    return "".join(f"[{c}{c.lower()}]" if c.isalpha() else c for c in kind)


_WORD = "[A-Za-z0-9_]"

#: Anything placeholder-shaped, in three alternatives:
#:
#: 1. vicary's own grammar, ``{KIND}`` / ``{KIND_n}``, for any upper-case kind;
#: 2. a known kind in any case, in ``{}``, ``[]`` or ``<>``, with stray spaces
#:    or tabs inside the brackets — the shapes a paraphrase takes;
#: 3. a bare upper-case ``KIND_n``, numbered, as a whole word.
#:
#: Plain words are not placeholders: ``NAME``, ``Ms. Name`` and ``{x}`` do not
#: match. ``{name}`` does — in text bound for a reader it is a template that was
#: never filled, which is the same failure.
PLACEHOLDER_LIKE: re.Pattern[str] = re.compile(
    r"\{[A-Z][A-Z_]*(?:_[0-9]+)?\}"
    r"|[{\[<][ \t]*(?:" + "|".join(_fold(k) for k in PLACEHOLDER_KINDS)
    + r")(?:_[0-9]+)?[ \t]*[}\]>]"
    r"|(?<!" + _WORD + r")(?:" + "|".join(PLACEHOLDER_KINDS)
    + r")_[0-9]+(?!" + _WORD + r")"
)

#: A sentence ends at ``.``, ``!`` or ``?`` followed by whitespace — but not
#: after an honorific, an initial, ``e.g.``, ``i.e.`` or ``vs.``, so "Unlike
#: Ms. {NAME_1}, you…" is one sentence and goes whole rather than leaving
#: "Unlike Ms." behind. Erring toward a longer sentence drops more text, which is
#: the safe direction for a guard.
SENTENCE_END: re.Pattern[str] = re.compile(
    r"(?<![A-Z][a-z]\.)"
    r"(?<!(?<!" + _WORD + r")[A-Z][a-z][a-z]\.)"
    r"(?<!(?<!" + _WORD + r")[A-Z]\.)"
    r"(?<!(?<!" + _WORD + r")e\.g\.)"
    r"(?<!(?<!" + _WORD + r")i\.e\.)"
    r"(?<!(?<!" + _WORD + r")vs\.)"
    r"(?<=[.!?])[ \t\n\r\f\v]+"
)

_SPACE = " \t\n\r\f\v"
_ELISION = re.compile(r"[ \t\n\r\f\v]*…[ \t\n\r\f\v]*")
_ELISION_BEFORE_PUNCT = re.compile(r" … ([.,;:!?])")
_NUMBER_SUFFIX = re.compile(r"_[0-9]+$")

#: What stands in for a placeholder when every sentence of a prose field carries
#: one, so dropping sentences would leave nothing. Any other kind reads "this".
GENERIC_STAND_INS: dict[str, str] = {
    "NAME": "someone",
    "PERSON": "someone",
    "LOCATION": "a place",
    "SCHOOL": "the school",
    "ORGANIZATION": "an organization",
}


@dataclass(frozen=True)
class PlaceholderMatch:
    """One placeholder found in text: where it is and what kind it names.

    ``start``/``end`` count characters (code points), as Python slices do; the
    TypeScript port reports the same numbers, not UTF-16 code units.
    """

    text: str
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class ScrubResult:
    """Text with every placeholder removed, and how many were."""

    text: str
    removed: int


class PlaceholderLeakError(ValueError):
    """Text bound for a reader still carries a redaction placeholder.

    Names the kinds and the count, never the text around them: the message is
    going to a log, and the text is exactly what was not supposed to leave.
    """

    def __init__(self, kinds: tuple[str, ...], count: int) -> None:
        self.kinds = kinds
        self.count = count
        noun = "placeholder" if count == 1 else "placeholders"
        super().__init__(
            f"{count} redaction {noun} would reach a reader "
            f"(kinds: {', '.join(kinds)})"
        )


def placeholder_kind(token: str) -> str:
    """The kind a placeholder token names: ``[ Name_2 ]`` -> ``NAME``.

    A token naming no known kind reports :data:`OTHER_KIND`.
    """
    kind = _NUMBER_SUFFIX.sub("", token.strip("{}[]<>" + _SPACE).upper())
    return kind if kind in PLACEHOLDER_KINDS else OTHER_KIND


def find_placeholders(text: str) -> list[PlaceholderMatch]:
    """Every placeholder in ``text``, in order."""
    if not text:
        return []
    return [
        PlaceholderMatch(m.group(0), placeholder_kind(m.group(0)), m.start(), m.end())
        for m in PLACEHOLDER_LIKE.finditer(text)
    ]


def has_placeholders(text: str) -> bool:
    """Whether ``text`` carries any placeholder."""
    return bool(text) and PLACEHOLDER_LIKE.search(text) is not None


def assert_no_placeholders(text: str) -> str:
    """Return ``text`` unchanged, or raise :class:`PlaceholderLeakError`.

    Call it on the last string a host builds before a reader sees it.
    """
    found = find_placeholders(text)
    if found:
        raise PlaceholderLeakError(
            tuple(sorted({m.kind for m in found})), len(found)
        )
    return text


def _generic(match: re.Match[str]) -> str:
    return GENERIC_STAND_INS.get(placeholder_kind(match.group(0)), "this")


def scrub_placeholders(text: str, *, quote: bool = False) -> ScrubResult:
    """Remove every placeholder from ``text``.

    Prose (the default): each sentence carrying a placeholder is dropped and the
    rest are joined with single spaces. If every sentence carries one, the
    placeholders are replaced by :data:`GENERIC_STAND_INS` instead, since an
    empty field is a worse reply than a vague one.

    ``quote=True``, for a verbatim quote of the writer's own words: each
    placeholder is elided as ``…``, so the quote stays a quote.
    """
    if not has_placeholders(text):
        return ScrubResult(text, 0)
    removed = len(PLACEHOLDER_LIKE.findall(text))
    if quote:
        out = _ELISION.sub(" … ", PLACEHOLDER_LIKE.sub("…", text))
        out = _ELISION_BEFORE_PUNCT.sub(r" …\1", out)
        return ScrubResult(out.strip(_SPACE), removed)
    kept = [s for s in SENTENCE_END.split(text.strip(_SPACE))
            if not has_placeholders(s)]
    if kept:
        return ScrubResult(" ".join(kept), removed)
    return ScrubResult(PLACEHOLDER_LIKE.sub(_generic, text), removed)
