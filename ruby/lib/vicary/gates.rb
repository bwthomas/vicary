# frozen_string_literal: true

require "set"

module Vicary
  # The gates, measured by this port rather than read from the spec.
  #
  # Five of the nine gates in `conformance/gates.json` need no data beyond the
  # fixture, so this port measures them. The other four declare `requires` —
  # `corpus` or `census` — and no package here ships either; they stay NOT
  # MEASURED, spelled out per gate, because five of nine held is a different
  # statement from nine of nine and a badge cannot tell them apart.
  #
  # **Why this is measured and not asserted from the golden.** The spec already
  # carries `aligns` and `mapping` per frame, computed by the reference. Reading a
  # gate's answer out of the file would make the port's gate report a restatement
  # of Python's, which is exactly the self-report MUST #6 warns about wearing an
  # external costume. Everything below is recovered from the port's own output by
  # chunk matching — the same way the reference recovers it, and without asking
  # the masker to report on itself.
  module Gates
    # Every placeholder the shipped classifier can emit.
    #
    # Anything else in masked output is malformed — a truncated or nested
    # placeholder is how a masking bug presents, and it reads as ordinary prose
    # to a downstream stage.
    KNOWN_PLACEHOLDERS = Set[
      "{NAME}",
      "{SCHOOL}",
      "{EMAIL}",
      "{URL}",
      "{US_SOCIAL_SECURITY_NUMBER}",
      "{IP_ADDRESS}",
      "{PHONE}",
      "{ADDRESS}",
      "{DATE_OF_BIRTH}",
      "{USERNAME}",
      "{ZIP_CODE}",
      "{AGE}",
      "{CREDIT_DEBIT_CARD_NUMBER}",
      "{ORGANIZATION}",
      "{LOCATION}",
    ].freeze

    # Deliberately loose, so it matches malformed output too — which is the point.
    PLACEHOLDER_RE = /\{[A-Za-z_0-9]*\}/.freeze

    # `\z` rather than `$`: Ruby's `$` also matches *before* a trailing newline,
    # so a token arriving with one would have its index left on. JavaScript's `$`
    # does not, and this must agree with the TypeScript port token for token.
    PLACEHOLDER_INDEX_RE = /_(\d+)\}\z/.freeze

    WEAK_TOKENS = Set["of", "van", "de", "la", "the", "der", "von", "mrs", "mr", "ms"].freeze

    # Invariant violations present at this fixture version, each one accounted for.
    #
    # Gated as an exact SET rather than a count, so a *new* violation fails even
    # though these do not — a ceiling of one would let a second defect in by
    # silently displacing this one.
    #
    # * `Robinson` — the documented, deliberately unpaid cost: once a document
    #   establishes "Jackie Robinson", a bare "Robinson" in it keeps, including a
    #   neighbour who shares the surname. No surname-level rule separates them.
    #
    # The companion check is the load-bearing half: an entry here that STOPS
    # occurring fails too, so a stale exemption cannot shelter the next defect of
    # the same shape. Two entries were retired from the Python list exactly that
    # way.
    ACCEPTED_VIOLATIONS = Set["leak\u0000NAME:Robinson"].freeze

    Alignment = Struct.new(:pairs, :ok, :reason, keyword_init: true)
    Violation = Struct.new(:kind, :detail, keyword_init: true)
    SpanOutcome = Struct.new(:frame_id, :entity, :literal, :verdict, :held_out,
                             :passed, keyword_init: true)
    # `value` is nil when this port does not measure the gate — never 0, which
    # would read as a measured failure.
    GateMeasurement = Struct.new(:gate, :value, :passed, :detail, keyword_init: true)
    GateReport = Struct.new(:measurements, :violations, :unaccounted,
                            :missing_accepted, keyword_init: true)

    class << self
      # `"{NAME_3}"` → `"{NAME}"`; an unnumbered token is returned unchanged.
      #
      # The index identifies *which* entity, the kind identifies *what* it is, and
      # every invariant here is about the kind.
      def placeholder_kind(token)
        token.sub(PLACEHOLDER_INDEX_RE, "}")
      end

      # Recover the span→placeholder mapping by matching the surviving prose.
      #
      # Splits `masked` at placeholder boundaries and reconstructs which region of
      # `original` each placeholder replaced. Recovered by chunk matching rather
      # than asked of the redactor, so it works against any masker without that
      # masker having to report its own spans.
      def align(original, masked)
        placeholders = masked.scan(PLACEHOLDER_RE)
        # The `-1` is load-bearing. Ruby's `split` DROPS trailing empty fields and
        # JavaScript's does not: for "a{X}" it would return ["a"] where the port
        # this mirrors returns ["a", ""]. That silently shortens the chunk list,
        # so the reconstruction below loses its final anchor and a placeholder at
        # the end of a sentence recovers the wrong region.
        parts = masked.split(PLACEHOLDER_RE, -1)

        if placeholders.empty?
          return Alignment.new(pairs: [], ok: false,
                               reason: "text changed with no placeholder emitted") if masked != original

          return Alignment.new(pairs: [], ok: true, reason: "")
        end

        # Anchored, all at once, rather than a left-to-right scan for each chunk in
        # turn. A greedy per-chunk `index` misaligns whenever a surviving chunk is
        # short enough to also occur inside the span that was just removed — a
        # trailing "." after a masked email address matches the "." inside the
        # address, and the recovered region collapses to one character. Anchoring
        # the whole reconstruction makes it consistent simultaneously, so a
        # candidate that cannot be completed to the end of the original is
        # rejected and the engine backtracks. The chunks are long, distinctive
        # prose, which is what keeps the lazy quantifiers from exploring.
        #
        # `\A`/`\z` rather than `^`/`$`, which in Ruby are line anchors: a
        # sentence containing a newline would otherwise let a partial
        # reconstruction satisfy the pattern and report `ok`.
        pattern = +"\\A" + Regexp.escape(parts[0])
        parts[1..].each { |chunk| pattern << "([\\s\\S]*?)#{Regexp.escape(chunk)}" }
        pattern << "\\z"

        found = Regexp.new(pattern).match(original)
        if found.nil?
          return Alignment.new(
            pairs: [], ok: false,
            reason: "masked text is not the original with spans replaced — prose was " \
                    "rewritten, reordered or dropped",
          )
        end

        regions = found.captures
        Alignment.new(
          pairs: placeholders.each_with_index.map { |p, i| [p, regions[i] || ""] },
          ok: true, reason: "",
        )
      end

      # Put the originals back the way an echo-fidelity restore would have to.
      #
      # Keyed on the placeholder token, because that is all a downstream consumer
      # has: the model echoes `{NAME}` and the caller must decide which name it
      # meant. With one token per entity type it cannot, which is what
      # `not-restorable` counts. Distinct from `Minter.restore`, which is handed a
      # map the masker built.
      def restore_by_token(masked, mapping)
        masked.gsub(PLACEHOLDER_RE) { |token| mapping.fetch(token, token) }
      end

      # True when the frame's sentence survives mask-then-restore exactly.
      def round_trips?(frame, masked)
        alignment = align(frame.sentence, masked)
        return false unless alignment.ok

        mapping = {}
        alignment.pairs.each { |placeholder, region| mapping[placeholder] ||= region }
        restore_by_token(masked, mapping) == frame.sentence
      end

      # Substrings whose survival proves a partial leak of `span`.
      #
      # A name masked halfway still identifies the person, so "the whole literal
      # is gone" is too weak a test on multi-token names.
      def leak_probes(span)
        return [] unless %w[NAME SCHOOL ORGANIZATION LOCATION].include?(span.entity)

        span.literal
            .split(/[\s\-]+/)
            .reject(&:empty?)
            .map { |t| t.sub(/\A[.,']+/, "").sub(/[.,']+\z/, "") }
            .select { |t| t.length >= 3 && !WEAK_TOKENS.include?(t.downcase) }
      end

      # Every structural invariant the masked text must satisfy.
      #
      # `leak` — a REDACT literal survived. `partial-leak` — the literal is gone
      # but a name token of it survived; worse than a miss, because it *looks*
      # redacted and recall scores it as a pass. `keep-destroyed` — a KEEP literal
      # was masked. `unknown-placeholder` — output carries a brace token nobody
      # emits. `chunk-alignment` — prose was rewritten rather than replaced.
      # `not-restorable` — one placeholder stands for two different originals.
      # `wrong-type` — masked, but as the wrong entity.
      def check_frame(frame, masked)
        out = []

        masked.scan(PLACEHOLDER_RE).uniq.each do |token|
          unless KNOWN_PLACEHOLDERS.include?(placeholder_kind(token))
            out << Violation.new(kind: "unknown-placeholder", detail: token)
          end
        end

        frame.spans.reject { |s| keep?(s) }.each do |span|
          if masked.include?(span.literal)
            out << Violation.new(kind: "leak", detail: "#{span.entity}:#{span.literal}")
            next
          end
          leak_probes(span).each do |probe|
            if /\b#{Regexp.escape(probe)}\b/.match?(masked)
              out << Violation.new(kind: "partial-leak",
                                   detail: "#{span.entity}:#{span.literal} → #{probe}")
            end
          end
        end

        frame.spans.select { |s| keep?(s) }.each do |span|
          unless masked.include?(span.literal)
            out << Violation.new(kind: "keep-destroyed",
                                 detail: "#{span.entity}:#{span.literal}")
          end
        end

        alignment = align(frame.sentence, masked)
        unless alignment.ok
          out << Violation.new(kind: "chunk-alignment", detail: alignment.reason)
          return out
        end

        seen = {}
        alignment.pairs.each do |placeholder, region|
          prior = seen[placeholder]
          if !prior.nil? && prior != region
            out << Violation.new(
              kind: "not-restorable",
              detail: "#{placeholder} ← #{prior.inspect} and #{region.inspect}",
            )
          end
          seen[placeholder] ||= region
        end

        frame.spans.reject { |s| keep?(s) }.each do |span|
          next if span.expect.nil? || masked.include?(span.literal)

          covering = alignment.pairs
                              .select { |_p, region| region.include?(span.literal) }
                              .map { |p, _region| placeholder_kind(p) }
          # `expect` carries its own braces — "{NAME}", not "NAME" — so it is
          # compared to `placeholder_kind` output directly. Wrapping it again
          # silently made every correctly-typed span a `wrong-type`, which read as
          # 41 violations and printed "expected {NAME} got {NAME}".
          if !covering.empty? && !covering.include?(span.expect)
            out << Violation.new(
              kind: "wrong-type",
              detail: "#{span.literal.inspect} expected #{span.expect} got #{covering[0]}",
            )
          end
        end

        out
      end

      def score_spans(frame, masked)
        frame.spans.map do |span|
          passed =
            if span.expect_count.nil?
              present = masked.include?(span.literal)
              keep?(span) ? present : !present
            else
              # Presence cannot decide a bare surname that also occurs inside a
              # kept full name, so this one is counted rather than tested for
              # absence.
              occurrences(masked, span.literal) == span.expect_count
            end
          SpanOutcome.new(frame_id: frame.frame_id, entity: span.entity,
                          literal: span.literal, verdict: span.verdict,
                          held_out: frame.held_out, passed: passed)
        end
      end

      # The key `ACCEPTED_VIOLATIONS` is written in. NUL, because neither half can
      # contain one.
      def violation_key(violation)
        "#{violation.kind}\u0000#{violation.detail}"
      end

      # Measure every gate this port can measure from the fixture, plus any whose
      # `requires` the caller has satisfied by supplying the data.
      #
      # `asset_entries` and `bare_surname_exposure` are passed in rather than
      # read here so this module stays free of the gazetteer and the filesystem —
      # a caller that wants those gates supplies the number, and one that does
      # not gets NOT MEASURED rather than a load.
      def measure(spec, gate_spec, asset_entries: nil, bare_surname_exposure: nil,
                  held_out_recall_carrier: nil, over_fire_per_essay: nil,
                  latency_p95_ms: nil)
        outcomes = []
        violations = []
        round_tripped = 0

        spec.frames.each do |frame|
          masked = yield(frame.sentence, spec.identity)
          outcomes.concat(score_spans(frame, masked))
          violations.concat(check_frame(frame, masked))
          round_tripped += 1 if round_trips?(frame, masked)
        end

        held_out_redact = outcomes.select { |o| o.held_out && o.verdict != "keep" }
        keeps = outcomes.select { |o| o.verdict == "keep" }
        unaccounted = violations.reject { |v| ACCEPTED_VIOLATIONS.include?(violation_key(v)) }
        occurred = violations.map { |v| violation_key(v) }.to_set
        missing_accepted = ACCEPTED_VIOLATIONS.reject { |k| occurred.include?(k) }

        values = {
          "held_out_recall" => {
            value: pct(held_out_redact.count(&:passed), held_out_redact.size),
            detail: "#{held_out_redact.count(&:passed)}/#{held_out_redact.size} " \
                    "held-out REDACT spans",
          },
          "keep_precision" => {
            value: pct(keeps.count(&:passed), keeps.size),
            detail: "#{keeps.count(&:passed)}/#{keeps.size} KEEP spans intact",
          },
          "round_trip" => {
            value: pct(round_tripped, spec.frames.size),
            detail: "#{round_tripped}/#{spec.frames.size} frames restore exactly",
          },
          "unaccounted_violations" => {
            value: unaccounted.size,
            detail: if unaccounted.empty?
                      "#{violations.size} violation(s), all accounted for"
                    else
                      unaccounted.map { |v| "#{v.kind}:#{v.detail}" }.join("; ")
                    end,
          },
          "asset_entries" => {
            value: asset_entries,
            detail: asset_entries.nil? ? "not supplied by the caller" : "#{asset_entries} entries",
          },
        }

        # Kept in a SEPARATE hash from `values` on purpose. A gate declaring
        # `requires` may be measured only from data that actually satisfies that
        # requirement — never from anything derived from the fixture, because
        # computing something else and calling it that gate is the more dangerous
        # failure. Two hashes make that structural rather than a rule to remember.
        no_corpus = "no corpus supplied by the caller"
        supplied = {
          "bare_surname_exposure" => {
            value: bare_surname_exposure,
            detail: if bare_surname_exposure.nil?
                      "no census file supplied by the caller"
                    else
                      "#{round3(bare_surname_exposure)}% of US surname bearers"
                    end,
          },
          "held_out_recall_carrier" => {
            value: held_out_recall_carrier,
            detail: if held_out_recall_carrier.nil?
                      no_corpus
                    else
                      "#{round3(held_out_recall_carrier)}% of held-out REDACT spans in carrier essays"
                    end,
          },
          "over_fire_prose" => {
            value: over_fire_per_essay,
            detail: if over_fire_per_essay.nil?
                      no_corpus
                    else
                      "#{round3(over_fire_per_essay)} spans masked per essay of un-injected prose"
                    end,
          },
          "latency_p95" => {
            value: latency_p95_ms,
            detail: if latency_p95_ms.nil?
                      no_corpus
                    else
                      "#{round3(latency_p95_ms)} ms at essay length, one-time asset load excluded"
                    end,
          },
        }

        measurements = gate_spec.gates.map do |gate|
          unless gate.requires.empty?
            given = supplied[gate.id]
            if given.nil? || given[:value].nil?
              next GateMeasurement.new(gate: gate, value: nil, passed: nil, detail: "")
            end

            next GateMeasurement.new(gate: gate, value: given[:value],
                                     passed: compare(given[:value], gate.op, gate.bar),
                                     detail: given[:detail])
          end

          found = values[gate.id]
          if found.nil? || found[:value].nil?
            next GateMeasurement.new(gate: gate, value: nil, passed: nil,
                                     detail: found ? found[:detail] : "")
          end

          GateMeasurement.new(gate: gate, value: found[:value],
                              passed: compare(found[:value], gate.op, gate.bar),
                              detail: found[:detail])
        end

        GateReport.new(measurements: measurements, violations: violations,
                       unaccounted: unaccounted, missing_accepted: missing_accepted)
      end

      # Render the gate block, NOT MEASURED spelled out per gate.
      #
      # Replaces the placeholder block `Conformance.report` prints when no caller
      # measured anything.
      def report(gate_report)
        lines = ["  gates:"]
        gate_report.measurements.each do |m|
          gate = m.gate
          # `FROM` rather than `NEEDS` once it holds a value, so the line never
          # reads as though a measured gate were still waiting on its data — and
          # so the provenance of an operator-supplied number stays attached to it.
          needs = if gate.requires.empty?
                    ""
                  else
                    "  #{m.passed.nil? ? 'NEEDS' : 'FROM'} #{gate.requires.join('+')}"
                  end
          status = if m.passed.nil?
                     "NOT MEASURED"
                   else
                     m.passed ? "PASS        " : "FAIL        "
                   end
          measured = m.value.nil? ? "" : "   measured #{round3(m.value)} #{gate.unit}"
          lines << format("    %s  %-28s %s %s %s%s%s", status, gate.label, gate.op,
                          gate.bar, gate.unit, needs, measured)
          lines << format("                  %s", m.detail) if m.passed == false && !m.detail.empty?
        end
        measured = gate_report.measurements.reject { |m| m.passed.nil? }
        held = measured.count(&:passed)
        lines << "  -> #{held} of #{measured.size} measured gates hold; " \
                 "#{gate_report.measurements.size - measured.size} are NOT MEASURED and " \
                 "need operator-supplied data."
        lines.join("\n")
      end

      private

      def keep?(span)
        span.verdict == "keep"
      end

      def occurrences(haystack, needle)
        return 0 if needle.empty?

        # `scan` with a String pattern matches it literally and does not overlap,
        # which is what the TypeScript loop's `at + needle.length` step does.
        haystack.scan(needle).size
      end

      def pct(passed, total)
        return nil if total.zero?

        (100.0 * passed) / total
      end

      def compare(value, op, bar)
        case op
        when ">=" then value >= bar
        when "<=" then value <= bar
        when "==" then value == bar
        else raise Conformance::SpecError, "unknown gate operator #{op}"
        end
      end

      def round3(value)
        value == value.to_i ? value.to_i.to_s : format("%.3f", value)
      end
    end
  end
end
