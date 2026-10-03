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
TASK=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cwd)        CWD="${2:-}";        shift $(( $# >= 2 ? 2 : 1 )) ;;
    --files)      FILES="${2:-}";      shift $(( $# >= 2 ? 2 : 1 )) ;;
    --state-file) STATE_FILE="${2:-}"; shift $(( $# >= 2 ? 2 : 1 )) ;;
    --task)       TASK="${2:-}";       shift $(( $# >= 2 ? 2 : 1 )) ;;
    *) echo "Unknown argument: $1" >&2; exit 0 ;;
  esac
done

# ---------------------------------------------------------------------------
# Pattern scanning
# ---------------------------------------------------------------------------

# Kill switch (owner: bytedigger; remove once default mode has shipped one
# release): exactly "1" restores the old substring patterns, ignores --task
# and does not scan path strings.
LEGACY=0
[[ "${BD_SECURITY_SCAN_LEGACY:-}" == "1" ]] && LEGACY=1

# Bound each term by a non-alphanumeric character or line edge (portable ERE).
bound() { printf '(^|[^[:alnum:]])(%s)([^[:alnum:]]|$)' "$1"; }

if [[ $LEGACY -eq 1 ]]; then
  TASK=""
  PAT_AUTH='auth|login|jwt|oauth|session|rbac|password|credential'
  PAT_CRYPTO='encrypt|decrypt|hash|sign|crypto|key.*gen|certificate'
  PAT_SECRETS='api.key|secret|token|\.env|keychain|vault'
else
  PAT_AUTH="$(bound 'auth|authenticat[[:alnum:]_]*|authoriz[[:alnum:]_]*|login|jwt|oauth2?|saml|rbac|passw(or)?d|credentials?')"
  PAT_CRYPTO="$(bound 'encrypt[[:alnum:]_]*|decrypt[[:alnum:]_]*|crypto[[:alnum:]_]*|cipher|hmac|bcrypt|argon2|pbkdf2|(private|public)[_-]?key|certificate')"
  PAT_SECRETS="$(bound 'api[_-]?key|secrets?|(access|auth|bearer)[_-]?token|keychain|vault')|(^|[[:space:]\"'/(=])\\.env([^[:alnum:]]|\$)"
fi
PAT_DATA='fetch|axios|request|query|insert|update|where|user.*input'
PAT_INFRA='Dockerfile|terraform|k8s|pipeline|deploy|helm'

HAS_AUTH=0
HAS_CRYPTO=0
HAS_SECRETS=0
HAS_DATA=0
HAS_INFRA=0
TRIG_AUTH=""
TRIG_CRYPTO=""
TRIG_SECRETS=""

# note_hit CATEGORY LABEL: mark the category, keep the first label seen.
note_hit() {
  case "$1" in
    AUTH)    HAS_AUTH=1;    [[ -z "$TRIG_AUTH" ]]    && TRIG_AUTH="$2" ;;
    CRYPTO)  HAS_CRYPTO=1;  [[ -z "$TRIG_CRYPTO" ]]  && TRIG_CRYPTO="$2" ;;
    SECRETS) HAS_SECRETS=1; [[ -z "$TRIG_SECRETS" ]] && TRIG_SECRETS="$2" ;;
    DATA)    HAS_DATA=1 ;;
    INFRA)   HAS_INFRA=1 ;;
  esac
  return 0
}

# scan_file FILE: classify file content; label is <file>:<line>.
scan_file() {
  local file="$1" cat pat out
  for cat in AUTH CRYPTO SECRETS DATA INFRA; do
    eval "pat=\$PAT_$cat"
    out="$(grep -m1 -n -a -iE -e "$pat" -- "$file" 2>/dev/null || true)"
    [[ -n "$out" ]] && note_hit "$cat" "$file:${out%%:*}"
  done
  return 0
}

# scan_text LABEL TEXT WITH_DATA_INFRA: classify a string (task text or a path).
scan_text() {
  local label="$1" text="$2" all="$3" cat pat out
  for cat in AUTH CRYPTO SECRETS DATA INFRA; do
    [[ "$all" != "1" && ( "$cat" == "DATA" || "$cat" == "INFRA" ) ]] && continue
    eval "pat=\$PAT_$cat"
    out="$(printf '%s\n' "$text" | grep -m1 -I -iE -e "$pat" 2>/dev/null || true)"
    [[ -n "$out" ]] && note_hit "$cat" "$label"
  done
  return 0
}

[[ -n "$TASK" ]] && scan_text "task" "$TASK" 1

if [[ -n "$FILES" ]]; then
  # Split comma-separated file list
  IFS=',' read -ra FILE_LIST <<< "$FILES"

  for f in "${FILE_LIST[@]}"; do
    # Trim whitespace
    f="${f#"${f%%[![:space:]]*}"}"
    f="${f%"${f##*[![:space:]]}"}"

    [[ -z "$f" ]] && continue
    # A planned file that does not exist yet still counts by its path.
    [[ $LEGACY -eq 0 ]] && scan_text "$f" "$f" 0
    [[ ! -f "$f" ]] && continue

    scan_file "$f"
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
  if [[ -z "$FILES" && -z "$TASK" ]]; then
    CLASSIFICATION="MEDIUM"
    PATTERNS_FOUND+=("unanalyzed")
  else
    CLASSIFICATION="LOW"
    if [[ $HAS_INFRA -eq 1 ]]; then
      PATTERNS_FOUND+=("INFRA")
    elif [[ -z "$FILES" ]]; then
      PATTERNS_FOUND+=("task_only")  # no file was scanned
    fi
  fi
fi

TRIGGERS=()
[[ -n "$TRIG_AUTH" ]]    && TRIGGERS+=("AUTH=$TRIG_AUTH")
[[ -n "$TRIG_CRYPTO" ]]  && TRIGGERS+=("CRYPTO=$TRIG_CRYPTO")
[[ -n "$TRIG_SECRETS" ]] && TRIGGERS+=("SECRETS=$TRIG_SECRETS")
TRIGGERS_STR="[]"
if [[ ${#TRIGGERS[@]} -gt 0 ]]; then
  TRIGGERS_STR="[$(IFS=','; printf '%s' "${TRIGGERS[*]}" | sed 's/,/, /g')]"
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
echo "security_triggers: $TRIGGERS_STR"

# ---------------------------------------------------------------------------
# State file update
# ---------------------------------------------------------------------------

if [[ -n "$STATE_FILE" && -f "$STATE_FILE" ]]; then
  # Remove any pre-existing keys before appending to avoid duplicates
  sed -i.bak '/^security_classification:/d; /^security_patterns_found:/d; /^security_triggers:/d' "$STATE_FILE" && rm -f "${STATE_FILE}.bak"
  printf 'security_classification: %s\n' "$CLASSIFICATION" >> "$STATE_FILE"
  printf 'security_patterns_found: %s\n' "$PATTERNS_STR"   >> "$STATE_FILE"
  printf 'security_triggers: %s\n' "$TRIGGERS_STR"         >> "$STATE_FILE"
fi

exit 0
