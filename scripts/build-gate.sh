#!/bin/bash
# build-gate.sh — ByteDigger build gate enforcement
# Checks pre-commit conditions at each pipeline phase.

set -euo pipefail

# ---------------------------------------------------------------------------
# Section 1: drain_stdin
# ---------------------------------------------------------------------------
drain_stdin() {
  cat > /dev/null
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
  # stat: GNU (-c %Y) first, then BSD/macOS (-f %m); GNU `stat -f` is filesystem status
  mtime=$(stat -c %Y "$BUILD_STATE" 2>/dev/null || stat -f %m "$BUILD_STATE" 2>/dev/null || echo "0")
  [[ "$mtime" =~ ^[0-9]+$ ]] || mtime=0
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

get_complexity() {
  echo "$COMPLEXITY"
}

# ---------------------------------------------------------------------------
# Section 5: Gate functions
# ---------------------------------------------------------------------------

# yaml_get <field> — value of top-level `field:` in $BUILD_STATE, trimmed of
# surrounding whitespace and one layer of surrounding quotes. Never fails.
yaml_get() {
  # Same order as scripts/ts/lib/state-reader.ts stripKeyAndQuotes:
  # prefix+leading ws, one leading quote, one trailing quote, then ws trim.
  local field="$1" line="" val="" ws=$' \t\v\f'
  line=$(grep "^${field}:" "$BUILD_STATE" 2>/dev/null | head -n 1) || true
  line="${line//$'\r'/}"
  val="${line#"${field}":}"
  val="${val#"${val%%[!$ws]*}"}"
  case "$val" in \"*|\'*) val="${val:1}" ;; esac
  case "$val" in *\"|*\') val="${val%?}" ;; esac
  val="${val#"${val%%[!$ws]*}"}"
  val="${val%"${val##*[!$ws]}"}"
  printf '%s' "$val"
}

# check_deliverables <phase> — soft deliverable checks driven by phase-deliverables.tsv
# (bd#136), which sits next to this script. The whole table is validated on every
# load: validation entries first (physical line order), then the failing rows of
# <phase> in file order. Kinds: field_eq, field_set, log_red, scratch_file.
# Pure bash 3.2: manual TAB split (consecutive TABs keep their empty field).
check_deliverables() {
  local phase="$1"
  local table content
  table="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/phase-deliverables.tsv"
  if ! content=$(cat "$table" 2>/dev/null); then
    MISSING_FIELDS+=("deliverable table unreadable: $table")
    return 0
  fi

  local line t rest kind want val nf i bad e
  local n=0
  local ws=$' \t\v\f\r'
  local tab=$'\t' cr=$'\r'
  local phase_re='^[0-9]+(\.[0-9]+)?$'
  local fields row_list
  fields=()
  row_list=()

  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line="${line%$cr}"
    t="${line#"${line%%[!$ws]*}"}"
    case "$t" in
      ""|"#"*) continue ;;
    esac

    fields=()
    rest="$line"
    while :; do
      case "$rest" in
        *"$tab"*)
          fields+=("${rest%%$tab*}")
          rest="${rest#*$tab}"
          ;;
        *)
          fields+=("$rest")
          break
          ;;
      esac
    done
    nf="${#fields[@]}"

    # 1. fewer than 2 fields, an empty field or a bad phase -> malformed row
    bad=0
    [ "$nf" -lt 2 ] && bad=1
    i=0
    while [ "$i" -lt "$nf" ]; do
      if [ -z "${fields[$i]}" ]; then bad=1; fi
      i=$((i + 1))
    done
    if ! [[ "${fields[0]}" =~ $phase_re ]]; then bad=1; fi
    if [ "$bad" -eq 1 ]; then
      MISSING_FIELDS+=("deliverable table: malformed row (line $n)")
      continue
    fi

    # 2. unknown kind
    kind="${fields[1]}"
    case "$kind" in
      field_eq) want=4 ;;
      field_set|log_red|scratch_file) want=3 ;;
      *)
        MISSING_FIELDS+=("deliverable table: unknown kind '$kind' (line $n)")
        continue
        ;;
    esac

    # 3. wrong arity for the kind -> malformed row
    if [ "$nf" -ne "$want" ]; then
      MISSING_FIELDS+=("deliverable table: malformed row (line $n)")
      continue
    fi

    [ "${fields[0]}" = "$phase" ] || continue

    case "$kind" in
      field_eq)
        val=$(yaml_get "${fields[2]}")
        if [ "$val" != "${fields[3]}" ]; then
          row_list+=("${fields[2]}=${fields[3]} (got: ${val:-<missing>})")
        fi
        ;;
      field_set)
        val=$(yaml_get "${fields[2]}")
        if [ -z "$val" ]; then
          row_list+=("${fields[2]} has no value")
        fi
        ;;
      log_red)
        if [ ! -s "$CWD/${fields[2]}" ]; then
          row_list+=("missing artifact: ${fields[2]}")
        elif ! grep -qE "FAIL|ERROR|FAILED|not ok" "$CWD/${fields[2]}" 2>/dev/null; then
          row_list+=("${fields[2]} contains no failures (tests must be RED)")
        fi
        ;;
      scratch_file)
        val=$(yaml_get "scratchpad_dir")
        if [ -n "$val" ] && [ ! -s "$val/${fields[2]}" ]; then
          row_list+=("missing deliverable: $val/${fields[2]}")
        fi
        ;;
    esac
  done <<< "$content"

  for e in "${row_list[@]+"${row_list[@]}"}"; do
    MISSING_FIELDS+=("$e")
  done
  return 0
}

