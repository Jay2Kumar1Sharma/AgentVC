# ADR-0002: Licence is Apache-2.0

- **Status:** accepted
- **Date:** 2026-10-17
- **Decided by:** the maintainer
- **Related:** `LICENSE`, `NOTICE`, plan §11 "Licence and ownership", `docs/progress.md` "Project decisions"

## Context

AgentVC is meant to be adopted by individual developers, teams that give agents real credentials, and agent builders who embed it (plan §1). It will also borrow ideas from other open-source tools and credit them (plan §11). The licence has to be settled before the first code is committed.

## Options considered

1. **MIT**: short and permissive; no explicit patent grant.
2. **Apache-2.0**: permissive, with an explicit patent grant and a `NOTICE` mechanism for attribution; common in developer tooling.
3. **A copyleft licence (GPL/AGPL)**: keeps derivatives open; discourages embedding by agent builders, one of the three target audiences.

## Decision

AgentVC is licensed under **Apache-2.0**. `LICENSE` is the official text from apache.org and is not edited. `NOTICE` lists the project's copyright and any third-party code included in the repository. `pyproject.toml` declares `license = "Apache-2.0"`.

## Consequences

- Anyone can use, modify and embed AgentVC, including commercially, as long as they keep the notices.
- Any third-party code copied into the repository must have a compatible licence and be listed in `NOTICE`. Ideas borrowed without copying code are credited in the docs (plan §11), not in `NOTICE`.
- How outside contributors certify their right to submit code (DCO, CLA, or nothing beyond Apache-2.0's section 5) is decided when contributions open (MAINTAINING.md "Contribution phases").
