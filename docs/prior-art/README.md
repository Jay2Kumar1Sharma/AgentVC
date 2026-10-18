# Prior art

One note per tool AgentVC learns from (plan §8 Discovery). Each covers how the tool captures, stores and restores; what happens for (a) `rm -rf` run through the shell and (b) `git push` followed by an undo; its licence; and its limits. Anything not verified by reading source or running the tool is marked **inferred**, and each note records the commit it read. No tool was installed or run for these notes; each lists the steps to try it.

| Tool | Licence | `rm -rf` via shell | `git push`, then undo | Note |
|---|---|---|---|---|
| Turso AgentFS | MIT (per README) | Only inside its own sandbox (`agentfs run`, not on Windows) | Not handled | [turso-agentfs.md](turso-agentfs.md) |
| turnback | MIT | Yes (whole-tree snapshot before each shell command), up to 100k files / 2 GB | Not handled | [turnback.md](turnback.md) |
| Claude Code `/rewind` | proprietary | No ("does not track files modified by Bash commands") | Not handled | [claude-code-rewind.md](claude-code-rewind.md) |
| Cline checkpoints | Apache-2.0 | Yes for tracked and untracked files, per turn; not ignored files; git repos only | Not handled | [cline-checkpoints.md](cline-checkpoints.md) |
| OpenCode snapshots | MIT | Yes, per step; git repos only | Not handled | [opencode-snapshots.md](opencode-snapshots.md) |
| toffoli / undolog / revoco | Apache-2.0 / MIT / Apache-2.0 | Not seen (they need wrapped tools or an MCP proxy) | revoco: force push compensable via a recorded SHA | [toffoli-undolog-revoco.md](toffoli-undolog-revoco.md) |
| container-use | Apache-2.0 | Isolated if run through its tools; not on Windows | Not handled | [container-use.md](container-use.md) |

**What none of them does** (and AgentVC's guarantees target):
- **G1:** never overwrite the user's own edits on rollback. turnback's CLI and Cline overwrite by default, and Cline can't undo the restore.
- **G1:** keep snapshots safe from the user's own `git gc`. OpenCode borrows objects through alternates; Cline stores into the user's `.git`.
- **G3:** report external effects such as a push as recorded but not undone.
- **G8:** detect tampering with the history.
