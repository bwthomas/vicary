"""Find the person-names a student wrote, so the notability filter can decide.

The shipped classifier masks only names it was *handed* — the student's own, from
their account — so it leaks every third-party name a student mentions: measured
at **0.0% on the held-out fixture frames**, in every frame, not sometimes. This
module is the other half: generate candidates from the text itself.

Precedence, and why it runs this way round
------------------------------------------
Finding capitalised name-shaped spans in English student prose is close to free.
The hard half is deciding which ones to *keep*, and the two cases look identical
syntactically:

    My cousin Terrence Okonkwo came over that summer     → redact
    My inspiration, Vincent van Gogh, painted for years  → keep

Both are first-person possessive, so a relational-trigger rule ("``my
<kinship-noun> <Name>``") gets van Gogh wrong. The discriminator has to be
**notability**, which is a lookup rather than a model. So: generate broadly here,
then ``notable → keep, everything else → redact``. Default-deny on names with an
allowlist of public figures, not the reverse.

Why generating broadly is the right bias
----------------------------------------
Essay-scoring corpora are distributed pre-anonymized: in ASAP, *all* proper nouns
are already placeholders — ``@PERSON``, ``@CAPS``, ``@ORGANIZATION``,
``@LOCATION`` at 22.6 tokens per essay. A model trained on such a corpus is not
perturbed by a redactor that masks proper nouns; masking moves its input *toward*
the training distribution, and text with real names in it is the
out-of-distribution case. So on the **inbound** path over-masking is
cheap, and recall is what to buy. Outbound is the opposite — a student reading
"great job describing your trip to ``{LOCATION}``" is a visible product defect —
which is why the notability oracle is injected rather than hardcoded, and can be
made stricter for the outbound pass.

Two routes in, and why the second one exists
--------------------------------------------
:data:`_CANDIDATE_RE` keys on capitalisation, which is free and covers the way
most students write. It scores **zero by construction** on ``then terrence
okonkwo showed up`` — there is no capital letter to key on — and the fixture says
so in as many words. So there is a second route: a case-insensitive scan seeded
on the gazetteer's given-name tier, wired in through :data:`GivenNameOracle`. See
:func:`_find_lowercase_candidates` for what it will and will not fire on.

Capitalisation is a clue, never the answer
------------------------------------------
Both routes weigh case rather than obeying it, because a writer who capitalises
most of their proper nouns still misses some and informal writers shout in ALL
CAPS. Three rules, each measured on 27 un-scrubbed student documents rather than
argued from the shape of English:

* A single-token span whose capital sits at a *sentence start* rests on a capital
  orthography required, so it needs a second signal — the given-name tier, or the
  document capitalising that same word mid-sentence somewhere else. See
  :func:`_capital_is_the_only_evidence` and :func:`_mid_sentence_capitals`.
* An all-caps run shorter than :data:`_ALLCAPS_RUN` inside mixed-case prose is
  emphasis. See :func:`_emphasis_spans`.
* :func:`document_capitalises_names` raises the lowercase route's bar rather than
  closing the route, which is what suppressing it outright used to do.

All three are conditional on a given-name oracle being supplied: without one the
document's own capitals are the only evidence channel, and there is nothing to
corroborate a name mentioned once at a sentence start.

What runs *before* either route
-------------------------------
:func:`find_title_spans` protects the books, films and characters a student writes
*about*. It has to run against the raw text rather than against candidates,
because generation never produces a whole title: "To Kill a Mockingbird" splits on
the stoplisted "a" and comes back as "To {NAME} a {NAME}", which no lookup on
either half can undo.

``allcaps-writing`` remains partially handled by the first route. In an all-caps
sentence capitalisation carries no information, so the stoplist is doing all the
work and it will both miss names and over-fire on ordinary words. See
``eval_recall`` for both numbers.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

#: Answers "is this a public figure, or otherwise topical?" ``True`` means keep.
#: Injected rather than hardcoded so the inbound pass (recall-biased) and the
#: outbound pass (precision-biased) can supply different oracles, and so the
#: offline gazetteer is a dependency rather than a hard import.
NotabilityOracle = Callable[[str], bool]

#: Answers "*which kind* of public thing is this?", returning the gazetteer's
#: tier name. A strictly richer signal than :data:`NotabilityOracle`, and needed
#: only where "keep" is too coarse an answer: surname corroboration must fire on
#: a human's full name and on nothing else. The boolean oracle cannot express
#: that, and the consequence was measured rather than imagined — "Pintos are from
#: America" let a kept *place* establish "america" as a surname, and the same
#: mechanism would have let "Lake Powell" license a classmate's bare "Powell".
NotabilityTierOracle = Callable[[str], str]

#: The tier a candidate must resolve to before it may establish a surname.
#: A place, a landmark, a work title and an already-bare iconic surname are all
#: excluded: none of them is a person written first-name-then-surname, so none of
#: them carries evidence about what a bare surname in the same document means.
CORROBORATING_TIER: str = "full_name"

#: The tiers whose keeps a first-person relation may override. Both are built
#: from strings that are *also* ordinary people's names: 578 title keys and
#: **33,682 full-name keys** are a common given name beside an ordinary US
#: surname ("Alice Adams" is a 1921 novel; "Alan Ford" is a footballer), and each
#: one keeps whichever private individual happens to carry it — 33,269 of them
#: measured doing exactly that.
#:
#: `full_name` is in here on a measurement that RETRACTS the reason it was left
#: out. The stated reason was that overriding it would redact "my hero Abraham
#: Lincoln"; it does not, because *hero*, *muse*, *inspiration*, *role model* and
#: *favourite* are admiration invocations and none of them is in
#: :data:`_RELATION_CUES`. Those pair with public figures as readily as with
#: relatives, which is precisely why they are not evidence — and it is why the
#: cue list is closed and hand-written rather than "any noun before the name".
#:
#: `place` and `iconic_short` are excluded and stay excluded. A place is not a
#: person, and a bare iconic surname has its own document-level rule with its own
#: guard (:func:`names_someone_in_the_writers_life`).
OVERRIDABLE_TIERS: frozenset[str] = frozenset({"title", "full_name"})

#: Answers "do lots of notable people share this first name?" — the **inverse**
#: signal to :data:`NotabilityOracle`. ``True`` is evidence the token names a
#: person, which on the inbound path means redact rather than keep. Injected for
#: the same reason: the gazetteer stays a dependency rather than a hard import,
#: and supplying it is what turns the lowercase route on.
GivenNameOracle = Callable[[str], bool]

#: Answers "is this string a published work or a fictional character?" ``True``
#: means keep. Separate from :data:`NotabilityOracle` because a title cannot be
#: resolved by looking up a candidate — candidate generation never *produces* the
#: whole title. "To Kill a Mockingbird" splits on the stoplisted "a" and comes
#: back as "To {NAME} a {NAME}", so the title has to be matched against the raw
#: text before generation runs, not against a candidate afterwards.
TitleOracle = Callable[[str], bool]

#: How many tokens a title match may span. The tier's longest entry is 36 tokens,
#: but scanning that far costs 36 lookups per token position for titles nobody
#: writes in an essay. 8 covers "To Kill a Mockingbird", "The Curious Incident of
#: the Dog in the Night-Time" is 10 and is NOT matched — a named limit, not an
#: oversight.
_TITLE_MAX_TOKENS: int = 8

#: Role titles and honorifics that introduce a name. Part of the span: masking
#: "Okonkwo" out of "Mrs. Okonkwo" leaves the relationship and the surname's
#: position, and students name teachers and coaches constantly.
_HONORIFICS: tuple[str, ...] = (
    "Mr", "Mrs", "Ms", "Miss", "Mx", "Dr", "Prof", "Professor", "Coach",
    "Officer", "Principal", "Rev", "Reverend", "Sgt", "Sergeant", "Capt",
    "Captain", "Sir", "Madam", "Fr", "Sister", "Brother", "Nurse", "Chief",
    "Aunt", "Uncle", "Grandma", "Grandpa", "Grandmother", "Grandfather",
    "Cousin", "Auntie",
)

#: Lowercase particles that sit *inside* a name. Without these, "Vincent van
#: Gogh" generates two candidates and the gazetteer has to know both halves.
_PARTICLES: tuple[str, ...] = (
    "van", "von", "de", "del", "della", "der", "den", "di", "da", "du", "la",
    "le", "los", "bin", "ibn", "al", "of", "the", "y",
)

#: Suffixes that make a capitalised span an organisation rather than a person.
#: Typed separately because the placeholder is what a student reads outbound.
_ORG_SUFFIXES: frozenset[str] = frozenset(
    {
        "inc", "inc.", "llc", "ltd", "corp", "corp.", "corporation", "company",
        "co", "co.", "insurance", "bank", "hospital", "clinic", "university",
        "college", "school", "academy", "institute", "foundation", "church",
        "temple", "mosque", "synagogue", "association", "society", "union",
        "department", "agency", "bureau", "committee", "council", "league",
        "team", "club", "store", "market", "restaurant", "airlines", "motors",
        "industries", "systems", "technologies", "group", "partners", "holdings",
    }
)

#: Suffixes that make a capitalised span a public landmark — topical by
#: construction, so kept without consulting the gazetteer. "Lincoln Memorial"
#: is the essay's subject; "Akron" in the same sentence is the student's town.
_LANDMARK_SUFFIXES: frozenset[str] = frozenset(
    {
        "memorial", "monument", "museum", "cathedral", "capitol", "bridge",
        "tower", "stadium", "arena", "park", "gardens", "canyon", "falls",
        "island", "mountain", "mountains", "river", "lake", "ocean", "sea",
        "desert", "valley", "peninsula", "statue", "palace", "castle", "temple",
        "pyramid", "wall", "trail", "highway", "zoo", "aquarium", "planetarium",
        "observatory", "library",
    }
)

#: Capitalised words that are not names. Deliberately broad: this list is the
#: only thing standing between candidate generation and "mask every capitalised
#: word", and a capitalised ordinary word is overwhelmingly sentence-initial.
#: Skewed toward over-inclusion on purpose — a missed name is one span and shows
#: up in the recall number, while a wrongly-masked common word corrupts every
#: essay that uses it and shows up nowhere unless somebody reads the prose.
_STOP_WORDS: frozenset[str] = frozenset(
    word.lower()
    for word in """
    a an the this that these those there here it its it's
    i me my mine myself we us our ours ourselves you your yours
    he him his she her hers they them their theirs who whom whose which what
    and or but so because although though however therefore thus hence yet
    if then else when while until since before after during once whenever
    for from to into onto out off over under above below between among across
    through around about against along beside besides beyond within without
    at by in on up down near next last first second third finally
    is am are was were be been being have has had having do does did doing
    can could will would shall should may might must let lets
    not no nor none nothing never always sometimes often usually rarely
    all any both each every few many more most much several some such
    another other others same different new old good bad better best worst
    great big small long short high low young happy sad hard easy
    one two three four five six seven eight nine ten hundred thousand million
    also even just only really very too still again ever else quite rather
    call called come came go went get got give gave take took make made
    see saw look looked think thought know knew say said tell told ask asked
    want wanted need needed try tried help helped work worked feel felt
    find found keep kept leave left put set start started stop stopped
    remember remembered learn learned teach taught write wrote read
    everyone everybody someone somebody anyone anybody nobody everything
    something anything people person thing things time times day days
    year years week weeks month months hour hours minute minutes
    school schools class classes teacher teachers student students friend
    friends family families home house mom dad mother father parent parents
    brother sister sisters brothers grandma grandpa
    life world way ways place places part parts kind sort lot lots
    yes yeah ok okay maybe perhaps well now today tomorrow yesterday
    january february march april may june july august september october
    november december monday tuesday wednesday thursday friday saturday sunday
    mr mrs ms dr am pm usa us u.s tv
    im ive ill id dont cant wont didnt isnt aint thats theres whats
    as than instead unless whether either neither plus versus etc
    getting making looking thinking talking playing living walking running
    sitting standing growing learning moving trying using
    back away together alone everywhere somewhere anywhere nowhere
    right wrong true false sure certain important special favorite
    """.split()
)

#: Contraction and possessive tails. ``[A-Z][A-Za-z'’]*`` matches "I'm" as one
#: token, so without stripping these the stoplist never sees the word — "I'm"
#: and "As" were the two most common over-fires on real prose. The
#: *un*-apostrophized spellings students actually type ("im", "dont", "thats")
#: cannot be stripped this way because there is no clitic boundary to find, so
#: they are listed in :data:`_STOP_WORDS` directly. "im" is a given name in
#: Wikidata, which is how "im faithfull" and "im going" became name candidates.
_CLITICS: tuple[str, ...] = (
    "n't", "n’t", "'s", "’s", "'m", "’m", "'re", "’re", "'ve", "’ve",
    "'ll", "’ll", "'d", "’d", "'t", "’t",
)

#: An all-caps run this long or longer means capitalisation is not a signal, so
#: the stoplist carries the whole decision and a capital neither helps nor hurts.
#: A run *shorter* than this in an otherwise mixed-case document is the opposite
#: case: informal writers put one or two words in caps to shout, and "SLAM",
#: "WHACK" and "Nooooooo" are not names. Measured on 27 un-scrubbed student
#: documents, short all-caps runs were emphasis in every instance.
_ALLCAPS_RUN: int = 3

#: Any word token, used to find all-caps runs and mid-sentence capitals.
_WORD_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*")

#: Where a sentence begins: start of text, after terminal punctuation and any
#: closing quote, after a line break, or immediately inside an *opening* quote. A
#: capital in one of these positions is required by orthography, so it is
#: evidence of nothing — which is the whole of the objection to treating a
#: capital as proof that a word is a name.
#:
#: The opening-quote arm was missing, and quoted material is how feedback refers
#: to a student's own words: "vivid words like 'Giggles filled the school'" put a
#: capital on `Giggles` for the same orthographic reason a full stop does, and it
#: masked as a name in text a student reads. Only the *capital* is discounted —
#: a real name inside quotes still carries the given-name tier, so "words like
#: 'Marisol' stand out" is unaffected.
#:
#: An apostrophe inside a word cannot match: the quote must not be preceded by a
#: letter, so "don't" and "Narciso's" are untouched.
_SENTENCE_BREAK = re.compile(
    r"(?:\A|[.!?][\"'’”\)]*\s+|\n+|(?:(?<=\s)|\A)[\"'‘“](?=[A-Za-z]))\s*"
)

#: One entirely-lowercase word. The leading ``\b`` is what keeps this from
#: matching the tail of a capitalised word — there is no word boundary between
#: the "T" and the "errence" of "Terrence", so the capitalised route keeps
#: exclusive claim on anything it can see.
_LOWER_TOKEN = re.compile(r"\b[a-z][a-z'’-]*")

#: Tokens a lowercase span must reach before it is emitted at all. Set to 2
#: deliberately, and it is the single decision that makes this route affordable —
#: see :func:`_find_lowercase_candidates`.
_LOWERCASE_MIN_TOKENS: int = 2

#: Determiners that make the word after them a common noun rather than a name.
#: "a little bit", "the guy thats", "our joy" — English does not put a bare
#: determiner in front of a person's given name, so this is a clean structural
#: signal rather than a word blacklist, and it does not grow with the corpus.
#: Measured on 25 ASAP essays it accounted for 22 of ~34 lowercase over-fire
#: seeds, `a` alone for 12. Possessives are included: a student writes "my cousin
#: terrence", never "my terrence".
_DETERMINERS: frozenset[str] = frozenset(
    """
    a an the this that these those
    my your his her its our their
    some any no every each either neither both all
    another other such one two three
    most much many few several enough
    """.split()
)


@dataclass(frozen=True)
class Candidate:
    """A name-shaped span, with the placeholder it would be masked as."""

    text: str
    start: int
    end: int
    #: ``NAME`` or ``ORGANIZATION``. LOCATION is deliberately absent: telling a
    #: place from a person needs NER, and per the module docstring the inbound
    #: path does not need the distinction — both are placeholders in the training
    #: distribution. A location therefore masks as ``{NAME}`` and is reported by
    #: the harness as recalled-but-mistyped rather than as a leak.
    kind: str = "NAME"

    @property
    def placeholder(self) -> str:
        return "{ORGANIZATION}" if self.kind == "ORGANIZATION" else "{NAME}"


_HONORIFIC_SET: frozenset[str] = frozenset(h.lower() for h in _HONORIFICS)
_HONORIFIC_ALT = "|".join(_HONORIFICS)
_PARTICLE_ALT = "|".join(_PARTICLES)
#: One capitalised word, hyphens and apostrophes included so
#: "Raghunathan-Bell" and "O'Brien" stay whole, and the possessive comes with
#: the name rather than being left behind as a fragment.
_WORD = r"[A-Z][A-Za-z'’]*(?:-[A-Z][A-Za-z'’]*)*"

_CANDIDATE_RE = re.compile(
    rf"""
    \b
    (?: (?: {_HONORIFIC_ALT} ) \.? \s+ )?      # Mrs. / Coach
    (?: [A-Z] \. \s* )*                        # J.
    {_WORD}
    (?: \s+ (?: (?: {_PARTICLE_ALT} ) \s+ )? {_WORD} )*
    """,
    re.VERBOSE,
)

#: Spans that are already redacted and must be left strictly alone. Two kinds,
#: and both were live defects rather than hypotheticals:
#:
#: * ``{NAME}`` — our own placeholders. The bare word inside the braces is
#:   capitalised, so without this a second pass generates "NAME" as a candidate
#:   and masking stops being idempotent. Both directions run this classifier and
#:   the outbound pass sees text the inbound pass already masked.
#: * ``@PERSON1`` — an upstream anonymization marker. The ``@`` is not part of a
#:   capitalised-word match, so ``PERSON`` matched on its own and every ASAP
#:   marker's kind-word became a candidate: 23.24 spans/essay of "over-firing"
#:   that was really this. Anything still carrying an ``@`` at this point is
#:   either a marker or a handle the USERNAME pattern already had its chance at,
#:   and leaving both alone is correct.
_PROTECTED = re.compile(r"\{[A-Za-z_0-9]*\}|@[A-Za-z]+\d*")


def _is_stop(token: str) -> bool:
    word = token.lower().strip(".,")
    for clitic in _CLITICS:
        if word.endswith(clitic) and len(word) > len(clitic):
            word = word[: -len(clitic)]
            break
    return word.strip("'’") in _STOP_WORDS


def _classify(tokens: list[str]) -> str:
    tail = tokens[-1].lower().strip(".,")
    if tail in _ORG_SUFFIXES:
        return "ORGANIZATION"
    return "NAME"


def _trim(tokens: list[str]) -> list[list[str]]:
    """Drop stoplisted tokens, splitting the span where one sits inside it.

    "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER" is one match, because in an
    all-caps sentence every token is capitalised. Trimming the edges is not
    enough — the name is in the middle — so an interior stopword ends the run
    and starts a new one.

    The exception is an honorific introducing a name. ``Mrs`` and ``Dr`` are in
    the stoplist so that a bare "Mrs." cannot become a candidate on its own, but
    "Mrs. Okonkwo" has to stay whole: masking only the surname leaves the
    relationship and the surname's position in the text.
    """
    runs: list[list[str]] = []
    current: list[str] = []
    for index, token in enumerate(tokens):
        introduces_a_name = (
            token.lower().strip(".,") in _HONORIFIC_SET
            and index + 1 < len(tokens)
            and not _is_stop(tokens[index + 1])
        )
        if _is_stop(token) and not introduces_a_name:
            if current:
                runs.append(current)
                current = []
            continue
        current.append(token)
    if current:
        runs.append(current)
    return runs


#: Any word token, either case. Used only by the title scan, which cannot key on
#: capitalisation because a student may write a title however they like.
_ANY_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*")

#: The one fold the title scan applies before consulting the prefix index. A word
#: processor turns every apostrophe curly, so "Charlotte’s Web" tokenises with a
#: character the gazetteer's keys never contain and the walk would stop on its
#: first token. Deliberately not the gazetteer's full ``normalize``: that does an
#: NFKD decomposition and a per-character rebuild, and this runs once per word of
#: every essay. An accented title head still fails the walk, which loses a keep
#: and never a redaction.
_CURLY_APOSTROPHE = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "′": "'"})

#: A capitalised word that is *not* sentence-initial. "I" is excluded because
#: every writer capitalises it whether or not they capitalise names, so it is the
#: one capital that says nothing about their habits.
_MID_SENTENCE_CAP = re.compile(r"(?<=[a-z,;:]\s)([A-Z][a-z]{2,})")

#: Mid-sentence capitals above which a document is taken to mark its proper nouns
#: with capitals — at which point a *lowercase* token is evidence against a name
#: and the lowercase route is suppressed.
#:
#: Measured on 36 un-scrubbed essay documents (~3,300 chars each, Project
#: Gutenberg) against a lower-cased copy of the same text: as written the median
#: is 10.5 and 35/36 documents are non-zero; lower-cased every document is 0.
#: Clean separation, so the threshold is not delicate — 2 rather than 1 only to
#: tolerate a single stray capital.
#:
#: This could not be measured on ASAP, whose authors replaced every proper noun
#: with an ``@CAPS``-style marker before release; there the median is 2 and the
#: lowercase fixture frame is 0, so no threshold separates them. That was a
#: property of the corpus, not of the signal.
_CAPITALISES_NAMES_MIN: int = 2

#: A sentence opening on a lower-case letter, which is the writer telling us
#: directly that they are not keeping standard capitalisation. Matched at the
#: start of the text as well as after a sentence break.
_LOWERCASE_SENTENCE_START = re.compile(
    r"(?:\A|(?<=[.!?]\s))\s*[a-z]", re.MULTILINE
)

#: A bare lower-case first-person "i" — the other unambiguous tell, and the one
#: that survives a writer who does capitalise sentence openings.
_BARE_LOWERCASE_I = re.compile(r"\bi\b")


def writes_without_standard_capitals(text: str) -> bool:
    """Positive evidence that this writer is not keeping standard capitalisation.

    The companion to :func:`document_capitalises_names`, and the reason it needs
    one: that function answers "did this document capitalise its proper nouns",
    and a **no** has two completely different causes. Either the writer does not
    capitalise — the case the lowercase route exists for — or the text simply
    contains no proper nouns to capitalise. Nothing distinguished them, so the
    second was being served the first's permissive treatment.

    That is not hypothetical. The outbound pass scores single feedback fields of
    108-290 characters, ordinary well-formed prose about a student's essay. Every
    one scored zero mid-sentence capitals, every one was therefore read as
    lower-case writing, and the route ran with no corroboration required: "tone
    toward", "line makes", "line circles" and "line loops" masked as names in
    text a student reads. Both `tone` and `line` are genuine given names, so the
    seed was legitimate and the absent guard was the entire defect.

    Two tells, both of them the writer's own doing rather than an inference from
    what is missing: a sentence opening in lower case, and a bare lower-case "i".
    Prose that keeps both conventions is making a positive claim about its own
    orthography, and a lower-case token inside it is evidence against a name for
    the same reason a mid-sentence capital is evidence for one.
    """
    return bool(
        _LOWERCASE_SENTENCE_START.search(text) or _BARE_LOWERCASE_I.search(text)
    )


def document_capitalises_names(text: str) -> bool:
    """Whether this document marks its proper nouns with capital letters.

    A writer who capitalises names has told us something about every *lowercase*
    token in the document: it is probably not one. A writer who does not has told
    us nothing, and the given-name tier is the only handle left.

    What this does NOT do is decide whether the lowercase route runs. It used to,
    and that cost every uncapitalised occurrence of a name in a document that
    capitalises the rest — which is most students most of the time, since
    capitalising proper nouns is a habit rather than a rule anyone keeps perfectly.
    It now selects how much evidence a lowercase seed needs: see
    :func:`_find_lowercase_candidates`'s ``corroborate``.

    Sentence-initial capitals are deliberately not counted. A student who
    capitalises the start of each sentence but not the names inside them is
    exactly the case the lowercase route exists for, and counting those capitals
    would suppress the route on them.
    """
    return sum(1 for _ in _MID_SENTENCE_CAP.finditer(text)) >= _CAPITALISES_NAMES_MIN


def _sentence_starts(text: str) -> frozenset[int]:
    """Offsets at which a sentence begins."""
    return frozenset(m.end() for m in _SENTENCE_BREAK.finditer(text))


def _emphasis_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Character ranges of all-caps runs SHORTER than :data:`_ALLCAPS_RUN`.

    A long all-caps run is a writer who has stopped using case at all, and the
    stoplist handles it. A one- or two-word run inside mixed-case prose is
    emphasis — the informal register's italics — and it is where "SLAM",
    "WHACK", "LAUGHTER" and "REDACT" came from on real student writing.

    Single-character tokens are excluded: "I" is upper-case for every writer, and
    the initials in "J. R. Tolkien" are part of a name rather than a shout.
    """
    runs: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    for match in _WORD_TOKEN.finditer(text):
        token = match.group(0)
        if len(token) > 1 and token.isupper():
            current.append(match.span())
            continue
        if current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return tuple(
        (run[0][0], run[-1][1]) for run in runs if len(run) < _ALLCAPS_RUN
    )


