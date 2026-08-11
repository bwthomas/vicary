# frozen_string_literal: true

# The port's tokenisation and capitalisation primitives, against the shared spec.
#
# `conformance/frames.json` scores finished output, which is the right final bar
# and a poor first one: a port with nothing implemented scores 0 of 36 and learns
# nothing about which of the forty-odd primitives underneath is wrong.
# `primitives.json` is that missing layer — generated from the Python functions,
# byte-compared against a fresh export by `python/tests/test_conformance.py`, and
# read here rather than transcribed.
#
# **Not a score and not a gate.** A port can be green in this file and mask
# nothing. The frames are still what says a port works; this only says which brick
# is crooked, and says it in one run instead of a bisect.
#
# It is also the answer to a measurement taken on this port the day it landed: of
# eight deliberate mutations to `candidates.rb` — reversed sort ties, swapped
# precedence rows, a wrong corroborating tier, a disabled apostrophe trim — the 36
# conformance frames caught exactly one. The frames say the port works on the
# fixture; this file says the port is the same detector.

require "minitest/autorun"
require "set"

require "vicary"

class PrimitivesTest < Minitest::Test
  SPEC = Vicary::Conformance.load_primitives

  C = Vicary::Candidates

  # The stand-in oracles, built from the spec's own lists.
  #
  # Their semantics are stated in the generator and must be implemented exactly;
  # a port that folds differently here is measuring its fold, not its scan. Real
  # gazetteer tiers are deliberately not used — a primitive that disagreed would
  # then be indistinguishable from a tier lookup that disagreed.
  SETTLEMENTS = Set.new(SPEC["oracles"]["settlements"])
  TITLES = SPEC["oracles"]["titles"]
  GIVEN_NAMES = Set.new(SPEC["oracles"]["given_names"])
  FULL_NAMES = Set.new(SPEC["oracles"]["full_names"])
  ICONIC_SURNAMES = Set.new(SPEC["oracles"]["iconic_surnames"])

  IS_SETTLEMENT = ->(name) { SETTLEMENTS.include?(name.downcase) }
  IS_TITLE = ->(text) { TITLES.include?(text.downcase.gsub("’", "'")) }
  IS_TITLE_PREFIX = ->(key) { TITLES.any? { |t| t == key || t.start_with?("#{key} ") } }
  IS_GIVEN = ->(token) { GIVEN_NAMES.include?(token.downcase) }

  # The tier oracle's four answers, spelled exactly as the generator states them.
  # `place` is here rather than folded into "not full_name" because it is the tier
  # whose keeps must NOT license a surname, and a port that dropped it would be
  # indistinguishable from one that never had it.
  TIER_OF = lambda do |name|
    key = name.downcase
    if FULL_NAMES.include?(key) then "full_name"
    elsif ICONIC_SURNAMES.include?(key) then "iconic_short"
    elsif SETTLEMENTS.include?(key) then "place"
    else "not_notable"
    end
  end
  IS_NOTABLE = ->(name) { TIER_OF.call(name) != "not_notable" }

  # The oracle set the spec's `find_candidates` section is generated with.
  WIRED = {
    given_name: IS_GIVEN, title: IS_TITLE, title_prefix: IS_TITLE_PREFIX,
    settlement: IS_SETTLEMENT,
  }.freeze
  # The full masking wiring the spec's `mask_candidates` arm is generated with.
  MASKING = WIRED.merge(notable: IS_NOTABLE, notability_tier: TIER_OF).freeze

  CORPUS = SPEC["corpus"]
  LISTS = SPEC["token_lists"]
  SPANS = SPEC["span_cases"]
  # The surname functions are keyed by the name itself, so the input *is* the key.
  NAME_FORMS = SPEC["name_forms"].to_h { |n| [n, n] }
  STOP_TOKENS = SPEC["stop_tokens"].to_h { |t| [t, t] }
  KEEPS = Set.new(SPEC["keeps"])

  # Every section this file checks. Read by the completeness test at the bottom.
  CHECKED = []

  class << self
    # Run one section over every case the spec lists for it.
    #
    # The section is read out of `spec["cases"]` rather than iterated from the
    # corpus, so a section the generator stopped emitting fails loudly here
    # instead of passing vacuously. That is the same reasoning as the asset's
    # declared tier counts: a silent shrinkage reads as a pass.
    def section(name, inputs, &produce)
      CHECKED << name
      define_method(:"test_primitive_#{name}") do
        cases = SPEC["cases"][name]
        refute_nil cases, "the spec has no `#{name}` section"
        refute_empty cases,
                     "the spec's `#{name}` section is empty, so this test checks nothing"
        cases.each do |case_name, expected|
          input = inputs[case_name]
          refute_nil input,
                     "`#{name}` names case #{case_name}, which the spec's inputs do not"
          produced = jsonable(produce.call(input, case_name))
          message = "#{name}[#{case_name}] — input #{input.inspect}"
          # `bare_surname_key` answers nil for most of its inputs, and minitest
          # refuses `assert_equal nil` outright rather than comparing it.
          if expected.nil?
            assert_nil produced, message
          else
            assert_equal expected, produced, message
          end
        end
      end
    end
  end

  # Round-trip through JSON so a Set, a Struct or a symbol compares as the plain
  # data the spec holds rather than by Ruby identity.
  def jsonable(value)
    JSON.parse(JSON.generate(value))
  end

  # `[start, end, matched]` per match — the shape the generator emits.
  def self.matches(pattern, text)
    C.each_match(text, pattern).map { |m| [m.begin(0), m.begin(0) + m[0].length, m[0]] }
  end

  # `[start, end, text, kind]` per candidate — the shape the generator emits.
  def self.as_rows(candidates)
    candidates.map { |c| [c.start, c.end, c.text, c.kind] }
  end

  def test_the_specs_constants_are_this_builds_constants
    # Emitted as data because a port that reads the corpus off the spec and the
    # thresholds off a literal it typed can pass every case below and still be
    # tuned differently — the corpus simply may not contain the input that
    # separates 2 from 3.
    assert_equal SPEC["constants"], {
      "allcaps_run" => C::ALLCAPS_RUN,
      "drops_capitals_min_rate" => C::DROPS_CAPITALS_MIN_RATE,
      "heading_max_chars" => C::HEADING_MAX_CHARS,
      "lowercase_min_tokens" => C::LOWERCASE_MIN_TOKENS,
      "marks_proper_nouns_min" => C::MARKS_PROPER_NOUNS_MIN,
      "relation_window" => C::RELATION_WINDOW,
      "stop_words" => C.stop_words.size,
      "title_max_tokens" => C::TITLE_MAX_TOKENS,
    }
  end

  def test_the_specs_precedence_table_is_this_builds_precedence_table
    # The rows are emitted as data for the same reason the constants are: a port
    # that reads the corpus off the spec and orders its own rows by hand can pass
    # every case below and still resolve a collision the other way. `classify`
    # cannot catch that on its own — a kept span and a span typed NAME are the
    # same string there — which is what `masks_with_settlement` is for.
    assert_equal SPEC["precedence"],
                 C::PRECEDENCE.map { |r| { "kind" => r.kind, "mask" => r.mask, "tag" => r.tag } }
  end

  def test_the_specs_suffix_lists_are_this_builds_suffix_lists
    # The two classification arms are only as ported as the words they read, and
    # the token lists reach 3 of 46 organisation suffixes and 3 of 36 landmark
    # suffixes — `inc`, `school`, `church` and `library`, `memorial`, `park`.
    # Every other entry was hand-transliterated and, until this assertion,
    # checked by nothing: a port missing `hospital` or `valley` stayed green
    # here, in the frames, and in the gates, and would quietly keep a town or
    # mask a landmark in prose the fixture happens not to contain.
    assert_equal SPEC["suffixes"]["organization"], C::ORG_SUFFIXES.to_a.sort
    assert_equal SPEC["suffixes"]["landmark"], C::LANDMARK_SUFFIXES.to_a.sort
  end

  def test_the_specs_hand_typed_word_lists_are_this_builds_in_order
    # The last data in candidate generation that no case pins: the spec's inputs
    # exercise 7 of 32 honorifics, 3 of 19 particles and 2 of 16 clitics.
    # Compared IN ORDER, not sorted — honorifics and particles become regex
    # alternations and `without_clitic` strips the first match, so a port that
    # reordered any of them builds a different pattern while holding the same
    # set.
    assert_equal SPEC["word_lists"]["honorifics"], C::HONORIFICS
    assert_equal SPEC["word_lists"]["particles"], C::PARTICLES
    assert_equal SPEC["word_lists"]["clitics"], C::CLITICS
  end

  def test_the_specs_relation_word_lists_are_this_builds
    # Same argument as the suffix lists: 37 cues, 13 proximity phrases and 6
    # pronouns, every one typed by hand in each port, and the span cases exercise
    # only a fraction of them. `overridable_tiers` is the policy half — a port
    # that let the override reach `place` would redact a town the tier
    # deliberately keeps, and no case would say so.
    assert_equal SPEC["relation"]["cues"], C::RELATION_CUES.to_a.sort
    assert_equal SPEC["relation"]["proximity_cues"], C::PROXIMITY_CUES
    assert_equal SPEC["relation"]["first_person"], C::FIRST_PERSON.to_a.sort
    assert_equal SPEC["relation"]["overridable_tiers"], C::OVERRIDABLE_TIERS.to_a.sort
  end

  def test_the_specs_corroborating_tier_is_this_builds
    # The policy string, checked separately for the reason `constants` is: a port
    # that compared against "person" or "notable" corroborates nothing, and every
    # corpus entry still passes because the spans involved were being masked
    # either way. The failure is silent in output and visible only here.
    assert_equal SPEC["corroboration"]["tier"], C::CORROBORATING_TIER
  end

  # -------------------------------------------------------------------------
  # The sections
  # -------------------------------------------------------------------------

  section("is_stop", STOP_TOKENS) { |token| C.stop?(token) }

  section("trim", LISTS) { |tokens| C.trim(tokens) }
  section("classify", LISTS) { |tokens| C.classify(tokens) }
  section("classify_with_settlement", LISTS) { |tokens| C.classify(tokens, IS_SETTLEMENT) }
  section("classify_tags", LISTS) { |tokens| C.classify_tags(tokens).to_a.sort }
  section("classify_tags_with_settlement", LISTS) do |tokens|
    C.classify_tags(tokens, IS_SETTLEMENT).to_a.sort
  end
  section("masks_with_settlement", LISTS) do |tokens|
    C.resolve(C.classify_tags(tokens, IS_SETTLEMENT)).mask
  end

  section("names_someone_in_the_writers_life", SPANS) do |c|
    C.names_someone_in_the_writers_life?(c["text"], c["start"], c["end"])
  end
  section("names_someone_the_writer_knows", SPANS) do |c|
    C.names_someone_the_writer_knows?(c["text"], c["start"], c["end"])
  end
  section("title_is_the_writers_own_relation", SPANS) do |c|
    C.title_is_the_writers_own_relation?(c["text"], c["start"], c["end"])
  end
  section("relation_led_title_is_internally_mixed", SPANS) do |c|
    C.relation_led_title_is_internally_mixed?(c["text"], c["start"], c["end"])
  end

  section("word_token", CORPUS) { |text| matches(C::WORD_TOKEN, text) }
  section("lower_token", CORPUS) { |text| matches(C::LOWER_TOKEN, text) }
  section("any_token", CORPUS) { |text| matches(C::ANY_TOKEN, text) }
  section("candidate_re", CORPUS) { |text| matches(C::CANDIDATE_RE, text) }
  section("protected", CORPUS) { |text| matches(C::PROTECTED, text) }

  section("sentence_starts", CORPUS) { |text| C.sentence_starts(text).to_a.sort }
  section("emphasis_spans", CORPUS) { |text| C.emphasis_spans(text) }
  section("heading_spans", CORPUS) { |text| C.heading_spans(text) }

  section("title_spans", CORPUS) do |text|
    C.find_title_spans(text, IS_TITLE, IS_TITLE_PREFIX)
  end
  section("title_spans_requires_capital", CORPUS) do |text|
    C.find_title_spans(text, IS_TITLE, IS_TITLE_PREFIX, requires_capital: true)
  end

  section("capitalisation_habit", CORPUS) { |text| C.capitalisation_habit(text) }
  section("capitalisation_habit_with_headings", CORPUS) do |text|
    C.capitalisation_habit(text, C.heading_spans(text))
  end

  section("mid_sentence_capitals", CORPUS) do |text|
    C.mid_sentence_capitals(text, C.sentence_starts(text)).to_a.sort
  end
  section("mid_sentence_capitals_with_headings", CORPUS) do |text|
    C.mid_sentence_capitals(text, C.sentence_starts(text), C.heading_spans(text)).to_a.sort
  end

  section("surname_tokens", NAME_FORMS) { |name| C.surname_tokens(name) }
  section("bare_surname_key", NAME_FORMS) { |name| C.bare_surname_key(name) }
  section("surname_forms", NAME_FORMS) { |name| C.surname_forms(name) }

  # Candidate generation, end to end. Both arms, because they are different
  # detectors: without oracles the capitalised route runs alone and the
  # corroboration guard is unreachable by construction, and a port that wired the
  # oracles into only one of the two would pass the other.
  section("find_candidates_without_oracles", CORPUS) { |text| as_rows(C.find_candidates(text)) }
  # The lowercase route at its limit. The only arm that reaches the overlap guard
  # — see the generator for why no realistic oracle does.
  section("find_candidates_permissive_given", CORPUS) do |text|
    as_rows(C.find_candidates(text, given_name: ->(_t) { true }))
  end
  section("find_candidates", CORPUS) { |text| as_rows(C.find_candidates(text, WIRED)) }

  section("corroborated_surnames", CORPUS) do |text|
    C.corroborated_surnames(C.find_candidates(text, WIRED), IS_NOTABLE, Set.new, TIER_OF)
     .to_a.sort
  end
  section("established_name_tokens", CORPUS) do |text|
    C.established_name_tokens(text, IS_NOTABLE, Set.new, TIER_OF).to_a.sort
  end

  # A FRESH minter per text — numbering is per-document, and one minter across the
  # corpus would make every case depend on the case before it.
  section("mask_candidates", CORPUS) do |text|
    C.mask_candidates(text, MASKING.merge(minter: Vicary::PlaceholderMinter.new))
  end
  section("mask_candidates_unnumbered", CORPUS) { |text| C.mask_candidates(text, MASKING) }
  section("mask_candidates_with_keeps", CORPUS) do |text|
    C.mask_candidates(text, MASKING.merge(keep: KEEPS, minter: Vicary::PlaceholderMinter.new))
  end
  section("mask_candidates_without_notability", CORPUS) do |text|
    C.mask_candidates(text, WIRED.merge(minter: Vicary::PlaceholderMinter.new))
  end

  def test_every_section_the_spec_carries_is_checked_by_this_file
    # The one assertion that cannot be written as a section, and the one that
    # keeps this file honest: a primitive added to the generator and not wired up
    # here would otherwise ship unchecked, which is the exact gap the file exists
    # to close.
    assert_equal [], SPEC["cases"].keys - CHECKED,
                 "the spec carries a primitive this port does not check"
  end
end
