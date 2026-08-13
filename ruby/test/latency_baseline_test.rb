# frozen_string_literal: true

require "json"
require "minitest/autorun"
require "tmpdir"

require "vicary"

# The latency regression comparison, including every refusal to make it.
#
# A relative gate has two ways to be useless and only one of them is loud. It can
# fail on hardware differences, which everybody notices; or it can quietly
# decline to compare and report that as a pass, which nobody notices until a
# regression ships. So the cases below assert the *reason* as well as the
# verdict, and the last few prove the gate still fails on an actual slowdown — a
# comparison that cannot fail is not a gate.
#
# This port's own suite. It checks Ruby's implementation of the comparison, not
# Python's answer about it. This is the port whose refusal caught the split
# release, so it is the one that least deserves an untested comparison.
class LatencyBaselineTest < Minitest::Test
  TOLERANCE = 8.0
  PROFILE = "github-ubuntu-latest"
  CORPUS = "persuade-20"
  LANG = "3.3"

  def with_baseline(pooled_median_ms, lang: LANG, corpus: CORPUS)
    Dir.mktmpdir do |dir|
      File.write(
        File.join(dir, Vicary::LatencyBaseline::BASELINE_FILENAME),
        JSON.dump(
          "document_version" => 1,
          "tolerance_pct" => TOLERANCE,
          "profile" => { "id" => PROFILE, "language_versions" => { "ruby" => lang } },
          "corpus" => corpus,
          "implementations" => { "ruby" => { "pooled_median_ms" => pooled_median_ms } }
        )
      )
      yield dir
    end
  end

  def compare(measured, dir, corpus: CORPUS, profile: PROFILE, lang: LANG)
    Vicary::LatencyBaseline.compare(
      measured, corpus, dir: dir, profile_env: profile, observed_language_version: lang
    )
  end

  def test_a_checkout_with_no_baseline_file_declines
    Dir.mktmpdir do |dir|
      c = compare(10.0, dir)
      refute c.comparable
      refute c.holds?
      assert_includes c.reason, Vicary::LatencyBaseline::BASELINE_FILENAME
    end
  end

  # The common case: a laptop, which has no business comparing itself against a
  # number recorded on a CI runner.
  def test_an_unclaimed_machine_declines
    with_baseline(10.0) do |dir|
      c = compare(10.0, dir, profile: "")
      refute c.comparable
      assert_includes c.reason, Vicary::LatencyBaseline::PROFILE_ENV_VAR
    end
  end

  def test_a_different_profile_declines
    with_baseline(10.0) do |dir|
      c = compare(10.0, dir, profile: "someones-laptop")
      refute c.comparable
      assert_includes c.reason, "someones-laptop"
    end
  end

  # Ruby 3.1 measured 15.9 ms where 3.3 measured 11.0 on one commit — a gap
  # several times the bar, so this must not be compared away as a regression.
  def test_a_different_interpreter_declines
    with_baseline(10.0) do |dir|
      c = compare(10.0, dir, lang: "3.1")
      refute c.comparable
      assert_includes c.reason, "3.1"
      assert_includes c.reason, "3.3"
    end
  end

  def test_a_different_corpus_declines
    with_baseline(10.0) do |dir|
      c = compare(10.0, dir, corpus: "asap-aes-set8")
      refute c.comparable
      assert_includes c.reason, "asap-aes-set8"
    end
  end

  # Nil is not zero and not a free pass.
  def test_an_unrecorded_baseline_declines_rather_than_passes
    with_baseline(nil) do |dir|
      c = compare(10.0, dir)
      refute c.comparable
      refute c.holds?
    end
  end

  def test_unchanged_code_holds
    with_baseline(10.0) do |dir|
      c = compare(10.0, dir)
      assert c.comparable
      assert c.holds?
      assert_in_delta 0.0, c.regression_pct, 1e-9
    end
  end

  def test_within_the_tolerance_holds
    with_baseline(10.0) do |dir|
      c = compare(10.7, dir)
      assert c.holds?
      assert_in_delta 7.0, c.regression_pct, 1e-9
    end
  end

  # The negative control. If this ever passes, every case above is decoration.
  def test_a_real_slowdown_fails
    with_baseline(10.0) do |dir|
      c = compare(12.0, dir)
      assert c.comparable
      assert_in_delta 20.0, c.regression_pct, 1e-9
      refute c.holds?
    end
  end

  def test_just_over_the_bar_fails
    with_baseline(10.0) do |dir|
      c = compare(10.81, dir)
      assert c.comparable
      refute c.holds?
      assert_operator c.regression_pct, :>, TOLERANCE
    end
  end

  def test_getting_faster_is_never_a_failure
    with_baseline(10.0) do |dir|
      c = compare(5.0, dir)
      assert c.holds?
      assert_in_delta(-50.0, c.regression_pct, 1e-9)
    end
  end

  # The real file, not a fixture — a malformed one would make every port decline
  # to compare and read as eight quiet passes.
  def test_the_shipped_baseline_file_parses_and_declares_its_profile
    doc = Vicary::LatencyBaseline.load
    refute_nil doc, "conformance/latency_baseline.json is missing"
    assert_equal TOLERANCE, doc["tolerance_pct"]
    refute_empty doc.dig("profile", "id").to_s
    assert_equal %w[python ruby typescript], doc["implementations"].keys.sort
    doc["implementations"].each_key do |impl|
      assert doc["implementations"][impl].key?("pooled_median_ms"), impl
      refute_empty doc.dig("profile", "language_versions", impl).to_s, impl
    end
  end
end
