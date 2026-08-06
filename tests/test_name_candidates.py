"""Tests for third-party name candidate generation.

This is the leg that took held-out recall on the fixture from **0.0% to 90.5%**
and precision on KEEP spans from 100% to 9.1%. Both halves of that trade are
pinned here: the frames it now catches, and the public figures it destroys
without a notability oracle — because the second number is what the gazetteer
has to fix, and a test suite that only covers the win would let it ship without.
"""

from __future__ import annotations

import re

import pytest

from vicary.eval.fixture import (
    RECALL_FRAMES,
    Frame,
    fixture_identity,
    is_own_identity,
)
from vicary.local_classifier import LocalNameClassifier
from vicary.name_candidates import (
    find_candidates,
    is_public_landmark,
    mask_candidates,
)

#: The frames the capitalisation route cannot reach on its own. Still named
#: rather than deleted: closing them needed a given-name list, and the list is
#: supplied per-call, so the default path continues to miss them by construction.
KNOWN_MISSES: frozenset[str] = frozenset({"lowercase-writing"})

#: A given-name oracle standing in for the gazetteer's tier. Local so these tests
#: stay unit tests — the real tier is exercised in ``test_gazetteer.py``, and a
#: test that needs the 1.8 MB asset to prove a scan rule is testing the asset.
GIVEN_NAMES: frozenset[str] = frozenset({"terrence", "marisol", "maria", "vincent"})


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


def is_given(token: str) -> bool:
    """Stand-in for ``gazetteer.is_common_given_name``, folding as it does.

    The fold is not incidental. The capitalised route consults this oracle with
    the token *as written* ("Terrence"), the lowercase route with an
    already-lower-cased one, and the real oracle normalises. A stand-in that only
    matched the lowercase call would pass while the shipped path failed.
    """
    return token.lower().strip(".,'’") in GIVEN_NAMES


def _mask(text: str, **kw) -> str:
    """Masked text with placeholder indices stripped — see :func:`unnumber`."""
    return unnumber(_mask_numbered(text, **kw))


def _mask_numbered(text: str, **kw) -> str:
    return LocalNameClassifier(fixture_identity(), candidates=True, **kw).mask(text).text


def _mask_lowercase(text: str, **kw) -> str:
    """Mask with the lowercase route on, defaulting to the stand-in tier."""
    kw.setdefault("given_name", is_given)
    return _mask(text, **kw)


def _names(text: str) -> list[str]:
    return [c.text for c in find_candidates(text)]


# ---------------------------------------------------------------------------
# The frames it now catches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frame",
    [f for f in RECALL_FRAMES if f.frame_id not in KNOWN_MISSES],
    ids=lambda f: f.frame_id,
)
def test_third_party_names_are_masked_in_every_supported_frame(frame: Frame) -> None:
    masked = _mask(frame.sentence)
    for span in frame.redact_spans:
        if is_own_identity(span):
            continue
        assert span.literal not in masked, f"{frame.frame_id}: {masked}"


@pytest.mark.parametrize("frame_id", sorted(KNOWN_MISSES))
def test_the_known_misses_need_the_given_name_oracle(frame_id: str) -> None:
    """Without a given-name list, lowercase prose is missed by construction.

    Pinned in both directions so the docs and the code cannot drift apart: this
    half asserts the default path still misses, and
    :func:`test_the_lowercase_route_closes_the_last_recall_frame` asserts the
    oracle closes it. Held-out recall is 90.5% without and 100% with.
    """
    frame = next(f for f in RECALL_FRAMES if f.frame_id == frame_id)
    masked = _mask(frame.sentence)
    assert any(s.literal in masked for s in frame.redact_spans)


# ---------------------------------------------------------------------------
# The lowercase route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("frame_id", sorted(KNOWN_MISSES))
def test_the_lowercase_route_closes_the_last_recall_frame(frame_id: str) -> None:
    """The frame that took held-out recall from 90.5% to 100%."""
    frame = next(f for f in RECALL_FRAMES if f.frame_id == frame_id)
    masked = _mask_lowercase(frame.sentence)
    for span in frame.redact_spans:
        assert span.literal not in masked, masked


