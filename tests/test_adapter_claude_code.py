"""Claude Code adapter, tested only against captured payloads (2.1.72, 2.1.302) and mutations.

Attacks covered (plan §15.3): ATK-CAP-01 malformed/truncated/huge, ATK-CAP-02 missing or renamed
fields, ATK-CAP-03 NUL and odd Unicode. Storage rule (U-13): file contents, assistant text and
(unless enabled) prompts never reach the event.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from agentvc import wiring
from agentvc.adapters import claude_code as cc
from agentvc.events import BlindSpot, Event, Kind

FIXTURES = Path(__file__).parent / "fixtures" / "hosts" / "claude-code"
ALL = sorted(FIXTURES.glob("*/*.json"))
CONTENT_KEYS = {"content", "old_string", "new_string", "originalFile", "oldString", "newString"}


def payload(rel: str) -> dict[str, Any]:
    data = json.loads((FIXTURES / rel).read_text(encoding="utf-8"))["payload"]
    assert isinstance(data, dict)
    return data


def event(rel: str, **kw: Any) -> Event:
    result = cc.parse(payload(rel), **kw)
    assert isinstance(result, Event), result
    return result


def strings_in(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return []


def content_strings(value: Any) -> list[str]:
    """Every string stored under a content-bearing key, anywhere in the payload."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k in CONTENT_KEYS:
                found += strings_in(v)
            else:
                found += content_strings(v)
    elif isinstance(value, list):
        for v in value:
            found += content_strings(v)
    return found


def test_both_versions_are_covered() -> None:
    assert {p.parent.name for p in ALL} == {"2.1.72", "2.1.302"}


@pytest.mark.parametrize("path", ALL, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_every_fixture_parses_or_is_a_known_unhandled_event(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))["payload"]
    result = cc.parse(data, record_prompts=False)
    name = data["hook_event_name"]
    if name in wiring.EVENTS:
        assert isinstance(result, Event)
        assert result.kind is cc.EVENT_KINDS[name]
        assert result.raw_event == name
        assert result.session_id == data["session_id"]
        assert result.cwd == data["cwd"]
        assert result.agent == "claude-code"
    else:
        assert isinstance(result, BlindSpot)
        assert result.reason == "unhandled-event"


@pytest.mark.parametrize("path", ALL, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_no_file_content_prompt_or_assistant_text_reaches_the_event(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))["payload"]
    result = cc.parse(data, record_prompts=False)
    stored = json.dumps(result.to_json(), ensure_ascii=False)
    secret_texts = [
        *content_strings(data),
        data.get("prompt", ""),
        data.get("last_assistant_message", ""),
    ]
    for text in secret_texts:
        if len(text) >= 4:
            assert text not in stored, (path.name, text[:40])


def test_editor_content_is_reduced_to_its_size() -> None:
    ev = event("2.1.302/0038-PreToolUse-Write.json", record_prompts=False)
    assert ev.tool_input is not None
    content = payload("2.1.302/0038-PreToolUse-Write.json")["tool_input"]["content"]
    assert ev.tool_input["content"] == {"chars": len(content)}
    assert ev.tool_input["file_path"].endswith("new.txt")


@pytest.mark.parametrize("version", ["2.1.72", "2.1.302"])
def test_bash_pre_and_post_pair_on_tool_use_id(version: str) -> None:
    files = sorted((FIXTURES / version).glob("*-PreToolUse-Bash.json"))
    pre = cc.parse(
        json.loads(files[0].read_text(encoding="utf-8"))["payload"], record_prompts=False
    )
    assert isinstance(pre, Event) and pre.kind is Kind.TOOL_PRE
    assert pre.tool_name == "Bash"
    assert pre.tool_input is not None and pre.tool_input["command"] == "echo hello"
    post_file = files[0].with_name(f"{int(files[0].name[:4]) + 1:04d}-PostToolUse-Bash.json")
    post = cc.parse(
        json.loads(post_file.read_text(encoding="utf-8"))["payload"], record_prompts=False
    )
    assert isinstance(post, Event) and post.kind is Kind.TOOL_POST
    assert post.tool_use_id == pre.tool_use_id
    assert post.outcome == "ok"


