"""`agentvc diff`, `rollback`, `undo`, `redo`, `repair` and what `inspect` shows about snapshots.

Design notes cli.md and restore.md. Every result is printed (G3); --json is pure ASCII; the same
answer comes from a subfolder (ATK-CLI-01).
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from agentsim import Agent

from agentvc import cli, crash


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    (root / "a.txt").write_bytes(b"line 1\nline 2\n")
    (root / "bin.dat").write_bytes(b"\0\1\2")
    (root / "src").mkdir()
    monkeypatch.chdir(root)
    yield root


def run(capsys: pytest.CaptureFixture[str], ws: Path, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv), environ={"AGENTVC_HOME": str(ws.parent / "home")})
    return code, capsys.readouterr().out


def agent_run(ws: Path) -> Agent:
    agent = Agent(ws)
    agent.shell(lambda: (ws / "a.txt").write_bytes(b"line 1\nline TWO\n"))
    (ws / "user.txt").write_bytes(b"made by the user between steps")
    agent.shell(lambda: (ws / "bin.dat").write_bytes(b"\0\7\7"))
    agent.write("src/new.py", b"print('hi')\n")
    return agent


def test_diff_of_a_step_is_a_unified_diff(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    agent = agent_run(ws)
    code, out = run(capsys, ws, "diff", f"{agent.run}:1")
    assert code == 0
    assert "--- a/a.txt\n+++ b/a.txt\n" in out
    assert "-line 2\n+line TWO\n" in out
    assert "bin.dat" not in out and "new.py" not in out  # only step 1
    agent.close()


def test_diff_of_a_run_lists_every_file_and_outside_changes(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = agent_run(ws)
    code, out = run(capsys, ws, "diff", str(agent.run))
    assert code == 0
    assert "Binary files a/bin.dat and b/bin.dat differ" in out
    assert "--- /dev/null\n+++ b/src/new.py\n" in out
    assert "Changed outside the agent before step 2: user.txt" in out
    _, raw = run(capsys, ws, "diff", str(agent.run), "--json")
    assert raw.isascii()
    data = json.loads(raw)
    assert [f["path"] for f in data["files"]] == ["a.txt", "bin.dat", "src/new.py"]
    assert [f["change"] for f in data["files"]] == ["modified", "modified", "added"]
    assert data["outside_changes"] == [{"before_step": 2, "paths": ["user.txt"]}]
    agent.close()


ROLLBACK = """\
Rollback 1 | rollback of run 1 | done
  restored    a.txt
  restored    bin.dat
  deleted     src/new.py
Restored 2 | deleted 1 | already 0 | conflicts 0 | could not undo 0
To undo this rollback: agentvc rollback --undo
"""


def test_rollback_prints_every_file_and_the_totals(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = agent_run(ws)
    code, out = run(capsys, ws, "rollback", str(agent.run))
    assert (code, out) == (0, ROLLBACK)
    assert (ws / "user.txt").exists()  # an outside change: never touched
    code, out = run(capsys, ws, "rollback", "--undo")
    assert code == 0 and out.startswith("Rollback 2 | rollback-undo of rollback 1 | done")
    assert (ws / "src" / "new.py").exists()
    agent.close()


def test_a_conflict_is_shown_with_its_reason_and_exits_1(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = agent_run(ws)
    (ws / "a.txt").write_text("edited after the run\n", encoding="utf-8")
    code, out = run(capsys, ws, "rollback", str(agent.run))
    assert code == 1
    assert "  conflict    a.txt  changed by someone else after step 1 left it; kept\n" in out
    assert "| partial" in out.splitlines()[0]
    assert "use --force" in out
    _, raw = run(capsys, ws, "rollback", "--undo", "--json")
    assert raw.isascii() and json.loads(raw)["kind"] == "rollback-undo"
    agent.close()


def test_undo_redo_and_errors_from_a_subfolder(
    ws: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = Agent(ws)
    agent.shell(lambda: (ws / "a.txt").write_text("turn 1\n", encoding="utf-8"))
    agent.prompt()
    agent.write("src/b.txt", b"turn 2")
    monkeypatch.chdir(ws / "src")
    code, out = run(capsys, ws, "undo")
    assert code == 0 and out.startswith("Rollback 1 | undo of 1:t2 | done")
    assert not (ws / "src" / "b.txt").exists()
    code, out = run(capsys, ws, "redo")
    assert code == 0 and (ws / "src" / "b.txt").read_bytes() == b"turn 2"
    code, out = run(capsys, ws, "rollback", "9")
    assert (code, out) == (1, "agentvc: no run 9\n")
    code, out = run(capsys, ws, "redo")
    assert (code, out) == (1, "agentvc: no undo to reverse\n")
    agent.close()


def test_repair_finishes_an_interrupted_rollback(
    ws: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = agent_run(ws)
    with crash.armed("restore.after_write"), pytest.raises(crash.CrashPointHit):
        run(capsys, ws, "rollback", str(agent.run))
    capsys.readouterr()
    code, out = run(capsys, ws, "inspect", str(agent.run))
    assert "Rollback 1 (rollback of run 1): INTERRUPTED, run `agentvc repair`" in out
    code, out = run(capsys, ws, "repair")
    assert code == 0 and "Rollback 1 | rollback of run 1 | done (recovered)" in out
    assert (ws / "a.txt").read_bytes() == b"line 1\nline 2\n"
    code, out = run(capsys, ws, "repair")
    assert (code, out) == (0, "Nothing to repair.\n")
    agent.close()


def test_inspect_shows_what_was_not_protected(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    agent = agent_run(ws)
    agent.send("0005-PostToolUse-Bash.json", tool_use_id="toolu_no_pre")  # no before snapshot
    code, out = run(capsys, ws, "inspect", str(agent.run))
    assert code == 0
    lines = out.splitlines()
    step2 = next(line for line in lines if line.startswith("  #2 "))
    assert "[outside changes: 1]" in step2
    step4 = next(line for line in lines if line.startswith("  #4 "))
    assert "[not snapshotted: no snapshot before it]" in step4
    assert "Snapshots: 3 of 4 file-changing steps protected" in out
    _, raw = run(capsys, ws, "inspect", str(agent.run), "--json")
    data = json.loads(raw)
    assert [s["phase"] for s in data["steps"][0]["snapshots"]] == ["before", "after"]
    agent.close()
