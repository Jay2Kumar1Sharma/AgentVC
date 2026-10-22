# Changelog

All notable changes to AgentVC are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Python project skeleton: `pyproject.toml` (Python 3.11+, no runtime dependencies), the `agentvc` package, and `python -m agentvc` printing the version.
- `Makefile` and `make.ps1` (Windows) with the `fast` lane (ruff, mypy, fast pytest), `test` (the full local suite: fast plus slow tests and the architecture check) and `arch` (architecture structure and render checks); placeholder `fuzz` and `chaos` targets fail loudly until implemented.
- CI: `.github/workflows/fast.yml` runs the fast lane on Linux, macOS and Windows with Python 3.11 and 3.13.
- First drafts of `docs/testing/fmea.md`, `hazards.md`, `unknowns.md` and `tests/traceability.yml`, with a test that fails if a traceability row names a test that doesn't exist and reports rows still marked `TODO` as gaps.
- `scripts/usage_study.py`: scans Claude Code transcripts and reports aggregate counts only (shell file changes, destructive commands, pushes, installs, deploys, database commands, HTTP and MCP calls), with privacy tests that plant canary strings and check none reach the output.
- `scripts/capture_payloads.py`: sets up a throwaway project whose project-local hooks record scrubbed Claude Code hook payloads into `tests/fixtures/hosts/claude-code/<version>/`; the hook prints nothing and always exits 0. `setup --events auto` (the default) writes only the events known for that Claude Code version (2.1.72 and 2.1.302 are known), because 2.1.72 registers no hooks at all if the settings name an event it doesn't know. The throwaway project also turns on the PowerShell tool.
- Captured hook payload fixtures for Claude Code 2.1.72 (63 files) and 2.1.302 (90 files, including the PowerShell tool and `PostToolBatch`).
- ADR template and ADRs for the project name (ADR-0001) and licence (ADR-0002).
- Design-decision spikes in `scripts/spikes/` with raw results in `results/spikes/2026-10-18/`: hook latency (S1/D1), hook failure behaviour in Claude Code (D2), snapshot object ownership (D3), ignored-file sizes (D4, aggregate only), interpreter pinning (D6) and workspace roots (D7). A CI workflow (`spike-latency.yml`) runs the latency spike on Linux, macOS and Windows on demand.
- Proposed ADRs 0003–0009 for decisions D1–D7: background process with `http` hooks, fail open visibly (closed in strict mode), own every snapshot object, ignored-files policy, ledger schema versioning, isolated pinned interpreter, workspace root.
- Architecture skeleton: `architecture/graph.json` from plan diagrams 4.1–4.3 (every node planned), an offline Overview page, `architecture/README.md` with the update rule, `scripts/arch_check.py` structure checks, and a Playwright render check (Playwright joins the dev extras).
- Prior-art notes in `docs/prior-art/` for Turso AgentFS, turnback, Claude Code `/rewind`, Cline, OpenCode, toffoli/undolog/revoco and container-use, and a pivot-check report in `docs/discovery/`.
- Hook latency spike measures the `http` hook round trip to a local server (U-18); Windows results in `results/spikes/2026-10-21/`.
- Testability foundations: one I/O layer with fault injection (`agentvc/io.py`): atomic writes, cross-process locks, SQLite access, bounded subprocesses that kill the whole process tree (job objects on Windows); injectable clock and randomness; named crash points; the secret scrubber (`agentvc/scrub.py`, now shared with the capture script, with more token formats); `AGENTVC_DEBUG` log.
- `agentvc/wiring.py`: the single list of handled events and known tools; generates Claude Code hook settings per version (only events that version knows; `http` hooks to `127.0.0.1` from 2.1.302; command hooks by absolute interpreter with `-I`; `onFailure: block` in strict mode where supported).
- Event model and Claude Code adapter, tested only against the captured payloads of both versions; unreadable payloads go to `.agentvc/blindspots.jsonl`.
- Ledger (`.agentvc/ledger.db`): runs, turns and steps over a hash-chained journal, anchored outside the workspace in `~/.agentvc/anchors/`; schema version and migrations tables; tampering, truncation and corruption reported, never repaired.
- Recorder: Pre/Post pairing on `tool_use_id`; a Pre without a Post and a Post without a Pre become visible GAPs; `PostToolBatch` reconciliation; subagent attribution; denied actions recorded as DENIED.
- `agentvcd`, the background process receiving `http` hooks (127.0.0.1 only, exclusive bind, token and Host checked), started and checked once per prompt by a command hook that records a GAP when it was down and reports gaps, unreadable payloads and rejected requests to the user.
- Hook failure policy (ADR-0004): fail open with a message and a GAP; strict mode blocks a PreToolUse that can't be recorded.
- Self-protection: PreToolUse denies agent writes into `.agentvc/`, which ignores itself with a `.gitignore` of `*`.
- CLI: `agentvc history`, `inspect <run>`, `check` (a realistic tool call through the real hook path), each with `--json`; `hooks-config`; internal `hook` and `daemon` commands.
- Design notes for session 3 in `docs/design/`; attack reviews in `docs/adversarial/` (capture, ledger).
- Snapshots (session 4): every file-changing step gets a before and an after snapshot in AgentVC's own git object store (`.agentvc/objects`, written by AgentVC in git's format, no alternates, every tree pinned, hashes checked on read). Edit tools capture only their target; shell, MCP and unknown tools capture the whole workspace. Links are never followed; nested repositories, heavy ignored folders, unreadable, oversized and torn files are listed as unprotected (G3). Ignored files follow ADR-0006; budgets follow ADR-0010 (proposed). A snapshot index (`.agentvc/snapindex.db`) makes unchanged files free; `agentvcd` builds the first snapshot in the background.
- Outside-change detection: a step's before snapshot is compared with the run's last after snapshot; the differences are recorded and never rolled back.
- Restore: `agentvc rollback <run | run:step | run:tTURN>`, `rollback --undo`, `undo`, `redo` and `repair`. Per-file compare-and-swap (plan diagram 4.4) over each file's chain of agent changes; the current state is saved before anything is written, including with `--force`; an intent record and named crash points make an interrupted rollback finishable (`repair`, also run first by every rollback). Paths through links, unsafe names and case collisions are refused.
- `agentvc diff <run | run:step | run:tTURN>`: unified diffs from the snapshots, plus outside changes and steps that weren't captured.
- `config.toml` gains `version` and a `[snapshot]` table (time budget, file size limits, heavy folders, include and exclude patterns); each bad setting is reported on its own.
- Ledger schema 2: `snapshots` and `rollbacks` tables, the first real migration, tested from a schema-1 ledger written by session 3's code.
- `scripts/broken_build.py` and `eval/broken_builds/`: runs tests against deliberately broken builds of the package (53 of 53 caught for snapshots, hook snapshots, restore and the CLI). Attack reviews `docs/adversarial/snapshot.md` and `restore.md`.

### Fixed
- Found in the first real Claude Code run (U-20): hook timings were quantised to Windows' 15.6 ms clock tick; `agentvcd` wrote a traceback whenever a client dropped a kept-alive connection; `agentvc inspect` could crash on piped output containing characters outside the console code page (text output now uses ASCII separators, `--json` is pure ASCII).

### Changed
- ADRs 0003–0009 accepted by the maintainer (2026-10-21). ADR-0003 amended (accepted 2026-10-22, verified with Claude Code 2.1.302): the token travels as a literal header value, because Claude Code blanks environment variables it considers credentials in `http` hook headers.
- `python -m agentvc` now runs the CLI (with no command it still prints the version).
- `agentvc inspect` tags each file-changing step that isn't protected (`[not snapshotted: ...]`, `[outside changes: N]`), counts the protected ones, and lists the run's rollbacks; runs recorded before snapshots existed now say their steps aren't protected.
