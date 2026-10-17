# Hazard analysis from the guarantees

_First draft, Discovery (2026-10-18). Reviewed before each release (plan §16.1)._

For each guarantee (plan §2), the conditions under which it could be violated. A hazard is a *situation*, not yet a bug: each one needs at least one test proving AgentVC handles it, listed in [`tests/traceability.yml`](../../tests/traceability.yml) (`TODO` until it exists). Related failure modes are in [`fmea.md`](fmea.md).

## G1: Never destroy user work

| ID | Hazard | FMEA |
|---|---|---|
| HZ-G1-01 | Restore writes to a path the user changed after the run | FM-RST-03 |
| HZ-G1-02 | User edits a file *during* the run, and the edit is attributed to the agent | FM-OUT-01 |
| HZ-G1-03 | Restore follows a symlink, or writes through a symlinked directory, to outside the workspace | FM-RST-04, FM-SNAP-03 |
| HZ-G1-04 | Crash or kill between two file writes of one rollback | FM-RST-01 |
| HZ-G1-05 | The snapshot holds a torn file (written during the read), and restore writes it back | FM-SNAP-01 |
| HZ-G1-06 | Snapshot objects disappear (user's `git gc`, AgentVC's own `gc`, disk full) | FM-OBJ-01, FM-OBJ-02, FM-GC-01 |
| HZ-G1-07 | A forced rollback runs without saving the current state first | FM-RST-05 |
| HZ-G1-08 | Compare-and-swap compares metadata (inode, mtime) instead of content | FM-RST-02 |
| HZ-G1-09 | A path changes type (file ↔ directory ↔ symlink) between snapshot and restore | FM-RST-06 |
| HZ-G1-10 | Two names map to one file (case-insensitive file system, Unicode normalisation) | — |
| HZ-G1-11 | Rollback runs while the agent is still writing | — |
| HZ-G1-12 | An older AgentVC opens a newer ledger and misreads it | FM-LED-04 |

## G2: Nothing silent

| ID | Hazard | FMEA |
|---|---|---|
| HZ-G2-01 | A Pre event arrives with no Post event, and no GAP is recorded | FM-HOOK-02 |
| HZ-G2-02 | AgentVC's hook crashes or times out without recording a GAP | FM-HOOK-01 |
| HZ-G2-03 | A payload field is renamed or missing, and the event is dropped instead of logged as a blind spot | FM-ADP-01 |
| HZ-G2-04 | A tool or event is not subscribed in the wiring | FM-ADP-03 |
| HZ-G2-05 | Subagent or parallel-batch actions are missed or misattributed | FM-ADP-02 |
| HZ-G2-06 | Concurrent hooks lose a ledger write | FM-LED-01 |
| HZ-G2-07 | The agent changes directory or adds a root outside the project, and its actions there go unreported | — |
| HZ-G2-08 | An action bypasses hooks entirely (a script calls an API); must be documented as a limit | — |
| HZ-G2-09 | An install writes a hook event the agent version doesn't know, and the agent silently drops all hooks | FM-INS-03 |

## G3: Honest accounting

| ID | Hazard | FMEA |
|---|---|---|
| HZ-G3-01 | A report says "restored" for a file that was skipped, conflicted or locked | FM-RST-07 |
| HZ-G3-02 | A path over the budget, or unreadable, is reported as protected | FM-SNAP-04, FM-SNAP-05 |
| HZ-G3-03 | An irreversible or unknown effect is classed as restorable | FM-CLS-01, FM-CLS-02 |
| HZ-G3-04 | A corrupt ledger is silently "repaired" into a false history | FM-LED-03 |

## G4: Never repeat the world

| ID | Hazard | FMEA |
|---|---|---|
| HZ-G4-01 | A replayed or forked step re-executes an effect the parent already performed | FM-FEN-01 |
| HZ-G4-02 | The same effect is reached through a reworded command or another tool | FM-FEN-01 |

## G5: Nothing leaves early

| ID | Hazard | FMEA |
|---|---|---|
| HZ-G5-01 | A staged effect is released before `commit` | FM-OBX-02 |
| HZ-G5-02 | A crash between release and record makes the next `commit` release again | FM-OBX-01 |
| HZ-G5-03 | `commit` runs twice at once (two terminals) | FM-OBX-01 |

## G6: Local and private

| ID | Hazard | FMEA |
|---|---|---|
| HZ-G6-01 | A secret in a command, output or environment is written to the ledger | FM-SEC-01 |
| HZ-G6-02 | An export includes `.env` or other secret-bearing files | FM-SEC-01 |
| HZ-G6-03 | AgentVC's own code makes a network call | — |
| HZ-G6-04 | The debug log or diagnostics bundle contains secrets, prompts or file contents | FM-SEC-01 |
| HZ-G6-05 | Development tooling (usage study, payload capture) writes transcript content, prompts or secrets into the repository | FM-DEV-01 |

## G7: Cheap enough to leave on

| ID | Hazard | FMEA |
|---|---|---|
| HZ-G7-01 | Cold-start hook latency exceeds 50 ms p95 (Windows in particular) | FM-HOOK-03 |
| HZ-G7-02 | Snapshotting a huge or pathological repository blows the budget or hangs | FM-SNAP-04 |
| HZ-G7-03 | Disk use grows without bound | FM-GC-01 |

## G8: Can't be silently switched off

| ID | Hazard | FMEA |
|---|---|---|
| HZ-G8-01 | The agent removes or edits AgentVC's hooks in the agent's settings | FM-CFG-02 |
| HZ-G8-02 | The agent switches AgentVC to `observe` mode | FM-CFG-01 |
| HZ-G8-03 | The agent deletes or edits `.agentvc/` (history, config) | FM-LED-02 |
| HZ-G8-04 | The agent runs `agentvc purge`, `uninstall` or `rollback --force`, or kills AgentVC's processes | — |
| HZ-G8-05 | Hooks fail on every call (wrong interpreter) and nobody notices | FM-INS-01 |
