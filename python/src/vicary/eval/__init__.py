"""Measurement: the fixture, the held-out figure list, and the scoring harness.

The gates that decide whether a change to this library is an improvement live in
``tests/test_gates.py`` and read this package. They live *with* the library on
purpose — a library whose only measurement lives in a downstream consumer is a
library whose measurement rots the first time that consumer reorganises.

One corpus is shipped (``persuade-20``, permissively licensed) and is what a bare
checkout measures; the ASAP-AES corpus this library was developed against is
licensed third-party data and is **not** packaged. Corpus-dependent measurements
skip, loudly, only when the corpus that *resolves* is operator-supplied and no
``VICARY_EVAL_CORPUS_TSV`` is set; the fixture-based ones need nothing.
"""
