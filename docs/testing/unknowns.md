# Unknowns register

_First draft, Discovery (2026-10-18). Ongoing (plan §16.1)._

Things not yet understood that could change the design or hide a failure. Each has an experiment that would answer it. When one is answered, record the answer and where the evidence is (a `results/` folder, an ADR, a fixture), and keep the row.

| ID | Unknown | Why it matters | Experiment | Status |
|---|---|---|---|---|
| U-01 | Cold-start latency of a Python hook on Windows, with Defender scanning | Decides per-call process vs background process (D1); G7 | Spike S1 (session 2) | open |
| U-02 | Does Claude Code run hooks for parallel tool calls concurrently, and in what order do Pre/Post events arrive? | Ledger locking and step attribution (FM-LED-01, FM-ADP-02) | Payload capture with a parallel tool batch; record arrival times | open |
| U-03 | Is `tool_use_id` unique across subagents and sessions? | Pairing Pre with Post events | Payload capture with a subagent | open |
| U-04 | When a hook exceeds its timeout, does the tool still run, and is PostToolUse still sent? | Fail-open/closed behaviour (D2), GAP detection | Throwaway project with a deliberately slow hook | open |
| U-05 | What does a PowerShell tool payload look like compared with Bash? | Windows shell capture | Payload capture on Windows | open |
| U-06 | Does PostToolUse fire when a tool fails, or is there a separate failure event? | GAP vs failed step | Payload capture with a failing command | open |
| U-07 | Do `/rewind` restores trigger any hook event? | Outside-change detection would see them as user edits | Payload capture, then `/rewind` | open |
| U-08 | Which hook events exist in the installed Claude Code version (e.g. `PostToolBatch`, `ConfigChange`, `CwdChanged`)? | Wiring (plan §6.2), G8 config-change detection | Compare the hooks reference with captured events | open |
| U-09 | How often do agents change files through the shell, push, install, deploy or call APIs? | The plan's pivot check (§8) | Usage study (`scripts/usage_study.py`) | open |
| U-10 | Do snapshots that borrow objects via git alternates survive `git gc --prune=now` in the user repo? | FM-OBJ-01, D3 | Spike D3 (session 2) | open |
| U-11 | How large are ignored files in real repositories? | Snapshot budget and default ignore policy (D4) | Spike D4 (session 2) | open |
| U-12 | How do OneDrive/Dropbox sync and antivirus lock files during snapshot and restore? | FM-RST-07, Windows reliability | Hostile-environment tests on a synced folder | open |
| U-13 | Do hook payloads carry secrets (environment, command arguments)? | Scrubbing before storage (G6) | Inspect captured payloads after scrubbing; secret corpus | open |
| U-14 | Are hook events in non-interactive `claude -p` runs the same as in interactive sessions? | Whether CI end-to-end tests reflect real use | Capture both and diff the event lists | open |
| U-15 | What do Codex hook payloads look like? | v0.2 Codex adapter | Payload capture for Codex | open |
| U-16 | How does a case-only rename behave on case-insensitive file systems (NTFS, APFS)? | HZ-G1-10 | Scenario on Windows and macOS | open |
