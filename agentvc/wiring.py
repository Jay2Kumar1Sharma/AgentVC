"""The single list of agent events and tools AgentVC handles (plan §6.2, design note wiring.md).

The Claude Code hook configuration is generated from this module; tests fail if the generated
config, the adapter and the recorder disagree with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODES = ("observe", "protect", "strict")

# AgentVC's own deadline per hook, and the agent-side hook timeout derived from it (ADR-0004):
# AgentVC stops in time to record what it has before the agent would kill it.
DEADLINE_S = 5
HOOK_TIMEOUT_S = 2 * DEADLINE_S

TOKEN_HEADER = "X-AgentVC-Token"

TOOLS: dict[str, str] = {
    "Bash": "shell",
    "PowerShell": "shell",
    "Edit": "edit",
    "Write": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Read": "read",
    "Glob": "read",
    "Grep": "read",
    "LS": "read",
    "ToolSearch": "read",
    "WebFetch": "network",
    "WebSearch": "network",
    "Agent": "agent",
    "Task": "agent",
    "TodoWrite": "other",
}


def tool_category(name: str) -> str:
    if name in TOOLS:
        return TOOLS[name]
    return "mcp" if name.startswith("mcp__") else "unknown"


@dataclass(frozen=True)
class Subscription:
    event: str
    tools: bool = False  # a tool event: matches every tool
    liveness: bool = False  # a command hook that also starts agentvcd and reports problems


SUBSCRIPTIONS: tuple[Subscription, ...] = (
    Subscription("SessionStart", liveness=True),
    Subscription("UserPromptSubmit", liveness=True),
    Subscription("PreToolUse", tools=True),
    Subscription("PostToolUse", tools=True),
    Subscription("PostToolUseFailure", tools=True),
    Subscription("PostToolBatch"),
    Subscription("SubagentStart"),
    Subscription("SubagentStop"),
    Subscription("Stop"),
    Subscription("SessionEnd"),
)
EVENTS = tuple(s.event for s in SUBSCRIPTIONS)

# Events each Claude Code version knows, read from its binary and confirmed by capture
# (scripts/capture_payloads.py holds the evidence; a test keeps the two equal). 2.1.72 silently
# registers no hooks at all if the settings name one event it doesn't know (FM-INS-03).
_EVENTS_2_1_72 = (
    "SessionStart",
    "Setup",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "TaskCompleted",
    "Stop",
    "TeammateIdle",
    "InstructionsLoaded",
    "ConfigChange",
    "PreCompact",
    "Elicitation",
    "ElicitationResult",
    "SessionEnd",
)
_EVENTS_2_1_302 = (
    "SessionStart",
    "Setup",
    "UserPromptSubmit",
    "UserPromptExpansion",
    "PreToolUse",
    "PermissionRequest",
    "PermissionDenied",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "Notification",
    "MessageDisplay",
    "SubagentStart",
    "SubagentStop",
    "TaskCreated",
    "TaskCompleted",
    "Stop",
    "StopFailure",
    "TeammateIdle",
    "InstructionsLoaded",
    "ConfigChange",
    "CwdChanged",
    "DirectoryAdded",
    "FileChanged",
    "PreCompact",
    "PostCompact",
    "PreModelSwitch",
    "PostModelSwitch",
    "Elicitation",
    "ElicitationResult",
    "SessionEnd",
)
VERSION_EVENTS: dict[str, tuple[str, ...]] = {"2.1.72": _EVENTS_2_1_72, "2.1.302": _EVENTS_2_1_302}

# Features by the first version known to have them (from the binaries and the hooks reference).
HTTP_HOOKS_SINCE = (2, 1, 302)  # verified in the 2.1.302 binary; unknown before
EXEC_FORM_SINCE = (2, 1, 302)  # command hooks with `args`, no shell
ON_FAILURE_SINCE = (2, 1, 295)  # `onFailure: "block"`


class UnsupportedVersion(ValueError):
    pass


@dataclass(frozen=True)
class GeneratedHooks:
    settings: dict[str, Any]
    warnings: tuple[str, ...] = field(default=())


def _parse_version(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    if not match:
        raise UnsupportedVersion(f"can't read the Claude Code version {text!r}")
    major, minor, patch = (int(g) for g in match.groups())
    return major, minor, patch


def _known_list(version: tuple[int, int, int], text: str) -> tuple[str, list[str]]:
    """The newest evidence at or below ``version``; events are added over time, not removed."""
    known = sorted((_parse_version(v), v) for v in VERSION_EVENTS)
    usable = [name for parsed, name in known if parsed <= version]
    if not usable:
        raise UnsupportedVersion(
            f"Claude Code {text} is older than any version AgentVC has evidence for "
            f"({known[0][1]}); its hook events are unknown"
        )
    chosen = usable[-1]
    warnings = []
    if chosen != text:
        warnings.append(
            f"Claude Code {text} isn't a version AgentVC has captured; using the hook events "
            f"of {chosen}. Run `agentvc check` after installing."
        )
    return chosen, warnings


def claude_code_hooks(
    *, agent_version: str, python: Path, port: int, token: str, mode: str
) -> GeneratedHooks:
    """The `hooks` settings for one Claude Code version (ADR-0003, ADR-0004, ADR-0008)."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    if not python.is_absolute():
        raise ValueError(f"the hook interpreter must be an absolute path, got {python}")
    version = _parse_version(agent_version)
    evidence, warnings = _known_list(version, agent_version)
    known_events = VERSION_EVENTS[evidence]
    # Features are judged by the evidence used, not the claimed version.
    effective = _parse_version(evidence)
    http = effective >= HTTP_HOOKS_SINCE
    exec_form = effective >= EXEC_FORM_SINCE
    on_failure = effective >= ON_FAILURE_SINCE
    if mode == "strict" and not on_failure:
        warnings.append(
            f"Claude Code {agent_version}: no onFailure support, so strict mode can block only "
            "the failures AgentVC catches itself (exit 2), not a killed hook"
        )

    def command(*args: str) -> dict[str, Any]:
        full = ["-I", "-m", "agentvc", "hook", *args]
        if exec_form:
            return {"type": "command", "command": str(python), "args": full}
        return {"type": "command", "command": f'"{python.as_posix()}" ' + " ".join(full)}

    hooks: dict[str, list[dict[str, Any]]] = {}
    for sub in SUBSCRIPTIONS:
        if sub.event not in known_events:
            continue
        handler: dict[str, Any]
        if not http:
            handler = command()
        elif sub.liveness:
            handler = command("--ensure-daemon")
        else:
            handler = {
                "type": "http",
                "url": f"http://127.0.0.1:{port}/hook",
                "headers": {TOKEN_HEADER: token},
            }
        handler["timeout"] = HOOK_TIMEOUT_S
        if mode == "strict" and on_failure and sub.event == "PreToolUse":
            handler["onFailure"] = "block"
        group: dict[str, Any] = {"hooks": [handler]}
        if sub.tools:
            group = {"matcher": "*", **group}
        hooks[sub.event] = [group]
    return GeneratedHooks(settings={"hooks": hooks}, warnings=tuple(warnings))
