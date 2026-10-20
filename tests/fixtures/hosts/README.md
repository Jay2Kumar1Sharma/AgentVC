# Host payload fixtures

Real hook payloads captured from coding agents, one folder per agent and version:
`tests/fixtures/hosts/<agent>/<version>/NNNN-<event>[-<tool>].json`.

Each file is `{"capture": {...}, "payload": {...}}`. `capture` holds the arrival order (`seq`), time and process id; `payload` is what the agent sent on stdin, scrubbed by `scripts/capture_payloads.py` (tokens, assigned secrets, private keys, emails, home folder → `~`, user name → `<user>`).

Adapters are tested only against these files (plan §6.2, §16.2), never against payloads written by hand. Re-capture when an agent updates, and keep old versions so the adapter keeps working for them.

**Before committing new captures:** read every file. Scrubbing is a safety net, not a guarantee.

How to capture: `python scripts/capture_payloads.py --help`, and the steps in `docs/progress.md` (session 1 handoff).
