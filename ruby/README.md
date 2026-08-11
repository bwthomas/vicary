# vicary (Ruby)

The RubyGems front door. **Not published yet** — the gem name is claimed by a
pending trusted publisher and the first release has not run.

The detector, the data asset and the measured numbers are described in the
[project README](https://github.com/bwthomas/vicary#readme). What lives here is a
port, and the bar it has to clear before it is published is the shared
conformance suite in [`conformance/`](../conformance): for every fixture frame it
must produce **byte-identical output to the Python implementation, placeholder
numbering included**.

It clears that bar — 38 of 38 masking-required frames, 54 of 54 overall.

```ruby
require "vicary"

Identity = Struct.new(:first_name, :last_name, :school_name)
identity = Identity.new("Marguerite", "Delacroix-Whitfield", "Westfield High School")

Vicary.redact("My cousin Terrence Okonkwo came over that summer.", identity)
# => "My cousin {NAME_1} came over that summer."

masked, n, restore_map = Vicary.redact_with_report(essay, identity)
Vicary.restore(masked, restore_map) == essay   # => true
```

## Checking it

Three layers, because each catches what the one above it cannot.

| command | what it says |
|---|---|
| `rake conformance` | the scoreboard against the 52 frames — the final bar, and a coarse first one |
| `rake test` | the unit suites, including `primitives_test.rb`: forty-odd primitives over the shared corpus, which says *which brick* is crooked |
| `rake parity` | gazetteer verdicts, name by name, against the Python reference |
| `rake redaction_parity` | masked bytes against the Python reference, on prose no fixture contains |

The last two need the reference interpreter — run `just py-setup` from the
repository root first.

**Why there are four and not one**, measured on the day the port landed: of
eleven deliberate mutations to `candidates.rb`, the conformance frames caught
**one**. The primitives spec caught seven. Three were inert. The last was a real
divergence that both corpora were blind to, because both are single-line and the
rule only differs across a newline — which is what `redaction_parity` and
`test/dialect_test.rb` exist for.

## Porting notes

`lib/vicary/candidates.rb` opens with the regex-dialect differences between Ruby
and Python that run through the detector. The short version: `^` and `$` mean
*line* in Ruby and *string* in Python, so every one of them is written `\A`, `\z`
or `\Z`; `\w`, `\d` and `\s` are ASCII-only in Ruby and Unicode-aware in Python;
and `\b` — unlike JavaScript's — already agrees with Python, so the explicit
lookarounds here are belt-and-braces rather than load-bearing.
`test/dialect_test.rb` pins all of it in both directions.
