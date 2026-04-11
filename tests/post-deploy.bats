#!/usr/bin/env bats
# RED tests for scripts/post-deploy.sh — Phase 8 post-deploy steps
# All tests MUST fail until post-deploy.sh is implemented.

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

# ---------------------------------------------------------------------------
# T01: Security scan with gitleaks available and passing
# ---------------------------------------------------------------------------

@test "T01_security_scan_with_gitleaks" {
  _mock_gitleaks 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Output must mention gitleaks
  echo "$output" | grep -qi "gitleaks"

  # State must reflect scan passed
  _assert_state "phase_8_security_scan" "pass"
}

# ---------------------------------------------------------------------------
# T02: Security scan with trivy available and passing
# ---------------------------------------------------------------------------

@test "T02_security_scan_with_trivy" {
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Output must mention trivy
  echo "$output" | grep -qi "trivy"

  # State must reflect scan passed
  _assert_state "phase_8_security_scan" "pass"
}

# ---------------------------------------------------------------------------
# T03: Security scan skipped when neither gitleaks nor trivy in PATH
# ---------------------------------------------------------------------------

@test "T03_security_scan_skipped_no_tools" {
  # Do NOT mock gitleaks or trivy — they won't be in PATH
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # State must reflect scan was skipped
  _assert_state "phase_8_security_scan" "skipped"
}

# ---------------------------------------------------------------------------
# T04: Security scan findings reported but script still exits 0 (informational)
# ---------------------------------------------------------------------------

@test "T04_security_scan_findings_reported" {
  # gitleaks exits 1 = findings detected
  _mock_gitleaks 1
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"

  # Must NEVER block — always exit 0
  [ "$status" -eq 0 ]

  # State must record the failure for informational purposes
  _assert_state "phase_8_security_scan" "fail"
}

# ---------------------------------------------------------------------------
# T05: SBOM generated when trivy is available
# ---------------------------------------------------------------------------

@test "T05_sbom_generated" {
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Output must reference the SBOM file path
  echo "$output" | grep -qi "sbom"

  # State must record SBOM was generated
  _assert_state "phase_8_sbom" "generated"
}

# ---------------------------------------------------------------------------
# T06: SBOM skipped when trivy not in PATH
# ---------------------------------------------------------------------------

@test "T06_sbom_skipped_no_trivy" {
  # Do NOT mock trivy
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # State must record SBOM was skipped
  _assert_state "phase_8_sbom" "skipped"
}

# ---------------------------------------------------------------------------
# T07: Cleanup gone branches
# ---------------------------------------------------------------------------

@test "T07_cleanup_gone_branches" {
  _mock_git "feat/old-branch" ""

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Output must mention cleanup
  echo "$output" | grep -qi "cleanup\|gone\|pruned\|removed\|branch"

  # State must record cleanup complete
  _assert_state "phase_8_cleanup" "complete"
}

# ---------------------------------------------------------------------------
# T08: Cleanup temp files older than 24h
# ---------------------------------------------------------------------------

@test "T08_cleanup_temp_files" {
  _mock_git

  # Create .bytedigger-* temp files in CWD
  local temp_file="$PROJECT_DIR/.bytedigger-tmp-test-$$"
  touch "$temp_file"
  # Backdate by 25 hours so script sees them as old enough
  touch -t "$(date -v-25H +%Y%m%d%H%M 2>/dev/null || date -d '25 hours ago' +%Y%m%d%H%M)" "$temp_file" 2>/dev/null || true

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Temp file must be removed
  [ ! -f "$temp_file" ]
}

# ---------------------------------------------------------------------------
# T09: Cleanup merged worktrees
# ---------------------------------------------------------------------------

@test "T09_cleanup_merged_worktrees" {
  local wt_branch="feat/merged-feature"
  _mock_git "" "$wt_branch"

  # Create the simulated worktree directory
  mkdir -p "$PROJECT_DIR/.bytedigger/worktrees/$wt_branch"

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # git worktree remove must have been called
  _assert_called "git worktree remove\|git worktree.*remove"
}

# ---------------------------------------------------------------------------
# T10: All three steps run sequentially (security_scan + sbom + cleanup)
# ---------------------------------------------------------------------------

@test "T10_all_steps_run_sequentially" {
  _mock_gitleaks 0
  _mock_trivy 0
  _mock_git

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # All three state fields must be written
  grep -q "phase_8_security_scan:" "$STATE_FILE"
  grep -q "phase_8_sbom:" "$STATE_FILE"
  grep -q "phase_8_cleanup:" "$STATE_FILE"

  # Verify order: security_scan line must appear before sbom, sbom before cleanup
  local scan_line sbom_line cleanup_line
  scan_line=$(grep -n "phase_8_security_scan:" "$STATE_FILE" | head -1 | cut -d: -f1)
  sbom_line=$(grep -n "phase_8_sbom:" "$STATE_FILE" | head -1 | cut -d: -f1)
  cleanup_line=$(grep -n "phase_8_cleanup:" "$STATE_FILE" | head -1 | cut -d: -f1)

  [ "$scan_line" -lt "$sbom_line" ]
  [ "$sbom_line" -lt "$cleanup_line" ]
}
