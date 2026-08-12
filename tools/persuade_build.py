"""Build the shipped `persuade-20` baseline corpus from its public upstream.

Why this exists. Three of the nine gates need real student prose, and for the
repository's whole history that prose was ASAP-AES — a corpus nobody may
redistribute, which every port reads through an environment variable pointing at
the operator's own copy. The consequence was not a missing feature but a missing
*measurement*: on any machine but the one that has the file, four gates report
NOT MEASURED, and a board of five greens and four blanks looks very much like a
board of nine greens to anyone scanning it. CI has never once measured them.

So this builds a second corpus that ships. PERSUADE 2.0 is the closest available
match to ASAP-AES — argumentative essays by US students in grades 6-12, and
already anonymised upstream the same way, with placeholder tokens standing in for
names — and its owner licenses it for redistribution. Twenty of its essays live
in `conformance/corpora/persuade-20/`, so a fresh checkout measures nine of nine
with no setup at all.

**The selection rule is the load-bearing part, and it is not the obvious one.**
The obvious rule — take the first twenty essays — produces a median essay of
1,607 characters against ASAP-AES set 8's 3,421. Two of the three corpus gates
are sensitive to length: latency p95 scales with the text it walks, and Ruby
currently sits at 8.8-9.9 ms against a 10 ms bar. Halving the essays would have
halved the measured latency and turned the one gate that constrains a port into a
formality, while every number on the board still read green. So the band is
ASAP-AES set 8's own first-quartile-to-maximum char range, which yields a mean of
3,336 characters against that baseline's 3,291 — 1.4% apart, so the bar keeps its
meaning across the substitution. Within the band nothing is cherry-picked: the
essays are the first twenty by id, ascending.

**On the licence, which two sources disagree about.** The Learning Agency Lab
holds the copyright and states "Persuade dataset (c) 2024 by The Learning Agency
Lab is licensed under CC BY 4.0". A widely-mirrored academic copy of the same
data instead states CC BY-NC-SA 4.0. This build relies on the owner's grant,
because it is the owner's to give; the disagreement is recorded in the shipped
NOTICE rather than resolved silently, so a reader who needs to care can see it.

Run it when the selection rule or the upstream changes, never as part of a test:

    python tools/persuade_build.py --write
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
import urllib.request
from pathlib import Path
from typing import Any

#: The unauthenticated mirror the bytes actually come from. The owner's own
#: distribution is behind a Kaggle login, which a build that must run
#: unattended cannot use; the licence relied on is still the owner's (see the
#: module docstring), and both are named in the NOTICE.
UPSTREAM_URL = (
    "https://huggingface.co/datasets/realbenpope/PERSUADE_manageable/"
    "resolve/main/persuade_full_text.csv"
)

#: sha256 of the upstream CSV this selection was drawn from. Pinned so a
#: re-run that silently gets different bytes is a loud failure rather than a
#: quietly different corpus — the essays are the baseline, so a changed
#: upstream changes what every gate number means.
UPSTREAM_SHA256 = "187c509645ea25a396572146dbad2270bb02eef32bee0e74076f530739320401"

#: Columns in the upstream CSV.
UPSTREAM_ID_COLUMN = "essay_id_comp"
UPSTREAM_TEXT_COLUMN = "full_text"

#: ASAP-AES set 8's own first-quartile and maximum essay lengths, in characters,
#: measured from the 25 essays in `conformance/carrier.json`. The band exists to
#: hold essay length — and therefore the latency gate — comparable across the
#: two corpora; see the module docstring for what the naive rule did instead.
CHAR_BAND = (2720, 4765)

#: How many essays ship. Twenty rather than ASAP-AES's twenty-five because the
#: count is not what the gates rest on and a smaller shipped artifact is
#: cheaper to review; the per-essay metrics are rates, not totals.
LIMIT = 20

#: Corpus id, and the directory it lives in under `conformance/corpora/`.
CORPUS_ID = "persuade-20"

PROFILE_DOCUMENT_VERSION = 1


def repository_root() -> Path:
    """The repository root, from this file's location."""
    return Path(__file__).resolve().parent.parent


