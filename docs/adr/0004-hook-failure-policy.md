# ADR-0004: When AgentVC's own hook fails or times out (D2)

- **Status:** proposed
- **Date:** 2026-10-18
- **Decided by:** pending the maintainer's review (drafted in Discovery part 2)
- **Related:** plan §7.1 D2, G2, G7, G8; unknown U-04; spike `scripts/spikes/hook_failure.py`, results `results/spikes/2026-10-18/hook_failure.json`; FMEA FM-HOOK-01, FM-HOOK-02; ADR-0003

## Context

AgentVC's hook can crash, hang or be killed. Two guarantees pull in opposite directions: G7 says AgentVC must not get in the agent's way, while G2 says no action may go unrecorded without the user seeing a gap. Plan §7.1's default is "fail open, visibly; fail closed in strict mode; every hook has a hard timeout under the agent's".

**What Claude Code 2.1.302 actually does** (experiment, 2026-10-18, a PreToolUse hook on Bash with a 3 s timeout):

| Our PreToolUse hook | Hook killed? | Tool ran? | After-tool event |
|---|---|---|---|
| sleeps 30 s (times out) | yes, at 3 s | **yes** | PostToolUse |
| raises (exit 1) | — | **yes** | PostToolUse |
| exits 2 | — | no (blocked) | none |

So Claude Code itself **fails open**, and the agent carries on without noticing. The hooks reference adds `onFailure: "block"` (Claude Code 2.1.295+) to make a failed or timed-out hook block the call instead.

## Options considered

1. **Always fail closed:** any AgentVC problem blocks the tool. Safest for G2, but a bug in AgentVC stops the user's work, and users turn it off (against G7 and, in effect, G8).
2. **Always fail open, silently:** never in the way, but actions go unrecorded with nobody told (breaks G2).
3. **Fail open visibly by default; fail closed in `strict` mode.**

## Decision (proposed)

- **Default (`observe`, `protect` modes): fail open, visibly.**
  - Every AgentVC hook runs under its **own deadline, shorter than the agent's hook timeout**: 5 s against a configured hook timeout of 10 s. When the deadline is near, it stops work, records what it has, and exits 0, so the agent's timeout never has to kill it mid-write.
  - A step with a Pre event but no Post event becomes a **GAP**; so does a Post event with no matching Pre event, which is exactly what a crashed or timed-out PreToolUse looks like (the tool still ran, then PostToolUse arrived). `PostToolUseFailure` counts as a Post event (fixture `2.1.302/0076`).
  - Every gap is shown: in `inspect`, in the session banner, and in a short `additionalContext`/`systemMessage` on the next `UserPromptSubmit` ("AgentVC missed 1 action at 14:02; it is listed as a gap").
- **`strict` mode: fail closed.** PreToolUse hooks are registered with `onFailure: "block"` and exit 2 when AgentVC can't record the step before its deadline. Agents or versions without `onFailure` get the exit-2 behaviour only, which still covers crashes AgentVC catches, but not a hard kill; `agentvc ready` says so.
- **Second line of defence:** if both Pre and Post hooks fail, nothing reaches the ledger. On the next prompt or session start, AgentVC compares the recorded steps with the tool calls listed in `PostToolBatch` events (Claude Code 2.1.302+) and, later, the transcript, and records any it missed as gaps.
- **Matchers cover every shell tool** (`Bash|PowerShell`), so blocking one shell in strict mode can't be bypassed through the other. The agent in the "exit 2" run explicitly considered PowerShell as a way around the block.

## Consequences

- The ledger needs a GAP record type with a reason (`pre-missing`, `post-missing`, `hook-timeout`, `hook-error`, `reconciled-from-batch`) and tests for each.
- `agentvc check` must exercise the failure path too: a deliberately failing hook must produce a visible gap.
- Deadlines appear in two places (AgentVC's own, and the agent's hook `timeout`), and `install` must write the agent timeout from AgentVC's deadline so they can't drift apart.
- Revisit if an agent offers transactional hooks (the tool waits for an explicit "recorded" acknowledgement).
