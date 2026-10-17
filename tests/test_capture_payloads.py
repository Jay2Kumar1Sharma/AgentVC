"""scripts/capture_payloads.py: records hook payloads, scrubbed, and never disturbs the agent.

Privacy: secrets, the user's home folder and user name, and email addresses never reach a
fixture (FM-DEV-01, HZ-G6-05). Safety: the hook prints nothing and always exits 0, and setup
writes only inside the throwaway folder it is given (never the user's global agent config).
"""

import io
import json
import sys
from pathlib import Path

import pytest

from scripts import capture_payloads as cp

ROOT = Path(__file__).resolve().parent.parent

SECRETS = [
    "sk-ant-api03-" + "a" * 40,
    "sk-proj-" + "B" * 40,
    "ghp_" + "c" * 36,
    "github_pat_" + "D" * 40,
    "xoxb-123456789012-abcdefghijkl",
    "AKIA" + "E" * 16,
    "AIza" + "f" * 35,
    "glpat-" + "g" * 20,
    "npm_" + "h" * 36,
    "pypi-" + "I" * 40,
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXNpZ25hdHVyZQ",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_scrub_removes_known_token_formats(secret: str) -> None:
    text = cp.scrub_text(f"export TOKEN_X={secret} and again {secret}", home=None, user=None)
    assert secret not in text
    assert "[REDACTED" in text


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abc.def-123_456",
        "PASSWORD=hunter2hunter2",
        "api_key: 'q1w2e3r4t5y6u7i8'",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAB3Nza\n-----END OPENSSH PRIVATE KEY-----",
    ],
)
def test_scrub_removes_assigned_secrets(text: str) -> None:
    scrubbed = cp.scrub_text(text, home=None, user=None)
    for fragment in ("abc.def-123_456", "hunter2hunter2", "q1w2e3r4t5y6u7i8", "AAAAB3Nza"):
        assert fragment not in scrubbed


def test_scrub_anonymises_home_user_and_email() -> None:
    home = "C:\\Users\\alice"
    text = (
        "C:\\Users\\alice\\proj and C:/Users/alice/proj and C:\\\\Users\\\\alice "
        "by alice <alice.smith@example.org>"
    )
    scrubbed = cp.scrub_text(text, home=home, user="alice")
    assert "alice" not in scrubbed
    assert "example.org" not in scrubbed
    assert "~" in scrubbed and "<user>" in scrubbed and "<email>" in scrubbed


def test_scrub_leaves_ordinary_text_alone() -> None:
    text = "git status; echo hello > notes.txt; Edit notes.txt replace one with two"
    assert cp.scrub_text(text, home=None, user=None) == text


def test_scrub_value_walks_nested_json() -> None:
    payload = {"tool_input": {"command": f"curl -H 'x: {SECRETS[2]}'"}, "list": [SECRETS[0]]}
    assert SECRETS[0] not in json.dumps(cp.scrub_value(payload, home=None, user=None))
    assert SECRETS[2] not in json.dumps(cp.scrub_value(payload, home=None, user=None))


# Hook mode.


