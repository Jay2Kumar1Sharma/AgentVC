"""Claude Code adapter: hook payload → `Event` or `BlindSpot` (design note capture.md).

Built only from captured payloads (tests/fixtures/hosts/claude-code/, versions 2.1.72 and
2.1.302). What it keeps (U-13, G6): commands and paths, scrubbed and capped; file contents
reduced to their size; tool output reduced to an outcome; prompts only when enabled; assistant
text never.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentvc.events import JSON, BatchCall, BlindSpot, Event, Kind
from agentvc.scrub import scrub_text

AGENT = "claude-code"

EVENT_KINDS: dict[str, Kind] = {
    "SessionStart": Kind.SESSION_START,
    "UserPromptSubmit": Kind.PROMPT,
    "PreToolUse": Kind.TOOL_PRE,
    "PostToolUse": Kind.TOOL_POST,
    "PostToolUseFailure": Kind.TOOL_POST,
    "PostToolBatch": Kind.TOOL_BATCH,
    "SubagentStart": Kind.SUBAGENT_START,
    "SubagentStop": Kind.SUBAGENT_STOP,
    "Stop": Kind.STOP,
    "SessionEnd": Kind.SESSION_END,
}

_COMMON: dict[str, type] = {"session_id": str, "hook_event_name": str, "cwd": str}
_TOOL: dict[str, type] = {"tool_name": str, "tool_use_id": str, "tool_input": dict}
_REQUIRED: dict[str, dict[str, type]] = {
    "PreToolUse": _TOOL,
    "PostToolUse": _TOOL,
    "PostToolUseFailure": _TOOL,
    "PostToolBatch": {"tool_calls": list},
    "SubagentStart": {"agent_id": str},
    "SubagentStop": {"agent_id": str},
}

# Keys whose values are file contents or model output: stored as their size only.
CONTENT_KEYS = frozenset(
    {
        "content",
        "old_string",
        "new_string",
        "new_source",
        "originalFile",
        "oldString",
        "newString",
        "structuredPatch",
        "last_assistant_message",
    }
)
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
MAX_BODY_BYTES = 64 * 1024 * 1024
MAX_TEXT_CHARS = 4096
MAX_PROMPT_CHARS = 16384
MAX_ERROR_CHARS = 1000
BLINDSPOT_MAX_CHARS = 16384


def required_fields(event_name: str) -> dict[str, type]:
    return {**_COMMON, **_REQUIRED.get(event_name, {})}


def text(value: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Scrubbed, storable (no NUL, no lone surrogates), capped with a visible marker."""
    value = value.replace("\x00", "�").encode("utf-8", "replace").decode("utf-8")
    value = scrub_text(value, home=None, user=None)
    if len(value) > limit:
        value = value[:limit] + f"…[+{len(value) - limit} chars]"
    return value


