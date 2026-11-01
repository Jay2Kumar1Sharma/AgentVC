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

## Guarantees touched

G2/G3: gaps and the ledger verdict are always shown, never only in `--json`. G8: `check` proves recording works on this machine.

## How it can fail

| Id | Failure | Detection / test |
|---|---|---|
| FM-CLI-01 | Output hides gaps or a tampered ledger | golden-output tests with gaps and a tampered ledger |
| FM-CLI-02 | `check` passes without going through the real hook path | test runs `check` against a daemon whose recording is broken by fault injection and expects failure |
