"""agentvc/snapshot.py: full and edited-path snapshots, index, budgets (design note snapshots.md).

Attacks from plan §15.3 "Snapshotter" are in tests/test_snapshot_attacks.py; these are the
behaviours they build on, including a differential test against git's own `add` + `write-tree`.
"""

import errno
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import IO as BinaryStream
from typing import Any

import pytest

from agentvc.clock import Clock, Deadline, FakeClock
from agentvc.config import SnapshotConfig
from agentvc.io import IO
from agentvc.snapshot import ABSENT, UNKNOWN, Snapshotter, row_values
from agentvc.store import Store

OLD = 1_600_000_000  # a modification time far from "now": not racy


class CountingIO(IO):
    def __init__(self) -> None:
        super().__init__()
        self.opened: list[str] = []

    def open_read(self, path: Path) -> BinaryStream[bytes]:
        self.opened.append(Path(path).name)
        return super().open_read(path)


class TickingClock(FakeClock):
    """Every reading moves time on, so any deadline runs out after a few checks."""

    def monotonic(self) -> float:
        self.advance(0.01)
        return super().monotonic()


def put(ws: Path, rel: str, data: bytes = b"x", *, old: bool = True) -> Path:
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if old:
        os.utime(path, (OLD, OLD))
    return path


def snapper(ws: Path, io: IO | None = None, clock: Clock | None = None, **cfg: Any) -> Snapshotter:
    store = Store(ws)
    store.ensure(io or IO())
    return Snapshotter(
        io or IO(),
        clock or FakeClock(datetime(2026, 10, 22, tzinfo=UTC)),
        ws,
        store,
        SnapshotConfig(**cfg),
    )


def contents(s: Snapshotter, tree: str | None) -> dict[str, bytes]:
    assert tree is not None
    return {p: s.objects.read_blob(sha) for p, (_, sha) in s.objects.read_flat(tree).items()}


def reasons(result: Any) -> dict[str, str]:
    return dict(result.unprotected)


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "ws"
    root.mkdir()
    yield root.resolve()


# --- full snapshots ----------------------------------------------------------------------------


def test_full_snapshot_captures_the_workspace_but_not_the_store_or_git(ws: Path) -> None:
    put(ws, "a.txt", b"alpha")
    put(ws, "src/deep/b.py", b"beta")
    put(ws, ".git/config", b"[core]")
    put(ws, "empty-dir/.keep", b"")
    s = snapper(ws)
    result = s.full(deadline=None)
    assert result.status == "ok" and result.reason is None
    assert contents(s, result.tree) == {
        "a.txt": b"alpha",
        "src/deep/b.py": b"beta",
        "empty-dir/.keep": b"",
    }
    assert result.scope is None and result.unprotected == []
    assert result.files == 3 and result.bytes == 9


