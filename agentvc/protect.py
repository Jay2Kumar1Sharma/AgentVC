"""Self-protection: the agent may not write into `.agentvc/` (plan §14.1, G8).

Checked on PreToolUse, in every mode. Edit tools are judged by their resolved target path;
shell commands by whether their text names the store. A determined agent can hide a shell
write (variables, encodings); the ledger's hash chain and anchor catch what that changes.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from agentvc import wiring
from agentvc.events import Event, Kind
from agentvc.store import STORE_DIR

_PATH_FIELDS = ("file_path", "notebook_path", "path")
# `.agentvc` as a whole path component: not `.agentvc-backup`, `.agentvcrc` or `agentvc`.
_NAMES_STORE = re.compile(r"(?i)(?<![\w.-])\.agentvc(?![\w.-])")
_CASE_INSENSITIVE = sys.platform in ("win32", "darwin")


def _canonical(path: str) -> str:
    resolved = os.path.normcase(os.path.realpath(path))
    return resolved.casefold() if _CASE_INSENSITIVE else resolved


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def write_into_store(event: Event, workspace: Path) -> str | None:
    """Why this PreToolUse must be denied, or None when it may run."""
    if event.kind is not Kind.TOOL_PRE or event.tool_name is None or event.tool_input is None:
        return None
    category = wiring.tool_category(event.tool_name)
    store = _canonical(str(workspace / STORE_DIR))
    if category == "edit":
        for field in _PATH_FIELDS:
            value = event.tool_input.get(field)
            if isinstance(value, str) and value:
                target = Path(value) if os.path.isabs(value) else Path(event.cwd) / value
                if _inside(_canonical(str(target)), store):
                    return (
                        f"AgentVC: writing into {STORE_DIR}/ is not allowed; it holds the record "
                        "of what the agent did. Use the `agentvc` command to read it."
                    )
    if category == "shell":
        command = event.tool_input.get("command")
        if isinstance(command, str) and _NAMES_STORE.search(command):
            return (
                f"AgentVC: shell commands may not touch {STORE_DIR}/; it holds the record of what "
                "the agent did. Use the `agentvc` command (history, inspect) to read it."
            )
    return None
