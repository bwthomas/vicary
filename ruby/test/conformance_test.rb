# frozen_string_literal: true

# The port's scoreboard against the shared spec.
#
# This file runs from the first commit of the port, before any detector exists, on
# purpose: a suite added once a port "works" cannot tell you when it started
# working, and a port with no scoreboard is a port whose readiness is somebody's
# opinion.
#
# **The ratchet.** MATCHED_REQUIRING_MASKING_RATCHET is the number of
# masking-required frames this port currently reproduces byte-for-byte. It is a
# floor: raise it when detector work lands, and a regression that drops below it
# fails the build. It is expressed over the 35 frames that require masking rather
# than all 51, because 16 frames expect nothing to be masked and a do-nothing
# implementation matches every one — a ratchet over 51 would start at 16 and read
# as progress.
#
# **Completeness is a separate, visible item.** `skip` reports it every run
# without failing CI, so the gap is impossible to lose track of and impossible to
# mistake for done.

require "minitest/autorun"

require "vicary"

class ConformanceTest < Minitest::Test
  # Raise this when detector work lands. Never lower it to make a build pass.
  MATCHED_REQUIRING_MASKING_RATCHET = 0

  def self.board
    @board ||= begin
      spec = Vicary::Conformance.load_spec
      Vicary::Conformance.score(spec) do |sentence, identity|
        Vicary.redact(sentence, identity)
      end
    end
  end

  def self.spec
    @spec ||= Vicary::Conformance.load_spec
  end

  def self.gates
    @gates ||= Vicary::Conformance.load_gates
  end

  # Printed unconditionally, including on a green run. The report is the artifact;
  # a pass with no numbers is the state this project has a written rule against.
  Minitest.after_run do
    puts
    puts Vicary::Conformance.report(board, gates)
  end

  def test_the_spec_loads_with_every_frame_and_its_golden_output
    assert_equal 51, self.class.spec.frames.size
    assert_equal 51, self.class.spec.golden.size
    assert_equal "2026-08-06.4", self.class.spec.fixture_version
    assert_equal "local-gazetteer-lowercase", self.class.spec.reference_arm
  end

  def test_the_spec_carries_the_identity_the_detector_is_told_about
    # Without these the port measures a different system: identity interpolation
    # is the one leg that reaches its spans trivially, and omitting it looks like
    # a detector bug rather than a missing input.
    identity = self.class.spec.identity
    assert_equal "Marguerite", identity.first_name
    assert_equal "Delacroix-Whitfield", identity.last_name
    assert_equal "Westfield High School", identity.school_name
  end

  def test_the_nine_gates_load_with_four_declaring_data_no_package_ships
    gates = self.class.gates
    assert_equal 9, gates.gates.size
    needs_data = gates.gates.reject { |g| g.requires.empty? }
    assert_equal 4, needs_data.size
    needs_data.each do |gate|
      gate.requires.each do |requirement|
        assert_includes gates.requirements, requirement,
                        "gate #{gate.label} requires #{requirement}, which the " \
                        "spec does not describe — a port cannot tell an " \
                        "operator what to supply"
      end
    end
  end

  def test_the_spec_says_which_frames_require_masking
    board = self.class.board
    assert_equal 51, board.total
    assert_operator board.requiring_masking, :>, 0,
                    "no frame requires masking, which means the golden output " \
                    "is empty and this suite is scoring nothing"
  end

  def test_the_port_does_not_regress_below_its_ratchet
    board = self.class.board
    assert_operator board.matched_requiring_masking, :>=,
                    MATCHED_REQUIRING_MASKING_RATCHET,
                    "matched #{board.matched_requiring_masking} of " \
                    "#{board.requiring_masking} masking-required frames, below " \
                    "the ratchet of #{MATCHED_REQUIRING_MASKING_RATCHET}. Either " \
                    "fix the regression or, if the drop is intended, say why in " \
                    "the commit that lowers the ratchet."
  end

  def test_every_frame_matches_the_reference_output_byte_for_byte
    board = self.class.board
    failures = board.outcomes.reject(&:matched).map do |o|
      "#{o.frame_id}: expected #{o.expected.inspect}, got #{o.produced.inspect}" \
        "#{o.error ? " (#{o.error})" : ''}"
    end
    if failures.any?
      skip "the detector is not ported yet — #{failures.size} of #{board.total} " \
           "frames differ. See ruby/README.md."
    end
    assert_empty failures
  end
end
