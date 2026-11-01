"""`agentvc history`, `inspect`, `hooks-config` (design note cli.md): golden output, --json, gaps
and ledger problems always visible (FM-CLI-01), same answer from a subfolder (plan §15.3 CLI).
"""

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentvc import cli
from agentvc.adapters import claude_code as cc
from agentvc.clock import FakeClock
from agentvc.events import Event
from agentvc.io import IO
from agentvc.ledger import Ledger
from agentvc.recorder import CHECK_SESSION_PREFIX, Recorder
from agentvc.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "hosts" / "claude-code" / "2.1.302"


def fixture(name: str, ws: Path) -> Event:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["payload"]
    payload["cwd"] = str(ws)
    event = cc.parse(payload, record_prompts=False)
    assert isinstance(event, Event)
    return event


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    store = Store(workspace)
    store.ensure(IO())
    clock = FakeClock(datetime(2026, 10, 22, 9, 30, tzinfo=UTC))
    lg = Ledger.open(
        IO(),
        store.ledger,
        workspace=workspace,
        anchor_dir=tmp_path / "home" / "anchors",
        clock=clock,
    )
    rec = Recorder(lg, workspace, clock)
    for name in ("0002-SessionStart.json", "0003-UserPromptSubmit.json"):
        rec.apply(fixture(name, workspace), timeout=5)
    clock.advance(2)
    rec.apply(fixture("0004-PreToolUse-Bash.json", workspace), timeout=5)
    clock.advance(1)
    rec.apply(fixture("0005-PostToolUse-Bash.json", workspace), timeout=5)
    clock.advance(1)
    write = fixture("0038-PreToolUse-Write.json", workspace)
    rec.apply(write, timeout=5)  # never gets a Post
    clock.advance(1)
    rec.apply(fixture("0076-PostToolUseFailure-Bash.json", workspace), timeout=5)  # no Pre
    rec.apply(fixture("0008-Stop.json", workspace), timeout=5)
    check = replace(
        fixture("0002-SessionStart.json", workspace), session_id=CHECK_SESSION_PREFIX + "x"
    )
    rec.apply(check, timeout=5)
    lg.close()
    monkeypatch.chdir(workspace)
    return workspace


def run(capsys: pytest.CaptureFixture[str], tmp_path: Path, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv), environ={"AGENTVC_HOME": str(tmp_path / "home")})
    return code, capsys.readouterr().out


HISTORY = """\
Run  Agent        Started (UTC)        Status  Turns  Steps  Gaps
  1  claude-code  2026-10-22 09:30:00  active      1      3     2

Ledger: ok
"""


def test_history_golden(ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, tmp_path, "history")
    assert code == 0
    assert out == HISTORY


