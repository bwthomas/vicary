# conformance — the spec all three front doors run against

Two files, generated from the Python implementation and consumed by every port:

| file | what it is |
|---|---|
| `frames.json` | the 51 fixture frames, the student identity the detector is told, and the **golden output** the reference arm produces for each frame |
| `gates.json` | the nine gates: what is measured, the bar, and which gates need data no package ships |

Regenerate with `just sync-conformance`. Never hand-edit them: `frames.json` is
compared byte-for-byte against a fresh export by
`python/tests/test_conformance.py`, so an edit that is not a regeneration fails
the build — which is the point.

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
