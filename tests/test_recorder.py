"""agentvc/recorder.py: events → runs, turns, steps and visible gaps (design note capture.md).

Replays the captured Claude Code sequences, then attacks: ATK-CAP-04 duplicated or out-of-order
events, ATK-CAP-05 a Pre with no Post, ATK-CAP-06 reused tool_use_id, ATK-CAP-07 events from
another project directory, ATK-CAP-08 two sessions at once.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentvc.adapters import claude_code as cc
from agentvc.clock import FakeClock
from agentvc.events import BatchCall, Event, Kind
from agentvc.io import IO
from agentvc.ledger import Ledger
from agentvc.recorder import Recorder

FIXTURES = Path(__file__).parent / "fixtures" / "hosts" / "claude-code"
WS = {
    "2.1.72": "~\\AppData\\Local\\Temp\\agentvc-capture-2026-10-18",
    "2.1.302": "~\\AppData\\Local\\Temp\\agentvc-capture-2.1.302",
}


def make(tmp_path: Path, workspace: str = WS["2.1.302"]) -> tuple[Recorder, Ledger]:
    clock = FakeClock(datetime(2026, 10, 22, tzinfo=UTC))
    lg = Ledger.open(
        IO(),
        tmp_path / "ledger.db",
        workspace=tmp_path,
        anchor_dir=tmp_path / "anchors",
        clock=clock,
    )
    return Recorder(lg, Path(workspace), clock), lg


def events(version: str) -> list[Event]:
    out = []
    for path in sorted((FIXTURES / version).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))["payload"]
        result = cc.parse(payload, record_prompts=False)
        if isinstance(result, Event):
            out.append(result)
    return out


def fixture(rel: str) -> Event:
    payload = json.loads((FIXTURES / rel).read_text(encoding="utf-8"))["payload"]
    result = cc.parse(payload, record_prompts=False)
    assert isinstance(result, Event)
    return result


def steps(lg: Ledger) -> list[dict[str, Any]]:
    return lg.rows("steps")


PRE = fixture("2.1.302/0004-PreToolUse-Bash.json")
POST = fixture("2.1.302/0005-PostToolUse-Bash.json")
STOP = fixture("2.1.302/0008-Stop.json")
PROMPT = fixture("2.1.302/0003-UserPromptSubmit.json")


# --- whole captured sessions --------------------------------------------------------------------


@pytest.mark.parametrize("version", ["2.1.72", "2.1.302"])
def test_a_whole_captured_session_is_one_run_with_every_step_recorded(
    tmp_path: Path, version: str
) -> None:
    rec, lg = make(tmp_path, WS[version])
    evs = events(version)
    for ev in evs:
        rec.apply(ev, timeout=5)
    assert len(lg.rows("runs")) == 1  # one-off InstructionsLoaded ids start no run (U-17)
    assert len(lg.rows("turns")) == sum(e.kind is Kind.PROMPT for e in evs)
    recorded = steps(lg)
    assert len(recorded) == sum(e.kind is Kind.TOOL_PRE for e in evs)
    assert {s["status"] for s in recorded} == {"RECORDED"}
    assert all(s["turn_id"] is not None for s in recorded)
    assert lg.verify().status == "ok"


def test_failed_commands_are_recorded_with_their_error(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    for ev in events("2.1.302"):
        rec.apply(ev, timeout=5)
    failed = [s for s in steps(lg) if s["outcome"] == "error"]
    assert {s["tool"] for s in failed} == {"Bash", "PowerShell"}
    assert all("Exit code" in s["error"] for s in failed)


def test_parallel_calls_pair_by_tool_use_id(tmp_path: Path) -> None:
    rec, lg = make(tmp_path, WS["2.1.72"])
    for n in (
        "0036-SessionStart",
        "0037-UserPromptSubmit",
        "0038-PreToolUse-Bash",
        "0039-PreToolUse-Bash",
        "0041-PostToolUse-Bash",
        "0040-PostToolUse-Bash",
    ):
        rec.apply(fixture(f"2.1.72/{n}.json"), timeout=5)
    a, b = steps(lg)
    assert a["status"] == b["status"] == "RECORDED"
    assert a["tool_use_id"] != b["tool_use_id"]


def test_subagent_steps_keep_their_agent_id(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    for ev in events("2.1.302"):
        rec.apply(ev, timeout=5)
    sub = [s for s in steps(lg) if s["agent_id"]]
    assert sub and all(s["tool"] == "Bash" for s in sub)


# --- gaps (ADR-0004) ----------------------------------------------------------------------------


def test_post_without_pre_is_a_visible_gap(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(POST, timeout=5)
    [step] = steps(lg)
    assert (step["status"], step["gap_reason"], step["tool"]) == ("GAP", "pre-missing", "Bash")


@pytest.mark.parametrize("closer", ["stop", "prompt", "session_end", "session_start"])
def test_pre_without_post_becomes_a_gap_when_the_turn_ends(tmp_path: Path, closer: str) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(PRE, timeout=5)
    assert steps(lg)[0]["status"] == "PENDING"
    closing = {
        "stop": STOP,
        "prompt": PROMPT,
        "session_end": fixture("2.1.302/0009-SessionEnd.json"),
        "session_start": fixture("2.1.302/0011-SessionStart.json"),
    }[closer]
    rec.apply(closing, timeout=5)
    [step] = steps(lg)
    assert (step["status"], step["gap_reason"]) == ("GAP", "post-missing")


def test_subagent_stop_closes_only_that_subagents_steps(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(PRE, timeout=5)
    sub_pre = replace(PRE, tool_use_id="toolu_sub", agent_id="a1")
    rec.apply(sub_pre, timeout=5)
    stop = Event(
        Kind.SUBAGENT_STOP, "claude-code", "SubagentStop", PRE.session_id, PRE.cwd, agent_id="a1"
    )
    rec.apply(stop, timeout=5)
    main, sub = steps(lg)
    assert main["status"] == "PENDING"
    assert (sub["status"], sub["gap_reason"]) == ("GAP", "post-missing")


def test_stop_leaves_subagent_steps_alone(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(replace(PRE, agent_id="a1"), timeout=5)
    rec.apply(STOP, timeout=5)
    assert steps(lg)[0]["status"] == "PENDING"


def test_batch_reconciles_missing_posts_and_unseen_calls(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(PRE, timeout=5)
    rec.apply(replace(PRE, tool_use_id="toolu_done"), timeout=5)
    rec.apply(replace(POST, tool_use_id="toolu_done"), timeout=5)
    batch = Event(
        Kind.TOOL_BATCH,
        "claude-code",
        "PostToolBatch",
        PRE.session_id,
        PRE.cwd,
        batch=(
            BatchCall(PRE.tool_use_id or "", "Bash"),
            BatchCall("toolu_done", "Bash"),
            BatchCall("toolu_never_seen", "Write"),
        ),
    )
    rec.apply(batch, timeout=5)
    by_id = {s["tool_use_id"]: s for s in steps(lg)}
    assert (by_id[PRE.tool_use_id]["status"], by_id[PRE.tool_use_id]["gap_reason"]) == (
        "GAP",
        "post-missing",
    )
    assert by_id["toolu_done"]["status"] == "RECORDED"
    unseen = by_id["toolu_never_seen"]
    assert (unseen["status"], unseen["gap_reason"], unseen["tool"]) == (
        "GAP",
        "reconciled-from-batch",
        "Write",
    )


def test_a_post_arriving_after_its_gap_completes_the_step(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(PRE, timeout=5)
    rec.apply(STOP, timeout=5)
    rec.apply(POST, timeout=5)
    [step] = steps(lg)
    assert step["status"] == "RECORDED"
    assert "post-late" in json.loads(step["flags"])


# --- ATK-CAP-04 / -06: duplicates, reordering, reused ids -----------------------------------------


def test_out_of_order_post_then_pre_stays_a_gap_marked_late(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(POST, timeout=5)
    rec.apply(PRE, timeout=5)
    [step] = steps(lg)
    assert (step["status"], step["gap_reason"]) == ("GAP", "pre-late")


def test_duplicate_events_change_nothing_and_are_reported(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(PRE, timeout=5)
    assert rec.apply(PRE, timeout=5).blindspot == "duplicate-event"
    rec.apply(POST, timeout=5)
    assert rec.apply(POST, timeout=5).blindspot == "duplicate-event"
    [step] = steps(lg)
    assert step["status"] == "RECORDED"


def test_a_reused_tool_use_id_opens_a_new_flagged_step(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(PRE, timeout=5)
    rec.apply(POST, timeout=5)
    rec.apply(PRE, timeout=5)
    rec.apply(POST, timeout=5)
    first, second = steps(lg)
    assert first["status"] == second["status"] == "RECORDED"
    assert "reused-id" in json.loads(second["flags"])


# --- ATK-CAP-07 / -08: other projects, two sessions -----------------------------------------------


def test_an_unknown_session_from_another_project_is_not_recorded(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    foreign = replace(PRE, session_id="other-session", cwd="D:\\elsewhere\\project")
    assert rec.apply(foreign, timeout=5).blindspot == "foreign-project"
    assert lg.rows("runs") == []


def test_a_known_session_that_leaves_the_workspace_is_recorded_and_flagged(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(replace(PRE, cwd="D:\\elsewhere"), timeout=5)
    [step] = steps(lg)
    assert "outside-workspace" in json.loads(step["flags"])


def test_subfolders_of_the_workspace_are_inside_it(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(replace(PRE, cwd=PRE.cwd + "\\SRC\\pkg"), timeout=5)
    assert "outside-workspace" not in json.loads(steps(lg)[0]["flags"])


def test_two_sessions_at_once_get_separate_runs(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    other = "11111111-2222-3333-4444-555555555555"
    rec.apply(PROMPT, timeout=5)
    rec.apply(replace(PROMPT, session_id=other), timeout=5)
    rec.apply(PRE, timeout=5)
    rec.apply(replace(PRE, session_id=other, tool_use_id="toolu_other"), timeout=5)
    rec.apply(replace(POST, session_id=other, tool_use_id="toolu_other"), timeout=5)
    rec.apply(POST, timeout=5)
    runs = lg.rows("runs")
    assert len(runs) == 2
    for s in steps(lg):
        assert s["status"] == "RECORDED"
    assert {s["run_id"] for s in steps(lg)} == {r["id"] for r in runs}


# --- run identity ---------------------------------------------------------------------------


def test_a_run_first_seen_mid_session_is_flagged(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PRE, timeout=5)
    [run] = lg.rows("runs")
    assert "start-missing" in json.loads(run["flags"])


def test_resume_with_an_unknown_session_id_starts_a_run_marked_resumed(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    resume = fixture("2.1.302/0011-SessionStart.json")
    rec.apply(replace(resume, session_id="99999999-0000-0000-0000-000000000000"), timeout=5)
    [run] = lg.rows("runs")
    assert "resumed" in json.loads(run["flags"])


def test_steps_join_the_turn_with_their_prompt_id(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.apply(replace(PROMPT, prompt_id="second-prompt"), timeout=5)
    rec.apply(PRE, timeout=5)  # carries the first prompt's id
    first_turn = lg.rows("turns")[0]
    assert steps(lg)[0]["turn_id"] == first_turn["id"]


def test_failures_can_be_recorded_as_gaps(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.record_gap(PRE.session_id, "hook-error", "boom", event=PRE, timeout=5)
    rec.record_gap(PRE.session_id, "daemon-down", "since 10:00", timeout=5)
    a, b = steps(lg)
    assert (a["gap_reason"], a["tool"], a["tool_use_id"]) == ("hook-error", "Bash", PRE.tool_use_id)
    assert (b["gap_reason"], b["tool"]) == ("daemon-down", None)
    assert lg.verify().status == "ok"


def test_a_denied_action_is_recorded_as_denied_not_as_a_gap(tmp_path: Path) -> None:
    rec, lg = make(tmp_path)
    rec.apply(PROMPT, timeout=5)
    rec.deny(PRE, "writes into .agentvc/", timeout=5)
    rec.apply(STOP, timeout=5)
    [step] = steps(lg)
    assert (step["status"], step["error"], step["tool"]) == (
        "DENIED",
        "writes into .agentvc/",
        "Bash",
    )
