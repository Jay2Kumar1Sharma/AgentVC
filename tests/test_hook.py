"""agentvc/hook.py: one hook event in, one reply out; failure policy per ADR-0004.

Fault-injection tests (FM-HOOK-01): the ledger can't be written, is locked past the deadline,
is too new, or the recorder crashes. Default modes fail open with a visible message and a GAP
where possible; strict mode blocks PreToolUse.
"""

import errno
import json
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentvc import hook
from agentvc.clock import FakeClock, SystemClock
from agentvc.config import Config
from agentvc.io import IO
from agentvc.ledger import Ledger
from agentvc.recorder import Recorder

FIXTURES = Path(__file__).parent / "fixtures" / "hosts" / "claude-code" / "2.1.302"


def body(name: str, **changes: Any) -> bytes:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["payload"]
    payload.update(changes)
    return json.dumps(payload).encode()


def context(
    tmp_path: Path, *, mode: str = "protect", io: IO | None = None, **kw: Any
) -> hook.HookContext:
    return hook.HookContext(
        io=io or IO(),
        clock=kw.pop("clock", SystemClock()),
        workspace=tmp_path,
        config=Config(mode=mode, record_prompts=kw.pop("record_prompts", False)),
        anchor_dir=tmp_path / "home" / "anchors",
        debug_enabled=kw.pop("debug", False),
        deadline_s=kw.pop("deadline_s", 5.0),
    )


CWD = {"cwd": "PLACEHOLDER"}


def ev(tmp_path: Path, name: str, **changes: Any) -> bytes:
    return body(name, cwd=str(tmp_path), **changes)


def steps(ctx: hook.HookContext) -> list[dict[str, Any]]:
    return ctx.ledger().rows("steps")


