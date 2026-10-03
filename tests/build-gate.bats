#!/usr/bin/env bats
# RED tests for scripts/build-gate.sh
# All tests MUST fail until implementation is written.

SCRIPT="${GATE_SCRIPT:-$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/build-gate.sh}"
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
current_phase: "5"
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
current_phase: "5"
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

# bd#89 P2a: test_phase_4_missing_architect_blocks / test_phase_4_complete_passes
# are retired -- phase 4 and its gate are dropped (phase-5 entry is covered below).

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

@test "test_phase_5_simple_requires_plan_review — SIMPLE phase 5 without plan_review → exit 2" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: SIMPLE
mode: AUTONOMOUS
current_phase: "5"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
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

@test "test_phase_6_unfixed_findings_blocks — phase_6_findings_total 5 phase_6_findings_fixed 3 → exit 2" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
phase_6_findings_total: 5
phase_6_findings_fixed: 3
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 2 ]
}

@test "test_phase_6_all_findings_fixed_passes — phase_6_findings_total equals phase_6_findings_fixed → exit 0" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
phase_6_findings_total: 5
phase_6_findings_fixed: 5
phase_6_findings_skipped: 0
post_review_gate: pass
EOF
  # Also need a review file without skip markers
  mkdir -p "$TMPDIR/review"
  echo "All issues resolved." > "$TMPDIR/review/phase6-review.md"
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# C1: schema field name fix tests
# ---------------------------------------------------------------------------

@test "C1_phase_6_old_field_names_not_read — findings_total/findings_fixed (old names) do not block" {
  # Old field names should NOT trigger the gate (schema fix: gate now reads phase_6_* prefix)
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
findings_fixed: 2
phase_6_findings_skipped: 0
post_review_gate: pass
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# C3: scratchpad_stale check
# ---------------------------------------------------------------------------

# bd#89 P2a: the two C3 cases (phase-4 scratchpad_stale on findings-*.md) are
# retired -- phase 4 and its gate are dropped. Phase 5 never reads research/.

# ---------------------------------------------------------------------------
# C4: findings_skipped and post_review_gate hard blocks
# ---------------------------------------------------------------------------

@test "C4_phase_6_findings_skipped_hard_blocks — phase_6_findings_skipped > 0 → exit 1" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
phase_6_findings_total: 5
phase_6_findings_fixed: 4
phase_6_findings_skipped: 1
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 1 ]
}

@test "C4_phase_6_post_review_gate_fail_hard_blocks — post_review_gate != pass → exit 1" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
phase_6_findings_total: 3
phase_6_findings_fixed: 3
phase_6_findings_skipped: 0
post_review_gate: fail
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 1 ]
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
# C2: loop bypass must NOT exempt Phase 6 hard blocks
# ---------------------------------------------------------------------------

@test "C2_bypass_counter_does_not_exempt_findings_skipped_hard_block — exit 1 even at counter 10" {
  # Even with gate_block_counter >> 3, hard blocks (exit 1) must still fire
  # because hard_block() calls exit 1 directly before loop_prevention() is reached
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
phase_6_findings_total: 5
phase_6_findings_fixed: 5
phase_6_findings_skipped: 2
gate_block_counter: 10
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 1 ]
}

@test "C2_bypass_counter_does_not_exempt_post_review_gate_hard_block — exit 1 even at counter 10" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
phase_6_findings_total: 3
phase_6_findings_fixed: 3
phase_6_findings_skipped: 0
post_review_gate: fail
gate_block_counter: 10
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq 1 ]
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
current_phase: "5"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
EOF
  # Without config, gates default ON → missing plan_review → should block (exit 2)
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

@test "legacy *_reviewers keys in bytedigger.json are ignored (gate exit code unchanged)" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "6"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
plan_review: approved
opus_validation: pass
phase_53_green: true
phase_6_findings_total: 5
phase_6_findings_fixed: 5
phase_6_findings_skipped: 0
post_review_gate: pass
EOF
  mkdir -p "$TMPDIR/reviews"
  echo "VERDICT: PASS" > "$TMPDIR/reviews/role-composite.md"
  echo "VERDICT: PASS" > "$TMPDIR/reviews/role-correctness.md"
  echo "VERDICT: PASS" > "$TMPDIR/reviews/role-security.md"
  # Baseline: config without the legacy keys
  run bash "$SCRIPT" < /dev/null
  local base_status="$status"
  # Same state, config carrying the three legacy keys
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "tdd_mandatory": true,
  "simple_reviewers": 3,
  "feature_reviewers": 6,
  "complex_reviewers": 6
}
EOF
  run bash "$SCRIPT" < /dev/null
  [ "$status" -eq "$base_status" ]
}

# ---------------------------------------------------------------------------
# 6. Stdin handling
# ---------------------------------------------------------------------------

@test "test_stdin_drained_without_blocking — piping JSON to stdin completes within 5s" {
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "5"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
EOF
  # Pipe a SubagentStop JSON payload via stdin; script must not block reading it
  local payload='{"type":"subagentStop","session_id":"abc123","exit_code":0}'
  # Use perl alarm as portable timeout (gtimeout not always available on macOS)
  run perl -e 'alarm 5; exec @ARGV' bash -c "printf '%s\n' '$payload' | bash '$SCRIPT'"
  # Must complete (not timeout) — SIGALRM would give exit 142
  [ "$status" -ne 142 ]
}
