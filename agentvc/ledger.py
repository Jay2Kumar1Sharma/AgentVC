"""The ledger: what the agent did, tamper-evident (design note ledger.md, ADR-0007, plan §14.1).

`entries` is an append-only journal whose rows are hash-chained; `runs`, `run_sessions`,
`turns` and `steps` are materialised state, each row equal to the body of the newest entry
that wrote it. Every write runs under a cross-process file lock and `BEGIN IMMEDIATE`, then
moves the **anchor**, a small file outside the workspace holding the newest entry's hash.
`verify()` reports tampering, truncation and corruption; nothing is ever "repaired".
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentvc import __version__, crash
from agentvc.clock import Clock
from agentvc.io import IO, LockTimeout

SCHEMA_VERSION = 1
# Forward-only migrations: MIGRATIONS[n] turns a version n-1 ledger into version n.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}

BEFORE_COMMIT = crash.declare("ledger.before_commit")
AFTER_COMMIT = crash.declare("ledger.after_commit")
MIGRATE_BEFORE_COMMIT = crash.declare("ledger.migrate.before_commit")

TABLES = ("runs", "run_sessions", "turns", "steps")
_SCHEMA_V1 = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
CREATE TABLE migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, agentvc_version TEXT NOT NULL);
CREATE TABLE entries (
    seq INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, tbl TEXT, row_id INTEGER,
    body TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX entries_row ON entries (tbl, row_id);
CREATE TABLE runs (
    id INTEGER PRIMARY KEY, agent TEXT NOT NULL, workspace TEXT, start_dir TEXT,
    started_at TEXT, ended_at TEXT, status TEXT NOT NULL, flags TEXT NOT NULL DEFAULT '[]',
    parent_run INTEGER, fork_step INTEGER);
CREATE TABLE run_sessions (
    id INTEGER PRIMARY KEY, agent TEXT NOT NULL, session_id TEXT NOT NULL,
    run_id INTEGER NOT NULL, UNIQUE (agent, session_id));
CREATE TABLE turns (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, seq INTEGER NOT NULL, prompt_id TEXT,
    prompt TEXT, started_at TEXT);
CREATE TABLE steps (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, turn_id INTEGER, seq INTEGER NOT NULL,
    agent_id TEXT, tool TEXT, category TEXT, tool_use_id TEXT, input TEXT,
    status TEXT NOT NULL, outcome TEXT, error TEXT, gap_reason TEXT,
    flags TEXT NOT NULL DEFAULT '[]', cwd TEXT, started_at TEXT, ended_at TEXT,
    duration_ms INTEGER);
CREATE INDEX steps_tool_use ON steps (run_id, tool_use_id);
CREATE INDEX steps_status ON steps (run_id, status);
"""

Row = dict[str, Any]


class LedgerError(Exception):
    pass


class LedgerTooNew(LedgerError):
    pass


class LedgerCorrupt(LedgerError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def entry_hash(
    prev_hash: str, seq: int, at: str, kind: str, tbl: str | None, row_id: int | None, body: str
) -> str:
    material = _canonical([prev_hash, seq, at, kind, tbl, row_id, body])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def workspace_key(workspace: Path) -> str:
    return os.path.normcase(str(workspace.resolve()))


@dataclass(frozen=True)
class Verdict:
    status: str  # ok | anchor-behind | truncated | tampered | corrupt
    problems: tuple[str, ...]
    head_seq: int

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "anchor-behind")


_SEVERITY = ("ok", "anchor-behind", "truncated", "tampered", "corrupt")


