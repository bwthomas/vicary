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
# fails the build. It is expressed over the 38 frames that require masking rather
# than all 54, because 16 frames expect nothing to be masked and a do-nothing
# implementation matches every one — a ratchet over 52 would start at 16 and read
# as progress.
#
# **Completeness is a separate, visible item.** `skip` reports it every run
# without failing CI, so the gap is impossible to lose track of and impossible to
# mistake for done.

require "minitest/autorun"

require "vicary"

class ConformanceTest < Minitest::Test
  # Raise this when detector work lands. Never lower it to make a build pass.
  MATCHED_REQUIRING_MASKING_RATCHET = 38

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

  # The bare-surname exposure, measured from the shipped surname table.
  #
  # Read here rather than in `gates.rb` so that module stays free of the
  # filesystem, and rescued so a malformed or unreadable copy costs this run one
  # NOT MEASURED gate instead of the whole scoreboard — the reader itself is
  # tested in `gates_test.rb`, where a bad table is supposed to raise.
  #
  # No longer conditional on an operator file: the surname table ships in
  # `conformance/census/`, so this resolves on any checkout.
  def self.bare_surname_exposure
    return @bare_surname_exposure if defined?(@bare_surname_exposure)

    @bare_surname_exposure = begin
      Vicary::Census.measure(Vicary::Census.load_census).rate
    rescue StandardError => e
      puts "  census file unreadable, gate stays NOT MEASURED: #{e.message}"
      nil
    end
  end

  # The three corpus gates, measured when the operator has supplied an essay
  # corpus. Rescued for the same reason the census read is: a mis-configured
  # corpus should cost this run three NOT MEASURED gates, not the whole
  # scoreboard.
  def self.corpus_metrics
    return @corpus_metrics if defined?(@corpus_metrics)

    @corpus_metrics = begin
      Vicary::Corpus.measure_from_config(spec) { |t, i| Vicary.redact(t, i) }
    rescue StandardError => e
      puts "  corpus unreadable, 3 gates stay NOT MEASURED: #{e.message}"
      nil
    end
  end

  def self.gate_report
    @gate_report ||= Vicary::Gates.measure(
      spec, gates, asset_entries: Vicary::Gazetteer.load.entry_count,
                   bare_surname_exposure: bare_surname_exposure,
                   held_out_recall_carrier: corpus_metrics&.recall_held_out,
                   over_fire_per_essay: corpus_metrics&.over_fire_spans_per_essay,
                   **(if corpus_metrics.nil?
                        {}
                      else
                        Vicary::LatencyBaseline.gate_fields(
                          corpus_metrics.latency_pooled_median_ms,
                          Vicary::Corpus.resolve_corpus_id
                        )
                      end),
                   # Which corpus these came from, so the over-fire gate is held
                   # to that corpus's bar rather than to ASAP-AES's everywhere.
                   corpus_id: corpus_metrics.nil? ? nil : Vicary::Corpus.resolve_corpus_id
    ) { |sentence, identity| Vicary.redact(sentence, identity) }
  end

  # Printed unconditionally, including on a green run. The report is the artifact;
  # a pass with no numbers is the state this project has a written rule against.
  Minitest.after_run do
    puts
    puts Vicary::Conformance.report(board, gates, Vicary::Gates.report(gate_report))
  end

  def test_the_spec_loads_with_every_frame_and_its_golden_output
    assert_equal 54, self.class.spec.frames.size
    assert_equal 54, self.class.spec.golden.size
    assert_equal "2026-08-11.2", self.class.spec.fixture_version
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
    assert_equal 54, board.total
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
    # Was a `skip` while the detector was unported — reported every run, failing
    # none. It is a hard assertion now that all 52 match: the completeness item
    # existed to keep the gap visible, and a gap that is closed should fail the
    # build if it reopens rather than go back to being a note.
    board = self.class.board
    failures = board.outcomes.reject(&:matched).map do |o|
      "#{o.frame_id}: expected #{o.expected.inspect}, got #{o.produced.inspect}" \
        "#{o.error ? " (#{o.error})" : ''}"
    end
    assert_empty failures
  end

  def test_placeholders_are_numbered_in_the_references_order
    # The bytes matching already implies this, and it is asserted separately
    # anyway: a diff on this list names the defect ("{NAME_1} and {NAME_2} are
    # swapped") where a diff on a whole sentence only shows that one exists.
    # Numbering is where ports diverge first, so it gets its own failure message.
    spec = self.class.spec
    wrong = spec.frames.filter_map do |frame|
      golden = spec.golden[frame.frame_id]
      masked, _n, restore_map = Vicary.redact_with_report(frame.sentence, spec.identity)
      # Order of first appearance IN THE TEXT, which is what the reference's
      # `align()` records — NOT mint order. The two differ, and the difference is
      # the whole point: the minter hands out indices in discovery order, and
      # candidate generation discovers right to left, so a sentence whose second
      # name is masked first reads "{NAME_2} … {NAME_1}". That is correct output
      # and the golden says so.
      emitted = restore_map.keys.select { |p| masked.include?(p) }
                           .sort_by { |p| masked.index(p) }
      next if emitted == golden.placeholders

      "#{frame.frame_id}: expected #{golden.placeholders.inspect}, got #{emitted.inspect}"
    end
    assert_empty wrong
  end

  def test_the_restore_map_puts_every_frames_original_bytes_back
    # The property numbering exists to buy, and the one the golden's `mapping`
    # records. Masking that cannot be undone is a different product: the caller
    # sends placeholders to a model and has no way to render the reply.
    spec = self.class.spec
    broken = spec.frames.filter_map do |frame|
      masked, _n, restore_map = Vicary.redact_with_report(frame.sentence, spec.identity)
      back = Vicary.restore(masked, restore_map)
      next if back == frame.sentence

      "#{frame.frame_id}: restored #{back.inspect}, expected #{frame.sentence.inspect}"
    end
    assert_empty broken
  end
end
