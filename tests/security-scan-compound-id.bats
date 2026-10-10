#!/usr/bin/env bats
# Tests for bd#248: the identifiers sessionToken / session_token / SESSION_TOKEN
# (and their plural and upper-case continuations) must be classified HIGH
# (SECRETS) by scripts/security-scan.sh in default mode, in task text, file
# content and planned paths, while the prose phrase "session token" and other
# look-alikes stay not-HIGH. The legacy kill switch keeps the old behaviour.
# Spec: docs/decisions/2026-10-10-bd248-session-token-identifier.md section 2
# (AC1-AC9). Each input has its own cell so a failure names the input.

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/security-scan.sh"

setup() {
  WORK="$(mktemp -d)"
  mkdir -p "$WORK/src"
  cd "$WORK"
  printf 'task: "x"\ncomplexity: FEATURE\n' > "$WORK/build-state.yaml"
}

teardown() {
  cd /
  rm -rf "$WORK"
}

# Default-mode scan (kill switch explicitly unset), cwd = hermetic temp dir.
scan() {
  run env -u BD_SECURITY_SCAN_LEGACY bash "$SCRIPT" --cwd "$WORK" "$@"
}

# Exact whole-line match on captured output.
out_has_line() {
  printf '%s\n' "$output" | grep -qxF -- "$1"
}

# First security_triggers line of captured output.
trigger_line() {
  printf '%s\n' "$output" | grep -m1 '^security_triggers:' || true
}

# Assert the trigger line contains the exact entry (bounded by , or ]).
assert_trigger() {
  local t re
  t="$(trigger_line)"
  re="[[,]? ?${1}(,|\\])"
  [[ "$t" =~ $re ]] || { echo "trigger line was: [$t], wanted entry: $1"; return 1; }
}

# Scan task text $1 (no files); require HIGH, SECRETS, trigger SECRETS=task.
assert_task_secrets() {
  scan --task "$1"
  [ "$status" -eq 0 ] || { echo "input [$1]: exit $status"; return 1; }
  out_has_line "security_classification: HIGH" \
    || { echo "input [$1] not HIGH: $output"; return 1; }
  out_has_line "security_patterns_found: SECRETS" \
    || { echo "input [$1] patterns: $output"; return 1; }
  assert_trigger "SECRETS=task"
}

# Scan task text $1 (no files); require HIGH only.
assert_task_high() {
  scan --task "$1"
  [ "$status" -eq 0 ] || { echo "input [$1]: exit $status"; return 1; }
  out_has_line "security_classification: HIGH" \
    || { echo "input [$1] not HIGH: $output"; return 1; }
}

# Write a 3-line file whose line 3 is $1; require HIGH, SECRETS, trigger on line 3.
assert_file_line3_secrets() {
  printf '%s\n' "// x" "const a = 1;" "$1" > "$WORK/src/probe.txt"
  scan --files "src/probe.txt"
  [ "$status" -eq 0 ] || { echo "input [$1]: exit $status"; return 1; }
  out_has_line "security_classification: HIGH" \
    || { echo "input [$1] not HIGH: $output"; return 1; }
  out_has_line "security_patterns_found: SECRETS" \
    || { echo "input [$1] patterns: $output"; return 1; }
  assert_trigger "SECRETS=src/probe\\.txt:3"
}

# Scan an absent planned path $1; require HIGH.
assert_path_high() {
  [ ! -e "$WORK/$1" ]
  scan --files "$1"
  [ "$status" -eq 0 ] || { echo "input [$1]: exit $status"; return 1; }
  out_has_line "security_classification: HIGH" \
    || { echo "input [$1] not HIGH: $output"; return 1; }
}

# Guard: task text $1 must exit 0 and not be HIGH.
assert_task_not_high() {
  scan --task "$1"
  [ "$status" -eq 0 ] || { echo "input [$1]: exit $status"; return 1; }
  if out_has_line "security_classification: HIGH"; then
    echo "input [$1] wrongly HIGH: $output"; return 1
  fi
  printf '%s\n' "$output" | grep -q '^security_classification: ' \
    || { echo "input [$1] no classification line: $output"; return 1; }
}

