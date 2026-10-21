# container-use (Dagger)

- **Source read:** [github.com/dagger/container-use](https://github.com/dagger/container-use) at commit `2e43e625e952` (2026-08-12): `README.md`, `docs/environment-workflow.mdx`, `repository/git.go`, `repository/flock.go`, `install.sh`.
- **Tried:** no.
- **Licence:** Apache-2.0.

## What it is

An MCP server and CLI that gives each coding agent its **own container and its own git branch**, so several agents can work in parallel without touching the user's checkout. The user reviews an environment with `container-use checkout`/`log`/`diff`, then accepts it (`merge` or `apply`) or throws it away (`delete`). Early development.

## Capture, storage, restore

- **Capture:** the agent does its work **through container-use's MCP tools** (run a command, write a file), and the work happens inside a Dagger container. Agent "rules" files tell the agent to use only those tools. The command history is kept with the environment.
- **Storage:** a fork of the user's repository under `~/.config/container-use/repos/<repo>`. Each environment is a worktree and branch there (`container-use/<env>`), and its state and history are stored as **git notes** (`repository/git.go`). The user's own repository is touched only when they `merge` or `apply`.
- **Restore:** isolation instead of undo. `container-use delete <env>` discards everything; "you're back to your last known good state (your current branch)". There's no per-step rollback inside an environment beyond its git history.

## (a) `rm -rf` run through the shell

- **Inside the environment** (via container-use's tools): the deletion happens in the container and the environment's branch. The user's checkout is untouched, and `delete` throws it away.
- **Through the agent's own Bash tool,** bypassing container-use: it lands on the host and container-use doesn't see it (**inferred**: it relies on the agent following its rules).

## (b) `git push`, then undo

A push made from inside the container goes to the remote, assuming the container has network access, which development environments normally need (**inferred**; I found no network policy in the docs I read). Discarding the environment doesn't undo it, and the push isn't flagged.

## Limits

- **Windows isn't supported:** `install.sh` exits with "Windows is not supported".
- **Needs Docker/Dagger,** plus an agent willing to route all work through its MCP tools.
- **No account of external effects.** Isolation covers files only.

## For AgentVC

- **Borrow:**
  - Treating every agent attempt as a branch the user can review, merge or discard; AgentVC's `promote` (v0.2) and `fork` (v0.3) take this direction.
  - Git notes for metadata attached to commits.
- **Differ:**
  - AgentVC works with the agent as it is: no container, no special tools to call, and Windows included.
  - It covers effects that leave the machine by classifying and reporting them (v0.2), which isolation can't.
- **Possible integration:** container-use as a sandbox backend (plan §14.4, v0.5+).
