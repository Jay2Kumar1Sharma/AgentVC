"""`python -I -m agentvc hook`: the per-call command hook, end to end in a process (ADR-0008)."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures" / "hosts" / "claude-code" / "2.1.302"


def body(ws: Path, name: str, **changes: Any) -> bytes:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["payload"]
    payload.update(cwd=str(ws), **changes)
    return json.dumps(payload).encode()


def run_hook(
    ws: Path, data: bytes, tmp_path: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "AGENTVC_HOME": str(tmp_path / "home"), "CLAUDE_PROJECT_DIR": str(ws)}
    return subprocess.run(
        [sys.executable, "-I", "-m", "agentvc", "hook", *args],
        input=data,
        capture_output=True,
        env=env,
        cwd=cwd or ws,
        timeout=60,
    )


def test_per_call_hook_records_into_the_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in (
        "0003-UserPromptSubmit.json",
        "0004-PreToolUse-Bash.json",
        "0005-PostToolUse-Bash.json",
    ):
        done = run_hook(ws, body(ws, name), tmp_path)
        assert (done.returncode, done.stdout, done.stderr) == (0, b"", b"")
    import sqlite3

    db = sqlite3.connect(ws / ".agentvc" / "ledger.db")
    assert db.execute("select status from steps").fetchall() == [("RECORDED",)]
    db.close()


def test_per_call_hook_denies_writes_into_the_store_with_exit_2(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    target = str(ws / ".agentvc" / "config.toml")
    data = body(ws, "0038-PreToolUse-Write.json", tool_input={"file_path": target, "content": "x"})
    done = run_hook(ws, data, tmp_path)
    assert done.returncode == 2
    assert b".agentvc" in done.stderr


def test_per_call_hook_survives_garbage_input(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    done = run_hook(ws, b"\x00\xff not json", tmp_path)
    assert done.returncode == 0
    assert (ws / ".agentvc" / "blindspots.jsonl").exists()


def test_a_planted_agentvc_package_in_the_project_is_not_run(tmp_path: Path) -> None:
    # ADR-0008: with -I, repo content can't shadow AgentVC's modules.
    ws = tmp_path / "ws"
    planted = ws / "agentvc"
    planted.mkdir(parents=True)
    (planted / "__init__.py").write_text("", encoding="utf-8")
    (planted / "__main__.py").write_text(
        "import sys; print('PLANTED'); sys.exit(0)", encoding="utf-8"
    )
    done = run_hook(ws, body(ws, "0003-UserPromptSubmit.json"), tmp_path)
    assert b"PLANTED" not in done.stdout
    assert (ws / ".agentvc" / "ledger.db").exists()
