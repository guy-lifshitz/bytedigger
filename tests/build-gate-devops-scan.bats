#!/usr/bin/env bats
# RED tests: devops_scan wiring in scripts/build-gate.sh (gate_phase_55, SHADOW by default).
# Spec (r2): docs/decisions/2026-10-03-s4-wire-devops-scan-build-gate.md
# Gate report: docs/decisions/2026-10-03-s4-wire-gate-r1.md
#
# Hermetic: the scan script is replaced by a tiny fake python3 file selected via
# DEVOPS_SCAN_SCRIPT_TEST_OVERRIDE. The fake records its argv in a marker file and
# exits with a chosen rc after printing chosen stdout. "Today" is pinned via
# BD_GATE_TODAY_TEST_OVERRIDE.

SCRIPT="${GATE_SCRIPT:-$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)/scripts/build-gate.sh}"

setup() {
  WORK="$(mktemp -d)"
  export WORK
  cat > "$WORK/bytedigger.json" <<'EOF'
{
  "gates_enabled": true,
  "tdd_mandatory": true
}
EOF
  export BYTEDIGGER_CONFIG="$WORK/bytedigger.json"
  MARKER="$WORK/scan-ran.marker"
  PIDFILE="$WORK/fake.pid"
  EVENTS="$WORK/.bytedigger/devops-scan-shadow.jsonl"
  FAKE="$WORK/fake_devops_scan.py"
  REAL_PY="$(command -v python3)"
  export DEVOPS_SCAN_SCRIPT_TEST_OVERRIDE="$FAKE"
  export BD_GATE_TODAY_TEST_OVERRIDE="2026-10-03"
  unset DEVOPS_SCAN_ENFORCE
}

teardown() {
  # never leave a sleeping fake behind
  if [ -s "${PIDFILE:-/nonexistent}" ]; then
    kill -9 "$(cat "$PIDFILE")" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}

# write_state <phase> [extra yaml lines...]
write_state() {
  local phase="$1"; shift
  {
    echo 'task: "test"'
    echo 'complexity: FEATURE'
    echo 'mode: AUTONOMOUS'
    echo "current_phase: \"$phase\""
    echo 'test_integrity_check: pass'
    for l in "$@"; do echo "$l"; done
  } > "$WORK/build-state.yaml"
}

# write_state_missing_deliverable: 5.5 state WITHOUT test_integrity_check
write_state_missing_deliverable() {
  {
    echo 'task: "test"'
    echo 'complexity: FEATURE'
    echo 'mode: AUTONOMOUS'
    echo 'current_phase: "5.5"'
  } > "$WORK/build-state.yaml"
}

