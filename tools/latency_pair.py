"""Measure the last release and this checkout on the SAME machine, and compare.

Why this exists, and why the stored baseline it replaces could not work.

The gate's question is "did this change make redaction slower". The obvious
implementation — record a number at each release, compare the next run against it
— was tried, shipped, and is what this replaces. It failed for a reason no
estimator can fix: **the machine is not a constant.** Measured on GitHub's
`ubuntu-latest`, thirty-six processes across six runners per port, on identical
code:

    port          spread between the fastest and slowest runner
    Ruby          67%   (6.53 ms on an Intel Xeon 6973P-C, 10.63 ms on an EPYC 7763)
    Python        26%
    TypeScript    21%   (and 3.3% between processes on ONE runner)

Against an 8% bar. In one probe run the pool served five different CPU models —
EPYC 7763, EPYC 9V74, Xeon Platinum 8370C, Xeon Platinum 8573C, Xeon 6973P-C —
and two runners of the *same* model still differed by 26%. A stored baseline
therefore decides releases by which machine the job landed on: it red-lit `main`
on unchanged code at +8.33%, and it refused RubyGems 0.2.3 while PyPI and npm
took the same commit.

Two repairs were measured and rejected before this one. A full warmup pass helps
TypeScript materially — its first four essays run at twice steady state while V8
tiers up — and is kept, in `latency_measure.*`, but it only cuts the
within-runner term. Dividing by a machine-speed calibrator (hash probing, and a
regex loop, neither touching vicary code) helped Ruby, hurt TypeScript, and left
18-25% residual spread: a dimensionless ratio does not carry the bar either.

What does work is not measuring the machine at all. Both sides of the comparison
run **here, now, interleaved**, so every property of the machine — model, clock,
co-tenancy, cache pressure — is common to both and cancels in the ratio. What is
left is within-process noise, which is 0.7% in Python, 1.7% in Ruby and 3.3% in
TypeScript, and the median over several rounds is tighter than that.

The previous release comes from this repository's own history rather than from a
registry, so this runs on a bare checkout with no network. Its `asset/` and
`conformance/` are overwritten with the current checkout's before it is built:
the gazetteer and the corpus are inputs, not code, and holding them fixed is what
makes the difference attributable to the change under test.

    python tools/latency_pair.py --impl ruby --out /tmp/pair.json

The gate reads that file. It reaches no verdict here — this writes down what was
measured, and each port decides for itself what it means.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

IMPLEMENTATIONS = ("python", "typescript", "ruby")

#: Rounds per side, per port, bought where they are needed rather than spread
#: evenly. Per-process noise on one runner is 0.7% in Python and 1.7% in Ruby,
#: where five rounds already put the pair's verdict inside +/-1%; it is 3.3% in
#: TypeScript, whose absolute figure is 2 ms and whose JIT does not settle
#: identically twice. Rounds are the cheapest thing there is in that port — a
#: TypeScript measurement costs about a third of a second — so it gets three
#: times as many, which is what brings its verdict into the same band as the
#: other two rather than leaving one port's gate three times looser.
DEFAULT_ROUNDS = {"python": 5, "typescript": 15, "ruby": 5}


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def check(cmd: list[str], cwd: Path, what: str, env: dict | None = None) -> str:
    proc = run(cmd, cwd, env)
    if proc.returncode != 0:
        sys.stderr.write(f"{what} failed: {' '.join(cmd)}\n{proc.stdout}{proc.stderr}\n")
        raise SystemExit(1)
    return proc.stdout.strip()


def repo_root() -> Path:
    here = Path(__file__).resolve().parent
    return here.parent


def previous_release(root: Path) -> tuple[str, str]:
    """The newest ``v*`` tag reachable from HEAD that is not HEAD itself.

    Excluding a tag ON HEAD is what makes this work during a release: the tag
    push that publishes 0.2.5 must compare against 0.2.4, not against itself,
    which would report 0% forever and pass every time.
    """
    head = check(["git", "rev-parse", "HEAD"], root, "resolving HEAD")
    tags = check(
        ["git", "tag", "--list", "v*", "--sort=-v:refname", "--merged", "HEAD"],
        root, "listing release tags",
    ).splitlines()
    for tag in (t.strip() for t in tags if t.strip()):
        sha = check(["git", "rev-list", "-n", "1", tag], root, f"resolving {tag}")
        if sha != head:
            return tag, sha
    sys.stderr.write(
        "no release tag before HEAD is reachable — nothing to compare against. "
        "A shallow clone is the usual cause: the pair needs tags and history, so "
        "check out with fetch-depth: 0.\n"
    )
    raise SystemExit(1)


def worktree(root: Path, ref: str, dest: Path) -> None:
    check(["git", "worktree", "add", "--detach", str(dest), ref], root, f"checking out {ref}")
    # The gazetteer and the corpus are inputs, not code. Both sides measure the
    # current ones, so a change to either cannot masquerade as a code
    # regression — and, more to the point, so that the essays being timed are
    # byte-identical on both sides. The measure scripts print a digest of the
    # corpus text and the driver refuses to compare if they disagree.
    for shared in ("asset", "conformance"):
        target = dest / shared
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(root / shared, target)


def prepare(impl: str, root: Path, tree: Path) -> None:
    """Make `tree`'s port measurable, without touching the network."""
    if impl == "python":
        # Stdlib-only, so nothing is installed: the measure script is pointed at
        # this source tree. Only the data asset has to be materialised.
        check([sys.executable, "-m", "vicary_build", "vendor",
               str(tree / "python" / "src" / "vicary" / "data")],
              tree, "vendoring the asset for the previous release")
    elif impl == "ruby":
        check(["rake", "sync_assets"], tree / "ruby",
              "vendoring the asset for the previous release")
    elif impl == "typescript":
        check(["node", "scripts/sync-assets.mjs"], tree / "typescript",
              "vendoring the asset for the previous release")
        # Compiled by THIS checkout's tsc, against this checkout's node_modules,
        # so the previous release needs no install and no registry. Neither the
        # compiler nor the type definitions are what is being measured — the
        # published library is stdlib-only and carries no runtime dependency —
        # and without the symlink `tsc` cannot resolve `@types/node` from a
        # worktree in a temporary directory.
        modules = root / "typescript" / "node_modules"
        tsc = modules / ".bin" / "tsc"
        if not tsc.exists():
            sys.stderr.write(
                f"{tsc} is missing — run `npm ci` in typescript/ first; the pair "
                f"compiles the previous release with this checkout's compiler\n"
            )
            raise SystemExit(1)
        linked = tree / "typescript" / "node_modules"
        if not linked.exists():
            linked.symlink_to(modules, target_is_directory=True)
        check([str(tsc), "-p", "tsconfig.json"], tree / "typescript",
              "compiling the previous release")