def test_the_tree_is_identical_to_gits_own_for_plain_files(ws: Path, tmp_path: Path) -> None:
    put(ws, "README.md", b"# hi\n")
    put(ws, "src/a.py", b"print(1)\r\n")  # CRLF kept byte for byte
    put(ws, "src/sub dir/b.bin", bytes(range(256)))
    s = snapper(ws)
    ours = s.full(deadline=None).tree
    git_dir = tmp_path / "plain.git"
    subprocess.run(["git", "init", "-q", "--bare", str(git_dir)], check=True)
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp_path / "plain.index")}
    git = ["git", "-c", "core.autocrlf=false", "--git-dir", str(git_dir), "--work-tree", str(ws)]
    subprocess.run([*git, "add", "-A", "--", ".", ":!.agentvc"], env=env, check=True)
    theirs = subprocess.run(
        [*git, "write-tree"], env=env, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert ours == theirs


def test_unchanged_files_are_not_read_again_and_changes_are_seen(ws: Path) -> None:
    put(ws, "a.txt", b"one")
    put(ws, "b.txt", b"two")
    io = CountingIO()
    s = snapper(ws, io)
    first = s.full(deadline=None)
    assert sorted(io.opened) == ["a.txt", "b.txt"]
    io.opened.clear()
    assert s.full(deadline=None).tree == first.tree
    assert io.opened == []
    put(ws, "b.txt", b"TWO", old=False)  # same size: only the time tells
    io.opened.clear()
    third = s.full(deadline=None)
    assert io.opened == ["b.txt"]
    assert contents(s, third.tree)["b.txt"] == b"TWO"


def test_a_fresh_snapshotter_reuses_the_saved_index(ws: Path) -> None:
    put(ws, "a.txt", b"one")
    snapper(ws).full(deadline=None)
    io = CountingIO()
    assert snapper(ws, io).full(deadline=None).status == "ok"
    assert io.opened == []


def test_racy_entries_are_read_again(ws: Path) -> None:
    put(ws, "a.txt", b"one", old=False)  # modified "now": within the racy window
    io = CountingIO()
    s = snapper(ws, io)
    s.full(deadline=None)
    io.opened.clear()
    s.full(deadline=None)
    assert io.opened == ["a.txt"]


def test_a_deleted_file_is_absent_from_the_next_snapshot(ws: Path) -> None:
    put(ws, "a.txt")
    put(ws, "b.txt")
    s = snapper(ws)
    s.full(deadline=None)
    (ws / "a.txt").unlink()
    assert set(contents(s, s.full(deadline=None).tree)) == {"b.txt"}


@pytest.mark.skipif(sys.platform == "win32", reason="the executable bit is POSIX-only")
def test_the_executable_bit_is_kept(ws: Path) -> None:
    path = put(ws, "run.sh", b"#!/bin/sh\n")
    path.chmod(0o755)
    s = snapper(ws)
    tree = s.full(deadline=None).tree
    assert tree is not None
    assert s.objects.read_flat(tree)["run.sh"][0] == "100755"


# --- what isn't captured is listed -------------------------------------------------------------


def test_a_nested_repository_is_listed_not_entered(ws: Path) -> None:
    put(ws, "vendor/lib/.git/HEAD", b"ref: refs/heads/main\n")
    put(ws, "vendor/lib/code.py")
    put(ws, "wt/.git", b"gitdir: /elsewhere\n")  # a linked worktree or submodule
    put(ws, "wt/f.txt")
    put(ws, "mine.txt")
    s = snapper(ws)
    result = s.full(deadline=None)
    assert set(contents(s, result.tree)) == {"mine.txt"}
    assert set(reasons(result)) == {"vendor/lib/", "wt/"}


def test_ignored_files_follow_adr_0006(ws: Path) -> None:
    put(ws, ".gitignore", b"*.log\nnode_modules/\n.env\nsecrets/\n")
    put(ws, "small.log", b"s" * 100)
    put(ws, "big.log", b"b" * 3000)
    put(ws, ".env", b"K=" + b"v" * 5000)  # over the limit, but .env is always kept
    put(ws, "node_modules/pkg/index.js", b"heavy")
    put(ws, "build/script.py", b"a tracked folder with a heavy name")  # not ignored
    put(ws, "secrets/key.pem", b"ignored but small")
    s = snapper(ws, ignored_file_max_kb=1)
    result = s.full(deadline=None)
    got = contents(s, result.tree)
    assert {"small.log", ".env", "build/script.py", "secrets/key.pem", ".gitignore"} <= set(got)
    assert "big.log" not in got and "node_modules/pkg/index.js" not in got
    why = reasons(result)
    assert set(why) == {"big.log", "node_modules/"}
    assert "1 KB" in why["big.log"] and "heavy" in why["node_modules/"]


def test_ignored_files_share_one_budget_per_snapshot(ws: Path) -> None:
    put(ws, ".gitignore", b"*.dat\n")
    for name in ("a.dat", "b.dat", "c.dat"):
        put(ws, name, b"d" * 450_000)
    s = snapper(ws, ignored_total_mb=1)
    result = s.full(deadline=None)
    assert set(contents(s, result.tree)) == {".gitignore", "a.dat", "b.dat"}
    assert set(reasons(result)) == {"c.dat"} and "budget" in reasons(result)["c.dat"]


def test_include_rules_from_the_config(ws: Path) -> None:
    put(ws, ".gitignore", b"*.db\n")
    put(ws, "local/app.db", b"q" * 3000)
    put(ws, "data/huge.csv", b"rows")
    s = snapper(
        ws, ignored_file_max_kb=1, always_include=("local/*.db",), never_include=("data/*",)
    )
    result = s.full(deadline=None)
    assert "local/app.db" in contents(s, result.tree)
    assert set(reasons(result)) == {"data/huge.csv"}
    assert "never_include" in reasons(result)["data/huge.csv"]


def test_a_file_over_the_size_limit_is_listed_and_not_read(ws: Path) -> None:
    put(ws, "huge.bin", b"z" * (1024 * 1024 + 1))
    put(ws, "ok.txt")
    io = CountingIO()
    s = snapper(ws, io, max_file_mb=1)
    result = s.full(deadline=None)
    assert set(contents(s, result.tree)) == {"ok.txt"}
    assert "1 MB" in reasons(result)["huge.bin"]
    assert "huge.bin" not in io.opened


def test_an_unreadable_file_is_listed(ws: Path) -> None:
    put(ws, "locked.txt")
    put(ws, "fine.txt")
    io = IO()
    s = snapper(ws, io)
    io.faults.fail("read", PermissionError(errno.EACCES, "Permission denied"), match="locked")
    result = s.full(deadline=None)
    assert result.status == "ok"
    assert set(contents(s, result.tree)) == {"fine.txt"}
    assert "can't read" in reasons(result)["locked.txt"]


def test_more_than_200_unprotected_paths_are_all_remembered(ws: Path) -> None:
    put(ws, ".gitignore", b"*.big\n")
    for i in range(250):
        put(ws, f"f{i:03}.big", b"b" * 2000)
    s = snapper(ws, ignored_file_max_kb=1)
    result = s.full(deadline=None)
    assert len(result.unprotected) == 250
    row = row_values(result)  # what the ledger keeps: 200 listed, the rest in a blob
    assert len(json.loads(row["unprotected"])) == 200 and row["unprotected_count"] == 250
    view = s.view_of(row)
    assert view is not None
    # A path missing from the listed 200 must still read as "not captured", never as absent:
    # otherwise a rollback would take it for a file the agent created, and delete it (G1).
    assert view.state("f249.big") is UNKNOWN
    assert view.state("never-existed.txt") is ABSENT


# --- budgets and failures ----------------------------------------------------------------------


def test_running_out_of_time_is_incomplete_and_the_work_is_kept(ws: Path) -> None:
    for i in range(40):
        put(ws, f"f{i:02}.txt", str(i).encode())
    clock = TickingClock(datetime(2026, 10, 22, tzinfo=UTC))
    io = CountingIO()
    s = snapper(ws, io, clock)
    result = s.full(deadline=Deadline(clock, 0.1))
    assert result.status == "incomplete" and result.tree is None
    assert "time budget" in (result.reason or "")
    done_before = len(io.opened)
    assert 0 < done_before < 40
    io.opened.clear()
    assert s.full(deadline=None).status == "ok"
    assert len(io.opened) == 40 - done_before  # nothing hashed twice


def test_disk_full_fails_the_snapshot_visibly(ws: Path) -> None:
    put(ws, "a.txt")
    io = IO()
    s = snapper(ws, io)
    io.faults.fail("write", OSError(errno.ENOSPC, "No space left on device"), match="tmp_obj")
    result = s.full(deadline=None)
    assert result.status == "failed" and result.tree is None
    assert "No space left" in (result.reason or "")


def test_a_store_with_alternates_does_not_count_as_protected(ws: Path) -> None:
    put(ws, "a.txt")
    s = snapper(ws)
    alternates = s.objects.objects / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text("/elsewhere\n")
    result = s.full(deadline=None)
    assert result.status == "failed" and "alternates" in (result.reason or "")


# --- edited paths only (fast path) -------------------------------------------------------------


def test_paths_snapshot_captures_only_its_targets(ws: Path) -> None:
    put(ws, "edit.py", b"before")
    put(ws, "other.py", b"untouched")
    s = snapper(ws)
    result = s.paths([str(ws / "edit.py"), str(ws / "new.py")], cwd=str(ws))
    assert result.status == "ok" and result.scope == ("edit.py", "new.py")
    assert contents(s, result.tree) == {"edit.py": b"before"}
    view = s.view(result)
    assert view.state("new.py") is ABSENT  # in scope, doesn't exist yet
    assert view.state("other.py") is UNKNOWN  # not in scope


def test_paths_snapshot_resolves_relative_targets_and_refuses_outside_ones(
    ws: Path, tmp_path: Path
) -> None:
    put(ws, "sub/x.txt", b"x")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not ours")
    s = snapper(ws)
    result = s.paths(
        ["x.txt", str(outside), str(ws / ".agentvc" / "ledger.db")], cwd=str(ws / "sub")
    )
    assert result.scope == ("sub/x.txt",)
    assert contents(s, result.tree) == {"sub/x.txt": b"x"}
    assert set(reasons(result)) == {str(outside), str(ws / ".agentvc" / "ledger.db")}


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_paths_snapshot_follows_a_junction_only_to_inside_the_workspace(
    ws: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "t.txt").write_bytes(b"theirs")
    put(ws, "real/t.txt", b"ours")
    for name, target in (("in", ws / "real"), ("out", outside)):
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(ws / name), str(target)],
            check=True,
            capture_output=True,
        )
    s = snapper(ws)
    result = s.paths([str(ws / "in" / "t.txt"), str(ws / "out" / "t.txt")], cwd=str(ws))
    assert result.scope == ("real/t.txt",)
    assert contents(s, result.tree) == {"real/t.txt": b"ours"}
    assert list(reasons(result)) == [str(ws / "out" / "t.txt")]
