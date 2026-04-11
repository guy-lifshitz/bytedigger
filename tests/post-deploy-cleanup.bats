#!/usr/bin/env bats
# RED tests for Phase 8 post-deploy cleanup — security scan + SBOM REMOVED
# These tests MUST fail until post-deploy.sh removes security scan and SBOM steps.
# Phase 8 should ONLY: cleanup (worktrees, temp files, gone branches) + report.

SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/post-deploy.sh"

# ---------------------------------------------------------------------------
# setup / teardown
# ---------------------------------------------------------------------------

setup() {
  TMPDIR="$(mktemp -d)"
  export TMPDIR

  # Mock bin directory — all tool mocks live here
  MOCK_BIN="$TMPDIR/mock-bin"
  mkdir -p "$MOCK_BIN"
  export MOCK_BIN

  # Prepend mock-bin to PATH so post-deploy.sh finds mocks instead of real tools
  export PATH="$MOCK_BIN:$PATH"

  # Call log for verifying mock invocations
  CALL_LOG="$TMPDIR/calls.log"
  touch "$CALL_LOG"
  export CALL_LOG

  # CWD for the script (simulated project root)
  PROJECT_DIR="$TMPDIR/project"
  mkdir -p "$PROJECT_DIR/.bytedigger"
  export PROJECT_DIR

  # Default state file
  STATE_FILE="$PROJECT_DIR/.bytedigger/build-state.yaml"
  cat > "$STATE_FILE" <<'EOF'
task: "Add user authentication"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "8"
forge_run_id: forge-test-001
EOF
  export STATE_FILE
}

teardown() {
  rm -rf "$TMPDIR"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Write a mock gitleaks that logs calls and exits with given code
_mock_gitleaks() {
  local exit_code="${1:-0}"
  cat > "$MOCK_BIN/gitleaks" <<EOF
#!/usr/bin/env bash
echo "gitleaks \$*" >> "$CALL_LOG"
exit $exit_code
EOF
  chmod +x "$MOCK_BIN/gitleaks"
}

# Write a mock trivy that logs calls and exits with given code
_mock_trivy() {
  local exit_code="${1:-0}"
  cat > "$MOCK_BIN/trivy" <<EOF
#!/usr/bin/env bash
echo "trivy \$*" >> "$CALL_LOG"
# Simulate SBOM output to a file if --format and --output flags are present
for i in "\$@"; do
  if [[ "\$i" == *.json ]]; then
    echo '{"bomFormat":"CycloneDX"}' > "\$i"
  fi
done
exit $exit_code
EOF
  chmod +x "$MOCK_BIN/trivy"
}

# Write a mock git that handles specific subcommands
_mock_git() {
  local gone_branch="${1:-}"
  local worktree_branch="${2:-}"
  cat > "$MOCK_BIN/git" <<EOF
#!/usr/bin/env bash
echo "git \$*" >> "$CALL_LOG"
subcmd="\$1 \$2"
case "\$subcmd" in
  "fetch --prune")
    exit 0
    ;;
  "branch -vv")
    if [ -n "$gone_branch" ]; then
      echo "  $gone_branch abc1234 [origin/$gone_branch: gone] Some commit"
    fi
    exit 0
    ;;
  "branch --merged")
    if [ -n "$worktree_branch" ]; then
      echo "  $worktree_branch"
    fi
    exit 0
    ;;
  "branch -d"*)
    exit 0
    ;;
  "worktree"*)
    if [ "\$2" = "list" ]; then
      if [ -n "$worktree_branch" ]; then
        echo "$PROJECT_DIR/.bytedigger/worktrees/$worktree_branch  abc1234 [$worktree_branch]"
      fi
    elif [ "\$2" = "remove" ]; then
      exit 0
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
EOF
  chmod +x "$MOCK_BIN/git"
}

# Assert CALL_LOG contains a line matching pattern
_assert_called() {
  local pattern="$1"
  grep -q "$pattern" "$CALL_LOG"
}

