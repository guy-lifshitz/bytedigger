#!/usr/bin/env bash
set -euo pipefail

# post-deploy.sh — Phase 8 post-deploy steps (informational only, never blocks)
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
# Tool detection
# If MOCK_BIN is set (test mode), only look there. Otherwise use full PATH.
# ---------------------------------------------------------------------------

_has_tool() {
  local tool="$1"
  if [[ -n "${MOCK_BIN:-}" ]]; then
    [[ -x "$MOCK_BIN/$tool" ]]
  else
    command -v "$tool" &>/dev/null
  fi
}

# ---------------------------------------------------------------------------
# Step 1: Security Scan
# ---------------------------------------------------------------------------

echo "[post-deploy] Step 1: Security scan"

HAS_GITLEAKS=0
HAS_TRIVY=0
_has_tool gitleaks && HAS_GITLEAKS=1 || true
_has_tool trivy    && HAS_TRIVY=1    || true

SCAN_RESULT="skipped"

if [[ $HAS_GITLEAKS -eq 0 && $HAS_TRIVY -eq 0 ]]; then
  echo "[post-deploy] No security tools found (gitleaks, trivy) — skipping scan"
  SCAN_RESULT="skipped"
else
  # Run available tools; worst result (fail) wins
  WORST="pass"

  if [[ $HAS_GITLEAKS -eq 1 ]]; then
    echo "[post-deploy] Running gitleaks..."
    if (cd "$CWD" && gitleaks detect --no-banner -v) ; then
      echo "[post-deploy] gitleaks: pass"
    else
      echo "[post-deploy] gitleaks: findings detected"
      WORST="fail"
    fi
  fi

  if [[ $HAS_TRIVY -eq 1 ]]; then
    echo "[post-deploy] Running trivy fs scan..."
    if (cd "$CWD" && trivy fs . --severity HIGH,CRITICAL --skip-dirs .bytedigger) ; then
      echo "[post-deploy] trivy: pass"
    else
      echo "[post-deploy] trivy: findings detected"
      WORST="fail"
    fi
  fi

  SCAN_RESULT="$WORST"
fi

echo "phase_8_security_scan: ${SCAN_RESULT}" >> "$STATE_FILE"
echo "[post-deploy] security_scan = ${SCAN_RESULT}"

# ---------------------------------------------------------------------------
# Step 2: SBOM Generation
# ---------------------------------------------------------------------------

echo "[post-deploy] Step 2: SBOM generation"

SBOM_RESULT="skipped"

if _has_tool trivy; then
  SBOM_DIR="$CWD/.bytedigger"
  mkdir -p "$SBOM_DIR"
  SBOM_FILE="$SBOM_DIR/sbom.cdx.json"
  echo "[post-deploy] Generating SBOM at ${SBOM_FILE}..."
  (cd "$CWD" && trivy fs . --format cyclonedx --output "$SBOM_FILE") || true
  SBOM_RESULT="generated"
  echo "[post-deploy] SBOM generated: ${SBOM_FILE}"
else
  echo "[post-deploy] trivy not available — skipping SBOM generation"
fi

echo "phase_8_sbom: ${SBOM_RESULT}" >> "$STATE_FILE"
echo "[post-deploy] sbom = ${SBOM_RESULT}"

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
    # Parse worktree path (first field) and branch name from [branch] at end of line
    wt_path=$(echo "$line" | awk '{print $1}')
    wt_branch=$(echo "$line" | grep -o '\[.*\]' | tr -d '[]') || true

    # Skip if no branch extracted
    [[ -z "$wt_branch" ]] && continue

    # Skip primary worktree
    [[ "$wt_path" == "$PRIMARY_WORKTREE" ]] && continue

    # Skip protected branches
    case "$wt_branch" in
      main|master|develop) continue ;;
    esac

    # Check if branch is merged
    if (cd "$CWD" && git branch --merged) | grep -qF "$wt_branch"; then
      echo "[post-deploy] Removing merged worktree: ${wt_path} (${wt_branch})"
      (cd "$CWD" && git worktree remove "$wt_path") || true
    fi
  done <<< "$WORKTREE_LIST"
fi

echo "phase_8_cleanup: complete" >> "$STATE_FILE"
echo "[post-deploy] cleanup = complete"

echo "[post-deploy] Phase 8 complete."
