# Design note: testability foundations

**Session 3 (v0.1-alpha), plan §16.4, §6.2.** Modules: `agentvc/io.py`, `agentvc/clock.py`, `agentvc/crash.py`, `agentvc/scrub.py`, `agentvc/debuglog.py`, `agentvc/_winjob.py`.

## What and why

Every later safety test (fault injection, crash points, deterministic simulation) needs the code to reach the outside world through a few seams. These are built first so no module is written against `open()`, `subprocess` or `time.time()` directly.

| Piece | What it does |
|---|---|
| `io.IO` | The one I/O layer: read/write/rename/remove/list files, append lines, open SQLite, run and spawn processes. Every operation first asks the **fault injector** whether to fail (`io.faults.fail("write", OSError(ENOSPC), match="ledger")`) |
| atomic write | temp file in the same folder → write → flush → fsync → `os.replace`; the temp file is removed on any error. Crash points between the steps |
| `io.FileLock` | Cross-process exclusive lock on a lock file (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere) with a timeout; raises `LockTimeout`, never waits forever |
| `io.run` | Bounded subprocess: wall-clock timeout, stdout/stderr caps (excess dropped, `truncated=True`), the whole child tree killed on timeout (POSIX: own session + `killpg`; Windows: a **job object** with kill-on-close). Returns a result object; never raises on a non-zero exit |
| `clock` | `Clock` (`now()` UTC, `monotonic()`), `SystemClock`, `FakeClock`; `Deadline` (remaining time, expired?); `RandomSource` (`token_hex`), system and seeded versions |
| `crash` | `crashpoint("ledger.after_commit")` is a no-op unless a test arms that name; armed, it raises `CrashPointHit` (in-process) or exits the process (subprocess chaos tests). Every name is registered at import so a test can enumerate them all |
| `scrub` | Prefix/pattern redaction of known token formats, private keys, `Bearer`, `name=secret` assignments, values under secret-looking keys; optional home/user anonymisation. Moved from `scripts/capture_payloads.py`, which then imports it (one scrubber) |
| `debuglog` | With `AGENTVC_DEBUG=1`, one JSON line per hook to `.agentvc/debug.log`: event, decision, phase timings. No payload content, scrubbed anyway; rotates at 1 MB keeping one old file |

## What it borrows

Atomic-replace and lock patterns are standard practice (POSIX `rename`, Windows `MoveFileEx` via `os.replace`); job objects are the documented Win32 way to kill a process tree. Written from scratch; nothing copied.

## Guarantees touched

- **G1/G2:** atomic writes and locks keep state files and the ledger whole when hooks race or a process dies (FM-LED-01).
- **G6:** one scrubber for everything stored; the debug log never holds payload content.
- **G7:** `io.run` can't hang a hook; locks time out.

## How it can fail (FMEA rows added)

| Id | Failure | Effect | Detection / test |
|---|---|---|---|
| FM-IO-01 | Crash between temp write and rename | Old file intact plus a stray temp file | crash-point test at every step of the atomic write |
| FM-IO-02 | Lock never released (holder killed) | Every later hook waits | OS releases file locks on process exit; lock timeout; test kills a holder |
| FM-IO-03 | Child process outlives its timeout (grandchild keeps pipes open) | Hook hangs; orphan processes | job object / process group kill; test with a grandchild |
| FM-IO-04 | Unbounded child output | Memory blow-up in a hook | output cap test |
