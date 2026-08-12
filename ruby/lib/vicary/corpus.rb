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
  # frames planted inside genuine student prose — and the prose is a corpus no
  # package here ships, so all three stay NOT MEASURED until an operator points
  # `VICARY_EVAL_CORPUS_TSV` at one.
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
      keyword_init: true
    )

    class << self
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
        cases
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
        over_fire = 0
        rewrites = 0

        # Load the gazetteer before the clock starts. It is a one-time ~207 ms
        # cost in this port, and whichever essay happens to be first pays all of
        # it: at n=25 that single sample lands at or above p95 and sets the
        # gate's answer by itself — 14.3 ms cold against 7.6 ms warm, on a 10 ms
        # bar. The number the gate claims is essay-length redaction latency, not
        # process startup. Excluded in all three ports alike.
        yield(cases.first.base[0, 200], identity) unless cases.empty?

        cases.each do |kase|
          started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
          masked = yield(kase.text, identity)
          latencies << ((Process.clock_gettime(Process::CLOCK_MONOTONIC) - started) * 1000.0)

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
          latency_p95_ms: at.call(0.95)
        )
      end

      # Load the corpus, rebuild the carriers, and measure. `nil` with no corpus.
      def measure_from_config(spec, &redact)
        tsv = corpus_source
        return nil if tsv.empty?

        plan = load_carrier_plan
        essays = load_set(tsv, plan["essay_set"], plan["limit"])
        return nil if essays.empty?

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
