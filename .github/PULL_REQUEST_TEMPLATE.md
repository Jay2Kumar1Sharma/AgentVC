## What and why

<!-- What does this change do, and why? Link the issue: "Fixes #123" / "Part of #123". -->

## Type

- [ ] Bug fix
- [ ] Feature
- [ ] Agent adapter
- [ ] Effect contract
- [ ] Docs
- [ ] Tests only
- [ ] Refactor / chore

## Guarantees touched

<!-- Which of G1–G8 could this change affect, and how is each protected? Write "none" if none. -->

## How it was tested

<!-- List the tests added or changed. For each new test: confirm it was seen to FAIL before the change. -->

- [ ] New tests were seen to fail before the change, then pass
- [ ] Adversarial tests added (required for restore, snapshot, ledger, capture, classifier, fence, outbox changes)
- [ ] `tests/traceability.yml`, `docs/testing/fmea.md` / `hazards.md` updated (if relevant)
- [ ] `make test` passes locally
- [ ] Tested on: <!-- Windows / macOS / Linux, and which agent -->

## Tests changed or removed

<!-- If any existing test was weakened, skipped or removed, explain why the old test was wrong. Otherwise write "none". -->

## Checklist

- [ ] One topic only; unrelated changes are in separate PRs
- [ ] Docs, `--help` and `CHANGELOG.md` ("Unreleased") updated
- [ ] `architecture/graph.json` updated and `make arch` passes, or not needed because: <!-- reason -->
- [ ] No new runtime dependency (or an ADR is linked)
- [ ] Commits are small and single-purpose, each passing the fast lane (no single "everything" commit)
- [ ] Commits are signed (Verified on GitHub) and their messages contain no email addresses (no `Signed-off-by` or `Co-Authored-By` trailers)
- [ ] If an AI agent helped: which one, and I have reviewed every line: <!-- e.g. Claude Code -->

## Unrelated issues found

<!-- Problems you noticed that are NOT part of this change. Each one must have a GitHub issue (use the "Found during development" template). Write "none" if none. -->

- #
