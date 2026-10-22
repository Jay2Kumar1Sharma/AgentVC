"""`agentvc diff`: what a step, a turn or a run changed (design note cli.md, plan §8 v0.1-alpha).

A step's diff is its before snapshot against its after snapshot. A turn's or a run's diff shows
each file from before the agent's first change to after its last. Files changed by someone else
between steps (outside changes) and steps that weren't captured are listed too (G3).
"""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from agentvc.objects import ObjectStore
from agentvc.restore import RestoreError, Restorer
from agentvc.snapshot import ABSENT, LINK_MODE

BINARY_PROBE = 8000


@dataclass
class FileChange:
    path: str
    change: str  # added | deleted | modified | mode
    text: str
    steps: list[int]


@dataclass
class DiffReport:
    target: str
    files: list[FileChange] = field(default_factory=list)
    outside: list[tuple[int, list[str]]] = field(default_factory=list)  # (step, paths)
    uncaptured: list[tuple[int, str]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "files": [
                {"path": f.path, "change": f.change, "steps": f.steps, "diff": f.text}
                for f in self.files
            ],
            "outside_changes": [{"before_step": s, "paths": p} for s, p in self.outside],
            "uncaptured_steps": [{"step": s, "why": w} for s, w in self.uncaptured],
        }


def _data(objects: ObjectStore, state: Any) -> bytes:
    if state is ABSENT:
        return b""
    mode, sha = state
    data = objects.read_blob(sha)
    if mode == LINK_MODE:
        return f"(symbolic link to {os.fsdecode(data)})\n".encode()
    return data


def _text(objects: ObjectStore, path: str, before: Any, after: Any) -> str:
    old, new = _data(objects, before), _data(objects, after)
    if b"\0" in old[:BINARY_PROBE] or b"\0" in new[:BINARY_PROBE]:
        return f"Binary files a/{path} and b/{path} differ\n"
    lines = difflib.unified_diff(
        old.decode("utf-8", "replace").splitlines(keepends=True),
        new.decode("utf-8", "replace").splitlines(keepends=True),
        fromfile="/dev/null" if before is ABSENT else f"a/{path}",
        tofile="/dev/null" if after is ABSENT else f"b/{path}",
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def _change(before: Any, after: Any) -> str:
    if before is ABSENT:
        return "added"
    if after is ABSENT:
        return "deleted"
    return "mode" if before[1] == after[1] else "modified"


def diff(restorer: Restorer, text: str) -> DiffReport:
    """``text``: RUN, RUN:STEP (that step only) or RUN:tTURN (that turn only)."""
    run_text, _, rest = text.partition(":")
    run = restorer.run_row(run_text)
    steps = restorer.steps_of(run["id"])
    if rest.startswith("t"):
        turns = {
            t["id"]: t["seq"]
            for t in restorer.ledger.rows("turns", "where run_id = ?", (run["id"],))
        }
        try:
            n = int(rest[1:])
        except ValueError as exc:
            raise RestoreError(f"can't read {text!r}: use RUN, RUN:STEP or RUN:tTURN") from exc
        if n not in turns.values():
            raise RestoreError(f"no turn {n} in run {run['id']}")
        steps = [s for s in steps if turns.get(s["turn_id"], 0) == n]
    elif rest:
        try:
            n = int(rest)
        except ValueError as exc:
            raise RestoreError(f"can't read {text!r}: use RUN, RUN:STEP or RUN:tTURN") from exc
        steps = [s for s in steps if s["seq"] == n]
        if not steps:
            raise RestoreError(f"no step {n} in run {run['id']}")
    label = text if rest else str(run["id"])
    chains, uncaptured, _ = restorer.chains(run["id"], steps)
    report = DiffReport(label, uncaptured=uncaptured)
    for path in sorted(chains):
        chain = chains[path]
        before, after = chain[0][1], chain[-1][2]
        if before == after:
            continue  # changed and changed back
        report.files.append(
            FileChange(
                path,
                _change(before, after),
                _text(restorer.objects, path, before, after),
                [seq for seq, _, _ in chain],
            )
        )
    ids = {s["id"]: s["seq"] for s in steps}
    for row in restorer.ledger.rows(
        "snapshots", "where run_id = ? and phase = 'before'", (run["id"],)
    ):
        paths = json.loads(row["outside"] or "[]")
        if row["step_id"] in ids and paths:
            report.outside.append((ids[row["step_id"]], paths))
    return report
