# frozen_string_literal: true

require "set"

module Vicary
  # Offline notability lookup: is this name a public figure, or somebody's cousin?
  #
  # The Ruby port of `python/src/vicary/gazetteer.py`. Same tiers, same verdicts,
  # same asymmetry — **notable => KEEP, everything else => REDACT** — so a miss
  # here costs precision (a public figure masked) while a false positive costs
  # recall, which is the gap the library exists to close.
  #
  # Candidate generation over capitalised token sequences proposes
  # `Terrence Okonkwo` and `Vincent van Gogh` with equal confidence, because in
  # English prose they are the same thing: two capitalised words. No syntactic
  # feature separates them, so the filter is a set-membership lookup, and this
  # module is the lookup.
  #
  # **A candidate is never split into tokens and tested piecewise.** If it were,
  # `Priya Raghunathan-Bell` would resolve notable off `Bell` and a real student's
  # name would leak. Whole-string matching is what makes the multi-token tier safe
  # to populate broadly, and it is why honorifics are *not* stripped before
  # lookup: `Coach Bramwell` matches no label and therefore redacts, where
  # stripping the title would demote it to a bare surname — the shape most likely
  # to collide with a public figure. The accepted cost is that `President Lincoln`
  # over-redacts.
  #
  # Two tiers are deliberately invisible to {Index#notability}: `given` points the
  # other way (a common first name is evidence of a *person*, so on the inbound
  # path it means redact), and `settlement` types a mask rather than granting one.
  # Wiring either into `notability` would readmit the exact PII the tiers exist to
  # remove, which is why each has its own guard test.
  module Gazetteer
    # Lookup verdicts. Strings rather than symbols so they survive a JSON round
    # trip into and out of the conformance spec unchanged.
    NOT_NOTABLE = "not_notable"
    TITLE = "title"
    FULL_NAME = "full_name"
    ICONIC_SHORT = "iconic_short"
    PLACE = "place"

    # A nationality or regional adjective — `Cuban`, `Nigerian`, `Bostonian`.
    #
    # Its own verdict rather than folded into PLACE because it is not a place: it
    # is a word *derived* from one, it is the only keep tier with no notability
    # evidence behind it, and eval attribution needs to see it separately to tell
    # whether this tier is where a leak came from.
    DEMONYM = "demonym"

    # Every tier this reader knows. An asset carrying a tier absent from this
    # list is refused rather than ignored — a tier added to the builder and
    # forgotten here would read back as an empty set, and an empty KEEP tier
    # redacts everything it was built to protect while presenting as
    # over-aggressive tuning.
    TIER_NAMES = %w[full short place given title demonym settlement].freeze

    # Name particles that may lead a two- or three-token *partial* surname.
    #
    # Kept in sync with the Python runtime's list by a unit test rather than by
    # import; the asset itself carries no copy.
    PARTICLES = Set.new(%w[
      van von de del della di da du la le
      les der den ten ter dos das al bin ibn
      mac mc st saint san abu ben op vander
    ]).freeze

    # Honorifics and role titles. NOT stripped before lookup — exposed because a
    # leading title is a positive signal that a candidate is a real person in the
    # student's life, which is a candidate-generator concern.
    ROLE_TITLES = Set.new(%w[
      mr mrs ms miss mx dr doctor prof professor
      coach principal officer sgt sergeant capt captain
      rev reverend father sister brother pastor rabbi
      imam nurse sen senator rep gov governor mayor
      sir dame lady lord aunt uncle grandma grandpa
    ]).freeze

    # Curly quotes and dashes that NFKD leaves alone.
    #
    # Student prose is full of them — a word processor turns every apostrophe
    # curly — and without this mapping `Lincoln’s` folds to `lincoln s` and misses
    # every tier, silently over-masking a notable name on the most ordinary
    # punctuation there is. Identical to the Python runtime's `_SMART_QUOTES`; a
    # unit test pins them together.
    SMART_QUOTES = {
      "‘" => "'", "’" => "'", "ʼ" => "'", "′" => "'",
      "“" => '"', "”" => '"',
      "‐" => "-", "‑" => "-", "‒" => "-", "–" => "-",
      "—" => "-", "−" => "-"
    }.freeze

    SMART_QUOTE_PATTERN = Regexp.union(SMART_QUOTES.keys).freeze

    # Letters and digits, matching Python's Unicode-aware `str.isalnum()`.
    ALNUM = /[[:alpha:][:digit:]]/.freeze

    # The asset is absent, unreadable, or not the shape this reader understands.
    #
    # Raised rather than degrading to "nothing is notable". That fallback is
    # privacy-safe and product-hostile — every public figure in every essay masked
    # — and it looks like a tuning regression rather than a packaging bug for
    # however long it takes somebody to notice.
    class AssetError < StandardError; end

    EMPTY = Set.new.freeze

    # Fold a name to its lookup key.
    #
    # Accent-stripped, lower-cased, punctuation reduced to spaces. The apostrophe
    # and internal hyphen survive because they belong to the name (`O'Keeffe`,
    # `Raghunathan-Bell`) rather than surrounding it. A trailing possessive is
    # dropped, because `Terrence's older brother` presents the name as
    # `Terrence's` and a lookup that misses on the clitic is a leak.
    #
    # Must fold identically to the Python runtime's `normalize`, because the asset
    # is keyed by one fold and probed by the other. If they drift, every lookup
    # silently misses and the gazetteer answers "nothing is notable" while looking
    # perfectly healthy.
    #
    # **One documented divergence from Python, unreachable on this asset**, shared
    # with the TypeScript port for the same reason. Python drops characters whose
    # *canonical combining class* is non-zero; this drops `\p{M}` — every mark.
    # The two sets differ only for marks with a combining class of zero (some Thai
    # and Indic vowel signs), which Python turns into a space and this drops
    # outright. That changes a key only when such a mark sits *between* two
    # alphanumerics, which cannot happen in a gazetteer whose keys are
    # Latin-folded, nor in the English prose the conformance frames carry.
    def self.normalize(name)
      folded = name.gsub(SMART_QUOTE_PATTERN) { |char| SMART_QUOTES.fetch(char, char) }
      folded = folded.unicode_normalize(:nfkd)
      folded = folded.gsub(/\p{M}/, "")
      folded = folded.downcase

      key = folded.each_char.map { |char|
        ALNUM.match?(char) || char == "'" || char == "-" ? char : " "
      }.join.split(" ").reject(&:empty?).join(" ")

      ["'s", "s'"].each do |clitic|
        next unless key.end_with?(clitic) && key.length > clitic.length + 1

        key = key[0...-clitic.length].sub(/'+\z/, "").strip
        break
      end

      key
    end

    # An immutable, loaded notability index over the tiers the asset carries.
    #
    # The derived indices (+title_heads+, +title_prefixes+) are memoized on first
    # use rather than taken as constructor arguments, because they are functions
    # of +title+ and must never be able to disagree with it.
    class Index
      attr_reader :full, :short, :place, :given, :title, :demonym, :settlement, :meta

      def initialize(asset)
        asset.tiers.each_key do |name|
          next if TIER_NAMES.include?(name)

          raise AssetError,
                "unknown gazetteer tier #{name.inspect}. Refusing the asset " \
                "rather than ignoring the tier: a tier this reader drops is a " \
                "tier that reads back empty, and an empty keep tier redacts " \
                "everything it was built to protect while looking like " \
                "over-aggressive tuning."
        end

        @full = asset.tiers.fetch("full", EMPTY)
        @short = asset.tiers.fetch("short", EMPTY)
        @place = asset.tiers.fetch("place", EMPTY)
        # Common given names. The INVERSE signal — see #common_given_name?.
        @given = asset.tiers.fetch("given", EMPTY)
        # Works and fictional characters — multi-token only. See #title?.
        @title = asset.tiers.fetch("title", EMPTY)
        # English demonyms — `cuban`, `nigerian`. A KEEP, see DEMONYM.
        @demonym = asset.tiers.fetch("demonym", EMPTY)
        # Human settlements. Neither a keep nor a redact signal — the only tier
        # that is neither. See #settlement?.
        @settlement = asset.tiers.fetch("settlement", EMPTY)
        @meta = asset.meta
      end

      # Entries that can make something KEEP.
      #
      # +given+ and +settlement+ are excluded on purpose: neither grants a keep,
      # so counting them would inflate the one number that answers "how much
      # notability does this asset carry".
      def entry_count
        full.size + short.size + place.size + title.size + demonym.size
      end

      # First tokens of every title, so a scanner can skip most positions.
      #
      # Without this the title scan costs one lookup per candidate length at
      # every token. With it the common case is a single set miss.
      def title_heads
        @title_heads ||= begin
          heads = Set.new
          title.each do |key|
            space = key.index(" ")
            heads << (space.nil? ? key : key[0, space])
          end
          heads
        end
      end

      # Every token-prefix of every title, so a scan can stop the moment no title
      # can still be reached.
      #
      # This is the automaton the per-position n-gram scan was standing in for: a
      # walk advances only while some title still starts with what it has read,
      # which on ordinary prose is one or two tokens. A flat set of pre-joined
      # prefixes rather than a trie of objects — same asymptotics, a fraction of
      # the allocations, built by a single pass over keys that are already
      # normalised.
      def title_prefixes
        @title_prefixes ||= begin
          prefixes = Set.new
          title.each do |key|
            tokens = key.split(" ")
            (1...tokens.length).each { |length| prefixes << tokens[0, length].join(" ") }
          end
          prefixes
        end
      end

      # Longest title in tokens, so a scanner knows how far to look ahead.
      def max_title_tokens
        @max_title_tokens ||= title.map { |key| key.count(" ") + 1 }.max || 0
      end

      # True when some title starts with (or equals) the token sequence +key+.
      #
      # +key+ is an already-folded lookup key — space-joined lower-cased tokens —
      # not raw text. The scan folds each token of the document once and joins,
      # rather than re-normalising a growing substring at every length.
      def title_prefix?(key)
        title_prefixes.include?(key) || title.include?(key)
      end

      # True when +name+ is a published work or a fictional character.
      #
      # The full tier is `P31 wd:Q5` — human — so before this tier existed every
      # work title and every fictional character redacted: "Harry Potter taught me
      # about friendship" came back as "{NAME} taught me about friendship".
      #
      # Multi-token by construction, and that is a safety property rather than a
      # convenience. "It", "Up", "Her", "Room", "Brave" and "Cats" are all films;
      # a single-token title tier would make those ordinary words permanently
      # notable, and notable means KEEP, so the cost would land on recall.
      def title?(name)
        key = Gazetteer.normalize(name)
        key.include?(" ") && title.include?(key)
      end

      # True when +token+ is a first name lots of notable people share.
      #
      # Not part of the notability decision, and deliberately not consulted by
      # #notability — it points the other way. A given-name hit is evidence the
      # token names a *person*, which on the inbound path means redact.
      #
      # It exists for the two frames capitalisation cannot reach: `then terrence
      # okonkwo showed up` and `MY BEST FRIEND DESHAWN PRITCHARD` score zero for
      # any candidate generator keyed on capitalisation, by construction. A
      # case-insensitive scan closes that, and a scan needs a list. This is the
      # list; the scan belongs to the candidate generator.
      def common_given_name?(token)
        key = Gazetteer.normalize(token)
        !key.empty? && !key.include?(" ") && given.include?(key)
      end

      # True when +name+ is a town, city or village.
      #
      # **Not part of the notability decision, and deliberately not consulted by
      # #notability.** A settlement is a student's hometown, so it must redact;
      # that is the whole reason settlements are subtracted from the place tier.
      # What this answers is the *next* question, asked only about a span already
      # being masked: which placeholder does it get. A host that reads the type
      # back writes "great job describing your trip to {LOCATION}", and before
      # this tier existed it wrote "{NAME}".
      #
      # The failure modes are not symmetric with a keep tier's: a miss types a
      # place `{NAME}` and a false positive types a person `{LOCATION}`. Both are
      # already redacted.
      def settlement?(name)
        key = Gazetteer.normalize(name)
        !key.empty? && settlement.include?(key)
      end

      # Classify +name+. One of the verdict constants above.
      #
      # Places are checked first: the string is being judged on what it *names*,
      # and a place-name that is also a surname (`Washington`, `Delaware`) is
      # keepable either way, so resolving it as a place costs nothing and saves a
      # probe.
      def notability(name)
        key = Gazetteer.normalize(name)
        return NOT_NOTABLE if key.empty?

        tokens = key.split(" ")
        return PLACE if place.include?(key)

        if tokens.length == 1
          return ICONIC_SHORT if short.include?(key)

          # After `short`, because a token that is both — none today, but the
          # tiers are rebuilt from a moving upstream — should report the tier that
          # carries notability evidence rather than the one that does not.
          return demonym.include?(key) ? DEMONYM : NOT_NOTABLE
        end

        # "van Gogh", "de Gaulle" — a partial, not a full name, so it is held to
        # the strict short-tier threshold.
        return ICONIC_SHORT if tokens.length <= 3 && PARTICLES.include?(tokens[0]) && short.include?(key)
        return FULL_NAME if full.include?(key)

        # Titles resolve LAST. "Joan of Arc" and "van Gogh" are both also film
        # titles, and attributing them to the title tier would be true but less
        # specific — the person is who the student wrote about. Either way the
        # verdict is KEEP; only the reported tier changes, and that tier is what
        # eval attribution and telemetry read.
        return TITLE if tokens.length > 1 && title.include?(key)

        NOT_NOTABLE
      end

      def notable?(name)
        notability(name) != NOT_NOTABLE
      end
    end

    class << self
      # Load (and memoize) the notability index.
      #
      # Lazy: requiring this file reads nothing. Call it at process init to move
      # the decompression off the first request's latency; otherwise the first
      # lookup pays it.
      def load(directory: nil)
        return @cached if @cached && directory.nil?

        index = Index.new(Asset.load(directory: directory))
        @cached = index if directory.nil?
        index
      end

      # Drop the memoized index. For tests that swap in a fixture asset.
      def reset_cache
        @cached = nil
      end

      # True when +name+ is a public figure or a public place. `notable => KEEP`.
      def notable?(name)
        load.notable?(name)
      end

      # Which tier matched +name+, for telemetry and for eval attribution.
      def notability(name)
        load.notability(name)
      end

      # True when +token+ is a common given name — a REDACT signal, not a KEEP.
      def common_given_name?(token)
        load.common_given_name?(token)
      end

      # True when +name+ is a town or city — a TYPING signal, not a keep.
      def settlement?(name)
        load.settlement?(name)
      end

      # True when +name+ is a published work or a fictional character.
      def title?(name)
        load.title?(name)
      end

      # True when some title *starts* with +token+ — the scan's cheap prefilter.
      #
      # Deliberately uses +downcase+ rather than {normalize}. This runs once per
      # word of every essay, and +normalize+ does an NFKD decomposition and a
      # per-character rebuild. The heads are already folded and overwhelmingly
      # plain ASCII, so the only cost is that a title beginning with an accented
      # word fails the prefilter and is not matched. That loses a keep, never a
      # redaction.
      def title_head?(token)
        load.title_heads.include?(token.downcase)
      end

      # True when some title starts with the folded token sequence +key+.
      def title_prefix?(key)
        load.title_prefix?(key)
      end

      # Longest title in tokens — how far a title scanner must look ahead.
      def max_title_tokens
        load.max_title_tokens
      end
    end
  end
end
