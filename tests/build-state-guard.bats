#!/usr/bin/env bats
# Test suite: build-state-guard.sh — blocks deletion of build-state.yaml
# and .bytedigger-orchestrator-pid while pipeline is mid-run (phase < 7).

HOOK="$BATS_TEST_DIRNAME/../hooks/build-state-guard.sh"

# ── helpers ──────────────────────────────────────────────────────────────────

setup() {
  TEST_DIR="$(mktemp -d /tmp/state-guard-test.XXXXXX)"
  cd "$TEST_DIR"
}

teardown() {
  cd /
  rm -rf "$TEST_DIR"
}

# Write build-state.yaml with a given current_phase value.
# Usage: write_state <phase>
write_state() {
  local phase="$1"
  cat > "$TEST_DIR/build-state.yaml" <<EOF
task: "test-task"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "${phase}"
forge_run_id: test-run-$$
EOF
}

# Send a Bash tool-use JSON payload to the hook and capture exit code + output.
# Usage: run_hook <command_string>
run_hook() {
  local cmd="$1"
  local json
  json=$(printf '{"tool_name":"Bash","tool_input":{"command":"%s"}}' "$cmd")
  cd "$TEST_DIR"
  echo "$json" | bash "$HOOK"
}

# ── tests ─────────────────────────────────────────────────────────────────────

# 1. No build-state.yaml present → allow any command (exit 0)
@test "T01: no build-state.yaml → allow any command" {
  # No state file created in setup
  run run_hook "rm build-state.yaml"
  [ "$status" -eq 0 ]
}

# 2. Phase 5 + rm build-state.yaml → BLOCK (exit 2)
@test "T02: phase 5 + rm build-state.yaml → block" {
  write_state "5"
  run run_hook "rm build-state.yaml"
  [ "$status" -eq 2 ]
}

# 3. Phase 5 + rm .bytedigger-orchestrator-pid → BLOCK (exit 2)
@test "T03: phase 5 + rm .bytedigger-orchestrator-pid → block" {
  write_state "5"
  run run_hook "rm .bytedigger-orchestrator-pid"
  [ "$status" -eq 2 ]
}

# 4. Phase 7 + rm build-state.yaml → ALLOW (legitimate post-pipeline cleanup)
@test "T04: phase 7 + rm build-state.yaml → allow" {
  write_state "7"
  run run_hook "rm build-state.yaml"
  [ "$status" -eq 0 ]
}

# 5. Phase "completed" + rm build-state.yaml → ALLOW
@test "T05: phase completed + rm build-state.yaml → allow" {
  write_state "completed"
  run run_hook "rm build-state.yaml"
  [ "$status" -eq 0 ]
}

# 6. Phase 5 + unrelated command (ls -la) → ALLOW (exit 0)
@test "T06: phase 5 + normal command ls -la → allow" {
  write_state "5"
  run run_hook "ls -la"
  [ "$status" -eq 0 ]
}

# 7. Phase 5 + rm targeting unrelated file → ALLOW (exit 0)
@test "T07: phase 5 + rm unrelated-file.txt → allow" {
  write_state "5"
  run run_hook "rm unrelated-file.txt"
  [ "$status" -eq 0 ]
}

# 8. Phase 5 + unlink build-state.yaml → BLOCK (exit 2)
@test "T08: phase 5 + unlink build-state.yaml → block" {
  write_state "5"
  run run_hook "unlink build-state.yaml"
  [ "$status" -eq 2 ]
}

# 9. Phase 5 + rm -f build-state.yaml (with flags) → BLOCK (exit 2)
@test "T09: phase 5 + rm -f build-state.yaml → block" {
  write_state "5"
  run run_hook "rm -f build-state.yaml"
  [ "$status" -eq 2 ]
}

# 10. Phase 5 + rm -rf . (catches build-state.yaml indirectly) → BLOCK (exit 2)
@test "T10: phase 5 + rm -rf . → block (indirect deletion risk)" {
  write_state "5"
  run run_hook "rm -rf ."
  [ "$status" -eq 2 ]
}

# 11. Invalid/empty stdin → fail open (exit 0)
@test "T11: invalid stdin (empty) → fail open, allow" {
  write_state "5"
  cd "$TEST_DIR"
  run bash -c "echo '' | bash \"$HOOK\""
  [ "$status" -eq 0 ]
}

# 12. Phase 0.5 + rm build-state.yaml → BLOCK (all pre-7 phases blocked)
@test "T12: phase 0.5 + rm build-state.yaml → block" {
  write_state "0.5"
  run run_hook "rm build-state.yaml"
  [ "$status" -eq 2 ]
}

# 13. Phase 6 + unlink .bytedigger-orchestrator-pid → BLOCK
@test "T13: phase 6 + unlink .bytedigger-orchestrator-pid → block" {
  write_state "6"
  run run_hook "unlink .bytedigger-orchestrator-pid"
  [ "$status" -eq 2 ]
}

# 14. Phase 5 + rm -rf build-state.yaml (verbose flags) → BLOCK (exit 2)
@test "T14: phase 5 + rm -rf build-state.yaml → block" {
  write_state "5"
  run run_hook "rm -rf build-state.yaml"
  [ "$status" -eq 2 ]
}

# 15. Malformed JSON on stdin → fail open (exit 0)
@test "T15: malformed JSON stdin → fail open, allow" {
  write_state "5"
  cd "$TEST_DIR"
  run bash -c "echo 'not-json-at-all' | bash \"$HOOK\""
  [ "$status" -eq 0 ]
}

# 16. Phase 5 + rm ./build-state.yaml → BLOCK (path prefix bypass)
@test "T16: phase 5 + rm ./build-state.yaml → block (path prefix bypass)" {
  write_state "5"
  run bash -c "echo '{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm ./build-state.yaml\"}}' | bash \"$HOOK\""
  [ "$status" -eq 2 ]
}

# 17. Phase 5 + rm -fr . → BLOCK (reversed flag order)
@test "T17: phase 5 + rm -fr . → block (reversed flag order)" {
  write_state "5"
  run bash -c "echo '{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -fr .\"}}' | bash \"$HOOK\""
  [ "$status" -eq 2 ]
}

# 18. Phase 5 + rm "build-state.yaml" (quoted) → BLOCK
@test "T18: phase 5 + rm quoted build-state.yaml → block" {
  write_state "5"
  run bash -c 'echo "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm \\\"build-state.yaml\\\"\"}}" | bash "'"$HOOK"'"'
  [ "$status" -eq 2 ]
}

# 19. Non-Bash tool (Read) → ALLOW
@test "T19: non-Bash tool (Read) → allow" {
  write_state "5"
  run bash -c "echo '{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"build-state.yaml\"}}' | bash \"$HOOK\""
  [ "$status" -eq 0 ]
}

# 20. Empty current_phase → allow (no active pipeline)
@test "T20: empty current_phase → allow (no active pipeline)" {
  cat > "$TEST_DIR/build-state.yaml" <<EOF
current_phase: ""
EOF
  run bash -c "echo '{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm build-state.yaml\"}}' | bash \"$HOOK\""
  [ "$status" -eq 0 ]
}