def test_the_lowercase_route_stops_at_the_surname() -> None:
    """It masks the name and not the verb after it.

    Reaching two ordinary tokens instead of one produced "terrence okonkwo
    showed" here, because "showed" is not on a stoplist of a few hundred words.
    A third token is allowed only across a name particle.
    """
    masked = _mask_lowercase("then terrence okonkwo showed up and everything changed.")
    assert masked == "then {NAME} showed up and everything changed."


def test_a_particle_carries_the_span_to_a_third_token() -> None:
    assert _mask_lowercase("maria de cruz lent me her notes.") == (
        "{NAME} lent me her notes."
    )


def test_a_lowercase_span_does_not_end_on_a_particle() -> None:
    """"maria de," is the name plus a fragment of the next clause."""
    assert "{NAME}" not in _mask_lowercase("my teacher maria de, well, she helped.")


def test_a_bare_lowercase_given_name_is_not_masked() -> None:
    """The named cost of requiring two tokens, and why it is worth paying.

    Plenty of given names are also ordinary English words — hope, grace, mark,
    little — so firing on a single lowercase token would put the whole given-name
    tier into the over-firing number. Measured on 25 ASAP essays, requiring two
    tokens holds over-firing to 3.72 spans/essay against 5.16 for one token.
    """
    assert _mask_lowercase("terrence and i stayed up late.") == (
        "terrence and i stayed up late."
    )


@pytest.mark.parametrize(
    "text",
    [
        "we got a little snack after the play ground.",
        "the guy just walked away.",
        "she is our joy every single day.",
        "i saw no marisol anywhere that day.",
    ],
)
def test_a_determiner_before_the_seed_suppresses_it(text: str) -> None:
    """English does not put a bare determiner in front of a given name.

    This is the biggest single lever on over-firing and it is structural rather
    than a word blacklist, so it does not need extending every time a corpus
    turns up a new ordinary word. Measured on 25 ASAP essays, "a" alone preceded
    12 of ~34 lowercase over-fire seeds and determiners 22 of them; adding this
    took over-firing from 5.16 to 4.08 spans/essay at no recall cost.
    """
    assert _mask_lowercase(text, given_name={"little", "guy", "joy",
                                             "marisol"}.__contains__) == text


def test_a_determiner_further_back_does_not_suppress() -> None:
    """Only a directly-adjacent determiner counts."""
    masked = _mask_lowercase("the day terrence okonkwo arrived was hot.")
    assert "terrence okonkwo" not in masked


def test_an_adjective_before_the_seed_is_a_known_residual() -> None:
    """The limit of the determiner rule, pinned rather than left to be found.

    "dumb little comments" and "silly little song" put a *modifier* before the
    seed, not a determiner, so the rule cannot see them. This is most of the
    remaining 3.92 spans/essay, and reaching it needs word frequency — a
    build-time list the asset does not carry — rather than another regex.
    Shrink this test when that lands; do not delete it.
    """
    masked = _mask_lowercase(
        "he was popping off with dumb little comments.",
        given_name={"little"}.__contains__,
    )
    assert "little comments" not in masked


def test_an_unapostrophized_contraction_is_not_a_name() -> None:
    """"im" is a given name in Wikidata, and how "im going" became a candidate.

    The apostrophized form is stripped by ``_CLITICS``; the spelling students
    actually type has no clitic boundary to find, so it is stoplisted directly.
    """
    for text in ("im going to have to stop.", "im glad i found you."):
        assert _mask_lowercase(text, given_name={"im"}.__contains__) == text


def test_a_comma_ends_a_lowercase_span() -> None:
    """Only whitespace may sit between two tokens of one span."""
    assert _mask_lowercase("terrence, my cousin, drove us.") == (
        "terrence, my cousin, drove us."
    )


def test_a_stopword_ends_a_lowercase_span() -> None:
    assert _mask_lowercase("i had hope that day and marisol was there.") == (
        "i had hope that day and marisol was there."
    )


def test_the_lowercase_route_is_off_without_an_oracle() -> None:
    """Supplying the oracle is what turns the route on — nothing else."""
    text = "then terrence okonkwo showed up."
    assert _mask(text) == text


def test_a_lowercase_notable_is_still_kept() -> None:
    """Lowercase spans go through the same notability gate as capitalised ones."""
    masked = _mask_lowercase(
        "my inspiration is vincent van gogh.",
        notable={"vincent van gogh"}.__contains__,
    )
    assert masked == "my inspiration is vincent van gogh."


