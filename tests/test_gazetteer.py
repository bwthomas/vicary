"""Tests for the offline notability gazetteer.

Two kinds of test here, and the split is deliberate. The first kind pins
*behaviour a change could plausibly break*: the fixture's KEEP spans must all
resolve notable and its REDACT literals must none of them resolve notable, so a
threshold nudge or a "helpful" lookup relaxation shows up as a red rather than as
a quiet leak six weeks later. The second kind pins the *packaging*, because the
way this asset fails is not a wrong answer — it is a missing file in a container
that then answers "nothing is notable" and looks like over-aggressive tuning.

Every assertion here is one that fails if the thing it guards is removed. In
particular ``test_lookup_does_not_decompose_a_candidate_into_tokens`` and
``test_staging_rsync_ships_the_asset`` were both red before the code and the
justfile they guard were written.
"""

from __future__ import annotations

import gzip
import re
import statistics
import time
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest

from vicary import assets
from vicary import gazetteer as gazetteer_module
from vicary.build import gazetteer as build_gazetteer
from vicary.eval import fixture
from vicary.gazetteer import (
    FULL_NAME,
    ICONIC_SHORT,
    NOT_NOTABLE,
    PLACE,
    SUPPORTED_FORMAT,
    TITLE,
    Gazetteer,
    GazetteerAssetMissing,
    asset_path,
    is_notable,
    is_title,
    is_title_prefix,
    load,
    normalize,
    notability,
    reset_cache,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every private-name literal in the fixture, plus the ones the redaction plan
#: names explicitly. None of these may ever resolve notable.
REDACT_LITERALS = (
    "Terrence Okonkwo", "Marisol", "Coach Bramwell", "Mrs. Okonkwo",
    "terrence okonkwo", "DESHAWN PRITCHARD", "Ayaan Chaudhary",
    "Marisol Ybarra", "Priya Raghunathan-Bell", "Terrence", "J. Okonkwo",
    "Terry", "Deshawn", "Akron", "Progressive Insurance",
    "Marguerite Delacroix-Whitfield", "Westfield High School",
)


@pytest.fixture(scope="module")
def gazetteer() -> Gazetteer:
    if not asset_path().exists():
        pytest.skip(
            f"gazetteer asset not built at {asset_path()}; "
            "run `python -m vicary.build.gazetteer`"
        )
    return load(force=True)


# ---------------------------------------------------------------------------
# Normalisation — the builder and the reader must fold identically
# ---------------------------------------------------------------------------

NORMALIZE_VECTORS = (
    "Vincent van Gogh",
    "VINCENT VAN GOGH",
    "Lincoln’s",
    "O’Keeffe",
    "  vincent   van  gogh  ",
    "Mrs. Okonkwo",
    "J. Okonkwo",
    "Priya Raghunathan-Bell",
    "Joan of Arc",
    "Georgia O'Keeffe",
    "Frédéric Chopin",
    "Beyoncé",
    "DESHAWN PRITCHARD",
    "Martin Luther King, Jr.",
    "",
    "   ",
    "!!!",
)


@pytest.mark.parametrize("raw", NORMALIZE_VECTORS)
def test_builder_and_runtime_normalize_identically(raw: str) -> None:
    """The asset is keyed by the builder's fold and probed by the runtime's.

    The two functions are duplicated on purpose — the request path must not
    import a build tool — so the only thing keeping them honest is this test.
    If they drift, every lookup silently misses and the gazetteer answers
    "nothing is notable" while looking perfectly healthy.
    """
    assert normalize(raw) == build_gazetteer.normalize(raw)


def test_normalize_strips_the_possessive_clitic() -> None:
    # "Terrence's older brother" hands the lookup "Terrence's". A fold that
    # keeps the clitic misses the gazetteer and, for a notable name, over-masks.
    assert normalize("Lincoln's") == "lincoln"
    assert normalize("Terrence's") == "terrence"
    assert normalize("Lincoln’s") == "lincoln"


def test_normalize_keeps_hyphens_and_apostrophes_inside_names() -> None:
    assert normalize("Raghunathan-Bell") == "raghunathan-bell"
    assert normalize("O'Keeffe") == "o'keeffe"


def test_particles_match_between_builder_and_runtime() -> None:
    from vicary import gazetteer as runtime

    assert runtime.PARTICLES == build_gazetteer.PARTICLES


def test_smart_quote_folding_matches_between_builder_and_runtime() -> None:
    from vicary import gazetteer as runtime

    assert runtime._SMART_QUOTES == build_gazetteer._SMART_QUOTES


def test_curly_apostrophes_fold_like_straight_ones() -> None:
    """Word processors emit U+2019, and NFKD does not touch it.

    Without an explicit mapping, "Lincoln’s" folds to "lincoln s" and misses
    every tier — a notable name silently over-masked on the most ordinary
    punctuation in student prose. This test was red before the mapping existed.
    """
    assert normalize("Lincoln’s") == normalize("Lincoln's") == "lincoln"
    assert normalize("O’Keeffe") == "o'keeffe"


# ---------------------------------------------------------------------------
# Precision — the names that must survive redaction
# ---------------------------------------------------------------------------


def test_every_fixture_keep_span_resolves_notable(gazetteer: Gazetteer) -> None:
    """The precision gate, at ALL of the spans the gazetteer is asked to carry.

    Was ">= 90% of KEEP spans", which is the wrong shape for this invariant. Two
    KEEP spans are backed by the *document* rather than the asset — a bare surname
    licensed by a full name in the same essay ("Wright"), and an ordinary word the
    writer also spells lower-case ("Curfew") — and no gazetteer can resolve
    either. A tolerance band absorbed them silently, and the band's real function
    turned out to be granting the same free pass to whatever landed in it next:
    adding the second one is what took the ratio under the threshold and turned a
    deliberate design property into a red test.

    So the spans are split by ``kept_by`` and each half is asserted for what it
    actually claims: notability-backed spans must resolve at 100%, and
    document-backed spans must NOT resolve — if one starts resolving, the frame is
    no longer testing document-internal evidence and the coverage it was measuring
    has gone quietly missing.
    """
    spans = [
        span
        for frame in fixture.ALL_FRAMES
        for span in frame.keep_spans
        if span.entity in {"NAME", "LOCATION", "ORGANIZATION"}
    ]
    assert spans, "fixture has no KEEP name/location spans — nothing measured"

    by_asset = [s for s in spans if s.kept_by == "notability"]
    by_document = [s for s in spans if s.kept_by == "document"]
    assert by_asset and by_document, (
        "both halves must be populated or this test is only measuring one of them"
    )

    misses = [s.literal for s in by_asset if not gazetteer.is_notable(s.literal)]
    assert not misses, (
        f"{len(misses)}/{len(by_asset)} notability-backed KEEP spans did not "
        f"resolve: {misses}"
    )
    resolved = [s.literal for s in by_document if gazetteer.is_notable(s.literal)]
    assert not resolved, (
        f"document-backed KEEP spans now resolve in the gazetteer: {resolved}. "
        "The frame no longer measures document-internal evidence — either mark it "
        "kept_by='notability' or find a literal the asset does not carry."
    )


@pytest.mark.parametrize(
    "literal,tier",
    [
        ("Vincent van Gogh", FULL_NAME),
        ("Henry David Thoreau", FULL_NAME),
        ("Toni Morrison", FULL_NAME),
        ("Joan of Arc", FULL_NAME),
        ("Malcolm Gladwell", FULL_NAME),
        ("Rosa Parks", FULL_NAME),
        ("Lincoln", ICONIC_SHORT),
        ("Thoreau", ICONIC_SHORT),
        ("van Gogh", ICONIC_SHORT),
        ("Delaware", PLACE),
        ("Lincoln Memorial", PLACE),
    ],
)
def test_partial_and_full_surface_forms_resolve_in_the_expected_tier(
    gazetteer: Gazetteer, literal: str, tier: str
) -> None:
    """Which tier answers is part of the contract, not an implementation detail.

    A name that starts resolving via a different tier means a threshold moved,
    and the tiers have very different false-positive profiles.
    """
    assert gazetteer.notability(literal) == tier


def test_washington_resolves_notable_despite_being_a_place_and_a_surname(
    gazetteer: Gazetteer,
) -> None:
    # The fixture's adversarial case: notable person, US state, and one of the
    # most common American surnames, all one string. It must be KEEP.
    assert gazetteer.is_notable("Washington")


# ---------------------------------------------------------------------------
# Recall — the names that must NOT survive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("literal", REDACT_LITERALS)
def test_private_names_do_not_resolve_notable(
    gazetteer: Gazetteer, literal: str
) -> None:
    """Each of these resolving notable is one name that leaks into the scorer."""
    assert gazetteer.notability(literal) == NOT_NOTABLE, (
        f"{literal!r} resolved notable — it would survive redaction"
    )


def test_no_fixture_redact_span_resolves_notable(gazetteer: Gazetteer) -> None:
    leaks = [
        (frame.frame_id, span.literal)
        for frame in fixture.ALL_FRAMES
        for span in frame.redact_spans
        if span.entity in {"NAME", "LOCATION", "ORGANIZATION"}
        and gazetteer.is_notable(span.literal)
    ]
    assert not leaks, f"gazetteer would leak: {leaks}"


def test_blakes_pair_splits(gazetteer: Gazetteer) -> None:
    """The whole reason this module exists, in two lines.

    Identical syntax, opposite verdicts. If these ever agree, the notability
    filter has stopped doing the only job it has.
    """
    assert gazetteer.is_notable("Vincent van Gogh") is True
    assert gazetteer.is_notable("Terrence Okonkwo") is False


def test_landmark_keeps_while_hometown_in_the_same_sentence_redacts(
    gazetteer: Gazetteer,
) -> None:
    # "We drove from Akron all the way to see the Lincoln Memorial."
    # Both are LOCATION spans; only one is PII. The settlement exclusion in the
    # builder is the only thing separating them.
    assert gazetteer.is_notable("Lincoln Memorial") is True
    assert gazetteer.is_notable("Akron") is False


def test_lookup_does_not_decompose_a_candidate_into_tokens(
    gazetteer: Gazetteer,
) -> None:
    """A multi-token candidate is matched WHOLE, never token by token.

    This is the test that fails the obvious "improvement". "Lincoln" is in the
    short tier; a lookup that tried each token would resolve "Priya Lincoln" and
    "Coach Lincoln" notable and leak a real student's name off a coincidence.
    """
    assert gazetteer.is_notable("Lincoln") is True
    assert gazetteer.is_notable("Priya Lincoln") is False
    assert gazetteer.is_notable("Coach Lincoln") is False
    assert gazetteer.is_notable("Lincoln Okonkwo") is False


def test_honorifics_are_not_stripped_before_lookup(gazetteer: Gazetteer) -> None:
    """Deliberate, and it costs precision on "President Lincoln".

    Stripping the title would demote a titled name to a bare surname, which is
    the highest-collision surface form there is. A title in front of a name is
    evidence of a real person in the student's life. Documented in the module;
    asserted here so the trade is not silently reversed.
    """
    assert gazetteer.is_notable("Mrs. Okonkwo") is False
    assert gazetteer.is_notable("Coach Bramwell") is False
    assert gazetteer.is_notable("President Lincoln") is False


def test_bare_first_names_never_resolve_notable(gazetteer: Gazetteer) -> None:
    """The single most common private-name surface form in student prose.

    The builder deliberately does not emit first names as short forms. If it
    started to, this reds.
    """
    for name in ("Terrence", "Marisol", "Deshawn", "Terry", "Marguerite", "Ayaan"):
        assert gazetteer.notability(name) == NOT_NOTABLE, name


def test_common_american_surnames_are_excluded_from_the_short_tier(
    gazetteer: Gazetteer,
) -> None:
    """The Census subtraction, asserted where it matters.

    Without it, a student writing "then Smith walked in" about a classmate gets
    "Smith" kept, and the population-weighted rate of that mistake is 16.2%.
    These four are the highest-frequency US surnames that a sitelink-only tier
    admits, so they are the cheapest possible red for a regressed exclusion.
    """
    for surname in ("Smith", "Johnson", "Williams", "Brown", "King", "Lee"):
        assert gazetteer.notability(surname) == NOT_NOTABLE, (
            f"{surname!r} is in the short tier — the Census exclusion regressed"
        )


def test_settlements_are_not_in_the_place_tier(gazetteer: Gazetteer) -> None:
    """A town name is a student's hometown, which is exactly the PII in scope."""
    for town in ("Akron", "Cleveland", "Dayton", "Westfield", "Brooklyn"):
        assert town.lower() not in gazetteer.place, town


def test_single_token_places_are_held_to_the_strict_bar(gazetteer: Gazetteer) -> None:
    """The place tier is an independent false-positive channel from the short tier.

    "Lee" is a minor geographic feature (38 sitelinks) and one of the twenty most
    common American surnames. Before single-token places were held to the same
    100-sitelink bar as single-token person names, it resolved notable via
    ``place`` and sailed straight past the short tier's Census exclusion. That
    channel exposed 7.91% of US surname-bearers; this test is its guard.
    """
    for surname in ("Lee", "Bell", "Ford", "Hill", "Wood"):
        if gazetteer.notability(surname) != NOT_NOTABLE:
            pytest.fail(
                f"{surname!r} resolves {gazetteer.notability(surname)!r} — a "
                "common American surname is notable via a single-token tier"
            )
    # The bar must not have swallowed the place names the fixture needs.
    assert gazetteer.notability("Delaware") == PLACE
    assert gazetteer.notability("Washington") == PLACE


# ---------------------------------------------------------------------------
# The inverse signal — common given names, for the case-insensitive frames
# ---------------------------------------------------------------------------


def test_common_given_names_are_recognised_as_a_redact_signal(
    gazetteer: Gazetteer,
) -> None:
    """The list that makes the lowercase/allcaps frames reachable at all.

    Capitalisation-based candidate generation scores zero on "then terrence
    okonkwo showed up" by construction. A case-insensitive scan needs something
    to scan for; this is it.
    """
    assert gazetteer.is_common_given_name("terrence") is True
    assert gazetteer.is_common_given_name("Marisol") is True
    assert gazetteer.is_common_given_name("TERRY") is True
    # Surnames are not given names — the tier is built from label-leading tokens.
    assert gazetteer.is_common_given_name("okonkwo") is False
    assert gazetteer.is_common_given_name("pritchard") is False
    # Multi-token input is not a given name by definition.
    assert gazetteer.is_common_given_name("terrence okonkwo") is False


def test_given_names_do_not_leak_into_the_notability_decision(
    gazetteer: Gazetteer,
) -> None:
    """The given tier points the OTHER way, and must never make a name notable.

    "terrence" is in the given tier. If ``notability`` ever consulted it, the
    fixture's most basic redact case would start being kept.
    """
    assert gazetteer.is_common_given_name("terrence") is True
    assert gazetteer.notability("terrence") == NOT_NOTABLE
    assert gazetteer.notability("Terrence Okonkwo") == NOT_NOTABLE


# ---------------------------------------------------------------------------
# Cost — lazy, and inside the PII pass's budget
# ---------------------------------------------------------------------------


def test_import_does_not_read_the_asset() -> None:
    """Importing must cost nothing; the load is paid on first use or on warmup."""
    from vicary import gazetteer as module

    reset_cache()
    assert module._loaded is None
    notability("Lincoln")
    assert module._loaded is not None


def test_lookups_fit_the_per_essay_budget(gazetteer: Gazetteer) -> None:
    """200 candidate spans — the top of the expected range — under 5 ms.

    The whole PII pass has ~35 ms of headroom on the serial request path, and the
    gazetteer's slice of that is 5 ms. Warm gazetteer, because load() is paid
    once per container. Generous ceiling so this is a real regression signal on a
    loaded CI box rather than a flake.
    """
    corpus = [
        span.literal for frame in fixture.ALL_FRAMES for span in frame.spans
    ]
    candidates = [corpus[i % len(corpus)] for i in range(200)]
    samples = []
    for _ in range(50):
        start = time.perf_counter()
        for candidate in candidates:
            gazetteer.is_notable(candidate)
        samples.append((time.perf_counter() - start) * 1000.0)
    median = statistics.median(samples)
    assert median < 5.0, f"{len(candidates)} lookups took {median:.2f} ms (budget 5 ms)"


def test_cold_load_is_bounded() -> None:
    """Container-init cost. Measured, not assumed — it is the one blocking read."""
    if not asset_path().exists():
        pytest.skip("gazetteer asset not built")
    reset_cache()
    start = time.perf_counter()
    load(force=True)
    elapsed = (time.perf_counter() - start) * 1000.0
    assert elapsed < 1000.0, f"cold load took {elapsed:.0f} ms"


# ---------------------------------------------------------------------------
# Packaging — how this actually breaks in production
# ---------------------------------------------------------------------------


def test_missing_asset_raises_rather_than_degrading(tmp_path: Path) -> None:
    """Fail loud.

    Degrading to "nothing is notable" would be privacy-safe and product-hostile:
    every public figure in every essay masked, presenting as a tuning regression
    rather than a missing file. See feedback_silent_fallback_audit.
    """
    with pytest.raises(GazetteerAssetMissing) as caught:
        load(tmp_path / "absent.txt.gz")
    assert "vicary.assets fetch" in str(caught.value)


def test_truncated_asset_raises(tmp_path: Path) -> None:
    """A half-written or half-copied asset answers plausibly wrong otherwise."""
    target = tmp_path / "truncated.txt.gz"
    with gzip.open(target, "wt", encoding="utf-8") as handle:
        # Current format, not a literal: this test is about a truncated tier,
        # and a stale version number here fails it on the format check instead.
        handle.write(
            f"#!gazetteer {SUPPORTED_FORMAT}\n#!meta {{}}\n#!tier full 3\n"
            "alpha beta\n"
        )
        handle.write("#!tier short 0\n#!tier place 0\n")
    with pytest.raises(GazetteerAssetMissing, match="truncated"):
        load(target)


def test_unsupported_asset_format_raises(tmp_path: Path) -> None:
    target = tmp_path / "future.txt.gz"
    with gzip.open(target, "wt", encoding="utf-8") as handle:
        handle.write("#!gazetteer 99\n")
    with pytest.raises(GazetteerAssetMissing, match="format"):
        load(target)


def test_asset_carries_its_provenance(gazetteer: Gazetteer) -> None:
    """A coverage number is only traceable if the cut that produced it is recorded."""
    for key in (
        "source",
        "cut_date",
        "full_min_sitelinks",
        "short_min_sitelinks",
        "short_max_us_surname_population",
        "place_min_sitelinks",
        "place_min_sitelinks_single_token",
        "given_name_min_bearers",
    ):
        assert key in gazetteer.meta, key
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", gazetteer.meta["cut_date"])


def test_every_data_file_is_declared_as_package_data() -> None:
    """A file in ``data/`` that no ``package-data`` glob matches is not shipped.

    This replaces a test that used to assert a *host repository's* build recipe
    included ``*.gz``, because that repository's file-copy step was an extension
    allowlist and had already silently dropped a runtime file once. Packaging
    removes that hazard but does not remove this one: setuptools ships what the
    globs match and nothing else, so a second asset with a new extension is
    dropped just as quietly. Add the glob when you add the file.
    """
    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("pyproject.toml not present (installed, not a checkout)")
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    globs = config["tool"]["setuptools"]["package-data"]["vicary"]

    data_dir = assets.DATA_DIR
    for entry in sorted(data_dir.iterdir()):
        if entry.name == "__pycache__" or entry.is_dir():
            continue
        relative = f"data/{entry.name}"
        assert any(fnmatch(relative, glob) for glob in globs), (
            f"{relative} matches none of {globs}, so it will be missing from "
            "the built wheel while the build itself succeeds"
        )


def test_asset_lives_inside_the_package_directory() -> None:
    """It has to be package data, not a sibling of the package.

    A file outside ``src/vicary/`` cannot be declared as package data at all, so
    it would reach a developer's checkout and nothing else.
    """
    package_root = Path(gazetteer_module.__file__).resolve().parent
    assert assets.bundled_path().is_relative_to(package_root)


def test_bundled_asset_matches_the_manifest() -> None:
    """The checksum in ``MANIFEST.json`` describes the asset actually shipped.

    Red whenever the asset is rebuilt without rewriting the manifest, which is
    the normal way a build goes wrong: ``python -m vicary.assets fetch`` does
    both, a hand-run builder invocation does only the first.
    """
    report = assets.verify()
    assert report, "; ".join(report.problems)


def test_module_level_helpers_use_the_cached_gazetteer() -> None:
    if not asset_path().exists():
        pytest.skip("gazetteer asset not built")
    reset_cache()
    assert is_notable("Vincent van Gogh") is True
    assert is_notable("Terrence Okonkwo") is False
    assert notability("Akron") == NOT_NOTABLE


# ---------------------------------------------------------------------------
# The title tier: works and fictional characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "My Cousin Vinny",
        "Harry Potter",
        "To Kill a Mockingbird",
        "The Lion King",
        "Atticus Finch",
        "The Great Gatsby",
    ],
)
def test_a_work_or_character_is_notable(name: str) -> None:
    """The defect this tier closed, pinned per case.

    The full tier is ``P31 wd:Q5`` — human — so before the title tier existed
    every one of these resolved not_notable and was redacted: "Harry Potter
    taught me about friendship" came back as "{NAME} taught me about
    friendship". Writing about a book or a film is one of the commonest things a
    school essay does.
    """
    assert is_notable(name), name