# make_fake <rc> <stdout-text>
make_fake() {
  local rc="$1" out="$2"
  cat > "$FAKE" <<PYEOF
import sys
with open("$MARKER", "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
sys.stdout.write("""$out""")
sys.exit($rc)
PYEOF
}

BLOCKED_JSON='{"status":"blocked","gating":[{"id":"F-001"},{"id":"F-002"}],"waived":[],"nongating":[],"reason":"hardcoded_secret"}'
UNAVAIL_JSON='{"status":"unavailable","gating":[],"waived":[],"nongating":[],"reason":"root_not_found"}'
CLEAN_JSON='{"status":"clean","gating":[],"waived":[],"nongating":[],"reason":""}'

# run_gate [PATH override]; sets GATE_RC, GATE_OUT, GATE_ERR
run_gate() {
  GATE_RC=0
  if [ -n "${1:-}" ]; then
    PATH="$1" bash "$SCRIPT" < /dev/null > "$WORK/out" 2> "$WORK/err" || GATE_RC=$?
  else
    bash "$SCRIPT" < /dev/null > "$WORK/out" 2> "$WORK/err" || GATE_RC=$?
  fi
  GATE_OUT="$(cat "$WORK/out")"
  GATE_ERR="$(cat "$WORK/err")"
}

# last_event <dotted.field>  (always uses the real python3, never a shim)
last_event() {
  "$REAL_PY" - "$EVENTS" "$1" <<'PYEOF'
import json, sys
lines = [l for l in open(sys.argv[1]).read().splitlines() if l.strip()]
d = json.loads(lines[-1])
for k in sys.argv[2].split("."):
    d = d[k]
print(d)
PYEOF
}

# ---------------------------------------------------------------------------
@test "AC1: SHADOW rc 1 -> exit 0, empty stdout, WARN, event with flag owner/expiry, scan ran once with --root/--timeout 3" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [ -z "$GATE_OUT" ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW blocked: 2 gating finding(s)"* ]]
  [ -f "$EVENTS" ]
  [ "$(last_event mode)" = "shadow" ]
  [ "$(last_event rc)" = "1" ]
  [ "$(last_event gating)" = "2" ]
  [ "$(last_event flag.name)" = "DEVOPS_SCAN_ENFORCE" ]
  [ "$(last_event flag.owner)" = "s4-bytedigger (MGR)" ]
  [ "$(last_event flag.expires)" = "2026-10-17" ]
  # exactly one run, full tree, exact argv (no --files)
  [ "$(wc -l < "$MARKER" | tr -d ' ')" = "1" ]
  [ "$(cat "$MARKER")" = "--root $WORK --timeout 3" ]
  ! grep -q -- "--files" "$MARKER"
}

@test "AC2: SHADOW rc 2 -> exit 0, WARN names reason, event present" {
  write_state "5.5"
  make_fake 2 "$UNAVAIL_JSON"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW unavailable"* ]]
  [[ "$GATE_ERR" == *"root_not_found"* ]]
  [ -f "$EVENTS" ]
  [ "$(last_event rc)" = "2" ]
  [ "$(last_event status)" = "unavailable" ]
}

@test "AC2: SHADOW rc 0 -> scan ran, no devops_scan WARN, no event file" {
  write_state "5.5"
  make_fake 0 "$CLEAN_JSON"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [ -f "$MARKER" ]
  [[ "$GATE_ERR" != *"devops_scan"* ]]
  [ ! -e "$EVENTS" ]
}

# ---------------------------------------------------------------------------
@test "AC3: ENFORCE=1 rc 1 -> exit 1 HARD BLOCK devops_scan blocked with count and ids" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *'{"decision":"block"'* ]]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan blocked"* ]]
  [[ "$GATE_OUT" == *"2 gating finding(s)"* ]]
  [[ "$GATE_OUT" == *"F-001"* ]]
  [[ "$GATE_OUT" == *"F-002"* ]]
}

@test "AC3: ENFORCE=1 rc 2 -> exit 1 HARD BLOCK devops_scan unavailable with reason" {
  write_state "5.5"
  make_fake 2 "$UNAVAIL_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *'{"decision":"block"'* ]]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan unavailable"* ]]
  [[ "$GATE_OUT" == *"root_not_found"* ]]
}

@test "AC3: ENFORCE=1 rc 0 -> exit 0, scan ran, no event" {
  write_state "5.5"
  make_fake 0 "$CLEAN_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [ -f "$MARKER" ]
  [ ! -e "$EVENTS" ]
}

@test "AC3: ENFORCE hard block is not bypassed by loop prevention (counter > 3)" {
  write_state "5.5" "gate_block_counter: 9" "gate_block_phase: 5.5"
  make_fake 1 "$BLOCKED_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan blocked"* ]]
}

# ---------------------------------------------------------------------------
@test "AC4: only exactly 1 enforces; true / 0 / empty / yes / 01 behave as SHADOW" {
  make_fake 1 "$BLOCKED_JSON"
  for v in true 0 "" yes 01; do
    rm -rf "$WORK/.bytedigger" "$MARKER"
    write_state "5.5"
    export DEVOPS_SCAN_ENFORCE="$v"
    run_gate
    [ "$GATE_RC" -eq 0 ]
    [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
    [ "$(last_event mode)" = "shadow" ]
  done
}

# ---------------------------------------------------------------------------
@test "AC5: SHADOW script missing -> exit 0 + WARN unavailable + event" {
  write_state "5.5"
  export DEVOPS_SCAN_SCRIPT_TEST_OVERRIDE="$WORK/does-not-exist.py"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW unavailable"* ]]
  [ "$(last_event status)" = "unavailable" ]
}

@test "AC5: ENFORCE script missing -> hard block unavailable" {
  write_state "5.5"
  export DEVOPS_SCAN_SCRIPT_TEST_OVERRIDE="$WORK/does-not-exist.py"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan unavailable"* ]]
}

@test "AC5: SHADOW non-JSON stdout (rc 0) -> exit 0 + WARN unavailable + event" {
  write_state "5.5"
  make_fake 0 "this is not json"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW unavailable"* ]]
  [ "$(last_event status)" = "unavailable" ]
}

@test "AC5: ENFORCE non-JSON stdout (rc 0) -> hard block unavailable" {
  write_state "5.5"
  make_fake 0 "this is not json"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan unavailable"* ]]
}

@test "AC5: SHADOW rc 7 -> exit 0 + WARN unavailable + event" {
  write_state "5.5"
  make_fake 7 "$CLEAN_JSON"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW unavailable"* ]]
  [ "$(last_event status)" = "unavailable" ]
}

@test "AC5: ENFORCE rc 7 -> hard block unavailable" {
  write_state "5.5"
  make_fake 7 "$CLEAN_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan unavailable"* ]]
}