def _mid_sentence_capitals(
    text: str,
    starts: frozenset[int],
    headings: tuple[tuple[int, int], ...] = (),
) -> frozenset[str]:
    """Lower-cased forms of every word this document capitalises mid-sentence.

    The document's own testimony about a particular word, which is the graded
    version of :func:`document_capitalises_names`. A writer who put a capital on
    "Cade" somewhere other than a sentence start has told us "Cade" is a name in
    this document; one who only ever writes "Eventually" after a full stop has
    told us nothing, because orthography would have put that capital there
    anyway.

    An entirely upper-case token is excluded, and that exclusion is load-bearing
    rather than tidy. Without it "SLAM" corroborates itself — the token is its own
    mid-sentence capital — so every emphasis shout would clear the bar the
    emphasis rule had just raised. A capital is testimony only where the writer
    had a lower-case alternative and declined it.
    """
    out: set[str] = set()
    for match in _WORD_TOKEN.finditer(text):
        token = match.group(0)
        if match.start() in starts or not token[0].isupper():
            continue
        if len(token) > 1 and token.isupper():
            continue
        # A heading is title-cased, so its non-initial capitals are orthographic
        # too. Counting them let "The First Horses" vouch for "Horses" as a name
        # -- the heading corroborating itself, one line removed.
        if any(match.start() < h_end and match.end() > h_start
               for h_start, h_end in headings):
            continue
        out.add(token.lower().strip("'’"))
    return frozenset(out)


