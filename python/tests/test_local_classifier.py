"""Unit tests for the local PII classifier.

Recall against injected ground truth is measured by
``python -m vicary.eval.recall``. Against the current fixture
(``src/vicary/eval/fixture.py`` v2026-08-05.1) the shipped classifier reads 35.6%
overall and **0.0% on the held-out frames**, at 0.6ms p50 and $0/essay. The
89.3% this file used to cite was a fixture-v1 figure: that fixture held one
third-party name in one sentence frame, so its headline measured the test set's
composition rather than the detector.

These tests pin the *behaviours* that measurement depends on, including the two
that are easy to regress silently: the false-positive guards, and the honest
boundary at third-party names. The per-frame verdicts and the structural
invariants live in ``test_fixture.py``.
"""

from __future__ import annotations

import re

import pytest

from vicary.local_classifier import (
    LocalNameClassifier,
    StudentIdentity,
    _luhn_ok,
    _school_acronym,
)


def unnumber(text: str) -> str:
    """Strip placeholder indices, so a test asserts the KIND it actually means.

    ``{NAME_1}`` -> ``{NAME}``. Numbering identifies *which* entity; almost every
    test here is about *what* the entity was typed as, and hardcoding an index
    would make each one break whenever an unrelated span is added earlier in the
    document. The tests that are genuinely about numbering assert on the raw
    output instead.
    """
    from vicary.eval.fixture import placeholder_kind

    return re.sub(r"\{[A-Za-z_0-9]*\}", lambda m: placeholder_kind(m.group(0)), text)


def _mask(text: str, identity: StudentIdentity | None = None) -> str:
    """Masked text with placeholder indices stripped — see :func:`unnumber`.

    Every assertion below is about the entity TYPE, so the index is noise here.
    ``_mask_numbered`` is the raw path, for the tests that are about numbering.
    """
    return unnumber(_mask_numbered(text, identity))


def _mask_numbered(text: str, identity: StudentIdentity | None = None) -> str:
    return LocalNameClassifier(identity).mask(text).text


# ---------------------------------------------------------------------------
# Structured entities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,placeholder",
    [
        ("write me at m.delacroix2011@westfieldhigh.k12.oh.us ok", "{EMAIL}"),
        ("call (330) 555-0148 today", "{PHONE}"),
        ("call 330-555-0148 today", "{PHONE}"),
        ("call 330.555.0148 today", "{PHONE}"),
        ("ssn 287-44-9163 here", "{US_SOCIAL_SECURITY_NUMBER}"),
        ("card 4532 7891 2345 6789 here", "{CREDIT_DEBIT_CARD_NUMBER}"),
        ("at 1147 Beaumont Terrace now", "{ADDRESS}"),
        ("server 192.168.1.100 down", "{IP_ADDRESS}"),
        ("see https://example.com/students/x here", "{URL}"),
        ("my handle is @margie_dw2011 ok", "{USERNAME}"),
        ("I am 14 years old", "{AGE}"),
        ("date of birth: 03/14/2011", "{DATE_OF_BIRTH}"),
    ],
)
def test_structured_entities_are_masked(text: str, placeholder: str) -> None:
    out = _mask(text)
    assert placeholder in out


def test_the_literal_is_gone_not_merely_flagged() -> None:
    """Recall means the span is absent, not that we noticed it."""
    out = _mask("reach me at m.delacroix2011@westfieldhigh.k12.oh.us")
    assert "m.delacroix2011" not in out
    assert "westfieldhigh" not in out


# ---------------------------------------------------------------------------
# False-positive guards — the quality side. Over-masking deletes prose the
# scorer needs, so each of these is a real grading defect, not a nitpick.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The essay scored 4532 points in 2011.",       # long digits, not a card
        "I ran 3 miles down the road that day.",       # no street suffix
        "Between 1999 and 2005 things changed.",       # years, not a phone
        "We read pages 100 200 300 in class.",         # digit runs
        "The population grew to 45000 people.",        # not a ZIP
        "Chapter 12 section 4 was the hardest.",
    ],
)
def test_ordinary_prose_is_left_alone(text: str) -> None:
    assert _mask(text) == text


def test_a_failed_luhn_check_is_not_a_card() -> None:
    """The card pattern is loose by necessity; Luhn is what makes it usable."""
    assert _luhn_ok("4532789123456789")
    assert not _luhn_ok("4532789123456780")
    assert "{CREDIT_DEBIT_CARD_NUMBER}" not in _mask("the number 4532 7891 2345 6780")


def test_a_given_name_that_is_also_a_word_is_not_masked_alone() -> None:
    """'Will you go' must survive a student named Will.

    A missed first name is one span; masking every occurrence of a common word
    corrupts every essay that uses it.
    """
    identity = StudentIdentity(first_name="Will", last_name="Hastings")
    out = _mask("Will you go to the store? Hastings said yes.", identity)

    assert out.startswith("Will you go")
    assert "{NAME}" in out  # the surname still masks


def test_the_full_name_still_masks_even_when_the_given_name_is_ambiguous() -> None:
    identity = StudentIdentity(first_name="Will", last_name="Hastings")
    out = _mask("Will Hastings wrote this.", identity)

    assert "Will Hastings" not in out
    assert out.startswith("{NAME}")


# ---------------------------------------------------------------------------
# Identity interpolation
# ---------------------------------------------------------------------------


