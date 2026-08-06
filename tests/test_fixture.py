"""Tests for the PII fixture itself, and the invariants it checks.

Two jobs, and the first is the one that is easy to skip:

1. **The fixture must be well-formed.** Every span's literal has to actually
   appear in its sentence, verdicts have to be unambiguous, and the held-out
   split has to be non-empty in both directions. A fixture with a typo'd literal
   reports a permanent leak that no detector can fix, and a fixture whose
   held-out set is empty reports a tuned number as a generalisation number. Both
   failures are silent from inside the harness.

2. **The invariant checker must have teeth.** ``check_frame`` returning ``[]``
   has to mean something, so each violation kind is exercised against text
   constructed to trip it. A checker nothing can fail is a green light with a
   comment on it.

The shipped classifier's *behaviour* against the fixture is pinned at the bottom,
including the legs that are currently red. Those are the baseline, not bugs in
the fixture: if one flips to green, the detector work landed and the recorded
numbers need updating rather than the test deleting.
"""

from __future__ import annotations

import re

import pytest

from vicary.eval.fixture import (
    ALL_FRAMES,
    FIXTURE_VERSION,
    INTERSECTION_FRAMES,
    KEEP_FRAMES,
    KNOWN_PLACEHOLDERS,
    RECALL_FRAMES,
    STRUCTURED_FRAMES,
    VERDICT_KEEP,
    VERDICT_REDACT,
    Frame,
    Span,
    align,
    check_frame,
    fixture_identity,
    frames,
    is_asap_token,
    is_own_identity,
    leak_probes,
    restore,
    round_trips,
)
from vicary.local_classifier import LocalNameClassifier


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


def _mask(text: str) -> str:
    return unnumber(LocalNameClassifier(fixture_identity()).mask(text).text)


def _kinds(frame: Frame, masked: str) -> set[str]:
    return {v.kind for v in check_frame(frame, masked)}


# ---------------------------------------------------------------------------
# The fixture is well-formed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("frame", ALL_FRAMES, ids=lambda f: f.frame_id)
def test_every_span_literal_appears_in_its_sentence(frame: Frame) -> None:
    """A literal that is not in the sentence is scored as masked, forever."""
    for span in frame.spans:
        assert span.literal in frame.sentence, (
            f"{frame.frame_id}: {span.literal!r} is not in the sentence"
        )


@pytest.mark.parametrize("frame", ALL_FRAMES, ids=lambda f: f.frame_id)
def test_every_span_has_a_known_verdict(frame: Frame) -> None:
    for span in frame.spans:
        assert span.verdict in {VERDICT_REDACT, VERDICT_KEEP}


@pytest.mark.parametrize("frame", ALL_FRAMES, ids=lambda f: f.frame_id)
def test_expected_placeholders_are_ones_something_could_emit(frame: Frame) -> None:
    for span in frame.spans:
        if span.expect is not None:
            assert span.expect in KNOWN_PLACEHOLDERS


@pytest.mark.parametrize("frame", ALL_FRAMES, ids=lambda f: f.frame_id)
def test_a_keep_span_is_not_inside_a_redact_span(frame: Frame) -> None:
    """Overlapping opposite verdicts are unscoreable, not hard.

    'Lincoln Memorial' (keep) beside 'Akron' (redact) is fine. 'Lincoln' (keep)
    *inside* a redact literal would make both verdicts true at once.
    """
    for keep in frame.keep_spans:
        for redact in frame.redact_spans:
            assert keep.literal not in redact.literal, (
                f"{frame.frame_id}: keep {keep.literal!r} sits inside "
                f"redact {redact.literal!r}"
            )


def test_frame_ids_are_unique() -> None:
    ids = [f.frame_id for f in ALL_FRAMES]
    assert len(ids) == len(set(ids))


def test_both_splits_are_populated_in_both_directions() -> None:
    """The held-out number is the only honest one, so it has to exist.

    Held out on *both* verdicts: a held-out set with no KEEP spans reports
    generalised recall with no generalised precision beside it, which is how
    "mask everything" scores well.
    """
    for group in (RECALL_FRAMES, KEEP_FRAMES, INTERSECTION_FRAMES):
        assert any(f.held_out for f in group)
        assert any(not f.held_out for f in group)

    held = frames(held_out=True)
    assert any(s.verdict == VERDICT_REDACT for f in held for s in f.spans)
    assert any(s.verdict == VERDICT_KEEP for f in held for s in f.spans)


