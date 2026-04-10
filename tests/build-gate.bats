#!/usr/bin/env bats
# RED tests for scripts/build-gate.sh
# All tests MUST fail until implementation is written.

SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/build-gate.sh"
PLUGIN_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
  TMPDIR="$(mktemp -d)"
  export TMPDIR
  # Default bytedigger.json with gates enabled
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "tdd_mandatory": true
}
EOF
  # Point script at plugin root via env var
  export BYTEDIGGER_PLUGIN_ROOT="$PLUGIN_ROOT"
  # Override config path so tests don't touch the real one
  export BYTEDIGGER_CONFIG="$TMPDIR/bytedigger.json"
}

teardown() {
  rm -rf "$TMPDIR"
}

# ---------------------------------------------------------------------------
# 1. Basic pass/fail
# ---------------------------------------------------------------------------

@test "test_no_state_file_exits_0 — no build-state.yaml means not a build session" {
  # No build-state.yaml in TMPDIR → should exit 0
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 0 ]
}

@test "test_gates_disabled_exits_0 — bytedigger.json gates_enabled false skips all checks" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": false
}
EOF
  cat > "$TMPDIR/build-state.yaml" <<'EOF'
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "4"
last_updated: "2026-04-10T12:00:00Z"
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 0 ]
}

@test "test_stale_state_exits_0 — build-state.yaml older than 600s is ignored" {
  cat > "$TMPDIR/build-state.yaml" <<'EOF'
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "4"
last_updated: "2026-04-10T12:00:00Z"
EOF
  # Make the file 700 seconds old
  touch -t "$(date -v -700S '+%Y%m%d%H%M.%S' 2>/dev/null || date -d '700 seconds ago' '+%Y%m%d%H%M.%S')" "$TMPDIR/build-state.yaml"
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# 2. Phase gate checks
# ---------------------------------------------------------------------------

@test "test_phase_4_missing_architect_blocks — phase 4 without phase_4_architect → exit 2" {
  cat > "$TMPDIR/build-state.yaml" <<'EOF'
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "4"
last_updated: "NOW"
EOF
  # Patch last_updated to now via python
  python3 -c "
import yaml, datetime, sys
with open('$TMPDIR/build-state.yaml') as f:
    d = yaml.safe_load(f)
d['last_updated'] = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
with open('$TMPDIR/build-state.yaml', 'w') as f:
    yaml.dump(d, f)
" 2>/dev/null || sed -i.bak "s/last_updated: \"NOW\"/last_updated: \"$(date -u '+%Y-%m-%dT%H:%M:%SZ')\"/" "$TMPDIR/build-state.yaml"

  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
}

@test "test_phase_4_complete_passes — phase 4 with architect complete and findings → exit 0" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "4"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
phase_4_architect: complete
findings_total: 3
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 0 ]
}

@test "test_phase_5_entry_missing_plan_review_blocks — FEATURE phase 5 without plan_review → exit 2" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "5"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
}

@test "test_phase_5_simple_skips_plan_review — SIMPLE phase 5 without plan_review → exit 0" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: SIMPLE
mode: AUTONOMOUS
current_phase: "5"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 0 ]
}

@test "test_phase_51_missing_red_output_blocks — phase 5.1 without build-red-output.log → exit 2" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "5.1"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
EOF
  # No build-red-output.log present
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
}

@test "test_phase_51_red_output_no_failures_blocks — log exists but contains no FAIL → exit 2" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "5.1"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
EOF
  # Log exists but no FAIL lines (tests passed = not RED)
  cat > "$TMPDIR/build-red-output.log" <<'EOF'
1..5
ok 1 test_one
ok 2 test_two
ok 3 test_three
ok 4 test_four
ok 5 test_five
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
}

@test "test_phase_52_missing_opus_validation_blocks — phase 5.2 without opus_validation pass → exit 2" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "5.2"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
EOF
  cat > "$TMPDIR/build-red-output.log" <<'EOF'
not ok 1 test_one
not ok 2 test_two
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
}

@test "test_phase_53_green_hard_blocks — phase 5.3 missing phase_53_green → exit 1 (hard block)" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "5.3"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 1 ]
}

@test "test_phase_55_assertion_gaming_hard_blocks — assertion_gaming_detected true → exit 1" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "5.5"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
assertion_gaming_detected: true
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 1 ]
}

# ---------------------------------------------------------------------------
# 3. Phase 6 review gates
# ---------------------------------------------------------------------------

@test "test_phase_6_unfixed_findings_blocks — findings_total 5 findings_fixed 3 → exit 2" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
findings_total: 5
findings_fixed: 3
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
}

@test "test_phase_6_all_findings_fixed_passes — findings_total equals findings_fixed → exit 0" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
findings_total: 5
findings_fixed: 5
EOF
  # Also need a review file without skip markers
  mkdir -p "$TMPDIR/review"
  echo "All issues resolved." > "$TMPDIR/review/phase6-review.md"
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 0 ]
}

@test "test_phase_6_semantic_skip_detected_blocks — review file contains 'fix later' → exit 2" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
findings_total: 3
findings_fixed: 3
EOF
  mkdir -p "$TMPDIR/review"
  echo "The edge case will fix later, it's minor." > "$TMPDIR/review/phase6-review.md"
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
}

# ---------------------------------------------------------------------------
# 4. Loop prevention
# ---------------------------------------------------------------------------

@test "test_loop_prevention_bypasses_after_3_blocks — gate_block_counter 3 → exit 0 with bypass marker" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "5.1"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
gate_block_counter: 3
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 0 ]
  # The yaml should now contain a bypass marker
  grep -q "gate_bypass" "$TMPDIR/build-state.yaml"
}

# ---------------------------------------------------------------------------
# 5. Config handling
# ---------------------------------------------------------------------------

@test "test_missing_config_uses_defaults — no bytedigger.json still enforces gates" {
  rm -f "$TMPDIR/bytedigger.json"
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "4"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
EOF
  # Without config, gates default ON → missing phase_4_architect → should block (exit 2)
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
}

@test "test_complexity_downgrade_hard_blocks — metadata FEATURE but yaml says SIMPLE → exit 1" {
  # build-metadata.json says FEATURE, build-state.yaml says SIMPLE (downgrade attempt)
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: SIMPLE
mode: AUTONOMOUS
current_phase: "5"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
EOF
  cat > "$TMPDIR/build-metadata.json" <<'EOF'
{
  "complexity": "FEATURE",
  "classified_at": "2026-04-10T10:00:00Z"
}
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 1 ]
}

# ---------------------------------------------------------------------------
# 6. Stdin handling
# ---------------------------------------------------------------------------

@test "test_stdin_drained_without_blocking — piping JSON to stdin completes within 5s" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "4"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
EOF
  # Pipe a SubagentStop JSON payload via stdin; script must not block reading it
  local payload='{"type":"subagentStop","session_id":"abc123","exit_code":0}'
  # Use perl alarm as portable timeout (gtimeout not always available on macOS)
  run perl -e 'alarm 5; exec @ARGV' bash -c "printf '%s\n' '$payload' | bash '$SCRIPT'"
  # Must complete (not timeout) — SIGALRM would give exit 142
  [ "$status" -ne 142 ]
}