def test_the_capitalised_route_keeps_its_claim() -> None:
    """A lowercase span overlapping a capitalised find is dropped, not merged.

    Two candidates over the same characters mask the outer one and leave the
    inner placeholder's braces behind as debris.
    """
    masked = _mask_lowercase("My cousin Terrence Okonkwo came over that summer.")
    assert masked == "My cousin {NAME} came over that summer."


def test_the_lowercase_route_leaves_upstream_markers_alone() -> None:
    text = "then @PERSON1 showed up with {NAME} and marisol ybarra."
    masked = _mask_lowercase(text)
    assert "@PERSON1" in masked
    assert masked.count("{NAME}") == 2
    assert "marisol ybarra" not in masked


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Terrence and I stayed up late.", ["Terrence"]),
        ("My cousin Terrence Okonkwo came over.", ["Terrence Okonkwo"]),
        ('"We should go," said Marisol, and we did.', ["Marisol"]),
        ("Coach Bramwell made us run laps.", ["Coach Bramwell"]),
        ("Mrs. Okonkwo taught me the trick.", ["Mrs. Okonkwo"]),
        ("J. Okonkwo sat behind me.", ["J. Okonkwo"]),
        ("Priya Raghunathan-Bell lent me her notes.", ["Priya Raghunathan-Bell"]),
        ("I read about Vincent van Gogh.", ["Vincent van Gogh"]),
        ("Ayaan Chaudhary, my neighbor, drove.", ["Ayaan Chaudhary"]),
    ],
)
def test_the_span_shape_is_right(text: str, expected: list[str]) -> None:
    """The honorific, the initial and the particle are part of the name.

    Masking "Okonkwo" out of "Mrs. Okonkwo" leaves the relationship and the
    surname's position behind. Splitting "Vincent van Gogh" into two candidates
    forces the gazetteer to know both halves.
    """
    assert _names(text) == expected


def test_a_possessive_is_masked_with_the_name() -> None:
    masked = _mask("Terrence's older brother drove us to the game.")
    assert "Terrence" not in masked


def test_offsets_locate_the_span_exactly() -> None:
    text = "My cousin Terrence Okonkwo came over."
    candidate = next(c for c in find_candidates(text) if c.text == "Terrence Okonkwo")
    assert text[candidate.start : candidate.end] == "Terrence Okonkwo"


# ---------------------------------------------------------------------------
# What it must NOT touch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The author argues for shorter commutes.",
        "Then we went home and I fell asleep.",
        "Everyone in my class knows about it.",
        "I'm not sure that was the right call.",
        "As I said, laughter is the best medicine.",
        "Being patient is harder than it looks.",
        "Getting there was the hardest part of all.",
        "Call me tomorrow if you change your mind.",
        "This is not something I would ever do again.",
        "However, there was one thing I had not tried.",
    ],
)
def test_ordinary_prose_generates_no_candidates(text: str) -> None:
    """The stoplist is the only thing between this and "mask every capital".

    Sentence-initial capitalisation is the dominant source of false positives,
    and a contraction defeats the lookup entirely unless the clitic is stripped
    — "I'm" and "As" were the two most common over-fires measured on real ASAP
    prose before that was fixed.
    """
    assert _names(text) == []


def test_our_own_placeholders_are_left_alone() -> None:
    """Idempotence. Both passes run this classifier and outbound sees inbound."""
    once = _mask("Marguerite and Deshawn both stayed after class.")
    assert _mask(once) == once
    assert "{NAME}" in once


def test_upstream_anonymization_markers_are_left_alone() -> None:
    """``@PERSON1`` is already redacted; ``PERSON`` is not a candidate.

    The ``@`` is not part of a capitalised-word match, so before this guard every
    ASAP marker's kind-word was generated as a candidate — 20 spans per essay of
    "over-firing" that was really this.
    """
    text = "Last summer @PERSON1 and I went to @LOCATION2 in @MONTH1."
    assert _names(text) == []
    assert _mask(text) == text


def test_the_masking_is_idempotent_over_a_whole_paragraph() -> None:
    text = ("Terrence Okonkwo met Mrs. Okonkwo at Progressive Insurance. "
            "Later, Deshawn and Marisol Ybarra arrived from Akron.")
    once = _mask(text)
    assert _mask(once) == once


