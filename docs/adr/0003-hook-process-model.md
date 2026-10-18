# ADR-0003: Hook process model (S1 / D1)

- **Status:** proposed
- **Date:** 2026-10-18
- **Decided by:** pending the maintainer's review (drafted in Discovery part 2)
- **Related:** plan §7.1 S1/D1, §10 budgets, G7; spike `scripts/spikes/hook_latency.py`, results `results/spikes/2026-10-18/hook_latency-windows.json`; CI job `.github/workflows/spike-latency.yml`; ADR-0004 (D2)

## Context

Every tool call fires at least two hooks (PreToolUse, PostToolUse). The budget is **< 50 ms p95** of overhead for a non-mutating step (plan §10). The question is whether each hook can be a fresh Python process, or whether AgentVC needs a small long-running local process (`agentvcd`) that hooks talk to.

**Measured on Windows 11** (Python 3.12.7, git 2.49, 50 runs each, 2026-10-18):

| What one hook call costs | p50 | p95 |
|---|---:|---:|
| Python start, minimal handler (reads the JSON payload) | 54 ms | 58 ms |
| Same with `python -I -S` (no site-packages; AgentVC is stdlib-only) | 43 ms | **48 ms** |
| Minimal handler launched **through Git Bash**, which is how Claude Code runs a shell-form hook on Windows | 73 ms | **77 ms** |
| Handler that snapshots a 10k-file repo, one file changed (`git add -A` + `write-tree`, private index) | 119 ms | 132 ms |
| Same, 100k files | 287 ms | 315 ms |
| **First** snapshot of a repo (builds the index): 10k / 100k files | 21 s / 243 s | |

From the Claude Code hooks reference (read 2026-10-18): a command hook with `args` runs in **exec form**, with no shell; an **`http` hook** POSTs the payload to a URL, so no process is started per call; a failed `http` hook is a non-blocking error unless it sets `onFailure: "block"` (Claude Code 2.1.295+).

**Linux and macOS: pending.** The CI job runs the same spike there, but only once the branch is pushed or the job is started by hand. No numbers yet.

## Options considered

1. **Per-call process, shell form** (what a plain `"command"` hook does): 77 ms p95 on Windows. **Over budget** before doing any work.
2. **Per-call process, exec form + `-I -S`**: 48 ms p95 for a handler that does nothing. It fits only with no headroom: importing AgentVC's modules and writing one ledger row will push it over on Windows.
3. **Background process (`agentvcd`), hooks as `http` hooks to `127.0.0.1`**: no process start per call. The per-call cost is one local HTTP round trip plus the work itself. **Not measured yet.**
4. **Background process, hooks as tiny command clients**: still pays a process start per call (option 2's cost at best). No advantage over option 3 for agents that support `http` hooks.

## Decision (proposed)

- **Default: a background process, `agentvcd`, started on demand, receiving hook events as `http` hooks bound to `127.0.0.1` only.** It holds the ledger connection and the snapshot index warm, so per-call work is only the work itself.
- **Fallback: per-call exec-form process** (`args`, absolute interpreter, `-I -S`) for agents or versions without `http` hooks, and whenever `agentvcd` can't be started. Over budget on Windows; reported as such by `agentvc ready` rather than hidden.
- **Liveness is checked per prompt, not per tool call:** a command hook on `SessionStart` and `UserPromptSubmit` (≈ 77 ms, once per prompt) starts `agentvcd` if needed and tells the user if it can't. Otherwise a dead daemon would make every `http` hook fail as a *non-blocking* error, which is a silent gap (G2, G8).
- **The local endpoint is authenticated:** each session gets a random token, stored in `.agentvc/` and sent as a header through `allowedEnvVars`, so other local processes can't write into the ledger. Nothing listens beyond `127.0.0.1` (G6).
- **First snapshots never run inside a hook:** building the index for a large repo takes seconds to minutes, so it runs in `agentvcd` at install or session start, in the background. Until it finishes, steps are recorded with a visible "baseline snapshot pending" notice.
- **To confirm before v0.1-alpha code depends on this:** measure the `http` hook round trip to a local server on Windows, Linux and macOS (an extension of this spike).

## What would change this decision

- **Linux and macOS numbers (CI job, pending):** they can't rescue Windows, which already fails option 1 and has no headroom in option 2. They would change the recommendation **for those platforms only** if exec-form per-call p95 there stays below about 35 ms *including* a real ledger write. Even then, one process model on every OS is simpler to test, so the per-call path would stay the fallback rather than become a second default.
- **The `http` round trip measuring above ~25 ms p95 on Windows** would remove the daemon's advantage; then option 2 with aggressive import trimming becomes the default, and the 50 ms budget needs an ADR.

## Consequences

- G7 is reachable on Windows only through the daemon; the per-call fallback is documented as over budget there.
- New moving parts: process lifecycle, port selection, token handling, and crash recovery for `agentvcd`. Each needs tests and FMEA rows (session 3 design note).
- Payload handling and scrubbing move into a long-lived process, so a memory leak or a stuck request becomes possible; per-request timeouts below the hook timeout apply (ADR-0004).
