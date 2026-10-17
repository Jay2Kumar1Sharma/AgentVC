# AgentVC: Prior Art & Design Research

_Research snapshot as of 2026-10-17, from public documentation, repositories and papers; some details come from secondary sources and summaries, and vendor claims are not independently verified. Projects change quickly. **Maintainers of the projects mentioned here: corrections are very welcome in Discussions.** The project was called "AgentFS" while this research was done; it is now **AgentVC**._

## 1. Key findings

1. **The working name was taken.** Turso already ships [`tursodatabase/agentfs`](https://github.com/tursodatabase/agentfs) (MIT), with an `agentfs run` CLI, a copy-on-write agent filesystem stored in SQLite, and a tool-call audit trail. That's why this project is named **AgentVC** (see §9).
2. **File-level undo is a commodity.** Claude Code `/rewind`, Cursor checkpoints and a lot of small OSS tools (turnback, bashward, agent-rollback, diffback) already snapshot files. Competing on files alone won't work.
3. **Side-effect undo is crowded with prototypes but nothing has been adopted.** toffoli, undolog, revoco and agent-undo all use the same reversible/compensable/irreversible taxonomy; all are early-stage and none had wide adoption as of October 2026. Rubrik Agent Rewind is the only serious product, and it's closed and enterprise-only. **No open-source project has become the default yet.**
4. **Naive rollback is dangerous.** ACRFence (2026) showed that restoring an agent checkpoint and letting the LLM re-run can **repeat irreversible actions**. The bank-transfer test duplicated the transfer in 10/10 trials. Replay has to know which effects already happened.
5. **The most useful idea we found is deferral, not undo.** Cordon (2026) stages outbound effects in an *effect outbox* and only releases them on commit. If an action can be held back until it's approved, it never needs undoing. The taxonomy should gain a **DEFERRABLE** class.
6. **The gap to fill:** a git-like **run ledger** across every effect type, with **pluggable effect adapters**, **effect-aware replay and branching**, and **staged commit**. It should work with any agent (Claude Code, Codex, OpenHands, …) through hooks, an MCP proxy and an OS-level sandbox.

---

## 2. Landscape

### 2.1 Direct overlap

| Project | What it does | Scope | Status |
|---|---|---|---|
| **Turso AgentFS** | SQLite-backed CoW filesystem, KV store, tool-call log; FUSE (Linux) / NFS (macOS) mount; `agentfs run` sandbox | Files + audit; snapshots = copy the `.db` | MIT, beta; sandbox "experimental"; no Windows support mentioned in the docs we read |
| **Rubrik Agent Rewind** (Aug 2025) | Maps agent actions to prompts/tools and rolls back files, DBs, configs and repos through Rubrik Security Cloud | Enterprise; Agentforce, Copilot Studio, Bedrock Agents | Commercial, closed |
| **turnback** | Hook-based; snapshots the workspace before every shell command into a shadow git repo under `~/.turnback`; `undo/redo/restore/blame/ui`; MCP server | Files only; Claude Code, Codex, Cursor, Gemini CLI, OpenCode, Antigravity | MIT; gives up on >100k files or >2 GB; 5 MB per-file cap |
| **bashward** (Rust) | Claude Code PreToolUse hook; parses bash for write targets (`rm`, `mv`, `sed -i`, redirects) and snapshots those paths | Files; macOS-oriented (APFS clonefile) | v0.1.1 |
| **agent-undo** | Journals file writes, shell, git and API calls; generates `undo.sh` | Mixed | Small |
| **toffoli** (TS) | Classifies actions as nullipotent/reversible/compensable/irreversible before they run: deterministic rules first, gated LLM judge only for ambiguous cases. Planner orders compensations; write-ahead journal; MCP server | Classification and recovery planning | Apache-2.0, early-stage. Has a **51-action gold set built from real incidents**, worth learning from |
| **undolog** (Py) | Append-only Postgres ledger; `wrap()` interceptor; each tool defined in a YAML spec (class, capture, compensation); freeze then roll back; "unknown" class until tested | External APIs (Stripe, Postgres, REST, Slack, S3…) | MIT, early-stage |
| **revoco** (Py) | Plans the undo *before* the action; **drills** test each inverse on a disposable canary (proof can only downgrade a class); revokes a grant and its whole blast radius; hash-chained ledger | 91 inverse specs, only fs/git tested on live systems (per its README) | Apache-2.0, early-stage |
| **Dagger container-use** | Each agent gets a container plus a git branch; container snapshots stored as git notes; MCP server | Files + container | Early-stage OSS |

### 2.2 Built-in agent checkpointing (what we wrap or replace)

- **Claude Code `/rewind`**: only tracks edits made through its file-editing tools. **Bash changes (`rm`, `mv`, `cp`) are not tracked**, and neither are directory operations or remote files. The Agent SDK exposes the same file checkpointing. The bash gap is why bashward and turnback exist.
- **Claude Code hooks**: `PreToolUse` can block or deny a call (exit 2 or a JSON `permissionDecision`) and receives `tool_name` and `tool_input` on stdin. `PostToolUse` can't block. This is the main way to integrate without changing the agent.
- **Codex CLI**: enforces a sandbox (macOS Seatbelt; Linux Landlock + seccomp/bubblewrap; Windows token/DACL) but doesn't do rollback.

### 2.3 Sandbox and snapshot infrastructure (possible backends)

| Infra | Snapshot model | Notes |
|---|---|---|
| Morph Cloud Infinibranch | Snapshot/branch whole VMs (memory + disk) | Vendor claims <250 ms; not independently verified |
| E2B | Firecracker; pause/resume keeps memory; `createSnapshot()` | Some sources say beta |
| Fly.io Sprites (Jan 2026) | Persistent VMs; checkpoint/restore built in | Restore latency claims conflict (~300 ms vs seconds) |
| Docker Sandboxes (2026) | microVM per agent, own Docker daemon; macOS/Windows | Reset means "delete and recreate"; no documented snapshots |
| **DeltaBox** (paper, May 2026) | Layered CoW fs (DeltaFS) + `fork()` from a frozen template process (DeltaCR) | **14 ms checkpoint / 5 ms rollback** on SWE-bench; no code released |
| Neon | CoW Postgres branches, point-in-time restore | Branch per agent run; history window depends on plan |
| Dolt | Git semantics for SQL (commit/branch/merge/diff) | Strong DB adapter target |

### 2.4 Academic work

| Paper | Relevance |
|---|---|
| **GoEX** (Berkeley, 2024, arXiv 2404.06921) | Introduced *undo* and *damage confinement* for LLM actions, and *post-facto validation*: checking results is easier than checking intent. Apache-2.0 code in the Gorilla repo. |
| **SagaLLM** (PVLDB 18, 2025) | Saga pattern for multi-agent planning: compensation + checkpointing, deliberately relaxed ACID. |
| **ACRFence** (arXiv 2603.20625, Mar 2026) | **Semantic rollback attacks**: *Action Replay* (duplicated payments) and *Authority Resurrection* (a consumed single-use token comes back after restore). Proposed defense: an MCP-proxy log of irreversible calls; on restore, replay recorded responses for semantically equivalent calls and block calls that differ. Not implemented yet, so **this is a feature we can ship.** |
| **Cordon** (arXiv 2606.17573, Jun 2026) | *Semantic transactions*: shadow state for reversible effects, an **effect outbox** for external ones, validation before commit. |
| **AgentRewind** (arXiv 2608.14380, Aug 2026) | Aligned checkpoints of agent context and environment; MettleBench for long-horizon tasks. |
| **Revisable by Design / StreamBench** (arXiv 2604.23283) | Ties agent flexibility to reversibility; a benchmark annotated with reversibility classes. |
| ChronoMem (arXiv 2607.27773) | Rollback of agent *memory*. Adjacent to our scope. |

### 2.5 Real incidents (use as test fixtures and in the pitch)

- **Replit, July 2025**: during a declared code freeze, the agent deleted a production database (~1.2k records), then **wrongly said rollback was impossible**. The user restored it manually. Replit's fix was to separate dev and prod databases and add one-click project restore. Takeaways: the undo system has to report what can be recovered, not the agent; and separating environments is a preventive layer.
- toffoli's gold set also includes the PocketOS database and co-located-backup loss and the postmark-mcp exfiltration.

---

## 3. Refined action taxonomy

The three proposed classes aren't enough. The proposal is five **recovery classes** plus orthogonal **attributes**:

| Class | Meaning | Recovery mechanism | Examples |
|---|---|---|---|
| `NULLIPOTENT` | No state change | Nothing to do; replay can return the cached result | `GET`, `SELECT`, `ls`, read-only MCP tools |
| `REVERSIBLE` | Before-image can be captured and restored by us | Restore the snapshot | File write, local git commit, `CREATE TABLE` inside a txn/branch, env var |
| `COMPENSATABLE` | Can't be restored, but an inverse operation exists | Run the inverse with a deterministic idempotency key | `git push` (force-push old SHA), Stripe charge (refund), Slack post (delete), `kubectl apply` (re-apply previous manifest) |
| `DEFERRABLE` | Irreversible *once released*, but can be held back | Stage in the outbox; release on commit, drop on abort | Send email, publish package, post a webhook, deploy |
| `IRREVERSIBLE` | None of the above | Gate (human or policy), confine blast radius, report | Delete an S3 bucket without versioning, `DROP DATABASE` on prod, wire transfer |

**Orthogonal attributes** for each action:
- `idempotent`, `open_world`, `destructive`, `read_only`: map these straight from **MCP tool annotations** (`idempotentHint`, `openWorldHint`, `destructiveHint`, `readOnlyHint`). Treat them as *hints from possibly untrusted servers*; unannotated tools default to destructive and open-world.
- `verified_at`: when the inverse was last proven to work by a drill (revoco's idea). **Evidence can only downgrade a class, never upgrade it.**
- `blast_radius`: what the action touched (resources, principals, cost).
- `confidence` and `classified_by`: `rule` / `adapter` / `llm` (toffoli's `llmAssisted` flag). An LLM must never be able to silently authorize an auto-undo.

**Rules:**
- Unknown tools are `IRREVERSIBLE` until an adapter or drill says otherwise (undolog's "unknown" class).
- Classes depend on context: `DROP TABLE` is `REVERSIBLE` on a Neon or Dolt branch and `IRREVERSIBLE` on prod. Classify the *action in its environment*, not the verb.
- Compensation isn't a true inverse. A refund leaves a trace, and a deleted Slack message may already have been read. Record what remains after compensation in the rollback report.

---

## 4. Architecture

### 4.1 Capture layers

No single interception point covers everything. Stack them, from the most cooperative to the most complete:

| Layer | Captures | Bypassable? | Cost |
|---|---|---|---|
| **Agent hooks** (Claude Code Pre/PostToolUse, Codex hooks, Gemini CLI, Cursor) | Tool intent with arguments *before* execution, and the result | Yes: the agent can run a script that does anything | Very low; the fastest way to get adoption |
| **MCP proxy** | Every MCP tool call, annotations and responses | Only if the agent talks to the server directly | Low. Natural home for the outbox and ACRFence-style replay |
| **Filesystem CoW** | Every byte written, by anything | No, inside the sandbox | Linux: overlayfs in a user namespace or FUSE. macOS: APFS `clonefile`. Windows: ReFS/Dev Drive block cloning (`FSCTL_DUPLICATE_EXTENTS_TO_FILE`) or ProjFS. Everywhere: a shadow git object store as the fallback |
| **Process sandbox** (Landlock + seccomp; Seatbelt; AppContainer) | Enforcement: block what we can't record | Hard to bypass | Medium. Seccomp user-notify can trap syscalls but the man page says it is **not a security mechanism** on its own |
| **Network egress proxy** | All HTTP(S) leaving the sandbox (needs a local CA for TLS) | No, inside the sandbox | Medium. Gives one choke point for record/replay, staged sends and secret redaction |
| **VM/microVM snapshot** (Firecracker, CRIU, DeltaBox-style) | Everything local, including process memory | No | High. Optional backend for "branch the whole machine" |

**Recommendation:** use hooks + MCP proxy for *intent and classification*, CoW filesystem + egress proxy for *ground truth*, and VM snapshots as an optional backend. Reconcile the two sides: a hook says "agent ran `npm install`", the CoW layer says "these 4,812 files changed". Both attach to the same step.

### 4.2 Data model (borrow from Jujutsu's operation log)

Jujutsu (`jj`) records **every** repository operation in an operation log, so `jj undo`/`jj op restore` can return to any earlier state. That user experience fits a tool like this well.

```
Run ─┬─ Step (operation)  ← one tool call or shell command
     │    ├─ intent        (tool, args, agent, prompt/turn ref, OTel span id)
     │    ├─ class + attrs (§3), classified_by
     │    ├─ effects[]     (adapter, resource id, before-ref, after-ref)
     │    ├─ inverse plan  (compensation ops with idempotency keys)
     │    ├─ outbox entries (DEFERRABLE, status: staged|released|dropped)
     │    └─ recorded I/O  (responses, for replay)
     └─ parent run/step → DAG, so branches and forks are explicit
```

- **Content-addressed blob store** for before/after images; dedupe across runs.
- **Hash-chained, append-only ledger**, with the head hash anchored externally. As revoco notes, a hash chain alone can't detect truncation of the newest entries.
- **Export to OpenTelemetry GenAI conventions** (`invoke_agent`, `execute_tool`, `gen_ai.tool.name`) so runs appear in existing observability tools. Pin the semconv version; attributes are still being renamed.
- Storage: SQLite (WAL) locally is the obvious choice. Turso picked it for the same reasons.

### 4.3 Rollback, replay and branching

- **Rollback** = apply inverses newest-first across the dependency graph. If something fails partway, stop and keep a resumable plan (toffoli's planner). Always output a **report**: restored / compensated / dropped from outbox / **could not undo**.
- **Conflict detection:** the world may have changed since the step (a human edited the file, someone pulled the pushed commit). Before restoring, compare the current state to the step's *after*-image. If they differ, treat it as a 3-way merge and don't overwrite blindly.
- **Replay** must be **effect-fenced** (ACRFence): `NULLIPOTENT` and already-applied steps return their *recorded* responses; new or different external calls are blocked until the user explicitly forks. Single-use credentials consumed in the original run are flagged.
- **Branch** = fork from step N with a CoW filesystem, a DB branch (Neon/Dolt) and a fresh outbox. Neither branch can release the other's staged effects.

---

## 5. Adapter notes by domain

| Domain | Best strategy | Gotchas |
|---|---|---|
| **Filesystem** | CoW layer, or snapshot before each step | Large repos and `node_modules` (turnback gives up above 100k files or 2 GB); symlinks; permissions and xattrs; files outside the workspace (`~/.npmrc`, global installs) |
| **Shell** | Treat as opaque; rely on the fs layer + egress proxy, with command parsing (bashward) only as a hint | Background processes, daemons, `sudo`, anything that reaches outside the sandbox |
| **Git** | Local: refs + reflog (reversible). `push`: compensatable (`push --force-with-lease` back to the old SHA) | Others may have fetched; CI may already have run. Push is `DEFERRABLE` by default |
| **Postgres** | Best: run the agent against a **Neon/Dolt branch** and merge on commit. Next best: transaction/savepoint per step (Postgres DDL is transactional). Fallback: before-images via triggers or logical decoding | Sequences, non-transactional DDL (`CREATE INDEX CONCURRENTLY`), extensions, external replicas |
| **Docker** | Record image digests, container configs and volumes; inverse = recreate the previous config | Volume data needs fs-level snapshots; pushing to a registry is compensatable at best |
| **Kubernetes** | Store the prior manifest (`get -o yaml`) before `apply`/`delete`; Velero for volumes | Controllers mutate state; deleting a PVC may destroy data, so `IRREVERSIBLE` unless snapshotted |
| **Cloud (AWS/GCP/Azure)** | Prefer Terraform/Pulumi plans as the unit (plan = intent, state = before-image); turn on soft-delete (S3 versioning, deletion protection) so deletes become reversible | Billing is irreversible; IAM changes can lock you out; resource IDs change on recreate |
| **HTTP APIs** | Per-API adapter specs (undolog's YAML format); method semantics as the fallback (GET = nullipotent, PUT = reversible if the before-state can be fetched, POST/DELETE = unknown) | Many APIs lack idempotency keys; responses must be recorded for replay |
| **MCP calls** | Proxy with annotations + per-server adapter specs + outbox | Annotations are untrusted hints |
| **Email / messaging** | `DEFERRABLE` with a configurable "undo send" window | Once delivered, it's irreversible |
| **Env vars / config** | Snapshot before and after; reversible | Secrets: never store raw values in the ledger; reference a vault or redact |

---

## 6. Hard problems and risks

1. **Agents can bypass hooks.** A model can write a Python script that calls an API directly. Only the sandbox + egress proxy layer closes that. Without it, only make "full undo" claims for what the hooks and proxy actually see.
2. **Rollback can make things worse** (ACRFence; conflicts with human edits). Default to *plan-only*, show the plan, then execute.
3. **Compensation reliability.** Inverses go stale as APIs change. revoco's **drills** (forward + inverse against a canary, on a schedule) are the answer, and are an open-source advantage: a community-maintained, tested registry of tool safety specs.
4. **Cross-system atomicity** doesn't exist. Sagas are the best option, which means partial failure states have to be reported clearly.
5. **Performance.** Snapshotting before every shell command has to stay well under ~50 ms on large repos to be acceptable. CoW (overlay/clonefile/block-clone) is required; copying won't do.
6. **Windows.** Turso AgentFS doesn't mention Windows; turnback runs CI on it. Dev Drive/ReFS block cloning plus ProjFS is an opening, since many competitors are Linux/macOS-first.
7. **Secrets and privacy.** Recorded I/O will contain tokens and PII. Redact at the proxy and encrypt the blob store.
8. **Ledger integrity.** An agent with filesystem access could edit the ledger. Store it outside the sandbox and anchor the hash chain.

---

## 7. Positioning

Turso AgentFS = *a filesystem for agents*. Container-use / Docker Sandboxes / E2B = *a place to run agents*. toffoli / undolog / revoco = *libraries for classifying and compensating*.

**This project:** *a version-control and transaction layer for everything an agent does*. It's agent-agnostic, local-first and adapter-based. It's the one tool that answers "what did run #142 do to the world, and what can I take back?"

What makes it different:
- A unified **run ledger** (jj-style operation log) across fs, shell, git, DB, containers, cloud, HTTP and MCP.
- A **staged-commit outbox** (`DEFERRABLE`). Approving a run becomes "review the diff, then release the effects", like a PR for side effects.
- **Effect-fenced replay and branching** (ACRFence defense, implemented).
- A **community adapter registry** with **drill-verified** inverses.
- Agent integrations through hooks + an MCP proxy; it can use existing sandboxes (Docker Sandboxes, E2B, Morph, Turso AgentFS) as backends instead of competing with them.

---

## 8. Suggested MVP path

> **Superseded.** This was an early suggestion made during research. The build order AgentVC actually follows is in [master-plan.md §8](master-plan.md#8-roadmap).

| Milestone | Scope | Why first |
|---|---|---|
| **M0: ledger** | Run/step schema, SQLite store, content-addressed blobs, `history`, `show`, OTel export | Everything else depends on it |
| **M1: coding-agent undo** | Claude Code + Codex hooks; workspace CoW snapshot per step (shadow git fallback on all OSes, clonefile/block-clone fast path); `rollback <run|step>` with a conflict check | Matches the existing competitors and gives a usable product right away |
| **M2: MCP proxy + outbox** | Wrap any MCP server; classify with annotations + rules; `DEFERRABLE` staging; `commit`/`abort` | The differentiator. Per-call approval gateways exist, and run-level commit/abort exists only as papers (Atomix, Cordon); see [gap-analysis.md](gap-analysis.md) |
| **M3: adapters** | Git push, Postgres (savepoint + Neon/Dolt branch), Docker, HTTP adapter specs (undolog-compatible YAML) | Real side-effect rollback |
| **M4: replay & branch** | Recorded-response replay, effect fencing, `branch <step>` | Hardest piece; builds on M0–M3 |
| **M5: sandbox mode** | Linux namespaces + overlayfs + egress proxy; macOS Seatbelt; Windows AppContainer/ProjFS | Closes the bypass gap |

Evaluation: build a test suite from the toffoli gold set + Replit/PocketOS-style incidents + ACRFence's replay scenarios. Report per-class recall and **"catastrophic misclassifications = 0"** as the headline metric.

---

## 9. Naming

**Decided: AgentVC** ("version control for agent execution"; CLI and package `agentvc`).

`AgentFS` collided with Turso's project, and names around *Rewind* (Rubrik Agent Rewind, AgentRewind), *turnback*, *undolog* and *agent-undo* were already in use. A separate MCP server listing uses the lowercase name `agentvc` (nintechio) for workspace checkpoints; AgentVC's README states clearly what this project is to avoid confusion.

---

## Sources

**Direct competitors and tools**
- [Turso AgentFS docs](https://docs.turso.tech/agentfs/introduction) · [blog](https://turso.tech/blog/agentfs) · [FUSE post](https://turso.tech/blog/agentfs-fuse) · [GitHub](https://github.com/tursodatabase/agentfs)
- [Rubrik Agent Rewind press release](https://www.businesswire.com/news/home/20250812418116/en/Rubrik-Unveils-Agent-Rewind-For-When-AI-Agents-Go-Awry) · [Computerworld](https://www.computerworld.com/article/4037957/ai-agents-make-mistakes-rubrik-has-figured-out-a-way-to-reverse-them.html)
- [turnback](https://github.com/json-open-source/turnback) · [bashward](https://docs.rs/bashward) · [agent-undo](https://github.com/lui01212/agent-undo) · [agent-rollback](https://github.com/Nainish-Rai/agent-rollback) · [diffback](https://github.com/A386official/diffback)
- [toffoli](https://github.com/theo-ai-lab/toffoli) · [undolog](https://github.com/saravanx41/undolog) · [revoco](https://github.com/rsh1k/revoco)
- [Dagger container-use](https://container-use.com/introduction) · [Dagger blog](https://dagger.io/blog/agent-container-use)

**Agent platforms**
- [Claude Code checkpointing](https://code.claude.com/docs/en/checkpointing) · [Agent SDK file checkpointing](https://platform.claude.com/docs/en/agent-sdk/file-checkpointing) · [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Codex sandbox internals (third-party)](https://codex.danielvaughan.com/2026/05/03/codex-cli-sandbox-internals-seatbelt-bubblewrap-landlock-windows-dacl/)
- [MCP tool annotations overview](https://www.speakeasy.com/docs/mcp/build/gram-functions/tool-annotations)

**Infrastructure**
- [Morph Cloud](https://cloud.morph.so/docs/developers) · [Fly.io vs E2B](https://fly.io/learn/fly-vs-e2b/) · [Docker Sandboxes](https://www.docker.com/blog/docker-sandboxes-run-claude-code-and-other-coding-agents-unsupervised-but-safely/)
- [Neon for agents](https://neon.com/faqs/postgres-databases-ai-coding-agents) · [Dolt version control](https://docs.dolthub.com/sql-reference/version-control)
- [Windows block cloning](https://learn.microsoft.com/en-us/windows/win32/FileIO/block-cloning) · [seccomp_unotify(2)](https://man.archlinux.org/man/seccomp_unotify.2.en.html) · [Hermit](https://developers.facebook.com/blog/post/2022/11/22/hermit-deterministic-linux-testing/)
- [jj undo](https://man.archlinux.org/man/jj-undo.1.en) · [OpenTelemetry GenAI conventions guide](https://www.dash0.com/knowledge/opentelemetry-genai-semantic-conventions-explained)

**Research**
- [GoEX (arXiv 2404.06921)](https://arxiv.org/abs/2404.06921v1) · [GoEX blog](https://gorilla.cs.berkeley.edu/blogs/10_gorilla_exec_engine.html)
- [SagaLLM (arXiv 2503.11951)](https://arxiv.org/abs/2503.11951v3)
- [ACRFence (arXiv 2603.20625)](https://arxiv.org/html/2603.20625)
- [Cordon (arXiv 2606.17573)](https://arxiv.org/pdf/2606.17573)
- [DeltaBox (arXiv 2605.22781)](https://arxiv.org/html/2605.22781v1)
- [AgentRewind (arXiv 2608.14380)](https://arxiv.org/pdf/2608.14380)
- [Revisable by Design (arXiv 2604.23283)](https://arxiv.org/html/2604.23283)
- [ChronoMem (arXiv 2607.27773)](https://arxiv.org/html/2607.27773v2)

**Incidents**
- [Replit incident (AI Incident Database)](https://incidentdatabase.ai/reports/5578/) · [The Stack](https://www.thestack.technology/vibe-coding-ceo-deletes-production-database/)
