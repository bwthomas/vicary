# conformance — the spec all three front doors run against

Three files, generated from the Python implementation and consumed by every port:

| file | what it is |
|---|---|
| `frames.json` | the 51 fixture frames, the student identity the detector is told, and the **golden output** the reference arm produces for each frame |
| `gates.json` | the nine gates: what is measured, the bar, and which gates need data no package ships |
| `primitives.json` | the tokenisation and capitalisation answers underneath a frame: 18 primitives over 27 texts and 12 token lists |

Regenerate with `just sync-conformance`. Never hand-edit them: all three are
compared byte-for-byte against a fresh export by
`python/tests/test_conformance.py`, so an edit that is not a regeneration fails
the build — which is the point.

## Why there is a primitives layer as well as a frames layer

`frames.json` scores finished output. That is the right *final* bar and a poor
first one: a port with nothing implemented scores 0 of 35 and learns nothing about
which of the forty-odd primitives underneath it is wrong. The TypeScript port paid
that cost by hand — a throwaway probe that ran both implementations over a corpus
and diffed the JSON. It found a real divergence no frame would have isolated
(JavaScript's `\b` is ASCII-only where Python's is Unicode-aware, so a
transliterated `\b[a-z]` matches `ve` inside `naïve`), and every later port would
otherwise have re-derived the same expectations by hand. Hand-derived expectations
are transcription, which is what this directory exists to prevent.

So the probe became an export. `primitives.json` carries the corpus, the token
lists, the stand-in oracles, the thresholds, and one answer per primitive per
input.

**It is not scored and it is not a gate.** A port can be green against it and mask
nothing; the frames still decide whether a port works. This layer only says which
brick is crooked, and says it in one run instead of a bisect — a failure names the
primitive and the input, e.g. `lower_token[accented]`.

Three things travel with it, each because leaving it out was a real way to be
wrong:

* **The oracles are data.** `settlements` and `titles` are stand-ins, not gazetteer
  tiers, so a disagreeing primitive can never be confused with a disagreeing tier
  lookup. Their semantics are exact and a port must match them: `is_settlement` is
  `name.lower() in settlements`; `is_title` folds curly apostrophes to `'` and
  lower-cases before the lookup; `is_title_prefix(key)` is true when some title
  equals `key` or starts with `key + " "`.
* **The thresholds are data.** A port that reads the corpus off this file and its
  own thresholds off a literal it typed can pass every case here and still be tuned
  differently, because the corpus may simply not contain the input that separates
  2 from 3.
* **The corpus reaches all four capitalisation states.** A state no example
  produces is a state a port can get wrong for free — and `silent` is the one with
  a written rule against reading it as consent.

## The bar

**For every frame, all three implementations produce byte-identical masked output,
placeholder numbering included.**

Numbering is not a detail and it is not positional. In `nickname-and-full-name`
the reference output emits `{NAME_2}` before `{NAME_1}`, because numbers follow
span discovery order rather than position in the text. A port that assumes
left-to-right numbering satisfies every semantic expectation in this file, passes
every leak and keep check, and still emits a restoration mapping that is wrong —
which breaks showing a student their own words back across a service boundary.
That property is the reason to use this over a cloud redaction API, so it is
pinned as bytes rather than described in prose.

## Two layers, checking different things

**Expectations** (`frames[].spans`) are semantic: this literal, of this entity
type, must be masked (`verdict: "redact"`) or must survive intact
(`verdict: "keep"`). Satisfying them means a port redacts the right things.

**Golden output** (`golden[frame_id]`) is exact: `masked` is the byte string the
reference arm produces, `placeholders` is the placeholder tokens in order of first
appearance, and `mapping` is the `[token, original]` pairs that restore the input.
This layer catches what expectations cannot.

Golden output is a snapshot of current behaviour, so a legitimate improvement to
the detector fails conformance until it is regenerated. That cost is deliberate:
regenerating is one command and a diff a human reads, and a suite that tolerates
output changes cannot detect the divergence it exists to detect.

## What a green conformance run does not mean

Four of the nine gates need data that no package here ships — three need an essay
corpus the operator supplies, one needs the US Census surname file. Each gate
declares this in its `requires` field. A runner that cannot reach them must report
`NOT MEASURED` **by name** and must not reduce the denominator: five of nine held
is a different statement from nine of nine, and a badge cannot tell them apart.

Every port has to carry that discipline. A green JavaScript badge that means less
than the Python one is worse than no badge, because nobody will notice.

## Reading `frames.json`

`document_version` is checked, not sniffed — a reader refuses an unknown version
rather than guessing which fields moved, the same way the gazetteer asset refuses
an unknown format.

Defaulted span fields are **omitted** to keep the file readable; a reader restores
them:

| field | default |
|---|---|
| `verdict` | `"redact"` |
| `expect_count` | `null` |
| `expect` | `null` |
| `kept_by` | `"notability"` |
| `redacted_by` | `"absence"` |
| `note` | `""` |
| `held_out` (frame) | `false` |
| `prompt_context` (frame) | `""` |

`identity` is the student the detector is **told about** — every arm interpolates
those three strings. A port that omits it measures a different system and misses
the easiest spans in the fixture, which reads as a porting bug when it is a
configuration one.

`reference_arm` names the configuration the golden bytes came from
(`local-gazetteer-lowercase`: candidate generation, the offline notability oracle,
and the lowercase route). Golden bytes without their arm are unreproducible, and a
port implementing a different arm while comparing against these bytes is measuring
two differences at once.
