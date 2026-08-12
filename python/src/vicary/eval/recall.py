"""Measure whether a redactor actually redacts — and what it destroys doing it.

The gap this closes: **"redaction enabled" is a configuration assertion, not a
measurement.** A detector that silently misses names is indistinguishable from a
working one from inside a pipeline, which only ever sees "no intervention" and
reads that as clean text. This harness exists because that is precisely how a
managed detector was found returning 0 of 75 known spans while reporting full
coverage.

Why the corpus has to be synthetic
----------------------------------
ASAP set-8 essays cannot measure recall, because the corpus authors already
removed the PII: real names, places and dates were replaced with ``@PERSON1``,
``@LOCATION1``, ``@CAPS1``, ``@DATE1`` tokens before publication. An intervention
rate measured on ASAP is a measurement of a corpus that has no PII in it, and
would read as "nothing to redact" no matter how bad the detector is (see
``feedback_a_guard_needs_a_plausible_failing_case``). Run ``--census`` for the
per-essay token counts that establish this.

So this harness injects ground truth it knows the literal of, taken from
:mod:`vicary.eval.fixture`, and scores four things rather than one:

* ``recall`` — fraction of REDACT literals absent from the masked text, reported
  separately for the **held-out** frames. This is the privacy number, and only
  the held-out half of it is honest once a detector has been tuned.
* ``precision`` — fraction of KEEP literals still intact. Notable figures and
  names the assignment prompt supplies. Recall alone rewards a redactor that
  masks everything, which is a live failure mode for candidate generation.
* ``over-firing on real prose`` — spans and characters the redactor removes from
  the *un-injected* essay. A floor, not a rate: ASAP is pre-scrubbed, so real
  student prose offers more to over-fire on than this measures.
* ``invariant violations`` — partial leaks, mistyped placeholders, and
  ``not-restorable``, where one placeholder token stands for two different
  originals so no restore keyed on the token can put them back.

``latency_ms`` matters because both redaction passes sit SERIALLY on a host's
request path — inbound before the first model call, outbound after the last — so
they spend a host's latency budget, not their own.

Two modes
---------
``--frames`` scores each fixture frame in isolation. No corpus, no arguments,
runs in milliseconds, and it is where the structural invariants are exact.
Default mode injects frames into real set-8 essays, which is what measures
cross-sentence interference and over-firing on genuine prose.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import string
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

from vicary import config
from vicary.eval.fixture import (
    FIXTURE_VERSION,
    Frame,
    Span,
    align,
    check_frame,
    fixture_identity,
    is_asap_token,
    round_trips,
)
from vicary.eval.fixture import (
    frames as select_frames,
)

#: Frames injected per essay. Selection is round-robin, not sampled: with 36
#: frames and a 25-essay run, random sampling leaves frames unmeasured and the
#: per-frame table full of holes, which is how the previous fixture's single
#: name frame went unnoticed.
DEFAULT_PER_ESSAY: int = 3

#: Words whose trailing period abbreviates rather than ends a sentence.
#:
#: Wider than the detector's ``_TITLE_ABBREVIATIONS`` and deliberately so — the
#: two sets answer different questions. That one asks "is the capital after this
#: period orthographically forced?", which only titles make interesting. This one
#: asks "may a whole sentence be inserted here?", and "etc." or "Inc." are just as
#: fatal a place to insert one as "Mrs." is.
_ABBREVIATIONS: frozenset[str] = frozenset({
    "mr", "mrs", "ms", "mx", "dr", "prof", "rev", "fr", "sr", "jr", "st",
    "sgt", "capt", "lt", "col", "gen", "gov", "sen", "rep", "hon",
    "etc", "vs", "eg", "ie", "cf", "al", "approx", "est",
    "inc", "ltd", "co", "corp", "dept", "univ", "no", "vol", "fig", "pp",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sept", "sep", "oct",
    "nov", "dec", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
})

#: The word a period sits directly behind, if the period follows a word at all.
_TRAILING_WORD = re.compile(r"([A-Za-z]+)\.\Z")

#: A single letter before a period is an initial — ``J. K. Rowling`` — with one
#: exception, and it is the most common word to end a student's sentence.
#: "in between @PERSON2 and I." is not an initial, and rejecting it cost real
#: injection points in every ASAP essay written in the first person. Lowercase
#: "i" is here too, because the corpora these run on are informal enough that the
#: dropped capital is the norm rather than the slip.
_INITIAL_LETTERS: frozenset[str] = frozenset(
    letter for letter in string.ascii_letters if letter not in "Ii"
)

#: Punctuation that closes the sentence *after* its terminator, and which the
#: injection point therefore belongs behind rather than in front of. Without this
#: the period in ``the country." America`` reads as mid-word, because the very
#: next character is not whitespace.
_CLOSES_A_SENTENCE = "\"'’”)]"

#: What may open the sentence after a break, beyond a capital letter: the quote or
#: bracket around one, a digit, and ``@`` — ASAP's own anonymization tokens
#: (``@PERSON1``, ``@LOCATION1``) start hundreds of perfectly good sentences in
#: that corpus, and reading them as non-starts discarded a third of its points.
_OPENS_A_SENTENCE = "\"'“‘(@0123456789"


def injection_points(base: str) -> list[int]:
    """Offsets in ``base`` at which inserting a sentence still reads as prose.

    Every ``.`` is *not* a sentence end, and treating it as one silently produced
    malformed carrier text: a frame landing inside "U.S." split an essay into
    ``cars in the U.`` + frame + ``S. has gone down``. That is not a harder test
    of the detector, it is a different text than the one the gate claims to
    measure, and it was 14 of ASAP-AES's 75 injection points.

    The offset first steps over any closing quote or bracket, because the frame
    belongs behind ``the country."`` rather than inside it. Then four things
    disqualify the period, and each rejects a case the others do not:

    * **No whitespace after it** — the period is inside a word. This is the
      "U.S." split above, and it is the one that produced visibly broken prose.
      It also rejects a genuine sentence end the essay forgot to space
      (``something.When``), and that is right rather than regrettable: the frame
      would be inserted flush against the next word.
    * **Nothing after it** — end of text, where there is no following sentence
      for the frame to precede.
    * **Lowercase after it** — ``the U.S. has gone down``. The abbreviation's
      *final* period does have whitespace after it, so only the continuation
      being lowercase tells us the sentence did not end.
    * **A known abbreviation before it** — ``Mrs. Okonkwo``, ``J. K. Rowling``.
      Here the following capital is real, so the rule above cannot see it; a
      single letter counts as an abbreviation for exactly the initials case.

    What this deliberately does not do is fix the essays. A corpus writes what it
    writes; the harness's job is to choose where it may cut in.
    """
    out: list[int] = []
    for index, char in enumerate(base):
        if char not in ".!?":
            continue
        at = index + 1
        while at < len(base) and base[at] in _CLOSES_A_SENTENCE:
            at += 1
        rest = base[at:]
        following = rest.lstrip()
        if not following or len(following) == len(rest):
            continue
        if not (following[0].isupper() or following[0] in _OPENS_A_SENTENCE):
            continue
        # Against the terminator, not against `at`: any closing quote consumed
        # above sits between the abbreviation and the offset, and would hide it.
        word = _TRAILING_WORD.search(base[:index + 1])
        if word and (word.group(1) in _INITIAL_LETTERS
                     or word.group(1).lower() in _ABBREVIATIONS):
            continue
        out.append(at)
    return out


@dataclass
class Case:
    """One injected essay plus the ground truth of what went into it."""

    essay_id: str
    text: str
    base: str
    frames: tuple[Frame, ...]
    #: Offsets into ``base`` where each frame in ``frames`` was inserted, in the
    #: order they were applied — descending, so an earlier insertion cannot shift
    #: a later one. Recorded rather than recomputed because this is the only part
    #: of case building that consumes the RNG, and it is what
    #: :mod:`vicary.eval.carrier` writes out so the other two ports can reproduce
    #: the same carrier text without reimplementing Python's Mersenne Twister.
    slots: tuple[int, ...] = ()

    @property
    def composite(self) -> Frame:
        """The injected essay as one frame, for essay-scale invariant checks.

        ``align`` and the restore invariants are properties of the whole masked
        text, not of one sentence — two names masked to the same ``{NAME}`` in
        different paragraphs are exactly as unrestorable as two in one sentence.
        """
        spans: list[Span] = []
        for frame in self.frames:
            spans.extend(frame.spans)
        return Frame(
            frame_id="composite",
            sentence=self.text,
            spans=tuple(spans),
        )


def unusable_for_injection(base: str,
                           per_essay: int = DEFAULT_PER_ESSAY) -> str | None:
    """Why ``base`` cannot carry ``per_essay`` frames, or ``None`` if it can.

    Split out from :func:`build_cases` so a skip can be *declared* rather than
    inferred from a short plan. An essay written without a space after its full
    stops — "skateboarding.Laughed", "Interesting fly.It was" — offers almost no
    place to cut in, and two of ASAP-AES's twenty-five are written that way.
    """
    points = injection_points(base)
    # `+ 2` because the draw is from `points[1:-1]`.
    if len(points) < per_essay + 2:
        return (f"{len(points)} usable injection points; carrying {per_essay} "
                f"frames needs {per_essay + 2}, since the draw excludes the "
                "first and the last")
    return None


def build_cases(essays: list[tuple[str, str]], *, seed: int = 20260805,
                per_essay: int = DEFAULT_PER_ESSAY,
                pool: tuple[Frame, ...] | None = None) -> list[Case]:
    """Inject ``per_essay`` fixture frames into each essay at sentence ends."""
    import random

    rng = random.Random(seed)
    pool = pool or select_frames()
    cases: list[Case] = []
    cursor = 0
    for essay_id, base in essays:
        # Insert at sentence ends so the frame reads as prose, not as a suffix.
        # `injection_points` is what decides which periods qualify, and
        # `unusable_for_injection` owns the arithmetic on how many are enough —
        # the old guard asked for `per_essay + 1` while drawing from a population
        # two shorter, so it was not merely loose, it guarded the wrong number.
        if unusable_for_injection(base, per_essay) is not None:
            continue
        stops = injection_points(base)
        picks = [pool[(cursor + k) % len(pool)] for k in range(per_essay)]
        cursor += per_essay
        slots = sorted(rng.sample(stops[1:-1], k=len(picks)), reverse=True)
        text = base
        for frame, at in zip(picks, slots, strict=True):
            text = text[:at] + " " + frame.sentence + text[at:]
        cases.append(Case(essay_id=essay_id, text=text, base=base,
                          frames=tuple(picks), slots=tuple(slots)))
    return cases


def build_cases_from_plan(essays: list[tuple[str, str]],
                          plan: dict,
                          pool: tuple[Frame, ...] | None = None) -> list[Case]:
    """The same cases, rebuilt from recorded slots rather than from the RNG.

    This is the path every port shares. ``build_cases`` above consumes Python's
    Mersenne Twister to choose where each frame lands, and reproducing that draw
    in JavaScript and Ruby would mean reimplementing MT19937 and
    ``random.sample`` three times over — a lot of code with nothing to do with
    redaction, whose failure mode is silent: different slots make different
    carrier text, and the gates simply report different numbers.

    So the slots are recorded once, in ``conformance/carrier.json``, and read
    back here. They are an *input* — where to inject — never an answer. Recall,
    over-firing and latency are still measured by each port from its own output,
    which is the distinction the spec already draws between carrying ``sentence``
    and refusing to carry ``aligns``.

    The essay each slot refers to is checked by digest, because an offset into
    the wrong text is not an error anything downstream would notice.
    """
    import hashlib

    by_id = {frame.frame_id: frame for frame in (pool or select_frames())}
    planned = {entry["essay_id"]: entry for entry in plan["cases"]}
    cases: list[Case] = []

    for essay_id, base in essays:
        entry = planned.get(essay_id)
        if entry is None:
            continue
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
        if digest != entry["base_sha256"]:
            raise ValueError(
                f"essay {essay_id} in this corpus does not match the one the "
                f"carrier plan was built from (sha256 {digest[:12]} vs "
                f"{entry['base_sha256'][:12]}). The recorded offsets point into "
                "different text, so every number downstream would be wrong "
                "without being detectably wrong. Regenerate with "
                "`python -m vicary.eval.carrier --write`."
            )
        picks = tuple(by_id[fid] for fid in entry["frames"])
        text = base
        for frame, at in zip(picks, entry["slots"], strict=True):
            text = text[:at] + " " + frame.sentence + text[at:]
        cases.append(Case(essay_id=essay_id, text=text, base=base,
                          frames=picks, slots=tuple(entry["slots"])))

    # Every planned essay, or none of them. A corpus that matches the plan only
    # partly would measure a *subset* and report it under the same gate — and the
    # degenerate case of matching nothing is worse than wrong, because
    # over-firing and latency both then compute as 0.0, which in a `<=` gate is
    # the most comfortable pass on the board. Refusing is the only outcome that
    # cannot be mistaken for a green run.
    if len(cases) != len(plan["cases"]):
        found = {case.essay_id for case in cases}
        missing = [e["essay_id"] for e in plan["cases"]
                   if e["essay_id"] not in found]
        raise ValueError(
            f"the carrier plan names {len(plan['cases'])} essays and this "
            f"corpus supplied {len(cases)} of them; missing "
            f"{', '.join(missing[:5])}{' …' if len(missing) > 5 else ''}. "
            "Refusing to measure a subset, because over-firing and latency on "
            "an empty or partial set compute as 0.0 and read as a pass."
        )

    # And every *corpus* essay is either carried or named unusable. The check
    # above only proves the plan got what it asked for; it cannot see an essay
    # the plan never asked about. That was safe while a plan always covered its
    # whole corpus, and stopped being safe when `unusable` made a short plan
    # legitimate — without this, a plan that quietly lost ten essays would
    # measure the fifteen it kept and report them under the same gate.
    unusable = {entry["essay_id"] for entry in plan.get("unusable", [])}
    accounted = {case.essay_id for case in cases} | unusable
    unaccounted = [essay_id for essay_id, _ in essays if essay_id not in accounted]
    if unaccounted:
        raise ValueError(
            f"the corpus supplies {len(essays)} essays and the carrier plan "
            f"accounts for {len(accounted)} of them — {len(plan['cases'])} "
            f"carried and {len(unusable)} declared unusable. Unaccounted: "
            f"{', '.join(unaccounted[:5])}"
            f"{' …' if len(unaccounted) > 5 else ''}. An essay the plan neither "
            "carries nor names is one it dropped silently, which is the same "
            "comfortable pass as a partial match. Regenerate with "
            "`python -m vicary.eval.carrier --write`."
        )
    return cases


def asap_pii_token_census(essays: list[tuple[str, str]]) -> dict:
    """Count the corpus authors' own anonymization tokens (``@PERSON1`` …).

    Evidence for why recall cannot be measured on ASAP directly: if these are
    dense, the real PII is already gone. It is also the best proxy available for
    the production leak rate — ``@PERSON`` runs 4.03 per essay across set-8.
    """
    import re

    pat = re.compile(r"@[A-Z]+\d*")
    per_essay = [len(pat.findall(text)) for _, text in essays]
    kinds: dict[str, int] = {}
    for _, text in essays:
        for tok in pat.findall(text):
            kind = tok.rstrip("0123456789")
            kinds[kind] = kinds.get(kind, 0) + 1
    return {
        "essays": len(essays),
        "mean_tokens_per_essay": statistics.fmean(per_essay) if per_essay else 0,
        "essays_with_any": sum(1 for n in per_essay if n),
        "by_kind": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class SpanOutcome:
    """One span's verdict, carrying the frame it rode in and the split."""

    frame_id: str
    entity: str
    literal: str
    verdict: str
    held_out: bool
    #: For REDACT: the literal is gone. For KEEP: the literal survived intact.
    passed: bool = False


