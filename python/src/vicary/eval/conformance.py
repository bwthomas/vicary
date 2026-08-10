"""The fixture and the gates as language-neutral data — the spec three ports share.

Why this module exists. The fixture is 51 frames of ground truth and the gates
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
    print(f"wrote {directory / FRAMES_FILENAME}")
    print(f"wrote {directory / GATES_FILENAME}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
