"""`agentvc check`: prove recording works here, through the real hook path (plan §6.1).

It does what Claude Code does: runs the liveness command hook (which starts `agentvcd`), then
POSTs a PreToolUse and a PostToolUse over HTTP with the token, and reads the step back from the
ledger. Then the failure path: a PostToolUse without its PreToolUse must come back as a GAP
(ADR-0004). Its runs are flagged `check`, so history and notices leave them out.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentvc import daemon, wiring
from agentvc.clock import Clock, RandomSource
from agentvc.hook import agentvc_home
from agentvc.io import IO
from agentvc.ledger import Ledger, LedgerError
from agentvc.recorder import CHECK_SESSION_PREFIX
from agentvc.store import Store


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Report:
    workspace: Path
    checks: list[Check] = field(default_factory=list)
    round_trip_ms: list[float] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str) -> bool:
        self.checks.append(Check(name, ok, detail))
        return ok


def _payload(session: str, workspace: Path, event: str, **extra: Any) -> bytes:
    data = {
        "session_id": session,
        "transcript_path": "",
        "cwd": str(workspace),
        "permission_mode": "default",
        "hook_event_name": event,
        **extra,
    }
    return json.dumps(data).encode()


def run_check(
    workspace: Path,
    *,
    io: IO,
    clock: Clock,
    rand: RandomSource,
    environ: Mapping[str, str],
    python: str,
) -> Report:
    report = Report(workspace)
    session = CHECK_SESSION_PREFIX + rand.token_hex(8)
    env = {**environ, "CLAUDE_PROJECT_DIR": str(workspace)}

    # 1. The liveness command hook, exactly as the agent runs it.
    start = _payload(session, workspace, "SessionStart", source="startup")
    done = subprocess.run(
        [python, "-I", "-m", "agentvc", "hook", "--ensure-daemon"],
        input=start,
        cwd=workspace,
        env=env,
        capture_output=True,
        timeout=wiring.HOOK_TIMEOUT_S * 3,
    )
    store = Store(workspace)
    state = daemon.load_state(io, store)
    healthy = state is not None and daemon.is_healthy(state)
    detail = f"exit {done.returncode}"
    if state is not None:
        detail += f", agentvcd on 127.0.0.1:{state.port}"
    message = done.stdout.decode("utf-8", "replace").strip()
    if "not running" in message:
        detail += " (" + json.loads(message).get("systemMessage", message)[:200] + ")"
        healthy = False
    if not report.add("liveness", done.returncode == 0 and healthy, detail) or state is None:
        return report

    # 2. A realistic tool call over HTTP, as Claude Code's http hooks send it.
    tool_use = "toolu_agentvc_check_" + rand.token_hex(6)
    tool_input = {"command": "echo agentvc-check", "description": "AgentVC self-check"}
    pre = _payload(
        session,
        workspace,
        "PreToolUse",
        tool_name="Bash",
        tool_input=tool_input,
        tool_use_id=tool_use,
    )
    post = _payload(
        session,
        workspace,
        "PostToolUse",
        tool_name="Bash",
        tool_input=tool_input,
        tool_use_id=tool_use,
        tool_response={"stdout": "agentvc-check", "stderr": "", "interrupted": False},
    )
    lonely = "toolu_agentvc_check_" + rand.token_hex(6)
    lonely_post = _payload(
        session,
        workspace,
        "PostToolUse",
        tool_name="Bash",
        tool_input=tool_input,
        tool_use_id=lonely,
        tool_response={"stdout": "", "stderr": "", "interrupted": False},
    )
    statuses = []
    for body in (pre, post, lonely_post, _payload(session, workspace, "Stop")):
        began = time.perf_counter()
        try:
            status, _ = daemon.call(state, "POST", "/hook", body, timeout=wiring.HOOK_TIMEOUT_S)
        except OSError as exc:
            status = 0
            report.add("http", False, f"agentvcd stopped answering: {exc}")
        report.round_trip_ms.append(round((time.perf_counter() - began) * 1000, 2))
        statuses.append(status)

    # 3. Read the result back from the ledger.
    try:
        ledger = Ledger.open(
            io,
            store.ledger,
            workspace=workspace,
            anchor_dir=agentvc_home(dict(environ)) / "anchors",
            clock=clock,
        )
    except (LedgerError, OSError) as exc:
        report.add("ledger", False, f"can't open the ledger: {exc}")
        return report
    try:
        steps = {s["tool_use_id"]: s for s in ledger.rows("steps")}
        recorded = steps.get(tool_use)
        trips = ", ".join(f"{ms:.1f}" for ms in report.round_trip_ms[:2])
        report.add(
            "record",
            recorded is not None
            and recorded["status"] == "RECORDED"
            and statuses[:2] == [200, 200],
            f"a Bash call through the http hook came back "
            f"{recorded['status'] if recorded else 'MISSING'} (round trip {trips} ms)",
        )
        gap = steps.get(lonely)
        report.add(
            "gap",
            gap is not None and gap["status"] == "GAP" and gap["gap_reason"] == "pre-missing",
            "a PostToolUse without its PreToolUse came back as "
            + (f"{gap['status']} ({gap['gap_reason']})" if gap else "MISSING"),
        )
        verdict = ledger.verify()
        report.add(
            "ledger",
            verdict.ok,
            "verified"
            if verdict.status == "ok"
            else f"{verdict.status}: {'; '.join(verdict.problems)}",
        )
    finally:
        ledger.close()
    return report


def render(report: Report) -> str:
    lines = [f"AgentVC check in {report.workspace}"]
    for c in report.checks:
        lines.append(f"  {'ok  ' if c.ok else 'FAIL'}  {c.name:<9} {c.detail}")
    lines.append(
        "All checks passed."
        if report.ok
        else "Some checks FAILED: AgentVC is not recording reliably here."
    )
    return "\n".join(lines)


def to_json(report: Report) -> dict[str, Any]:
    return {
        "workspace": str(report.workspace),
        "ok": report.ok,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in report.checks],
        "round_trip_ms": report.round_trip_ms,
    }