def corpus_dir(root: Path | None = None) -> Path:
    return (root or repository_root()) / "conformance" / "corpora" / CORPUS_ID


def fetch_upstream(url: str = UPSTREAM_URL) -> bytes:
    """Download the upstream CSV. Reaches the network.

    The digest is checked by the caller rather than here, so a mismatch can
    report what it got instead of raising from inside a download.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "vicary-corpus-build"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def select(payload: bytes, *, char_band: tuple[int, int] = CHAR_BAND,
           limit: int = LIMIT) -> list[tuple[str, str]]:
    """Apply the selection rule to the upstream CSV bytes.

    Deterministic and total: every essay whose length falls in ``char_band``
    is a candidate, candidates are ordered by id ascending, and the first
    ``limit`` of them ship. No sampling, no seed, nothing to reproduce but the
    rule itself.
    """
    csv.field_size_limit(1 << 30)
    text = payload.decode("utf-8")
    low, high = char_band
    candidates: list[tuple[str, str]] = []
    for row in csv.DictReader(text.splitlines(keepends=True)):
        essay = row.get(UPSTREAM_TEXT_COLUMN) or ""
        # CRLF to LF, so a digest computed here matches one computed on any
        # platform. Recorded in the profile as the only transform applied.
        essay = essay.replace("\r\n", "\n").replace("\r", "\n")
        if low <= len(essay) <= high:
            candidates.append((row.get(UPSTREAM_ID_COLUMN) or "", essay))
    candidates.sort()
    if len(candidates) < limit:
        raise RuntimeError(
            f"upstream yielded {len(candidates)} essays in char band {char_band}, "
            f"which cannot fill a corpus of {limit}"
        )
    return candidates[:limit]


def build_documents(essays: list[tuple[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The two shipped JSON files: the essays, and the profile describing them."""
    lengths = [len(text) for _, text in essays]
    essays_document = {
        "document_version": PROFILE_DOCUMENT_VERSION,
        "corpus_id": CORPUS_ID,
        "note": (
            "Essay text, shipped. Redistributed under the owner's CC BY 4.0 grant "
            "— see NOTICE. Text is verbatim upstream but for CRLF newlines "
            "normalised to LF, so digests do not depend on the platform that "
            "wrote the file."
        ),
        "essays": [{"id": eid, "text": text} for eid, text in essays],
    }
    profile = {
        "document_version": PROFILE_DOCUMENT_VERSION,
        "id": CORPUS_ID,
        "name": "PERSUADE 2.0",
        "description": (
            "Twenty argumentative essays by US students in grades 6-12, shipped so "
            "the three corpus gates are measurable on a fresh checkout."
        ),
        "license": {
            "id": "CC-BY-4.0",
            "holder": "The Learning Agency Lab",
            "statement": (
                "Persuade dataset (c) 2024 by The Learning Agency Lab is licensed "
                "under CC BY 4.0."
            ),
            "conflicting_claim": (
                "A widely-mirrored academic copy of the same data states CC BY-NC-SA "
                "4.0. This repository relies on the copyright holder's grant above. "
                "See NOTICE."
            ),
        },
        "source": {
            "kind": "shipped",
            "text_file": "essays.json",
            "upstream_url": UPSTREAM_URL,
            "upstream_sha256": UPSTREAM_SHA256,
            "transform": "CRLF newlines normalised to LF; text otherwise verbatim",
        },
        "selection": {
            "rule": (
                "Essays whose character length falls within ASAP-AES set 8's own "
                "first-quartile-to-maximum band, ordered by id ascending, first N. "
                "The band holds essay length comparable to the ASAP-AES baseline so "
                "the latency bar keeps its meaning; the naive first-N rule yields a "
                "median of 1,607 characters against that baseline's 3,421 and would "
                "have weakened the one gate that constrains a port."
            ),
            "char_band": list(CHAR_BAND),
            "order": f"{UPSTREAM_ID_COLUMN} ascending",
            "limit": LIMIT,
            "reproducer": "tools/persuade_build.py",
        },
        "measured_lengths": {
            "count": len(essays),
            "chars_min": min(lengths),
            "chars_median": statistics.median(lengths),
            "chars_mean": round(statistics.mean(lengths)),
            "chars_max": max(lengths),
            "asap_aes_set8_mean_for_comparison": 3291,
        },
        "essays": [
            {
                "id": eid,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "chars": len(text),
            }
            for eid, text in essays
        ],
    }
    return essays_document, profile


