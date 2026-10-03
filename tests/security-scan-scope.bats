#!/usr/bin/env bats
# RED tests for bd#129: security-scan.sh scope (task text, path strings, word
# boundaries, provenance, kill switch) + the two phase docs that call it.
# Spec: docs/decisions/2026-10-03-bd129-security-scan-scope.md section 2 (AC1-AC9).
# AC10 (tests/security-scan.bats stays green) needs no new cell.

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/security-scan.sh"
PHASE05="$REPO_ROOT/phases/phase-05-inject.md"
PHASE45="$REPO_ROOT/phases/phase-45-spec.md"

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

write_ac1_file() {
  printf '%s\n' "session token hash sign" "fetch('/items');" > "$WORK/src/ac1.ts"
}

# ---------------------------------------------------------------------------
# AC1
# ---------------------------------------------------------------------------

@test "AC1_bare_session_token_hash_sign_is_not_high" {
  write_ac1_file
  scan --files "src/ac1.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: MEDIUM"
  out_has_line "security_patterns_found: DATA"
}

# ---------------------------------------------------------------------------
# AC2
# ---------------------------------------------------------------------------

@test "AC2_jwt_verify_is_high_auth_with_file_line_provenance" {
  printf '%s\n' "// x" "const a = 1;" "jwt.verify(t, s);" > "$WORK/src/a.ts"
  scan --files "src/a.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  out_has_line "security_patterns_found: AUTH"
  assert_trigger "AUTH=src/a\\.ts:3"
}

@test "AC2_encrypt_is_high_crypto_with_file_line_provenance" {
  printf '%s\n' "// x" "const out = encrypt(data, key);" > "$WORK/src/c.ts"
  scan --files "src/c.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  out_has_line "security_patterns_found: CRYPTO"
  assert_trigger "CRYPTO=src/c\\.ts:2"
}

@test "AC2_api_key_is_high_secrets_with_file_line_provenance" {
  printf '%s\n' "API_KEY=abc" "// x" > "$WORK/src/s.ts"
  scan --files "src/s.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  out_has_line "security_patterns_found: SECRETS"
  assert_trigger "SECRETS=src/s\\.ts:1"
}

# ---------------------------------------------------------------------------
# AC3
# ---------------------------------------------------------------------------

@test "AC3_state_file_gets_security_triggers_line" {
  printf '%s\n' "// x" "const a = 1;" "jwt.verify(t, s);" > "$WORK/src/a.ts"
  scan --files "src/a.ts" --state-file "$WORK/build-state.yaml"
  [ "$status" -eq 0 ]
  grep -q '^security_triggers: \[.*AUTH=src/a\.ts:3.*\]$' "$WORK/build-state.yaml"
}

@test "AC3_two_runs_leave_one_of_each_key_and_second_run_wins" {
  printf '%s\n' "// x" "const a = 1;" "jwt.verify(t, s);" > "$WORK/src/a.ts"
  printf '%s\n' "console.log('hello');" > "$WORK/src/b.ts"
  scan --files "src/a.ts" --state-file "$WORK/build-state.yaml"
  [ "$status" -eq 0 ]
  scan --files "src/b.ts" --state-file "$WORK/build-state.yaml"
  [ "$status" -eq 0 ]
  [ "$(grep -c '^security_triggers:' "$WORK/build-state.yaml")" -eq 1 ]
  [ "$(grep -c '^security_classification:' "$WORK/build-state.yaml")" -eq 1 ]
  [ "$(grep -c '^security_patterns_found:' "$WORK/build-state.yaml")" -eq 1 ]
  grep -qxF 'security_classification: LOW' "$WORK/build-state.yaml"
  grep -qxF 'security_triggers: []' "$WORK/build-state.yaml"
}

# ---------------------------------------------------------------------------
# AC4
# ---------------------------------------------------------------------------

@test "AC4_auth_task_text_without_files_is_high_auth_task_trigger" {
  scan --task "add JWT login to the api"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  out_has_line "security_patterns_found: AUTH"
  out_has_line "security_triggers: [AUTH=task]"
}

@test "AC4_benign_task_text_without_files_is_low_task_only" {
  scan --task "consolidate a config-file reader"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: LOW"
  out_has_line "security_patterns_found: task_only"
  out_has_line "security_triggers: []"
}

@test "AC4_GUARD_no_files_and_no_task_is_medium_unanalyzed" {
  scan
  [ "$status" -eq 0 ]
  out_has_line "security_classification: MEDIUM"
  out_has_line "security_patterns_found: unanalyzed"
}

# ---------------------------------------------------------------------------
# AC5
# ---------------------------------------------------------------------------

@test "AC5_benign_task_plus_jwt_file_is_high" {
  printf '%s\n' "// x" "const a = 1;" "jwt.verify(t, s);" > "$WORK/src/a.ts"
  scan --task "rename a helper function" --files "src/a.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  assert_trigger "AUTH=src/a\\.ts:3"
}

@test "AC5_auth_task_plus_benign_file_is_high" {
  printf '%s\n' "console.log('hello');" > "$WORK/src/b.ts"
  scan --task "add jwt login" --files "src/b.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  assert_trigger "AUTH=task"
}

# ---------------------------------------------------------------------------
# AC6
# ---------------------------------------------------------------------------

