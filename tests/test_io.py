"""agentvc/io.py: atomic writes, file locks, fault injection, SQLite access (plan §16.4)."""

import errno
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentvc import crash
from agentvc.io import IO, LockTimeout

ATOMIC_POINTS = ("io.atomic.after_temp_write", "io.atomic.after_fsync")


def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_write_atomic_creates_and_replaces(tmp_path: Path) -> None:
    io = IO()
    target = tmp_path / "state.json"
    io.write_atomic(target, b"one")
    io.write_atomic(target, b"two")
    assert target.read_bytes() == b"two"
    assert leftovers(tmp_path) == ["state.json"]


@pytest.mark.parametrize("point", ATOMIC_POINTS)
def test_crash_before_replace_keeps_the_old_file(tmp_path: Path, point: str) -> None:
    io = IO()
    target = tmp_path / "state.json"
    target.write_bytes(b"old")
    with crash.armed(point), pytest.raises(crash.CrashPointHit):
        io.write_atomic(target, b"new")
    assert target.read_bytes() == b"old"
    assert leftovers(tmp_path) == ["state.json"]  # the temp file is cleaned up


def test_crash_after_replace_has_the_new_file(tmp_path: Path) -> None:
    io = IO()
    target = tmp_path / "state.json"
    target.write_bytes(b"old")
    with crash.armed("io.atomic.after_replace"), pytest.raises(crash.CrashPointHit):
        io.write_atomic(target, b"new")
    assert target.read_bytes() == b"new"


@pytest.mark.parametrize("point", ATOMIC_POINTS)
def test_a_killed_process_never_leaves_a_torn_file(tmp_path: Path, point: str) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"old")
    code = (
        "import sys\nfrom pathlib import Path\nfrom agentvc.io import IO\n"
        "IO().write_atomic(Path(sys.argv[1]), b'new' * 1000)\n"
    )
    env = {"AGENTVC_CRASH_AT": point, "SYSTEMROOT": "C:\\Windows"}
    done = subprocess.run([sys.executable, "-c", code, str(target)], env=env, timeout=30)
    assert done.returncode == crash.EXIT_CODE
    assert target.read_bytes() == b"old"
    stray = [n for n in leftovers(tmp_path) if n != "state.json"]
    assert all(n.startswith(".state.json.") and n.endswith(".tmp") for n in stray)


def test_injected_fault_fails_the_write_and_keeps_the_old_file(tmp_path: Path) -> None:
    io = IO()
    target = tmp_path / "ledger-anchor.json"
    target.write_bytes(b"old")
    io.faults.fail("write", OSError(errno.ENOSPC, "No space left on device"), match="anchor")
    with pytest.raises(OSError) as info:
        io.write_atomic(target, b"new")
    assert info.value.errno == errno.ENOSPC
    assert target.read_bytes() == b"old"
    assert leftovers(tmp_path) == ["ledger-anchor.json"]
    io.write_atomic(target, b"new")  # the fault fired once
    assert target.read_bytes() == b"new"


def test_fault_match_limits_which_paths_fail(tmp_path: Path) -> None:
    io = IO()
    io.faults.fail("write", OSError(errno.EIO, "I/O error"), match="ledger", times=5)
    io.write_atomic(tmp_path / "other.txt", b"fine")
    with pytest.raises(OSError):
        io.write_atomic(tmp_path / "ledger.txt", b"x")


def test_read_and_other_operations_can_fail_too(tmp_path: Path) -> None:
    io = IO()
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    io.faults.fail("read", PermissionError(errno.EACCES, "denied"))
    with pytest.raises(PermissionError):
        io.read_bytes(tmp_path / "a.txt")
    assert io.read_text(tmp_path / "a.txt") == "hi"


def test_unknown_fault_operation_is_rejected() -> None:
    with pytest.raises(ValueError):
        IO().faults.fail("wirte", OSError())


def test_append_line_appends_whole_lines(tmp_path: Path) -> None:
    io = IO()
    log = tmp_path / "blindspots.jsonl"
    io.append_line(log, '{"a": 1}')
    io.append_line(log, '{"b": 2}')
    assert log.read_text(encoding="utf-8") == '{"a": 1}\n{"b": 2}\n'
    with pytest.raises(ValueError):
        io.append_line(log, "two\nlines")