# --- AC1 task text, camelCase ---

@test "AC1_task_rotate_the_sessionToken_is_high_secrets_task_trigger" {
  assert_task_secrets "rotate the sessionToken"
}

# --- AC2 task text, snake / upper ---

@test "AC2_task_rotate_the_session_token_is_high_secrets_task_trigger" {
  assert_task_secrets "rotate the session_token"
}

@test "AC2_task_rotate_the_SESSION_TOKEN_is_high_secrets_task_trigger" {
  assert_task_secrets "rotate the SESSION_TOKEN"
}

# --- AC3 file content, line provenance ---

@test "AC3_file_line3_const_sessionToken_is_high_secrets_file_line_trigger" {
  assert_file_line3_secrets "const sessionToken = read();"
}

@test "AC3_file_line3_session_token_assignment_is_high_secrets_file_line_trigger" {
  assert_file_line3_secrets "session_token = read()"
}

# --- AC4 planned paths, files absent ---

@test "AC4_planned_path_src_sessionToken_ts_is_high_with_path_trigger" {
  [ ! -e "$WORK/src/sessionToken.ts" ]
  scan --files "src/sessionToken.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  assert_trigger "SECRETS=src/sessionToken\\.ts"
}

@test "AC4_planned_path_src_session_token_py_is_high" {
  assert_path_high "src/session_token.py"
}

# --- AC5 continuations ---

@test "AC5_task_log_sessionTokens_is_high" { assert_task_high "log sessionTokens"; }
@test "AC5_task_persist_sessionTokenHash_is_high" { assert_task_high "persist sessionTokenHash"; }

# --- AC6 regression: authToken stays HIGH ---

@test "AC6_task_rotate_the_authToken_is_high" { assert_task_high "rotate the authToken"; }

@test "AC6_file_with_authToken_is_high" {
  printf '%s\n' "const authToken = read();" > "$WORK/src/probe.txt"
  scan --files "src/probe.txt"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
}

# --- AC7 guards: never HIGH, exit 0 ---

@test "AC7_GUARD_task_session_token_hash_sign_not_high" { assert_task_not_high "session token hash sign"; }
@test "AC7_GUARD_task_use_sessionStorage_not_high" { assert_task_not_high "use sessionStorage"; }
@test "AC7_GUARD_task_fix_the_sessionTokenizer_not_high" { assert_task_not_high "fix the sessionTokenizer"; }
@test "AC7_GUARD_task_list_sessions_and_tokens_not_high" { assert_task_not_high "list sessions and tokens"; }
@test "AC7_GUARD_task_a_session_dash_token_note_not_high" { assert_task_not_high "a session-token note"; }

@test "AC7_GUARD_planned_path_src_session_tokenizer_ts_not_high" {
  [ ! -e "$WORK/src/session/tokenizer.ts" ]
  scan --files "src/session/tokenizer.ts"
  [ "$status" -eq 0 ]
  if out_has_line "security_classification: HIGH"; then echo "wrongly HIGH: $output"; return 1; fi
  printf '%s\n' "$output" | grep -q '^security_classification: '
}

# --- AC8 kill switch ---

@test "AC8_legacy_1_ignores_task_sessionToken_so_no_files_is_medium_unanalyzed" {
  run env BD_SECURITY_SCAN_LEGACY=1 bash "$SCRIPT" --cwd "$WORK" --task "rotate the sessionToken"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: MEDIUM"
  out_has_line "security_patterns_found: unanalyzed"
}

# --- AC9 existing suites stay green, unedited ---

@test "AC9_existing_security_scan_bats_passes" {
  run bats "$REPO_ROOT/tests/security-scan.bats"
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}

@test "AC9_existing_security_scan_scope_bats_passes" {
  run bats "$REPO_ROOT/tests/security-scan-scope.bats"
  [ "$status" -eq 0 ] || { echo "$output"; return 1; }
}
