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

# ---------------------------------------------------------------------------
# Delta r2 (spec section 5): AC11-AC18
# ---------------------------------------------------------------------------

# Write $1 as the only content line of src/probe.txt, scan it, require HIGH.
assert_content_high() {
  printf '%s\n' "$1" > "$WORK/src/probe.txt"
  scan --files "src/probe.txt"
  [ "$status" -eq 0 ] || { echo "input [$1]: exit $status"; return 1; }
  out_has_line "security_classification: HIGH" \
    || { echo "input [$1] not HIGH: $output"; return 1; }
}

# Scan an absent planned path $1, require HIGH.
assert_path_high() {
  [ ! -e "$WORK/$1" ]
  scan --files "$1"
  [ "$status" -eq 0 ] || { echo "input [$1]: exit $status"; return 1; }
  out_has_line "security_classification: HIGH" \
    || { echo "input [$1] not HIGH: $output"; return 1; }
}

# Scan task text $1 with no files, require HIGH.
assert_task_high() {
  scan --task "$1"
  [ "$status" -eq 0 ] || { echo "input [$1]: exit $status"; return 1; }
  out_has_line "security_classification: HIGH" \
    || { echo "input [$1] not HIGH: $output"; return 1; }
}

# Guard: content $1 must exit 0 and not be HIGH.
assert_content_not_high() {
  printf '%s\n' "$1" > "$WORK/src/probe.txt"
  scan --files "src/probe.txt"
  [ "$status" -eq 0 ] || { echo "input [$1]: exit $status"; return 1; }
  if out_has_line "security_classification: HIGH"; then
    echo "input [$1] wrongly HIGH: $output"; return 1
  fi
  printf '%s\n' "$output" | grep -q '^security_classification: ' \
    || { echo "input [$1] no classification line: $output"; return 1; }
}

# --- AC11 content plurals/phrases ---

@test "AC11_content_passwords_table_is_high" { assert_content_high "passwords table"; }
@test "AC11_content_hashed_passwords_is_high" { assert_content_high "hashed_passwords"; }
@test "AC11_content_user_sessions_is_high" { assert_content_high "user sessions"; }
@test "AC11_content_API_keys_is_high" { assert_content_high "API keys"; }
@test "AC11_content_bearer_tokens_is_high" { assert_content_high "bearer tokens"; }
@test "AC11_content_private_key_is_high" { assert_content_high "private key"; }
@test "AC11_content_BEGIN_RSA_PRIVATE_KEY_is_high" { assert_content_high "BEGIN RSA PRIVATE KEY"; }
@test "AC11_content_id_rsa_is_high" { assert_content_high "id_rsa"; }
@test "AC11_content_ssh_key_is_high" { assert_content_high "ssh key"; }
@test "AC11_content_authz_is_high" { assert_content_high "authz"; }
@test "AC11_content_ldap_bind_is_high" { assert_content_high "ldap bind"; }

# --- AC12 identifiers in content ---

@test "AC12_identifier_refresh_token_is_high" { assert_content_high "refresh_token"; }
@test "AC12_identifier_jwtToken_is_high" { assert_content_high "jwtToken"; }
@test "AC12_identifier_sessionId_is_high" { assert_content_high "sessionId"; }
@test "AC12_identifier_SessionMiddleware_is_high" { assert_content_high "SessionMiddleware"; }
@test "AC12_identifier_authMiddleware_is_high" { assert_content_high "authMiddleware"; }

# --- AC13 planned paths (files absent) ---

@test "AC13_planned_path_authService_ts_is_high" { assert_path_high "src/authService.ts"; }
@test "AC13_planned_path_AuthService_ts_is_high" { assert_path_high "src/AuthService.ts"; }
@test "AC13_planned_path_token_store_py_is_high" { assert_path_high "src/token_store.py"; }
@test "AC13_planned_path_session_manager_rb_is_high" { assert_path_high "src/session_manager.rb"; }

# --- AC14 task text, no files ---

