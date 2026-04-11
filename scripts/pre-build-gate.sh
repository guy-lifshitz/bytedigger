#!/usr/bin/env bash
# pre-build-gate.sh — CWD mutex collision guard for ByteDigger
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
# TRIVIAL exemption — exit immediately, no file writes
# ---------------------------------------------------------------------------
COMPLEXITY_UPPER=$(echo "$COMPLEXITY" | tr '[:lower:]' '[:upper:]')
if [[ "$COMPLEXITY_UPPER" == "TRIVIAL" ]]; then
  exit 0
fi

# ---------------------------------------------------------------------------
# Get CWD and branch
# ---------------------------------------------------------------------------
CWD=$(pwd)
BRANCH=$(git branch --show-current 2>/dev/null || echo "")

# ---------------------------------------------------------------------------
# Advisory lock (mkdir-based — atomic on POSIX)
# ---------------------------------------------------------------------------
LOCK_DIR="${SESSION_FILE}.lock"
LOCK_TIMEOUT=5
LOCK_INTERVAL=0.1
STALE_THRESHOLD=30

acquire_lock() {
  local elapsed=0
  while true; do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      # Write timestamp into lock for stale detection
      echo "$(date +%s)" > "$LOCK_DIR/ts"
      return 0
    fi
    # Check for stale lock
    if [[ -f "$LOCK_DIR/ts" ]]; then
      local lock_ts
      lock_ts=$(cat "$LOCK_DIR/ts" 2>/dev/null || echo 0)
      local now_ts
      now_ts=$(date +%s)
      local age=$(( now_ts - lock_ts ))
      if [[ $age -gt $STALE_THRESHOLD ]]; then
        rm -rf "$LOCK_DIR"
        continue
      fi
    fi
    elapsed=$(echo "$elapsed + $LOCK_INTERVAL" | bc)
    local elapsed_int
    elapsed_int=$(echo "$elapsed" | cut -d. -f1)
    if [[ "${elapsed_int:-0}" -ge "$LOCK_TIMEOUT" ]]; then
      echo "ERROR: Could not acquire session lock after ${LOCK_TIMEOUT}s" >&2
      exit 1
    fi
    sleep "$LOCK_INTERVAL"
  done
}

release_lock() {
  rm -rf "$LOCK_DIR"
}

acquire_lock
trap release_lock EXIT

# ---------------------------------------------------------------------------
# Session management via python3
# ---------------------------------------------------------------------------
SESSION_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

python3 - "$SESSION_FILE" "$CWD" "$BRANCH" "$SESSION_ID" "$STARTED_AT" "$COMPLEXITY_UPPER" <<'PYEOF'
import json, sys, time, datetime, os

session_file = sys.argv[1]
cwd          = sys.argv[2]
branch       = sys.argv[3]
session_id   = sys.argv[4]
started_at   = sys.argv[5]
complexity   = sys.argv[6]

# Load sessions — JSON array format
sessions = []
if os.path.isfile(session_file):
    try:
        with open(session_file) as f:
            data = json.load(f)
        if isinstance(data, list):
            sessions = data
    except Exception:
        sessions = []

# Clean stale sessions (>24h old)
cutoff = time.time() - 86400
active = []
for s in sessions:
    started = s.get("started_at", "")
    try:
        ts = started.rstrip("Z").replace("T", " ")
        dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        epoch = time.mktime(dt.timetuple())
    except Exception:
        epoch = 0
    if epoch >= cutoff:
        active.append(s)

# Collision check: exact match, parent, or child overlap
def paths_overlap(a, b):
    """Return True if a == b, a is a parent of b, or b is a parent of a."""
    if a == b:
        return True
    # Ensure trailing slash for prefix check
    a_slash = a.rstrip("/") + "/"
    b_slash = b.rstrip("/") + "/"
    return b.startswith(a_slash) or a.startswith(b_slash)

for s in active:
    s_cwd = s.get("cwd", "")
    if paths_overlap(cwd, s_cwd):
        if cwd == s_cwd:
            msg = f"ERROR: Session collision — another active build is already running at CWD '{cwd}'. Wait for it to finish or clean up the session file."
        else:
            msg = f"ERROR: Path overlap/parent-child conflict — active session at '{s_cwd}' overlaps with requested CWD '{cwd}'."
        print(msg, file=sys.stderr)
        sys.exit(1)

# Register new session
new_session = {
    "session_id": session_id,
    "cwd": cwd,
    "branch": branch,
    "started_at": started_at,
    "complexity": complexity,
}
active.append(new_session)

with open(session_file, "w") as f:
    json.dump(active, f, indent=2)

sys.exit(0)
PYEOF
