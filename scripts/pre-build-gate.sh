#!/usr/bin/env bash
# pre-build-gate.sh — pre-build branch + session collision guard for ByteDigger
# Usage: pre-build-gate.sh --complexity <level> --session-file <path>
set -euo pipefail

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------
COMPLEXITY=""
SESSION_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --complexity)
      COMPLEXITY="${2:-}"
      shift 2
      ;;
    --session-file)
      SESSION_FILE="${2:-}"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "$COMPLEXITY" ]]; then
  echo "ERROR: --complexity is required" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Branch detection
# ---------------------------------------------------------------------------
BRANCH=$(git branch --show-current 2>/dev/null || echo "")

# ---------------------------------------------------------------------------
# Worktree enforcement
# ---------------------------------------------------------------------------
COMPLEXITY_UPPER=$(echo "$COMPLEXITY" | tr '[:lower:]' '[:upper:]')

is_protected_branch() {
  [[ "$1" == "main" || "$1" == "master" ]]
}

is_complex_task() {
  [[ "$1" == "FEATURE" || "$1" == "COMPLEX" ]]
}

if is_protected_branch "$BRANCH"; then
  if is_complex_task "$COMPLEXITY_UPPER"; then
    echo "ERROR: Blocked — branch '$BRANCH' is protected. $COMPLEXITY_UPPER tasks are not allowed on $BRANCH. Please create a feature branch." >&2
    exit 1
  else
    # SIMPLE/TRIVIAL on main — warn but allow
    echo "WARNING: Running SIMPLE task directly on '$BRANCH' branch is unusual. Proceed with caution." >&2
    # Fall through to session check below (still register session)
  fi
fi

# ---------------------------------------------------------------------------
# Session collision detection
# ---------------------------------------------------------------------------
# Build new session entry (used if we proceed)
NEW_SESSION_ID="sess-$(date -u +%s)-$$"
NEW_SESSION_BRANCH="$BRANCH"
NEW_SESSION_STARTED=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [[ -n "$SESSION_FILE" && -f "$SESSION_FILE" ]]; then
  # Parse existing sessions using python3 (portable JSON)
  SESSIONS_JSON=$(python3 - "$SESSION_FILE" "$BRANCH" <<'PYEOF'
import json, sys, time, datetime

session_file = sys.argv[1]
branch = sys.argv[2]

with open(session_file) as f:
    data = json.load(f)

sessions = data.get("sessions", [])
cutoff = int(time.time()) - 86400  # 24h ago as local epoch

# Filter out stale sessions
# Note: timestamps may be stored as local time with Z suffix (from BSD date without -u)
# Use time.mktime (local timezone) for comparison to match how they were created
active = []
for s in sessions:
    started = s.get("started_at", "")
    try:
        # Strip Z and parse as naive datetime, then convert via mktime (local time)
        ts = started.rstrip("Z").replace("T", " ")
        dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        epoch = int(time.mktime(dt.timetuple()))
    except Exception:
        epoch = 0
    if epoch >= cutoff:
        active.append(s)

# Check for collision on same branch
collision = any(s.get("branch") == branch for s in active)

if collision:
    print("COLLISION")
    sys.exit(0)

print(json.dumps(active))
PYEOF
)

  if [[ "$SESSIONS_JSON" == "COLLISION" ]]; then
    echo "ERROR: Session collision — another active session is already running on branch '$BRANCH'. Wait for it to finish or clean up .bytedigger-sessions.json." >&2
    exit 1
  fi

  # Write updated session file with stale entries removed + new session appended
  # Write SESSIONS_JSON to a temp file to avoid injection and pipe/heredoc conflict
  SESSIONS_TMP=$(mktemp)
  printf '%s' "$SESSIONS_JSON" > "$SESSIONS_TMP"
  python3 - "$SESSION_FILE" "$NEW_SESSION_ID" "$NEW_SESSION_BRANCH" "$NEW_SESSION_STARTED" "$$" "$SESSIONS_TMP" <<'PYEOF'
import json, sys

session_file   = sys.argv[1]
new_id         = sys.argv[2]
new_branch     = sys.argv[3]
new_started_at = sys.argv[4]
new_pid        = int(sys.argv[5])
sessions_tmp   = sys.argv[6]

with open(sessions_tmp) as f:
    active_sessions = json.load(f)

new_session = {
    "id": new_id,
    "branch": new_branch,
    "started_at": new_started_at,
    "pid": new_pid
}
active_sessions.append(new_session)

data = {"sessions": active_sessions}
with open(session_file, "w") as f:
    json.dump(data, f, indent=2)
PYEOF
  rm -f "$SESSIONS_TMP"

elif [[ -n "$SESSION_FILE" ]]; then
  # Session file doesn't exist — create it with the new session
  python3 - <<PYEOF
import json

new_session = {
    "id": "$NEW_SESSION_ID",
    "branch": "$NEW_SESSION_BRANCH",
    "started_at": "$NEW_SESSION_STARTED",
    "pid": $$
}
data = {"sessions": [new_session]}
with open("$SESSION_FILE", "w") as f:
    json.dump(data, f, indent=2)
PYEOF
fi

exit 0
