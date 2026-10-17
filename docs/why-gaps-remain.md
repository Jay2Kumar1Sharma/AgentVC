# Why the Gaps Are Still Open

_As of 2026-10-17. Follows [gap-analysis.md](gap-analysis.md). Separates **permanent** blockers (design around them) from **temporary** ones (an opportunity for us). Some figures come from secondary sources and vendor statements; corrections welcome in Discussions._

## Short answer

The gaps stay open for four kinds of reasons, which reinforce each other:

1. **Part of the problem is impossible in general.** With black-box tools you can't always know whether an effect happened, can't undo what other people did in response to it, and can't release effects on several tools atomically. So nobody can honestly ship "git for everything", and the claim's impossibility puts people off attempting the possible parts.
2. **It's nobody's job.** Agent vendors only control their own tools, so they build *prevention*. Tool providers don't publish undo semantics. Sandbox vendors are paid to run compute and "throw it away and recreate" is good enough for them. The work that's needed — a neutral, long-tail registry of compensation contracts — has no owner.
3. **Users reject friction.** People approve 93–97% of permission prompts and turn on YOLO mode. Transactional systems historically die from complexity (Microsoft's Transactional NTFS). The frequent pain (bad file edits) is already solved; the catastrophic pain (prod DB deletion, payments) is rare.
4. **It's very new and big to build.** The key papers came out in March–September 2026 with no code. The integration surface (N agents × M tools × 3 OSes, MCP OAuth, TLS interception) is too large for the solo developers who have tried, so the result is many early-stage fragments, each covering one slice.

Reasons 3 and 4 are **temporary**, and reason 2 is **exactly the kind of problem open source handles well**. Only reason 1 is permanent, and it can be turned into a feature (§5).

---

## 1. Fundamental limits (permanent)

The Agent–Tool Boundary paper (arXiv 2609.15397) identifies **four limits that no runtime can overcome with black-box tool calls alone**:

| Limit | What it means | Consequence |
|---|---|---|
| **No authoritative outcome** | Without a status endpoint or idempotent re-issue, a timed-out call might or might not have happened. Retrying risks a duplicate; giving up risks a missing effect; compensating risks undoing something that never happened. | Exactly-once behavior is impossible for many tools today |
| **Open-world reactions** | If someone sees the email or acts on the webhook, retracting it doesn't retract their reaction (anomaly A8) | **No runtime covers A8.** Prevention (staging, visibility control) is the only answer |
| **No atomic release across tools** | A crash between releasing effect #1 (GitHub) and #2 (Slack) leaves a partial state | Requires prepare/commit support from the tools themselves, i.e. cross-organization 2PC, which has never been widely adopted |
| **Non-commuting irreversible effects** | Two agents acting on the same resource in the wrong order can't be repaired afterwards | Needs a mediator *before* release |

Two more limits specific to LLMs:

- **Nondeterministic re-execution.** Idempotency keys and deterministic replay assume a retry sends the *same request*. LLMs regenerate slightly different calls, even at temperature 0 (ACRFence). That's why naive checkpoint-restore duplicated payments in 10/10 trials, and why existing distributed-systems tools don't simply carry over.
- **Data dependency vs. staging.** Staging an effect means the agent doesn't get its result (the new issue ID, the created resource). Pure deferral breaks the agent's next step. Both Atomix and Cordon have to split effects into "bufferable" and "must run now"; that split is unsolved in general.

**Why this keeps people away:** the honest pitch is "undo what can be undone, stage what can be staged, and give a clear report on the rest." That's harder to market than "an undo button", and tools that oversell lose trust (Replit's agent wrongly told the user rollback was impossible; revoco warns that "a wrong mapping produces a confident, wrong rollback").

---

## 2. Structural reasons: nobody's job (temporary for an open-source project)

### 2.1 Agent vendors build prevention, not recovery

- Anthropic's answer to approval fatigue was **auto mode**: a classifier that blocks destructive, irreversible or external commands *before* they run (89% of dangerous test commands blocked, with a reported ~17% false-negative rate). That's prevention.
- Claude Code `/rewind` stops at files because **files are the only state Anthropic controls**. Undoing a Stripe charge requires Stripe-specific knowledge that no agent vendor wants to maintain.
- Each vendor's rollback only works inside its own agent. A tool that works **across agents** has no natural corporate owner.

### 2.2 Tool providers don't publish undo semantics

- MCP census (98,291 tools): **26% have no annotations**; `destructiveHint` is meaningful for only **12.9%**; only **3.1%** actually say "destructive". **52.8%** carry an inapplicable combination. The vocabulary also can't express idempotency keys, status endpoints, compensation or commutativity.
- Even basic idempotency isn't standardized: the IETF `Idempotency-Key` header is **still a draft**. Stripe, the best-known example, prunes keys after ~24 h (v1).
- Providers have no incentive: publishing compensation semantics is work that pays off for *someone else's* runtime.

### 2.3 The chicken-and-egg problem behind fragmentation

