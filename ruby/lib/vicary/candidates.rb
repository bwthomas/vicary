# frozen_string_literal: true

require "set"

module Vicary
  # Find the person-names a student wrote, so the notability filter can decide.
  #
  # The Ruby port of `python/src/vicary/name_candidates.py`.
  #
  # Why generation runs before the notability lookup, rather than instead of it
  # ---------------------------------------------------------------------------
  # Finding capitalised name-shaped spans in English student prose is close to
  # free. The hard half is deciding which ones to *keep*, and the two cases look
  # identical syntactically:
  #
  #     My cousin Terrence Okonkwo came over that summer     => redact
  #     My inspiration, Vincent van Gogh, painted for years  => keep
  #
  # Both are first-person possessive, so a relational-trigger rule gets van Gogh
  # wrong. The discriminator has to be **notability**, which is a lookup rather
  # than a model. So: generate broadly here, then `notable => keep, everything
  # else => redact`.
  #
  # Capitalisation is a clue, never the answer
  # ------------------------------------------
  # Every rule in this file weighs case rather than obeying it, because a writer
  # who capitalises most of their proper nouns still misses some and informal
  # writers shout in ALL CAPS. Each threshold below was measured on 27
  # un-scrubbed student documents rather than argued from the shape of English;
  # the numbers travel with the constants.
  #
  # ## Regex dialect
  #
  # Ported from Python `re`. Two differences run through this whole file:
  #
  # * `^` and `$` are start- and end-of-*line* in Ruby, where Python without
  #   `re.MULTILINE` means the whole string. Every one of them is written `\A` or
  #   `\z` here. This is not cosmetic, and **neither shared spec layer catches it
  #   if somebody writes it back**: with a bare `$`, {RELATION_ATTACHED_BEFORE}
  #   attaches "my cousin" on one line to a name on the next, and all 36
  #   conformance frames and all 2,526 primitive assertions stay green while it
  #   does. `test/dialect_test.rb` is what catches it.
  # * `\w` is ASCII-only in Ruby and Unicode-aware in Python, which is why
  #   {NOT_WORD_BEFORE} spells its character class out rather than using `\w`.
  #   `\d` and `\s` diverge the same way and are deliberately left as-is,
  #   matching the TypeScript port, which has the identical narrowing and
  #   reproduces every frame.
  #
  # `\b` is NOT one of the differences, which is worth stating because the
  # TypeScript port's identical-looking lookarounds exist for a reason that does
  # not apply here: *JavaScript's* `\b` is ASCII-only and finds a boundary inside
  # `naïve` that Python does not. Ruby's `\b` is Unicode-aware and already agrees
  # with Python.
  module Candidates
    # Python's `\b` before a letter, written out.
    #
    # Belt-and-braces rather than load-bearing in Ruby — see the dialect note
    # above — and kept because this is the form the shared spec pins, and
    # `[\p{L}\p{N}_]` is the same set the gazetteer folds on.
    NOT_WORD_BEFORE = '(?<![\p{L}\p{N}_])'

    # The same on the trailing side: `\b` *after* a letter.
    NOT_WORD_AFTER = '(?![\p{L}\p{N}_])'

    # Role titles and honorifics that introduce a name. Part of the span: masking
    # "Okonkwo" out of "Mrs. Okonkwo" leaves the relationship and the surname's
    # position, and students name teachers and coaches constantly.
    HONORIFICS = %w[
      Mr Mrs Ms Miss Mx Dr Prof Professor Coach
      Officer Principal Rev Reverend Sgt Sergeant Capt
      Captain Sir Madam Fr Sister Brother Nurse Chief
      Aunt Uncle Grandma Grandpa Grandmother Grandfather
      Cousin Auntie
    ].freeze

    # Lowercase particles that sit *inside* a name. Without these, "Vincent van
    # Gogh" generates two candidates and the gazetteer has to know both halves.
    PARTICLES = %w[
      van von de del della der den di da du la
      le los bin ibn al of the y
    ].freeze

    # Suffixes that make a capitalised span an organisation rather than a person.
    # Typed separately because the placeholder is what a student reads outbound.
    ORG_SUFFIXES = Set.new(%w[
      inc inc. llc ltd corp corp. corporation company
      co co. insurance bank hospital clinic university
      college school academy institute foundation church
      temple mosque synagogue association society union
      department agency bureau committee council league
      team club store market restaurant airlines motors
      industries systems technologies group partners holdings
    ]).freeze

    # Suffixes that make a capitalised span a public landmark — topical by
    # construction, so kept without consulting the gazetteer. "Lincoln Memorial"
    # is the essay's subject; "Akron" in the same sentence is the student's town.
    LANDMARK_SUFFIXES = Set.new(%w[
      memorial monument museum cathedral capitol bridge
      tower stadium arena park gardens canyon falls
      island mountain mountains river lake ocean sea
      desert valley peninsula statue palace castle temple
      pyramid wall trail highway zoo aquarium planetarium
      observatory library
    ]).freeze

    # Capitalised words that are not names, read from the vendored lexicon.
    #
    # Deliberately broad: this list is the only thing standing between candidate
    # generation and "mask every capitalised word", and a capitalised ordinary
    # word is overwhelmingly sentence-initial. Skewed toward over-inclusion on
    # purpose — a missed name is one span and shows up in the recall number,
    # while a wrongly-masked common word corrupts every essay that uses it and
    # shows up nowhere unless somebody reads the prose.
    #
    # It is *data*, not a literal, because all three front doors need the same
    # 421 words and a hand-transliterated stoplist diverges silently. Loaded at
    # first use rather than at require time — the difference from Python's
    # load-at-import is that a host may `require "vicary"` to read
    # {Vicary::VERSION} without a vendored asset, and raising there would fail a
    # program that never redacts anything.
    def self.stop_words
      @stop_words ||= Lexicon.load("stop_words")
    end

    # Contraction and possessive tails. `[A-Z][A-Za-z'’]*` matches "I'm" as one
    # token, so without stripping these the stoplist never sees the word — "I'm"
    # and "As" were the two most common over-fires on real prose. The
    # *un*-apostrophized spellings students actually type ("im", "dont",
    # "thats") cannot be stripped this way because there is no clitic boundary to
    # find, so they are listed in the stoplist directly. "im" is a given name in
    # Wikidata, which is how "im faithfull" and "im going" became name
    # candidates.
    CLITICS = ["n't", "n’t", "'s", "’s", "'m", "’m", "'re", "’re", "'ve", "’ve",
               "'ll", "’ll", "'d", "’d", "'t", "’t"].freeze

    # An all-caps run this long or longer means capitalisation is not a signal,
    # so the stoplist carries the whole decision and a capital neither helps nor
    # hurts. A run *shorter* than this in an otherwise mixed-case document is the
    # opposite case: informal writers put one or two words in caps to shout, and
    # "SLAM", "WHACK" and "Nooooooo" are not names. Measured on 27 un-scrubbed
    # student documents, short all-caps runs were emphasis in every instance.
    ALLCAPS_RUN = 3

    # Any word token, used to find all-caps runs and mid-sentence capitals.
    WORD_TOKEN = /[A-Za-z][A-Za-z'’-]*/

    # Where a sentence begins: start of text, after terminal punctuation and any
    # closing quote, after a line break, or immediately inside an *opening*
    # quote. A capital in one of these positions is required by orthography, so
    # it is evidence of nothing — which is the whole of the objection to treating
    # a capital as proof that a word is a name.
    #
    # The opening-quote arm was missing, and quoted material is how feedback
    # refers to a student's own words: "vivid words like 'Giggles filled the
    # school'" put a capital on `Giggles` for the same orthographic reason a full
    # stop does, and it masked as a name in text a student reads. Only the
    # *capital* is discounted — a real name inside quotes still carries the
    # given-name tier.
    #
    # An apostrophe inside a word cannot match: the quote must not be preceded by
    # a letter, so "don't" and "Narciso's" are untouched.
    #
    # `\A` rather than `^`, so it is start-of-text and not start-of-line — the
    # two differ here and the difference is every hard-wrapped line in the
    # corpus.
    SENTENCE_BREAK = /(?:\A|[.!?]["'’”)]*\s+|\n+|(?:(?<=\s)|\A)["'‘“](?=[A-Za-z]))\s*/

    # One entirely-lowercase word. The leading boundary is what keeps this from
    # matching the tail of a capitalised word — there is no word boundary between
    # the "T" and the "errence" of "Terrence", so the capitalised route keeps
    # exclusive claim on anything it can see.
    LOWER_TOKEN = Regexp.new("#{NOT_WORD_BEFORE}[a-z][a-z'’-]*")

    # Tokens a lowercase span must reach before it is emitted at all. Set to 2
    # deliberately, and it is the single decision that makes the lowercase route
    # affordable.
    LOWERCASE_MIN_TOKENS = 2

    # Determiners that make the word after them a common noun rather than a name.
    # "a little bit", "the guy thats", "our joy" — English does not put a bare
    # determiner in front of a person's given name, so this is a clean structural
    # signal rather than a word blacklist, and it does not grow with the corpus.
    # Measured on 25 ASAP essays it accounted for 22 of ~34 lowercase over-fire
    # seeds, `a` alone for 12. Possessives are included: a student writes "my
    # cousin terrence", never "my terrence".
    DETERMINERS = Set.new(%w[
      a an the this that these those
      my your his her its our their
      some any no every each either neither both all
      another other such one two three
      most much many few several enough
    ]).freeze

    HONORIFIC_SET = Set.new(HONORIFICS.map(&:downcase)).freeze

    # One capitalised word, hyphens and apostrophes included so
    # "Raghunathan-Bell" and "O'Brien" stay whole, and the possessive comes with
    # the name rather than being left behind as a fragment.
    WORD = "[A-Z][A-Za-z'’]*(?:-[A-Z][A-Za-z'’]*)*"

    # A capitalised, name-shaped span: an optional honorific, optional initials,
    # then one or more capitalised words joined by optional lowercase particles.
    #
    # The honorific alternation is leftmost-first in all three languages, which
    # is what makes "Mrs." work: `Mr` matches first, its trailing `\s+` fails
    # against the "s", and the engine backtracks into `Mrs`.
    CANDIDATE_RE = Regexp.new(
      "#{NOT_WORD_BEFORE}" \
      "(?:(?:#{HONORIFICS.join('|')})\\.?\\s+)?" \
      '(?:[A-Z]\.\s*)*' \
      "#{WORD}" \
      "(?:\\s+(?:(?:#{PARTICLES.join('|')})\\s+)?#{WORD})*",
    )

    # Spans that are already redacted and must be left strictly alone. Two kinds,
    # and both were live defects rather than hypotheticals:
    #
    # * `{NAME}` — our own placeholders. The bare word inside the braces is
    #   capitalised, so without this a second pass generates "NAME" as a
    #   candidate and masking stops being idempotent. Both directions run this
    #   classifier and the outbound pass sees text the inbound pass already
    #   masked.
    # * `@PERSON1` — an upstream anonymization marker. The `@` is not part of a
    #   capitalised-word match, so `PERSON` matched on its own and every ASAP
    #   marker's kind-word became a candidate: 23.24 spans/essay of "over-firing"
    #   that was really this.
    PROTECTED = /\{[A-Za-z_0-9]*\}|@[A-Za-z]+\d*/

    # Any word token, either case. Used only by the title scan, which cannot key
    # on capitalisation because a student may write a title however they like.
    ANY_TOKEN = /[A-Za-z][A-Za-z'’-]*/

    # The one fold the title scan applies before consulting the prefix index. A
    # word processor turns every apostrophe curly, so "Charlotte’s Web" tokenises
    # with a character the gazetteer's keys never contain and the walk would stop
    # on its first token. Deliberately not the gazetteer's full `normalize`: that
    # does an NFKD decomposition and a per-character rebuild, and this runs once
    # per word of every essay. An accented title head still fails the walk, which
    # loses a keep and never a redaction.
    CURLY_APOSTROPHE = /[’‘ʼ′]/

    # How many tokens a title match may span. See {.find_title_spans}.
    TITLE_MAX_TOKENS = 8

    # Longest line still readable as a heading. Body prose in these documents is
    # hard-wrapped at ~60–590 chars per line, so length alone does not separate a
    # heading from a wrapped line — the blank line above it is what does.
    HEADING_MAX_CHARS = 60

    # A name-shaped span, with the placeholder it would be masked as.
    #
    # `LOCATION` was absent until 2026-08-07 on the argument that telling a place
    # from a person needs NER and the inbound path does not need the distinction
    # — both are placeholders in the training distribution. The first half of
    # that is what changed: a settlement gazetteer tier is not NER, and it types
    # the case that actually occurs. The second half was always the weaker claim,
    # because the inbound path is not the only reader — a host that echoes the
    # placeholder back writes "your trip to {NAME}".
    #
    # Still no `LOCATION` for a place that is *kept* (a landmark, a country): a
    # kept span is never masked, so it has no placeholder to type.
    Candidate = Struct.new(:text, :start, :end, :kind)

    # One row of the precedence table: a tag, and what it decides.
    PrecedenceRow = Struct.new(:tag, :mask, :kind)

    # The precedence table. The first row whose tag the span carries decides both
    # the mask/keep verdict and the placeholder, and that is the whole
    # classification policy.
    #
    # Pinned against `precedence` in `conformance/primitives.json`, because this
    # is the one part of the detector a port can get wrong while passing every
    # frame: reordering two rows changes which spans survive, and only a
    # colliding span can tell. The reference's frame set had no colliding span
    # for the detector's whole life, which is how 383 real settlements came to be
    # kept.
    #
    # **One principle orders the whole table: a lookup beats a guess, and a guess
    # that masks beats a guess that keeps.** Tier membership is a lookup — the
    # gazetteer positively asserts this exact string is a town. A suffix match is
    # a guess from a word ending.
    #
    # 1. `LOCATION` first, the only row backed by a lookup. `settlement?` is an
    #    **exact** match on a normalised key, not a prefix reading, so a span
    #    reaches this row only where the tier vouches for the whole string. Of
    #    the 16 real tier entries that also carry an org suffix, 12 are ordinary
    #    towns (Falls Church, Cut Bank, Union, Agency, College, Council, ...) and
    #    4 are tier noise (Byumba Hospital, Zeyrek Mosque, ...), so this is the
    #    better label 12 times in 16 — and a place is the more identifying
    #    reading.
    # 2. `ORGANIZATION` second. The suffix is still direct evidence about *this*
    #    string, and it types the case that actually occurs: "Progressive
    #    Insurance" is in nobody's settlement tier, so the order above costs it
    #    nothing.
    # 3. `LANDMARK` third — a guess like an org suffix, but one that *keeps*
    #    rather than masks, so it ranks below both. Ranking it above `LOCATION`
    #    is what kept 383 real hometowns whose names end in park, lake, valley or
    #    falls.
    # 4. `PERSON` last, and always matching, so the table is total. Below
    #    `LANDMARK` is not a redact-wins violation: `PERSON` is the absence of
    #    evidence, and keeping "Lincoln Memorial" is the landmark row's whole
    #    purpose.
    #
    # Nothing outside this table branches on the kind — it selects the
    # placeholder string and the minter's numbering namespace, while `mask` alone
    # carries the verdict. So rows 1 and 2 trade label accuracy only, with no
    # recall or privacy risk either way.
    PRECEDENCE = [
      PrecedenceRow.new("LOCATION", true, "LOCATION"),
      PrecedenceRow.new("ORGANIZATION", true, "ORGANIZATION"),
      PrecedenceRow.new("LANDMARK", false, nil),
      PrecedenceRow.new("PERSON", true, "NAME"),
    ].freeze

    # Mid-sentence capital. "I" is excluded because every writer capitalises it
    # whether or not they capitalise names, so it is the one capital that says
    # nothing about their habits.
    MID_SENTENCE_CAP = /(?<=[a-z,;:]\s)([A-Z][a-z]{2,})/

    # Mid-sentence capitals above which a document is taken to mark its proper
    # nouns with capitals — at which point a *lowercase* token is evidence
    # against a name.
    #
    # Measured on 36 un-scrubbed essay documents (~3,300 chars each, Project
    # Gutenberg) against a lower-cased copy of the same text: as written the
    # median is 10.5 and 35/36 documents are non-zero; lower-cased every document
    # is 0. Clean separation, so the threshold is not delicate — 2 rather than 1
    # only to tolerate a single stray capital.
    #
    # **A rate was measured against this floor and rejected.** A count is
    # length-blind, so the obvious repair is marks per 1,000 characters — and on
    # the 27 un-scrubbed student documents that does not separate the deciding
    # band, it only re-orders it. Both documents sitting at exactly 2 marks with
    # the closest rates are decided *the wrong way round* by a rate: `141-693`
    # marks "Powerball" twice in 3,478 characters (0.58 per 1k, a genuine
    # capitaliser) and `141-433` marks "The" and "There" in 1,144 (1.75 per 1k,
    # both artefacts of a sentence break the detector missed). A rate threshold
    # demotes the real one and promotes the false one. What actually separates
    # them is the *content* of the mark, which is per-token evidence — so the
    # band falls through to {.mid_sentence_capitals} rather than being decided at
    # document level, and that is what `INCONSISTENT` is for.
    MARKS_PROPER_NOUNS_MIN = 2

    # A sentence opening on a lower-case letter, which is the writer telling us
    # directly that they are not keeping standard capitalisation. Matched at the
    # start of the text as well as after a sentence break.
    LOWERCASE_SENTENCE_START = /(?:\A|(?<=[.!?]\s))\s*[a-z]/

    # A bare lower-case first-person "i" — the other unambiguous tell, and the
    # one that survives a writer who does capitalise sentence openings.
    BARE_LOWERCASE_I = Regexp.new("#{NOT_WORD_BEFORE}i#{NOT_WORD_AFTER}")

    # One terminal-punctuation unit. The denominator for the drop rate, and it
    # has to be this rather than {.sentence_starts}: that counts `\n` as a break
    # too, and these documents are hard-wrapped, so it would report a wrapped
    # line as a sentence and halve the rate. This is the population
    # {LOWERCASE_SENTENCE_START} actually draws from.
    SENTENCE_UNIT = /[^.!?]+[.!?]*/

    # Fraction of sentence openings that must be lower-case before a writer who
    # *does* mark proper nouns is read as also dropping capitals, rather than as
    # having made a typo. Read {.capitalisation_habit} for the reason this is
    # consulted on only one side of the floor — it is the load-bearing half.
    #
    # On the 27 un-scrubbed student documents the boolean "any lower-case
    # opening" fires on 8, and the openings split in two with a gap between 12.5%
    # and 7%:
    #
    # * habit — `my-fabit-book` 2 of 3 openings (67%), `141-433` 6 of 35 (17%),
    #   `121-816` 1 of 8 (12.5%);
    # * not — `my-first-tooth-gone` 1 of 14 (7%), `marching-to-his-own-beat` 3 of
    #   60 (5%), `141-140` 2 of 41 (5%), `121-502` 1 of 25 (4%).
    #
    # Every opening in the second group was read, and they are line wraps,
    # citations and one stylistic `Boy! did we cry`.
    # `marching-to-his-own-beat` is an NWP anchor paper that marks 26 proper
    # nouns correctly; the boolean called it a writer who does not keep standard
    # capitalisation, on three artefacts.
    DROPS_CAPITALS_MIN_RATE = 0.1

    # What a document has told us about how its writer uses capital letters.
    #
    # These replace two booleans — "does it capitalise its proper nouns" and
    # "does it drop standard capitals" — which were consulted separately and
    # *contradict each other on 7 of 27 un-scrubbed student documents*. `141-433`
    # has two mid-sentence capitals and six lower-case sentence openings, so it
    # was simultaneously a writer who capitalises and a writer who does not, and
    # whichever predicate a call site happened to read decided the treatment.
    #
    # Four states, because the two signals are independent and all four cells
    # occur:
    #
    # `consistent`
    #     Marks its proper nouns, and does not drop sentence capitals. A
    #     lower-case token here is evidence *against* a name. 15 of the 27.
    # `inconsistent`
    #     Does both. This is the writer the booleans had no cell for, and **both
    #     document-level treatments are wrong for them** — suppressing the
    #     lowercase route loses the names they wrote lower-case, and opening it
    #     wide fires on ordinary words. So there is no document-level answer here
    #     on purpose: the band falls through to per-token evidence
    #     ({.mid_sentence_capitals}), which is the right granularity and already
    #     existed. 4 of the 27.
    # `lowercase`
    #     Drops capitals and marks nothing. The given-name tier is the only
    #     handle left, and the lowercase route runs without corroboration. 1 of
    #     the 27.
    # `silent`
    #     Says nothing either way: no proper nouns to capitalise, and no dropped
    #     openings. **Silence is not consent.** Reading it as consent is what put
    #     "line circles" and "tone toward" in front of a student, because a
    #     108-290 character feedback field is ordinary prose with nothing in it
    #     to capitalise. Treated like `inconsistent`: per-token evidence, never
    #     the permissive path. 7 of the 27.
    #
    # Strings rather than symbols so they survive a JSON round trip into and out
    # of the conformance spec unchanged, and so the three languages can be diffed
    # on the wire without a mapping table in between.
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    LOWERCASE = "lowercase"
    SILENT = "silent"

    # The tiers whose keeps a first-person relation may override.
    #
    # Both are built from strings that are *also* ordinary people's names: 578
    # title keys and 33,682 full-name keys are a common given name beside an
    # ordinary US surname ("Alice Adams" is a 1921 novel; "Alan Ford" is a
    # footballer), and each keeps whichever private individual happens to carry
    # it.
    #
    # `place` and `iconic_short` are excluded and stay excluded. A place is not a
    # person, and a bare iconic surname has its own document-level rule with its
    # own guard ({.names_someone_in_the_writers_life?}).
    OVERRIDABLE_TIERS = Set.new(%w[title full_name demonym]).freeze

    # Words that make a nearby bare surname somebody in the WRITER'S life rather
    # than the public figure the document established.
    #
    # Deliberately NOT "the appositive contains a first-person pronoun", which
    # was the first design and is wrong: literary prose writes "Wright, who
    # taught me to look away from nothing", and refusing corroboration there
    # re-destroys the author the essay is about. A first-person pronoun says the
    # sentence is personal; only these cues say the *person* is.
    #
    # Closed and hand-written on purpose rather than "any noun before the name":
    # *hero*, *muse*, *inspiration*, *role model* and *favourite* are admiration
    # invocations that pair with public figures as readily as with relatives,
    # which is exactly why they are not evidence.
    RELATION_CUES = Set.new(%w[
      neighbor neighbour neighbors neighbours
      cousin cousins brother brothers sister sisters
      uncle aunt grandma grandpa grandmother grandfather
      mom mother dad father stepdad stepmom
      coach teacher tutor principal babysitter
      friend friends bestfriend classmate classmates roommate
      teammate teammates boss coworker
    ]).freeze

    # Multi-word proximity phrases, matched on the folded context string.
    #
    # Needed because the shape that actually occurs is "lives two doors down from
    # us" — a relation expressed as distance, with no relation noun in it
    # anywhere.
    PROXIMITY_CUES = [
      "doors down", "door down", "down the street", "next door",
      "across the street", "up the block", "down the block",
      "in my class", "in my grade", "on my team", "at my school",
      "in my neighborhood", "in my neighbourhood",
    ].freeze

    # First-person tokens, for the proximity leg. A proximity phrase says
    # somebody lives nearby; only a first-person pronoun says nearby *to the
    # writer*.
    FIRST_PERSON = Set.new(%w[i me my we us our]).freeze

    # How far around a bare surname to look for the cues. One clause either side:
    # long enough for "Robinson, who lives two doors down from us," and short
    # enough that the next sentence's unrelated cousin does not reach back.
    RELATION_WINDOW = 90

    # The relation nouns as a regex alternation. Sorted so the pattern is stable
    # across runs and diffs — and so it is the *same* pattern the reference
    # builds, since `sorted()` over the Python frozenset and a sort here must
    # agree.
    RELATION_ALTERNATION = RELATION_CUES.to_a.sort.join("|")

    # Up to two words may sit between the possessive and the relation noun — "my
    # next-door neighbor", "my best friend", "my old soccer coach".
    #
    # The reference comments this class as "lower-case only, so a capitalised
    # name cannot be swallowed as a modifier". That is **not** what it does:
    # every caller folds its window with `.lower()` before matching, so no
    # capital ever reaches `[a-z]` and the restriction cannot fire. "My Old
    # soccer coach Deshawn" is accepted exactly as "my old soccer coach Deshawn"
    # is. Kept as-is because the behaviour is identical in all three languages
    # and a port is the wrong place to change a rule.
    MODIFIERS = "(?:[a-z][a-z'’-]*\\s+){0,2}"

    # "my cousin " immediately before the span. Anchored at the end: the relation
    # phrase has to run right up to the name, which is what makes it name *that*
    # person rather than merely appear in the same sentence.
    RELATION_ATTACHED_BEFORE = Regexp.new(
      "#{NOT_WORD_BEFORE}(?:my|our)\\s+#{MODIFIERS}(?:#{RELATION_ALTERNATION})\\s+\\z",
    )

    # ", my next-door neighbor" immediately after it. The comma is required — an
    # appositive is punctuated and a prepositional phrase is not, and that is the
    # whole difference between "Alice Adams, my neighbor," and "Harry Potter …
    # with my little brother".
    RELATION_ATTACHED_AFTER = Regexp.new(
      "\\A\\s*,\\s*(?:who\\s+(?:is|was)\\s+)?(?:my|our)\\s+#{MODIFIERS}" \
      "(?:#{RELATION_ALTERNATION})#{NOT_WORD_AFTER}",
    )

    # A title whose own first words are a first-person relation — "My Cousin
    # Vinny", "My Sister Eileen", "My Best Friend Anne Frank". 41 keys in the
    # shipped tier, and they are the most dangerous shape in it: the phrase they
    # occupy is `kinship-possessive`, the single commonest frame a student names
    # somebody in.
    TITLE_LEADS_WITH_RELATION = Regexp.new(
      "\\A(?:my|our)\\s+#{MODIFIERS}(?:#{RELATION_ALTERNATION})#{NOT_WORD_AFTER}",
    )

    # The tier a candidate must resolve to before it may establish a surname.
    #
    # A place, a landmark, a work title and an already-bare iconic surname are
    # all excluded: none of them is a person written first-name-then-surname, so
    # none carries evidence about what a bare surname in the same document means.
    #
    # Pinned against `corroboration.tier` in `conformance/primitives.json`,
    # because a port that compared against some other string would corroborate
    # nothing and still pass every other case — a corroboration that never fires
    # is invisible in output the span was going to be masked in anyway.
    CORROBORATING_TIER = "full_name"

    # `PARTICLES` as a set, for the membership tests the surname folding does.
    PARTICLE_SET = Set.new(PARTICLES).freeze

    class << self
      # ---------------------------------------------------------------------
      # Primitives
      # ---------------------------------------------------------------------

      # Every match of `pattern` in `text`, with offsets — Ruby's answer to
      # `re.finditer`.
      #
      # A zero-length match advances by one character rather than looping
      # forever, which is what both other languages' global iteration does.
      # {SENTENCE_BREAK} can match empty at offset 0, so this is reached rather
      # than theoretical.
      def each_match(text, pattern)
        return enum_for(:each_match, text, pattern) unless block_given?

        pos = 0
        length = text.length
        while pos <= length && (m = pattern.match(text, pos))
          yield m
          pos = m.end(0) > m.begin(0) ? m.end(0) : m.begin(0) + 1
        end
      end

      # Python's `str.strip(chars)`: drop any of `chars` from both ends.
      def strip(text, chars)
        first = 0
        last = text.length
        first += 1 while first < last && chars.include?(text[first])
        last -= 1 while last > first && chars.include?(text[last - 1])
        text[first...last]
      end

      # Python's `str.isupper()` for a token this module's patterns can produce.
      #
      # True when the token has at least one cased character and none of them is
      # lowercase. Every token here starts with an ASCII letter, so the cased set
      # is non-empty and the comparison is the whole test; the apostrophes and
      # hyphens in between are uncased and drop out of it in every language.
      def upper?(token)
        token == token.upcase && token != token.downcase
      end

      # Python's `str.islower()` for a token this module produces.
      #
      # Every token here comes from {ANY_TOKEN}, which is `[A-Za-z][A-Za-z'’-]*`
      # — so a cased character is always present and the "at least one cased
      # char" half of Python's contract is satisfied by construction, leaving the
      # comparison.
      def lower?(token)
        token == token.downcase
      end

      # `word` with one trailing contraction or possessive tail removed.
      #
      # Returns `word` unchanged when there is nothing to remove, so a caller can
      # compare the two and tell whether the fold did anything. Only one tail
      # comes off — "Terrence's" is a name plus a possessive, not a name plus
      # two.
      def without_clitic(word)
        CLITICS.each do |clitic|
          return word[0...-clitic.length] if word.end_with?(clitic) && word.length > clitic.length
        end
        word
      end

      # Whether `token` is an ordinary word that must never become a candidate.
      def stop?(token)
        word = without_clitic(strip(token.downcase, ".,"))
        stop_words.include?(strip(word, "'’"))
      end

      # Words split on whitespace, the way Python's bare `str.split()` does.
      def words(text)
        text.split(/\s+/).reject(&:empty?)
      end

      # Every {ANY_TOKEN} match in `text`, as Python's `findall` returns them.
      def any_tokens(text)
        text.scan(ANY_TOKEN)
      end

      # The first clause of `text` — the scan stops at terminal punctuation.
      def first_clause(text)
        text.split(/[.!?\n]/, -1)[0].to_s
      end

      # Whether `[start, finish)` overlaps any of `spans`.
      def overlaps?(spans, start, finish)
        spans.any? { |span_start, span_end| start < span_end && finish > span_start }
      end

      # ---------------------------------------------------------------------
      # Classification
      # ---------------------------------------------------------------------

      # The placeholder a candidate of this kind masks as.
      def placeholder_for(kind)
        %w[ORGANIZATION LOCATION].include?(kind) ? "{#{kind}}" : "{NAME}"
      end

      # Every tag the evidence supports for this span. Decides nothing.
      #
      # Separated from the decision on purpose: this reads evidence and
      # {PRECEDENCE} applies policy, so changing what we do about a collision is
      # an edit to a table rather than to a detector.
      #
      # `settlement` absent means the `LOCATION` tag is never reachable — the
      # behaviour before the tier existed, and the behaviour a caller that wires
      # no oracles still gets.
      def classify_tags(tokens, settlement = nil)
        # PERSON is unconditional: the span reached the table because it is
        # name-shaped, so the tag records that there is no evidence *beyond* the
        # shape. Making it unconditional is what makes the table total.
        tags = Set.new(["PERSON"])
        # No tokens is no evidence, which is what a bare PERSON tag already says.
        return tags if tokens.empty?

        tail = strip(tokens[-1].downcase, ".,")
        tags << "ORGANIZATION" if ORG_SUFFIXES.include?(tail)
        tags << "LOCATION" if !settlement.nil? && settlement.call(tokens.join(" "))
        # Multi-token only: a bare "Park" is a surname far more often than a
        # place.
        tags << "LANDMARK" if tokens.length > 1 && LANDMARK_SUFFIXES.include?(tail)
        tags
      end

      # The first row of {PRECEDENCE} this span carries the tag for.
      def resolve(tags)
        row = PRECEDENCE.find { |r| tags.include?(r.tag) }
        # Unreachable: PERSON is unconditional, so the last row always matches.
        raise "no precedence row matched #{tags.to_a.sort.join(',')}" if row.nil?

        row
      end

      # Which placeholder kind this span would mask as.
      #
      # The kind half of the table's verdict. A span the table *keeps* has no
      # placeholder, and types `NAME` here as an inert default — nothing reads
      # it, because the masking pass asks the same table for the verdict first.
      def classify(tokens, settlement = nil)
        resolve(classify_tags(tokens, settlement)).kind || "NAME"
      end

      # Whether `name` carries the `LANDMARK` tag — a suffix guess, no lookup.
      #
      # A tag, not a verdict. It says the span *looks* like a landmark, which is
      # all a word ending can say; whether that keeps the span is {PRECEDENCE}'s
      # call, and a settlement lookup outranks it.
      def public_landmark?(name)
        classify_tags(words(name)).include?("LANDMARK")
      end

      # Drop stoplisted tokens, splitting the span where one sits inside it.
      #
      # "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER" is one match, because in
      # an all-caps sentence every token is capitalised. Trimming the edges is
      # not enough — the name is in the middle — so an interior stopword ends the
      # run and starts a new one.
      #
      # The exception is an honorific introducing a name. "Mrs" and "Dr" are in
      # the stoplist so that a bare "Mrs." cannot become a candidate on its own,
      # but "Mrs. Okonkwo" has to stay whole: masking only the surname leaves the
      # relationship and the surname's position in the text.
      def trim(tokens)
        runs = []
        current = []
        tokens.each_with_index do |token, index|
          introduces_a_name =
            HONORIFIC_SET.include?(strip(token.downcase, ".,")) &&
            index + 1 < tokens.length &&
            !stop?(tokens[index + 1])
          if stop?(token) && !introduces_a_name
            unless current.empty?
              runs << current
              current = []
            end
            next
          end
          current << token
        end
        runs << current unless current.empty?
        runs
      end

      # ---------------------------------------------------------------------
      # Reading the document's own orthography
      # ---------------------------------------------------------------------

      # Offsets at which a sentence begins.
      def sentence_starts(text)
        each_match(text, SENTENCE_BREAK).map { |m| m.begin(0) + m[0].length }.to_set
      end

      # Character ranges of all-caps runs SHORTER than {ALLCAPS_RUN}.
      #
      # A long all-caps run is a writer who has stopped using case at all, and
      # the stoplist handles it. A one- or two-word run inside mixed-case prose
      # is emphasis — the informal register's italics — and it is where "SLAM",
      # "WHACK", "LAUGHTER" and "REDACT" came from on real student writing.
      #
      # Single-character tokens are excluded: "I" is upper-case for every writer,
      # and the initials in "J. R. Tolkien" are part of a name rather than a
      # shout.
      def emphasis_spans(text)
        runs = []
        current = []
        each_match(text, WORD_TOKEN) do |m|
          token = m[0]
          if token.length > 1 && upper?(token)
            current << [m.begin(0), m.begin(0) + token.length]
            next
          end
          unless current.empty?
            runs << current
            current = []
          end
        end
        runs << current unless current.empty?
        runs.select { |run| run.length < ALLCAPS_RUN }
            .map { |run| [run[0][0], run[-1][1]] }
      end

      # Character ranges of lines that are section headings, not prose.
      #
      # A heading is title-cased by convention, so **every capital in it is
      # orthographic** and none of it is testimony about any word. This replaces
      # a rule that read the same spans as emphasis, which the data does not
      # support: across the 27 un-scrubbed documents there was not one instance
      # of a writer capitalising an initial letter for emphasis. Emphasis in
      # student prose is ALL CAPS ("this is BULLSHIT") or mixed caps, and
      # {.emphasis_spans} already has it. What actually generates these spans is
      # layout — "Horses" on its own line, "Horse Families", "Breeds I Like", "My
      # Description of a Horse".
      #
      # Three conditions, all structural and none of them a word list:
      #
      # * short — under {HEADING_MAX_CHARS};
      # * no terminal punctuation — a heading is not a sentence;
      # * preceded by a blank line, or first in the document.
      #
      # The blank line is load-bearing rather than belt-and-braces. Body prose
      # here is hard-wrapped, so "The INternet as we know it today first" is a
      # short unpunctuated line too, and without the blank-line test it would
      # read as a heading and take a real name's evidence with it.
      def heading_spans(text)
        out = []
        offset = 0
        previous_blank = true # start of document counts
        # `-1` keeps the trailing empty field, so a document ending in a newline
        # advances the offset the same way one that does not.
        text.split("\n", -1).each do |line|
          stripped = line.strip
          if !stripped.empty? &&
             stripped.length < HEADING_MAX_CHARS &&
             !".!?".include?(stripped[-1]) &&
             previous_blank
            out << [offset, offset + line.length]
          end
          previous_blank = stripped.empty?
          offset += line.length + 1
        end
        out
      end

      # Classify how this document's writer uses capitals. See {CONSISTENT} and
      # its siblings.
      #
      # Two independent readings, each taken from evidence the writer supplied
      # rather than inferred from what is missing.
      #
      # **Does it mark proper nouns?** Count mid-sentence capitals, excluding any
      # that fall inside a heading. Sentence-initial capitals are not counted at
      # all: a student who capitalises the start of each sentence but not the
      # names inside them is exactly the case the lowercase route exists for, and
      # counting those would suppress the route on them. The heading exclusion
      # brings this counter into line with {.mid_sentence_capitals}, which the
      # inconsistent band falls through to — the two channels were reading the
      # same evidence through different rules, which is a defect whatever the
      # threshold is. Its measured effect on the 27 documents is **none**: it
      # lowers five counts (`horses` 52 to 27 is the largest) and none of them
      # crosses the floor. It is a precision repair, not a fix, and is recorded
      # as one.
      #
      # **Does it drop standard capitals?** A bare lower-case "i" anywhere, or a
      # lower-case sentence opening. Both are the writer's own doing rather than
      # an inference from what is missing.
      #
      # **The rate is consulted on only one side of the floor, and that asymmetry
      # is the measurement, not an oversight.** Above the floor there is a
      # presence signal to weigh the drop side against, so the rate can say "26
      # marks and 3 dropped openings is a writer who typed three typos" — which
      # is `marching-to-his-own-beat`, an NWP anchor paper the boolean libelled.
      # Below the floor there is nothing to weigh it against, and applying it
      # there **costs a held-out name**: the `lowercase-writing` fixture frame
      # rides in two carrier essays, and in 20739 (one mid-sentence capital, one
      # lower-case opening in 59 sentences, no bare "i") a 1.7% drop rate demoted
      # a genuine lower-case-writing document to `silent`, withdrew the
      # permissive path, and leaked "terrence okonkwo". Held-out recall 28/28 to
      # 27/28 for one span of over-firing — the wrong direction for a tool whose
      # whole bias is over-redact rather than leak.
      #
      # So below the floor the document has given us one bit and it is taken
      # conservatively: any tell at all means `lowercase`. The cost of that is
      # `my-first-tooth-gone` staying on the permissive path when it is really a
      # capitaliser with nothing to capitalise — and that cost was measured at
      # **zero** spans, because its only candidate is "Boy" from the capitalised
      # route under either reading. A guard whose failing case costs nothing,
      # against a rate whose correction costs a name, is not a guard worth
      # having.
      #
      # @param headings spans whose capitals are orthographic because title case
      #   put them there. Passed in rather than computed so the arm that turns
      #   the heading rule off stays coherent — with it off, this reads headings
      #   as prose, exactly like every other consumer of that flag.
      def capitalisation_habit(text, headings = [])
        marks = 0
        each_match(text, MID_SENTENCE_CAP) do |m|
          # The lookbehind consumes nothing, so group 1 starts where the match
          # does — which is what the reference's `m.start(1)` resolves to too.
          marks += 1 unless overlaps?(headings, m.begin(0), m.begin(0) + m[0].length)
        end
        openings = each_match(text, LOWERCASE_SENTENCE_START).count
        # The bare "i" stays a boolean on both sides. It is the higher-precision
        # tell — 26 of the 27 un-scrubbed documents have none at all, and the one
        # that does has nine — so there is no noise for a rate to remove.
        bare_i = BARE_LOWERCASE_I.match?(text)

        if marks >= MARKS_PROPER_NOUNS_MIN
          sentences = each_match(text, SENTENCE_UNIT).count { |m| !m[0].strip.empty? }
          habitual = openings.to_f / [1, sentences].max >= DROPS_CAPITALS_MIN_RATE
          return bare_i || habitual ? INCONSISTENT : CONSISTENT
        end
        bare_i || openings.positive? ? LOWERCASE : SILENT
      end

      # Whether the writer puts capitals on proper nouns at all.
      #
      # True for both `consistent` and `inconsistent`: an inconsistent writer who
      # capitalised "Vinny" and left "cousin" lower-case made a choice, and that
      # choice is testimony. It is the *absence* of a capital that means nothing
      # in a `lowercase` or `silent` document.
      def marks_proper_nouns?(habit)
        habit == CONSISTENT || habit == INCONSISTENT
      end

      # Whether the writer drops standard capitals as a habit.
      def drops_capitals?(habit)
        habit == INCONSISTENT || habit == LOWERCASE
      end

      # Lower-cased forms of every word this document capitalises mid-sentence.
      #
      # The document's own testimony about a particular word, which is the graded
      # version of {.capitalisation_habit}, and what its `inconsistent` state
      # falls through to. A writer who put a capital on "Cade" somewhere other
      # than a sentence start has told us "Cade" is a name in this document; one
      # who only ever writes "Eventually" after a full stop has told us nothing,
      # because orthography would have put that capital there anyway.
      #
      # An entirely upper-case token is excluded, and that exclusion is
      # load-bearing rather than tidy. Without it "SLAM" corroborates itself —
      # the token is its own mid-sentence capital — so every emphasis shout would
      # clear the bar the emphasis rule had just raised. A capital is testimony
      # only where the writer had a lower-case alternative and declined it.
      #
      # A heading is excluded for the same reason: it is title-cased, so its
      # non-initial capitals are orthographic too. Counting them let "The First
      # Horses" vouch for "Horses" as a name — the heading corroborating itself,
      # one line removed.
      def mid_sentence_capitals(text, starts, headings = [])
        out = Set.new
        each_match(text, WORD_TOKEN) do |m|
          token = m[0]
          next if starts.include?(m.begin(0)) || !token[0].match?(/[A-Z]/)
          next if token.length > 1 && upper?(token)
          next if overlaps?(headings, m.begin(0), m.begin(0) + token.length)

          out << strip(token.downcase, "'’")
        end
        out
      end

      # ---------------------------------------------------------------------
      # The sentence-initial guard
      # ---------------------------------------------------------------------

      # Whether this span rests on a capital that had to be there anyway.
      #
      # Three shapes are excluded, because each carries evidence beyond the
      # capital: a multi-token span ("Sadie Johnson") is a *shape*; an honorific
      # in front of the name is a relationship; and a capital in the middle of a
      # sentence is a choice the writer made rather than one orthography made for
      # them.
      #
      # A heading is the exception to the first of those. Title case capitalises
      # every word, so "Horse Families" is not a shape there — the second capital
      # is as orthographic as the first, and a multi-token span inside a heading
      # has no more evidence than a single-token one. So the multi-token
      # exemption does not apply inside a heading, and "My Brother Terrence
      # Okonkwo" as a heading is still caught: it needs the given-name tier
      # rather than its own capitals, which is exactly the bar every other
      # unevidenced capital has to clear.
      def capital_is_the_only_evidence?(tokens, start, starts, emphasis, headings = [])
        finish = start + tokens.join(" ").length
        in_heading = overlaps?(headings, start, finish)
        return false if tokens.length > 1 && !in_heading
        return true if in_heading
        return true if overlaps?(emphasis, start, finish)

        starts.include?(start)
      end

      # A second signal, for a span whose capital proves nothing on its own.
      #
      # Two channels: the document's own mid-sentence capitalisation of the word
      # (`written_as_a_capital`, from {.mid_sentence_capitals}), and the
      # given-name tier. `is_given` is passed in rather than defaulted so this is
      # only reachable on the path where an oracle exists.
      #
      # ANY token counts, not just the first, and the heading rule is what made
      # that distinction load-bearing. Before it, this was only ever reached for
      # single-token spans, so "first token" and "any token" were the same thing.
      # A heading is title-cased, so a multi-token span inside one also arrives
      # here — and "My Brother Terrence Okonkwo" leads with an honorific, so
      # checking only the first token consulted "Brother" and leaked the name.
      def corroborated?(tokens, written_as_a_capital, is_given)
        # Both channels see the same stripped token, and the strip set is `.,'’`
        # rather than the `'’` {.mid_sentence_capitals} folds with. That
        # asymmetry is deliberate and was a defect once: the capital channel
        # stripped `.,'’` and the given-name channel got the raw token, so a name
        # against a closing quote — "words like 'Terrence'", which the candidate
        # pattern hands over as `Terrence'` because an apostrophe is a name
        # character — asked the tier about `Terrence'` and was told no.
        tokens.each do |token|
          stripped = strip(token.downcase, ".,'’")
          return true if written_as_a_capital.include?(stripped) || is_given.call(stripped)

          # ...and again with the possessive off. "Terrence's" at a sentence
          # start is the shape this is for: the writer capitalised "Terrence"
          # elsewhere in the document, which is testimony about the name, and the
          # `'s` is not part of it. Without this the document's own capital
          # cannot vouch for its own possessive, so the span is suppressed and
          # the name ships.
          #
          # The gazetteer's given-name tier folds possessives itself, so the
          # shipped arm already behaved this way through channel two and nothing
          # changes for it. What the fold buys is the *first* channel, which had
          # no such normalisation, and independence from an oracle contract
          # nobody wrote down.
          #
          # Strictly additive: it can turn a false into a true and never the
          # reverse, so it can only reduce suppression, never increase it.
          folded = without_clitic(stripped)
          if folded != stripped &&
             (written_as_a_capital.include?(folded) || is_given.call(folded))
            return true
          end
        end
        false
      end

      # The sentence-initial guard: drop a span whose only evidence is a capital
      # that orthography required, unless a second channel vouches for it.
      #
      # The two halves are separate methods because they answer separate
      # questions — "is the capital all we have?" and "is there anything else?" —
      # and this is the conjunction {.find_candidates} applies.
      #
      # **Requiring a second signal is only sound when there is a second signal
      # to require.** Without a given-name list the document's own capitalisation
      # is the sole channel, and a name mentioned once at a sentence start is
      # then genuinely indistinguishable from "Eventually" — so the no-oracle arm
      # keeps its recall-maximal, precision-minimal character rather than
      # becoming quietly stricter. That is why the caller reaches this only when
      # an oracle was passed, and why this takes `is_given` rather than treating
      # its absence as permissive.
      #
      # Measured on real prose: 133 occurrences over 101 distinct spans
      # suppressed, 99 of the 101 correctly — about 98% precise. The two it gets
      # wrong are names written once, at a sentence start, that no tier knows.
      # **Do not "fix" it**; the tier feeding it was the defect, and that was
      # addressed in 0.1.0 by adding SSA births to the given-name tier.
      def suppressed_as_an_unevidenced_capital?(tokens, start, starts, emphasis, headings,
                                                written_as_a_capital, is_given)
        capital_is_the_only_evidence?(tokens, start, starts, emphasis, headings) &&
          !corroborated?(tokens, written_as_a_capital, is_given)
      end

      # ---------------------------------------------------------------------
      # Titles, and the relations that override them
      # ---------------------------------------------------------------------

      # Character ranges covered by a work title or a fictional character name.
      #
      # Runs against the raw text *before* candidate generation, longest match
      # first, and the ranges it returns are protected exactly like an upstream
      # anonymization marker. That ordering is the whole point: the notability
      # oracle cannot save a title, because generation never hands it one. "To
      # Kill a Mockingbird" is split by the stoplisted "a" into two candidates,
      # and no lookup on either half recovers the book.
      #
      # Matches do not overlap — once a span is claimed the scan resumes after it
      # — so "The Lion King" cannot also match a shorter title inside itself.
      #
      # The 8-token limit is a named limit, not an oversight: the tier's longest
      # entry is 36 tokens, but scanning that far costs 36 lookups per token
      # position for titles nobody writes in an essay. 8 covers "To Kill a
      # Mockingbird"; "The Curious Incident of the Dog in the Night-Time" is 10
      # and is NOT matched.
      #
      # @param is_prefix answers "does some title start with these folded
      #   tokens?" and is the automaton this scan walks. It doubles as the
      #   first-token prefilter, since a length-1 prefix *is* a title head.
      #   Supplied, the walk stops as soon as no title can still be reached — one
      #   or two tokens on ordinary prose, against the eight-lookup worst case
      #   the length-descending scan paid at every position whose first word
      #   happens to head some title ("the", "a", "my"). Absent, every length up
      #   to {TITLE_MAX_TOKENS} is tried and the result is identical; only the
      #   cost differs.
      # @param requires_capital in a document that capitalises its proper nouns,
      #   a title's first word is capitalised too. Requiring that skips almost
      #   every position in ordinary prose. Documents that do NOT capitalise are
      #   scanned at every position, because there the case carries nothing.
      def find_title_spans(text, is_title, is_prefix = nil, requires_capital: false)
        tokens = each_match(text, ANY_TOKEN).map do |m|
          [m.begin(0), m.begin(0) + m[0].length, m[0].downcase.gsub(CURLY_APOSTROPHE, "'")]
        end
        spans = []
        index = 0
        while index < tokens.length
          head_start, head_end, = tokens[index]
          if requires_capital && !text[head_start].match?(/[A-Z]/)
            index += 1
            next
          end
          longest = 0
          longest_end = head_end
          key = ""
          limit = [TITLE_MAX_TOKENS, tokens.length - index].min
          (1..limit).each do |length|
            _, token_end, token_key = tokens[index + length - 1]
            key = length == 1 ? token_key : "#{key} #{token_key}"
            # Multi-token only: "It" and "Up" must not make ordinary words
            # permanently notable.
            if length > 1 && is_title.call(text[head_start...token_end])
              longest = length
              longest_end = token_end
            end
            break if !is_prefix.nil? && !is_prefix.call(key)
          end
          spans << [head_start, longest_end] if longest.positive?
          index += longest.positive? ? longest : 1
        end
        spans
      end

      # Whether the local context marks this surname as personal, not public.
      #
      # Checked only for a bare surname the document has otherwise *established*
      # as a public figure's, and it is the one signal that can separate the two
      # readings of "Robinson" in a document containing "Jackie Robinson": the
      # neighbour carries an appositive about the writer's own life, and the
      # ballplayer does not.
      #
      # Looks after the span for an appositive or relative clause, and before it
      # for a possessive introduction ("my neighbour Robinson"). Both sides
      # matter — English puts the relation either place — and neither reaches
      # past one clause.
      def names_someone_in_the_writers_life?(text, start, finish)
        after = text[finish, RELATION_WINDOW].to_s.downcase
        before = text[[0, start - RELATION_WINDOW].max...start].to_s.downcase

        # After: only an appositive or relative clause counts. A new sentence
        # does not, so the scan stops at terminal punctuation. `before` is NOT
        # clipped the same way — the reference scans the whole leading window.
        [first_clause(after), before].each do |window|
          return true if PROXIMITY_CUES.any? { |cue| window.include?(cue) }
          return true if any_tokens(window).any? { |token| RELATION_CUES.include?(token) }
        end
        false
      end

      # Whether a relation-led title span is really the writer naming somebody.
      #
      # "My Cousin Vinny is my favorite movie" and "My cousin Vinny Delgado came
      # over that summer" fold to the same lookup key, and the tier keeps both.
      # The difference is one the writer supplied: a title is title-cased, so its
      # relation word carries a capital, and a sentence about a relative does
      # not.
      #
      # That is the same evidence the heading rule reads and the same evidence
      # rule 1 of the capitalisation rules reads — the document's own
      # orthography, not a guess about intent. Callers gate this on
      # {.marks_proper_nouns?}, because in a document that capitalises nothing
      # the absent capital is not testimony about anything. An INCONSISTENT
      # writer passes that gate: they put a capital on "Vinny" and left "cousin"
      # lower-case, and that is a choice rather than an absence.
      #
      # The cost of being wrong is a student who writes "my cousin vinny is my
      # favorite movie" losing the film to a placeholder inbound. The cost of the
      # other error is a cousin's name reaching a third-party model.
      def title_is_the_writers_own_relation?(text, start, finish)
        span = text[start...finish].to_s
        return false unless TITLE_LEADS_WITH_RELATION.match?(span.downcase)

        # Everything after the leading possessive: "Cousin Vinny" in the title,
        # "cousin Vinny" in the sentence. The relation word is the one that
        # differs.
        any_tokens(span)[1, 2].to_a.any? { |token| lower?(token) }
      end

      # Whether the span alone proves the writer used capitals and skipped one.
      #
      # The document-level gate on {.title_is_the_writers_own_relation?} costs a
      # leak on the shortest documents. `marks_proper_nouns?` needs two
      # capitalised names *somewhere else* to be true, and "My cousin Vinny came
      # over that summer and never left." has none — the only other capital is
      # sentence-initial. So the refusal switched off, the 1992 film kept the
      # span, and the cousin's name shipped. Measured, not supposed: adding one
      # unrelated name ("the Alvarez family") to the same sentence flips the
      # document tell and the same cousin masks correctly. A leak that depends on
      # how much *else* the student wrote is a leak.
      #
      # What this reads instead is confined to the span, so it needs no document:
      #
      #     My Cousin Vinny   -- every token capitalised; the film. Already
      #                          excluded by title_is_the_writers_own_relation?.
      #     My cousin Vinny   -- the name carries a capital and the relation word
      #                          does not. MIXED: the writer uses capitals, and
      #                          chose not to put one on "cousin". A relative.
      #     my cousin vinny   -- nothing carries a capital. Not mixed, and the
      #                          document gate above applies in full.
      #
      # The trailing token is the test rather than "any token", because the
      # leading possessive is sentence-initial in every frame this shape occurs
      # in, and a sentence-initial capital is orthography, not evidence.
      def relation_led_title_is_internally_mixed?(text, start, finish)
        tokens = any_tokens(text[start...finish].to_s)
        return false if tokens.length < 2

        last = tokens[-1]
        initial = last[0, 1]
        # Python's `[:1].isupper()`; the character is an ASCII letter by
        # construction.
        starts_upper = !initial.empty? && initial == initial.upcase
        starts_upper && tokens[1, 2].to_a.any? { |token| lower?(token) }
      end

      # Whether a first-person relation is syntactically attached to this name.
      #
      # The strict sibling of {.names_someone_in_the_writers_life?}, and strict
      # for a measured reason. That method scans a window for any relation cue,
      # which is right for a bare surname the document itself established — but
      # applied to the title tier it refuses six of the seven curriculum
      # characters it must keep, because characters are *described by* their
      # relations: Atticus Finch is a father, Peter Parker lives with his aunt,
      # Tom Sawyer talks his friends into whitewashing a fence. A relation noun
      # in the window is therefore no evidence at all about a work title.
      #
      # Two things separate "My neighbor Alice Adams" from those. The relation is
      # **first-person** — the writer's own — and it is **attached** to the name,
      # either immediately before it or inside the appositive immediately after
      # it. Both are required. First person alone keeps "I read Harry Potter with
      # my little brother"; attachment alone keeps "Atticus Finch, a father
      # who…".
      #
      # The error costs are asymmetric and that is what makes the rule affordable
      # at all: a title hit overridden wrongly over-redacts a book the student
      # wrote about, which the inbound placeholder absorbs; a title hit honoured
      # wrongly ships a classmate's name to a third-party model.
      def names_someone_the_writer_knows?(text, start, finish)
        before = text[[0, start - RELATION_WINDOW].max...start].to_s.downcase
        after = text[finish, RELATION_WINDOW].to_s.downcase
        return true if RELATION_ATTACHED_BEFORE.match?(before)
        # Anchored at the start of the window by the pattern's own `\A`, which is
        # what the reference's `match` rather than `search` carries.
        return true if RELATION_ATTACHED_AFTER.match?(after)

        # The relation expressed as distance — "Alice Adams, who lives two doors
        # down from us". Same attachment requirement (the clause is the
        # appositive that follows the name), plus a first-person pronoun, because
        # "two doors down" on its own says nothing about whose street it is.
        if after.lstrip.start_with?(",")
          clause = first_clause(after)
          if PROXIMITY_CUES.any? { |cue| clause.include?(cue) } &&
             any_tokens(clause).any? { |token| FIRST_PERSON.include?(token) }
            return true
          end
        end
        false
      end

      # ---------------------------------------------------------------------
      # Corroboration
      # ---------------------------------------------------------------------

      # Lower-cased tokens of `name` with the possessive tail removed.
      #
      # "Wright’s" and "Wright" must fold together or corroboration reaches the
      # citation form of the name and not the one literary analysis actually
      # writes — on the un-scrubbed corpus the possessive was 10 of the 27 masked
      # "Wright" spans, so this is most of the effect rather than an edge case.
      def surname_tokens(name)
        folded = name.gsub(CURLY_APOSTROPHE, "'").downcase
        out = []
        folded.strip.split(/\s+/).each do |raw|
          token = strip(raw, ".,;:!?'\"")
          token = token[0...-2] if token.end_with?("'s") && token.length > 3
          out << token unless token.empty?
        end
        out
      end

      # `name` as a corroboration key, or nil if it is not a bare form.
      #
      # A bare surname is one token, or a particle-led run ("van Gogh", "de
      # Beauvoir") where every token but the last is a particle. Anything else —
      # "Coach Wright", "Priya Wright" — is a *different* candidate that happens
      # to share a surname, and must not be reached by another name's
      # corroboration.
      def bare_surname_key(name)
        tokens = surname_tokens(name)
        return nil if tokens.empty?
        return tokens[0] if tokens.length == 1
        return tokens.join(" ") if tokens.length <= 3 && tokens[0...-1].all? { |t| PARTICLE_SET.include?(t) }

        nil
      end

      # The bare surface forms a writer may substitute for `name` later on.
      #
      # `"Richard Wright"` yields `["wright"]`; `"Vincent van Gogh"` yields
      # `["gogh", "van gogh"]`. The bare *first* name is never a form, for the
      # same reason the builder refuses to emit one: a first name is the
      # commonest private surface form in student prose, and corroborating it
      # would make one notable full name keep every "Terrence" in the document.
      #
      # Returns `[]` for a single-token name — a mononym corroborates nothing,
      # because it is already the bare form.
      def surname_forms(name)
        tokens = surname_tokens(name)
        return [] if tokens.length < 2

        forms = [tokens[-1]]
        if PARTICLE_SET.include?(tokens[-2])
          forms << tokens[-2..].join(" ")
          forms << tokens[-3..].join(" ") if tokens.length >= 3 && PARTICLE_SET.include?(tokens[-3])
        end
        forms
      end

      # Whether `name` may establish a surname, given the two oracle shapes a
      # caller might have. Factored out because {.corroborated_surnames} and
      # {.established_name_tokens} apply the identical three-way test and the two
      # drifting apart is a silent asymmetry between the inbound and outbound
      # paths.
      def establishes?(name, notable, lowered_keep, tier = nil)
        # A name the assignment prompt supplied. Topical by construction, and the
        # prompt naming "Richard Wright" is the same evidence as the essay naming
        # him — arguably better, since it is not the student's writing.
        return true if lowered_keep.include?(name.downcase)
        return tier.call(name) == CORROBORATING_TIER unless tier.nil?

        notable.call(name) && !public_landmark?(name)
      end

      # Surnames this document has already established belong to a public figure.
      #
      # The observation is narrow and it is free: if a document writes "Richard
      # Wright" somewhere, and the gazetteer keeps "Richard Wright", then a bare
      # "Wright" elsewhere in *that document* is that person. Literary-analysis
      # convention makes this the dominant shape of the problem — a student names
      # the author once and writes the surname for the rest of the essay. On the
      # 27 un-scrubbed student essays the shipped arm masked "Wright" or
      # "Wright's" 27 times in a single document that also contained "Richard
      # Wright's".
      #
      # What it deliberately cannot do: corroborate from a name the gazetteer
      # does *not* keep. A student's own "Terrence Okonkwo" establishes nothing,
      # so bare "Okonkwo" still redacts.
      #
      # @param tier restricts corroboration to human full names, see
      #   {CORROBORATING_TIER}. Strongly recommended: without it a kept *place*
      #   can license a surname, which is a measured defect and not a
      #   hypothetical one. Absent, landmark-shaped names are excluded as a
      #   partial substitute and the rest of the place tier is not.
      def corroborated_surnames(candidates, notable, keep = Set.new, tier = nil)
        lowered_keep = Set.new(keep.map(&:downcase))
        out = Set.new
        candidates.each do |candidate|
          name = candidate.text
          next if words(name).length < 2
          next unless establishes?(name, notable, lowered_keep, tier)

          surname_forms(name).each { |form| out << form }
        end
        out
      end

      # Every bare token of every notable full name `text` establishes.
      #
      # `"Narciso Rodriguez's memoir"` yields `{"narciso", "rodriguez"}`. The
      # **first** name is included, which is exactly what {.surname_forms}
      # refuses to do, so the difference has to be justified rather than assumed.
      #
      # {.surname_forms} is for the INBOUND pass, over prose a student wrote,
      # where a bare first name is the commonest private surface form there is.
      # That argument does not survive the trip to the outbound pass, and the
      # reason is structural rather than a judgement call: **outbound text was
      # generated from already-redacted input.** A classmate named Narciso was
      # masked on the way in, so the model never saw the token and cannot have
      # written it back. The only "Narciso" that can appear in feedback about
      # this essay is the one the essay kept.
      #
      # That is conditional on the pipeline shape — inbound first, outbound over
      # text derived only from the inbound result. A host that redacts outbound
      # text from some *other* source must not feed it this set.
      #
      # Only multi-token names contribute. A mononym is already the bare form and
      # establishes nothing new.
      def established_name_tokens(text, notable, keep = Set.new, tier = nil)
        lowered_keep = Set.new(keep.map(&:downcase))
        out = Set.new
        find_candidates(text).each do |candidate|
          name = candidate.text
          next if words(name).length < 2
          next unless establishes?(name, notable, lowered_keep, tier)

          surname_tokens(name).each do |token|
            out << token if token.length > 1 && !PARTICLE_SET.include?(token)
          end
        end
        out
      end

      # ---------------------------------------------------------------------
      # Generation
      # ---------------------------------------------------------------------

      # Names written in lowercase, seeded on the gazetteer's given-name tier.
      #
      # A given-name hit says "a person is being named", which inbound means
      # redact. But a hit on its own is not enough to fire on, and this is the
      # whole design problem: plenty of common given names are also ordinary
      # English words — hope, grace, mark, rose, art, may — so a single lowercase
      # hit in prose is indistinguishable from prose. Firing on one token would
      # put the given-name tier's 10,469 entries directly into the over-firing
      # number.
      #
      # So a span has to reach a second adjacent token that is not stoplisted,
      # which is the given-name-plus-surname shape ("terrence okonkwo"). The cost
      # is a bare lowercase first name ("terrence and i stayed up late") which
      # this route does not reach; the benefit is that "i had hope that day"
      # stops at the stopword and emits nothing.
      #
      # Adjacency is strict: only whitespace may sit between two tokens of one
      # span. "terrence, my cousin" therefore stops at the comma and drops to one
      # token. The span reaches exactly one token past the seed — a surname — and
      # a third only across a name particle ("maria de cruz"). Reaching two
      # ordinary tokens masks "terrence okonkwo showed" out of "then terrence
      # okonkwo showed up", because the stoplist is a few hundred words and
      # English is not.
      #
      # A seed sitting directly after a determiner is dropped: see {DETERMINERS}.
      # That is where most of the remaining over-firing lives, and it is
      # structural rather than a word list.
      #
      # @param corroborate how {.capitalisation_habit} participates without being
      #   a kill switch. In a document that marks its proper nouns with capitals
      #   a lowercase token is weak evidence, so the seed must additionally
      #   appear *capitalised mid-sentence somewhere in the same document* — the
      #   writer's own testimony that this particular word is a name they
      #   sometimes slip on. Passing nil means the document supplies no
      #   capitalisation signal, and the seed stands on the given-name tier
      #   alone.
      def find_lowercase_candidates(text, is_given, protected_span, corroborate = nil,
                                    settlement = nil)
        tokens = each_match(text, LOWER_TOKEN).map do |m|
          [m[0], m.begin(0), m.begin(0) + m[0].length]
        end
        out = []
        index = 0
        while index < tokens.length
          word, start, = tokens[index]
          if stop?(word) || !is_given.call(word)
            index += 1
            next
          end
          if !corroborate.nil? && !corroborate.include?(strip(word, "'’"))
            index += 1
            next
          end
          if index.positive? && DETERMINERS.include?(tokens[index - 1][0])
            # Only a directly-adjacent determiner counts. "the day terrence
            # arrived" must stay reachable, and punctuation between the two means
            # they are not one noun phrase.
            preceding = text[tokens[index - 1][2]...start]
            if !preceding.empty? && preceding.strip.empty?
              index += 1
              next
            end
          end
          reach = index
          while reach + 1 < tokens.length
            break if reach > index && !PARTICLE_SET.include?(tokens[reach][0])

            next_word, next_start, = tokens[reach + 1]
            gap = text[tokens[reach][2]...next_start]
            break if gap.empty? || !gap.strip.empty? || next_word.length < 2 || stop?(next_word)

            reach += 1
          end
          # A span may not end on a particle: "maria de," is the name plus a
          # fragment of the next clause, and masking the fragment is a visible
          # defect on the outbound path.
          reach -= 1 while reach > index && PARTICLE_SET.include?(tokens[reach][0])
          span_end = tokens[reach][2]
          if reach - index + 1 < LOWERCASE_MIN_TOKENS || protected_span.call(start, span_end)
            index += 1
            next
          end
          joined = text[start...span_end]
          out << Candidate.new(joined, start, span_end, classify(words(joined), settlement))
          index = reach + 1
        end
        out
      end

      # Every name-shaped span, before any notability decision.
      #
      # High recall and deliberately poor precision — precision is what the
      # notability filter buys. Offsets are into `text`.
      #
      # Options, all optional:
      # * `:given_name` — turns on the lowercase route. Absent, this keys on
      #   capitalisation alone and misses lowercase writing by construction.
      # * `:title`, `:title_prefix` — protect work titles and fictional-character
      #   names from generation entirely. Absent, a student writing about a book
      #   has the book redacted.
      # * `:settlement` — types a masked span `{LOCATION}` instead of `{NAME}`.
      #   Changes no verdict — it cannot make a span keep or stop a span masking,
      #   only relabel one that was already going to be masked.
      # * `:headings_are_orthographic` — treat a section heading's capitals as
      #   required by title case rather than chosen by the writer. On by default.
      # * `:title_relation_refusal` — withdraw title protection from a span with
      #   a first-person relation attached to it — "My neighbor Alice Adams". The
      #   protection is applied *here*, before generation, so the refusal has to
      #   be applied here too; the notability gate on the masking side is the
      #   second half of the same rule and neither half works alone.
      def find_candidates(text, options = {})
        given_name = options[:given_name]
        title = options[:title]
        title_prefix = options[:title_prefix]
        settlement = options[:settlement]
        headings_are_orthographic = options.fetch(:headings_are_orthographic, true)
        title_relation_refusal = options.fetch(:title_relation_refusal, true)

        blocked = each_match(text, PROTECTED).map { |m| [m.begin(0), m.begin(0) + m[0].length] }
        starts = sentence_starts(text)
        emphasis = emphasis_spans(text)
        headings = headings_are_orthographic ? heading_spans(text) : []
        # Read before the title pass, because the title pass needs it. The habit
        # is a property of the whole document, so it is computed once and every
        # consumer reads the same verdict — which two separate booleans could not
        # guarantee.
        habit = capitalisation_habit(text, headings)
        unless title.nil?
          title_spans = find_title_spans(text, title, title_prefix,
                                         requires_capital: marks_proper_nouns?(habit))
          if title_relation_refusal
            title_spans = title_spans.reject do |s, e|
              names_someone_the_writer_knows?(text, s, e) ||
                # ...or the title is itself a relation phrase the writer is using
                # literally. The document's capitalisation signal answers this,
                # EXCEPT on a document too short to have one — where the span's
                # own mixed case answers it instead, and the missing answer used
                # to ship a cousin's name.
                ((marks_proper_nouns?(habit) ||
                  relation_led_title_is_internally_mixed?(text, s, e)) &&
                 title_is_the_writers_own_relation?(text, s, e))
            end
          end
          blocked.concat(title_spans)
        end

        is_protected = lambda do |start, finish|
          blocked.any? { |block_start, block_end| start < block_end && finish > block_start }
        end

        written_as_a_capital = mid_sentence_capitals(text, starts, headings)

        out = []
        each_match(text, CANDIDATE_RE) do |m|
          span = m[0]
          next if is_protected.call(m.begin(0), m.begin(0) + span.length)

          tokens = words(span)
          # A long all-caps run means the capitalisation told us nothing, so the
          # stoplist is carrying the whole decision.
          trim(tokens).each do |run|
            next if run.empty?

            joined = run.join(" ")
            # Locate the run inside the original span so offsets stay exact.
            offset = span.index(joined)
            next if offset.nil?

            start = m.begin(0) + offset
            next if is_protected.call(start, start + joined.length)
            # Requiring a second signal is only sound when there is a second
            # signal to require, which is why this is reached only where an
            # oracle exists.
            if !given_name.nil? &&
               suppressed_as_an_unevidenced_capital?(run, start, starts, emphasis, headings,
                                                     written_as_a_capital, given_name)
              next
            end

            # A *trailing* apostrophe is the closing quote, not part of the name.
            # The candidate pattern treats `'` as a name character so O'Brien
            # survives, which also means "words like 'Terrence'" arrives as
            # `Terrence'` — and masking that ate the quote. Possessives are
            # untouched because they end in `s`. The one case this trims wrongly
            # is a plural possessive ("the Smiths'"), which reads `the {NAME_1}'`
            # — cosmetically odd, against a defect that unbalances a quotation in
            # text a student reads.
            finish = joined.length
            finish -= 1 while finish.positive? && ["'", "’"].include?(joined[finish - 1])
            masked_text = joined[0, finish]
            next if masked_text.empty?

            out << Candidate.new(masked_text, start, start + masked_text.length,
                                 classify(run, settlement))
          end
        end

        unless given_name.nil?
          # The capitalised route claimed first, so a lowercase span overlapping
          # one it already found is dropped rather than merged: two candidates
          # over the same characters would mask the outer one and leave the inner
          # placeholder's braces as debris.
          claimed = out.map { |candidate| [candidate.start, candidate.end] }
          # nil here is the permissive path: "no capitalisation signal, so the
          # given-name tier stands alone". Exactly one of the four habits reaches
          # it. It is NOT reached on the mere absence of capitals — absence is
          # what a text with no names in it looks like, and reading its silence
          # as consent is what put "line circles" in front of a student — and it
          # is not reached by the INCONSISTENT writer either, who has per-token
          # evidence to offer and is better served by it.
          find_lowercase_candidates(
            text, given_name, is_protected,
            habit == LOWERCASE ? nil : written_as_a_capital, settlement
          ).each do |candidate|
            next if claimed.any? { |s, e| candidate.start < e && candidate.end > s }

            out << candidate
          end
        end
        out
      end

      # Mask every candidate the notability filter does not keep.
      #
      # Returns `[masked_text, count]`.
      #
      # The order of the four gates is the policy, and each one is the exception
      # to the one before it: the prompt's own keeps win outright, then the
      # precedence table decides mask-or-keep, then the notability oracle keeps a
      # public figure *unless* a first-person relation is attached to the name,
      # then a document-established surname keeps *unless* the sentence says this
      # one is somebody the writer knows.
      #
      # Options are {.find_candidates}'s, plus:
      # * `:notable` — returns true for a public figure. Absent, nothing is kept,
      #   which is the recall-maximal, precision-minimal posture.
      # * `:keep` — exact strings to keep regardless, case-insensitively.
      # * `:corroborate` — keep a bare surname when the same document also writes
      #   a full name the oracle keeps. No effect without `:notable`.
      # * `:notability_tier` — which tier vouched for a name. Needed by
      #   `:title_relation_refusal`: the boolean oracle cannot say, and
      #   overriding every tier would redact "my hero Abraham Lincoln".
      # * `:minter` — numbers the placeholders so masking is reversible. Shared
      #   with the caller's identity and structured passes so indices do not
      #   collide across them.
      # * `:relation_refusal` — refuse corroboration for a bare surname whose
      #   local context marks it as someone in the writer's life.
      def mask_candidates(text, options = {})
        notable = options[:notable]
        keep = options[:keep] || Set.new
        settlement = options[:settlement]
        corroborate = options.fetch(:corroborate, true)
        notability_tier = options[:notability_tier]
        minter = options[:minter]
        relation_refusal = options.fetch(:relation_refusal, true)
        title_relation_refusal = options.fetch(:title_relation_refusal, true)

        lowered_keep = Set.new(keep.map(&:downcase))
        candidates = find_candidates(text, options)
        established =
          if corroborate && !notable.nil?
            corroborated_surnames(candidates, notable, keep, notability_tier)
          else
            Set.new
          end

        out = text
        count = 0
        # Right to left so earlier offsets stay valid as the text shrinks. The
        # sort must be STABLE and ties must NOT be reversed, which is what keeps
        # the minter handing out the same indices as the reference — Ruby's
        # `sort_by` is not stable, so the original position rides along as the
        # tiebreaker.
        ordered = candidates.each_with_index.sort_by { |c, i| [-c.start, i] }.map(&:first)
        ordered.each do |candidate|
          name = candidate.text
          # The possessive folds into the keep, for the same reason it folds into
          # corroboration: literary analysis writes "Wright's" far more often
          # than "Wright", and a keep list that only matched the citation form
          # would miss the shape students actually use.
          next if lowered_keep.include?(name.downcase) ||
                  lowered_keep.include?(surname_tokens(name).join(" "))
          # The table decides keep-or-mask, and it is the only thing that does. A
          # bare landmark-suffix test here kept 383 real settlements — a
          # student's hometown leaked whenever it was named after a park, lake,
          # valley or falls.
          next unless resolve(classify_tags(words(name), settlement)).mask

          if !notable.nil? && notable.call(name)
            # ...unless a work title is standing in for a person the writer
            # knows. "Alice Adams" is a 1921 novel and also 589 real people's
            # names in this tier alone; no threshold separates them from the
            # curriculum, so the separation has to come from the sentence.
            overridden = title_relation_refusal && !notability_tier.nil? &&
                         OVERRIDABLE_TIERS.include?(notability_tier.call(name)) &&
                         names_someone_the_writer_knows?(text, candidate.start, candidate.end)
            next unless overridden
          end

          # Only the bare form corroborates. "Coach Wright" and "Priya Wright"
          # stay masked even where "Wright" is established.
          bare = bare_surname_key(name)
          if !established.empty? && !bare.nil? && established.include?(bare)
            # ...unless the local context says this one is someone in the
            # writer's life who happens to share the surname. Corroboration is a
            # document-level inference and this is the sentence-level exception
            # to it; without it a neighbour named Robinson is protected by Jackie
            # Robinson's fame.
            refused = relation_refusal &&
                      names_someone_in_the_writers_life?(text, candidate.start, candidate.end)
            next unless refused
          end

          placeholder = minter.nil? ? placeholder_for(candidate.kind) : minter.mint(candidate.kind, name)
          out = out[0, candidate.start] + placeholder + out[candidate.end..].to_s
          count += 1
        end
        [out, count]
      end
    end
  end
end
