#!/usr/bin/env bats
# RED tests for scripts/pre-build-gate.sh — CWD mutex model
# All tests MUST fail until pre-build-gate.sh is rewritten for CWD-based collision detection.

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

  # Session file path passed explicitly to the script
  SESSION_FILE="$TMPDIR/.bytedigger-sessions.json"
  export SESSION_FILE

  # Default git mock — returns feature branch
  _mock_git_branch "feat/test-branch"
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

# Write a session file as a JSON array with one active entry at the given CWD.
# Usage: _create_active_session_at_cwd <cwd> [complexity]
_create_active_session_at_cwd() {
  local cwd="$1"
  local complexity="${2:-FEATURE}"
  local now
  now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  cat > "$SESSION_FILE" <<EOF
[
  {
    "session_id": "sess-active-001",
    "cwd": "$cwd",
    "branch": "feat/existing",
    "started_at": "$now",
    "complexity": "$complexity"
  }
]
EOF
}

# Write a session file as a JSON array with one stale entry (>24h ago) at the given CWD.
# Usage: _create_stale_session_at_cwd <cwd>
_create_stale_session_at_cwd() {
  local cwd="$1"
  local stale
  if date -v-25H +"%Y-%m-%dT%H:%M:%SZ" > /dev/null 2>&1; then
    # macOS BSD date
    stale=$(date -v-25H +"%Y-%m-%dT%H:%M:%SZ")
  else
    # GNU date (Linux)
    stale=$(date -u -d "25 hours ago" +"%Y-%m-%dT%H:%M:%SZ")
  fi
  cat > "$SESSION_FILE" <<EOF
[
  {
    "session_id": "sess-stale-001",
    "cwd": "$cwd",
    "branch": "feat/old-branch",
    "started_at": "$stale",
    "complexity": "FEATURE"
  }
]
EOF
}

# ---------------------------------------------------------------------------
# T01: TRIVIAL complexity skips registration — exit 0, no session entry written
# ---------------------------------------------------------------------------

@test "T01_trivial_skips_registration" {
  # TRIVIAL tasks should skip entirely — no session file written
  run bash "$SCRIPT" --complexity TRIVIAL --session-file "$SESSION_FILE"
  [ "$status" -eq 0 ]

  # Session file must NOT exist (TRIVIAL never registers)
  [ ! -f "$SESSION_FILE" ]
}

# ---------------------------------------------------------------------------
# T02: First FEATURE build registers session with CWD and complexity
# ---------------------------------------------------------------------------

@test "T02_first_build_registers" {
  local project_dir
  project_dir=$(mktemp -d)

  # Run from a temp project directory so CWD is controlled
  run bash -c "cd '$project_dir' && bash '$SCRIPT' --complexity FEATURE --session-file '$SESSION_FILE'"
  [ "$status" -eq 0 ]

  # Session file must exist
  [ -f "$SESSION_FILE" ]

  # Session file must contain the correct CWD
  grep -q "$project_dir" "$SESSION_FILE"

  # Session file must contain the complexity
  grep -qi "FEATURE" "$SESSION_FILE"

  rm -rf "$project_dir"
}

# ---------------------------------------------------------------------------
# T03: Second build at same CWD is blocked (collision)
# ---------------------------------------------------------------------------

@test "T03_second_build_same_cwd_blocked" {
  local project_dir
  project_dir=$(mktemp -d)

  # Pre-populate session file with active FEATURE session at the same CWD
  _create_active_session_at_cwd "$project_dir"

  run bash -c "cd '$project_dir' && bash '$SCRIPT' --complexity FEATURE --session-file '$SESSION_FILE'"

  # Must be blocked
  [ "$status" -eq 1 ]

  # Must print collision message
  echo "$output" | grep -qi "collision\|already\|active\|cwd\|blocked"

  rm -rf "$project_dir"
}

# ---------------------------------------------------------------------------
# T04: Build at a different CWD is allowed even if another session is active
# ---------------------------------------------------------------------------

