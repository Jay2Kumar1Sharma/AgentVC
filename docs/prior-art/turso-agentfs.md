# Turso AgentFS

- **Source read:** [github.com/tursodatabase/agentfs](https://github.com/tursodatabase/agentfs) at commit `0a014ebd4918` (2026-06-03): `README.md`, `SPEC.md`, `MANUAL.md`, `cli/src/cmd/run*.rs`, `cli/src/sandbox/darwin.rs`.
- **Tried:** no. Reading only; see "Trying it" for the steps.
- **Licence:** MIT according to the README badge. The badge links to `LICENSE.md`, which **isn't in the repository at this commit**; only the licences of two vendored components (`licenses/`: fuser, MIT; nfsserve, BSD-3-Clause) are.

## What it is

"The filesystem for agents": a filesystem whose entire contents live in **one SQLite database** (`.agentfs/<id>.db`), plus a key-value store and a tool-call audit table in the same file. Status: beta.

## Capture, storage, restore

- **Capture:** AgentFS doesn't watch an agent; the agent *works inside* it.
  - **SDK:** the agent's code calls `fs.writeFile` and similar (TypeScript, Python, Rust).
  - **Mount:** the database is mounted with FUSE (Linux) or a local NFS server (macOS).
  - **Sandbox:** `agentfs run <command>` runs a program over a **copy-on-write overlay** of a host folder.
  - Tool calls appear in the audit table only when the agent's code records them (`agent.tools.record(...)`); nothing records shell commands automatically.
- **Storage:** inodes, directory entries and file chunks are SQLite tables (`fs_inode`, `fs_dentry`, `fs_data`). In overlay mode the host folder is a read-only base; changes go into the database, and deletions are **whiteout** rows (`fs_whiteout`).
- **Restore:** at the level of the whole database. "Snapshot an agent's state with `cp agent.db snapshot.db`" (README); in overlay mode, discarding the delta leaves the host untouched. `agentfs diff` lists overlay changes. There are no per-step snapshots and no per-file rollback. I found no command that writes overlay changes back to the host folder at this commit (**inferred** from the CLI command list and a source search).

## (a) `rm -rf` run through the shell

- **Inside `agentfs run` (overlay):** the deletion becomes whiteouts in the delta and the host folder is unchanged, so discarding the session is the undo.
- **Agent on the host, as with Claude Code by default:** AgentFS sees nothing, so nothing can be undone.

## (b) `git push`, then undo

The sandbox doesn't stop it: on macOS `agentfs run` sets `allow_network: true` (`run_darwin.rs`), and I found no network restriction for Linux (**inferred**). The push reaches the remote. AgentFS has no notion of external effects, and no record of the push unless the agent's code logs it as a tool call.

## Limits

- `agentfs run` is **not supported on Windows** (`run_windows.rs` exits with an error). Mounting is FUSE on Linux and NFS on macOS.
- Whole-session granularity: undoing one step means having copied the database before it.
- An agent has to be built on it, or run inside it; existing agents such as Claude Code aren't recorded as they are.

## For AgentVC

- **Borrow:**
  - The idea of keeping the whole history in one inspectable SQLite file.
  - Whiteouts as an explicit record of deletions.
  - A diff command scoped to what changed.
- **Differ:**
  - AgentVC records agents in place through their hooks, with per-step snapshots and per-file compare-and-swap restore.
  - It reports external effects instead of ignoring them.
  - It supports Windows.
- **Possible integration:** AgentFS as a sandbox backend (plan §14.4, v0.5+).

## Trying it (steps for the maintainer; not done in this session)

On Linux or macOS (not Windows): `curl -fsSL https://agentfs.ai/install | bash`, then `agentfs init demo --base <a throwaway copy of a project>`, `agentfs run bash`, `rm -rf src`, exit, and check that `agentfs diff demo` lists the deletions while the host copy is intact.
