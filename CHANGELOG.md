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
