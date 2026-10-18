# Pivot check, 2026-10-18

The plan's Discovery milestone ends with a pivot check (plan §8, §12). This is the recommendation from Discovery parts 1 and 2. **The plan is unchanged**; any change needs the maintainer's decision.

## The rules being checked

- **§8:** "If shell file changes and side effects are rare, start with v0.4 (outbox)."
- **§12, "Problem rarer than assumed":** if the usage study shows it, lead with effects/outbox.
- **§12, "Already solved":** if `/rewind` + turnback pass the v0.1 scenarios, contribute upstream and focus on v0.2–v0.4.

## Evidence

**Usage study** (one person, 35 sessions, `results/usage-study/2026-10-18/`):
- Shell file changes are **common**: 26.5% of file changes went through the shell, in 17 of 31 tool-using sessions; a third of sessions ran a destructive command (`rm -rf`, `git reset --hard`, force push…).
- External side effects are **rare**: 3 pushes, 0 deploys, 6 database commands, 7 HTTP writes; MCP calls in a single session.

**Prior art** (`docs/prior-art/`):
- **File and shell undo now exists in several tools.** turnback (hook-based, six agents) snapshots the whole tree before every shell command; Cline and OpenCode snapshot per turn or step.
  - **Claude Code's `/rewind`** still doesn't cover shell changes.
  - **This is plan §12's "already solved" signal, partly met:** turnback in particular covers much of v0.1's headline feature.
- **But the existing tools miss AgentVC's guarantees in specific, checkable ways:**
  - **Rollback overwrites the user's edits:** turnback's CLI by default, and Cline, which also deletes the pre-restore state so the restore can't be undone (G1).
  - **Snapshots borrow objects from the user's repository:** OpenCode, through alternates, the set-up the D3 spike showed losing snapshots after a prune; Cline writes into the user's `.git` (G1).
  - **Missing coverage:** no git repository means no snapshots (Cline, OpenCode); ignored files like `.env` aren't captured (Cline).
  - **External effects are ignored by all file-undo tools:** a `git push` is neither undone nor reported (G3).
  - **No platform support:** AgentFS's sandbox and container-use don't run on Windows.
- **The classify-and-compensate libraries** (toffoli, undolog, revoco) handle side effects, but only for actions routed through their own wrappers or proxies, never the agent's shell.

**Outside validation:** the 5+ conversations with other agent users (plan §8) **haven't happened yet**. The usage study is a sample of one.

## Recommendation

1. **Keep the plan's order: v0.1 (undo for files and shell) first.** The usage data points at shell file changes, not external effects, as the everyday risk, so the outbox-first pivot isn't indicated.
2. **Make "already solved" a measured question instead of a feeling.** During v0.1-alpha, run the v0.1 scenario suite against turnback as well as AgentVC (plan §16.2 already lists a differential test against turnback and `/rewind`). If turnback passes the G1 scenarios, plan §12's response applies: contribute the missing safety properties upstream and move AgentVC's effort to v0.2–v0.4. The prior-art reading suggests it won't pass them as it stands (overwrites by default), but that's an inference until it's run.
3. **Lead the v0.1 story with what's actually different,** not with "undo shell changes", which is no longer unique:
   - rollback that never overwrites the user's work, and can itself be undone;
   - history that survives the user's own git maintenance;
   - honest reporting of what can't be undone, including pushes;
   - tamper evidence;
   - Windows as a first-class platform.
4. **Do the outside conversations before v0.1 public.** If none of the five people has felt this pain, plan §12 says that's the pivot signal, regardless of this sample.

## Decisions for the maintainer

- Accept, change or reject this recommendation.
- Whether to add the turnback differential run to the v0.1-alpha "Done when" (today it's in the test strategy, not the milestone criteria).