def reduce(value: JSON, depth: int = 0) -> JSON:
    """Tool input as stored: content-bearing fields become their size, strings scrubbed."""
    if depth > 8:
        return "…"
    if isinstance(value, dict):
        out: dict[str, JSON] = {}
        for key, item in list(value.items())[:100]:
            name = text(str(key), 100)
            if key in CONTENT_KEYS:
                out[name] = {"chars": len(item) if isinstance(item, str) else len(json.dumps(item))}
            else:
                out[name] = reduce(item, depth + 1)
        return out
    if isinstance(value, list):
        return [reduce(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return text(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return text(repr(value), 100)


def shape(value: JSON, depth: int = 0) -> JSON:
    """The structure of a payload without its content: keys and value types."""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {text(str(k), 100): shape(v, depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [shape(v, depth + 1) for v in value[:3]] + (
            [f"+{len(value) - 3}"] if len(value) > 3 else []
        )
    if isinstance(value, str):
        return f"str({len(value)})"
    if value is None:
        return "null"
    return type(value).__name__


def _blind(reason: str, detail: str, payload: JSON = None, name: str | None = None) -> BlindSpot:
    spot = BlindSpot(reason, text(detail, 500), text(name, 100) if name else None, shape(payload))
    if len(json.dumps(spot.to_json())) > BLINDSPOT_MAX_CHARS:
        spot = BlindSpot(reason, spot.detail, spot.event_name, "(shape too large)")
    return spot


def parse_bytes(body: bytes, *, record_prompts: bool) -> Event | BlindSpot:
    if len(body) > MAX_BODY_BYTES:
        return _blind("too-large", f"{len(body)} bytes")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        return _blind("not-json", type(exc).__name__)
    return parse(payload, record_prompts=record_prompts)


def _str(payload: dict[str, JSON], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def parse(payload: JSON, *, record_prompts: bool) -> Event | BlindSpot:
    if not isinstance(payload, dict):
        return _blind("not-object", type(payload).__name__)
    name = payload.get("hook_event_name")
    if not isinstance(name, str):
        reason = "missing-field" if name is None else "bad-field"
        return _blind(reason, "hook_event_name", payload)
    if name not in EVENT_KINDS:
        return _blind("unhandled-event", f"event {name!r} isn't handled", payload, name)
    for key, expected in required_fields(name).items():
        if key not in payload:
            return _blind("missing-field", key, payload, name)
        if not isinstance(payload[key], expected):
            return _blind("bad-field", f"{key} is {type(payload[key]).__name__}", payload, name)
    for key in ("session_id", "tool_use_id", "agent_id"):
        value = payload.get(key)
        if key in required_fields(name) and not (isinstance(value, str) and _ID.fullmatch(value)):
            return _blind("bad-field", f"{key} isn't an id", payload, name)

    kind = EVENT_KINDS[name]
    fields: dict[str, Any] = {
        "kind": kind,
        "agent": AGENT,
        "raw_event": name,
        "session_id": payload["session_id"],
        "cwd": text(payload["cwd"]),
        "prompt_id": text(p, 200) if (p := _str(payload, "prompt_id")) else None,
        "source": text(s, 50) if (s := _str(payload, "source")) else None,
    }
    agent_id = _str(payload, "agent_id")
    if agent_id and _ID.fullmatch(agent_id):
        fields["agent_id"] = agent_id
    duration = payload.get("duration_ms")
    if isinstance(duration, int) and not isinstance(duration, bool):
        fields["duration_ms"] = duration

    if kind in (Kind.TOOL_PRE, Kind.TOOL_POST):
        fields["tool_name"] = text(payload["tool_name"], 200)
        fields["tool_use_id"] = payload["tool_use_id"]
        fields["tool_input"] = reduce(payload["tool_input"])
    if kind is Kind.TOOL_POST:
        fields.update(_outcome(name, payload))
    if kind is Kind.PROMPT and record_prompts and (prompt := _str(payload, "prompt")):
        fields["prompt"] = text(prompt, MAX_PROMPT_CHARS)
    if kind is Kind.TOOL_BATCH:
        calls = []
        for call in payload["tool_calls"]:
            if not (
                isinstance(call, dict)
                and isinstance(call.get("tool_use_id"), str)
                and _ID.fullmatch(call["tool_use_id"])
                and isinstance(call.get("tool_name"), str)
            ):
                return _blind("bad-field", "tool_calls entry", payload, name)
            calls.append(BatchCall(call["tool_use_id"], text(call["tool_name"], 200)))
        fields["batch"] = tuple(calls)
    return Event(**fields)


def _outcome(name: str, payload: dict[str, JSON]) -> dict[str, str | None]:
    if name == "PostToolUseFailure":
        error = _str(payload, "error")
        outcome = "interrupted" if payload.get("is_interrupt") is True else "error"
        return {"outcome": outcome, "error": text(error, MAX_ERROR_CHARS) if error else None}
    response = payload.get("tool_response")
    if isinstance(response, dict) and response.get("interrupted") is True:
        return {"outcome": "interrupted", "error": None}
    return {"outcome": "ok", "error": None}
