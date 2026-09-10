"""Read a language-neutral word list from ``asset/lexicon/``.

The build's own reader for the authored lists under ``asset/lexicon/``. Every
front door ships a reader for the same format over its *vendored* copy — Python's
is :mod:`vicary.lexicon` — and the four are pinned together by
``asset/tests/test_lexicon.py``, which parses the same file with this reader and
with each front door's and compares the sets.

Two readers rather than one shared import for the same reason the whole directory
exists: the build tool must not import one of the three implementations it feeds.
The duplication is ~40 lines and a test makes it honest; the coupling would be
structural and permanent.
"""

from __future__ import annotations

from pathlib import Path

from vicary_build import config

#: On-disk format version. Must match :data:`vicary.lexicon.LEXICON_FORMAT` and
#: its equivalents; the pin test asserts it.
LEXICON_FORMAT = 1

SUFFIX = ".txt"


class LexiconError(RuntimeError):
    """A lexicon is missing, unparseable, or not the size it declares."""


def lexicon_path(name: str) -> Path:
    return config.LEXICON_DIR / f"{name}{SUFFIX}"


def names() -> list[str]:
    """Every lexicon in the source directory, for the sync step to vendor."""
    return sorted(p.stem for p in config.LEXICON_DIR.glob(f"*{SUFFIX}"))


def load(name: str, *, path: Path | None = None) -> frozenset[str]:
    """The case-folded distinct words of lexicon ``name``.

    The declared count is asserted rather than trusted. A short read makes every
    reader of this list *more* aggressive about what counts as a name, which looks
    privacy-safe, corrupts prose, and passes any check that only asks whether
    something was masked.
    """
    target = path or lexicon_path(name)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise LexiconError(f"cannot read lexicon at {target}: {exc}") from exc

    declared: int | None = None
    words: set[str] = set()
    saw_format = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#!"):
            parts = stripped[2:].split()
            if not parts:
                raise LexiconError(f"{target}:{lineno}: empty directive")
            if parts[0] == "lexicon":
                saw_format = True
                if len(parts) != 2 or parts[1] != str(LEXICON_FORMAT):
                    raise LexiconError(
                        f"{target}:{lineno}: lexicon format "
                        f"{' '.join(parts[1:])!r}, this build writes "
                        f"{LEXICON_FORMAT}"
                    )
            elif parts[0] == "list":
                if len(parts) != 3 or parts[1] != name:
                    raise LexiconError(
                        f"{target}:{lineno}: expected `#!list {name} <count>`, "
                        f"got {stripped!r}"
                    )
                declared = int(parts[2])
            else:
                raise LexiconError(
                    f"{target}:{lineno}: unknown directive {parts[0]!r}"
                )
            continue
        if not stripped or stripped.startswith("#"):
            continue
        words.update(word.lower() for word in stripped.split())

    if not saw_format:
        raise LexiconError(f"{target}: no `#!lexicon` directive")
    if declared is None:
        raise LexiconError(f"{target}: no `#!list {name} <count>` directive")
    if len(words) != declared:
        raise LexiconError(
            f"{target}: declares {declared} distinct words, parsed {len(words)}. "
            "Update the `#!list` count in the same edit that changes the words — "
            "the count is the only thing that catches a truncated read, and a "
            "truncated stoplist makes the redactor more aggressive rather than "
            "less."
        )
    return frozenset(words)


# ---------------------------------------------------------------------------
# Inflection, folded once here rather than in three runtime implementations
# ---------------------------------------------------------------------------

#: The line that opens the generated region of a lexicon source file, and the
#: line that closes it. Everything between them is rewritten by
#: :func:`rewrite`; everything above the opener is authored by hand.
GENERATED_BEGIN = "# >>> generated inflections — written by `python -m vicary_build lexicon`"
GENERATED_END = "# <<< end generated inflections"

#: How wide a generated word line may run before it wraps. Cosmetic, but pinned
#: so that a regeneration on another machine produces the same bytes.
_WRAP = 78

#: Two spaces, matching the authored groupings above.
_INDENT = "  "


def plural_forms(word: str) -> set[str]:
    """The bare plural of ``word``, by the three regular English rules.

    ``consonant + y`` takes ``ies``; a sibilant ending takes ``es``; everything
    else takes ``s``. One form per word, not a cross product — this list is a
    *veto* on becoming a name candidate, and every extra form it carries is one
    more capitalised token the redactor will decline to mask.

    Possessives are not here, and deliberately: they are folded at *runtime* by
    ``_without_clitic`` in each front door, before the lookup, so ``Nazi's``
    already reaches this list as ``nazi``. Emitting possessive forms as well
    would make inflection two mechanisms in two places that have to agree, which
    is the thing this function exists to stop being true of plurals.
    """
    if len(word) < 2 or not word.isalpha():
        return set()
    if word.endswith("y") and word[-2] not in "aeiou":
        return {word[:-1] + "ies"}
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return {word + "es"}
    return {word + "s"}


