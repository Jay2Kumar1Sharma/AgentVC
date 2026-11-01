"""A simulated agent for restore tests: real Claude Code 2.1.302 payloads through `hook.handle`.

Each action sends PreToolUse, does what the tool would do to the workspace, then sends
PostToolUse, so the snapshots and steps are exactly what the hook path records.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from itertools import count
from pathlib import Path
from typing import Any

from agentvc import hook
from agentvc.clock import SystemClock
from agentvc.config import Config, SnapshotConfig
from agentvc.io import IO
from agentvc.restore import Restorer

FIXTURES = Path(__file__).parent / "fixtures" / "hosts" / "claude-code" / "2.1.302"
_ids = count(1)


def _payload(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["payload"]
    return data


class Agent:
    def __init__(self, ws: Path, *, io: IO | None = None, **cfg: Any) -> None:
        self.ws = ws
        self.ctx = hook.HookContext(
            io=io or IO(),
            clock=SystemClock(),
            workspace=ws,
            config=Config(snapshot=SnapshotConfig(**cfg)),
            anchor_dir=ws.parent / "home" / "anchors",
        )
        self.session = f"sim-session-{next(_ids)}"
        self.prompt_id = ""
        self.send("0002-SessionStart.json")
        self.prompt()

    def send(self, name: str, **changes: Any) -> hook.HookReply:
        data = _payload(name)
        data.update(cwd=str(self.ws), session_id=self.session)
        if "prompt_id" in data or name.endswith("UserPromptSubmit.json"):
            data["prompt_id"] = self.prompt_id
        if "tool_input" in changes:
            data["tool_input"] = {**data["tool_input"], **changes.pop("tool_input")}
        data.update(changes)
        return hook.handle(json.dumps(data).encode(), self.ctx)

    def prompt(self) -> None:
        self.prompt_id = f"prompt-{next(_ids)}"
        self.send("0003-UserPromptSubmit.json")

    def shell(self, action: Callable[[], object], command: str = "do something") -> None:
        call = f"toolu_sim_{next(_ids)}"
        cmd = {"tool_input": {"command": command}}
        self.send("0004-PreToolUse-Bash.json", tool_use_id=call, **cmd)
        action()
        self.send("0005-PostToolUse-Bash.json", tool_use_id=call, **cmd)

    def pre_shell(self) -> str:
        call = f"toolu_sim_{next(_ids)}"
        self.send("0004-PreToolUse-Bash.json", tool_use_id=call)
        return call

    def write(self, rel: str, data: bytes) -> None:
        call = f"toolu_sim_{next(_ids)}"
        target = {"tool_input": {"file_path": str(self.ws / rel)}}
        self.send("0038-PreToolUse-Write.json", tool_use_id=call, **target)
        (self.ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.ws / rel).write_bytes(data)
        self.send("0039-PostToolUse-Write.json", tool_use_id=call, **target)

    @property
    def run(self) -> int:
        runs = self.ctx.ledger().rows("runs")
        return int(runs[-1]["id"])

    def restorer(self) -> Restorer:
        return Restorer(self.ctx.io, self.ctx.clock, self.ctx.ledger(), self.ctx.snapshotter())

    def close(self) -> None:
        self.ctx.close()


def tree(ws: Path) -> dict[str, bytes]:
    """Every file in the workspace (outside .agentvc), by relative path."""
    return {
        p.relative_to(ws).as_posix(): p.read_bytes()
        for p in sorted(ws.rglob("*"))
        if p.is_file() and ".agentvc" not in p.relative_to(ws).parts
    }
