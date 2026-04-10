#!/usr/bin/env bash
# learning-store.sh — ByteDigger pluggable learning interface
# Subcommands: inject <keywords> | extract <scratchpad_dir>
# Config: BYTEDIGGER_CONFIG env var or --config flag

set -euo pipefail

# ---------------------------------------------------------------------------
# Section 1: helpers
# ---------------------------------------------------------------------------

_script_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

# Resolve config file path (env > flag > auto-detect)
_resolve_config() {
  if [ -n "${BYTEDIGGER_CONFIG:-}" ]; then
    echo "$BYTEDIGGER_CONFIG"
    return
  fi
  # Walk args for --config flag (parsed by callers, but check here as fallback)
  local script_dir
  script_dir="$(_script_dir)"
  local candidate="$script_dir/../bytedigger.json"
  if [ -f "$candidate" ]; then
    echo "$candidate"
    return
  fi
  echo ""
}

# Derive CWD from config file location (tests simulate CWD via config path)
_cwd_from_config() {
  local config_file="$1"
  if [ -n "$config_file" ] && [ -f "$config_file" ]; then
    dirname "$config_file"
  else
    pwd
  fi
}

# Read learning config via python3
_read_config() {
  local config_file="$1"
  if [ ! -f "$config_file" ]; then
    echo "BACKEND=none"
    echo "MAX_INJECT=10"
    echo "MAX_STORED=200"
    echo "STORAGE_PATH=.bytedigger/learnings"
    return
  fi
  python3 - "$config_file" <<'PYEOF' 2>/dev/null || echo "BACKEND=none"
import json, sys
try:
    with open(sys.argv[1]) as f:
        c = json.load(f)
    learning = c.get('learning', {})
    backend      = learning.get('backend', 'none')
    max_inject   = str(learning.get('max_inject', 10))
    max_stored   = str(learning.get('max_stored', 200))
    storage_path = learning.get('storage_path', '.bytedigger/learnings')
    print(f"BACKEND={backend}")
    print(f"MAX_INJECT={max_inject}")
    print(f"MAX_STORED={max_stored}")
    print(f"STORAGE_PATH={storage_path}")
except Exception:
    print("BACKEND=none")
    print("MAX_INJECT=10")
    print("MAX_STORED=200")
    print("STORAGE_PATH=.bytedigger/learnings")
PYEOF
}

# Write a key: value line to build-state.yaml (append or replace)
_write_state() {
  local cwd="$1"
  local key="$2"
  local value="$3"
  local state_file="$cwd/build-state.yaml"

  {
    if [ -f "$state_file" ]; then
      grep -v "^${key}:" "$state_file" || true
    fi
    echo "${key}: ${value}"
  } > "${state_file}.tmp" && mv "${state_file}.tmp" "$state_file" || true
}

# NOTE: tag generation and category sanitization are handled inline
# within the python3 block in cmd_extract() for reliability.

# ---------------------------------------------------------------------------
# Section 2: inject subcommand
# ---------------------------------------------------------------------------

