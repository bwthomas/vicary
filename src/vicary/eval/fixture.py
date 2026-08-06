"""The PII fixture: frames a redactor must catch, names it must keep.

Why this module exists
---------------------
The first version of the recall harness carried its ground truth inline as ten
``(entity, literal)`` pairs dropped into four generic sentence templates. It
measured the structured entities honestly — eight types at 100% — and it could
not measure names at all, for a reason worth stating plainly: it contained
**exactly one third-party name in exactly one syntactic frame**, ``my cousin
Terrence Okonkwo``. A detector that learns the string ``my cousin ___`` scores
100% on that fixture and ~0% on real student prose. A guard needs a plausible
failing case, and that one did not provide one.

So the ground truth here is *frames*, not literals. A frame is a whole sentence
with the spans inside it labelled, which buys four things the old shape could
not express:

1. **Recall over the frames English actually uses** — bare first name, dialogue
   attribution, honorific + surname, appositive, all-caps, lowercase, initial +
   surname. The frame is the axis under test, so it is named and scored
   per-frame rather than sampled at random.
2. **Precision as a first-class verdict.** Some spans are labelled
   ``VERDICT_KEEP``: notable figures and names the assignment prompt supplies.
   Recall alone rewards a redactor that masks everything, and "mask everything"
   is a real failure mode for the candidate-generation approach on the table.
   Blake's pair — *"my cousin Vinny"* vs *"my inspiration, Vincent van Gogh"* —
   is in here as two frames with the same syntax and opposite verdicts, which is
   the whole reason the discriminator cannot be syntactic.
3. **Intersections.** One sentence carrying a person, an organisation and a
   location, or a notable figure beside a private one. Type confusion and
   precedence are only visible when the cases collide inside one span of text.
4. **Round-trip invariants** (:func:`check_frame`). Masking is only half the
   contract: the placeholder has to be *reversible* if feedback is ever going to
   name the student again, and a partially-masked name leaks the rest. These are
   structural properties of the transform, checkable without a model.

A third of the frames are marked ``held_out=True``. They are the ones a
trigger-tuned detector would not generalise to, so tuning on the visible set and
quoting the held-out number is the only honest way to report progress.

Nothing here spends money or touches the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from vicary.local_classifier import StudentIdentity

#: Bump on any change to the frames. Recorded on every eval row and used in the
#: resume key, because a resumed record built from a different fixture is a
#: foreign record, not a consenting one.
FIXTURE_VERSION: str = "2026-08-06.1"

VERDICT_REDACT: str = "redact"
VERDICT_KEEP: str = "keep"

#: Every placeholder the shipped classifier can emit. Anything else in masked
#: output is malformed — a truncated or nested placeholder is how a masking bug
#: presents, and it reads as ordinary prose to a downstream stage.
KNOWN_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "{NAME}",
        "{SCHOOL}",
        "{EMAIL}",
        "{URL}",
        "{US_SOCIAL_SECURITY_NUMBER}",
        "{IP_ADDRESS}",
        "{PHONE}",
        "{ADDRESS}",
        "{DATE_OF_BIRTH}",
        "{USERNAME}",
        "{ZIP_CODE}",
        "{AGE}",
        "{CREDIT_DEBIT_CARD_NUMBER}",
        # Not emitted today. Present because the ASAP training distribution has
        # them (@ORGANIZATION 1.26/essay, @LOCATION 1.06/essay) and a candidate
        # generator that fires on proper nouns will need somewhere to put them.
        "{ORGANIZATION}",
        "{LOCATION}",
    }
)

#: Deliberately loose so it matches malformed output too, which is the point.
_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_0-9]*\}")

#: The ``_1`` a numbered placeholder carries. Stripped before the token is checked
#: against :data:`KNOWN_PLACEHOLDERS` or against a span's ``expect``, so numbering
#: does not turn every well-formed placeholder into an unknown one and every
#: correctly-typed span into a ``wrong-type``.
_PLACEHOLDER_INDEX_RE = re.compile(r"_(\d+)\}$")


def placeholder_kind(token: str) -> str:
    """``"{NAME_3}"`` -> ``"{NAME}"``; an unnumbered token is returned unchanged.

    The index identifies *which* entity, the kind identifies *what* it is, and
    every invariant here is about the kind. Keeping them separable is the whole
    reason the index is a suffix rather than part of the name.
    """
    return _PLACEHOLDER_INDEX_RE.sub("}", token)

#: The ASAP authors' own anonymization tokens: ``@PERSON1``, ``@CAPS3``,
#: ``@ORGANIZATION1``, ``@LOCATION2``, ``@DATE1``, ``@NUM1``.
_ASAP_TOKEN_RE = re.compile(r"^@[A-Z]+\d*$")

#: Tokens inside a name literal too short or too common to prove a partial leak.
_WEAK_TOKENS = frozenset({"of", "van", "de", "la", "the", "der", "von", "mrs", "mr", "ms"})


@dataclass(frozen=True)
class Span:
    """One labelled substring of a frame's sentence.

    ``literal`` is scored by presence: a ``VERDICT_REDACT`` span must be absent
    from masked output, a ``VERDICT_KEEP`` span must survive intact — unless
    ``expect_count`` is set, which is the only honest way to score a bare surname.
    """

    entity: str
    literal: str
    verdict: str = VERDICT_REDACT
    #: Occurrences of ``literal`` the masked text must contain, when presence
    #: alone cannot decide the span.
    #:
    #: A bare surname is a substring of the full name it came from, so a frame
    #: that contains both — which every cross-sentence surname frame must, since
    #: the full name is the evidence under test — is UNFALSIFIABLE by presence:
    #: "Wright" survives inside a kept "Richard Wright" whether or not the bare
    #: one was masked, and "Robinson" is present inside a kept "Jackie Robinson"
    #: whether or not the neighbour leaked. Both directions score wrong, and both
    #: score wrong *silently*, which is worse than either. Counting occurrences is
    #: decisive where presence is not, so a frame of this shape declares the count
    #: it expects and the score means something.
    expect_count: int | None = None
    #: Placeholder this span should be replaced by. ``None`` means "any
    #: placeholder counts" — used where the right type is genuinely arguable.
    #: When set, a span masked as the wrong type scores as recalled but is
    #: reported separately, because inbound the mask is what matters and
    #: outbound the type is what a student reads.
    expect: str | None = None
    #: What is supposed to make a KEEP span survive. ``"notability"`` — the
    #: default and the overwhelming majority — means the gazetteer vouches for it,
    #: and a gazetteer that cannot resolve it is a defect in the asset.
    #: ``"document"`` means the *document* vouches for it: a bare surname licensed
    #: by a full name in the same essay, or an ordinary word the writer also
    #: spells lower-case. No gazetteer can resolve those and none should be asked
    #: to, so they are named here rather than absorbed into a tolerance on the
    #: notability gate — a percentage threshold silently gives every future
    #: unresolvable KEEP span the same free pass these two earned on purpose.
    kept_by: str = "notability"
    #: What is supposed to make a REDACT span disappear. ``"absence"`` — the
    #: default and nearly all of them — means the gazetteer has never heard of it,
    #: so generation proposes it and no notability tier rescues it. ``"context"``
    #: means the gazetteer *does* vouch for the string and the sentence overrides
    #: it: "My neighbor Alice Adams" is a 1921 novel and also somebody's
    #: neighbour. The distinction is asserted both ways in the asset invariants —
    #: an ``"absence"`` span that resolves notable is an asset defect, and a
    #: ``"context"`` span that does *not* resolve notable is a frame testing
    #: nothing, which is the failure mode a carve-out normally introduces.
    redacted_by: str = "absence"
    note: str = ""

    @property
    def is_keep(self) -> bool:
        return self.verdict == VERDICT_KEEP


@dataclass(frozen=True)
class Frame:
    """One sentence of ground truth.

    The sentence is stored rendered, with every span's literal appearing in it
    verbatim, so there is no template-substitution step that could silently
    change what was actually injected.
    """

    frame_id: str
    sentence: str
    spans: tuple[Span, ...]
    #: True for frames whose surface form a trigger-tuned detector would not
    #: generalise to. Score these separately or the number is self-graded.
    held_out: bool = False
    #: What the assignment prompt or source passage supplies. Names appearing
    #: here are topical by construction and are the free, exact leg of the
    #: notability filter. Empty when the frame does not exercise it.
    prompt_context: str = ""
    note: str = ""

    @property
    def redact_spans(self) -> tuple[Span, ...]:
        return tuple(s for s in self.spans if not s.is_keep)

    @property
    def keep_spans(self) -> tuple[Span, ...]:
        return tuple(s for s in self.spans if s.is_keep)


#: The identity a caller would really have supplied for this fixture's student.
#: Shared by the harness and the unit tests so they cannot drift apart. The
#: third-party names below are deliberately NOT in it: a mention of someone else
#: is the leg identity interpolation cannot reach, and it has to surface as a
#: miss rather than be quietly fed in.
def fixture_identity() -> StudentIdentity:
    return StudentIdentity(
        first_name="Marguerite",
        last_name="Delacroix-Whitfield",
        school_name="Westfield High School",
    )


def is_own_identity(span: Span) -> bool:
    """True when the span is the fixture student's own identifying string.

    The split is load-bearing, not cosmetic: the two legs run on different
    mechanisms. Identity interpolation reaches the student's own name at 100%
    because it was handed the strings; nothing shipped reaches anyone else's.
    Averaging the two hides that, which is how "89.3% recall" came to describe a
    detector with a leg at zero.
    """
    identity = fixture_identity()
    parts = [
        p for p in (identity.first_name, identity.last_name, identity.school_name)
        if p
    ]
    return any(p.lower() in span.literal.lower() for p in parts)


# ---------------------------------------------------------------------------
# Recall — third-party names, in the frames narrative essays actually use
# ---------------------------------------------------------------------------

#: Every span here must disappear. None of these names is in the identity, so
#: the shipped classifier is expected to leak all of them; that expectation is
#: the baseline, not a bug in the fixture.
RECALL_FRAMES: tuple[Frame, ...] = (
    Frame(
        frame_id="kinship-possessive",
        sentence="My cousin Terrence Okonkwo came over that summer and never left.",
        spans=(Span("NAME", "Terrence Okonkwo", expect="{NAME}"),),
        note="The only frame the previous fixture contained. Kept as the "
             "visible/tunable case so the held-out frames measure generalisation.",
    ),
    Frame(
        frame_id="bare-first-name",
        sentence="Terrence and I stayed up late finishing the whole thing.",
        spans=(Span("NAME", "Terrence", expect="{NAME}"),),
        note="No relational trigger, no surname. A trigger rule cannot see this.",
    ),
    Frame(
        frame_id="dialogue-attribution",
        sentence='"We should go back next year," said Marisol, and we did.',
        spans=(Span("NAME", "Marisol", expect="{NAME}"),),
        held_out=True,
    ),
    Frame(
        frame_id="title-surname",
        sentence="Coach Bramwell made us run laps until it got dark.",
        spans=(Span("NAME", "Coach Bramwell", expect="{NAME}"),),
        note="Surname only, behind a role title. 'Bramwell' not 'Whitfield' on "
             "purpose: the student's own surname would confound the leg.",
    ),
    Frame(
        frame_id="honorific-surname",
        sentence="Mrs. Okonkwo taught me the trick with the index cards.",
        spans=(Span("NAME", "Mrs. Okonkwo", expect="{NAME}"),),
        held_out=True,
    ),
    Frame(
        frame_id="lowercase-writing",
        sentence="then terrence okonkwo showed up and everything changed for me.",
        spans=(Span("NAME", "terrence okonkwo", expect="{NAME}"),),
        held_out=True,
        note="Students write like this. A capitalisation-based candidate "
             "generator scores zero here by construction, which is worth "
             "knowing before it ships rather than after.",
    ),
    Frame(
        frame_id="allcaps-writing",
        sentence="MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER DO THAT TO ME.",
        spans=(Span("NAME", "DESHAWN PRITCHARD", expect="{NAME}"),),
        held_out=True,
        note="The mirror of lowercase: every token is a candidate, so this is "
             "the frame where over-firing shows up as collateral, not a miss.",
    ),
    Frame(
        frame_id="appositive",
        sentence="Ayaan Chaudhary, my next-door neighbor, drove us all the way there.",
        spans=(Span("NAME", "Ayaan Chaudhary", expect="{NAME}"),),
    ),
    Frame(
        frame_id="private-surname-shadowed-by-a-notable-one",
        sentence="Jackie Robinson broke the color line in 1947. Robinson, who "
                 "lives two doors down from us, taught me how to throw.",
        spans=(Span("NAME", "Robinson", expect="{NAME}", expect_count=1),),
        note="THE PRICE OF same-document corroboration, and it is currently "
             "unpaid — this frame FAILS in the shipped arm and is scored so the "
             "cost is a number rather than a caveat in a docstring. Once a "
             "document establishes 'Jackie Robinson', a bare 'Robinson' anywhere "
             "in it keeps, including this neighbour. No surname-level rule can "
             "separate the two, because within this document the surname really "
             "is ambiguous and the writer supplied no other evidence. Kept "
             "VISIBLE rather than held out on purpose: the held-out recall number "
             "is the privacy gate, and a known accepted cost must not be able to "
             "masquerade as a fresh privacy regression there. The trade being "
             "accepted is one contrived neighbour against 27 destroyed spans in a "
             "single real essay about its author — see "
             "notable-surname-established-in-document.",
    ),
    Frame(
        frame_id="heading-capital-on-an-ordinary-word",
        sentence="\n\nBreeds I Like\n\nI wrote about horses because I like to "
                 "ride them every summer.",
        spans=(Span("NAME", "Like", verdict=VERDICT_KEEP, kept_by="document"),),
        note="The dominant over-fire class on real student prose, and the frame "
             "is copied from one: \"Breeds I Like\" is a literal heading in the "
             "Education Northwest horses paper. A heading is title-cased by "
             "convention, so every capital in it is orthographic and none of it "
             "is evidence about any word. Mid-line on purpose — sentence-initial "
             "capitals are already discounted, so a heading\'s FIRST word was "
             "never the problem; its later words are, and they are the ones that "
             "read as a deliberate mid-sentence capital. THIS REPLACES a rule "
             "that read these spans as emphasis. That story did not survive the "
             "data: across 27 un-scrubbed documents not one writer capitalised "
             "an initial letter for emphasis. Emphasis is ALL CAPS or mixed "
             "caps, which _emphasis_spans already had.",
    ),
    Frame(
        frame_id="heading-that-actually-names-somebody",
        sentence="\n\nMy Brother Terrence Okonkwo\n\nHe taught me how to ride "
                 "a bike without training wheels.",
        spans=(Span("NAME", "Terrence Okonkwo", expect="{NAME}"),),
        held_out=True,
        note="THE GUARD on the heading rule, and the reason it withholds evidence "
             "rather than vetoing. Students title sections after people, so a "
             "heading is not a licence to keep. Inside a heading the span has to "
             "clear the same bar as any other unevidenced capital — the "
             "given-name tier — and it does. A veto keyed on layout would leak "
             "this, which is precisely how the rule it replaced failed.",
    ),
    Frame(
        frame_id="notable-surname-in-a-first-person-appositive",
        sentence="Richard Wright wrote about hunger without flinching. Wright, "
                 "who taught me to look away from nothing, is why I write.",
        spans=(Span("NAME", "Wright", verdict=VERDICT_KEEP, expect_count=2,
                    kept_by="document"),),
        note="THE GUARD on relation refusal, and the reason its discriminator is "
             "a closed cue list rather than 'the appositive mentions the writer'. "
             "That was the first design and this frame kills it: literary analysis "
             "writes exactly this sentence, and refusing corroboration here "
             "re-destroys the author the essay is about — the defect corroboration "
             "was built to fix. A first-person pronoun says the SENTENCE is "
             "personal; only a relation or proximity cue says the PERSON is. "
             "expect_count=2 counts both the full name and the bare surname, so a "
             "regression cannot hide inside the kept 'Richard Wright'.",
    ),
    Frame(
        frame_id="private-person-shadowed-by-a-work-title",
        sentence="My neighbor Alice Adams walked me to the bus stop every "
                 "morning that whole year.",
        spans=(Span("NAME", "Alice Adams", expect="{NAME}",
                    redacted_by="context"),),
        held_out=True,
        note="THE TITLE TIER'S version of the Robinson problem, and the one the "
             "surname tier's fix does not reach. 'Alice Adams' is a 1921 novel, "
             "so the title tier keeps it, and 589 keys in the shipped tier have "
             "this same <common given name> <ordinary US surname> shape — every "
             "one of them shelters whichever private individual carries it. No "
             "sitelink floor separates them from the curriculum: Atticus Finch "
             "sits at 17 sitelinks and this hole at 24. So the discriminator has "
             "to be local evidence, as it was for bare surnames, and this is the "
             "frame that scores it.",
    ),
    Frame(
        frame_id="full-name-midsentence",
        sentence="I asked Marisol Ybarra what she thought about the ending.",
        spans=(Span("NAME", "Marisol Ybarra", expect="{NAME}"),),
    ),
    Frame(
        frame_id="hyphenated-surname",
        sentence="Priya Raghunathan-Bell lent me her notes for the whole week.",
        spans=(Span("NAME", "Priya Raghunathan-Bell", expect="{NAME}"),),
    ),
    Frame(
        frame_id="possessive-third-party",
        sentence="Terrence's older brother drove us to the game on Friday.",
        spans=(Span("NAME", "Terrence", expect="{NAME}"),),
        held_out=True,
        note="The possessive has to be masked with the name; leaving \"'s\" "
             "behind is cosmetic, leaving \"Terrence\" behind is a leak.",
    ),
    Frame(
        frame_id="initial-plus-surname",
        sentence="J. Okonkwo sat behind me in that class all year long.",
        spans=(Span("NAME", "J. Okonkwo", expect="{NAME}"),),
        held_out=True,
    ),
    Frame(
        frame_id="nickname-and-full-name",
        sentence="Everyone called him Terry, but Terrence Okonkwo hated that name.",
        spans=(
            Span("NAME", "Terrence Okonkwo", expect="{NAME}"),
            Span("NAME", "Terry", expect="{NAME}",
                 note="The name-linking leg: same person, second surface form. "
                      "A detector can mask the full name and still leak the "
                      "nickname, and a restore keyed on one placeholder cannot "
                      "tell the two apart."),
        ),
        held_out=True,
    ),
    Frame(
        frame_id="student-own-name",
        sentence="I am Marguerite Delacroix-Whitfield and this is my final essay.",
        spans=(Span("NAME", "Marguerite Delacroix-Whitfield", expect="{NAME}"),),
        note="The leg identity interpolation already covers, at 100%. Kept so a "
             "candidate-generation change cannot regress it unnoticed.",
    ),
)


# ---------------------------------------------------------------------------
# Precision — the names that must survive
# ---------------------------------------------------------------------------

#: Blake's precedence question lives here. Every span is a KEEP: masking one is
#: a visible product defect outbound ("your essay about {NAME}'s paintings") and
#: destroys the topical content of the essay inbound.
KEEP_FRAMES: tuple[Frame, ...] = (
    Frame(
        frame_id="notable-possessive",
        sentence="My inspiration, Vincent van Gogh, painted through his worst years.",
        spans=(Span("NAME", "Vincent van Gogh", verdict=VERDICT_KEEP),),
        note="Same first-person-possessive frame as 'my cousin X'. This pair is "
             "why the discriminator has to be notability rather than syntax.",
    ),
    Frame(
        frame_id="notable-bare-surname",
        sentence="Lincoln held the country together in the middle of a war.",
        spans=(Span("NAME", "Lincoln", verdict=VERDICT_KEEP),),
    ),
    Frame(
        frame_id="notable-name-is-also-a-place",
        sentence="Washington crossed the Delaware in the dead of winter.",
        spans=(
            Span("NAME", "Washington", verdict=VERDICT_KEEP),
            Span("LOCATION", "Delaware", verdict=VERDICT_KEEP,
                 note="A public place-name, not the student's address."),
        ),
        held_out=True,
        note="Notable person whose name is also a state and a common surname. "
             "A gazetteer keyed on exact strings keeps this; a NER pass that "
             "types it LOCATION and masks it does not.",
    ),
    Frame(
        frame_id="notable-full-name",
        sentence="Henry David Thoreau wrote about disobedience before it had a name.",
        spans=(Span("NAME", "Henry David Thoreau", verdict=VERDICT_KEEP),),
    ),
    Frame(
        frame_id="notable-in-kinship-shaped-frame",
        sentence="I read Toni Morrison the way my cousin reads box scores.",
        spans=(Span("NAME", "Toni Morrison", verdict=VERDICT_KEEP),),
        held_out=True,
        note="'my cousin' is present as a distractor and refers to nobody named. "
             "A trigger rule that fires on the phrase masks the wrong span.",
    ),
    Frame(
        frame_id="notable-single-name",
        sentence="Joan of Arc led an army when she was barely seventeen.",
        spans=(Span("NAME", "Joan of Arc", verdict=VERDICT_KEEP),),
        held_out=True,
    ),
    Frame(
        frame_id="source-author-cited",
        sentence="As Malcolm Gladwell argues, the trend is older than it looks.",
        spans=(Span("NAME", "Malcolm Gladwell", verdict=VERDICT_KEEP),),
        prompt_context="Read the passage by Malcolm Gladwell and respond to it.",
        note="The prompt_context leg: exact, free, zero false positives — but "
             "only if callers populate GraderRequest.prompt_context, which is "
             "unverified. This frame fails without the gazetteer behind it.",
    ),
    Frame(
        frame_id="prompt-context-figure",
        sentence="Rosa Parks did not plan to be the story, and that is the point.",
        spans=(Span("NAME", "Rosa Parks", verdict=VERDICT_KEEP),),
        prompt_context="Write about a moment when Rosa Parks changed what was "
                       "possible.",
        held_out=True,
    ),
    Frame(
        frame_id="work-title-with-interior-stopword",
        sentence="I read To Kill a Mockingbird in ninth grade and it changed how "
                 "I read everything after it.",
        spans=(Span("TITLE", "To Kill a Mockingbird", verdict=VERDICT_KEEP),),
        held_out=True,
        note="The frame no notability *lookup* can pass. Candidate generation "
             "splits this on the stoplisted 'a' and proposes 'To' and "
             "'Mockingbird' separately, so neither half can be resolved back to "
             "the book — the title has to be matched against the raw text before "
             "generation runs. Scored here so that ordering is a measurement "
             "rather than a claim in a docstring.",
    ),
    Frame(
        frame_id="notable-surname-established-in-document",
        sentence="Richard Wright wrote about hunger without flinching. Wright "
                 "never lets the reader look away from what it costs.",
        spans=(Span("NAME", "Wright", verdict=VERDICT_KEEP, expect_count=2,
                    kept_by="document"),),
        held_out=True,
        note="The shape literary analysis actually takes: name the author once, "
             "write the surname for the rest of the essay. The bare surname of a "
             "famous person with a common surname cannot clear the short tier's "
             "two gates at once — SHORT_MIN_SITELINKS and "
             "SHORT_MAX_US_SURNAME_POPULATION are in tension by construction — so "
             "before same-document corroboration this frame red, and on 27 real "
             "un-scrubbed student essays it red 27 times inside ONE document, "
             "which was an essay about Black Boy. Two sentences because the "
             "evidence is cross-sentence: no single-sentence frame can express "
             "it, which is why the whole class was invisible to this fixture.",
    ),
    Frame(
        frame_id="fictional-character",
        sentence="My favorite character is Atticus Finch, because he does the "
                 "right thing when it costs him something.",
        spans=(Span("NAME", "Atticus Finch", verdict=VERDICT_KEEP),),
        held_out=True,
        note="Syntactically identical to 'My cousin Terrence Okonkwo' — two "
             "capitalised words in a first-person-possessive frame — and the "
             "opposite verdict. The notability tier is built from P31 wd:Q5, "
             "human, so every fictional character resolved not-notable and was "
             "redacted; this is the pair that makes that visible.",
    ),
    Frame(
        frame_id="fictional-character-described-by-a-relation",
        sentence="Atticus Finch is the kind of father who does the right thing "
                 "even when it costs him everything.",
        spans=(Span("NAME", "Atticus Finch", verdict=VERDICT_KEEP),),
        held_out=True,
        note="THE GUARD on title-tier relation refusal, and the reason that rule "
             "cannot reuse the bare-surname cue test unchanged. Characters are "
             "described BY their relations — a father, a friend, an aunt in "
             "Queens — so a bare relation noun in the window fires on the "
             "curriculum: measured, the surname rule's discriminator refuses "
             "Atticus Finch, Harry Potter, Percy Jackson, Tom Sawyer, Peter "
             "Parker and Clark Kent, six of the seven characters it must keep. "
             "What separates them from 'My neighbor Alice Adams' is that the "
             "relation is not the WRITER'S: title-tier refusal therefore demands "
             "a first-person possessive attached to the name itself.",
    ),
    Frame(
        frame_id="work-title-near-an-unattached-first-person-relation",
        sentence="I read Harry Potter out loud with my little brother almost "
                 "every night that winter.",
        spans=(Span("NAME", "Harry Potter", verdict=VERDICT_KEEP),),
        held_out=True,
        note="THE GUARD on the adjacency half of the same rule. 'my little "
             "brother' is a genuine first-person relation and it names nobody — "
             "so proximity in the window is not enough either, and refusal has "
             "to require the relation phrase to be syntactically attached to the "
             "span: immediately before it, or inside the appositive immediately "
             "after it. Without this frame the rule could pass its other two by "
             "scanning a window for 'my' and a kinship noun, which would "
             "over-redact a book every time a student mentions who they read it "
             "with.",
    ),
)


# ---------------------------------------------------------------------------
# Intersections — where precedence and type confusion become visible
# ---------------------------------------------------------------------------

INTERSECTION_FRAMES: tuple[Frame, ...] = (
    Frame(
        frame_id="intersect-notable-beside-private",
        sentence="I wrote about Vincent van Gogh for Mrs. Okonkwo's class last spring.",
        spans=(
            Span("NAME", "Vincent van Gogh", verdict=VERDICT_KEEP),
            Span("NAME", "Mrs. Okonkwo", expect="{NAME}"),
        ),
        held_out=True,
        note="The discriminator, in one sentence. Masking both is over-firing; "
             "masking neither is the shipped behaviour; masking the wrong one "
             "is the worst outcome and is invisible to a recall-only metric.",
    ),
    Frame(
        frame_id="intersect-person-org-location",
        sentence="Terrence Okonkwo moved to Akron and got a job at Progressive Insurance.",
        spans=(
            Span("NAME", "Terrence Okonkwo", expect="{NAME}"),
            Span("LOCATION", "Akron", expect="{LOCATION}",
                 note="Masked inbound is fine and distributionally native "
                      "(@LOCATION is 1.06/essay in the training corpus); the "
                      "type is what matters, so a NAME placeholder here scores "
                      "as recalled-but-mistyped."),
            Span("ORGANIZATION", "Progressive Insurance", expect="{ORGANIZATION}"),
        ),
        note="Three types, one sentence. This is the only place type confusion "
             "is measurable — separated into three frames it is not.",
    ),
    Frame(
        frame_id="intersect-self-and-third-party",
        sentence="Marguerite and Deshawn both stayed after class to finish it.",
        spans=(
            Span("NAME", "Marguerite", expect="{NAME}",
                 note="Reached by identity interpolation."),
            Span("NAME", "Deshawn", expect="{NAME}",
                 note="Reached by nothing shipped today."),
        ),
        note="Two names, two mechanisms, one sentence — and one placeholder "
             "token for both, which is exactly what makes a restore ambiguous.",
    ),
    Frame(
        frame_id="intersect-school-and-friend",
        sentence="Deshawn and I both go to Westfield High School on the east side.",
        spans=(
            Span("NAME", "Deshawn", expect="{NAME}"),
            Span("SCHOOL", "Westfield High School", expect="{SCHOOL}"),
        ),
    ),
    Frame(
        frame_id="intersect-landmark-and-hometown",
        sentence="We drove from Akron all the way to see the Lincoln Memorial.",
        spans=(
            Span("LOCATION", "Akron", expect="{LOCATION}",
                 note="The student's hometown. Redact."),
            Span("LOCATION", "Lincoln Memorial", verdict=VERDICT_KEEP,
                 note="A public landmark and the essay's subject. Keep — and "
                      "note it contains 'Lincoln', so a substring gazetteer "
                      "hit is not enough to decide either span."),
        ),
        held_out=True,
    ),
    Frame(
        frame_id="intersect-name-beside-structured",
        sentence="Call Terrence Okonkwo at (330) 555-0148 or m.delacroix2011@westfieldhigh.k12.oh.us.",  # noqa: E501 (fixture text)
        spans=(
            Span("NAME", "Terrence Okonkwo", expect="{NAME}"),
            Span("PHONE", "(330) 555-0148", expect="{PHONE}"),
            Span("EMAIL", "m.delacroix2011@westfieldhigh.k12.oh.us",
                 expect="{EMAIL}"),
        ),
        note="Adjacent spans of different types. Pattern order decides who "
             "claims the boundary characters, and a name half-eaten by the "
             "phone pattern leaks the remainder.",
    ),
)


# ---------------------------------------------------------------------------
# Structured entities — the legs already at 100%, kept measured
# ---------------------------------------------------------------------------

STRUCTURED_FRAMES: tuple[Frame, ...] = (
    Frame(
        frame_id="structured-address",
        sentence="We moved to 1147 Beaumont Terrace, Akron, Ohio 44305 that fall.",
        spans=(Span("ADDRESS", "1147 Beaumont Terrace", expect="{ADDRESS}"),),
    ),
    Frame(
        frame_id="structured-phone",
        sentence="You can reach my mom at (330) 555-0148 most afternoons.",
        spans=(Span("PHONE", "(330) 555-0148", expect="{PHONE}"),),
    ),
    Frame(
        frame_id="structured-email",
        sentence="I sent it to m.delacroix2011@westfieldhigh.k12.oh.us by mistake.",
        spans=(Span("EMAIL", "m.delacroix2011@westfieldhigh.k12.oh.us",
                    expect="{EMAIL}"),),
    ),
    Frame(
        frame_id="structured-age",
        sentence="I am 14 years old and this is the first thing I finished.",
        spans=(Span("AGE", "14", expect="{AGE}"),),
    ),
    Frame(
        frame_id="structured-ssn",
        sentence="The form wanted 287-44-9163 which I should not have written down.",
        spans=(Span("US_SOCIAL_SECURITY_NUMBER", "287-44-9163",
                    expect="{US_SOCIAL_SECURITY_NUMBER}"),),
    ),
    Frame(
        frame_id="structured-url",
        sentence="It is posted at https://westfieldhigh.k12.oh.us/students/mdelacroix now.",
        spans=(Span("URL", "https://westfieldhigh.k12.oh.us/students/mdelacroix",
                    expect="{URL}"),),
    ),
    Frame(
        frame_id="structured-username",
        sentence="My handle is @margie_dw2011 if you ever want to look it up.",
        spans=(Span("USERNAME", "@margie_dw2011", expect="{USERNAME}"),),
    ),
    Frame(
        frame_id="structured-card",
        sentence="She read out 4532 7891 2345 6789 and I typed it in wrong.",
        spans=(Span("CREDIT_DEBIT_CARD_NUMBER", "4532 7891 2345 6789",
                    expect="{CREDIT_DEBIT_CARD_NUMBER}"),),
    ),
)


ALL_FRAMES: tuple[Frame, ...] = (
    *RECALL_FRAMES,
    *KEEP_FRAMES,
    *INTERSECTION_FRAMES,
    *STRUCTURED_FRAMES,
)


def frames(*, held_out: bool | None = None,
           groups: tuple[str, ...] | None = None) -> tuple[Frame, ...]:
    """Select frames. ``held_out=None`` means both splits.

    ``groups`` filters by ``recall``/``keep``/``intersect``/``structured``.
    """
    by_group = {
        "recall": RECALL_FRAMES,
        "keep": KEEP_FRAMES,
        "intersect": INTERSECTION_FRAMES,
        "structured": STRUCTURED_FRAMES,
    }
    if groups:
        unknown = set(groups) - set(by_group)
        if unknown:
            raise ValueError(f"unknown fixture group(s): {sorted(unknown)}")
        pool: tuple[Frame, ...] = tuple(
            f for g in groups for f in by_group[g]
        )
    else:
        pool = ALL_FRAMES
    if held_out is None:
        return pool
    return tuple(f for f in pool if f.held_out is held_out)


# ---------------------------------------------------------------------------
# Structural invariants of the masking transform
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """One broken invariant, named so it can be counted by kind."""

    kind: str
    detail: str


@dataclass
class Alignment:
    """Which original region each emitted placeholder replaced.

    Recovered by chunk matching rather than asked of the redactor, so it works
    against any masker — the local classifier, a Bedrock Guardrail, or whatever
    lands next — without that masker having to report its own spans. A masker's
    self-report of what it masked is a self-report (MUST #6).
    """

    #: ``(placeholder, original_region)`` in output order.
    pairs: list[tuple[str, str]] = field(default_factory=list)
    #: False when the masked text cannot be explained as "delete spans, insert
    #: placeholders" — which means something rewrote prose it should not have.
    ok: bool = True
    reason: str = ""

    @property
    def collateral_chars(self) -> int:
        return sum(len(region) for _, region in self.pairs)


def align(original: str, masked: str) -> Alignment:
    """Recover the span→placeholder mapping by matching the surviving prose.

    Splits ``masked`` at placeholder boundaries and walks the resulting literal
    chunks through ``original`` in order. The gap consumed between two chunks is
    what the placeholder between them replaced.
    """
    parts = _PLACEHOLDER_RE.split(masked)
    placeholders = _PLACEHOLDER_RE.findall(masked)
    if not placeholders:
        if masked != original:
            return Alignment(ok=False, reason="text changed with no placeholder emitted")
        return Alignment()

    # Anchored, all at once, rather than a left-to-right scan for each chunk in
    # turn. A greedy per-chunk ``find`` misaligns whenever a surviving chunk is
    # short enough to also occur inside the span that was just removed — a
    # trailing "." after a masked email address matches the "." inside the
    # address, and the recovered region collapses to one character. ``fullmatch``
    # makes the whole reconstruction consistent simultaneously, so a candidate
    # that cannot be completed to the end of the original is rejected and the
    # engine backtracks. The chunks are long and distinctive prose, which is what
    # keeps the lazy quantifiers from exploring.
    pattern = re.escape(parts[0]) + "".join(
        r"([\s\S]*?)" + re.escape(chunk) for chunk in parts[1:]
    )
    found = re.fullmatch(pattern, original)
    if found is None:
        return Alignment(
            ok=False,
            reason="masked text is not the original with spans replaced — "
                   "prose was rewritten, reordered or dropped",
        )
    return Alignment(pairs=list(zip(placeholders, found.groups(), strict=True)))


def is_asap_token(region: str) -> bool:
    """True when a masked region was one of ASAP's own anonymization tokens.

    Load-bearing for the over-firing metric, because the two legs it separates
    have nothing to do with each other. Masking genuine prose is a precision
    defect. Masking ``@PERSON1`` is not — the PII is already gone — but it does
    rewrite a token a model trained on the corpus has *seen* into one it has not,
    at ~22 per essay, and the current ``{USERNAME}`` pattern does exactly that
    to every ``@``-token in the corpus. Averaged together the two look like one
    catastrophic precision failure; separated, one is zero and the other is a
    replay confound.
    """
    return bool(_ASAP_TOKEN_RE.match(region.strip()))


def leak_probes(span: Span) -> tuple[str, ...]:
    """Substrings whose survival proves a partial leak of ``span``.

    A name masked halfway still identifies the person, so "the whole literal is
    gone" is too weak a test on multi-token names. Each meaningful token has to
    be gone too.
    """
    if span.entity not in {"NAME", "SCHOOL", "ORGANIZATION", "LOCATION"}:
        return ()
    tokens = [t for t in re.split(r"[\s\-]+", span.literal) if t]
    return tuple(
        t.strip(".,'")
        for t in tokens
        if len(t.strip(".,'")) >= 3 and t.strip(".,'").lower() not in _WEAK_TOKENS
    )


def check_frame(frame: Frame, masked: str) -> list[Violation]:
    """Every structural invariant the masked text must satisfy.

    Verdict invariants:
      ``leak`` — a REDACT literal survived.
      ``partial-leak`` — the literal is gone but a name token of it survived.
      ``keep-destroyed`` — a KEEP literal was masked.
    Well-formedness:
      ``unknown-placeholder`` — output carries a brace token nobody emits.
      ``chunk-alignment`` — the output is not the original minus spans plus
        placeholders, so prose was rewritten rather than replaced.
    Round-trip:
      ``not-restorable`` — one placeholder token stands for two different
        originals, so no map keyed on the token can put them back. This is the
        deficit numbering fixes (``{NAME}`` → ``{NAME_1}``), and it was measured
        before the fix rather than assumed: unnumbered output round-tripped 36% of
        injected essays with 37 violations of this kind. See
        :class:`vicary.name_candidates.PlaceholderMinter`.
      ``wrong-type`` — masked, but as the wrong entity. Harmless inbound;
        outbound it is what the student reads.
    """
    out: list[Violation] = []

    for tok in set(_PLACEHOLDER_RE.findall(masked)):
        if placeholder_kind(tok) not in KNOWN_PLACEHOLDERS:
            out.append(Violation("unknown-placeholder", tok))

    for span in frame.redact_spans:
        if span.literal in masked:
            out.append(Violation("leak", f"{span.entity}:{span.literal}"))
            continue
        for probe in leak_probes(span):
            if re.search(rf"\b{re.escape(probe)}\b", masked):
                out.append(
                    Violation("partial-leak", f"{span.entity}:{span.literal} → {probe}")
                )

    for span in frame.keep_spans:
        if span.literal not in masked:
            out.append(Violation("keep-destroyed", f"{span.entity}:{span.literal}"))

    alignment = align(frame.sentence, masked)
    if not alignment.ok:
        out.append(Violation("chunk-alignment", alignment.reason))
        return out

    seen: dict[str, str] = {}
    for placeholder, region in alignment.pairs:
        prior = seen.get(placeholder)
        if prior is not None and prior != region:
            out.append(
                Violation("not-restorable", f"{placeholder} ← {prior!r} and {region!r}")
            )
        seen.setdefault(placeholder, region)

    for span in frame.redact_spans:
        if span.expect is None or span.literal in masked:
            continue
        covering = [
            placeholder_kind(p)
            for p, region in alignment.pairs
            if span.literal in region
        ]
        if covering and span.expect not in covering:
            out.append(
                Violation(
                    "wrong-type",
                    f"{span.literal!r} expected {span.expect} got {covering[0]}",
                )
            )

    return out


def restore(masked: str, mapping: dict[str, str]) -> str:
    """Put the originals back, the way an echo-fidelity restore would have to.

    Keyed on the placeholder token and applied left to right, because that is
    all a downstream consumer has: the model echoes ``{NAME}`` and the caller
    must decide which name it meant. With one token per entity type it cannot,
    which is what ``not-restorable`` counts.
    """

    def _sub(match: re.Match[str]) -> str:
        return mapping.get(match.group(0), match.group(0))

    return _PLACEHOLDER_RE.sub(_sub, masked)


def round_trips(frame: Frame, masked: str) -> bool:
    """True when the frame's sentence survives mask-then-restore exactly."""
    alignment = align(frame.sentence, masked)
    if not alignment.ok:
        return False
    mapping: dict[str, str] = {}
    for placeholder, region in alignment.pairs:
        mapping.setdefault(placeholder, region)
    return restore(masked, mapping) == frame.sentence
