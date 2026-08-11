# frozen_string_literal: true

module Vicary
  # Hands out `{KIND_n}` placeholders, stable per distinct original.
  #
  # The Ruby port of `python/src/vicary/redaction.py`'s minter.
  #
  # Why numbering, stated as a measurement rather than a preference: a bare
  # `{NAME}` standing for every person in a document is **not reversible**. On 25
  # injected essays the unnumbered masker produced 37 not-restorable violations
  # and only 36% of essays round-tripped — one token meant `Marisol` in one
  # paragraph and `Terrence Okonkwo` in the next, so no map keyed on the token
  # can put either back.
  #
  # Two properties, and the second is the one that needs care:
  #
  # * **Injective** — distinct originals never share a placeholder, which is what
  #   makes restore well-defined.
  # * **Stable within a document** — the *same* original always gets the same
  #   index, so a name written five times masks to one placeholder rather than
  #   five. That matters beyond restorability: a scoring model reading
  #   `{NAME_1} argued … {NAME_1} concluded` can still see one person doing two
  #   things, where `{NAME_1} … {NAME_5}` reads as two strangers.
  #
  # Keyed on the exact original text, because restore must return the exact
  # bytes. `Terrence` and `Terrence's` are therefore different keys — correct but
  # unsatisfying, and the reason surname-folding does NOT belong here: folding
  # them together would make the mapping non-injective again.
  #
  # **Indices follow mint order, which is discovery order, not position in the
  # text.** One minter serves the whole document precisely so that holds;
  # per-pass minters would restart each counter and emit `{NAME_1}` twice for two
  # different people, which is the bug numbering exists to remove.
  class PlaceholderMinter
    # Off reproduces the unnumbered output byte for byte, so the two arms stay
    # separately measurable.
    attr_reader :number

    def initialize(number: true)
      @number = number
      # `[kind, original]` -> index. A tuple key rather than the joined string
      # the other two ports use: Ruby hashes take array keys directly, so there
      # is no separator to pick and no way for a name containing one to collide.
      # Insertion-ordered, which is what makes {#assigned} come back in discovery
      # order.
      @assigned = {}
      @high = Hash.new(0)
    end

    # The placeholder `original` should be replaced by.
    def mint(kind, original)
      return "{#{kind}}" unless @number

      key = [kind, original]
      index = @assigned[key]
      if index.nil?
        index = @high[kind] + 1
        @high[kind] = index
        @assigned[key] = index
      end
      "{#{kind}_#{index}}"
    end

    # Replace every match with a minted placeholder; returns the text and count.
    def substitute(kind, pattern, text)
      count = 0
      replaced = text.gsub(pattern) do |match|
        count += 1
        mint(kind, match)
      end
      [replaced, count]
    end

    # `{placeholder => original}` — the restore map, for free.
    #
    # Insertion-ordered, so the map reads in the order the document discovered
    # each span rather than in the order the placeholders sort.
    def assigned
      @assigned.keys.to_h { |kind, original| [mint(kind, original), original] }
    end
  end

  # Put the originals back.
  #
  # Longest placeholder first, so `{NAME_1}` cannot be partially consumed while
  # `{NAME_11}` is still pending.
  def self.restore(text, map)
    map.keys.sort_by { |k| -k.length }.reduce(text) do |out, placeholder|
      # Block form, not the two-argument one: a replacement *string* interprets
      # `\1`, `\&` and `\\`, so a restored name containing a backslash would come
      # back altered. The block returns the original bytes untouched.
      out.gsub(placeholder) { map[placeholder] }
    end
  end
end
