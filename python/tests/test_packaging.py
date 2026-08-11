"""Every source file has to be *in the repository*, not merely on this disk.

Why this test exists, precisely. `.gitignore` carried an unanchored ``build/``
for the setuptools output directory. That pattern also matches
``src/vicary/build/`` — the gazetteer builder — so the builder was never
committed, through seventeen commits and the first public push. Nothing local
could see it: the files are on disk, imports resolve, the suite passes, and a
locally built wheel even carries them, because setuptools reads the working
tree rather than the index.

What it actually cost, in a package that ships an offline asset: a release built
from a clean checkout had no builder at all, so the only way to rebuild the
gazetteer raised ImportError for whoever installed it. The asset-integrity check
passed the whole time, because the asset was fine; it was the thing that
regenerates it that was missing.

CI on a fresh checkout catches this as a side effect (imports fail, and ruff
reclassifies the missing package as third-party). That is a coincidence, and a
coincidence is not a guard, so the property gets asserted directly.

The second test here is the same defect class from the other direction. The
gazetteer and the stoplist are now *deliberately* untracked in this package —
they are vendored from the repository's ``asset/`` by ``just asset-sync``, so that
no one of three front doors owns the shared input. The cost of that symmetry is
that a wheel built in a tree where the sync never ran installs cleanly and loads
nothing, which means redacting every public figure in every essay. So the files
are asserted present on disk, where the tracking test cannot look for them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

#: The Python package's own root — where ``pyproject.toml``, ``src`` and ``tests``
#: live. Since the monorepo move this is ``<repo>/python``, not the repo root.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=PACKAGE_ROOT, capture_output=True, text=True, check=True
    ).stdout


def _is_a_git_checkout() -> bool:
    """True when this tree is inside a git working copy.

    Asked of git rather than by looking for a ``.git`` directory beside this
    package. The monorepo move put the package one level down, where no ``.git``
    exists — and a probe that answers "not a checkout" turns the skipif below
    into an unconditional skip. The test would then have gone green, in CI, for
    the exact defect it was written to catch.
    """
    try:
        return subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=PACKAGE_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip() == "true"
    except (OSError, subprocess.CalledProcessError):
        return False


@pytest.mark.skipif(
    not _is_a_git_checkout(),
    reason="not a git checkout — nothing to compare the working tree against",
)
def test_every_source_file_is_tracked() -> None:
    tracked = {
        PACKAGE_ROOT / line
        for line in _git("ls-files", "src", "tests").splitlines()
        if line.endswith(".py")
    }
    on_disk = {
        path
        for path in (PACKAGE_ROOT / "src").rglob("*.py")
        if "__pycache__" not in path.parts and ".egg-info" not in str(path)
    } | {
        path
        for path in (PACKAGE_ROOT / "tests").rglob("*.py")
        if "__pycache__" not in path.parts
    }

    missing = sorted(str(p.relative_to(PACKAGE_ROOT)) for p in on_disk - tracked)
    assert not missing, (
        "these source files are on disk but not in the repository, so a clean "
        "checkout does not have them and neither does a release built from "
        "one: " + ", ".join(missing) + ". Check .gitignore for an unanchored "
        "pattern — `build/` matches src/vicary/build/, `dist/` would match a "
        "package called dist, and so on."
    )


#: What the wheel must carry. Not globbed: a glob that matched nothing would pass,
#: and "no gazetteer" is exactly the state this asserts against.
REQUIRED_PACKAGE_DATA = (
    "data/notability.txt.gz",
    "data/MANIFEST.json",
    "data/stop_words.txt",
)


def test_the_vendored_asset_is_present_for_a_build() -> None:
    """The other half of the tracking question, for files that are not tracked.

    `.gitignore` keeps ``src/vicary/data/`` out of the index on purpose, so
    ``git ls-files`` can say nothing about it and the test above cannot cover it.
    setuptools reads the working tree, which is what makes this the right question:
    is the payload on disk *right now*, at the moment a wheel would be built.
    """
    missing = [
        name
        for name in REQUIRED_PACKAGE_DATA
        if not (PACKAGE_ROOT / "src" / "vicary" / name).is_file()
    ]
    assert not missing, (
        "the vendored asset payload is incomplete: "
        + ", ".join(missing)
        + ". Run `just asset-sync` (or `just py-setup`). A wheel built now would "
        "install cleanly and load an empty gazetteer, which means masking every "
        "public figure in every essay — privacy-safe, product-hostile, and "
        "indistinguishable from over-tuning until somebody reads the output."
    )


def test_the_vendored_asset_matches_the_repositorys_copy() -> None:
    """A stale vendored copy is worse than a missing one: it loads.

    Two front doors shipping different gazetteers is the failure this whole
    arrangement exists to prevent, and it starts as one package's ``data/`` being
    a cut behind.
    """
    if not _is_a_git_checkout():
        pytest.skip("not a checkout — nothing to compare the vendored copy against")
    from vicary_build import config as build_config
    from vicary_build import manifest as build_manifest

    from vicary import assets

    for name in ("notability.txt.gz", "MANIFEST.json"):
        canonical = build_config.DATA_DIR / name
        assert build_manifest.sha256_of(canonical) == assets.sha256_of(
            PACKAGE_ROOT / "src" / "vicary" / "data" / name
        ), f"vendored {name} is stale — run `just asset-sync`"
