# frozen_string_literal: true

require "json"
require "minitest/autorun"
require "tmpdir"

require "vicary"

# The latency regression comparison, including every refusal to make it.
#
# A relative gate has two ways to be useless and only one of them is loud. It can
# fail on differences that are not the code — which everybody notices, because it
# red-lights a green build — or it can quietly decline to compare and report that
# as a pass, which nobody notices until a regression ships. So the cases below
# assert the *reason* as well as the verdict, and several prove the gate still
# fails on an actual slowdown: a comparison that cannot fail is not a gate.
#
# The refusals changed shape when the comparison did. They used to be about the
# machine this run is on versus the machine a number was recorded on. They are
# now about the paired record — is there one, is it this port's, was it taken on
# these essays, was it taken for this commit — because both sides are now
# measured on one machine and the machine cancels.
#
# This port's own suite. It checks Ruby's implementation of the comparison, not
# Python's answer about it. This is the port whose refusal caught the split
# release, so it is the one that least deserves an untested comparison.
class LatencyBaselineTest < Minitest::Test
  TOLERANCE = 8.0
  CORPUS = "persuade-20"
  HEAD = ("a" * 40)

  # A directory holding the spec, and a pair record beside it.
  def with_fixture(previous_ms: 10.0, current_ms: 10.0, implementation: "ruby",
                   corpus: CORPUS, head: HEAD,
                   document_version: Vicary::LatencyBaseline::PAIR_DOCUMENT_VERSION)
    Dir.mktmpdir do |dir|
      File.write(
        File.join(dir, Vicary::LatencyBaseline::SPEC_FILENAME),
        JSON.dump("document_version" => 2, "tolerance_pct" => TOLERANCE)
      )
      pair = File.join(dir, "pair.json")
      File.write(pair, JSON.dump(
        "document_version" => document_version,
        "implementation" => implementation,
        "corpus" => corpus,
        "head_sha" => head,
        "against" => { "ref" => "v0.2.4", "sha" => "b" * 40 },
        "previous_ms" => previous_ms,
        "current_ms" => current_ms
      ))
      yield dir, pair
    end
  end

  # Compare 10 ms measured here against a pair record shaped by the keywords.
  def compared(building_sha: "", **kwargs)
    with_fixture(**kwargs) do |dir, pair|
      return Vicary::LatencyBaseline.compare(
        10.0, CORPUS, dir: dir, pair_path: pair, building_sha: building_sha
      )
    end
  end

  # -------------------------------------------------------------------------
  # The refusals
  # -------------------------------------------------------------------------

  # The ordinary laptop case, and the one that must never read as a pass.
  def test_no_paired_measurement_declines
    with_fixture do |dir, _pair|
      previous = ENV.delete(Vicary::LatencyBaseline::PAIR_ENV_VAR)
      begin
        c = Vicary::LatencyBaseline.compare(10.0, CORPUS, dir: dir, building_sha: "")
        refute c.comparable
        refute c.holds?
        assert_includes c.reason, Vicary::LatencyBaseline::PAIR_ENV_VAR
      ensure
        ENV[Vicary::LatencyBaseline::PAIR_ENV_VAR] = previous unless previous.nil?
      end
    end
  end

  def test_a_missing_record_declines
    with_fixture do |dir, _pair|
      c = Vicary::LatencyBaseline.compare(10.0, CORPUS, dir: dir,
                                          pair_path: File.join(dir, "nope.json"),
                                          building_sha: "")
      refute c.comparable
      assert_includes c.reason, "does not exist"
    end
  end

  # A broken harness and an absent one must not report the same thing.
  def test_an_unreadable_record_declines_rather_than_passing
    with_fixture do |dir, _pair|
      broken = File.join(dir, "broken.json")
      File.write(broken, "{not json")
      c = Vicary::LatencyBaseline.compare(10.0, CORPUS, dir: dir, pair_path: broken,
                                          building_sha: "")
      refute c.comparable
      assert_includes c.reason, "could not be read"
    end
  end

  def test_a_record_this_reader_does_not_understand_declines
    c = compared(document_version: 99)
    refute c.comparable
    assert_includes c.reason, "document_version 99"
  end

  # Three ports write records side by side; reading TypeScript's is a wrong
  # answer rather than a missing one — the ports are 2-3x apart in absolute cost.
  def test_another_ports_record_declines
    c = compared(implementation: "typescript")
    refute c.comparable
    assert_includes c.reason, "typescript"
  end

  def test_another_corpus_declines
    c = compared(corpus: "asap-aes-set8")
    refute c.comparable
    assert_includes c.reason, "asap-aes-set8"
  end

  # A stale artifact is the failure this design invites: the record is a file,
  # and a file outlives the job that wrote it.
  def test_a_record_from_another_commit_declines
    c = compared(building_sha: "c" * 40)
    refute c.comparable
    assert_includes c.reason, "stale"
  end

  def test_the_commit_check_passes_when_the_record_is_this_build
    c = compared(building_sha: HEAD)
    assert c.comparable
    assert c.holds?
  end

  def test_a_previous_measurement_of_zero_declines
    c = compared(previous_ms: 0.0)
    refute c.comparable
    assert_includes c.reason, "not positive"
  end

  # -------------------------------------------------------------------------
  # The verdicts
  # -------------------------------------------------------------------------

  def test_unchanged_code_holds
    c = compared(previous_ms: 10.0, current_ms: 10.0)
    assert c.comparable
    assert c.holds?
    assert_in_delta 0.0, c.regression_pct, 1e-9
    assert_equal "v0.2.4", c.against
  end

  def test_within_the_tolerance_holds
    c = compared(previous_ms: 10.0, current_ms: 10.7)
    assert c.holds?
    assert_in_delta 7.0, c.regression_pct, 1e-9
  end

  def test_just_over_the_bar_fails
    c = compared(previous_ms: 10.0, current_ms: 10.81)
    assert c.comparable
    refute c.holds?
  end

  def test_a_real_slowdown_fails
    c = compared(previous_ms: 10.0, current_ms: 13.0)
    assert c.comparable
    refute c.holds?
    assert_in_delta 30.0, c.regression_pct, 1e-9
  end

  def test_getting_faster_is_never_a_failure
    c = compared(previous_ms: 10.0, current_ms: 6.0)
    assert c.holds?
    assert_in_delta(-40.0, c.regression_pct, 1e-9)
  end

  # The property the whole design rests on. This process's own figure is reported
  # and never gated: here it is 10 ms against a pair measured at 3 ms — the
  # laptop-versus-runner gap that broke both earlier designs — and the verdict
  # still comes from the two numbers taken back to back on one machine.
  def test_the_verdict_comes_from_the_pair_and_not_from_this_process
    c = compared(previous_ms: 3.0, current_ms: 3.1)
    assert c.comparable
    assert c.holds?
    assert_in_delta 10.0, c.measured_ms, 1e-9
    assert_in_delta((3.1 / 3.0 - 1.0) * 100.0, c.regression_pct, 1e-9)
    assert_includes Vicary::LatencyBaseline.render(c), "10.000 ms here"
  end

  # -------------------------------------------------------------------------
  # The file that ships
  # -------------------------------------------------------------------------

  # It carries no measurements on purpose, and a reader should be able to see
  # that this is deliberate rather than an empty file.
  def test_the_shipped_spec_declares_a_tolerance_and_a_protocol
    doc = Vicary::LatencyBaseline.load
    refute_nil doc
    assert_operator doc["tolerance_pct"].to_f, :>, 0
    assert_includes doc["protocol"], "paired"
    refute doc.key?("implementations"),
           "recorded per-release measurements are what the paired protocol " \
           "replaced; leaving them here would let a stale number be read as a gate"
  end
end
