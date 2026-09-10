"""The word-list exposure probe: the properties that make its numbers mean something.

These are not gates — nothing here has a bar to defend, because no veto ships
yet. They pin the three things that were measured wrong on the way to building
it, so the next caller does not have to rediscover them.
"""

from __future__ import annotations

import pytest

from vicary.eval import lexicon_exposure as lx

WORDS = "/usr/share/dict/words"


@pytest.fixture(scope="module")
def word_list_path(tmp_path_factory) -> str:
    """The system list if this box has one, else a stand-in carrying the same
    trap: ordinary words plus capitalised proper names."""
    import os
    if os.path.exists(WORDS):
        return WORDS
    path = tmp_path_factory.mktemp("lex") / "words"
    path.write_text("\n".join([
        "cold", "space", "field", "english", "boy", "boys",
        "English", "Halloween", "Martinez", "Nguyen", "Moore", "William",
    ]), encoding="utf-8")
    return str(path)


def test_case_folding_alone_admits_real_surnames(word_list_path) -> None:
    """The defect this module exists to price.

    A system word list is not a lexicon of common nouns. Case-folding it to pick
    up ``English`` and ``Halloween`` also picks up ``Martinez`` and ``Nguyen``,
    which is how a common-noun veto ends up claiming a real name.
    """
    lowercase = lx.load_word_list(word_list_path)
    folded = lx.load_word_list(word_list_path, case_fold=True)

    assert "english" in folded and "english" not in lowercase
    assert {"martinez", "nguyen"} <= folded
    assert not {"martinez", "nguyen"} & lowercase


def test_excluding_by_bearer_count_keeps_the_words_and_drops_the_names(
        word_list_path) -> None:
    """The repair, and why it needs a threshold rather than a flag.

    A blanket exclusion of every census surname costs 16 of 35 recovered spans
    on the NWP AWC corpus, because the census carries rare surnames that are
    ordinary words (``Cold``, ``Space``, ``Boys``). Excluding by *bearers* keeps
    those and drops the common ones.
    """
    repaired = lx.load_word_list(word_list_path, case_fold=True,
                                 exclude_names_above=100_000)
    assert "english" in repaired, "the entry case-folding was turned on for"
    assert "martinez" not in repaired
    assert "nguyen" not in repaired


def test_plural_stripping_does_not_compose_with_a_fuzzy_matcher() -> None:
    """The distance-2 back door.

    Depluralising a *fuzzy candidate* silently reaches two edits: a
    keyboard-substitution arm matched ``feild`` -> ``feils`` -> ``feil`` and
    reported it as one edit. Plurals apply to the exact test only.
    """
    lexicon = frozenset({"feil"})
    member = lx.build_membership(lexicon, matcher="keyboard-substitution",
                                 plurals=True)
    assert not member("feild"), "reached a two-edit match through depluralise"

    assert lx.build_membership(frozenset({"boy"}), plurals=True)("boys")


def test_matchers_are_monotone_in_reach() -> None:
    """Each channel is a superset of exact, and damerau-1 subsumes transposition.

    A ranking is the only thing this probe is licensed to produce, so the
    ordering has to be a property rather than an observation.
    """
    lexicon = frozenset({"field", "australia", "coach"})
    token = "feild"
    exact = lx.build_membership(lexicon)
    transpose = lx.build_membership(lexicon, matcher="transposition")
    damerau = lx.build_membership(lexicon, matcher="damerau-1")

    assert not exact(token)
    assert transpose(token)
    assert damerau(token)
    assert set(lx._transpositions(token)) <= set(lx._damerau_1(token))


def test_the_population_excludes_what_the_given_name_guard_carries() -> None:
    """Counting names the cheapest guard already protects inflates every arm
    equally and therefore ranks nothing."""
    pytest.importorskip("vicary.eval.census")
    from vicary import gazetteer
    from vicary.eval import census
    if census.shipped_dir() is None:
        pytest.skip("no conformance/census/ in this tree")

    population = lx.surname_population()
    assert population, "empty population would make every bound read 0%"
    assert not any(gazetteer.is_common_given_name(name) for name in population)
    assert all(bearers > 0 for bearers in population.values())


def test_the_bound_is_bearer_weighted_not_a_token_count() -> None:
    """``rate`` is the headline and must weight by bearers — claiming ``Brown``
    is not the same event as claiming a surname one family carries."""
    result = lx.measure(frozenset({"brown"}), population={"brown": 1_000_000,
                                                          "zzzyx": 1})
    assert result.claimed == 1
    assert result.bearers_claimed == 1_000_000
    assert result.rate > result.distinct_rate