def test_the_third_party_name_gap_has_more_than_one_frame() -> None:
    """The defect that motivated this module, pinned so it cannot come back.

    The previous fixture had one third-party name in one syntactic frame, so a
    detector keying on 'my cousin ___' scored 100%. Several distinct frames, and
    most of them held out, is the minimum that makes a recall number quotable.
    """
    third_party = [
        f for f in RECALL_FRAMES
        if any(not is_own_identity(s) for s in f.spans)
    ]
    assert len(third_party) >= 8
    assert sum(1 for f in third_party if f.held_out) >= 4


def test_the_precedence_pair_is_present_with_matching_syntax() -> None:
    """'my cousin X' and 'my inspiration, X' must both be here, opposed.

    They are the reason the discriminator cannot be syntactic, so a fixture
    missing either one cannot evaluate a notability filter at all.
    """
    cousin = next(f for f in ALL_FRAMES if f.frame_id == "kinship-possessive")
    inspiration = next(f for f in ALL_FRAMES
                       if f.frame_id == "notable-possessive")
    assert cousin.sentence.startswith("My cousin")
    assert "My inspiration," in inspiration.sentence
    assert cousin.spans[0].verdict == VERDICT_REDACT
    assert inspiration.spans[0].verdict == VERDICT_KEEP


def test_structured_entities_stay_covered() -> None:
    """The eight legs already at 100% must not fall out of the fixture."""
    entities = {s.entity for f in STRUCTURED_FRAMES for s in f.spans}
    assert entities >= {
        "ADDRESS", "PHONE", "EMAIL", "AGE", "US_SOCIAL_SECURITY_NUMBER",
        "URL", "USERNAME", "CREDIT_DEBIT_CARD_NUMBER",
    }


def test_selection_by_group_and_split() -> None:
    assert frames(groups=("keep",)) == KEEP_FRAMES
    assert set(frames(groups=("recall", "keep"))) == set(RECALL_FRAMES) | set(KEEP_FRAMES)
    assert all(f.held_out for f in frames(held_out=True))
    with pytest.raises(ValueError):
        frames(groups=("nope",))


def test_the_version_is_stamped() -> None:
    """Every eval row carries this; an unstamped record is a foreign record."""
    assert FIXTURE_VERSION


# ---------------------------------------------------------------------------
# align() recovers spans without asking the masker
# ---------------------------------------------------------------------------


def test_alignment_recovers_what_each_placeholder_replaced() -> None:
    result = align("I met Ada Lovelace in Bath.", "I met {NAME} in {LOCATION}.")
    assert result.ok
    assert result.pairs == [("{NAME}", "Ada Lovelace"), ("{LOCATION}", "Bath")]
    assert result.collateral_chars == len("Ada Lovelace") + len("Bath")


def test_alignment_handles_a_placeholder_at_each_edge() -> None:
    result = align("Ada met Bath", "{NAME} met {LOCATION}")
    assert result.ok
    assert result.pairs == [("{NAME}", "Ada"), ("{LOCATION}", "Bath")]


def test_alignment_reports_unchanged_text_as_clean() -> None:
    result = align("Nothing to see here.", "Nothing to see here.")
    assert result.ok
    assert result.pairs == []


def test_alignment_fails_when_prose_was_rewritten() -> None:
    """The invariant is delete-and-insert. Anything else is a rewrite."""
    result = align("I met Ada Lovelace.", "I saw {NAME}.")
    assert not result.ok
    assert result.reason


def test_alignment_fails_when_text_changed_with_no_placeholder() -> None:
    result = align("I met Ada.", "I met.")
    assert not result.ok


# ---------------------------------------------------------------------------
# The checker has teeth: one constructed red per violation kind
# ---------------------------------------------------------------------------


_FRAME = Frame(
    frame_id="probe",
    sentence="My cousin Terrence Okonkwo met Vincent van Gogh in Akron.",
    spans=(
        Span("NAME", "Terrence Okonkwo", expect="{NAME}"),
        Span("NAME", "Vincent van Gogh", verdict=VERDICT_KEEP),
        Span("LOCATION", "Akron", expect="{LOCATION}"),
    ),
)


def test_a_perfect_mask_has_no_violations() -> None:
    masked = "My cousin {NAME} met Vincent van Gogh in {LOCATION}."
    assert check_frame(_FRAME, masked) == []


def test_a_surviving_literal_is_a_leak() -> None:
    masked = "My cousin Terrence Okonkwo met Vincent van Gogh in {LOCATION}."
    assert "leak" in _kinds(_FRAME, masked)


def test_half_a_masked_name_is_a_partial_leak() -> None:
    """The whole-literal test alone calls this a success. It identifies a person."""
    masked = "My cousin {NAME} Okonkwo met Vincent van Gogh in {LOCATION}."
    kinds = _kinds(_FRAME, masked)
    assert "partial-leak" in kinds
    assert "leak" not in kinds


