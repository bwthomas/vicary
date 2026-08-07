# Changelog

## Unreleased — the 2026-08-06 cut

### Two build defects that made every tier change invisible

* **`vicary-assets fetch` was a silent no-op.** The builder resolved its output
  against its own directory, writing `src/vicary/build/data/notability.txt.gz`,
  which nothing loads — the runtime and `MANIFEST.json` both read
  `src/vicary/data/`. So a full Wikidata rebuild fetched everything, wrote to a
  dead path, rewrote the manifest by checksumming the *old* asset, verified that
  same old asset against its own fresh checksum, and printed a pass. Pinned by
  `test_the_builder_writes_where_the_runtime_reads`.
* **`write_asset` iterated a hardcoded five-tier list.** A tier added to
  `build_tiers` and not to that list built cleanly, reported its count in the
  build log, and read back empty — which for a KEEP tier means redacting
  everything it was built to protect. It now writes every tier the fold
  produced, and the round-trip is asserted against the fold's own output so the
  *next* tier is covered without anybody remembering.
* **The shipped asset's tier counts are now reconciled with the manifest's**, on
  the frozensets a loaded process actually holds rather than on the build log —
  the log said 1,044 demonyms while the running gazetteer held 0. The reader's
  tier names live in one place (`gazetteer.TIER_NAMES`), the parser and the
  dataclass are asserted against it, and `load()` logs every tier rather than a
  hand-written five, which is where an operator would have seen `demonym=0`.

### New `demonym` tier — asset format 2 → 3

* 1,044 English demonyms from Wikidata `P1549`. Closes `Cuban` in "your Cuban
  heritage", 1 of the 3 residual over-fires on real generated feedback.
* Subtracted by the `given` tier and by any surname with 10,000+ US bearers —
  2.5× stricter than the short tier, because a demonym is the only keep with no
  notability evidence behind it. `Horner` forced the number: a demonym of Horn
  and 23,881 Americans' surname.
* Reachable by the first-person relation override, so "my coach Cornish"
  redacts while "my Cuban heritage" keeps.

### Carrying a keep across the two passes

* `redact_inbound` records the bare tokens of the notable full names it kept;
  `redact_outbound` treats them as topical. Closes `Narciso` and `Narciso's`,
  the other 2 residual over-fires — a case no lookup can reach, because the
  `given` tier is built *from* the first tokens of the full tier.
* Sound because outbound text is generated from inbound-redacted input, so a
  masked name never reached the model. Off with `carry_notable_keeps=False` for
  a host whose outbound text has another source.
* Refused for the student's own name and for a token a second, private full name
  in the same essay also claims.
* A keep now folds the possessive, so a `keep` entry for "Narciso" also covers
  "Narciso's". This reaches the `prompt_context` leg too.

### Census exposure 1.47% → 1.20%

* `PLACE_MIN_SITELINKS_SINGLE_TOKEN` 100 → 150, with held-out figure recall
  **unchanged** at 60.3% and Washington/Delaware/Jordan all surviving.
* The obvious lever was measured and rejected: reaching 1.4% via the short
  tier's Census bar costs **Poe, Milton, Swift, Dahl and Thurman**, and leaves
  `saavedra` and `hathaway` — the entries the regression was attributed to —
  untouched, because they sit below the cut. The place tier had never been
  Census-examined at all.
* The 100–149 band is mostly *foreign* first-level subdivisions arriving through
  `Q10864048`: French départements, Spanish and Italian provinces, Brazilian and
  Mexican states. A Spanish province is named for its capital, so the
  administrative reading readmits the settlement names the settlement exclusion
  removes. Cost: bare `Auschwitz`, `Alsace`, `Burgundy`, `Bohemia`, `Anatolia`.
* The Census control now counts the demonym tier (0.025pp) — a keep on a bare
  token is exactly what it measures, and omitting it would make a new tier look
  free. Gate ceiling 1.5 → 1.25.

### Build tooling