#: Longest line still readable as a heading. Body prose in these documents is
#: hard-wrapped at ~60–590 chars per line, so length alone does not separate a
#: heading from a wrapped line — the blank line above it is what does.
_HEADING_MAX_CHARS: int = 60


def _heading_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Character ranges of lines that are section headings, not prose.

    A heading is title-cased by convention, so **every capital in it is
    orthographic** and none of it is testimony about any word. This replaces a
    rule that read the same spans as emphasis, which the data does not support:
    across the 27 un-scrubbed documents there was not one instance of a writer
    capitalising an initial letter for emphasis. Emphasis in student prose is
    ALL CAPS ("this is BULLSHIT") or mixed caps, and :func:`_emphasis_spans`
    already has it. What actually generates these spans is layout — "Horses" on
    its own line, "Horse Families", "Breeds I Like", "My Description of a Horse".

    Three conditions, all structural and none of them a word list:

    * short — under :data:`_HEADING_MAX_CHARS`;
    * no terminal punctuation — a heading is not a sentence;
    * preceded by a blank line, or first in the document.

    The blank line is load-bearing rather than belt-and-braces. Body prose here
    is hard-wrapped, so "The INternet as we know it today first" is a short
    unpunctuated line too, and without the blank-line test it would read as a
    heading and take a real name's evidence with it.
    """
    out: list[tuple[int, int]] = []
    offset = 0
    previous_blank = True  # start of document counts
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and (
            len(stripped) < _HEADING_MAX_CHARS
            and stripped[-1] not in ".!?"
            and previous_blank
        ):
            out.append((offset, offset + len(line)))
        previous_blank = not stripped
        offset += len(line) + 1
    return tuple(out)


def _capital_is_the_only_evidence(
    tokens: list[str],
    start: int,
    starts: frozenset[int],
    emphasis: tuple[tuple[int, int], ...],
    headings: tuple[tuple[int, int], ...] = (),
) -> bool:
    """Whether this span rests on a capital that had to be there anyway.

    Three shapes are excluded, because each carries evidence beyond the capital:
    a multi-token span ("Sadie Johnson") is a *shape*; an honorific in front of
    the name is a relationship; and a capital in the middle of a sentence is a
    choice the writer made rather than one orthography made for them.

    A heading is the exception to the first of those. Title case capitalises every
    word, so "Horse Families" is not a shape there — the second capital is as
    orthographic as the first, and a multi-token span inside a heading has no more
    evidence than a single-token one. So the multi-token exemption does not apply
    inside a heading, and "My Brother Terrence Okonkwo" as a heading is still
    caught: it needs the given-name tier rather than its own capitals, which is
    exactly the bar every other unevidenced capital has to clear.
    """
    end = start + len(" ".join(tokens))
    in_heading = any(start < h_end and end > h_start for h_start, h_end in headings)
    if len(tokens) > 1 and not in_heading:
        return False
    if in_heading:
        return True
    if any(start < e and end > s for s, e in emphasis):
        return True
    return start in starts


def find_title_spans(
    text: str,
    is_title: TitleOracle,
    is_prefix: TitleOracle | None = None,
    requires_capital: bool = False,
) -> list[tuple[int, int]]:
    """Character ranges covered by a work title or a fictional character name.

    Runs against the raw text *before* candidate generation, longest match first,
    and the ranges it returns are protected exactly like an upstream anonymization
    marker. That ordering is the whole point: the notability oracle cannot save a
    title, because generation never hands it one. "To Kill a Mockingbird" is split
    by the stoplisted "a" into two candidates, and no lookup on either half
    recovers the book.

    Matches do not overlap — once a span is claimed the scan resumes after it — so
    "The Lion King" cannot also match a shorter title inside itself.

    Args:
        is_prefix: Answers "does some title start with these folded tokens?" and
            is the automaton this scan walks. It doubles as the first-token
            prefilter, since a length-1 prefix *is* a title head. Supplied, the
            walk stops as soon as no title can still be reached — one or two
            tokens on ordinary prose, against the eight-lookup worst case the
            length-descending scan paid at every position whose first word happens
            to head some title ("the", "a", "my"). Absent, every length up to
            :data:`_TITLE_MAX_TOKENS` is tried and the result is identical; only
            the cost differs.
    """
    tokens = [
        (m.start(), m.end(), m.group(0).lower().translate(_CURLY_APOSTROPHE))
        for m in _ANY_TOKEN.finditer(text)
    ]
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(tokens):
        head_start, head_end, _ = tokens[index]
        # Two prefilters, because the scan is the most expensive thing in the
        # module and it runs over every word of every essay.
        #
        # 1. In a document that capitalises its proper nouns, a title's first word
        #    is capitalised too — "The Lion King", not "the lion king". Requiring
        #    that skips almost every position in ordinary prose, where "the", "a"
        #    and "my" are extremely common title heads and each one otherwise costs
        #    a lookup per candidate length. Documents that do NOT capitalise are
        #    scanned at every position, because there the case carries nothing.
        # 2. Most remaining tokens start no title at all, which is the length-1
        #    case of the prefix walk below and costs one frozenset miss.
        if requires_capital and not text[head_start].isupper():
            index += 1
            continue
        longest = 0
        longest_end = head_end
        key = ""
        limit = min(_TITLE_MAX_TOKENS, len(tokens) - index)
        for length in range(1, limit + 1):
            _, token_end, token_key = tokens[index + length - 1]
            key = token_key if length == 1 else f"{key} {token_key}"
            # Multi-token only: see the module docstring on why "It" and "Up"
            # must not make ordinary words permanently notable.
            if length > 1 and is_title(text[head_start:token_end]):
                longest = length
                longest_end = token_end
            if is_prefix is not None and not is_prefix(key):
                break
        if longest:
            spans.append((head_start, longest_end))
        index += longest if longest else 1
    return spans


def _find_lowercase_candidates(
    text: str,
    is_given: GivenNameOracle,
    protected: Callable[[int, int], bool],
    corroborate: frozenset[str] | None = None,
) -> list[Candidate]:
    """Names written in lowercase, seeded on the gazetteer's given-name tier.

    A given-name hit says "a person is being named", which inbound means redact.
    But a hit on its own is not enough to fire on, and this is the whole design
    problem: plenty of common given names are also ordinary English words — hope,
    grace, mark, rose, art, may — so a single lowercase hit in prose is
    indistinguishable from prose. Firing on one token would put the given-name
    tier's 10,469 entries directly into the over-firing number.

    So a span has to reach a second adjacent token that is not stoplisted, which
    is the given-name-plus-surname shape ("terrence okonkwo"). The cost is a bare
    lowercase first name ("terrence and i stayed up late") which this route does
    not reach; the benefit is that "i had hope that day" stops at the stopword and
    emits nothing. Both legs are measured in ``eval_recall`` rather than argued.

    Adjacency is strict: only whitespace may sit between two tokens of one span.
    "terrence, my cousin" therefore stops at the comma and drops to one token.

    The span reaches exactly one token past the seed — a surname — and a third
    only across a name particle ("maria de cruz"). Reaching two ordinary tokens
    masks "terrence okonkwo showed" out of "then terrence okonkwo showed up",
    because the stoplist is a few hundred words and English is not.

    A seed sitting directly after a determiner is dropped: see
    :data:`_DETERMINERS`. That is where most of the remaining over-firing lives,
    and it is structural rather than a word list, so it does not need extending
    every time a new corpus turns up a new ordinary word.

    ``corroborate`` is how :func:`document_capitalises_names` participates without
    being a kill switch. In a document that marks its proper nouns with capitals a
    lowercase token is weak evidence, so the seed must additionally appear
    *capitalised mid-sentence somewhere in the same document* — the writer's own
    testimony that this particular word is a name they sometimes slip on. Passing
    ``None`` means the document supplies no capitalisation signal, and the seed
    stands on the given-name tier alone.

    That replaced suppressing the route outright, which cost every uncapitalised
    occurrence of a name in a document that capitalises the rest. Both legs are in
    ``eval_recall`` and in the student-prose audit rather than argued.
    """
    tokens = [(m.group(0), m.start(), m.end()) for m in _LOWER_TOKEN.finditer(text)]
    particles = frozenset(_PARTICLES)
    out: list[Candidate] = []
    index = 0
    while index < len(tokens):
        word, start, _ = tokens[index]
        if _is_stop(word) or not is_given(word):
            index += 1
            continue
        if corroborate is not None and word.strip("'’") not in corroborate:
            index += 1
            continue
        if index and tokens[index - 1][0] in _DETERMINERS:
            preceding = text[tokens[index - 1][2] : start]
            # Only a directly-adjacent determiner counts. "the day terrence
            # arrived" must stay reachable, and punctuation between the two means
            # they are not one noun phrase.
            if preceding and not preceding.strip():
                index += 1
                continue
        reach = index
        while reach + 1 < len(tokens):
            if reach > index and tokens[reach][0] not in particles:
                break
            next_word, next_start, _ = tokens[reach + 1]
            gap = text[tokens[reach][2] : next_start]
            if not gap or gap.strip() or len(next_word) < 2 or _is_stop(next_word):
                break
            reach += 1
        # A span may not end on a particle: "maria de," is the name plus a
        # fragment of the next clause, and masking the fragment is a visible
        # defect on the outbound path.
        while reach > index and tokens[reach][0] in particles:
            reach -= 1
        span_end = tokens[reach][2]
        if reach - index + 1 < _LOWERCASE_MIN_TOKENS or protected(start, span_end):
            index += 1
            continue
        joined = text[start:span_end]
        out.append(
            Candidate(
                text=joined,
                start=start,
                end=span_end,
                kind=_classify(joined.split()),
            )
        )
        index = reach + 1
    return out


def find_candidates(
    text: str,
    *,
    given_name: GivenNameOracle | None = None,
    title: TitleOracle | None = None,
    title_prefix: TitleOracle | None = None,
    headings_are_orthographic: bool = True,
    title_relation_refusal: bool = True,
) -> list[Candidate]:
    """Every name-shaped span, before any notability decision.

    High recall and deliberately poor precision — precision is what the
    notability filter buys. Offsets are into ``text``.

    Args:
        given_name: Turns on the lowercase route. Absent, this function keys on
            capitalisation alone and misses lowercase writing by construction.
        title: Protects work titles and fictional-character names from generation
            entirely. Absent, a student writing about a book has the book redacted.
        title_relation_refusal: Withdraw that protection from a title span with a
            first-person relation attached to it — "My neighbor Alice Adams". The
            protection is applied *here*, before generation, so the refusal has
            to be applied here too; the notability gate in
            :func:`mask_candidates` is the second half of the same rule and
            neither half works alone.
        headings_are_orthographic: Treat a section heading's capitals as required
            by title case rather than chosen by the writer. On by default; the flag
            exists so the arm stays measurable against its control.
    """
    blocked = [m.span() for m in _PROTECTED.finditer(text)]
    capitalises = document_capitalises_names(text)
    if title is not None:
        title_spans = find_title_spans(
            text, title, title_prefix, requires_capital=capitalises
        )
        if title_relation_refusal:
            title_spans = [
                (s, e) for s, e in title_spans
                if not names_someone_the_writer_knows(text, s, e)
                # ...and the title is not itself a relation phrase the writer is
                # using literally. This one needs the document's capitalisation
                # signal, so it is gated on the same test the scan itself is.
                and not (capitalises
                         and title_is_the_writers_own_relation(text, s, e))
            ]
        blocked += title_spans

    def _protected(start: int, end: int) -> bool:
        return any(start < b_end and end > b_start for b_start, b_end in blocked)

    starts = _sentence_starts(text)
    emphasis = _emphasis_spans(text)
    headings = _heading_spans(text) if headings_are_orthographic else ()
    written_as_a_capital = _mid_sentence_capitals(text, starts, headings)

    def _corroborated(tokens: list[str], is_given: GivenNameOracle) -> bool:
        """A second signal, for a span whose capital proves nothing on its own.

        Two channels: the document's own mid-sentence capitalisation of the word,
        and the given-name tier. ``is_given`` is passed in rather than closed over
        so this is only reachable on the path where an oracle exists.

        ANY token counts, not just the first, and the heading rule is what made
        that distinction load-bearing. Before it, this was only ever reached for
        single-token spans, so "first token" and "any token" were the same thing.
        A heading is title-cased, so a multi-token span inside one also arrives
        here — and "My Brother Terrence Okonkwo" leads with an honorific, so
        checking only the first token consulted "Brother" and leaked the name.
        """
        # Both channels see the same stripped token. They did not: the capital
        # channel stripped `.,'’` and the given-name channel got the raw token,
        # so a name against a closing quote — "words like 'Terrence'", which the
        # candidate regex hands over as `Terrence'` because an apostrophe is a
        # name character — asked the tier about `Terrence'` and was told no.
        # Only reachable once the capital is the sole evidence, which is why an
        # opening quote counting as a sentence start is what surfaced it.
        return any(
            stripped in written_as_a_capital or is_given(stripped)
            for stripped in (t.lower().strip(".,'’") for t in tokens)
        )

    out: list[Candidate] = []
    for match in _CANDIDATE_RE.finditer(text):
        span = match.group(0)
        if _protected(match.start(), match.end()):
            continue
        tokens = span.split()
        # A long all-caps run means the capitalisation told us nothing, so the
        # stoplist is carrying the whole decision. Recorded here rather than
        # silently: this is where the allcaps frame's misses come from.
        for run in _trim(tokens):
            if not run:
                continue
            joined = " ".join(run)
            # Locate the run inside the original span so offsets stay exact.
            offset = span.find(joined)
            if offset < 0:
                continue
            start = match.start() + offset
            if _protected(start, start + len(joined)):
                continue
            # Requiring a second signal is only sound when there is a second
            # signal to require. Without a name list the document's own
            # capitalisation is the sole channel, and a name mentioned once at a
            # sentence start is then genuinely indistinguishable from
            # "Eventually" — so the no-oracle arm keeps its recall-maximal,
            # precision-minimal character rather than becoming quietly stricter.
            if (
                given_name is not None
                and _capital_is_the_only_evidence(
                    run, start, starts, emphasis, headings
                )
                and not _corroborated(run, given_name)
            ):
                continue
            # A *trailing* apostrophe is the closing quote, not part of the name.
            # The candidate pattern treats `'` as a name character so O'Brien
            # survives, which also means "words like 'Terrence'" arrives as
            # `Terrence'` — and masking that ate the quote: "Words like
            # '{NAME_1} stand out". Possessives are untouched because they end in
            # `s`. The one case this trims wrongly is a plural possessive ("the
            # Smiths'"), which loses the apostrophe from the masked span and
            # reads `the {NAME_1}'` — cosmetically odd, against a defect that
            # unbalances a quotation in text a student reads.
            masked_text = joined.rstrip("'’")
            if not masked_text:
                continue
            out.append(
                Candidate(
                    text=masked_text,
                    start=start,
                    end=start + len(masked_text),
                    kind=_classify(run),
                )
            )

    if given_name is not None:
        # The capitalised route claimed first, so a lowercase span overlapping
        # one it already found is dropped rather than merged: two candidates over
        # the same characters would mask the outer one and leave the inner
        # placeholder's braces as debris.
        claimed = [(c.start, c.end) for c in out]
        # `None` here is the permissive path: "no capitalisation signal, so the
        # given-name tier stands alone". It is reached only on positive evidence
        # that the writer drops capitals, never on the mere absence of them —
        # absence is what a text with no names in it looks like, and reading its
        # silence as consent is what put "line circles" in front of a student.
        # See :func:`writes_without_standard_capitals`.
        for candidate in _find_lowercase_candidates(
            text, given_name, _protected,
            corroborate=(
                None
                if not capitalises and writes_without_standard_capitals(text)
                else written_as_a_capital
            ),
        ):
            if any(candidate.start < end and candidate.end > start
                   for start, end in claimed):
                continue
            out.append(candidate)
    return out


class PlaceholderMinter:
    """Hands out ``{KIND_n}`` placeholders, stable per distinct original.

    Why numbering, stated as a measurement rather than a preference: a bare
    ``{NAME}`` standing for every person in a document is **not reversible**. On
    25 injected essays the shipped masker produced 37 ``not-restorable``
    violations and only 36% of essays round-tripped — one token meant "Marisol"
    in one paragraph and "Terrence Okonkwo" in the next, so no map keyed on the
    token can put either back. Numbering is the fix ``check_frame``'s docstring
    has named since the harness was written.

    Two properties, and the second is the one that needs care:

    * **Injective** — distinct originals never share a placeholder, which is what
      makes restore well-defined.
    * **Stable within a document** — the *same* original always gets the same
      index, so a name written five times masks to one placeholder rather than
      five. That matters beyond restorability: a scoring model reading
      ``{NAME_1} argued … {NAME_1} concluded`` can still see one person doing two
      things, where ``{NAME_1} … {NAME_5}`` reads as two strangers and quietly
      costs coherence on exactly the essays that mention somebody a lot.

    Keyed on the exact original text, because restore must return the exact
    bytes. "Terrence" and "Terrence's" are therefore different keys — correct but
    unsatisfying, and the reason ``surname_forms``-style folding does NOT belong
    here: folding them together would make the mapping non-injective again.
    """

    def __init__(self, *, number: bool = True) -> None:
        #: Off reproduces the previous unnumbered output byte for byte, so the
        #: two arms stay separately measurable.
        self.number = number
        self._assigned: dict[tuple[str, str], int] = {}
        self._high: dict[str, int] = {}

    def mint(self, kind: str, original: str) -> str:
        """The placeholder ``original`` should be replaced by."""
        if not self.number:
            return f"{{{kind}}}"
        key = (kind, original)
        index = self._assigned.get(key)
        if index is None:
            index = self._high.get(kind, 0) + 1
            self._high[kind] = index
            self._assigned[key] = index
        return f"{{{kind}_{index}}}"

    def substitute(self, kind: str, pattern: re.Pattern[str], text: str) -> tuple[str, int]:
        """``pattern.subn`` with a minted placeholder per distinct match."""
        return pattern.subn(lambda m: self.mint(kind, m.group(0)), text)

    @property
    def assigned(self) -> dict[str, str]:
        """``{placeholder: original}`` — the restore map, for free."""
        return {
            self.mint(kind, original): original
            for (kind, original) in self._assigned
        }


def is_public_landmark(name: str) -> bool:
    """A landmark is topical by construction, so it needs no gazetteer lookup."""
    tokens = name.split()
    return len(tokens) > 1 and tokens[-1].lower().strip(".,") in _LANDMARK_SUFFIXES


def _surname_tokens(name: str) -> list[str]:
    """Lower-cased tokens of ``name`` with the possessive tail removed.

    "Wright’s" and "Wright" must fold together or corroboration reaches the
    citation form of the name and not the one literary analysis actually writes —
    on the un-scrubbed corpus the possessive was 10 of the 27 masked "Wright"
    spans, so this is most of the effect rather than an edge case.
    """
    folded = name.translate(_CURLY_APOSTROPHE).lower()
    tokens = []
    for token in re.split(r"\s+", folded.strip()):
        token = token.strip(".,;:!?'\"")
        if token.endswith("'s") and len(token) > 3:
            token = token[:-2]
        if token:
            tokens.append(token)
    return tokens


def _bare_surname_key(name: str) -> str | None:
    """``name`` as a corroboration key, or ``None`` if it is not a bare form.

    A bare surname is one token, or a particle-led run ("van Gogh", "de Beauvoir")
    where every token but the last is a particle. Anything else — "Coach Wright",
    "Priya Wright" — is a *different* candidate that happens to share a surname,
    and must not be reached by another name's corroboration.
    """
    tokens = _surname_tokens(name)
    if not tokens:
        return None
    if len(tokens) == 1:
        return tokens[0]
    if len(tokens) <= 3 and all(t in _PARTICLES for t in tokens[:-1]):
        return " ".join(tokens)
    return None


def surname_forms(name: str) -> tuple[str, ...]:
    """The bare surface forms a writer may substitute for ``name`` later on.

    ``"Richard Wright"`` yields ``("wright",)``; ``"Vincent van Gogh"`` yields
    ``("gogh", "van gogh")``. The bare *first* name is never a form, for the same
    reason the builder refuses to emit one: a first name is the commonest private
    surface form in student prose, and corroborating it would make one notable
    full name keep every "Terrence" in the document.

    Returns ``()`` for a single-token name — a mononym corroborates nothing,
    because it is already the bare form.
    """
    tokens = _surname_tokens(name)
    if len(tokens) < 2:
        return ()
    forms = [tokens[-1]]
    if tokens[-2] in _PARTICLES:
        forms.append(" ".join(tokens[-2:]))
        if len(tokens) >= 3 and tokens[-3] in _PARTICLES:
            forms.append(" ".join(tokens[-3:]))
    return tuple(forms)


#: Words that make a nearby bare surname somebody in the WRITER'S life rather than
#: the public figure the document established. Two kinds, and both are needed:
#: relation nouns ("my cousin", "our coach") and *proximity* phrases, because the
#: shape that actually occurs is "lives two doors down from us" — a relation
#: expressed as distance, with no relation noun in it anywhere.
#:
#: Deliberately NOT "the appositive contains a first-person pronoun", which was the
#: first design and is wrong: literary prose writes "Wright, who taught me to look
#: away from nothing", and refusing corroboration there re-destroys the author the
#: essay is about — the exact defect corroboration was built to fix. A first-person
#: pronoun says the sentence is personal; only these cues say the *person* is.
_RELATION_CUES: frozenset[str] = frozenset(
    """
    neighbor neighbour neighbors neighbours
    cousin cousins brother brothers sister sisters
    uncle aunt grandma grandpa grandmother grandfather
    mom mother dad father stepdad stepmom
    coach teacher tutor principal babysitter
    friend friends bestfriend classmate classmates roommate
    teammate teammates boss coworker
    """.split()
)

#: Multi-word proximity phrases, matched on the folded context string.
_PROXIMITY_CUES: tuple[str, ...] = (
    "doors down",
    "door down",
    "down the street",
    "next door",
    "across the street",
    "up the block",
    "down the block",
    "in my class",
    "in my grade",
    "on my team",
    "at my school",
    "in my neighborhood",
    "in my neighbourhood",
)

#: How far around a bare surname to look for the cues. One clause either side:
#: long enough for "Robinson, who lives two doors down from us," and short enough
#: that the next sentence's unrelated cousin does not reach back.
_RELATION_WINDOW: int = 90


def names_someone_in_the_writers_life(text: str, start: int, end: int) -> bool:
    """Whether the local context marks this surname as personal, not public.

    Checked only for a bare surname the document has otherwise *established* as a
    public figure's, and it is the one signal that can separate the two readings
    of "Robinson" in a document containing "Jackie Robinson": the neighbour
    carries an appositive about the writer's own life, and the ballplayer does not.

    Looks after the span for an appositive or relative clause, and before it for a
    possessive introduction ("my neighbour Robinson"). Both sides matter — English
    puts the relation either place — and neither reaches past one clause.
    """
    after = text[end : end + _RELATION_WINDOW].lower()
    before = text[max(0, start - _RELATION_WINDOW) : start].lower()

    # After: only an appositive or relative clause counts. A new sentence does
    # not, so the scan stops at terminal punctuation.
    clause = re.split(r"[.!?\n]", after, maxsplit=1)[0]
    for window in (clause, before):
        if any(cue in window for cue in _PROXIMITY_CUES):
            return True
        if any(token in _RELATION_CUES for token in _ANY_TOKEN.findall(window)):
            return True
    return False


#: The relation nouns as a regex alternation, for the *attached-phrase* patterns
#: below. Sorted so the pattern is stable across runs and diffs.
_RELATION_ALTERNATION: str = "|".join(sorted(_RELATION_CUES))

#: Up to two words may sit between the possessive and the relation noun — "my
#: next-door neighbor", "my best friend", "my old soccer coach". Lower-case only,
#: so a capitalised name cannot be swallowed as a modifier.
_MODIFIERS: str = r"(?:[a-z][a-z'’-]*\s+){0,2}"

#: "my cousin " immediately before the span. Anchored at the end: the relation
#: phrase has to run right up to the name, which is what makes it name *that*
#: person rather than merely appear in the same sentence.
_RELATION_ATTACHED_BEFORE = re.compile(
    rf"\b(?:my|our)\s+{_MODIFIERS}(?:{_RELATION_ALTERNATION})\s+$"
)

#: ", my next-door neighbor" immediately after it. The comma is required — an
#: appositive is punctuated and a prepositional phrase is not, and that is the
#: whole difference between "Alice Adams, my neighbor," and "Harry Potter … with
#: my little brother".
_RELATION_ATTACHED_AFTER = re.compile(
    rf"^\s*,\s*(?:who\s+(?:is|was)\s+)?(?:my|our)\s+{_MODIFIERS}"
    rf"(?:{_RELATION_ALTERNATION})\b"
)

#: First-person tokens, for the proximity leg. A proximity phrase says somebody
#: lives nearby; only a first-person pronoun says nearby *to the writer*.
_FIRST_PERSON: frozenset[str] = frozenset({"i", "me", "my", "we", "us", "our"})

#: A title whose own first words are a first-person relation — "My Cousin Vinny",
#: "My Sister Eileen", "My Best Friend Anne Frank". 41 keys in the shipped tier,
#: and they are the most dangerous shape in it: the phrase they occupy is
#: ``kinship-possessive``, the single commonest frame a student names somebody in.
#: The tier match is case-insensitive, so "My cousin Vinny Delgado came over"
#: matched the 1992 film, blocked generation over the whole phrase, and shipped
#: the name. See :func:`title_is_the_writers_own_relation`.
_TITLE_LEADS_WITH_RELATION = re.compile(
    rf"^(?:my|our)\s+{_MODIFIERS}(?:{_RELATION_ALTERNATION})\b"
)


def title_is_the_writers_own_relation(text: str, start: int, end: int) -> bool:
    """Whether a relation-led title span is really the writer naming somebody.

    "My Cousin Vinny is my favorite movie" and "My cousin Vinny Delgado came over
    that summer" fold to the same lookup key, and the tier keeps both. The
    difference is one the writer supplied: a title is title-cased, so its relation
    word carries a capital, and a sentence about a relative does not.

    That is the same evidence the heading rule reads and the same evidence rule 1
    of the capitalisation rules reads — the document's own orthography, not a
    guess about intent. Callers gate this on
    :func:`document_capitalises_names`, because in a document that capitalises
    nothing the absent capital is not testimony about anything.

    The cost of being wrong is a student who writes "my cousin vinny is my
    favorite movie" losing the film to a placeholder inbound. The cost of the
    other error is a cousin's name reaching a third-party model.
    """
    span = text[start:end]
    if not _TITLE_LEADS_WITH_RELATION.match(span.lower()):
        return False
    # Everything after the leading possessive: "Cousin Vinny" in the title,
    # "cousin Vinny" in the sentence. The relation word is the one that differs.
    tokens = _ANY_TOKEN.findall(span)
    return any(token.islower() for token in tokens[1:3])


def names_someone_the_writer_knows(text: str, start: int, end: int) -> bool:
    """Whether a first-person relation is syntactically attached to this name.

    The strict sibling of :func:`names_someone_in_the_writers_life`, and strict
    for a measured reason. That function scans a window for any relation cue,
    which is right for a bare surname the document itself established — but
    applied to the title tier it refuses six of the seven curriculum characters
    it must keep, because characters are *described by* their relations: Atticus
    Finch is a father, Peter Parker lives with his aunt, Tom Sawyer talks his
    friends into whitewashing a fence. A relation noun in the window is therefore
    no evidence at all about a work title.

    Two things separate "My neighbor Alice Adams" from those. The relation is
    **first-person** — the writer's own — and it is **attached** to the name,
    either immediately before it or inside the appositive immediately after it.
    Both are required. First person alone keeps "I read Harry Potter with my
    little brother"; attachment alone keeps "Atticus Finch, a father who…".

    The error costs are asymmetric and that is what makes the rule affordable at
    all: a title hit overridden wrongly over-redacts a book the student wrote
    about, which the inbound placeholder absorbs; a title hit honoured wrongly
    ships a classmate's name to a third-party model.
    """
    before = text[max(0, start - _RELATION_WINDOW) : start].lower()
    after = text[end : end + _RELATION_WINDOW].lower()
    if _RELATION_ATTACHED_BEFORE.search(before):
        return True
    if _RELATION_ATTACHED_AFTER.match(after):
        return True
    # The relation expressed as distance — "Alice Adams, who lives two doors down
    # from us". Same attachment requirement (the clause is the appositive that
    # follows the name), plus a first-person pronoun, because "two doors down"
    # on its own says nothing about whose street it is.
    if after.lstrip().startswith(","):
        clause = re.split(r"[.!?\n]", after, maxsplit=1)[0]
        if any(cue in clause for cue in _PROXIMITY_CUES) and any(
            token in _FIRST_PERSON for token in _ANY_TOKEN.findall(clause)
        ):
            return True
    return False


def corroborated_surnames(
    candidates: list[Candidate],
    notable: NotabilityOracle,
    keep: frozenset[str] = frozenset(),
    tier: NotabilityTierOracle | None = None,
) -> frozenset[str]:
    """Surnames this document has already established belong to a public figure.

    The observation is narrow and it is free: if a document writes "Richard
    Wright" somewhere, and the gazetteer keeps "Richard Wright", then a bare
    "Wright" elsewhere in *that document* is that person. Literary-analysis
    convention makes this the dominant shape of the problem — a student names the
    author once and writes the surname for the rest of the essay. On the 27
    un-scrubbed student essays the shipped arm masked "Wright" or "Wright's" 27
    times in a single document that also contained "Richard Wright's".

    Why this rather than moving the short tier's thresholds: a famous person with
    a common surname cannot clear ``SHORT_MIN_SITELINKS`` and
    ``SHORT_MAX_US_SURNAME_POPULATION`` at once, by construction, and relaxing
    either one keeps "Robinson" for every document in the corpus. This keeps it
    only where the document itself supplied the first name, which is per-document
    evidence rather than a global bet, and it moves no threshold and changes no
    asset.

    What it deliberately cannot do: corroborate from a name the gazetteer does
    *not* keep. A student's own "Terrence Okonkwo" establishes nothing, so bare
    "Okonkwo" still redacts. The residual risk is amplification of an existing
    full-tier false positive — if some obscure footballer makes "Jose Rodriguez"
    keep, bare "Rodriguez" in that document now keeps too — which is scored as a
    fixture frame (``private-surname-shadowed-by-a-notable-one``) rather than
    left as a caveat.

    Args:
        tier: Restricts corroboration to human full names — see
            :data:`CORROBORATING_TIER`. Strongly recommended: without it a kept
            *place* can license a surname, which is a measured defect and not a
            hypothetical one. Absent, landmark-shaped names are excluded as a
            partial substitute and the rest of the place tier is not.
    """
    out: set[str] = set()
    lowered_keep = {k.lower() for k in keep}
    for candidate in candidates:
        name = candidate.text
        if len(name.split()) < 2:
            continue
        if name.lower() in lowered_keep:
            # A name the assignment prompt supplied. Topical by construction, and
            # the prompt naming "Richard Wright" is the same evidence as the essay
            # naming him — arguably better, since it is not the student's writing.
            pass
        elif tier is not None:
            if tier(name) != CORROBORATING_TIER:
                continue
        elif not notable(name) or is_public_landmark(name):
            continue
        out.update(surname_forms(name))
    return frozenset(out)


def mask_candidates(
    text: str,
    *,
    notable: NotabilityOracle | None = None,
    keep: frozenset[str] = frozenset(),
    given_name: GivenNameOracle | None = None,
    title: TitleOracle | None = None,
    title_prefix: TitleOracle | None = None,
    corroborate: bool = True,
    notability_tier: NotabilityTierOracle | None = None,
    minter: PlaceholderMinter | None = None,
    relation_refusal: bool = True,
    title_relation_refusal: bool = True,
    headings_are_orthographic: bool = True,
) -> tuple[str, int]:
    """Mask every candidate the notability filter does not keep.

    Args:
        notable: Returns True for a public figure. Defaults to keeping nothing,
            which is the recall-maximal, precision-minimal posture — supply the
            gazetteer for production.
        keep: Exact strings to keep regardless, case-insensitively. This is the
            ``prompt_context`` leg: a name in the assignment prompt or source
            passage is topical by construction, exact, and free.
        given_name: Turns on the lowercase route. Lowercase spans go through the
            same keep / landmark / notability gates as capitalised ones, so a
            student who writes "van gogh" is treated like one who writes "Van
            Gogh".
        title: Keeps work titles and fictional-character names whole. Applied
            before generation rather than after, so it also protects titles that
            generation would otherwise split on an interior stopword.
        corroborate: Keep a bare surname when the same document also writes a
            full name the oracle keeps. See :func:`corroborated_surnames`. No
            effect without ``notable``, since there is nothing to corroborate
            from.
        minter: Numbers the placeholders so masking is reversible — see
            :class:`PlaceholderMinter`. Shared with the caller's identity and
            structured passes so indices do not collide across them. Absent, the
            unnumbered ``{NAME}`` is emitted and the output is not restorable.
        relation_refusal: Refuse corroboration for a bare surname whose local
            context marks it as someone in the writer's life. See
            :func:`names_someone_in_the_writers_life`. No effect without
            ``corroborate``, since there is nothing to refuse.
        title_relation_refusal: Refuse a *title-tier* keep when a first-person
            relation is attached to the name. See
            :func:`names_someone_the_writer_knows`. Needs ``notability_tier``:
            the boolean oracle cannot say which tier vouched for a name, and
            overriding every tier would redact "my hero Abraham Lincoln".

    Returns:
        ``(masked_text, spans_masked)``.
    """
    lowered_keep = {k.lower() for k in keep}
    candidates = find_candidates(
        text, given_name=given_name, title=title, title_prefix=title_prefix,
        headings_are_orthographic=headings_are_orthographic,
        title_relation_refusal=title_relation_refusal,
    )
    established: frozenset[str] = frozenset()
    if corroborate and notable is not None:
        established = corroborated_surnames(
            candidates, notable, keep, tier=notability_tier
        )
    n = 0
    # Right to left so earlier offsets stay valid as the text shrinks.
    for candidate in sorted(candidates, key=lambda c: c.start, reverse=True):
        name = candidate.text
        if name.lower() in lowered_keep:
            continue
        if is_public_landmark(name):
            continue
        if notable is not None and notable(name):
            # ...unless a work title is standing in for a person the writer
            # knows. "Alice Adams" is a 1921 novel and also 589 real people's
            # names in this tier alone; no threshold separates them from the
            # curriculum (Atticus Finch sits BELOW the hole in sitelinks), so the
            # separation has to come from the sentence.
            if not (
                title_relation_refusal
                and notability_tier is not None
                and notability_tier(name) in OVERRIDABLE_TIERS
                and names_someone_the_writer_knows(
                    text, candidate.start, candidate.end
                )
            ):
                continue
        # Only the bare form corroborates. "Coach Wright" and "Priya Wright" stay
        # masked even where "Wright" is established — the whole-candidate rule
        # that keeps "Priya Lincoln" out of the short tier applies here too.
        if established and _bare_surname_key(name) in established:
            # ...unless the local context says this one is someone in the
            # writer's life who happens to share the surname. Corroboration is a
            # document-level inference and this is the sentence-level exception to
            # it; without it a neighbour named Robinson is protected by Jackie
            # Robinson's fame.
            if not (relation_refusal and names_someone_in_the_writers_life(
                text, candidate.start, candidate.end
            )):
                continue
        placeholder = (
            candidate.placeholder if minter is None
            else minter.mint(candidate.kind, name)
        )
        text = text[: candidate.start] + placeholder + text[candidate.end :]
        n += 1
    return text, n
