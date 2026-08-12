"""The shared spec, and the four ways it can quietly stop being shared.

``conformance/frames.json`` and ``conformance/gates.json`` are what the
TypeScript and Ruby ports run against. They are *generated* from the Python
literals, which makes transcription error impossible and drift the only remaining
failure mode — so drift is what this file gates, in four directions:

1. the committed JSON is byte-identical to a fresh export (edit a frame, forget
   to regenerate, the build goes red);
2. reading the JSON back reproduces the Python objects exactly, so the file is
   lossless rather than merely plausible;
3. the reference arm re-run over the JSON-loaded frames reproduces the golden
   masked bytes, **placeholder numbering included** — the property no semantic
   expectation can express;
4. every bar in ``gates.json`` equals the constant the Python gate actually
   asserts, because a spec that says 0.60 while the gate asserts 0.72 gives the
   ports a bar nobody holds.

Numbering deserves its own note, since it is the thing most likely to be dismissed
as an implementation detail. In ``nickname-and-full-name`` the reference output
emits ``{NAME_2}`` *before* ``{NAME_1}`` — numbering does not follow position in
the text. Any port that assumes it does passes every semantic check and produces a
restoration mapping that is wrong for the two frames where it matters.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vicary.eval import conformance
from vicary.eval import fixture as fx


@pytest.fixture(scope="module")
def conformance_directory() -> Path:
    directory = conformance.conformance_dir()
    # Not a skip. Outside a checkout there is genuinely nothing to compare, but
    # inside one a missing directory means the spec was deleted or moved, and
    # skipping would report that as a pass. The suite runs from a checkout.
    assert directory is not None, (
        "no conformance/ directory found above vicary.eval.conformance — the "
        "shared spec is missing, and every port is now checking itself against "
        "nothing"
    )
    return directory


@pytest.fixture(scope="module")
def committed_frames(conformance_directory: Path) -> dict:
    return json.loads(
        (conformance_directory / conformance.FRAMES_FILENAME).read_text("utf-8")
    )


# ---------------------------------------------------------------------------
# 1. The committed file is the current export.
# ---------------------------------------------------------------------------


def test_the_committed_frames_file_is_a_current_export(
    conformance_directory: Path,
) -> None:
    on_disk = (conformance_directory / conformance.FRAMES_FILENAME).read_text("utf-8")
    fresh = conformance.dumps(conformance.build_frames_document())
    assert on_disk == fresh, (
        "conformance/frames.json is stale — the fixture or the detector changed "
        "and the spec the ports run against did not. Regenerate with "
        "`just sync-conformance` and READ THE DIFF: a changed `golden` block "
        "means output changed, which is either the improvement you intended or "
        "a regression the ports would have inherited."
    )


def test_the_committed_gates_file_is_a_current_export(
    conformance_directory: Path,
) -> None:
    on_disk = (conformance_directory / conformance.GATES_FILENAME).read_text("utf-8")
    fresh = conformance.dumps(conformance.build_gates_document())
    assert on_disk == fresh, (
        "conformance/gates.json is stale — regenerate with `just sync-conformance`"
    )


def test_the_committed_primitives_file_is_a_current_export(
    conformance_directory: Path,
) -> None:
    on_disk = (
        conformance_directory / conformance.PRIMITIVES_FILENAME
    ).read_text("utf-8")
    fresh = conformance.dumps(conformance.build_primitives_document())
    assert on_disk == fresh, (
        "conformance/primitives.json is stale — a tokenisation or capitalisation "
        "primitive changed and the file the ports check themselves against did "
        "not. Regenerate with `just sync-conformance` and READ THE DIFF: unlike "
        "`golden`, a change here is invisible in masked output until it reaches a "
        "frame, so this file is where a port finds out first."
    )


def test_every_primitive_case_covers_the_whole_corpus() -> None:
    """A section that skips an input is a hole no port can see.

    The document is a lookup table, so a missing key reads to a port as "nothing
    to check here" rather than as a gap. That is the same silent-shrinkage failure
    the asset's declared tier counts exist to prevent, one layer up.
    """
    document = conformance.build_primitives_document()
    corpus = set(document["corpus"])
    lists = set(document["token_lists"])
    spans = set(document["span_cases"])
    names = set(document["name_forms"])
    over_lists = {
        "trim", "classify", "classify_with_settlement",
        "classify_tags", "classify_tags_with_settlement", "masks_with_settlement",
    }
    # The surname functions take a name, not a text, a token list or a span — a
    # fourth input group, listed for the same reason `over_spans` is.
    over_names = {"surname_tokens", "bare_surname_key", "surname_forms"}
    # The relation predicates take ``(text, start, end)``, so they are keyed by
    # `span_cases` rather than by the corpus. Listed explicitly rather than
    # inferred from the keys, because inferring which input group a section
    # belongs to from the section's own keys is how a section that covers the
    # wrong group passes: it would be compared against whatever it happens to
    # match.
    over_spans = {
        "names_someone_in_the_writers_life", "names_someone_the_writer_knows",
        "title_is_the_writers_own_relation",
        "relation_led_title_is_internally_mixed",
    }

    for section, cases in document["cases"].items():
        if section == "is_stop":
            assert set(cases) == set(document["stop_tokens"]), section
        elif section in over_lists:
            assert set(cases) == lists, section
        elif section in over_spans:
            assert set(cases) == spans, section
        elif section in over_names:
            assert set(cases) == names, section
        else:
            assert set(cases) == corpus, section


def test_the_primitive_corpus_exercises_every_capitalisation_state() -> None:
    """Four states, and a corpus that reaches three of them pins three.

    The state a corpus never produces is the state a port can get wrong for free —
    and `silent` is the one with a written rule against reading it as consent, so
    it is the one most worth having an example of.
    """
    document = conformance.build_primitives_document()
    observed = set(document["cases"]["capitalisation_habit"].values())
    assert observed == {"consistent", "inconsistent", "lowercase", "silent"}


# ---------------------------------------------------------------------------
# 2. The file is lossless.
# ---------------------------------------------------------------------------


def test_reading_the_document_back_reproduces_every_frame(
    committed_frames: dict,
) -> None:
    """Field-for-field, not count-for-count.

    The export omits defaulted span fields to stay readable, so "54 frames in,
    54 frames out" is satisfied by a reader that drops `kept_by` on every span.
    Comparing the dataclasses is what makes the omission safe.
    """
    rebuilt = conformance.frames_from_document(committed_frames)
    assert rebuilt == fx.ALL_FRAMES


def test_the_document_carries_the_identity_the_detector_is_told(
    committed_frames: dict,
) -> None:
    """A port that omits these measures a different system.

    The student's own name is *given* to every arm, and interpolating it is the
    one leg that reaches 100% trivially. A port running without it misses the
    easiest spans in the fixture and reads as a porting bug when it is a
    configuration one.
    """
    identity = fx.fixture_identity()
    assert committed_frames["identity"] == {
        "first_name": identity.first_name,
        "last_name": identity.last_name,
        "school_name": identity.school_name,
    }


def test_the_document_names_the_arm_its_golden_output_came_from(
    committed_frames: dict,
) -> None:
    """Golden bytes without an arm are unreproducible: the same fixture through
    `local` rather than `local-gazetteer-lowercase` masks different spans, and a
    port comparing against these bytes while implementing that arm would be
    measuring two differences at once."""
    assert committed_frames["reference_arm"] == conformance.REFERENCE_ARM
    assert committed_frames["fixture_version"] == fx.FIXTURE_VERSION


# ---------------------------------------------------------------------------
# 3. The golden output still reproduces — bytes, and numbering.
# ---------------------------------------------------------------------------


@pytest.mark.gates
def test_the_reference_arm_reproduces_the_golden_masked_bytes(
    committed_frames: dict,
) -> None:
    """Python runs the conformance suite the way a port does: off the file.

    Marked as a gate because it loads the 2.1 MB gazetteer, and because it is a
    measurement of the shipped detector rather than a unit assertion.
    """
    from vicary.eval.recall import build_redactor

    redactor = build_redactor(conformance.REFERENCE_ARM, None)
    golden = committed_frames["golden"]
    mismatched: list[str] = []
    for frame in conformance.frames_from_document(committed_frames):
        produced = redactor._apply(frame.sentence, source="INPUT").text
        if produced != golden[frame.frame_id]["masked"]:
            mismatched.append(
                f"{frame.frame_id}: expected {golden[frame.frame_id]['masked']!r}, "
                f"got {produced!r}"
            )
    assert not mismatched, "golden output diverged:\n  " + "\n  ".join(mismatched)


def test_the_golden_output_pins_placeholder_numbering_where_it_is_not_positional(
    committed_frames: dict,
) -> None:
    """At least one frame must number against text order.

    This is the assertion that keeps the golden layer honest. If every frame's
    placeholders happened to run 1, 2, 3 left to right, a port could assume
    positional numbering, pass all 54 frames, and still be wrong the first time a
    real essay hands it a nickname before the full name it belongs to. The
    fixture does contain such a frame; this fails if it ever stops containing one,
    because then the suite has stopped testing the risk it was built for.
    """
    golden = committed_frames["golden"]

    def is_positional(tokens: list[str]) -> bool:
        numbers = [int(t.rsplit("_", 1)[1].rstrip("}")) for t in tokens]
        return numbers == sorted(numbers)

    non_positional = {
        frame_id: entry["placeholders"]
        for frame_id, entry in golden.items()
        if len(entry["placeholders"]) > 1 and not is_positional(entry["placeholders"])
    }
    assert non_positional, (
        "no frame numbers its placeholders against text order any more, so the "
        "suite no longer proves a port cannot assume positional numbering. Either "
        "the detector's numbering changed, or a frame was removed; both need a "
        "look before this assertion is relaxed."
    )


def test_every_frame_has_golden_output(committed_frames: dict) -> None:
    frame_ids = {f["frame_id"] for f in committed_frames["frames"]}
    golden_ids = set(committed_frames["golden"])
    assert frame_ids == golden_ids, (
        f"frames without golden output: {sorted(frame_ids - golden_ids)}; "
        f"golden output for unknown frames: {sorted(golden_ids - frame_ids)}"
    )


def test_the_golden_mapping_restores_the_original(committed_frames: dict) -> None:
    """The mapping is the product, not a debugging aid.

    Numbered placeholders exist so a student can be shown their own words back.
    A port can reproduce `masked` exactly and still emit a mapping that does not
    reconstruct the input, so the spec asserts reconstruction rather than
    trusting that matching bytes imply it.
    """
    failed: list[str] = []
    for frame in committed_frames["frames"]:
        entry = committed_frames["golden"][frame["frame_id"]]
        restored = fx.restore(entry["masked"], dict(entry["mapping"]))
        if restored != frame["sentence"]:
            failed.append(f"{frame['frame_id']}: {restored!r}")
    assert not failed, "golden mapping does not restore the original:\n  " + \
        "\n  ".join(failed)


# ---------------------------------------------------------------------------
# 4. The published bars are the asserted bars.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def committed_gates(conformance_directory: Path) -> dict:
    return json.loads(
        (conformance_directory / conformance.GATES_FILENAME).read_text("utf-8")
    )


# Whether each port's own asserted bar matches the published one is that port's
# question, not the generator's, and it is asked once per front door:
# `python/tests/test_gates.py`, `typescript/test/gates.test.ts` and
# `ruby/test/gates_test.rb` each read `gates.json` and compare it to the constants
# they assert against. Asking it here as well would have this suite import a front
# door, which is the coupling the split exists to remove.


def test_the_spec_says_which_gates_declare_a_data_requirement(
    committed_gates: dict,
) -> None:
    """Four of nine declare a `requires`, and both requirements are now satisfied
    from the repository — `persuade-20` for the corpus, `conformance/census/` for
    the surname table — so a bare checkout reaches all nine.

    The declaration still has to be carried, and named. A port that drops the
    distinction cannot report NOT MEASURED for the next gate whose data goes out
    of reach; it would publish a green badge meaning "eight gates held" while the
    Python one means nine, and nobody notices because both badges are the same
    colour. `requires` says what a gate depends on, not whether it is reachable
    today."""
    needs_data = {
        g["label"] for g in committed_gates["gates"] if g["requires"]
    }
    assert needs_data == {
        "held-out recall (carrier)",
        "over-fire on prose",
        "bare-surname exposure",
        "latency p95",
    }
    declared = set(committed_gates["requirements"])
    for gate in committed_gates["gates"]:
        unknown = set(gate["requires"]) - declared
        assert not unknown, (
            f"{gate['label']} requires {sorted(unknown)}, which the document "
            f"does not describe — a port cannot tell an operator what to supply"
        )
