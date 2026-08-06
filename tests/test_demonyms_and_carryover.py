"""Guards for the 2026-08-06 cut: the demonym tier, the cross-leg carry-over,
and the two build-plumbing defects that made both of them invisible.

Every test here was red against the defect it names before the fix landed. The
build-plumbing pair matter most: neither the demonym tier nor anything else added
to :func:`build_tiers` could reach a running process, and *nothing failed* — the
rebuild wrote to a path nothing reads and the writer skipped the new tier, so a
2.1 MB asset shipped with a tier the loader saw as empty. An empty KEEP tier
redacts everything it was built to protect, which reads as over-aggressive tuning
rather than as a missing file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vicary import (
    StudentIdentity,
    assets,
    build_redactor_if_enabled,
    gazetteer,
)
from vicary.build import gazetteer as builder

# --- build plumbing ---------------------------------------------------------


def test_the_builder_writes_where_the_runtime_reads() -> None:
    """The silent no-op rebuild.

    ``ASSET_RELPATH`` was resolved against the *build* module's directory, so a
    full Wikidata rebuild wrote ``src/vicary/build/data/notability.txt.gz`` while
    the loader and ``MANIFEST.json`` both read ``src/vicary/data/``. ``fetch``
    then rewrote the manifest by checksumming the old asset and ``verify``
    compared that asset against its own fresh checksum and passed. Hours of
    fetching, a success message, and not one byte of the shipped asset changed.
    """
    assert builder.default_out() == assets.resolve()[0]


def test_every_tier_the_fold_produces_survives_the_write(tmp_path: Path) -> None:
    """``write_asset`` iterated a hardcoded five-tier list.

    A tier added to :func:`build_tiers` and not to that list built cleanly,
    reported its entry count in the build log, and read back as an empty
    frozenset. Red before the fix: ``demonym`` was 1,044 entries in the log and 0
    in the loaded gazetteer.

    Asserted against the fold's own output rather than a literal tier list, so
    the *next* tier is covered by this test without anybody remembering to
    extend it — which is the failure mode that produced the bug.
    """
    tiers = builder.build_tiers(
        humans=[("Ada Lovelace", 120), ("Narciso Rodriguez", 14)],
        places=[("Delaware", 220)],
        census={"lovelace": 500},
        titles=[("Harry Potter", 90)],
        demonyms=[("Cuban", 1)],
    )
    asset = tmp_path / "notability.txt.gz"
    builder.write_asset(asset, tiers, {})
    parsed = gazetteer.load(asset, force=True)
    try:
        for tier, entries in tiers.items():
            assert getattr(parsed, tier, None) == frozenset(entries), (
                f"tier {tier!r} did not survive write -> parse"
            )
    finally:
        gazetteer.load(force=True)


def test_the_shipped_asset_carries_a_populated_demonym_tier() -> None:
    """The end-to-end version of the two above, on the real asset."""
    assert len(gazetteer.load().demonym) > 500


# --- the demonym tier -------------------------------------------------------


def test_a_nationality_adjective_is_not_a_name() -> None:
    """``Cuban`` was 1 of the 3 residual over-fires on real Stage-5 feedback:
    "connecting your family's story to your Cuban heritage" came back with a
    ``{NAME}`` in it."""
    gaz = gazetteer.load()
    assert gaz.notability("Cuban") == gazetteer.DEMONYM
    assert gaz.is_notable("Nigerian")
    assert gaz.is_notable("Irish")


@pytest.mark.parametrize("token", ["horner", "english", "welsh", "thai"])
def test_a_demonym_that_is_a_common_surname_is_subtracted(token: str) -> None:
    """``Horner`` is a demonym of Horn AND 23,881 Americans' surname. Keeping it
    would stop a coach named Horner redacting, which is why this tier's Census
    bar is 10,000 rather than the short tier's 25,000."""
    assert token not in gazetteer.load().demonym


@pytest.mark.parametrize("token", ["french", "german", "roman", "dane"])
def test_a_demonym_that_is_a_common_given_name_is_subtracted(token: str) -> None:
    """The ``given`` tier is a REDACT signal and has to win: it is evidence the
    token names a person."""
    assert token not in gazetteer.load().demonym


