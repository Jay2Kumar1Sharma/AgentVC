# Instructions for coding agents working on AgentVC

These rules apply to every session. Read them fully before doing anything.

## What this project is

AgentVC is version control for AI-agent execution: it records every action a coding agent takes, snapshots the workspace around it, and lets the user see, diff, roll back, replay and approve what the agent did, honestly reporting anything it can't undo.

## Read these first, in this order

1. `docs/progress.md`: where the project is now, and the handoff from the last session. **Start here every time.**
2. `docs/master-plan.md`: the plan. **It governs.** Guarantees §2, architecture §4–§7, roadmap §8, workflow and issue handling §13, adversarial testing §15, test strategy §16, checklists §17.
3. `CONTRIBUTING.md`: workflow, definition of done, test requirements, code rules.
4. `docs/research.md`, `docs/gap-analysis.md`, `docs/why-gaps-remain.md`: background research. Read when relevant.
5. `docs/private/` (git-ignored, may be absent) holds the maintainer's private notes. Never commit it, never move its content into public files, and never copy another project's code or structure based on it.

## Fixed technical choices

- Python 3.11+, **standard library only at runtime**. Dev tools (pytest, Hypothesis, mypy, ruff, mutmut, Playwright) are fine. A runtime dependency needs an ADR and the user's approval.
- `git` is the only external binary.
- All file, process and database access goes through one I/O module; clock and randomness are injectable; multi-step writes have named crash points (plan §16.4).
- No network calls from AgentVC's own code.

## The eight guarantees (never break these)

G1 never destroy user work · G2 nothing silent · G3 honest accounting · G4 never repeat the world · G5 nothing leaves early · G6 local and private · G7 cheap enough to leave on · G8 can't be silently switched off. Full text in `docs/master-plan.md` §2.

## How to work on a task

1. Work only on the milestone or task the user gave you for this session.
2. For a feature: write a one-page design note in `docs/design/` (what, why, what it borrows, guarantees touched, failure modes) before code. Decisions that change a guarantee, budget, format or dependency need an ADR in `docs/adr/`.
3. Write tests first and **watch each one fail** before implementing. Safety-relevant code also needs adversarial tests (plan §15.3).
4. **Commit gradually, never one big commit.** Commit each small step as soon as it works: one logical change per commit (a test plus the code that makes it pass, a doc section, a refactor), ideally under ~200 changed lines, with the fast lane passing after every commit. Keep refactors, formatting, generated files and related-issue fixes in separate commits. Commit before switching tasks and before the end of the session. Use conventional prefixes, e.g. `fix(restore): skip files changed after the run (G1)`. **Commit identity:** this repository's git config signs every commit automatically (SSH signature, shown as **Verified** on GitHub) and uses the maintainer's GitHub-linked email from the repository's git config. Never write that email (or any email) into a file or commit message. Never add `Signed-off-by` (don't use `git commit -s`), `Co-Authored-By` or any other text containing an email to a commit message, and never change the repository's `user.email`, `user.signingkey`, `gpg.format` or `commit.gpgsign`. Check after committing: `git log -1 --format='%G?'` must print `G`, and `git log -1 --format='%ae'` must equal `git config user.email`. A test written before its code goes in the same commit as the code, or first as `xfail(strict=True)` with the marker removed in the next commit.
5. Run the relevant test tier (`make fast`, then `make test` once it exists) and report the real results, including failures.
6. If you changed the architecture (new module, adapter, hook event, ledger table, recovery class, CLI flow), update `architecture/graph.json` and run `make arch` (plan §5.3).
7. Update docs, `CHANGELOG.md` ("Unreleased"), and `tests/traceability.yml` / `docs/testing/fmea.md` where relevant.
8. **End of session:** update `docs/progress.md` (what was done, what's next, decisions made, open questions, unrelated issues raised), then give the user a short summary.

## When you find an issue (plan §13.3)

**First decide: is it related to your current task?** Related = in code you changed, caused by your change, or breaks your task's tests. Unrelated = it existed before your task, in code you didn't touch (check on `main` if unsure).

- **Unrelated:** don't stop and don't fix it in your branch.
  1. `gh issue list --search "<keywords>" --state all` to check for duplicates.
  2. `gh issue create` with the "Found during development" template and labels (`found-during-dev`, `severity:S?`, `component:?`).
  3. If there's no GitHub repo yet or `gh` isn't available, add it to `docs/known-issues.md` instead.
  4. List it under "Unrelated issues found" in your summary, then **continue your task**.
  5. If it's S0/S1 (data loss, repeated external effect, leaked secret, silent recording failure), **tell the user immediately**, then continue unless they say stop.
  6. If it blocks your task, raise it and ask the user how to proceed.
- **Related:** follow plan §13.3: write it down, assign severity, reproduce minimally, write a failing test, find the root cause, search for the same mistake elsewhere, fix minimally, verify, record. For S0, don't continue the rest of the feature until it's fixed.

## Never

- Delete, skip or weaken a failing test to get green. If a test is wrong, explain why in the commit.
- Edit CI gates, coverage thresholds, performance budgets or `xfail` markers unless the user asked.
- Claim something works without having run it. Report failures and skipped steps plainly.
- Leave a whole feature or session's work in one large commit, or uncommitted at the end of a session.
- Mark a task done with an open S0, or an open S1 in that task.
- Disable or bypass AgentVC's own hooks while developing AgentVC.
- Change the user's **global** agent configuration (e.g. `~/.claude/settings.json`). Do hook experiments in a throwaway project folder with project-local settings.
- Read or store the *content* of the user's agent transcripts. The usage study (plan §8 Discovery) stores **aggregate counts only**.
- Push to a remote, create a GitHub repository, publish a package or create a release without the user's explicit approval.
- Invent decisions that belong to the user (see below).

## Ask the user before

- Choosing the project name, licence or GitHub owner (if not already recorded in `docs/progress.md`).
- Anything in "Never" above.
- Changing a guarantee, budget, format or the scope of a milestone.
- Severity S0/S1 decisions you're unsure about.

## Definition of done for a task

Tests seen to fail then pass · adversarial tests where required · relevant tier green · docs/changelog/traceability updated · architecture updated if needed · `docs/progress.md` updated · honest summary given to the user.