# Assert CALL_LOG does NOT contain a line matching pattern
_assert_not_called() {
  local pattern="$1"
  ! grep -q "$pattern" "$CALL_LOG"
}

# Assert state file contains a given key: value line
_assert_state() {
  local key="$1"
  local value="$2"
  grep -q "^${key}: ${value}" "$STATE_FILE"
}

# Assert state file does NOT contain a given key
_assert_state_missing() {
  local key="$1"
  ! grep -q "^${key}:" "$STATE_FILE"
}

# ---------------------------------------------------------------------------
# T01: Phase 8 must NOT call gitleaks (even if it's available)
# ---------------------------------------------------------------------------

@test "T01_security_scan_not_called_gitleaks" {
  _mock_gitleaks 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # gitleaks MUST NOT be called
  _assert_not_called "gitleaks"
}

# ---------------------------------------------------------------------------
# T02: Phase 8 must NOT call trivy for security scan (even if available)
# ---------------------------------------------------------------------------

@test "T02_security_scan_not_called_trivy" {
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # trivy fs scan MUST NOT be called (no --severity flag = security scan)
  _assert_not_called "trivy fs.*--severity"
}

# ---------------------------------------------------------------------------
# T03: State file must NOT contain phase_8_security_scan key
# ---------------------------------------------------------------------------

@test "T03_state_no_security_scan_key" {
  _mock_gitleaks 0
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # phase_8_security_scan must NOT appear in state
  _assert_state_missing "phase_8_security_scan"
}

# ---------------------------------------------------------------------------
# T04: State file must NOT contain phase_8_sbom key
# ---------------------------------------------------------------------------

@test "T04_state_no_sbom_key" {
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # phase_8_sbom must NOT appear in state
  _assert_state_missing "phase_8_sbom"
}

# ---------------------------------------------------------------------------
# T05: Output must NOT mention "security scan"
# ---------------------------------------------------------------------------

