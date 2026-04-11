#!/usr/bin/env bats
# RED tests for scripts/pre-build-gate.sh — pre-build branch + session guard
# All tests MUST fail until pre-build-gate.sh is implemented.

SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/pre-build-gate.sh"
PLUGIN_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

# ---------------------------------------------------------------------------
# setup / teardown
# ---------------------------------------------------------------------------

setup() {
  TMPDIR="$(mktemp -d)"
  export TMPDIR

  # Mock bin directory — git mocks live here
  MOCK_BIN="$TMPDIR/mock-bin"
  mkdir -p "$MOCK_BIN"
  export MOCK_BIN

  # Prepend mock-bin to PATH so pre-build-gate.sh finds mocks instead of real git
  export PATH="$MOCK_BIN:$PATH"

  # Track calls: each mock appends its args to a call-log file
  CALL_LOG="$TMPDIR/calls.log"
  touch "$CALL_LOG"
  export CALL_LOG

  # Session file path used by the script (in CWD)
  SESSION_FILE="$TMPDIR/.bytedigger-sessions.json"
  export SESSION_FILE
}

teardown() {
  rm -rf "$TMPDIR"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Write a mock git that logs calls and returns a given branch name.
# Usage: _mock_git_branch <branch_name>
_mock_git_branch() {
  local branch="$1"
  cat > "$MOCK_BIN/git" <<EOF
#!/usr/bin/env bash
echo "git \$*" >> "$CALL_LOG"
case "\$1 \$2" in
  "branch --show-current")
    echo "$branch"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
EOF
  chmod +x "$MOCK_BIN/git"
}

# Create a session file with an active session on the given branch (current time = active).
# Usage: _create_active_session <branch_name>
_create_active_session() {
  local branch="$1"
  local now
  now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  cat > "$SESSION_FILE" <<EOF
{
  "sessions": [
    {
      "id": "sess-001",
      "branch": "$branch",
      "started_at": "$now",
      "pid": 99999
    }
  ]
}
EOF
}

# Create a session file with a session older than 24h on the given branch.
# Usage: _create_stale_session <branch_name>
_create_stale_session() {
  local branch="$1"
  # 25 hours ago — stale by any reasonable definition
  local stale
  if date -v-25H +"%Y-%m-%dT%H:%M:%SZ" > /dev/null 2>&1; then
    # macOS BSD date
    stale=$(date -v-25H +"%Y-%m-%dT%H:%M:%SZ")
  else
    # GNU date (Linux)
    stale=$(date -u -d "25 hours ago" +"%Y-%m-%dT%H:%M:%SZ")
  fi
  cat > "$SESSION_FILE" <<EOF
{
  "sessions": [
    {
      "id": "sess-stale-001",
      "branch": "$branch",
      "started_at": "$stale",
      "pid": 88888
    }
  ]
}
EOF
}

# ---------------------------------------------------------------------------
# T01: blocks FEATURE on main branch
# ---------------------------------------------------------------------------

@test "T01_blocks_feature_on_main" {
  _mock_git_branch "main"

  run bash "$SCRIPT" --complexity FEATURE --session-file "$SESSION_FILE"
  [ "$status" -eq 1 ]
  # Must print a message explaining the block (not a silent exit 1)
  echo "$output" | grep -qi "main\|feature\|branch\|blocked\|not allowed\|forbidden"
}

# ---------------------------------------------------------------------------
# T02: blocks COMPLEX on master branch
# ---------------------------------------------------------------------------

@test "T02_blocks_complex_on_master" {
  _mock_git_branch "master"

  run bash "$SCRIPT" --complexity COMPLEX --session-file "$SESSION_FILE"
  [ "$status" -eq 1 ]
  # Must print a message explaining the block (not a silent exit 1)
  echo "$output" | grep -qi "master\|complex\|branch\|blocked\|not allowed\|forbidden"
}

# ---------------------------------------------------------------------------
# T03: allows SIMPLE on main (with warning)
# ---------------------------------------------------------------------------

@test "T03_allows_simple_on_main" {
  _mock_git_branch "main"

  run bash "$SCRIPT" --complexity SIMPLE --session-file "$SESSION_FILE"
  [ "$status" -eq 0 ]
  # Must print a warning — SIMPLE on main is unusual
  echo "$output" | grep -qi "warn\|caution\|simple.*main\|main.*simple"
}

# ---------------------------------------------------------------------------
# T04: allows FEATURE on a non-main/master branch
# ---------------------------------------------------------------------------

@test "T04_allows_feature_on_branch" {
  _mock_git_branch "feat/something"

  run bash "$SCRIPT" --complexity FEATURE --session-file "$SESSION_FILE"
  [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# T05: blocks when another session is active on the same branch
# ---------------------------------------------------------------------------

@test "T05_detects_session_collision" {
  _mock_git_branch "feat/my-feature"
  _create_active_session "feat/my-feature"

  run bash "$SCRIPT" --complexity FEATURE --session-file "$SESSION_FILE"
  [ "$status" -eq 1 ]
  # Must mention collision
  echo "$output" | grep -qi "collision\|already\|session\|active"
}

# ---------------------------------------------------------------------------
# T06: allows run when active session is on a different branch
# ---------------------------------------------------------------------------

@test "T06_allows_different_branch_session" {
  _mock_git_branch "feat/my-feature"
  _create_active_session "feat/other-feature"

  run bash "$SCRIPT" --complexity FEATURE --session-file "$SESSION_FILE"
  [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# T07: cleans up stale sessions (older than 24h) before checking
# ---------------------------------------------------------------------------

@test "T07_cleans_stale_sessions" {
  _mock_git_branch "feat/my-feature"
  _create_stale_session "feat/my-feature"

  run bash "$SCRIPT" --complexity FEATURE --session-file "$SESSION_FILE"
  # After running, the stale session must be gone — so exit 0 (not blocked)
  [ "$status" -eq 0 ]
  # The session file must either be absent or contain no stale entry
  if [ -f "$SESSION_FILE" ]; then
    ! grep -q "sess-stale-001" "$SESSION_FILE"
  fi
}

# ---------------------------------------------------------------------------
# T08: handles missing session file gracefully (creates new one, exits 0)
# ---------------------------------------------------------------------------

@test "T08_handles_missing_session_file" {
  _mock_git_branch "feat/new-feature"
  # Deliberately do NOT create SESSION_FILE

  run bash "$SCRIPT" --complexity FEATURE --session-file "$SESSION_FILE"
  [ "$status" -eq 0 ]
}