gate_phase_53() {
  if ! grep -q "^phase_53_green:" "$BUILD_STATE" 2>/dev/null || \
     [ "$(grep "^phase_53_green:" "$BUILD_STATE" | sed 's/^phase_53_green:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ')" != "complete" ]; then
    hard_block "phase_53_green not complete — GREEN phase must pass before proceeding"
  fi
}

gate_phase_55() {
  # An absent key means "not detected" (yaml_get never fails).
  local gaming
  gaming=$(yaml_get "assertion_gaming_detected")
  if [ "$gaming" = "true" ]; then
    hard_block "assertion_gaming_detected — tests were written to pass without real implementation"
  fi
  check_deliverables "5.5"
}

gate_phase_6() {
  # C1: use correct schema field names (phase_6_findings_total / phase_6_findings_fixed)
  local findings_total="" findings_fixed=""
  findings_total=$(grep "^phase_6_findings_total:" "$BUILD_STATE" 2>/dev/null | sed 's/^phase_6_findings_total:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ') || true
  findings_fixed=$(grep "^phase_6_findings_fixed:" "$BUILD_STATE" 2>/dev/null | sed 's/^phase_6_findings_fixed:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ') || true

  if ! [[ "$findings_total" =~ ^[0-9]+$ ]]; then findings_total=0; fi
  if ! [[ "$findings_fixed" =~ ^[0-9]+$ ]]; then findings_fixed=0; fi

  if [ -n "$findings_total" ] && [ "$findings_total" -gt 0 ] 2>/dev/null; then
    if [ -z "$findings_fixed" ] || [ "$findings_fixed" -lt "$findings_total" ] 2>/dev/null; then
      MISSING_FIELDS+=("unfixed findings: ${findings_fixed:-0}/${findings_total} fixed")
    fi
  fi

  # C4: hard block if findings_skipped > 0
  local findings_skipped=""
  findings_skipped=$(grep "^phase_6_findings_skipped:" "$BUILD_STATE" 2>/dev/null | sed 's/^phase_6_findings_skipped:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ') || true
  if [[ "$findings_skipped" =~ ^[0-9]+$ ]] && [ "$findings_skipped" -gt 0 ]; then
    hard_block "phase_6_findings_skipped=$findings_skipped — all findings must be fixed, zero exceptions (build.md:137)"
  fi

  # C4: hard block if post_review_gate != pass
  local post_review_gate=""
  post_review_gate=$(grep "^post_review_gate:" "$BUILD_STATE" 2>/dev/null | sed 's/^post_review_gate:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ') || true
  if [ -n "$post_review_gate" ] && [ "$post_review_gate" != "pass" ]; then
    hard_block "post_review_gate=$post_review_gate — must be 'pass' before proceeding to Phase 7 (build.md:137, phase-6-review.md:271)"
  fi

  scan_semantic_skip
}

