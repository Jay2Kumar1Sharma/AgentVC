"""Usage study (plan §8 Discovery): how often do coding agents change files through the shell,
push, install packages, deploy, run database commands, or call HTTP and MCP?

Scans Claude Code transcripts (default ``~/.claude/projects/**/*.jsonl``) and reports
AGGREGATE COUNTS ONLY, per session and in total.

Privacy (AGENTS.md, plan §8): prompts, commands, tool output, file paths, project names and
session ids are read in memory, classified and dropped. They are never written or printed.
Sessions appear only as anonymous rows of numbers. The result is checked by
``assert_aggregate_only`` before it is written: whitelisted keys and numbers, nothing else.

The shell classification is a heuristic (regular expressions over each command); see
``classify_shell`` and the tests for exactly what counts.

Usage:
    python scripts/usage_study.py                      # print a summary
    python scripts/usage_study.py --out results/usage-study/2026-10-18
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import re
import statistics
import sys
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_VERSION = 1
RESULT_FILE = "usage-study.json"
DEFAULT_ROOT = "~/.claude/projects"

SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
WEB_FETCH_TOOLS = frozenset({"WebFetch"})
MCP_PREFIX = "mcp__"

# Per-tool-call counters; each tool call adds 0 or 1 to each.
SHELL_CATEGORIES = (
    "shell_file_modifications",
    "shell_destructive",
    "git_pushes",
    "package_installs",
    "deploys",
    "db_commands",
    "http_calls",
    "http_writes",
)
COUNT_KEYS = (
    "tool_calls",
    "shell_commands",
    "edit_tool_calls",
    "mcp_calls",
    "web_fetch_calls",
    *SHELL_CATEGORIES,
)
EXTERNAL_EFFECT_KEYS = (
    "git_pushes",
    "package_installs",
    "deploys",
    "db_commands",
    "http_writes",
    "mcp_calls",
)
STAT_KEYS = ("mean", "median", "p90", "max")
TOP_LEVEL_KEYS = (
    "files_scanned",
    "unreadable_files",
    "subagent_files",
    "sessions",
    "sessions_with_tool_calls",
    "sessions_with_external_effects",
    "unparseable_lines",
    "duplicate_tool_calls_skipped",
    "totals",
    "sessions_with_any",
    "per_session_stats",
    "per_session",
    "derived",
)
DERIVED_KEYS = ("shell_share_of_file_changes",)
ALLOWED_STRINGS = frozenset(
    (*COUNT_KEYS, *STAT_KEYS, *TOP_LEVEL_KEYS, *DERIVED_KEYS, "subagent_files")
)
META_KEYS = ("script", "script_version", "generated", "python", "os", "source")

# --- Shell command classification -------------------------------------------------------------

# Start of a simple command: line start or after a separator, skipping sudo/env/VAR=x prefixes.
_START = r"(?:^|[;&|(\n`{]|\$\()\s*(?:(?:sudo|env|time|exec|nohup|command)\s+|\w+=\S*\s+)*"
_PY = r"(?:(?:python[\d.]*|py)(?:\s+-[\d.]+)?\s+-m\s+)?"

_PATTERNS: dict[str, list[str]] = {
    "shell_file_modifications": [
        _START + r"(?:rm|rmdir|mv|cp|touch|mkdir|ln|truncate|shred|unlink|chmod|chown)\b",
        _START + r"(?:del|erase|ren|move|copy|rd)\b",
        r"\bsed\s+(?:-[a-zA-Z]*i|--in-place)",
        r"\bperl\s+-[a-zA-Z]*i",
        r"(?:^|\|)\s*tee\b",
        r"\bdd\b[^\n]*\bof=",
        # Output redirection, but not 2>&1, >/dev/null, >$null, NUL, =>, >=, ->.
        r"(?<![<>&\d=-])>>?(?![&=])\s*(?!/dev/null\b|\$null\b|nul\b|NUL\b)[^\s&|;>]",
        r"\bgit\s+(?:checkout|switch|restore|reset\s+--hard|clean|stash|apply|am|merge|rebase"
        r"|pull|cherry-pick|revert|rm|mv)\b",
        r"\bunzip\b",
        r"\btar\s+-?[a-zA-Z]*x",
        r"(?i)\b(?:Remove-Item|Move-Item|Copy-Item|Set-Content|Add-Content|Out-File|New-Item"
        r"|Rename-Item|Clear-Content|Expand-Archive)\b",
    ],
    "shell_destructive": [
        _START + r"rm\s+(?:-[a-zA-Z]*[rRf][a-zA-Z]*|--recursive|--force)\b",
        r"(?i)\bRemove-Item\b[^;|&\n]*\s-(?:Recurse|Force)\b",
        r"(?i)\b(?:rd|rmdir)\s+/s\b",
        r"\bfind\b[^\n]*\s-delete\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-[a-zA-Z]*f",
        r"\bgit\s+checkout\s+(?:--\s+)?\.(?:\s|$)",
        r"\bgit\s+restore\s+(?:--\S+\s+)*\.(?:\s|$)",
        r"\bgit\s+push\b[^;&|\n]*\s(?:--force(?:-with-lease)?|-f)\b",
        r"\bgit\s+branch\s+-D\b",
        r"\bgit\s+stash\s+(?:drop|clear)\b",
    ],
    "git_pushes": [
        r"\bgit\s+(?:-[Cc]\s+\S+\s+)*push\b(?![^;&|\n]*--dry-run)",
    ],
    "package_installs": [
        _PY + r"\b(?:pip[\d.]*|uv\s+pip)\s+install\b",
        r"\b(?:pipx|gem|brew|choco|winget|scoop)\s+install\b",
        r"\buv\s+(?:add|sync|tool\s+install)\b",
        r"\bpoetry\s+(?:add|install)\b",
        r"\b(?:conda|mamba)\s+(?:install|create)\b",
        r"\b(?:npm|pnpm|bun)\s+(?:install|i|ci|add)\b",
        r"\byarn\s+(?:add|install)\b",
        r"\bcargo\s+(?:install|add)\b",
        r"\bgo\s+(?:install|get)\b",
        r"\bbundle\s+install\b",
        r"\bcomposer\s+(?:install|require)\b",
        r"\bapt(?:-get)?\s+install\b",
        r"\bdotnet\s+add\b[^\n]*\bpackage\b",
        r"(?i)\bInstall-(?:Module|Package)\b",
    ],
    "deploys": [
        r"\bvercel\s+(?:deploy|--prod)\b",
        r"\b(?:netlify|firebase|cdk|sam|eb)\s+deploy\b",
        r"\bfly(?:ctl)?\s+deploy\b",
        r"\b(?:serverless|sls)\s+deploy\b",
        r"\bwrangler\s+(?:deploy|publish)\b",
        r"\bkubectl\s+(?:apply|create|delete|replace|patch|rollout|scale|set)\b",
        r"\bhelm\s+(?:install|upgrade|uninstall|rollback)\b",
        r"\b(?:terraform|tofu)\s+(?:apply|destroy)\b",
        r"\bpulumi\s+(?:up|destroy)\b",
        r"\b(?:gcloud|az)\s[^\n]*\bdeploy\b",
        r"\baws\s[^\n]*\b(?:deploy|cloudformation|s3\s+(?:cp|sync|rm|mv)|lambda\s+update)",
        r"\bdocker\s+push\b",
        r"\b(?:npm|yarn|pnpm|cargo)\s+publish\b",
        r"\btwine\s+upload\b",
        r"\bgem\s+push\b",
        r"\bgh\s+release\s+create\b",
        r"\brailway\s+up\b",
        r"\bheroku\s+(?:deploy|container:release|releases:rollback)\b",
    ],
    "db_commands": [
        _START + r"(?:psql|mysql|mariadb|sqlite3|mongosh|mongo|redis-cli|cqlsh|sqlcmd"
        r"|clickhouse-client|duckdb)\b",
        r"(?i)\bInvoke-Sqlcmd\b",
        r"\bprisma\s+(?:migrate|db)\b",
        r"\balembic\s+(?:upgrade|downgrade|stamp)\b",
        r"\bmanage\.py\s+(?:migrate|flush|loaddata|dbshell|sqlflush)\b",
        r"\b(?:rails|rake)\s+db:",
        r"\bknex\s+migrate",
        r"\bsequelize\s+db:",
        r"\b(?:flyway|liquibase)\b",
        r"\bsupabase\s+db\b",
        r"\bdbt\s+(?:run|seed|build|snapshot)\b",
        r"\bdrizzle-kit\s+(?:push|migrate)\b",
        r"\btypeorm\s+migration",
        r"\bdiesel\s+migration\b",
    ],
    "http_calls": [
        _START + r"(?:curl|wget|http|https|xh|iwr|irm)\s",
        r"(?i)\bInvoke-(?:WebRequest|RestMethod)\b",
    ],
}
_HTTP_WRITE = re.compile(
    r"(?i)(?:\s-X\s*(?:POST|PUT|PATCH|DELETE)\b|--request\s+(?:POST|PUT|PATCH|DELETE)\b"
    r"|\s(?:-d|-F|-T)(?:\s|['\"{@])|\s--(?:data[\w-]*|form|upload-file|json)\b"
    r"|-Method\s+(?:Post|Put|Patch|Delete)\b|\s-Body\s|--post-(?:data|file)"
    r"|--method=(?:POST|PUT|PATCH|DELETE))"
)
_COMPILED = {
    category: [re.compile(p, re.MULTILINE) for p in patterns]
    for category, patterns in _PATTERNS.items()
}
# Heredoc and PowerShell here-string bodies are data (commit messages, file contents), not commands.
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n\s*\1\b", re.DOTALL)
_HERESTRING = re.compile(r"@(['\"])\n.*?\n\1@", re.DOTALL)


def _strip_data(command: str) -> str:
    command = _HEREDOC.sub(lambda m: "<<" + m.group(1) + m.group(2), command)
    return _HERESTRING.sub("@''@", command)


def classify_shell(command: str) -> frozenset[str]:
    """Return the categories (from SHELL_CATEGORIES) one shell command falls into."""
    text = _strip_data(command)
    found = {
        category
        for category, patterns in _COMPILED.items()
        if any(p.search(text) for p in patterns)
    }
    if "http_calls" in found and _HTTP_WRITE.search(text):
        found.add("http_writes")
    return frozenset(found)


# --- Transcript scanning ----------------------------------------------------------------------


def _session_key(path: Path) -> tuple[str, str, bool]:
    """(project folder, session id, is a subagent file). Kept in memory only."""
    if path.parent.name == "subagents":
        return str(path.parents[2]), path.parents[1].name, True
    return str(path.parent), path.stem, False


def _tool_uses(record: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(record, dict):
        return
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block


def _count_tool_use(block: dict[str, Any], counts: dict[str, int]) -> None:
    name = block.get("name")
    tool_input = block.get("input")
    counts["tool_calls"] += 1
    if not isinstance(name, str):
        return
    if name in SHELL_TOOLS:
        counts["shell_commands"] += 1
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if isinstance(command, str):
            for category in classify_shell(command):
                counts[category] += 1
    elif name in EDIT_TOOLS:
        counts["edit_tool_calls"] += 1
    elif name in WEB_FETCH_TOOLS:
        counts["web_fetch_calls"] += 1
    elif name.startswith(MCP_PREFIX):
        counts["mcp_calls"] += 1


def _stats(values: list[int]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "p90": 0.0, "max": 0}
    ordered = sorted(values)
    return {
        "mean": round(statistics.fmean(ordered), 2),
        "median": float(statistics.median(ordered)),
        "p90": float(ordered[math.ceil(0.9 * len(ordered)) - 1]),
        "max": ordered[-1],
    }


def scan(roots: Iterable[Path]) -> dict[str, Any]:
    """Scan transcripts under ``roots`` and return aggregate counts only."""
    sessions: dict[tuple[str, str], dict[str, int]] = {}
    seen_ids: set[str] = set()
    files = unreadable = subagent_files = unparseable = duplicates = 0

    for root in roots:
        for path in sorted(root.rglob("*.jsonl")):
            project, session_id, is_subagent = _session_key(path)
            counts = sessions.setdefault(
                (project, session_id), {key: 0 for key in (*COUNT_KEYS, "subagent_files")}
            )
            files += 1
            if is_subagent:
                subagent_files += 1
                counts["subagent_files"] += 1
            try:
                with path.open(encoding="utf-8", errors="replace") as lines:
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                        except ValueError:
                            unparseable += 1
                            continue
                        for block in _tool_uses(record):
                            tool_id = block.get("id")
                            if isinstance(tool_id, str):
                                if tool_id in seen_ids:
                                    duplicates += 1
                                    continue
                                seen_ids.add(tool_id)
                            _count_tool_use(block, counts)
            except OSError:
                unreadable += 1

    rows = sorted(sessions.values(), key=lambda c: tuple(c[k] for k in COUNT_KEYS), reverse=True)
    totals = {key: sum(row[key] for row in rows) for key in COUNT_KEYS}
    file_changes = totals["shell_file_modifications"] + totals["edit_tool_calls"]
    result: dict[str, Any] = {
        "files_scanned": files,
        "unreadable_files": unreadable,
        "subagent_files": subagent_files,
        "sessions": len(rows),
        "sessions_with_tool_calls": sum(1 for row in rows if row["tool_calls"]),
        "sessions_with_external_effects": sum(
            1 for row in rows if any(row[k] for k in EXTERNAL_EFFECT_KEYS)
        ),
        "unparseable_lines": unparseable,
        "duplicate_tool_calls_skipped": duplicates,
        "totals": totals,
        "sessions_with_any": {key: sum(1 for row in rows if row[key]) for key in COUNT_KEYS},
        "per_session_stats": {key: _stats([row[key] for row in rows]) for key in COUNT_KEYS},
        "per_session": rows,
        "derived": {
            "shell_share_of_file_changes": (
                round(totals["shell_file_modifications"] / file_changes, 3)
                if file_changes
                else None
            ),
        },
    }
    assert_aggregate_only(result)
    return result


def assert_aggregate_only(value: Any) -> None:
    """Raise ValueError unless ``value`` holds only whitelisted keys, numbers and None."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in ALLOWED_STRINGS:
                # The offending key is not echoed: it could be transcript content.
                raise ValueError("result contains a key that is not an allowed key")
            assert_aggregate_only(item)
    elif isinstance(value, list):
        for item in value:
            assert_aggregate_only(item)
    elif isinstance(value, str):
        raise ValueError("result contains a string value; only numbers are allowed")
    elif value is not None and not isinstance(value, int | float):
        raise ValueError("result contains a value that is not a number")


