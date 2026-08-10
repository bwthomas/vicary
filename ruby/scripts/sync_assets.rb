# frozen_string_literal: true

# Vendor the gazetteer asset into this gem.
#
# The asset is the product; the language is the wrapper. `notability.txt.gz` is
# ~2.1 MB of folded Wikidata, Census and SSA evidence with a format number and a
# sha256 manifest, and every front door must load THE SAME BYTES — a port with its
# own gazetteer is a second detector wearing the first one's name.
#
# So this copies rather than rebuilds, from the one tracked source in the
# repository. Vendoring rather than fetching at install time is deliberate: "no
# network, no per-request cost" is the product claim, and a build-time fetch puts
# a fetch back in the story.
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
SOURCE = REPO_ROOT.join("python", "src", "vicary", "data")
TARGET = GEM_ROOT.join("assets")
FILES = ["notability.txt.gz", "MANIFEST.json"].freeze

unless SOURCE.directory?
  warn "no asset source at #{SOURCE}"
  warn "This script vendors from the monorepo. Outside a checkout there is " \
       "nothing to vendor from, and a published gem should already carry assets/."
  exit 2
end

FileUtils.mkdir_p(TARGET)
FILES.each { |name| FileUtils.cp(SOURCE.join(name), TARGET.join(name)) }

# Verify what landed, not what was copied. `cp` returning without raising says the
# call succeeded; it does not say the bytes on disk are the bytes the manifest
# describes, and a truncated asset loads as a SMALLER gazetteer — which redacts
# more, looks privacy-safe, and is invisible to every test that only checks output
# was masked.
entry = JSON.parse(TARGET.join("MANIFEST.json").read)
              .dig("assets", "notability.txt.gz")
bytes = TARGET.join("notability.txt.gz").binread
digest = Digest::SHA256.hexdigest(bytes)

if bytes.bytesize != entry.fetch("bytes")
  abort "vendored asset is #{bytes.bytesize} bytes, manifest says #{entry['bytes']}"
end
if digest != entry.fetch("sha256")
  abort "vendored asset sha256 #{digest} does not match manifest #{entry['sha256']}"
end

# stderr, not stdout, matching the npm script: a confirmation line on stdout
# corrupts any machine-readable output a caller is parsing. Diagnostics stay
# visible in a log without being mistaken for data.
warn "vendored notability.txt.gz (#{bytes.bytesize} bytes, format " \
     "#{entry['format']}, cut #{entry['cut_date']}) — sha256 verified"
