"""Offline notability lookup: is this name a public figure, or somebody's cousin?

The problem this solves
-----------------------
Candidate generation over capitalised token sequences finds names with high
recall and terrible precision — it proposes ``Terrence Okonkwo`` and
``Vincent van Gogh`` with equal confidence, because in English prose they are the
same thing: two capitalised words. Blake's pair puts it beyond argument, since
both sit in the same first-person-possessive frame:

    My cousin Terrence Okonkwo came over            -> REDACT
    My inspiration, Vincent van Gogh, painted ...   -> KEEP

No syntactic feature separates those. So the filter is a lookup, and this module
is the lookup: a bundled, offline, no-model, no-network set membership test.
Precedence is deliberately asymmetric — **notable => KEEP, everything else =>
REDACT** — so a miss here costs precision (a public figure masked, which on the
inbound path is nearly free when the downstream model was trained on text where
every proper noun is already a placeholder) while a false positive here costs
*recall*, which is the entire gap being closed. The tiers are
sized with that asymmetry in mind: when in doubt, do not be notable.

That asymmetry rests on one property of the consuming system, and a host should
check that it holds before trusting the tuning: **the model downstream of this
redactor is one for which a placeholder token is ordinary input.** Scoring models
trained on essay corpora are typically such models — the corpora ship with every
proper noun already replaced by a placeholder — so masking a public figure costs
almost nothing there. A host feeding text to a model that has never seen a
placeholder pays more for over-redaction than these tiers assume.

Five tiers, and why the query is not decomposed into tokens
-----------------------------------------------------------
The asset (built by :mod:`vicary.build.gazetteer`) carries five sets: ``full``
(multi-token names of humans with >= 10 sitelinks), ``short`` (single-token and
particle-led surnames of humans with >= 100 sitelinks), ``place`` (public
geography and named landmarks, settlements excluded), ``given`` (common given
names, which are a **redact** signal rather than a keep), and ``title``
(published works and fictional characters).

Which tier a candidate is allowed to consult depends on its *shape*, and that
restriction is load-bearing rather than an optimisation:

* one token (``Lincoln``, ``Thoreau``) -> ``short`` or ``place``
* particle-led pair (``van Gogh``, ``de Gaulle``) -> ``short`` or ``place``
* two or more tokens (``Toni Morrison``, ``Lincoln Memorial``) -> ``full`` or
  ``place``, matched **whole**

A candidate is never split up and tested token by token. If it were,
``Priya Raghunathan-Bell`` would resolve notable off ``Bell`` (Alexander Graham
Bell, 84 sitelinks) and a real student's name would leak. Whole-string matching
is what makes the multi-token tier safe to populate broadly.

For the same reason honorifics and role titles are **not** stripped before
lookup. ``Coach Bramwell`` and ``Mrs. Okonkwo`` are two-token strings that match
no Wikidata label, so they redact — and stripping the title would demote them to
bare surnames, which is the one shape most likely to collide with a public
figure. A title in front of a name is evidence of a real person in the student's
life, not a prefix to discard. The cost is accepted and named: ``President
Lincoln`` over-redacts. :data:`ROLE_TITLES` is exported so a candidate generator
can use a leading title as a positive redact signal.

Cost
----
Lazy: importing this module reads nothing. The first ``is_notable`` call (or an
explicit :func:`load`) decompresses the asset. Lookups are ``frozenset``
membership on a normalised key — a handful of microseconds each, so a whole essay
of candidates costs well under the redaction pass's millisecond budget. Both
numbers are asserted in ``tests/test_gazetteer.py`` rather than assumed.

Where the asset comes from
--------------------------
It ships as package data inside the wheel; :mod:`vicary.assets` resolves it,
checksums it against the bundled manifest, and provides the
``VICARY_ASSET_PATH`` override for pointing a deployment at a different one.
:func:`load` raises rather than degrading, because the degraded state — an empty
gazetteer, so nothing is notable and every public figure masks — is
privacy-safe, product-hostile, and indistinguishable from over-aggressive tuning
for however long it takes somebody to notice.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from vicary import assets, config

logger = logging.getLogger(__name__)

#: Asset location, relative to this module. Written by
#: ``vicary.build.gazetteer`` and read here; nothing else should touch it.
ASSET_RELPATH = Path("data") / assets.NOTABILITY_ASSET

#: On-disk format this reader understands. A mismatch raises: an asset whose
#: tier semantics changed is worse than a missing one, because it answers.
SUPPORTED_FORMAT = 4

#: Lookup verdicts. Strings rather than an enum so they survive a JSON round
#: trip into eval rows without a converter.
NOT_NOTABLE = "not_notable"
TITLE = "title"
FULL_NAME = "full_name"
ICONIC_SHORT = "iconic_short"
PLACE = "place"
#: A nationality or regional adjective — ``Cuban``, ``Nigerian``, ``Bostonian``.
#: Its own verdict rather than folded into PLACE because it is not a place: it
#: is a word *derived* from one, it is the only keep tier with no notability
#: evidence behind it, and eval attribution needs to see it separately to tell
#: whether this tier is where a leak came from.
DEMONYM = "demonym"

#: Every tier this reader knows, in one place. :func:`_parse` builds its
#: accumulator from it, :class:`Gazetteer` carries one field per name, and
#: ``tests/test_assets.py`` reconciles all three against the shipped asset's
#: manifest — so a tier added to the builder and forgotten anywhere downstream
#: is a red test rather than a frozenset that silently reads empty. An empty
#: KEEP tier redacts everything it was built to protect, which presents as
#: over-aggressive tuning rather than as a packaging bug.
TIER_NAMES: tuple[str, ...] = ("full", "short", "place", "given", "title",
                               "demonym", "settlement")

#: Name particles that may lead a two- or three-token *partial* surname. Kept in
#: sync with the builder's list by a unit test rather than by import, so the
#: runtime module never depends on a build tool.
PARTICLES = frozenset(
    {
        "van", "von", "de", "del", "della", "di", "da", "du", "la", "le",
        "les", "der", "den", "ten", "ter", "dos", "das", "al", "bin", "ibn",
        "mac", "mc", "st", "saint", "san", "abu", "ben", "op", "vander",
    }
)

#: Honorifics and role titles. NOT stripped before lookup (see module docstring)
#: — exported because a leading title is a positive signal that a candidate is a
#: real person in the student's life, which is a candidate-generator concern.
ROLE_TITLES = frozenset(
    {
        "mr", "mrs", "ms", "miss", "mx", "dr", "doctor", "prof", "professor",
        "coach", "principal", "officer", "sgt", "sergeant", "capt", "captain",
        "rev", "reverend", "father", "sister", "brother", "pastor", "rabbi",
        "imam", "nurse", "sen", "senator", "rep", "gov", "governor", "mayor",
        "sir", "dame", "lady", "lord", "aunt", "uncle", "grandma", "grandpa",
    }
)


#: Curly quotes and dashes that NFKD leaves alone. Student prose is full of them
#: — a word processor turns every apostrophe curly — and without this mapping
#: "Lincoln’s" folds to "lincoln s" and misses every tier. Kept identical to
#: ``vicary.build.gazetteer._SMART_QUOTES``; a unit test pins them together.
_SMART_QUOTES = str.maketrans(
    {
        "‘": "'", "’": "'", "ʼ": "'", "′": "'",
        "“": '"', "”": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-",
        "—": "-", "−": "-",
    }
)


class GazetteerAssetMissing(RuntimeError):
    """The bundled asset is absent or unreadable.

    Raised rather than degrading to "nothing is notable". That fallback would be
    privacy-safe and product-hostile — every public figure in every essay masked
    — and it would look like a tuning regression, not a packaging bug, for as
    long as it took somebody to notice. See feedback_silent_fallback_audit.
    """


def normalize(name: str) -> str:
    """Fold a name to its lookup key.

    Accent-stripped, lower-cased, punctuation reduced to spaces. The apostrophe
    and internal hyphen survive because they belong to the name ("O'Keeffe",
    "Raghunathan-Bell") rather than surrounding it. A trailing possessive is
    dropped, because ``Terrence's older brother`` presents the name as
    ``Terrence's`` and a lookup that misses on the clitic is a leak.

    Must fold identically to ``vicary.build.gazetteer.normalize``; a unit
    test pins the two together over a shared vector set.
    """
    folded = name.translate(_SMART_QUOTES)
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower()
    chars = [c if (c.isalnum() or c in "'-") else " " for c in folded]
    key = " ".join("".join(chars).split())
    for clitic in ("'s", "s'"):
        if key.endswith(clitic) and len(key) > len(clitic) + 1:
            key = key[: -len(clitic)].rstrip("'").strip()
            break
    return key


@dataclass(frozen=True)
class Gazetteer:
    """An immutable, loaded notability index."""

    full: frozenset[str]
    short: frozenset[str]
    place: frozenset[str]
    #: Common given names. The INVERSE signal — see :meth:`is_common_given_name`.
    given: frozenset[str] = frozenset()
    #: Works and fictional characters — multi-token only. See :meth:`is_title`.
    title: frozenset[str] = frozenset()
    #: English demonyms — ``cuban``, ``nigerian``. A KEEP, see :attr:`DEMONYM`.
    demonym: frozenset[str] = frozenset()
    #: Human settlements. Neither a keep nor a redact signal — the only tier that
    #: is neither. See :meth:`is_settlement`.
    settlement: frozenset[str] = frozenset()
    #: Memoized first-token index, built on first use. Not a constructor argument
    #: because it is derived from ``title`` and must never disagree with it.
    _heads: frozenset[str] | None = field(default=None, compare=False)
    #: Memoized token-prefix index. Derived from ``title`` for the same reason.
    _prefixes: frozenset[str] | None = field(default=None, compare=False)
    meta: dict = field(default_factory=dict)

    @property
    def entry_count(self) -> int:
        """Entries that can make something KEEP.

        ``given`` and ``settlement`` are excluded on purpose: neither grants a
        keep, so counting them here would inflate the one number that answers
        "how much notability does this asset carry".
        """
        return (len(self.full) + len(self.short) + len(self.place)
                + len(self.title) + len(self.demonym))

    @property
    def title_heads(self) -> frozenset[str]:
        """First tokens of every title, so a scanner can skip most positions.

        Without this the title scan costs one lookup per candidate length at every
        token — measured at p95 14.9 ms on a 3,300-char essay against 1.8 ms
        without the scan, which is most of the G2 latency headroom for a feature
        that fires a few times an essay. With it the common case is a single
        frozenset miss.
        """
        if self._heads is None:
            object.__setattr__(
                self, "_heads",
                frozenset(key.split(" ", 1)[0] for key in self.title),
            )
        assert self._heads is not None
        return self._heads

    @property
    def title_prefixes(self) -> frozenset[str]:
        """Every token-prefix of every title, so a scan can stop the moment no
        title can still be reached.

        This is the automaton the per-position n-gram scan was standing in for.
        The old scan tried every length from the longest down at every candidate
        position: a position starting "the" — one of the commonest title heads in
        English and also one of the commonest words in a sentence — cost a full
        eight lookups over growing substrings before concluding nothing matched.
        With the prefix index a walk advances only while some title still starts
        with what it has read, which on ordinary prose is one or two tokens.

        Membership rather than a trie of dicts on purpose. A nested-dict trie over
        37,249 titles allocates a Python object per edge; one flat frozenset of
        pre-joined prefixes is the same asymptotics with a fraction of the objects
        and no per-node attribute lookup, and it is built by a single pass over
        keys that are already normalised.
        """
        if self._prefixes is None:
            prefixes: set[str] = set()
            for key in self.title:
                tokens = key.split(" ")
                for length in range(1, len(tokens)):
                    prefixes.add(" ".join(tokens[:length]))
            object.__setattr__(self, "_prefixes", frozenset(prefixes))
        assert self._prefixes is not None
        return self._prefixes

    def is_title_prefix(self, key: str) -> bool:
        """True when some title starts with (or equals) the token sequence ``key``.

        ``key`` is an already-folded lookup key — space-joined lower-cased tokens
        — not raw text. The scan folds each token of the document once and joins,
        rather than re-normalising a growing substring at every length, which is
        where the old scan spent most of its time.
        """
        return key in self.title_prefixes or key in self.title

    @property
    def max_title_tokens(self) -> int:
        """Longest title in tokens, so a scanner knows how far to look ahead."""
        return max((key.count(" ") + 1 for key in self.title), default=0)

    def is_title(self, name: str) -> bool:
        """True when ``name`` is a published work or a fictional character.

        The full tier is ``P31 wd:Q5`` — human — so before this tier existed
        every work title and every fictional character resolved not-notable and
        was redacted: "Harry Potter taught me about friendship" came back as
        "{NAME} taught me about friendship". Writing about a book or a film is
        one of the commonest things a school essay does.

        Multi-token by construction, and that is a safety property rather than a
        convenience. "It", "Up", "Her", "Room", "Brave" and "Cats" are all films;
        a single-token title tier would make those ordinary words permanently
        notable, and notable means KEEP, so the cost would land on recall — the
        leg this whole effort exists to close.
        """
        key = normalize(name)
        return " " in key and key in self.title

    def is_common_given_name(self, token: str) -> bool:
        """True when ``token`` is a first name lots of notable people share.

        Not part of the notability decision, and deliberately not consulted by
        :meth:`notability` — it points the other way. A given-name hit is
        evidence the token names a *person*, which on the inbound path means
        redact.

        It exists for the two frames capitalisation cannot reach. ``then terrence
        okonkwo showed up`` and ``MY BEST FRIEND DESHAWN PRITCHARD`` score zero
        for any candidate generator keyed on capitalisation, by construction —
        the fixture says so in as many words. A case-insensitive scan closes
        that, and a scan needs a list. This is the list; the scan belongs to the
        candidate generator.
        """
        key = normalize(token)
        return bool(key) and " " not in key and key in self.given

    def is_settlement(self, name: str) -> bool:
        """True when ``name`` is a town, city or village.

        **Not part of the notability decision, and deliberately not consulted by**
        :meth:`notability`. A settlement is a student's hometown, so it must
        redact; that is the whole reason ``Q486972`` is subtracted from the place
        tier. What this answers is the *next* question, asked only about a span
        that is already being masked: which placeholder does it get. A host that
        reads the type back writes "great job describing your trip to
        {LOCATION}", and before this tier existed it wrote "{NAME}".

        So the failure modes are not symmetric with a keep tier's, and neither is
        the cost of a wrong answer: a miss types a place ``{NAME}`` and a false
        positive types a person ``{LOCATION}``. Both are already redacted. The
        builder subtracts common given names and well-borne US surnames precisely
        because the second is the one a student would read.

        Same shape as :meth:`is_common_given_name` — a tier that ships in the
        asset, is reconciled by the manifest, and is invisible to
        :meth:`is_notable`.
        """
        key = normalize(name)
        return bool(key) and key in self.settlement

    def notability(self, name: str) -> str:
        """Classify ``name``. One of the four verdict constants above.

        Places are checked first: the string is being judged on what it *names*,
        and a place-name that is also a surname (``Washington``, ``Delaware``) is
        keepable either way, so resolving it as a place costs nothing and saves a
        second probe.
        """
        key = normalize(name)
        if not key:
            return NOT_NOTABLE
        tokens = key.split()
        if key in self.place:
            return PLACE
        if len(tokens) == 1:
            if key in self.short:
                return ICONIC_SHORT
            # After `short`, because a token that is both — none today, but the
            # tiers are rebuilt from a moving upstream — should report the tier
            # that carries notability evidence rather than the one that does not.
            # The builder has already subtracted `given` from this tier, so a
            # demonym here cannot be shadowing a common first name.
            return DEMONYM if key in self.demonym else NOT_NOTABLE
        if len(tokens) <= 3 and tokens[0] in PARTICLES and key in self.short:
            # "van Gogh", "de Gaulle" — a partial, not a full name, so it is held
            # to the strict short-tier threshold.
            return ICONIC_SHORT
        if key in self.full:
            return FULL_NAME
        # Titles resolve LAST. "Joan of Arc" and "van Gogh" are both also film
        # titles, and attributing them to the title tier would be true but less
        # specific — the person is who the student wrote about. Either way the
        # verdict is KEEP; only the reported tier changes, and that tier is what
        # eval attribution and telemetry read.
        if len(tokens) > 1 and key in self.title:
            return TITLE
        return NOT_NOTABLE

    def is_notable(self, name: str) -> bool:
        return self.notability(name) != NOT_NOTABLE


_lock = threading.Lock()
_loaded: Gazetteer | None = None


def asset_path() -> Path:
    """Absolute path to the asset this process will load.

    The bundled copy by default — computed from the module's own location, so a
    checkout, a virtualenv and a container are all correct with nothing set — or
    whatever ``VICARY_ASSET_PATH`` names. See :mod:`vicary.assets`.
    """
    return assets.resolve()[0]


def _parse(text: str) -> Gazetteer:
    tiers: dict[str, set[str]] = {name: set() for name in TIER_NAMES}
    meta: dict = {}
    declared: dict[str, int] = {}
    current: set[str] | None = None
    saw_header = False

    for line in text.splitlines():
        if line.startswith("#!"):
            head, _, rest = line[2:].partition(" ")
            if head == "gazetteer":
                saw_header = True
                if int(rest.strip()) != SUPPORTED_FORMAT:
                    raise GazetteerAssetMissing(
                        f"gazetteer asset format {rest.strip()} is not "
                        f"{SUPPORTED_FORMAT}; rebuild with "
                        "`python -m vicary.assets fetch`"
                    )
            elif head == "meta":
                meta = json.loads(rest)
            elif head == "tier":
                tier_name, _, tier_count = rest.partition(" ")
                if tier_name not in tiers:
                    raise GazetteerAssetMissing(
                        f"unknown gazetteer tier {tier_name!r}"
                    )
                current = tiers[tier_name]
                declared[tier_name] = int(tier_count)
            continue
        if line and current is not None:
            current.add(line)

    if not saw_header:
        raise GazetteerAssetMissing("gazetteer asset has no format header")
    for name, count in declared.items():
        if len(tiers[name]) != count:
            # A truncated asset is the failure mode a partial COPY or a
            # half-written build produces, and it answers plausibly wrong
            # instead of erroring. Cheap to catch, so catch it.
            raise GazetteerAssetMissing(
                f"gazetteer tier {name!r} declares {count} entries, found "
                f"{len(tiers[name])} — asset is truncated"
            )

    return Gazetteer(
        full=frozenset(tiers["full"]),
        short=frozenset(tiers["short"]),
        place=frozenset(tiers["place"]),
        given=frozenset(tiers["given"]),
        title=frozenset(tiers["title"]),
        demonym=frozenset(tiers["demonym"]),
        settlement=frozenset(tiers["settlement"]),
        meta=meta,
    )


def load(path: Path | None = None, *, force: bool = False) -> Gazetteer:
    """Load (and memoize) the gazetteer. Safe to call from several threads.

    Call it at container init to move the decompression off the first request's
    latency; otherwise the first :func:`is_notable` pays it.
    """
    global _loaded
    if path is None and _loaded is not None and not force:
        return _loaded
    if path is not None:
        target, verified_against_manifest = Path(path), False
    else:
        target, verified_against_manifest = assets.resolve()
    if not target.exists():
        raise GazetteerAssetMissing(
            f"notability gazetteer not found at {target}. If this is an "
            "installed package the wheel is incomplete — reinstall it. To build "
            "one from the upstream sources, run `python -m vicary.assets fetch`; "
            f"to point at an existing one, set {config.ASSET_PATH_ENV_VAR}."
        )
    if verified_against_manifest:
        # Only the bundled asset is manifest-checked. An asset supplied through
        # VICARY_ASSET_PATH is meant to be a different file, so comparing it to
        # the bundled checksum would fail by design; its format header and
        # tier-count reconciliation below are its verification.
        report = assets.verify()
        if not report:
            raise GazetteerAssetMissing(
                "bundled notability gazetteer does not match the package "
                "manifest: " + "; ".join(report.problems)
            )
    try:
        with gzip.open(target, "rt", encoding="utf-8") as fh:
            gazetteer = _parse(fh.read())
    except OSError as exc:  # unreadable / not gzip / truncated stream
        raise GazetteerAssetMissing(f"cannot read gazetteer at {target}: {exc}") from exc

    if path is None:
        with _lock:
            _loaded = gazetteer
    # Every tier, from TIER_NAMES rather than a hand-written format string: this
    # line is where an operator would have seen `demonym=0` on the cut that
    # shipped the tier empty, and it said nothing because nobody extended it.
    logger.info(
        "loaded notability gazetteer: %s cut=%s",
        " ".join(f"{name}={len(getattr(gazetteer, name))}"
                 for name in TIER_NAMES),
        gazetteer.meta.get("cut_date", "unknown"),
    )
    return gazetteer


def reset_cache() -> None:
    """Drop the memoized gazetteer. For tests that swap in a fixture asset."""
    global _loaded
    with _lock:
        _loaded = None


def is_notable(name: str) -> bool:
    """True when ``name`` is a public figure or a public place.

    The one call the redaction path needs. ``notable => KEEP``.
    """
    return load().is_notable(name)


def notability(name: str) -> str:
    """Which tier matched ``name``, for telemetry and for eval attribution."""
    return load().notability(name)


def is_common_given_name(token: str) -> bool:
    """True when ``token`` is a common given name — a REDACT signal, not a KEEP."""
    return load().is_common_given_name(token)


def is_settlement(name: str) -> bool:
    """True when ``name`` is a town or city — a TYPING signal, not a keep."""
    return load().is_settlement(name)


def is_title(name: str) -> bool:
    """True when ``name`` is a published work or a fictional character."""
    return load().is_title(name)


def is_title_head(token: str) -> bool:
    """True when some title *starts* with ``token`` — the scan's cheap prefilter.

    Deliberately uses ``str.lower`` rather than :func:`normalize`. This runs once
    per word of every essay, and ``normalize`` does an NFKD decomposition and a
    per-character rebuild — measured at p95 15.4 ms for the title scan against
    1.8 ms without it, which is 44% of the G2 latency headroom spent on a
    prefilter. The heads are already folded and overwhelmingly plain ASCII, so the
    only cost is that a title beginning with an accented word fails the prefilter
    and is not matched. That loses a keep, never a redaction.
    """
    return token.lower() in load().title_heads


def is_title_prefix(key: str) -> bool:
    """True when some title starts with the folded token sequence ``key``."""
    return load().is_title_prefix(key)


def max_title_tokens() -> int:
    """Longest title in tokens — how far a title scanner must look ahead."""
    return load().max_title_tokens