# python3 "absent" for the scan: a PATH shim named python3 that fails (rc 127,
# empty stdout) whenever it is asked to run a devops_scan script, and otherwise
# delegates to the real python3 (the gate still needs python3 for its config
# parse). The shim records that it was reached, so the cell proves the gate
# really tried to launch the scan through python3 and handled its failure; it
# cannot pass vacuously through skip or an unrelated PATH-whitelist crash.
make_failing_python_shim() {
  local d="$WORK/shim"
  mkdir -p "$d"
  cat > "$d/python3" <<SHEOF
#!/bin/bash
case "\$*" in
  *devops_scan*) echo called >> "$WORK/shim-called"; exit 127 ;;
esac
exec "$REAL_PY" "\$@"
SHEOF
  chmod +x "$d/python3"
  echo "$d"
}

@test "AC5: SHADOW python3 failing for the scan (PATH shim) -> exit 0 + WARN unavailable + event, fake never ran" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  local shim; shim="$(make_failing_python_shim)"
  run_gate "$shim:$PATH"
  [ "$GATE_RC" -eq 0 ]
  [ -e "$WORK/shim-called" ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW unavailable"* ]]
  [ ! -e "$MARKER" ]
  [ "$(last_event status)" = "unavailable" ]
}

@test "AC5: ENFORCE python3 failing for the scan (PATH shim) -> hard block unavailable, fake never ran" {
  write_state "5.5"
  make_fake 0 "$CLEAN_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  local shim; shim="$(make_failing_python_shim)"
  run_gate "$shim:$PATH"
  [ "$GATE_RC" -eq 1 ]
  [ -e "$WORK/shim-called" ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan unavailable"* ]]
  [ ! -e "$MARKER" ]
}

# ---------------------------------------------------------------------------
# AC6 (r2): expiry degrades, never blocks.
@test "AC6: SHADOW on expiry day 2026-10-17 -> normal shadow behaviour, no expiry WARN" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  export BD_GATE_TODAY_TEST_OVERRIDE="2026-10-17"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
  [[ "$GATE_ERR" != *"window expired"* ]]
  [ -f "$MARKER" ]
  [ "$(last_event flag.expired)" = "False" ]
}

@test "AC6: SHADOW after expiry 2026-10-18 -> NOT blocked, expiry WARN names flag/owner/date, event flag.expired true, scan still ran" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  export BD_GATE_TODAY_TEST_OVERRIDE="2026-10-18"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [ -z "$GATE_OUT" ]
  [[ "$GATE_ERR" == *"WARN: DEVOPS_SCAN_ENFORCE SHADOW window expired 2026-10-17 (owner s4-bytedigger (MGR)): flip to 1 or extend"* ]]
  [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
  [ -f "$MARKER" ]
  [ "$(last_event flag.expired)" = "True" ]
}

@test "AC6: SHADOW after expiry with clean scan -> exit 0, expiry WARN, event written with flag.expired true" {
  write_state "5.5"
  make_fake 0 "$CLEAN_JSON"
  export BD_GATE_TODAY_TEST_OVERRIDE="2026-10-18"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"window expired 2026-10-17"* ]]
  [ -f "$MARKER" ]
  [ -f "$EVENTS" ]
  [ "$(last_event flag.expired)" = "True" ]
}

@test "AC6: ENFORCE=1 after expiry 2026-10-18 -> normal ENFORCE verdict (blocked), no expiry text" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  export BD_GATE_TODAY_TEST_OVERRIDE="2026-10-18"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan blocked"* ]]
  [[ "$GATE_OUT" != *"window expired"* ]]
  [ -f "$MARKER" ]
}

@test "AC6: ENFORCE=1 after expiry, clean scan -> exit 0" {
  write_state "5.5"
  make_fake 0 "$CLEAN_JSON"
  export BD_GATE_TODAY_TEST_OVERRIDE="2026-10-18"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [ -f "$MARKER" ]
}