@test "AC14_task_hash_passwords_with_salt_is_high" { assert_task_high "hash passwords with salt"; }
@test "AC14_task_rotate_API_keys_is_high" { assert_task_high "rotate API keys"; }
@test "AC14_task_store_user_token_in_cookie_is_high" { assert_task_high "store user token in cookie"; }
@test "AC14_task_sign_webhook_payloads_is_high" { assert_task_high "sign webhook payloads"; }
@test "AC14_task_enable_2FA_is_high" { assert_task_high "enable 2FA"; }
@test "AC14_task_require_MFA_is_high" { assert_task_high "require MFA"; }
@test "AC14_task_fix_CSRF_is_high" { assert_task_high "fix CSRF"; }
@test "AC14_task_rename_getApiKey_helper_is_high" { assert_task_high "rename getApiKey helper"; }
@test "AC14_task_upgrade_OAuth2_flow_is_high" { assert_task_high "upgrade OAuth2 flow"; }

# --- AC15 guards: never HIGH, exit 0 ---

@test "AC15_GUARD_reference_session_token_hash_sign_not_high" { assert_content_not_high "session token hash sign"; }
@test "AC15_GUARD_bare_tokens_not_high" { assert_content_not_high "tokens"; }
@test "AC15_GUARD_bare_sessions_not_high" { assert_content_not_high "sessions"; }
@test "AC15_GUARD_sessionStorage_not_high" { assert_content_not_high "sessionStorage"; }
@test "AC15_GUARD_author_authority_not_high" { assert_content_not_high "author authority"; }
@test "AC15_GUARD_process_env_NODE_ENV_not_high" { assert_content_not_high "process.env.NODE_ENV"; }
@test "AC15_GUARD_tokenizer_hashtable_not_high" { assert_content_not_high "tokenizer hashtable"; }

@test "AC15_GUARD_planned_path_src_config_reader_ts_not_high" {
  [ ! -e "$WORK/src/config/reader.ts" ]
  scan --files "src/config/reader.ts"
  [ "$status" -eq 0 ]
  if out_has_line "security_classification: HIGH"; then echo "wrongly HIGH: $output"; return 1; fi
  printf '%s\n' "$output" | grep -q '^security_classification: '
}

@test "AC15_GUARD_task_consolidate_a_config_file_reader_not_high" {
  scan --task "consolidate a config-file reader"
  [ "$status" -eq 0 ]
  if out_has_line "security_classification: HIGH"; then echo "wrongly HIGH: $output"; return 1; fi
  printf '%s\n' "$output" | grep -q '^security_classification: '
}

# --- AC16 $TASK definition line in both phase docs ---

# The exact spec section 5.5 line.
TASK_LINE=$'TASK=$(sed -n \'s/^task: *//p\' build-state.yaml | head -1 | sed \'s/^"//; s/"$//\')'

assert_task_line_works() {
  local f="$1" n m line got v want
  # Exactly one column-0 line equal to the section 5.5 line (exact compare).
  n="$(grep -cxF -- "$TASK_LINE" "$f" || true)"
  [ "$n" -eq 1 ] || { echo "$f: expected exactly one exact TASK line, found $n"; return 1; }
  # No other column-0 TASK= line (a hardcoded TASK="..." must fail).
  m="$(grep -c '^TASK=' "$f" || true)"
  [ "$m" -eq 1 ] || { echo "$f: expected exactly one ^TASK= line, found $m"; return 1; }
  line="$(grep -xF -- "$TASK_LINE" "$f")"
  for v in "Add user authentication" "Fix the login page"; do
    printf 'task: "%s"\ncomplexity: FEATURE\n' "$v" > "$WORK/build-state.yaml"
    got="$(cd "$WORK" && bash -c "$line"$'\n''printf %s "$TASK"')"
    want="$v"
    [ "$got" = "$want" ] || { echo "$f: TASK was [$got], wanted [$want]"; return 1; }
  done
}

@test "AC16_phase05_TASK_line_yields_task_from_build_state" {
  [ -f "$PHASE05" ]
  assert_task_line_works "$PHASE05"
}

@test "AC16_phase45_TASK_line_yields_task_from_build_state" {
  [ -f "$PHASE45" ]
  assert_task_line_works "$PHASE45"
}

# --- AC17 state file without trailing newline ---

