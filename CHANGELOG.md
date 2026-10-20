# Changelog

All notable changes to AgentVC are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Python project skeleton: `pyproject.toml` (Python 3.11+, no runtime dependencies), the `agentvc` package, and `python -m agentvc` printing the version.
- `Makefile` and `make.ps1` (Windows) with the `fast` lane (ruff, mypy, fast pytest) and placeholder `test`, `arch`, `fuzz`, `chaos` targets that fail loudly until implemented.
- CI: `.github/workflows/fast.yml` runs the fast lane on Linux, macOS and Windows with Python 3.11 and 3.13.
- First drafts of `docs/testing/fmea.md`, `hazards.md`, `unknowns.md` and `tests/traceability.yml`, with a test that fails if a traceability row names a test that doesn't exist and reports rows still marked `TODO` as gaps.
- `scripts/usage_study.py`: scans Claude Code transcripts and reports aggregate counts only (shell file changes, destructive commands, pushes, installs, deploys, database commands, HTTP and MCP calls), with privacy tests that plant canary strings and check none reach the output.
- `scripts/capture_payloads.py`: sets up a throwaway project whose project-local hooks record scrubbed Claude Code hook payloads into `tests/fixtures/hosts/claude-code/<version>/`; the hook prints nothing and always exits 0. `setup --events auto` (the default) writes only the events known for that Claude Code version (2.1.72 and 2.1.302 are known), because 2.1.72 registers no hooks at all if the settings name an event it doesn't know. The throwaway project also turns on the PowerShell tool.
- Captured hook payload fixtures for Claude Code 2.1.72 (63 files) and 2.1.302 (90 files, including the PowerShell tool and `PostToolBatch`).
- ADR template and ADRs for the project name (ADR-0001) and licence (ADR-0002).
