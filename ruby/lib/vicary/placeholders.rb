# frozen_string_literal: true

module Vicary
  # Text bound for a reader still carries a redaction placeholder.
  #
  # Names the kinds and the count, never the text around them: the message is
  # going to a log, and the text is exactly what was not supposed to leave.
  class PlaceholderLeakError < ArgumentError
    attr_reader :kinds, :count

    def initialize(kinds, count)
      @kinds = kinds.freeze
      @count = count
      noun = count == 1 ? "placeholder" : "placeholders"
      super("#{count} redaction #{noun} would reach a reader " \
            "(kinds: #{kinds.join(', ')})")
    end
  end

  # The host boundary: assert that no placeholder reaches a reader — the Ruby
  # port of `python/src/vicary/placeholders.py`.
  #
  # vicary masks what it redacts as typed placeholders (`{NAME_1}`,
  # `{LOCATION_2}`, `{EMAIL}`) and a host restores the ones a reader may see.
  # Everything else must never be displayed, and two ways one gets out anyway
  # are invisible to the masker: a model echoes a placeholder the host's restore
  # map does not cover, or paraphrases one — `[NAME_1]`, `{Name_1}`,
  # `{ NAME_1 }`, a bare `NAME_1`.
  #
  # {assert_no_placeholders} raises before the text is shown;
  # {scrub_placeholders} removes them (a prose sentence carrying one is dropped,
  # a quote elides it with `…`). Nothing is rewritten back into a word here.
  #
  # The pattern source is byte-identical in all three front doors, written in
  # the dialect the engines share — ASCII classes rather than `\b`/`\w`/`\s`,
  # and `\A`/`\z` rather than `^`/`$`, which anchor per line in Ruby.
  # `test/placeholders_test.rb` compares it with `conformance/placeholders.json`.
  module Placeholders
    KINDS = %w[
      ADDRESS AGE AWS_ACCESS_KEY AWS_SECRET_KEY
      CREDIT_DEBIT_CARD_CVV CREDIT_DEBIT_CARD_EXPIRY
      CREDIT_DEBIT_CARD_NUMBER DATE_OF_BIRTH DRIVER_ID EMAIL
      IP_ADDRESS LICENSE_PLATE LOCATION MAC_ADDRESS NAME
      ORGANIZATION PASSWORD PERSON PHONE PIN SCHOOL URL
      US_BANK_ACCOUNT_NUMBER US_BANK_ROUTING_NUMBER US_PASSPORT_NUMBER
      US_SOCIAL_SECURITY_NUMBER USERNAME ZIP_CODE
    ].freeze
    private_constant :KINDS

    # Every placeholder kind vicary or its Guardrail arm can mint, plus
    # `PERSON`, which is what a model writes when it paraphrases `NAME`.
    # Longest first, so a match reports the whole kind.
    PLACEHOLDER_KINDS = KINDS.sort_by { |kind| [-kind.length, kind] }.freeze

    # The kind reported for a `{UPPER_CASE}` token that names no known kind.
    OTHER_KIND = "OTHER"

    WORD = "[A-Za-z0-9_]"
    private_constant :WORD

    # `NAME` -> `[Nn][Aa][Mm][Ee]`: case-insensitive in every dialect.
    def self.fold(kind)
      kind.chars.map { |c| c.match?(/[A-Z]/) ? "[#{c}#{c.downcase}]" : c }.join
    end
    private_class_method :fold

    # Anything placeholder-shaped: vicary's own `{KIND}` / `{KIND_n}` for any
    # upper-case kind; a known kind in any case inside `{}`, `[]` or `<>` with
    # stray spaces or tabs; or a bare upper-case `KIND_n` as a whole word.
    PLACEHOLDER_LIKE = Regexp.new(
      "\\{[A-Z][A-Z_]*(?:_[0-9]+)?\\}" \
      "|[{\\[<][ \\t]*(?:#{PLACEHOLDER_KINDS.map { |k| fold(k) }.join('|')})" \
      "(?:_[0-9]+)?[ \\t]*[}\\]>]" \
      "|(?<!#{WORD})(?:#{PLACEHOLDER_KINDS.join('|')})_[0-9]+(?!#{WORD})"
    )

    # A sentence ends at `.`, `!` or `?` followed by whitespace — but not after
    # an honorific, an initial, `e.g.`, `i.e.` or `vs.`.
    SENTENCE_END = Regexp.new(
      "(?<![A-Z][a-z]\\.)" \
      "(?<!(?<!#{WORD})[A-Z][a-z][a-z]\\.)" \
      "(?<!(?<!#{WORD})[A-Z]\\.)" \
      "(?<!(?<!#{WORD})e\\.g\\.)" \
      "(?<!(?<!#{WORD})i\\.e\\.)" \
      "(?<!(?<!#{WORD})vs\\.)" \
      "(?<=[.!?])[ \\t\\n\\r\\f\\v]+"
    )

    # What stands in for a placeholder when every sentence of a prose field
    # carries one. Any other kind reads "this".
    GENERIC_STAND_INS = {
      "NAME" => "someone",
      "PERSON" => "someone",
      "LOCATION" => "a place",
      "SCHOOL" => "the school",
      "ORGANIZATION" => "an organization"
    }.freeze

    TRIM = /\A[ \t\n\r\f\v]+|[ \t\n\r\f\v]+\z/
    TRIM_TOKEN = /\A[{}\[\]<> \t\n\r\f\v]+|[{}\[\]<> \t\n\r\f\v]+\z/
    ELISION = /[ \t\n\r\f\v]*…[ \t\n\r\f\v]*/
    ELISION_BEFORE_PUNCT = / … ([.,;:!?])/
    NUMBER_SUFFIX = /_[0-9]+\z/
    private_constant :TRIM, :TRIM_TOKEN, :ELISION, :ELISION_BEFORE_PUNCT,
                     :NUMBER_SUFFIX

    # One placeholder found in text. `start`/`end` count characters.
    Match = Struct.new(:text, :kind, :start, :end) do
      def to_h
        { "text" => text, "kind" => kind, "start" => start, "end" => self.end }
      end
    end

    # Text with every placeholder removed, and how many were.
    ScrubResult = Struct.new(:text, :removed)

    class << self
      # The kind a placeholder token names: `[ Name_2 ]` -> `NAME`; else
      # {OTHER_KIND}.
      def kind_of(token)
        kind = token.gsub(TRIM_TOKEN, "").upcase.sub(NUMBER_SUFFIX, "")
        PLACEHOLDER_KINDS.include?(kind) ? kind : OTHER_KIND
      end

      # Every placeholder in `text`, in order.
      def find(text)
        return [] if text.nil? || text.empty?

        text.to_enum(:scan, PLACEHOLDER_LIKE).map do
          m = Regexp.last_match
          Match.new(m[0], kind_of(m[0]), m.begin(0), m.end(0))
        end
      end

      # Whether `text` carries any placeholder.
      def any?(text)
        !text.nil? && !text.empty? && PLACEHOLDER_LIKE.match?(text)
      end

      # Return `text` unchanged, or raise {Vicary::PlaceholderLeakError}. Call
      # it on the last string a host builds before a reader sees it.
      def assert_none(text)
        found = find(text)
        unless found.empty?
          raise PlaceholderLeakError.new(found.map(&:kind).uniq.sort, found.length)
        end

        text
      end

      # Remove every placeholder from `text`. Prose (the default) drops each
      # sentence carrying one, or — when every sentence does — replaces each
      # with its {GENERIC_STAND_INS} entry; `quote: true` elides each as `…`.
      def scrub(text, quote: false)
        return ScrubResult.new(text, 0) unless any?(text)

        removed = text.scan(PLACEHOLDER_LIKE).length
        if quote
          out = text.gsub(PLACEHOLDER_LIKE, "…").gsub(ELISION, " … ")
                    .gsub(ELISION_BEFORE_PUNCT, ' …\1').gsub(TRIM, "")
          return ScrubResult.new(out, removed)
        end

        kept = text.gsub(TRIM, "").split(SENTENCE_END).reject { |s| any?(s) }
        return ScrubResult.new(kept.join(" "), removed) unless kept.empty?

        out = text.gsub(PLACEHOLDER_LIKE) do |token|
          GENERIC_STAND_INS.fetch(kind_of(token), "this")
        end
        ScrubResult.new(out, removed)
      end
    end
  end

  class << self
    # Every placeholder in `text` — see {Vicary::Placeholders}.
    def find_placeholders(text)
      Placeholders.find(text)
    end

    # Whether `text` carries any placeholder.
    def has_placeholders?(text)
      Placeholders.any?(text)
    end

    # Return `text`, or raise {Vicary::PlaceholderLeakError}.
    def assert_no_placeholders(text)
      Placeholders.assert_none(text)
    end

    # Remove every placeholder from `text`.
    def scrub_placeholders(text, quote: false)
      Placeholders.scrub(text, quote: quote)
    end
  end
end
