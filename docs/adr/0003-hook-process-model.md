# ADR-0003: Hook process model (S1 / D1)

- **Status:** accepted (2026-10-21)
- **Date:** 2026-10-18
- **Decided by:** the maintainer, 2026-10-21 (drafted in Discovery part 2)
- **Related:** plan §7.1 S1/D1, §10 budgets, G7; spike `scripts/spikes/hook_latency.py`, results `results/spikes/2026-10-18/hook_latency-windows.json` and (U-18) `results/spikes/2026-10-21/hook_latency_http-windows.json`; CI job `.github/workflows/spike-latency.yml`; ADR-0004 (D2)

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

**Linux, macOS and Windows on GitHub's runners** (same spike, 30 runs each, [Actions run 39114705668](https://github.com/Jay2Kumar1Sharma/AgentVC/actions/runs/39114705668); raw data in `results/spikes/2026-10-18/ci-runners/`), p95:

| What one hook call costs | Linux | macOS | Windows (CI) |
|---|---:|---:|---:|
| Minimal handler, `-I -S`, no shell | 31 ms | 32 ms | 43 ms |
| Minimal handler through the shell | 35 ms | 47 ms | **92 ms** |
| Snapshot, 10k files, one changed | 62 ms | 87 ms | **216 ms** (over the 150 ms budget) |
| Snapshot, 100k files, one changed | 290 ms | 283 ms | 436 ms |
| **First** snapshot, 10k / 100k files | 1.3 s / 9 s | 4 s / 44 s | **82 s / 15 min** |

Shared runners are small (2–5 CPUs) and noisy; read them as orders of magnitude.

**The `http` hook round trip (unknown U-18), measured on Windows 11** (same machine, Python 3.12.7, Node 22.18.0, 200 calls each, 2026-10-21; `hook_latency.py --http-only`). The server is a stand-in for `agentvcd`: stdlib `ThreadingHTTPServer` on 127.0.0.1, random port, checks a token header, parses the JSON payload, answers `{}`. The client runs in an already-running process, as the agent's does:

| Client, per hook call | p50 | p95 |
|---|---:|---:|
| Node's built-in `fetch` (the closest stand-in for the agent's JavaScript client) | 1.5 ms | **2.7 ms** (3.4 ms on a re-run) |
| Python, one kept-alive connection | 0.5 ms | 1.1 ms |
| Python, a new connection per call | 3.7 ms | 27 ms |
| Python, new connection, a Write payload carrying a 256 KB file | 4.4 ms | 28 ms |
| Python, new connection to the **name `localhost`** | **2,054 ms** | **2,066 ms** |

- The Python new-connection p95 is a client artifact: with a socket timeout set, Python's `connect` sometimes waits one Windows timer tick (~15 ms); split into phases, connect + send + response take about 2 ms. The agent's client isn't Python.
- **`localhost` costs about 2 seconds per call on Windows**: the name resolves to `::1` first, nothing listens there, and Windows waits before trying 127.0.0.1. Hook URLs must use the literal `127.0.0.1`.
- Not measured: Claude Code's own client (its binary bundles its own JavaScript runtime). `agentvc check` measures the real round trip on each install.

## Options considered

1. **Per-call process, shell form** (what a plain `"command"` hook does): 77 ms p95 on Windows. **Over budget** before doing any work.
2. **Per-call process, exec form + `-I -S`**: 48 ms p95 for a handler that does nothing. It fits only with no headroom: importing AgentVC's modules and writing one ledger row will push it over on Windows.
3. **Background process (`agentvcd`), hooks as `http` hooks to `127.0.0.1`**: no process start per call. The per-call cost is one local HTTP round trip (**2.7 ms p95** with Node's `fetch`, U-18) plus the work itself.
4. **Background process, hooks as tiny command clients**: still pays a process start per call (option 2's cost at best). No advantage over option 3 for agents that support `http` hooks.

## Decision

- **Default: a background process, `agentvcd`, started on demand, receiving hook events as `http` hooks bound to `127.0.0.1` only.** It holds the ledger connection and the snapshot index warm, so per-call work is only the work itself.
- **Fallback: per-call exec-form process** (`args`, absolute interpreter, `-I -S`) for agents or versions without `http` hooks, and whenever `agentvcd` can't be started. Over budget on Windows; reported as such by `agentvc ready` rather than hidden.
- **Liveness is checked per prompt, not per tool call:** a command hook on `SessionStart` and `UserPromptSubmit` (≈ 77 ms, once per prompt) starts `agentvcd` if needed and tells the user if it can't. Otherwise a dead daemon would make every `http` hook fail as a *non-blocking* error, which is a silent gap (G2, G8).
- **Hook URLs name `127.0.0.1` literally, never `localhost`** (about 2 s per call on Windows, U-18).
- **The local endpoint is authenticated:** each session gets a random token, stored in `.agentvc/` and sent as a header through `allowedEnvVars`, so other local processes can't write into the ledger. Nothing listens beyond `127.0.0.1` (G6).
- **First snapshots never run inside a hook:** building the index for a large repo takes seconds to minutes, so it runs in `agentvcd` at install or session start, in the background. Until it finishes, steps are recorded with a visible "baseline snapshot pending" notice.
- **Confirmed on Windows (U-18, 2026-10-21):** the round trip is well under the budget (above). Linux and macOS numbers come from the CI spike job, which now runs the same measurement.

## What would change this decision

- **Linux and macOS numbers (measured, see above):** exec-form per-call is 31–32 ms p95 for a handler that does *nothing*, so it reaches the ~35 ms bar set here only before any real work (a ledger write, module imports). That doesn't change the recommendation: per-call stays the fallback on every OS. Windows on CI is worse than on the maintainer's machine (92 ms through the shell, 216 ms for a 10k-file snapshot), which strengthens the case for the daemon and for keeping the index warm in it.
- **The `http` round trip measuring above ~25 ms p95 on Windows** with the agent's own client would remove the daemon's advantage; then option 2 with aggressive import trimming becomes the default, and the 50 ms budget needs an ADR. Measured (U-18): 2.7 ms with Node's `fetch`. Only the Python client with a new connection per call came near the line (27 ms, the timer artifact above); AgentVC's own Python clients (`agentvc check`, the per-call fallback's forwarder) therefore keep their connection open or connect without a socket timeout.

## Consequences

- G7 is reachable on Windows only through the daemon; the per-call fallback is documented as over budget there.
- New moving parts: process lifecycle, port selection, token handling, and crash recovery for `agentvcd`. Each needs tests and FMEA rows (session 3 design note).
- Payload handling and scrubbing move into a long-lived process, so a memory leak or a stuck request becomes possible; per-request timeouts below the hook timeout apply (ADR-0004).
