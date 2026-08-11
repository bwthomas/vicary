# frozen_string_literal: true

# The release path's own tests.
#
# Two pieces of logic decide whether a gem named `vicary` reaches RubyGems and
# whether we believe it got there. Both used to live as shell inside
# `.github/workflows/release-gem.yml`, where the only way to exercise them was to
# cut a release — which is to say, they were never exercised, and the first time
# either ran for real would also be the first time anyone found out it was wrong.
#
# So: no network, no credential, no tag. The gate is driven in both directions,
# including the *allow* branch that this port has never once reached.

require "minitest/autorun"

require "vicary"
require_relative "../scripts/release_gate"
require_relative "../scripts/registry_serves"

class ReleaseGateTest < Minitest::Test
  def board(matched:, requiring:)
    Vicary::Conformance::Scoreboard.new(
      fixture_version: "test", reference_arm: "test", total: 54, matched: matched,
      requiring_masking: requiring, matched_requiring_masking: matched, outcomes: [],
    )
  end

  def test_an_incomplete_port_may_not_publish
    decision = ReleaseGate.decide(board(matched: 0, requiring: 38))
    refute decision.publishable,
           "a port matching 0 of 38 masking-required frames was cleared to publish. " \
           "That is a gem called vicary that does not redact."
    assert_includes decision.reason, "REFUSING TO PUBLISH"
  end

  def test_one_frame_short_may_not_publish
    # The interesting boundary. An off-by-one here reads as done on every log line
    # that prints a ratio and rounds it.
    decision = ReleaseGate.decide(board(matched: 37, requiring: 38))
    refute decision.publishable, "37 of 38 was cleared to publish"
  end

  def test_a_complete_port_may_publish
    # The branch this test existed to reach before any release had. If the gate
    # only ever refuses, it is indistinguishable from a gate that is stuck shut,
    # and the first real release is where that would have got discovered. 0.2.0
    # has since taken it for real, which is confirmation and not a replacement:
    # this runs on every commit and costs no version number.
    decision = ReleaseGate.decide(board(matched: 38, requiring: 38))
    assert decision.publishable, "38 of 38 was refused: the gate is stuck shut"
    assert_includes decision.reason, "byte-for-byte"
  end

  def test_a_denominator_of_zero_is_refused_rather_than_read_as_success
    # `matched == total` is true at 0 == 0. A spec that failed to load its frames
    # would otherwise clear the gate by describing nothing.
    decision = ReleaseGate.decide(board(matched: 0, requiring: 0))
    refute decision.publishable,
           "an empty spec cleared the publish gate by matching zero of zero"
    assert_includes decision.reason, "denominator of zero"
  end
end

class RegistryServesTest < Minitest::Test
  def payload(*numbers)
    JSON.dump(numbers.map { |n| { "number" => n, "platform" => "ruby" } })
  end

  def test_it_reads_the_version_numbers_the_api_returns
    answer = RegistryServes.parse(payload("0.2.0", "0.1.1"))
    assert_equal %w[0.2.0 0.1.1], answer.versions
    assert answer.serving?("0.2.0")
    refute answer.serving?("0.3.0")
  end

  def test_a_payload_that_is_not_json_is_unknown_rather_than_absent
    # The distinction the shell loop could not make. Fastly serving an HTML error
    # page must not read as "the version is not published".
    answer = RegistryServes.parse("<html>504 Gateway Timeout</html>")
    assert answer.unknown?
    refute answer.serving?("0.2.0")
    assert_includes answer.error, "did not return JSON"
  end

  def test_a_payload_whose_shape_moved_is_unknown_rather_than_absent
    answer = RegistryServes.parse(JSON.dump([{ "version" => "0.2.0" }]))
    assert answer.unknown?
    assert_includes answer.error, "payload shape moved"
  end

  def test_it_returns_success_once_the_registry_serves_the_version
    out = StringIO.new
    slept = []
    calls = 0
    fetcher = lambda do |_gem|
      calls += 1
      calls < 3 ? RegistryServes::Answer.new(versions: [], error: nil)
                : RegistryServes.parse(payload("0.2.0"))
    end

    code = RegistryServes.wait_for("vicary", "0.2.0", attempts: 5, interval: 10, out: out,
                                   fetcher: fetcher, sleeper: ->(s) { slept << s })

    assert_equal 0, code
    assert_equal 3, calls, "it stopped polling on the wrong attempt"
    assert_equal [10, 10], slept, "it slept between attempts, but not after the last one"
    assert_includes out.string, "rubygems.org is serving vicary 0.2.0"
  end

  def test_it_fails_when_the_registry_never_serves_the_version
    out = StringIO.new
    fetcher = ->(_gem) { RegistryServes::Answer.new(versions: ["0.1.1"], error: nil) }

    code = RegistryServes.wait_for("vicary", "0.2.0", attempts: 3, interval: 0, out: out,
                                   fetcher: fetcher, sleeper: ->(_s) {})

    assert_equal 1, code
    assert_includes out.string, "never served it across 3 attempts"
    assert_includes out.string, "Do not treat this"
  end

  def test_a_registry_it_cannot_reach_fails_rather_than_passing
    # The failure mode worth having a test for: an unreachable registry must not
    # be able to end the run in a state that reads as a verified publish.
    out = StringIO.new
    fetcher = ->(_gem) { RegistryServes::Answer.new(versions: nil, error: "connection refused") }

    code = RegistryServes.wait_for("vicary", "0.2.0", attempts: 2, interval: 0, out: out,
                                   fetcher: fetcher, sleeper: ->(_s) {})

    assert_equal 1, code
    assert_includes out.string, "could not read the registry"
  end
end
