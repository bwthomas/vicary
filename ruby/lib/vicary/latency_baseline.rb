# frozen_string_literal: true

require "json"
require "pathname"

module Vicary
  # Is this build slower than the last release, and is that a fair question here?
  #
  # The latency gate used to hold an absolute number — 10 ms — which is a claim
  # about the machine as much as about the code. It passed on a laptop and failed
  # on the CI runner enforcing it, so v0.2.3 published to PyPI and npm and was
  # refused by RubyGems on the same commit. This gem is the one that caught it.
  #
  # What replaced it asks a relative question: is this port slower than it was at
  # the last release, by more than the tolerance. That only means something
  # between measurements taken on comparable hardware, so this module's real work
  # is REFUSING to compare when they are not — a machine difference reported as a
  # code regression is worse than no gate, because it trains the reader to ignore
  # it.
  #
  # This port reaches its own verdict from the shared file. It does not read
  # Python's answer.
  module LatencyBaseline
    BASELINE_FILENAME = "latency_baseline.json"

    # Set by CI on the one matrix entry whose language version matches the
    # recorded profile. Absent everywhere else on purpose: a developer's laptop
    # measures the same commit two to three times faster than the runner, and
    # comparing that against a runner baseline reports a phantom improvement.
    PROFILE_ENV_VAR = "VICARY_LATENCY_PROFILE"

    IMPLEMENTATION = "ruby"

    DEFAULT_TOLERANCE_PCT = 8.0

    # The gate's answer, and — when it declines — why.
    Comparison = Struct.new(
      :measured_ms, :baseline_ms, :regression_pct, :tolerance_pct,
      :comparable, :reason,
      keyword_init: true
    ) do
      def holds?
        return false unless comparable && !regression_pct.nil?

        regression_pct <= tolerance_pct
      end
    end

    class << self
      def baseline_path(dir = nil)
        root = dir || Conformance.directory
        return nil if root.nil?

        path = Pathname.new(root).join(BASELINE_FILENAME)
        path.exist? ? path : nil
      end

      def load(dir = nil)
        path = baseline_path(dir)
        return nil if path.nil?

        JSON.parse(path.read)
      end

      # `major.minor` of the running Ruby, matching how the profile records it.
      def language_version
        RUBY_VERSION.split(".").first(2).join(".")
      end

      # Compare +measured_ms+ against the recorded baseline for this port.
      #
      # Every reason below is a refusal to compare, not a failure to measure: the
      # number was measured either way and is reported either way. What is
      # withheld is the verdict, because the two sides would not be like for like.
      def compare(measured_ms, corpus_id, dir: nil, implementation: IMPLEMENTATION,
                  observed_language_version: nil, profile_env: nil)
        doc = load(dir)
        tolerance = (doc && doc["tolerance_pct"] || DEFAULT_TOLERANCE_PCT).to_f
        lang = observed_language_version || language_version

        declined = lambda do |reason, baseline_ms = nil|
          Comparison.new(measured_ms: measured_ms, baseline_ms: baseline_ms,
                         regression_pct: nil, tolerance_pct: tolerance,
                         comparable: false, reason: reason)
        end

        return declined.call("no #{BASELINE_FILENAME} in this checkout") if doc.nil?

        profile = doc["profile"] || {}
        want_profile = profile["id"]
        have_profile = (profile_env || ENV[PROFILE_ENV_VAR] || "").strip
        if have_profile.empty?
          return declined.call(
            "#{PROFILE_ENV_VAR} is unset, so this machine does not claim to be " \
            "#{want_profile.inspect}; the baseline was recorded there"
          )
        end
        unless have_profile == want_profile
          return declined.call(
            "#{PROFILE_ENV_VAR}=#{have_profile.inspect} but the baseline was " \
            "recorded on #{want_profile.inspect}"
          )
        end

        want_lang = (profile["language_versions"] || {})[implementation]
        if !want_lang.nil? && want_lang.to_s != lang
          return declined.call(
            "#{implementation} #{lang} is not the #{want_lang} the baseline was " \
            "recorded on; interpreter versions differ by more than the bar"
          )
        end

        want_corpus = doc["corpus"]
        if !want_corpus.nil? && want_corpus != corpus_id
          return declined.call(
            "corpus #{corpus_id.inspect} is not the #{want_corpus.inspect} the " \
            "baseline was recorded on; latency scales with essay length"
          )
        end

        entry = (doc["implementations"] || {})[implementation] || {}
        recorded = entry["pooled_median_ms"]
        if recorded.nil?
          return declined.call(
            "no baseline recorded for #{implementation} yet — the next release " \
            "records one"
          )
        end

        recorded = recorded.to_f
        if recorded <= 0
          return declined.call(
            "recorded baseline for #{implementation} is not positive", recorded
          )
        end

        Comparison.new(
          measured_ms: measured_ms, baseline_ms: recorded,
          regression_pct: (measured_ms / recorded - 1.0) * 100.0,
          tolerance_pct: tolerance, comparable: true, reason: nil
        )
      end

      def render(comparison)
        c = comparison
        unless c.comparable
          return format("latency %.3f ms — NOT COMPARED against the last release: %s",
                        c.measured_ms, c.reason)
        end

        sign = c.regression_pct >= 0 ? "+" : ""
        format("latency %.3f ms vs %.3f ms at the last release — %s%.2f%% " \
               "against a %d%% bar",
               c.measured_ms, c.baseline_ms, sign, c.regression_pct, c.tolerance_pct)
      end

      # The keyword arguments Gates.measure wants. Returns the *detail* rather
      # than a value when the comparison was declined, so the gate reports NOT
      # MEASURED with the reason attached instead of quietly passing.
      def gate_fields(measured_ms, corpus_id, **opts)
        c = compare(measured_ms, corpus_id, **opts)
        if c.comparable
          { latency_regression_pct: c.regression_pct }
        else
          { latency_regression_detail: render(c) }
        end
      end
    end
  end
end
