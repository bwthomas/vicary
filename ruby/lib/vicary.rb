# frozen_string_literal: true

# vicary — offline redaction of personal names in student compositions.
#
# The RubyGems front door. **The detector is not ported yet.** What is here is
# everything underneath it: the asset layer, which loads the identical gazetteer
# bytes the Python package loads; the lexicon reader, which loads the identical
# stoplist; the notability index those two feed; and the conformance harness that
# scores the port frame by frame against `conformance/frames.json`.
#
# The layer still missing is candidate generation and the masking pass — which is
# to say, all of the deciding. {Vicary::Gazetteer} can already tell you that
# `Rosa Parks` is notable and `Terrence Okonkwo` is not, verified name-by-name
# against the reference by `scripts/parity_probe.rb`; nothing yet finds those
# spans in an essay or replaces them.
#
# Nothing here should be pointed at student writing until `rake conformance`
# reports 36 of 36 masking-required frames matching the reference output
# byte-for-byte, placeholder numbering included. The scoreboard prints the real
# count on every run precisely so that state cannot be mistaken for readiness.
module Vicary
end

require_relative "vicary/version"
require_relative "vicary/asset"
require_relative "vicary/lexicon"
require_relative "vicary/gazetteer"
require_relative "vicary/conformance"
require_relative "vicary/redact"
