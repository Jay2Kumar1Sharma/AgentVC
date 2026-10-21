# Cline checkpoints

- **Source read:** [github.com/cline/cline](https://github.com/cline/cline) at commit `a42df8c8d5fd` (2026-10-16): `docs/core-workflows/checkpoints.mdx`, `sdk/packages/core/src/hooks/checkpoint-hooks.ts`, `sdk/packages/core/src/session/checkpoint-restore.ts`, `apps/vscode/src/core/controller/checkpoints/checkpointRestore.ts`.
- **Tried:** no.
- **Licence:** Apache-2.0.

## What it is

Cline (a VS Code extension, CLI and SDK) snapshots the project so the user can roll back code, conversation, or both ("Restore Files", "Restore Task Only", "Restore Files & Task"), and compare any checkpoint with a diff view.

**The docs and the code disagree.**
- **The docs page:** "a shadow Git repository separate from your project's actual Git history. After each tool use … Cline commits the current state of your files to this shadow repo. Your main Git repository stays untouched."
- **The current SDK code**, which the VS Code extension's restore now calls (`checkpointRestore.ts` → `sdkRestoreCheckpoint`), works differently, as described below.

This note follows the code; the docs page is probably out of date (**inferred**).

## Capture, storage, restore (as implemented at this commit)

- **Capture:** **one checkpoint per agent run (user turn)**, not per tool use. It builds a stash commit of the working tree with `git stash create` **in the user's own repository**, and adds a synthesized third parent holding untracked files. It uses a persistent private index in Cline's data folder so unchanged untracked files aren't re-hashed. **Git-ignored files are not captured** ("untracked (but not git-ignored) files"). Without a git repository there are no checkpoints, until the user runs `git init`.
- **Storage:** the stash commits live **in the user's `.git`**, kept reachable by private refs `refs/cline/checkpoints/<session>/<n>`, hidden from `git stash list`, and deleted with the session. A clean tree falls back to recording `HEAD`.
- **Restore:**
  1. `beginWorktreeRestoreTransaction` saves the current tree (including untracked files) under a private ref.
  2. It then runs `git reset --hard` and `git clean -fd`, and applies the checkpoint's stash.
  3. On failure it rolls back to the saved state.
  4. **On success it deletes the saved ref** (`commit()` → `update-ref -d`), so the pre-restore state is no longer kept.

## (a) `rm -rf` run through the shell

- **Tracked and untracked (not ignored) files** deleted during a turn come back with "Restore Files" to that turn's checkpoint.
- **Deleted git-ignored files** (`.env`, local configs) **can't be restored**.
- **Changes earlier in the same turn** are lost too, since the checkpoint is per turn.
- **Folders that aren't repositories** have no checkpoint at all.

## (b) `git push`, then undo

Not handled. The checkpoint system only touches the working tree; a pushed commit stays on the remote, with no record or warning.

## Limits

- **Your edits are overwritten:** restore does `reset --hard` + `clean -fd`, so any of your uncommitted edits, and untracked files created since the checkpoint, are replaced. After a successful restore the saved pre-restore state is deleted, so **the restore itself can't be undone** through Cline. G1 forbids exactly this.
- **It writes into your `.git`** (refs and objects). A user who deletes unknown refs or re-clones loses the checkpoints.
- **Large repositories:** the docs warn about storage and speed, and suggest turning checkpoints off.

## For AgentVC

- **Borrow:**
  - A persistent private index to avoid re-hashing unchanged files; AgentVC's spike relies on the same idea.
  - Restore as a transaction with rollback on failure.
  - Per-snapshot telemetry without paths; for AgentVC this is the local debug log, never sent anywhere.
- **Differ:**
  - **Never write into the user's `.git`** (ADR-0005: AgentVC's own store, owning its objects).
  - **Capture small ignored files** such as `.env` (ADR-0006).
  - **Snapshot per step,** not only per turn.
  - **Per-file compare-and-swap** instead of `reset --hard` + `clean -fd`.
  - **Keep the pre-rollback state** so `rollback --undo` always works (G1).
