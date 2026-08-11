"""The lexicon format, and the pin between its readers.

Every front door ships its own reader for this format, because the build tool must
not import one of the three implementations it feeds. The duplication is only
honest if something compares the results, which is what
:func:`test_both_readers_agree_on_the_shipped_stoplist` is for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vicary_build import config, lexicon


def test_the_shipped_stoplist_parses() -> None:
    words = lexicon.load("stop_words")
    assert len(words) == 421
    # Spot-checks at the two ends of the file, so a truncated read fails here and
    # not only on the count.
    assert "the" in words
    assert "favorite" in words
    # Case-folded on read, so a reader never has to remember to fold.
    assert all(word == word.lower() for word in words)


def test_both_readers_agree_on_the_shipped_stoplist() -> None:
    """The build tool's reader and the Python front door's, on the same bytes.

    Two readers of one format is the cost of not coupling the build mechanism to
    one of its consumers. This is the test that keeps that cost from becoming a
    divergence: a stoplist that parses to 421 words here and 419 there would show
    up as prose corruption in one language and nothing at all in the tests.
    """
    from vicary import lexicon as runtime_lexicon

    assert runtime_lexicon.LEXICON_FORMAT == lexicon.LEXICON_FORMAT
    assert runtime_lexicon.load("stop_words") == lexicon.load("stop_words")


def test_every_lexicon_in_the_directory_is_discovered() -> None:
    """The sync step vendors what this returns, so a new file must appear here."""
    assert lexicon.names() == ["stop_words"]
    assert set(lexicon.names()) == {
        path.stem for path in config.LEXICON_DIR.glob("*.txt")
    }


# ---------------------------------------------------------------------------
# The guards. Each has a plausible failing case, written out rather than implied.
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "probe.txt"
    target.write_text(body, encoding="utf-8")
    return target


def test_a_declared_count_that_disagrees_is_an_error(tmp_path: Path) -> None:
    """The guard that matters most, and the one whose absence is invisible.

    A short read makes every reader of this list *more* aggressive about what
    counts as a name — fewer stop words means more capitalised ordinary words
    become candidates. That looks privacy-safe, corrupts prose, and passes any
    check that only asks whether something was masked.
    """
    probe = _write(tmp_path, "#!lexicon 1\n#!list probe 3\nalpha beta\n")
    with pytest.raises(lexicon.LexiconError, match="declares 3 distinct words, parsed 2"):
        lexicon.load("probe", path=probe)


def test_duplicates_count_once(tmp_path: Path) -> None:
    """The groupings in the source file overlap on purpose ("else", "may", "us").

    Enforcing uniqueness in the source would make the list harder to read for no
    benefit, so the count is of DISTINCT words and this is what that means.
    """
    probe = _write(tmp_path, "#!lexicon 1\n#!list probe 2\nalpha beta\nALPHA\n")
    assert lexicon.load("probe", path=probe) == {"alpha", "beta"}


def test_a_future_format_is_refused(tmp_path: Path) -> None:
    probe = _write(tmp_path, "#!lexicon 2\n#!list probe 1\nalpha\n")
    with pytest.raises(lexicon.LexiconError, match="lexicon format"):
        lexicon.load("probe", path=probe)


def test_an_unknown_directive_is_refused_not_ignored(tmp_path: Path) -> None:
    """Ignoring it would mean guessing which lines are still words.

    A file written by something that knows more than this reader is a file this
    reader cannot claim to have read whole.
    """
    probe = _write(tmp_path, "#!lexicon 1\n#!list probe 1\n#!weights 3\nalpha\n")
    with pytest.raises(lexicon.LexiconError, match="unknown directive 'weights'"):
        lexicon.load("probe", path=probe)


def test_a_plain_word_list_is_not_a_lexicon(tmp_path: Path) -> None:
    """No header means no count, and no count means no truncation check."""
    probe = _write(tmp_path, "alpha beta\n")
    with pytest.raises(lexicon.LexiconError, match="no `#!lexicon` directive"):
        lexicon.load("probe", path=probe)


def test_a_lexicon_naming_a_different_list_is_refused(tmp_path: Path) -> None:
    """Catches a mis-vendored file: right format, wrong contents."""
    probe = _write(tmp_path, "#!lexicon 1\n#!list other 1\nalpha\n")
    with pytest.raises(lexicon.LexiconError, match="expected `#!list probe"):
        lexicon.load("probe", path=probe)


def test_comments_and_blank_lines_contribute_nothing(tmp_path: Path) -> None:
    probe = _write(
        tmp_path,
        "#!lexicon 1\n#!list probe 2\n# a note\n\nalpha\n   \n# beta is not a word\nbeta\n",
    )
    assert lexicon.load("probe", path=probe) == {"alpha", "beta"}
