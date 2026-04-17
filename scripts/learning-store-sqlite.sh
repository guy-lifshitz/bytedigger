#!/usr/bin/env bash
# learning-store-sqlite.sh — SQLite backend delegate for bytedigger learning store
# Invoked via: exec <this> inject|extract <arg> --config <path>
# Contract: always exits 0 in pipeline mode (never blocks the build).
#           Set LEARNING_STORE_STRICT=1 to enable non-zero exit on errors (test-harness mode).
#           Strict mode applies to setup/validation errors (_die) and dep checks; per-row
#           sqlite3 query failures always exit 0 (best-effort per spec §6 row 6).
set -euo pipefail

umask 0077  # DB may contain build metadata; restrict to owner

# --- Error slugs (external contract — consumed by build-state.yaml readers + tests) ---
readonly SLUG_URL_NOT_SET="LEARNING_DB_URL_not_set"
readonly SLUG_INVALID_PATH="invalid_db_path"
readonly SLUG_PARENT_MISSING="db_parent_missing"
readonly SLUG_FILE_MISSING="db_file_missing"
readonly SLUG_DEPS_MISSING="deps_missing"
readonly SLUG_SQLITE3_ERROR="sqlite3_error"

# ── Util: CWD from config path ─────────────────────────────────────────────────
# Duplicated from learning-store.sh so this script runs standalone under exec
_cwd_from_config() {
  local config_file="$1"
  if [ -n "$config_file" ] && [ -f "$config_file" ]; then
    dirname "$config_file"
  else
    pwd
  fi
}

# ── Util: state-file writer ────────────────────────────────────────────────────
# Matches learning-store.sh semantics so backend swap is transparent to callers.
# Uses grep -v "^${key}:" startsWith filter per writeStateField_startsWith_not_regex learning.
_write_state() {
  local cwd="$1"
  local key="$2"
  local value="$3"
  local state_file="${cwd}/build-state.yaml"
  local tmp="${state_file}.tmp"

  if ! {
    if [ -f "$state_file" ]; then
      grep -v "^${key}:" "$state_file" 2>/dev/null || {
        local g=$?
        [ "$g" -gt 1 ] && _err "state-file grep failed rc=${g} on ${state_file}"
        true
      }
    fi
    echo "${key}: ${value}"
  } > "$tmp"; then
    _err "state-file tmp-write failed: ${tmp} (key=${key})"
    rm -f "$tmp" 2>/dev/null  # best-effort cleanup; 2>/dev/null: rm on missing file is noise
    return 1
  fi

  if ! mv "$tmp" "$state_file"; then
    _err "state-file atomic-rename failed: ${tmp} -> ${state_file} (key=${key})"
    rm -f "$tmp" 2>/dev/null  # best-effort cleanup; 2>/dev/null: rm on missing file is noise
    return 1
  fi
  return 0
}

# ── Util: structured error — log to stderr ────────────────────────────────────
_err() {
  echo "[learning-store-sqlite] ERROR: $1" >&2
}

# ── Util: write error slug to state file (uniform quoting — spec §6 canonical form) ──
_write_error_slug() {
  local cwd="$1"
  local slug="$2"
  if ! _write_state "$cwd" "learnings_extraction_error" "\"${slug}\""; then
    _err "failed to record error-slug '${slug}' to state file; downstream phase-gate will see no error signal"
  fi
}

# ── Util: die with error — write state key, then exit per STRICT mode ─────────
_die() {
  local cwd="$1"
  local slug="$2"
  local msg="$3"
  _err "$msg"
  _write_error_slug "$cwd" "$slug"
  if [ "${LEARNING_STORE_STRICT:-0}" = "1" ]; then
    exit 1
  fi
  exit 0
}

# ── Util: ID generation ────────────────────────────────────────────────────────
# openssl is a hard dep (confirmed present on macOS + every Linux the pipeline ships to).
# python3 is also a hard dep (config reader + extract parser require it).
gen_id() {
  openssl rand -hex 16
}

# ── Util: epoch-ms timestamp ───────────────────────────────────────────────────
# BSD date (macOS) does NOT support %3N — confirmed broken. python3 is the only path.
now_ms() {
  python3 -c 'import time; print(int(time.time()*1000))'
}

# ── Util: sql_escape — double single-quotes for SQLite string literals ─────────
sql_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

