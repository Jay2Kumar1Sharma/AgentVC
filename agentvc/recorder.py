"""Recorder: events → runs, turns, steps and visible gaps (design note capture.md, ADR-0004).

Every rule here exists so that each agent action ends up as a recorded step or a visible GAP
(G2), never silently dropped and never merged into another action.
"""

from __future__ import annotations

import json
import ntpath
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentvc import wiring
from agentvc.clock import Clock
from agentvc.events import Event, Kind
from agentvc.ledger import Ledger, Row, Transaction


@dataclass(frozen=True)
class Outcome:
    action: str
    run_id: int | None = None
    step_id: int | None = None
    gaps: int = 0  # steps turned into gaps by this event
    blindspot: str | None = None  # a reason to log a blind spot: duplicate-event, ...


def _norm(path: str) -> str:
    # Payload paths are Windows or POSIX; compare them the way the agent's OS would.
    if "\\" in path or ntpath.splitdrive(path)[0]:
        return ntpath.normcase(ntpath.normpath(path)).rstrip("\\")
    return os.path.normpath(path).rstrip("/")


def _flags(row: Row) -> list[str]:
    value = json.loads(row.get("flags") or "[]")
    return list(value) if isinstance(value, list) else []


def _with_flag(row: Row, flag: str) -> str:
    return json.dumps(sorted({*_flags(row), flag}))


