# frozen_string_literal: true

require_relative "lib/vicary/version"

Gem::Specification.new do |spec|
  spec.name = "vicary"
  spec.version = Vicary::VERSION
  spec.authors = ["Blake Thomas"]
  spec.email = ["bwthomas@gmail.com"]

  spec.summary = "Offline redaction of personal names in student compositions"
  spec.description = <<~TEXT
    Finds the names a student writes about — classmates, teachers, relatives — and
    replaces them with numbered placeholders a later pass can restore, while
    leaving the public figures they are writing about alone. No model, no network,
    no per-request cost: a folded gazetteer and a candidate generator.
  TEXT
  spec.homepage = "https://github.com/bwthomas/vicary"
  spec.license = "MIT"
  spec.required_ruby_version = ">= 3.1"

  # Three links, the same three each front door declares: the repository, the
  # changelog, the issue tracker. No `source_code_uri` — it would be the homepage
  # again, and `gem build` warns when two keys carry one URI because rubygems.org
  # renders only the first. The homepage IS the source here; there is no separate
  # project site to point at.
  spec.metadata["homepage_uri"] = spec.homepage
  spec.metadata["changelog_uri"] = "https://github.com/bwthomas/vicary/blob/main/CHANGELOG.md"
  spec.metadata["bug_tracker_uri"] = "https://github.com/bwthomas/vicary/issues"

  # The gazetteer asset is vendored, not fetched: "no network, no per-request
  # cost" is the product claim, and a build-time fetch puts a fetch back in the
  # story. `rake sync_assets` populates assets/ from the one tracked copy in the
  # repository, and `rake build` runs it first.
  spec.files = Dir["lib/**/*.rb", "assets/*", "README.md", "LICENSE"]
  spec.require_paths = ["lib"]

  # No runtime dependencies, deliberately, matching the Python package. The
  # redaction path is standard library only.
end
