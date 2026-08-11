# frozen_string_literal: true

require "pathname"
require "set"

module Vicary
  # Read a language-neutral word list that ships beside the gazetteer.
  #
  # The stoplist used to be a literal in Python's `name_candidates.py`, which was
  # fine while there was one front door. With three, a hand-transliterated word
  # list is a second detector wearing the first one's name: the divergence shows
  # up as prose corruption in one language and not the others, and no parity
  # check on *masked output* would catch it, because a stop word going missing
  # changes what gets masked in essays nobody put in a fixture.
  #
  # So the list is authored once, language-neutrally, under `asset/lexicon/` in
  # the repository, and vendored into each package the same way the gazetteer is.
  # This is the reader; it owns nothing.
  #
  # Format
  # ------
  # `#!` lines are directives, `#` lines are comments, blank lines are skipped,
  # and every other line contributes whitespace-separated words. One directive is
  # required:
  #
  #     #!lexicon 1               format version
  #     #!list <name> <count>     the list's name, and its DISTINCT word count
  #
  # The count is asserted, not trusted. A short read here makes the redactor
  # **more** aggressive — fewer stop words means more capitalised ordinary words
  # become name candidates — which looks privacy-safe, corrupts prose, and passes
  # any check that only asks whether something was masked. Same reasoning as the
  # gazetteer's per-tier counts; same failure mode if it is skipped.
  #
  # This is the fourth reader of this format, after `asset/vicary_build/lexicon.py`,
  # `python/src/vicary/lexicon.py` and `typescript/src/lexicon.ts`. The duplication
  # is deliberate — the build tool must not import one of the implementations it
  # feeds — and it is only honest because something compares the results.
  # `test/lexicon_test.rb` pins this reader's output against the count and the
  # spot-checks the other three are pinned against.
  module Lexicon
    # On-disk format version for a lexicon file. Bump when the parse changes, so
    # a stale vendored copy fails loudly rather than parsing to a different list.
    LEXICON_FORMAT = 1

    # Filename suffix. Named so a second list costs a file rather than a refactor.
    SUFFIX = ".txt"

    # A lexicon is absent, unreadable, or not the shape this reader understands.
    #
    # Its own class rather than a bare RuntimeError for the same reason
    # {Asset::FormatError} has one: a caller that wants to tell "the install is
    # incomplete" apart from "something else raised" cannot do it on a message.
    class LexiconError < StandardError; end

    class << self
      # Where the vendored copy of +name+ lives, whether or not it exists.
      #
      # Searched along the same path as the gazetteer, in the same order, so a
      # package cannot end up reading its stoplist from one cut and its
      # gazetteer from another.
      def path(name)
        filename = "#{name}#{SUFFIX}"
        tried = Asset.search_path
        found = tried.find { |dir| dir.join(filename).file? }
        return found.join(filename) if found

        # Return the first location rather than raising, so `load` reports the
        # same "missing" error whether the directory is absent or the file
        # inside it is.
        (tried.first || Pathname.new(".")).join(filename)
      end

      # Parse lexicon text into its case-folded distinct words.
      #
      # Public so a test can feed it a deliberately malformed document. A parser
      # reachable only through a vendored file on disk is a parser whose failure
      # paths are never exercised — and every one of this parser's failure paths
      # exists to turn a silent short read into a loud one.
      #
      # @param where [String] a path, used only in error messages, so a failure
      #   names a file.
      def parse(name, text, where)
        declared = nil
        saw_format = false
        words = Set.new

        text.split(/\r?\n/, -1).each_with_index do |line, index|
          lineno = index + 1
          stripped = line.strip

          if stripped.start_with?("#!")
            parts = stripped[2..].to_s.split(/\s+/).reject(&:empty?)
            raise LexiconError, "#{where}:#{lineno}: empty directive" if parts.empty?

            case parts[0]
            when "lexicon"
              saw_format = true
              unless parts.length == 2 && parts[1] == LEXICON_FORMAT.to_s
                raise LexiconError,
                      "#{where}:#{lineno}: lexicon format " \
                      "#{parts[1..].join(' ').inspect}, this build reads " \
                      "#{LEXICON_FORMAT}"
              end
            when "list"
              unless parts.length == 3 && parts[1] == name
                raise LexiconError,
                      "#{where}:#{lineno}: expected `\#!list #{name} <count>`, " \
                      "got #{stripped.inspect}"
              end
              # `to_i` would read "3x" as 3, where Python's `int()` raises. The
              # difference is the whole guard: a count this reader silently
              # repaired is a count that no longer proves anything about the parse.
              unless /\A\d+\z/.match?(parts[2])
                raise LexiconError,
                      "#{where}:#{lineno}: count #{parts[2].inspect} is not an integer"
              end

              declared = parts[2].to_i
            else
              # Refused rather than ignored: an unrecognised directive means the
              # file was written by something that knows more than this reader,
              # and guessing which lines are still words is how a partial list
              # loads as a whole one.
              raise LexiconError, "#{where}:#{lineno}: unknown directive #{parts[0].inspect}"
            end
            next
          end

          next if stripped.empty? || stripped.start_with?("#")

          stripped.split(/\s+/).each do |word|
            words << word.downcase unless word.empty?
          end
        end

        unless saw_format
          raise LexiconError, "#{where}: no `\#!lexicon` directive; not a lexicon file"
        end
        raise LexiconError, "#{where}: no `\#!list #{name} <count>` directive" if declared.nil?

        unless words.size == declared
          raise LexiconError,
                "#{where}: declares #{declared} distinct words, parsed " \
                "#{words.size}. A short read makes the redactor more aggressive, " \
                "which is why this is an error and not a warning."
        end

        words
      end

      # The case-folded distinct words of lexicon +name+.
      #
      # Raises {LexiconError} rather than returning a partial list. An empty or
      # truncated stoplist is the quiet failure this whole module exists to
      # prevent.
      def load(name, path: nil)
        location = Pathname.new(path || self.path(name))
        begin
          text = location.read(encoding: "UTF-8")
        rescue Errno::ENOENT
          raise LexiconError,
                "lexicon #{name.inspect} missing at #{location}. The installed " \
                "vicary gem is incomplete — reinstall it, or vendor the asset " \
                "with `rake sync_assets` from a checkout."
        rescue SystemCallError => e
          raise LexiconError, "cannot read lexicon at #{location}: #{e.message}"
        end

        parse(name, text, location.to_s)
      end
    end
  end
end