@test "AC6: malformed BD_GATE_TODAY_TEST_OVERRIDE is ignored (real UTC date used), scan still ran, exit 0" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  local real_today; real_today="$(date -u +%F)"
  local bad
  for bad in garbage "" "2026-13-99x"; do
    rm -rf "$WORK/.bytedigger" "$MARKER"
    write_state "5.5"
    export BD_GATE_TODAY_TEST_OVERRIDE="$bad"
    run_gate
    [ "$GATE_RC" -eq 0 ]
    [ -f "$MARKER" ]
    [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
    if [[ "$real_today" > "2026-10-17" ]]; then
      [[ "$GATE_ERR" == *"window expired"* ]]
    else
      [[ "$GATE_ERR" != *"window expired"* ]]
    fi
  done
}

# ---------------------------------------------------------------------------
# AC7: scan is invoked only in phase 5.5 with active gates and fresh state.
# Guard tests: green today (feature absent), must stay green after GREEN
# (the fake is reachable via the real seam, so a wrongly-placed call would trip them).
@test "AC7: phases 5.3 / 6 / 7 never invoke the scan" {
  make_fake 1 "$BLOCKED_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  write_state "5.3" "phase_53_green: complete"
  run_gate
  [ ! -e "$MARKER" ]
  write_state "6"
  run_gate
  [ ! -e "$MARKER" ]
  write_state "7"
  run_gate
  [ ! -e "$MARKER" ]
}

@test "AC7: gates_enabled false never invokes the scan" {
  cat > "$WORK/bytedigger.json" <<'EOF'
{ "gates_enabled": false }
EOF
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [ ! -e "$MARKER" ]
}

@test "AC7: stale state (older than 600s) never invokes the scan" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  touch -t "$(date -v -700S '+%Y%m%d%H%M.%S' 2>/dev/null || date -d '700 seconds ago' '+%Y%m%d%H%M.%S')" "$WORK/build-state.yaml"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [ ! -e "$MARKER" ]
}

@test "AC7: assertion_gaming hard block in 5.5 fires before the scan" {
  write_state "5.5" "assertion_gaming_detected: true"
  make_fake 0 "$CLEAN_JSON"
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"assertion_gaming_detected"* ]]
  [ ! -e "$MARKER" ]
}

# ---------------------------------------------------------------------------
@test "AC8: event write failure (.bytedigger is a file) does not change SHADOW exit code" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  echo "i am a file" > "$WORK/.bytedigger"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  # the verdict is still surfaced even though telemetry cannot be written
  [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
}

@test "AC8: event write failure does not change ENFORCE exit code" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  echo "i am a file" > "$WORK/.bytedigger"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan blocked"* ]]
}

