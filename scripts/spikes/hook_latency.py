"""Spike S1/D1 (plan §7.1): how long does one hook call cost when each call is a new process?

Measures, end to end from the caller's side (as the agent sees it):

- python_noop             a bare interpreter start (`python -c pass`)
- hook_minimal            a handler that reads a JSON payload from stdin and exits
- hook_minimal_isolated   the same with `-I -S` (no site-packages; AgentVC is stdlib-only)
- hook_via_bash           the minimal handler launched through bash, as Claude Code does on
                          Windows (skipped when bash is not on PATH)
- snapshot_first          per repo size: the first snapshot of a generated test repo
- snapshot_unchanged      per repo size: a snapshot when nothing changed since the last one
- snapshot_one_file_changed  per repo size: a snapshot after one file changed

A snapshot is what AgentVC's snapshotter would do (plan §7): `git add -A` into a private index
and object store outside the work tree, then `git write-tree`, run inside the handler process.

Usage:
    python scripts/spikes/hook_latency.py --sizes 1000 10000 100000 --runs 50 \\
        --out results/spikes/2026-10-18
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

PAYLOAD = json.dumps(
    {
        "session_id": "00000000-0000-0000-0000-000000000000",
        "transcript_path": "~/.claude/projects/x/s.jsonl",
        "cwd": "~/project",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hello", "description": "Print hello"},
        "tool_use_id": "toolu_0000000000000000000000",
    }
)

MINIMAL_HANDLER = """\
import json, sys
json.loads(sys.stdin.read())
"""

SNAPSHOT_HANDLER = """\
import json, os, subprocess, sys
json.loads(sys.stdin.read())
repo, store, index = sys.argv[1:4]
env = dict(os.environ, GIT_INDEX_FILE=index)
git = ["git", "-c", "core.autocrlf=false", "--git-dir", store, "--work-tree", repo]
subprocess.run(git + ["add", "-A"], env=env, check=True, capture_output=True)
subprocess.run(git + ["write-tree"], env=env, check=True, capture_output=True)
"""


def _stats(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 1),
        "max_ms": round(ordered[-1], 1),
        "mean_ms": round(statistics.fmean(ordered), 1),
    }


def _time_ms(command: list[str], before: Callable[[], None] | None = None) -> float:
    if before:
        before()
    start = time.perf_counter()
    subprocess.run(command, input=PAYLOAD, text=True, check=True, capture_output=True, timeout=900)
    return (time.perf_counter() - start) * 1000


def _measure(
    name: str, command: list[str], runs: int, before: Callable[[], None] | None = None, **extra: Any
) -> dict[str, Any]:
    _time_ms(command, before)  # warm-up: file-system cache, antivirus first scan
    samples = [round(_time_ms(command, before), 2) for _ in range(runs)]
    result = {"name": name, "runs": runs, **extra, **_stats(samples), "samples_ms": samples}
    print(
        f"  {name:<28}{extra.get('files', ''):>8}  p50 {result['p50_ms']:>8} ms"
        f"  p95 {result['p95_ms']:>8} ms"
    )
    return result


def make_repo(root: Path, files: int) -> None:
    """Generate ``files`` small text files, 100 per folder, with deterministic content."""
    for i in range(files):
        folder = root / f"d{i // 100:04d}"
        if i % 100 == 0:
            folder.mkdir(parents=True, exist_ok=True)
        (folder / f"f{i:06d}.txt").write_text(f"file {i}\n" + "x" * 200 + "\n", encoding="utf-8")


def _file_changer(target: Path) -> Callable[[], None]:
    """Return a function that gives ``target`` new content on every call."""
    counter = [0]

    def change() -> None:
        counter[0] += 1
        target.write_text(f"changed {counter[0]}\n", encoding="utf-8")

    return change


def _environment() -> dict[str, Any]:
    git = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "git": git.stdout.strip(),
        "bash_on_path": shutil.which("bash") is not None,
    }


def run(sizes: list[int], runs: int, workdir: Path) -> dict[str, Any]:
    py = sys.executable
    workdir.mkdir(parents=True, exist_ok=True)
    minimal = workdir / "minimal_handler.py"
    minimal.write_text(MINIMAL_HANDLER, encoding="utf-8")
    snapshot = workdir / "snapshot_handler.py"
    snapshot.write_text(SNAPSHOT_HANDLER, encoding="utf-8")

    print("Process start:")
    results = [
        _measure("python_noop", [py, "-c", "pass"], runs),
        _measure("hook_minimal", [py, str(minimal)], runs),
        _measure("hook_minimal_isolated", [py, "-I", "-S", str(minimal)], runs),
    ]
    bash = shutil.which("bash")
    if bash:
        line = f'"{Path(py).as_posix()}" "{minimal.as_posix()}"'
        results.append(_measure("hook_via_bash", [bash, "-c", line], runs))

    for files in sizes:
        print(f"Snapshot, {files} files (generating repo...):")
        repo, store, index = (
            workdir / f"repo-{files}",
            workdir / f"store-{files}",
            workdir / f"index-{files}",
        )
        for path in (repo, store):
            shutil.rmtree(path, ignore_errors=True)
        index.unlink(missing_ok=True)
        make_repo(repo, files)
        subprocess.run(["git", "--git-dir", str(store), "init", "-q"], check=True)
        command = [py, "-I", "-S", str(snapshot), str(repo), str(store), str(index)]

        start = time.perf_counter()
        subprocess.run(
            command, input=PAYLOAD, text=True, check=True, capture_output=True, timeout=3600
        )
        first = round((time.perf_counter() - start) * 1000, 2)
        results.append(
            {
                "name": "snapshot_first",
                "files": files,
                "runs": 1,
                **_stats([first]),
                "samples_ms": [first],
            }
        )
        print(f"  {'snapshot_first':<28}{files:>8}  {first:>8} ms")

        size_runs = max(3, runs // 5) if files >= 100_000 else runs
        results.append(_measure("snapshot_unchanged", command, size_runs, files=files))
        touch_one = _file_changer(repo / "d0000" / "f000000.txt")
        results.append(
            _measure("snapshot_one_file_changed", command, size_runs, before=touch_one, files=files)
        )
    return {"environment": _environment(), "measurements": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10_000, 100_000])
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--out", type=Path, required=True, help="folder for the JSON result")
    parser.add_argument("--workdir", type=Path, help="scratch folder (default: a temp folder)")
    args = parser.parse_args(argv)

    if args.workdir:
        result = run(args.sizes, args.runs, args.workdir)
    else:
        with tempfile.TemporaryDirectory(prefix="agentvc-spike-") as tmp:
            result = run(args.sizes, args.runs, Path(tmp))
    result["meta"] = {
        "script": "scripts/spikes/hook_latency.py",
        "date": date.today().isoformat(),
        "sizes": args.sizes,
        "runs": args.runs,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    name = f"hook_latency-{platform.system().lower()}.json"
    (args.out / name).write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Wrote {args.out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
