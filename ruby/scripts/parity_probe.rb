#!/usr/bin/env ruby
# frozen_string_literal: true

# Diff this port's raw gazetteer verdicts against the Python reference.
#
# The conformance suite scores masked *output*, which is the claim that matters
# and also the coarsest one: two implementations can disagree about which tier
# matched, or about how a name folds, and still produce identical text on every
# frame in the set. This probes the layer underneath — the fold and the verdict,
# name by name — so a divergence shows up as a diff rather than waiting for a
# frame that happens to collide.
#
# It runs BOTH implementations and compares them. A probe that only printed this
# port's answers would need a hand-copied expected column, which is the failure
# mode the whole repository is arranged against: a constant somebody typed agrees
# with itself forever.
#
#     ruby scripts/parity_probe.rb                    # the built-in probe list
#     ruby scripts/parity_probe.rb path/to/names.txt  # one name per line
#
# Exits non-zero on any divergence, so it can gate a commit.

require "json"
require "open3"
require "pathname"
require "tempfile"

require_relative "../lib/vicary"

ROOT = Pathname.new(__dir__).join("..", "..").expand_path
PYTHON = ROOT.join("python", ".venv", "bin", "python")

# The names live in `conformance/probes.json`, shared with
# `typescript/scripts/gazetteer-parity.mjs` rather than kept here. Each is a place
# two implementations can drift apart without any frame noticing, and each entry
# there says which — a probe whose purpose is forgotten gets deleted as redundant.
SPEC_PATH = ROOT.join("conformance", "probes.json")

DEFAULT_PROBES = begin
  raw = JSON.parse(SPEC_PATH.read)
  unless raw["document_version"] == 1
    warn "probes.json is document_version #{raw['document_version'].inspect}, and " \
         "this reader knows 1. Refusing rather than probing a subset of it."
    exit 2
  end
  raw["gazetteer_names"].map { |entry| entry["name"] }.freeze
end

def probes(argv)
  return DEFAULT_PROBES if argv.empty?

  Pathname.new(argv[0]).readlines(chomp: true).reject(&:empty?)
end

def ruby_rows(names)
  names.map do |name|
    [
      name,
      Vicary::Gazetteer.normalize(name),
      Vicary::Gazetteer.notability(name),
      Vicary::Gazetteer.settlement?(name).to_s,
      Vicary::Gazetteer.common_given_name?(name).to_s
    ].join("\t")
  end
end

def python_rows(names)
  unless PYTHON.file?
    warn "no reference interpreter at #{PYTHON}."
    warn "This probe compares two implementations; with only one of them it " \
         "would be a script that agrees with itself. Run `just py-setup` from " \
         "the repository root first."
    exit 2
  end

  script = <<~PY
    import sys
    from vicary import gazetteer as g
    for line in sys.stdin.read().split("\\n"):
        if not line:
            continue
        print("\\t".join([
            line,
            g.normalize(line),
            g.notability(line),
            str(g.is_settlement(line)).lower(),
            str(g.is_common_given_name(line)).lower(),
        ]))
  PY

  out, err, status = Open3.capture3(PYTHON.to_s, "-c", script,
                                    stdin_data: names.join("\n"),
                                    chdir: ROOT.join("python").to_s)
  unless status.success?
    warn "the reference implementation failed:\n#{err}"
    exit 2
  end
  out.split("\n")
end

names = probes(ARGV)
mine = ruby_rows(names)
reference = python_rows(names)

divergent = mine.zip(reference).reject { |ours, theirs| ours == theirs }

if divergent.empty?
  puts "#{names.length} probes, no divergence from the Python reference."
  puts "(fold, verdict, settlement typing and given-name signal all identical)"
  exit 0
end

warn "#{divergent.length} of #{names.length} probes diverge from the reference:"
warn ""
divergent.each do |ours, theirs|
  warn "  reference: #{theirs}"
  warn "  this port: #{ours}"
  warn ""
end
warn "Columns are: name, fold, verdict, is_settlement, is_common_given_name."
warn "A divergence in the fold column is the one to fix first — every other"
warn "column is a lookup keyed on it, so one bad fold moves all of them."
exit 1
