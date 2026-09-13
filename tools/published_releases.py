"""Which releases a registry is actually serving — the thing a tag only claims.

The latency pair compares this checkout against "the last release". Until now it
read that phrase out of ``git tag``, and a tag is the system under measurement
describing itself: it records that someone *started* a release, never that one
finished. This repository has the counter-example twice over.

* ``v0.2.10`` is tagged and published nowhere. ``9eaa538`` cut 0.2.11 for exactly
  that reason and said so in its subject.
* ``v0.2.13`` is tagged, merged into ``main``, and published nowhere either — all
  three release workflows failed the latency gate on it. Measured against that
  tag, a 0.2.14 that fixed nothing reads −3.60% / −14.75% / −10.49% and passes
  every port, while the regression against what users actually have (0.2.12)
  ships unremarked.

So the baseline has to come from outside our own git state, and the three
registries are the only witnesses that qualify. The two scripts this one joins —
``typescript/scripts/registry-serves.mjs`` and ``ruby/scripts/registry_serves.rb``
— already ask that question at publish time, for the same reason and with the
same tri-state: ``versions is None`` is "I could not tell", ``versions == ()`` is
"the registry has never heard of this package", and the second must never read as
the first. This is the read side of it, in the port the pair driver is written
in, and it keeps the fetch separate from the parse so every payload shape below
is checked by a test rather than by a release.

**Per port, not pooled.** Each port's users install from one registry, and the
registries disagree: PyPI has never served 0.2.11, npm has never served 0.2.6,
and RubyGems refused 0.2.3 on the same commit PyPI and npm took. "The last
release" is therefore a different version depending on which library is being
timed, and answering it per port is what makes the gate's question — is this
slower than what a user of THIS port has — the question it claims to ask.

**Offline is a refusal, not a fallback.** A lookup that fails leaves the driver
with no baseline it can defend, and falling back to the newest tag is precisely
the defect above. The named override exists for the box with no network:

    VICARY_PUBLISHED_VERSIONS_PYTHON="0.2.12 0.2.8"   # this port only
    VICARY_PUBLISHED_VERSIONS="0.2.12"                # all ports

which is an operator asserting a fact out loud, and is reported as such in the
pair record's ``source``. An unset variable and an unreachable registry are both
"I could not tell", and the driver stops there.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

#: Per-port override, then the shared one. The per-port form exists because the
#: registries genuinely disagree: one list asserted for all three ports on an
#: offline box re-creates the conflation this module was written to end.
ENV_VAR = "VICARY_PUBLISHED_VERSIONS"

#: Seconds. A registry that has not answered by then is unknown, which is a
#: refusal — so this is the time the gate is willing to wait before declining to
#: measure, not a retry budget.
DEFAULT_TIMEOUT = 15.0


@dataclass(frozen=True)
class Answer:
    """What one lookup found.

    ``versions`` is ``None`` when the lookup itself failed, which is a different
    fact from "the registry serves this package and this version is not among
    them" and must never collapse into it.
    """

    versions: tuple[str, ...] | None
    error: str | None
    source: str

    @property
    def unknown(self) -> bool:
        return self.versions is None

    def serving(self, version: str) -> bool:
        return self.versions is not None and version in self.versions


@dataclass(frozen=True)
class Registry:
    #: What to call it in a refusal a human has to act on.
    name: str
    #: The name the port publishes under, which is not the port's name.
    package: str
    url: str
    parse: Callable[[str], Answer]


def _unknown(source: str, error: str) -> Answer:
    return Answer(versions=None, error=error, source=source)


def _parse_pypi(body: str) -> Answer:
    """PyPI's JSON API: ``releases`` maps version -> its files.

    A version whose every file is yanked is not something a user can install, so
    it is not a release for this purpose — ``pip install vicary==X`` on a fully
    yanked version resolves to nothing. A version with no files at all is the
    same story by a different route.
    """
    source = "PyPI"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        return _unknown(source, f"PyPI did not return JSON: {exc}")
    if not isinstance(parsed, dict):
        return _unknown(source, f"expected a JSON object from PyPI, got {type(parsed).__name__}")
    releases = parsed.get("releases")
    if not isinstance(releases, dict):
        return _unknown(source, "the PyPI payload carries no `releases` object — the shape moved")
    served = []
    for version, files in releases.items():
        if not isinstance(files, list) or not files:
            continue
        if all(isinstance(f, dict) and f.get("yanked") for f in files):
            continue
        served.append(str(version))
    return Answer(versions=tuple(sorted(served)), error=None, source=source)


def _parse_npm(body: str) -> Answer:
    """npm's packument: ``versions`` keyed by version string."""
    source = "npm"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        return _unknown(source, f"npm did not return JSON: {exc}")
    if not isinstance(parsed, dict):
        return _unknown(
            source, f"expected a JSON object packument from npm, got {type(parsed).__name__}")
    versions = parsed.get("versions")
    if not isinstance(versions, dict):
        return _unknown(source, "the npm packument carries no `versions` object — the shape moved")
    return Answer(versions=tuple(sorted(str(v) for v in versions)), error=None, source=source)


