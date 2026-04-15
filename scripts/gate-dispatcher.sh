#!/usr/bin/env bash
# gate-dispatcher.sh — Routes gate invocations to bash, ts, or shadow backend.
#
# Resolution order for backend selection:
#   1. GATE_BACKEND env var (bash|ts|shadow) wins over config
#   2. bytedigger.json "gate_backend" field
#   3. default: bash
#
# Stdin: drained (safety: harness pipes JSON)
# Stdout: forwarded from selected backend
# Exit: 0 pass, 1 hard block, 2 soft block
#
# Fail-closed: gate_backend=ts with missing bun → exit 1 + JSON hard block.
# Fail-closed: malformed config when user asked for non-default backend →
#              stderr warning so silent fallback to bash is visible.

set -euo pipefail

# Drain stdin immediately — harness pipes JSON, we never read it.
cat > /dev/null 2>/dev/null || true

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SD/.." && pwd)"
# Test-only seam: GATE_DISPATCHER_{BASH,TS}_TEST_OVERRIDE let bats tests
# substitute fake gate binaries to force a known divergence. The _TEST_
# suffix is load-bearing — operators must NOT set these as configuration.
# Production paths resolve from $SD and cannot be configured via env.
BASH_GATE="${GATE_DISPATCHER_BASH_TEST_OVERRIDE:-$SD/build-gate.sh}"
TS_GATE="${GATE_DISPATCHER_TS_TEST_OVERRIDE:-$SD/ts/build-phase-gate.ts}"

# ---------------------------------------------------------------------------
# Config discovery (mirrors build-gate.sh load_config)
# ---------------------------------------------------------------------------
resolve_config() {
  if [ -n "${BYTEDIGGER_CONFIG:-}" ]; then
    echo "$BYTEDIGGER_CONFIG"
  elif [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    echo "$CLAUDE_PLUGIN_ROOT/bytedigger.json"
  elif [ -n "${BYTEDIGGER_PLUGIN_ROOT:-}" ]; then
    echo "$BYTEDIGGER_PLUGIN_ROOT/bytedigger.json"
  else
    echo "$ROOT/bytedigger.json"
  fi
}

# Emits backend value to stdout. If the config file exists but cannot be
# parsed, logs a stderr warning so operators see the silent fallback.
read_backend_from_config() {
  local cfg="$1"
  if [ ! -f "$cfg" ]; then
    echo "bash"
    return 0
  fi
  local val py_rc
  set +e
  val=$(python3 - "$cfg" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        c = json.load(f)
    print(c.get("gate_backend", "bash"))
except Exception as e:
    sys.stderr.write(f"[gate-dispatcher] python config read failed: {e}\n")
    sys.exit(2)
PYEOF
)
  py_rc=$?
  set -e
  if [ "$py_rc" -ne 0 ] || [ -z "$val" ]; then
    # Fallback grep. If the fallback also fails, warn on stderr so a user
    # who wrote gate_backend=ts but has a malformed JSON sees why they got
    # bash instead of their configured backend.
    val=$(grep -oE '"gate_backend"[[:space:]]*:[[:space:]]*"[a-z]*"' "$cfg" 2>/dev/null \
          | grep -oE '"[a-z]*"$' | tr -d '"' || true)
    if [ -z "$val" ]; then
      echo "[gate-dispatcher] WARN: could not parse gate_backend from $cfg — defaulting to bash" >&2
      val="bash"
    fi
  fi
  echo "$val"
}

# ---------------------------------------------------------------------------
# Backend resolution
# ---------------------------------------------------------------------------
CONFIG_FILE="$(resolve_config)"

if [ -n "${GATE_BACKEND:-}" ]; then
  BACKEND="$GATE_BACKEND"
else
  BACKEND="$(read_backend_from_config "$CONFIG_FILE")"
fi

# Normalize + validate
case "$BACKEND" in
  bash|ts|shadow) ;;
  *)
    echo "[gate-dispatcher] unknown gate_backend=$BACKEND, defaulting to bash" >&2
    BACKEND="bash"
    ;;
