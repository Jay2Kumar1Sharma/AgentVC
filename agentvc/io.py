"""The one I/O layer (plan §16.4): files, locks, SQLite and processes.

Engine code reaches the outside world only through `IO`, so tests can inject faults
(`io.faults.fail("write", OSError(ENOSPC), match="ledger")`) and crash at named points. Every
operation asks the fault injector first.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO as BinaryStream

from agentvc import crash

AFTER_TEMP_WRITE = crash.declare("io.atomic.after_temp_write")
AFTER_FSYNC = crash.declare("io.atomic.after_fsync")
AFTER_REPLACE = crash.declare("io.atomic.after_replace")

OPERATIONS = frozenset(
    {"read", "write", "replace", "mkdir", "remove", "append", "lock", "sqlite", "run", "spawn"}
)
DEFAULT_MAX_OUTPUT = 1024 * 1024


class LockTimeout(TimeoutError):
    pass


@dataclass
class _Fault:
    op: str
    error: BaseException
    match: str | None
    times: int


class FaultInjector:
    """Rules that make chosen I/O operations fail, for fault-injection tests."""

    def __init__(self) -> None:
        self._rules: list[_Fault] = []
        self._lock = threading.Lock()

    def fail(
        self, op: str, error: BaseException, *, match: str | None = None, times: int = 1
    ) -> None:
        if op not in OPERATIONS:
            raise ValueError(f"unknown I/O operation {op!r}; known: {sorted(OPERATIONS)}")
        with self._lock:
            self._rules.append(_Fault(op, error, match, times))

    def clear(self) -> None:
        with self._lock:
            self._rules.clear()

    def check(self, op: str, target: str) -> None:
        with self._lock:
            for rule in self._rules:
                if (
                    rule.op == op
                    and rule.times > 0
                    and (rule.match is None or rule.match in target)
                ):
                    rule.times -= 1
                    raise rule.error


@dataclass(frozen=True)
class RunResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    truncated: bool


class IO:
    def __init__(self, faults: FaultInjector | None = None) -> None:
        self.faults = faults or FaultInjector()

    # --- files ------------------------------------------------------------------------------

    def read_bytes(self, path: Path) -> bytes:
        self.faults.check("read", str(path))
        return path.read_bytes()

    def read_text(self, path: Path) -> str:
        return self.read_bytes(path).decode("utf-8")

    def exists(self, path: Path) -> bool:
        return path.exists()

    def mkdir(self, path: Path) -> None:
        self.faults.check("mkdir", str(path))
        path.mkdir(parents=True, exist_ok=True)

    def size(self, path: Path) -> int:
        """Size in bytes; 0 for a missing file."""
        try:
            return path.stat().st_size
        except FileNotFoundError:
            return 0

    def replace(self, source: Path, target: Path) -> None:
        self.faults.check("replace", str(target))
        os.replace(source, target)

    def remove(self, path: Path) -> None:
        self.faults.check("remove", str(path))
        path.unlink(missing_ok=True)

    def write_atomic(self, path: Path, data: bytes, *, fsync: bool = True) -> None:
        """Replace ``path`` with ``data`` so readers see the old or the new file, never a mix."""
        self.faults.check("write", str(path))
        temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(temp, "wb") as f:
                f.write(data)
                f.flush()
                crash.hit(AFTER_TEMP_WRITE)
                if fsync:
                    os.fsync(f.fileno())
            crash.hit(AFTER_FSYNC)
            self.faults.check("replace", str(path))
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        crash.hit(AFTER_REPLACE)

    def append_line(self, path: Path, line: str, *, lock_timeout: float = 5.0) -> None:
        """Append one line, under a lock, in one write: concurrent writers never interleave."""
        if "\n" in line or "\r" in line:
            raise ValueError("append_line takes a single line")
        self.faults.check("append", str(path))
        lock_file = path.with_name(path.name + ".lock")
        with self.lock(lock_file, timeout=lock_timeout), open(path, "ab") as f:
            f.write(line.encode("utf-8") + b"\n")

    # --- locks ------------------------------------------------------------------------------

    @contextmanager
    def lock(self, path: Path, *, timeout: float) -> Iterator[None]:
        """Exclusive cross-process lock on ``path``; raises `LockTimeout`, never waits forever.

        The operating system releases it if the holder dies.
        """
        self.faults.check("lock", str(path))
        handle = open(path, "a+b")  # noqa: SIM115 - closed in the finally below
        try:
            end = time.monotonic() + timeout
            while not _try_lock(handle):
                if time.monotonic() >= end:
                    raise LockTimeout(f"could not lock {path} within {timeout:.1f} s")
                time.sleep(0.005)
            try:
                yield
            finally:
                _unlock(handle)
        finally:
            handle.close()

    # --- SQLite -----------------------------------------------------------------------------

    def connect_sqlite(
        self, path: Path, *, timeout: float, check_same_thread: bool = True
    ) -> sqlite3.Connection:
        self.faults.check("sqlite", str(path))
        return sqlite3.connect(
            path, timeout=timeout, isolation_level=None, check_same_thread=check_same_thread
        )

    # --- processes --------------------------------------------------------------------------

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        max_output: int = DEFAULT_MAX_OUTPUT,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        input: bytes | None = None,
    ) -> RunResult:
        """Run a command with a wall-clock timeout and capped output.

        On timeout, the command and every process it started are killed. Processes it left
        running after it exited are killed too, so a run never leaves orphans behind.
        """
        self.faults.check("run", " ".join(argv))
        popen_kwargs: dict[str, object] = {}
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            **popen_kwargs,  # type: ignore[call-overload]
        )
        tree = _ProcessTree(proc)
        assert proc.stdout is not None and proc.stderr is not None
        out = _CappedReader(proc.stdout, max_output)
        err = _CappedReader(proc.stderr, max_output)
        timed_out = False
        end = time.monotonic() + timeout
        try:
            if input is not None:
                assert proc.stdin is not None
                try:
                    proc.stdin.write(input)
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                proc.wait(timeout=max(0.0, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
            # Leftover processes (or a timed-out tree) are killed so the pipes close.
            tree.kill()
            proc.wait()
            out.join(max(1.0, end - time.monotonic()))
            err.join(1.0)
        finally:
            tree.close()
        return RunResult(
            returncode=None if timed_out else proc.returncode,
            stdout=out.data(),
            stderr=err.data(),
            timed_out=timed_out,
            truncated=out.truncated or err.truncated,
        )

    def spawn_detached(
        self, argv: Sequence[str], *, cwd: Path, env: dict[str, str] | None, log: Path
    ) -> int:
        """Start a background process that outlives the caller (for `agentvcd`); return its pid."""
        self.faults.check("spawn", " ".join(argv))
        with open(log, "ab") as log_file:
            kwargs: dict[str, object] = {
                "stdin": subprocess.DEVNULL,
                "stdout": log_file,
                "stderr": log_file,
                "cwd": cwd,
                "env": env,
                "close_fds": True,
            }
            if sys.platform == "win32":
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                try:
                    # Leave the hook's job object (if any), or the agent may kill the daemon
                    # along with the hook.
                    proc = subprocess.Popen(
                        list(argv),
                        creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB,
                        **kwargs,  # type: ignore[call-overload]
                    )
                except PermissionError:
                    proc = subprocess.Popen(list(argv), creationflags=flags, **kwargs)  # type: ignore[call-overload]
            else:
                proc = subprocess.Popen(list(argv), start_new_session=True, **kwargs)  # type: ignore[call-overload]
        return int(proc.pid)


class _CappedReader:
    def __init__(self, stream: BinaryStream[bytes], limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._chunks: list[bytes] = []
        self._size = 0
        self.truncated = False
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while chunk := self._stream.read1(65536):  # type: ignore[attr-defined]
                room = self._limit - self._size
                if room > 0:
                    self._chunks.append(chunk[:room])
                    self._size += min(room, len(chunk))
                if len(chunk) > room:
                    self.truncated = True
        except (OSError, ValueError):
            pass
        finally:
            self._stream.close()

    def join(self, timeout: float) -> None:
        self._thread.join(timeout)

    def data(self) -> bytes:
        return b"".join(self._chunks)


class _ProcessTree:
    """The process and everything it starts: a job object on Windows, a session elsewhere."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc
        self._job = None
        if sys.platform == "win32":
            from agentvc._winjob import Job

            job = Job()
            try:
                job.assign(int(proc._handle))  # type: ignore[attr-defined]
            except OSError:
                job.close()
            else:
                self._job = job

    def kill(self) -> None:
        if sys.platform == "win32":
            if self._job is not None:
                self._job.terminate()
            elif self._proc.poll() is None:
                self._proc.kill()
        else:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(self._proc.pid, signal.SIGKILL)

    def close(self) -> None:
        if self._job is not None:
            self._job.close()


if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle: BinaryStream[bytes]) -> bool:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(handle: BinaryStream[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: BinaryStream[bytes]) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(handle: BinaryStream[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
