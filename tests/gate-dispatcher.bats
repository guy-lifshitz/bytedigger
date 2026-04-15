#!/usr/bin/env bats
# RED tests for scripts/gate-dispatcher.sh
# All tests MUST fail until dispatcher is implemented in Phase 5.2 GREEN.
#
# Spec ref: build-spec.md §7.2 D1–D8
#   - Routing per backend (bash / ts / shadow / unknown / missing config)
#   - Fail-closed on missing bun (Story 3, commit 42f72651)
#   - Shadow mode JSONL mismatch logging (Story 4, commit 30583611)
#   - GATE_BACKEND env-var override (plan-review §7.1 R2)

DISPATCHER="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/gate-dispatcher.sh"
BASH_GATE="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/build-gate.sh"
PLUGIN_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
  TMPDIR="$(mktemp -d)"
  export TMPDIR
  export BYTEDIGGER_PLUGIN_ROOT="$PLUGIN_ROOT"
  export BYTEDIGGER_CONFIG="$TMPDIR/bytedigger.json"
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "tdd_mandatory": true
}
EOF
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "4"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
EOF
}

teardown() {
  rm -rf "$TMPDIR"
}

# ---------------------------------------------------------------------------
# D1 — gate_backend absent → routes to bash
# ---------------------------------------------------------------------------
@test "D1 dispatcher: gate_backend absent routes to bash, exit code matches direct bash invocation" {
  cd "$TMPDIR"
  run bash "$DISPATCHER" < /dev/null
  dispatcher_status=$status
  run bash "$BASH_GATE" < /dev/null
  bash_status=$status
  [ "$dispatcher_status" -eq "$bash_status" ]
}

# ---------------------------------------------------------------------------
# D2 — gate_backend: bash → routes to bash
# ---------------------------------------------------------------------------
@test "D2 dispatcher: gate_backend=bash routes to bash" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "gate_backend": "bash"
}
EOF
  cd "$TMPDIR"
  run bash "$DISPATCHER" < /dev/null
  dispatcher_status=$status
  run bash "$BASH_GATE" < /dev/null
  [ "$dispatcher_status" -eq "$status" ]
}

# ---------------------------------------------------------------------------
# D3 — gate_backend: ts + bun present → routes to ts
# ---------------------------------------------------------------------------
@test "D3 dispatcher: gate_backend=ts with bun present invokes bun build-phase-gate.ts" {
  command -v bun >/dev/null 2>&1 || skip "bun not installed"
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "gate_backend": "ts"
}
EOF
  cd "$TMPDIR"
  run bash "$DISPATCHER" < /dev/null
  # GREEN expectation: exit 2 (soft block, no build-architecture.md). RED stub returns 99 → fails.
  [ "$status" -eq 2 ]
}

# ---------------------------------------------------------------------------
# D4 — gate_backend: ts + bun MISSING → fail-closed hard block (Story 3, commit 42f72651)
# ---------------------------------------------------------------------------
@test "D4 dispatcher: gate_backend=ts without bun on PATH fails closed (exit 1)" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "gate_backend": "ts"
}
EOF
  cd "$TMPDIR"
  # LEGITIMATE_REFACTOR: intent is "no bun on PATH", not "no shell binaries".
  # The original env -i PATH=fake_path wiped bash/env themselves → exit 127.
  # Keep /bin and /usr/bin so `bash` and `env` resolve, but exclude any dir
  # containing a `bun` binary.
  fake_path="$TMPDIR/fakepath:/bin:/usr/bin"
  mkdir -p "$TMPDIR/fakepath"
  run env -i PATH="$fake_path" HOME="$HOME" TMPDIR="$TMPDIR" \
        BYTEDIGGER_CONFIG="$BYTEDIGGER_CONFIG" \
        BYTEDIGGER_PLUGIN_ROOT="$BYTEDIGGER_PLUGIN_ROOT" \
        bash "$DISPATCHER" < /dev/null
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "gate_backend=ts but bun not found"
  # Must NOT silently fall back to bash
  ! echo "$output" | grep -q "phase_4_architect"
}

# ---------------------------------------------------------------------------
# D5 — shadow mode, verdicts agree → no mismatch entry, returns bash verdict
# ---------------------------------------------------------------------------
@test "D5 dispatcher: shadow mode with matching verdicts returns bash verdict, no mismatch logged" {
  command -v bun >/dev/null 2>&1 || skip "bun not installed"
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "gate_backend": "shadow"
}
EOF
  cd "$TMPDIR"
  rm -rf "$TMPDIR/.bytedigger/gate-shadow"
  run bash "$DISPATCHER" < /dev/null
  bash_status=$status
  # Should match a direct bash run
  run bash "$BASH_GATE" < /dev/null
  [ "$bash_status" -eq "$status" ]
  # No mismatch file (or empty) — volume control per commit 30583611
  if [ -f "$TMPDIR/.bytedigger/gate-shadow/mismatches.jsonl" ]; then
    [ ! -s "$TMPDIR/.bytedigger/gate-shadow/mismatches.jsonl" ]
  fi
}

# ---------------------------------------------------------------------------
# D6 — shadow mode, verdicts differ → mismatch JSONL is written with correct shape
#
# LEGITIMATE_REFACTOR: previous assertion (`wc -l ≥ 0`) was tautological AND
# wrapped in `if -f file` so the whole block was skipped when mismatches.jsonl
# didn't exist. Rewriter 5 (G2) flagged this as BLOCKING: the test exercised
# zero code in GREEN. This version forces divergence via GATE_DISPATCHER_*_OVERRIDE
# stubs and asserts the file exists + has exactly one record + the record has
# the expected JSON keys with the right types.
# ---------------------------------------------------------------------------
@test "D6 dispatcher: shadow mode with diverging verdicts writes JSONL entry (forced divergence)" {
  command -v bun >/dev/null 2>&1 || skip "bun not installed"
  command -v python3 >/dev/null 2>&1 || skip "python3 not installed"
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "gate_backend": "shadow"
}
EOF
  cat > "$TMPDIR/build-state.yaml" <<EOF
