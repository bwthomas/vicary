"""The span map: can an offset cross between redacted and original coordinates?

Why this matters, and it is not a robustness nicety. A host that scores a
masked copy of a document and records char offsets against it has offsets in
*masked* coordinates. A placeholder is not the width of what it replaced, so
rendering those offsets over the original document displaces every span after
the first replacement by the cumulative delta. Measured on a real 56-document
corpus: redaction fired on 43 documents, and on those 43 not one of 2,795
recorded offset pairs reproduced its own quote against the original.

The map is DERIVED, not recorded — see :func:`vicary.derive_spans` for why
instrumenting the multi-pass masker is unnecessary.
"""

from __future__ import annotations

import pytest

from vicary import (
    RedactionResult,
    RedactionSpan,
    build_redactor_if_enabled,
    derive_spans,
    to_original,
    to_redacted,
)

TEXT = (
    "My teacher Mr. Periwinkle assigned this. "
    "Civil disobedience requires courage. Call 555-867-5309."
)
QUOTE = "Civil disobedience requires courage."


@pytest.fixture
def result():
    return build_redactor_if_enabled("local", identity=None).redact_inbound(TEXT)


def test_the_premise_masking_moves_the_offsets(result):
    """If it did not, every assertion below would pass for the wrong reason."""
    assert result.intervened
    assert len(result.text) != len(TEXT)
    assert result.text.index(QUOTE) != TEXT.index(QUOTE)


def test_round_trip_reconstructs_the_original(result):
    assert result.original() == TEXT


def test_an_offset_translated_to_original_selects_the_same_words(result):
    start = result.text.index(QUOTE)
    end = start + len(QUOTE)
    o_start, o_end = result.to_original(start), result.to_original(end)
    assert TEXT[o_start:o_end] == QUOTE
    # And the naive read — offsets used as-is — is wrong, which is the defect.
    assert TEXT[start:end] != QUOTE


def test_translation_is_invertible(result):
    start = result.text.index(QUOTE)
    assert result.to_redacted(result.to_original(start)) == start


def test_offset_inside_a_placeholder_maps_to_the_span_it_replaced(result):
    """A placeholder's interior has no counterpart in the original.

    Any position within it is the same position in original terms, so it maps
    to the start of what was removed rather than to a plausible-looking
    interior offset.
    """
    inside = result.text.index("{") + 3
    span = next(s for s in result.spans() if s.new_start <= inside < s.new_end)
    assert result.to_original(inside) == span.orig_start


def test_offsets_before_the_first_replacement_are_unchanged(result):
    first = result.spans()[0]
    assert result.to_original(first.new_start - 1) == first.new_start - 1


def test_no_restore_map_means_no_spans_rather_than_wrong_spans():
    """A partial or absent map must DECLINE, not guess.

    The Guardrail path returns masked text and no map. A caller can handle "no
    map" — do not highlight — and cannot detect a silently wrong offset, so
    refusing is the only safe answer.
    """
    bare = RedactionResult(text="a {NAME_1} b", intervened=True, char_units=0)
    assert bare.spans() == ()
    assert bare.to_original(11) == 11


def test_a_map_that_does_not_cover_every_placeholder_is_refused():
    partial = {"{NAME_1}": "Jane"}
    assert derive_spans("a {NAME_1} and {NAME_2} b", partial) == ()


def test_repeated_placeholder_is_located_at_every_occurrence():
    """One person named twice is two spans, each with the same original width."""
    spans = derive_spans("{NAME_1} met {NAME_2}, then {NAME_1} left.",
                         {"{NAME_1}": "Bartholomew", "{NAME_2": "x",
                          "{NAME_2}": "Jo"})
    assert [s.new_start for s in spans] == [0, 13, 28]
    assert [s.orig_end - s.orig_start for s in spans] == [11, 2, 11]


def test_pure_functions_accept_a_bare_span_sequence():
    """The translation is usable without a RedactionResult in hand."""
    spans = (RedactionSpan(orig_start=5, orig_end=15, new_start=5, new_end=10),)
    assert to_original(20, spans) == 25
    assert to_redacted(25, spans) == 20


def test_clean_text_yields_an_empty_map_and_identity_translation():
    clean = build_redactor_if_enabled("local", identity=None).redact_inbound(
        "Civil disobedience requires moral courage."
    )
    assert not clean.intervened
    assert clean.spans() == ()
    assert clean.to_original(7) == 7
