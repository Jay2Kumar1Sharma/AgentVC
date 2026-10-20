# eval/

Evaluation suites that run AgentVC end to end (plan §6.2, §15, §16).

| Path | What | Arrives |
|---|---|---|
| `eval/scenarios/` | One folder per scenario: setup, agent actions, human actions, expected result. Run with `python -m eval.run --all` | v0.1-alpha |
| `eval/adversarial/<component>/<attack>/` | The attack catalog (plan §15.3) as data. Run with `python -m eval.adversarial --all` | v0.1-alpha |
| `eval/replay_transcripts.py` | Feeds past agent sessions through the recorder offline; aggregate output only | v0.1 |

Numbers published in the README come from these suites, with the raw output kept in `results/`.
