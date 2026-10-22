"""Attacks on restore (plan §15.3 "Restore / rollback"; docs/adversarial/restore.md).

Must hold: G1, zero bytes of non-agent work changed; partial rollbacks are resumable or
reversible. ATK-RST-01, -05, -06 and the in-process crash points are in tests/test_restore.py.
"""

import errno
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agentsim import Agent, tree

from agentvc import crash
from agentvc.io import IO
from agentvc.restore import CASE_INSENSITIVE
from agentvc.snapshot import Snapshotter


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "ws"
    root.mkdir()
    yield root.resolve()


def results(report: Any) -> dict[str, str]:
    return {e.path: e.result for e in report.files}


def junction(link: Path, target: Path) -> None:
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
    )


def can_symlink(tmp: Path) -> bool:
    try:
        os.symlink("x", tmp / "probe-link")
    except OSError:
        return False
    os.unlink(tmp / "probe-link")
    return True


# --- ATK-RST-02: file deleted then recreated ---------------------------------------------------


def test_a_file_the_agent_deleted_and_the_user_recreated_is_kept(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"original")
    (ws / "b.txt").write_bytes(b"original b")
    agent = Agent(ws)

    def rm() -> None:
        (ws / "a.txt").unlink()
        (ws / "b.txt").unlink()

    agent.shell(rm, "rm a.txt b.txt")
    (ws / "a.txt").write_bytes(b"the user wrote a new a.txt")
    (ws / "b.txt").write_bytes(b"original b")  # recreated exactly as it was
    report = agent.restorer().rollback(str(agent.run))
    assert results(report) == {"a.txt": "conflict", "b.txt": "already"}
    assert (ws / "a.txt").read_bytes() == b"the user wrote a new a.txt"
    agent.close()


# --- ATK-RST-03: file / directory / symlink type changes ---------------------------------------


def test_a_file_the_agent_turned_into_a_symlink_comes_back(ws: Path, tmp_path: Path) -> None:
    if not can_symlink(tmp_path):
        pytest.skip("creating symlinks needs privileges here")
    (ws / "f.txt").write_bytes(b"a real file")

    def to_link() -> None:
        (ws / "f.txt").unlink()
        os.symlink("elsewhere.txt", ws / "f.txt")

    agent = Agent(ws)
    agent.shell(to_link)
    agent.restorer().rollback(str(agent.run))
    assert not (ws / "f.txt").is_symlink()
    assert (ws / "f.txt").read_bytes() == b"a real file"
    agent.close()


# --- ATK-RST-04: restoring through a symlinked directory ---------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_nothing_is_deleted_or_written_through_a_junction(ws: Path, tmp_path: Path) -> None:
    (ws / "src").mkdir()
    (ws / "src" / "g.txt").write_bytes(b"g original")
    agent = Agent(ws)
    agent.write("src/f.txt", b"agent created f")
    agent.shell(lambda: (ws / "src" / "g.txt").unlink(), "rm src/g.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_bytes(b"agent created f")  # same bytes: compare-and-swap would pass
    for p in (ws / "src").iterdir():
        p.unlink()
    (ws / "src").rmdir()
    junction(ws / "src", outside)
    report = agent.restorer().rollback(str(agent.run))
    assert results(report) == {"src/f.txt": "could-not", "src/g.txt": "could-not"}
    assert all("link" in e.detail for e in report.files)
    assert sorted(p.name for p in outside.iterdir()) == ["f.txt"]  # not deleted, nothing added
    agent.close()


def test_a_hard_link_to_a_file_outside_is_never_written_through(ws: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside, version 1")
    os.link(outside, ws / "linked.txt")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "linked.txt").write_bytes(b"agent wrote through the link"))
    before_rollback = outside.read_bytes()
    agent.restorer().rollback(str(agent.run))
    assert (ws / "linked.txt").read_bytes() == b"outside, version 1"
    assert outside.read_bytes() == before_rollback  # the rollback replaced the name, not the inode
    agent.close()


# --- changes racing the rollback ---------------------------------------------------------------


