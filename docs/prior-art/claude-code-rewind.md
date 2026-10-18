# Claude Code checkpoints and `/rewind`

- **Source read:** the official docs page [code.claude.com/docs/en/checkpointing](https://code.claude.com/docs/en/checkpointing), read 2026-10-18 (Claude Code 2.1.302 is current). Closed source; nothing below comes from reading code.
- **Tried:** no.
- **Licence:** proprietary (part of Claude Code).

## What it is

Claude Code's built-in undo: it snapshots the files its own edit tools touch and lets the user rewind code, conversation, or both, to any earlier prompt in the session.

## Capture, storage, restore

- **Capture:** "Claude Code tracks all changes made by its file editing tools". A checkpoint is taken before each prompt that starts a turn. **Only files edited within the current session are tracked.**
- **Storage:** file snapshots for the 100 most recent checkpoints in a session, saved with the conversation, so they survive `--resume`. They're deleted by the retention sweep about 30 days after the session last saved one (`cleanupPeriodDays`).
- **Restore:** the `/rewind` menu (or `Esc` twice) offers **Restore code and conversation**, **Restore conversation**, **Restore code**, and two summarize options. Restoring the conversation puts the original prompt back in the input.

## (a) `rm -rf` run through the shell

**Not covered.** "Checkpointing does not track files modified by Bash commands", with the example `rm file.txt`, `mv old.txt new.txt`, `cp source.txt dest.txt`: "These file modifications cannot be undone through rewind."

## (b) `git push`, then undo

**Not covered.** Rewind restores tracked files only; the docs say nothing about external effects. The push stays on the remote, and nothing marks it as irreversible.

## Limits (from the docs)

- **Shell commands:** changes made through Bash aren't tracked.
- **Subagents:** their edits are usually not restored, except a skill forked in the foreground. The docs say to "use git to revert them."
- **Your own edits:** "Manual changes you make to files outside of Claude Code … are normally not captured, unless they happen to modify the same files as the current session." Whether a code rewind then overwrites your edit in such a file isn't stated; **inferred:** it restores the snapshot over it, since there's no conflict check described.
- **Linked files:** symlinked and hard-linked files are skipped on restore, with a warning.
- **Retention:** snapshots expire with the session's retention period.
- "Not a replacement for version control."

## For AgentVC

- **Borrow:**
  - Turns as the unit of undo; AgentVC's turns come from `UserPromptSubmit`, plan §14.1.
  - Rewinding conversation and code separately; AgentVC's context sync after a rollback (plan §14.1) is the file-side half of this.
- **Differ:**
  - Shell and subagent changes are snapshotted (the gap `/rewind` documents).
  - Per-file compare-and-swap, so a rollback never overwrites the user's edits (G1).
  - History kept until AgentVC's own retention rules, not the session's.
  - External effects are reported.
- **Interplay to test:** unknown U-07 asks whether a `/rewind` restore fires any hook. If not, AgentVC sees `/rewind`'s writes as outside changes, which outside-change detection must treat as the user's (never rolled back).
