"""Reference tables the build reads in order to *subtract* from what it emits.

Neither table ships. Both answer the same question — "is this string something a
real person is called" — and both are consulted here in one direction only: a
form this build would otherwise write into a word list is dropped when a table
says a person bears it.

Which way each read fails is the thing to hold onto. A **short** read of either
table vetoes too little, so more forms land in the stoplist, so fewer capitalised
tokens become name candidates, so the redactor masks **less**. That is a recall
regression with no error and no diff anybody reads — the same asymmetry the
lexicon's own declared count exists to catch, pointing the same way. So both
readers assert what they parsed rather than trusting it, and both raise rather
than degrading.

The tables are read from the repository, never from an operator's environment.
What they gate is a **tracked, generated** artifact, so a build on one machine
has to produce the byte-identical file a build on another does; an operator's
newer census release would silently make one checkout's regeneration a diff.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from vicary_build import config

#: Filenames inside ``conformance/census/``. Kept in step with
#: :mod:`vicary.eval.census`, which reads the same two files for the exposure
#: gate; a pin test compares the parsed tables.
CENSUS_TABLE_FILENAME = "surnames.txt.gz"
CENSUS_PROFILE_FILENAME = "profile.json"

#: Row floor for the census table, matching the builder's own and the eval's.
#: The 2010 release carries ~162k surnames.
CENSUS_MINIMUM_ROWS = 100_000

#: The gazetteer tier naming first names lots of people share.
GIVEN_TIER = "given"


class ReferenceError(RuntimeError):
    """A reference table is missing, unreadable, or not the size it declares."""


def borne_surnames(directory: Path | None = None) -> frozenset[str]:
    """Every American surname borne by 100 or more people at the 2010 census.

    The digest pinned in ``profile.json`` is checked rather than trusted, for the
    reason in this module's docstring: an edited or truncated table vetoes fewer
    forms and the effect is invisible.
    """
    found = directory or config.CENSUS_DIR
    table = found / CENSUS_TABLE_FILENAME
    profile = found / CENSUS_PROFILE_FILENAME
    if not table.is_file() or not profile.is_file():
        raise ReferenceError(
            f"no census table at {found}. It is tracked in this repository; "
            "outside a checkout there is nothing to veto against, and a build "
            "that skipped the veto would write borne surnames into the stoplist."
        )

    payload = table.read_bytes()
    expected = (
        json.loads(profile.read_text(encoding="utf-8"))
        .get("table", {})
        .get("sha256", "")
    )
    actual = hashlib.sha256(payload).hexdigest()
    if expected and actual != expected:
        raise ReferenceError(
            f"{table} has sha256 {actual}, but {CENSUS_PROFILE_FILENAME} pins "
            f"{expected}. Refusing to generate a word list against a table that "
            "is not the one this repository measured."
        )

    names: set[str] = set()
    for line in gzip.decompress(payload).decode("utf-8").splitlines():
        name, _, _bearers = line.partition("\t")
        if name:
            names.add(name)
    if len(names) < CENSUS_MINIMUM_ROWS:
        raise ReferenceError(
            f"{table} parsed to only {len(names):,} surnames; expected at least "
            f"{CENSUS_MINIMUM_ROWS:,}."
        )
    return frozenset(names)


def common_given_names(path: Path | None = None) -> frozenset[str]:
    """The built gazetteer's ``given`` tier, read off the asset on disk.

    Read from the artifact rather than rebuilt from the SSA archive because the
    archive is not fetchable (``ssa.gov`` answers some networks with an Akamai
    403) and because the tier that matters is the one the detectors will actually
    load — regenerating a word list against a *different* given-name population
    than the shipped gazetteer carries is how the two disagree about one token.
    """
    target = path or (config.DATA_DIR / "notability.txt.gz")
    if not target.is_file():
        raise ReferenceError(
            f"no gazetteer at {target}; run `just asset-fetch` first. Without "
            "the `given` tier this build cannot tell a generated plural from a "
            "child's first name, and a stop word wins over the given-name tier."
        )

    declared: int | None = None
    entries: set[str] = set()
    in_tier = False
    with gzip.open(target, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#!"):
                head, _, rest = line[2:].rstrip("\n").partition(" ")
                if head != "tier":
                    continue
                tier, _, count = rest.partition(" ")
                in_tier = tier == GIVEN_TIER
                if in_tier:
                    declared = int(count)
                continue
            if in_tier:
                token = line.strip()
                if token:
                    entries.add(token)

    if declared is None:
        raise ReferenceError(f"{target} declares no `{GIVEN_TIER}` tier")
    if len(entries) != declared:
        raise ReferenceError(
            f"{target}: `{GIVEN_TIER}` tier declares {declared} entries, parsed "
            f"{len(entries)}."
        )
    return frozenset(entries)


def veto(census_dir: Path | None = None, gazetteer: Path | None = None
         ) -> frozenset[str]:
    """Every string a generated word form must not be: borne surname or given name.

    One set rather than two arguments at the call site, because the two tables
    are never usefully consulted apart — a form is safe to emit only when *no*
    table says somebody is called it.
    """
    return borne_surnames(census_dir) | common_given_names(gazetteer)