task: "test"
complexity: FEATURE
mode: AUTONOMOUS
current_phase: "4"
last_updated: "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
phase_4_architect: complete
EOF
  # Force divergence: bash stub prints a pass verdict; TS stub prints a
  # hard-block verdict. Different stdout AND different exit codes → the
  # mismatch logger MUST fire.
  cat > "$TMPDIR/fake-bash-gate.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' '{"decision":"pass"}'
exit 0
EOF
  chmod +x "$TMPDIR/fake-bash-gate.sh"
  cat > "$TMPDIR/fake-ts-gate.ts" <<'EOF'
process.stdout.write(JSON.stringify({decision: "block", reason: "HARD BLOCK: forced divergence with embedded \"quote\" and backslash \\"}) + "\n");
process.exit(1);
EOF

  cd "$TMPDIR"
  GATE_DISPATCHER_BASH_TEST_OVERRIDE="$TMPDIR/fake-bash-gate.sh" \
  GATE_DISPATCHER_TS_TEST_OVERRIDE="$TMPDIR/fake-ts-gate.ts" \
    run bash "$DISPATCHER" < /dev/null
  # Dispatcher returns the bash (stub) verdict.
  [ "$status" -eq 0 ]
  # The mismatch file MUST exist and be non-empty.
  [ -f "$TMPDIR/.bytedigger/gate-shadow/mismatches.jsonl" ]
  [ -s "$TMPDIR/.bytedigger/gate-shadow/mismatches.jsonl" ]
  # Exactly one record, valid JSON with the six required keys + correct types.
  record_count=$(wc -l < "$TMPDIR/.bytedigger/gate-shadow/mismatches.jsonl")
  [ "$record_count" -eq 1 ]
  run python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    rec = json.loads(f.read().strip())
assert set(["ts","bash_code","ts_code","bash_stdout","ts_stdout"]).issubset(rec.keys()), rec
assert isinstance(rec["bash_code"], int), rec["bash_code"]
assert isinstance(rec["ts_code"], int), rec["ts_code"]
assert rec["bash_code"] == 0, rec["bash_code"]
assert rec["ts_code"] == 1, rec["ts_code"]
assert "HARD BLOCK" in rec["ts_stdout"], rec["ts_stdout"]
# Embedded-quote + backslash round-trip safety:
assert "quote" in rec["ts_stdout"], rec["ts_stdout"]
assert rec["ts"].endswith("Z"), rec["ts"]
print("OK")
' "$TMPDIR/.bytedigger/gate-shadow/mismatches.jsonl"
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "OK"
}

# ---------------------------------------------------------------------------
# D7 — unknown gate_backend value → defaults to bash with stderr warning
# ---------------------------------------------------------------------------
@test "D7 dispatcher: unknown gate_backend value defaults to bash with stderr warning" {
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "gate_backend": "garbage"
}
EOF
  cd "$TMPDIR"
  run bash "$DISPATCHER" < /dev/null
  dispatcher_status=$status
  run bash "$BASH_GATE" < /dev/null
  [ "$dispatcher_status" -eq "$status" ]
  # Warning expected on stderr (captured by `run` in $output combined; we check substring)
  run bash -c "bash '$DISPATCHER' < /dev/null 2>&1 >/dev/null"
  echo "$output" | grep -q "unknown gate_backend"
}

# ---------------------------------------------------------------------------
# D8 — config file missing → defaults to bash
# ---------------------------------------------------------------------------
@test "D8 dispatcher: missing bytedigger.json defaults to bash" {
  rm -f "$TMPDIR/bytedigger.json"
  cd "$TMPDIR"
  run bash "$DISPATCHER" < /dev/null
  dispatcher_status=$status
  run bash "$BASH_GATE" < /dev/null
  [ "$dispatcher_status" -eq "$status" ]
}

# ---------------------------------------------------------------------------
# D9 (plan-review §7.1 R2) — GATE_BACKEND env var overrides JSON config
# ---------------------------------------------------------------------------
@test "D9 dispatcher: GATE_BACKEND env var overrides gate_backend in config" {
  command -v bun >/dev/null 2>&1 || skip "bun not installed"
  # Config says bash, env var says ts → must route to ts
  cat > "$TMPDIR/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "gate_backend": "bash"
}
EOF
  cd "$TMPDIR"
  GATE_BACKEND=ts run bash "$DISPATCHER" < /dev/null
  # GREEN: TS gate at phase 4 missing build-architecture.md → exit 2
  [ "$status" -eq 2 ]
}

# ---------------------------------------------------------------------------
# D10 — stdin must NOT be drained (safety: harness pipes JSON, dispatcher must passthrough)
# ---------------------------------------------------------------------------
@test "D10 dispatcher: piping JSON to stdin completes within 5s (no stall)" {
  cd "$TMPDIR"
  payload='{"type":"subagentStop","session_id":"abc","exit_code":0}'
  run perl -e 'alarm 5; exec @ARGV' bash -c "printf '%s\n' '$payload' | bash '$DISPATCHER'"
  [ "$status" -ne 142 ]
  # Tighten: must be a known valid gate exit code (pass/hard/soft).
  [ "$status" -eq 0 ] || [ "$status" -eq 1 ] || [ "$status" -eq 2 ]
}
