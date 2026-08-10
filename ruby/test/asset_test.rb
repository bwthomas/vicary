# frozen_string_literal: true

# The asset layer, and the one claim it has to earn: the same bytes.
#
# "All three front doors produce byte-identical output" is impossible if they read
# different gazetteers, so this file checks agreement against the manifest the
# Python package ships rather than against constants written here. A hand-copied
# expected tier count would agree with itself forever.

require "json"
require "minitest/autorun"
require "pathname"

require "vicary"

class AssetTest < Minitest::Test
  def manifest
    gazetteer = Vicary::Asset.load
    path = Pathname.new(gazetteer.path).dirname.join("MANIFEST.json")
    JSON.parse(path.read).dig("assets", "notability.txt.gz")
  end

  def test_the_asset_loads_at_the_format_this_reader_supports
    gazetteer = Vicary::Asset.load
    assert_equal Vicary::Asset::SUPPORTED_FORMAT, gazetteer.format
    assert_equal manifest.fetch("format"), gazetteer.format
  end

  def test_the_bytes_read_are_the_bytes_the_manifest_describes
    # This is the parity claim at its root. If this digest ever differs from the
    # Python package's, nothing downstream about identical output is checkable.
    assert_equal manifest.fetch("sha256"), Vicary::Asset.load.sha256
  end

  def test_every_tier_parses_to_the_count_the_manifest_declares
    parsed = Vicary::Asset.load.tiers.transform_values(&:size)
    # Compared as whole hashes, not tier by tier: a loop over the manifest's keys
    # would pass while the reader invented an extra tier, and a loop over the
    # reader's keys would pass while it dropped one entirely.
    assert_equal manifest.fetch("tiers"), parsed
  end

  def test_a_known_entry_resolves_in_the_tier_that_should_hold_it
    tiers = Vicary::Asset.load.tiers
    # Entries are normalised to lowercase by the builder, so lookups are on the
    # folded form. Rosa Parks is a KEEP span in the fixture; if `full` cannot
    # answer for her, every keep frame fails for a reason unrelated to detection.
    assert_includes tiers.fetch("full"), "rosa parks"
    assert_includes tiers.fetch("settlement"), "akron"
    assert_includes tiers.fetch("given"), "deshawn"
  end

  def test_an_unknown_format_is_refused_rather_than_partially_read
    error = assert_raises(Vicary::Asset::FormatError) do
      Vicary::Asset.parse("#!gazetteer 999\n#!tier full 0\n")
    end
    assert_match(/format 999 is not/, error.message)
  end

  def test_a_truncated_tier_is_refused_rather_than_silently_smaller
    # The failure this guards is asymmetric: a short read means fewer notable
    # people, which means MORE redaction, which looks privacy-safe and passes any
    # check that only asks whether something was masked.
    error = assert_raises(Vicary::Asset::FormatError) do
      Vicary::Asset.parse("#!gazetteer 5\n#!tier full 3\nabraham lincoln\n")
    end
    assert_match(/declares 3 entries and parsed 1/, error.message)
  end

  def test_a_directive_the_format_number_did_not_admit_to_is_refused
    error = assert_raises(Vicary::Asset::FormatError) do
      Vicary::Asset.parse("#!gazetteer 5\n#!tier full 1\nx\n#!newthing 1\n")
    end
    assert_match(/format changed without its number changing/, error.message)
  end

  def test_an_entry_before_any_tier_is_an_error_not_an_orphan
    error = assert_raises(Vicary::Asset::FormatError) do
      Vicary::Asset.parse("#!gazetteer 5\nabraham lincoln\n")
    end
    assert_match(/before any/, error.message)
  end

  def test_the_cache_can_be_reset_so_a_test_can_load_a_different_directory
    first = Vicary::Asset.load
    Vicary::Asset.reset_cache
    assert_equal first.sha256, Vicary::Asset.load.sha256
  end
end
