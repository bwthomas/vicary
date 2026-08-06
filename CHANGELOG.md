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
