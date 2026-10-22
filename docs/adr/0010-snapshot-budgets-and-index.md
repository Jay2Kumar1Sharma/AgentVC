# ADR-0010: Snapshot budgets and the snapshot index

- **Status:** proposed (2026-10-22); the code uses these defaults until the maintainer decides
- **Date:** 2026-10-22
- **Decided by:** pending (drafted in session 4)
- **Related:** plan §7, §10, §14.1, §14.6, G1, G3, G7; ADR-0003, ADR-0005, ADR-0006; design note `docs/design/snapshots.md`; FMEA FM-SNAP-04, FM-SNAP-07

## Context

Session 4 builds the snapshotter. Two choices aren't settled by earlier ADRs:

1. **Budgets.** ADR-0006 limits *ignored* files (1 MB each, 50 MB per snapshot). Nothing limits a tracked 4 GB file, or how long a full snapshot may run inside a hook. A hook has 5 s (ADR-0004); plan §10 asks for < 150 ms p95 at 10k files and < 500 ms at 100k, "or edited paths only with a visible notice".
2. **The private index.** Plan §7 says snapshots use git with a private index (`git add` into `GIT_INDEX_FILE`). Each git process costs tens of milliseconds on Windows (ADR-0003 spike: 119 ms for an incremental 10k-file snapshot through `git add -A`), `git add` follows `.gitignore` (ADR-0006 needs ignored files too), silently skips FIFOs and sockets (G3), and gives no hook to detect a file changing while it's read (FM-SNAP-01).

## Options considered

- Budgets: (a) none, rely on the hook deadline: a huge file or repository makes every hook fail; (b) fixed limits in code; (c) configurable limits with defaults, anything over them listed as unprotected.
- Index: (a) git's index through `git add`; (b) AgentVC writes git-format objects itself and keeps its own stat cache, using git only for `.gitignore` semantics.

## Decision (proposed)

- **Budgets (configurable in `config.toml` `[snapshot]`):** `time_budget_ms = 2000` for a full snapshot taken inside a hook (over it, the snapshot is *incomplete*: the step is shown as not protected and the work continues in `agentvcd`'s background); `max_file_mb = 100` for any single file (over it, *unprotected*). ADR-0006's ignored-file limits stay (`ignored_file_max_kb = 1024`, `ignored_total_mb = 50`). A snapshot row lists at most 200 unprotected paths plus their total count.
- **Index:** AgentVC writes blobs and trees itself, as git **loose objects** in `.agentvc/objects` (readable and checkable by any `git`), and keeps its own stat cache, `.agentvc/snapindex.db` (SQLite). The cache is disposable: deleting it costs one full re-read. git runs only for `check-ignore` (and to read packed objects), isolated from user configuration.
- These numbers are starting points; session 5 measures them against the plan §10 budgets and proposes changes here.

## Consequences

- A file over 100 MB, or a workspace too big to snapshot in 2 s before its baseline exists, is visibly unprotected rather than slowing every hook.
- The object format stays git's, so ADR-0005's ownership rule and any later `gc` (repacking with git) are unchanged. A differential test checks AgentVC's objects against `git hash-object`, `git cat-file` and `git fsck`.
- Revisit after session 5's measurements, or if a user hits the 100 MB limit on files they need protected.
