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

  # The exposure measured from an operator-supplied census file, or nil when no
  # one pointed `VICARY_EVAL_CENSUS_CSV` at a copy.
  def self.exposure
    return @exposure if defined?(@exposure)

    @exposure = if Vicary::Census.census_source.empty?
                  nil
                else
                  Vicary::Census.measure(Vicary::Census.load_census)
                end
  end

  # The same gates again with the census requirement satisfied.
  #
  # Measured separately rather than folded into `report` so the assertions above
  # keep testing what they were written to test: that an *absent* requirement
  # yields NOT MEASURED. Both paths then have a test, which is the point — the
  # failure being guarded against is a gate that quietly acquires a value.
  def self.census_report
    return @census_report if defined?(@census_report)

    @census_report = if exposure.nil?
                       nil
                     else
                       Vicary::Gates.measure(
                         spec, Vicary::Conformance.load_gates,
                         asset_entries: Vicary::Gazetteer.load.entry_count,
                         bare_surname_exposure: exposure.rate
                       ) { |sentence, identity| Vicary.redact(sentence, identity) }
                     end
  end

  # Minitest has no per-test skip predicate, so each census test opens with this.
  def skip_without_census
    skip "no VICARY_EVAL_CENSUS_CSV; see ruby/lib/vicary/census.rb" if self.class.exposure.nil?
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

  def test_with_no_data_supplied_all_four_gates_needing_data_stay_not_measured
    unmeasured = self.class.report.measurements
                     .select { |m| m.passed.nil? }.map { |m| m.gate.id }.sort
    assert_equal %w[bare_surname_exposure held_out_recall_carrier latency_p95
                    over_fire_prose], unmeasured
    # Not measurable *because the data is absent*, not because the port declined.
    self.class.report.measurements.select { |m| m.passed.nil? }.each do |m|
      refute_empty m.gate.requires, m.gate.id
    end
  end

  def test_a_gate_whose_data_is_absent_is_never_given_a_value
    # The dangerous failure is not "unmeasured" — it is a plausible number
    # computed from the wrong inputs and printed under the right label.
    self.class.report.measurements.each do |m|
      assert_nil m.value, m.gate.id unless m.gate.requires.empty?
    end
  end

  # -------------------------------------------------------------------------
  # The census gate, when the operator supplies the file
  # -------------------------------------------------------------------------

  def test_bare_surname_exposure_is_measured_and_holds_when_supplied
    skip_without_census
    m = self.class.census_report.measurements.find { |x| x.gate.id == "bare_surname_exposure" }
    refute_nil m.value
    # Reconciled against `python -m vicary.eval.census` on the same file, to four
    # decimals rather than the two the report rounds to — the gate bar is 1.25
    # and 1.2 would sit under it whatever the later digits did.
    assert_equal "1.1992", format("%.4f", m.value)
    exposure = self.class.exposure
    assert_equal 162_253, exposure.surnames_scored
    assert_equal 792, exposure.surnames_matched
    assert_equal 265_667_228, exposure.bearers_total
    assert_equal 3_185_816, exposure.bearers_exposed
    assert_equal true, m.passed
  end

  def test_supplying_the_census_file_measures_that_gate_and_no_other
    skip_without_census
    # A requirement satisfied is not a licence for the other three: the corpus
    # gates must stay NOT MEASURED, or "six of nine" silently becomes "nine".
    still_unmeasured = self.class.census_report.measurements
                           .select { |m| m.passed.nil? }.map { |m| m.gate.id }.sort
    assert_equal %w[held_out_recall_carrier latency_p95 over_fire_prose], still_unmeasured
  end

  # -------------------------------------------------------------------------
  # The corpus gates, when the operator supplies an essay corpus
  # -------------------------------------------------------------------------

  def self.corpus
    return @corpus if defined?(@corpus)

    # No `corpus_source` guard: a shipped corpus needs no operator TSV, and
    # `measure_from_config` returns nil for exactly the case where the data is
    # absent. Guarding on the env var here is what kept this port reporting NEEDS
    # corpus against a corpus sitting in the repository.
    @corpus = Vicary::Corpus.measure_from_config(spec) { |t, i| Vicary.redact(t, i) }
  end

  def skip_without_corpus
    return unless self.class.corpus.nil?

    skip "the resolved corpus is operator-supplied and no VICARY_EVAL_CORPUS_TSV " \
         "is set; see ruby/lib/vicary/corpus.rb"
  end

  def test_the_carrier_essays_are_byte_identical_to_the_references
    skip_without_corpus
    # The load-bearing parity assertion. Every corpus gate is measured on this
    # text, so if it diverges from Python's the three ports are answering
    # different questions and agreeing on the numbers would prove nothing.
    # Anchored on a digest rather than on the metrics, because the metrics can
    # coincide across genuinely different inputs.
    corpus_id = Vicary::Corpus.resolve_corpus_id
    plan = Vicary::Corpus.load_carrier_plan(corpus_id)
    cases = Vicary::Corpus.build_cases(Vicary::Corpus.load_essays(corpus_id), plan,
                                       self.class.spec)
    assert_equal Vicary::Corpus.load_measured["carrier_text_sha256"],
                 Digest::SHA256.hexdigest(cases.map(&:text).join)
  end

  def test_the_corpus_gates_measure_what_the_reference_measures
    skip_without_corpus
    m = self.class.corpus
    # Read off `conformance/measured.json`, not typed here. These were literals
    # in this file, in TypeScript's gate test and in Python's — and three copies
    # of a number is not three checks of it. When the reference's figure moves,
    # Python is updated because that is where the change was made, and the other
    # two go on asserting the stale value while staying green: measuring a
    # different thing from the reference and reporting agreement.
    measured = Vicary::Corpus.load_measured
    reference = measured["corpus_gates"]

    # Before comparing anything, that the two are the same question. A count
    # taken at another fixture version fails as an off-by-a-few that reads like a
    # detector regression and costs a bisect to attribute.
    assert_equal self.class.spec.fixture_version, measured["envelope"]["fixture_version"],
                 "measured.json was measured at a different fixture than this port is " \
                 "scoring against — regenerate it with `just sync-conformance` rather " \
                 "than comparing across fixtures"

    # Counts, not just percentages: 100% of a wrong denominator is still 100%,
    # and the denominator is what moves when a fixture revision adds a span.
    assert_equal reference["essays"], m.essays
    assert_equal reference["recall_held_out_passed"], m.recall_held_out_passed
    assert_equal reference["recall_held_out_total"], m.recall_held_out_total
    assert_in_delta reference["recall_held_out_pct"], m.recall_held_out, 1e-9
    assert_equal reference["over_fire_spans_total"], m.over_fire_spans_total
    assert_in_delta reference["over_fire_spans_per_essay"], m.over_fire_spans_per_essay, 1e-9
    assert_in_delta reference["asap_rewrites_per_essay"], m.asap_rewrites_per_essay, 1e-9
  end

  def test_latency_is_this_ports_own_and_is_not_asserted_against_the_references
    skip_without_corpus
    # The one corpus gate whose answer Python's number says nothing about. This
    # port runs nearest the bar of the three, so the assertion is the bar itself
    # rather than a figure — pinning it would make an ordinary CI machine fail a
    # correctness suite for being busy.
    m = self.class.corpus
    assert_operator m.latency_p95_ms, :>, 0
    assert_operator m.latency_p95_ms, :<=, 10.0,
                    "latency p95 #{m.latency_p95_ms} ms exceeds the 10 ms bar"
  end

  def test_an_asap_anonymization_token_is_told_from_ordinary_prose
    # The over-fire metric's whole meaning rests on this split.
    ["@PERSON1", "@LOCATION2", "@CAPS", " @ORGANIZATION3 "].each do |token|
      assert Vicary::Corpus.asap_token?(token), token
    end
    ["@", "@person1", "Mr. Okonkwo", "@PERSON1 and more"].each do |prose|
      refute Vicary::Corpus.asap_token?(prose), prose
    end
  end

  def test_a_corpus_essay_that_does_not_match_the_plan_is_refused
    # An offset into the wrong text is not an error anything downstream notices;
    # it produces a plausible number from text nobody intended.
    plan = { "cases" => [{ "essay_id" => "1", "base_sha256" => "0" * 64,
                           "frames" => [], "slots" => [] }] }
    error = assert_raises(Vicary::Conformance::SpecError) do
      Vicary::Corpus.build_cases([["1", "some other essay"]], plan, self.class.spec)
    end
    assert_match(/does not match the one the carrier plan was built from/, error.message)
  end

  def test_a_corpus_that_supplies_only_some_of_the_planned_essays_is_refused
    # Caught in review by pointing the harness at a one-essay TSV: it built zero
    # cases, and over-firing and latency then computed as 0.0 — which in a `<=`
    # gate is the most comfortable pass on the board. Two gates went green on no
    # data at all. A subset must be refused, not averaged.
    plan = Vicary::Corpus.load_carrier_plan
    error = assert_raises(Vicary::Conformance::SpecError) do
      Vicary::Corpus.build_cases([["not-a-planned-id", "some other essay"]], plan,
                                 self.class.spec)
    end
    assert_match(/Refusing to measure a subset/, error.message)
  end

  def test_a_corpus_essay_the_plan_neither_carries_nor_names_is_refused
    # The subset check above compares cases built against cases planned, so a
    # plan that quietly lost ten of its twenty-five essays matches itself
    # perfectly and measures fifteen — under the same gate, at the same bar.
    # Unreachable while a plan always covered its whole corpus, and reachable the
    # moment `unusable` made a short plan legitimate. So the count reconciles
    # against the *corpus*: carried plus named must equal supplied.
    base = "The dog barked. The cat ran. The bird flew. The fish swam. " \
           "The cow mooed. And then it was quiet."
    plan = {
      "cases" => [{ "essay_id" => "carried",
                    "base_sha256" => Digest::SHA256.hexdigest(base),
                    "frames" => [self.class.spec.frames.first.frame_id],
                    "slots" => [16] }],
      "unusable" => []
    }
    essays = [["carried", base], ["neither-carried-nor-named", base]]

    error = assert_raises(Vicary::Conformance::SpecError) do
      Vicary::Corpus.build_cases(essays, plan, self.class.spec)
    end
    assert_match(/dropped silently/, error.message)

    # And naming it is what makes the same corpus measurable — otherwise the
    # check would just be an assertion that plans are never short.
    plan["unusable"] = [{ "essay_id" => "neither-carried-nor-named",
                          "reason" => "declared for this test" }]
    assert_equal 1, Vicary::Corpus.build_cases(essays, plan, self.class.spec).size
  end

  # -------------------------------------------------------------------------
  # The census reader's guards — these need no census file
  # -------------------------------------------------------------------------

  def test_a_truncated_census_file_is_refused_rather_than_scored
    # The failure mode is silent and one-directional: fewer rows is a smaller
    # denominator, which reports a more comfortable exposure than the truth.
    short = "name,rank,count\nSMITH,1,2442977\nJOHNSON,2,1932812"
    error = assert_raises(RuntimeError) { Vicary::Census.parse_census_surnames(short) }
    assert_match(/only 2 rows/, error.message)
  end

  def test_a_census_file_with_no_usable_header_is_refused
    error = assert_raises(ArgumentError) do
      Vicary::Census.parse_census_surnames("surname,total\nSMITH,2442977")
    end
    assert_match(%r{no 'name'/'count' header}, error.message)
  end

  def test_a_zip_is_refused_by_name_rather_than_read_as_text
    # Reading the archive's bytes as CSV yields zero rows, and zero rows is the
    # most comfortable exposure rate there is.
    error = assert_raises(ArgumentError) { Vicary::Census.load_census("/nonexistent/names.zip") }
    assert_match(/reads the extracted \.csv only/, error.message)
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

  # Print the board, whatever this run could reach.
  #
  # `rake gates` used to print minitest's dot row and nothing else, while Python's
  # `pytest -m gates -s` printed the nine numbers. Same gates, same bars, and only
  # one of the three ports would tell you what it measured — so "all three agree"
  # was a claim you had to run the reference to check.
  #
  # Assembled from the measurements the tests above already made rather than
  # re-measuring: the corpus arm redacts 50 essays and paying for that twice to
  # print it would make the report expensive enough to switch off.
  def self.print_board
    corpus_metrics = corpus
    corpus_id = corpus_metrics.nil? ? nil : Vicary::Corpus.resolve_corpus_id
    full = Vicary::Gates.measure(
      spec, Vicary::Conformance.load_gates,
      asset_entries: Vicary::Gazetteer.load.entry_count,
      bare_surname_exposure: exposure&.rate,
      held_out_recall_carrier: corpus_metrics&.recall_held_out,
      over_fire_per_essay: corpus_metrics&.over_fire_spans_per_essay,
      latency_p95_ms: corpus_metrics&.latency_p95_ms,
      corpus_id: corpus_id
    ) { |sentence, identity| Vicary.redact(sentence, identity) }
    puts
    # The corpus is named, not implied. Two of these gates carry a per-corpus
    # bar — over-firing is 8.15 spans/essay on persuade-20 against 0.61 on
    # ASAP-AES — so a board that prints `8.150 <= 8.15 PASS` without saying which
    # corpus produced it is a number filed under no corpus at all.
    puts "gate report — fixture #{spec.fixture_version}, arm #{spec.reference_arm}, " \
         "corpus #{corpus_id || '(none measured)'}"
    puts Vicary::Gates.report(full)
  end
end

# After the assertions, so a failing gate still reports the value that failed it
# — the same ordering `python/tests/conftest.py` enforces, and for the same
# reason: a report that runs first prints an empty table and passes.
Minitest.after_run { GatesTest.print_board }