def test_history_all_shows_check_runs(
    ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out = run(capsys, tmp_path, "history", "--all")
    assert "  2  claude-code" in out and "check" in out


def test_history_json(ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, tmp_path, "history", "--json")
    data = json.loads(out)
    assert code == 0
    assert [r["id"] for r in data["runs"]] == [1]
    assert data["runs"][0]["gaps"] == 2
    assert data["verdict"]["status"] == "ok"


INSPECT = (
    "Run 1 | claude-code | started 2026-10-22 09:30:00 | active\n"
    "Turn 1\n"
    "  #1  09:30:02  Bash   shell  echo hello                          ok     810 ms\n"
    "  #2  09:30:04  Write  edit   ...agentvc-capture-2.1.302\\new.txt"
    "  GAP: no post event (the tool may have run unrecorded)\n"
    "  #3  09:30:05  Bash   shell  exit 3                              error  742 ms"
    "  GAP: no pre event (state before it unknown)\n"
    "\n"
    "Steps: 3 | recorded 1 | gaps 2 | denied 0\n"
    "Ledger: ok\n"
)


def test_inspect_golden(ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, tmp_path, "inspect", "1")
    assert code == 0
    assert out == INSPECT


def test_inspect_json(ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(capsys, tmp_path, "inspect", "1", "--json")
    data = json.loads(out)
    assert data["run"]["id"] == 1
    assert [s["status"] for s in data["steps"]] == ["RECORDED", "GAP", "GAP"]
    assert data["steps"][1]["gap_reason"] == "post-missing"
    assert data["verdict"]["status"] == "ok"


def test_inspect_latest_and_unknown_runs(
    ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(capsys, tmp_path, "inspect", "latest")
    assert code == 0 and out.startswith("Run 1 ")
    code, out = run(capsys, tmp_path, "inspect", "99")
    assert code == 1 and "no run 99" in out


def test_tampering_is_shown_in_text_and_json(
    ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = sqlite3.connect(ws / ".agentvc" / "ledger.db", isolation_level=None)
    db.execute("update steps set status = 'RECORDED' where id = 2")
    db.close()
    for argv in (("history",), ("inspect", "1")):
        code, out = run(capsys, tmp_path, *argv)
        assert code == 1
        assert "Ledger: TAMPERED" in out and "steps row 2 differs" in out
    _, out = run(capsys, tmp_path, "history", "--json")
    assert json.loads(out)["verdict"]["status"] == "tampered"


def test_same_answer_from_a_subfolder(
    ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, top = run(capsys, tmp_path, "history")
    monkeypatch.chdir(ws / "src")
    _, sub = run(capsys, tmp_path, "history")
    assert sub == top


def test_blind_spots_are_counted_in_history(
    ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (ws / ".agentvc" / "blindspots.jsonl").write_text('{"reason": "not-json"}\n', encoding="utf-8")
    _, out = run(capsys, tmp_path, "history")
    assert "Blind spots: 1 hook payload couldn't be read (.agentvc/blindspots.jsonl)" in out


def test_no_store_is_reported_plainly_and_not_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    code, out = run(capsys, tmp_path, "history")
    assert code == 0 and "has not recorded anything" in out
    assert not (empty / ".agentvc").exists()


def test_hooks_config_prints_generated_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    code, out = run(capsys, tmp_path, "hooks-config", "--agent-version", "2.1.302")
    assert code == 0
    settings = json.loads(out)
    state = json.loads((proj / ".agentvc" / "daemon.json").read_text(encoding="utf-8"))
    [pre] = settings["hooks"]["PreToolUse"][0]["hooks"]
    assert pre["url"] == f"http://127.0.0.1:{state['port']}/hook"
    assert pre["headers"]["X-AgentVC-Token"] == state["token"]
    code, _ = run(capsys, tmp_path, "hooks-config", "--agent-version", "1.0.0")
    assert code == 1


def test_a_newer_or_corrupt_ledger_is_reported_not_a_traceback(
    ws: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = sqlite3.connect(ws / ".agentvc" / "ledger.db", isolation_level=None)
    db.execute("update schema_version set version = 99")
    db.close()
    code, out = run(capsys, tmp_path, "history")
    assert code == 1 and "newer AgentVC" in out
    (ws / ".agentvc" / "ledger.db").write_bytes(b"garbage" * 1000)
    for suffix in ("-wal", "-shm"):
        (ws / ".agentvc" / f"ledger.db{suffix}").unlink(missing_ok=True)
    code, out = run(capsys, tmp_path, "inspect", "1")
    assert code == 1 and "not a readable ledger" in out


def test_piped_output_never_crashes_on_unusual_characters(tmp_path: Path) -> None:
    # Under -I, Python ignores PYTHONIOENCODING; piped output uses the console code page
    # (cp1252 on this Windows machine). Found in the U-20 run: separators came out garbled,
    # and a command with characters outside that code page would crash `inspect`.
    import os
    import subprocess
    import sys

    workspace = tmp_path / "uni"
    workspace.mkdir()
    store = Store(workspace)
    store.ensure(IO())
    clock = FakeClock(datetime(2026, 10, 22, 9, 30, tzinfo=UTC))
    lg = Ledger.open(
        IO(),
        store.ledger,
        workspace=workspace,
        anchor_dir=tmp_path / "home" / "anchors",
        clock=clock,
    )
    rec = Recorder(lg, workspace, clock)
    pre = fixture("0004-PreToolUse-Bash.json", workspace)
    rec.apply(replace(pre, tool_input={"command": "echo 漢字 → done"}), timeout=5)
    lg.close()
    env = {**os.environ, "AGENTVC_HOME": str(tmp_path / "home")}

    def agentvc(*argv: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-I", "-m", "agentvc", *argv],
            cwd=workspace, env=env, capture_output=True, timeout=60,
        )  # fmt: skip

    text = agentvc("inspect", "1")
    assert text.returncode == 0, text.stderr
    assert b"Run 1 | claude-code | started" in text.stdout
    as_json = agentvc("inspect", "1", "--json")
    assert as_json.returncode == 0
    assert max(as_json.stdout) < 128  # pure ASCII: any consumer decodes it exactly
    data = json.loads(as_json.stdout)
    assert data["steps"][0]["input"]["command"] == "echo 漢字 → done"
