# OpenCode snapshots

- **Source read:** [github.com/sst/opencode](https://github.com/sst/opencode) at commit `60a5a6ca7ce1` (2026-10-18): `packages/core/src/snapshot.ts`, `packages/core/src/git.ts` (repository set-up, packing), `packages/core/src/session/revert.ts`, `packages/core/src/session/runner/step.ts`.
- **Tried:** no.
- **Licence:** MIT.

## What it is

OpenCode, an open-source coding agent, snapshots the project around each agent step so a session can be reverted to an earlier message and the revert itself undone.

## Capture, storage, restore

- **Capture:** at the **start of each step** and again **after it** (`session/runner/step.ts`), the whole project folder becomes a git tree (`git.tree.capture`). Untracked files are capped at **2 MB** each. Snapshots run **only when the project is a git repository** (`enabled = location.vcs?.type === "git"`). How ignored files are treated isn't fully clear from the code: "Snapshots track every scoped file; the source repository's ignore rules decide what callers see", with the source repo's ignores passed to `capture` (**inferred:** ignored files are excluded or hidden).
- **Storage:** a separate git directory under OpenCode's data folder, `<data>/snapshot/<project id>/<hash of worktree>`. **It is created with an `objects/info/alternates` file pointing at the user's repository** (`git.ts`, `initRepository` with a `seed`), so objects already in the user's repo are borrowed, not copied. Snapshot trees have no refs; a custom packer packs loose objects and never prunes, and "objects are never delegated to the source repository through alternates", which applies to *its* packing.
- **Restore (revert):** before reverting, it captures the current state as `original`; it then restores, path by path, each file changed after the chosen message from the tree it had then. A path missing from that tree is deleted. "Clear" (un-revert) puts `original` back, so a revert can be undone. I found no check for the user's own edits to those paths before overwriting them (**inferred**).

## (a) `rm -rf` run through the shell

- **Covered within a session:** the step-start snapshot holds the files, and reverting to the message before the step brings them back.
- **Not covered:** non-git projects, and untracked files over 2 MB.
- **Ignored files:** unclear (see above).

## (b) `git push`, then undo

Not handled: revert only restores files; the remote keeps the push, with no record or warning.

## Limits

- **Snapshots can be lost (D3).** Objects that existed in the user's repository when a snapshot was taken are *borrowed* through alternates. If the user rewrites history and prunes, those snapshots lose objects. AgentVC's D3 spike reproduced exactly this with plain git (ADR-0005). That OpenCode is affected is **inferred** from its set-up; not tested against OpenCode itself.
- **No conflict check:** user edits made after the step can be overwritten by a revert (**inferred**).

## For AgentVC

- **Borrow:**
  - Capture at step start *and* end.
  - Revert as a staged, undoable operation.
  - Restore by path from per-path trees.
  - A packer that never prunes snapshot objects without checking.
- **Differ:**
  - Own every object, no alternates (ADR-0005).
  - Compare-and-swap before overwriting (G1).
  - Work without a git repository.
  - Report external effects.