Runtimes need contracts → providers won't write contracts until runtimes use them → so each runtime writes **private adapters** (undolog YAML, revoco's 91 unverified specs, toffoli rules, Atomix/Cordon declarations). The Agent–Tool Boundary paper says it directly: coverage "rests on runtime-owned declarations… rather than on a reusable shared contract."

### 2.4 Compensations have always had to be written by hand

This is a 40-year-old problem. Sagas (Garcia-Molina & Salem, 1987) defined compensation; microservices still require developers to **hand-write** each compensating action (Temporal and similar). Two-phase commit/XA never worked across organizations because of lock-holding and coupling. Agents inherit the problem *and* make it worse: the person writing the agent isn't the person who owns the API. It's long-tail maintenance labor — thousands of tools, each with changing APIs, each needing test accounts to verify — and **no company funds that**.

### 2.5 Adjacent vendors are satisfied with their slice

- Sandbox vendors (Docker Sandboxes, E2B, Morph) are paid for compute; Docker's own answer to "agent went off the rails" is *delete the sandbox and recreate*.
- Rubrik already backs up the systems it can restore, so Agent Rewind is a feature on top of a closed enterprise product.
- Turso AgentFS is a showcase for Turso's database; files are the natural boundary for it.

---

## 3. Demand and UX reasons (temporary, but must be designed for)

