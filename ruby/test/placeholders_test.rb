# frozen_string_literal: true

# The port's placeholder guard, against the shared spec.
#
# `conformance/placeholders.json` is generated from the Python reference and
# byte-compared against a fresh export by `tools/tests/test_conformance.py`, so
# the cases are read rather than transcribed. The pattern's SOURCE is compared
# first: all three ports compile the same bytes, and a port whose pattern
# drifted fails there, naming the cause, before it fails on a case.

require "minitest/autorun"

require "vicary"

class PlaceholdersTest < Minitest::Test
  SPEC = Vicary::Conformance.load_placeholders
  CASES = SPEC.fetch("cases")
  P = Vicary::Placeholders

  def test_the_spec_is_not_empty
    assert_operator CASES.length, :>=, 30
    assert(CASES.any? { |c| c["leaks"] })
    assert(CASES.any? { |c| !c["leaks"] })
  end

  def test_the_pattern_source_is_the_references_byte_for_byte
    assert_equal SPEC["pattern"], P::PLACEHOLDER_LIKE.source
    assert_equal SPEC["sentence_end"], P::SENTENCE_END.source
    assert_equal SPEC["kinds"], P::PLACEHOLDER_KINDS
    assert_equal SPEC["other_kind"], P::OTHER_KIND
    assert_equal SPEC["generic_stand_ins"], P::GENERIC_STAND_INS
  end

  def test_matches_kinds_and_offsets_match_the_reference
    CASES.each do |kase|
      assert_equal kase["matches"],
                   Vicary.find_placeholders(kase["text"]).map(&:to_h),
                   kase["case_id"]
      assert_equal kase["leaks"], Vicary.has_placeholders?(kase["text"]),
                   kase["case_id"]
    end
  end

  def test_both_scrubs_match_the_reference
    CASES.each do |kase|
      prose = Vicary.scrub_placeholders(kase["text"])
      assert_equal kase["scrub_prose"],
                   { "text" => prose.text, "removed" => prose.removed },
                   "#{kase['case_id']} (prose)"
      quote = Vicary.scrub_placeholders(kase["text"], quote: true)
      assert_equal kase["scrub_quote"],
                   { "text" => quote.text, "removed" => quote.removed },
                   "#{kase['case_id']} (quote)"
    end
  end

  def test_every_kind_vicary_can_mint_is_caught_in_its_own_grammar
    P::PLACEHOLDER_KINDS.each do |kind|
      ["{#{kind}}", "{#{kind}_1}", "{#{kind}_42}"].each do |token|
        assert_equal [token], Vicary.find_placeholders("a #{token} b").map(&:text)
        assert_equal kind, P.kind_of(token)
      end
    end
  end

  def test_the_error_names_kinds_and_count_never_the_text
    text = "Priya told {NAME_1} at [EMAIL] about {JOHN} and {NAME_2}."
    error = assert_raises(Vicary::PlaceholderLeakError) do
      Vicary.assert_no_placeholders(text)
    end
    assert_kind_of ArgumentError, error
    assert_equal ["EMAIL", "NAME", P::OTHER_KIND], error.kinds
    assert_equal 4, error.count
    %w[Priya told JOHN {NAME_1}].each do |fragment|
      refute_includes error.message, fragment
    end
  end

  def test_clean_text_passes_through_unchanged
    text = "A clear thesis. Ms. Name would agree."
    assert_same text, Vicary.assert_no_placeholders(text)
    result = Vicary.scrub_placeholders(text)
    assert_same text, result.text
    assert_equal 0, result.removed
  end
end