def edit_after_saving(monkeypatch: pytest.MonkeyPatch, path: Path, data: bytes) -> None:
    """Someone writes ``path`` right after the rollback saved the current state."""
    original = Snapshotter.keys

    def keys_then_edit(self: Snapshotter, keys: Any) -> Any:
        result = original(self, keys)
        path.write_bytes(data)
        return result

    monkeypatch.setattr(Snapshotter, "keys", keys_then_edit)


@pytest.mark.parametrize("name", ["edited.txt", "made.txt"])
def test_a_file_changed_while_the_rollback_runs_is_kept(
    ws: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    (ws / "edited.txt").write_bytes(b"original")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "edited.txt").write_bytes(b"agent"))
    agent.write("made.txt", b"created by the agent")
    edit_after_saving(monkeypatch, ws / name, b"typed by the user just now")
    report = agent.restorer().rollback(str(agent.run))
    assert results(report)[name] == "conflict"
    assert (ws / name).read_bytes() == b"typed by the user just now"
    agent.close()


# --- ATK-RST-07: crash halfway, by a real kill -------------------------------------------------


def test_a_killed_rollback_is_finished_by_repair(ws: Path) -> None:
    for n in range(4):
        (ws / f"f{n}.txt").write_bytes(b"original")
    agent = Agent(ws)
    agent.shell(lambda: [(ws / f"f{n}.txt").write_bytes(b"agent") for n in range(4)])
    run = agent.run
    agent.close()
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from agentvc import hook\n"
        "from agentvc.clock import SystemClock\n"
        "from agentvc.config import Config\n"
        "from agentvc.io import IO\n"
        "from agentvc.restore import Restorer\n"
        "ws = Path(sys.argv[1])\n"
        "ctx = hook.HookContext(io=IO(), clock=SystemClock(), workspace=ws, config=Config(),\n"
        "                       anchor_dir=ws.parent / 'home' / 'anchors')\n"
        "Restorer(ctx.io, ctx.clock, ctx.ledger(), ctx.snapshotter()).rollback(sys.argv[2])\n"
    )
    env = {**os.environ, "AGENTVC_CRASH_AT": "restore.after_write"}
    done = subprocess.run([sys.executable, "-c", code, str(ws), str(run)], env=env, timeout=60)
    assert done.returncode == crash.EXIT_CODE
    assert sorted(tree(ws).values()).count(b"original") == 1  # killed after the first write
    fresh = Agent(ws)
    [report] = fresh.restorer().repair()
    assert report.status == "done"
    [row] = fresh.ctx.ledger().rows("rollbacks")
    assert json.loads(row["flags"]) == ["recovered"]
    assert set(tree(ws).values()) == {b"original"}
    assert tree(ws).keys() == {f"f{n}.txt" for n in range(4)}  # no temp files left
    fresh.close()


def test_a_new_rollback_finishes_an_interrupted_one_first(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"a0")
    (ws / "b.txt").write_bytes(b"b0")
    agent = Agent(ws)
    agent.shell(lambda: ((ws / "a.txt").write_bytes(b"a1"), (ws / "b.txt").write_bytes(b"b1")))
    with crash.armed("restore.after_write"), pytest.raises(crash.CrashPointHit):
        agent.restorer().rollback(str(agent.run))
    report = agent.restorer().rollback(str(agent.run))
    [repaired] = report.repaired
    assert repaired.status == "done"
    assert tree(ws) == {"a.txt": b"a0", "b.txt": b"b0"}
    [row] = agent.ctx.ledger().rows("rollbacks")
    assert row["status"] == "done" and "recovered" in row["flags"]
    agent.close()


# --- ATK-RST-08 / -09: locked and protected files ----------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="files held open block replacement on Windows")
def test_a_file_held_open_by_an_editor_is_reported_and_finished_later(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"a0")
    (ws / "b.txt").write_bytes(b"b0")
    agent = Agent(ws)
    agent.shell(lambda: ((ws / "a.txt").write_bytes(b"a1"), (ws / "b.txt").write_bytes(b"b1")))
    with open(ws / "a.txt", "rb"):  # an editor holding it (no delete sharing)
        report = agent.restorer().rollback(str(agent.run))
    assert report.status == "partial"
    assert results(report) == {"a.txt": "could-not", "b.txt": "restored"}
    assert "in use" in report.files[0].detail
    assert (ws / "a.txt").read_bytes() == b"a1"
    again = agent.restorer().rollback(str(agent.run))
    assert results(again) == {"a.txt": "restored", "b.txt": "already"}
    assert tree(ws) == {"a.txt": b"a0", "b.txt": b"b0"}
    agent.close()


