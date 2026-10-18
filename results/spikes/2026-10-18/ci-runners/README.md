# Hook latency on GitHub-hosted runners, 2026-10-18

Raw output of `scripts/spikes/hook_latency.py --sizes 1000 10000 100000 --runs 30`, run by `.github/workflows/spike-latency.yml` in [Actions run 39114705668](https://github.com/Jay2Kumar1Sharma/AgentVC/actions/runs/39114705668) on commit `915d2a9` (branch `discovery/session-2`), downloaded from the run's artifacts.

| File | Runner | Python | git | CPUs |
|---|---|---|---|---|
| `hook_latency-linux.json` | ubuntu-latest | 3.12.15 | 2.55.0 | 2 |
| `hook_latency-darwin.json` | macos-latest | 3.12.10 | 2.56.0 | 5 |
| `hook_latency-windows-ci.json` | windows-latest | 3.12.10 | 2.56.0.windows.2 | 2 |

Shared CI runners are noisier and smaller than a developer machine; read these as orders of magnitude next to the local Windows run in `../hook_latency-windows.json`. Interpretation: ADR-0003.
