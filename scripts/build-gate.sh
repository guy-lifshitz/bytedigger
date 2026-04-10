#!/bin/bash
# build-gate.sh — ByteDigger build gate enforcement
# Checks pre-commit conditions at each pipeline phase.

set -euo pipefail

# ---------------------------------------------------------------------------
# Section 1: drain_stdin
# ---------------------------------------------------------------------------
drain_stdin() {
  while IFS= read -r -t 1 _line 2>/dev/null; do :; done
}

# ---------------------------------------------------------------------------
# Section 2: load_config
# ---------------------------------------------------------------------------
load_config() {
  # Determine config file location
  local config_file=""
  if [ -n "${BYTEDIGGER_CONFIG:-}" ]; then
    config_file="$BYTEDIGGER_CONFIG"
    # Derive CWD from config location
    CWD="$(dirname "$config_file")"
  elif [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    config_file="$CLAUDE_PLUGIN_ROOT/bytedigger.json"
    CWD="$(pwd)"
  else
    # Resolve from script's parent dir
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    config_file="$script_dir/../bytedigger.json"
    CWD="$(pwd)"
  fi

  # Defaults
  GATES_ENABLED=true
  TDD_MANDATORY=true
  SIMPLE_REVIEWERS=3
  FEATURE_REVIEWERS=6
  COMPLEX_REVIEWERS=6

  if [ ! -f "$config_file" ]; then
    # Missing config → use defaults (gates ON)
    return 0
  fi

  # Read config via python3
  local config_values
  config_values=$(python3 - "$config_file" <<'PYEOF' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1]) as f:
        c = json.load(f)
    gates = str(c.get('gates_enabled', True)).lower()
    tdd   = str(c.get('tdd_mandatory', True)).lower()
    sr    = str(c.get('simple_reviewers', 3))
    fr    = str(c.get('feature_reviewers', 6))
    cr    = str(c.get('complex_reviewers', 6))
    print(f"GATES_ENABLED_RAW={gates}")
    print(f"TDD_MANDATORY_RAW={tdd}")
    print(f"SIMPLE_REVIEWERS={sr}")
    print(f"FEATURE_REVIEWERS={fr}")
    print(f"COMPLEX_REVIEWERS={cr}")
except Exception as e:
    pass
PYEOF
) || true

  if [ -n "$config_values" ]; then
    local ge tdd_raw
    ge=$(echo "$config_values" | grep "^GATES_ENABLED_RAW=" | cut -d= -f2)
    tdd_raw=$(echo "$config_values" | grep "^TDD_MANDATORY_RAW=" | cut -d= -f2)
    local sr fr cr
    sr=$(echo "$config_values" | grep "^SIMPLE_REVIEWERS=" | cut -d= -f2)
    fr=$(echo "$config_values" | grep "^FEATURE_REVIEWERS=" | cut -d= -f2)
    cr=$(echo "$config_values" | grep "^COMPLEX_REVIEWERS=" | cut -d= -f2)

    [ "$ge" = "false" ] && GATES_ENABLED=false
    [ "$tdd_raw" = "false" ] && TDD_MANDATORY=false
    [ -n "$sr" ] && SIMPLE_REVIEWERS="$sr"
    [ -n "$fr" ] && FEATURE_REVIEWERS="$fr"
    [ -n "$cr" ] && COMPLEX_REVIEWERS="$cr"
  else
    # Fallback: grep-based parsing
    local ge_grep
    ge_grep=$(grep -o '"gates_enabled"[[:space:]]*:[[:space:]]*[a-z]*' "$config_file" 2>/dev/null | grep -o '[a-z]*$' || true)
    [ "$ge_grep" = "false" ] && GATES_ENABLED=false
  fi

  if [ "$GATES_ENABLED" = "false" ]; then
    exit 0
  fi
}

