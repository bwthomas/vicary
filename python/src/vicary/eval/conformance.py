"""The fixture and the gates as language-neutral data — the spec three ports share.

Why this module exists. The fixture is 52 frames of ground truth and the gates
are nine bars, and both lived only as Python literals. A TypeScript or Ruby port
that passed a fixture it *transcribed by hand* would prove nothing: the two
suites could disagree about what the right answer is and both stay green. So the
spec is emitted as JSON, once, from the implementation that defines it, and every
front door — Python included — runs against that file.

**The direction of truth, stated plainly.** The Python literals in
:mod:`vicary.eval.fixture` are the source; ``conformance/frames.json`` is the
export. Nothing here lets the JSON drift from them silently:
:mod:`tests.test_conformance` re-exports and compares byte-for-byte, so editing a
frame without running ``just sync-conformance`` fails the build. Generating the
file rather than moving the literals into it is the safer half of the same idea —
a generator cannot mistranscribe.

**Two layers, and they check different things.**

*Expectations* are semantic: this literal, of this entity type, must be masked or
must survive. They are what the fixture already asserted, and a port satisfying
them is a port that redacts the right things.

*Golden output* is exact: the byte string the reference arm produces, and the
placeholder tokens in order of first appearance. This is the layer that catches
what expectations cannot — **placeholder numbering**. Two implementations can
both mask "Deshawn" and "Marguerite" correctly and disagree about which becomes
``{NAME_1}``, because numbering follows iteration order over candidate spans. A
disagreement there breaks restoration across a service boundary, which is the one
property a cloud redaction API could not offer and the reason any of this exists.
So it is pinned as bytes, not described.

Golden output is a snapshot of current behaviour, which means a legitimate
improvement to the detector will fail conformance until the snapshot is
regenerated. That is the intended cost: regenerating is one command and a diff a
human reads, and the alternative — a suite that tolerates output changes — cannot
detect the divergence it exists to detect.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from vicary.eval import fixture as fx

#: Schema version of the emitted document. Bumped when a *reader* would have to
#: change; a port refuses an unknown major rather than guessing, exactly as
#: :mod:`vicary.assets` refuses an unknown asset format.
DOCUMENT_VERSION = 1

#: The arm the golden output is produced by. Named in the document because a
#: golden string without its arm is unreproducible: `local-gazetteer-lowercase`
#: is the shippable configuration (candidate generation plus the offline
#: notability oracle plus the lowercase route), and it is what the gates measure.
#: A port implementing a different arm and comparing against these bytes is
#: measuring two changes at once.
REFERENCE_ARM = "local-gazetteer-lowercase"

#: Where the spec lives relative to the repository root. Not packaged: it is a
#: cross-language artifact of the repository, not of the Python distribution, and
#: a wheel that carried it would imply an installed copy was authoritative.
CONFORMANCE_DIRNAME = "conformance"
FRAMES_FILENAME = "frames.json"
GATES_FILENAME = "gates.json"
PRIMITIVES_FILENAME = "primitives.json"


def conformance_dir() -> Path | None:
    """The repo's ``conformance/`` directory, or ``None`` outside a checkout.

    ``None`` rather than a guess, and callers must treat it as "cannot run the
    conformance suite here" rather than "the suite passed" — an installed wheel
    has no repository to read the spec from, and a suite that quietly reports a
    pass on a missing spec is the failure this whole file guards against.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / CONFORMANCE_DIRNAME
        if candidate.is_dir():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _span_to_json(span: fx.Span) -> dict[str, Any]:
    """One span, with defaulted fields omitted.

    Omission keeps the file readable — most spans set two of eight fields — and
    the reader below restores the same defaults, so a round-trip is exact.
    """
    out: dict[str, Any] = {"entity": span.entity, "literal": span.literal}
    defaults = {f.name: f.default for f in fields(fx.Span)}
    for name in ("verdict", "expect_count", "expect", "kept_by", "redacted_by",
                 "note"):
        value = getattr(span, name)
        if value != defaults[name]:
            out[name] = value
    return out


def _frame_to_json(frame: fx.Frame, group: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "frame_id": frame.frame_id,
        "group": group,
        "sentence": frame.sentence,
        "spans": [_span_to_json(s) for s in frame.spans],
    }
    if frame.held_out:
        out["held_out"] = True
    if frame.prompt_context:
        out["prompt_context"] = frame.prompt_context
    if frame.note:
        out["note"] = frame.note
    return out


def _groups() -> dict[str, tuple[fx.Frame, ...]]:
    return {
        "recall": fx.RECALL_FRAMES,
        "keep": fx.KEEP_FRAMES,
        "intersect": fx.INTERSECTION_FRAMES,
        "structured": fx.STRUCTURED_FRAMES,
    }


def _golden_for(frames: tuple[fx.Frame, ...]) -> dict[str, dict[str, Any]]:
    """Exact reference output per frame: the masked bytes and the numbering.

    Imported lazily because building the reference arm loads the 2.1 MB gazetteer,
    and every other function here is pure data movement that should stay cheap.
    """
    from vicary.eval.recall import build_redactor

    redactor = build_redactor(REFERENCE_ARM, None)
    golden: dict[str, dict[str, Any]] = {}
    for frame in frames:
        result = redactor._apply(frame.sentence, source="INPUT")
        alignment = fx.align(frame.sentence, result.text)
        # Placeholders in order of FIRST APPEARANCE, which is the numbering
        # contract a port has to reproduce. Recorded separately from `masked`
        # even though it is derivable from it, because a diff on this list names
        # the defect ("{NAME_1} and {NAME_2} are swapped") where a diff on a
        # whole sentence only shows that one exists.
        #
        # `align()` yields (placeholder_token, original_region) — element ZERO is
        # the token. Taking element one instead records the student's names as
        # "placeholders", which reads plausibly in a JSON diff and pins nothing:
        # the numbering this file exists to fix would be entirely unconstrained.
        seen: list[str] = []
        for placeholder, _ in alignment.pairs:
            if placeholder not in seen:
                seen.append(placeholder)
        golden[frame.frame_id] = {
            "masked": result.text,
            "placeholders": seen,
            # (placeholder_token, original_region) per emitted placeholder — the
            # restoration mapping. A port reproducing `masked` but not these has
            # numbered correctly and cannot put the words back.
            "mapping": [list(p) for p in alignment.pairs],
            "aligns": alignment.ok,
        }
    return golden


