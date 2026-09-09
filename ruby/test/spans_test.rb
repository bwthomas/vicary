# frozen_string_literal: true

# The port's offset arithmetic, against the shared spec.
#
# `conformance/spans.json` is generated from the Python reference and
# byte-compared against a fresh export by `tools/tests/test_conformance.py`, so
# the cases here are read rather than transcribed. Its 49 cases are of two
# kinds, and the second is why the file exists: 38 come from the fixture frames
# — real multi-pass masker output, mixed entity types, length deltas of both
# signs — and 11 are hand-built degenerates no essay produces, including the two
# where the contract is to *refuse*.
#
# **What a failure here means, and no other suite would say.** A one-character
# disagreement with the reference is a highlight landing on the wrong word in a
# student's essay. `frames_test`/`conformance` compares masked bytes and would
# be green; `redact_test` compares the restore map and would be green. Both are
# about WHAT is masked and what it is called; this is the only place that asks
# where it came from.
#
# The per-case assertions are deliberately separate from the parity loop below:
# a single `assert_equal` over the whole document reports "documents differ" and
# names nothing, where these say which case and which offset.

require "minitest/autorun"

require "vicary"

class SpansTest < Minitest::Test
  SPEC = Vicary::Conformance.load_spans
  CASES = SPEC.fetch("cases")

  def test_the_spec_is_not_empty
    # The one assertion that catches a spec this suite could otherwise pass
    # vacuously: an empty `cases` array would make every loop below a no-op and
    # print the same green as full agreement.
    refute_empty CASES
    assert_operator CASES.length, :>=, 20,
                    "spans.json shrank — #{CASES.length} cases is fewer than " \
                    "the edge table alone, so frames stopped contributing"
  end

  def test_derived_spans_match_the_reference
    CASES.each do |kase|
      spans = Vicary::Spans.derive(kase["masked"], kase["restore_map"])
      actual = spans.map do |span|
        { "orig_start" => span.orig_start, "orig_end" => span.orig_end,
          "new_start" => span.new_start, "new_end" => span.new_end }
      end
      assert_equal kase["spans"], actual, kase["case_id"]
    end
  end

  def test_to_original_matches_the_reference_at_every_probe
    CASES.each do |kase|
      spans = Vicary::Spans.derive(kase["masked"], kase["restore_map"])
      kase["to_original"].each do |(offset, expected)|
        assert_equal expected, Vicary::Spans.to_original(offset, spans),
                     "#{kase['case_id']} to_original(#{offset})"
      end
    end
  end

  def test_to_redacted_matches_the_reference_at_every_probe
    CASES.each do |kase|
      spans = Vicary::Spans.derive(kase["masked"], kase["restore_map"])
      kase["to_redacted"].each do |(offset, expected)|
        assert_equal expected, Vicary::Spans.to_redacted(offset, spans),
                     "#{kase['case_id']} to_redacted(#{offset})"
      end
    end
  end

  def test_reconstruction_matches_the_reference
    CASES.each do |kase|
      assert_equal kase["original"],
                   Vicary::Spans.original(kase["masked"], kase["restore_map"]),
                   kase["case_id"]
    end
  end

  # ------------------------------------------------------------------
  # Behaviour named directly, so a failure says which rule broke rather
  # than which case differs. Same division of labour as primitives_test.
  # ------------------------------------------------------------------

  def test_an_absent_map_yields_no_spans
    # Not "nothing was masked" — the text plainly contains a placeholder. The
    # Guardrail arm is exactly this state: masked bytes back from a service,
    # no map, so nothing can be placed and saying so is the whole contract.
    assert_empty Vicary::Spans.derive("I sat next to {NAME_1}.", {})
    assert_empty Vicary::Spans.derive("I sat next to {NAME_1}.", nil)
  end

  def test_a_partial_map_is_refused_rather_than_half_answered
    spans = Vicary::Spans.derive("{NAME_1} and {NAME_2} left.",
                                 { "{NAME_1}" => "Marguerite" })
    assert_empty spans,
                 "a map covering one of two placeholders must yield no spans: " \
                 "a caller can handle none and cannot detect a wrong offset"
  end

  def test_a_repeated_placeholder_yields_one_span_per_occurrence
    # Keyed on occurrences in the text, not on map entries. One entry, two
    # spans, and the second's original offset depends on the first's delta.
    spans = Vicary::Spans.derive("{NAME_1} saw {NAME_1}.",
                                 { "{NAME_1}" => "Deshawn" })
    assert_equal 2, spans.length
    assert_equal 13, spans[1].new_start
    assert_equal 12, spans[1].orig_start
  end

  def test_delta_signs_both_ways
    wider = Vicary::Spans.derive("{NAME_1} won.", { "{NAME_1}" => "Bo" })
    assert_equal 6, wider.first.delta
    narrower = Vicary::Spans.derive("{NAME_1} won.",
                                    { "{NAME_1}" => "Bartholomew Okonkwo" })
    assert_equal(-11, narrower.first.delta)
  end

  def test_an_offset_inside_a_placeholder_collapses_to_the_span_start
    spans = Vicary::Spans.derive("{NAME_1} won.", { "{NAME_1}" => "Bo" })
    (0..7).each do |inside|
      assert_equal 0, Vicary::Spans.to_original(inside, spans),
                    "offset #{inside} is inside the placeholder"
    end
    assert_equal 2, Vicary::Spans.to_original(8, spans)
  end

  def test_the_translations_are_inverse_at_span_boundaries
    CASES.each do |kase|
      spans = Vicary::Spans.derive(kase["masked"], kase["restore_map"])
      spans.each do |span|
        assert_equal span.new_start,
                     Vicary::Spans.to_redacted(span.orig_start, spans),
                     "#{kase['case_id']} start round trip"
        assert_equal span.new_end,
                     Vicary::Spans.to_redacted(span.orig_end, spans),
                     "#{kase['case_id']} end round trip"
      end
    end
  end
end