def test_masking_a_notable_figure_is_a_precision_failure() -> None:
    masked = "My cousin {NAME} met {NAME} in {LOCATION}."
    assert "keep-destroyed" in _kinds(_FRAME, masked)


def test_the_wrong_placeholder_is_reported_separately_from_a_leak() -> None:
    """Masked, so not a leak. Mistyped, which is what a student would read."""
    masked = "My cousin {NAME} met Vincent van Gogh in {NAME}."
    kinds = _kinds(_FRAME, masked)
    assert "wrong-type" in kinds
    assert "leak" not in kinds


def test_a_malformed_placeholder_is_caught() -> None:
    masked = "My cousin {NAM} met Vincent van Gogh in {LOCATION}."
    assert "unknown-placeholder" in _kinds(_FRAME, masked)


def test_one_token_for_two_originals_is_not_restorable() -> None:
    """The deficit numbering exists to fix, measured rather than assumed.

    Two different names both become ``{NAME}``, so a restore map keyed on the
    token has to guess. This is why the ASAP convention numbers them.
    """
    frame = Frame(
        frame_id="two-names",
        sentence="Deshawn and Terrence both stayed.",
        spans=(Span("NAME", "Deshawn"), Span("NAME", "Terrence")),
    )
    kinds = _kinds(frame, "{NAME} and {NAME} both stayed.")
    assert "not-restorable" in kinds


def test_numbered_placeholders_would_restore() -> None:
    """The fix, demonstrated: distinct tokens round-trip exactly."""
    masked = "{NAME} and {NAME} both stayed."
    assert restore(masked, {"{NAME}": "Deshawn"}) == "Deshawn and Deshawn both stayed."
    numbered = "{NAME} and {SCHOOL} both stayed."
    assert restore(
        numbered, {"{NAME}": "Deshawn", "{SCHOOL}": "Terrence"}
    ) == "Deshawn and Terrence both stayed."


def test_round_trips_is_true_only_when_the_original_comes_back() -> None:
    one = Frame("one", "Deshawn stayed.", (Span("NAME", "Deshawn"),))
    assert round_trips(one, "{NAME} stayed.")
    two = Frame(
        "two", "Deshawn and Terrence stayed.",
        (Span("NAME", "Deshawn"), Span("NAME", "Terrence")),
    )
    assert not round_trips(two, "{NAME} and {NAME} stayed.")


@pytest.mark.parametrize(
    "literal,expected",
    [
        ("Terrence Okonkwo", ("Terrence", "Okonkwo")),
        ("Vincent van Gogh", ("Vincent", "Gogh")),      # 'van' is too weak
        ("Priya Raghunathan-Bell", ("Priya", "Raghunathan", "Bell")),
        ("J. Okonkwo", ("Okonkwo",)),                   # 'J.' proves nothing
        ("Mrs. Okonkwo", ("Okonkwo",)),
    ],
)
def test_leak_probes_are_the_tokens_that_would_identify_someone(
    literal: str, expected: tuple[str, ...]
) -> None:
    assert leak_probes(Span("NAME", literal)) == expected


def test_structured_entities_get_no_partial_leak_probes() -> None:
    """A half-masked phone number is not a partial identity, it is noise."""
    assert leak_probes(Span("PHONE", "(330) 555-0148")) == ()


@pytest.mark.parametrize(
    "region,expected",
    [
        ("@PERSON1", True),
        ("@CAPS3", True),
        ("@ORGANIZATION1", True),
        ("@LOCATION2", True),
        ("@DATE1", True),
        ("@NUM1", True),
        (" @PERSON1 ", True),
        ("@margie_dw2011", False),   # a real handle, lowercase
        ("@PERSON1 and @PERSON2", False),  # a region, not a token
        ("Terrence", False),
    ],
)
def test_the_asap_token_split_separates_two_unrelated_metrics(
    region: str, expected: bool
) -> None:
    """Without this split, over-firing reads as 21 spans/essay when it is zero.

    The shipped ``{USERNAME}`` pattern matches every ``@``-token in the corpus,
    so summing the two legs produces a catastrophic-looking precision number
    made entirely of regions that never contained PII.
    """
    assert is_asap_token(region) is expected