def score_spans(frames_used: tuple[Frame, ...], masked: str) -> list[SpanOutcome]:
    out: list[SpanOutcome] = []
    for frame in frames_used:
        for span in frame.spans:
            if span.expect_count is not None:
                # Presence cannot decide a bare surname that also occurs inside a
                # kept full name; see Span.expect_count. Counted at essay scale,
                # so a frame injected into a corpus essay that happens to contain
                # the same surname would read as extra occurrences — which is the
                # correct direction to fail in, since it flags the collision
                # rather than averaging it away.
                passed = masked.count(span.literal) == span.expect_count
            else:
                present = span.literal in masked
                passed = present if span.is_keep else not present
            out.append(
                SpanOutcome(
                    frame_id=frame.frame_id,
                    entity=span.entity,
                    literal=span.literal,
                    verdict=span.verdict,
                    held_out=frame.held_out,
                    passed=passed,
                )
            )
    return out


def score_case(case: Case, masked: str, masked_base: str) -> dict:
    """Per-case recall, precision, over-firing on clean prose, invariants."""
    outcomes = score_spans(case.frames, masked)
    violations = check_frame(case.composite, masked)

    # Over-firing on the un-injected essay. Everything masked here fired on text
    # with no PII in it, but the two reasons it can fire are unrelated: prose
    # (a precision defect) versus one of ASAP's own @-tokens (not a defect, but a
    # rewrite of a token the encoder was trained on). Reported apart, because
    # summed they read as one catastrophic precision failure and the prose leg is
    # zero.
    base_alignment = align(case.base, masked_base)
    prose_fp = [
        region for _, region in base_alignment.pairs if not is_asap_token(region)
    ]

    return {
        "essay_id": case.essay_id,
        "frames": [f.frame_id for f in case.frames],
        "spans": [vars(o) for o in outcomes],
        "n_redact": sum(1 for o in outcomes if o.verdict == "redact"),
        "n_keep": sum(1 for o in outcomes if o.verdict == "keep"),
        "recalled": sum(1 for o in outcomes
                        if o.verdict == "redact" and o.passed),
        "kept": sum(1 for o in outcomes if o.verdict == "keep" and o.passed),
        "violations": [vars(v) for v in violations],
        "round_trips": round_trips(case.composite, masked),
        "base_fp_spans": len(prose_fp),
        "base_fp_chars": sum(len(r) for r in prose_fp),
        "base_fp_examples": prose_fp[:5],
        "base_asap_rewrites": len(base_alignment.pairs) - len(prose_fp),
        "base_aligned": base_alignment.ok,
        "input_chars": len(case.text),
        "masked_chars": len(masked),
    }


