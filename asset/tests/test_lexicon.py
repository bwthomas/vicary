"""The lexicon format, and the pin between its readers.

Every front door ships its own reader for this format, because the build tool must
not import one of the three implementations it feeds. The duplication is only
honest if something compares the results, which is what
:func:`test_both_readers_agree_on_the_shipped_stoplist` is for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vicary_build import config, lexicon, reference


def test_the_shipped_stoplist_parses() -> None:
    words = lexicon.load("stop_words")
    assert len(words) == 794
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
    divergence: a stoplist that parses to 794 words here and 792 there would show
    up as prose corruption in one language and nothing at all in the tests.
    """
    from vicary import lexicon as runtime_lexicon

    assert runtime_lexicon.LEXICON_FORMAT == lexicon.LEXICON_FORMAT
    assert runtime_lexicon.load("stop_words") == lexicon.load("stop_words")


def test_the_generated_region_is_what_a_regeneration_would_write() -> None:
    """The tracked file is generated in part, so CI has to own its freshness.

    `python -m vicary_build lexicon` writes the inflections; nothing stops a
    person adding a singular and committing without running it. The cost of that
    is silent and one-directional — the plural nobody generated stays a name
    candidate — so the check is byte equality against a recompose, not a spot
    check on a word somebody thought of.
    """
    target = lexicon.lexicon_path("stop_words")
    composed = lexicon.compose("stop_words", veto=reference.veto())
    assert composed == target.read_text(encoding="utf-8"), (
        "asset/lexicon/stop_words.txt is not what `python -m vicary_build "
        "lexicon` would write. Run it, then `python -m vicary_build manifest` "
        "and `just asset-sync`."
    )


def test_the_built_list_still_carries_every_word_it_used_to() -> None:
    """The hand-written plurals were removed on the promise the build re-emits them.

    Which is a promise the census veto can break: a form an American bears as a
    surname is dropped from the generated region, so `friends` and `schools` are
    still written out by hand and must stay that way. This is the assertion that
    turns "we think the generator covers it" into something that fails loudly the
    day it stops being true.
    """
    words = lexicon.load("stop_words")
    for removed in _REMOVED_HANDWRITTEN_PLURALS:
        assert removed in words, (
            f"{removed!r} was a hand-written stop word, removed because the "
            "build generates it. It is no longer in the built list, so a "
            "capitalised occurrence is a name candidate again."
        )
    borne = reference.borne_surnames()
    for kept in _PLURALS_THE_VETO_DROPS:
        assert kept in words
        assert kept in borne, (
            f"{kept!r} is written out by hand only because the census veto "
            "refuses to generate it. If no American bears it as a surname the "
            "generator now covers it, and the authored copy should go."
        )


#: Hand-written before the build learned to inflect, and deleted on the promise
#: it re-emits them.
_REMOVED_HANDWRITTEN_PLURALS = (
    "americans", "besides", "classes", "families", "hours", "lets", "lots",
    "minutes", "months", "nazis", "others", "parents", "parts", "places",
    "sisters", "students", "teachers", "things",
)

#: Genuine plurals the census veto refuses to generate, because an American
#: family bears each one as a surname. They stay authored by hand.
_PLURALS_THE_VETO_DROPS = (
    "brothers", "days", "friends", "schools", "times", "ways", "weeks", "years",
)


def test_a_plural_is_generated_and_a_borne_surname_is_not() -> None:
    """The two halves of the rule, on the shipped list rather than a fixture.

    `sets` is the case the whole task is for: `Sets` reached the detector as a
    name candidate because nobody hand-wrote the pair. `mays` and `wills` are
    what the census veto is for, and `wes` is what the given-name veto is for —
    a stop word beats the given-name tier, so emitting it would stop redacting a
    child called Wes for good.
    """
    words = lexicon.load("stop_words")
    assert "sets" in words
    assert "parties" not in words  # `party` is not a stop word; nothing to fold
    for borne in ("mays", "downs", "wills", "peoples"):
        assert borne not in words, f"{borne} is an American surname"
    assert "wes" not in words, "a stop word wins over the given-name tier"


def test_a_plural_of_a_plural_is_not_a_word() -> None:
    """`days` is on the list, and `dayses` must not be.

    Cheap to get wrong and cheap to check. A generated file a reader stops
    trusting is one they start hand-editing.
    """
    words = lexicon.load("stop_words")
    for junk in ("dayses", "brotherses", "itses", "yearses"):
        assert junk not in words


def test_the_inflection_rules_are_the_three_regular_ones() -> None:
    assert lexicon.plural_forms("set") == {"sets"}
    assert lexicon.plural_forms("party") == {"parties"}
    assert lexicon.plural_forms("class") == {"classes"}
    assert lexicon.plural_forms("box") == {"boxes"}
    assert lexicon.plural_forms("church") == {"churches"}
    # A vowel before the `y` keeps it: "days", never "daies".
    assert lexicon.plural_forms("day") == {"days"}
    # Single letters and anything with punctuation are left alone: `a` would
    # otherwise generate `as`, and `u.s` would generate `u.ss`.
    assert lexicon.plural_forms("a") == set()
    assert lexicon.plural_forms("u.s") == set()


def test_a_generated_region_that_is_never_closed_is_an_error(tmp_path: Path) -> None:
    """Refusing beats guessing: the closer is what says where authored words end."""
    probe = _write(
        tmp_path,
        f"#!lexicon 1\n#!list probe 1\nalpha\n{lexicon.GENERATED_BEGIN}\nbeta\n",
    )
    with pytest.raises(lexicon.LexiconError, match="never closed"):
        lexicon.compose("probe", veto=frozenset(), path=probe)


def test_regenerating_is_idempotent(tmp_path: Path) -> None:
    """Twice must equal once, or every rebuild is a diff and nobody reads them."""
    probe = _write(tmp_path, "#!lexicon 1\n#!list probe 1\ncat\n")
    once = lexicon.compose("probe", veto=frozenset(), path=probe)
    probe.write_text(once, encoding="utf-8")
    assert lexicon.compose("probe", veto=frozenset(), path=probe) == once
    assert lexicon.load("probe", path=probe) == {"cat", "cats"}


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