# ---------------------------------------------------------------------------
# Typing and the keep legs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,name,placeholder",
    [
        ("He works at Progressive Insurance now.", "Progressive Insurance",
         "{ORGANIZATION}"),
        ("She goes to Riverside Academy downtown.", "Riverside Academy",
         "{ORGANIZATION}"),
        ("I met Terrence Okonkwo there.", "Terrence Okonkwo", "{NAME}"),
    ],
)
def test_an_org_suffix_changes_the_placeholder(
    text: str, name: str, placeholder: str
) -> None:
    """Inbound the mask is what matters; outbound the type is what a student reads."""
    candidate = next(c for c in find_candidates(text) if c.text == name)
    assert candidate.placeholder == placeholder


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Lincoln Memorial", True),
        ("Grand Canyon", True),
        ("Central Park", True),
        ("Lincoln", False),          # bare surname is not a landmark
        ("Terrence Okonkwo", False),
        ("Memorial", False),         # single token proves nothing
    ],
)
def test_public_landmarks_are_kept_without_a_gazetteer(
    name: str, expected: bool
) -> None:
    assert is_public_landmark(name) is expected


def test_the_topical_allowlist_keeps_a_name_from_the_prompt() -> None:
    """The ``prompt_context`` leg: exact, free, zero false positives."""
    text = "As Malcolm Gladwell argues, the trend is older than it looks."
    assert "Malcolm Gladwell" not in _mask(text)
    kept = _mask(text, topical=frozenset({"Malcolm Gladwell"}))
    assert "Malcolm Gladwell" in kept


def test_the_topical_allowlist_is_case_insensitive() -> None:
    text = "I read Toni Morrison twice."
    assert "Toni Morrison" in _mask(text, topical=frozenset({"toni morrison"}))


def test_a_notability_oracle_keeps_what_it_claims() -> None:
    text = "My inspiration, Vincent van Gogh, painted through his worst years."
    assert "Vincent van Gogh" not in _mask(text)
    kept = mask_candidates(text, notable=lambda n: n == "Vincent van Gogh")[0]
    assert "Vincent van Gogh" in kept


def test_the_oracle_does_not_rescue_a_private_name() -> None:
    """A guard that keeps everything is not a filter.

    The precedence is notable → keep, everything else → redact. An oracle that
    answers True for the notable name must still leave the private one masked in
    the same sentence, which is the case a substring gazetteer gets wrong.
    """
    text = "I wrote about Vincent van Gogh for Mrs. Okonkwo's class."
    out = mask_candidates(text, notable=lambda n: "Gogh" in n)[0]
    assert "Vincent van Gogh" in out
    assert "Okonkwo" not in out


_PUBLIC_FIGURES = ("Vincent van Gogh", "Henry David Thoreau", "Toni Morrison",
                   "Rosa Parks", "Malcolm Gladwell")


@pytest.mark.parametrize("figure", _PUBLIC_FIGURES)
def test_with_no_oracle_every_public_figure_is_destroyed(figure: str) -> None:
    """Why the flag defaults off. Generation alone is a product defect.

    Recorded as a passing test asserting a *failing product behaviour*, so the
    trade cannot be forgotten: candidate generation buys recall and spends
    precision, and shipping it without an oracle means a student reads feedback
    about their essay on ``{NAME}``.
    """
    assert figure not in _mask(f"I admire {figure} more than anyone.")


@pytest.mark.parametrize("figure", _PUBLIC_FIGURES)
def test_the_gazetteer_is_what_makes_generation_shippable(figure: str) -> None:
    """The other half of the same trade, with the oracle supplied.

    Measured on the full fixture: KEEP precision 9.1% → 100% at **zero** recall
    cost (held-out recall stays 90.0%). The pair of tests above and here is the
    argument for the default: off without an oracle, on with one.
    """
    from vicary.gazetteer import is_notable

    kept = _mask(f"I admire {figure} more than anyone.", notable=is_notable)
    assert figure in kept


def test_masking_reports_a_count() -> None:
    _, n = mask_candidates("Terrence Okonkwo met Marisol Ybarra downtown.")
    assert n == 2


# ---------------------------------------------------------------------------
# Capitalisation is a clue, never the answer
# ---------------------------------------------------------------------------


