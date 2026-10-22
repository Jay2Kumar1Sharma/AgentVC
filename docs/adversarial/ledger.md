# Attack review: ledger and state

Session 3, 2026-10-22, Windows 11, Python 3.12.7. Plan §15.3 row "Ledger and state". Must hold: tampering is detected (hash chain + anchor outside the workspace); corruption is reported, never silently "repaired" into a false history.

## Attacks and the tests that cover them

| Attack | Tests (`tests/test_ledger.py` unless noted) | Result |
|---|---|---|
| ATK-LED-01 editing or deleting rows | `test_tampering_with_rows_or_entries_is_detected` (edit a step, delete a step, insert a step, edit an entry, delete an entry, edit a run), `test_deleting_the_newest_row_stays_visible_after_later_writes`, `test_a_consistently_rewritten_chain_is_caught_by_the_anchor` | detected |
| ATK-LED-02 truncating the newest entries | `test_truncation_is_detected_by_the_anchor` | detected (`truncated`) |
| Entries forged and appended with a valid chain | `test_entries_appended_outside_agentvc_are_detected_and_not_laundered` | detected, and a later legitimate write records an anomaly entry instead of making the forgery look legitimate |
| A deleted anchor | `test_a_deleted_anchor_is_reported` | detected |
| ATK-LED-03 corrupting the database | `test_corrupted_database_is_reported_never_repaired`, `test_a_garbage_file_is_corruption`, `test_missing_version_row_is_corruption_not_a_guess` | reported; the file is left byte-for-byte as found |
| ATK-LED-04 writing into `.agentvc/` through a shell script or Python | `test_protect.py::test_shell_commands_naming_the_store_are_denied` (prevention for plain commands); detection by the chain and anchor as above | holds within the limits below |
| ATK-LED-05 two writers at once | `test_processes_writing_at_once_lose_nothing` (4 processes × 25 transactions), `test_threads_writing_at_once_lose_nothing`, `test_daemon.py::test_parallel_hooks_lose_nothing` | holds: nothing lost, chain valid |
| ATK-LED-06 clock changes | `test_a_clock_jumping_back_cannot_reorder_history` | holds: order is `seq`, never time |
| Crash during a write | `test_crash_before_commit_leaves_the_old_valid_state`, `test_crash_between_commit_and_anchor_is_noted_not_alarmed` | holds |
| Newer schema (ADR-0007) | `test_a_newer_schema_is_refused_and_left_untouched`, `test_hook.py::test_a_newer_ledger_is_a_visible_recording_failure` | refused, visibly |

## Seen to fail against broken builds

| Broken build | Tests that failed |
|---|---|
| verify ignores the anchor | 9, incl. `test_truncation_is_detected_by_the_anchor` |
| rows not compared with their entries | 22 |
| row ids from SQLite (reused after a delete) | `test_deleting_the_newest_row_stays_visible_after_later_writes` |
| no head check at write time | `test_entries_appended_outside_agentvc_are_detected_and_not_laundered` |
| hash not checked | `test_a_consistently_rewritten_chain_is_caught_by_the_anchor`, `test_processes_writing_at_once_lose_nothing` |

## Design points that came out of attacking

- **Laundering.** Without care, entries appended by the agent would be adopted by the next real write (its anchor would then include them). The anchor now also holds the *pending* head written just before each commit, so a crash between commit and anchor write (benign) is told apart from foreign entries (an anomaly entry is chained in, permanently).
- **Id reuse.** SQLite reuses the highest rowid after a delete, which would hide the deletion of the newest row behind the next insert. Row ids now come from the hash-chained entries.

## Limits

- An agent that deletes **both** the ledger and the anchor (in the user's home folder) leaves a fresh, consistent-looking history: the deletion is noticed only if a later check knows a ledger existed. Session 6's session-start check (G8) should look for an anchor without a ledger and the reverse.
- The anchor is written without `fsync`; after a power loss it can lag or lead the ledger. Both cases are reported honestly ("a crash can also cause this"), never repaired.
- `verify()` reads everything; fine for the CLI, too slow for a hook on a large ledger. Hooks don't call it.
