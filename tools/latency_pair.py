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

That cancellation is measured, not assumed. Each port's gate statistic was run
many times against a FIXED head and tag — its own CI invocation, no extra rounds
— so the true value is constant and the spread IS the noise:

    port          sigma   95% CI        8% bar is   (inferred before)
    Python        0.60%   0.44-0.93%    13.3 sigma  0.7%
    Ruby          0.46%   0.34-0.72%    17.2 sigma  1.7%
    TypeScript    1.98%   1.56-2.69%     4.0 sigma  3.3%

n=16 per port, plus a first TypeScript probe pooled in for n=28 there. Every mean
is indistinguishable from zero. Ruby is the surprise: its pair is nearly four
times tighter than its single-process noise, because a median over five rounds
and a ratio cancel more than the component figure suggests. So the inferred
numbers were conservative in every port, never optimistic.

**Machine cancellation, tested against real CPU diversity.** 24 runner
allocations drew three models (EPYC 7763, EPYC 9V74, Xeon Platinum 8573C). The
contrast that matters is within one dataset — the absolute medians against the
ratio, from the same runs:

    port          absolute spread across models    ratio spread
    Ruby          31.8%                            0.36 pp
    Python        19.7%                            0.23 pp
    TypeScript     0.8%                            2.67 pp (n.s.)

Ruby is the proof: the machine moves its absolute figure by a third — the same
axis, and nearly the same size, as the 67% that killed the stored baseline — and
moves its ratio by a third of a percentage point. TypeScript inverts it, and that
is worth knowing too: at ~2 ms the JIT dominates the machine, so its absolute
figure barely notices the CPU while its ratio is the noisiest of the three. Its
noise was never machine noise, which is why the calibrator hurt it.

On the between-runner term specifically: with 14 runner groups pooled, F = 0.93
on df (13, 14) against a 2.51 critical value, and the variance component estimates
negative. No runner effect survives the pairing. One 8-group probe put that
component at +1.1% before pooling — a variance component on 8 groups is that
unstable, which is the reason for the pooled figure rather than the first one.

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
#: times as many.
#:
#: That does NOT equalise the three, and an earlier version of this comment
#: claimed it did. Measured: the gate statistic lands at sigma 0.60% in Python
#: and 0.46% in Ruby on five rounds, and 1.98% in TypeScript on fifteen. So
#: TypeScript's gate really is three to four times looser than the other two,
#: and the extra rounds narrow that gap rather than closing it. It is left there
#: on purpose: 1.98% still puts the 8% bar 4.0 sigma out, which is a gate worth
#: having, and closing the gap is not worth what it costs: sigma falls as
#: 1/sqrt(rounds), so matching Ruby's 0.46% means about 19 times the rounds —
#: 280-odd, up from fifteen — to sharpen a decision that is not close either way.
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


def _version_tuple(text: str) -> tuple[int, ...]:
    """``"0.2.10"`` -> ``(0, 2, 10)``. Anything unparseable sorts lowest."""
    parts: list[int] = []
    for piece in text.strip().split("."):
        digits = "".join(c for c in piece if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _asset_is_readable_by(root: Path, version: str) -> tuple[bool, str]:
    """Whether a release at `version` can read THIS checkout's asset payload.

    The manifest already answers this: every entry carries a
    ``min_package_version``, which is the format contract between the payload and
    the code that reads it. Sharing an asset across that line hands old code a
    payload it was never written for.
    """
    manifest = root / "asset" / "data" / "MANIFEST.json"
    if not manifest.exists():
        return True, ""
    entries = json.loads(manifest.read_text()).get("assets", {})
    have = _version_tuple(version)
    gating = sorted(
        name for name, entry in entries.items()
        if _version_tuple(str(entry.get("min_package_version", "0"))) > have
    )
    if not gating:
        return True, ""
    return False, ", ".join(gating)


def worktree(root: Path, ref: str, dest: Path) -> None:
    check(["git", "worktree", "add", "--detach", str(dest), ref], root, f"checking out {ref}")
    # The corpus is an input, not code, and both sides must time the SAME essays:
    # the measure scripts print a digest of the corpus text and the driver refuses
    # to compare if they disagree. So it is always this checkout's.
    #
    # The gazetteer and the word lists are inputs too, and the same reasoning
    # normally applies — a change to either must not be able to masquerade as a
    # code regression. But the payload is versioned, and the previous release may
    # predate its current format. `min_package_version` in the manifest is exactly
    # that line: 0.2.10 split `stop_words.txt` into two files, so a 0.2.8 reader
    # handed this checkout's asset raises `lexicon "stop_words" missing` and the
    # pair dies before it measures anything — which is how a green `just ci` on a
    # developer box shipped a red CI on the release tag. Below the line the
    # previous release reads its OWN asset, and the difference is named on stdout
    # rather than left to be inferred from a number.
    target = dest / "conformance"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(root / "conformance", target)

    version = (dest / "VERSION").read_text().strip() if (dest / "VERSION").exists() else "0"
    readable, gating = _asset_is_readable_by(root, version)
    if readable:
        target = dest / "asset"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(root / "asset", target)
    else:
        print(
            f"{ref} ({version}) predates this checkout's asset format — "
            f"{gating} declares a higher min_package_version — so it is timed "
            f"against its own asset. The corpus is still shared, so the essays "
            f"are identical; the word lists are not.",
            flush=True,
        )


def prepare(impl: str, root: Path, tree: Path) -> None:
    """Make `tree`'s port measurable, without touching the network."""
    if impl == "python":
        # Stdlib-only, so nothing is installed: the measure script is pointed at
        # this source tree. Only the data asset has to be materialised.
        #
        # From the WORKTREE's own builder, which is what the other two ports have
        # always done — they run `rake sync_assets` and `sync-assets.mjs` out of
        # the tree being prepared, while this one ran the builder installed in the
        # developer's venv, i.e. always this checkout's. That is invisible while
        # the asset is shared (`worktree` copies this checkout's asset in, so the
        # two are the same code and the same payload) and wrong the moment it is
        # not: below the format floor the tree keeps its own asset, and this
        # checkout's builder vendored a payload the previous release cannot read.
        # PYTHONPATH rather than a cwd change because `vicary_build` is installed
        # editable in the venv, so only an earlier sys.path entry displaces it.
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(tree / "asset"), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        check([sys.executable, "-m", "vicary_build", "vendor",
               str(tree / "python" / "src" / "vicary" / "data")],
              tree, "vendoring the asset for the previous release", env=env)
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
