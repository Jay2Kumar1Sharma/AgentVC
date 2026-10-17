# Gap Analysis: What's Still Open

_As of 2026-10-17. Builds on [research.md](research.md). Each gap below was checked against targeted searches; "open" means no shipped, usable open-source implementation was found, not that nobody has thought of it. Star ratings (★) rate how strong each gap is, not GitHub stars. Maintainers of the projects mentioned: corrections welcome in Discussions._

## The overall pattern

There are three groups working in this space, and none of them overlap:

| Group | Has | Lacks |
|---|---|---|
| **Academia** (Atomix, Cordon, ACRFence, the Agent–Tool Boundary paper) | Correct designs: staged effects, commit/abort, fenced replay, anomaly taxonomy | No usable code (Cordon and ACRFence publish none; ACRFence's defense isn't even implemented) |
| **Small OSS tools** (turnback, toffoli, undolog, revoco, approval gateways, MCP cassettes) | Working pieces: file undo, classifiers, per-call approval, record/replay for tests | Each covers one slice; early-stage; incompatible formats |
| **Products** (Rubrik Agent Rewind, Turso AgentFS, Claude Code `/rewind`, sandbox vendors) | Polish, adoption | Rubrik is closed and enterprise-only; Turso and `/rewind` cover files only |

**The opening is to turn the academic designs into a usable open-source tool, built from the OSS pieces, that works with the agents people already run.**

---

## Already covered (don't compete here)

| Area | Who covers it |
|---|---|
| File undo for coding agents, including bash changes | Claude Code `/rewind` (edit tools), turnback, bashward, agent-rollback |
| CoW agent filesystem | Turso AgentFS (overlay: read-only host base + SQLite delta) |
| Per-call approval gates at the MCP boundary | mcp-approval-gateway (JSONL queue, out-of-band approval), MCPProxy, TrueFoundry (pending result + validity window) |
| Record/replay of MCP traffic **for testing** | agent-vcr, mcp-cassette, mcp-recorder |
| Pre-execution reversibility classification | toffoli (rules + gated LLM judge, gold set) |
| Whole-machine snapshot/branch | Morph, E2B, Sprites, DeltaBox (paper) |

Reuse these where you can, for example Turso AgentFS or a shadow-git store as the file backend.

---

## Open gaps, ranked

### Gap 1: Transactions across a whole run, not approval per call ★★★★★

**What exists:** Approval gateways hold *one call at a time* for a yes/no decision. Atomix and Cordon describe proper commit/abort across a workflow, with bufferable effects released only on commit, but only as papers.

**What's missing:** A tool where an agent run is a **transaction**. The agent works freely, every outbound effect (push, email, deploy, POST) is staged, and at the end you review **one diff covering files, DB and staged effects**. Then `commit` releases everything in dependency order, or `abort` drops it. This is a PR review for side effects.

**Why it's open:** Per-call approval causes approval fatigue and doesn't let you see the run as a whole. Nobody ships run-level staging that plugs into Claude Code or Codex.

**Hard part:** The agent sometimes needs the *result* of a staged call to continue (e.g., the ID of a created issue). That needs placeholder/promise results, or reclassifying the call as "must execute now, compensatable". Atomix's "bufferable vs reversible-external" split is the model to follow.

---

### Gap 2: Safe replay after rollback ★★★★★

**What exists:** Cassette tools replay recorded MCP responses, *for tests*. ACRFence showed checkpoint restore in Claude Code **duplicated irreversible actions in 10/10 trials** and proposed a fix (log irreversible calls; on restore, serve recorded responses for equivalent calls and block calls that differ), but **didn't implement it**.

**What's missing:** Record/replay used as a **safety mechanism in normal operation**: after `rollback` or `branch`, re-execution goes through a fence that:
- returns recorded results for effects that already happened,
- blocks *new* external effects until the user explicitly forks,
- flags single-use credentials or tokens that were already used.

**Why it's open:** It needs both the run ledger and the MCP proxy. Testing tools don't have the ledger; undo tools don't have the proxy.