esac

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
run_bash() {
  bash "$BASH_GATE" "$@" < /dev/null
}

run_ts() {
  if ! command -v bun >/dev/null 2>&1; then
    printf '%s\n' '{"decision":"block","severity":"hard","reason":"HARD BLOCK: gate_backend=ts but bun not found"}'
    exit 1
  fi
  bun run "$TS_GATE" "$@" < /dev/null
}

case "$BACKEND" in
  bash)
    set +e
    run_bash "$@"
    rc=$?
    set -e
    exit "$rc"
    ;;
  ts)
    set +e
    run_ts "$@"
    rc=$?
    set -e
    exit "$rc"
    ;;
  shadow)
    # Resolve shadow dir up-front so we can fail loud if it can't be created.
    # Prefer per-project .bytedigger/gate-shadow if CWD is the config dir.
    if [ -n "${BYTEDIGGER_CONFIG:-}" ]; then
      SHADOW_DIR="$(dirname "$BYTEDIGGER_CONFIG")/.bytedigger/gate-shadow"
    else
      SHADOW_DIR="$ROOT/.bytedigger/gate-shadow"
    fi
    if ! mkdir -p "$SHADOW_DIR" 2>/dev/null; then
      echo "[gate-dispatcher] WARN: cannot create $SHADOW_DIR — falling back to ${TMPDIR:-/tmp}/bytedigger-shadow" >&2
      SHADOW_DIR="${TMPDIR:-/tmp}/bytedigger-shadow"
      mkdir -p "$SHADOW_DIR"
    fi

    # Capture both backends. DO NOT redirect stderr to /dev/null — we need
    # operator visibility when a backend crashes. Tee stderr into a log so
    # the comparison below sees only stdout diffs.
    STDERR_LOG="$SHADOW_DIR/dispatcher-stderr.log"
    set +e
    bash_stdout="$(run_bash "$@" 2>>"$STDERR_LOG")"
    bash_status=$?
    ts_stdout="$(run_ts "$@" 2>>"$STDERR_LOG")"
    ts_status=$?
    set -e

    if [ "$bash_status" != "$ts_status" ] || [ "$bash_stdout" != "$ts_stdout" ]; then
      ts_iso=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
      # JSON-escape via python. If python3 is missing or the write fails,
      # fall back to a raw log line so the mismatch is NEVER silently lost.
      if ! python3 - "$SHADOW_DIR/mismatches.jsonl" "$ts_iso" "$bash_status" "$ts_status" "$bash_stdout" "$ts_stdout" <<'PYEOF'
import json, sys
path, ts, bc, tc, bs, ts_out = sys.argv[1:7]
rec = {"ts": ts, "bash_code": int(bc), "ts_code": int(tc),
       "bash_stdout": bs, "ts_stdout": ts_out}
with open(path, "a") as f:
    f.write(json.dumps(rec) + "\n")
PYEOF
      then
        echo "[gate-dispatcher] WARN: python3 mismatch logger failed — writing raw fallback" >&2
        printf '%s %s %s\n' "$ts_iso" "$bash_status" "$ts_status" >> "$SHADOW_DIR/mismatches.raw"
      fi
    fi

    # Fail-closed on degenerate bash output: if bash exited non-zero with
    # empty stdout, emit a hard-block verdict instead of forwarding nothing.
    if [ -z "$bash_stdout" ] && [ "$bash_status" -ne 0 ]; then
      printf '%s\n' '{"decision":"block","severity":"hard","reason":"HARD BLOCK: shadow-mode bash backend exited non-zero with empty stdout"}'
      exit 1
    fi

    # Return bash verdict (shadow mode = bash is still source of truth).
    if [ -n "$bash_stdout" ]; then
      printf '%s\n' "$bash_stdout"
    fi
    exit "$bash_status"
    ;;
esac
