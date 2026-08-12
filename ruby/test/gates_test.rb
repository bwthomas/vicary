# frozen_string_literal: true

# The gates this port measures, and the machinery that measures them.
#
# Five of the nine need no operator-supplied data. Their values are checked
# against the Python gate report rather than against a hand-written expectation —
# held-out recall 16/16, KEEP precision 21/21, round-trip 54/54, unaccounted
# violations 0, asset entries 360,793. A port that agrees only with itself proves
# nothing about the claim the repository makes.
#
# The remaining four stay NOT MEASURED and are asserted to stay that way: a gate
# silently reduced out of the denominator is how "five of nine" becomes "all
# green" without anybody deciding it should.
#
# **Why the hand-built frames below carry their weight.** The fixture produces no
# `wrong-type` violation and only one `leak`, so most invariants here are never
# exercised by scoring the spec — in the TypeScript port, deleting the
# `wrong-type` check outright left the whole suite green. Each probe frame is the
# negative control for one invariant.

require "minitest/autorun"

require "vicary"

class GatesTest < Minitest::Test
  def self.report
    @report ||= Vicary::Gates.measure(
      spec, Vicary::Conformance.load_gates,
      asset_entries: Vicary::Gazetteer.load.entry_count
    ) { |sentence, identity| Vicary.redact(sentence, identity) }
  end

  def self.spec
    @spec ||= Vicary::Conformance.load_spec
  end

  def measurement(id)
    self.class.report.measurements.find { |m| m.gate.id == id }
  end

  # A frame carrying only what an invariant check reads.
  def frame(sentence, spans: [], held_out: false)
    Vicary::Conformance::Frame.new(
      frame_id: "probe", group: "probe", sentence: sentence, spans: spans,
      held_out: held_out, prompt_context: "", note: "",
    )
  end

  def span(entity:, literal:, verdict: "redact", expect: nil, expect_count: nil)
    Vicary::Conformance::Span.new(
      entity: entity, literal: literal, verdict: verdict,
      expect_count: expect_count, expect: expect, kept_by: "notability",
      redacted_by: "absence", note: "",
    )
  end

  # -------------------------------------------------------------------------
  # The gates
  # -------------------------------------------------------------------------

  def test_every_gate_needing_no_operator_data_is_measured_and_holds
    measured = self.class.report.measurements.reject { |m| m.passed.nil? }
    assert_equal 5, measured.size
    failed = measured.reject(&:passed)
                     .map { |m| "#{m.gate.label} measured #{m.value} #{m.gate.unit}: #{m.detail}" }
    assert_equal [], failed
  end

  def test_the_measured_values_match_the_python_gate_report
    # Reconciled against `pytest tests/test_gates.py -s`, not against a number
    # typed here from memory. Counts as well as percentages, because 100% of a
    # wrong denominator is still 100%.
    assert_in_delta 100.0, measurement("held_out_recall").value
    assert_match(%r{\A16/16 }, measurement("held_out_recall").detail)
    assert_in_delta 100.0, measurement("keep_precision").value
    assert_match(%r{\A21/21 }, measurement("keep_precision").detail)
    assert_in_delta 100.0, measurement("round_trip").value
    assert_match(%r{\A54/54 }, measurement("round_trip").detail)
    assert_equal 0, measurement("unaccounted_violations").value
    assert_equal 360_793, measurement("asset_entries").value
  end

  def test_the_four_gates_needing_data_stay_not_measured
    unmeasured = self.class.report.measurements
                     .select { |m| m.passed.nil? }.map { |m| m.gate.id }.sort
    assert_equal %w[bare_surname_exposure held_out_recall_carrier latency_p95
                    over_fire_prose], unmeasured
    # Not measurable *because the data is absent*, not because the port declined.
    self.class.report.measurements.select { |m| m.passed.nil? }.each do |m|
      refute_empty m.gate.requires, m.gate.id
    end
  end

  def test_a_gate_that_needs_data_is_never_given_a_value
    # The dangerous failure is not "unmeasured" — it is a plausible number
    # computed from the wrong inputs and printed under the right label.
    self.class.report.measurements.each do |m|
      assert_nil m.value, m.gate.id unless m.gate.requires.empty?
    end
  end

  def test_an_unmeasured_gate_reports_no_value_rather_than_zero
    # nil, never 0: a 0 in a `<=` gate reads as a comfortable PASS, which is how
    # an unmeasured gate would come to look like the best-performing one.
    self.class.report.measurements.select { |m| m.passed.nil? }.each do |m|
      refute_equal 0, m.value, m.gate.id
    end
  end

  # -------------------------------------------------------------------------
  # The accounted-for violations
  # -------------------------------------------------------------------------

  def test_no_violation_appears_that_is_not_already_accounted_for
    assert_equal [], self.class.report.unaccounted.map { |v| "#{v.kind}:#{v.detail}" }
  end

  def test_every_accepted_violation_still_actually_happens
    # The load-bearing half. An exemption going stale IS the pass: without this,
    # a fixed defect leaves an entry behind that shelters the next defect of the
    # same shape. Two entries were retired from the Python list exactly this way.
    assert_equal [], self.class.report.missing_accepted
    refute_empty Vicary::Gates::ACCEPTED_VIOLATIONS
  end

  def test_the_accepted_violation_is_the_documented_robinson_keep
    # Named rather than counted, so a different violation cannot inherit the
    # exemption by arriving at the same total.
    assert_equal ["leak\u0000NAME:Robinson"], Vicary::Gates::ACCEPTED_VIOLATIONS.to_a
    keys = self.class.report.violations.map { |v| Vicary::Gates.violation_key(v) }
    assert_equal 1, keys.count { |k| k == "leak\u0000NAME:Robinson" }
  end

  def test_the_violation_key_separator_cannot_occur_in_either_half
    # NUL, so that a detail containing the separator cannot forge a different
    # key. A space would: `partial-leak` details contain several.
    key = Vicary::Gates.violation_key(
      Vicary::Gates::Violation.new(kind: "leak", detail: "NAME:Robinson"),
    )
    assert_equal "leak\u0000NAME:Robinson", key
    assert_includes key, "\u0000"
  end

  # -------------------------------------------------------------------------
  # Alignment
  # -------------------------------------------------------------------------

  def test_alignment_recovers_what_each_placeholder_replaced
    alignment = Vicary::Gates.align("Terrence Okonkwo and Marisol stayed.",
                                    "{NAME_1} and {NAME_2} stayed.")
    assert alignment.ok
    assert_equal [["{NAME_1}", "Terrence Okonkwo"], ["{NAME_2}", "Marisol"]],
                 alignment.pairs
  end

  def test_alignment_is_anchored_so_a_short_chunk_cannot_misalign_it
    # The defect this guards: a trailing "." after a masked email also occurs
    # INSIDE the address, and a greedy per-chunk scan collapses the recovered
    # region to one character. Anchoring the whole reconstruction rejects that.
    alignment = Vicary::Gates.align("Write to a.b@example.org.", "Write to {EMAIL_1}.")
    assert alignment.ok
    assert_equal [["{EMAIL_1}", "a.b@example.org"]], alignment.pairs
  end

  def test_alignment_recovers_a_placeholder_that_ends_the_sentence
    # The `split(re, -1)` case. Ruby drops trailing empty fields where JavaScript
    # keeps them, so without the limit the final chunk vanishes, the pattern
    # loses its closing anchor, and this region comes back short.
    alignment = Vicary::Gates.align("The essay was by Marisol Vega",
                                    "The essay was by {NAME_1}")
    assert alignment.ok
    assert_equal [["{NAME_1}", "Marisol Vega"]], alignment.pairs
  end

  def test_alignment_is_anchored_to_the_whole_string_not_to_a_line
    # Ruby's `^` and `$` match at EVERY line boundary — there is no opt-out, where
    # JavaScript's anchor the whole input. With line anchors, alignment reports a
    # clean pass while an entire line of the essay went missing: the
    # reconstruction satisfies the pattern against one line alone. A composition
    # is multi-line, so this is the shape the fixture cannot show.
    #
    # Both ends, separately. Each anchor is the only thing standing between one
    # direction of this defect and a green run, so one case would leave the other
    # anchor free to be rewritten.
    trailing = Vicary::Gates.align("Marisol wrote it.\nExtra trailing line.",
                                   "{NAME_1} wrote it.")
    refute trailing.ok, "a dropped trailing line must not align"
    assert_match(/rewritten, reordered or dropped/, trailing.reason)

    # Needs surviving prose BEFORE the placeholder, or the leading anchor is
    # satisfied by the region absorbing the dropped line rather than by the match
    # position.
    leading = Vicary::Gates.align("Dropped first line.\nShe met Marisol Vega there.",
                                  "She met {NAME_1} there.")
    refute leading.ok, "a dropped leading line must not align"
    assert_match(/rewritten, reordered or dropped/, leading.reason)
  end

  def test_alignment_refuses_text_that_was_rewritten_rather_than_replaced
    alignment = Vicary::Gates.align("She stayed late.", "He stayed late.")
    refute alignment.ok
    assert_match(/no placeholder emitted/, alignment.reason)

    rewritten = Vicary::Gates.align("She stayed late.", "{NAME_1} departed late.")
    refute rewritten.ok
    assert_match(/rewritten, reordered or dropped/, rewritten.reason)
  end

  def test_unmasked_text_aligns_with_no_pairs
    alignment = Vicary::Gates.align("Nothing to mask here.", "Nothing to mask here.")
    assert alignment.ok
    assert_equal [], alignment.pairs
  end

  def test_a_literal_with_regex_metacharacters_does_not_break_alignment
    # The original is student data; a surname like "O'Brien (Jr.)" must not
    # compile as a group when it is escaped into the reconstruction pattern.
    alignment = Vicary::Gates.align("O'Brien (Jr.) was here.", "{NAME_1} was here.")
    assert alignment.ok
    assert_equal [["{NAME_1}", "O'Brien (Jr.)"]], alignment.pairs
  end

  # -------------------------------------------------------------------------
  # Invariants
  # -------------------------------------------------------------------------

  def test_a_partial_leak_is_caught_even_though_the_whole_literal_is_gone
    # Worse than a miss, because it LOOKS redacted: "{NAME_1} Okonkwo" reads as a
    # working redactor in every summary statistic while publishing the surname,
    # and recall — which tests for the whole literal — scores it as a pass.
    probe = frame("Terrence Okonkwo sat behind me.",
                  spans: [span(entity: "NAME", literal: "Terrence Okonkwo")])
    assert_equal ["partial-leak"],
                 Vicary::Gates.check_frame(probe, "{NAME_1} Okonkwo sat behind me.")
                              .map(&:kind)
    assert_empty Vicary::Gates.check_frame(probe, "{NAME_1} sat behind me.")
  end

  def test_a_span_masked_as_the_wrong_entity_is_caught
    # No fixture frame produces a `wrong-type` violation, so nothing else here
    # exercises this arm: deleting the check outright left the whole suite green.
    # A hometown typed {NAME} rather than {LOCATION} is masked either way, so it
    # is invisible to recall — but outbound it is what the student reads.
    probe = frame("We drove from Akron that morning.",
                  spans: [span(entity: "LOCATION", literal: "Akron",
                               expect: "{LOCATION}")])
    assert_equal ["wrong-type"],
                 Vicary::Gates.check_frame(probe, "We drove from {NAME_1} that morning.")
                              .map(&:kind)
    # The correctly-typed mask is NOT a violation. Asserted because the first
    # version of this check re-braced `expect` and made every correct span a
    # `wrong-type` — 41 of them, each reading "expected {NAME} got {NAME}".
    assert_empty Vicary::Gates.check_frame(probe,
                                           "We drove from {LOCATION_1} that morning.")
  end

  def test_a_placeholder_nobody_emits_is_caught
    # A truncated or nested placeholder is how a masking bug presents, and it
    # reads as ordinary prose to a downstream stage.
    probe = frame("Akron is where we lived.")
    assert_equal ["unknown-placeholder"],
                 Vicary::Gates.check_frame(probe, "{PERSON_1} is where we lived.")
                              .map(&:kind)
  end

  def test_a_destroyed_keep_span_is_caught
    # Recall alone rewards a redactor that masks everything; this is the invariant
    # that stops that being a clean run.
    probe = frame("I wrote about Jackie Robinson for class.",
                  spans: [span(entity: "NAME", literal: "Jackie Robinson",
                               verdict: "keep")])
    assert_equal ["keep-destroyed"],
                 Vicary::Gates.check_frame(probe, "I wrote about {NAME_1} for class.")
                              .map(&:kind)
    assert_empty Vicary::Gates.check_frame(probe, probe.sentence)
  end

  def test_one_placeholder_standing_for_two_originals_is_caught
    # `not-restorable` — the deficit numbering fixes. Unnumbered output produced
    # 37 of these across 25 injected essays.
    probe = frame("Terrence and Marisol stayed.")
    assert_equal ["not-restorable"],
                 Vicary::Gates.check_frame(probe, "{NAME} and {NAME} stayed.")
                              .map(&:kind)
  end

  def test_weak_tokens_do_not_count_as_a_partial_leak
    # "van", "de", "the" surviving proves nothing — they are not the name.
    assert_equal %w[Vincent Gogh],
                 Vicary::Gates.leak_probes(span(entity: "NAME",
                                                literal: "Vincent van Gogh"))
  end

  def test_a_non_name_entity_has_no_leak_probes
    # Half a phone number is not an identifying fragment the way half a name is.
    assert_empty Vicary::Gates.leak_probes(span(entity: "PHONE",
                                                literal: "(330) 555-0148"))
  end

  # -------------------------------------------------------------------------
  # Placeholders and round-trip
  # -------------------------------------------------------------------------

  def test_the_kind_is_separable_from_the_index
    assert_equal "{NAME}", Vicary::Gates.placeholder_kind("{NAME_3}")
    assert_equal "{NAME}", Vicary::Gates.placeholder_kind("{NAME}")
    assert_equal "{ZIP_CODE}", Vicary::Gates.placeholder_kind("{ZIP_CODE_2}")
    # The one that would break a naive strip-after-underscore rule.
    assert_equal "{CREDIT_DEBIT_CARD_NUMBER}",
                 Vicary::Gates.placeholder_kind("{CREDIT_DEBIT_CARD_NUMBER_1}")
    assert_includes Vicary::Gates::KNOWN_PLACEHOLDERS,
                    Vicary::Gates.placeholder_kind("{LOCATION_9}")
  end

  def test_the_index_is_stripped_only_at_the_very_end_of_the_token
    # `\z`, not `$`. Ruby's `$` also matches before a trailing newline, so a
    # malformed token arriving with one would have its index quietly stripped and
    # come back a KNOWN placeholder — turning an `unknown-placeholder` violation
    # into a clean run. JavaScript's `$` does not, and the two ports must agree.
    assert_equal "{NAME_3}\n", Vicary::Gates.placeholder_kind("{NAME_3}\n")
    refute_includes Vicary::Gates::KNOWN_PLACEHOLDERS,
                    Vicary::Gates.placeholder_kind("{NAME_3}\n")
  end

  def test_an_unnumbered_document_does_not_round_trip
    # The measurement numbering exists to answer, not an opinion about it: one
    # token standing for two people cannot be put back by any map keyed on it.
    probe = frame("Terrence and Marisol stayed.")
    assert Vicary::Gates.round_trips?(probe, "{NAME_1} and {NAME_2} stayed.")
    refute Vicary::Gates.round_trips?(probe, "{NAME} and {NAME} stayed.")
  end

  def test_restore_by_token_is_keyed_on_what_a_consumer_actually_sees
    # Distinct from Minter.restore, which is handed the map the masker built.
    # This one has only the echoed token, which is the situation a downstream
    # stage is in.
    assert_equal "Terrence and Marisol stayed.",
                 Vicary::Gates.restore_by_token(
                   "{NAME_1} and {NAME_2} stayed.",
                   { "{NAME_1}" => "Terrence", "{NAME_2}" => "Marisol" },
                 )
    # An unmapped token is left alone rather than dropped — losing it would
    # silently shorten the text and read as a successful restore.
    assert_equal "{NAME_9} stayed.",
                 Vicary::Gates.restore_by_token("{NAME_9} stayed.", {})
  end

  # -------------------------------------------------------------------------
  # The rendered block
  # -------------------------------------------------------------------------

  def test_the_rendered_block_says_five_of_five_and_names_the_four_it_cannot
    block = Vicary::Gates.report(self.class.report)
    assert_match(/5 of 5 measured gates hold; 4 are NOT MEASURED/, block)
    # Gate ROWS, not occurrences: the summary line says "NOT MEASURED" too, so a
    # bare count of the string is 5 and agrees with nothing.
    rows = block.lines.grep(/^    NOT MEASURED /)
    assert_equal 4, rows.size
    assert_equal 5, block.lines.grep(/^    (PASS|FAIL) /).size
    assert_match(/NEEDS corpus/, block)
    assert_match(/NEEDS census/, block)
  end

  def test_the_scoreboard_still_prints_the_unmeasured_block_for_a_caller_that_measured_nothing
    # The honest output when no gate block is handed in — not an assertion that
    # nothing is measurable, but that this module refuses to imply otherwise.
    board = Vicary::Conformance.score(self.class.spec) do |sentence, identity|
      Vicary.redact(sentence, identity)
    end
    bare = Vicary::Conformance.report(board, Vicary::Conformance.load_gates)
    assert_match(/the caller measured no gate/, bare)
    assert_equal 9, bare.scan("NOT MEASURED").size
  end
end
