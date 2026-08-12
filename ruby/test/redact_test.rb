# frozen_string_literal: true

# The public entry point: identity interpolation, the two passes, arm selection,
# and the restore map.
#
# The counterpart of `typescript/test/redact.test.ts`, case for case, and this
# port had none. `redact.rb` was exercised by every other Ruby suite *through*
# `Vicary.redact` and by both parity probes, but nothing tested arm selection or
# the restore map directly.
#
# The conformance suite scores exactly one arm — `local-gazetteer-lowercase` — so
# nothing there distinguishes the other two levels from it, or from each other. A
# level that silently resolved to the wrong bundle would leave every frame
# matching and every log line reporting spans while the deployment found a
# different set of names than it was configured to.
#
# The declared gap this closes is recorded in `conformance/coverage.json`, and
# `tools/tests/test_coverage_parity.py` fails if its entry outlives it.

require "minitest/autorun"
require "set"

require "vicary"

class RedactTest < Minitest::Test
  Person = Struct.new(:first_name, :last_name, :school_name, keyword_init: true)

  IDENTITY = Person.new(first_name: "Marguerite", last_name: "Delacroix-Whitfield",
                        school_name: "Westfield High School").freeze

  # -------------------------------------------------------------------------
  # Resolving the level
  # -------------------------------------------------------------------------

  def test_each_levels_own_name_resolves_to_it
    assert_equal Vicary::NAMES_IDENTITY, Vicary.name_detection(Vicary::NAMES_IDENTITY)
    assert_equal Vicary::NAMES_GAZETTEER, Vicary.name_detection(Vicary::NAMES_GAZETTEER)
    assert_equal Vicary::NAMES_LOWERCASE, Vicary.name_detection(Vicary::NAMES_LOWERCASE)
  end

  def test_the_aliases_a_host_is_likely_to_write_resolve
    # A host configuring this from an env file writes "true", not
    # "gazetteer-lowercase". Matching the reference's alias sets rather than
    # demanding the canonical spelling.
    %w[off none 0 false no].each do |alias_|
      assert_equal Vicary::NAMES_IDENTITY, Vicary.name_detection(alias_), alias_
    end
    %w[on 1 true yes names].each do |alias_|
      assert_equal Vicary::NAMES_GAZETTEER, Vicary.name_detection(alias_), alias_
    end
    %w[lowercase full max gazetteer_lowercase].each do |alias_|
      assert_equal Vicary::NAMES_LOWERCASE, Vicary.name_detection(alias_), alias_
    end
    assert_equal Vicary::NAMES_GAZETTEER, Vicary.name_detection("  GaZeTTeer  ")
  end

  def test_an_unrecognized_value_falls_to_the_default_not_to_identity
    # The opposite of the redaction-mode dial's fail-safe, and deliberately so.
    # There, a typo makes the host behave as it did before redaction existed — a
    # recoverable non-event. Here, dropping silently to `identity` would leave
    # redaction ON and reporting spans while finding none of the names a reader
    # would call PII: a failure that looks exactly like success from every log
    # line and every metric.
    assert_equal Vicary::DEFAULT_NAME_DETECTION, Vicary.name_detection("gazeteer") # one 't'
    assert_equal Vicary::DEFAULT_NAME_DETECTION, Vicary.name_detection("yes please")
    assert_equal Vicary::NAMES_LOWERCASE, Vicary::DEFAULT_NAME_DETECTION
  end

  def test_an_empty_value_is_not_a_request_for_identity_only
    # "" is unset, not "off" — it has to reach the default. The alias set
    # contains several falsy-looking strings, so this is one guard away from
    # inverting.
    assert_equal Vicary::DEFAULT_NAME_DETECTION, Vicary.name_detection("")
    assert_equal Vicary::DEFAULT_NAME_DETECTION, Vicary.name_detection("   ")
  end

  # -------------------------------------------------------------------------
  # What a level wires in
  # -------------------------------------------------------------------------

  def test_the_identity_level_loads_no_gazetteer_and_generates_nothing
    oracles = Vicary.gazetteer_oracles(Vicary::NAMES_IDENTITY)
    assert_equal false, oracles[:candidates]
    assert_equal [:candidates], oracles.keys
  end

  def test_generation_and_the_oracle_are_one_decision_not_two
    # Generation alone masks every public figure a student writes about; the
    # oracle alone has nothing to judge. Neither level may supply half.
    [Vicary::NAMES_GAZETTEER, Vicary::NAMES_LOWERCASE].each do |level|
      oracles = Vicary.gazetteer_oracles(level)
      assert_equal true, oracles[:candidates], level
      %i[notable notability_tier title title_prefix].each do |key|
        assert oracles.key?(key), "#{level} should wire #{key}"
        refute_nil oracles[key], "#{level}'s #{key}"
      end
    end
  end

  def test_the_lowercase_route_is_the_only_difference_between_the_two_levels
    gazetteer = Vicary.gazetteer_oracles(Vicary::NAMES_GAZETTEER)
    lowercase = Vicary.gazetteer_oracles(Vicary::NAMES_LOWERCASE)
    refute gazetteer.key?(:given_name)
    assert lowercase.key?(:given_name)
    # The settlement oracle is wired at BOTH, unlike `given_name`: it decides a
    # placeholder's TYPE, not a verdict, so it has nothing to do with which
    # candidate routes are on. Presence rather than identity, because each call
    # builds fresh lambdas here — the TypeScript counterpart can compare the
    # function references and this port cannot.
    assert gazetteer.key?(:settlement)
    assert lowercase.key?(:settlement)
    assert_equal [:given_name], lowercase.keys - gazetteer.keys
  end

  # -------------------------------------------------------------------------
  # The levels, end to end
  # -------------------------------------------------------------------------

  def test_only_the_lowercase_level_reaches_a_student_who_writes_without_capitals
    # The arm's whole reason to exist. Capitalisation is the primary signal, so
    # a composition typed in lowercase is invisible to the level below.
    text = "then terrence okonkwo showed up and everything changed for me."
    assert_equal text, Vicary.redact(text, IDENTITY, names: Vicary::NAMES_IDENTITY)
    assert_equal text, Vicary.redact(text, IDENTITY, names: Vicary::NAMES_GAZETTEER)
    assert_equal "then {NAME_1} showed up and everything changed for me.",
                 Vicary.redact(text, IDENTITY, names: Vicary::NAMES_LOWERCASE)
  end

  def test_the_identity_level_still_masks_every_structured_entity
    # Turning name detection off is not turning redaction off. A caller who
    # picks `identity` for its precision still gets the syntax, which is the half
    # regex scored 100% on.
    assert_equal "Call me at {PHONE_1} or {EMAIL_1}.",
                 Vicary.redact("Call me at 555-123-4567 or rosa@example.org.",
                               IDENTITY, names: Vicary::NAMES_IDENTITY)
  end

  def test_a_keep_from_the_assignment_prompt_survives_the_detector
    # The prompt_context leg: exact, free, zero false positives. Left EMPTY when
    # the golden was generated, so no frame exercises it — this is the only thing
    # that does.
    text = "I wrote about Ngozi Adeyemi for class last spring."
    assert_match(/\{NAME_1\}/, Vicary.redact(text, IDENTITY))
    assert_equal text, Vicary.redact(text, IDENTITY, keep: Set.new(["Ngozi Adeyemi"]))
  end

  # -------------------------------------------------------------------------
  # Numbering, across both passes
  # -------------------------------------------------------------------------

  def test_one_minter_serves_both_passes_so_indices_never_collide
    # The defect numbering exists to remove: two minters would restart each
    # counter and hand {NAME_1} to the student and to a classmate both.
    masked, n_masked, restore_map = Vicary.redact_with_report(
      "Marguerite Delacroix-Whitfield and Terrence Okonkwo both stayed after class.",
      IDENTITY,
    )
    assert_equal "{NAME_1} and {NAME_2} both stayed after class.", masked
    assert_equal 2, n_masked
    assert_equal [["{NAME_1}", "Marguerite Delacroix-Whitfield"],
                  ["{NAME_2}", "Terrence Okonkwo"]],
                 restore_map.to_a
  end

  def test_the_unnumbered_arm_reproduces_the_older_output_and_is_not_restorable
    # Kept measurable rather than deleted, and this is the measurement: two
    # people collapse to one token, so no map keyed on it can put either back.
    # Unnumbered output round-tripped 36% of injected essays.
    text = "Marguerite Delacroix-Whitfield and Terrence Okonkwo stayed."
    masked, _n, restore_map = Vicary.redact_with_report(text, IDENTITY,
                                                       number_placeholders: false)
    assert_equal "{NAME} and {NAME} stayed.", masked
    refute_equal text, Vicary.restore(masked, restore_map)
  end

  def test_empty_text_is_returned_unchanged_with_an_empty_report
    masked, n_masked, restore_map = Vicary.redact_with_report("", IDENTITY)
    assert_equal "", masked
    assert_equal 0, n_masked
    assert_equal 0, restore_map.size
  end
end
