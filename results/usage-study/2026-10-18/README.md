# Usage study, 2026-10-18

Aggregate counts of what a coding agent did in the maintainer's Claude Code sessions (plan §8 Discovery, unknown U-09). Raw result: [`usage-study.json`](usage-study.json).

## How it was produced

```text
python scripts/usage_study.py --out results/usage-study/2026-10-18
```

- Script: `scripts/usage_study.py`, script version 1, at commit `0899890`.
- Source: `~/.claude/projects/**/*.jsonl` (all projects on the machine, not only AgentVC).
- Environment: Windows 11, Python 3.12.7, Claude Code 2.1.72.
- Privacy: the script writes aggregate counts only; the agent that ran it saw only the printed summary, never transcript content.

## Results

35 sessions (31 with tool calls), 4,005 tool calls in total.

| Category | Total | Sessions with ≥ 1 (of 31) | Max in one session |
|---|---:|---:|---:|
| Shell commands (Bash + PowerShell) | 1,987 | 27 | 636 |
| Edit-tool calls (Edit, Write, MultiEdit, NotebookEdit) | 1,050 | 18 | 341 |
| **Shell commands that modify files** | **379** | **17** | 107 |
| **Destructive shell commands** (recursive/forced deletes, `reset --hard`, `clean -f`, force push…) | **63** | **10** | 21 |
| Git pushes | 3 | 3 | 1 |
| Package installs | 24 | 9 | 6 |
| Deploys / publishes | 0 | 0 | 0 |
| Database commands | 6 | 2 | 4 |
| HTTP calls from the shell | 26 | 6 | 9 |
| HTTP writes (POST/PUT/PATCH/DELETE, request bodies) | 7 | 3 | 3 |
| MCP tool calls | 60 | 1 | 60 |
| WebFetch calls | 61 | 8 | 17 |

- **26.5%** of file-changing actions went through the shell (379 of 379 + 1,050), where edit-tool checkpoints such as `/rewind` don't see them.
- 12 of 31 sessions had at least one external effect (push, install, deploy, DB command, HTTP write or MCP call), most of them package installs.

## What it means for the pivot check (plan §8, §12)

The pivot rule is: *if shell file changes and side effects are rare, start with v0.4 (outbox)*.

- **Shell file changes are not rare.** More than half of the tool-using sessions changed files through the shell, about a quarter of all file changes happened that way, and a third of sessions ran at least one destructive command. That is the gap v0.1 targets.
- **External side effects are rare in this sample.** Three pushes, no deploys, a handful of DB commands and HTTP writes, and MCP calls concentrated in a single session. On this evidence the outbox (v0.4) is not the most urgent need **for this user**.
- **Recommendation:** the evidence supports keeping the plan's order (v0.1 file and shell undo first). The final pivot call waits for the outside conversations (plan §8 "Outside validation"), because this sample comes from one person.

## Limits of this data

- **A sample of one person, 35 sessions.** It includes the current session (Discovery part 1), which runs many `git` and file commands itself, and earlier planning sessions for this project.
- **The classifier is a heuristic** (regular expressions; see `classify_shell` and `tests/test_usage_study.py`). It has not been checked against hand-labelled real commands, because nobody but the user may read the transcripts. "Modify files" includes benign actions (`mkdir`, `cp` into build folders, `git checkout`) and may over-count output redirection; commands run indirectly (scripts, `python -c`, `npm run`) are missed.
- Commands inside heredocs and PowerShell here-strings are ignored on purpose (they're usually commit messages or file contents).
- One transcript line could not be parsed (counted, then skipped). No subagent transcript files were found under the `subagents/` layout.
- **A spot check the user can do locally:** run `classify_shell` over a few of their own commands to see whether the categories match their judgement. Its output is printed to the terminal only and should not be committed.
