"""Measurement: the fixture, the held-out figure list, and the scoring harness.

The gates that decide whether a change to this library is an improvement live in
``tests/test_gates.py`` and read this package. They live *with* the library on
purpose — a library whose only measurement lives in a downstream consumer is a
library whose measurement rots the first time that consumer reorganises.

The essay corpora these modules score against are licensed third-party data and
are **not** packaged. Corpus-dependent measurements skip, loudly, when
``VICARY_EVAL_CORPUS_TSV`` is unset; the fixture-based ones need nothing.
"""
