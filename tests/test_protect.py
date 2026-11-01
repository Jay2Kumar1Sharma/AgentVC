"""agentvc/protect.py: PreToolUse denies agent writes into `.agentvc/` (plan §14.1, G8).

Two directions (plan §15.2): it fires on writes into the store, including `..`, case and
symlink variants; and it doesn't fire on ordinary work, so it can't pass by refusing everything.
"""

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agentvc.events import Event, Kind
from agentvc.protect import write_into_store


def pre(tmp_path: Path, tool: str, tool_input: dict[str, object], cwd: Path | None = None) -> Event:
    return Event(
        Kind.TOOL_PRE,
        "claude-code",
        "PreToolUse",
        "s1",
        str(cwd or tmp_path),
        tool_name=tool,
        tool_use_id="toolu_1",
        tool_input=tool_input,
    )


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / ".agentvc").mkdir()
    (tmp_path / "src").mkdir()
    return tmp_path


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
@pytest.mark.parametrize(
    "target",
    [
        ".agentvc/ledger.db",
        ".agentvc",
        "src/../.agentvc/config.toml",
        ".AGENTVC/ledger.db" if sys.platform in ("win32", "darwin") else ".agentvc/x",
    ],
)
def test_edit_tools_cannot_write_into_the_store(ws: Path, tool: str, target: str) -> None:
    for path in (target, str(ws / target)):
        reason = write_into_store(pre(ws, tool, {"file_path": path}), ws)
        assert reason is not None and ".agentvc" in reason


def test_notebook_edit_uses_its_own_field(ws: Path) -> None:
    event = pre(ws, "NotebookEdit", {"notebook_path": str(ws / ".agentvc" / "x.ipynb")})
    assert write_into_store(event, ws)


def test_relative_paths_resolve_against_the_event_cwd(ws: Path) -> None:
    event = pre(ws, "Write", {"file_path": "../.agentvc/ledger.db"}, cwd=ws / "src")
    assert write_into_store(event, ws)


@pytest.mark.skipif(sys.platform == "win32" and not hasattr(os, "symlink"), reason="no symlinks")
def test_a_symlink_into_the_store_is_followed(ws: Path) -> None:
    link = ws / "innocent"
    try:
        link.symlink_to(ws / ".agentvc", target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs privileges here")
    assert write_into_store(pre(ws, "Write", {"file_path": str(link / "ledger.db")}), ws)


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_a_junction_into_the_store_is_followed(ws: Path) -> None:
    import subprocess

    link = ws / "innocent"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(ws / ".agentvc")],
        check=True,
        capture_output=True,
    )
    assert write_into_store(pre(ws, "Write", {"file_path": str(link / "ledger.db")}), ws)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf .agentvc",
        "echo x > .agentvc/ledger.db",
        "Remove-Item -Recurse -Force .\\.agentvc",
        "python -c \"open('.agentvc/config.toml','w').write('mode=1')\"",
        "sqlite3 ./.AgentVC/ledger.db 'delete from steps'",
    ],
)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_shell_commands_naming_the_store_are_denied(ws: Path, tool: str, command: str) -> None:
    assert write_into_store(pre(ws, tool, {"command": command}), ws)


# The other direction: ordinary work is never blocked.


@pytest.mark.parametrize(
    "event_input",
    [
        ("Write", {"file_path": "src/app.py"}),
        ("Edit", {"file_path": "notes/agentvc-ideas.md"}),
        ("Write", {"file_path": ".agentvc-backup/x"}),
        ("Write", {"file_path": "docs/.agentvcrc"}),
        ("Read", {"file_path": ".agentvc/ledger.db"}),  # reading is allowed
        ("Bash", {"command": "pytest -q && git status"}),
        ("Bash", {"command": "agentvc history --json"}),
        ("Bash", {"command": "pip install agentvc"}),
        ("PowerShell", {"command": "Get-ChildItem src"}),
        ("Grep", {"pattern": ".agentvc", "path": "src"}),
    ],
)
def test_ordinary_work_is_not_denied(ws: Path, event_input: tuple[str, dict[str, object]]) -> None:
    tool, tool_input = event_input
    assert write_into_store(pre(ws, tool, tool_input), ws) is None


def test_only_pre_tool_events_are_checked(ws: Path) -> None:
    event = replace(pre(ws, "Write", {"file_path": ".agentvc/x"}), kind=Kind.TOOL_POST)
    assert write_into_store(event, ws) is None
