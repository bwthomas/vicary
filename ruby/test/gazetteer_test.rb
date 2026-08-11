# frozen_string_literal: true

# The notability index, and the asymmetry it exists to hold.
#
# Every assertion here is about a *keep* being granted or withheld, because that
# is the direction with teeth: notable => KEEP, everything else => REDACT. A miss
# masks a public figure, which is visible and annoying; a false keep leaks a real
# student's name, which is neither.
#
# Behavioural parity with the Python reference is checked elsewhere and more
# strongly — `test/conformance_test.rb` scores this port against the shared frame
# set, and `scripts/parity_probe.rb` diffs raw verdicts against the reference
# implementation. What this file pins is the set of properties a frame set cannot
# see, because no frame in it happens to collide.

require "json"
require "minitest/autorun"
require "set"
require_relative "../lib/vicary"

class GazetteerTest < Minitest::Test
  def index
    @index ||= Vicary::Gazetteer.load
  end

  # A fixture index over hand-built tiers, so a property can be tested without
  # depending on which real people happen to be in the shipped cut.
  def fixture(**tiers)
    asset = Vicary::Asset::Gazetteer.new(
      format: Vicary::Asset::SUPPORTED_FORMAT,
      meta: {},
      tiers: tiers.transform_keys(&:to_s).transform_values { |v| Set.new(v) },
      sha256: "fixture",
      path: "fixture"
    )
    Vicary::Gazetteer::Index.new(asset)
  end

  # --- the two tiers that must stay invisible to notability -----------------

  def test_a_common_given_name_is_not_notable
    # `given` points the OTHER way. A given-name hit is evidence the token names
    # a person, which on the inbound path means redact. Wiring it into
    # notability would readmit exactly the PII the tier exists to remove: every
    # student called Marisol would resolve notable and keep her own name.
    subject = fixture(given: ["marisol"])
    assert subject.common_given_name?("Marisol"), "the fixture tier did not load"
    assert_equal Vicary::Gazetteer::NOT_NOTABLE, subject.notability("Marisol")
    refute subject.notable?("Marisol")
  end

  def test_a_settlement_is_not_notable
    # A settlement is a student's hometown, so it must redact — that is the whole
    # reason settlements are subtracted from the place tier. This tier answers
    # the *next* question, asked only about a span already being masked: which
    # placeholder it gets.
    subject = fixture(settlement: ["akron"])
    assert subject.settlement?("Akron"), "the fixture tier did not load"
    assert_equal Vicary::Gazetteer::NOT_NOTABLE, subject.notability("Akron")
  end

  def test_the_shipped_cut_keeps_both_tiers_invisible_too
    # The fixtures above prove the code path; this proves the shipped asset
    # actually populates the tiers that path guards, so the two tests cannot both
    # pass against an asset where `given` and `settlement` are empty.
    refute_empty index.given
    refute_empty index.settlement
    assert index.common_given_name?("Marisol")
    assert index.settlement?("Akron")
    assert_equal Vicary::Gazetteer::NOT_NOTABLE, index.notability("Akron")
  end

  def test_entry_count_excludes_the_tiers_that_grant_no_keep
    # The one number that answers "how much notability does this asset carry".
    # Counting `given` or `settlement` would inflate it by ~31,000 entries that
    # can never grant a keep.
    subject = fixture(full: ["ada lovelace"], given: %w[marisol terrence],
                      settlement: %w[akron dayton])
    assert_equal 1, subject.entry_count
  end

  # --- the multi-token property that keeps ordinary words redactable --------

  def test_a_single_token_title_can_never_grant_a_keep
    # "It", "Up", "Her", "Room", "Brave" and "Cats" are all films. A single-token
    # title tier would make those ordinary words permanently notable, and notable
    # means KEEP, so the cost would land on recall.
    subject = fixture(title: ["cats", "the lion king"])
    refute subject.title?("Cats")
    assert_equal Vicary::Gazetteer::NOT_NOTABLE, subject.notability("Cats")
    assert subject.title?("The Lion King")
    assert_equal Vicary::Gazetteer::TITLE, subject.notability("The Lion King")
  end

  # --- verdict precedence ---------------------------------------------------

  def test_a_place_resolves_as_a_place_before_anything_else
    subject = fixture(place: ["washington"], short: ["washington"], given: ["washington"])
    assert_equal Vicary::Gazetteer::PLACE, subject.notability("Washington")
  end

  def test_a_demonym_resolves_after_the_short_tier
    # A token that is both should report the tier carrying notability evidence
    # rather than the one that does not.
    both = fixture(short: ["cuban"], demonym: ["cuban"])
    assert_equal Vicary::Gazetteer::ICONIC_SHORT, both.notability("Cuban")
    only = fixture(demonym: ["cuban"])
    assert_equal Vicary::Gazetteer::DEMONYM, only.notability("Cuban")
  end

  def test_a_title_resolves_last_of_all
    # "Joan of Arc" is both a person and a film. Either way the verdict is KEEP;
    # only the reported tier changes, and that tier is what eval attribution and
    # telemetry read.
    subject = fixture(full: ["joan of arc"], title: ["joan of arc"])
    assert_equal Vicary::Gazetteer::FULL_NAME, subject.notability("Joan of Arc")
  end

  def test_a_particle_led_partial_is_held_to_the_short_tier
    # "van Gogh", "de Gaulle" — a partial, not a full name, so it is held to the
    # strict short-tier threshold rather than the full tier's.
    subject = fixture(short: ["van gogh"])
    assert_equal Vicary::Gazetteer::ICONIC_SHORT, subject.notability("van Gogh")
  end

  def test_a_non_particle_multi_token_span_is_not_read_from_the_short_tier
    # The particle rule is the only way a multi-token span reaches `short`.
    # Without that guard every two-token span would be probed against a tier of
    # bare surnames, which is the shape most likely to collide with a real
    # student's name.
    subject = fixture(short: ["okonkwo bell"])
    assert_equal Vicary::Gazetteer::NOT_NOTABLE, subject.notability("Okonkwo Bell")
  end

  def test_a_candidate_is_never_split_and_tested_piecewise
    # If it were, `Priya Raghunathan-Bell` would resolve notable off `Bell` and a
    # real student's name would leak.
    subject = fixture(short: ["bell"], full: ["priya raghunathan"])
    assert_equal Vicary::Gazetteer::NOT_NOTABLE, subject.notability("Priya Raghunathan-Bell")
  end

  def test_an_honorific_is_not_stripped_before_lookup
    # Stripping the title would demote `Coach Bramwell` to a bare surname — the
    # shape most likely to collide with a public figure. The accepted cost is
    # that `President Lincoln` over-redacts.
    subject = fixture(short: ["bramwell"])
    assert_equal Vicary::Gazetteer::NOT_NOTABLE, subject.notability("Coach Bramwell")
  end

  # --- normalize ------------------------------------------------------------

  def test_normalize_drops_a_trailing_possessive
    # `Terrence's older brother` presents the name as `Terrence's`, and a lookup
    # that misses on the clitic is a leak.
    assert_equal "terrence", Vicary::Gazetteer.normalize("Terrence's")
    assert_equal "student", Vicary::Gazetteer.normalize("students'")
  end

  def test_normalize_folds_a_curly_apostrophe_before_dropping_the_possessive
    # A word processor turns every apostrophe curly. Without the smart-quote
    # mapping `Lincoln’s` folds to `lincoln s`, misses every tier, and silently
    # over-masks a notable name on the most ordinary punctuation there is.
    assert_equal "lincoln", Vicary::Gazetteer.normalize("Lincoln’s")
  end

  def test_normalize_keeps_the_punctuation_that_belongs_to_a_name
    # The apostrophe and internal hyphen belong to the name rather than
    # surrounding it.
    assert_equal "o'keeffe", Vicary::Gazetteer.normalize("O'Keeffe")
    assert_equal "raghunathan-bell", Vicary::Gazetteer.normalize("Raghunathan-Bell")
  end

  def test_normalize_strips_accents
    assert_equal "jose", Vicary::Gazetteer.normalize("José")
    assert_equal "sao paulo", Vicary::Gazetteer.normalize("São Paulo")
  end

  def test_normalize_will_not_eat_a_short_word_that_is_all_clitic
    # The length guard. Without it a bare `'s` folds to the empty string and an
    # empty key matches nothing in a way that is impossible to debug from a
    # verdict.
    assert_equal "'s", Vicary::Gazetteer.normalize("'s")
  end

  def test_an_empty_key_is_never_notable
    assert_equal Vicary::Gazetteer::NOT_NOTABLE, index.notability("   ")
    assert_equal Vicary::Gazetteer::NOT_NOTABLE, index.notability("!!!")
  end

  # --- the title scan's derived indices ------------------------------------

  def test_the_derived_title_indices_cannot_disagree_with_the_title_tier
    # Memoized functions of `title` rather than constructor arguments, precisely
    # so this is true by construction. Asserted anyway: a scan whose prefilter
    # disagrees with the tier it filters for silently stops matching.
    subject = fixture(title: ["the lion king", "to kill a mockingbird", "charlotte's web"])
    assert_equal Set.new(%w[the to charlotte's]), subject.title_heads
    assert_equal 4, subject.max_title_tokens
    assert subject.title_prefix?("the")
    assert subject.title_prefix?("the lion")
    assert subject.title_prefix?("the lion king")
    refute subject.title_prefix?("the lion king of")
    refute subject.title_prefix?("lion")
  end

  def test_max_title_tokens_is_zero_for_an_empty_tier
    assert_equal 0, fixture(title: []).max_title_tokens
  end

  # --- refusing an asset this reader does not understand --------------------

  def test_an_unknown_tier_is_refused_rather_than_ignored
    # A tier this reader drops is a tier that reads back empty, and an empty keep
    # tier redacts everything it was built to protect while looking like
    # over-aggressive tuning.
    error = assert_raises(Vicary::Gazetteer::AssetError) do
      fixture(full: ["ada lovelace"], nickname: ["ada"])
    end
    assert_match(/unknown gazetteer tier "nickname"/, error.message)
  end

  def test_every_tier_the_shipped_asset_carries_is_one_this_reader_knows
    # The other direction, and the one that actually fires when the builder gains
    # a tier: the constructor above refuses an unknown tier, so this asserts the
    # shipped cut never trips it.
    shipped = Vicary::Asset.load.tiers.keys.sort
    assert_equal shipped, (shipped & Vicary::Gazetteer::TIER_NAMES).sort,
                 "the asset carries a tier this reader does not know: " \
                 "#{(shipped - Vicary::Gazetteer::TIER_NAMES).inspect}"
    # And that the manifest agrees, so a truncated tier cannot pass as a small one.
    manifest = JSON.parse(
      Vicary::Asset.locate.join(Vicary::Asset::MANIFEST_FILENAME).read
    ).dig("assets", Vicary::Asset::ASSET_FILENAME, "tiers")
    manifest.each do |name, count|
      assert_equal count, Vicary::Asset.load.tiers.fetch(name).size,
                   "tier #{name} parsed to a different size than the manifest declares"
    end
  end
end