# ── Util: config reader — returns MAX_INJECT and MAX_STORED lines ──────────────
# MAX_STORED/MAX_INJECT are interpolated into SQL — must be integers; coerced by callers.
_read_config() {
  local config_file="$1"
  if [ ! -f "$config_file" ]; then
    echo "MAX_INJECT=10"
    echo "MAX_STORED=200"
    return
  fi

  # Capture both stdout and stderr; a parse failure on a present config is a user error
  local py_stderr_file
  py_stderr_file=$(mktemp)  # no suffix — portable BSD + GNU mktemp
  local result
  result=$(python3 - "$config_file" 2>"$py_stderr_file" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1]) as f:
        c = json.load(f)
    learning = c.get('learning', {})
    max_inject = int(learning.get('max_inject', 10))
    max_stored = int(learning.get('max_stored', 200))
    print("MAX_INJECT=" + str(max_inject))
    print("MAX_STORED=" + str(max_stored))
except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
    print("config parse error: " + str(e), file=sys.stderr)
    sys.exit(1)
PYEOF
  ) || {
    local err_content
    err_content=$(cat "$py_stderr_file" 2>/dev/null || true)
    rm -f "$py_stderr_file" 2>/dev/null
    _err "config parse failed: ${config_file}: ${err_content}"
    echo "MAX_INJECT=10"
    echo "MAX_STORED=200"
    return
  }
  rm -f "$py_stderr_file" 2>/dev/null

  if [ -z "$result" ]; then
    echo "MAX_INJECT=10"
    echo "MAX_STORED=200"
  else
    echo "$result"
  fi
}

# ── Util: LEARNING_DB_URL path validation ─────────────────────────────────────
# Returns 0 on valid path, 1 on failure.
# Uses globals (not stdout) so _die can read slug+msg without shell-word-splitting concerns.
DB_PATH_ERROR_SLUG=""
DB_PATH_ERROR_MSG=""

_validate_db_path() {
  local db_path="$1"

  # Rule 1: must be set
  if [ -z "$db_path" ]; then
    DB_PATH_ERROR_SLUG="$SLUG_URL_NOT_SET"
    DB_PATH_ERROR_MSG="LEARNING_DB_URL not set"
    return 1
  fi

  # Rule 2: reject path traversal (..)
  case "$db_path" in
    *..*)
      DB_PATH_ERROR_SLUG="$SLUG_INVALID_PATH"
      DB_PATH_ERROR_MSG="invalid path in LEARNING_DB_URL"
      return 1
      ;;
  esac

  # Rule 3: parent directory must exist
  local parent
  parent=$(dirname "$db_path")
  if [ ! -d "$parent" ]; then
    DB_PATH_ERROR_SLUG="$SLUG_PARENT_MISSING"
    DB_PATH_ERROR_MSG="DB parent dir not found"
    return 1
  fi

  # Rule 4: DB file must exist (schema not auto-created)
  if [ ! -f "$db_path" ]; then
    DB_PATH_ERROR_SLUG="$SLUG_FILE_MISSING"
    DB_PATH_ERROR_MSG="DB file not found: ${db_path}"
    return 1
  fi

  return 0
}

