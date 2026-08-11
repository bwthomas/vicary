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