def test_upstream_anonymization_markers_survive_redaction() -> None:
    """Already-redacted text must not be redacted again.

    Masking ``@PERSON1`` adds no privacy — the PII is already gone — and costs
    real information: a model trained on the corpus saw these tokens at ~22 per
    essay, so rewriting them to ``{USERNAME}`` moves its input away from the
    training distribution. Until 2026-08-05 the USERNAME pattern ate all 14
    kinds, which would have made a placeholder-alignment replay over ASAP
    measure the rewrite instead of the alignment.
    """
    text = "Last summer @PERSON1 and I visited @LOCATION2 with @CAPS3 in @MONTH1."
    assert _mask(text) == text


def test_a_real_handle_is_still_masked_beside_the_markers() -> None:
    """The exclusion must be narrow: a guard that spares everything is no guard."""
    masked = _mask("@PERSON1 told me, and my handle is @margie_dw2011 by the way.")
    assert "@PERSON1" in masked
    assert "@margie_dw2011" not in masked
    assert masked.count("{USERNAME}") == 1


@pytest.mark.parametrize(
    "text,masked_count",
    [
        ("@PERSONAL is my brand", 1),   # longer word, not a marker
        ("@person1 is my handle", 1),   # lowercase, not a marker
        ("@CAPS is a marker", 0),       # unnumbered marker
        ("@NUM12 is a marker", 0),
    ],
)
def test_the_marker_exclusion_does_not_overreach(
    text: str, masked_count: int
) -> None:
    assert _mask(text).count("{USERNAME}") == masked_count


# ---------------------------------------------------------------------------
# The shipped classifier against the fixture — the recorded baseline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("frame", STRUCTURED_FRAMES, ids=lambda f: f.frame_id)
def test_structured_frames_are_fully_masked_today(frame: Frame) -> None:
    """The eight legs at 100%. A regression here is a shipped privacy leak."""
    masked = _mask(frame.sentence)
    for span in frame.redact_spans:
        assert span.literal not in masked, f"{span.entity} leaked: {masked}"


def test_the_students_own_name_is_still_masked() -> None:
    frame = next(f for f in ALL_FRAMES if f.frame_id == "student-own-name")
    assert "Marguerite Delacroix-Whitfield" not in _mask(frame.sentence)


@pytest.mark.parametrize("frame", KEEP_FRAMES, ids=lambda f: f.frame_id)
def test_precision_is_perfect_today_because_nothing_fires(frame: Frame) -> None:
    """100% precision, for the least reassuring reason available.

    The shipped classifier keeps every notable figure because it does not detect
    names it was not handed. That is a real number and it is also the ceiling a
    candidate generator has to defend: this test is what turns red first when
    Rung 1 over-fires, which is the whole point of having it.
    """
    masked = _mask(frame.sentence)
    for span in frame.keep_spans:
        assert span.literal in masked, f"over-redacted: {masked}"


def test_every_third_party_name_leaks_today() -> None:
    """The documented boundary, now measured across frames instead of one.

    The shipped classifier does not miss third-party names sometimes — it misses
    them always, in every frame. Identity interpolation cannot reach a name it
    was not given, and nothing else in the pipeline tries.

    When this starts FAILING the detector work has landed. Update the recorded
    recall in ``local_classifier.py`` and the project docs rather than deleting
    the test, and re-run the harness for the held-out number.
    """
    leaked, masked_any = [], []
    for frame in RECALL_FRAMES:
        third_party = [s for s in frame.redact_spans if not is_own_identity(s)]
        if not third_party:
            continue
        out = _mask(frame.sentence)
        for span in third_party:
            (leaked if span.literal in out else masked_any).append(
                f"{frame.frame_id}:{span.literal}"
            )
    assert not masked_any, f"a third-party name got masked: {masked_any}"
    assert len(leaked) >= 8


def test_masking_is_idempotent_on_every_frame() -> None:
    """A second pass must not re-mask its own placeholders.

    Both directions run the same classifier and the outbound pass sees text that
    has already been through the inbound one, so non-idempotence would corrupt
    real feedback rather than a fixture.
    """
    for frame in ALL_FRAMES:
        once = _mask(frame.sentence)
        assert _mask(once) == once, frame.frame_id


def test_no_frame_trips_the_structural_invariants_today() -> None:
    """Whatever the classifier's recall, its transform must stay well-formed.

    Only the verdict kinds are allowed to be red at this baseline. A structural
    kind — a rewrite, a malformed token, a partial name — is a bug in the masker
    rather than a gap in it.
    """
    structural = {"chunk-alignment", "unknown-placeholder", "partial-leak"}
    found: dict[str, set[str]] = {}
    for frame in ALL_FRAMES:
        kinds = _kinds(frame, _mask(frame.sentence)) & structural
        if kinds:
            found[frame.frame_id] = kinds
    assert not found, found
