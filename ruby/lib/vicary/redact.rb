# frozen_string_literal: true

module Vicary
  # Raised by {Vicary.redact} until the detector is ported.
  class NotPortedError < StandardError; end

  # The redaction entry point — **not implemented yet**, and it raises.
  #
  # Raising is the design, not a placeholder nobody got round to. The alternative
  # shape — return the input unchanged until the detector lands — is a silent
  # no-op redactor: it satisfies every caller, and a host that wires it up gets
  # exactly zero redaction with no error to notice. This project has already
  # shipped that failure once in another form (a detection level that scored 0%
  # recall on the only class of name it could not interpolate, for months, because
  # nothing failed). A pass-through here would be the same mistake repainted.
  #
  # Until the port reproduces the reference output, calling this is an error, and
  # `rake conformance` is where you watch it stop being one.
  #
  # @param text [String] the composition to redact.
  # @param identity [Object] the student the detector is told about. Every
  #   reference arm interpolates these strings, so a caller that omits them is
  #   measuring a different system.
  # @raise [NotPortedError] always, for now.
  def self.redact(text, identity)
    _ = text
    _ = identity
    raise NotPortedError,
          "the Ruby detector is not ported yet. This deliberately raises rather " \
          "than returning the text unchanged, because a redactor that silently " \
          "does nothing is worse than one that is absent: the caller cannot " \
          "tell. Track progress with `rake conformance`; use the Python package " \
          "(pip install vicary) until it reports 35 of 35."
  end
end
