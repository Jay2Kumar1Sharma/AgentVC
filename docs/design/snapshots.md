# Design note: snapshotter and object store

**Session 4 (v0.1-alpha), plan §4.2, §4.3, §7, §14.1, §14.6; ADR-0003, ADR-0005, ADR-0006, ADR-0009, ADR-0010 (proposed).** Modules: `agentvc/objects.py`, `agentvc/snapshot.py`; changes to `agentvc/hook.py`, `agentvc/recorder.py`, `agentvc/daemon.py`, `agentvc/config.py`, `agentvc/io.py`.

## What and why

Every step gets a **before** snapshot (at PreToolUse) and an **after** snapshot (at PostToolUse), so a rollback knows exactly what the agent changed and what it left (G1). A snapshot is a git tree in AgentVC's own object store.

**Object store** (`objects.py`, ADR-0005). `.agentvc/objects` is a bare git directory that AgentVC writes itself: blobs and trees as git **loose objects** (`zlib(type size\0data)`, named by SHA-1), so any `git` can read and `fsck` them, but no git process is started per snapshot (a process start costs tens of ms on Windows, ADR-0003). No `objects/info/alternates`, ever: one found is reported, and snapshots taken while it exists don't count as protected. Every snapshot tree is pinned by a ref (`refs/agentvc/keep/<tree>`). Reads check each object's hash, so a damaged or tampered object is an error, never restored silently. If an object was packed by someone else, reads fall back to `git cat-file` (through `IO.run`, isolated as below).

**Two kinds of snapshot**
- **Fast path** (edit tools: Write, Edit, MultiEdit, NotebookEdit): only the target file, resolved through links; the snapshot's **scope** is that one path. A target outside the workspace is listed as unprotected, never captured.
- **Full** (shell, MCP and unknown tools; an edit whose target can't be read from the payload): the whole workspace. Read-only, network, agent and todo tools get none.

**Full snapshot rules** (one walk with `IO.scandir`, `lstat` only):
- `.agentvc/` and `.git` at the root are never captured (the user's repository metadata is v0.2's git adapter).
- **Links are never followed.** A symlink is stored as a link (mode `120000`). A Windows junction or other reparse-point directory, or a directory on another device, is listed as unprotected and not entered.
- A directory holding a `.git` entry is a nested repository, submodule or linked worktree: listed as unprotected and not entered (ADR-0009; separate snapshots of nested roots come later).
- FIFOs, sockets and devices: unprotected. A directory or file that can't be read: unprotected, with the reason.
- **Ignored files** (ADR-0006): classified by `git check-ignore` with AgentVC's git directory and the workspace as work tree (so nested `.gitignore` files and negations follow git's own rules). Ignored folders named in `heavy_folders` aren't entered (listed once). Other ignored files are kept up to 1 MB each and 50 MB per snapshot, except `.env` / `.env.*` and `always_include` patterns. A heavy-named folder that is **not** ignored (a tracked `build/`) is captured like any other.
- **Budgets** (ADR-0010, proposed): any file over `max_file_mb` (default 100) is unprotected. A full snapshot taken inside a hook stops at `time_budget_ms` (default 2000) and is recorded as **incomplete**; the work done so far is kept, and it continues in the background.
- **Torn files** (FM-SNAP-01): each file is `lstat`ed, opened, `fstat`ed, read while hashing, then checked again; any change in size or modification time, or a byte count different from the size, means it changed while being read. It is retried once, then listed as unprotected ("changed while being read"), never saved as if whole.

**Snapshot index** (the "private index", plan §7). `.agentvc/snapindex.db` (SQLite) maps each path to its last `(size, mtime_ns, ctime_ns on POSIX, mode, blob, ignored)`. A file whose stat matches is not read again. Entries written within 2 s of the snapshot's start (by the file system's own clock, from a stamp file) are **racy** and always re-read, as in git. `agentvcd` keeps it in memory (ADR-0003); writes go back incrementally. It is a cache: deleting it costs one re-read, never data. Restore never trusts it: compare-and-swap reads real bytes.

