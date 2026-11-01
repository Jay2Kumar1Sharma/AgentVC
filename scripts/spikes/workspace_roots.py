"""Spike D7 (plan §7.1): where does `.agentvc/` belong with git worktrees, nested repositories and
monorepo subfolders, and what does a snapshot of the outer repository capture?

Builds, in a scratch folder:

    main/                         a repository with a commit
    main/packages/app/            a monorepo subfolder
    main/vendor/nested/           a nested repository (its own .git)
    main/.claude/worktrees/wt/    a linked worktree inside the project (Claude Code's layout)
    sibling_worktree/             a linked worktree next to the project
    no_git/                       a plain folder

For each start folder it records what `git rev-parse` reports (top level, linked worktree or
not). Then it snapshots `main` the way AgentVC would (`git add -A` into a private index and
store) and records how the nested repository and the inner worktree end up in the snapshot.

Output uses the labels above, never real paths. Git runs with an empty global config.

Usage:
    python scripts/spikes/workspace_roots.py --out results/spikes/2026-10-18
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


class Git:
    def __init__(self, workdir: Path) -> None:
        empty = workdir / "empty-gitconfig"
        empty.write_text("", encoding="utf-8")
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "spike", "GIT_AUTHOR_EMAIL": "",
                    "GIT_COMMITTER_NAME": "spike", "GIT_COMMITTER_EMAIL": ""}  # fmt: skip

    def run(self, *args: str, cwd: Path | None = None, index: Path | None = None) -> str:
        env = dict(self.env, GIT_INDEX_FILE=str(index)) if index else self.env
        result = subprocess.run(
            ["git", "-c", "core.autocrlf=false", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return result.stdout.strip()


def _commit_file(git: Git, repo: Path, name: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    git.run("add", "-A", cwd=repo)
    git.run("commit", "-q", "-m", name, cwd=repo)


def build(git: Git, root: Path) -> dict[str, Path]:
    main = root / "main"
    main.mkdir(parents=True)
    git.run("init", "-q", str(main))
    _commit_file(git, main, "README.md")
    _commit_file(git, main, "packages/app/app.txt")
    nested = main / "vendor" / "nested"
    nested.mkdir(parents=True)
    git.run("init", "-q", str(nested))
    _commit_file(git, nested, "lib.txt")
    git.run("worktree", "add", "-q", "-b", "inner", str(main / ".claude" / "worktrees" / "wt"),
            cwd=main)  # fmt: skip
    git.run("worktree", "add", "-q", "-b", "sibling", str(root / "sibling_worktree"), cwd=main)
    (root / "no_git").mkdir()
    return {
        "main": main,
        "monorepo_subfolder": main / "packages" / "app",
        "nested_repo": nested,
        "nested_worktree": main / ".claude" / "worktrees" / "wt",
        "sibling_worktree": root / "sibling_worktree",
        "no_git": root / "no_git",
    }


def _label(path: Path, places: dict[str, Path]) -> str | None:
    for label in ("nested_worktree", "nested_repo", "sibling_worktree", "main"):
        if path.resolve() == places[label].resolve():
            return label
    return "unlabelled"


def run(workdir: Path) -> dict[str, Any]:
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    git = Git(workdir)
    places = build(git, workdir / "layout")

    roots = []
    for start, folder in places.items():
        try:
            top = Path(git.run("rev-parse", "--show-toplevel", cwd=folder))
            git_dir = Path(git.run("rev-parse", "--absolute-git-dir", cwd=folder))
            common = (folder / git.run("rev-parse", "--git-common-dir", cwd=folder)).resolve()
            roots.append({"start": start, "toplevel": _label(top, places),
                          "is_linked_worktree": git_dir.resolve() != common})  # fmt: skip
        except RuntimeError:
            roots.append({"start": start, "toplevel": None, "is_linked_worktree": False})

    store, index = workdir / "store", workdir / "snapshot.index"
    git.run("--git-dir", str(store), "init", "-q")
    main = places["main"]
    git.run("--git-dir", str(store), "--work-tree", str(main), "add", "-A", index=index)
    entries = git.run("--git-dir", str(store), "ls-files", "-s", index=index).splitlines()

    def how(prefix: str) -> str:
        found = [e for e in entries if e.split("\t", 1)[1].startswith(prefix)]
        if not found:
            return "absent"
        if any(e.startswith("160000") for e in found):
            return "gitlink"
        return "files"

    snapshot = {
        "main_files_captured": any(e.endswith("\tREADME.md") for e in entries),
        "nested_repo": how("vendor/nested"),
        "nested_worktree": how(".claude/worktrees/wt"),
    }
    print(json.dumps({"roots": roots, "outer_snapshot": snapshot}, indent=2))
    version = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
    return {
        "environment": {"os": platform.system(), "git": version.stdout.strip()},
        "roots": roots,
        "outer_snapshot": snapshot,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args(argv)
    if args.workdir:
        result = run(args.workdir)
    else:
        with tempfile.TemporaryDirectory(prefix="agentvc-d7-") as tmp:
            result = run(Path(tmp) / "w")
    result["meta"] = {"script": "scripts/spikes/workspace_roots.py",
                      "date": date.today().isoformat()}  # fmt: skip
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "workspace_roots.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