@test "T05_output_no_security_scan_mention" {
  _mock_gitleaks 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Output must NOT reference security scan step
  local captured_output="$output"
  run bash -c 'echo "$1" | grep -qi "step 1.*security"' -- "$captured_output"
  [ "$status" -ne 0 ]
  run bash -c 'echo "$1" | grep -qi "security.*scan"' -- "$captured_output"
  [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------------------
# T06: Output must NOT mention "SBOM" or "sbom"
# ---------------------------------------------------------------------------

@test "T06_output_no_sbom_mention" {
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Output must NOT reference SBOM or SBOM generation
  local captured_output="$output"
  run bash -c 'echo "$1" | grep -qi "sbom"' -- "$captured_output"
  [ "$status" -ne 0 ]
  run bash -c 'echo "$1" | grep -qi "cyclonedx"' -- "$captured_output"
  [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------------------
# T07: Cleanup (gone branches) MUST still be called
# ---------------------------------------------------------------------------

@test "T07_cleanup_gone_branches_still_runs" {
  _mock_git "feat/old-branch" ""

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # git branch -vv MUST be called (gone branch detection)
  _assert_called "git branch -vv"

  # Output must mention cleanup
  echo "$output" | grep -qi "cleanup\|gone\|branch"
}

# ---------------------------------------------------------------------------
# T08: Cleanup (merged worktrees) MUST still be called
# ---------------------------------------------------------------------------

@test "T08_cleanup_merged_worktrees_still_runs" {
  local wt_branch="feat/merged-feature"
  _mock_git "" "$wt_branch"

  # Create the simulated worktree directory
  mkdir -p "$PROJECT_DIR/.bytedigger/worktrees/$wt_branch"

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # git worktree list MUST be called
  _assert_called "git worktree list"
}

# ---------------------------------------------------------------------------
# T09: Cleanup temp files MUST still be called
# ---------------------------------------------------------------------------

@test "T09_cleanup_temp_files_still_runs" {
  _mock_git

  # Create .bytedigger-* temp files in CWD
  local temp_file="$PROJECT_DIR/.bytedigger-tmp-test-$$"
  touch "$temp_file"
  # Backdate by 25 hours so script sees them as old enough
  touch -t "$(date -v-25H +%Y%m%d%H%M 2>/dev/null || date -d '25 hours ago' +%Y%m%d%H%M)" "$temp_file" 2>/dev/null || true

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Temp file must be removed (cleanup still works)
  [ ! -f "$temp_file" ]
}

# ---------------------------------------------------------------------------
# T10: State must contain phase_8_cleanup: complete (cleanup still recorded)
# ---------------------------------------------------------------------------

@test "T10_state_cleanup_complete_recorded" {
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # phase_8_cleanup: complete MUST be in state
  _assert_state "phase_8_cleanup" "complete"
}

# ---------------------------------------------------------------------------
# T11: git fetch --prune MUST still be called
# ---------------------------------------------------------------------------

@test "T11_git_fetch_prune_still_called" {
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # git fetch --prune MUST be called
  _assert_called "git fetch --prune"
}

# ---------------------------------------------------------------------------
# T12: trivy MUST NOT be called with cyclonedx/sbom flags
# ---------------------------------------------------------------------------

@test "T12_trivy_not_called_for_sbom_generation" {
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # trivy with --format cyclonedx MUST NOT be called
  _assert_not_called "trivy.*cyclonedx"
  _assert_not_called "trivy.*sbom"
}

# ---------------------------------------------------------------------------
# T13: SBOM file must NOT be created
# ---------------------------------------------------------------------------

@test "T13_sbom_file_not_created" {
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # sbom.cdx.json file must NOT exist
  [ ! -f "$PROJECT_DIR/.bytedigger/sbom.cdx.json" ]
}

# ---------------------------------------------------------------------------
# T14: Script must NOT print Step 1 or Step 2 (security/sbom steps)
# ---------------------------------------------------------------------------

@test "T14_no_step_1_or_step_2_printed" {
  _mock_gitleaks 0
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Must NOT print "Step 1:" or "Step 2:"
  local captured_output="$output"
  run bash -c 'echo "$1" | grep -q "Step 1:"' -- "$captured_output"
  [ "$status" -ne 0 ]
  run bash -c 'echo "$1" | grep -q "Step 2:"' -- "$captured_output"
  [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------------------
# T15: Output must print only Step 3 (cleanup)
# ---------------------------------------------------------------------------

@test "T15_only_step_3_cleanup_printed" {
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Must print "Step 3:" for cleanup
  echo "$output" | grep -q "Step 3:"
}

# ---------------------------------------------------------------------------
# T16: Script must still exit 0 on completion
# ---------------------------------------------------------------------------

@test "T16_script_exits_zero" {
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# T17: State file must NOT contain any "security" related keys
# ---------------------------------------------------------------------------

@test "T17_state_no_security_keys" {
  _mock_gitleaks 0
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # State must have no security-related keys
  ! grep -q "security" "$STATE_FILE"
}

# ---------------------------------------------------------------------------
# T18: State file must contain ONLY cleanup key (no security/sbom)
# ---------------------------------------------------------------------------

@test "T18_state_only_cleanup_key_added" {
  _mock_gitleaks 0
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Count how many phase_8_* keys are in state (should be 1: cleanup)
  local phase_8_count
  phase_8_count=$(grep -c "^phase_8_" "$STATE_FILE" || true)
  [ "$phase_8_count" -eq 1 ]

  # That one key must be cleanup
  _assert_state "phase_8_cleanup" "complete"
}

# ---------------------------------------------------------------------------
# T19: Script must complete with Phase 8 complete message (no security mention)
# ---------------------------------------------------------------------------

@test "T19_completion_message_no_security" {
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Output must say Phase 8 complete with exact format (no security/sbom context)
  echo "$output" | grep -q "\[post-deploy\] Phase 8 complete\."
}
