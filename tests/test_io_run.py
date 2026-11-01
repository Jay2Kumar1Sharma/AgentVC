"""agentvc/io.py bounded subprocess helper: timeouts, output caps, whole-tree cleanup (FM-IO-03)."""

import errno
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentvc.io import IO


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


def test_run_returns_output_and_exit_code() -> None:
    result = IO().run(
        py("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"), timeout=30
    )
    assert result.returncode == 3
    assert result.stdout.strip() == b"out"
    assert result.stderr.strip() == b"err"
    assert not result.timed_out
    assert not result.truncated


def test_run_passes_input_and_cwd(tmp_path: Path) -> None:
    result = IO().run(
        py("import os, sys; print(os.getcwd()); print(sys.stdin.read())"),
        timeout=30,
        cwd=tmp_path,
        input=b"payload",
    )
    lines = result.stdout.decode().splitlines()
    assert Path(lines[0]).resolve() == tmp_path.resolve()
    assert lines[1] == "payload"


def test_timeout_kills_the_child_and_its_grandchild(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
        "time.sleep(120)\n"
    )
    start = time.monotonic()
    result = IO().run(py(code), timeout=3)
    assert result.timed_out
    assert time.monotonic() - start < 30
    grandchild = int(pid_file.read_text())
    for _ in range(50):
        if not pid_alive(grandchild):
            break
        time.sleep(0.1)
    assert not pid_alive(grandchild)


def test_a_grandchild_holding_the_pipes_cannot_hang_a_finished_run(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
        "print('parent done', flush=True)\n"
    )
    start = time.monotonic()
    result = IO().run(py(code), timeout=5)
    assert time.monotonic() - start < 30
    assert b"parent done" in result.stdout
    grandchild = int(pid_file.read_text())
    for _ in range(50):
        if not pid_alive(grandchild):
            break
        time.sleep(0.1)
    assert not pid_alive(grandchild)


def test_output_is_capped() -> None:
    result = IO().run(
        py("import sys; sys.stdout.write('x' * 500_000)"), timeout=30, max_output=1000
    )
    assert len(result.stdout) == 1000
    assert result.truncated
    assert result.returncode == 0


def test_run_fault_injection() -> None:
    io = IO()
    io.faults.fail("run", OSError(errno.ENOENT, "git not found"), match="git")
    with pytest.raises(OSError):
        io.run(["git", "--version"], timeout=10)
