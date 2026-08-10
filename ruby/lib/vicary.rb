# frozen_string_literal: true

# vicary — offline redaction of personal names in student compositions.
#
# The RubyGems front door. **The detector is not ported yet**: what is here is the
# asset layer, which loads the identical gazetteer bytes the Python package loads,
# and the conformance harness that scores the port frame by frame against
# `conformance/frames.json`.
#
# Nothing here should be pointed at student writing until `rake conformance`
# reports 35 of 35 masking-required frames matching the reference output
# byte-for-byte, placeholder numbering included. The scoreboard prints the real
# count on every run precisely so that state cannot be mistaken for readiness.
module Vicary
end

require_relative "vicary/version"
require_relative "vicary/asset"
require_relative "vicary/conformance"
require_relative "vicary/redact"