# ---------------------------------------------------------------------------
# Section 3: load_state
# ---------------------------------------------------------------------------
load_state() {
  BUILD_STATE="$CWD/build-state.yaml"

  if [ ! -f "$BUILD_STATE" ]; then
    exit 0  # Not a build session
  fi

  # Stale check: mtime > 600s → exit 0
  local now mtime age
  now=$(date +%s)
  # macOS stat
  mtime=$(stat -f %m "$BUILD_STATE" 2>/dev/null || stat -c %Y "$BUILD_STATE" 2>/dev/null || echo "0")
  age=$((now - mtime))
  if [ "$age" -gt 600 ]; then
    exit 0
  fi

  # Read CURRENT_PHASE
  CURRENT_PHASE=$(grep "^current_phase:" "$BUILD_STATE" | sed 's/^current_phase:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')

  if [ -z "$CURRENT_PHASE" ]; then
    echo "WARN: build-state.yaml has no current_phase — skipping gate" >&2
    exit 0
  fi

  # Read complexity from build-metadata.json (TRUSTED_COMPLEXITY)
  local metadata_file="$CWD/build-metadata.json"
  TRUSTED_COMPLEXITY=""
  if [ -f "$metadata_file" ]; then
    TRUSTED_COMPLEXITY=$(python3 - "$metadata_file" <<'PYEOF' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
    print(d.get('complexity', ''))
except Exception:
    pass
PYEOF
) || true
    if [ -z "$TRUSTED_COMPLEXITY" ]; then
      # Grep fallback
      TRUSTED_COMPLEXITY=$(grep -o '"complexity"[[:space:]]*:[[:space:]]*"[A-Z]*"' "$metadata_file" 2>/dev/null | grep -o '"[A-Z]*"$' | tr -d '"' || true)
    fi
  fi

  # Read complexity from yaml
  local yaml_complexity
  yaml_complexity=$(grep "^complexity:" "$BUILD_STATE" | sed 's/^complexity:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')

  # Complexity downgrade detection: metadata != yaml → hard_block
  if [ -n "$TRUSTED_COMPLEXITY" ] && [ -n "$yaml_complexity" ] && [ "$TRUSTED_COMPLEXITY" != "$yaml_complexity" ]; then
    hard_block "complexity downgrade detected: metadata=$TRUSTED_COMPLEXITY yaml=$yaml_complexity"
  fi

  # Set final complexity
  if [ -n "$TRUSTED_COMPLEXITY" ]; then
    COMPLEXITY="$TRUSTED_COMPLEXITY"
  else
    COMPLEXITY="$yaml_complexity"
  fi
}

# ---------------------------------------------------------------------------
# Section 4: Helpers
# ---------------------------------------------------------------------------

yaml_field_equals() {
  local field="$1"
  local expected="$2"
  local value
  value=$(grep "^${field}:" "$BUILD_STATE" | sed "s/^${field}:[[:space:]]*//" | tr -d '"' | tr -d "'" | tr -d ' ')
  if [ "$value" != "$expected" ]; then
    MISSING_FIELDS+=("${field}=${expected} (got: ${value:-<missing>})")
    return 1
  fi
  return 0
}

yaml_key_has_value() {
  local field="$1"
  local value
  value=$(grep "^${field}:" "$BUILD_STATE" | sed "s/^${field}:[[:space:]]*//" | tr -d '"' | tr -d "'" | tr -d ' ')
  if [ -z "$value" ]; then
    MISSING_FIELDS+=("${field} has no value")
    return 1
  fi
  return 0
}

get_complexity() {
  echo "$COMPLEXITY"
}

# ---------------------------------------------------------------------------
# Section 5: Gate functions
# ---------------------------------------------------------------------------

gate_phase_4() {
  yaml_field_equals "phase_4_architect" "complete" || true
}

gate_phase_45() {
  [ "$COMPLEXITY" = "SIMPLE" ] && return 0
  yaml_field_equals "plan_review" "pass" || true
}

gate_phase_51() {
  if [ ! -s "$CWD/build-red-output.log" ]; then
    MISSING_FIELDS+=("missing artifact: build-red-output.log")
    return
  fi
  if ! grep -qE "FAIL|ERROR|FAILED|not ok" "$CWD/build-red-output.log" 2>/dev/null; then
    MISSING_FIELDS+=("build-red-output.log contains no failures (tests must be RED)")
  fi
}

gate_phase_52() {
  yaml_field_equals "opus_validation" "pass" || true
  yaml_field_equals "phase_52a_gherkin" "complete" || true
}

gate_phase_53() {
  if ! grep -q "^phase_53_green:" "$BUILD_STATE" 2>/dev/null || \
     [ "$(grep "^phase_53_green:" "$BUILD_STATE" | sed 's/^phase_53_green:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')" != "complete" ]; then
    hard_block "phase_53_green not complete — GREEN phase must pass before proceeding"
  fi
}

gate_phase_55() {
  local gaming
  gaming=$(grep "^assertion_gaming_detected:" "$BUILD_STATE" 2>/dev/null | sed 's/^assertion_gaming_detected:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')
  if [ "$gaming" = "true" ]; then
    hard_block "assertion_gaming_detected — tests were written to pass without real implementation"
  fi
  yaml_key_has_value "test_integrity_check" || true
}

gate_phase_5() {
  [ "$COMPLEXITY" = "SIMPLE" ] && return 0
  yaml_field_equals "phase_4_architect" "complete" || true
  yaml_field_equals "plan_review" "pass" || true
  yaml_field_equals "phase_5_implement" "complete" || true
  yaml_field_equals "opus_validation" "pass" || true
}

gate_phase_6() {
  # Check findings
  local findings_total findings_fixed
  findings_total=$(grep "^findings_total:" "$BUILD_STATE" 2>/dev/null | sed 's/^findings_total:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')
  findings_fixed=$(grep "^findings_fixed:" "$BUILD_STATE" 2>/dev/null | sed 's/^findings_fixed:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')

  if ! [[ "$findings_total" =~ ^[0-9]+$ ]]; then findings_total=0; fi
  if ! [[ "$findings_fixed" =~ ^[0-9]+$ ]]; then findings_fixed=0; fi

  if [ -n "$findings_total" ] && [ "$findings_total" -gt 0 ] 2>/dev/null; then
    if [ -z "$findings_fixed" ] || [ "$findings_fixed" -lt "$findings_total" ] 2>/dev/null; then
      MISSING_FIELDS+=("unfixed findings: ${findings_fixed:-0}/${findings_total} fixed")
    fi
  fi

  scan_semantic_skip
}

