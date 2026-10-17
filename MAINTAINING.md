# Maintaining AgentVC

The handbook for maintainers: how to triage, review, merge, release, and respond when something goes wrong. Contributors should read [CONTRIBUTING.md](CONTRIBUTING.md); this file is about running the project.

---

## Contents

1. [Roles](#roles)
2. [Contribution phases](#contribution-phases)
3. [Routines at a glance](#routines-at-a-glance)
4. [Labels](#labels)
5. [Triaging issues](#triaging-issues)
6. [Reviewing pull requests](#reviewing-pull-requests)
7. [Merging](#merging)
8. [Releases](#releases)
9. [Incidents: a released version harms users](#incidents-a-released-version-harms-users)
10. [Security reports](#security-reports)
11. [Keeping the project healthy](#keeping-the-project-healthy)
12. [Decisions and governance](#decisions-and-governance)
13. [Repository settings](#repository-settings)

---

## Roles

| Role | Who | Can |
|---|---|---|
| **Lead maintainer** | `@Jay2Kumar1Sharma` | Everything below, plus releases, security advisories, adding maintainers, final say on disputes |
| **Maintainer** | listed in `.github/CODEOWNERS` | Triage, review, merge, label, close |
| **Triager** | trusted contributors | Label, reproduce, close duplicates (GitHub "Triage" role) |
| **Contributor** | anyone | Discussions and planning issues now; pull requests once contributions open ([phases](#contribution-phases)) |

While the project has one maintainer, "a second reviewer" in this guide can be a separate AI review session that didn't write the code, plus your own review after a break. Record which one in the PR.

---

## Contribution phases

| Phase | Who can contribute code | Status |
|---|---|---|
| **1. Closed** (now) | Maintainer only (including the maintainer's AI coding sessions). Outside PRs are closed automatically by `.github/workflows/close-external-prs.yml` with a polite message | **Current** |
| **2. Invited** | Specific people the maintainer invites as collaborators, on agreed issues | After v0.1 at the earliest |
| **3. Open** | Anyone, following CONTRIBUTING.md | After the first milestones, when the checklist below is complete |

**What stays open in phase 1:** Discussions (feedback on the plan, agent-disaster stories), security reports, and issues for planning information (agent hooks, effect contracts, feature ideas). If issue volume becomes a burden, GitHub's **interaction limits** (Settings → Moderation) can restrict issues to collaborators for up to 6 months at a time.

**Checklist for opening contributions (phase 3):**
- [ ] At least v0.1 is released, and the maintainer has decided which milestone opens contributions
- [ ] `make fast` and `make test` work on a fresh clone on Linux, macOS and Windows, as CONTRIBUTING.md describes
- [ ] CI runs on pull requests from forks, with required checks and branch protection configured
- [ ] Contributor sign-off policy decided (e.g. DCO or CLA) and its check installed; CONTRIBUTING.md updated
- [ ] At least 5 well-described `good first issue` issues (adapters, contracts, docs, tests)
- [ ] The maintainer has time for reviews (first response targets in this handbook)
- [ ] Delete `.github/workflows/close-external-prs.yml`
- [ ] Update the README "Contributing" section and badge, the banner at the top of CONTRIBUTING.md, and the notes in the issue templates
- [ ] Announce in Discussions and in the release notes

---

## Routines at a glance

> Response times in this handbook are **targets for a volunteer-run project**, not promises. Public-facing docs (SECURITY.md, issue templates) only state targets the current maintainers can actually keep.

| When | Task | Time |
|---|---|---|
| **Daily** (or every working day) | Check new issues for `severity:S0`/`S1` and security reports; respond to them first | 10 min |
| **Twice a week** | Triage new issues; review open PRs | 30–60 min |
| **Weekly** | Triage all `found-during-dev` issues; look at nightly CI failures; review the drift-canary results (agent updates) | 30 min |
| **Every release** | Release checklist (below), red-team report, quality metrics in release notes | half a day+ |
| **Monthly** | Dependency/tool updates for dev tooling; stale-issue sweep; roadmap cards on the architecture page; user-signal review (recoveries reported, interviews) | 1 h |
| **Per minor release** | Review `docs/threat-model.md`, `hazards.md`, limitations page and support policy | 1 h |

---

## Labels

Create them once with [`scripts/sync-labels.sh`](scripts/sync-labels.sh) (`gh` CLI required).

| Group | Labels | Meaning |
|---|---|---|
| **Type** | `bug`, `feature`, `docs`, `test`, `adapter`, `contract`, `question` | What the issue is |
| **Severity** | `severity:S0` … `severity:S3` | S0 = guarantee broken and harmful; S1 = guarantee at risk / core flow fails; S2 = wrong result with workaround; S3 = cosmetic |
| **Guarantee** | `guarantee:G1` … `guarantee:G8` | Which promise is affected |
| **Component** | `component:capture`, `snapshot`, `restore`, `ledger`, `classifier`, `fence`, `outbox`, `mcp-proxy`, `cli`, `report`, `architecture`, `docs` | Where it lives |
| **Platform** | `os:windows`, `os:macos`, `os:linux`, `agent:claude-code`, `agent:codex`, … | Where it happens |
| **State** | `needs-triage`, `needs-repro`, `needs-design`, `accepted`, `blocked`, `wontfix`, `duplicate` | Where it is in the process |
| **Source** | `found-during-dev`, `from-redteam`, `from-fuzzing`, `from-user` | How it was found |
| **Help** | `good first issue`, `help wanted` | For contributors |

---

## Triaging issues

Every new issue gets `needs-triage` automatically. For each one:

1. **Is it a security vulnerability?** Move it to a private advisory (see [Security reports](#security-reports)) and hide the public details.
2. **Duplicate?** Close with `duplicate`, linking the original.
3. **Set type, component, platform labels.**
4. **Set severity**, using the table below. When unsure, pick the higher level and say why.

   | Severity | Examples | Response |
   |---|---|---|
   | **S0** | rollback overwrote user work; a fork repeated a payment; an effect released before commit; a secret in an export | Respond within 24 h; start the [incident procedure](#incidents-a-released-version-harms-users) if it's in a release |
   | **S1** | a step silently not recorded; AgentVC disabled without notice; hooks hang the agent | Respond within 3 days; fix in the next patch |
   | **S2** | wrong label in a report; budget exceeded on an unusual repo | Respond within a week; next minor release or limitations page |
   | **S3** | typos, cosmetics | Backlog |

5. **Can it be reproduced?** If there isn't enough information, add `needs-repro` and ask for `agentvc doctor --bundle`, versions and steps. Try it yourself on the reporter's OS when possible.
6. **Decide:** `accepted` (with a target milestone), `needs-design` (requires a design note or ADR first), or `wontfix` (explain why; link the limitations page if relevant).
7. **Remove `needs-triage`.**

### `found-during-dev` issues

These were raised by contributors (or AI agents) who found an unrelated problem and kept working, as CONTRIBUTING.md asks. In the **weekly triage**:
- confirm severity and add a reproduction if missing;
- S0/S1 → into the current milestone, fixed on its own branch;
- S2/S3 → next milestone or backlog;
- target: no `found-during-dev` issue older than one week without triage.

### Saying no

Saying no keeps the project focused. Close with a short, kind reason when a request:
- turns AgentVC into an agent, framework, dashboard, gateway or cloud service (non-goals);
- adds a runtime dependency without an ADR;
- weakens a guarantee;
- belongs in another project (suggest which one).

---

## Reviewing pull requests

**Check the basics first:** CI green on all three OSes, the template filled in, commits signed (Verified) with no email addresses in their messages, one topic per PR.

**Then review in this order:**

1. **Guarantees.** Which of G1–G8 does this touch? Could it lose user work, miss an action, repeat an effect, release early, leak a secret, or be switched off silently? Ask "what happens if the process is killed right here?" at every write.
2. **Tests.**
   - Was each new test seen to fail? (Ask for evidence if it isn't obvious.)
   - Are there adversarial tests for safety-relevant code?
   - Were any tests weakened, skipped or deleted? If so, is the reason convincing?
   - Is `tests/traceability.yml` updated?
3. **Design rules.** I/O through `agentvc.io`; injected clock/randomness; crash points on new write paths; timeouts on hook paths; no runtime dependencies; no network calls.
4. **Scope.** Unrelated changes belong in another PR. Unrelated problems found should be listed as GitHub issues, not fixed here.
5. **Docs and architecture.** Docs, `--help`, changelog, `architecture/graph.json` updated if needed (`make arch` passes in CI).
6. **Readability.** Can the next maintainer understand it without the author?

**Tone:** ask questions rather than issue orders; explain the risk you see; approve when it's safe, not when it's perfect. Use GitHub's "suggested changes" for small fixes.

**Response times to aim for:** first review within 3 working days; follow-ups within 2.

---

## Merging

- **Squash-merge** with a conventional-commit title (`fix: …`, `feat: …`).
- **Required before merge** (enforced by branch protection): all required checks green, conversations resolved. **One approving review** once there are two or more maintainers. While there's a single maintainer, GitHub can't require an approval (you can't approve your own PR); record the self-review and the separate AI review session in the PR description instead.
- **Never merge with** an open S0/S1 in the touched code, a failing or skipped required check, or a weakened test without a stated reason.
- **After merge:** close linked issues, update the milestone, and make sure "Unrelated issues found" in the PR all have GitHub issues.

---

## Releases

AgentVC uses semantic versioning. Before 1.0, a minor version (0.x) may break formats, but only with a migration and a changelog note.

### Release checklist

```text
[ ] First release only: code-of-conduct contact email added to CODE_OF_CONDUCT.md
[ ] Milestone has no open S0/S1 issues
[ ] Gates for this version met (coverage, mutation, crash points, compatibility matrix; see docs/master-plan.md §16.3)
[ ] Full adversarial suite passes on Linux, macOS, Windows
[ ] Red-team session done; report at docs/adversarial/redteam-<version>.md
[ ] Upgrade tests from every earlier released version pass
[ ] CHANGELOG.md: "Unreleased" moved under the new version, with migration notes and a "Quality" paragraph (escape rate, S0/S1 fix times)
[ ] Docs and limitations page updated; README numbers regenerated from results/
[ ] Architecture graph meta.version and changelog bumped
[ ] Version bumped in pyproject.toml and the plugin manifests
[ ] Tag vX.Y.Z (signed); CI builds, signs (Sigstore) and publishes artifacts + SBOM
[ ] Smoke test: install the published package in a clean environment; `agentvc check` passes with Claude Code
[ ] GitHub release notes published; announcement in Discussions
[ ] Rollback plan ready: how to yank, and how users pin the previous version
```

### Hotfixes

Branch `hotfix/vX.Y.Z` from the release tag, fix with a regression test, release `vX.Y.(Z+1)`, then merge the fix back into `main`.

### Yanking

If a release can lose data, yank it from PyPI (`Yank` in the PyPI UI) and the plugin marketplace, and pin an issue telling users which version to use.

---

## Incidents: a released version harms users

An **S0 in a released version** (data loss, repeated external effect, early release, leaked secret) is an incident.

| Step | Action | Deadline |
|---|---|---|
| 1 | Acknowledge on the issue; label `severity:S0`; assign yourself | 24 h |
| 2 | Mitigate: publish a workaround (e.g. `agentvc config set mode observe`, or which command to avoid); yank the release if data loss is likely | as soon as possible |
| 3 | Reproduce with a failing test; find the root cause; search for the same defect elsewhere | — |
| 4 | Hotfix release (above) | ≤ 2 days for S0 |
| 5 | Communicate: release notes, pinned issue, and a GitHub security advisory if relevant; tell users how to check whether they were affected and how to recover from `.agentvc/` history | with the fix |
| 6 | Postmortem in `docs/postmortems/YYYY-MM-DD-title.md`: what happened, impact, root cause, **why no test caught it**, which test or gate was added | ≤ 1 week |

Postmortems are blameless: they're about the system, not the person.

---

## Security reports

- Reports arrive through **GitHub private vulnerability reporting** (see [SECURITY.md](SECURITY.md)).
- Acknowledge within 3 days; agree on a disclosure date (default 90 days, sooner once fixed).
- Fix in a private fork or a temporary private branch; request a CVE through the GitHub advisory if appropriate.
- Credit the reporter in the advisory unless they ask not to be named.

---

## Keeping the project healthy

| What | How |
|---|---|
| **Nightly CI failures** | Each one becomes an issue (`from-fuzzing` or `bug`) with the failing seed; never just re-run until green |
| **Flaky tests** | Quarantine only with an issue and a two-week deadline; flaky means there's a bug somewhere |
| **Agent updates** | The weekly drift canary opens an issue when an agent's hook payloads change; update fixtures and the adapter within one release |
| **Stale issues** | Monthly: ask for updates on `needs-repro` issues older than 30 days; close after 14 more days without reply (they can be reopened) |
| **Stale PRs** | Ping after 14 days; offer to take over or close after 30 days, with thanks |
| **Contributor experience** | Keep `good first issue` stocked (5+); answer first-time contributors quickly |
| **Process metrics** (each release) | Escape rate, time to fix S0/S1, recurrences, untriaged `found-during-dev` issues, quarantined tests |
| **Docs** | Commands in docs run in CI; fix broken docs like broken code |

---

## Decisions and governance

- **Small decisions:** made in the PR or issue.
- **Significant decisions** (a guarantee, a format, a dependency, a budget, a new major feature): an ADR in `docs/adr/NNNN-title.md`, discussed in a PR, merged by a maintainer. Superseded ADRs are kept and marked, not deleted.
- **Changing a guarantee** requires an ADR, an update to the README and tests, and a release-note entry.
- **Disagreements:** discuss on the PR or in Discussions; if unresolved, the lead maintainer decides and records the reasoning.
- **Becoming a maintainer:** sustained, high-quality contributions and reviews over about three months; nominated by a maintainer, agreed by the lead. Added to `CODEOWNERS` and the Roles table.
- **Stepping down:** tell the other maintainers; you move to emeritus, with thanks in the README.

---

## Repository settings

Set these once on GitHub:

| Setting | Value |
|---|---|
| Default branch | `main` |
| Branch protection on `main` (**single maintainer**) | Require a pull request; required status checks (fast lane, full test on 3 OSes, architecture) once they exist; require conversations resolved; require signed commits; block force pushes and deletion. **Don't** require approving reviews or code-owner review yet: a sole maintainer can't approve their own PR and would be locked out |
| Branch protection on `main` (**two or more maintainers**) | Add: 1 approving review, code-owner review, dismiss stale approvals, apply rules to administrators |
| Merge options | Squash merge only; auto-delete head branches |
| Private vulnerability reporting | Enabled (Security → Settings) |
| Discussions | Enabled, categories: Q&A, Ideas, Design, Show and tell ("saved by AgentVC") |
| Issue templates | `.github/ISSUE_TEMPLATE/` (blank issues disabled) |
| Labels | `scripts/sync-labels.sh` |
| Pages | Deploy `architecture/` from `main` |
| Dependabot | Security alerts on; version updates configured in `.github/dependabot.yml` (GitHub Actions now; pip dev dependencies once `pyproject.toml` exists) |
| Releases | Signed tags; release workflow publishes to PyPI with trusted publishing |
| Commit identity | Commits are SSH-signed and use an email that is verified on the maintainer's GitHub account (repository-local git config: `user.email`, `gpg.format ssh`, `user.signingkey`, `commit.gpgsign true`). The public key is added to GitHub as a **Signing key** (Settings → SSH and GPG keys). Leave "Block command line pushes that expose my email" **off**, or GitHub will reject pushes of these commits. The email is never written in files or commit messages; no sign-off or co-author trailers. A contributor sign-off policy (DCO or CLA) is decided when contributions open |
