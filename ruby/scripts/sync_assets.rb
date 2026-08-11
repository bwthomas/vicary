# frozen_string_literal: true

# Vendor the shared asset payload into this gem.
#
# The asset is the product; the language is the wrapper. `notability.txt.gz` is
# ~2.1 MB of folded Wikidata, Census and SSA evidence with a format number and a
# sha256 manifest, `stop_words.txt` is the 421-word stoplist that decides what
# becomes a name candidate at all, and every front door must load THE SAME BYTES —
# a port with its own gazetteer or its own stoplist is a second detector wearing
# the first one's name.
#
# So this copies rather than rebuilds, from the repository's `asset/`, which is not
# inside any of the three packages. That directory is the build mechanism's output
# and no front door's property; see `asset/README.md`. Vendoring rather than
# fetching at install time is deliberate: "no network, no per-request cost" is the
# product claim, and a build-time fetch puts a fetch back in the story.
#
# The copy is .gitignore'd: it is a build input reproduced from a tracked file, and
# a second tracked copy is a second thing to bump per asset cut — which is exactly
# how two front doors end up shipping different gazetteers.

require "digest"
require "fileutils"
require "json"
require "pathname"

GEM_ROOT = Pathname.new(__dir__).join("..").expand_path
REPO_ROOT = GEM_ROOT.join("..").expand_path
BUILT_DIR = REPO_ROOT.join("asset", "data")
LEXICON_DIR = REPO_ROOT.join("asset", "lexicon")
TARGET = GEM_ROOT.join("assets")

# `[source_dir, filename]`, mirroring `asset/vicary_build/vendor.py`. Built
# artifacts come from `asset/data/`; authored word lists from `asset/lexicon/`,
# where they are checksummed, rather than from a staged duplicate.
PAYLOAD = [
  [BUILT_DIR, "notability.txt.gz"],
  [BUILT_DIR, "MANIFEST.json"],
  *LEXICON_DIR.glob("*.txt").sort.map { |p| [LEXICON_DIR, p.basename.to_s] }
].freeze

[BUILT_DIR, LEXICON_DIR].each do |dir|
  next if dir.directory?

  warn "no asset source at #{dir}"
  warn "This script vendors from the monorepo. Outside a checkout there is " \
       "nothing to vendor from, and a published gem should already carry assets/."
  exit 2
end

FileUtils.mkdir_p(TARGET)
PAYLOAD.each { |dir, name| FileUtils.cp(dir.join(name), TARGET.join(name)) }

described = JSON.parse(TARGET.join("MANIFEST.json").read).fetch("assets")

# Every manifest entry must have been vendored, and nothing else. Adding an asset
# without updating this list would otherwise ship a gem whose manifest describes a
# file it does not carry — which fails at load time for a user, not at build time
# for us.
vendored = PAYLOAD.map { |_, name| name }.reject { |name| name == "MANIFEST.json" }
if vendored.sort != described.keys.sort
  abort "vendored payload does not match the manifest:\n" \
        "  described but not vendored: #{(described.keys - vendored).sort.inspect}\n" \
        "  vendored but not described: #{(vendored - described.keys).sort.inspect}"
end

# Verify what landed, not what was copied. `cp` returning without raising says the
# call succeeded; it does not say the bytes on disk are the bytes the manifest
# describes, and a truncated asset loads as a SMALLER gazetteer — which redacts
# more, looks privacy-safe, and is invisible to every test that only checks output
# was masked. The same argument runs the other way for the stoplist: a short read
# there makes the redactor MORE aggressive.
described.sort.each do |name, entry|
  bytes = TARGET.join(name).binread
  digest = Digest::SHA256.hexdigest(bytes)

  if bytes.bytesize != entry.fetch("bytes")
    abort "vendored #{name} is #{bytes.bytesize} bytes, manifest says #{entry['bytes']}"
  end
  if digest != entry.fetch("sha256")
    abort "vendored #{name} sha256 #{digest} does not match manifest #{entry['sha256']}"
  end

  # stderr, not stdout, matching the npm script: a confirmation line on stdout
  # corrupts any machine-readable output a caller is parsing. Diagnostics stay
  # visible in a log without being mistaken for data.
  warn "vendored #{name} (#{bytes.bytesize} bytes) — sha256 verified"
end