gate_phase_7() {
  [ "$COMPLEXITY" = "TRIVIAL" ] && return 0

  # Soft learning validation: when backend != none, warn if learnings_extracted is missing.
  # This never hard-blocks — learning failures must never stop the pipeline.
  local backend
  backend=$(yaml_get "learning_backend")
  if [ -n "$backend" ] && [ "$backend" != "none" ]; then
    local extracted
    extracted=$(yaml_get "learnings_extracted")
    if [ -z "$extracted" ]; then
      # Warn only — do not add to MISSING_FIELDS (soft, never blocks)
      echo "WARN: learnings_extracted not set in build-state.yaml (backend=$backend)" >&2
    fi
  fi

  # Review result + bd#127 synthesizer deliverable come from the table (soft).
  check_deliverables "7"
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
# canon_phase <p> — alias map shared with the TS gate: 45 51 52 53 55 -> 4.5 5.1 5.2 5.3 5.5
canon_phase() {
  case "$1" in
    45) printf '%s' "4.5" ;;
    51) printf '%s' "5.1" ;;
    52) printf '%s' "5.2" ;;
    53) printf '%s' "5.3" ;;
    55) printf '%s' "5.5" ;;
    *) printf '%s' "$1" ;;
  esac
}

# Per-phase counter (bd#136): C1 no stored phase -> count+1; C2 same phase -> count+1;
# C3 different phase -> 1; C4 missing / non-numeric counter -> 0.
loop_prevention() {
  local phase
  phase="$(canon_phase "$1")"

  local count stored_phase
  count=$(yaml_get "gate_block_counter")
  if ! [[ "$count" =~ ^[0-9]+$ ]]; then count=0; fi
  stored_phase=$(canon_phase "$(yaml_get "gate_block_phase")")

  local new_count
  if [ -n "$stored_phase" ] && [ "$stored_phase" != "$phase" ]; then
    new_count=1
  else
    new_count=$((10#$count + 1))
  fi

  # One atomic rewrite (unique temp name, then rename). Best-effort: the verdict is
  # emitted even when the state file cannot be written.
  local tmp_file="${BUILD_STATE}.$$.${RANDOM}${RANDOM}.tmp"
  if ! {
    grep -v -e '^gate_block_counter:' -e '^gate_block_phase:' \
            -e '^gate_bypass:' -e '^gate_bypass_phase:' "$BUILD_STATE" 2>/dev/null || true
    echo "gate_block_counter: $new_count"
    echo "gate_block_phase: $phase"
    if [ "$new_count" -gt 3 ]; then
      echo "gate_bypass: true"
      echo "gate_bypass_phase: $phase"
    fi
  } > "$tmp_file" || ! mv "$tmp_file" "$BUILD_STATE"; then
    echo "[gate] WARN: loop_prevention could not update $BUILD_STATE" >&2
    rm -f "$tmp_file" 2>/dev/null || true
  fi

  if [ "$new_count" -gt 3 ]; then
    return 0  # bypass
  fi

  return 1  # still blocking
}

# ---------------------------------------------------------------------------
# Section 8: block / hard_block
# ---------------------------------------------------------------------------
block() {
  # JSON-escape the reason: backslash first, then double quote.
  local reason
  reason=$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  echo "{\"decision\":\"block\",\"reason\":\"$reason\"}"
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
  0|1|2|3|4) exit 0 ;;  # 1-4 kept as pass-through: an in-flight build may still carry them
  4.5|5|5.1|5.2) check_deliverables "$CURRENT_PHASE" ;;
  5.3) gate_phase_53 ;;  # hard block handled inside
  5.5) gate_phase_55 ;;  # assertion gaming = hard block inside
  6)   gate_phase_6 ;;
  7)   gate_phase_7 ;;
  *)   exit 0 ;;
esac

[ "${#MISSING_FIELDS[@]}" -eq 0 ] && exit 0

# C2: Check for hard blocks before loop prevention.
# Hard blocks (findings_skipped, post_review_gate) must always fire regardless of bypass counter.
# These are already handled via hard_block() calls inside gate functions (exit 1 directly),
# so if we reach here, any remaining MISSING_FIELDS are soft blocks subject to loop prevention.

# Loop prevention (not for 5.3 — already handled inside; hard blocks already exited via hard_block())
if loop_prevention "$CURRENT_PHASE"; then
  exit 0  # bypassed
fi

REASON=$(printf '%s; ' "${MISSING_FIELDS[@]}")
block "$REASON"
