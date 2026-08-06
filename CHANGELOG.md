# Changelog

## 0.1.0 — unreleased

First release as a standalone package. The code previously lived inside an
essay-scoring pipeline; extracting it changed the following, and nothing else.

### Configuration

* Every environment variable moved from the host application's `GRADER_*`
  namespace into `VICARY_*`. **The old names are still read**, at lower
  precedence, each logging a one-time deprecation warning naming its
  replacement. `ENVIRONMENT` is unchanged and not deprecated — it is a host
  convention, not a name this library owns.
* All resolution now lives in `vicary.config`, with one precedence order tested
  in one place.
* `scrible-prod` no longer counts as a production environment name. The generic
  `prod` / `production` / `live` still do, and a host registers its own with
  `config.add_production_alias()`.
* `guardrail` mode no longer falls back to a hardcoded AWS region. Set
  `VICARY_BEDROCK_GUARDRAIL_REGION`, or let boto3's own chain resolve it; an
  unset region raises `NoRegionError` rather than silently querying the wrong
  region, which used to present as a missing Guardrail.

### API

* `PIIRedactor` → `Redactor`; `LocalPIIClassifier` → `LocalNameClassifier`.
* `vicary/__init__.py` exports the small surface a host needs: `Redactor`,
  `StudentIdentity`, `build_redactor_if_enabled`, `redaction_mode`, and the mode
  constants.

### The data asset

* Ships as package data in the wheel rather than reaching deployment through a
  build-time file-copy allowlist. That allowlist had already silently dropped a
  runtime file once, producing an image that built clean and failed at request
  time.
* New `data/MANIFEST.json`: SHA-256, byte count, per-tier counts, cut date,
  upstream sources, and `min_package_version` per asset.
* `load()` now verifies the bundled asset against the manifest and refuses an
  asset that requires a newer release than the installed one. The pre-existing
  format-header and tier-count checks are unchanged.
* New `VICARY_ASSET_PATH` override, accepting a file or a directory. An
  overridden asset is checked against its own header, not the bundled manifest —
  pointing elsewhere is the point of the override.
* New `vicary-assets` CLI: `show`, `verify`, `fetch`.
* The gazetteer builder now identifies itself to Wikidata and the Census as
  `vicary-gazetteer/<version>`, with `USER_AGENT_SUFFIX` for a deployment's own
  contact address.

### Detection

* A **first-person relation attached to a name now overrides a `title`-tier
  keep**: "My neighbor Alice Adams" redacts, where before the 1921 novel of that
  name kept it. 578 keys in the tier are a common given name beside an ordinary
  US surname, and every one of them sheltered whichever private individual
  carries it — measured through `redact_inbound`, 578 of 578 leaked. The
  override recovers 508; the remaining 70 resolve in another tier and are
  untouched by it.

  No threshold could have fixed this. The distributions interleave — Atticus
  Finch is at 17 sitelinks, the hole "Alice Adams" at 24 — so raising the floor
  to 25 costs Atticus Finch, Peter Parker and Clark Kent to close a quarter of
  the holes. The evidence had to come from the sentence instead.

  The rule is deliberately stricter than the bare-surname refusal it is modelled
  on: it requires a **first-person** relation, and requires it **attached** to
  the name. Reusing the surname rule unchanged refuses six of the seven
  curriculum characters, because characters are described *by* their relations
  ("Atticus Finch is the kind of father who…"); dropping attachment redacts a
  book whenever a student says who they read it with ("I read Harry Potter with
  my little brother"). Both frames are in the fixture, held out, in both
  directions.

  On 27 un-scrubbed student documents the override fires 0 times, which is why
  over-firing on prose is unchanged at 1.20 — an explained equality, not a
  coincidental one. Off with `title_relation_refusal=False`, or
  `vicary-eval --no-title-relation-refusal` for the control arm.

* **The override reaches the `full` tier too, on a measurement that retracts the
  reason it did not.** The scoping was justified in writing by "overriding
  `full` would also redact 'my hero Abraham Lincoln'". It does not: *hero*,
  *muse*, *inspiration* and *role model* are admiration invocations, they attach
  to a public figure as readily as to a relative, and none of them is a relation
  cue. The hole they were protecting is **57× the title one** — 33,682
  name-shaped keys in `full`, of which 33,269 shelter a private individual, and
  the override recovers 33,182. Of the eight probes that would have to break for
  the original claim to hold, seven are unchanged and the eighth is "my coach
  Steve Kerr", which redacts and should. Pinned by two held-out frames,
  `private-person-shadowed-by-a-real-public-figure` and
  `admiration-invocation-before-a-public-figure`, both verified to flip when the
  override is scoped back to `title`.

* **A relation-led title no longer shelters an actual relative.** 41 keys lead
  with a first-person relation — "My Cousin Vinny", "My Sister Eileen", "My
  Brother Nikhil" — and the tier matches case-insensitively, so "My cousin Vinny
  Delgado came over that summer" matched the 1992 film, blocked generation across
  the whole phrase, and shipped the name. That is the fixture's commonest frame
  (`kinship-possessive`) wearing a film's title. The writer supplies the
  discriminator: a title is title-cased and a sentence about a relative is not,
  the same orthographic evidence the heading rule reads, gated on the document
  capitalising at all. Measured across all 41: **41/41 survive when written as a
  title, 31/31 of the name-bearing ones are removed when written as prose.**

* `Span.redacted_by` distinguishes a span the gazetteer has never heard of
  (`"absence"`) from one it vouches for that the sentence overrides
  (`"context"`). Both directions are asserted: an `"absence"` span that resolves
  notable is an asset defect, and a `"context"` span that does *not* is a frame
  measuring nothing.

### Measurement

* The eval harness ships with the library. Its gates are pytest tests in
  `tests/test_gates.py`, marked `gates`, each printing its measured number.
* Corpus-dependent gates skip visibly when no corpus is configured, rather than
  passing on no data.
* `vicary-eval` no longer defaults to paths inside the former host repository.
* Deleted: the test asserting a host repository's build recipe copied the asset.
  Packaging removes that hazard; a test that reaches into another repo's build
  script cannot survive extraction. Replaced by a test that every file in
  `data/` is matched by a `package-data` glob, which is the equivalent hazard
  packaging *does* have.

### Gates, and one correction they forced

Measured at fixture `2026-08-06.3`, the arm that clears the gate set is
`local-gazetteer-lowercase` — candidate generation, the notability oracle, **and**
the case-insensitive route. The route was previously left off by default on the
belief that it cost over-firing. It does not, on this fixture: it *halves* it.

| axis | with the route | without |
|---|---|---|
| held-out recall | 100% | 90.5% |
| KEEP precision | 100% | 93.5% |
| over-firing on prose | 1.20 spans/essay | 3.36 |

Over-firing is essay-selection dependent — the same code on a different 25 essays
reads 1.16 rather than 1.20 — so the gate pins the selection along with the bar.
