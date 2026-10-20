# AgentVC progress

Updated at the end of every session by the agent doing the work. Newest handoff first.

## Project decisions

| Decision | Value | Decided |
|---|---|---|
| Project name | **AgentVC** (CLI and Python package: `agentvc`) | 2026-10-17 |
| Licence | **Apache-2.0** (`LICENSE` = official text from apache.org; `NOTICE`) | 2026-10-17 |
| GitHub owner/repo | **github.com/Jay2Kumar1Sharma/AgentVC** | 2026-10-17 |
| Outside contributions | **Closed for now**; outside PRs auto-closed; opens after the first milestones (MAINTAINING.md "Contribution phases") | 2026-10-17 |
| Public / private docs | Private notes in `docs/private/` (git-ignored); everything else public | 2026-10-17 |
| Git repository initialised | yes (remote `origin` exists; nothing pushed by agents) | 2026-10-17 |

## Milestone tracker

| Milestone (plan §8) | Status |
|---|---|
| Discovery (week 1) | in progress: part 1 done (session 1, branch `discovery/session-1`), Claude Code payloads captured; part 2 next |
| v0.1-alpha (private) | not started |
| v0.1 (public) | not started |
| v0.2 Know your effects | not started |
| v0.3 Replay safely | not started |
| v0.4 Approve before it leaves | not started |
| v0.5 Beyond the workspace | not started |

## Handoff log