cmd_inject() {
  local keywords="$1"
  local config_file="$2"
  local cwd
  cwd="$(_cwd_from_config "$config_file")"

  # Read config (graceful degradation on any error)
  local config_values
  config_values=$(_read_config "$config_file") || config_values="BACKEND=none"

  local BACKEND MAX_INJECT STORAGE_PATH
  BACKEND=$(echo "$config_values" | grep "^BACKEND=" | cut -d= -f2)
  MAX_INJECT=$(echo "$config_values" | grep "^MAX_INJECT=" | cut -d= -f2)
  STORAGE_PATH=$(echo "$config_values" | grep "^STORAGE_PATH=" | cut -d= -f2)

  # Default fallbacks
  BACKEND="${BACKEND:-none}"
  MAX_INJECT="${MAX_INJECT:-10}"
  STORAGE_PATH="${STORAGE_PATH:-.bytedigger/learnings}"

  # Empty keywords: return 0 entries (not an error, per spec)
  if [ -z "$keywords" ] || [ -z "$(echo "$keywords" | tr -d '[:space:]')" ]; then
    _write_state "$cwd" "learnings_injected" "0" || true
    _write_state "$cwd" "learning_backend" "$BACKEND" || true
    exit 0
  fi

  # backend=none: record skip and exit
  if [ "$BACKEND" = "none" ]; then
    _write_state "$cwd" "learning_skip_reason" "disabled" || true
    exit 0
  fi

  # backend=sqlite: delegate to sibling script
  if [ "$BACKEND" = "sqlite" ]; then
    local sqlite_script
    sqlite_script="$(_script_dir)/learning-store-sqlite.sh"
    if [ -x "$sqlite_script" ]; then
      exec "$sqlite_script" inject "$keywords" --config "$config_file"
    else
      # sqlite script not present — fall back to none silently
      _write_state "$cwd" "learning_skip_reason" "sqlite_unavailable" || true
      exit 0
    fi
  fi

  # backend=file: scan storage_path for keyword matches
  local storage_abs
  if [[ "$STORAGE_PATH" = /* ]]; then
    storage_abs="$STORAGE_PATH"
  else
    storage_abs="$cwd/$STORAGE_PATH"
  fi

  # No learnings directory or no md files → count 0, empty stdout
  if [ ! -d "$storage_abs" ] || [ -z "$(ls "$storage_abs"/*.md 2>/dev/null)" ]; then
    _write_state "$cwd" "learnings_injected" "0" || true
    _write_state "$cwd" "learning_backend" "$BACKEND" || true
    exit 0
  fi

  # Build grep pattern from keywords (space-separated → alternation)
  local pattern
  pattern=$(echo "$keywords" | tr ' ' '|') || pattern="$keywords"

  # Collect matching entries (each entry = lesson line + optional comment line)
  # We match files that contain the keyword (case-insensitive) anywhere in the file,
  # then extract full entries (bullet + metadata comment pair)
  local matching_files=()
  while IFS= read -r -d '' f; do
    if grep -qiE "$pattern" "$f" 2>/dev/null; then
      matching_files+=("$f")
    fi
  done < <(find "$storage_abs" -name "*.md" -print0 2>/dev/null) || true

  if [ "${#matching_files[@]}" -eq 0 ]; then
    _write_state "$cwd" "learnings_injected" "0" || true
    _write_state "$cwd" "learning_backend" "$BACKEND" || true
    exit 0
  fi

  # Extract entries matching keyword from each file
  # Entries: lines starting with "- " followed optionally by "  <!-- ... -->" comment
  local all_entries=()
  for f in "${matching_files[@]}"; do
    local in_entry=0
    local entry_lines=()
    while IFS= read -r line; do
      if [[ "$line" =~ ^-[[:space:]] ]]; then
        # Save previous entry if it matched
        if [ "$in_entry" -eq 1 ] && [ "${#entry_lines[@]}" -gt 0 ]; then
          local entry_text
          entry_text=$(printf '%s\n' "${entry_lines[@]}")
          if echo "$entry_text" | grep -qiE "$pattern" 2>/dev/null; then
            all_entries+=("$entry_text")
          fi
        fi
        in_entry=1
        entry_lines=("$line")
      elif [[ "$line" =~ ^[[:space:]]+\<\!-- ]] && [ "$in_entry" -eq 1 ]; then
        entry_lines+=("$line")
      else
        # Non-entry line: flush current entry
        if [ "$in_entry" -eq 1 ] && [ "${#entry_lines[@]}" -gt 0 ]; then
          local entry_text
          entry_text=$(printf '%s\n' "${entry_lines[@]}")
          if echo "$entry_text" | grep -qiE "$pattern" 2>/dev/null; then
            all_entries+=("$entry_text")
          fi
        fi
        in_entry=0
        entry_lines=()
      fi
    done < "$f" || true
    # Flush last entry
    if [ "$in_entry" -eq 1 ] && [ "${#entry_lines[@]}" -gt 0 ]; then
      local entry_text
      entry_text=$(printf '%s\n' "${entry_lines[@]}")
      if echo "$entry_text" | grep -qiE "$pattern" 2>/dev/null; then
        all_entries+=("$entry_text")
      fi
    fi
  done || true

  local count="${#all_entries[@]}"
  if [ "$count" -eq 0 ]; then
    _write_state "$cwd" "learnings_injected" "0" || true
    _write_state "$cwd" "learning_backend" "$BACKEND" || true
    exit 0
  fi

  # Cap at max_inject
  local output_count
  if [ "$count" -gt "$MAX_INJECT" ]; then
    output_count="$MAX_INJECT"
  else
    output_count="$count"
  fi

  # Output entries to stdout
  for i in $(seq 0 $((output_count - 1))); do
    printf '%s\n' "${all_entries[$i]}"
  done

  _write_state "$cwd" "learnings_injected" "$output_count" || true
  _write_state "$cwd" "learning_backend" "$BACKEND" || true
  exit 0
}

# ---------------------------------------------------------------------------
# Section 3: extract subcommand
# ---------------------------------------------------------------------------

cmd_extract() {
  local scratchpad_dir="$1"
  local config_file="$2"
  local cwd
  cwd="$(_cwd_from_config "$config_file")"

  # Read config
  local config_values
  config_values=$(_read_config "$config_file") || config_values="BACKEND=none"

  local BACKEND MAX_STORED STORAGE_PATH
  BACKEND=$(echo "$config_values" | grep "^BACKEND=" | cut -d= -f2)
  MAX_STORED=$(echo "$config_values" | grep "^MAX_STORED=" | cut -d= -f2)
  STORAGE_PATH=$(echo "$config_values" | grep "^STORAGE_PATH=" | cut -d= -f2)

  BACKEND="${BACKEND:-none}"
  MAX_STORED="${MAX_STORED:-200}"
  STORAGE_PATH="${STORAGE_PATH:-.bytedigger/learnings}"

  # backend=none: record skip and exit
  if [ "$BACKEND" = "none" ]; then
    _write_state "$cwd" "learning_skip_reason" "disabled" || true
    exit 0
  fi

  # backend=sqlite: delegate to sibling script
  if [ "$BACKEND" = "sqlite" ]; then
    local sqlite_script
    sqlite_script="$(_script_dir)/learning-store-sqlite.sh"
    if [ -x "$sqlite_script" ]; then
      exec "$sqlite_script" extract "$scratchpad_dir" --config "$config_file"
    else
      _write_state "$cwd" "learning_skip_reason" "sqlite_unavailable" || true
      exit 0
    fi
  fi

  # backend=file
  local raw_md="$scratchpad_dir/reviews/learnings-raw.md"
  if [ ! -f "$raw_md" ]; then
    _write_state "$cwd" "learnings_extracted" "0" || true
    exit 0
  fi

  # Resolve storage path
  local storage_abs
  if [[ "$STORAGE_PATH" = /* ]]; then
    storage_abs="$STORAGE_PATH"
  else
    storage_abs="$cwd/$STORAGE_PATH"
  fi

  mkdir -p "$storage_abs" || true

  # Read forge_run_id from build-state.yaml for source metadata
  local forge_run_id="forge-unknown"
  local state_file="$cwd/build-state.yaml"
  if [ -f "$state_file" ]; then
    local fid
    fid=$(grep "^forge_run_id:" "$state_file" 2>/dev/null | sed 's/^forge_run_id:[[:space:]]*//' | tr -d '"' | tr -d "'" | tr -d ' ') || true
    [ -n "$fid" ] && forge_run_id="$fid"
  fi

  # Use python3 for robust cross-platform parsing (bash regex unreliable on macOS 3.2)
  local extracted_count
  extracted_count=$(python3 - "$raw_md" "$storage_abs" "$forge_run_id" "$MAX_STORED" <<'PYEOF' 2>/dev/null || echo "0"
import sys, re, os, datetime

raw_md      = sys.argv[1]
storage_abs = sys.argv[2]
forge_run_id = sys.argv[3]
max_stored  = int(sys.argv[4])
today       = datetime.date.today().isoformat()

def sanitize_category(raw):
    """Lowercase, replace non-alnum chars with dashes, strip leading/trailing dashes."""
    s = raw.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')

def generate_tags(lesson):
    """Extract lowercase words >3 chars from lesson, deduplicated, max 20."""
    words = re.split(r'[^a-zA-Z0-9]+', lesson)
    seen = set()
    tags = []
    for w in words:
        w = w.lower()
        if len(w) > 3 and w not in seen:
            seen.add(w)
            tags.append(w)
        if len(tags) >= 20:
            break
    return ','.join(tags)

def trim_entries(file_path, max_stored):
    """Keep only the most recent max_stored entries (each entry = bullet + optional comment)."""
    try:
        with open(file_path, 'r', errors='replace') as f:
            lines = f.readlines()
        entries = []
        current = []
        for line in lines:
            if line.startswith('- '):
                if current:
                    entries.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            entries.append(current)
        if len(entries) > max_stored:
            entries = entries[-max_stored:]
        with open(file_path, 'w') as f:
            for entry in entries:
                f.writelines(entry)
    except Exception:
        pass

# Pattern: "- [category] --- lesson" or "- [category] — lesson"
pattern = re.compile(r'^-\s+\[([^\]]+)\]\s+(?:---?|—)\s+(.+)$')

try:
    with open(raw_md, 'r', errors='replace') as f:
        lines = f.readlines()
except Exception:
    print(0)
    sys.exit(0)

count = 0
for line in lines:
    line = line.rstrip('\n')
    m = pattern.match(line)
    if not m:
        continue
    raw_category = m.group(1)
    lesson       = m.group(2).strip()
    if not lesson:
        continue
    category = sanitize_category(raw_category)
    if not category:
        continue
    tags = generate_tags(lesson)
    cat_file = os.path.join(storage_abs, f"{category}.md")
    try:
        with open(cat_file, 'a') as f:
            f.write(f"- {lesson}\n")
            f.write(f"  <!-- tags: {tags} | source: {forge_run_id} | date: {today} -->\n")
    except Exception:
        continue
    # Trim if over max_stored
    try:
        with open(cat_file, 'r', errors='replace') as f:
            bullet_count = sum(1 for l in f if l.startswith('- '))
        if bullet_count > max_stored:
            trim_entries(cat_file, max_stored)
    except Exception:
        pass
    count += 1

print(count)
PYEOF
  ) || extracted_count=0

  _write_state "$cwd" "learnings_extracted" "$extracted_count" || true
  exit 0
}

# NOTE: Trimming is handled inline within the python3 block in cmd_extract()
# via the trim_entries() function (no separate shell wrapper needed).

# ---------------------------------------------------------------------------
# Section 4: argument parsing + dispatch
# ---------------------------------------------------------------------------

_usage() {
  echo "Usage:"
  echo "  bash scripts/learning-store.sh inject <keywords> [--config <path>]"
  echo "  bash scripts/learning-store.sh extract <scratchpad_dir> [--config <path>]"
  exit 1
}

main() {
  if [ $# -lt 1 ]; then
    _usage
  fi

  local subcommand="$1"
  shift

  # Parse remaining args for --config flag
  local config_file=""
  local positional=""

  while [ $# -gt 0 ]; do
    case "$1" in
      --config)
        shift
        config_file="${1:-}"
        shift
        ;;
      --config=*)
        config_file="${1#--config=}"
        shift
        ;;
      *)
        if [ -z "$positional" ]; then
          positional="$1"
        fi
        shift
        ;;
    esac
  done

  # If no --config flag, try env or auto-detect
  if [ -z "$config_file" ]; then
    config_file="$(_resolve_config)"
  fi

  case "$subcommand" in
    inject)
      # positional = keywords string
      cmd_inject "${positional:-}" "$config_file" || true
      ;;
    extract)
      # positional = scratchpad_dir
      if [ -z "$positional" ]; then
        echo "ERROR: extract requires <scratchpad_dir>" >&2
        exit 0
      fi
      cmd_extract "$positional" "$config_file" || true
      ;;
    *)
      _usage
      ;;
  esac
}

main "$@"
