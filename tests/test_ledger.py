"""agentvc/ledger.py: schema versioning (ADR-0007), hash chain + anchor, locking (design ledger.md).

Attacks covered (plan §15.3): ATK-LED-01 editing or deleting rows, ATK-LED-02 truncating the
newest entries, ATK-LED-03 corrupting the database, ATK-LED-05 two writers at once,
ATK-LED-06 clock changes.
"""

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentvc import crash, ledger
from agentvc.clock import FakeClock
from agentvc.io import IO, LockTimeout
from agentvc.ledger import Ledger, LedgerCorrupt, LedgerTooNew


def open_ledger(tmp_path: Path, clock: FakeClock | None = None) -> Ledger:
    return Ledger.open(
        IO(),
        tmp_path / "ws" / ".agentvc" / "ledger.db",
        workspace=tmp_path / "ws",
        anchor_dir=tmp_path / "home" / "anchors",
        clock=clock or FakeClock(datetime(2026, 10, 22, tzinfo=UTC)),
    )


def add_run(lg: Ledger, n_steps: int = 2) -> int:
    with lg.transaction(timeout=5) as tx:
        run = tx.insert("runs", "run.start", {"agent": "claude-code", "status": "active"})
        for i in range(n_steps):
            tx.insert("steps", "step.open", {"run_id": run, "seq": i + 1, "status": "PENDING"})
    return run


