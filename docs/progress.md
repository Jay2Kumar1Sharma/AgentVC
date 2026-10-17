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
| Discovery (week 1) | in progress: part 1 done (session 1, branch `discovery/session-1`); payload capture awaiting the user; part 2 next |
| v0.1-alpha (private) | not started |
| v0.1 (public) | not started |
| v0.2 Know your effects | not started |
| v0.3 Replay safely | not started |
| v0.4 Approve before it leaves | not started |
| v0.5 Beyond the workspace | not started |

## Handoff log

### 2026-10-18: session 1, repo setup and Discovery part 1
Work is on branch **`discovery/session-1`** (11 small signed commits, not merged, not pushed).

**Done**
- **ADRs:** `docs/adr/0000-template.md`, ADR-0001 (name AgentVC), ADR-0002 (Apache-2.0).
- **Skeleton:** `pyproject.toml` (Python 3.11+, no runtime deps, `license = "Apache-2.0"`, dev extras), `agentvc/` with `python -m agentvc` printing the version, `tests/`, `eval/`, `results/`, `docs/design/`, `docs/prior-art/`, `docs/testing/`, `CHANGELOG.md`.
- **Task runners:** `Makefile` and `make.ps1` (Windows) with `fast` (ruff check + format check, mypy strict, pytest `-m "not slow"`); `test`/`arch`/`fuzz`/`chaos` are placeholders that **exit 2 with a message**, so they can't pass silently. A test keeps both runners' targets in sync.
- **CI:** `.github/workflows/fast.yml`, Linux/macOS/Windows × Python 3.11/3.13. **CI status: not yet run** (nothing pushed). Validated locally: a test parses every workflow and checks the matrix; `make fast` (MSYS2 make) and `make.ps1 fast` (PowerShell 5.1) both pass on Windows with **Python 3.12.7 only**. 3.11 and 3.13 aren't installed here, so they're untested until CI runs.
- **Testing drafts (§16.1):** `docs/testing/fmea.md` (37 rows), `hazards.md` (42 hazards across G1–G8), `unknowns.md` (16 unknowns with experiments), `tests/traceability.yml` (8 guarantees + 42 hazards + 37 FMEA + 76 attacks from §15.3). `tests/test_traceability.py` fails if a row names a missing test (seen failing on two planted bad references), if the docs' IDs and the YAML disagree, or if a guarantee is missing; `TODO` rows show as a `TraceabilityGap` warning with per-section counts. Only FM-DEV-01 / HZ-G6-05 (dev tooling privacy) have tests so far.
- **Usage study:** `scripts/usage_study.py` (aggregate counts only; privacy tests plant canary strings in prompts, commands, paths, output, folder and session names). Result in `results/usage-study/2026-10-18/`: 35 sessions; **26.5% of file changes went through the shell**, 17/31 tool-using sessions changed files via shell, 10/31 ran a destructive command (63 in all); external effects were rare (3 pushes, 0 deploys, 6 DB commands, 7 HTTP writes, MCP calls in 1 session).
- **Pivot check (recommendation, plan unchanged):** shell file changes are common and side effects rare in this sample, so keep v0.1 (file + shell undo) first. Final call after the outside conversations (one-person sample; heuristic classifier not checked against labelled commands; includes this session).
- **Payload capture:** `scripts/capture_payloads.py` (`setup`, `hook`, `summary`); hook prints nothing, always exits 0, scrubs tokens/assigned secrets/keys/emails/home/user name. Tests were checked against three deliberate breaks (they were written first but first run with the code). A throwaway project was created at `%TEMP%\agentvc-capture-2026-10-18` (all 31 events; Claude Code 2.1.72). **No payloads captured yet:** running Claude Code, even in a throwaway folder, writes to `~/.claude.json` and `~/.claude/projects/`, so the session is left to the user (steps below).

**Decisions made (small, reversible; tell me if you disagree)**
- Feature branch per session instead of committing to `main` (plan §13.1 squash-merge workflow).
- Dev tools live in a git-ignored `.venv`; **PyYAML** added as a dev-only dependency (test parsing of YAML); runtime stays stdlib-only.
- ruff is limited to Python files (newer ruff also reformats code blocks inside Markdown).
- `scripts/` is a package (`scripts/__init__.py`) so tests can import the scripts; pytest uses `pythonpath = ["."]`.
- Capture hooks exclude `WorktreeCreate`/`WorktreeRemove` (a hook there replaces Claude Code's worktree handling); `--events core` is a fallback if 2.1.72 rejects newer event names.

**For the user: capture payloads (about 5 minutes)**
1. Keep `D:\open_source\AgentFS\.venv` in place (the hooks call its Python by absolute path).
2. In a new terminal: `cd %TEMP%\agentvc-capture-2026-10-18`, then `claude`. Accept the folder-trust prompt.
3. Run `/hooks` and check the events are listed. If Claude Code reports a settings error or lists none, delete the folder and re-run `python scripts/capture_payloads.py setup %TEMP%\agentvc-capture-2026-10-18 --events core` from the repository.
4. Send the 7 prompts in `CAPTURE.md` (Bash, PowerShell, Edit, Write, a parallel batch, a subagent, a failing command), then `/exit`.
5. In the repository: `.venv\Scripts\python scripts/capture_payloads.py summary tests/fixtures/hosts/claude-code/2.1.72`, **read every captured file**, then commit them (`test(fixtures): Claude Code 2.1.72 hook payloads`). Delete the throwaway folder.

**Open questions**
- Merge `discovery/session-1` into `main`: fast-forward (keeps the 11 commits) or squash (plan §13.1)? And push when you're ready; CI runs then.
- Is PyYAML acceptable as a dev dependency?
- Outside conversations (5+ people, `docs/user-conversations.md`) are still needed for the pivot check.

**Next (session 2):** start with spike S1/D1 (hook latency on Windows), then D2–D7 ADRs, prior-art notes, architecture skeleton. If the payload capture has been done, use those fixtures to answer U-02–U-08 in `docs/testing/unknowns.md`.

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
