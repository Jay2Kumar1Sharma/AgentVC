"""One event model for every agent (plan §4.1, design note capture.md).

Adapters turn an agent's hook payload into an `Event`, or into a `BlindSpot` when they can't.
Events hold only what AgentVC may store: scrubbed text, file contents reduced to their size,
prompts only when enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

JSON = Any


class Kind(StrEnum):
    SESSION_START = "session_start"
    PROMPT = "prompt"
    TOOL_PRE = "tool_pre"
    TOOL_POST = "tool_post"
    TOOL_BATCH = "tool_batch"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    STOP = "stop"
    SESSION_END = "session_end"


@dataclass(frozen=True)
class BatchCall:
    tool_use_id: str
    tool_name: str


@dataclass(frozen=True)
class Event:
    kind: Kind
    agent: str
    raw_event: str
    session_id: str
    cwd: str
    tool_name: str | None = None
    tool_use_id: str | None = None
    tool_input: dict[str, JSON] | None = None
    outcome: str | None = None  # "ok" | "error" | "interrupted", on tool_post
    error: str | None = None
    agent_id: str | None = None  # set inside a subagent
    prompt_id: str | None = None
    prompt: str | None = None  # only when record_prompts is on
    source: str | None = None  # SessionStart: startup | resume | clear | compact
    duration_ms: int | None = None
    batch: tuple[BatchCall, ...] = ()
    # An edit tool's target paths as the agent gave them, made absolute against its cwd.
    # Used only to snapshot those files; never stored (`to_json` leaves them out).
    targets: tuple[str, ...] = ()

    def to_json(self) -> dict[str, JSON]:
        data: dict[str, JSON] = {"kind": self.kind.value}
        for name in (
            "agent",
            "raw_event",
            "session_id",
            "cwd",
            "tool_name",
            "tool_use_id",
            "tool_input",
            "outcome",
            "error",
            "agent_id",
            "prompt_id",
            "prompt",
            "source",
            "duration_ms",
        ):
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        if self.batch:
            data["batch"] = [
                {"tool_use_id": c.tool_use_id, "tool_name": c.tool_name} for c in self.batch
            ]
        return data


@dataclass(frozen=True)
class BlindSpot:
    """A payload AgentVC couldn't turn into an event: kept as its shape, never its content."""

    reason: str  # not-json | not-object | too-large | missing-field | bad-field | unhandled-event
    detail: str
    event_name: str | None
    shape: JSON

    def to_json(self) -> dict[str, JSON]:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "event_name": self.event_name,
            "shape": self.shape,
        }