def test_lock_excludes_a_second_holder_and_times_out(tmp_path: Path) -> None:
    io = IO()
    lock_file = tmp_path / "ledger.lock"
    with io.lock(lock_file, timeout=1.0):
        start = time.monotonic()
        with pytest.raises(LockTimeout), io.lock(lock_file, timeout=0.2):
            pass
        assert 0.15 < time.monotonic() - start < 2.0
    with io.lock(lock_file, timeout=0.2):  # released
        pass


def test_lock_held_by_a_killed_process_is_released(tmp_path: Path) -> None:
    lock_file = tmp_path / "ledger.lock"
    code = (
        "import sys, time\nfrom pathlib import Path\nfrom agentvc.io import IO\n"
        "with IO().lock(Path(sys.argv[1]), timeout=5):\n"
        "    print('locked', flush=True)\n    time.sleep(60)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(lock_file)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(LockTimeout), IO().lock(lock_file, timeout=0.2):
            pass
        child.kill()
        child.wait(timeout=10)
        with IO().lock(lock_file, timeout=5):
            pass
    finally:
        child.kill()
        child.wait(timeout=10)
        if child.stdout:
            child.stdout.close()


def test_sqlite_connections_go_through_the_layer(tmp_path: Path) -> None:
    io = IO()
    conn = io.connect_sqlite(tmp_path / "x.db", timeout=1.0)
    conn.execute("create table t (x)")
    conn.close()
    io.faults.fail("sqlite", sqlite3.OperationalError("database is locked"))
    with pytest.raises(sqlite3.OperationalError):
        io.connect_sqlite(tmp_path / "x.db", timeout=1.0)


# --- file-system primitives for the snapshotter and restore (session 4) -------------------------


def test_scandir_lists_names_with_lstat_and_never_follows_links(tmp_path: Path) -> None:
    io = IO()
    (tmp_path / "a.txt").write_bytes(b"12345")
    (tmp_path / "sub").mkdir()
    entries = {e.name: e for e in io.scandir(tmp_path)}
    assert sorted(entries) == ["a.txt", "sub"]
    assert entries["a.txt"].stat.st_size == 5
    assert entries["a.txt"].is_file() and not entries["a.txt"].is_dir()
    assert entries["sub"].is_dir() and not entries["sub"].is_link()


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_scandir_reports_a_junction_as_a_link_not_a_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(ws / "j"), str(outside)], check=True, capture_output=True
    )
    [entry] = IO().scandir(ws)
    assert entry.is_link() and not entry.is_dir()


def test_lstat_open_read_readlink_and_plain_writes(tmp_path: Path) -> None:
    io = IO()
    target = tmp_path / "f.bin"
    io.write_file(target, b"data")
    assert io.lstat(target).st_size == 4
    with io.open_read(target) as f:
        assert f.read() == b"data"
    with io.open_new(tmp_path / "new.bin") as f:
        f.write(b"x")
    with pytest.raises(FileExistsError):
        io.open_new(tmp_path / "new.bin")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_bytes(b"")
    with pytest.raises(OSError):
        io.remove_dir(tmp_path / "d")  # not empty
    io.remove(tmp_path / "d" / "f")
    io.remove_dir(tmp_path / "d")
    assert not (tmp_path / "d").exists()


@pytest.mark.parametrize(
    ("op", "call"),
    [
        ("list", lambda io, p: io.scandir(p.parent)),
        ("stat", lambda io, p: io.lstat(p)),
        ("read", lambda io, p: io.open_read(p)),
        ("read", lambda io, p: io.readlink(p)),
        ("write", lambda io, p: io.write_file(p, b"x")),
        ("write", lambda io, p: io.open_new(p.with_name("other"))),
        ("write", lambda io, p: io.chmod(p, 0o644)),
        ("write", lambda io, p: io.symlink("x", p.with_name("link"))),
        ("remove", lambda io, p: io.remove_dir(p.parent / "empty")),
    ],
)
def test_every_new_primitive_honours_fault_injection(tmp_path: Path, op: str, call: object) -> None:
    io = IO()
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    (tmp_path / "empty").mkdir()
    io.faults.fail(op, OSError(errno.EIO, "injected"))
    with pytest.raises(OSError) as info:
        call(io, target)  # type: ignore[operator]
    assert info.value.errno == errno.EIO
    assert target.read_bytes() == b"old"
