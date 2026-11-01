"""Snapshots on the hook path (design note snapshots.md): before at PreToolUse, after at Post.

Driven by the captured Claude Code 2.1.302 payloads, pointed at a temporary workspace.
"""

import errno
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agentvc import hook
from agentvc.clock import SystemClock
from agentvc.config import Config, SnapshotConfig
from agentvc.io import IO, Entry
from agentvc.snapshot import changes

FIXTURES = Path(__file__).parent / "fixtures" / "hosts" / "claude-code" / "2.1.302"


def body(ws: Path, name: str, **changes_: Any) -> bytes:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["payload"]
    payload["cwd"] = str(ws)
    if "tool_input" in changes_:
        payload["tool_input"] = {**payload["tool_input"], **changes_.pop("tool_input")}
    payload.update(changes_)
    return json.dumps(payload).encode()


def context(
    ws: Path, *, mode: str = "protect", io: IO | None = None, background: bool = False, **cfg: Any
) -> hook.HookContext:
    return hook.HookContext(
        io=io or IO(),
        clock=SystemClock(),
        workspace=ws,
        config=Config(mode=mode, snapshot=SnapshotConfig(**cfg)),
        anchor_dir=ws.parent / "home" / "anchors",
        background_baseline=background,
    )


def send(ctx: hook.HookContext, ws: Path, *names: str, **changes_: Any) -> hook.HookReply:
    reply = hook.HookReply(None)
    for name in names:
        reply = hook.handle(body(ws, name, **changes_), ctx)
    return reply


def snaps(ctx: hook.HookContext) -> list[dict[str, Any]]:
    return ctx.ledger().rows("snapshots")


def files(ctx: hook.HookContext, row: dict[str, Any]) -> dict[str, bytes]:
    objects = ctx.snapshotter().objects
    return {p: objects.read_blob(s) for p, (_, s) in objects.read_flat(row["tree"]).items()}


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    return root.resolve()


START = ("0002-SessionStart.json", "0003-UserPromptSubmit.json")


def test_a_shell_step_gets_whole_workspace_snapshots_before_and_after(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"one")
    ctx = context(ws)
    send(ctx, ws, *START, "0004-PreToolUse-Bash.json")
    (ws / "a.txt").write_bytes(b"two")  # what the command did
    (ws / "b.txt").write_bytes(b"new")
    reply = send(ctx, ws, "0005-PostToolUse-Bash.json")
    assert reply.to_http() == (200, {})
    [step] = ctx.ledger().rows("steps")
    before, after = snaps(ctx)
    assert (before["phase"], after["phase"]) == ("before", "after")
    assert before["step_id"] == after["step_id"] == step["id"]
    assert before["status"] == after["status"] == "ok"
    assert before["scope"] is None and json.loads(before["outside"]) == []
    assert files(ctx, before) == {"a.txt": b"one"}
    assert files(ctx, after) == {"a.txt": b"two", "b.txt": b"new"}
    snapper = ctx.snapshotter()
    views = [snapper.view_of(r) for r in (before, after)]
    assert views[0] is not None and views[1] is not None
    assert changes(views[0], views[1]) == ["a.txt", "b.txt"]


def test_an_edit_step_snapshots_only_its_target(ws: Path) -> None:
    (ws / "other.txt").write_bytes(b"untouched")
    target = {"tool_input": {"file_path": str(ws / "new.txt")}}
    ctx = context(ws)
    send(ctx, ws, *START)
    send(ctx, ws, "0038-PreToolUse-Write.json", **target)
    (ws / "new.txt").write_bytes(b"written")
    send(ctx, ws, "0039-PostToolUse-Write.json", **target)
    before, after = snaps(ctx)
    assert json.loads(before["scope"]) == json.loads(after["scope"]) == ["new.txt"]
    assert files(ctx, before) == {}
    assert files(ctx, after) == {"new.txt": b"written"}


def test_read_only_tools_get_no_snapshot(ws: Path) -> None:
    ctx = context(ws)
    send(ctx, ws, *START, "0025-PreToolUse-Read.json", "0026-PostToolUse-Read.json")
    assert ctx.ledger().rows("steps") and snaps(ctx) == []


