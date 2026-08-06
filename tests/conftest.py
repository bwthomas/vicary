"""Shared fixtures. Chiefly the recorder the gate report reads.

The gates in ``test_gates.py`` each measure one number. Collecting them requires
somewhere session-scoped to put them, and ordering the report last requires
pytest to run it last — both of which live here so the gate file stays a list of
gates.
"""

from __future__ import annotations

import pytest

#: ``(name, value, operator, bar, unit)`` per gate, in the order they ran.
_RESULTS: list[tuple[str, float, str, float, str]] = []


@pytest.fixture(scope="session")
def gate_results() -> list[tuple[str, float, str, float, str]]:
    """Everything recorded so far. Read by the report."""
    return _RESULTS


@pytest.fixture(scope="session")
def record_gate():
    """Record one measured number, its comparison, and its bar.

    Recording happens *before* the assertion in each gate, deliberately: a gate
    that fails must still appear in the report with the value that failed it. A
    recorder called after the assert would make the report show only passes.
    """

    def record(name: str, value: float | None, op: str, bar: float,
               unit: str = "") -> None:
        if value is None:
            return
        _RESULTS.append((name, float(value), op, float(bar), unit))

    return record


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run the gate report last, whatever order collection produced.

    Without this the report can run before the gates it reports on, print an
    empty table, and pass — a green test asserting nothing.
    """
    report = [i for i in items if i.name.startswith("test_the_gate_report")]
    if not report:
        return
    for item in report:
        items.remove(item)
    items.extend(report)