@test "T04_different_cwd_allowed" {
  local project_a project_b
  project_a=$(mktemp -d)
  project_b=$(mktemp -d)

  # Active session at project-a
  _create_active_session_at_cwd "$project_a"

  # Build from project-b — must be allowed
  run bash -c "cd '$project_b' && bash '$SCRIPT' --complexity FEATURE --session-file '$SESSION_FILE'"
  [ "$status" -eq 0 ]

  rm -rf "$project_a" "$project_b"
}

# ---------------------------------------------------------------------------
# T05: Parent/child CWD overlap is blocked (e.g. /tmp/project vs /tmp/project/subdir)
# ---------------------------------------------------------------------------

@test "T05_cwd_parent_child_overlap" {
  local parent_dir child_dir
  parent_dir=$(mktemp -d)
  child_dir="$parent_dir/subdir"
  mkdir -p "$child_dir"

  # Active session at parent directory
  _create_active_session_at_cwd "$parent_dir"

  # Build from child directory — must be blocked (overlapping paths)
  run bash -c "cd '$child_dir' && bash '$SCRIPT' --complexity FEATURE --session-file '$SESSION_FILE'"
  [ "$status" -eq 1 ]

  # Must print a message mentioning overlap, parent, or path conflict
  echo "$output" | grep -qi "overlap\|parent\|path\|contain\|nested"

  rm -rf "$parent_dir"
}

# ---------------------------------------------------------------------------
# T06: Stale sessions (>24h) are cleaned before collision check
# ---------------------------------------------------------------------------

@test "T06_stale_sessions_cleaned" {
  local project_dir
  project_dir=$(mktemp -d)

  # Pre-populate session file with a stale entry at the same CWD
  _create_stale_session_at_cwd "$project_dir"

  # Running from the same CWD must succeed (stale entry cleaned, no collision)
  run bash -c "cd '$project_dir' && bash '$SCRIPT' --complexity FEATURE --session-file '$SESSION_FILE'"
  [ "$status" -eq 0 ]

  # Stale session must be gone from the file
  if [ -f "$SESSION_FILE" ]; then
    ! grep -q "sess-stale-001" "$SESSION_FILE"
  fi

  rm -rf "$project_dir"
}

# ---------------------------------------------------------------------------
# T07: Missing session file is created on first FEATURE run
# ---------------------------------------------------------------------------

@test "T07_missing_session_file_created" {
  local project_dir
  project_dir=$(mktemp -d)

  # Deliberately do NOT create SESSION_FILE
  [ ! -f "$SESSION_FILE" ]

  run bash -c "cd '$project_dir' && bash '$SCRIPT' --complexity FEATURE --session-file '$SESSION_FILE'"
  [ "$status" -eq 0 ]

  # Session file must have been created
  [ -f "$SESSION_FILE" ]

  # Must contain exactly one entry
  python3 -c "
import json, sys
with open('$SESSION_FILE') as f:
    sessions = json.load(f)
assert isinstance(sessions, list), 'expected JSON array'
assert len(sessions) == 1, f'expected 1 entry, got {len(sessions)}'
sys.exit(0)
"

  rm -rf "$project_dir"
}

# ---------------------------------------------------------------------------
# T08: FEATURE on main branch is blocked (worktree enforcement — phase-05-inject.md:19)
# ---------------------------------------------------------------------------

@test "T08_feature_on_main_blocked" {
  local project_dir
  project_dir=$(mktemp -d)

  # Mock git returning "main"
  _mock_git_branch "main"

  # Worktree enforcement: FEATURE on main/master → must exit 1 with clear message
  run bash -c "cd '$project_dir' && bash '$SCRIPT' --complexity FEATURE --session-file '$SESSION_FILE'"
  [ "$status" -eq 1 ]
  echo "$output" | grep -qi "worktree\|main\|master\|feature branch"

  rm -rf "$project_dir"
}
