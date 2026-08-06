"""Measure spurious redaction on text that contains no names at all.

Recall and precision are both measured against a fixture of *known* spans. This
measures the opposite thing: what the detector does to ordinary prose nobody
asked it to touch. Every mask it produces here is a false positive by
construction, because the identity handed to the redactor appears nowhere in the
corpus.

That construction is the whole instrument. Run the same corpus with a real
identity and you cannot tell a correct mask from a wrong one without labels;
run it with an identity that cannot possibly occur and the count *is* the error
rate, no labelling pass required.

Why it earns a module rather than a scratch script: the number it produces is
the one that decides how aggressive the default detection level should be, and a
number that decides something has to be re-derivable by whoever doubts it later.

Two corpora matter and they are not interchangeable:

*Inbound* — what a writer submits. Over-redaction here is close to free: the
consumer is usually a model that never sees the original, and a placeholder in
place of a common noun costs it little. :mod:`vicary.eval.recall` already
reports this alongside its recall figures.

*Outbound* — text generated about the writer and shown back to them. Over-
redaction here is a visible product defect: a reader sees a placeholder sitting
where an ordinary word belongs and cannot tell what was removed or why. Nothing
measured this before, so the outbound behaviour of each detection level was an
assumption. It should not have been: the levels do not rank the same way on the
two corpora as intuition suggests, and the level that redacts *more* names
redacts *fewer* ordinary words.

Usage::

    python -m vicary.eval.overfire --texts feedback.txt
    python -m vicary.eval.overfire --jsonl out.jsonl \\
        --field reasoning --field actionable_suggestion

The corpus is the caller's. Nothing here ships one — text generated about real
writers is exactly the kind of data a redaction library should not be carrying
around.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from vicary.local_classifier import StudentIdentity
from vicary.redaction import (
    BATCH_SEPARATOR,
    NAMES_GAZETTEER,
    NAMES_IDENTITY,
    NAMES_LOWERCASE,
    build_redactor_if_enabled,
)

#: An identity chosen to be absent from any real corpus, so that every span the
#: detector masks is an error. Not a random string: it has to look like a
#: plausible name or the identity-interpolation leg would not build the same
#: patterns it builds in production, and the arm would measure a code path
#: nobody runs.
ABSENT_IDENTITY = StudentIdentity(
    first_name="Zephyrine",
    last_name="Quillfeather",
    school_name="Nonexistent Academy",
)

#: The levels compared, in increasing order of how hard they look.
LEVELS: tuple[str, ...] = (NAMES_IDENTITY, NAMES_GAZETTEER, NAMES_LOWERCASE)


@dataclass
class LevelResult:
    """What one detection level did to a corpus it should not have touched."""

    level: str
    documents: int
    #: How many host-level calls the documents were grouped into (one group per
    #: record, matching a host that redacts a whole response at once).
    groups: int
    characters: int
    spans: int
    documents_touched: int
    #: Masked text -> how many times it was masked. The distribution matters more
    #: than the total: twelve distinct sentence-initial verbs and twelve
    #: occurrences of one word are the same count and completely different bugs.
    by_span: collections.Counter = field(default_factory=collections.Counter)

    @property
    def spans_per_document(self) -> float:
        return self.spans / self.documents if self.documents else 0.0

    @property
    def spans_per_group(self) -> float:
        """The rate a reader experiences: over-fires per response shown."""
        return self.spans / self.groups if self.groups else 0.0

    @property
    def spans_per_1k_chars(self) -> float:
        return self.spans / self.characters * 1000 if self.characters else 0.0


def measure(groups: Sequence[Sequence[str]], level: str,
            identity: StudentIdentity = ABSENT_IDENTITY) -> LevelResult:
    """Run each group through the outbound pass at ``level`` and count the masks.

    A *group* is the set of fields a host redacts together in one call. Grouping
    is not cosmetic and this harness will not let a caller skip it: the detector
    weighs same-document evidence, so a name seen twice across two fields of one
    response is a different input than the same two fields scored apart. A host
    that batches its outbound fields and a harness that does not are measuring
    different systems.

    Which is what this function used to do. It took groups, documented why they
    mattered, and then masked **each field separately** — so it measured the one
    shape no host runs. :meth:`Redactor.redact_outbound_batch` joins every field
    of a response into ONE pass (it is a billing fix: ``ApplyGuardrail`` rounds
    each field up to a 1000-char unit), and the difference is not academic here.
    Two document-level signals are computed over whatever text arrives —
    same-document surname corroboration, and the capitalisation tell in
    :func:`~vicary.name_candidates.writes_without_standard_capitals` — so a
    191-character field and the 1,656-character response it belongs to are
    genuinely different inputs. It now joins on the same separator the host does.

    Goes through :func:`build_redactor_if_enabled` rather than constructing a
    Redactor directly, for the same reason the ``path-*`` arms in
    :mod:`vicary.eval.recall` do: a measurement of a configuration no host can
    request is a measurement of nothing.
    """
    redactor = build_redactor_if_enabled(True, identity=identity, names=level)
    if redactor is None:  # pragma: no cover - local mode always builds one
        raise RuntimeError("redaction resolved to off with an explicit True")
    classifier = redactor._classifier  # noqa: SLF001
    if classifier is None:  # pragma: no cover - local mode always has one
        raise RuntimeError(f"level {level!r} built no local classifier")
    result = LevelResult(
        level=level,
        documents=sum(len(g) for g in groups),
        groups=len(groups),
        characters=sum(len(t) for g in groups for t in g),
        spans=0,
        documents_touched=0,
    )
    for group in groups:
        fields = [t for t in group if t]
        if not fields:
            continue
        # The classifier, not the public wrapper, because only it hands back the
        # restore map — and the restore map is the finding. A bare count says the
        # detector over-fired; the spans say whether it ate a rare surname or the
        # word "Reread". Joined first, on the host's own separator, so the
        # document-level signals see the document the host shows them.
        masked = classifier.mask(BATCH_SEPARATOR.join(fields))
        restored = masked.restore_map or {}
        if not restored:
            continue
        # Per-field attribution survives the join: the separator is not
        # name-shaped, so it passes through untouched and the masked text splits
        # back into the same number of parts. If it ever does not, count the
        # group as one touched document rather than guessing which field it was.
        parts = masked.text.split(BATCH_SEPARATOR)
        if len(parts) == len(fields):
            result.documents_touched += sum(1 for p in parts if "{" in p)
        else:
            result.documents_touched += 1
        for original in restored.values():
            result.spans += 1
            result.by_span[original] += 1
    return result


def compare(groups: Sequence[Sequence[str]],
            levels: Iterable[str] = LEVELS) -> list[LevelResult]:
    return [measure(groups, level) for level in levels]


def load_groups(*, texts_path: str | None = None,
                jsonl_path: str | None = None,
                field_names: Sequence[str] = ()) -> list[list[str]]:
    """Read the corpus as groups: one group per JSONL record, or per text line.

    ``field_names`` must name EVERY field the host redacts, because which fields
    are in scope can decide the comparison rather than merely sharpen it. On one
    real corpus, scoring the explanatory field alone ranked two detection levels
    6 spans to 8; adding the imperative suggestion field the host also redacts
    reversed it to 17 to 8, because imperatives open with a capitalised verb and
    one level has no filter for a sentence-initial capital. Same detector, same
    documents, opposite conclusion. Name the whole field set.

    Blank documents are dropped rather than counted, so a trailing newline
    cannot quietly deflate a rate.
    """
    groups: list[list[str]] = []
    if texts_path:
        with open(texts_path, encoding="utf-8") as fh:
            for line in fh:
                text = line.rstrip("\n")
                if text.strip():
                    groups.append([text])
    if jsonl_path:
        if not field_names:
            raise ValueError("--jsonl requires --field")
        with open(jsonl_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                group = [
                    t for name in field_names
                    for t in _collect_field(record, name) if t.strip()
                ]
                if group:
                    groups.append(group)
    return groups


def _collect_field(node: object, name: str) -> list[str]:
    """Every string at key ``name``, at any depth, including inside JSON strings.

    The nesting tolerance is not generality for its own sake: generated output
    routinely arrives as a JSON document stored *as a string* inside another
    one, and a loader that only looked at the top level would silently return an
    empty corpus — which reads as "no over-firing" rather than "no input".
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == name and isinstance(value, str) and value.strip():
                found.append(value)
            else:
                found.extend(_collect_field(value, name))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_field(item, name))
    elif isinstance(node, str):
        stripped = node.strip()
        if stripped.startswith(("{", "[")):
            try:
                found.extend(_collect_field(json.loads(stripped), name))
            except json.JSONDecodeError:
                pass
    return found