def test_a_sentence_initial_ordinary_word_is_not_a_name() -> None:
    """The capital orthography required is evidence of nothing.

    Measured on 27 un-scrubbed student documents (CCSS Appendix C + a partner
    anchors): "Eventually", "Lastly", "Especially", "Unfortunatly" and about
    ninety more sentence-initial ordinary words were masked as names. On ASAP the
    same rule takes over-firing from 3.80 to 1.16 spans/essay.
    """
    masked = _mask_lowercase("Eventually the bus came. Lastly we all went home.")
    assert "Eventually" in masked
    assert "Lastly" in masked


def test_a_sentence_initial_name_survives_on_the_given_name_tier() -> None:
    """The second channel. "Terrence" is in the tier, "Eventually" is not."""
    assert "Terrence" not in _mask_lowercase("Terrence and I stayed up late.")


def test_a_sentence_initial_name_survives_on_the_writers_own_capitals() -> None:
    """The other second channel, and the one that needs no list at all.

    A writer who put a capital on "Okonkwo" mid-sentence has told us it is a name
    in this document. That testimony carries to the sentence-initial occurrence,
    which orthography would have capitalised regardless.
    """
    text = ("Okonkwo waited by the gate. I had known Okonkwo since third grade.")
    masked = _mask_lowercase(text)
    assert "Okonkwo" not in masked


def test_a_short_all_caps_run_is_emphasis_rather_than_a_name() -> None:
    """Informal writers shout in caps; "SLAM" is not a person.

    The mirror of the sentence-initial rule: a one- or two-word all-caps run
    inside mixed-case prose is the informal register's italics. A long run is a
    writer who has stopped using case, and the stoplist handles that instead --
    see :func:`test_a_long_all_caps_run_still_yields_the_name`.
    """
    masked = _mask_lowercase("The door went SLAM and then WHACK, right in front of me.")
    assert "SLAM" in masked
    assert "WHACK" in masked


def test_a_long_all_caps_run_still_yields_the_name() -> None:
    """The allcaps fixture frame, which the emphasis rule must not eat."""
    masked = _mask_lowercase(
        "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER DO THAT TO ME."
    )
    assert "DESHAWN PRITCHARD" not in masked


def test_the_document_capitalising_raises_the_bar_instead_of_closing_the_route() -> None:
    """The gate is a clue now, not a switch.

    It used to suppress the lowercase route outright in any document that marked
    its proper nouns with capitals, which cost **every** uncapitalised occurrence
    of a name in such a document -- and students who capitalise most names still
    miss some. Now the seed must additionally appear capitalised mid-sentence
    somewhere in the document: the writer's own testimony that this word is a
    name they slip on.
    """
    # Capitalises its proper nouns, and slips once on a name it capitalises
    # elsewhere. The old gate scored zero here by construction.
    text = ("I went to Chicago with Maria in July. later that week maria delgado "
            "drove us to the lake in Wisconsin.")
    masked = _mask_lowercase(text)
    assert "maria delgado" not in masked, masked

    # Same document shape, but the seed is never capitalised: an ordinary word
    # that happens to be a given name. It must stay.
    text = ("I went to Chicago with Amelia in July. we ate the sugar cubes right "
            "out of the bowl in Wisconsin.")
    assert "sugar cubes" in _mask_lowercase(text, given_name={"sugar"}.__contains__)


def test_the_no_oracle_arm_keeps_its_recall_maximal_character() -> None:
    """Requiring a second signal needs a second signal to exist.

    Without a given-name list the document's own capitals are the only channel,
    and a name mentioned once at a sentence start is then indistinguishable from
    "Eventually". So the rule is conditional on the oracle, and the no-oracle arm
    is byte-for-byte what it was: over-firing 3.04 spans/essay, KEEP precision
    7.7%. Measured, not assumed.
    """
    assert "Terrence" not in _mask("Terrence and I stayed up late.")


# ---------------------------------------------------------------------------
# Same-document surname corroboration
# ---------------------------------------------------------------------------


def _oracles():
    from vicary.gazetteer import (
        is_common_given_name,
        is_notable,
        is_title,
        is_title_prefix,
        notability,
    )

    return dict(
        notable=is_notable,
        title=is_title,
        title_prefix=is_title_prefix,
        given_name=is_common_given_name,
        notability_tier=notability,
    )


