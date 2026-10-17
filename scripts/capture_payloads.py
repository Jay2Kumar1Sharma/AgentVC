"""Capture real Claude Code hook payloads as test fixtures (plan §6.2, §8 Discovery).

Three commands:

    setup DIR     Create a THROWAWAY project folder (outside this repository) whose
                  project-local .claude/settings.json sends every hook event to this script.
                  Never touches the user's global agent configuration.
    hook          The hook itself: reads one payload from stdin, scrubs it, and writes it to
                  tests/fixtures/hosts/claude-code/<version>/NNNN-<event>[-<tool>].json.
                  Prints nothing (stdout of some hooks becomes agent context) and always
                  exits 0, so it can never block or change the agent's behaviour.
    summary DIR   Count captured payloads per event and tool.

Scrubbing removes known token formats, assigned secrets (PASSWORD=..., api_key: ...), bearer
tokens, private keys, email addresses, the user's home folder (-> ~) and user name (-> <user>).
Review the captured files before committing them anyway.

Usage:
    python scripts/capture_payloads.py setup %TEMP%/agentvc-capture
    python scripts/capture_payloads.py summary tests/fixtures/hosts/claude-code/2.1.72
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

SCRIPT_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures" / "hosts"
HOOK_TIMEOUT_SECONDS = 10

# Events from the Claude Code hooks reference (code.claude.com/docs/en/hooks, read 2026-10-18).
# WorktreeCreate/WorktreeRemove are left out: a hook there replaces Claude Code's own
# worktree handling. "core" is the long-standing subset, for versions that reject newer names.
EVENTS: dict[str, tuple[str, ...]] = {
    "core": (
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "SubagentStop",
        "Notification",
        "PreCompact",
    ),
    "all": (
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
    ),
}

# --- Scrubbing --------------------------------------------------------------------------------

_TOKEN_PATTERNS = [
    re.compile(p)
    for p in (
        r"sk-ant-[\w-]{20,}",
        r"\bsk-(?:proj-|live_|test_)?[A-Za-z0-9_-]{20,}",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}",
        r"\bgithub_pat_[A-Za-z0-9_]{30,}",
        r"\bxox[abposr]-[A-Za-z0-9-]{10,}",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        r"\bAIza[0-9A-Za-z_-]{35}",
        r"\bglpat-[A-Za-z0-9_-]{20,}",
        r"\bnpm_[A-Za-z0-9]{36}",
        r"\bpypi-[A-Za-z0-9_-]{30,}",
        r"\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}",
    )
]
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
)
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}")
_SECRET_NAME = (
    r"[\w.-]*(?:api[_-]?key|token|secret|password|passwd|credential|access[_-]?key)[\w.-]*"
)
_ASSIGNED = re.compile(rf"(?i)\b({_SECRET_NAME})(\s*[:=]\s*)(['\"]?)([^\s'\"]{{6,}})\3")
_SECRET_KEY = re.compile(rf"(?i)^{_SECRET_NAME}$")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def scrub_text(text: str, *, home: str | None, user: str | None) -> str:
    """Remove secrets and personal identifiers from one string."""
    text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    text = _BEARER.sub(r"\1 [REDACTED]", text)
    text = _ASSIGNED.sub(r"\1\2\3[REDACTED]\3", text)
    text = _EMAIL.sub("<email>", text)
    if home and len(home) > 3:
        variants = {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}
        if drive := re.match(r"([A-Za-z]):[\\/](.*)", home):  # Git Bash form: /c/Users/name
            variants.add(f"/{drive.group(1).lower()}/" + drive.group(2).replace("\\", "/"))
        for variant in variants:
            text = re.sub(re.escape(variant), "~", text, flags=re.IGNORECASE)
    if user and len(user) >= 3:
        text = re.sub(rf"(?i)(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", "<user>", text)
    return text


def scrub_value(value: Any, *, home: str | None, user: str | None) -> Any:
    """Scrub every string in a JSON value; values under secret-looking keys are dropped."""
    if isinstance(value, str):
        return scrub_text(value, home=home, user=user)
    if isinstance(value, list):
        return [scrub_value(v, home=home, user=user) for v in value]
    if isinstance(value, dict):
        return {
            scrub_text(str(k), home=home, user=user): (
                "[REDACTED]"
                if isinstance(v, str) and _SECRET_KEY.match(str(k))
                else scrub_value(v, home=home, user=user)
            )
            for k, v in value.items()
        }
    return value


# --- Hook mode --------------------------------------------------------------------------------


def _safe_name(value: Any, default: str) -> str:
    text = value if isinstance(value, str) and value else default
    return re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")[:40] or default


def _default_identity() -> tuple[str | None, str | None]:
    home = os.path.expanduser("~")
    try:
        user: str | None = getpass.getuser()
    except Exception:
        user = None
    return (home if home != "~" else None), user


def hook(
    out: Path,
    *,
    stdin: TextIO | None = None,
    home: str | None = None,
    user: str | None = None,
) -> int:
    """Record one hook payload. Never prints to stdout and always returns 0."""
    try:
        raw = (stdin or sys.stdin).read()
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
            unparseable = False
        except ValueError:
            payload, unparseable = None, True

        if payload is None:
            name = "unparseable"
        else:
            name = _safe_name(payload.get("hook_event_name"), "unknown")
            if "tool_name" in payload:
                name += "-" + _safe_name(payload.get("tool_name"), "tool")

        out.mkdir(parents=True, exist_ok=True)
        existing = [int(m.group(1)) for p in out.iterdir() if (m := re.match(r"(\d{4})-", p.name))]
        seq = max(existing, default=0) + 1
        while True:
            path = out / f"{seq:04d}-{name}.json"
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                seq += 1

        record = {
            "capture": {
                "seq": seq,
                "received_at": datetime.now(UTC).isoformat(),
                "pid": os.getpid(),
                "unparseable": unparseable,
                "script_version": SCRIPT_VERSION,
            },
            "payload": None if payload is None else scrub_value(payload, home=home, user=user),
        }
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    except Exception as exc:  # A capture failure must never reach the agent.
        print(f"capture_payloads: {type(exc).__name__}", file=sys.stderr)
    return 0


# --- Setup mode -------------------------------------------------------------------------------

_CAPTURE_GUIDE = """\
# Payload capture session

