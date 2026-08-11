# frozen_string_literal: true

# Ask RubyGems whether it is serving a version, rather than believing `gem push`.
#
# `gem push` printing "Successfully registered gem" is the system under
# measurement describing itself. The same rule that makes `release.yml` install
# the built wheel into a clean interpreter applies here: the claim worth making
# is "the registry serves it", and only the registry can make it.
#
# **Why this is a script and not a shell loop in the workflow.** The loop it
# replaces parsed the versions API with an inline `ruby -rjson -e ...` inside a
# `curl | ruby` pipe, inside a `for` loop, with `|| true` swallowing the failure.
# Every one of those is a place where "the version is not there yet" and "I could
# not tell" produce the same output — and the second is the one that must never
# read as the first, because the whole point of the step is to disbelieve a
# success message. Here the two are separate return values, and
# `test/release_test.rb` drives both without a network.
#
# Usage:
#   ruby scripts/registry_serves.rb vicary 0.2.0
#   ruby scripts/registry_serves.rb --attempts 12 --interval 10 vicary 0.2.0

require "json"
require "net/http"
require "uri"

module RegistryServes
  DEFAULT_ATTEMPTS = 12
  DEFAULT_INTERVAL = 10
  HOST = "https://rubygems.org"

  # What one lookup found. `versions` is nil when the lookup itself failed, which
  # is a different fact from "the gem exists and this version is not among them"
  # and must never collapse into it.
  Answer = Struct.new(:versions, :error, keyword_init: true) do
    def serving?(version)
      !versions.nil? && versions.include?(version)
    end

    def unknown? = versions.nil?
  end

  module_function

  # Parse the versions API payload. Separate from the fetch so the shape this
  # depends on — a JSON array of objects carrying "number" — is checked by a test
  # rather than by a release.
  def parse(body)
    parsed = JSON.parse(body)
    unless parsed.is_a?(Array)
      return Answer.new(versions: nil,
                        error: "expected a JSON array from the versions API, got #{parsed.class}")
    end

    numbers = parsed.filter_map { |v| v["number"] if v.is_a?(Hash) }
    if numbers.empty? && !parsed.empty?
      return Answer.new(versions: nil,
                        error: "the versions API returned #{parsed.size} entries and none " \
                               "carried a \"number\" field — the payload shape moved")
    end

    Answer.new(versions: numbers, error: nil)
  rescue JSON::ParserError => e
    Answer.new(versions: nil, error: "the versions API did not return JSON: #{e.message}")
  end

  def fetch(gem_name, host: HOST)
    uri = URI.parse("#{host}/api/v1/versions/#{gem_name}.json")
    response = Net::HTTP.get_response(uri)
    case response
    when Net::HTTPSuccess then parse(response.body)
    when Net::HTTPNotFound
      # The gem itself is unknown to the registry. Not an error: it is exactly
      # what a first release looks like right up until the moment it is not.
      Answer.new(versions: [], error: nil)
    else
      Answer.new(versions: nil, error: "#{uri} returned #{response.code} #{response.message}")
    end
  rescue StandardError => e
    Answer.new(versions: nil, error: "#{uri} could not be reached: #{e.class}: #{e.message}")
  end

  # Poll until the registry serves `version`, or give up out loud.
  #
  # `sleeper` and `fetcher` are injected so the test can run the real control flow
  # — including the give-up path — in microseconds and with no network.
  def wait_for(gem_name, version, attempts: DEFAULT_ATTEMPTS, interval: DEFAULT_INTERVAL,
               out: $stdout, fetcher: method(:fetch), sleeper: method(:sleep))
    attempts.times do |i|
      answer = fetcher.call(gem_name)

      if answer.serving?(version)
        out.puts "rubygems.org is serving #{gem_name} #{version}"
        return 0
      end

      if answer.unknown?
        out.puts "attempt #{i + 1}: could not read the registry — #{answer.error}"
      else
        out.puts "attempt #{i + 1}: rubygems.org lists [#{answer.versions.join(', ')}], not #{version}"
      end

      sleeper.call(interval) unless i == attempts - 1
    end

    out.puts
    out.puts "pushed #{gem_name} #{version} but rubygems.org never served it across " \
             "#{attempts} attempts."
    out.puts "The push reported success; the registry disagrees. Do not treat this"
    out.puts "release as published until that is reconciled."
    1
  end

  def main(argv, out: $stdout)
    attempts = DEFAULT_ATTEMPTS
    interval = DEFAULT_INTERVAL
    rest = []

    until argv.empty?
      case (arg = argv.shift)
      when "--attempts" then attempts = Integer(argv.shift)
      when "--interval" then interval = Integer(argv.shift)
      else rest << arg
      end
    end

    gem_name, version = rest
    unless gem_name && version
      out.puts "usage: registry_serves.rb [--attempts N] [--interval S] GEM VERSION"
      return 2
    end

    wait_for(gem_name, version, attempts: attempts, interval: interval, out: out)
  end
end

exit RegistryServes.main(ARGV) if $PROGRAM_NAME == __FILE__