def test_a_full_name_in_the_document_keeps_its_bare_surname() -> None:
    """The defect this exists for, and it fails loudly without corroboration.

    Literary analysis names the author once and writes the surname for the rest
    of the essay. "Wright" cannot clear the short tier — Richard Wright is famous
    and "Wright" is a common American surname, and those two gates are in tension
    by construction — so before this rule the subject of the essay was destroyed.
    """
    text = ("Richard Wright wrote about hunger without flinching. Wright never "
            "lets the reader look away, and Wright's honesty is the whole point.")
    masked_off, n_off = mask_candidates(text, corroborate=False, **_oracles())
    masked_on, n_on = mask_candidates(text, corroborate=True, **_oracles())

    assert "{NAME}" in masked_off, (
        "control is not red — without corroboration the bare surname must be "
        "masked, or this test proves nothing"
    )
    assert "{NAME}" not in masked_on, masked_on
    assert n_on < n_off


def test_a_private_full_name_corroborates_nothing() -> None:
    """The gate. Only a name the oracle KEEPS may license a bare surname.

    A student's own third-party name must not bootstrap itself: "Terrence
    Okonkwo" is not notable, so bare "Okonkwo" stays masked. Without this the
    rule would keep every surname any two-word capitalised span ended in, which
    is every private name in the corpus.
    """
    text = ("My cousin Terrence Okonkwo drove us there. Okonkwo laughed the "
            "whole way and Okonkwo would not turn the radio down.")
    masked, _ = mask_candidates(text, corroborate=True, **_oracles())
    assert "Okonkwo" not in masked, masked


def test_a_kept_place_name_does_not_license_a_surname() -> None:
    """Measured, not hypothetical: "Pintos are from America" established "america".

    A place is not a person written first-name-then-surname, so it carries no
    evidence about a bare surname. Left ungated, the same mechanism lets "Lake
    Powell" keep a classmate's bare "Powell", which is a privacy leak sourced
    from a geography lookup.
    """
    from vicary.gazetteer import notability
    from vicary.name_candidates import corroborated_surnames, find_candidates

    text = ("We drove through South America last summer. Powell sat behind me "
            "and Powell would not stop kicking my seat.")
    candidates = find_candidates(text)
    established = corroborated_surnames(
        candidates, lambda n: True, tier=notability
    )
    assert "america" not in established, established

    masked, _ = mask_candidates(text, corroborate=True, **_oracles())
    assert "Powell" not in masked, masked


def test_only_the_bare_form_is_corroborated() -> None:
    """A shared surname is not a shared person.

    This is the same invariant that keeps "Priya Lincoln" out of the short tier,
    applied one level up: establishing "Wright" must not keep "Coach Wright" or
    "Priya Wright", because those are different people who happen to share a
    surname with the author under discussion.
    """
    text = ("Richard Wright wrote about hunger. Coach Wright read it aloud to us "
            "and Priya Wright cried at the end.")
    masked, _ = mask_candidates(text, corroborate=True, **_oracles())
    assert "Coach Wright" not in masked, masked
    assert "Priya Wright" not in masked, masked
    assert "Richard Wright" in masked, masked


def test_corroboration_folds_the_possessive() -> None:
    """"Wright's" and "Wright" must fold together.

    On the un-scrubbed student corpus the possessive was 10 of the 27 masked
    "Wright" spans, so a rule that only reached the plain form would leave most
    of the defect in place while reporting a fix.
    """
    from vicary.name_candidates import _bare_surname_key, surname_forms

    assert _bare_surname_key("Wright’s") == "wright"
    assert _bare_surname_key("Wright's") == "wright"
    assert surname_forms("Richard Wright’s") == ("wright",)
    # A first name is never a corroborating form; see surname_forms.
    assert "richard" not in surname_forms("Richard Wright")


def test_a_particle_surname_corroborates_both_forms() -> None:
    """"van Gogh" and "Gogh" are both the bare form of the same name."""
    from vicary.name_candidates import surname_forms

    assert surname_forms("Vincent van Gogh") == ("gogh", "van gogh")


# ---------------------------------------------------------------------------
# Numbered placeholders — reversibility
# ---------------------------------------------------------------------------


def test_two_different_people_get_two_different_placeholders() -> None:
    """The defect numbering exists for, and the control is red.

    Unnumbered, one ``{NAME}`` meant "Marisol" in one clause and "Terrence
    Okonkwo" in the next, so no map keyed on the token could put either back —
    37 ``not-restorable`` violations across 25 injected essays, 36% round-trip.
    """
    text = "Marisol waved at Terrence Okonkwo across the gym."
    numbered = _mask_numbered(text, given_name=is_given)
    assert "{NAME_1}" in numbered and "{NAME_2}" in numbered, numbered

    unnumbered = _mask_numbered(text, given_name=is_given,
                                number_placeholders=False)
    assert unnumbered.count("{NAME}") == 2, (
        "control is not red — if the unnumbered arm does not collide, this test "
        "proves nothing about numbering"
    )