# ---------------------------------------------------------------------------
# The primitives layer — what a port checks BEFORE it can produce a frame
# ---------------------------------------------------------------------------
#
# `frames.json` scores finished output, which is the right final bar and a poor
# first one: a port with nothing implemented scores 0 of 35 and gets no signal at
# all about which of the forty-odd primitives underneath it is wrong. The first
# port paid that cost by hand — a throwaway probe that ran both implementations
# over a corpus and diffed the JSON, which found a real divergence (JavaScript's
# `\b` is ASCII-only where Python's is Unicode-aware) that no frame would have
# isolated. Every later port would otherwise re-derive the same expectations by
# hand, which is transcription, which is the thing this directory exists to avoid.
#
# So the probe becomes an export. Same direction of truth as the other two
# documents: the Python functions are the source, this is generated from them,
# and `tests/test_conformance.py` byte-compares the committed file against a fresh
# export so it cannot drift.
#
# It is deliberately NOT scored and NOT a gate. A port can be green here and mask
# nothing; the frames are still what says a port works. This layer only says which
# brick is crooked.

#: Texts every primitive is evaluated over. Chosen to exercise a named behaviour
#: each, not for coverage of English: an all-caps sentence, a shout inside
#: mixed-case prose, an opening quote, a hard-wrapped line that is not a heading,
#: our own placeholders, a curly possessive, an accented word, and one document
#: per capitalisation-habit state.
PRIMITIVE_CORPUS: dict[str, str] = {
    "empty": "",
    "plain": "My cousin Terrence Okonkwo came over that summer.",
    "honorific": "Mrs. Okonkwo taught us. Dr Ruiz did not.",
    "allcaps": "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER.",
    "shout": "Then SLAM! the door closed and Marisol laughed.",
    "quoted": 'vivid words like "Giggles filled the school" stand out',
    "headings": (
        "Horse Families\n\nThe first horses were small. They lived in herds.\n\n"
        "Breeds I Like\n\nMy favourite is the Arabian."
    ),
    "wrapped": "The INternet as we know it today first\nappeared in a lab in Ohio.",
    "protected": "{NAME_1} met @PERSON2 and {LOCATION} last June.",
    "particle": "My inspiration, Vincent van Gogh, painted for years.",
    "initials": "J. R. R. Tolkien wrote it, and so did T.S. Eliot.",
    "hyphen": "Marguerite Delacroix-Whitfield and O'Brien were there.",
    "possessive": "Terrence's older brother, Narciso's friend, and Lincoln’s hat.",
    # The `\b` case. Python reads the diaeresis as a word character and emits
    # `na`; a reader whose word boundary is ASCII-only also emits `ve`.
    "accented": "naïve café Renée went home. i did too.",
    "lowercase_writer": "then terrence okonkwo showed up. i was so happy. we went home.",
    "bare_i": "The Dog barked at Marisol. i ran. Then Terrence came over.",
    "silent_prose": "Nothing here is capitalised mid sentence at all. It is only prose.",
    "inconsistent": (
        "My cousin Terrence came over. my aunt Marisol drove. "
        "we went to Akron. then Deshawn showed up. i was tired."
    ),
    "one_mark": "I met Marisol today. She was nice.",
    "two_marks": "I met Marisol today. I also saw Deshawn there.",
    # The rate asymmetry, as a pair one percentage point apart. Above the floor a
    # 8.3% drop rate reads as typos; below it a 9.1% rate is taken as the habit,
    # because there is no presence signal to weigh it against.
    "typo_capitaliser": (
        "Marisol went to Akron. She met Deshawn. They saw Terrence. We drove home. "
        "She waved. He smiled. They left. It rained. We slept. boy did we laugh. "
        "She called again. He answered."
    ),
    "below_floor": (
        "The dog barked. The cat ran. The bird flew. The fish swam. The cow mooed. "
        "The pig oinked. The hen clucked. The duck quacked. The goat bleated. "
        "The horse neighed. the sheep baaed."
    ),
    # The six entries below were added because a negative control survived
    # without them: each removes a rule from `find_candidates` that no corpus
    # text separated from its absence, so a port could drop the rule and pass.
    # The rule each one pins is named, because that is the only thing that says
    # why the text is worded the way it is.
    #
    # A bare seed that reaches no second token, and the same seed behind a
    # determiner. `_LOWERCASE_MIN_TOKENS` and the determiner guard.
    "determiner_seed": (
        "terrence and i stayed up late. the terrence okonkwo i knew moved away."
    ),
    # A lowercase span that would otherwise end on a particle.
    "lowercase_particle_tail": "marisol de and i went home. we stayed late.",
    # A single-quoted name: the trailing apostrophe is the closing quote, and
    # keeping it in the span eats the quotation mark out of the student's prose.
    "single_quoted": "He used words like 'Terrence' and 'Marisol' in the story.",
    # A hyphenated surname whose second half the writer left lowercase, followed
    # by another lowercase name. The lowercase route reaches *into* the
    # capitalised span here — the one shape where the two routes collide — and
    # emitting both would leave the inner placeholder's braces as debris.
    "hyphen_lowercase_tail": (
        "Marguerite Delacroix-marisol okonkwo waved at us. i waved back."
    ),
    # A capitalised possessive in a document whose habit is `lowercase`. The one
    # input that reaches the overlap guard, and it needs both halves: the `s` of
    # a possessive is a lowercase token sitting INSIDE a capitalised span (the
    # apostrophe is not a word character, so the lowercase scanner sees it), and
    # only a `lowercase` habit drops the corroboration requirement that would
    # otherwise reject the seed before the routes could collide. `possessive`
    # above has the first half and not the second.
    "possessive_lowercase_habit": "i saw Marisol's older brother. i waved back.",
    # A work title with a first-person relation attached in front of it, which
    # withdraws the title protection. Without this the refusal could be deleted
    # and every case still passed.
    "title_relation": "My neighbor Alice Adams walked me to the bus stop.",
    # ...and a title that IS the relation phrase, which is the other half of the
    # same rule and reads the writer's own capitals rather than the attachment.
    "title_is_relation": "My cousin Vinny came over that summer and never left.",
    "title_book": "I read To Kill a Mockingbird last year.",
    "title_nested": "The Lion King is my favourite film.",
    "title_curly": "We read Charlotte’s Web in class.",
    "title_lower": "i read to kill a mockingbird last year.",
    "title_none": "Nothing here matches any title at all.",
}

