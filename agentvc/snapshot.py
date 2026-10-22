"""Snapshotter: workspace content as git trees in AgentVC's own store (design note snapshots.md).

Two kinds: `full()` captures the whole workspace (shell and unknown tools); `paths()` captures
only an edit tool's targets. Anything not captured is listed with a reason, never skipped
silently (G3); links are never followed out of the workspace and a file that changes while it
is read is never saved as if whole (G1). The snapshot index makes unchanged files free to
snapshot again; it is a cache, never trusted by restore.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
import sys
import threading
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO as BinaryStream
from typing import Any

from agentvc import crash
from agentvc.clock import Clock, Deadline
from agentvc.config import SnapshotConfig
from agentvc.io import IO, LockTimeout, is_link
from agentvc.objects import Flat, ObjectCorrupt, ObjectStore, SizeMismatch
from agentvc.store import STORE_DIR, Store

AFTER_BLOBS = crash.declare("snapshot.after_blobs")
AFTER_TREE = crash.declare("snapshot.after_tree")
INDEX_BEFORE_COMMIT = crash.declare("snapshot.index.before_commit")

RACY_NS = 2_000_000_000  # entries this close to the snapshot's start are re-read next time
LISTED = 200  # unprotected paths kept in the ledger row; the full list goes in a blob
LINK_MODE = "120000"
CLI_LOCK_WAIT_S = 60.0
GIT_TIMEOUT_S = 60.0
WINDOWS = sys.platform == "win32"
BASELINE_PENDING = (
    "baseline pending: the first snapshot of this workspace is still being built in the background"
)


class _Sentinel:
    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return self._name


ABSENT = _Sentinel("ABSENT")  # the snapshot shows the path doesn't exist
UNKNOWN = _Sentinel("UNKNOWN")  # the snapshot can't say (not in scope, or not captured)
State = tuple[str, str] | _Sentinel


@dataclass(frozen=True)
class SnapshotResult:
    status: str  # ok | incomplete | failed
    tree: str | None = None
    scope: tuple[str, ...] | None = None  # None: the whole workspace
    unprotected: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)
    reason: str | None = None
    files: int = 0
    bytes: int = 0
    duration_ms: int = 0
    unprotected_blob: str | None = None  # the full list, when longer than LISTED


class View:
    """What one snapshot says about each path."""

    def __init__(
        self, flat: Flat, scope: Sequence[str] | None, unprotected: Sequence[Sequence[str]]
    ) -> None:
        self.flat = flat
        self.scope = frozenset(scope) if scope is not None else None
        paths = [str(item[0]) for item in unprotected]
        self._files = {p for p in paths if not p.endswith("/")}
        self._dirs = {p for p in paths if p.endswith("/")}

    def state(self, path: str) -> State:
        if self.scope is not None and path not in self.scope:
            return UNKNOWN
        if path in self.flat:
            return self.flat[path]
        if path in self._files or path + "/" in self._dirs:
            return UNKNOWN
        parts = path.split("/")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) + "/" in self._dirs:
                return UNKNOWN
        return ABSENT

    def candidates(self) -> set[str]:
        return set(self.flat) | set(self.scope or ())


def row_values(result: SnapshotResult) -> dict[str, Any]:
    """The `snapshots` ledger columns for a result: the list capped, the full one in a blob."""
    return {
        "status": result.status,
        "tree": result.tree,
        "scope": json.dumps(list(result.scope)) if result.scope is not None else None,
        "unprotected": json.dumps(result.unprotected[:LISTED]),
        "unprotected_count": len(result.unprotected),
        "unprotected_blob": result.unprotected_blob,
        "reason": result.reason,
        "files": result.files,
        "bytes": result.bytes,
        "duration_ms": result.duration_ms,
    }


def changes(older: View, newer: View) -> list[str]:
    """Paths both snapshots know about whose content differs."""
    out = []
    for path in sorted(older.candidates() | newer.candidates()):
        a, b = older.state(path), newer.state(path)
        if a is not UNKNOWN and b is not UNKNOWN and a != b:
            out.append(path)
    return out


# --- the index ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: str
    sha: str
    racy: bool


def _mode(st: os.stat_result) -> str:
    if not WINDOWS and st.st_mode & 0o111:
        return "100755"
    return "100644"


def _sig(st: os.stat_result) -> tuple[int, int, int]:
    # ctime can't be set by a tool on POSIX; on Windows it is the creation time (not used).
    return st.st_size, st.st_mtime_ns, 0 if WINDOWS else st.st_ctime_ns


class _Index:
    """The snapshot index (`snapindex.db`): path → stat and blob, plus ignore results."""

    def __init__(self, io: IO, path: Path) -> None:
        self._io = io
        self._path = path
        self.loaded = False
        self.entries: dict[str, _Entry] = {}
        self.ignored: dict[str, bool] = {}
        self.meta: dict[str, str] = {}
        self._dirty: set[str] = set()
        self._dropped: set[str] = set()
        self._ignore_reset = False
        self._ignore_dirty: set[str] = set()
        self._meta_dirty = False

    def load(self) -> None:
        try:
            self._load()
        except Exception:
            # A cache: if it's damaged, start again (costs one full re-read, never data).
            self._io.remove(self._path)
            self._load()
        self.loaded = True

    def _load(self) -> None:
        db = self._io.connect_sqlite(self._path, timeout=10)
        try:
            db.execute(
                "create table if not exists entries (path text primary key, size integer, "
                "mtime_ns integer, ctime_ns integer, mode text, sha text, racy integer)"
            )
            db.execute("create table if not exists ignored (path text primary key, value integer)")
            db.execute("create table if not exists meta (key text primary key, value text)")
            self.entries = {
                row[0]: _Entry(row[1], row[2], row[3], row[4], row[5], bool(row[6]))
                for row in db.execute("select * from entries")
            }
            self.ignored = {p: bool(v) for p, v in db.execute("select * from ignored")}
            self.meta = dict(db.execute("select * from meta").fetchall())
        finally:
            db.close()

    def put(self, path: str, entry: _Entry) -> None:
        self.entries[path] = entry
        self._dirty.add(path)
        self._dropped.discard(path)

    def drop_except(self, keep: set[str]) -> None:
        gone = set(self.entries) - keep
        for path in gone:
            del self.entries[path]
        self._dropped |= gone
        self._dirty -= gone
        for path in set(self.ignored) - keep:
            del self.ignored[path]
            self._ignore_dirty.add(path)

    def reset_ignored(self) -> None:
        self.ignored.clear()
        self._ignore_dirty.clear()
        self._ignore_reset = True

    def set_ignored(self, path: str, value: bool) -> None:
        self.ignored[path] = value
        self._ignore_dirty.add(path)

    def set_meta(self, key: str, value: str) -> None:
        if self.meta.get(key) != value:
            self.meta[key] = value
            self._meta_dirty = True

    def flush(self) -> None:
        if (
            not (self._dirty or self._dropped or self._ignore_dirty or self._ignore_reset)
            and not self._meta_dirty
        ):
            return
        db = self._io.connect_sqlite(self._path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            db.executemany(
                "insert or replace into entries values (?, ?, ?, ?, ?, ?, ?)",
                [
                    (p, e.size, e.mtime_ns, e.ctime_ns, e.mode, e.sha, int(e.racy))
                    for p in self._dirty
                    if (e := self.entries.get(p)) is not None
                ],
            )
            db.executemany("delete from entries where path = ?", [(p,) for p in self._dropped])
            if self._ignore_reset:
                db.execute("delete from ignored")
            for path in self._ignore_dirty:
                if path in self.ignored:
                    db.execute(
                        "insert or replace into ignored values (?, ?)",
                        (path, int(self.ignored[path])),
                    )
                else:
                    db.execute("delete from ignored where path = ?", (path,))
            db.executemany("insert or replace into meta values (?, ?)", list(self.meta.items()))
            crash.hit(INDEX_BEFORE_COMMIT)
            db.execute("COMMIT")
        finally:
            db.close()
        self._dirty.clear()
        self._dropped.clear()
        self._ignore_dirty.clear()
        self._ignore_reset = False
        self._meta_dirty = False


# --- the snapshotter ----------------------------------------------------------------------------


class _OutOfTime(Exception):
    pass


class _Failed(Exception):
    pass


class _Changed(Exception):
    pass


class _ReadError(Exception):
    def __init__(self, exc: OSError) -> None:
        super().__init__(str(exc))
        self.exc = exc


class _Reader:
    """A file being hashed: its read errors are told apart from errors writing the object."""

    def __init__(self, stream: BinaryStream[bytes]) -> None:
        self._stream = stream

    def read(self, n: int) -> bytes:
        try:
            return self._stream.read(n)
        except OSError as exc:
            raise _ReadError(exc) from exc


def _why(exc: BaseException) -> str:
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or type(exc).__name__


def _matches(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def _is_dotenv(name: str) -> bool:
    return name == ".env" or name.startswith(".env.")


class Snapshotter:
    def __init__(
        self, io: IO, clock: Clock, workspace: Path, store: Store, config: SnapshotConfig
    ) -> None:
        self.io = io
        self.clock = clock
        self.workspace = workspace
        self.store = store
        self.config = config
        self.objects = ObjectStore(io, store.dir / "objects")
        self._index = _Index(io, store.dir / "snapindex.db")
        self._lock = threading.RLock()
        self._lock_path = store.dir / "snapshot.lock"
        self._stamp = store.dir / "snapshot.stamp"
        self._known: set[str] = set()  # blobs known to exist in the store
        self._flats: OrderedDict[str, Flat] = OrderedDict()
        self._baseline: threading.Thread | None = None

    # --- locking and set-up -------------------------------------------------------------------

    @contextmanager
    def locked(self, timeout: float) -> Iterator[None]:
        """The store's snapshot lock, shared with restore (and other processes)."""
        if not self._lock.acquire(timeout=max(timeout, 0.001)):
            raise LockTimeout(f"snapshot store busy for {timeout:.1f} s")
        try:
            with self.io.lock(self._lock_path, timeout=timeout):
                if not self._index.loaded:
                    self.objects.ensure()
                    self._index.load()
                yield
        finally:
            self._lock.release()

    def baseline_ready(self) -> bool:
        if not self._index.loaded:
            try:
                with self.locked(1.0):
                    pass
            except (LockTimeout, OSError):
                return False
        return self._index.meta.get("baseline") == "1"

    def baseline_running(self) -> bool:
        return self._baseline is not None and self._baseline.is_alive()

    def start_baseline(self) -> None:
        """Build the first full snapshot in the background (ADR-0003); hooks never wait for it."""
        if self.baseline_running() or self.baseline_ready():
            return
        self._baseline = threading.Thread(
            target=self.full, kwargs={"deadline": None}, name="agentvc-baseline", daemon=True
        )
        self._baseline.start()

    # --- full snapshots -----------------------------------------------------------------------

    def full(self, *, deadline: Deadline | None) -> SnapshotResult:
        """The whole workspace. ``deadline`` set: a hook's call, bounded by its budget."""
        start = self.clock.monotonic()
        if deadline is not None and self.baseline_running():
            return self._finish(start, SnapshotResult("incomplete", reason=BASELINE_PENDING))
        wait = deadline.remaining() if deadline is not None else CLI_LOCK_WAIT_S
        try:
            with self.locked(wait):
                return self._finish(start, self._full(deadline))
        except LockTimeout:
            reason = "another snapshot or a rollback was using the store"
            return self._finish(start, SnapshotResult("incomplete", reason=reason))
        except Exception as exc:
            return self._finish(start, SnapshotResult("failed", reason=_why(exc)))

    def _finish(self, start: float, result: SnapshotResult) -> SnapshotResult:
        blob = None
        if len(result.unprotected) > LISTED:
            try:
                blob = self.objects.write_blob(json.dumps(result.unprotected).encode())
                self.objects.pin(blob)
            except OSError as exc:
                return SnapshotResult(
                    "failed", reason=f"can't store the list of unprotected paths ({_why(exc)})"
                )
        ms = int((self.clock.monotonic() - start) * 1000)
        return SnapshotResult(
            result.status,
            result.tree,
            result.scope,
            result.unprotected,
            result.reason,
            result.files,
            result.bytes,
            ms,
            blob,
        )

    def _check(self, deadline: Deadline | None) -> None:
        if deadline is not None and deadline.expired():
            raise _OutOfTime

    def _full(self, deadline: Deadline | None) -> SnapshotResult:
        problems = self.objects.problems()
        if problems:
            return SnapshotResult("failed", reason=problems[0])
        stamp = self._stamp_ns()
        unprotected: list[tuple[str, str]] = []
        try:
            files, links = self._walk(deadline, unprotected)
            ignored = self._ignored(sorted(files), files)
            flat: Flat = {}
            total_bytes = 0
            ignored_total = 0
            cfg = self.config
            for path in sorted(files):
                st = files[path]
                name = path.rsplit("/", 1)[-1]
                if _matches(path, cfg.never_include):
                    unprotected.append((path, "excluded by config (never_include)"))
                    continue
                if st.st_size > cfg.max_file_mb * 1024 * 1024:
                    unprotected.append((path, f"over {cfg.max_file_mb} MB (max_file_mb)"))
                    continue
                if ignored[path] and not (_is_dotenv(name) or _matches(path, cfg.always_include)):
                    if st.st_size > cfg.ignored_file_max_kb * 1024:
                        unprotected.append(
                            (
                                path,
                                f"ignored file over {cfg.ignored_file_max_kb} KB "
                                "(ignored_file_max_kb)",
                            )
                        )
                        continue
                    if ignored_total + st.st_size > cfg.ignored_total_mb * 1024 * 1024:
                        unprotected.append(
                            (
                                path,
                                f"ignored files over the {cfg.ignored_total_mb} MB budget per "
                                "snapshot (ignored_total_mb)",
                            )
                        )
                        continue
                    ignored_total += st.st_size
                self._check(deadline)
                entry = self._blob(path, st, stamp, unprotected)
                if entry is not None:
                    flat[path] = entry
                    total_bytes += st.st_size
            for path in sorted(links):
                entry = self._link(path, unprotected)
                if entry is not None:
                    flat[path] = entry
            crash.hit(AFTER_BLOBS)
            tree = self.objects.write_flat(flat)
            crash.hit(AFTER_TREE)
            self.objects.pin(tree)
            self._remember(tree, flat)
            self._index.drop_except(set(files))
            self._index.set_meta("baseline", "1")
            return SnapshotResult(
                "ok",
                tree,
                None,
                sorted(unprotected),
                files=len(flat),
                bytes=total_bytes,
            )
        except _OutOfTime:
            reason = f"time budget of {self.config.time_budget_ms} ms used up"
            if self._index.meta.get("baseline") != "1":
                reason += "; " + BASELINE_PENDING
            return SnapshotResult("incomplete", reason=reason)
        finally:
            self._index.flush()

    def _stamp_ns(self) -> int:
        """The file system's own "now" (racy entries are judged against it, as git does)."""
        self.io.write_file(self._stamp, b"", fsync=False)
        return self.io.lstat(self._stamp).st_mtime_ns

    def _walk(
        self, deadline: Deadline | None, unprotected: list[tuple[str, str]]
    ) -> tuple[dict[str, os.stat_result], dict[str, os.stat_result]]:
        files: dict[str, os.stat_result] = {}
        links: dict[str, os.stat_result] = {}
        heavy_names = set(self.config.heavy_folders)
        root_dev = None if WINDOWS else self.io.lstat(self.workspace).st_dev
        pending: list[str] = [""]
        heavy: list[str] = []
        while pending or heavy:
            if not pending:
                verdict = self._check_ignore(heavy)
                for folder in heavy:
                    if folder in verdict:
                        unprotected.append((folder + "/", "ignored heavy folder (not captured)"))
                    else:
                        pending.append(folder)
                heavy = []
                continue
            rel = pending.pop()
            self._check(deadline)
            where = self.workspace / rel if rel else self.workspace
            try:
                entries = self.io.scandir(where)
            except OSError as exc:
                unprotected.append((rel + "/", f"can't list it ({_why(exc)})"))
                continue
            if rel and any(e.name == ".git" for e in entries):
                unprotected.append(
                    (rel + "/", "nested repository, submodule or worktree (not captured yet)")
                )
                continue
            for e in entries:
                if not rel and e.name in (STORE_DIR, ".git"):
                    continue
                path = f"{rel}/{e.name}" if rel else e.name
                st = e.stat
                if is_link(st):
                    if stat.S_ISLNK(st.st_mode):
                        links[path] = st
                    else:
                        unprotected.append((path + "/", "junction or mount point (not followed)"))
                elif stat.S_ISDIR(st.st_mode):
                    if root_dev is not None and st.st_dev != root_dev:
                        unprotected.append((path + "/", "another file system (not followed)"))
                    elif e.name in heavy_names:
                        heavy.append(path)
                    else:
                        pending.append(path)
                elif stat.S_ISREG(st.st_mode):
                    files[path] = st
                else:
                    unprotected.append((path, "special file (fifo, socket or device)"))
        return files, links

    def _ignore_signature(self, files: dict[str, os.stat_result]) -> str:
        parts = [
            f"{p}\0{files[p].st_size}\0{files[p].st_mtime_ns}"
            for p in sorted(files)
            if p.rsplit("/", 1)[-1] == ".gitignore"
        ]
        exclude = self.workspace / ".git" / "info" / "exclude"
        try:
            st = self.io.lstat(exclude)
            parts.append(f"exclude\0{st.st_size}\0{st.st_mtime_ns}")
        except OSError:
            pass
        return hashlib.sha256("\n".join(parts).encode("utf-8", "surrogateescape")).hexdigest()

    def _ignored(self, paths: list[str], files: dict[str, os.stat_result]) -> dict[str, bool]:
        signature = self._ignore_signature(files)
        if self._index.meta.get("ignore_signature") != signature:
            self._index.reset_ignored()
            self._index.set_meta("ignore_signature", signature)
        unknown = [p for p in paths if p not in self._index.ignored]
        if unknown:
            verdict = self._check_ignore(unknown)
            for path in unknown:
                self._index.set_ignored(path, path in verdict)
        return {p: self._index.ignored[p] for p in paths}

    def _check_ignore(self, paths: list[str]) -> set[str]:
        """Which paths git's ignore rules exclude (nested .gitignore files, negations...)."""
        if not paths:
            return set()
        args = []
        exclude = self.workspace / ".git" / "info" / "exclude"
        if self.io.exists(exclude):
            args += ["-c", f"core.excludesFile={exclude.as_posix()}"]
        args += ["check-ignore", "--no-index", "-z", "--stdin"]
        data = b"\0".join(os.fsencode(p) for p in paths) + b"\0"
        done = self.objects.git(args, timeout=GIT_TIMEOUT_S, input=data, work_tree=self.workspace)
        if done.timed_out or done.returncode not in (0, 1):
            detail = done.stderr.decode("utf-8", "replace").strip()[:200] or "timed out"
            raise _Failed(f"git check-ignore failed: {detail}")
        return {os.fsdecode(p) for p in done.stdout.split(b"\0") if p}

    def _blob(
        self, path: str, st: os.stat_result, stamp: int, unprotected: list[tuple[str, str]]
    ) -> tuple[str, str] | None:
        cached = self._index.entries.get(path)
        if (
            cached is not None
            and not cached.racy
            and (cached.size, cached.mtime_ns, cached.ctime_ns) == _sig(st)
            and cached.mode == _mode(st)
            and self._exists(cached.sha)
        ):
            return cached.mode, cached.sha
        try:
            sha, seen = self._hash_file(self.workspace / path)
        except FileNotFoundError:
            return None  # deleted since the walk: absent
        except _Changed:
            unprotected.append((path, "changed while being read (retried once)"))
            return None
        except _ReadError as exc:
            unprotected.append((path, f"can't read it ({_why(exc.exc)})"))
            return None
        size, mtime_ns, ctime_ns = _sig(seen)
        racy = stamp - mtime_ns < RACY_NS
        self._index.put(path, _Entry(size, mtime_ns, ctime_ns, _mode(seen), sha, racy))
        self._known.add(sha)
        return _mode(seen), sha

    def _hash_file(self, path: Path) -> tuple[str, os.stat_result]:
        for _ in range(2):
            try:
                handle = self.io.open_read(path)
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise _ReadError(exc) from exc
            with handle:
                before = self.io.fstat(handle)
                try:
                    sha = self.objects.write_blob_stream(_Reader(handle), before.st_size)
                except SizeMismatch:
                    continue
                after = self.io.fstat(handle)
            try:
                now = self.io.lstat(path)
            except FileNotFoundError:
                continue
            if _sig(before) == _sig(after) == _sig(now):
                return sha, before
        raise _Changed

    def _link(self, path: str, unprotected: list[tuple[str, str]]) -> tuple[str, str] | None:
        try:
            target = self.io.readlink(self.workspace / path)
        except OSError as exc:
            unprotected.append((path, f"can't read the link ({_why(exc)})"))
            return None
        return LINK_MODE, self.objects.write_blob(os.fsencode(target))

    def _exists(self, sha: str) -> bool:
        if sha in self._known:
            return True
        if self.objects.has(sha):
            self._known.add(sha)
            return True
        return False

    # --- edited paths only --------------------------------------------------------------------

    def resolve(self, targets: Sequence[str], cwd: str) -> tuple[list[str], list[tuple[str, str]]]:
        """Workspace-relative keys for an edit tool's targets, links resolved."""
        keys: list[str] = []
        unprotected: list[tuple[str, str]] = []
        for raw in targets:
            path = Path(raw)
            if not path.is_absolute():
                path = Path(cwd) / path
            real = Path(os.path.realpath(path))
            try:
                rel = real.relative_to(self.workspace)
            except ValueError:
                unprotected.append((raw, "outside the workspace"))
                continue
            if not rel.parts:
                unprotected.append((raw, "the workspace folder itself"))
                continue
            if rel.parts[0] in (STORE_DIR, ".git"):
                unprotected.append((raw, "inside AgentVC's store or the repository's .git"))
                continue
            key = "/".join(rel.parts)
            if key not in keys:
                keys.append(key)
        return keys, unprotected

    def paths(
        self, targets: Sequence[str], *, cwd: str, deadline: Deadline | None = None
    ) -> SnapshotResult:
        """Only ``targets`` (an edit tool's files): the fast path."""
        start = self.clock.monotonic()
        keys, unprotected = self.resolve(targets, cwd)
        wait = deadline.remaining() if deadline is not None else CLI_LOCK_WAIT_S
        try:
            with self.locked(wait):
                return self._finish(start, self._paths(keys, unprotected))
        except LockTimeout:
            reason = "another snapshot or a rollback was using the store"
            return self._finish(start, SnapshotResult("incomplete", reason=reason))
        except Exception as exc:
            return self._finish(start, SnapshotResult("failed", reason=_why(exc)))

    def keys(self, keys: Sequence[str]) -> SnapshotResult:
        """Workspace-relative paths exactly as given (restore's pre-rollback snapshot)."""
        start = self.clock.monotonic()
        try:
            with self.locked(CLI_LOCK_WAIT_S):
                return self._finish(start, self._paths(list(keys), []))
        except LockTimeout:
            reason = "another snapshot or a rollback was using the store"
            return self._finish(start, SnapshotResult("incomplete", reason=reason))
        except Exception as exc:
            return self._finish(start, SnapshotResult("failed", reason=_why(exc)))

    def _paths(self, keys: list[str], unprotected: list[tuple[str, str]]) -> SnapshotResult:
        problems = self.objects.problems()
        if problems:
            return SnapshotResult("failed", reason=problems[0])
        stamp = self._stamp_ns()
        flat: Flat = {}
        total = 0
        try:
            for key in keys:
                try:
                    st = self.io.lstat(self.workspace / key)
                except FileNotFoundError:
                    continue  # absent, and in scope: that's what the snapshot says
                except OSError as exc:
                    unprotected.append((key, f"can't read it ({_why(exc)})"))
                    continue
                entry: tuple[str, str] | None
                if is_link(st):
                    if stat.S_ISLNK(st.st_mode):
                        entry = self._link(key, unprotected)
                    else:
                        unprotected.append((key + "/", "junction or mount point (not followed)"))
                        entry = None
                elif stat.S_ISDIR(st.st_mode):
                    unprotected.append((key + "/", "a directory"))
                    entry = None
                elif not stat.S_ISREG(st.st_mode):
                    unprotected.append((key, "special file (fifo, socket or device)"))
                    entry = None
                elif st.st_size > self.config.max_file_mb * 1024 * 1024:
                    unprotected.append((key, f"over {self.config.max_file_mb} MB (max_file_mb)"))
                    entry = None
                else:
                    entry = self._blob(key, st, stamp, unprotected)
                    total += st.st_size if entry else 0
                if entry is not None:
                    flat[key] = entry
            tree = self.objects.write_flat(flat)
            self.objects.pin(tree)
            self._remember(tree, flat)
        finally:
            self._index.flush()
        return SnapshotResult(
            "ok", tree, tuple(keys), sorted(unprotected), files=len(flat), bytes=total
        )

    # --- reading snapshots back ---------------------------------------------------------------

    def _remember(self, tree: str, flat: Flat) -> None:
        with self._lock:
            self._flats[tree] = flat
            self._flats.move_to_end(tree)
            while len(self._flats) > 4:
                self._flats.popitem(last=False)

    def flat(self, tree: str) -> Flat:
        with self._lock:
            if tree in self._flats:
                self._flats.move_to_end(tree)
                return self._flats[tree]
        flat = self.objects.read_flat(tree)
        self._remember(tree, flat)
        return flat

    def view(self, result: SnapshotResult) -> View:
        assert result.tree is not None, "only a complete snapshot has a view"
        return View(self.flat(result.tree), result.scope, result.unprotected)

    def view_of(self, row: dict[str, Any]) -> View | None:
        """The view of a `snapshots` ledger row; None if that snapshot holds nothing usable."""
        if row.get("status") != "ok" or not row.get("tree"):
            return None
        try:
            if row.get("unprotected_blob"):
                unprotected = json.loads(self.objects.read_blob(str(row["unprotected_blob"])))
            else:
                unprotected = json.loads(str(row.get("unprotected") or "[]"))
            scope = json.loads(str(row["scope"])) if row.get("scope") else None
            return View(self.flat(str(row["tree"])), scope, unprotected)
        except (ObjectCorrupt, ValueError):
            return None
