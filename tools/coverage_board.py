"""Print which concern each front door tests, as one board.

Why a board and not a total. The three front doors report 341, 303 and 203 tests,
and for a long time nothing in the repository could reconcile those numbers — so
the obvious reading, "Python is tested three times as well as Ruby", was available
to anybody who ran all three, and it was wrong. Most of the spread is granularity
(Python parametrizes 223 functions into 323 cases; the other two write them out),
some is scope (the reference carries an evaluation harness the ports do not), and
underneath that there really was a hole.

``tools/tests/test_coverage_parity.py`` is what *enforces* the shape; this only
prints it, and it deliberately prints CONCERNS rather than counts, for the reason
that file records: a count is not a measurement of coverage, and asserting a ratio
between two of them fails on a refactor that changed nothing.

    just coverage
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PORTS = ("python", "typescript", "ruby")

#: Column width for a port's cell. The longest suite path is
#: `tests/test_name_candidates.py` at 30.
WIDTH = 32


def board(coverage: dict) -> list[str]:
    reasons = {
        (entry["concern"], entry["port"]): entry["reason"]
        for entry in coverage["accepted_divergences"]
    }

    lines = [
        "  " + "concern".ljust(18) + "".join(port.ljust(WIDTH) for port in PORTS),
        "  " + "-" * (18 + WIDTH * len(PORTS)),
    ]

    covered = 0
    justified = 0
    for concern, ports in coverage["concerns"].items():
        cells = []
        complete = True
        for port in PORTS:
            suite = ports.get(port)
            if suite is not None:
                cells.append(suite.ljust(WIDTH))
                continue
            reason = reasons.get((concern, port), "")
            label = reason.split(".", 1)[0].strip() or "UNDECLARED"
            if label == "JUSTIFIED":
                justified += 1
                cells.append("— justified".ljust(WIDTH))
            else:
                complete = False
                cells.append(f"— {label}".ljust(WIDTH))
        covered += complete
        lines.append("  " + concern.ljust(18) + "".join(cells).rstrip())

    total = len(coverage["concerns"])
    lines.append("")
    lines.append(
        f"  -> {covered} of {total} concerns are tested in every port that needs "
        f"them; {justified} declared divergences, all justified."
    )
    # An unjustified cell is the state the parity suite fails on. Said here too,
    # because a board that only ever prints good news stops being read.
    unjustified = total - covered
    if unjustified:
        lines.append(
            f"  -> {unjustified} concern(s) carry an OPEN GAP — see conformance/"
            "coverage.json, and `just tools` fails until it is closed or declared."
        )
    return lines


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[1]
    document = json.loads((root / "conformance" / "coverage.json").read_text("utf-8"))
    print("  coverage by concern:")
    for line in board(document):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