def test_a_single_token_title_is_not_notable() -> None:
    """The safety property that makes the tier affordable.

    "It", "Up", "Her", "Room" and "Brave" are all films. A single-token title
    tier would make those ordinary words permanently notable, and notable means
    KEEP, so the cost would land on recall — the leg this all exists to close.
    """
    for word in ("It", "Up", "Her", "Room", "Brave", "Cats"):
        assert not is_notable(word), word


def test_a_title_of_only_ordinary_words_is_dropped() -> None:
    """"My Best Friend" is a film, and admitting it broke the allcaps frame.

    With it in the tier, "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER DO THAT"
    was protected whole and recall on that frame went 100% -> 0%. A title carries
    evidence only when at least one token is not a word every essay contains.
    """
    assert not is_notable("My Best Friend")


def test_a_person_outranks_a_same_named_title() -> None:
    """Both verdicts are KEEP; the tier attributed is what telemetry reads.

    "Joan of Arc" and "van Gogh" are also film titles. The person is who the
    student wrote about, so the person tiers resolve first and the title tier is
    the fallback.
    """
    assert notability("Joan of Arc") == FULL_NAME
    assert notability("van Gogh") == ICONIC_SHORT
    assert notability("My Cousin Vinny") == TITLE


def test_the_prefix_index_reaches_every_title_head() -> None:
    """The prefix index subsumes the first-token index it replaced.

    ``title_heads`` is the length-1 case of ``title_prefixes``, and the scan now
    walks one index instead of consulting two. If they ever disagreed, the scan
    would skip a position whose first word does start a title.
    """
    gz = load()
    for head in gz.title_heads:
        assert gz.is_title_prefix(head), head


