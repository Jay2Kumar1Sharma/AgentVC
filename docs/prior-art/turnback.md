# turnback

- **Source read:** [github.com/MFaizR77/turnback](https://github.com/MFaizR77/turnback) at commit `2b468dabd097` (2026-10-14), the repository of the npm package `turnback` (0.9.1): `README.md`, `guide/LIMITS.md`, `guide/RESTORE.md`, `src/core/restore.ts`, `src/git/shadow.ts`. Several unrelated or mirrored GitHub repositories share the name; this is the one npm points to.
- **Tried:** no. Reading only; see "Trying it".
- **Licence:** MIT.

## What it is

Undo for coding agents, built on hooks: one history across Claude Code, Codex, Cursor, Gemini CLI, OpenCode and Antigravity CLI, plus a CLI, a browser UI and an MCP server. It is the closest existing tool to AgentVC v0.1.

## Capture, storage, restore

- **Capture:** agent hooks. Before a mutating tool, the hook takes the turn's baseline; before a **shell command it captures the whole tree**; later edits capture only the affected paths; the end of the turn captures the result. The initial baseline is built in the background at install and session start (README "How it works"). `turnback run -- <cmd>` records any command as a turn without an agent.
- **Storage:** a **separate bare git repository** per workspace under `~/.turnback` (`GIT_DIR` with the project as work tree). It includes tracked, untracked and ignored files up to 5 MB, excluding dependency and build folders. The shadow repo has **no alternates**, so it owns its objects (no `alternates` in the source). Turns older than 7 days and outside the last 50 are cleaned up automatically. Above 100k files or 2 GB it switches to `edits-only` mode, where shell commands are recorded as `unprotected`.
- **Restore:** `undo`, `redo`, `restore <turn> [--before-step N]`, `recover <file>`, with a `--dry-run` plan first. **Before writing, it saves the current state as a safety snapshot** (`pre-restore`), which is what `redo` uses.
  - Files changed since the end of the last finished turn are marked **uncertain** ("probably edited manually by the user").
  - **The CLI restores uncertain files by default** (`skipUncertain = false`, `src/core/restore.ts:153`); the MCP path skips them unless the client can ask the user.
  - The MCP restore is two-step: a plan, then a token bound to the workspace state.

## (a) `rm -rf` run through the shell

Covered: the shell hook snapshots the whole tree before the command runs, so `turnback undo` brings the files back. This is its headline feature over `/rewind`. **Exceptions:**
- **Over 100k files or 2 GB:** the shell command is `unprotected`.
- **Dependency and build folders, and files over 5 MB:** never captured.
- **Baseline still building:** a turn waits for the background baseline at most 10 s, after which its steps are `unprotected`.

## (b) `git push`, then undo

Not handled. "Network effects, databases, processes, files outside the root … are not restored" (`guide/LIMITS.md`). There's no `push` handling in the source, so the push is neither classified nor reported as irreversible. Undo restores the work tree; the remote keeps the pushed commits.

## Limits (from its own docs)

- Files only; conversation context is left to `/rewind`.
- Hooks never block the agent; a failed baseline shows as `unprotected` in `turnback status`.
- A corrupt shadow repo is moved aside and a new baseline taken; earlier turns become unrestorable.
- It stores the first 200 characters of each prompt by default (can be turned off).

## For AgentVC

**Borrow** (ideas only, credited):
- A background baseline at session start. AgentVC's spike measured why it's needed: the first snapshot took 21 s for 10k files and 243 s for 100k.
- An `edits-only` mode with explicit `unprotected` states.
- A safety snapshot before every restore.
- Two-step restore with a token bound to the workspace state.
- End-of-turn warnings for mass deletions and secret files.

**Differ:**
- **G1 default:** AgentVC skips and shows conflicts by default and overwrites only with `--force` (still saving first); turnback's CLI overwrites uncertain files by default.
- **Per-file compare-and-swap:** AgentVC restores a file only if it holds exactly the bytes the agent left, not "changed since the last finished turn".
- **External effects:** AgentVC classifies them (v0.2) instead of ignoring them.
- **Tamper evidence:** a hash-chained, anchored ledger.
- **Prompts:** stored only if enabled.

If it passes AgentVC's v0.1 scenarios, plan §12 says to consider contributing upstream instead.

## Trying it (steps for the maintainer; not done in this session)

In a **throwaway** project (it installs user-level hooks with `install all`, so prefer a project-level install): `npm install -g turnback`, then `turnback install claude --project` in the throwaway folder, run a Claude Code turn that does `rm -rf src`, and check that `turnback undo --dry-run` shows the plan and `turnback undo --yes` restores it. Remove with `turnback uninstall claude --project` (same level as the install) and `npm uninstall -g turnback`.