def measure(impl: str, root: Path, tree: Path | None) -> dict:
    """One measurement, of `tree`'s library or of this checkout's."""
    env = dict(os.environ)
    if impl == "python":
        src = (tree or root) / "python" / "src"
        env["VICARY_MEASURE_SRC"] = str(src)
        cmd = [sys.executable, str(root / "tools" / "latency_measure.py")]
        cwd = root / "python"
    elif impl == "ruby":
        lib = (tree or root) / "ruby" / "lib"
        cmd = ["ruby", f"-I{lib}", str(root / "ruby" / "scripts" / "latency_measure.rb")]
        cwd = (tree or root) / "ruby"
    else:
        env["VICARY_DIST"] = str((tree or root) / "typescript" / "dist")
        cmd = ["node", str(root / "typescript" / "scripts" / "latency-measure.mjs")]
        cwd = (tree or root) / "typescript"

    proc = run(cmd, cwd, env)
    if proc.returncode != 0:
        sys.stderr.write(f"measurement failed:\n{proc.stdout}{proc.stderr}\n")
        raise SystemExit(1)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--impl", required=True, choices=IMPLEMENTATIONS)
    ap.add_argument("--out", required=True, help="where to write the pair record")
    ap.add_argument("--rounds", type=int, default=None,
                    help="rounds per side; defaults per port, see DEFAULT_ROUNDS")
    args = ap.parse_args(argv)
    if args.rounds is None:
        args.rounds = DEFAULT_ROUNDS[args.impl]

    root = repo_root()
    ref, ref_sha = previous_release(root)
    head = check(["git", "rev-parse", "HEAD"], root, "resolving HEAD")

    with tempfile.TemporaryDirectory(prefix="vicary-prev-") as tmp:
        tree = Path(tmp) / "prev"
        try:
            worktree(root, ref, tree)
            prepare(args.impl, root, tree)

            previous: list[float] = []
            current: list[float] = []
            corpora: set[str] = set()
            digests: set[str] = set()
            runtimes: set[str] = set()
            # Interleaved AND counterbalanced: previous first on even rounds,
            # this checkout first on odd ones. Neither half is decoration. A
            # machine that drifts partway through — a noisy neighbour, a thermal
            # cap — biases whichever side ran last if the sides are measured in
            # blocks, so they take turns; and within a single turn the second
            # process still inherits whatever the first one did to the cache and
            # the clock, which on a loaded laptop showed up as a steady penalty
            # on whichever side ran second. Swapping the order every round is
            # what turns that from a bias into noise.
            for round_index in range(args.rounds):
                order = ((tree, previous), (None, current))
                if round_index % 2 == 1:
                    order = tuple(reversed(order))
                for side, into in order:
                    got = measure(args.impl, root, side)
                    into.append(float(got["pooled_median_ms"]))
                    corpora.add(got["corpus"])
                    digests.add(got["corpus_sha256"])
                    runtimes.add(got["runtime"])
        finally:
            run(["git", "worktree", "remove", "--force", str(tree)], root)

    if len(digests) != 1:
        sys.stderr.write(
            f"the two sides timed different essay text ({len(digests)} digests). "
            f"They are not comparable, and no record is written.\n"
        )
        return 1

    prev_ms = statistics.median(previous)
    cur_ms = statistics.median(current)
    record = {
        "document_version": 1,
        "implementation": args.impl,
        "corpus": sorted(corpora)[0],
        "corpus_sha256": sorted(digests)[0],
        "against": {"ref": ref, "sha": ref_sha},
        "head_sha": head,
        "rounds": args.rounds,
        "runtime": sorted(runtimes)[0] if len(runtimes) == 1 else "mixed",
        "machine": cpu_model(),
        "previous_ms": round(prev_ms, 6),
        "current_ms": round(cur_ms, 6),
        "previous_rounds_ms": [round(v, 6) for v in previous],
        "current_rounds_ms": [round(v, 6) for v in current],
        "regression_pct": round((cur_ms / prev_ms - 1.0) * 100.0, 4) if prev_ms > 0 else None,
    }
    Path(args.out).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    sign = "+" if (record["regression_pct"] or 0) >= 0 else ""
    print(
        f"{args.impl}: {cur_ms:.3f} ms here against {prev_ms:.3f} ms at {ref}, "
        f"measured on the same machine — {sign}{record['regression_pct']:.2f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
