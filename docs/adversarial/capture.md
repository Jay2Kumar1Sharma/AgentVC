# Attack review: hook capture (adapter, recorder, hook, agentvcd, self-protection)

Session 3, 2026-10-22, Windows 11, Python 3.12.7. Plan §15.3 rows "Hook capture / adapters" and "Self-protection" (part). Must hold: **G2**, every action is a step or a visible gap; unknown payloads land in the blind-spot log; hooks never crash or hang the agent.

## Attacks and the tests that cover them

| Attack | Tests | Result |
|---|---|---|
| ATK-CAP-01 malformed, truncated or huge payloads | `test_adapter_claude_code.py::test_malformed_bodies_become_blind_spots` (empty, not JSON, a list, truncated, a string, binary garbage, 100k-deep nesting, a 3 MB id), `test_huge_but_valid_write_payload_is_recorded_without_its_content` (5 MB file), `test_daemon.py::test_oversized_bodies_are_refused_and_logged` | holds: blind spot (shape only, ≤ 16 KB), never an exception |
| ATK-CAP-02 missing or renamed fields | `test_dropping_or_retyping_any_field_never_crashes` (every field of every handled fixture of both versions, dropped and retyped), `test_renamed_field_is_a_blind_spot` | holds |
| ATK-CAP-03 NUL and odd Unicode | `test_nul_and_lone_surrogates_are_made_storable` (NUL, lone surrogate, RTL override, accents) | holds: stored text is valid UTF-8 without NUL |
| ATK-CAP-04 duplicated or out-of-order events | `test_recorder.py::test_out_of_order_post_then_pre_stays_a_gap_marked_late`, `test_duplicate_events_change_nothing_and_are_reported`, `test_a_post_arriving_after_its_gap_completes_the_step` | holds |
| ATK-CAP-05 a Pre with no Post | `test_pre_without_post_becomes_a_gap_when_the_turn_ends` (Stop, next prompt, SessionEnd, SessionStart), `test_subagent_stop_closes_only_that_subagents_steps`, `test_batch_reconciles_missing_posts_and_unseen_calls` | holds |
| ATK-CAP-06 reused `tool_use_id` | `test_a_reused_tool_use_id_opens_a_new_flagged_step` | holds: a new step flagged `reused-id`, never merged |
| ATK-CAP-07 events from another project directory | `test_an_unknown_session_from_another_project_is_not_recorded`, `test_a_known_session_that_leaves_the_workspace_is_recorded_and_flagged` | holds |
| ATK-CAP-08 two sessions at once | `test_two_sessions_at_once_get_separate_runs`, `test_daemon.py::test_parallel_hooks_lose_nothing` (12 parallel tool calls) | holds |
| ATK-SELF-04 agent kills AgentVC processes | `test_daemon.py::test_liveness_starts_the_daemon_and_recovers_from_a_kill` | detected: the next prompt restarts `agentvcd`, records a `daemon-down` GAP and tells the user |
| Writes into `.agentvc/` (plan §14.1) | `test_protect.py` (edit tools with `..`, case, a directory junction; shell commands in Bash and PowerShell; and ordinary work that must **not** be denied) | holds for edit tools and plain shell commands; see limits |
| Hook failure (ADR-0004) | `test_hook.py` fault-injection tests: I/O error, locked ledger past the deadline, recorder crash, newer ledger, disk full | fails open with a message and a GAP where possible; strict mode blocks PreToolUse |

## Seen to fail against broken builds

Each group was run against deliberately broken builds (the module patched in a child process, then the test file run); every break made at least one test fail:

| Component | Broken build | First test that failed |
|---|---|---|
| Adapter | content keys not reduced | `test_editor_content_is_reduced_to_its_size` |
| Adapter | required fields not checked | `test_dropping_or_retyping_any_field_never_crashes` |
| Adapter | parse errors other than `ValueError` not caught | `test_malformed_bodies_become_blind_spots[6]` (deep nesting) |
| Adapter | strings not sanitised | `test_nul_and_lone_surrogates_are_made_storable` |
| Recorder | Post without Pre dropped | `test_post_without_pre_is_a_visible_gap` |
| Recorder | pending steps never closed | 6 × `test_pre_without_post_becomes_a_gap_when_the_turn_ends[...]` |
| Recorder | no foreign-project check | `test_an_unknown_session_from_another_project_is_not_recorded` |
| Recorder | one run per workspace instead of per session | `test_two_sessions_at_once_get_separate_runs` |
| Recorder | reused id merged into the old step | `test_duplicate_events_change_nothing_and_are_reported` and 2 more |
| Recorder | PostToolBatch ignored | `test_batch_reconciles_missing_posts_and_unseen_calls` |
| Hook | no GAP on failure / failure silent / exceptions escape | 5–6 tests each |
| Hook | strict mode ignored | `test_strict_mode_blocks_a_pre_tool_use_it_cannot_record` |
| Hook | self-protection skipped | `test_writes_into_the_store_are_denied_and_recorded_as_denied` |
| Protect | links not resolved | `test_a_junction_into_the_store_is_followed` |
| Protect | shell check over-matches (`agentvc`) | `test_ordinary_work_is_not_denied` (2 cases) |
| agentvcd | token and Host not checked | 3 tests |
| agentvcd | no `daemon-down` GAP | `test_liveness_starts_the_daemon_and_recovers_from_a_kill` |
| agentvcd | no notices | `test_liveness_reply_reports_gaps_blind_spots_and_rejections` |
| agentvcd | `SO_REUSEADDR` instead of exclusive bind | `test_no_other_socket_can_take_over_the_daemons_port[True]` |
| Wiring | event list of the version ignored | `test_config_names_only_events_the_version_knows[2.1.72]` and 2 more |
| Wiring | event wired without an adapter parser | `test_wiring_adapter_and_recorder_agree` |

These runs were ad hoc (session 5 builds `eval/adversarial/` with attacks as data, plan §15.2).

## Found while attacking (fixed in this session)

- **Port takeover on Windows (S1, G2/G6).** With `SO_REUSEADDR` (Python's `HTTPServer` default), any other local socket could bind `agentvcd`'s port and receive hook traffic, token included. Now an exclusive bind on Windows.
- **Self-connection (S2).** A port picked from the ephemeral range let a health check connect to itself while nothing listened, and read its own request back. Ports now come from 20000–32767.
- **Silent rejections (S1, G2).** A request with a wrong token or Host is refused; that is now counted and reported at the next prompt.

## Limits (not covered yet)

- A shell write into `.agentvc/` hidden behind variables, encodings or a script isn't denied; the ledger's hash chain and anchor detect what it changes (see `ledger.md`). Self-protection for hook settings and `config.toml` (ATK-SELF-02, -03) is session 6.
- Whether Claude Code's own `http` client sends `Host: 127.0.0.1:<port>` and the literal token header is **not verified** with a real Claude Code run (U-20). If not, every hook is refused, which is now reported at the next prompt, not silent.
- Actions that never reach a hook at all (both hooks lost) are found only through `PostToolBatch`; transcript reconciliation is later (ADR-0004).
