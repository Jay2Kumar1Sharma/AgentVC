"""Attacks on the snapshotter (plan §15.3 "Snapshotter"; docs/adversarial/snapshot.md).

Must hold: never follows links outside the root; anything it can't capture is marked
unprotected, never "saved". ATK-SNAP-05, -06, -10 (nested repositories) and -12 are in
tests/test_snapshot.py.
"""

import contextlib
import errno
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import IO as BinaryStream
from typing import Any

import pytest
from agentsim import Agent

from agentvc.clock import Deadline, FakeClock
from agentvc.config import SnapshotConfig
from agentvc.io import IO
from agentvc.snapshot import Snapshotter
from agentvc.store import Store

OLD = 1_600_000_000


def snapper(ws: Path, io: IO | None = None, **cfg: Any) -> Snapshotter:
    io = io or IO()
    store = Store(ws)
    store.ensure(io)
    return Snapshotter(
        io, FakeClock(datetime(2026, 10, 22, tzinfo=UTC)), ws, store, SnapshotConfig(**cfg)
    )


def captured(s: Snapshotter, tree: str | None) -> dict[str, bytes]:
    assert tree is not None
    return {p: s.objects.read_blob(sha) for p, (_, sha) in s.objects.read_flat(tree).items()}


def reasons(result: Any) -> dict[str, str]:
    return dict(result.unprotected)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root.resolve()


def junction(link: Path, target: Path) -> None:
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
    )


# --- ATK-SNAP-01: files changing during the snapshot -------------------------------------------


class RewritingIO(IO):
    """While a chosen file is being read, it is rewritten in place: same size, new bytes."""

    def __init__(self, name: str, times: int) -> None:
        super().__init__()
        self.name, self.times = name, times

    def open_read(self, path: Path) -> BinaryStream[bytes]:
        handle = super().open_read(path)
        if Path(path).name != self.name or self.times <= 0:
            return handle
        self.times -= 1
        target = Path(path)

        class Racing:
            def __init__(self) -> None:
                self.first = True

            def read(self, n: int = -1) -> bytes:
                data = handle.read(n)
                if self.first:
                    self.first = False
                    with open(target, "r+b") as f:  # the writer, mid-read
                        f.write(b"Z" * len(data))
                    os.utime(target, (time.time() + 5, time.time() + 5))
                return data

            def fileno(self) -> int:
                return handle.fileno()

            def __enter__(self) -> "Racing":
                return self

            def __exit__(self, *exc: object) -> None:
                handle.close()

        return Racing()  # type: ignore[return-value]


def test_a_file_rewritten_while_it_is_read_is_never_saved_as_whole(ws: Path) -> None:
    (ws / "racing.txt").write_bytes(b"A" * 1000)
    (ws / "calm.txt").write_bytes(b"calm")
    s = snapper(ws, RewritingIO("racing.txt", times=2))
    result = s.full(deadline=None)
    assert result.status == "ok"
    assert captured(s, result.tree) == {"calm.txt": b"calm"}
    assert "changed while being read" in reasons(result)["racing.txt"]


def test_a_file_that_settles_after_one_retry_is_captured_as_it_ended(ws: Path) -> None:
    (ws / "racing.txt").write_bytes(b"A" * 1000)
    s = snapper(ws, RewritingIO("racing.txt", times=1))
    result = s.full(deadline=None)
    assert captured(s, result.tree) == {"racing.txt": b"Z" * 1000}
    assert result.unprotected == []


# --- ATK-SNAP-02: links to outside the project, and link loops ---------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_junctions_out_of_the_workspace_and_into_itself_are_not_followed(
    ws: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"not the workspace's")
    (ws / "mine.txt").write_bytes(b"mine")
    junction(ws / "out", outside)
    junction(ws / "loop", ws)  # a loop: would recurse forever if followed
    s = snapper(ws)
    result = s.full(deadline=None)
    assert captured(s, result.tree) == {"mine.txt": b"mine"}
    assert set(reasons(result)) == {"out/", "loop/"}


