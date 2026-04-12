#!/usr/bin/env bash
set -euo pipefail

# security-scan.sh — ByteDigger Phase 0.5 security pattern scanner
# Scans modified files for security-sensitive patterns and classifies risk level.
# Always exits 0 (scan is informational, never blocks the pipeline).

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

CWD=""
FILES=""
STATE_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cwd)       CWD="$2";        shift 2 ;;
    --files)     FILES="$2";      shift 2 ;;
    --state-file) STATE_FILE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 0 ;;
  esac
done

# ---------------------------------------------------------------------------
# Pattern scanning
# ---------------------------------------------------------------------------

HAS_AUTH=0
HAS_CRYPTO=0
HAS_SECRETS=0
HAS_DATA=0
HAS_INFRA=0

if [[ -n "$FILES" ]]; then
  # Split comma-separated file list
  IFS=',' read -ra FILE_LIST <<< "$FILES"

  for f in "${FILE_LIST[@]}"; do
    # Trim whitespace
    f="${f#"${f%%[![:space:]]*}"}"
    f="${f%"${f##*[![:space:]]}"}"

    [[ -z "$f" ]] && continue
    [[ ! -f "$f" ]] && continue

    grep -qiE 'auth|login|jwt|oauth|session|rbac|password|credential' "$f" 2>/dev/null && HAS_AUTH=1
    grep -qiE 'encrypt|decrypt|hash|sign|crypto|key.*gen|certificate'  "$f" 2>/dev/null && HAS_CRYPTO=1
    grep -qiE 'api.key|secret|token|\.env|keychain|vault'              "$f" 2>/dev/null && HAS_SECRETS=1
    grep -qiE 'fetch|axios|request|query|insert|update|where|user.*input' "$f" 2>/dev/null && HAS_DATA=1
    grep -qiE 'Dockerfile|terraform|k8s|pipeline|deploy|helm'          "$f" 2>/dev/null && HAS_INFRA=1
  done
fi

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

CLASSIFICATION=""
PATTERNS_FOUND=()

if [[ $HAS_AUTH -eq 1 ]]; then
  CLASSIFICATION="HIGH"
  PATTERNS_FOUND+=("AUTH")
fi
if [[ $HAS_CRYPTO -eq 1 ]]; then
  CLASSIFICATION="HIGH"
  PATTERNS_FOUND+=("CRYPTO")
fi
if [[ $HAS_SECRETS -eq 1 ]]; then
  CLASSIFICATION="HIGH"
  PATTERNS_FOUND+=("SECRETS")
fi

if [[ -z "$CLASSIFICATION" ]]; then
  if [[ $HAS_DATA -eq 1 ]]; then
    CLASSIFICATION="MEDIUM"
    PATTERNS_FOUND+=("DATA")
  fi
fi

if [[ -z "$CLASSIFICATION" ]]; then
  # Fail-closed: if no files were provided/scanned, we can't confirm safety → MEDIUM
  if [[ -z "$FILES" ]]; then
    CLASSIFICATION="MEDIUM"
    PATTERNS_FOUND+=("unanalyzed")
  else
    CLASSIFICATION="LOW"
    [[ $HAS_INFRA -eq 1 ]] && PATTERNS_FOUND+=("INFRA")
  fi
fi

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

PATTERNS_STR="none"
if [[ ${#PATTERNS_FOUND[@]} -gt 0 ]]; then
  PATTERNS_STR="$(IFS=','; echo "${PATTERNS_FOUND[*]}")"
fi

echo "security_classification: $CLASSIFICATION"
echo "security_patterns_found: $PATTERNS_STR"

# ---------------------------------------------------------------------------
# State file update
# ---------------------------------------------------------------------------

if [[ -n "$STATE_FILE" && -f "$STATE_FILE" ]]; then
  # Remove any pre-existing keys before appending to avoid duplicates
  sed -i.bak '/^security_classification:/d; /^security_patterns_found:/d' "$STATE_FILE" && rm -f "${STATE_FILE}.bak"
  printf 'security_classification: %s\n' "$CLASSIFICATION" >> "$STATE_FILE"
  printf 'security_patterns_found: %s\n' "$PATTERNS_STR"   >> "$STATE_FILE"
fi

exit 0