def test_permission_denied_is_reported_never_hidden(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"a0")
    io = IO()
    agent = Agent(ws, io=io)
    agent.shell(lambda: (ws / "a.txt").write_bytes(b"a1"))
    io.faults.fail("replace", PermissionError(errno.EACCES, "Permission denied"), match="a.txt")
    report = agent.restorer().rollback(str(agent.run))
    assert report.status == "partial" and results(report) == {"a.txt": "could-not"}
    assert (ws / "a.txt").read_bytes() == b"a1"
    assert [p.name for p in ws.iterdir() if p.name.endswith(".tmp")] == []
    agent.close()


# --- crafted trees (a tampered store) ----------------------------------------------------------


def crafted_step(agent: Agent, before: dict[str, bytes], after: dict[str, bytes]) -> None:
    """Newer before/after snapshot rows for the run's last step, with trees built by hand."""
    objects = agent.ctx.snapshotter().objects
    lg = agent.ctx.ledger()
    step = lg.rows("steps")[-1]
    with lg.transaction(timeout=5) as tx:
        for phase, files in (("before", before), ("after", after)):
            flat = {p: ("100644", objects.write_blob(d)) for p, d in files.items()}
            tx.insert(
                "snapshots",
                f"snapshot.{phase}",
                {
                    "run_id": step["run_id"],
                    "step_id": step["id"],
                    "phase": phase,
                    "status": "ok",
                    "tree": objects.write_flat(flat),
                },
            )


def test_paths_from_a_tampered_tree_never_escape_the_workspace(ws: Path, tmp_path: Path) -> None:
    agent = Agent(ws)
    agent.shell(lambda: None)
    evil = {
        "../escape.txt": b"x",
        "x//y.txt": b"x",  # an empty name in the tree
        "a\\b.txt": b"x",  # on Windows this would alias a/b.txt
        ".agentvc/ledger.db": b"x",
        ".git/config": b"x",
        "ok/fine.txt": b"restorable",
    }
    if sys.platform == "win32":
        evil.update({"CON": b"x", "trailing.": b"x", "ads.txt:stream": b"x"})
    crafted_step(agent, before=evil, after={})
    report = agent.restorer().rollback(str(agent.run))
    outcome = results(report)
    assert outcome.pop("ok/fine.txt") == "restored"
    assert set(outcome.values()) == {"could-not"}, outcome
    assert not (tmp_path / "escape.txt").exists()
    assert (ws / "ok" / "fine.txt").read_bytes() == b"restorable"
    # Unsafe paths are never even read: the pre-rollback snapshot covers only the safe one.
    [pre] = agent.ctx.ledger().rows("snapshots", "where phase = 'pre-rollback'")
    assert json.loads(pre["scope"]) == ["ok/fine.txt"]
    agent.close()


@pytest.mark.skipif(not CASE_INSENSITIVE, reason="case collisions need a case-insensitive system")
def test_two_paths_differing_only_in_case_are_both_skipped(ws: Path) -> None:
    agent = Agent(ws)
    agent.shell(lambda: None)
    crafted_step(agent, before={"Readme.md": b"one", "README.md": b"two"}, after={})
    report = agent.restorer().rollback(str(agent.run))
    assert results(report) == {"README.md": "conflict", "Readme.md": "conflict"}
    assert list(ws.iterdir()) == [ws / ".agentvc"]
    agent.close()


def test_damaged_saved_content_is_reported_not_written(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"original")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "a.txt").write_bytes(b"agent"))
    objects = agent.ctx.snapshotter().objects
    sha = objects.hash_blob(b"original")
    obj = objects.objects / sha[:2] / sha[2:]
    obj.write_bytes(obj.read_bytes()[:-3])  # truncated by a crash or an attacker
    report = agent.restorer().rollback(str(agent.run))
    assert results(report) == {"a.txt": "could-not"} and "damaged" in report.files[0].detail
    assert (ws / "a.txt").read_bytes() == b"agent"
    agent.close()
