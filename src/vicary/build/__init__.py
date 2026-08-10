"""Build-time tooling: regenerate the data asset from its public upstreams.

Nothing on the redaction path imports this. It reaches the network and it is slow
(a full Wikidata sweep plus the US Census surname file), which is exactly why the
result ships as a prebuilt asset instead of being fetched at runtime.

Entry point: ``python -m vicary.assets fetch``, which drives
:func:`vicary.build.gazetteer.main` and then rewrites the asset manifest so the
checksum stays honest.
"""