class Recorder:
    def __init__(self, ledger: Ledger, workspace: Path, clock: Clock) -> None:
        self._ledger = ledger
        self._workspace = _norm(str(workspace))
        self._clock = clock

    def _inside(self, cwd: str) -> bool:
        path = _norm(cwd)
        sep = "\\" if "\\" in self._workspace else "/"
        return path == self._workspace or path.startswith(self._workspace + sep)

    def _now(self) -> str:
        return self._clock.now().isoformat()

    # --- entry points ----------------------------------------------------------------------

    def apply(self, event: Event, *, timeout: float) -> Outcome:
        with self._ledger.transaction(timeout=timeout) as tx:
            run = self._find_run(tx, event)
            if run is None:
                if not self._inside(event.cwd):
                    return Outcome("foreign", blindspot="foreign-project")
                run = self._start_run(tx, event)
            return self._apply(tx, run, event)

    def record_gap(
        self,
        session_id: str,
        reason: str,
        detail: str,
        *,
        event: Event | None = None,
        timeout: float,
        agent: str = "claude-code",
    ) -> Outcome:
        """A GAP for a failure AgentVC itself had (hook-error, hook-timeout, daemon-down)."""
        with self._ledger.transaction(timeout=timeout) as tx:
            found = tx.find("run_sessions", "agent = ? and session_id = ?", (agent, session_id))
            if found:
                run = found[0]["run_id"]
            else:
                stub = event or Event(Kind.SESSION_START, agent, "", session_id, "")
                run = self._start_run(tx, stub, extra_flags=("start-missing",))
            step = self._new_step(
                tx, run, event, status="GAP", gap_reason=reason, error=detail[:1000]
            )
            return Outcome("gap", run_id=run, step_id=step, gaps=1)

    # --- runs and turns --------------------------------------------------------------------

    def _find_run(self, tx: Transaction, event: Event) -> int | None:
        found = tx.find(
            "run_sessions", "agent = ? and session_id = ?", (event.agent, event.session_id)
        )
        return int(found[0]["run_id"]) if found else None

    def _start_run(self, tx: Transaction, event: Event, extra_flags: tuple[str, ...] = ()) -> int:
        flags = set(extra_flags)
        if event.kind is not Kind.SESSION_START:
            flags.add("start-missing")  # its SessionStart was lost
        if event.source == "resume":
            flags.add("resumed")  # a resume whose session id AgentVC hasn't seen (U-17)
        run = tx.insert(
            "runs",
            "run.start",
            {
                "agent": event.agent,
                "workspace": self._workspace,
                "start_dir": event.cwd,
                "started_at": self._now(),
                "status": "active",
                "flags": json.dumps(sorted(flags)),
            },
        )
        tx.insert(
            "run_sessions",
            "run.session",
            {"agent": event.agent, "session_id": event.session_id, "run_id": run},
        )
        return run

    def _turn_for(self, tx: Transaction, run: int, prompt_id: str | None) -> int | None:
        if prompt_id:
            match = tx.find("turns", "run_id = ? and prompt_id = ?", (run, prompt_id))
            if match:
                return int(match[-1]["id"])
        turns = tx.find("turns", "run_id = ?", (run,))
        return int(turns[-1]["id"]) if turns else None

    # --- steps -----------------------------------------------------------------------------

    def _new_step(self, tx: Transaction, run: int, event: Event | None, **values: Any) -> int:
        existing = tx.find("steps", "run_id = ?", (run,))
        flags = set(json.loads(values.pop("flags", "[]")))
        row: dict[str, Any] = {"run_id": run, "seq": len(existing) + 1, "started_at": self._now()}
        if event is not None:
            if event.tool_name is not None:
                category = wiring.tool_category(event.tool_name)
                row.update(tool=event.tool_name, category=category)
                if category == "unknown":
                    flags.add("unknown-tool")
            if event.cwd and not self._inside(event.cwd):
                flags.add("outside-workspace")
            row.update(
                turn_id=self._turn_for(tx, run, event.prompt_id),
                agent_id=event.agent_id,
                tool_use_id=event.tool_use_id,
                input=json.dumps(event.tool_input, ensure_ascii=False)
                if event.tool_input is not None
                else None,
                cwd=event.cwd or None,
            )
        else:
            row["turn_id"] = self._turn_for(tx, run, None)
        row.update(values)
        row["flags"] = json.dumps(sorted(flags))
        return tx.insert("steps", f"step.{str(row['status']).lower()}", row)

    def _close_pending(
        self, tx: Transaction, run: int, *, agent_id: str | None, every: bool
    ) -> int:
        pending = tx.find("steps", "run_id = ? and status = 'PENDING'", (run,))
        closed = 0
        for step in pending:
            if every or step["agent_id"] == agent_id:
                tx.update(
                    "steps",
                    step["id"],
                    "step.gap",
                    {"status": "GAP", "gap_reason": "post-missing", "ended_at": self._now()},
                )
                closed += 1
        return closed

    def _complete(self, tx: Transaction, step: Row, event: Event, *, late: bool) -> Outcome:
        changes: dict[str, Any] = {
            "status": "RECORDED",
            "outcome": event.outcome,
            "error": event.error,
            "ended_at": self._now(),
            "duration_ms": event.duration_ms,
        }
        if late:
            changes.update(flags=_with_flag(step, "post-late"), gap_reason=None)
        tx.update("steps", step["id"], "step.complete", changes)
        return Outcome("step-recorded", run_id=step["run_id"], step_id=step["id"])

    # --- the rules -------------------------------------------------------------------------

    def _apply(self, tx: Transaction, run: int, event: Event) -> Outcome:
        kind = event.kind
        if kind is Kind.SESSION_START:
            # After a (re)start no earlier tool call can still be running.
            gaps = self._close_pending(tx, run, agent_id=None, every=True)
            return Outcome("session-start", run_id=run, gaps=gaps)
        if kind is Kind.PROMPT:
            gaps = self._close_pending(tx, run, agent_id=None, every=False)
            seq = len(tx.find("turns", "run_id = ?", (run,))) + 1
            tx.insert(
                "turns",
                "turn.start",
                {
                    "run_id": run,
                    "seq": seq,
                    "prompt_id": event.prompt_id,
                    "prompt": event.prompt,
                    "started_at": self._now(),
                },
            )
            return Outcome("turn", run_id=run, gaps=gaps)
        if kind is Kind.TOOL_PRE:
            return self._pre(tx, run, event)
        if kind is Kind.TOOL_POST:
            return self._post(tx, run, event)
        if kind is Kind.TOOL_BATCH:
            return self._batch(tx, run, event)
        if kind is Kind.SUBAGENT_STOP:
            gaps = self._close_pending(tx, run, agent_id=event.agent_id, every=False)
            return Outcome("subagent-stop", run_id=run, gaps=gaps)
        if kind is Kind.STOP:
            gaps = self._close_pending(tx, run, agent_id=None, every=False)
            return Outcome("stop", run_id=run, gaps=gaps)
        if kind is Kind.SESSION_END:
            gaps = self._close_pending(tx, run, agent_id=None, every=True)
            tx.update("runs", run, "run.end", {"status": "ended", "ended_at": self._now()})
            return Outcome("session-end", run_id=run, gaps=gaps)
        return Outcome("noted", run_id=run)  # SUBAGENT_START: attribution comes from agent_id

    def _same_call(self, tx: Transaction, run: int, event: Event) -> list[Row]:
        return tx.find("steps", "run_id = ? and tool_use_id = ?", (run, event.tool_use_id))

    def _pre(self, tx: Transaction, run: int, event: Event) -> Outcome:
        earlier = self._same_call(tx, run, event)
        last = earlier[-1] if earlier else None
        if last is not None and last["status"] == "PENDING":
            return Outcome("duplicate", run_id=run, step_id=last["id"], blindspot="duplicate-event")
        if last is not None and last["gap_reason"] == "pre-missing":
            # Its Post already arrived: this Pre is late (a slow or retried hook).
            tx.update(
                "steps",
                last["id"],
                "step.pre-late",
                {"gap_reason": "pre-late", "flags": _with_flag(last, "pre-late")},
            )
            return Outcome("pre-late", run_id=run, step_id=last["id"])
        flags = ["reused-id"] if last is not None else []
        step = self._new_step(tx, run, event, status="PENDING", flags=json.dumps(flags))
        return Outcome("step-opened", run_id=run, step_id=step)

    def _post(self, tx: Transaction, run: int, event: Event) -> Outcome:
        earlier = self._same_call(tx, run, event)
        for step in reversed(earlier):
            if step["status"] == "PENDING":
                return self._complete(tx, step, event, late=False)
            if step["status"] == "GAP" and step["gap_reason"] == "post-missing":
                return self._complete(tx, step, event, late=True)
        if earlier:
            return Outcome("duplicate", run_id=run, blindspot="duplicate-event")
        step_id = self._new_step(
            tx,
            run,
            event,
            status="GAP",
            gap_reason="pre-missing",
            outcome=event.outcome,
            error=event.error,
            ended_at=self._now(),
            duration_ms=event.duration_ms,
        )
        return Outcome("gap", run_id=run, step_id=step_id, gaps=1)

    def _batch(self, tx: Transaction, run: int, event: Event) -> Outcome:
        gaps = 0
        for call in event.batch:
            found = tx.find("steps", "run_id = ? and tool_use_id = ?", (run, call.tool_use_id))
            pending = [s for s in found if s["status"] == "PENDING"]
            for step in pending:
                tx.update(
                    "steps",
                    step["id"],
                    "step.gap",
                    {"status": "GAP", "gap_reason": "post-missing", "ended_at": self._now()},
                )
                gaps += 1
            if not found:
                stub = Event(
                    Kind.TOOL_POST,
                    event.agent,
                    event.raw_event,
                    event.session_id,
                    event.cwd,
                    tool_name=call.tool_name,
                    tool_use_id=call.tool_use_id,
                    agent_id=event.agent_id,
                    prompt_id=event.prompt_id,
                )
                self._new_step(tx, run, stub, status="GAP", gap_reason="reconciled-from-batch")
                gaps += 1
        return Outcome("batch", run_id=run, gaps=gaps)