def test_the_prefix_index_stops_a_walk_that_cannot_reach_a_title() -> None:
    """What the automaton is for: the early exit."""
    gz = load()
    assert gz.is_title_prefix("to")
    assert gz.is_title_prefix("to kill")
    assert gz.is_title_prefix("to kill a mockingbird")   # the whole title
    assert not gz.is_title_prefix("to kill a spider")


def test_the_automaton_and_the_n_gram_scan_agree() -> None:
    """The prefix index is an optimisation, not a behaviour change.

    p95 on a 3,300-char essay went 12.5 ms -> 4.0 ms, so this is the test that
    the 8.5 ms came out of the lookups and not out of the results.
    """
    from vicary.name_candidates import find_title_spans

    text = (
        "I read To Kill a Mockingbird in ninth grade, then The Lion King came on "
        "and my cousin Terrence Okonkwo said Atticus Finch would have hated it. "
        "The great gatsby is on the list too, and so is Of Mice and Men."
    )
    walked = find_title_spans(text, is_title, is_title_prefix)
    exhaustive = find_title_spans(text, is_title)
    assert walked == exhaustive
    assert walked, "the fixture text contains titles; a scan finding none is broken"


# ---------------------------------------------------------------------------
# The held-out figure list — the honest test of the bare-surname tier
# ---------------------------------------------------------------------------


