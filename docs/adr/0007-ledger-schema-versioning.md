# ADR-0007: Ledger schema versioning (D5)

- **Status:** proposed
- **Date:** 2026-10-18
- **Decided by:** pending the maintainer's review (drafted in Discovery part 2)
- **Related:** plan §7.1 D5, §16.2 "Upgrade / migration tests"; FMEA FM-LED-04; hazard HZ-G1-12

## Context

`ledger.db` (SQLite) holds every run, turn, step and effect. Its schema will change in every milestone: effects in v0.2, forks in v0.3, the outbox in v0.4. Users will upgrade AgentVC with existing history, and sometimes downgrade. An AgentVC that misreads a ledger written by another version can show a false history (G2, G3) or roll back the wrong files (G1). Plan §7.1 asks for a schema version from day one.

## Options considered

1. **No version; infer from the tables present.** Fragile: two versions can share tables with different meanings.
2. **SQLite `PRAGMA user_version` only.** Built in and cheap, but holds one integer with no history of which migrations ran or when.
3. **A `schema_version` table plus a `migrations` history table.** Explicit, inspectable with any SQLite tool, and records who migrated when.

## Decision

- The ledger has a **`schema_version` table with exactly one row** (`version INTEGER NOT NULL`), created with the ledger, starting at 1. A **`migrations` table** records each applied migration (`version`, `applied_at`, `agentvc_version`).
- **Migrations are forward-only**, numbered, and each runs in one SQLite transaction. Before the first migration of an upgrade, AgentVC copies the ledger to `ledger.db.bak-v<old>` (atomic write helper), so an upgrade can always be undone by hand. Each migration step has named crash points.
- **A newer schema is refused:** an AgentVC that finds `version` higher than it knows opens nothing for writing, records nothing, and says plainly which version wrote the ledger and how to upgrade. In a hook this is a visible recording failure (ADR-0004 / D2), never a silent "repair".
- A missing or unreadable `schema_version` row is treated as corruption: reported, never guessed (FM-LED-03).
- The same rule applies to AgentVC's other on-disk formats as they appear: `config.toml` and contract files carry a `version` / `format` field.

## Consequences

- Every release runs upgrade tests that open each earlier version's `.agentvc/` and check the data is intact (plan §16.2), and a downgrade test that checks the refusal.
- A migration that can't be written forward-only (e.g. a destructive reshaping) needs its own ADR.
- Revisit if SQLite stops being the ledger store.
