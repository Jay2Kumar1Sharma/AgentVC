"""One hook event in, one reply out (design note hook-path.md, ADR-0004).

`handle()` is shared by `agentvcd` (http hooks) and the per-call command. It never raises and
never hangs past its deadline: a failure becomes a visible message and, where the ledger still
works, a GAP. In strict mode a PreToolUse that can't be recorded is blocked.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentvc import blindspots, wiring
from agentvc.adapters import claude_code
from agentvc.clock import Clock, Deadline
from agentvc.config import Config
from agentvc.debuglog import DebugLog
from agentvc.events import BlindSpot, Event, Kind
from agentvc.io import IO, LockTimeout
from agentvc.ledger import Ledger, LedgerError, Transaction
from agentvc.protect import write_into_store
from agentvc.recorder import Outcome, Recorder
from agentvc.scrub import scrub_text
from agentvc.snapshot import SnapshotResult, Snapshotter, changes, row_values
from agentvc.store import Store


def agentvc_home(environ: dict[str, str] | None = None) -> Path:
    """AgentVC's per-user folder (anchors live here, outside every workspace)."""
    env = os.environ if environ is None else environ
    return Path(env["AGENTVC_HOME"]) if env.get("AGENTVC_HOME") else Path.home() / ".agentvc"


@dataclass
class HookContext:
    io: IO
    clock: Clock
    workspace: Path
    config: Config
    anchor_dir: Path
    debug_enabled: bool = False
    deadline_s: float = wiring.DEADLINE_S
    background_baseline: bool = False  # agentvcd builds the first snapshot in a thread
    store: Store = field(init=False)
    debug: DebugLog = field(init=False)
    _ledger: Ledger | None = field(default=None, init=False)
    _recorder: Recorder | None = field(default=None, init=False)
    _snapshotter: Snapshotter | None = field(default=None, init=False)
    _open_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self.store = Store(self.workspace)
        self.debug = DebugLog(
            self.io, self.store.debug_log, enabled=self.debug_enabled, clock=self.clock
        )

    def ledger(self) -> Ledger:
        with self._open_lock:  # agentvcd's request threads share one context
            if self._ledger is None:
                self.store.ensure(self.io)  # the store's .gitignore comes first
                self._ledger = Ledger.open(
                    self.io,
                    self.store.ledger,
                    workspace=self.workspace,
                    anchor_dir=self.anchor_dir,
                    clock=self.clock,
                )
            return self._ledger

    def recorder(self) -> Recorder:
        ledger = self.ledger()
        with self._open_lock:
            if self._recorder is None:
                self._recorder = Recorder(ledger, self.workspace, self.clock)
            return self._recorder

    def snapshotter(self) -> Snapshotter:
        with self._open_lock:
            if self._snapshotter is None:
                self.store.ensure(self.io)
                self._snapshotter = Snapshotter(
                    self.io, self.clock, self.workspace, self.store, self.config.snapshot
                )
            return self._snapshotter

    def close(self) -> None:
        if self._ledger is not None:
            self._ledger.close()
            self._ledger = self._recorder = None


@dataclass(frozen=True)
class HookReply:
    event_name: str | None
    deny_reason: str | None = None
    system_message: str | None = None  # shown to the user
    context: str | None = None  # added to the agent's context (prompt / session events)

    def to_http(self) -> tuple[int, dict[str, Any]]:
        out: dict[str, Any] = {}
        if self.deny_reason:
            out["hookSpecificOutput"] = {
                "hookEventName": self.event_name or "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": self.deny_reason,
            }
        elif self.context:
            out["hookSpecificOutput"] = {
                "hookEventName": self.event_name,
                "additionalContext": self.context,
            }
        if self.system_message:
            out["systemMessage"] = self.system_message
        return 200, out

    def to_command(self) -> tuple[int, str, str]:
        """Exit code, stdout, stderr for a command hook. Exit 2 blocks the tool."""
        if self.deny_reason:
            return 2, "", self.deny_reason
        _, out = self.to_http()
        return 0, json.dumps(out) if out else "", ""


