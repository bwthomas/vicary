# frozen_string_literal: true

# Candidate generation internals — the regexes, the capitalisation habit, the
# relation window, the corroboration guard.
#
# The counterpart of `typescript/test/candidates.test.ts`, case for case, and
# this port had none. `candidates.rb` is 1,702 lines, the most detector logic in
# any file here, and it was reached only through `Vicary.redact` — by the 54
# conformance frames, by the 37 shared primitive families, and by the two parity
# probes.
#
# That is real coverage of the wrong shape, and it was measured rather than
# assumed: of eleven deliberate mutations to `candidates.rb`, the frames caught
# one and the primitives caught seven. Three were inert. One — a regex
# end-anchor — escaped both.
#
# **Every expected value here is the one `typescript/test/candidates.test.ts`
# pins**, and that file's values were captured by running the Python reference.
# So agreement is transitive: Ruby matching these matches TypeScript matching
# Python. A value that differs is a real divergence in this port, not a dialect
# difference to paper over — the two places a genuine one is expected (Python's
# Unicode `\b`, Python's `str.strip`) are called out where they arise.
#
# The declared gap this closes is recorded in `conformance/coverage.json`, and
# `tools/tests/test_coverage_parity.py` fails if its entry outlives it.

require "minitest/autorun"
require "set"

require "vicary"

