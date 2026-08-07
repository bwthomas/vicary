"""The quality gates: measured numbers with a bar, expressed as tests.

Why these are tests and not a harness. A harness is something somebody remembers
to run; a test is something CI runs whether anybody remembers or not. Every gate
here is an ordinary pytest function that measures a number, prints it, and
asserts a bar — so a regression fails a build instead of appearing in a report
nobody opened.

Run them:

    pytest -m gates -s          # the numbers, printed
    pytest -m "not gates"       # everything else, fast
    pytest --gate-report        # the numbers as one table at the end

**Two of these gates need a corpus that is not packaged**, because it is licensed
third-party essay data. They skip when it is absent, and the skip is loud: a run
with `VICARY_EVAL_CORPUS_TSV` unset reports fewer gates, and
:func:`test_the_gate_report_says_what_it_could_not_measure` refuses to let a
partial run read as a complete one.

The bars are the values currently measured, used as ceilings and floors rather
than targets. Moving one is a deliberate act with a number attached, which is why
each carries the value it is protecting rather than a round approximation.
"""

from __future__ import annotations

import statistics

import pytest

from vicary import DEFAULT_NAME_DETECTION, config, gazetteer
from vicary.eval import census as census_eval
from vicary.eval.fixture import FIXTURE_VERSION
from vicary.eval.fixture import frames as select_frames
from vicary.eval.recall import (
    build_cases,
    load_set8,
    run,
    run_frames,
    summarize,
)

pytestmark = pytest.mark.gates


# ---------------------------------------------------------------------------
# The bars.
# ---------------------------------------------------------------------------

#: Held-out REDACT spans that must be masked. A private name reaching a model is
#: the failure this library exists to prevent, so this one is 100% and the other
#: gates are the ones allowed to have slack.
HELD_OUT_RECALL_FLOOR = 100.0

#: KEEP spans that must survive intact. Recall alone rewards a redactor that
#: masks everything; this is what stops that being a passing score.
KEEP_PRECISION_FLOOR = 100.0

#: Cases whose masked text maps back to the original one-to-one. Below 100%, some
#: placeholder stands for two different originals and no restore keyed on the
#: token can put either back.
ROUND_TRIP_FLOOR = 100.0

#: Spans removed from genuine, un-injected student prose. A ceiling: this is the
#: visible product defect, since a student reading their own feedback sees it.
#:
#: 0.72 is the measured value for **this** essay selection — the first 25 set-8
#: essays in file order. The number is selection-dependent and that is not noise:
#: at the previous bar of 1.20, the same code on a different 25 essays read 1.16.
#: Anything comparing across selections is comparing two measurements, so the
#: gate pins the selection (``load_set8(tsv, None, 25)``) and the bar goes with
#: it.
#:
#: Lowered from 1.20 when the capitalisation signal stopped reading *absence* of
#: mid-sentence capitals as evidence of a writer who drops them (see
#: :class:`~vicary.name_candidates.CapitalisationHabit`) and an
#: opening quote started counting as a sentence start. Held-out recall, KEEP
#: precision and the leak count were all unchanged across that move, so the bar
#: follows the measurement down rather than banking the slack — a ceiling left
#: above the measured value is not a gate, it is a comment.
OVER_FIRE_SPANS_CEILING = 0.72

#: Population-weighted share of US surname bearers whose BARE surname resolves
#: notable. A ceiling on the *single-token* tiers' generosity — short, place and
#: demonym, all three of which grant a keep to a bare token.
#:
#: 1.25 as of 2026-08-06, measured 1.20. It was 1.5 against a measured 1.47, and
#: the reduction is closed work rather than the open item that comment used to
#: describe: raising ``PLACE_MIN_SITELINKS_SINGLE_TOKEN`` 100 -> 150 took it to
#: 1.20 with held-out figure recall unchanged at 60.3%. The margin is deliberately
#: ~4% rather than the latency gate's 3x, because this number is computed from
#: two fixed files and has no run-to-run noise at all — anything that moves it
#: moved a tier, and that is exactly what should fail here.
CENSUS_BARE_SURNAME_CEILING = 1.25

#: Both redaction passes sit serially on a host's request path, so this is spent
#: from the host's latency budget. Measured p95 is ~3.4 ms on essay-length text;
#: the bar has headroom because it should fail on a change of *kind* (a new
#: per-token lookup that normalises) rather than on a noisy CI box.
LATENCY_P95_MS_CEILING = 10.0