@test "AC6_planned_auth_path_that_does_not_exist_is_high_auth_path_trigger" {
  [ ! -e "$WORK/src/auth/login.ts" ]
  scan --files "src/auth/login.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  out_has_line "security_patterns_found: AUTH"
  out_has_line "security_triggers: [AUTH=src/auth/login.ts]"
}

@test "AC6_GUARD_planned_neutral_path_that_does_not_exist_is_not_high" {
  [ ! -e "$WORK/src/config/reader.ts" ]
  scan --files "src/config/reader.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: LOW"
}

# ---------------------------------------------------------------------------
# AC7
# ---------------------------------------------------------------------------

@test "AC7_word_boundaries_author_hashtable_tokenizer_sessionStorage_process_env_not_high" {
  printf '%s\n' "the author signed the design; hashtable; tokenizer; sessionStorage; process.env.NODE_ENV" > "$WORK/src/w.ts"
  scan --files "src/w.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: LOW"
  out_has_line "security_patterns_found: none"
}

@test "AC7_hash_comment_password_is_auth_with_provenance" {
  printf '%s\n' "# password" > "$WORK/src/p.py"
  scan --files "src/p.py"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  out_has_line "security_patterns_found: AUTH"
  assert_trigger "AUTH=src/p\\.py:1"
}

# ---------------------------------------------------------------------------
# AC8
# ---------------------------------------------------------------------------

@test "AC8_legacy_1_restores_old_substring_patterns_and_still_writes_triggers" {
  write_ac1_file
  run env BD_SECURITY_SCAN_LEGACY=1 bash "$SCRIPT" --cwd "$WORK" --files "src/ac1.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  out_has_line "security_patterns_found: AUTH,CRYPTO,SECRETS"
  printf '%s\n' "$output" | grep -q '^security_triggers: \['
}

@test "AC8_legacy_1_ignores_task_so_no_files_is_medium_unanalyzed" {
  run env BD_SECURITY_SCAN_LEGACY=1 bash "$SCRIPT" --cwd "$WORK" --task "add jwt"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: MEDIUM"
  out_has_line "security_patterns_found: unanalyzed"
}

@test "AC8_legacy_1_does_not_scan_path_strings" {
  run env BD_SECURITY_SCAN_LEGACY=1 bash "$SCRIPT" --cwd "$WORK" --files "src/auth/login.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: LOW"
  printf '%s\n' "$output" | grep -q '^security_triggers: \[\]$'
}

@test "AC8_legacy_values_0_true_empty_yes_use_default_mode" {
  write_ac1_file
  local v
  for v in 0 true "" yes; do
    run env BD_SECURITY_SCAN_LEGACY="$v" bash "$SCRIPT" --cwd "$WORK" --files "src/ac1.ts"
    [ "$status" -eq 0 ]
    printf '%s\n' "$output" | grep -qxF "security_classification: MEDIUM" \
      || { echo "value [$v]: $output"; return 1; }
    printf '%s\n' "$output" | grep -qxF "security_patterns_found: DATA" \
      || { echo "value [$v]: $output"; return 1; }
  done
}

# ---------------------------------------------------------------------------
# AC9 (docs)
# ---------------------------------------------------------------------------

# Print each security-scan.sh invocation (backslash continuations joined) on one line.
scan_invocations() {
  awk '
    function flush() { if (cur != "") print cur; cur = "" }
    {
      if (cur != "" || index($0, "security-scan.sh") > 0) {
        line = $0
        cont = (line ~ /\\[[:space:]]*$/)
        sub(/\\[[:space:]]*$/, "", line)
        cur = cur " " line
        if (!cont) flush()
      }
    }
    END { flush() }
  ' "$1"
}

@test "AC9_phase05_passes_task_to_the_scan_and_names_the_kill_switch" {
  [ -f "$PHASE05" ]
  scan_invocations "$PHASE05" | grep -qE -- '--task'
  grep -q 'BD_SECURITY_SCAN_LEGACY' "$PHASE05"
}

@test "AC9_phase05_git_ls_files_only_inside_a_block_naming_the_kill_switch" {
  [ -f "$PHASE05" ]
  run awk '
    function flush() {
      if (blk ~ /git ls-files/) { seen = 1; if (blk !~ /BD_SECURITY_SCAN_LEGACY/) bad = 1 }
      blk = ""
    }
    /^```/ { if (infence) { blk = blk "\n" $0; flush(); infence = 0 } else { flush(); infence = 1; blk = $0 } ; next }
    infence { blk = blk "\n" $0; next }
    /^[[:space:]]*$/ { flush(); next }
    { blk = blk "\n" $0 }
    END { flush(); print "bad=" bad+0 " seen=" seen+0; exit (bad ? 1 : 0) }
  ' "$PHASE05"
  [ "$status" -eq 0 ]
  # The kill switch must be named in the doc at all (else the block check is vacuous).
  grep -q 'BD_SECURITY_SCAN_LEGACY' "$PHASE05"
}

@test "AC9_phase45_reruns_scan_with_both_task_and_files" {
  [ -f "$PHASE45" ]
  local found=1 inv
  while IFS= read -r inv; do
    if [[ "$inv" == *"--task"* && "$inv" == *"--files"* ]]; then found=0; fi
  done < <(scan_invocations "$PHASE45")
  [ "$found" -eq 0 ]
}
