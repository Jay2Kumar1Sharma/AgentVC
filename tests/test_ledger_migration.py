"""Ledger schema 1 → 2, the first real migration (ADR-0007, design note ledger.md).

The schema-1 ledger in tests/fixtures/ledger/schema-1/ was written by session 3's own code
(tests/fixtures/ledger/make_schema_1.py), so these tests open what an older AgentVC really left.
"""

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentvc import crash, ledger
from agentvc.clock import FakeClock
from agentvc.io import IO
from agentvc.ledger import Ledger, LedgerTooNew, workspace_key

FIXTURE = Path(__file__).parent / "fixtures" / "ledger" / "schema-1"
OLD_TABLES = ("entries", "runs", "run_sessions", "turns", "steps")


def install(tmp_path: Path) -> tuple[Path, Path, Path]:
    """The fixture as a workspace's ledger, with its anchor in a temporary home."""
    ws = tmp_path / "ws"
    (ws / ".agentvc").mkdir(parents=True)
    db = ws / ".agentvc" / "ledger.db"
    shutil.copyfile(FIXTURE / "ledger.db", db)
    anchors = tmp_path / "home" / "anchors"
    anchors.mkdir(parents=True)
    head = json.loads((FIXTURE / "head.json").read_text(encoding="utf-8"))
    name = hashlib.sha256(workspace_key(ws).encode()).hexdigest() + ".json"
    (anchors / name).write_text(
        json.dumps({**head, "pending": None, "workspace": str(ws)}), encoding="utf-8"
    )
    return ws, db, anchors


def raw(db: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db)
    try:
        tables = {n for (n,) in conn.execute("select name from sqlite_master where type='table'")}
        return {
            "version": conn.execute("select version from schema_version").fetchall(),
            "tables": tables,
            **{t: conn.execute(f"select * from {t} order by 1").fetchall() for t in OLD_TABLES},
        }
    finally:
        conn.close()


def open_ledger(ws: Path, db: Path, anchors: Path) -> Ledger:
    clock = FakeClock(datetime(2026, 10, 23, tzinfo=UTC))
    return Ledger.open(IO(), db, workspace=ws, anchor_dir=anchors, clock=clock)


def test_the_fixture_really_is_schema_1() -> None:
    before = raw(FIXTURE / "ledger.db")
    assert before["version"] == [(1,)]
    assert "snapshots" not in before["tables"]
    assert len(before["steps"]) >= 10


def test_a_schema_1_ledger_upgrades_with_every_row_intact(tmp_path: Path) -> None:
    ws, db, anchors = install(tmp_path)
    before = raw(db)
    lg = open_ledger(ws, db, anchors)
    try:
        after = raw(db)
        assert after["version"] == [(ledger.SCHEMA_VERSION,)] == [(2,)]
        assert {"snapshots", "rollbacks"} <= after["tables"]
        for table in OLD_TABLES:
            assert after[table] == before[table], table
        assert lg.rows("snapshots") == [] and lg.rows("rollbacks") == []
        assert lg.verify().status == "ok", lg.verify().problems
        versions = [r[0] for r in sqlite3.connect(db).execute("select version from migrations")]
        assert versions == [1, 2]
        # Still writable, and the chain continues cleanly.
        with lg.transaction(timeout=5) as tx:
            tx.insert("runs", "run.start", {"agent": "claude-code", "status": "active"})
        assert lg.verify().status == "ok"
    finally:
        lg.close()
    backup = raw(db.with_name("ledger.db.bak-v1"))
    assert backup["version"] == [(1,)]
    assert backup["entries"] == before["entries"]


def test_a_crash_during_the_migration_leaves_schema_1_intact(tmp_path: Path) -> None:
    ws, db, anchors = install(tmp_path)
    before = raw(db)
    with crash.armed(ledger.MIGRATE_BEFORE_COMMIT), pytest.raises(crash.CrashPointHit):
        open_ledger(ws, db, anchors)
    assert raw(db) == before
    lg = open_ledger(ws, db, anchors)  # the next start migrates
    try:
        assert lg.verify().status == "ok"
    finally:
        lg.close()
    assert raw(db)["version"] == [(2,)]


def test_an_agentvc_that_knows_only_schema_1_refuses_the_upgraded_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, db, anchors = install(tmp_path)
    open_ledger(ws, db, anchors).close()
    upgraded = db.read_bytes()
    monkeypatch.setattr(ledger, "SCHEMA_VERSION", 1)
    with pytest.raises(LedgerTooNew, match="schema 2"):
        open_ledger(ws, db, anchors)
    assert db.read_bytes() == upgraded