class Ledger:
    def __init__(
        self,
        io: IO,
        db: sqlite3.Connection,
        path: Path,
        workspace: Path,
        anchor: Path,
        clock: Clock,
    ) -> None:
        self._io = io
        self._db = db
        self.path = path
        self.workspace = workspace
        self.anchor_path = anchor
        self.lock_path = path.with_name(path.name.replace(".db", "") + ".lock")
        self._clock = clock
        self._thread_lock = threading.Lock()

    # --- opening -----------------------------------------------------------------------------

    @classmethod
    def open(cls, io: IO, path: Path, *, workspace: Path, anchor_dir: Path, clock: Clock) -> Ledger:
        io.mkdir(path.parent)
        io.mkdir(anchor_dir)
        anchor = anchor_dir / (
            hashlib.sha256(workspace_key(workspace).encode()).hexdigest() + ".json"
        )
        try:
            db = io.connect_sqlite(path, timeout=30, check_same_thread=False)
        except sqlite3.DatabaseError as exc:
            raise LedgerCorrupt(f"{path}: can't open it ({exc})") from exc
        ledger = cls(io, db, path, workspace, anchor, clock)
        try:
            with io.lock(ledger.lock_path, timeout=30):
                ledger._prepare()
        except sqlite3.DatabaseError as exc:
            db.close()
            raise LedgerCorrupt(f"{path}: not a readable ledger ({exc})") from exc
        except BaseException:
            db.close()
            raise
        return ledger

    def _prepare(self) -> None:
        db = self._db
        tables = {
            name for (name,) in db.execute("select name from sqlite_master where type='table'")
        }
        if not tables:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.executescript(_SCHEMA_V1.replace(";\n", ";\n"))  # executescript commits first
            db.execute("BEGIN IMMEDIATE")
            db.execute("insert into schema_version (version) values (1)")
            self._record_migration(1)
            db.execute("COMMIT")
            version = 1
        else:
            if "schema_version" not in tables:
                raise LedgerCorrupt(f"{self.path}: no schema_version table")
            rows = db.execute("select version from schema_version").fetchall()
            if len(rows) != 1 or not isinstance(rows[0][0], int):
                raise LedgerCorrupt(f"{self.path}: schema_version must hold exactly one number")
            version = rows[0][0]
            if version > SCHEMA_VERSION:
                raise LedgerTooNew(
                    f"{self.path} was written by a newer AgentVC (ledger schema {version}; this "
                    f"AgentVC {__version__} reads up to {SCHEMA_VERSION}). Upgrade AgentVC; "
                    "nothing was recorded or changed."
                )
            if version < SCHEMA_VERSION:
                self._backup(version)
        db.execute("PRAGMA synchronous=NORMAL")
        for target in range(version + 1, SCHEMA_VERSION + 1):
            db.execute("BEGIN IMMEDIATE")
            try:
                MIGRATIONS[target](db)
                db.execute("update schema_version set version = ?", (target,))
                self._record_migration(target)
                crash.hit(MIGRATE_BEFORE_COMMIT)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def _record_migration(self, version: int) -> None:
        self._db.execute(
            "insert into migrations values (?, ?, ?)",
            (version, self._clock.now().isoformat(), __version__),
        )

    def _backup(self, version: int) -> None:
        backup = self.path.with_name(f"{self.path.name}.bak-v{version}")
        target = self._io.connect_sqlite(backup, timeout=30)
        try:
            self._db.backup(target)
        finally:
            target.close()

    def close(self) -> None:
        self._db.close()

    # --- writing -----------------------------------------------------------------------------

    @contextmanager
    def transaction(self, *, timeout: float) -> Iterator[Transaction]:
        """One atomic change: lock, `BEGIN IMMEDIATE`, entries + rows, commit, move the anchor.

        Raises `LockTimeout` if the ledger can't be locked within ``timeout`` seconds.
        """
        if not self._thread_lock.acquire(timeout=max(timeout, 0.001)):
            raise LockTimeout(f"ledger busy for {timeout:.1f} s")
        try:
            with self._io.lock(self.lock_path, timeout=timeout):
                anchor = self._read_anchor()
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    tx = Transaction(self)
                    problem = self._head_problem(anchor, tx.head_seq, tx.head_hash)
                    if problem:
                        tx.note("ledger.anomaly", {"problem": problem})
                    start = tx.head_seq
                    yield tx
                    if tx.head_seq != start and isinstance(anchor, dict):
                        pending = {"seq": tx.head_seq, "hash": tx.head_hash}
                        self._write_anchor({**anchor, "pending": pending})
                    crash.hit(BEFORE_COMMIT)
                    self._db.execute("COMMIT")
                except BaseException:
                    if self._db.in_transaction:
                        self._db.execute("ROLLBACK")
                    raise
                crash.hit(AFTER_COMMIT)
                if tx.head_seq != start:
                    self._write_anchor({"seq": tx.head_seq, "hash": tx.head_hash, "pending": None})
        finally:
            self._thread_lock.release()

    def _read_anchor(self) -> dict[str, Any] | str | None:
        """The anchor's content, None when there is none, or a string saying why it's unreadable."""
        try:
            data = json.loads(self._io.read_text(self.anchor_path))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            return f"the anchor {self.anchor_path} is unreadable ({exc})"
        if not (isinstance(data, dict) and isinstance(data.get("seq"), int)):
            return f"the anchor {self.anchor_path} is malformed"
        return data

    def _write_anchor(self, content: dict[str, Any]) -> None:
        content = {**content, "workspace": str(self.workspace)}
        self._io.write_atomic(self.anchor_path, _canonical(content).encode(), fsync=False)

    @staticmethod
    def _head_problem(
        anchor: dict[str, Any] | str | None, head_seq: int, head_hash: str
    ) -> str | None:
        if isinstance(anchor, str):
            return anchor
        if anchor is None:
            return None if head_seq == 0 else "the anchor outside the workspace is missing"
        if head_seq == anchor["seq"] and head_hash == anchor.get("hash"):
            return None
        pending = anchor.get("pending")
        if isinstance(pending, dict) and (head_seq, head_hash) == (
            pending.get("seq"),
            pending.get("hash"),
        ):
            return None  # the last write committed but died before moving the anchor
        if head_seq < anchor["seq"]:
            return (
                f"entries are missing: the ledger ends at {head_seq}, the anchor at {anchor['seq']}"
            )
        return (
            f"entries were added or changed outside AgentVC (ledger head {head_seq}, "
            f"anchor {anchor['seq']})"
        )

    # --- reading -----------------------------------------------------------------------------

    def rows(self, tbl: str, where: str = "", params: tuple[Any, ...] = ()) -> list[Row]:
        if tbl not in TABLES:
            raise ValueError(f"unknown table {tbl!r}")
        with self._thread_lock:
            cur = self._db.execute(f"select * from {tbl} {where} order by id", params)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    def row(self, tbl: str, row_id: int) -> Row:
        found = self.rows(tbl, "where id = ?", (row_id,))
        if not found:
            raise KeyError(f"{tbl} {row_id}")
        return found[0]

    def verify(self) -> Verdict:
        with self._thread_lock:
            try:
                return self._verify()
            except sqlite3.DatabaseError as exc:
                return Verdict("corrupt", (f"the ledger database is damaged ({exc})",), 0)

    def _verify(self) -> Verdict:
        problems: list[tuple[str, str]] = []
        check = [r for (r,) in self._db.execute("PRAGMA quick_check")]
        if check != ["ok"]:
            return Verdict("corrupt", tuple(f"database check: {c}" for c in check[:5]), 0)

        prev, expected, head_seq = "", 1, 0
        latest: dict[tuple[str, int], Row] = {}
        hashes: dict[int, str] = {}
        cur = self._db.execute(
            "select seq, at, kind, tbl, row_id, body, prev_hash, hash from entries order by seq"
        )
        for seq, at, kind, tbl, row_id, body, prev_hash, digest in cur:
            if seq != expected:
                problems.append(("tampered", f"entries {expected}..{seq - 1} are missing"))
            if prev_hash != prev:
                problems.append(("tampered", f"entry {seq} doesn't follow the one before it"))
            if entry_hash(prev_hash, seq, at, kind, tbl, row_id, body) != digest:
                problems.append(("tampered", f"entry {seq} was changed after it was written"))
            if kind == "ledger.anomaly":
                problems.append(
                    ("tampered", f"entry {seq} recorded: {json.loads(body)['problem']}")
                )
            if tbl is not None:
                latest[(tbl, row_id)] = json.loads(body)["row"]
            prev, expected, head_seq = digest, seq + 1, seq
            hashes[seq] = digest

        for tbl in TABLES:
            cur = self._db.execute(f"select * from {tbl} order by id")
            names = [d[0] for d in cur.description]
            present = set()
            for values in cur.fetchall():
                row = dict(zip(names, values, strict=True))
                present.add(row["id"])
                if latest.get((tbl, row["id"])) != row:
                    problems.append(("tampered", f"{tbl} row {row['id']} differs from its entries"))
            for key_tbl, key_id in latest:
                if key_tbl == tbl and key_id not in present:
                    problems.append(("tampered", f"{tbl} row {key_id} was deleted"))

        anchor = self._read_anchor()
        if isinstance(anchor, str):
            problems.append(("tampered", anchor))
        elif anchor is None:
            if head_seq:
                problems.append(("tampered", "the anchor outside the workspace is missing"))
        elif head_seq < anchor["seq"]:
            problems.append(
                (
                    "truncated",
                    f"entries {head_seq + 1}..{anchor['seq']} were removed (or lost in a crash)",
                )
            )
        elif hashes.get(anchor["seq"]) != anchor.get("hash"):
            problems.append(("tampered", "history before the anchor was rewritten"))
        elif head_seq > anchor["seq"]:
            pending = anchor.get("pending")
            if (
                isinstance(pending, dict)
                and pending.get("seq") == head_seq
                and pending.get("hash") == hashes[head_seq]
            ):
                problems.append(
                    ("anchor-behind", "the last write was interrupted before the anchor moved")
                )
            else:
                problems.append(
                    (
                        "tampered",
                        f"entries {anchor['seq'] + 1}..{head_seq} were added outside AgentVC",
                    )
                )

        status = max((s for s, _ in problems), key=_SEVERITY.index, default="ok")
        return Verdict(status, tuple(p for _, p in problems), head_seq)


