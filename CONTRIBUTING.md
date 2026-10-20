# Contributing to AgentVC

Thanks for helping. AgentVC records what coding agents do and lets people undo it, so **a bug here can destroy someone's work**. This guide explains how to contribute in a way that keeps that from happening.

> [!IMPORTANT]
> **Contributions are not open yet.** AgentVC is pre-alpha and the core is being built by the maintainer. Pull requests from outside the maintainer team are **closed automatically** for now, so please don't spend time on code changes yet.
>
> **What you can do now:** give feedback on the [plan](docs/master-plan.md) and share agent-disaster stories in [Discussions](../../discussions); report security problems through [SECURITY.md](SECURITY.md).
>
> **When it opens:** after the first milestones are released. The README badge and this banner will change, and it will be announced in Discussions. The rest of this guide describes how contributing will work then; the maintainer already follows it.

`pip install -e ".[dev]"` and `make fast` work now; `make test`, `make arch`, `make fuzz`, `make chaos` and the `eval` runners arrive with the milestones that build them and fail with a "not implemented yet" message until then.

**Windows:** `make.ps1` offers the same targets as the Makefile, e.g. `powershell -ExecutionPolicy Bypass -File make.ps1 fast`.

---

## Contents

1. [Ground rules](#ground-rules)
2. [Set up your environment](#set-up-your-environment)
3. [Kinds of contribution](#kinds-of-contribution)
4. [Workflow: from issue to merged PR](#workflow-from-issue-to-merged-pr)
5. [Tests you must write](#tests-you-must-write)
6. [When you find a problem while working](#when-you-find-a-problem-while-working)
7. [Code style and design rules](#code-style-and-design-rules)
8. [Commits and PRs](#commits-and-prs)
9. [Architecture page and docs](#architecture-page-and-docs)
10. [Using AI coding agents to contribute](#using-ai-coding-agents-to-contribute)
11. [Getting help](#getting-help)

---

## Ground rules

AgentVC makes eight promises to its users. Every change must keep all of them:

| ID | Guarantee |
|---|---|
| **G1** | Never destroy user work. Rollback only rewrites a file that still holds exactly what the agent left; every rollback can be undone. |
| **G2** | Nothing silent. Every agent action is recorded, or shown as a gap. |
| **G3** | Honest accounting. Reports say what couldn't be undone, and why. |
| **G4** | Never repeat the world. Replay and fork never re-run an external effect that already happened. |
| **G5** | Nothing leaves early. Staged effects are released only on commit, exactly once. |
| **G6** | Local and private. AgentVC sends none of its own data anywhere; secrets are scrubbed. |
| **G7** | Cheap enough to leave on. Overhead stays within the published budgets. |
| **G8** | Can't be silently switched off. |

Also:
- **The runtime uses only the Python standard library.** Development tools (pytest, Hypothesis, mypy…) are fine; runtime dependencies need an ADR (see [design rules](#code-style-and-design-rules)).
- **Be kind.** We follow the [Code of Conduct](CODE_OF_CONDUCT.md).
- **Security problems are not public issues.** See [SECURITY.md](SECURITY.md).

---

## Set up your environment

Requirements: Python 3.11+ and git 2.30+. The architecture render check also uses Playwright, installed by the dev extras below.

```bash
git clone https://github.com/Jay2Kumar1Sharma/AgentVC.git
cd agentvc
python -m venv .venv
# macOS/Linux: source .venv/bin/activate     Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m playwright install chromium   # only needed for `make arch`
make fast                               # must pass in under 2 minutes
```

Useful commands:

| Command | What it runs |
|---|---|
| `make fast` | Unit, golden, adapter-contract, small property and fast adversarial tests (< 2 min). Run before every push |
| `make test` | Everything that runs on a pull request (< 15 min) |
| `make arch` | Regenerates the code map and README diagrams, then validates and renders the architecture page |
| `make fuzz` / `make chaos` | Fuzzers / kill-at-random-point tests (longer) |
| `python -m eval.run --all` | Scenario suite (the numbers in the README come from here) |
| `python -m eval.adversarial --component <name>` | Attack catalog for one component |

Windows contributors are especially welcome: many file-system bugs only show up there.

---

## Kinds of contribution

| Contribution | Start here | Extra requirements |
|---|---|---|
| **Bug fix** | An issue labelled `bug` | A test that fails before your fix and passes after |
| **New agent adapter** (Gemini CLI, Cursor, Copilot, …) | Open a "New agent adapter" issue | Real hook payloads captured with `scripts/capture_payloads.py`; the adapter must pass the shared conformance suite |
| **New effect contract** (a CLI or API AgentVC should recognise) | Open a "New effect contract" issue | A YAML contract plus classifier tests; a dangerous effect must never be classed as safe |
| **New feature** | Open a feature request first and wait for a maintainer's `accepted` label | A one-page design note (below) |
| **Docs** | Any page under `docs/` or the README | Commands you show must actually work: docs commands run in CI |
| **Tests only** | `docs/testing/fmea.md`, `hazards.md` or the attack catalog for gaps without tests | Link the FMEA/hazard/attack row in `tests/traceability.yml` |
| **Triage help** | Reproduce open `needs-repro` issues on your OS | Comment with exact versions and steps |

**Please open an issue before writing a large change.** It saves you from building something we can't merge.

---

## Workflow: from issue to merged PR

1. **Pick or open an issue.** Comment that you're working on it; a maintainer assigns it.
2. **Branch from `main`:** `fix/<issue>-short-name`, `feat/<issue>-short-name`, `adapter/<agent>`, `contract/<tool>`, `docs/<topic>`.
3. **For features: write a design note** (≤ 1 page) in `docs/design/<feature>.md`:
   - what it does and why;
   - what it borrows from existing work (and its licence);
   - which guarantees (G1–G8) it touches;
   - how it can fail, and the new rows for `docs/testing/fmea.md`.
   For design decisions that change a guarantee, a budget or a dependency, also write an ADR in `docs/adr/`.
4. **Write tests first and watch them fail** (see [Tests you must write](#tests-you-must-write)).
5. **Implement in small commits.**
6. **Run `make test`** (and `make arch` if you changed the architecture).
7. **Open a pull request** using the template. Fill every section, including "Unrelated issues found".
8. **Review:** a maintainer reviews. Expect questions about failure modes; that's normal here.
9. **Merge:** maintainers squash-merge once CI is green and the checklist is complete.

### Definition of done

A change is ready to merge only when:
- [ ] It works on Linux, macOS and Windows (CI shows all three).
- [ ] New tests were **seen to fail first**, then pass.
- [ ] Safety-relevant changes include **adversarial tests** (attacks from `eval/adversarial/`) as well as normal tests.
- [ ] `tests/traceability.yml`, `docs/testing/fmea.md` and `hazards.md` are updated where relevant.
- [ ] Coverage for changed lines is complete; safety modules keep their coverage and mutation gates.
- [ ] Performance budgets still pass.
- [ ] Docs, `--help` text, `CHANGELOG.md` (under "Unreleased") and the architecture graph are updated.
- [ ] No open S0/S1 issue touches the changed code.

---

## Tests you must write

| Your change | Required tests |
|---|---|
| Any bug fix | A regression test that fails without the fix |
| Restore, rollback, snapshot or ledger code | Unit + scenario + property test; extend the model-based test if you add an operation; a crash-point test for each new write step |
| Hook/adapter code | Contract tests on **real captured payloads**, including malformed and missing-field cases |
| Classifier or contracts | Positive and negative examples; obfuscated variants (`sh -c`, variables, aliases) must not classify as safe |
| Fence, outbox | Deterministic simulation test and exactly-once check |
| CLI output | Golden-output test |
| Anything touching paths | Names-and-encodings cases (Unicode, long paths, Windows reserved names) |

Rules:
- **Two directions:** a check needs a test that it fires when it should *and* a test that tries to defeat it.
- **Seen to fail first:** break the code on purpose, confirm the test fails, then restore it.
- **Never weaken, skip or delete a failing test** to make CI pass. If a test is wrong, explain why in the commit message.
- **Known bugs** that can't be fixed yet get `@pytest.mark.xfail(strict=True, reason="#<issue>")`.

---

## When you find a problem while working

First decide: **is it related to what you're working on?**

- **Related**: it's in code you changed, caused by your change, or breaks your change's tests. **Fix it as part of your work**, with a test that failed before the fix.
- **Unrelated**: it existed before your change, in code you didn't touch (check whether it also happens on `main`). **Don't stop, and don't fix it in your branch.** Instead:
  1. Search for an existing issue: `gh issue list --search "<key words>" --state all`.
  2. If none exists, open one with the **"Found during development"** template (or `gh issue create --label found-during-dev`).
  3. List it under **"Unrelated issues found"** in your PR.
  4. Carry on with your work.

If the unrelated problem **blocks** your work, open the issue and ask a maintainer on it how to proceed. If it looks like **data loss, a repeated external effect or a leaked secret**, open the issue and also mention a maintainer right away. If it's a security vulnerability, use [SECURITY.md](SECURITY.md) instead of a public issue.

---

## Code style and design rules

- **Formatting and linting:** `ruff format` and `ruff check` (run by `make fast`).
- **Types:** type hints everywhere; `mypy --strict` must pass for safety modules (`restore`, `ledger`, `snapshot`, `classifier`, `fence`, `outbox`).
- **Standard library only at runtime.** Adding a runtime dependency requires an ADR and a maintainer's approval.
- **All I/O goes through `agentvc.io`** (files, processes, database), never `open()`/`subprocess` directly. This is what makes fault injection and crash testing possible.
- **Use the injected clock and random source**, never `time.time()` or `random` directly in engine code.
- **Every multi-step write path has named crash points** (`crashpoint("restore.after_write")`).
- **Never hang the agent:** every hook path has a timeout; failures become visible gaps, not exceptions.
- **No network calls** from AgentVC's own code (except releasing user-approved outbox effects).
- **Scrub before storing:** anything recorded passes through the secret scrubber.
- **Comments explain why**, not what.

---

## Commits and PRs

- **Commit gradually.** Make a commit as soon as each small step works, instead of one large commit at the end:
  - one logical change per commit (a test plus the code that makes it pass, a doc section, a refactor);
  - ideally under ~200 changed lines per commit (generated files and fixtures excepted);
  - the fast lane (`make fast`) passes after every commit;
  - refactors, formatting, generated files and behaviour changes go in separate commits;
  - a test written before its code is committed with that code, or first as `xfail(strict=True)` and un-marked in the next commit.
  Your branch keeps this history for review; `main` gets one squash-merged commit per PR.
- **Conventional commit prefixes:** `fix:`, `feat:`, `test:`, `docs:`, `refactor:`, `perf:`, `chore:`, `adapter:`, `contract:`, with a scope where useful (`fix(restore): …`).
- **Signed commits, no email addresses in messages.** Maintainer commits are cryptographically signed (GitHub shows **Verified**) and linked to the maintainer's GitHub account; messages contain no `Signed-off-by` or `Co-Authored-By` trailers. How outside contributors certify their right to submit code will be decided when contributions open.
- **One topic per PR.** Unrelated fixes go in their own PR.
- **Keep PRs reviewable:** under ~400 changed lines where possible; split larger work.
- **Breaking changes** (ledger format, contract format, CLI behaviour) must be called out in the PR and need a migration plan.

---

## Architecture page and docs

AgentVC keeps a live architecture page (`architecture/`). **If your change adds or removes a module, adapter, hook event, ledger table, recovery class or CLI flow, update `architecture/graph.json` in the same PR**, then run `make arch`. CI fails if a module is missing from the map, if the map points at deleted code, or if the README diagrams are out of date. Bug fixes, tests and docs-only changes don't need a graph update.

---

## Using AI coding agents to contribute

AI-assisted contributions are welcome. You're responsible for them as if you wrote them yourself:
- Read and understand every line before opening the PR.
- The rules in `CLAUDE.md` / `AGENTS.md` apply to your agent: follow the issue procedure above, never weaken tests or gates, raise unrelated issues and keep going.
- Say in the PR description that an agent was used, and which one.
- Don't let the agent disable AgentVC's own hooks in this repository.

---

## Getting help

- **Questions and ideas:** [GitHub Discussions](../../discussions).
- **Stuck on a PR:** comment on it and mention `@Jay2Kumar1Sharma`.
- **Design questions:** open a Discussion in the "Design" category before writing code.

Thank you for making agents safer to use.
