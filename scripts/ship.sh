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
# Readiness gate (bd#117) — one call, before any git mutation
# ---------------------------------------------------------------------------
# `readiness check --stage ship` decides AND consumes the approval. Exit 3 is a
# refusal, 4 is "unavailable"; a crash or a missing python3 (any other code,
# 127 included) is mapped to 4. Its stderr is forwarded as-is.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# One gh name everywhere: HAL_GH_BIN > BD_GH_BIN > BYTEDIGGER_GH_BIN > gh
GH_BIN="${HAL_GH_BIN:-${BD_GH_BIN:-${BYTEDIGGER_GH_BIN:-gh}}}"

READINESS_RC=0
READINESS_OUT=$(bash "$SCRIPT_DIR/readiness" check --stage ship --json) || READINESS_RC=$?
case "$READINESS_RC" in
  0) ;;
  3) exit 3 ;;
  *)
    echo "ERROR: readiness check unavailable (exit $READINESS_RC) — nothing shipped" >&2
    exit 4
    ;;
esac

READINESS_REQUIRED=false
if printf '%s' "$READINESS_OUT" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("required") is True else 1)' 2>/dev/null; then
  READINESS_REQUIRED=true
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

# Validate TASK does not contain newlines or backticks (injection prevention)
if printf '%s' "$TASK" | grep -qE $'[\n`]'; then
  printf '%s\n' "invalid task string: contains newline or backtick" >&2
  exit 1
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
    .bytedigger/*)  return 0 ;;  # scratchpad dir used by ByteDigger pipeline
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

# Resume rule (only under readiness required:true): commits already made but not
# yet pushed still ship. Base = @{upstream}, else refs/bd/policy (the push
# target's default branch readiness just fetched). A non-numeric count is 0.
_ahead_of_base() {
  local base="refs/bd/policy" count
  if git rev-parse --verify -q '@{upstream}' >/dev/null 2>&1; then
    base='@{upstream}'
  fi
  count=$(git rev-list --count "$base..HEAD" 2>/dev/null || true)
  case "$count" in
    ''|*[!0-9]*) count=0 ;;
  esac
  [[ "$count" -gt 0 ]]
}

NOTHING_STAGED=false
if git diff --cached --quiet; then
  NOTHING_STAGED=true
fi

# Guard: if nothing was staged (all files were sensitive), skip commit gracefully —
# unless the resume rule applies (required:true and commits are ahead of the base).
if [[ "$NOTHING_STAGED" == "true" ]]; then
  RESUME=false
  if [[ "$READINESS_REQUIRED" == "true" ]] && _ahead_of_base; then
    RESUME=true
  fi
  if [[ "$RESUME" != "true" ]]; then
    echo "WARNING: No files to commit (all excluded as sensitive)" >&2
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

if [[ "$NOTHING_STAGED" != "true" ]]; then
  git commit -m "$TASK" --
fi

# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------

git push -u origin "$CURRENT_BRANCH"

# ---------------------------------------------------------------------------
# PR creation (best-effort)
# ---------------------------------------------------------------------------

PR_URL=""

# PR creation is best-effort: gh may be absent, may fail auth, may fail network
if command -v "$GH_BIN" &>/dev/null; then
  # The last body line is the provenance marker (bd#117 op-B1): the companion tuner only trusts it on a BD-authored PR.
  PR_BODY=$'Built via ByteDigger /build pipeline.\n<!-- bd:built -->'
  PR_URL=$("$GH_BIN" pr create --title "$TASK" --body "$PR_BODY" 2>/dev/null) || {
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
