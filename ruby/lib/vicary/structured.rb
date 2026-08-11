# frozen_string_literal: true

require "set"

module Vicary
  # Structured entities and interpolated identity — the two legs regex does well.
  #
  # The Ruby port of `python/src/vicary/local_classifier.py`.
  #
  # Structured entities (EMAIL, PHONE, SSN, CARD, IP, ZIP, street ADDRESS) are
  # *syntax*, and regex scored **100%** on them in the harness that measured the
  # Bedrock Guardrail at 97.3%. No model beats 100%, and a regex is free and
  # sub-millisecond.
  #
  # The student's own name and school are the NAME/SCHOOL spans that matter most,
  # and they are not being guessed at: the caller knows who submitted the essay.
  # Interpolating those into patterns turns the hardest category for a detector
  # into an exact match.
  #
  # **Order is the contract, not an optimisation.** The first pattern to claim a
  # span wins, and placeholder indices follow mint order, so reordering these
  # tables changes the output bytes even when it changes no verdict. Identity
  # runs first (an address line can otherwise swallow a surname); EMAIL before
  # PHONE; SSN and CARD before the generic digit runs; ZIP and AGE last, because
  # both are bare digits and would claim characters belonging to a phone, card or
  # address.
  #
  # ## Regex dialect
  #
  # Ported from Python `re`. Three differences touch this file, each pinned by
  # `test/dialect_test.rb` rather than reasoned about:
  #
  # * `$` in Ruby means end of *line*; in Python without `re.MULTILINE` it means
  #   end of string, or just before a trailing newline. Ruby spells that `\Z`,
  #   and {ZIP} uses it. With a bare `$` any five-digit number ending a line —
  #   a locker combination, a population, a year range — satisfies the ZIP
  #   lookahead and masks, in every hard-wrapped essay. Neither the conformance
  #   frames nor the primitives spec catches that, because both corpora are
  #   single-line.
  # * `\w` is Unicode-aware in Python and ASCII-only in Ruby. Every `\w` here is
  #   written out as {W} so the two agree.
  # * `\d` and `\s` diverge the same way and are left as-is, matching the
  #   TypeScript port, which has the same narrowing and reproduces every frame:
  #   no fixture distinguishes them, and widening them here alone would make this
  #   the odd port out.
  #
  # `\b` is left alone in the structured patterns. Unlike JavaScript's, Ruby's is
  # Unicode-aware and agrees with Python — and it is exact for the ASCII
  # neighbourhoods these patterns match either way. {word_pattern} still spells
  # its boundaries out, because the literal it wraps is a caller's name and may
  # end in punctuation that `\b` cannot assert against at all.
  module Structured
    # Python's `\w`, written out. Ruby's `\w` is `[a-zA-Z0-9_]`, so a phone
    # number preceded by an accented letter would match here and not there if
    # this were left alone.
    W = '\p{L}\p{N}_'

    # Practical email shape. Deliberately not RFC 5322 — the full grammar matches
    # strings no student writes and is a known source of catastrophic
    # backtracking.
    EMAIL = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b/

    # US SSN. Excludes the never-issued ranges (000/666/9xx area, 00 group, 0000
    # serial) so dates and score ranges don't trip it.
    SSN = /\b(?!000|666|9\d{2})\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}\b/

    # Candidate payment-card runs, 13–19 digits with optional space/hyphen
    # grouping. Luhn-checked below, because an un-checked pattern this loose eats
    # any long number a student writes.
    CARD_CANDIDATE = /\b(?:\d[ -]?){12,18}\d\b/

    # NANP phone, plus common international prefix. Requires separators or parens
    # somewhere so a bare 10-digit number isn't assumed to be a phone.
    PHONE = Regexp.new(
      "(?<![#{W}-])" \
      '(?:\+?\d{1,3}[-.\s]?)?' \
      '(?:' \
      '\(\d{3}\)[-.\s]*\d{3}[-.\s]?\d{4}' \
      '|\d{3}[-.\s]\d{3}[-.\s]\d{4}' \
      ')' \
      '(?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?' \
      "(?![#{W}-])",
    )

    IP = /\b(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\b/

    # US street address: number + street words + a suffix. The suffix list is
    # what keeps this from matching "I ran 3 miles down the road" — a bare
    # number-plus-words pattern has an unacceptable false-positive rate in prose.
    STREET_SUFFIX =
      '(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct' \
      '|Circle|Cir|Place|Pl|Terrace|Ter|Way|Parkway|Pkwy|Highway|Hwy|Trail|Trl' \
      '|Square|Sq|Loop|Alley|Commons)'

    ADDRESS = Regexp.new(
      '\b\d{1,6}\s+' \
      '(?:[NSEW]\.?|North|South|East|West|Northeast|Northwest|Southeast|Southwest)?\s*' \
      "(?:[A-Z][A-Za-z.'-]*\\s+){0,4}" \
      "#{STREET_SUFFIX}" '\b\.?' \
      "(?:\\s*(?:Apt|Apartment|Suite|Ste|Unit|\#)\\s*[#{W}-]+)?",
    )

    # US ZIP, with the optional +4. Bounded so it can't eat a 5-digit year range.
    # `\Z` rather than `$` — see the dialect note above.
    ZIP = /\b\d{5}(?:-\d{4})?\b(?=\s*\Z|\s*[,.]|\s+[A-Z]{2}\b)/

    # Explicit age statements. Bare numbers are not ages; the phrasing is.
    AGE = /\b(?:(?:I\s+am|I'm|aged?|age(?:d)?\s+of)\s+)(\d{1,2})\b(?=\s*(?:years?\s+old)?)|\b(\d{1,2})\s+years?\s+old\b/i

    # URLs. Student essays cite them, and a personal profile URL is PII.
    URL_PATTERN = %r{\bhttps?://[^\s<>"']+|\bwww\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+[^\s<>"']*}

    # Anonymization markers somebody upstream already substituted for real PII.
    #
    # Text arriving with these in it has *already been redacted*, so masking them
    # again destroys information while adding none. The kinds are the closed set
    # the ASAP corpus authors used, measured over the full training set rather
    # than taken from their documentation: 14 distinct kinds across 64,166
    # occurrences.
    #
    # Why this is in the shipped classifier and not just the eval harness: real
    # student prose contains none of these, so production behaviour is unchanged.
    # What changes is every measurement taken over that corpus — a model trained
    # on it saw these tokens at ~22 per essay, and rewriting them to `{USERNAME}`
    # hands it a token it has never seen.
    UPSTREAM_ANON_KINDS = %w[
      CAPS NUM PERSON LOCATION ORGANIZATION MONTH DATE
      PERCENT TIME MONEY EMAIL STATE CITY DR
    ].freeze

    # `@handles`. Requires the `@` so it can't eat ordinary words, and a length
    # floor so it can't eat an email's local part (email runs first anyway). The
    # lookahead spares upstream anonymization markers; a genuine all-caps handle
    # colliding with one of those 14 words is the accepted cost, and it is the
    # right way round — a missed handle is one span, and eating `@PERSON1`
    # corrupts every essay in the evaluation corpus.
    USERNAME = Regexp.new(
      "(?<![#{W}@.])@(?!(?:#{UPSTREAM_ANON_KINDS.join('|')})\\d*\\b)[A-Za-z0-9_]{3,30}\\b",
    )

    # Date of birth, explicitly labelled.
    DOB = %r{\b(?:date\s+of\s+birth|d\.?o\.?b\.?|born\s+on)\s*:?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b}i

    # (placeholder kind, pattern) in application order.
    #
    # CARD is handled separately because it needs the Luhn gate; ZIP and AGE run
    # after it for the reason in the module docstring.
    STRUCTURED = [
      ["EMAIL", EMAIL],
      ["URL", URL_PATTERN],
      ["US_SOCIAL_SECURITY_NUMBER", SSN],
      ["IP_ADDRESS", IP],
      ["PHONE", PHONE],
      ["ADDRESS", ADDRESS],
      ["DATE_OF_BIRTH", DOB],
      ["USERNAME", USERNAME],
    ].freeze

    # Given names that are also ordinary English words.
    #
    # A bare first-name match on one of these destroys prose ("Will you go", "the
    # Art of war", "a Grace period"), so a standalone occurrence is left alone;
    # the full name and the surname still mask. Skewed toward over-inclusion on
    # purpose: a missed first name is one span, a wrongly-masked common word
    # corrupts every essay that uses it.
    AMBIGUOUS_GIVEN_NAMES = %w[
      art bill brook chase dawn drew faith frank
      grace grant hope jack joy june mark may
      mercy miles nick pat patience penny rich
      robin rose sky summer sunny trinity will wills
    ].to_set.freeze

    # Surnames common enough as words to need the same treatment.
    AMBIGUOUS_SURNAMES = %w[
      young white black green brown king moore price rich stone
    ].to_set.freeze

    # Possessive tails, straight and curly.
    #
    # A word processor turns every apostrophe curly, so the straight forms alone
    # miss the majority of real prose. `s'` is the plural-family form ("the
    # Delacroix-Whitfields' house").
    POSSESSIVE_TAIL = "(?:['’]s|s['’])?"

    class << self
      # Luhn checksum. Cuts the card pattern's false positives on long numbers.
      def luhn_ok?(digits)
        total = 0
        digits.each_char.reverse_each.with_index do |char, i|
          d = char.ord - 48
          if i.odd?
            d *= 2
            d -= 9 if d > 9
          end
          total += d
        end
        (total % 10).zero?
      end

      # Leading and trailing boundary assertions appropriate to `literal`.
      #
      # `\b` is a boundary only when there is a word character beside it, so a
      # literal *ending* in punctuation — "O'Brien (Jr.)", which is exactly the
      # shape roster data arrives in — can never satisfy a trailing `\b` and
      # silently matches nothing at all. Asserting only on the side that has a
      # word character to assert against masks that literal, and is identical to
      # `\b` for every literal that does not.
      #
      # Written as lookarounds over {W} rather than `\b` because Ruby's `\b` is
      # ASCII-only where Python's is Unicode-aware; these agree with Python for
      # an accented name.
      def literal_boundaries(literal)
        word = /\A[#{W}]\z/
        [
          literal[0].to_s.match?(word) ? "(?<![#{W}])" : "",
          literal[-1].to_s.match?(word) ? "(?![#{W}])" : "",
        ]
      end

      # Case-insensitive whole-token match for a literal, possessive-tolerant.
      #
      # A bare boundary mis-handles a trailing apostrophe-s, which is exactly how
      # a name appears in student prose ("Sarah's essay"), so the possessive is
      # part of the match and gets masked with the name.
      def word_pattern(literal)
        lead, trail = literal_boundaries(literal)
        Regexp.new("#{lead}#{Regexp.escape(literal)}#{POSSESSIVE_TAIL}#{trail}", Regexp::IGNORECASE)
      end

      # `"Lincoln High School"` => `"LHS"`. Nil when it would be too short.
      #
      # Students write the acronym far more often than the full name, and a
      # two-letter acronym collides with ordinary words and state codes.
      def school_acronym(name)
        acronym = name.scan(/[A-Za-z][\p{L}\p{N}_'-]*/).map { |word| word[0] }.join.upcase
        acronym.length >= 3 ? acronym : nil
      end

      # Patterns masking this student's own identifying strings.
      #
      # Ordered most-specific-first: the full name is matched before either part
      # of it, so "Jane Quincy-Adams" becomes one `{NAME}` rather than two
      # adjacent placeholders.
      def identity_patterns(identity)
        out = []
        first = identity_field(identity, :first_name)
        last = identity_field(identity, :last_name)
        school = identity_field(identity, :school_name)

        if !first.empty? && !last.empty?
          out << ["NAME", word_pattern("#{first} #{last}")]
          # "Adams, Jane" — the roster/header order.
          out << ["NAME", word_pattern("#{last}, #{first}")]
        end
        out << ["NAME", word_pattern(last)] if !last.empty? && !AMBIGUOUS_SURNAMES.include?(last.downcase)
        out << ["NAME", word_pattern(first)] if !first.empty? && !AMBIGUOUS_GIVEN_NAMES.include?(first.downcase)

        extra_names(identity).each do |raw|
          extra = raw.to_s.strip
          out << ["NAME", word_pattern(extra)] unless extra.empty?
        end

        unless school.empty?
          out << ["SCHOOL", word_pattern(school)]
          acronym = school_acronym(school)
          unless acronym.nil?
            # Case-SENSITIVE for the acronym: lowercasing it would match ordinary
            # words (three-letter acronyms shaped like "was"/"his" are a real
            # hazard).
            out << ["SCHOOL", Regexp.new("\\b#{Regexp.escape(acronym)}\\b")]
          end
        end
        out
      end

      # Mask identity and structured spans, minting through the caller's minter.
      #
      # The minter is passed in rather than created here because it must serve
      # the whole document: candidate generation numbers into the same counters,
      # and a second minter would emit `{NAME_1}` for two different people.
      #
      # Returns `[masked_text, n_masked]`.
      def mask(text, identity, minter)
        return [text, 0] if text.nil? || text.empty?

        masked = text
        n = 0

        # Identity patterns run FIRST: a name is the span most likely to be
        # partially consumed by a looser pattern (an address line can swallow a
        # surname), and masking it first makes that impossible.
        (identity_patterns(identity) + STRUCTURED).each do |kind, pattern|
          masked, count = minter.substitute(kind, pattern, masked)
          n += count
        end

        # Cards need the Luhn gate, so they can't go through a plain
        # substitution.
        masked = masked.gsub(CARD_CANDIDATE) do |match|
          digits = match.gsub(/\D/, "")
          if luhn_ok?(digits)
            n += 1
            minter.mint("CREDIT_DEBIT_CARD_NUMBER", match)
          else
            match
          end
        end

        masked, zip_count = minter.substitute("ZIP_CODE", ZIP, masked)
        n += zip_count

        masked = masked.gsub(AGE) do |match|
          n += 1
          # Only the digits are the age; the surrounding "I am … years old" is
          # the student's prose and has to survive, so this mints against the
          # digit run rather than the whole match.
          digits = /\d{1,2}/.match(match)
          if digits.nil?
            match
          else
            "#{match[0, digits.begin(0)]}#{minter.mint('AGE', digits[0])}#{match[(digits.begin(0) + digits[0].length)..]}"
          end
        end

        [masked, n]
      end

      private

      def identity_field(identity, name)
        return "" unless identity.respond_to?(name)

        identity.public_send(name).to_s.strip
      end

      def extra_names(identity)
        return [] unless identity.respond_to?(:extra_names)

        Array(identity.extra_names)
      end
    end
  end
end
