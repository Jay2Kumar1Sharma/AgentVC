"""Restore: rollback, undo, redo and repair, never destroying work that isn't the agent's (G1).

Design note restore.md, plan diagram 4.4. Per file, the agent's changes in the range are undone
newest first while the file still holds exactly what the agent left; the first mismatch stops
it (someone else changed the file), and that is reported, never overwritten without `--force`.
Before anything is written, the current state of every file the plan may touch is saved (the
pre-rollback snapshot) and an intent record is written; every write is a compare-and-swap; an
interrupted rollback is finished by `repair`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentvc import crash
from agentvc.clock import Clock
from agentvc.io import IO, is_link
from agentvc.ledger import Ledger, Row
from agentvc.objects import ObjectCorrupt
from agentvc.snapshot import (
    ABSENT,
    LINK_MODE,
    UNKNOWN,
    Snapshotter,
    View,
    changes,
    file_mode,
    row_values,
)
from agentvc.store import STORE_DIR

AFTER_PRE_SNAPSHOT = crash.declare("restore.after_pre_snapshot")
AFTER_INTENT = crash.declare("restore.after_intent")
BEFORE_REPLACE = crash.declare("restore.before_replace")
AFTER_WRITE = crash.declare("restore.after_write")
AFTER_DELETE = crash.declare("restore.after_delete")
BEFORE_DONE = crash.declare("restore.before_done")

WINDOWS = sys.platform == "win32"
CASE_INSENSITIVE = sys.platform in ("win32", "darwin")
LOCK_WAIT_S = 60.0
LEDGER_WAIT_S = 30.0
CHUNK = 1024 * 1024
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}
_NO_CHANGES = frozenset({"read", "network", "agent", "other"})
WRITTEN = frozenset({"restored", "deleted", "partly", "forced"})


class RestoreError(Exception):
    """A rollback that can't start (nothing changed on disk)."""


class _Dir:
    def __repr__(self) -> str:
        return "DIR"


class _Other:
    def __repr__(self) -> str:
        return "OTHER"


DIR = _Dir()  # a directory is at the path now
OTHER = _Other()  # a junction, fifo or device is at the path now
Now = Any  # (mode, sha) | ABSENT | UNKNOWN | DIR | OTHER


def _enc(state: Any) -> Any:
    if state is ABSENT:
        return None
    if state is DIR:
        return "dir"
    if isinstance(state, tuple):
        return list(state)
    raise ValueError(f"can't record state {state!r}")


def _dec(value: Any) -> Any:
    if value is None:
        return ABSENT
    if value == "dir":
        return DIR
    return (str(value[0]), str(value[1]))


@dataclass
class Entry:
    """What happens to one path: its state now (saved first), its target, and the result."""

    path: str
    now: Any
    target: Any
    result: str  # restored | deleted | partly | forced | already | conflict | could-not
    detail: str = ""

    @property
    def writes(self) -> bool:
        return self.result in WRITTEN

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "now": _enc(self.now) if self.now not in (UNKNOWN, OTHER) else "unknown",
            "target": _enc(self.target) if self.target not in (UNKNOWN, OTHER) else "unknown",
            "result": self.result,
            "detail": self.detail,
        }

    @staticmethod
    def from_json(data: dict[str, Any]) -> Entry:
        now = UNKNOWN if data["now"] == "unknown" else _dec(data["now"])
        target = UNKNOWN if data["target"] == "unknown" else _dec(data["target"])
        return Entry(data["path"], now, target, data["result"], data.get("detail", ""))


@dataclass
class Report:
    rollback_id: int | None
    kind: str
    target: str
    status: str  # done | partial | nothing
    files: list[Entry] = field(default_factory=list)
    uncaptured: list[tuple[int, str]] = field(default_factory=list)  # (step seq, why)
    unprotected: list[str] = field(default_factory=list)
    repaired: list[Report] = field(default_factory=list)
    again: str = ""  # for a reversal: the command that would redo what it reversed

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for entry in self.files:
            out[entry.result] += 1
        return dict(out)

    def to_json(self) -> dict[str, Any]:
        return {
            "rollback": self.rollback_id,
            "kind": self.kind,
            "target": self.target,
            "status": self.status,
            "files": [
                {k: e.to_json()[k] for k in ("path", "result", "detail")} for e in self.files
            ],
            "counts": self.counts(),
            "uncaptured_steps": [{"step": s, "why": w} for s, w in self.uncaptured],
            "unprotected": self.unprotected,
            "repaired": [r.to_json() for r in self.repaired],
        }


