# frozen_string_literal: true

require "digest"
require "json"
require "pathname"
require "set"
require "zlib"

module Vicary
  # The false-positive control the fixture cannot provide.
  #
  # The fixture reports zero leaks on bare surnames partly because the private
  # surnames in it are rare — Okonkwo, Bramwell, Pritchard, Ybarra. A clean
  # control needs an *unlikely* clean, so this scores the single-token tiers
  # against **every American surname**: the population-weighted rate at which a
  # bare surname resolves notable, regardless of whose surname it is.
  #
  # Read the headline number as: *for a private person named by bare surname
  # only — no first name, no title, no same-document corroboration — this share
  # resolves "notable" and leaks.* It is conditional on that surface form, which
  # is a minority of private-name mentions in real prose, so it is not an
  # essay-level leak rate.
  #
  # The source is the US Census 2010 surname file, and this repository now ships
  # the two columns of it this measurement uses — see `conformance/census/`,
  # built by `tools/census_build.py`. So the gate is measured on a bare checkout
  # and in CI, which it was not: census.gov stopped serving the upstream, and the
  # gate reported NOT MEASURED everywhere but on a machine holding a
  # hand-downloaded copy.
  #
  # `VICARY_EVAL_CENSUS_CSV` still wins when set — an operator holding a newer
  # release gets the number their file gives.
  #
  # **For that operator file, this port reads the extracted `.csv` only.** Python
  # additionally accepts the distributed `.zip` because its standard library has
  # a zip reader and Ruby's does not. A `.zip` here is refused by name rather
  # than parsed as text, since the alternative is a binary read that yields zero
  # rows — which is a *lower* exposure rate than the truth, and the wrong
  # direction to fail in silently. The shipped table sidesteps this entirely: it
  # is gzip, which `zlib` reads.
  module Census
    # Where a locally-held copy of the Census surname file is configured.
    EVAL_CENSUS_CSV_ENV_VAR = "VICARY_EVAL_CENSUS_CSV"

    # The member name inside the distributed archive, and the file this port
    # wants handed to it directly.
    CENSUS_SURNAMES_MEMBER = "Names_2010Census.csv"

    # Where the operator gets the file, quoted when it is missing.
    CENSUS_SURNAMES_URL = "https://www2.census.gov/topics/genealogy/2010surnames/names.zip"

    # The row-count floor. Not decoration: this list is scored *against* the
    # single-token tiers, so a short read shrinks the denominator and reports a
    # more comfortable exposure rate than the truth.
    MINIMUM_ROWS = 100_000

    # Directory under `conformance/` holding the shipped table and its provenance.
    SHIPPED_DIRNAME = "census"
    SHIPPED_TABLE_FILENAME = "surnames.txt.gz"
    SHIPPED_PROFILE_FILENAME = "profile.json"

    # How much of the US surname population the single-token tiers claim.
    Exposure = Struct.new(
      :surnames_scored,   # Distinct surnames in the Census file.
      :surnames_matched,  # Distinct surnames matching some single-token tier.
      :bearers_total,     # Total bearers across the file.
      :bearers_exposed,   # Bearers whose surname matches some single-token tier.
      :bearers_via_short, # Bearers exposed via the `short` tier specifically.
      :bearers_via_place, # Bearers exposed via a single-token `place` entry.
      # Bearers exposed via the `demonym` tier. Counted here for the same reason
      # the other two are: it is a KEEP granted to a bare single token, which is
      # exactly the surface form this control measures. Leaving it out would make
      # adding a tier look free.
      :bearers_via_demonym,
      keyword_init: true
    ) do
      # Population-weighted exposure, as a percentage. The headline.
      def rate
        100.0 * bearers_exposed / bearers_total
      end

      def short_rate
        100.0 * bearers_via_short / bearers_total
      end

      def place_rate
        100.0 * bearers_via_place / bearers_total
      end

      def demonym_rate
        100.0 * bearers_via_demonym / bearers_total
      end

      def distinct_rate
        100.0 * surnames_matched / surnames_scored
      end
    end

    class << self
      # Configured path to a local Census surname file, or `""`.
      def census_source
        (ENV[EVAL_CENSUS_CSV_ENV_VAR] || "").strip
      end

      # `{normalised surname => number of US bearers}` from the Census CSV text.
      #
      # Field-indexed off the header rather than positional, so a column added
      # upstream shifts nothing. The file carries no quoted fields — every row is
      # 11 bare comma-separated values — so this splits rather than requiring
      # `csv`, and a row that does not yield an integer count is skipped the same
      # way Python's `DictReader` loop skips it.
      def parse_census_surnames(text)
        counts = {}
        lines = text.split(/\r?\n/, -1)
        header = (lines.first || "").split(",")
        name_at = header.index("name")
        count_at = header.index("count")
        if name_at.nil? || count_at.nil?
          raise ArgumentError,
                "Census surname file has no 'name'/'count' header; got #{header.join(',')}"
        end

        lines.drop(1).each do |line|
          next if line.empty?

          fields = line.split(",")
          name = Gazetteer.normalize(fields[name_at] || "")
          next if name.empty? || name == "all other names"

          count = Integer(fields[count_at], exception: false)
          next if count.nil?

          counts[name] = count
        end

        if counts.size < MINIMUM_ROWS
          raise RuntimeError,
                "Census surname file parsed to only #{counts.size} rows; expected ~162k. " \
                "Refusing to score exposure against a truncated list, because the " \
                "failure mode is a more comfortable rate than the truth."
        end
        counts
      end

      # `conformance/census/`, or nil outside a checkout.
      def shipped_dir
        candidate = Conformance.directory.join(SHIPPED_DIRNAME)
        candidate.join(SHIPPED_TABLE_FILENAME).file? ? candidate : nil
      rescue Conformance::SpecError
        nil
      end

      # `{normalised surname => bearers}` from the table this repository ships.
      #
      # The digest in `profile.json` is checked, not trusted. This table is used
      # to SUBTRACT exposure from a permissive tier, so a truncated or edited
      # copy scores the gazetteer against a smaller America and reads as a
      # *better* number — the one direction this measurement must never fail in
      # quietly. A bad digest raises rather than degrading.
      def load_shipped_census(directory = nil)
        dir = directory ? Pathname.new(directory) : shipped_dir
        if dir.nil?
          raise Errno::ENOENT,
                "no conformance/#{SHIPPED_DIRNAME}/ above this module. The shipped " \
                "table lives in the repository, not in an installed gem."
        end

        payload = dir.join(SHIPPED_TABLE_FILENAME).binread
        profile = JSON.parse(dir.join(SHIPPED_PROFILE_FILENAME).read)
        expected = profile.dig("table", "sha256").to_s
        actual = Digest::SHA256.hexdigest(payload)
        if !expected.empty? && actual != expected
          raise RuntimeError,
                "#{SHIPPED_TABLE_FILENAME} has sha256 #{actual}, but " \
                "#{SHIPPED_PROFILE_FILENAME} pins #{expected}. Refusing to score the " \
                "gazetteer against a table that is not the one this repository " \
                "measured, because a short read reads as a better number. Rebuild " \
                "with `python tools/census_build.py --write`."
        end

        counts = {}
        Zlib.gunzip(payload).force_encoding("UTF-8").each_line do |line|
          name, _, bearers = line.chomp.partition("\t")
          counts[name] = Integer(bearers) unless name.empty?
        end
        if counts.size < MINIMUM_ROWS
          raise RuntimeError,
                "#{SHIPPED_TABLE_FILENAME} parsed to only #{counts.size} rows; " \
                "expected at least #{MINIMUM_ROWS}."
        end
        counts
      end

      # `{normalised surname => bearers}`, resolved in this order:
      #
      # 1. An explicit `source`, or `VICARY_EVAL_CENSUS_CSV`. An operator holding
      #    a newer Census release still wins, and gets the number *their* file
      #    gives.
      # 2. The table shipped in `conformance/census/`, which is the same 162,253
      #    rows the 2010 release carries and therefore the same rate to the last
      #    bearer. This is why the gate no longer skips on a bare checkout.
      #
      # There is no third step. census.gov answers the documented URL with a WAF
      # rejection page under a 200 status, which is why the shipped table exists.
      def load_census(source = nil)
        path = (source || census_source).strip
        if path.empty?
          return load_shipped_census unless shipped_dir.nil?

          raise Errno::ENOENT,
                "no conformance/#{SHIPPED_DIRNAME}/ in this tree and no " \
                "#{EVAL_CENSUS_CSV_ENV_VAR} set. Point that at a copy of " \
                "#{CENSUS_SURNAMES_MEMBER}, extracted from #{CENSUS_SURNAMES_URL}, " \
                "or run from a checkout"
        end
        if path.downcase.end_with?(".zip")
          raise ArgumentError,
                "#{path} is a .zip and this port reads the extracted .csv only. " \
                "Extract #{CENSUS_SURNAMES_MEMBER} from it and point " \
                "#{EVAL_CENSUS_CSV_ENV_VAR} at that."
        end
        parse_census_surnames(File.read(path, encoding: "UTF-8"))
      end

      # Score the loaded gazetteer's single-token tiers against the Census file.
      def measure(census, gaz = nil)
        gaz ||= Gazetteer.load
        single_token_places = gaz.place.reject { |n| n.include?(" ") }.to_set
        single = single_token_places | gaz.short.to_set | gaz.demonym.to_set

        bearers_total = 0
        bearers_exposed = 0
        surnames_matched = 0
        via_short = 0
        via_place = 0
        via_demonym = 0

        census.each do |name, count|
          bearers_total += count
          if single.include?(name)
            bearers_exposed += count
            surnames_matched += 1
          end
          via_short += count if gaz.short.include?(name)
          via_place += count if single_token_places.include?(name)
          via_demonym += count if gaz.demonym.include?(name)
        end

        Exposure.new(
          surnames_scored: census.size,
          surnames_matched: surnames_matched,
          bearers_total: bearers_total,
          bearers_exposed: bearers_exposed,
          bearers_via_short: via_short,
          bearers_via_place: via_place,
          bearers_via_demonym: via_demonym
        )
      end

      # The report block, for a CLI or a gate's failure message.
      def render(exposure)
        [
          "BARE-SURNAME FALSE-POSITIVE RATE (US Census 2010 surname file)",
          "  distinct surnames scored   #{group(exposure.surnames_scored)}",
          "  any single-token tier hit  #{group(exposure.surnames_matched)} " \
          "(#{format('%.2f', exposure.distinct_rate)}% of distinct)",
          "  population-weighted rate   #{format('%.2f', exposure.rate)}% " \
          "(#{group(exposure.bearers_exposed)} / #{group(exposure.bearers_total)} bearers)",
          "    via the short tier       #{format('%.2f', exposure.short_rate)}%",
          "    via single-token places  #{format('%.2f', exposure.place_rate)}%",
          "    via the demonym tier     #{format('%.2f', exposure.demonym_rate)}%",
          "  reads as: for a private person named by BARE SURNAME ONLY — no",
          "            first name, no title, no corroboration — this share",
          "            resolves 'notable'. Conditional on that surface form."
        ].join("\n")
      end

      private

      def group(value)
        value.to_s.reverse.scan(/\d{1,3}/).join(",").reverse
      end
    end
  end
end