def inflections(words: frozenset[str] | set[str], *, veto: frozenset[str]
                ) -> list[str]:
    """Sorted plural forms to add to ``words``, minus anything in ``veto``.

    Over-generous on purpose, and it has to be: nothing here knows a noun from a
    conjunction, so ``because`` yields ``becauses``. That is the same bias the
    authored list already declares — a word that is never written costs nothing,
    where a missing stop word corrupts every essay using it. What the bias must
    not be allowed to do is swallow a *name*, which is what ``veto`` is for.

    ``veto`` is what keeps this from being a recall regression, and the two
    tables behind it are not interchangeable. A form that is a **borne surname**
    would put every family bearing it beyond the redactor's reach — unguarded,
    the fold over the shipped stoplist claims ``Mays``, ``Downs``, ``Wills`` and
    ``Peoples``. A form that is a **common given name** is worse and rarer: ``we``
    pluralises to ``wes``, and a stop word wins over the given-name tier, so
    without the veto a child called Wes stops being redacted for good.
    """
    words = set(words)
    derived = {form for word in words for form in plural_forms(word)}

    generated: set[str] = set()
    for word in words:
        # A plural of a plural is not a word. Without this the block carries
        # `dayses`, `brotherses` and `itses`, which cost nothing and are still
        # the kind of thing that makes a reader stop trusting a generated file.
        if word in derived:
            continue
        generated.update(plural_forms(word))
    return sorted(generated - words - set(veto))


def render_generated(forms: list[str]) -> list[str]:
    """The generated region's lines, wrapped, for ``forms``."""
    lines: list[str] = []
    current = _INDENT
    for form in forms:
        candidate = f"{current} {form}" if current != _INDENT else current + form
        if len(candidate) > _WRAP and current != _INDENT:
            lines.append(current)
            current = _INDENT + form
        else:
            current = candidate
    if current != _INDENT:
        lines.append(current)
    return lines


def _authored_head(text: str, *, target: Path) -> str:
    """Everything above the generated region, opener excluded.

    A source file with no generated region yet is accepted — that is a lexicon
    nobody has inflected. A file with an opener and no closer is not: the region
    has no end, so a rewrite would have to guess how much of the file to replace.
    """
    if GENERATED_BEGIN not in text:
        if GENERATED_END in text:
            raise LexiconError(
                f"{target}: closes a generated region it never opens. Restore "
                f"the {GENERATED_BEGIN!r} line or delete the closer."
            )
        return text.rstrip("\n") + "\n"
    head, _, rest = text.partition(GENERATED_BEGIN)
    if GENERATED_END not in rest:
        raise LexiconError(
            f"{target}: opens a generated region that is never closed. A "
            f"rewrite would have to guess where the authored words end."
        )
    return head.rstrip("\n") + "\n"


def _with_count(head: str, name: str, count: int, *, target: Path) -> str:
    """``head`` with its ``#!list`` count set to ``count``."""
    directive = f"#!list {name} {count}"
    lines = head.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith(f"#!list {name} "):
            lines[index] = directive
            return "\n".join(lines) + "\n"
    raise LexiconError(f"{target}: no `#!list {name} <count>` directive to update")


#: Why the region exists, carried in the file so a reader who never opens this
#: module still learns what not to hand-edit and what the veto protects.
_GENERATED_PREAMBLE = """\
# Bare plurals of the words above, so a capitalised `Sets` or `Parties` is
# vetoed by the same lookup its singular is. Hand-written pairs used to do this
# and only for the 35 somebody thought of.
#
# Two subtractions are applied here and neither is optional. A form borne as an
# American surname is dropped, because a stop word puts every family bearing it
# beyond the redactor (`may` would claim `Mays`, `will` would claim `Wills`).
# A form that is a common given name is dropped for a sharper reason: a stop
# word wins over the given-name tier, so `we` -> `wes` would stop redacting a
# child called Wes. Plurals still written out by hand above are the ones a
# subtraction removes from here — they are load-bearing, not leftovers.
#
# Regenerate with `python -m vicary_build lexicon`; do not edit below by hand."""


def _authored_words(head: str) -> frozenset[str]:
    """The case-folded words of the authored region, directives and comments out."""
    return frozenset(
        word.lower()
        for line in head.splitlines()
        if not line.strip().startswith("#")
        for word in line.split()
    )


def compose(name: str, *, veto: frozenset[str], path: Path | None = None) -> str:
    """The full text of lexicon ``name`` with its generated region rebuilt.

    Returns the bytes rather than writing them, so the freshness test can compare
    without touching the tree. A generated artifact that only a *write* can be
    checked against is one CI has to trust.
    """
    target = path or lexicon_path(name)
    text = target.read_text(encoding="utf-8")
    head = _authored_head(text, target=target)
    authored = _authored_words(head)
    forms = inflections(authored, veto=veto)
    head = _with_count(head, name, len(authored | set(forms)), target=target)
    body = "\n".join(render_generated(forms))
    return (
        f"{head}\n{GENERATED_BEGIN}\n{_GENERATED_PREAMBLE}\n{body}\n"
        f"{GENERATED_END}\n"
    )


def rewrite(name: str, *, veto: frozenset[str], path: Path | None = None
            ) -> tuple[Path, bool]:
    """Rebuild lexicon ``name``'s generated region on disk. ``(path, changed)``."""
    target = path or lexicon_path(name)
    composed = compose(name, veto=veto, path=path)
    changed = composed != target.read_text(encoding="utf-8")
    if changed:
        target.write_text(composed, encoding="utf-8")
    return target, changed