def raw(tmp_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(tmp_path / "ws" / ".agentvc" / "ledger.db", isolation_level=None)


# --- schema versioning (ADR-0007) --------------------------------------------------------------


def test_new_ledger_has_schema_version_and_migration_history(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    lg.close()
    db = raw(tmp_path)
    assert db.execute("select version from schema_version").fetchall() == [(1,)]
    [(version, applied_at, agentvc_version)] = db.execute("select * from migrations").fetchall()
    assert version == 1 and applied_at and agentvc_version
    db.close()


def test_a_newer_schema_is_refused_and_left_untouched(tmp_path: Path) -> None:
    open_ledger(tmp_path).close()
    db = raw(tmp_path)
    db.execute("update schema_version set version = 99")
    db.close()
    before = (tmp_path / "ws" / ".agentvc" / "ledger.db").read_bytes()
    with pytest.raises(LedgerTooNew) as info:
        open_ledger(tmp_path)
    assert "99" in str(info.value)
    assert (tmp_path / "ws" / ".agentvc" / "ledger.db").read_bytes() == before


def test_missing_version_row_is_corruption_not_a_guess(tmp_path: Path) -> None:
    open_ledger(tmp_path).close()
    db = raw(tmp_path)
    db.execute("delete from schema_version")
    db.close()
    with pytest.raises(LedgerCorrupt):
        open_ledger(tmp_path)


def test_a_garbage_file_is_corruption(tmp_path: Path) -> None:
    path = tmp_path / "ws" / ".agentvc" / "ledger.db"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(LedgerCorrupt):
        open_ledger(tmp_path)


def test_migration_backs_up_first_and_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_run(open_ledger(tmp_path))
    ran: list[int] = []
    monkeypatch.setattr(ledger, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(ledger, "MIGRATIONS", {2: lambda db: ran.append(2)})
    lg = open_ledger(tmp_path)
    lg.close()
    assert ran == [2]
    assert (tmp_path / "ws" / ".agentvc" / "ledger.db.bak-v1").exists()
    db = raw(tmp_path)
    assert db.execute("select version from schema_version").fetchall() == [(2,)]
    assert [v for (v,) in db.execute("select version from migrations order by version")] == [1, 2]
    db.close()


# --- writes and the hash chain ----------------------------------------------------------------


def test_writes_produce_rows_and_a_verified_chain(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    run = add_run(lg)
    with lg.transaction(timeout=5) as tx:
        tx.update("steps", 1, "step.complete", {"status": "RECORDED", "outcome": "ok"})
    assert lg.row("steps", 1)["status"] == "RECORDED"
    assert lg.row("runs", run)["agent"] == "claude-code"
    verdict = lg.verify()
    assert verdict.status == "ok", verdict.problems
    assert verdict.head_seq == 4


def test_update_of_a_missing_row_fails(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    with pytest.raises(KeyError), lg.transaction(timeout=5) as tx:
        tx.update("steps", 42, "step.complete", {"status": "RECORDED"})


def test_failed_transaction_leaves_nothing(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    with pytest.raises(RuntimeError), lg.transaction(timeout=5) as tx:
        tx.insert("steps", "step.open", {"run_id": 1, "seq": 3, "status": "PENDING"})
        raise RuntimeError("boom")
    assert lg.verify().head_seq == 3
    assert lg.verify().status == "ok"


# ATK-LED-01: editing or deleting rows


@pytest.mark.parametrize(
    "sql",
    [
        "update steps set status = 'RECORDED' where id = 1",
        "delete from steps where id = 2",
        "insert into steps (run_id, seq, status) values (1, 9, 'RECORDED')",
        "update entries set body = replace(body, 'PENDING', 'RECORDED') where seq = 2",
        "delete from entries where seq = 2",
        "update runs set agent = 'someone-else'",
    ],
)
def test_tampering_with_rows_or_entries_is_detected(tmp_path: Path, sql: str) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    db = raw(tmp_path)
    db.execute(sql)
    db.close()
    verdict = lg.verify()
    assert verdict.status == "tampered", verdict
    assert verdict.problems


def test_deleting_the_newest_row_stays_visible_after_later_writes(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    db = raw(tmp_path)
    db.execute("delete from steps where id = 2")
    db.close()
    add_run(lg)  # must not reuse id 2 and hide the deletion
    verdict = lg.verify()
    assert verdict.status == "tampered"
    assert any("steps row 2 was deleted" in p for p in verdict.problems)


def test_a_consistently_rewritten_chain_is_caught_by_the_anchor(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    db = raw(tmp_path)
    db.execute("update steps set status = 'RECORDED'")
    rows = db.execute(
        "select seq, at, kind, tbl, row_id, body from entries order by seq"
    ).fetchall()
    prev = ""
    for seq, at, kind, tbl, row_id, body in rows:
        body = body.replace("PENDING", "RECORDED")
        digest = ledger.entry_hash(prev, seq, at, kind, tbl, row_id, body)
        db.execute(
            "update entries set body=?, prev_hash=?, hash=? where seq=?", (body, prev, digest, seq)
        )
        prev = digest
    db.close()
    verdict = lg.verify()
    assert verdict.status == "tampered"
    assert any("anchor" in p for p in verdict.problems)


# ATK-LED-02: truncating the newest entries


def test_truncation_is_detected_by_the_anchor(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    with lg.transaction(timeout=5) as tx:
        tx.update("steps", 2, "step.complete", {"status": "RECORDED"})
    db = raw(tmp_path)
    db.execute("delete from entries where seq = 4")
    db.execute("update steps set status = 'PENDING' where id = 2")
    db.close()
    verdict = lg.verify()
    assert verdict.status == "truncated"


def test_entries_appended_outside_agentvc_are_detected_and_not_laundered(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    db = raw(tmp_path)
    [(prev,)] = db.execute("select hash from entries order by seq desc limit 1").fetchall()
    body = json.dumps({"row": {"id": 3, "run_id": 1, "seq": 3, "status": "RECORDED"}})
    at = "2026-10-22T00:00:00+00:00"
    digest = ledger.entry_hash(prev, 4, at, "step.open", "steps", 3, body)
    db.execute(
        "insert into entries values (4, ?, 'step.open', 'steps', 3, ?, ?, ?)",
        (at, body, prev, digest),
    )
    db.execute("insert into steps (id, run_id, seq, status) values (3, 1, 3, 'RECORDED')")
    db.close()
    assert lg.verify().status == "tampered"
    add_run(lg)  # a later legitimate write must not make the forgery look legitimate
    verdict = lg.verify()
    assert verdict.status == "tampered"
    assert any("outside AgentVC" in p for p in verdict.problems)


def test_a_deleted_anchor_is_reported(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    for anchor in (tmp_path / "home" / "anchors").iterdir():
        anchor.unlink()
    verdict = lg.verify()
    assert verdict.status == "tampered"
    assert any("anchor" in p for p in verdict.problems)


# ATK-LED-03: corrupting the database


def test_corrupted_database_is_reported_never_repaired(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    for _ in range(30):
        add_run(lg, n_steps=20)
    lg.close()
    path = tmp_path / "ws" / ".agentvc" / "ledger.db"
    data = bytearray(path.read_bytes())
    for i in range(4096, len(data), 4096):  # scribble over every page after the first
        data[i + 100 : i + 400] = b"\xa5" * 300
    path.write_bytes(bytes(data))
    with pytest.raises(LedgerCorrupt):
        lg = open_ledger(tmp_path)
        verdict = lg.verify()
        if verdict.status == "corrupt":
            raise LedgerCorrupt(verdict.problems[0])
    assert path.read_bytes() == bytes(data)  # nothing rewritten


# --- crashes ------------------------------------------------------------------------------------


def test_crash_before_commit_leaves_the_old_valid_state(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    with crash.armed(ledger.BEFORE_COMMIT), pytest.raises(crash.CrashPointHit):
        add_run(lg)
    verdict = lg.verify()
    assert (verdict.status, verdict.head_seq) == ("ok", 3)
    add_run(lg)
    assert lg.verify().status == "ok"


def test_crash_between_commit_and_anchor_is_noted_not_alarmed(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    with crash.armed(ledger.AFTER_COMMIT), pytest.raises(crash.CrashPointHit):
        add_run(lg)
    verdict = lg.verify()
    assert verdict.status == "anchor-behind"
    assert verdict.ok
    add_run(lg)
    assert lg.verify().status == "ok"


# ATK-LED-06: clock changes


def test_a_clock_jumping_back_cannot_reorder_history(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 10, 22, tzinfo=UTC))
    lg = open_ledger(tmp_path, clock)
    add_run(lg)
    clock.set_wall(datetime(2001, 1, 1, tzinfo=UTC))
    add_run(lg)
    assert lg.verify().status == "ok"
    assert [r["id"] for r in lg.rows("runs")] == [1, 2]


# --- locking (ATK-LED-05, FM-LED-01) ------------------------------------------------------------


def test_lock_timeout_is_raised_not_waited_out(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    with IO().lock(lg.lock_path, timeout=1), pytest.raises(LockTimeout):
        add_run(lg)


def test_threads_writing_at_once_lose_nothing(tmp_path: Path) -> None:
    import threading

    lg = open_ledger(tmp_path)
    threads = [
        threading.Thread(target=lambda: [add_run(lg, 1) for _ in range(20)]) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(lg.rows("runs")) == 80
    assert lg.verify().status == "ok"


WRITER = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from agentvc.clock import SystemClock
from agentvc.io import IO
from agentvc.ledger import Ledger
root = Path(sys.argv[1])
lg = Ledger.open(IO(), root / "ws" / ".agentvc" / "ledger.db", workspace=root / "ws",
                 anchor_dir=root / "home" / "anchors", clock=SystemClock())
for i in range(25):
    with lg.transaction(timeout=30) as tx:
        run = tx.insert("runs", "run.start", {"agent": sys.argv[2], "status": "active"})
        tx.insert("steps", "step.open", {"run_id": run, "seq": 1, "status": "PENDING"})
"""


def test_processes_writing_at_once_lose_nothing(tmp_path: Path) -> None:
    open_ledger(tmp_path).close()
    procs = [
        subprocess.Popen([sys.executable, "-c", WRITER, str(tmp_path), f"writer-{i}"])
        for i in range(4)
    ]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    lg = open_ledger(tmp_path)
    assert len(lg.rows("runs")) == 100
    assert len(lg.rows("steps")) == 100
    verdict = lg.verify()
    assert verdict.status == "ok", verdict.problems


def test_anchor_lives_outside_the_workspace(tmp_path: Path) -> None:
    lg = open_ledger(tmp_path)
    add_run(lg)
    [anchor] = (tmp_path / "home" / "anchors").iterdir()
    assert not anchor.resolve().is_relative_to((tmp_path / "ws").resolve())
    expected = hashlib.sha256(ledger.workspace_key(tmp_path / "ws").encode()).hexdigest()
    assert anchor.name == f"{expected}.json"
