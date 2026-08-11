# frozen_string_literal: true

# Where Ruby's regex dialect differs from Python's, pinned rather than reasoned about.
#
# The detector was written against Python `re` and ported through TypeScript.
# Ruby's defaults disagree with Python's in two ways, each a silent behaviour
# change rather than an error:
#
# * `^` and `$` mean start- and end-of-**line** in Ruby, and start- and
#   end-of-**string** in Python without `re.MULTILINE`. (`\Z` is Python's `$`;
#   `\z` is JavaScript's.)
# * `\w`, `\d` and `\s` are ASCII-only in Ruby and Unicode-aware in Python.
#
# `\b` is the one that does NOT disagree, which is worth a test of its own: the
# TypeScript port spells its boundaries out because *JavaScript's* `\b` is
# ASCII-only, and inheriting that reasoning without checking it would state
# something about Ruby that is not true.
#
# Every site is already written the Python way. This file exists because
# **neither shared spec layer catches it if somebody writes it back.** Measured,
# not assumed: mutating `\z` to `$` in `RELATION_ATTACHED_BEFORE` and `\Z` to `$`
# in `ZIP` leaves all 36 conformance frames green and all 2,526 primitive
# assertions green. The fixture corpus is single-line, and these two rules only
# diverge across a newline.
#
# So each test below asserts the divergence in both directions: that the pattern
# as written gives the reference answer, AND that the idiomatic-Ruby spelling
# gives a different one. The second half is what makes this a test rather than a
# restatement — an assertion that only pins the current answer would still pass
# if the difference evaporated, and then it would be guarding nothing.

require "minitest/autorun"
require "set"

require "vicary"