**Hard part:** Deciding whether a regenerated call is "the same call". ACRFence suggests an LLM analyzer; a stricter version uses canonical argument hashing plus per-tool equivalence rules from the spec in Gap 3.

---

### Gap 3: An open contract spec for tool side effects ★★★★★ (most suited to open source)

**What exists:** MCP's four boolean hints. The Agent–Tool Boundary paper (arXiv 2609.15397) surveyed **98,291 MCP tools** and found `destructiveHint` meaningful for **only 12.9%**. None of the capabilities needed to prevent its 8 anomaly types can be expressed in today's vocabulary. Every OSS project made up its own format (undolog YAML, revoco inverse specs, toffoli rules), and none are compatible.

**What's missing:** A shared, versioned **side-effect contract** per tool, covering:
- `recovery_class` (nullipotent / reversible / compensatable / deferrable / irreversible)
- `idempotency` (key field, or "none")
- `compensation` (inverse call template + what it leaves behind)
- `stageable` (can it be held, and what placeholder does it return)
- `outcome_query` (how to check whether an uncertain call actually happened, which addresses the paper's A1/A2 anomalies)
- `commutes_with` / conflict keys (A7)
- `verified` (drill results, date, adapter version)

Plus a **community registry** of contracts for popular tools (GitHub, Slack, Stripe, AWS, Postgres, the major MCP servers), each with a **drill** that runs the action and its inverse against a sandbox in CI.

**Why it's open:** The paper explicitly says the missing piece is "reusable transactional contracts at the tool interface, not further recovery mechanisms." There is no standard. Specs win through community adoption, which a single company has trouble getting. It could later be proposed upstream as an MCP extension.

**Hard part:** Bootstrapping coverage. Start with the 20 most-used MCP servers and auto-generate draft contracts that are clearly marked "unverified".

---

### Gap 4: One ledger joining intent, local changes and external effects ★★★★☆

**What exists:** Turso logs file operations plus a tool-call table, assumes a **static base**, and has **no diff/commit to the host**. turnback logs files per turn. Rubrik joins everything, but closed and enterprise-only.

**What's missing:** The `agentfs history` / `Run #142` view from the original pitch. Each step joins:
- **intent** (hook: "agent ran `npm install jose`")
- **local ground truth** (CoW layer: 312 files changed; egress proxy: 14 requests to registry.npmjs.org)
- **external effects** (MCP proxy: a staged `git push`; a DB changeset)

Nobody connects hook events to filesystem and network ground truth, so today a bash command is either opaque (hooks) or detached from its cause (fs watchers).

**Why it's open:** It's integration work across three layers that existing projects each own separately.

---

### Gap 5: Rollback when the world has moved on ★★★★☆

**What exists:** Everyone restores snapshots blindly. Turso documents "base changes are incidental, not a consistency guarantee." The anomaly catalog lists *conflicting externalization* (A7) and *phantom compensation* (A8) as uncovered by **every** runtime surveyed.

**What's missing:** Rollback that does a **3-way check** (before-image / agent's after-image / current state) for each resource:
- Files: refuse or merge if a human edited the file after the agent did.
- Git: detect that the pushed commit was already fetched or built on.
- DB rows: detect later updates to rows the agent touched.
- External: report reactions that compensation can't remove (e.g., the email was read and someone replied).

**Why it's open:** It's unglamorous and most tools assume a single user. It's the difference between a demo and something you'd trust on a shared repo or database.

---

### Gap 6: Reviewable data diffs for databases ★★★☆☆

**What exists:** Neon branches are CoW, but built-in diffing is **schema only**, and reset *discards* child changes. No data-merge path back to the parent was found (not definitively confirmed). Dolt merges data but requires running Dolt.

**What's missing:** For an agent working on a Postgres branch or in a transaction: capture its **row-level changeset** (logical decoding or triggers), show it as a reviewable diff, and **apply it to the parent** on commit, with conflict detection (ties into Gap 5).

**Why it's open:** It's hard, and DB vendors focus on schema workflows for humans. Scope it to Postgres only.