#: Already-split spans, for the functions that take tokens rather than text.
PRIMITIVE_TOKEN_LISTS: dict[str, list[str]] = {
    "two_names": ["Terrence", "Okonkwo"],
    "allcaps_run": ["My", "Best", "Friend", "Deshawn", "Pritchard", "Would", "Never"],
    "honorific_and_name": ["Mrs.", "Okonkwo"],
    "bare_honorific": ["Mrs."],
    "honorific_then_stop": ["Dr", "The"],
    "interior_stop": ["Coach", "Ruiz", "And", "Marisol"],
    "landmark": ["The", "Lincoln", "Memorial"],
    "civic": ["Akron", "Public", "Library"],
    "township": ["Springfield", "Township"],
    "org_suffix": ["Acme", "Inc."],
    "settlement": ["Akron"],
    "school": ["Westfield", "High", "School"],
    # The collision the precedence table exists to resolve: a real town whose
    # last token is a landmark suffix. Carried as a primitive because a port that
    # ordered the rows the other way would still pass every frame — the frame set
    # had no example of this shape for the detector's whole life, which is how
    # 383 real settlements leaked. See `precedence` below.
    "settlement_with_landmark_suffix": ["Allen", "Park"],
    "settlement_with_org_suffix": ["Falls", "Church"],
}

#: Spans, for the relation-override predicates, which take ``(text, start, end)``
#: rather than a whole text or a token list. Carried as ``(text, needle)`` and
#: resolved to offsets at build time, so the committed file states the offsets a
#: port must use and no port has to reproduce this file's own index arithmetic.
#:
#: The set is what the piece-5 probe diffed the two languages over, kept rather
#: than thrown away: each id names the rule it separates, including the two
#: boundary cases that pin :data:`_RELATION_WINDOW` behaviourally (a cue 80
#: characters after the span is inside the window; one 92 characters after it is
#: not) and the two that pin the port's Unicode word-boundary spelling.
PRIMITIVE_SPAN_CASES: dict[str, tuple[str, str]] = {
    "kinship-possessive-mixed": (
        "My cousin Vinny came over that summer and never left.",
        "My cousin Vinny",
    ),
    "kinship-possessive-title": (
        "We stayed with the Alvarez family in July. My Cousin Vinny is my favorite movie and "
        "I have seen it four times.",
        "My Cousin Vinny",
    ),
    "kinship-possessive-lower": (
        "my cousin vinny is my favorite movie and i have seen it four times.",
        "my cousin vinny",
    ),
    "kinship-full-name": (
        "We stayed with the Alvarez family in July. My cousin Vinny Delgado came over that "
        "summer and never left.",
        "My cousin Vinny Delgado",
    ),
    "neighbour-appositive": (
        "Jackie Robinson broke the color line in 1947. Robinson, who lives two doors down "
        "from us, taught me how to throw.",
        "Robinson, who",
    ),
    "neighbour-bare-surname": (
        "Jackie Robinson broke the color line in 1947. Robinson, who lives two doors down "
        "from us, taught me how to throw.",
        "Robinson",
    ),
    "relation-before-attached": (
        "My neighbor Alice Adams walked me to the bus stop every morning that whole year.",
        "Alice Adams",
    ),
    "relation-after-appositive": (
        "Alice Adams, my next-door neighbor, drove us all the way there.",
        "Alice Adams",
    ),
    "relation-after-who-is": (
        "Alice Adams, who is my neighbor, drove us all the way there.",
        "Alice Adams",
    ),
    "relation-after-who-was": (
        "Alice Adams, who was my coach, drove us all the way there.",
        "Alice Adams",
    ),
    "relation-after-no-comma": (
        "Alice Adams my neighbor drove us all the way there.",
        "Alice Adams",
    ),
    "first-person-alone-keeps": (
        "I read Harry Potter with my little brother over the summer holiday.",
        "Harry Potter",
    ),
    "attachment-alone-keeps": (
        "Atticus Finch, a father who taught me to look away from nothing.",
        "Atticus Finch",
    ),
    "character-described-by-relation": (
        "Peter Parker lives with his aunt in a small apartment in Queens.",
        "Peter Parker",
    ),
    "modifiers-zero": (
        "My friend Deshawn Pritchard stayed after class to finish it.",
        "Deshawn Pritchard",
    ),
    "modifiers-one": (
        "My best friend Deshawn Pritchard stayed after class to finish it.",
        "Deshawn Pritchard",
    ),
    "modifiers-two": (
        "My old soccer coach Deshawn Pritchard stayed after class to finish it.",
        "Deshawn Pritchard",
    ),
    "modifiers-three-too-many": (
        "My very old soccer coach Deshawn Pritchard stayed after class to finish it.",
        "Deshawn Pritchard",
    ),
    "modifier-capitalised-not-swallowed": (
        "My Old soccer coach Deshawn Pritchard stayed after class to finish it.",
        "Deshawn Pritchard",
    ),
    "our-possessive": (
        "Our coach Bramwell made us run laps until it got dark.",
        "Bramwell",
    ),
    "proximity-first-person": (
        "Alice Adams, who lives two doors down from us, walked me to the bus stop.",
        "Alice Adams",
    ),
    "proximity-no-first-person": (
        "Alice Adams, who lives two doors down from the school, walked to the bus stop.",
        "Alice Adams",
    ),
    "proximity-in-my-class": (
        "Alice Adams, who is in my class, walked me to the bus stop every morning.",
        "Alice Adams",
    ),
    "proximity-across-the-street": (
        "Alice Adams, who lives across the street from me, walked me to the bus stop.",
        "Alice Adams",
    ),
    "sentence-break-stops-scan": (
        "Alice Adams walked to the bus stop. My cousin was there too on that morning.",
        "Alice Adams",
    ),
    "window-boundary-inside": (
        "Alice Adams walked me all the way to the bus stop on that cold morning, my cousin "
        "said later.",
        "Alice Adams",
    ),
    "window-boundary-outside": (
        "Alice Adams walked me all the way to the bus stop on that very cold winter morning "
        "again and again, my cousin said later.",
        "Alice Adams",
    ),
    "boundary-before-accented": (
        "naïmy cousin Terrence came over that summer and never left the house.",
        "Terrence",
    ),
    "boundary-after-accented": (
        "Alice Adams, my cousinä came over that summer and never left the house.",
        "Alice Adams",
    ),
    "boundary-before-ascii-run": (
        "roomy cousin Terrence came over that summer and never left the house.",
        "Terrence",
    ),
    "curly-apostrophe-modifier": (
        "My mom’s friend Alice Adams walked me to the bus stop every morning.",
        "Alice Adams",
    ),
    "single-token-span": (
        "My cousin Vinny came over that summer and never left.",
        "Vinny",
    ),
    "relation-cue-plural": (
        "My cousins Alice Adams and Deshawn came over that summer.",
        "Alice Adams",
    ),
    "no-relation-anywhere": (
        "Vincent van Gogh painted the sunflowers in Arles during that year.",
        "Vincent van Gogh",
    ),
    "title-leads-two-modifiers": (
        "My Best Friend Anne Frank is the book I read last spring for class.",
        "My Best Friend Anne Frank",
    ),
    "title-leads-mixed-case": (
        "My best Friend Anne Frank is the book I read last spring for class.",
        "My best Friend Anne Frank",
    ),
    "hero-is-not-a-cue": (
        "My hero Abraham Lincoln freed the slaves and saved the whole union.",
        "Abraham Lincoln",
    ),
    "favourite-is-not-a-cue": (
        "My favorite author Alice Adams wrote a book I read last spring.",
        "Alice Adams",
    ),
    "span-at-text-start": (
        "Robinson, my neighbor, taught me how to throw a curveball that year.",
        "Robinson",
    ),
    "span-at-text-end": (
        "The best throw I ever saw came from my neighbor Robinson",
        "Robinson",
    ),
    "newline-stops-clause": (
        "Alice Adams walked to the bus stop\nMy cousin was there too on that morning.",
        "Alice Adams",
    ),
    "teacher-cue": (
        "My teacher Mrs. Okonkwo taught me the trick with the index cards.",
        "Mrs. Okonkwo",
    ),
    "grandmother-cue": (
        "My grandmother Marisol Ybarra told me that story every single summer.",
        "Marisol Ybarra",
    ),
    "window-cue-at-80-inside-90": (
        "Alice Adams walked me to the bus stop every single morning of that whole long cold "
        "winter, my cousin said later.",
        "Alice Adams",
    ),
    "window-cue-at-92-outside-90": (
        "Alice Adams walked me to the bus stop every single morning of that whole long and "
        "very cold winter that year, my cousin said later.",
        "Alice Adams",
    ),
}


