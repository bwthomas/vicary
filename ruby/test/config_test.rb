# frozen_string_literal: true

# Environment-variable resolution: which name wins, and what counts as unset.
#
# The counterpart of `typescript/test/config.test.ts`. This port reads five
# variables — the asset path, the redaction arm, the corpus TSV, the corpus
# directory and the Census CSV — and tested the resolution of none. Python has 34
# tests here, but most of them are the seven legacy names it kept a fallback for
# when it became a library, which is history this port never had. What was NOT
# justified is the other half, and this file is it.
#
# Why resolution specifically. Every one of these five is read once, at the edge,
# and a wrong answer does not raise: pointing `VICARY_ASSET_PATH` at a directory
# with no gazetteer loads an empty index and redacts every public figure in every
# essay; a corpus variable resolving to `""` reports a gate as NOT MEASURED, which
# reads as "operator supplied no data" rather than "the operator did and we dropped
# it". Both look exactly like success from every log line.
#
# The whitespace cases are not padding. A `.env` file written by hand carries
# trailing spaces, and `" "` must count as unset rather than as a path to a
# directory whose name is a space.
#
# The declared gap this closes is recorded in `conformance/coverage.json`, and
# `tools/tests/test_coverage_parity.py` fails if its entry outlives it.

require "minitest/autorun"

require "vicary"

