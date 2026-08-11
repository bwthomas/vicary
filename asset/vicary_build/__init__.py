"""Build the data asset every front door ships, from its public upstreams.

This is the repository's build mechanism, not part of any of the three packages.
Nothing on any redaction path imports it: it reaches the network, it is slow (a
full Wikidata sweep plus the US Census surname file), and it is needed once per
asset cut rather than once per request — which is exactly why the result ships as
a prebuilt asset instead of being fetched at runtime.

It used to live inside the Python package as ``vicary.build``, which made one of
three equal front doors the owner of the shared input. Three costs, all real:
``pip install vicary`` carried a SPARQL client no host wanted; the TypeScript and
Ruby ports vendored their gazetteer out of ``python/src/vicary/data/``, so the
Python package was structurally privileged; and the build imported the Python
detector's stoplist, so the tool depended on one of its own consumers.

Entry points
------------
::

    python -m vicary_build fetch            # rebuild the asset, rewrite the manifest
    python -m vicary_build fetch --stats    # report what a rebuild would produce
    python -m vicary_build vendor <dir>     # copy the payload into one package

or, from the repository root, ``just asset-fetch`` / ``just asset-sync``.
"""

__all__ = ["config", "gazetteer", "lexicon", "manifest", "vendor"]