NOTICE = """\
PERSUADE 2.0 — twenty essays, redistributed in this repository
=============================================================

`essays.json` in this directory contains twenty essays from the PERSUADE 2.0
corpus. They are not vicary's work and are not MIT-licensed; the rest of this
repository is.

    Persuade dataset (c) 2024 by The Learning Agency Lab
    is licensed under CC BY 4.0.
    https://creativecommons.org/licenses/by/4.0/

Cite the corpus as:

    Crossley, S. A., Baffour, P., Tian, Y., Franklin, A., Benner, M., & Boser, U.
    A large-scale corpus for assessing written argumentation: PERSUADE 2.0.

Two sources disagree about the licence, and this repository has taken a side.
The Learning Agency Lab, which holds the copyright, publishes the statement
above. A widely-mirrored academic copy of the same data instead states
CC BY-NC-SA 4.0. We rely on the copyright holder's grant, because it is theirs
to give — but if a NonCommercial term would matter to you, that disagreement is
the thing to check before depending on this directory.

Why these twenty and not others: `profile.json` records the selection rule, and
`tools/persuade_build.py` reproduces it from the upstream file whose digest that
profile pins. No essay was chosen by hand.

What was changed: nothing but newlines. CRLF was normalised to LF so that a
digest of an essay does not depend on the platform that wrote the file. No
redaction, truncation or reordering was applied to any essay's text.
"""


def write(root: Path | None = None, *, payload: bytes | None = None) -> list[Path]:
    """Fetch, verify, select and write all three files. Reaches the network."""
    if payload is None:
        payload = fetch_upstream()
    got = hashlib.sha256(payload).hexdigest()
    if got != UPSTREAM_SHA256:
        raise RuntimeError(
            f"upstream {UPSTREAM_URL} is sha256 {got}, and this build pins "
            f"{UPSTREAM_SHA256}. Refusing: the essays ARE the baseline, so "
            "different upstream bytes mean every gate number measured against "
            "this corpus describes a different corpus. Re-pin deliberately."
        )
    essays = select(payload)
    essays_document, profile = build_documents(essays)

    directory = corpus_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, document in (("essays.json", essays_document), ("profile.json", profile)):
        path = directory / name
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        written.append(path)
    notice = directory / "NOTICE"
    notice.write_text(NOTICE, encoding="utf-8")
    written.append(notice)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/persuade_build.py",
        description="Build the shipped persuade-20 baseline corpus from its "
                    "public upstream.",
    )
    parser.add_argument("--write", action="store_true",
                        help="write conformance/corpora/persuade-20/ in place "
                             "(default: report what a build would produce)")
    parser.add_argument("--from-file", type=Path, default=None,
                        help="read the upstream CSV from a local path instead of "
                             "the network; the pinned digest is still enforced")
    args = parser.parse_args(argv)

    payload = args.from_file.read_bytes() if args.from_file else None

    if args.write:
        for path in write(payload=payload):
            print(f"wrote {path}")
        return 0

    if payload is None:
        payload = fetch_upstream()
    got = hashlib.sha256(payload).hexdigest()
    print(f"upstream sha256 {got} {'(matches pin)' if got == UPSTREAM_SHA256 else '(DOES NOT MATCH PIN)'}")
    essays = select(payload)
    _, profile = build_documents(essays)
    print(json.dumps(profile["measured_lengths"], indent=2, sort_keys=True))
    print(f"{len(essays)} essays: {', '.join(eid for eid, _ in essays[:5])} ...")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
