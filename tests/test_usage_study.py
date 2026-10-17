"""scripts/usage_study.py: correct aggregate counts, and nothing from the transcripts leaks out.

The privacy tests plant canary strings everywhere a transcript can carry private data (prompts,
commands, file paths, tool output, project folder and session names) and check that none of them
reaches the written result or stdout (FM-DEV-01, HZ-G6-05).
"""

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import usage_study

CANARIES = {
    "prompt": "CANARY_PROMPT_7f3a",
    "command": "CANARY_CMD_91b2",
    "path": "CANARY_PATH_55e1",
    "output": "CANARY_OUTPUT_aa12",
    "project": "CANARY_DIR_0c4d",
    "session": "CANARY_SESSION_e9d0",
    "mcp": "CANARY_MCP_SERVER_3b7c",
}


def tool_use(tool_id: str, name: str, **tool_input: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        },
    }


def user_prompt(text: str) -> dict[str, Any]:
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result(tool_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}],
        },
    }


def write_jsonl(path: Path, records: list[Any], extra_lines: tuple[str, ...] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in records] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def transcripts(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    project = root / f"C--Users-someone-{CANARIES['project']}"
    session = f"{CANARIES['session']}-1111"
    c = CANARIES
    write_jsonl(
        project / f"{session}.jsonl",
        [
            user_prompt(f"please clean up {c['prompt']}"),
            tool_use("t1", "Bash", command=f"rm -rf build/{c['command']}"),
            tool_result("t1", f"removed {c['output']}"),
            tool_use("t2", "Bash", command="git push origin main"),
            tool_use("t3", "Edit", file_path=f"/home/someone/{c['path']}.py", old_string="a"),
            tool_use("t4", f"mcp__{c['mcp']}__send_message", text=c["prompt"]),
            tool_use("t5", "PowerShell", command=f"Remove-Item -Recurse {c['path']}"),
            # The same tool_use repeated on another line (streamed message) counts once.
            tool_use("t2", "Bash", command="git push origin main"),
        ],
        extra_lines=("{not json", ""),
    )
    # A subagent transcript belongs to its parent session.
    write_jsonl(
        project / session / "subagents" / "agent-1.jsonl",
        [tool_use("s1", "Bash", command="pip install requests")],
    )
    write_jsonl(
        project / "second-session.jsonl",
        [
            user_prompt(c["prompt"]),
            tool_use("u1", "Bash", command="ls -la"),
            tool_use("u2", "WebFetch", url=f"https://example.com/{c['path']}"),
            tool_use("u3", "Bash", command=f"curl -X POST https://api.example.com/{c['path']}"),
        ],
    )
    return root


# Classification of shell commands, in both directions.


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("rm -rf build", "shell_file_modifications"),
        ("echo hi > notes.txt", "shell_file_modifications"),
        ("sed -i 's/a/b/' x.py", "shell_file_modifications"),
        ("Remove-Item -Recurse dist", "shell_file_modifications"),
        ("Set-Content -Path a.txt -Value 1", "shell_file_modifications"),
        ("rm -rf build", "shell_destructive"),
        ("git reset --hard HEAD~1", "shell_destructive"),
        ("git clean -fdx", "shell_destructive"),
        ("Remove-Item -Recurse -Force dist", "shell_destructive"),
        ("cd repo && git push --force origin main", "git_pushes"),
        ("pip install requests", "package_installs"),
        ("python -m pip install -e .", "package_installs"),
        ("npm ci", "package_installs"),
        ("winget install Git.Git", "package_installs"),
        ("vercel deploy --prod", "deploys"),
        ("terraform apply -auto-approve", "deploys"),
        ("kubectl apply -f k8s/", "deploys"),
        ("npm publish", "deploys"),
        ("docker push registry/app:1", "deploys"),
        ("psql -c 'select 1'", "db_commands"),
        ("npx prisma migrate deploy", "db_commands"),
        ("python manage.py migrate", "db_commands"),
        ("curl https://example.com", "http_calls"),
        ("Invoke-RestMethod -Uri https://x", "http_calls"),
        ("curl -X POST -d '{}' https://example.com", "http_writes"),
        ("curl --data a=1 https://example.com", "http_writes"),
    ],
)
def test_classify_detects(command: str, category: str) -> None:
    assert category in usage_study.classify_shell(command)


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("ls -la", "shell_file_modifications"),
        ("git status 2>&1", "shell_file_modifications"),
        ("pytest -q > /dev/null", "shell_file_modifications"),
        ("Get-ChildItem 2>$null", "shell_file_modifications"),
        ("python -c 'print(1 >= 0)'", "shell_file_modifications"),
        ("git log --oneline", "git_pushes"),
        ("git stash push -m wip", "git_pushes"),
        ("pip list", "package_installs"),
        ("npm run build", "package_installs"),
        ("curl https://example.com", "http_writes"),
        ("rm notes.txt", "shell_destructive"),
        ("grep -r deploy .", "deploys"),
        ("git push --dry-run", "git_pushes"),
    ],
)
def test_classify_ignores(command: str, category: str) -> None:
    assert category not in usage_study.classify_shell(command)


# Scanning: counts, sessions, robustness.


def test_scan_counts(transcripts: Path) -> None:
    result = usage_study.scan([transcripts])
    totals = result["totals"]
    assert result["files_scanned"] == 3
    assert result["sessions"] == 2
    assert result["unparseable_lines"] == 1
    assert result["duplicate_tool_calls_skipped"] == 1
    assert totals["tool_calls"] == 9
    assert totals["shell_commands"] == 6
    assert totals["edit_tool_calls"] == 1
    assert totals["mcp_calls"] == 1
    assert totals["web_fetch_calls"] == 1
    assert totals["shell_file_modifications"] == 2
    assert totals["shell_destructive"] == 2
    assert totals["git_pushes"] == 1
    assert totals["package_installs"] == 1
    assert totals["http_calls"] == 1
    assert totals["http_writes"] == 1
    assert totals["deploys"] == 0
    assert totals["db_commands"] == 0
    sessions = sorted(result["per_session"], key=lambda s: s["tool_calls"])
    assert [s["tool_calls"] for s in sessions] == [3, 6]
    assert sessions[1]["subagent_files"] == 1


# Privacy: aggregate counts only.


def all_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [k, *all_strings(v)]]
    if isinstance(value, list):
        return [s for v in value for s in all_strings(v)]
    return [value] if isinstance(value, str) else []


def test_written_result_contains_no_transcript_data(
    transcripts: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    assert usage_study.main(["--root", str(transcripts), "--out", str(out)]) == 0
    written = (out / usage_study.RESULT_FILE).read_text(encoding="utf-8")
    printed = capsys.readouterr().out
    for text in (written, printed):
        for canary in CANARIES.values():
            assert canary not in text
        assert str(tmp_path) not in text
        assert "someone" not in text
    assert sorted(p.name for p in out.iterdir()) == [usage_study.RESULT_FILE]


def test_result_holds_only_whitelisted_strings_and_numbers(transcripts: Path) -> None:
    result = usage_study.scan([transcripts])
    for text in all_strings(result):
        assert text in usage_study.ALLOWED_STRINGS, text
    usage_study.assert_aggregate_only(result)


def test_assert_aggregate_only_rejects_foreign_strings() -> None:
    with pytest.raises(ValueError, match="not an allowed key"):
        usage_study.assert_aggregate_only({"totals": {"rm -rf secret": 1}})
    with pytest.raises(ValueError, match="string value"):
        usage_study.assert_aggregate_only({"sessions": "prompt text"})
