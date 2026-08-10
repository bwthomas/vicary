"""Every source file has to be *in the repository*, not merely on this disk.

Why this test exists, precisely. `.gitignore` carried an unanchored ``build/``
for the setuptools output directory. That pattern also matches
``src/vicary/build/`` — the gazetteer builder — so the builder was never
committed, through seventeen commits and the first public push. Nothing local
could see it: the files are on disk, imports resolve, the suite passes, and a
locally built wheel even carries them, because setuptools reads the working
tree rather than the index.

What it actually costs, in a package that ships an offline asset: a release
built from a clean checkout has no ``vicary.build``, so ``vicary-assets fetch``
— the only way to rebuild the gazetteer — raises ImportError for whoever
installs it. The asset-integrity check passes the whole time, because the asset
is fine; it is the thing that regenerates it that is missing.

CI on a fresh checkout catches this as a side effect (imports fail, and ruff
reclassifies the missing package as third-party). That is a coincidence, and a
coincidence is not a guard, so the property gets asserted directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _is_a_git_checkout() -> bool:
    return (REPO_ROOT / ".git").exists()


@pytest.mark.skipif(
    not _is_a_git_checkout(),
    reason="not a git checkout — nothing to compare the working tree against",
)
def test_every_source_file_is_tracked() -> None:
    tracked = {
        REPO_ROOT / line
        for line in _git("ls-files", "src", "tests").splitlines()
        if line.endswith(".py")
    }
    on_disk = {
        path
        for path in (REPO_ROOT / "src").rglob("*.py")
        if "__pycache__" not in path.parts and ".egg-info" not in str(path)
    } | {
        path
        for path in (REPO_ROOT / "tests").rglob("*.py")
        if "__pycache__" not in path.parts
    }

    missing = sorted(str(p.relative_to(REPO_ROOT)) for p in on_disk - tracked)
    assert not missing, (
        "these source files are on disk but not in the repository, so a clean "
        "checkout does not have them and neither does a release built from "
        "one: " + ", ".join(missing) + ". Check .gitignore for an unanchored "
        "pattern — `build/` matches src/vicary/build/, `dist/` would match a "
        "package called dist, and so on."
    )
