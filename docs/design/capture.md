# Design note: event model, Claude Code adapter, recorder

**Session 3 (v0.1-alpha), plan §4.2, §4.3, §14.1, §14.5.** Modules: `agentvc/events.py`, `agentvc/adapters/claude_code.py`, `agentvc/blindspots.py`, `agentvc/recorder.py`, `agentvc/protect.py`.

## What and why

**Event model.** One frozen dataclass, `Event`, for every agent: `kind` (`session_start`, `prompt`, `tool_pre`, `tool_post`, `tool_batch`, `subagent_start`, `subagent_stop`, `stop`, `session_end`), `agent`, `session_id`, `cwd`, and the optional `tool_name`, `tool_use_id`, `tool_input` (already reduced, below), `outcome` (`ok`, `error`, `interrupted`), `error`, `agent_id` (subagent), `prompt_id`, `prompt` (only if `record_prompts`), `source`, `duration_ms`, `batch` (tool-use ids and names listed by `PostToolBatch`).

**Adapter** (`parse(payload, record_prompts) -> Event | BlindSpot`). Tested **only against the captured fixtures** for Claude Code 2.1.72 and 2.1.302, plus mutations of them. `PostToolUseFailure` is a Post event with outcome `error` (`interrupted` when `is_interrupt`). Anything it can't parse (not JSON, not an object, unknown event name, a required field missing or of the wrong type) becomes a `BlindSpot` with a reason, written to `.agentvc/blindspots.jsonl`, and is never silently dropped (G2, FM-ADP-01).

**What is stored, and what isn't** (G6, plan §14.5, U-13). Payloads carry whole file contents, assistant text and prompts.
- `tool_input` keeps its keys; strings are scrubbed; content-bearing fields (`content`, `old_string`, `new_string`, `edits`, `new_source`, …) become `{"chars": n, "sha256": …}`: file contents belong in snapshots (session 4), not the ledger. Commands are kept, scrubbed. Every string is capped (4 KB).
- `tool_response` is reduced to an outcome; stdout is not stored. Errors are scrubbed and capped.
- Prompts are stored only if `record_prompts = true` (default off), scrubbed. `MessageDisplay` and `last_assistant_message` are never stored.
- Blind spots keep the payload's **shape** (keys and value types) and short scrubbed scalars, never long text, capped at 16 KB per record.

**Recorder** (`apply(event)`), all under the ledger's write lock:
- **Run identity (U-17)**: never `session_id` alone. A run is looked up by `(agent, session_id)` within this workspace's ledger; ids seen in both versions are stable across resumes, but a resume with an unknown id simply starts a new run marked `resumed`. `InstructionsLoaded` (one-off ids) isn't handled at all. A run first seen through a tool or prompt event (its `SessionStart` was lost) is created and marked `start-missing`.
- **Events from another project** (ATK-CAP-07): an event for an unknown session whose `cwd` is outside the workspace is a blind spot (`foreign-project`), not a run. A known session that `cd`s out is recorded with `outside_workspace` set (reported as unprotected; plan §14.1).
- **Turns**: `UserPromptSubmit` opens a turn; steps join the turn with the same `prompt_id` when present, else the run's latest turn.
- **Steps**: Pre → `PENDING`; Post (or Failure) with the same `tool_use_id` → `RECORDED`. **Post without Pre** → a step in state `GAP`, reason `pre-missing` (a crashed or timed-out PreToolUse, ADR-0004). **Pre without Post** → `GAP`, reason `post-missing`, decided at the next `Stop` / `SubagentStop` (same `agent_id`) / `UserPromptSubmit` / `SessionEnd` / `SessionStart`, when the tool can no longer be running. `PostToolBatch` reconciles: a listed call still pending becomes `post-missing`; a listed call never seen becomes a `GAP` with reason `reconciled-from-batch`.
- **Duplicates and reuse** (ATK-CAP-04, -06): a second Post for a finished step is ignored and logged as a blind spot (`duplicate-event`); a Pre reusing a finished `tool_use_id` opens a **new** step flagged `reused-id` (never merged, never dropped). A Pre arriving after its Post (late hook) marks that step `pre-late`, still a GAP.

**Self-protection** (`protect.check(event)`, plan §14.1, G8). `PreToolUse` **denies** agent writes into `.agentvc/`: edit tools whose target resolves (relative to `cwd`, `..` and symlinks resolved, case-insensitively on Windows and macOS) inside the store, and shell commands that name the store's path. Reads through edit tools are allowed. Obfuscated shell writes can get past the text check; the ledger's hash chain and anchor (ledger note) detect what they change. The denial is a `permissionDecision: deny` with a reason, in every mode.

## Guarantees touched

G2 (every action a step or a visible gap; blind-spot log), G6 (reduction and scrubbing before storage), G8 (deny writes into the store).

## How it can fail

| Id | Failure | Detection / test |
|---|---|---|
| FM-ADP-01 | Field renamed or missing | blind spot; fixture-mutation tests |
| FM-ADP-02 | Subagent / parallel calls misattributed | `agent_id` and `tool_use_id` pairing; fixture sequences 2.1.72 0038–0041, 2.1.302 0060–0067 |
| FM-ADP-04 | A lone one-off session id starts a phantom run | `InstructionsLoaded` not handled; test replays whole fixture sequences and expects one run per version |
| FM-ADP-05 | File contents or prompts stored in the ledger | reduction tests with canary strings on every fixture |
| FM-SELF-01 | Agent writes into `.agentvc/` through an edit tool or a plain shell command | deny tests, including `..`, case and symlink variants |
