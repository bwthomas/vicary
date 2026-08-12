# frozen_string_literal: true

# vicary — offline redaction of personal names in student compositions.
#
# The RubyGems front door. {Vicary.redact} is the one call most hosts want: hand
# it a composition and the student's own identity, get the masked text back.
# {Vicary.redact_with_report} returns the same bytes plus the map
# {Vicary.restore} needs to put the originals back.
#
# **What the surface is claiming.** `redact` does the deciding now, and it
# reproduces all 52 fixture frames byte-for-byte against the Python reference,
# placeholder numbering included — the arm being `local-gazetteer-lowercase`. It
# raised {Vicary::NotPortedError} before that rather than returning the text
# unchanged, because a partially ported redactor is a reasonable thing to measure
# and an unreasonable thing to hand a host: it would mask a phone number, miss
# every name in the essay, and give the caller no way to tell.
#
# Three layers check that claim, and each catches what the one above it cannot:
#
# * `rake conformance` scores the 54 frames — the final bar, and a coarse first
#   one;
# * `rake test` runs `test/primitives_test.rb`, forty-odd primitives over the
#   shared `primitives.json` corpus, which says *which brick is crooked*;
# * `rake redaction_parity` runs both implementations over prose neither corpus
#   contains and diffs the bytes, because several rules only diverge across a
#   newline and both corpora are single-line.
#
# The scoreboard prints the real count on every run precisely so readiness is
# never somebody's recollection.
module Vicary
end

require_relative "vicary/version"
require_relative "vicary/asset"
require_relative "vicary/lexicon"
require_relative "vicary/gazetteer"
require_relative "vicary/minter"
require_relative "vicary/structured"
require_relative "vicary/candidates"
require_relative "vicary/conformance"
require_relative "vicary/gates"
require_relative "vicary/census"
require_relative "vicary/corpus"
require_relative "vicary/redact"