---

### Gap 7: Windows as a full platform ★★☆☆☆

Turso AgentFS covers Linux (FUSE) and macOS (NFS) with no Windows mentioned. Codex has a sandbox but no rollback. ReFS/Dev Drive block cloning + ProjFS could give fast CoW. This is a real but narrow advantage: worth doing, not worth leading with.

---

### Gap 8: A conformance benchmark for undo ★★★☆☆

**What exists:** toffoli's 51-action gold set (classification only), StreamBench (simulated), MettleBench (task success).

**What's missing:** A test suite that runs **real** agents against **real** sandboxed services (git server, Postgres, mock Stripe/Slack/SMTP) and scores whether a system can **undo, compensate, stage and safely replay**. Score it against the 8 anomaly types (A1–A8) and ACRFence's two attacks. A recognized benchmark gives the project credibility and a citation path.

---

## Recommendation: the wedge

> **Input to the plan, not the plan.** The project chose to start with file and shell undo (v0.1) because it's the quickest way to something useful and safe, then add the effects, replay and outbox work this section recommends (v0.2–v0.4). The Discovery usage study can still reverse that order. See [master-plan.md §8](master-plan.md#8-roadmap).

Don't lead with a filesystem; Turso owns that name and space. Lead with **Gaps 1 + 2 + 3 combined**:

> **A transaction layer for agent side effects.** It's an open contract spec plus a local MCP/tool proxy that stages effects, commits or aborts whole runs, and fences replay after rollback. It plugs into Claude Code, Codex and any MCP client, and uses existing tools (shadow git, Turso AgentFS, Neon) for local state.

Why this combination:
- **Verified open:** academia has designed it, but no usable OSS implementation exists.
- **Defensible as open source:** the spec and drill-verified registry get better with every contributor, and a closed product like Rubrik can't match a community registry.
- **Clear demo:** "Agent ran 40 steps. Review: 23 file changes, 1 migration, 2 staged pushes, 1 staged email. [Commit] [Abort]". Then: "Rolled back to step 12, re-ran, and the fence blocked a duplicate payment."
- **Gaps 4–6 build on it**: the ledger, drift-aware rollback and DB diffs extend the same core.
- **Gap 8 proves it:** publish the benchmark alongside.

## Sources

- [Agent–Tool Boundary anomalies (arXiv 2609.15397)](https://arxiv.org/html/2609.15397v1)
- [Atomix (arXiv 2602.14849)](https://arxiv.org/abs/2602.14849) · [OpenReview](https://openreview.net/forum?id=UeRbEpSVUz)
- [Cordon (arXiv 2606.17573)](https://arxiv.org/pdf/2606.17573) · [ACRFence (arXiv 2603.20625)](https://arxiv.org/html/2603.20625)
- [AgentFS CoW internals (codepointer)](https://codepointer.dev/p/agentfs-a-copy-on-write-filesystem) · [AgentFS FUSE](https://turso.tech/blog/agentfs-fuse)
- [mcp-approval-gateway](https://github.com/thehairlesshorseman/mcp-approval-gateway) · [MCPProxy](https://mcpproxy.app/) · [TrueFoundry MCP approvals](https://www.truefoundry.com/blog/mcp-tool-approval-human-gate-call-path)
- [agent-vcr](https://github.com/authoritydmc/agent-vcr) · [Agent VCR (MCP)](https://github.com/Jarvis2021/agent-vcr) · [mcp-cassette](https://github.com/cheneeheng/mcp-cassette) · [mcp-recorder](https://github.com/devhelmhq/mcp-recorder) · [ToolReplay](https://github.com/Matthew0822/ToolReplay)
- [Neon schema diff](https://neon.com/docs/guides/schema-diff)
- [Reversible-action workflow design (Manolachi)](https://www.mariusmanolachi.com/blog/how-to-design-an-ai-workflow-around-reversible-actions) · [Compensating transactions (TianPan)](https://tianpan.co/blog/2026/07/05/the-compensating-transaction-your-agent-never-runs)