def _log_blindspot(ctx: HookContext, entry: dict[str, Any]) -> None:
    try:
        ctx.store.ensure(ctx.io)
        blindspots.record(ctx.io, ctx.store.blindspots, entry, ctx.clock)
    except Exception:
        pass  # counted as missing at worst; the hook must still answer


def handle(body: bytes, ctx: HookContext) -> HookReply:
    return handle_event(body, ctx)[0]


def handle_event(body: bytes, ctx: HookContext) -> tuple[HookReply, Event | None]:
    """Like `handle`, also returning the event it handled (None for a blind spot)."""
    deadline = Deadline(ctx.clock, ctx.deadline_s)
    start = ctx.clock.monotonic()
    parsed = claude_code.parse_bytes(body, record_prompts=ctx.config.record_prompts)
    parse_ms = (ctx.clock.monotonic() - start) * 1000
    if isinstance(parsed, BlindSpot):
        _log_blindspot(ctx, parsed.to_json())
        ctx.debug.record("hook", event=parsed.event_name, action="blind-spot", reason=parsed.reason)
        return HookReply(event_name=parsed.event_name), None

    event = parsed
    action = "failed"
    try:
        reply, action = _record(event, ctx, deadline)
    except Exception as exc:
        reply = _failed(event, ctx, deadline, exc)
    total_ms = (ctx.clock.monotonic() - start) * 1000
    ctx.debug.record(
        "hook",
        event=event.raw_event,
        action=action,
        decision="deny" if reply.deny_reason else "allow",
        mode=ctx.config.mode,
        parse_ms=round(parse_ms, 2),
        record_ms=round(total_ms - parse_ms, 2),
        total_ms=round(total_ms, 2),
    )
    return reply, event


# Tools whose effects on files are captured: edit tools by their targets, the rest whole.
FULL_SNAPSHOT_CATEGORIES = frozenset({"shell", "mcp", "unknown"})
# Time kept back from the hook's deadline for recording after the snapshot.
RECORD_RESERVE_S = 1.0
_ATTACH_ACTIONS = frozenset({"step-opened", "step-recorded", "gap"})


def _snapshot(event: Event, ctx: HookContext, deadline: Deadline) -> SnapshotResult | None:
    """The before (Pre) or after (Post) snapshot this event needs, or None."""
    if event.kind not in (Kind.TOOL_PRE, Kind.TOOL_POST) or event.tool_name is None:
        return None
    category = wiring.tool_category(event.tool_name)
    if category != "edit" and category not in FULL_SNAPSHOT_CATEGORIES:
        return None
    seconds = min(
        ctx.config.snapshot.time_budget_ms / 1000, deadline.remaining() - RECORD_RESERVE_S
    )
    budget = Deadline(ctx.clock, max(seconds, 0.05))
    try:
        snapper = ctx.snapshotter()
        if category == "edit" and event.targets:
            return snapper.paths(event.targets, cwd=event.cwd, deadline=budget)
        return snapper.full(deadline=budget)
    except Exception as exc:  # the snapshotter reports its own failures; this is a backstop
        return SnapshotResult("failed", reason=f"{type(exc).__name__}: {exc}")


def _attach(
    ctx: HookContext, event: Event, snap: SnapshotResult
) -> Callable[[Transaction, Outcome], None]:
    phase = "before" if event.kind is Kind.TOOL_PRE else "after"

    def attach(tx: Transaction, outcome: Outcome) -> None:
        if outcome.step_id is None or outcome.action not in _ATTACH_ACTIONS:
            return
        values: dict[str, Any] = {
            "run_id": outcome.run_id,
            "step_id": outcome.step_id,
            "phase": phase,
            "taken_at": ctx.clock.now().isoformat(),
            **row_values(snap),
        }
        try:
            if phase == "before" and snap.status == "ok":
                values["outside"] = json.dumps(_outside(ctx, tx, outcome.run_id, snap))
        except Exception as exc:  # outside changes unknown; the snapshot itself still counts
            values["reason"] = f"outside changes couldn't be checked ({exc})"
        tx.insert("snapshots", f"snapshot.{phase}", values)

    return attach


