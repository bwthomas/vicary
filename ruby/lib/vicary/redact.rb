# frozen_string_literal: true

require "set"

module Vicary
  # Retained, and no longer raised by anything.
  #
  # The reason it existed still holds: the alternative to an error is a silent
  # no-op redactor, which satisfies every caller and redacts nothing. Kept so a
  # future incomplete arm can raise it rather than inventing a new type.
  class NotPortedError < StandardError; end

  # Whether this build detects third-party names.
  #
  # Exported so a host can assert on it rather than infer it from a version
  # number. True since candidate generation landed; it is still meaningful,
  # because {NAMES_IDENTITY} turns the whole route back off at runtime.
  DETECTS_NAMES = true

  # Only the identity the caller handed over, plus the structured entities. No
  # gazetteer is loaded and no candidate is generated — 0% recall on third-party
  # names, which is a defensible choice only when it is a chosen one.
  NAMES_IDENTITY = "identity"
  # Generation plus the offline notability oracle: the shippable arm.
  NAMES_GAZETTEER = "gazetteer"
  # ...and the lowercase route, the only one that reaches a student who writes
  # without capitals.
  NAMES_LOWERCASE = "gazetteer-lowercase"

  # The default, matching the reference. Recall is what to buy inbound.
  DEFAULT_NAME_DETECTION = NAMES_LOWERCASE

  NAME_DETECTION_ENV_VAR = "VICARY_NAME_DETECTION"

  # Separator for a batched pass, byte-identical to the Python reference's
  # `BATCH_SEPARATOR`. Chosen to be something a writing-coach model will not
  # emit and a PII policy will not touch: no words, no digits, no name-shaped or
  # address-shaped substrings for an entity detector to bite on, and distinctive
  # enough that a stray single character cannot fake it. A round-trip check runs
  # anyway — see {redact_batch_with_report} — because a separator that "should"
  # survive is exactly the kind of assumption that silently mis-aligns fields.
  BATCH_SEPARATOR = "\n\u241E\u241E\u241E\n"

  IDENTITY_ALIASES = Set.new(%w[identity off none 0 false no]).freeze
  GAZETTEER_ALIASES = Set.new(%w[gazetteer on 1 true yes names]).freeze
  LOWERCASE_ALIASES = Set.new(%w[gazetteer-lowercase gazetteer_lowercase lowercase full max]).freeze

  # The redaction entry point — the whole detector, wired end to end.
  #
  # Two passes over one document, in this order and for this reason:
  #
  # 1. **The identity and structured pass** ({Vicary::Structured}) — the
  #    student's own name, school and school acronym, then every syntactic
  #    entity: email, URL, SSN, IP, phone, street address, date of birth,
  #    `@handle`, payment card behind a Luhn gate, ZIP and age. These are exact
  #    patterns, and they run first so that no looser match can consume part of
  #    one.
  # 2. **Candidate generation** ({Vicary::Candidates}) — the third-party names
  #    nothing hands over: the classmate, the teacher, the relative, the
  #    neighbour. High recall by construction, filtered by the offline notability
  #    oracle so the public figures a student writes *about* survive.
  #
  # Generation runs LAST, for the same reason it does in the reference: a broad
  # capitalised-word match run early would swallow the first token of an address
  # or the local part of an email, and a name half-eaten by another pattern leaks
  # the remainder.
  #
  # **One minter for the whole document.** Placeholder indices follow mint order
  # across both passes, so `{NAME_1}` means one person from the first line to the
  # last. Two minters would restart each counter and hand the same token to two
  # different people, which is the defect numbering exists to remove.
  #
  # The arm this reproduces is `local-gazetteer-lowercase` — generation, plus the
  # gazetteer notability oracle, plus the lowercase route. That is the arm the
  # conformance golden was produced by, and a port comparing against those bytes
  # while implementing a different arm is measuring two changes at once.
  class << self
    # Resolve how hard the detector looks for names it was not handed.
    #
    # Explicit argument, then `VICARY_NAME_DETECTION`, then the code default.
    #
    # An unrecognized non-empty value resolves to the **default**, not to
    # `identity`. Dropping silently to `identity` would leave redaction on and
    # reporting spans while finding none of the names a reader would call PII — a
    # failure that looks exactly like success from every log line and metric.
    def name_detection(value = nil)
      raw = (value || ENV[NAME_DETECTION_ENV_VAR] || "").strip.downcase
      return NAMES_IDENTITY if !raw.empty? && IDENTITY_ALIASES.include?(raw)
      return NAMES_GAZETTEER if GAZETTEER_ALIASES.include?(raw)
      return NAMES_LOWERCASE if LOWERCASE_ALIASES.include?(raw)

      DEFAULT_NAME_DETECTION
    end

    # Wire the bundled gazetteer into a detection level.
    #
    # Generation and the oracle are ONE decision, not two: generation alone masks
    # every public figure a student writes about, and the oracle alone has
    # nothing to judge. There is deliberately no supported way to ask for half of
    # it.
    #
    # At {NAMES_IDENTITY} this returns nothing and the 2.1 MB asset is never
    # touched. At the other two levels the first lookup pays the decompression;
    # call `Vicary::Gazetteer.load` at process start to move that off the first
    # request.
    def gazetteer_oracles(level)
      return { candidates: false } if level == NAMES_IDENTITY

      oracles = {
        candidates: true,
        notable: ->(name) { Gazetteer.notable?(name) },
        notability_tier: ->(name) { Gazetteer.notability(name) },
        title: ->(name) { Gazetteer.title?(name) },
        title_prefix: ->(key) { Gazetteer.title_prefix?(key) },
        # Wired at BOTH gazetteer levels, unlike `given_name` below. This one
        # decides a placeholder's type, not a verdict, so it has nothing to do
        # with which candidate routes are on.
        settlement: ->(name) { Gazetteer.settlement?(name) },
      }
      # The one difference between the two gazetteer levels. Absent rather than
      # nil, so `gazetteer` and `gazetteer-lowercase` differ by the presence of a
      # key rather than by a value the merge would have to strip.
      oracles[:given_name] = ->(token) { Gazetteer.common_given_name?(token) } if level == NAMES_LOWERCASE
      oracles
    end

    # Redact `text`, returning the masked bytes and everything needed to undo it.
    #
    # One minter for the whole document, because placeholder indices follow mint
    # order across every pass.
    #
    # Returns `[masked_text, n_masked, restore_map]`.
    def redact_with_report(text, identity, options = {})
      names = options[:names]
      keep = options[:keep] || Set.new
      number_placeholders = options.fetch(:number_placeholders, true)
      headings_are_orthographic = options.fetch(:headings_are_orthographic, true)
      corroborate = options.fetch(:corroborate, true)
      relation_refusal = options.fetch(:relation_refusal, true)
      title_relation_refusal = options.fetch(:title_relation_refusal, true)

      minter = PlaceholderMinter.new(number: number_placeholders)
      return [text, 0, minter.assigned] if text.nil? || text.empty?

      masked, n = Structured.mask(text, identity, minter)

      # Candidate generation runs LAST, so every exact pattern has already
      # claimed its span.
      oracles = gazetteer_oracles(name_detection(names))
      if oracles.delete(:candidates)
        masked, count = Candidates.mask_candidates(
          masked,
          oracles.merge(
            keep: keep,
            corroborate: corroborate,
            minter: minter,
            headings_are_orthographic: headings_are_orthographic,
            relation_refusal: relation_refusal,
            title_relation_refusal: title_relation_refusal,
          ),
        )
        n += count
      end

      [masked, n, minter.assigned]
    end

    # Split a joined document's restore map into one map per part.
    #
    # By OCCURRENCE, not by re-deriving: the placeholders are unique strings
    # over the joined document, so a part's map is exactly the entries whose
    # placeholder survived into that part. Re-running detection per part would
    # renumber, and renumbering is the one thing this must not do.
    def split_joined_restore_map(restore_map, parts)
      parts.map do |part|
        restore_map.select { |placeholder, _original| part.include?(placeholder) }
      end
    end

    # Redact several fields in ONE pass, and return the way back for each.
    #
    # Numbering is the JOINED document's, not each field's, so the same entity
    # carries the same placeholder across fields — which is the property that
    # lets a reader match a name in one field to the same name in another. A
    # caller must therefore not assume a field's map starts at `{NAME_1}`.
    #
    # **Why a host wants this.** Masking field by field is irreversible in the
    # way that matters: a caller holding only the masked strings cannot tell
    # which placeholder belongs to which field, nor what any of them stood for,
    # so it cannot put back the words its reader is already entitled to see.
    # It also renumbers, so one entity gets a different token per field.
    #
    # **Where it can go wrong, and what happens then.** The join/split round
    # trip is the risk: masking changes lengths, so offsets cannot be trusted,
    # and the split relies on {BATCH_SEPARATOR} surviving the pass intact. If
    # the masked document does not split back into exactly as many parts as
    # went in, this **falls back to per-field passes and says so**
    # (`batched == false`) rather than returning a mis-aligned list — one
    # field's suggestion pasted into another's is a worse outcome than a slower
    # call. Cross-field placeholder identity does not survive that fallback,
    # which is stated rather than papered over: a caller correlating names
    # across fields would otherwise be silently wrong on exactly those inputs.
    #
    # Returns `[masked_texts, n_masked, restore_maps, batched]`. `restore_maps`
    # is positionally aligned with `texts` — one map per field, empty for a
    # field that was empty or that nothing was masked in.
    def redact_batch_with_report(texts, identity, options = {})
      texts = texts.to_a
      return [[], 0, [], true] if texts.empty?

      empty_maps = texts.map { {} }
      nonempty = texts.each_index.reject { |i| texts[i].nil? || texts[i].empty? }
      return [texts.dup, 0, empty_maps, true] if nonempty.empty?

      if nonempty.length == 1
        only = nonempty.first
        masked, n, map = redact_with_report(texts[only], identity, options)
        out = texts.dup
        out[only] = masked
        maps = empty_maps.dup
        maps[only] = map
        return [out, n, maps, true]
      end

      joined = nonempty.map { |i| texts[i] }.join(BATCH_SEPARATOR)
      masked, n, map = redact_with_report(joined, identity, options)
      parts = masked.split(BATCH_SEPARATOR, -1)

      if parts.length != nonempty.length
        out = texts.dup
        maps = empty_maps.dup
        total = 0
        nonempty.each do |i|
          field_masked, field_n, field_map = redact_with_report(texts[i], identity, options)
          out[i] = field_masked
          maps[i] = field_map
          total += field_n
        end
        return [out, total, maps, false]
      end

      out = texts.dup
      maps = empty_maps.dup
      split = split_joined_restore_map(map, parts)
      nonempty.each_with_index do |i, k|
        out[i] = parts[k]
        maps[i] = split[k]
      end
      [out, n, maps, true]
    end

    # Redact personal names and structured PII from `text`.
    #
    # @param text [String] the composition to redact.
    # @param identity [Object] the student the detector is told about — anything
    #   answering `first_name`, `last_name` and `school_name`. Every reference arm
    #   interpolates these strings, so a caller that omits them is measuring a
    #   different system and misses the easiest spans in the fixture.
    # @param options [Hash] the defaults are the reference arm; every flag exists
    #   so its arm stays separately measurable.
    def redact(text, identity, options = {})
      redact_with_report(text, identity, options)[0]
    end
  end
end
