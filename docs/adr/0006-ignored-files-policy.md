# ADR-0006: Which git-ignored files snapshots include (D4)

- **Status:** proposed
- **Date:** 2026-10-18
- **Decided by:** pending the maintainer's review (drafted in Discovery part 2)
- **Related:** plan §7.1 D4, §14.5, G1, G3, G6, G7; FMEA FM-SNAP-02, FM-SNAP-04; spike `scripts/spikes/ignored_files.py`, results `results/spikes/2026-10-18/ignored_files.json`

## Context

A snapshot built with git's own rules would skip everything in `.gitignore`. That includes the files that hurt most when an agent deletes them, such as `.env`, local configs and notes, because they exist nowhere else. But ignored paths also include dependency and build folders that can hold hundreds of thousands of files and would break the snapshot budget (G7).

**Spike (2026-10-18, this machine, aggregate numbers only):** 48 git repositories were found, but **40 have no tracked files and only 3 have 100 or more**, so the sample says little. In what there is, ignored data sits almost entirely in dependency folders (399 MB, about 43k files, in 2 repos); one repo has 10 small other ignored files (60 KB in total); no `.env` files were found.

**The evidence is too weak to tune numbers.** The plan's default stands until a larger sample exists: the spike can be re-run on any machine with real projects (`python scripts/spikes/ignored_files.py --search <folder>`), and v0.1's pinned benchmark corpus (plan §14.6) adds public repositories.

## Options considered

1. **Follow `.gitignore` exactly:** cheap, but `.env` and local configs can't be restored (FM-SNAP-02).
2. **Snapshot everything:** dependency folders blow every budget and fill the disk.
3. **Snapshot ignored files under a budget, minus known heavy folders, always including small dotenv files** (the plan's default).

## Decision (proposed)

- Snapshots include git-ignored files **except** inside known heavy folders: `node_modules`, `.venv`, `venv`, `target`, `dist`, `build`, `out`, `.next`, `.gradle`, `__pycache__` and the other names listed in `scripts/spikes/ignored_files.py` (`HEAVY`), configurable in `config.toml`.
- **Always included:** `.env` and `.env.*` files, and other ignored files of at most **1 MB each**, up to **50 MB of ignored files per snapshot** in total. Both limits are configurable.
- **Anything left out is reported, never silently skipped:** a heavy folder or an over-budget file is listed as *unprotected* in the session banner and in `inspect` (G3). Users can add paths to an always-include or never-include list.
- **Never exported:** ignored files are snapshotted for local restore only. `export`, reports and the diagnostics bundle exclude or redact them by default (G6, plan §14.5).
- Snapshots of ignored files follow the same ownership rule as tracked files (ADR-0005).

## Consequences

- The snapshotter can't use git's default ignore handling; it builds its own include list (ignored minus heavy plus always-include), which needs tests for nested `.gitignore` files, negations and case-insensitive file systems.
- Secret-bearing files now live in `.agentvc/objects`. That makes the store's own `.gitignore` (`*`) and the export exclusions safety-relevant, with tests.
- Revisit the 1 MB and 50 MB limits once the spike has run on a real-world sample.
