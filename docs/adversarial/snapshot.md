# Attack review: snapshotter and object store

Session 4, 2026-10-22, Windows 11, Python 3.12.7, git 2.49. Plan §15.3 row "Snapshotter". Must hold: **never follows links outside the root; anything it can't capture is marked unprotected, never "saved"**.

## Attacks and the tests that cover them

| Attack | Tests | Result |
|---|---|---|
| ATK-SNAP-01 files changing during the snapshot | `test_snapshot_attacks.py::test_a_file_rewritten_while_it_is_read_is_never_saved_as_whole` (same size, new bytes, mid-read), `test_a_file_that_settles_after_one_retry_is_captured_as_it_ended` | holds: listed as "changed while being read"; captured only once stable |
| ATK-SNAP-02 links outside the project, link loops | `test_junctions_out_of_the_workspace_and_into_itself_are_not_followed`, `test_symlinks_are_stored_as_links_never_followed` (skips without the symlink privilege; runs on CI) | holds: junctions listed, never entered (the loop ends); symlinks stored as links |
| ATK-SNAP-03 hard links | `test_hard_links_are_captured_as_their_content`; restore side `test_restore_attacks.py::test_a_hard_link_to_a_file_outside_is_never_written_through` | holds |
| ATK-SNAP-04 FIFOs, sockets, devices | `test_a_fifo_is_listed_and_never_opened` (POSIX; skipped on Windows) | listed, never opened (opening a FIFO would hang the hook) |
| ATK-SNAP-05 unreadable files | `test_snapshot.py::test_an_unreadable_file_is_listed`, `test_a_folder_that_cannot_be_listed_is_reported` (POSIX) | holds |
| ATK-SNAP-06 very large files | `test_snapshot.py::test_a_file_over_the_size_limit_is_listed_and_not_read` | listed, not even opened |
| ATK-SNAP-07 a huge number of tiny files | `test_thousands_of_tiny_files_stop_at_the_budget_and_never_hang` (slow lane), `test_snapshot.py::test_running_out_of_time_is_incomplete_and_the_work_is_kept`, `test_hook_snapshots.py::test_the_first_snapshot_is_built_in_the_background_never_in_a_hook` | stops at the budget, keeps its work, finishes in the background |
| ATK-SNAP-08 names with newlines or `..` | `test_odd_but_legal_names_round_trip` (newline, backslash, tab, `...` on POSIX); a tree holding `..` is the restore attack below | holds |
| ATK-SNAP-09 case collisions | `test_names_differing_only_in_case_are_both_kept_where_the_disk_allows`; restore side `test_two_paths_differing_only_in_case_are_both_skipped` | holds |
| ATK-SNAP-10 nested repos, submodules, LFS | `test_snapshot.py::test_a_nested_repository_is_listed_not_entered`, `test_an_lfs_pointer_is_captured_as_the_pointer` | nested roots listed as unprotected (ADR-0009: separate snapshots later); LFS content not fetched |
| ATK-SNAP-11 a tampered `.git` | `test_a_tampered_git_folder_runs_nothing_and_changes_nothing` (clean/smudge filter, fsmonitor, excludesFile `*`), `test_objects.py::test_inherited_git_variables_cannot_redirect_agentvcs_git` | holds: nothing runs, content captured byte for byte |
| ATK-SNAP-12 disk full | `test_snapshot.py::test_disk_full_fails_the_snapshot_visibly`, `test_objects.py::test_disk_full_leaves_no_object_and_no_temp_file`, `test_hook_snapshots.py::test_a_failed_snapshot_is_visible_and_fails_open` | snapshot `failed`, message to the user, never protected |
| ATK-SNAP-13 read-only file system | `test_a_read_only_store_fails_the_snapshot_visibly` | idem |
| FM-OBJ-01 the user's own `reset` + `gc --prune` | `test_snapshots_survive_the_user_rewriting_and_pruning_their_repository`, `test_objects.py::test_pinned_snapshots_survive_git_gc_and_packed_objects_still_read` | holds (ADR-0005) |

## Seen to fail against broken builds

Reproducible: `python scripts/broken_build.py eval/broken_builds/snapshot.json eval/broken_builds/snapshot_attacks.json eval/broken_builds/hook_snapshots.json eval/broken_builds/adapter.json`. Each broken build is one exact replacement in a copy of the package; a sentinel test in the same pytest run proves the copy is what the tests import. **31 of 31 caught**, for example:

| Broken build | First test that failed |
|---|---|
| a file changing while it's read is saved anyway | `test_a_file_rewritten_while_it_is_read_is_never_saved_as_whole` |
| junctions entered like folders | `test_junctions_out_of_the_workspace_and_into_itself_are_not_followed` |
| content hashed by git inside the user's repository (its filters run) | `test_a_tampered_git_folder_runs_nothing_and_changes_nothing` |
| only the first 200 unprotected paths kept | `test_more_than_200_unprotected_paths_are_all_remembered` |
| racy index entries trusted / index compares size only | `test_racy_entries_are_read_again` / `test_unchanged_files_are_not_read_again_and_changes_are_seen` |
| ignored heavy folders entered; ignored limits off; nested repositories walked; `.agentvc/` captured | the matching `test_snapshot.py` tests |
| objects read back without checking their hash; git tree order wrong; alternates unreported | the matching `test_objects.py` tests |
| no before snapshot; edits snapshot everything; a failed snapshot silent; strict mode ignores it; a hook waits for the baseline | the matching `test_hook_snapshots.py` tests |

## Found while attacking

- **Truncated unprotected list (S0 risk, G1; fixed before commit).** Keeping only 200 unprotected paths per snapshot row would make the 201st read as *absent*; if a later snapshot captured it, a rollback would take it for a file the agent created and delete it. The full list now goes in a pinned blob (FM-SNAP-11, HZ-G1-14).
- **The broken-build harness itself was blind at first.** `pyproject.toml`'s `pythonpath = ["."]` made pytest import the real package, so every mutation "survived". The sentinel test now fails the run unless the broken copy is imported.
- **An attack test that attacked nothing.** The tampered-`.git` test planted its commands unquoted; git config reads `;` as a comment, so the filter could never run and the test passed vacuously. The commands are quoted now, the filter was confirmed to run under plain git, and a broken build that hashes with the user's git is caught.
- **An equivalent mutant.** Asking the *user's* repository for ignore rules turned out harmless, because AgentVC's own `-c core.excludesFile` overrides a tampered `excludesFile`. Recorded, not counted as caught.

## Limits

- An agent can write `*` into `.gitignore` or `.git/info/exclude`: every file then counts as ignored and falls under ADR-0006's limits. Whatever that leaves out is **listed** as unprotected (G3), not hidden.
- Windows directory listings can lag for a file still open for writing; together with no `ctime` on Windows, a tool that keeps size and modification time can make the index reuse old content (FM-SNAP-09). Restore never trusts the index, so the outcome is a reported conflict, not lost work.
- FIFOs, unlistable folders and symlink cases run only on POSIX or with the symlink privilege; CI covers them once pushed.