class Transaction:
    """Changes inside one ledger transaction. Every change appends one hash-chained entry."""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger
        self._db = ledger._db
        head = self._db.execute(
            "select seq, hash from entries order by seq desc limit 1"
        ).fetchone()
        self.head_seq: int = head[0] if head else 0
        self.head_hash: str = head[1] if head else ""
        self._columns: dict[str, list[str]] = {}

    def _cols(self, tbl: str) -> list[str]:
        if tbl not in TABLES:
            raise ValueError(f"unknown table {tbl!r}")
        if tbl not in self._columns:
            self._columns[tbl] = [r[1] for r in self._db.execute(f"PRAGMA table_info({tbl})")]
        return self._columns[tbl]

    def _append(self, kind: str, tbl: str | None, row_id: int | None, body: dict[str, Any]) -> None:
        seq = self.head_seq + 1
        at = self._ledger._clock.now().isoformat()
        text = _canonical(body)
        digest = entry_hash(self.head_hash, seq, at, kind, tbl, row_id, text)
        self._db.execute(
            "insert into entries values (?, ?, ?, ?, ?, ?, ?, ?)",
            (seq, at, kind, tbl, row_id, text, self.head_hash, digest),
        )
        self.head_seq, self.head_hash = seq, digest

    def _read(self, tbl: str, row_id: int) -> Row | None:
        cur = self._db.execute(f"select * from {tbl} where id = ?", (row_id,))
        values = cur.fetchone()
        if values is None:
            return None
        return dict(zip([d[0] for d in cur.description], values, strict=True))

    def insert(self, tbl: str, kind: str, values: Row) -> int:
        columns = self._cols(tbl)
        unknown = set(values) - set(columns) | ({"id"} & set(values))
        if unknown:
            raise ValueError(f"{tbl}: can't set {sorted(unknown)}")
        # Ids come from the hash-chained entries, never from SQLite, so a deleted row's id is
        # never handed out again (which would hide the deletion).
        (last,) = self._db.execute(
            "select max(row_id) from entries where tbl = ?", (tbl,)
        ).fetchone()
        row_id = (last or 0) + 1
        names = ["id", *values]
        self._db.execute(
            f"insert into {tbl} ({', '.join(names)}) values ({', '.join('?' * len(names))})",
            (row_id, *values.values()),
        )
        row = self._read(tbl, row_id)
        self._append(kind, tbl, row_id, {"row": row})
        return row_id

    def update(self, tbl: str, row_id: int, kind: str, changes: Row) -> Row:
        columns = self._cols(tbl)
        unknown = set(changes) - set(columns) | ({"id"} & set(changes))
        if unknown:
            raise ValueError(f"{tbl}: can't set {sorted(unknown)}")
        if self._read(tbl, row_id) is None:
            raise KeyError(f"{tbl} {row_id}")
        assignments = ", ".join(f"{name} = ?" for name in changes)
        self._db.execute(
            f"update {tbl} set {assignments} where id = ?", (*changes.values(), row_id)
        )
        row = self._read(tbl, row_id)
        assert row is not None
        self._append(kind, tbl, row_id, {"row": row})
        return row

    def note(self, kind: str, body: dict[str, Any]) -> None:
        """An entry that changes no row (e.g. an anomaly found before this write)."""
        self._append(kind, None, None, body)

    def find(self, tbl: str, where: str, params: tuple[Any, ...]) -> list[Row]:
        self._cols(tbl)
        cur = self._db.execute(f"select * from {tbl} where {where} order by id", params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
