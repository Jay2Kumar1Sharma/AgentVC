"""agentvc/wiring.py: one list of events and tools; the hook config is generated from it."""

import re
from pathlib import Path
from typing import Any

import pytest

from agentvc import wiring
from scripts import capture_payloads

PYTHON = Path("C:/Program Files/AgentVC/python.exe").resolve()
FIXTURE_VERSIONS = sorted(
    p.name for p in Path("tests/fixtures/hosts/claude-code").iterdir() if p.is_dir()
)


def generate(version: str, mode: str = "protect") -> wiring.GeneratedHooks:
    return wiring.claude_code_hooks(
        agent_version=version, python=PYTHON, port=48123, token="t0k3n", mode=mode
    )


def handlers(settings: dict[str, Any], event: str) -> list[dict[str, Any]]:
    return [h for group in settings["hooks"].get(event, []) for h in group["hooks"]]


def test_version_event_lists_match_the_capture_evidence() -> None:
    for version in ("2.1.72", "2.1.302"):
        assert wiring.VERSION_EVENTS[version] == capture_payloads.EVENTS[version]


@pytest.mark.parametrize("version", FIXTURE_VERSIONS)
def test_config_names_only_events_the_version_knows(version: str) -> None:
    # FM-INS-03: 2.1.72 registers no hooks at all if one event name is unknown.
    settings = generate(version).settings
    assert set(settings["hooks"]) <= set(wiring.VERSION_EVENTS[version])
    assert set(settings["hooks"]) == {
        s.event for s in wiring.SUBSCRIPTIONS if s.event in wiring.VERSION_EVENTS[version]
    }


def test_post_tool_batch_only_where_it_exists() -> None:
    assert "PostToolBatch" not in generate("2.1.72").settings["hooks"]
    assert "PostToolBatch" in generate("2.1.302").settings["hooks"]


def test_unknown_newer_version_uses_the_newest_known_list_with_a_warning() -> None:
    hooks = generate("2.1.306")
    assert set(hooks.settings["hooks"]) <= set(wiring.VERSION_EVENTS["2.1.302"])
    assert any("2.1.306" in w for w in hooks.warnings)


def test_unknown_version_between_known_ones_uses_the_older_list() -> None:
    hooks = generate("2.1.200")
    assert set(hooks.settings["hooks"]) <= set(wiring.VERSION_EVENTS["2.1.72"])
    assert all(
        h["type"] == "command" for e in hooks.settings["hooks"] for h in handlers(hooks.settings, e)
    )


def test_versions_older_than_any_evidence_are_refused() -> None:
    with pytest.raises(wiring.UnsupportedVersion):
        generate("2.0.5")
    with pytest.raises(wiring.UnsupportedVersion):
        generate("not-a-version")


def test_tool_events_match_every_tool_both_shells_included() -> None:
    settings = generate("2.1.302").settings
    for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        for group in settings["hooks"][event]:
            matcher = group["matcher"]
            for tool in [*wiring.TOOLS, "Bash", "PowerShell", "mcp__github__create_issue"]:
                assert matcher == "*" or re.fullmatch(matcher, tool), (event, tool)


def test_http_hooks_use_127_0_0_1_and_a_literal_token() -> None:
    settings = generate("2.1.302").settings
    for event in ("PreToolUse", "PostToolUse", "Stop", "SessionEnd"):
        [hook] = handlers(settings, event)
        assert hook["type"] == "http"
        assert hook["url"] == "http://127.0.0.1:48123/hook"  # never localhost (U-18)
        assert hook["headers"] == {wiring.TOKEN_HEADER: "t0k3n"}
        assert "allowedEnvVars" not in hook
        assert hook["timeout"] == wiring.HOOK_TIMEOUT_S


def test_liveness_events_run_agentvc_by_absolute_path_in_isolated_mode() -> None:
    settings = generate("2.1.302").settings
    for event in ("SessionStart", "UserPromptSubmit"):
        [hook] = handlers(settings, event)
        assert hook["type"] == "command"
        assert hook["command"] == str(PYTHON)
        assert hook["args"] == ["-I", "-m", "agentvc", "hook", "--ensure-daemon"]
        assert hook["timeout"] == wiring.HOOK_TIMEOUT_S


def test_versions_without_http_hooks_get_the_per_call_command_everywhere() -> None:
    settings = generate("2.1.72").settings
    for event in settings["hooks"]:
        [hook] = handlers(settings, event)
        assert hook["type"] == "command"
        assert hook["command"] == f'"{PYTHON.as_posix()}" -I -m agentvc hook'
        assert "args" not in hook


def test_interpreter_must_be_absolute() -> None:
    with pytest.raises(ValueError):
        wiring.claude_code_hooks(
            agent_version="2.1.302", python=Path("python"), port=1, token="t", mode="protect"
        )


def test_hook_timeout_is_derived_from_agentvc_deadline() -> None:
    assert wiring.HOOK_TIMEOUT_S == 2 * wiring.DEADLINE_S


def test_strict_mode_blocks_on_failure_only_where_supported() -> None:
    [pre] = handlers(generate("2.1.302", mode="strict").settings, "PreToolUse")
    assert pre["onFailure"] == "block"
    [post] = handlers(generate("2.1.302", mode="strict").settings, "PostToolUse")
    assert "onFailure" not in post
    [pre] = handlers(generate("2.1.302", mode="protect").settings, "PreToolUse")
    assert "onFailure" not in pre
    hooks = generate("2.1.72", mode="strict")
    [pre] = handlers(hooks.settings, "PreToolUse")
    assert "onFailure" not in pre
    assert any("onFailure" in w for w in hooks.warnings)


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        generate("2.1.302", mode="paranoid")


def test_tool_categories() -> None:
    assert wiring.tool_category("Bash") == wiring.tool_category("PowerShell") == "shell"
    assert wiring.tool_category("Edit") == "edit"
    assert wiring.tool_category("mcp__slack__post") == "mcp"
    assert wiring.tool_category("BrandNewTool") == "unknown"
