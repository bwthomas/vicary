# frozen_string_literal: true

module Vicary
  # One replacement, located in both the original and the masked text.
  #
  # `orig_start...orig_end` is what was removed; `new_start...new_end` is the
  # placeholder standing in its place. The two widths differ — that difference
  # is the whole reason this type exists.
  RedactionSpan = Struct.new(:orig_start, :orig_end, :new_start, :new_end) do
    # How much this replacement moved everything after it.
    def delta
      (new_end - new_start) - (orig_end - orig_start)
    end

    def to_h
      { orig_start: orig_start, orig_end: orig_end,
        new_start: new_start, new_end: new_end }
    end
  end

  # Offset translation between the masked text and the composition the student
  # holds — the Ruby port of `python/src/vicary/redaction.py`'s span layer.
  #
  # Why a host needs it. `redact_with_report` hands back masked bytes and a
  # restore map, and between them they still say nothing about WHERE anything
  # sits. A host rendering a highlight works in the student's coordinates, the
  # detector produced its text in masked coordinates, and `{NAME_1}` is not the
  # width of the name it replaced: every offset after the first replacement is
  # displaced by the cumulative delta. Measured on a 56-paper corpus, redaction
  # fired on 43 essays and not one recorded offset pair on those 43 landed on
  # the original text.
  #
  # **Nothing in the masker records anything for this to work.** That is the
  # point, and it is why this is a pure module rather than instrumentation:
  # {Vicary::Candidates.mask_candidates} is many passes over a string each pass
  # mutates, so a span recorded inside one pass is in that pass's intermediate
  # coordinates and would have to be composed forward through every later one.
  # It is unnecessary — the finished text still *contains* every placeholder, so
  # each replacement's new span is where its placeholder sits, and the restore
  # map gives the original, so its width is a `length`.
  #
  # Checked against `conformance/spans.json`, generated from the Python
  # reference, by `test/spans_test.rb`. The arithmetic is shared with the other
  # two ports and pinned there rather than described here.
  module Spans
    # A placeholder token as it appears in masked text, e.g. `{NAME_1}`.
    PLACEHOLDER = /\{[A-Z_]+(?:_\d+)?\}/.freeze

    class << self
      # The span map, derived from the masked text and the restore map alone.
      #
      # Replacement preserves order, so one left-to-right walk accumulating the
      # running delta recovers the original offsets exactly.
      #
      # Returns `[]` when `restore_map` is empty or does not cover a placeholder
      # present in the text. A partial map is refused rather than answered for
      # the half it can place, because a translation that is right for some
      # offsets and silently wrong for others is worse than one that declines:
      # the caller can handle "no map" and cannot detect "wrong offset".
      def derive(masked, restore_map)
        return [] if restore_map.nil? || restore_map.empty?

        spans = []
        delta = 0
        masked.to_s.enum_for(:scan, PLACEHOLDER).each do
          match = Regexp.last_match
          original = restore_map[match[0]]
          return [] if original.nil?

          orig_start = match.begin(0) - delta
          span = RedactionSpan.new(orig_start, orig_start + original.length,
                                   match.begin(0), match.end(0))
          spans << span
          delta += span.delta
        end
        spans
      end

      # A redacted-text offset in original coordinates.
      #
      # An offset landing INSIDE a placeholder maps to the start of the span it
      # replaced: the placeholder's interior has no counterpart in the original,
      # so any position within it is the same position in original terms.
      def to_original(offset, spans)
        result = offset
        spans.each do |span|
          break if offset < span.new_start
          return span.orig_start if offset < span.new_end

          result = span.orig_end + (offset - span.new_end)
        end
        result
      end

      # An original-text offset in redacted coordinates.
      #
      # An offset inside a replaced span maps to the start of its placeholder,
      # for the mirror-image reason.
      def to_redacted(offset, spans)
        result = offset
        spans.each do |span|
          break if offset < span.orig_start
          return span.new_start if offset < span.orig_end

          result = span.new_end + (offset - span.orig_end)
        end
        result
      end

      # Reconstruct the pre-redaction text from the masked bytes and the map.
      #
      # Exact where a span map exists, and the masked text unchanged where one
      # does not — which is the honest answer rather than a partial restoration,
      # for the reason {derive} declines.
      def original(masked, restore_map)
        masked = masked.to_s
        out = +""
        prev = 0
        derive(masked, restore_map).each do |span|
          out << masked[prev...span.new_start]
          out << restore_map[masked[span.new_start...span.new_end]]
          prev = span.new_end
        end
        out << masked[prev..].to_s
        out
      end
    end
  end
end
