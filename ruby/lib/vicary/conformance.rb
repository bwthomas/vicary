# frozen_string_literal: true

require "json"
require "pathname"

module Vicary
  # Read the shared spec and score this implementation against it.
  #
  # The spec lives in the repository's `conformance/` directory, is generated from
  # the Python implementation, and is what all three front doors run against. See
  # `conformance/README.md` for the bar; the short version is that every frame's
  # masked output must be byte-identical **including placeholder numbering**.
  #
  # **Why the scoreboard reports two denominators.** 16 of the 51 frames expect
  # nothing to be masked — they exist to catch over-redaction. An implementation
  # that returns its input unchanged therefore scores 16 of 51 and looks a third
  # of the way done while detecting nothing. So the number that leads is
  # `matched of frames_requiring_masking`, with the 51-frame total beside it
  # rather than instead of it. A ratio whose numerator a null implementation can
  # inflate is not a measure of progress.
  module Conformance
    DOCUMENT_VERSION = 1

    class SpecError < StandardError; end

    Span = Struct.new(:entity, :literal, :verdict, :expect_count, :expect,
                      :kept_by, :redacted_by, :note, keyword_init: true)
    Frame = Struct.new(:frame_id, :group, :sentence, :spans, :held_out,
                       :prompt_context, :note, keyword_init: true)
    Identity = Struct.new(:first_name, :last_name, :school_name,
                          keyword_init: true)
    Golden = Struct.new(:masked, :placeholders, :mapping, :aligns,
                        keyword_init: true)
    Spec = Struct.new(:fixture_version, :reference_arm, :identity, :frames,
                      :golden, keyword_init: true)
    Gate = Struct.new(:id, :label, :unit, :op, :bar, :requires, :why,
                      keyword_init: true)
    GateSpec = Struct.new(:reference_arm, :requirements, :gates,
                          keyword_init: true)
    Outcome = Struct.new(:frame_id, :requires_masking, :matched, :expected,
                         :produced, :error, keyword_init: true)
    Scoreboard = Struct.new(:fixture_version, :reference_arm, :total, :matched,
                            :requiring_masking, :matched_requiring_masking,
                            :outcomes, keyword_init: true)

    class << self
      # Locate the repository's `conformance/` directory, or raise naming the search.
      def directory
        tried = []
        current = Pathname.new(__dir__).expand_path
        8.times do
          candidate = current.join("conformance")
          tried << candidate
          return candidate if candidate.join("frames.json").file?

          parent = current.parent
          break if parent == current

          current = parent
        end
        raise SpecError,
              "no conformance/frames.json found. Looked in: #{tried.join(', ')}. " \
              "The spec lives in the repository, not in an installed gem — a " \
              "packaged copy would imply the installed one is authoritative."
      end

      def load_spec(dir = nil)
        dir = Pathname.new(dir || directory)
        raw = JSON.parse(dir.join("frames.json").read)
        require_version(raw["document_version"], "frames.json")

        frames = raw.fetch("frames").map do |f|
          Frame.new(
            frame_id: f.fetch("frame_id"),
            group: f.fetch("group"),
            sentence: f.fetch("sentence"),
            held_out: f.fetch("held_out", false),
            prompt_context: f.fetch("prompt_context", ""),
            note: f.fetch("note", ""),
            spans: f.fetch("spans").map { |s| span_from(s) },
          )
        end

        golden = raw.fetch("golden").transform_values do |g|
          Golden.new(masked: g.fetch("masked"),
                     placeholders: g.fetch("placeholders"),
                     mapping: g.fetch("mapping"),
                     aligns: g.fetch("aligns"))
        end

        identity = raw.fetch("identity")
        Spec.new(
          fixture_version: raw.fetch("fixture_version"),
          reference_arm: raw.fetch("reference_arm"),
          identity: Identity.new(first_name: identity.fetch("first_name"),
                                 last_name: identity.fetch("last_name"),
                                 school_name: identity.fetch("school_name")),
          frames: frames,
          golden: golden,
        )
      end

      def load_gates(dir = nil)
        dir = Pathname.new(dir || directory)
        raw = JSON.parse(dir.join("gates.json").read)
        require_version(raw["document_version"], "gates.json")
        GateSpec.new(
          reference_arm: raw.fetch("reference_arm"),
          requirements: raw.fetch("requirements"),
          gates: raw.fetch("gates").map do |g|
            Gate.new(id: g.fetch("id"), label: g.fetch("label"),
                     unit: g.fetch("unit"), op: g.fetch("op"),
                     bar: g.fetch("bar"), requires: g.fetch("requires"),
                     why: g.fetch("why"))
          end,
        )
      end

      # The primitives spec — the layer underneath the frames.
      #
      # `frames.json` scores finished output, which is the right final bar and a
      # poor first one: a port with nothing implemented scores 0 of 36 and learns
      # nothing about which of the forty-odd primitives underneath is wrong.
      # `primitives.json` is that missing layer, generated from the Python
      # functions and byte-compared against a fresh export by
      # `python/tests/test_conformance.py`.
      #
      # Returned as the parsed document rather than as structs: it is a table of
      # forty-odd differently-shaped sections, and a struct per section would be
      # forty transcriptions of the thing the file exists to stop anyone
      # transcribing.
      def load_primitives(dir = nil)
        dir = Pathname.new(dir || directory)
        path = dir.join("primitives.json")
        unless path.file?
          raise SpecError,
                "no primitives.json at #{path}. The ports would check their " \
                "tokenisation against nothing."
        end

        raw = JSON.parse(path.read)
        require_version(raw["document_version"], "primitives.json")
        raw
      end

      # Score an implementation against every frame.
      #
      # The block receives (sentence, identity) — the same input every Python arm
      # receives. Omitting the identity measures a different system and misses the
      # easiest spans in the fixture.
      def score(spec)
        outcomes = spec.frames.map do |frame|
          golden = spec.golden[frame.frame_id]
          if golden.nil?
            raise SpecError,
                  "frame #{frame.frame_id} has no golden output in the spec; the " \
                  "file is internally inconsistent and scoring against it would " \
                  "be meaningless"
          end

          produced = nil
          error = nil
          begin
            produced = yield(frame.sentence, spec.identity)
          rescue StandardError => e
            produced = ""
            error = e.message
          end

          Outcome.new(frame_id: frame.frame_id,
                      requires_masking: !golden.placeholders.empty?,
                      matched: error.nil? && produced == golden.masked,
                      expected: golden.masked, produced: produced, error: error)
        end

        requiring = outcomes.select(&:requires_masking)
        Scoreboard.new(
          fixture_version: spec.fixture_version,
          reference_arm: spec.reference_arm,
          total: outcomes.size,
          matched: outcomes.count(&:matched),
          requiring_masking: requiring.size,
          matched_requiring_masking: requiring.count(&:matched),
          outcomes: outcomes,
        )
      end

      # Render the scoreboard.
      #
      # Leads with the masking-required ratio, the one a null implementation
      # cannot inflate. Gates print NOT MEASURED per gate rather than being
      # reduced out of the denominator — five of nine held is a different
      # statement from nine of nine, and a badge cannot tell them apart.
      def report(board, gates)
        lines = []
        lines << "conformance — fixture #{board.fixture_version}, arm #{board.reference_arm}"
        lines << ("-" * 58)
        lines << format("  frames requiring masking   %3d / %d",
                        board.matched_requiring_masking, board.requiring_masking)
        lines << format("  all frames                 %3d / %d   (%d expect no " \
                        "masking, so an identity function scores that many)",
                        board.matched, board.total,
                        board.total - board.requiring_masking)
        lines << ("-" * 58)
        lines << "  gates:"
        gates.gates.each do |gate|
          needs = gate.requires.empty? ? "" : "  NEEDS #{gate.requires.join('+')}"
          lines << format("    NOT MEASURED  %-28s %s %s %s%s",
                          gate.label, gate.op, gate.bar, gate.unit, needs)
        end
        lines << "  -> no gate is measured by this port yet. A green run here " \
                 "means the spec loads,"
        lines << "     never that the gate set is clear."
        lines.join("\n")
      end

      private

      def span_from(raw)
        # The defaults are documented in conformance/README.md. Applying them here
        # rather than requiring the exporter to write them keeps the file
        # readable; getting one wrong silently changes what a frame asserts.
        Span.new(entity: raw.fetch("entity"), literal: raw.fetch("literal"),
                 verdict: raw.fetch("verdict", "redact"),
                 expect_count: raw.fetch("expect_count", nil),
                 expect: raw.fetch("expect", nil),
                 kept_by: raw.fetch("kept_by", "notability"),
                 redacted_by: raw.fetch("redacted_by", "absence"),
                 note: raw.fetch("note", ""))
      end

      def require_version(version, file)
        return if version == DOCUMENT_VERSION

        raise SpecError,
              "#{file} is document_version #{version.inspect}, this reader " \
              "understands #{DOCUMENT_VERSION}. Refusing to read it rather than " \
              "guessing which fields moved."
      end
    end
  end
end