@pytest.mark.parametrize(
    "rel",
    [
        "2.1.72/0061-PostToolUseFailure-Bash.json",
        "2.1.302/0076-PostToolUseFailure-Bash.json",
        "2.1.302/0086-PostToolUseFailure-PowerShell.json",
    ],
)
def test_tool_failure_is_a_post_event_with_an_error(rel: str) -> None:
    ev = event(rel, record_prompts=False)
    assert ev.kind is Kind.TOOL_POST
    assert ev.outcome == "error"
    assert ev.error is not None and "Exit code" in ev.error


def test_interrupted_failure_and_interrupted_bash() -> None:
    data = payload("2.1.302/0076-PostToolUseFailure-Bash.json")
    data["is_interrupt"] = True
    ev = cc.parse(data, record_prompts=False)
    assert isinstance(ev, Event) and ev.outcome == "interrupted"
    data = payload("2.1.302/0005-PostToolUse-Bash.json")
    data["tool_response"]["interrupted"] = True
    ev = cc.parse(data, record_prompts=False)
    assert isinstance(ev, Event) and ev.outcome == "interrupted"


def test_powershell_is_a_shell_tool() -> None:
    ev = event("2.1.302/0014-PreToolUse-PowerShell.json", record_prompts=False)
    assert ev.tool_name == "PowerShell"
    assert wiring.tool_category(ev.tool_name) == "shell"


@pytest.mark.parametrize(
    "rel", ["2.1.72/0051-PreToolUse-Bash.json", "2.1.302/0062-PreToolUse-Bash.json"]
)
def test_subagent_tool_calls_carry_the_agent_id(rel: str) -> None:
    ev = event(rel, record_prompts=False)
    assert ev.agent_id


def test_post_tool_batch_lists_its_calls() -> None:
    ev = event("2.1.302/0052-PostToolBatch.json", record_prompts=False)
    assert ev.kind is Kind.TOOL_BATCH
    assert len(ev.batch) == 2
    assert all(c.tool_use_id.startswith("toolu_") and c.tool_name == "Bash" for c in ev.batch)


def test_prompts_only_when_enabled_and_scrubbed() -> None:
    rel = "2.1.302/0003-UserPromptSubmit.json"
    assert event(rel, record_prompts=False).prompt is None
    assert event(rel, record_prompts=True).prompt == payload(rel)["prompt"]
    data = payload(rel)
    token = "ghp_" + "B" * 36
    data["prompt"] = f"use {token} to push"
    ev = cc.parse(data, record_prompts=True)
    assert isinstance(ev, Event) and ev.prompt is not None and token not in ev.prompt


def test_secrets_in_commands_are_scrubbed() -> None:
    data = payload("2.1.302/0004-PreToolUse-Bash.json")
    token = "ghp_" + "C" * 36
    data["tool_input"]["command"] = f"git push https://x:{token}@github.com/a/b"
    ev = cc.parse(data, record_prompts=False)
    assert isinstance(ev, Event)
    assert token not in json.dumps(ev.to_json())


# --- ATK-CAP-02: missing, renamed, wrong-typed fields ------------------------------------------

HANDLED = [
    p
    for p in ALL
    if json.loads(p.read_text(encoding="utf-8"))["payload"]["hook_event_name"] in wiring.EVENTS
]


@pytest.mark.parametrize("path", HANDLED, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_dropping_or_retyping_any_field_never_crashes(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))["payload"]
    name = data["hook_event_name"]
    for key in list(data):
        for broken in ({k: v for k, v in data.items() if k != key}, {**data, key: 12345}):
            result = cc.parse(broken, record_prompts=False)
            assert isinstance(result, Event | BlindSpot)
            if key in cc.required_fields(name):
                assert isinstance(result, BlindSpot), (key, result)
                assert result.reason in {"missing-field", "bad-field", "unhandled-event"}


def test_renamed_field_is_a_blind_spot() -> None:
    data = payload("2.1.302/0004-PreToolUse-Bash.json")
    data["toolUseId"] = data.pop("tool_use_id")
    result = cc.parse(data, record_prompts=False)
    assert isinstance(result, BlindSpot)
    assert result.reason == "missing-field"
    assert "tool_use_id" in result.detail


