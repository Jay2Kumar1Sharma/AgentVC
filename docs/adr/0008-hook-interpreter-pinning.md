# ADR-0008: Hooks run AgentVC's own interpreter, by absolute path, isolated (D6)

- **Status:** proposed
- **Date:** 2026-10-18
- **Decided by:** pending the maintainer's review (drafted in Discovery part 2)
- **Related:** plan §7.1 D6, §14.5 threat model, G2, G8; FMEA FM-INS-01; spike `scripts/spikes/interpreter_pinning.py`, results `results/spikes/2026-10-18/interpreter_pinning.json`; ADR-0003

## Context

Hooks run inside the user's project, often with the project's own virtual environment active and with the project folder as the working directory. A hook command that says just `python` runs whatever interpreter is first on `PATH`.

**Spike (Windows, 2026-10-18).** A throwaway project with its own venv made from **Python 3.10** (AgentVC runs on 3.12), activated, with `PYTHONPATH` set to the project and a **planted `agentvc/` package** in the project folder, as hostile or just unlucky repo content might contain:

| Hook command | Interpreter used | What ran |
|---|---|---|
| `python -m agentvc` | the project's 3.10 venv | **the planted package** |
| `<AgentVC's python> -m agentvc` | AgentVC's 3.12 | **the planted package** (the working directory and `PYTHONPATH` come first on `sys.path`) |
| `<AgentVC's python> -I -m agentvc` | AgentVC's 3.12 | **the real AgentVC** |
| `<AgentVC's python> -I -S -m agentvc` | AgentVC's 3.12 | nothing: `-S` also skips site-packages, where AgentVC is installed |

So an absolute interpreter path alone is **not enough**: a repository can hijack AgentVC's hook just by containing an `agentvc/` folder. That would let a malicious repo switch recording off or forge the ledger (G2, G8).

## Options considered

1. **Bare `python` / `python3`:** breaks in any project with its own venv or an older Python, and runs repo content.
2. **Absolute interpreter path:** fixes the wrong-interpreter problem, but not the shadowing.
3. **Absolute path plus isolated mode (`-I`)**, optionally a launcher so `-S` can also be used.

## Decision (proposed)

- `agentvc install` writes hooks that call **AgentVC's own interpreter by absolute path** (the pipx or venv interpreter it was installed with), **always with `-I`**, in exec form where the agent supports it (ADR-0003).
- No hook ever depends on `PATH`, `PYTHONPATH`, `VIRTUAL_ENV`, the working directory or the user site directory.
- If the hook start-up time later needs `-S` (about 10 ms saved on Windows, ADR-0003), it runs a small **launcher script installed next to AgentVC** that adds AgentVC's own site-packages to `sys.path` explicitly; it never falls back to the project's.
- `agentvc ready` / `doctor` check that the recorded interpreter still exists and still imports the same AgentVC version. A broken path is reported loudly at session start (G8), never as silent hook errors.

## Consequences

- Reinstalling or upgrading AgentVC into a new environment must rewrite the hooks; `install` is idempotent and updates the path.
- An install test runs inside a project with its own venv **and** a planted `agentvc/` folder (plan §16.1 FMEA row FM-INS-01 gains this case).
- The threat model (v0.1) lists "repo content shadowing AgentVC's modules" as an attack, mitigated by `-I`.