def _parse_rubygems(body: str) -> Answer:
    """RubyGems' versions API: a JSON array of objects carrying ``number``."""
    source = "RubyGems"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        return _unknown(source, f"RubyGems did not return JSON: {exc}")
    if not isinstance(parsed, list):
        return _unknown(source, f"expected a JSON array from RubyGems, got {type(parsed).__name__}")
    numbers = [str(v["number"]) for v in parsed if isinstance(v, dict) and "number" in v]
    if parsed and not numbers:
        return _unknown(
            source,
            f"the RubyGems versions API returned {len(parsed)} entries and none carried "
            f"a \"number\" field — the payload shape moved",
        )
    return Answer(versions=tuple(sorted(numbers)), error=None, source=source)


#: One registry per port, because one registry is what that port's users install
#: from. The package names differ from each other and from the port name; they
#: are the strings in `python/pyproject.toml`, `typescript/package.json` and
#: `ruby/vicary.gemspec`, and `tools/tests/test_latency_pair_published.py`
#: asserts they still are.
REGISTRIES: dict[str, Registry] = {
    "python": Registry(
        name="PyPI", package="vicary",
        url="https://pypi.org/pypi/vicary/json", parse=_parse_pypi,
    ),
    "typescript": Registry(
        name="npm", package="@bwthomas/vicary",
        url="https://registry.npmjs.org/" + urllib.parse.quote("@bwthomas/vicary", safe=""),
        parse=_parse_npm,
    ),
    "ruby": Registry(
        name="RubyGems", package="vicary",
        url="https://rubygems.org/api/v1/versions/vicary.json", parse=_parse_rubygems,
    ),
}


def env_override(impl: str, environ: dict[str, str] | None = None) -> tuple[str, ...] | None:
    """The operator's asserted list, or ``None`` if they asserted nothing.

    Whitespace or commas, either way, so a list pasted out of a registry page and
    a list typed by hand both work.
    """
    env = os.environ if environ is None else environ
    for name in (f"{ENV_VAR}_{impl.upper()}", ENV_VAR):
        raw = (env.get(name) or "").strip()
        if raw:
            return tuple(sorted(p for p in raw.replace(",", " ").split() if p))
    return None


def parse_for(impl: str, body: str) -> Answer:
    """Parse one registry's payload. Public and separate from :func:`fetch` so
    every shape this depends on is checked by a test rather than by a release."""
    return REGISTRIES[impl].parse(body)


def fetch(impl: str, timeout: float = DEFAULT_TIMEOUT,
          opener: Callable[[str, float], str] | None = None) -> Answer:
    """Ask the registry, and report a failed ask as a failed ask."""
    registry = REGISTRIES[impl]
    read = opener or _read
    try:
        body = read(registry.url, timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        return _unknown(
            registry.name,
            f"{registry.name} could not be reached at {registry.url}: {exc}",
        )
    return registry.parse(body)


def _read(url: str, timeout: float) -> str:
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "vicary-latency-gate"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8")


def published_versions(impl: str, timeout: float = DEFAULT_TIMEOUT,
                       environ: dict[str, str] | None = None,
                       opener: Callable[[str, float], str] | None = None) -> Answer:
    """What this port's registry serves — asserted by an operator, or asked."""
    asserted = env_override(impl, environ)
    if asserted is not None:
        return Answer(versions=asserted, error=None,
                      source=f"{ENV_VAR} (asserted, not asked)")
    return fetch(impl, timeout=timeout, opener=opener)
