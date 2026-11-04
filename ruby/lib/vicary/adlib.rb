# frozen_string_literal: true

require "digest"
require "pathname"
require "set"

module Vicary
  # Every pool entry is taken or collides with the document.
  class AdlibPoolExhausted < RuntimeError; end

  # Adlib: an invented value stands in for every redacted span — the Ruby port of
  # `python/src/vicary/adlib.py`. README "Adlib" is the byte-level spec;
  # `conformance/adlib.json` holds the reference's answers. Never log the map.
  module Adlib
    GIVEN = "given"
    SURNAME = "surname"
    NAME = "NAME"
    SCHOOL = "SCHOOL"
    ORGANIZATION = "ORGANIZATION"
    LOCATION = "LOCATION"

    # Python's Unicode classes, spelled out: Ruby's `\w`, `\d`, `\s` are ASCII.
    W = "\\p{L}\\p{N}_"
    LETTER = "\\p{L}\\p{Nl}\\p{No}"
    SPACE = "[:space:]\\u001c-\\u001f"
    PY_END = "(?=\\n?\\z)" # Python `$` without re.M
    private_constant :W, :LETTER, :SPACE, :PY_END

    FORMAL_TITLES = Set.new(%w[
      mr mrs ms miss mx dr doctor prof professor coach principal officer sgt sergeant
      capt captain rev reverend pastor rabbi imam nurse sen senator rep gov governor
      mayor sir dame madam chief fr mister judge detective lord lady
    ]).freeze
    KIN_TITLES = Set.new(%w[
      aunt auntie uncle grandma grandpa grandmother grandfather cousin sister brother
      father mother
    ]).freeze
    TITLES = (FORMAL_TITLES | KIN_TITLES).freeze

    APOSTROPHES = "'’ʼ"
    POSSESSIVE = Regexp.new("(?:[#{APOSTROPHES}][sS]|(?<=[sS])[#{APOSTROPHES}])#{PY_END}")
    LETTERS = Regexp.new("[#{LETTER}]+")
    SEPARATORS = Regexp.new("([#{SPACE}]+|,)")
    SEPARATOR_FULL = Regexp.new("\\A(?:[#{SPACE}]+|,)\\z")
    EDGE = Regexp.new("\\A([^#{W}]*)(.*?)([^#{W}]*)#{PY_END}", Regexp::MULTILINE)
    WS_SPLIT = Regexp.new("([#{SPACE}]+)")
    WS_ONLY = Regexp.new("\\A[#{SPACE}]+\\z")
    ND = /\p{Nd}/
    # Python `str.isdigit`: Nd plus the Numeric_Type=Digit No characters.
    DIGIT = /[\p{Nd}²³¹⁰⁴-⁹₀-₉①-⑨⑴-⑼⒈-⒐⓪⓵-⓽⓿❶-❾➀-➈➊-➒፩-፱᧚]/

    DESCRIPTIVE = Set.new(%w[
      elementary middle high school schools academy college university prep preparatory
      charter montessori institute junior senior jr sr community christian catholic day
      district unified isd usd magnet primary secondary intermediate kindergarten
      preschool early learning center centre club inc llc ltd co corp corporation company
      church temple mosque synagogue foundation library museum hospital clinic team league
      society association council scouts troop band choir orchestra park lake river creek
      county city town township village mount mt valley beach island springs falls heights
      hills bay harbor port north south east west new saint st fort the of and & for at in
      on state national public
    ]).freeze
    SCHOOL_CUES = Set.new(%w[elementary school academy college university]).freeze
    SCHOOL_TAIL_CUES = Set.new(%w[high middle]).freeze
    ORG_CUES = Set.new(%w[
      inc llc ltd corp corporation company club church foundation library museum society
      association league council center centre
    ]).freeze
    RUN_POOLS = { SCHOOL => "school", ORGANIZATION => "org", LOCATION => "place" }.freeze

    DIRECTIONS = Set.new(%w[
      n s e w ne nw se sw north south east west northeast northwest southeast southwest
    ]).freeze
    STREET_SUFFIXES = Set.new(%w[
      street st avenue ave boulevard blvd road rd drive dr lane ln court ct circle cir
      place pl terrace ter way parkway pkwy highway hwy trail trl square sq loop alley
      commons
    ]).freeze
    UNIT = Regexp.new("([#{SPACE}]*(?:(?:Apt|Apartment|Suite|Ste|Unit)\\.?|\\#)[#{SPACE}]*)" \
                      "([#{W}-]+)([^#{W}]*)#{PY_END}", Regexp::IGNORECASE)

    PROVIDERS = Set.new(%w[
      gmail.com yahoo.com outlook.com hotmail.com icloud.com aol.com protonmail.com
      proton.me live.com msn.com me.com mac.com comcast.net
    ]).freeze
    URL_SHAPES = [
      ["youtube.com", "/@{h}"], ["youtube.com", "/c/{h}"],
      ["youtube.com", "/channel/{i}"], ["youtube.com", "/user/{h}"],
      ["instagram.com", "/stories/{h}"], ["instagram.com", "/{h}"],
      ["twitter.com", "/{h}/status/{i}"], ["twitter.com", "/{h}"],
      ["x.com", "/{h}/status/{i}"], ["x.com", "/{h}"],
      ["tiktok.com", "/@{h}/video/{i}"], ["tiktok.com", "/@{h}"],
      ["threads.net", "/@{h}"], ["threads.com", "/@{h}"],
      ["facebook.com", "/profile.php?id={i}"], ["facebook.com", "/people/{h}/{i}"],
      ["facebook.com", "/{h}"],
      ["linkedin.com", "/in/{h}"], ["linkedin.com", "/pub/{h}"],
      ["github.com", "/orgs/{h}"], ["github.com", "/sponsors/{h}"], ["github.com", "/{h}"],
      ["reddit.com", "/user/{h}"], ["reddit.com", "/u/{h}"],
      ["old.reddit.com", "/user/{h}"], ["old.reddit.com", "/u/{h}"],
      ["medium.com", "/@{h}"], ["mastodon.social", "/@{h}"],
      ["bsky.app", "/profile/{h}"], ["snapchat.com", "/add/{h}"],
      ["pinterest.com", "/{h}"], ["twitch.tv", "/{h}"], ["soundcloud.com", "/{h}"],
      ["linktr.ee", "/{h}"], ["vsco.co", "/{h}"], ["patreon.com", "/{h}"],
      ["tumblr.com", "/{h}"], ["deviantart.com", "/{h}"], ["codepen.io", "/{h}"],
      ["replit.com", "/@{h}"], ["scratch.mit.edu", "/users/{h}"],
      ["roblox.com", "/users/{i}"], ["khanacademy.org", "/profile/{h}"],
      ["open.spotify.com", "/user/{h}"], ["steamcommunity.com", "/id/{h}"],
      ["steamcommunity.com", "/profiles/{i}"], ["flickr.com", "/people/{h}"],
      ["flickr.com", "/photos/{h}"], ["wattpad.com", "/user/{h}"],
      ["archiveofourown.org", "/users/{h}"], ["quizlet.com", "/user/{h}"],
      ["goodreads.com", "/user/show/{h}"]
    ].map(&:freeze).freeze
    URL_RESERVED = Set.new(%w[
      about accounts add apps c channel collections compose dashboard developer direct
      directory discover enterprise events explore features gaming groups hashtag help
      home i ideas intent issues join legal login marketplace messages new notifications
      orgs p pages people permalink.php photo.php pin policies pricing privacy profile.php
      pulls reel reels register search settings share sharer.php signup sponsors stories
      stream tagged today topics tos trending tv upload user videos watch you
    ]).freeze
    URL_USER_SUBDOMAINS = Set.new(%w[
      substack.com tumblr.com blogspot.com wordpress.com github.io neocities.org
      weebly.com wixsite.com carrd.co
    ]).freeze

    SSN_PLUG = "799-99-9999"
    ZIP_PLUG = "99999"
    PHONE_AREA_CODE = "201"
    PHONE_PLUG = "201-555-0100"
    IP_RANGES = ["192.0.2.", "198.51.100.", "203.0.113."].freeze
    CARD_PLUGS = [
      [%w[34 37], 15, "378282246310005"],
      [%w[4], 16, "4111111111111111"],
      [%w[4], 13, "4222222222222"],
      [%w[51 52 53 54 55 22 23 24 25 26 27], 16, "5555555555554444"],
      [%w[6011 64 65], 16, "6011111111111117"],
      [%w[30 36 38], 14, "30569309025904"],
      [%w[35], 16, "3530111333300000"]
    ].freeze
    AGE_BANDS = [
      [0, 4], [5, 7], [8, 10], [11, 13], [14, 18], [19, 24], [25, 34],
      [35, 44], [45, 54], [55, 64], [65, 74], [75, 84], [85, 99]
    ].freeze
    DATE = /(\p{Nd}{1,2})([\/-])(\p{Nd}{1,2})\2(\p{Nd}{2,4})/
    ACRONYM = /\A[A-Z]{3,6}\z/
    MAX_BUMPS = 256
    PLUG_KINDS = Set.new(%w[US_SOCIAL_SECURITY_NUMBER ZIP_CODE IP_ADDRESS
                            CREDIT_DEBIT_CARD_NUMBER PHONE]).freeze
    POOL_NAMES = %w[given org place school street surname].freeze

    # One chunk of a name span: kind is "sep" | "title" | "name" | "raw".
    Part = Struct.new(:kind, :text, :key, :lead, :tail, :chunk, :plural) do
      def self.of(kind, text, key: "", lead: "", tail: "", chunk: 0, plural: false)
        new(kind, text, key, lead, tail, chunk, plural)
      end
    end

    # What {invent} returns: one stand-in per span, and `{invented: original}` tokens.
    Invented = Struct.new(:stand_ins, :tokens)

    class << self
      # -- Python string semantics ------------------------------------------------

      def fold(token)
        folded = token.unicode_normalize(:nfc).downcase(:fold)
        folded.tr("’ʼ", "''")
      end

      def rstrip_dots(str) = str.sub(/\.+\z/, "")

      # `str.split(sep)` with Python's answer for "": [""].
      def py_split(str, sep)
        str.empty? ? [""] : str.split(sep, -1)
      end

      def cased_chars?(str, cased, other)
        found = false
        str.each_char do |c|
          return false if c.match?(other) || c.match?(/\p{Lt}/)

          found = true if c.match?(cased)
        end
        found
      end

      def py_lower?(str) = cased_chars?(str, /\p{Lowercase}/, /\p{Uppercase}/)
      def py_upper?(str) = cased_chars?(str, /\p{Uppercase}/, /\p{Lowercase}/)
      def py_alpha?(char) = !char.empty? && char.match?(/\A\p{L}+\z/)
      def py_digits?(str) = !str.empty? && str.each_char.all? { |c| c.match?(DIGIT) }

      # Value of a decimal digit: Nd runs are contiguous 0..9 sets.
      def nd_value(char)
        cp = char.ord
        back = 0
        back += 1 while cp - back - 1 >= 0 && [cp - back - 1].pack("U").match?(ND)
        back % 10
      end

      def py_int(str) = str.each_char.reduce(0) { |acc, c| (acc * 10) + nd_value(c) }

      def letters_of(str) = str.scan(LETTERS).join

      # -- pools -------------------------------------------------------------------

      # Where the vendored pools live: the gem's copy, then the Python package's.
      def pool_search_path
        Asset.search_path.map { |dir| dir.join("pools") } +
          [Pathname.new(__dir__).join("..", "..", "..", "python", "src", "vicary", "pools").expand_path]
      end

      def pool_path(name)
        file = "#{name}.txt"
        dir = pool_search_path.find { |d| d.join(file).file? }
        raise Asset::MissingAssetError, "no adlib pool #{file} in #{pool_search_path.join(', ')}" unless dir

        dir.join(file)
      end

      def load_pool(name)
        @pools ||= {}
        @pools[name] ||= pool_path(name).read(encoding: "UTF-8")
                                        .split(/\r\n|[\n\r\v\f\u001c-\u001e\u0085  ]/)
                                        .map(&:strip).reject(&:empty?).map(&:freeze).freeze
      end

      def given_pool = load_pool("given")
      def surname_pool = load_pool("surname")

      # -- the draw ----------------------------------------------------------------

      def draw(seed, document_digest, purpose, key, bump, index, n)
        material = [seed, document_digest, purpose, key, "#{bump}.#{index}"].join("\0")
        Digest::SHA256.digest(material.encode("UTF-8"))[0, 8].unpack1("Q>") % n
      end

      # -- names -------------------------------------------------------------------

      def split_span(original)
        parts = []
        py_split(original, SEPARATORS).each_with_index do |chunk, index|
          next if chunk.empty?

          if chunk.match?(SEPARATOR_FULL)
            parts << Part.of("sep", chunk)
            next
          end
          lead, core, tail = EDGE.match(chunk).captures
          if (pos = core =~ POSSESSIVE)
            tail = core[pos..] + tail
            core = core[0...pos]
          end
          if core.empty?
            parts << Part.of("raw", chunk)
            next
          end
          if TITLES.include?(rstrip_dots(fold(core))) && parts.none? { |p| p.kind == "name" }
            parts << Part.of("title", chunk)
            next
          end
          pieces = core.split("-", -1)
          pieces.each_with_index do |piece, i|
            piece_lead = i.zero? ? lead : "-"
            piece_tail = i == pieces.length - 1 ? tail : ""
            unless piece.match?(LETTERS)
              parts << Part.of("raw", piece_lead + piece + piece_tail)
              next
            end
            parts << Part.of("name", piece, key: rstrip_dots(fold(piece)), lead: piece_lead,
                                            tail: piece_tail, chunk: index)
          end
        end
        parts
      end

      def initial?(key) = key.scan(LETTERS).length == 1 && rstrip_dots(key).length == 1

      def roles(spans, given_name)
        votes = {}
        spans.each do |parts|
          names = parts.select { |p| p.kind == "name" && !initial?(p.key) }
          next if names.empty?

          names.each { |p| votes[p.key] ||= [0, 0] }
          comma = parts.any? { |p| p.kind == "sep" && p.text == "," }
          titled = parts.any? { |p| p.kind == "title" && FORMAL_TITLES.include?(rstrip_dots(fold(p.text))) }
          chunks = names.map(&:chunk).uniq.sort
          if titled
            names.each { |p| p.chunk == chunks[-1] ? votes[p.key][1] += 2 : votes[p.key][0] += 1 }
          elsif chunks.length > 1
            surname = comma ? chunks[0] : chunks[-1]
            names.each { |p| votes[p.key][p.chunk == surname ? 1 : 0] += 1 }
          end
        end
        votes.to_h do |key, (given, surname)|
          role = if given != surname then given > surname ? GIVEN : SURNAME
                 elsif given_name && !given_name.call(key) then SURNAME
                 else GIVEN
                 end
          [key, role]
        end
      end

      def cased(stand_in, like)
        letters = letters_of(like)
        return stand_in.downcase if py_lower?(letters)
        return stand_in.upcase if py_upper?(letters) && letters.length > 1

        stand_in
      end

      def apostrophe_tail?(tail)
        !tail.empty? && APOSTROPHES.include?(tail[0]) && (tail.length == 1 || !py_alpha?(tail[1]))
      end

      def possessive_tail(tail, stand_in)
        if apostrophe_tail?(tail) && !stand_in.downcase.end_with?("s")
          return tail[0] + (py_upper?(stand_in) ? "S" : "s") + tail[1..]
        end

        tail
      end

      def initials(seed, digest, keys, forbidden)
        alphabet = ("a".."z").to_a.join
        out = {}
        used = Set.new
        keys.each do |key|
          letter = rstrip_dots(key)
          next if out.key?(letter)

          start = draw(seed, digest, "initial", letter, 0, 0, alphabet.length)
          candidate = (0...alphabet.length).lazy.map { |s| alphabet[(start + s) % alphabet.length] }
                                           .find { |c| !forbidden.include?(c) && !used.include?(c) }
          candidate ||= letter
          out[letter] = candidate
          used << candidate
        end
        out
      end

      def plural(word) = word + (word.downcase.end_with?("s", "x", "z", "ch", "sh") ? "es" : "s")

      def plural_possessive?(part) = apostrophe_tail?(part.tail) && part.key.end_with?("s")

      def resolve_plurals(spans)
        bases = Set.new
        spans.each_value { |ps| ps.each { |p| bases << p.key if p.kind == "name" && !plural_possessive?(p) } }
        spans.each_value do |parts|
          parts.each_with_index do |p, i|
            next if p.kind != "name" || !plural_possessive?(p)

            [1, 2].each do |cut|
              base = p.key.length >= cut ? p.key[0...-cut] : ""
              next unless (cut == 1 || p.key.end_with?("es")) && bases.include?(base)

              parts[i] = p.dup.tap { |q| q.key = base; q.plural = true }
              break
            end
          end
        end
      end

      def acronym(name) = name.scan(Regexp.new("[A-Za-z][#{W}'-]*")).map { |w| w[0] }.join.upcase

      def luhn_digit(payload)
        total = 0
        payload.reverse.each_char.with_index do |ch, i|
          d = (ch.ord - 48) * (i.even? ? 2 : 1)
          total += d > 9 ? d - 9 : d
        end
        ((10 - (total % 10)) % 10).to_s
      end

      def fill_digits(original, digits)
        it = digits.each_char
        original.each_char.map do |c|
          next c unless c.match?(DIGIT)

          begin
            it.next
          rescue StopIteration
            raise RuntimeError, "generator raised StopIteration"
          end
        end.join
      end

      def card_plug(digits, variant)
        hit = CARD_PLUGS.find { |prefixes, length, _| length == digits.length && digits.start_with?(*prefixes) }
        base = hit&.last
        if base.nil?
          payload = "4" + ("1" * [digits.length - 2, 0].max)
          base = payload + luhn_digit(payload)
        end
        return base if variant.zero?

        payload = base[0...-1]
        tail4 = payload.length >= 4 ? payload[-4..] : payload
        last4 = (tail4.to_i - variant) % 10_000
        payload = payload[0...[payload.length - 4, 0].max] + format("%04d", last4)
        payload + luhn_digit(payload)
      end

      def days(year)
        leap = (year % 4).zero? && ((year % 100).nonzero? || (year % 400).zero?)
        [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
      end

      def shift_date(date, offset)
        m = DATE.match(date)
        return date if m.nil?

        first, sep, second, year_text = m.captures
        day_first = py_int(first) > 12
        month_text, day_text = day_first ? [second, first] : [first, second]
        year = py_int(year_text) + (year_text.length == 2 ? 2000 : 0)
        ds = days(year)
        month = py_int(month_text).clamp(1, 12)
        day = py_int(day_text).clamp(1, ds[month - 1])
        doy = ds[0...(month - 1)].sum + day
        doy = ((doy - 1 + offset) % ds.sum) + 1
        month = 1
        while doy > ds[month - 1]
          doy -= ds[month - 1]
          month += 1
        end
        new_month = month.to_s.rjust(month_text.length, "0")
        new_day = doy.to_s.rjust(day_text.length, "0")
        fields = day_first ? [new_day, new_month] : [new_month, new_day]
        date[0...m.begin(0)] + "#{fields[0]}#{sep}#{fields[1]}#{sep}#{year_text}" + date[m.end(0)..]
      end

      def effective_kind(kind, original)
        kind = NAME if kind == "PERSON"
        return kind if kind != NAME

        words = original.split(Regexp.new("[#{SPACE}]+")).reject(&:empty?).map { |w| rstrip_dots(fold(w)) }
        return kind if words.empty?
        return SCHOOL if words.any? { |w| SCHOOL_CUES.include?(w) } || SCHOOL_TAIL_CUES.include?(words[-1])
        return ORGANIZATION if words.any? { |w| ORG_CUES.include?(w) }

        kind
      end

      # A stand-in for every `[kind, original]` span of one document.
      def invent(spans, document:, seed: 0, stop_words: Set.new, given_name: nil, notable: nil,
                 identity: [])
        engine = Engine.new(document: document, seed: seed, stop_words: stop_words,
                            identity: identity, given_name: given_name, notable: notable)
        engine.invent_all(spans)
      end

      def invent_names(originals, **kwargs)
        result = invent(originals.map { |o| [NAME, o] }, **kwargs)
        originals.zip(result.stand_ins).to_h
      end

      # Put originals back into text carrying adlib stand-ins (a model reply).
      def restore_adlib(text, restore_map, token_map = nil)
        table = {}
        [restore_map, token_map || {}].each do |mapping|
          mapping.each { |key, original| table[fold(key)] = original if !key.empty? && !table.key?(fold(key)) }
        end
        return text if table.empty?

        keys = table.keys.sort_by { |k| [-k.length, k] }
        pattern = Regexp.new("(?<![#{W}])(?:#{keys.map { |k| Regexp.escape(k) }.join('|')})(?![#{W}])",
                             Regexp::IGNORECASE)
        text.gsub(pattern) do |seen|
          original = table.fetch(fold(seen))
          letters = letters_of(seen)
          if py_upper?(letters) && letters.length > 1 then original.upcase
          elsif py_lower?(letters) then original.downcase
          else original
          end
        end
      end
    end

    # One document's adlib state: the taken words, the maps, the used sets.
    class Engine
      attr_accessor :dob_offset
      attr_reader :tokens

      def initialize(document:, seed:, stop_words:, identity:, given_name:, notable:)
        @seed = seed.to_s
        @digest = Digest::SHA256.hexdigest(document.encode("UTF-8"))
        a = Adlib
        identity_words = identity.flat_map { |v| (v || "").scan(LETTERS).map { |w| a.fold(w) } }
        @taken = Set.new(document.scan(LETTERS).map { |w| a.fold(w) }) | stop_words.to_set | identity_words
        @given_name = given_name
        @notable = notable
        @names = {}
        @prose_names = {}
        @used_names = Set.new
        @stems = {}
        @used_stems = {}
        @tokens = {}
        @dob_offset = 1
      end

      def draw(purpose, key, bump, index, n) = Adlib.draw(@seed, @digest, purpose, key, bump, index, n)

      def a = Adlib

      def invent_names(originals)
        spans = {}
        originals.each { |o| spans[o] ||= a.split_span(o) }
        a.resolve_plurals(spans)
        roles = a.roles(spans.values, @given_name)
        order = []
        surface = {}
        spans.each_value do |parts|
          parts.each do |p|
            next unless p.kind == "name" && !a.initial?(p.key) && !surface.key?(p.key)

            order << p.key
            surface[p.key] = p.text
          end
        end
        order.each { |key| roles[key] ||= GIVEN }
        pools = { GIVEN => a.given_pool, SURNAME => a.surname_pool }
        bumps = {}
        assign = lambda do
          used = Set.new
          assigned = {}
          order.each do |key|
            pool = pools[roles[key]]
            start = Adlib.draw(@seed, @digest, roles[key], key, bumps.fetch(key, 0), 0, pool.length)
            hit = (0...pool.length).find do |step|
              folded = a.fold(pool[(start + step) % pool.length])
              !@taken.include?(folded) && !used.include?(folded)
            end
            raise AdlibPoolExhausted, "no unused #{roles[key]} stand-in left for this document" if hit.nil?

            assigned[key] = pool[(start + hit) % pool.length]
            used << a.fold(assigned[key])
          end
          assigned
        end
        assigned = assign.call
        a.surname_pool.length.times do
          break if @notable.nil?

          clash = nil
          spans.each_value do |parts|
            keys = parts.select { |p| p.kind == "name" && assigned.key?(p.key) }.map(&:key)
            if keys.length > 1 && @notable.call(keys.map { |k| assigned[k] }.join(" "))
              clash = keys[-1]
              break
            end
          end
          break if clash.nil?

          bumps[clash] = bumps.fetch(clash, 0) + 1
          assigned = assign.call
        end
        @names = assigned.dup
        @prose_names = assigned.dup
        @used_names = Set.new(assigned.values.map { |v| a.fold(v) })
        assigned.each { |key, stand_in| @tokens[stand_in] = surface[key] unless @tokens.key?(stand_in) }

        initial_keys = spans.values.flatten.select { |p| p.kind == "name" && a.initial?(p.key) }.map(&:key)
        initials = a.initials(@seed, @digest, initial_keys, Set.new(initial_keys.map { |k| a.rstrip_dots(k) }))
        spans.to_h do |original, parts|
          rendered = parts.map do |p|
            next p.text if p.kind != "name"

            if a.initial?(p.key)
              letter = initials[a.rstrip_dots(p.key)]
              target = a.rstrip_dots(p.text)[-1]
              core = p.text.sub(target) { a.cased(letter.upcase, p.text) }
              p.lead + core + a.possessive_tail(p.tail, core)
            elsif p.plural
              p.lead + a.cased(a.plural(assigned[p.key]), p.text) + p.tail
            else
              core = a.cased(assigned[p.key], p.text)
              p.lead + core + a.possessive_tail(p.tail, core)
            end
          end
          [original, rendered.join]
        end
      end

      def name_token(key, like)
        unless @names.key?(key)
          pool = a.given_pool
          start = draw(GIVEN, key, 0, 0, pool.length)
          hit = (0...pool.length).find do |step|
            folded = a.fold(pool[(start + step) % pool.length])
            !@taken.include?(folded) && !@used_names.include?(folded)
          end
          raise AdlibPoolExhausted, "no unused given stand-in left for this document" if hit.nil?

          candidate = pool[(start + hit) % pool.length]
          @names[key] = candidate
          @used_names << a.fold(candidate)
          @tokens[candidate] = like unless @tokens.key?(candidate)
        end
        a.cased(@names[key], like)
      end

      def stem(pool_name, key, like)
        memo = [pool_name, key]
        unless @stems.key?(memo)
          pool = a.load_pool(pool_name)
          used = (@used_stems[pool_name] ||= Set.new)
          start = draw(pool_name, key, 0, 0, pool.length)
          hit = (0...pool.length).find do |step|
            candidate = pool[(start + step) % pool.length]
            words = Set.new(candidate.scan(LETTERS).map { |w| a.fold(w) })
            !used.include?(a.fold(candidate)) && !words.intersect?(@taken)
          end
          raise AdlibPoolExhausted, "no unused #{pool_name} stem left for this document" if hit.nil?

          candidate = pool[(start + hit) % pool.length]
          used << a.fold(candidate)
          @stems[memo] = candidate
          @tokens[candidate] = like unless @tokens.key?(candidate)
        end
        a.cased(@stems[memo], like)
      end

      def runs(text, pool_name, kept)
        out = []
        run = []
        pending = +""
        flush = lambda do
          unless run.empty?
            like = run.map { |r| r[1] }.join(" ")
            key = run.map { |r| a.fold(r[1]) }.join(" ")
            out << (run[0][0] + stem(pool_name, key, like) + run[-1][2])
            run.clear
          end
          out << pending
          pending = +""
        end
        a.py_split(text, WS_SPLIT).each do |chunk|
          next if chunk.empty?

          if chunk.match?(WS_ONLY)
            run.empty? ? out << chunk : pending << chunk
            next
          end
          lead, core, tail = EDGE.match(chunk).captures
          if (pos = core =~ POSSESSIVE)
            tail = core[pos..] + tail
            core = core[0...pos]
          end
          if !core.match?(LETTERS) || kept.call(a.rstrip_dots(a.fold(core)))
            flush.call
            out << chunk
            next
          end
          flush.call if !run.empty? && !run[-1][2].empty?
          pending = +""
          run << [lead, core, tail]
        end
        flush.call
        out.join
      end

      def scramble(original, bump)
        original.each_char.with_index.map do |c, i|
          if c.match?(DIGIT) then draw("scramble", original, bump, i, 10).to_s
          elsif c.between?("a", "z") && c.length == 1 && c.ord < 128 then (97 + draw("scramble", original, bump, i, 26)).chr
          elsif c.between?("A", "Z") && c.ord < 128 then (65 + draw("scramble", original, bump, i, 26)).chr
          else c
          end
        end.join
      end

      def labels(text)
        a.py_split(text, /([A-Za-z]+|\p{Nd}+)/).map do |piece|
          next "" if piece.empty?

          if a.py_digits?(piece)
            piece.length >= 3 ? scramble(piece, 0) : piece
          elsif piece.match?(/\A[A-Za-z]+\z/)
            key = a.fold(piece)
            if @names.key?(key) then a.cased(@names[key], piece)
            elsif !(joined = joined_names(key)).empty? then a.cased(joined, piece)
            elsif piece.length <= 2 then piece
            else name_token(key, piece)
            end
          else piece
          end
        end.join
      end

      def joined_names(key)
        (2...(key.length - 1)).each do |cut|
          return @names[key[0...cut]] + @names[key[cut..]] if @names.key?(key[0...cut]) && @names.key?(key[cut..])
        end
        ""
      end

      def email(original, bump)
        local, _, domain = original.rpartition("@")
        local = labels(local) + (bump.zero? ? "" : (bump + 1).to_s)
        unless PROVIDERS.include?(a.fold(domain))
          tld = domain.split(".", -1)[-1] || ""
          domain = "example." + (%w[com net org].include?(tld.downcase) ? tld : "org")
        end
        "#{local}@#{domain}"
      end

      URL = %r{\A((?:[a-z][a-z0-9+.-]*://)?)([^/?#]*)([^?#]*)(\?[^#]*)?(#.*)?\z}im
      HOST = /\A((?:www|m|mobile)\.)?(.*)\z/im

      def url(original, bump)
        scheme, host, path, query, fragment = URL.match(original).captures.map { |g| g || "" }
        hm = HOST.match(host)
        lead = hm[1] || ""
        bare = hm[2]
        changed = url_shape(bare, path, query)
        if changed.nil?
          label, dot, suffix = bare.partition(".")
          if !dot.empty? && URL_USER_SUBDOMAINS.include?(a.fold(suffix)) && !label.empty? &&
             !URL_RESERVED.include?(a.fold(label)) && a.fold(label) != "www"
            changed = [labels(label) + dot + suffix, path, query]
          end
        end
        if changed.nil?
          stem_, dot, tld = bare.rpartition(".")
          bare = mapped_words(stem_) + dot + tld unless dot.empty?
          path, query, fragment = [path, query, fragment].map { |t| mapped_words(t) }
        else
          bare, path, query = changed
        end
        scheme + lead + bare + path + query + fragment + (bump.zero? ? "" : (bump + 1).to_s)
      end

      def mapped_words(text)
        a.py_split(text, /([A-Za-z]+)/).map do |piece|
          key = a.fold(piece)
          next a.cased(@prose_names[key], piece) if key.length >= 3 && @prose_names.key?(key)

          cut = (3...(key.length - 2)).find { |c| @prose_names.key?(key[0...c]) && @prose_names.key?(key[c..]) }
          if cut
            a.cased(@prose_names[key[0...cut]], piece[0...cut].to_s) +
              a.cased(@prose_names[key[cut..]], piece[cut..].to_s)
          else
            piece
          end
        end.join
      end

      def url_shape(host, path, query)
        folded_host = a.fold(host)
        segments = a.py_split(path, "/")
        URL_SHAPES.each do |shape_host, pattern|
          next if shape_host != folded_host

          path_pattern, _, query_pattern = pattern.partition("?")
          wanted = a.py_split(path_pattern, "/")
          next if segments.length < wanted.length || (!query_pattern.empty? && segments.length != wanted.length)

          out = segments.dup
          matched = wanted.each_with_index.all? do |want, at|
            seg = segments[at]
            if ["{h}", "{i}", "@{h}"].include?(want)
              body = want == "@{h}" ? seg[1..].to_s : seg
              next false if (want == "@{h}" && !seg.start_with?("@")) || body.empty? ||
                            URL_RESERVED.include?(a.fold(body))

              body = want == "{i}" ? scramble(body, 0) : labels(body)
              out[at] = (want == "@{h}" ? "@" : "") + body
              true
            else
              a.fold(seg) == want
            end
          end
          next unless matched

          q = query
          unless query_pattern.empty?
            key = query_pattern.partition("=")[0]
            pairs = q.empty? ? [] : a.py_split(q[1..], /([&;])/)
            hit = false
            pairs.each_with_index do |pair, n|
              name, eq, value = pair.partition("=")
              next unless !eq.empty? && a.fold(name) == key && !value.empty?

              pairs[n] = name + eq + scramble(value, 0)
              hit = true
            end
            next unless hit

            q = "?" + pairs.join
          end
          return [host, out.join("/"), q]
        end
        nil
      end

      def username(original, bump) = "@" + labels(original[1..].to_s) + (bump.zero? ? "" : (bump + 1).to_s)

      def address(original, bump)
        unit = UNIT.match(original)
        head = unit ? original[0...unit.begin(0)] : original
        if (number = /\A\p{Nd}+/.match(head))
          head = house_number(number[0], bump) + head[number.end(0)..]
        end
        head = runs(head, "street", ->(w) { DIRECTIONS.include?(w) || STREET_SUFFIXES.include?(w) })
        head += unit[1] + scramble(unit[2], bump) + unit[3] if unit
        head
      end

      def house_number(digits, bump)
        ([(1 + draw("address-number", digits, bump, 0, 9)).to_s] +
          (1...digits.length).map { |i| draw("address-number", digits, bump, i, 10).to_s }).join
      end

      def age(original, used, originals)
        value = a.py_int(original)
        lo, hi = AGE_BANDS.find { |l, h| l <= value && value <= h } || [0, 99]
        pool = (lo..hi).reject { |v| v == value }
        free = pool.reject { |v| originals.include?(v.to_s) }
        free = pool if free.empty?
        start = draw("age", original, 0, 0, free.length)
        walk = (0...free.length).map { |s| free[(start + s) % free.length] } + pool.reject { |v| free.include?(v) }
        candidate = walk.map(&:to_s).find { |s| !used.include?(s) } || free[start].to_s
        original.length > candidate.length ? candidate.rjust(original.length, "0") : candidate
      end

      def plug(kind, original, variant)
        case kind
        when "US_SOCIAL_SECURITY_NUMBER"
          return a.fill_digits(original, "79999#{format('%04d', (9999 - variant) % 10_000)}")
        when "ZIP_CODE"
          return a.fill_digits(original, format("%05d", (99_999 - variant) % 100_000) + "9999")
        when "IP_ADDRESS"
          return IP_RANGES[(variant / 254) % 3] + ((variant % 254) + 1).to_s
        when "CREDIT_DEBIT_CARD_NUMBER"
          return a.fill_digits(original, a.card_plug(original.gsub(/\P{Nd}/, ""), variant))
        end
        ext = Regexp.new("[#{SPACE}]*(?:x|ext\\.?|extension)[#{SPACE}]*\\p{Nd}{1,6}#{PY_END}",
                         Regexp::IGNORECASE).match(original)
        main = ext ? original[0...ext.begin(0)] : original
        number = main.gsub(/\P{Nd}/, "").chars
        plug7 = "55501#{format('%02d', variant % 100)}".chars
        number = number.length >= 7 ? number[0...-7] + plug7 : plug7
        number[-10...-7] = PHONE_AREA_CODE.chars if number.length >= 10
        a.fill_digits(main, number.join) + (ext ? ext[0] : "")
      end

      def render(kind, original, bump, memo, ages)
        return runs(original, RUN_POOLS[kind], ->(w) { DESCRIPTIVE.include?(w) }) if RUN_POOLS.key?(kind)

        case kind
        when "EMAIL" then email(original, bump)
        when "URL" then url(original, bump)
        when "USERNAME" then username(original, bump)
        when "ADDRESS" then address(original, bump)
        when "DATE_OF_BIRTH" then a.shift_date(original, @dob_offset)
        else
          if kind == "AGE" && a.py_digits?(original)
            used = Set.new(memo.select { |(k, _), _| k == "AGE" }.values)
            age(original, used, ages)
          elsif PLUG_KINDS.include?(kind) then plug(kind, original, bump)
          else scramble(original, bump)
          end
        end
      end

      def invent_all(spans)
        kinds = spans.map { |kind, original| a.effective_kind(kind, original) }
        pairs = spans.zip(kinds)
        names = invent_names(pairs.select { |_, k| k == NAME }.map { |(_, o), _| o })

        dob_dates = Set.new
        pairs.each { |(_, o), k| (m = DATE.match(o)) && dob_dates << m[0] if k == "DATE_OF_BIRTH" }
        MAX_BUMPS.times do |bump|
          @dob_offset = 1 + draw("dob-offset", "", bump, 0, 364)
          break unless Set.new(dob_dates.map { |d| a.shift_date(d, @dob_offset) }).intersect?(dob_dates)
        end
        ages = Set.new(pairs.select { |_, k| k == "AGE" }.map { |(_, o), _| o })
        original_folds = Set.new(spans.map { |_, o| a.fold(o) })

        out = Array.new(spans.length, "")
        memo = {}
        owner = {}
        variants = {}
        acronyms = []
        pairs.each_with_index do |((_, original), kind), index|
          if kind == NAME
            out[index] = names[original]
            next
          end
          if memo.key?([kind, original])
            out[index] = memo[[kind, original]]
            next
          end
          if kind == SCHOOL && original.match?(ACRONYM)
            acronyms << index
            next
          end
          start = PLUG_KINDS.include?(kind) ? variants.fetch(kind, 0) : 0
          own = a.fold(original)
          candidate = folded = nil
          bump = (start...(start + MAX_BUMPS)).find do |b|
            candidate = render(kind, original, b, memo, ages)
            folded = a.fold(candidate)
            next true if RUN_POOLS.key?(kind) || %w[DATE_OF_BIRTH AGE].include?(kind)
            next true if kind == "URL" && folded == own
            next false if original_folds.include?(folded) && folded != own
            next false if owner.fetch([kind, folded], own) != own
            next false if folded == own

            true
          end
          raise AdlibPoolExhausted, "no distinct #{kind} stand-in left for this document" if bump.nil?

          variants[kind] = bump + 1 if PLUG_KINDS.include?(kind)
          owner[[kind, folded]] = own
          memo[[kind, original]] = out[index] = candidate
        end

        schools = memo.select { |(k, _), _| k == SCHOOL }.map { |(_, o), v| [o, v] }
        acronyms.each do |index|
          original = spans[index][1]
          unless memo.key?([SCHOOL, original])
            hit = schools.find { |full, stand_in| a.acronym(full) == original && a.acronym(stand_in) != original }
            memo[[SCHOOL, original]] = hit ? a.acronym(hit[1]) : scramble(original, 0)
          end
          out[index] = memo[[SCHOOL, original]]
        end
        Invented.new(out, @tokens.dup)
      end
    end
  end
end

module Vicary
  RENDER_PLACEHOLDER = "placeholder"
  RENDER_ADLIB = "adlib"
  DEFAULT_RENDER = RENDER_PLACEHOLDER
  RENDER_ENV_VAR = "VICARY_RENDER"
  ADLIB_SEED_ENV_VAR = "VICARY_ADLIB_SEED"
  RENDER_ALIASES = {
    RENDER_PLACEHOLDER => Set.new(%w[placeholder placeholders default]),
    RENDER_ADLIB => Set.new(%w[adlib invented names])
  }.freeze

  # Kinds whose trailing bare apostrophe is a possessive adlib must re-form.
  ADLIB_POSSESSIVE_KINDS = Set.new(%w[NAME PERSON SCHOOL ORGANIZATION LOCATION]).freeze

  # A placeholder pass rendered as adlib. `restore_map` is `{stand-in: original}`,
  # `adlib_map` its inverse, `adlib_spans` `[[RedactionSpan, original], ...]`,
  # `adlib_tokens` `{invented token: original token}`. The maps are the
  # de-identification key: never log them.
  AdlibResult = Struct.new(:text, :restore_map, :adlib_map, :adlib_spans, :adlib_tokens,
                           keyword_init: true) do
    def spans = adlib_spans.map(&:first)

    def original
      out = +""
      prev = 0
      adlib_spans.each do |span, source|
        out << text[prev...span.new_start] << source
        prev = span.new_end
      end
      out << text[prev..].to_s
    end

    def inspect = "#<Vicary::AdlibResult text=#{text.inspect}>"
    alias_method :to_s, :inspect
  end

  class << self
    # How redacted spans are written: call-site value, then `VICARY_RENDER`,
    # then placeholders. An unrecognised value falls back to placeholders.
    def render_mode(value = nil)
      raw = (value || ENV[RENDER_ENV_VAR] || "").to_s.strip.downcase
      return DEFAULT_RENDER if raw.empty?

      RENDER_ALIASES.each { |mode, aliases| return mode if aliases.include?(raw) }
      warn "Unrecognised #{RENDER_ENV_VAR} value #{raw.inspect}; rendering #{DEFAULT_RENDER.inspect}."
      DEFAULT_RENDER
    end

    # The adlib seed: call-site value, then `VICARY_ADLIB_SEED`, then "0".
    def resolve_adlib_seed(value = nil)
      return value.to_s.strip if !value.nil? && !value.to_s.strip.empty?

      env = ENV.fetch(ADLIB_SEED_ENV_VAR, "").strip
      env.empty? ? "0" : env
    end

    # Rewrite a numbered placeholder result with invented values (see {Adlib}).
    # `original` is the text `masked` was redacted from. Raises ArgumentError when
    # `masked` carries placeholders `restore_map` cannot place.
    def render_adlib(masked, restore_map, original, seed: 0, identity: nil)
      spans = Spans.derive(masked, restore_map)
      if spans.empty?
        raise ArgumentError, "adlib needs a numbered restore map to render over" if masked.match?(Spans::PLACEHOLDER)

        return AdlibResult.new(text: masked, restore_map: restore_map.dup, adlib_map: {},
                               adlib_spans: [], adlib_tokens: {})
      end
      kinded = spans.map do |span|
        token = masked[span.new_start...span.new_end]
        [Placeholders.kind_of(token), restore_map.fetch(token)]
      end
      spans.each_with_index do |span, i|
        after = masked[span.new_end, 2].to_s
        next unless ADLIB_POSSESSIVE_KINDS.include?(kinded[i][0]) && ["'", "’"].include?(after[0, 1]) &&
                    !Adlib.py_alpha?(after[1, 1].to_s) && original[span.orig_end, 1] == after[0, 1]

        spans[i] = RedactionSpan.new(span.orig_start, span.orig_end + 1, span.new_start, span.new_end + 1)
        kinded[i] = [kinded[i][0], kinded[i][1] + after[0, 1]]
      end
      identity_values = []
      if identity
        extra = identity.respond_to?(:extra_names) ? identity.extra_names.to_a : []
        identity_values = [identity.first_name, identity.last_name, identity.school_name, *extra]
                          .reject { |v| v.nil? || v.empty? }
      end
      invented = Adlib.invent(
        kinded, document: original, seed: seed, stop_words: Lexicon.stop_words,
                given_name: ->(t) { Gazetteer.common_given_name?(t) },
                notable: ->(n) { Gazetteer.notable?(n) }, identity: identity_values
      )
      out = +""
      rendered = []
      restore = {}
      adlib_map = {}
      prev = 0
      spans.zip(kinded, invented.stand_ins).each do |span, (_, source), stand_in|
        out << masked[prev...span.new_start]
        new_start = out.length
        out << stand_in
        rendered << [RedactionSpan.new(span.orig_start, span.orig_end, new_start, new_start + stand_in.length),
                     source]
        restore[stand_in] = source unless restore.key?(stand_in)
        adlib_map[source] = stand_in unless adlib_map.key?(source)
        prev = span.new_end
      end
      out << masked[prev..].to_s
      AdlibResult.new(text: out, restore_map: restore, adlib_map: adlib_map, adlib_spans: rendered,
                      adlib_tokens: invented.tokens)
    end

    def restore_adlib(text, restore_map, token_map = nil) = Adlib.restore_adlib(text, restore_map, token_map)
  end
end
