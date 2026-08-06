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
import statistics
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


@dataclass
class Case:
    """One injected essay plus the ground truth of what went into it."""

    essay_id: str
    text: str
    base: str
    frames: tuple[Frame, ...]

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
        stops = [i + 1 for i, ch in enumerate(base) if ch in ".!?"]
        if len(stops) < per_essay + 1:
            continue
        picks = [pool[(cursor + k) % len(pool)] for k in range(per_essay)]
        cursor += per_essay
        slots = sorted(rng.sample(stops[1:-1], k=len(picks)), reverse=True)
        text = base
        for frame, at in zip(picks, slots, strict=True):
            text = text[:at] + " " + frame.sentence + text[at:]
        cases.append(Case(essay_id=essay_id, text=text, base=base,
                          frames=tuple(picks)))
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


def build_redactor(mode: str, guardrail_id: str | None, *,
                   candidates: bool = False, corroborate: bool = True,
                   number_placeholders: bool = True,
                   headings_are_orthographic: bool = True,
                   relation_refusal: bool = True):
    from vicary.redaction import Redactor

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
            given_name=(is_common_given_name
                        if mode == "local-gazetteer-lowercase" else None),
            corroborate=corroborate,
            notability_tier=notability,
            number_placeholders=number_placeholders,
            headings_are_orthographic=headings_are_orthographic,
            relation_refusal=relation_refusal,
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
        relation_refusal: bool = True) -> list[dict]:
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
                              relation_refusal=relation_refusal)
    results: list[dict] = []
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
        relation_refusal: bool = True) -> list[dict]:
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
    # No path literal here. The corpus is licensed third-party data, is not
    # packaged, and its location is per-machine — see vicary.config.
    ap.add_argument("--tsv", default=config.eval_corpus_tsv(),
                    help=f"ASAP {config.EVAL_CORPUS_FILENAME} (or set "
                         f"{config.EVAL_CORPUS_TSV_ENV_VAR} / "
                         f"{config.EVAL_CORPUS_DIR_ENV_VAR}).")
    ap.add_argument("--ids", default="",
                    help="JSONL of composition_id records restricting which "
                         "essays are used. Omit to take the first --n in file "
                         "order.")
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--per-essay", type=int, default=DEFAULT_PER_ESSAY)
    ap.add_argument("--modes", default="local",
                    help="comma-separated: local,local-candidates,"
                         "local-gazetteer,local-gazetteer-lowercase,stub,"
                         "guardrail. `local-candidates` adds third-party name "
                         "detection with no notability oracle — recall-maximal, "
                         "precision-minimal, and the two are reported apart. "
                         "`local-gazetteer` adds the oracle; the `-lowercase` "
                         "variant also adds the case-insensitive route.")
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
                                  relation_refusal=not args.no_relation_refusal)
                arm = f"{mode}:{source}:{FIXTURE_VERSION}:frames"
                if args.no_corroborate:
                    arm += ":no-corroborate"
                if args.no_number_placeholders:
                    arm += ":unnumbered"
                if args.no_heading_rule:
                    arm += ":no-heading-rule"
                if args.no_relation_refusal:
                    arm += ":no-relation-refusal"
                metrics[arm] = summarize(recs, arm)
        if args.metrics_out:
            with open(args.metrics_out, "w", encoding="utf-8") as fh:
                json.dump(metrics, fh, indent=1)
        return 0

    if not args.tsv:
        print("no corpus: pass --tsv, set "
              f"{config.EVAL_CORPUS_TSV_ENV_VAR}, or use --frames",
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
                relation_refusal=not args.no_relation_refusal)
            arm = f"{mode}:{source}:{FIXTURE_VERSION}"
            if args.no_corroborate:
                arm += ":no-corroborate"
            if args.no_number_placeholders:
                arm += ":unnumbered"
            if args.no_heading_rule:
                arm += ":no-heading-rule"
            if args.no_relation_refusal:
                arm += ":no-relation-refusal"
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
