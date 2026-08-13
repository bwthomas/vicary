# frozen_string_literal: true

require "json"
require "pathname"

module Vicary
  # Is this build slower than the last release, and is that a fair question here?
  #
  # The gate has asked this three ways. The first two are worth keeping in view,
  # because each looked correct until it decided a release.
  #
  # **An absolute bar — 10 ms.** A claim about the machine as much as about the
  # code. It passed on a laptop and failed on the CI runner enforcing it, so
  # v0.2.3 published to PyPI and npm and was refused by RubyGems on the same
  # commit. This gem is the one that caught it.
  #
  # **A stored baseline** — record each release's number and compare the next run
  # against it, refusing unless the run claims the profile the baseline was
  # recorded on. Better, and still wrong, for a reason no estimator fixes: the
  # profile `github-ubuntu-latest` is not a machine. Thirty-six processes across
  # six runners per port, on identical code, spread 67% in THIS port — 6.53 ms on
  # an Intel Xeon 6973P-C against 10.63 ms on an EPYC 7763 — 26% in Python and
  # 21% in TypeScript, against an 8% bar. One probe run drew five CPU models from
  # that one label, and two runners of the same model still differed by 26%.
  #
  # **A pair, measured here.** The previous release's code and this checkout,
  # measured on the SAME machine, interleaved and counterbalanced, by
  # `tools/latency_pair.py`. Every property of the machine is common to both
  # sides and cancels; what is left is within-process noise, 1.7% in this port.
  #
  # Which leaves this module the job it has always had: REFUSING to compare when
  # the two sides would not be like for like. What changed is that the refusals
  # are about the pair record — is there one, is it this port's, was it measured
  # on these essays, was it measured for this commit — rather than about the
  # profile of a machine somewhere else.
  #
  # This port reaches its own verdict from the shared record. It does not read
  # Python's answer.
  module LatencyBaseline
    # The tolerance and the protocol, in the repository. Not a measurement:
    # nothing is recorded at release time any more, because the comparison point
    # is the previous release's *code*, which the repository already has.
    SPEC_FILENAME = "latency_baseline.json"

    # Where `tools/latency_pair.py` left the paired measurement. Set by CI in the
    # same job, seconds before the gate runs. Absent on a laptop unless the
    # harness was run there by hand, and that absence is a refusal to compare
    # rather than a pass — measuring one side of a comparison is not a gate.
    PAIR_ENV_VAR = "VICARY_LATENCY_PAIR"

    # What this reader understands. A record from a future shape is refused
    # rather than half-read: a partly-understood record still yields a number,
    # and a number is exactly what must not be invented here.
    PAIR_DOCUMENT_VERSION = 1

    IMPLEMENTATION = "ruby"

    # The bar, chosen rather than derived — 8% is what a reviewer is willing to
    # call a regression. What the noise decides is whether the bar is USABLE,
    # and it is: measured in TypeScript, the noisiest port, the gate statistic
    # holds sigma 1.71% over twelve runs on six CI runners, so 8% is 4.7 sigma
    # out. It was about a third of a sigma under the stored baseline, which is
    # how that one red-lit `main` on unchanged code — and how it refused this
    # port's 0.2.3 while the other two took the same commit. See
    # `tools/latency_pair.py`.
    #
    # It does not catch drift: +5% a release passes every time and compounds.
    # That is deliberate — this gate is for the step change, not the trend.
    DEFAULT_TOLERANCE_PCT = 8.0

    # The gate's answer, and — when it declines — why.
    Comparison = Struct.new(
      :measured_ms, :previous_ms, :current_ms, :regression_pct, :tolerance_pct,
      :against, :comparable, :reason,
      keyword_init: true
    ) do
      def holds?
        return false unless comparable && !regression_pct.nil?

        regression_pct <= tolerance_pct
      end
    end

    class << self
      def spec_path(dir = nil)
        root = dir || Conformance.directory
        return nil if root.nil?

        path = Pathname.new(root).join(SPEC_FILENAME)
        path.exist? ? path : nil
      end

      def load(dir = nil)
        path = spec_path(dir)
        return nil if path.nil?

        JSON.parse(path.read)
      end

      # The paired measurement, or why there is none to read.
      #
      # An unreadable file and an absent one stay distinguishable: the first is a
      # broken harness and the second is an ordinary laptop, and they should not
      # report the same thing.
      def load_pair(path = nil)
        given = (path || ENV[PAIR_ENV_VAR] || "").strip
        if given.empty?
          return [nil,
                  "#{PAIR_ENV_VAR} is unset, so no paired measurement was taken on " \
                  "this machine; the gate compares this build against the last " \
                  "release measured HERE, and one side of a comparison is not a gate"]
        end
        return [nil, "#{PAIR_ENV_VAR}=#{given.inspect} does not exist"] unless File.exist?(given)

        begin
          [JSON.parse(File.read(given)), nil]
        rescue StandardError => e
          [nil, "the pair record at #{given} could not be read: #{e.message}"]
        end
      end

      # Compare the pair measured on this machine, for this port.
      #
      # +measured_ms+ is this process's own figure. It is reported either way and
      # it is never the verdict: the verdict comes from the two numbers in the
      # pair record, taken back to back on one machine. Mixing this process's
      # measurement with the pair's other side would reintroduce exactly the
      # machine difference the pair exists to cancel.
      def compare(measured_ms, corpus_id, dir: nil, implementation: IMPLEMENTATION,
                  pair_path: nil, building_sha: nil)
        doc = load(dir) || {}
        tolerance = (doc["tolerance_pct"] || DEFAULT_TOLERANCE_PCT).to_f

        declined = lambda do |reason|
          Comparison.new(measured_ms: measured_ms, previous_ms: nil, current_ms: nil,
                         regression_pct: nil, tolerance_pct: tolerance, against: nil,
                         comparable: false, reason: reason)
        end

        record, why = load_pair(pair_path)
        return declined.call(why || "no paired measurement") if record.nil?

        unless record["document_version"] == PAIR_DOCUMENT_VERSION
          return declined.call(
            "the pair record is document_version #{record['document_version']} " \
            "and this reader knows #{PAIR_DOCUMENT_VERSION}"
          )
        end
        unless record["implementation"] == implementation
          return declined.call(
            "the pair record measures #{record['implementation'].inspect}, " \
            "not #{implementation.inspect}"
          )
        end
        unless record["corpus"] == corpus_id
          return declined.call(
            "the pair was measured on corpus #{record['corpus'].inspect} and this " \
            "run is #{corpus_id.inspect}; latency scales with essay length"
          )
        end

        # Only where there is something to check against. `GITHUB_SHA` names the
        # commit the job is building, so a record left over from an earlier
        # commit is caught here rather than being read as this build's verdict.
        # Locally there is no such witness and no such risk: the harness is run
        # by hand, minutes before, on the tree in front of you.
        building = (building_sha || ENV["GITHUB_SHA"] || "").strip
        head = record["head_sha"].to_s
        if !building.empty? && !head.empty? && building != head
          return declined.call(
            "the pair was measured for commit #{head[0, 12]} and this job is " \
            "building #{building[0, 12]}; the record is stale"
          )
        end

        previous = record["previous_ms"]
        current = record["current_ms"]
        unless previous.is_a?(Numeric) && current.is_a?(Numeric)
          return declined.call("the pair record carries no pair of measurements")
        end
        if previous <= 0
          return declined.call(
            "the previous release measured #{previous} ms, which is not positive"
          )
        end

        Comparison.new(
          measured_ms: measured_ms, previous_ms: previous.to_f,
          current_ms: current.to_f,
          regression_pct: (current.to_f / previous.to_f - 1.0) * 100.0,
          tolerance_pct: tolerance, against: (record["against"] || {})["ref"],
          comparable: true, reason: nil
        )
      end

      def render(comparison)
        c = comparison
        unless c.comparable
          return format("latency %.3f ms — NOT COMPARED against the last release: %s",
                        c.measured_ms, c.reason)
        end

        sign = c.regression_pct >= 0 ? "+" : ""
        format("latency %.3f ms here; paired on this machine, %.3f ms against " \
               "%s's %.3f ms — %s%.2f%% against a %d%% bar",
               c.measured_ms, c.current_ms, c.against || "the last release",
               c.previous_ms, sign, c.regression_pct, c.tolerance_pct)
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
