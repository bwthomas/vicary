"""The host boundary: does any placeholder survive into text bound for a reader?

The cases are read from ``conformance/placeholders.json``, the same file the
TypeScript and Ruby ports run, rather than from the literals that generated it —
so Python is a participant in its own parity check. The named tests below pin the
behaviours a host relies on, so a failure says which rule broke rather than which
case differs.
"""

from __future__ import annotations

import pytest

from vicary import (
    PLACEHOLDER_KINDS,
    PLACEHOLDER_LIKE,
    PlaceholderLeakError,
    assert_no_placeholders,
    find_placeholders,
    has_placeholders,
    scrub_placeholders,
)
from vicary.eval import conformance
from vicary.placeholders import OTHER_KIND, SENTENCE_END, placeholder_kind

SPEC = conformance.load_placeholders_document()
CASES = SPEC["cases"]


def test_the_spec_is_not_empty() -> None:
    assert len(CASES) >= 30
    assert any(c["leaks"] for c in CASES)
    assert any(not c["leaks"] for c in CASES)


def test_the_spec_records_this_pattern() -> None:
    assert SPEC["pattern"] == PLACEHOLDER_LIKE.pattern
    assert SPEC["sentence_end"] == SENTENCE_END.pattern
    assert SPEC["kinds"] == list(PLACEHOLDER_KINDS)


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_each_case_matches_the_spec(case: dict) -> None:
    text = case["text"]
    found = [
        {"text": m.text, "kind": m.kind, "start": m.start, "end": m.end}
        for m in find_placeholders(text)
    ]
    assert found == case["matches"]
    assert has_placeholders(text) is case["leaks"]
    prose = scrub_placeholders(text)
    assert {"text": prose.text, "removed": prose.removed} == case["scrub_prose"]
    quote = scrub_placeholders(text, quote=True)
    assert {"text": quote.text, "removed": quote.removed} == case["scrub_quote"]


def test_vicary_s_own_grammar_is_a_subset() -> None:
    # Everything the masker can mint must be caught: the guard is a superset of
    # `redaction._PLACEHOLDER`, never a parallel grammar that could miss one.
    for kind in PLACEHOLDER_KINDS:
        for token in (f"{{{kind}}}", f"{{{kind}_1}}", f"{{{kind}_42}}"):
            assert [m.text for m in find_placeholders(f"a {token} b")] == [token]
            assert placeholder_kind(token) == kind


def test_the_reported_incident_is_caught() -> None:
    text = "How exactly does microplastic affect a specific {NAME_1}"
    with pytest.raises(PlaceholderLeakError) as raised:
        assert_no_placeholders(text)
    assert raised.value.kinds == ("NAME",)
    assert raised.value.count == 1


def test_the_error_names_kinds_and_never_the_text() -> None:
    text = "Priya told {NAME_1} at [EMAIL] about {JOHN} and {NAME_2}."
    with pytest.raises(PlaceholderLeakError) as raised:
        assert_no_placeholders(text)
    message = str(raised.value)
    assert raised.value.kinds == ("EMAIL", "NAME", OTHER_KIND)
    assert raised.value.count == 4
    # Neither the surrounding words nor an unknown kind's letters, which are
    # the document's own text, reach the message.
    for fragment in ("Priya", "told", "JOHN", "{NAME_1}"):
        assert fragment not in message
    assert isinstance(raised.value, ValueError)


def test_clean_text_passes_through_unchanged() -> None:
    text = "A clear thesis. Ms. Name would agree."
    assert assert_no_placeholders(text) is text
    assert scrub_placeholders(text).text is text
    assert scrub_placeholders(text).removed == 0


def test_a_prose_scrub_drops_the_whole_sentence() -> None:
    result = scrub_placeholders("Great work. Unlike Ms. {NAME_1}, you cite. Go on!")
    assert result.text == "Great work. Go on!"
    assert result.removed == 1
    assert not has_placeholders(result.text)


def test_a_quote_scrub_elides_and_keeps_the_quote() -> None:
    result = scrub_placeholders("I told {NAME_1} it was fine.", quote=True)
    assert result.text == "I told … it was fine."


def test_no_case_leaves_a_placeholder_behind() -> None:
    for case in CASES:
        assert not has_placeholders(case["scrub_prose"]["text"]), case["case_id"]
        assert not has_placeholders(case["scrub_quote"]["text"]), case["case_id"]