def test_a_relation_overrides_a_demonym_keep() -> None:
    """``Cornish`` is a demonym and 8,050 Americans' surname — below the Census
    bar, so the tier keeps it. "My coach Cornish taught me to swim" leaked until
    ``demonym`` joined :data:`OVERRIDABLE_TIERS`.
    """
    redactor = build_redactor_if_enabled("local")
    assert redactor is not None
    leaked = redactor.redact_outbound("My coach Cornish taught me to swim.").text
    assert "Cornish" not in leaked
    # ...and the nationality reading is untouched, which is the whole tier.
    kept = redactor.redact_outbound("I am proud of my Cuban heritage.").text
    assert "Cuban" in kept


# --- the cross-leg carry-over -----------------------------------------------

ESSAY = (
    "Narciso Rodriguez wrote about his home and family. "
    "Narciso was grateful to his parents for what they gave him."
)
FEEDBACK = "You're introducing who Narciso is and where his story begins."


def _two_leg(essay: str, feedback: str,
             identity: StudentIdentity | None = None) -> str:
    redactor = build_redactor_if_enabled("local", identity=identity)
    assert redactor is not None
    redactor.redact_inbound(essay)
    return redactor.redact_outbound(feedback).text


def test_a_first_name_the_essay_established_survives_the_feedback() -> None:
    """The defect: a student reads "introducing who {NAME} is" about the author
    they just wrote about. No lookup fixes it — the ``given`` tier is built FROM
    the first tokens of the full tier, so every entry heads a notable name."""
    assert "Narciso" in _two_leg(ESSAY, FEEDBACK)


def test_the_possessive_form_is_carried_too() -> None:
    """``Narciso's`` was a separate over-fire span from ``Narciso`` and survived
    the keep that had already rescued its bare neighbour."""
    assert "Narciso's" in _two_leg(ESSAY, "You back up Narciso's gratitude well.")


def test_the_carry_is_refused_when_the_writer_shares_the_name() -> None:
    """Identity masking is exact-match, not gazetteer-driven, so a student named
    Narciso never enters the notable set and must not be un-masked by a token
    carried from the designer."""
    out = _two_leg(ESSAY, FEEDBACK,
                   StudentIdentity(first_name="Narciso", last_name="Okonkwo"))
    assert "Narciso" not in out


def test_the_carry_is_refused_when_a_private_full_name_competes() -> None:
    """Two roles for one token in one document is an ambiguity, not evidence."""
    out = _two_leg(ESSAY + " My cousin Narciso Delgado came over.", FEEDBACK)
    assert "Narciso" not in out


def test_a_bare_mention_alone_does_not_cancel_the_carry() -> None:
    """The bug that made the first version of this measure exactly zero.

    Subtracting *any* non-notable candidate counted the essay's own bare
    "Narciso" — not notable on its own, which is the entire premise — as evidence
    of a private person, so the keep cancelled itself. A bare mention is the
    symptom; only a competing full name is evidence. Red before the narrowing:
    ``ESSAY`` contains exactly that bare mention.
    """
    assert "Narciso" in ESSAY.replace("Narciso Rodriguez", "")
    assert "Narciso" in _two_leg(ESSAY, FEEDBACK)


def test_the_carry_is_inert_without_a_notability_oracle() -> None:
    """A host on the identity-only level establishes nothing."""
    redactor = build_redactor_if_enabled("local", names="identity")
    assert redactor is not None
    assert redactor.carried_keeps(ESSAY) == frozenset()


def test_the_carry_can_be_turned_off() -> None:
    from vicary.redaction import Redactor

    gaz = gazetteer.load()
    redactor = Redactor(
        local=True, local_candidates=True, notable=gaz.is_notable,
        notability_tier=gaz.notability, given_name=gaz.is_common_given_name,
        title=gaz.is_title, title_prefix=gaz.is_title_prefix,
        carry_notable_keeps=False,
    )
    redactor.redact_inbound(ESSAY)
    assert "Narciso" not in redactor.redact_outbound(FEEDBACK).text