# ---------------------------------------------------------------------------
# AC9: wall-clock budget (8 s constant; hook harness timeout is 15 s).
make_sleeping_fake() {
  cat > "$FAKE" <<PYEOF
import os, sys, time
with open("$MARKER", "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
with open("$PIDFILE", "w") as f:
    f.write(str(os.getpid()))
time.sleep(30)
sys.stdout.write('$CLEAN_JSON')
sys.exit(0)
PYEOF
}

# assert_fake_dead: the sleeping fake was started and is no longer running
assert_fake_dead() {
  [ -s "$PIDFILE" ]
  local pid; pid="$(cat "$PIDFILE")"
  local i
  for i in 1 2 3 4 5 6; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.5
  done
  return 1
}

@test "AC9: SHADOW fake sleeps 30s -> gate returns < 12s, exit 0, WARN budget_exceeded, event, fake killed" {
  write_state "5.5"
  make_sleeping_fake
  local start=$SECONDS
  run_gate
  local elapsed=$(( SECONDS - start ))
  [ "$elapsed" -lt 12 ]
  [ "$GATE_RC" -eq 0 ]
  [ -z "$GATE_OUT" ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW unavailable"* ]]
  [[ "$GATE_ERR" == *"budget_exceeded"* ]]
  [ -f "$EVENTS" ]
  [[ "$(last_event reason)" == *"budget_exceeded"* ]]
  assert_fake_dead
}

@test "AC9: ENFORCE fake sleeps 30s -> gate returns < 12s, hard block unavailable budget_exceeded, fake killed" {
  write_state "5.5"
  make_sleeping_fake
  export DEVOPS_SCAN_ENFORCE=1
  local start=$SECONDS
  run_gate
  local elapsed=$(( SECONDS - start ))
  [ "$elapsed" -lt 12 ]
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan unavailable"* ]]
  [[ "$GATE_OUT" == *"budget_exceeded"* ]]
  assert_fake_dead
}

# ---------------------------------------------------------------------------
# AC10: regression shields.
@test "AC10(a): 5.5 deliverable missing + SHADOW rc 1 -> still exit 2 soft block, plus WARN and event" {
  write_state_missing_deliverable
  make_fake 1 "$BLOCKED_JSON"
  run_gate
  [ "$GATE_RC" -eq 2 ]
  [[ "$GATE_OUT" == *"test_integrity_check"* ]]
  [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
  [ -f "$EVENTS" ]
  [ "$(last_event rc)" = "1" ]
}

@test "AC10(a'): 5.5 deliverable missing + ENFORCE rc 1 -> hard block wins (exit 1)" {
  write_state_missing_deliverable
  make_fake 1 "$BLOCKED_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan blocked"* ]]
}

@test "AC10(b): SHADOW stdout and exit code byte-identical with and without a finding" {
  write_state "5.5"
  make_fake 0 "$CLEAN_JSON"
  run_gate
  local rc_clean="$GATE_RC"
  cp "$WORK/out" "$WORK/out.clean"
  rm -rf "$WORK/.bytedigger" "$MARKER"
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  run_gate
  # the scan really ran and reported (otherwise identical output proves nothing)
  [ -f "$MARKER" ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
  [ "$GATE_RC" -eq "$rc_clean" ]
  cmp "$WORK/out" "$WORK/out.clean"
  [ ! -s "$WORK/out" ]
}

@test "AC10(c): all three env seams unset -> no unbound-variable crash, real sibling script used with --root/--timeout 3, exit 0" {
  local scripts_copy="$WORK/gatecopy/scripts"
  mkdir -p "$scripts_copy"
  cp "$SCRIPT" "$scripts_copy/build-gate.sh"
  cp "$(dirname "$SCRIPT")"/*.tsv "$scripts_copy/" 2>/dev/null || true
  # fake sibling devops_scan.py next to the copied gate
  cat > "$scripts_copy/devops_scan.py" <<PYEOF
import sys
with open("$MARKER", "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
sys.stdout.write('$CLEAN_JSON')
sys.exit(0)
PYEOF
  write_state "5.5"
  unset DEVOPS_SCAN_SCRIPT_TEST_OVERRIDE BD_GATE_TODAY_TEST_OVERRIDE DEVOPS_SCAN_ENFORCE
  GATE_RC=0
  bash "$scripts_copy/build-gate.sh" < /dev/null > "$WORK/out" 2> "$WORK/err" || GATE_RC=$?
  GATE_ERR="$(cat "$WORK/err")"
  [[ "$GATE_ERR" != *"unbound variable"* ]]
  [ "$GATE_RC" -eq 0 ]
  [ -f "$MARKER" ]
  [ "$(cat "$MARKER")" = "--root $WORK --timeout 3" ]
}

@test "AC10(d): rc 1 with finding id containing quote and backslash -> ENFORCE stdout is valid JSON" {
  write_state "5.5"
  cat > "$FAKE" <<PYEOF
import json, sys
with open("$MARKER", "a") as f:
    f.write("ran\n")
sys.stdout.write(json.dumps({"status": "blocked", "gating": [{"id": 'F"1\\\\x'}, {"id": "ok"}],
                             "waived": [], "nongating": [], "reason": 'bad"reason\\\\z'}))
sys.exit(1)
PYEOF
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ -f "$MARKER" ]
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan blocked"* ]]
  "$REAL_PY" -c 'import json,sys; json.loads(open(sys.argv[1]).read())' "$WORK/out"
}

@test "AC10(d): same JSON-hostile finding in SHADOW -> event line is valid JSON" {
  write_state "5.5"
  cat > "$FAKE" <<PYEOF
import json, sys
with open("$MARKER", "a") as f:
    f.write("ran\n")
sys.stdout.write(json.dumps({"status": "blocked", "gating": [{"id": 'F"1\\\\x'}],
                             "waived": [], "nongating": [], "reason": 'bad"reason\\\\z'}))
sys.exit(1)
PYEOF
  run_gate
  [ -f "$MARKER" ]
  [ "$GATE_RC" -eq 0 ]
  [ -f "$EVENTS" ]
  [ "$(last_event rc)" = "1" ]
}

@test "AC10(e): rc 1 with JSON status clean -> rc wins (SHADOW: blocked WARN, event rc 1)" {
  write_state "5.5"
  make_fake 1 "$CLEAN_JSON"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
  [ "$(last_event rc)" = "1" ]
}

@test "AC10(e): rc 1 with JSON status clean -> rc wins (ENFORCE: hard block)" {
  write_state "5.5"
  make_fake 1 "$CLEAN_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan blocked"* ]]
}

# AC11 (bd136 / build-gate.bats / gate-dispatcher.bats stay green) is verified by
# running those suites; no pattern-presence test is written here.