# --- ATK-CAP-01: malformed, truncated, huge ----------------------------------------------------

BAD_BODIES = [
    b"",
    b"not json",
    b"[1, 2, 3]",
    b'{"session_id": "x", "hook_event_name": "PreToolUse"',  # truncated
    b'"just a string"',
    b"\xff\xfe\x00garbage",
    b"[" * 100_000 + b"]" * 100_000,  # nesting deep enough to blow the parser's recursion
    b'{"hook_event_name": "PreToolUse", "session_id": "' + b"A" * 3_000_000 + b'"}',
]


@pytest.mark.parametrize("body", BAD_BODIES, ids=range(len(BAD_BODIES)))
def test_malformed_bodies_become_blind_spots(body: bytes) -> None:
    result = cc.parse_bytes(body, record_prompts=False)
    assert isinstance(result, BlindSpot)
    assert len(json.dumps(result.to_json())) <= cc.BLINDSPOT_MAX_CHARS


def test_huge_but_valid_write_payload_is_recorded_without_its_content() -> None:
    data = payload("2.1.302/0038-PreToolUse-Write.json")
    data["tool_input"]["content"] = "z" * 5_000_000
    result = cc.parse_bytes(json.dumps(data).encode(), record_prompts=False)
    assert isinstance(result, Event)
    assert len(json.dumps(result.to_json())) < 10_000


# --- ATK-CAP-03: NUL and odd Unicode -----------------------------------------------------------


def test_nul_and_lone_surrogates_are_made_storable() -> None:
    data = payload("2.1.302/0004-PreToolUse-Bash.json")
    body = json.dumps(data).replace("echo hello", "echo he\\u0000llo \\ud800 \\u202e caf\\u00e9")
    result = cc.parse_bytes(body.encode(), record_prompts=False)
    assert isinstance(result, Event)
    stored = json.dumps(result.to_json(), ensure_ascii=False)
    stored.encode("utf-8")  # no lone surrogates left
    assert "\x00" not in stored
    assert "café" in stored


def test_blind_spot_keeps_shape_not_content() -> None:
    data = payload("2.1.302/0038-PreToolUse-Write.json")
    data["hook_event_name"] = "PreToolUseV2"
    data["tool_input"]["content"] = "TOPSECRET-" + "q" * 500
    result = cc.parse(data, record_prompts=False)
    assert isinstance(result, BlindSpot)
    text = json.dumps(result.to_json())
    assert "TOPSECRET" not in text
    assert result.event_name == "PreToolUseV2"
    assert result.shape["tool_input"]["content"] == "str(510)"


@pytest.mark.parametrize(
    ("rel", "key"),
    [
        ("2.1.302/0038-PreToolUse-Write.json", "file_path"),
        ("2.1.302/0028-PreToolUse-Edit.json", "file_path"),
        ("2.1.302/0039-PostToolUse-Write.json", "file_path"),
    ],
)
def test_edit_targets_are_kept_for_snapshots_but_never_stored(rel: str, key: str) -> None:
    data = payload(rel)
    # Absolute on this OS (a drive path isn't absolute on POSIX), and looks like a token.
    secret_path = str(Path(Path.cwd().anchor, "work", "ghp_" + "a" * 36, "notes.txt"))
    data["tool_input"][key] = secret_path
    event = cc.parse(data, record_prompts=False)
    assert isinstance(event, Event)
    assert event.targets == (secret_path,)  # unscrubbed: it has to find the real file
    assert "ghp_" + "a" * 36 not in json.dumps(event.to_json())


def test_relative_targets_are_made_absolute_and_shell_tools_have_none() -> None:
    data = payload("2.1.302/0038-PreToolUse-Write.json")
    data["tool_input"]["file_path"] = "sub/new.txt"
    data["cwd"] = "C:/proj" if __import__("sys").platform == "win32" else "/proj"
    event = cc.parse(data, record_prompts=False)
    assert isinstance(event, Event)
    [target] = event.targets
    assert Path(target).is_absolute() and target.replace("\\", "/").endswith("proj/sub/new.txt")
    bash = cc.parse(payload("2.1.302/0004-PreToolUse-Bash.json"), record_prompts=False)
    assert isinstance(bash, Event) and bash.targets == ()
