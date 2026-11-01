# ADR-0005: AgentVC owns every object its snapshots reference (D3)

- **Status:** proposed
- **Date:** 2026-10-18
- **Decided by:** pending the maintainer's review (drafted in Discovery part 2)
- **Related:** plan §7.1 D3, G1; FMEA FM-OBJ-01; hazard HZ-G1-06; spike `scripts/spikes/object_ownership.py`, results `results/spikes/2026-10-18/object_ownership.json`

## Context

Snapshots are git trees in AgentVC's own object store (`.agentvc/objects`). Git **alternates** would let that store borrow objects already in the user's repository instead of copying them, saving disk and time. But the user owns their repository and may rewrite history and prune it at any time.

**Spike (git 2.49, Windows, 2026-10-18).** The user commits v1, then v2 plus a new file. AgentVC snapshots the work tree into its store and keeps a ref to the snapshot. The user then drops the v2 commit (`reset --hard HEAD~1`), expires reflogs and runs `git gc --prune=now`:

| Store set-up | Restorable before the prune | Restorable after | Missing objects |
|---|---|---|---|
| Borrows through alternates | yes | **no** | 1 (the snapshot's tree) |
| Owns every object (no alternates) | yes | yes | 0 |
| Borrows, then `git repack -a -d` and drops the alternates file | yes | yes | 0 |

A second finding came from a bug in the spike itself: an alternates file with a Windows line ending (`\r\n`) is **silently ignored** by git, apart from a warning on stderr. Anything relying on alternates must check they're actually in effect.

## Options considered

1. **Borrow through alternates permanently:** cheapest, but snapshots break when the user prunes (data loss in AgentVC's own history, G1).
2. **Own every object:** every snapshot writes its blobs into AgentVC's store. Costs disk and the time to hash new content (measured in ADR-0003's spike: 10k files, one changed, p95 132 ms).
3. **Borrow while snapshotting, then make the store self-contained** (repack, drop alternates): survives, but repacking on every step is too slow for a hook; feasible only as background maintenance.

## Decision (proposed)

- **AgentVC owns every object any snapshot references.** The snapshot store has **no alternates file in normal operation**; `git add` into its private index writes every new blob into `.agentvc/objects`.
- Alternates may be used only as a **read-only speed-up** for the very first (baseline) snapshot of a large repository, and only if, before the step is marked recorded, the store is made self-contained (`repack -a -d`, alternates file removed) and a check confirms every referenced object is readable without alternates. Until then the snapshot is marked *pending*, never *protected*.
- Every snapshot is pinned by a ref inside the store (`refs/agentvc/...`) so AgentVC's own `gc` (v0.1) can't prune it while a ledger row references it (FM-GC-01).
- `agentvc check` and a test verify ownership: snapshot, rewrite and prune a throwaway user repo, then restore.

## Consequences

- Disk use grows with every changed file; `inspect` shows disk per run, and `agentvc gc` with retention arrives in v0.1 (plan §14.1).
- Restore never depends on the user's `.git` being intact, which also covers users who delete or re-clone their repository.
- Revisit only if a measured disk cost forces it; any return to alternates needs a new ADR plus the verification above.
