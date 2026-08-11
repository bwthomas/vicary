# frozen_string_literal: true

require "json"
require "minitest/autorun"
require_relative "../lib/vicary"

class LexiconTest < Minitest::Test
  def test_the_shipped_stoplist_parses_to_its_declared_words
    words = Vicary::Lexicon.load("stop_words")
    assert_equal manifest_entry.fetch("entries"), words.size
    # Spot-checks at the two ends of the file, so a truncated read fails here and
    # not only on the count. Same two words the Python suite checks.
    assert_includes words, "the"
    assert_includes words, "favorite"
    # Case-folded on read, so a reader never has to remember to fold.
    words.each { |word| assert_equal word, word.downcase }
  end

  def test_the_shipped_stoplist_is_the_format_this_reader_claims_to_read
    # Against the manifest rather than a literal, for the reason asset_test.rb
    # states: a hand-copied expected value agrees with itself forever.
    assert_equal Vicary::Lexicon::LEXICON_FORMAT, manifest_entry.fetch("format")
  end

  def test_the_stoplist_is_read_from_the_same_directory_as_the_gazetteer
    # Not decoration: a package that finds its stoplist in one cut and its
    # gazetteer in another has two halves of two different detectors, and every
    # symptom of that is a masking decision nobody can reproduce.
    directory = Vicary::Lexicon.path("stop_words").parent
    on_path = Vicary::Asset.search_path.map { |dir| dir.expand_path.to_s }
    assert_includes on_path, directory.expand_path.to_s,
                    "stoplist resolved to #{directory}, which is not on the asset search path"
  end

  def test_a_declared_count_that_disagrees_is_an_error
    # The guard that matters most, and the one whose absence is invisible. A short
    # read makes every reader of this list *more* aggressive about what counts as a
    # name — fewer stop words means more capitalised ordinary words become
    # candidates. That looks privacy-safe, corrupts prose, and passes any check
    # that only asks whether something was masked.
    error = assert_raises(Vicary::Lexicon::LexiconError) do
      Vicary::Lexicon.parse("probe", "#!lexicon 1\n#!list probe 3\nalpha beta\n", "probe.txt")
    end
    assert_match(/declares 3 distinct words, parsed 2/, error.message)
  end

  def test_duplicates_count_once
    # The groupings in the source file overlap on purpose ("else", "may", "us").
    # Enforcing uniqueness in the source would make the list harder to read for no
    # benefit, so the count is of DISTINCT words and this is what that means.
    words = Vicary::Lexicon.parse("probe", "#!lexicon 1\n#!list probe 2\nalpha beta\nbeta alpha\n", "probe.txt")
    assert_equal ["alpha", "beta"], words.to_a.sort
  end

  def test_a_file_with_no_format_directive_is_refused
    error = assert_raises(Vicary::Lexicon::LexiconError) do
      Vicary::Lexicon.parse("probe", "#!list probe 1\nalpha\n", "probe.txt")
    end
    assert_match(/no `#!lexicon` directive/, error.message)
  end

  def test_a_format_this_build_does_not_read_is_refused
    error = assert_raises(Vicary::Lexicon::LexiconError) do
      Vicary::Lexicon.parse("probe", "#!lexicon 2\n#!list probe 1\nalpha\n", "probe.txt")
    end
    assert_match(/lexicon format \"2\", this build reads 1/, error.message)
  end

  def test_a_file_with_no_list_directive_is_refused
    error = assert_raises(Vicary::Lexicon::LexiconError) do
      Vicary::Lexicon.parse("probe", "#!lexicon 1\nalpha\n", "probe.txt")
    end
    assert_match(/no `#!list probe <count>`/, error.message)
  end

  def test_a_list_directive_naming_a_different_list_is_refused
    # The name is how a caller says which list it asked for. A reader that accepts
    # any `#!list` line will happily load the wrong file under the right name.
    error = assert_raises(Vicary::Lexicon::LexiconError) do
      Vicary::Lexicon.parse("probe", "#!lexicon 1\n#!list other 1\nalpha\n", "probe.txt")
    end
    assert_match(/expected `#!list probe <count>`/, error.message)
  end

  def test_an_unrecognised_directive_is_refused_rather_than_skipped
    # Skipping it means the file was written by something that knows more than this
    # reader, and guessing which lines are still words is how a partial list loads
    # as a whole one.
    error = assert_raises(Vicary::Lexicon::LexiconError) do
      Vicary::Lexicon.parse("probe", "#!lexicon 1\n#!list probe 1\n#!tier given 5\nalpha\n", "probe.txt")
    end
    assert_match(/unknown directive "tier"/, error.message)
  end

  def test_a_non_integer_count_is_refused_rather_than_repaired
    # JavaScript's `parseInt` reads "3x" as 3, where Python's `int()` raises. A
    # count this reader silently repaired is a count that no longer proves anything
    # about the parse, so the two languages have to refuse the same input.
    error = assert_raises(Vicary::Lexicon::LexiconError) do
      Vicary::Lexicon.parse("probe", "#!lexicon 1\n#!list probe 3x\nalpha beta\n", "probe.txt")
    end
    assert_match(/count "3x" is not an integer/, error.message)
  end

  def test_comments_and_blank_lines_contribute_no_words
    words = Vicary::Lexicon.parse(
      "probe",
      "#!lexicon 1\n#!list probe 1\n# a comment\n\n   \nalpha\n# another\n",
      "probe.txt"
    )
    assert_equal ["alpha"], words.to_a
  end

  def test_a_missing_lexicon_names_the_file_and_how_to_get_it
    error = assert_raises(Vicary::Lexicon::LexiconError) do
      Vicary::Lexicon.load("stop_words", path: File.join(__dir__, "no-such-lexicon.txt"))
    end
    assert_match(/no-such-lexicon\.txt/, error.message)
    assert_match(/rake sync_assets/, error.message)
  end

  private

  # What the manifest says about the shipped stoplist, read from whichever
  # directory the reader actually resolved — so this cannot pass by reading one
  # cut's manifest about another cut's file.
  def manifest_entry
    directory = Vicary::Lexicon.path("stop_words").parent
    manifest = JSON.parse(directory.join(Vicary::Asset::MANIFEST_FILENAME).read)
    entry = manifest.dig("assets", "stop_words.txt")
    assert entry, "the manifest does not describe stop_words.txt"
    entry
  end
end