def report(results: Sequence[LevelResult], *, examples: int = 12) -> str:
    lines: list[str] = []
    first = results[0] if results else None
    if first is not None:
        lines.append(
            f"corpus: {first.groups} responses / {first.documents} fields, "
            f"{first.characters} chars"
        )
        lines.append(
            "identity: "
            f"{ABSENT_IDENTITY.first_name} {ABSENT_IDENTITY.last_name} "
            "(absent by construction — every span below is a false positive)"
        )
        lines.append("")
    lines.append(f"{'level':22s} {'docs hit':>9s} {'spans':>7s} "
                 f"{'per resp':>9s} {'per 1k ch':>10s}")
    lines.append("-" * 62)
    for r in results:
        lines.append(
            f"{r.level:22s} {r.documents_touched:>9d} {r.spans:>7d} "
            f"{r.spans_per_group:>9.2f} {r.spans_per_1k_chars:>10.2f}"
        )
    lines.append("")
    for r in results:
        if not r.by_span:
            continue
        lines.append(f"{r.level} — {len(r.by_span)} distinct:")
        for span, count in r.by_span.most_common(examples):
            lines.append(f"    {count} x {span!r}")
        remaining = len(r.by_span) - examples
        if remaining > 0:
            lines.append(f"    … and {remaining} more distinct")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--texts", help="file with one document per line")
    ap.add_argument("--jsonl", help="JSONL file to pull a field out of")
    ap.add_argument("--field", action="append", default=[],
                    help="field to collect from --jsonl; repeat (or comma-"
                         "separate) for EVERY field the host redacts together")
    ap.add_argument("--levels", default=",".join(LEVELS),
                    help="comma-separated detection levels to compare")
    ap.add_argument("--examples", type=int, default=12,
                    help="distinct masked spans to list per level")
    args = ap.parse_args(argv)

    if not args.texts and not args.jsonl:
        ap.error("give --texts or --jsonl/--field")
    fields = [f.strip() for spec in args.field for f in spec.split(",")
              if f.strip()]
    groups = load_groups(texts_path=args.texts, jsonl_path=args.jsonl,
                         field_names=fields)
    if not groups:
        # An empty corpus makes every level report zero over-firing, which is
        # indistinguishable from a clean result and would be read as one.
        print("no documents found — check --field / the input path",
              file=sys.stderr)
        return 2
    levels = [level.strip() for level in args.levels.split(",") if level.strip()]
    print(f"fields scored: {', '.join(fields) if fields else '(one per line)'}")
    print(report(compare(groups, levels), examples=args.examples))
    return 0


if __name__ == "__main__":
    sys.exit(main())