#: Single tokens for the stoplist predicate, each standing for a rule rather than
#: a word: a clitic, a curly clitic, an un-apostrophized spelling, a bare clitic.
PRIMITIVE_STOP_TOKENS: tuple[str, ...] = (
    "The", "the", "Mrs.", "Mrs", "I'm", "I’m", "As", "Terrence", "Okonkwo",
    "im", "dont", "thats", "n't", "'s", "Dr", "Coach", "SLAM", "a", "A",
    "Won't", "Won’t", "he'd", "'", "It's", "Favorite", "favorite", "I",
)

#: The stand-in oracles, emitted as data so every port wires the SAME ones.
#: Real gazetteer tiers cannot be used here: they are 2.1 MB of asset, and a
#: primitive that disagrees would then be indistinguishable from a tier lookup
#: that disagreed. Semantics, which a port must implement exactly:
#:
#: * ``is_settlement(name)``   -> ``name.lower()`` is in ``settlements``
#: * ``is_title(text)``        -> ``text.lower()``, curly apostrophes folded to
#:                                ``'``, is in ``titles``
#: * ``is_title_prefix(key)``  -> some entry of ``titles`` equals ``key`` or
#:                                starts with ``key + " "``
PRIMITIVE_SETTLEMENTS: tuple[str, ...] = (
    "acme inc.", "akron", "akron public library", "allen park", "falls church",
    "springfield township", "westfield high school",
)
#: ``alice adams`` and ``my cousin vinny`` are the two that carry the relation
#: refusal: a novel whose title is an ordinary person's name, and a film whose
#: title is a kinship phrase. Both are real entries in the shipped title tier and
#: both are the shape the refusal exists for — without them in this list the
#: refusal can be deleted and every case here still passes.
PRIMITIVE_TITLES: tuple[str, ...] = (
    "alice adams", "charlotte's web", "my cousin vinny", "the lion",
    "the lion king", "to kill a mockingbird",
)

#: The given-name tier, as a stand-in. Supplying this is what turns the lowercase
#: route on, so a port handed the same seven tokens either reaches
#: "terrence okonkwo" in ``lowercase_writer`` or does not — and the difference is
#: a candidate rather than a count. It also feeds the corroboration guard, which
#: is the arm that *removes* capitalised candidates, so the same list is exercised
#: in both directions.
#:
#: * ``is_given(token)`` -> ``token.lower()`` is in ``given_names``
PRIMITIVE_GIVEN_NAMES: tuple[str, ...] = (
    "deshawn", "marguerite", "marisol", "narciso", "renée", "terrence", "vincent",
)

