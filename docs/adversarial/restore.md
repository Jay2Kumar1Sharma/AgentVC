# Attack review: restore and rollback

Session 4, 2026-10-22, Windows 11, Python 3.12.7. Plan §15.3 row "Restore / rollback". Must hold: **G1, zero bytes of non-agent work changed; partial rollbacks are resumable or reversible**.

## Attacks and the tests that cover them

| Attack | Tests | Result |
|---|---|---|
| ATK-RST-01 user edits after the run | `test_restore.py::test_a_file_the_user_changed_after_the_run_is_a_conflict_and_kept` | conflict, kept, exit 1 in the CLI |
| — user edits between two agent steps (FM-OUT-02) | `test_restore.py::test_a_change_made_between_two_agent_steps_is_never_rolled_back` | the later agent change undone, the earlier one kept ("partly") |
| — someone writes while the rollback runs | `test_restore_attacks.py::test_a_file_changed_while_the_rollback_runs_is_kept` (edited and created files) | write-time compare-and-swap: conflict, kept |
| ATK-RST-02 file deleted then recreated | `test_a_file_the_agent_deleted_and_the_user_recreated_is_kept` | new content kept; identical content counts as already restored |
| ATK-RST-03 file / directory / symlink type changes | `test_a_file_replaced_by_a_folder_and_back_rolls_back`, `test_a_folder_holding_user_files_is_never_removed`, `test_a_file_the_agent_turned_into_a_symlink_comes_back` | holds |
| ATK-RST-04 restoring through a symlinked directory | `test_nothing_is_deleted_or_written_through_a_junction` (identical bytes behind the junction, so content checks alone would pass) | refused: "the path goes through a link" |
| — a hard link to a file outside | `test_a_hard_link_to_a_file_outside_is_never_written_through` | the name is replaced, the outside file untouched |
| — an editor saving by rename (FM-RST-02) | `test_an_editor_saving_by_rename_is_judged_by_content_not_by_inode` | judged by content: same bytes restored, other bytes kept |
| ATK-RST-05 rollback while the agent is running | `test_a_run_with_a_tool_still_running_is_not_rolled_back` | refused unless `--force` |
| ATK-RST-06 rollback of a rollback | `test_rolling_back_twice_finds_everything_already_restored`, `test_rollback_then_rollback_undo_changes_nothing` (metamorphic) | holds |
| ATK-RST-07 crash halfway | `test_a_crash_at_any_point_is_repaired_without_touching_user_work` (every `restore.*` crash point), `test_a_killed_rollback_is_finished_by_repair` (a real process exit), `test_a_new_rollback_finishes_an_interrupted_one_first` | never a third state; repair finishes; user files untouched |
| ATK-RST-08 files locked by an editor (Windows) | `test_a_file_held_open_by_an_editor_is_reported_and_finished_later` | `could-not` ("in use"), partial, finished by running it again |
| ATK-RST-09 permission denied | `test_permission_denied_is_reported_never_hidden` | `could-not`, no temp file left |
| — a tampered tree (FM-RST-08, -09) | `test_paths_from_a_tampered_tree_never_escape_the_workspace` (`..`, empty and backslashed names, `.agentvc/`, `.git/`, `CON`, trailing dots, `:` streams), `test_two_paths_differing_only_in_case_are_both_skipped`, `test_damaged_saved_content_is_reported_not_written` | refused before anything is read or written |
| — `--force` | `test_force_overwrites_a_conflict_but_saves_it_first` | the user's edit comes back with `rollback --undo` |

## Seen to fail against broken builds

Reproducible: `python scripts/broken_build.py eval/broken_builds/restore.json eval/broken_builds/cli.json`. **22 of 22 caught**, among them:

| Broken build | First test that failed |
|---|---|
| every agent change undone without checking the file still holds what the agent left | `test_a_file_the_user_changed_after_the_run_is_a_conflict_and_kept` |
| no compare-and-swap at write time / deletes without it | `test_a_file_changed_while_the_rollback_runs_is_kept[edited.txt]` / `[made.txt]` |
| paths through a junction written and deleted | `test_nothing_is_deleted_or_written_through_a_junction` |
| `..` and empty path parts accepted | `test_paths_from_a_tampered_tree_never_escape_the_workspace` (only after adding names the Windows-name rule doesn't also catch, below) |
| the target overwritten in place instead of replaced | `test_a_hard_link_to_a_file_outside_is_never_written_through` |
| the intent record written as already done | `test_a_crash_at_any_point_is_repaired_without_touching_user_work[restore.after_delete]` |
| a new rollback doesn't repair first | `test_a_new_rollback_finishes_an_interrupted_one_first` |
| per-file results not kept (a forced rollback can't be undone) | `test_rollback_then_rollback_undo_changes_nothing` |
| conflicts hidden from the output; a partial rollback exits 0 | `test_cli_rollback.py::test_a_conflict_is_shown_with_its_reason_and_exits_1` |

## Found while attacking

- **The pre-rollback snapshot read unsafe paths (S1 risk, G6; fixed before commit).** It captured every path in the plan before the path-safety check, so a tampered tree's `../x` would have been *read* from outside the workspace into the store, and `.agentvc/ledger.db` was read and reported as a conflict instead of refused. Unsafe paths are now refused before anything is read; the test checks the pre-rollback snapshot's scope.
- **Deletes through a junction (S0 risk, G1; fixed before commit).** The first version checked links only before writing. A delete whose bytes matched (an identical file behind a junction) would have deleted outside the workspace. Every compare-and-swap now re-checks the path's parents at the moment of writing.
- **An equivalent mutant on Windows.** Accepting `..` survived at first because the Windows rule against trailing dots refuses `..` too. Crafted names that only the first rule refuses (an empty part, a backslash) now catch it on every OS.

## Limits

- The window between the last check and `os.replace` is microseconds; a write landing inside it is overwritten, but the pre-rollback snapshot still holds it (`rollback --undo`).
- A change made by someone else *during* a single tool call is attributed to that call (no hook fires in between). Restore still never overwrites anything changed after the agent left it.
- Empty directories aren't restored. On Windows, restoring a symlink needs the symlink privilege; without it the file is `could-not`.
- The agent isn't told its files changed after a rollback (context sync, v0.1 public); its next step shows the rollback as an outside change.
