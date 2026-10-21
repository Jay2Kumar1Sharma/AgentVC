# Design note: hook wiring

**Session 3 (v0.1-alpha), plan §6.2 "One source for hook wiring".** Module: `agentvc/wiring.py`.

## What and why

`wiring.py` is the **single list** of the agent events AgentVC handles and the tools it knows. The Claude Code hook configuration is generated from it; a test fails if the generated config, the adapter's parsers and the recorder's handlers disagree. So a handler can't exist without being subscribed, or the reverse (FM-ADP-03, HZ-G2-04).

**Events handled** (Claude Code): `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `SubagentStart`, `SubagentStop`, `Stop`, `SessionEnd`. `InstructionsLoaded` is deliberately **not** handled: it carries one-off session ids (U-17) and no agent action.

**Tools:** a table of known tool names with a category (`shell`: Bash, PowerShell; `edit`: Edit, Write, MultiEdit, NotebookEdit; `read`; `agent`; `other`). Tool events use the matcher `*`, so a tool AgentVC doesn't know is still recorded (category `unknown`) and logged as a blind spot, never missed. A test checks the matcher covers every listed tool, both shells included.

**Per-version event lists** (FM-INS-03): Claude Code 2.1.72 silently registers **no hooks at all** if the settings name an event it doesn't know. The generator therefore takes the target version and emits only events that version is known to have (lists copied from `scripts/capture_payloads.py`, which are evidence from the binaries; a test keeps them equal). An unknown version gets only the long-standing core events and a warning. `PostToolBatch` exists from 2.1.302 on.

**Transport per event** (ADR-0003, ADR-0004, ADR-0008):
- `SessionStart` and `UserPromptSubmit`: a **command** hook, `"<absolute python>" -I -m agentvc hook --ensure-daemon`, which starts `agentvcd` if needed, forwards the event, and reports problems (daemon down, rejected requests, gaps) to the user. Once per prompt, so its process start-up is affordable.
- Every other event: an **`http`** hook to `http://127.0.0.1:<port>/hook` with the workspace token as a **literal header** (see the ADR-0003 amendment: env-var interpolation into headers can be silently blanked).
- Versions without known `http` hook support (2.1.72: unverified) get the per-call command form for every event (ADR-0003's fallback).
- `strict` mode adds `onFailure: "block"` to `PreToolUse` where the version supports it (2.1.295+).
- Every hook gets `timeout` = AgentVC's deadline × 2 (5 s → 10 s), written from one constant so the two can't drift (ADR-0004).

## Guarantees touched

G2 (nothing unsubscribed, no config that silently registers nothing), G8 (`-I`, absolute interpreter), G7 (http for the per-tool events).

## How it can fail

| Id | Failure | Detection / test |
|---|---|---|
| FM-ADP-03 | A tool or event isn't subscribed | wiring ↔ adapter ↔ recorder consistency test; `*` matcher test |
| FM-INS-03 | Config names an event the version doesn't know | generator test per fixture version: every event name appears in that version's list |
| FM-WIR-01 | Hook timeout and AgentVC's deadline drift apart | both derived from one constant; test |