def test_held_out_full_names_almost_all_resolve() -> None:
    """The tier's easy leg, and the one worth guarding at a high floor.

    A full name is multi-token, so it cannot collide with an ordinary word and
    the sitelink floor is the only gate it has to clear. 60 figures a school
    essay actually names should nearly all be present; when they are not, the
    cause has been a *label* problem rather than a fame problem — Wikidata moving
    an English label to the ``mul`` language code (Charles Darwin), or storing a
    president under the name on the inauguration card rather than the textbook
    cover ("Franklin Delano Roosevelt" vs "Franklin Roosevelt"). Both are silent
    from inside the builder, which is why this is a test and not a docstring.
    """
    from vicary.eval import held_out_figures

    result = held_out_figures.score(gazetteer_is_notable())
    assert result.full_name_rate >= 0.90, (
        "held-out FULL NAMES have regressed:\n" + held_out_figures.render(result)
    )


def test_held_out_bare_surnames_are_measured_not_assumed() -> None:
    """The number the fixture's two bare surnames could not tell us.

    Fixture v2's KEEP frames exercise exactly two — Lincoln and Washington — and
    both clear, so KEEP precision reads 100% and generalises to nothing. Measured
    over 60 held-out figures the bare-surname rate is far lower, because
    SHORT_MIN_SITELINKS and SHORT_MAX_US_SURNAME_POPULATION are in tension by
    construction: a famous person with a common American surname cannot clear
    both gates. This asserts a floor rather than a target — the point is that the
    number exists and moves when the tier changes, and that a *drop* reds.
    """
    from vicary.eval import held_out_figures

    result = held_out_figures.score(gazetteer_is_notable())
    assert result.surname_rate >= 0.55, (
        "held-out bare-surname KEEP rate has regressed:\n"
        + held_out_figures.render(result)
    )
    # The guard needs a plausible failing case in the other direction too: a
    # gazetteer that kept everything would pass the floor above and be a
    # catastrophic redactor, so assert the list still contains destroyed entries.
    # If this ever legitimately hits zero, delete the assertion and say so.
    assert result.surname_destroyed, (
        "every held-out surname now resolves notable — either the tier was "
        "relaxed to keep-everything, or this list stopped being held out"
    )


