# toffoli, undolog, revoco: classify-and-compensate libraries

Three independent, early-stage projects that classify agent actions as reversible, compensable or irreversible and run inverse actions for side effects. They're grouped here because they share a taxonomy and a gap: none of them watches an existing coding agent's shell.

| | toffoli | undolog | revoco |
|---|---|---|---|
| Source read | [theo-ai-lab/toffoli](https://github.com/theo-ai-lab/toffoli) `e9bee29b6981` (2026-10-09): `README.md`, `lib/cli.ts`, `lib/mcp/` | [saravanx41/undolog](https://github.com/saravanx41/undolog) `93b392ff57f1` (2026-10-03): `README.md` | [rsh1k/revoco](https://github.com/rsh1k/revoco) `3b27d1d99f33` (2026-09-07): `README.md`, `src/revoco/adapters/workstation.py`, `src/revoco/adapters/devops.py` |
| Licence | Apache-2.0 | MIT | Apache-2.0 |
| Language | TypeScript | Python (+ Postgres) | Python |
| Tried | no | no | no |

## How each one works

- **toffoli:**
  - **Classifies actions** before or after they run: deterministic rules first (HTTP verb semantics, SQL DML/DDL, payments, hard deletes), with a gated LLM judge for the rest, badged `llmAssisted`. Every verdict must cite a rule. "Unknown → IRREVERSIBLE, and escalate."
  - **Checkpoint and recover** are separate MCP tools (`toffoli.checkpoint`, `toffoli.classify`, `toffoli.recover`), working against an in-memory sandbox "world" or a folder (`TOFFOLI_MCP_FS_ROOT`). The agent has to *call* `checkpoint` before a risky step; nothing captures automatically.
  - Publishes its own recall numbers, which are modest (COMPENSABLE 0.67, IRREVERSIBLE 0.83 on a 51-action gold set).
- **undolog:**
  - The agent's tools are wrapped with `wrap()`, and every call becomes a row in an **append-only Postgres ledger**.
  - Each tool is described by a **YAML spec**: its class, how to capture the before-state, and the compensating call with an idempotency key.
  - "Freeze" stops an agent thread; rollback undoes what it can (dry-run first) and prints a **blast-radius report** of what it couldn't. Tools without a tested recipe are **unknown**.
  - Ships adapters for Stripe, Postgres and REST.
- **revoco:**
  - **Plans the undo before the action runs** ("the only moment prior state still exists"), runs a pipeline of classify, enforce, plan undo, record, verify authority, detect and verdict, and keeps a **hash-chained ledger**.
  - **Recovery drills** run the real action and its inverse against a canary and compare state; a failing or stale drill can only *downgrade* a tool's class.
  - An **MCP proxy** gates `tools/call` on recoverability.
  - Only its "workstation" adapter (filesystem and git) has been validated against real systems; the other seven are specifications.

## (a) `rm -rf` run through the shell

None of them sees a shell command run through Claude Code's own Bash tool (**inferred** from their integration points: wrapped tools, an MCP proxy, explicit checkpoint calls).
- revoco has a structured `fs.delete_tree` tool whose inverse needs a full capture of the tree first. Without that capture it defaults to **irreversible**, so "a human sees every recursive delete before it happens".
- toffoli recovers a folder only if the agent called `checkpoint` on it beforehand.

## (b) `git push`, then undo

- **revoco:** treats a force push or a deleted branch as compensable. It records the previous commit before the action, then updates the ref back through the GitHub API, with a check that the objects are still present.
- **undolog:** would need a REST spec for the forge's API; none ships for git (**inferred**).
- **toffoli:** would classify the push and escalate it, not undo it (**inferred**).

## Limits

All three are early-stage, with small evaluation sets or mostly-unvalidated adapters. Each requires the agent to call tools *through* them, so effects made through the shell are invisible to them.

## For AgentVC (ideas only, credited; v0.2 onward)

- **Borrow:**
  - **The taxonomy**, plus **"unknown" as a first-class class** that only evidence can promote.
  - **Every classification cites its rule**, and an LLM may never silently authorize an undo.
  - **YAML contracts** with an idempotency key on every compensation (undolog).
  - **Drills that can only downgrade a class** (revoco).
  - **Name the residue** of each compensation (what survives the undo), and plan the undo *before* the action.
  - **A hash chain doesn't detect truncation:** anchor the head outside the workspace. revoco says this explicitly; plan §14.1 already does it.
- **Differ:**
  - AgentVC captures effects made through the agent's **shell** by recording the agent's own hooks, instead of requiring agents to call wrapped tools.
  - It snapshots the workspace automatically, without the agent's cooperation.