def _outside(
    ctx: HookContext, tx: Transaction, run_id: int | None, snap: SnapshotResult
) -> list[str]:
    """Paths changed since the run's last after snapshot: not by the agent (plan §14.1)."""
    previous = tx.find(
        "snapshots",
        "id = (select max(id) from snapshots where run_id = ? and phase = 'after' "
        "and status = 'ok')",
        (run_id,),
    )
    if not previous:
        return []
    snapper = ctx.snapshotter()
    older = snapper.view_of(previous[0])
    if older is None:
        return []
    return changes(older, snapper.view(snap))


def _snapshot_message(event: Event, snap: SnapshotResult | None) -> str | None:
    if snap is None or snap.status != "failed":
        return None
    when = "before" if event.kind is Kind.TOOL_PRE else "after"
    return (
        f"AgentVC couldn't snapshot the workspace {when} this action ({event.tool_name}): "
        f"{snap.reason}. Its changes can't be rolled back."
    )


def _record(event: Event, ctx: HookContext, deadline: Deadline) -> tuple[HookReply, str]:
    reason = write_into_store(event, ctx.workspace)
    if reason:
        with suppress(Exception):  # protection doesn't depend on recording
            ctx.recorder().deny(event, reason, timeout=max(deadline.remaining(), 0.001))
        return HookReply(event.raw_event, deny_reason=reason), "denied"
    snap = _snapshot(event, ctx, deadline)
    message = _snapshot_message(event, snap)
    if message and ctx.config.mode == "strict" and event.kind is Kind.TOOL_PRE:
        reason = message + " In strict mode, AgentVC blocks actions it can't protect."
        with suppress(Exception):
            ctx.recorder().deny(event, reason, timeout=max(deadline.remaining(), 0.001))
        return HookReply(event.raw_event, deny_reason=reason), "denied-unprotected"
    outcome = ctx.recorder().apply(
        event,
        timeout=max(deadline.remaining(), 0.001),
        on_step=_attach(ctx, event, snap) if snap is not None else None,
    )
    if event.kind in (Kind.SESSION_START, Kind.PROMPT) and ctx.background_baseline:
        with suppress(Exception):
            ctx.snapshotter().start_baseline()
    if message:
        return HookReply(event.raw_event, system_message=message), outcome.action
    if outcome.blindspot:
        _log_blindspot(
            ctx,
            {
                "reason": outcome.blindspot,
                "detail": f"{event.raw_event} {event.tool_use_id or ''}".strip(),
                "event_name": event.raw_event,
                "shape": None,
            },
        )
    return HookReply(event.raw_event), outcome.action


def _failed(event: Event, ctx: HookContext, deadline: Deadline, exc: Exception) -> HookReply:
    timed_out = isinstance(exc, LockTimeout) or deadline.expired()
    detail = scrub_text(f"{type(exc).__name__}: {exc}", home=None, user=None)[:300]
    recorded = False
    if not isinstance(exc, LedgerError):
        try:
            tool_event = event if event.kind in (Kind.TOOL_PRE, Kind.TOOL_POST) else None
            ctx.recorder().record_gap(
                event.session_id,
                "hook-timeout" if timed_out else "hook-error",
                detail,
                event=tool_event,
                timeout=min(1.0, max(deadline.remaining(), 0.2)),
                agent=event.agent,
            )
            recorded = True
        except Exception:
            pass
    what = f"{event.raw_event} {event.tool_name or ''}".strip()
    message = f"AgentVC could not record this action ({what}): {detail}." + (
        " It is listed as a gap." if recorded else " Run `agentvc check`."
    )
    if ctx.config.mode == "strict" and event.kind is Kind.TOOL_PRE:
        return HookReply(
            event.raw_event,
            deny_reason=message + " In strict mode, AgentVC blocks actions it can't record.",
        )
    return HookReply(event.raw_event, system_message=message)