def test_the_held_out_list_is_disjoint_from_the_fixture() -> None:
    """Held out means held out. A shared figure silently makes it a tuning set.

    Scored against every frame's whole SENTENCE, not just its span literals: a
    figure the fixture merely mentions is visible, and the frames that motivated
    this work name "Richard Wright" and "Jackie Robinson" in prose while
    labelling only the bare surname. Checking literals alone would have let both
    stay on a list calling itself held out.
    """
    from vicary.eval import held_out_figures
    from vicary.eval.fixture import frames as all_frames

    corpus = " ".join(
        frame.sentence + " " + frame.prompt_context for frame in all_frames()
    ).lower()
    for figure in held_out_figures.HELD_OUT_FIGURES:
        assert figure.full_name.lower() not in corpus, figure
        assert figure.surname.lower() not in corpus.split(), figure


def test_a_query_timeout_dressed_as_a_429_is_not_retried(monkeypatch) -> None:
    """qlever reports a timeout as HTTP 429. Retrying it burns 200s and fails.

    Red before the fix: the retry ladder treated the status code alone as the
    signal, slept 20+40+60+80s, and then raised "Too Many Requests" for a failure
    that was never about rate. The 5-9 title band failed exactly this way in 365s.
    Asserts on both halves — raises the timeout, and does NOT sleep.
    """
    import io
    import urllib.error

    slept: list[float] = []
    monkeypatch.setattr(build_gazetteer.time, "sleep", lambda s: slept.append(s))

    body = (
        b'{"exception": "Operation timed out. Last operation: '
        b'Sort (internal order) on ?cls", "resultsize": 0}'
    )

    def raise_429(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://qlever.dev/api/wikidata", 429, "Too Many Requests",
            {}, io.BytesIO(body),
        )

    monkeypatch.setattr(build_gazetteer.urllib.request, "urlopen", raise_429)

    with pytest.raises(RuntimeError, match="query timeout"):
        build_gazetteer._query("SELECT ?l ?s WHERE { ?i ?p ?o }")
    assert slept == [], (
        f"slept {slept} on a deterministic failure — the timeout was retried"
    )


def test_a_real_throttle_is_still_retried(monkeypatch) -> None:
    """The other half: a 429 with no timeout in the body must keep its backoff.

    Without this, "don't retry 429" would be an equally plausible fix and would
    reintroduce the failure the retry ladder was built for — one throttled query
    discarding three successful multi-minute fetches.
    """
    import io
    import urllib.error

    slept: list[float] = []
    monkeypatch.setattr(build_gazetteer.time, "sleep", lambda s: slept.append(s))

    attempts = {"n": 0}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"l,s\nCharles Darwin,277\n"

    def flaky(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise urllib.error.HTTPError(
                "https://qlever.dev/api/wikidata", 429, "Too Many Requests",
                {}, io.BytesIO(b"slow down"),
            )
        return _Response()

    monkeypatch.setattr(build_gazetteer.urllib.request, "urlopen", flaky)

    assert build_gazetteer._query("SELECT ?l ?s WHERE { ?i ?p ?o }") == [
        ("Charles Darwin", 277)
    ]
    assert slept == [build_gazetteer._QUERY_BACKOFF_SECONDS]


def gazetteer_is_notable():
    """The module-level predicate, so the tests read as one call."""
    from vicary.gazetteer import is_notable

    return is_notable