def load_set8(tsv: str, ids_path: str | None, limit: int) -> list[tuple[str, str]]:
    import csv

    wanted: set[str] | None = None
    if ids_path and os.path.exists(ids_path):
        wanted = set()
        with open(ids_path, encoding="utf-8") as fh:
            for line in fh:
                cid = json.loads(line).get("composition_id", "")
                if cid:
                    wanted.add(cid.rsplit(":", 1)[-1])

    out: list[tuple[str, str]] = []
    with open(tsv, encoding="latin-1", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("essay_set") != "8":
                continue
            eid = row.get("essay_id", "")
            if wanted is not None and eid not in wanted:
                continue
            out.append((eid, row.get("essay", "")))
            if len(out) >= limit:
                break
    return out


#: Arms that construct the redactor the way a HOST does — through
#: :func:`vicary.redaction.build_redactor_if_enabled` — rather than by calling
#: :class:`Redactor` with hand-picked oracles the way every other arm here does.
#:
#: The distinction is the whole point of these three. Every published number in
#: this harness's history described a redactor the harness itself assembled, and
#: for a long stretch no deployment could assemble that redactor at all: the
#: entry point took no oracle arguments, so "redaction is on in production" and
#: "the measured arm" were different objects with no test between them. An arm
#: that goes through the front door cannot drift from the front door.
PATH_ARMS: dict[str, str] = {
    "path-identity": "identity",
    "path-gazetteer": "gazetteer",
    "path-gazetteer-lowercase": "gazetteer-lowercase",
}


def build_redactor(mode: str, guardrail_id: str | None, *,
                   candidates: bool = False, corroborate: bool = True,
                   number_placeholders: bool = True,
                   headings_are_orthographic: bool = True,
                   relation_refusal: bool = True,
                   title_relation_refusal: bool = True):
    from vicary.redaction import Redactor

    if mode in PATH_ARMS:
        # Deliberately NOT Redactor(...) — the host entry point, with the level
        # passed as a host would pass it. The knobs this harness varies for the
        # research arms (corroborate, number_placeholders, …) are not reachable
        # from here, which is correct: they are not reachable from a deployment
        # either, and an arm that could set them would stop describing one.
        from vicary.redaction import build_redactor_if_enabled

        redactor = build_redactor_if_enabled(
            True, identity=fixture_identity(), names=PATH_ARMS[mode],
        )
        assert redactor is not None, "local mode must build a redactor"
        return redactor
    if mode in ("local-gazetteer", "local-gazetteer-lowercase"):
        # The shippable arm: generation plus the offline notability oracle. Both
        # halves must be on together — generation alone destroys every public
        # figure a student writes about, and the oracle alone has nothing to
        # judge. `-lowercase` adds the case-insensitive route, which is the only
        # one that reaches a student writing without capitals.
        #
        # prompt_context is left EMPTY on purpose. It is the free, exact leg of
        # the notability filter, and populating it here would let it carry frames
        # the gazetteer is supposed to carry alone ("Rosa Parks" would pass
        # without the gazetteer being consulted at all). Nothing has confirmed
        # callers populate it, so the honest measurement is the unaided one.
        from vicary.gazetteer import (
            is_common_given_name,
            is_notable,
            is_settlement,
            is_title,
            is_title_prefix,
            notability,
        )

        return Redactor(
            local=True,
            identity=fixture_identity(),
            local_candidates=True,
            notable=is_notable,
            title=is_title,
            title_prefix=is_title_prefix,
            settlement=is_settlement,
            given_name=(is_common_given_name
                        if mode == "local-gazetteer-lowercase" else None),
            corroborate=corroborate,
            notability_tier=notability,
            number_placeholders=number_placeholders,
            headings_are_orthographic=headings_are_orthographic,
            relation_refusal=relation_refusal,
            title_relation_refusal=title_relation_refusal,
        )
    if mode == "local-candidates" or (mode == "local" and candidates):
        # Candidate generation with NO notability oracle: recall-maximal,
        # precision-minimal. Measuring the two arms apart is the point — the
        # recall it buys is a property of generation, the precision it costs is
        # a property of the missing gazetteer, and averaging them describes
        # neither.
        from vicary.local_classifier import StudentIdentity  # noqa: F401

        return Redactor(local=True, identity=fixture_identity(),
                           local_candidates=True)
    if mode == "local":
        # The fixture's student, exactly as a caller would have supplied them.
        # The third-party names are deliberately absent from it: a mention of
        # someone else is the leg identity interpolation cannot reach, and it
        # must surface as a miss rather than be quietly fed in.
        return Redactor(local=True, identity=fixture_identity())
    return Redactor(simulate=(mode == "stub"), guardrail_id=guardrail_id)


def run(cases: list[Case], mode: str, sidecar: str, *,
        guardrail_id: str | None, source: str = "INPUT",
        corroborate: bool = True,
        number_placeholders: bool = True,
        headings_are_orthographic: bool = True,
        relation_refusal: bool = True,
        title_relation_refusal: bool = True) -> list[dict]:
    """Redact every case under ``mode``, appending one JSONL record per case.

    Resumable by ``(arm, essay_id)`` so a re-run picks up where a kill left off
    (MUST #8c) — this spends money in guardrail mode. The arm carries the
    fixture version as well as the mode and source, because a record scored
    against different ground truth is a foreign record, not a resumable one.

    Corroboration is in the arm key for the same reason, and it is the reason
    that reason is not academic: it is a *default-on* behaviour change, so a
    control run and a treatment run differ in nothing the old key could see, and
    the resume logic would have silently returned the control's records as the
    treatment's result.
    """
    arm = f"{mode}:{source}:{FIXTURE_VERSION}"
    if not corroborate:
        arm += ":no-corroborate"
    if not number_placeholders:
        arm += ":unnumbered"
    if not headings_are_orthographic:
        arm += ":no-heading-rule"
    if not relation_refusal:
        arm += ":no-relation-refusal"
    if not title_relation_refusal:
        arm += ":no-title-relation-refusal"

    done: set[tuple[str, str]] = set()
    if os.path.exists(sidecar):
        with open(sidecar, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                done.add((rec.get("mode", ""), rec.get("essay_id", "")))

    redactor = build_redactor(mode, guardrail_id, corroborate=corroborate,
                              number_placeholders=number_placeholders,
                              headings_are_orthographic=headings_are_orthographic,
                              relation_refusal=relation_refusal,
                              title_relation_refusal=title_relation_refusal)
    results: list[dict] = []
    # Load the gazetteer before the clock starts. It is a one-time ~84 ms cost
    # here and ~207 ms in Ruby, and whichever essay happens to be first pays all
    # of it: at n=25 that single sample lands at or above p95 and sets the gate's
    # answer by itself. The number the gate claims is essay-length redaction
    # latency, not process startup, and leaving this in made the SAME code report
    # 3.1 ms or 4.0 ms depending only on whether something earlier in the process
    # had touched the asset. Excluded deliberately, and excluded identically in
    # all three ports.
    if cases:
        redactor._apply(cases[0].base[:200], source=source)
    with open(sidecar, "a", encoding="utf-8") as fh:
        for case in cases:
            if (arm, case.essay_id) in done:
                continue
            t0 = time.monotonic()
            result = redactor._apply(case.text, source=source)
            elapsed = (time.monotonic() - t0) * 1000.0
            # The clean-prose pass is measurement scaffolding, not on the
            # request path, so its latency is deliberately not counted.
            base_result = redactor._apply(case.base, source=source)
            rec = score_case(case, result.text, base_result.text)
            rec.update({
                "mode": arm,
                "source": source,
                "fixture_version": FIXTURE_VERSION,
                "latency_ms": round(elapsed, 1),
                "char_units": result.char_units,
                "intervened": result.intervened,
            })
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            results.append(rec)
    return results


def run_frames(mode: str, guardrail_id: str | None, source: str,
               pool: tuple[Frame, ...], *,
               corroborate: bool = True,
               number_placeholders: bool = True,
               headings_are_orthographic: bool = True,
        relation_refusal: bool = True,
        title_relation_refusal: bool = True) -> list[dict]:
    """Score each frame in isolation — no corpus, exact invariants."""
    # Every axis in the arm label must also reach the redactor. It did not:
    # ``number_placeholders`` was named in the label and dropped here, so an
    # ``:unnumbered`` frames run built a NUMBERED redactor and returned the
    # treatment's result under the control's name. The two arms came back
    # identical, which is the signature this repo already has a rule for
    # (feedback_identical_ab_metrics_indict_the_instrument) — a label is not a
    # measurement.
    redactor = build_redactor(
        mode, guardrail_id, corroborate=corroborate,
        number_placeholders=number_placeholders,
        headings_are_orthographic=headings_are_orthographic,
        relation_refusal=relation_refusal,
        title_relation_refusal=title_relation_refusal,
    )
    # Same envelope rule as run(): the arm label has to name every axis the run
    # varied, or the summary silently reports zero rows for the treatment.
    arm = f"{mode}:{source}:{FIXTURE_VERSION}:frames"
    if not corroborate:
        arm += ":no-corroborate"
    if not number_placeholders:
        arm += ":unnumbered"
    if not headings_are_orthographic:
        arm += ":no-heading-rule"
    if not relation_refusal:
        arm += ":no-relation-refusal"
    if not title_relation_refusal:
        arm += ":no-title-relation-refusal"
    out: list[dict] = []
    for frame in pool:
        t0 = time.monotonic()
        result = redactor._apply(frame.sentence, source=source)
        elapsed = (time.monotonic() - t0) * 1000.0
        out.append({
            "essay_id": frame.frame_id,
            "mode": arm,
            "frames": [frame.frame_id],
            "held_out": frame.held_out,
            "spans": [vars(o) for o in score_spans((frame,), result.text)],
            "n_redact": len(frame.redact_spans),
            "n_keep": len(frame.keep_spans),
            "recalled": sum(1 for s in frame.redact_spans
                            if s.literal not in result.text),
            "kept": sum(1 for s in frame.keep_spans if s.literal in result.text),
            "violations": [vars(v) for v in check_frame(frame, result.text)],
            "round_trips": round_trips(frame, result.text),
            "base_fp_spans": 0,
            "base_fp_chars": 0,
            "base_aligned": True,
            "latency_ms": round(elapsed, 3),
            "char_units": result.char_units,
            "intervened": result.intervened,
            "masked": result.text,
        })
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass
class _Tally:
    passed: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.passed += int(ok)

    @property
    def pct(self) -> float | None:
        return 100.0 * self.passed / self.total if self.total else None

    def render(self) -> str:
        if not self.total:
            return "     n/a"
        return f"{self.passed:3}/{self.total:3} = {self.pct:5.1f}%"


@dataclass
class _Report:
    recall: dict[bool, _Tally] = field(default_factory=dict)
    precision: dict[bool, _Tally] = field(default_factory=dict)
    by_entity: dict[str, _Tally] = field(default_factory=dict)
    by_frame: dict[str, _Tally] = field(default_factory=dict)


def summarize(records: list[dict], mode: str, *, show_frames: bool = True) -> dict:
    """Print the report and return the metrics dict for eval-history."""
    rows = [r for r in records if r["mode"] == mode]
    if not rows:
        print(f"{mode}: no records")
        return {}

    rep = _Report()
    for split in (False, True):
        rep.recall[split] = _Tally()
        rep.precision[split] = _Tally()

    for row in rows:
        for span in row["spans"]:
            held = bool(span["held_out"])
            target = rep.recall if span["verdict"] == "redact" else rep.precision
            target[held].add(span["passed"])
            key = f"{span['entity']}/{span['verdict']}"
            rep.by_entity.setdefault(key, _Tally()).add(span["passed"])
            # Keyed by verdict too: a frame reported at "1/2" is otherwise
            # ambiguous between a leaked name and an over-redacted keep, which
            # are opposite defects with opposite fixes.
            rep.by_frame.setdefault(
                f"{span['frame_id']} [{span['verdict']}]", _Tally()
            ).add(span["passed"])

    kinds = Counter(
        v["kind"] for row in rows for v in row["violations"]
    )
    lat = sorted(r["latency_ms"] for r in rows)
    units = [r["char_units"] for r in rows]
    fp_spans = [r["base_fp_spans"] for r in rows]
    fp_chars = [r["base_fp_chars"] for r in rows]
    asap_rw = [r.get("base_asap_rewrites", 0) for r in rows]
    n_rt = sum(1 for r in rows if r["round_trips"])

    r_all = _Tally(
        sum(t.passed for t in rep.recall.values()),
        sum(t.total for t in rep.recall.values()),
    )
    p_all = _Tally(
        sum(t.passed for t in rep.precision.values()),
        sum(t.total for t in rep.precision.values()),
    )

    print(f"\n=== {mode}   (n={len(rows)} cases)")
    print(f"RECALL    all       : {r_all.render()}")
    print(f"          visible   : {rep.recall[False].render()}")
    print(f"          HELD OUT  : {rep.recall[True].render()}   "
          f"<- the honest number")
    print(f"PRECISION all       : {p_all.render()}   (KEEP spans left intact)")
    print(f"          visible   : {rep.precision[False].render()}")
    print(f"          HELD OUT  : {rep.precision[True].render()}")
    print(f"round-trips exactly : {n_rt}/{len(rows)}")
    print(f"latency ms          : p50={lat[len(lat) // 2]:.1f}  "
          f"p95={lat[min(int(len(lat) * 0.95), len(lat) - 1)]:.1f}  "
          f"max={lat[-1]:.1f}")
    print(f"billed units        : mean={statistics.fmean(units):.2f}  "
          f"cost/essay=${statistics.fmean(units) * 0.0001:.6f}")
    if fp_spans:
        print(f"over-fire on prose  : {statistics.fmean(fp_spans):.2f} spans/essay, "
              f"{statistics.fmean(fp_chars):.0f} chars/essay  "
              f"(a FLOOR — ASAP is pre-scrubbed)")
        examples = [e for r in rows for e in r.get("base_fp_examples", [])][:6]
        if examples:
            print(f"                      e.g. {examples}")
    if any(asap_rw):
        print(f"ASAP @-token rewrite: {statistics.fmean(asap_rw):.2f} /essay  "
              f"(not a precision defect — but it converts tokens the encoder "
              f"was TRAINED on into ones it has never seen)")

    print("\nviolations by kind:")
    if kinds:
        for kind, n in kinds.most_common():
            print(f"  {kind:22} {n:4}")
    else:
        print("  (none)")

    print("\nper entity/verdict:")
    for key, tally in sorted(rep.by_entity.items(),
                             key=lambda kv: (kv[1].pct or 0.0)):
        print(f"  {key:34} {tally.render()}")

    if show_frames:
        print("\nper frame (misses first):")
        for fid, tally in sorted(rep.by_frame.items(),
                                 key=lambda kv: (kv[1].pct or 0.0, kv[0])):
            print(f"  {fid:38} {tally.render()}")

    return {
        "fixture_version": FIXTURE_VERSION,
        "cases": len(rows),
        "recall_all": r_all.pct,
        "recall_visible": rep.recall[False].pct,
        "recall_held_out": rep.recall[True].pct,
        "recall_span_count": r_all.total,
        "precision_all": p_all.pct,
        "precision_visible": rep.precision[False].pct,
        "precision_held_out": rep.precision[True].pct,
        "precision_span_count": p_all.total,
        "round_trip_rate": 100.0 * n_rt / len(rows),
        "violations_by_kind": dict(kinds),
        "latency_p50_ms": lat[len(lat) // 2],
        "latency_p95_ms": lat[min(int(len(lat) * 0.95), len(lat) - 1)],
        "char_units_mean": statistics.fmean(units),
        "over_fire_prose_spans_per_essay": statistics.fmean(fp_spans) if fp_spans else 0.0,
        "over_fire_prose_chars_per_essay": statistics.fmean(fp_chars) if fp_chars else 0.0,
        "asap_token_rewrites_per_essay": statistics.fmean(asap_rw) if asap_rw else 0.0,
        "per_frame_recall": {
            fid: t.pct for fid, t in sorted(rep.by_frame.items())
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="vicary-eval",
        description="Score a redaction arm for recall, precision, over-firing "
                    "and round-trip restorability.",
    )
    # No path literal here, and no dataset named. The corpus is third-party data
    # the operator supplies, is not packaged, and its location is per-machine —
    # see vicary.config.
    # A mis-named corpus directory raises rather than resolving to "", so that a
    # configured-but-wrong corpus cannot read as an unconfigured one. Carry the
    # message to the --tsv check below instead of letting it surface here as a
    # traceback out of argparse's default expression.
    try:
        default_tsv, corpus_problem = config.eval_corpus_tsv(), ""
    except config.CorpusDirectoryError as exc:
        default_tsv, corpus_problem = "", str(exc)
    ap.add_argument("--tsv", default=default_tsv,
                    help=f"the corpus TSV (or set "
                         f"{config.EVAL_CORPUS_TSV_ENV_VAR}, or "
                         f"{config.EVAL_CORPUS_DIR_ENV_VAR} to a directory "
                         f"holding one).")
    ap.add_argument("--ids", default="",
                    help="JSONL of composition_id records restricting which "
                         "essays are used. Omit to take the first --n in file "
                         "order.")
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--per-essay", type=int, default=DEFAULT_PER_ESSAY)
    ap.add_argument("--modes", default="local",
                    help="comma-separated: local,local-candidates,"
                         "local-gazetteer,local-gazetteer-lowercase,stub,"
                         "guardrail,path-identity,path-gazetteer,"
                         "path-gazetteer-lowercase. `local-candidates` adds "
                         "third-party name detection with no notability oracle "
                         "— recall-maximal, precision-minimal, and the two are "
                         "reported apart. `local-gazetteer` adds the oracle; the "
                         "`-lowercase` variant also adds the case-insensitive "
                         "route. The `path-*` arms build the redactor through "
                         "build_redactor_if_enabled, the way a host does, so "
                         "they measure what a deployment can actually run.")
    ap.add_argument("--sources", default="INPUT",
                    help="comma-separated: INPUT,OUTPUT. ANONYMIZE behaves "
                         "differently on each, so this is an axis, not a detail.")
    ap.add_argument("--groups", default="",
                    help="restrict the fixture to recall,keep,intersect,"
                         "structured (default: all)")
    ap.add_argument("--frames", action="store_true",
                    help="score each fixture frame in isolation; no corpus "
                         "needed and the structural invariants are exact")
    ap.add_argument("--guardrail-id",
                    default=config.get(config.GUARDRAIL_ID_ENV_VAR) or None)
    # Separate file from the pre-fixture harness: the record schema changed
    # shape (per-span outcomes and violations replaced a flat recall list), and
    # two schemas in one JSONL is how a reader ends up averaging across them.
    ap.add_argument("--sidecar", default="vicary-recall-eval.jsonl",
                    help="append-only per-case record file. Guardrail mode "
                         "spends money per call, so a run that dies partway "
                         "must not lose what it already paid for.")
    ap.add_argument("--metrics-out", default="",
                    help="write the summary metrics dict here as JSON, for "
                         "eval-history recording")
    ap.add_argument("--no-number-placeholders", action="store_true",
                    help="emit a bare {NAME} for every person instead of "
                         "{NAME_1}, {NAME_2}. The unrestorable arm, kept "
                         "measurable so the round-trip recovery is a delta.")
    ap.add_argument("--no-corroborate", action="store_true",
                    help="turn OFF same-document surname corroboration, which "
                         "is on by default in the shipped arm. The arm that "
                         "destroys the author an essay is about, kept "
                         "measurable so the recovery is a delta rather than a "
                         "claim.")
    ap.add_argument("--no-heading-rule", action="store_true",
                    help="read a section heading's capitals as the writer's "
                         "choice rather than as title case. The control for the "
                         "heading arm, which is where over-firing on real "
                         "student prose comes from.")
    ap.add_argument("--no-relation-refusal", action="store_true",
                    help="stop the local context from refusing "
                         "corroboration for a bare surname it marks as "
                         "someone in the writer's life. The control for "
                         "the neighbour-who-shares-a-famous-surname arm.")
    ap.add_argument("--no-title-relation-refusal", action="store_true",
                    help="stop an attached first-person relation from "
                         "overriding a title-tier keep. The control for the "
                         "neighbour-whose-name-is-also-a-novel arm.")
    ap.add_argument("--census", action="store_true",
                    help="report the corpus authors' own @PERSON tokens and exit")
    args = ap.parse_args(argv)

    groups = tuple(g.strip() for g in args.groups.split(",") if g.strip())
    pool = select_frames(groups=groups or None)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    sources = [s.strip().upper() for s in args.sources.split(",") if s.strip()]
    for mode in modes:
        if mode == "guardrail" and not args.guardrail_id:
            print("guardrail mode needs --guardrail-id (or "
                  f"{config.GUARDRAIL_ID_ENV_VAR})", file=sys.stderr)
            return 2

    metrics: dict[str, dict] = {}

    if args.frames:
        print(f"fixture {FIXTURE_VERSION}: {len(pool)} frames, "
              f"{sum(len(f.spans) for f in pool)} spans "
              f"({sum(1 for f in pool if f.held_out)} frames held out)")
        for mode in modes:
            for source in sources:
                recs = run_frames(mode, args.guardrail_id if mode == "guardrail"
                                  else None, source, pool,
                                  corroborate=not args.no_corroborate,
                                  number_placeholders=not args.no_number_placeholders,
                                  headings_are_orthographic=not args.no_heading_rule,
                                  relation_refusal=not args.no_relation_refusal,
                                  title_relation_refusal=not args.no_title_relation_refusal)
                arm = f"{mode}:{source}:{FIXTURE_VERSION}:frames"
                if args.no_corroborate:
                    arm += ":no-corroborate"
                if args.no_number_placeholders:
                    arm += ":unnumbered"
                if args.no_heading_rule:
                    arm += ":no-heading-rule"
                if args.no_relation_refusal:
                    arm += ":no-relation-refusal"
                if args.no_title_relation_refusal:
                    arm += ":no-title-relation-refusal"
                metrics[arm] = summarize(recs, arm)
        if args.metrics_out:
            with open(args.metrics_out, "w", encoding="utf-8") as fh:
                json.dump(metrics, fh, indent=1)
        return 0

    if not args.tsv:
        print(corpus_problem or
              ("no corpus: pass --tsv, set "
               f"{config.EVAL_CORPUS_TSV_ENV_VAR}, or use --frames"),
              file=sys.stderr)
        return 2

    essays = load_set8(args.tsv, args.ids, args.n)
    print(f"essays: {len(essays)}")
    if not essays:
        return 1

    if args.census:
        print(json.dumps(asap_pii_token_census(essays), indent=1))
        return 0

    cases = build_cases(essays, per_essay=args.per_essay, pool=pool)
    covered = {f.frame_id for c in cases for f in c.frames}
    print(f"cases: {len(cases)}  injected spans: "
          f"{sum(len(f.spans) for c in cases for f in c.frames)}  "
          f"frames covered: {len(covered)}/{len(pool)}")
    missing = [f.frame_id for f in pool if f.frame_id not in covered]
    if missing:
        # Never let a coverage gap read as a pass (no silent caps).
        print(f"NOT MEASURED ({len(missing)}): {', '.join(missing)}",
              file=sys.stderr)

    os.makedirs(os.path.dirname(args.sidecar) or ".", exist_ok=True)
    arms = []
    for mode in modes:
        for source in sources:
            run(cases, mode, args.sidecar,
                guardrail_id=args.guardrail_id if mode == "guardrail" else None,
                source=source, corroborate=not args.no_corroborate,
                number_placeholders=not args.no_number_placeholders,
                headings_are_orthographic=not args.no_heading_rule,
                relation_refusal=not args.no_relation_refusal,
                title_relation_refusal=not args.no_title_relation_refusal)
            arm = f"{mode}:{source}:{FIXTURE_VERSION}"
            if args.no_corroborate:
                arm += ":no-corroborate"
            if args.no_number_placeholders:
                arm += ":unnumbered"
            if args.no_heading_rule:
                arm += ":no-heading-rule"
            if args.no_relation_refusal:
                arm += ":no-relation-refusal"
            if args.no_title_relation_refusal:
                arm += ":no-title-relation-refusal"
            arms.append(arm)

    all_recs = []
    with open(args.sidecar, encoding="utf-8") as fh:
        for line in fh:
            try:
                all_recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    for arm in arms:
        metrics[arm] = summarize(all_recs, arm)

    if args.metrics_out:
        with open(args.metrics_out, "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=1)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