* `--cache-dir` persists the raw SPARQL rows, so a threshold sweep re-folds them
  offline instead of re-issuing ~30 queries per candidate value against donated
  infrastructure. Delete the directory after changing a query.
* `vicary.eval.overfire.measure` takes `sources=`, running the two-leg shape a
  host runs — inbound on the essay, then outbound. Run cold it cannot see a
  cross-pass fix at all, which is the same defect as scoring fields unbatched,
  one leg further out.

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
* `examplecorp-prod` no longer counts as a production environment name. The generic
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

* **Absence of capitals stopped counting as evidence of a writer who drops
  them.** `document_capitalises_names` answers "did this document capitalise its
  proper nouns", and a **no** has two causes that were being treated as one: the
  writer drops capitals — the case the lowercase route exists for — or the text
  contains no proper nouns at all. Only the first should reach the permissive
  path.

  The second is what the outbound pass sees. Its inputs are single feedback
  fields of 108-290 characters, ordinary prose about a student's essay; every one
  scored zero mid-sentence capitals, every one was read as lower-case writing,
  and the route ran with no corroboration required. "tone toward", "line makes",
  "line circles" and "line loops" masked as names in text a student reads. Both
  `tone` and `line` are genuine given names, so the seed was legitimate and the
  absent guard was the whole defect.

  New `writes_without_standard_capitals` requires a *positive* tell — a sentence
  opening in lower case, or a bare lower-case "i" — rather than inferring one
  from silence. Held-out recall, KEEP precision and the leak count are all
  unchanged; over-firing falls **1.20 → 0.72 spans/essay inbound** and **0.57 →
  0.29 spans/response outbound**.

* **An opening quote now counts as a sentence start.** The pattern knew about a
  *closing* quote after terminal punctuation and not about an opening one, so
  "vivid words like 'Giggles filled the school'" read that capital as the
  writer's choice. Quoting the student's own words is how feedback refers to
  them. Outbound over-firing **0.29 → 0.21**.

  Two things this surfaced, both older than it. `_corroborated` stripped
  punctuation for the capitalisation channel and not before the given-name tier,
  so `Terrence'` — the candidate pattern keeps the apostrophe so O'Brien survives
  — was asked about verbatim and answered no; a quoted first name would have
  started leaking. And a trailing apostrophe rode into the masked span, so
  masking ate the closing quote: "Words like '{NAME_1} stand out". Both fixed,
  both pinned.

* **The over-fire harness now batches the way the host does.** `measure()` took
  groups, documented at length why grouping matters, and then masked each field
  separately — the one shape no host runs. `redact_outbound_batch` joins every
  field of a response into ONE pass, and two document-level signals are computed
  over whatever arrives, so a 191-character field and the 1,656-character
  response it belongs to are different inputs. Pinned by a test where it changes
  the answer: "Narciso Rodriguez" in one field and a bare "Rodriguez" in the next
  over-fires scored apart and does not scored together.

* **The two directions are separately configurable**, via
  `VICARY_NAME_DETECTION_OUTBOUND`. Unset it inherits the inbound level, so this
  changes no deployment; set, it builds a second classifier for the outbound
  pass only. It exists because the error costs are not the same and one setting
  could not express both: inbound an over-redaction is a placeholder in text the
  model already reads as `@PERSON1`, outbound it is a hole in feedback a student
  reads, and the residual there is real at 0.57 spans per response.

  Two things this needed that were not obvious. The direction was **not
  represented anywhere in the call** — both passes invoke `ApplyGuardrail` with
  `source="OUTPUT"`, because `ANONYMIZE` only masks there — so `_apply` takes an
  explicit `outbound` flag rather than inferring one. And equal levels share one
  classifier rather than building two identical ones, because two objects that
  agree today can stop agreeing and the outbound number would then move with
  nothing in the configuration to explain it.

  Which level outbound *should* be is a measurement, not a default, and it has
  not been made yet. The dial had to exist before the question could be asked.

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
