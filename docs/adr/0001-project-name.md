# ADR-0001: The project is named AgentVC

- **Status:** accepted
- **Date:** 2026-10-17
- **Decided by:** the maintainer
- **Related:** plan §8 Discovery, §12 "Name confusion"; `docs/progress.md` "Project decisions"

## Context

The project needs one name for the repository, the CLI, the Python package and the docs before any code or packaging exists. The working folder is called `AgentFS`, which collides with Turso AgentFS, a related but different tool (an agent file system, not version control of agent actions). Another project, `nintechio/agentvc`, also exists.

## Options considered

1. **AgentFS**: the folder's name; collides with Turso AgentFS and suggests a file system, which AgentVC is not.
2. **AgentVC**: "version control for agent execution", which is what the plan describes; one known name clash (`nintechio/agentvc`).

## Decision

The project is **AgentVC**. The CLI command and the Python package are **`agentvc`**. The repository is `github.com/Jay2Kumar1Sharma/AgentVC`. The local folder name (`AgentFS`) does not matter and is not used in code or docs.

## Consequences

- All code, docs, commands and packaging use `agentvc` / AgentVC.
- Docs avoid "FS" wording, and the README says clearly what AgentVC is and how it differs from Turso AgentFS (plan §12).
- Before publishing a package, check that `agentvc` is free on PyPI; if it isn't, the package name (not the project name) gets its own ADR.
