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

set -uo pipefail

# Drain stdin immediately — harness pipes JSON, we never read it.
cat > /dev/null 2>/dev/null || true

SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SD/.." && pwd)"
BASH_GATE="$SD/build-gate.sh"
TS_GATE="$SD/ts/build-phase-gate.ts"

# ---------------------------------------------------------------------------
# Config discovery (match build-gate.sh section 2)
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

read_backend_from_config() {
  local cfg="$1"
  [ -f "$cfg" ] || { echo "bash"; return 0; }
  local val
  val=$(python3 - "$cfg" <<'PYEOF' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1]) as f:
        c = json.load(f)
    print(c.get("gate_backend", "bash"))
except Exception:
    print("bash")
PYEOF
)
  if [ -z "$val" ]; then
    # Fallback grep
    val=$(grep -oE '"gate_backend"[[:space:]]*:[[:space:]]*"[a-z]*"' "$cfg" 2>/dev/null \
          | grep -oE '"[a-z]*"$' | tr -d '"')
  fi
  echo "${val:-bash}"
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
    printf '%s\n' '{"decision":"block","severity":"hard","reason":"gate_backend=ts but bun not found"}'
    exit 1
  fi
  bun run "$TS_GATE" "$@" < /dev/null
}

case "$BACKEND" in
  bash)
    run_bash "$@"
    exit $?
    ;;
  ts)
    run_ts "$@"
    exit $?
    ;;
  shadow)
    set +e
    bash_stdout="$(run_bash "$@" 2>/dev/null)"
    bash_status=$?
    ts_stdout="$(run_ts "$@" 2>/dev/null)"
    ts_status=$?
    set -e

    if [ "$bash_status" != "$ts_status" ] || [ "$bash_stdout" != "$ts_stdout" ]; then
      SHADOW_DIR="${TMPDIR:-/tmp}"
      # Prefer per-project .bytedigger/gate-shadow if CWD is the config dir.
      if [ -n "${BYTEDIGGER_CONFIG:-}" ]; then
        SHADOW_DIR="$(dirname "$BYTEDIGGER_CONFIG")/.bytedigger/gate-shadow"
      else
        SHADOW_DIR="$ROOT/.bytedigger/gate-shadow"
      fi
      mkdir -p "$SHADOW_DIR" 2>/dev/null || true
      ts_iso=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
      # JSON-escape via python
      python3 - "$SHADOW_DIR/mismatches.jsonl" "$ts_iso" "$bash_status" "$ts_status" "$bash_stdout" "$ts_stdout" <<'PYEOF' 2>/dev/null || true
import json, sys
path, ts, bc, tc, bs, ts_out = sys.argv[1:7]
rec = {"ts": ts, "bash_code": int(bc), "ts_code": int(tc),
       "bash_stdout": bs, "ts_stdout": ts_out}
with open(path, "a") as f:
    f.write(json.dumps(rec) + "\n")
PYEOF
    fi

    # Return bash verdict
    if [ -n "$bash_stdout" ]; then
      printf '%s\n' "$bash_stdout"
    fi
    exit "$bash_status"
    ;;
esac
