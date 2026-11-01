"""Spike D3 (plan §7.1): who owns the git objects a snapshot references?

AgentVC stores snapshots as git trees in its own object store (`.agentvc/objects`). Git
alternates would let that store *borrow* objects already in the user's repository instead of
copying them. This spike checks what happens to borrowed objects when the user rewrites history
and runs `git gc --prune=now`, for three set-ups:

- alternates               the store borrows from the user's repo
- own_every_object         no alternates: every object is written into the store
- alternates_then_repack   borrow while snapshotting, then `git repack -a -d` and drop the
                           alternates file (what `git clone --dissociate` does)

Each scenario: the user commits v1, then v2 (+ a new file); AgentVC snapshots the work tree into
its store and keeps a ref to it; the user then drops the v2 commit (`reset --hard HEAD~1`),
expires reflogs and prunes. The snapshot is restorable if every object it references can be read.

Git runs with an empty global config and no system config, so the user's settings (signing,
hooks) don't affect the experiment.

Usage:
    python scripts/spikes/object_ownership.py --out results/spikes/2026-10-18
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

SCENARIOS = ("alternates", "own_every_object", "alternates_then_repack")


class Git:
    def __init__(self, workdir: Path) -> None:
        empty = workdir / "empty-gitconfig"
        empty.write_text("", encoding="utf-8")
        self.env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": str(empty),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "spike",
            "GIT_AUTHOR_EMAIL": "",
            "GIT_COMMITTER_NAME": "spike",
            "GIT_COMMITTER_EMAIL": "",
        }

    def run(self, *args: str, index: Path | None = None, check: bool = True) -> str:
        env = dict(self.env, GIT_INDEX_FILE=str(index)) if index else self.env
        result = subprocess.run(
            ["git", "-c", "core.autocrlf=false", "-c", "gc.auto=0", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def has(self, git_dir: Path, obj: str) -> bool:
        return (
            subprocess.run(
                ["git", "--git-dir", str(git_dir), "cat-file", "-e", obj],
                env=self.env,
                capture_output=True,
                timeout=60,
                check=False,
            ).returncode
            == 0
        )


def _missing_objects(git: Git, store: Path, tree: str) -> int:
    """Count objects of ``tree`` (the tree itself and every blob) the store can't read."""
    if not git.has(store, tree):
        return 1
    listing = git.run("--git-dir", str(store), "ls-tree", "-r", tree, check=False)
    blobs = [line.split()[2] for line in listing.splitlines() if line]
    return sum(not git.has(store, blob) for blob in blobs)


def _restore_ok(git: Git, store: Path, tree: str, scratch: Path) -> bool:
    """Try a real restore: read the tree into a fresh index and check out every file."""
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    index = scratch.parent / (scratch.name + ".index")
    try:
        git.run("--git-dir", str(store), "read-tree", tree, index=index)
        git.run("--git-dir", str(store), "--work-tree", str(scratch), "checkout-index", "-a",
                index=index)  # fmt: skip
    except RuntimeError:
        return False
    finally:
        index.unlink(missing_ok=True)
    return True


def _store_bytes(store: Path) -> int:
    objects = store / "objects"
    return sum(
        p.stat().st_size for p in objects.rglob("*") if p.is_file() and "info" not in p.parts
    )


def scenario(name: str, root: Path, git: Git) -> dict[str, Any]:
    user, store, index = root / "user", root / "store", root / "snapshot.index"
    user.mkdir(parents=True)
    git.run("init", "-q", str(user))
    ug = ["-C", str(user)]
    (user / "a.txt").write_text("version 1\n", encoding="utf-8")
    git.run(*ug, "add", "-A")
    git.run(*ug, "commit", "-q", "-m", "v1")
    (user / "a.txt").write_text("version 2\n", encoding="utf-8")
    (user / "b.txt").write_text("only in the dropped commit\n", encoding="utf-8")
    git.run(*ug, "add", "-A")
    git.run(*ug, "commit", "-q", "-m", "v2")
    v2_blob = git.run(*ug, "rev-parse", "HEAD:a.txt")

    git.run("--git-dir", str(store), "init", "-q")
    alternates = store / "objects" / "info" / "alternates"
    if name in ("alternates", "alternates_then_repack"):
        # newline="\n": a CRLF here makes git ignore the alternates file with only a warning.
        alternates.write_text(
            (user / ".git" / "objects").as_posix() + "\n", encoding="utf-8", newline="\n"
        )

    start = time.perf_counter()
    store_git = ["--git-dir", str(store), "--work-tree", str(user)]
    git.run(*store_git, "add", "-A", index=index)
    tree = git.run(*store_git, "write-tree", index=index)
    commit = git.run("--git-dir", str(store), "commit-tree", tree, "-m", "snapshot")
    git.run("--git-dir", str(store), "update-ref", "refs/agentvc/snapshots/1", commit)
    if name == "alternates_then_repack":
        git.run("--git-dir", str(store), "repack", "-a", "-d", "-q")
        alternates.unlink()
    snapshot_ms = round((time.perf_counter() - start) * 1000, 1)

    restore_before = _restore_ok(git, store, tree, root / "restore-before")

    # The user drops the v2 commit and prunes everything unreachable.
    git.run(*ug, "reset", "-q", "--hard", "HEAD~1")
    git.run(*ug, "reflog", "expire", "--expire=now", "--all")
    git.run(*ug, "gc", "-q", "--prune=now")
    lost = not git.has(user / ".git", v2_blob)

    return {
        "name": name,
        "uses_alternates_at_restore": alternates.exists(),
        "snapshot_ms": snapshot_ms,
        "store_bytes": _store_bytes(store),
        "restore_ok_before_prune": restore_before,
        "user_repo_lost_objects": lost,
        "missing_objects_after_prune": _missing_objects(git, store, tree),
        "restore_ok_after_prune": _restore_ok(git, store, tree, root / "restore-after"),
    }


def run(workdir: Path) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    git = Git(workdir)
    results = []
    for name in SCENARIOS:
        root = workdir / name
        shutil.rmtree(root, ignore_errors=True)
        results.append(scenario(name, root, git))
        r = results[-1]
        print(
            f"{name:<24} restore before prune: {r['restore_ok_before_prune']!s:<5}  "
            f"after prune: {r['restore_ok_after_prune']!s:<5}  "
            f"missing objects: {r['missing_objects_after_prune']}"
        )
    version = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
    return {
        "environment": {
            "os": platform.system(),
            "python": platform.python_version(),
            "git": version.stdout.strip(),
        },
        "scenarios": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, help="scratch folder (default: a temp folder)")
    args = parser.parse_args(argv)
    if args.workdir:
        result = run(args.workdir)
    else:
        with tempfile.TemporaryDirectory(prefix="agentvc-d3-") as tmp:
            result = run(Path(tmp))
    result["meta"] = {
        "script": "scripts/spikes/object_ownership.py",
        "date": date.today().isoformat(),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "object_ownership.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
