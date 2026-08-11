"""Read a language-neutral word list that ships beside the gazetteer.

The stoplist used to be a literal in :mod:`vicary.name_candidates`, which was
fine while there was one front door. With three, a hand-transliterated word list
is a second detector wearing the first one's name: the divergence shows up as
prose corruption in one language and not the others, and no parity check on
*masked output* would catch it, because a stop word going missing changes what
gets masked in essays nobody put in a fixture.

So the list is authored once, language-neutrally, under ``asset/lexicon/`` in the
repository, and vendored into each package the same way the gazetteer is. The
build mechanism owns the file; every front door just reads it.

Format
------
``#!`` lines are directives, ``#`` lines are comments, blank lines are skipped,
and every other line contributes whitespace-separated words. One directive is
required::

    #!lexicon 1               format version
    #!list <name> <count>     the list's name, and its DISTINCT word count

The count is asserted, not trusted. A short read here makes the redactor **more**
aggressive — fewer stop words means more capitalised ordinary words become name
candidates — which looks privacy-safe, corrupts prose, and passes any check that
only asks whether something was masked. Same reasoning as the gazetteer's
per-tier counts; same failure mode if it is skipped.
"""

from __future__ import annotations

from pathlib import Path

from vicary.assets import DATA_DIR, AssetError

#: On-disk format version for a lexicon file. Bump when the parse changes, so a
#: stale vendored copy fails loudly rather than parsing to a different list.
LEXICON_FORMAT = 1

#: Filename suffix. Named so a second list costs a file rather than a refactor.
SUFFIX = ".txt"


def lexicon_path(name: str) -> Path:
    """Path to the vendored copy of ``name``, whether or not it exists."""
    return DATA_DIR / f"{name}{SUFFIX}"


def load(name: str) -> frozenset[str]:
    """The case-folded distinct words of lexicon ``name``.

    Raises :class:`AssetError` rather than returning a partial list. An empty or
    truncated stoplist is the quiet failure this whole module exists to prevent.
    """
    path = lexicon_path(name)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AssetError(
            f"lexicon {name!r} missing at {path}. The installed vicary package is "
            "incomplete — reinstall it, or vendor the asset with "
            "`just asset-sync` from a checkout."
        ) from exc
    except OSError as exc:
        raise AssetError(f"cannot read lexicon at {path}: {exc}") from exc

    declared: int | None = None
    words: set[str] = set()
    saw_format = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#!"):
            parts = stripped[2:].split()
            if not parts:
                raise AssetError(f"{path}:{lineno}: empty directive")
            if parts[0] == "lexicon":
                saw_format = True
                if len(parts) != 2 or parts[1] != str(LEXICON_FORMAT):
                    raise AssetError(
                        f"{path}:{lineno}: lexicon format {' '.join(parts[1:])!r}, "
                        f"this build reads {LEXICON_FORMAT}"
                    )
            elif parts[0] == "list":
                if len(parts) != 3 or parts[1] != name:
                    raise AssetError(
                        f"{path}:{lineno}: expected `#!list {name} <count>`, "
                        f"got {stripped!r}"
                    )
                declared = int(parts[2])
            else:
                # Refused rather than ignored: an unrecognised directive means the
                # file was written by something that knows more than this reader,
                # and guessing which lines are still words is how a partial list
                # loads as a whole one.
                raise AssetError(f"{path}:{lineno}: unknown directive {parts[0]!r}")
            continue
        if not stripped or stripped.startswith("#"):
            continue
        words.update(word.lower() for word in stripped.split())

    if not saw_format:
        raise AssetError(f"{path}: no `#!lexicon` directive; not a lexicon file")
    if declared is None:
        raise AssetError(f"{path}: no `#!list {name} <count>` directive")
    if len(words) != declared:
        raise AssetError(
            f"{path}: declares {declared} distinct words, parsed {len(words)}. A "
            "short read makes the redactor more aggressive, which is why this is "
            "an error and not a warning."
        )
    return frozenset(words)