def test_the_same_person_keeps_one_placeholder() -> None:
    """Stability, which is a coherence property and not only a restore property.

    A name written three times must mask to ONE placeholder. ``{NAME_1} argued …
    {NAME_1} concluded`` still reads as one person doing two things; ``{NAME_1} …
    {NAME_3}`` reads as strangers, and it would do so on exactly the essays that
    mention somebody a lot.
    """
    numbered = _mask_numbered(
        "Terrence Okonkwo drove. Terrence Okonkwo parked. Terrence Okonkwo left.",
        given_name=is_given,
    )
    assert numbered.count("{NAME_1}") == 3, numbered
    assert "{NAME_2}" not in numbered, numbered


def test_masking_is_reversible_when_numbered() -> None:
    """The end-to-end contract: mask, then restore, and get the bytes back."""
    from vicary.local_classifier import LocalNameClassifier

    text = ("Marisol emailed m.d@westfieldhigh.k12.oh.us about Terrence Okonkwo, "
            "then called (330) 555-0148 to tell Marisol's mother.")
    result = LocalNameClassifier(
        fixture_identity(), candidates=True, given_name=is_given
    ).mask(text)
    assert result.text != text
    assert result.restore(result.text) == text, result.text


def test_an_unnumbered_pass_offers_no_restore_map() -> None:
    """Because an unnumbered map cannot be inverted, and a half-map is worse than
    none — it would restore the first name it saw over every other person."""
    from vicary.local_classifier import LocalNameClassifier

    result = LocalNameClassifier(
        fixture_identity(), candidates=True, given_name=is_given,
        number_placeholders=False,
    ).mask("Marisol waved at Terrence Okonkwo.")
    assert result.restore_map == {}


def test_indices_do_not_restart_between_passes() -> None:
    """One minter for the whole document.

    The identity pass, the structured passes and candidate generation each mask
    spans; per-pass counters would emit {NAME_1} twice for two different people
    and re-introduce the collision one level down.
    """
    from vicary.local_classifier import LocalNameClassifier

    # "Marguerite Delacroix-Whitfield" is the fixture student (identity pass);
    # "Terrence Okonkwo" is a third party (candidate pass).
    result = LocalNameClassifier(
        fixture_identity(), candidates=True, given_name=is_given
    ).mask("Marguerite Delacroix-Whitfield sat beside Terrence Okonkwo.")
    assert "{NAME_1}" in result.text and "{NAME_2}" in result.text, result.text
    assert result.restore(result.text) == (
        "Marguerite Delacroix-Whitfield sat beside Terrence Okonkwo."
    )


# ---------------------------------------------------------------------------
# Refusing corroboration where the context names someone in the writer's life


def _mask_corroborating(text: str, **kw) -> str:
    """Mask with the full gazetteer-shaped oracle set corroboration needs."""
    kw.setdefault("given_name", is_given)
    notable = frozenset({"jackie robinson", "richard wright", "toni morrison"})
    kw.setdefault("notable", lambda n: n.lower().strip(".,'’") in notable)
    kw.setdefault("notability_tier",
                  lambda n: "full_name" if n.lower() in notable else None)
    return _mask(text, **kw)


def test_a_neighbour_sharing_a_famous_surname_is_still_masked() -> None:
    """Corroboration is a document-level inference; this is its exception.

    Without this, a neighbour named Robinson is protected by Jackie Robinson's
    fame — the surname is genuinely ambiguous document-wide, and only the local
    context can break the tie.
    """
    text = ("Jackie Robinson broke the color line in 1947. Robinson, who lives "
            "two doors down from us, taught me how to throw.")
    masked = _mask_corroborating(text)
    assert "Jackie Robinson" in masked, masked
    assert masked.count("Robinson") == 1, masked


def test_the_author_an_essay_is_about_still_keeps_a_bare_surname() -> None:
    """The frame corroboration exists for. No relation cue, no refusal."""
    text = ("Richard Wright wrote about hunger without flinching. Wright never "
            "lets the reader look away from what it costs.")
    assert _mask_corroborating(text).count("Wright") == 2