# ── Subcommand: inject — query DB and emit bullets to stdout ──────────────────
cmd_inject() {
  local keywords="$1"
  local config_file="$2"
  local cwd
  cwd="$(_cwd_from_config "$config_file")"

  # Validate DB path
  local db_path="${LEARNING_DB_URL:-}"
  if ! _validate_db_path "$db_path"; then
    _die "$cwd" "$DB_PATH_ERROR_SLUG" "$DB_PATH_ERROR_MSG"
  fi

  # Check sqlite3 dependency
  if ! command -v sqlite3 >/dev/null 2>&1; then  # 2>/dev/null: dep probe only
    _die "$cwd" "$SLUG_DEPS_MISSING" "sqlite3 not found"
  fi

  # Read config
  local config_values
  config_values=$(_read_config "$config_file")
  local MAX_INJECT
  MAX_INJECT=$(echo "$config_values" | grep "^MAX_INJECT=" | cut -d= -f2)
  MAX_INJECT="${MAX_INJECT:-10}"
  # Coerce to integer — interpolated into SQL LIMIT; non-numeric would be a SQLi vector
  MAX_INJECT="${MAX_INJECT//[^0-9]/}"
  MAX_INJECT="${MAX_INJECT:-10}"

  # Empty keywords: write both state keys and exit (mirrors dispatcher convention)
  if [ -z "$keywords" ] || [ -z "$(echo "$keywords" | tr -d '[:space:]')" ]; then
    _write_state "$cwd" "learnings_injected" "0"
    _write_state "$cwd" "learning_backend" "sqlite"
    exit 0
  fi

  # Build WHERE clause — one OR group per keyword (sql_escape each for defense-in-depth)
  local where_clauses=""
  for kw in $keywords; do
    local kw_esc
    kw_esc=$(sql_escape "$kw")
    if [ -z "$where_clauses" ]; then
      where_clauses="(pattern LIKE '%${kw_esc}%' OR approach LIKE '%${kw_esc}%')"
    else
      where_clauses="${where_clauses} OR (pattern LIKE '%${kw_esc}%' OR approach LIKE '%${kw_esc}%')"
    fi
  done

  # Query DB and emit bullets to stdout; capture count
  local count=0
  local select_sql
  select_sql="SELECT pattern, approach FROM learning_entries WHERE ${where_clauses} ORDER BY last_used DESC LIMIT ${MAX_INJECT};"

  local query_output sq_err sq_rc
  sq_rc=0
  sq_err=$(sqlite3 "$db_path" "$select_sql" 2>&1 >/dev/null) || sq_rc=$?
  if [ "$sq_rc" -ne 0 ]; then
    _err "sqlite3 failed: ${sq_rc}: ${sq_err}"
    _write_error_slug "$cwd" "$SLUG_SQLITE3_ERROR"
    _write_state "$cwd" "learnings_injected" "0"
    _write_state "$cwd" "learning_backend" "sqlite"
    exit 0
  fi
  query_output=$(sqlite3 "$db_path" "$select_sql")

  local pattern approach
  while IFS='|' read -r pattern approach; do
    [ -z "$pattern" ] && continue
    printf -- '- [%s] --- %s\n' "$pattern" "$approach"
    count=$(( count + 1 ))
  done <<< "$query_output"

  _write_state "$cwd" "learnings_injected" "$count"
  # learning_backend: sqlite is load-bearing — dispatcher exec-handoff skips its own backend write
  _write_state "$cwd" "learning_backend" "sqlite"
}