# Every line of the state file is a known key, each security key appears once
# on its own line (no joined lines).
assert_state_keys_on_own_lines() {
  local f="$1" bad
  [ "$(grep -c '^security_classification: ' "$f")" -eq 1 ] || { cat "$f"; return 1; }
  [ "$(grep -c '^security_patterns_found: ' "$f")" -eq 1 ] || { cat "$f"; return 1; }
  [ "$(grep -c '^security_triggers: ' "$f")" -eq 1 ] || { cat "$f"; return 1; }
  bad="$(grep -vE '^(k|other|security_classification|security_patterns_found|security_triggers): ' "$f" || true)"
  [ -z "$bad" ] || { echo "unexpected/joined lines: [$bad]"; cat "$f"; return 1; }
}

@test "AC17_stale_classification_as_last_line_without_newline_is_replaced_and_keys_on_own_lines" {
  printf '%s\n' "console.log('hello');" > "$WORK/src/b.ts"
  printf 'k: v\nother: x\nsecurity_classification: OLD' > "$WORK/state"
  scan --files "src/b.ts" --state-file "$WORK/state"
  [ "$status" -eq 0 ]
  grep -qxF 'k: v' "$WORK/state"
  grep -qxF 'other: x' "$WORK/state"
  ! grep -q 'OLD' "$WORK/state"
  assert_state_keys_on_own_lines "$WORK/state"
}

@test "AC17_no_key_present_and_no_trailing_newline_keeps_last_line_and_keys_on_own_lines" {
  printf '%s\n' "console.log('hello');" > "$WORK/src/b.ts"
  printf 'k: v' > "$WORK/state"
  scan --files "src/b.ts" --state-file "$WORK/state"
  [ "$status" -eq 0 ]
  grep -qxF 'k: v' "$WORK/state"
  assert_state_keys_on_own_lines "$WORK/state"
}

# --- AC18 legacy parity ---

@test "AC18_legacy_1_does_not_split_identifiers_getApiKey_content_stays_not_high" {
  printf '%s\n' "getApiKey()" > "$WORK/src/probe.txt"
  run env BD_SECURITY_SCAN_LEGACY=1 bash "$SCRIPT" --cwd "$WORK" --files "src/probe.txt"
  [ "$status" -eq 0 ]
  if out_has_line "security_classification: HIGH"; then echo "legacy wrongly HIGH: $output"; return 1; fi
  printf '%s\n' "$output" | grep -q '^security_classification: ' || { echo "$output"; return 1; }
}

@test "AC18_legacy_1_reference_result_AUTH_CRYPTO_SECRETS_unchanged" {
  printf '%s\n' "session token hash sign" > "$WORK/src/ref.ts"
  run env BD_SECURITY_SCAN_LEGACY=1 bash "$SCRIPT" --cwd "$WORK" --files "src/ref.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  out_has_line "security_patterns_found: AUTH,CRYPTO,SECRETS"
}

# --- AC19 raw pass (no digit split, no single-letter-run split) ---

@test "AC19_raw_pass_content_2FA_is_high" { assert_content_high "2FA"; }
@test "AC19_raw_pass_content_OAuth2_is_high" { assert_content_high "OAuth2"; }
@test "AC19_raw_pass_content_JWTToken_is_high" { assert_content_high "JWTToken"; }

# --- AC20 invalid UTF-8 byte before a hit, UTF-8 locale forced ---

@test "AC20_invalid_byte_line1_password_line2_is_high_with_line2_trigger_under_utf8_locale" {
  printf '\xff\npassword\n' > "$WORK/src/bad.txt"
  run env -u BD_SECURITY_SCAN_LEGACY LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    bash "$SCRIPT" --cwd "$WORK" --files "src/bad.txt"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  assert_trigger "AUTH=src/bad\\.txt:2"
}

# --- AC21 trigger labels name the original path / line, never split text ---

@test "AC21_planned_path_authService_ts_trigger_is_exact_original_path" {
  [ ! -e "$WORK/src/authService.ts" ]
  scan --files "src/authService.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_triggers: [AUTH=src/authService.ts]"
}

@test "AC21_getApiKey_on_line_3_trigger_is_SECRETS_original_path_line_3" {
  printf '%s\n' "// x" "const a = 1;" "getApiKey()" > "$WORK/src/c.ts"
  scan --files "src/c.ts"
  [ "$status" -eq 0 ]
  out_has_line "security_classification: HIGH"
  out_has_line "security_triggers: [SECRETS=src/c.ts:3]"
}
