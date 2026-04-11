#!/usr/bin/env bash
# ship.sh — ByteDigger SHIP protocol
# Commits, pushes, and opens a PR when --pr flag is passed.
# Usage: ship.sh [--pr] [--config <path>] [--state <path>]

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

PR_FLAG=false
CONFIG_PATH=""
STATE_PATH="build-state.yaml"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pr)
      PR_FLAG=true
      shift
      ;;
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --state)
      STATE_PATH="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

# Without --pr, exit immediately — no git operations
if [[ "$PR_FLAG" != "true" ]]; then
  exit 0
fi

# ---------------------------------------------------------------------------
# Read build-state.yaml
# ---------------------------------------------------------------------------

if [[ ! -f "$STATE_PATH" ]]; then
  echo "ERROR: state file not found: $STATE_PATH" >&2
  exit 1
fi

# Extract task field — preserve colons in value by stripping only the key prefix
TASK=$(sed -n 's/^task:[[:space:]]*//p' "$STATE_PATH" | sed 's/^["\x27]\(.*\)["\x27]$/\1/')

# Guard: empty task means branch name would be invalid
if [[ -z "$TASK" ]]; then
  echo "WARNING: task field is empty in state file — using 'unnamed-build'" >&2
  TASK="unnamed-build"
fi

# Extract files_modified list using awk (lines starting with "  - ")
FILES_MODIFIED=$(awk '/^files_modified:/{found=1; next} found && /^  - /{sub(/^  - /, ""); print; next} found{found=0}' "$STATE_PATH")

# ---------------------------------------------------------------------------
# Sensitive file exclusion
# ---------------------------------------------------------------------------

_is_sensitive() {
  local f="$1"
  local base
  base=$(basename "$f")
  # Check the full path for directory patterns first
  case "$f" in
    node_modules/*) return 0 ;;
    .hal-build/*)   return 0 ;;
  esac
  # Check the basename for file patterns (handles nested paths like config/.env)
  case "$base" in
    .env|.env.*)        return 0 ;;
    *.env|*.env.*)      return 0 ;;
    *.pem|*.key)        return 0 ;;
    *.credentials*)     return 0 ;;
  esac
  return 1
}

# ---------------------------------------------------------------------------
# Branch management
# ---------------------------------------------------------------------------

CURRENT_BRANCH=$(git branch --show-current)

if [[ "$CURRENT_BRANCH" == "main" || "$CURRENT_BRANCH" == "master" ]]; then
  # Build slug: lowercase, non-alphanum→hyphens, collapse hyphens, max 50 chars
  SLUG=$(printf '%s' "$TASK" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//' | cut -c1-50)
  BRANCH="feat/${SLUG}"
  git checkout -b "$BRANCH"
  CURRENT_BRANCH="$BRANCH"
fi

# ---------------------------------------------------------------------------
# Stage files (skip sensitive)
# ---------------------------------------------------------------------------

while IFS= read -r file; do
  [[ -z "$file" ]] && continue
  if _is_sensitive "$file"; then
    echo "SKIP (sensitive): $file"
    continue
  fi
  git add "$file"
done <<< "$FILES_MODIFIED"

# Guard: if nothing was staged (all files were sensitive), skip commit gracefully
if git diff --cached --quiet; then
  echo "WARNING: No files to commit (all excluded as sensitive)" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

git commit -m "$TASK"

# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------

git push -u origin "$CURRENT_BRANCH"

# ---------------------------------------------------------------------------
# PR creation (best-effort)
# ---------------------------------------------------------------------------

PR_URL=""

# PR creation is best-effort: gh may be absent, may fail auth, may fail network
if command -v gh &>/dev/null; then
  PR_URL=$(gh pr create --title "$TASK" --body "Built via ByteDigger /build pipeline." 2>/dev/null) || {
    echo "WARNING: gh pr create failed — skipping PR creation. Push complete, open a PR manually." >&2
    PR_URL=""
  }
else
  echo "WARNING: gh CLI not found — skipping PR creation. Push complete, open a PR manually." >&2
fi

# ---------------------------------------------------------------------------
# Update build-state.yaml with ship results
# ---------------------------------------------------------------------------

# Remove any pre-existing ship fields, then append new values
TMPFILE=$(mktemp)
trap "rm -f '$TMPFILE'" EXIT
grep -v '^ship_complete:' "$STATE_PATH" | grep -v '^ship_pr_url:' > "$TMPFILE"
printf 'ship_complete: true\n' >> "$TMPFILE"
printf 'ship_pr_url: %s\n' "$PR_URL" >> "$TMPFILE"
mv "$TMPFILE" "$STATE_PATH"

exit 0
