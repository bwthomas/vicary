# Measure this checkout's redaction latency once, and print it as JSON.
#
# One process, one number, no verdict. The verdict is `tools/latency_pair.py`'s
# job, because a latency number only means something next to another one taken
# on the same machine — see that file's header for what these measurements are
# for.
#
# The library it measures is whatever is on the load path, so pointing `-I` at
# another checkout's `lib` measures that one with this script. That is how the
# pair driver measures the previous release: same script, same corpus, same
# estimator, different library.
#
#     ruby -Ilib scripts/latency_measure.rb
#     ruby -I/tmp/prev/ruby/lib scripts/latency_measure.rb
require "digest"
require "json"
require "vicary"
require "vicary/conformance"
require "vicary/corpus"

spec = Vicary::Conformance.load_spec
corpus_id = Vicary::Corpus.resolve_corpus_id
essays = Vicary::Corpus.load_essays(corpus_id)
cases = essays.nil? ? [] : Vicary::Corpus.build_cases(essays, Vicary::Corpus.load_carrier_plan(corpus_id), spec)
if cases.empty?
  puts JSON.generate({ error: "no corpus in this checkout" })
  exit 1
end
identity = spec.identity

# One pass over every essay, timed or not.
#
# The untimed pass is the warmup, and it is not a formality. It is measured: on a
# GitHub runner TypeScript's first four essays run at about twice their
# steady-state cost while V8 tiers the redaction path up, which made the
# estimator's value depend on when the JIT happened to finish. This port barely
# moves, which is the other half of the reason the warmup is here — the three
# ports have to estimate the same way or the gate is three different gates.
sweep = lambda do |timed|
  out = []
  cases.each do |kase|
    timings = Array.new(Vicary::Corpus::LATENCY_REPEATS) do
      started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
      Vicary.redact(kase.text, identity)
      (Process.clock_gettime(Process::CLOCK_MONOTONIC) - started) * 1000.0
    end
    out << timings if timed
    # The clean-prose pass the gate's own loop does between essays. Untimed
    # there and untimed here, but it runs, so the process is in the same state
    # from one timed essay to the next.
    Vicary.redact(kase.base, identity)
  end
  out
end

# The asset load, before the clock: a one-time ~207 ms cost in this port that
# whichever essay came first would otherwise pay in full.
Vicary.redact(cases.first.base[0, 200], identity)

sweep.call(false)
pooled = sweep.call(true).flatten.sort
median = pooled.size.odd? ? pooled[(pooled.size - 1) / 2] : (pooled[pooled.size / 2 - 1] + pooled[pooled.size / 2]) / 2.0

puts JSON.generate({
  impl: "ruby",
  runtime: RUBY_VERSION.split(".").first(2).join("."),
  corpus: corpus_id,
  corpus_sha256: Digest::SHA256.hexdigest(cases.map(&:text).join),
  essays: cases.size,
  repeats: Vicary::Corpus::LATENCY_REPEATS,
  pooled_median_ms: median.round(6)
})
