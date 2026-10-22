# Failure mode analysis (FMEA)

_First draft, Discovery (2026-10-18). Seeded from the example rows in plan §16.1 and the failure modes implied by §7.1, §14.1 and §15.3. Updated with every new component (plan §13.1, §13.3 step 10)._

**How to use this file**
- One row per way a component can fail. Every row has an ID, and every ID has a row in [`tests/traceability.yml`](../../tests/traceability.yml) listing the tests that cover it (`TODO` until they exist). A test fails if an ID here is missing from the traceability file.
- **Severity** uses plan §13.2 (S0 critical … S3 low), judged as if the failure reached a user unnoticed.
- **Detection / test** says how the failure is detected at runtime and how a test proves it's handled. Components that don't exist yet describe the planned test.

| ID | Component | Failure mode | Effect on the user | Sev. | Guarantee | Detection / test |
|---|---|---|---|---|---|---|
| FM-SNAP-01 | Snapshotter | File modified while being read | Snapshot holds a torn file; rollback restores garbage | S0 | G1 | Stat before/after read; mark *unprotected*; test with a writer racing the snapshot |
| FM-SNAP-02 | Snapshotter | Ignored `.env` deleted by the agent | Secrets/config can't be recovered | S1 | G1, G3 | Snapshot small ignored files (D4); scenario test |
| FM-SNAP-03 | Snapshotter | Follows a symlink to outside the project | Captures (and later restores) files outside the workspace | S0 | G1, G6 | Never follow links out of the root; symlink-escape attack test |
| FM-SNAP-04 | Snapshotter | Size, count or time budget exceeded | Files silently not captured | S1 | G3, G7 | Explicit UNPROTECTED state, shown in banner and `inspect`; budget test |
| FM-SNAP-05 | Snapshotter | Unreadable file (permission denied, locked by an editor) | Missing from the snapshot | S1 | G3 | Mark unprotected and report; fault-injection test |
| FM-SNAP-06 | Snapshotter | Nested repository or worktree inside the workspace is recorded as a gitlink | Its files aren't in the snapshot; an agent's `rm -rf` there can't be undone | S1 | G1, G3 | Snapshot nested roots separately, or mark them unprotected (ADR-0009); spike `workspace_roots.py` shows the gitlink |
| FM-SNAP-07 | Snapshotter | First snapshot of a large repository runs inside a hook | Hook takes seconds to minutes (21 s at 10k files, 243 s at 100k on Windows) | S2 | G7 | Baseline built in the background, steps marked "baseline pending" (ADR-0003); hook latency spike |
| FM-OBJ-01 | Object store | User's `git gc` prunes objects AgentVC borrowed via alternates | Old snapshots unreadable | S0 | G1 | Own every referenced object (D3); test: snapshot, prune the user's repo, then restore |
| FM-OBJ-02 | Object store | Disk full during object write | Truncated or missing object; snapshot recorded as complete | S1 | G1, G3 | Verify objects before marking the step recorded; disk-full fault injection |
| FM-OBJ-03 | Object store | An alternates file is malformed (e.g. CRLF) | Git ignores it with only a warning; objects silently copied, or reads silently fail later | S2 | G1 | Never rely on alternates; verify before use (ADR-0005); found by the D3 spike |
| FM-RST-01 | Restore | Crash after writing half the files | Workspace in a mixed state | S0 | G1 | Intent log + resumable restore; crash-point test at every write |
| FM-RST-02 | Restore | Editor saves by rename (atomic save) | Inode changes; compare-and-swap checks the wrong thing | S0 | G1 | Compare content, not inode or mtime; test with rename-style saves |
| FM-RST-03 | Restore | User edited the file after the run | User's edit overwritten | S0 | G1 | Per-file compare-and-swap (diagram 4.4); conflict listed and skipped; scenario + property test |
| FM-RST-04 | Restore | Writes through a symlinked directory | A file outside the workspace is overwritten | S0 | G1 | Resolve and check every target path before writing; symlink attack test |
| FM-RST-05 | Restore | `--force` without a pre-rollback snapshot | Forced rollback can't be undone | S0 | G1 | Pre-rollback snapshot always taken first; test `--force` then `rollback --undo` |
| FM-RST-06 | Restore | File ↔ directory ↔ symlink type change between snapshots | Wrong type restored, or data inside a directory lost | S0 | G1 | Type-change scenario tests; model-based test |
| FM-RST-07 | Restore | File locked by an editor or antivirus (Windows) | Partial rollback reported as complete | S1 | G1, G3 | Per-file result reporting; locked-file test on Windows |
| FM-OUT-01 | Outside-change detection | User edits during the run are attributed to the agent | Rollback reverts the user's edit | S0 | G1 | Compare each step's before-snapshot with the previous step's after-snapshot; scenario test |
| FM-LED-01 | Ledger | Two hooks write at once | Lost or duplicated step | S1 | G2 | Locking + concurrency stress test |
| FM-LED-02 | Ledger | Agent edits or deletes rows | False history | S1 | G2, G8 | Hash chain + anchor outside the workspace; tamper attack test |
| FM-LED-03 | Ledger | Database corrupted (crash, disk) | History unreadable, or "repaired" into a false history | S1 | G2, G3 | Integrity check on open; report, never silently repair; corruption test |
| FM-LED-04 | Ledger | Opened by an older AgentVC with a newer schema | Misread or damaged ledger | S1 | G1, G2 | `schema_version` table; refuse newer schemas (D5); upgrade/downgrade test |
| FM-LED-05 | Ledger | Crash between commit and anchor write | False tamper alarm, or entries forged later look legitimate after the next write | S2 | G3, G8 | The anchor records the pending head before each commit; crash-point test; forged-append test |
| FM-HOOK-01 | Hook | AgentVC's hook crashes or exceeds the timeout | Agent blocked, or step unrecorded | S1 | G2, G7 | Fail open with a visible GAP; fail closed in strict mode (D2); fault-injection test |
| FM-HOOK-02 | Hook | Pre event with no matching Post event | Step silently missing | S1 | G2 | Pending step becomes a visible GAP; adapter test |
| FM-HOOK-03 | Hook | Hook too slow (cold Python start on Windows) | User turns AgentVC off | S2 | G7 | Spike S1 / D1; latency benchmark in CI |
| FM-HOOK-04 | Hook | The background process (agentvcd) is down while hooks are `http` hooks | Every hook fails as a non-blocking error: actions silently unrecorded | S1 | G2, G8 | Liveness check on SessionStart/UserPromptSubmit that restarts it or tells the user (ADR-0003) |
| FM-HOOK-05 | Hook | agentvcd's port is taken by another program | No daemon, so every http hook fails | S1 | G2 | Liveness hook detects it, records the event in-process and tells the user; test |
| FM-HOOK-06 | Hook | Hook requests with a wrong or blanked token, or a foreign Host header | Hooks refused: the actions go unrecorded | S1 | G2 | Rejections counted and reported at the next prompt; tests |
| FM-HOOK-07 | Hook | The hook URL says `localhost` | About 2 s per hook call on Windows (U-18) | S2 | G7 | The generator always writes 127.0.0.1; test |
| FM-HOOK-08 | Hook | Another local socket binds agentvcd's port (SO_REUSEADDR on Windows), or the port is ephemeral and a client connects to itself | Hook traffic and the token intercepted; health checks answered by the client itself | S1 | G2, G6 | SO_EXCLUSIVEADDRUSE on Windows; ports 20000-32767; tests |
| FM-ADP-01 | Adapter | Agent update renames a payload field | Steps silently stop recording | S1 | G2 | Blind-spot log + weekly payload-drift canary (§16.2) |
| FM-ADP-02 | Adapter | Subagent or parallel-batch tool calls not attributed | Steps missing or attributed to the wrong agent | S1 | G2 | `SubagentStart`/`Stop` + `PostToolBatch` handling; fixture tests |
| FM-ADP-03 | Adapter | Tool the wiring doesn't subscribe to (new tool name) | Its actions never recorded | S1 | G2 | Catch-all matcher; unknown tools logged to the blind-spot log; wiring test |
| FM-ADP-04 | Adapter | A one-off session id (InstructionsLoaded on resume) starts a phantom run | History split across false runs | S2 | G3 | InstructionsLoaded isn't handled; whole captured sessions replay as one run |
| FM-ADP-05 | Adapter | File contents, prompts or assistant text stored in the ledger | Private content kept in recordings | S1 | G6 | Contents reduced to their size; prompts only if enabled; canary test on every fixture |
| FM-SELF-01 | Self-protection | The agent writes into `.agentvc/` through an edit tool or a plain shell command | Recording forged or deleted | S1 | G8 | PreToolUse deny with `..`, case and junction variants; the hash chain catches what slips past |
| FM-CLS-01 | Classifier | New CLI with a destructive subcommand | Effect classed safe | S1 | G3 | Default `UNKNOWN`; fuzzing; incident gold set |
| FM-CLS-02 | Classifier | Obfuscated command (`sh -c`, `eval`, base64) | Risky command classed safe | S1 | G3 | Obfuscation fuzzing; must classify `UNKNOWN` or worse |
| FM-FEN-01 | Fence | Same effect via a different tool | Duplicate payment | S0 | G4 | Cross-tool equivalence tests |
| FM-OBX-01 | Outbox | Release succeeds but the result is lost in a crash | Second commit releases again | S0 | G5 | Idempotency keys + crash-point test between release and record |
| FM-OBX-02 | Outbox | Staged effect released before `commit` | Message or call leaves early | S0 | G5 | Deterministic simulation; exactly-once checker |
| FM-SEC-01 | Scrubber | Token format not recognised | Secret written to ledger or export | S0 | G6 | Secret corpus test; exports redact `.env` by default |
| FM-CFG-01 | Config | Agent switches mode to `observe` | Protection silently off | S1 | G8 | `ConfigChange` hook + session-start check (G8) |
| FM-CFG-02 | Install | Agent removes AgentVC's hooks from `.claude/settings.json` | Recording silently stops | S1 | G8 | Session-start check + `ConfigChange`; self-protection attack test |
| FM-INS-01 | Install | Hook calls the wrong Python (project venv) | Hook errors on every call | S1 | G2, G8 | Absolute interpreter path (D6); install test inside a foreign venv |
| FM-INS-02 | Install | Install overwrites the user's other hooks | User's tooling broken | S2 | G1 | Merge, never replace; AgentVC-owned markers; install/uninstall tests |
| FM-INS-03 | Install | Hook config names an event the installed agent version doesn't know | The agent silently registers **no** hooks; nothing is recorded (seen on Claude Code 2.1.72) | S1 | G2, G8 | Per-version event lists; `agentvc check` confirms a hook fires; install test per supported agent version |
| FM-INS-04 | Install | Repo content shadows AgentVC's modules (a planted `agentvc/` in the project folder) | The project's code runs as AgentVC's hook: recording off or the ledger forged | S1 | G2, G8 | Absolute interpreter path **and** `-I` (ADR-0008); spike `interpreter_pinning.py` |
| FM-WIR-01 | Wiring | The agent's hook timeout and AgentVC's own deadline drift apart | The agent kills the hook mid-write | S2 | G2 | Both derived from one constant; test |
| FM-IO-01 | I/O layer | Crash between the temp write and the rename | Old file intact plus a stray temp file; never a torn file | S2 | G1, G2 | Atomic replace; crash-point tests in-process and in a killed process |
| FM-IO-02 | I/O layer | A lock holder is killed | Every later hook waits | S2 | G7 | The OS releases file locks on exit; locks time out; test kills a holder |
| FM-IO-03 | I/O layer | A child process outlives its timeout (a grandchild keeps the pipes open) | Hook hangs; orphan processes | S1 | G7 | Job object on Windows, process group elsewhere; grandchild tests |
| FM-IO-04 | I/O layer | Unbounded child output | Memory blow-up in a hook | S2 | G7 | Output caps; test |
| FM-CLI-01 | CLI | Output hides gaps or a tampered ledger | The user trusts a broken record | S1 | G2, G3 | Verdict and gap reasons always printed, exit 1 when not clean; golden tests |
| FM-CLI-02 | CLI | `agentvc check` passes without exercising the real hook path | "Installed but not recording" goes unnoticed | S1 | G2, G8 | check runs the real command hook and http hooks; test with recording broken |
| FM-GC-01 | GC | Deletes a snapshot still referenced by a fork | Rollback of the fork fails | S0 | G1 | Reference-counting test; model-based test with forks + gc |
| FM-DEV-01 | Dev tooling | Usage study or payload capture writes transcript content, prompts or secrets into the repository | Private data published with the repository | S0 | G6 | Aggregate-only output schema; scrubbing; tests that plant canary strings and check they never reach the output |