class CandidatesTest < Minitest::Test
  C = Vicary::Candidates

  # `[start, finish, matched]` for every match — the shape the reference probe
  # dumped, and the shape the TypeScript counterpart compares.
  def matches(pattern, text)
    C.each_match(text, pattern).map { |m| [m.begin(0), m.begin(0) + m[0].length, m[0]] }
  end

  # -------------------------------------------------------------------------
  # The stoplist, and what a stop word is
  # -------------------------------------------------------------------------

  def test_the_stoplist_is_the_shipped_421_words_not_a_transliteration
    assert_equal 421, C.stop_words.size
  end

  def test_a_clitic_is_stripped_before_the_stoplist_is_consulted
    # `[A-Z][A-Za-z'’]*` matches "I'm" as one token, so without stripping the
    # tail the stoplist never sees the word — "I'm" and "As" were the two most
    # common over-fires on real prose. Both apostrophes, because a word
    # processor curls them and half the corpus arrives that way.
    assert C.stop?("I'm")
    assert C.stop?("I’m")
    assert C.stop?("It's")
    assert C.stop?("he'd")
    # "Won" is not a stop word, so stripping the tail does not rescue it.
    refute C.stop?("Won't")
    refute C.stop?("Won’t")
  end

  def test_the_un_apostrophized_spellings_students_type_are_stoplisted_directly
    # There is no clitic boundary to find in "im", and "im" is a given name in
    # Wikidata — which is how "im faithfull" and "im going" became candidates.
    %w[im dont thats].each do |token|
      assert C.stop?(token), "#{token} should be a stop word"
    end
  end

  def test_a_bare_clitic_is_not_itself_a_stop_word
    # `word.length > clitic.length`: stripping "'s" off "'s" would leave
    # nothing, and an empty string is not a stoplist hit in any language.
    refute C.stop?("'s")
    refute C.stop?("n't")
    refute C.stop?("'")
  end

  def test_trailing_punctuation_and_case_do_not_hide_a_stop_word
    assert C.stop?("The")
    assert C.stop?("the")
    assert C.stop?("Mrs.")
    assert C.stop?("Mrs")
    assert C.stop?("A")
  end

  def test_honorifics_that_are_not_stoplisted_stay_available_as_name_heads
    # "Dr" is stoplisted so a bare "Dr." cannot become a candidate; "Coach" is
    # not, and the asymmetry is the reference's, pinned here so a port cannot
    # quietly normalise it away.
    assert C.stop?("Dr")
    refute C.stop?("Coach")
    refute C.stop?("Terrence")
    refute C.stop?("SLAM")
  end

  # -------------------------------------------------------------------------
  # Trimming a match into runs
  # -------------------------------------------------------------------------

  def test_an_interior_stop_word_splits_a_span_rather_than_trimming_its_edges
    # "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER" is one match, because in an
    # all-caps sentence every token is capitalised. The name is in the middle,
    # so edge-trimming would keep the whole shout.
    assert_equal [%w[Deshawn Pritchard]],
                 C.trim(%w[My Best Friend Deshawn Pritchard Would Never])
    assert_equal [%w[Coach Ruiz], %w[Marisol]], C.trim(%w[Coach Ruiz And Marisol])
  end

  def test_an_honorific_introducing_a_name_keeps_the_span_whole
    # Masking only the surname leaves the relationship and the surname's
    # position in the text, which is most of what a reader needed the name for.
    assert_equal [["Mrs.", "Okonkwo"]], C.trim(["Mrs.", "Okonkwo"])
  end

  def test_an_honorific_introducing_nothing_is_dropped
    assert_equal [], C.trim(["Mrs."])
    assert_equal [], C.trim(%w[Dr The])
  end

  def test_a_span_with_no_stop_words_survives_unsplit
    assert_equal [%w[Terrence Okonkwo]], C.trim(%w[Terrence Okonkwo])
  end

  # -------------------------------------------------------------------------
  # Typing the span
  # -------------------------------------------------------------------------

  def test_an_org_suffix_types_the_span_organization_with_no_oracle_wired
    assert_equal "ORGANIZATION", C.classify(["Acme", "Inc."])
    assert_equal "NAME", C.classify(%w[Terrence Okonkwo])
  end

  def test_without_a_settlement_oracle_every_non_organization_span_is_a_name
    # The behaviour before the tier existed, and the behaviour a caller that
    # wires no oracles still gets.
    assert_equal "NAME", C.classify(%w[Akron])
    assert_equal "NAME", C.classify(%w[Springfield Township])
  end

  def test_the_settlement_lookup_beats_the_org_suffix
    # Changed 2026-08-11: a lookup beats a guess, uniformly. The tier vouches
    # for the whole string on an exact match; a suffix is a guess from a word
    # ending. Both rows mask, so only the word a student reads outbound turns on
    # it — and of the 16 real tier entries carrying both, 12 are ordinary towns.
    known = Set.new(["akron", "acme inc.", "springfield township"])
    settlement = ->(name) { known.include?(name.downcase) }
    assert_equal "LOCATION", C.classify(["Acme", "Inc."], settlement)
    assert_equal "LOCATION", C.classify(%w[Akron], settlement)
    assert_equal "LOCATION", C.classify(%w[Springfield Township], settlement)
    # The case that actually occurs: no tier vouches, so the suffix types it.
    assert_equal "ORGANIZATION", C.classify(%w[Progressive Insurance], settlement)
  end

  def test_only_the_last_token_is_consulted_for_an_org_suffix
    assert_includes C::ORG_SUFFIXES, "school"
    assert_equal "ORGANIZATION", C.classify(%w[Westfield High School])
    assert_equal "NAME", C.classify(%w[School Of Rock])
  end

  def test_a_placeholder_is_typed_only_for_the_kinds_that_have_one
    assert_equal "{NAME}", C.placeholder_for("NAME")
    assert_equal "{ORGANIZATION}", C.placeholder_for("ORGANIZATION")
    assert_equal "{LOCATION}", C.placeholder_for("LOCATION")
  end

  # -------------------------------------------------------------------------
  # The token patterns
  # -------------------------------------------------------------------------

  def test_the_candidate_pattern_keeps_an_honorific_initials_hyphens_and_the_possessive
    assert_equal [[0, 12, "Mrs. Okonkwo"], [24, 31, "Dr Ruiz"]],
                 matches(C::CANDIDATE_RE, "Mrs. Okonkwo taught us. Dr Ruiz did not.")
    assert_equal [[0, 16, "J. R. R. Tolkien"], [38, 48, "T.S. Eliot"]],
                 matches(C::CANDIDATE_RE,
                         "J. R. R. Tolkien wrote it, and so did T.S. Eliot.")
    assert_equal [[0, 30, "Marguerite Delacroix-Whitfield"], [35, 42, "O'Brien"]],
                 matches(C::CANDIDATE_RE,
                         "Marguerite Delacroix-Whitfield and O'Brien were there.")
    # The possessive comes with the name rather than being left behind as a
    # fragment, curly apostrophe included.
    assert_equal [[0, 10, "Terrence's"], [26, 35, "Narciso's"], [48, 57, "Lincoln’s"]],
                 matches(C::CANDIDATE_RE,
                         "Terrence's older brother, Narciso's friend, and Lincoln’s hat.")
  end

  def test_a_lowercase_particle_stays_inside_the_name
    # Without this "Vincent van Gogh" generates two candidates and the
    # gazetteer has to know both halves.
    assert_equal [[0, 2, "My"], [16, 32, "Vincent van Gogh"]],
                 matches(C::CANDIDATE_RE,
                         "My inspiration, Vincent van Gogh, painted for years.")
  end

  def test_the_candidate_pattern_sees_the_word_inside_a_placeholder
    # Which is why PROTECTED exists. Not a defect to fix here: the bare word
    # inside the braces is capitalised, so generation produces it and the
    # protected-span pass is what removes it. Pinned so a port that "fixes" the
    # pattern instead diverges from the reference.
    text = "{NAME_1} met @PERSON2 and {LOCATION} last June."
    assert_equal [[1, 5, "NAME"], [14, 20, "PERSON"], [27, 35, "LOCATION"],
                  [42, 46, "June"]],
                 matches(C::CANDIDATE_RE, text)
    assert_equal [[0, 8, "{NAME_1}"], [13, 21, "@PERSON2"], [26, 36, "{LOCATION}"]],
                 matches(C::PROTECTED, text)
  end

  def test_the_lowercase_route_cannot_claim_the_tail_of_a_capitalised_word
    # There is no word boundary between the "T" and the "errence" of
    # "Terrence", so the capitalised route keeps exclusive claim on anything it
    # can see.
    assert_equal [[17, 21, "came"], [22, 26, "over"]],
                 matches(C::LOWER_TOKEN, "Terrence Okonkwo came over")
  end

  def test_an_accented_letter_is_a_word_character_as_it_is_in_the_reference
    # The divergence the parity sweep caught in the TypeScript port, kept here
    # as the same regression test. JavaScript's `\b` is ASCII-only, so a
    # transliterated `\b[a-z]` finds a boundary inside "naïve" and emits "ve" —
    # a lowercase token the reference never produces. Ruby's `\p{L}` lookbehind
    # agrees with Python: "na" and stop, because `ï` is a word character.
    assert_equal [[0, 2, "na"], [6, 9, "caf"], [17, 21, "went"], [22, 26, "home"],
                  [28, 29, "i"], [30, 33, "did"], [34, 37, "too"]],
                 matches(C::LOWER_TOKEN, "naïve café Renée went home. i did too.")
  end

  def test_the_word_patterns_differ_only_in_the_case_they_admit
    text = "Then SLAM! the door closed and Marisol laughed."
    assert_equal matches(C::WORD_TOKEN, text), matches(C::ANY_TOKEN, text)
    assert_equal %w[Then SLAM the door closed and Marisol laughed],
                 matches(C::WORD_TOKEN, text).map { |(_, _, token)| token }
  end

  # -------------------------------------------------------------------------
  # Sentence starts
  # -------------------------------------------------------------------------

  def test_a_sentence_begins_at_the_start_of_the_text
    assert_equal [0], C.sentence_starts("My cousin Terrence Okonkwo came over.").to_a.sort
  end

  def test_an_opening_quote_begins_a_sentence_so_its_capital_is_orthographic
    # Quoted material is how feedback refers to a student's own words, and
    # "vivid words like 'Giggles filled the school'" put a capital on `Giggles`
    # for the same orthographic reason a full stop does. It masked as a name in
    # text a student reads.
    starts = C.sentence_starts('vivid words like "Giggles filled the school" stand out')
    assert_equal [0, 18], starts.to_a.sort
  end

  def test_a_line_break_begins_a_sentence_and_terminal_punctuation_does_too
    starts = C.sentence_starts("One line.\n\nAnother line.\nA third.")
    assert_equal [0, 11, 25], starts.to_a.sort
  end

  def test_an_apostrophe_inside_a_word_is_not_an_opening_quote
    # The quote must not be preceded by a letter, so "don't" and "Narciso's"
    # are untouched — otherwise every possessive would start a sentence.
    assert_equal [0], C.sentence_starts("I don't think Narciso's cat minds").to_a.sort
  end

  # -------------------------------------------------------------------------
  # Emphasis
  # -------------------------------------------------------------------------

  def test_a_short_all_caps_run_is_emphasis
    # Where "SLAM", "WHACK", "LAUGHTER" and "REDACT" came from on real student
    # writing: the informal register's italics.
    assert_equal [[5, 9]],
                 C.emphasis_spans("Then SLAM! the door closed and Marisol laughed.")
  end

  def test_a_run_of_allcaps_run_words_or_more_is_not_emphasis
    # A long all-caps run is a writer who has stopped using case at all, and
    # the stoplist carries the whole decision there.
    assert_equal 3, C::ALLCAPS_RUN
    assert_equal [], C.emphasis_spans("MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER.")
    assert_equal [], C.emphasis_spans("THIS IS BAD")
    assert_equal [[0, 7]], C.emphasis_spans("THIS IS")
  end

  def test_single_character_tokens_are_not_a_shout
    # "I" is upper-case for every writer, and the initials in "J. R. Tolkien"
    # are part of a name rather than a shout.
    assert_equal [], C.emphasis_spans("J. R. R. Tolkien wrote it, and so did T.S. Eliot.")
    assert_equal [], C.emphasis_spans("I went home")
  end

  # -------------------------------------------------------------------------
  # Headings
  # -------------------------------------------------------------------------

  def test_a_short_unpunctuated_line_after_a_blank_line_is_a_heading
    text = "Horses\n\nThe first horses were small.\n\nHorse Families\n\nThey live in herds."
    assert_equal [[0, 6], [38, 52]], C.heading_spans(text)
  end

  def test_the_blank_line_is_what_separates_a_heading_from_a_wrapped_line
    # Body prose here is hard-wrapped, so "The INternet as we know it today
    # first" is a short unpunctuated line too. First-in-document counts as
    # preceded by a blank, which is why this one is still read as a heading —
    # and why the second line, mid-paragraph, is not.
    text = "The INternet as we know it today first\nappeared in a lab."
    assert_equal [[0, 38]], C.heading_spans(text)
  end

  def test_a_sentence_is_not_a_heading_however_short
    assert_equal [], C.heading_spans("My cousin Terrence Okonkwo came over.")
    assert_equal [], C.heading_spans("Short.")
    assert_equal [], C.heading_spans("Short!")
    assert_equal [], C.heading_spans("Short?")
  end

  def test_a_line_at_or_over_the_length_limit_is_prose
    assert_equal [], C.heading_spans("x" * C::HEADING_MAX_CHARS)
    assert_equal [[0, C::HEADING_MAX_CHARS - 1]],
                 C.heading_spans("x" * (C::HEADING_MAX_CHARS - 1))
  end

  def test_overlap_is_half_open_on_both_ends
    spans = [[5, 9]]
    refute C.overlaps?(spans, 0, 5)
    refute C.overlaps?(spans, 9, 12)
    assert C.overlaps?(spans, 4, 6)
    assert C.overlaps?(spans, 8, 12)
    refute C.overlaps?([], 0, 100)
  end

  # -------------------------------------------------------------------------
  # The title scan
  # -------------------------------------------------------------------------

  TITLES = ["to kill a mockingbird", "the lion king", "charlotte's web", "the lion"].freeze

  def is_title
    ->(text) { TITLES.include?(text.downcase.gsub("’", "'")) }
  end

  def is_prefix
    ->(key) { TITLES.any? { |title| title == key || title.start_with?("#{key} ") } }
  end

  def test_a_title_is_claimed_whole_across_the_stop_word_that_would_split_it
    # The whole reason this runs before generation: "To Kill a Mockingbird"
    # splits on the stoplisted "a" and comes back as "To {NAME} a {NAME}", which
    # no lookup on either half can undo.
    assert_equal [[7, 28]],
                 C.find_title_spans("I read To Kill a Mockingbird last year.",
                                    is_title, is_prefix)
  end

  def test_the_longest_title_wins_and_the_scan_resumes_after_it
    # "The Lion" is also a title here, so a shortest-match scan would claim it
    # and leave "King" to generation.
    assert_equal [[0, 13]],
                 C.find_title_spans("The Lion King is my favourite film.",
                                    is_title, is_prefix)
  end

  def test_a_curly_apostrophe_is_folded_before_the_prefix_walk
    # A word processor turns every apostrophe curly, so "Charlotte’s Web"
    # tokenises with a character the gazetteer's keys never contain and the walk
    # would stop on its first token.
    assert_equal [[8, 23]],
                 C.find_title_spans("We read Charlotte’s Web in class.", is_title, is_prefix)
  end

  def test_the_prefix_walk_and_the_exhaustive_scan_agree
    # `is_prefix` is a cost optimisation, not a semantic one. If the two
    # disagree the optimisation is a behaviour change wearing a performance
    # costume.
    ["I read To Kill a Mockingbird last year.",
     "The Lion King is my favourite film.",
     "We read Charlotte’s Web in class.",
     "Nothing here matches any title at all."].each do |text|
      assert_equal C.find_title_spans(text, is_title, is_prefix),
                   C.find_title_spans(text, is_title), text
    end
  end

  def test_requires_capital_skips_a_lowercase_title_head
    # In a document that capitalises its proper nouns, a title's first word is
    # capitalised too. Documents that do NOT capitalise are scanned at every
    # position, because there the case carries nothing.
    lower = "i read to kill a mockingbird last year."
    assert_equal [[7, 28]], C.find_title_spans(lower, is_title, is_prefix)
    assert_equal [], C.find_title_spans(lower, is_title, is_prefix, requires_capital: true)
  end

  def test_a_single_token_title_is_never_matched
    # "It" and "Up" must not make ordinary words permanently notable.
    single = ->(text) { text.downcase == "it" }
    assert_equal [], C.find_title_spans("It was a dark night.", single)
  end

  def test_the_token_limit_is_a_named_limit_not_an_oversight
    # The tier's longest entry is 36 tokens, but scanning that far costs 36
    # lookups per token position for titles nobody writes in an essay. 8 covers
    # "To Kill a Mockingbird"; "The Curious Incident of the Dog in the
    # Night-Time" is 10.
    assert_equal 8, C::TITLE_MAX_TOKENS
    nine = "a b c d e f g h i"
    assert_equal [], C.find_title_spans(nine, ->(text) { text == nine })
    eight = "a b c d e f g h"
    assert_equal [[0, 15]], C.find_title_spans(eight, ->(text) { text == eight })
  end

  # -------------------------------------------------------------------------
  # The constants the later pieces read
  # -------------------------------------------------------------------------

  def test_the_determiner_list_is_the_structural_signal_it_claims_to_be
    # English does not put a bare determiner in front of a person's given name,
    # so this does not grow with the corpus. Possessives are in it: a student
    # writes "my cousin terrence", never "my terrence".
    assert_equal 35, C::DETERMINERS.size
    %w[a an the my their enough].each do |word|
      assert_includes C::DETERMINERS, word, "#{word} should be a determiner"
    end
    refute_includes C::DETERMINERS, ""
  end

  def test_the_lowercase_routes_floor_is_two_tokens
    # The single decision that makes that route affordable.
    assert_equal 2, C::LOWERCASE_MIN_TOKENS
  end

  # -------------------------------------------------------------------------
  # How this writer uses capital letters
  # -------------------------------------------------------------------------

  def test_a_writer_who_marks_proper_nouns_and_keeps_sentence_capitals_is_consistent
    text = "We drove to Akron in July. My cousin Terrence met us there. " \
           "Later Marisol showed up with Deshawn and we all went to Ohio."
    assert_equal C::CONSISTENT, C.capitalisation_habit(text)
  end

  def test_a_writer_who_marks_nothing_and_drops_openings_is_lowercase
    assert_equal C::LOWERCASE,
                 C.capitalisation_habit(
                   "then terrence okonkwo showed up. i was so happy. we went home.",
                 )
  end

  def test_a_writer_who_does_both_is_inconsistent
    # Which is the cell the booleans had none for. Suppressing the lowercase
    # route loses the names they wrote lower-case; opening it wide fires on
    # ordinary words. So there is no document-level answer here — the band falls
    # through to per-token evidence.
    text = "My cousin Terrence came over. my aunt Marisol drove. " \
           "we went to Akron. then Deshawn showed up. i was tired."
    assert_equal C::INCONSISTENT, C.capitalisation_habit(text)
  end

  def test_a_document_that_says_nothing_either_way_is_silent
    # And silence is not consent. Reading it as consent is what put "line
    # circles" and "tone toward" in front of a student: a 108-290 character
    # feedback field is ordinary prose with nothing in it to capitalise.
    assert_equal C::SILENT,
                 C.capitalisation_habit(
                   "Nothing here is capitalised mid sentence at all. It is only prose.",
                 )
    assert_equal C::SILENT, C.capitalisation_habit("")
  end

  def test_a_bare_lower_case_i_alone_is_enough_to_read_the_writer_as_dropping_capitals
    # The higher-precision tell: 26 of the 27 un-scrubbed documents have none
    # at all, and the one that does has nine. So it stays a boolean on both
    # sides of the floor, with no rate to soften it.
    assert_equal C::INCONSISTENT,
                 C.capitalisation_habit(
                   "The Dog barked at Marisol. i ran. Then Terrence came over.",
                 )
  end

  def test_one_mid_sentence_capital_is_under_the_floor
    # 2 rather than 1 only to tolerate a single stray capital. Lower-cased,
    # every one of the 36 measured documents scores 0, so the separation is not
    # delicate.
    assert_equal 2, C::MARKS_PROPER_NOUNS_MIN
    assert_equal C::SILENT, C.capitalisation_habit("I met Marisol today. She was nice.")
    assert_equal C::CONSISTENT,
                 C.capitalisation_habit("I met Marisol today. I also saw Deshawn there.")
  end

  def test_the_rate_is_consulted_above_the_floor_and_not_below_it
    # And the same rate decides opposite ways. The asymmetry is the
    # measurement, not an oversight, and this pair is it: two documents one
    # percentage point apart in drop rate, treated differently because only one
    # of them has a presence signal to weigh the drop against.
    #
    # Above — 3 marks, 1 stylistic lower-case opening in 12 sentences (8.3%).
    # The rate says "a writer who typed one typo", which is
    # `marching-to-his-own-beat`, an NWP anchor paper marking 26 proper nouns
    # correctly that the boolean libelled.
    typo_capitaliser =
      "Marisol went to Akron. She met Deshawn. They saw Terrence. We drove home. " \
      "She waved. He smiled. They left. It rained. We slept. boy did we laugh. " \
      "She called again. He answered."
    assert_equal C::CONSISTENT, C.capitalisation_habit(typo_capitaliser)

    # Below — 0 marks, 1 lower-case opening in 11 sentences (9.1%). Nothing to
    # weigh it against, so the one bit is taken conservatively. Applying the
    # rate here cost a held-out name: in carrier essay 20739 it demoted a
    # genuine lower-case-writing document to `silent`, withdrew the permissive
    # path, and leaked "terrence okonkwo". Held-out recall 28/28 to 27/28.
    below_floor =
      "The dog barked. The cat ran. The bird flew. The fish swam. The cow mooed. " \
      "The pig oinked. The hen clucked. The duck quacked. The goat bleated. " \
      "The horse neighed. the sheep baaed."
    assert_equal C::LOWERCASE, C.capitalisation_habit(below_floor)
    assert_in_delta 0.1, C::DROPS_CAPITALS_MIN_RATE
  end

  def test_the_heading_exclusion_can_move_a_document_across_the_floor
    # A heading is title-cased, so every capital in it is orthographic.
    # Counting them let a heading vouch for its own words. Here it is the whole
    # difference between a document that marks proper nouns and one that says
    # nothing.
    text = "Horse Families\n\nThe first horses were small. They lived in herds.\n\n" \
           "Breeds I Like\n\nMy favourite is the Arabian."
    assert_equal C::SILENT, C.capitalisation_habit(text, C.heading_spans(text))
    assert_equal C::CONSISTENT, C.capitalisation_habit(text)
  end

  def test_the_two_predicates_cannot_contradict_each_other
    # Which is the point of the four states. `141-433` has two mid-sentence
    # capitals and six lower-case sentence openings, so under the old booleans
    # it was simultaneously a writer who capitalises and a writer who does not,
    # and whichever predicate a call site read decided the treatment.
    [[C::CONSISTENT, true, false],
     [C::INCONSISTENT, true, true],
     [C::LOWERCASE, false, true],
     [C::SILENT, false, false]].each do |habit, marks, drops|
      assert_equal marks, C.marks_proper_nouns?(habit), habit
      assert_equal drops, C.drops_capitals?(habit), habit
    end
  end

  def test_the_habit_state_is_the_string_the_references_enum_carries
    # So the two languages can be diffed on the wire without a mapping table in
    # between, which is where a fifth state would otherwise appear.
    assert_equal %w[consistent inconsistent lowercase silent],
                 [C::CONSISTENT, C::INCONSISTENT, C::LOWERCASE, C::SILENT]
  end

  # -------------------------------------------------------------------------
  # Per-token testimony
  # -------------------------------------------------------------------------

  def test_a_capital_the_writer_chose_is_testimony_one_orthography_forced_is_not
    # A writer who put a capital on "Cade" somewhere other than a sentence
    # start has told us "Cade" is a name in this document; one who only ever
    # writes "Eventually" after a full stop has told us nothing.
    #
    # "i" is in the answer, and it is unreachable rather than wrong. The
    # document-level counter matches `[A-Z][a-z]{2,}`, so a bare "I" cannot vote
    # on whether the writer marks proper nouns. This per-token channel has no
    # such filter — but its only consumer asks about the tokens of a candidate
    # *run*, and runs come from `trim`, which drops stop words. "I" is a stop
    # word and is not an honorific, so `trim(["I"])` is `[]`. Measured:
    # removing "i" from the set leaves all 51 golden frames byte-identical. A
    # port that "fixes" it diverges from the reference for no gain.
    text = "I met Marisol today. Eventually I also saw Deshawn there."
    assert_equal %w[deshawn i marisol],
                 C.mid_sentence_capitals(text, C.sentence_starts(text)).to_a.sort
    # The document-level counter sees the same two names and not the "I".
    assert_equal %w[Marisol Deshawn],
                 C.each_match(text, C::MID_SENTENCE_CAP).map { |m| m[1] }
  end

  def test_an_all_caps_token_cannot_corroborate_itself
    # Load-bearing rather than tidy: without the exclusion "SLAM" is its own
    # mid-sentence capital, so every emphasis shout would clear the bar the
    # emphasis rule had just raised.
    text = "Then SLAM! the door closed and Marisol laughed at Deshawn."
    assert_equal %w[deshawn marisol],
                 C.mid_sentence_capitals(text, C.sentence_starts(text)).to_a.sort
  end

  def test_a_headings_capitals_are_not_testimony_about_its_own_words
    # Counting them let "The First Horses" vouch for "Horses" as a name — the
    # heading corroborating itself, one line removed.
    text = "Horse Families\n\nThe first horses were small. They lived in herds.\n\n" \
           "Breeds I Like\n\nMy favourite is the Arabian."
    starts = C.sentence_starts(text)
    assert_equal %w[arabian],
                 C.mid_sentence_capitals(text, starts, C.heading_spans(text)).to_a.sort
    assert_equal %w[arabian families i like],
                 C.mid_sentence_capitals(text, starts).to_a.sort
  end

  def test_a_multi_token_span_carries_evidence_beyond_its_capitals
    # "Sadie Johnson" is a *shape*, which a single capitalised word is not.
    text = "Terrence Okonkwo came over that summer."
    starts = C.sentence_starts(text)
    assert C.capital_is_the_only_evidence?(%w[Terrence], 0, starts, [])
    refute C.capital_is_the_only_evidence?(%w[Terrence Okonkwo], 0, starts, [])
  end

  def test_inside_a_heading_a_multi_token_span_is_not_a_shape
    # Title case capitalises every word, so the second capital is as
    # orthographic as the first. "My Brother Terrence Okonkwo" as a heading
    # needs the given-name tier rather than its own capitals — the bar every
    # unevidenced capital clears.
    text = "The INternet as we know it today first\nappeared in a lab in Ohio."
    starts = C.sentence_starts(text)
    headings = C.heading_spans(text)
    assert_equal [[0, 38]], headings
    assert C.capital_is_the_only_evidence?(%w[Terrence Okonkwo], 0, starts, [], headings)
    refute C.capital_is_the_only_evidence?(%w[Terrence Okonkwo], 0, starts, [])
  end

  def test_a_capital_inside_an_emphasis_shout_is_not_the_writers_choice_either
    text = "Then SLAM! the door closed and Marisol laughed."
    starts = C.sentence_starts(text)
    emphasis = C.emphasis_spans(text)
    assert_equal [[5, 9]], emphasis
    assert C.capital_is_the_only_evidence?(%w[SLAM], 5, starts, emphasis)
    # Mid-sentence, outside the shout: a choice the writer made.
    refute C.capital_is_the_only_evidence?(%w[Marisol], 31, starts, emphasis)
  end

  # -------------------------------------------------------------------------
  # The sentence-initial corroboration guard
  #
  # The guard is ~98% precise on real prose: 133 occurrences over 101 distinct
  # spans suppressed, 99 of the 101 correctly. It is NOT the defect and must not
  # be "fixed" — the tier feeding it was, and that was addressed in 0.1.0.
  # -------------------------------------------------------------------------

  # The stand-in given-name tier the reference probe used.
  GIVEN = Set.new(%w[terrence marisol sadie deshawn cade]).freeze

  def is_given
    ->(name) { GIVEN.include?(name) }
  end

  def no_oracle
    ->(_name) { false }
  end

  def test_an_unevidenced_sentence_initial_capital_is_suppressed
    # The whole purpose. "Words" opens the sentence, so orthography put the
    # capital there; no tier knows it and the document never capitalises it
    # mid-sentence.
    text = "Words like 'Terrence' stand out in that chapter."
    starts = C.sentence_starts(text)
    written = C.mid_sentence_capitals(text, starts, C.heading_spans(text))
    assert_equal [], written.to_a
    assert C.suppressed_as_an_unevidenced_capital?(%w[Words], 0, starts, [], [],
                                                   written, is_given)
  end

  def test_both_channels_see_the_same_stripped_token_closing_quote_included
    # The candidate pattern treats `'` as a name character, so "words like
    # 'Terrence'" arrives as `Terrence'`. Stripping `.,'’` on BOTH channels is
    # what lets the tier recognise it; stripping only `.,` asks the tier about
    # `terrence'` and is told no. This case is what the reference's negative
    # control moved.
    text = "Words like 'Terrence' stand out in that chapter."
    starts = C.sentence_starts(text)
    written = C.mid_sentence_capitals(text, starts, C.heading_spans(text))
    assert C.corroborated?(["Terrence'"], written, is_given)
    # An opening quote counts as a sentence start, which is why this span
    # reaches the guard at all — and the corroboration is what keeps it.
    refute C.suppressed_as_an_unevidenced_capital?(["Terrence'"], 12, starts, [], [],
                                                   written, is_given)
  end

  def test_the_documents_own_mid_sentence_capital_vouches_for_a_later_sentence_start
    # Channel one, with no tier involvement: the writer capitalised "Cade" at
    # offset 6, where they had a lower-case alternative and declined it. That
    # testimony carries the sentence-initial "Cade" at 21.
    text = "I saw Cade at lunch. Cade never sits with anyone else."
    starts = C.sentence_starts(text)
    written = C.mid_sentence_capitals(text, starts, C.heading_spans(text))
    assert_equal %w[cade], written.to_a
    assert C.capital_is_the_only_evidence?(%w[Cade], 21, starts, [])
    refute C.suppressed_as_an_unevidenced_capital?(%w[Cade], 21, starts, [], [],
                                                   written, is_given)
  end

  def test_the_given_name_tier_vouches_on_its_own_with_no_capital_to_read
    # Channel two. The document capitalises only "Johnson" mid-sentence, so
    # "Sadie" at a sentence start has nothing but the tier — and that is enough.
    text = "Sadie came over. Later Johnson called. Nobody answered him."
    starts = C.sentence_starts(text)
    written = C.mid_sentence_capitals(text, starts, C.heading_spans(text))
    assert_equal %w[johnson], written.to_a.sort
    refute C.suppressed_as_an_unevidenced_capital?(%w[Sadie], 0, starts, [], [],
                                                   written, is_given)
  end

  def test_any_token_corroborates_not_just_the_first
    # The heading rule is what made this load-bearing. "My Brother Terrence
    # Okonkwo" in a heading is multi-token but title-cased, so it reaches the
    # guard; it leads with an honorific, so checking only the first token
    # consults "Brother" and leaks the name. "Terrence" is the third token.
    text = "\n\nMy Brother Terrence Okonkwo\n\nHe taught me how to ride a bike.\n"
    starts = C.sentence_starts(text)
    headings = C.heading_spans(text)
    written = C.mid_sentence_capitals(text, starts, headings)
    run = %w[Brother Terrence Okonkwo]
    assert C.capital_is_the_only_evidence?(run, 5, starts, [], headings)
    refute C.corroborated?(%w[Brother], written, is_given)
    assert C.corroborated?(run, written, is_given)
    refute C.suppressed_as_an_unevidenced_capital?(run, 5, starts, [], headings,
                                                   written, is_given)
  end

  def test_an_emphasis_shout_cannot_corroborate_itself
    # `mid_sentence_capitals` excludes an entirely upper-case token, so "SLAM"
    # is not its own testimony — without that exclusion every shout would clear
    # the bar the emphasis rule had just raised.
    text = "And then *SLAM* the door shut behind us."
    starts = C.sentence_starts(text)
    emphasis = C.emphasis_spans(text)
    written = C.mid_sentence_capitals(text, starts, C.heading_spans(text))
    assert_equal [], written.to_a
    assert C.suppressed_as_an_unevidenced_capital?(%w[SLAM], 10, starts, emphasis, [],
                                                   written, is_given)
  end

  def test_a_mid_sentence_multi_token_span_never_reaches_the_guard
    # Not suppressed because `capital_is_the_only_evidence?` is already false:
    # the shape is the evidence. The corroboration channels are irrelevant here,
    # and the guard must not consult them — an oracle-free caller has to behave
    # identically.
    text = "I asked Marisol Ybarra what she thought about the ending."
    starts = C.sentence_starts(text)
    written = C.mid_sentence_capitals(text, starts, C.heading_spans(text))
    refute C.capital_is_the_only_evidence?(%w[Marisol Ybarra], 8, starts, [])
    refute C.suppressed_as_an_unevidenced_capital?(%w[Marisol Ybarra], 8, starts, [], [],
                                                   written, no_oracle)
  end

  def test_no_tokens_corroborate_nothing
    refute C.corroborated?([], Set.new(%w[terrence]), is_given)
  end

  def test_the_documents_own_capital_vouches_for_its_own_possessive
    # Added 2026-08-11. The writer capitalised "Terrence" mid-sentence, which is
    # testimony about the name; the `'s` is not part of it. Before the fold,
    # channel one could not vouch for its own possessive — only the gazetteer's
    # possessive folding, on the OTHER channel, was hiding that.
    text = "I saw Terrence at lunch. Terrence's brother stayed home that day."
    starts = C.sentence_starts(text)
    written = C.mid_sentence_capitals(text, starts, C.heading_spans(text))
    assert_equal %w[terrence], written.to_a
    assert C.corroborated?(["Terrence's"], written, no_oracle)
    refute C.suppressed_as_an_unevidenced_capital?(["Terrence's"], 25, starts, [], [],
                                                   written, no_oracle)
  end

  def test_the_possessive_fold_takes_one_tail_and_only_a_real_one
    # It cannot invent corroboration for a word with no clitic, so the guard's
    # whole purpose survives the change.
    written = Set.new(%w[terrence])
    refute C.corroborated?(%w[Words], written, no_oracle)
    refute C.corroborated?(%w[Terrences], written, no_oracle)
    assert_equal "terrence", C.without_clitic("terrence's")
    assert_equal "terrence", C.without_clitic("terrence")
  end

  # -------------------------------------------------------------------------
  # The relation override
  #
  # The exhaustive half is `primitives_test.rb`, which runs all four predicates
  # over 45 span cases exported from the reference. These are the cases whose
  # *answer needs a reason*.
  # -------------------------------------------------------------------------

  def test_a_first_person_relation_must_be_attached_not_merely_nearby
    # The whole point of the strict sibling. Applied to the title tier, a window
    # scan refuses six of the seven curriculum characters it must keep, because
    # characters are described BY their relations.
    attached = "My neighbor Alice Adams walked me to the bus stop."
    nearby = "I read Harry Potter with my little brother over the holiday."
    assert C.names_someone_the_writer_knows?(attached, 12, 23)
    refute C.names_someone_the_writer_knows?(nearby, 7, 19)
    # ...and attachment without first person is equally not enough.
    described = "Atticus Finch, a father who taught me to look away."
    refute C.names_someone_the_writer_knows?(described, 0, 13)
  end

  def test_the_appositives_comma_is_the_rule_not_punctuation_taste
    # An appositive is punctuated and a prepositional phrase is not, and that is
    # the entire difference between naming a person and mentioning a relation.
    with_comma = "Alice Adams, my next-door neighbor, drove us there."
    without = "Alice Adams my neighbor drove us all the way there."
    assert C.names_someone_the_writer_knows?(with_comma, 0, 11)
    refute C.names_someone_the_writer_knows?(without, 0, 11)
  end

  def test_a_proximity_phrase_needs_a_first_person_to_say_whose_street_it_is
    ours = "Alice Adams, who lives two doors down from us, walked me home."
    theirs = "Alice Adams, who lives two doors down from the school, walked."
    assert C.names_someone_the_writer_knows?(ours, 0, 11)
    refute C.names_someone_the_writer_knows?(theirs, 0, 11)
  end

  def test_at_most_two_modifiers_may_sit_between_the_possessive_and_the_cue
    two = "My old soccer coach Deshawn Pritchard stayed after class."
    three = "My very old soccer coach Deshawn Pritchard stayed after class."
    assert C.names_someone_the_writer_knows?(two, 20, 37)
    refute C.names_someone_the_writer_knows?(three, 25, 42)
  end

  def test_the_modifier_patterns_lower_case_restriction_is_inert_and_that_is_fine
    # Recorded because the reference's comment claims otherwise: it says the
    # modifier class is "lower-case only, so a capitalised name cannot be
    # swallowed as a modifier". Every caller folds the window with `.downcase`
    # before matching, so by the time `[a-z]` is applied there are no capitals
    # left to exclude and the restriction can never fire.
    #
    # Measured in every language and identical in all — the comment is wrong,
    # not the behaviour, which is why this pins the behaviour rather than
    # "fixing" the pattern during a port.
    capital = "My Old soccer coach Deshawn Pritchard stayed after class."
    lower = "My old soccer coach Deshawn Pritchard stayed after class."
    assert C.names_someone_the_writer_knows?(capital, 20, 37)
    assert C.names_someone_the_writer_knows?(lower, 20, 37)
  end

  def test_a_relation_led_span_is_the_writers_relative_when_it_is_internally_mixed
    # The three rows of the docstring's table, as three assertions. Only the
    # middle one is what `relation_led_title_is_internally_mixed?` adds: the
    # name carries a capital and the relation word does not, so the writer used
    # capitals and chose not to put one on "cousin".
    film = "My Cousin Vinny is my favorite movie and I have seen it twice."
    relative = "My cousin Vinny came over that summer and never left."
    lower = "my cousin vinny is my favorite movie and i have seen it twice."
    refute C.relation_led_title_is_internally_mixed?(film, 0, 15)
    assert C.relation_led_title_is_internally_mixed?(relative, 0, 15)
    refute C.relation_led_title_is_internally_mixed?(lower, 0, 15)
    # The document-level sibling agrees about the film and the relative, and it
    # is the lower-case row where the two differ — there the absent capital is
    # not testimony about anything, so the document gate stays in charge.
    refute C.title_is_the_writers_own_relation?(film, 0, 15)
    assert C.title_is_the_writers_own_relation?(relative, 0, 15)
    assert C.title_is_the_writers_own_relation?(lower, 0, 15)
  end

  def test_the_window_reaches_one_clause_and_stops_at_a_sentence_end
    # A cue 80 characters after the span is inside the window; the same cue 99
    # characters after it is not. Both are pinned in the spec, because a port
    # with a different window passes every other case here.
    inside = "Alice Adams walked me to the bus stop every single morning of that " \
             "whole long cold winter, my cousin said later."
    outside = "Alice Adams walked me to the bus stop every single morning of that " \
              "whole long and very cold winter that year, my cousin said later."
    assert C.names_someone_in_the_writers_life?(inside, 0, 11)
    refute C.names_someone_in_the_writers_life?(outside, 0, 11)
    # Terminal punctuation ends the scan: the next sentence's cousin is not
    # this span's appositive.
    next_sentence = "Alice Adams walked to the bus stop. My cousin was there."
    refute C.names_someone_in_the_writers_life?(next_sentence, 0, 11)
  end

  def test_the_override_reaches_the_tiers_built_from_ordinary_names_not_place
    # A place is not a person, and a bare iconic surname has its own
    # document-level rule with its own guard.
    assert_equal %w[demonym full_name title], C::OVERRIDABLE_TIERS.to_a.sort
    refute_includes C::OVERRIDABLE_TIERS, "place"
    refute_includes C::OVERRIDABLE_TIERS, "iconic_short"
  end

  def test_pythons_word_boundary_not_javascripts_on_both_sides_of_a_cue
    # `\b` is Unicode-aware in Python and ASCII-only in JavaScript, so a
    # transliterated `\b` finds a boundary inside an accented word that the
    # reference never finds. Ruby's explicit `\p{L}` lookaround agrees with
    # Python. Both directions are pinned because both occur: the possessive is
    # preceded by a word, and the cue is followed by one.
    before = "naïmy cousin Terrence came over that summer and never left."
    after = "Alice Adams, my cousinä came over that summer and never left."
    refute C.names_someone_the_writer_knows?(before, 13, 21)
    refute C.names_someone_the_writer_knows?(after, 0, 11)
    # The ASCII-run control: "roomy" is one word in every language, so all
    # agree there and the case above is isolating the Unicode difference.
    ascii = "roomy cousin Terrence came over that summer and never left."
    refute C.names_someone_the_writer_knows?(ascii, 13, 21)
  end
end
