#!/usr/bin/env bats
# RED tests: devops_scan wiring in scripts/build-gate.sh (gate_phase_55, SHADOW by default).
# Spec: docs/decisions/2026-10-03-s4-wire-devops-scan-build-gate.md
#
# Hermetic: the scan script is replaced by a tiny fake python3 file selected via
# DEVOPS_SCAN_SCRIPT. The fake records its argv in a marker file and exits with a
# chosen rc after printing chosen stdout. "Today" is pinned via BD_GATE_TODAY.

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
  EVENTS="$WORK/.bytedigger/devops-scan-shadow.jsonl"
  FAKE="$WORK/fake_devops_scan.py"
  export DEVOPS_SCAN_SCRIPT="$FAKE"
  export BD_GATE_TODAY="2026-10-03"
  unset DEVOPS_SCAN_ENFORCE
}

teardown() {
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

# make_fake <rc> <stdout-text>
make_fake() {
  local rc="$1" out="$2"
  cat > "$FAKE" <<PYEOF
import sys
with open("$MARKER", "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
sys.stdout.write(sys.argv and """$out""")
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

# last_event <dotted.field>
last_event() {
  python3 - "$EVENTS" "$1" <<'PYEOF'
import json, sys
lines = [l for l in open(sys.argv[1]).read().splitlines() if l.strip()]
d = json.loads(lines[-1])
for k in sys.argv[2].split("."):
    d = d[k]
print(d)
PYEOF
}

# ---------------------------------------------------------------------------
@test "AC1: SHADOW rc 1 -> exit 0, WARN, event line with flag owner/expiry, scan ran once with --root" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
  [ -f "$EVENTS" ]
  [ "$(last_event mode)" = "shadow" ]
  [ "$(last_event rc)" = "1" ]
  [ "$(last_event gating)" = "2" ]
  [ "$(last_event flag.name)" = "DEVOPS_SCAN_ENFORCE" ]
  [ "$(last_event flag.owner)" = "s4-bytedigger (MGR)" ]
  [ "$(last_event flag.expires)" = "2026-10-17" ]
  # exactly one run, full tree (--root CWD, no --files)
  [ "$(wc -l < "$MARKER" | tr -d ' ')" = "1" ]
  grep -q -- "--root $WORK" "$MARKER"
  ! grep -q -- "--files" "$MARKER"
}

@test "AC2: SHADOW rc 2 -> exit 0, WARN names reason, event present" {
  write_state "5.5"
  make_fake 2 "$UNAVAIL_JSON"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"WARN"* ]]
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
@test "AC4: only exactly 1 enforces; true / 0 / empty behave as SHADOW" {
  write_state "5.5"
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
@test "AC5: SHADOW script missing -> exit 0 + WARN + event unavailable" {
  write_state "5.5"
  export DEVOPS_SCAN_SCRIPT="$WORK/does-not-exist.py"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"WARN"* ]]
  [[ "$GATE_ERR" == *"devops_scan"* ]]
  [ "$(last_event status)" = "unavailable" ]
}

@test "AC5: ENFORCE script missing -> hard block unavailable" {
  write_state "5.5"
  export DEVOPS_SCAN_SCRIPT="$WORK/does-not-exist.py"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan unavailable"* ]]
}

@test "AC5: SHADOW non-JSON stdout (rc 0) -> exit 0 + WARN + event unavailable" {
  write_state "5.5"
  make_fake 0 "this is not json"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"WARN"* ]]
  [[ "$GATE_ERR" == *"devops_scan"* ]]
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

@test "AC5: SHADOW rc 7 -> exit 0 + WARN + event unavailable" {
  write_state "5.5"
  make_fake 7 "$CLEAN_JSON"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"WARN"* ]]
  [[ "$GATE_ERR" == *"devops_scan"* ]]
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

# PATH without python3: symlink only the coreutils the gate needs.
make_nopython_path() {
  local d="$WORK/nopybin" t p
  mkdir -p "$d"
  for t in bash sh cat grep sed tr date stat find head mkdir dirname basename mv rm cut env wc touch printf uname sort; do
    p="$(command -v "$t" 2>/dev/null || true)"
    case "$p" in /*) ln -sf "$p" "$d/$t" ;; esac
  done
  echo "$d"
}

@test "AC5: SHADOW python3 absent from PATH -> exit 0 + WARN, scan not executed" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  local np; np="$(make_nopython_path)"
  PATH="$np" command -v python3 && skip "python3 still resolvable on PATH"
  run_gate "$np"
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"WARN"* ]]
  [[ "$GATE_ERR" == *"devops_scan"* ]]
  [ ! -e "$MARKER" ]
}

@test "AC5: ENFORCE python3 absent from PATH -> hard block unavailable" {
  write_state "5.5"
  make_fake 0 "$CLEAN_JSON"
  export DEVOPS_SCAN_ENFORCE=1
  local np; np="$(make_nopython_path)"
  PATH="$np" command -v python3 && skip "python3 still resolvable on PATH"
  run_gate "$np"
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan unavailable"* ]]
}

# ---------------------------------------------------------------------------
@test "AC6: SHADOW on expiry day 2026-10-17 -> normal shadow behaviour" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  export BD_GATE_TODAY="2026-10-17"
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [[ "$GATE_ERR" == *"devops_scan SHADOW blocked"* ]]
  [ -f "$MARKER" ]
}

@test "AC6: SHADOW after expiry 2026-10-18 -> hard block naming flag/owner/expiry, scan NOT executed" {
  write_state "5.5"
  make_fake 0 "$CLEAN_JSON"
  export BD_GATE_TODAY="2026-10-18"
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: DEVOPS_SCAN_ENFORCE SHADOW window expired 2026-10-17"* ]]
  [[ "$GATE_OUT" == *"s4-bytedigger (MGR)"* ]]
  [ ! -e "$MARKER" ]
}

@test "AC6: ENFORCE=1 after expiry 2026-10-18 -> normal ENFORCE verdict (blocked)" {
  write_state "5.5"
  make_fake 1 "$BLOCKED_JSON"
  export BD_GATE_TODAY="2026-10-18"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 1 ]
  [[ "$GATE_OUT" == *"HARD BLOCK: devops_scan blocked"* ]]
  [[ "$GATE_OUT" != *"window expired"* ]]
}

@test "AC6: ENFORCE=1 after expiry, clean scan -> exit 0" {
  write_state "5.5"
  make_fake 0 "$CLEAN_JSON"
  export BD_GATE_TODAY="2026-10-18"
  export DEVOPS_SCAN_ENFORCE=1
  run_gate
  [ "$GATE_RC" -eq 0 ]
  [ -f "$MARKER" ]
}

# ---------------------------------------------------------------------------
# AC7: scan is invoked only in phase 5.5 with active gates and fresh state.
# Guard tests: green today (feature absent), must stay green after GREEN.
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

# AC9 (static / non-regression of build-gate.bats and gate-dispatcher.bats) is
# covered by running those suites; no pattern-presence test is written here.