def test_a_first_person_appositive_alone_does_not_refuse() -> None:
    """The guard on the discriminator, and the reason it is a cue list.

    "Wright, who taught me to look away" is how literary analysis reads. Keying
    refusal on "the appositive mentions the writer" re-destroys the author the
    essay is about, which is the defect corroboration was built to fix.
    """
    text = ("Richard Wright wrote about hunger without flinching. Wright, who "
            "taught me to look away from nothing, is why I write.")
    assert _mask_corroborating(text).count("Wright") == 2


def test_a_relation_noun_before_the_surname_also_refuses() -> None:
    """English puts the relation either side of the name."""
    text = ("Jackie Robinson broke the color line in 1947. My coach Robinson "
            "made us run the bases until dark.")
    assert _mask_corroborating(text).count("Robinson") == 1


def test_the_refusal_does_not_reach_across_a_sentence_boundary() -> None:
    """A cue in the NEXT sentence is not this surname's context.

    Without the terminal-punctuation stop the window would drag in an unrelated
    cousin from the following sentence and refuse every corroboration near one.
    """
    text = ("Richard Wright wrote about hunger without flinching. Wright is the "
            "one I go back to. My cousin never reads anything.")
    assert _mask_corroborating(text).count("Wright") == 2


def test_relation_refusal_off_restores_the_old_behaviour() -> None:
    """The control arm, so the recovery is a delta rather than a claim."""
    text = ("Jackie Robinson broke the color line in 1947. Robinson, who lives "
            "two doors down from us, taught me how to throw.")
    assert _mask_corroborating(text, relation_refusal=False).count("Robinson") == 2


# ---------------------------------------------------------------------------
# A heading's capitals are orthographic, so they are not evidence


HEADING_DOC = (
    "Horses\n"
    "by Gwen\n"
    "\n"
    "Breeds I Like\n"
    "\n"
    "I wrote about horses because I like to ride them every summer.\n"
)


def test_a_heading_capital_is_not_a_name() -> None:
    """The dominant over-fire class on real prose, copied from a real document.

    "Breeds I Like" is a literal heading in the Education Northwest horses paper,
    and "Like" reads as a deliberate mid-sentence capital to every earlier rule.
    """
    assert "Like" in _mask_lowercase(HEADING_DOC)
    assert "Like" not in _mask_lowercase(
        HEADING_DOC, headings_are_orthographic=False
    )


def test_a_multi_token_heading_is_not_a_shape() -> None:
    """Title case capitalises every word, so two capitals in a row prove nothing.

    Outside a heading "Horse Movement" is a name-shaped span and stays one; inside
    a heading the second capital is as orthographic as the first.
    """
    doc = "Intro\n\nHorse Movement\n\nA horse can walk, trot, canter or gallop.\n"
    assert "Horse Movement" in _mask_lowercase(doc)


def test_a_heading_that_names_somebody_is_still_masked() -> None:
    """The guard: the rule withholds evidence, it does not grant a licence.

    Students title sections after people. Inside a heading the span clears the
    same bar as any other unevidenced capital — the given-name tier.
    """
    doc = ("Intro\n\nMy Brother Terrence Okonkwo\n\nHe taught me how to ride "
           "a bike.\n")
    assert "Terrence" not in _mask_lowercase(doc)


def test_a_hard_wrapped_prose_line_is_not_a_heading() -> None:
    """The blank line is load-bearing, not belt-and-braces.

    Body prose is hard-wrapped, so a short unpunctuated line is common. Without
    the blank-line test "Terrence Okonkwo lent me his bike" would read as a
    heading and lose the name's evidence with it.
    """
    doc = ("I had a long summer that year and it went by fast,\n"
           "Terrence Okonkwo lent me his bike\n"
           "and I rode it until the tires gave out.\n")
    assert "Terrence" not in _mask_lowercase(doc)


def test_a_heading_does_not_corroborate_its_own_words() -> None:
    """A title-cased heading must not supply the mid-sentence capital either.

    Counting "The First Horses" as a mid-sentence capital let the heading vouch
    for "Horses" one line removed, which is how the span survived every rule
    aimed at it.
    """
    doc = "Intro\n\nThe First Horses\n\nHorses eat hay, grass and oats.\n"
    assert "Horses" in _mask_lowercase(doc)
