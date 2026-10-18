"""scripts/spikes/hook_failure.py (unknown U-04, decision D2): setup and result reading.

Running the scenarios needs Claude Code, so these tests cover the parts that don't: the
throwaway project each scenario gets, and how marker files become a result.
"""

import json
from pathlib import Path

import pytest

from scripts.spikes import hook_failure


@pytest.mark.parametrize("name", sorted(hook_failure.SCENARIOS))
def test_setup_writes_project_local_settings(name: str, tmp_path: Path) -> None:
    hook_failure.setup(tmp_path, name)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    pre = settings["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "Bash"
    assert pre["hooks"][0]["timeout"] == hook_failure.PRE_TIMEOUT_SECONDS
    assert set(settings["hooks"]) == {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
    assert (tmp_path / "pre.py").is_file() and (tmp_path / "post.py").is_file()


def test_report_reads_markers(tmp_path: Path) -> None:
    (tmp_path / "pre_started").write_text("1", encoding="utf-8")
    (tmp_path / "tool_ran.txt").write_text("ran\n", encoding="utf-8")
    (tmp_path / "post_events.txt").write_text("PostToolUse\n", encoding="utf-8")
    assert hook_failure.report(tmp_path) == {
        "pre_hook_started": True,
        "pre_hook_finished": False,
        "tool_ran": True,
        "post_events": ["PostToolUse"],
    }


def test_report_on_an_empty_folder(tmp_path: Path) -> None:
    assert hook_failure.report(tmp_path) == {
        "pre_hook_started": False,
        "pre_hook_finished": False,
        "tool_ran": False,
        "post_events": [],
    }


def test_report_when_the_hook_blocked_the_tool(tmp_path: Path) -> None:
    (tmp_path / "pre_started").write_text("1", encoding="utf-8")
    (tmp_path / "pre_finished").write_text("1", encoding="utf-8")
    result = hook_failure.report(tmp_path)
    assert result["pre_hook_started"] is True
    assert result["pre_hook_finished"] is True
    assert result["tool_ran"] is False
    assert result["post_events"] == []
