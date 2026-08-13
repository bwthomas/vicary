"""The quality gates: measured numbers with a bar, expressed as tests.

Why these are tests and not a harness. A harness is something somebody remembers
to run; a test is something CI runs whether anybody remembers or not. Every gate
here is an ordinary pytest function that measures a number, prints it, and
asserts a bar — so a regression fails a build instead of appearing in a report
nobody opened.

Run them:

    pytest -m gates -s          # the numbers, and the report table at the end
    pytest -m "not gates"       # everything else, fast

**One of these gates needs data that is not packaged**: bare-surname exposure
needs the Census surname file. The three corpus gates — over-firing, latency, and
held-out recall in a carrier essay — are measured against the shipped corpus on a
bare checkout, and fall back to skipping only when the *resolved* corpus is
operator-supplied and no TSV is configured. Every skip is loud: a run that could
not measure a gate reports fewer of them, and
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
from vicary.eval import baseline, carrier, conformance, measured
from vicary.eval import census as census_eval
from vicary.eval import corpus as corpus_mod
from vicary.eval.fixture import FIXTURE_VERSION
from vicary.eval.fixture import frames as select_frames
from vicary.eval.recall import (
    build_cases_from_plan,
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
#: 0.60 is the measured value for **this** essay selection — the first 25 set-8
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
#:
#: Lowered again 0.72 -> 0.60 on 2026-08-07, by the same rule and in the rarer
#: direction: rebuilding the ``given`` tier from US birth counts instead of
#: notable people's first tokens **improved both legs at once**, closing the
#: `Deshawn` leak (visible recall 96.2% -> 100.0%) while over-firing fell. The
#: floor that produced it was picked against this number — 1,600 births measures
#: 0.72 and would have passed with no headroom, 1,800 measures 0.60 — so banking
#: the slack here would retire the very measurement that chose the floor.
#:
#: Raised 0.60 -> 0.61 on 2026-08-12, and this one is the exception the rule
#: above is worded against — it is arithmetic, not slack. Rejecting malformed
#: carrier injection points (:func:`~vicary.eval.recall.injection_points`) found
#: two ASAP essays written with no space after their full stops, which offer
#: nowhere to cut in and are now declared unusable. The *same detector* removed
#: the *same spans* from the *same prose*: 15 spans over 25 essays became 14 over
#: 23, because the two essays that left contributed one span between them. Only
#: the denominator moved. A bar that did not follow it would fail a run in which
#: nothing over-fired that did not over-fire before.
#:
#: Per corpus since 2026-08-12, because this is the only gate whose bar describes
#: the *prose* rather than the detector. See ``_OVER_FIRE_SPANS_CEILINGS``.
OVER_FIRE_SPANS_CEILING = 0.61

#: The bar each corpus is held to, with the constant above as the fallback.
#:
#: ASAP-AES set 8 is a personal narrative whose names the corpus authors had
#: already replaced with ``@PERSON`` tokens; PERSUADE's prompts are source-based,
#: so its essays name real entities constantly — `Venus`, `Vauban`, `Paris`,
#: `Earth`, `Science Olympiad`. 8.15 spans/essay against 0.61 is that difference
#: and not a regression. One bar cannot hold both: loose enough for PERSUADE
#: retires the gate on ASAP-AES, tight enough for ASAP-AES fails PERSUADE for
#: being the prose it is.
_OVER_FIRE_SPANS_CEILINGS: dict[str, float] = {
    "asap-aes-set8": 0.61,
    "persuade-20": 8.15,
}


def over_fire_ceiling(corpus_id: str) -> float:
    """The over-fire bar for ``corpus_id``, or the fallback for an unlisted one.

    The fallback is deliberately the tighter of the two bars: an unregistered
    corpus should fail loudly rather than inherit PERSUADE's slack.
    """
    return _OVER_FIRE_SPANS_CEILINGS.get(corpus_id, OVER_FIRE_SPANS_CEILING)

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

#: How much slower than the last release this port may redact the corpus. Read
#: from the baseline file rather than written here, so one number governs all
#: three ports and a release updates it in one place. The absolute ceiling this
#: replaced lived here as 10.0 and was a claim about the machine: it passed on a
#: laptop and failed on the runner that had to enforce it.
LATENCY_REGRESSION_PCT_CEILING = float(
    (baseline.load() or {}).get("tolerance_pct", 8.0)
)

#: Invariant violations present at this fixture version, each one accounted for.
#: Gated as an exact set rather than a count, so a *new* violation fails even
#: though these two do not — a ceiling of two would let a third defect in by
#: silently displacing one of these.
#:
#: * ``Robinson`` — the documented, deliberately unpaid cost: once a document
#:   establishes "Jackie Robinson", a bare "Robinson" in it keeps, including a
#:   neighbour who shares the surname. No surname-level rule separates them.
#:
#: Two entries were removed on 2026-08-07, both surfaced by
#: ``test_the_accepted_violations_still_happen`` going red — an exemption going
#: stale IS the pass:
#:
#: * ``Akron`` twice, ``wrong-type`` — masked correctly but typed ``{NAME}``.
#:   Closed by the ``settlement`` tier, which types it ``{LOCATION}``.
#: * ``Deshawn``, ``leak`` — the arm's one visible-recall miss. The ``given``
#:   tier was built from the first tokens of *notable people's* names, which
#:   answers "was a famous person called this" and missed Deshawn at every bearer
#:   floor. Rebuilding it from **US birth counts** closed it and took visible
#:   recall 96.2% -> 100.0%.
ACCEPTED_VIOLATIONS = {
    ("leak", "NAME:Robinson"),
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

    Skips only when the resolved corpus is operator-supplied and unconfigured.
    That skip is the honest outcome: over-firing cannot be measured on synthetic
    sentences, because the whole point is what the redactor does to text nobody
    planted anything in.

    It deliberately does *not* guard on ``VICARY_EVAL_CORPUS_TSV``. That guard
    asks whether the operator supplied a corpus, and since ``persuade-20`` became
    the default the answer no longer decides whether one is available — this port
    reported NEEDS corpus against twenty essays in the repository while
    TypeScript and Ruby measured them, which is the reference port failing to
    measure what it is the reference for.
    """
    unreadable = corpus_mod.unreadable_reason()
    if unreadable:
        pytest.skip(unreadable)
    corpus_id, essays = corpus_mod.load_essays()
    plan = carrier.load_plan(corpus_id)
    # From the recorded plan, not from the RNG — the same path TypeScript and
    # Ruby take, so a divergence between the ports is a divergence in the
    # redactor rather than in where three languages happened to inject.
    cases = build_cases_from_plan(essays, plan, pool=select_frames())
    sidecar = tmp_path_factory.mktemp("gates") / "recall.jsonl"
    records = run(cases, _GATE_ARM, str(sidecar), guardrail_id=None)
    arm = f"{_GATE_ARM}:INPUT:{FIXTURE_VERSION}"
    metrics = summarize(records, arm, show_frames=False)
    assert metrics, f"the corpus arm {arm} produced no records"
    return metrics


