"""Write the repository's version into every file that has to restate it.

One detector, one number — but five files must carry that number literally,
because each is read in a place the repository root is not present:

* ``python/src/vicary/_version.py`` and ``ruby/lib/vicary/version.rb`` and
  ``typescript/src/version.ts`` are read from an INSTALLED package, where
  ``../../VERSION`` does not exist. A host asking the module its version cannot
  be answered by a file that shipped in no wheel, gem or tarball.
* ``typescript/package.json`` and ``asset/pyproject.toml`` are read by build
  backends before any of our code runs, and both want a static string.

So the number cannot be *read* from one place at runtime; it can only be
*written* to five from one place at release time. That is what this does.
``asset/tests/test_version.py`` is the other half — it fails when any of them
drifts, which is what makes hand-editing one file and forgetting another a
failing build rather than a published mismatch.

    just version 0.3.0     # set VERSION, then rewrite all five
    just version           # rewrite all five from the current VERSION
    python tools/version_sync.py --check

``python/pyproject.toml`` is deliberately absent from the list: it declares
``dynamic = ["version"]`` and reads ``_version.py``, so it never restates the
number and never can drift.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: A release version, and nothing looser. A tag is cut from this string, three
#: registries reject what they disagree with, and `0.3` or `0.3.0-dev` would each
#: be accepted by some subset of the five files below and rejected by the rest.
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")


@dataclass(frozen=True)
class Declaration:
    """One file's restatement of the version, and how to find it."""

    #: Repository-relative path.
    path: str
    #: Matches the whole declaration with the version as group 1. Anchored on the
    #: surrounding syntax, never on the old version — a pattern that matched the
    #: number alone would rewrite any other 1.2.3 in the file.
    pattern: re.Pattern[str]
    #: Why this file has to restate it, for the failure message.
    why: str
    #: False for a front door that may not be present in a partial checkout.
    required: bool = True


DECLARATIONS: tuple[Declaration, ...] = (
    Declaration(
        "python/src/vicary/_version.py",
        re.compile(r'(?<=^__version__ = ")([^"]+)(?="$)', re.M),
        "read from the installed wheel, where the repository root is absent",
    ),
    Declaration(
        "asset/pyproject.toml",
        re.compile(r'(?<=^version = ")([^"]+)(?="$)', re.M),
        "stamped into the asset manifest and the build User-Agent",
    ),
    Declaration(
        "typescript/package.json",
        re.compile(r'(?<=^  "version": ")([^"]+)(?=",$)', re.M),
        "read by npm before any of our code runs",
        required=False,
    ),
    Declaration(
        "typescript/src/version.ts",
        re.compile(r'(?<=^export const VERSION = ")([^"]+)(?=";$)', re.M),
        "read from the installed package, which ships no VERSION file",
        required=False,
    ),
    Declaration(
        "ruby/lib/vicary/version.rb",
        re.compile(r'(?<=^  VERSION = ")([^"]+)(?="$)', re.M),
        "read from the installed gem, which ships no VERSION file",
        required=False,
    ),
)


def read_version() -> str:
    """The repository's one number."""
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def write_version(version: str) -> None:
    (REPO_ROOT / "VERSION").write_text(version + "\n", encoding="utf-8")


def _declared(declaration: Declaration) -> tuple[Path, str | None]:
    """``(path, declared version)``, with ``None`` for an absent front door."""
    path = REPO_ROOT / declaration.path
    if not path.exists():
        if declaration.required:
            raise FileNotFoundError(
                f"{declaration.path} is missing — it {declaration.why}"
            )
        return path, None
    found = declaration.pattern.search(path.read_text(encoding="utf-8"))
    if not found:
        raise ValueError(
            f"{declaration.path} declares no version this can find. Its "
            f"declaration is what {declaration.why}, so a silent skip here is a "
            "file that stops being synced and stops being checked at once."
        )
    return path, found.group(1)


def sync(version: str) -> list[str]:
    """Rewrite every declaration to ``version``. Returns the paths changed."""
    changed: list[str] = []
    for declaration in DECLARATIONS:
        path, declared = _declared(declaration)
        if declared is None or declared == version:
            continue
        text = path.read_text(encoding="utf-8")
        path.write_text(declaration.pattern.sub(version, text), encoding="utf-8")
        changed.append(f"{declaration.path}  {declared} -> {version}")
    return changed


def check(version: str) -> list[str]:
    """Every declaration that disagrees with ``version``."""
    drifted: list[str] = []
    for declaration in DECLARATIONS:
        _, declared = _declared(declaration)
        if declared is not None and declared != version:
            drifted.append(f"{declaration.path}  declares {declared}, not {version}")
    return drifted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/version_sync.py",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
    )
    parser.add_argument(
        "version",
        nargs="?",
        help="the new version; omit to re-sync from the current VERSION file",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit non-zero, writing nothing",
    )
    args = parser.parse_args(argv)

    if args.version and args.check:
        parser.error("--check verifies the committed tree; it takes no version")
    if args.version and not VERSION_PATTERN.fullmatch(args.version):
        parser.error(f"{args.version!r} is not a MAJOR.MINOR.PATCH release version")

    if args.version:
        write_version(args.version)
    version = read_version()
    if not VERSION_PATTERN.fullmatch(version):
        print(f"VERSION holds {version!r}, not a MAJOR.MINOR.PATCH release version",
              file=sys.stderr)
        return 1

    if args.check:
        drifted = check(version)
        for line in drifted:
            print(line, file=sys.stderr)
        if drifted:
            print("run `just version` to rewrite them from VERSION", file=sys.stderr)
            return 1
        print(f"all declarations agree on {version}")
        return 0

    changed = sync(version)
    for line in changed:
        print(line)
    print(f"{len(changed)} rewritten; every declaration now reads {version}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