def test_symlinks_are_stored_as_links_never_followed(ws: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"not the workspace's")
    try:
        os.symlink(outside, ws / "out", target_is_directory=True)
        os.symlink(ws, ws / "loop", target_is_directory=True)
        os.symlink(outside / "secret.txt", ws / "file-link")
    except OSError:
        pytest.skip("creating symlinks needs privileges here")
    s = snapper(ws)
    result = s.full(deadline=None)
    assert result.tree is not None
    flat = s.objects.read_flat(result.tree)
    assert set(flat) == {"out", "loop", "file-link"}
    assert all(mode == "120000" for mode, _ in flat.values())
    assert b"not the workspace's" not in captured(s, result.tree).values()


# --- ATK-SNAP-03: hard links -------------------------------------------------------------------


def test_hard_links_are_captured_as_their_content(ws: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"shared inode")
    os.link(outside, ws / "a.txt")
    os.link(ws / "a.txt", ws / "b.txt")
    s = snapper(ws)
    assert captured(s, s.full(deadline=None).tree) == {
        "a.txt": b"shared inode",
        "b.txt": b"shared inode",
    }


# --- ATK-SNAP-04: FIFOs, sockets, devices ------------------------------------------------------


def _posix_root() -> bool:
    if sys.platform == "win32":
        return False
    return os.geteuid() == 0  # type: ignore[unreachable, unused-ignore]


@pytest.mark.skipif(sys.platform == "win32", reason="FIFOs are POSIX-only")
def test_a_fifo_is_listed_and_never_opened(ws: Path) -> None:
    if sys.platform != "win32":
        os.mkfifo(ws / "pipe")  # opening it to read would block forever
    (ws / "f.txt").write_bytes(b"x")
    s = snapper(ws)
    result = s.full(deadline=None)
    assert captured(s, result.tree) == {"f.txt": b"x"}
    assert "special file" in reasons(result)["pipe"]


@pytest.mark.skipif(sys.platform == "win32" or _posix_root(), reason="POSIX, not root")
def test_a_folder_that_cannot_be_listed_is_reported(ws: Path) -> None:
    (ws / "closed").mkdir()
    (ws / "closed" / "f.txt").write_bytes(b"x")
    (ws / "closed").chmod(0)
    try:
        result = snapper(ws).full(deadline=None)
    finally:
        (ws / "closed").chmod(0o755)
    assert "can't list" in reasons(result)["closed/"]


# --- ATK-SNAP-07: a huge number of tiny files ---------------------------------------------------


@pytest.mark.slow
def test_thousands_of_tiny_files_stop_at_the_budget_and_never_hang(ws: Path) -> None:
    for i in range(3000):
        (ws / f"f{i:04}.txt").write_bytes(b"x")
    io = IO()
    store = Store(ws)
    store.ensure(io)
    from agentvc.clock import SystemClock

    s = Snapshotter(io, SystemClock(), ws, store, SnapshotConfig())
    started = time.perf_counter()
    result = s.full(deadline=Deadline(SystemClock(), 0.05))
    assert time.perf_counter() - started < 2.0
    assert result.status == "incomplete" and "time budget" in (result.reason or "")
    assert s.full(deadline=None).status == "ok"  # and it finishes outside a hook


# --- ATK-SNAP-08 / -09: names with newlines or dots; case collisions ----------------------------


def test_odd_but_legal_names_round_trip(ws: Path) -> None:
    names = ["..x", "a..b", "trailing~", "名前.txt", "sp ace.txt", "semi;colon"]
    if sys.platform != "win32":
        names += ["...", "new\nline", "back\\slash", "tab\there"]  # not legal on Windows
    for name in names:
        (ws / name).write_bytes(name.encode("utf-8", "surrogateescape"))
    s = snapper(ws)
    got = captured(s, s.full(deadline=None).tree)
    assert got == {n: n.encode("utf-8", "surrogateescape") for n in names}


