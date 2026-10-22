"""agentvc/restore.py: rollback, undo / redo, --force, repair (design note restore.md, G1).

The rule under test everywhere: a rollback never changes a byte the agent didn't write. Attacks
from plan §15.3 "Restore / rollback" are in tests/test_restore_attacks.py.
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from agentsim import Agent, tree

from agentvc import crash, restore
from agentvc.restore import RestoreError


@pytest.fixture
def ws(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "ws"
    root.mkdir()
    yield root.resolve()


def results(report: restore.Report) -> dict[str, str]:
    return {e.path: e.result for e in report.files}


def test_a_run_is_rolled_back_edits_deletions_and_creations(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"original a")
    (ws / "b.txt").write_bytes(b"original b")
    (ws / "keep.txt").write_bytes(b"not touched")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "a.txt").write_bytes(b"agent's a"), "sed -i ...")
    agent.shell(lambda: (ws / "b.txt").unlink(), "rm b.txt")
    agent.write("new/c.txt", b"agent created this")
    report = agent.restorer().rollback(str(agent.run))
    assert report.status == "done" and report.rollback_id is not None
    assert results(report) == {"a.txt": "restored", "b.txt": "restored", "new/c.txt": "deleted"}
    assert tree(ws) == {"a.txt": b"original a", "b.txt": b"original b", "keep.txt": b"not touched"}
    assert not (ws / "new").exists()  # the folder it created for c.txt is gone too
    agent.close()


def test_a_file_the_user_changed_after_the_run_is_a_conflict_and_kept(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"v1")
    (ws / "d.txt").write_bytes(b"d1")
    agent = Agent(ws)
    agent.shell(lambda: ((ws / "a.txt").write_bytes(b"agent"), (ws / "d.txt").write_bytes(b"d2")))
    (ws / "a.txt").write_bytes(b"the user's own edit")
    report = agent.restorer().rollback(str(agent.run))
    assert results(report) == {"a.txt": "conflict", "d.txt": "restored"}
    assert report.status == "partial"
    assert (ws / "a.txt").read_bytes() == b"the user's own edit"
    assert (ws / "d.txt").read_bytes() == b"d1"
    agent.close()


def test_a_change_made_between_two_agent_steps_is_never_rolled_back(ws: Path) -> None:
    (ws / "f.txt").write_bytes(b"A")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "f.txt").write_bytes(b"B (agent)"))
    (ws / "f.txt").write_bytes(b"C (user, between steps)")
    agent.shell(lambda: (ws / "f.txt").write_bytes(b"D (agent)"))
    report = agent.restorer().rollback(str(agent.run))
    assert results(report) == {"f.txt": "partly"}
    assert (ws / "f.txt").read_bytes() == b"C (user, between steps)"
    agent.close()


def test_rolling_back_twice_finds_everything_already_restored(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"one")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "a.txt").write_bytes(b"two"))
    agent.restorer().rollback(str(agent.run))
    again = agent.restorer().rollback(str(agent.run))
    assert again.status == "nothing" and again.rollback_id is None
    assert results(again) == {"a.txt": "already"}
    agent.close()


def test_rollback_then_rollback_undo_changes_nothing(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"one")
    (ws / "gone.txt").write_bytes(b"deleted by the agent")
    agent = Agent(ws)

    def edit_and_delete() -> None:
        (ws / "a.txt").write_bytes(b"two")
        (ws / "gone.txt").unlink()

    agent.shell(edit_and_delete)
    agent.write("made.txt", b"created")
    (ws / "user.txt").write_bytes(b"the user's")
    before = tree(ws)
    agent.restorer().rollback(str(agent.run))
    assert tree(ws) != before
    undone = agent.restorer().rollback_undo()
    assert undone.kind == "rollback-undo" and undone.status == "done"
    assert tree(ws) == before
    with pytest.raises(RestoreError):
        agent.restorer().rollback_undo()  # nothing left to reverse
    agent.close()


def test_force_overwrites_a_conflict_but_saves_it_first(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"original")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "a.txt").write_bytes(b"agent"))
    (ws / "a.txt").write_bytes(b"user's later edit")
    report = agent.restorer().rollback(str(agent.run), force=True)
    assert results(report) == {"a.txt": "forced"}
    assert (ws / "a.txt").read_bytes() == b"original"
    agent.restorer().rollback_undo()
    assert (ws / "a.txt").read_bytes() == b"user's later edit"  # G1: even --force is undoable
    agent.close()


def test_step_and_turn_targets_roll_back_from_that_point_on(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"a0")
    (ws / "b.txt").write_bytes(b"b0")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "a.txt").write_bytes(b"a1"))  # turn 1, step 1
    agent.prompt()
    agent.shell(lambda: (ws / "b.txt").write_bytes(b"b1"))  # turn 2, step 2
    r = agent.restorer()
    assert results(r.rollback(f"{agent.run}:t2")) == {"b.txt": "restored"}
    assert tree(ws) == {"a.txt": b"a1", "b.txt": b"b0"}
    r.rollback_undo()
    assert results(r.rollback(f"{agent.run}:2")) == {"b.txt": "restored"}
    assert results(r.rollback(f"{agent.run}:1")) == {"a.txt": "restored", "b.txt": "already"}
    with pytest.raises(RestoreError, match="no step 9"):
        r.rollback(f"{agent.run}:9")
    with pytest.raises(RestoreError, match="no turn 7"):
        r.rollback(f"{agent.run}:t7")
    with pytest.raises(RestoreError, match="no run 42"):
        r.rollback("42")
    agent.close()


def test_undo_and_redo_walk_back_and_forth_through_turns(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"a0")
    (ws / "b.txt").write_bytes(b"b0")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "a.txt").write_bytes(b"a1"))
    agent.prompt()
    agent.shell(lambda: (ws / "b.txt").write_bytes(b"b1"))
    r = agent.restorer()
    assert r.undo().target == f"{agent.run}:t2"
    assert tree(ws) == {"a.txt": b"a1", "b.txt": b"b0"}
    assert r.undo().target == f"{agent.run}:t1"
    assert tree(ws) == {"a.txt": b"a0", "b.txt": b"b0"}
    with pytest.raises(RestoreError, match="nothing to undo"):
        r.undo()
    r.redo()
    assert tree(ws) == {"a.txt": b"a1", "b.txt": b"b0"}
    r.redo()
    assert tree(ws) == {"a.txt": b"a1", "b.txt": b"b1"}
    with pytest.raises(RestoreError, match="no undo"):
        r.redo()
    agent.close()


def test_a_run_with_a_tool_still_running_is_not_rolled_back(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"one")
    agent = Agent(ws)
    agent.shell(lambda: (ws / "a.txt").write_bytes(b"two"))
    agent.pre_shell()  # running now
    with pytest.raises(RestoreError, match="running a tool"):
        agent.restorer().rollback(str(agent.run))
    assert (ws / "a.txt").read_bytes() == b"two"
    report = agent.restorer().rollback(str(agent.run), force=True)
    assert (ws / "a.txt").read_bytes() == b"one"
    assert [seq for seq, _ in report.uncaptured] == [2]
    agent.close()


def test_steps_without_snapshots_are_reported_not_ignored(ws: Path) -> None:
    (ws / "a.txt").write_bytes(b"one")
    agent = Agent(ws)
    agent.send("0005-PostToolUse-Bash.json", tool_use_id="toolu_lost_pre")  # Pre never arrived
    report = agent.restorer().rollback(str(agent.run))
    assert report.status == "nothing"
    [(seq, why)] = report.uncaptured
    assert seq == 1 and "no snapshot before it" in why
    agent.close()


def test_a_file_replaced_by_a_folder_and_back_rolls_back(ws: Path) -> None:
    (ws / "x").write_bytes(b"x was a file")
    (ws / "y").mkdir()
    (ws / "y" / "f.txt").write_bytes(b"y was a folder")

    def swap() -> None:
        (ws / "x").unlink()
        (ws / "x").mkdir()
        (ws / "x" / "inner.txt").write_bytes(b"agent")
        shutil.rmtree(ws / "y")
        (ws / "y").write_bytes(b"agent made y a file")

    agent = Agent(ws)
    agent.shell(swap)
    report = agent.restorer().rollback(str(agent.run))
    assert report.status == "done", [(e.path, e.result, e.detail) for e in report.files]
    assert tree(ws) == {"x": b"x was a file", "y/f.txt": b"y was a folder"}
    agent.close()


def test_a_folder_holding_user_files_is_never_removed(ws: Path) -> None:
    (ws / "x").write_bytes(b"x was a file")

    def to_folder() -> None:
        (ws / "x").unlink()
        (ws / "x").mkdir()
        (ws / "x" / "inner.txt").write_bytes(b"agent")

    agent = Agent(ws)
    agent.shell(to_folder)
    (ws / "x" / "users.txt").write_bytes(b"the user's file")
    report = agent.restorer().rollback(str(agent.run))
    assert results(report) == {"x": "conflict", "x/inner.txt": "deleted"}
    assert (ws / "x" / "users.txt").read_bytes() == b"the user's file"
    agent.close()


@pytest.mark.parametrize("point", sorted(p for p in crash.declared() if p.startswith("restore.")))
def test_a_crash_at_any_point_is_repaired_without_touching_user_work(ws: Path, point: str) -> None:
    for name in ("a", "b", "c"):
        (ws / f"{name}.txt").write_bytes(f"{name} original".encode())
    agent = Agent(ws)
    agent.shell(
        lambda: [(ws / f"{n}.txt").write_bytes(f"{n} agent".encode()) for n in ("a", "b", "c")]
    )
    agent.write("made.txt", b"created by the agent")
    (ws / "user.txt").write_bytes(b"user's work")
    expected = {f"{n}.txt": f"{n} original".encode() for n in ("a", "b", "c")}
    expected["user.txt"] = b"user's work"
    with crash.armed(point), pytest.raises(crash.CrashPointHit):
        agent.restorer().rollback(str(agent.run))
    # Every file is the agent's version or the restored one, never a third state.
    for name, data in tree(ws).items():
        if name.startswith("."):
            continue
        assert data in (expected.get(name), f"{name[0]} agent".encode(), b"created by the agent")
    assert (ws / "user.txt").read_bytes() == b"user's work"
    repaired = agent.restorer().repair()
    if point == "restore.after_pre_snapshot":
        assert repaired == []  # nothing was written or recorded yet
        agent.restorer().rollback(str(agent.run))
    else:
        [report] = repaired
        assert report.status == "done"
    assert tree(ws) == expected  # no stray temp files either
    agent.close()
