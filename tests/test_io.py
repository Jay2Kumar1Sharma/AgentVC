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
