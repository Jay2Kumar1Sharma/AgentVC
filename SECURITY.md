# Security policy

AgentVC stores recordings of what coding agents do, snapshots of your workspace, and (when enabled) can release actions to external services. Security problems here can expose secrets or damage work, so please report them privately.

## Reporting a vulnerability

**Don't open a public issue.** Use GitHub's private reporting:
**Security → Report a vulnerability** on this repository ([direct link](https://github.com/Jay2Kumar1Sharma/AgentVC/security/advisories/new)).

Please include: what an attacker could do, the steps to reproduce, affected versions, and your OS and agent.

What happens next:
- We aim to acknowledge your report within **3 days** (AgentVC is maintained by a volunteer).
- We agree a disclosure date with you (default **90 days**, sooner once a fix is released).
- We credit you in the advisory unless you ask us not to.

## In scope

- Secrets written to recordings, logs, exports or diagnostics bundles despite scrubbing
- Rollback or restore writing outside the project, following links outside it, or destroying data
- The agent (or content in a repository) disabling, bypassing or tampering with AgentVC without the user being told
- Tampering with the ledger going undetected
- Outbox releasing effects without approval, or more than once
- AgentVC sending any data over the network on its own

## Out of scope

- Things an agent does by bypassing hooks entirely (documented on the limitations page), unless AgentVC claims to cover them
- Attacks requiring an already-compromised machine or user account
- Vulnerabilities in agents, git or the operating system themselves (please report those upstream; tell us if AgentVC makes them worse)

## Supported versions

Security fixes go to the latest minor release. Before 1.0, only the latest 0.x release is supported.
