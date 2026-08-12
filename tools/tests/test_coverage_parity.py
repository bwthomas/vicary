"""Do the three front doors test the same things to the same depth?

Three implementations of one detector should be tested alike. They were not, and
nothing said so: the gap was found by counting test functions per file by hand,
which is not a thing anybody does twice. The Python front door appeared to have
twice the coverage of the other two, most of that difference turned out to be the
tools suite filed under it, and underneath that was a real hole — Ruby
carries the largest detector file in the repository and unit-tests none of it.

So the expected shape is declared in ``conformance/coverage.json`` and checked
here. What this buys, precisely:

* **A suite added to one port and not the others fails.** That is the drift this
  file exists to stop, and it is the direction drift actually goes: somebody
  fixes a bug in TypeScript, writes the test that proves it, and the other two
  ports keep the bug with every suite green.
* **Every gap carries a reason, and the reason says whether it is justified.**
  Python having no gazetteer *build* tests in its front door is justified — they
  moved to this suite. Ruby having no candidates tests is not; it is an open gap
  written down as one. A file that let the two read the same would be
  documentation, not a check.

What it deliberately does NOT do: compare test *counts*. A count is not a
measurement of coverage — Python's 156 collected candidate tests are 101 functions
parametrized, TypeScript's 77 are 77, and asserting a ratio between them would
fail on a refactor that changed nothing. The declared unit is a *concern* and
whether a port has a suite for it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vicary.eval import conformance

#: Where each port's suite paths are rooted, relative to the repository.
PORT_ROOTS = {"python": "python", "typescript": "typescript", "ruby": "ruby"}

DOCUMENT_VERSION = 1


@pytest.fixture(scope="module")
def repository_root() -> Path:
    directory = conformance.conformance_dir()
    assert directory is not None, "no conformance/ directory above this module"
    return directory.parent


@pytest.fixture(scope="module")
def coverage(repository_root: Path) -> dict:
    document = json.loads(
        (repository_root / "conformance" / "coverage.json").read_text("utf-8"))
    assert document["document_version"] == DOCUMENT_VERSION, (
        f"coverage.json is document_version {document['document_version']!r} and "
        f"this reader knows {DOCUMENT_VERSION}"
    )
    return document


def test_every_declared_suite_exists(coverage: dict, repository_root: Path) -> None:
    """A declared path that does not exist is worse than an undeclared gap.

    It reads as coverage on every scan of this file while testing nothing, which
    is exactly the state a renamed or deleted suite leaves behind.
    """
    missing = []
    for concern, ports in coverage["concerns"].items():
        for port, root in PORT_ROOTS.items():
            declared = ports.get(port)
            if declared is None:
                continue
            if not (repository_root / root / declared).is_file():
                missing.append(f"{concern}/{port}: {root}/{declared}")
    assert not missing, (
        "coverage.json points at suites that are not on disk, so it claims "
        f"coverage nothing provides: {missing}"
    )


#: Where each port keeps its unit tests, and what a suite file is called there.
PORT_SUITES = {
    "python": ("tests", "test_*.py"),
    "typescript": ("test", "*.test.ts"),
    "ruby": ("test", "*_test.rb"),
}

#: Suite files that are deliberately not a declared concern of their own.
#: Keep this SHORT — an entry here is an exemption from the check below, and the
#: reason it is a tuple of explicit names rather than a glob is that a glob would
#: quietly absorb the next undeclared suite too.
UNDECLARED_BY_DESIGN: dict[str, frozenset[str]] = {
    "python": frozenset(),
    "typescript": frozenset(),
    "ruby": frozenset(),
}


def test_every_suite_on_disk_is_a_declared_concern(
    coverage: dict, repository_root: Path
) -> None:
    """The other direction, and the one this file spent its first weeks missing.

    ``test_every_gap_is_declared_with_a_reason`` walks the *declared* concerns and
    asks which ports lack a suite. That cannot see a suite which exists on disk
    under no concern at all — and a suite nothing declares is precisely how a
    concern ends up tested in one port only, because the file that is supposed to
    notice has never heard of it.

    It was not hypothetical. ``python/tests/test_local_classifier.py`` sat here for
    weeks — 41 collected tests, no declared concern, and this suite green the whole
    time. It turned out to be justified (the module is the reference evaluation
    harness's, not the detector's), but nothing had ever *asked*, and the headline
    claim at the top of this file — "a suite added to one port and not the others
    fails" — was only true of concerns already listed.
    """
    declared: dict[str, set[str]] = {port: set() for port in PORT_ROOTS}
    for ports in coverage["concerns"].values():
        for port in PORT_ROOTS:
            path = ports.get(port)
            if path is not None:
                declared[port].add(path)

    undeclared = []
    for port, root in PORT_ROOTS.items():
        directory, pattern = PORT_SUITES[port]
        for found in sorted((repository_root / root / directory).glob(pattern)):
            relative = f"{directory}/{found.name}"
            if relative in declared[port]:
                continue
            if found.name in UNDECLARED_BY_DESIGN[port]:
                continue
            undeclared.append(f"{port}: {relative}")

    assert not undeclared, (
        "these suite files exist but no concern in coverage.json declares them, so "
        "this file cannot tell whether the other two ports are missing an "
        "equivalent. Declare the concern (and give the other ports a suite or an "
        f"accepted_divergences entry): {undeclared}"
    )


def test_every_gap_is_declared_with_a_reason(coverage: dict) -> None:
    """The load-bearing assertion, and the one that catches drift.

    A port with no suite for a concern must appear in ``accepted_divergences``.
    Adding a suite to one port and not the others therefore fails here until
    somebody either writes the other two or writes down why not — which is the
    decision that was previously never made explicitly.
    """
    declared_gaps = {
        (entry["concern"], entry["port"])
        for entry in coverage["accepted_divergences"]
    }
    undeclared = [
        f"{concern}/{port}"
        for concern, ports in coverage["concerns"].items()
        for port in PORT_ROOTS
        if ports.get(port) is None and (concern, port) not in declared_gaps
    ]
    assert not undeclared, (
        "these ports have no suite for a concern and no recorded reason. Either "
        "write the suite, or add an accepted_divergences entry saying why this "
        f"port does not need one: {undeclared}"
    )


def test_no_divergence_is_recorded_for_a_port_that_has_the_suite(
    coverage: dict,
) -> None:
    """An exemption going stale IS the pass.

    The same rule the gate suite applies to ``ACCEPTED_VIOLATIONS``: once a port
    grows the suite it was excused from, the excuse has to go, or the next real
    gap in that concern hides behind it.
    """
    stale = [
        f"{entry['concern']}/{entry['port']}"
        for entry in coverage["accepted_divergences"]
        if coverage["concerns"][entry["concern"]].get(entry["port"]) is not None
    ]
    assert not stale, (
        "these divergences are recorded for a port that now HAS the suite — "
        f"delete the entry, or the next gap in it will be sheltered: {stale}"
    )


def test_every_divergence_names_a_concern_and_a_port_that_exist(
    coverage: dict,
) -> None:
    """A typo in either field is an exemption that shelters nothing and hides
    that fact, which is how a list like this rots."""
    for entry in coverage["accepted_divergences"]:
        assert entry["concern"] in coverage["concerns"], entry["concern"]
        assert entry["port"] in PORT_ROOTS, entry["port"]
        assert entry["reason"].strip(), (
            f"{entry['concern']}/{entry['port']} has an empty reason"
        )


def test_an_open_gap_is_labelled_as_one(coverage: dict) -> None:
    """Justified and unjustified divergences must be told apart in the text.

    Without this the file drifts toward reading as though every gap were
    considered and accepted — the state it exists to prevent. Each reason has to
    open by saying which it is, so a reader scanning for real work finds it.
    """
    prefixes = ("OPEN GAP", "JUSTIFIED", "PARTIALLY JUSTIFIED")
    unlabelled = [
        f"{entry['concern']}/{entry['port']}"
        for entry in coverage["accepted_divergences"]
        if not entry["reason"].lstrip().startswith(prefixes)
    ]
    assert not unlabelled, (
        f"these reasons do not open with one of {prefixes}, so a reader cannot "
        f"tell a decision from a to-do: {unlabelled}"
    )


def test_the_open_gaps_are_the_ones_currently_known(coverage: dict) -> None:
    """Pins the open gaps as an exact set, so closing one is visible and opening
    a new one is deliberate.

    A count would let a new gap in by displacing a closed one, the same reason
    the gate suite pins its accepted violations as a set rather than a ceiling.
    """
    open_gaps = {
        (entry["concern"], entry["port"])
        for entry in coverage["accepted_divergences"]
        if entry["reason"].lstrip().startswith(("OPEN GAP", "PARTIALLY JUSTIFIED"))
    }
    assert open_gaps == set(), (
        "the open coverage gaps moved. This set was emptied on 2026-08-12, when the "
        "last four — candidates/ruby, redact/ruby, packaging/typescript and "
        "dialect/typescript — and the two PARTIALLY JUSTIFIED config entries were "
        "closed by writing the suites. Every remaining divergence is JUSTIFIED, "
        "meaning a decision rather than a to-do. If you opened one, that is what "
        f"this test is for: {sorted(open_gaps)}"
    )