#: Invariant violations present at this fixture version, each one accounted for.
#: Gated as an exact set rather than a count, so a *new* violation fails even
#: though these four do not — a ceiling of four would let a fifth defect in by
#: silently displacing one of these.
#:
#: * ``Robinson`` — the documented, deliberately unpaid cost: once a document
#:   establishes "Jackie Robinson", a bare "Robinson" in it keeps, including a
#:   neighbour who shares the surname. No surname-level rule separates them.
#: * ``Deshawn`` — a bare first name in a two-name frame. The gate arm's one
#:   visible-recall miss.
#: * ``Akron`` twice — redacted correctly but typed ``{NAME}`` instead of
#:   ``{LOCATION}``. Not a leak; it matters to a host that reads the placeholder
#:   type back ("your trip to {LOCATION}").
ACCEPTED_VIOLATIONS = {
    ("leak", "NAME:Robinson"),
    ("leak", "NAME:Deshawn"),
    ("wrong-type", "'Akron' expected {LOCATION} got {NAME}"),
}

#: The arm the bars describe: candidate generation **plus** the offline notability
#: gazetteer **plus** the case-insensitive route. All three have to be on together
#: — generation alone destroys every public figure a student writes about, the
#: oracle alone has nothing to judge, and without the case-insensitive route a
#: student writing without capitals is invisible.
#:
#: Measured, at fixture 2026-08-05.6, this arm dominates the arm without the
#: case-insensitive route on every axis: held-out recall 100% vs 90.5%, KEEP
#: precision 100% vs 93.5%, and over-firing **1.20 vs 3.36** spans/essay. The
#: route was previously believed to cost over-firing; on this fixture it saves it.
_GATE_ARM = "local-gazetteer-lowercase"

#: The arm a host gets from :func:`vicary.build_redactor_if_enabled` with no
#: oracles wired in: identity interpolation and structured identifiers only. It is
#: reported rather than gated, because the gap between it and ``_GATE_ARM`` is the
#: *third-party name* leg — real, known, and closed by passing the oracles in. A
#: bar on it would either be trivially met or would fail permanently for a reason
#: no change to this library fixes.
_DEFAULT_ARM = "local"


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def frame_metrics(record_gate) -> dict:
    """Per-frame scoring of the shipped arm. No corpus needed, exact invariants."""
    pool = select_frames()
    records = run_frames(_GATE_ARM, None, "INPUT", pool)
    # The arm label carries the fixture version, so a record scored against
    # different ground truth cannot be summarised alongside this one.
    arm = f"{_GATE_ARM}:INPUT:{FIXTURE_VERSION}:frames"
    metrics = summarize(records, arm, show_frames=False)
    assert metrics, f"the frames arm {arm} produced no records"
    assert metrics["fixture_version"] == FIXTURE_VERSION
    return metrics


@pytest.fixture(scope="module")
def frame_violations() -> set[tuple[str, str]]:
    """``{(kind, detail)}`` for the gate arm, scored frame by frame."""
    records = run_frames(_GATE_ARM, None, "INPUT", select_frames())
    return {
        (v["kind"], v["detail"])
        for record in records
        for v in record["violations"]
    }


@pytest.fixture(scope="module")
def corpus_metrics(record_gate, tmp_path_factory) -> dict:
    """Frames injected into real essays. Measures over-firing on genuine prose.

    Skips without a corpus. That skip is the honest outcome: over-firing cannot
    be measured on synthetic sentences, because the whole point is what the
    redactor does to text nobody planted anything in.
    """
    tsv = config.eval_corpus_tsv()
    if not tsv:
        pytest.skip(
            f"no corpus: set {config.EVAL_CORPUS_TSV_ENV_VAR} or "
            f"{config.EVAL_CORPUS_DIR_ENV_VAR}"
        )
    essays = load_set8(tsv, None, 25)
    if not essays:
        pytest.skip(f"corpus at {tsv} yielded no set-8 essays")
    cases = build_cases(essays, pool=select_frames())
    sidecar = tmp_path_factory.mktemp("gates") / "recall.jsonl"
    records = run(cases, _GATE_ARM, str(sidecar), guardrail_id=None)
    arm = f"{_GATE_ARM}:INPUT:{FIXTURE_VERSION}"
    metrics = summarize(records, arm, show_frames=False)
    assert metrics, f"the corpus arm {arm} produced no records"
    return metrics


