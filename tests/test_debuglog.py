"""agentvc/debuglog.py: AGENTVC_DEBUG log, scrubbed, bounded, never in the way (plan §6.2)."""

import errno
import json
from datetime import UTC, datetime
from pathlib import Path

from agentvc.clock import FakeClock
from agentvc.debuglog import DebugLog, enabled_from_env
from agentvc.io import IO

CLOCK = FakeClock(datetime(2026, 10, 21, 9, 30, tzinfo=UTC))


def test_enabled_only_by_the_environment_variable() -> None:
    assert enabled_from_env({"AGENTVC_DEBUG": "1"})
    assert enabled_from_env({"AGENTVC_DEBUG": "true"})
    assert not enabled_from_env({"AGENTVC_DEBUG": "0"})
    assert not enabled_from_env({})


def test_disabled_log_writes_nothing(tmp_path: Path) -> None:
    log = DebugLog(IO(), tmp_path / "debug.log", enabled=False, clock=CLOCK)
    log.record("hook", event="PreToolUse")
    assert not (tmp_path / "debug.log").exists()


def test_records_are_json_lines_with_time_and_fields(tmp_path: Path) -> None:
    log = DebugLog(IO(), tmp_path / "debug.log", enabled=True, clock=CLOCK)
    log.record("hook", event="PreToolUse", decision="allow", parse_ms=1.5, ok=True)
    [line] = (tmp_path / "debug.log").read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record == {
        "at": "2026-10-21T09:30:00+00:00",
        "what": "hook",
        "event": "PreToolUse",
        "decision": "allow",
        "parse_ms": 1.5,
        "ok": True,
    }


def test_values_are_scrubbed_and_capped(tmp_path: Path) -> None:
    log = DebugLog(IO(), tmp_path / "debug.log", enabled=True, clock=CLOCK)
    secret = "ghp_" + "A" * 36
    log.record("hook", error=f"failed with token {secret}", detail="y" * 10_000)
    text = (tmp_path / "debug.log").read_text(encoding="utf-8")
    assert secret not in text
    assert len(text) < 1000


def test_log_rotates_and_stays_bounded(tmp_path: Path) -> None:
    log = DebugLog(IO(), tmp_path / "debug.log", enabled=True, clock=CLOCK, max_bytes=2000)
    for i in range(500):
        log.record("hook", n=i)
    names = sorted(p.name for p in tmp_path.iterdir() if not p.name.endswith(".lock"))
    assert names == ["debug.log", "debug.log.1"]
    assert sum((tmp_path / n).stat().st_size for n in names) < 5000


def test_a_failing_log_never_raises(tmp_path: Path) -> None:
    io = IO()
    io.faults.fail("append", OSError(errno.ENOSPC, "No space left on device"), times=100)
    log = DebugLog(io, tmp_path / "debug.log", enabled=True, clock=CLOCK)
    log.record("hook", event="PreToolUse")  # must not raise
