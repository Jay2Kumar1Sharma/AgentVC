"""scripts/spikes/workspace_roots.py (decision D7): where .agentvc/ belongs, and what a snapshot
of the outer repository captures from nested repositories and worktrees inside it."""

import json
from pathlib import Path

import pytest

from scripts.spikes import workspace_roots


@pytest.mark.slow
def test_roots_and_nested_capture(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert workspace_roots.main(["--workdir", str(tmp_path / "w"), "--out", str(out)]) == 0
    data = json.loads((out / "workspace_roots.json").read_text(encoding="utf-8"))
    roots = {r["start"]: r for r in data["roots"]}

    assert roots["main"]["toplevel"] == "main"
    assert roots["monorepo_subfolder"]["toplevel"] == "main"
    assert roots["sibling_worktree"]["toplevel"] == "sibling_worktree"
    assert roots["sibling_worktree"]["is_linked_worktree"] is True
    assert roots["nested_worktree"]["toplevel"] == "nested_worktree"
    assert roots["nested_repo"]["toplevel"] == "nested_repo"
    assert roots["no_git"]["toplevel"] is None

    snapshot = data["outer_snapshot"]
    assert snapshot["main_files_captured"] is True
    # A plain `git add -A` records nested repositories and worktrees as gitlinks: their files
    # are NOT in the snapshot.
    assert snapshot["nested_repo"] == "gitlink"
    assert snapshot["nested_worktree"] == "gitlink"
