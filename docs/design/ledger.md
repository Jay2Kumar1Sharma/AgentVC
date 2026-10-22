# Design note: ledger

**Session 3 (v0.1-alpha), plan §4.7, §14.1, ADR-0007.** Module: `agentvc/ledger.py`.

## What and why

`.agentvc/ledger.db` (SQLite, WAL) holds what the agent did. Two needs pull against each other: rows must be updatable (a step goes `PENDING` → `RECORDED`), yet tampering must be detectable (the agent can write to the workspace).

**Schema v1** (ADR-0007):
- `schema_version(version)` — exactly one row; `migrations(version, applied_at, agentvc_version)`.
- `entries(seq, at, kind, run_id, step_id, body, prev_hash, hash)` — the **append-only journal**. Every change is one entry; `hash = sha256(prev_hash ‖ seq ‖ at ‖ kind ‖ body)`, `body` is canonical JSON. This is the hash chain.
- `runs(id, agent, workspace, start_dir, started_at, ended_at, status, flags, parent_run, fork_step)`, `run_sessions(agent, session_id, run_id)`, `turns(id, run_id, seq, prompt_id, prompt, started_at)`, `steps(id, run_id, turn_id, seq, agent_id, tool, category, tool_use_id, input, status, outcome, error, gap_reason, flags, cwd, started_at, ended_at, duration_ms, last_entry)` — **materialised state**, each row pointing at the entry that last set it.

**Writes.** One method per change (`start_run`, `start_turn`, `open_step`, `complete_step`, `record_gap`, …). Each takes the cross-process file lock (bounded by the caller's deadline), opens `BEGIN IMMEDIATE`, appends the entry and updates the tables in the **same transaction**, commits, then writes the **anchor**. Crash points: `ledger.before_commit`, `ledger.after_commit`, plus the atomic write's own.

**Anchor** (plan §14.1). `~/.agentvc/anchors/<sha256 of the workspace path>.json` holds `{seq, hash}` of the newest entry, written atomically after each commit. It lives outside the workspace, so an agent that rewrites `.agentvc/` can't also fix it without leaving the workspace.

**Verify** (`verify() -> Verdict`): recompute the chain; check every materialised row equals the state its `last_entry` describes; compare the head with the anchor.
- chain broken or a row differs from its entry → **tampered** (ATK-LED-01);
- the anchor is *ahead* of the ledger (entries missing) → **truncated or rolled back** (ATK-LED-02); reported, with "a crash can also cause this" when only the last entry is missing;
- the anchor is *behind* → a crash between commit and anchor write; noted, not an alarm;
- SQLite can't read the file → **corrupt** (ATK-LED-03).
Nothing is ever "repaired" into a different history (FM-LED-03, HZ-G3-04). `inspect`, `history` and `check` show the verdict.

**Versions** (ADR-0007): a newer `schema_version` → `LedgerTooNew`, nothing written; a missing or unreadable version row → `LedgerCorrupt`. Forward-only migrations with a `.bak-v<old>` copy first (none exist yet; the mechanism and its tests do).

**Schema 2 (session 4), the first real migration.** Adds two tables, `snapshots` and `rollbacks` (snapshot and restore notes), and nothing else: existing tables keep their columns, so every old row still equals its entry and `verify()` stays clean across the upgrade. Migration 2 runs inside one transaction after the `.bak-v1` copy; its crash point leaves the ledger at version 1, intact. Tested from a real schema-1 ledger written by session 3's code (`tests/fixtures/ledger/schema-1/`), and an AgentVC that knows only schema 1 refuses the upgraded ledger (the existing newer-schema rule).

**Time** comes from the injected clock; ordering never depends on it (`seq` does), so clock changes (ATK-LED-06) can't reorder history.

## Guarantees touched

G2 (no lost writes under concurrency: lock + `BEGIN IMMEDIATE`; FM-LED-01), G3/G8 (tampering, truncation and corruption reported, never hidden).

## How it can fail

| Id | Failure | Detection / test |
|---|---|---|
| FM-LED-01 | Two hooks write at once | concurrency stress test with processes and threads; chain stays valid |
| FM-LED-02 | Agent edits or deletes rows | verify test for edit, delete, reorder |
| FM-LED-03 | Database corrupted | verify reports corrupt; nothing rewritten |
| FM-LED-04 | Newer schema | refusal test |
| FM-LED-05 | Crash between commit and anchor write | crash-point test: anchor behind, noted not alarmed; next write catches up |