# ── Subcommand: extract — parse learnings-raw.md and INSERT to DB ─────────────
cmd_extract() {
  local scratchpad_dir="$1"
  local config_file="$2"
  local cwd
  cwd="$(_cwd_from_config "$config_file")"

  # Validate DB path
  local db_path="${LEARNING_DB_URL:-}"
  if ! _validate_db_path "$db_path"; then
    _die "$cwd" "$DB_PATH_ERROR_SLUG" "$DB_PATH_ERROR_MSG"
  fi

  # Check sqlite3 dependency
  if ! command -v sqlite3 >/dev/null 2>&1; then  # 2>/dev/null: dep probe only
    _die "$cwd" "$SLUG_DEPS_MISSING" "sqlite3 not found"
  fi

  # Read config
  local config_values
  config_values=$(_read_config "$config_file")
  local MAX_STORED
  MAX_STORED=$(echo "$config_values" | grep "^MAX_STORED=" | cut -d= -f2)
  MAX_STORED="${MAX_STORED:-200}"
  # Coerce to integer — MAX_STORED is interpolated into SQL LIMIT; non-numeric would be a SQLi vector
  MAX_STORED="${MAX_STORED//[^0-9]/}"
  MAX_STORED="${MAX_STORED:-200}"

  # Check for learnings-raw.md
  local raw_md="${scratchpad_dir}/reviews/learnings-raw.md"
  if [ ! -f "$raw_md" ]; then
    _write_state "$cwd" "learnings_extracted" "0"
    exit 0
  fi

  # Parse learnings-raw.md via python3 (reliable regex for multi-line markdown)
  # Output format: <sanitized_category>\x1f<lesson> one per line
  # IFS=0x1f must match python3 writer below — ASCII unit separator chosen because
  # lesson text can contain tab/pipe/comma
  local py_stderr_file
  py_stderr_file=$(mktemp)  # no suffix — portable BSD + GNU mktemp
  local parsed_entries
  parsed_entries=$(python3 - "$raw_md" 2>"$py_stderr_file" <<'PYEOF'
import re, sys

raw_md_path = sys.argv[1]
pattern_re = re.compile(r'^-\s+\[([^\]]+)\]\s+(?:---?|\u2014)\s+(.+)$')

try:
    with open(raw_md_path, 'r', encoding='utf-8') as f:
        for line in f:
            m = pattern_re.match(line.strip())
            if m:
                category = m.group(1).strip()
                lesson   = m.group(2).strip()
                # Sanitize: lowercase, non-alnum to dash, strip edge dashes
                sanitized = re.sub(r'[^a-z0-9]+', '-', category.lower()).strip('-')
                # Delimiter: ASCII unit separator (0x1f) safe against text content
                print(sanitized + '\x1f' + lesson)
except (OSError, IOError, UnicodeDecodeError) as e:
    print("[learning-store-sqlite] ERROR: parse failed: " + str(e), file=sys.stderr)
    sys.exit(1)
PYEOF
  ) || {
    local parse_err
    parse_err=$(cat "$py_stderr_file" 2>/dev/null || true)
    rm -f "$py_stderr_file" 2>/dev/null
    _err "extract parse failed: ${parse_err}"
    _write_error_slug "$cwd" "$SLUG_SQLITE3_ERROR"
    _write_state "$cwd" "learnings_extracted" "0"
    exit 0
  }
  rm -f "$py_stderr_file" 2>/dev/null

  if [ -z "$parsed_entries" ]; then
    _write_state "$cwd" "learnings_extracted" "0"
    exit 0
  fi

  local inserted=0

  # Process each parsed entry
  local category lesson
  while IFS=$'\x1f' read -r category lesson; do
    [ -z "$category" ] && continue
    [ -z "$lesson" ] && continue

    local entry_id
    entry_id=$(gen_id)
    local ts
    ts=$(now_ms)

    # Single SQL path using sql_escape() — handles apostrophes via sed "s/'/''/g".
    # Named-param (.param set) path removed: it wraps values in single-quotes which
    # conflicts with sql_escape's doubling (e.g. "don''t" breaks inside '.param set').
    local insert_output rc
    rc=0
    insert_output=$(sqlite3 "$db_path" \
      "INSERT OR IGNORE INTO learning_entries
         (id, pattern, approach, domain, confidence, attempts, successes, failures,
          partial_successes, created_at, first_used, last_used, source)
       VALUES (
         '$(sql_escape "$entry_id")',
         '$(sql_escape "$category")',
         '$(sql_escape "$lesson")',
         '$(sql_escape "$category")',
         1.0, 1, 1, 0, 0,
         ${ts}, ${ts}, ${ts},
         'bytedigger'
       );
       SELECT changes();" \
      2>&1) || rc=$?

    if [ "$rc" -ne 0 ]; then
      _err "sqlite3 failed: ${rc}: ${insert_output}"
      _write_error_slug "$cwd" "$SLUG_SQLITE3_ERROR"
      continue
    fi

    # Count newly-inserted rows via changes() — INSERT OR IGNORE returns 0 on duplicate.
    # Sanitize to digits only: if insert_output contains an error string, row_changes stays 0.
    local row_changes
    row_changes=$(echo "$insert_output" | tail -1 | tr -cd '0-9')
    [ -z "$row_changes" ] && row_changes=0
    if [ "$row_changes" -gt 0 ]; then
      inserted=$(( inserted + 1 ))
    fi

  done <<< "$parsed_entries"

  # Trim: DELETE rows beyond MAX_STORED per domain (scoped to source='bytedigger').
  # Runs once per unique domain after all INSERTs complete (not per-row — efficiency).
  local domains
  domains=$(echo "$parsed_entries" | cut -d$'\x1f' -f1 | sort -u)
  while IFS= read -r domain; do
    [ -z "$domain" ] && continue
    local trim_err trim_rc
    trim_rc=0
    trim_err=$(sqlite3 "$db_path" \
      "DELETE FROM learning_entries
       WHERE source = 'bytedigger'
         AND domain = '$(sql_escape "$domain")'
         AND rowid NOT IN (
           SELECT rowid FROM learning_entries
           WHERE source = 'bytedigger'
             AND domain = '$(sql_escape "$domain")'
           ORDER BY last_used DESC
           LIMIT ${MAX_STORED}
         );" \
      2>&1) || trim_rc=$?
    if [ "$trim_rc" -ne 0 ]; then
      _err "trim failed (non-fatal, MAX_STORED=${MAX_STORED} may be exceeded): ${trim_err}"
    fi
  done <<< "$domains"

  _write_state "$cwd" "learnings_extracted" "$inserted"
}

# ── Main: argument parser + subcommand dispatch ────────────────────────────────
main() {
  local subcommand="${1:-}"
  local positional="${2:-}"
  local config_file=""

  # Parse --config <path> from argv $3 $4
  if [ "${3:-}" = "--config" ]; then
    config_file="${4:-}"
  fi

  case "$subcommand" in
    inject)
      cmd_inject "$positional" "$config_file"
      ;;
    extract)
      cmd_extract "$positional" "$config_file"
      ;;
    *)
      _err "unknown subcommand: ${subcommand}"
      if [ "${LEARNING_STORE_STRICT:-0}" = "1" ]; then
        exit 1
      fi
      exit 0
      ;;
  esac
}

main "$@"