@pytest.fixture(scope="module")
def census_exposure(record_gate) -> census_eval.Exposure:
    """The bare-surname false-positive control.

    No longer skippable on a checkout. The surname table ships in
    `conformance/census/`, so this is measured here and in CI; it used to skip
    everywhere but on the one machine holding a hand-downloaded copy of a file
    census.gov stopped serving. `VICARY_EVAL_CENSUS_CSV` still overrides.

    It skips only outside a checkout, where there is no `conformance/` to read —
    the same condition every other spec-dependent test here skips on, and not a
    condition any CI run is in.
    """
    if not census_eval.census_source() and census_eval.shipped_dir() is None:
        pytest.skip(
            "no conformance/census/ in this tree and no "
            f"{config.EVAL_CENSUS_CSV_ENV_VAR} set (see vicary/eval/census.py)"
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


def test_held_out_recall_in_a_carrier_essay(corpus_metrics, record_gate) -> None:
    """The same spans again, riding in real essays instead of alone.

    Not redundant with :func:`test_held_out_recall`, and the gap between them let a
    real regression through. That gate reads the **frames** arm, where each frame
    is scored alone; this reads the **essay-carrier** arm, where a frame is injected
    into 3,000-odd characters of somebody else's prose. Every document-level signal
    the detector weighs — the capitalisation habit, same-document surname
    corroboration — sees a completely different input in the two arms, so a change
    can be flat on one and move the other.

    It did. A variant that read the lower-case-opening *rate* below the
    mark floor scored 15/15 held out on frames and 27/28 in carriers: in essay
    20739 one dropped opening across 59 sentences is a 1.7% rate, which withdrew
    the permissive path and leaked "terrence okonkwo". All eight gates printed
    PASS. This is that failing case, kept as a gate — see
    :func:`~vicary.name_candidates.capitalisation_habit`.

    Skips without a corpus, like the over-firing gate, and for the same reason:
    there is no carrier to ride in.
    """
    value = corpus_metrics["recall_held_out"]
    record_gate("held-out recall (carrier)", value, ">=", HELD_OUT_RECALL_FLOOR, "%")
    assert value is not None, "no held-out spans were scored"
    assert value >= HELD_OUT_RECALL_FLOOR, (
        f"held-out recall in a carrier essay {value:.1f}% is below "
        f"{HELD_OUT_RECALL_FLOOR}%: a private name reached the output once a real "
        "essay was around it, even though the frames arm is clean. Failing "
        f"frames: {_failing_frames(corpus_metrics)}"
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
    corpus_id = corpus_mod.resolve_corpus_id()
    ceiling = over_fire_ceiling(corpus_id)
    value = corpus_metrics["over_fire_prose_spans_per_essay"]
    record_gate("over-fire on prose", value, "<=", ceiling, " spans/essay")
    assert value <= ceiling, (
        f"over-firing {value:.2f} spans/essay on {corpus_id} exceeds "
        f"{ceiling:.2f}. This is the visible defect: a student reads their own "
        "words replaced by a placeholder."
    )


def test_a_corpus_supplying_only_some_planned_essays_is_refused() -> None:
    """A subset is refused rather than measured.

    Caught by pointing the harness at a one-essay TSV: it built zero cases, and
    over-firing and latency then computed as 0.0 — which in a ``<=`` gate is the
    most comfortable pass on the board. Two gates went green on no data at all.
    """
    plan = carrier.load_plan(corpus_mod.resolve_corpus_id())
    with pytest.raises(ValueError, match="Refusing to measure a subset"):
        build_cases_from_plan([("not-a-planned-id", "some other essay")], plan,
                              pool=select_frames())


def test_a_corpus_essay_the_plan_neither_carries_nor_names_is_refused() -> None:
    """The subset check above cannot see an essay the plan never asked about.

    It compares cases built against cases planned, so a plan that quietly lost
    ten of its twenty-five essays matches itself perfectly and measures fifteen —
    under the same gate, at the same bar. That was unreachable while a plan always
    covered its whole corpus, and became reachable the moment ``unusable`` made a
    short plan legitimate. So the count reconciles against the *corpus*: carried
    plus named must equal supplied.
    """
    import hashlib

    base = ("The dog barked. The cat ran. The bird flew. The fish swam. "
            "The cow mooed. And then it was quiet.")
    plan = {
        "cases": [{
            "essay_id": "carried",
            "base_sha256": hashlib.sha256(base.encode("utf-8")).hexdigest(),
            "base_chars": len(base),
            "frames": [select_frames()[0].frame_id],
            "slots": [16],
        }],
        "unusable": [],
    }
    essays = [("carried", base), ("neither-carried-nor-named", base)]

    with pytest.raises(ValueError, match="dropped silently"):
        build_cases_from_plan(essays, plan, pool=select_frames())

    # And naming it is what makes the same corpus measurable — otherwise the
    # check would just be an assertion that plans are never short.
    plan["unusable"] = [{"essay_id": "neither-carried-nor-named",
                         "reason": "declared for this test"}]
    assert len(build_cases_from_plan(essays, plan, pool=select_frames())) == 1


def test_a_corpus_essay_that_does_not_match_the_plan_is_refused() -> None:
    """An offset into the wrong text yields a plausible number, not an error."""
    plan = carrier.load_plan(corpus_mod.resolve_corpus_id())
    first = plan["cases"][0]["essay_id"]
    with pytest.raises(ValueError, match="does not match the one the carrier"):
        build_cases_from_plan([(first, "a different essay entirely")], plan,
                              pool=select_frames())


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


def test_latency_regression(corpus_metrics, frame_metrics, record_gate) -> None:
    """Is this build slower than the last release, on hardware that can say?

    Measured on the **corpus** arm, because that is essay-length text; the frames
    arm redacts one sentence at a time and reads ~0.2 ms, which would make this
    gate insensitive to the thing it is watching for.

    The gate is skipped — reported, never silently passed — when the run cannot
    be compared against the baseline. That is the common case on a developer's
    machine and it is the honest one: this laptop measures the same commit at
    3.7 ms where the recorded runner measured 9.2, so a comparison would report
    a 60% improvement that is entirely the hardware.
    """
    measured = corpus_metrics["latency_pooled_median_ms"]
    corpus_id, _ = corpus_mod.load_essays()
    c = baseline.compare(measured, corpus_id)
    print(f"  {baseline.render(c)}")
    print(f"  (p95 across essays, ungated, for comparison: "
          f"{corpus_metrics['latency_p95_ms']:.2f} ms; per-sentence p95 "
          f"{frame_metrics['latency_p95_ms']:.2f} ms)")
    if not c.comparable:
        pytest.skip(f"latency not compared: {c.reason}")
    assert c.regression_pct is not None
    record_gate("latency vs last release", c.regression_pct, "<=",
                LATENCY_REGRESSION_PCT_CEILING, "%")
    assert c.regression_pct <= LATENCY_REGRESSION_PCT_CEILING, (
        f"{measured:.3f} ms is {c.regression_pct:+.2f}% against the last "
        f"release's {c.baseline_ms:.3f} ms, over the "
        f"{LATENCY_REGRESSION_PCT_CEILING:.0f}% bar"
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
    measured = {name for name, *_ in gate_results}
    missing = sorted(_ALL_GATES - measured)
    # The corpus is named, not implied. Two of these gates carry a per-corpus bar
    # — over-firing is 8.15 spans/essay on persuade-20 against 0.61 on ASAP-AES —
    # so a board that prints `8.15 <= 8.15 PASS` without saying which corpus
    # produced it is a number filed under no corpus at all.
    corpus_id = (corpus_mod.resolve_corpus_id()
                 if "over-fire on prose" in measured else "(none measured)")
    lines = ["", f"gate report — fixture {FIXTURE_VERSION}, corpus {corpus_id}",
             "-" * 58]
    for name, value, op, bar, unit in gate_results:
        verdict = "PASS" if _passes(value, op, bar) else "FAIL"
        lines.append(f"  {name:<26} {value:>8.2f}{unit:<14} {op} {bar:g}  {verdict}")
    lines.append("-" * 58)
    if missing:
        lines.append(f"  NOT MEASURED ({len(missing)}): {', '.join(missing)}")
        lines.append("  -> this run does not clear the gate set. Both requirements")
        lines.append("     ship in conformance/ — corpora/ and census/ — so this")
        lines.append("     usually means a partial checkout rather than a missing")
        lines.append(f"     setting. To override: {config.EVAL_CORPUS_TSV_ENV_VAR}")
        lines.append(f"     and {config.EVAL_CENSUS_CSV_ENV_VAR}.")
    else:
        lines.append(f"  all {len(_ALL_GATES)} gates measured")
    print("\n".join(lines))


#: Every gate name that a complete run reports. Kept here so the report can say
#: what is missing rather than only what ran — a list of what happened cannot
#: describe what did not.
_ALL_GATES = {
    "held-out recall",
    "held-out recall (carrier)",
    "KEEP precision",
    "round-trip",
    "unaccounted violations",
    "over-fire on prose",
    "bare-surname exposure",
    "latency vs last release",
    "asset entries",
}


# ---------------------------------------------------------------------------
# The published measurements are this run's measurements.
# ---------------------------------------------------------------------------


def test_the_published_measurements_are_what_this_run_measures() -> None:
    """``conformance/measured.json`` still describes the reference.

    The other two ports assert their corpus counts against that file rather than
    against literals, which makes the reference the one port nothing was checking
    — its measurement could drift, the file would go stale, and the first symptom
    would be TypeScript and Ruby failing against a document that describes
    nothing. That names the wrong two ports and costs a bisect through two
    languages to attribute.

    So the reference checks its own publication. A failure here means run
    `just sync-conformance` with a corpus configured and read the diff: either
    the detector changed and every port's numbers move together, or it did not
    and something is wrong with the measurement.
    """
    unreadable = corpus_mod.unreadable_reason()
    if unreadable:
        pytest.skip(unreadable)
    corpus_id, digest, gates, envelope = measured.measure()
    published = measured.load_measurements(corpus_id)

    assert published["envelope"] == envelope, (
        "measured.json was taken in a different envelope from this run — the "
        "fixture, the arm or the corpus slice moved, so its numbers describe a "
        "different question"
    )
    assert published["carrier_text_sha256"] == digest, (
        "measured.json pins carrier text this run does not reproduce, so every "
        "number in it was measured on text no port now builds"
    )
    assert published["corpus_gates"] == gates


# ---------------------------------------------------------------------------
# The published bars are the asserted bars.
# ---------------------------------------------------------------------------


def test_the_spec_lists_exactly_the_gates_this_report_knows_about() -> None:
    """``_ALL_GATES`` is what this report calls a complete run. A spec listing
    eight of them hands the ports a smaller bar than this one holds, and the
    ninth would never be missed by name."""
    published = {g["label"] for g in conformance.load_gates_document()["gates"]}
    assert published == _ALL_GATES


def test_every_published_bar_is_the_bar_this_port_asserts() -> None:
    """The numbers in ``conformance/gates.json``, against the module constants
    the gates above assert against.

    Without this, ``gates.json`` is documentation: somebody moves
    ``OVER_FIRE_SPANS_CEILING`` and the other two ports keep holding the old
    value, or hold a tighter one and fail for no reason anybody can find.

    Each front door asks this for itself — the TypeScript and Ruby suites carry
    the same test against their own constants. It deliberately does *not* live
    with the generator: a spec that checked its own bars against one port's
    literals would be checking Python twice and the other two not at all.
    """
    asserted = {
        "held-out recall": HELD_OUT_RECALL_FLOOR,
        "held-out recall (carrier)": HELD_OUT_RECALL_FLOOR,
        "KEEP precision": KEEP_PRECISION_FLOOR,
        "round-trip": ROUND_TRIP_FLOOR,
        "unaccounted violations": 0.0,
        "over-fire on prose": OVER_FIRE_SPANS_CEILING,
        "bare-surname exposure": CENSUS_BARE_SURNAME_CEILING,
        "latency vs last release": LATENCY_REGRESSION_PCT_CEILING,
        "asset entries": 1.0,
    }
    gates = conformance.load_gates_document()["gates"]
    published = {g["label"]: g["bar"] for g in gates}
    assert published == asserted

    # And the per-corpus overrides, which the map above cannot carry. Left out,
    # `bars_by_corpus` would be the one bar in the file nothing reconciles — the
    # exact documentation-not-gate state this test exists to prevent, and the
    # loosest number on the board is a poor place to start allowing it.
    by_corpus = {
        g["label"]: g["bars_by_corpus"] for g in gates if "bars_by_corpus" in g
    }
    assert by_corpus == {"over-fire on prose": _OVER_FIRE_SPANS_CEILINGS}


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
