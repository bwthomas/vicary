"""Local, zero-cost PII classifier — the default redactor in production.

Why this exists
---------------
The Bedrock Guardrail is a good detector with two problems for this pipeline:
it costs money on every essay, and it is a network hop on the latency-critical
path (measured +690ms p50). Measured against 75 known PII spans in 25 injected
set-8 essays, the Guardrail's OUTPUT pass caught 73/75 (97.3%).

This classifier splits that work by what each method is actually good at:

* **Structured entities** (EMAIL, PHONE, SSN, CARD, IP, ZIP, street ADDRESS)
  are *syntax*, and regex already scored **100%** on them in the same harness.
  No model can beat 100%, and a regex is free and sub-millisecond.
* **The student's own name and school** are the NAME/ADDRESS spans that
  actually matter, and we are not guessing at them — the caller knows who
  submitted the essay and which school they attend. Interpolating those into
  patterns turns the hardest category for a detector into an exact match.

What it does NOT cover
----------------------
**Third-party names.** A classmate, a teacher, or a public figure the student
mentions is not in the identity we were handed, and no regex finds it. In the
ASAP corpus that is a real population — 2,108 ``@PERSON`` tokens, ~4 name
mentions per essay. Closing it needs a NER pass (the co-located encoder is the
intended home, since it is already on the box and already paid for). Until that
lands, :data:`MODE_GUARDRAIL` remains available and the Bedrock Guardrail is
still the more complete detector for names it has never been told about.

So: this is the default because it is free, fast, and exact on the spans we can
name. It is not a claim to have beaten the Guardrail on recall. Re-run
``python -m vicary.eval.recall`` against both before asserting otherwise
(MUST #6 — a detector's opinion of itself is not a measurement).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from vicary.name_candidates import (
    GivenNameOracle,
    NotabilityOracle,
    NotabilityTierOracle,
    PlaceholderMinter,
    TitleOracle,
    mask_candidates,
)

# ---------------------------------------------------------------------------
# Structured entities — syntax, so regex is the right tool and scores 100%.
# ---------------------------------------------------------------------------

# Order matters: the first pattern to claim a span wins, so the most specific
# and least ambiguous run first. EMAIL before PHONE (an email can contain
# digits that look like a phone); SSN and CARD before the generic digit runs.

#: Practical email shape. Deliberately not RFC 5322 — the full grammar matches
#: strings no student writes and is a known source of catastrophic backtracking.
_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"
)

#: US SSN. Excludes the never-issued ranges (000/666/9xx area, 00 group,
#: 0000 serial) so dates and score ranges don't trip it.
_SSN = re.compile(
    r"\b(?!000|666|9\d{2})\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}\b"
)

#: Candidate payment-card runs, 13–19 digits with optional space/hyphen
#: grouping. Luhn-checked below, because an un-checked pattern this loose
#: eats any long number a student writes.
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

#: NANP phone, plus common international prefix. Requires separators or
#: parens somewhere so a bare 10-digit number isn't assumed to be a phone.
_PHONE = re.compile(
    r"""(?x)
    (?<![\w-])
    (?:\+?\d{1,3}[-.\s]?)?          # optional country code
    (?:
        \(\d{3}\)[-.\s]*\d{3}[-.\s]?\d{4}   # (555) 555-5555
      | \d{3}[-.\s]\d{3}[-.\s]\d{4}         # 555-555-5555 / 555.555.5555
    )
    (?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?
    (?![\w-])
    """
)

_IP = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b"
)

#: US street address: number + street words + a suffix. The suffix list is what
#: keeps this from matching "I ran 3 miles down the road" — a bare
#: number-plus-words pattern has an unacceptable false-positive rate in prose.
_STREET_SUFFIX = (
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct"
    r"|Circle|Cir|Place|Pl|Terrace|Ter|Way|Parkway|Pkwy|Highway|Hwy|Trail|Trl"
    r"|Square|Sq|Loop|Alley|Commons)"
)
_ADDRESS = re.compile(
    rf"""(?x)
    \b\d{{1,6}}\s+
    (?:[NSEW]\.?|North|South|East|West|Northeast|Northwest|Southeast|Southwest)?\s*
    (?:[A-Z][A-Za-z.'-]*\s+){{0,4}}
    {_STREET_SUFFIX}\b\.?
    (?:\s*(?:Apt|Apartment|Suite|Ste|Unit|\#)\s*[\w-]+)?
    """
)

#: US ZIP, with the optional +4. Bounded so it can't eat a 5-digit year range.
_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b(?=\s*$|\s*[,.]|\s+[A-Z]{2}\b)")

#: Explicit age statements. Bare numbers are not ages; the phrasing is.
_AGE = re.compile(
    r"\b(?:(?:I\s+am|I'm|aged?|age(?:d)?\s+of)\s+)(\d{1,2})\b(?=\s*(?:years?\s+old)?)"
    r"|\b(\d{1,2})\s+years?\s+old\b",
    re.IGNORECASE,
)

#: URLs. Student essays cite them, and a personal profile URL is PII.
_URL = re.compile(
    r"\bhttps?://[^\s<>\"']+|\bwww\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+[^\s<>\"']*"
)

#: Anonymization markers somebody upstream already substituted for real PII.
#: Text arriving with these in it has *already been redacted*, so masking them
#: again is not a privacy win — it destroys information while adding none, which
#: is the definition of a no-op we should skip.
#:
#: The kinds are the closed set the ASAP corpus authors used, measured over the
#: full training set rather than taken from their documentation: 14 distinct
#: kinds across 64,166 occurrences (@CAPS 36,765 · @NUM 7,288 · @PERSON 6,106 ·
#: @LOCATION 3,734 · @ORGANIZATION 3,601 · @MONTH 2,918 · @DATE 1,895 ·
#: @PERCENT 958 · @TIME 535 · @MONEY 328 · @DR 17 · @STATE 12 · @CITY 8 ·
#: @EMAIL 1). Always uppercase, optionally numbered.
#:
#: Why this is in the shipped classifier and not just the eval harness: real
#: student prose contains none of these, so production behaviour is unchanged.
#: What changes is every measurement taken over ASAP text with redaction ON. A
#: model trained on that corpus saw these tokens at ~22 per essay; rewriting them
#: to {USERNAME} hands it a token it has never seen and moves its input away from
#: the training distribution, so an experiment run in that state measures the
#: rewrite rather than whatever it meant to vary.
_UPSTREAM_ANON_KINDS: tuple[str, ...] = (
    "CAPS", "NUM", "PERSON", "LOCATION", "ORGANIZATION", "MONTH", "DATE",
    "PERCENT", "TIME", "MONEY", "EMAIL", "STATE", "CITY", "DR",
)
_ANON_ALT: str = "|".join(_UPSTREAM_ANON_KINDS)

#: @handles. Requires the @ so it can't eat ordinary words, and a length floor
#: so it can't eat an email's local part (email runs first anyway). The
#: lookahead spares upstream anonymization markers; a genuine all-caps handle
#: colliding with one of those 14 words is the accepted cost, and it is the
#: right way round — a missed handle is one span, and eating @PERSON1 corrupts
#: every essay in the evaluation corpus.
_USERNAME = re.compile(
    rf"(?<![\w@.])@(?!(?:{_ANON_ALT})\d*\b)[A-Za-z0-9_]{{3,30}}\b"
)

#: Date of birth, explicitly labelled.
_DOB = re.compile(
    r"\b(?:date\s+of\s+birth|d\.?o\.?b\.?|born\s+on)\s*:?\s*"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    re.IGNORECASE,
)


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum. Cuts the card pattern's false positives on long numbers."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


#: (placeholder, pattern) in application order. CARD is handled separately
#: because it needs the Luhn gate.
_STRUCTURED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("{EMAIL}", _EMAIL),
    ("{URL}", _URL),
    ("{US_SOCIAL_SECURITY_NUMBER}", _SSN),
    ("{IP_ADDRESS}", _IP),
    ("{PHONE}", _PHONE),
    ("{ADDRESS}", _ADDRESS),
    ("{DATE_OF_BIRTH}", _DOB),
    ("{USERNAME}", _USERNAME),
)


# ---------------------------------------------------------------------------
# Identity interpolation — the leg regex alone cannot do.
# ---------------------------------------------------------------------------

#: Given names that are also ordinary English words. A bare first-name match on
#: one of these destroys prose ("Will you go", "the Art of war", "a Grace
#: period"), so a standalone occurrence of one is left alone; the full name and
#: the surname still mask. Skewed toward over-inclusion on purpose: a missed
#: first name is one span, a wrongly-masked common word corrupts every essay
#: that uses it.
_AMBIGUOUS_GIVEN_NAMES = frozenset(
    {
        "art", "bill", "brook", "chase", "dawn", "drew", "faith", "frank",
        "grace", "grant", "hope", "jack", "joy", "june", "mark", "may",
        "mercy", "miles", "nick", "pat", "patience", "penny", "rich",
        "robin", "rose", "sky", "summer", "sunny", "trinity", "will", "wills",
    }
)

#: Surnames common enough as words to need the same treatment.
_AMBIGUOUS_SURNAMES = frozenset({"young", "white", "black", "green", "brown",
                                 "king", "moore", "price", "rich", "stone"})


@dataclass(frozen=True)
class StudentIdentity:
    """Who wrote the essay, so their own PII can be masked exactly.

    ``first_name`` / ``last_name`` come from the student's account;
    ``school_name`` from the LEA associated with their teacher. Every field is
    optional — an absent field simply contributes no patterns, so a caller that
    knows only the surname still gets the surname masked.
    """

    first_name: str | None = None
    last_name: str | None = None
    school_name: str | None = None
    #: Extra strings to mask verbatim (preferred names, a district name, a
    #: second surname). Masked as {NAME} unless the caller says otherwise.
    extra_names: tuple[str, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return not any(
            (self.first_name, self.last_name, self.school_name, self.extra_names)
        )


def _word_pattern(literal: str) -> re.Pattern[str]:
    """Case-insensitive whole-token match for a literal, possessive-tolerant.

    ``\\b`` alone mis-handles a trailing apostrophe-s, which is exactly how a
    name appears in student prose ("Sarah's essay"), so the possessive is part
    of the match and gets masked with the name.
    """
    return re.compile(
        rf"\b{re.escape(literal)}(?:'s|'s|s')?\b", re.IGNORECASE
    )


def _school_acronym(name: str) -> str | None:
    """``"Lincoln High School"`` → ``"LHS"``. None when it would be too short.

    Students write the acronym far more often than the full name, and a
    two-letter acronym collides with ordinary words and state codes.
    """
    initials = [w[0] for w in re.findall(r"[A-Za-z][\w'-]*", name) if w]
    acronym = "".join(initials).upper()
    return acronym if len(acronym) >= 3 else None


def identity_patterns(
    identity: StudentIdentity,
) -> list[tuple[str, re.Pattern[str]]]:
    """Patterns masking this student's own identifying strings.

    Ordered most-specific-first: the full name is matched before either part of
    it, so "Jane Quincy-Adams" becomes one ``{NAME}`` rather than two adjacent
    placeholders.
    """
    out: list[tuple[str, re.Pattern[str]]] = []
    first = (identity.first_name or "").strip()
    last = (identity.last_name or "").strip()
    school = (identity.school_name or "").strip()

    if first and last:
        out.append(("{NAME}", _word_pattern(f"{first} {last}")))
        # "Adams, Jane" — the roster/header order.
        out.append(("{NAME}", _word_pattern(f"{last}, {first}")))

    if last and last.lower() not in _AMBIGUOUS_SURNAMES:
        out.append(("{NAME}", _word_pattern(last)))
    if first and first.lower() not in _AMBIGUOUS_GIVEN_NAMES:
        out.append(("{NAME}", _word_pattern(first)))

    for extra in identity.extra_names:
        extra = extra.strip()
        if extra:
            out.append(("{NAME}", _word_pattern(extra)))

    if school:
        out.append(("{SCHOOL}", _word_pattern(school)))
        acronym = _school_acronym(school)
        if acronym:
            # Case-SENSITIVE for the acronym: lowercasing it would match
            # ordinary words ("lhs" is not a word, but "was"/"his"-shaped
            # three-letter acronyms are a real hazard).
            out.append(("{SCHOOL}", re.compile(rf"\b{re.escape(acronym)}\b")))

    return out


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------


@dataclass
class LocalRedactionResult:
    text: str
    n_masked: int
    #: ``{placeholder: original}`` for this document — what restore needs. Falls
    #: out of numbering for free, and is empty when numbering is off because an
    #: unnumbered map cannot be inverted: one ``{NAME}`` stands for everybody.
    restore_map: dict[str, str] = field(default_factory=dict)

    @property
    def intervened(self) -> bool:
        return self.n_masked > 0

    def restore(self, text: str) -> str:
        """Put the originals back. Longest placeholder first, so ``{NAME_1}``
        cannot be partially consumed while ``{NAME_11}`` is pending."""
        for placeholder in sorted(self.restore_map, key=len, reverse=True):
            text = text.replace(placeholder, self.restore_map[placeholder])
        return text


class LocalNameClassifier:
    """Regex + interpolated-identity PII masker. Free, offline, deterministic.

    Construct one per request (identity is per-student) and reuse it for the
    inbound and outbound passes.

    Third-party names — a classmate, a relative, a teacher — need
    ``candidates=True``, which turns on generation from the text itself
    (:mod:`vicary.name_candidates`) plus a notability filter. It is
    **off by default** and that is a sequencing decision, not caution for its own
    sake: generation without a notability oracle masks every public figure a
    student writes *about*, so precision on the fixture's KEEP spans goes to zero.
    Turn it on with an oracle, or turn it on knowingly for the inbound path where
    over-masking is distributionally native (see the module docstring in
    ``name_candidates``).
    """

    def __init__(
        self,
        identity: StudentIdentity | None = None,
        *,
        candidates: bool = False,
        notable: NotabilityOracle | None = None,
        topical: frozenset[str] = frozenset(),
        given_name: GivenNameOracle | None = None,
        title: TitleOracle | None = None,
        title_prefix: TitleOracle | None = None,
        corroborate: bool = True,
        notability_tier: NotabilityTierOracle | None = None,
        number_placeholders: bool = True,
        headings_are_orthographic: bool = True,
        relation_refusal: bool = True,
    ) -> None:
        self.identity = identity or StudentIdentity()
        self.candidates = candidates
        self.notable = notable
        #: Keeps a bare surname the document itself established — "Wright" where
        #: "Richard Wright" is also present and kept. On by default because the
        #: alternative is destroying the author an essay is about; overridable so
        #: the two arms stay separately measurable.
        self.corroborate = corroborate
        #: Which tier a kept name resolved to. Corroboration needs it because
        #: only a human full name may license a bare surname.
        self.notability_tier = notability_tier
        #: Emit {NAME_1} rather than {NAME}, so masking is reversible. On by
        #: default: unnumbered output round-tripped 36% of injected essays.
        self.number_placeholders = number_placeholders
        #: Treats a section heading's capitals as required by title case rather
        #: than chosen by the writer. Headings are the bulk of over-firing on real
        #: student prose ("Horses", "Horse Movement", "Breeds I Like"). On by
        #: default; overridable so the arm stays measurable.
        self.headings_are_orthographic = headings_are_orthographic
        #: Refuses corroboration for a bare surname the local context marks as
        #: someone in the writer's life — the neighbour who shares a famous
        #: person's surname. On by default; overridable so the arm stays measurable.
        self.relation_refusal = relation_refusal
        #: Turns on the lowercase route through candidate generation, which is
        #: the only one that reaches a student who writes without capitals.
        self.given_name = given_name
        #: Keeps the books, films and characters a student writes *about*. The
        #: notability tier holds real people only, so without this every work
        #: title and fictional character is redacted.
        self.title = title
        #: Cheap prefilter for the title scan; see gazetteer.title_heads.
        self.title_prefix = title_prefix
        #: Names the assignment prompt or source passage supplies. Topical by
        #: construction, so exact, free and zero-false-positive — the first rung
        #: of the notability filter, ahead of any gazetteer.
        self.topical = topical
        # Identity patterns run FIRST: a name is the span most likely to be
        # partially consumed by a looser pattern (an address line can swallow a
        # surname), and masking it first makes that impossible.
        self._patterns: list[tuple[str, re.Pattern[str]]] = [
            *identity_patterns(self.identity),
            *_STRUCTURED,
        ]

    @staticmethod
    def _kind(placeholder: str) -> str:
        """``"{NAME}"`` -> ``"NAME"``. The pattern tables carry the braced form."""
        return placeholder.strip("{}")

    def mask(self, text: str) -> LocalRedactionResult:
        """Replace every detected span with a typed ``{PLACEHOLDER}``."""
        if not text:
            return LocalRedactionResult(text=text, n_masked=0)

        masked = text
        n = 0
        # ONE minter for the whole document, shared by every pass below and by
        # candidate generation. Per-pass minters would restart each counter and
        # emit {NAME_1} twice for two different people, which is the bug numbering
        # exists to remove.
        minter = PlaceholderMinter(number=self.number_placeholders)

        for placeholder, pattern in self._patterns:
            masked, count = minter.substitute(
                self._kind(placeholder), pattern, masked
            )
            n += count

        # Cards need the Luhn gate, so they can't go through subn directly.
        def _card(match: re.Match[str]) -> str:
            nonlocal n
            digits = re.sub(r"\D", "", match.group(0))
            if _luhn_ok(digits):
                n += 1
                return minter.mint("CREDIT_DEBIT_CARD_NUMBER", match.group(0))
            return match.group(0)

        masked = _CARD_CANDIDATE.sub(_card, masked)

        # ZIP and AGE run last: both are bare digits and would otherwise claim
        # characters belonging to a phone, SSN, card or street address.
        masked, count = minter.substitute("ZIP_CODE", _ZIP, masked)
        n += count

        def _age(match: re.Match[str]) -> str:
            nonlocal n
            n += 1
            # Only the digits are the age; the surrounding "I am … years old" is
            # the student's prose and has to survive, so this mints against the
            # digit run rather than the whole match.
            digits = re.search(r"\d{1,2}", match.group(0))
            if digits is None:
                return match.group(0)
            return (
                match.group(0)[: digits.start()]
                + minter.mint("AGE", digits.group(0))
                + match.group(0)[digits.end():]
            )

        masked = _AGE.sub(_age, masked)

        # Candidate generation runs LAST, so every exact pattern has already
        # claimed its span. Running it earlier would let a broad capitalised-word
        # match swallow the first token of an address or the local part of an
        # email, and a name half-eaten by another pattern leaks the remainder.
        if self.candidates:
            masked, extra = mask_candidates(
                masked,
                notable=self.notable,
                keep=self.topical,
                given_name=self.given_name,
                title=self.title,
                title_prefix=self.title_prefix,
                corroborate=self.corroborate,
                notability_tier=self.notability_tier,
                minter=minter,
                headings_are_orthographic=self.headings_are_orthographic,
                relation_refusal=self.relation_refusal,
            )
            n += extra

        return LocalRedactionResult(text=masked, n_masked=n,
                                    restore_map=minter.assigned)
