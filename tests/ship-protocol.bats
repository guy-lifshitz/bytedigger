#!/usr/bin/env bats
# RED tests for scripts/ship.sh — SHIP protocol (--pr flag)
# All tests MUST fail until ship.sh is implemented.

SCRIPT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/ship.sh"
PLUGIN_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

# ---------------------------------------------------------------------------
# setup / teardown
# ---------------------------------------------------------------------------

setup() {
  TMPDIR="$(mktemp -d)"
  export TMPDIR

  # Mock bin directory — all git/gh mocks live here
  MOCK_BIN="$TMPDIR/mock-bin"
  mkdir -p "$MOCK_BIN"
  export MOCK_BIN

  # Prepend mock-bin to PATH so ship.sh finds mocks instead of real tools
  export PATH="$MOCK_BIN:$PATH"

  # Default bytedigger.json
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "tdd_mandatory": true
}
EOF
  export BYTEDIGGER_CONFIG="$TMPDIR/bytedigger.json"

  # Track calls: each mock appends its args to a call-log file
  CALL_LOG="$TMPDIR/calls.log"
  touch "$CALL_LOG"
  export CALL_LOG

  # Default build-state.yaml (task set, phase 7 = pipeline done)
  cat > "$TMPDIR/build-state.yaml" <<'EOF'
task: "Add user authentication"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "7"
forge_run_id: forge-test-001
files_modified:
  - src/auth.ts
  - src/auth.test.ts
EOF
}

teardown() {
  rm -rf "$TMPDIR"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Write a mock git that logs calls and handles specific subcommands.
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
  "checkout -b")
    exit 0
    ;;
  "add"*)
    exit 0
    ;;
  "diff --cached")
    # Exit 1 = there are staged changes (non-quiet = proceed with commit)
    exit 1
    ;;
  "commit -m"*)
    exit 0
    ;;
  "push"*)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
EOF
  chmod +x "$MOCK_BIN/git"
}

# Write a mock gh that logs calls and always succeeds.
_mock_gh() {
  cat > "$MOCK_BIN/gh" <<EOF
#!/usr/bin/env bash
echo "gh \$*" >> "$CALL_LOG"
if [ "\$1" = "pr" ] && [ "\$2" = "create" ]; then
  echo "https://github.com/shtofadhor/bytedigger/pull/1"
fi
exit 0
EOF
  chmod +x "$MOCK_BIN/gh"
}

# Assert that CALL_LOG contains a line matching a pattern
_assert_called() {
  local pattern="$1"
  grep -q "$pattern" "$CALL_LOG"
}

# Assert that CALL_LOG does NOT contain a line matching a pattern
_assert_not_called() {
  local pattern="$1"
  ! grep -q "$pattern" "$CALL_LOG"
}

# ---------------------------------------------------------------------------
# Test 1: creates a new branch when currently on main
# ---------------------------------------------------------------------------

@test "T01_ship_creates_branch_when_on_main" {
  _mock_git_branch "main"
  _mock_gh

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # Must have called `git checkout -b` with a feat/ prefix
  _assert_called "git checkout -b feat/"
}

# ---------------------------------------------------------------------------
# Test 2: skips branch creation when already on a feature branch
# ---------------------------------------------------------------------------

@test "T02_ship_skips_branch_when_on_feature" {
  _mock_git_branch "feat/existing-branch"
  _mock_gh

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # Must NOT have called `git checkout -b`
  _assert_not_called "git checkout -b"
}

# ---------------------------------------------------------------------------
# Test 3: stages files listed in build-state.yaml files_modified
# ---------------------------------------------------------------------------

@test "T03_ship_stages_files_from_state" {
  _mock_git_branch "feat/add-auth"
  _mock_gh

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # Must have called `git add` with files from the state
  _assert_called "git add.*src/auth.ts"
  _assert_called "git add.*src/auth.test.ts"
}

# ---------------------------------------------------------------------------
# Test 4: excludes sensitive files (.env) from git add
# ---------------------------------------------------------------------------

@test "T04_ship_excludes_sensitive_files" {
  _mock_git_branch "feat/add-auth"
  _mock_gh

  # Overwrite build-state to include .env
  cat > "$TMPDIR/build-state.yaml" <<'EOF'
task: "Add user authentication"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "7"
forge_run_id: forge-test-001
files_modified:
  - src/auth.ts
  - .env
  - .env.local
  - src/auth.test.ts
EOF

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # .env must NOT appear in any git add call
  _assert_not_called "git add .*.env"
  # Safe files must still be staged
  _assert_called "git add.*src/auth.ts"
}

# ---------------------------------------------------------------------------
# Test 5: commits with a descriptive message containing the task description
# ---------------------------------------------------------------------------

@test "T05_ship_commits_with_descriptive_message" {
  _mock_git_branch "feat/add-auth"
  _mock_gh

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # git commit must have been called
  _assert_called "git commit"
  # Commit message must contain the task text from build-state.yaml
  _assert_called "git commit.*[Aa]dd user authentication"
}

