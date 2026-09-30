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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# One gh name everywhere: HAL_GH_BIN > BD_GH_BIN > BYTEDIGGER_GH_BIN > gh
GH_BIN="${HAL_GH_BIN:-${BD_GH_BIN:-${BYTEDIGGER_GH_BIN:-gh}}}"

# ---------------------------------------------------------------------------
# Refusals (bd#131) — read-only, before the readiness check so a refusal does
# not consume an approval
# ---------------------------------------------------------------------------

CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || true)
if [[ -z "$CURRENT_BRANCH" ]]; then
  echo "ERROR: detached HEAD — nothing shipped" >&2
  exit 1
fi
# A merge/rebase conflict: discovery's `git add -A` would mark the files resolved
if [[ -n "$(git ls-files -u 2>/dev/null)" ]]; then
  echo "ERROR: unmerged paths — nothing shipped" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Readiness gate (bd#117) — one call, before any git mutation
# ---------------------------------------------------------------------------
# `readiness check --stage ship` decides AND consumes the approval. Exit 3 is a
# refusal, 4 is "unavailable"; a crash or a missing python3 (any other code,
# 127 included) is mapped to 4. Its stderr is forwarded as-is. Its stdout (the
# --json verdict) is captured and not read: the push rule no longer depends on it.

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

# Tracked changes (modified or deleted) not listed in files_modified ship too (bd#131).
# NUL-delimited and unquoted, so any path is safe. Untracked files are never
# discovered, only staged when listed above. --literal-pathspecs: no glob/magic.
while IFS= read -r -d '' file; do
  [[ -z "$file" ]] && continue
  if _is_sensitive "$file"; then
    echo "SKIP (sensitive): $file"
    continue
  fi
  echo "STAGE (tracked change): $file"
  git --literal-pathspecs add -A -- "$file"
done < <(git -c core.quotePath=false diff --name-only -z)

# Untracked files that are not staged stay out of the PR; say so once (BD's own
# leftovers, build-* and .bytedigger*, are not worth a warning).
UNTRACKED_N=0
UNTRACKED_LIST=""
while IFS= read -r -d '' file; do
  case "${file##*/}" in
    build-*|.bytedigger*) continue ;;
  esac
  case "/$file" in
    */.bytedigger*/*) continue ;;
  esac
  UNTRACKED_N=$((UNTRACKED_N + 1))
  if [[ "$UNTRACKED_N" -le 20 ]]; then
    UNTRACKED_LIST="${UNTRACKED_LIST:+$UNTRACKED_LIST, }$file"
  fi
done < <(git -c core.quotePath=false ls-files --others --exclude-standard -z)
if [[ "$UNTRACKED_N" -gt 0 ]]; then
  if [[ "$UNTRACKED_N" -gt 20 ]]; then
    UNTRACKED_LIST="$UNTRACKED_LIST, … (+$((UNTRACKED_N - 20)) more)"
  fi
  echo "WARNING: untracked files not shipped: $UNTRACKED_LIST" >&2
fi

# Push rule (every readiness mode): commits already made but not yet pushed ship.
# Base = @{upstream}, else refs/bd/policy (the push target's default branch
# readiness just fetched). A non-numeric count is 0.
BASE_REF=""
if git rev-parse --verify -q '@{upstream}' >/dev/null 2>&1; then
  BASE_REF='@{upstream}'
elif git rev-parse --verify -q 'refs/bd/policy' >/dev/null 2>&1; then
  BASE_REF='refs/bd/policy'
fi
AHEAD_COUNT=0
if [[ -n "$BASE_REF" ]]; then
  AHEAD_COUNT=$(git rev-list --count "$BASE_REF..HEAD" 2>/dev/null || true)
  case "$AHEAD_COUNT" in
    ''|*[!0-9]*) AHEAD_COUNT=0 ;;
  esac
fi

NOTHING_STAGED=false
if git diff --cached --quiet; then
  NOTHING_STAGED=true
fi

# Guard: nothing staged and nothing ahead -> nothing to ship. Exit 0 before any
# push, PR or state write, so Phase 7 sees no ship_complete.
if [[ "$NOTHING_STAGED" == "true" && "$AHEAD_COUNT" -eq 0 ]]; then
  echo "WARNING: nothing to ship — no changes to commit and HEAD is not ahead of ${BASE_REF:-any base (none found)}" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# PR text (bd#131) — scripts/ship_pr_text.py never fails the ship: each call is
# checked on its own, a bad result falls back to the task string / two-line
# body, with at most one warning per run. Helper stderr is discarded.
# ---------------------------------------------------------------------------

HELPER="$SCRIPT_DIR/ship_pr_text.py"
HELPER_WARNED=false
_helper_fallback() {
  if [[ "$HELPER_WARNED" != "true" ]]; then
    echo "WARNING: ship_pr_text failed — using the task string" >&2
    HELPER_WARNED=true
  fi
}

TITLE_ARGS=(title --state "$STATE_PATH")
if [[ -n "$BASE_REF" ]]; then
  TITLE_ARGS+=(--base "$BASE_REF")
fi
TITLE=""
if TITLE=$(python3 "$HELPER" "${TITLE_ARGS[@]}" 2>/dev/null); then
  TITLE="${TITLE%%$'\n'*}"
else
  TITLE=""
fi
if [[ -z "$TITLE" ]]; then
  TITLE="$TASK"
  _helper_fallback
fi

# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

if [[ "$NOTHING_STAGED" != "true" ]]; then
  if [[ "$TASK" != "$TITLE" ]]; then
    git commit -m "$TITLE" -m "$TASK" --
  else
    git commit -m "$TITLE" --
  fi
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
  # Same literal as readiness.BUILT_MARKER in engine_py/bytedigger_engine/readiness.py.
  # The helper's body must end with that exact line, else the two-line body is used.
  PR_BODY_FALLBACK=$'Built via ByteDigger /build pipeline.\n<!-- bd:built -->'
  PR_BODY=""
  if PR_BODY=$(python3 "$HELPER" body --state "$STATE_PATH" 2>/dev/null) \
    && [[ "${PR_BODY##*$'\n'}" == "<!-- bd:built -->" ]]; then
    :
  else
    PR_BODY="$PR_BODY_FALLBACK"
    _helper_fallback
  fi
  PR_URL=$("$GH_BIN" pr create --title "$TITLE" --body "$PR_BODY" 2>/dev/null) || {
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