def run_hook(stdin_text: str, out: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = cp.hook(out, stdin=io.StringIO(stdin_text), home="C:\\Users\\alice", user="alice")
    return code, capsys.readouterr().out


def test_hook_writes_scrubbed_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = {
        "session_id": "s-1",
        "transcript_path": "C:\\Users\\alice\\.claude\\projects\\x\\s-1.jsonl",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": f"echo {SECRETS[0]}"},
    }
    code, printed = run_hook(json.dumps(payload), tmp_path, capsys)
    assert code == 0
    assert printed == ""  # stdout from some hooks becomes agent context; never print.
    files = list(tmp_path.glob("*.json"))
    assert [f.name for f in files] == ["0001-PreToolUse-Bash.json"]
    text = files[0].read_text(encoding="utf-8")
    assert SECRETS[0] not in text and "alice" not in text
    record = json.loads(text)
    assert record["capture"]["seq"] == 1
    assert record["payload"]["hook_event_name"] == "PreToolUse"
    assert record["payload"]["transcript_path"].startswith("~")


def test_hook_numbers_files_in_arrival_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for event in ("UserPromptSubmit", "Stop"):
        run_hook(json.dumps({"hook_event_name": event, "prompt": "hi"}), tmp_path, capsys)
    assert sorted(f.name for f in tmp_path.glob("*.json")) == [
        "0001-UserPromptSubmit.json",
        "0002-Stop.json",
    ]


@pytest.mark.parametrize("bad", ["{not json", "", "[1, 2]", "\x00\x01"])
def test_hook_survives_bad_input(
    bad: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, printed = run_hook(bad, tmp_path, capsys)
    assert code == 0 and printed == ""
    [record_file] = tmp_path.glob("*.json")
    assert record_file.name == "0001-unparseable.json"
    record = json.loads(record_file.read_text(encoding="utf-8"))
    assert record["payload"] is None and record["capture"]["unparseable"] is True


def test_hook_exits_0_even_if_it_cannot_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    code, printed = run_hook('{"hook_event_name": "Stop"}', blocker, capsys)
    assert code == 0 and printed == ""


def test_event_names_are_safe_in_file_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_hook(json.dumps({"hook_event_name": "../../evil", "tool_name": "a/b"}), tmp_path, capsys)
    [record_file] = tmp_path.glob("*.json")
    assert record_file.parent == tmp_path
    assert "/" not in record_file.name and ".." not in record_file.name


# Setup mode.


def test_setup_creates_project_local_settings_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    target = tmp_path / "throwaway"
    fixtures = tmp_path / "fixtures"

    cp.setup(target, fixtures_root=fixtures, agent_version="9.9.9", events="all")

    assert list(fake_home.iterdir()) == []  # nothing written to the (fake) home folder
    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    assert set(hooks) == set(cp.EVENTS["all"])
    for event in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "PostToolBatch"):
        assert event in hooks
    command = hooks["PreToolUse"][0]["hooks"][0]["command"]
    assert Path(sys.executable).as_posix() in command  # absolute interpreter (D6)
    assert (fixtures / "claude-code" / "9.9.9").as_posix() in command
    assert hooks["PreToolUse"][0]["hooks"][0]["timeout"] <= 10
    assert (target / "CAPTURE.md").is_file()
    assert (target / "notes.txt").is_file()


def test_setup_core_events_are_a_subset(tmp_path: Path) -> None:
    cp.setup(tmp_path / "t", fixtures_root=tmp_path / "f", agent_version="1.0.0", events="core")
    settings = json.loads((tmp_path / "t" / ".claude" / "settings.json").read_text("utf-8"))
    assert set(settings["hooks"]) == set(cp.EVENTS["core"])
    assert set(cp.EVENTS["core"]) < set(cp.EVENTS["all"])


def test_setup_refuses_existing_settings(tmp_path: Path) -> None:
    target = tmp_path / "t"
    (target / ".claude").mkdir(parents=True)
    (target / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="already exists"):
        cp.setup(target, fixtures_root=tmp_path / "f", agent_version="1.0.0", events="all")
    assert (target / ".claude" / "settings.json").read_text(encoding="utf-8") == "{}"


def test_setup_refuses_folder_inside_this_repository(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="outside"):
        cp.setup(ROOT / "scratch" / "x", fixtures_root=tmp_path, agent_version="1", events="all")
    assert not (ROOT / "scratch" / "x").exists()


def test_setup_refuses_unsafe_version(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="version"):
        cp.setup(tmp_path / "t", fixtures_root=tmp_path, agent_version="../x", events="all")


def test_scrub_replaces_git_bash_form_of_home() -> None:
    scrubbed = cp.scrub_text("cd /c/Users/alice/proj", home="C:\\Users\\alice", user=None)
    assert scrubbed == "cd ~/proj"
