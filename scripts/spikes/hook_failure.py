"""Spike for unknown U-04 and decision D2 (plan §7.1): what does Claude Code do when a
PreToolUse hook times out or crashes?

Each scenario gets a throwaway project with project-local hooks only:

- timeout   the PreToolUse hook sleeps far longer than its timeout
- crash     the PreToolUse hook raises (exit 1)
- block     the PreToolUse hook exits 2, the documented "block" signal (control case)

Claude Code is then asked to run `echo ran > tool_ran.txt` with Bash. Marker files show whether
the hook started, whether it ran to the end (was it killed?), whether the tool ran, and which
after-tool events arrived (PostToolUse / PostToolUseFailure).

Running Claude Code writes its usual session state (transcripts, ~/.claude.json); no settings
file outside the throwaway projects is touched. The parent session's CLAUDE* environment
variables are removed so the child runs as a normal session.

Usage:
    python scripts/spikes/hook_failure.py --claude claude --out results/spikes/2026-10-18
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

PRE_TIMEOUT_SECONDS = 3
HOOK_SLEEP_SECONDS = 30
PROMPT = "Run `echo ran > tool_ran.txt` with the Bash tool, then reply with the word done."

_PRE = """\
import sys, time
from pathlib import Path
here = Path(__file__).parent
sys.stdin.read()
(here / "pre_started").write_text(str(time.time()), encoding="utf-8")
{body}
(here / "pre_finished").write_text(str(time.time()), encoding="utf-8")
"""
SCENARIOS = {
    "timeout": f"time.sleep({HOOK_SLEEP_SECONDS})",
    "crash": 'raise RuntimeError("deliberate crash in a PreToolUse hook")',
    "block": 'print("blocked by the hook_failure spike", file=sys.stderr)\nsys.exit(2)',
}
_POST = """\
import json, sys
from pathlib import Path
event = json.loads(sys.stdin.read()).get("hook_event_name", "?")
with open(Path(__file__).parent / "post_events.txt", "a", encoding="utf-8") as f:
    f.write(event + "\\n")
"""


def setup(folder: Path, scenario: str) -> None:
    """Create the throwaway project for one scenario."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pre.py").write_text(_PRE.format(body=SCENARIOS[scenario]), encoding="utf-8")
    (folder / "post.py").write_text(_POST, encoding="utf-8")
    python = Path(sys.executable).as_posix()

    def handler(script: str, timeout: int) -> dict[str, Any]:
        command = f'"{python}" "{(folder / script).resolve().as_posix()}"'
        return {"type": "command", "command": command, "timeout": timeout}

    settings = {
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [handler("pre.py", PRE_TIMEOUT_SECONDS)]}],
            "PostToolUse": [{"matcher": "Bash", "hooks": [handler("post.py", 10)]}],
            "PostToolUseFailure": [{"matcher": "Bash", "hooks": [handler("post.py", 10)]}],
        }
    }
    (folder / ".claude").mkdir(exist_ok=True)
    (folder / ".claude" / "settings.json").write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )


def report(folder: Path) -> dict[str, Any]:
    """Read the marker files a scenario left behind."""
    events = folder / "post_events.txt"
    return {
        "pre_hook_started": (folder / "pre_started").exists(),
        "pre_hook_finished": (folder / "pre_finished").exists(),
        "tool_ran": (folder / "tool_ran.txt").exists(),
        "post_events": events.read_text(encoding="utf-8").split() if events.exists() else [],
    }


def run_scenario(folder: Path, scenario: str, claude: str) -> dict[str, Any]:
    setup(folder, scenario)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    command = [claude, "-p", PROMPT, "--setting-sources", "project", "--allowedTools", "Bash",
               "--output-format", "json"]  # fmt: skip
    start = time.perf_counter()
    result = subprocess.run(
        command, cwd=folder, env=env, capture_output=True, text=True, timeout=600,
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    wall = round(time.perf_counter() - start, 1)
    # A hook that wasn't killed finishes HOOK_SLEEP_SECONDS after it started; wait that long.
    started = folder / "pre_started"
    if started.exists():
        finish_by = float(started.read_text(encoding="utf-8")) + HOOK_SLEEP_SECONDS + 3
        time.sleep(max(0.0, finish_by - time.time()))
    try:
        reply = json.loads(result.stdout).get("result", "")
    except ValueError:
        reply = ""
    return {
        "scenario": scenario,
        **report(folder),
        "claude_exit_code": result.returncode,
        "wall_seconds": wall,
        "agent_reply": reply[:300],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--claude", default="claude", help="Claude Code executable")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenarios", nargs="+", default=sorted(SCENARIOS))
    args = parser.parse_args(argv)
    version = subprocess.run([args.claude, "--version"], capture_output=True, text=True, timeout=60)
    results = []
    with tempfile.TemporaryDirectory(prefix="agentvc-hook-failure-") as tmp:
        for scenario in args.scenarios:
            outcome = run_scenario(Path(tmp) / scenario, scenario, args.claude)
            results.append(outcome)
            print(json.dumps(outcome))
        shutil.rmtree(tmp, ignore_errors=True)
    data = {
        "meta": {"script": "scripts/spikes/hook_failure.py", "date": date.today().isoformat(),
                 "claude_code": version.stdout.strip(), "pre_timeout_seconds": PRE_TIMEOUT_SECONDS,
                 "hook_sleep_seconds": HOOK_SLEEP_SECONDS},
        "scenarios": results,
    }  # fmt: skip
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "hook_failure.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