| Evidence | Implication |
|---|---|
| Users approve **93%** (Anthropic engineering post) to **97%** (later reports) of permission prompts | Approving each call is security theater; a new gate per call will be rubber-stamped too |
| In a 1,053-tester study, people caught an inserted dangerous command only **13.6%** of the time | Human review of individual actions doesn't work |
| YOLO toggles are standard in Cursor, Claude Code (`--dangerously-skip-permissions`), Codex (`--full-auto`), Gemini CLI (`--yolo`), Copilot CLI (`--allow-all`) | Anything that slows the agent gets switched off |
| The 2026 consensus is "sandbox + YOLO" (Docker, Pulumi) | Isolation is seen as *the* answer; it covers local state but not credentials that reach outside |
| Microsoft's Transactional NTFS saw "extremely limited developer interest… due to its complexity" and Microsoft now steers developers away from it | Transactional systems fail on **adoption complexity**, not technical feasibility |
| Frequent mistakes (file edits) are already undoable; catastrophic ones (Replit's prod DB, payments) are rare | It's like insurance: people buy it after the incident. Many early-stage OSS attempts with little adoption so far suggest the pain is felt but demand isn't proven |

**Counter-signal (an opportunity):** reports say users approve 97% of individual permissions but **reject 39% of plans**. People *do* review — at a coarse level. That supports run-level review (Gap 1) over per-call gates.

---

## 4. Timing and execution reasons (temporary)

- **The field is very young.** MCP launched Nov 2024; tool annotations arrived in the March 2025 spec; Replit's DB deletion was July 2025; Rubrik Agent Rewind Aug 2025; ACRFence Mar 2026, DeltaBox May 2026, Cordon Jun 2026, AgentRewind Aug 2026, the Agent–Tool Boundary paper Sep 2026. The *problem definition* was only finished last month.
- **Research doesn't ship code.** Cordon, ACRFence and DeltaBox list no repository; ACRFence's defense **isn't implemented at all**. Academic incentives end at the paper.
- **The integration surface is large:**
  - Different, unstable hook APIs in each agent (Claude Code, Codex, Cursor, Gemini CLI, OpenCode…)
  - MCP proxying: the spec **forbids token passthrough**, so a proxy must become the OAuth client for every upstream (Zuplo's gateway does this); plus stdio vs. streamable HTTP and session IDs
  - Egress capture requires TLS interception with a local CA
  - Sandboxing is different on each OS (Landlock/seccomp, Seatbelt, AppContainer), and seccomp user-notify "is not a security mechanism" on its own
  - Copy-on-write is different on each OS (overlayfs, APFS clonefile, ReFS block cloning)
- **Solo developers build slices.** That explains the pattern of turnback (files), toffoli (classification), undolog (ledger), mcp-approval-gateway (gating), mcp-cassette (replay): each is one person's weekend-sized piece, so none reaches critical mass.
- **Verification is expensive.** Proving an inverse works means running real actions against real (test) accounts on a schedule. revoco verified only 1 of 8 adapter surfaces on live systems.

---

## 5. Gap-by-gap: blocker and how to get around it

| Gap | Main blocker | Permanent? | How to get around it |
|---|---|---|---|
| **1. Run-level transactions** | Data dependency on staged results; no atomic cross-tool release; friction | Partly | Stage only `DEFERRABLE` effects; return placeholder IDs where the contract allows; release in dependency order with a resumable plan; **one review per run** (39%-plan-rejection evidence) |
| **2. Safe replay after rollback** | LLM nondeterminism; needs ledger + proxy together; research only from Mar 2026 | No — mostly timing | Ship the ACRFence fence: recorded responses + argument canonicalization + per-tool equivalence rules; block anything new until an explicit fork |
| **3. Side-effect contract spec** | Chicken-and-egg; nobody's job; long-tail maintenance | No — structural | Be the **neutral registry**: import undolog/revoco/toffoli formats; auto-draft "unverified" contracts from MCP schemas; community drills in CI; later propose as an MCP extension |
| **4. Unified ledger** | Integration effort across layers owned by different projects | No — execution | Reuse rather than rebuild: shadow git or Turso AgentFS for files, existing hooks, one SQLite ledger |
| **5. Drift-aware rollback** | Single-user assumption; A8 unsolvable | Partly | 3-way check for local resources (files, git, rows); for external reactions, *report* honestly instead of claiming to undo |
| **6. DB data diffs** | Vendors focus on schema workflows for humans; logical decoding is complex | No — execution | Postgres only; trigger or logical-decoding changeset capture on a branch or transaction |
| **7. Windows** | Linux-first ecosystem | No | Shadow git everywhere, ReFS block-clone fast path |
| **8. Undo benchmark** | Needs a working system and realistic service fixtures | No | Build alongside: the A1–A8 anomalies + ACRFence attacks + toffoli/Replit incidents as fixtures |

---

## 6. What this means for the project

> These conclusions fed into [master-plan.md](master-plan.md), which is where the current decisions live.

1. **Turn the permanent limit into the product.** Promise *honest accounting*, not universal undo: every run ends with **undone / compensated / still staged / could not undo (and why)**. This directly fixes the Replit failure, where the agent lied about recoverability.
2. **Take on the "nobody's job" work.** Agent vendors won't build a cross-agent registry; tool providers won't write contracts for runtimes they don't own. A neutral open-source spec with drill-verified contracts is the piece that's both missing and defensible.
3. **Design against friction from the start.** Default mode = *record only* (zero friction, still useful for `history` and file undo). Opt in to staging only for external `DEFERRABLE` effects. Review once per run, never per call. Lesson from TxF: the API must be invisible.
4. **Use the timing.** The ACRFence fence is concrete, recent, cited, and unbuilt. Being first to implement it is the fastest way to credibility (and a paper or blog post).
5. **Keep scope small enough to survive.** One agent (Claude Code), the MCP proxy, shadow-git files on all three OSes, Postgres. Use existing tools for the rest.
6. **Watch the main risk.** If Anthropic or OpenAI extend `/rewind` to bash and external effects, a single-agent tool becomes redundant. What protects against that is **cross-agent** coverage and the **community registry**, which a vendor can't build alone.
7. **Demand is uncertain.** The many early-stage attempts with little adoption could reflect how new the field is, *or* weak demand. Validate early: put the run-review demo in front of teams that give agents prod-adjacent credentials (DB, cloud, payments), since that's where the catastrophic pain is.

## Sources

- [Agent–Tool Boundary anomalies, four limits and MCP census (arXiv 2609.15397)](https://arxiv.org/html/2609.15397v1)
- [ACRFence (arXiv 2603.20625)](https://arxiv.org/html/2603.20625) · [Atomix (arXiv 2602.14849)](https://arxiv.org/abs/2602.14849) · [Cordon (arXiv 2606.17573)](https://arxiv.org/pdf/2606.17573)
- [Anthropic: Claude Code auto mode](https://anthropic.com/engineering/claude-code-auto-mode) · [The Register coverage](https://www.theregister.com/a/5285326) · [TechWyse: auto mode default](https://www.techwyse.com/news/platform-updates/anthropic-claude-code-auto-mode-default)
- [WorkOS: agent permissions & blast radius](https://workos.com/blog/agent-permissions-blast-radius) · [Docker: YOLO mode](https://www.docker.com/?p=93273) · [Pulumi: sandboxing YOLO agents](https://www.pulumi.com/blog/sandboxing-coding-agents-yolo-mode/) · [Approval fatigue](https://www.buildmvpfast.com/blog/approval-fatigue-agent-permission-ux-2026)
- [Microsoft: Alternatives to Transactional NTFS](https://learn.microsoft.com/sv-se/windows/win32/FileIO/deprecation-of-txf) · [Wikipedia: TxF](https://en.wikipedia.org/wiki/Transactional_NTFS)
- [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests.md) · [Idempotency keys in 2026](https://ecorpit.com/idempotency-keys-rest-api-safe-retries-2026/)
- [Sagas vs 2PC vs TCC (Atomikos)](https://www.atomikos.com/Documentation/SagasVsTwoPhaseCommitVsTCC) · [Why sagas beat 2PC](https://www.techinterview.org/post/3233476145/sagas-vs-two-phase-commit-distributed-transactions/) · [Sagas, SIGMOD '87](https://rmarcus.info/dbscholar/papers/h44f737c7e1fbb2b5)
- [Zuplo MCP gateway (no token passthrough)](https://zuplo.com/docs/mcp-gateway/how-it-works.md) · [Envoy AI Gateway MCP](https://aigateway.envoyproxy.io/docs/capabilities/mcp/)
- [Docker Sandboxes](https://www.docker.com/blog/docker-sandboxes-run-claude-code-and-other-coding-agents-unsupervised-but-safely/) · [revoco](https://github.com/rsh1k/revoco) · [Replit incident](https://incidentdatabase.ai/reports/5578/)
