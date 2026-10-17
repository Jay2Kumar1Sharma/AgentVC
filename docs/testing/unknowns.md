# Unknowns register

_First draft, Discovery (2026-10-18). Ongoing (plan §16.1)._

Things not yet understood that could change the design or hide a failure. Each has an experiment that would answer it. When one is answered, record the answer and where the evidence is (a `results/` folder, an ADR, a fixture), and keep the row.

| ID | Unknown | Why it matters | Experiment | Status |
|---|---|---|---|---|
| U-01 | Cold-start latency of a Python hook on Windows, with Defender scanning | Decides per-call process vs background process (D1); G7 | Spike S1 (session 2) | open |
| U-02 | Does Claude Code run hooks for parallel tool calls concurrently, and in what order do Pre/Post events arrive? | Ledger locking and step attribution (FM-LED-01, FM-ADP-02) | Payload capture with a parallel tool batch; record arrival times | **answered (2.1.72, `-p`)**: both PreToolUse events arrived before either PostToolUse; the two PostToolUse hooks ran in separate processes 4 ms apart, i.e. concurrently. The ledger must lock. Evidence: `tests/fixtures/hosts/claude-code/2.1.72/0038`–`0041` |
| U-03 | Is `tool_use_id` unique across subagents and sessions? | Pairing Pre with Post events | Payload capture with a subagent | partly answered: ids look like `toolu_…` and were unique per call; a subagent's calls carry `agent_id`/`agent_type` (`0051`, `0052`). Uniqueness across sessions not proven |
| U-04 | When a hook exceeds its timeout, does the tool still run, and is PostToolUse still sent? | Fail-open/closed behaviour (D2), GAP detection | Throwaway project with a deliberately slow hook | open |
| U-05 | What does a PowerShell tool payload look like compared with Bash? | Windows shell capture | Payload capture on Windows | partly answered: Claude Code 2.1.72 offered **no PowerShell tool** in a default setup on Windows (`0013`); Bash runs through Git Bash. Recheck on newer versions |
| U-06 | Does PostToolUse fire when a tool fails, or is there a separate failure event? | GAP vs failed step | Payload capture with a failing command | **answered (2.1.72)**: a failing Bash command fires `PostToolUseFailure` (`error`, `is_interrupt`) **instead of** PostToolUse (`0061`). The adapter must treat it as the Post event, or every failed command becomes a false GAP |
| U-07 | Do `/rewind` restores trigger any hook event? | Outside-change detection would see them as user edits | Payload capture, then `/rewind` | open |
| U-08 | Which hook events exist in the installed Claude Code version (e.g. `PostToolBatch`, `ConfigChange`, `CwdChanged`)? | Wiring (plan §6.2), G8 config-change detection | Compare the hooks reference with captured events | **answered (2.1.72)**: 21 events (from the binary's event list, confirmed by capture). `PostToolBatch`, `CwdChanged`, `DirectoryAdded`, `FileChanged`, `PostCompact` don't exist yet; `ConfigChange`, `SubagentStart`, `PostToolUseFailure` do. **Settings naming an unknown event register no hooks at all, without a warning** (FM-INS-03) |
| U-09 | How often do agents change files through the shell, push, install, deploy or call APIs? | The plan's pivot check (§8) | Usage study (`scripts/usage_study.py`) | **answered (one-person sample)**: `results/usage-study/2026-10-18/` |
| U-10 | Do snapshots that borrow objects via git alternates survive `git gc --prune=now` in the user repo? | FM-OBJ-01, D3 | Spike D3 (session 2) | open |
| U-11 | How large are ignored files in real repositories? | Snapshot budget and default ignore policy (D4) | Spike D4 (session 2) | open |
| U-12 | How do OneDrive/Dropbox sync and antivirus lock files during snapshot and restore? | FM-RST-07, Windows reliability | Hostile-environment tests on a synced folder | open |
| U-13 | Do hook payloads carry secrets (environment, command arguments)? | Scrubbing before storage (G6) | Inspect captured payloads after scrubbing; secret corpus | partly answered: no environment in payloads, but commands are verbatim, and Edit/Read/Write payloads carry **whole file contents** (`originalFile`, `content`), so file secrets reach the hook. Scrub before storing; payload size grows with file size |
| U-14 | Are hook events in non-interactive `claude -p` runs the same as in interactive sessions? | Whether CI end-to-end tests reflect real use | Capture both and diff the event lists | open |
| U-15 | What do Codex hook payloads look like? | v0.2 Codex adapter | Payload capture for Codex | open |
| U-16 | How does a case-only rename behave on case-insensitive file systems (NTFS, APFS)? | HZ-G1-10 | Scenario on Windows and macOS | open |
| U-17 | Does every resume start a new `session_id`? | Run identity: a run can't be keyed on `session_id` alone | Interactive capture with `/resume`; compare with `-p --resume` | open: with `claude -p --resume` each turn got a **new** `session_id` (7 ids, one conversation; `SessionStart` source `resume`) |
