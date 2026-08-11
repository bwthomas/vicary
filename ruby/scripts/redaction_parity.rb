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
# So the probes below are chosen for the seams a single-sentence fixture cannot
# reach: line breaks inside a relation window, a five-digit number ending a line,
# headings with blank lines around them, a document that mixes cases. Each is a
# place two implementations can drift apart with every committed test still
# green.
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

# The fixture identity, which every reference arm interpolates. A probe that
# omitted it would be measuring a different system.
IDENTITY = Struct.new(:first_name, :last_name, :school_name)
                 .new("Marguerite", "Delacroix-Whitfield", "Westfield High School")

# Texts chosen for the seams, not for coverage. The comment on each says which
# seam, because a probe whose purpose is forgotten gets deleted as redundant.
DEFAULT_PROBES = {
  # `$` vs `\Z`: a five-digit number ending a line is not a ZIP code.
  "multiline-zip" =>
    "I live at 12345\nMy locker combination is 90210 and I always forget it.",
  # `$` vs `\z`: a relation cue on an earlier line is not attached to this name.
  "relation-across-lines" =>
    "my cousin \nand then the dog ran past Wright and kept going.",
  # Corroboration and its sentence-level refusal, across hard-wrapped lines.
  "wrapped-prose" =>
    "Richard Wright wrote Native Son, and Wright is the author my\n" \
    "teacher assigned. My neighbor Robinson lives two doors down from us,\n" \
    "so Robinson is not Jackie Robinson.",
  # Headings: the blank-line rule, and a heading's capitals vouching for nothing.
  "heading-doc" =>
    "My Description of a Horse\n\nHorses are big. My cousin Terrence Okonkwo " \
    "rode one at the fair in\nAllen Park last summer, and Mrs. Okonkwo took a " \
    "picture.\n\nBreeds I Like\n\nI like the Arabian best.",
  # The lowercase route, and a relation-led title used literally.
  "lowercase-writer" =>
    "i went to the store with terrence okonkwo and then i came home. my cousin " \
    "vinny came over that summer and never left.",
  # The emphasis rule against a long all-caps run.
  "allcaps" =>
    "MY BEST FRIEND DESHAWN PRITCHARD WOULD NEVER DO THAT. it was SLAM and " \
    "then WHACK.",
  # Every structured pattern at once, in application order.
  "structured" =>
    "Call me at (555) 123-4567 or email marguerite.d@example.com. I live at " \
    "412 N Maple Street, Apt 3B, Akron 44301. My SSN is 123-45-6789 and my " \
    "card is 4111 1111 1111 1111. Follow @terrence_ok on there. I am 16 years " \
    "old. Born on 03/14/2009. See https://example.com/~mine and www.foo.co.uk/bar.",
  # Title protection, and a character described by a relation who must be kept.
  "titles" =>
    "I read To Kill a Mockingbird and Charlotte's Web. My Cousin Vinny is my " \
    "favorite movie. Atticus Finch, a father who taught me plenty, is in the " \
    "first one.",
  # Curly and straight apostrophes, plural possessives, particle-led surnames.
  "quoted-and-possessive" =>
    "The teacher liked vivid words like 'Giggles filled the school'. " \
    "Terrence's essay and the Delacroix-Whitfields' house were both mentioned. " \
    "Vincent van Gogh painted; van Gogh died young.",
  # Idempotence: our own placeholders and upstream markers survive a second pass.
  "protected-idempotence" =>
    "{NAME_1} went with @PERSON1 to see {LOCATION_2} and Terrence Okonkwo.",
  # Word boundaries against accented letters — where Ruby, Python and JavaScript
  # all three disagree about what `\b` means.
  "accented" =>
    "Ana said naïve things to René Descartes and to Renée at the café.",
  # The precedence table's colliding rows: settlement vs org suffix vs landmark.
  "org-and-place" =>
    "Progressive Insurance is in Falls Church. The Lincoln Memorial is in " \
    "Washington. Allen Park has a park. Springfield Township hired Akron " \
    "Public Library.",
}.freeze

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

    # The arm the conformance golden was produced by. A probe against any other
    # arm would diff two different detectors and report the difference as a bug.
    redactor = build_redactor("local-gazetteer-lowercase", None)
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
