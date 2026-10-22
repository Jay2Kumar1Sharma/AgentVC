# Known issues (offline fallback)

Use this file only when an issue can't be raised on GitHub yet (no repository, or no network). Each entry must be turned into a GitHub issue as soon as possible, then removed from here. See `docs/master-plan.md` §13.3.

Format:

```text
## <component>: <short symptom>
- Found: <date>, commit <sha> on branch <branch>, while working on <task>
- Severity (suspected): S0 | S1 | S2 | S3; guarantees: G?
- Seen: <what happened, with output>
- Reproduce: <command or test>
- Environment: <OS, Python, git, agent and version>
```

## Open entries

## spikes: hook latency spike fails under PowerShell when `bash` on PATH is WSL's
- Found: 2026-10-22, commit 7d5ce03 on branch `alpha/session-3`, while running the end-of-session full suite with `./make.ps1 test`. Also fails on `discovery/session-2` (checked in a temporary worktree), so it predates session 3.
- Severity (suspected): S3; guarantees: none (development spike only).
- Seen: `tests/test_spike_hook_latency.py::test_spike_writes_every_measurement` fails: `subprocess.CalledProcessError: Command '['C:\\windows\\system32\\bash.EXE', '-c', '"D:/open_source/AgentFS/.venv/Scripts/python.exe" ".../minimal_handler.py"']' returned non-zero exit status 1`. `scripts/spikes/hook_latency.py` measures `hook_via_bash` with whatever `shutil.which("bash")` returns; in PowerShell that is the WSL launcher, which can't run a Windows interpreter path. Under MSYS2/Git Bash (`make test`) Git's bash comes first and the test passes.
- Reproduce: in Windows PowerShell with WSL installed, `./make.ps1 test`, or `python -m pytest tests/test_spike_hook_latency.py -k every_measurement`.
- Environment: Windows 11, Python 3.12.7, git 2.49.0.windows.1, `C:\windows\system32\bash.exe` (WSL) first on PowerShell's PATH.
- Possible fix: look for Git's bash next to `git` (the shell Claude Code uses on Windows), or skip `hook_via_bash` when the only bash is WSL's.

## recorder: an action Claude Code's own permission system denied shows as "may have run unrecorded"
- Found: 2026-10-22, commit 9eab05d on branch `alpha/session-4`, during the session-4 run with real Claude Code 2.1.302 (`results/spikes/2026-10-22/s4-real-claude-code-rollback/`). The behaviour predates session 4 (recorder and wiring, session 3).
- Severity (suspected): S3; guarantees: G3 (wording only: nothing is hidden, and nothing ran).
- Seen: an Edit that Claude Code itself denied (an untrusted workspace ignores the project's `permissions.allow`) produced a PreToolUse and no PostToolUse, so the step became a GAP "no post event (the tool may have run unrecorded)", and `inspect` adds `[not snapshotted: no snapshot after it]`. The tool never ran. Claude Code 2.1.302 sends a `PermissionDenied` event for this, which AgentVC doesn't subscribe to.
- Reproduce: in a throwaway project that Claude Code doesn't trust, `claude -p` asking for an Edit, with AgentVC's hooks installed; then `agentvc inspect latest`.
- Environment: Windows 11, Python 3.12.7, Claude Code 2.1.302.
- Possible fix: capture a real `PermissionDenied` payload into `tests/fixtures/hosts/claude-code/2.1.302/` (adapter tests use real fixtures only), subscribe to it in `wiring.py`, and close the pending step as DENIED (by the agent's permissions) instead of a GAP.