**Baseline** (ADR-0003, FM-SNAP-07). The first full snapshot of a workspace can take minutes. `agentvcd` builds it in a background thread from `SessionStart`. While it runs, full snapshots in hooks return at once as **incomplete, "baseline pending"**; edit-tool snapshots still work. Without the daemon (per-call fallback), each hook does at most `time_budget_ms` of the work. Small workspaces finish within the first hook, so they're protected from the first step.

**In the ledger** (schema 2, ledger note): one `snapshots` row per snapshot: `step_id`, `phase` (`before`, `after`, `pre-rollback`), `status` (`ok`, `incomplete`, `failed`), `tree`, `scope` (null = whole workspace), `unprotected` (paths and reasons, capped at 200, plus the count), `outside`, timings. A step whose snapshot is missing, incomplete or failed is shown as **not protected**, with the reason, in `inspect`, and summed up at the next prompt (G3).

**Outside changes** (plan §14.1, FM-OUT-01). When step *k*'s before snapshot is recorded, it is compared with the latest after snapshot of the same run, over the paths both captured. Differences were made by someone else (the user, another session, a rollback): they are stored in `outside` and shown by `inspect` and `diff`, and restore never rolls them back (restore note).

**Failure policy** (ADR-0004). The snapshotter never raises into the hook: an error (disk full, read-only file system) makes the snapshot `failed`, the step not protected, and the hook adds a message. In strict mode a PreToolUse whose before snapshot failed is denied. Running out of budget is not a failure: it is reported, never blocked.

**git, isolated** (ATK-SNAP-11). `check-ignore` and `cat-file` run through `IO.run` with a timeout, every inherited `GIT_*` variable removed, no system or global config, and `core.fsmonitor=false`. AgentVC never runs git against the user's `.git`, so a tampered `.git/config` (fsmonitor command, filters) can't make AgentVC run anything; content is hashed by AgentVC itself, with no clean/smudge filters.

## What it borrows

git's object format and its racy-timestamp rule (git is GPL-2.0; nothing copied, the formats are documented in `gitformat-*`). Snapshot-per-tool-call and edited-paths-only ideas come from the prior art in `docs/prior-art/` (Claude Code `/rewind`, Cline and OpenCode checkpoints, turnback); none of their code is used.

## Guarantees touched

- **G1:** never follows links out of the root; never saves a torn file; owns every object; hashes verified on read.
- **G3:** everything not captured is listed with a reason; incomplete and failed snapshots are visible per step.
- **G6:** ignored files and `.env` live only in `.agentvc/objects` (ignored by its own `.gitignore`); nothing leaves the machine.
- **G7:** warm index in `agentvcd`; no process start per snapshot; budgets; baseline in the background.

## Limits (documented, not hidden)

- Edits made **during** one tool call by someone else are attributed to that call; restore's compare-and-swap still never overwrites what changed after the agent left it.
- On Windows the index can't use `ctime`, so a tool that rewrites a file keeping its size and modification time can make an after snapshot stale; a rollback then reports a conflict instead of restoring (safe, but not undoable).
- Empty directories, the user's `.git`, nested repositories and Git LFS content (only the pointer file is captured) are not protected yet.
- Loose objects aren't `fsync`ed; a power loss can leave a damaged object, which a read detects (hash check) and reports.

## How it can fail (FMEA rows; existing ones now have tests)

| Id | Failure | Detection / test |
|---|---|---|
| FM-SNAP-01..07 | as in `docs/testing/fmea.md` | tests named in `tests/traceability.yml` |
| FM-OBJ-01..03 | as in `docs/testing/fmea.md` | idem |
| FM-SNAP-08 | A Windows junction or other reparse point is entered like a directory | junction test: not entered, listed |
| FM-SNAP-09 | A stale index entry (racy timestamp) makes a snapshot hold old content | racy-entry test; restore re-reads content |
| FM-SNAP-10 | git run by AgentVC executes repository-controlled commands (fsmonitor, filters) | tampered-`.git` test with a marker command |
| FM-OUT-02 | An outside change is rolled back as if the agent made it | restore test with an outside edit between two agent steps |
