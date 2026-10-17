# Changelog

All notable changes to AgentVC are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Python project skeleton: `pyproject.toml` (Python 3.11+, no runtime dependencies), the `agentvc` package, and `python -m agentvc` printing the version.
- `Makefile` and `make.ps1` (Windows) with the `fast` lane (ruff, mypy, fast pytest) and placeholder `test`, `arch`, `fuzz`, `chaos` targets that fail loudly until implemented.
- ADR template and ADRs for the project name (ADR-0001) and licence (ADR-0002).