This folder is a throwaway project for `scripts/capture_payloads.py` (AgentVC repository).
Its `.claude/settings.json` sends every hook event to the capture script, which writes
scrubbed payloads to:

    {out}

Start Claude Code here (`claude`), run `/hooks` to confirm the hooks are registered, then
send these prompts one at a time:

1. Run `echo hello` with the Bash tool.
2. Run `Get-ChildItem` with the PowerShell tool. (If there is no PowerShell tool, skip this.)
3. Use the Edit tool to change "one" to "two" in notes.txt.
4. Use the Write tool to create new.txt containing "hi".
5. In a single message, run `echo a` and `echo b` as two parallel Bash tool calls.
6. Use a subagent to list the files in this folder.
7. Run `exit 3` with the Bash tool.

Then type `/exit`. Delete this folder afterwards.
"""


def _claude_version() -> str:
    try:
        result = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"could not run `claude --version` ({exc}); pass --agent-version") from exc
    match = re.match(r"\s*(\d+\.\d+\.\d+)", result.stdout)
    if not match:
        raise SystemExit("could not read the Claude Code version; pass --agent-version")
    return match.group(1)


def setup(target: Path, *, fixtures_root: Path, agent_version: str, events: str) -> Path:
    """Create the throwaway capture project. Returns the fixture folder hooks will write to."""
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", agent_version) or ".." in agent_version:
        raise SystemExit(f"unsafe agent version: {agent_version!r}")
    target = target.resolve()
    if target == REPO_ROOT or REPO_ROOT in target.parents:
        raise SystemExit("the throwaway folder must be outside this repository")
    settings_path = target / ".claude" / "settings.json"
    if settings_path.exists():
        raise SystemExit(f"{settings_path} already exists; use a new, empty folder")

    out = (fixtures_root / "claude-code" / agent_version).resolve()
    command = (
        f'"{Path(sys.executable).as_posix()}" "{Path(__file__).resolve().as_posix()}" '
        f'hook --out "{out.as_posix()}"'
    )
    handler = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}
    settings = {"hooks": {event: [{"hooks": [handler]}] for event in EVENTS[events]}}

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8", newline="\n")
    (target / "notes.txt").write_text("one\n", encoding="utf-8", newline="\n")
    (target / "CAPTURE.md").write_text(
        _CAPTURE_GUIDE.format(out=out), encoding="utf-8", newline="\n"
    )
    return out


# --- Summary ----------------------------------------------------------------------------------


def summary(folder: Path) -> str:
    counts: Counter[str] = Counter()
    for path in sorted(folder.glob("*.json")):
        counts[path.stem.split("-", 1)[1] if "-" in path.stem else path.stem] += 1
    return "\n".join(f"{count:4}  {name}" for name, count in sorted(counts.items())) or "(none)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    p_hook = commands.add_parser("hook", help="record one payload from stdin")
    p_hook.add_argument("--out", type=Path, required=True)
    p_setup = commands.add_parser("setup", help="create a throwaway capture project")
    p_setup.add_argument("folder", type=Path)
    p_setup.add_argument("--agent-version", help="default: from `claude --version`")
    p_setup.add_argument("--events", choices=sorted(EVENTS), default="all")
    p_summary = commands.add_parser("summary", help="count captured payloads")
    p_summary.add_argument("folder", type=Path)
    args = parser.parse_args(argv)

    if args.command == "hook":
        home, user = _default_identity()
        return hook(args.out, home=home, user=user)
    if args.command == "setup":
        version = args.agent_version or _claude_version()
        out = setup(
            args.folder, fixtures_root=FIXTURES_ROOT, agent_version=version, events=args.events
        )
        print(f"Created {args.folder.resolve()} (Claude Code {version}, {args.events} events).")
        print(f"Payloads will be written to {out}")
        print(f"Next: follow {args.folder.resolve() / 'CAPTURE.md'}")
        return 0
    print(summary(args.folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