### 2026-10-18: Claude Code 2.1.302 payloads (PowerShell, PostToolBatch)
Branch **`discovery/capture-2.1.302`**, based on `discovery/session-1` (which is in PR #1, open and green, waiting for the maintainer's review and squash merge). 4 signed commits, **not pushed**.

**Done**
- The 2.1.72 install on PATH has no PowerShell tool and no `PostToolBatch`, so these were captured with the **2.1.302 binary bundled with the IDE extension** (`~\.antigravity-ide\extensions\anthropic.claude-code-2.1.302-win32-x64\resources\native-binary\claude.exe`), run by full path. Nothing was installed or updated.
- `scripts/capture_payloads.py`: event list for 2.1.302 (33 events, read from the binary, matching the docs); `setup` turns on the PowerShell tool in the throwaway project's own settings; the capture prompts live in one list (now with a failing PowerShell command) that the guide renders. Three new tests, each seen failing first.
- **90 fixtures** in `tests/fixtures/hosts/claude-code/2.1.302/` from 8 resumed `claude -p` turns; every file read before committing.
- `docs/testing/unknowns.md`: U-05 answered (PowerShell payload = Bash fields; failure → `PostToolUseFailure`); U-08, U-13, U-17 extended. `CHANGELOG.md` now lists the fixtures and the per-version event lists (the gap noted at the end of session 1).

**Findings**
- `PostToolBatch` fires once per model response, even for one call; it lists every call with its result as plain text, also fires after a failed command, and carries `agent_id` inside a subagent.
- 2.1.302 keeps the same `session_id` across `-p --resume` turns (2.1.72 didn't), except the first `InstructionsLoaded` of most resumes, which carries a one-off id.
- 2.1.302 adds `MessageDisplay` events with the assistant's reply text, plus `prompt_id`, `effort`, `duration_ms` and cost estimates in other events. Model output reaching hooks falls under the same storage rule as prompts.

**Full test suite (AGENTS.md step 8)**, Windows, Python 3.12.7, on this branch after the last code commit:
- `make fast` (MSYS2 make): **pass**, exit 0; ruff, ruff format, mypy (11 files) clean; 94 passed.
- `python -m pytest` with no marker filter (stand-in while `make test` is a placeholder): **94 passed, 0 failed, 0 skipped**, 0.73 s (1.1 s wall). The one warning is the intended `TraceabilityGap` report.

**Side effects outside the repository:** the capture runs added transcripts under `~/.claude/projects/C--Users-<user>-AppData-Local-Temp-agentvc-capture-2-1-302` and an entry in `~/.claude.json`. No settings file was changed. The throwaway folder is deleted.

**Open questions**
- Merge order: PR #1 first, then this branch as its own PR (or add it to PR #1 before merging)?
- Your terminal's `claude` is still 2.1.72 (file dated 2026-03-10), while the IDE extension is 2.1.302. Update it with `claude update` if you want them to match.

**Unrelated issues found:** none.

### 2026-10-18: new rule: full test suite at the end of every session
- **Rule added** (AGENTS.md step 8, plan §13.1 and §16.5, `docs/agent-prompts.md`): every session ends with the full local suite; results go in this log; related failures are fixed first, others recorded.
- **Applied retroactively to session 1** on `discovery/session-1`, Python 3.12.7, Windows:
  - `./make.ps1 fast` (ruff, mypy strict, pytest `-m "not slow"`): **pass**, exit 0, 1.6 s.
  - `python -m pytest` with no marker filter (stand-in for `make test`, still a placeholder): **91 passed, 0 failed, 0 skipped**, 0.72 s. The one warning is the intended `TraceabilityGap` report (TODO rows without tests).
- Note: GNU `make` isn't on every shell's PATH on this machine; `./make.ps1` works everywhere on Windows.
- **Next session (2) starts with task 0:** replace the `make test` placeholder with the real full suite.

### 2026-10-18: session 1, repo setup and Discovery part 1
Work is on branch **`discovery/session-1`** (signed commits, not merged). **Pushed on 2026-10-18 at the user's request**, together with `main` (pushed first so it stays the default branch).

**Done**
- **ADRs:** `docs/adr/0000-template.md`, ADR-0001 (name AgentVC), ADR-0002 (Apache-2.0).
- **Skeleton:** `pyproject.toml` (Python 3.11+, no runtime deps, `license = "Apache-2.0"`, dev extras), `agentvc/` with `python -m agentvc` printing the version, `tests/`, `eval/`, `results/`, `docs/design/`, `docs/prior-art/`, `docs/testing/`, `CHANGELOG.md`.
- **Task runners:** `Makefile` and `make.ps1` (Windows) with `fast` (ruff check + format check, mypy strict, pytest `-m "not slow"`); `test`/`arch`/`fuzz`/`chaos` are placeholders that **exit 2 with a message**, so they can't pass silently. A test keeps both runners' targets in sync.
- **CI:** `.github/workflows/fast.yml`, Linux/macOS/Windows × Python 3.11/3.13. **CI status: passing**: run 39068785382 on commit `d82e4b7`, all 6 jobs green (Linux, macOS, Windows × Python 3.11, 3.13), 91 tests each; `main` has no workflow yet, so CI runs only on the branch. Validated locally: a test parses every workflow and checks the matrix; `make fast` (MSYS2 make) and `make.ps1 fast` (PowerShell 5.1) both pass on Windows. **Re-verified from a fresh clone of the branch with fresh environments:** `make fast` passes on Python 3.11.12, 3.12.7 and 3.13.3 (91 tests each; 3.11 and 3.13 via uv-managed interpreters), and `make.ps1 fast` passes on 3.12.7. Linux and macOS verified by CI (above).
- **Testing drafts (§16.1):** `docs/testing/fmea.md` (38 rows), `hazards.md` (43 hazards across G1–G8), `unknowns.md` (17 unknowns with experiments), `tests/traceability.yml` (8 guarantees + 43 hazards + 38 FMEA + 76 attacks from §15.3). `tests/test_traceability.py` fails if a row names a missing test (seen failing on two planted bad references), if the docs' IDs and the YAML disagree, or if a guarantee is missing; `TODO` rows show as a `TraceabilityGap` warning with per-section counts. Only FM-DEV-01 / HZ-G6-05 (dev tooling privacy) have tests so far.
- **Usage study:** `scripts/usage_study.py` (aggregate counts only; privacy tests plant canary strings in prompts, commands, paths, output, folder and session names). Result in `results/usage-study/2026-10-18/`: 35 sessions; **26.5% of file changes went through the shell**, 17/31 tool-using sessions changed files via shell, 10/31 ran a destructive command (63 in all); external effects were rare (3 pushes, 0 deploys, 6 DB commands, 7 HTTP writes, MCP calls in 1 session).
- **Pivot check (recommendation, plan unchanged):** shell file changes are common and side effects rare in this sample, so keep v0.1 (file + shell undo) first. Final call after the outside conversations (one-person sample; heuristic classifier not checked against labelled commands; includes this session).
- **Payload capture:** `scripts/capture_payloads.py` (`setup`, `hook`, `summary`); hook prints nothing, always exits 0, scrubs tokens/assigned secrets/keys/emails/home/user name. Tests were checked against three deliberate breaks (they were written first but first run with the code). **Captured at the user's request:** 63 payloads from Claude Code 2.1.72 in `tests/fixtures/hosts/claude-code/2.1.72/` (all files read before committing), via 7 resumed `claude -p` turns in a throwaway folder with `--setting-sources project` and the parent session's `CLAUDE*` environment variables removed. Covered: Bash, Edit, Read, Write, ToolSearch, a parallel Bash batch, an Explore subagent (SubagentStart/Stop), a failing command (PostToolUseFailure), SessionStart/End, Stop, UserPromptSubmit, InstructionsLoaded. **Not captured:** PowerShell (2.1.72 offered no PowerShell tool) and PostToolBatch (doesn't exist in 2.1.72).

**Decisions made (small, reversible; tell me if you disagree)**
- Feature branch per session instead of committing to `main` (plan §13.1 squash-merge workflow).
- Dev tools live in a git-ignored `.venv`; **PyYAML** added as a dev-only dependency (test parsing of YAML); runtime stays stdlib-only.
- ruff is limited to Python files (newer ruff also reformats code blocks inside Markdown).
- `scripts/` is a package (`scripts/__init__.py`) so tests can import the scripts; pytest uses `pythonpath = ["."]`.
- Capture hooks exclude `WorktreeCreate`/`WorktreeRemove` (a hook there replaces Claude Code's worktree handling); `--events core` is a fallback if 2.1.72 rejects newer event names.
- `capture_payloads.py setup` defaults to `--events auto`: a known per-version event list (2.1.72's), else all.

**Findings from the capture** (details in `docs/testing/unknowns.md`)
- **Claude Code 2.1.72 registers no hooks at all, silently, if the settings name an event it doesn't know** (found when the first capture recorded nothing; `--debug-file` showed "Found 0 hook matchers"). New FMEA row FM-INS-03 and hazard HZ-G2-09: AgentVC's installer must write only events the installed version supports, and `agentvc check` must prove a hook fires.
- Failed commands fire `PostToolUseFailure` instead of `PostToolUse`; the adapter must pair it with the Pre event.
- Hooks for parallel tool calls run concurrently (two Post hooks 4 ms apart, separate processes): the ledger needs locking.
- Every `claude -p --resume` turn had a new `session_id`: run identity can't rely on `session_id` alone (U-17).
- Edit/Read/Write payloads include whole file contents: scrub before storing; payload size grows with file size.

**Side effects outside the repository (from the capture runs):** transcripts for the throwaway folders under `~/.claude/projects/C--Users-<user>-AppData-Local-Temp-agentvc-capture-*` and their entries in `~/.claude.json`. No settings file was changed. They're the user's to delete; until then a re-run of the usage study counts those ~10 test sessions.

**Open questions**
- Merge `discovery/session-1` into `main`: fast-forward (keeps the commits) or squash via a pull request (plan §13.1)? Once `fast.yml` is on `main`, it can become a required check (MAINTAINING.md "Repository settings").
- Is PyYAML acceptable as a dev dependency?
- Outside conversations (5+ people, `docs/user-conversations.md`) are still needed for the pivot check.

**Next (session 2):** start with spike S1/D1 (hook latency on Windows), then D2–D7 ADRs, prior-art notes, architecture skeleton. Still open from the capture: U-04 (hook timeout behaviour), U-07 (`/rewind`), U-14 (interactive vs `-p`), U-17 (session ids on interactive resume), PowerShell payloads.

**Unrelated issues found:** none.

### 2026-10-17: full evaluation before first push
- Checked mechanically: links, anchors and § references in all public Markdown (clean); all 9 Mermaid diagrams render with mermaid-cli 11.4.2; all GitHub YAML parses; label script passes `bash -n`; LICENSE matches apache.org's SHA-256; `docs/private/` and local settings are git-ignored.
- Fixed: branch protection that would lock out a sole maintainer; Dependabot config added; PR auto-close workflow given `issues: write`; G6 now covers MCP-proxy forwarding; playground is a `playground/` folder everywhere; research docs marked as input superseded by the plan where they disagree; `docs/known-issues.md` created; agent prompts say CI only runs after the user pushes; outside-user validation added to Discovery; plan bumped to v0.4 with a changelog.
- Still open for the user: enable GitHub settings after creating the repo (MAINTAINING.md "Repository settings").
- Code-of-conduct reports go through GitHub's "Report to repository admins" for now; a contact email is deferred until AgentVC is released for use (item on the release checklist).

### 2026-10-17: planning complete, no code yet
- Plan, research, contribution guide, maintainer guide, GitHub templates, label script and agent instructions exist.
- **Next:** session 1 in `docs/agent-prompts.md` (repo setup + Discovery part 1).
- Agent rules: `AGENTS.md` (loaded via `CLAUDE.md` in Claude Code).
- Decided: name AgentVC, Apache-2.0, owner Jay2Kumar1Sharma, contributions closed for now, public/private split.
- Code-of-conduct contact email deferred until release (reports via GitHub's "Report to repository admins" meanwhile).
- Unrelated issues raised: none.
