#!/usr/bin/env ruby
# frozen_string_literal: true

# Diff this port's masked OUTPUT against the Python reference, on prose no
# fixture contains.
#
# The sibling of `parity_probe.rb`, one layer up. That one compares gazetteer
# verdicts name by name; this one runs the whole detector — both passes, both
# routes, the minter — and compares bytes.
#
# **Why it exists, measured rather than supposed.** The day this port landed, of
# eleven deliberate mutations to `candidates.rb` the 36 conformance frames caught
# one and the 2,526 primitive assertions caught seven. Three of the remaining
# were inert. The last was not: changing `\z` to `$` in `RELATION_ATTACHED_BEFORE`
# — the idiomatic Ruby spelling, and wrong — left every frame and every primitive
# green, because both of those corpora are single-line and the rule only diverges
# across a newline.
#
# So the probes are chosen for the seams a single-sentence fixture cannot reach:
# line breaks inside a relation window, a five-digit number ending a line,
# headings with blank lines around them, a document that mixes cases. Each is a
# place two implementations can drift apart with every committed test still
# green.
#
# They live in `conformance/probes.json`, shared with
# `typescript/scripts/redaction-parity.mjs`. Two ports probing different seams
# would reproduce exactly the drift these scripts exist to catch.
#
# It runs BOTH implementations and compares them. A probe that only printed this
# port's answers would need a hand-copied expected column, which is the failure
# mode the whole repository is arranged against: a constant somebody typed agrees
# with itself forever.
#
#     ruby scripts/redaction_parity.rb                 # the built-in probe list
#     ruby scripts/redaction_parity.rb path/to/probes.json  # {"name": "text", …}
#
# Exits non-zero on any divergence, so it can gate a commit.

require "json"
require "open3"
require "pathname"

require_relative "../lib/vicary"

ROOT = Pathname.new(__dir__).join("..", "..").expand_path
PYTHON = ROOT.join("python", ".venv", "bin", "python")
SPEC_PATH = ROOT.join("conformance", "probes.json")

SPEC = begin
  raw = JSON.parse(SPEC_PATH.read)
  unless raw["document_version"] == 1
    warn "probes.json is document_version #{raw['document_version'].inspect}, and " \
         "this reader knows 1. Refusing rather than probing a subset of it."
    exit 2
  end
  raw
end

# The fixture identity, which every reference arm interpolates. A probe that
# omitted it would be measuring a different system.
IDENTITY = Struct.new(:first_name, :last_name, :school_name)
                 .new(SPEC["identity"]["first_name"], SPEC["identity"]["last_name"],
                      SPEC["identity"]["school_name"])

# The probe texts live in `conformance/probes.json`, shared with the TypeScript
# script rather than kept here. Two ports probing different seams would reproduce
# exactly the drift these scripts exist to catch, and each entry there says WHICH
# seam it covers — a probe whose purpose is forgotten gets deleted as redundant.
DEFAULT_PROBES = SPEC["redaction_probes"]
                 .to_h { |probe| [probe["id"], probe["text"]] }
                 .freeze

def probes(argv)
  return DEFAULT_PROBES if argv.empty?

  JSON.parse(Pathname.new(argv[0]).read)
end

def ruby_output(texts)
  texts.transform_values { |text| Vicary.redact(text, IDENTITY) }
end

def python_output(texts)
  unless PYTHON.file?
    warn "no reference interpreter at #{PYTHON}."
    warn "This probe compares two implementations; with only one of them it " \
         "would be a script that agrees with itself. Run `just py-setup` from " \
         "the repository root first."
    exit 2
  end

  script = <<~PY
    import json, sys
    from vicary.eval.recall import build_redactor

    # The arm the conformance golden was produced by, read off the shared spec.
    # A probe against any other arm would diff two different detectors and
    # report the difference as a bug.
    redactor = build_redactor(#{SPEC['arm'].inspect}, None)
    texts = json.load(sys.stdin)
    print(json.dumps({
        name: redactor._apply(text, source="INPUT").text
        for name, text in texts.items()
    }, ensure_ascii=False))
  PY

  out, err, status = Open3.capture3(PYTHON.to_s, "-c", script,
                                    stdin_data: JSON.generate(texts),
                                    chdir: ROOT.join("python").to_s)
  unless status.success?
    warn "the reference implementation failed:\n#{err}"
    exit 2
  end
  JSON.parse(out)
end

texts = probes(ARGV)
mine = ruby_output(texts)
reference = python_output(texts)

divergent = texts.keys.reject { |name| mine[name] == reference[name] }

if divergent.empty?
  puts "#{texts.size} probes, no divergence from the Python reference."
  puts "(masked bytes identical, placeholder numbering included, on prose the"
  puts " conformance frames and the primitives corpus do not contain)"
  exit 0
end

warn "#{divergent.length} of #{texts.size} probes diverge from the reference:"
warn ""
divergent.each do |name|
  warn "  #{name}"
  warn "    input:     #{texts[name].inspect}"
  warn "    reference: #{reference[name].inspect}"
  warn "    this port: #{mine[name].inspect}"
  warn ""
end
warn "The frames and the primitives spec may both still be green — they are"
warn "single-line corpora, and several rules only diverge across a newline."
exit 1
