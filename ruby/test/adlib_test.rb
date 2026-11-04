# frozen_string_literal: true

# Adlib behaviour beyond conformance/adlib.json: the redactor option, its
# refusal, the env dials and the round trips.

require "minitest/autorun"
require "vicary"

class AdlibTest < Minitest::Test
  Identity = Struct.new(:first_name, :last_name, :school_name)
  ESSAY = "Maria Lopez helped me. Later Ms. Lopez said Maria's notes were the best."

  def redact_adlib(text, seed = 7)
    Vicary.redact_with_report(text, Identity.new("", "", ""), render: "adlib", adlib_seed: seed)
  end

  def test_the_redactor_renders_over_its_own_placeholders
    masked, = Vicary.redact_with_report(ESSAY, Identity.new("", "", ""))
    text, n, restore, result = redact_adlib(ESSAY)
    assert_operator n, :>, 0
    refute Vicary.has_placeholders?(text), text
    assert_equal ESSAY, result.original
    assert_equal result.restore_map, restore
    assert_equal result.adlib_map.invert, restore
    assert_equal masked.scan(Vicary::Spans::PLACEHOLDER).size, result.spans.size
  end

  def test_one_person_keeps_one_identity_and_the_seed_moves_it
    _, _, _, a = redact_adlib(ESSAY, 7)
    _, _, _, b = redact_adlib(ESSAY, 7)
    _, _, _, c = redact_adlib(ESSAY, 8)
    assert_equal a.text, b.text
    refute_equal a.text, c.text
    given = a.adlib_tokens.key("Maria")
    surname = a.adlib_tokens.key("Lopez")
    assert_includes a.text, "#{given} #{surname} helped me"
    assert_includes a.text, "Ms. #{surname} said #{given}'s notes"
  end

  def test_a_lone_token_in_a_reply_restores
    _, _, restore, r = redact_adlib(ESSAY)
    given = r.adlib_tokens.key("Maria")
    assert_equal "Maria wrote it.", Vicary.restore_adlib("#{given} wrote it.", restore, r.adlib_tokens)
    assert_equal "MARIA!", Vicary.restore_adlib("#{given.upcase}!", {}, r.adlib_tokens)
  end

  def test_adlib_refuses_without_numbered_placeholders
    assert_raises(ArgumentError) do
      Vicary.redact_with_report(ESSAY, Identity.new("", "", ""), render: "adlib", number_placeholders: false)
    end
    assert_raises(ArgumentError) { Vicary.render_adlib("{NAME} said hi", {}, "Maria said hi") }
  end

  def test_render_mode_reads_aliases_and_falls_back_to_placeholders
    assert_equal "adlib", Vicary.render_mode("Invented")
    assert_equal "placeholder", Vicary.render_mode("default")
    _out, err = capture_io { assert_equal "placeholder", Vicary.render_mode("bogus") }
    assert_match(/Unrecognised VICARY_RENDER/, err)
    assert_equal "0", Vicary.resolve_adlib_seed(nil) unless ENV.key?(Vicary::ADLIB_SEED_ENV_VAR)
    assert_equal "12", Vicary.resolve_adlib_seed(12)
  end

  def test_nothing_masked_is_returned_unchanged
    text, n, = redact_adlib("The weather was nice.")
    assert_equal ["The weather was nice.", 0], [text, n]
  end

  def test_every_given_pool_entry_is_a_common_given_name_and_no_surname_is
    Vicary::Adlib.given_pool.each { |g| assert Vicary::Gazetteer.common_given_name?(g), g }
    Vicary::Adlib.surname_pool.each { |s| refute Vicary::Gazetteer.common_given_name?(s), s }
  end
end