def test_pre_and_post_are_recorded_and_the_agent_is_not_disturbed(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    for name in (
        "0002-SessionStart.json",
        "0003-UserPromptSubmit.json",
        "0004-PreToolUse-Bash.json",
        "0005-PostToolUse-Bash.json",
    ):
        reply = hook.handle(ev(tmp_path, name), ctx)
        assert reply.to_command() == (0, "", "")
        assert reply.to_http() == (200, {})
    [step] = steps(ctx)
    assert step["status"] == "RECORDED"
    assert (tmp_path / ".agentvc" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_writes_into_the_store_are_denied_and_recorded_as_denied(tmp_path: Path) -> None:
    ctx = context(tmp_path, mode="observe")  # self-protection holds in every mode
    target = str(tmp_path / ".agentvc" / "ledger.db")
    reply = hook.handle(
        ev(
            tmp_path, "0038-PreToolUse-Write.json", tool_input={"file_path": target, "content": "x"}
        ),
        ctx,
    )
    code, _, stderr = reply.to_command()
    assert code == 2 and ".agentvc" in stderr
    status, payload = reply.to_http()
    decision = payload["hookSpecificOutput"]
    assert (status, decision["permissionDecision"]) == (200, "deny")
    [step] = steps(ctx)
    assert step["status"] == "DENIED"


def test_unreadable_payloads_go_to_the_blind_spot_log(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    reply = hook.handle(b"{not json", ctx)
    assert reply.to_command() == (0, "", "")
    [line] = (tmp_path / ".agentvc" / "blindspots.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["reason"] == "not-json"


def test_duplicates_and_foreign_sessions_are_logged_as_blind_spots(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    pre = ev(tmp_path, "0004-PreToolUse-Bash.json")
    hook.handle(pre, ctx)
    hook.handle(pre, ctx)
    hook.handle(body("0004-PreToolUse-Bash.json", session_id="other", cwd="Z:\\other"), ctx)
    lines = (tmp_path / ".agentvc" / "blindspots.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["reason"] for x in lines] == ["duplicate-event", "foreign-project"]


# --- failure policy (ADR-0004) --------------------------------------------------------------------


def test_a_failed_write_fails_open_visibly_and_leaves_a_gap(tmp_path: Path) -> None:
    io = IO()
    ctx = context(tmp_path, io=io)
    hook.handle(ev(tmp_path, "0003-UserPromptSubmit.json"), ctx)
    io.faults.fail("lock", OSError(errno.EIO, "I/O error"), match="ledger")  # one failure
    reply = hook.handle(ev(tmp_path, "0004-PreToolUse-Bash.json"), ctx)
    code, stdout, _ = reply.to_command()
    assert code == 0
    assert "could not record" in json.loads(stdout)["systemMessage"]
    [gap] = steps(ctx)
    assert (gap["status"], gap["gap_reason"], gap["tool"]) == ("GAP", "hook-error", "Bash")


def test_strict_mode_blocks_a_pre_tool_use_it_cannot_record(tmp_path: Path) -> None:
    io = IO()
    ctx = context(tmp_path, mode="strict", io=io)
    hook.handle(ev(tmp_path, "0003-UserPromptSubmit.json"), ctx)
    io.faults.fail("lock", OSError(errno.EIO, "I/O error"), match="ledger", times=10)
    code, _, stderr = hook.handle(ev(tmp_path, "0004-PreToolUse-Bash.json"), ctx).to_command()
    assert code == 2 and "strict" in stderr
    # Post events are never blocked: the tool already ran.
    code, stdout, _ = hook.handle(ev(tmp_path, "0005-PostToolUse-Bash.json"), ctx).to_command()
    assert code == 0 and "could not record" in json.loads(stdout)["systemMessage"]


def test_a_locked_ledger_fails_open_within_the_deadline(tmp_path: Path) -> None:
    ctx = context(tmp_path, deadline_s=0.5)
    hook.handle(ev(tmp_path, "0003-UserPromptSubmit.json"), ctx)
    with IO().lock(ctx.ledger().lock_path, timeout=1):
        start = time.monotonic()
        reply = hook.handle(ev(tmp_path, "0004-PreToolUse-Bash.json"), ctx)
        elapsed = time.monotonic() - start
    assert elapsed < 2.0
    code, stdout, _ = reply.to_command()
    assert code == 0 and "could not record" in json.loads(stdout)["systemMessage"]


def test_a_recorder_crash_is_caught_and_becomes_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path)
    hook.handle(ev(tmp_path, "0003-UserPromptSubmit.json"), ctx)

    def boom(self: Recorder, event: object, *, timeout: float) -> None:
        raise RuntimeError("bug in AgentVC")

    monkeypatch.setattr(Recorder, "apply", boom)
    code, stdout, _ = hook.handle(ev(tmp_path, "0004-PreToolUse-Bash.json"), ctx).to_command()
    assert code == 0 and "bug in AgentVC" in json.loads(stdout)["systemMessage"]
    monkeypatch.undo()
    [gap] = steps(ctx)
    assert gap["gap_reason"] == "hook-error"


def test_a_newer_ledger_is_a_visible_recording_failure(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    hook.handle(ev(tmp_path, "0003-UserPromptSubmit.json"), ctx)
    ctx.close()
    db = sqlite3.connect(tmp_path / ".agentvc" / "ledger.db", isolation_level=None)
    db.execute("update schema_version set version = 99")
    db.close()
    ctx = context(tmp_path, mode="strict")
    code, _, stderr = hook.handle(ev(tmp_path, "0004-PreToolUse-Bash.json"), ctx).to_command()
    assert code == 2 and "newer AgentVC" in stderr
    ctx = context(tmp_path)
    code, stdout, _ = hook.handle(ev(tmp_path, "0004-PreToolUse-Bash.json"), ctx).to_command()
    assert code == 0 and "newer AgentVC" in json.loads(stdout)["systemMessage"]


def test_disk_full_with_no_way_to_record_still_tells_the_user(tmp_path: Path) -> None:
    io = IO()
    ctx = context(tmp_path, io=io)
    hook.handle(ev(tmp_path, "0003-UserPromptSubmit.json"), ctx)
    for op in ("lock", "append", "write"):
        io.faults.fail(op, OSError(errno.ENOSPC, "No space left on device"), times=100)
    code, stdout, _ = hook.handle(ev(tmp_path, "0004-PreToolUse-Bash.json"), ctx).to_command()
    assert code == 0
    assert "No space left" in json.loads(stdout)["systemMessage"]


# --- privacy and debugging -----------------------------------------------------------------------


def test_prompts_are_stored_only_when_enabled(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    hook.handle(ev(tmp_path, "0003-UserPromptSubmit.json"), ctx)
    assert ctx.ledger().rows("turns")[0]["prompt"] is None
    ctx2 = context(tmp_path / "b", record_prompts=True)
    (tmp_path / "b").mkdir()
    hook.handle(ev(tmp_path / "b", "0003-UserPromptSubmit.json"), ctx2)
    assert ctx2.ledger().rows("turns")[0]["prompt"]


def test_debug_log_has_timings_and_no_content(tmp_path: Path) -> None:
    ctx = context(tmp_path, debug=True, clock=FakeClock(datetime(2026, 10, 22, tzinfo=UTC)))
    hook.handle(
        ev(tmp_path, "0004-PreToolUse-Bash.json", tool_input={"command": "echo SECRETWORD"}), ctx
    )
    [line] = (tmp_path / ".agentvc" / "debug.log").read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["event"] == "PreToolUse" and record["action"] == "step-opened"
    assert {"parse_ms", "record_ms", "total_ms"} <= set(record)
    assert "SECRETWORD" not in line


def test_reply_shapes() -> None:
    allow = hook.HookReply(event_name="Stop")
    assert allow.to_command() == (0, "", "")
    told = replace(allow, system_message="hi")
    assert told.to_http() == (200, {"systemMessage": "hi"})
    ctx_reply = hook.HookReply(event_name="UserPromptSubmit", context="note")
    assert ctx_reply.to_http()[1]["hookSpecificOutput"] == {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "note",
    }


def test_the_ledger_is_opened_once_per_context(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    hook.handle(ev(tmp_path, "0003-UserPromptSubmit.json"), ctx)
    first = ctx.ledger()
    hook.handle(ev(tmp_path, "0004-PreToolUse-Bash.json"), ctx)
    assert ctx.ledger() is first
    assert isinstance(first, Ledger)
