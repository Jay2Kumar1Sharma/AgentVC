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
| Git repository initialised | no | — |

## Milestone tracker

| Milestone (plan §8) | Status |
|---|---|
| Discovery (week 1) | not started |
| v0.1-alpha (private) | not started |
| v0.1 (public) | not started |
| v0.2 Know your effects | not started |
| v0.3 Replay safely | not started |
| v0.4 Approve before it leaves | not started |
| v0.5 Beyond the workspace | not started |

## Handoff log

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
