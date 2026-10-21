"""`agentvc check` (plan §6.1, design note cli.md): a realistic fake tool call through the real
hook path, and the failure path. FM-CLI-02: it must fail when recording is broken.
"""

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentvc import daemon
from agentvc.io import IO
from agentvc.store import Store


def run_cli(ws: Path, tmp_path: Path, *argv: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "agentvc", *argv],
        cwd=ws,
        env={**os.environ, "AGENTVC_HOME": str(tmp_path / "home"), **env},
        capture_output=True,
        text=True,
        timeout=120,
    )


def stop_daemon(ws: Path) -> None:
    state = daemon.load_state(IO(), Store(ws))
    if state is not None:
        with contextlib.suppress(OSError):
            daemon.call(state, "POST", "/shutdown")


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Path]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    yield workspace
    stop_daemon(workspace)


@pytest.mark.slow
def test_check_passes_and_leaves_no_trace_in_history_or_notices(ws: Path, tmp_path: Path) -> None:
    done = run_cli(ws, tmp_path, "check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "All checks passed" in done.stdout
    assert "round trip" in done.stdout

    done = run_cli(ws, tmp_path, "check", "--json")
    data = json.loads(done.stdout)
    assert done.returncode == 0
    assert data["checks"] and all(c["ok"] for c in data["checks"])
    assert {c["name"] for c in data["checks"]} >= {"liveness", "record", "gap", "ledger"}
    assert data["round_trip_ms"] and all(ms > 0 for ms in data["round_trip_ms"])

    history = run_cli(ws, tmp_path, "history", "--json")
    assert json.loads(history.stdout)["runs"] == []  # check runs are hidden
    everything = run_cli(ws, tmp_path, "history", "--all", "--json")
    assert len(json.loads(everything.stdout)["runs"]) == 2

    # The deliberate gap from the check is not reported to the user as a missed action.
    prompt = json.dumps(
        {
            "session_id": "11111111-1111-1111-1111-111111111111",
            "cwd": str(ws),
            "hook_event_name": "UserPromptSubmit",
            "prompt": "hi",
        }
    )
    done = subprocess.run(
        [sys.executable, "-I", "-m", "agentvc", "hook", "--ensure-daemon"],
        input=prompt,
        cwd=ws,
        env={**os.environ, "AGENTVC_HOME": str(tmp_path / "home"), "CLAUDE_PROJECT_DIR": str(ws)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0
    assert "not fully recorded" not in done.stdout


@pytest.mark.slow
def test_check_fails_when_recording_is_broken(ws: Path, tmp_path: Path) -> None:
    # Every ledger write kills the process before it commits: nothing can be recorded.
    done = run_cli(ws, tmp_path, "check", AGENTVC_CRASH_AT="ledger.before_commit")
    assert done.returncode == 1, done.stdout
    assert "FAIL" in done.stdout
    assert "All checks passed" not in done.stdout
