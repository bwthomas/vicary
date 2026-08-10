# frozen_string_literal: true

require "digest"
require "json"
require "pathname"
require "set"
require "zlib"

module Vicary
  # Load the gazetteer asset — the same bytes the Python package loads.
  #
  # The asset is a gzipped, line-oriented text file, chosen over a binary format
  # precisely so three languages can read it without a schema compiler:
  #
  #     #!gazetteer 5                 format number, checked not sniffed
  #     #!meta {"cut_date": ...}      provenance, one JSON object
  #     #!tier demonym 1047           tier name and its DECLARED entry count
  #     abidjanese                    one normalised entry per line
  #
  # Two properties matter more than convenience.
  #
  # **The format number is refused, not tolerated.** An unknown format means the
  # file's meaning changed, and a reader that skips lines it does not recognise
  # degrades into a smaller gazetteer — which redacts MORE, reads as
  # privacy-safe, and is invisible to any test that only asks whether something
  # was masked.
  #
  # **The declared tier count is checked against the parsed count.** A truncated
  # read is the same silent failure in a different costume: fewer notable people
  # means fewer public figures kept, so an essay about Rosa Parks comes back with
  # her name removed.
  module Asset
    # Asset format this reader understands. Refuse anything else.
    SUPPORTED_FORMAT = 5

    ASSET_FILENAME = "notability.txt.gz"
    MANIFEST_FILENAME = "MANIFEST.json"

    # Environment override, spelled the same as the Python package's.
    ASSET_PATH_ENV_VAR = "VICARY_ASSET_PATH"

    Gazetteer = Struct.new(:format, :meta, :tiers, :sha256, :path,
                           keyword_init: true)

    class FormatError < StandardError; end
    class MissingAssetError < StandardError; end

    class << self
      # Candidate asset locations, most specific first.
      #
      # The env override first, so an operator can point at a different cut
      # without reinstalling. Then this gem's vendored copy, which is what an
      # installed gem has. Then the monorepo's Python package, which is what a
      # checkout has before `rake sync_assets` — so `git clone && rake test`
      # works with no bootstrap step rather than failing in a way that reads as
      # a broken port.
      def search_path
        gem_root = Pathname.new(__dir__).join("..", "..").expand_path
        repo_root = gem_root.join("..").expand_path
        candidates = []
        override = ENV.fetch(ASSET_PATH_ENV_VAR, "").strip
        candidates << Pathname.new(override) unless override.empty?
        candidates << gem_root.join("assets")
        candidates << repo_root.join("python", "src", "vicary", "data")
        candidates
      end

      def locate
        tried = search_path
        found = tried.find { |dir| dir.join(ASSET_FILENAME).file? }
        return found if found

        raise MissingAssetError,
              "no #{ASSET_FILENAME} found. Looked in: " \
              "#{tried.join(', ')}. In a checkout, run `rake sync_assets`; set " \
              "#{ASSET_PATH_ENV_VAR} to override."
      end

      # Parse the decompressed asset text.
      #
      # Public so a test can feed it a deliberately malformed document. A parser
      # reachable only through a 2.1 MB file on disk is a parser whose failure
      # paths are never exercised.
      def parse(text)
        lines = text.split("\n", -1)
        header = /\A\#!gazetteer (\d+)\z/.match(lines.first.to_s)
        unless header
          raise FormatError,
                "asset does not begin with a \#!gazetteer header (got " \
                "#{lines.first.to_s[0, 40].inspect})"
        end

        format = header[1].to_i
        unless format == SUPPORTED_FORMAT
          raise FormatError,
                "asset format #{format} is not #{SUPPORTED_FORMAT}. Refusing to " \
                "read it rather than skipping the parts that changed: a " \
                "partially understood gazetteer is a smaller one, and a smaller " \
                "one redacts more while looking correct."
        end

        meta = {}
        tiers = {}
        declared = {}
        current = nil

        lines.each_with_index do |line, index|
          next if index.zero? || line.empty?

          if line.start_with?("#!meta ")
            meta = JSON.parse(line.delete_prefix("#!meta "))
            next
          end

          if (tier = /\A\#!tier (\S+) (\d+)\z/.match(line))
            current = Set.new
            tiers[tier[1]] = current
            declared[tier[1]] = tier[2].to_i
            next
          end

          if line.start_with?("#!")
            raise FormatError,
                  "unrecognised directive #{line[0, 40].inspect} at line " \
                  "#{index + 1}; the asset format changed without its number " \
                  "changing"
          end

          raise FormatError, "entry at line #{index + 1} appears before any \#!tier" if current.nil?

          current << line
        end

        declared.each do |name, count|
          actual = tiers.fetch(name).size
          next if actual == count

          raise FormatError,
                "tier #{name} declares #{count} entries and parsed #{actual}. A " \
                "short read here removes public figures from the keep list, so " \
                "an essay about a historical figure comes back with their name " \
                "redacted."
        end

        [format, meta, tiers]
      end

      # Load and cache the gazetteer.
      def load(directory: nil)
        return @cached if @cached && directory.nil?

        dir = Pathname.new(directory || locate)
        asset_path = dir.join(ASSET_FILENAME)
        compressed = asset_path.binread
        sha256 = Digest::SHA256.hexdigest(compressed)

        verify_against_manifest(dir, asset_path, sha256)

        format, meta, tiers = parse(Zlib::GzipReader.new(StringIO.new(compressed)).read)
        gazetteer = Gazetteer.new(format: format, meta: meta, tiers: tiers,
                                  sha256: sha256, path: asset_path.to_s)
        @cached = gazetteer if directory.nil?
        gazetteer
      end

      # Forget the cached gazetteer. For tests.
      def reset_cache
        @cached = nil
      end

      private

      def verify_against_manifest(dir, asset_path, sha256)
        manifest_path = dir.join(MANIFEST_FILENAME)
        return unless manifest_path.file?

        entry = JSON.parse(manifest_path.read).dig("assets", ASSET_FILENAME)
        return if entry.nil? || entry["sha256"] == sha256

        raise FormatError,
              "#{asset_path} sha256 #{sha256} does not match the manifest's " \
              "#{entry['sha256']}. The asset was modified or truncated in " \
              "transit; every front door must load identical bytes or " \
              "\"byte-identical output\" is not a claim anybody can make."
      end
    end
  end
end

require "stringio"