def test_names_differing_only_in_case_are_both_kept_where_the_disk_allows(ws: Path) -> None:
    (ws / "README.md").write_bytes(b"upper")
    with contextlib.suppress(OSError):
        (ws / "readme.md").write_bytes(b"lower")
    distinct = {p.name for p in ws.iterdir() if p.name.lower() == "readme.md"}
    s = snapper(ws)
    got = captured(s, s.full(deadline=None).tree)
    assert set(got) == distinct  # one entry per name the disk really holds
    if len(distinct) == 1:
        assert list(got.values()) == [(ws / next(iter(distinct))).read_bytes()]


# --- ATK-SNAP-10: Git LFS pointers --------------------------------------------------------------


def test_an_lfs_pointer_is_captured_as_the_pointer(ws: Path) -> None:
    pointer = (
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393\n"
        b"size 12345\n"
    )
    (ws / "model.bin").write_bytes(pointer)
    s = snapper(ws)
    assert captured(s, s.full(deadline=None).tree) == {"model.bin": pointer}


# --- ATK-SNAP-11: a tampered .git ---------------------------------------------------------------


def test_a_tampered_git_folder_runs_nothing_and_changes_nothing(ws: Path, tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    marker = tmp_path / "ran.txt"
    # Double-quoted: in git config an unquoted ";" starts a comment.
    command = f"\"sh -c 'echo ran > {marker.as_posix()}; cat'\""
    exclude_all = ws / ".git" / "everything"
    exclude_all.write_text("*\n", encoding="utf-8")
    with open(ws / ".git" / "config", "a", encoding="utf-8") as f:
        f.write(
            f'[filter "evil"]\n\tclean = {command}\n\tsmudge = {command}\n'
            f"[core]\n\tfsmonitor = {command}\n\texcludesFile = {exclude_all.as_posix()}\n"
        )
    (ws / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    (ws / "data.bin").write_bytes(b"d" * 3000)  # over 1 KB: dropped if wrongly taken as ignored
    s = snapper(ws, ignored_file_max_kb=1)
    result = s.full(deadline=None)
    got = captured(s, result.tree)
    assert got["data.bin"] == b"d" * 3000  # captured byte for byte: no filter, not "ignored"
    assert not marker.exists()
    assert result.unprotected == []


# --- ATK-SNAP-13: read-only file system ---------------------------------------------------------


def test_a_read_only_store_fails_the_snapshot_visibly(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"x")
    io = IO()
    agent = Agent(ws, io=io)
    io.faults.fail(
        "write", OSError(errno.EROFS, "Read-only file system"), match="tmp_obj", times=99
    )
    reply = agent.send("0004-PreToolUse-Bash.json", tool_use_id="toolu_rofs")
    assert reply.system_message and "Read-only file system" in reply.system_message
    [row] = agent.ctx.ledger().rows("snapshots")
    assert row["status"] == "failed" and row["tree"] is None
    agent.close()


# --- FM-OBJ-01 (ADR-0005): the user's own git gc can't take snapshots away --------------------


def test_snapshots_survive_the_user_rewriting_and_pruning_their_repository(ws: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=ws,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (ws / "f.txt").write_bytes(b"v1")
    git("add", "f.txt")
    git("commit", "-q", "-m", "v1")
    (ws / "f.txt").write_bytes(b"v2, only ever in a dropped commit")
    git("commit", "-q", "-am", "v2")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "f.txt").write_bytes(b"agent's v3"))
    git("reset", "-q", "--hard", "HEAD~1")
    git("reflog", "expire", "--expire=now", "--all")
    git("gc", "-q", "--prune=now")
    (ws / "f.txt").write_bytes(b"agent's v3")  # back to what the agent left
    report = agent.restorer().rollback(str(agent.run))
    assert [(e.path, e.result) for e in report.files] == [("f.txt", "restored")]
    assert (ws / "f.txt").read_bytes() == b"v2, only ever in a dropped commit"
    agent.close()
