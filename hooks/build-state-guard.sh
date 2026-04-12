#!/bin/bash
# build-state-guard.sh
# PreToolUse hook: blocks deletion of build-state.yaml and .bytedigger-orchestrator-pid
# while a pipeline is mid-run (phase < 7).
#
# Exit codes: 0 = allow, 2 = block with message

# Require python3 — fail open if unavailable.
if ! command -v python3 >/dev/null 2>&1; then
  cat > /dev/null
  exit 0
fi

# Combine existence check + read into one operation (TOCTOU mitigation).
# If build-state.yaml is absent or has no current_phase, no pipeline is active.
CURRENT_PHASE=$(grep -m1 'current_phase:' build-state.yaml 2>/dev/null \
  | sed 's/.*: *"\{0,1\}//;s/"\{0,1\} *$//' \
  | tr -d '\r')

[ -z "$CURRENT_PHASE" ] && { cat > /dev/null; exit 0; }

# Phase 7 or "completed" means pipeline finished — cleanup is legitimate.
if [ "$CURRENT_PHASE" = "7" ] || [ "$CURRENT_PHASE" = "completed" ]; then
  cat > /dev/null
  exit 0
fi

# Read stdin JSON.
INPUT=$(cat)

# Single python3 call, printf to avoid -n/-e issues.
PARSED=$(printf '%s\n' "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_name', ''))
    print(d.get('tool_input', {}).get('command', ''))
except:
    print('')
    print('')
" 2>/dev/null) || { exit 0; }

TOOL_NAME=$(printf '%s\n' "$PARSED" | head -1)
COMMAND=$(printf '%s\n' "$PARSED" | sed -n '2p')

# Only inspect Bash tool calls.
[ "$TOOL_NAME" != "Bash" ] && exit 0

# Use python3 for detection — handles quotes, ./ prefix, flag-order variants.
if printf '%s' "$COMMAND" | python3 -c "
import sys, re
cmd = sys.stdin.read()
# Normalize: strip quotes so 'file' and \"file\" both match
normalized = cmd.replace('\"', '').replace(\"'\", '')
state_files = ['build-state.yaml', '.bytedigger-orchestrator-pid']
# Block rm/unlink targeting a protected state file (with optional path prefix)
for sf in state_files:
    if re.search(r'(rm|unlink)\b.*' + re.escape(sf), normalized):
        sys.exit(1)
# Block rm -r[f]* . / rm -R[f]* . variants (single or combined recursive flag, targeting bare .)
# Matches: rm -r ., rm -R ., rm -rf ., rm -fr ., rm -rfv ., etc.
# Does NOT match: rm -r ./specific-file (dot must be followed by whitespace/end/shell separator)
if re.search(r'rm\s+-[a-zA-Z]*[rR][a-zA-Z]*\s+\.(\s|$|;|\||&)', normalized):
    sys.exit(1)
sys.exit(0)
" 2>/dev/null; then
  # No match — allow
  exit 0
fi

# Deletion detected — send block reason to both stdout and stderr.
BLOCK_MSG="BLOCKED: Cannot delete build-state.yaml or .bytedigger-orchestrator-pid during active pipeline (phase: ${CURRENT_PHASE}). State files are protected until Phase 7."
echo "$BLOCK_MSG"
echo "$BLOCK_MSG" >&2
exit 2