# --- Command line -----------------------------------------------------------------------------


def _summary(result: dict[str, Any]) -> str:
    sessions = result["sessions"]
    lines = [
        f"Sessions: {sessions} ({result['sessions_with_tool_calls']} with tool calls), "
        f"files: {result['files_scanned']} ({result['subagent_files']} subagent), "
        f"unparseable lines: {result['unparseable_lines']}",
        f"Sessions with an external effect: {result['sessions_with_external_effects']}",
        "",
        f"{'category':<26}{'total':>8}{'sessions':>10}{'mean':>8}{'p90':>7}{'max':>7}",
    ]
    for key in COUNT_KEYS:
        stats = result["per_session_stats"][key]
        lines.append(
            f"{key:<26}{result['totals'][key]:>8}{result['sessions_with_any'][key]:>10}"
            f"{stats['mean']:>8}{stats['p90']:>7}{stats['max']:>7}"
        )
    share = result["derived"]["shell_share_of_file_changes"]
    lines += ["", f"Share of file changes made through the shell: {share}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        help="transcript folder to scan (repeatable; default ~/.claude/projects)",
    )
    parser.add_argument("--out", type=Path, help=f"folder to write {RESULT_FILE} into")
    args = parser.parse_args(argv)

    roots: list[Path] = args.root or [Path(DEFAULT_ROOT).expanduser()]
    result = scan(roots)
    if not result["files_scanned"]:
        print("No transcripts found.", file=sys.stderr)
        return 1
    print(_summary(result))

    if args.out:
        meta = {
            "script": "scripts/usage_study.py",
            "script_version": SCRIPT_VERSION,
            "generated": date.today().isoformat(),
            "python": platform.python_version(),
            "os": platform.system(),
            "source": f"{DEFAULT_ROOT}/**/*.jsonl" if not args.root else "custom --root",
        }
        assert set(meta) == set(META_KEYS)
        args.out.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"meta": meta, **result}, indent=2) + "\n"
        (args.out / RESULT_FILE).write_text(text, encoding="utf-8", newline="\n")
        print(f"\nWrote {RESULT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
