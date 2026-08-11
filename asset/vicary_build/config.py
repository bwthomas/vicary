"""Build-time configuration, owned by the build tool rather than any front door.

This used to be read through ``vicary.config``, which made the builder import the
Python library — one of the three consumers of what it produces. A tool that
depends on one of its consumers cannot honestly be called shared, and the coupling
had a second cost: an environment variable read only at build time was documented
in the runtime library's configuration surface, where a host integrating the
library would find it and reasonably expect it to matter.

The variable *names* are unchanged, deliberately. They are what an operator has in
a shell profile, and renaming them to tidy an internal boundary would break a
rebuild for no gain the operator can see.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repository root — the directory holding ``VERSION``, ``asset/`` and the three
#: package directories. Resolved from this file rather than the working directory,
#: so a build launched from anywhere writes to the same place.
REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent

#: Where the built asset lands: one canonical tracked copy that every front door
#: vendors from. Not inside any package — see ``asset/README.md``.
DATA_DIR: Path = REPO_ROOT / "asset" / "data"

#: Language-neutral word lists the build reads. Authored, not fetched, and
#: mirrored by an in-language literal in each front door that a test pins.
LEXICON_DIR: Path = REPO_ROOT / "asset" / "lexicon"

#: Local copy of the SSA baby-names archive (``names.zip``), the source of the
#: ``given`` tier.
#:
#: A local path rather than a download because ``ssa.gov`` returns an Akamai HTTP
#: 403 to some networks on *every* path including the site root — verified 403 for
#: this one with a plain UA, a browser UA, a cookie jar and a referer, while
#: ``www2.census.gov`` and ``query.wikidata.org`` answered normally in the same
#: run. So the builder cannot assume it can fetch this file, and a build that
#: silently proceeded without it would ship a tier built from the wrong population
#: under a format number promising the right one.
SSA_NAMES_ZIP_ENV_VAR: str = "VICARY_BUILD_SSA_NAMES_ZIP"

#: The US Census surname file, when a local copy is preferred to the download.
#: Shared with the evaluation harness, which reads the same file for coverage
#: reporting, so the name is the one an operator already exports.
CENSUS_CSV_ENV_VAR: str = "VICARY_EVAL_CENSUS_CSV"


def get(name: str, default: str = "") -> str:
    """Resolve one configured value, stripped, or ``default``.

    No legacy-name or host-fallback chain, unlike the library's ``config.get``.
    Those exist because a *deployment* names things its own way; a build is run by
    whoever is cutting an asset, from a documented command.
    """
    return (os.environ.get(name) or "").strip() or default


def version() -> str:
    """The one version number all three front doors share.

    Read from the repository's ``VERSION`` file rather than from any package's
    manifest, because "one detector, one number" cannot be true if the number
    lives inside one of the three implementations and the other two copy it.
    """
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
