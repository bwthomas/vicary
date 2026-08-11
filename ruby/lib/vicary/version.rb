# frozen_string_literal: true

module Vicary
  # Single source of this package's version.
  #
  # Shared across all three front doors on purpose: one detector, one number. A
  # gem 0.3.0 that corresponds to nothing on PyPI cannot be reasoned about, and
  # the parity claim is between *versions*, not between package names.
  VERSION = "0.2.0"
end
