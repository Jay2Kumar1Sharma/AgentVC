# Design note: CLI for session 3

**Session 3 (v0.1-alpha), plan §5, §6.1.** Module: `agentvc/cli.py` (`python -m agentvc`, later the `agentvc` entry point).

## Commands

| Command | Shows | `--json` |
|---|---|---|
| `agentvc history` | One line per run, newest first: id, agent, started, status, turns, steps, **gaps**; then the ledger verdict when it isn't clean. Runs made by `check` are hidden unless `--all` | list of run objects + `verdict` |
| `agentvc inspect <run>` | The run's timeline: turn, step number, time, tool, category, short scrubbed input (command or target path), outcome, duration; GAP rows stand out with their reason; flags (`outside-workspace`, `reused-id`, `start-missing`); totals; the ledger verdict | run + steps + verdict |
| `agentvc check` | Sends a realistic fake tool call (a PreToolUse + PostToolUse pair shaped like the 2.1.302 fixtures, its own session id) **through the real hook path**: starts/locates `agentvcd`, POSTs over HTTP with the token, then reads the ledger to confirm the step came back `RECORDED`. Then the failure path: a Post without a Pre must come back as a visible GAP. Reports the measured round trip and the ledger verdict. Exit 1 on any failure | each check with ok/detail |

Internal subcommands used by the hooks: `hook [--ensure-daemon]` (per-call handler), `daemon` (run `agentvcd` in the foreground), `hooks-config --agent-version X [--mode M]` (print the generated hook config).

Every command finds the workspace the same way the hook does (from the current folder), so running from a subfolder gives the same answer (plan §15.3 CLI). A missing `.agentvc/` is reported plainly ("AgentVC has not recorded anything here"), never created by a read command.

## Session 4 commands (snapshots and rollback)

| Command | Does | `--json` |
|---|---|---|
| `agentvc diff <run>[:<step>]` | A step: what it changed (before → after snapshot), as unified diffs (`Binary files differ` for binary content). A run: each file from before the agent's first change to after its last, plus files changed **outside** the agent between steps and steps that weren't captured | files with status, diff text, outside changes, uncaptured steps |
| `agentvc rollback <run>[:<step> \| :t<turn>]` | Restore design note. Prints one line per file and the totals: restored / already / conflicts / could not undo, and the rollback number. `--force` overwrites conflicts after saving them | the rollback row and per-file results |
| `agentvc rollback --undo` | Reverses the newest rollback or undo | idem |
| `agentvc undo` / `agentvc redo` | The newest turn back / forward | idem |
| `agentvc repair` | Finishes an interrupted rollback | idem |

`inspect` gains, per step, what wasn't protected (`[not snapshotted: baseline pending]`, `[outside changes: 2]`) and a totals line for snapshots and rollbacks. Targets accept `latest` for the run. Unencodable characters in paths print as `?` (FM-CLI-03); diffs are decoded as UTF-8 with replacement.

## Guarantees touched

G2/G3: gaps and the ledger verdict are always shown, never only in `--json`. G8: `check` proves recording works on this machine. G1: `rollback`, `undo`, `redo` never write a file that isn't exactly what the agent left (restore note); G3: every file's result is printed.

## How it can fail

| Id | Failure | Detection / test |
|---|---|---|
| FM-CLI-01 | Output hides gaps or a tampered ledger | golden-output tests with gaps and a tampered ledger |
| FM-CLI-02 | `check` passes without going through the real hook path | test runs `check` against a daemon whose recording is broken by fault injection and expects failure |
