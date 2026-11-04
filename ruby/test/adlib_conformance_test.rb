# frozen_string_literal: true

# Adlib against `conformance/adlib.json`, the Python reference's answers: every
# case in every layer must match byte for byte. Prints a per-layer count.

require "minitest/autorun"
require "json"
require "vicary"

class AdlibConformanceTest < Minitest::Test
  DOC = JSON.parse(Vicary::Conformance.directory.join("adlib.json").read)
  A = Vicary::Adlib
  COUNTS = Hash.new { |h, k| h[k] = [0, 0] }

  Minitest.after_run do
    puts "\nadlib conformance"
    COUNTS.sort.each { |layer, (ok, all)| puts format("  %-11s %3d / %d", layer, ok, all) }
  end

  def tally(layer, failures, total)
    COUNTS[layer] = [total - failures.size, total]
    assert_empty failures, "#{layer}: #{failures.size} of #{total} differ\n#{failures.first(5).join("\n")}"
  end

  def test_document_version = assert_equal(1, DOC["document_version"])

  def test_pools
    bad = DOC["pools"].reject do |name, want|
      bytes = A.pool_path(name).binread
      Digest::SHA256.hexdigest(bytes) == want["sha256"] && A.load_pool(name).length == want["entries"]
    end.keys
    tally("pools", bad, DOC["pools"].size)
  end

  def test_constants
    c = DOC["constants"]
    have = { "age_bands" => A::AGE_BANDS, "card_plugs" => A::CARD_PLUGS, "ip_ranges" => A::IP_RANGES,
             "max_bumps" => A::MAX_BUMPS, "phone_area_code" => A::PHONE_AREA_CODE,
             "phone_plug" => A::PHONE_PLUG, "providers" => A::PROVIDERS.to_a.sort,
             "ssn_plug" => A::SSN_PLUG, "url_reserved" => A::URL_RESERVED.to_a.sort,
             "url_shapes" => A::URL_SHAPES, "url_user_subdomains" => A::URL_USER_SUBDOMAINS.to_a.sort,
             "zip_plug" => A::ZIP_PLUG }
    bad = c.keys.reject { |k| have.key?(k) && JSON.parse(JSON.generate(have[k])) == c[k] }
    tally("constants", bad, c.size)
  end

  def test_draw
    bad = DOC["draw"].reject { |d| A.draw(*d["args"]) == d["index"] }.map { |d| d["args"].inspect }
    tally("draw", bad, DOC["draw"].size)
  end

  def test_shift_date
    bad = DOC["shift_date"].reject { |d| A.shift_date(d["date"], d["offset"]) == d["shifted"] }
                           .map { |d| "#{d['date']}+#{d['offset']} -> #{A.shift_date(d['date'], d['offset'])}" }
    tally("shift_date", bad, DOC["shift_date"].size)
  end

  def test_card_plug
    bad = DOC["card_plug"].reject { |d| A.card_plug(d["digits"], d["variant"]) == d["plug"] }.map(&:inspect)
    tally("card_plug", bad, DOC["card_plug"].size)
  end

  def test_acronym
    bad = DOC["acronym"].reject { |d| A.acronym(d["name"]) == d["acronym"] }.map(&:inspect)
    tally("acronym", bad, DOC["acronym"].size)
  end

  def self.oracles(kind)
    return {} if kind == "none"

    { stop_words: Vicary::Lexicon.stop_words,
      given_name: ->(t) { Vicary::Gazetteer.common_given_name?(t) },
      notable: ->(n) { Vicary::Gazetteer.notable?(n) } }
  end

  # A case too large to inline writes its spans as a generator:
  # `NAME 'Q{i}x Zz{i}' for i in 0..1999`, the range inclusive.
  def self.spans_of(spans)
    return spans unless spans.is_a?(Hash)

    m = /\A(\S+) '(.*)' for i in (\d+)\.\.(\d+)\z/.match(spans.fetch("generator"))
    (m[3].to_i..m[4].to_i).map { |i| [m[1], m[2].gsub("{i}", i.to_s)] }
  end

  def test_invent
    bad = DOC["invent"].filter_map do |c|
      c = c.merge("spans" => self.class.spans_of(c["spans"]))
      args = { document: c["document"], seed: c["seed"], identity: c["identity"], **self.class.oracles(c["oracles"]) }
      if c["raises"]
        begin
          A.invent(c["spans"], **args)
          next "#{c['case_id']}: did not raise"
        rescue Vicary::AdlibPoolExhausted
          next nil
        end
      end
      got = A.invent(c["spans"], **args)
      next if got.stand_ins == c["stand_ins"] && got.tokens.to_a == c["tokens"]

      "#{c['case_id']}: #{got.stand_ins.inspect} #{got.tokens.inspect}\n  want #{c['stand_ins'].inspect} " \
        "#{c['tokens'].inspect}"
    rescue StandardError => e
      "#{c['case_id']}: #{e.class}: #{e.message}"
    end
    tally("invent", bad, DOC["invent"].size)
  end

  def test_restore
    bad = DOC["restore"].filter_map do |c|
      # Maps ship as ordered [key, value] lists; restore is first-wins on that order.
      got = Vicary.restore_adlib(c["text"], c["restore_map"].to_h, c["tokens"].to_h)
      got == c["restored"] ? nil : "#{c['case_id']}: #{got.inspect}"
    end
    tally("restore", bad, DOC["restore"].size)
  end

  def test_render
    bad = DOC["render"].filter_map do |c|
      r = Vicary.render_adlib(c["placeholder_text"], c["placeholder_restore_map"].to_h, c["original"], seed: c["seed"])
      spans = r.adlib_spans.map { |s, o| [s.orig_start, s.orig_end, s.new_start, s.new_end, o] }
      ok = r.text == c["text"] && r.restore_map.to_a == c["restore_map"] && r.adlib_map.to_a == c["adlib_map"] &&
           r.adlib_tokens.to_a == c["adlib_tokens"] && spans == c["spans"] && r.original == c["original"]
      ok ? nil : "#{c['case_id']}: #{r.text.inspect}\n  want #{c['text'].inspect}"
    rescue StandardError => e
      "#{c['case_id']}: #{e.class}: #{e.message}"
    end
    tally("render", bad, DOC["render"].size)
  end

  # The port's own placeholder pass, where it already passes frames conformance,
  # reproduces the reference arm's placeholder input to render.
  def test_render_input_from_this_ports_redactor
    identity = Vicary::Conformance.load_spec.identity
    bad = DOC["render"].filter_map do |c|
      masked, _n, map = Vicary.redact_with_report(c["original"], identity)
      masked == c["placeholder_text"] && map.to_a == c["placeholder_restore_map"] ? nil : c["case_id"]
    end
    tally("render_input", bad, DOC["render"].size)
  end
end
