"""The counterbalance must reach the answer, and an odd round count breaks it.

The pair swaps which side runs first every round, because whichever side runs
second inherits what the first did to the cache and the clock. That swap only
cancels the penalty if the two orders carry equal weight in the statistic, and
two things were stopping it.

The round counts were odd -- 15 in TypeScript, 5 in the other two -- so one
order always had one more round than the other. And the statistic pooled all
rounds per side and took a single median, which lets the over-represented order
decide. Measured on TypeScript at 15 rounds against an unchanged checkout: the
previous-first rounds read +6.80%, the current-first rounds +3.16%, and the
pooled statistic landed at +6.61% -- beside the over-represented order rather
than between the two, on a gate whose bar is 8%. That is what failed the npm
publish for 0.2.14 twice while CI passed the same commit.
"""

from __future__ import annotations

import importlib.util
import statistics
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "latency_pair", ROOT / "tools" / "latency_pair.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = _load()


def test_every_default_round_count_is_even():
    """An odd count cannot be counterbalanced. This is the whole defect."""
    for impl, rounds in lp.DEFAULT_ROUNDS.items():
        assert rounds % 2 == 0, f"{impl} has {rounds} rounds, which cannot be balanced"


def test_an_odd_rounds_flag_is_refused_rather_than_silently_unbalanced():
    assert lp.main(["--impl", "typescript", "--rounds", "15", "--out", "/dev/null"]) == 1


def test_the_two_orders_are_weighted_equally_not_pooled():
    """Ten rounds where the second-position rounds are uniformly slower.

    Indices 0,2,4.. are one order and 1,3,5.. the other. Pooling these and
    taking one median lands on whichever half is larger; the balanced statistic
    sits between the two, which is what the counterbalance was for.
    """
    values = [10.0, 20.0] * 5  # order A always 10, order B always 20
    assert lp.balanced_median(values) == pytest.approx(15.0)
    assert statistics.median(values) == pytest.approx(15.0)

    # Now make the halves unequal in size, the way an odd round count does.
    lopsided = [10.0, 20.0, 10.0, 20.0, 10.0]
    assert statistics.median(lopsided) == pytest.approx(10.0)  # pooled: order A wins
    assert lp.balanced_median(lopsided) == pytest.approx(15.0)  # balanced: still between


def test_balanced_median_is_the_plain_median_when_the_orders_agree():
    """No order effect means no correction -- the fix cannot invent a delta."""
    values = [3.0, 3.2, 2.9, 3.1, 3.0, 3.05]
    assert lp.balanced_median(values) == pytest.approx(
        statistics.median(values), abs=0.06)


def test_an_empty_round_list_is_an_error_not_a_zero():
    with pytest.raises(ValueError):
        lp.balanced_median([])