class DialectTest < Minitest::Test
  C = Vicary::Candidates
  S = Vicary::Structured

  # The same pattern with a Ruby-idiomatic anchor substituted back in.
  def loosened(pattern, from, to)
    source = pattern.source
    refute_equal source, source.sub(from, to),
                 "#{from} is no longer in this pattern, so this test is checking nothing"
    Regexp.new(source.sub(from, to), pattern.options)
  end

  def test_zip_anchors_on_the_string_not_the_line
    # `\s*$` would satisfy the ZIP lookahead at the end of every line of a
    # hard-wrapped essay, so any 5-digit number a student ends a line on — a
    # locker combination, a population, a year range — masks as `{ZIP_CODE_1}`.
    text = "I live at 12345\nMy locker combination is 90210 and I forget it."

    assert_empty text.scan(S::ZIP),
                 "a 5-digit number at a line end is not a ZIP code"
    assert_equal ["12345"], text.scan(loosened(S::ZIP, '\Z', "$")),
                 "the line-anchored spelling was expected to over-fire here; if it " \
                 "no longer does, this guard has stopped guarding anything"

    # ...and the cases a real ZIP arrives in still match, so the anchor is not
    # simply switched off.
    assert_equal ["12345"], "Send it to 12345.".scan(S::ZIP)
    assert_equal ["12345"], "12345".scan(S::ZIP)
    assert_equal ["12345"], "Akron 12345 OH".scan(S::ZIP)
  end

  def test_an_attached_relation_may_not_reach_across_a_line_break
    # The window handed to this pattern is the 90 characters before a name. With
    # `$` the relation cue need only end *a line* rather than run up to the name,
    # so "my cousin" two lines earlier attaches to a name it has nothing to do
    # with — and that wrongly overrides a title keep, or wrongly refuses
    # corroboration for a public figure's surname.
    across_a_break = "my cousin \nand then the dog ran "

    refute C::RELATION_ATTACHED_BEFORE.match?(across_a_break),
           "a relation cue on an earlier line is not attached to this name"
    assert loosened(C::RELATION_ATTACHED_BEFORE, '\z', "$").match?(across_a_break),
           "the line-anchored spelling was expected to attach across the break"

    # Genuinely adjacent still attaches, on both the possessive and the modifier
    # forms the reference accepts.
    assert C::RELATION_ATTACHED_BEFORE.match?("we walked home. my neighbor ")
    assert C::RELATION_ATTACHED_BEFORE.match?("this is my old soccer coach ")
    assert C::RELATION_ATTACHED_BEFORE.match?("our next-door neighbour ")
  end

  def test_the_appositive_and_title_patterns_anchor_at_the_window_start
    # These two are `match` rather than `search` in the reference — the relation
    # must open the window, not merely appear in it. Ruby's `^` would let it open
    # any line of the window instead.
    after = "was there.\n, my cousin, came over"

    refute C::RELATION_ATTACHED_AFTER.match?(after),
           "an appositive on a later line does not follow this name"
    assert loosened(C::RELATION_ATTACHED_AFTER, '\A', "^").match?(after),
           "the line-anchored spelling was expected to match a later line"

    assert C::RELATION_ATTACHED_AFTER.match?(", my cousin, came over")
    assert C::RELATION_ATTACHED_AFTER.match?(", who is my neighbor, came over")
    assert C::TITLE_LEADS_WITH_RELATION.match?("my cousin vinny")
    refute C::TITLE_LEADS_WITH_RELATION.match?("the film\nmy cousin vinny")
  end

  def test_the_sentence_break_anchor_is_inert_here_and_that_is_why_it_stays_strict
    # The odd one out, recorded so nobody "fixes" it in either direction. `^`
    # would report a sentence start after every newline — but the pattern's own
    # `\n+` arm already reports exactly those offsets, and `sentence_starts`
    # returns a Set, so the duplicates collapse and the answer is identical.
    #
    # It is written `\A` anyway: the equivalence is a coincidence of two arms
    # overlapping, not a property anyone should have to re-derive, and it costs a
    # duplicate match per line to rely on.
    loose = loosened(C::SENTENCE_BREAK, '\A', "^")
    ["A line.\nanother line\nthird one.", "one\n\ntwo", "x.\ny? \"Quoted\" z"].each do |text|
      strict_offsets = C.each_match(text, C::SENTENCE_BREAK).map { |m| m.begin(0) + m[0].length }
      loose_offsets = C.each_match(text, loose).map { |m| m.begin(0) + m[0].length }
      assert_equal strict_offsets.to_set, loose_offsets.to_set,
                   "the two anchors were expected to agree as SETS on #{text.inspect}"
      assert_operator loose_offsets.size, :>, strict_offsets.size,
                      "the line anchor was expected to match more often on #{text.inspect}"
    end
  end

  def test_ruby_word_boundaries_already_agree_with_python
    # Recorded because the TypeScript port's reason for spelling these out does
    # NOT apply here, and repeating it would have been a plausible-sounding lie.
    # JavaScript's `\b` is ASCII-only, so it finds a boundary between `n` and `ä`
    # that Python does not. Ruby's `\b` is Unicode-aware and agrees with Python.
    refute_match(/\bcousin\b/, "cousinä",
                 "Ruby's \\b was expected to agree with Python's here")
    refute_match(/\bve/, "naïve")

    # So the explicit lookarounds are belt-and-braces on this side rather than
    # load-bearing — and they stay anyway, because they are the form the shared
    # spec pins and `[\p{L}\p{N}_]` is the same set the gazetteer folds on.
    refute_match(Regexp.new("cousin#{C::NOT_WORD_AFTER}"), "cousinä")
    assert_match(Regexp.new("cousin#{C::NOT_WORD_AFTER}"), "my cousin came over")
    refute_match(Regexp.new("#{C::NOT_WORD_BEFORE}ve"), "naïve")
    assert_match(Regexp.new("#{C::NOT_WORD_BEFORE}ve"), "the ve token")
  end

  def test_the_shorthand_classes_are_ascii_only_which_is_the_real_divergence
    # `\w` is where Ruby actually parts company with Python, and it is why
    # NOT_WORD_BEFORE spells its class out instead of using `\w`.
    refute_match(/\A\w\z/, "ä", "Ruby's \\w was expected to be ASCII-only")
    assert_match(Regexp.new("\\A[#{C::NOT_WORD_BEFORE[4..-2]}]\\z"), "ä")

    # `\d` and `\s` diverge the same way and are deliberately left as-is,
    # matching the TypeScript port, which has the identical narrowing and
    # reproduces every frame. Asserted rather than assumed so the choice is
    # visible: if a future fixture contains a non-ASCII digit, this is the test
    # that says which way the three ports will disagree.
    refute_match(/\A\d\z/, "٣", "Python matches an Arabic-Indic digit here")
    refute_match(/\A\s\z/, " ", "Python matches a non-breaking space here")
  end
end
