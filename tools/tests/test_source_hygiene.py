"""Repository-wide properties of the source itself, rather than of what it does.

One test, and it earns its place from a real cost. ``typescript/src/minter.ts``
carried three raw NUL bytes — the separator in its ``kind\\u0000original`` cache
key, written as a literal rather than as an escape. Nothing was wrong with the
code. What was wrong was that a single NUL makes a file **binary to every
line-oriented tool that touches it**:

* ``grep`` prints ``Binary file typescript/src/minter.ts matches`` and gives up,
  so a search for a symbol defined in that file silently returns nothing. That
  wasted real time — a function was declared missing from the repository twice
  before ``file(1)`` explained why.
* ``git diff`` and ``git log -p`` refuse to show the contents, so every change to
  that file was unreviewable in a terminal.
* Web review tools and editors render the byte as blank, so the separator looks
  like a space and reads as a bug in the key format.

The fix in the file was to write ``\\u0000``, which compiles to exactly the same
character. The fix *here* is what stops it coming back, in any of the four
languages, next time somebody reaches for a control character as a delimiter.

Deliberately narrow: this is not a linter. Each of the three ports already has
one, and the properties they check are language-specific. What belongs here is the
small set of things that are true of the whole repository and that no per-language
linter is looking at.
"""

from __future__ import annotations

import subprocess

import pytest

from vicary.eval import conformance

#: Tracked paths allowed to contain bytes no text tool can read. The gazetteer is
#: gzip and there is nothing else — listed by exact path rather than by extension
#: so a new binary has to be added here on purpose.
BINARY_BY_DESIGN = frozenset({"asset/data/notability.txt.gz"})

#: Control characters that have no business in source. Tab is absent on purpose:
#: it is a formatting argument, the per-language linters already have opinions
#: about it, and it does not break any tool. These do.
FORBIDDEN = {
    0x00: r"NUL — write it as \0 or \x00; a literal one makes the file "
          "binary to grep, git diff and every review tool",
    0x0B: r"vertical tab — write it as \v",
    0x0C: r"form feed — write it as \f",
    0x1A: r"substitute (Ctrl-Z) — usually a stray paste; delete it",
}


@pytest.fixture(scope="module")
def tracked_files() -> list[str]:
    """Every path git tracks, from git rather than from a directory walk.

    A walk would have to reimplement `.gitignore` and would pick up `node_modules`
    and three `dist/` trees, which are not ours to hold to this.
    """
    directory = conformance.conformance_dir()
    assert directory is not None, "no conformance/ directory above this module"
    root = directory.parent
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, check=True,
    ).stdout
    return [
        f"{root}/{name}"
        for name in listed.decode("utf-8").split("\0")
        if name
    ]


def test_no_tracked_source_file_contains_a_forbidden_control_byte(
    tracked_files: list[str],
) -> None:
    """The one that pays for this file.

    Reports every offending byte with its line, because "some file has a NUL in
    it" is the part that is easy to work out and the location is the part that is
    not — the byte is invisible in every tool that would show you.
    """
    directory = conformance.conformance_dir()
    assert directory is not None
    root = str(directory.parent) + "/"

    problems = []
    for path in tracked_files:
        relative = path.removeprefix(root)
        if relative in BINARY_BY_DESIGN:
            continue
        with open(path, "rb") as handle:
            data = handle.read()
        for byte, why in FORBIDDEN.items():
            if bytes([byte]) not in data:
                continue
            line = data[: data.index(bytes([byte]))].count(b"\n") + 1
            problems.append(
                f"{relative}:{line} contains {data.count(bytes([byte]))} "
                f"0x{byte:02X} byte(s) — {why}"
            )
    assert not problems, "\n".join(problems)


def test_the_binary_allowlist_is_not_stale(tracked_files: list[str]) -> None:
    """An allowlist entry for a file that is gone, or has become text, hides the
    next real one behind it — the same rule the gate suite applies to its accepted
    violations."""
    directory = conformance.conformance_dir()
    assert directory is not None
    root = str(directory.parent) + "/"
    tracked = {path.removeprefix(root) for path in tracked_files}

    missing = sorted(BINARY_BY_DESIGN - tracked)
    assert not missing, (
        f"these paths are allowed to be binary and are no longer tracked: {missing}"
    )
    still_binary = []
    for relative in sorted(BINARY_BY_DESIGN):
        with open(root + relative, "rb") as handle:
            if b"\x00" in handle.read():
                still_binary.append(relative)
    assert still_binary == sorted(BINARY_BY_DESIGN), (
        "these paths are exempted as binary but contain no NUL, so the exemption "
        f"is doing nothing: {sorted(set(BINARY_BY_DESIGN) - set(still_binary))}"
    )


def test_every_tracked_file_decodes_as_utf8(tracked_files: list[str]) -> None:
    """A latin-1 byte in a source file is the same class of defect one step down:
    it does not stop grep, but it does make the file's meaning depend on the
    reader's locale, and the three ports fold accented names for a living."""
    undecodable = []
    directory = conformance.conformance_dir()
    assert directory is not None
    root = str(directory.parent) + "/"
    for path in tracked_files:
        relative = path.removeprefix(root)
        if relative in BINARY_BY_DESIGN:
            continue
        with open(path, "rb") as handle:
            data = handle.read()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            undecodable.append(f"{relative}: {error}")
    assert not undecodable, "\n".join(undecodable)
