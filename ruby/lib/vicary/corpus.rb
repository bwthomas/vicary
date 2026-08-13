# frozen_string_literal: true

require "csv"
require "digest"
require "json"
require "pathname"

module Vicary
  # The three gates that need an essay corpus, measured by this port.
  #
  # Held-out recall in a carrier essay, over-firing on real prose, and latency at
  # essay length cannot be measured on isolated sentences. They need fixture
  # frames planted inside genuine student prose. That prose ships: `persuade-20`
  # lives in `conformance/corpora/` and is the registry default, so all three are
  # measured on a bare checkout. They fall back to NOT MEASURED only when the
  # corpus that *resolves* is operator-supplied — ASAP-AES, selected either by
  # `VICARY_EVAL_CORPUS` or by having `VICARY_EVAL_CORPUS_TSV` configured — and no
  # TSV is there to read.
  #
  # **Where the carrier text comes from.** Everything about building it is
  # deterministic except which sentence ends the frames land on, which the Python
  # reference draws from its Mersenne Twister. Rather than reimplement MT19937
  # and `random.sample` here — several hundred lines with nothing to do with
  # redaction, whose failure mode is silent — the draw is recorded once in
  # `conformance/carrier.json` and read back. The plan is an *input*, exactly as
  # `frames.json` is: it says where to inject. What this port then measures from
  # the resulting text is recovered from its own output, never read from the spec.
  #
  # **Why the digest check is not paranoia.** An offset into the wrong essay is
  # not an error anything downstream notices; it produces a plausible number from
  # text nobody intended. So each essay is checked against the digest the plan
  # was built from, and a mismatch raises rather than measuring.
  module Corpus
    EVAL_CORPUS_TSV_ENV_VAR = "VICARY_EVAL_CORPUS_TSV"
    EVAL_CORPUS_DIR_ENV_VAR = "VICARY_EVAL_CORPUS_DIR"
    EVAL_CORPUS_PREFERRED_FILENAME = "corpus.tsv"

    # How many times each essay is redacted for the latency figure. The recorded
    # number is the MEDIAN of these, not one sample. Must stay odd, so the median
    # is a sample rather than a mean of two.
    #
    # Why: the latency gate takes p95 across essays, and at n=20 that index *is*
    # the maximum. So a single-sample-per-essay design asked "did a GC pause land
    # in any one of twenty calls" and answered a `<=` gate with it. Five
    # consecutive runs of unchanged code in this port gave 13.8, 7.4, 13.1, 7.7,
    # 6.8 ms against a 10 ms bar — two failures out of five, bimodal at 2x rather
    # than noisy, which is the signature of a pause landing on the one sample that
    # decides the answer. A median of three per essay means a pause has to hit the
    # same essay twice to move the number.
    #
    # Five rather than three because the gated number is now a regression bar
    # with 8% of room, and the estimator has to reproduce itself to well inside
    # that on unchanged code. Every repeat re-redacts the whole corpus, so this
    # is not free; five is where the measured gain flattened. The same constant
    # lives in all three ports, because a gate two ports estimate differently is
    # not the same gate.
    LATENCY_REPEATS = 5

    CARRIER_FILENAME = "carrier.json"

    # Bumped when a field's meaning changes. An unknown version is refused.
    # 2 keyed the plans by corpus id. A version-1 reader handed a version-2 file
    # finds no `cases` at the top level and builds zero carrier essays — which in a
    # `<=` gate is the most comfortable pass on the board, so the refusal is the
    # point of the number.
    CARRIER_DOCUMENT_VERSION = 2

    # Where the corpus profiles live, under `conformance/`.
    CORPORA_DIRNAME = "corpora"
    CORPORA_INDEX_FILENAME = "index.json"
    CORPUS_PROFILE_FILENAME = "profile.json"

    # Source kinds a corpus profile may declare, and the file the shipped kind
    # keeps beside its profile.
    KIND_SHIPPED = "shipped"
    KIND_OPERATOR_TSV = "operator_tsv"
    ESSAYS_FILENAME = "essays.json"
    PROFILE_DOCUMENT_VERSION = 1

    # Names a corpus id directly, overriding the operator-TSV inference.
    EVAL_CORPUS_ENV_VAR = "VICARY_EVAL_CORPUS"

    # The reference's ANSWERS on the plan the carrier file describes. Separate
    # file because they are a different kind of thing: `carrier.json` is an input
    # every port replays, `measured.json` is what Python got from replaying it.
    MEASURED_FILENAME = "measured.json"
    # 2 keyed the measurements by corpus id. Two of the three numbers are
    # properties of the prose rather than of the detector, so an unkeyed block
    # invited comparing one corpus's figures against another's.
    MEASURED_DOCUMENT_VERSION = 2

    # One of ASAP's own anonymization tokens — `@PERSON1`, `@LOCATION2`.
    #
    # Load-bearing for the over-fire metric, because the two legs it separates
    # are unrelated. Masking genuine prose is a precision defect; masking
    # `@PERSON1` is not, since the PII is already gone. Summed they read as one
    # catastrophic precision failure while the prose leg is zero.
    #
    # `\A`/`\z`, not `^`/`$`: Ruby anchors those at every line boundary, so a
    # region spanning a newline would match on its last line alone.
    ASAP_TOKEN_RE = /\A@[A-Z]+\d*\z/

    Case = Struct.new(:essay_id, :text, :base, :frames, keyword_init: true)

    Metrics = Struct.new(
      :essays, :recall_held_out, :recall_held_out_passed, :recall_held_out_total,
      :over_fire_spans_per_essay, :over_fire_spans_total,
      :asap_rewrites_per_essay, :latency_p50_ms, :latency_p95_ms,
      :latency_pooled_median_ms,
      keyword_init: true
    )

    class << self
      # Mean of the two middle samples at even length, matching how the other
      # two ports define it. Pooled n is 20 x 5, so the even branch is the one
      # that runs.
      def median_of(xs)
        return 0.0 if xs.empty?

        s = xs.sort
        mid = s.size / 2
        s.size.odd? ? s[mid] : (s[mid - 1] + s[mid]) / 2.0
      end

      def asap_token?(region)
        ASAP_TOKEN_RE.match?(region.strip)
      end

      # Configured path to the corpus TSV, or `""`.
      def corpus_source
        explicit = (ENV[EVAL_CORPUS_TSV_ENV_VAR] || "").strip
        return explicit unless explicit.empty?

        directory = (ENV[EVAL_CORPUS_DIR_ENV_VAR] || "").strip
        return "" if directory.empty?

        File.join(directory, EVAL_CORPUS_PREFERRED_FILENAME)
      end

      # `[[essay_id, text], ...]` for the first `limit` essays of the named set,
      # in file order.
      #
      # **Read as latin-1, then converted to UTF-8.** ASAP-AES is not UTF-8, and
      # reading it as UTF-8 yields invalid byte sequences that break both the
      # digests and every offset computed against them. The conversion afterwards
      # matters too: the frame sentences come from JSON as UTF-8, and Ruby raises
      # `Encoding::CompatibilityError` on concatenating the two encodings once
      # either side holds a non-ASCII byte.
      #
      # Parsed here rather than by `CSV`, for two reasons that both bite on this
      # file. ASAP essays contain `"` characters and some records span more than
      # one physical line inside a quoted field — 12,980 lines for 12,976
      # records — so splitting on tabs and newlines silently truncates essays
      # mid-sentence. And the line endings are mixed, 12,979 LF against 12,977
      # CR, which Ruby's `CSV` refuses outright ("New line must be <\"\\n\">")
      # while Python's `csv` accepts. The state machine below takes CRLF, LF and
      # a lone CR all as record separators, which is what the reference does.
      def load_set(tsv, essay_set, limit)
        text = File.read(tsv, encoding: "ISO-8859-1").encode("UTF-8")
        parse_delimited(text, "\t", essay_set, limit)
      end

      # `[essay_id, text]` for a corpus whose essays ship in this repository.
      #
      # **The essays ARE the baseline**, so every byte is checked against the
      # digest the profile pins. A corrupted or edited file has to fail here
      # rather than quietly rebase what every corpus gate means — the numbers
      # describe this exact prose and nothing warns you when the prose changes
      # underneath them. The carrier plan checks the same bytes again from its own
      # digests, which is deliberate: two independent records of what this corpus
      # is, and either catches an edit to the other.
      def load_shipped(corpus_id, dir = nil)
        profile = load_corpus_profile(corpus_id, dir)
        text_file = profile.dig("source", "text_file") || ESSAYS_FILENAME
        path = Pathname.new(dir || Conformance.directory)
                       .join(CORPORA_DIRNAME, corpus_id, text_file)
        document = read_versioned(path, "corpus")
        essays = document["essays"].map { |e| [e["id"], e["text"]] }
        pinned = (profile["essays"] || []).to_h { |e| [e["id"], e["sha256"]] }

        essays.each do |essay_id, text|
          want = pinned[essay_id]
          if want.nil?
            raise Conformance::SpecError,
                  "#{corpus_id}: #{text_file} carries essay #{essay_id}, which " \
                  "#{CORPUS_PROFILE_FILENAME} does not list"
          end
          got = Digest::SHA256.hexdigest(text)
          next if got == want

          raise Conformance::SpecError,
                "#{corpus_id}: essay #{essay_id} in #{text_file} is sha256 #{got}, and " \
                "#{CORPUS_PROFILE_FILENAME} pins #{want}. Refusing: the essays are the " \
                "baseline, so different text means every gate number measured on this " \
                "corpus describes different prose."
        end
        if pinned.size != essays.size
          raise Conformance::SpecError,
                "#{corpus_id}: #{CORPUS_PROFILE_FILENAME} lists #{pinned.size} essays " \
                "and #{text_file} holds #{essays.size}"
        end
        essays
      end

      # The resolved corpus's essays, whichever kind it is.
      #
      # `nil` only for an operator corpus with no TSV configured — the one case
      # where the data genuinely is not here. A shipped corpus always loads, which
      # is the whole point of shipping one.
      def load_essays(corpus_id = nil, dir = nil)
        id = corpus_id || resolve_corpus_id(dir)
        profile = load_corpus_profile(id, dir)
        kind = profile.dig("source", "kind")
        return load_shipped(id, dir) if kind == KIND_SHIPPED

        unless kind == KIND_OPERATOR_TSV
          raise Conformance::SpecError,
                "corpus #{id} declares source kind #{kind}; this reader knows " \
                "#{KIND_SHIPPED} and #{KIND_OPERATOR_TSV}"
        end
        tsv = corpus_source
        return nil if tsv.empty?

        load_set(tsv, profile.dig("source", "filter", "equals") || "",
                 profile.dig("selection", "limit"))
      end

      def carrier_path(dir = nil)
        Pathname.new(dir || Conformance.directory).join(CARRIER_FILENAME)
      end

      # The corpus registry: which corpora exist, and which applies by default.
      def load_corpus_index(dir = nil)
        read_versioned(
          Pathname.new(dir || Conformance.directory)
                  .join(CORPORA_DIRNAME, CORPORA_INDEX_FILENAME), "registry"
        )
      end

      # One corpus's profile: where its essays come from and which are in.
      def load_corpus_profile(corpus_id, dir = nil)
        read_versioned(
          Pathname.new(dir || Conformance.directory)
                  .join(CORPORA_DIRNAME, corpus_id, CORPUS_PROFILE_FILENAME), "profile"
        )
      end

      # Which corpus applies here, in the reference's order: an explicit
      # VICARY_EVAL_CORPUS wins, then an operator with a configured TSV keeps
      # measuring the corpus they always measured, then the registry default.
      def resolve_corpus_id(dir = nil)
        index = load_corpus_index(dir)
        known = index["corpora"] || []
        explicit = (ENV[EVAL_CORPUS_ENV_VAR] || "").strip
        unless explicit.empty?
          unless known.include?(explicit)
            raise Conformance::SpecError,
                  "#{EVAL_CORPUS_ENV_VAR}=#{explicit} is not a registered corpus; this " \
                  "checkout registers #{known.join(', ')}"
          end
          return explicit
        end
        return index["operator_default"] if !corpus_source.empty? && index["operator_default"]

        index["default"]
      end

      def load_carrier_plan(corpus_id = nil, dir = nil)
        raw = JSON.parse(carrier_path(dir).read)
        version = raw["document_version"]
        unless version == CARRIER_DOCUMENT_VERSION
          raise Conformance::SpecError,
                "#{CARRIER_FILENAME} is document_version #{version.inspect}, and this " \
                "reader knows #{CARRIER_DOCUMENT_VERSION}. Refusing rather than reading " \
                "the fields it recognises, because a partly-read plan produces carrier " \
                "text that is wrong without being detectably wrong."
        end
        id = corpus_id || resolve_corpus_id(dir)
        plans = raw["plans"] || {}
        plan = plans[id]
        if plan.nil?
          raise Conformance::SpecError,
                "#{CARRIER_FILENAME} holds no plan for corpus #{id}; it has " \
                "#{plans.keys.sort.join(', ')}. Regenerate with " \
                "`python -m vicary.eval.carrier --write` on a machine that can read " \
                "that corpus."
        end
        # The row filter and essay count are properties of the corpus, so they are
        # read off its profile rather than restated here — two records of one fact
        # is how they drift.
        profile = load_corpus_profile(id, dir)
        plan.merge(
          "corpus_id" => id,
          "essay_set" => profile.dig("source", "filter", "equals"),
          "limit" => profile.dig("selection", "limit")
        )
      end

      def measured_path(dir = nil)
        Pathname.new(dir || Conformance.directory).join(MEASURED_FILENAME)
      end

      # The counts the Python reference gets on the carrier text the plan builds.
      #
      # Read rather than transcribed. These were literals in this port's gate
      # test — `assert_equal 29, m.recall_held_out_passed` — and in TypeScript's,
      # and in Python's. Three copies of a number is not three checks of it: when
      # the reference's figure legitimately moves, Python's suite is updated
      # because that is where the change was made, and the other two keep
      # asserting the stale value and stay green while measuring something else.
      #
      # Returns the raw document. The envelope matters as much as the numbers, so
      # nothing here flattens it away — see `Gates.check_measured_envelope`.
      def load_measured(corpus_id = nil, dir = nil)
        raw = JSON.parse(measured_path(dir).read)
        version = raw["document_version"]
        unless version == MEASURED_DOCUMENT_VERSION
          raise Conformance::SpecError,
                "#{MEASURED_FILENAME} is document_version #{version.inspect}, and this " \
                "reader knows #{MEASURED_DOCUMENT_VERSION}. Refusing rather than reading " \
                "the fields it recognises: a partly-read document compares this port " \
                "against numbers whose meaning it is guessing at."
        end
        id = corpus_id || resolve_corpus_id(dir)
        corpora = raw["corpora"] || {}
        entry = corpora[id]
        if entry.nil?
          raise Conformance::SpecError,
                "#{MEASURED_FILENAME} holds no measurements for corpus #{id}; it has " \
                "#{corpora.keys.sort.join(', ')}. Regenerate with `just sync-conformance` " \
                "on a machine that can read that corpus."
        end
        entry
      end

      def read_versioned(path, what)
        raw = JSON.parse(path.read)
        version = raw["document_version"]
        unless version == PROFILE_DOCUMENT_VERSION
          raise Conformance::SpecError,
                "#{path.basename} is document_version #{version.inspect} and this reader " \
                "knows #{PROFILE_DOCUMENT_VERSION}. Refusing to read the fields it " \
                "recognises: a partly-read #{what} selects a different slice of prose " \
                "without being detectably wrong."
        end
        raw
      end

      # Rebuild the carrier essays from the plan.
      #
      # Slots are applied in the order recorded — descending — so an earlier
      # insertion cannot shift a later one.
      def build_cases(essays, plan, spec)
        by_id = spec.frames.each_with_object({}) { |f, h| h[f.frame_id] = f }
        planned = plan["cases"].each_with_object({}) { |c, h| h[c["essay_id"]] = c }

        cases = build_each(essays, planned, by_id)

        # Every planned essay, or none of them. A corpus that matches the plan
        # only partly would measure a *subset* and report it under the same gate
        # — and the degenerate case of matching nothing is worse than wrong,
        # because over-firing and latency both then compute as 0.0, which in a
        # `<=` gate is the most comfortable pass on the board. Refusing is the
        # only outcome that cannot be mistaken for a green run.
        if cases.size != plan["cases"].size
          found = cases.map(&:essay_id).to_set
          missing = plan["cases"].map { |e| e["essay_id"] }.reject { |id| found.include?(id) }
          raise Conformance::SpecError,
                "the carrier plan names #{plan['cases'].size} essays and this corpus " \
                "supplied #{cases.size} of them; missing #{missing.first(5).join(', ')}" \
                "#{missing.size > 5 ? ' …' : ''}. Refusing to measure a subset, because " \
                "over-firing and latency on an empty or partial set compute as 0.0 and " \
                "read as a pass."
        end

        reconcile_against_corpus(essays, plan, cases)
        cases
      end

      # Every *corpus* essay is either carried or named unusable.
      #
      # The check above only proves the plan got what it asked for; it cannot see
      # an essay the plan never asked about. That was safe while a plan always
      # covered its whole corpus, and stopped being safe when `unusable` made a
      # short plan legitimate — without this, a plan that quietly lost ten essays
      # would measure the fifteen it kept and report them under the same gate.
      def reconcile_against_corpus(essays, plan, cases)
        unusable = (plan["unusable"] || []).map { |e| e["essay_id"] }
        accounted = cases.map(&:essay_id).to_set | unusable.to_set
        unaccounted = essays.map(&:first).reject { |id| accounted.include?(id) }
        return if unaccounted.empty?

        raise Conformance::SpecError,
              "the corpus supplies #{essays.size} essays and the carrier plan accounts " \
              "for #{accounted.size} of them — #{plan['cases'].size} carried and " \
              "#{unusable.size} declared unusable. Unaccounted: " \
              "#{unaccounted.first(5).join(', ')}#{unaccounted.size > 5 ? ' …' : ''}. " \
              "An essay the plan neither carries nor names is one it dropped silently, " \
              "which is the same comfortable pass as a partial match."
      end

      def build_each(essays, planned, by_id)
        essays.filter_map do |essay_id, base|
          entry = planned[essay_id]
          next if entry.nil?

          digest = Digest::SHA256.hexdigest(base)
          if digest != entry["base_sha256"]
            raise Conformance::SpecError,
                  "essay #{essay_id} in this corpus does not match the one the carrier " \
                  "plan was built from (sha256 #{digest[0, 12]} vs " \
                  "#{entry['base_sha256'][0, 12]}). The recorded offsets point into " \
                  "different text, so every number downstream would be wrong without " \
                  "being detectably wrong."
          end

          picks = entry["frames"].map do |fid|
            by_id.fetch(fid) do
              raise Conformance::SpecError,
                    "carrier plan names frame #{fid}, absent from the spec"
            end
          end

          text = base.dup
          picks.each_with_index do |frame, i|
            at = entry["slots"][i]
            text = text[0, at] + " " + frame.sentence + text[at..]
          end
          Case.new(essay_id: essay_id, text: text, base: base, frames: picks)
        end
      end

      # Measure the three corpus gates.
      #
      # Each essay is redacted twice — once with the frames injected, to score
      # recall, and once bare, to see what the redactor does to prose with
      # nothing planted in it. The bare pass is where over-firing comes from, and
      # it is why the metric means anything: the frames cannot contaminate it.
      def measure(cases, identity)
        outcomes = []
        latencies = []
        # Every essay's every sample. The gated figure is the median of THESE,
        # not a percentile over the per-essay collapses in `latencies`.
        pooled = []
        over_fire = 0
        rewrites = 0

        # Warm up before the clock starts, over the WHOLE corpus rather than one
        # 200-char call. Two costs are being excluded, and the second one is why
        # this grew.
        #
        # The gazetteer load is a one-time ~207 ms cost in this port, and
        # whichever essay happens to be first pays all of it — 14.3 ms cold
        # against 7.6 ms warm.
        #
        # The second belongs to TypeScript, where V8 tiers the redaction path up
        # over roughly the first four essays and runs them at about twice their
        # steady-state cost. This port barely moves under a full warmup and does
        # it anyway: the three ports measure identically or the gate is three
        # different gates.
        cases.each do |kase|
          yield(kase.text, identity)
          yield(kase.base, identity)
        end

        cases.each do |kase|
          # The median of LATENCY_REPEATS, not one sample — see that constant.
          masked = nil
          timings = Array.new(LATENCY_REPEATS) do
            started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
            masked = yield(kase.text, identity)
            (Process.clock_gettime(Process::CLOCK_MONOTONIC) - started) * 1000.0
          end
          latencies << timings.sort[(LATENCY_REPEATS - 1) / 2]
          pooled.concat(timings)

          kase.frames.each { |frame| outcomes.concat(Gates.score_spans(frame, masked)) }

          masked_base = yield(kase.base, identity)
          pairs = Gates.align(kase.base, masked_base).pairs
          prose = pairs.reject { |(_, region)| asap_token?(region) }
          over_fire += prose.size
          rewrites += pairs.size - prose.size
        end

        held_out = outcomes.select { |o| o.held_out && o.verdict != "keep" }
        passed = held_out.count(&:passed)
        sorted = latencies.sort
        at = lambda do |q|
          next 0.0 if sorted.empty?

          sorted[[(sorted.size * q).floor, sorted.size - 1].min]
        end

        Metrics.new(
          essays: cases.size,
          recall_held_out: held_out.empty? ? 0.0 : 100.0 * passed / held_out.size,
          recall_held_out_passed: passed,
          recall_held_out_total: held_out.size,
          over_fire_spans_per_essay: cases.empty? ? 0.0 : over_fire.to_f / cases.size,
          over_fire_spans_total: over_fire,
          asap_rewrites_per_essay: cases.empty? ? 0.0 : rewrites.to_f / cases.size,
          latency_p50_ms: at.call(0.5),
          latency_p95_ms: at.call(0.95),
          latency_pooled_median_ms: median_of(pooled)
        )
      end

      # Load the corpus, rebuild the carriers, and measure. `nil` with no corpus.
      def measure_from_config(spec, &redact)
        corpus_id = resolve_corpus_id
        essays = load_essays(corpus_id)
        return nil if essays.nil? || essays.empty?

        plan = load_carrier_plan(corpus_id)
        measure(build_cases(essays, plan, spec), spec.identity, &redact)
      end

      private

      # RFC4180 as Python's `csv` implements it: a quote opens a field only at
      # its start, `""` inside one is a literal quote, and anything after the
      # closing quote is taken literally. Stops as soon as `limit` matching rows
      # are found, so this walks only as far into a 16 MB file as it has to.
      def parse_delimited(text, delimiter, essay_set, limit)
        out = []
        header = nil
        set_at = id_at = essay_at = nil
        row = []
        field = +""
        in_quotes = false
        pending_quote = false
        after_cr = false

        finish_row = lambda do
          row << field
          field = +""
          finished = row
          row = []
          if header.nil?
            header = finished
            set_at = header.index("essay_set")
            id_at = header.index("essay_id")
            essay_at = header.index("essay")
            if set_at.nil? || id_at.nil? || essay_at.nil?
              raise Conformance::SpecError,
                    "corpus has no essay_set/essay_id/essay header; got #{header.join(',')}"
            end
            next false
          end
          next false if finished.size == 1 && finished[0].empty?
          next false unless finished[set_at] == essay_set

          out << [finished[id_at].to_s, finished[essay_at].to_s]
          out.size >= limit
        end

        text.each_char do |ch|
          # CRLF is one record separator, not two. The CR ended the record; this
          # swallows the LF that follows it rather than opening an empty one.
          if after_cr
            after_cr = false
            next if ch == "\n" && !in_quotes && !pending_quote
          end

          if pending_quote
            pending_quote = false
            if ch == '"'
              field << '"'
              next
            end
            in_quotes = false
            # fall through and handle `ch` as an unquoted character
          end

          if in_quotes
            if ch == '"'
              pending_quote = true
            else
              field << ch
            end
            next
          end

          case ch
          when '"'
            if field.empty?
              in_quotes = true
            else
              field << ch
            end
          when delimiter
            row << field
            field = +""
          when "\n"
            return out if finish_row.call
          when "\r"
            after_cr = true
            return out if finish_row.call
          else
            field << ch
          end
        end

        finish_row.call unless field.empty? && row.empty?
        out
      end
    end
  end
end
