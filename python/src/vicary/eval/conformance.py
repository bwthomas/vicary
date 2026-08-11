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
PRIMITIVE_TITLES: tuple[str, ...] = (
    "charlotte's web", "the lion", "the lion king", "to kill a mockingbird",
)


def _primitive_settlement(name: str) -> bool:
    return name.lower() in PRIMITIVE_SETTLEMENTS


def _primitive_title(text: str) -> bool:
    return text.lower().replace("’", "'") in PRIMITIVE_TITLES


def _primitive_title_prefix(key: str) -> bool:
    return any(t == key or t.startswith(key + " ") for t in PRIMITIVE_TITLES)


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

    def spans(fn: Any) -> Any:
        return lambda text: [list(s) for s in fn(text)]

    return {
        "document_version": DOCUMENT_VERSION,
        "corpus": corpus,
        "token_lists": lists,
        "stop_tokens": list(PRIMITIVE_STOP_TOKENS),
        "oracles": {
            "settlements": list(PRIMITIVE_SETTLEMENTS),
            "titles": list(PRIMITIVE_TITLES),
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
        "constants": {
            "allcaps_run": nc._ALLCAPS_RUN,
            "drops_capitals_min_rate": nc._DROPS_CAPITALS_MIN_RATE,
            "heading_max_chars": nc._HEADING_MAX_CHARS,
            "lowercase_min_tokens": nc._LOWERCASE_MIN_TOKENS,
            "marks_proper_nouns_min": nc._MARKS_PROPER_NOUNS_MIN,
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
            "mid_sentence_capitals_with_headings": over_corpus(
                lambda x: sorted(
                    nc._mid_sentence_capitals(
                        x, nc._sentence_starts(x), nc._heading_spans(x)
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
