# ADR-0009: Where `.agentvc/` lives: worktrees, nested repositories, monorepos (D7)

- **Status:** proposed
- **Date:** 2026-10-18
- **Decided by:** pending the maintainer's review (drafted in Discovery part 2)
- **Related:** plan §7.1 D7, §14.1 "Working-directory and multi-root changes", G1, G3; spike `scripts/spikes/workspace_roots.py`, results `results/spikes/2026-10-18/workspace_roots.json`; ADR-0005, ADR-0006

## Context

An agent session starts in one folder, but that folder may be a monorepo subfolder, a linked git worktree (Claude Code creates its own under `.claude/worktrees/` inside the project), or contain nested repositories (vendored code, submodules). AgentVC has to pick one `.agentvc/` per workspace and know what its snapshots cover.

**Spike (git 2.49, Windows, 2026-10-18):**

| Session starts in | `git rev-parse --show-toplevel` | Linked worktree? |
|---|---|---|
| the repository | the repository | no |
| a monorepo subfolder (`packages/app`) | **the repository root** | no |
| a linked worktree next to the project | the worktree | yes |
| a linked worktree **inside** the project (`.claude/worktrees/wt`) | the worktree | yes |
| a nested repository (`vendor/nested`) | the nested repository | no |
| a folder that isn't a repository | (error) | — |

**Snapshot of the outer repository** (private index, `git add -A`, as AgentVC would do it): the repository's own files are captured, but a nested repository and a worktree inside the project are each recorded as a **gitlink** (a pointer to another repository's commit). **Their files are not in the snapshot.**

## Decision (proposed)

- **The workspace root** is `git rev-parse --show-toplevel` of the folder the session starts in (`CLAUDE_PROJECT_DIR` for Claude Code), or that folder itself when it isn't in a repository. `.agentvc/` lives at the workspace root.
- **A monorepo subfolder** shares the repository root's `.agentvc/`: an agent started in `packages/app` can still edit or delete anything in the repository, and a rollback must cover it. Each run records the folder it started in.
- **Every linked worktree is its own workspace** with its own `.agentvc/`, whether it sits next to the project or inside it. The outer workspace **excludes** worktrees inside it from its snapshots: they're another workspace, recorded there.
- **Nested repositories and submodules** inside the workspace are snapshotted **separately**: each gets its own private index in the same object store, and the step records one tree per nested root. Until that is built (v0.1), each nested repository is listed as **unprotected** in the banner and in `inspect`, never silently skipped (G3).
- **A session that moves outside its workspace** (`cd` elsewhere, an added directory) is reported as unprotected in v0.1 and snapshotted as an extra root in v0.2 (plan §14.1).
- `.agentvc/` itself is never part of a snapshot (its own `.gitignore` holds `*`).

## Consequences

- The snapshotter must find nested repositories on every full snapshot (look for `.git` entries below the root), within its time budget.
- Rollback restores each nested root from its own tree, with the same compare-and-swap rule (G1).
- A user who opens the same repository in two worktrees gets two separate histories. This is documented, and `history` could later show them side by side.
- Revisit if the walk to find nested repositories proves too slow on large monorepos.