def _why(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return "in use by another program, or not writable"
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or type(exc).__name__


def _depth(path: str) -> int:
    return path.count("/")


class Restorer:
    def __init__(self, io: IO, clock: Clock, ledger: Ledger, snapshotter: Snapshotter) -> None:
        self.io = io
        self.clock = clock
        self.ledger = ledger
        self.snap = snapshotter
        self.objects = snapshotter.objects
        self.workspace = snapshotter.workspace

    # --- entry points -------------------------------------------------------------------------

    def rollback(self, text: str, *, force: bool = False) -> Report:
        run, steps, desc = self._range(text)
        return self._rollback("rollback", run, steps, desc, force)

    def undo(self, *, force: bool = False) -> Report:
        """Roll back the newest turn that still has changes in place."""
        run = self.run_row("latest")
        undone = {
            r["target"]
            for r in self.ledger.rows(
                "rollbacks",
                "where kind = 'undo' and run_id = ? and inverted_by is null "
                "and status in ('done', 'partial')",
                (run["id"],),
            )
        }
        steps = self.steps_of(run["id"])
        turns = {
            t["id"]: t["seq"] for t in self.ledger.rows("turns", "where run_id = ?", (run["id"],))
        }
        by_turn: dict[int, list[Row]] = defaultdict(list)
        for step in steps:
            by_turn[turns.get(step["turn_id"], 0)].append(step)
        for seq in sorted(by_turn, reverse=True):
            target = f"{run['id']}:t{seq}"
            if target in undone:
                continue
            chains, _, _ = self.chains(run["id"], by_turn[seq])
            if chains:
                later = [s for n in by_turn if n >= seq for s in by_turn[n]]
                return self._rollback("undo", run, later, target, force, label=target)
        raise RestoreError(f"nothing to undo in run {run['id']}")

    def redo(self, *, force: bool = False) -> Report:
        """Reverse the newest undo that hasn't been reversed."""
        return self._invert("redo", ("undo",), force)

    def rollback_undo(self, *, force: bool = False) -> Report:
        """Reverse the newest rollback or undo that hasn't been reversed."""
        return self._invert("rollback-undo", ("rollback", "undo"), force)

    def repair(self) -> list[Report]:
        with self.snap.locked(LOCK_WAIT_S):
            return self._repair()

    # --- what to roll back --------------------------------------------------------------------

    def run_row(self, text: str) -> Row:
        runs = self.ledger.rows("runs")
        if text == "latest":
            visible = [r for r in runs if "check" not in json.loads(r["flags"] or "[]")]
            if not visible:
                raise RestoreError("no runs recorded here")
            return visible[-1]
        found = [r for r in runs if str(r["id"]) == text]
        if not found:
            raise RestoreError(f"no run {text}")
        return found[0]

    def steps_of(self, run_id: int) -> list[Row]:
        return sorted(
            self.ledger.rows("steps", "where run_id = ?", (run_id,)), key=lambda s: s["seq"]
        )

    def _range(self, text: str) -> tuple[Row, list[Row], str]:
        run_text, _, rest = text.partition(":")
        run = self.run_row(run_text)
        steps = self.steps_of(run["id"])
        if not rest:
            return run, steps, str(run["id"])
        try:
            if rest.startswith("t"):
                n = int(rest[1:])
                turns = {
                    t["id"]: t["seq"]
                    for t in self.ledger.rows("turns", "where run_id = ?", (run["id"],))
                }
                if n not in turns.values():
                    raise RestoreError(f"no turn {n} in run {run['id']}")
                return run, [s for s in steps if turns.get(s["turn_id"], 0) >= n], text
            n = int(rest)
        except ValueError as exc:
            raise RestoreError(f"can't read {text!r}: use RUN, RUN:STEP or RUN:tTURN") from exc
        if n not in {s["seq"] for s in steps}:
            raise RestoreError(f"no step {n} in run {run['id']}")
        return run, [s for s in steps if s["seq"] >= n], text

    def chains(
        self, run_id: int, steps: list[Row]
    ) -> tuple[dict[str, list[tuple[int, Any, Any]]], list[tuple[int, str]], list[str]]:
        """Per path, the agent's changes in step order; the steps that can't be undone."""
        rows: dict[int, dict[str, Row]] = defaultdict(dict)
        for row in self.ledger.rows(
            "snapshots", "where run_id = ? and step_id is not null", (run_id,)
        ):
            rows[row["step_id"]][row["phase"]] = row  # newest wins
        chains: dict[str, list[tuple[int, Any, Any]]] = defaultdict(list)
        uncaptured: list[tuple[int, str]] = []
        unprotected: set[str] = set()
        for step in steps:
            if step["status"] == "DENIED":
                continue
            snaps = rows.get(step["id"], {})
            if not snaps:
                if step["category"] in _NO_CHANGES:
                    continue
                why = step["gap_reason"] or "no snapshot was taken"
                uncaptured.append((step["seq"], f"not snapshotted ({why})"))
                continue
            before, after = snaps.get("before"), snaps.get("after")
            older = self.snap.view_of(before) if before else None
            newer = self.snap.view_of(after) if after else None
            for phase_row in (before, after):
                if phase_row is not None:
                    listed = json.loads(phase_row["unprotected"] or "[]")
                    unprotected |= {str(p) for p, _ in listed}
            if older is None or newer is None:
                missing = before if older is None else after
                which = "before" if older is None else "after"
                why = (missing or {}).get("reason") or (
                    "still running" if step["status"] == "PENDING" else "missing"
                )
                uncaptured.append((step["seq"], f"no snapshot {which} it ({why})"))
                continue
            for path in changes(older, newer):
                chains[path].append((step["seq"], older.state(path), newer.state(path)))
        return dict(chains), uncaptured, sorted(unprotected)

    # --- planning -----------------------------------------------------------------------------

    def _now(self, view: View, path: str) -> Any:
        state = view.state(path)
        if state is UNKNOWN:
            try:
                st = self.io.lstat(self.workspace / path)
            except OSError:
                return UNKNOWN
            if stat.S_ISDIR(st.st_mode) and not is_link(st):
                return DIR
        return state

    def _decide(self, path: str, chain: list[tuple[int, Any, Any]], now: Any, force: bool) -> Entry:
        if now is UNKNOWN or now is OTHER:
            return Entry(
                path, now, UNKNOWN, "could-not", "its current state couldn't be saved first"
            )
        target, undone, blocked = (ABSENT if now is DIR else now), 0, None
        for seq, before, after in reversed(chain):
            if target == after:
                target, undone = before, undone + 1
            elif target != before:
                blocked = seq
                break
        current = ABSENT if now is DIR else now
        if blocked is None:
            if target == current:
                return Entry(path, now, target, "already")
            return Entry(path, now, target, "deleted" if target is ABSENT else "restored")
        if force:
            full = chain[0][1]
            if full == current:
                return Entry(path, now, full, "already")
            return Entry(path, now, full, "forced", f"overwrote changes made after step {blocked}")
        if undone == 0:
            return Entry(
                path,
                now,
                now,
                "conflict",
                f"changed by someone else after step {blocked} left it; kept",
            )
        return Entry(
            path,
            now,
            target,
            "partly",
            f"step {blocked}'s change is kept: the file changed outside the agent after it",
        )

    def _settle_dirs(self, entries: dict[str, Entry]) -> None:
        """A folder where a file must go is fine only if this plan deletes all it holds."""
        for entry in sorted(entries.values(), key=lambda e: -_depth(e.path)):
            if entry.now is not DIR or not entry.writes:
                continue
            inside = self._files_under(entry.path)
            ok = inside is not None and all(
                p in entries and entries[p].writes and entries[p].target is ABSENT for p in inside
            )
            if not ok:
                entry.target, entry.result = entry.now, "conflict"
                entry.detail = "a folder holding other files is where it was; kept"

    def _files_under(self, path: str) -> list[str] | None:
        found: list[str] = []
        pending = [path]
        while pending:
            rel = pending.pop()
            try:
                listed = self.io.scandir(self.workspace / rel)
            except OSError:
                return None
            for e in listed:
                child = f"{rel}/{e.name}"
                if e.is_dir():
                    pending.append(child)
                else:
                    found.append(child)
        return found

    def _unsafe(self, path: str) -> str | None:
        """Why ``path`` must not be written, or None (FM-RST-04, FM-RST-08)."""
        parts = path.split("/")
        for part in parts:
            if part in ("", ".", "..") or "\\" in part or chr(0) in part:
                return "not a safe path inside the workspace"
            if WINDOWS and (
                ":" in part or part != part.rstrip(" .") or part.split(".")[0].upper() in _RESERVED
            ):
                return "not a valid Windows file name"
        if parts[0] in (STORE_DIR, ".git"):
            return "inside .agentvc or .git"
        where = self.workspace
        for part in parts[:-1]:
            where = where / part
            try:
                st = self.io.lstat(where)
            except (FileNotFoundError, NotADirectoryError):
                break
            if is_link(st):
                return "the path goes through a link"
        return None

    def _plan(
        self, chains: dict[str, list[tuple[int, Any, Any]]], view: View, force: bool
    ) -> list[Entry]:
        entries = {}
        for path, chain in chains.items():
            reason = self._unsafe(path)
            if reason:  # never read, saved or written (it was left out of the pre-snapshot)
                entries[path] = Entry(path, UNKNOWN, UNKNOWN, "could-not", reason)
            else:
                entries[path] = self._decide(path, chain, self._now(view, path), force)
        self._settle_dirs(entries)
        if CASE_INSENSITIVE:
            groups: dict[str, list[Entry]] = defaultdict(list)
            for entry in entries.values():
                if entry.writes and entry.target is not ABSENT:
                    groups[entry.path.casefold()].append(entry)
            for group in groups.values():
                if len(group) > 1:
                    for entry in group:
                        entry.target, entry.result = entry.now, "conflict"
                        entry.detail = "another path differs from it only in letter case"
        return sorted(entries.values(), key=lambda e: e.path)

    # --- running a rollback -------------------------------------------------------------------

    def _rollback(
        self,
        kind: str,
        run: Row,
        steps: list[Row],
        desc: str,
        force: bool,
        label: str | None = None,
    ) -> Report:
        if not force and any(s["status"] == "PENDING" for s in steps) and run["status"] == "active":
            raise RestoreError(
                f"run {run['id']} is running a tool right now; wait for it to finish "
                "(or use --force)"
            )
        with self.snap.locked(LOCK_WAIT_S):
            repaired = self._repair()
            chains, uncaptured, unprotected = self.chains(run["id"], steps)

            def plan(view: View) -> list[Entry]:
                return self._plan(chains, view, force)

            safe = {p for p in chains if self._unsafe(p) is None}
            report = self._run_op(kind, label or desc, run["id"], None, safe, plan, force)
        report.uncaptured, report.unprotected, report.repaired = uncaptured, unprotected, repaired
        return report

    def _invert(self, kind: str, kinds: tuple[str, ...], force: bool) -> Report:
        with self.snap.locked(LOCK_WAIT_S):
            repaired = self._repair()
            marks = ", ".join(f"'{k}'" for k in kinds)
            found = self.ledger.rows(
                "rollbacks",
                f"where kind in ({marks}) and inverted_by is null "
                "and status in ('done', 'partial')",
            )
            if not found:
                what = "undo" if kinds == ("undo",) else "rollback or undo"
                raise RestoreError(f"no {what} to reverse")
            original = found[-1]
            written = [
                e
                for e in (Entry.from_json(d) for d in json.loads(original["result"] or "[]"))
                if e.writes
            ]

            def plan(view: View) -> list[Entry]:
                out = []
                for old in written:
                    back = ABSENT if old.now is DIR else old.now
                    reason = self._unsafe(old.path)
                    if reason:
                        out.append(Entry(old.path, UNKNOWN, UNKNOWN, "could-not", reason))
                        continue
                    now = self._now(view, old.path)
                    current = ABSENT if now is DIR else now
                    if now is UNKNOWN or now is OTHER:
                        out.append(
                            Entry(
                                old.path,
                                now,
                                UNKNOWN,
                                "could-not",
                                "its current state couldn't be saved first",
                            )
                        )
                    elif current == back:
                        out.append(Entry(old.path, now, back, "already"))
                    elif current == old.target or force:
                        result = "restored" if back is not ABSENT else "deleted"
                        if current != old.target:
                            result = "forced"
                        out.append(Entry(old.path, now, back, result))
                    else:
                        out.append(
                            Entry(
                                old.path,
                                now,
                                now,
                                "conflict",
                                f"changed since rollback {original['id']} wrote it; kept",
                            )
                        )
                entries = {e.path: e for e in out}
                for entry in entries.values():
                    reason = self._unsafe(entry.path) if entry.writes else None
                    if reason:
                        entry.target, entry.result, entry.detail = UNKNOWN, "could-not", reason
                self._settle_dirs(entries)
                return sorted(entries.values(), key=lambda e: e.path)

            report = self._run_op(
                kind,
                f"rollback {original['id']}",
                original["run_id"],
                original["id"],
                {e.path for e in written if self._unsafe(e.path) is None},
                plan,
                force,
            )
        report.repaired = repaired
        report.again = (
            "agentvc undo"
            if original["kind"] == "undo"
            else f"agentvc rollback {original['target']}"
        )
        return report

    def _run_op(
        self,
        kind: str,
        target: str,
        run_id: int | None,
        inverts: int | None,
        paths: set[str],
        plan: Callable[[View], list[Entry]],
        force: bool,
    ) -> Report:
        """Save, record the intent, write, record the result. Holds the snapshot lock."""
        pre = self.snap.keys(sorted(paths))
        if pre.status != "ok":
            raise RestoreError(
                f"couldn't save the current state first ({pre.reason}); nothing changed"
            )
        entries = plan(self.snap.view(pre))
        crash.hit(AFTER_PRE_SNAPSHOT)
        if not any(e.writes for e in entries):
            return Report(None, kind, target, "nothing", entries)
        with self.ledger.transaction(timeout=LEDGER_WAIT_S) as tx:
            op = tx.insert(
                "rollbacks",
                "rollback.start",
                {
                    "kind": kind,
                    "target": target,
                    "run_id": run_id,
                    "inverts": inverts,
                    "force": int(force),
                    "status": "started",
                    "plan": json.dumps([e.to_json() for e in entries]),
                    "started_at": self.clock.now().isoformat(),
                },
            )
            tx.insert(
                "snapshots",
                "snapshot.pre-rollback",
                {
                    "run_id": run_id,
                    "rollback_id": op,
                    "phase": "pre-rollback",
                    "taken_at": self.clock.now().isoformat(),
                    **row_values(pre),
                },
            )
        crash.hit(AFTER_INTENT)
        self._apply(op, entries)
        crash.hit(BEFORE_DONE)
        return self._finish(op, kind, target, inverts, entries, recovered=False)

    def _finish(
        self,
        op: int,
        kind: str,
        target: str,
        inverts: int | None,
        entries: list[Entry],
        *,
        recovered: bool,
    ) -> Report:
        failed = any(e.result in ("could-not",) for e in entries)
        status = "partial" if failed or any(e.result == "conflict" for e in entries) else "done"
        with self.ledger.transaction(timeout=LEDGER_WAIT_S) as tx:
            changes_: dict[str, Any] = {
                "status": status,
                "result": json.dumps([e.to_json() for e in entries]),
                "ended_at": self.clock.now().isoformat(),
            }
            if recovered:
                changes_["flags"] = json.dumps(["recovered"])
            tx.update("rollbacks", op, f"rollback.{status}", changes_)
            if inverts is not None and any(e.writes for e in entries):
                tx.update("rollbacks", inverts, "rollback.inverted", {"inverted_by": op})
        return Report(op, kind, target, status, entries)

    # --- writing ------------------------------------------------------------------------------

    def _apply(self, op: int, entries: list[Entry]) -> None:
        writes = [e for e in entries if e.writes]
        for entry in sorted(
            (e for e in writes if e.target is ABSENT), key=lambda e: -_depth(e.path)
        ):
            self._delete(entry)
        for entry in sorted(writes, key=lambda e: -_depth(e.path)):
            if entry.now is DIR and entry.writes:
                self._remove_empty_tree(entry)
        for entry in sorted(
            (e for e in writes if e.target is not ABSENT and e.writes), key=lambda e: _depth(e.path)
        ):
            self._write(op, entry)

    def _actual(self, path: str) -> Any:
        """The path's state right now, from its real bytes (never from the index)."""
        where = self.workspace / path
        try:
            st = self.io.lstat(where)
        except (FileNotFoundError, NotADirectoryError):
            return ABSENT
        if is_link(st):
            if not stat.S_ISLNK(st.st_mode):
                return OTHER
            return LINK_MODE, self.objects.hash_blob(os.fsencode(self.io.readlink(where)))
        if stat.S_ISDIR(st.st_mode):
            return DIR
        if not stat.S_ISREG(st.st_mode):
            return OTHER
        with self.io.open_read(where) as handle:
            size = self.io.fstat(handle).st_size
            digest = hashlib.sha1(f"blob {size}\0".encode(), usedforsecurity=False)
            seen = 0
            while chunk := handle.read(CHUNK):
                seen += len(chunk)
                digest.update(chunk)
        if seen != size:
            return OTHER  # changing right now
        return file_mode(st), digest.hexdigest()

    def _expected_now(self, entry: Entry) -> Any:
        return ABSENT if entry.now is DIR else entry.now

    def _check(self, entry: Entry) -> bool:
        """Compare-and-swap: the path still holds exactly what was saved before the rollback."""
        reason = self._unsafe(entry.path)
        if reason:  # checked again now: a folder may have become a link since planning
            entry.result, entry.detail = "could-not", reason
            return False
        try:
            actual = self._actual(entry.path)
        except OSError as exc:
            entry.result, entry.detail = "could-not", f"can't read it now ({_why(exc)})"
            return False
        if actual != self._expected_now(entry):
            entry.result = "conflict"
            entry.detail = "changed while the rollback ran; kept"
            return False
        return True

    def _delete(self, entry: Entry) -> None:
        if not self._check(entry):
            return
        try:
            self.io.remove(self.workspace / entry.path)
        except OSError as exc:
            entry.result, entry.detail = "could-not", _why(exc)
            return
        crash.hit(AFTER_DELETE)

    def _remove_empty_tree(self, entry: Entry) -> None:
        """Remove the (now empty) folder standing where a file goes back."""
        folders = [entry.path]
        pending = [entry.path]
        while pending:
            rel = pending.pop()
            try:
                listed = self.io.scandir(self.workspace / rel)
            except OSError:
                break
            for e in listed:
                if e.is_dir():
                    folders.append(f"{rel}/{e.name}")
                    pending.append(f"{rel}/{e.name}")
        for rel in sorted(folders, key=lambda p: -_depth(p)):
            try:
                self.io.remove_dir(self.workspace / rel)
            except OSError as exc:
                entry.result, entry.detail = (
                    "could-not",
                    f"the folder in the way isn't empty ({_why(exc)})",
                )
                return

    def _write(self, op: int, entry: Entry) -> None:
        where = self.workspace / entry.path
        reason = self._unsafe(entry.path)
        if reason:
            entry.result, entry.detail = "could-not", reason
            return
        temp = where.with_name(f".{where.name}.agentvc-{op}.tmp")
        mode, sha = entry.target
        try:
            data = self.objects.read_blob(sha)
        except ObjectCorrupt as exc:
            entry.result, entry.detail = "could-not", f"its saved content is damaged ({exc})"
            return
        try:
            parent = where.parent
            if parent != self.workspace:
                try:
                    st = self.io.lstat(parent)
                    if not stat.S_ISDIR(st.st_mode) or is_link(st):
                        entry.result, entry.detail = (
                            "could-not",
                            "a file is where its folder should be",
                        )
                        return
                except (FileNotFoundError, NotADirectoryError):
                    self.io.mkdir(parent)
            self.io.remove(temp)
            if mode == LINK_MODE:
                self.io.symlink(os.fsdecode(data), temp)
            else:
                self.io.write_file(temp, data)
                if mode == "100755" and not WINDOWS:
                    self.io.chmod(temp, 0o755)
            crash.hit(BEFORE_REPLACE)
            if not self._check(entry):
                self.io.remove(temp)
                return
            self.io.replace(temp, where)
        except OSError as exc:
            entry.result, entry.detail = "could-not", _why(exc)
            self._remove_temp(temp)
            return
        crash.hit(AFTER_WRITE)

    # --- repair -------------------------------------------------------------------------------

    def _repair(self) -> list[Report]:
        """Finish every interrupted rollback (status still `started`). Holds the lock."""
        reports = []
        for row in self.ledger.rows("rollbacks", "where status = 'started'"):
            entries = [Entry.from_json(d) for d in json.loads(row["plan"])]
            for entry in entries:
                where = self.workspace / entry.path
                self._remove_temp(where.with_name(f".{where.name}.agentvc-{row['id']}.tmp"))
            pending = []
            for entry in entries:
                if not entry.writes:
                    continue
                try:
                    actual = self._actual(entry.path)
                except OSError as exc:
                    entry.result, entry.detail = "could-not", f"can't read it now ({_why(exc)})"
                    continue
                if actual == entry.target:
                    continue  # done before the interruption
                pending.append(entry)
            self._apply(row["id"], pending)
            reports.append(
                self._finish(
                    row["id"], row["kind"], row["target"], row["inverts"], entries, recovered=True
                )
            )
        return reports

    def _remove_temp(self, temp: Path) -> None:
        with contextlib.suppress(OSError):
            self.io.remove(temp)
