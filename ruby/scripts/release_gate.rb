# frozen_string_literal: true

# The gate that decides whether this gem may be published.
#
# A port that publishes while it reproduces 0 of 38 masking-required frames is a
# gem called `vicary` that does not redact — worse than no gem, because a host
# that installs it gets silence instead of an error. So the release workflow
# refuses to push unless every masking-required frame matches the reference
# output byte-for-byte.
#
# **Why this is a script and not four lines of shell in the workflow.** It was
# shell: `rake conformance | grep ... | awk '{print $4}'`, comparing two strings
# scraped out of a human-readable report. That gate is the only thing standing
# between an unfinished port and RubyGems, and it had three failure modes nobody
# could see — a relabelled report line, a changed column, and an `awk` that
# yields the empty string on both. The empty-string case was guarded; the other
# two silently compare `""` to `""`... no, worse: they compare whatever landed in
# column four. A gate whose own correctness is unobservable is not a gate.
#
# Here it reads the scoreboard object the harness already returns, so there is no
# text to misparse, and `test/release_test.rb` exercises the decision in both
# directions — refuse at 0 of 38, allow at 38 of 38 — without a network, a
# credential, or a tag.
#
# Usage:
#   ruby -Ilib scripts/release_gate.rb              # print the report; exit 1 unless complete
#   ruby -Ilib scripts/release_gate.rb --report-only # print the report; always exit 0

require "vicary"

module ReleaseGate
  # Why a complete board is required, said once so both the workflow log and the
  # test read the same sentence.
  REFUSAL = <<~TEXT.freeze
    REFUSING TO PUBLISH. Publishing now would ship a gem named vicary that does
    not redact, and a caller cannot tell: `Vicary.redact` would return text with
    names still in it, or raise NotPortedError inside somebody's request path.
    Raise the ratchet and land the detector first.
  TEXT

  Decision = Struct.new(:matched, :total, :publishable, :reason, keyword_init: true)

  module_function

  # Decide from a scoreboard. Pure, so the test can hand it a board that does not
  # exist yet in this port and check the *allow* branch too — the branch that has
  # never once run here, and the expensive one to get wrong.
  def decide(board)
    matched = board.matched_requiring_masking
    total = board.requiring_masking

    if total.nil? || total.zero?
      return Decision.new(
        matched: matched, total: total, publishable: false,
        reason: "the spec reports 0 frames requiring masking, so this gate is " \
                "scoring nothing. Refusing to publish on a denominator of zero " \
                "rather than reading it as success.",
      )
    end

    if matched == total
      Decision.new(matched: matched, total: total, publishable: true,
                   reason: "#{matched} of #{total} masking-required frames match " \
                           "the reference output byte-for-byte.")
    else
      Decision.new(matched: matched, total: total, publishable: false,
                   reason: "#{matched} of #{total} masking-required frames match " \
                           "the reference output.\n\n#{REFUSAL}")
    end
  end

  # Score this port against the shared spec.
  def board
    spec = Vicary::Conformance.load_spec
    Vicary::Conformance.score(spec) { |sentence, identity| Vicary.redact(sentence, identity) }
  end

  def main(argv, out: $stdout)
    report_only = argv.include?("--report-only")
    spec_board = board
    decision = decide(spec_board)

    spec = Vicary::Conformance.load_spec
    gates = Vicary::Conformance.load_gates
    gate_report = Vicary::Gates.measure(
      spec, gates, asset_entries: Vicary::Gazetteer.load.entry_count
    ) { |sentence, identity| Vicary.redact(sentence, identity) }

    out.puts Vicary::Conformance.report(spec_board, gates, Vicary::Gates.report(gate_report))
    out.puts
    out.puts "release gate: #{decision.publishable ? 'PUBLISHABLE' : 'BLOCKED'}"
    out.puts decision.reason

    return 0 if report_only || decision.publishable

    1
  end
end

exit ReleaseGate.main(ARGV) if $PROGRAM_NAME == __FILE__
