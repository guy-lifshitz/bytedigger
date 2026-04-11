#!/usr/bin/env bash
set -euo pipefail

# post-deploy.sh — Phase 8 cleanup (informational only, never blocks)
# Usage: post-deploy.sh --cwd <path> --state-file <path>

CWD=""
STATE_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cwd)        CWD="$2";        shift 2 ;;
    --state-file) STATE_FILE="$2"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ -z "$CWD" || -z "$STATE_FILE" ]]; then
  echo "[post-deploy] WARNING: --cwd or --state-file missing — skipping post-deploy steps" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# Step 3: Cleanup
# ---------------------------------------------------------------------------

echo "[post-deploy] Step 3: Cleanup"

# 3a: Prune remote refs
(cd "$CWD" && git fetch --prune) || true

# 3b: Delete gone branches
echo "[post-deploy] Checking for gone branches..."
GONE_BRANCHES=$(cd "$CWD" && git branch -vv | grep ': gone]' | awk '{print $1}') || true
if [[ -n "$GONE_BRANCHES" ]]; then
  while IFS= read -r branch; do
    echo "[post-deploy] Deleting gone branch: ${branch}"
    (cd "$CWD" && git branch -d "$branch") || true
  done <<< "$GONE_BRANCHES"
else
  echo "[post-deploy] No gone branches found"
fi

# 3c: Remove temp files older than 24h
echo "[post-deploy] Cleaning up .bytedigger-* temp files older than 24h..."
find "$CWD" -maxdepth 1 -name '.bytedigger-*' -mmin +1440 -exec rm -f {} \; || true

# 3d: Remove merged worktrees
echo "[post-deploy] Checking for merged worktrees..."
WORKTREE_LIST=$(cd "$CWD" && git worktree list) || true
if [[ -n "$WORKTREE_LIST" ]]; then
  PRIMARY_WORKTREE=$(echo "$WORKTREE_LIST" | awk 'NR==1{print $1}')
  while IFS= read -r line; do
    wt_path=$(echo "$line" | awk '{print $1}')
    wt_branch=$(echo "$line" | grep -o '\[.*\]' | tr -d '[]') || true

    [[ -z "$wt_branch" ]] && continue
    [[ "$wt_path" == "$PRIMARY_WORKTREE" ]] && continue

    case "$wt_branch" in
      main|master|develop) continue ;;
    esac

    if (cd "$CWD" && git branch --merged) | grep -qF "$wt_branch"; then
      echo "[post-deploy] Removing merged worktree: ${wt_path} (${wt_branch})"
      (cd "$CWD" && git worktree remove "$wt_path") || true
    fi
  done <<< "$WORKTREE_LIST"
fi

echo "phase_8_cleanup: complete" >> "$STATE_FILE"
echo "[post-deploy] cleanup = complete"

echo "[post-deploy] Phase 8 complete."