#: The notability *tier* oracle, as a stand-in — see
#: :data:`~vicary.name_candidates.CORROBORATING_TIER`. Only ``full_name`` may
#: establish a surname, so the settlements are carried here too, spelling the tier
#: that must NOT: a port that folded these two into one boolean would let "Akron"
#: license a classmate's bare surname, which is the defect the tier oracle exists
#: to prevent and which no boolean case above can show.
#:
#: * ``tier(name)`` -> ``"full_name"`` if ``name.lower()`` is in ``full_names``,
#:   ``"place"`` if it is in ``settlements``, otherwise ``"not_notable"``
#: * ``is_notable(name)`` -> ``tier(name) != "not_notable"``
PRIMITIVE_FULL_NAMES: tuple[str, ...] = (
    "j. r. r. tolkien", "narciso rodriguez", "richard wright", "t.s. eliot",
    "vincent van gogh",
)

#: Names for the surname-folding functions, which take a name rather than a text
#: or a token list. Each entry is here for a shape rather than for coverage: the
#: possessive that folds to the citation form, the particle run that yields two
#: forms, the three-particle run that yields three, the mononym that yields none,
#: the honorific-led and first-name-led spans that must NOT reduce to a bare
#: surname key, and the curly apostrophe a port reading only ``'`` gets wrong.
PRIMITIVE_NAME_FORMS: tuple[str, ...] = (
    "Richard Wright", "Wright", "Wright's", "Wright’s", "Vincent van Gogh",
    "van Gogh", "de la Cruz", "Coach Wright", "Priya Wright", "Mrs. Okonkwo",
    "T.S. Eliot", "",
)


def _primitive_settlement(name: str) -> bool:
    return name.lower() in PRIMITIVE_SETTLEMENTS


def _primitive_title(text: str) -> bool:
    return text.lower().replace("’", "'") in PRIMITIVE_TITLES


def _primitive_title_prefix(key: str) -> bool:
    return any(t == key or t.startswith(key + " ") for t in PRIMITIVE_TITLES)


def _primitive_given(token: str) -> bool:
    return token.lower() in PRIMITIVE_GIVEN_NAMES


def _primitive_tier(name: str) -> str:
    if name.lower() in PRIMITIVE_FULL_NAMES:
        return "full_name"
    if name.lower() in PRIMITIVE_SETTLEMENTS:
        return "place"
    return "not_notable"


def _primitive_notable(name: str) -> bool:
    return _primitive_tier(name) != "not_notable"


def _finds(pattern: Any, text: str) -> list[list[Any]]:
    """``[start, end, matched]`` per match — the shape every port can produce."""
    return [[m.start(), m.end(), m.group(0)] for m in pattern.finditer(text)]