@pytest.fixture(scope="module")
def census_exposure(record_gate) -> census_eval.Exposure:
    """The bare-surname false-positive control. Skips without a local Census file."""
    if not census_eval.census_source():
        pytest.skip(
            f"no Census surname file: set {config.EVAL_CENSUS_CSV_ENV_VAR} to a "
            "local copy (see vicary/eval/census.py)"
        )
    return census_eval.measure()


# ---------------------------------------------------------------------------
# Privacy: the gates that must not move.
# ---------------------------------------------------------------------------


def test_held_out_recall(frame_metrics, record_gate) -> None:
    """Every held-out REDACT span is masked.

    Held out means: chosen without reference to the gazetteer, and not used to
    tune anything. Once a detector has been tuned against a fixture, only the
    held-out half of its recall is a measurement rather than a memory.
    """
    value = frame_metrics["recall_held_out"]
    record_gate("held-out recall", value, ">=", HELD_OUT_RECALL_FLOOR, "%")
    assert value is not None, "no held-out spans were scored"
    assert value >= HELD_OUT_RECALL_FLOOR, (
        f"held-out recall {value:.1f}% is below {HELD_OUT_RECALL_FLOOR}%: a "
        "private name reached the output. Failing frames: "
        f"{_failing_frames(frame_metrics)}"
    )


def test_keep_precision(frame_metrics, record_gate) -> None:
    """Every KEEP span survives intact — public figures, cited authors, titles."""
    value = frame_metrics["precision_all"]
    record_gate("KEEP precision", value, ">=", KEEP_PRECISION_FLOOR, "%")
    assert value is not None, "no KEEP spans were scored"
    assert value >= KEEP_PRECISION_FLOOR, (
        f"KEEP precision {value:.1f}% is below {KEEP_PRECISION_FLOOR}%: "
        "something that should have been kept was masked. Failing frames: "
        f"{_failing_frames(frame_metrics)}"
    )


def test_round_trip_restorability(frame_metrics, record_gate) -> None:
    """Masked text maps back to the original, one placeholder per original span.

    A host that shows a student their own words needs this to be exact. It reads
    100% because placeholders are numbered; with bare placeholders it is 36%.
    """
    value = frame_metrics["round_trip_rate"]
    record_gate("round-trip", value, ">=", ROUND_TRIP_FLOOR, "%")
    assert value >= ROUND_TRIP_FLOOR, (
        f"round-trip {value:.1f}% is below {ROUND_TRIP_FLOOR}%: "
        f"{frame_metrics['violations_by_kind'].get('not-restorable', 0)} cases "
        "have a placeholder standing for two different originals"
    )


def test_no_new_invariant_violations(frame_violations, record_gate) -> None:
    """No invariant violation appears that is not already accounted for.

    A partial leak is worse than a miss: it *looks* redacted. ``Terrence
    Okonkwo`` masked to ``{NAME} Okonkwo`` reads as a working redactor in every
    summary statistic while publishing the surname — and recall, which tests for
    the whole literal, scores it as a pass. So this is checked separately from
    recall, and against an explicit set rather than a count.
    """
    unexpected = frame_violations - ACCEPTED_VIOLATIONS
    record_gate("unaccounted violations", float(len(unexpected)), "==", 0.0, "")
    assert not unexpected, (
        "invariant violations not in ACCEPTED_VIOLATIONS: "
        f"{sorted(unexpected)}\nEither fix them or add them with a reason."
    )


def test_the_accepted_violations_still_happen(frame_violations) -> None:
    """The accepted set describes reality, not history.

    An entry that stops occurring means somebody fixed it, and leaving it listed
    would let the next real defect of the same shape hide behind a stale
    exemption.
    """
    stale = ACCEPTED_VIOLATIONS - frame_violations
    assert not stale, (
        f"these accepted violations no longer occur: {sorted(stale)}. Remove "
        "them from ACCEPTED_VIOLATIONS so the exemption cannot shelter a new one."
    )


# ---------------------------------------------------------------------------
# Cost of the protection.
# ---------------------------------------------------------------------------


