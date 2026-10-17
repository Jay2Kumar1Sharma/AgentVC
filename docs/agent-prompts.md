# Prompts for building AgentVC with a coding agent

How to use this file:
- Open the agent (Claude Code, Codex, …) **in the repository root**. Claude Code then loads `CLAUDE.md` → `AGENTS.md` automatically; Codex reads `AGENTS.md`.
- Run **one session per row** of the sequence below. Paste that session's prompt. Fill in anything in `«angle brackets»` first.
- After each session: read its summary and `docs/progress.md`, review the diff, and answer its open questions before starting the next session.
- Use a **separate session** for reviews and red-team runs, ideally a different model or agent from the one that wrote the code.
- Every session must **commit gradually** (AGENTS.md, plan §13.1): small, single-purpose, working commits as it goes, not one big commit at the end. When reviewing a session, check `git log`. If you see one huge commit, ask the agent to follow the rule next time.

---

## Session sequence

| # | Session | Milestone (plan §8) | Prompt |
|---|---|---|---|
| 1 | Repo setup + Discovery part 1 | Discovery | [Session 1](#session-1-repo-setup-and-discovery-part-1) |
| 2 | Discovery part 2: spikes, prior art, architecture skeleton | Discovery | [Session 2](#session-2-discovery-part-2) |
| 3 | Alpha A: foundations, capture, ledger | v0.1-alpha | [Session 3](#session-3-alpha-a-foundations-capture-ledger) |
| 4 | Alpha B: snapshots and rollback | v0.1-alpha | [Session 4](#session-4-alpha-b-snapshots-and-rollback) |
| 5 | Alpha C: the safety test suite | v0.1-alpha | [Session 5](#session-5-alpha-c-safety-test-suite) |
| — | Review of sessions 3–5 | | [Review prompt](#review-session) |
| 6 | v0.1 public: completion | v0.1 | [Session 6](#session-6-v01-public-completion) |
| — | Red team for v0.1 | v0.1 | [Red-team prompt](#red-team-session) |
| 7 | Release v0.1 | v0.1 | [Release prompt](#release-session) |
| 8+ | v0.2, v0.3, v0.4, v0.5 | | [Next-milestone prompt](#next-milestone-session) |
| any | Fix a GitHub issue | | [Issue prompt](#fix-an-issue-session) |

If a session runs out of room or time, start a new session with: *"Continue the current session's task. Read `docs/progress.md` first; the last handoff says where to resume."*

---

## Session 1: repo setup and Discovery part 1

```text
You are building AgentVC from scratch, following docs/master-plan.md. Read AGENTS.md and docs/progress.md first, then the plan.

Decisions already made by me (recorded in docs/progress.md):
- Project name: AgentVC (CLI and Python package: agentvc)
- Licence: Apache-2.0 (LICENSE and NOTICE already exist; don't change them)
- GitHub owner/repo: Jay2Kumar1Sharma/AgentVC
- Outside contributions are closed for now
Record the name and licence as ADR-0001 and ADR-0002 in docs/adr/, using a short ADR template you create (docs/adr/0000-template.md). Never commit docs/private/.

Task for this session (plan §8 "Discovery" and §17 week 1). Do these in order:
1. If the folder isn't a git repository yet: `git init -b main`, check `git status` shows nothing from docs/private/ or .claude/settings.local.json, then commit the existing files in small topic commits (hygiene files; README+LICENSE+NOTICE; conduct+security; contributing+maintaining; .github and scripts; agent instructions; plan; research), signed by the repository's git config, with no email addresses in messages (no sign-off or co-author trailers). If the repository already exists, skip this. Do NOT create a remote or push.
2. Project skeleton: pyproject.toml (Python 3.11+, no runtime dependencies, dev extras: pytest, hypothesis, mypy, ruff), package folder `agentvc/` with __init__.py and a `__main__` that prints the version, `tests/`, `eval/`, `scripts/`, `results/`, `docs/adr/`, `docs/design/`, `docs/testing/`, `docs/prior-art/`, CHANGELOG.md with an "Unreleased" section. In pyproject.toml set `license = "Apache-2.0"`.
3. A Makefile (and a make.ps1 or documented equivalents for Windows) with `fast` (ruff + mypy + pytest fast tests) and placeholders for `test`, `arch`, `fuzz`, `chaos`. A first trivial test, seen to fail then pass.
4. CI: .github/workflows/fast.yml running the fast lane on Linux, macOS and Windows with Python 3.11 and 3.13. It can't run until I push the repository; validate the workflow file locally (YAML parses; same commands pass locally) and report CI as "not yet run".
5. First drafts of docs/testing/fmea.md, docs/testing/hazards.md, docs/testing/unknowns.md and tests/traceability.yml (plan §16.1), seeded from the plan's FMEA rows and guarantees. Add a test that fails if any traceability row lists a test that doesn't exist (rows may list "TODO" for now; the test reports them as gaps).
6. Usage study (plan §8 Discovery): a script scripts/usage_study.py that scans my agent transcripts (default ~/.claude/projects/**/*.jsonl) and outputs AGGREGATE COUNTS ONLY per session: shell commands that modify files, git pushes, package installs, deploys, DB commands, HTTP calls, MCP calls. It must never write transcript content, file paths from the transcripts, or prompts to disk. Run it and save the aggregate result to results/usage-study/<date>/. Summarise what it shows and what it means for the plan's pivot check.
7. Payload capture: scripts/capture_payloads.py that, installed as a hook in a THROWAWAY project folder (create one under a temp/scratch directory with project-local .claude/settings.json; never touch my global settings), records each hook payload as JSON into tests/fixtures/hosts/claude-code/<version>/. Tell me the exact steps to run a short Claude Code session in that throwaway folder to capture Bash, PowerShell, Edit, Write, UserPromptSubmit, SubagentStart/Stop and PostToolBatch payloads, or run them yourself if you can do so without touching my global config. Scrub secrets from captured payloads.

Rules: follow AGENTS.md (tests seen to fail first, unrelated issues → GitHub issue or docs/known-issues.md and keep going, never touch global agent config, report honestly). Stop when these 7 items are done. Update docs/progress.md with what was done, results, decisions, open questions, and what session 2 should start with.
```

## Session 2: Discovery part 2

```text
Read AGENTS.md and docs/progress.md, then docs/master-plan.md §7.1, §5, §8 Discovery and §17 week 1.

Task for this session:
1. Spikes for the design decisions in plan §7.1, each with a short measurement script in scripts/spikes/, raw results in results/spikes/<date>/, and an ADR (ADR-0003 onward):
   - S1/D1 hook latency: measure cold-start latency of a minimal Python hook handler, and of a handler that snapshots a 1k / 10k / 100k-file test repo, on this machine (Windows). Write a CI job that runs the same spike on Linux and macOS and uploads the numbers; it will only run once I push the repository, so say clearly that those results are still pending and don't push to get them. Recommend per-call process vs background process from the Windows numbers, and say what the Linux/macOS numbers would need to show to change the recommendation.
   - D2 hook failure policy: write the ADR (fail open with a visible gap; fail closed in strict mode; hook timeout below the agent's).
   - D3 object ownership: create snapshots that use git alternates to a user repo, run `git gc --prune=now` in the user repo after rewriting history, and check whether the snapshots survive. Then show the "own every object" approach survives. ADR.
   - D4 ignored files: measure the size of ignored files in a few real repos on this machine (aggregate sizes only), propose the default policy. ADR.
   - D5 schema version, D6 interpreter pinning (test inside a project with its own venv), D7 worktrees/monorepos: ADRs.
2. Prior art (plan §8 Discovery): for each of Turso AgentFS, turnback, Claude Code /rewind (docs), Cline checkpoints, OpenCode snapshots, toffoli/undolog/revoco, container-use, write docs/prior-art/<tool>.md: how it captures, stores and restores; what happens for (a) `rm -rf` run via shell, (b) `git push` then undo; licence; limits. Read source where available (clone into a scratch folder, record the commit). Mark anything inferred as inferred. If trying a tool requires installing software, list the steps for me instead of installing globally.
3. Architecture skeleton (plan §5.2): architecture/graph.json from plan diagrams 4.1–4.3 with every node status "planned", architecture/index.html offline renderer with the Overview view, architecture/README.md with the update rule from plan §5.3, scripts/arch_check.py (structure checks: unique ids, no dangling edges, sub ≤ 42 chars, roadmap fields). Add the render check with Playwright and SHOW it failing once on a deliberately broken page, then passing.
4. Report the pivot check (plan §8): using the usage-study results and prior art, does the plan's order still make sense? Recommend; don't change the plan without my approval.

Follow AGENTS.md. Update docs/progress.md at the end, including the ADR list and anything that needs my decision.
```

## Session 3: Alpha A: foundations, capture, ledger

```text
Read AGENTS.md, docs/progress.md, and docs/master-plan.md §2, §3, §4.2, §4.7, §6, §7, §7.1 (with the ADRs in docs/adr/), §8 v0.1-alpha, §13, §15.3, §16.4.

Task: the first part of v0.1-alpha. Write a one-page design note per component in docs/design/ before coding it. Tests first, each seen to fail.
1. Testability foundations (§16.4): agentvc/io.py (all file, process and SQLite access; fault injection hooks), injectable clock and random source, named crash points helper, the bounded subprocess helper (timeouts, output caps, child cleanup incl. Windows job objects), atomic write + file lock helper, secret scrubber, debug log (AGENTVC_DEBUG).
2. agentvc/wiring.py as the single list of events and tools; generate the Claude Code hook config from it; a test that fails if they differ.
3. Event model + Claude Code adapter, tested only against the captured fixtures in tests/fixtures/hosts/claude-code/. Hook timeout and fail-open/closed behaviour per ADR D2. Unknown payloads go to .agentvc/blindspots.jsonl.
4. Ledger (SQLite, schema_version table, runs/turns/steps, prev_hash chain; anchor outside the workspace per plan §14.1). A Pre without a Post becomes a visible GAP.
5. CLI: `agentvc history`, `agentvc inspect <run>`, `agentvc check` (sends a realistic fake tool call through the real hook path), `--json` on each.
6. `.agentvc/` creates its own .gitignore with `*`; PreToolUse denies agent writes into `.agentvc/`.
7. Adversarial tests for capture and ledger from plan §15.3, each seen to fail on a deliberately broken build first.
8. Update architecture/graph.json (flip shipped nodes), traceability.yml, fmea.md, CHANGELOG.

Do not start snapshots or rollback; that's session 4. Follow AGENTS.md. Update docs/progress.md at the end.
```

## Session 4: Alpha B: snapshots and rollback

```text
Read AGENTS.md, docs/progress.md, and docs/master-plan.md §2 (G1 especially), §4.3, §4.4, §7.1 (ADRs D3, D4), §14.1, §15.3 (snapshotter, restore), §16.1 FMEA rows.

Task: the second part of v0.1-alpha. Design notes first; tests first, seen to fail.
1. Snapshotter: git object store at .agentvc/objects with a private index, owning every object it references (ADR D3); fast path for edit tools (target file only); full snapshot for shell commands; stat-before/after check for files changing during the snapshot (mark unprotected); budgets → UNPROTECTED, reported. Ignored-file policy per ADR D4.
2. `agentvc diff <run>[:step]`.
3. Restore with per-file compare-and-swap exactly as plan diagram 4.4; pre-rollback snapshot; `rollback <run|turn|step>`, `rollback --undo`; conflicts listed, never overwritten without --force (and --force still saves first). Intent log + named crash points on every write; recovery on next start (`agentvc repair`).
4. Turns from UserPromptSubmit; `agentvc undo` / `redo` (last turn).
5. Outside-change detection (plan §14.1): compare each step's before-snapshot with the previous step's after-snapshot; mark differences as outside changes; never roll them back.
6. Config file .agentvc/config.toml with modes observe/protect (plan §14.1).
7. Adversarial tests for snapshotter and restore from plan §15.3, each seen to fail first.
8. Update graph.json, traceability, FMEA, CHANGELOG.

Follow AGENTS.md. G1 failures are S0: stop the line for the feature until fixed. Update docs/progress.md at the end.
```

## Session 5: Alpha C: safety test suite

```text
Read AGENTS.md, docs/progress.md, and docs/master-plan.md §15 and §16 (especially §16.2, §16.3 gate ramp column "v0.1-alpha", §16.4).

Task: make v0.1-alpha's safety evidence complete.
1. Scenario runner (eval/scenarios/, `python -m eval.run --all`) with at least 10 scenarios from plan §8 v0.1 and §14.1 (shell rm -rf / mv / sed -i, human edit after the run, generated files, symlinks, binaries, oversized files, mid-run commit, non-git folder, Windows paths).
2. Property test for G1 (random file trees + random agent and user edits → rollback never changes a byte the agent didn't write).
3. Reference model + model-based (stateful) test: agent edits, user edits, snapshot, rollback, undo, redo; compared to the reference model.
4. Differential test: snapshot/restore vs a plain-copy reference implementation; `agentvc diff` vs `git diff --no-index`.
5. Metamorphic tests: rollback then --undo = no change; running from a subfolder gives the same result.
6. Crash-point test that crashes at every named point in restore and checks recovery; chaos test killing the process during snapshot and rollback.
7. eval/adversarial/ runner (`python -m eval.adversarial --all`) and the attack-coverage report (which §15.3 attacks have tests).
8. Measure the v0.1-alpha gates (§16.3 ramp) and the performance budgets (§10); save results in results/.
9. Report honestly which gates pass and which don't, with numbers. Don't lower any gate.

Follow AGENTS.md. Every failure found: related → fix per §13.3; unrelated → issue and continue. Update docs/progress.md, and state whether v0.1-alpha's "Done when" is met.
```

## Session 6: v0.1 public: completion

```text
Read AGENTS.md, docs/progress.md, and docs/master-plan.md §8 v0.1, §5, §6, §11, §14.5, §14.8, §17 weeks 4–5.

Task: complete v0.1 (everything listed under "v0.1 (public)" in the plan) except the red team and the release itself:
context sync after rollback; subagent and parallel-batch attribution; `status`; `gc`; ignored-file policy; install/uninstall that preserves other hooks (with tests); session banner; `doctor --bundle`; transcript replay (eval/replay_transcripts.py, aggregate output only); playground repo content (scripted disasters) in a playground/ folder; architecture page Data flow + Lifecycle views and all §5.3 checks in CI; arch_export.py generating README diagrams; README per plan §11; quick start, commands, troubleshooting, threat model, limitations docs; packaging (pyproject entry point, plugin manifest).
Design notes first, tests first (seen to fail), adversarial tests for self-protection (§15.3).
Do not publish, push or tag anything. Update docs/progress.md, and list what remains for the v0.1 release.
```

## Review session

Run in a **separate** session (ideally a different model/agent) after sessions 3–5, and before every release.

```text
You are reviewing AgentVC, not building it. Read AGENTS.md, docs/master-plan.md (§2 guarantees, §13.1 definition of done, §15, §16.3), CONTRIBUTING.md and MAINTAINING.md "Reviewing pull requests".

Review the changes since «commit or tag, e.g. the first commit», in this order: guarantees (could any of G1–G8 break? ask "what if the process is killed here?" at every write), tests (seen to fail? adversarial tests present? any test weakened or skipped?), design rules (I/O layer, injected clock, crash points, timeouts, no runtime deps, no network), scope, docs/architecture, readability.

Run the test suites and the adversarial runner yourself; don't trust earlier reports.
Do NOT fix anything. Write findings to docs/reviews/<date>-review.md with severity S0–S3, file:line, a concrete failure scenario, and a suggested fix. For each S0/S1 also open a GitHub issue (or add to docs/known-issues.md if there's no repo yet). Summarise the top findings for me.
```

## Red-team session

Run in a **separate** session (a different model/agent from the builder) before each release.

```text
You are the red team for AgentVC «version». Read AGENTS.md and docs/master-plan.md §2 and §15.4.

Goal: break any of the guarantees G1–G8. Lose data the user can't get back, cause an action AgentVC doesn't record or report, repeat an effect in a fork, release something early, leak a secret, or disable AgentVC without the user noticing.

Set up with `make redteam` (or, if it doesn't exist yet, create a throwaway copy of playground/ in a scratch folder with AgentVC installed using project-local settings only). Never touch my global agent configuration or my real projects.
Time budget: «e.g. 2 hours of work». For every attempt, record: what you tried, the exact steps, what happened, and which guarantee it targets.
Write the report to docs/adversarial/redteam-«version».md. Every successful attack: add it to eval/adversarial/ as an attack case, mark it with a strict xfail probe, and open a GitHub issue (or docs/known-issues.md) with severity. Do NOT fix anything yourself.
Summarise: attempts / successes / severities.
```

## Release session

```text
Read AGENTS.md, docs/progress.md, MAINTAINING.md "Releases", and the latest review and red-team reports.

Prepare release «vX.Y.Z». Go through the release checklist in MAINTAINING.md item by item and report each as done / failing / needs me. Fix only release-preparation items (changelog, version bumps, docs numbers regenerated from results/, architecture meta). Any code problem found → follow AGENTS.md issue rules; an open S0/S1 means the release does not go ahead.
Do NOT push, tag, publish or create the GitHub release. When everything is ready, give me the exact commands to run, in order.
```

## Next-milestone session

For v0.2 onward. Split a milestone across several sessions if it's large (as v0.1 was split into sessions 3–6).

```text
Read AGENTS.md, docs/progress.md, and docs/master-plan.md: §8 «milestone, e.g. v0.2 "Know your effects"», plus every section that milestone refers to (§14 items, §15.3 attacks for its components, §16.3 gate-ramp column, §5.3 architecture rule).

Task: «the part of the milestone for this session, e.g. "effect detection, recovery classes and contract format v0"».
First, list the work items you'll do this session and the tests that will prove each, then proceed without waiting unless something needs my decision.
Design notes first; tests first and seen to fail; adversarial tests for safety-relevant components; update architecture, traceability, FMEA, docs and CHANGELOG.
Follow AGENTS.md. Update docs/progress.md at the end, including whether the milestone's "Done when" is met yet.
```

## Fix-an-issue session

```text
Read AGENTS.md and docs/progress.md. Fix GitHub issue #«number» («or the entry in docs/known-issues.md»).
Follow docs/master-plan.md §13.3 for a related issue: reproduce minimally, write a failing test and show it failing, find and state the root cause, search for the same mistake elsewhere, make the smallest fix, run the relevant tiers and the component's adversarial tests, update FMEA/traceability/docs/CHANGELOG. For S0/S1, write a short postmortem in docs/postmortems/.
Work on a branch named fix/«number»-short-name. Don't push. Summarise: root cause, the test that failed before and passes after, other places checked.
```
