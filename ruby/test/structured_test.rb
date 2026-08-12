# frozen_string_literal: true

# The structured pass, the minter, and identity interpolation.
#
# The counterpart of `typescript/test/structured.test.ts`, and this port had no
# equivalent until now. `structured.rb` and `minter.rb` were reached only through
# `Vicary.redact` — by the 54 conformance frames, which carry PHONE, EMAIL,
# ADDRESS, URL, AGE, SSN, USERNAME and CARD spans, and by the `structured` probe
# in `conformance/probes.json`.
#
# That is genuine coverage and it is the wrong shape. A Luhn regression, a
# never-issued SSN range going live, or the pattern tables being reordered fails
# as a diff in somebody else's frame, naming a fixture case rather than the rule
# that broke. Every test here names one behaviour, so the failure says which.
#
# The declared gap this closes is recorded in `conformance/coverage.json`, and
# `mechanism/tests/test_coverage_parity.py` fails if its entry outlives it.

require "minitest/autorun"
require "set"

require "vicary"

class StructuredTest < Minitest::Test
  S = Vicary::Structured

  Person = Struct.new(:first_name, :last_name, :school_name, keyword_init: true)

  # The fixture identity every reference arm interpolates.
  IDENTITY = Person.new(first_name: "Marguerite", last_name: "Delacroix-Whitfield",
                        school_name: "Westfield High School").freeze

  def surname_only(surname)
    Person.new(first_name: "", last_name: surname, school_name: "")
  end

  # -------------------------------------------------------------------------
  # Numbering — the property the golden layer exists to pin
  # -------------------------------------------------------------------------

  def test_the_same_original_always_mints_the_same_placeholder
    # A name written five times masks to one placeholder, not five. Beyond
    # restorability: a scoring model reading "{NAME_1} argued … {NAME_1}
    # concluded" sees one person doing two things.
    minter = Vicary::PlaceholderMinter.new
    assert_equal "{NAME_1}", minter.mint("NAME", "Terrence")
    assert_equal "{NAME_1}", minter.mint("NAME", "Terrence")
    assert_equal "{NAME_2}", minter.mint("NAME", "Marisol")
    assert_equal "{NAME_1}", minter.mint("NAME", "Terrence")
  end

  def test_distinct_originals_never_share_a_placeholder
    # Injectivity is what makes restore well-defined. Without it one token means
    # "Marisol" in one paragraph and "Terrence" in the next, and no map keyed on
    # the token can put either back.
    minter = Vicary::PlaceholderMinter.new
    seen = Set.new
    %w[A B C D].each do |name|
      token = minter.mint("NAME", name)
      refute_includes seen, token, token
      seen << token
    end
  end

  def test_each_kind_counts_independently
    minter = Vicary::PlaceholderMinter.new
    assert_equal "{NAME_1}", minter.mint("NAME", "x")
    assert_equal "{PHONE_1}", minter.mint("PHONE", "y")
    assert_equal "{NAME_2}", minter.mint("NAME", "z")
  end

  def test_indices_follow_mint_order_not_position_in_the_text
    # The property a port is most likely to get wrong, because left-to-right
    # numbering passes every leak check, every keep check and every semantic
    # expectation while emitting a restoration mapping that is wrong. Here the
    # later-positioned span is minted first and therefore numbered first.
    minter = Vicary::PlaceholderMinter.new
    assert_equal "{NAME_1}", minter.mint("NAME", "second-in-text")
    assert_equal "{NAME_2}", minter.mint("NAME", "first-in-text")
  end

  def test_numbering_off_reproduces_the_unnumbered_output
    minter = Vicary::PlaceholderMinter.new(number: false)
    assert_equal "{NAME}", minter.mint("NAME", "Terrence")
    assert_equal "{NAME}", minter.mint("NAME", "Marisol")
    assert_empty minter.assigned
  end

  def test_the_restore_map_puts_the_exact_bytes_back
    original = "Call Marguerite at 555-123-4567 or a@b.com."
    masked, _n, map = Vicary.redact_with_report(original, IDENTITY)
    refute_equal original, masked
    assert_equal original, Vicary.restore(masked, map)
  end

  def test_restore_is_not_confused_by_a_placeholder_that_prefixes_another
    # {NAME_1} must not be partially consumed while {NAME_11} is pending, which is
    # why restore works longest-first.
    map = { "{NAME_1}" => "Ann", "{NAME_11}" => "Bea" }
    assert_equal "Bea and Ann", Vicary.restore("{NAME_11} and {NAME_1}", map)
  end

  def test_restore_returns_a_name_containing_a_backslash_unaltered
    # Ruby-specific, and the reason `restore` uses the block form of `gsub`: a
    # replacement *string* interprets `\1`, `\&` and `\\`, so this name would come
    # back mangled from the two-argument form while every ASCII name round-tripped.
    map = { "{NAME_1}" => 'Van\\Dyke' }
    assert_equal 'Van\\Dyke was here.', Vicary.restore("{NAME_1} was here.", map)
  end

  # -------------------------------------------------------------------------
  # Structured entities
  # -------------------------------------------------------------------------

  def test_a_card_is_masked_only_when_it_passes_luhn
    # An un-checked pattern this loose eats any long number a student writes.
    assert S.luhn_ok?("4111111111111111")
    refute S.luhn_ok?("4111111111111112")
    assert_match(/\{CREDIT_DEBIT_CARD_NUMBER_1\}/,
                 Vicary.redact("She read out 4111 1111 1111 1111.", IDENTITY))
    assert_equal "She read out 4111 1111 1111 1112.",
                 Vicary.redact("She read out 4111 1111 1111 1112.", IDENTITY)
  end

  def test_never_issued_ssn_ranges_are_not_masked
    # So dates and score ranges don't trip it.
    ["000-12-3456", "666-12-3456", "900-12-3456", "123-00-6789", "123-45-0000"].each do |bad|
      assert_equal "The form said #{bad}.",
                   Vicary.redact("The form said #{bad}.", IDENTITY), bad
    end
    assert_match(/\{US_SOCIAL_SECURITY_NUMBER_1\}/,
                 Vicary.redact("The form said 123-45-6789.", IDENTITY))
  end

  def test_an_address_needs_a_street_suffix
    # A bare number-plus-words pattern has an unacceptable false-positive rate in
    # prose, which is what the suffix list is for.
    unchanged = "I ran 3 miles down the road and nothing happened."
    assert_equal unchanged, Vicary.redact(unchanged, IDENTITY)
    assert_match(/\{ADDRESS_1\}/,
                 Vicary.redact("We moved to 1428 Elm Street that fall.", IDENTITY))
  end

  def test_upstream_anonymization_markers_survive
    # Text arriving with these has already been redacted, so masking them again
    # destroys information while adding none — and rewriting @PERSON1 to
    # {USERNAME} moves every essay in the evaluation corpus off the distribution
    # the scoring model was trained on.
    text = "Upstream markers @PERSON1 and @CAPS2 must survive."
    assert_equal text, Vicary.redact(text, IDENTITY)
    assert_match(/\{USERNAME_1\}/,
                 Vicary.redact("My handle is @terrence_o now.", IDENTITY))
  end

  def test_a_bare_ten_digit_number_is_not_assumed_to_be_a_phone
    text = "Her number is 5551234567 with no separators."
    assert_equal text, Vicary.redact(text, IDENTITY)
  end

  def test_the_age_pattern_masks_the_digits_and_leaves_the_prose
    # "I am … years old" is the student's own writing and has to survive; only the
    # number is PII.
    assert_equal "I am {AGE_1} years old and this is the first thing I finished.",
                 Vicary.redact(
                   "I am 14 years old and this is the first thing I finished.", IDENTITY
                 )
  end

  # -------------------------------------------------------------------------
  # Identity interpolation
  # -------------------------------------------------------------------------

  def test_the_full_name_is_masked_as_one_span_not_two
    # Ordered most-specific-first, so "Marguerite Delacroix-Whitfield" becomes one
    # {NAME} rather than two adjacent placeholders.
    assert_equal "{NAME_1} wrote this.",
                 Vicary.redact("Marguerite Delacroix-Whitfield wrote this.", IDENTITY)
  end

  def test_the_roster_order_is_matched_too
    assert_equal "{NAME_1} is the roster order.",
                 Vicary.redact("Delacroix-Whitfield, Marguerite is the roster order.",
                               IDENTITY)
  end

  def test_a_possessive_is_masked_with_the_name
    # `\b` alone mis-handles a trailing apostrophe-s, which is exactly how a name
    # appears in student prose.
    assert_match(/\A\{NAME_1\} essay/,
                 Vicary.redact("Marguerite's essay was late.", IDENTITY))
  end

  def test_a_curly_possessive_is_masked_with_the_name
    # A word processor turns every apostrophe curly, so this is the common form,
    # not the exotic one. It missed in every port until the Python pattern's
    # duplicated `'s` branch became the curly form it always resembled.
    assert_equal "{NAME_1} essay was late.",
                 Vicary.redact("Marguerite’s essay was late.", IDENTITY)
  end

  def test_a_plural_family_possessive_is_masked_with_the_name
    # "the Delacroix-Whitfields' house" — the `s'` branch the pattern always
    # carried and could never reach, because the boundary after its apostrophe had
    # no word character to hold on to.
    ["I went to the Delacroix-Whitfields' house.",
     "I went to the Delacroix-Whitfields’ house."].each do |text|
      assert_equal "I went to the {NAME_1} house.", Vicary.redact(text, IDENTITY), text
    end
  end

  def test_the_school_acronym_is_matched_case_sensitively
    # Lowercasing it would match ordinary words; three-letter acronyms shaped like
    # "was"/"his" are a real hazard.
    assert_equal "WHS", S.school_acronym("Westfield High School")
    assert_equal "LHS", S.school_acronym("Lincoln High School")
    assert_nil S.school_acronym("Bay School") # two letters is too short
    assert_match(/\{SCHOOL_1\}/, Vicary.redact("I go to WHS on the east side.", IDENTITY))
    lower = "I said whs and meant nothing by it."
    assert_equal lower, Vicary.redact(lower, IDENTITY)
  end

  def test_an_ambiguous_given_name_is_left_alone_standing_on_its_own
    # "Will you go", "a Grace period" — a bare first-name match on one of these
    # destroys prose. The full name and the surname still mask.
    patterns = S.identity_patterns(
      Person.new(first_name: "Grace", last_name: "Okonkwo", school_name: "")
    )
    assert_equal 3, patterns.size # full, roster, surname — no bare "Grace"

    text = "We had a Grace period before the deadline."
    identity = Person.new(first_name: "Grace", last_name: "Okonkwo",
                          school_name: IDENTITY.school_name)
    assert_equal text, Vicary.redact(text, identity, names: Vicary::NAMES_IDENTITY)
    # Scoped to the identity level on purpose: this pins the identity ARM, and at
    # the shippable level candidate generation reaches "Grace" on its own and masks
    # it. Both readings are the reference's — checked against Python at both levels
    # — so the level has to be named rather than assumed.
    assert_equal "We had a {NAME_1} period before the deadline.",
                 Vicary.redact(text, identity)
  end

  def test_an_ambiguous_surname_is_left_alone_too
    patterns = S.identity_patterns(
      Person.new(first_name: "Terrence", last_name: "Young", school_name: "")
    )
    assert_equal 3, patterns.size # full, roster, given — no bare "Young"
  end

  def test_an_empty_identity_contributes_no_patterns
    # A caller that knows nothing still gets the structured entities.
    assert_empty S.identity_patterns(Person.new(first_name: "", last_name: "",
                                                school_name: ""))
    assert_equal "Call me at {PHONE_1}.",
                 Vicary.redact("Call me at 555-123-4567.",
                               Person.new(first_name: "", last_name: "", school_name: ""))
  end

  def test_an_identity_that_answers_nothing_is_not_an_error
    # Ruby-specific: `identity_field` reaches for the accessor with `respond_to?`
    # rather than assuming a Struct, so a host passing its own object with none of
    # the three readers gets the structured pass rather than a NoMethodError.
    assert_equal "Call me at {PHONE_1}.",
                 Vicary.redact("Call me at 555-123-4567.", Object.new)
  end

  def test_a_literal_with_regex_metacharacters_is_escaped
    # A surname is user data. "O'Brien (Jr.)" must not compile as a group.
    #
    # The escaped STRING differs across the three ports and the behaviour must
    # not. Ruby's `Regexp.escape` and Python's `re.escape` both escape the space
    # — `O'Brien\ \(Jr\.\)` — while TypeScript escapes narrower and leaves it
    # bare, because `\ ` is not a valid escape under the `u` flag. An escaped
    # space and a literal space match the same character outside extended mode,
    # so the three patterns are different spellings of one matcher. Asserted here
    # rather than shared, since a spelling is exactly what a port may not copy.
    assert_equal "O'Brien\\ \\(Jr\\.\\)", Regexp.escape("O'Brien (Jr.)")
    S.word_pattern("O'Brien (Jr.)") # must not raise
    assert_equal "{NAME_1} was here.",
                 Vicary.redact("O'Brien was here.", surname_only("O'Brien"))
  end

  def test_a_literal_ending_in_punctuation_is_masked
    # Roster data arrives suffixed, and a trailing `\b` can never hold after a
    # closing paren — so this literal used to mask nothing at all, in every port,
    # while looking configured. The boundary is now asserted only on the side that
    # has a word character to assert against.
    assert_equal "{NAME_1} was here.",
                 Vicary.redact("O'Brien (Jr.) was here.", surname_only("O'Brien (Jr.)"))
  end

  def test_dropping_that_boundary_does_not_widen_an_ordinary_literal
    # The guard on the fix above: the assertion is dropped per-side, not
    # unconditionally, so a name that *can* be bounded still is.
    unchanged = "I grew up near Okonkwoville."
    identity = surname_only("Okonkwo")
    assert_equal unchanged,
                 Vicary.redact(unchanged, identity, names: Vicary::NAMES_IDENTITY)
    # "Okonkwoville" is a capitalised span no oracle keeps, so the shippable level
    # masks it as a name — which is over-firing on a coined place, not the boundary
    # widening this test guards. Naming the level keeps the two apart.
    assert_equal "I grew up near {NAME_1}.", Vicary.redact(unchanged, identity)
  end

  def test_a_hyphenated_surname_survives_escaping
    # Python's re.escape writes `\-`; TypeScript cannot use that under the u flag
    # and escapes narrower. Ruby's Regexp.escape is a third spelling again, and the
    # behaviour must be identical in all three.
    assert_equal "{NAME_1} lent me her notes.",
                 Vicary.redact("Delacroix-Whitfield lent me her notes.", IDENTITY)
  end

  # -------------------------------------------------------------------------
  # The identity-pattern cache
  # -------------------------------------------------------------------------

  def test_two_identities_with_the_same_fields_share_compiled_patterns
    # The cache is keyed on the field VALUES, so this is a hit across two distinct
    # objects — which is the case that makes it worth having, since a host builds a
    # fresh identity per request.
    S.reset_identity_cache
    a = S.identity_patterns(IDENTITY)
    b = S.identity_patterns(Person.new(first_name: IDENTITY.first_name,
                                       last_name: IDENTITY.last_name,
                                       school_name: IDENTITY.school_name))
    assert_same a, b
  end

  def test_a_mutated_identity_object_does_not_serve_the_previous_students_patterns
    # The failure the cache must not have, and the reason it is not keyed on the
    # object: a host reusing one mutable struct per request would otherwise redact
    # the second student's essay against the first student's name — a privacy
    # failure wearing a stale cache's clothes.
    S.reset_identity_cache
    reused = Person.new(first_name: "Marisol", last_name: "Okonkwo", school_name: "")
    Vicary.redact("Marisol Okonkwo wrote this.", reused)

    reused.first_name = "Terrence"
    reused.last_name = "Pritchard"
    assert_equal "{NAME_1} wrote this.",
                 Vicary.redact("Terrence Pritchard wrote this.", reused,
                               names: Vicary::NAMES_IDENTITY)
    # And the previous student's name is no longer being masked out of prose it
    # has nothing to do with.
    assert_equal "Marisol Okonkwo wrote this.",
                 Vicary.redact("Marisol Okonkwo wrote this.", reused,
                               names: Vicary::NAMES_IDENTITY)
  end

  # -------------------------------------------------------------------------
  # Ordering — reordering the tables changes the bytes even when it changes no
  # verdict, so the order is pinned here
  # -------------------------------------------------------------------------

  def test_identity_runs_before_the_loose_structured_patterns
    # An address line can otherwise swallow a surname, and a name half-eaten by
    # another pattern leaks the remainder.
    _masked, _n, map = Vicary.redact_with_report(
      "Marguerite lives at 1428 Elm Street.", IDENTITY
    )
    assert_equal ["{NAME_1}", "{ADDRESS_1}"], map.keys
  end

  def test_email_and_url_are_claimed_before_identity_not_after
    # The other side of the ordering, and the direction that was wrong: a
    # school-issued address IS the writer's name, so identity interpolation
    # running first shredded it into `{NAME_2}.{NAME_1}{USERNAME_1}.k12.oh.us` —
    # the wrong number of placeholders, of the wrong kinds, with the domain tail
    # left in the clear and the span unrestorable. Both patterns are anchored on
    # structure a name cannot supply, so claiming them first cannot cost a surname
    # in prose.
    original = "I sent it to marguerite.delacroix-whitfield@westfieldhigh.k12.oh.us " \
               "by mistake."
    masked, _n, map = Vicary.redact_with_report(original, IDENTITY)
    assert_equal "I sent it to {EMAIL_1} by mistake.", masked
    assert_equal ["{EMAIL_1}"], map.keys
    # And the round trip the shredded form could not survive.
    assert_equal original, Vicary.restore(masked, map)

    url = "My page is https://westfieldhigh.k12.oh.us/students/" \
          "marguerite-delacroix-whitfield now."
    masked, _n, map = Vicary.redact_with_report(url, IDENTITY)
    assert_equal "My page is {URL_1} now.", masked
    assert_equal ["{URL_1}"], map.keys
  end

  def test_a_phone_is_claimed_before_the_bare_digit_patterns
    # ZIP and AGE run last precisely so they cannot claim characters belonging to a
    # phone, SSN, card or street address.
    assert_equal "Reach me at {PHONE_1} today.",
                 Vicary.redact("Reach me at 330-555-0142 today.", IDENTITY)
  end

  def test_email_is_claimed_before_phone
    # An email can contain digits that look like a phone.
    assert_equal "Write to {EMAIL_1} now.",
                 Vicary.redact("Write to a555.123.4567b@example.com now.", IDENTITY)
  end
end