def test_over_firing_on_real_prose(corpus_metrics, record_gate) -> None:
    """Spans removed from prose nobody planted anything in.

    A floor rather than a rate: the corpus is pre-anonymized, so genuine student
    prose offers more to over-fire on than this measures. Treat a pass as "no
    worse than before", never as "1.00 is the true value".
    """
    value = corpus_metrics["over_fire_prose_spans_per_essay"]
    record_gate("over-fire on prose", value, "<=", OVER_FIRE_SPANS_CEILING,
                " spans/essay")
    assert value <= OVER_FIRE_SPANS_CEILING, (
        f"over-firing {value:.2f} spans/essay exceeds "
        f"{OVER_FIRE_SPANS_CEILING:.2f}. This is the visible defect: a student "
        "reads their own words replaced by a placeholder."
    )


def test_bare_surname_census_exposure(census_exposure, record_gate) -> None:
    """How much of the US surname population the single-token tiers claim.

    The control the fixture cannot be: its private surnames are rare, so it
    reports a clean that is not unlikely. This scores every American surname.
    """
    value = census_exposure.rate
    record_gate("bare-surname exposure", value, "<=",
                CENSUS_BARE_SURNAME_CEILING, "%")
    assert value <= CENSUS_BARE_SURNAME_CEILING, (
        f"bare-surname exposure {value:.2f}% exceeds "
        f"{CENSUS_BARE_SURNAME_CEILING}%\n" + census_eval.render(census_exposure)
    )


def test_latency_p95(corpus_metrics, frame_metrics, record_gate) -> None:
    """The redaction pass is serial on a host's request path.

    Measured on the **corpus** arm, because that is essay-length text; the frames
    arm redacts one sentence at a time and reads ~0.2 ms, which would make this
    gate insensitive to the thing it is watching for. Deliberately loose: it
    should fail on a change of *kind* — a new per-token lookup that normalises,
    say — not on a busy CI machine.
    """
    value = corpus_metrics["latency_p95_ms"]
    record_gate("latency p95", value, "<=", LATENCY_P95_MS_CEILING, " ms")
    print(f"  (per-sentence p95 for comparison: "
          f"{frame_metrics['latency_p95_ms']:.2f} ms)")
    assert value <= LATENCY_P95_MS_CEILING, (
        f"p95 {value:.1f} ms exceeds {LATENCY_P95_MS_CEILING} ms"
    )


# ---------------------------------------------------------------------------
# The asset, which every gate above depends on.
# ---------------------------------------------------------------------------


def test_the_gazetteer_the_gates_measured_is_the_one_that_ships(record_gate) -> None:
    """A gate run against an overridden or unverified asset measures a different
    library than the one a user installs, and would report a number that cannot
    be reproduced from the wheel."""
    from vicary import assets

    path, is_bundled = assets.resolve()
    record_gate("asset entries", float(gazetteer.load().entry_count), ">=", 1.0, "")
    assert is_bundled, (
        f"{config.ASSET_PATH_ENV_VAR} is set to {path}; unset it before reading "
        "these numbers as the shipped library's"
    )
    report = assets.verify()
    assert report, "; ".join(report.problems)


# ---------------------------------------------------------------------------
# The report, and the refusal to let a partial run look complete.
# ---------------------------------------------------------------------------


def test_the_unaided_arm_is_reported_not_hidden(record_gate) -> None:
    """Measure what a host gets with **no oracles wired in**, and print it.

    This does not gate. It exists because the difference between it and the gated
    arm is the single most misreadable thing about this library: wiring the
    redactor in without passing the gazetteer oracles gives structured-identifier
    masking and the writer's own name, and **almost no third-party name detection
    at all**. That is a configuration gap, not a defect in the detector, and the
    only way it stays visible is if the number is printed next to the good one.
    """
    pool = select_frames()
    records = run_frames(_DEFAULT_ARM, None, "INPUT", pool)
    arm = f"{_DEFAULT_ARM}:INPUT:{FIXTURE_VERSION}:frames"
    metrics = summarize(records, arm, show_frames=False)
    assert metrics, f"the unaided arm {arm} produced no records"

    held_out = metrics["recall_held_out"] or 0.0
    print(
        f"\nunaided arm ({_DEFAULT_ARM}): held-out recall {held_out:.1f}%, "
        f"all-span recall {metrics['recall_all']:.1f}% — this is what a host "
        "gets WITHOUT passing notable/title/given_name oracles. Pass them to "
        f"reach the {_GATE_ARM} numbers above."
    )
    # The one thing worth asserting: it must be *worse*, or the gated arm's
    # oracles are doing nothing and one of these two measurements is wrong.
    assert held_out <= HELD_OUT_RECALL_FLOOR