def build_primitives_document() -> dict[str, Any]:
    """Every tokenisation and capitalisation primitive, over the shared corpus.

    One entry per function, keyed by corpus name, so a failing port is told which
    primitive disagrees on which input rather than which frame came out wrong.
    """
    from vicary import name_candidates as nc

    corpus = PRIMITIVE_CORPUS
    lists = PRIMITIVE_TOKEN_LISTS

    def over_corpus(fn: Any) -> dict[str, Any]:
        return {name: fn(text) for name, text in corpus.items()}

    def over_lists(fn: Any) -> dict[str, Any]:
        return {name: fn(tokens) for name, tokens in lists.items()}

    def over_names(fn: Any) -> dict[str, Any]:
        return {name: fn(name) for name in PRIMITIVE_NAME_FORMS}

    def candidates(**kwargs: Any) -> Any:
        return lambda text: [
            [c.start, c.end, c.text, c.kind]
            for c in nc.find_candidates(text, **kwargs)
        ]

    def over_spans(fn: Any) -> dict[str, Any]:
        return {
            name: fn(text, text.index(needle), text.index(needle) + len(needle))
            for name, (text, needle) in PRIMITIVE_SPAN_CASES.items()
        }

    def spans(fn: Any) -> Any:
        return lambda text: [list(s) for s in fn(text)]

    return {
        "document_version": DOCUMENT_VERSION,
        "corpus": corpus,
        "token_lists": lists,
        "stop_tokens": list(PRIMITIVE_STOP_TOKENS),
        "name_forms": list(PRIMITIVE_NAME_FORMS),
        "oracles": {
            "settlements": list(PRIMITIVE_SETTLEMENTS),
            "titles": list(PRIMITIVE_TITLES),
            "given_names": list(PRIMITIVE_GIVEN_NAMES),
            "full_names": list(PRIMITIVE_FULL_NAMES),
        },
        # The classification policy itself, as data. Emitted because it is the
        # one part of the detector a port can get wrong while passing every
        # frame: the rows are a total order, reordering two of them changes which
        # spans survive, and only a colliding span can tell. A port reads these
        # rows in order and takes the first whose tag the span carries.
        "precedence": [
            {"tag": row.tag, "mask": row.mask, "kind": row.kind}
            for row in nc._PRECEDENCE
        ],
        # The two word lists the classification arms read, emitted in full for
        # the reason the precedence rows are: they are the last data in the
        # classification path a port types by hand. Measured on this spec at the
        # time they were added, the token lists exercise 3 of 46 organisation
        # suffixes and 3 of 36 landmark suffixes — so a port could omit
        # `hospital`, `university` or `valley` and pass every case above, every
        # frame, and every gate. Counts alone would not do either: a port with
        # 46 suffixes, one of them misspelled, is a port that keeps a town.
        #
        # Sorted so the comparison is over a set rather than an iteration order.
        # `_STOP_WORDS` is deliberately NOT here — it is a lexicon asset with its
        # own sha256 and its own declared count, so `constants["stop_words"]`
        # pins it without restating 421 words in a second place.
        "suffixes": {
            "organization": sorted(nc._ORG_SUFFIXES),
            "landmark": sorted(nc._LANDMARK_SUFFIXES),
        },
        # The three remaining hand-typed lists, and the last data in candidate
        # generation a port could get wrong while staying green. Measured on this
        # spec: its inputs exercise 7 of 32 honorifics, 3 of 19 particles and 2 of
        # 16 clitics, so most of each was checked by nothing.
        #
        # **Emitted in source order, and compared in order.** These are not sets
        # like `suffixes`: `_HONORIFICS` and `_PARTICLES` are joined into regex
        # alternations, where the order of the branches decides which one matches
        # first, and `_without_clitic` strips the first clitic that matches. A
        # port that sorted any of them would build a different regex.
        "word_lists": {
            "honorifics": list(nc._HONORIFICS),
            "particles": list(nc._PARTICLES),
            "clitics": list(nc._CLITICS),
        },
        # The span cases the relation predicates run over, with offsets resolved
        # here so a port compares answers rather than reproducing this file's
        # index arithmetic.
        "span_cases": {
            name: {
                "text": text,
                "start": text.index(needle),
                "end": text.index(needle) + len(needle),
            }
            for name, (text, needle) in PRIMITIVE_SPAN_CASES.items()
        },
        # The relation override's word lists, on the same argument as `suffixes`:
        # 38 cues, 13 proximity phrases and 6 pronouns, every one of them typed by
        # hand in each port. `overridable_tiers` is here because it is the policy
        # half — a port that let the override reach `place` would redact a town
        # the tier deliberately keeps, and no case above would say so.
        "relation": {
            "cues": sorted(nc._RELATION_CUES),
            # Order matters: this one is a tuple scanned in order, not a set.
            "proximity_cues": list(nc._PROXIMITY_CUES),
            "first_person": sorted(nc._FIRST_PERSON),
            "overridable_tiers": sorted(nc.OVERRIDABLE_TIERS),
        },
        # The one tier a candidate may establish a surname from, as data for the
        # same reason `overridable_tiers` is: it is a policy string, and a port
        # that compared against `"person"` or `"notable"` would corroborate
        # nothing and pass every case above, because a corroboration that never
        # fires is invisible in output that was already going to be masked.
        "corroboration": {"tier": nc.CORROBORATING_TIER},
        "constants": {
            "allcaps_run": nc._ALLCAPS_RUN,
            "drops_capitals_min_rate": nc._DROPS_CAPITALS_MIN_RATE,
            "heading_max_chars": nc._HEADING_MAX_CHARS,
            "lowercase_min_tokens": nc._LOWERCASE_MIN_TOKENS,
            "marks_proper_nouns_min": nc._MARKS_PROPER_NOUNS_MIN,
            "relation_window": nc._RELATION_WINDOW,
            "stop_words": len(nc._STOP_WORDS),
            "title_max_tokens": nc._TITLE_MAX_TOKENS,
        },
        "cases": {
            "is_stop": {t: nc._is_stop(t) for t in PRIMITIVE_STOP_TOKENS},
            "trim": over_lists(lambda t: nc._trim(t)),
            "classify": over_lists(lambda t: nc._classify(t)),
            "classify_with_settlement": over_lists(
                lambda t: nc._classify(t, _primitive_settlement)
            ),
            # Sorted so the comparison is over a set, not an iteration order.
            "classify_tags": over_lists(lambda t: sorted(nc.classify_tags(t))),
            "classify_tags_with_settlement": over_lists(
                lambda t: sorted(nc.classify_tags(t, _primitive_settlement))
            ),
            # The verdict the table returns for each span — the half of
            # classification that `classify` cannot show, because a kept span and
            # a span typed NAME are the same string there.
            "masks_with_settlement": over_lists(
                lambda t: nc._resolve(
                    nc.classify_tags(t, _primitive_settlement)
                ).mask
            ),
            "word_token": over_corpus(lambda x: _finds(nc._WORD_TOKEN, x)),
            "lower_token": over_corpus(lambda x: _finds(nc._LOWER_TOKEN, x)),
            "any_token": over_corpus(lambda x: _finds(nc._ANY_TOKEN, x)),
            "candidate_re": over_corpus(lambda x: _finds(nc._CANDIDATE_RE, x)),
            "protected": over_corpus(lambda x: _finds(nc._PROTECTED, x)),
            "sentence_starts": over_corpus(lambda x: sorted(nc._sentence_starts(x))),
            "emphasis_spans": over_corpus(spans(nc._emphasis_spans)),
            "heading_spans": over_corpus(spans(nc._heading_spans)),
            "title_spans": over_corpus(
                lambda x: [
                    list(s)
                    for s in nc.find_title_spans(
                        x, _primitive_title, _primitive_title_prefix
                    )
                ]
            ),
            "title_spans_requires_capital": over_corpus(
                lambda x: [
                    list(s)
                    for s in nc.find_title_spans(
                        x, _primitive_title, _primitive_title_prefix, True
                    )
                ]
            ),
            "capitalisation_habit": over_corpus(
                lambda x: nc.capitalisation_habit(x).value
            ),
            "capitalisation_habit_with_headings": over_corpus(
                lambda x: nc.capitalisation_habit(x, nc._heading_spans(x)).value
            ),
            "mid_sentence_capitals": over_corpus(
                lambda x: sorted(nc._mid_sentence_capitals(x, nc._sentence_starts(x)))
            ),
            # The relation override. Four predicates over the span cases: the
            # window scan for a bare surname, the strict attached-phrase test for
            # a title-tier hit, and the two that read the writer's own capitals
            # inside a relation-led span.
            "names_someone_in_the_writers_life": over_spans(
                nc.names_someone_in_the_writers_life
            ),
            "names_someone_the_writer_knows": over_spans(
                nc.names_someone_the_writer_knows
            ),
            "title_is_the_writers_own_relation": over_spans(
                nc.title_is_the_writers_own_relation
            ),
            "relation_led_title_is_internally_mixed": over_spans(
                nc.relation_led_title_is_internally_mixed
            ),
            "mid_sentence_capitals_with_headings": over_corpus(
                lambda x: sorted(
                    nc._mid_sentence_capitals(
                        x, nc._sentence_starts(x), nc._heading_spans(x)
                    )
                )
            ),
            # Surname folding. Three functions over the same names, because the
            # difference between them is the whole rule and each one alone reads
            # as the other two: `surname_tokens` folds the possessive,
            # `bare_surname_key` says which spans a corroborated surname may
            # reach, and `surname_forms` says which forms a full name licenses —
            # and it deliberately does NOT license the bare first name, which is
            # the leg that would keep every "Terrence" in a document.
            "surname_tokens": over_names(nc._surname_tokens),
            "bare_surname_key": over_names(nc._bare_surname_key),
            "surname_forms": over_names(lambda n: list(nc.surname_forms(n))),
            # Candidate generation, end to end, as `[start, end, text, kind]` per
            # span. This is the function every primitive above feeds, so it is the
            # first case here that can fail for a reason no other case names — and
            # the last one a port can pass by reproducing a regex.
            #
            # Both arms are emitted because they are different detectors. Without
            # oracles the capitalised route runs alone, recall-maximal, and the
            # corroboration guard is unreachable by construction; with them the
            # lowercase route turns on, titles are protected before generation,
            # and the guard starts *removing* capitalised spans. A port that wired
            # the oracles into only one of those two would pass the other.
            "find_candidates_without_oracles": over_corpus(candidates()),
            # The lowercase route at its limit, with an oracle that calls every
            # lowercase token a given name. Not a system anyone runs — it is the
            # arm that reaches the overlap guard, and nothing else does.
            #
            # A capitalised span contains a lowercase token in exactly two
            # places: a name particle, and the `s` left dangling by a possessive
            # ("Terrence's" -> the `s` is preceded by an apostrophe, which is not
            # a word character, so the lowercase scanner sees a token). The
            # particle case cannot reach two tokens — the span may not end on a
            # particle, so it trims back to one and falls under the minimum. That
            # leaves the possessive, which the shipped given-name tier does not
            # seed on, so the guard is unreachable under any realistic oracle and
            # a port could delete it and pass everything. It stays because the
            # tier is data and the next one may well contain the token; this arm
            # is what makes deleting it fail.
            "find_candidates_permissive_given": over_corpus(
                candidates(given_name=lambda _token: True)
            ),
            "find_candidates": over_corpus(
                candidates(
                    given_name=_primitive_given,
                    title=_primitive_title,
                    title_prefix=_primitive_title_prefix,
                    settlement=_primitive_settlement,
                )
            ),
            # What each document establishes about a bare surname written later in
            # it, sorted so the comparison is over a set. Emitted over the corpus
            # rather than over hand-written candidate lists so the tier oracle is
            # exercised through the generator that actually feeds it.
            "corroborated_surnames": over_corpus(
                lambda x: sorted(
                    nc.corroborated_surnames(
                        nc.find_candidates(
                            x,
                            given_name=_primitive_given,
                            title=_primitive_title,
                            title_prefix=_primitive_title_prefix,
                            settlement=_primitive_settlement,
                        ),
                        _primitive_notable,
                        tier=_primitive_tier,
                    )
                )
            ),
            # The outbound counterpart, which includes the first name that
            # `surname_forms` refuses to carry inbound. Kept adjacent to it on
            # purpose: the two differ by exactly that token, and a port that made
            # them agree has broken one of them.
            "established_name_tokens": over_corpus(
                lambda x: sorted(
                    nc.established_name_tokens(
                        x, _primitive_notable, tier=_primitive_tier
                    )
                )
            ),
        },
    }