gate_phase_7() {
  yaml_field_equals "review_complete" "pass" || true

  # Soft learning validation: when backend != none, warn if learnings_extracted is missing.
  # This never hard-blocks — learning failures must never stop the pipeline.
  local backend
  backend=$(grep "^learning_backend:" "$BUILD_STATE" 2>/dev/null | sed 's/^learning_backend:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')
  if [ -n "$backend" ] && [ "$backend" != "none" ]; then
    local extracted
    extracted=$(grep "^learnings_extracted:" "$BUILD_STATE" 2>/dev/null | sed 's/^learnings_extracted:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')
    if [ -z "$extracted" ]; then
      # Warn only — do not add to MISSING_FIELDS (soft, never blocks)
      echo "WARN: learnings_extracted not set in build-state.yaml (backend=$backend)" >&2
    fi
  fi
}

# ---------------------------------------------------------------------------
# Section 6: scan_semantic_skip
# ---------------------------------------------------------------------------
scan_semantic_skip() {
  local SKIP_PHRASES=(
    "not our responsibility"
    "not related"
    "acceptable risk"
    "pre-existing"
    "out of scope"
    "known issue"
    "fix later"
    "will address in follow-up"
    "good enough"
    "wont fix"
    "defer"
    "low severity, skip"
    "low priority, skip"
    "cosmetic"
    "won't fix"
    "wontfix"
    "technical debt"
    "acceptable for"
  )

  # Scan build-review-*.md at root level and any *review*.md in subdirs
  local review_files=()
  while IFS= read -r -d '' f; do
    review_files+=("$f")
  done < <(find "$CWD" -maxdepth 2 \( -name "build-review-*.md" -o -name "*review*.md" \) -print0 2>/dev/null)

  for file in "${review_files[@]+"${review_files[@]}"}"; do
    [ -f "$file" ] || continue
    for phrase in "${SKIP_PHRASES[@]}"; do
      if grep -qiF "$phrase" "$file" 2>/dev/null; then
        MISSING_FIELDS+=("semantic skip detected: '$phrase' in $(basename "$file")")
      fi
    done
  done
}

# ---------------------------------------------------------------------------
# Section 7: loop_prevention
# ---------------------------------------------------------------------------
loop_prevention() {
  local phase="$1"

  # Read gate_block_counter from build-state.yaml
  local count
  count=$(grep "^gate_block_counter:" "$BUILD_STATE" 2>/dev/null | sed 's/^gate_block_counter:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')
  count="${count:-0}"

  # Increment counter
  local new_count=$((count + 1))

  # Update counter in build-state.yaml
  if grep -q "^gate_block_counter:" "$BUILD_STATE" 2>/dev/null; then
    # Replace existing
    local tmp_file="${BUILD_STATE}.tmp"
    grep -v "^gate_block_counter:" "$BUILD_STATE" > "$tmp_file" && mv "$tmp_file" "$BUILD_STATE"
  fi
  echo "gate_block_counter: $new_count" >> "$BUILD_STATE"

  if [ "$new_count" -gt 3 ]; then
    echo "gate_bypass: true" >> "$BUILD_STATE"
    echo "gate_bypass_phase: $phase" >> "$BUILD_STATE"
    return 0  # bypass
  fi

  return 1  # still blocking
}

# ---------------------------------------------------------------------------
# Section 8: block / hard_block
# ---------------------------------------------------------------------------
block() {
  echo "{\"decision\":\"block\",\"reason\":\"$1\"}"
  exit 2
}

hard_block() {
  echo "{\"decision\":\"block\",\"reason\":\"HARD BLOCK: $1\"}"
  exit 1
}

# ---------------------------------------------------------------------------
# Section 9: Main dispatch
# ---------------------------------------------------------------------------
drain_stdin
load_config
load_state

MISSING_FIELDS=()

case "$CURRENT_PHASE" in
  0|1|2|3) exit 0 ;;
  4)   gate_phase_4 ;;
  4.5) gate_phase_45 ;;
  5)   gate_phase_5 ;;
  5.1) gate_phase_51 ;;
  5.2) gate_phase_52 ;;
  5.3) gate_phase_53 ;;  # hard block handled inside
  5.5) gate_phase_55 ;;  # assertion gaming = hard block inside
  6)   gate_phase_6 ;;
  7)   gate_phase_7 ;;
  *)   exit 0 ;;
esac

[ "${#MISSING_FIELDS[@]}" -eq 0 ] && exit 0

# Loop prevention (not for 5.3 — already handled inside)
if loop_prevention "$CURRENT_PHASE"; then
  exit 0  # bypassed
fi

REASON=$(printf '%s; ' "${MISSING_FIELDS[@]}")
block "$REASON"