@pytest.fixture
def student() -> StudentIdentity:
    return StudentIdentity(
        first_name="Marguerite",
        last_name="Delacroix-Whitfield",
        school_name="Westfield High School",
    )


def test_the_students_own_name_is_masked(student: StudentIdentity) -> None:
    out = _mask("I remember when Marguerite Delacroix-Whitfield said so.", student)
    assert "Marguerite" not in out
    assert "Delacroix-Whitfield" not in out


def test_the_full_name_masks_as_one_span(student: StudentIdentity) -> None:
    """Not two adjacent placeholders — the full name is matched first."""
    assert _mask("Marguerite Delacroix-Whitfield", student) == "{NAME}"


def test_the_roster_order_is_masked(student: StudentIdentity) -> None:
    assert "Delacroix-Whitfield, Marguerite" not in _mask(
        "Delacroix-Whitfield, Marguerite", student
    )


def test_a_possessive_name_is_masked_with_the_name(student: StudentIdentity) -> None:
    """'Marguerite's essay' — how a name actually appears in student prose."""
    out = _mask("This is Marguerite's essay.", student)
    assert "Marguerite" not in out


def test_a_curly_possessive_is_masked_with_the_name(
    student: StudentIdentity,
) -> None:
    """A word processor turns every apostrophe curly, so this is the common form.

    The straight-apostrophe case above passed for a year while this one failed,
    because the pattern's second alternative repeated the first instead of being
    the curly form it resembled.
    """
    out = _mask("This is Marguerite’s essay.", student)
    assert "Marguerite" not in out
    assert "’s" not in out


def test_a_plural_family_possessive_is_masked_with_the_name(
    student: StudentIdentity,
) -> None:
    """The tail the pattern always claimed to cover and never could.

    "the Delacroix-Whitfields' house" — the ``s'`` alternative was dead code,
    because the boundary after its apostrophe had no word character to hold on
    to.
    """
    for text in (
        "I went to the Delacroix-Whitfields' house.",
        "I went to the Delacroix-Whitfields’ house.",
    ):
        assert "Delacroix-Whitfield" not in _mask(text, student)


def test_a_literal_ending_in_punctuation_is_masked() -> None:
    """Roster data arrives suffixed, and a trailing ``\\b`` can never hold after
    a closing paren — so this literal silently masked nothing at all."""
    suffixed = StudentIdentity(last_name="O'Brien (Jr.)")
    assert "O'Brien" not in _mask("O'Brien (Jr.) was here.", suffixed)


def test_a_punctuation_edged_literal_does_not_widen_an_ordinary_one() -> None:
    """The boundary is dropped only on the side that has no word character to
    assert against, so every ordinary name is bounded exactly as before."""
    plain = StudentIdentity(last_name="Okonkwo")
    # "Okonkwoville" must not match, and would if the trailing assertion were
    # dropped unconditionally rather than only where it cannot hold.
    unchanged = "I grew up near Okonkwoville."
    assert _mask(unchanged, plain) == unchanged


def test_the_school_and_its_acronym_are_masked(student: StudentIdentity) -> None:
    out = _mask("I go to Westfield High School. WHS is big.", student)
    assert "Westfield High School" not in out
    assert "WHS" not in out
    assert out.count("{SCHOOL}") == 2


def test_a_short_acronym_is_not_generated() -> None:
    """A two-letter acronym collides with state codes and ordinary words."""
    assert _school_acronym("Westfield High School") == "WHS"
    assert _school_acronym("North High") is None


def test_an_absent_identity_still_masks_structured_pii() -> None:
    """The honest degrade: no identity costs names, not everything."""
    out = _mask("call (330) 555-0148 or write jo@example.com", None)
    assert "{PHONE}" in out
    assert "{EMAIL}" in out


def test_an_empty_identity_contributes_no_patterns() -> None:
    assert StudentIdentity().is_empty()
    assert not StudentIdentity(last_name="Okonkwo").is_empty()


# ---------------------------------------------------------------------------
# The documented boundary
# ---------------------------------------------------------------------------


def test_a_third_party_name_is_NOT_masked(student: StudentIdentity) -> None:
    """This is the known gap, pinned deliberately rather than left implied.

    Interpolation can only mask names we were given. A classmate the student
    mentions needs candidate generation plus a notability filter, so that a
    public figure the student writes *about* survives.

    This is one frame. ``test_fixture.py`` measures the same leg across every
    frame narrative prose actually uses and finds it at 0.0% in all of them —
    the classifier does not miss third-party names sometimes, it misses them
    always.

    If this test starts FAILING, the detector leg has landed: update the recall
    numbers in local_classifier.py, production_config.md and
    transparency_artifact.md rather than just deleting the test.
    """
    out = _mask("My cousin Terrence Okonkwo came over.", student)
    assert "Terrence Okonkwo" in out


# ---------------------------------------------------------------------------
# Cost and latency posture
# ---------------------------------------------------------------------------


def test_masking_is_reported_and_counted(student: StudentIdentity) -> None:
    result = LocalNameClassifier(student).mask(
        "Marguerite Delacroix-Whitfield, (330) 555-0148, jo@example.com"
    )
    assert result.intervened
    assert result.n_masked == 3


def test_clean_text_reports_no_intervention() -> None:
    result = LocalNameClassifier().mask("The author argues for shorter commutes.")
    assert not result.intervened
    assert result.n_masked == 0


def test_empty_text_is_handled() -> None:
    result = LocalNameClassifier().mask("")
    assert result.text == ""
    assert not result.intervened