def test_a_denied_action_gets_no_snapshot(ws: Path) -> None:
    ctx = context(ws)
    into_store = {"tool_input": {"file_path": str(ws / ".agentvc" / "ledger.db")}}
    send(ctx, ws, *START)
    reply = send(ctx, ws, "0038-PreToolUse-Write.json", **into_store)
    assert reply.deny_reason
    assert snaps(ctx) == []


def test_changes_between_two_steps_are_recorded_as_outside_changes(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"agent")
    (ws / "b.txt").write_bytes(b"user's v1")
    ctx = context(ws)
    send(ctx, ws, *START, "0048-PreToolUse-Bash.json", "0050-PostToolUse-Bash.json")
    (ws / "b.txt").write_bytes(b"user's v2")  # the user, between two agent steps
    send(ctx, ws, "0049-PreToolUse-Bash.json")
    second_before = snaps(ctx)[-1]
    assert second_before["phase"] == "before"
    assert json.loads(second_before["outside"]) == ["b.txt"]


def test_a_post_without_its_pre_still_gets_an_after_snapshot(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"x")
    ctx = context(ws)
    send(ctx, ws, *START, "0005-PostToolUse-Bash.json")
    [step] = ctx.ledger().rows("steps")
    assert step["status"] == "GAP"
    [after] = snaps(ctx)
    assert after["phase"] == "after" and after["step_id"] == step["id"]


def test_a_failed_snapshot_is_visible_and_fails_open(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"x")
    io = IO()
    ctx = context(ws, io=io)
    send(ctx, ws, *START)
    io.faults.fail("write", OSError(errno.ENOSPC, "No space left on device"), match="tmp_obj")
    reply = send(ctx, ws, "0004-PreToolUse-Bash.json")
    assert reply.deny_reason is None
    assert reply.system_message and "snapshot" in reply.system_message
    assert "No space left" in reply.system_message
    [step] = ctx.ledger().rows("steps")
    assert step["status"] == "PENDING"  # the action itself is still recorded
    [row] = snaps(ctx)
    assert row["status"] == "failed" and "No space left" in row["reason"]


def test_strict_mode_blocks_an_action_it_cannot_snapshot(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"x")
    io = IO()
    ctx = context(ws, mode="strict", io=io)
    send(ctx, ws, *START)
    io.faults.fail("write", OSError(errno.ENOSPC, "No space left on device"), match="tmp_obj")
    reply = send(ctx, ws, "0004-PreToolUse-Bash.json")
    assert reply.deny_reason and "snapshot" in reply.deny_reason
    [step] = ctx.ledger().rows("steps")
    assert step["status"] == "DENIED"


class SlowIO(IO):
    """Listing folders is slow while `slow` is set: a large workspace's first snapshot."""

    def __init__(self) -> None:
        super().__init__()
        self.slow = threading.Event()

    def scandir(self, path: Path) -> list[Entry]:
        if self.slow.is_set():
            time.sleep(0.3)
        return super().scandir(path)


def test_the_first_snapshot_is_built_in_the_background_never_in_a_hook(ws: Path) -> None:
    for i in range(5):
        (ws / f"d{i}").mkdir()
        (ws / f"d{i}" / "f.txt").write_bytes(b"x")
    io = SlowIO()
    ctx = context(ws, io=io, background=True)
    io.slow.set()
    send(ctx, ws, *START)  # SessionStart starts the baseline in agentvcd
    started = time.perf_counter()
    reply = send(ctx, ws, "0004-PreToolUse-Bash.json")
    assert time.perf_counter() - started < 0.5  # it didn't wait for the baseline
    assert reply.to_http() == (200, {})
    [pending] = snaps(ctx)
    assert pending["status"] == "incomplete" and "baseline pending" in pending["reason"]
    io.slow.clear()
    for _ in range(100):
        if not ctx.snapshotter().baseline_running():
            break
        time.sleep(0.05)
    assert ctx.snapshotter().baseline_ready()
    send(ctx, ws, "0005-PostToolUse-Bash.json")
    assert snaps(ctx)[-1]["status"] == "ok"