def build_frames_document() -> dict[str, Any]:
    """The whole fixture as a JSON-ready dict, golden output included."""
    grouped = _groups()
    frames = [
        _frame_to_json(frame, group)
        for group, pool in grouped.items()
        for frame in pool
    ]
    identity = fx.fixture_identity()
    return {
        "document_version": DOCUMENT_VERSION,
        "fixture_version": fx.FIXTURE_VERSION,
        "reference_arm": REFERENCE_ARM,
        "verdicts": {"keep": fx.VERDICT_KEEP, "redact": fx.VERDICT_REDACT},
        # The student whose own name the detector is TOLD. Every arm interpolates
        # these three strings, so a port that omits them measures a different
        # system and will miss on exactly the spans that are easiest to catch.
        "identity": {
            "first_name": identity.first_name,
            "last_name": identity.last_name,
            "school_name": identity.school_name,
        },
        "frames": frames,
        "golden": _golden_for(fx.ALL_FRAMES),
    }


def build_gates_document() -> dict[str, Any]:
    """The nine gates as data: what is measured, against what bar, on what data.

    ``requires`` is the load-bearing field. Four gates need data no repository
    ships, and a port that silently omitted them would publish a green badge
    meaning less than the Python one — so each gate declares its dependency and
    a conformance runner must report ``NOT MEASURED`` by name rather than
    reducing the count.
    """
    return {
        "document_version": DOCUMENT_VERSION,
        "reference_arm": REFERENCE_ARM,
        "requirements": {
            "corpus": "An essay corpus TSV the operator supplies "
                      "(VICARY_EVAL_CORPUS_TSV / _DIR). Not shipped by any "
                      "package here.",
            "census": "The US Census surname file (VICARY_EVAL_CENSUS_CSV). Not "
                      "shipped: 3 MB the redaction path never reads.",
        },
        "gates": [
            {
                "id": "held_out_recall",
                "label": "held-out recall",
                "unit": "%",
                "op": ">=",
                "bar": 100.0,
                "requires": [],
                "why": "A private name reaching a model is the failure this "
                       "library exists to prevent, so this one is 100% and the "
                       "others are allowed slack.",
            },
            {
                "id": "held_out_recall_carrier",
                "label": "held-out recall (carrier)",
                "unit": "%",
                "op": ">=",
                "bar": 100.0,
                "requires": ["corpus"],
                "why": "The same spans inside a real essay rather than an "
                       "isolated frame. Isolated frames went green while a "
                       "carrier essay leaked; eight passes hid one leak.",
            },
            {
                "id": "keep_precision",
                "label": "KEEP precision",
                "unit": "%",
                "op": ">=",
                "bar": 100.0,
                "requires": [],
                "why": "Recall alone rewards a redactor that masks everything. "
                       "This is what stops that being a passing score.",
            },
            {
                "id": "round_trip",
                "label": "round-trip",
                "unit": "%",
                "op": ">=",
                "bar": 100.0,
                "requires": [],
                "why": "Masked text must map back one-to-one. Below 100% a "
                       "student cannot be shown their own words, which is the "
                       "property numbered placeholders exist for.",
            },
            {
                "id": "unaccounted_violations",
                "label": "unaccounted violations",
                "unit": "count",
                "op": "==",
                "bar": 0.0,
                "requires": [],
                "why": "Known violations are listed with reasons. The gate is "
                       "that no UNLISTED one appears, and a second test fails "
                       "when a listed one stops occurring, so a stale exemption "
                       "cannot shelter the next defect of the same shape.",
            },
            {
                "id": "over_fire_prose",
                "label": "over-fire on prose",
                "unit": "spans/essay",
                "op": "<=",
                "bar": 0.60,
                "requires": ["corpus"],
                "why": "Over-redaction is the cost side of recall. A FLOOR, not "
                       "a rate: the measured corpus is pre-scrubbed, so real "
                       "prose offers more to over-fire on.",
            },
            {
                "id": "bare_surname_exposure",
                "label": "bare-surname exposure",
                "unit": "%",
                "op": "<=",
                "bar": 1.25,
                "requires": ["census"],
                "why": "How many ordinary US surnames a bare mention would keep "
                       "by mistake. Watches every new single-token tier.",
            },
            {
                "id": "latency_p95",
                "label": "latency p95",
                "unit": "ms",
                "op": "<=",
                "bar": 10.0,
                "requires": ["corpus"],
                "why": "p95 rather than p50, because a threshold read off a "
                       "median reads rosy. The claim is single-digit "
                       "milliseconds with no network.",
            },
            {
                "id": "asset_entries",
                "label": "asset entries",
                "unit": "count",
                "op": ">=",
                "bar": 1.0,
                "requires": [],
                "why": "A gazetteer that reads back empty redacts every public "
                       "figure in every essay — privacy-safe, product-hostile, "
                       "and invisible to every other gate.",
            },
        ],
    }