class ConfigTest < Minitest::Test
  # Run the block with exactly these variables set, restoring the environment
  # after. `nil` deletes rather than setting the string "nil" — the trap that
  # makes an "unset" case silently test a set one.
  def with_env(vars)
    saved = vars.keys.to_h { |name| [name, ENV.fetch(name, nil)] }
    vars.each { |name, value| value.nil? ? ENV.delete(name) : ENV[name] = value }
    yield
  ensure
    saved.each { |name, value| value.nil? ? ENV.delete(name) : ENV[name] = value }
  end

  # -------------------------------------------------------------------------
  # The asset path
  # -------------------------------------------------------------------------

  ASSET_VAR = Vicary::Asset::ASSET_PATH_ENV_VAR

  def test_the_asset_override_is_consulted_before_either_bundled_location
    # An operator pointing at a different cut must not be silently overruled by
    # the copy this gem vendored, which is the whole purpose of the override.
    with_env(ASSET_VAR => "/tmp/some-other-cut") do
      assert_equal "/tmp/some-other-cut", Vicary::Asset.search_path.first.to_s
    end
  end

  def test_an_unset_asset_override_contributes_no_candidate_at_all
    # Not an empty-string entry in the list: an empty candidate joins to a
    # relative path, so it silently searches the process's working directory.
    with_override = with_env(ASSET_VAR => "/tmp/x") { Vicary::Asset.search_path }
    without = with_env(ASSET_VAR => nil) { Vicary::Asset.search_path }
    assert_equal with_override.length - 1, without.length
    without.each { |candidate| refute_empty candidate.to_s }
  end

  def test_a_whitespace_only_asset_override_is_unset_not_a_directory_named_space
    unset = with_env(ASSET_VAR => nil) { Vicary::Asset.search_path }
    ["", " ", "   ", "\t", "\n"].each do |blank|
      assert_equal unset, with_env(ASSET_VAR => blank) { Vicary::Asset.search_path },
                   blank.inspect
    end
  end

  def test_the_asset_override_is_stripped_rather_than_used_raw
    with_env(ASSET_VAR => "  /tmp/padded  ") do
      assert_equal "/tmp/padded", Vicary::Asset.search_path.first.to_s
    end
  end

  def test_the_bundled_locations_stay_in_most_specific_first_order
    # The vendored copy is what an installed gem has; the monorepo's Python
    # package is what a checkout has before `rake sync_assets`. Reversing them
    # makes a checkout read a stale asset that a publish would never ship.
    path = with_env(ASSET_VAR => nil) { Vicary::Asset.search_path }.map(&:to_s)
    vendored = path.index { |p| p.end_with?("assets") }
    monorepo = path.index { |p| p.include?("python") }
    refute_nil vendored, "no vendored candidate in #{path.inspect}"
    refute_nil monorepo, "no monorepo candidate in #{path.inspect}"
    assert_operator vendored, :<, monorepo, "the vendored copy must be searched first"
  end

  # -------------------------------------------------------------------------
  # The redaction arm
  # -------------------------------------------------------------------------

  ARM_VAR = Vicary::NAME_DETECTION_ENV_VAR

  def test_the_environment_supplies_the_arm_when_the_caller_does_not
    with_env(ARM_VAR => "identity") do
      assert_equal Vicary::NAMES_IDENTITY, Vicary.name_detection
    end
    with_env(ARM_VAR => "gazetteer") do
      assert_equal Vicary::NAMES_GAZETTEER, Vicary.name_detection
    end
  end

  def test_an_explicit_argument_beats_the_environment
    # The precedence a host depends on to override a deployment-wide default for
    # one call. Reversing it makes the argument decorative, and every caller that
    # passes one keeps getting the env's answer.
    with_env(ARM_VAR => "identity") do
      assert_equal Vicary::NAMES_LOWERCASE, Vicary.name_detection(Vicary::NAMES_LOWERCASE)
      assert_equal Vicary::NAMES_GAZETTEER, Vicary.name_detection(Vicary::NAMES_GAZETTEER)
    end
  end

  def test_an_unset_environment_reaches_the_code_default
    with_env(ARM_VAR => nil) do
      assert_equal Vicary::DEFAULT_NAME_DETECTION, Vicary.name_detection
      assert_equal Vicary::NAMES_LOWERCASE, Vicary.name_detection
    end
  end

  def test_a_whitespace_only_arm_is_unset_rather_than_unrecognised
    # Both land on the default here, so this pins WHY rather than what: an unset
    # variable and a typo must not be distinguishable to a caller, because the
    # fail-safe for both is the same and a future edit that split them would turn
    # the typo case into `identity`.
    ["", " ", "\t\n"].each do |blank|
      with_env(ARM_VAR => blank) do
        assert_equal Vicary::DEFAULT_NAME_DETECTION, Vicary.name_detection, blank.inspect
      end
    end
  end

  def test_the_arm_variable_is_spelled_the_same_as_every_other_ports
    # Three front doors reading three different names is a deployment that thinks
    # it configured all of them.
    assert_equal "VICARY_NAME_DETECTION", ARM_VAR
  end

  # -------------------------------------------------------------------------
  # The corpus
  # -------------------------------------------------------------------------

  TSV_VAR = Vicary::Corpus::EVAL_CORPUS_TSV_ENV_VAR
  DIR_VAR = Vicary::Corpus::EVAL_CORPUS_DIR_ENV_VAR
  PREFERRED = Vicary::Corpus::EVAL_CORPUS_PREFERRED_FILENAME

  def test_the_explicit_tsv_wins_over_the_directory_form
    with_env(TSV_VAR => "/data/explicit.tsv", DIR_VAR => "/data/dir") do
      assert_equal "/data/explicit.tsv", Vicary::Corpus.corpus_source
    end
  end

  def test_the_directory_form_appends_the_preferred_filename
    with_env(TSV_VAR => nil, DIR_VAR => "/data/dir") do
      assert_equal "/data/dir/#{PREFERRED}", Vicary::Corpus.corpus_source
    end
    assert_equal "corpus.tsv", PREFERRED
  end

  def test_neither_variable_set_resolves_to_the_empty_string_not_a_bare_filename
    # `""` is what the gate suite reads as NOT MEASURED. A bare "corpus.tsv" would
    # instead be looked up relative to the working directory, so a gate would
    # measure whatever happened to be beside the process.
    with_env(TSV_VAR => nil, DIR_VAR => nil) do
      assert_equal "", Vicary::Corpus.corpus_source
    end
  end

  def test_a_whitespace_only_corpus_variable_is_unset_and_falls_through
    # The realistic `.env` defect: the TSV name is present but empty, and the
    # directory below it is the real configuration. Treating `" "` as set makes
    # the corpus unreadable while reporting a path.
    with_env(TSV_VAR => "   ", DIR_VAR => "/data/dir") do
      assert_equal "/data/dir/#{PREFERRED}", Vicary::Corpus.corpus_source
    end
    with_env(TSV_VAR => " ", DIR_VAR => "  ") do
      assert_equal "", Vicary::Corpus.corpus_source
    end
  end

  def test_both_corpus_paths_are_stripped_rather_than_used_raw
    with_env(TSV_VAR => "  /data/padded.tsv \n") do
      assert_equal "/data/padded.tsv", Vicary::Corpus.corpus_source
    end
    with_env(TSV_VAR => nil, DIR_VAR => " /data/dir ") do
      assert_equal "/data/dir/#{PREFERRED}", Vicary::Corpus.corpus_source
    end
  end

  # -------------------------------------------------------------------------
  # The Census file
  # -------------------------------------------------------------------------

  CENSUS_VAR = Vicary::Census::EVAL_CENSUS_CSV_ENV_VAR

  def test_the_census_path_is_read_stripped_and_empty_when_unset
    with_env(CENSUS_VAR => "/data/Names_2010Census.csv") do
      assert_equal "/data/Names_2010Census.csv", Vicary::Census.census_source
    end
    with_env(CENSUS_VAR => "  /data/padded.csv  ") do
      assert_equal "/data/padded.csv", Vicary::Census.census_source
    end
    [nil, "", " ", "\t"].each do |blank|
      with_env(CENSUS_VAR => blank) do
        assert_equal "", Vicary::Census.census_source, blank.inspect
      end
    end
  end

  def test_every_variable_this_port_reads_is_spelled_the_way_the_others_spell_it
    # The list is asserted rather than described, so adding a sixth variable to
    # one port and not the others shows up here as a failing name rather than as
    # a deployment that configured two of three front doors.
    assert_equal ["VICARY_ASSET_PATH", "VICARY_EVAL_CENSUS_CSV", "VICARY_EVAL_CORPUS_DIR",
                  "VICARY_EVAL_CORPUS_TSV", "VICARY_NAME_DETECTION"],
                 [ASSET_VAR, ARM_VAR, TSV_VAR, DIR_VAR, CENSUS_VAR].sort
  end
end
