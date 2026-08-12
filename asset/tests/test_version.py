"""One detector, one number.

Six files declare the version: the repository's ``VERSION``, and five restatements
that exist because each is read where the repository root is not — an installed
wheel, gem or npm tarball, or a build backend running before any of our code does.
A gem 0.3.0 corresponding to nothing on PyPI cannot be reasoned about, and the
parity claim is between *versions*, not between package names — so a drift between
any two of these is a claim nobody can check.

Each release workflow already asserts that its tag equals its own package's
version. That catches a mistyped tag; it cannot catch three packages agreeing with
their own tags and disagreeing with each other. This does.

``tools/version_sync.py`` (``just version``) is what WRITES the five from
``VERSION``, so they are no longer five hand-edits. This deliberately does not
import its table and re-run its matcher: a shared list means one wrong pattern
writes a file and then agrees with itself about it. These are hand-written
patterns against the same files, which is the only version of this that can catch
the writer being wrong.
"""

from __future__ import annotations

import json
import re

import pytest

from vicary_build import config

REPO_ROOT = config.REPO_ROOT


def test_the_repository_declares_a_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", config.version())


def test_the_python_package_agrees() -> None:
    text = (REPO_ROOT / "python" / "src" / "vicary" / "_version.py").read_text(
        encoding="utf-8"
    )
    found = re.search(r'__version__ = "([^"]+)"', text)
    assert found and found.group(1) == config.version()


def test_the_npm_package_agrees() -> None:
    manifest_path = REPO_ROOT / "typescript" / "package.json"
    if not manifest_path.exists():
        pytest.skip("no typescript front door in this tree")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["version"] == config.version()


def test_the_typescript_module_agrees() -> None:
    """The number npm publishes and the number the module exports are two files.

    `typescript/test/asset.test.ts` checks this from the TypeScript side, and had
    to: package.json, the root VERSION, the gem and the wheel once all said 0.2.0
    while this file said something else. Checked from here too, because the
    TypeScript suite does not run on a checkout with no node, and this is the
    suite that owns "all six agree".
    """
    version_ts = REPO_ROOT / "typescript" / "src" / "version.ts"
    if not version_ts.exists():
        pytest.skip("no typescript front door in this tree")
    found = re.search(
        r'VERSION = "([^"]+)"', version_ts.read_text(encoding="utf-8")
    )
    assert found and found.group(1) == config.version()


def test_the_gem_agrees() -> None:
    version_rb = REPO_ROOT / "ruby" / "lib" / "vicary" / "version.rb"
    if not version_rb.exists():
        pytest.skip("no ruby front door in this tree")
    found = re.search(r'VERSION = "([^"]+)"', version_rb.read_text(encoding="utf-8"))
    assert found and found.group(1) == config.version()


def test_the_build_tool_agrees() -> None:
    """It stamps the version into the asset metadata and the User-Agent, so a
    stale number here mislabels a cut."""
    text = (REPO_ROOT / "asset" / "pyproject.toml").read_text(encoding="utf-8")
    found = re.search(r'^version = "([^"]+)"', text, re.M)
    assert found and found.group(1) == config.version()
