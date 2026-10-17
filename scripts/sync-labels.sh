#!/usr/bin/env bash
# Create or update all AgentVC issue labels. Safe to run repeatedly.
# Requires the GitHub CLI, authenticated with access to the repository:
#   gh auth login
#   scripts/sync-labels.sh Jay2Kumar1Sharma/AgentVC
set -euo pipefail

REPO="${1:?usage: scripts/sync-labels.sh <owner>/<repo>}"

label() { gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force; }

# Type
label "bug"               "d73a4a" "Something AgentVC does wrong"
label "feature"           "a2eeef" "New capability"
label "docs"              "0075ca" "Documentation"
label "test"              "c5def5" "Tests only"
label "adapter"           "5319e7" "Support for a coding agent"
label "contract"          "7057ff" "Effect contract (recognising or undoing a command/API)"
label "question"          "d876e3" "Question"

# Severity
label "severity:S0"       "b60205" "Guarantee broken in a harmful way: stop the line"
label "severity:S1"       "d93f0b" "Guarantee at risk or core flow fails: blocks release"
label "severity:S2"       "fbca04" "Wrong or confusing result, with a workaround"
label "severity:S3"       "c2e0c6" "Cosmetic or minor"

# Guarantees
label "guarantee:G1"      "0e8a16" "Never destroy user work"
label "guarantee:G2"      "0e8a16" "Nothing silent"
label "guarantee:G3"      "0e8a16" "Honest accounting"
label "guarantee:G4"      "0e8a16" "Never repeat the world"
label "guarantee:G5"      "0e8a16" "Nothing leaves early"
label "guarantee:G6"      "0e8a16" "Local and private"
label "guarantee:G7"      "0e8a16" "Cheap enough to leave on"
label "guarantee:G8"      "0e8a16" "Can't be silently switched off"

# Components
for c in capture snapshot restore ledger classifier fence outbox mcp-proxy cli report architecture docs; do
  label "component:$c"    "bfd4f2" "Component: $c"
done

# Platforms and agents
label "os:windows"        "1d76db" "Windows"
label "os:macos"          "1d76db" "macOS"
label "os:linux"          "1d76db" "Linux"
label "agent:claude-code" "006b75" "Claude Code"
label "agent:codex"       "006b75" "Codex CLI"

# State
label "needs-triage"      "ededed" "Not yet triaged"
label "needs-repro"       "ededed" "Needs a reproduction"
label "needs-design"      "ededed" "Needs a design note or ADR first"
label "accepted"          "0e8a16" "Accepted and scheduled"
label "blocked"           "000000" "Blocked by something else"
label "wontfix"           "ffffff" "Will not be done (reason given)"
label "duplicate"         "cfd3d7" "Duplicate of another issue"

# Source
label "found-during-dev"  "f9d0c4" "Unrelated problem found while working on something else"
label "from-redteam"      "f9d0c4" "Found by a red-team session"
label "from-fuzzing"      "f9d0c4" "Found by fuzzing or nightly tests"
label "from-user"         "f9d0c4" "Reported by a user"

# Help
label "good first issue"  "7057ff" "Good for newcomers"
label "help wanted"       "008672" "Extra attention is needed"

echo "Labels synced for $REPO"
