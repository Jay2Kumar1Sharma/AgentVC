# AgentVC

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](docs/progress.md)
[![Contributions: not open yet](https://img.shields.io/badge/contributions-not%20open%20yet-lightgrey.svg)](CONTRIBUTING.md)

**Version control for what AI coding agents do.**
Record every action an agent takes, see exactly what changed, and undo it, including what it did through the shell. When something can't be undone, AgentVC says so.

> [!WARNING]
> **Status: pre-alpha, design phase. There is no working code yet.**
> This repository currently holds the plan, the research behind it, and the project's process. Don't install anything from here yet. Watch the repo or join [Discussions](../../discussions) to follow along.

---

## The problem

Coding agents like Claude Code, Codex, Gemini CLI and Cursor now run shell commands, install packages, push to git and call APIs. Their built-in undo only covers edits made through their own file-editing tools. A `rm -rf` run through the shell, a `git push`, a database migration or a sent message can't be taken back, and often you can't even see a full list of what happened.

## What AgentVC will do

- **See:** a history of every agent action, grouped by the request that caused it.
- **Diff:** exactly what each step changed.
- **Roll back:** restore your workspace, including changes made through the shell, **without ever overwriting work you did yourself**.
- **Know your effects:** label what the agent did outside your files (pushes, installs, API calls) as undoable, compensatable, or permanent.
- **Replay safely:** go back to a step and try again, without repeating a payment or a push that already happened.
- **Approve before it leaves:** optionally hold outgoing actions until you review and commit them.

It runs locally, makes no model calls and sends no data anywhere.

## Our promises

| | |
|---|---|
| **Never destroy your work** | A rollback only rewrites a file that still holds exactly what the agent left. Every rollback can be undone. |
| **Nothing silent** | Every agent action is recorded, or shown as a gap. |
| **Honest accounting** | Reports list what couldn't be undone, and why. |
| **Never repeat the world** | Replay never re-runs an external action that already happened. |
| **Nothing leaves early** | Held-back actions are released only when you approve, exactly once. |
| **Local and private** | No telemetry, no cloud; secrets are scrubbed. |
| **Cheap enough to leave on** | Overhead stays within published budgets. |
| **Can't be silently switched off** | If the agent tampers with AgentVC, you're told. |

## Roadmap

| Version | Goal |
|---|---|
| **v0.1** | Undo for files and shell, for Claude Code |
| **v0.2** | Know your effects: labels for git pushes, installs and other actions; Codex support |
| **v0.3** | Replay and fork without repeating external effects |
| **v0.4** | Hold outgoing actions until you approve them (MCP outbox) |
| **v0.5** | Databases, Docker, more agents, a shared registry of tested undo recipes |

The full plan is in [docs/master-plan.md](docs/master-plan.md); current progress is in [docs/progress.md](docs/progress.md).

## Contributing

**AgentVC is not accepting code contributions yet.** While the core is being built, pull requests from outside the maintainer team are closed automatically. Contributions will open after the first milestones are released; this README and [CONTRIBUTING.md](CONTRIBUTING.md) will announce it.

Until then, feedback is welcome in [Discussions](../../discussions):
- challenge the [plan](docs/master-plan.md), especially the [guarantees](docs/master-plan.md#2-guarantees) and the [design decisions](docs/master-plan.md#71-design-decisions-to-settle-in-discovery);
- share agent disasters you've had: what the agent did, and what you needed to undo.

Security problems: see [SECURITY.md](SECURITY.md).

## Related work

AgentVC builds on ideas from many projects and papers, including Cline and OpenCode checkpoints, Jujutsu's operation log, turnback, toffoli, undolog, Turso AgentFS, and the ACRFence, Atomix and Cordon papers. Background research is in [docs/research.md](docs/research.md).

## Licence

AgentVC is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
