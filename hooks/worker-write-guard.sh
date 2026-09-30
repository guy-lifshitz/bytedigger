#!/bin/bash
# worker-write-guard.sh
# PreToolUse hook (Write|Edit|MultiEdit|NotebookEdit): while a build is active, stops
# subagents from writing orchestrator state (build-state.yaml, build-metadata.json,
# build-red-output.log, build-green-output.log, .bytedigger-orchestrator-pid) and confines
# each read-only role to one scratchpad dir (explorer -> research/, architect ->
# architecture/, synthesizer -> reviews/). Logic lives in worker_write_guard.py (bd#133).
#
# Thin wrapper: stdin JSON, cwd and state reach Python only via stdin / the process cwd,
# never through shell interpolation.
#
# Exit codes: 0 = allow, 2 = block with message

# Require python3 — fail open if unavailable (same policy as build-state-guard.sh).
if ! command -v python3 >/dev/null 2>&1; then
  cat > /dev/null
  echo "WARN (bytedigger write guard): python3 not found; write guard disabled" >&2
  exit 0
fi

HOOK_DIR=$(dirname "${BASH_SOURCE[0]}")
exec python3 "$HOOK_DIR/worker_write_guard.py"