def dumps(document: dict[str, Any]) -> str:
    """Canonical serialisation: sorted keys, two-space indent, trailing newline.

    Fixed so that "the committed file equals a fresh export" is a byte
    comparison. Any formatting freedom here turns that check into a parse-and-
    compare, and a parse-and-compare cannot tell a reformat from an edit.
    """
    return json.dumps(document, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Read back
# ---------------------------------------------------------------------------


def frames_from_document(document: dict[str, Any]) -> tuple[fx.Frame, ...]:
    """Rebuild :class:`~vicary.eval.fixture.Frame` objects from the document.

    Exists so Python runs the conformance suite the way a port does — off the
    file — rather than off the literals it exported. Otherwise Python is not a
    participant in its own parity check, and the file could be wrong in a way
    only the other two languages would discover.
    """
    version = document.get("document_version")
    if version != DOCUMENT_VERSION:
        raise ValueError(
            f"conformance document version {version!r} is not "
            f"{DOCUMENT_VERSION}; refusing to read it rather than guessing "
            f"which fields moved"
        )
    out = []
    for raw in document["frames"]:
        spans = tuple(
            fx.Span(
                entity=s["entity"],
                literal=s["literal"],
                verdict=s.get("verdict", fx.VERDICT_REDACT),
                expect_count=s.get("expect_count"),
                expect=s.get("expect"),
                kept_by=s.get("kept_by", "notability"),
                redacted_by=s.get("redacted_by", "absence"),
                note=s.get("note", ""),
            )
            for s in raw["spans"]
        )
        out.append(fx.Frame(
            frame_id=raw["frame_id"],
            sentence=raw["sentence"],
            spans=spans,
            held_out=raw.get("held_out", False),
            prompt_context=raw.get("prompt_context", ""),
            note=raw.get("note", ""),
        ))
    return tuple(out)


def load_frames_document(path: Path | None = None) -> dict[str, Any]:
    """Read ``conformance/frames.json``. Raises when it is absent."""
    if path is None:
        directory = conformance_dir()
        if directory is None:
            raise FileNotFoundError(
                "no conformance/ directory above this module — the spec lives in "
                "the repository, not in an installed distribution"
            )
        path = directory / FRAMES_FILENAME
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_gates_document(path: Path | None = None) -> dict[str, Any]:
    """Read ``conformance/gates.json``. Raises when it is absent."""
    if path is None:
        directory = conformance_dir()
        if directory is None:
            raise FileNotFoundError(
                "no conformance/ directory above this module — the spec lives in "
                "the repository, not in an installed distribution"
            )
        path = directory / GATES_FILENAME
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# CLI — `python -m vicary.eval.conformance --write`
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="vicary-conformance",
        description="Emit the fixture and the gates as language-neutral JSON.",
    )
    ap.add_argument("--write", action="store_true",
                    help="write conformance/frames.json and gates.json in place "
                         "(default: print frames.json to stdout)")
    ap.add_argument("--dir", default="",
                    help="conformance directory (default: found above this "
                         "module)")
    args = ap.parse_args(argv)

    frames_doc = dumps(build_frames_document())
    gates_doc = dumps(build_gates_document())
    primitives_doc = dumps(build_primitives_document())

    if not args.write:
        print(frames_doc, end="")
        return 0

    directory = Path(args.dir) if args.dir else conformance_dir()
    if directory is None:
        print("no conformance/ directory found; pass --dir", flush=True)
        return 2
    directory.mkdir(parents=True, exist_ok=True)
    (directory / FRAMES_FILENAME).write_text(frames_doc, encoding="utf-8")
    (directory / GATES_FILENAME).write_text(gates_doc, encoding="utf-8")
    (directory / PRIMITIVES_FILENAME).write_text(primitives_doc, encoding="utf-8")
    print(f"wrote {directory / FRAMES_FILENAME}")
    print(f"wrote {directory / GATES_FILENAME}")
    print(f"wrote {directory / PRIMITIVES_FILENAME}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