# ---------------------------------------------------------------------------
# Test 6: pushes branch with -u upstream flag
# ---------------------------------------------------------------------------

@test "T06_ship_pushes_with_upstream" {
  _mock_git_branch "feat/add-auth"
  _mock_gh

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # Must call `git push -u origin <branch>`
  _assert_called "git push -u origin"
}

# ---------------------------------------------------------------------------
# Test 7: calls gh pr create with title and body
# ---------------------------------------------------------------------------

@test "T07_ship_creates_pr_with_gh" {
  _mock_git_branch "feat/add-auth"
  _mock_gh

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # Must have called gh pr create
  _assert_called "gh pr create"
  # Must include --title flag
  _assert_called "gh pr create.*--title"
  # Must include --body flag
  _assert_called "gh pr create.*--body"
}

# ---------------------------------------------------------------------------
# Test 8: without --pr flag, ship.sh should exit 0 without running git operations
# ---------------------------------------------------------------------------

@test "T08_ship_skips_without_pr_flag" {
  _mock_git_branch "main"
  _mock_gh

  run bash "$SCRIPT" --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # No git operations should have run at all
  _assert_not_called "git"
  _assert_not_called "gh"
}

# ---------------------------------------------------------------------------
# Test 9: missing gh CLI → commit+push still happen, PR creation is skipped with warning
# ---------------------------------------------------------------------------

@test "T09_ship_handles_missing_gh" {
  _mock_git_branch "feat/add-auth"
  # Deliberately do NOT create a mock for gh — it won't be in PATH

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # git commit and push must still have happened
  _assert_called "git commit"
  _assert_called "git push"
  # gh must NOT have been called (it doesn't exist in PATH)
  _assert_not_called "gh pr create"
  # stderr or stdout must warn about missing gh
  echo "$output" | grep -qi "gh\|pull request\|pr"
}

# ---------------------------------------------------------------------------
# Test 10: writes ship_complete and ship_pr_url to build-state.yaml on success
# ---------------------------------------------------------------------------

@test "T10_ship_writes_state_on_success" {
  _mock_git_branch "feat/add-auth"
  _mock_gh

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # build-state.yaml must contain ship_complete: true
  grep -q "ship_complete: true" "$TMPDIR/build-state.yaml"
  # build-state.yaml must contain ship_pr_url with a non-empty value
  grep -q "ship_pr_url:" "$TMPDIR/build-state.yaml"
  local url_line
  url_line=$(grep "^ship_pr_url:" "$TMPDIR/build-state.yaml")
  # URL must not be empty (strip key and whitespace)
  local url_value
  url_value=$(echo "$url_line" | sed 's/^ship_pr_url:[[:space:]]*//')
  [ -n "$url_value" ]
}

# ---------------------------------------------------------------------------
# Test 11: all files are sensitive → no commit, warning printed, exit 0
# ---------------------------------------------------------------------------

@test "T11_ship_handles_all_sensitive_files" {
  # Override git mock: make `git diff --cached --quiet` exit 0 (nothing staged)
  cat > "$MOCK_BIN/git" <<EOF
#!/usr/bin/env bash
echo "git \$*" >> "$CALL_LOG"
case "\$1 \$2" in
  "branch --show-current")
    echo "feat/add-auth"
    exit 0
    ;;
  "diff --cached")
    # --quiet: exit 0 means nothing staged (empty diff)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
EOF
  chmod +x "$MOCK_BIN/git"
  _mock_gh

  # State with only sensitive files
  cat > "$TMPDIR/build-state.yaml" <<'EOF'
task: "Add secrets management"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "7"
forge_run_id: forge-test-011
files_modified:
  - .env
  - secrets.pem
EOF

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"

  # Must exit 0 — not a hard failure
  [ "$status" -eq 0 ]

  # Must NOT have called git commit
  _assert_not_called "git commit"

  # Must print a warning about no files to commit
  echo "$output" | grep -qi "no files to commit\|all excluded\|sensitive"
}

# ---------------------------------------------------------------------------
# Test 12: task with colon in value → full message preserved, not truncated
# ---------------------------------------------------------------------------

@test "T12_ship_handles_colon_in_task" {
  _mock_git_branch "feat/add-oauth2"
  _mock_gh

  # State with colon in task value
  cat > "$TMPDIR/build-state.yaml" <<'EOF'
task: "Add OAuth2: user login"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "7"
forge_run_id: forge-test-012
files_modified:
  - src/oauth.ts
EOF

  run bash "$SCRIPT" --pr --config "$TMPDIR/bytedigger.json" --state "$TMPDIR/build-state.yaml"
  [ "$status" -eq 0 ]

  # git commit must have been called with the FULL task message (including colon part)
  _assert_called "git commit.*OAuth2"
  # Must NOT have been truncated — "user login" must appear in commit call
  _assert_called "git commit.*user login"
}