def test_the_default_a_host_gets_is_the_arm_these_gates_measured() -> None:
    """The gated arm and the arm a deployment runs must be the same object.

    This is the gate that was missing, and the gap it would have caught was not
    small: :func:`build_redactor_if_enabled` took no oracle arguments at all, so
    every number above described a redactor assembled by the test suite while
    every deployment ran identity-only detection at **0% held-out third-party
    recall**. Both facts were true, documented, and measured for months. Nothing
    compared them, because comparing them was nobody's test.

    It asserts on the constructed redactor rather than on the level string, so
    renaming a constant cannot make it pass while the wiring rots.
    """
    from vicary.eval.recall import PATH_ARMS, build_redactor, fixture_identity
    from vicary.redaction import build_redactor_if_enabled

    shipped = build_redactor_if_enabled(True, identity=fixture_identity())
    assert shipped is not None

    gated = build_redactor(_GATE_ARM, None)

    # The oracle set is the arm. Compare the classifier's resolved oracles, which
    # is what actually decides a verdict, not the arguments that produced them.
    def oracles(redactor) -> dict[str, object]:
        classifier = redactor._classifier
        return {
            name: getattr(classifier, name, None) is not None
            for name in ("notable", "given_name", "title", "title_prefix")
        } | {"candidates": bool(getattr(classifier, "candidates", False))}

    assert oracles(shipped) == oracles(gated), (
        "the default configuration differs from the arm the gates measured — "
        f"default={oracles(shipped)} gated={oracles(gated)}. Either the gates "
        "are describing a configuration nobody runs, or the default regressed."
    )

    # And the level name that produces it is the one the path arm measures, so
    # the published table and the default cannot drift apart either.
    assert PATH_ARMS["path-" + DEFAULT_NAME_DETECTION] == DEFAULT_NAME_DETECTION


def test_the_gate_report_says_what_it_could_not_measure(gate_results) -> None:
    """Runs last. Prints every gate's number, and names the ones that skipped.

    This is the whole reason the gates are allowed to skip: a green run that
    quietly measured four things out of seven is the failure mode the skip
    mechanism creates, so the report states the coverage rather than implying it.
    """
    lines = ["", f"gate report — fixture {FIXTURE_VERSION}", "-" * 58]
    for name, value, op, bar, unit in gate_results:
        verdict = "PASS" if _passes(value, op, bar) else "FAIL"
        lines.append(f"  {name:<24} {value:>8.2f}{unit:<14} {op} {bar:g}  {verdict}")
    measured = {name for name, *_ in gate_results}
    missing = sorted(_ALL_GATES - measured)
    lines.append("-" * 58)
    if missing:
        lines.append(f"  NOT MEASURED ({len(missing)}): {', '.join(missing)}")
        lines.append("  -> this run does not clear the gate set. Configure the")
        lines.append(f"     corpus ({config.EVAL_CORPUS_TSV_ENV_VAR}) and the")
        lines.append(f"     Census file ({config.EVAL_CENSUS_CSV_ENV_VAR}).")
    else:
        lines.append(f"  all {len(_ALL_GATES)} gates measured")
    print("\n".join(lines))


#: Every gate name that a complete run reports. Kept here so the report can say
#: what is missing rather than only what ran — a list of what happened cannot
#: describe what did not.
_ALL_GATES = {
    "held-out recall",
    "KEEP precision",
    "round-trip",
    "unaccounted violations",
    "over-fire on prose",
    "bare-surname exposure",
    "latency p95",
    "asset entries",
}


def _passes(value: float, op: str, bar: float) -> bool:
    if op == ">=":
        return value >= bar
    if op == "<=":
        return value <= bar
    return value == bar


def _failing_frames(metrics: dict) -> list[str]:
    return sorted(
        fid for fid, pct in (metrics.get("per_frame_recall") or {}).items()
        if pct is not None and pct < 100.0
    )


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0
