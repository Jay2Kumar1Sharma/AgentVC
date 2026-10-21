# Design note: hook path and `agentvcd`

**Session 3 (v0.1-alpha), ADR-0003, ADR-0004, ADR-0008.** Modules: `agentvc/hook.py`, `agentvc/daemon.py`, `agentvc/config.py`, `agentvc/store.py`.

## What and why

**One core, two transports.** `hook.handle(body, ctx) -> HookReply` does all the work for one hook event: parse (adapter), protect (deny writes into the store), record (recorder → ledger), build the reply (`{}`, a deny decision, or a `systemMessage`/`additionalContext`). It's called by:
1. **`agentvcd`** (default): a stdlib `ThreadingHTTPServer` bound to **127.0.0.1 only**, one per workspace, on a port chosen at first start and kept in `.agentvc/daemon.json` with its pid and a random **token** (that file is the only copy; the store's `.gitignore` is `*`). Requests without the token get 401 and are counted. It keeps the ledger connection warm and exits after 2 hours without requests.
2. **The per-call command** `python -I -m agentvc hook`: reads the payload on stdin, writes the reply on stdout. Used for versions without `http` hooks, and in-process when the daemon can't be reached.

**Liveness per prompt** (ADR-0003). `SessionStart` and `UserPromptSubmit` run `agentvc hook --ensure-daemon`: if `daemon.json`'s daemon doesn't answer `/health` with the right token, it starts a new one (detached; Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, POSIX new session), waits briefly, then forwards the event. If the daemon was supposed to be running but wasn't, the ledger gets a GAP with reason `daemon-down` for that interval, and the user sees a `systemMessage`: actions since then may be missing (G2, FM-HOOK-04). Rejected (wrong-token) requests since the last prompt are reported the same way. If the daemon can't start, the command handles the event itself and says so.

**Deadline and failure policy** (ADR-0004). Each handled event runs under a `Deadline` of **5 s** (the hook's `timeout` is 10 s, from the same constant). The ledger's lock wait is bounded by the remaining time. Outcomes:
- *Fail open, visibly* (`observe`, `protect`): any exception or an expired deadline → reply `{}` plus a `systemMessage` ("AgentVC could not record this action: …"), exit 0. If the ledger is writable, a GAP (`hook-error` / `hook-timeout`) is recorded; if not (disk full, locked), the message is the only trace, and the next liveness hook repeats it.
- *Fail closed* (`strict`): the same failure on `PreToolUse` → a **deny** decision (http) / exit 2 (command), so the tool doesn't run. Crashes AgentVC can't catch are covered by `onFailure: "block"` where supported.
- A ledger written by a newer AgentVC (ADR-0007) is a recording failure handled by the same policy, never a silent repair.

**Workspace and store** (ADR-0009). The workspace is `git rev-parse --show-toplevel` of `CLAUDE_PROJECT_DIR` (else the payload `cwd`), or that folder when it isn't a repository. `.agentvc/` is created with a `.gitignore` containing `*` before anything else is written.

**Config** (`.agentvc/config.toml`, minimal until session 4): `mode` (`observe` | `protect` | `strict`, default `protect`), `record_prompts` (default `false`). An unreadable or invalid config is reported and the defaults apply; it never turns recording off (G8).

## Guarantees touched

G2 (gaps for every failure path, daemon-down detection), G6 (127.0.0.1 only, token, no outbound traffic), G7 (warm daemon; deadline), G8 (`-I`; the agent can't silently stop the daemon: the next prompt notices).

## How it can fail

| Id | Failure | Detection / test |
|---|---|---|
| FM-HOOK-01 | Hook crashes or exceeds its deadline | fault-injection tests for both modes |
| FM-HOOK-04 | `agentvcd` down while hooks are `http` | liveness test kills the daemon, expects a `daemon-down` GAP and a message |
| FM-HOOK-05 | Port taken by another program after a restart | start fails visibly; test binds the port first |
| FM-HOOK-06 | Requests with a wrong or blanked token (e.g. a header the agent blanked) | 401 counted and reported at the next prompt; test |
| FM-HOOK-07 | The hook URL says `localhost` (≈ 2 s per call on Windows, U-18) | generator always writes `127.0.0.1`; test |
