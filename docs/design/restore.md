# Design note: restore, rollback, undo / redo, repair

**Session 4 (v0.1-alpha), plan §2 G1, §4.3, §4.4, §14.1, §14.2, §15.3 "Restore / rollback".** Module: `agentvc/restore.py`; CLI in `agentvc/cli.py`.

## What and why

`rollback` puts files back to how they were before the agent changed them, **without ever overwriting work that isn't the agent's** (G1), and every rollback can itself be undone.

**What a target means.** Every target means "back to just before it":

| Command | Rolls back |
|---|---|
| `agentvc rollback <run>` | every step of the run |
| `agentvc rollback <run>:<step>` | that step and every later step of the run |
| `agentvc rollback <run>:t<turn>` | that turn and every later turn |
| `agentvc undo` | the newest turn (of the newest run) that still has changes in place |
| `agentvc redo` | re-applies the newest `undo` not yet redone |
| `agentvc rollback --undo` | reverses the newest `rollback` or `undo` not yet reversed |

**Planning, per file** (diagram 4.4, extended to a chain). For each path the agent changed in the range, its changes are listed in step order: step *k* changed it from `before_k` to `after_k` (from that step's snapshots, within their scope). Going backwards from the file's **current** content:
- current = what step *k* left → the target becomes `before_k`, continue with the previous change;
- current = `before_k` → already undone, continue;
- anything else → **stop**: someone else changed it (after the run, or between two agent steps: an outside change). Nothing before that point is undone.

The file ends at the last target reached. So a change made by the user between two agent steps is never rolled back (FM-OUT-02): the later agent change is undone, the earlier one is reported as blocked. If nothing could be undone, the file is a **conflict**: listed with the reason, never written. A step whose before or after snapshot is missing, incomplete or failed can't be undone; it is listed (G3).

**Doing it** (every path through `IO`; crash points `restore.*` between every step):
1. Lock (`.agentvc/snapshot.lock`, shared with the snapshotter, so no snapshot sees a half-written rollback). Finish any interrupted rollback first (step 7). Refuse if the run has a tool call still running, unless `--force`.
2. **Pre-rollback snapshot** of every path the plan may write, taken before anything is written, pinned, and recorded (`phase = pre-rollback`). A path whose current state can't be saved (unreadable, changing, a directory with content) is never written, even with `--force`.
3. **Intent record**: a `rollbacks` row with status `started` and the plan (for each path: expected current content, target content). This is the write-ahead log.
4. For each path: check it is safe (below), then **compare-and-swap**: read the real bytes now; only if they are exactly what the pre-rollback snapshot saved, write the target (temp file in the same folder, `fsync`, `os.replace`) or delete it. Deletions go first (deepest first), so type changes (file ↔ directory) work; folders left empty by a deletion are removed.
5. `--force`: conflicts are written too, but only paths whose current state is in the pre-rollback snapshot (so `rollback --undo` restores them).
6. Mark the row `done` (all written) or `partial`, with a per-file result: `restored`, `deleted`, `already`, `conflict`, `blocked` (stopped at an outside change), `forced`, `could-not` (reason). The CLI prints the counts: restored / already / conflicts / could not undo and why (G3).
7. **Recovery** (`agentvc repair`; also run first by `rollback`, `undo`, `redo`; reported at the next prompt by the hook): a row still `started` was interrupted. For each planned path: current = target → done; current = expected → do the write now (same compare-and-swap); anything else → conflict. Stray temp files of that rollback are removed. The row ends `done` or `partial`, flagged `recovered`.

**Undoing a rollback** (`redo`, `rollback --undo`): a new rollback whose plan comes from the old one's results: each path it wrote goes back to what it held before, by the same steps 1–6 (its own pre-rollback snapshot first, compare-and-swap against what the old one wrote). The old row gets `inverted_by`.

**Safe paths** (FM-RST-04, ATK-RST-04). A path from a tree is written only if every component is a normal name (not empty, `.`, `..`, no separators or NUL; on Windows no `:`, reserved device names or trailing dots and spaces), it isn't under `.agentvc/` or `.git/`, and every existing parent folder is a real directory: not a symlink, junction or other reparse point. Two paths that differ only by case on a case-insensitive file system are both skipped as a conflict. Writing replaces the directory entry (`os.replace`), so a hard link to a file outside the workspace is never written through.

**Locked and protected files** (ATK-RST-08, -09): a file another program holds open (Windows) or that can't be written is `could-not` with the reason; the rollback is `partial`, and running it again later finishes it (already-restored files are skipped).

## What it borrows

Compare-and-swap per file is plan diagram 4.4. Write-ahead intent records and roll-forward recovery are standard database practice. Nothing copied.

## Guarantees touched

- **G1:** content compare (never inode or mtime, FM-RST-02); outside changes never rolled back; the current state is saved before anything is written, including with `--force` (FM-RST-05); no writes through links; crash recovery with no third state (FM-RST-01).
- **G3:** every file's result and every step that can't be undone is reported; a partial rollback says so.

## Limits

- A rollback doesn't tell the agent its files changed (context sync, plan §14.1, comes in v0.1 public). The agent's next step shows the rollback as an outside change.
- Empty directories aren't restored. On Windows, restoring a symlink needs the symlink privilege; without it the file is `could-not`.
- The check-then-replace window is a few microseconds; a write landing inside it is overwritten. The pre-rollback snapshot still holds the state it replaced.

## How it can fail (FMEA rows; existing ones now have tests)

| Id | Failure | Detection / test |
|---|---|---|
| FM-RST-01..07 | as in `docs/testing/fmea.md` | tests named in `tests/traceability.yml` |
| FM-RST-08 | A tampered tree holds `..`, an absolute path or a reserved name | path-safety test with crafted trees |
| FM-RST-09 | Two paths differ only by case on a case-insensitive file system | case-collision test with crafted trees |
| FM-RST-10 | A rollback is interrupted and a new one starts on top of the half-done one | new rollbacks repair first; crash-point test |
| FM-RST-11 | Rollback while the agent is running a tool | refused while a step is pending; test |
