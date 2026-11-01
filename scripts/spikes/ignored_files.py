"""Spike D4 (plan §7.1): how big are git-ignored files in real repositories?

For each repository found, lists ignored entries with `git ls-files --others --ignored
--exclude-standard --directory`, walks them without following symlinks, and adds up files and
bytes per category:

- heavy    dependency, build and cache folders (node_modules, .venv, target, dist, ...)
- dotenv   files named .env or .env.*
- other    everything else, also bucketed by file size

It also adds up what the proposed default policy would snapshot: dotenv files plus "other" files
of at most 1 MB each.

Privacy: the output holds AGGREGATE numbers only. Repositories appear as repo-1, repo-2, ...;
no repository name, file name or path is written or printed.

Usage:
    python scripts/spikes/ignored_files.py --search D:/code --search ~/projects \\
        --out results/spikes/2026-10-18
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

HEAVY = frozenset(
    {
        "node_modules", ".venv", "venv", "env", ".env.d", "target", "dist", "build", "out",
        ".next", ".nuxt", ".svelte-kit", ".gradle", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".cache", ".parcel-cache", ".turbo",
        "coverage", "htmlcov", ".terraform", "bin", "obj", ".idea", ".vs", ".hypothesis",
        "site-packages", ".eggs", "bower_components", "Pods", ".dart_tool", "DerivedData",
    }
)  # fmt: skip
SIZE_BUCKETS = (
    ("le_64k", 64 * 1024),
    ("gt_64k_le_1m", 1024 * 1024),
    ("gt_1m_le_10m", 10 * 1024 * 1024),
    ("gt_10m", None),
)
POLICY_MAX_FILE = 1024 * 1024
SKIP_SEARCH = frozenset({"node_modules", "AppData", ".venv", "venv", "$Recycle.Bin", ".cache"})


def _is_dotenv(name: str) -> bool:
    return name == ".env" or name.startswith(".env.")


def _bucket(size: int) -> str:
    for name, limit in SIZE_BUCKETS:
        if limit is None or size <= limit:
            return name
    raise AssertionError("unreachable")


def _empty() -> dict[str, int]:
    return {"files": 0, "bytes": 0}


def _add(counter: dict[str, int], size: int) -> None:
    counter["files"] += 1
    counter["bytes"] += size


def scan_repo(repo: Path) -> dict[str, Any]:
    """Aggregate ignored files in one repository. Returns numbers only."""
    listed = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--others", "--ignored", "--exclude-standard",
         "--directory", "-z"],
        capture_output=True, timeout=600, check=True,
    ).stdout.decode("utf-8", "replace").split("\0")  # fmt: skip
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, timeout=600, check=True
    ).stdout.count(b"\0")

    result: dict[str, Any] = {
        "tracked_files": tracked,
        "heavy": _empty(),
        "dotenv": _empty(),
        "other": _empty(),
        "other_by_size": {name: _empty() for name, _ in SIZE_BUCKETS},
        "policy_default": _empty(),
        "symlinks_skipped": 0,
        "unreadable": 0,
    }

    def record(path: Path, heavy: bool) -> None:
        try:
            info = path.lstat()
        except OSError:
            result["unreadable"] += 1
            return
        if path.is_symlink():
            result["symlinks_skipped"] += 1
            return
        size = info.st_size
        if heavy:
            _add(result["heavy"], size)
        elif _is_dotenv(path.name):
            _add(result["dotenv"], size)
            if size <= POLICY_MAX_FILE:
                _add(result["policy_default"], size)
        else:
            _add(result["other"], size)
            _add(result["other_by_size"][_bucket(size)], size)
            if size <= POLICY_MAX_FILE:
                _add(result["policy_default"], size)

    for entry in filter(None, listed):
        path = repo / entry.rstrip("/")
        parts = Path(entry.rstrip("/")).parts
        heavy = any(part in HEAVY for part in parts)
        if path.is_dir() and not path.is_symlink():
            for folder, dirs, files in os.walk(path, followlinks=False):
                inner_heavy = heavy or any(p in HEAVY for p in Path(folder).relative_to(repo).parts)
                for name in files:
                    record(Path(folder) / name, inner_heavy)
                for name in dirs:
                    if (Path(folder) / name).is_symlink():
                        result["symlinks_skipped"] += 1
        else:
            record(path, heavy)
    return result


def find_repos(search: Path, max_depth: int = 4) -> list[Path]:
    repos: list[Path] = []
    for folder, dirs, _files in os.walk(search):
        depth = len(Path(folder).relative_to(search).parts)
        if (Path(folder) / ".git").exists():
            repos.append(Path(folder))
        dirs[:] = [d for d in dirs if d not in SKIP_SEARCH and d != ".git" and depth < max_depth]
    return sorted(repos)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--search", type=Path, action="append", required=True,
                        help="folder to search for repositories (repeatable)")  # fmt: skip
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    repos = sorted({repo for root in args.search for repo in find_repos(root.expanduser())})
    results = []
    for number, repo in enumerate(repos, 1):
        try:
            results.append({"repo": f"repo-{number}", **scan_repo(repo)})
        except (subprocess.SubprocessError, OSError):
            results.append({"repo": f"repo-{number}", "error": True})
        print(f"repo-{number}: done")
    data = {
        "meta": {
            "script": "scripts/spikes/ignored_files.py",
            "date": date.today().isoformat(),
            "os": platform.system(),
            "repos_found": len(repos),
            "policy_default": "dotenv files + other ignored files of at most 1 MB each",
        },
        "repos": results,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "ignored_files.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
