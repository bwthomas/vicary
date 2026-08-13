"""Write measured latency figures into ``conformance/latency_baseline.json``.

The numbers this records must come from a run on the profile the file names —
GitHub's ubuntu-latest, on the pinned language version per port — because that
is the only place the gate will compare against them. A figure taken from a
laptop would set a baseline two to three times faster than the runner can
reproduce, and every subsequent release would fail a regression it did not have.
So this refuses to invent them: each value is passed in explicitly, from a CI
log, and the version they were measured for is recorded beside them.

    python tools/latency_baseline_record.py --version 0.2.4 \\
        --python 9.213 --typescript 1.048 --ruby 11.302

Read the diff before committing. A baseline that moved a long way since the last
release is either the improvement you meant or the regression this gate exists
to catch — and once it is written here, the gate compares against the new number
and stops asking about the old one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPOSITORY_ROOT / "conformance" / "latency_baseline.json"

PORTS = ("python", "typescript", "ruby")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True,
                        help="the release these figures were measured for")
    for port in PORTS:
        parser.add_argument(f"--{port}", type=float, default=None,
                            help=f"{port}'s pooled median, in ms, from the runner")
    parser.add_argument("--show", action="store_true",
                        help="print what is recorded now and write nothing")
    args = parser.parse_args(argv)

    doc = json.loads(BASELINE.read_text(encoding="utf-8"))

    if args.show:
        print(f"recorded_for_version: {doc.get('recorded_for_version')}")
        for port in PORTS:
            got = doc["implementations"][port]["pooled_median_ms"]
            print(f"  {port:<11} {got if got is not None else 'not recorded'}")
        return 0

    given = {port: getattr(args, port) for port in PORTS}
    supplied = {p: v for p, v in given.items() if v is not None}
    if not supplied:
        parser.error("give at least one port's figure, or --show")

    for port, value in supplied.items():
        if value <= 0:
            parser.error(f"--{port} {value} is not a positive number of ms")
        doc["implementations"][port]["pooled_median_ms"] = value

    # Named so a partial record cannot masquerade as a full one: a file whose
    # version says 0.2.4 while one port still holds 0.2.3's figure would compare
    # two releases against each other and call the difference a regression.
    missing = [p for p in PORTS
               if doc["implementations"][p]["pooled_median_ms"] is None]
    doc["recorded_for_version"] = args.version

    BASELINE.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    print(f"wrote {BASELINE.relative_to(REPOSITORY_ROOT)} for {args.version}")
    for port in PORTS:
        got = doc["implementations"][port]["pooled_median_ms"]
        mark = "  <- set" if port in supplied else ""
        print(f"  {port:<11} {got if got is not None else 'not recorded'}{mark}")
    if missing:
        print(f"\nSTILL UNRECORDED: {', '.join(missing)} — those ports report "
              f"NOT MEASURED until a figure is recorded for them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
