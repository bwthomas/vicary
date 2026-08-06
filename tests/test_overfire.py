"""Tests for the over-fire harness — mostly about it refusing to look clean.

A measurement tool's worst failure is not being wrong, it is reading zero when
it measured nothing. Most of what is asserted here is that the empty and the
misconfigured cases are distinguishable from the good result.
"""

from __future__ import annotations

import json

import pytest

from vicary.eval import overfire
from vicary.redaction import NAMES_GAZETTEER, NAMES_IDENTITY, NAMES_LOWERCASE


def test_prose_with_no_names_at_the_identity_level_is_left_alone() -> None:
    """The floor: identity-only detection cannot invent a name it was not told."""
    groups = [["The lesson went well and the weather held all afternoon."]]
    result = overfire.measure(groups, NAMES_IDENTITY)
    assert result.spans == 0
    assert result.documents_touched == 0
    assert result.by_span == {}


def test_the_absent_identity_really_is_absent_from_what_it_is_scoring() -> None:
    """The instrument's one assumption, asserted rather than assumed.

    Every span this harness counts is called a false positive *because* the
    identity cannot occur in the corpus. If a caller's text happened to contain
    Zephyrine Quillfeather, the count would silently include correct redactions
    and the whole number would be wrong in the flattering direction.
    """
    text = "Zephyrine Quillfeather turned the assignment in on time."
    result = overfire.measure([[text]], NAMES_IDENTITY)
    assert result.spans >= 1, (
        "the sentinel identity is not being masked at all, so a corpus that "
        "contained it would not be detectable as a contaminated corpus"
    )


def test_the_levels_are_reported_separately_not_averaged() -> None:
    groups = [["Reread the opening line and push the idea further."]]
    results = overfire.compare(groups)
    assert [r.level for r in results] == list(overfire.LEVELS)
    assert len({id(r) for r in results}) == 3


def test_rates_use_the_response_as_the_denominator_not_the_field() -> None:
    """A reader experiences one response, not four fields of one.

    Both rates are reported, but they differ by the field count and quoting the
    wrong one under-states the defect by exactly that factor.
    """
    groups = [["one", "two", "three", "four"]]
    result = overfire.measure(groups, NAMES_IDENTITY)
    assert result.groups == 1
    assert result.documents == 4
    result.spans = 4
    assert result.spans_per_group == 4.0
    assert result.spans_per_document == 1.0


def test_a_jsonl_field_is_found_inside_a_json_string(tmp_path) -> None:
    """Generated output usually arrives as JSON nested in a JSON string field.

    A loader that only walked the top level would return nothing here, and
    nothing scores as a clean result.
    """
    payload = json.dumps({"items": [{"reasoning": "Reread your opening."}]})
    path = tmp_path / "out.jsonl"
    path.write_text(json.dumps({"output": payload}) + "\n", encoding="utf-8")

    groups = overfire.load_groups(jsonl_path=str(path), field_names=["reasoning"])
    assert groups == [["Reread your opening."]]


def test_every_named_field_lands_in_the_same_group(tmp_path) -> None:
    """Fields a host redacts in one call must be one group, not several.

    This is the grouping the comparison turned on: scoring the fields apart
    changed which detection level looked better.
    """
    record = {"reasoning": "You anchored it.", "suggestion": "Reread the close."}
    path = tmp_path / "out.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    groups = overfire.load_groups(
        jsonl_path=str(path), field_names=["reasoning", "suggestion"]
    )
    assert groups == [["You anchored it.", "Reread the close."]]


def test_a_field_name_that_matches_nothing_yields_no_groups(tmp_path) -> None:
    """A typo'd field name must be distinguishable from a clean corpus.

    It returns empty, and the CLI turns empty into exit 2 rather than a table of
    zeroes — see the next test.
    """
    path = tmp_path / "out.jsonl"
    path.write_text(json.dumps({"reasoning": "hi"}) + "\n", encoding="utf-8")
    assert overfire.load_groups(jsonl_path=str(path), field_names=["nope"]) == []


def test_the_cli_fails_rather_than_reporting_zero_over_firing(
    tmp_path, capsys
) -> None:
    path = tmp_path / "out.jsonl"
    path.write_text(json.dumps({"reasoning": "hi"}) + "\n", encoding="utf-8")
    code = overfire.main(["--jsonl", str(path), "--field", "absent"])
    assert code == 2
    assert "no documents found" in capsys.readouterr().err


def test_jsonl_without_a_field_name_is_an_error(tmp_path) -> None:
    path = tmp_path / "out.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="--field"):
        overfire.load_groups(jsonl_path=str(path))


def test_blank_documents_do_not_pad_the_denominator(tmp_path) -> None:
    path = tmp_path / "texts.txt"
    path.write_text("Reread the opening.\n\n   \n", encoding="utf-8")
    groups = overfire.load_groups(texts_path=str(path))
    assert groups == [["Reread the opening."]]


def test_a_sentence_initial_verb_survives_the_default_but_not_the_level_below() -> None:
    """Pins the mechanism the default was chosen for, on the smallest case.

    ``gazetteer`` has no filter for a candidate whose only evidence is a
    sentence-initial capital, so it eats the first word of an imperative.
    Passing the given-name oracle gates that filter on. This is one concrete
    instance of the 17-vs-8 outbound result, kept as a test so the mechanism
    cannot quietly change without the comparison being re-run.
    """
    imperative = [["Reread the opening paragraph before you revise it."]]
    bare = overfire.measure(imperative, NAMES_GAZETTEER)
    full = overfire.measure(imperative, NAMES_LOWERCASE)
    assert "Reread" in bare.by_span
    assert "Reread" not in full.by_span
