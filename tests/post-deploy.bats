#!/usr/bin/env bats
# Tests for scripts/post-deploy.sh — Phase 8 post-deploy steps
# post-deploy.sh is implemented: these tests verify real behavior.

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

# Write a mock git that handles specific subcommands
_mock_git() {
  local gone_branch="${1:-}"
  local worktree_branch="${2:-}"
  cat > "$MOCK_BIN/git" << 'GITEOF'
#!/usr/bin/env bash
echo "git $*" >> "$CALL_LOG"
subcmd="$1 $2"
case "$subcmd" in
  "fetch --prune")
    exit 0
    ;;
  "branch -vv")
    if [ -n "$GONE_BRANCH" ]; then
      echo "  $GONE_BRANCH abc1234 [origin/$GONE_BRANCH: gone] Some commit"
    fi
    exit 0
    ;;
  "branch --merged")
    if [ -n "$WORKTREE_BRANCH" ]; then
      echo "$WORKTREE_BRANCH"
    fi
    exit 0
    ;;
  "branch -d"*)
    exit 0
    ;;
  "worktree"*)
    if [ "$2" = "list" ]; then
      if [ -n "$WORKTREE_BRANCH" ]; then
        echo "$PROJECT_DIR/.bytedigger/worktrees/$WORKTREE_BRANCH  abc1234 [$WORKTREE_BRANCH]"
      fi
    elif [ "$2" = "remove" ]; then
      exit 0
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
GITEOF

  # Set environment variables for the mock script
  export GONE_BRANCH="$gone_branch"
  export WORKTREE_BRANCH="$worktree_branch"

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
# T01: Cleanup gone branches
# ---------------------------------------------------------------------------

@test "T01_cleanup_gone_branches" {
  _mock_git "feat/old-branch" ""

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Output must mention cleanup
  echo "$output" | grep -qi "cleanup\|gone\|pruned\|removed\|branch"

  # State must record cleanup complete
  _assert_state "phase_8_cleanup" "complete"
}

# ---------------------------------------------------------------------------
# T02: Cleanup temp files older than 24h
# ---------------------------------------------------------------------------

@test "T02_cleanup_temp_files" {
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
# T03: Cleanup merged worktrees
# ---------------------------------------------------------------------------

@test "T03_cleanup_merged_worktrees" {
  local wt_branch="feat/merged-feature"
  _mock_git "" "$wt_branch"

  # Create the simulated worktree directory
  mkdir -p "$PROJECT_DIR/.bytedigger/worktrees/$wt_branch"

  run bash "$SCRIPT" --cwd "$PROJECT_DIR" --state-file "$STATE_FILE"
  [ "$status" -eq 0 ]

  # Script must check for worktrees
  _assert_called "worktree"
}
